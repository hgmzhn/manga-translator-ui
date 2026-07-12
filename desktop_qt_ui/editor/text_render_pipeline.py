"""文字渲染管线 — 构建 TextBlock、render_params、执行渲染。"""
import json
import logging
from typing import Optional

import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPixmap, QTransform

from manga_translator.utils import TextBlock

logger = logging.getLogger('manga_translator')


def _freeze_cache_value(value):
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(item) for item in value)
    if isinstance(value, np.ndarray):
        return tuple(map(tuple, value.reshape(-1, value.shape[-1]))) if value.ndim else value.item()
    return value


def build_text_block_from_region(region_data: dict, font_size_override=None, log_tag: str = "") -> Optional[TextBlock]:
    args = region_data.copy()

    try:
        if "lines" in args and isinstance(args["lines"], list):
            args["lines"] = np.array(args["lines"])

        if args.get("texts") is None:
            args["texts"] = []

        if "font_color" in args and isinstance(args["font_color"], str):
            hex_color = args.pop("font_color")
            try:
                r = int(hex_color[1:3], 16)
                g = int(hex_color[3:5], 16)
                b = int(hex_color[5:7], 16)
                args["fg_color"] = (r, g, b)
            except (ValueError, TypeError):
                args["fg_color"] = (0, 0, 0)
        elif "fg_colors" in args:
            args["fg_color"] = args.pop("fg_colors")

        if "bg_colors" in args:
            args["bg_color"] = args.pop("bg_colors")

        if "direction" in args:
            d = args["direction"]
            if d == "horizontal":
                args["direction"] = "h"
            elif d == "vertical":
                args["direction"] = "v"

        # center 由上游快照显式给定，不做隐式偏移
        args["angle"] = 0
        if font_size_override is not None:
            args["font_size"] = font_size_override

        return TextBlock(**args)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        # 非法区域数据不再被静默吞掉：记录后跳过该区域（画布留空），供用户
        # 排查（F04c）。IndexError 覆盖 texts 为空列表时 TextBlock 取
        # texts[0] 的既有崩溃路径；lines 形状不合法时 np.array 的 ValueError
        # 同属"坏区域数据"，一并纳入降级。
        logger.warning("Failed to build TextBlock from region data%s: %s", log_tag, exc)
        return None


def build_region_render_params(
    render_parameter_service,
    _text_renderer_backend,
    region_index: int,
    region_data: dict,
    text_block: TextBlock,
) -> dict:
    render_params = render_parameter_service.export_parameters_for_backend(region_index, region_data)
    render_params["font_size"] = text_block.font_size
    region_font = (
        region_data.get("font_family")
        or getattr(text_block, "font_family", "")
        or render_params.get("font_family", "")
    )
    if region_font:
        render_params["font_family"] = region_font
        text_block.font_family = region_font

    return render_params


def make_text_render_cache_key(text_block: TextBlock, dst_points: np.ndarray, render_params: dict):
    return (
        _freeze_cache_value(text_block.get_translation_for_rendering()),
        tuple(map(tuple, dst_points.reshape(-1, 2))),
        render_params.get("font_family"),
        render_params.get("font_size"),
        render_params.get("bold"),
        render_params.get("italic"),
        render_params.get("font_weight"),
        tuple(render_params.get("font_color", (0, 0, 0))),
        tuple(render_params.get("text_stroke_color", (0, 0, 0))),
        render_params.get("opacity"),
        render_params.get("alignment"),
        render_params.get("direction"),
        render_params.get("vertical"),
        render_params.get("line_spacing"),
        render_params.get("letter_spacing"),
        render_params.get("layout_mode"),
        render_params.get("disable_auto_wrap"),
        render_params.get("hyphenate"),
        render_params.get("font_size_offset"),
        render_params.get("font_size_minimum"),
        render_params.get("max_font_size"),
        render_params.get("font_scale_ratio"),
        render_params.get("center_text_in_bubble"),
        render_params.get("text_stroke_width"),
        render_params.get("shadow_radius"),
        render_params.get("shadow_strength"),
        tuple(render_params.get("shadow_color", (0, 0, 0))),
        tuple(render_params.get("shadow_offset", [0.0, 0.0])),
        render_params.get("disable_font_border"),
    )


def render_region_text(text_renderer_backend, text_block: TextBlock, dst_points: np.ndarray, render_params: dict, total_regions: int):
    identity_transform = QTransform()
    return text_renderer_backend.render_text_for_region(
        text_block,
        dst_points,
        identity_transform,
        render_params,
        pure_zoom=1.0,
        total_regions=total_regions,
    )


def clear_region_text(item):
    item.update_text_pixmap(QPixmap(), QPointF(0, 0))
    item.set_dst_points(None)
