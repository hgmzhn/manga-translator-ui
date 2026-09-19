"""Build renderable text regions from model-facing rectangles."""

from ..domain.tool_models import CreateRegion, ToolError
from . import text


def create_region(page: dict, edit: CreateRegion) -> dict:
    x, y = edit.center
    half_w, half_h = edit.width / 2, edit.height / 2
    left, top, right, bottom = x - half_w, y - half_h, x + half_w, y + half_h
    if (left < 0 or top < 0
            or right > page.get("width", 16000) or bottom > page.get("height", 16000)):
        raise ToolError("invalid_geometry", "新文本框的未旋转矩形必须位于页面内")
    text.boundaries(edit.translation)
    config = page.get("_render_config", {})
    region = {
        "region_id": edit.region_id, "version": 1,
        "center": [x, y], "angle": edit.angle,
        "lines": [[[left, top], [right, top], [right, bottom], [left, bottom]]],
        "render_box_rect_local": [-half_w, -half_h, half_w, half_h],
        "translation": edit.translation, "translation_raw": edit.translation,
        "texts": [], "ocr_status": "missing",
        "font_size": edit.font_size, "font_color": "#000000", "stroke_color": "#ffffff",
        "alignment": "center",
        "direction": ("v" if edit.height > edit.width else "h")
        if edit.direction == "auto" else edit.direction,
    }
    for key in ("font_family", "line_spacing", "letter_spacing", "disable_font_border"):
        if config.get(key) is not None:
            region[key] = config[key]
    return region
