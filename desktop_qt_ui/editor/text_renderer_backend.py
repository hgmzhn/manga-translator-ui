import logging

import numpy as np
from manga_translator.rendering import text_render
from manga_translator.rendering.text_render import set_font
from manga_translator.utils import TextBlock, parse_color
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPixmap, QPolygonF

from editor.render_text_value import (
    has_renderable_text,
    render_text_value_from_text_block,
)

logger = logging.getLogger("manga_translator")

_APPLIED_FONT_TARGET = None


def apply_font_for_render(font_value: str) -> str:
    """Apply a Qt family name for the current render call."""
    global _APPLIED_FONT_TARGET

    target_font = font_value or text_render.DEFAULT_FONT_FAMILY
    if _APPLIED_FONT_TARGET == target_font:
        return target_font

    set_font(target_font)
    _APPLIED_FONT_TARGET = target_font
    return target_font


def _rgba_image_to_qimage(rgba_image: np.ndarray) -> QImage:
    h, w, _ = rgba_image.shape
    return QImage(
        rgba_image.data, w, h, w * 4,
        QImage.Format.Format_RGBA8888_Premultiplied,
    ).copy()


def _map_dst_points_to_screen(dst_points: np.ndarray, transform) -> np.ndarray:
    points = np.asarray(dst_points, dtype=np.float32).reshape(4, 2)
    if transform is None or transform.isIdentity():
        return points

    qpoly = transform.map(
        QPolygonF([QPointF(float(p[0]), float(p[1])) for p in points])
    )
    return np.float32([[p.x(), p.y()] for p in qpoly])




def _native_rect_points(center, width: int, height: int, angle: float) -> np.ndarray:
    """按原生像素宽高生成实际渲染四角；只旋转，不缩放。"""
    cx, cy = float(center[0]), float(center[1])
    hw, hh = float(width) / 2.0, float(height) / 2.0
    local = np.array(
        [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]],
        dtype=np.float32,
    )
    rad = np.deg2rad(float(angle or 0.0))
    cos_a, sin_a = float(np.cos(rad)), float(np.sin(rad))
    rotated = np.empty_like(local)
    rotated[:, 0] = cx + local[:, 0] * cos_a - local[:, 1] * sin_a
    rotated[:, 1] = cy + local[:, 0] * sin_a + local[:, 1] * cos_a
    return rotated.reshape(1, 4, 2)




def render_text_image_for_region(
    text_block: TextBlock,
    dst_points: np.ndarray,
    transform,
    render_params: dict,
    pure_zoom: float = 1.0,
    total_regions: int = 1,
):
    """
    为单个区域渲染文本的核心函数
    返回一个包含 (QImage, QPointF) 的元组，适合离屏/线程内处理。
    """
    text_to_render = render_text_value_from_text_block(text_block)
    if not has_renderable_text(text_to_render):
        logger.debug("[EDITOR RENDER SKIPPED] Text is empty")
        return None

    region_font = render_params.get("font_family") or getattr(
        text_block, "font_family", ""
    )
    apply_font_for_render(region_font)

    disable_font_border = render_params.get("disable_font_border", False)
    dst_points_screen = _map_dst_points_to_screen(dst_points, transform)
    middle_pts = (dst_points_screen[[1, 2, 3, 0]] + dst_points_screen) / 2
    render_w = round(np.linalg.norm(middle_pts[1] - middle_pts[3]))
    render_h = round(np.linalg.norm(middle_pts[2] - middle_pts[0]))
    font_size = text_block.font_size

    text_block_fg, bg_color_default = text_block.get_font_colors()
    fg_color = parse_color(render_params.get("font_color"), None)
    if fg_color is None:
        fg_color = text_block_fg
    bg_color = render_params.get("text_stroke_color", bg_color_default)
    if disable_font_border:
        bg_color = None
    stroke_width = render_params["stroke_width"]

    if render_w <= 0 or render_h <= 0:
        logger.debug(
            f"[EDITOR RENDER SKIPPED] Invalid render dimensions: width={render_w}, height={render_h}"
        )
        return None

    line_spacing_multiplier = render_params.get("line_spacing", 1.0)
    letter_spacing_multiplier = render_params.get("letter_spacing", 1.0)
    region_count = len(text_block.lines) if text_block.lines is not None else 1

    if text_block.horizontal:
        rendered_surface = text_render.put_text_horizontal(
            font_size,
            text_to_render,
            render_w,
            render_h,
            text_block.alignment,
            text_block.direction == "hl",
            fg_color,
            bg_color,
            text_block.target_lang,
            True,
            line_spacing_multiplier,
            config=None,
            region_count=region_count,
            stroke_width=stroke_width,
            letter_spacing=letter_spacing_multiplier,
        )
    else:
        rendered_surface = text_render.put_text_vertical(
            font_size,
            text_to_render,
            render_h,
            text_block.alignment,
            fg_color,
            bg_color,
            line_spacing_multiplier,
            config=None,
            stroke_width=stroke_width,
            letter_spacing=letter_spacing_multiplier,
        )

    rendered_surface = rendered_surface.copy()
    alpha_f = rendered_surface[:, :, 3] / 255.0
    rendered_surface[:, :, 0] = (rendered_surface[:, :, 0] * alpha_f).astype(
        np.uint8
    )
    rendered_surface[:, :, 1] = (rendered_surface[:, :, 1] * alpha_f).astype(
        np.uint8
    )
    rendered_surface[:, :, 2] = (rendered_surface[:, :, 2] * alpha_f).astype(
        np.uint8
    )

    native_image = rendered_surface
    h, w, _ = native_image.shape
    target_center = np.mean(dst_points_screen, axis=0)
    native_pos = QPointF(
        float(target_center[0]) - w / 2.0,
        float(target_center[1]) - h / 2.0,
    )
    native_dst_points = _native_rect_points(
        target_center,
        w,
        h,
        getattr(text_block, "angle", 0.0),
    )
    final_image = _rgba_image_to_qimage(native_image)
    return (final_image, native_pos, native_dst_points)


def render_text_for_region(
    text_block: TextBlock,
    dst_points: np.ndarray,
    transform,
    render_params: dict,
    pure_zoom: float = 1.0,
    total_regions: int = 1,
):
    final_image, pos, native_dst_points = render_text_image_for_region(
        text_block,
        dst_points,
        transform,
        render_params,
        pure_zoom=pure_zoom,
        total_regions=total_regions,
    )
    return (QPixmap.fromImage(final_image), pos, native_dst_points)
