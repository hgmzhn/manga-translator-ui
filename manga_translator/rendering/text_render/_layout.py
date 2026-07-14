"""布局层：横排行/竖排列的富文本布局 builder 与包络几何。

测量与绘制共用同一套几何数字（measure_only 跳过光栅化）——这是
"测量框 == 绘制输出面"契约的实现处。竖排字符槽位规则（旋转/贴边/
半宽/紧凑）也在本模块。
"""
import math
import re
from time import perf_counter
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetricsF, QPainterPath

from ..rich_text import RenderSpan, RichTextDocument, TextStyle, normalize_rich_linebreaks
from ._compose import (
    _apply_style_layer_effects,
    _bitmap_ink_rect,
    _crop_pair,
    _glyph_pair_rgba,
    _paste_bitmap,
    _paste_rgba,
    _rgba_from_alpha_pair,
    _stroke_alpha_from_text_alpha,
    _stroke_bitmap_from_alpha,
    _stroke_pad_px,
    _style_fill_color,
    _style_font_size,
    _style_italic_shear,
    _style_layer_effects_geometry,
    _style_stroke_color,
    _style_stroke_ratio,
)
from ._fonts import _bold_scope, _create_text_layout, _layout_font, _state, _style_font_scope
from ._glyphs import GlyphRaster, _glyph_raster, _glyph_spec, _rasterize_path
from ._shared import _VERTICAL_CACHE_MAX, _cache_get, _cache_put, _profile_add

_HORIZONTAL_SYMBOL_HALFWIDTH_MAP = str.maketrans({'！': '!', '？': '?'})
_VERTICAL_ASCII_ROTATE = {chr(i) for i in range(0x21, 0x7F)}
_VERTICAL_ROTATE_OPEN_BRACKETS = {'「', '『', '（', '《', '〈', '【', '〖', '〔', '［', '｛', '(', '“', '‘'}
_VERTICAL_ROTATE_CLOSE_BRACKETS = {'」', '』', '）', '》', '〉', '】', '〗', '〕', '］', '｝', ')', '”', '’'}
_VERTICAL_OPEN_BRACKETS = _VERTICAL_ROTATE_OPEN_BRACKETS | {'﹁', '﹃', '︵', '︷', '︹', '︻', '︽', '︿', '﹇'}
_VERTICAL_CLOSE_BRACKETS = _VERTICAL_ROTATE_CLOSE_BRACKETS | {'﹂', '﹄', '︶', '︸', '︺', '︼', '︾', '﹀', '﹈'}
_VERTICAL_PUNCT_UP = {'。', '．', '，', '、', '·', '：', '；', '！', '？', '︒', '︐', '︑', '︓', '︔', '︕', '︖', '﹅', '﹆'}
_VERTICAL_ROTATE_NONBRACKET = {'⸺', '…', '⋯', '～', '-', '–', '—', '﹏', '●', '•', '~'}
_VERTICAL_ROTATE_CHARS = (
    _VERTICAL_ASCII_ROTATE
    | _VERTICAL_ROTATE_NONBRACKET
    | _VERTICAL_ROTATE_OPEN_BRACKETS
    | _VERTICAL_ROTATE_CLOSE_BRACKETS
)
_VERTICAL_COMPACT_SLOT = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS | _VERTICAL_PUNCT_UP
_VERTICAL_HALF_ADVANCE = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS

_VERTICAL_ROTATE_ALIGN_TOP_RIGHT = {'「', '『', '“', '‘'}
_VERTICAL_ROTATE_ALIGN_BOTTOM_LEFT = {'」', '』', '”', '’'}
_VERTICAL_ALIGN_TOP_RIGHT = {'﹁', '﹃'} | _VERTICAL_ROTATE_ALIGN_TOP_RIGHT
_VERTICAL_ALIGN_BOTTOM_LEFT = {'﹂', '﹄'} | _VERTICAL_ROTATE_ALIGN_BOTTOM_LEFT
_VERTICAL_ALIGN_TOP_CENTER = {'︵', '︷', '︹', '︻', '︽', '︿', '﹇'}
_VERTICAL_ALIGN_BOTTOM_CENTER = {'︶', '︸', '︺', '︼', '︾', '﹀', '﹈'}
_VERTICAL_PUNCT_CENTER = {'。', '．', '，', '、', '·', '︒', '︐', '︑', '﹅'}
_VERTICAL_FORCE_COMPACT_RE = re.compile(
    '['
    + r'\u2700-\u275A\u2761-\u2767\u2776-\u27BF'
    + r'\u2600-\u26FF'
    + r'⁁⁂⁇⁈⁉⁊⁋⁎※⁑⁒⁕⁖⁘⁙⁛⁜‼‽'
    + ']'
)


def CJK_Compatibility_Forms_translate(cdpt: str, direction: int):
    """渲染层不替换字符，只返回方向相关的旋转信息。"""
    if direction == 1 and (cdpt == 'ー' or cdpt in _VERTICAL_ROTATE_CHARS):
        return cdpt, 90
    return cdpt, 0


def _normalize_horizontal_block_content(content: str) -> str:
    # F14：BR 编解码统一走 rich_text.normalize_rich_linebreaks（BR→\n 后去换行，
    # 与旧的 _BR_RE.sub('') 输出一致）
    content = normalize_rich_linebreaks(content).replace('\r', '').replace('\n', '')
    return content.translate(_HORIZONTAL_SYMBOL_HALFWIDTH_MAP) if re.fullmatch(r'[!?！？]+', content) else content


def _rich_vertical_ruby_space(font_size: int) -> int:
    return int(round(font_size * 0.45))


def _rich_vertical_dot_space(font_size: int) -> int:
    return int(round(font_size * 0.35))


def _rich_vertical_line_gap(spacing_x: int, layout: dict) -> int:
    side_space = int(layout.get('ruby_extra', 0)) + int(layout.get('dot_extra', 0))
    return max(int(spacing_x), side_space)


def _rich_vertical_side_space(layout: dict) -> int:
    return int(layout.get('ruby_extra', 0)) + int(layout.get('dot_extra', 0))


def _vertical_column_walk(widths: list, gaps: list, origin_right: float) -> list:
    """竖排列位游走（F13 共享 helper）：从右向左排列各列。

    widths[i] 为第 i 列宽度，gaps[i] 为第 i 列与第 i+1 列之间的间距
    （len(gaps) == len(widths) - 1）。返回每列 (left, right) 边缘坐标。
    传统纯文本路径与富文本路径共用；数值以旧纯文本路径为准。
    """
    columns = []
    edge = float(origin_right)
    for idx, width in enumerate(widths):
        width = float(width)
        columns.append((edge - width, edge))
        if idx + 1 < len(widths):
            edge -= width + float(gaps[idx])
    return columns


