import os
import shutil
from typing import List

import einops
import numpy as np
import torch
from PIL import Image

from ..utils import get_logger
from .common import OfflineUpscaler
from .esrgan_pytorch import RRDBNet, infer_params
from .tile_utils import merge_tiles_into_image, split_image_into_tiles

logger = get_logger('MangaJaNaiUpscaler')

# List of known MangaJaNai models with SHA256 hashes
_KNOWN_MODELS = {
    "2x_IllustrationJaNai_V1_ESRGAN_120k.pth": "5f49a71d3cd0000a51ed0e3adfe5c11824740f1c58f7cb520d8d2d1e924c2b88",
    "2x_MangaJaNai_1200p_V1_ESRGAN_70k.pth": "43b784f674bdbf89886a62a64cd5f8d8df92caf4d861bdf4d47dad249ede0267",
    "2x_MangaJaNai_1300p_V1_ESRGAN_75k.pth": "15ca3c0f75f97f7bf52065bf7c9b8d602de94ce9e3b078ac58793855eed18589",
    "2x_MangaJaNai_1400p_V1_ESRGAN_70k.pth": "a940ad8ebcf6bea5580f2f59df67deb009f054c9b87dbbc58c2e452722f34858",
    "2x_MangaJaNai_1500p_V1_ESRGAN_90k.pth": "d91f2d247fa61144c1634a2ba46926acd3956ae90d281a5bed6655f8364a5b2c",
    "2x_MangaJaNai_1600p_V1_ESRGAN_90k.pth": "6f5923f812dbc5d6aeed727635a21e74cacddce595afe6135cbd95078f6eee44",
    "2x_MangaJaNai_1920p_V1_ESRGAN_70k.pth": "1ad4aa6f64684baa430da1bb472489bff2a02473b14859015884a3852339c005",
    "2x_MangaJaNai_2048p_V1_ESRGAN_95k.pth": "146cd009b9589203a8444fe0aa7195709bb5b9fdeaca3808b7fbbd5538f94c41",
    "4x_IllustrationJaNai_V1_DAT2_190k.pth": "a82f3a2d8d1c676171b86a00048b7a624e3c62c87ec701012f106a171c309fbe",
    "4x_IllustrationJaNai_V1_ESRGAN_135k.pth": "c67e76c4b5f0474d5116e5f3885202d1bee68187e1389f82bb90baace24152f8",
    "4x_MangaJaNai_1200p_V1_ESRGAN_70k.pth": "6e3a8d21533b731eb3d8eaac1a09cf56290fa08faf8473cbe3debded9ab1ebe1",
    "4x_MangaJaNai_1300p_V1_ESRGAN_75k.pth": "eacf8210543446f3573d4ea1625f6fc11a3b2a5e18b38978873944be146417a8",
    "4x_MangaJaNai_1400p_V1_ESRGAN_105k.pth": "d77f977a6c6c4bf855dae55f0e9fad6ac2823fa8b2ef883b50e525369fde6a74",
    "4x_MangaJaNai_1500p_V1_ESRGAN_105k.pth": "5e5174b60316e9abb7875e6d2db208fec4ffc34f3d09fa7f0e0f6476f9d31687",
    "4x_MangaJaNai_1600p_V1_ESRGAN_70k.pth": "c126ec8d4b7434d8f6a43d24bec1f56d343104ab8a86b5e01d5d25be6b5244c0",
    "4x_MangaJaNai_1920p_V1_ESRGAN_105k.pth": "d469e96e590a25a86037760b26d51405c77759a55b0966b15dc76b609f72f20b",
    "4x_MangaJaNai_2048p_V1_ESRGAN_70k.pth": "f70e08c60da372b7207e7348486ea6b498ea8dea6246bb717530a4d45c955b9b",
}

