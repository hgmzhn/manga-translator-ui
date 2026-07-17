import logging
from time import perf_counter

import cv2
import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPixmap, QPolygonF

from editor.render_text_value import has_renderable_text, render_text_value_from_text_block
from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import has_legacy_line_breaks, legacy_line_breaks_to_document, plain_text_of
from manga_translator.rendering.text_render import (
    set_font,
)
from manga_translator.utils import TextBlock

logger = logging.getLogger('manga_translator')

_APPLIED_FONT_TARGET = None


def _translation_preview(value, limit: int = 50) -> str:
    # F12："富文本值转纯文本"统一委托协议层单点 plain_text_of
    return plain_text_of(value)[:limit]


def apply_font_for_render(font_value: str) -> str:
    """Apply a Qt family name for the current render call."""
    global _APPLIED_FONT_TARGET

    target_font = font_value or text_render.DEFAULT_FONT_FAMILY
    if _APPLIED_FONT_TARGET == target_font:
        return target_font

    try:
        set_font(target_font)
        _APPLIED_FONT_TARGET = target_font
    except Exception:
        set_font(text_render.DEFAULT_FONT_FAMILY)
        _APPLIED_FONT_TARGET = text_render.DEFAULT_FONT_FAMILY
        return ''
    return target_font


def _rgba_image_to_qimage(rgba_image: np.ndarray) -> QImage:
    h, w, _ = rgba_image.shape
    rgba8888_premultiplied = getattr(QImage.Format, "Format_RGBA8888_Premultiplied", None)
    if rgba8888_premultiplied is not None:
        return QImage(rgba_image.data, w, h, w * 4, rgba8888_premultiplied).copy()

    bgra_image = rgba_image.copy()
    bgra_image[:, :, [0, 2]] = bgra_image[:, :, [2, 0]]
    return QImage(bgra_image.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def _map_dst_points_to_screen(dst_points: np.ndarray, transform) -> np.ndarray:
    points = np.asarray(dst_points, dtype=np.float32).reshape(4, 2)
    if transform is None or transform.isIdentity():
        return points

    qpoly = transform.map(QPolygonF([QPointF(float(p[0]), float(p[1])) for p in points]))
    return np.float32([[p.x(), p.y()] for p in qpoly])


def _target_rect_from_points(points: np.ndarray):
    x_s, y_s, w_s, h_s = cv2.boundingRect(np.round(points).astype(np.int32))
    if w_s <= 0 or h_s <= 0:
        return None
    return x_s, y_s, w_s, h_s


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


def _record_profile_elapsed(stats: dict | None, key: str, start_time: float | None) -> None:
    if stats is not None and start_time is not None:
        stats[key] = stats.get(key, 0.0) + (perf_counter() - start_time) * 1000.0


def render_text_image_for_region(text_block: TextBlock, dst_points: np.ndarray, transform, render_params: dict, pure_zoom: float = 1.0, total_regions: int = 1):
    """
    为单个区域渲染文本的核心函数
    返回一个包含 (QImage, QPointF) 的元组，适合离屏/线程内处理。
    """
    profile_stats = render_params.get("_profile_stats") if isinstance(render_params, dict) else None
    stage_t0 = perf_counter() if profile_stats is not None else None
    try:
        # --- 1. 文本预处理 ---
        text_to_render = render_text_value_from_text_block(text_block)
        if not has_renderable_text(text_to_render):
            logger.debug("[EDITOR RENDER SKIPPED] Text is empty")
            return None
        if isinstance(text_to_render, str) and has_legacy_line_breaks(text_to_render):
            text_to_render = legacy_line_breaks_to_document(text_to_render).to_dict()

        region_font = (
            render_params.get('font_family')
            or getattr(text_block, 'font_family', '')
        )
        applied_font = apply_font_for_render(region_font)
        if not applied_font and region_font:
            logger.warning(f"[EDITOR RENDER] Font unavailable: {region_font}, fallback to default font")

        # --- 2. 渲染 ---
        disable_font_border = render_params.get('disable_font_border', False)
        
        dst_points_screen = _map_dst_points_to_screen(dst_points, transform)
        target_rect = _target_rect_from_points(dst_points_screen)
        if target_rect is None:
            logger.debug("[EDITOR RENDER SKIPPED] Screen bounding box has invalid dimensions. Text may be outside visible area.")
            return None
        x_s, y_s, w_s, h_s = target_rect

        middle_pts = (dst_points_screen[[1, 2, 3, 0]] + dst_points_screen) / 2
        norm_h = np.linalg.norm(middle_pts[1] - middle_pts[3])
        norm_v = np.linalg.norm(middle_pts[2] - middle_pts[0])

        render_w = round(norm_h)
        render_h = round(norm_v)
        font_size = text_block.font_size

        # 从 text_block 获取默认颜色
        fg_color, bg_color_default = text_block.get_font_colors()
        
        # 优先使用 render_params 中用户设置的描边颜色
        bg_color = render_params.get('text_stroke_color', bg_color_default)
        
        # 从 render_params 中获取描边宽度
        stroke_width = render_params['stroke_width']
        
        if disable_font_border:
            bg_color = None

        if render_w <= 0 or render_h <= 0:
            logger.debug(f"[EDITOR RENDER SKIPPED] Invalid render dimensions: width={render_w}, height={render_h}")
            return None

        line_spacing_multiplier = render_params.get('line_spacing', 1.0)
        letter_spacing_multiplier = render_params.get('letter_spacing', 1.0)

        # 获取区域数（lines数组的长度），用于智能排版模式的换行判断
        region_count = 1
        if hasattr(text_block, 'lines') and text_block.lines is not None:
            try:
                region_count = len(text_block.lines)
            except Exception:
                region_count = 1

        text_for_render = text_to_render
        _record_profile_elapsed(profile_stats, "backend_prepare_ms", stage_t0)

        # 使用 Qt 离屏渲染器
        stage_t0 = perf_counter() if profile_stats is not None else None
        if text_block.horizontal:
            rendered_surface = text_render.put_text_horizontal(
                font_size, 
                text_for_render, 
                render_w, 
                render_h, 
                text_block.alignment, 
                text_block.direction == 'hl', 
                fg_color, 
                bg_color, 
                text_block.target_lang, 
                True, 
                line_spacing_multiplier, 
                config=None,
                region_count=region_count,
                stroke_width=stroke_width,
                letter_spacing=letter_spacing_multiplier,
                profile_stats=profile_stats,
            )
        else:
            rendered_surface = text_render.put_text_vertical(
                font_size, 
                text_for_render, 
                render_h, 
                text_block.alignment, 
                fg_color, 
                bg_color, 
                line_spacing_multiplier, 
                config=None,
                region_count=region_count,
                stroke_width=stroke_width,
                letter_spacing=letter_spacing_multiplier,
                profile_stats=profile_stats,
            )
        _record_profile_elapsed(profile_stats, "backend_draw_ms", stage_t0)

        if rendered_surface is None or rendered_surface.size == 0:
            logger.debug(
                "[EDITOR RENDER SKIPPED] Rendered surface is None or empty. "
                f"Text: '{_translation_preview(getattr(text_block, 'translation', None), 50)}...'"
            )
            return None
        
        # 转为预乘 Alpha，与 QImage.Format_RGBA8888_Premultiplied 的输入契约一致。
        stage_t0 = perf_counter() if profile_stats is not None else None
        rendered_surface = rendered_surface.copy()
        alpha_f = rendered_surface[:, :, 3] / 255.0
        rendered_surface[:, :, 0] = (rendered_surface[:, :, 0] * alpha_f).astype(np.uint8)
        rendered_surface[:, :, 1] = (rendered_surface[:, :, 1] * alpha_f).astype(np.uint8)
        rendered_surface[:, :, 2] = (rendered_surface[:, :, 2] * alpha_f).astype(np.uint8)
        _record_profile_elapsed(profile_stats, "backend_premul_ms", stage_t0)

        # --- 3. 保持原生像素尺寸 ---
        # dst_points 只提供文字的布局锚点，不再作为 RGBA 图层的缩放/透视目标。
        # RegionTextItem 的父节点负责 angle 旋转，因此编辑器后端只需把原生
        # pixmap 居中放到目标框中心。文字大于白框时允许自然溢出。
        native_image = rendered_surface
        h, w, ch = native_image.shape
        if h == 0 or w == 0:
            logger.debug(
                f"[EDITOR RENDER SKIPPED] Rendered surface has zero dimensions: "
                f"width={w}, height={h}"
            )
            return None

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

        # --- 4. 转换为QImage并返回绘制信息 ---
        if ch == 4:
            stage_t0 = perf_counter() if profile_stats is not None else None
            final_image = _rgba_image_to_qimage(native_image)
            _record_profile_elapsed(profile_stats, "backend_qimage_ms", stage_t0)
            return (final_image, native_pos, native_dst_points)

    except Exception as e:
        logger.debug(f"Error during backend text rendering: {e}")
        return None


def render_text_for_region(text_block: TextBlock, dst_points: np.ndarray, transform, render_params: dict, pure_zoom: float = 1.0, total_regions: int = 1):
    image_result = render_text_image_for_region(
        text_block,
        dst_points,
        transform,
        render_params,
        pure_zoom=pure_zoom,
        total_regions=total_regions,
    )
    if image_result is None:
        return None

    final_image, pos, native_dst_points = image_result
    profile_stats = render_params.get("_profile_stats") if isinstance(render_params, dict) else None
    stage_t0 = perf_counter() if profile_stats is not None else None
    pixmap = QPixmap.fromImage(final_image)
    _record_profile_elapsed(profile_stats, "backend_pixmap_ms", stage_t0)
    return (pixmap, pos, native_dst_points)
