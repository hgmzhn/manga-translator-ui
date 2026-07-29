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


def test_sfx_filter_keeps_bubble_box_without_yolo_support():
    bubble_box = _box(2, 2, 12, 12)
    outside_box = _box(20, 20, 30, 30)
    image = np.zeros((40, 40, 3), dtype=np.uint8)

    with patch("manga_translator.detection.is_bubble_advanced", side_effect=lambda _img, x, *_args: x < 10):
        result = merge_detection_boxes(
            [],
            [bubble_box, outside_box],
            use_sfx_filter=True,
            image=image,
        )

    assert len(result) == 1
    assert result[0] is bubble_box
    assert merge_detection_boxes([], [outside_box]) == [outside_box]


if __name__ == "__main__":
    test_sfx_filter_keeps_bubble_box_without_yolo_support()
