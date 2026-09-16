"""阿里云机器翻译通用版（TranslateGeneral）翻译器。

实现参考 Immersive Translate 的阿里云签名流程，但使用本项目现有的
CommonTranslator 和 curl_cffi 网络层接入。该接口不是 OpenAI 兼容接口，
需要 AccessKey ID 与 AccessKey Secret 两个凭证。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..utils.curl_cffi_transport import (
    CURL_CFFI_IMPERSONATE,
    create_curl_cffi_async_session,
)
from ..utils.dotenv_utils import load_app_dotenv
from ..utils.system_proxy import system_proxy_request_kwargs
from ..utils.retry import is_retryable_api_error
from .common import CommonTranslator


ALIYUN_MT_API_BASE = "https://mt.cn-hangzhou.aliyuncs.com"
ALIYUN_MT_API_VERSION = "2018-10-12"
ALIYUN_MT_ACTION = "TranslateGeneral"
ALIYUN_MT_MAX_SOURCE_TEXT_LENGTH = 5000

# Aliyun TranslateGeneral language codes. The application-level language names
# stay unchanged so translator chains continue to use CHS/JPN/etc.
ALIYUN_LANGUAGE_CODE_MAP = {
    "CHS": "zh",
    "CHT": "zh-tw",
    "CSY": "cs",
    "NLD": "nl",
    "ENG": "en",
    "FRA": "fr",
    "DEU": "de",
    "HUN": "hu",
    "ITA": "it",
    "JPN": "ja",
    "KOR": "ko",
    "POL": "pl",
    "PTB": "pt",
    "ROM": "ro",
    "RUS": "ru",
    "ESP": "es",
    "TRK": "tr",
    "UKR": "uk",
    "VIN": "vi",
    "ARA": "ar",
    "CNR": "cnr",
    "THA": "th",
    "IND": "id",
    "FIL": "tl",
}


def aliyun_percent_encode(value: Any) -> str:
    """Encode a value as required by Aliyun RPC APIs.

    urllib.parse.quote already emits uppercase hexadecimal escapes and its
    RFC3986-safe set matches the final set used by the reference implementation.
    """

    return quote(str(value), safe="-_.~")


def aliyun_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def build_aliyun_translate_request(
    access_key_id: str,
    access_key_secret: str,
    source_text: str,
    source_language: str,
    target_language: str,
    *,
    api_base: str = ALIYUN_MT_API_BASE,
    signature_nonce: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(endpoint, form_body)`` for a signed TranslateGeneral call."""

    params: Dict[str, str] = {
        "AccessKeyId": access_key_id,
        "Action": ALIYUN_MT_ACTION,
        "Format": "JSON",
        "FormatType": "text",
        "Scene": "general",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": signature_nonce or str(uuid.uuid4()),
        "SignatureVersion": "1.0",
        "SourceLanguage": source_language,
        "SourceText": source_text,
        "TargetLanguage": target_language,
        "Timestamp": timestamp or aliyun_timestamp(),
        "Version": ALIYUN_MT_API_VERSION,
    }

    canonicalized_query_string = "&".join(
        f"{aliyun_percent_encode(key)}={aliyun_percent_encode(params[key])}"
        for key in sorted(params)
    )
    string_to_sign = (
        f"POST&%2F&{aliyun_percent_encode(canonicalized_query_string)}"
    )
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")
    body = (
        f"{canonicalized_query_string}"
        f"&Signature={aliyun_percent_encode(signature)}"
    )
    return api_base.rstrip("/"), body


def _response_error(payload: Any, status_code: int) -> str:
    if isinstance(payload, dict):
        code = payload.get("Code") or payload.get("code")
        message = payload.get("Message") or payload.get("message") or payload.get("msg")
        if code or message:
            return f"HTTP {status_code}, Code={code or 'unknown'}: {message or 'unknown error'}"
    return f"HTTP {status_code}: Aliyun returned an invalid translation response"


