"""
alignment_service.py 的行为测试。
使用 Mock 对象模拟 RegionTextItem，不依赖 Qt。
每个测试验证一个行为，通过公开接口。
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --- Mock classes: mimic RegionTextItem interface without Qt ---

class MockPoint:
    """Mimics QPointF."""
    def __init__(self, x: float, y: float):
        self._x = float(x)
        self._y = float(y)

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


class MockItem:
    """
    Mimics RegionTextItem's geometry interface.
    - pos() returns the item center in world coords (QPointF-like)
    - _get_white_frame_world_points() returns reference points in world coords
    All coordinates are in world (scene) space.
    """

    def __init__(
        self,
        idx: int,
        center_x: float,
        center_y: float,
        wf_left: float,
        wf_top: float,
        wf_right: float,
        wf_bottom: float,
    ):
        self.region_index = idx
        self._cx = float(center_x)
        self._cy = float(center_y)
        self._pos = MockPoint(self._cx, self._cy)
        wf_cx = self._cx + (wf_left + wf_right) / 2.0
        wf_cy = self._cy + (wf_top + wf_bottom) / 2.0
        self._wf = {
            "center": MockPoint(wf_cx, wf_cy),
            "left": MockPoint(self._cx + wf_left, wf_cy),
            "right": MockPoint(self._cx + wf_right, wf_cy),
            "top": MockPoint(wf_cx, self._cy + wf_top),
            "bottom": MockPoint(wf_cx, self._cy + wf_bottom),
        }

    def pos(self):
        return self._pos

    def _get_white_frame_world_points(self):
        return self._wf


# --- Helpers ---

def make_items(*specs):
    """Each spec: (idx, cx, cy, wf_left, wf_top, wf_right, wf_bottom)"""
    return [MockItem(*s) for s in specs]


# ============================================================
# Tests: align_items
# ============================================================

def test_align_horizontal_center_selection():
    """对齐：水平居中（参照选区）。两个宽度不同的框应对齐到包围盒水平中线。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    # Box A: center(100, 200), wf(-30, -20, 30, 20)  → width 60
    # Box B: center(300, 200), wf(-50, -20, 50, 20)  → width 100
    # Selection bounds x: min=100-30=70, max=300+50=350, mid=210
    items = make_items(
        (0, 100, 200, -30, -20, 30, 20),
        (1, 300, 200, -50, -20, 50, 20),
    )

    result = align_items(items, "horizontal_center", "selection")

    assert len(result) == 2
    # Both should move to X=210 (selection horizontal center)
    # Item 0: wf_cx = 100, delta = 210 - 100 = 110, new_cx = 100 + 110 = 210
    assert result[0][0] == 0 and abs(result[0][1] - 210.0) < 0.01
    # Item 1: wf_cx = 300, delta = 210 - 300 = -90, new_cx = 300 + (-90) = 210
    assert result[1][0] == 1 and abs(result[1][1] - 210.0) < 0.01
    # Y should not change
    assert abs(result[0][2] - 200.0) < 0.01
    assert abs(result[1][2] - 200.0) < 0.01


def test_align_left_selection():
    """对齐：左对齐（参照选区）。所有框左边对齐到包围盒最左边。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 200, 100, -20, -10, 20, 10),   # wf_left = 200-20 = 180
        (1, 400, 100, -40, -10, 40, 10),   # wf_left = 400-40 = 360
    )
    # Selection min_x = min(180, 360) = 180

    result = align_items(items, "left", "selection")

    # Item 0: current wf_left=180, target=180, delta=0, cx unchanged
    assert result[0][0] == 0 and abs(result[0][1] - 200.0) < 0.01
    # Item 1: current wf_left=360, target=180, delta=180-360=-180, new_cx = 400+(-180) = 220
    assert result[1][0] == 1 and abs(result[1][1] - 220.0) < 0.01


def test_align_right_selection():
    """对齐：右对齐（参照选区）。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 200, 100, -20, -10, 20, 10),   # wf_right = 200+20 = 220
        (1, 400, 100, -40, -10, 40, 10),   # wf_right = 400+40 = 440
    )
    # Selection max_x = max(220, 440) = 440

    result = align_items(items, "right", "selection")

    # Item 0: wf_right=220, target=440, delta=220, new_cx=200+220=420
    assert abs(result[0][1] - 420.0) < 0.01
    # Item 1: wf_right=440, target=440, delta=0, cx unchanged
    assert abs(result[1][1] - 400.0) < 0.01


