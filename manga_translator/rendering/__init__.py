import math
import os
import re
import copy
import threading
import cv2
# import logging
import numpy as np
from typing import List, Optional, Tuple
from shapely import affinity
from shapely.geometry import Polygon
from tqdm import tqdm

from . import text_render
from . import text_render_hq
from .auto_linebreak import solve_no_br_layout
from .text_render_eng import render_textblock_list_eng
from .text_render_pillow_eng import render_textblock_list_eng as render_textblock_list_eng_pillow

# 只使用 freetype 渲染器（稳定可靠）
from ..utils import (
    BASE_PATH,
    TextBlock,
    build_bubble_mask_from_mangalens_result,
    color_difference,
    fg_bg_compare,
    get_cached_bubbles_with_mangalens,
    get_logger,
    rotate_polygons,
)
from ..config import Config, Renderer

logger = get_logger('render')

# FreeType 的 Face/Glyph 状态和当前文本渲染缓存是全局共享的。
# 在整图并发开启后，多条渲染线程会同时操作这些共享对象，容易触发 invalid outline/reference。
# 先把本地字体渲染串行化，恢复到并发改造前的稳定行为。
_FREETYPE_RENDER_LOCK = threading.RLock()

# 基准字体大小，用于模拟文本块
BASE_FONT_SIZE = 100

