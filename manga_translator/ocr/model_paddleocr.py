# Copyright (c) 2025 PaddlePaddle Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0

"""
PP-OCRv5 ONNX Adapter for Manga Translator

Uses PP-OCRv5 recognition models via ONNX Runtime (no PaddlePaddle dependency).
Text detection is handled by manga-translator's own detection modules.

Supports:
- Chinese/Japanese/English (ch_PP-OCRv5_rec_server_infer)
- Korean/English (korean_PP-OCRv5_rec_mobile_infer)
"""

import os
import numpy as np
from typing import List
import cv2
import math

from .common import OfflineOCR
from ..config import OcrConfig
from ..utils import Quadrilateral


class ModelPaddleOCR(OfflineOCR):
    """
    PP-OCRv5 ONNX text recognition for manga-image-translator.

    Supports Chinese, Japanese, Korean, and English text recognition.
    """

    # Use BASE_PATH for consistency with other models and PyInstaller compatibility
    # _MODEL_DIR and _MODEL_SUB_DIR are inherited from ModelWrapper and OfflineOCR
    # Final path: BASE_PATH/models/ocr

    _MODELS = {
        'ch': {  # Chinese/Japanese/English
            'onnx': 'ch_PP-OCRv5_rec_server_infer.onnx',
            'dict': 'ppocrv5_dict.txt',
        },
        'korean': {  # Korean/English
            'onnx': 'korean_PP-OCRv5_rec_mobile_infer.onnx',
            'dict': 'ppocrv5_korean_dict.txt',
        }
    }

    def __init__(self, model_type='ch', *args, **kwargs):
        """
        Args:
            model_type: 'ch' for Chinese/Japanese/English, 'korean' for Korean/English
        """
        super().__init__(*args, **kwargs)
        self.model_type = model_type
        self.session = None
        self.char_dict = None
        self.device = 'cpu'

    async def _load(self, device: str):
        """Load PP-OCRv5 ONNX model"""
        from ..utils.backend import create_onnx_session

        self.device = device
        model_config = self._MODELS[self.model_type]

        # Load model using inherited model_dir property (PyInstaller-compatible)
        model_path = self._get_file_path(model_config['onnx'])
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")

        # Load dictionary
        dict_path = self._get_file_path(model_config['dict'])
        if not os.path.exists(dict_path):
            raise FileNotFoundError(f"Dictionary not found: {dict_path}")

        with open(dict_path, 'r', encoding='utf-8') as f:
            self.char_dict = ['<blank>'] + [line.strip() for line in f]

        # Create ONNX session with backend auto-selection (prefer DML on Windows; fallback to CPU)
        prefer = 'cuda' if str(device).startswith('cuda') else 'auto'
        self.session, selected_backend = create_onnx_session(
            model_path,
            logger=self.logger,
            prefer=prefer,
            device_hint=device,
        )

        self.logger.info(
            f"PP-OCRv5 ONNX loaded: {model_config['onnx']} ({len(self.char_dict)} chars, device={device}, provider={selected_backend})"
        )

    async def _unload(self):
        """Unload model"""
        if self.session is not None:
            del self.session
            self.session = None
        self.char_dict = None

    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral],
                     config: OcrConfig, verbose: bool = False) -> List[Quadrilateral]:
        """
        Perform OCR on detected text regions.

        Args:
            image: RGB image
            textlines: Detected text regions
            config: OCR configuration
            verbose: Verbose logging
        """
        if self.session is None:
            self.logger.error("Model not loaded")
            return textlines

        threshold = 0.2 if config.prob is None else config.prob

        # Extract and preprocess regions
        regions = []
        valid_indices = []

        # Prepare debug output directory if verbose
        if verbose:
            ocr_result_dir = os.environ.get('MANGA_OCR_RESULT_DIR', 'result/ocrs/')
            os.makedirs(ocr_result_dir, exist_ok=True)

        for i, textline in enumerate(textlines):
            try:
                pts = textline.pts

                # Use perspective transform to extract rotated text regions
                # This handles tilted text and automatically rotates vertical text
                region = self._get_rotate_crop_image(image, pts)

                if region is None or region.size == 0:
                    continue

                # Convert RGB to BGR
                if len(region.shape) == 3 and region.shape[2] == 3:
                    region_bgr = cv2.cvtColor(region, cv2.COLOR_RGB2BGR)
                else:
                    region_bgr = region

                # Save debug image if verbose
                if verbose:
                    from ..utils import imwrite_unicode
                    img_data = region_bgr.copy()

                    # Limit OCR debug image max size to 200 pixels
                    max_ocr_size = 200
                    height, width = img_data.shape[:2]
                    if max(height, width) > max_ocr_size:
                        scale = max_ocr_size / max(height, width)
                        new_width = int(width * scale)
                        new_height = int(height * scale)
                        img_data = cv2.resize(img_data, (new_width, new_height), interpolation=cv2.INTER_AREA)

                    # Use high compression for saving
                    compression_params = [cv2.IMWRITE_PNG_COMPRESSION, 9]
                    imwrite_unicode(os.path.join(ocr_result_dir, f'{i}.png'), img_data, compression_params)

                regions.append(region_bgr)
                valid_indices.append(i)

            except Exception as e:
                self.logger.warning(f"Failed to extract region {i}: {e}")
                continue

        # Batch inference
        if regions:
            try:
                preprocessed = [self._preprocess(r) for r in regions]
                batch = np.concatenate(preprocessed, axis=0)

                # Run inference
                input_name = self.session.get_inputs()[0].name
                outputs = self.session.run(None, {input_name: batch})
                predictions = outputs[0]  # [batch, seq_len, num_classes]

                # Decode predictions
                for idx, pred in zip(valid_indices, predictions):
                    text, confidence = self._decode_ctc(pred)

                    if confidence < threshold:
                        if verbose:
                            self.logger.info(f"Skipped '{text}' (conf: {confidence:.3f})")
                        continue

                    textline = textlines[idx]
                    textline.text = text
                    textline.prob = confidence

                    # Estimate colors
                    region_idx = valid_indices.index(idx)
                    self._estimate_colors(regions[region_idx], textline)

                    if verbose:
                        self.logger.info(f'prob: {confidence:.3f} {text} fg: ({textline.fg_r}, {textline.fg_g}, {textline.fg_b}) bg: ({textline.bg_r}, {textline.bg_g}, {textline.bg_b})')

            except Exception as e:
                self.logger.error(f"Inference failed: {e}")

        return textlines

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        """
        Preprocess image for PP-OCRv5 recognition.

        Input: BGR image [H, W, 3]
        Output: Normalized tensor [1, 3, 48, W']
        """
        h, w = img.shape[:2]
        imgC, imgH, imgW = 3, 48, 320

        # Resize keeping aspect ratio
        ratio = w / float(h)
        resized_w = int(math.ceil(imgH * ratio))
        if resized_w > imgW:
            resized_w = imgW

        resized_img = cv2.resize(img, (resized_w, imgH))

        # Normalize: (img/255 - 0.5) / 0.5 => range [-1, 1]
        resized_img = resized_img.astype(np.float32)
        resized_img = resized_img.transpose(2, 0, 1)  # HWC -> CHW
        resized_img = resized_img / 255.0
        resized_img = (resized_img - 0.5) / 0.5

        # Pad to fixed width
        padded = np.zeros((imgC, imgH, imgW), dtype=np.float32)
        padded[:, :, :resized_w] = resized_img

        return padded[np.newaxis, :]  # Add batch dimension

    def _decode_ctc(self, pred: np.ndarray):
        """
        Decode CTC prediction to text.

        Args:
            pred: [seq_len, num_classes]

        Returns:
            (text, confidence)
        """
        # Get most probable character at each time step
        indices = np.argmax(pred, axis=1)
        confidences = np.max(pred, axis=1)

        # Remove blanks and duplicates
        chars = []
        prev_idx = -1

        for idx in indices:
            if idx != 0 and idx != prev_idx:  # 0 is <blank>
                if idx < len(self.char_dict):
                    chars.append(self.char_dict[idx])
            prev_idx = idx

        text = ''.join(chars)
        confidence = float(np.mean(confidences))

        return text, confidence

    def _estimate_colors(self, region: np.ndarray, textline: Quadrilateral):
        """Estimate foreground/background colors using improved Otsu thresholding"""
        try:
            if len(region.shape) == 3:
                gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            else:
                gray = region

            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            white_pixels = np.sum(binary == 255)
            black_pixels = np.sum(binary == 0)

            if white_pixels < black_pixels:
                fg_mask = binary == 255
                bg_mask = binary == 0
            else:
                fg_mask = binary == 0
                bg_mask = binary == 255

            if len(region.shape) == 3 and region.shape[2] == 3:
                # BGR to RGB
                region_rgb = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)

                if np.any(fg_mask):
                    fg_pixels = region_rgb[fg_mask]

                    # 改进：使用中位数代替平均值，减少抗锯齿像素的影响
                    fg_r = int(np.median(fg_pixels[:, 0]))
                    fg_g = int(np.median(fg_pixels[:, 1]))
                    fg_b = int(np.median(fg_pixels[:, 2]))

                    # 颜色量化：如果接近黑色（RGB < 40），强制设为纯黑
                    if fg_r < 40 and fg_g < 40 and fg_b < 40:
                        textline.fg_r = textline.fg_g = textline.fg_b = 0
                    # 如果接近白色（RGB > 215），强制设为纯白
                    elif fg_r > 215 and fg_g > 215 and fg_b > 215:
                        textline.fg_r = textline.fg_g = textline.fg_b = 255
                    else:
                        textline.fg_r = fg_r
                        textline.fg_g = fg_g
                        textline.fg_b = fg_b
                else:
                    textline.fg_r = textline.fg_g = textline.fg_b = 0

                if np.any(bg_mask):
                    bg_pixels = region_rgb[bg_mask]

                    # 改进：使用中位数代替平均值
                    bg_r = int(np.median(bg_pixels[:, 0]))
                    bg_g = int(np.median(bg_pixels[:, 1]))
                    bg_b = int(np.median(bg_pixels[:, 2]))

                    # 颜色量化：如果接近白色（RGB > 215），强制设为纯白
                    if bg_r > 215 and bg_g > 215 and bg_b > 215:
                        textline.bg_r = textline.bg_g = textline.bg_b = 255
                    # 如果接近黑色（RGB < 40），强制设为纯黑
                    elif bg_r < 40 and bg_g < 40 and bg_b < 40:
                        textline.bg_r = textline.bg_g = textline.bg_b = 0
                    else:
                        textline.bg_r = bg_r
                        textline.bg_g = bg_g
                        textline.bg_b = bg_b
                else:
                    textline.bg_r = textline.bg_g = textline.bg_b = 255
            else:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255

        except Exception as e:
            textline.fg_r = textline.fg_g = textline.fg_b = 0
            textline.bg_r = textline.bg_g = textline.bg_b = 255
            self.logger.debug(f"Color estimation failed: {e}")

    def _get_rotate_crop_image(self, img: np.ndarray, points: np.ndarray) -> np.ndarray:
        """
        Extract and rotate text region using perspective transform.
        Based on PaddleOCR's get_rotate_crop_image implementation.

        Automatically rotates vertical text (height/width >= 1.5) to horizontal.

        Args:
            img: RGB image
            points: 4 corner points of text region [4, 2]

        Returns:
            Cropped and rotated BGR image
        """
        try:
            assert len(points) == 4, "points must have 4 corners"

            # Calculate crop dimensions based on edge lengths
            img_crop_width = int(max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3])
            ))
            img_crop_height = int(max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2])
            ))

            # Prevent invalid dimensions
            if img_crop_width <= 0 or img_crop_height <= 0:
                return None

            # Define target rectangle
            pts_std = np.float32([
                [0, 0],
                [img_crop_width, 0],
                [img_crop_width, img_crop_height],
                [0, img_crop_height],
            ])

            # Perspective transform
            M = cv2.getPerspectiveTransform(points.astype(np.float32), pts_std)
            dst_img = cv2.warpPerspective(
                img,
                M,
                (img_crop_width, img_crop_height),
                borderMode=cv2.BORDER_REPLICATE,
                flags=cv2.INTER_CUBIC,
            )

            dst_img_height, dst_img_width = dst_img.shape[0:2]

            # Rotate vertical text (height/width >= 1.5) to horizontal
            if dst_img_height * 1.0 / dst_img_width >= 1.5:
                dst_img = np.rot90(dst_img)

            return dst_img

        except Exception as e:
            self.logger.warning(f"Failed to extract rotated crop: {e}")
            return None


# Alias for backward compatibility
class ModelPaddleOCRChinese(ModelPaddleOCR):
    """Chinese/Japanese/English OCR"""
    def __init__(self, *args, **kwargs):
        super().__init__(model_type='ch', *args, **kwargs)


class ModelPaddleOCRKorean(ModelPaddleOCR):
    """Korean/English OCR"""
    def __init__(self, *args, **kwargs):
        super().__init__(model_type='korean', *args, **kwargs)
