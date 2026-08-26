import _bootstrap  # noqa: I001

from types import SimpleNamespace

import pytest

import desktop_qt_ui.app_logic as app_logic_module
from PyQt6.QtWidgets import QApplication

from desktop_qt_ui.app_logic import MainAppLogic
from services.i18n_service import I18nManager

_QT_APP = QApplication.instance() or QApplication([])
LOCALE_DIR = _bootstrap.ROOT / "desktop_qt_ui" / "locales"


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


@pytest.mark.parametrize(
    ("locale", "expected_fragment"),
    [
        ("zh_CN", "所有 2 个文件"),
        ("zh_TW", "所有 2 個檔案"),
        ("en_US", "All 2 files"),
        ("ja_JP", "2 個のファイル"),
        ("ko_KR", "2개 파일"),
        ("es_ES", "los 2 archivos"),
    ],
)
def test_all_existing_outputs_use_dedicated_overwrite_disabled_message(
    monkeypatch,
    locale,
    expected_fragment,
):
    warning_signal = _Signal()
    completed_signal = _Signal()
    state_manager = _StateManager()
    i18n = I18nManager(
        locale_dir=str(LOCALE_DIR),
        fallback_locale="zh_CN",
        config_language=locale,
    )

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
        _t=i18n.translate,
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

    message = warning_signal.values[0][0]
    assert expected_fragment in message
    assert "{count}" not in message
    assert "all_existing_outputs_skipped_dialog" not in message
    assert "2" in state_manager.status_message
    assert "all_existing_outputs_skipped_status" not in state_manager.status_message
    assert "01.png" not in message
    assert not state_manager.translating
    assert completed_signal.values == [([],)]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