class AliyunTranslator(CommonTranslator):
    """Aliyun TranslateGeneral translator for ordinary text regions."""

    _LANGUAGE_CODE_MAP = ALIYUN_LANGUAGE_CODE_MAP
    _MAX_REQUESTS_PER_MINUTE = 0
    _REQUEST_TIMEOUT = 15.0

    def __init__(self):
        super().__init__()
        load_app_dotenv(override=True)
        self.access_key_id = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
        self.access_key_secret = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
        self.api_base = os.getenv("ALIYUN_API_BASE", ALIYUN_MT_API_BASE)

    def parse_args(self, config):
        super().parse_args(config)
        translator_config = self._resolve_translator_config(config)

        self.access_key_id = str(
            self._get_config_value(
                translator_config,
                "aliyun_access_key_id",
                None,
            )
            or os.getenv("ALIYUN_ACCESS_KEY_ID", "")
        ).strip()
        self.access_key_secret = str(
            self._get_config_value(
                translator_config,
                "aliyun_access_key_secret",
                None,
            )
            or os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
        ).strip()
        self.api_base = str(
            self._get_config_value(translator_config, "aliyun_api_base", None)
            or os.getenv("ALIYUN_API_BASE", ALIYUN_MT_API_BASE)
        ).strip().rstrip("/")

        max_rpm = self._get_config_value(translator_config, "max_requests_per_minute", 0)
        try:
            max_rpm = int(max_rpm or 0)
        except (TypeError, ValueError):
            max_rpm = 0
        if max_rpm > 0:
            self._MAX_REQUESTS_PER_MINUTE = max_rpm

    async def _request_translation(
        self,
        session,
        source_text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        endpoint, body = build_aliyun_translate_request(
            self.access_key_id,
            self.access_key_secret,
            source_text,
            source_language,
            target_language,
            api_base=self.api_base,
        )
        response = await session.post(
            endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=self._REQUEST_TIMEOUT,
            **system_proxy_request_kwargs(endpoint),
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"Aliyun returned invalid JSON (HTTP {response.status_code})"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(_response_error(payload, response.status_code))

        code = payload.get("Code") if isinstance(payload, dict) else None
        if str(code) != "200":
            raise RuntimeError(_response_error(payload, response.status_code))

        data = payload.get("Data") if isinstance(payload, dict) else None
        translated = data.get("Translated") if isinstance(data, dict) else None
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("Aliyun response did not contain Data.Translated")
        return translated

    async def _translate(
        self,
        from_lang: str,
        to_lang: str,
        queries: List[str],
        ctx=None,
    ) -> List[str]:
        del ctx
        if not self.access_key_id or not self.access_key_secret:
            raise RuntimeError(
                "Aliyun translator requires ALIYUN_ACCESS_KEY_ID and "
                "ALIYUN_ACCESS_KEY_SECRET"
            )

        if not queries:
            return []

        for query in queries:
            if len(str(query)) > ALIYUN_MT_MAX_SOURCE_TEXT_LENGTH:
                raise RuntimeError(
                    "Aliyun TranslateGeneral supports at most 5000 characters per request"
                )

        session = create_curl_cffi_async_session(
            base_url=self.api_base,
            impersonate=CURL_CFFI_IMPERSONATE,
        )
        translations: List[str] = []
        try:
            for index, query in enumerate(queries):
                self._check_cancelled()
                if index:
                    await self._ratelimit_sleep()

                attempt = 0
                while True:
                    try:
                        translations.append(
                            await self._request_translation(
                                session,
                                str(query),
                                from_lang,
                                to_lang,
                            )
                        )
                        break
                    except Exception as exc:
                        attempt += 1
                        max_attempts = self._max_total_attempts
                        retry_allowed = is_retryable_api_error(exc) and (
                            max_attempts == -1 or attempt < max_attempts
                        )
                        if not retry_allowed:
                            raise
                        self.logger.warning(
                            "Aliyun translation request failed, retrying "
                            f"({attempt}/{max_attempts if max_attempts != -1 else '∞'}): {exc}"
                        )
                        await self._sleep_with_cancel_polling(
                            min(1.0, 0.5 * attempt)
                        )
        finally:
            await session.close()
        return translations


async def test_aliyun_connection(
    access_key_id: str,
    access_key_secret: str,
    api_base: Optional[str] = None,
) -> tuple[bool, str]:
    """Make one small real request for the desktop API-management test button."""

    access_key_id = str(access_key_id or "").strip()
    access_key_secret = str(access_key_secret or "").strip()
    if not access_key_id or not access_key_secret:
        return False, "请同时配置 ALIYUN_ACCESS_KEY_ID 和 ALIYUN_ACCESS_KEY_SECRET。"

    base = str(api_base or ALIYUN_MT_API_BASE).strip().rstrip("/")
    session = create_curl_cffi_async_session(
        base_url=base,
        impersonate=CURL_CFFI_IMPERSONATE,
    )
    try:
        translator = AliyunTranslator()
        translator.access_key_id = access_key_id
        translator.access_key_secret = access_key_secret
        translator.api_base = base
        await translator._request_translation(session, "测试", "zh", "en")
        return True, "连接成功，阿里云 TranslateGeneral 可用"
    except Exception as exc:
        return False, str(exc)
    finally:
        await session.close()