def test_align_top_selection():
    """对齐：顶对齐（参照选区）。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 100, -10, -30, 10, 30),   # wf_top = 100-30 = 70
        (1, 100, 300, -10, -20, 10, 20),   # wf_top = 300-20 = 280
    )
    # Selection min_y = min(70, 280) = 70

    result = align_items(items, "top", "selection")

    # Item 0: wf_top=70, target=70, delta=0, cy unchanged
    assert abs(result[0][2] - 100.0) < 0.01
    # Item 1: wf_top=280, target=70, delta=-210, new_cy=300-210=90
    assert abs(result[1][2] - 90.0) < 0.01
    # X should not change
    assert abs(result[1][1] - 100.0) < 0.01


def test_align_bottom_selection():
    """对齐：底对齐（参照选区）。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 100, -10, -20, 10, 20),   # wf_bottom = 100+20 = 120
        (1, 100, 300, -10, -30, 10, 30),   # wf_bottom = 300+30 = 330
    )
    # Selection max_y = max(120, 330) = 330

    result = align_items(items, "bottom", "selection")

    # Item 0: wf_bottom=120, target=330, delta=210, new_cy=100+210=310
    assert abs(result[0][2] - 310.0) < 0.01
    # Item 1: wf_bottom=330, target=330, delta=0, cy unchanged
    assert abs(result[1][2] - 300.0) < 0.01


def test_align_vertical_center_selection():
    """对齐：垂直居中（参照选区）。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 100, -10, -30, 10, 30),   # wf_cy=100
        (1, 100, 400, -10, -50, 10, 50),   # wf_cy=400
    )
    # Selection mid_y = (70+450)/2 = 260

    result = align_items(items, "vertical_center", "selection")

    # Item 0: wf_cy=100, target=260, delta=160, new_cy=100+160=260
    assert abs(result[0][2] - 260.0) < 0.01
    # Item 1: wf_cy=400, target=260, delta=-140, new_cy=400-140=260
    assert abs(result[1][2] - 260.0) < 0.01


def test_align_horizontal_center_canvas():
    """对齐：水平居中（参照画布）。以画布中线为基准。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 200, -20, -10, 20, 10),
        (1, 500, 200, -30, -10, 30, 10),
    )
    canvas = (0, 0, 800, 600)  # canvas mid_x = 400

    result = align_items(items, "horizontal_center", "canvas", canvas)

    # Both should center at X=400
    assert abs(result[0][1] - 400.0) < 0.01
    assert abs(result[1][1] - 400.0) < 0.01


def test_align_top_canvas():
    """对齐：顶对齐（参照画布）。以画布顶边为基准。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 150, -20, -30, 20, 30),   # wf_top = 150-30 = 120
        (1, 100, 300, -20, -20, 20, 20),   # wf_top = 300-20 = 280
    )
    canvas = (0, 0, 800, 600)  # canvas min_y = 0

    result = align_items(items, "top", "canvas", canvas)

    # Item 0: wf_top=120, target=0, delta=-120, new_cy=150-120=30
    assert abs(result[0][2] - 30.0) < 0.01
    # Item 1: wf_top=280, target=0, delta=-280, new_cy=300-280=20
    assert abs(result[1][2] - 20.0) < 0.01


# ============================================================
# Tests: distribute_items
# ============================================================

def test_distribute_horizontal_center():
    """分布：水平居中分布。3个框，两端不动，中间均分。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -20, -10, 20, 10),   # wf_cx = 100
        (1, 300, 100, -20, -10, 20, 10),   # wf_cx = 300
        (2, 500, 100, -20, -10, 20, 10),   # wf_cx = 500
    )

    result = distribute_items(items, "horizontal_center")

    # Item 0 (min) and Item 2 (max) should not move
    # Item 1 should move to the middle: 100 + (500-100)/2 = 300 → no change
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][1] - 300.0) < 0.01


