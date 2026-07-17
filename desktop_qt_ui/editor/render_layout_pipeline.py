"""渲染布局管线 — 用统一的 RenderParameters 计算文字几何。"""
import logging
from typing import Optional

import numpy as np
from manga_translator.rendering import calc_box_from_font
from manga_translator.utils import TextBlock

from editor import text_renderer_backend
from editor.render_text_value import (
    has_renderable_text,
    render_text_value_from_text_block,
)
from services.render_parameter_service import RenderParameters

logger = logging.getLogger('manga_translator')


def resolve_region_layout_parameters(
    render_parameter_service,
    region_index: int,
    region_data: dict,
    text_block: TextBlock,
) -> RenderParameters:
    """Resolve the exact parameter snapshot shared by measurement and drawing."""
    params = render_parameter_service.get_region_parameters(region_index, region_data)
    region_font = getattr(text_block, "font_family", "")
    if region_font:
        params.font_family = region_font
    return params


def calculate_region_dst_points(
    text_block: TextBlock,
    params: RenderParameters,
    override_dst_points=None,
) -> Optional[object]:
    """计算文字渲染的目标四角点（世界坐标轴对齐矩形）。

    dst_points 以 text_block.center 为中心。在快照流程中，center 已经被设为
    render_center（白框中心的世界坐标），因此 dst_points 自然与白框对齐。
    """
    if override_dst_points is not None:
        return override_dst_points

    font_size = text_block.font_size if text_block.font_size > 0 else 24
    translation = render_text_value_from_text_block(text_block)
    if not has_renderable_text(translation):
        return text_block.min_rect

    is_horizontal = text_block.horizontal
    line_spacing = params.line_spacing or 1.0
    letter_spacing = params.letter_spacing or 1.0
    target_lang = text_block.target_lang or "en_US"
    region_font = params.font_family or getattr(text_block, "font_family", "")
    text_renderer_backend.apply_font_for_render(region_font)
    # 编辑器尺寸计算与最终渲染保持一致，避免预览白框和最终文字尺寸不一致。
    box_w, box_h, _, _ = calc_box_from_font(
        font_size,
        translation,
        is_horizontal,
        line_spacing,
        None,
        target_lang,
        center=None,
        angle=0,
        letter_spacing=letter_spacing,
        stroke_width=params.effective_stroke_width,
    )
    cx, cy = tuple(text_block.center)
    hw = float(box_w) / 2.0
    hh = float(box_h) / 2.0
    return np.array(
        [[[cx - hw, cy - hh], [cx + hw, cy - hh],
          [cx + hw, cy + hh], [cx - hw, cy + hh]]],
        dtype=np.float32,
    )
