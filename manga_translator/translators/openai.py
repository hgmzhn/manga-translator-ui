import asyncio
import os
import re

# import json
from typing import Any, Dict, List

# import openai
from ..api_request_params import merge_openai_chat_request_params
from ..api_key_rotation import APIRotationExhaustedError, run_with_api_candidates
from ..runtime_api_resolver import resolve_runtime_api_config
from ..utils.dotenv_utils import load_app_dotenv
from .common import (
    VALID_LANGUAGES,
    AsyncOpenAICurlCffi,
    CommonTranslator,
    merge_glossary_to_file,
    parse_hq_response,
    validate_openai_response,
)
from .keys import OPENAI_API_KEY

# 浏览器风格的请求头，避免被 CF 拦截
# 注意：移除 Accept-Encoding 让 httpx 自动处理，避免压缩响应导致的 UTF-8 解码错误
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


class OpenAITranslator(CommonTranslator):
    """
    OpenAI纯文本翻译器
    支持批量文本翻译，不包含图片处理
    """
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES
    
    # 类变量: 跨实例共享的RPM限制时间戳
    _GLOBAL_LAST_REQUEST_TS = {}  # {model_name: timestamp}
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.prev_context = ""  # 用于存储多页上下文
        # 只在非Web环境下重新加载.env文件
        is_web_server = os.getenv('MANGA_TRANSLATOR_WEB_SERVER', 'false').lower() == 'true'
        if not is_web_server:
            load_app_dotenv(override=True)
        
        self.api_key = os.getenv('OPENAI_API_KEY', OPENAI_API_KEY)
        self.base_url = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')
        self.model = os.getenv('OPENAI_MODEL', "gpt-4o")
        self._runtime_api_settings = None
        self._refresh_runtime_api_settings(None)
        self.max_tokens = None  # 不限制，使用模型默认最大值
        self._MAX_REQUESTS_PER_MINUTE = 0  # 默认无限制
        # 使用全局时间戳,跨实例共享
        if self.model not in OpenAITranslator._GLOBAL_LAST_REQUEST_TS:
            OpenAITranslator._GLOBAL_LAST_REQUEST_TS[self.model] = 0
        self._last_request_ts_key = self.model
        self._setup_client()
    
    def set_prev_context(self, context: str):
        """设置多页上下文（用于context_size > 0时）"""
        self.prev_context = context if context else ""
    
    def parse_args(self, args):
        """解析配置参数"""
        # 调用父类的 parse_args 来设置通用参数（包括 attempts、post_check 等）
        super().parse_args(args)
        translator_args = self._resolve_translator_config(args)
        
        # 同步重试次数到"总尝试次数"（首次请求 + 重试）
        self._max_total_attempts = self._resolve_max_total_attempts()
        
        # 从配置中读取RPM限制
        max_rpm = self._get_config_value(translator_args, 'max_requests_per_minute', 0)
        if max_rpm > 0:
            self._MAX_REQUESTS_PER_MINUTE = max_rpm
            self.logger.info(f"Setting OpenAI max requests per minute to: {max_rpm}")
        
        # 读取自定义API参数配置
        self._configure_custom_api_params(args)
        
        # 从配置中读取用户级 API Key（优先于环境变量）
        need_rebuild_client = False
        
        user_api_key = self._get_config_value(translator_args, 'user_api_key', None)
        if user_api_key and user_api_key != self.api_key:
            self.api_key = user_api_key
            need_rebuild_client = True
            self.logger.info("[UserAPIKey] Using user-provided API key")
        
        user_api_base = self._get_config_value(translator_args, 'user_api_base', None)
        if user_api_base and user_api_base != self.base_url:
            self.base_url = user_api_base
            need_rebuild_client = True
            self.logger.info(f"[UserAPIKey] Using user-provided API base: {user_api_base}")
        
        user_api_model = self._get_config_value(translator_args, 'user_api_model', None)
        if user_api_model:
            self.model = user_api_model
            self.logger.info(f"[UserAPIKey] Using user-provided model: {user_api_model}")

        if user_api_key or user_api_base or user_api_model:
            self._runtime_api_settings = None
        else:
            old_signature = (self.api_key or "", self.base_url, self.model)
            self._refresh_runtime_api_settings(args)
            if (self.api_key or "", self.base_url, self.model) != old_signature:
                need_rebuild_client = True

        # 如果 API Key 或 Base URL 变化，重建客户端
        if need_rebuild_client:
            self.client = None
            self._setup_client()

    def _refresh_runtime_api_settings(self, config):
        settings = resolve_runtime_api_config(
            config,
            feature="translator",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            api_base_env="OPENAI_API_BASE",
            model_env="OPENAI_MODEL",
            fallback_api_key_env=None,
            fallback_api_base_env=None,
            fallback_model_env=None,
            default_api_base="https://api.openai.com/v1",
            default_model="gpt-4o",
            allow_empty_local_api_key=True,
        )
        self._runtime_api_settings = settings
        if settings.api_key:
            self.api_key = settings.api_key
            self.base_url = settings.base_url
            self.model = settings.model_name
        return settings

    async def _close_current_client(self):
        if not self.client:
            return
        try:
            await self.client.close()
        except Exception:
            pass
        finally:
            self.client = None

    async def _reset_client_for_candidate(self, endpoint, error: Exception):
        del endpoint, error
        await self._close_current_client()

    def _apply_api_endpoint(self, endpoint):
        signature = (endpoint.api_key or "", endpoint.base_url, endpoint.model_name)
        if signature == (self.api_key or "", self.base_url, self.model) and self.client:
            return
        self.api_key = endpoint.api_key
        self.base_url = endpoint.base_url
        self.model = endpoint.model_name
        if self.model not in OpenAITranslator._GLOBAL_LAST_REQUEST_TS:
            OpenAITranslator._GLOBAL_LAST_REQUEST_TS[self.model] = 0
        self._last_request_ts_key = self.model
        self._setup_client(force_recreate=True)

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
            provider_name="OpenAI",
            operation_name=operation_name,
            logger=self.logger,
            retry_attempts=self.attempts,
            on_candidate_error=self._reset_client_for_candidate,
        )

    def _setup_client(self, force_recreate: bool = False):
        """设置OpenAI客户端

        Args:
            force_recreate: 是否强制重建客户端（用于重试时断开旧连接）
        """
        if force_recreate and self.client:
            # 关闭旧客户端，断开连接
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 如果事件循环正在运行，创建任务异步关闭
                    asyncio.create_task(self.client.close())
                else:
                    # 否则同步关闭
                    loop.run_until_complete(self.client.close())
            except Exception as e:
                self.logger.debug(f"关闭旧客户端时出错（可忽略）: {e}")
            self.client = None

        if not self.client:
            # 强制使用 curl_cffi 客户端（不回退标准 SDK）
            self.client = AsyncOpenAICurlCffi(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=600.0,
                stream_timeout=300.0
            )
            self.logger.debug("已创建新的OpenAI客户端连接（强制 curl_cffi 模式）")
    
    async def _cleanup(self):
        """清理资源"""
        if self.client:
            try:
                await self.client.close()
            except Exception:
                pass  # 忽略清理时的错误

    async def _abort_inflight_request(self):
        """取消时中断当前请求连接，避免长时间阻塞。"""
        if not self.client:
            return
        try:
            await self.client.close()
        except Exception as e:
            self.logger.debug(f"中断请求时关闭客户端失败（可忽略）: {e}")
        finally:
            self.client = None
    
    def __del__(self):
        """析构函数，确保资源被清理"""
        if self.client:
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running() and not loop.is_closed():
                    # 如果事件循环未关闭，同步执行清理
                    loop.run_until_complete(self._cleanup())
            except Exception:
                pass  # 忽略所有清理错误

    def _build_user_prompt(self, texts: List[str], ctx: Any, retry_attempt: int = 0, retry_reason: str = "") -> str:
        """构建用户提示词（纯文本版）- 使用 JSON 格式以配合 HQ Prompt"""
        return self._build_user_prompt_for_texts(texts, ctx, "", retry_attempt=retry_attempt, retry_reason=retry_reason)

    def _get_system_prompt(self, source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None, retry_attempt: int = 0, retry_reason: str = "", extract_glossary: bool = False) -> str:
        """获取完整的系统提示词"""
        return self._build_system_prompt(source_lang, target_lang, custom_prompt_json=custom_prompt_json, line_break_prompt_json=line_break_prompt_json, retry_attempt=retry_attempt, retry_reason=retry_reason, extract_glossary=extract_glossary)

    async def _translate_batch(self, texts: List[str], source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None, ctx: Any = None, split_level: int = 0) -> List[str]:
        """批量翻译方法（纯文本）"""
        if not texts:
            return []
        
        if not self.client:
            self._setup_client()
        
        # 初始化重试信息
        retry_attempt = 0
        retry_reason = ""
        
        # 保存参数供重试时使用
        _source_lang = source_lang
        _target_lang = target_lang
        _custom_prompt_json = custom_prompt_json
        _line_break_prompt_json = line_break_prompt_json
        
        # 发送请求
        max_retries = self._resolve_max_total_attempts()
        attempt = 0
        is_infinite = max_retries == -1
        last_exception = None
        local_attempt = 0  # 本次批次的尝试次数

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
            
            # 确定是否开启术语提取
            config_extract = False
            if ctx and hasattr(ctx, 'config') and hasattr(ctx.config, 'translator'):
                config_extract = getattr(ctx.config.translator, 'extract_glossary', False)
            
            extract_glossary = bool(_custom_prompt_json) and config_extract

            # 构建系统提示词和用户提示词（包含重试信息以避免缓存）
            system_prompt = self._get_system_prompt(_source_lang, _target_lang, custom_prompt_json=_custom_prompt_json, line_break_prompt_json=_line_break_prompt_json, retry_attempt=retry_attempt, retry_reason=retry_reason, extract_glossary=extract_glossary)
            user_prompt = self._build_user_prompt(texts, ctx, retry_attempt=retry_attempt, retry_reason=retry_reason)
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self._build_openai_context_messages(self.prev_context))
            messages.append({"role": "user", "content": user_prompt})

            try:
                # RPM限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    import time
                    now = time.time()
                    delay = 60.0 / self._MAX_REQUESTS_PER_MINUTE
                    elapsed = now - OpenAITranslator._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key]
                    if elapsed < delay:
                        sleep_time = delay - elapsed
                        self.logger.info(f'Ratelimit sleep: {sleep_time:.2f}s')
                        await self._sleep_with_cancel_polling(sleep_time)
                
                # 构建API参数，只有当max_tokens有值时才传递（新模型如o1/gpt-4.1不支持null值）
                api_params = {
                    "model": self.model,
                    "messages": messages,
                }
                if self.max_tokens is not None:
                    api_params["max_tokens"] = self.max_tokens
                
                def _extract_openai_stream_text(chunk):
                    if not (hasattr(chunk, 'choices') and chunk.choices):
                        return ""
                    choice = chunk.choices[0]
                    delta = getattr(choice, 'delta', None)
                    return getattr(delta, 'content', '') if delta else ""

                def _extract_openai_stream_finish_reason(chunk):
                    if not (hasattr(chunk, 'choices') and chunk.choices):
                        return None
                    return getattr(chunk.choices[0], 'finish_reason', None)
                
                def _on_stream_chunk(delta_text, _full_text):
                    self._emit_stream_json_preview("[OpenAI Stream]", delta_text, source_texts=texts)

                streamed_text = None
                streamed_finish_reason = None
                response = None
                use_streaming = self._is_streaming_enabled(ctx)

                async def _send_openai_request():
                    nonlocal response, streamed_text, streamed_finish_reason
                    response = None
                    streamed_text = None
                    streamed_finish_reason = None
                    custom_api_params = self._resolve_translator_custom_api_params(self.model)
                    request_params = merge_openai_chat_request_params(
                        {**api_params, "model": self.model},
                        custom_api_params,
                    )
                    if custom_api_params:
                        self.logger.debug(f"使用翻译模型预设参数: {custom_api_params}")
                    if use_streaming:
                        try:
                            self._reset_stream_json_preview()
                            stream_params = dict(request_params)
                            stream_params["stream"] = True
                            streamed_text, streamed_finish_reason = await self._run_unified_stream_transport(
                                create_stream=lambda: self.client.chat.completions.create(**stream_params),
                                extract_text=_extract_openai_stream_text,
                                extract_finish_reason=_extract_openai_stream_finish_reason,
                                on_chunk=_on_stream_chunk,
                                on_cancel=self._abort_inflight_request,
                                poll_interval=0.2,
                                sync_iter_in_thread=False,
                            )
                            self._finish_stream_inline()
                        except Exception as stream_error:
                            self._finish_stream_inline()
                            streamed_text = None
                            streamed_finish_reason = None
                            self.logger.warning(f"流式请求不可用，已回退普通请求: {stream_error}")
                            response = await self._await_with_cancel_polling(
                                self.client.chat.completions.create(**request_params),
                                poll_interval=0.2,
                                on_cancel=self._abort_inflight_request,
                            )
                    else:
                        self.logger.info("已禁用流式传输，使用普通请求。")
                        response = await self._await_with_cancel_polling(
                            self.client.chat.completions.create(**request_params),
                            poll_interval=0.2,
                            on_cancel=self._abort_inflight_request,
                        )

                    if streamed_text is not None:
                        if not streamed_text.strip():
                            raise RuntimeError("OpenAI returned empty content")
                    else:
                        validate_openai_response(response, self.logger)
                        has_response_content = bool(
                            getattr(response, "choices", None)
                            and response.choices[0].message.content
                        )
                        if not has_response_content:
                            raise RuntimeError("OpenAI returned empty content")

                await self._run_with_api_rotation(_send_openai_request, "translation request")

                # 在API调用成功后立即更新时间戳，确保所有请求（包括重试）都被计入速率限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    OpenAITranslator._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key] = time.time()

                if streamed_text is not None:
                    finish_reason = streamed_finish_reason
                    has_content = bool(streamed_text)
                else:
                    # 验证响应对象是否有效
                    validate_openai_response(response, self.logger)
                    # 检查成功条件：有内容就尝试处理，后续会有质量检查
                    finish_reason = response.choices[0].finish_reason if (hasattr(response, 'choices') and response.choices) else None
                    has_content = response.choices and response.choices[0].message.content
                 
                if has_content:
                    result_text = streamed_text.strip() if streamed_text is not None else response.choices[0].message.content.strip()
                    
                    # 统一的编码清理（处理UTF-16-LE等编码问题）
                    from .common import sanitize_text_encoding
                    result_text = sanitize_text_encoding(result_text)
                    
                    self.logger.debug(f"--- OpenAI Raw Response ---\n{result_text}\n---------------------------")
                    
                    # 去除 <think>...</think> 标签及内容（LM Studio 等本地模型的思考过程）
                    result_text = re.sub(r'(</think>)?<think>.*?</think>', '', result_text, flags=re.DOTALL)
                    # 提取 <answer>...</answer> 中的内容（如果存在）
                    answer_match = re.search(r'<answer>(.*?)</answer>', result_text, flags=re.DOTALL)
                    if answer_match:
                        result_text = answer_match.group(1).strip()
                    
                    # 增加清理步骤，移除可能的Markdown代码块
                    if result_text.startswith("```") and result_text.endswith("```"):
                         # 这里的正则比简单的切片更安全
                         code_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', result_text, re.DOTALL)
                         if code_match:
                             result_text = code_match.group(1).strip()
                         elif result_text.startswith("```"): # 简单fallback
                             result_text = result_text.strip("`").strip()
                    
                    # 使用通用函数解析响应（支持JSON和纯文本）
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

                        # 重试前断开连接，重建客户端
                        self.logger.info("重试前断开旧连接，重建客户端...")
                        self._setup_client(force_recreate=True)
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

                        # 重试前断开连接，重建客户端
                        self.logger.info("重试前断开旧连接，重建客户端...")
                        self._setup_client(force_recreate=True)
                        await self._sleep_with_cancel_polling(2)
                        continue

                    self._emit_final_translation_results(texts, translations)

                    # BR检查：检查翻译结果是否包含必要的[BR]标记
                    if not self._validate_br_markers(translations, queries=texts, ctx=ctx):
                        retry_attempt += 1
                        retry_reason = "BR markers missing in translations"
                        log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                        self.logger.warning(f"[{log_attempt}] {retry_reason}, retrying...")
                        
                        # 记录错误以便在达到最大尝试次数时显示
                        last_exception = Exception("AI断句检查失败: 翻译结果缺少必要的[BR]标记")
                        
                        # 如果达到最大重试次数，抛出友好的异常
                        if not is_infinite and attempt >= max_retries:
                            from .common import BRMarkersValidationException
                            self.logger.error("OpenAI翻译在多次重试后仍然失败：AI断句检查失败。")
                            raise BRMarkersValidationException(
                                missing_count=0,  # 具体数字在_validate_br_markers中已记录
                                total_count=len(texts),
                                tolerance=max(1, len(texts) // 10)
                            )
                        
                        # 重试前断开连接，重建客户端
                        self.logger.info("重试前断开旧连接，重建客户端...")
                        self._setup_client(force_recreate=True)
                        await self._sleep_with_cancel_polling(2)
                        continue

                    return translations[:len(texts)]
                
                # 如果不成功，则记录原因并准备重试
                retry_attempt += 1
                log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                
                # finish_reason 已在上面获取，根据不同情况处理
                if finish_reason == 'content_filter':
                    retry_reason = "Content filter triggered"
                    self.logger.warning(f"OpenAI内容被安全策略拦截 ({log_attempt})。正在重试...")
                    last_exception = Exception("OpenAI content filter triggered")
                elif finish_reason == 'length':
                    retry_reason = "Response truncated due to length limit"
                    self.logger.warning(f"OpenAI回复被截断（达到token限制） ({log_attempt})。正在重试...")
                    last_exception = Exception("OpenAI response truncated due to length limit")
                elif finish_reason == 'tool_calls':
                    retry_reason = "Tool calls instead of translation"
                    self.logger.warning(f"OpenAI尝试调用工具而非返回翻译 ({log_attempt})。正在重试...")
                    last_exception = Exception("OpenAI attempted tool calls instead of translation")
                elif not has_content:
                    retry_reason = f"Empty content (finish_reason: {finish_reason})"
                    self.logger.warning(f"OpenAI返回空内容 (finish_reason: '{finish_reason}') ({log_attempt})。正在重试...")
                    last_exception = Exception(f"OpenAI returned empty content (finish_reason: {finish_reason})")
                else:
                    retry_reason = f"Unexpected finish_reason: {finish_reason}"
                    self.logger.warning(f"OpenAI返回意外的结束原因 '{finish_reason}' ({log_attempt})。正在重试...")
                    last_exception = Exception(f"OpenAI returned unexpected finish_reason: {finish_reason}")

                if not is_infinite and attempt >= max_retries:
                    self.logger.error("OpenAI翻译在多次重试后仍然失败。即将终止程序。")
                    raise last_exception
                
                # 重试前断开连接，重建客户端
                self.logger.info("重试前断开旧连接，重建客户端...")
                self._setup_client(force_recreate=True)
                await self._sleep_with_cancel_polling(1)

            except APIRotationExhaustedError:
                raise
            except Exception as e:
                log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                last_exception = e
                self.logger.warning(f"OpenAI翻译出错 ({log_attempt}): {e}")
                
                if not is_infinite and attempt >= max_retries:
                    self.logger.error("OpenAI翻译在多次重试后仍然失败。即将终止程序。")
                    raise last_exception
                
                # 重试前断开连接，重建客户端
                self.logger.info("重试前断开旧连接，重建客户端...")
                self._setup_client(force_recreate=True)
                await self._sleep_with_cancel_polling(1)

        # 只有在所有重试都失败后才会执行到这里
        raise last_exception if last_exception else Exception("OpenAI translation failed after all retries")

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str], ctx=None) -> List[str]:
        """主翻译方法"""
        if not queries:
            return []

        # 重置全局尝试计数器
        self._reset_global_attempt_count()

        self.logger.info(f"使用OpenAI纯文本翻译模式处理{len(queries)}个文本，最大尝试次数: {self._max_total_attempts}")
        custom_prompt_json = getattr(ctx, 'custom_prompt_json', None) if ctx else None
        line_break_prompt_json = getattr(ctx, 'line_break_prompt_json', None) if ctx else None

        # 使用分割包装器进行翻译
        translations = await self._translate_with_split(
            self._translate_batch,
            queries,
            split_level=0,
            source_lang=from_lang,
            target_lang=to_lang,
            custom_prompt_json=custom_prompt_json,
            line_break_prompt_json=line_break_prompt_json,
            ctx=ctx
        )

        # 应用文本后处理
        translations = [self._clean_translation_output(q, r, to_lang) for q, r in zip(queries, translations)]
        return translations