def test_distribute_horizontal_center_uneven():
    """分布：水平居中分布。框宽度不同，中间框均匀分到两端之间。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),   # wf_cx = 100
        (1, 200, 100, -10, -10, 10, 10),   # wf_cx = 200
        (2, 500, 100, -10, -10, 10, 10),   # wf_cx = 500
    )

    result = distribute_items(items, "horizontal_center")

    # Item 1 target: 100 + (500-100)/2 = 300, delta = 300-200 = 100, new_cx = 300
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][1] - 300.0) < 0.01


def test_distribute_vertical_center():
    """分布：垂直居中分布。3个框纵向均匀分布。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -20, 10, 20),   # wf_cy = 100
        (1, 100, 300, -10, -20, 10, 20),   # wf_cy = 300
        (2, 100, 500, -10, -20, 10, 20),   # wf_cy = 500
    )

    result = distribute_items(items, "vertical_center")

    # Item 0 and 2 should not move
    # Item 1 should already be centered between 100 and 500
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][2] - 300.0) < 0.01


def test_distribute_five_items():
    """分布：5个框水平居中分布。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),   # wf_cx = 100
        (1, 200, 100, -10, -10, 10, 10),   # wf_cx = 200
        (2, 300, 100, -10, -10, 10, 10),   # wf_cx = 300
        (3, 400, 100, -10, -10, 10, 10),   # wf_cx = 400
        (4, 800, 100, -10, -10, 10, 10),   # wf_cx = 800
    )

    result = distribute_items(items, "horizontal_center")

    # Endpoints (0 and 4) should not move
    # Items 1,2,3 should be evenly spaced: step = (800-100)/4 = 175
    # Item 1→275, Item 2→450, Item 3→625
    assert len(result) == 3
    expected = {1: 275.0, 2: 450.0, 3: 625.0}
    for idx, new_cx, _new_cy in result:
        assert abs(new_cx - expected[idx]) < 0.01


def test_distribute_top():
    """分布：按顶分布。3个框。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -30, 10, 30),   # wf_top = 100-30 = 70
        (1, 100, 200, -10, -40, 10, 40),   # wf_top = 200-40 = 160
        (2, 100, 500, -10, -20, 10, 20),   # wf_top = 500-20 = 480
    )

    result = distribute_items(items, "top")

    # Item 1 target: 70 + (480-70)/2 = 275, delta = 275-160 = 115, new_cy = 200+115 = 315
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][2] - 315.0) < 0.01


def test_distribute_left():
    """分布：按左分布。3个框。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -20, -10, 20, 10),   # wf_left = 100-20 = 80
        (1, 300, 100, -30, -10, 30, 10),   # wf_left = 300-30 = 270
        (2, 600, 100, -10, -10, 10, 10),   # wf_left = 600-10 = 590
    )

    result = distribute_items(items, "left")

    # Item 1 target: 80 + (590-80)/2 = 335, delta = 335-270 = 65, new_cx = 300+65 = 365
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][1] - 365.0) < 0.01


def test_distribute_bottom():
    """分布：按底分布。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -20, 10, 20),   # wf_bottom = 100+20 = 120
        (1, 100, 300, -10, -20, 10, 20),   # wf_bottom = 300+20 = 320
        (2, 100, 600, -10, -20, 10, 20),   # wf_bottom = 600+20 = 620
    )

    result = distribute_items(items, "bottom")

    # Item 1 target: 120 + (620-120)/2 = 370, delta = 370-320 = 50, new_cy = 300+50 = 350
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][2] - 350.0) < 0.01


def test_distribute_right():
    """分布：按右分布。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),   # wf_right = 100+10 = 110
        (1, 300, 100, -30, -10, 30, 10),   # wf_right = 300+30 = 330
        (2, 600, 100, -10, -10, 10, 10),   # wf_right = 600+10 = 610
    )

    result = distribute_items(items, "right")

    # Item 1 target: 110 + (610-110)/2 = 360, delta = 360-330 = 30, new_cx = 300+30 = 330
    assert len(result) == 1
    assert result[0][0] == 1
    assert abs(result[0][1] - 330.0) < 0.01


# ============================================================
# Tests: edge cases
# ============================================================

def test_align_empty_list():
    """边界：空列表对齐返回空。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    result = align_items([], "horizontal_center", "selection")
    assert result == []


