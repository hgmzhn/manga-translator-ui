# Lama with Masking Positional Encoding
# original implementation https://github.com/DQiaole/ZITS_inpainting.git
# paper https://arxiv.org/pdf/2203.00867.pdf

import os
import shutil
from typing import Tuple

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from ..config import InpainterConfig
from ..utils import resize_keep_aspect
from ..utils.onnx_runtime import (
    create_inference_session,
    create_session_options,
    import_onnxruntime,
)
from .common import OfflineInpainter

TORCH_DTYPE_MAP = {
    'fp32': torch.float32,
    'fp16': torch.float16,
    'bf16': torch.bfloat16,
}


def load_masked_position_encoding(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute masked position encoding (MPE) for Lama inpainting.
    Ported from rust implementation: manga-image-translator-rust/crates/modules/inpainter/lama_mpe/src/mpe.rs
    
    Args:
        mask: Binary mask (H, W), 255 for masked area, 0 for known area
        
    Returns:
        rel_pos: Relative position encoding (H, W) with dtype int64
        direct: Directional encoding (H, W, 4) with dtype int64
    """
    ori_h, ori_w = mask.shape[:2]
    
    # Define directional filters (3x3 kernels)
    d_filter1 = np.array([[1., 1., 0.],
                          [1., 1., 0.],
                          [0., 0., 0.]], dtype=np.float32)
    
    d2_filter = np.array([[0., 0., 0.],
                          [1., 1., 0.],
                          [1., 1., 0.]], dtype=np.float32)
    
    d3_filter = np.array([[0., 1., 1.],
                          [0., 1., 1.],
                          [0., 0., 0.]], dtype=np.float32)
    
    d4_filter = np.array([[0., 0., 0.],
                          [0., 1., 1.],
                          [0., 1., 1.]], dtype=np.float32)
    
    ones_filter = np.ones((3, 3), dtype=np.float32)
    
    str_size = 256
    pos_num = 128
    
    # Normalize original mask
    ori_mask = (mask > 0).astype(np.float32)
    
    # Resize mask to fixed size for computation
    mask_resized = cv2.resize(mask, (str_size, str_size), interpolation=cv2.INTER_AREA)
    mask3 = (mask_resized == 0).astype(np.float32)  # Invert: 1 for known, 0 for masked
    
    h, w = str_size, str_size
    pos = np.zeros((h, w), dtype=np.int32)
    direct = [np.zeros((h, w), dtype=np.uint8) for _ in range(4)]
    
    i = 0
    if np.any(mask3 > 0.0):
        while np.sum(1.0 - mask3) > 0.0:
            i += 1
            
            # Dilate mask
            mask3_ = cv2.filter2D(mask3, -1, ones_filter, borderType=cv2.BORDER_DEFAULT)
            mask3_c = (mask3_ > 0.0).astype(np.float32)
            
            # Compute boundary
            mask3_[mask3_ > 0.0] = 1.0 - mask3[mask3_ > 0.0]
            mask3_[mask3_ <= 0.0] -= mask3[mask3_ <= 0.0]
            
            # Update position
            pos[mask3_ == 1.0] = i
            
            # Compute directional encoding
            for idx, d_filter in enumerate([d_filter1, d2_filter, d3_filter, d4_filter]):
                m = cv2.filter2D(mask3, -1, d_filter, borderType=cv2.BORDER_DEFAULT)
                m[m > 0.0] = 1.0 - mask3[m > 0.0]
                m[m <= 0.0] -= mask3[m <= 0.0]
                direct[idx][m == 1.0] = 1
            
            mask3 = mask3_c
    
    # Normalize position to [0, pos_num-1]
    rel_pos = np.clip((pos.astype(np.float32) / (str_size / 2.0) * pos_num).astype(np.int32), 0, pos_num - 1).astype(np.uint8)
    
    # Resize back to original size if needed
    if ori_w != w or ori_h != h:
        rel_pos = cv2.resize(rel_pos, (ori_w, ori_h), interpolation=cv2.INTER_NEAREST)
        rel_pos[ori_mask == 0] = 0
        
        for idx in range(4):
            direct[idx] = cv2.resize(direct[idx], (ori_w, ori_h), interpolation=cv2.INTER_NEAREST)
            direct[idx][ori_mask == 0] = 0
    
    # Stack directional encodings (H, W, 4)
    direct_stack = np.stack(direct, axis=2).astype(np.int64)
    
    return rel_pos.astype(np.int64), direct_stack


class LamaMPEInpainter(OfflineInpainter):

    '''
    Better mark as deprecated and replace with lama large
    '''

    _MODEL_MAPPING = {
        'model': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/inpainting_lama_mpe.ckpt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/inpainting_lama_mpe.ckpt',
            ],
            'hash': 'd625aa1b3e0d0408acfd6928aa84f005867aa8dbb9162480346a4e20660786cc',
            'file': '.',
        },
        'onnx': {
            'url': [
                'https://github.com/frederik-uni/manga-image-translator-rust/releases/download/lama_mpe/model.onnx',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/lama_mpe_inpainting.onnx',
            ],
            'hash': '4c372fdbb974d9b6ccce7a91eaa3aef65c68bf2178e9671a50f65b6eae590a66',
            'file': 'lamampe.onnx',
        },
    }

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        if os.path.exists('inpainting_lama_mpe.ckpt'):
            shutil.move('inpainting_lama_mpe.ckpt', self._get_file_path('inpainting_lama_mpe.ckpt'))
        super().__init__(*args, **kwargs)
    
    def _check_downloaded_map(self, map_key: str) -> bool:
        """检查模型文件是否存在
        
        lama_mpe 的 ONNX 模型有设计缺陷会导致降级到 PyTorch，
        因此需要确保两个模型文件都下载，不跳过任何检查。
        """
        return super()._check_downloaded_map(map_key)

    async def _load(self, device: str, **kwargs):
        self.device = device
        self.torch_device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        
        # ✅ CPU或DirectML模式使用ONNX（DirectML不支持PyTorch的ComplexFloat复数算子，ONNX更稳定且支持DirectML GPU加速）
        if not device.startswith('cuda') and device != 'mps':
            try:
                ort = import_onnxruntime(
                    "onnxruntime is required for Lama MPE ONNX inference. "
                    "Install with: pip install onnxruntime-gpu (or onnxruntime)"
                )
                onnx_path = self._get_file_path('lamampe.onnx')
                self.logger.info(f'使用ONNX模型（DirectML/CPU优化）: {onnx_path}')
                
                # 🔧 内存优化配置
                sess_options = create_session_options(
                    ort,
                    log_severity_level=3,
                    enable_mem_pattern=False,
                    enable_cpu_mem_arena=False,
                )
                self.session, ort_device = create_inference_session(
                    ort,
                    onnx_path,
                    sess_options=sess_options,
                    device='cpu' if device == 'dml' else device,
                    logger=self.logger,
                )
                self.backend = 'onnx'
                self.logger.info(f'ONNX Runtime版本: {ort.__version__}，活动设备: {ort_device}')
                return
            except Exception as e:
                self.logger.warning(f'ONNX加载失败，回退到PyTorch: {e}')
        
        # ✅ GPU模式或ONNX失败时使用PyTorch
        self.model = load_lama_mpe(self._get_file_path('inpainting_lama_mpe.ckpt'), device='cpu')
        self.model.eval()
        self.backend = 'torch'
        if device.startswith('cuda') or device == 'mps':
            self.model.to(self.torch_device)
        elif device == 'dml':
            # PyTorch torch-directml 不支持 ComplexFloat 复数算子，在 PyTorch 模式下强制使用 CPU 运行以确保 100% 稳定
            self.torch_device = torch.device('cpu')

    async def _unload(self):
        if hasattr(self, 'backend'):
            if self.backend == 'onnx':
                del self.session
            elif self.backend == 'torch':
                del self.model
        elif hasattr(self, 'model'):
            del self.model

    async def _infer(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        # ✅ ONNX推理（lamampe.onnx 包含完整的 MPE 支持：4个输入 image, mask, rel_pos, direct）
        if hasattr(self, 'backend') and self.backend == 'onnx':
            try:
                # 调用包含MPE的ONNX推理
                return await self._infer_onnx(image, mask, inpainting_size, verbose)
            except Exception as e:
                self.logger.warning(f'ONNX推理失败（{str(e)[:100]}），本次降级到PyTorch')
                # 降级：加载PyTorch模型（.ckpt 应该已经在初始化时下载）
                if not hasattr(self, 'model'):
                    self.logger.info('正在加载PyTorch模型...')
                    self.model = load_lama_mpe(self._get_file_path('inpainting_lama_mpe.ckpt'), device='cpu')
                    self.model.eval()
                    if self.device == 'dml':
                        self.torch_device = torch.device('cpu')
                    if self.device.startswith('cuda') or self.device == 'mps' or self.device == 'dml':
                        self.model.to(self.torch_device)
        
        # ✅ PyTorch推理（原有逻辑）
        img_original = np.copy(image)
        mask_original = np.copy(mask)
        mask_original = np.where(mask_original > 0, 1, 0).astype(np.uint8)
        mask_original = mask_original[:, :, None]

        height, width, c = image.shape
        if max(image.shape[0: 2]) > inpainting_size:
            image = resize_keep_aspect(image, inpainting_size)
            mask = resize_keep_aspect(mask, inpainting_size)
        pad_size = 8
        h, w, c = image.shape
        if h % pad_size != 0:
            new_h = (pad_size - (h % pad_size)) + h
        else:
            new_h = h
        if w % pad_size != 0:
            new_w = (pad_size - (w % pad_size)) + w
        else:
            new_w = w
        if new_h != h or new_w != w:
            image = cv2.resize(image, (new_w, new_h), interpolation = cv2.INTER_LINEAR)
            mask = cv2.resize(mask, (new_w, new_h), interpolation = cv2.INTER_NEAREST)
        mask = np.where(mask > 0, 255, 0).astype(np.uint8)
        self.logger.info(f'Inpainting resolution: {new_w}x{new_h}')
        if isinstance(self.model, LamaFourier):
            img_torch = torch.from_numpy(image).permute(2, 0, 1).unsqueeze_(0).float() / 255.
        else:
            img_torch = torch.from_numpy(image).permute(2, 0, 1).unsqueeze_(0).float() / 127.5 - 1.0
        mask_torch = torch.from_numpy(mask).unsqueeze_(0).unsqueeze_(0).float() / 255.0
        mask_torch[mask_torch < 0.5] = 0
        mask_torch[mask_torch >= 0.5] = 1
        if self.device.startswith('cuda') or self.device == 'mps' or self.device == 'dml':
            img_torch = img_torch.to(self.torch_device)
            mask_torch = mask_torch.to(self.torch_device)
        with torch.no_grad():
            img_torch *= (1 - mask_torch)
            if not (self.device.startswith('cuda')):
                # mps and dml devices here
                img_inpainted_torch = self.model(img_torch, mask_torch)
            else:
                # Note: lama's weight shouldn't be convert to fp16 or bf16 otherwise it produces darkened results.
                # but it can inference under torch.autocast

                precision = TORCH_DTYPE_MAP[str(config.inpainting_precision)]
                
                if precision == torch.float16:
                    precision = torch.bfloat16
                    self.logger.warning('Switch to bf16 due to Lama only compatible with bf16 and fp32.')

                with torch.autocast(device_type="cuda", dtype=precision):
                    img_inpainted_torch = self.model(img_torch, mask_torch)
                
        if isinstance(self.model, LamaFourier):
            img_inpainted_torch = img_inpainted_torch.to(torch.float32)
            img_inpainted = (img_inpainted_torch.cpu().squeeze_(0).permute(1, 2, 0).numpy() * 255.).astype(np.uint8)
        else:
            img_inpainted_torch = img_inpainted_torch.to(torch.float32)
            img_inpainted = ((img_inpainted_torch.cpu().squeeze_(0).permute(1, 2, 0).numpy() + 1.0) * 127.5).astype(np.uint8)
        if new_h != height or new_w != width:
            img_inpainted = cv2.resize(img_inpainted, (width, height), interpolation = cv2.INTER_LINEAR)
        
        # 确保所有数组尺寸匹配
        self.logger.debug(f"Before blend - img_inpainted: {img_inpainted.shape}, img_original: {img_original.shape}, mask_original: {mask_original.shape}")
        
        # 如果mask_original尺寸不匹配，resize它
        if mask_original.shape[:2] != img_inpainted.shape[:2]:
            self.logger.warning(f"Resizing mask_original from {mask_original.shape} to match img_inpainted {img_inpainted.shape[:2]}")
            mask_original = cv2.resize(mask_original, (img_inpainted.shape[1], img_inpainted.shape[0]), interpolation = cv2.INTER_NEAREST)
            mask_original = mask_original[:, :, None] if len(mask_original.shape) == 2 else mask_original
            mask_original = np.where(mask_original > 0, 1, 0).astype(np.uint8)
        
        ans = img_inpainted * mask_original + img_original * (1 - mask_original)
        
        return ans
    
    async def _infer_onnx(self, image: np.ndarray, mask: np.ndarray, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        """ONNX推理方法（包含MPE计算）- 采用Rust策略：padding而非resize"""
        img_original = np.copy(image)
        mask_original = np.copy(mask)
        mask_original = np.where(mask_original > 0, 1, 0).astype(np.uint8)
        mask_original = mask_original[:, :, None]
        
        height, width, c = image.shape
        
        # 步骤1: 保持宽高比缩放（如果需要）
        if max(image.shape[0: 2]) > inpainting_size:
            image = resize_keep_aspect(image, inpainting_size)
            mask_resized = resize_keep_aspect(mask, inpainting_size)
            mask_original_resized = resize_keep_aspect(mask_original, inpainting_size)
        else:
            mask_resized = mask
            mask_original_resized = mask_original
        mask_resized = np.where(mask_resized > 0, 255, 0).astype(np.uint8)
        mask_original_resized = np.where(mask_original_resized > 0, 1, 0).astype(np.uint8)
        
        # 步骤2: Padding到64的倍数（Lama FFT架构需要更大对齐值避免维度不匹配）
        # 原先 pad_size=8 在某些尺寸下会导致 FFT 中间层维度不匹配（如 168 vs 169）
        pad_size = 64
        h, w, c = image.shape
        new_h = h if h % pad_size == 0 else (pad_size - (h % pad_size)) + h
        new_w = w if w % pad_size == 0 else (pad_size - (w % pad_size)) + w
        
        self.logger.info(f'Inpainting resolution: {new_w}x{new_h}')
        
        # ✅ 使用 padding 而非 resize（保持图像不变形）
        img_pad = np.pad(image, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')
        mask_pad_single = np.pad(mask_resized, ((0, new_h - h), (0, new_w - w)), mode='constant', constant_values=0)
        
        # 处理 mask_original_resized 的 padding
        if len(mask_original_resized.shape) == 3:
            mask_pad = np.pad(mask_original_resized, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')
        else:
            mask_pad = np.pad(mask_original_resized, ((0, new_h - h), (0, new_w - w)), mode='symmetric')
            mask_pad = mask_pad[:, :, None]
        
        # 计算MPE输入
        rel_pos, direct = load_masked_position_encoding(mask_pad_single)
        
        # 准备输入（0-1归一化）
        img = img_pad.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]  # [1, 3, H, W]
        
        mask_input = mask_pad.astype(np.float32)[:, :, 0:1]
        mask_input = np.transpose(mask_input, (2, 0, 1))[None, ...]  # [1, 1, H, W]
        
        # MPE输入格式
        rel_pos_input = rel_pos[None, ...].astype(np.int64)
        direct_input = direct[None, ...].astype(np.int64)
        
        # ONNX推理
        ort_inputs = {
            'image': img.astype(np.float32),
            'mask': mask_input.astype(np.float32),
            'rel_pos': rel_pos_input,
            'direct': direct_input
        }
        img_inpainted = self.session.run(None, ort_inputs)[0]
        
        # 后处理
        img_inpainted = np.transpose(img_inpainted[0], (1, 2, 0))
        img_inpainted = (img_inpainted * 255.).astype(np.uint8)
        
        # 移除 padding
        img_inpainted = img_inpainted[:h, :w, :]
        
        # 还原到原始尺寸（使用双三次插值，与Rust一致）
        if max(height, width) > inpainting_size:
            img_inpainted = cv2.resize(img_inpainted, (width, height), interpolation=cv2.INTER_CUBIC)
            mask_original_resized = cv2.resize(mask_original_resized, (width, height), interpolation=cv2.INTER_NEAREST)
            if len(mask_original_resized.shape) == 2:
                mask_original_resized = mask_original_resized[:, :, None]
            mask_original_resized = np.where(mask_original_resized > 0, 1, 0).astype(np.uint8)
        
        ans = img_inpainted * mask_original_resized + img_original * (1 - mask_original_resized)
        
        return ans
    
    async def _infer_onnx_mpe(self, image: np.ndarray, mask: np.ndarray, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        """ONNX专用推理方法（MPE版本）"""
        img_original = np.copy(image)
        mask_original = np.copy(mask)
        mask_original = np.where(mask_original > 0, 1, 0).astype(np.uint8)
        mask_original = mask_original[:, :, None]
        
        height, width, c = image.shape
        if max(image.shape[0: 2]) > inpainting_size:
            image = resize_keep_aspect(image, inpainting_size)
            mask_resized = resize_keep_aspect(mask, inpainting_size)
            mask_original_resized = resize_keep_aspect(mask_original, inpainting_size)
        else:
            mask_resized = mask
            mask_original_resized = mask_original
        mask_resized = np.where(mask_resized > 0, 255, 0).astype(np.uint8)
        mask_original_resized = np.where(mask_original_resized > 0, 1, 0).astype(np.uint8)
        
        # Padding到64的倍数（Lama FFT架构需要更大对齐值）
        pad_size = 64
        h, w, c = image.shape
        new_h = h if h % pad_size == 0 else (pad_size - (h % pad_size)) + h
        new_w = w if w % pad_size == 0 else (pad_size - (w % pad_size)) + w
        
        # Padding
        img_pad = np.pad(image, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')
        mask_pad_single = np.pad(mask_resized, ((0, new_h - h), (0, new_w - w)), mode='constant', constant_values=0)
        # 根据 mask_original_resized 的维度决定 padding 参数
        if len(mask_original_resized.shape) == 3:
            mask_pad = np.pad(mask_original_resized, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')
        else:
            mask_pad = np.pad(mask_original_resized, ((0, new_h - h), (0, new_w - w)), mode='symmetric')
            mask_pad = mask_pad[:, :, None]  # 扩展为3维
        
        # ✅ 计算MPE输入（使用padding后的mask）
        rel_pos, direct = load_masked_position_encoding(mask_pad_single)
        
        # 准备输入（0-1归一化）
        img = img_pad.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))[None, ...]  # [1, 3, H, W]
        
        mask_input = mask_pad.astype(np.float32)[:, :, 0:1]
        mask_input = np.transpose(mask_input, (2, 0, 1))[None, ...]  # [1, 1, H, W]
        
        # MPE输入格式：[1, H, W] for rel_pos, [1, H, W, 4] for direct
        rel_pos_input = rel_pos[None, ...].astype(np.int64)  # [1, H, W]
        direct_input = direct[None, ...].astype(np.int64)    # [1, H, W, 4]
        
        # ONNX推理（4个输入）
        ort_inputs = {
            'image': img.astype(np.float32),
            'mask': mask_input.astype(np.float32),
            'rel_pos': rel_pos_input,
            'direct': direct_input
        }
        img_inpainted = self.session.run(None, ort_inputs)[0]
        
        # 后处理
        img_inpainted = np.transpose(img_inpainted[0], (1, 2, 0))  # [H, W, 3]
        img_inpainted = (img_inpainted * 255.).astype(np.uint8)
        
        # Remove padding
        img_inpainted = img_inpainted[:h, :w, :]
        
        # Resize back
        if max(height, width) > inpainting_size:
            img_inpainted = cv2.resize(img_inpainted, (width, height), interpolation=cv2.INTER_LINEAR)
            mask_original_resized = cv2.resize(mask_original_resized, (width, height), interpolation=cv2.INTER_NEAREST)
            if len(mask_original_resized.shape) == 2:
                mask_original_resized = mask_original_resized[:, :, None]
            mask_original_resized = np.where(mask_original_resized > 0, 1, 0).astype(np.uint8)
        
        ans = img_inpainted * mask_original_resized + img_original * (1 - mask_original_resized)
        
        return ans


class LamaLargeInpainter(LamaMPEInpainter):

    _MODEL_MAPPING = {
        'model': {
            # 使用 Hugging Face 镜像站（自动遵循 HF_ENDPOINT 环境变量）
            'url': [
                'https://hf-mirror.com/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/lama_large_512px.ckpt',
            ],
            'hash': '11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935',
            'file': '.',
        },
        'onnx': {
            'url': [
                'https://github.com/frederik-uni/manga-image-translator-rust/releases/download/lama_large_512px/model.onnx',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/lama_large_512px_inpainting.onnx',
            ],
            'hash': '107c8306ac1d27c83638d6535846986542dfe2707f1498b1ac9be25b4a963864',
            'file': 'lamalarge.onnx',
        },
    }
    
    def _check_downloaded_map(self, map_key: str) -> bool:
        """检查模型文件是否存在
        
        逻辑：
        - 如果是 'onnx' key，只检查 ONNX 文件
        - 如果是 'model' key：必须确保 .ckpt 文件存在（用于降级）
        """
        # 如果检查的是 onnx key，直接调用父类检查
        if map_key == 'onnx':
            return super()._check_downloaded_map(map_key)
        
        # 如果检查的是 model key（.ckpt）
        if map_key == 'model':
            ckpt_path = self._get_file_path('lama_large_512px.ckpt')
            # 必须确保 .ckpt 存在，用于 ONNX 失败时降级
            return os.path.isfile(ckpt_path)
        
        return super()._check_downloaded_map(map_key)

    async def _load(self, device: str, force_torch: bool = False):
        self.device = device
        self.torch_device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        
        # ✅ CPU或DirectML模式使用ONNX（DirectML不支持PyTorch的ComplexFloat复数算子，ONNX更稳定且支持DirectML GPU加速）
        if not device.startswith('cuda') and device != 'mps' and not force_torch:
            try:
                ort = import_onnxruntime(
                    "onnxruntime is required for Lama Large ONNX inference. "
                    "Install with: pip install onnxruntime-gpu (or onnxruntime)"
                )
                onnx_path = self._get_file_path('lamalarge.onnx')
                ckpt_path = self._get_file_path('lama_large_512px.ckpt')
                
                # 检查 ONNX 文件是否存在
                if not os.path.isfile(onnx_path):
                    self.logger.info('ONNX 模型不存在，需要下载')
                    # 标记为未下载，触发下载
                    self._downloaded = False
                    await self._download()
                    self._downloaded = True
                
                # ⚠️ 检查备用的 PyTorch 模型是否存在（用于 ONNX 失败时降级）
                if not os.path.isfile(ckpt_path):
                    self.logger.warning(f'备用 PyTorch 模型不存在: {ckpt_path}')
                    self.logger.info('正在下载备用 PyTorch 模型...')
                    try:
                        # 临时标记为未下载，触发下载
                        old_downloaded = self._downloaded
                        self._downloaded = False
                        await self._download()
                        self._downloaded = old_downloaded
                        self.logger.info('备用 PyTorch 模型下载完成')
                    except Exception as download_error:
                        self.logger.warning(f'备用模型下载失败: {download_error}')
                        self.logger.warning('如果 ONNX 推理失败，将无法降级到 PyTorch')
                
                self.logger.info(f'使用ONNX模型（DirectML/CPU优化）: {onnx_path}')
                
                # 🔧 ONNX Runtime 配置
                sess_options = create_session_options(
                    ort,
                    log_severity_level=3,
                    enable_mem_pattern=False,
                    enable_cpu_mem_arena=False,
                    intra_op_num_threads=4,
                    inter_op_num_threads=1,
                )
                self.session, ort_device = create_inference_session(
                    ort,
                    onnx_path,
                    sess_options=sess_options,
                    device='cpu' if device == 'dml' else device,
                    logger=self.logger,
                )
                self.backend = 'onnx'
                self.logger.info(f'ONNX Runtime版本: {ort.__version__}，活动设备: {ort_device}')
                return
            except Exception as e:
                self.logger.warning(f'ONNX加载失败，回退到PyTorch: {e}')
        
        # ✅ 强制使用PyTorch或GPU模式
        if force_torch:
            self.logger.info('已启用"强制使用PyTorch"选项，跳过ONNX')
        
        # ✅ GPU模式或ONNX失败时使用PyTorch
        ckpt_path = self._get_file_path('lama_large_512px.ckpt')
        
        # 检查 .ckpt 文件是否存在
        if not os.path.isfile(ckpt_path):
            self.logger.info('PyTorch 模型 (.ckpt) 不存在，需要下载')
            # 标记为未下载，触发下载
            self._downloaded = False
            await self._download()
            self._downloaded = True
        
        # 直接加载到目标设备，避免重复移动
        target_device = self.torch_device if (device.startswith('cuda') or device == 'mps') else 'cpu'
        if device == 'dml':
            # PyTorch torch-directml 不支持 ComplexFloat 复数算子，在 PyTorch 模式下强制使用 CPU 运行以确保 100% 稳定
            target_device = 'cpu'
        self.model = load_lama_mpe(ckpt_path, device=target_device, use_mpe=False, large_arch=True)
        self.model.eval()
        self.backend = 'torch'
    
    async def _unload(self):
        if hasattr(self, 'backend'):
            if self.backend == 'onnx':
                del self.session
            elif self.backend == 'torch':
                del self.model

    def _prepare_large_image(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        inpainting_size: int,
        verbose: bool = False,
    ) -> dict:
        mask_binary = np.asarray(mask)
        if mask_binary.ndim == 3:
            mask_binary = mask_binary[:, :, 0]
        mask_binary = np.where(mask_binary > 0, 1, 0).astype(np.uint8)
        mask_original = mask_binary[:, :, None]

        height, width = image.shape[:2]
        if max(height, width) > inpainting_size:
            image_resized = resize_keep_aspect(image, inpainting_size)
            mask_resized = resize_keep_aspect(mask_original, inpainting_size)
        else:
            image_resized = image
            mask_resized = mask_original
        mask_resized = np.where(mask_resized > 0, 1, 0).astype(np.uint8)
        if mask_resized.ndim == 2:
            mask_resized = mask_resized[:, :, None]

        pad_size = 64
        h, w = image_resized.shape[:2]
        new_h = h if h % pad_size == 0 else (pad_size - (h % pad_size)) + h
        new_w = w if w % pad_size == 0 else (pad_size - (w % pad_size)) + w

        self.logger.info(f'Inpainting resolution: {new_w}x{new_h}')

        image_pad = np.pad(image_resized, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')
        mask_pad = np.pad(mask_resized, ((0, new_h - h), (0, new_w - w), (0, 0)), mode='symmetric')

        return {
            'img_original': image,
            'mask_resized': mask_resized,
            'image_pad': image_pad,
            'mask_pad': mask_pad,
            'resized_shape': (h, w),
            'original_shape': (height, width),
            'needs_resize_back': max(height, width) > inpainting_size,
        }

    def _restore_large_output(self, img_inpainted: np.ndarray, prep: dict) -> np.ndarray:
        h, w = prep['resized_shape']
        img_inpainted = img_inpainted[:h, :w, :]

        mask_blend = prep['mask_resized']
        if prep['needs_resize_back']:
            height, width = prep['original_shape']
            img_inpainted = cv2.resize(img_inpainted, (width, height), interpolation=cv2.INTER_CUBIC)
            mask_blend = cv2.resize(mask_blend, (width, height), interpolation=cv2.INTER_NEAREST)
            if mask_blend.ndim == 2:
                mask_blend = mask_blend[:, :, None]

        mask_blend = np.where(mask_blend > 0, 1, 0).astype(np.uint8)
        return img_inpainted * mask_blend + prep['img_original'] * (1 - mask_blend)

    async def _infer_torch_large(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        config: InpainterConfig,
        inpainting_size: int = 1024,
        verbose: bool = False,
    ) -> np.ndarray:
        prep = self._prepare_large_image(image, mask, inpainting_size, verbose)
        image_pad = prep.pop('image_pad')
        mask_pad = prep.pop('mask_pad')

        if isinstance(self.model, LamaFourier):
            img_torch = torch.from_numpy(image_pad).permute(2, 0, 1).unsqueeze_(0).float() / 255.0
        else:
            img_torch = torch.from_numpy(image_pad).permute(2, 0, 1).unsqueeze_(0).float() / 127.5 - 1.0

        mask_torch = torch.from_numpy(mask_pad[:, :, 0]).unsqueeze_(0).unsqueeze_(0).float()
        del image_pad, mask_pad
        if self.device.startswith('cuda') or self.device == 'mps' or self.device == 'dml':
            img_torch = img_torch.to(self.torch_device)
            mask_torch = mask_torch.to(self.torch_device)

        with torch.no_grad():
            img_torch *= (1 - mask_torch)
            if not self.device.startswith('cuda'):
                img_inpainted_torch = self.model(img_torch, mask_torch)
            else:
                precision = TORCH_DTYPE_MAP[str(config.inpainting_precision)]
                if precision == torch.float16:
                    precision = torch.bfloat16
                    self.logger.warning('Switch to bf16 due to Lama only compatible with bf16 and fp32.')

                with torch.autocast(device_type="cuda", dtype=precision):
                    img_inpainted_torch = self.model(img_torch, mask_torch)

        if isinstance(self.model, LamaFourier):
            img_inpainted_torch = img_inpainted_torch.to(torch.float32)
            img_inpainted = (img_inpainted_torch.cpu().squeeze_(0).permute(1, 2, 0).numpy() * 255.0).astype(np.uint8)
        else:
            img_inpainted_torch = img_inpainted_torch.to(torch.float32)
            img_inpainted = ((img_inpainted_torch.cpu().squeeze_(0).permute(1, 2, 0).numpy() + 1.0) * 127.5).astype(np.uint8)

        return self._restore_large_output(img_inpainted, prep)

    async def _infer(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        # ✅ ONNX推理，失败时自动降级到PyTorch
        if hasattr(self, 'backend') and self.backend == 'onnx':
            try:
                return await self._infer_onnx(image, mask, inpainting_size, verbose)
            except Exception as e:
                self.logger.warning(f'ONNX推理失败（{str(e)[:100]}），本次降级到PyTorch')
                # 降级：需要加载PyTorch模型
                if not hasattr(self, 'model'):
                    self.logger.info('正在加载PyTorch模型...')
                    ckpt_path = self._get_file_path('lama_large_512px.ckpt')
                    if not os.path.isfile(ckpt_path):
                        self.logger.error(f'PyTorch 模型文件不存在: {ckpt_path}')
                        self.logger.error('ONNX 推理失败且 PyTorch 模型缺失，无法进行修复')
                        raise FileNotFoundError(f'模型文件缺失: {ckpt_path}')
                    self.model = load_lama_mpe(ckpt_path, device='cpu', use_mpe=False, large_arch=True)
                    self.model.eval()
                    if self.device == 'dml':
                        self.torch_device = torch.device('cpu')
                    if self.device.startswith('cuda') or self.device == 'mps' or self.device == 'dml':
                        self.model.to(self.torch_device)

        # ✅ PyTorch推理：与 ONNX 保持一致的缩放/补边逻辑
        return await self._infer_torch_large(image, mask, config, inpainting_size, verbose)
    
    async def _infer_onnx(self, image: np.ndarray, mask: np.ndarray, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        """ONNX专用推理方法 - 采用Rust策略：padding而非resize"""
        try:
            prep = self._prepare_large_image(image, mask, inpainting_size, verbose)

            image_pad = prep.pop('image_pad')
            mask_pad = prep.pop('mask_pad')

            img = image_pad.astype(np.float32) / 255.0
            img = np.transpose(img, (2, 0, 1))[None, ...]  # [1, 3, H, W]

            mask_input = mask_pad.astype(np.float32)[:, :, 0:1]
            mask_input = np.transpose(mask_input, (2, 0, 1))[None, ...]  # [1, 1, H, W]
            del image_pad, mask_pad

            # ONNX推理
            ort_inputs = {
                'image': img,
                'mask': mask_input
            }
            img_inpainted = self.session.run(None, ort_inputs)[0]
            
            # 立即释放输入数据
            del img, mask_input, ort_inputs
            
            # 后处理
            img_inpainted = np.transpose(img_inpainted[0], (1, 2, 0))
            img_inpainted = (img_inpainted * 255.).astype(np.uint8)

            return self._restore_large_output(img_inpainted, prep)
            
        except Exception:
            raise



def set_requires_grad(module, value):
    for param in module.parameters():
        param.requires_grad = value

def get_activation(kind='tanh'):
    if kind == 'tanh':
        return nn.Tanh()
    if kind == 'sigmoid':
        return nn.Sigmoid()
    if kind is False:
        return nn.Identity()
    raise ValueError(f'Unknown activation kind {kind}')


class FFCSE_block(nn.Module):

    def __init__(self, channels, ratio_g):
        super(FFCSE_block, self).__init__()
        in_cg = int(channels * ratio_g)
        in_cl = channels - in_cg
        r = 16

        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.conv1 = nn.Conv2d(channels, channels // r,
                               kernel_size=1, bias=True)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv_a2l = None if in_cl == 0 else nn.Conv2d(
            channels // r, in_cl, kernel_size=1, bias=True)
        self.conv_a2g = None if in_cg == 0 else nn.Conv2d(
            channels // r, in_cg, kernel_size=1, bias=True)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = x if type(x) is tuple else (x, 0)
        id_l, id_g = x

        x = id_l if type(id_g) is int else torch.cat([id_l, id_g], dim=1)
        x = self.avgpool(x)
        x = self.relu1(self.conv1(x))

        x_l = 0 if self.conv_a2l is None else id_l * \
            self.sigmoid(self.conv_a2l(x))
        x_g = 0 if self.conv_a2g is None else id_g * \
            self.sigmoid(self.conv_a2g(x))
        return x_l, x_g


class FourierUnit(nn.Module):

    def __init__(self, in_channels, out_channels, groups=1, spatial_scale_factor=None, spatial_scale_mode='bilinear',
                 spectral_pos_encoding=False, use_se=False, se_kwargs=None, ffc3d=False, fft_norm='ortho'):
        # bn_layer not used
        super(FourierUnit, self).__init__()
        self.groups = groups

        self.conv_layer = torch.nn.Conv2d(in_channels=in_channels * 2 + (2 if spectral_pos_encoding else 0),
                                          out_channels=out_channels * 2,
                                          kernel_size=1, stride=1, padding=0, groups=self.groups, bias=False)
        self.bn = torch.nn.BatchNorm2d(out_channels * 2)
        self.relu = torch.nn.ReLU(inplace=True)

        # squeeze and excitation block
        self.use_se = use_se
        # if use_se:
        #     if se_kwargs is None:
        #         se_kwargs = {}
        #     self.se = SELayer(self.conv_layer.in_channels, **se_kwargs)

        self.spatial_scale_factor = spatial_scale_factor
        self.spatial_scale_mode = spatial_scale_mode
        self.spectral_pos_encoding = spectral_pos_encoding
        self.ffc3d = ffc3d
        self.fft_norm = fft_norm

    def forward(self, x):
        batch = x.shape[0]

        if self.spatial_scale_factor is not None:
            orig_size = x.shape[-2:]
            x = F.interpolate(x, scale_factor=self.spatial_scale_factor, mode=self.spatial_scale_mode, align_corners=False)

        # (batch, c, h, w/2+1, 2)
        fft_dim = (-3, -2, -1) if self.ffc3d else (-2, -1)

        if x.dtype in (torch.float16, torch.bfloat16):
            x = x.type(torch.float32)

        ffted = torch.fft.rfftn(x, dim=fft_dim, norm=self.fft_norm)
        ffted = torch.stack((ffted.real, ffted.imag), dim=-1)
        ffted = ffted.permute(0, 1, 4, 2, 3).contiguous()  # (batch, c, 2, h, w/2+1)
        ffted = ffted.view((batch, -1,) + ffted.size()[3:])

        if self.spectral_pos_encoding:
            height, width = ffted.shape[-2:]
            coords_vert = torch.linspace(0, 1, height)[None, None, :, None].expand(batch, 1, height, width).to(ffted)
            coords_hor = torch.linspace(0, 1, width)[None, None, None, :].expand(batch, 1, height, width).to(ffted)
            ffted = torch.cat((coords_vert, coords_hor, ffted), dim=1)

        if self.use_se:
            ffted = self.se(ffted)

        ffted = self.conv_layer(ffted)  # (batch, c*2, h, w/2+1)
        ffted = self.relu(self.bn(ffted))

        ffted = ffted.view((batch, -1, 2,) + ffted.size()[2:]).permute(
            0, 1, 3, 4, 2).contiguous()  # (batch,c, t, h, w/2+1, 2)
        if ffted.dtype in (torch.float16, torch.bfloat16):
            ffted = ffted.type(torch.float32)
        ffted = torch.complex(ffted[..., 0], ffted[..., 1])

        ifft_shape_slice = x.shape[-3:] if self.ffc3d else x.shape[-2:]
        output = torch.fft.irfftn(ffted, s=ifft_shape_slice, dim=fft_dim, norm=self.fft_norm)

        if self.spatial_scale_factor is not None:
            output = F.interpolate(output, size=orig_size, mode=self.spatial_scale_mode, align_corners=False)

        return output


class SpectralTransform(nn.Module):

    def __init__(self, in_channels, out_channels, stride=1, groups=1, enable_lfu=True, **fu_kwargs):
        # bn_layer not used
        super(SpectralTransform, self).__init__()
        self.enable_lfu = enable_lfu
        if stride == 2:
            self.downsample = nn.AvgPool2d(kernel_size=(2, 2), stride=2)
        else:
            self.downsample = nn.Identity()

        self.stride = stride
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels //
                      2, kernel_size=1, groups=groups, bias=False),
            nn.BatchNorm2d(out_channels // 2),
            nn.ReLU(inplace=True)
        )
        self.fu = FourierUnit(
            out_channels // 2, out_channels // 2, groups, **fu_kwargs)
        if self.enable_lfu:
            self.lfu = FourierUnit(
                out_channels // 2, out_channels // 2, groups)
        self.conv2 = torch.nn.Conv2d(
            out_channels // 2, out_channels, kernel_size=1, groups=groups, bias=False)

    def forward(self, x):

        x = self.downsample(x)
        x = self.conv1(x)
        output = self.fu(x)

        if self.enable_lfu:
            n, c, h, w = x.shape
            split_no = 2
            split_s = h // split_no
            xs = torch.cat(torch.split(
                x[:, :c // 4], split_s, dim=-2), dim=1).contiguous()
            xs = torch.cat(torch.split(xs, split_s, dim=-1),
                           dim=1).contiguous()
            xs = self.lfu(xs)
            xs = xs.repeat(1, 1, split_no, split_no).contiguous()
        else:
            xs = 0

        output = self.conv2(x + output + xs)

        return output


class FFC(nn.Module):

    def __init__(self, in_channels, out_channels, kernel_size,
                 ratio_gin, ratio_gout, stride=1, padding=0,
                 dilation=1, groups=1, bias=False, enable_lfu=True,
                 padding_type='reflect', gated=False, **spectral_kwargs):
        super(FFC, self).__init__()

        assert stride == 1 or stride == 2, "Stride should be 1 or 2."
        self.stride = stride

        in_cg = int(in_channels * ratio_gin)
        in_cl = in_channels - in_cg
        out_cg = int(out_channels * ratio_gout)
        out_cl = out_channels - out_cg
        #groups_g = 1 if groups == 1 else int(groups * ratio_gout)
        #groups_l = 1 if groups == 1 else groups - groups_g

        self.ratio_gin = ratio_gin
        self.ratio_gout = ratio_gout
        self.global_in_num = in_cg

        module = nn.Identity if in_cl == 0 or out_cl == 0 else nn.Conv2d
        self.convl2l = module(in_cl, out_cl, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cl == 0 or out_cg == 0 else nn.Conv2d
        self.convl2g = module(in_cl, out_cg, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cg == 0 or out_cl == 0 else nn.Conv2d
        self.convg2l = module(in_cg, out_cl, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cg == 0 or out_cg == 0 else SpectralTransform
        self.convg2g = module(
            in_cg, out_cg, stride, 1 if groups == 1 else groups // 2, enable_lfu, **spectral_kwargs)

        self.gated = gated
        module = nn.Identity if in_cg == 0 or out_cl == 0 or not self.gated else nn.Conv2d
        self.gate = module(in_channels, 2, 1)

    def forward(self, x):
        x_l, x_g = x if type(x) is tuple else (x, 0)
        out_xl, out_xg = 0, 0

        if self.gated:
            total_input_parts = [x_l]
            if torch.is_tensor(x_g):
                total_input_parts.append(x_g)
            total_input = torch.cat(total_input_parts, dim=1)

            gates = torch.sigmoid(self.gate(total_input))
            g2l_gate, l2g_gate = gates.chunk(2, dim=1)
        else:
            g2l_gate, l2g_gate = 1, 1

        if self.ratio_gout != 1:
            out_xl = self.convl2l(x_l) + self.convg2l(x_g) * g2l_gate
        if self.ratio_gout != 0:
            out_xg = self.convl2g(x_l) * l2g_gate + self.convg2g(x_g)

        return out_xl, out_xg


class FFC_BN_ACT(nn.Module):

    def __init__(self, in_channels, out_channels,
                 kernel_size, ratio_gin, ratio_gout,
                 stride=1, padding=0, dilation=1, groups=1, bias=False,
                 norm_layer=nn.BatchNorm2d, activation_layer=nn.Identity,
                 padding_type='reflect',
                 enable_lfu=True, **kwargs):
        super(FFC_BN_ACT, self).__init__()
        self.ffc = FFC(in_channels, out_channels, kernel_size,
                       ratio_gin, ratio_gout, stride, padding, dilation,
                       groups, bias, enable_lfu, padding_type=padding_type, **kwargs)
        lnorm = nn.Identity if ratio_gout == 1 else norm_layer
        gnorm = nn.Identity if ratio_gout == 0 else norm_layer
        global_channels = int(out_channels * ratio_gout)
        self.bn_l = lnorm(out_channels - global_channels)
        self.bn_g = gnorm(global_channels)

        lact = nn.Identity if ratio_gout == 1 else activation_layer
        gact = nn.Identity if ratio_gout == 0 else activation_layer
        self.act_l = lact(inplace=True)
        self.act_g = gact(inplace=True)

    def forward(self, x):
        x_l, x_g = self.ffc(x)
        x_l = self.act_l(self.bn_l(x_l))
        x_g = self.act_g(self.bn_g(x_g))
        return x_l, x_g


class FFCResnetBlock(nn.Module):
    def __init__(self, dim, padding_type, norm_layer, activation_layer=nn.ReLU, dilation=1,
                 spatial_transform_kwargs=None, inline=False, **conv_kwargs):
        super().__init__()
        self.conv1 = FFC_BN_ACT(dim, dim, kernel_size=3, padding=dilation, dilation=dilation,
                                norm_layer=norm_layer,
                                activation_layer=activation_layer,
                                padding_type=padding_type,
                                **conv_kwargs)
        self.conv2 = FFC_BN_ACT(dim, dim, kernel_size=3, padding=dilation, dilation=dilation,
                                norm_layer=norm_layer,
                                activation_layer=activation_layer,
                                padding_type=padding_type,
                                **conv_kwargs)
        # if spatial_transform_kwargs is not None:
        #     self.conv1 = LearnableSpatialTransformWrapper(self.conv1, **spatial_transform_kwargs)
        #     self.conv2 = LearnableSpatialTransformWrapper(self.conv2, **spatial_transform_kwargs)
        self.inline = inline

    def forward(self, x):
        if self.inline:
            x_l, x_g = x[:, :-self.conv1.ffc.global_in_num], x[:, -self.conv1.ffc.global_in_num:]
        else:
            x_l, x_g = x if type(x) is tuple else (x, 0)

        id_l, id_g = x_l, x_g

        x_l, x_g = self.conv1((x_l, x_g))
        x_l, x_g = self.conv2((x_l, x_g))

        x_l, x_g = id_l + x_l, id_g + x_g
        out = x_l, x_g
        if self.inline:
            out = torch.cat(out, dim=1)
        return out


class MaskedSinusoidalPositionalEmbedding(nn.Embedding):
    """This module produces sinusoidal positional embeddings of any length."""

    def __init__(self, num_embeddings: int, embedding_dim: int):
        super().__init__(num_embeddings, embedding_dim)
        self.weight = self._init_weight(self.weight)

    @staticmethod
    def _init_weight(out: nn.Parameter):
        """
        Identical to the XLM create_sinusoidal_embeddings except features are not interleaved. The cos features are in
        the 2nd half of the vector. [dim // 2:]
        """
        n_pos, dim = out.shape
        position_enc = np.array(
            [[pos / np.power(10000, 2 * (j // 2) / dim) for j in range(dim)] for pos in range(n_pos)]
        )
        out.requires_grad = False  # set early to avoid an error in pytorch-1.8+
        sentinel = dim // 2 if dim % 2 == 0 else (dim // 2) + 1
        out[:, 0:sentinel] = torch.FloatTensor(np.sin(position_enc[:, 0::2]))
        out[:, sentinel:] = torch.FloatTensor(np.cos(position_enc[:, 1::2]))
        out.detach_()
        return out

    @torch.no_grad()
    def forward(self, input_ids):
        """`input_ids` is expected to be [bsz x seqlen]."""
        return super().forward(input_ids)


class MultiLabelEmbedding(nn.Module):
    def __init__(self, num_positions: int, embedding_dim: int):
        super().__init__()
        self.weight = nn.Parameter(torch.Tensor(num_positions, embedding_dim))
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.normal_(self.weight)

    def forward(self, input_ids):
        # input_ids:[B,HW,4](onehot)
        out = torch.matmul(input_ids, self.weight)  # [B,HW,dim]
        return out


class NLayerDiscriminator(nn.Module):
    def __init__(self, input_nc=3, ndf=64, n_layers=4, norm_layer=nn.BatchNorm2d,):
        super().__init__()
        self.n_layers = n_layers

        kw = 4
        padw = int(np.ceil((kw-1.0)/2))
        sequence = [[nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw),
                     nn.LeakyReLU(0.2, True)]]

        nf = ndf
        for n in range(1, n_layers):
            nf_prev = nf
            nf = min(nf * 2, 512)

            cur_model = []
            cur_model += [
                nn.Conv2d(nf_prev, nf, kernel_size=kw, stride=2, padding=padw),
                norm_layer(nf),
                nn.LeakyReLU(0.2, True)
            ]
            sequence.append(cur_model)

        nf_prev = nf
        nf = min(nf * 2, 512)

        cur_model = []
        cur_model += [
            nn.Conv2d(nf_prev, nf, kernel_size=kw, stride=1, padding=padw),
            norm_layer(nf),
            nn.LeakyReLU(0.2, True)
        ]
        sequence.append(cur_model)

        sequence += [[nn.Conv2d(nf, 1, kernel_size=kw, stride=1, padding=padw)]]

        for n in range(len(sequence)):
            setattr(self, 'model'+str(n), nn.Sequential(*sequence[n]))

    def get_all_activations(self, x):
        res = [x]
        for n in range(self.n_layers + 2):
            model = getattr(self, 'model' + str(n))
            res.append(model(res[-1]))
        return res[1:]

    def forward(self, x):
        act = self.get_all_activations(x)
        return act[-1], act[:-1]


class ConcatTupleLayer(nn.Module):
    def forward(self, x):
        assert isinstance(x, tuple)
        x_l, x_g = x
        assert torch.is_tensor(x_l) or torch.is_tensor(x_g)
        if not torch.is_tensor(x_g):
            return x_l
        return torch.cat(x, dim=1)


class FFCResNetGenerator(nn.Module):
    def __init__(self, input_nc=4, output_nc=3, ngf=64, n_downsampling=3, n_blocks=9, norm_layer=nn.BatchNorm2d,
                 padding_type='reflect', activation_layer=nn.ReLU,
                 up_norm_layer=nn.BatchNorm2d, up_activation=nn.ReLU(True),
                 init_conv_kwargs={}, downsample_conv_kwargs={}, resnet_conv_kwargs={}, spatial_transform_kwargs={},
                 add_out_act=True, max_features=1024, out_ffc=False, out_ffc_kwargs={}):
        assert (n_blocks >= 0)
        super().__init__()

        model = [nn.ReflectionPad2d(3),
                 FFC_BN_ACT(input_nc, ngf, kernel_size=7, padding=0, norm_layer=norm_layer,
                            activation_layer=activation_layer, **init_conv_kwargs)]

        ### downsample
        for i in range(n_downsampling):
            mult = 2 ** i
            if i == n_downsampling - 1:
                cur_conv_kwargs = dict(downsample_conv_kwargs)
                cur_conv_kwargs['ratio_gout'] = resnet_conv_kwargs.get('ratio_gin', 0)
            else:
                cur_conv_kwargs = downsample_conv_kwargs
            model += [FFC_BN_ACT(min(max_features, ngf * mult),
                                 min(max_features, ngf * mult * 2),
                                 kernel_size=3, stride=2, padding=1,
                                 norm_layer=norm_layer,
                                 activation_layer=activation_layer,
                                 **cur_conv_kwargs)]

        mult = 2 ** n_downsampling
        feats_num_bottleneck = min(max_features, ngf * mult)

        ### resnet blocks
        for i in range(n_blocks):
            cur_resblock = FFCResnetBlock(feats_num_bottleneck, padding_type=padding_type, activation_layer=activation_layer,
                                          norm_layer=norm_layer, **resnet_conv_kwargs)
            model += [cur_resblock]

        model += [ConcatTupleLayer()]

        ### upsample
        for i in range(n_downsampling):
            mult = 2 ** (n_downsampling - i)
            model += [nn.ConvTranspose2d(min(max_features, ngf * mult),
                                         min(max_features, int(ngf * mult / 2)),
                                         kernel_size=3, stride=2, padding=1, output_padding=1),
                      up_norm_layer(min(max_features, int(ngf * mult / 2))),
                      up_activation]

        if out_ffc:
            model += [FFCResnetBlock(ngf, padding_type=padding_type, activation_layer=activation_layer,
                                     norm_layer=norm_layer, inline=True, **out_ffc_kwargs)]

        model += [nn.ReflectionPad2d(3),
                  nn.Conv2d(ngf, output_nc, kernel_size=7, padding=0)]
        if add_out_act:
            model.append(get_activation('tanh' if add_out_act is True else add_out_act))
        self.model = nn.Sequential(*model)

    def forward(self, img, mask, rel_pos=None, direct=None) -> Tensor:
        masked_img = torch.cat([img * (1 - mask), mask], dim=1)
        if rel_pos is None:
            return self.model(masked_img)
        else:
            
            x_l, x_g = self.model[:2](masked_img)
            x_l = x_l.to(torch.float32)
            x_l += rel_pos
            x_l += direct
            return self.model[2:]((x_l, x_g))


class MPE(nn.Module):
    def __init__(self):
        super().__init__()
        self.rel_pos_emb = MaskedSinusoidalPositionalEmbedding(num_embeddings=128,
                                                                embedding_dim=64)
        self.direct_emb = MultiLabelEmbedding(num_positions=4, embedding_dim=64)
        self.alpha5 = nn.Parameter(torch.tensor(0, dtype=torch.float32), requires_grad=True)
        self.alpha6 = nn.Parameter(torch.tensor(0, dtype=torch.float32), requires_grad=True)

    def forward(self, rel_pos=None, direct=None):
        b, h, w = rel_pos.shape
        rel_pos = rel_pos.reshape(b, h * w)
        rel_pos_emb = self.rel_pos_emb(rel_pos).reshape(b, h, w, -1).permute(0, 3, 1, 2) * self.alpha5
        direct = direct.reshape(b, h * w, 4).to(torch.float32)
        direct_emb = self.direct_emb(direct).reshape(b, h, w, -1).permute(0, 3, 1, 2) * self.alpha6

        return rel_pos_emb, direct_emb



class LamaFourier:
    def __init__(self, build_discriminator=True, use_mpe=False, large_arch: bool = False) -> None:
        # super().__init__()

        n_blocks = 9
        if large_arch:
            n_blocks = 18
        
        self.generator = FFCResNetGenerator(4, 3, add_out_act='sigmoid', 
                            n_blocks = n_blocks,
                            init_conv_kwargs={
                            'ratio_gin': 0,
                            'ratio_gout': 0,
                            'enable_lfu': False
                        }, downsample_conv_kwargs={
                            'ratio_gin': 0,
                            'ratio_gout': 0,
                            'enable_lfu': False
                        }, resnet_conv_kwargs={
                            'ratio_gin': 0.75,
                            'ratio_gout': 0.75,
                            'enable_lfu': False
                        }, 
                    )
        
        self.discriminator = NLayerDiscriminator() if build_discriminator else None
        self.inpaint_only = False
        if use_mpe:
            self.mpe = MPE()
        else:
            self.mpe = None

    def train_generator(self):
        self.inpaint_only = False
        self.forward_generator = True
        self.forward_discriminator = False
        self.generator.train()
        self.discriminator.eval()
        set_requires_grad(self.discriminator, False)
        set_requires_grad(self.generator, True)
        if self.mpe is not None:
            set_requires_grad(self.mpe, True)

    def train_discriminator(self):
        self.inpaint_only = False
        self.forward_generator = False
        self.forward_discriminator = True
        self.discriminator.train()
        self.generator.eval()
        set_requires_grad(self.discriminator, True)
        set_requires_grad(self.generator, False)
        if self.mpe is not None:
            set_requires_grad(self.mpe, False)

    def to(self, device):
        self.generator.to(device)
        if self.discriminator is not None:
            self.discriminator.to(device)
        if self.mpe is not None:
            self.mpe.to(device)
        return self

    def eval(self):
        self.inpaint_only = True
        self.generator.eval()
        if self.mpe is not None:
            self.mpe.eval()
        return self

    def cuda(self):
        self.generator.cuda()
        if self.discriminator is not None:
            self.discriminator.cuda()
        if self.mpe is not None:
            self.mpe.cuda()
        return self

    def __call__(self, img: Tensor, mask: Tensor, rel_pos=None, direct=None):

        if self.mpe is not None:
            # 1 batch only
            rel_pos, _, direct = self.load_masked_position_encoding(mask[0][0].cpu().numpy())
            rel_pos = torch.LongTensor(rel_pos).unsqueeze_(0).to(img.device)
            direct = torch.LongTensor(direct).unsqueeze_(0).to(img.device)
            rel_pos, direct = self.mpe(rel_pos, direct)
        else:
            rel_pos, direct = None, None
        predicted_img = self.generator(img, mask, rel_pos, direct)

        if self.inpaint_only:
            return predicted_img * mask + (1 - mask) * img

        if self.forward_discriminator:
            predicted_img = predicted_img.detach()
            img.requires_grad = True


        discr_real_pred, discr_real_features = self.discriminator(img)
        discr_fake_pred, discr_fake_features = self.discriminator(predicted_img)
        # fp = discr_fake_pred.detach().mean()

        if self.forward_discriminator:
            return  {
                'predicted_img': predicted_img, 
                'discr_real_pred': discr_real_pred, 
                'discr_fake_pred':discr_fake_pred
            }
        else:
            return  {
                'predicted_img': predicted_img, 
                'discr_real_features': discr_real_features, 
                'discr_fake_features': discr_fake_features, 
                'discr_fake_pred': discr_fake_pred
            }

    def load_masked_position_encoding(self, mask):
        mask = (mask * 255).astype(np.uint8)
        ones_filter = np.ones((3, 3), dtype=np.float32)
        d_filter1 = np.array([[1, 1, 0], [1, 1, 0], [0, 0, 0]], dtype=np.float32)
        d_filter2 = np.array([[0, 0, 0], [1, 1, 0], [1, 1, 0]], dtype=np.float32)
        d_filter3 = np.array([[0, 1, 1], [0, 1, 1], [0, 0, 0]], dtype=np.float32)
        d_filter4 = np.array([[0, 0, 0], [0, 1, 1], [0, 1, 1]], dtype=np.float32)
        str_size = 256
        pos_num = 128

        ori_mask = mask.copy()
        ori_h, ori_w = ori_mask.shape[0:2]
        ori_mask = ori_mask / 255
        mask = cv2.resize(mask, (str_size, str_size), interpolation=cv2.INTER_AREA)
        mask[mask > 0] = 255
        h, w = mask.shape[0:2]
        mask3 = mask.copy()
        mask3 = 1. - (mask3 / 255.0)
        pos = np.zeros((h, w), dtype=np.int32)
        direct = np.zeros((h, w, 4), dtype=np.int32)
        i = 0

        if mask3.max() > 0:
            # otherwise it will cause infinity loop
            while np.sum(1 - mask3) > 0:
                i += 1
                mask3_ = cv2.filter2D(mask3, -1, ones_filter)
                mask3_[mask3_ > 0] = 1
                sub_mask = mask3_ - mask3
                pos[sub_mask == 1] = i

                m = cv2.filter2D(mask3, -1, d_filter1)
                m[m > 0] = 1
                m = m - mask3
                direct[m == 1, 0] = 1

                m = cv2.filter2D(mask3, -1, d_filter2)
                m[m > 0] = 1
                m = m - mask3
                direct[m == 1, 1] = 1

                m = cv2.filter2D(mask3, -1, d_filter3)
                m[m > 0] = 1
                m = m - mask3
                direct[m == 1, 2] = 1

                m = cv2.filter2D(mask3, -1, d_filter4)
                m[m > 0] = 1
                m = m - mask3
                direct[m == 1, 3] = 1

                mask3 = mask3_

        abs_pos = pos.copy()
        rel_pos = pos / (str_size / 2)  # to 0~1 maybe larger than 1
        rel_pos = (rel_pos * pos_num).astype(np.int32)
        rel_pos = np.clip(rel_pos, 0, pos_num - 1)

        if ori_w != w or ori_h != h:
            rel_pos = cv2.resize(rel_pos, (ori_w, ori_h), interpolation=cv2.INTER_NEAREST)
            rel_pos[ori_mask == 0] = 0
            direct = cv2.resize(direct, (ori_w, ori_h), interpolation=cv2.INTER_NEAREST)
            direct[ori_mask == 0, :] = 0

        return rel_pos, abs_pos, direct


def load_lama_mpe(model_path, device, use_mpe: bool = True, large_arch: bool = False) -> LamaFourier:
    model = LamaFourier(build_discriminator=False, use_mpe=use_mpe, large_arch=large_arch)
    sd = torch.load(model_path, map_location='cpu', weights_only=False)
    model.generator.load_state_dict(sd['gen_state_dict'])
    if use_mpe:
        model.mpe.load_state_dict(sd['str_state_dict'])
    model.eval()
    
    # 使用 to_empty() 来避免 meta tensor 错误
    if device != 'cpu':
        try:
            # 先尝试直接移动
            model.to(device)
        except NotImplementedError as e:
            if 'meta tensor' in str(e):
                # 如果遇到 meta tensor 错误，使用 to_empty()
                model.generator = model.generator.to_empty(device=device)
                model.generator.load_state_dict(sd['gen_state_dict'])
                if use_mpe and model.mpe is not None:
                    model.mpe = model.mpe.to_empty(device=device)
                    model.mpe.load_state_dict(sd['str_state_dict'])
            else:
                raise
    
    return model


