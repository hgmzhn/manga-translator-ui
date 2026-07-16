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
    _glyph_pair_rgba,
    _paste_bitmap,
    _paste_rgba,
    _rgba_from_alpha_pair,
    _stroke_alpha_from_text_alpha,
    _stroke_bitmap_from_alpha,
    _stroke_pad_px,
    _style_fill_color,
    _style_font_size,
    _style_layer_effects_geometry,
    _style_stroke_color,
    _style_stroke_ratio,
)
from ._fonts import _bold_scope, _create_text_layout, _layout_font, _state, _style_font_scope
from ._glyphs import GlyphRaster, _glyph_raster, _rasterize_path
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
    """Visible ink gap between adjacent horizontal lines.

    ``line_spacing`` scales a 0.1-em natural gap.  Line boxes themselves are
    content-derived, so this is the only vertical whitespace added by layout.
    """
    value = _normalize_line_spacing(line_spacing)
    return max(0, int(round(font_size * 0.10 * value)))


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
    _ = text_length
    return float(line.naturalTextWidth())


def _line_metrics(text: str, font_size: int, letter_spacing: float = 1.0) -> dict:
    normalized, qfont, _, line = _horizontal_line(text, font_size, letter_spacing)
    metrics = QFontMetricsF(qfont)
    if line is None:
        return {'text': normalized, 'logical_width': 0.0, 'ascent': float(metrics.ascent()), 'height': float(metrics.height()), 'descent': float(metrics.descent())}
    return {'text': normalized, 'logical_width': _line_logical_width(line, len(normalized)), 'ascent': float(line.ascent()), 'height': float(line.height()), 'descent': float(line.descent())}


def _horizontal_glyph_path(
    line_text: str,
    font_size: int,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
):
    """Shape one horizontal span and return its exact vector ink path.

    QTextLayout remains responsible for glyph selection and baseline positions,
    but line fitting never consumes its ascent/descent box.  The returned path
    is the actual union of the shaped glyph outlines in QTextLine coordinates.
    """
    stage_t0 = perf_counter() if profile_stats is not None else None
    normalized, _, layout, line = _horizontal_line(line_text, font_size, letter_spacing)
    _profile_add(profile_stats, "tr_layout_ms", stage_t0)
    if not line_text or line is None:
        return normalized, layout, line, QPainterPath()

    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    stage_t0 = perf_counter() if profile_stats is not None else None
    for glyph_run in layout.glyphRuns():
        raw_font = glyph_run.rawFont()
        for glyph_id, pos in zip(glyph_run.glyphIndexes(), glyph_run.positions()):
            glyph_path = raw_font.pathForGlyph(glyph_id)
            if glyph_path.isEmpty():
                continue
            glyph_path.translate(pos.x(), pos.y())
            path.addPath(glyph_path)
    _profile_add(profile_stats, "tr_path_ms", stage_t0)
    return normalized, layout, line, path


