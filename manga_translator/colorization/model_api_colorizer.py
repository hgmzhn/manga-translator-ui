import asyncio
import base64
import io
import os
from typing import Optional

import numpy as np
from PIL import Image

from ..api_request_params import (
    normalize_openai_image_request_params,
    split_gemini_request_params,
)
from ..custom_api_params import resolve_custom_api_params
from ..api_key_rotation import run_with_api_candidates
from ..runtime_api_resolver import resolve_runtime_api_config
from ..utils import get_logger
from ..utils.dotenv_utils import load_app_dotenv
from ..utils.ai_image_preprocess import (
    normalize_ai_image,
    prepare_square_ai_image,
    restore_square_ai_image,
)
from ..utils.openai_image_interface import request_openai_image_with_fallback
from .common import CommonColorizer
from .prompt_loader import (
    DEFAULT_AI_COLORIZER_PROMPT,
    build_ai_colorizer_prompt_payload,
    ensure_ai_colorizer_prompt_file,
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

_COLORIZER_SEMAPHORES: dict[str, tuple[int, asyncio.Semaphore]] = {}
_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _get_colorizer_semaphore(name: str, concurrency: int) -> asyncio.Semaphore:
    current = _COLORIZER_SEMAPHORES.get(name)
    if current is None or current[0] != concurrency:
        current = (concurrency, asyncio.Semaphore(concurrency))
        _COLORIZER_SEMAPHORES[name] = current
    return current[1]


class BaseAPIColorizer(CommonColorizer):
    API_KEY_ENV = ""
    API_BASE_ENV = ""
    MODEL_ENV = ""
    FALLBACK_API_KEY_ENV = ""
    FALLBACK_API_BASE_ENV = ""
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = ""
    DEFAULT_MODEL = ""
    BROWSER_HEADERS = {}
    PROVIDER_NAME = "API Colorizer"
    ALLOW_EMPTY_LOCAL_API_KEY = False
    RUNTIME_PROVIDER = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = get_logger("colorizer")
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

    def _read_runtime_config(self, runtime_config=None):
        try:
            load_app_dotenv(override=True)
        except Exception:
            pass

        return resolve_runtime_api_config(
            runtime_config,
            feature="colorizer",
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

    async def _ensure_client(self, runtime_config=None, runtime_settings=None, endpoint=None):
        current_loop = asyncio.get_running_loop()
        settings = runtime_settings or self._read_runtime_config(runtime_config)
        selected_endpoint = endpoint or (settings.candidates[0] if settings.candidates else None)
        api_key = selected_endpoint.api_key if selected_endpoint else settings.api_key
        base_url = selected_endpoint.base_url if selected_endpoint else settings.base_url
        model_name = selected_endpoint.model_name if selected_endpoint else settings.model_name
        signature = (api_key or "", base_url, model_name)
        if (
            self.client is not None
            and signature == self._client_signature
            and self._client_loop is current_loop
        ):
            return

        if self.client is not None:
            if self._client_loop is current_loop:
                try:
                    await self.client.close()
                except Exception:
                    pass
            else:
                self.logger.info(f"{self.PROVIDER_NAME}: recreating API client for a new event loop.")
            self.client = None
            self._client_loop = None

        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self._client_signature = signature

        if api_key:
            self.client = self._create_client(api_key=api_key, base_url=base_url)
            self._client_loop = current_loop

    def _build_colorizer_request(self, image: Image.Image, kwargs) -> tuple[str, list[dict[str, bytes | str]]]:
        ensure_ai_colorizer_prompt_file()
        image_path = kwargs.get("image_name") or getattr(image, "name", None)
        payload = build_ai_colorizer_prompt_payload(
            None,
            image_path=image_path,
        )

        reference_images: list[dict[str, bytes | str]] = []
        for ref in payload.get("reference_images", []):
            if not ref.resolved_path:
                self.logger.warning(f"{self.PROVIDER_NAME}: reference image not found: {ref.path}")
                continue
            try:
                with Image.open(ref.resolved_path) as reference_image:
                    reference_rgb = normalize_ai_image(reference_image)
                reference_images.append(
                    {
                        "kind": "prompt_reference",
                        "label": ref.description or os.path.basename(ref.path) or ref.path,
                        "image_bytes": self._image_to_png_bytes(reference_rgb),
                    }
                )
            except Exception as exc:
                self.logger.warning(
                    f"{self.PROVIDER_NAME}: failed to load reference image {ref.resolved_path}: {exc}"
                )

        prompt_text = payload.get("prompt_text") or DEFAULT_AI_COLORIZER_PROMPT
        history_images = kwargs.get("colorizer_history_images") or []
        if history_images:
            for idx, history_image in enumerate(history_images, start=1):
                try:
                    reference_images.append(
                        {
                            "kind": "history_reference",
                            "label": f"Previously colorized history page {idx}",
                            "image_bytes": self._image_to_png_bytes(self._to_rgb_image(history_image)),
                        }
                    )
                except Exception as exc:
                    self.logger.warning(
                        f"{self.PROVIDER_NAME}: failed to serialize history page {idx}: {exc}"
                    )

            prompt_text = (
                f"{prompt_text}\n\n"
                "Previously colorized history pages are attached separately. Use them only to keep cross-page palette, "
                "character colors, materials, and lighting consistent. Do not change the current page composition."
            ).strip()

        prompt_text = self._append_multi_image_prompt_guidance(prompt_text, reference_images)
        return prompt_text, reference_images

    def _resolve_concurrency(self, kwargs) -> int:
        del kwargs
        return 1

    def _append_multi_image_prompt_guidance(
        self,
        prompt_text: str,
        reference_images: list[dict[str, bytes | str]],
    ) -> str:
        if not reference_images:
            return prompt_text

        lines = [
            (prompt_text or "").strip(),
            "",
            "Attached image roles:",
            "1. Image 1 (first attached image): target manga page to colorize. Use this image for composition, panel layout, line art, and page content.",
        ]
        for idx, ref in enumerate(reference_images, start=2):
            label = str(ref.get("label") or f"Reference image {idx - 1}").strip()
            kind = str(ref.get("kind") or "").strip().lower()
            if kind == "prompt_reference":
                role_text = label or "general reference"
                lines.append(
                    f"{idx}. Image {idx} ({_ordinal_label(idx)} attached image): prompt-file reference image. "
                    f"Role/purpose: {role_text}."
                )
            elif kind == "history_reference":
                history_text = label or f"Previously colorized history page {idx - 1}"
                lines.append(
                    f"{idx}. Image {idx} ({_ordinal_label(idx)} attached image): previously colorized history page "
                    f"for cross-page consistency: {history_text}."
                )
            else:
                role_text = label or "general reference"
                lines.append(
                    f"{idx}. Image {idx} ({_ordinal_label(idx)} attached image): reference image. "
                    f"Role/purpose: {role_text}."
                )
        lines.extend(
            [
                "",
                "Use Image 1 as the only page to colorize. Use Image 2 and above only as reference images for palette, character consistency, materials, and lighting.",
                "If Image 2 or above is marked as a previously colorized history page, use it mainly for cross-page consistency.",
            ]
        )
        return "\n".join(lines).strip()
    def _to_rgb_image(self, image) -> Image.Image:
        if not isinstance(image, Image.Image):
            image = Image.fromarray(np.asarray(image).astype(np.uint8))
        return normalize_ai_image(image)

    def _image_to_png_bytes(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        self._to_rgb_image(image).save(buffer, format="PNG")
        return buffer.getvalue()

    async def _fetch_image_from_url(self, url: str) -> Image.Image:
        response = await self.client.session.get(url, timeout=600.0)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to download generated image: HTTP {response.status_code}")
        return normalize_ai_image(Image.open(io.BytesIO(response.content)))

    def _load_image_from_bytes(self, payload: bytes) -> Image.Image:
        return normalize_ai_image(Image.open(io.BytesIO(payload)))

    async def _reset_client_for_candidate(self, endpoint, error: Exception):
        del endpoint, error
        await self._close_current_client()

    async def _close_current_client(self):
        if self.client is None:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client_loop is current_loop and current_loop is not None:
            try:
                await self.client.close()
            except Exception:
                pass
        self.client = None
        self._client_loop = None

    def _extract_gemini_image(self, response) -> Optional[Image.Image]:
        raw = getattr(response, "raw", None) or {}
        for candidate in raw.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                inline_data = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline_data, dict):
                    continue
                data = inline_data.get("data")
                if not data:
                    continue
                return self._load_image_from_bytes(base64.b64decode(data))
        return None

    async def _colorize(self, image: Image.Image, colorization_size: int, **kwargs) -> Image.Image:
        del colorization_size
        runtime_config = kwargs.get("config")
        runtime_settings = self._read_runtime_config(runtime_config)
        if not runtime_settings.api_key:
            raise RuntimeError(self._missing_api_key_message())

        image = self._to_rgb_image(image)
        original_size = image.size
        request_image, restore_info = prepare_square_ai_image(image)
        prompt_text, reference_images = self._build_colorizer_request(image, kwargs)
        semaphore = _get_colorizer_semaphore(self.PROVIDER_NAME, self._resolve_concurrency(kwargs))
        async with semaphore:
            async def _request_with_endpoint(endpoint) -> Image.Image:
                await self._ensure_client(runtime_config, runtime_settings=runtime_settings, endpoint=endpoint)
                if not self.client or not self.api_key:
                    raise RuntimeError(self._missing_api_key_message())
                return await self._request_colorized_image(
                    image=request_image,
                    prompt_text=prompt_text,
                    reference_images=reference_images,
                    runtime_config=runtime_config,
                )

            async def _do_request() -> Image.Image:
                return await run_with_api_candidates(
                    endpoints=runtime_settings.candidates,
                    strategy=runtime_settings.strategy,
                    operation=_request_with_endpoint,
                    provider_name=self.PROVIDER_NAME,
                    operation_name="colorization request",
                    logger=self.logger,
                    runtime_config=runtime_config,
                    on_candidate_error=self._reset_client_for_candidate,
                )

            result_image = await _do_request()

        result_image = restore_square_ai_image(self._to_rgb_image(result_image), restore_info)
        if result_image.size != original_size:
            result_image = result_image.resize(original_size, _LANCZOS)
        return result_image

    def _create_client(self, api_key: str, base_url: str):
        raise NotImplementedError

    async def _request_colorized_image(
        self,
        image: Image.Image,
        prompt_text: str,
        reference_images: list[dict[str, bytes | str]],
        runtime_config=None,
    ) -> Image.Image:
        raise NotImplementedError


class OpenAIColorizer(BaseAPIColorizer):
    API_KEY_ENV = "COLOR_OPENAI_API_KEY"
    API_BASE_ENV = "COLOR_OPENAI_API_BASE"
    MODEL_ENV = "COLOR_OPENAI_MODEL"
    FALLBACK_API_KEY_ENV = "OPENAI_API_KEY"
    FALLBACK_API_BASE_ENV = "OPENAI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-image-1"
    BROWSER_HEADERS = OPENAI_BROWSER_HEADERS
    PROVIDER_NAME = "OpenAI Colorizer"
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

    async def _request_colorized_image(
        self,
        image: Image.Image,
        prompt_text: str,
        reference_images: list[dict[str, bytes | str]],
        runtime_config=None,
    ) -> Image.Image:
        custom_api_params = resolve_custom_api_params(
            runtime_config,
            self.logger,
            model_name=self.model_name,
            section="colorizer",
        )
        request_params = normalize_openai_image_request_params(custom_api_params)
        return await request_openai_image_with_fallback(
            session=self.client.session,
            base_url=self.base_url,
            api_key=self.api_key,
            default_headers=self.BROWSER_HEADERS,
            model_name=self.model_name,
            prompt_text=prompt_text,
            image_bytes=self._image_to_png_bytes(image),
            filename="page.png",
            timeout=600.0,
            fetch_remote_image=self._fetch_image_from_url,
            provider_name=self.PROVIDER_NAME,
            logger=self.logger,
            extra_images=reference_images,
            extra_request_params=request_params,
        )


class GeminiColorizer(BaseAPIColorizer):
    API_KEY_ENV = "COLOR_GEMINI_API_KEY"
    API_BASE_ENV = "COLOR_GEMINI_API_BASE"
    MODEL_ENV = "COLOR_GEMINI_MODEL"
    FALLBACK_API_KEY_ENV = "GEMINI_API_KEY"
    FALLBACK_API_BASE_ENV = "GEMINI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL = "gemini-2.0-flash-preview-image-generation"
    BROWSER_HEADERS = GEMINI_BROWSER_HEADERS
    PROVIDER_NAME = "Gemini Colorizer"
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

    async def _request_colorized_image(
        self,
        image: Image.Image,
        prompt_text: str,
        reference_images: list[dict[str, bytes | str]],
        runtime_config=None,
    ) -> Image.Image:
        image_b64 = base64.b64encode(self._image_to_png_bytes(image)).decode("ascii")
        parts = [
            {"text": prompt_text},
            {"text": "Target image to colorize:"},
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": image_b64,
                }
            },
        ]
        for idx, ref in enumerate(reference_images, start=1):
            parts.append({"text": f"Reference image {idx}: {ref.get('label') or f'Reference {idx}'}"})
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(ref["image_bytes"]).decode("ascii"),
                    }
                }
            )
        custom_api_params = resolve_custom_api_params(
            runtime_config,
            self.logger,
            model_name=self.model_name,
            section="colorizer",
        )
        request_overrides, generation_overrides = split_gemini_request_params(custom_api_params)
        generation_config = {
            "responseModalities": ["TEXT", "IMAGE"],
        }
        generation_config.update(generation_overrides)
        request_kwargs = {
            "model": self.model_name,
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": generation_config,
            "safetySettings": self.SAFETY_SETTINGS,
        }
        request_kwargs.update(request_overrides)
        response = await self.client.models.generate_content(**request_kwargs)
        image_result = self._extract_gemini_image(response)
        if image_result is None:
            raise RuntimeError("Gemini colorization response did not contain an image.")
        return image_result

def _ordinal_label(index: int) -> str:
    labels = {
        1: "first",
        2: "second",
        3: "third",
        4: "fourth",
        5: "fifth",
        6: "sixth",
        7: "seventh",
        8: "eighth",
        9: "ninth",
        10: "tenth",
    }
    return labels.get(index, f"{index}th")
