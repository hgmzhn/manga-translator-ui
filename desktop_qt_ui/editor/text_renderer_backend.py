import logging
import os
from time import perf_counter

import cv2
import numpy as np
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPixmap, QPolygonF

from manga_translator.rendering import text_render
from manga_translator.rendering.text_render import (
    set_font,
)
from manga_translator.utils import TextBlock

logger = logging.getLogger('manga_translator')

_APPLIED_FONT_TARGET = None


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    import os
    import sys
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(base_path, relative_path)

def resolve_font_path(font_path: str) -> str:
    """Resolve absolute/relative font path for both dev and packaged runtime.

    When an absolute path does not exist on the current machine (e.g., the path
    was saved on a different machine or the install directory changed), we fall
    back to searching for the font file by name inside the local fonts/ directory.
    """
    if not font_path:
        return ''
    if os.path.exists(font_path):
        return font_path

    # 路径不存在时（含绝对路径盘符不同的情况），用文件名在 fonts/ 目录里继续找
    font_basename = os.path.basename(font_path)
    candidates = (
        resource_path(os.path.join('fonts', font_basename)),
        resource_path(font_basename),
    )
    if not os.path.isabs(font_path):
        # 相对路径还额外尝试直接 join
        candidates = (
            resource_path(font_path),
        ) + candidates

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ''

def apply_font_for_render(font_path: str) -> str:
    """Apply font for current render call; fallback to built-in default."""
    global _APPLIED_FONT_TARGET

    resolved_font_path = resolve_font_path(font_path)
    target_font = resolved_font_path or text_render.DEFAULT_FONT
    if _APPLIED_FONT_TARGET == target_font:
        return resolved_font_path

    try:
        set_font(target_font)
        _APPLIED_FONT_TARGET = target_font
    except Exception:
        set_font(text_render.DEFAULT_FONT)
        _APPLIED_FONT_TARGET = text_render.DEFAULT_FONT
        return ''
    return resolved_font_path


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


def _is_axis_aligned_rect(points: np.ndarray, tolerance: float = 0.01) -> bool:
    p = np.asarray(points, dtype=np.float32).reshape(4, 2)
    return (
        abs(float(p[0, 1] - p[1, 1])) <= tolerance
        and abs(float(p[2, 1] - p[3, 1])) <= tolerance
        and abs(float(p[0, 0] - p[3, 0])) <= tolerance
        and abs(float(p[1, 0] - p[2, 0])) <= tolerance
    )


def _resize_to_target_rect(box: np.ndarray, width: int, height: int) -> np.ndarray:
    if box.shape[1] == width and box.shape[0] == height:
        return box
    return cv2.resize(box, (width, height), interpolation=cv2.INTER_LINEAR)


def _record_profile_elapsed(stats: dict | None, key: str, start_time: float | None) -> None:
    if stats is not None and start_time is not None:
        stats[key] = stats.get(key, 0.0) + (perf_counter() - start_time) * 1000.0