def _vertical_line_origin_y(origin_y, alignment: str, max_height: int, line_height: int):
    """竖排列的纵向对齐偏移（F13 共享 helper）。

    left=顶对齐（不偏移）、center=居中、right=底对齐；
    公式与旧纯文本路径逐字相同，origin_y 的 int/float 类型原样保留。
    """
    if alignment == 'center':
        return origin_y + round((max_height - line_height) / 2.0)
    if alignment == 'right':
        return origin_y + max_height - line_height
    return origin_y


def _rich_vertical_layout_geometry(layouts: list, font_size: int, line_spacing: float) -> dict:
    spacing_x = calc_vertical_line_spacing_px(font_size, line_spacing)
    body_width = sum(int(layout['thickness']) for layout in layouts)
    body_width += spacing_x * max(0, len(layouts) - 1)
    layout_width = sum(int(layout['thickness']) for layout in layouts)
    layout_width += sum(_rich_vertical_line_gap(spacing_x, layout) for layout in layouts[1:])
    # 紧凑框：左右溢出各按真实需要计算。右侧 = 首列注音/着重号 + 字形右溢出；
    # 左侧只有字形左溢出。正文列区间 [left_extra, left_extra+layout_width] 因此
    # 一般不在 paint 框正中，正文中心由 body_center_x 显式给出。
    left_extra = max((int(layout.get('paint_left_extra', 0)) for layout in layouts), default=0)
    right_paint_extra = max((int(layout.get('paint_right_extra', 0)) for layout in layouts), default=0)
    right_extra = max(_rich_vertical_side_space(layouts[0]) if layouts else 0, right_paint_extra)
    # 纵向包络：正文高 = 最高列的游走高度；上下 extras 由各列图层实际
    # 纵向溢出（偏移/切变/描边外扩）取最大值，测量与绘制共用。
    body_height = max((int(layout['height']) for layout in layouts), default=0)
    top_extra = max((int(layout.get('paint_top_extra', 0)) for layout in layouts), default=0)
    bottom_extra = max((int(layout.get('paint_bottom_extra', 0)) for layout in layouts), default=0)
    return {
        'spacing_x': int(spacing_x),
        'body_width': int(body_width),
        'layout_width': int(layout_width),
        'paint_width': int(layout_width + left_extra + right_extra),
        'left_extra': int(left_extra),
        'right_extra': int(right_extra),
        'body_center_x': float(left_extra) + float(layout_width) / 2.0,
        'body_height': int(body_height),
        'top_extra': int(top_extra),
        'bottom_extra': int(bottom_extra),
        'paint_height': int(body_height + top_extra + bottom_extra),
        'body_center_y': float(top_extra) + float(body_height) / 2.0,
    }


def _rich_vertical_column_positions(layouts: list, geometry: dict, origin_x: float = 0.0) -> list:
    origin_right = float(origin_x) + geometry['left_extra'] + geometry['layout_width']
    widths = [float(layout['thickness']) for layout in layouts]
    gaps = [
        _rich_vertical_line_gap(geometry['spacing_x'], layouts[idx + 1])
        for idx in range(max(0, len(layouts) - 1))
    ]
    return [
        (left, right, left + thickness / 2.0)
        for (left, right), thickness in zip(
            _vertical_column_walk(widths, gaps, origin_right), widths
        )
    ]


def _normalize_letter_spacing(letter_spacing: float) -> float:
    try:
        value = float(letter_spacing)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def _normalize_line_spacing(line_spacing: float) -> float:
    try:
        value = float(line_spacing)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def calc_horizontal_line_spacing_px(font_size: int, line_spacing: float) -> int:
    value = _normalize_line_spacing(line_spacing)
    return int(font_size * (value - 1.0))


def calc_vertical_line_spacing_px(font_size: int, line_spacing: float) -> int:
    """竖排列间距像素（F13 共享 helper）：传统纯文本路径与富文本路径共用。

    公式以旧纯文本路径（put_text_vertical）为准：
    倍率 >= 1 时按 0.2*字号*倍率；< 1 时允许负间距（紧排）。
    """
    val_ls = _normalize_line_spacing(line_spacing)
    if val_ls >= 1.0:
        return int(font_size * 0.2 * val_ls)
    return int(font_size * (val_ls - 0.8))


def _scale_advance(advance: int, letter_spacing: float) -> int:
    if advance <= 0:
        return int(advance)
    return max(1, int(round(advance * _normalize_letter_spacing(letter_spacing))))


def _horizontal_line(text: str, font_size: int, letter_spacing: float = 1.0):
    return _create_text_layout(text or '', font_size, letter_spacing)


def _line_logical_width(line, text_length: int) -> float:
    cursor_x = line.cursorToX(text_length)
    return float(cursor_x[0] if isinstance(cursor_x, tuple) else cursor_x)


def _sorted_glyph_positions(layout, reversed_direction: bool):
    positions = [pos for run in layout.glyphRuns() for pos in run.positions()]
    positions.sort(key=lambda p: p.x(), reverse=reversed_direction)
    return positions


def _horizontal_ellipsis_tracking_offsets(
    text: str,
    font_size: int,
    letter_spacing: float,
    positions: list,
    reversed_direction: bool = False,
) -> list:
    if reversed_direction or _normalize_letter_spacing(letter_spacing) == 1.0 or '……' not in (text or ''):
        return [0.0] * len(positions)
    _, _, base_layout, _ = _horizontal_line(text, font_size, 1.0)
    if base_layout is None:
        return [0.0] * len(positions)
    base_positions = _sorted_glyph_positions(base_layout, False)
    limit = min(len(text), len(positions), len(base_positions))
    offsets = [0.0] * len(positions)
    idx = 0
    while idx < limit:
        if text[idx] != '…':
            idx += 1
            continue
        run_start = idx
        while idx < limit and text[idx] == '…':
            idx += 1
        if idx - run_start < 2:
            continue
        start_spaced_x = positions[run_start].x()
        start_base_x = base_positions[run_start].x()
        for run_idx in range(run_start + 1, idx):
            spaced_delta = positions[run_idx].x() - start_spaced_x
            base_delta = base_positions[run_idx].x() - start_base_x
            offsets[run_idx] = spaced_delta - base_delta
    return offsets


