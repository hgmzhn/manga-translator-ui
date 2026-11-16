import _bootstrap  # noqa: I001
import asyncio
import json
from types import SimpleNamespace

import pytest

import app_logic
from manga_translator.translators import common
from services.i18n_service import I18nManager

LOCALE_DIR = _bootstrap.ROOT / "desktop_qt_ui" / "locales"


class _FakeCompletions:
    def __init__(self, non_stream, stream):
        self._non_stream = non_stream
        self._stream = stream

    async def create(self, model, messages, stream=False, **kwargs):
        behavior = self._stream if stream else self._non_stream
        if isinstance(behavior, Exception):
            raise behavior
        if not stream:
            return SimpleNamespace(choices=behavior)

        async def _chunks():
            for choices in behavior:
                yield SimpleNamespace(choices=choices)

        return _chunks()


def _install_fake_client(monkeypatch, non_stream, stream):
    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=_FakeCompletions(non_stream, stream))

        async def close(self):
            pass

    monkeypatch.setattr(common, "AsyncOpenAICurlCffi", FakeClient)


def _run_test(locale="zh_CN"):
    manager = I18nManager(locale_dir=str(LOCALE_DIR), fallback_locale="zh_CN", config_language=locale)
    view = SimpleNamespace(
        _t=manager.translate,
        _probe_openai_chat=app_logic.MainAppLogic._probe_openai_chat,
    )
    return asyncio.run(
        app_logic.MainAppLogic._test_openai_text_api(view, "sk-test", "https://api.example/v1", "model"),
    ), manager


@pytest.mark.parametrize(
    ("non_stream", "stream", "expected_key"),
    [
        (["choice"], [["choice"]], "api_test_stream_mode_both"),
        ([], [[], ["choice"]], "api_test_stream_mode_stream_only"),
        (Exception("HTTP 500"), [["choice"]], "api_test_stream_mode_stream_only"),
        (["choice"], [[]], "api_test_stream_mode_non_stream_only"),
        (["choice"], Exception("stream unsupported"), "api_test_stream_mode_non_stream_only"),
    ],
)
def test_openai_text_api_reports_usable_request_modes(monkeypatch, non_stream, stream, expected_key):
    _install_fake_client(monkeypatch, non_stream, stream)

    (success, message), manager = _run_test()

    assert success is True
    assert "model" in message
    assert manager.translate(expected_key) in message


def test_openai_text_api_fails_when_both_request_modes_fail(monkeypatch):
    _install_fake_client(monkeypatch, [], Exception("stream unsupported"))

    with pytest.raises(Exception) as exc_info:
        _run_test()

    assert "non-streaming response contained no choices" in str(exc_info.value)
    assert "stream unsupported" in str(exc_info.value)


@pytest.mark.parametrize("locale", ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"])
def test_stream_mode_messages_are_localized(locale):
    catalog = json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))

    for key in (
        "api_test_stream_mode_both",
        "api_test_stream_mode_stream_only",
        "api_test_stream_mode_non_stream_only",
    ):
        assert catalog.get(key, "").strip(), key
