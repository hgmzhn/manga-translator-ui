"""
Real-CUGAN Upscaler Implementation using PyTorch
Based on Bilibili AI Lab's Real-CUGAN model
https://github.com/bilibili/ailab/tree/main/Real-CUGAN

Using external tile-based processing to reduce VRAM usage
"""

import os
import shutil
from typing import List

# import torch.nn.functional as F
import numpy as np
import torch
from PIL import Image

from ..utils import get_logger
from .common import OfflineUpscaler
from .tile_utils import merge_tiles_into_image, split_image_into_tiles

logger = get_logger('RealCUGANUpscaler')


# Model file hashes (SHA256)
_MODEL_HASHES = {
    'up2x-latest-conservative.pth': '6cfe3b23687915d08ba96010f25198d9cfe8a683aa4131f1acf7eaa58ee1de93',
    'up2x-latest-denoise1x.pth': '2e783c39da6a6394fbc250fdd069c55eaedc43971c4f2405322f18949ce38573',
    'up2x-latest-denoise2x.pth': '8188b3faef4258cf748c59360cbc8086ebedf4a63eb9d5d6637d45f819d32496',
    'up2x-latest-denoise3x.pth': '0a14739f3f5fcbd74ec3ce2806d13a47916c916b20afe4a39d95f6df4ca6abd8',
    'up2x-latest-no-denoise.pth': 'f491f9ecf6964ead9f3a36bf03e83527f32c6a341b683f7378ac6c1e2a5f0d16',
    'up3x-latest-conservative.pth': 'f6ea5fd20380413beb2701182483fd80c2e86f3b3f08053eb3df4975184aefe3',
    'up3x-latest-denoise3x.pth': '39f1e6e90d50e5528a63f4ba1866bad23365a737cbea22a80769b2ec4c1c3285',
    'up3x-latest-no-denoise.pth': '763f0a87e70d744673f1a41db5396d5f334d22de97fff68ffc40deb91404a584',
    'up4x-latest-conservative.pth': 'a8c8185def699b0883662a02df0ef2e6db3b0275170b6cc0d28089b64b273427',
    'up4x-latest-denoise3x.pth': '42bd8fcdae37c12c5b25ed59625266bfa65780071a8d38192d83756cb85e98dd',
    'up4x-latest-no-denoise.pth': 'aaf3ef78a488cce5d3842154925eb70ff8423b8298e2cd189ec66eb7f6f66fae',
    'pro-conservative-up2x.pth': 'b8ae5225d2d515aa3c33ef1318aadc532a42ea5ed8d564471b5a5b586783e964',
    'pro-denoise3x-up2x.pth': 'e80ca8fc7c261e3dc8f4c0ce0656ac5501d71a476543071615c43392dbeb4c0d',
    'pro-no-denoise-up2x.pth': 'ccce1f535d94c50ce38e268a53687bc7e68ef7215e3c5e6b3bfd1bfc1dacf0fa',
    'pro-conservative-up3x.pth': 'a9f3c783a04b15c793b95e332bfdac524cfa30ba186cb829c1290593e28ad9e7',
    'pro-denoise3x-up3x.pth': '4ddd14e2430db0d75d186c6dda934db34929c50da8a88a0c6f4accb871fe4b70',
    'pro-no-denoise-up3x.pth': 'c14d693a6d3316b8a3eba362e7576f178aea3407e1d89ca0bcb34e1c61269b0f',
}


