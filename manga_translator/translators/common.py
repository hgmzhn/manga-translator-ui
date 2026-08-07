import asyncio
import contextlib
import inspect
import json
import re
import shutil
import sys
import textwrap
import time
from abc import abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..utils import InfererModule, ModelWrapper, is_valuable_text, repeating_sequence
from ..utils.openai_compat import (
    is_local_openai_compatible_endpoint,
    resolve_openai_compatible_api_key,
)
from ..utils.image_modes import normalize_rgb_image
from ..utils.retry import (
    get_retry_attempts_from_config,
    normalize_retry_attempts,
    resolve_total_attempts,
    summarize_response_text,
)

try:
    import readline
except Exception:
    readline = None

VALID_LANGUAGES = {
    'CHS': 'Chinese (Simplified)',
    'CHT': 'Chinese (Traditional)',
    'CSY': 'Czech',
    'NLD': 'Dutch',
    'ENG': 'English',
    'FRA': 'French',
    'DEU': 'German',
    'HUN': 'Hungarian',
    'ITA': 'Italian',
    'JPN': 'Japanese',
    'KOR': 'Korean',
    'POL': 'Polish',
    'PTB': 'Portuguese (Brazil)',
    'ROM': 'Romanian',
    'RUS': 'Russian',
    'ESP': 'Spanish',
    'TRK': 'Turkish',
    'UKR': 'Ukrainian',
    'VIN': 'Vietnamese',
    'ARA': 'Arabic',
    'CNR': 'Montenegrin',
    'SRP': 'Serbian',
    'HRV': 'Croatian',
    'THA': 'Thai',
    'IND': 'Indonesian',
    'FIL': 'Filipino (Tagalog)'
}

KEEP_LANGUAGES = {
    **VALID_LANGUAGES,
    'SWE': 'Swedish',
    'DAN': 'Danish',
    'NOR': 'Norwegian',
    'FIN': 'Finnish',
    'MSA': 'Malay',
    'CAT': 'Catalan',
}

ISO_639_1_TO_VALID_LANGUAGES = {
    'zh': 'CHS',
    'ja': 'JPN',
    'en': 'ENG',
    'ko': 'KOR',
    'vi': 'VIN',
    'cs': 'CSY',
    'nl': 'NLD',
    'fr': 'FRA',
    'de': 'DEU',
    'hu': 'HUN',
    'it': 'ITA',
    'pl': 'POL',
    'pt': 'PTB',
    'ro': 'ROM',
    'ru': 'RUS',
    'es': 'ESP',
    'tr': 'TRK',
    'uk': 'UKR',
    'ar': 'ARA',
    'cnr': 'CNR',
    'sr': 'SRP',
    'hr': 'HRV',
    'th': 'THA',
    'id': 'IND',
    'tl': 'FIL'
}

ISO_639_1_TO_KEEP_LANGUAGES = {
    **ISO_639_1_TO_VALID_LANGUAGES,
    'sv': 'SWE',
    'da': 'DAN',
    'no': 'NOR',
    'nb': 'NOR',
    'nn': 'NOR',
    'fi': 'FIN',
    'ms': 'MSA',
    'ca': 'CAT',
}

class InvalidServerResponse(Exception):
    pass


def _extract_http_error_details(response) -> str:
    raw_text = summarize_response_text(
        getattr(response, "text", ""),
        empty_placeholder="(empty response)",
    )
    try:
        payload = response.json()
    except Exception:
        return raw_text

    if not isinstance(payload, dict):
        return raw_text

    details: List[str] = []
    error_obj = payload.get("error")
    if isinstance(error_obj, dict):
        for key in ("code", "type", "param"):
            value = error_obj.get(key)
            if value not in (None, ""):
                details.append(f"{key}={value}")
        message = error_obj.get("message")
        if message:
            details.append(str(message))
    else:
        for key in ("code", "type"):
            value = payload.get(key)
            if value not in (None, ""):
                details.append(f"{key}={value}")
        for key in ("message", "msg", "detail"):
            value = payload.get(key)
            if value:
                details.append(str(value))
                break
        data_value = payload.get("data")
        if data_value not in (None, "", [], {}):
            try:
                data_text = json.dumps(data_value, ensure_ascii=False)
            except Exception:
                data_text = str(data_value)
            details.append(f"data={summarize_response_text(data_text, limit=400)}")

    try:
        raw_json = json.dumps(payload, ensure_ascii=False)
    except Exception:
        raw_json = raw_text

    if details:
        return f"{'; '.join(details)} | raw={summarize_response_text(raw_json, limit=800)}"
    return summarize_response_text(raw_json, limit=800, empty_placeholder="(empty response)")


def _response_diagnostics(response, *, limit: int = 1200) -> str:
    """Return safe, bounded HTTP response details for parse errors."""
    headers = getattr(response, "headers", {}) or {}
    content_type = ""
    try:
        content_type = headers.get("content-type", "") or headers.get("Content-Type", "")
    except Exception:
        content_type = ""
    status_code = getattr(response, "status_code", "unknown")
    raw_text = summarize_response_text(
        getattr(response, "text", ""),
        limit=limit,
        empty_placeholder="(empty response)",
    )
    return f"HTTP {status_code}, Content-Type: {content_type or '(missing)'}, raw response: {raw_text}"


class LanguageUnsupportedException(Exception):
    def __init__(self, language_code: str, translator: str = None, supported_languages: List[str] = None):
        error = 'Language not supported for %s: "%s"' % (translator if translator else 'chosen translator', language_code)
        if supported_languages:
            error += '. Supported languages: "%s"' % ','.join(supported_languages)
        super().__init__(error)

class BRMarkersValidationException(Exception):
    """AI断句检查失败异常"""
    def __init__(self, missing_count: int, total_count: int, tolerance: int):
        self.missing_count = missing_count
        self.total_count = total_count
        self.tolerance = tolerance
        super().__init__(
            f"AI断句检查失败：{missing_count}/{total_count} 条翻译缺失[BR]标记（容忍度：{tolerance}）"
        )




# ============================================================================
# AsyncOpenAI 客户端包装器 - 使用 curl_cffi 绕过 TLS 指纹检测
# ============================================================================

class AsyncOpenAICurlCffi:
    """
    异步 OpenAI 客户端包装器，使用 curl_cffi 绕过 TLS 指纹检测
    完全兼容 AsyncOpenAI 的接口，可直接替换使用

    用法:
        client = AsyncOpenAICurlCffi(
            api_key="your-api-key",
            base_url="https://api.openai.com/v1",
            default_headers={"User-Agent": "..."},
            impersonate="chrome110"
        )

        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}]
        )
    """

    class ChatCompletions:
        """聊天完成接口"""

        def __init__(self, parent):
            self.parent = parent

        async def create(self, model, messages, temperature=None, max_tokens=None, **kwargs):
            """创建聊天完成请求"""
            url = f"{self.parent.base_url}/chat/completions"

            headers = {
                "Content-Type": "application/json"
            }
            if self.parent.api_key:
                headers["Authorization"] = f"Bearer {self.parent.api_key}"

            # 合并默认请求头
            if self.parent.default_headers:
                headers.update(self.parent.default_headers)

            # 构建请求数据
            data = {
                "model": model,
                "messages": messages
            }

            if temperature is not None:
                data["temperature"] = temperature
            if max_tokens is not None:
                data["max_tokens"] = max_tokens

            # 添加其他参数
            data.update(kwargs)

            stream_mode = bool(data.get("stream"))
            # 部分 OpenAI 兼容站点会在缺省 stream 参数时错误地默认返回 SSE。
            # 普通请求显式传 false，确保响应遵循非流式 JSON 格式。
            data["stream"] = stream_mode
            if stream_mode:
                return self._create_stream(url, data, headers)

            # 发送异步请求
            response = await self.parent.session.post(
                url,
                json=data,
                headers=headers,
                timeout=self.parent.timeout
            )

            if response.status_code != 200:
                print(f"[AsyncOpenAICurlCffi] Error - URL: {url}")
                print(f"[AsyncOpenAICurlCffi] Error - Status: {response.status_code}")
                print(f"[AsyncOpenAICurlCffi] Error - Response: {summarize_response_text(response.text)}")
                error_msg = (
                    f"API request failed with status {response.status_code}: "
                    f"{_extract_http_error_details(response)}"
                )
                raise Exception(error_msg)

            try:
                result = response.json()
            except Exception as e:
                raise Exception(
                    f"无法解析 API 的 JSON 响应: {e}. {_response_diagnostics(response)}"
                ) from e

            # 转换为类似 OpenAI SDK 的响应对象
            return _OpenAIResponse(result)

        def _create_stream(self, url, data, headers):
            """SSE 流式请求，返回异步可迭代对象。"""

            async def _gen():
                async with self.parent.session.stream(
                    "POST",
                    url,
                    json=data,
                    headers=headers,
                    timeout=self.parent.stream_timeout
                ) as response:
                    if response.status_code != 200:
                        text = await response.atext()
                        raise Exception(
                            f"API request failed with status {response.status_code}: "
                            f"{summarize_response_text(text)}"
                        )

                    async for raw_line in response.aiter_lines():
                        if isinstance(raw_line, (bytes, bytearray)):
                            raw_line = raw_line.decode("utf-8", errors="ignore")
                        line = str(raw_line or "").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(payload)
                        except Exception:
                            continue
                        yield _OpenAIStreamChunk(chunk_data)

            return _gen()

    class Chat:
        """聊天接口"""

        def __init__(self, parent):
            self.completions = AsyncOpenAICurlCffi.ChatCompletions(parent)

    class Models:
        """模型列表接口"""

        def __init__(self, parent):
            self.parent = parent

        async def list(self):
            """获取可用模型列表"""
            url = f"{self.parent.base_url}/models"

            headers = {
                "Content-Type": "application/json"
            }
            if self.parent.api_key:
                headers["Authorization"] = f"Bearer {self.parent.api_key}"

            # 合并默认请求头
            if self.parent.default_headers:
                headers.update(self.parent.default_headers)

            # 发送异步请求
            response = await self.parent.session.get(
                url,
                headers=headers,
                timeout=self.parent.timeout
            )

            if response.status_code != 200:
                print(f"[AsyncOpenAICurlCffi] List Error - URL: {url}")
                print(f"[AsyncOpenAICurlCffi] List Error - Status: {response.status_code}")
                print(f"[AsyncOpenAICurlCffi] List Error - Response: {summarize_response_text(response.text)}")
                error_msg = (
                    f"API request failed with status {response.status_code}: "
                    f"{_extract_http_error_details(response)}"
                )
                raise Exception(error_msg)

            # 检查响应内容类型
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type and 'text/json' not in content_type:
                # 可能返回了 HTML 页面，说明 API 不支持 /models 端点
                raise Exception(
                    "API 不支持获取模型列表（返回了非 JSON 响应）。"
                    f"{_response_diagnostics(response)}。请手动输入模型名称。"
                )

            try:
                result = response.json()
            except Exception as e:
                raise Exception(
                    f"无法解析 API 响应: {str(e)}. "
                    f"{_response_diagnostics(response)}。请手动输入模型名称。"
                ) from e

            # 转换为类似 OpenAI SDK 的响应对象
            return _ModelsResponse(result)

    def __init__(self, api_key, base_url="https://api.openai.com/v1",
                 default_headers=None, http_client=None, impersonate="chrome110",
                 timeout=600, stream_timeout=300):
        """
        初始化异步客户端

        Args:
            api_key: OpenAI API 密钥
            base_url: API 基础 URL
            default_headers: 默认请求头
            http_client: 忽略此参数（为了兼容性）
            impersonate: 模拟的浏览器类型 (chrome110, chrome120, safari15_5 等)
            timeout: 非流式请求超时时间（秒）
            stream_timeout: 流式 HTTP 请求超时时间（秒）
        """
        self.api_key = resolve_openai_compatible_api_key(api_key, base_url)
        self.base_url = base_url.rstrip('/')
        self.default_headers = default_headers or {}
        self.timeout = timeout
        self.stream_timeout = stream_timeout
        self.impersonate = impersonate

        # 检测是否是本地地址（本地地址不需要 impersonate，且可能导致超时）
        is_local = is_local_openai_compatible_endpoint(base_url)

        # 延迟导入 curl_cffi，避免在不需要时导入
        try:
            from curl_cffi.requests import AsyncSession
            if is_local:
                # 本地连接：不使用 impersonate，避免 HTTP/2 兼容性问题
                self.session = AsyncSession()
                print(f"[AsyncOpenAICurlCffi] Local address detected, disabled impersonate for: {base_url}")
            else:
                # 云端连接：使用 impersonate 绕过 TLS 指纹检测
                self.session = AsyncSession(impersonate=impersonate)
        except ImportError:
            raise ImportError(
                "curl_cffi is required for TLS fingerprint bypass. "
                "Install it with: pip install curl_cffi"
            )

        # 创建聊天接口
        self.chat = self.Chat(self)
        # 创建模型列表接口
        self.models = self.Models(self)

    async def close(self):
        """关闭 session"""
        if hasattr(self.session, 'close'):
            await self.session.close()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close()


class _OpenAIResponse:
    """模拟 OpenAI SDK 的响应对象"""

    class Choice:
        class Message:
            def __init__(self, content):
                self.content = content

        def __init__(self, message_content, finish_reason):
            self.message = self.Message(message_content)
            self.finish_reason = finish_reason

    class Usage:
        def __init__(self, total_tokens, prompt_tokens=0, completion_tokens=0):
            self.total_tokens = total_tokens
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens

    def __init__(self, data):
        self.model = data.get('model', '')
        self.choices = [
            self.Choice(
                choice['message']['content'],
                choice.get('finish_reason', 'stop')
            )
            for choice in data.get('choices', [])
        ]
        usage_data = data.get('usage', {})
        self.usage = self.Usage(
            usage_data.get('total_tokens', 0),
            usage_data.get('prompt_tokens', 0),
            usage_data.get('completion_tokens', 0)
        )


class _OpenAIStreamChunk:
    """模拟 OpenAI SDK stream chunk 对象（最小字段集）"""

    class Choice:
        class Delta:
            def __init__(self, content):
                self.content = content

        def __init__(self, delta_content, finish_reason):
            self.delta = self.Delta(delta_content)
            self.finish_reason = finish_reason

    def __init__(self, data):
        choices = data.get("choices", []) if isinstance(data, dict) else []
        self.choices = []
        for c in choices:
            delta = c.get("delta", {}) if isinstance(c, dict) else {}
            delta_content = delta.get("content", "") if isinstance(delta, dict) else ""
            finish_reason = c.get("finish_reason") if isinstance(c, dict) else None
            self.choices.append(self.Choice(delta_content, finish_reason))