def _line_ink_geometry(
    line_text: str,
    font_size: int,
    stroke_ratio: float = 0.0,
    reversed_direction: bool = False,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
) -> dict:
    """Return the fixed pixel frame of the shaped glyph ink.

    The floor/ceil policy is identical to ``_rasterize_path``.  Stroke padding
    is part of the frame, so measurement and rendering use one geometry without
    the former outer ``calc_box_from_font`` padding estimate.
    """
    normalized, layout, line, path = _horizontal_glyph_path(
        line_text,
        font_size,
        reversed_direction,
        letter_spacing,
        profile_stats,
    )
    logical_width = 0.0 if line is None else _line_logical_width(line, len(normalized))
    ascent = float(_line_metrics('', font_size, letter_spacing)['ascent']) if line is None else float(line.ascent())
    descent = float(_line_metrics('', font_size, letter_spacing)['descent']) if line is None else float(line.descent())
    if path.isEmpty():
        return {
            'path': path,
            'logical_width': float(logical_width),
            'ascent': ascent,
            'descent': descent,
            'left_rel': 0.0,
            'top_rel': 0.0,
            'width': 0,
            'height': 0,
            'has_ink': False,
        }

    rect = path.boundingRect()
    left = math.floor(rect.left())
    top = math.floor(rect.top())
    right = math.ceil(rect.right())
    bottom = math.ceil(rect.bottom())
    pad = _stroke_pad_px(font_size, stroke_ratio)
    left -= pad
    top -= pad
    right += pad
    bottom += pad
    origin_x = -logical_width if reversed_direction else 0.0
    return {
        'path': path,
        'layout': layout,
        'logical_width': float(logical_width),
        'ascent': ascent,
        'descent': descent,
        'left_rel': float(left) - origin_x,
        'top_rel': float(top) - ascent,
        'width': max(0, int(right - left)),
        'height': max(0, int(bottom - top)),
        'has_ink': right > left and bottom > top,
        'frame_left': int(left),
        'frame_top': int(top),
        'fill_left': int(left + pad),
        'fill_top': int(top + pad),
        'pad': int(pad),
    }


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
    geometry = _line_ink_geometry(
        line_text,
        font_size,
        stroke_ratio if border_size > 0 else 0.0,
        reversed_direction,
        letter_spacing,
        profile_stats,
    )
    path = geometry['path']
    if not geometry['has_ink']:
        return None
    stage_t0 = perf_counter() if profile_stats is not None else None
    fill_alpha, fill_left, fill_top = _rasterize_path(path)
    _profile_add(profile_stats, "tr_raster_ms", stage_t0)
    if fill_alpha.size == 0:
        return None
    frame_left = int(geometry['frame_left'])
    frame_top = int(geometry['frame_top'])
    frame_width = int(geometry['width'])
    frame_height = int(geometry['height'])
    text_canvas = np.zeros((frame_height, frame_width), dtype=np.uint8)
    border_canvas = np.zeros_like(text_canvas)
    _paste_bitmap(text_canvas, fill_alpha, fill_left - frame_left, fill_top - frame_top)
    if border_size > 0:
        stage_t0 = perf_counter() if profile_stats is not None else None
        stroke_px = max(int(stroke_ratio * font_size), 1)
        border_alpha, border_dx, border_dy = _stroke_alpha_from_text_alpha(fill_alpha, stroke_px)
        border_left, border_top = fill_left + border_dx, fill_top + border_dy
        _paste_bitmap(border_canvas, border_alpha, border_left - frame_left, border_top - frame_top)
        _profile_add(profile_stats, "tr_stroke_ms", stage_t0)
    result = {
        'text': text_canvas,
        'border': border_canvas,
        'left_rel': geometry['left_rel'],
        'right_rel': geometry['left_rel'] + frame_width,
        'top_rel': geometry['top_rel'],
        'width': frame_width,
        'height': frame_height,
        'logical_width': geometry['logical_width'],
        'line_ascent': geometry['ascent'],
        'line_descent': geometry['descent'],
        'ink_top': geometry['top_rel'],
        'ink_bottom': geometry['top_rel'] + frame_height,
    }
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
        'height': float(surface.get('height', font_size)),
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


def _rich_horizontal_main_rect(run: dict) -> Tuple[float, float, float, float]:
    """Transformed main-ink rectangle relative to run cursor and baseline."""
    if not run.get('has_ink'):
        return (0.0, 0.0, 0.0, 0.0)
    span = run['span']
    left = float(run['left_rel'])
    top = float(run['top_rel'])
    height = int(run['ink_height'])
    width = int(run['ink_width'])
    out_h, out_w, dx, dy = _style_layer_effects_geometry(height, width, span.style)
    return (
        left + float(dx) + span.style.transform.offset_x,
        top + float(dy) + span.style.transform.offset_y,
        float(out_w),
        float(out_h),
    )


def _measure_rich_horizontal_run(
    span: RenderSpan,
    base_font_size: int,
    global_stroke_ratio: float,
    stroke_enabled,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
) -> dict:
    font_size = _style_font_size(base_font_size, span.style)
    stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, stroke_enabled)
    with _style_font_scope(span.style):
        geometry = _line_ink_geometry(
            span.text,
            font_size,
            stroke_ratio,
            reversed_direction,
            letter_spacing,
            profile_stats,
        )
    left_rel = float(geometry['left_rel'])
    if reversed_direction:
        left_rel -= float(geometry['logical_width'])
    return {
        'span': span,
        'font_size': font_size,
        'stroke_ratio': stroke_ratio,
        'surface': None,
        'logical_width': float(geometry['logical_width']),
        'ascent': float(geometry['ascent']),
        'descent': float(geometry['descent']),
        'has_ink': bool(geometry['has_ink']),
        'left_rel': left_rel,
        'top_rel': float(geometry['top_rel']),
        'ink_width': int(geometry['width']),
        'ink_height': int(geometry['height']),
    }


