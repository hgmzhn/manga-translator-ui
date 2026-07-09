import asyncio
import base64
import io
import os
from typing import List, Optional

import numpy as np
from PIL import Image

from ..config import Renderer
from ..custom_api_params import (
    load_enabled_custom_api_params,
    split_gemini_request_params,
)
from ..api_key_rotation import run_with_api_candidates
from ..runtime_api_resolver import resolve_runtime_api_config
from ..utils import TextBlock, get_logger
from ..utils.dotenv_utils import load_app_dotenv
from ..utils.ai_image_preprocess import (
    normalize_ai_image,
    prepare_square_ai_image,
    restore_square_ai_image,
)
from ..utils.openai_image_interface import request_openai_image_with_fallback
from .prompt_loader import (
    DEFAULT_AI_RENDERER_PROMPT,
    ensure_ai_renderer_prompt_file,
    load_ai_renderer_prompt_file,
)
from .rich_text import plain_text_of

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

_RENDERER_SEMAPHORES: dict[str, tuple[int, asyncio.Semaphore]] = {}
_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _get_renderer_semaphore(name: str, concurrency: int) -> asyncio.Semaphore:
    current = _RENDERER_SEMAPHORES.get(name)
    if current is None or current[0] != concurrency:
        current = (concurrency, asyncio.Semaphore(concurrency))
        _RENDERER_SEMAPHORES[name] = current
    return current[1]


