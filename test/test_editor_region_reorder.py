import _bootstrap  # noqa: F401, I001

import numpy as np

from editor.commands import MoveRegionCommand
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel


def _isolated_model() -> EditorModel:
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.zeros((8, 8, 3), dtype=np.uint8),
        )
    )
    return model


def test_region_reorder_preserves_ids_selection_and_undo() -> None:
    model = _isolated_model()
    model.session.set_regions(
        [
            {"text": "A", "translation": "TA"},
            {"text": "B", "translation": "TB"},
            {"text": "C", "translation": "TC"},
        ]
    )
    original_ids = [model.get_region_id(index) for index in range(3)]
    model.set_selection([0, 2])

    changes = []
    model.regions_changed.connect(changes.append)
    command = MoveRegionCommand(model, 0, 2)

    command.redo()
    assert [region["text"] for region in model.get_regions()] == ["B", "C", "A"]
    assert [model.get_region_id(index) for index in range(3)] == [
        original_ids[1],
        original_ids[2],
        original_ids[0],
    ]
    assert model.get_selection() == [1, 2]
    assert changes[-1].kind == "reset"
    assert changes[-1].source == "reorder"

    command.undo()
    assert [region["text"] for region in model.get_regions()] == ["A", "B", "C"]
    assert [model.get_region_id(index) for index in range(3)] == original_ids
    assert model.get_selection() == [0, 2]

    command.redo()
    assert [region["text"] for region in model.get_regions()] == ["B", "C", "A"]
    assert model.get_selection() == [1, 2]
