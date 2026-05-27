"""
PaddleOCR-VL-1.5 OCR Model

基于 PaddleOCR-VL-1.5 的 OCR 模型
模型来源: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5
"""

import os
import sys
from typing import List

import cv2
import einops
import numpy as np
import torch
from PIL import Image

from ..config import OcrConfig
from ..utils import Quadrilateral
from ..utils.generic import AvgMeter
from .common import OfflineOCR


class ModelPaddleOCRVL(OfflineOCR):
    """
    PaddleOCR-VL-1.5 OCR 模型

    这是一个基于 VLM 的 OCR 模型。
    模型使用 transformers 库加载，支持 GPU 加速。
    """

    _MODEL_MAPPING = {
        'model': {
            'url': [
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/PaddleOCR-VL-1.5.7z',
            ],
            'hash': '6427e6fbe68f28cdb99594ea39d98d6169f38f04d386b0f4eb62cc176510c2eb',
            'archive': {
                'PaddleOCR-VL-1.5/': '.',
            },
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
    MODEL_DIR_NAME = "PaddleOCR-VL-1.5"
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = None
        self.processor = None
        self.device = None
        self.color_model = None  # 48px 模型用于颜色预测

    async def _load(self, device: str):
        """加载模型"""
        # 在加载前过滤相关警告
        import warnings
        warnings.filterwarnings('ignore', message='.*slow image processor.*')

        # 确定模型路径 - 使用 models/ocr/PaddleOCR-VL-1.5
        model_path = os.path.join(self.model_dir, self.MODEL_DIR_NAME)
        
        # 自动修补模型文件
        from .paddleocr_vl_patcher import (
            patch_paddleocr_vl_files,
            register_ernie_modules,
        )
        if os.path.exists(model_path):
            patch_paddleocr_vl_files(model_path)
            register_ernie_modules(model_path)
        
        use_relative_path = False
        original_cwd = None

        if not os.path.exists(model_path) or not os.path.exists(os.path.join(model_path, "config.json")):
            # 如果本地没有，尝试从 HuggingFace 加载
            model_path = "PaddlePaddle/PaddleOCR-VL-1.5"
        else:
            # Windows 中文路径兼容：使用 tokenizers 后端（use_fast=True）避免 sentencepiece 路径问题
            # 通过切换工作目录使用相对路径来规避
            if sys.platform == 'win32':
                try:
                    # 检测路径是否包含非 ASCII 字符
                    model_path.encode('ascii')
                except UnicodeEncodeError:
                    use_relative_path = True

        # 设置设备
        if device == 'cuda' and torch.cuda.is_available():
            self.device = 'cuda'
            self.torch_device = 'cuda'
            self.use_gpu = True
            # 使用 bfloat16 以节省显存
            model_dtype = torch.bfloat16
        elif device == 'mps' and hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            self.device = 'mps'
            self.torch_device = 'mps'
            self.use_gpu = True
            model_dtype = torch.float16
        elif device == 'dml':
            # PyTorch torch-directml 算子在部分 AMD/Intel 显卡驱动上存在兼容性缺陷，极易引发 C++ 级底层崩溃闪退。
            # 这是一个重型的 VLM 视觉大模型，在 torch-directml 运行时极易发生驱动超时或算子兼容性崩溃。
            # 强制回退至 CPU 推理，保障软件绝对不闪退。
            self.device = 'cpu'
            self.torch_device = 'cpu'
            self.use_gpu = False
            model_dtype = torch.float32
        else:
            self.device = 'cpu'
            self.torch_device = 'cpu'
            self.use_gpu = False
            model_dtype = torch.float32

        try:
            # 如果需要使用相对路径，切换工作目录
            if use_relative_path:
                original_cwd = os.getcwd()
                os.chdir(model_path)
                load_path = "."
            else:
                load_path = model_path

            # 直接从 tokenizer.json 加载纯快速 tokenizer，完全避免 sentencepiece
            import json

            from transformers import PreTrainedTokenizerFast
            
            tokenizer_json_path = os.path.join(load_path if not use_relative_path else ".", "tokenizer.json")
            tokenizer_config_path = os.path.join(load_path if not use_relative_path else ".", "tokenizer_config.json")
            chat_template_path = os.path.join(load_path if not use_relative_path else ".", "chat_template.jinja")
            
            # 读取 tokenizer 配置
            with open(tokenizer_config_path, 'r', encoding='utf-8') as f:
                tokenizer_config = json.load(f)
            
            # 读取 chat_template
            chat_template = None
            if os.path.exists(chat_template_path):
                with open(chat_template_path, 'r', encoding='utf-8') as f:
                    chat_template = f.read()
            
            # 使用 PreTrainedTokenizerFast 直接加载，不依赖 sentencepiece
            tokenizer = PreTrainedTokenizerFast(
                tokenizer_file=tokenizer_json_path,
                bos_token=tokenizer_config.get('bos_token', '<s>'),
                eos_token=tokenizer_config.get('eos_token', '</s>'),
                unk_token=tokenizer_config.get('unk_token', '<unk>'),
                pad_token=tokenizer_config.get('pad_token'),
                model_max_length=tokenizer_config.get('model_max_length', 1000000000000000019884624838656),
                clean_up_tokenization_spaces=tokenizer_config.get('clean_up_tokenization_spaces', False)
            )
            
            # 手动设置 chat_template
            if chat_template:
                tokenizer.chat_template = chat_template
            
            # 加载 image processor（使用慢速模式）
            # 过滤 slow image processor 警告
            import warnings

            from transformers import AutoImageProcessor
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='.*slow image processor.*')
                image_processor = AutoImageProcessor.from_pretrained(
                    load_path,
                    trust_remote_code=True,
                    local_files_only=use_relative_path
                )
            
            # 手动创建 processor，直接导入自定义类避免 AutoProcessor 再次加载 tokenizer
            sys.path.insert(0, load_path if not use_relative_path else ".")
            try:
                from processing_paddleocr_vl import PaddleOCRVLProcessor
                self.processor = PaddleOCRVLProcessor(
                    image_processor=image_processor,
                    tokenizer=tokenizer,
                    chat_template=tokenizer.chat_template if hasattr(tokenizer, 'chat_template') else None
                )
            finally:
                if (load_path if not use_relative_path else ".") in sys.path:
                    sys.path.remove(load_path if not use_relative_path else ".")

            # 使用 AutoModel 加载自定义模型架构
            from transformers import AutoModel
            # DirectML 不支持 transformers device_map,改用显式 .to()
            use_device_map = self.device not in ('cpu', 'dml')
            self.model = AutoModel.from_pretrained(
                load_path,
                trust_remote_code=True,
                torch_dtype=model_dtype,
                device_map=self.device if use_device_map else None,
                local_files_only=use_relative_path
            )
        finally:
            # 恢复原工作目录
            if original_cwd is not None:
                os.chdir(original_cwd)

        if self.device == 'cpu' or self.device == 'dml':
            self.model = self.model.to(self.torch_device)

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
                    self.color_model = self.color_model.to(self.torch_device)
                else:
                    self.color_model = self.color_model.to(torch.device('cpu'))
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

        # 确保是 RGB 模式
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')

        # 构建对话消息
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_img},
                    {"type": "text", "text": prompt_text}
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
        inputs = {k: v.to(self.torch_device) if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}

        # 生成文本
        with torch.no_grad():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=256,
                do_sample=False
            )

        # 解码 - 只取新生成的部分
        input_len = inputs["input_ids"].shape[1]
        generated_ids_trimmed = generated_ids[:, input_len:]
        output_text = self.processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False
        )[0]

        return output_text.strip()

    def _estimate_colors_48px(self, region: np.ndarray, textline: Quadrilateral):
        """使用 48px 模型预测前景色和背景色"""
        try:
            # 如果 48px 模型未加载，使用默认颜色
            if self.color_model is None:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                return

            # 调整大小到 48px 高度
            text_height = 48
            h, w = region.shape[:2]
            ratio = w / float(h)
            new_w = int(round(ratio * text_height))

            if new_w == 0:
                new_w = 1

            region_resized = cv2.resize(region, (new_w, text_height), interpolation=cv2.INTER_AREA)

            canvas_w = self._get_ocr_canvas_width([new_w], base_align=4)
            batch_region = np.zeros((1, text_height, canvas_w, 3), dtype=np.uint8)
            batch_region[0, :, :new_w, :] = region_resized
            image_tensor = (torch.from_numpy(batch_region).float() - 127.5) / 127.5
            image_tensor = einops.rearrange(image_tensor, 'N H W C -> N C H W')

            # GPU 加速
            if self.use_gpu:
                image_tensor = image_tensor.to(self.torch_device)

            # 使用 48px 模型推理
            with torch.no_grad():
                ret = self.color_model.infer_beam_batch(image_tensor, [new_w], beams_k=5, max_seq_length=255)

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
        text_height = 48  # 默认文本高度
        ignore_bubble = config.ignore_bubble
        use_model_bubble_filter = bool(getattr(config, 'use_model_bubble_filter', False))
        ocr_prompt = self._build_ocr_prompt(config)

        # 生成文本方向信息
        quadrilaterals = list(self._generate_text_direction(textlines))

        output_regions = []

        for idx, (q, direction) in enumerate(quadrilaterals):
            # 获取变换后的区域图像
            region_img = q.get_transformed_region(image, direction, text_height)

            # 过滤非气泡区域
            if ignore_bubble > 0 or use_model_bubble_filter:
                if self._should_ignore_region(region_img, ignore_bubble, image, q, config):
                    self.logger.info(f'[FILTERED] Region {idx} ignored - Non-bubble area detected (ignore_bubble={ignore_bubble}, model_filter={use_model_bubble_filter})')
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
                self._estimate_colors_48px(region_img, q)

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