def _line_metrics(text: str, font_size: int, letter_spacing: float = 1.0) -> dict:
    normalized, qfont, _, line = _horizontal_line(text, font_size, letter_spacing)
    metrics = QFontMetricsF(qfont)
    if line is None:
        return {'text': normalized, 'logical_width': 0.0, 'ascent': float(metrics.ascent()), 'height': float(metrics.height()), 'descent': float(metrics.descent())}
    return {'text': normalized, 'logical_width': _line_logical_width(line, len(normalized)), 'ascent': float(line.ascent()), 'height': float(line.height()), 'descent': float(line.descent())}


def _line_surface_impl(
    line_text: str,
    font_size: int,
    border_size: int,
    stroke_ratio: float = 0.07,
    reversed_direction: bool = False,
    letter_spacing: float = 1.0,
    bold: bool = False,
    profile_stats: Optional[dict] = None,
):
    stage_t0 = perf_counter() if profile_stats is not None else None
    normalized, _, layout, line = _horizontal_line(line_text, font_size, letter_spacing)
    _profile_add(profile_stats, "tr_layout_ms", stage_t0)
    if not line_text or line is None:
        return None
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    # Qt/DirectWrite 把 family name 以 '[' 开头的字体归入同一字体集合，
    # 导致 shaping 时 character-to-glyph 映射返回错误的 glyph_id。
    # 修复：用 _glyph_spec 查字形路径（走 font_selection 直接加载的 QRawFont，
    # 绕开 Qt 字体数据库），位置（pos）仍从 QTextLayout 取。
    # glyphRuns() 返回的 run 顺序不保证与字符串字符顺序一致（混合脚本时
    # 如 CJK + ASCII 会分成多个 run，run 顺序不定），按 x 坐标排序以
    # 确保位置与字符的逻辑顺序匹配
    stage_t0 = perf_counter() if profile_stats is not None else None
    all_positions = _sorted_glyph_positions(layout, reversed_direction)
    position_offsets = _horizontal_ellipsis_tracking_offsets(
        normalized,
        font_size,
        letter_spacing,
        all_positions,
        reversed_direction,
    )
    for idx, char in enumerate(normalized):
        if idx >= len(all_positions):
            break
        pos = all_positions[idx]
        try:
            spec = _glyph_spec(char, font_size)
        except Exception:
            continue
        glyph_path = spec.raw_font.pathForGlyph(spec.glyph_id)
        if not glyph_path.isEmpty():
            offset_x = position_offsets[idx] if idx < len(position_offsets) else 0.0
            glyph_path.translate(pos.x() - offset_x, pos.y())
            path.addPath(glyph_path)
    _profile_add(profile_stats, "tr_path_ms", stage_t0)
                
    if path.isEmpty():
        return None
    stage_t0 = perf_counter() if profile_stats is not None else None
    fill_alpha, fill_left, fill_top = _rasterize_path(path)
    _profile_add(profile_stats, "tr_raster_ms", stage_t0)
    if fill_alpha.size == 0:
        return None
    if border_size > 0:
        stage_t0 = perf_counter() if profile_stats is not None else None
        stroke_px = max(int(stroke_ratio * font_size), 1)
        border_alpha, border_dx, border_dy = _stroke_alpha_from_text_alpha(fill_alpha, stroke_px)
        border_left, border_top = fill_left + border_dx, fill_top + border_dy
        left = min(fill_left, border_left)
        top = min(fill_top, border_top)
        right = max(fill_left + fill_alpha.shape[1], border_left + border_alpha.shape[1])
        bottom = max(fill_top + fill_alpha.shape[0], border_top + border_alpha.shape[0])
        text_canvas = np.zeros((bottom - top, right - left), dtype=np.uint8)
        border_canvas = np.zeros((bottom - top, right - left), dtype=np.uint8)
        _paste_bitmap(text_canvas, fill_alpha, fill_left - left, fill_top - top)
        _paste_bitmap(border_canvas, border_alpha, border_left - left, border_top - top)
        _profile_add(profile_stats, "tr_stroke_ms", stage_t0)
    else:
        left, top = fill_left, fill_top
        text_canvas, border_canvas = fill_alpha, np.zeros_like(fill_alpha)
    stage_t0 = perf_counter() if profile_stats is not None else None
    cropped = _crop_pair(text_canvas, border_canvas)
    if cropped is None:
        return None
    text_bitmap, border_bitmap, x, y, w, h = cropped
    logical_width = _line_logical_width(line, len(normalized))
    origin_x = -logical_width if reversed_direction else 0.0
    ascent, height = float(line.ascent()), float(line.height())
    result = {
        'text': text_bitmap, 'border': border_bitmap, 'left_rel': left + x - origin_x,
        'right_rel': left + x - origin_x + w, 'top_rel': top + y - ascent, 'width': w, 'height': h,
        'logical_width': logical_width,
        'line_ascent': ascent, 'line_descent': float(line.descent()), 'line_height': height,
        'ink_top': float(top + y), 'ink_bottom': float(top + y + h),
    }
    _profile_add(profile_stats, "tr_crop_ms", stage_t0)
    return result


def _line_surface(
    line_text: str,
    font_size: int,
    border_size: int,
    stroke_ratio: float = 0.07,
    reversed_direction: bool = False,
    letter_spacing: float = 1.0,
    bold: bool = False,
    profile_stats: Optional[dict] = None,
):
    effective_bold = bool(bold) or _state().bold
    with _bold_scope(effective_bold):
        return _line_surface_impl(
            line_text,
            font_size,
            border_size,
            stroke_ratio,
            reversed_direction,
            letter_spacing,
            bold,
            profile_stats,
        )


def _block_surface(
    font_size: int,
    content: str,
    border_size: int,
    stroke_ratio: float = 0.07,
    letter_spacing: float = 1.0,
    bold: bool = False,
    profile_stats: Optional[dict] = None,
):
    content = _normalize_horizontal_block_content(content)
    surface = _line_surface(content, font_size, border_size, stroke_ratio, False, letter_spacing, bold, profile_stats)
    if surface is None:
        return None
    text_bitmap, border_bitmap = surface['text'], surface['border']
    h, w = text_bitmap.shape
    return {'text': text_bitmap, 'border': border_bitmap, 'width': int(w), 'height': int(h)}


def _rich_span_surface(
    span: RenderSpan,
    base_font_size: int,
    global_stroke_ratio: float,
    global_stroke_color,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
):
    text = span.text
    if not text:
        return None
    font_size = _style_font_size(base_font_size, span.style)
    stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, global_stroke_color)
    border_size = int(max(font_size * stroke_ratio, 1)) if stroke_ratio > 0 else 0
    with _style_font_scope(span.style):
        surface = _line_surface(
            text,
            font_size,
            border_size,
            stroke_ratio,
            reversed_direction,
            letter_spacing,
            span.style.bold,
            profile_stats,
        )
    if surface is None:
        return None
    return {
        'span': span,
        'font_size': font_size,
        'stroke_ratio': stroke_ratio,
        'surface': surface,
        'logical_width': float(surface.get('logical_width', 0.0)),
        'ascent': float(surface.get('line_ascent', font_size)),
        'descent': float(surface.get('line_descent', font_size * 0.2)),
        'height': float(surface.get('line_height', font_size)),
    }


