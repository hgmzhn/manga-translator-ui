import copy
import base64
import math
import os
import re
from typing import List, Optional, Tuple

import cv2

# import logging
import numpy as np
from shapely import affinity
from shapely.geometry import Polygon
from tqdm import tqdm

from ..config import Config, Renderer

# 只使用 Qt 离屏渲染器
from ..utils import (
    BASE_PATH,
    TextBlock,
    build_bubble_mask_from_mangalens_result,
    fg_bg_compare,
    get_cached_bubbles_with_mangalens,
    get_logger,
    rotate_polygons,
)
from . import text_render, text_render_hq
from .auto_linebreak import (
    _is_chinese_lang,
    solve_no_br_layout,
    should_force_no_wrap_single_region,
    strip_linebreak_edge_punctuation,
)
from .chinese_linebreak import (
    BubbleLinebreakEvaluation,
    append_chinese_linebreak_debug_record,
    build_chinese_linebreak_debug_snapshot,
    bubble_mask_overflow_pixels,
    choose_chinese_bubble_linebreak_with_trace,
    download_chinese_linebreak_models_if_enabled,
)
from .text_replacement_layout import prepare_text_replacements_for_layout, sync_translation_raw_from_layout
from .text_render_eng import apply_manga2eng_line_breaks
from .rich_text import (
    ensure_rich_text_document,
    has_content as rich_text_has_content,
    is_rich_text_document,
    plain_equivalent_text,
    plain_text_of,
)

logger = get_logger('render')

# 基准字体大小，用于模拟文本块
BASE_FONT_SIZE = 100


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _encode_mask_png_base64(mask: Optional[np.ndarray]) -> str:
    if mask is None:
        return ""
    try:
        mask_u8 = np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8)
        ok, buffer = cv2.imencode(".png", mask_u8)
        if not ok:
            return ""
        return base64.b64encode(buffer).decode("ascii")
    except Exception:
        return ""


def _estimate_effect_padding(font_size: int, config: Config = None) -> float:
    """估算文本效果（当前主要是描边）带来的额外边缘像素。"""
    if font_size <= 0:
        return 0.0

    render_cfg = getattr(config, 'render', None) if config is not None else None
    disable_border = bool(getattr(render_cfg, 'disable_font_border', False)) if render_cfg is not None else False
    if disable_border:
        return 0.0

    stroke_ratio = 0.07
    if render_cfg is not None:
        stroke_ratio = _safe_float(getattr(render_cfg, 'stroke_width', stroke_ratio), stroke_ratio)
    stroke_ratio = max(stroke_ratio, 0.0)
    if stroke_ratio <= 0.0:
        return 0.0

    # 与 text_render.py 中 bg_size 计算保持一致
    return float(max(int(font_size * stroke_ratio), 1))


