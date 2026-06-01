"""
对齐与分布数学计算模块（无 Qt 依赖，纯数值计算）。
对应 PS 移动工具选项栏中"对齐"和"分布"两组按钮的行为。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graphics_items import RegionTextItem


def _get_white_frame_world_ref(item: "RegionTextItem", ref_key: str) -> float | None:
    """获取 item 白框在场景坐标中指定参考点的 x 或 y 值。"""
    pts = item._get_white_frame_world_points()
    if not pts:
        return None
    pt = pts.get(ref_key)
    if pt is None:
        return None
    return pt


def _wf_ref_x(item: "RegionTextItem", ref_key: str) -> float | None:
    result = _get_white_frame_world_ref(item, ref_key)
    return result.x() if result is not None else None


def _wf_ref_y(item: "RegionTextItem", ref_key: str) -> float | None:
    result = _get_white_frame_world_ref(item, ref_key)
    return result.y() if result is not None else None


def compute_selection_bounds(items: list["RegionTextItem"]) -> tuple[float, float, float, float] | None:
    """计算所有选中项白框在世界坐标中的包围盒 (min_x, min_y, max_x, max_y)。"""
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    found = False

    for item in items:
        pts = item._get_white_frame_world_points()
        if not pts:
            continue
        for key in ("left", "right", "top", "bottom"):
            pt = pts.get(key)
            if pt is None:
                continue
            found = True
            min_x = min(min_x, pt.x())
            max_x = max(max_x, pt.x())
            min_y = min(min_y, pt.y())
            max_y = max(max_y, pt.y())

    if not found:
        return None
    return (min_x, min_y, max_x, max_y)


def _get_target_line(
    mode: str,
    reference: str,
    bounds: tuple[float, float, float, float],
    canvas_rect: tuple[float, float, float, float] | None,
) -> float | None:
    """返回对齐目标线的坐标值（x 或 y，取决于 mode）。"""
    min_x, min_y, max_x, max_y = bounds
    cmid_x = (min_x + max_x) / 2.0
    cmid_y = (min_y + max_y) / 2.0

    if reference == "canvas" and canvas_rect is not None:
        c_min_x, c_min_y, c_max_x, c_max_y = canvas_rect
        c_mid_x = (c_min_x + c_max_x) / 2.0
        c_mid_y = (c_min_y + c_max_y) / 2.0

        targets = {
            "top": c_min_y,
            "vertical_center": c_mid_y,
            "bottom": c_max_y,
            "left": c_min_x,
            "horizontal_center": c_mid_x,
            "right": c_max_x,
        }
    elif reference == "selection":
        targets = {
            "top": min_y,
            "vertical_center": cmid_y,
            "bottom": max_y,
            "left": min_x,
            "horizontal_center": cmid_x,
            "right": max_x,
        }
    else:
        return None

    return targets.get(mode)


def align_items(
    items: list["RegionTextItem"],
    mode: str,
    reference: str,
    canvas_rect: tuple[float, float, float, float] | None = None,
) -> list[tuple[int, float, float]]:
    """
    对齐多个选中项。返回 [(region_index, new_center_x, new_center_y), ...]。

    mode: top / vertical_center / bottom / left / horizontal_center / right
    reference: "selection" | "canvas"
    canvas_rect: 画布参照模式下图片的 sceneBoundingRect (min_x, min_y, max_x, max_y)，可为 None
    """
    bounds = compute_selection_bounds(items)
    if bounds is None:
        return []

    target = _get_target_line(mode, reference, bounds, canvas_rect)
    if target is None:
        return []

    is_vertical = mode in ("top", "vertical_center", "bottom")
    ref_key_from_mode = {
        "top": "top",
        "vertical_center": "center",
        "bottom": "bottom",
        "left": "left",
        "horizontal_center": "center",
        "right": "right",
    }
    ref_key = ref_key_from_mode.get(mode, "center")

    results = []
    for item in items:
        if is_vertical:
            current = _wf_ref_y(item, ref_key)
        else:
            current = _wf_ref_x(item, ref_key)

        if current is None:
            continue

        delta = target - current
        new_cx = float(item.pos().x()) + (0.0 if is_vertical else delta)
        new_cy = float(item.pos().y()) + (delta if is_vertical else 0.0)
        results.append((item.region_index, new_cx, new_cy))

    return results


def distribute_items(
    items: list["RegionTextItem"],
    mode: str,
) -> list[tuple[int, float, float]]:
    """
    均分多个选中项的间距。返回 [(region_index, new_center_x, new_center_y), ...]。
    两端不动，中间项均分。

    mode: top / vertical_center / bottom / left / horizontal_center / right
    """
    if len(items) < 3:
        return []

    is_vertical = mode in ("top", "vertical_center", "bottom")
    ref_key_from_mode = {
        "top": "top",
        "vertical_center": "center",
        "bottom": "bottom",
        "left": "left",
        "horizontal_center": "center",
        "right": "right",
    }
    ref_key = ref_key_from_mode.get(mode, "center")

    # 收集每个 item 的参考位置，过滤无数据的
    positioned: list[tuple["RegionTextItem", float, float, float]] = []
    for item in items:
        cx = float(item.pos().x())
        cy = float(item.pos().y())
        if is_vertical:
            ref = _wf_ref_y(item, ref_key)
        else:
            ref = _wf_ref_x(item, ref_key)
        if ref is None:
            continue
        positioned.append((item, ref, cx, cy))

    if len(positioned) < 3:
        return []

    # 按参考值排序
    positioned.sort(key=lambda p: p[1])

    first_ref = positioned[0][1]
    last_ref = positioned[-1][1]
    n = len(positioned)

    results = []
    for i, (item, ref, cx, cy) in enumerate(positioned):
        if i == 0 or i == n - 1:
            # 两端不动
            continue
        target_ref = first_ref + (last_ref - first_ref) * i / (n - 1)
        delta = target_ref - ref
        new_cx = cx + (0.0 if is_vertical else delta)
        new_cy = cy + (delta if is_vertical else 0.0)
        results.append((item.region_index, new_cx, new_cy))

    return results


def distribute_spacing_items(
    items: list["RegionTextItem"],
    orientation: str,
) -> list[tuple[int, float, float]]:
    """
    真正的间距分布：等分 item 之间的空白间隙，而非等分边缘/中心位置。

    orientation: "vertical" | "horizontal"
    返回 [(region_index, new_center_x, new_center_y), ...]
    """
    if len(items) < 3:
        return []

    is_vert = orientation == "vertical"
    positioned: list[tuple["RegionTextItem", float, float, float, float]] = []
    for item in items:
        pts = item._get_white_frame_world_points()
        if not pts:
            continue
        cx = float(item.pos().x())
        cy = float(item.pos().y())
        if is_vert:
            top_pt = pts.get("top")
            bottom_pt = pts.get("bottom")
            if top_pt is None or bottom_pt is None:
                continue
            near = top_pt.y()
            far = bottom_pt.y()
            pos = near
        else:
            left_pt = pts.get("left")
            right_pt = pts.get("right")
            if left_pt is None or right_pt is None:
                continue
            near = left_pt.x()
            far = right_pt.x()
            pos = near
        positioned.append((item, near, far, cx, cy, pos))

    if len(positioned) < 3:
        return []

    positioned.sort(key=lambda p: p[5])
    n = len(positioned)
    first_near = positioned[0][1]
    last_far = positioned[-1][2]
    total_span = last_far - first_near
    total_size = sum(far - near for _, near, far, _, _, _ in positioned)
    total_gap = max(0.0, total_span - total_size)
    gap_per_space = total_gap / (n - 1)

    results = []
    prev_delta = 0.0
    for i in range(1, n - 1):
        item, near, far, cx, cy, _pos = positioned[i]
        # Use the shifted far of the previous item so later gaps
        # accumulate corrections from earlier redistributions.
        prev_far = positioned[i - 1][2] + prev_delta
        target_near = prev_far + gap_per_space
        delta = target_near - near
        prev_delta = delta
        new_cx = cx + (0.0 if is_vert else delta)
        new_cy = cy + (delta if is_vert else 0.0)
        results.append((item.region_index, new_cx, new_cy))

    return results