def _resolve_font_path(font_path: str) -> str:
    """Resolve font path from absolute/relative/project-fonts path.

    When an absolute path does not exist on the current machine (e.g., saved
    path from a different install directory), falls back to searching by
    filename inside the project fonts/ directory.
    """
    if not font_path:
        return ''
    if os.path.exists(font_path):
        return font_path

    # 路径不存在时（含绝对路径盘符/目录不同的情况），用文件名在 fonts/ 目录里找
    font_basename = os.path.basename(font_path)
    candidate = os.path.join(BASE_PATH, 'fonts', font_basename)
    if os.path.exists(candidate):
        return candidate

    if not os.path.isabs(font_path):
        candidate = os.path.join(BASE_PATH, font_path)
        if os.path.exists(candidate):
            return candidate

    return ''


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def calc_text_block_dimensions(text: str, is_horizontal: bool, line_spacing: float = 1.0,
                                config: Config = None, target_lang: str = None,
                                font_size: int = BASE_FONT_SIZE, letter_spacing: float = 1.0) -> tuple:
    """
    按指定字号模拟渲染文本块，返回精确的像素尺寸

    复用后端渲染的尺寸计算逻辑，保证和实际渲染一致。

    Args:
        text: 文本内容（支持 [BR]、<H> 等标记）
        is_horizontal: True=横排，False=竖排
        line_spacing: 行间距倍率
        config: 配置对象
        target_lang: 目标语言

    Returns:
        (base_width, base_height, n_lines) - 基准尺寸和行/列数
    """
    base_font = max(1, int(font_size))

    # 先做特殊符号规范化（省略号等），保证排版和渲染阶段文本一致
    text = text_render.compact_special_symbols(text)
    # 处理 BR 标记
    text_for_calc = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', text, flags=re.IGNORECASE)

    if is_horizontal:
        lines, widths = text_render.calc_horizontal(
            base_font, text_for_calc,
            max_width=99999, max_height=99999,
            language=target_lang or 'en_US',
            letter_spacing=letter_spacing
        )
        if widths:
            spacing_y = int(base_font * 0.01 * line_spacing)
            # 和后端渲染一致：最大行宽 + 行间距
            base_width = max(widths)
            base_height = base_font * len(lines) + spacing_y * max(0, len(lines) - 1)
            return base_width, base_height, len(lines)
    else:
        # 竖排：应用自动旋转符号
        if config and hasattr(config, 'render') and config.render.auto_rotate_symbols:
            text_for_calc = text_render.auto_add_horizontal_tags(text_for_calc)

        lines, heights = text_render.calc_vertical(
            base_font, text_for_calc,
            max_height=99999, config=config, letter_spacing=letter_spacing
        )
        if heights:
            spacing_x = int(base_font * 0.2 * line_spacing)

            # 和后端渲染一致：计算每列的实际宽度
            line_widths = []
            for line_text in lines:
                max_width = base_font
                parts = re.split(r'(<H>.*?</H>)', line_text, flags=re.IGNORECASE | re.DOTALL)
                for part in parts:
                    if not part:
                        continue
                    is_horizontal_block = part.lower().startswith('<h>') and part.lower().endswith('</h>')
                    if not is_horizontal_block:
                        for c in part:
                            cdpt, _ = text_render.CJK_Compatibility_Forms_translate(c, 1)
                            slot = text_render.get_char_glyph(cdpt, base_font, 1)
                            if slot.bitmap.width > max_width:
                                max_width = slot.bitmap.width
                line_widths.append(max_width)

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

    text = (text or '').strip()
    if not text:
        return 1

    def _fits(fs: int) -> bool:
        req_w, req_h, _ = calc_box_from_font(
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


def calc_box_from_font(font_size: int, text: str, is_horizontal: bool,
                       line_spacing: float = 1.0, config: Config = None,
                       target_lang: str = None, center: tuple = None,
                       angle: float = 0, letter_spacing: float = 1.0) -> tuple:
    """
    字体 → 框：直接按目标字号测量文本像素尺寸

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
        如果 center 为 None: (required_width, required_height, n_lines)
        如果 center 不为 None: dst_points (shape: (1, 4, 2)) - 可直接用于绿框
    """
    font_size = max(1, int(font_size))
    base_w, base_h, n_lines = calc_text_block_dimensions(
        text, is_horizontal, line_spacing, config, target_lang, font_size=font_size, letter_spacing=letter_spacing
    )

    if base_w <= 0 or base_h <= 0:
        if center is not None:
            return None
        return 0, 0, 0

    # 直接按目标字号测量，不再做基准字号线性缩放
    req_width = math.ceil(base_w)
    req_height = math.ceil(base_h)

    # 计入效果边距，避免“字号不变但框太小导致视觉缩字”
    effect_padding = _estimate_effect_padding(font_size, config)
    if effect_padding > 0.0:
        req_width += int(effect_padding * 2.0)
        req_height += int(effect_padding * 2.0)

    # 如果没有提供中心点，返回尺寸
    if center is None:
        return req_width, req_height, n_lines

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

    # 应用旋转
    if angle != 0:
        dst_points = rotate_polygons(
            center, unrotated_points.reshape(1, -1),
            -angle, to_int=False
        ).reshape(-1, 4, 2)
    else:
        dst_points = unrotated_points.reshape(-1, 4, 2)

    return dst_points

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
                if config.render.auto_rotate_symbols:
                    text_for_calc = text_render.auto_add_horizontal_tags(text_for_calc)
                
                lines, heights = text_render.calc_vertical(target_font_size, text_for_calc, max_height=99999, letter_spacing=letter_spacing_multiplier)
                if heights:
                    spacing_x = int(target_font_size * 0.2 * line_spacing_multiplier)
                    required_height = max(heights)
                    required_width = target_font_size * len(lines) + spacing_x * max(0, len(lines) - 1)
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
    
    # 标准化原文以便比较（只用于判断是否真的有改变）
    _original_normalized = re.sub(r'\s*(<br>|【BR】)\s*', '[BR]', original_translation, flags=re.IGNORECASE)
    
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


def _apply_final_font_constraints(layout_font_size: int, config: Config) -> int:
    final_font_size = max(int(layout_font_size), 1)
    render_cfg = getattr(config, 'render', None) if config is not None else None

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


def _calc_region_dst_points_for_font(
    region: TextBlock,
    font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
) -> Optional[np.ndarray]:
    return calc_box_from_font(
        int(max(font_size, 1)),
        region.translation,
        render_horizontally,
        line_spacing_multiplier,
        config,
        region.target_lang,
        center=tuple(region.center),
        angle=region.angle,
        letter_spacing=letter_spacing_multiplier,
    )


def _font_size_fits_bubble_mask(
    region: TextBlock,
    font_size: int,
    render_horizontally: bool,
    line_spacing_multiplier: float,
    letter_spacing_multiplier: float,
    config: Config,
    bubble_mask: np.ndarray,
) -> Tuple[bool, Optional[np.ndarray]]:
    dst_points = _calc_region_dst_points_for_font(
        region=region,
        font_size=font_size,
        render_horizontally=render_horizontally,
        line_spacing_multiplier=line_spacing_multiplier,
        letter_spacing_multiplier=letter_spacing_multiplier,
        config=config,
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
        )
        if fits:
            best_font = mid
            best_dst_points = dst_points
            lo = mid + 1
        else:
            hi = mid - 1

    return best_font, best_dst_points


def resize_regions_to_font_size(img: np.ndarray, text_regions: List['TextBlock'], config: Config, original_img: np.ndarray = None, return_debug_img: bool = False, skip_font_scaling: bool = False):
    """
    Resize text regions based on layout mode.

    Args:
        return_debug_img: If True, returns (dst_points_list, debug_img) for balloon_fill mode
        skip_font_scaling: If True, skip font scaling algorithm and use font_size from region directly (for load_text mode)
    """
    mode = config.render.layout_mode
    
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

    dst_points_list = []
    for region_idx, region in enumerate(text_regions):
        if region is None:
            logger.info(f"[RESIZE] 区域 {region_idx}: None，跳过")
            dst_points_list.append(None)
            continue
        if config:
            config._current_region = region

        # 如果 translation 为空,直接返回 min_rect,避免触发复杂的布局计算
        if not region.translation or not region.translation.strip():
            logger.info(f"[RESIZE] 区域 {region_idx}: translation 为空，使用 min_rect")
            dst_points_list.append(region.min_rect)
            continue

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

            # 用区域自己的字体来量度字符宽度，保证和 render() 实际渲染时一致
            resolved_region_font_path = _resolve_font_path(getattr(region, 'font_path', '') or '')
            if resolved_region_font_path:
                text_render.set_font(resolved_region_font_path)
            else:
                text_render.set_font(text_render.DEFAULT_FONT)

            dst_points = calc_box_from_font(
                fixed_font_size, region.translation, actual_horizontal,
                line_spacing_multiplier, config, region.target_lang,
                center=tuple(region.center), angle=region.angle,
                letter_spacing=letter_spacing_multiplier
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
            target_font_size = max(original_region_font_size, layout_min_font_size)

            # 保存布局算法的基础字号；最终字号会在后置约束中统一应用 offset/scale/min/max
            region.layout_base_font_size = int(target_font_size)

        # --- Mode 5: balloon_fill (MUST BE FIRST to override other modes) ---
        if mode == 'balloon_fill':
            logger.debug(f"=== balloon_fill mode activated for region {region_idx} ===")
            logger.debug(f"OCR box (xywh): {region.xywh}")
            configured_min_font_size = _resolve_configured_min_font_size(config)
            min_font_size = max(configured_min_font_size if configured_min_font_size > 0 else 1, 1)

            if original_img is None:
                logger.warning("balloon_fill mode requires original_img, fallback to geometry-based dst_points")
                fallback_font_size = _apply_final_font_constraints(target_font_size, config)
                fallback_dst_points = _calc_region_dst_points_for_font(
                    region=region,
                    font_size=fallback_font_size,
                    render_horizontally=_resolve_region_render_horizontal(region),
                    line_spacing_multiplier=_resolve_line_spacing_multiplier(region, config),
                    letter_spacing_multiplier=_resolve_letter_spacing_multiplier(region, config),
                    config=config,
                )
                if fallback_dst_points is None:
                    fallback_dst_points = region.min_rect
                region.font_size = fallback_font_size
                dst_points_list.append(fallback_dst_points)
                continue

            try:
                render_horizontally = _resolve_region_render_horizontal(region)
                line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
                letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)

                has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))
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
                    )
                    if isinstance(smart_result, list) and len(smart_result) > 0:
                        chosen_dst_points = smart_result[0]
                    if chosen_dst_points is None:
                        chosen_dst_points = region.min_rect
                    chosen_font_size = region.font_size if region.font_size > 0 else chosen_font_size
                    logger.debug(f"balloon_fill region {region_idx}: not fully enclosed, fallback to smart_scaling")
                else:
                    bubble_width, bubble_height = region.unrotated_size
                    mask_nonzero = cv2.findNonZero(region_bubble_mask)
                    if mask_nonzero is not None:
                        _, _, bubble_width, bubble_height = cv2.boundingRect(mask_nonzero)
                    if not (isinstance(bubble_width, (int, float)) and np.isfinite(bubble_width) and bubble_width > 0):
                        bubble_width = float(max(region.xywh[2], 1))
                    if not (isinstance(bubble_height, (int, float)) and np.isfinite(bubble_height) and bubble_height > 0):
                        bubble_height = float(max(region.xywh[3], 1))

                    layout_target_font_size = int(target_font_size)
                    # 对所有 balloon_fill(enclosed) 场景先做一次“框->字体”估算：
                    # 若估算字号更大，则提升目标字号上限，避免被输入字号卡死。
                    estimated_font_from_box = calc_font_from_box(
                        width=float(bubble_width),
                        height=float(bubble_height),
                        text=region.translation,
                        is_horizontal=render_horizontally,
                        line_spacing=line_spacing_multiplier,
                        config=config,
                        target_lang=region.target_lang,
                        letter_spacing=letter_spacing_multiplier,
                    )
                    if estimated_font_from_box > layout_target_font_size:
                        logger.debug(
                            f"balloon_fill region {region_idx}: boost layout_target_font_size "
                            f"{layout_target_font_size}->{estimated_font_from_box} (calc_font_from_box)"
                        )
                        layout_target_font_size = int(estimated_font_from_box)

                    if not has_br:
                        no_br_max_font_size = int(layout_target_font_size)
                        if render_horizontally:
                            total_width = text_render.get_string_width(
                                layout_target_font_size,
                                region.translation,
                                letter_spacing=letter_spacing_multiplier,
                            )
                            spacing_y = int(layout_target_font_size * 0.01 * line_spacing_multiplier)
                            ratio = bubble_width / bubble_height if bubble_height > 0 else 1.0

                            a = layout_target_font_size + spacing_y
                            b = -spacing_y
                            c = -total_width / ratio if ratio > 0 else -total_width

                            discriminant = b * b - 4 * a * c
                            if discriminant >= 0 and a > 0:
                                n_float = (-b + np.sqrt(discriminant)) / (2 * a)
                                n_floor = max(1, int(np.floor(n_float)))
                                n_ceil = max(1, int(np.ceil(n_float)))
                            else:
                                n_floor = n_ceil = 1

                            def calc_max_font_horizontal(n, total_w, bw, bh, lsm, target_fs):
                                height_factor = n + (n - 1) * 0.01 * lsm
                                max_by_height = int(bh / height_factor) if height_factor > 0 else target_fs
                                max_by_width = int(bw * n * target_fs / total_w) if total_w > 0 else target_fs
                                return min(max_by_height, max_by_width)

                            font_floor = calc_max_font_horizontal(
                                n_floor, total_width, bubble_width, bubble_height, line_spacing_multiplier, layout_target_font_size
                            )
                            font_ceil = calc_max_font_horizontal(
                                n_ceil, total_width, bubble_width, bubble_height, line_spacing_multiplier, layout_target_font_size
                            )

                            if font_floor >= font_ceil:
                                seed_segments = n_floor
                                seed_font_size = font_floor
                            else:
                                seed_segments = n_ceil
                                seed_font_size = font_ceil
                        else:
                            total_height = text_render.get_string_height(
                                layout_target_font_size,
                                region.translation,
                                letter_spacing=letter_spacing_multiplier,
                            )
                            spacing_x = int(layout_target_font_size * 0.2 * line_spacing_multiplier)
                            ratio = bubble_width / bubble_height if bubble_height > 0 else 1.0

                            a = layout_target_font_size + spacing_x
                            b = -spacing_x
                            c = -total_height * ratio

                            discriminant = b * b - 4 * a * c
                            if discriminant >= 0 and a > 0:
                                n_float = (-b + np.sqrt(discriminant)) / (2 * a)
                                n_floor = max(1, int(np.floor(n_float)))
                                n_ceil = max(1, int(np.ceil(n_float)))
                            else:
                                n_floor = n_ceil = 1

                            def calc_max_font_vertical(n, total_h, bw, bh, lsm, target_fs):
                                width_factor = n + (n - 1) * 0.2 * lsm
                                max_by_width = int(bw / width_factor) if width_factor > 0 else target_fs
                                max_by_height = int(bh * n * target_fs / total_h) if total_h > 0 else target_fs
                                return min(max_by_width, max_by_height)

                            font_floor = calc_max_font_vertical(
                                n_floor, total_height, bubble_width, bubble_height, line_spacing_multiplier, layout_target_font_size
                            )
                            font_ceil = calc_max_font_vertical(
                                n_ceil, total_height, bubble_width, bubble_height, line_spacing_multiplier, layout_target_font_size
                            )

                            if font_floor >= font_ceil:
                                seed_segments = n_floor
                                seed_font_size = font_floor
                            else:
                                seed_segments = n_ceil
                                seed_font_size = font_ceil

                        seed_font_size = int(max(min(seed_font_size, layout_target_font_size), layout_min_font_size))
                        no_br_result = solve_no_br_layout(
                            text=region.translation,
                            horizontal=render_horizontally,
                            seed_segments=seed_segments,
                            seed_font_size=seed_font_size,
                            bubble_width=bubble_width,
                            bubble_height=bubble_height,
                            min_font_size=layout_min_font_size,
                            max_font_size=no_br_max_font_size,
                            line_spacing_multiplier=line_spacing_multiplier,
                            letter_spacing_multiplier=letter_spacing_multiplier,
                            target_lang=region.target_lang,
                            config=config,
                        )
                        region.translation = no_br_result.text_with_br
                        layout_target_font_size = int(no_br_result.font_size)
                        logger.debug(
                            f"balloon_fill region {region_idx}: no_br layout merged, segments={no_br_result.n_segments}, "
                            f"font={layout_target_font_size}, required={no_br_result.required_width:.1f}x{no_br_result.required_height:.1f}"
                        )

                    preferred_font_size = _apply_final_font_constraints(layout_target_font_size, config)
                    preferred_font_size_for_debug = preferred_font_size

                    # 调试用途：记录“超出范围候选框”（较大字号候选但不满足蒙版约束）
                    preferred_dst_points = _calc_region_dst_points_for_font(
                        region=region,
                        font_size=preferred_font_size,
                        render_horizontally=render_horizontally,
                        line_spacing_multiplier=line_spacing_multiplier,
                        letter_spacing_multiplier=letter_spacing_multiplier,
                        config=config,
                    )
                    if preferred_dst_points is not None and preferred_dst_points.size > 0:
                        preferred_fits = _polygon_fully_inside_mask(np.asarray(preferred_dst_points[0]), region_bubble_mask)
                        if not preferred_fits:
                            overflow_candidate_dst_points = preferred_dst_points

                    best_font_size, best_dst_points = _binary_search_font_for_bubble_mask(
                        region=region,
                        start_font_size=preferred_font_size,
                        min_font_size=min_font_size,
                        render_horizontally=render_horizontally,
                        line_spacing_multiplier=line_spacing_multiplier,
                        letter_spacing_multiplier=letter_spacing_multiplier,
                        config=config,
                        bubble_mask=region_bubble_mask,
                    )
                    if best_font_size is not None and best_dst_points is not None:
                        chosen_font_size = int(best_font_size)
                        chosen_dst_points = best_dst_points
                        logger.debug(
                            f"balloon_fill region {region_idx}: enclosed lines, binary-search font {preferred_font_size}->{chosen_font_size}"
                        )
                    else:
                        chosen_font_size = preferred_font_size
                        chosen_dst_points = preferred_dst_points
                        if chosen_dst_points is None:
                            chosen_dst_points = _calc_region_dst_points_for_font(
                                region=region,
                                font_size=chosen_font_size,
                                render_horizontally=render_horizontally,
                                line_spacing_multiplier=line_spacing_multiplier,
                                letter_spacing_multiplier=letter_spacing_multiplier,
                                config=config,
                            )
                        if min_font_size > 1:
                            logger.debug(
                                f"balloon_fill region {region_idx}: no mask-safe layout at configured min font "
                                f"{min_font_size}, keeping preferred_font_size={chosen_font_size}"
                            )
                        else:
                            logger.debug(
                                f"balloon_fill region {region_idx}: no mask-safe layout found, keeping preferred_font_size={chosen_font_size}"
                            )

                if chosen_dst_points is None:
                    chosen_dst_points = region.min_rect

                region.font_size = int(max(chosen_font_size, 1))
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
                logger.error(f"Error in balloon_fill mode: {e}")
                import traceback
                logger.error(traceback.format_exc())
                dst_points_list.append(region.min_rect)
                region.font_size = target_font_size

            continue

        # --- Mode: strict ---
        if mode == 'strict':
            # Optimize line breaks if enabled
            has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))
            if config.render.optimize_line_breaks and config.render.disable_auto_wrap and has_br:
                bubble_width, bubble_height = region.unrotated_size
                logger.debug("[OPTIMIZE] Optimizing line breaks for strict mode")
                optimized_text, _ = optimize_line_breaks_for_region(
                    region, config, target_font_size, bubble_width, bubble_height
                )
                region.translation = optimized_text
                logger.debug(f"[OPTIMIZE] Optimized text: {region.translation}")
            
            font_size = target_font_size
            min_shrink_font_size = 8
            letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
            render_horizontally = _resolve_region_render_horizontal(region)

            # AI 断句适配：如果开启了 AI 断句且有 BR 标记，使用无限宽度/高度
            
            # 检测是否为替换翻译模式
            is_replace_mode = config.cli.replace_translation if (config and hasattr(config, 'cli')) else False
            
            # 增强逻辑：如果是替换翻译模式 + 严格模式，且OCR检测为单行（len(region.lines) == 1）
            # 强制使用无限宽度（不换行），以复刻原图的单行结构
            is_single_line = len(region.lines) == 1
            force_single_line_no_wrap = is_replace_mode and is_single_line
            
            use_ai_break = (config.render.disable_auto_wrap and has_br) or force_single_line_no_wrap
            
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

            while test_font_size <= target_font_size:
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
            total_font_scale = final_font_size / max(layout_font_size, 1)

            # Scale region to match final font size
            dst_points = region.min_rect
            if total_font_scale != 1.0:
                try:
                    poly = Polygon(region.unrotated_min_rect[0])
                    scaled_poly = affinity.scale(poly, xfact=total_font_scale, yfact=total_font_scale, origin='center')
                    scaled_points = np.array(scaled_poly.exterior.coords[:4])
                    dst_points = rotate_polygons(region.center, scaled_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
                except Exception as e:
                    logger.warning(f"Failed to scale region for font_scale_ratio: {e}")

            region.font_size = final_font_size
            dst_points_list.append(dst_points)
            continue

        # --- Mode: smart_scaling ---
        elif mode == 'smart_scaling':
            # Check if text contains [BR] markers
            has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))

            # 添加诊断日志
            logger.debug(f"[SMART_SCALING] Region {region_idx}: mode={mode}, has_br={has_br}")

            # --- UNIFIED ALGORITHM: 只分 has_br 和 no_br 两种情况 ---
            try:
                # Calculate required dimensions using current font size (fixed layout)
                bubble_width, bubble_height = region.unrotated_size

                # Defensive check for invalid bubble sizes
                if not (isinstance(bubble_width, (int, float)) and np.isfinite(bubble_width) and bubble_width > 0 and
                        isinstance(bubble_height, (int, float)) and np.isfinite(bubble_height) and bubble_height > 0):
                    logger.warning(f"Invalid bubble size for region: w={bubble_width}, h={bubble_height}. Skipping smart scaling for this region.")
                    dst_points_list.append(region.min_rect)
                    final_font_size = _apply_final_font_constraints(target_font_size, config)
                    region.font_size = final_font_size
                    continue

                # Create base polygon for scaling
                try:
                    unrotated_base_poly = Polygon(region.unrotated_min_rect[0])
                except Exception:
                    unrotated_base_poly = Polygon([(0, 0), (bubble_width, 0), (bubble_width, bubble_height), (0, bubble_height)])

                # Optimize line breaks if enabled
                has_br = bool(re.search(r'(\[BR\]|【BR】|<br>)', region.translation, flags=re.IGNORECASE))
                logger.debug(f"[SMART_SCALING] Region {region_idx}: has_br={has_br}, translation='{region.translation[:30]}...'")
                if config.render.optimize_line_breaks and has_br:
                    optimized_text, _ = optimize_line_breaks_for_region(
                        region, config, target_font_size, bubble_width, bubble_height
                    )
                    region.translation = optimized_text

                # n 表示行/列数量，后续溢出重算会统一使用；默认 1 防止未赋值。
                n = 1
                line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
                letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
                render_horizontally = _resolve_region_render_horizontal(region)

                # 根据有没有BR选择不同的计算方式
                if has_br:
                    logger.debug(f"[SMART_SCALING] Region {region_idx}: 有BR分支")
                    # 有BR：用无限宽度，按BR换行
                    required_width = 0
                    required_height = 0

                    if render_horizontally:
                        lines, widths = text_render.calc_horizontal(
                            target_font_size,
                            region.translation,
                            max_width=99999,
                            max_height=99999,
                            language=region.target_lang,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        n = max(1, len(lines))
                        if widths:
                            spacing_y = int(target_font_size * 0.01 * line_spacing_multiplier)
                            required_width = max(widths)
                            required_height = target_font_size * n + spacing_y * max(0, n - 1)
                    else: # Vertical
                        # Convert [BR] tags to \n for vertical text
                        text_for_calc = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', region.translation, flags=re.IGNORECASE)

                        # Apply auto_add_horizontal_tags if enabled
                        if config.render.auto_rotate_symbols:
                            text_for_calc = text_render.auto_add_horizontal_tags(text_for_calc)

                        lines, heights = text_render.calc_vertical(
                            target_font_size,
                            text_for_calc,
                            max_height=99999,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        n = max(1, len(lines))
                        if heights:
                            spacing_x = int(target_font_size * 0.2 * line_spacing_multiplier)
                            required_height = max(heights)
                            required_width = target_font_size * n + spacing_x * max(0, n - 1)
                else:
                    logger.debug(f"[SMART_SCALING] Region {region_idx}: 无BR分支，开始反推断句")
                    # 无BR：用精确像素反推最优行数/列数
                    no_br_max_font_size = target_font_size

                    if render_horizontally:
                        # 横排：计算单行总宽度
                        total_width = text_render.get_string_width(
                            target_font_size,
                            region.translation,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        spacing_y = int(target_font_size * 0.01 * line_spacing_multiplier)
                        ratio = bubble_width / bubble_height if bubble_height > 0 else 1.0

                        # 二次方程反推行数
                        a = target_font_size + spacing_y
                        b = -spacing_y
                        c = -total_width / ratio if ratio > 0 else -total_width

                        discriminant = b * b - 4 * a * c
                        if discriminant >= 0 and a > 0:
                            n_float = (-b + np.sqrt(discriminant)) / (2 * a)
                            n_floor = max(1, int(np.floor(n_float)))
                            n_ceil = max(1, int(np.ceil(n_float)))
                        else:
                            n_floor = n_ceil = 1

                        # 分别计算两个n对应的最大字体，选字体大的
                        def calc_max_font_horizontal(n, total_w, bw, bh, lsm, target_fs):
                            height_factor = n + (n - 1) * 0.01 * lsm
                            max_by_height = int(bh / height_factor) if height_factor > 0 else target_fs
                            max_by_width = int(bw * n * target_fs / total_w) if total_w > 0 else target_fs
                            return min(max_by_height, max_by_width)

                        font_floor = calc_max_font_horizontal(n_floor, total_width, bubble_width, bubble_height, line_spacing_multiplier, target_font_size)
                        font_ceil = calc_max_font_horizontal(n_ceil, total_width, bubble_width, bubble_height, line_spacing_multiplier, target_font_size)

                        # 选字体大的那个
                        if font_floor >= font_ceil:
                            n = n_floor
                            final_font_size = font_floor
                        else:
                            n = n_ceil
                            final_font_size = font_ceil

                        final_font_size = min(final_font_size, target_font_size)
                        final_font_size = max(final_font_size, layout_min_font_size)

                        # 用最终字体重新计算精确的required尺寸
                        final_total_width = text_render.get_string_width(
                            final_font_size,
                            region.translation,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        final_spacing_y = int(final_font_size * 0.01 * line_spacing_multiplier)
                        required_width = final_total_width / n
                        required_height = n * final_font_size + max(0, n - 1) * final_spacing_y

                        target_font_size = final_font_size
                        logger.debug(f"[SMART_SCALING DEBUG] No BR Horizontal: n={n}, final_font={final_font_size}, required={required_width:.1f}x{required_height:.1f}")

                        no_br_result = solve_no_br_layout(
                            text=region.translation,
                            horizontal=True,
                            seed_segments=n,
                            seed_font_size=final_font_size,
                            bubble_width=bubble_width,
                            bubble_height=bubble_height,
                            min_font_size=layout_min_font_size,
                            max_font_size=no_br_max_font_size,
                            line_spacing_multiplier=line_spacing_multiplier,
                            letter_spacing_multiplier=letter_spacing_multiplier,
                            target_lang=region.target_lang,
                            config=config,
                        )
                        region.translation = no_br_result.text_with_br
                        target_font_size = no_br_result.font_size
                        n = no_br_result.n_segments
                        required_width = no_br_result.required_width
                        required_height = no_br_result.required_height
                        logger.debug(
                            f"[SMART_SCALING] Region {region_idx}: 横排融合断句后 n={n}, "
                            f"font={target_font_size}, required={required_width:.1f}x{required_height:.1f}"
                        )
                    else: # Vertical
                        # 竖排：计算单列总高度
                        total_height = text_render.get_string_height(
                            target_font_size,
                            region.translation,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        spacing_x = int(target_font_size * 0.2 * line_spacing_multiplier)
                        ratio = bubble_width / bubble_height if bubble_height > 0 else 1.0

                        # 二次方程反推列数
                        a = target_font_size + spacing_x
                        b = -spacing_x
                        c = -total_height * ratio

                        discriminant = b * b - 4 * a * c
                        if discriminant >= 0 and a > 0:
                            n_float = (-b + np.sqrt(discriminant)) / (2 * a)
                            n_floor = max(1, int(np.floor(n_float)))
                            n_ceil = max(1, int(np.ceil(n_float)))
                        else:
                            n_floor = n_ceil = 1

                        # 分别计算两个n对应的最大字体，选字体大的
                        def calc_max_font_vertical(n, total_h, bw, bh, lsm, target_fs):
                            width_factor = n + (n - 1) * 0.2 * lsm
                            max_by_width = int(bw / width_factor) if width_factor > 0 else target_fs
                            max_by_height = int(bh * n * target_fs / total_h) if total_h > 0 else target_fs
                            return min(max_by_width, max_by_height)

                        font_floor = calc_max_font_vertical(n_floor, total_height, bubble_width, bubble_height, line_spacing_multiplier, target_font_size)
                        font_ceil = calc_max_font_vertical(n_ceil, total_height, bubble_width, bubble_height, line_spacing_multiplier, target_font_size)

                        # 选字体大的那个
                        if font_floor >= font_ceil:
                            n = n_floor
                            final_font_size = font_floor
                        else:
                            n = n_ceil
                            final_font_size = font_ceil

                        final_font_size = min(final_font_size, target_font_size)
                        final_font_size = max(final_font_size, layout_min_font_size)

                        # 用最终字体重新计算精确的required尺寸
                        final_total_height = text_render.get_string_height(
                            final_font_size,
                            region.translation,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        final_spacing_x = int(final_font_size * 0.2 * line_spacing_multiplier)
                        required_height = final_total_height / n
                        required_width = n * final_font_size + max(0, n - 1) * final_spacing_x

                        target_font_size = final_font_size
                        logger.debug(f"[SMART_SCALING DEBUG] No BR Vertical: n={n}, final_font={final_font_size}, required={required_width:.1f}x{required_height:.1f}")

                        no_br_result = solve_no_br_layout(
                            text=region.translation,
                            horizontal=False,
                            seed_segments=n,
                            seed_font_size=final_font_size,
                            bubble_width=bubble_width,
                            bubble_height=bubble_height,
                            min_font_size=layout_min_font_size,
                            max_font_size=no_br_max_font_size,
                            line_spacing_multiplier=line_spacing_multiplier,
                            letter_spacing_multiplier=letter_spacing_multiplier,
                            target_lang=region.target_lang,
                            config=config,
                        )
                        region.translation = no_br_result.text_with_br
                        target_font_size = no_br_result.font_size
                        n = no_br_result.n_segments
                        required_width = no_br_result.required_width
                        required_height = no_br_result.required_height
                        logger.debug(
                            f"[SMART_SCALING] Region {region_idx}: 竖排融合断句后 n={n}, "
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
                        final_total_height = text_render.get_string_height(
                            target_font_size,
                            region.translation,
                            letter_spacing=letter_spacing_multiplier,
                        )
                        final_spacing_x = int(target_font_size * 0.2 * line_spacing_multiplier)
                        required_height = final_total_height / n if n > 0 else final_total_height
                        required_width = n * target_font_size + max(0, n - 1) * final_spacing_x

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
                logger.error(f"Error in new smart_scaling algorithm: {e}")
                # Fallback to a safe state
                target_font_size = getattr(region, 'layout_base_font_size', target_font_size)
                dst_points = region.min_rect

            # Apply final post-layout constraints: offset, scale ratio, and min/max clamps.
            final_font_size = _apply_final_font_constraints(target_font_size, config)

            # 用辅助函数直接计算 dst_points（包含矩形构建和旋转）
            line_spacing_multiplier = _resolve_line_spacing_multiplier(region, config)
            letter_spacing_multiplier = _resolve_letter_spacing_multiplier(region, config)
            dst_points = calc_box_from_font(
                final_font_size, region.translation, render_horizontally,
                line_spacing_multiplier, config, region.target_lang,
                center=tuple(region.center), angle=region.angle,
                letter_spacing=letter_spacing_multiplier,
            )

            # 如果计算失败，使用原始检测框
            if dst_points is None:
                dst_points = region.min_rect

            region.font_size = final_font_size
            dst_points_list.append(dst_points)
            continue

        # --- Fallback for any other modes (e.g., 'fixed_font') ---
        else:
            # Apply final post-layout constraints: offset, scale ratio, and min/max clamps.
            layout_font_size = max(int(min(target_font_size, 512)), 1)
            final_font_size = _apply_final_font_constraints(layout_font_size, config)
            total_font_scale = final_font_size / layout_font_size

            # Scale region to match final font size
            dst_points = region.min_rect
            if total_font_scale != 1.0:
                try:
                    poly = Polygon(region.unrotated_min_rect[0])
                    scaled_poly = affinity.scale(poly, xfact=total_font_scale, yfact=total_font_scale, origin='center')
                    scaled_points = np.array(scaled_poly.exterior.coords[:4])
                    dst_points = rotate_polygons(region.center, scaled_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
                except Exception as e:
                    logger.warning(f"Failed to scale region for font_scale_ratio: {e}")

            region.font_size = final_font_size
            dst_points_list.append(dst_points)
            continue
        
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
    skip_font_scaling: bool = False
    ):

    if config is None:
        from ..config import Config
        config = Config()

    if config.render.renderer in (Renderer.openai_renderer, Renderer.gemini_renderer):
        from .model_api_renderer import dispatch_api_rendering

        return await dispatch_api_rendering(img=img, text_regions=text_regions, config=config)

    with _FREETYPE_RENDER_LOCK:
        # 渲染阶段只依赖 region.font_path；这里仅设置一个稳定的初始字体兜底
        text_render.set_font(text_render.DEFAULT_FONT)
        text_regions = list(filter(lambda region: region.translation, text_regions))

        result = resize_regions_to_font_size(img, text_regions, config, original_img, return_debug_img, skip_font_scaling=skip_font_scaling)
        
        # Handle return value (may be tuple if debug image is included)
        if return_debug_img and isinstance(result, tuple):
            dst_points_list, debug_img = result
        else:
            dst_points_list = result
            debug_img = None

        for region, dst_points in tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions)):
            # 保存缩放算法计算的 dst_points 到 region，供 PSD 导出使用
            # 注意：这是缩放后的真实文本区域，不是 render 函数中扩展后的区域
            region.dst_points = dst_points
            
            # 检查是否有文本需要渲染
            if not region.translation or not region.translation.strip():
                logger.info(f"[RENDER] 跳过空文本区域: text='{region.text[:20] if region.text else ''}', translation='{region.translation[:20] if region.translation else ''}'")
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
            )
        
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
    config: Config
):
    # 完全依赖区域字体，不再使用运行时全局字体参数
    # JSON 导出时已为每个区域填入完整 font_path，这里直接用即可
    region_font_path = getattr(region, 'font_path', '') or ''
    resolved_font_path = _resolve_font_path(region_font_path)
    if resolved_font_path:
        text_render.set_font(resolved_font_path)
    else:
        if region_font_path:
            logger.warning(f"Font path not found for region: {region_font_path}, using built-in default font")
        text_render.set_font(text_render.DEFAULT_FONT)

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
    except Exception as _e:
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

    # Centralized text preprocessing
    # 检查是否有富文本，并标记给渲染器
    has_rich_text = hasattr(region, 'rich_text') and region.rich_text
    _rich_text_html = region.rich_text if has_rich_text else None
    
    if has_rich_text:
        # 有富文本，从 HTML 中提取纯文本（用于非 Qt 渲染器）
        from html import unescape
        text = region.rich_text
        
        # 1. 还原特殊标记
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = text.replace('<!--H_START-->', '<H>')
        text = text.replace('<!--H_END-->', '</H>')
        
        # 2. 移除所有 HTML 标签（保留 <H> 和 </H>）
        # 先保护 <H> 标签
        text = text.replace('<H>', '___H_START___')
        text = text.replace('</H>', '___H_END___')
        
        # 移除其他 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 还原 <H> 标签
        text = text.replace('___H_START___', '<H>')
        text = text.replace('___H_END___', '</H>')
        
        # 3. 解码 HTML 实体
        text = unescape(text)
        
        # 4. 清理多余的空白
        text_to_render = text.strip()
    else:
        # 没有富文本，使用普通文本
        text_to_render = region.get_translation_for_rendering()
        # 将所有BR标记转换为\n用于渲染
        has_br_in_text = bool(re.search(r'(\[BR\]|<br>|【BR】)', text_to_render, flags=re.IGNORECASE))
        if has_br_in_text:
            text_to_render = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', text_to_render, flags=re.IGNORECASE)

        # Automatically add horizontal tags for vertical text
        render_horizontally = _resolve_region_render_horizontal(region)
        if not render_horizontally and config.render.auto_rotate_symbols:
            text_to_render = text_render.auto_add_horizontal_tags(text_to_render)

    if disable_font_border :
        bg = None

    middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
    norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
    norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
    r_orig = np.mean(norm_h / norm_v)

    render_horizontally = _resolve_region_render_horizontal(region)

    # 如果最终判断为横排,删除所有 <H> 标签,防止打印出来
    if render_horizontally:
        text_to_render = re.sub(r'<H>(.*?)</H>', r'\1', text_to_render, flags=re.IGNORECASE | re.DOTALL)

    letter_spacing = _resolve_letter_spacing_multiplier(region, config)

    # 将当前region传递给config，用于方向不匹配检测
    if config:
        config._current_region = region

    # 使用 freetype 渲染器（稳定可靠）
    # 检测是否需要使用高质量渲染（针对低分辨率优化）
    use_hq_render = text_render_hq.should_use_hq_rendering(
        region.font_size, 
        (img.shape[1], img.shape[0])
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
        logger.warning(f"[RENDER SKIPPED] Text rendering returned None. Text: '{region.translation[:100]}...'")
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
                # 垂直方向：根据center_text_in_bubble决定是否居中
                if config.render.center_text_in_bubble:
                    # 居中开启：垂直居中
                    box[h_ext:h_ext+h, 0:w] = temp_box
                else:
                    # 默认：贴顶部
                    box[0:h, 0:w] = temp_box
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
                # 竖排文本垂直方向：根据center_text_in_bubble决定是否居中
                if config.render.center_text_in_bubble:
                    # 居中开启：垂直居中
                    box[h_ext:h_ext+h, 0:w] = temp_box
                else:
                    # 默认：贴顶部
                    box[0:h, 0:w] = temp_box
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

    # 智能边界调整：检查文本是否超出图片边界
    img_h, img_w = img.shape[:2]
    x, y, w, h = cv2.boundingRect(np.round(dst_points[0]).astype(np.int32))
    
    adjusted = False
    adjusted_dst_points = dst_points.copy()
    
    # 获取四个角点的坐标
    pts = adjusted_dst_points[0].copy()  # shape: (4, 2)
    
    # 检查每个边是否超出，超出则缩回来
    # 左边超出
    min_x = pts[:, 0].min()
    if min_x < 0:
        # 把所有超出左边界的点拉回到0
        pts[:, 0] = np.maximum(pts[:, 0], 0)
        adjusted = True
        logger.info(f"Left edge exceeded, clipped from {min_x:.1f} to 0")
    
    # 右边超出
    max_x = pts[:, 0].max()
    if max_x > img_w:
        # 把所有超出右边界的点拉回到img_w
        pts[:, 0] = np.minimum(pts[:, 0], img_w)
        adjusted = True
        logger.info(f"Right edge exceeded, clipped from {max_x:.1f} to {img_w}")
    
    # 上边超出
    min_y = pts[:, 1].min()
    if min_y < 0:
        # 把所有超出上边界的点拉回到0
        pts[:, 1] = np.maximum(pts[:, 1], 0)
        adjusted = True
        logger.info(f"Top edge exceeded, clipped from {min_y:.1f} to 0")
    
    # 下边超出
    max_y = pts[:, 1].max()
    if max_y > img_h:
        # 把所有超出下边界的点拉回到img_h
        pts[:, 1] = np.minimum(pts[:, 1], img_h)
        adjusted = True
        logger.info(f"Bottom edge exceeded, clipped from {max_y:.1f} to {img_h}")
    
    if adjusted:
        adjusted_dst_points[0] = pts
        new_x, new_y, new_w, new_h = cv2.boundingRect(np.round(pts).astype(np.int32))
        logger.info(f"Text box adjusted to fit image: ({x}, {y}, {w}, {h}) -> ({new_x}, {new_y}, {new_w}, {new_h})")

    # 统一使用局部区域渲染，避免 OpenCV warpPerspective 的 32767 像素限制
    SHRT_MAX = 32767
    if box.shape[0] > SHRT_MAX or box.shape[1] > SHRT_MAX:
        logger.error(f"[RENDER SKIPPED] Text box size exceeds OpenCV limit (32767). "
                     f"box={box.shape[:2]}, text='{region.translation[:50] if hasattr(region, 'translation') else 'N/A'}...'")
        return img
    
    # 计算文字区域的边界框，添加边距
    x_adj, y_adj, w_adj, h_adj = cv2.boundingRect(np.round(adjusted_dst_points[0]).astype(np.int32))
    margin = max(w_adj, h_adj) // 2 + 100  # 添加足够的边距
    
    # 计算局部区域边界
    local_x1 = max(0, x_adj - margin)
    local_y1 = max(0, y_adj - margin)
    local_x2 = min(img_w, x_adj + w_adj + margin)
    local_y2 = min(img_h, y_adj + h_adj + margin)
    local_w = local_x2 - local_x1
    local_h = local_y2 - local_y1
    
    # 检查局部区域是否仍然超限
    if local_w > SHRT_MAX or local_h > SHRT_MAX:
        logger.error(f"[RENDER SKIPPED] Local region still exceeds OpenCV limit. "
                     f"local_size=({local_w}, {local_h}), text='{region.translation[:50] if hasattr(region, 'translation') else 'N/A'}...'")
        return img
    
    # 调整目标点到局部坐标系
    local_dst_points = adjusted_dst_points.copy()
    local_dst_points[0, :, 0] -= local_x1
    local_dst_points[0, :, 1] -= local_y1
    
    # 重新计算变换矩阵
    M_local, _ = cv2.findHomography(src_points, local_dst_points[0], cv2.RANSAC, 5.0)

    # 检查变换矩阵是否有效
    if M_local is None:
        logger.warning(f"[RENDER SKIPPED] Failed to compute homography matrix for text: "
                      f"'{region.translation[:50] if hasattr(region, 'translation') else 'N/A'}...'")
        return img

    # 在局部区域进行变换
    rgba_region = cv2.warpPerspective(box, M_local, (local_w, local_h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    
    # 计算在局部区域中的有效范围
    local_text_x = x_adj - local_x1
    local_text_y = y_adj - local_y1
    valid_y1 = max(0, local_text_y)
    valid_y2 = min(local_h, local_text_y + h_adj)
    valid_x1 = max(0, local_text_x)
    valid_x2 = min(local_w, local_text_x + w_adj)
    
    if valid_y2 > valid_y1 and valid_x2 > valid_x1:
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
                (target_region.astype(np.float32) * (1 - mask_region) + canvas_region.astype(np.float32) * mask_region), 
                0, 255
            ).astype(np.uint8)
        else:
            logger.warning(f"Text region size mismatch: canvas={canvas_region.shape[:2]}, target={target_region.shape[:2]}, skipping region")
    else:
        logger.warning(f"Text region completely outside image bounds: x={x_adj}, y={y_adj}, w={w_adj}, h={h_adj}, image_size=({img_w}, {img_h}). Text: '{region.translation[:50] if hasattr(region, 'translation') else 'N/A'}...'")
    
    return img

async def dispatch_eng_render(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/comic shanns 2.ttf')
    text_render.set_font(font_path)

    return render_textblock_list_eng(img_canvas, text_regions, line_spacing=line_spacing, size_tol=1.2, original_img=original_img, downscale_constraint=0.8,disable_font_border=disable_font_border)

async def dispatch_eng_render_pillow(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '') -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttf.ttc')

    return render_textblock_list_eng_pillow(font_path, img_canvas, text_regions, original_img=original_img, downscale_constraint=0.95)
