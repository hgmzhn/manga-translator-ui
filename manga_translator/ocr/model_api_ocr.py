import asyncio
import base64
import io
import os
from typing import List

import cv2
import einops
import numpy as np
import torch
from PIL import Image

from ..config import OcrConfig
from ..custom_api_params import (
    load_enabled_custom_api_params,
    merge_openai_request_params,
    split_gemini_request_params,
)
from ..runtime_api_resolver import resolve_runtime_api_config
from ..api_key_rotation import run_with_api_candidates
from ..utils import Quadrilateral
from ..utils.dotenv_utils import load_app_dotenv
from ..utils.generic import AvgMeter
from ..utils.image_modes import normalize_rgb_image
from .common import OfflineOCR
from .prompt_loader import (
    DEFAULT_AI_OCR_PROMPT,
    ensure_ai_ocr_prompt_file,
    load_ai_ocr_prompt_file,
)

OPENAI_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

GEMINI_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Origin": "https://aistudio.google.com",
    "Referer": "https://aistudio.google.com/",
}

class BaseAPIOCR(OfflineOCR):
    _MODEL_MAPPING = {
        "color_model": {
            "url": [
                "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/ocr_ar_48px.ckpt",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ocr_ar_48px.ckpt",
            ],
            "hash": "29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d",
        },
        "color_dict": {
            "url": [
                "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/alphabet-all-v7.txt",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/alphabet-all-v7.txt",
            ],
            "hash": "f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a",
        },
    }

    API_KEY_ENV = ""
    API_BASE_ENV = ""
    MODEL_ENV = ""
    FALLBACK_API_KEY_ENV = ""
    FALLBACK_API_BASE_ENV = ""
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = ""
    DEFAULT_MODEL = ""
    BROWSER_HEADERS = {}
    PROVIDER_NAME = "API OCR"
    SUPPORTS_RUNTIME_CONFIG = True
    ALLOW_EMPTY_LOCAL_API_KEY = False
    RUNTIME_PROVIDER = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = "cpu"
        self.color_model = None
        self.client = None
        self.api_key = None
        self.base_url = None
        self.model_name = None
        self._client_signature = None
        self._client_loop = None

        is_web_server = os.getenv("MANGA_TRANSLATOR_WEB_SERVER", "false").lower() == "true"
        if not is_web_server:
            try:
                load_app_dotenv(override=True)
            except Exception:
                pass

    async def _load(self, device: str):
        if device == "cuda" and torch.cuda.is_available():
            self.device = "cuda"
            self.use_gpu = True
        elif device == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self.device = "mps"
            self.use_gpu = True
        else:
            self.device = "cpu"
            self.use_gpu = False

        await self._load_color_model(self.device)

    async def _unload(self):
        if self.color_model is not None:
            del self.color_model
            self.color_model = None

    def _read_runtime_config(self, runtime_config=None):
        try:
            load_app_dotenv(override=True)
        except Exception:
            pass

        return resolve_runtime_api_config(
            runtime_config,
            feature="ocr",
            provider=self.RUNTIME_PROVIDER,
            api_key_env=self.API_KEY_ENV,
            api_base_env=self.API_BASE_ENV,
            model_env=self.MODEL_ENV,
            fallback_api_key_env=self.FALLBACK_API_KEY_ENV,
            fallback_api_base_env=self.FALLBACK_API_BASE_ENV,
            fallback_model_env=self.FALLBACK_MODEL_ENV,
            default_api_base=self.DEFAULT_API_BASE,
            default_model=self.DEFAULT_MODEL,
            allow_empty_local_api_key=self.ALLOW_EMPTY_LOCAL_API_KEY,
        )

    def _missing_api_key_message(self) -> str:
        message = f"{self.PROVIDER_NAME} is not configured. Set {self.API_KEY_ENV} in .env"
        if self.FALLBACK_API_KEY_ENV:
            message += f" (or fallback {self.FALLBACK_API_KEY_ENV})"
        return message + "."

    async def _close_client(self, client):
        if client is None:
            return
        close_fn = getattr(client, "close", None)
        if not callable(close_fn):
            return
        close_result = close_fn()
        if asyncio.iscoroutine(close_result):
            await close_result

    def _build_ocr_prompt(self, config: OcrConfig) -> str:
        ensure_ai_ocr_prompt_file()
        file_prompt = load_ai_ocr_prompt_file(None)
        if file_prompt:
            return file_prompt
        custom_prompt = (getattr(config, "ai_ocr_custom_prompt", None) or "").strip()
        if custom_prompt:
            return custom_prompt
        return DEFAULT_AI_OCR_PROMPT

    def _normalize_ocr_text(self, text: str) -> str:
        text = (text or "").replace("\r\n", "\n").strip()
        if text.startswith("```") and text.endswith("```"):
            stripped = text.strip("`").strip()
            if "\n" in stripped:
                stripped = stripped.split("\n", 1)[1].strip()
            text = stripped
        return text

    def _encode_region_png_base64(self, region: np.ndarray) -> str:
        image = Image.fromarray(region.astype(np.uint8))
        image = normalize_rgb_image(image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _get_ai_ocr_concurrency(self, config: OcrConfig) -> int:
        try:
            return max(int(getattr(config, "ai_ocr_concurrency", 1) or 1), 1)
        except (TypeError, ValueError):
            return 1

    async def recognize(
        self,
        image: np.ndarray,
        textlines: List[Quadrilateral],
        config: OcrConfig,
        verbose: bool = False,
        runtime_config=None,
    ) -> List[Quadrilateral]:
        self._model_bubble_cache_key = None
        self._model_bubble_cache_mask = None
        self._model_bubble_no_boxes_logged = False
        custom_api_params = load_enabled_custom_api_params(
            runtime_config if runtime_config is not None else config,
            self.logger,
            target="ocr",
        )
        if bool(getattr(config, 'use_model_bubble_filter', False)):
            threshold = float(getattr(config, 'model_bubble_overlap_threshold', 0.1))
            self.logger.info(f"Model bubble filter enabled (overlap_threshold={threshold:.3f})")
        return await self._infer(
            image,
            textlines,
            config,
            verbose,
            runtime_config=runtime_config,
            custom_api_params=custom_api_params,
        )

    async def _load_color_model(self, device: str):
        from .model_48px import OCR

        try:
            dict_48px_path = self._get_file_path("alphabet-all-v7.txt")
            ckpt_48px_path = self._get_file_path("ocr_ar_48px.ckpt")

            if os.path.exists(dict_48px_path) and os.path.exists(ckpt_48px_path):
                with open(dict_48px_path, "r", encoding="utf-8") as fp:
                    dictionary_48px = [s[:-1] for s in fp.readlines()]

                self.color_model = OCR(dictionary_48px, 768)
                sd = torch.load(ckpt_48px_path, map_location="cpu", weights_only=False)

                if "state_dict" in sd:
                    sd = sd["state_dict"]

                cleaned_sd = {}
                for k, v in sd.items():
                    if k.startswith("model."):
                        cleaned_sd[k[6:]] = v
                    else:
                        cleaned_sd[k] = v

                self.color_model.load_state_dict(cleaned_sd)
                self.color_model.eval()

                if device in ("cuda", "mps"):
                    self.color_model = self.color_model.to(device)
            else:
                self.logger.warning(
                    f"48px model files are missing: {dict_48px_path} or {ckpt_48px_path}"
                )
                self.color_model = None
        except Exception as e:
            self.logger.warning(f"Failed to load 48px color model: {e}")
            self.color_model = None

    def _estimate_colors_48px(self, region: np.ndarray, textline: Quadrilateral):
        try:
            if self.color_model is None:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                return

            text_height = 48
            h, w = region.shape[:2]
            ratio = w / float(h)
            new_w = max(int(round(ratio * text_height)), 1)

            region_resized = cv2.resize(region, (new_w, text_height), interpolation=cv2.INTER_AREA)
            canvas_w = self._get_ocr_canvas_width([new_w], base_align=4)
            batch_region = np.zeros((1, text_height, canvas_w, 3), dtype=np.uint8)
            batch_region[0, :, :new_w, :] = region_resized

            image_tensor = (torch.from_numpy(batch_region).float() - 127.5) / 127.5
            image_tensor = einops.rearrange(image_tensor, "N H W C -> N C H W")

            if self.use_gpu:
                image_tensor = image_tensor.to(self.device)

            with torch.no_grad():
                ret = self.color_model.infer_beam_batch(
                    image_tensor, [new_w], beams_k=5, max_seq_length=255
                )

            if not ret:
                textline.fg_r = textline.fg_g = textline.fg_b = 0
                textline.bg_r = textline.bg_g = textline.bg_b = 255
                return

            pred_chars_index, _, fg_pred, bg_pred, fg_ind_pred, bg_ind_pred = ret[0]
            has_fg = fg_ind_pred[:, 1] > fg_ind_pred[:, 0]
            has_bg = bg_ind_pred[:, 1] > bg_ind_pred[:, 0]

            fr = AvgMeter()
            fg = AvgMeter()
            fb = AvgMeter()
            br = AvgMeter()
            bg = AvgMeter()
            bb = AvgMeter()

            for chid, c_fg, c_bg, h_fg, h_bg in zip(
                pred_chars_index, fg_pred, bg_pred, has_fg, has_bg
            ):
                ch = self.color_model.dictionary[chid]
                if ch == "<S>":
                    continue
                if ch == "</S>":
                    break
                if h_fg.item():
                    fr(int(c_fg[0] * 255))
                    fg(int(c_fg[1] * 255))
                    fb(int(c_fg[2] * 255))
                if h_bg.item():
                    br(int(c_bg[0] * 255))
                    bg(int(c_bg[1] * 255))
                    bb(int(c_bg[2] * 255))
                else:
                    br(int(c_fg[0] * 255))
                    bg(int(c_fg[1] * 255))
                    bb(int(c_fg[2] * 255))

            textline.fg_r = min(max(int(fr()), 0), 255)
            textline.fg_g = min(max(int(fg()), 0), 255)
            textline.fg_b = min(max(int(fb()), 0), 255)
            textline.bg_r = min(max(int(br()), 0), 255)
            textline.bg_g = min(max(int(bg()), 0), 255)
            textline.bg_b = min(max(int(bb()), 0), 255)
        except Exception as e:
            textline.fg_r = textline.fg_g = textline.fg_b = 0
            textline.bg_r = textline.bg_g = textline.bg_b = 255
            self.logger.debug(f"48px color estimation failed: {e}")

    async def _recognize_single(
        self,
        img: np.ndarray,
        prompt_text: str,
        runtime_config=None,
        runtime_settings=None,
        custom_api_params: dict | None = None,
    ) -> str:
        settings = runtime_settings or self._read_runtime_config(runtime_config)
        if not settings.api_key:
            raise RuntimeError(self._missing_api_key_message())

        async def _request_with_endpoint(endpoint) -> str:
            client = self._create_client(api_key=endpoint.api_key, base_url=endpoint.base_url)
            try:
                text = self._normalize_ocr_text(
                    await self._request_ocr_text(
                        client=client,
                        model_name=endpoint.model_name,
                        img=img,
                        prompt_text=prompt_text,
                        custom_api_params=custom_api_params,
                    )
                )
                if not text:
                    raise RuntimeError(f"{self.PROVIDER_NAME} response did not contain OCR text.")
                return text
            finally:
                await self._close_client(client)

        async def _do_request() -> str:
            return await run_with_api_candidates(
                endpoints=settings.candidates,
                strategy=settings.strategy,
                operation=_request_with_endpoint,
                provider_name=self.PROVIDER_NAME,
                operation_name="OCR request",
                logger=self.logger,
                runtime_config=runtime_config,
            )

        return await _do_request()

    async def _infer(
        self,
        image: np.ndarray,
        textlines: List[Quadrilateral],
        config: OcrConfig,
        verbose: bool = False,
        runtime_config=None,
        custom_api_params: dict | None = None,
    ) -> List[Quadrilateral]:
        text_height = 48
        ignore_bubble = config.ignore_bubble
        use_model_bubble_filter = bool(getattr(config, "use_model_bubble_filter", False))
        ocr_prompt = self._build_ocr_prompt(config)
        quadrilaterals = list(self._generate_text_direction(textlines))
        output_regions = []
        pending_regions = []
        runtime_settings = self._read_runtime_config(runtime_config)
        if not runtime_settings.api_key:
            raise RuntimeError(self._missing_api_key_message())

        for idx, (q, direction) in enumerate(quadrilaterals):
            region_img = q.get_transformed_region(image, direction, text_height)

            if ignore_bubble > 0 or use_model_bubble_filter:
                if self._should_ignore_region(region_img, ignore_bubble, image, q, config):
                    self.logger.info(
                        f"[FILTERED] Region {idx} ignored - Non-bubble area detected "
                        f"(ignore_bubble={ignore_bubble}, model_filter={use_model_bubble_filter})"
                    )
                    self._cleanup_ocr_memory(region_img)
                    continue

            pending_regions.append((idx, q, region_img))

        if not pending_regions:
            return output_regions

        semaphore = asyncio.Semaphore(self._get_ai_ocr_concurrency(config))

        async def _process_region(idx: int, q: Quadrilateral, region_img: np.ndarray) -> Quadrilateral:
            try:
                async with semaphore:
                    text = await self._recognize_single(
                        region_img,
                        ocr_prompt,
                        runtime_config=runtime_config,
                        runtime_settings=runtime_settings,
                        custom_api_params=custom_api_params,
                    )
                    self.logger.info(f"[OCR] Region {idx}: {text}")
                    q.text = text
                    q.prob = 0.9

                    self._estimate_colors_48px(region_img, q)
            except Exception as e:
                self.logger.error(f"[ERROR] Region {idx} OCR failed: {e}")
                q.text = ""
                q.prob = 0.0
                q.fg_r = q.fg_g = q.fg_b = 0
                q.bg_r = q.bg_g = q.bg_b = 255
            finally:
                self._cleanup_ocr_memory(region_img)
            return q

        output_regions.extend(
            await asyncio.gather(
                *(_process_region(idx, q, region_img) for idx, q, region_img in pending_regions)
            )
        )

        return output_regions

    def _create_client(self, api_key: str, base_url: str):
        raise NotImplementedError

    async def _request_ocr_text(self, client, model_name: str, img: np.ndarray, prompt_text: str) -> str:
        raise NotImplementedError


class ModelOpenAIOCR(BaseAPIOCR):
    API_KEY_ENV = "OCR_OPENAI_API_KEY"
    API_BASE_ENV = "OCR_OPENAI_API_BASE"
    MODEL_ENV = "OCR_OPENAI_MODEL"
    FALLBACK_API_KEY_ENV = "OPENAI_API_KEY"
    FALLBACK_API_BASE_ENV = "OPENAI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o"
    BROWSER_HEADERS = OPENAI_BROWSER_HEADERS
    PROVIDER_NAME = "OpenAI OCR"
    ALLOW_EMPTY_LOCAL_API_KEY = True
    RUNTIME_PROVIDER = "openai"

    def _create_client(self, api_key: str, base_url: str):
        from ..translators.common import AsyncOpenAICurlCffi

        return AsyncOpenAICurlCffi(
            api_key=api_key,
            base_url=base_url,
            default_headers=self.BROWSER_HEADERS,
            impersonate="chrome110",
            timeout=600.0,
            stream_timeout=300.0,
        )

    def _extract_openai_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        parts.append(str(item["text"]))
                    elif "content" in item:
                        parts.append(str(item["content"]))
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)
        if isinstance(content, dict):
            if "text" in content:
                return str(content["text"])
            if "content" in content:
                return str(content["content"])
        return "" if content is None else str(content)

    async def _request_ocr_text(
        self,
        client,
        model_name: str,
        img: np.ndarray,
        prompt_text: str,
        custom_api_params: dict | None = None,
    ) -> str:
        image_b64 = self._encode_region_png_base64(img)
        request_params = merge_openai_request_params(
            {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                        ],
                    }
                ],
            },
            custom_api_params,
        )
        response = await client.chat.completions.create(
            **request_params,
        )
        if not getattr(response, "choices", None):
            return ""
        return self._extract_openai_text(response.choices[0].message.content)