def _has_explicit_line_breaks(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return bool(re.search(r'(\[BR\]|【BR】|<br\s*/?>|\r\n|\r|\n)', text or '', flags=re.IGNORECASE))


def _rich_text_has_content(value) -> bool:
    # F12：薄委托 rich_text.has_content（本层语义：仅富文本文档参与判断）
    return is_rich_text_document(value) and rich_text_has_content(value)


def _translation_preview(value, limit: int = 80) -> str:
    # F12：薄委托 rich_text.plain_text_of（任意译文值 → 纯文本）
    return plain_text_of(value)[:limit]


def _region_render_value(region: TextBlock):
    if hasattr(region, 'get_translation_for_rendering'):
        return region.get_translation_for_rendering()
    return getattr(region, 'translation', '')


def _should_apply_default_english_line_break_method(region: TextBlock, config: Config = None) -> bool:
    if is_rich_text_document(_region_render_value(region)):
        return False
    if not isinstance(getattr(region, 'translation', ''), str):
        return False
    # 开关开启时，对所有语言生效（强制横排+气泡排版）
    render_cfg = getattr(config, 'render', None) if config is not None else None
    bubble_layout = bool(getattr(render_cfg, 'bubble_layout_english', False)) if render_cfg is not None else False
    if bubble_layout:
        return not _has_explicit_line_breaks(getattr(region, 'translation', ''))
    # 默认行为：仅对英文横排生效
    return (
        str(getattr(region, 'target_lang', '') or '').upper() == 'ENG'
        and _resolve_region_render_horizontal(region)
        and not _has_explicit_line_breaks(getattr(region, 'translation', ''))
    )


def _apply_default_english_case_preferences(region: TextBlock, config: Config = None) -> bool:
    if is_rich_text_document(_region_render_value(region)):
        return False
    if not isinstance(getattr(region, 'translation', ''), str):
        return False
    if str(getattr(region, 'target_lang', '') or '').upper() != 'ENG':
        return False
    if not _resolve_region_render_horizontal(region):
        return False

    render_cfg = getattr(config, 'render', None) if config is not None else None
    uppercase = bool(getattr(render_cfg, 'uppercase', False)) if render_cfg is not None else False
    lowercase = bool(getattr(render_cfg, 'lowercase', False)) if render_cfg is not None else False

    original_translation = str(getattr(region, 'translation', '') or '')
    updated_translation = original_translation
    if uppercase:
        updated_translation = original_translation.upper()
    elif lowercase:
        updated_translation = original_translation.lower()

    if updated_translation == original_translation:
        return False

    region.translation = updated_translation
    return True


def _apply_default_english_line_break_method(
    region: TextBlock,
    target_font_size: int,
    original_img: np.ndarray = None,
    config: Config = None,
) -> bool:
    if not _should_apply_default_english_line_break_method(region, config):
        return False

    # 开关开启时，强制横排
    render_cfg = getattr(config, 'render', None) if config is not None else None
    bubble_layout = bool(getattr(render_cfg, 'bubble_layout_english', False)) if render_cfg is not None else False
    if bubble_layout:
        # 强制设置为横排
        region._direction = 'h'

    applied = apply_manga2eng_line_breaks(
        region,
        original_img=original_img,
        seed_font_size=target_font_size,
        config=config,
        letter_spacing=_resolve_letter_spacing_multiplier(region, config),
    )
    if applied:
        logger.debug("[BUBBLE LAYOUT] Applied bubble-based line breaking (force horizontal)")
    return applied


def calc_text_block_dimensions(text: str, is_horizontal: bool, line_spacing: float = 1.0,
                                config: Config = None, target_lang: str = None,
                                font_size: int = BASE_FONT_SIZE, letter_spacing: float = 1.0) -> tuple:
    """
    按指定字号模拟渲染文本块，返回精确的像素尺寸

    复用后端渲染的尺寸计算逻辑，保证和实际渲染一致。

    Args:
        text: 文本内容。旧 [BR]/<br>/【BR】 会在入口层转换成 richtext.v1 段落。
        is_horizontal: True=横排，False=竖排
        line_spacing: 行间距倍率
        config: 配置对象
        target_lang: 目标语言

    Returns:
        (base_width, base_height, n_lines) - 基准尺寸和行/列数
    """
    base_font = max(1, int(font_size))
    if text_render.is_rich_text_document(text):
        if is_horizontal:
            return text_render.measure_rich_text_horizontal(
                base_font,
                text,
                line_spacing,
                config=config,
                letter_spacing=letter_spacing,
            )
        return text_render.measure_rich_text_vertical(
            base_font,
            text,
            line_spacing,
            config=config,
            letter_spacing=letter_spacing,
        )

    # 特殊符号规范化已外移到 text_replacements.yaml 规则层，渲染入口不再改写字符。
    text = text or ''
    # 处理 BR 标记
    text_for_calc = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', text, flags=re.IGNORECASE)

    if is_horizontal:
        normalized_lines = re.sub(r'\r\n?|\n', '\n', text_for_calc).split('\n')
        if any(line for line in normalized_lines):
            widths = [
                text_render.get_string_width(base_font, line_text, letter_spacing=letter_spacing)
                for line_text in normalized_lines
            ]
            spacing_y = text_render.calc_horizontal_line_spacing_px(base_font, line_spacing)
            # 测量链路直接按原始行文本量宽，避免英文断行器折叠连续空格。
            base_width = max(widths, default=0)
            base_height = base_font * len(normalized_lines) + spacing_y * max(0, len(normalized_lines) - 1)
            return base_width, base_height, len(normalized_lines)
    else:
        lines, heights, line_widths = text_render.calc_vertical_metrics(
            base_font, text_for_calc,
            max_height=99999, config=config, letter_spacing=letter_spacing
        )
        if heights:
            spacing_x = int(base_font * 0.2 * line_spacing)

            # 和后端渲染一致：sum(line_widths) + spacing
            base_width = sum(line_widths) + spacing_x * max(0, len(lines) - 1)
            base_height = max(heights)
            return base_width, base_height, len(lines)

    return 0, 0, 0


def calc_font_from_box(width: float, height: float, text: str, is_horizontal: bool,
                       line_spacing: float = 1.0, config: Config = None,
                       target_lang: str = None, letter_spacing: float = 1.0) -> int:
    """
    框 → 字体：基于真实测量结果二分搜索可容纳的最大字号

    Args:
        width: 框宽度（像素）
        height: 框高度（像素）
        text: 文本内容
        is_horizontal: True=横排，False=竖排
        line_spacing: 行间距倍率
        config: 配置对象
        target_lang: 目标语言

    Returns:
        能放入框内的最大字体大小（像素）
    """
    if width <= 0 or height <= 0:
        return 1

    if is_rich_text_document(text):
        if not _rich_text_has_content(text):
            return 1
        # F24：解析一次向下传实例，二分迭代内不再重复解析 dict
        text = ensure_rich_text_document(text)
    else:
        text = (text or '').strip()
        if not text:
            return 1

    def _fits(fs: int) -> bool:
        req_w, req_h, _, _ = calc_box_from_font(
            fs,
            text,
            is_horizontal,
            line_spacing,
            config,
            target_lang,
            center=None,
            angle=0,
            letter_spacing=letter_spacing
        )
        return req_w <= width and req_h <= height

    if not _fits(1):
        return 1

    lo = 1
    hi = max(1, int(min(width, height)))
    hi = min(hi, 8192)

    while hi < 8192 and _fits(hi):
        lo = hi
        hi = min(hi * 2, 8192)
        if hi == lo:
            break

    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _fits(mid):
            lo = mid
        else:
            hi = mid - 1

    return max(1, int(lo))


def _select_preserved_line_layout_font(
    base_font_size: int,
    width: float,
    height: float,
    text: str,
    is_horizontal: bool,
    line_spacing: float = 1.0,
    config: Config = None,
    target_lang: str = None,
    letter_spacing: float = 1.0,
) -> Tuple[int, int]:
    base_font = max(int(base_font_size), 1)
    line_font = max(
        int(
            calc_font_from_box(
                width=width,
                height=height,
                text=text,
                is_horizontal=is_horizontal,
                line_spacing=line_spacing,
                config=config,
                target_lang=target_lang,
                letter_spacing=letter_spacing,
            )
        ),
        1,
    )
    return max(base_font, line_font), line_font


def _solve_unified_no_br_layout(
    text: str,
    render_horizontally: bool,
    target_font_size: int,
    bubble_width: float,
    bubble_height: float,
    layout_min_font_size: int,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config = None,
    target_lang: str = None,
    max_font_size: Optional[int] = None,
) -> Tuple[str, int, float, float, int]:
    """Shared no-BR line-break solver for strict, smart_scaling, and balloon_fill."""
    safe_target_font_size = max(int(target_font_size), int(layout_min_font_size), 1)
    safe_max_font_size = max(
        safe_target_font_size,
        int(max_font_size) if isinstance(max_font_size, (int, float)) else safe_target_font_size,
    )
    safe_bubble_width = float(bubble_width) if isinstance(bubble_width, (int, float)) and bubble_width > 0 else 1.0
    safe_bubble_height = float(bubble_height) if isinstance(bubble_height, (int, float)) and bubble_height > 0 else 1.0

    if render_horizontally:
        total_width = text_render.get_string_width(
            safe_target_font_size,
            text,
            letter_spacing=letter_spacing_multiplier,
        )
        spacing_y = int(safe_target_font_size * 0.01 * line_spacing_multiplier)
        ratio = safe_bubble_width / safe_bubble_height if safe_bubble_height > 0 else 1.0

        a = safe_target_font_size + spacing_y
        b = -spacing_y
        c = -total_width / ratio if ratio > 0 else -total_width

        discriminant = b * b - 4 * a * c
        if discriminant >= 0 and a > 0:
            n_float = (-b + np.sqrt(discriminant)) / (2 * a)
            n_floor = max(1, int(np.floor(n_float)))
            n_ceil = max(1, int(np.ceil(n_float)))
        else:
            n_floor = n_ceil = 1

        def calc_max_font_horizontal(n: int, total_w: float, bw: float, bh: float, lsm: float, target_fs: int) -> int:
            height_factor = n + (n - 1) * 0.01 * lsm
            max_by_height = int(bh / height_factor) if height_factor > 0 else target_fs
            max_by_width = int(bw * n * target_fs / total_w) if total_w > 0 else target_fs
            return min(max_by_height, max_by_width)

        font_floor = calc_max_font_horizontal(
            n_floor, total_width, safe_bubble_width, safe_bubble_height, line_spacing_multiplier, safe_target_font_size
        )
        font_ceil = calc_max_font_horizontal(
            n_ceil, total_width, safe_bubble_width, safe_bubble_height, line_spacing_multiplier, safe_target_font_size
        )
    else:
        total_height = text_render.get_string_height(
            safe_target_font_size,
            text,
            letter_spacing=letter_spacing_multiplier,
        )
        spacing_x = int(safe_target_font_size * 0.2 * line_spacing_multiplier)
        ratio = safe_bubble_width / safe_bubble_height if safe_bubble_height > 0 else 1.0

        a = safe_target_font_size + spacing_x
        b = -spacing_x
        c = -total_height * ratio

        discriminant = b * b - 4 * a * c
        if discriminant >= 0 and a > 0:
            n_float = (-b + np.sqrt(discriminant)) / (2 * a)
            n_floor = max(1, int(np.floor(n_float)))
            n_ceil = max(1, int(np.ceil(n_float)))
        else:
            n_floor = n_ceil = 1

        def calc_max_font_vertical(n: int, total_h: float, bw: float, bh: float, lsm: float, target_fs: int) -> int:
            width_factor = n + (n - 1) * 0.2 * lsm
            max_by_width = int(bw / width_factor) if width_factor > 0 else target_fs
            max_by_height = int(bh * n * target_fs / total_h) if total_h > 0 else target_fs
            return min(max_by_width, max_by_height)

        font_floor = calc_max_font_vertical(
            n_floor, total_height, safe_bubble_width, safe_bubble_height, line_spacing_multiplier, safe_target_font_size
        )
        font_ceil = calc_max_font_vertical(
            n_ceil, total_height, safe_bubble_width, safe_bubble_height, line_spacing_multiplier, safe_target_font_size
        )

    if font_floor >= font_ceil:
        seed_segments = n_floor
        seed_font_size = font_floor
    else:
        seed_segments = n_ceil
        seed_font_size = font_ceil

    seed_font_size = min(seed_font_size, safe_target_font_size)
    seed_font_size = max(seed_font_size, int(layout_min_font_size), 1)

    no_br_result = solve_no_br_layout(
        text=text,
        horizontal=render_horizontally,
        seed_segments=seed_segments,
        seed_font_size=seed_font_size,
        bubble_width=safe_bubble_width,
        bubble_height=safe_bubble_height,
        min_font_size=layout_min_font_size,
        max_font_size=safe_max_font_size,
        line_spacing_multiplier=line_spacing_multiplier,
        letter_spacing_multiplier=letter_spacing_multiplier,
        target_lang=target_lang,
        config=config,
        adjust_font_size=False,
        debug_context="ocr_box",
    )
    text_with_br = no_br_result.text_with_br
    layout_font_size, _ = _select_preserved_line_layout_font(
        base_font_size=seed_font_size,
        width=safe_bubble_width,
        height=safe_bubble_height,
        text=text_with_br,
        is_horizontal=render_horizontally,
        line_spacing=line_spacing_multiplier,
        config=config,
        target_lang=target_lang,
        letter_spacing=letter_spacing_multiplier,
    )
    layout_font_size = max(int(layout_font_size), int(layout_min_font_size), 1)
    required_width, required_height, n_segments, _ = calc_box_from_font(
        layout_font_size,
        text_with_br,
        render_horizontally,
        line_spacing_multiplier,
        config,
        target_lang,
        center=None,
        angle=0,
        letter_spacing=letter_spacing_multiplier,
    )
    return text_with_br, layout_font_size, required_width, required_height, n_segments


def calc_text_block_metrics(text, is_horizontal: bool, line_spacing: float,
                            config: Config = None, target_lang: str = None,
                            font_size: int = None, letter_spacing: float = 1.0) -> tuple:
    """尺寸 + 正文中心：在 calc_text_block_dimensions 基础上追加正文框中心点。

    Returns:
        (base_width, base_height, n_lines, body_center)
        body_center 为正文框中心在渲染框内的坐标（相对渲染框左上角）。
        纯文本没有框外装饰，恒为渲染框正中心。
    """
    base_font = max(1, int(font_size))
    if text_render.is_rich_text_document(text):
        metrics = text_render.measure_rich_text_metrics(
            base_font, text, is_horizontal, line_spacing,
            config=config, letter_spacing=letter_spacing,
        )
        return metrics['width'], metrics['height'], metrics['n_lines'], metrics['body_center']

    base_w, base_h, n_lines = calc_text_block_dimensions(
        text, is_horizontal, line_spacing, config, target_lang,
        font_size=base_font, letter_spacing=letter_spacing,
    )
    return base_w, base_h, n_lines, (base_w / 2.0, base_h / 2.0)


def calc_box_from_font(font_size: int, text: str, is_horizontal: bool,
                       line_spacing: float = 1.0, config: Config = None,
                       target_lang: str = None, center: tuple = None,
                       angle: float = 0, letter_spacing: float = 1.0) -> tuple:
    """
    字体 → 框：直接按目标字号测量文本像素尺寸，并给出正文中心点

    Args:
        font_size: 字体大小（像素）
        text: 文本内容
        is_horizontal: True=横排，False=竖排
        line_spacing: 行间距倍率
        config: 配置对象
        target_lang: 目标语言
        center: 中心点坐标 (cx, cy)，如果提供则返回 dst_points
        angle: 旋转角度（度），仅当 center 不为 None 时使用

    Returns:
        center 为 None: (required_width, required_height, n_lines, body_center)
            body_center = 正文框中心在渲染框内的坐标（相对渲染框左上角）。
            纯文本恒为 (w/2, h/2)；富文本会因首行注音/末行着重号（横排）
            或首列注音（竖排）偏离渲染框正中心。
        center 不为 None: (dst_points, body_center_world)
            dst_points shape (1, 4, 2)，渲染框以 center 为正中心；
            body_center_world = 正文中心的世界坐标（已随 angle 旋转）。
            文本为空时返回 (None, None)。
    """
    font_size = max(1, int(font_size))
    base_w, base_h, n_lines, (body_x, body_y) = calc_text_block_metrics(
        text, is_horizontal, line_spacing, config, target_lang,
        font_size=font_size, letter_spacing=letter_spacing,
    )

    if base_w <= 0 or base_h <= 0:
        if center is not None:
            return None, None
        return 0, 0, 0, (0.0, 0.0)

    # 直接按目标字号测量，不再做基准字号线性缩放
    req_width = math.ceil(base_w)
    req_height = math.ceil(base_h)
    body_x = float(body_x)
    body_y = float(body_y)

    # 计入效果边距，避免“字号不变但框太小导致视觉缩字”。
    # 边距四边对称，正文中心随左上角同步平移。
    effect_padding = _estimate_effect_padding(font_size, config)
    if effect_padding > 0.0:
        pad_total = int(effect_padding * 2.0)
        req_width += pad_total
        req_height += pad_total
        body_x += pad_total / 2.0
        body_y += pad_total / 2.0

    # 如果没有提供中心点，返回尺寸和正文中心（框内坐标）
    if center is None:
        return req_width, req_height, n_lines, (body_x, body_y)

    # 提供了中心点，构建 dst_points
    cx, cy = center
    half_w = req_width / 2
    half_h = req_height / 2

    # 未旋转的矩形四个角点
    unrotated_points = np.array([
        [cx - half_w, cy - half_h],
        [cx + half_w, cy - half_h],
        [cx + half_w, cy + half_h],
        [cx - half_w, cy + half_h]
    ], dtype=np.float32)
    # 正文中心（未旋转世界坐标）：框左上角 + 框内坐标
    unrotated_body = np.array([
        [cx - half_w + body_x, cy - half_h + body_y],
    ] * 4, dtype=np.float32)

    # 应用旋转（正文点走与角点完全相同的变换，保证坐标约定一致）
    if angle != 0:
        dst_points = rotate_polygons(
            center, unrotated_points.reshape(1, -1),
            -angle, to_int=False
        ).reshape(-1, 4, 2)
        rotated_body = rotate_polygons(
            center, unrotated_body.reshape(1, -1),
            -angle, to_int=False
        ).reshape(-1, 4, 2)
        body_world = (float(rotated_body[0, 0, 0]), float(rotated_body[0, 0, 1]))
    else:
        dst_points = unrotated_points.reshape(-1, 4, 2)
        body_world = (float(unrotated_body[0, 0]), float(unrotated_body[0, 1]))

    return dst_points, body_world

def find_largest_inscribed_rect(mask: np.ndarray) -> tuple:
    """
    Find the largest axis-aligned rectangle that fits inside the mask.
    Uses distance transform to find a good inscribed rectangle.
    
    Returns:
        (x, y, width, height) of the largest inscribed rectangle
    """
    if mask.sum() == 0:
        return 0, 0, 0, 0
    
    # Distance transform to find distances from edges
    dist_transform = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    
    # Find the maximum distance (center of largest inscribed circle)
    _, max_dist, _, max_loc = cv2.minMaxLoc(dist_transform)
    center_x, center_y = max_loc
    
    h, w = mask.shape
    
    # Start with a rectangle based on distance transform
    # Use 85% of max distance as initial radius for conservative estimate
    radius = int(max_dist * 0.85)
    
    x1 = max(0, center_x - radius)
    y1 = max(0, center_y - radius)
    x2 = min(w, center_x + radius)
    y2 = min(h, center_y + radius)
    
    # Expand rectangle while it stays inside the mask
    # Try to expand in all four directions
    max_iterations = 100
    improved = True
    iteration = 0
    
    while improved and iteration < max_iterations:
        improved = False
        iteration += 1
        
        # Try expanding left
        if x1 > 0 and np.all(mask[y1:y2, x1-1] > 0):
            x1 -= 1
            improved = True
        
        # Try expanding right
        if x2 < w and np.all(mask[y1:y2, x2] > 0):
            x2 += 1
            improved = True
        
        # Try expanding up
        if y1 > 0 and np.all(mask[y1-1, x1:x2] > 0):
            y1 -= 1
            improved = True
        
        # Try expanding down
        if y2 < h and np.all(mask[y2, x1:x2] > 0):
            y2 += 1
            improved = True
    
    rect_width = x2 - x1
    rect_height = y2 - y1
    
    if rect_width <= 0 or rect_height <= 0:
        # Fallback to a small rectangle at center
        return max(0, center_x - 5), max(0, center_y - 5), 10, 10
    
    return x1, y1, rect_width, rect_height

def parse_font_paths(path: str, default: List[str] = None) -> List[str]:
    if path:
        parsed = path.split(',')
        parsed = list(filter(lambda p: os.path.isfile(p), parsed))
    else:
        parsed = default or []
    return parsed

def count_text_length(text: str) -> float:
    """Calculate text length, treating っッぁぃぅぇぉ as 0.5 characters"""
    half_width_chars = 'っッぁぃぅぇぉ'  
    length = 0.0
    for char in text.strip():
        if char in half_width_chars:
            length += 0.5
        else:
            length += 1.0
    return length

def generate_line_break_combinations(text: str):
    """
    Generate line break combinations using a smart pruning strategy.
    
    Strategy:
    1. For small n (<=10): Use exhaustive search (original algorithm)
    2. For medium n (11-20): Use beam search with top-k pruning
    3. For large n (>20): Use greedy + sampling strategy
    
    This balances quality and performance.
    """
    import itertools
    import random
    
    # Standardize all break markers to [BR] (including full-width brackets)
    text = re.sub(r'\s*(<br>|【BR】)\s*', '[BR]', text, flags=re.IGNORECASE)
    
    # Find all [BR] positions
    breaks = []
    pattern = r'\[BR\]'
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        breaks.append((match.start(), match.end()))
    
    if not breaks:
        return [(text, "no_breaks", None)]
    
    n_breaks = len(breaks)
    combinations = []
    
    # Strategy 1: Small n - exhaustive search (original algorithm)
    if n_breaks <= 10:
        logger.debug(f"[OPTIMIZE_LINE_BREAKS] Using exhaustive search (n={n_breaks})")
        
        # Add original (keep all breaks)
        combinations.append((text, "all_breaks", None))
        
        # Generate all possible combinations
        for r in range(1, n_breaks + 1):
            for combo in itertools.combinations(range(n_breaks), r):
                segments = re.split(pattern, text, flags=re.IGNORECASE)
                
                if 0 in combo and len(segments[0].strip()) <= 2:
                    combinations.append((None, f"remove_{combo}", "first_segment_too_short"))
                    continue
                
                modified_text = text
                for idx in sorted(combo, reverse=True):
                    start, end = breaks[idx]
                    modified_text = modified_text[:start] + modified_text[end:]
                
                combinations.append((modified_text, f"remove_{combo}", None))
        
        return combinations
    
    # Strategy 2: Medium n - beam search with sampling
    elif n_breaks <= 20:
        logger.debug(f"[OPTIMIZE_LINE_BREAKS] Using beam search (n={n_breaks})")
        
        combinations.append((text, "all_breaks", None))
        
        # Sample combinations: all singles, all pairs, some triples, and remove_all
        # Singles: remove each break individually
        for i in range(n_breaks):
            if i == 0:
                segments = re.split(pattern, text, flags=re.IGNORECASE)
                if len(segments[0].strip()) <= 2:
                    combinations.append((None, f"remove_({i},)", "first_segment_too_short"))
                    continue
            
            modified_text = text
            start, end = breaks[i]
            modified_text = modified_text[:start] + modified_text[end:]
            combinations.append((modified_text, f"remove_({i},)", None))
        
        # Pairs: remove adjacent breaks
        for i in range(n_breaks - 1):
            if i == 0:
                segments = re.split(pattern, text, flags=re.IGNORECASE)
                if len(segments[0].strip()) <= 2:
                    continue
            
            modified_text = text
            for idx in [i+1, i]:
                start, end = breaks[idx]
                if idx == i+1:
                    modified_text = modified_text[:start] + modified_text[end:]
                else:
                    # Recalculate position after first removal
                    offset = breaks[i+1][1] - breaks[i+1][0]
                    start -= offset
                    end -= offset
                    modified_text = modified_text[:start] + modified_text[end:]
            
            combinations.append((modified_text, f"remove_({i},{i+1})", None))
        
        # Sample some triples (every 3rd combination)
        for r in [3]:
            sampled = list(itertools.combinations(range(n_breaks), r))
            # Sample at most 20 combinations
            if len(sampled) > 20:
                sampled = random.sample(sampled, 20)
            
            for combo in sampled:
                if 0 in combo:
                    segments = re.split(pattern, text, flags=re.IGNORECASE)
                    if len(segments[0].strip()) <= 2:
                        continue
                
                modified_text = text
                for idx in sorted(combo, reverse=True):
                    start, end = breaks[idx]
                    modified_text = modified_text[:start] + modified_text[end:]
                
                combinations.append((modified_text, f"remove_{combo}", None))
        
        # Remove all
        modified_text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        combinations.append((modified_text, "remove_all", None))
        
        return combinations
    
    # Strategy 3: Large n - greedy + sampling
    else:
        logger.debug(f"[OPTIMIZE_LINE_BREAKS] Using greedy+sampling (n={n_breaks})")
        
        combinations.append((text, "all_breaks", None))
        
        # Singles: sample every 2nd break
        for i in range(0, n_breaks, 2):
            if i == 0:
                segments = re.split(pattern, text, flags=re.IGNORECASE)
                if len(segments[0].strip()) <= 2:
                    continue
            
            modified_text = text
            start, end = breaks[i]
            modified_text = modified_text[:start] + modified_text[end:]
            combinations.append((modified_text, f"remove_({i},)", None))
        
        # Pairs: sample every 3rd adjacent pair
        for i in range(0, n_breaks - 1, 3):
            if i == 0:
                segments = re.split(pattern, text, flags=re.IGNORECASE)
                if len(segments[0].strip()) <= 2:
                    continue
            
            modified_text = text
            for idx in [i+1, i]:
                start, end = breaks[idx]
                if idx == i+1:
                    modified_text = modified_text[:start] + modified_text[end:]
                else:
                    offset = breaks[i+1][1] - breaks[i+1][0]
                    start -= offset
                    end -= offset
                    modified_text = modified_text[:start] + modified_text[end:]
            
            combinations.append((modified_text, f"remove_({i},{i+1})", None))
        
        # Remove all
        modified_text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        combinations.append((modified_text, "remove_all", None))
        
        return combinations

def calculate_uniformity(lines: List[str]) -> float:
    """
    Calculate uniformity score for line lengths.
    Lower score = more uniform (better).
    Uses coefficient of variation (std/mean).
    """
    if not lines or len(lines) <= 1:
        return 0.0
    
    lengths = [len(line.strip()) for line in lines]
    if not lengths or sum(lengths) == 0:
        return float('inf')
    
    mean_length = np.mean(lengths)
    std_length = np.std(lengths)
    
    # Coefficient of variation
    cv = std_length / mean_length if mean_length > 0 else float('inf')
    return cv

def optimize_line_breaks_for_region(region: TextBlock, config: Config, target_font_size: int, bubble_width: float, bubble_height: float):
    """
    Optimize line breaks for a single region by testing all combinations.
    Returns the best text variant and the font size it achieves.
    """
    original_translation = region.translation
    combinations = generate_line_break_combinations(original_translation)
    
    best_text = original_translation
    best_font_size = 0
    best_uniformity = float('inf')
    
    layout_mode = config.render.layout_mode if config and hasattr(config.render, 'layout_mode') else 'default'
    logger.debug(f"[OPTIMIZE_LINE_BREAKS] Testing {len(combinations)} combinations, layout_mode={layout_mode}")
    render_horizontally = _resolve_region_render_horizontal(region)
    
    for text_variant, combo_desc, skip_reason in combinations:
        if skip_reason:
            logger.debug(f"[OPTIMIZE_LINE_BREAKS] Skipping {combo_desc}: {skip_reason}")
            continue
        
        # Convert [BR] to \n for calculation
        text_for_calc = re.sub(r'\s*\[BR\]\s*', '\n', text_variant, flags=re.IGNORECASE)
        
        # 严格智能缩放模式：如果去掉所有断句（无\n），会导致文本框扩大，淘汰此方案
        strict_smart_scaling = getattr(config.render, 'strict_smart_scaling', False) if config and hasattr(config, 'render') else False
        if layout_mode == 'smart_scaling' and strict_smart_scaling:
            if '\n' not in text_for_calc:
                logger.debug(f"[OPTIMIZE_LINE_BREAKS] Skipping {combo_desc}: 严格智能缩放模式下无断句会扩大文本框")
                continue
        
        try:
            line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
            letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
            # Calculate required dimensions
            if render_horizontally:
                lines, widths = text_render.calc_horizontal(
                    target_font_size, text_for_calc, 
                    max_width=99999, max_height=99999, 
                    language=region.target_lang,
                    letter_spacing=letter_spacing_multiplier
                )
                if widths:
                    spacing_y = int(target_font_size * 0.01 * line_spacing_multiplier)
                    required_width = max(widths)
                    required_height = target_font_size * len(lines) + spacing_y * max(0, len(lines) - 1)
                else:
                    continue
            else:  # Vertical
                lines, heights, line_widths = text_render.calc_vertical_metrics(
                    target_font_size,
                    text_for_calc,
                    max_height=99999,
                    config=config,
                    letter_spacing=letter_spacing_multiplier,
                )
                if heights:
                    spacing_x = int(target_font_size * 0.2 * line_spacing_multiplier)
                    required_height = max(heights)
                    required_width = sum(line_widths) + spacing_x * max(0, len(lines) - 1)
                else:
                    continue
            
            # Calculate how much the text fits in the bubble
            # Larger font size is better
            width_ratio = bubble_width / required_width if required_width > 0 else 1.0
            height_ratio = bubble_height / required_height if required_height > 0 else 1.0
            fit_ratio = min(width_ratio, height_ratio)
            
            # Calculate effective font size for this combination
            effective_font_size = target_font_size * fit_ratio
            
            # Calculate uniformity
            uniformity = calculate_uniformity(lines)
            
            logger.debug(f"[OPTIMIZE_LINE_BREAKS] {combo_desc}: font_size={effective_font_size:.1f}, uniformity={uniformity:.3f}")
            
            # Choose the best: prioritize font size, then uniformity
            is_better = False
            if effective_font_size > best_font_size + 0.5:  # Significantly larger font
                is_better = True
            elif abs(effective_font_size - best_font_size) <= 0.5:  # Similar font size
                if uniformity < best_uniformity:  # Better uniformity
                    is_better = True
            
            if is_better:
                best_text = text_variant
                best_font_size = effective_font_size
                best_uniformity = uniformity
                logger.debug(f"[OPTIMIZE_LINE_BREAKS] New best: {combo_desc}")
        
        except Exception as e:
            logger.warning(f"[OPTIMIZE_LINE_BREAKS] Error evaluating {combo_desc}: {e}")
            continue
    
    # Compare and log optimization results
    # 使用统一的正则匹配所有BR变体进行统计
    br_pattern = r'(\[BR\]|【BR】|<br>)'
    original_br_count = len(re.findall(br_pattern, original_translation, flags=re.IGNORECASE))
    optimized_br_count = len(re.findall(br_pattern, best_text, flags=re.IGNORECASE))
    
    # 只有当BR数量真的改变时才应用优化
    if optimized_br_count != original_br_count:
        br_change = optimized_br_count - original_br_count
        if br_change > 0:
            change_desc = f"增加了 {br_change}"
        elif br_change < 0:
            change_desc = f"去掉了 {-br_change}"
        else:
            change_desc = "调整了位置"
        logger.debug(f"[AI断句自动扩大文字] 优化完成：{change_desc} 个换行符，字体大小提升至 {best_font_size:.1f}px")
        logger.debug(f"[AI断句自动扩大文字] 原文: {original_translation}")
        logger.debug(f"[AI断句自动扩大文字] 优化后: {best_text}")
        return best_text, best_font_size
    else:
        logger.debug(f"[AI断句自动扩大文字] 未进行优化：保持原断句方案最佳，字体大小 {best_font_size:.1f}px")
        # 即使数量相同，也返回标准化后的文本（全角变半角）
        return best_text, best_font_size

def _resolve_region_render_horizontal(region: TextBlock) -> bool:
    forced_direction = region._direction if hasattr(region, '_direction') else region.direction
    if forced_direction != 'auto':
        if forced_direction in ['horizontal', 'h']:
            return True
        if forced_direction in ['vertical', 'v']:
            return False
    return region.horizontal


def _polygon_fully_inside_mask(points: np.ndarray, bubble_mask: np.ndarray) -> bool:
    if points is None or points.size == 0 or bubble_mask is None:
        return False
    h, w = bubble_mask.shape[:2]
    if h <= 0 or w <= 0:
        return False

    pts = np.asarray(points, dtype=np.int32)
    if pts.ndim != 2 or pts.shape[0] < 3:
        return False
    pts[:, 0] = np.clip(pts[:, 0], 0, max(w - 1, 0))
    pts[:, 1] = np.clip(pts[:, 1], 0, max(h - 1, 0))

    poly_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(poly_mask, [pts], 255)
    poly_pixels = poly_mask > 0
    if not np.any(poly_pixels):
        return False
    return bool(np.all(bubble_mask[poly_pixels] > 0))


def _region_lines_fully_inside_mask(region: TextBlock, bubble_mask: np.ndarray) -> bool:
    lines = np.asarray(region.lines)
    if lines.size == 0:
        return False

    if lines.ndim == 2 and lines.shape[1] == 8:
        polys = lines.reshape(-1, 4, 2)
    elif lines.ndim == 3 and lines.shape[1:] == (4, 2):
        polys = lines
    else:
        return False

    for poly in polys:
        if not _polygon_fully_inside_mask(poly, bubble_mask):
            return False
    return True


def _extract_region_polygons(region: TextBlock) -> List[np.ndarray]:
    lines = np.asarray(region.lines)
    polygons: List[np.ndarray] = []

    if lines.size > 0:
        if lines.ndim == 2 and lines.shape[1] == 8:
            polygons.extend(np.asarray(poly, dtype=np.float32) for poly in lines.reshape(-1, 4, 2))
        elif lines.ndim == 3 and lines.shape[1:] == (4, 2):
            polygons.extend(np.asarray(poly, dtype=np.float32) for poly in lines)

    if not polygons:
        rect = np.asarray(region.min_rect)
        if rect.ndim == 3 and rect.shape[0] > 0 and rect.shape[1:] == (4, 2):
            polygons.append(np.asarray(rect[0], dtype=np.float32))
        elif rect.ndim == 2 and rect.shape == (4, 2):
            polygons.append(np.asarray(rect, dtype=np.float32))

    return polygons


def _build_region_reference_mask(
    region: TextBlock,
    bubble_mask: np.ndarray,
    label_map: Optional[np.ndarray],
) -> np.ndarray:
    if bubble_mask is None or np.count_nonzero(bubble_mask) == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    h, w = bubble_mask.shape[:2]
    region_mask = np.zeros((h, w), dtype=np.uint8)
    for poly in _extract_region_polygons(region):
        pts = np.asarray(poly, dtype=np.int32)
        if pts.ndim != 2 or pts.shape[0] < 3:
            continue
        pts[:, 0] = np.clip(pts[:, 0], 0, max(w - 1, 0))
        pts[:, 1] = np.clip(pts[:, 1], 0, max(h - 1, 0))
        cv2.fillPoly(region_mask, [pts], 255)

    overlap_pixels = (region_mask > 0) & (bubble_mask > 0)
    if not np.any(overlap_pixels):
        return np.zeros((h, w), dtype=np.uint8)

    if label_map is None or label_map.shape[:2] != bubble_mask.shape[:2]:
        return np.where(bubble_mask > 0, 255, 0).astype(np.uint8)

    labels = np.unique(label_map[overlap_pixels])
    labels = labels[labels > 0]
    if labels.size == 0:
        return np.zeros((h, w), dtype=np.uint8)

    selected = np.isin(label_map, labels)
    return np.where(selected, 255, 0).astype(np.uint8)


def _resolve_line_spacing_multiplier(region: TextBlock, config: Config) -> float:
    region_line_spacing = getattr(region, 'line_spacing', None)
    if isinstance(region_line_spacing, (int, float)) and region_line_spacing > 0:
        return float(region_line_spacing)
    cfg_val = config.render.line_spacing if config and hasattr(config, 'render') else None
    if isinstance(cfg_val, (int, float)) and cfg_val > 0:
        return float(cfg_val)
    return 1.0


def _resolve_letter_spacing_multiplier(region: TextBlock, config: Config) -> float:
    region_letter_spacing = getattr(region, 'letter_spacing', None)
    if isinstance(region_letter_spacing, (int, float)) and region_letter_spacing > 0:
        return float(region_letter_spacing)
    cfg_val = config.render.letter_spacing if config and hasattr(config, 'render') else None
    if isinstance(cfg_val, (int, float)) and cfg_val > 0:
        return float(cfg_val)
    return 1.0


def _resolve_configured_min_font_size(config: Config) -> int:
    render_cfg = getattr(config, 'render', None) if config is not None else None
    raw_min_font_size = getattr(render_cfg, 'font_size_minimum', 0) if render_cfg is not None else 0
    if isinstance(raw_min_font_size, (int, float)) and raw_min_font_size > 0:
        return max(int(raw_min_font_size), 1)
    return 0


def _resolve_configured_fixed_font_size(config: Config) -> int:
    render_cfg = getattr(config, 'render', None) if config is not None else None
    raw_font_size = getattr(render_cfg, 'font_size', None) if render_cfg is not None else None
    if isinstance(raw_font_size, (int, float)) and raw_font_size > 0:
        return max(int(raw_font_size), 1)
    return 0


def _resolve_initial_layout_font_size(region: TextBlock, img: np.ndarray, config: Config) -> int:
    region_font_size = getattr(region, 'font_size', 0)
    if isinstance(region_font_size, (int, float)) and region_font_size > 0:
        return max(int(region_font_size), 1)

    if img is not None and hasattr(img, 'shape') and len(img.shape) >= 2:
        return max(round((img.shape[0] + img.shape[1]) / 200), 1)
    return 24


def _apply_final_font_constraints(layout_font_size: int, config: Config) -> int:
    final_font_size = max(int(layout_font_size), 1)
    render_cfg = getattr(config, 'render', None) if config is not None else None

    configured_font_size = _resolve_configured_fixed_font_size(config)
    if configured_font_size > 0:
        final_font_size = configured_font_size

    font_size_offset = getattr(render_cfg, 'font_size_offset', 0) if render_cfg is not None else 0
    if isinstance(font_size_offset, (int, float)) and font_size_offset != 0:
        final_font_size = max(int(final_font_size + float(font_size_offset)), 1)

    font_scale_ratio = getattr(render_cfg, 'font_scale_ratio', 1.0) if render_cfg is not None else 1.0
    if not isinstance(font_scale_ratio, (int, float)) or font_scale_ratio <= 0:
        font_scale_ratio = 1.0
    final_font_size = max(int(final_font_size * float(font_scale_ratio)), 1)

    configured_min_font_size = _resolve_configured_min_font_size(config)
    if configured_min_font_size > 0:
        final_font_size = max(final_font_size, configured_min_font_size)

    max_font_size = getattr(render_cfg, 'max_font_size', 0) if render_cfg is not None else 0
    if isinstance(max_font_size, (int, float)) and max_font_size > 0:
        final_font_size = min(final_font_size, int(max_font_size))

    return max(final_font_size, 1)


def _compute_top_aligned_center(region: 'TextBlock', text_height: float) -> tuple:
    """Shift center toward bubble top so text is top-aligned within the bubble."""
    pts = region.min_rect  # (1, 4, 2)
    mid = (pts[:, [1, 2, 3, 0]] + pts) / 2
    top_mid = mid[0, 0]
    bot_mid = mid[0, 2]
    bubble_h = float(np.linalg.norm(bot_mid - top_mid))
    if bubble_h <= 0 or text_height >= bubble_h:
        return tuple(region.center)
    up = (top_mid - bot_mid) / bubble_h
    shift = (bubble_h - text_height) / 2.0
    nc = np.array(region.center, dtype=float) + shift * up
    return (float(nc[0]), float(nc[1]))


def _resolve_layout_anchor_mode(*, apply_bubble_centering: bool, skip_font_scaling: bool = False) -> str:
    """统一中心锚点策略。

    - skip_font_scaling: 编辑器授权布局——region.center 是编辑器白框（渲染框）
      中心，按"渲染框中心"语义摆放（center_box，不做正文平移），与编辑器
      预览逐像素一致
    - 正常渲染: 锚点由管线自己计算，语义是"正文中心该在哪"——气泡内居中
      命中时 center，否则 top
    """
    if skip_font_scaling:
        return 'center_box'
    return 'center' if apply_bubble_centering else 'top'


def _resolve_region_layout_center(
    region: TextBlock,
    font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
    anchor_mode: str = 'top',
    render_value=None,
) -> tuple:
    if anchor_mode in ('center', 'center_box'):
        return tuple(region.center)
    if anchor_mode != 'top':
        raise ValueError(f"Unsupported anchor_mode: {anchor_mode!r}")

    if render_value is None:
        render_value = _region_render_value(region)
    _, req_h, _, (_, body_y) = calc_box_from_font(
        int(max(font_size, 1)),
        render_value,
        render_horizontally,
        line_spacing_multiplier,
        config,
        region.target_lang,
        letter_spacing=letter_spacing_multiplier,
    )
    # 上对齐用正文本体高度（正文中心到底边的两倍），把框外注音/着重号排除，
    # 这样正文顶边贴气泡顶边，而不是让注音顶边贴气泡。
    body_height = 2.0 * (req_h - body_y)
    return _compute_top_aligned_center(region, body_height)


def _calc_region_dst_points_for_font(
    region: TextBlock,
    font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
    anchor_mode: str = 'top',
) -> Optional[np.ndarray]:
    # F24：富文本渲染值在此解析一次；锚点计算与 dst 计算（以及掩码二分的
    # 每一步）复用同一实例，不再重复解析 dict。
    render_value = _region_render_value(region)
    if is_rich_text_document(render_value):
        render_value = ensure_rich_text_document(render_value)
    # anchor 是“正文中心”应落到的世界坐标（纯文本时正文中心即框中心）。
    anchor = _resolve_region_layout_center(
        region=region,
        font_size=font_size,
        render_horizontally=render_horizontally,
        line_spacing_multiplier=line_spacing_multiplier,
        letter_spacing_multiplier=letter_spacing_multiplier,
        config=config,
        anchor_mode=anchor_mode,
        render_value=render_value,
    )
    dst_points, body_world = calc_box_from_font(
        int(max(font_size, 1)),
        render_value,
        render_horizontally,
        line_spacing_multiplier,
        config,
        region.target_lang,
        center=anchor,
        angle=region.angle,
        letter_spacing=letter_spacing_multiplier,
    )
    if dst_points is None:
        return None
    # calc_box_from_font 把“渲染框中心”放在 anchor。管线自算锚点（top/center）
    # 的语义是“正文中心该在哪”：平移整框，使正文中心落在 anchor——纯文本
    # body_world==anchor、delta=0，与旧行为完全一致；富文本则把注音/着重号
    # 挤到框外，正文本体锚定不动。center_box（编辑器授权中心）保持渲染框
    # 中心语义，不平移，与编辑器预览对齐。
    if anchor_mode == 'center_box':
        return dst_points
    delta_x = float(anchor[0]) - float(body_world[0])
    delta_y = float(anchor[1]) - float(body_world[1])
    if delta_x or delta_y:
        dst_points = dst_points + np.array([delta_x, delta_y], dtype=dst_points.dtype)
    return dst_points


def _font_size_fits_bubble_mask(
    region: TextBlock,
    font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
    bubble_mask: np.ndarray,
    anchor_mode: str = 'top',
) -> Tuple[bool, Optional[np.ndarray]]:
    dst_points = _calc_region_dst_points_for_font(
        region=region,
        font_size=font_size,
        render_horizontally=render_horizontally,
        line_spacing_multiplier=line_spacing_multiplier,
        letter_spacing_multiplier=letter_spacing_multiplier,
        config=config,
        anchor_mode=anchor_mode,
    )
    if dst_points is None or dst_points.size == 0:
        return False, None
    fits = _polygon_fully_inside_mask(np.asarray(dst_points[0]), bubble_mask)
    return fits, dst_points


def _binary_search_font_for_bubble_mask(
    region: TextBlock,
    start_font_size: int,
    min_font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
    bubble_mask: np.ndarray,
    anchor_mode: str = 'top',
) -> Tuple[Optional[int], Optional[np.ndarray]]:
    lo = max(int(min_font_size), 1)
    hi = max(int(start_font_size), lo)
    best_font: Optional[int] = None
    best_dst_points: Optional[np.ndarray] = None

    while lo <= hi:
        mid = (lo + hi) // 2
        fits, dst_points = _font_size_fits_bubble_mask(
            region=region,
            font_size=mid,
            render_horizontally=render_horizontally,
            line_spacing_multiplier=line_spacing_multiplier,
            letter_spacing_multiplier=letter_spacing_multiplier,
            config=config,
            bubble_mask=bubble_mask,
            anchor_mode=anchor_mode,
        )
        if fits:
            best_font = mid
            best_dst_points = dst_points
            lo = mid + 1
        else:
            hi = mid - 1

    return best_font, best_dst_points


def resize_regions_to_font_size(
    img: np.ndarray,
    text_regions: List['TextBlock'],
    config: Config,
    original_img: np.ndarray = None,
    return_debug_img: bool = False,
    skip_font_scaling: bool = False,
    skip_text_replacements: bool = False,
):
    """
    Resize text regions based on layout mode.

    Args:
        return_debug_img: If True, returns (dst_points_list, debug_img) for balloon_fill mode
        skip_font_scaling: If True, skip font scaling algorithm and use font_size from region directly (for load_text mode)
    """
    mode = config.render.layout_mode
    if (
        mode == 'balloon_fill'
        and return_debug_img
        and bool(getattr(config.render, 'semantic_linebreak', False))
    ):
        config._chinese_linebreak_debug_records = []
    
    logger.debug(f"[RESIZE] 开始处理 {len(text_regions)} 个区域")

    # Prepare debug image for balloon_fill mode (only when requested)
    debug_img = None
    if mode == 'balloon_fill' and original_img is not None and return_debug_img:
        # OpenCV 绘制 API 使用 BGR 颜色；调试图统一转为 BGR，避免颜色对不上
        debug_img = cv2.cvtColor(original_img, cv2.COLOR_RGB2BGR)
        logger.debug("Created debug image for balloon_fill visualization")

    balloon_fill_mask = None
    balloon_fill_label_map = None
    if mode == 'balloon_fill' and original_img is not None:
        try:
            model_result = get_cached_bubbles_with_mangalens(original_img, return_annotated=False, verbose=False)
            if model_result is None:
                logger.warning("balloon_fill bubble cache miss, skip global bubble mask")
                balloon_fill_mask = np.zeros(original_img.shape[:2], dtype=np.uint8)
            else:
                balloon_fill_mask = build_bubble_mask_from_mangalens_result(model_result, original_img.shape[:2])
                mask_pixels = int(np.count_nonzero(balloon_fill_mask))
                detected = len(model_result.detections)
                logger.debug(
                    f"balloon_fill model mask prepared from cache: detections={detected}, mask_pixels={mask_pixels}"
                )
                if mask_pixels == 0 and debug_img is not None:
                    logger.warning("balloon_fill global bubble mask is empty (mask_pixels=0), blue overlay will not be visible")
                if mask_pixels > 0:
                    _, balloon_fill_label_map = cv2.connectedComponents(
                        np.where(balloon_fill_mask > 0, 1, 0).astype(np.uint8),
                        connectivity=8,
                    )
                    if debug_img is not None:
                        # 在调试图上渲染“蓝色蒙版区域”（半透明填充）+ 蓝色边界，提升可见性
                        mask_u8 = np.where(balloon_fill_mask > 0, 255, 0).astype(np.uint8)
                        mask_pixels_idx = mask_u8 > 0
                        if np.any(mask_pixels_idx):
                            overlay = debug_img.copy()
                            overlay[mask_pixels_idx] = (255, 0, 0)  # BGR 蓝色
                            cv2.addWeighted(overlay, 0.22, debug_img, 0.78, 0, dst=debug_img)

                        contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            cv2.drawContours(debug_img, contours, -1, (255, 0, 0), 2)
        except Exception as exc:
            logger.warning(f"balloon_fill bubble cache read failed, skip global bubble mask: {exc}")
            balloon_fill_mask = np.zeros(original_img.shape[:2], dtype=np.uint8)
            balloon_fill_label_map = None

    # Bubble mask for center_text_in_bubble: reuse balloon_fill_mask or try mangalens cache
    center_check_mask = balloon_fill_mask
    center_check_label_map = balloon_fill_label_map
    if center_check_mask is None and config.render.center_text_in_bubble and original_img is not None:
        try:
            _cr = get_cached_bubbles_with_mangalens(original_img, return_annotated=False, verbose=False)
            if _cr is not None:
                center_check_mask = build_bubble_mask_from_mangalens_result(_cr, original_img.shape[:2])
                if center_check_mask is not None and np.count_nonzero(center_check_mask) > 0:
                    _, center_check_label_map = cv2.connectedComponents(
                        np.where(center_check_mask > 0, 1, 0).astype(np.uint8), connectivity=8
                    )
                else:
                    center_check_mask = None
        except Exception:
            pass

    dst_points_list = []
    for region_idx, region in enumerate(text_regions):
        if region is None:
            logger.info(f"[RESIZE] 区域 {region_idx}: None，跳过")
            dst_points_list.append(None)
            continue
        region_font_family = getattr(region, 'font_family', '') or ''
        try:
            if config:
                config._current_region = region
                config._semantic_linebreak_current_region_idx = region_idx

            # 区域字体统一在布局测量前应用；后续候选字号、缩放和最终 dst_points 都用同一字体。
            if region_font_family:
                text_render.set_font(region_font_family)
            else:
                text_render.set_font(text_render.DEFAULT_FONT_FAMILY)
            text_render.set_bold(getattr(region, 'bold', False))

            # 如果 translation 为空,直接返回 min_rect,避免触发复杂的布局计算
            render_value = _region_render_value(region)
            if (
                not render_value
                or (isinstance(render_value, str) and not render_value.strip())
                or (is_rich_text_document(render_value) and not _rich_text_has_content(render_value))
            ):
                logger.info(f"[RESIZE] 区域 {region_idx}: translation 为空，使用 min_rect")
                dst_points_list.append(region.min_rect)
                continue

            _apply_default_english_case_preferences(region, config)
            prepare_text_replacements_for_layout(
                [region],
                config,
                resolve_render_horizontal=_resolve_region_render_horizontal,
                skip_text_replacements=skip_text_replacements,
            )

            # 判断是否需要气泡内居中：开启设置 且 区域确实在检测到的气泡内
            apply_bubble_centering = config.render.center_text_in_bubble
            if apply_bubble_centering and center_check_mask is not None and np.count_nonzero(center_check_mask) > 0:
                _rm = _build_region_reference_mask(region, center_check_mask, center_check_label_map)
                apply_bubble_centering = np.count_nonzero(_rm) > 0
            normal_anchor_mode = _resolve_layout_anchor_mode(
                apply_bubble_centering=apply_bubble_centering,
                skip_font_scaling=False,
            )
            skip_anchor_mode = _resolve_layout_anchor_mode(
                apply_bubble_centering=apply_bubble_centering,
                skip_font_scaling=True,
            )

            # skip_font_scaling模式：使用region.font_size作为最终字体，完全跳过排版缩放
            # 编辑器导出时用户设多少字号就渲染多少，不做任何缩放
            if skip_font_scaling:
                fixed_font_size = region.font_size if region.font_size > 0 else round((img.shape[0] + img.shape[1]) / 200)
                logger.debug(f"[RESIZE] skip_font_scaling: 区域 {region_idx} 使用固定字体大小 {fixed_font_size}")

                # 直接用固定字体大小计算文本框
                # 需要考虑 direction 强制覆盖（和 render() 中的判断逻辑一致）
                actual_horizontal = _resolve_region_render_horizontal(region)

                line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
                letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)

                dst_points = _calc_region_dst_points_for_font(
                    region=region,
                    font_size=fixed_font_size,
                    render_horizontally=actual_horizontal,
                    line_spacing_multiplier=line_spacing_multiplier,
                    letter_spacing_multiplier=letter_spacing_multiplier,
                    config=config,
                    anchor_mode=skip_anchor_mode,
                )

                if dst_points is None:
                    dst_points = region.min_rect

                region.font_size = fixed_font_size
                dst_points_list.append(dst_points)
                continue
            else:
                original_region_font_size = region.font_size if region.font_size > 0 else round((img.shape[0] + img.shape[1]) / 200)

                # 保存原始字体大小到region对象，用于JSON导出
                if not hasattr(region, 'original_font_size'):
                    region.original_font_size = original_region_font_size

                layout_min_font_size = 1
                target_font_size = max(_resolve_initial_layout_font_size(region, img, config), layout_min_font_size)

                # 入口只保留布局算法自身的参考字号：
                # region.font_size > 图像估算值
                # render.font_size 作为固定字号，在统一出口覆盖布局结果。
                region.layout_base_font_size = int(target_font_size)

                english_auto_line_break_applied = _apply_default_english_line_break_method(
                    region=region,
                    target_font_size=target_font_size,
                    original_img=original_img,
                    config=config,
                )
                if english_auto_line_break_applied:
                    render_horizontally = _resolve_region_render_horizontal(region)
                    line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
                    letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
                    final_font_size = _apply_final_font_constraints(target_font_size, config)

                    dst_points = _calc_region_dst_points_for_font(
                        region=region,
                        font_size=final_font_size,
                        render_horizontally=render_horizontally,
                        line_spacing_multiplier=line_spacing_multiplier,
                        letter_spacing_multiplier=letter_spacing_multiplier,
                        config=config,
                        anchor_mode=skip_anchor_mode,
                    )
                    if dst_points is None:
                        dst_points = region.min_rect

                    region.font_size = final_font_size
                    dst_points_list.append(dst_points)
                    continue

            render_horizontally = _resolve_region_render_horizontal(region)
            line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
            letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
            no_br_source_text = region.translation
            # region.translation 恒为 str（TextBlock._translation 只经
            # _translation_plain_text 写入），无需 isinstance 防御
            has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))

            line_box_width, line_box_height = region.unrotated_size
            if not (isinstance(line_box_width, (int, float)) and np.isfinite(line_box_width) and line_box_width > 0):
                line_box_width = float(max(region.xywh[2], 1))
            if not (isinstance(line_box_height, (int, float)) and np.isfinite(line_box_height) and line_box_height > 0):
                line_box_height = float(max(region.xywh[3], 1))

            layout_candidate_font_size = int(max(target_font_size, layout_min_font_size))
            configured_fixed_font_size = _resolve_configured_fixed_font_size(config)
            remove_linebreak_punctuation = bool(getattr(config.render, 'remove_linebreak_punctuation', False))
            if is_rich_text_document(_region_render_value(region)):
                # 富文本文档不可重排：不做断句优化/自动断行（会破坏结构化段落
                # 与样式边界），但字号自适应必须生效——不再直接使用估算字号。
                # 解析一次向下传实例，字号二分内不重复解析 dict。
                rich_render_value = ensure_rich_text_document(_region_render_value(region))
                layout_font_size = layout_candidate_font_size
                if configured_fixed_font_size <= 0:
                    # 1) 未旋转外接框内能容纳的最大字号，与估算字号取 min（只收缩）
                    box_fit_font_size = calc_font_from_box(
                        width=float(line_box_width),
                        height=float(line_box_height),
                        text=rich_render_value,
                        is_horizontal=render_horizontally,
                        line_spacing=line_spacing_multiplier,
                        config=config,
                        target_lang=region.target_lang,
                        letter_spacing=letter_spacing_multiplier,
                    )
                    layout_font_size = max(
                        min(layout_candidate_font_size, int(box_fit_font_size)),
                        layout_min_font_size,
                    )

                    # 2) balloon_fill：继续用气泡蒙版收缩（_calc_region_dst_points_for_font
                    #    内部已支持富文本正文锚定）；区域不完全在蒙版内时保持框收缩结果
                    if mode == 'balloon_fill' and original_img is not None:
                        try:
                            region_bubble_mask = np.zeros(original_img.shape[:2], dtype=np.uint8)
                            if balloon_fill_mask is not None and np.count_nonzero(balloon_fill_mask) > 0:
                                region_bubble_mask = _build_region_reference_mask(
                                    region, balloon_fill_mask, balloon_fill_label_map
                                )
                            if (
                                np.count_nonzero(region_bubble_mask) > 0
                                and _region_lines_fully_inside_mask(region, region_bubble_mask)
                            ):
                                configured_min_font_size = _resolve_configured_min_font_size(config)
                                bubble_min_font_size = max(
                                    configured_min_font_size if configured_min_font_size > 0 else 1, 1
                                )
                                best_font_size, _ = _binary_search_font_for_bubble_mask(
                                    region=region,
                                    start_font_size=layout_font_size,
                                    min_font_size=bubble_min_font_size,
                                    render_horizontally=render_horizontally,
                                    line_spacing_multiplier=line_spacing_multiplier,
                                    letter_spacing_multiplier=letter_spacing_multiplier,
                                    config=config,
                                    bubble_mask=region_bubble_mask,
                                    anchor_mode=normal_anchor_mode,
                                )
                                if best_font_size is not None:
                                    layout_font_size = int(best_font_size)
                        except Exception as exc:
                            logger.warning(
                                f"balloon_fill rich-text mask shrink failed for region {region_idx}: {exc}"
                            )

                final_font_size = _apply_final_font_constraints(layout_font_size, config)
                dst_points = _calc_region_dst_points_for_font(
                    region=region,
                    font_size=final_font_size,
                    render_horizontally=render_horizontally,
                    line_spacing_multiplier=line_spacing_multiplier,
                    letter_spacing_multiplier=letter_spacing_multiplier,
                    config=config,
                    anchor_mode=normal_anchor_mode,
                )
                if dst_points is None:
                    dst_points = region.min_rect

                region.font_size = final_font_size
                dst_points_list.append(dst_points)
                continue

            if has_br and remove_linebreak_punctuation:
                region.translation = strip_linebreak_edge_punctuation(region.translation)
                has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))

            if config.render.optimize_line_breaks and has_br and (mode != 'strict' or config.render.disable_auto_wrap):
                optimized_text, _ = optimize_line_breaks_for_region(
                    region,
                    config,
                    layout_candidate_font_size,
                    float(line_box_width),
                    float(line_box_height),
                )
                region.translation = optimized_text
                if remove_linebreak_punctuation:
                    region.translation = strip_linebreak_edge_punctuation(region.translation)
                has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))

            if has_br:
                if configured_fixed_font_size <= 0:
                    layout_candidate_font_size, _ = _select_preserved_line_layout_font(
                        base_font_size=layout_candidate_font_size,
                        width=float(line_box_width),
                        height=float(line_box_height),
                        text=region.translation,
                        is_horizontal=render_horizontally,
                        line_spacing=line_spacing_multiplier,
                        config=config,
                        target_lang=region.target_lang,
                        letter_spacing=letter_spacing_multiplier,
                    )
                    layout_candidate_font_size = max(int(layout_candidate_font_size), layout_min_font_size)
                candidate_required_width, candidate_required_height, candidate_n, _ = calc_box_from_font(
                    layout_candidate_font_size,
                    region.translation,
                    render_horizontally,
                    line_spacing_multiplier,
                    config,
                    region.target_lang,
                    center=None,
                    angle=0,
                    letter_spacing=letter_spacing_multiplier,
                )
            else:
                line_layout_max_font_size = int(
                    max(layout_candidate_font_size, line_box_width, line_box_height, layout_min_font_size)
                )
                if configured_fixed_font_size > 0:
                    line_layout_max_font_size = int(max(configured_fixed_font_size, layout_min_font_size))
                    layout_candidate_font_size = int(max(configured_fixed_font_size, layout_min_font_size))

                (
                    region.translation,
                    unified_layout_font_size,
                    candidate_required_width,
                    candidate_required_height,
                    candidate_n,
                ) = _solve_unified_no_br_layout(
                    text=region.translation,
                    render_horizontally=render_horizontally,
                    target_font_size=layout_candidate_font_size,
                    bubble_width=float(line_box_width),
                    bubble_height=float(line_box_height),
                    layout_min_font_size=layout_min_font_size,
                    line_spacing_multiplier=line_spacing_multiplier,
                    letter_spacing_multiplier=letter_spacing_multiplier,
                    config=config,
                    target_lang=region.target_lang,
                    max_font_size=line_layout_max_font_size,
                )
                layout_candidate_font_size = max(int(layout_candidate_font_size), int(unified_layout_font_size))

            # --- Mode 5: balloon_fill (MUST BE FIRST to override other modes) ---
            if mode == 'balloon_fill':
                semantic_linebreak_debug = (
                    bool(getattr(config.render, 'semantic_linebreak', False))
                    and _is_chinese_lang(getattr(region, 'target_lang', '') or '')
                )
                if not semantic_linebreak_debug:
                    logger.debug(f"=== balloon_fill mode activated for region {region_idx} ===")
                    logger.debug(f"OCR box (xywh): {region.xywh}")
                configured_min_font_size = _resolve_configured_min_font_size(config)
                min_font_size = max(configured_min_font_size if configured_min_font_size > 0 else 1, 1)

                if original_img is None:
                    logger.warning("balloon_fill mode requires original_img, fallback to geometry-based dst_points")
                    fallback_font_size = _apply_final_font_constraints(layout_candidate_font_size, config)
                    fallback_dst_points = _calc_region_dst_points_for_font(
                        region=region,
                        font_size=fallback_font_size,
                        render_horizontally=_resolve_region_render_horizontal(region),
                        line_spacing_multiplier=_resolve_line_spacing_multiplier(region, config),
                        letter_spacing_multiplier=_resolve_letter_spacing_multiplier(region, config),
                        config=config,
                        anchor_mode=normal_anchor_mode,
                    )
                    if fallback_dst_points is None:
                        fallback_dst_points = region.min_rect
                    region.font_size = fallback_font_size
                    dst_points_list.append(fallback_dst_points)
                    continue

                try:
                    region_bubble_mask = np.zeros(original_img.shape[:2], dtype=np.uint8)
                    if balloon_fill_mask is not None and np.count_nonzero(balloon_fill_mask) > 0:
                        region_bubble_mask = _build_region_reference_mask(region, balloon_fill_mask, balloon_fill_label_map)

                    lines_fully_enclosed = (
                        np.count_nonzero(region_bubble_mask) > 0
                        and _region_lines_fully_inside_mask(region, region_bubble_mask)
                    )
                    used_smart_scaling_fallback = False
                    chosen_dst_points = None
                    chosen_font_size = int(max(target_font_size, layout_min_font_size))
                    overflow_candidate_dst_points = None
                    preferred_font_size_for_debug = None
                    bubble_w = 0
                    bubble_h = 0
                    line_budget = 0.0

                    if not lines_fully_enclosed:
                        used_smart_scaling_fallback = True
                        smart_scaling_config = copy.deepcopy(config)
                        smart_scaling_config.render.layout_mode = 'smart_scaling'
                        smart_result = resize_regions_to_font_size(
                            img=img,
                            text_regions=[region],
                            config=smart_scaling_config,
                            original_img=None,
                            return_debug_img=False,
                            skip_font_scaling=skip_font_scaling,
                            skip_text_replacements=skip_text_replacements,
                        )
                        if isinstance(smart_result, list) and len(smart_result) > 0:
                            chosen_dst_points = smart_result[0]
                        if chosen_dst_points is None:
                            chosen_dst_points = region.min_rect
                        chosen_font_size = region.font_size if region.font_size > 0 else chosen_font_size
                        if not semantic_linebreak_debug:
                            logger.debug(f"balloon_fill region {region_idx}: not fully enclosed, fallback to smart_scaling")
                    else:
                        if (
                            not has_br
                            and bool(getattr(config.render, 'semantic_linebreak', False))
                            and _is_chinese_lang(getattr(region, 'target_lang', '') or '')
                            and np.count_nonzero(region_bubble_mask) > 0
                        ):
                            _bubble_x, _bubble_y, bubble_w, bubble_h = find_largest_inscribed_rect(region_bubble_mask)
                            line_budget = float(bubble_w if render_horizontally else bubble_h)

                        if has_br:
                            if not semantic_linebreak_debug:
                                logger.debug(
                                    f"balloon_fill region {region_idx}: keep explicit breaks, "
                                    f"candidate font={layout_candidate_font_size}, "
                                    f"required={candidate_required_width:.1f}x{candidate_required_height:.1f}"
                                )
                        else:
                            if not semantic_linebreak_debug:
                                logger.debug(
                                    f"balloon_fill region {region_idx}: unified no_br layout, "
                                    f"result_segments={candidate_n}, font={layout_candidate_font_size}, "
                                    f"required={candidate_required_width:.1f}x{candidate_required_height:.1f}"
                                )

                        preferred_font_size = int(max(layout_candidate_font_size, layout_min_font_size))
                        preferred_font_size_for_debug = preferred_font_size

                        # 调试用途：记录“超出范围候选框”（较大字号候选但不满足蒙版约束）
                        preferred_fits = False
                        preferred_dst_points = _calc_region_dst_points_for_font(
                            region=region,
                            font_size=preferred_font_size,
                            render_horizontally=render_horizontally,
                            line_spacing_multiplier=line_spacing_multiplier,
                            letter_spacing_multiplier=letter_spacing_multiplier,
                            config=config,
                            anchor_mode=normal_anchor_mode,
                        )
                        if preferred_dst_points is not None and preferred_dst_points.size > 0:
                            preferred_fits = _polygon_fully_inside_mask(np.asarray(preferred_dst_points[0]), region_bubble_mask)
                            if not preferred_fits:
                                overflow_candidate_dst_points = preferred_dst_points

                        if (
                            semantic_linebreak_debug
                            and not has_br
                            and bubble_w > 0
                            and bubble_h > 0
                            and line_budget > 0
                        ):
                            single_width, single_height, _, _ = calc_box_from_font(
                                preferred_font_size,
                                no_br_source_text,
                                render_horizontally,
                                line_spacing_multiplier,
                                config,
                                region.target_lang,
                                center=None,
                                angle=0,
                                letter_spacing=letter_spacing_multiplier,
                            )
                            total_budget = float(single_width if render_horizontally else single_height)
                            linebreak_snapshot = build_chinese_linebreak_debug_snapshot(
                                no_br_source_text,
                                font_size=preferred_font_size,
                                target_segments=candidate_n,
                                total_budget=total_budget,
                                line_budget=line_budget,
                                horizontal=render_horizontally,
                                letter_spacing=letter_spacing_multiplier,
                            )

                            original_candidate_text = region.translation

                            def evaluate_chinese_candidate(candidate_text: str) -> Optional[BubbleLinebreakEvaluation]:
                                region.translation = candidate_text
                                req_w, req_h, req_n, _ = calc_box_from_font(
                                    preferred_font_size,
                                    candidate_text,
                                    render_horizontally,
                                    line_spacing_multiplier,
                                    config,
                                    region.target_lang,
                                    center=None,
                                    angle=0,
                                    letter_spacing=letter_spacing_multiplier,
                                )
                                candidate_dst_points = _calc_region_dst_points_for_font(
                                    region=region,
                                    font_size=preferred_font_size,
                                    render_horizontally=render_horizontally,
                                    line_spacing_multiplier=line_spacing_multiplier,
                                    letter_spacing_multiplier=letter_spacing_multiplier,
                                    config=config,
                                    anchor_mode=normal_anchor_mode,
                                )
                                if candidate_dst_points is None or candidate_dst_points.size == 0:
                                    return None
                                return BubbleLinebreakEvaluation(
                                    text_with_br=candidate_text,
                                    required_width=float(req_w),
                                    required_height=float(req_h),
                                    n_segments=int(req_n),
                                    dst_points=candidate_dst_points,
                                    overflow_pixels=bubble_mask_overflow_pixels(candidate_dst_points, region_bubble_mask),
                                )

                            try:
                                semantic_choice = choose_chinese_bubble_linebreak_with_trace(
                                    source_text=no_br_source_text,
                                    current_text=region.translation,
                                    font_size=preferred_font_size,
                                    target_segments=candidate_n,
                                    total_budget=total_budget,
                                    line_budget=line_budget,
                                    horizontal=render_horizontally,
                                    letter_spacing=letter_spacing_multiplier,
                                    evaluate=evaluate_chinese_candidate,
                                )
                            finally:
                                region.translation = original_candidate_text

                            if semantic_choice is not None and semantic_choice.selected is not None:
                                chosen_semantic_candidate = semantic_choice.selected
                                expected_candidate_n = candidate_n
                                region.translation = chosen_semantic_candidate.text_with_br
                                layout_candidate_font_size = preferred_font_size
                                candidate_required_width = chosen_semantic_candidate.required_width
                                candidate_required_height = chosen_semantic_candidate.required_height
                                candidate_n = chosen_semantic_candidate.n_segments
                                preferred_dst_points = chosen_semantic_candidate.dst_points
                                preferred_fits = chosen_semantic_candidate.fits
                                overflow_candidate_dst_points = None if preferred_fits else chosen_semantic_candidate.dst_points
                                append_chinese_linebreak_debug_record(
                                    config,
                                    {
                                        "stage": "bubble_mask_choice",
                                        "region_index": region_idx,
                                        "input": no_br_source_text,
                                        "current_candidate": original_candidate_text,
                                        "direction": "h" if render_horizontally else "v",
                                        "font_size": preferred_font_size,
                                        "target_segments": expected_candidate_n,
                                        "ocr_box_xywh": np.asarray(region.xywh).tolist() if getattr(region, "xywh", None) is not None else None,
                                        "ocr_box_size": {"width": float(line_box_width), "height": float(line_box_height)},
                                        "bubble_inscribed_rect": {
                                            "width": float(bubble_w),
                                            "height": float(bubble_h),
                                            "line_budget": float(line_budget),
                                        },
                                        "single_line_required": {"width": float(single_width), "height": float(single_height)},
                                        "total_budget": float(total_budget),
                                        "mask": {
                                            "encoding": "png_base64",
                                            "width": int(region_bubble_mask.shape[1]) if region_bubble_mask is not None else 0,
                                            "height": int(region_bubble_mask.shape[0]) if region_bubble_mask is not None else 0,
                                            "nonzero_pixels": int(np.count_nonzero(region_bubble_mask)) if region_bubble_mask is not None else 0,
                                            "data": _encode_mask_png_base64(region_bubble_mask),
                                        },
                                        "linebreak_snapshot": linebreak_snapshot,
                                        "selected": {
                                            "text_with_br": chosen_semantic_candidate.text_with_br,
                                            "segments": int(chosen_semantic_candidate.n_segments),
                                            "required": {
                                                "width": float(chosen_semantic_candidate.required_width),
                                                "height": float(chosen_semantic_candidate.required_height),
                                            },
                                            "fits": bool(chosen_semantic_candidate.fits),
                                            "overflow_pixels": int(chosen_semantic_candidate.overflow_pixels),
                                            "dst_points": np.asarray(chosen_semantic_candidate.dst_points).tolist()
                                            if chosen_semantic_candidate.dst_points is not None
                                            else None,
                                        },
                                        "candidate_evaluations": semantic_choice.evaluations,
                                        "candidates": [
                                            {
                                                "rank": rank,
                                                "score": list(score),
                                                "selected": candidate.text_with_br == chosen_semantic_candidate.text_with_br,
                                                "text_with_br": candidate.text_with_br,
                                                "segments": int(candidate.n_segments),
                                                "semantic_penalty": int(score[1]),
                                                "required": {
                                                    "width": float(candidate.required_width),
                                                    "height": float(candidate.required_height),
                                                },
                                                "fits": bool(candidate.fits),
                                                "overflow_pixels": int(candidate.overflow_pixels),
                                                "dst_points": np.asarray(candidate.dst_points).tolist()
                                                if candidate.dst_points is not None
                                                else None,
                                            }
                                            for rank, (score, candidate) in enumerate(semantic_choice.candidates, start=1)
                                        ],
                                    },
                                )
                            else:
                                append_chinese_linebreak_debug_record(
                                    config,
                                    {
                                        "stage": "bubble_mask_choice",
                                        "region_index": region_idx,
                                        "input": no_br_source_text,
                                        "current_candidate": original_candidate_text,
                                        "direction": "h" if render_horizontally else "v",
                                        "font_size": preferred_font_size,
                                        "target_segments": candidate_n,
                                        "ocr_box_xywh": np.asarray(region.xywh).tolist() if getattr(region, "xywh", None) is not None else None,
                                        "bubble_inscribed_rect": {
                                            "width": float(bubble_w),
                                            "height": float(bubble_h),
                                            "line_budget": float(line_budget),
                                        },
                                        "single_line_required": {"width": float(single_width), "height": float(single_height)},
                                        "total_budget": float(total_budget),
                                        "mask": {
                                            "encoding": "png_base64",
                                            "width": int(region_bubble_mask.shape[1]) if region_bubble_mask is not None else 0,
                                            "height": int(region_bubble_mask.shape[0]) if region_bubble_mask is not None else 0,
                                            "nonzero_pixels": int(np.count_nonzero(region_bubble_mask)) if region_bubble_mask is not None else 0,
                                            "data": _encode_mask_png_base64(region_bubble_mask),
                                        },
                                        "linebreak_snapshot": linebreak_snapshot,
                                        "selected": None,
                                        "candidate_evaluations": semantic_choice.evaluations if semantic_choice is not None else [],
                                        "candidates": [],
                                    },
                                )

                        best_font_size, best_dst_points = _binary_search_font_for_bubble_mask(
                            region=region,
                            start_font_size=preferred_font_size,
                            min_font_size=min_font_size,
                            render_horizontally=render_horizontally,
                            line_spacing_multiplier=line_spacing_multiplier,
                            letter_spacing_multiplier=letter_spacing_multiplier,
                            config=config,
                            bubble_mask=region_bubble_mask,
                            anchor_mode=normal_anchor_mode,
                        )
                        if best_font_size is not None and best_dst_points is not None:
                            chosen_font_size = int(best_font_size)
                            chosen_dst_points = best_dst_points
                            if not semantic_linebreak_debug:
                                logger.debug(
                                    f"balloon_fill region {region_idx}: enclosed lines, binary-search font {preferred_font_size}->{chosen_font_size}"
                                )
                        else:
                            chosen_font_size = int(max(min_font_size, 1))
                            chosen_dst_points = _calc_region_dst_points_for_font(
                                region=region,
                                font_size=chosen_font_size,
                                render_horizontally=render_horizontally,
                                line_spacing_multiplier=line_spacing_multiplier,
                                letter_spacing_multiplier=letter_spacing_multiplier,
                                config=config,
                                anchor_mode=normal_anchor_mode,
                            )
                            if chosen_dst_points is None:
                                chosen_font_size = preferred_font_size
                                chosen_dst_points = preferred_dst_points
                            if not semantic_linebreak_debug:
                                logger.debug(
                                    f"balloon_fill region {region_idx}: no mask-safe layout found, shrink to font={chosen_font_size}"
                                )

                    if chosen_dst_points is None:
                        chosen_dst_points = region.min_rect

                    final_font_size = _apply_final_font_constraints(chosen_font_size, config)
                    final_dst_points = _calc_region_dst_points_for_font(
                        region=region,
                        font_size=final_font_size,
                        render_horizontally=render_horizontally,
                        line_spacing_multiplier=line_spacing_multiplier,
                        letter_spacing_multiplier=letter_spacing_multiplier,
                        config=config,
                        anchor_mode=normal_anchor_mode,
                    )
                    if final_dst_points is None:
                        final_dst_points = chosen_dst_points

                    region.font_size = final_font_size
                    chosen_dst_points = final_dst_points
                    dst_points_list.append(chosen_dst_points)

                    if debug_img is not None:
                        ocr_x1, ocr_y1, ocr_w, ocr_h = map(int, region.xywh)
                        cv2.rectangle(debug_img, (ocr_x1, ocr_y1), (ocr_x1 + ocr_w, ocr_y1 + ocr_h), (0, 0, 255), 2)

                        if np.count_nonzero(region_bubble_mask) > 0:
                            component_contours, _ = cv2.findContours(region_bubble_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                            if component_contours:
                                cv2.drawContours(debug_img, component_contours, -1, (0, 255, 255), 1)

                        render_poly = np.asarray(chosen_dst_points).reshape(-1, 2).astype(np.int32)
                        if render_poly.shape[0] >= 4:
                            cv2.polylines(debug_img, [render_poly], True, (0, 255, 0), 2)
                            label = f'B{region_idx}:{region.font_size}'
                            if used_smart_scaling_fallback:
                                label += ':SSF'
                            elif lines_fully_enclosed:
                                if preferred_font_size_for_debug is not None:
                                    label += f':ENC({preferred_font_size_for_debug}->{chosen_font_size})'
                                else:
                                    label += f':ENC({chosen_font_size})'
                            cv2.putText(
                                debug_img,
                                label,
                                tuple(render_poly[0]),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.45,
                                (0, 255, 0),
                                1,
                            )

                        if overflow_candidate_dst_points is not None:
                            overflow_poly = np.asarray(overflow_candidate_dst_points).reshape(-1, 2).astype(np.int32)
                            if overflow_poly.shape[0] >= 4:
                                # BGR 橙色：表示候选框超出蒙版范围，最终被收缩/放弃
                                cv2.polylines(debug_img, [overflow_poly], True, (0, 165, 255), 2)
                                cv2.putText(
                                    debug_img,
                                    f'B{region_idx}:OVR',
                                    tuple(overflow_poly[0]),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.45,
                                    (0, 165, 255),
                                    1,
                                )
                except Exception as e:
                    logger.exception(f"Error in balloon_fill layout for region {region_idx}: {e}")
                    dst_points_list.append(region.min_rect)
                    region.font_size = target_font_size

                continue

            # --- Mode: strict ---
            if mode == 'strict':
                font_size = layout_candidate_font_size
                min_shrink_font_size = 8

                # AI 断句适配：如果开启了 AI 断句且有 BR 标记，使用无限宽度/高度

                # 检测是否为替换翻译模式
                is_replace_mode = config.cli.replace_translation if (config and hasattr(config, 'cli')) else False
                
                force_single_line_no_wrap = is_replace_mode and should_force_no_wrap_single_region(region)
                if is_replace_mode and len(region.lines) == 1 and not force_single_line_no_wrap:
                    logger.debug("[STRICT MODE] 替换模式单行区域检测到方向改写，允许自动换行")

                use_ai_break = (config.render.disable_auto_wrap and has_br) or force_single_line_no_wrap

                if not has_br:
                    layout_font_size = max(layout_candidate_font_size, min_shrink_font_size)
                    final_font_size = _apply_final_font_constraints(layout_font_size, config)
                    dst_points = _calc_region_dst_points_for_font(
                        region=region,
                        font_size=final_font_size,
                        render_horizontally=render_horizontally,
                        line_spacing_multiplier=line_spacing_multiplier,
                        letter_spacing_multiplier=letter_spacing_multiplier,
                        config=config,
                        anchor_mode=normal_anchor_mode,
                    )
                    if dst_points is None:
                        dst_points = region.min_rect

                    region.font_size = final_font_size
                    dst_points_list.append(dst_points)
                    continue

                if use_ai_break:
                    calc_max_width = 99999
                    calc_max_height = 99999
                    if force_single_line_no_wrap:
                        logger.debug("[STRICT MODE] 替换模式单行强制不换行 (OCR lines=1)，使用无限尺寸")
                        # 强制清洗文本：移除所有可能导致换行的字符（\n, [BR]等），确保它真的是单行
                        region.translation = re.sub(r'(\n|\[BR\]|【BR】|<br>)', '', region.translation, flags=re.IGNORECASE)
                    else:
                        logger.debug("[STRICT MODE] AI断句开启，使用无限尺寸")
                else:
                    calc_max_width = region.unrotated_size[0]
                    calc_max_height = region.unrotated_size[1]

                # Step 1: 先缩小字体直到文本能放进文本框
                while font_size >= min_shrink_font_size:
                    if render_horizontally:
                        lines, _ = text_render.calc_horizontal(
                            font_size,
                            region.translation,
                            max_width=calc_max_width,
                            max_height=calc_max_height,
                            language=region.target_lang,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        if len(lines) <= len(region.texts):
                            break
                    else:
                        lines, _ = text_render.calc_vertical(
                            font_size,
                            region.translation,
                            max_height=calc_max_height,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        if len(lines) <= len(region.texts):
                            break
                    font_size -= 1

                # Step 2: 尝试扩大字体以更好地填充空间（但不超过初始大小）
                # 从当前能放下的字体大小开始，逐步增加
                max_fitting_font_size = font_size
                test_font_size = font_size + 1

                while test_font_size <= layout_candidate_font_size:
                    if render_horizontally:
                        test_lines, _ = text_render.calc_horizontal(
                            test_font_size,
                            region.translation,
                            max_width=calc_max_width,
                            max_height=calc_max_height,
                            language=region.target_lang,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        if len(test_lines) <= len(region.texts):
                            max_fitting_font_size = test_font_size
                            test_font_size += 1
                        else:
                            break
                    else:
                        test_lines, _ = text_render.calc_vertical(
                            test_font_size,
                            region.translation,
                            max_height=calc_max_height,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        if len(test_lines) <= len(region.texts):
                            max_fitting_font_size = test_font_size
                            test_font_size += 1
                        else:
                            break

                # Apply final post-layout constraints: offset, scale ratio, and min/max clamps.
                layout_font_size = max(max_fitting_font_size, min_shrink_font_size)
                final_font_size = _apply_final_font_constraints(layout_font_size, config)
                dst_points = _calc_region_dst_points_for_font(
                    region=region,
                    font_size=final_font_size,
                    render_horizontally=render_horizontally,
                    line_spacing_multiplier=line_spacing_multiplier,
                    letter_spacing_multiplier=letter_spacing_multiplier,
                    config=config,
                    anchor_mode=normal_anchor_mode,
                )
                if dst_points is None:
                    dst_points = region.min_rect

                region.font_size = final_font_size
                dst_points_list.append(dst_points)
                continue

            # --- Mode: smart_scaling ---
            elif mode == 'smart_scaling':
                # 添加诊断日志
                logger.debug(f"[SMART_SCALING] Region {region_idx}: mode={mode}, has_br={has_br}")

                try:
                    bubble_width = float(line_box_width)
                    bubble_height = float(line_box_height)
                    required_width = float(candidate_required_width)
                    required_height = float(candidate_required_height)
                    n = max(1, int(candidate_n))
                    target_font_size = int(max(layout_candidate_font_size, layout_min_font_size))

                    # Create base polygon for scaling
                    try:
                        unrotated_base_poly = Polygon(region.unrotated_min_rect[0])
                    except Exception:
                        unrotated_base_poly = Polygon([(0, 0), (bubble_width, 0), (bubble_width, bubble_height), (0, bubble_height)])

                    logger.debug(
                        f"[SMART_SCALING] Region {region_idx}: candidate n={n}, "
                        f"font={target_font_size}, required={required_width:.1f}x{required_height:.1f}"
                    )

                    # Check for overflow in either dimension
                    width_overflow = max(0, required_width - bubble_width)
                    height_overflow = max(0, required_height - bubble_height)

                    dst_points = region.min_rect

                    if width_overflow > 0 or height_overflow > 0:
                        # 独立缩放宽度和高度（单列/单行和多列/多行都使用相同逻辑）
                        width_scale_factor = 1.0
                        height_scale_factor = 1.0

                        if width_overflow > 0:
                            width_scale_needed = required_width / bubble_width if bubble_width > 0 else 1.0
                            diff_ratio_w = width_scale_needed - 1.0
                            box_expansion_ratio_w = diff_ratio_w / 2
                            width_scale_factor = 1 + min(box_expansion_ratio_w, 1.0)

                        if height_overflow > 0:
                            height_scale_needed = required_height / bubble_height if bubble_height > 0 else 1.0
                            diff_ratio_h = height_scale_needed - 1.0
                            box_expansion_ratio_h = diff_ratio_h / 2
                            height_scale_factor = 1 + min(box_expansion_ratio_h, 1.0)

                        try:
                            scaled_unrotated_poly = affinity.scale(unrotated_base_poly, xfact=width_scale_factor, yfact=height_scale_factor, origin='center')
                            scaled_unrotated_points = np.array(scaled_unrotated_poly.exterior.coords[:4])
                            dst_points = rotate_polygons(region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
                        except Exception as e:
                            logger.warning(f"Failed to apply independent scaling: {e}")

                        # 字体缩放基于最大的溢出维度
                        scale_needed = max(required_width / bubble_width if bubble_width > 0 else 1.0,
                                         required_height / bubble_height if bubble_height > 0 else 1.0)
                        diff_ratio = scale_needed - 1.0
                        font_shrink_ratio = diff_ratio / 2 / (1 + diff_ratio)
                        font_scale_factor = 1 - min(font_shrink_ratio, 0.5)
                        target_font_size = int(target_font_size * font_scale_factor)

                        # 用取整后的字体重新算required
                        if render_horizontally:
                            final_total_width = text_render.get_string_width(
                                target_font_size,
                                region.translation,
                                letter_spacing=letter_spacing_multiplier,
                            )
                            final_spacing_y = int(target_font_size * 0.01 * line_spacing_multiplier)
                            required_width = final_total_width / n if n > 0 else final_total_width
                            required_height = n * target_font_size + max(0, n - 1) * final_spacing_y
                        else:
                            required_width, required_height, n, _ = calc_box_from_font(
                                target_font_size,
                                region.translation,
                                False,
                                line_spacing_multiplier,
                                config,
                                region.target_lang,
                                center=None,
                                angle=0,
                                letter_spacing=letter_spacing_multiplier,
                            )

                        # 用新的required重新计算框扩大
                        width_scale_factor = required_width / bubble_width if bubble_width > 0 and required_width > bubble_width else 1.0
                        height_scale_factor = required_height / bubble_height if bubble_height > 0 and required_height > bubble_height else 1.0

                        try:
                            scaled_unrotated_poly = affinity.scale(unrotated_base_poly, xfact=width_scale_factor, yfact=height_scale_factor, origin='center')
                            scaled_unrotated_points = np.array(scaled_unrotated_poly.exterior.coords[:4])
                            dst_points = rotate_polygons(region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
                        except Exception as e:
                            logger.warning(f"Failed to apply final scaling: {e}")
                    else:
                        # No overflow, can enlarge font to fit better
                        if required_width > 0 and required_height > 0:
                            width_scale_factor = bubble_width / required_width
                            height_scale_factor = bubble_height / required_height
                            font_scale_factor = min(width_scale_factor, height_scale_factor)
                            target_font_size = int(target_font_size * font_scale_factor)

                        try:
                            unrotated_points = np.array(unrotated_base_poly.exterior.coords[:4])
                            dst_points = rotate_polygons(region.center, unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
                        except Exception as e:
                            logger.warning(f"Failed to use base polygon: {e}")

                except Exception as e:
                    logger.exception(f"Error in smart_scaling layout for region {region_idx}: {e}")
                    # Fallback to a safe state
                    target_font_size = getattr(region, 'layout_base_font_size', target_font_size)
                    dst_points = region.min_rect

                # Apply final post-layout constraints: offset, scale ratio, and min/max clamps.
                final_font_size = _apply_final_font_constraints(target_font_size, config)

                # 用辅助函数直接计算 dst_points（包含矩形构建和旋转）
                line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
                letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
                dst_points = _calc_region_dst_points_for_font(
                    region=region,
                    font_size=final_font_size,
                    render_horizontally=render_horizontally,
                    line_spacing_multiplier=line_spacing_multiplier,
                    letter_spacing_multiplier=letter_spacing_multiplier,
                    config=config,
                    anchor_mode=normal_anchor_mode,
                )

                # 如果计算失败，使用原始检测框
                if dst_points is None:
                    dst_points = region.min_rect

                region.font_size = final_font_size
                dst_points_list.append(dst_points)
                continue

            # --- Unsupported layout modes ---
            else:
                raise ValueError(
                    f"Unsupported render.layout_mode: {mode!r}. "
                    "Supported values: balloon_fill, smart_scaling, strict"
                )
        except Exception:
            raise
        
    # Add legend to debug image
    if return_debug_img and debug_img is not None:
        # Add legend in top-left corner
        legend_y = 30
        cv2.putText(debug_img, 'Balloon Fill Debug:', (10, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(debug_img, 'Red = OCR Box', (10, legend_y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        cv2.putText(debug_img, 'Yellow = Region Bubble Component', (10, legend_y + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        cv2.putText(debug_img, 'Blue = Global Bubble Mask', (10, legend_y + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
        cv2.putText(debug_img, 'Green = Render Box', (10, legend_y + 105), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.putText(debug_img, 'Orange = Overflow Candidate Box', (10, legend_y + 130), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
        return dst_points_list, debug_img
    
    return dst_points_list


async def dispatch(
    img: np.ndarray,
    text_regions: List[TextBlock],
    config: Config = None,
    original_img: np.ndarray = None,
    return_debug_img: bool = False,
    skip_font_scaling: bool = False,
    skip_text_replacements: bool = False,
    render_alpha: Optional[np.ndarray] = None,
    ):

    if config is None:
        from ..config import Config
        config = Config()

    if config.render.renderer in (
        Renderer.openai_renderer,
        Renderer.gemini_renderer,
    ):
        prepare_text_replacements_for_layout(
            text_regions,
            config,
            resolve_render_horizontal=_resolve_region_render_horizontal,
            skip_text_replacements=skip_text_replacements,
        )
        from .model_api_renderer import dispatch_api_rendering

        result = await dispatch_api_rendering(img=img, text_regions=text_regions, config=config)
        sync_translation_raw_from_layout(text_regions, config)
        return result

    await download_chinese_linebreak_models_if_enabled(config)

    text_render.set_font(getattr(config.render, 'font_family', None) or text_render.DEFAULT_FONT_FAMILY)
    text_regions = list(filter(lambda region: _region_render_value(region), text_regions))

    result = resize_regions_to_font_size(
        img,
        text_regions,
        config,
        original_img,
        return_debug_img,
        skip_font_scaling=skip_font_scaling,
        skip_text_replacements=skip_text_replacements,
    )
    sync_translation_raw_from_layout(text_regions, config)

    # Handle return value (may be tuple if debug image is included)
    if return_debug_img and isinstance(result, tuple):
        dst_points_list, debug_img = result
    else:
        dst_points_list = result
        debug_img = None

    for region_idx, (region, dst_points) in enumerate(tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions))):
        # 保存缩放算法计算的 dst_points 到 region，供 PSD 导出使用
        # 注意：这是缩放后的真实文本区域，不是 render 函数中扩展后的区域
        region.dst_points = dst_points

        try:
            # 检查是否有文本需要渲染
            render_value = _region_render_value(region)
            if not render_value or (isinstance(render_value, str) and not render_value.strip()):
                logger.info(
                    f"[RENDER] 跳过空文本区域: text='{region.text[:20] if region.text else ''}', "
                    f"translation='{_translation_preview(render_value, 20)}'"
                )
                continue

            # render() / put_text_*() 统一接收“倍率”，基础值在文本渲染器内部处理。
            line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
            img = render(
                img,
                region,
                dst_points,
                not config.render.no_hyphenation,
                line_spacing_multiplier,
                config.render.disable_font_border,
                config,
                render_alpha=render_alpha,
            )
        except Exception:
            raise
    
    if return_debug_img and debug_img is not None:
        return img, debug_img
    return img

def render(
    img,
    region: TextBlock,
    dst_points,
    hyphenate,
    line_spacing,
    disable_font_border,
    config: Config,
    render_alpha: Optional[np.ndarray] = None,
):
    # 区域只保存 family；字体文件在启动/导入阶段注册。
    region_font_family = getattr(region, 'font_family', '') or ''
    if region_font_family:
        text_render.set_font(region_font_family)
    else:
        text_render.set_font(text_render.DEFAULT_FONT_FAMILY)
    text_render.set_bold(getattr(region, 'bold', False))

    # --- START BRUTEFORCE COLOR FIX ---
    fg = (0, 0, 0) # Default to black
    try:
        # Priority 1: Check for the original hex string from the UI
        if hasattr(region, 'font_color') and isinstance(region.font_color, str) and region.font_color.startswith('#'):
            hex_c = region.font_color
            if len(hex_c) == 7:
                r = int(hex_c[1:3], 16)
                g = int(hex_c[3:5], 16)
                b = int(hex_c[5:7], 16)
                fg = (r, g, b)
        # Priority 2: Check for a pre-converted tuple
        elif hasattr(region, 'fg_colors') and isinstance(region.fg_colors, (tuple, list)) and len(region.fg_colors) == 3:
            fg = tuple(region.fg_colors)
        # Last resort: Use the method2
        else:
            fg, _ = region.get_font_colors()
    except Exception:
        # If anything fails, fg remains black
        pass

    # Get background color separately
    _, bg = region.get_font_colors()
    # --- END BRUTEFORCE COLOR FIX ---

    # Convert hex color string to RGB tuple, if necessary
    if isinstance(fg, str) and fg.startswith('#') and len(fg) == 7:
        try:
            r = int(fg[1:3], 16)
            g = int(fg[3:5], 16)
            b = int(fg[5:7], 16)
            fg = (r, g, b)
        except ValueError:
            fg = (0, 0, 0)  # Default to black on error
    elif not isinstance(fg, (tuple, list)):
        fg = (0, 0, 0) # Default to black if format is unexpected

    if getattr(region, 'adjust_bg_color', True):
        fg, bg = fg_bg_compare(fg, bg)

    text_to_render = region.get_translation_for_rendering()
    has_br_in_text = isinstance(text_to_render, str) and bool(re.search(r'(\[BR\]|<br>|【BR】)', text_to_render, flags=re.IGNORECASE))
    if has_br_in_text:
        text_to_render = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', text_to_render, flags=re.IGNORECASE)

    if disable_font_border :
        bg = None

    middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
    norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
    norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
    r_orig = np.mean(norm_h / norm_v)

    render_horizontally = _resolve_region_render_horizontal(region)
    letter_spacing = _resolve_letter_spacing_multiplier(region, config)

    # 将当前region传递给config，用于方向不匹配检测
    if config:
        config._current_region = region

    # 使用 Qt 离屏渲染器
    # 检测是否需要使用高质量渲染（针对低分辨率优化）
    text_is_structured = is_rich_text_document(text_to_render)
    if text_is_structured:
        # 仅由 BR 转换产生的"纯文本 + 换行"文档（无任何样式/注音/纵中横）
        # 回退等价多行字符串，走纯字符串渲染路径，保住 HQ 超采样；
        # 带样式的文档保持结构化路径（text_render_hq 不支持富文本）。
        plain_equivalent = plain_equivalent_text(text_to_render)
        if plain_equivalent is not None:
            text_to_render = plain_equivalent
            text_is_structured = False
    use_hq_render = (not text_is_structured) and text_render_hq.should_use_hq_rendering(
        region.font_size,
        (img.shape[1], img.shape[0]),
    )
    
    if use_hq_render:
        logger.debug(f"[HQ_RENDER] 使用高质量渲染模式 (font_size={region.font_size})")
        temp_box = text_render_hq.render_text_with_upscale(
            font_size=region.font_size,
            text=text_to_render,
            width=round(norm_h[0]),
            height=round(norm_v[0]),
            alignment=region.alignment,
            fg=fg,
            bg=bg,
            line_spacing=line_spacing,
            config=config,
            is_horizontal=render_horizontally,
            upscale_factor=None,  # 自动计算
            region_count=len(region.lines),
            # 横排专用参数
            reversed_direction=(region.direction == 'hl'),
            target_lang=region.target_lang,
            hyphenate=hyphenate,
            stroke_width=region.stroke_width,  # 传递区域的描边宽度
            letter_spacing=letter_spacing,
        )
    elif render_horizontally:
        temp_box = text_render.put_text_horizontal(
            region.font_size,
            text_to_render,
            round(norm_h[0]),
            round(norm_v[0]),
            region.alignment,
            region.direction == 'hl',
            fg,
            bg,
            region.target_lang,
            hyphenate,
            line_spacing,
            config,
            len(region.lines),  # Pass region count
            stroke_width=region.stroke_width,  # 传递区域的描边宽度
            letter_spacing=letter_spacing,
        )
    else:
        temp_box = text_render.put_text_vertical(
            region.font_size,
            text_to_render,
            round(norm_v[0]),
            region.alignment,
            fg,
            bg,
            line_spacing,
            config,
            len(region.lines),  # Pass region count
            stroke_width=region.stroke_width,  # 传递区域的描边宽度
            letter_spacing=letter_spacing,
        )
    
    if temp_box is None:
        logger.warning(f"[RENDER SKIPPED] Text rendering returned None. Text: '{_translation_preview(region.translation, 100)}...'")
        return img
    
    h, w, _ = temp_box.shape
    if h == 0 or w == 0:
        logger.warning(f"Skipping rendering for region with invalid dimensions (w={w}, h={h}). Text: '{region.translation}'")
        return img
    r_temp = w / h

    box = None
    if render_horizontally:
        if r_temp > r_orig:
            h_ext = int((w / r_orig - h) // 2) if r_orig > 0 else 0
            if h_ext >= 0:
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)
                box[h_ext:h_ext+h, 0:w] = temp_box
            else:
                box = temp_box.copy()
        else:
            w_ext = int((h * r_orig - w) // 2)
            if w_ext >= 0:
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)
                # 横排文本默认水平居中
                box[0:h, w_ext:w_ext+w] = temp_box
            else:
                box = temp_box.copy()
    else:
        if r_temp > r_orig:
            h_ext = int(w / (2 * r_orig) - h / 2) if r_orig > 0 else 0
            if h_ext >= 0:
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)
                box[h_ext:h_ext+h, 0:w] = temp_box
            else:
                box = temp_box.copy()
        else:
            w_ext = int((h * r_orig - w) / 2)
            if w_ext >= 0:
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)
                # 竖排文本水平居中
                box[0:h, w_ext:w_ext+w] = temp_box
            else:
                box = temp_box.copy()

    src_points = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]]).astype(np.float32)

    # 文字框允许超出画布；最终只把画布内的有效像素合成回原图。
    # 不在这里按坐标分量 clip 四角点，否则会压缩/扭曲文本，并造成预览与导出不一致。
    img_h, img_w = img.shape[:2]

    # 统一使用局部区域渲染，避免 OpenCV warpPerspective 的 32767 像素限制
    SHRT_MAX = 32767
    if box.shape[0] > SHRT_MAX or box.shape[1] > SHRT_MAX:
        logger.error(
            f"[RENDER SKIPPED] Text box size exceeds OpenCV limit (32767). "
            f"box={box.shape[:2]}, text='{_translation_preview(getattr(region, 'translation', None), 50)}...'"
        )
        return img
    
    # 计算文字区域的边界框，添加边距
    x_adj, y_adj, w_adj, h_adj = cv2.boundingRect(np.round(dst_points[0]).astype(np.int32))
    margin = max(w_adj, h_adj) // 2 + 100  # 添加足够的边距
    
    # 计算局部区域边界
    local_x1 = max(0, x_adj - margin)
    local_y1 = max(0, y_adj - margin)
    local_x2 = min(img_w, x_adj + w_adj + margin)
    local_y2 = min(img_h, y_adj + h_adj + margin)
    local_w = local_x2 - local_x1
    local_h = local_y2 - local_y1

    if local_w <= 0 or local_h <= 0:
        logger.warning(
            f"Text region completely outside image bounds: x={x_adj}, y={y_adj}, "
            f"w={w_adj}, h={h_adj}, image_size=({img_w}, {img_h}). "
            f"Text: '{_translation_preview(getattr(region, 'translation', None), 50)}...'"
        )
        return img
    
    # 检查局部区域是否仍然超限
    if local_w > SHRT_MAX or local_h > SHRT_MAX:
        logger.error(
            f"[RENDER SKIPPED] Local region still exceeds OpenCV limit. "
            f"local_size=({local_w}, {local_h}), text='{_translation_preview(getattr(region, 'translation', None), 50)}...'"
        )
        return img
    
    # 调整目标点到局部坐标系
    local_dst_points = dst_points.copy()
    local_dst_points[0, :, 0] -= local_x1
    local_dst_points[0, :, 1] -= local_y1
    
    # 重新计算变换矩阵
    M_local, _ = cv2.findHomography(src_points, local_dst_points[0], cv2.RANSAC, 5.0)

    # 检查变换矩阵是否有效
    if M_local is None:
        logger.warning(f"[RENDER SKIPPED] Failed to compute homography matrix for text: "
                      f"'{_translation_preview(getattr(region, 'translation', None), 50)}...'")
        return img

    # 在局部区域进行变换
    # 关键修复 1：先做 Alpha 预乘，避免 warpPerspective 的插值把边缘像素和
    # 透明黑 (0,0,0,0) 混合成灰/黑色晕边。
    # 关键修复 2：使用 INTER_LINEAR 而非 INTER_LANCZOS4。Lanczos4 的 sinc 负
    # lobe 在预乘空间会把"远处的透明黑"按负权重减出来，且 RGB 与 alpha 振铃
    # 量不成比例 —— 反预乘还原后，描边外缘出现 ~5% 的灰（约 #f9f9f9）。
    # 双线性无负 lobe，描边边缘保持纯白；漫画文字大字号缩放下肉眼看不出锐度差异。
    if box.shape[2] == 4:
        box_pm = box.copy()
        a_f = box_pm[:, :, 3].astype(np.float32) / 255.0
        for c in range(3):
            box_pm[:, :, c] = np.clip(box_pm[:, :, c].astype(np.float32) * a_f, 0, 255).astype(np.uint8)
    else:
        box_pm = box

    rgba_region = cv2.warpPerspective(box_pm, M_local, (local_w, local_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    # 计算在局部区域中的有效范围
    local_text_x = x_adj - local_x1
    local_text_y = y_adj - local_y1
    valid_y1 = max(0, local_text_y)
    valid_y2 = min(local_h, local_text_y + h_adj)
    valid_x1 = max(0, local_text_x)
    valid_x2 = min(local_w, local_text_x + w_adj)
    
    if valid_y2 > valid_y1 and valid_x2 > valid_x1:
        # canvas_region 已经是预乘的 RGB；直接使用预乘合成公式：
        #   out = target*(1-a) + src_premul
        # 不再除以 alpha 还原，避免边缘像素（alpha 很小）在还原时放大数值噪声
        canvas_region = rgba_region[valid_y1:valid_y2, valid_x1:valid_x2, :3]
        mask_region = rgba_region[valid_y1:valid_y2, valid_x1:valid_x2, 3:4].astype(np.float32) / 255.0
        
        # 计算在原图中的对应位置
        img_target_y1 = local_y1 + valid_y1
        img_target_y2 = local_y1 + valid_y2
        img_target_x1 = local_x1 + valid_x1
        img_target_x2 = local_x1 + valid_x2
        
        target_region = img[img_target_y1:img_target_y2, img_target_x1:img_target_x2]
        if canvas_region.shape[:2] == target_region.shape[:2]:
            img[img_target_y1:img_target_y2, img_target_x1:img_target_x2] = np.clip(
                target_region.astype(np.float32) * (1.0 - mask_region) + canvas_region.astype(np.float32),
                0, 255
            ).astype(np.uint8)
            if render_alpha is not None:
                try:
                    alpha_region = rgba_region[valid_y1:valid_y2, valid_x1:valid_x2, 3]
                    alpha_target = render_alpha[img_target_y1:img_target_y2, img_target_x1:img_target_x2]
                    if alpha_region.shape == alpha_target.shape:
                        np.maximum(alpha_target, alpha_region, out=alpha_target)
                except Exception as alpha_error:
                    logger.debug(f"Failed to accumulate render alpha: {alpha_error}")
        else:
            logger.warning(f"Text region size mismatch: canvas={canvas_region.shape[:2]}, target={target_region.shape[:2]}, skipping region")
    else:
        logger.warning(
            f"Text region completely outside image bounds: x={x_adj}, y={y_adj}, "
            f"w={w_adj}, h={h_adj}, image_size=({img_w}, {img_h}). "
            f"Text: '{_translation_preview(getattr(region, 'translation', None), 50)}...'"
        )
    
    return img