def _rich_colorized_surface(run: dict, fg, bg):
    span = run['span']
    surface = run['surface']
    fill = _style_fill_color(span.style, fg)
    # stroke_ratio<=0 时 surface['border'] 是全零层：描边色传 None 走纯文字
    # 分支，而不是把全零层当描边 alpha 用（会把整段变透明）。
    if float(run.get('stroke_ratio', 0.0)) > 0.0:
        stroke = _style_stroke_color(span.style, bg)
    else:
        stroke = None
    layer = _rgba_from_alpha_pair(surface['text'], surface['border'], fill, stroke)
    return _apply_style_layer_effects(layer, span.style, int(run.get('font_size', 1)))


def _rich_ruby_surface(span: RenderSpan, parent_font_size: int, fg, bg, letter_spacing: float, profile_stats: Optional[dict] = None):
    ruby = ''.join(run.text for run in (span.ruby or []))
    if not ruby:
        return None
    ruby_style = span.style.copy()
    ruby_style.emphasis = False
    ruby_span = RenderSpan(ruby, ruby_style)
    ruby_font_size = max(1, int(round(parent_font_size * 0.42)))
    stroke_ratio = 0.0 if bg is None and not ruby_style.stroke else _style_stroke_ratio(ruby_style, ruby_font_size, 0.0, bg)
    border_size = int(max(ruby_font_size * stroke_ratio, 1)) if stroke_ratio > 0 else 0
    with _style_font_scope(ruby_style):
        surface = _line_surface(ruby, ruby_font_size, border_size, stroke_ratio, False, letter_spacing, ruby_style.bold, profile_stats)
    if surface is None:
        return None
    run = {'span': ruby_span, 'surface': surface, 'font_size': ruby_font_size, 'stroke_ratio': stroke_ratio}
    layer, layer_dx, layer_dy = _rich_colorized_surface(run, fg, bg)
    if layer is None:
        return None
    return {'surface': surface, 'layer': layer, 'font_size': ruby_font_size, 'offset_x': layer_dx, 'offset_y': layer_dy}


def _rich_horizontal_ruby_extra(run: dict, bg) -> float:
    """横排注音预留高度（度量式，测量/绘制共用）。

    基数 0.50×字号沿用旧测量口径；注音带描边时（描边判定与
    _rich_ruby_surface 一致）加上描边图层外扩，避免包络裁到注音描边。
    """
    span = run['span']
    ruby_font = max(1, int(round(run['font_size'] * 0.42)))
    ruby_stroke_ratio = 0.0 if bg is None and not span.style.stroke else _style_stroke_ratio(span.style, ruby_font, 0.0, bg)
    return run['font_size'] * 0.50 + _stroke_pad_px(ruby_font, ruby_stroke_ratio) * 2.0


def _build_rich_horizontal_layout(
    document: RichTextDocument,
    base_font_size: int,
    global_stroke_ratio: float,
    fg,
    bg,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    measure_only: bool = False,
):
    """横排富文本布局（竖排 F21 builder 的横排对应物）。

    measure_only=True 时完全不光栅化，run 只带 QTextLayout 度量字段
    （与 _rich_span_surface 读取的是同一条 QTextLayout line，数值逐位
    一致）；行宽/行高/包络 extras 两条路径出自同一套度量公式，保证
    measure_rich_text_metrics 与绘制输出面逐像素同尺寸。
    空白 span 统一保留为无墨迹 spacer：占宽度、推进游标（旧绘制路径
    直接丢弃空白 span 导致与测量宽度不一致，此处一并收口）。
    """
    layouts = []
    for paragraph in document.paragraphs:
        runs = []
        for span in paragraph.spans:
            if not span.text:
                continue
            span_font = _style_font_size(base_font_size, span.style)
            span_stroke_ratio = _style_stroke_ratio(span.style, span_font, global_stroke_ratio, bg)
            run = None
            if not measure_only:
                run = _rich_span_surface(span, base_font_size, global_stroke_ratio, bg, reversed_direction, letter_spacing, profile_stats)
            elif span.text.strip():
                with _style_font_scope(span.style):
                    metrics = _line_metrics(span.text, span_font, letter_spacing)
                run = {
                    'span': span,
                    'font_size': span_font,
                    'stroke_ratio': span_stroke_ratio,
                    'surface': None,
                    'logical_width': float(metrics['logical_width']),
                    'ascent': float(metrics['ascent']),
                    'descent': float(metrics['descent']),
                }
            if run is None:
                with _style_font_scope(span.style):
                    spacer_width = float(_measure_horizontal_text_width(span.text, span_font, letter_spacing))
                run = {
                    'span': span,
                    'font_size': span_font,
                    'stroke_ratio': span_stroke_ratio,
                    'surface': None,
                    'logical_width': spacer_width,
                    'ascent': None,
                    'descent': None,
                }
            if span.ruby and run['ascent'] is not None:
                ruby_text = ''.join(item.text for item in span.ruby)
                if ruby_text:
                    ruby_font = max(1, int(round(run['font_size'] * 0.42)))
                    with _style_font_scope(span.style):
                        run['ruby_width'] = float(_line_metrics(ruby_text, ruby_font, letter_spacing)['logical_width'])
                    run['ruby_extra_self'] = _rich_horizontal_ruby_extra(run, bg)
                    if not measure_only:
                        run['ruby'] = _rich_ruby_surface(span, run['font_size'], fg, bg, letter_spacing, profile_stats)
            runs.append(run)

        inked = [run for run in runs if run['ascent'] is not None]
        if inked:
            ascent = max(run['ascent'] for run in inked)
            descent = max(run['descent'] for run in inked)
            ruby_extra = max((run['ruby_extra_self'] for run in inked if run.get('ruby_width')), default=0.0)
            dot_extra = max((run['font_size'] * 0.25 for run in inked if run['span'].style.emphasis), default=0.0)
            line_height = ruby_extra + ascent + descent + dot_extra
        else:
            metrics = _line_metrics('', base_font_size, letter_spacing)
            ascent, descent = float(metrics['ascent']), float(metrics['descent'])
            ruby_extra = dot_extra = 0.0
            line_height = float(metrics['height'])
        layouts.append({
            'runs': runs,
            'logical_width': sum(float(run['logical_width']) for run in runs),
            'ascent': float(ascent),
            'descent': float(descent),
            'ruby_extra': float(ruby_extra),
            'dot_extra': float(dot_extra),
            'height': float(line_height),
        })
    return layouts


