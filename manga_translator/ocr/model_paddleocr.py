# Copyright (c) 2025 PaddlePaddle Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0

"""
PP-OCR ONNX Adapter for Manga Translator

Uses PP-OCR recognition models via ONNX Runtime (no PaddlePaddle dependency).
Text detection is handled by manga-translator's own detection modules.

Supports:
- Multilingual PP-OCRv6 (PP-OCRv6_medium_rec)
- Korean/English (korean_PP-OCRv5_rec_mobile_infer)
"""

import math
import os
from typing import List

import cv2
import einops
import numpy as np
import torch

from ..config import OcrConfig
from ..utils import Quadrilateral
from ..utils.onnx_runtime import (
    create_inference_session,
    create_session_options,
    import_onnxruntime,
)
from .common import OfflineOCR


class ModelPaddleOCR(OfflineOCR):
    """
    PP-OCR ONNX text recognition for manga-image-translator.

    Supports PP-OCRv6 multilingual recognition plus legacy PP-OCRv5
    Korean/Latin/Thai variants.
    """

    # Use BASE_PATH for consistency with other models and PyInstaller compatibility
    # _MODEL_DIR and _MODEL_SUB_DIR are inherited from ModelWrapper and OfflineOCR
    # Final path: BASE_PATH/models/ocr

    # Model mapping for unified model management
    _MODEL_MAPPING = {
        'ch_onnx': {
            'url': [
                'https://www.modelscope.cn/models/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/master/inference.onnx',
            ],
            'hash': '9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba',
            'file': 'PP-OCRv6_medium_rec.onnx',
        },
        'ch_config': {
            'url': [
                'https://www.modelscope.cn/models/PaddlePaddle/PP-OCRv6_medium_rec_onnx/resolve/master/inference.yml',
            ],
            'hash': '991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129',
            'file': 'PP-OCRv6_medium_rec.yml',
        },
        'korean_onnx': {
            'url': [
                'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/korean_PP-OCRv5_rec_mobile_infer.onnx',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/korean_PP-OCRv5_rec_mobile_infer.onnx',
            ],
            'hash': 'cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b',
            'file': '.',
        },
        'korean_dict': {
            'url': [
                'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/ppocrv5_korean_dict.txt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ppocrv5_korean_dict.txt',
            ],
            'hash': 'a88071c68c01707489baa79ebe0405b7beb5cca229f4fc94cc3ef992328802d7',
            'file': '.',
        },
        'latin_onnx': {
            'url': [
                'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.8.0/latin_PP-OCRv5_rec_mobile_infer.onnx',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/latin_PP-OCRv5_rec_mobile_infer.onnx',
            ],
            'hash': '614ffc2d6d3902d360fad7f1b0dd455ee45e877069d14c4e51a99dc4ef144409',
            'file': '.',
        },
        'latin_dict': {
            'url': [
                'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.8.0/ppocrv5_latin_dict.txt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ppocrv5_latin_dict.txt',
            ],
            'hash': '3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff',
            'file': '.',
        },
        'thai_onnx': {
            'url': [
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/thai_PP-OCRv5_rec_mobile_infer.onnx',
            ],
            'hash': '2b6e56b1872200349e227574c25aeb0e0f9af9b8356e9ff5f75ac543a535669a',
            'file': '.',
        },
        'thai_dict': {
            'url': [
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ppocrv5_thai_dict.txt',
            ],
            'hash': '57f5406f94bb6688fb7077f7be65f08bbd71cecf48c01ea26c522cb5c4836b7a',
            'file': '.',
        },
        # 48px 模型用于颜色预测
        'model_48px': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/ocr_ar_48px.ckpt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ocr_ar_48px.ckpt',
            ],
            'hash': '29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d',
        },
        'dict_48px': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/alphabet-all-v7.txt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/alphabet-all-v7.txt',
            ],
            'hash': 'f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a',
        }
    }

    _MODELS = {
        'ch': {  # PP-OCRv6 multilingual
            'onnx': 'PP-OCRv6_medium_rec.onnx',
            'yml': 'PP-OCRv6_medium_rec.yml',
            'name': 'PP-OCRv6_medium_rec',
        },
        'korean': {  # Korean/English
            'onnx': 'korean_PP-OCRv5_rec_mobile_infer.onnx',
            'dict': 'ppocrv5_korean_dict.txt',
            'name': 'korean_PP-OCRv5_rec_mobile_infer',
        },
        'latin': {  # Latin alphabet languages (English, Spanish, etc.)
            'onnx': 'latin_PP-OCRv5_rec_mobile_infer.onnx',
            'dict': 'ppocrv5_latin_dict.txt',
            'name': 'latin_PP-OCRv5_rec_mobile_infer',
        },
        'thai': {  # Thai
            'onnx': 'thai_PP-OCRv5_rec_mobile_infer.onnx',
            'dict': 'ppocrv5_thai_dict.txt',
            'name': 'thai_PP-OCRv5_rec_mobile_infer',
        }
    }
    _MODEL_MAPPING_KEYS = {
        'ch': ('ch_onnx', 'ch_config'),
        'korean': ('korean_onnx', 'korean_dict'),
        'latin': ('latin_onnx', 'latin_dict'),
        'thai': ('thai_onnx', 'thai_dict'),
    }
    _COMMON_MODEL_MAPPING_KEYS = ('model_48px', 'dict_48px')

    def __init__(self, model_type='ch', *args, **kwargs):
        """
        Args:
            model_type: 'ch' for PP-OCRv6 multilingual, 'korean' for Korean/English, 'latin' for Latin/English, 'thai' for Thai
        """
        if model_type not in self._MODELS:
            raise ValueError(f"Unsupported PaddleOCR model type: {model_type}")
        self.model_type = model_type
        all_mappings = self.__class__._MODEL_MAPPING
        keys = self._MODEL_MAPPING_KEYS[model_type] + self._COMMON_MODEL_MAPPING_KEYS
        self._MODEL_MAPPING = {key: all_mappings[key] for key in keys}
        super().__init__(*args, **kwargs)
        self.session = None
        self.char_dict = None
        self.device = 'cpu'
        self.color_model = None  # 48px 模型用于颜色预测
        self.use_gpu = False  # 初始化 use_gpu 标志

    async def _load(self, device: str):
        """Load PP-OCR ONNX model and 48px color prediction model"""
        from .model_48px import OCR

        ort = import_onnxruntime(
            "onnxruntime is required for PaddleOCR ONNX inference. "
            "Install with: pip install onnxruntime-gpu (or onnxruntime)"
        )
        self.device = device
        model_config = self._MODELS[self.model_type]

        # Load model using inherited model_dir property (PyInstaller-compatible)
        model_path = self._get_file_path(model_config['onnx'])
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")

        self.char_dict = self._load_char_dict(model_config)

        # Create ONNX session (official provider format + automatic CUDA->CPU fallback)
        sess_options = create_session_options(ort, log_severity_level=3)
        self.session, ort_device = create_inference_session(
            ort,
            model_path,
            device=device,
            sess_options=sess_options,
            logger=self.logger,
        )
        output_shape = self.session.get_outputs()[0].shape
        output_classes = output_shape[-1] if output_shape else None
        if isinstance(output_classes, int) and output_classes != len(self.char_dict):
            raise ValueError(
                f"PaddleOCR dictionary size mismatch: model outputs {output_classes} "
                f"classes, dictionary has {len(self.char_dict)} entries"
            )

        # 加载 48px 模型用于颜色预测
        try:
            dict_48px_path = self._get_file_path('alphabet-all-v7.txt')
            ckpt_48px_path = self._get_file_path('ocr_ar_48px.ckpt')
            
            if os.path.exists(dict_48px_path) and os.path.exists(ckpt_48px_path):
                with open(dict_48px_path, 'r', encoding='utf-8') as fp:
                    dictionary_48px = [s[:-1] for s in fp.readlines()]
                
                self.color_model = OCR(dictionary_48px, 768)
                sd = torch.load(ckpt_48px_path, map_location='cpu', weights_only=False)
                
                # Handle PyTorch Lightning checkpoint format
                if 'state_dict' in sd:
                    sd = sd['state_dict']
                
                # Remove 'model.' prefix from keys if present
                cleaned_sd = {}
                for k, v in sd.items():
                    if k.startswith('model.'):
                        cleaned_sd[k[6:]] = v
                    else:
                        cleaned_sd[k] = v
                
                self.color_model.load_state_dict(cleaned_sd)
                self.color_model.eval()
                
                if device == 'cuda' or device == 'mps':
                    self.color_model = self.color_model.to(device)
                    self.use_gpu = True
                else:
                    self.use_gpu = False
                
                self.logger.info("48px color prediction model loaded for PaddleOCR")
            else:
                self.logger.warning(f"48px model not found at {dict_48px_path} or {ckpt_48px_path}")
                self.color_model = None
        except Exception as e:
            self.logger.warning(f"Failed to load 48px color model: {e}")
            self.color_model = None

        self.logger.info(
            f"{model_config.get('name', 'PP-OCR')} ONNX loaded: {model_config['onnx']} "
            f"({len(self.char_dict)} chars, device={ort_device})"
        )

    async def _unload(self):
        """Unload model"""
        if self.session is not None:
            del self.session
            self.session = None
        self.char_dict = None
        if self.color_model is not None:
            del self.color_model
            self.color_model = None

    def _load_char_dict(self, model_config: dict) -> List[str]:
        """Load CTC dictionary from Paddle inference yml or legacy dict text."""
        yml_name = model_config.get('yml')
        if yml_name:
            yml_path = self._get_file_path(yml_name)
            if not os.path.exists(yml_path):
                raise FileNotFoundError(f"PaddleOCR config not found: {yml_path}")
            try:
                import yaml
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required to read PaddleOCR inference.yml. "
                    "Install with: pip install pyyaml"
                ) from exc
            with open(yml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            chars = (data.get('PostProcess') or {}).get('character_dict')
            if not isinstance(chars, list) or not chars:
                raise ValueError(f"PaddleOCR character_dict missing in {yml_path}")
            chars = [str(ch) for ch in chars]
            if ' ' not in chars:
                chars.append(' ')
            return ['<blank>'] + chars

        dict_path = self._get_file_path(model_config['dict'])
        if not os.path.exists(dict_path):
            raise FileNotFoundError(f"Dictionary not found: {dict_path}")
        with open(dict_path, 'r', encoding='utf-8') as f:
            chars = [line.strip('\r\n') for line in f]
        if ' ' not in chars:
            chars.append(' ')
        return ['<blank>'] + chars

    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral],
                     config: OcrConfig, verbose: bool = False, q=None) -> List[Quadrilateral]:
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

        ignore_bubble = config.ignore_bubble
        use_model_bubble_filter = bool(getattr(config, 'use_model_bubble_filter', False))
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

                # 使用基类的通用气泡过滤方法 - 缩放到 48px 后再过滤（与其他 OCR 模型一致）
                if ignore_bubble > 0 or use_model_bubble_filter:
                    # 缩放到 48px 高度用于过滤（与 _preprocess 一致）
                    h, w = region.shape[:2]
                    ratio = w / float(h)
                    resized_w = int(math.ceil(48 * ratio))
                    region_48px = cv2.resize(region, (resized_w, 48))
                    if self._should_ignore_region(region_48px, ignore_bubble, image, textline, config):
                        self.logger.info(f'[FILTERED] Region {i} ignored - Non-bubble area detected (ignore_bubble={ignore_bubble}, model_filter={use_model_bubble_filter})')
                        continue

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
                    imwrite_unicode(os.path.join(ocr_result_dir, f'{i}.png'), img_data, self.logger, compression_params)

                regions.append(region_bgr)
                valid_indices.append(i)

            except Exception as e:
                self.logger.warning(f"Failed to extract region {i}: {e}")
                continue

        # Batch inference with chunking (max 16 regions per batch)
        if regions:
            max_chunk_size = 16  # 每批最多处理 16 个文本区域，与其他 OCR 保持一致
            
            try:
                # 分批处理所有区域
                for chunk_start in range(0, len(regions), max_chunk_size):
                    chunk_end = min(chunk_start + max_chunk_size, len(regions))
                    chunk_regions = regions[chunk_start:chunk_end]
                    chunk_indices = valid_indices[chunk_start:chunk_end]
                    
                    # Preprocess and batch
                    preprocessed = [self._preprocess(r) for r in chunk_regions]
                    batch = np.concatenate(preprocessed, axis=0)

                    # Run inference
                    input_name = self.session.get_inputs()[0].name
                    outputs = self.session.run(None, {input_name: batch})
                    predictions = outputs[0]  # [batch, seq_len, num_classes]

                    # Batch color prediction if 48px model is available
                    color_results = None
                    if self.color_model is not None:
                        color_results = self._estimate_colors_batch(chunk_regions)

                    # Decode predictions for this chunk
                    for i, (idx, pred) in enumerate(zip(chunk_indices, predictions)):
                        text, confidence = self._decode_ctc(pred)

                        textline = textlines[idx]
                        
                        if confidence < threshold:
                            self.logger.info(f"[FILTERED] prob: {confidence:.3f} < threshold: {threshold} - Text: \"{text}\"")
                            # Keep the textline with empty text for hybrid OCR to retry
                            textline.text = ''  # Empty text for hybrid OCR
                            textline.prob = confidence
                            textline.fg_r = 0
                            textline.fg_g = 0
                            textline.fg_b = 0
                            textline.bg_r = 255
                            textline.bg_g = 255
                            textline.bg_b = 255
                            continue

                        textline.text = text
                        textline.prob = confidence

                        # Apply batch color prediction results
                        if color_results is not None and i < len(color_results):
                            fr, fg, fb, br, bg, bb = color_results[i]
                            textline.fg_r = fr
                            textline.fg_g = fg
                            textline.fg_b = fb
                            textline.bg_r = br
                            textline.bg_g = bg
                            textline.bg_b = bb
                        else:
                            # Default colors if no color prediction
                            textline.fg_r = textline.fg_g = textline.fg_b = 0
                            textline.bg_r = textline.bg_g = textline.bg_b = 255

                        self.logger.info(f'prob: {confidence:.3f} {text} fg: ({textline.fg_r}, {textline.fg_g}, {textline.fg_b}) bg: ({textline.bg_r}, {textline.bg_g}, {textline.bg_b})')

            except Exception as e:
                self.logger.error(f"Inference failed: {e}")

        # 清理 GPU 显存
        self._cleanup_ocr_memory(force_gpu_cleanup=False)

        return textlines

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        """
        Preprocess image for PP-OCR recognition.

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
        Decode CTC prediction to text with special character handling.

        Args:
            pred: [seq_len, num_classes]

        Returns:
            (text, confidence)
        """
        # Get most probable character at each time step
        indices = np.argmax(pred, axis=1)
        confidences = np.max(pred, axis=1)

        # Match PaddleOCR's CTCLabelDecode confidence: remove CTC blanks
        # and duplicate timesteps before averaging kept character scores.
        selection = np.ones(len(indices), dtype=bool)
        if len(indices) > 1:
            selection[1:] = indices[1:] != indices[:-1]
        selection &= indices != 0  # 0 is <blank>

        chars = []
        kept_confidences = []

        for idx, confidence in zip(indices[selection], confidences[selection]):
            if idx >= len(self.char_dict):
                continue

            ch = self.char_dict[idx]

            # Special character handling (similar to model_48px)
            if ch == '<S>':      # Start token
                continue
            if ch == '</S>':     # End token
                break
            if ch == '<SP>':     # Space token
                ch = ' '

            chars.append(ch)
            kept_confidences.append(confidence)

        text = ''.join(chars)
        confidence = float(np.mean(kept_confidences)) if kept_confidences else 0.0

        return text, confidence

    def _estimate_colors_batch(self, regions: List[np.ndarray]) -> List[tuple]:
        """批量预测前景色和背景色（复用 mocr 的批量处理逻辑）"""
        from ..utils import chunks
        from ..utils.generic import AvgMeter
        
        try:
            if not regions:
                return []
            
            text_height = 48
            max_chunk_size = 16  # 与 mocr 保持一致
            results = [None] * len(regions)
            
            # 分批处理（与 mocr 相同）
            for indices in chunks(range(len(regions)), max_chunk_size):
                N = len(indices)
                
                # 准备批量数据
                widths = []
                resized_regions = []
                
                for idx in indices:
                    region = regions[idx]
                    # 将 BGR 转换为 RGB
                    if len(region.shape) == 3 and region.shape[2] == 3:
                        region_rgb = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)
                    else:
                        region_rgb = region
                    
                    # 调整大小到 48px 高度
                    h, w = region_rgb.shape[:2]
                    ratio = w / float(h)
                    new_w = int(round(ratio * text_height))
                    if new_w == 0:
                        new_w = 1
                    
                    region_resized = cv2.resize(region_rgb, (new_w, text_height), interpolation=cv2.INTER_AREA)
                    resized_regions.append(region_resized)
                    widths.append(new_w)
                
                # 打包成 batch
                max_width = self._get_ocr_canvas_width(widths, base_align=4)
                batch_region = np.zeros((N, text_height, max_width, 3), dtype=np.uint8)
                
                for i, region_resized in enumerate(resized_regions):
                    W = region_resized.shape[1]
                    batch_region[i, :, :W, :] = region_resized
                
                # 转换为 tensor
                image_tensor = (torch.from_numpy(batch_region).float() - 127.5) / 127.5
                image_tensor = einops.rearrange(image_tensor, 'N H W C -> N C H W')
                
                # GPU 加速
                if self.use_gpu:
                    image_tensor = image_tensor.to(self.device)
                
                # 批量推理
                with torch.no_grad():
                    ret = self.color_model.infer_beam_batch_tensor(image_tensor, widths, beams_k=5, max_seq_length=255)
                
                # 处理结果（与 mocr 完全相同的逻辑）
                for i, (pred_chars_index, prob, fg_pred, bg_pred, fg_ind_pred, bg_ind_pred) in enumerate(ret):
                    has_fg = (fg_ind_pred[:, 1] > fg_ind_pred[:, 0])
                    has_bg = (bg_ind_pred[:, 1] > bg_ind_pred[:, 0])
                    
                    fr = AvgMeter()
                    fg = AvgMeter()
                    fb = AvgMeter()
                    br = AvgMeter()
                    bg = AvgMeter()
                    bb = AvgMeter()
                    
                    for chid, c_fg, c_bg, h_fg, h_bg in zip(pred_chars_index, fg_pred, bg_pred, has_fg, has_bg):
                        ch = self.color_model.dictionary[chid]
                        if ch == '<S>':
                            continue
                        if ch == '</S>':
                            break
                        # 处理前景色
                        if h_fg.item():
                            fr(int(c_fg[0] * 255))
                            fg(int(c_fg[1] * 255))
                            fb(int(c_fg[2] * 255))
                        # 处理背景色
                        if h_bg.item():
                            br(int(c_bg[0] * 255))
                            bg(int(c_bg[1] * 255))
                            bb(int(c_bg[2] * 255))
                        else:
                            # 如果没有背景色，使用前景色作为背景色
                            br(int(c_fg[0] * 255))
                            bg(int(c_fg[1] * 255))
                            bb(int(c_fg[2] * 255))
                    
                    fr = min(max(int(fr()), 0), 255)
                    fg = min(max(int(fg()), 0), 255)
                    fb = min(max(int(fb()), 0), 255)
                    br = min(max(int(br()), 0), 255)
                    bg = min(max(int(bg()), 0), 255)
                    bb = min(max(int(bb()), 0), 255)
                    
                    results[indices[i]] = (fr, fg, fb, br, bg, bb)
            
            return results
            
        except Exception as e:
            self.logger.warning(f"Batch color prediction failed: {e}")
            # 返回默认颜色
            return [(0, 0, 0, 255, 255, 255)] * len(regions)

    def _estimate_colors_48px(self, region: np.ndarray, textline: Quadrilateral):
        """使用 48px 模型预测前景色和背景色"""
        from ..utils.generic import AvgMeter
        
        try:
            # 如果 48px 模型未加载，使用默认颜色
            if self.color_model is None:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                return
            
            # 将 BGR 转换为 RGB
            if len(region.shape) == 3 and region.shape[2] == 3:
                region_rgb = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)
            else:
                region_rgb = region
            
            # 调整大小到 48px 高度
            text_height = 48
            h, w = region_rgb.shape[:2]
            ratio = w / float(h)
            new_w = int(round(ratio * text_height))
            
            if new_w == 0:
                new_w = 1
            
            region_resized = cv2.resize(region_rgb, (new_w, text_height), interpolation=cv2.INTER_AREA)
            
            canvas_w = self._get_ocr_canvas_width([new_w], base_align=4)
            batch_region = np.zeros((1, text_height, canvas_w, 3), dtype=np.uint8)
            batch_region[0, :, :new_w, :] = region_resized
            image_tensor = (torch.from_numpy(batch_region).float() - 127.5) / 127.5
            image_tensor = einops.rearrange(image_tensor, 'N H W C -> N C H W')
            
            # GPU 加速
            if self.use_gpu:
                image_tensor = image_tensor.to(self.device)
            
            # 使用 48px 模型推理
            with torch.no_grad():
                ret = self.color_model.infer_beam_batch_tensor(image_tensor, [new_w], beams_k=5, max_seq_length=255)
            
            if ret and len(ret) > 0:
                pred_chars_index, prob, fg_pred, bg_pred, fg_ind_pred, bg_ind_pred = ret[0]
                
                # 计算颜色 - 与 mocr 保持一致的逻辑
                has_fg = (fg_ind_pred[:, 1] > fg_ind_pred[:, 0])
                has_bg = (bg_ind_pred[:, 1] > bg_ind_pred[:, 0])
                
                fr = AvgMeter()
                fg = AvgMeter()
                fb = AvgMeter()
                br = AvgMeter()
                bg = AvgMeter()
                bb = AvgMeter()
                
                for chid, c_fg, c_bg, h_fg, h_bg in zip(pred_chars_index, fg_pred, bg_pred, has_fg, has_bg):
                    ch = self.color_model.dictionary[chid]
                    if ch == '<S>':
                        continue
                    if ch == '</S>':
                        break
                    # 处理前景色
                    if h_fg.item():
                        fr(int(c_fg[0] * 255))
                        fg(int(c_fg[1] * 255))
                        fb(int(c_fg[2] * 255))
                    # 处理背景色
                    if h_bg.item():
                        br(int(c_bg[0] * 255))
                        bg(int(c_bg[1] * 255))
                        bb(int(c_bg[2] * 255))
                    else:
                        # 如果没有背景色，使用前景色作为背景色
                        br(int(c_fg[0] * 255))
                        bg(int(c_fg[1] * 255))
                        bb(int(c_fg[2] * 255))
                
                textline.fg_r = min(max(int(fr()), 0), 255)
                textline.fg_g = min(max(int(fg()), 0), 255)
                textline.fg_b = min(max(int(fb()), 0), 255)
                textline.bg_r = min(max(int(br()), 0), 255)
                textline.bg_g = min(max(int(bg()), 0), 255)
                textline.bg_b = min(max(int(bb()), 0), 255)
            else:
                # 如果推理失败，设置默认颜色
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                self.logger.debug("48px color prediction returned no results, using default colors")
                
        except Exception as e:
            # 如果出错，设置默认颜色
            textline.fg_r = textline.fg_g = textline.fg_b = 0
            textline.bg_r = textline.bg_g = textline.bg_b = 255
            self.logger.debug(f"48px color prediction failed: {e}, using default colors")

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

            # 先裁剪包围框区域，避免在整个大图上做透视变换（与 48px 模型相同的策略）
            src_pts = points.astype(np.int64).copy()
            im_h, im_w = img.shape[:2]

            x1, y1, x2, y2 = src_pts[:, 0].min(), src_pts[:, 1].min(), src_pts[:, 0].max(), src_pts[:, 1].max()
            x1 = np.clip(x1, 0, im_w)
            y1 = np.clip(y1, 0, im_h)
            x2 = np.clip(x2, 0, im_w)
            y2 = np.clip(y2, 0, im_h)
            
            # 检查裁剪区域是否有效
            if x1 >= x2 or y1 >= y2:
                return None
            
            # 裁剪局部区域
            img_cropped = img[y1:y2, x1:x2]
            
            # 调整点坐标到局部坐标系
            src_pts[:, 0] -= x1
            src_pts[:, 1] -= y1

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

            # Perspective transform on cropped image
            M = cv2.getPerspectiveTransform(src_pts.astype(np.float32), pts_std)
            dst_img = cv2.warpPerspective(
                img_cropped,
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


class ModelPaddleOCRLatin(ModelPaddleOCR):
    """Latin/English OCR"""
    def __init__(self, *args, **kwargs):
        super().__init__(model_type='latin', *args, **kwargs)


class ModelPaddleOCRThai(ModelPaddleOCR):
    """Thai OCR"""
    def __init__(self, *args, **kwargs):
        super().__init__(model_type='thai', *args, **kwargs)