def is_color_image(image, threshold: float = 0.05) -> bool:
    """
    检测图片是否为彩色图片
    使用多个指标综合判断，提高准确性
    
    Args:
        image: PIL Image 或 numpy array
        threshold: 饱和度阈值（默认0.05）
    
    Returns:
        True 如果是彩色图片，False 如果是黑白/灰度图片
    """
    # 统一转换为 PIL Image
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    
    if image.mode == 'L':
        return False
    
    # 转换为 RGB
    img_rgb = image.convert('RGB')
    img_np = np.array(img_rgb, dtype=np.float32)
    
    # 方法1: 检查RGB通道的相关性（最可靠的方法）
    # 灰度图的RGB通道高度相关（相关系数接近1）
    r_flat = img_np[:, :, 0].flatten()
    g_flat = img_np[:, :, 1].flatten()
    b_flat = img_np[:, :, 2].flatten()
    
    # 计算通道间的相关系数
    corr_rg = np.corrcoef(r_flat, g_flat)[0, 1]
    corr_gb = np.corrcoef(g_flat, b_flat)[0, 1]
    corr_rb = np.corrcoef(r_flat, b_flat)[0, 1]
    min_corr = min(corr_rg, corr_gb, corr_rb)
    
    # 如果通道高度相关（>0.995），肯定是黑白图
    if min_corr > 0.995:
        logger.debug(f"彩图检测 - 通道高度相关({min_corr:.4f})，判断: 黑白")
        return False
    
    # 方法2: 计算平均饱和度
    max_val = img_np.max(axis=2)
    min_val = img_np.min(axis=2)
    max_val = np.maximum(max_val, 1)
    saturation = (max_val - min_val) / max_val
    mean_saturation = np.mean(saturation)
    
    # 方法3: 检查RGB通道的标准差差异
    # 如果是灰度图，三个通道的标准差应该非常接近
    r_std = np.std(img_np[:, :, 0])
    g_std = np.std(img_np[:, :, 1])
    b_std = np.std(img_np[:, :, 2])
    std_diff = max(abs(r_std - g_std), abs(g_std - b_std), abs(r_std - b_std))
    
    # 综合判断（通道相关性已经排除了黑白图）
    # 1. 饱和度超过阈值
    # 2. 通道标准差差异明显（>1.0）
    # 3. 通道相关性较低（<0.99）
    is_color = (mean_saturation > threshold) or (std_diff > 1.0) or (min_corr < 0.99)
    
    logger.debug(f"彩图检测 - 饱和度: {mean_saturation:.4f}, 通道标准差差异: {std_diff:.2f}, 最小相关性: {min_corr:.4f}, 判断: {'彩色' if is_color else '黑白'}")
    
    return is_color


def enhance_contrast(image) -> Image.Image:
    """
    Auto-adjust levels to enhance contrast, similar to MangaJaNaiConverterGui.
    Finds black/white points from histogram and stretches the range.
    
    Args:
        image: PIL Image 或 numpy array
        
    Returns:
        PIL Image 对象
    """
    # 统一转换为 PIL Image
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    
    if image.mode != 'L':
        image_p = image.convert("L")
    else:
        image_p = image

    # Calculate the histogram
    hist = image_p.histogram()

    # Find the global maximum peak in the range 0-30 for the black level
    new_black_level = 0
    global_max_black = hist[0]

    for i in range(1, 31):
        if hist[i] > global_max_black:
            global_max_black = hist[i]
            new_black_level = i

    # Continue searching at 31 and later for the black level
    continuous_count = 0
    for i in range(31, 256):
        if hist[i] > global_max_black:
            continuous_count = 0
            global_max_black = hist[i]
            new_black_level = i
        elif hist[i] < global_max_black:
            continuous_count += 1
            if continuous_count > 1:
                break

    # Find the global maximum peak in the range 255-225 for the white level
    new_white_level = 255
    global_max_white = hist[255]

    for i in range(254, 224, -1):
        if hist[i] > global_max_white:
            global_max_white = hist[i]
            new_white_level = i

    # Continue searching at 224 and below for the white level
    continuous_count = 0
    for i in range(223, -1, -1):
        if hist[i] > global_max_white:
            continuous_count = 0
            global_max_white = hist[i]
            new_white_level = i
        elif hist[i] < global_max_white:
            continuous_count += 1
            if continuous_count > 1:
                break

    # If levels are trivial, return original
    if new_black_level == 0 and new_white_level == 255:
        return image

    logger.debug(f"Auto adjusted levels: black={new_black_level}, white={new_white_level}")

    # Apply levels adjustment
    # Formula: (x - black) / (white - black) * 255
    # We use a LUT for speed
    lut = []
    div = new_white_level - new_black_level
    if div <= 0: div = 1 # avoid division by zero
    
    for i in range(256):
        val = (i - new_black_level) * 255.0 / div
        lut.append(max(0, min(255, int(val + 0.5))))
    
    if image.mode == 'RGB':
        # Apply to each channel
        return image.point(lut * 3)
    elif image.mode == 'L':
        return image.point(lut)
    else:
        # Convert to RGB, apply, (optional: convert back)
        return image.convert('RGB').point(lut * 3)


