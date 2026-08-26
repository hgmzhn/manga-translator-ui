import _bootstrap  # noqa: F401, I001

from types import SimpleNamespace

import desktop_qt_ui.app_logic as app_logic_module
from PyQt6.QtWidgets import QApplication

from desktop_qt_ui.app_logic import MainAppLogic

_QT_APP = QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


class _StateManager:
    def __init__(self):
        self.translating = True
        self.status_message = None

    def set_translating(self, value):
        self.translating = bool(value)

    def set_status_message(self, message):
        self.status_message = message


def test_all_existing_outputs_use_dedicated_overwrite_disabled_message(monkeypatch):
    translations = []
    warning_signal = _Signal()
    completed_signal = _Signal()
    state_manager = _StateManager()

    def translate(key, **kwargs):
        translations.append((key, kwargs))
        return f"overwrite-disabled:{kwargs['count']}"

    logic = SimpleNamespace(
        current_task_id=7,
        current_worker=object(),
        saved_files_count=0,
        completed_output_sources={},
        _task_failures=[],
        _path_key=MainAppLogic._path_key,
        _record_task_failure_from_result=lambda _result: None,
        _record_task_failure=lambda *_args: None,
        _ui_log=lambda *_args: None,
        _t=translate,
        state_manager=state_manager,
        warning_dialog_requested=warning_signal,
        error_dialog_requested=_Signal(),
        task_completed=completed_signal,
        _cleanup_after_task=lambda: None,
    )
    monkeypatch.setattr(app_logic_module.QTimer, "singleShot", lambda *_args: None)
    results = [
        {
            "skipped": True,
            "original_path": rf"C:\batch\{name}.png",
            "skip_message": f"output exists: {name}.png",
        }
        for name in ("01", "02")
    ]

    MainAppLogic.on_task_finished(logic, results, 7)

    assert translations == [
        ("⏭️ Skipped {count} existing files.", {"count": 2})
    ]
    assert warning_signal.values == [("overwrite-disabled:2",)]
    assert state_manager.status_message == "overwrite-disabled:2"
    assert not state_manager.translating
    assert completed_signal.values == [([],)]
    assert "01.png" not in warning_signal.values[0][0]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