class ModelGeminiOCR(BaseAPIOCR):
    API_KEY_ENV = "OCR_GEMINI_API_KEY"
    API_BASE_ENV = "OCR_GEMINI_API_BASE"
    MODEL_ENV = "OCR_GEMINI_MODEL"
    FALLBACK_API_KEY_ENV = "GEMINI_API_KEY"
    FALLBACK_API_BASE_ENV = "GEMINI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL = "gemini-1.5-flash"
    BROWSER_HEADERS = GEMINI_BROWSER_HEADERS
    PROVIDER_NAME = "Gemini OCR"
    RUNTIME_PROVIDER = "gemini"
    SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    ]

    def _create_client(self, api_key: str, base_url: str):
        from ..translators.common import AsyncGeminiCurlCffi

        return AsyncGeminiCurlCffi(
            api_key=api_key,
            base_url=base_url,
            default_headers=self.BROWSER_HEADERS,
            impersonate="chrome110",
            timeout=600.0,
            stream_timeout=300.0,
        )

    def _extract_gemini_text(self, response) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return getattr(response, "text", "") or ""
        parts = getattr(candidates[0].content, "parts", None) or []
        return "".join(part.text for part in parts if getattr(part, "text", None))

    async def _request_ocr_text(
        self,
        client,
        model_name: str,
        img: np.ndarray,
        prompt_text: str,
        custom_api_params: dict | None = None,
    ) -> str:
        image_b64 = self._encode_region_png_base64(img)
        request_overrides, generation_overrides = split_gemini_request_params(custom_api_params)
        request_kwargs = {
            "model": model_name,
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": image_b64,
                            }
                        },
                    ],
                }
            ],
            "safetySettings": self.SAFETY_SETTINGS,
        }
        if generation_overrides:
            request_kwargs["generationConfig"] = generation_overrides
        request_kwargs.update(request_overrides)
        response = await client.models.generate_content(**request_kwargs)
        return self._extract_gemini_text(response)
