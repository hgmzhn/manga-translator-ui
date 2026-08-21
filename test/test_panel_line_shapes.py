import _bootstrap  # noqa: F401, I001

import numpy as np

from manga_translator.utils.panel.lib import page as page_module
from manga_translator.utils.panel.lib.page import Page


class _LineDetector:
    def __init__(self, lines: np.ndarray):
        self._lines = lines

    def detect(self, _image: np.ndarray):
        return self._lines, None, None, None


def _detect_segments(monkeypatch, lines: np.ndarray):
    detector = _LineDetector(lines)
    monkeypatch.setattr(page_module.cv, "createLineSegmentDetector", lambda _mode: detector)

    page = Page.__new__(Page)
    page.gray = np.zeros((100, 100), dtype=np.uint8)
    page.img_size = [100, 100]
    page.small_panel_ratio = 0.1
    page.get_segments()
    return page.segments


def test_get_segments_accepts_opencv_4_and_5_line_shapes(monkeypatch):
    for lines in (
        np.array([[[10.2, 20.4, 80.6, 70.8]]], dtype=np.float32),
        np.array([[10.2, 20.4, 80.6, 70.8]], dtype=np.float32),
    ):
        segments = _detect_segments(monkeypatch, lines)

        assert len(segments) == 1
        assert segments[0].a == (10, 20)
        assert segments[0].b == (81, 71)
