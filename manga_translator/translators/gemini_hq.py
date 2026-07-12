# import re
import asyncio
import os

# import base64
from io import BytesIO
from typing import Any, Dict, List

from google.genai import types
from PIL import Image

from ..api_key_rotation import APIRotationExhaustedError, run_with_api_candidates
from ..runtime_api_resolver import resolve_runtime_api_config
from ..utils.dotenv_utils import load_app_dotenv
from ..utils.image_modes import normalize_rgb_image
from .common import (
    VALID_LANGUAGES,
    AsyncGeminiCurlCffi,
    CommonTranslator,
    draw_text_boxes_on_image,
    extract_gemini_response_diagnostics,
    format_gemini_response_diagnostics,
    gemini_diagnostics_indicate_safety,
    gemini_diagnostics_should_disable_images,
    gemini_error_message_indicates_safety,
    merge_glossary_to_file,
    parse_hq_response,
    validate_gemini_response,
)

# 浏览器风格的请求头，避免被 CF 拦截
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Origin": "https://aistudio.google.com",
    "Referer": "https://aistudio.google.com/",
}


def encode_image_for_gemini(image, max_size=1024):
    """将图片处理为适合Gemini API的格式，返回bytes和mime_type"""
    image = normalize_rgb_image(image)

    # 调整图片大小
    w, h = image.size
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)

    # 转换为 JPEG bytes
    buffer = BytesIO()
    image.save(buffer, format='JPEG', quality=85)
    image_bytes = buffer.getvalue()
    
    return image_bytes, 'image/jpeg'


