import os
from abc import abstractmethod

import numpy as np

from ..config import InpainterConfig
from ..utils import InfererModule, ModelWrapper


class CommonInpainter(InfererModule):

    async def inpaint(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        return await self._inpaint(image, mask, config, inpainting_size, verbose)

    @abstractmethod
    async def _inpaint(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        pass

class OfflineInpainter(CommonInpainter, ModelWrapper):
    _MODEL_SUB_DIR = 'inpainting'

    async def _inpaint(self, *args, **kwargs):
        return await self.infer(*args, **kwargs)

    @abstractmethod
    async def _infer(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        pass

    def _get_inpaint_canvas_hw(self, h: int, w: int, base_align: int = 8) -> tuple[int, int]:
        """
        Normalize inpainting model input shape on GPU to reduce dynamic-shape plan growth.
        Env vars:
        - MANGA_INPAINT_FIXED_SIZE: force min H/W when > 0
        """
        base_align = max(1, int(base_align))
        h = base_align * ((int(h) + base_align - 1) // base_align)
        w = base_align * ((int(w) + base_align - 1) // base_align)

        if not (hasattr(self, 'device') and isinstance(self.device, str) and self.device.startswith('cuda')):
            return h, w

        fixed_size = int(os.environ.get("MANGA_INPAINT_FIXED_SIZE", "0") or 0)
        if fixed_size > 0:
            return max(h, fixed_size), max(w, fixed_size)

        return h, w