def render_text_image_for_region(text_block: TextBlock, dst_points: np.ndarray, transform, render_params: dict, pure_zoom: float = 1.0, total_regions: int = 1):
    """
    为单个区域渲染文本的核心函数
    返回一个包含 (QImage, QPointF) 的元组，适合离屏/线程内处理。
    """
    original_translation = text_block.translation
    profile_stats = render_params.get("_profile_stats") if isinstance(render_params, dict) else None
    stage_t0 = perf_counter() if profile_stats is not None else None
    try:
        # --- 1. 文本预处理 ---
        text_to_render = original_translation or text_block.text
        if not text_to_render:
            logger.debug("[EDITOR RENDER SKIPPED] Text is empty")
            return None

        text_block.translation = text_to_render

        # 区域级字体优先：render_params.font_path -> text_block.font_path -> 默认字体
        region_font_path = render_params.get('font_path') or getattr(text_block, 'font_path', '')
        resolved_font_path = apply_font_for_render(region_font_path)
        if not resolved_font_path and region_font_path:
            logger.warning(f"[EDITOR RENDER] Font path not found: {region_font_path}, fallback to default font")

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
        stroke_width = render_params.get('text_stroke_width', None)
        
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

        text_for_render = text_render.prepare_text_for_direction_rendering(
            text_block.get_translation_for_rendering(),
            is_horizontal=text_block.horizontal,
            auto_rotate_symbols=bool(render_params.get('auto_rotate_symbols')),
        )
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
            logger.debug(f"[EDITOR RENDER SKIPPED] Rendered surface is None or empty. Text: '{text_block.translation[:50] if hasattr(text_block, 'translation') else 'N/A'}...'")
            return None
        
        # 预乘 Alpha: 防止 cv2.warpPerspective 插值或填充 0 (透明黑) 时导致黑边灰边
        stage_t0 = perf_counter() if profile_stats is not None else None
        rendered_surface = rendered_surface.copy()
        alpha_f = rendered_surface[:, :, 3] / 255.0
        rendered_surface[:, :, 0] = (rendered_surface[:, :, 0] * alpha_f).astype(np.uint8)
        rendered_surface[:, :, 1] = (rendered_surface[:, :, 1] * alpha_f).astype(np.uint8)
        rendered_surface[:, :, 2] = (rendered_surface[:, :, 2] * alpha_f).astype(np.uint8)
        _record_profile_elapsed(profile_stats, "backend_premul_ms", stage_t0)

        # --- 3. 宽高比校正 (与后端渲染逻辑完全同步) ---
        stage_t0 = perf_counter() if profile_stats is not None else None
        h_temp, w_temp, _ = rendered_surface.shape
        if h_temp == 0 or w_temp == 0:
            logger.debug(f"[EDITOR RENDER SKIPPED] Rendered surface has zero dimensions: width={w_temp}, height={h_temp}")
            return None
        r_temp = w_temp / h_temp
        
        r_orig = norm_h / norm_v

        box = None
        if text_block.horizontal:
            if r_temp > r_orig:
                h_ext = int((w_temp / r_orig - h_temp) // 2) if r_orig > 0 else 0
                if h_ext >= 0:
                    box = np.zeros((h_temp + h_ext * 2, w_temp, 4), dtype=np.uint8)
                    box[h_ext:h_ext+h_temp, 0:w_temp] = rendered_surface
                else:
                    box = rendered_surface.copy()
            else:
                w_ext = int((h_temp * r_orig - w_temp) // 2)
                if w_ext >= 0:
                    box = np.zeros((h_temp, w_temp + w_ext * 2, 4), dtype=np.uint8)
                    # 横排文本默认水平居中
                    box[0:h_temp, w_ext:w_ext+w_temp] = rendered_surface
                else:
                    box = rendered_surface.copy()
        else: # Vertical
            if r_temp > r_orig:
                h_ext = int(w_temp / (2 * r_orig) - h_temp / 2) if r_orig > 0 else 0
                if h_ext >= 0:
                    box = np.zeros((h_temp + h_ext * 2, w_temp, 4), dtype=np.uint8)
                    box[h_ext:h_ext+h_temp, 0:w_temp] = rendered_surface
                else:
                    box = rendered_surface.copy()
            else:
                w_ext = int((h_temp * r_orig - w_temp) / 2)
                if w_ext >= 0:
                    box = np.zeros((h_temp, w_temp + w_ext * 2, 4), dtype=np.uint8)
                    # 竖排文本水平居中
                    box[0:h_temp, w_ext:w_ext+w_temp] = rendered_surface
                else:
                    box = rendered_surface.copy()

        if box is None:
            box = rendered_surface.copy()
        _record_profile_elapsed(profile_stats, "backend_box_ms", stage_t0)

        # --- 4. 坐标变换与扭曲 (Warping) ---
        stage_t0 = perf_counter() if profile_stats is not None else None
        if _is_axis_aligned_rect(dst_points_screen):
            # 编辑器文字框在当前渲染链路里通常是轴对齐矩形；直接缩放比
            # findHomography + warpPerspective 轻很多，视觉结果等价。
            warped_image = _resize_to_target_rect(box, w_s, h_s)
        else:
            src_points = np.float32([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]])
            dst_points_warp = dst_points_screen - [x_s, y_s]
            matrix = cv2.getPerspectiveTransform(src_points, dst_points_warp.astype(np.float32))
            if matrix is None:
                logger.debug("[EDITOR RENDER SKIPPED] Failed to compute perspective matrix for text transformation")
                return None

            warped_image = cv2.warpPerspective(box, matrix, (w_s, h_s), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0,0))
        _record_profile_elapsed(profile_stats, "backend_warp_ms", stage_t0)

        # --- 5. 转换为QImage并返回绘制信息 ---
        h, w, ch = warped_image.shape
        if ch == 4:
            stage_t0 = perf_counter() if profile_stats is not None else None
            final_image = _rgba_image_to_qimage(warped_image)
            _record_profile_elapsed(profile_stats, "backend_qimage_ms", stage_t0)
            return (final_image, QPointF(x_s, y_s))

    except Exception as e:
        logger.debug(f"Error during backend text rendering: {e}")
        return None
    finally:
        text_block.translation = original_translation


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

    final_image, pos = image_result
    profile_stats = render_params.get("_profile_stats") if isinstance(render_params, dict) else None
    stage_t0 = perf_counter() if profile_stats is not None else None
    pixmap = QPixmap.fromImage(final_image)
    _record_profile_elapsed(profile_stats, "backend_pixmap_ms", stage_t0)
    return (pixmap, pos)