def _rich_horizontal_run_paint_rects(run: dict) -> list:
    """run 图层的度量包络矩形（相对行内游标原点 x=0、基线 y=0）。

    字形框 = 逻辑框 + 描边外扩，经 _style_layer_effects_geometry
    （镜像/切变/旋转，与绘制同一矩阵）后叠加 transform 偏移；注音框按
    run 中心对称展开（注音绘制不参与偏移/特效，与绘制路径一致）。
    """
    rects = []
    ascent = run.get('ascent')
    if ascent is None:
        return rects
    span = run['span']
    ascent = float(ascent)
    descent = float(run.get('descent') or 0.0)
    pad = float(_stroke_pad_px(run['font_size'], float(run.get('stroke_ratio') or 0.0)))
    box_w = float(run['logical_width']) + pad * 2.0
    box_h = ascent + descent + pad * 2.0
    if box_w > 0 and box_h > 0:
        transform = span.style.transform
        if _style_italic_shear(span.style) or transform.rotation:
            # 有切变/旋转才走角点几何（内部按整型框计算，保守 ≤1px）；
            # 无特效时保持浮点框，避免 ceil 残量污染无装饰文档的包络。
            out_h, out_w, dx, dy = _style_layer_effects_geometry(
                max(1, int(math.ceil(box_h))), max(1, int(math.ceil(box_w))), span.style
            )
            rects.append((
                -pad + float(dx) + transform.offset_x,
                -ascent - pad + float(dy) + transform.offset_y,
                float(out_w),
                float(out_h),
            ))
        else:
            rects.append((
                -pad + transform.offset_x,
                -ascent - pad + transform.offset_y,
                box_w,
                box_h,
            ))
    ruby_width = float(run.get('ruby_width') or 0.0)
    if ruby_width > 0:
        ruby_extra = float(run.get('ruby_extra_self') or 0.0)
        rects.append((
            float(run['logical_width']) / 2.0 - ruby_width / 2.0,
            -ascent - ruby_extra,
            ruby_width,
            ruby_extra,
        ))
    return rects


def _rich_horizontal_layout_geometry(layouts: list, font_size: int, line_spacing: float) -> dict:
    """横排包络几何（测量/绘制共用，F21 竖排 geometry 的横排对应物）。

    正文框 = 行按对齐堆叠的逻辑区域（宽 = 最长行逻辑宽，高 = 行高累加）；
    包络在正文框四周按 run 图层的度量矩形外扩（描边/切变/旋转/偏移/注音
    超宽）。X 向 extras 以各行自身行框为参照取最大值 —— 相对逐行对齐位置
    是保守估计，保证不裁墨迹，最多在非贴边行留少量空白。
    body_center 沿用旧口径：正文剔除首行注音区与末行着重号区后的中心。
    """
    spacing_y = calc_horizontal_line_spacing_px(font_size, line_spacing)
    body_width = max((float(layout['logical_width']) for layout in layouts), default=0.0)
    left_extra = 0.0
    right_extra = 0.0
    top_abs = 0.0
    bottom_abs = 0.0
    y = 0.0
    body_height = 0.0
    for index, layout in enumerate(layouts):
        baseline = y + layout['ruby_extra'] + layout['ascent']
        cursor = 0.0
        for run in layout['runs']:
            for rect_x, rect_y, rect_w, rect_h in _rich_horizontal_run_paint_rects(run):
                left_extra = max(left_extra, -(cursor + rect_x))
                right_extra = max(right_extra, cursor + rect_x + rect_w - float(layout['logical_width']))
                top_abs = min(top_abs, baseline + rect_y)
                bottom_abs = max(bottom_abs, baseline + rect_y + rect_h)
            cursor += float(run['logical_width'])
        y += float(layout['height'])
        bottom_abs = max(bottom_abs, y)
        body_height = y
        if index < len(layouts) - 1:
            y += spacing_y
    top_extra = max(0.0, -top_abs)
    bottom_extra = max(0.0, bottom_abs - body_height)
    body_top = float(layouts[0]['ruby_extra']) if layouts else 0.0
    last_dot_extra = float(layouts[-1]['dot_extra']) if layouts else 0.0
    # 整数化各分量后再求和：无装饰文档 extras 全零时，正文中心严格等于
    # 渲染框正中心（白框锚定契约：纯文本 delta 恒为 0）。ceil 余量并入
    # 正文侧（框内空气），与旧逐行 ceil 口径一致。
    left_i = int(math.ceil(left_extra))
    right_i = int(math.ceil(right_extra))
    top_i = int(math.ceil(top_extra))
    bottom_i = int(math.ceil(bottom_extra))
    body_w_i = int(math.ceil(body_width))
    body_h_i = int(math.ceil(body_height))
    return {
        'spacing_y': int(spacing_y),
        'body_width': float(body_width),
        'body_height': float(body_height),
        'left_extra': left_i,
        'right_extra': right_i,
        'top_extra': top_i,
        'bottom_extra': bottom_i,
        'paint_width': body_w_i + left_i + right_i,
        'paint_height': body_h_i + top_i + bottom_i,
        'body_center': (
            float(left_i) + float(body_w_i) / 2.0,
            float(top_i) + (body_top + float(body_h_i) - last_dot_extra) / 2.0,
        ),
    }


def _rich_vertical_block_item(
    span: RenderSpan,
    raw: str,
    base_font_size: int,
    global_stroke_ratio: float,
    fg,
    bg,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    measure_only: bool = False,
):
    font_size = _style_font_size(base_font_size, span.style)
    stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, bg)
    border_size = int(max(font_size * stroke_ratio, 1)) if stroke_ratio > 0 else 0
    with _style_font_scope(span.style):
        surface = _block_surface(
            font_size,
            raw,
            border_size,
            stroke_ratio,
            letter_spacing,
            span.style.bold,
            profile_stats,
        )
    if surface is None:
        return None
    if measure_only:
        # F21：跳过上色与特效 warp。上色不改变图层尺寸，特效几何按角点计算。
        layer = None
        height, width, layer_dx, layer_dy = _style_layer_effects_geometry(
            int(surface['height']), int(surface['width']), span.style
        )
    else:
        fill = _style_fill_color(span.style, fg)
        # 同 _rich_colorized_surface：stroke_ratio<=0 时描边色传 None 走纯文字分支
        stroke = _style_stroke_color(span.style, bg) if stroke_ratio > 0 else None
        layer = _rgba_from_alpha_pair(surface['text'], surface['border'], fill, stroke)
        if layer is None:
            return None
        layer, layer_dx, layer_dy = _apply_style_layer_effects(layer, span.style, font_size)
        height, width = int(layer.shape[0]), int(layer.shape[1])
    return {
        'kind': 'block',
        'span': span,
        'layer': layer,
        'offset_x': layer_dx,
        'offset_y': layer_dy,
        'width': width,
        'height': height,
        'font_size': font_size,
        'advance_y': height,
        'body_width': base_font_size,
        'pre_advance_y': int(round(span.style.pre_kerning * font_size)),
        'post_advance_y': int(round(span.style.kerning * font_size)),
    }


