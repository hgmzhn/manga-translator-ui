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
