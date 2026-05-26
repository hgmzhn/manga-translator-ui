import os

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.transforms import ToTensor

from .common import OfflineColorizer
from .manga_colorization_v2_utils.denoising.denoiser import FFDNetDenoiser
from .manga_colorization_v2_utils.networks.models import Colorizer
from .manga_colorization_v2_utils.utils.utils import resize_pad


# https://github.com/qweasdd/manga-colorization-v2
class MangaColorizationV2(OfflineColorizer):
    _MODEL_SUB_DIR = os.path.join(OfflineColorizer._MODEL_SUB_DIR, 'manga-colorization-v2')
    _MODEL_MAPPING = {
        # Models were in google drive so had to upload to github
        'generator': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/manga-colorization-v2-generator.zip',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/manga-colorization-v2-generator.zip',
            ],
            'file': 'generator.zip',
            'hash': '087e6a0bc02770e732a52f33878b71a272a6123c9ac649e9b5bfb75e39e5c1d5',
        },
        'denoiser': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/manga-colorization-v2-net_rgb.pth',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/manga-colorization-v2-net_rgb.pth',
            ],
            'file': 'net_rgb.pth',
            'hash': '0fe98bfd2ac870b15f360661b1c4789eecefc6dc2e4462842a0dd15e149a0433',
        }
    }

    async def _load(self, device: str):
        self.device = device
        self.torch_device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        self.colorizer = Colorizer().to(self.torch_device)
        self.colorizer.generator.load_state_dict(
            torch.load(self._get_file_path('generator.zip'), map_location='cpu'))
        self.colorizer = self.colorizer.eval()
        # FFDNetDenoiser 内部使用 DataParallel/map_location 写死 cuda 分支,
        # DirectML 不兼容,降噪开销很小,直接回退到 CPU。
        denoiser_device = 'cpu' if device == 'dml' else device
        self.denoiser = FFDNetDenoiser(denoiser_device, _weights_dir=self.model_dir)

    async def _unload(self):
        del self.colorizer
        del self.denoiser

    async def _infer(self, image: Image.Image, colorization_size: int, denoise_sigma=25, **kwargs) -> Image.Image:
        # Size has to be multiple of 32
        img = np.array(image.convert('RGBA'))
        original_size = image.size  # 保存原始尺寸 (width, height)
        max_size = min(*img.shape[:2])
        max_size -= max_size % 32
        if colorization_size > 0:
            size = min(max_size, colorization_size - (colorization_size % 32))
        else:
            # size<=576 gives best results
            size = min(max_size, 576)

        # Denoising前先保存原始img尺寸，因为denoiser可能会改变尺寸
        img_shape_before_denoise = img.shape[:2]
        
        if 0 <= denoise_sigma and denoise_sigma <= 255:
            img = self.denoiser.get_denoised_image(img, sigma=denoise_sigma)
        
        # 如果denoiser改变了尺寸，恢复回去
        if img.shape[:2] != img_shape_before_denoise:
            # denoiser可能缩小了图片，需要恢复
            # 注意：cv2.resize的参数是 (width, height)，而shape是 (height, width, channels)
            img = cv2.resize(img, (img_shape_before_denoise[1], img_shape_before_denoise[0]), interpolation=cv2.INTER_LINEAR)

        img, current_pad = resize_pad(img, size)
        extra_pad = (0, 0)
        if isinstance(self.device, str) and self.device.startswith('cuda'):
            h, w = img.shape[:2]
            fixed_size = int(os.environ.get("MANGA_COLORIZATION_FIXED_SIZE", "0") or 0)
            if fixed_size > 0:
                target_h = max(h, fixed_size)
                target_w = max(w, fixed_size)
            else:
                bucket = int(os.environ.get("MANGA_COLORIZATION_SIZE_BUCKET", "128") or 128)
                if bucket > 1:
                    target_h = bucket * ((h + bucket - 1) // bucket)
                    target_w = bucket * ((w + bucket - 1) // bucket)
                else:
                    target_h, target_w = h, w
            extra_pad = (max(0, target_h - h), max(0, target_w - w))
            if extra_pad[0] or extra_pad[1]:
                img = np.pad(img, ((0, extra_pad[0]), (0, extra_pad[1]), (0, 0)), 'maximum')

        transform = ToTensor()
        current_image = transform(img).unsqueeze(0).to(self.torch_device)
        current_hint = torch.zeros(1, 4, current_image.shape[2], current_image.shape[3]).float().to(self.torch_device)

        with torch.no_grad():
            fake_color, _ = self.colorizer(torch.cat([current_image, current_hint], 1))
            fake_color = fake_color.detach()

        result = fake_color[0].detach().cpu().permute(1, 2, 0) * 0.5 + 0.5

        if extra_pad[0] != 0:
            result = result[:-extra_pad[0]]
        if extra_pad[1] != 0:
            result = result[:, :-extra_pad[1]]
        if current_pad[0] != 0:
            result = result[:-current_pad[0]]
        if current_pad[1] != 0:
            result = result[:, :-current_pad[1]]

        colored_image = result.numpy() * 255
        colored_img_pil = Image.fromarray(colored_image.astype(np.uint8))
        
        # 恢复到原始尺寸
        if colored_img_pil.size != original_size:
            colored_img_pil = colored_img_pil.resize(original_size, Image.Resampling.LANCZOS)
        
        return colored_img_pil
