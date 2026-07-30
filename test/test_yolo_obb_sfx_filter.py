import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.detection import merge_detection_boxes  # noqa: E402
from manga_translator.utils import Quadrilateral  # noqa: E402


def _box(x1, y1, x2, y2):
    return Quadrilateral(
        np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32),
        "",
        0.9,
    )


def test_sfx_filter_keeps_box_inside_model_bubble_mask():
    bubble_box = _box(2, 2, 12, 12)
    outside_box = _box(20, 20, 30, 30)
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    bubble_mask = np.zeros((40, 40), dtype=np.uint8)
    bubble_mask[0:15, 0:15] = 255

    with patch(
        "manga_translator.detection.detect_bubbles_with_mangalens",
        return_value=object(),
    ), patch(
        "manga_translator.detection.build_bubble_mask_from_mangalens_result",
        return_value=bubble_mask,
    ):
        result = merge_detection_boxes(
            [],
            [bubble_box, outside_box],
            use_sfx_filter=True,
            image=image,
        )

    assert len(result) == 1
    assert result[0] is bubble_box
    assert merge_detection_boxes([], [outside_box]) == [outside_box]


def test_sfx_filter_removes_all_unsupported_boxes_when_model_fails():
    bubble_box = _box(2, 2, 12, 12)
    image = np.zeros((40, 40, 3), dtype=np.uint8)

    with patch(
        "manga_translator.detection.detect_bubbles_with_mangalens",
        side_effect=RuntimeError("model unavailable"),
    ):
        result = merge_detection_boxes(
            [],
            [bubble_box],
            use_sfx_filter=True,
            image=image,
        )

    assert result == []


def test_sfx_filter_removes_unsupported_boxes_when_no_bubble_detected():
    bubble_box = _box(2, 2, 12, 12)
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    empty_mask = np.zeros((40, 40), dtype=np.uint8)

    with patch(
        "manga_translator.detection.detect_bubbles_with_mangalens",
        return_value=object(),
    ), patch(
        "manga_translator.detection.build_bubble_mask_from_mangalens_result",
        return_value=empty_mask,
    ):
        result = merge_detection_boxes(
            [],
            [bubble_box],
            use_sfx_filter=True,
            image=image,
        )

    assert result == []


def main():
    test_sfx_filter_keeps_box_inside_model_bubble_mask()
    test_sfx_filter_removes_all_unsupported_boxes_when_model_fails()
    test_sfx_filter_removes_unsupported_boxes_when_no_bubble_detected()
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
