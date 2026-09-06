import _bootstrap  # noqa: F401, I001

import numpy as np
from PyQt6.QtGui import QUndoStack

from editor.commands import PasteOverlaysReplaceCommand
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.paste_overlay_state import serialize_paste_overlays


def _model_with_page() -> EditorModel:
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.full((5, 7, 3), 3, dtype=np.uint8),
        )
    )
    return model


def _overlay(name: str, overlay_id: str):
    return {
        "id": overlay_id,
        "name": name,
        "z": 0,
        "visible": True,
        "opacity": 1.0,
        "center_x": 0.0,
        "center_y": 0.0,
        "width": 64.0,
        "height": 32.0,
        "rotation": 0.0,
        "flip_h": False,
        "flip_v": False,
        "image": "",
    }


def test_replace_command_undo_redo():
    model = _model_with_page()
    stack = QUndoStack()
    target = serialize_paste_overlays([_overlay("a", "ovl-a")])

    stack.push(PasteOverlaysReplaceCommand(model, before=[], after=target))
    assert model.get_paste_overlays() == target

    stack.undo()
    assert model.get_paste_overlays() == []

    stack.redo()
    assert model.get_paste_overlays() == target

    stack.undo()
    assert model.get_paste_overlays() == []


def test_undo_redo_keeps_overlay_ids_stable():
    model = _model_with_page()
    stack = QUndoStack()
    target = serialize_paste_overlays(
        [_overlay("a", ""), _overlay("b", "")]
    )
    ids = [item["id"] for item in target]
    assert len(ids) == len(set(ids))

    stack.push(PasteOverlaysReplaceCommand(model, before=[], after=target))
    stack.undo()
    stack.redo()
    assert [item["id"] for item in model.get_paste_overlays()] == ids


def test_replace_command_is_isolated_from_caller_mutation():
    model = _model_with_page()
    stack = QUndoStack()
    target = serialize_paste_overlays([_overlay("a", "ovl-a")])
    stack.push(PasteOverlaysReplaceCommand(model, before=[], after=target))
    stack.undo()

    # 命令内部快照不受外部修改影响
    target[0]["name"] = "mutated"
    stack.redo()
    assert model.get_paste_overlays()[0]["name"] == "a"


def test_select_paste_overlay_clears_region_selection():
    from unittest.mock import MagicMock

    from ui.editor.graphics_view_paste_overlays import GraphicsViewPasteOverlayMixin

    class DummyView(GraphicsViewPasteOverlayMixin):
        def __init__(self, model):
            self.model = model
            self.scene = MagicMock()
            self._paste_overlay_items = []
            self._selected_paste_overlay_id = None

    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.full((5, 7, 3), 3, dtype=np.uint8),
            regions=[{"translation": "test"}],
        )
    )
    model.set_selection([0])
    assert model.get_selection() == [0]

    view = DummyView(model)
    view.select_paste_overlay("ovl-1")
    assert model.get_selection() == []
    assert view._selected_paste_overlay_id == "ovl-1"


def test_clipboard_tracks_last_copied_kind(monkeypatch):
    from editor.editor_controller import EditorController
    from services.history_service import EditorStateManager

    history = EditorStateManager()
    monkeypatch.setattr(
        "editor.editor_controller.get_history_service", lambda: history
    )

    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.full((5, 7, 3), 3, dtype=np.uint8),
            regions=[{"translation": "text"}],
            paste_overlays=[_overlay("a", "ovl-a")],
        )
    )
    controller = EditorController(model)

    assert controller.last_clipboard_kind() is None
    assert controller.copy_paste_overlay("ovl-a") is True
    assert controller.last_clipboard_kind() == "paste_overlay"
    assert controller.paste_overlay_clipboard_available() is True

    # 复制文本区域后，最近类型切换为 region
    controller.copy_region(0)
    assert controller.last_clipboard_kind() == "region"
    assert controller.history_service.has_clipboard_data() is True


def test_rebuild_resets_dangling_selected_overlay_id():
    from unittest.mock import MagicMock

    from PyQt6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from ui.editor.graphics_view_paste_overlays import GraphicsViewPasteOverlayMixin

    class DummyView(GraphicsViewPasteOverlayMixin):
        def __init__(self, model):
            self.model = model
            self.scene = MagicMock()
            self._image_item = MagicMock()
            self._paste_overlay_items = []
            self._selected_paste_overlay_id = "old-page-id"

    model = _model_with_page()
    model.set_paste_overlays([_overlay("a", "new-page-id")])
    view = DummyView(model)

    view._rebuild_paste_overlay_items()
    assert view._selected_paste_overlay_id is None