class _ModelsResponse:
    """模拟 OpenAI SDK 的模型列表响应对象"""

    class Model:
        def __init__(self, model_data):
            self.id = model_data.get('id', '')
            self.object = model_data.get('object', 'model')
            self.created = model_data.get('created', 0)
            self.owned_by = model_data.get('owned_by', '')

    def __init__(self, data):
        self.data = [
            self.Model(model_data)
            for model_data in data.get('data', [])
        ]
        self.object = data.get('object', 'list')


# ============================================================================
# AsyncGemini 客户端包装器 - 使用 curl_cffi 绕过 TLS 指纹检测
# ============================================================================

class AsyncGeminiCurlCffi:
    """
    异步 Gemini 客户端包装器，使用 curl_cffi 绕过 TLS 指纹检测
    兼容 Google genai SDK 的接口

    用法:
        client = AsyncGeminiCurlCffi(
            api_key="your-api-key",
            base_url="https://generativelanguage.googleapis.com",
            impersonate="chrome110"
        )

        response = await client.models.generate_content(
            model="gemini-1.5-flash",
            contents="Hello"
        )
    """

    class Models:
        """模型接口"""

        def __init__(self, parent):
            self.parent = parent

        async def generate_content(self, model, contents, generation_config=None, safety_settings=None, **kwargs):
            """生成内容请求"""
            # 对模型名进行 URL 编码，处理包含 "/" 的模型名（如 z-ai/glm4.7）
            import urllib.parse
            encoded_model = urllib.parse.quote(model, safe='')

            # 构建 URL - Gemini API 格式
            url = f"{self.parent.base_url}/v1beta/models/{encoded_model}:generateContent"

            # 实际请求使用完整的 API Key
            request_headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.parent.api_key
            }

            # 合并默认请求头
            if self.parent.default_headers:
                request_headers.update(self.parent.default_headers)

            # 构建请求数据
            data = {}

            def _normalize_system_instruction(value):
                if not value:
                    return None
                if isinstance(value, str):
                    return {"parts": [{"text": value}]}
                if isinstance(value, dict):
                    if "parts" in value:
                        return value
                    if "text" in value:
                        return {"parts": [{"text": str(value["text"])}]}
                    return {"parts": [{"text": json.dumps(value, ensure_ascii=False)}]}
                if isinstance(value, list):
                    parts = []
                    for item in value:
                        if isinstance(item, dict):
                            if any(k in item for k in ("text", "inlineData", "inline_data", "fileData", "file_data")):
                                parts.append(item)
                            else:
                                parts.append({"text": json.dumps(item, ensure_ascii=False)})
                        else:
                            parts.append({"text": str(item)})
                    return {"parts": parts}
                if hasattr(value, "model_dump"):
                    dumped = value.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if isinstance(dumped, dict) and "parts" in dumped:
                        return dumped
                    return {"parts": [{"text": json.dumps(dumped, ensure_ascii=False)}]}
                return {"parts": [{"text": str(value)}]}

            # 处理 contents 参数
            if isinstance(contents, str):
                data["contents"] = [{"role": "user", "parts": [{"text": contents}]}]
            elif isinstance(contents, list):
                # 如果是列表，检查是否已有 role 字段，没有则添加
                processed_contents = []
                for item in contents:
                    if isinstance(item, dict):
                        if "role" not in item:
                            # 添加默认 role
                            item = {"role": "user", **item}
                        processed_contents.append(item)
                    else:
                        processed_contents.append({"role": "user", "parts": [{"text": str(item)}]})
                data["contents"] = processed_contents
            else:
                data["contents"] = [{"role": "user", "parts": [{"text": str(contents)}]}]

            # 添加生成配置
            if generation_config:
                config_dict = {}
                if hasattr(generation_config, 'temperature'):
                    config_dict['temperature'] = generation_config.temperature
                if hasattr(generation_config, 'top_p'):
                    config_dict['topP'] = generation_config.top_p
                if hasattr(generation_config, 'top_k'):
                    config_dict['topK'] = generation_config.top_k
                if hasattr(generation_config, 'max_output_tokens'):
                    config_dict['maxOutputTokens'] = generation_config.max_output_tokens
                if config_dict:
                    data["generationConfig"] = config_dict

                system_instruction = _normalize_system_instruction(
                    getattr(generation_config, 'system_instruction', None)
                )
                if system_instruction:
                    data["systemInstruction"] = system_instruction

            kw_system_instruction = _normalize_system_instruction(kwargs.pop("system_instruction", None))
            if kw_system_instruction:
                data["systemInstruction"] = kw_system_instruction

            # 添加安全设置
            if safety_settings:
                safety_list = []
                for setting in safety_settings:
                    if hasattr(setting, 'category') and hasattr(setting, 'threshold'):
                        # 提取枚举值名称，去掉类名前缀
                        # 例如: "HarmCategory.HARM_CATEGORY_HARASSMENT" -> "HARM_CATEGORY_HARASSMENT"
                        # 例如: "HarmBlockThreshold.OFF" -> "OFF"
                        category_str = str(setting.category)
                        threshold_str = str(setting.threshold)

                        # 去掉枚举类名前缀
                        if '.' in category_str:
                            category_str = category_str.split('.')[-1]
                        if '.' in threshold_str:
                            threshold_str = threshold_str.split('.')[-1]

                        safety_list.append({
                            "category": category_str,
                            "threshold": threshold_str
                        })
                if safety_list:
                    data["safetySettings"] = safety_list

            # 添加其他参数
            data.update(kwargs)

            stream_mode = bool(data.pop("stream", False))
            if stream_mode:
                return self._generate_content_stream(url, data, request_headers)

            # 发送异步请求
            response = await self.parent.session.post(
                url,
                json=data,
                headers=request_headers,
                timeout=self.parent.timeout
            )

            if response.status_code != 200:
                print(f"[AsyncGeminiCurlCffi] Error - URL: {url}")
                print(f"[AsyncGeminiCurlCffi] Error - Status: {response.status_code}")
                print(f"[AsyncGeminiCurlCffi] Error - Response: {summarize_response_text(response.text)}")
                error_msg = f"Gemini API request failed with status {response.status_code}"
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = f"{error_msg}: {error_data['error'].get('message', '')}"
                except Exception:
                    error_msg = (
                        f"{error_msg}: "
                        f"{summarize_response_text(response.text, empty_placeholder='(empty response)')}"
                    )
                raise Exception(error_msg)

            # 检查响应内容类型和内容
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type and 'text/json' not in content_type:
                raise Exception(
                    f"API 返回了非 JSON 响应 (Content-Type: {content_type}): "
                    f"{summarize_response_text(response.text)}"
                )

            try:
                result = response.json()
            except Exception as e:
                raise Exception(
                    f"无法解析 API 响应: {str(e)}。响应内容: "
                    f"{summarize_response_text(response.text)}"
                )

            # 转换为类似 Gemini SDK 的响应对象
            return _GeminiResponse(result)

        async def generate_content_stream(self, model, contents, config=None, **kwargs):
            """兼容 google-genai 的流式接口"""
            generation_config = config or kwargs.pop("generation_config", None)
            return await self.generate_content(
                model=model,
                contents=contents,
                generation_config=generation_config,
                stream=True,
                **kwargs
            )

        def _generate_content_stream(self, url, data, headers):
            async def _gen():
                stream_url = f"{url}?alt=sse" if "?" not in url else f"{url}&alt=sse"
                async with self.parent.session.stream(
                    "POST",
                    stream_url,
                    json=data,
                    headers=headers,
                    timeout=self.parent.stream_timeout
                ) as response:
                    if response.status_code != 200:
                        text = await response.atext()
                        raise Exception(
                            f"Gemini API request failed with status {response.status_code}: "
                            f"{summarize_response_text(text)}"
                        )

                    async for raw_line in response.aiter_lines():
                        if isinstance(raw_line, (bytes, bytearray)):
                            raw_line = raw_line.decode("utf-8", errors="ignore")
                        line = str(raw_line or "").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        try:
                            chunk_data = json.loads(payload)
                        except Exception:
                            continue
                        yield _GeminiResponse(chunk_data)

            return _gen()

        async def list(self):
            """获取可用模型列表"""
            url = f"{self.parent.base_url}/v1beta/models"

            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.parent.api_key
            }

            # 合并默认请求头
            if self.parent.default_headers:
                headers.update(self.parent.default_headers)

            # 发送异步请求
            response = await self.parent.session.get(
                url,
                headers=headers,
                timeout=self.parent.timeout
            )

            if response.status_code != 200:
                print(f"[AsyncGeminiCurlCffi] List Error - URL: {url}")
                print(f"[AsyncGeminiCurlCffi] List Error - Status: {response.status_code}")
                print(f"[AsyncGeminiCurlCffi] List Error - Response: {summarize_response_text(response.text)}")
                error_msg = f"Gemini API request failed with status {response.status_code}"
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = f"{error_msg}: {error_data['error'].get('message', '')}"
                except Exception:
                    error_msg = f"{error_msg}: {summarize_response_text(response.text)}"
                raise Exception(error_msg)

            # 检查响应内容类型
            content_type = response.headers.get('content-type', '')
            if 'application/json' not in content_type and 'text/json' not in content_type:
                # 可能返回了 HTML 页面，说明 API 不支持 /models 端点
                raise Exception("API 不支持获取模型列表（返回了非 JSON 响应）。请手动输入模型名称。")

            try:
                result = response.json()
            except Exception as e:
                raise Exception(f"无法解析 API 响应: {str(e)}。请手动输入模型名称。")

            # 返回模型列表
            return _GeminiModelsResponse(result)

    def __init__(self, api_key, base_url="https://generativelanguage.googleapis.com",
                 default_headers=None, impersonate="chrome110", timeout=600, stream_timeout=300):
        """
        初始化异步客户端

        Args:
            api_key: Gemini API 密钥
            base_url: API 基础 URL
            default_headers: 默认请求头
            impersonate: 模拟的浏览器类型 (chrome110, chrome120, safari15_5 等)
            timeout: 非流式请求超时时间（秒）
            stream_timeout: 流式 HTTP 请求超时时间（秒）
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.default_headers = default_headers or {}
        self.timeout = timeout
        self.stream_timeout = stream_timeout
        self.impersonate = impersonate

        # 检测是否是本地地址（本地地址不需要 impersonate，且可能导致超时）
        local_indicators = ['localhost', '127.0.0.1', '0.0.0.0', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.']
        is_local = any(indicator in base_url.lower() for indicator in local_indicators)

        # 延迟导入 curl_cffi，避免在不需要时导入
        try:
            from curl_cffi.requests import AsyncSession
            if is_local:
                # 本地连接：不使用 impersonate，避免 HTTP/2 兼容性问题
                self.session = AsyncSession()
                print(f"[AsyncGeminiCurlCffi] Local address detected, disabled impersonate for: {base_url}")
            else:
                # 云端连接：使用 impersonate 绕过 TLS 指纹检测
                self.session = AsyncSession(impersonate=impersonate)
        except ImportError:
            raise ImportError(
                "curl_cffi is required for TLS fingerprint bypass. "
                "Install it with: pip install curl_cffi"
            )

        # 创建模型接口
        self.models = self.Models(self)

    async def close(self):
        """关闭 session"""
        if hasattr(self.session, 'close'):
            await self.session.close()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close()


class _GeminiResponse:
    """模拟 Gemini SDK 的响应对象"""

    class Candidate:
        class Content:
            class Part:
                def __init__(self, text):
                    self.text = text if isinstance(text, str) else (str(text) if text is not None else "")

            def __init__(self, parts_data):
                parts_data = parts_data or []
                self.parts = [self.Part(p.get('text', '') if p else '') for p in parts_data]

        def __init__(self, candidate_data):
            content_data = candidate_data.get('content') or {}
            parts_data = content_data.get('parts') or []
            self.content = self.Content(parts_data)
            self.finish_reason = candidate_data.get('finishReason')
            self.safety_ratings = candidate_data.get('safetyRatings') or []

    class PromptFeedback:
        def __init__(self, feedback_data):
            feedback_data = feedback_data or {}
            self.block_reason = feedback_data.get('blockReason')
            self.safety_ratings = feedback_data.get('safetyRatings') or []

    def __init__(self, data):
        self.raw = data or {}
        candidates_data = data.get('candidates') or [] if data else []
        self.candidates = [self.Candidate(c) for c in candidates_data]
        self.prompt_feedback = self.PromptFeedback(self.raw.get('promptFeedback') or {})

        # 提供便捷的 text 属性
        if self.candidates and self.candidates[0].content.parts:
            self._text = self.candidates[0].content.parts[0].text
        else:
            self._text = ""

    @property
    def text(self):
        return self._text


class _GeminiModelsResponse:
    """模拟 Gemini SDK 的模型列表响应对象"""

    class Model:
        def __init__(self, model_data):
            self.name = model_data.get('name', '')
            self.display_name = model_data.get('displayName', '')
            self.description = model_data.get('description', '')
            # 从 name 中提取模型 ID (格式: models/gemini-1.5-flash)
            if '/' in self.name:
                self.id = self.name.split('/')[-1]
            else:
                self.id = self.name

    def __init__(self, data):
        # 使用 or [] 确保即使 models 是 None 也能正确处理
        models_data = data.get('models') or [] if data else []
        self._models = [self.Model(m) for m in models_data]

    def __iter__(self):
        return iter(self._models)


def validate_openai_response(response, logger=None) -> bool:
    """
    验证OpenAI API响应对象的有效性
    
    Args:
        response: API返回的响应对象
        logger: 日志记录器（可选）
    
    Returns:
        bool: 响应是否有效
    
    Raises:
        Exception: 如果响应对象无效
    """
    # 检查响应对象是否有choices属性
    if not hasattr(response, 'choices'):
        error_msg = f"API返回了无效的响应对象: {type(response).__name__}, 内容: {str(response)[:200]}"
        if logger:
            logger.error(error_msg)
        raise Exception(f"API返回了无效的响应对象，类型: {type(response).__name__}")
    
    return True

def validate_gemini_response(response, logger=None) -> bool:
    """
    验证Gemini API响应对象的有效性
    
    Args:
        response: API返回的响应对象
        logger: 日志记录器（可选）
    
    Returns:
        bool: 响应是否有效
    
    Raises:
        Exception: 如果响应对象无效
    """
    # 检查响应对象是否有candidates属性
    if not hasattr(response, 'candidates'):
        error_msg = f"Gemini API返回了无效的响应对象: {type(response).__name__}, 内容: {str(response)[:200]}"
        if logger:
            logger.error(error_msg)
        raise Exception(f"Gemini API返回了无效的响应对象，类型: {type(response).__name__}")
    
    # 检查是否有text属性（某些错误响应可能没有）
    if not hasattr(response, 'text'):
        diagnostics = extract_gemini_response_diagnostics(response)
        error_msg = f"Gemini API响应缺少text属性: {format_gemini_response_diagnostics(diagnostics)}"
        if logger:
            logger.error(error_msg)
        raise Exception("Gemini API响应缺少text属性")
    
    # text 可能存在但为 None（如安全拦截/空回），后续 .strip() 会崩溃
    if getattr(response, 'text', None) is None:
        diagnostics = extract_gemini_response_diagnostics(response)
        error_msg = f"Gemini returned empty content ({format_gemini_response_diagnostics(diagnostics)})"
        if logger:
            logger.error(error_msg)
        raise Exception(error_msg)
    
    return True


def _get_gemini_field(obj: Any, *names: str) -> Any:
    """兼容 SDK 对象 / 自定义对象 / dict 的 Gemini 字段读取。"""
    if obj is None:
        return None
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return None


def _normalize_gemini_enum(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, 'name'):
        try:
            return str(value.name)
        except Exception:
            pass
    text = str(value)
    if '.' in text:
        text = text.split('.')[-1]
    return text


def _normalize_gemini_safety_ratings(ratings: Any) -> List[Dict[str, Any]]:
    normalized = []
    for item in ratings or []:
        category = _normalize_gemini_enum(_get_gemini_field(item, 'category'))
        probability = _normalize_gemini_enum(_get_gemini_field(item, 'probability'))
        severity = _normalize_gemini_enum(_get_gemini_field(item, 'severity'))
        blocked = _get_gemini_field(item, 'blocked')
        normalized.append({
            'category': category,
            'probability': probability,
            'severity': severity,
            'blocked': bool(blocked) if blocked is not None else None,
        })
    return normalized


def extract_gemini_response_diagnostics(response: Any, fallback_finish_reason: Any = None) -> Dict[str, Any]:
    """提取 Gemini 响应诊断信息，供日志与重试逻辑复用。"""
    candidate = None
    candidates = _get_gemini_field(response, 'candidates')
    if candidates:
        try:
            candidate = candidates[0]
        except Exception:
            candidate = None

    prompt_feedback = _get_gemini_field(response, 'prompt_feedback', 'promptFeedback')
    finish_reason = fallback_finish_reason
    if finish_reason is None:
        finish_reason = _get_gemini_field(candidate, 'finish_reason', 'finishReason')
    block_reason = _get_gemini_field(prompt_feedback, 'block_reason', 'blockReason')

    prompt_safety_ratings = _normalize_gemini_safety_ratings(
        _get_gemini_field(prompt_feedback, 'safety_ratings', 'safetyRatings')
    )
    candidate_safety_ratings = _normalize_gemini_safety_ratings(
        _get_gemini_field(candidate, 'safety_ratings', 'safetyRatings')
    )

    finish_reason_str = _normalize_gemini_enum(finish_reason)
    block_reason_str = _normalize_gemini_enum(block_reason)
    text_value = _get_gemini_field(response, 'text')

    return {
        'finish_reason': finish_reason,
        'finish_reason_str': finish_reason_str,
        'block_reason': block_reason,
        'block_reason_str': block_reason_str,
        'prompt_safety_ratings': prompt_safety_ratings,
        'candidate_safety_ratings': candidate_safety_ratings,
        'text': text_value,
    }


def _format_gemini_safety_ratings(ratings: List[Dict[str, Any]]) -> str:
    if not ratings:
        return "[]"

    parts = []
    for item in ratings:
        segments = []
        if item.get('category'):
            segments.append(item['category'])
        if item.get('probability'):
            segments.append(f"prob={item['probability']}")
        if item.get('severity'):
            segments.append(f"sev={item['severity']}")
        if item.get('blocked') is not None:
            segments.append(f"blocked={item['blocked']}")
        parts.append("(" + ", ".join(segments) + ")")
    return "[" + ", ".join(parts) + "]"


def format_gemini_response_diagnostics(diagnostics: Dict[str, Any]) -> str:
    """格式化 Gemini 诊断信息，便于日志输出。"""
    parts = [
        f"finish_reason={diagnostics.get('finish_reason_str') or 'None'}",
        f"block_reason={diagnostics.get('block_reason_str') or 'None'}",
    ]

    prompt_ratings = diagnostics.get('prompt_safety_ratings') or []
    candidate_ratings = diagnostics.get('candidate_safety_ratings') or []
    if prompt_ratings:
        parts.append(f"prompt_safety_ratings={_format_gemini_safety_ratings(prompt_ratings)}")
    if candidate_ratings:
        parts.append(f"candidate_safety_ratings={_format_gemini_safety_ratings(candidate_ratings)}")

    return ", ".join(parts)


def gemini_diagnostics_indicate_safety(diagnostics: Dict[str, Any]) -> bool:
    """根据 Gemini 响应诊断判断是否属于安全策略拦截。"""
    values = [
        (diagnostics.get('finish_reason_str') or "").upper(),
        (diagnostics.get('block_reason_str') or "").upper(),
    ]
    safety_keywords = (
        'SAFETY',
        'BLOCKLIST',
        'PROHIBITED_CONTENT',
        'SPII',
        'RECITATION',
    )
    if any(any(keyword in value for keyword in safety_keywords) for value in values):
        return True

    for rating in (diagnostics.get('prompt_safety_ratings') or []) + (diagnostics.get('candidate_safety_ratings') or []):
        if rating.get('blocked') is True:
            return True

    return False


def gemini_diagnostics_should_disable_images(diagnostics: Dict[str, Any]) -> bool:
    """根据 Gemini 诊断判断 HQ 重试时是否应去掉图片。"""
    if gemini_diagnostics_indicate_safety(diagnostics):
        return True
    finish_reason = (diagnostics.get('finish_reason_str') or "").upper()
    return 'OTHER' in finish_reason


def gemini_error_message_indicates_safety(error_message: str) -> bool:
    upper = (error_message or "").upper()
    return any(token in upper for token in (
        'SAFETY',
        'BLOCKLIST',
        'PROHIBITED_CONTENT',
        'SPII',
        'RECITATION',
        'BLOCKED=TRUE',
        'FINISH_REASON: 2',
        'FINISH_REASON=2',
        'FINISH_REASON IS 2',
    ))

def draw_text_boxes_on_image(image, text_regions: List[Any], text_order: List[int], 
                             upscaled_size: Tuple[int, int] = None):
    """
    在图片上绘制带编号的文本框
    
    Args:
        image: 原始图片 (numpy array 或 PIL Image)
        text_regions: 文本区域列表，每个区域应该有 xyxy 或 min_rect 属性
        text_order: 文本顺序列表，对应每个文本框的编号
        upscaled_size: 超分后的图片尺寸 (height, width)，用于坐标转换。如果为None则不转换
    
    Returns:
        绘制了文本框的图片（与输入类型相同）
    """
    if image is None or len(text_regions) == 0:
        return image
    
    # 检查是否为PIL Image，如果是则转换为numpy数组
    from PIL import Image as PILImage
    is_pil = isinstance(image, PILImage.Image)
    if is_pil:
        canvas = np.array(normalize_rgb_image(image))
    else:
        canvas = image.copy()
    
    h, w = canvas.shape[:2]
    
    # 计算坐标缩放比例（超分坐标 -> 原图坐标）
    scale_x, scale_y = 1.0, 1.0
    if upscaled_size is not None:
        upscaled_h, upscaled_w = upscaled_size
        if upscaled_w > 0 and upscaled_h > 0:
            scale_x = w / upscaled_w
            scale_y = h / upscaled_h
    
    # 计算线宽
    lw = max(round(sum(canvas.shape[:2]) / 2 * 0.003), 2)
    
    # 定义多种颜色（RGB格式）
    colors = [
        (255, 0, 0),     # 红
        (0, 255, 0),     # 绿
        (0, 0, 255),     # 蓝
        (255, 165, 0),   # 橙
        (128, 0, 128),   # 紫
        (0, 255, 255),   # 青
        (255, 0, 255),   # 品红
        (255, 255, 0),   # 黄
        (0, 128, 0),     # 深绿
        (128, 0, 0),     # 深红
    ]
    
    # 先收集所有框的边界信息
    all_boxes = []
    for region in text_regions:
        if hasattr(region, 'xyxy'):
            x1, y1, x2, y2 = region.xyxy
            x1, x2 = x1 * scale_x, x2 * scale_x
            y1, y2 = y1 * scale_y, y2 * scale_y
            all_boxes.append((int(x1), int(y1), int(x2), int(y2)))
        elif hasattr(region, 'min_rect'):
            pts = region.min_rect.astype(np.float64)
            pts[:, 0] *= scale_x
            pts[:, 1] *= scale_y
            bx1, by1 = int(pts[:, 0].min()), int(pts[:, 1].min())
            bx2, by2 = int(pts[:, 0].max()), int(pts[:, 1].max())
            all_boxes.append((bx1, by1, bx2, by2))
    
    def check_overlap(lx, ly, lw_size, lh_size, exclude_idx):
        """检查标签区域是否与其他框重叠"""
        label_rect = (lx, ly - lh_size, lx + lw_size, ly)
        for i, (bx1, by1, bx2, by2) in enumerate(all_boxes):
            if i == exclude_idx:
                continue
            # 检查矩形是否重叠
            if not (label_rect[2] < bx1 or label_rect[0] > bx2 or label_rect[3] < by1 or label_rect[1] > by2):
                return True
        return False
    
    # 遍历每个文本区域并绘制
    for idx, region in enumerate(text_regions):
        if idx >= len(text_order):
            break
            
        order_num = text_order[idx]
        color = colors[idx % len(colors)]
        
        # 获取文本框坐标并转换
        # 边框向外扩展，避免粗边框覆盖文字内容
        expand = lw  # 向外扩展的像素数（等于线宽）
        
        if hasattr(region, 'xyxy'):
            x1, y1, x2, y2 = region.xyxy
            x1, x2 = x1 * scale_x, x2 * scale_x
            y1, y2 = y1 * scale_y, y2 * scale_y
            # 向外扩展边框
            box_x1, box_y1 = int(x1) - expand, int(y1) - expand
            box_x2, box_y2 = int(x2) + expand, int(y2) + expand
            cv2.rectangle(canvas, (box_x1, box_y1), (box_x2, box_y2), color, lw)
        elif hasattr(region, 'min_rect'):
            pts = region.min_rect.astype(np.float64)
            pts[:, 0] *= scale_x
            pts[:, 1] *= scale_y
            # 计算中心点，向外扩展多边形
            center_x = pts[:, 0].mean()
            center_y = pts[:, 1].mean()
            for i in range(len(pts)):
                dx = pts[i, 0] - center_x
                dy = pts[i, 1] - center_y
                dist = np.sqrt(dx*dx + dy*dy)
                if dist > 0:
                    pts[i, 0] += (dx / dist) * expand
                    pts[i, 1] += (dy / dist) * expand
            pts = pts.astype(np.int32)
            cv2.polylines(canvas, [pts], True, color, lw)
            box_x1, box_y1 = int(pts[:, 0].min()), int(pts[:, 1].min())
            box_x2, box_y2 = int(pts[:, 0].max()), int(pts[:, 1].max())
        else:
            continue
        
        # 绘制编号标签
        label_text = str(order_num)
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(lw / 2, 0.6)
        font_thickness = max(lw, 2)
        
        (text_width, text_height), _ = cv2.getTextSize(label_text, font, font_scale, font_thickness)
        margin = 3
        
        # 四个候选位置：上、下、左、右
        candidates = [
            (box_x1, box_y1 - margin),                          # 上
            (box_x1, box_y2 + text_height + margin),            # 下
            (box_x1 - text_width - margin, box_y1 + text_height), # 左
            (box_x2 + margin, box_y1 + text_height),            # 右
        ]
        
        # 选择不重叠且在图片范围内的位置
        label_x, label_y = candidates[0]  # 默认上方
        for cx, cy in candidates:
            # 检查是否在图片范围内
            if cx < 0 or cy - text_height < 0 or cx + text_width > w or cy > h:
                continue
            # 检查是否与其他框重叠
            if not check_overlap(cx, cy, text_width, text_height, idx):
                label_x, label_y = cx, cy
                break
        
        # 最终边界检查
        label_x = max(0, min(label_x, w - text_width))
        label_y = max(text_height, min(label_y, h))
        
        # 绘制编号文本（带黑色描边）
        cv2.putText(canvas, label_text, (label_x, label_y), font, font_scale, (0, 0, 0), font_thickness + 2, cv2.LINE_AA)
        cv2.putText(canvas, label_text, (label_x, label_y), font, font_scale, color, font_thickness, cv2.LINE_AA)
    
    # 如果输入是PIL Image，转换回PIL格式
    if is_pil:
        return PILImage.fromarray(canvas)
    return canvas


class MTPEAdapter():
    async def dispatch(self, queries: List[str], translations: List[str]) -> List[str]:
        # TODO: Make it work in windows (e.g. through os.startfile)
        if not readline:
            print('MTPE is currently only supported on linux')
            return translations
        new_translations = []
        print('Running Machine Translation Post Editing (MTPE)')
        for i, (query, translation) in enumerate(zip(queries, translations)):
            print(f'\n[{i + 1}/{len(queries)}] {query}:')
            readline.set_startup_hook(lambda: readline.insert_text(translation.replace('\n', '\\n')))
            new_translation = ''
            try:
                new_translation = input(' -> ').replace('\\n', '\n')
            finally:
                readline.set_startup_hook()
            new_translations.append(new_translation)
        print()
        return new_translations


def _flatten_prompt_data(data, indent: int = 0) -> str:
    """Recursively flattens a dictionary or list into a formatted string.
    
    Used to convert custom prompt JSON/YAML data into a readable text block
    for inclusion in system prompts.
    """
    prompt_parts = []
    prefix = "  " * indent

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                prompt_parts.append(f"{prefix}- {key}:")
                prompt_parts.append(_flatten_prompt_data(value, indent + 1))
            else:
                prompt_parts.append(f"{prefix}- {key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                prompt_parts.append(_flatten_prompt_data(item, indent + 1))
            else:
                prompt_parts.append(f"{prefix}- {item}")
    
    return "\n".join(prompt_parts)


class CommonTranslator(InfererModule):
    # Translator has to support all languages listed in here. The language codes will be resolved into
    # _LANGUAGE_CODE_MAP[lang_code] automatically if _LANGUAGE_CODE_MAP is a dict.
    # If it is a list it will simply return the language code as is.
    _LANGUAGE_CODE_MAP = {}

    # The amount of repeats upon detecting an invalid translation.
    # Use with _is_translation_invalid and _modify_invalid_translation_query.
    _INVALID_REPEAT_COUNT = 0

    # Will sleep for the rest of the minute if the request count is over this number.
    _MAX_REQUESTS_PER_MINUTE = -1

    def __init__(self):
        super().__init__()
        self.mtpe_adapter = MTPEAdapter()
        self._last_request_ts = 0
        self.enable_post_translation_check = False
        self.post_check_repetition_threshold = 5
        self.post_check_max_retry_attempts = 2
        self.attempts = -1
        self._MAX_SPLIT_ATTEMPTS = 3  # 最大分割层级
        self._SPLIT_THRESHOLD = 2  # 重试N次后触发分割
        self._global_attempt_count = 0  # 全局尝试计数器
        self._max_total_attempts = -1  # 全局最大尝试次数
        self._cancel_check_callback = None  # 取消检查回调
        self._custom_api_params_config = None
        self._enable_streaming = True
        self._stream_inline_last_len = 0
        self._stream_inline_buffer = ""
        self._stream_json_seen: Dict[int, str] = {}
        self._stream_term_seen: Dict[Tuple[str, str], str] = {}
        self._stream_preview_buffer = ""
        self._stream_preview_scan_pos = 0
        self._stream_preview_object_starts: List[int] = []
        self._stream_preview_in_string = False
        self._stream_preview_escape = False
        self._stream_preview_tag_tail = ""
        self._stream_preview_in_think = False
        self._stream_result_header_printed = False
        self._stream_result_pairs_printed = False

    def _normalize_retry_attempts(self, attempts: Any) -> int:
        return normalize_retry_attempts(attempts, logger=self.logger, default=-1)

    def _resolve_max_total_attempts(self) -> int:
        return resolve_total_attempts(self.attempts)

    def _resolve_translator_config(self, config: Any) -> Any:
        if isinstance(config, dict):
            return config.get('translator', config)
        return getattr(config, 'translator', config)

    def _get_config_value(self, config: Any, key: str, default: Any = None) -> Any:
        if isinstance(config, dict):
            return config.get(key, default)
        return getattr(config, key, default)

    def _is_streaming_enabled(self, ctx: Any = None) -> bool:
        if ctx and hasattr(ctx, 'config') and ctx.config is not None:
            translator_config = self._resolve_translator_config(ctx.config)
            value = self._get_config_value(translator_config, 'enable_streaming', None)
            if value is not None:
                return bool(value)
        return bool(getattr(self, '_enable_streaming', True))
    
    def _configure_custom_api_params(self, args) -> bool:
        """Remember the runtime config; model matching happens per API request."""
        from ..custom_api_params import is_custom_api_params_enabled

        use_custom_params = is_custom_api_params_enabled(args)
        self._custom_api_params_config = args if use_custom_params else None
        return use_custom_params

    def _resolve_translator_custom_api_params(self, model_name: str | None) -> dict[str, Any]:
        from ..custom_api_params import resolve_custom_api_params

        if self._custom_api_params_config is None:
            return {}
        return resolve_custom_api_params(
            self._custom_api_params_config,
            self.logger,
            model_name=model_name,
            section="translator",
        )
    
    def set_cancel_check_callback(self, callback):
        """设置取消检查回调"""
        self._cancel_check_callback = callback
    
    def _check_cancelled(self):
        """检查任务是否被取消"""
        if self._cancel_check_callback and self._cancel_check_callback():
            raise asyncio.CancelledError("Translation cancelled by user")

    async def _await_with_cancel_polling(
        self,
        awaitable: Awaitable,
        poll_interval: float = 0.2,
        on_cancel: Optional[Callable[[], Awaitable[None] | None]] = None,
    ):
        """
        等待一个长耗时 awaitable，并定期轮询取消状态。
        在收到取消时，尝试取消内部任务并执行 on_cancel 清理回调。
        """
        task = asyncio.create_task(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=poll_interval)
                if done:
                    return task.result()
                self._check_cancelled()
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                with contextlib.suppress(Exception):
                    done, _ = await asyncio.wait({task}, timeout=max(poll_interval, 0.2))
                    if not done:
                        self.logger.debug("取消请求任务超时，直接退出等待")
            if on_cancel:
                try:
                    cleanup_result = on_cancel()
                    if asyncio.iscoroutine(cleanup_result):
                        await asyncio.wait_for(cleanup_result, timeout=max(poll_interval, 0.3))
                except asyncio.TimeoutError:
                    self.logger.debug("取消时清理请求超时，直接退出等待")
                except Exception as cleanup_error:
                    self.logger.debug(f"取消时清理请求失败（可忽略）: {cleanup_error}")
            raise

    async def _sleep_with_cancel_polling(self, seconds: float, poll_interval: float = 0.2):
        """可取消的 sleep，避免等待期间无法响应停止。"""
        if seconds <= 0:
            self._check_cancelled()
            return
        await self._await_with_cancel_polling(
            asyncio.sleep(seconds),
            poll_interval=min(poll_interval, max(seconds, 0.05)),
        )

    async def _run_unified_stream_transport(
        self,
        *,
        create_stream: Callable[[], Any],
        extract_text: Callable[[Any], Any],
        extract_finish_reason: Optional[Callable[[Any], Any]] = None,
        on_chunk: Optional[Callable[[str, str], None]] = None,
        on_cancel: Optional[Callable[[], Awaitable[None] | None]] = None,
        poll_interval: float = 0.2,
        sync_iter_in_thread: bool = False,
        first_chunk_timeout: float = 300.0,
        idle_timeout: float = 300.0,
    ) -> Tuple[str, Any]:
        """
        通用流式传输层：
        - OpenAI async stream（异步迭代）
        - Gemini stream（同步迭代，放入 to_thread 消费）
        返回：(聚合后的完整文本, 最后一次 finish_reason)
        """
        def _normalize_stream_piece(piece_text: str, current_text: str) -> str:
            """
            兼容“增量块/累计块/重复块”三种常见流格式，尽量只返回新增部分。
            """
            if not piece_text:
                return ""
            if not current_text:
                return piece_text

            # 标准累计块：piece = current + delta
            if piece_text.startswith(current_text):
                return piece_text[len(current_text):]

            # 回退/截断块：piece 只是 current 的前缀（且不是极短 token），忽略
            if len(piece_text) >= 16 and current_text.startswith(piece_text):
                return ""

            # 某些服务会把 current 放在 piece 中间，取最后一次出现后的尾部
            pos = piece_text.rfind(current_text)
            if pos != -1:
                return piece_text[pos + len(current_text):]

            # 明确重发：较长片段且 current 已以该片段结尾，忽略
            if len(piece_text) >= 16 and current_text.endswith(piece_text):
                return ""

            # 处理部分重叠：current 尾部 + piece 头部
            max_overlap = min(len(piece_text), len(current_text))
            for overlap in range(max_overlap, 0, -1):
                if current_text.endswith(piece_text[:overlap]):
                    return piece_text[overlap:]

            # 无法判断关系时按增量处理（保守）
            return piece_text

        text_parts: List[str] = []
        last_finish_reason = None

        if sync_iter_in_thread:
            def _consume_sync_stream():
                local_parts: List[str] = []
                local_finish = None
                stream_obj = create_stream()
                for chunk in stream_obj:
                    piece = extract_text(chunk)
                    if piece:
                        piece_text = str(piece)
                        current_text = ''.join(local_parts)
                        normalized_piece = _normalize_stream_piece(piece_text, current_text)
                        if normalized_piece:
                            local_parts.append(normalized_piece)
                        if on_chunk:
                            on_chunk(normalized_piece, ''.join(local_parts))
                    if extract_finish_reason:
                        finish = extract_finish_reason(chunk)
                        if finish is not None:
                            local_finish = finish
                return ''.join(local_parts), local_finish

            return await self._await_with_cancel_polling(
                asyncio.to_thread(_consume_sync_stream),
                poll_interval=poll_interval,
                on_cancel=on_cancel,
            )

        stream_obj = create_stream()
        while inspect.isawaitable(stream_obj):
            stream_obj = await self._await_with_cancel_polling(
                stream_obj,
                poll_interval=poll_interval,
                on_cancel=on_cancel,
            )

        try:
            aiter = stream_obj.__aiter__()
            got_first_chunk = False
            while True:
                chunk_timeout = first_chunk_timeout if not got_first_chunk else idle_timeout
                try:
                    chunk = await self._await_with_cancel_polling(
                        asyncio.wait_for(aiter.__anext__(), timeout=chunk_timeout),
                        poll_interval=poll_interval,
                        on_cancel=on_cancel,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as timeout_error:
                    timeout_type = "首包" if not got_first_chunk else "流空闲"
                    raise TimeoutError(f"流式{timeout_type}超时（{chunk_timeout:.0f}s）") from timeout_error

                got_first_chunk = True
                self._check_cancelled()
                piece = extract_text(chunk)
                if piece:
                    piece_text = str(piece)
                    current_text = ''.join(text_parts)
                    normalized_piece = _normalize_stream_piece(piece_text, current_text)
                    if normalized_piece:
                        text_parts.append(normalized_piece)
                    if on_chunk:
                        on_chunk(normalized_piece, ''.join(text_parts))
                if extract_finish_reason:
                    finish = extract_finish_reason(chunk)
                    if finish is not None:
                        last_finish_reason = finish
        except asyncio.CancelledError:
            if on_cancel:
                cleanup_result = on_cancel()
                if asyncio.iscoroutine(cleanup_result):
                    with contextlib.suppress(Exception):
                        await cleanup_result
            raise
        finally:
            close_fn = getattr(stream_obj, "aclose", None)
            if callable(close_fn):
                with contextlib.suppress(Exception):
                    close_ret = close_fn()
                    if asyncio.iscoroutine(close_ret):
                        await close_ret

        return ''.join(text_parts), last_finish_reason

    def _get_retry_hint(self, attempt: int, reason: str = "") -> str:
        """
        生成重试提示信息，用于避免模型服务器缓存导致的重复错误
        
        Args:
            attempt: 当前尝试次数
            reason: 重试原因（可选）
            
        Returns:
            重试提示字符串
        """
        hints = [
            f"[Retry attempt #{attempt}]",
            f"[This is attempt #{attempt}, please provide a different response]",
            f"[Attempt {attempt}: Previous response had issues, please try again]",
            f"[Retry #{attempt}: Please ensure quality this time]",
            f"[Attempt {attempt} - Previous attempts failed quality check]"
        ]
        
        # 根据尝试次数选择不同的提示（循环使用）
        base_hint = hints[(attempt - 1) % len(hints)]
        
        # 如果提供了原因，添加到提示中
        if reason:
            return f"{base_hint} Reason: {reason}\n\n"
        else:
            return f"{base_hint}\n\n"

    # HQ 翻译器用的详细 fallback 提示词（当 system_prompt_hq.yaml/json 不存在时）
    _HQ_FALLBACK_PROMPT = """You are an expert manga translator. Your task is to accurately translate manga text from the source language into **{{{target_lang}}}**. You will be given the full manga page for context.

