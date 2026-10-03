"""Completion actions are tested with timers and OS execution intercepted."""
import _bootstrap  # noqa: I001

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import desktop_qt_ui.app_logic as module
from desktop_qt_ui.app_logic import MainAppLogic
from desktop_qt_ui.core.config_models import AppSection
from desktop_qt_ui.utils.system_shutdown import build_completion_command, execute_completion_action


def make_logic(action="shutdown"):
    """Provide an idle successful batch without initializing external services."""
    logic = SimpleNamespace(
        _auto_shutdown_pending=True, _auto_shutdown_triggered=False,
        _shutdown_started=False, _stop_requested=False,
        _translate_future=None, _cleanup_future=None, archive_to_temp_map={},
        _task_executor=SimpleNamespace(submit=Mock()),
        _cleanup_archive_paths=Mock(), _ui_log=Mock(),
        _t=lambda key, **kwargs: key,
        state_manager=SimpleNamespace(is_translating=lambda: False),
        config_service=SimpleNamespace(get_config=lambda: SimpleNamespace(
            app=SimpleNamespace(after_translation_action=action))),
        close_application_requested=Mock(),
    )
    logic._cleanup_after_task = lambda: MainAppLogic._cleanup_after_task(logic)
    logic._execute_completion_action = lambda a, g: MainAppLogic._execute_completion_action(logic, a, g)
    logic._schedule_auto_shutdown_after_task = lambda: MainAppLogic._schedule_auto_shutdown_after_task(logic)
    return logic


@pytest.mark.parametrize("action", ["none", "close", "sleep", "hibernate", "shutdown"])
def test_completion_config_and_dropdown(action):
    """Each dropdown value is accepted, with no action as the default."""
    assert AppSection().after_translation_action == "none"
    assert AppSection(after_translation_action=action).after_translation_action == action
    logic = SimpleNamespace(_t=lambda key: key, translation_service=SimpleNamespace(
        get_target_languages=lambda: {}, get_keep_languages=lambda: {}))
    assert action in MainAppLogic.get_display_mapping(logic, "after_translation_action")


@pytest.mark.parametrize("action", ["close", "sleep", "hibernate", "shutdown"])
def test_countdown_cancellation_makes_stale_callback_harmless(monkeypatch, action):
    """Cancelling then starting a new countdown cannot execute the old action."""
    callbacks = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda ms, cb: callbacks.append((ms, cb)))
    logic = make_logic(action)
    logic._schedule_auto_shutdown_after_task()
    assert callbacks[0][0] == 60000
    assert not logic._task_executor.submit.called
    MainAppLogic._cancel_auto_shutdown(logic)
    logic._auto_shutdown_pending = True
    logic._schedule_auto_shutdown_after_task()
    callbacks[0][1]()
    assert not logic._task_executor.submit.called
    assert not logic.close_application_requested.emit.called
    callbacks[1][1]()
    if action == "close":
        logic.close_application_requested.emit.assert_called_once()
    else:
        logic._task_executor.submit.assert_called_once()


def test_none_does_not_start_countdown(monkeypatch):
    """The default never schedules an action."""
    timer = Mock()
    monkeypatch.setattr(module.QTimer, "singleShot", timer)
    make_logic("none")._schedule_auto_shutdown_after_task()
    timer.assert_not_called()


def test_cleanup_submission_failure_prevents_action(monkeypatch):
    """Retain pending cleanup when the executor rejects it and do not power off."""
    timer = Mock()
    monkeypatch.setattr(module.QTimer, "singleShot", timer)
    logic = make_logic()
    logic.archive_to_temp_map = {"book.zip": "temp-book"}
    logic._task_executor.submit.side_effect = RuntimeError("closed")
    logic._schedule_auto_shutdown_after_task()
    assert logic.archive_to_temp_map == {"book.zip": "temp-book"}
    assert not logic._auto_shutdown_pending
    timer.assert_not_called()


def test_cleanup_must_complete_before_countdown(monkeypatch):
    """An active cleanup future delays the start of the 60-second countdown."""
    callbacks = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda ms, cb: callbacks.append((ms, cb)))
    logic = make_logic()
    logic._cleanup_future = Future()
    logic._schedule_auto_shutdown_after_task()
    assert callbacks[0][0] == 100
    logic._cleanup_future.set_result(None)
    callbacks[0][1]()
    assert callbacks[1][0] == 60000


@pytest.mark.parametrize("action,native", [("sleep", "suspend"), ("hibernate", "hibernate"), ("shutdown", "poweroff")])
def test_linux_power_commands(action, native):
    """Linux maps to systemd's supported power actions."""
    runner = Mock()
    assert execute_completion_action(action, platform_name="linux", runner=runner)
    assert runner.call_args.args[0] == ["systemctl", native]


@pytest.mark.parametrize("action,state", [("sleep", "Suspend"), ("hibernate", "Hibernate")])
def test_windows_distinguishes_sleep_from_hibernation(action, state):
    """Windows uses the typed API, not rundll32's incompatible calling convention."""
    command = build_completion_command(action, "win32")
    assert command[0] == "powershell.exe"
    assert f"PowerState]::{state}, $false, $false" in command[-1]


def test_unsupported_and_denied_actions_report_failure():
    """Unsupported hibernation and permission errors leave the app running."""
    runner = Mock()
    assert not execute_completion_action("hibernate", platform_name="darwin", runner=runner)
    runner.assert_not_called()
    assert not execute_completion_action("shutdown", platform_name="linux",
                                         runner=Mock(side_effect=PermissionError()))


def test_settings_renders_dropdown_and_emits_stored_action():
    """A real Qt combo renders all actions and emits the selected stored value."""
    from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from ui.main_page import dynamic_settings

    app = QApplication.instance() or QApplication([])
    mapping = {action: label for action, label in zip(
        ["none", "close", "sleep", "hibernate", "shutdown"],
        ["不执行操作", "关闭软件", "睡眠", "休眠", "关机"])}
    view = SimpleNamespace(
        _settings_value_bindings={}, _t=lambda key: key,
        setting_changed=Mock(),
        controller=SimpleNamespace(
            get_options_for_key=lambda key: None,
            get_display_mapping=lambda key: mapping if key == "after_translation_action" else {},
        ),
    )
    view._on_setting_changed = lambda *args: dynamic_settings._on_setting_changed(view, *args)
    parent = QWidget()
    layout = QVBoxLayout(parent)
    assert dynamic_settings._create_param_widgets(
        view, {"after_translation_action": "none"}, layout, "app") == 1
    combo = view._settings_value_bindings["app.after_translation_action"][0]
    assert combo.count() == 5
    assert combo.currentText() == "不执行操作"
    combo.setCurrentText("休眠")
    view.setting_changed.emit.assert_called_once_with("app.after_translation_action", "hibernate")
    parent.deleteLater()
    app.processEvents()
