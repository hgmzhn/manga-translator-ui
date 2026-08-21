"""
PaddleOCR-VL-1.6 OCR Model

基于 PaddleOCR-VL-1.6 的 OCR 模型
模型来源: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
"""

import os
import re
from typing import List

import einops
import numpy as np
import torch
from PIL import Image

from ..config import OcrConfig
from ..utils import Quadrilateral
from ..utils.generic import AvgMeter
from ..utils.image_modes import normalize_rgb_image
from .common import OfflineOCR


_PADDLEOCR_VL_16_BASE_URL = "https://www.modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.6/resolve/master"
_PADDLEOCR_VL_16_FILES = {
    "added_tokens.json": "f59f889088e0fe21c523e7cf121bb6dca3b0bb148cb7159fbb4572c74dfc5644",
    "chat_template.jinja": "2f27812dab7f333e471884e0c803d807f11953d5453140dfb1aaba234f872bc8",
    "config.json": "ce7f4565f8b1db78532ad5d1b9ebe55c2139d49bd4cb04778b580a08a598f171",
    "configuration_paddleocr_vl.py": "753dd93654c3a9c8c85a3eaee1e3092dd12591b0f2dce0305e1abfb7a41ff160",
    "generation_config.json": "a6701d78ab3b4d972307cdec3b69d4c13f46e0d5140514f50ab7d84259324b94",
    "image_processing_paddleocr_vl.py": "a4fa521b9cb16e207f94b7f2d16427771776dfc634420d319fc4916ee58049ec",
    "inference.yml": "1587aece2d6442366efce34161d5f7d5f67f09ebfbf043168e5fade892c2780e",
    "model.safetensors": "85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db",
    "modeling_paddleocr_vl.py": "c5013dff57ca8b87dc1de64d0fd839a44313de09d230a4fb2d08289d2cad5111",
    "preprocessor_config.json": "111872ab1e8bb7fd040ac5087bfced7ab8f011f02139b088cba294964c3b1d0e",
    "processing_paddleocr_vl.py": "e29cb1e5f275f2bd3ce051bd5c9983a33894e693b2823a0e13d4c07c8c4f9e13",
    "processor_config.json": "1568858960a9760c54431dae693a6152e601ff55cdf6d2eab97a4a99958faea0",
    "special_tokens_map.json": "d3a125c03103deb2acaf7730791bdbbf196f620e5a2213b664511ff9b4b25bab",
    "tokenizer.json": "c8a215a59183d0d0781adc33bacd3ce6162716f7fd568fb30234a74d69803a7d",
    "tokenizer.model": "34ef7db83df785924fb83d7b887b6e822a031c56e15cff40aaf9b982988180df",
    "tokenizer_config.json": "1f979337347cc0cb72a6282d8a23ed183539aa81a87a906f022aee2bab83c7c5",
}


