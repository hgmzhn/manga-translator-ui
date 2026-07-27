"""回归：切图"未保存"检测以 QUndoStack clean 状态为唯一真相源；
异步翻译写回应用替换规则且可撤销。"""
from __future__ import annotations

import os
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))


def _ensure_app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_dirty_detection_follows_undo_stack_clean_state() -> None:
    _ensure_app()
    from PyQt6.QtGui import QUndoCommand

    from desktop_qt_ui.editor.controller_export_service import EditorControllerExportService
    from desktop_qt_ui.services.history_service import EditorStateManager

    history = EditorStateManager()
    controller = SimpleNamespace(history_service=history)
    export_service = object.__new__(EditorControllerExportService)
    export_service.controller = controller

    # 新加载的文档：无改动
    assert not export_service.has_changes_since_last_export()

    # 任意命令入栈 → 有改动
    history.execute(QUndoCommand("edit"))
    assert export_service.has_changes_since_last_export()

    # 导出成功（mark_clean）→ 无改动
    history.mark_clean()
    assert not export_service.has_changes_since_last_export()

    # 撤销离开 clean 点 → 有改动；重做回到 clean 点 → 无改动
    history.undo()
    assert export_service.has_changes_since_last_export()
    history.redo()
    assert not export_service.has_changes_since_last_export()

    # 导出失败回退乐观标记（mark_dirty）→ 有改动
    history.mark_dirty()
    assert export_service.has_changes_since_last_export()


def test_async_translation_write_back_applies_replacements_and_is_undoable() -> None:
    from desktop_qt_ui.editor.editor_controller import EditorController, _AsyncRegionUpdateRequest
    from manga_translator.rendering.text_replacements import apply_replacements

    class FakeModel:
        def __init__(self):
            self.ids = [7]
            self.regions = [{"text": "src", "translation": "", "translation_raw": "", "direction": "h"}]

        def find_region_index(self, region_id):
            try:
                return self.ids.index(region_id)
            except ValueError:
                return None

        def get_region_by_index(self, index):
            if index is None or not 0 <= index < len(self.regions):
                return None
            return dict(self.regions[index])

        def get_regions(self):
            return [dict(region) for region in self.regions]

        def update_regions(self, updates, *, fields=None, source=""):
            for index, region in updates.items():
                self.regions[index] = region

    model = FakeModel()
    executed_commands = []

    def execute_command(command):
        executed_commands.append(command)
        command.redo()

    controller = SimpleNamespace(
        model=model,
        logger=SimpleNamespace(
            error=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        ),
        execute_command=execute_command,
        _finish_async_region_update=lambda *args, **kwargs: None,
    )
    controller._apply_translation_replacements = (
        lambda *args, **kwargs: EditorController._apply_translation_replacements(controller, *args, **kwargs)
    )
    controller._replace_plain_translation = (
        lambda *args, **kwargs: EditorController._replace_plain_translation(controller, *args, **kwargs)
    )

    raw_value = "wait..."
    request = _AsyncRegionUpdateRequest("translation", [(7, raw_value)], "translation")
    EditorController.on_regions_update_finished(controller, request)

    # translation 与手动编辑同径：过替换规则；translation_raw 保留译文原样
    assert model.regions[0]["translation"] == apply_replacements(raw_value, 0)
    assert model.regions[0]["translation_raw"] == raw_value

    # 写回可撤销
    assert len(executed_commands) == 1
    executed_commands[0].undo()
    assert model.regions[0]["translation"] == ""
    assert model.regions[0]["translation_raw"] == ""


