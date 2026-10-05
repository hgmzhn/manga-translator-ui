import _bootstrap  # noqa: F401, I001

from unittest.mock import patch

import numpy as np

from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from services.history_service import EditorStateManager


def _region(center, left):
    """与画布新建文本框一致：lines 和 polygons 共用同一批顶点。"""
    top = center[1] - 10.0
    bottom = center[1] + 10.0
    points = [
        [left, top],
        [left + 40.0, top],
        [left + 40.0, bottom],
        [left, bottom],
    ]
    return {
        "text": "原文",
        "translation": "译文",
        "center": [float(center[0]), float(center[1])],
        "lines": [points],
        "polygons": [points],
    }


def _model_with_regions(regions):
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.full((400, 600, 3), 3, dtype=np.uint8),
            regions=regions,
        )
    )
    return model


def _controller(model):
    history = EditorStateManager()
    with patch("editor.editor_controller.get_history_service", return_value=history):
        from editor.editor_controller import EditorController

        return EditorController(model)


def test_copy_and_paste_multiple_regions_keeps_relative_offsets():
    model = _model_with_regions(
        [_region((100.0, 100.0), 80.0), _region((300.0, 200.0), 280.0)]
    )
    controller = _controller(model)

    controller.copy_regions([0, 1])
    controller.paste_regions()

    regions = model.get_regions()
    assert len(regions) == 4
    pasted = regions[2:]
    assert [region["center"] for region in pasted] == [[120.0, 120.0], [320.0, 220.0]]
    assert pasted[1]["center"][0] - pasted[0]["center"][0] == 200.0
    assert pasted[1]["center"][1] - pasted[0]["center"][1] == 100.0
    assert pasted[0]["lines"][0][0] == [100.0, 110.0]
    assert model.get_selection() == [2, 3]


def test_multi_region_paste_is_a_single_undo_step():
    model = _model_with_regions(
        [_region((100.0, 100.0), 80.0), _region((300.0, 200.0), 280.0)]
    )
    controller = _controller(model)

    controller.copy_regions([0, 1])
    controller.paste_regions()
    assert len(model.get_regions()) == 4

    controller.undo()
    assert len(model.get_regions()) == 2

    controller.redo()
    assert len(model.get_regions()) == 4


def test_paste_moves_shared_line_and_polygon_points_once():
    model = _model_with_regions([_region((100.0, 100.0), 80.0)])
    source = model.get_regions()[0]
    assert source["lines"][0] is source["polygons"][0]

    controller = _controller(model)
    controller.copy_regions([0])
    controller.paste_regions()

    pasted = model.get_regions()[1]
    assert pasted["center"] == [120.0, 120.0]
    assert pasted["lines"][0][0] == [100.0, 110.0]
    assert pasted["polygons"][0][0] == [100.0, 110.0]


def main() -> int:
    test_copy_and_paste_multiple_regions_keeps_relative_offsets()
    test_multi_region_paste_is_a_single_undo_step()
    test_paste_moves_shared_line_and_polygon_points_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