def test_align_single_item():
    """边界：单项对齐（参照选区），delta=0 因为 target==current。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items((0, 100, 100, -10, -10, 10, 10))
    result = align_items(items, "horizontal_center", "selection")
    assert len(result) == 1
    # delta should be 0 since target == current (single item = selection bounds)
    assert abs(result[0][1] - 100.0) < 0.01
    assert abs(result[0][2] - 100.0) < 0.01


def test_align_single_item_to_canvas_center():
    """单项对齐到画布中心：单个框应移动到页面正中央。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items((0, 100, 100, -10, -10, 10, 10))
    canvas = (0, 0, 800, 600)  # mid=(400, 300)

    # 水平居中: target=400, wf_cx=100, delta=300, new_cx=400
    result_h = align_items(items, "horizontal_center", "canvas", canvas)
    assert len(result_h) == 1
    assert abs(result_h[0][1] - 400.0) < 0.01

    # 垂直居中: target=300, wf_cy=100, delta=200, new_cy=300
    result_v = align_items(items, "vertical_center", "canvas", canvas)
    assert len(result_v) == 1
    assert abs(result_v[0][2] - 300.0) < 0.01


def test_distribute_empty_list():
    """边界：空列表分布返回空。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    result = distribute_items([], "horizontal_center")
    assert result == []


def test_distribute_two_items():
    """边界：2项分布返回空（至少需要3个）。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),
        (1, 300, 100, -10, -10, 10, 10),
    )
    result = distribute_items(items, "horizontal_center")
    assert result == []


def test_align_invalid_mode():
    """边界：无效对齐模式返回空。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),
        (1, 300, 100, -10, -10, 10, 10),
    )
    result = align_items(items, "invalid_mode", "selection")
    assert result == []


def test_distribute_invalid_mode():
    """边界：无效分布模式仍会以 center 为 fallback 产生结果。"""
    from desktop_qt_ui.editor.alignment_service import distribute_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),
        (1, 200, 100, -10, -10, 10, 10),
        (2, 500, 100, -10, -10, 10, 10),
    )
    result = distribute_items(items, "invalid_mode")
    # Falls back to "center" ref_key, horizontal distribution
    assert len(result) == 1  # item 1 gets moved


def test_align_canvas_no_rect():
    """边界：画布模式但未提供 canvas_rect，行为应等同于 selection 模式。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    items = make_items(
        (0, 100, 100, -10, -10, 10, 10),
        (1, 300, 100, -10, -10, 10, 10),
    )
    result = align_items(items, "horizontal_center", "canvas", None)
    # canvas_rect=None → _get_target_line returns None → result is empty
    assert result == []


# ============================================================
# Test: integration-style - rotated items
# ============================================================

def test_align_with_offset_white_frame():
    """对齐应正确处理白框偏移（非对称白框）。"""
    from desktop_qt_ui.editor.alignment_service import align_items

    # Box A: center(100,100), wf(-10,-20, 30,10) → asymmetric wf
    # Box B: center(400,100), wf(-20,-10, 20,30) → asymmetric wf
    items = make_items(
        (0, 100, 100, -10, -20, 30, 10),
        (1, 400, 100, -20, -10, 20, 30),
    )

    result = align_items(items, "horizontal_center", "selection")
    # Selection bounds: x min = 100-10=90, x max = 400+20=420, mid = 255
    # Item 0: wf_cx = 100 + (-10+30)/2 = 110, delta = 255-110 = 145, new_cx = 245
    # Item 1: wf_cx = 400 + (-20+20)/2 = 400, delta = 255-400 = -145, new_cx = 255
    assert len(result) == 2
    assert abs(result[0][1] - 245.0) < 0.01
    assert abs(result[1][1] - 255.0) < 0.01


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_")
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        sys.exit(1)