def test_commit_pending_edits_flushes_floating_editor() -> None:
    from desktop_qt_ui.editor.editor_controller import EditorController

    flushed = []
    controller = SimpleNamespace(
        view=SimpleNamespace(
            rich_text_editor=SimpleNamespace(flush_pending_changes=lambda: flushed.append(True))
        ),
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )
    EditorController.commit_pending_edits(controller)
    assert flushed == [True]

    # 无视图 / 无浮动编辑器时安全跳过
    EditorController.commit_pending_edits(
        SimpleNamespace(view=None, logger=SimpleNamespace(warning=lambda *args, **kwargs: None))
    )
    EditorController.commit_pending_edits(
        SimpleNamespace(
            view=SimpleNamespace(rich_text_editor=None),
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        )
    )


def test_export_commits_pending_edits_before_reading_model() -> None:
    from desktop_qt_ui.editor.editor_controller import EditorController

    calls = []
    future = Future()
    controller = SimpleNamespace(
        commit_pending_edits=lambda: calls.append("commit"),
        export_service=SimpleNamespace(
            export_image=lambda automatic=False: calls.append(
                f"export:{'auto' if automatic else 'manual'}"
            )
            or future
        ),
    )
    EditorController.export_image(controller)
    assert calls == ["commit", "export:manual"]


def test_image_switch_commits_pending_edits_before_dirty_check() -> None:
    _ensure_app()
    from desktop_qt_ui.editor.controller_document_service import EditorControllerDocumentService

    calls = []

    def has_changes():
        calls.append("check")
        return False

    controller = SimpleNamespace(
        commit_pending_edits=lambda: calls.append("commit"),
        export_service=SimpleNamespace(has_changes_since_last_export=has_changes),
    )
    service = EditorControllerDocumentService(controller)
    service.do_load_image = lambda path: calls.append("load")
    service.load_image_and_regions("dummy.png")

    # flush 必须先于脏检测，否则 debounce 期草稿漏检
    assert calls == ["commit", "check", "load"]


def _make_switch_service(calls, *, dirty: bool, auto_export: bool):
    from desktop_qt_ui.editor.controller_document_service import EditorControllerDocumentService

    future = Future()
    controller = SimpleNamespace(
        commit_pending_edits=lambda: calls.append("commit"),
        export_service=SimpleNamespace(has_changes_since_last_export=lambda: dirty),
        export_image=lambda automatic=False: calls.append(
            f"export:{'auto' if automatic else 'manual'}"
        )
        or future,
        config_service=SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                app=SimpleNamespace(editor_auto_export_on_switch=auto_export)
            )
        ),
        logger=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        ),
    )
    service = EditorControllerDocumentService(controller)
    service.do_load_image = lambda path: calls.append("load")
    return service


def test_image_switch_auto_exports_when_enabled() -> None:
    _ensure_app()
    calls = []
    service = _make_switch_service(calls, dirty=True, auto_export=True)
    service.load_image_and_regions("dummy.png")
    # 有改动 + 开关开：自动导出（快照同步完成）后立即切图，不弹窗不阻塞
    assert calls == ["commit", "export:auto", "load"]


def test_image_switch_asks_when_auto_export_disabled() -> None:
    _ensure_app()
    calls = []
    service = _make_switch_service(calls, dirty=True, auto_export=False)
    service._ask_unsaved_action = lambda: calls.append("ask") or "cancel"
    service.load_image_and_regions("dummy.png")
    # 开关关：回到弹窗流程，取消则不切图
    assert calls == ["commit", "ask"]

    calls.clear()
    service._ask_unsaved_action = lambda: calls.append("ask") or "discard"
    service.load_image_and_regions("dummy.png")
    assert calls == ["commit", "ask", "load"]


def main() -> int:
    test_dirty_detection_follows_undo_stack_clean_state()
    test_async_translation_write_back_applies_replacements_and_is_undoable()
    test_commit_pending_edits_flushes_floating_editor()
    test_export_commits_pending_edits_before_reading_model()
    test_image_switch_commits_pending_edits_before_dirty_check()
    test_image_switch_auto_exports_when_enabled()
    test_image_switch_asks_when_auto_export_disabled()
    print("all dirty-detection regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
