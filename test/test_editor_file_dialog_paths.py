import _bootstrap  # noqa: F401

from types import SimpleNamespace

import desktop_qt_ui.editor.editor_logic as editor_logic_module
from desktop_qt_ui.editor.editor_logic import EditorLogic


class _ConfigService:
    def __init__(self, last_open_dir="."):
        self.config = SimpleNamespace(app=SimpleNamespace(last_open_dir=last_open_dir))
        self.updates = []
        self.save_count = 0

    def get_config(self):
        return self.config

    def update_config(self, updates):
        self.updates.append(updates)
        self.config.app.last_open_dir = updates["app"]["last_open_dir"]

    def save_config_file(self):
        self.save_count += 1


def _logic(config_service):
    logic = EditorLogic.__new__(EditorLogic)
    logic.config_service = config_service
    logic.add_files = lambda paths: setattr(logic, "added_files", paths)
    logic.add_folders = lambda paths: setattr(logic, "added_folders", paths)
    return logic


def test_open_and_add_files_persists_selected_file_directory(monkeypatch):
    config_service = _ConfigService(r"C:\previous")
    logic = _logic(config_service)
    selected = [r"D:\xiazai\图片助手(ImageAssistant)_批量图片下载器\104\page.png"]
    calls = []

    def choose_files(parent, title, start_dir, file_filter):
        calls.append((parent, title, start_dir, file_filter))
        return selected, ""

    monkeypatch.setattr(editor_logic_module.QFileDialog, "getOpenFileNames", choose_files)

    EditorLogic.open_and_add_files(logic)

    assert calls[0][2] == r"C:\previous"
    assert logic.added_files == selected
    assert config_service.config.app.last_open_dir == (
        r"D:\xiazai\图片助手(ImageAssistant)_批量图片下载器\104"
    )
    assert config_service.save_count == 1


def test_open_and_add_folder_persists_first_selected_directory(monkeypatch):
    config_service = _ConfigService(r"C:\previous")
    logic = _logic(config_service)
    selected = [
        r"D:\xiazai\图片助手(ImageAssistant)_批量图片下载器\104",
        r"D:\other",
    ]
    calls = []

    def choose_folders(**kwargs):
        calls.append(kwargs)
        return selected

    monkeypatch.setattr(editor_logic_module, "select_folders", choose_folders)

    EditorLogic.open_and_add_folder(logic)

    assert calls[0]["start_dir"] == r"C:\previous"
    assert logic.added_folders == selected
    assert config_service.config.app.last_open_dir == selected[0]
    assert config_service.save_count == 1