class BaseAPIRenderer:
    API_KEY_ENV = ""
    API_BASE_ENV = ""
    MODEL_ENV = ""
    FALLBACK_API_KEY_ENV = ""
    FALLBACK_API_BASE_ENV = ""
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = ""
    DEFAULT_MODEL = ""
    BROWSER_HEADERS = {}
    PROVIDER_NAME = "API Renderer"
    ALLOW_EMPTY_LOCAL_API_KEY = False
    RUNTIME_PROVIDER = ""

    def __init__(self):
        self.logger = get_logger("render")
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
            feature="renderer",
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

    async def ensure_client(self, runtime_config=None, runtime_settings=None, endpoint=None):
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

    def _build_base_prompt(self) -> str:
        ensure_ai_renderer_prompt_file()
        return load_ai_renderer_prompt_file(None) or DEFAULT_AI_RENDERER_PROMPT

    def _format_prompt_value(self, value) -> str:
        # 富文本→纯文本统一走 rich_text.plain_text_of
        return plain_text_of(value).replace("\r\n", "\n").replace("\n", "\\n").strip()

    def _compose_render_prompt(
        self,
        text_regions: List[TextBlock],
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> str:
        base_prompt = self._build_base_prompt().strip()
        lines = [base_prompt, "", "Translation list with original texts as reference:"]
        for region in text_regions:
            translation = self._format_prompt_value(getattr(region, "translation", "") or "")
            if not translation:
                continue
            original = self._format_prompt_value(getattr(region, "text", "") or "")
            direction = "vertical" if region.vertical else "horizontal"
            lines.append(f"- translation: {translation}")
            if original:
                lines.append(f"  original: {original}")
            lines.append(f"  direction: {direction}")
        lines.extend(
            [
                "",
                "Rules:",
                "- Match each translated line to the corresponding bubble on the image using the original text as reference.",
                "- Render every provided translation, including sound effects and onomatopoeia.",
                "- Keep the page layout and artwork intact.",
                "- Return only the fully rendered image.",
            ]
        )
        return "\n".join(lines)

    def _resolve_concurrency(self, config) -> int:
        render_config = getattr(config, "render", None)
        try:
            return max(int(getattr(render_config, "ai_renderer_concurrency", 1) or 1), 1)
        except (TypeError, ValueError):
            return 1

    def _to_pil(self, image) -> Image.Image:
        if not isinstance(image, Image.Image):
            image = Image.fromarray(np.asarray(image).astype(np.uint8))
        return normalize_ai_image(image)

    def _image_to_png_bytes(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        self._to_pil(image).save(buffer, format="PNG")
        return buffer.getvalue()

    async def _fetch_image_from_url(self, url: str) -> Image.Image:
        response = await self.client.session.get(url, timeout=600.0)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to download rendered image: HTTP {response.status_code}")
        return normalize_ai_image(Image.open(io.BytesIO(response.content)))

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
                return normalize_ai_image(Image.open(io.BytesIO(base64.b64decode(data))))
        return None

    async def render(self, img: np.ndarray, text_regions: List[TextBlock], config) -> np.ndarray:
        runtime_settings = self._read_runtime_config(config)
        if not runtime_settings.api_key:
            raise RuntimeError(self._missing_api_key_message())

        renderable_regions = [
            region for region in text_regions
            if self._format_prompt_value(getattr(region, "translation", ""))
        ]
        if not renderable_regions:
            return img

        request_image, restore_info = prepare_square_ai_image(self._to_pil(img))
        prompt_text = self._compose_render_prompt(
            renderable_regions,
            offset_x=restore_info.offset_x,
            offset_y=restore_info.offset_y,
        )
        custom_api_params = load_enabled_custom_api_params(config, self.logger, target="render")
        semaphore = _get_renderer_semaphore(self.PROVIDER_NAME, self._resolve_concurrency(config))

        async with semaphore:
            async def _request_with_endpoint(endpoint) -> Image.Image:
                await self.ensure_client(config, runtime_settings=runtime_settings, endpoint=endpoint)
                if not self.client or not self.api_key:
                    raise RuntimeError(self._missing_api_key_message())
                return await self._request_rendered_image(
                    image=request_image,
                    prompt_text=prompt_text,
                    custom_api_params=custom_api_params,
                )

            async def _do_request() -> Image.Image:
                return await run_with_api_candidates(
                    endpoints=runtime_settings.candidates,
                    strategy=runtime_settings.strategy,
                    operation=_request_with_endpoint,
                    provider_name=self.PROVIDER_NAME,
                    operation_name="render request",
                    logger=self.logger,
                    runtime_config=config,
                    on_candidate_error=self._reset_client_for_candidate,
                )

            result_image = await _do_request()

        result_image = restore_square_ai_image(self._to_pil(result_image), restore_info)
        if result_image.size != (img.shape[1], img.shape[0]):
            result_image = result_image.resize((img.shape[1], img.shape[0]), _LANCZOS)
        return np.array(result_image)

    def _create_client(self, api_key: str, base_url: str):
        raise NotImplementedError

    async def _request_rendered_image(
        self,
        image: Image.Image,
        prompt_text: str,
        custom_api_params: dict | None = None,
    ) -> Image.Image:
        raise NotImplementedError


class OpenAIRenderer(BaseAPIRenderer):
    API_KEY_ENV = "RENDER_OPENAI_API_KEY"
    API_BASE_ENV = "RENDER_OPENAI_API_BASE"
    MODEL_ENV = "RENDER_OPENAI_MODEL"
    FALLBACK_API_KEY_ENV = "OPENAI_API_KEY"
    FALLBACK_API_BASE_ENV = "OPENAI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-image-1"
    BROWSER_HEADERS = OPENAI_BROWSER_HEADERS
    PROVIDER_NAME = "OpenAI Renderer"
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

    async def _request_rendered_image(
        self,
        image: Image.Image,
        prompt_text: str,
        custom_api_params: dict | None = None,
    ) -> Image.Image:
        return await request_openai_image_with_fallback(
            session=self.client.session,
            base_url=self.base_url,
            api_key=self.api_key,
            default_headers=self.BROWSER_HEADERS,
            model_name=self.model_name,
            prompt_text=prompt_text,
            image_bytes=self._image_to_png_bytes(image),
            filename="numbered_page.png",
            timeout=600.0,
            fetch_remote_image=self._fetch_image_from_url,
            provider_name=self.PROVIDER_NAME,
            logger=self.logger,
            extra_request_params=custom_api_params,
        )


class GeminiRenderer(BaseAPIRenderer):
    API_KEY_ENV = "RENDER_GEMINI_API_KEY"
    API_BASE_ENV = "RENDER_GEMINI_API_BASE"
    MODEL_ENV = "RENDER_GEMINI_MODEL"
    FALLBACK_API_KEY_ENV = "GEMINI_API_KEY"
    FALLBACK_API_BASE_ENV = "GEMINI_API_BASE"
    FALLBACK_MODEL_ENV = ""
    DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL = "gemini-2.0-flash-preview-image-generation"
    BROWSER_HEADERS = GEMINI_BROWSER_HEADERS
    PROVIDER_NAME = "Gemini Renderer"
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

    async def _request_rendered_image(
        self,
        image: Image.Image,
        prompt_text: str,
        custom_api_params: dict | None = None,
    ) -> Image.Image:
        image_b64 = base64.b64encode(self._image_to_png_bytes(image)).decode("ascii")
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
            "generationConfig": generation_config,
            "safetySettings": self.SAFETY_SETTINGS,
        }
        request_kwargs.update(request_overrides)
        response = await self.client.models.generate_content(**request_kwargs)
        image_result = self._extract_gemini_image(response)
        if image_result is None:
            raise RuntimeError("Gemini renderer response did not contain an image.")
        return image_result

def get_api_renderer(key: Renderer) -> BaseAPIRenderer:
    if key == Renderer.openai_renderer:
        return OpenAIRenderer()
    elif key == Renderer.gemini_renderer:
        return GeminiRenderer()
    else:
        raise ValueError(f"Unsupported API renderer: {key}")


async def dispatch_api_rendering(img: np.ndarray, text_regions: List[TextBlock], config) -> np.ndarray:
    renderer = get_api_renderer(config.render.renderer)
    return await renderer.render(img=img, text_regions=text_regions, config=config)