def _prepare_rich_vertical_char_item(item: dict, measure_only: bool = False) -> dict:
    base = item['base']
    bitmap = base['bitmap']
    if bitmap is None:
        return item
    bitmap_dx = 0
    bitmap_dy = 0
    if measure_only:
        # F21：度量只需要图层几何，跳过描边距离变换 / RGBA 合成 / 特效 warp。
        # 描边位图尺寸是确定的（正文位图四边各加 pad，见 _stroke_alpha_from_text_alpha
        # 与 _glyph_pair_rgba 的合并规则），上色不改变尺寸，特效按角点计算，
        # 全部几何与渲染路径逐像素一致。
        if bitmap is None or bitmap.size == 0:
            return item
        height, width = int(bitmap.shape[0]), int(bitmap.shape[1])
        off_x = off_y = 0
        if item['stroke_ratio'] > 0 and item['stroke'] is not None:
            stroke_px = max(int(item['stroke_ratio'] * item['font_size']), 1)
            pad = max(1, int(stroke_px)) + 1
            height += pad * 2
            width += pad * 2
            off_x = off_y = -pad
        height, width, layer_dx, layer_dy = _style_layer_effects_geometry(height, width, item['span'].style)
        item['layer_width'] = int(width)
        item['layer_height'] = int(height)
        item['paint_offset_x'] = float(bitmap_dx + off_x + layer_dx)
        item['paint_offset_y'] = float(bitmap_dy + off_y + layer_dy)
        return item
    if item['stroke_ratio'] > 0:
        stroke_bitmap = _stroke_bitmap_from_alpha(bitmap, item['font_size'], item['stroke_ratio'])
    else:
        stroke_bitmap = None
    layer, off_x, off_y = _glyph_pair_rgba(bitmap, stroke_bitmap, item['fill'], item['stroke'])
    if layer is None:
        return item
    layer, layer_dx, layer_dy = _apply_style_layer_effects(layer, item['span'].style, item['font_size'])
    item['layer'] = layer
    item['layer_width'] = int(layer.shape[1])
    item['layer_height'] = int(layer.shape[0])
    item['paint_offset_x'] = float(bitmap_dx + off_x + layer_dx)
    item['paint_offset_y'] = float(bitmap_dy + off_y + layer_dy)
    return item


def _rich_vertical_block_layer_x(body_left: float, thickness: float, item: dict) -> float:
    body_center = body_left + thickness / 2.0
    return (
        body_center
        - float(item['width']) / 2.0
        + item['span'].style.transform.offset_x
        + float(item.get('offset_x', 0.0))
    )


def _rich_vertical_char_layer_x(body_left: float, thickness: float, item: dict) -> float:
    base = item['base']
    char_x = _vertical_char_bitmap_x(body_left, thickness, base, item['font_size'])
    return char_x + item['span'].style.transform.offset_x + float(item.get('paint_offset_x', 0.0))


def _rich_vertical_item_paint_extra(item: dict, thickness: int) -> Tuple[int, int]:
    if item['kind'] == 'block':
        x = _rich_vertical_block_layer_x(0.0, float(thickness), item)
        width = float(item['width'])
    elif item['kind'] == 'char' and item.get('layer_width') is not None:
        x = _rich_vertical_char_layer_x(0.0, float(thickness), item)
        width = float(item['layer_width'])
    else:
        return 0, 0
    left_extra = max(0.0, -x)
    right_extra = max(0.0, x + width - float(thickness))
    return int(math.ceil(left_extra)), int(math.ceil(right_extra))


def _rich_vertical_item_paint_extent_y(item: dict) -> Optional[Tuple[float, float]]:
    """item 图层的纵向包络区间 [y0, y1)，相对列顶（cursor 原点）。

    与绘制路径的 y 公式同源：块 = cursor_y + transform 偏移 + 特效偏移；
    字符 = cursor_y + base.y + transform 偏移 + paint_offset_y。
    无图层的占位/空白项返回 None。
    """
    if item['kind'] == 'block':
        y0 = float(item['cursor_y']) + item['span'].style.transform.offset_y + float(item.get('offset_y', 0.0))
        return y0, y0 + float(item.get('height', 0))
    if item['kind'] == 'char' and item.get('layer_height') is not None:
        y0 = (
            float(item['cursor_y'])
            + float(int(item['base']['y']))
            + item['span'].style.transform.offset_y
            + float(item.get('paint_offset_y', 0.0))
        )
        return y0, y0 + float(item['layer_height'])
    return None