class ModelPaddleOCRVL(OfflineOCR):
    """
    PaddleOCR-VL-1.6 OCR 模型

    这是一个基于 VLM 的 OCR 模型。
    模型使用 transformers 库加载，支持 GPU 加速。
    """

    _MODEL_MAPPING = {
        **{
            f"paddleocr_vl_16_{filename}": {
                "url": f"{_PADDLEOCR_VL_16_BASE_URL}/{filename}",
                "hash": sha256,
                "file": os.path.join("PaddleOCR-VL-1.6", filename),
            }
            for filename, sha256 in _PADDLEOCR_VL_16_FILES.items()
        },
        # 48px 颜色预测模型
        'color_model': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/ocr_ar_48px.ckpt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ocr_ar_48px.ckpt',
            ],
            'hash': '29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d',
        },
        'color_dict': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/alphabet-all-v7.txt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/alphabet-all-v7.txt',
            ],
            'hash': 'f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a',
        },
    }

    # 模型子目录名（在 models/ocr/ 下）
    MODEL_DIR_NAME = "PaddleOCR-VL-1.6"
    _OCR_VL_LANGUAGE_HINTS = {
        "auto": "OCR: Extract all text.",
        "multilingual": "OCR: Extract all multilingual text.",
        "arabic": "OCR: Extract all Arabic text.",
        "simplified chinese": "OCR: Extract all Simplified Chinese text.",
        "traditional chinese": "OCR: Extract all Traditional Chinese text.",
        "english": "OCR: Extract all English text.",
        "japanese": "OCR: Extract all Japanese text.",
        "korean": "OCR: Extract all Korean text.",
        "spanish": "OCR: Extract all Spanish text.",
        "french": "OCR: Extract all French text.",
        "german": "OCR: Extract all German text.",
        "russian": "OCR: Extract all Russian text.",
        "portuguese": "OCR: Extract all Portuguese text.",
        "italian": "OCR: Extract all Italian text.",
        "thai": "OCR: Extract all Thai text.",
        "vietnamese": "OCR: Extract all Vietnamese text.",
        "indonesian": "OCR: Extract all Indonesian text.",
        "turkish": "OCR: Extract all Turkish text.",
        "polish": "OCR: Extract all Polish text.",
        "ukrainian": "OCR: Extract all Ukrainian text.",
    }
    _OCR_VL_LANGUAGE_ALIASES = {
        # Legacy short forms
        "ar": "Arabic",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "en": "English",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "ru": "Russian",
        # Common config language codes
        "eng": "English",
        "jpn": "Japanese",
        "kor": "Korean",
        "chs": "Simplified Chinese",
        "cht": "Traditional Chinese",
        "esp": "Spanish",
        "fra": "French",
        "deu": "German",
        "rus": "Russian",
        "ptb": "Portuguese",
        "ptg": "Portuguese",
        "ita": "Italian",
        "tha": "Thai",
        "vie": "Vietnamese",
        "ind": "Indonesian",
        "trk": "Turkish",
        "pol": "Polish",
        "ukr": "Ukrainian",
    }
    _OCR_VL_RETRY_GENERATION_CONFIGS = [
        {
            "max_new_tokens": 128,
            "do_sample": False,
        },
        {
            "max_new_tokens": 64,
            "do_sample": False,
            "repetition_penalty": 1.15,
            "no_repeat_ngram_size": 4,
        },
        {
            "max_new_tokens": 48,
            "do_sample": False,
            "repetition_penalty": 1.25,
            "no_repeat_ngram_size": 3,
        },
    ]
    _OCR_VL_RETRY_PROMPT_SUFFIXES = [
        "",
        "\nReturn only the visible OCR text once. Do not repeat characters or punctuation.",
        "\nOnly output the exact visible text. If unsure, output nothing.",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.processor = None
        self.device = None
        self.color_model = None  # 48px 模型用于颜色预测

    async def _download(self):
        os.makedirs(os.path.join(self.model_dir, self.MODEL_DIR_NAME), exist_ok=True)
        await super()._download()

    async def _load(self, device: str):
        """加载模型"""
        # 确定模型路径 - 使用 models/ocr/PaddleOCR-VL-1.6
        model_path = os.path.join(self.model_dir, self.MODEL_DIR_NAME)

        # 设置设备
        if device == 'cuda' and torch.cuda.is_available():
            self.device = 'cuda'
            self.use_gpu = True
            # 使用 bfloat16 以节省显存
            model_dtype = torch.bfloat16
        elif device == 'mps' and hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            self.device = 'mps'
            self.use_gpu = True
            model_dtype = torch.float16
        else:
            self.device = 'cpu'
            self.use_gpu = False
            model_dtype = torch.float32
        from .paddleocr_vl_patcher import patch_transformers_paddleocr_vl_docs

        # Transformers 5.15.0 omits min/max_pixels from the generated docs,
        # which logs errors during PaddleOCR-VL image processor import.
        patch_transformers_paddleocr_vl_docs()
        from transformers import (
            AutoImageProcessor,
            AutoTokenizer,
            PaddleOCRVLForConditionalGeneration,
        )
        from transformers.models.paddleocr_vl.configuration_paddleocr_vl import PaddleOCRTextConfig
        from transformers.models.paddleocr_vl.processing_paddleocr_vl import PaddleOCRVLProcessor

        PaddleOCRTextConfig.ignore_keys_at_rope_validation = {"mrope_section"}
        image_processor = AutoImageProcessor.from_pretrained(
            model_path,
            trust_remote_code=False,
            local_files_only=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            trust_remote_code=False,
            local_files_only=True,
        )
        self.processor = PaddleOCRVLProcessor(
            image_processor=image_processor,
            tokenizer=tokenizer,
            chat_template=getattr(tokenizer, "chat_template", None),
        )
        self.model = PaddleOCRVLForConditionalGeneration.from_pretrained(
            model_path,
            key_mapping={
                r"^visual\.": r"model.visual.",
                r"^mlp_AR\.": r"model.projector.",
                r"^model\.(?!visual\.|projector\.|language_model\.)": r"model.language_model.",
            },
            dtype=model_dtype,
            device_map=self.device if self.device != 'cpu' else None,
            local_files_only=True,
        )

        if self.device == 'cpu':
            self.model = self.model.to(self.device)

        self.model.eval()

        # 加载 48px 模型用于颜色预测
        await self._load_color_model(device)

    async def _load_color_model(self, device: str):
        """加载 48px 颜色预测模型"""
        from .model_48px import OCR

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
            else:
                self.logger.warning(f"48px 模型文件不存在: {dict_48px_path} 或 {ckpt_48px_path}")
                self.color_model = None
        except Exception as e:
            self.logger.warning(f"加载 48px 颜色模型失败: {e}")
            self.color_model = None

    async def _unload(self):
        """卸载模型"""
        if self.model is not None:
            del self.model
            self.model = None
        if self.processor is not None:
            del self.processor
            self.processor = None
        if self.color_model is not None:
            del self.color_model
            self.color_model = None
        if self.use_gpu:
            pass

    def _build_ocr_prompt(self, config: OcrConfig) -> str:
        """Build OCR prompt with user override and language template prompt."""
        custom_prompt = (getattr(config, 'ocr_vl_custom_prompt', None) or '').strip()
        if custom_prompt:
            return custom_prompt
        language_hint_raw = (getattr(config, 'ocr_vl_language_hint', 'auto') or 'auto').strip()
        language_hint = language_hint_raw.lower()
        if language_hint in self._OCR_VL_LANGUAGE_HINTS:
            return self._OCR_VL_LANGUAGE_HINTS[language_hint]
        if language_hint in self._OCR_VL_LANGUAGE_ALIASES:
            full_name = self._OCR_VL_LANGUAGE_ALIASES[language_hint]
            return f"OCR: Extract all {full_name} text."
        return f"OCR: Extract all {language_hint_raw} text."

    def _looks_like_repeated_hallucination(self, text: str) -> bool:
        """Detect obvious PaddleOCR-VL runaway repetition such as a + many dots."""
        compact = re.sub(r"\s+", "", text or "")
        if len(compact) < 24:
            return False

        if re.search(r"(.)\1{19,}", compact):
            return True

        if re.search(r"(.{2,8})\1{7,}", compact):
            return True

        punctuation_count = sum(1 for ch in compact if not ch.isalnum())
        most_common_count = max(compact.count(ch) for ch in set(compact))
        return (
            len(compact) >= 30
            and punctuation_count / len(compact) >= 0.65
            and most_common_count / len(compact) >= 0.45
        )

    def _recognize_single(self, img: np.ndarray, prompt_text: str) -> str:
        """
        识别单个图像区域的文本

        Args:
            img: numpy 数组格式的图像 (RGB)

        Returns:
            识别的文本
        """
        # 转换为 PIL Image
        if isinstance(img, np.ndarray):
            pil_img = Image.fromarray(img)
        else:
            pil_img = img

        pil_img = normalize_rgb_image(pil_img)

        last_output = ''
        for attempt, (generation_config, retry_suffix) in enumerate(zip(
            self._OCR_VL_RETRY_GENERATION_CONFIGS,
            self._OCR_VL_RETRY_PROMPT_SUFFIXES,
        )):
            current_prompt = f"{prompt_text}{retry_suffix}"
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": pil_img},
                        {"type": "text", "text": current_prompt}
                    ]
                }
            ]

            # 直接使用 tokenizer 的聊天模板
            text = self.processor.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # 预处理
            inputs = self.processor(
                text=[text],
                images=[pil_img],
                return_tensors="pt",
                padding=True
            )

            # 移除模型不需要的 token_type_ids
            if 'token_type_ids' in inputs:
                del inputs['token_type_ids']

            # 移动到设备
            inputs = {k: v.to(self.device) if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}

            # 生成文本
            with torch.no_grad():
                generated_ids = self.model.generate(
                    **inputs,
                    **generation_config,
                )

            # 解码 - 只取新生成的部分
            input_len = inputs["input_ids"].shape[1]
            generated_ids_trimmed = generated_ids[:, input_len:]
            output_text = self.processor.batch_decode(
                generated_ids_trimmed,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False
            )[0].strip()

            last_output = output_text
            if not self._looks_like_repeated_hallucination(output_text):
                return output_text

            preview = output_text[:80].replace('\n', '\\n')
            self.logger.warning(
                f"PaddleOCR-VL repeated-output hallucination detected "
                f"(attempt={attempt + 1}, preview={preview!r}), retrying"
            )

        self.logger.warning("PaddleOCR-VL repeated-output hallucination persisted after retries; returning empty text")
        return '' if self._looks_like_repeated_hallucination(last_output) else last_output


    def _estimate_colors_48px(self, image: np.ndarray, textline: Quadrilateral, direction: str):
        """使用 48px 模型预测前景色和背景色"""
        try:
            # 如果 48px 模型未加载，使用默认颜色
            if self.color_model is None:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                return

            text_height = 48
            region_resized = textline.get_transformed_region(image, direction, text_height)
            new_w = region_resized.shape[1]

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

                # 计算颜色
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

        except Exception as e:
            # 如果出错，设置默认颜色
            textline.fg_r = textline.fg_g = textline.fg_b = 0
            textline.bg_r = textline.bg_g = textline.bg_b = 255
            self.logger.debug(f"48px 颜色预测失败: {e}")

    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral], config: OcrConfig, verbose: bool = False) -> List[Quadrilateral]:
        """
        推理主函数

        Args:
            image: 完整图像
            textlines: 检测到的文本行边界框
            config: OCR 配置
            verbose: 是否详细输出

        Returns:
            带有识别文本的 Quadrilateral 列表
        """
        text_height = 48  # 仅供气泡过滤和颜色预测使用
        ignore_bubble = config.ignore_bubble
        use_model_bubble_filter = bool(getattr(config, 'use_model_bubble_filter', False))
        ocr_prompt = self._build_ocr_prompt(config)
        image_height, image_width = image.shape[:2]

        # 生成文本方向信息
        quadrilaterals = list(self._generate_text_direction(textlines))

        output_regions = []

        for idx, (q, direction) in enumerate(quadrilaterals):
            q.assigned_direction = direction
            # VLM 直接识别原尺寸文本框；48px 变换只留给传统过滤和颜色模型。
            x1 = max(int(np.floor(q.pts[:, 0].min())), 0)
            y1 = max(int(np.floor(q.pts[:, 1].min())), 0)
            x2 = min(int(np.ceil(q.pts[:, 0].max())) + 1, image_width)
            y2 = min(int(np.ceil(q.pts[:, 1].max())) + 1, image_height)
            region_img = image[y1:y2, x1:x2].copy()
            if region_img.size == 0:
                region_img = np.full((2, 2, 3), 255, dtype=np.uint8)

            # 过滤非气泡区域
            if ignore_bubble > 0 or use_model_bubble_filter:
                filter_region = q.get_transformed_region(image, direction, text_height)
                should_ignore = self._should_ignore_region(filter_region, ignore_bubble, image, q, config)
                self._cleanup_ocr_memory(filter_region)
                if should_ignore:
                    self.logger.info(f'[FILTERED] Region {idx} ignored - Non-bubble area detected (ignore_bubble={ignore_bubble}, model_filter={use_model_bubble_filter})')
                    self._cleanup_ocr_memory(region_img)
                    continue

            try:
                # 识别文本
                text = self._recognize_single(region_img, ocr_prompt)

                if not text:
                    self.logger.info(f'[EMPTY] Region {idx} - No text detected')
                    q.text = ''
                    q.prob = 0.0
                else:
                    self.logger.info(f'[OCR] Region {idx}: {text}')
                    q.text = text
                    q.prob = 0.9  # VLM 模型没有置信度输出，使用固定值

                # 使用 48px 模型预测颜色
                self._estimate_colors_48px(image, q, direction)

                output_regions.append(q)

            except Exception as e:
                self.logger.error(f'[ERROR] Region {idx} OCR failed: {e}')
                q.text = ''
                q.prob = 0.0
                # 设置默认颜色
                q.fg_r = q.fg_g = q.fg_b = 0
                q.bg_r = q.bg_g = q.bg_b = 255
                output_regions.append(q)

            # 清理内存
            self._cleanup_ocr_memory(region_img)

        # 清理 GPU 显存
        if self.use_gpu:
            pass

        return output_regions


