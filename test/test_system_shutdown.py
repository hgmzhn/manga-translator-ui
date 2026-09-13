import _bootstrap  # noqa: I001

from types import SimpleNamespace

import pytest

import desktop_qt_ui.app_logic as app_logic_module
from desktop_qt_ui.app_logic import MainAppLogic
from desktop_qt_ui.utils.system_shutdown import (
    DEFAULT_SHUTDOWN_DELAY_SECONDS,
    build_system_shutdown_command,
    schedule_system_shutdown,
)


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [
        (
            "win32",
            [
                "shutdown",
                "/s",
                "/t",
                "60",
                "/d",
                "p:0:0",
                "/c",
                "Manga Translator translation completed",
            ],
        ),
        ("linux", ["shutdown", "-h", "+1"]),
        ("darwin", ["shutdown", "-h", "+1"]),
    ],
)
def test_build_system_shutdown_command_is_platform_specific(platform_name, expected):
    assert build_system_shutdown_command(
        DEFAULT_SHUTDOWN_DELAY_SECONDS,
        platform_name=platform_name,
    ) == expected


def test_build_system_shutdown_command_rounds_unix_delay_up_to_minutes():
    assert build_system_shutdown_command(61, platform_name="linux-gnu") == [
        "shutdown",
        "-h",
        "+2",
    ]


def test_schedule_system_shutdown_uses_injected_runner():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))

    assert schedule_system_shutdown(60, platform_name="win32", runner=runner)
    assert calls[0][0][0:4] == ["shutdown", "/s", "/t", "60"]
    assert calls[0][1]["check"] is True


def test_schedule_system_shutdown_returns_false_for_unsupported_platform():
    assert not schedule_system_shutdown(60, platform_name="freebsd")


def test_auto_shutdown_is_scheduled_only_when_enabled(monkeypatch):
    calls = []
    logs = []
    logic = SimpleNamespace(
        _auto_shutdown_pending=True,
        _shutdown_started=False,
        _stop_requested=False,
        _auto_shutdown_triggered=False,
        config_service=SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                app=SimpleNamespace(shutdown_after_translation=True)
            )
        ),
        _translate_future=None,
        _cleanup_future=None,
        archive_to_temp_map={},
        _cleanup_after_task=lambda: None,
        _ui_log=lambda *args: logs.append(args),
        _t=lambda key, **kwargs: key.format(**kwargs),
    )
    monkeypatch.setattr(
        app_logic_module,
        "schedule_system_shutdown",
        lambda seconds: calls.append(seconds) or True,
    )

    MainAppLogic._schedule_auto_shutdown_after_task(logic)

    assert calls == [DEFAULT_SHUTDOWN_DELAY_SECONDS]
    assert logic._auto_shutdown_pending is False
    assert logic._auto_shutdown_triggered is True
    assert logs == [("log_auto_shutdown_scheduled",)]


def test_auto_shutdown_is_not_scheduled_when_disabled(monkeypatch):
    calls = []
    logic = SimpleNamespace(
        _auto_shutdown_pending=True,
        _shutdown_started=False,
        _stop_requested=False,
        _auto_shutdown_triggered=False,
        config_service=SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                app=SimpleNamespace(shutdown_after_translation=False)
            )
        ),
        _ui_log=lambda *_args: None,
    )
    monkeypatch.setattr(
        app_logic_module,
        "schedule_system_shutdown",
        lambda seconds: calls.append(seconds) or True,
    )

    MainAppLogic._schedule_auto_shutdown_after_task(logic)

    assert calls == []
    assert logic._auto_shutdown_pending is False
