import cv2
import numpy as np

from ..config import InpainterConfig
from .common import CommonInpainter


class BlurInpainter(CommonInpainter):
    """
    毛玻璃高斯模糊修复器 (Frosted Glass / Gaussian Blur Inpainter).
    专为彩色漫画、插画与游戏 CG 设计：
    不进行任何可能产生伪影的 AI 结构脑补，而是对文字蒙版区域进行柔和毛玻璃模糊，
    既彻底消除原文字笔画，又完整保留背景的光影色彩与环境氛围。
    """

    async def _inpaint(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        config: InpainterConfig,
        inpainting_size: int = 1024,
        verbose: bool = False,
    ) -> np.ndarray:
        blur_radius = getattr(config, 'blur_radius', 0) if config else 0
        return self.apply_blur(image, mask, blur_radius=blur_radius)

    @staticmethod
    def _merge_overlapping_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        """合并所有重叠或相交的矩形窗口 [x1, y1, x2, y2]，保证各窗口互斥无交集"""
        if not boxes:
            return []

        result = [list(b) for b in boxes]
        merged_any = True
        while merged_any:
            merged_any = False
            new_result = []
            while result:
                curr = result.pop(0)
                merged = False
                for i, other in enumerate(result):
                    if not (curr[2] < other[0] or other[2] < curr[0] or curr[3] < other[1] or other[3] < curr[1]):
                        result[i] = [
                            min(curr[0], other[0]),
                            min(curr[1], other[1]),
                            max(curr[2], other[2]),
                            max(curr[3], other[3]),
                        ]
                        merged = True
                        merged_any = True
                        break
                if not merged:
                    new_result.append(curr)
            result = new_result

        return [(b[0], b[1], b[2], b[3]) for b in result]

    @staticmethod
    def apply_blur(image: np.ndarray, mask: np.ndarray, blur_radius: int = 0) -> np.ndarray:
        if mask is None or not np.any(mask > 0):
            return np.copy(image)

        # 确保蒙版为单通道 uint8
        if mask.ndim == 3:
            mask_gray = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        else:
            mask_gray = mask
        mask_binary = np.where(mask_gray > 0, 255, 0).astype(np.uint8)

        h, w = image.shape[:2]

        # 1. 计算模糊核尺寸：若指定 blur_radius > 0 则按指定值（限制在 0~200），否则根据图像短边自适应
        if blur_radius and int(blur_radius) > 0:
            ksize = max(3, min(200, int(blur_radius)))
        else:
            ksize = max(21, int(min(h, w) * 0.025))
        if ksize % 2 == 0:
            ksize += 1

        # 2. 蒙版微弱膨胀，确保覆盖文字边缘与抗锯齿
        dilate_k = max(3, int(min(h, w) * 0.003)) | 1
        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_k, dilate_k))
        mask_dilated = cv2.dilate(mask_binary, kernel_dilate, iterations=1)

        # 3. 局部 Telea 预填充，消除深色高对比文字笔画（避免深色文字在高斯模糊后晕染成黑灰色脏斑）
        contours, _ = cv2.findContours(mask_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return np.copy(image)

        pad = max(10, ksize)
        raw_boxes = []
        for cnt in contours:
            bx, by, bw, bh = cv2.boundingRect(cnt)
            raw_boxes.append((
                max(0, bx - pad),
                max(0, by - pad),
                min(w, bx + bw + pad),
                min(h, by + bh + pad),
            ))

        merged_rois = BlurInpainter._merge_overlapping_boxes(raw_boxes)

        inpainted_base = np.copy(image)
        for x1, y1, x2, y2 in merged_rois:
            roi_mask = mask_dilated[y1:y2, x1:x2]
            if np.any(roi_mask > 0):
                roi_img = image[y1:y2, x1:x2]
                roi_telea = cv2.inpaint(roi_img, roi_mask, 3, cv2.INPAINT_TELEA)
                inpainted_base[y1:y2, x1:x2] = roi_telea

        # 4. 对预填充底图进行高斯模糊，形成均匀丝滑的毛玻璃
        blurred = cv2.GaussianBlur(inpainted_base, (ksize, ksize), 0)

        # 5. 蒙版边缘羽化，使毛玻璃边缘柔和融入原背景，避免生硬接缝
        feather_k = max(7, (ksize // 3) | 1)
        alpha = cv2.GaussianBlur(mask_dilated.astype(np.float32) / 255.0, (feather_k, feather_k), 0)
        alpha = np.clip(alpha[:, :, None], 0.0, 1.0)

        # 6. 线性插值合成
        result = np.clip(
            image.astype(np.float32) * (1.0 - alpha) + blurred.astype(np.float32) * alpha,
            0,
            255,
        ).astype(np.uint8)

        return result
