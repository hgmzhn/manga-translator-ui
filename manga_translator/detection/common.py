from abc import abstractmethod
from typing import List, Tuple

import cv2
import numpy as np

from ..utils import InfererModule, ModelWrapper, Quadrilateral, build_det_rearrange_plan


class CommonDetector(InfererModule):
    supports_detection_rearrange = False

    async def detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                     verbose: bool = False, min_box_area_ratio: float = 0.0009, result_path_fn=None,
                     det_rearrange_min_effective_short_side: float = 341.0):
        '''
        Returns textblock list and text mask.
        '''

        # Apply filters
        img_h, img_w = image.shape[:2]
        minimum_image_size = 400
        rearrange_before_border = self._should_rearrange_before_border(
            image,
            detect_size,
            det_rearrange_min_effective_short_side,
        )
        # Automatically add border if image too small (instead of simply resizing due to them more likely containing large fonts)
        add_border = min(img_w, img_h) < minimum_image_size and not rearrange_before_border
        if add_border:
            self.logger.debug('Adding border')
            image = self._add_border(image, minimum_image_size)

        # Run detection
        textlines, raw_mask, mask = await self._detect(
            image,
            detect_size,
            text_threshold,
            box_threshold,
            unclip_ratio,
            verbose,
            result_path_fn,
            det_rearrange_min_effective_short_side,
        )
        # 面积过滤已移至文本行合并后进行（基于合并后的大框）

        # Remove filters
        if add_border:
            textlines, raw_mask, mask = self._remove_border(image, img_w, img_h, textlines, raw_mask, mask)

        return textlines, raw_mask, mask

    def _should_rearrange_before_border(
        self,
        image: np.ndarray,
        detect_size: int,
        det_rearrange_min_effective_short_side: float = 341.0,
    ) -> bool:
        if not self.supports_detection_rearrange:
            return False
        return build_det_rearrange_plan(
            image,
            tgt_size=self._get_rearrange_target_size(detect_size),
            min_effective_short_side=det_rearrange_min_effective_short_side,
        ) is not None

    def _get_rearrange_target_size(self, detect_size: int) -> int:
        return detect_size

    @abstractmethod
    async def _detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                      unclip_ratio: float, verbose: bool = False, result_path_fn=None,
                      det_rearrange_min_effective_short_side: float = 341.0) -> Tuple[List[Quadrilateral], np.ndarray, np.ndarray]:
        pass

    def _add_border(self, image: np.ndarray, target_side_length: int):
        old_h, old_w = image.shape[:2]
        new_w = new_h = max(old_w, old_h, target_side_length)
        new_image = np.zeros([new_h, new_w, 3]).astype(np.uint8)
        # new_image[:] = np.array([255, 255, 255], np.uint8)
        x, y = 0, 0
        # x, y = (new_h - old_h) // 2, (new_w - old_w) // 2
        new_image[y:y+old_h, x:x+old_w] = image
        return new_image

    def _remove_border(self, image: np.ndarray, old_w: int, old_h: int, textlines: List[Quadrilateral], raw_mask, mask):
        new_h, new_w = image.shape[:2]
        raw_mask = self._resize_and_crop_mask(raw_mask, new_w, new_h, old_w, old_h)
        mask = self._resize_and_crop_mask(mask, new_w, new_h, old_w, old_h)

        # Filter out regions within the border and clamp the points of the remaining regions
        new_textlines = []
        for txtln in textlines:
            if txtln.xyxy[0] >= old_w and txtln.xyxy[1] >= old_h:
                continue
            points = txtln.pts
            points[:,0] = np.clip(points[:,0], 0, old_w)
            points[:,1] = np.clip(points[:,1], 0, old_h)
            new_txtln = Quadrilateral(points, txtln.text, txtln.prob)
            # 保留自定义属性（如 det_label, yolo_label, is_yolo_box）
            for attr in ('det_label', 'yolo_label', 'is_yolo_box'):
                if hasattr(txtln, attr):
                    setattr(new_txtln, attr, getattr(txtln, attr))
            new_textlines.append(new_txtln)
        return new_textlines, raw_mask, mask

    def _resize_and_crop_mask(self, mask, resize_w: int, resize_h: int, crop_w: int, crop_h: int):
        if mask is None:
            return None

        if isinstance(mask, tuple):
            return tuple(self._resize_and_crop_mask(m, resize_w, resize_h, crop_w, crop_h) for m in mask)

        if isinstance(mask, list):
            return [self._resize_and_crop_mask(m, resize_w, resize_h, crop_w, crop_h) for m in mask]

        if not isinstance(mask, np.ndarray):
            self.logger.warning(f'Unexpected mask type in _remove_border: {type(mask)}')
            return mask

        if mask.size == 0 or len(mask.shape) < 2:
            self.logger.warning(f'Invalid mask shape in _remove_border: {mask.shape}')
            return mask

        resized = cv2.resize(mask, (resize_w, resize_h), interpolation=cv2.INTER_LINEAR)
        return resized[:crop_h, :crop_w]

class OfflineDetector(CommonDetector, ModelWrapper):
    _MODEL_SUB_DIR = 'detection'

    async def _detect(self, *args, **kwargs):
        return await self.infer(*args, **kwargs)

    @abstractmethod
    async def _infer(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                       unclip_ratio: float, verbose: bool = False, result_path_fn=None,
                       det_rearrange_min_effective_short_side: float = 341.0):
        pass
