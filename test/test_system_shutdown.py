import _bootstrap  # noqa: I001

from types import SimpleNamespace

import pytest

import desktop_qt_ui.app_logic as app_logic_module
from desktop_qt_ui.app_logic import MainAppLogic
from desktop_qt_ui.ui.main_window import MainWindow
from desktop_qt_ui.utils.system_shutdown import (
    DEFAULT_SHUTDOWN_DELAY_SECONDS,
    build_system_shutdown_cancel_command,
    build_system_shutdown_command,
    cancel_scheduled_system_shutdown,
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


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [
        ("win32", ["shutdown", "/a"]),
        ("linux", ["shutdown", "-c"]),
        ("darwin", ["shutdown", "-c"]),
    ],
)
def test_build_system_shutdown_cancel_command_is_platform_specific(platform_name, expected):
    assert build_system_shutdown_cancel_command(platform_name=platform_name) == expected


def test_cancel_scheduled_system_shutdown_uses_injected_runner():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))

    assert cancel_scheduled_system_shutdown(platform_name="win32", runner=runner)
    assert calls[0][0] == ["shutdown", "/a"]
    assert calls[0][1]["check"] is True


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


def test_auto_shutdown_can_be_cancelled(monkeypatch):
    calls = []
    logs = []
    logic = SimpleNamespace(
        _auto_shutdown_pending=True,
        _auto_shutdown_triggered=True,
        _ui_log=lambda *args: logs.append(args),
        _t=lambda key, **_kwargs: key,
    )
    monkeypatch.setattr(
        app_logic_module,
        "cancel_scheduled_system_shutdown",
        lambda: calls.append(True) or True,
    )

    assert MainAppLogic._cancel_auto_shutdown(logic)

    assert calls == [True]
    assert logic._auto_shutdown_pending is False
    assert logic._auto_shutdown_triggered is False
    assert logs == [("log_auto_shutdown_cancelled",)]


def test_disabling_shutdown_keeps_config_when_cancellation_fails():
    calls = []
    config = SimpleNamespace(
        app=SimpleNamespace(shutdown_after_translation=True)
    )
    logic = SimpleNamespace(
        logger=SimpleNamespace(
            debug=lambda *_args: None,
            error=lambda *_args: None,
        ),
        config_service=SimpleNamespace(
            get_config=lambda: config,
            set_config=lambda *_args: calls.append("set"),
            save_config_file=lambda: calls.append("save"),
        ),
        _cancel_auto_shutdown=lambda: False,
    )

    assert not MainAppLogic.update_single_config(
        logic,
        "app.shutdown_after_translation",
        False,
    )
    assert config.app.shutdown_after_translation is True
    assert calls == []


def test_update_config_does_not_submit_disable_when_cancellation_fails():
    calls = []
    logic = SimpleNamespace(
        config_service=SimpleNamespace(
            update_config=lambda _updates: calls.append("update"),
        ),
        _cancel_auto_shutdown=lambda: False,
        logger=SimpleNamespace(
            info=lambda *_args: None,
            error=lambda *_args: None,
        ),
    )

    assert not MainAppLogic.update_config(
        logic,
        {"app": {"shutdown_after_translation": False}},
    )
    assert calls == []


def test_main_window_restores_setting_control_when_update_is_rejected():
    restored_configs = []
    config_dump = {"app": {"shutdown_after_translation": True}}
    logic = SimpleNamespace(
        update_single_config=lambda *_args: False,
    )
    window = SimpleNamespace(
        app_logic=logic,
        config_service=SimpleNamespace(
            get_config=lambda: SimpleNamespace(model_dump=lambda: config_dump),
        ),
        main_view=SimpleNamespace(
            set_parameters=lambda config: restored_configs.append(config),
        ),
        logger=SimpleNamespace(warning=lambda *_args: None),
    )

    MainWindow._on_main_view_setting_changed(
        window,
        "app.shutdown_after_translation",
        False,
    )

    assert restored_configs == [config_dump]