class MangaJaNaiUpscaler(OfflineUpscaler):
    """Upscaler for MangaJaNai/IllustrationJaNai models"""
    
    _MODEL_SUB_DIR = os.path.join('upscaling', 'mangajanai')
    
    # We rely on local files mostly
    _MODEL_MAPPING = {} 
    
    # Allow all ratios, we will scale accordingly
    _VALID_UPSCALE_RATIOS = [2, 3, 4]
    
    # Preset modes
    MODE_X2 = 'x2'
    MODE_X4 = 'x4'
    MODE_DAT2 = 'DAT2 x4'

    def __init__(self, *args, model_name: str = '', tile_size: int = 400, **kwargs):
        # Default fallback if model_name is empty
        if not model_name:
            model_name = self.MODE_X4
            
        self.user_model_name = model_name  # The requested mode/model
        self.current_loaded_model_file = None
        self.model = None
        self.device = None
        self.scale = 4 # default assumption
        self.tile_size = tile_size
        
        # Configure modes
        self.is_auto_mode = False
        self.target_auto_scale = 0
        self.model_candidates = []
        
        # Determine if we are in a preset mode or using a specific file
        if model_name == self.MODE_X2:
            self.is_auto_mode = True
            self.target_auto_scale = 2
            self.model_candidates = [m for m in _KNOWN_MODELS if m.startswith('2x_MangaJaNai_')]
            # Default fallback for init
            self.model_file = "2x_MangaJaNai_1600p_V1_ESRGAN_90k.pth" 
        elif model_name == self.MODE_X4:
            self.is_auto_mode = True
            self.target_auto_scale = 4
            self.model_candidates = [m for m in _KNOWN_MODELS if m.startswith('4x_MangaJaNai_')]
            # Default fallback for init
            self.model_file = "4x_MangaJaNai_1600p_V1_ESRGAN_70k.pth"
        elif model_name == self.MODE_DAT2:
            self.is_auto_mode = False # DAT2 is specific
            self.model_file = "4x_IllustrationJaNai_V1_DAT2_190k.pth"
        else:
            # Specific file or unknown mode
            self.is_auto_mode = False
            self.model_file = model_name
            # Fuzzy match for filename if needed
            if not self.model_file.endswith('.pth'):
                 for f in _KNOWN_MODELS:
                     if f.startswith(model_name) and (f == model_name or f == model_name + '.pth'):
                         self.model_file = f
                         break
                 if not self.model_file.endswith('.pth'):
                      self.model_file += '.pth'

        # Populate MODEL_MAPPING for download checks
        # We use the v1.9.5 release from hgmzhn/manga-translator-ui
        base_url_github = 'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.9.5/'
        base_url_modelscope = 'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/'
        
        self._MODEL_MAPPING = {}
        
        # If auto mode, we might need any of the candidates, so we map them all
        if self.is_auto_mode:
            for m in self.model_candidates:
                mapping = {
                    'file': m,
                    'url': [
                        f'{base_url_github}{m}',
                        f'{base_url_modelscope}{m}',
                    ],
                }
                if _KNOWN_MODELS.get(m):
                    mapping['hash'] = _KNOWN_MODELS[m]
                self._MODEL_MAPPING[m] = mapping
            
            # 同时添加 IllustrationJaNai 模型（用于彩色图片）
            illust_models = [
                "2x_IllustrationJaNai_V1_ESRGAN_120k.pth",
                "4x_IllustrationJaNai_V1_ESRGAN_135k.pth",
            ]
            for m in illust_models:
                if m not in self._MODEL_MAPPING:
                    mapping = {
                        'file': m,
                        'url': [
                            f'{base_url_github}{m}',
                            f'{base_url_modelscope}{m}',
                        ],
                    }
                    if _KNOWN_MODELS.get(m):
                        mapping['hash'] = _KNOWN_MODELS[m]
                    self._MODEL_MAPPING[m] = mapping
        else:
            mapping = {
                'file': self.model_file,
                'url': [
                    f'{base_url_github}{self.model_file}',
                    f'{base_url_modelscope}{self.model_file}',
                ],
            }
            if _KNOWN_MODELS.get(self.model_file):
                mapping['hash'] = _KNOWN_MODELS[self.model_file]
            self._MODEL_MAPPING[self.model_file] = mapping

        self._migrate_old_models()
        super().__init__(*args, **kwargs)

    def _migrate_old_models(self):
        """Move models from old directory to new directory"""
        old_dir = os.path.join(self._MODEL_DIR, 'upscaling')
        new_dir = self.model_dir
        
        if not os.path.exists(old_dir):
            return

        os.makedirs(new_dir, exist_ok=True)
        
        for model_file in _KNOWN_MODELS:
            old_path = os.path.join(old_dir, model_file)
            new_path = os.path.join(new_dir, model_file)
            
            if os.path.exists(old_path):
                if not os.path.exists(new_path):
                    try:
                        logger.info(f'Migrating model {model_file} to {new_dir}')
                        shutil.move(old_path, new_path)
                    except Exception as e:
                        logger.warning(f'Failed to move {model_file}: {e}')
                else:
                    # If file exists in both places, maybe clean up the old one?
                    logger.debug(f'Model {model_file} exists in both old and new locations.')

    def _check_downloaded(self) -> bool:
        """Override check to just check file existence"""
        # For auto mode, we ideally want all candidates, but let's at least check the default one
        # or we could be lazy and only check what we need at runtime.
        # But OfflineUpscaler expects this to return True/False for the 'primary' model.
        # We will check if the *current* model_file exists.
        model_path = os.path.join(self.model_dir, self.model_file)
        return os.path.exists(model_path)
    
    def _select_best_model(self, img) -> str:
        """
        Select best model from candidates based on image resolution and color
        
        Args:
            img: PIL Image 或 numpy array
            
        Returns:
            模型文件名
        """
        if not self.is_auto_mode:
            return self.model_file
        
        # 统一确保输入是 PIL Image
        if isinstance(img, np.ndarray):
            img = Image.fromarray(img)
        
        # 检测是否为彩色图片
        is_color = is_color_image(img)
        
        # 根据彩色/黑白和目标倍率选择模型
        if is_color:
            # 彩色图片使用 IllustrationJaNai 模型
            if self.target_auto_scale == 2:
                model_file = "2x_IllustrationJaNai_V1_ESRGAN_120k.pth"
            else:
                model_file = "4x_IllustrationJaNai_V1_ESRGAN_135k.pth"
            logger.info(f"检测到彩色图片，使用 IllustrationJaNai 模型: {model_file}")
            return model_file
        
        # 黑白图片使用 MangaJaNai 模型，根据分辨率选择
        if not self.model_candidates:
            return self.model_file
            
        # Get image short side (usually height for manga pages)
        res = min(img.width, img.height)
        
        best_model = self.model_candidates[0]
        min_diff = float('inf')
        
        for m in self.model_candidates:
            # Parse resolution from filename
            # Format: 2x_MangaJaNai_{RES}p_...
            try:
                parts = m.split('_')
                for p in parts:
                    if p.endswith('p') and p[:-1].isdigit():
                        model_res = int(p[:-1])
                        diff = abs(model_res - res)
                        if diff < min_diff:
                            min_diff = diff
                            best_model = m
                        break
            except Exception:
                continue
        
        logger.info(f"检测到黑白图片 (分辨率 {res}p)，使用 MangaJaNai 模型: {best_model}")
        return best_model

    async def _load(self, device: str):
        # 延迟加载：不在这里加载模型，等到 _infer 时根据图片类型再加载
        # 只保存 device 信息
        self.device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        else:
            self.torch_device = device
        logger.info("MangaJaNai upscaler initialized, will load model based on image type")

    async def _load_specific_model(self, filename: str, device: str):
        if self.current_loaded_model_file == filename and self.model is not None:
            return

        model_path = os.path.join(self.model_dir, filename)
        if not os.path.exists(model_path):
            # Try to see if we can download it? 
            # But _load is usually called after download. 
            # For now, raise error.
            raise FileNotFoundError(f"Model file not found: {model_path}")

        logger.info(f"Loading MangaJaNai model: {filename}")
        
        sd = torch.load(model_path, map_location='cpu', weights_only=False)
        
        # Handle cases where state_dict is inside a key like 'params' or 'params_ema'
        if 'params_ema' in sd:
            sd = sd['params_ema']
        elif 'params' in sd:
            sd = sd['params']

        # 尝试使用 spandrel 库加载（支持多种模型架构，自动识别）
        try:
            from spandrel import ModelLoader
            loader = ModelLoader()
            model_desc = loader.load_from_file(model_path)
            self.model = model_desc.model
            self.scale = model_desc.scale
            logger.info(f"Loaded model via spandrel: {filename}, scale={self.scale}x")
        except ImportError:
            # spandrel 未安装，回退到手动加载
            logger.warning("spandrel 库未安装，尝试手动加载模型")
            try:
                in_nc, out_nc, nf, nb, plus, mscale = infer_params(sd)
                self.model = RRDBNet(in_nc=in_nc, out_nc=out_nc, nf=nf, nb=nb, upscale=mscale, plus=plus)
                self.model.load_state_dict(sd)
                self.scale = mscale
            except Exception as e:
                logger.error(f"Failed to load model {filename}: {e}")
                raise
        except Exception as e:
            logger.error(f"Failed to load model via spandrel: {e}")
            raise

        self.model.eval()
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        else:
            self.torch_device = device
        self.model = self.model.to(self.torch_device)
        self.device = device
        self.current_loaded_model_file = filename

    async def _unload(self):
        """卸载模型"""
        if self.model:
            del self.model
            self.model = None
        self.current_loaded_model_file = None

    async def _infer(self, image_batch: List[Image.Image], upscale_ratio: float) -> List[Image.Image]:
        # Note: We don't check `if not self.model` here because we might load it dynamically
        
        results = []
        for img in image_batch:
            # 统一确保输入是 PIL Image
            if isinstance(img, np.ndarray):
                img = Image.fromarray(img)
            
            # 1. Determine best model for this image
            target_model_file = self.model_file
            if self.is_auto_mode:
                target_model_file = self._select_best_model(img)
            
            # 2. Switch model if needed
            if target_model_file != self.current_loaded_model_file:
                await self._load_specific_model(target_model_file, self.device)
            
            if not self.model:
                 raise RuntimeError("Model not loaded")

            # 3. Preprocessing: Enhance Contrast (Auto Levels)
            # This is specific to MangaJaNai models to remove grey background
            img_processed = enhance_contrast(img)

            # 4. Process (with tiling if needed)
            if self.tile_size > 0:
                output_img = self._process_with_tiles(img_processed, self.torch_device, self.tile_size)
            else:
                output_img = self._process_single(img_processed, self.torch_device)
                
            # 5. Post-resize if needed
            # Use current model scale, which might differ if we switched models
            ratio = upscale_ratio / self.scale
            if ratio != 1.0:
                new_size = (int(round(output_img.size[0] * ratio)), int(round(output_img.size[1] * ratio)))
                output_img = output_img.resize(size=new_size, resample=Image.Resampling.BILINEAR)
            
            results.append(output_img)
            
        return results

    def _process_single(self, img, device: torch.device) -> Image.Image:
        """
        处理单张图片
        
        Args:
            img: PIL Image 或 numpy array
            device: torch 设备
            
        Returns:
            处理后的 PIL Image
        """
        # 统一确保输入是 PIL Image
        if isinstance(img, np.ndarray):
            img = Image.fromarray(img)
        # Check minimum size requirement
        min_size = 40
        original_size = img.size
        padded = False
        
        # 计算需要的 padding：尺寸必须是 2 的倍数（pixel_unshuffle 要求）
        pad_w = (2 - img.size[0] % 2) % 2
        pad_h = (2 - img.size[1] % 2) % 2
        
        # 同时检查最小尺寸要求
        if img.size[0] + pad_w < min_size:
            pad_w = min_size - img.size[0]
        if img.size[1] + pad_h < min_size:
            pad_h = min_size - img.size[1]
        
        # 确保 padding 后仍是 2 的倍数
        if (img.size[0] + pad_w) % 2 != 0:
            pad_w += 1
        if (img.size[1] + pad_h) % 2 != 0:
            pad_h += 1
        
        if pad_w > 0 or pad_h > 0:
            new_size = (img.size[0] + pad_w, img.size[1] + pad_h)
            padded_img = Image.new('RGB', new_size, (0, 0, 0))
            padded_img.paste(img, (0, 0))
            img = padded_img
            padded = True
            logger.debug(f'Padded image from {original_size} to {new_size} for pixel_unshuffle compatibility')
        
        # Convert to tensor: B C H W
        img_np = np.array(img.convert('RGB'))
        tensor = einops.rearrange(torch.from_numpy(img_np).float() / 255.0, 'h w c -> 1 c h w').to(device)
        
        with torch.no_grad():
            output = self.model(tensor)
        
        # Convert back to PIL
        output_np = (einops.rearrange(output.squeeze(0).clamp(0, 1), 'c h w -> h w c').cpu().numpy() * 255.0).astype(np.uint8)
        result_img = Image.fromarray(output_np)
        
        # If image was padded, crop back to original scaled size
        if padded:
            scaled_width = original_size[0] * self.scale
            scaled_height = original_size[1] * self.scale
            result_img = result_img.crop((0, 0, scaled_width, scaled_height))
        
        return result_img

    def _process_with_tiles(self, img, device: torch.device, tile_size: int) -> Image.Image:
        """
        使用分块处理图片
        
        Args:
            img: PIL Image 或 numpy array
            device: torch 设备
            tile_size: 分块大小
            
        Returns:
            处理后的 PIL Image
        """
        # 统一确保输入是 PIL Image
        if isinstance(img, np.ndarray):
            img = Image.fromarray(img)
        tiles_with_pos = split_image_into_tiles(img, tile_size, overlap=16)
        
        processed_tiles = []
        for tile, pos in tiles_with_pos:
            orig_w, orig_h = tile.size
            # Pad edge tiles to canonical size so model input shapes stay stable.
            if orig_w != tile_size or orig_h != tile_size:
                padded_tile = Image.new('RGB', (tile_size, tile_size), (0, 0, 0))
                padded_tile.paste(tile, (0, 0))
                tile_for_infer = padded_tile
            else:
                tile_for_infer = tile
            processed_tile = self._process_single(tile_for_infer, device)
            expected_w = orig_w * self.scale
            expected_h = orig_h * self.scale
            if processed_tile.size != (expected_w, expected_h):
                processed_tile = processed_tile.crop((0, 0, expected_w, expected_h))
            processed_tiles.append((processed_tile, pos))
        
        return merge_tiles_into_image(processed_tiles, img.size, self.scale, overlap=16)
