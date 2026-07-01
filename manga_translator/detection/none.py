import numpy as np

from .common import CommonDetector


class NoneDetector(CommonDetector):
    async def _detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                      verbose: bool = False, result_path_fn=None, det_rearrange_min_effective_short_side: float = 341.0):
        return [], np.zeros(image.shape), None