def _build_rich_vertical_layout(
    document: RichTextDocument,
    base_font_size: int,
    global_stroke_ratio: float,
    fg,
    bg,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    measure_only: bool = False,
):
    layouts = []
    thickness = max(1, int(base_font_size))
    for paragraph in document.paragraphs:
        items = []
        paint_left_extra = 0
        paint_right_extra = 0
        ruby_extra = 0
        dot_extra = 0
        for span in paragraph.spans:
            font_size = _style_font_size(base_font_size, span.style)
            if span.tcy:
                block = _rich_vertical_block_item(
                    span,
                    span.text,
                    base_font_size,
                    global_stroke_ratio,
                    fg,
                    bg,
                    letter_spacing,
                    profile_stats,
                    measure_only,
                )
                if block is not None:
                    items.append(block)
                    left_extra, right_extra = _rich_vertical_item_paint_extra(block, thickness)
                    paint_left_extra = max(paint_left_extra, left_extra)
                    paint_right_extra = max(paint_right_extra, right_extra)
                continue
            fill = _style_fill_color(span.style, fg)
            stroke = _style_stroke_color(span.style, bg)
            stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, bg)
            span_ruby_extra = _rich_vertical_ruby_space(font_size) if span.ruby else 0
            span_dot_extra = _rich_vertical_dot_space(font_size) if span.style.emphasis else 0
            ruby_extra = max(ruby_extra, span_ruby_extra)
            dot_extra = max(dot_extra, span_dot_extra)
            # 字体作用域提升到 span 层，避免带 fontFamily 的 span 逐字符
            # 反复 set_font（每次都会清空测量/竖排缓存导致缓存永不命中）
            with _style_font_scope(span.style):
                for char in span.text:
                    if char == '＿':
                        items.append({
                            'kind': 'placeholder',
                            'advance_y': _scale_advance(font_size, letter_spacing),
                            'pre_advance_y': int(round(span.style.pre_kerning * font_size)),
                            'post_advance_y': int(round(span.style.kerning * font_size)),
                            'font_size': font_size,
                        })
                        continue
                    base = _vertical_base(font_size, char, letter_spacing)
                    item = {
                        'kind': 'char',
                        'span': span,
                        'base': base,
                        'font_size': font_size,
                        'fill': fill,
                        'stroke': stroke,
                        'stroke_ratio': stroke_ratio,
                        'advance_y': int(base['advance_y']),
                        'body_width': thickness,
                        'pre_advance_y': int(round(span.style.pre_kerning * font_size)),
                        'post_advance_y': int(round(span.style.kerning * font_size)),
                    }
                    item = _prepare_rich_vertical_char_item(item, measure_only)
                    left_extra, right_extra = _rich_vertical_item_paint_extra(item, thickness)
                    paint_left_extra = max(paint_left_extra, left_extra)
                    paint_right_extra = max(paint_right_extra, right_extra)
                    items.append(item)
        cursor = 0
        laid = []
        for item in items:
            item = dict(item)
            cursor += int(item.get('pre_advance_y', 0))
            item['cursor_y'] = cursor
            cursor += int(item.get('advance_y', item.get('height', 0)))
            cursor += int(item.get('post_advance_y', 0))
            laid.append(item)
        column_height = max(0, int(cursor))
        paint_top_extra = 0.0
        paint_bottom_extra = 0.0
        for item in laid:
            extent = _rich_vertical_item_paint_extent_y(item)
            if extent is None:
                continue
            paint_top_extra = max(paint_top_extra, -extent[0])
            paint_bottom_extra = max(paint_bottom_extra, extent[1] - column_height)
        layouts.append({
            'width': int(thickness),
            'body_width': int(thickness),
            'thickness': int(thickness),
            'paint_left_extra': int(paint_left_extra),
            'paint_right_extra': int(paint_right_extra),
            'paint_top_extra': int(math.ceil(max(0.0, paint_top_extra))),
            'paint_bottom_extra': int(math.ceil(max(0.0, paint_bottom_extra))),
            'ruby_extra': int(ruby_extra),
            'dot_extra': int(dot_extra),
            'height': column_height,
            'items': laid,
        })
    return layouts


def _draw_vertical_ruby(canvas: np.ndarray, text: str, x: float, y1: float, y2: float, font_size: int, style: TextStyle, fg, bg, letter_spacing: float):
    if not text:
        return
    ruby_size = max(1, int(round(font_size * 0.36)))
    total_h = max(1.0, y2 - y1)
    slot = total_h / max(len(text), 1)
    ruby_style = style.copy()
    ruby_style.emphasis = False
    fill = _style_fill_color(ruby_style, fg)
    stroke = _style_stroke_color(ruby_style, None)
    # F23：字体作用域提升到 span 层（一次 set_font 覆盖整段注音）
    with _style_font_scope(ruby_style):
        for idx, char in enumerate(text):
            base = _vertical_base(ruby_size, char, letter_spacing)
            if base['bitmap'] is None:
                continue
            layer, off_x, off_y = _glyph_pair_rgba(base['bitmap'], None, fill, stroke)
            if layer is None:
                continue
            cy = y1 + slot * (idx + 0.5)
            _paste_rgba(canvas, layer, int(round(x - layer.shape[1] / 2.0 + off_x)), int(round(cy - layer.shape[0] / 2.0 + off_y)))


def _is_vertical_ellipsis_char(cdpt: str) -> bool:
    return cdpt in ('︙', '⋮', '⋯', '…')


def _estimate_ellipsis_gap(bitmap_char: np.ndarray) -> Optional[float]:
    if bitmap_char is None or bitmap_char.size == 0:
        return None
    labels, _, stats, centers = cv2.connectedComponentsWithStats((bitmap_char > 0).astype(np.uint8), connectivity=8)
    ys = sorted(float(centers[i][1]) for i in range(1, labels) if stats[i, cv2.CC_STAT_AREA] > 0)
    return None if len(ys) < 3 else (ys[1] - ys[0] + ys[2] - ys[1]) / 2.0


def _vertical_ellipsis_advance(glyph: GlyphRaster, font_size: int, bitmap_char: Optional[np.ndarray] = None) -> int:
    raw = bitmap_char.shape[0] + glyph.vert_bearing_y if bitmap_char is not None and bitmap_char.size else glyph.advance_y
    raw = raw if raw > 0 else font_size
    gap = _estimate_ellipsis_gap(bitmap_char)
    return max(1, int(round(3.0 * gap))) if gap and gap > 0 else max(1, raw)


def _vertical_force_compact_slot(cdpt: str) -> bool:
    return cdpt in _VERTICAL_PUNCT_UP or _VERTICAL_FORCE_COMPACT_RE.match(cdpt) is not None


def _vertical_rotated_advance(glyph: GlyphRaster, font_size: int, bitmap_char: Optional[np.ndarray] = None) -> int:
    if glyph.advance_x > 0:
        return int(glyph.advance_x)
    if bitmap_char is not None and bitmap_char.size:
        return int(bitmap_char.shape[0])
    return int(font_size)


def _vertical_space_advance(font_size: int, letter_spacing: float = 1.0) -> int:
    width = _measure_horizontal_text_width(' ', font_size, 1.0)
    if width <= 0:
        width = max(1, int(round(font_size * 0.25)))
    return _scale_advance(width, letter_spacing)