class RealCUGANUpscaler(OfflineUpscaler):
    """Real-CUGAN upscaler using PyTorch with external tiling"""
    
    _MODEL_SUB_DIR = os.path.join('upscaling', 'realcugan')
    
    _VALID_UPSCALE_RATIOS = [2, 3, 4]
    
    _VALID_MODELS = {
        # SE models
        '2x-conservative': {'scale': 2, 'file': 'up2x-latest-conservative.pth'},
        '2x-denoise1x': {'scale': 2, 'file': 'up2x-latest-denoise1x.pth'},
        '2x-denoise2x': {'scale': 2, 'file': 'up2x-latest-denoise2x.pth'},
        '2x-denoise3x': {'scale': 2, 'file': 'up2x-latest-denoise3x.pth'},
        '2x-no-denoise': {'scale': 2, 'file': 'up2x-latest-no-denoise.pth'},
        
        '3x-conservative': {'scale': 3, 'file': 'up3x-latest-conservative.pth'},
        '3x-denoise3x': {'scale': 3, 'file': 'up3x-latest-denoise3x.pth'},
        '3x-no-denoise': {'scale': 3, 'file': 'up3x-latest-no-denoise.pth'},
        
        '4x-conservative': {'scale': 4, 'file': 'up4x-latest-conservative.pth'},
        '4x-denoise3x': {'scale': 4, 'file': 'up4x-latest-denoise3x.pth'},
        '4x-no-denoise': {'scale': 4, 'file': 'up4x-latest-no-denoise.pth'},
        
        # PRO models
        '2x-conservative-pro': {'scale': 2, 'file': 'pro-conservative-up2x.pth'},
        '2x-denoise3x-pro': {'scale': 2, 'file': 'pro-denoise3x-up2x.pth'},
        '2x-no-denoise-pro': {'scale': 2, 'file': 'pro-no-denoise-up2x.pth'},
        
        '3x-conservative-pro': {'scale': 3, 'file': 'pro-conservative-up3x.pth'},
        '3x-denoise3x-pro': {'scale': 3, 'file': 'pro-denoise3x-up3x.pth'},
        '3x-no-denoise-pro': {'scale': 3, 'file': 'pro-no-denoise-up3x.pth'},
    }
    
    # Model download mapping (all models hosted on GitHub Releases)
    # Note: This is populated in __init__ to only include the selected model
    _MODEL_MAPPING = {}
    
    def __init__(self, *args, model_name: str = '4x-denoise3x', tile_size: int = 400, **kwargs):
        """
        Initialize Real-CUGAN PyTorch upscaler
        
        Args:
            model_name: Model name (e.g. '4x-denoise3x', '2x-conservative-pro')
            tile_size: Tile size for splitting large images (default: 400, 0 = process full image)
        """
        if model_name not in self._VALID_MODELS:
            raise ValueError(
                f'Invalid model name: {model_name}. '
                f'Valid models: {list(self._VALID_MODELS.keys())}'
            )
        
        self.model_name = model_name
        self.tile_size = tile_size
        self.scale = self._VALID_MODELS[model_name]['scale']
        self.model_file = self._VALID_MODELS[model_name]['file']
        self.model = None
        
        self._migrate_old_models()
        
        # Only add the selected model to _MODEL_MAPPING for downloading
        self._MODEL_MAPPING = {
            model_name: {
                'url': [
                    f'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.8.0/{self.model_file}',
                    f'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/{self.model_file}',
                ],
                'hash': _MODEL_HASHES.get(self.model_file),
                'file': self.model_file
            }
        }
        
        super().__init__(*args, **kwargs)
        
        logger.info(
            f'Initialized RealCUGAN PyTorch: model={model_name}, '
            f'scale={self.scale}x, tile_size={tile_size}'
        )

    def _migrate_old_models(self):
        """Move models from old directory to new directory"""
        old_dir = os.path.join(self._MODEL_DIR, 'upscaling')
        new_dir = self.model_dir
        
        if not os.path.exists(old_dir):
            return

        os.makedirs(new_dir, exist_ok=True)
        
        # Check all known RealCUGAN models
        for model_file in _MODEL_HASHES.keys():
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
                    logger.debug(f'Model {model_file} exists in both old and new locations.')
    
    def _check_downloaded(self) -> bool:
        """Override parent's check - models are pre-downloaded"""
        model_path = os.path.join(self.model_dir, self.model_file)
        exists = os.path.exists(model_path)
        if not exists:
            logger.error(f'Model file not found: {model_path}')
            logger.error('Please ensure Real-CUGAN PyTorch models are pre-downloaded')
        return exists
    
    async def _load(self, device: str):
        """Load the Real-CUGAN PyTorch model"""
        if self.model is not None:
            return
        
        # Get model path
        model_path = os.path.join(self.model_dir, self.model_file)
        
        # Check if model exists
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f'Model file not found: {model_path}\n'
                f'Please ensure Real-CUGAN PyTorch models are downloaded to {self.model_dir}'
            )
        
        # Load model
        logger.info(f'Loading Real-CUGAN model from {model_path}')
        
        try:
            # Import Real-CUGAN model architecture
            from .realcugan_arch import upcunet_v3
            
            # Create model based on scale
            if self.scale == 2:
                self.model = upcunet_v3.UpCunet2x(in_channels=3, out_channels=3)
            elif self.scale == 3:
                self.model = upcunet_v3.UpCunet3x(in_channels=3, out_channels=3)
            elif self.scale == 4:
                self.model = upcunet_v3.UpCunet4x(in_channels=3, out_channels=3)
            else:
                raise ValueError(f'Unsupported scale: {self.scale}x')
            
            # Load weights
            state_dict = torch.load(model_path, map_location='cpu')
            
            # Handle pro models that might have wrapper structure
            if 'pro' in state_dict and isinstance(state_dict['pro'], dict):
                # Extract the actual model weights from the 'pro' wrapper
                state_dict = state_dict['pro']
            elif 'pro' in state_dict:
                # If 'pro' exists but is not a dict, remove it
                state_dict = {k: v for k, v in state_dict.items() if k != 'pro'}
            
            self.model.load_state_dict(state_dict, strict=True)

            # Move to device
            if device == 'dml':
                import torch_directml
                self.torch_device = torch_directml.device()
            else:
                self.torch_device = device
            self.model = self.model.to(self.torch_device)
            self.device = device
            self.model.eval()
            
            logger.info(f'Real-CUGAN loaded: {self.model_name}, scale={self.scale}x')
            
        except Exception as e:
            logger.error(f'Failed to load Real-CUGAN model: {e}')
            raise
    
    async def _unload(self):
        """Unload the Real-CUGAN model"""
        if self.model is not None:
            del self.model
            self.model = None
            if torch.cuda.is_available():
                pass
        logger.info('Real-CUGAN model unloaded')
    
    async def _infer(self, image_batch: List[Image.Image], upscale_ratio: float) -> List[Image.Image]:
        """
        Perform inference using PyTorch
        
        Args:
            image_batch: List of PIL images to upscale
            upscale_ratio: Target upscale ratio
        
        Returns:
            List of upscaled PIL images
        """
        if self.model is None:
            raise RuntimeError('Model not loaded')
        
        target_scale = int(upscale_ratio)
        if target_scale != self.scale:
            logger.warning(
                f'Requested scale {target_scale}x does not match model scale {self.scale}x. '
                f'Using model scale {self.scale}x'
            )
        
        results = []
        device = next(self.model.parameters()).device
        
        for img in image_batch:
            # 确保输入是 PIL Image
            if isinstance(img, np.ndarray):
                img = Image.fromarray(img)
            
            # Use tiling only if tile_size > 0
            if self.tile_size > 0:
                output_img = self._process_with_tiles(img, device, self.tile_size)
            else:
                logger.info('Processing full image without tiling')
                output_img = self._process_single(img, device)
            
            results.append(output_img)
        
        return results
    
    def _process_single(self, img: Image.Image, device: torch.device) -> Image.Image:
        """Process a single image without tiling"""
        # Check minimum size requirement for RealCUGAN
        # RealCUGAN uses padding=18/14/19 and then crops -20, requires minimum dimensions
        min_size = 40  # Minimum size to allow padding and cropping
        original_size = img.size
        padded = False
        
        if img.size[0] < min_size or img.size[1] < min_size:
            logger.warning(
                f'Image size ({img.size[0]}x{img.size[1]}) is too small for RealCUGAN. '
                f'Minimum size is {min_size}x{min_size}. '
                f'Adding black padding to reach minimum size.'
            )
            # Calculate padding needed
            pad_w = max(0, min_size - img.size[0])
            pad_h = max(0, min_size - img.size[1])
            
            # Create new image with black background
            new_size = (img.size[0] + pad_w, img.size[1] + pad_h)
            padded_img = Image.new('RGB', new_size, (0, 0, 0))
            
            # Paste original image in top-left corner
            padded_img.paste(img, (0, 0))
            img = padded_img
            padded = True
        
        # Convert grayscale to RGB if necessary
        if img.mode in ('L', 'LA'):
            img = img.convert('RGB')
        elif img.mode == 'RGBA':
            # Convert RGBA to RGB by compositing over white background
            rgb = Image.new('RGB', img.size, (255, 255, 255))
            rgb.paste(img, mask=img.split()[3])  # Use alpha channel as mask
            img = rgb
        
        # Convert to tensor
        np_img = np.array(img).astype(np.float32) / 255.0
        
        # Ensure 3D tensor (H, W, C)
        if len(np_img.shape) == 2:
            # Grayscale: expand to 3 channels
            np_img = np.stack([np_img] * 3, axis=2)
        
        tensor = torch.from_numpy(np_img).permute(2, 0, 1).unsqueeze(0).to(device)
        
        # Determine model parameters
        is_pro = '-pro' in self.model_name
        
        # Determine denoise level (alpha)
        if 'denoise3x' in self.model_name or 'denoise_3' in self.model_name:
            alpha = 1.0  # Strong denoise
        elif 'denoise2x' in self.model_name or 'denoise_2' in self.model_name:
            alpha = 0.66  # Medium denoise
        elif 'denoise1x' in self.model_name or 'denoise_1' in self.model_name:
            alpha = 0.33  # Light denoise
        else:
            alpha = 1.0  # Default/conservative/no-denoise
        
        # Inference (tile_mode=0 means no internal tiling)
        with torch.no_grad():
            output = self.model(
                tensor,
                tile_mode=0,      # No internal tiling (we handle it externally)
                cache_mode=0,     # No caching
                alpha=alpha,      # Denoise strength
                pro=is_pro        # PRO model flag
            )
        
        # Convert back to PIL (output is already uint8 from model)
        if output.dtype == torch.uint8:
            output_np = output.squeeze(0).permute(1, 2, 0).cpu().numpy()
        else:
            output_np = output.squeeze(0).permute(1, 2, 0).cpu().numpy()
            output_np = np.clip(output_np, 0, 255).astype(np.uint8)
        
        # ✅ 清理中间张量以释放显存
        del tensor
        del output
        if torch.cuda.is_available():
            pass
        
        result_img = Image.fromarray(output_np, mode='RGB')
        
        # If image was padded, crop back to original scaled size
        if padded:
            # Calculate the original region after upscaling
            scaled_width = original_size[0] * self.scale
            scaled_height = original_size[1] * self.scale
            result_img = result_img.crop((0, 0, scaled_width, scaled_height))
            logger.info(f'Cropped upscaled image from {output_np.shape[1]}x{output_np.shape[0]} to {scaled_width}x{scaled_height}')
        
        return result_img
    
    def _process_with_tiles(self, img: Image.Image, device: torch.device, tile_size: int) -> Image.Image:
        """Process image with external tiling"""
        # Split into tiles
        tiles_with_pos = split_image_into_tiles(img, tile_size, overlap=16)
        
        logger.info(f'Split image ({img.size[0]}x{img.size[1]}) into {len(tiles_with_pos)} tiles (tile_size={tile_size})')
        
        # Process each tile
        processed_tiles = []
        for i, (tile, pos) in enumerate(tiles_with_pos):
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
        
        # Merge tiles
        output_img = merge_tiles_into_image(
            processed_tiles,
            img.size,
            self.scale,
            overlap=16
        )
        
        logger.info(f'Merged tiles into final image: {output_img.size[0]}x{output_img.size[1]} (scale={self.scale}x)')
        
        return output_img