class GeminiHighQualityTranslator(CommonTranslator):
    """
    Gemini高质量翻译器
    支持多图片批量处理，提供文本框顺序、原文和原图给AI进行更精准的翻译
    """
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES
    API_KEY_ENV = "GEMINI_API_KEY"
    API_BASE_ENV = "GEMINI_API_BASE"
    MODEL_ENV = "GEMINI_MODEL"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL_NAME = "gemini-1.5-flash"
    LOG_PROVIDER_NAME = "Gemini HQ"
    LOG_PROVIDER_NAME_ZH = "Gemini高质量翻译"
    STREAM_LOG_PREFIX = "[Gemini HQ Stream]"
    
    # 类变量: 跨实例共享的RPM限制时间戳
    _GLOBAL_LAST_REQUEST_TS = {}  # {model_name: timestamp}
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.prev_context = ""  # 用于存储多页上下文
        # Initial setup from environment variables
        # 只在非Web环境下重新加载.env文件
        is_web_server = os.getenv('MANGA_TRANSLATOR_WEB_SERVER', 'false').lower() == 'true'
        if not is_web_server:
            load_app_dotenv(override=True)
        
        self.api_key = os.getenv(self.API_KEY_ENV, '')
        self.base_url = os.getenv(self.API_BASE_ENV, self.DEFAULT_BASE_URL) if self.API_BASE_ENV else self.DEFAULT_BASE_URL
        self.model_name = os.getenv(self.MODEL_ENV, self.DEFAULT_MODEL_NAME)
        self._runtime_api_settings = None
        self._refresh_runtime_api_settings(None)
        self.max_tokens = None  # 不限制，使用模型默认最大值
        self._MAX_REQUESTS_PER_MINUTE = 0  # 默认无限制
        # 使用全局时间戳,跨实例共享
        if self.model_name not in type(self)._GLOBAL_LAST_REQUEST_TS:
            type(self)._GLOBAL_LAST_REQUEST_TS[self.model_name] = 0
        self._last_request_ts_key = self.model_name
        # 新版 SDK 的安全设置
        self.safety_settings = [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
        ]
        self._setup_client()

    def _log_provider_name(self) -> str:
        return self.LOG_PROVIDER_NAME

    def _log_provider_name_zh(self) -> str:
        return self.LOG_PROVIDER_NAME_ZH
    
    def set_prev_context(self, context: str):
        """设置多页上下文（用于context_size > 0时）"""
        self.prev_context = context if context else ""
    
    def parse_args(self, args):
        """解析配置参数"""
        # 调用父类的 parse_args 来设置通用参数（包括 attempts、post_check 等）
        super().parse_args(args)
        translator_args = self._resolve_translator_config(args)
        
        # 同步重试次数到“总尝试次数”（首次请求 + 重试）
        self._max_total_attempts = self._resolve_max_total_attempts()
        
        # 从配置中读取RPM限制
        max_rpm = self._get_config_value(translator_args, 'max_requests_per_minute', 0)
        if max_rpm > 0:
            self._MAX_REQUESTS_PER_MINUTE = max_rpm
            self.logger.info(f"Setting {self._log_provider_name()} max requests per minute to: {max_rpm}")
        
        # 读取自定义API参数配置
        self._configure_custom_api_params(args)
        
        # 从配置中读取用户级 API Key（优先于环境变量）
        # 这允许 Web 服务器为每个用户使用不同的 API Key
        need_rebuild_client = False
        
        user_api_key = self._get_config_value(translator_args, 'user_api_key', None)
        if user_api_key and user_api_key != self.api_key:
            self.api_key = user_api_key
            need_rebuild_client = True
            self.logger.info(f"[UserAPIKey] Using user-provided API key for {self._log_provider_name()}")
        
        user_api_base = self._get_config_value(translator_args, 'user_api_base', None)
        if user_api_base and user_api_base != self.base_url:
            self.base_url = user_api_base
            need_rebuild_client = True
            self.logger.info(f"[UserAPIKey] Using user-provided API base: {user_api_base}")
        
        user_api_model = self._get_config_value(translator_args, 'user_api_model', None)
        if user_api_model:
            self.model_name = user_api_model
            # 更新全局时间戳的 key
            if self.model_name not in type(self)._GLOBAL_LAST_REQUEST_TS:
                type(self)._GLOBAL_LAST_REQUEST_TS[self.model_name] = 0
            self._last_request_ts_key = self.model_name
            self.logger.info(f"[UserAPIKey] Using user-provided model: {user_api_model}")

        if user_api_key or user_api_base or user_api_model:
            self._runtime_api_settings = None
        else:
            old_signature = (self.api_key or "", self.base_url, self.model_name)
            self._refresh_runtime_api_settings(args)
            if (self.api_key or "", self.base_url, self.model_name) != old_signature:
                need_rebuild_client = True

        # 如果 API Key 或 Base URL 变化，重建客户端
        if need_rebuild_client:
            self.client = None
            self._setup_client()

    def _refresh_runtime_api_settings(self, config):
        settings = resolve_runtime_api_config(
            config,
            feature="translator",
            provider="gemini",
            api_key_env=self.API_KEY_ENV,
            api_base_env=self.API_BASE_ENV,
            model_env=self.MODEL_ENV,
            fallback_api_key_env=None,
            fallback_api_base_env=None,
            fallback_model_env=None,
            default_api_base=self.DEFAULT_BASE_URL,
            default_model=self.DEFAULT_MODEL_NAME,
            allow_empty_local_api_key=False,
        )
        self._runtime_api_settings = settings
        if settings.api_key:
            self.api_key = settings.api_key
            self.base_url = settings.base_url
            self.model_name = settings.model_name
        return settings

    async def _close_current_client(self):
        if not self.client:
            return
        close_fn = getattr(self.client, "close", None)
        try:
            if callable(close_fn):
                close_result = close_fn()
                if asyncio.iscoroutine(close_result):
                    await close_result
        except Exception:
            pass
        finally:
            self.client = None

    async def _reset_client_for_candidate(self, endpoint, error: Exception):
        del endpoint, error
        await self._close_current_client()

    def _apply_api_endpoint(self, endpoint):
        signature = (endpoint.api_key or "", endpoint.base_url, endpoint.model_name)
        if signature == (self.api_key or "", self.base_url, self.model_name) and self.client:
            return
        self.api_key = endpoint.api_key
        self.base_url = endpoint.base_url
        self.model_name = endpoint.model_name
        if self.model_name not in type(self)._GLOBAL_LAST_REQUEST_TS:
            type(self)._GLOBAL_LAST_REQUEST_TS[self.model_name] = 0
        self._last_request_ts_key = self.model_name
        self.client = None
        self._setup_client()

    async def _run_with_api_rotation(self, operation, operation_name: str):
        settings = self._runtime_api_settings
        if not settings or not settings.candidates:
            if not self.client:
                self._setup_client()
            return await operation()

        async def _with_endpoint(endpoint):
            self._apply_api_endpoint(endpoint)
            return await operation()

        return await run_with_api_candidates(
            endpoints=settings.candidates,
            strategy=settings.strategy,
            operation=_with_endpoint,
            provider_name=self._log_provider_name(),
            operation_name=operation_name,
            logger=self.logger,
            retry_attempts=self.attempts,
            on_candidate_error=self._reset_client_for_candidate,
        )

    def _setup_client(self, system_instruction=None):
        """设置高质量翻译客户端"""
        if not self.client and self.api_key:
            # 检查是否使用自定义 API Base
            is_custom_api = (
                self.base_url
                and self.base_url.strip()
                and self.base_url.strip() not in ["https://generativelanguage.googleapis.com", "https://generativelanguage.googleapis.com/"]
            )

            if is_custom_api:
                self.client = AsyncGeminiCurlCffi(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    default_headers=BROWSER_HEADERS,
                    impersonate="chrome110",
                    timeout=600,
                    stream_timeout=300
                )
                self._use_curl_cffi = True
                self.logger.info(
                    f"{self._log_provider_name()}客户端初始化完成（强制 curl_cffi，自定义API Base）。Base URL: {self.base_url}"
                )
            else:
                self.client = AsyncGeminiCurlCffi(
                    api_key=self.api_key,
                    default_headers=BROWSER_HEADERS,
                    impersonate="chrome110",
                    timeout=600,
                    stream_timeout=300
                )
                self._use_curl_cffi = True
                self.logger.info(f"{self._log_provider_name()}客户端初始化完成（强制 curl_cffi 模式）")

            self.logger.info("安全设置策略：默认发送 OFF，如遇错误自动回退")

    async def _abort_inflight_request(self):
        """取消时尝试关闭当前客户端连接，尽快中断阻塞请求。"""
        if not self.client:
            return

        close_fn = getattr(self.client, "close", None)
        try:
            if callable(close_fn):
                close_result = close_fn()
                if asyncio.iscoroutine(close_result):
                    await close_result
        except Exception as e:
            self.logger.debug(f"中断{self._log_provider_name()}请求时关闭客户端失败（可忽略）: {e}")
        finally:
            self.client = None


    def _build_user_prompt(self, batch_data: List[Dict], ctx: Any, retry_attempt: int = 0, retry_reason: str = "") -> str:
        """构建用户提示词（高质量版）- 使用统一方法，只包含当前待翻译文本"""
        return self._build_user_prompt_for_hq(batch_data, ctx, "", retry_attempt=retry_attempt, retry_reason=retry_reason)
    
    def _get_system_instruction(self, source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None, retry_attempt: int = 0, retry_reason: str = "", extract_glossary: bool = False) -> str:
        """获取完整的系统指令（包含断句提示词、自定义提示词和基础系统提示词）"""
        # 构建系统提示词（包含所有指令）
        return self._build_system_prompt(source_lang, target_lang, custom_prompt_json=custom_prompt_json, line_break_prompt_json=line_break_prompt_json, retry_attempt=retry_attempt, retry_reason=retry_reason, extract_glossary=extract_glossary)

    async def _translate_batch_high_quality(self, texts: List[str], batch_data: List[Dict], source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None, ctx: Any = None, split_level: int = 0) -> List[str]:
        """高质量批量翻译方法"""
        if not texts:
            return []
        if batch_data is None:
            batch_data = []
        
        # 保存参数供重试时使用
        _source_lang = source_lang
        _target_lang = target_lang
        _custom_prompt_json = custom_prompt_json
        _line_break_prompt_json = line_break_prompt_json
        
        # 打印输入的原文
        self.logger.info("--- Original Texts for Translation ---")
        for i, text in enumerate(texts):
            self.logger.info(f"{i+1}: {text}")
        self.logger.info("------------------------------------")

        # 打印图片信息
        self.logger.info("--- Image Info ---")
        for i, data in enumerate(batch_data):
            image = data.get('image')
            if image is None:
                self.logger.info(f"Image {i+1}: missing image data (None), skip upload")
                continue
            image_size = getattr(image, "size", None)
            image_mode = getattr(image, "mode", None)
            self.logger.info(f"Image {i+1}: size={image_size}, mode={image_mode}")
        self.logger.info("--------------------")

        # 准备图片列表（放在最后）- 使用新版 SDK 的 Part 格式
        image_parts = []
        for i, data in enumerate(batch_data):
            image = data.get('image')
            if image is None:
                self.logger.debug(f"图片[{i + 1}] 缺少图像数据，跳过图片上传")
                continue
            
            # 在图片上绘制带编号的文本框
            text_regions = data.get('text_regions', [])
            text_order = data.get('text_order', [])
            upscaled_size = data.get('upscaled_size')
            if text_regions and text_order:
                image = draw_text_boxes_on_image(image, text_regions, text_order, upscaled_size)
                self.logger.debug(f"已在图片上绘制 {len(text_regions)} 个带编号的文本框")
            
            # 使用新版 SDK 的格式
            try:
                image_bytes, mime_type = encode_image_for_gemini(image)
                image_parts.append(
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type).model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                )
            except Exception as image_error:
                self.logger.warning(f"图片[{i + 1}] 处理失败，跳过上传: {image_error}")
                continue
        
        # 初始化重试信息
        retry_attempt = 0
        retry_reason = ""
        
        # 发送请求
        max_retries = self._resolve_max_total_attempts()
        attempt = 0
        is_infinite = max_retries == -1
        last_exception = None
        local_attempt = 0  # 本次批次的尝试次数
        
        # 标记是否需要回退（不发送安全设置）
        should_retry_without_safety = False
        
        # 标记是否发送图片（降级机制）
        send_images = len(image_parts) > 0
        if not send_images:
            self.logger.info(f"未提供可用图片，{self._log_provider_name()}将使用纯文本请求模式")

        while is_infinite or attempt < max_retries:
            # 检查是否被取消
            self._check_cancelled()
            
            # 检查全局尝试次数
            if not self._increment_global_attempt():
                self.logger.error("Reached global attempt limit. Stopping translation.")
                # 包含最后一次错误的真正原因
                last_error_msg = str(last_exception) if last_exception else "Unknown error"
                raise Exception(f"达到最大尝试次数 ({self._max_total_attempts})，最后一次错误: {last_error_msg}")

            local_attempt += 1
            attempt += 1

            # 文本分割逻辑已禁用
            # if local_attempt > self._SPLIT_THRESHOLD and len(texts) > 1 and split_level < self._MAX_SPLIT_ATTEMPTS:
            #     self.logger.warning(f"Triggering split after {local_attempt} local attempts")
            #     raise self.SplitException(local_attempt, texts)
            
            # 确定是否开启术语提取
            # 必须同时满足：1. 有自定义提示词（才有地方存） 2. 配置开启了提取开关
            config_extract = False
            if ctx and hasattr(ctx, 'config') and hasattr(ctx.config, 'translator'):
                config_extract = getattr(ctx.config.translator, 'extract_glossary', False)
            
            extract_glossary = bool(_custom_prompt_json) and config_extract

            # 获取系统指令（通过 systemInstruction 发送）
            system_instruction = self._get_system_instruction(_source_lang, _target_lang, custom_prompt_json=_custom_prompt_json, line_break_prompt_json=_line_break_prompt_json, retry_attempt=retry_attempt, retry_reason=retry_reason, extract_glossary=extract_glossary)
            
            # 初始化客户端（不传入 system_instruction）
            if not self.client:
                self._setup_client(system_instruction=None)
            
            if not self.client:
                raise RuntimeError(
                    f"{self._log_provider_name()}客户端初始化失败：请检查 "
                    f"{self.API_KEY_ENV} / {self.API_BASE_ENV} / {self.MODEL_ENV} 配置"
                )
            
            # 构建用户提示词（包含重试信息以避免缓存）
            user_prompt = self._build_user_prompt(batch_data, ctx, retry_attempt=retry_attempt, retry_reason=retry_reason)
            contents = self._build_gemini_context_messages(self.prev_context)

            current_user_parts = [{"text": user_prompt}]
            if send_images:
                current_user_parts.extend(image_parts)
            else:
                if retry_attempt > 0: # 仅在重试且被标记为不发图时打印
                     self.logger.warning("降级模式：仅发送文本，不发送图片")
            contents.append({"role": "user", "parts": current_user_parts})
            
            # 构建生成配置
            config_params = {
                "top_p": 0.95,
                "top_k": 64,
                "safety_settings": None if should_retry_without_safety else self.safety_settings,
            }
            # 只在 max_tokens 不为 None 时才设置（兼容新模型）
            if self.max_tokens is not None:
                config_params["max_output_tokens"] = self.max_tokens
            
            generation_config = types.GenerateContentConfig(**config_params)
            generation_config.system_instruction = system_instruction
            
            # 合并自定义API参数
            if self._custom_api_params:
                for key, value in self._custom_api_params.items():
                    if hasattr(generation_config, key):
                        setattr(generation_config, key, value)

            try:
                # RPM限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    import time
                    now = time.time()
                    delay = 60.0 / self._MAX_REQUESTS_PER_MINUTE
                    elapsed = now - type(self)._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key]
                    if elapsed < delay:
                        sleep_time = delay - elapsed
                        self.logger.info(f'Ratelimit sleep: {sleep_time:.2f}s')
                        await self._sleep_with_cancel_polling(sleep_time)
                
                def _extract_gemini_stream_text(chunk):
                    return getattr(chunk, "text", "") or ""
                
                def _on_stream_chunk(delta_text, _full_text):
                    self._emit_stream_json_preview(self.STREAM_LOG_PREFIX, delta_text, source_texts=texts)

                response = None
                streamed_text = None
                streamed_finish_reason = None
                streamed_diagnostics = None

                use_streaming = self._is_streaming_enabled(ctx)

                async def _send_gemini_request():
                    nonlocal response, streamed_text, streamed_finish_reason, streamed_diagnostics
                    response = None
                    streamed_text = None
                    streamed_finish_reason = None
                    streamed_diagnostics = None
                    if use_streaming:
                        try:
                            self._reset_stream_json_preview()
                            # 自动尝试流式；不支持时回退普通请求
                            def _extract_stream_finish_reason(chunk):
                                nonlocal streamed_diagnostics
                                streamed_diagnostics = extract_gemini_response_diagnostics(chunk)
                                return streamed_diagnostics.get('finish_reason')

                            streamed_text, streamed_finish_reason = await self._run_unified_stream_transport(
                                create_stream=lambda: self.client.models.generate_content_stream(
                                    model=self.model_name,
                                    contents=contents,
                                    config=generation_config
                                ),
                                extract_text=_extract_gemini_stream_text,
                                extract_finish_reason=_extract_stream_finish_reason,
                                on_chunk=_on_stream_chunk,
                                on_cancel=self._abort_inflight_request,
                                poll_interval=0.2,
                                sync_iter_in_thread=not getattr(self, '_use_curl_cffi', False),
                            )
                            self._finish_stream_inline()
                        except Exception as stream_error:
                            self._finish_stream_inline()
                            streamed_text = None
                            streamed_finish_reason = None
                            streamed_diagnostics = None
                            self.logger.warning(f"流式请求不可用，已回退普通请求: {stream_error}")
                            # 使用标准 SDK（同步调用包装为异步）
                            if getattr(self, '_use_curl_cffi', False):
                                response = await self._await_with_cancel_polling(
                                    self.client.models.generate_content(
                                        model=self.model_name,
                                        contents=contents,
                                        generation_config=generation_config,
                                        safety_settings=None if should_retry_without_safety else self.safety_settings
                                    ),
                                    poll_interval=0.2,
                                    on_cancel=self._abort_inflight_request,
                                )
                            else:
                                response = await self._await_with_cancel_polling(
                                    asyncio.to_thread(
                                        self.client.models.generate_content,
                                        model=self.model_name,
                                        contents=contents,
                                        config=generation_config
                                    ),
                                    poll_interval=0.2,
                                    on_cancel=self._abort_inflight_request,
                                )
                    else:
                        self.logger.info("已禁用流式传输，使用普通请求。")
                        if getattr(self, '_use_curl_cffi', False):
                            response = await self._await_with_cancel_polling(
                                self.client.models.generate_content(
                                    model=self.model_name,
                                    contents=contents,
                                    generation_config=generation_config,
                                    safety_settings=None if should_retry_without_safety else self.safety_settings
                                ),
                                poll_interval=0.2,
                                on_cancel=self._abort_inflight_request,
                            )
                        else:
                            response = await self._await_with_cancel_polling(
                                asyncio.to_thread(
                                    self.client.models.generate_content,
                                    model=self.model_name,
                                    contents=contents,
                                    config=generation_config
                                ),
                                poll_interval=0.2,
                                on_cancel=self._abort_inflight_request,
                            )

                    if streamed_text is not None:
                        if not streamed_text.strip():
                            raise RuntimeError(f"{self._log_provider_name()} returned empty content")
                    else:
                        validate_gemini_response(response, self.logger)
                        raw_text = getattr(response, "text", "")
                        if not (raw_text if isinstance(raw_text, str) else str(raw_text or "")).strip():
                            raise RuntimeError(f"{self._log_provider_name()} returned empty content")

                await self._run_with_api_rotation(_send_gemini_request, "translation request")

                # 在API调用成功后立即更新时间戳，确保所有请求（包括重试）都被计入速率限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    import time
                    type(self)._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key] = time.time()

                if streamed_text is None:
                    # 验证响应对象是否有效
                    validate_gemini_response(response, self.logger)

                diagnostics = streamed_diagnostics or extract_gemini_response_diagnostics(
                    response,
                    fallback_finish_reason=streamed_finish_reason if streamed_text is not None else None,
                )
                diagnostics_text = format_gemini_response_diagnostics(diagnostics)
                finish_reason = diagnostics.get('finish_reason')
                finish_reason_str = diagnostics.get('finish_reason_str') or ""
                if finish_reason and "STOP" not in finish_reason_str.upper():  # 不是成功
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"

                    self.logger.warning(f"{self._log_provider_name()} API失败 ({log_attempt}): {diagnostics_text}")
                    
                    if gemini_diagnostics_should_disable_images(diagnostics):
                        self.logger.warning(f"检测到{self._log_provider_name()}阻断或未知结束状态，下次重试将不再发送图片。")
                        send_images = False
                    if gemini_diagnostics_indicate_safety(diagnostics) and not should_retry_without_safety:
                        self.logger.warning(f"检测到{self._log_provider_name()}安全策略拦截，下次重试将移除安全设置参数。")
                        should_retry_without_safety = True

                    if not is_infinite and attempt >= max_retries:
                        self.logger.error(f"{self._log_provider_name()}翻译在多次重试后仍失败: {diagnostics_text}")
                        break
                    await self._sleep_with_cancel_polling(1)
                    continue

                # 兼容 text 为 None/非字符串的场景，避免 .strip() 崩溃
                if streamed_text is not None:
                    result_text = streamed_text.strip()
                else:
                    raw_text = getattr(response, "text", "")
                    result_text = (raw_text if isinstance(raw_text, str) else str(raw_text or "")).strip()
                
                # 统一的编码清理（处理UTF-16-LE等编码问题）
                from .common import sanitize_text_encoding
                result_text = sanitize_text_encoding(result_text)
                
                self.logger.debug(f"--- {self._log_provider_name()} Raw Response ---\n{result_text}\n---------------------------")
                if not result_text:
                    self.logger.warning(f"{self._log_provider_name()}返回空内容 ({diagnostics_text})，下次重试将不再发送图片")
                    send_images = False
                    if gemini_diagnostics_indicate_safety(diagnostics) and not should_retry_without_safety:
                        self.logger.warning(f"空响应伴随{self._log_provider_name()}安全策略信息，下次重试将移除安全设置参数。")
                        should_retry_without_safety = True
                    raise Exception(f"{self._log_provider_name()} returned empty content ({diagnostics_text})")


                # 使用通用函数解析响应（支持JSON和纯文本，以及术语提取）
                translations, new_terms = parse_hq_response(result_text)
                
                # 处理提取到的术语
                if extract_glossary and new_terms:
                    self._emit_terms_from_list(new_terms)
                    prompt_path = None
                    if ctx and hasattr(ctx, 'config') and hasattr(ctx.config, 'translator'):
                        prompt_path = getattr(ctx.config.translator, 'high_quality_prompt_path', None)
                    
                    if prompt_path:
                        merge_glossary_to_file(prompt_path, new_terms)
                    else:
                        self.logger.warning("Extracted new terms but prompt path not found in context.")
                
                # Strict validation: must match input count
                if len(translations) != len(texts):
                    retry_attempt += 1
                    retry_reason = f"Translation count mismatch: expected {len(texts)}, got {len(translations)}"
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] {retry_reason}. Retrying...")
                    self.logger.warning(f"Expected texts: {texts}")
                    self.logger.warning(f"Got translations: {translations}")
                    
                    # 记录错误以便在达到最大尝试次数时显示
                    last_exception = Exception(f"翻译数量不匹配: 期望 {len(texts)} 条，实际得到 {len(translations)} 条")

                    if not is_infinite and attempt >= max_retries:
                        raise Exception(f"Translation count mismatch after {max_retries} attempts: expected {len(texts)}, got {len(translations)}")

                    await self._sleep_with_cancel_polling(2)
                    continue

                # 质量验证：检查空翻译、合并翻译、可疑符号等
                is_valid, error_msg = self._validate_translation_quality(texts, translations)
                if not is_valid:
                    retry_attempt += 1
                    retry_reason = f"Quality check failed: {error_msg}"
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] {retry_reason}. Retrying...")
                    
                    # 记录错误以便在达到最大尝试次数时显示
                    last_exception = Exception(f"翻译质量检查失败: {error_msg}")

                    if not is_infinite and attempt >= max_retries:
                        raise Exception(f"Quality check failed after {max_retries} attempts: {error_msg}")

                    await self._sleep_with_cancel_polling(2)
                    continue

                # 打印原文和译文的对应关系
                self._emit_final_translation_results(texts, translations)

                # BR检查：检查翻译结果是否包含必要的[BR]标记
                # BR check: Check if translations contain necessary [BR] markers
                if not self._validate_br_markers(translations, batch_data=batch_data, ctx=ctx):
                    retry_attempt += 1
                    retry_reason = "BR markers missing in translations"
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] {retry_reason}, retrying...")
                    
                    # 记录错误以便在达到最大尝试次数时显示
                    last_exception = Exception("AI断句检查失败: 翻译结果缺少必要的[BR]标记")
                    
                    # 如果达到最大重试次数，抛出友好的异常
                    if not is_infinite and attempt >= max_retries:
                        from .common import BRMarkersValidationException
                        self.logger.error(f"{self._log_provider_name_zh()}在多次重试后仍然失败：AI断句检查失败。")
                        raise BRMarkersValidationException(
                            missing_count=0,  # 具体数字在_validate_br_markers中已记录
                            total_count=len(texts),
                            tolerance=max(1, len(texts) // 10)
                        )
                    
                    await self._sleep_with_cancel_polling(2)
                    continue

                return translations[:len(texts)]

            except APIRotationExhaustedError:
                raise
            except Exception as e:
                # 检查是否是400错误或多模态不支持问题
                error_message = str(e)
                last_exception = e  # 保存最后一次错误
                is_bad_request = '400' in error_message or 'BadRequest' in error_message
                is_multimodal_unsupported = any(keyword in error_message.lower() for keyword in [
                    'image_url', 'multimodal', 'vision', 'expected `text`', 'unknown variant', 'does not support'
                ])
                is_empty_content = 'returned empty content' in error_message.lower()
                
                # 降级检查：502错误、安全设置错误或400错误（非多模态不支持）
                is_502_error = '502' in error_message
                is_safety_error = any(keyword in error_message.lower() for keyword in [
                    'safety_settings', 'safetysettings', 'harm', 'block', 'safety'
                ]) or ("400" in error_message and not is_multimodal_unsupported) or gemini_error_message_indicates_safety(error_message)

                if is_502_error or is_safety_error or is_empty_content:
                     if is_empty_content:
                         self.logger.warning(f"检测到空响应，下次重试将不再发送图片。错误信息: {error_message}")
                     else:
                         self.logger.warning(f"检测到网络错误(502)或安全设置错误，下次重试将不再发送图片。错误信息: {error_message}")
                     send_images = False

                if is_bad_request and is_multimodal_unsupported:
                    self.logger.error(f"❌ 模型 {self.model_name} 不支持多模态输入（图片+文本）")
                    self.logger.error("💡 解决方案：")
                    self.logger.error(f"   1. 使用支持多模态的{self._log_provider_name()}模型")
                    self.logger.error("   2. 或者切换到普通翻译模式（不使用高质量翻译器）")
                    self.logger.error("   3. 检查第三方API是否支持图片输入")
                    raise Exception(f"模型不支持多模态输入: {self.model_name}") from e
                
                # 如果是安全设置错误且还没有尝试回退，则标记回退
                if is_safety_error and not should_retry_without_safety:
                    self.logger.warning(f"检测到安全设置相关错误，将在下次重试时移除安全设置参数: {error_message}")
                    should_retry_without_safety = True
                    # 不增加attempt计数，直接重试
                    await self._sleep_with_cancel_polling(1)
                    continue
                    
                log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                self.logger.warning(f"{self._log_provider_name_zh()}出错 ({log_attempt}): {e}")

                if gemini_error_message_indicates_safety(error_message):
                    self.logger.warning(f"检测到{self._log_provider_name()}安全策略拦截。正在重试...")
                    send_images = False # 显式确保降级
                
                # 检查是否达到最大重试次数
                if not is_infinite and attempt >= max_retries:
                    self.logger.error(f"{self._log_provider_name()}翻译在多次重试后仍然失败。即将终止程序。")
                    raise e
                
                await self._sleep_with_cancel_polling(1)
        
        raise last_exception if last_exception else RuntimeError(
            f"{self._log_provider_name()} translation failed without a response"
        )

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str], ctx=None) -> List[str]:
        """主翻译方法"""
        if not self.client:
            from .. import manga_translator
            if hasattr(manga_translator, 'config'):
                self.parse_args(manga_translator.config)

        if not queries:
            return []

        # 重置全局尝试计数器
        self._reset_global_attempt_count()

        batch_data = getattr(ctx, 'high_quality_batch_data', None) if ctx else None
        if not batch_data:
            # 统一后备路径：仍走高质量批量函数，不再保留第二套 API 请求实现
            self.logger.info(f"{self._log_provider_name()}未提供batch_data，使用统一后备批次路径")
            fallback_regions = getattr(ctx, 'text_regions', []) if ctx else []
            batch_data = [{
                'image': getattr(ctx, 'input', None) if ctx else None,
                'text_regions': fallback_regions if fallback_regions else [],
                'text_order': list(range(1, len(queries) + 1)),
                'upscaled_size': None,
                'original_texts': queries,
            }]

        self.logger.info(
            f"使用{self._log_provider_name_zh()}统一路径，批次图片数: {len(batch_data)}，最大尝试次数: {self._max_total_attempts}"
        )
        custom_prompt_json = getattr(ctx, 'custom_prompt_json', None)
        line_break_prompt_json = getattr(ctx, 'line_break_prompt_json', None)

        # 使用分割包装器进行翻译
        translations = await self._translate_with_split(
            self._translate_batch_high_quality,
            queries,
            split_level=0,
            batch_data=batch_data,
            source_lang=from_lang,
            target_lang=to_lang,
            custom_prompt_json=custom_prompt_json,
            line_break_prompt_json=line_break_prompt_json,
            ctx=ctx
        )

        # 应用文本后处理（与普通翻译器保持一致）
        translations = [self._clean_translation_output(q, r, to_lang) for q, r in zip(queries, translations)]
        return translations