def _vertical_base(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> dict:
    state = _state()
    key = (state.font_family or tuple(state.font_selection), bool(state.bold), int(font_size), cdpt, round(_normalize_letter_spacing(letter_spacing), 4))
    cached = _cache_get(state.vertical, key)
    if cached is not None:
        return cached
    translated, rot = CJK_Compatibility_Forms_translate(cdpt, 1)
    if translated == ' ':
        base = {
            'translated': translated, 'rot_degree': 0, 'bitmap': None,
            'advance_y': _vertical_space_advance(font_size, letter_spacing),
            'ink_x': 0.0, 'ink_w': 0.0, 'y': 0,
            'advance_x': int(max(font_size, 1)), 'glyph_left': 0.0,
            'frame_width': int(max(font_size, 1)),
        }
        return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)

    rotated = rot == 90
    glyph = _glyph_raster(translated, font_size)
    bitmap = glyph.alpha if glyph.alpha.size else None
    if bitmap is not None and rotated:
        bitmap = cv2.rotate(bitmap, cv2.ROTATE_90_CLOCKWISE)
    ink_x, ink_y = 0.0, 0.0
    ink_w = float(bitmap.shape[1]) if bitmap is not None else 0.0
    ink_h = float(bitmap.shape[0]) if bitmap is not None else 0.0
    if bitmap is not None:
        rect = _bitmap_ink_rect(bitmap)
        if rect is not None:
            ink_x, ink_y, ink_w, ink_h = rect

    force_compact = _vertical_force_compact_slot(translated)
    if translated in _VERTICAL_HALF_ADVANCE:
        advance_y = font_size * 0.5
    elif rotated:
        advance_y = _vertical_rotated_advance(glyph, font_size, bitmap)
    elif _is_vertical_ellipsis_char(translated):
        advance_y = _vertical_ellipsis_advance(glyph, font_size, bitmap)
    else:
        advance_y = glyph.advance_y if glyph.advance_y > 0 else font_size

    if translated in _VERTICAL_HALF_ADVANCE:
        advance_y = _scale_advance(int(round(advance_y)), letter_spacing)
    elif force_compact and ink_h > 0:
        if translated in _VERTICAL_PUNCT_CENTER:
            metrics = QFontMetricsF(_layout_font(font_size, letter_spacing))
            advance_y = ink_h + max(0.0, float(metrics.descent()))
        else:
            advance_y = ink_h
        advance_y = _scale_advance(int(round(advance_y)), letter_spacing)
    else:
        advance_y = _scale_advance(int(round(advance_y)), letter_spacing)

    slot_height = advance_y if (translated in _VERTICAL_HALF_ADVANCE or force_compact or rotated) else max(1, advance_y)
    frame_width = max(font_size, int(round(ink_w)) if ink_w else 0, 1)
    if not rotated:
        frame_width = max(frame_width, int(glyph.advance_x))
    slot_origin_y = max(0, int(round((advance_y - slot_height) / 2.0)))
    
    # 默认居中对齐真实墨迹（考虑到 ink_y 和 ink_h）
    y = slot_origin_y + max(0, int(round((slot_height - ink_h) / 2.0))) - ink_y
    
    padding = max(1, int(round(font_size * 0.05)))
    if translated in _VERTICAL_ALIGN_TOP_RIGHT or translated in _VERTICAL_ALIGN_TOP_CENTER:
        y = padding - ink_y
    elif translated in _VERTICAL_ALIGN_BOTTOM_LEFT or translated in _VERTICAL_ALIGN_BOTTOM_CENTER:
        y = advance_y - ink_h - padding - ink_y
    elif force_compact:
        y = slot_origin_y - ink_y
        if translated in _VERTICAL_PUNCT_CENTER:
            y += max(0.0, (slot_height - ink_h) / 2.0)

    base = {
        'translated': translated, 'rot_degree': rot, 'bitmap': bitmap, 'advance_y': int(advance_y),
        'ink_x': float(ink_x), 'ink_w': float(ink_w), 'y': int(round(y)),
        'advance_x': int(max(glyph.advance_x, 1)), 'glyph_left': float(glyph.left),
        'frame_width': int(frame_width),
    }
    return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)


def get_vertical_char_bitmap_width(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> int:
    return int(_vertical_base(font_size, cdpt, letter_spacing)['frame_width'])


def _vertical_char_bitmap_x(
    frame_left: float,
    frame_width: float,
    base: dict,
    padding_size: Optional[float] = None,
) -> float:
    """返回竖排字符位图左边缘，普通直立字按 advance 居中。

    对应 Canvas 的 textAlign='center'：先把字体 advance box 的中心放到列中心，
    再加 glyph left bearing 得到位图原点。旋转字符已经在光栅层转过 90°，其
    原始 advance 轴也随之转为纵轴，因此横向仍使用旋转后位图框居中。标点的
    顶右/底左贴边规则最后覆盖默认居中。
    """
    frame_left = float(frame_left)
    frame_width = float(frame_width)
    ink_w = float(base.get('ink_w', 0.0))
    ink_x = float(base.get('ink_x', 0.0))
    translated = base.get('translated', '')
    if translated in _VERTICAL_PUNCT_UP:
        # 竖排标点的 advance/side bearing 常按横排标点设计，不能用于列内居中。
        # 它们仍按实际标点墨迹居中；正文直立字继续使用 advance box。
        x = frame_left + (frame_width - ink_w) / 2.0 - ink_x
    elif int(base.get('rot_degree', 0)) == 0:
        advance_x = max(float(base.get('advance_x', frame_width)), 1.0)
        x = frame_left + (frame_width - advance_x) / 2.0 + float(base.get('glyph_left', 0.0))
    else:
        x = frame_left + (frame_width - ink_w) / 2.0 - ink_x

    padding = max(1, int(round(float(padding_size if padding_size is not None else frame_width) * 0.05)))
    if translated in _VERTICAL_ALIGN_TOP_RIGHT:
        x = frame_left + frame_width - ink_w - ink_x - padding
    elif translated in _VERTICAL_ALIGN_BOTTOM_LEFT:
        x = frame_left - ink_x + padding
    return x


def _measure_horizontal_text_width(text: str, font_size: int, letter_spacing: float = 1.0) -> int:
    normalized = text or ''
    if not normalized:
        return 0
    if '\n' in normalized or '\r' in normalized:
        return max((_measure_horizontal_text_width(part, font_size, letter_spacing) for part in normalized.splitlines()), default=0)
    state = _state()
    key = ('logical-width', state.font_family or tuple(state.font_selection), bool(state.bold), int(font_size), round(_normalize_letter_spacing(letter_spacing), 4), normalized)
    cached = state.measures.get(key)
    if cached is not None:
        return cached
    _, _, _, line = _horizontal_line(normalized, font_size, letter_spacing)
    width = 0 if line is None else int(round(_line_logical_width(line, len(normalized))))
    if len(state.measures) >= 4096:
        state.measures.clear()
    state.measures[key] = width
    return width


def calc_horizontal_block_height(font_size: int, content: str, letter_spacing: float = 1.0) -> int:
    surface = _block_surface(font_size, content, 0, 0.0, letter_spacing)
    return font_size if surface is None or surface['height'] <= 0 else int(surface['height'])
