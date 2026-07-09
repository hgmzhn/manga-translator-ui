"""几何编辑提交管线 — 构建旋转 / 白框编辑的 region_data。"""
import copy
from typing import Optional

from manga_translator.rendering import calc_font_from_box

from editor.render_text_value import has_renderable_text, render_text_value_from_region


def build_rotate_region_data(
    region_data: dict,
    new_angle: float,
    new_center: Optional[list] = None,
    new_lines: Optional[list] = None,
) -> dict:
    """构建旋转提交数据（可选包含 center / lines 同步）。"""
    data = copy.deepcopy(region_data)
    data["angle"] = float(new_angle)
    if new_center is not None and len(new_center) >= 2:
        data["center"] = [float(new_center[0]), float(new_center[1])]
    if new_lines is not None:
        data["lines"] = copy.deepcopy(new_lines)
    return data


def build_white_frame_region_data(
    region_data: dict,
    white_patch: dict,
    white_frame_local: Optional[list],
    old_white_frame_local: Optional[list] = None,
    edit_mode: Optional[str] = None,
) -> dict:
    """构建白框编辑提交数据（含可选字体尺寸回写）。"""
    data = copy.deepcopy(region_data)
    data.update(white_patch)

    if edit_mode == "white_move":
        return data

    if not _white_frame_size_changed(old_white_frame_local, white_frame_local):
        return data

    new_fs = _calc_font_size(data, white_frame_local)
    if new_fs is not None:
        data["font_size"] = new_fs
    return data


def _white_frame_size_changed(
    old_wf_local: Optional[list],
    new_wf_local: Optional[list],
) -> bool:
    """仅当白框宽高发生变化时，才触发字号重算。"""
    old_size = _extract_white_frame_pixel_size(old_wf_local)
    new_size = _extract_white_frame_pixel_size(new_wf_local)
    if old_size is None or new_size is None:
        return True

    return new_size != old_size


def _extract_white_frame_pixel_size(wf_local: Optional[list]) -> Optional[tuple[int, int]]:
    size = _extract_white_frame_size(wf_local)
    if size is None:
        return None
    width, height = size
    return int(round(width)), int(round(height))


def _extract_white_frame_size(wf_local: Optional[list]) -> Optional[tuple[float, float]]:
    if wf_local is None or len(wf_local) != 4:
        return None
    left, top, right, bottom = wf_local
    width = float(max(0.0, right - left))
    height = float(max(0.0, bottom - top))
    if width <= 0.0 or height <= 0.0:
        return None
    return width, height


def _calc_font_size(region_data: dict, wf_local: Optional[list]) -> Optional[int]:
    size = _extract_white_frame_size(wf_local)
    if size is None:
        return None
    w, h = size
    # 反算与正算使用同一文本源（translation_rich 优先），
    # 保证拖框得到的字号与随后按字号正算的白框尺寸一致。
    text = render_text_value_from_region(region_data)
    direction = region_data.get("direction", "h")
    is_h = direction in ("h", "horizontal", "hr")
    if not (has_renderable_text(text) and w > 0 and h > 0):
        return None
    fs = calc_font_from_box(
        w,
        h,
        text,
        is_h,
        region_data.get("line_spacing", 1.0) or 1.0,
        letter_spacing=region_data.get("letter_spacing", 1.0) or 1.0,
    )
    return int(max(8, fs))
