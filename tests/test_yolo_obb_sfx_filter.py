import sys
from pathlib import Path

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from manga_translator.detection import merge_detection_boxes
from manga_translator.utils import Quadrilateral


def _box(x1, y1, x2, y2, label=None):
    quad = Quadrilateral(
        np.array(
            [
                [x1, y1],
                [x2, y1],
                [x2, y2],
                [x1, y2],
            ],
            dtype=np.int32,
        ),
        "",
        0.9,
    )
    if label is not None:
        quad.det_label = label
        quad.yolo_label = label
        quad.is_yolo_box = True
    return quad


def test_sfx_filter_is_disabled_by_default():
    main = _box(0, 0, 10, 10)
    yolo = _box(30, 30, 40, 40, "balloon")

    result = merge_detection_boxes([yolo], [main], overlap_threshold=0.1)

    assert main in result


def test_sfx_filter_removes_main_box_without_yolo_support():
    main = _box(0, 0, 10, 10)
    yolo = _box(30, 30, 40, 40, "balloon")

    result = merge_detection_boxes(
        [yolo],
        [main],
        overlap_threshold=0.1,
        use_sfx_filter=True,
    )

    assert main not in result
    assert yolo in result


def test_sfx_filter_keeps_main_box_overlapping_yolo_text_box():
    main = _box(0, 0, 10, 10)
    yolo = _box(5, 0, 15, 10, "balloon")

    result = merge_detection_boxes(
        [yolo],
        [main],
        overlap_threshold=0.1,
        use_sfx_filter=True,
    )

    assert main in result


def test_sfx_filter_keeps_main_box_wrapped_by_other():
    main = _box(5, 5, 10, 10)
    other = _box(0, 0, 20, 20, "other")

    result = merge_detection_boxes(
        [other],
        [main],
        overlap_threshold=0.1,
        use_sfx_filter=True,
    )

    assert main in result


def test_sfx_filter_does_not_treat_partial_other_overlap_as_wrap():
    main = _box(0, 0, 10, 10)
    other = _box(5, 0, 15, 10, "other")

    result = merge_detection_boxes(
        [other],
        [main],
        overlap_threshold=0.1,
        use_sfx_filter=True,
    )

    assert main not in result


def test_sfx_filter_removes_all_main_boxes_when_yolo_detects_nothing():
    main = _box(0, 0, 10, 10)

    result = merge_detection_boxes([], [main], use_sfx_filter=True)

    assert result == []
