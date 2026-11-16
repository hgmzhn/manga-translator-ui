import _bootstrap  # noqa: I001
import asyncio
import json
from types import SimpleNamespace

import pytest

import app_logic
from manga_translator.translators import common
from services.i18n_service import I18nManager
from ui.main_page import env_management

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


def _manager(locale="zh_CN"):
    return I18nManager(locale_dir=str(LOCALE_DIR), fallback_locale="zh_CN", config_language=locale)


def _run_test():
    view = SimpleNamespace(
        _t=_manager().translate,
        _probe_openai_chat=app_logic.MainAppLogic._probe_openai_chat,
    )
    return asyncio.run(
        app_logic.MainAppLogic._test_openai_text_api(view, "sk-test", "https://api.example/v1", "model"),
    )


@pytest.mark.parametrize(
    ("non_stream", "stream", "expected_line"),
    [
        (["choice"], [["choice"]], "流式：可用  非流式：可用"),
        ([], [[], ["choice"]], "流式：可用  非流式：不可用"),
        (Exception("HTTP 500"), [["choice"]], "流式：可用  非流式：不可用"),
        (["choice"], [[]], "流式：不可用  非流式：可用"),
        (["choice"], Exception("stream unsupported"), "流式：不可用  非流式：可用"),
    ],
)
def test_openai_text_api_reports_each_request_mode(monkeypatch, non_stream, stream, expected_line):
    _install_fake_client(monkeypatch, non_stream, stream)

    success, message = _run_test()

    assert success is True
    assert message.splitlines() == ["连接成功，模型 model 可用", expected_line]


def test_openai_text_api_fails_with_mode_status_when_both_modes_fail(monkeypatch):
    _install_fake_client(monkeypatch, [], Exception("stream unsupported"))

    with pytest.raises(Exception) as exc_info:
        _run_test()

    message = str(exc_info.value)
    assert message.splitlines()[0] == "流式：不可用  非流式：不可用"
    assert "stream unsupported" in message
    assert "non-streaming response contained no choices" in message


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_mode_status_does_not_affect_connection_error_classification(locale):
    manager = _manager(locale)
    view = SimpleNamespace(_t=manager.translate)
    mode_line = manager.translate(
        "api_test_stream_modes",
        stream=manager.translate("API test unavailable"),
        non_stream=manager.translate("API test unavailable"),
    )
    raw_error = (
        f"{mode_line}\n"
        "Streaming request failed: Error code: 401 - invalid api key\n"
        "Non-streaming request failed: Error code: 401 - invalid api key"
    )

    message = env_management._format_test_connection_error(
        view,
        "openai",
        manager.translate("api_test_error_connection_failed", error=raw_error),
    )

    assert message.startswith(manager.translate("api_test_error_config"))
    assert manager.translate("api_test_error_service") not in message
    assert " ".join(mode_line.split()) in " ".join(message.split())


def test_batch_result_text_keeps_mode_status_of_available_channels():
    view = SimpleNamespace(_t=_manager().translate)
    results = [
        {"label": "A", "success": True, "message": "连接成功，模型 a 可用\n流式：可用  非流式：不可用"},
        {"label": "B", "success": False, "message": "连接失败"},
    ]

    text = env_management._format_api_batch_result_text(view, results)

    assert text.splitlines() == [
        "[不可用] B",
        "连接失败",
        "",
        "[可用] A",
        "连接成功，模型 a 可用",
        "流式：可用  非流式：不可用",
    ]


@pytest.mark.parametrize("locale", ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"])
def test_stream_mode_status_is_localized(locale):
    catalog = json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))

    rendered = catalog["api_test_stream_modes"].format(
        stream=catalog["API test available"],
        non_stream=catalog["API test unavailable"],
    )

    assert catalog["API test available"] in rendered
    assert catalog["API test unavailable"] in rendered