def _measure_ruby_layer(run: dict, stroke_enabled, letter_spacing: float) -> Optional[dict]:
    span = run['span']
    ruby_text = ''.join(item.text for item in (span.ruby or []))
    if not ruby_text:
        return None
    ruby_style = span.style.copy()
    ruby_style.emphasis = False
    ruby_font = max(1, int(round(run['font_size'] * 0.42)))
    ruby_stroke_ratio = _style_stroke_ratio(ruby_style, ruby_font, 0.0, stroke_enabled)
    with _style_font_scope(ruby_style):
        geometry = _line_ink_geometry(ruby_text, ruby_font, ruby_stroke_ratio, False, letter_spacing)
    if not geometry['has_ink']:
        return None
    out_h, out_w, _, _ = _style_layer_effects_geometry(
        int(geometry['height']), int(geometry['width']), ruby_style
    )
    return {'width': int(out_w), 'height': int(out_h), 'font_size': ruby_font}


def _finalize_rich_horizontal_line(runs: list, base_font_size: int, letter_spacing: float) -> dict:
    cursor = 0.0
    body_rects = []
    paint_rects = []
    for run in runs:
        main_rect = _rich_horizontal_main_rect(run)
        run['main_rect'] = main_rect
        if main_rect[2] > 0 and main_rect[3] > 0:
            rect = (cursor + main_rect[0], main_rect[1], main_rect[2], main_rect[3])
            body_rects.append(rect)
            paint_rects.append(rect)

            ruby = run.get('ruby_box')
            if ruby:
                gap = max(1, int(round(run['font_size'] * 0.08)))
                ruby_x = cursor + run['logical_width'] / 2.0 - ruby['width'] / 2.0
                ruby_y = main_rect[1] - gap - ruby['height']
                run['ruby_rect'] = (ruby_x - cursor, ruby_y, float(ruby['width']), float(ruby['height']))
                paint_rects.append((ruby_x, ruby_y, float(ruby['width']), float(ruby['height'])))

            if run['span'].style.emphasis:
                radius = max(1, int(round(run['font_size'] * 0.055)))
                size = radius * 2 + 3
                gap = max(1, int(round(run['font_size'] * 0.08)))
                top = main_rect[1] + main_rect[3] + gap
                run['emphasis_center_y'] = top + size // 2
                char_cursor = cursor
                for char in run['span'].text:
                    advance = _measure_horizontal_text_width(char, run['font_size'], letter_spacing)
                    center_x = char_cursor + advance / 2.0
                    paint_rects.append((center_x - size // 2, top, float(size), float(size)))
                    char_cursor += advance
        cursor += float(run['logical_width'])

    logical_width = sum(float(run['logical_width']) for run in runs)
    if not paint_rects:
        half = max(float(base_font_size), 1.0) / 2.0
        return {
            'runs': runs,
            'logical_width': logical_width,
            'body_left': 0.0,
            'body_right': logical_width,
            'body_top': -half,
            'body_bottom': half,
            'paint_left': 0.0,
            'paint_right': logical_width,
            'paint_top': -half,
            'paint_bottom': half,
            'blank': True,
        }

    def bounds(rects):
        left = min(rect[0] for rect in rects)
        top = min(rect[1] for rect in rects)
        right = max(rect[0] + rect[2] for rect in rects)
        bottom = max(rect[1] + rect[3] for rect in rects)
        return left, top, right, bottom

    body_left, body_top, body_right, body_bottom = bounds(body_rects)
    paint_left, paint_top, paint_right, paint_bottom = bounds(paint_rects)
    return {
        'runs': runs,
        'logical_width': logical_width,
        'body_left': body_left,
        'body_right': body_right,
        'body_top': body_top,
        'body_bottom': body_bottom,
        'paint_left': paint_left,
        'paint_right': paint_right,
        'paint_top': paint_top,
        'paint_bottom': paint_bottom,
        'blank': False,
    }


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
    """Build one content-derived ink plan for horizontal text.

    Pure and rich text share this plan.  QText baselines are retained only as
    drawing coordinates; line fitting and vertical placement consume the real
    shaped glyph/effect rectangles.
    """
    layouts = []
    for paragraph in document.paragraphs:
        runs = []
        for span in paragraph.spans:
            if not span.text:
                continue
            if measure_only:
                run = _measure_rich_horizontal_run(
                    span, base_font_size, global_stroke_ratio, bg,
                    reversed_direction, letter_spacing, profile_stats,
                )
            else:
                run = _rich_span_surface(
                    span, base_font_size, global_stroke_ratio, bg,
                    reversed_direction, letter_spacing, profile_stats,
                )
                if run is None:
                    run = _measure_rich_horizontal_run(
                        span, base_font_size, global_stroke_ratio, bg,
                        reversed_direction, letter_spacing, profile_stats,
                    )
                else:
                    surface = run['surface']
                    run.update({
                        'has_ink': True,
                        'left_rel': float(surface['left_rel']) - (run['logical_width'] if reversed_direction else 0.0),
                        'top_rel': float(surface['top_rel']),
                        'ink_width': int(surface['width']),
                        'ink_height': int(surface['height']),
                    })
            if span.ruby and run.get('has_ink'):
                run['ruby_box'] = _measure_ruby_layer(run, bg, letter_spacing)
                if not measure_only and run['ruby_box']:
                    run['ruby'] = _rich_ruby_surface(
                        span, run['font_size'], fg, bg, letter_spacing, profile_stats
                    )
            runs.append(run)
        layouts.append(_finalize_rich_horizontal_line(runs, base_font_size, letter_spacing))
    return layouts


def _rich_horizontal_layout_geometry(layouts: list, font_size: int, line_spacing: float) -> dict:
    """Place real line ink boxes and return their normalized render frame."""
    gap = calc_horizontal_line_spacing_px(font_size, line_spacing)
    body_width = max((float(layout['logical_width']) for layout in layouts), default=0.0)
    left_extra = max((max(0.0, -float(layout['paint_left'])) for layout in layouts), default=0.0)
    right_extra = max((max(0.0, float(layout['paint_right']) - float(layout['logical_width'])) for layout in layouts), default=0.0)

    baselines = []
    if layouts:
        baselines.append(-float(layouts[0]['paint_top']))
        for previous, current in zip(layouts, layouts[1:]):
            advance = float(previous['paint_bottom']) - float(current['paint_top']) + gap
            baselines.append(baselines[-1] + max(1.0, advance))

    paint_top = min((baseline + layout['paint_top'] for baseline, layout in zip(baselines, layouts)), default=0.0)
    paint_bottom = max((baseline + layout['paint_bottom'] for baseline, layout in zip(baselines, layouts)), default=0.0)
    body_top = min((baseline + layout['body_top'] for baseline, layout in zip(baselines, layouts)), default=0.0)
    body_bottom = max((baseline + layout['body_bottom'] for baseline, layout in zip(baselines, layouts)), default=0.0)
    centered_body_left = min((
        (body_width - float(layout['logical_width'])) / 2.0 + float(layout['body_left'])
        for layout in layouts
    ), default=0.0)
    centered_body_right = max((
        (body_width - float(layout['logical_width'])) / 2.0 + float(layout['body_right'])
        for layout in layouts
    ), default=body_width)

    frame_left = math.floor(-left_extra)
    frame_right = math.ceil(body_width + right_extra)
    frame_top = math.floor(paint_top)
    frame_bottom = math.ceil(paint_bottom)
    body_left = -frame_left
    normalized_baselines = [baseline - frame_top for baseline in baselines]
    return {
        'spacing_y': int(gap),
        'body_width': float(body_width),
        'body_height': float(max(0.0, body_bottom - body_top)),
        'left_extra': int(body_left),
        'right_extra': int(frame_right - math.ceil(body_width)),
        'top_extra': int(-frame_top),
        'bottom_extra': int(frame_bottom - math.ceil(body_bottom)),
        'paint_width': int(max(0, frame_right - frame_left)),
        'paint_height': int(max(0, frame_bottom - frame_top)),
        'baselines': normalized_baselines,
        'body_center': (
            float((centered_body_left + centered_body_right) / 2.0 - frame_left),
            float((body_top + body_bottom) / 2.0 - frame_top),
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