**CRITICAL INSTRUCTIONS (FOLLOW STRICTLY):**

1.  **TRANSLATE EVERYTHING**: Translate all text provided, including sound effects and single characters. Do not leave any line untranslated.

2.  **ACCURACY AND TONE**:
    -   Preserve the original tone, emotion, and character's voice.
    -   Ensure consistent translation of names, places, and special terms.
    -   For onomatopoeia (sound effects), provide the equivalent sound in {{{target_lang}}} or a brief description (e.g., '(rumble)', '(thud)').

3.  **ANTI-HALLUCINATION**:
    -   Do not add information that is not present in the original text.
    -   If OCR appears wrong, prefer the visible image context over broken OCR text.

---

**FINAL INSTRUCTION:** Translate the provided text regions faithfully and follow the separate output-format requirements appended below."""
    def _parse_prev_context_turns(self, prev_context: str) -> List[Dict[str, str]]:
        """解析历史上下文，只接受新的 user/assistant JSON 轮次。"""
        payload = (prev_context or "").strip()
        if not payload:
            return []

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []

        turns: List[Dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            user_content = item.get("user")
            assistant_content = item.get("assistant")
            if isinstance(user_content, str) and isinstance(assistant_content, str):
                turns.append({"user": user_content, "assistant": assistant_content})
        return turns

    def _build_openai_context_messages(self, prev_context: str) -> List[Dict[str, Any]]:
        """将历史上下文转换为 OpenAI 多轮消息，不附带图片。"""
        turns = self._parse_prev_context_turns(prev_context)
        if not turns:
            self.logger.info("[Context] None")
            return []

        total_chars = sum(len(turn["user"]) + len(turn["assistant"]) for turn in turns)
        self.logger.info(f"[Context] Turns: {len(turns)}, Length: {total_chars} chars")
        messages: List[Dict[str, Any]] = []
        for turn in turns:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["assistant"]})
        return messages

    def _build_gemini_context_messages(self, prev_context: str) -> List[Dict[str, Any]]:
        """将历史上下文转换为 Gemini 多轮消息，不附带图片。"""
        turns = self._parse_prev_context_turns(prev_context)
        if not turns:
            self.logger.info("[Context] None")
            return []

        total_chars = sum(len(turn["user"]) + len(turn["assistant"]) for turn in turns)
        self.logger.info(f"[Context] Turns: {len(turns)}, Length: {total_chars} chars")
        messages: List[Dict[str, Any]] = []
        for turn in turns:
            messages.append({"role": "user", "parts": [{"text": turn["user"]}]})
            messages.append({"role": "model", "parts": [{"text": turn["assistant"]}]})
        return messages

    def _build_system_prompt_prefix(
        self,
        line_break_prompt_str: str,
        custom_prompt_str: str,
        retry_attempt: int = 0,
        retry_reason: str = "",
    ) -> str:
        """构建系统提示词前缀：[重试提示] → [断句提示] → [自定义提示]"""
        prompt_prefix = ""

        if retry_attempt > 0:
            prompt_prefix += self._get_retry_hint(retry_attempt, retry_reason) + "\n"

        if line_break_prompt_str:
            prompt_prefix += f"{line_break_prompt_str}\n\n---\n\n"

        if custom_prompt_str:
            prompt_prefix += f"{custom_prompt_str}\n\n---\n\n"

        return prompt_prefix

    def _build_system_prompt_without_glossary(
        self,
        prompt_prefix: str,
        base_prompt: str,
        target_lang_full: str,
    ) -> str:
        """普通翻译模式：基础系统提示 + 标准 translations 输出格式。"""
        final_prompt = prompt_prefix + base_prompt
        output_format_prompt = get_system_prompt_hq_format_prompt(target_lang_full, extract_glossary=False)
        if output_format_prompt:
            final_prompt += "\n\n---\n\n" + output_format_prompt
        else:
            self.logger.info("未启用自动术语提取，但未加载到标准输出格式提示词。")
        return final_prompt

    def _build_system_prompt_with_glossary(
        self,
        prompt_prefix: str,
        base_prompt: str,
        target_lang_full: str,
    ) -> str:
        """术语提取模式：基础系统提示 + 术语提取规则 + 扩展输出格式。"""
        final_prompt = prompt_prefix + base_prompt
        extraction_prompt = get_glossary_extraction_prompt(target_lang_full)
        output_format_prompt = get_system_prompt_hq_format_prompt(target_lang_full, extract_glossary=True)
        glossary_sections = [p for p in (extraction_prompt, output_format_prompt) if p]

        if glossary_sections:
            final_prompt += "\n\n---\n\n" + "\n\n---\n\n".join(glossary_sections)
            self.logger.info("已启用自动术语提取，使用带 new_terms 输出格式的系统提示词。")
        else:
            self.logger.info("已启用自动术语提取，但未加载到术语提取附加提示词。")

        return final_prompt

    def _build_system_prompt(
        self,
        source_lang: str,
        target_lang: str,
        custom_prompt_json: dict = None,
        line_break_prompt_json: dict = None,
        retry_attempt: int = 0,
        retry_reason: str = "",
        extract_glossary: bool = False,
    ) -> str:
        """
        构建完整的系统提示词（统一实现，所有翻译器共享）。

        不开启自动术语提取时：
        [重试提示] → [断句提示] → [自定义提示] → [基础系统提示] → [标准输出格式]

        开启自动术语提取时：
        [重试提示] → [断句提示] → [自定义提示] → [基础系统提示] → [术语提取规则] → [扩展输出格式]
        """
        target_lang_full = VALID_LANGUAGES.get(target_lang, target_lang)

        # --- 处理自定义提示词 ---
        custom_prompt_str = ""
        if custom_prompt_json:
            custom_prompt_str = _flatten_prompt_data(custom_prompt_json)

        # --- 处理断句提示词 ---
        line_break_prompt_str = ""
        if line_break_prompt_json and line_break_prompt_json.get('line_break_prompt'):
            line_break_prompt_str = line_break_prompt_json['line_break_prompt']

        # --- 加载 HQ System Prompt（优先 YAML，兼容 JSON） ---
        import os

        from ..utils import BASE_PATH
        from .prompt_loader import load_system_prompt_hq
        dict_dir = os.path.join(BASE_PATH, 'dict')
        base_prompt = load_system_prompt_hq(dict_dir)

        # Fallback
        if not base_prompt:
            base_prompt = self._HQ_FALLBACK_PROMPT

        # --- 替换占位符 ---
        base_prompt = base_prompt.replace("{{{target_lang}}}", target_lang_full)
        if custom_prompt_str:
            custom_prompt_str = custom_prompt_str.replace("{{{target_lang}}}", target_lang_full)

        prompt_prefix = self._build_system_prompt_prefix(
            line_break_prompt_str=line_break_prompt_str,
            custom_prompt_str=custom_prompt_str,
            retry_attempt=retry_attempt,
            retry_reason=retry_reason,
        )

        if extract_glossary:
            return self._build_system_prompt_with_glossary(
                prompt_prefix=prompt_prefix,
                base_prompt=base_prompt,
                target_lang_full=target_lang_full,
            )

        return self._build_system_prompt_without_glossary(
            prompt_prefix=prompt_prefix,
            base_prompt=base_prompt,
            target_lang_full=target_lang_full,
        )

    def _build_unified_user_prompt(self, batch_data: List[Dict], ctx=None, prev_context: str = "", retry_attempt: int = 0, retry_reason: str = "", is_image_mode: bool = True) -> str:
        """
        统一的用户提示词构建方法（支持多模态和纯文本）
        Unified user prompt builder for both multimodal and text-only modes.

        Args:
            batch_data: List of dicts, each containing 'original_texts' and optional 'text_regions'.
            ctx: Context object.
            prev_context: 保留兼容；历史上下文现在作为独立消息注入，不再拼进当前用户提示词。
            retry_attempt: Retry attempt count.
            retry_reason: Reason for retry.
            is_image_mode: Whether to include image-specific descriptions.

        Returns:
            Constructed user prompt string.
        """
        import json
        
        # 检查是否开启AI断句
        enable_ai_break = False
        if ctx and hasattr(ctx, 'config') and ctx.config and hasattr(ctx.config, 'render'):
            enable_ai_break = getattr(ctx.config.render, 'disable_auto_wrap', False)

        prompt = ""

        # 添加重试提示到最前面（如果是重试）
        if retry_attempt > 0:
            prompt += self._get_retry_hint(retry_attempt, retry_reason) + "\n"

        if is_image_mode:
            prompt += "Please translate the following manga text regions. I'm providing multiple images with their text regions in reading order:\n\n"
            # 添加图片信息
            for i, data in enumerate(batch_data):
                prompt += f"=== Image {i+1} ===\n"
                prompt += f"Text regions ({len(data['original_texts'])} regions):\n"
                text_order = data.get('text_order', [])
                for j, text in enumerate(data['original_texts']):
                    if text is not None:
                        display_id = text_order[j] if j < len(text_order) else (j + 1)
                        prompt += f"  {display_id}. {text}\n"
                prompt += "\n"
        else:
            prompt += "Please translate the following manga text regions:\n\n"

        prompt += "All texts to translate (JSON Array):\n"
        input_data = []
        text_index = 1
        for img_idx, data in enumerate(batch_data):
            # 获取 text_regions 用于 AI 断句
            text_regions = data.get('text_regions', [])
            text_order = data.get('text_order', [])
            
            for region_idx, text in enumerate(data['original_texts']):
                # 跳过 None 值
                if text is None:
                    self.logger.warning(f"跳过 None 文本 (img_idx={img_idx}, region_idx={region_idx})")
                    continue
                
                # 预处理文本：移除换行符
                text_clean = text.replace('\n', ' ').replace('\ufffd', '')
                
                # HQ 模式优先使用 text_order，确保与图片编号一致
                item_id = text_order[region_idx] if region_idx < len(text_order) else text_index
                item = {
                    "id": item_id,
                    "text": text_clean
                }
                
                # AI 断句逻辑：获取 original_region_count
                if enable_ai_break:
                    region_count = 1
                    # 尝试从 text_regions 获取
                    if text_regions and region_idx < len(text_regions):
                        region = text_regions[region_idx]
                        if hasattr(region, 'lines') and region.lines is not None:
                            region_count = len(region.lines)
                        elif isinstance(region, dict) and 'lines' in region:
                            region_count = len(region['lines'])
                    
                    # 如果获取失败（比如纯文本模式下 text_regions 为空），回退到数换行符
                    if region_count == 1 and text:
                        newline_count = text.count('\n')
                        if newline_count > 0:
                            region_count = newline_count + 1
                    
                    item["original_region_count"] = region_count
                
                input_data.append(item)
                text_index += 1

        prompt += json.dumps(input_data, ensure_ascii=False, indent=2)
        prompt += "\n\nCRITICAL: Provide translations in the exact same order as the input array. Follow the OUTPUT FORMAT specified in the System Prompt."

        return prompt

    def _build_user_prompt_for_hq(self, batch_data: List, ctx=None, prev_context: str = "", retry_attempt: int = 0, retry_reason: str = "") -> str:
        """Alias for backward compatibility (HQ mode)"""
        return self._build_unified_user_prompt(batch_data, ctx, prev_context, retry_attempt, retry_reason, is_image_mode=True)

    def _build_user_prompt_for_texts(self, texts: List[str], ctx=None, prev_context: str = "", retry_attempt: int = 0, retry_reason: str = "") -> str:
        """Alias for text mode: wraps texts into batch_data"""
        # 构造伪 batch_data
        batch_data = [{
            'original_texts': texts,
            'text_regions': getattr(ctx, 'text_regions', []) if ctx else []
        }]
        return self._build_unified_user_prompt(batch_data, ctx, prev_context, retry_attempt, retry_reason, is_image_mode=False)

    def _validate_br_markers(self, translations: List[str], queries: List[str] = None, ctx=None, batch_indices: List[int] = None, batch_data: List = None, split_level: int = 0) -> bool:
        """
        检查翻译结果是否包含必要的[BR]标记
        Check if translations contain necessary [BR] markers

        同时清理单区域（region_count == 1）翻译中多余的断句标记：
        该清理只依赖 AI 断句（disable_auto_wrap）开关，与「AI断句检查」
        （check_br_and_retry）是否开启无关。

        Args:
            translations: 翻译结果列表
            queries: 原始查询列表（可选）
            ctx: 上下文（用于获取配置和区域信息）
            batch_indices: 批次索引列表（可选，用于定位text_regions）
            batch_data: 批次数据列表（可选，HQ翻译器使用）
            split_level: 分割级别（可选，用于跳过深度分割时的检查）

        Returns:
            True if validation passes, False if BR markers are missing
        """
        import re

        # 如果分割级别过深（>=3），跳过BR检查以避免无限重试
        if split_level >= 3:
            self.logger.info(f"[AI断句检查] 分割级别过深 (split_level={split_level})，跳过BR标记检查")
            return True

        # 检查是否启用了AI断句
        ai_break_enabled = False
        if ctx and hasattr(ctx, 'config') and hasattr(ctx.config, 'render'):
            ai_break_enabled = getattr(ctx.config.render, 'disable_auto_wrap', False)

        if not ai_break_enabled:
            return True  # AI断句未启用，不需要处理BR

        # 提取每个翻译对应的区域数
        region_counts = []
        single_region_indices = []  # 已确认是单区域（region_count < 2）的翻译下标
        if ctx and hasattr(ctx, 'text_regions') and ctx.text_regions:
            for idx in range(len(translations)):
                # 确定实际的region索引
                if batch_indices and idx < len(batch_indices):
                    region_idx = batch_indices[idx]
                else:
                    region_idx = idx

                if region_idx < len(ctx.text_regions):
                    region = ctx.text_regions[region_idx]
                    region_count = len(region.lines) if hasattr(region, 'lines') else 1
                    region_counts.append(region_count)
                    if region_count < 2:
                        single_region_indices.append(idx)
                else:
                    region_counts.append(1)  # 默认为1
        elif batch_data:
            # HQ翻译器使用batch_data
            for idx in range(len(translations)):
                region_idx = idx
                for data in batch_data:
                    if 'text_regions' in data and data['text_regions'] and region_idx < len(data['text_regions']):
                        region = data['text_regions'][region_idx]
                        region_count = len(region.lines) if hasattr(region, 'lines') else 1
                        region_counts.append(region_count)
                        if region_count < 2:
                            single_region_indices.append(idx)
                        break
                else:
                    region_counts.append(1)
        else:
            region_counts = [1] * len(translations)  # 默认都为1

        # 单区域清理（与「AI断句检查」开关无关，只依赖 AI 断句开启）：
        # 模型不遵守 N=1 规则返回 [BR]/<br>/【BR】 时，统一清理成单行，
        # 避免单区域被渲染成多行。
        for idx in single_region_indices:
            translation = translations[idx]
            if translation:
                cleaned = re.sub(r'\s*(\[BR\]|【BR】|<br\s*/?>)\s*', '', translation, flags=re.IGNORECASE)
                if cleaned != translation:
                    self.logger.info(
                        f"[AI断句] 单区域翻译 #{idx+1} 自动清理多余断句标记: {translation[:50]!r} -> {cleaned[:50]!r}"
                    )
                    translations[idx] = cleaned

        # 检查是否启用了BR检查
        check_enabled = False
        if ctx and hasattr(ctx, 'config') and hasattr(ctx.config, 'render'):
            check_enabled = getattr(ctx.config.render, 'check_br_and_retry', False)

        if not check_enabled:
            return True  # 检查未启用，直接通过

        # 检查每个翻译，统计缺失BR的数量
        needs_check_count = 0
        missing_br_count = 0
        missing_indices = []

        for idx, (translation, region_count) in enumerate(zip(translations, region_counts)):
            # 只检查区域数≥2的翻译
            if region_count >= 2:
                needs_check_count += 1
                # 检查是否包含BR标记
                has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', translation, flags=re.IGNORECASE))
                if not has_br:
                    missing_br_count += 1
                    missing_indices.append(idx + 1)
                    self.logger.warning(
                        f"Translation {idx+1} missing [BR] markers (expected for {region_count} regions): {translation[:50]}..."
                    )

        # 计算容忍的错误数量：十分之一，最少1个
        if needs_check_count > 0:
            tolerance = max(1, needs_check_count // 10)

            if missing_br_count > tolerance:
                # 超过容忍度，验证失败
                self.logger.warning(
                    f"[AI断句检查] 缺失BR标记的翻译数 ({missing_br_count}/{needs_check_count}) 超过容忍度 ({tolerance})，需要重试"
                )
                return False
            elif missing_br_count > 0:
                # 在容忍度内，警告但通过
                self.logger.warning(
                    f"[AI断句检查] ⚠ {missing_br_count}/{needs_check_count} 条翻译缺失BR标记，但在容忍度内 ({tolerance})，继续执行"
                )
                return True
            else:
                # 全部通过
                self.logger.info(f"[AI断句检查] ✓ 所有多行区域的翻译都包含[BR]标记 (检查了 {needs_check_count}/{len(translations)} 条)")
                return True

        return True  # 没有需要检查的翻译，直接通过

    def _validate_translation_quality(self, queries: List[str], translations: List[str]) -> Tuple[bool, str]:
        """
        验证翻译质量，检测常见问题

        Args:
            queries: 原文列表
            translations: 译文列表

        Returns:
            (is_valid, error_message)
        """
        # 1. 检查数量匹配 (这是必须的，不能跳过)
        if len(translations) != len(queries):
            return False, f"Translation count mismatch: expected {len(queries)}, got {len(translations)}"

        # 2. 检查空翻译（原文不为空但译文为空）- 已禁用
        # empty_translation_errors = []
        # for i, (source, translation) in enumerate(zip(queries, translations)):
        #     if source.strip() and not translation.strip():
        #         empty_translation_errors.append(i + 1)
        # 
        # if empty_translation_errors:
        #     return False, f"Empty translation detected at positions: {empty_translation_errors}"

        # 3. 检查合并翻译（原文是正常文本但译文只有标点）- 已禁用
        # for i, (source, translation) in enumerate(zip(queries, translations)):
        #     is_source_simple = all(char in string.punctuation or char.isspace() for char in source)
        #     is_translation_simple = all(char in string.punctuation or char.isspace() for char in translation)
        #
        #     if is_translation_simple and not is_source_simple:
        #         return False, f"Detected potential merged translation at position {i+1}"

        # 4. 检查可疑符号（模型幻觉）- 已禁用
        # SUSPICIOUS_SYMBOLS = ["ହ", "ି", "ഹ"]
        # for symbol in SUSPICIOUS_SYMBOLS:
        #     for translation in translations:
        #         if symbol in translation:
        #             return False, f"Suspicious symbol '{symbol}' detected in translation"

        return True, ""

    def _reset_global_attempt_count(self):
        """重置全局尝试计数器（每次新的翻译任务开始时调用）"""
        self._global_attempt_count = 0
        self._max_total_attempts = self._resolve_max_total_attempts()

    def _increment_global_attempt(self) -> bool:
        """
        增加全局尝试计数，返回是否还可以继续尝试

        Returns:
            True: 还可以继续尝试
            False: 已达到总次数上限
        """
        self._global_attempt_count += 1

        # 无限重试模式
        if self._max_total_attempts == -1:
            return True

        # 检查是否超过上限（注意：允许等于上限的这次请求执行）
        if self._global_attempt_count > self._max_total_attempts:
            self.logger.warning(f"Exceeded max total attempts: {self._global_attempt_count}/{self._max_total_attempts}")
            return False

        return True

    class SplitException(Exception):
        """用于触发分割的特殊异常"""
        def __init__(self, attempt_count, texts):
            self.attempt_count = attempt_count
            self.texts = texts
            super().__init__(f"Split triggered after {attempt_count} attempts")

    async def _translate_with_split(self, translator_func, texts: List[str], split_level: int = 0, **kwargs) -> List[str]:
        """
        带分割重试的翻译包装器（新逻辑）

        Args:
            translator_func: 实际的翻译函数（async callable）
            texts: 要翻译的文本列表
            split_level: 当前分割层级
            **kwargs: 传递给translator_func的其他参数

        Returns:
            翻译结果列表
        """
        # 检查是否超过全局尝试次数
        if self._max_total_attempts != -1 and self._global_attempt_count >= self._max_total_attempts:
            self.logger.error(f"Global attempt limit reached before translation: {self._global_attempt_count}/{self._max_total_attempts}")
            raise Exception(f"Translation failed: reached max total attempts ({self._max_total_attempts})")

        try:
            # 尝试翻译（内部会检查是否需要分割）
            translations = await translator_func(texts, split_level=split_level, **kwargs)
            return translations

        except self.SplitException as split_ex:
            # 触发分割
            if split_level < self._MAX_SPLIT_ATTEMPTS and len(texts) > 1:
                self.logger.warning(
                    f"Splitting after {split_ex.attempt_count} attempts at split_level={split_level}, "
                    f"batch size {len(texts)} → splitting into two halves"
                )

                # 分成两半（只分割texts，不分割batch_data等其他参数）
                mid = len(texts) // 2
                left_texts = texts[:mid]
                right_texts = texts[mid:]

                self.logger.info(f"Split: left={len(left_texts)}, right={len(right_texts)}, global_attempts={self._global_attempt_count}/{self._max_total_attempts}")

                # 并发翻译左右两部分（kwargs保持完整传递）
                try:
                    left_translations, right_translations = await asyncio.gather(
                        self._translate_with_split(translator_func, left_texts, split_level + 1, **kwargs),
                        self._translate_with_split(translator_func, right_texts, split_level + 1, **kwargs),
                        return_exceptions=False
                    )
                except Exception as split_error:
                    # 如果并发失败，回退到串行处理
                    self.logger.warning(f"Concurrent split failed, falling back to sequential: {split_error}")
                    left_translations = await self._translate_with_split(translator_func, left_texts, split_level + 1, **kwargs)
                    right_translations = await self._translate_with_split(translator_func, right_texts, split_level + 1, **kwargs)

                # 合并结果
                return left_translations + right_translations

            else:
                # 不能再分割了，终止翻译进程
                if len(texts) == 1:
                    self.logger.error(f"Single text translation failed at split_level={split_level}: {texts[0][:50]}...")
                    raise Exception(f"Translation failed for single text after {split_ex.attempt_count} attempts")
                else:
                    self.logger.error(f"Max split level ({self._MAX_SPLIT_ATTEMPTS}) reached, batch size={len(texts)}")
                    raise Exception(f"Translation failed: max split level reached with batch size {len(texts)}")

        except Exception as e:
            # 其他异常（非分割触发的），直接终止
            self.logger.error(f"Translation failed with exception at split_level={split_level}: {e}")
            raise e

    def parse_args(self, config):
        translator_config = self._resolve_translator_config(config)
        self._enable_streaming = self._get_config_value(
            translator_config,
            'enable_streaming',
            self._enable_streaming,
        )
        self.enable_post_translation_check = getattr(
            translator_config,
            'enable_post_translation_check',
            self.enable_post_translation_check,
        )
        self.post_check_repetition_threshold = getattr(
            translator_config,
            'post_check_repetition_threshold',
            self.post_check_repetition_threshold,
        )
        self.post_check_max_retry_attempts = getattr(
            translator_config,
            'post_check_max_retry_attempts',
            self.post_check_max_retry_attempts,
        )
        self.attempts = get_retry_attempts_from_config(config, logger=self.logger, fallback=-1)
        self._max_total_attempts = self._resolve_max_total_attempts()

    def _emit_stream_lines(self, prefix: str, text: str, width: int = 100) -> None:
        """将流式增量按固定宽度换行输出，避免命令行单行过长被截断。"""
        content = (text or "").strip()
        if not content:
            return
        for line in textwrap.wrap(content, width=width, break_long_words=True, break_on_hyphens=False):
            self.logger.info(f"{prefix} {line}")

    def _reset_stream_json_preview(self) -> None:
        self._stream_json_seen = {}
        self._stream_term_seen = {}
        self._stream_preview_buffer = ""
        self._stream_preview_scan_pos = 0
        self._stream_preview_object_starts = []
        self._stream_preview_in_string = False
        self._stream_preview_escape = False
        self._stream_preview_tag_tail = ""
        self._stream_preview_in_think = False
        self._stream_result_header_printed = False
        self._stream_result_pairs_printed = False

    def _has_stream_result_pairs(self) -> bool:
        return bool(self._stream_result_pairs_printed)

    def _emit_final_translation_results(self, source_texts: List[str], translations: List[str]) -> None:
        """
        输出最终翻译结果。
        - 若流式预览没有完整覆盖最终结果，则补打一份最终快照
        - 若预览内容与最终结果一致，则只输出结尾分隔线，避免重复刷屏
        """
        should_log_full = not self._has_stream_result_pairs()

        if not should_log_full:
            if len(source_texts) != len(translations):
                should_log_full = True
            else:
                for tid, translated in enumerate(translations, start=1):
                    if self._stream_json_seen.get(tid) != translated:
                        should_log_full = True
                        break

        if should_log_full:
            if self._stream_result_header_printed:
                self.logger.info("--- Final Translation Results ---")
            else:
                self.logger.info("--- Translation Results ---")
                self._stream_result_header_printed = True
            for original, translated in zip(source_texts, translations):
                self.logger.info(f"{original} -> {translated}")
            self._stream_result_pairs_printed = True

        self.logger.info("---------------------------")

    def _emit_terms_from_list(self, new_terms: List[Dict[str, str]]) -> None:
        """统一输出术语提取结果；按(原文,译文)去重并优先保留带分类版本。"""
        if not new_terms:
            return
        for term in new_terms:
            if not isinstance(term, dict):
                continue
            term_o = str(term.get("original") or term.get("src") or "").strip()
            term_t = str(term.get("translation") or term.get("dst") or "").strip()
            term_c = str(term.get("category") or "").strip()
            if not term_o or not term_t:
                continue

            key = (term_o, term_t)
            prev_category = self._stream_term_seen.get(key)
            if prev_category is not None:
                if prev_category == term_c:
                    continue
                if prev_category and not term_c:
                    continue
            # 无分类先缓存，不立即输出；后续若拿到分类再输出，避免重复两条
            if not term_c and prev_category is None:
                self._stream_term_seen[key] = ""
                continue
            self._stream_term_seen[key] = term_c

            if term_c:
                self.logger.info(f"[TERM] {term_o} -> {term_t} ({term_c})")
            else:
                self.logger.info(f"[TERM] {term_o} -> {term_t}")

    def _filter_stream_preview_delta(self, delta_text: str) -> str:
        """
        以流式方式剥离 <think>/<answer> 标签，仅返回应参与 JSON 预览解析的新增正文。
        """
        if not delta_text:
            return ""

        known_tags = ("<think>", "</think>", "<answer>", "</answer>")
        data = f"{self._stream_preview_tag_tail}{delta_text}"
        self._stream_preview_tag_tail = ""
        out_parts: List[str] = []
        i = 0

        while i < len(data):
            if self._stream_preview_in_think:
                close_idx = data.lower().find("</think>", i)
                if close_idx == -1:
                    keep = min(len("</think>") - 1, len(data) - i)
                    self._stream_preview_tag_tail = data[-keep:] if keep > 0 else ""
                    return "".join(out_parts)
                i = close_idx + len("</think>")
                self._stream_preview_in_think = False
                continue

            lt_idx = data.find("<", i)
            if lt_idx == -1:
                out_parts.append(data[i:])
                break

            if lt_idx > i:
                out_parts.append(data[i:lt_idx])

            lower_rem = data[lt_idx:].lower()
            matched = False
            for tag in known_tags:
                if lower_rem.startswith(tag):
                    matched = True
                    if tag == "<think>":
                        self._stream_preview_in_think = True
                    i = lt_idx + len(tag)
                    break
                if tag.startswith(lower_rem):
                    self._stream_preview_tag_tail = data[lt_idx:]
                    return "".join(out_parts)

            if matched:
                continue

            out_parts.append("<")
            i = lt_idx + 1

        return "".join(out_parts)

    def _consume_completed_stream_preview_objects(self) -> List[str]:
        """
        从 preview_buffer 的 scan_pos 开始增量扫描，提取新闭合的 JSON 对象。
        """
        completed_objects: List[str] = []
        i = self._stream_preview_scan_pos

        while i < len(self._stream_preview_buffer):
            ch = self._stream_preview_buffer[i]

            if not self._stream_preview_object_starts:
                if ch == "{":
                    self._stream_preview_object_starts.append(i)
                    self._stream_preview_in_string = False
                    self._stream_preview_escape = False
                i += 1
                continue

            if self._stream_preview_in_string:
                if self._stream_preview_escape:
                    self._stream_preview_escape = False
                elif ch == "\\":
                    self._stream_preview_escape = True
                elif ch == '"':
                    self._stream_preview_in_string = False
            else:
                if ch == '"':
                    self._stream_preview_in_string = True
                elif ch == "{":
                    self._stream_preview_object_starts.append(i)
                elif ch == "}":
                    start = self._stream_preview_object_starts.pop()
                    completed_objects.append(self._stream_preview_buffer[start:i + 1])
                    if not self._stream_preview_object_starts:
                        self._stream_preview_in_string = False
                        self._stream_preview_escape = False

            i += 1

        self._stream_preview_scan_pos = i
        return completed_objects

    def _compact_stream_preview_buffer(self) -> None:
        """
        丢弃已经扫描且不再需要的前缀，避免 buffer 无界增长。
        """
        if not self._stream_preview_buffer:
            return

        trim_to = (
            min(self._stream_preview_object_starts)
            if self._stream_preview_object_starts
            else self._stream_preview_scan_pos
        )
        if trim_to <= 0:
            return

        self._stream_preview_buffer = self._stream_preview_buffer[trim_to:]
        self._stream_preview_scan_pos = max(0, self._stream_preview_scan_pos - trim_to)
        self._stream_preview_object_starts = [pos - trim_to for pos in self._stream_preview_object_starts]

    def _emit_stream_preview_translation_item(
        self,
        prefix: str,
        tid: int,
        translated: str,
        source_texts: Optional[List[str]] = None,
    ) -> None:
        translated = str(translated)
        if self._stream_json_seen.get(tid) == translated:
            return

        self._stream_json_seen[tid] = translated
        if not self._stream_result_header_printed:
            self.logger.info("--- Translation Results ---")
            self._stream_result_header_printed = True

        if source_texts and 1 <= tid <= len(source_texts):
            self.logger.info(f"{source_texts[tid - 1]} -> {translated}")
        else:
            self.logger.info(f"{prefix} #{tid}: {translated}")
        self._stream_result_pairs_printed = True

    def _process_stream_preview_object(
        self,
        prefix: str,
        obj_text: str,
        source_texts: Optional[List[str]] = None,
    ) -> None:
        try:
            parsed = json.loads(obj_text)
        except Exception:
            return

        if not isinstance(parsed, dict):
            return

        translation_items: List[Tuple[int, str]] = []
        if "id" in parsed and "translation" in parsed:
            try:
                translation_items.append((int(parsed["id"]), str(parsed["translation"])))
            except Exception:
                pass

        trans_list = parsed.get("translations")
        if not trans_list and "t" in parsed:
            trans_list = parsed.get("t")

        if isinstance(trans_list, list):
            if trans_list and isinstance(trans_list[0], dict):
                for item in trans_list:
                    if not isinstance(item, dict):
                        continue
                    if "id" not in item:
                        continue
                    text = item.get("translation")
                    if text is None:
                        text = item.get("text")
                    if text is None:
                        continue
                    try:
                        translation_items.append((int(item["id"]), str(text)))
                    except Exception:
                        continue
            else:
                for idx, text in enumerate(trans_list, start=1):
                    translation_items.append((idx, str(text)))

        for tid, translated in translation_items:
            self._emit_stream_preview_translation_item(prefix, tid, translated, source_texts=source_texts)

        term_items: List[Dict[str, str]] = []
        if "original" in parsed and "translation" in parsed:
            term_items.append(
                {
                    "original": str(parsed.get("original") or ""),
                    "translation": str(parsed.get("translation") or ""),
                    "category": str(parsed.get("category") or ""),
                }
            )

        nested_terms = parsed.get("new_terms") or parsed.get("glossary")
        if isinstance(nested_terms, list):
            for item in nested_terms:
                if not isinstance(item, dict):
                    continue
                term_items.append(
                    {
                        "original": str(item.get("original") or item.get("src") or ""),
                        "translation": str(item.get("translation") or item.get("dst") or ""),
                        "category": str(item.get("category") or ""),
                    }
                )

        if term_items:
            if not self._stream_result_header_printed:
                self.logger.info("--- Translation Results ---")
                self._stream_result_header_printed = True
            self._emit_terms_from_list(term_items)

    def _emit_stream_json_preview(self, prefix: str, delta_text: str, source_texts: Optional[List[str]] = None) -> None:
        """
        仅消费新增 delta_text，通过 preview_buffer + scan_pos 增量提取新闭合的 JSON 对象。
        """
        if not delta_text:
            return

        filtered_delta = self._filter_stream_preview_delta(str(delta_text))
        if filtered_delta:
            self._stream_preview_buffer += filtered_delta

        completed_objects = self._consume_completed_stream_preview_objects()
        for obj_text in completed_objects:
            self._process_stream_preview_object(prefix, obj_text, source_texts=source_texts)

        self._compact_stream_preview_buffer()

        if not filtered_delta and not completed_objects:
            return

    def _update_stream_inline(self, prefix: str, delta_text: str) -> None:
        """按增量流内容刷新；遇到换行符时真正换行输出。"""
        if not delta_text:
            return
        self._stream_inline_buffer += str(delta_text)

        # 输出完整行（包含模型返回的换行）
        while "\n" in self._stream_inline_buffer:
            line_text, rest = self._stream_inline_buffer.split("\n", 1)
            self._stream_inline_buffer = rest
            line = f"{prefix} {line_text}"
            pad = max(0, self._stream_inline_last_len - len(line))
            try:
                sys.stdout.write("\r" + line + (" " * pad) + "\n")
                sys.stdout.flush()
            except Exception:
                self._emit_stream_lines(prefix, line_text)
            self._stream_inline_last_len = 0

        # 没有换行时做同一行刷新
        text = self._stream_inline_buffer
        if not text:
            return
        try:
            term_width = shutil.get_terminal_size(fallback=(120, 24)).columns
        except Exception:
            term_width = 120
        # 预留少量边距，避免贴边抖动
        available = max(20, term_width - len(prefix) - 2)
        tail = text[-available:]
        line = f"{prefix} {tail}"
        pad = max(0, self._stream_inline_last_len - len(line))
        try:
            sys.stdout.write("\r" + line + (" " * pad))
            sys.stdout.flush()
            self._stream_inline_last_len = len(line)
        except Exception:
            # 终端不可写时退回普通日志
            self._emit_stream_lines(prefix, tail)

    def _finish_stream_inline(self) -> None:
        """结束同一行刷新，补一个换行。"""
        if self._stream_inline_last_len > 0 or self._stream_inline_buffer:
            try:
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass
            self._stream_inline_last_len = 0
            self._stream_inline_buffer = ""

    def supports_languages(self, from_lang: str, to_lang: str, fatal: bool = False) -> bool:
        supported_src_languages = ['auto'] + list(self._LANGUAGE_CODE_MAP)
        supported_tgt_languages = list(self._LANGUAGE_CODE_MAP)

        if from_lang not in supported_src_languages:
            if fatal:
                raise LanguageUnsupportedException(from_lang, self.__class__.__name__, supported_src_languages)
            return False
        if to_lang not in supported_tgt_languages:
            if fatal:
                raise LanguageUnsupportedException(to_lang, self.__class__.__name__, supported_tgt_languages)
            return False
        return True

    def parse_language_codes(self, from_lang: str, to_lang: str, fatal: bool = False) -> Tuple[str, str]:
        if not self.supports_languages(from_lang, to_lang, fatal):
            return None, None
        if type(self._LANGUAGE_CODE_MAP) is list:
            return from_lang, to_lang

        _from_lang = self._LANGUAGE_CODE_MAP.get(from_lang) if from_lang != 'auto' else 'auto'
        _to_lang = self._LANGUAGE_CODE_MAP.get(to_lang)
        return _from_lang, _to_lang

    async def translate(self, from_lang: str, to_lang: str, queries: List[str], use_mtpe: bool = False, ctx=None) -> List[str]:
        """
        Translates list of queries of one language into another.
        """
        if to_lang not in VALID_LANGUAGES:
            raise ValueError('Invalid language code: "%s". Choose from the following: %s' % (to_lang, ', '.join(VALID_LANGUAGES)))
        if from_lang not in VALID_LANGUAGES and from_lang != 'auto':
            raise ValueError('Invalid language code: "%s". Choose from the following: auto, %s' % (from_lang, ', '.join(VALID_LANGUAGES)))
        self.logger.info(f'Translating into {VALID_LANGUAGES[to_lang]}')

        if from_lang == to_lang:
            # 即使源语言和目标语言相同，也应用文本清理（如全角句点替换）
            return [self._clean_translation_output(q, q, to_lang) for q in queries]

        # Dont translate queries without text
        query_indices = []
        final_translations = []
        for i, query in enumerate(queries):
            if not is_valuable_text(query):
                final_translations.append(queries[i])
            else:
                final_translations.append(None)
                query_indices.append(i)

        queries = [queries[i] for i in query_indices]

        translations = [''] * len(queries)
        untranslated_indices = list(range(len(queries)))
        for i in range(1 + self._INVALID_REPEAT_COUNT): # Repeat until all translations are considered valid
            if i > 0:
                self.logger.warning(f'Repeating because of invalid translation. Attempt: {i+1}')
                await asyncio.sleep(0.1)

            # Sleep if speed is over the ratelimit
            await self._ratelimit_sleep()

            # Translate
            _translations = await self._translate(*self.parse_language_codes(from_lang, to_lang, fatal=True), queries, ctx=ctx)

            # Strict validation: translation count must match query count
            if len(_translations) != len(queries):
                error_msg = f"Translation count mismatch: expected {len(queries)}, got {len(_translations)}"
                self.logger.error(error_msg)
                self.logger.error(f"Queries: {queries}")
                self.logger.error(f"Translations: {_translations}")
                raise InvalidServerResponse(error_msg)

            # Only overwrite yet untranslated indices
            for j in untranslated_indices:
                translations[j] = _translations[j]

            if self._INVALID_REPEAT_COUNT == 0:
                break

            new_untranslated_indices = []
            for j in untranslated_indices:
                q, t = queries[j], translations[j]
                # Repeat invalid translations with slightly modified queries
                if self._is_translation_invalid(q, t):
                    new_untranslated_indices.append(j)
                    queries[j] = self._modify_invalid_translation_query(q, t)
            untranslated_indices = new_untranslated_indices

            if not untranslated_indices:
                break

        translations = [self._clean_translation_output(q, r, to_lang) for q, r in zip(queries, translations)]

        if to_lang == 'ARA':
            import arabic_reshaper
            import bidi.algorithm
            translations = [bidi.algorithm.get_display(arabic_reshaper.reshape(t)) for t in translations]

        if use_mtpe:
            translations = await self.mtpe_adapter.dispatch(queries, translations)

        # Merge with the queries without text
        for i, trans in enumerate(translations):
            final_translations[query_indices[i]] = trans
            self.logger.info(f'{i}: {queries[i]} => {trans}')

        return final_translations

    @abstractmethod
    async def _translate(self, from_lang: str, to_lang: str, queries: List[str], ctx=None) -> List[str]:
        pass

    async def _ratelimit_sleep(self):
        if self._MAX_REQUESTS_PER_MINUTE > 0:
            now = time.time()
            ratelimit_timeout = self._last_request_ts + 60 / self._MAX_REQUESTS_PER_MINUTE
            if ratelimit_timeout > now:
                self.logger.info(f'Ratelimit sleep: {(ratelimit_timeout-now):.2f}s')
                await asyncio.sleep(ratelimit_timeout-now)
            self._last_request_ts = time.time()

    def _is_translation_invalid(self, query: str, trans: str) -> bool:
        if not trans and query:
            return True
        if not query or not trans:
            return False

        query_symbols_count = len(set(query))
        trans_symbols_count = len(set(trans))
        if query_symbols_count > 6 and trans_symbols_count < 6 and trans_symbols_count < 0.25 * len(trans):
            return True
        return False

    def _modify_invalid_translation_query(self, query: str, trans: str) -> str:
        """
        Can be overwritten if _INVALID_REPEAT_COUNT was set. It modifies the query
        for the next translation attempt.
        """
        return query

    def _clean_translation_output(self, query: str, trans: str, to_lang: str) -> str:
        """
        Tries to spot and skim down invalid translations.
        """
        if not query or not trans:
            return ''

        # 移除内部标记：【Original regions: X】或 [Original regions: X]
        # Remove internal markers: 【Original regions: X】 or [Original regions: X]
        trans = re.sub(r'【Original regions:\s*\d+】\s*', '', trans, flags=re.IGNORECASE)
        trans = re.sub(r'\[Original regions:\s*\d+\]\s*', '', trans, flags=re.IGNORECASE)
        
        # 替换全角句点连续出现（．．．或．．）为省略号
        trans = trans.replace('．．．', '…')
        trans = trans.replace('．．', '…')

        # '  ' -> ' '
        trans = re.sub(r'\s+', r' ', trans)
        # 'text.text' -> 'text. text'
        trans = re.sub(r'(?<![.,;!?])([.,;!?])(?=\w)', r'\1 ', trans)
        # ' ! ! . . ' -> ' !!.. '
        trans = re.sub(r'([.,;!?])\s+(?=[.,;!?]|$)', r'\1', trans)

        if to_lang != 'ARA':
            # 'text .' -> 'text.'
            trans = re.sub(r'(?<=[.,;!?\w])\s+([.,;!?])', r'\1', trans)
            # ' ... text' -> ' ...text'
            trans = re.sub(r'((?:\s|^)\.+)\s+(?=\w)', r'\1', trans)

        seq = repeating_sequence(trans.lower())

        # 'aaaaaaaaaaaaa' -> 'aaaaaa'
        if len(trans) < len(query) and len(seq) < 0.5 * len(trans):
            # Shrink sequence to length of original query
            trans = seq * max(len(query) // len(seq), 1)
            # Transfer capitalization of query to translation
            nTrans = ''
            for i in range(min(len(trans), len(query))):
                nTrans += trans[i].upper() if query[i].isupper() else trans[i]
            trans = nTrans

        # words = text.split()
        # elements = list(set(words))
        # if len(elements) / len(words) < 0.1:
        #     words = words[:int(len(words) / 1.75)]
        #     text = ' '.join(words)

        #     # For words that appear more then four times consecutively, remove the excess
        #     for el in elements:
        #         el = re.escape(el)
        #         text = re.sub(r'(?: ' + el + r'){4} (' + el + r' )+', ' ', text)

        return trans

class OfflineTranslator(CommonTranslator, ModelWrapper):
    _MODEL_SUB_DIR = 'translators'

    async def _translate(self, *args, **kwargs):
        return await self.infer(*args, **kwargs)

    @abstractmethod
    async def _infer(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        pass

    async def load(self, from_lang: str, to_lang: str, device: str):
        return await super().load(device, *self.parse_language_codes(from_lang, to_lang))

    @abstractmethod
    async def _load(self, from_lang: str, to_lang: str, device: str):
        pass

    async def reload(self, from_lang: str, to_lang: str, device: str):
        return await super().reload(device, from_lang, to_lang)


    async def unload(self, device: str):
        return await super().unload()

def sanitize_text_encoding(text: str) -> str:
    """
    统一的文本编码清理函数，处理各种编码问题
    Unified text encoding sanitization to handle various encoding issues
    
    Args:
        text: 输入文本
        
    Returns:
        清理后的文本
    """
    if not text:
        return text
    
    try:
        # 1. 尝试检测并修复UTF-16-LE编码问题
        # 如果文本包含UTF-16-LE的BOM或特征，尝试重新解码
        if isinstance(text, bytes):
            # 如果是bytes，尝试多种编码
            for encoding in ['utf-8', 'utf-16-le', 'utf-16-be', 'latin-1']:
                try:
                    text = text.decode(encoding, errors='ignore')
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue
        
        # 2. 移除不可见的控制字符和损坏的字符
        # 保留常用的控制字符：换行(\n)、回车(\r)、制表符(\t)
        import unicodedata
        cleaned = []
        for char in text:
            # 跳过控制字符（除了\n, \r, \t）
            if unicodedata.category(char)[0] == 'C' and char not in '\n\r\t':
                continue
            # 跳过私有使用区字符（可能是损坏的编码）
            if '\uE000' <= char <= '\uF8FF':  # 私有使用区
                continue
            # 跳过替换字符（表示解码失败）
            if char == '\ufffd':
                continue
            cleaned.append(char)
        
        text = ''.join(cleaned)
        
        # 3. 修复常见的编码混淆问题
        # UTF-16-LE误识别为UTF-8时会产生的特征模式
        # 例如：每个字符后跟\x00
        if '\x00' in text:
            text = text.replace('\x00', '')
        
        # 4. 确保文本是有效的UTF-8
        # 通过编码再解码来验证和清理
        text = text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
        
        return text
        
    except Exception as e:
        import logging
        logger = logging.getLogger('manga_translator')
        logger.warning(f"文本编码清理失败: {e}，返回原文本")
        # 如果清理失败，至少移除明显的问题字符
        if isinstance(text, str):
            return text.replace('\ufffd', '').replace('\x00', '')
        return str(text)


def normalize_model_output_text(text: str, preview: bool = False) -> str:
    """
    清理模型在正文外包裹的控制标签，避免思考区和回答区交叉污染解析。

    preview=True 时会额外移除未闭合的 <think> 尾部，避免流式中途把思考区当正文。
    """
    if not text:
        return text

    cleaned = str(text)
    cleaned = re.sub(r'(?:</think>)?<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    if preview:
        cleaned = re.sub(r'<think>.*$', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    answer_match = re.search(r'<answer>(.*?)</answer>', cleaned, flags=re.DOTALL | re.IGNORECASE)
    if answer_match:
        cleaned = answer_match.group(1).strip()

    cleaned = re.sub(r'</?answer>', '', cleaned, flags=re.IGNORECASE)
    return cleaned



def extract_json_payload_from_mixed_text(text: str) -> Tuple[str, bool]:
    """
    通用 JSON 提取器：从混杂文本中优先提取最可能的 JSON 负载。
    返回 (payload, extracted)，extracted=True 表示确实抽取到了 JSON 片段。
    """
    import json
    import re

    if text is None:
        return "", False

    raw = str(text).strip()
    if not raw:
        return raw, False

    def _extract_balanced_json_candidates(src: str) -> List[str]:
        candidates = []
        stack = []
        start = -1
        in_str = False
        escape = False
        for i, ch in enumerate(src):
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in '{[':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch in '}]' and stack:
                top = stack[-1]
                if (top == '{' and ch == '}') or (top == '[' and ch == ']'):
                    stack.pop()
                    if not stack and start >= 0:
                        candidates.append(src[start:i + 1].strip())
                        start = -1
                else:
                    stack = []
                    start = -1
        return candidates

    def _is_json_parseable(candidate: str) -> bool:
        try:
            json.loads(candidate)
            return True
        except Exception:
            try:
                import json5
                json5.loads(candidate)
                return True
            except Exception:
                return False

    candidates: List[str] = []

    # 1) fenced code block candidates
    fenced = re.findall(r'`{3,}\s*(?:json)?\s*([\s\S]*?)`{3,}', raw, flags=re.IGNORECASE)
    for c in fenced:
        c = c.strip()
        if c:
            candidates.append(c)

    # 2) balanced-json candidates from full text
    candidates.extend(_extract_balanced_json_candidates(raw))

    # 3) fallback: from first bracket onward
    first_bracket = raw.find('[')
    first_brace = raw.find('{')
    json_start = -1
    if first_bracket != -1 and first_brace != -1:
        json_start = min(first_bracket, first_brace)
    elif first_bracket != -1:
        json_start = first_bracket
    elif first_brace != -1:
        json_start = first_brace
    if json_start >= 0:
        candidates.append(raw[json_start:].strip())

    # dedup keep-order
    dedup: List[str] = []
    seen = set()
    for c in candidates:
        key = c.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(key)

    if not dedup:
        return raw, False

    preferred_markers = ('"translations"', "'translations'", '"translation"', '"id"', '"new_terms"', '"glossary"')

    best_candidate = None
    best_score = (-1, -1)
    for c in dedup:
        low = c.lower()
        marker_score = sum(1 for m in preferred_markers if m in low)
        parseable_score = 1 if _is_json_parseable(c) else 0
        score = (parseable_score * 10 + marker_score, len(c))
        if score > best_score:
            best_score = score
            best_candidate = c

    if best_candidate is None:
        return raw, False
    return best_candidate, True

def parse_hq_response(result_text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """
    专门解析HQ翻译器的响应，支持提取翻译和新术语
    Parse HQ translator response, supporting extraction of translations and new terms
    
    Returns:
        (translations, new_terms)
    """
    import json
    import logging
    import re
    
    logger = logging.getLogger('manga_translator')
    
    # 统一的编码清理
    result_text = sanitize_text_encoding(result_text)
    result_text = normalize_model_output_text(result_text)
    extracted_payload, extracted = extract_json_payload_from_mixed_text(result_text)
    if extracted:
        result_text = extracted_payload
        logger.info("Extracted JSON payload from mixed response text")
    
    _original_text = result_text # Keep for logging
    result_text = result_text.strip()
    if not result_text:
        return [], []

    # 1. 从原始文本中收集候选 JSON 片段（兼容流式重复块）
    def _extract_balanced_json_candidates(text: str) -> List[str]:
        candidates = []
        stack = []
        start = -1
        in_str = False
        escape = False
        for i, ch in enumerate(text):
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in '{[':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch in '}]' and stack:
                top = stack[-1]
                if (top == '{' and ch == '}') or (top == '[' and ch == ']'):
                    stack.pop()
                    if not stack and start >= 0:
                        candidates.append(text[start:i + 1].strip())
                        start = -1
                else:
                    stack = []
                    start = -1
        return candidates

    raw_text = result_text
    candidate_texts: List[str] = []

    if "```" in raw_text:
        fenced = re.findall(r'```(?:json)?\s*\n(.*?)\n```', raw_text, flags=re.DOTALL)
        candidate_texts.extend([x.strip() for x in fenced if x and x.strip()])
        lines = raw_text.split('\n')
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped_text = "\n".join(lines).strip()
        if stripped_text:
            candidate_texts.append(stripped_text)
        result_text = stripped_text or raw_text

    # 2. 查找JSON起始 (清理前缀)
    first_bracket = result_text.find('[')
    first_brace = result_text.find('{')
    json_start = -1
    if first_bracket != -1 and first_brace != -1: json_start = min(first_bracket, first_brace)
    elif first_bracket != -1: json_start = first_bracket
    elif first_brace != -1: json_start = first_brace
    
    if json_start > 0:
        result_text = result_text[json_start:].strip()
    if result_text:
        candidate_texts.append(result_text)

    # 原文中所有平衡 JSON 片段也作为候选
    candidate_texts.extend(_extract_balanced_json_candidates(raw_text))

    # 去重且保序
    dedup_candidates = []
    seen_candidates = set()
    for c in candidate_texts:
        if not c:
            continue
        key = c.strip()
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        dedup_candidates.append(key)

    translations = []
    new_terms = []
    
    def _parse_candidate(candidate_text: str):
        parsed = None
        try:
            parsed = json.loads(candidate_text)
        except json.JSONDecodeError:
            try:
                import json5
                parsed = json5.loads(candidate_text)
                logger.info("Using json5 for parsing")
            except (ImportError, Exception):
                parsed = None
        if parsed is None:
            return None

        out_trans = []
        out_terms = []
        try:
            if isinstance(parsed, dict):
                trans_list = parsed.get("translations")
                if not trans_list and "t" in parsed:
                    trans_list = parsed.get("t")
                if not trans_list:
                    trans_list = []

                if isinstance(trans_list, list):
                    if trans_list and isinstance(trans_list[0], dict):
                        for item in trans_list:
                            text = item.get('translation') or item.get('text') or list(item.values())[0]
                            out_trans.append(str(text) if text is not None else "")
                    else:
                        out_trans = [str(x) for x in trans_list]

                terms_list = parsed.get("new_terms") or parsed.get("glossary")
                if isinstance(terms_list, list):
                    out_terms = terms_list
            elif isinstance(parsed, list):
                if parsed:
                    if isinstance(parsed[0], dict):
                        for item in parsed:
                            text = item.get('translation') or item.get('text') or list(item.values())[0]
                            out_trans.append(str(text) if text is not None else "")
                    else:
                        out_trans = [str(x) for x in parsed]
            return out_trans, out_terms
        except Exception:
            return None

    # 优先选择“翻译条目数最多”的候选；同分时选术语更多
    best = None
    best_score = (-1, -1)
    for c in dedup_candidates:
        parsed_result = _parse_candidate(c)
        if not parsed_result:
            continue
        cand_trans, cand_terms = parsed_result
        score = (len(cand_trans), len(cand_terms))
        if score > best_score:
            best_score = score
            best = (cand_trans, cand_terms)

    if best is not None:
        return best[0], best[1]

    # === 策略3: 正则表达式暴力提取 ===
    logger.warning("JSON parsing failed, falling back to Regex extraction")
    
    # 3.1 尝试提取带ID的对象: {"id": 1, "translation": "..."}
    object_pattern = r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"translation"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    matches = re.findall(object_pattern, result_text)
    
    if matches:
        logger.info(f"Regex extracted {len(matches)} translations with IDs")
        translations = [match[1].replace('\\"', '"').replace('\\n', '\n') for match in matches]
        
        # 尝试提取术语 (简单正则)
        # 假设 new_terms 在后面，格式类似 {"original": "...", ...}
        # 这里的正则很难完美匹配嵌套结构，只能尽力而为
        term_pattern = r'\{\s*"original"\s*:\s*"([^"]+)"\s*,\s*"translation"\s*:\s*"([^"]+)"\s*,\s*"category"\s*:\s*"([^"]+)"\s*\}'
        term_matches = re.findall(term_pattern, result_text)
        for tm in term_matches:
            new_terms.append({"original": tm[0], "translation": tm[1], "category": tm[2]})
            
        return translations, new_terms

    # 3.2 尝试只提取 translation 字段
    translation_pattern = r'"translation"\s*:\s*"((?:[^"\\]|\\.)*)"'
    matches = re.findall(translation_pattern, result_text)
    if matches:
         logger.warning(f"Regex extracted {len(matches)} translations (no IDs)")
         translations = [match.replace('\\"', '"').replace('\\n', '\n') for match in matches]
         return translations, []

    # 3.3 最后的兜底：按行分割 (仅当不像JSON时)
    if not result_text.startswith('{') and not result_text.startswith('['):
         for line in result_text.split('\n'):
            line = line.strip()
            if line:
                line = re.sub(r'^\d+\.\s*', '', line)
                line = line.replace('\\n', '\n').replace('↵', '\n')
                translations.append(line)

    return translations, new_terms

def parse_json_or_text_response(result_text: str) -> List[str]:
    """
    解析LLM返回的文本，支持JSON列表格式或按行分割格式
    Wrapper around parse_hq_response for backward compatibility
    """
    translations, _ = parse_hq_response(result_text)
    return translations





def merge_glossary_to_file(file_path: str, new_terms: List[Dict[str, str]]) -> bool:
    """
    将新提取的术语合并到提示词文件中
    Merge newly extracted terms into the prompt file
    
    支持 JSON (.json) 和 YAML (.yaml/.yml) 格式，根据文件扩展名自动选择。
    
    Args:
        file_path: 提示词文件路径
        new_terms: 新术语列表 [{"original": "...", "translation": "...", "category": "..."}]
    """
    import json
    import os

    from .prompt_loader import load_prompt_file
    
    if not new_terms:
        return False

    ext = os.path.splitext(file_path)[1].lower()
    is_yaml = ext in ('.yaml', '.yml')

    try:
        # 读取现有文件（统一使用 prompt_loader，自动支持 JSON/YAML）
        data = {}
        if os.path.exists(file_path):
            loaded = load_prompt_file(file_path)
            if loaded is not None:
                data = loaded
        
        # 确保结构完整
        if "glossary" not in data or not isinstance(data["glossary"], dict):
            # 如果旧格式是列表，或者没有 glossary，初始化为新的分类结构
            data["glossary"] = {
                "Person": [], "Location": [], "Org": [], "Item": [], "Skill": [], "Creature": []
            }
        
        glossary = data["glossary"]
        # 确保所有标准分类键都存在
        valid_keys_map = {
            "person": "Person", 
            "location": "Location", 
            "org": "Org", 
            "organization": "Org",
            "item": "Item", 
            "skill": "Skill", 
            "creature": "Creature"
        }
        
        # 确保标准键存在于 glossary 中
        for key in set(valid_keys_map.values()):
            if key not in glossary:
                glossary[key] = []

        modified = False
        
        for term in new_terms:
            raw_category = term.get("category", "Item")
            original = term.get("original")
            translation = term.get("translation")
            
            if not original or not translation:
                continue

            # 映射 Category 到标准 Key
            target_key = "Item" # Default fallback
            if raw_category:
                normalized_cat = raw_category.lower()
                if normalized_cat in valid_keys_map:
                    target_key = valid_keys_map[normalized_cat]
                else:
                    # 尝试模糊匹配或直接使用 Title Case
                    for k in valid_keys_map.values():
                        if k.lower() == normalized_cat:
                            target_key = k
                            break
            
            # 检查是否已存在 (根据 original 去重)
            exists = False
            if target_key in glossary:
                for existing_term in glossary[target_key]:
                    if existing_term.get("original") == original:
                        exists = True
                        break
            else:
                glossary[target_key] = []
            
            if not exists:
                glossary[target_key].append({
                    "original": original,
                    "translation": translation
                })
                modified = True
        
        if modified:
            # 确保存储目录存在
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                if is_yaml:
                    from .prompt_loader import _get_yaml
                    yaml = _get_yaml()
                    if yaml is not None:
                        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    else:
                        # YAML 不可用，回退到 JSON
                        json.dump(data, f, indent=2, ensure_ascii=False)
                else:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            return True
            
    except Exception as e:
        print(f"Error merging glossary: {e}")
        return False
    
    return False

def get_glossary_extraction_prompt(target_lang: str) -> str:
    """
    获取术语提取的追加提示词
    Get the additional prompt for glossary extraction
    """
    import os

    from ..utils import BASE_PATH
    from .prompt_loader import load_glossary_extraction_prompt as _load_glossary
    
    dict_dir = os.path.join(BASE_PATH, 'dict')
    return _load_glossary(dict_dir, target_lang)


def get_system_prompt_hq_format_prompt(target_lang: str, extract_glossary: bool = False) -> str:
    """
    获取 HQ 通用输出格式提示词。
    extract_glossary=False: 仅要求 translations
    extract_glossary=True: 要求 translations + new_terms
    """
    import os

    from ..utils import BASE_PATH
    from .prompt_loader import load_system_prompt_hq_format as _load_hq_format

    dict_dir = os.path.join(BASE_PATH, 'dict')
    return _load_hq_format(dict_dir, target_lang, extract_glossary=extract_glossary)


def get_glossary_output_format_prompt(target_lang: str) -> str:
    """
    兼容旧调用：获取开启术语提取时的 HQ 输出格式提示词。
    """
    return get_system_prompt_hq_format_prompt(target_lang, extract_glossary=True)
