"""渲染布局管线 — 计算 dst_points（文字渲染的目标四角点）。"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger('manga_translator')

import numpy as np
from editor import text_renderer_backend

from manga_translator.config import Config, RenderConfig
from manga_translator.rendering import calc_box_from_font
from manga_translator.utils import TextBlock


def _normalize_direction(direction_value):
    if direction_value == "h":
        return "horizontal"
    if direction_value == "v":
        return "vertical"
    return direction_value


def prepare_layout_context(render_parameter_service, _text_renderer_backend) -> Tuple[dict, Config]:
    default_params_obj = render_parameter_service.get_default_parameters()
    global_params_dict = default_params_obj.to_dict()
    global_params_dict["direction"] = _normalize_direction(global_params_dict.get("direction"))

    config_obj = Config(render=RenderConfig(**global_params_dict))
    return global_params_dict, config_obj


def build_region_specific_params(global_params_dict: dict, text_block: TextBlock) -> dict:
    region_params = global_params_dict.copy()
    if hasattr(text_block, "direction"):
        region_params["direction"] = _normalize_direction(getattr(text_block, "direction", None))
    if hasattr(text_block, "letter_spacing"):
        region_params["letter_spacing"] = getattr(text_block, "letter_spacing", None)
    region_font_path = getattr(text_block, "font_path", "")
    if region_font_path:
        region_params["font_path"] = region_font_path
    return region_params


def calculate_region_dst_points(
    text_block: TextBlock,
    region_params: dict,
    config_obj: Config,
    override_dst_points=None,
) -> Optional[object]:
    """计算文字渲染的目标四角点（世界坐标轴对齐矩形）。

    dst_points 以 text_block.center 为中心。在快照流程中，center 已经被设为
    render_center（白框中心的世界坐标），因此 dst_points 自然与白框对齐。
    """
    if override_dst_points is not None:
        return override_dst_points

    font_size = text_block.font_size if text_block.font_size > 0 else 24
    translation = text_block.translation or ""
    if not translation.strip():
        return text_block.min_rect

    is_horizontal = text_block.horizontal
    line_spacing = region_params.get("line_spacing") or config_obj.render.line_spacing or 1.0
    letter_spacing = region_params.get("letter_spacing") or getattr(config_obj.render, "letter_spacing", None) or 1.0
    target_lang = text_block.target_lang or "en_US"
    region_font_path = region_params.get("font_path") or getattr(text_block, "font_path", "")
    text_renderer_backend.apply_font_for_render(region_font_path)
    # 编辑器尺寸计算与最终渲染保持一致，避免竖排内横排块出现白框/文字不一致
    box_w, box_h, _ = calc_box_from_font(
        font_size,
        translation,
        is_horizontal,
        line_spacing,
        config_obj,
        target_lang,
        center=None,
        angle=0,
        letter_spacing=letter_spacing,
    )
    cx, cy = tuple(text_block.center)
    hw = float(box_w) / 2.0
    hh = float(box_h) / 2.0
    return np.array(
        [[[cx - hw, cy - hh], [cx + hw, cy - hh],
          [cx + hw, cy + hh], [cx - hw, cy + hh]]],
        dtype=np.float32,
    )
