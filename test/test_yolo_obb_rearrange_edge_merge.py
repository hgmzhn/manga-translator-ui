import sys
from pathlib import Path

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from manga_translator.detection.yolo_obb import YOLOOBBDetector


def _box(x1, y1, x2, y2):
    return np.array(
        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        dtype=np.float32,
    )


def test_other_boxes_merge_only_across_adjacent_rearrange_edges():
    boxes = np.array(
        [
            _box(124, 2780, 591, 3031),
            _box(127, 2649, 590, 2880),
        ]
    )
    scores = np.array([0.96, 0.95], dtype=np.float32)
    class_ids = np.array([5, 5], dtype=np.int32)
    source_indices = np.array([1, 0], dtype=np.int32)

    merged, _, _ = YOLOOBBDetector._merge_edge_other_boxes(
        boxes,
        scores,
        class_ids,
        source_indices,
        np.array([1, 2], dtype=np.int32),
    )
    unchanged, _, _ = YOLOOBBDetector._merge_edge_other_boxes(
        boxes,
        scores,
        class_ids,
        source_indices,
        np.array([0, 0], dtype=np.int32),
    )

    assert len(merged) == 1
    assert len(unchanged) == 2
    np.testing.assert_allclose(merged[0].min(axis=0), [124, 2649], atol=1)
    np.testing.assert_allclose(merged[0].max(axis=0), [591, 3031], atol=1)
