from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from desktop_qt_ui.editor.desktop_ui_geometry import (  # noqa: E402
    calculate_center_scaled_rect,
    rotate_point,
)


def test_center_scale_corner_and_edges() -> None:
    rect = [-10.0, -5.0, 10.0, 5.0]
    assert calculate_center_scaled_rect(rect, "corner", 2, (20.0, 15.0)) == [
        -20.0, -15.0, 20.0, 15.0
    ]
    assert calculate_center_scaled_rect(rect, "edge", 1, (15.0, 99.0)) == [
        -15.0, -5.0, 15.0, 5.0
    ]
    assert calculate_center_scaled_rect(rect, "edge", 0, (99.0, -12.0)) == [
        -10.0, -12.0, 10.0, 12.0
    ]


def test_center_scale_keeps_offset_frame_center() -> None:
    scaled = calculate_center_scaled_rect([5.0, 10.0, 25.0, 30.0], "corner", 2, (35.0, 50.0))
    assert scaled == [-5.0, -10.0, 35.0, 50.0]
    assert (scaled[0] + scaled[2], scaled[1] + scaled[3]) == (30.0, 40.0)


def test_center_scale_rotated_pointer_and_minimum_size() -> None:
    center = (100.0, 200.0)
    world = rotate_point(120.0, 215.0, 30.0, *center)
    model = rotate_point(*world, -30.0, *center)
    local = (model[0] - center[0], model[1] - center[1])
    assert calculate_center_scaled_rect([-10.0, -5.0, 10.0, 5.0], "corner", 2, local) == [
        -20.0, -15.0, 20.0, 15.0
    ]
    assert calculate_center_scaled_rect([-10.0, -5.0, 10.0, 5.0], "corner", 0, (0.0, 0.0)) == [
        -4.0, -4.0, 4.0, 4.0
    ]


def main() -> int:
    test_center_scale_corner_and_edges()
    test_center_scale_keeps_offset_frame_center()
    test_center_scale_rotated_pointer_and_minimum_size()
    print("center-scale regression: 3 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
