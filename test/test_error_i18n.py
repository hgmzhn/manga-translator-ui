import _bootstrap  # noqa: I001
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
        assert {
            key: _placeholders(catalog[key])
            for key in ERROR_KEYS
        } == {
            key: _placeholders(reference[key])
            for key in ERROR_KEYS
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
