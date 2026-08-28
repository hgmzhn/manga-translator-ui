import _bootstrap  # noqa: I001
import asyncio
import json
from string import Formatter
from types import SimpleNamespace

import pytest

import app_logic
from services.i18n_service import I18nManager
from ui.main_page import env_management

ROOT = _bootstrap.ROOT
LOCALE_DIR = ROOT / "desktop_qt_ui" / "locales"
LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")
ERROR_KEYS = {
    "api_models_error_failed",
    "api_models_error_unsupported",
    "api_test_error_address_example",
    "api_test_error_config",
    "api_test_error_connection_failed",
    "api_test_error_invalid_key_characters",
    "api_test_error_gemini_no_image",
    "api_test_error_model_unavailable",
    "api_test_error_network",
    "api_test_error_raw",
    "api_test_error_remote_image",
    "api_test_error_sakura_base",
    "api_test_error_service",
    "api_test_error_unsupported",
    "friendly_error_api_404_html",
    "friendly_error_api_credentials",
    "friendly_error_br_markers",
    "friendly_error_colorizer_unsupported",
    "friendly_error_ocr_unavailable",
    "friendly_error_content_filter",
    "friendly_error_empty_ai_response",
    "friendly_error_generic",
    "friendly_error_http_403",
    "friendly_error_http_404",
    "friendly_error_http_429",
    "friendly_error_http_500",
    "friendly_error_http_gateway",
    "friendly_error_language_unsupported",
    "friendly_error_model_unsupported",
    "friendly_error_multimodal_unsupported",
    "friendly_error_network",
    "friendly_error_raw_details",
    "friendly_error_renderer_unsupported",
    "friendly_error_request_blocked",
    "friendly_error_translation_count",
    "friendly_error_translation_quality",
}


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(text) if field}


def _manager(locale: str) -> I18nManager:
    return I18nManager(
        locale_dir=str(LOCALE_DIR),
        fallback_locale="zh_CN",
        config_language=locale,
    )


def test_error_catalogs_are_complete_and_preserve_placeholders():
    catalogs = {
        locale: json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in LOCALES
    }
    reference = catalogs["zh_CN"]

    for locale, catalog in catalogs.items():
        assert ERROR_KEYS <= catalog.keys(), locale
        assert {key: _placeholders(catalog[key]) for key in ERROR_KEYS} == {
            key: _placeholders(reference[key]) for key in ERROR_KEYS
        }, locale


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en_US", "try enabling the system proxy"),
        ("zh_CN", "开启系统代理"),
        ("ja_JP", "システムプロキシを有効"),
    ],
)
def test_api_connection_network_error_uses_current_locale(locale, expected):
    manager = _manager(locale)
    view = SimpleNamespace(_t=manager.translate)

    message = env_management._format_test_connection_error(
        view,
        "openai",
        "connection timed out",
    )

    assert expected in message
    assert "https://api.openai.com/v1" in message


def test_api_actions_localize_unencodable_key_without_network_call():
    manager = _manager("zh_CN")
    view = SimpleNamespace(
        _normalize_api_test_target=app_logic.MainAppLogic._normalize_api_test_target,
        _is_openai_compatible_target=app_logic.MainAppLogic._is_openai_compatible_target,
        _t=manager.translate,
    )
    api_key = "abcdefghijklm中文测试"

    connection_result = asyncio.run(
        app_logic.MainAppLogic.test_api_connection_async(
            view,
            "openai",
            api_key,
            "https://api.example/v1",
            "model",
        )
    )
    models_result = asyncio.run(
        app_logic.MainAppLogic.get_available_models_async(
            view,
            "openai",
            api_key,
            "https://api.example/v1",
        )
    )

    assert connection_result == (
        False,
        "API 密钥包含请求头不支持的字符（位置 14-17）。"
        "请重新粘贴密钥，并删除中文、全角符号或不可见字符。",
    )
    assert models_result == (False, [], connection_result[1])
    assert "latin-1" not in connection_result[1]


def test_translation_network_error_uses_current_locale(monkeypatch):
    manager = _manager("en_US")
    monkeypatch.setattr(app_logic, "get_i18n_manager", lambda: manager)

    message = app_logic.TranslationWorker._build_friendly_error_message(
        "connection timed out",
        "",
    )

    assert "Network connection or Host resolution failed" in message
    assert "Try enabling the system proxy" in message
    assert "网络连接" not in message


def test_ocr_candidate_error_uses_ocr_specific_message(monkeypatch):
    manager = _manager("zh_CN")
    monkeypatch.setattr(app_logic, "get_i18n_manager", lambda: manager)

    message = app_logic.TranslationWorker._build_friendly_error_message(
        "OpenAI OCR has no available API candidates for OCR request.",
        "",
    )

    assert "错误原因：当前 OCR 模型或 API 候选不可用" in message
    assert "不要选择「OpenAI OCR」或「Gemini OCR」" in message
    assert "设置 → OCR → OCR模型" in message
    assert "API 管理 → 文字识别" in message


@pytest.mark.parametrize(
    ("error_message", "expected"),
    [
        (
            "OpenAI Renderer render request failed after exhausting 1 API candidate(s): "
            "OpenAI Renderer could not find a compatible image output interface",
            "错误原因：当前模型不支持渲染",
        ),
        (
            "OpenAI Colorizer colorization request failed after exhausting 1 API candidate(s): "
            "the API supports only supported API model names, but you passed gpt-image",
            "错误原因：当前模型不支持上色",
        ),
        (
            "OpenAI Renderer could not find a compatible image output interface",
            "错误原因：当前模型不支持渲染",
        ),
        (
            "OpenAI OCR: OCR request failed after exhausting 1 API candidate(s): "
            "API request failed with status 403: code=limited",
            "错误原因：当前 OCR 模型或 API 候选不可用",
        ),
        (
            "OpenAI OCR model does not exist: gpt-4o-mini",
            "错误原因：当前 OCR 模型或 API 候选不可用",
        ),
    ]
)
def test_feature_error_classification_uses_feature_specific_messages(monkeypatch, error_message, expected):
    manager = _manager("zh_CN")
    monkeypatch.setattr(app_logic, "get_i18n_manager", lambda: manager)

    message = app_logic.TranslationWorker._build_friendly_error_message(error_message, "")

    assert expected in message
