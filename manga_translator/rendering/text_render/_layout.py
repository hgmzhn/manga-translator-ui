"""布局层：横排行/竖排列的富文本布局 builder 与包络几何。

测量与绘制共用同一套几何计划，像素光栅化只发生在渲染层——这是
"测量框 == 绘制输出面"契约的实现处。竖排字符槽位规则（旋转/贴边/
半宽/紧凑）也在本模块。
"""
import math
import re
from dataclasses import replace
from time import perf_counter
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetricsF, QPainterPath, QTransform

from ..rich_text import RenderSpan, RichTextDocument, normalize_rich_linebreaks
from ._compose import (
    _bitmap_ink_rect,
    _paste_bitmap,
    _stroke_alpha_from_text_alpha,
    _stroke_pad_px,
    _style_fill_color,
    _style_font_size,
    _style_italic_shear,
    _style_layer_effects_geometry,
    _style_stroke_color,
    _style_stroke_ratio,
)
from ._fonts import _bold_scope, _create_text_layout, _layout_font, _state, _style_font_scope
from ._glyphs import GlyphRaster, _glyph_raster, _rasterize_path
from ._plans import (
    FlowAxis,
    Bounds,
    HorizontalLinePlan,
    HorizontalRunPlan,
    RubyGlyph,
    RubyPlan,
    Rect,
    TcyPlan,
    plan_emphasis,
    plan_ruby_glyphs,
)
from ._policy import RICH_TEXT_POLICY
from ._shared import _VERTICAL_CACHE_MAX, _cache_get, _cache_put, _profile_add
from ._vertical_types import (
    VerticalCharPlan,
    VerticalColumnPlan,
    VerticalGlyphBase,
    VerticalPlaceholderPlan,
)

_HORIZONTAL_SYMBOL_HALFWIDTH_MAP = str.maketrans({'！': '!', '？': '?'})
# 普通自动旋转字符已移到 rich_text_rules.yaml。四个弯引号与四个日文
# 角引号保留渲染引擎特殊路径：自动旋转 90°，再做顶右/底左定位。
_VERTICAL_ROTATE_OPEN_SPECIALS = {'“', '‘', '「', '『'}
_VERTICAL_ROTATE_CLOSE_SPECIALS = {'”', '’', '」', '』'}
_VERTICAL_OPEN_BRACKETS = _VERTICAL_ROTATE_OPEN_SPECIALS | {'﹁', '﹃', '︵', '︷', '︹', '︻', '︽', '︿', '﹇'}
_VERTICAL_CLOSE_BRACKETS = _VERTICAL_ROTATE_CLOSE_SPECIALS | {'﹂', '﹄', '︶', '︸', '︺', '︼', '︾', '﹀', '﹈'}
_VERTICAL_PUNCT_UP = {'。', '．', '，', '、', '·', '：', '；', '！', '？', '!', '?', '︒', '︐', '︑', '︓', '︔', '︕', '︖', '﹅', '﹆'}
_VERTICAL_ROTATE_CHARS = _VERTICAL_ROTATE_OPEN_SPECIALS | _VERTICAL_ROTATE_CLOSE_SPECIALS
_VERTICAL_COMPACT_SLOT = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS | _VERTICAL_PUNCT_UP
_VERTICAL_HALF_ADVANCE = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS

_VERTICAL_ALIGN_TOP_RIGHT = {'﹁', '﹃'} | _VERTICAL_ROTATE_OPEN_SPECIALS
_VERTICAL_ALIGN_BOTTOM_LEFT = {'﹂', '﹄'} | _VERTICAL_ROTATE_CLOSE_SPECIALS
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
    if direction == 1 and cdpt in _VERTICAL_ROTATE_CHARS:
        return cdpt, 90
    return cdpt, 0


def _normalize_horizontal_block_content(content: str) -> str:
    # F14：BR 编解码统一走 rich_text.normalize_rich_linebreaks（BR→\n 后去换行，
    # 与旧的 _BR_RE.sub('') 输出一致）
    content = normalize_rich_linebreaks(content).replace('\r', '').replace('\n', '')
    return content.translate(_HORIZONTAL_SYMBOL_HALFWIDTH_MAP) if re.fullmatch(r'[!?！？]+', content) else content


def _rich_vertical_ruby_space(font_size: int) -> int:
    return int(round(font_size * RICH_TEXT_POLICY.vertical_ruby_side_space))


def _adjacent_line_adjustment(previous, current, font_size: int) -> float:
    """当前行 LK 优先，否则使用上一行 NK；值为字号比例。"""
    if current.line_kerning is not None:
        return float(current.line_kerning) * font_size
    if previous.next_kerning is not None:
        return float(previous.next_kerning) * font_size
    return 0.0


def _rich_vertical_line_gap(spacing_x: int, previous: VerticalColumnPlan, current: VerticalColumnPlan) -> int:
    base = max(int(spacing_x), current.annotation_cross_extent)
    return int(round(base + _adjacent_line_adjustment(previous, current, max(previous.thickness, current.thickness))))


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


def _rich_vertical_layout_geometry(
    layouts: list[VerticalColumnPlan],
    font_size: int,
    line_spacing: float,
) -> dict:
    spacing_x = calc_vertical_line_spacing_px(font_size, line_spacing)
    body_width = sum(column.thickness for column in layouts)
    body_width += spacing_x * max(0, len(layouts) - 1)
    gaps = [
        _rich_vertical_line_gap(spacing_x, previous, current)
        for previous, current in zip(layouts, layouts[1:])
    ]
    layout_width = sum(column.thickness for column in layouts) + sum(gaps)
    # 紧凑框：各列 content_paint_bounds 先经列位游走换算成正文带内的绝对
    # 区间再取并集——中间列的斜体切变/描边外扩落在列间隙或邻列区域内，
    # 不放大整框；只有真正越过 [0, layout_width] 的墨迹才计入 extras。
    # 右侧另含首列注音/着重号。正文列区间 [left_extra, left_extra+layout_width]
    # 因此一般不在 paint 框正中，正文中心由 body_center_x 显式给出。
    column_edges = _vertical_column_walk(
        [float(column.thickness) for column in layouts], gaps, float(layout_width)
    )
    paint_left = min((
        column_left + column.content_paint_bounds.left
        for (column_left, _), column in zip(column_edges, layouts)
    ), default=0.0)
    paint_right = max((
        column_left + column.content_paint_bounds.right
        for (column_left, _), column in zip(column_edges, layouts)
    ), default=float(layout_width))
    left_extra = int(math.ceil(max(0.0, -paint_left)))
    right_extra = max(
        layouts[0].annotation_cross_extent if layouts else 0,
        int(math.ceil(max(0.0, paint_right - layout_width))),
    )
    # 纵向包络：正文高 = 最高列的游走高度；上下 extras 由各列图层实际
    # 纵向溢出（偏移/切变/描边外扩）取最大值，测量与绘制共用。
    body_height = max((column.height for column in layouts), default=0)
    top_extra = max((
        int(max(0.0, -column.content_paint_bounds.top))
        for column in layouts
    ), default=0)
    bottom_extra = max((
        int(max(0.0, column.content_paint_bounds.bottom - column.height))
        for column in layouts
    ), default=0)
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


def _rich_vertical_column_positions(
    layouts: list[VerticalColumnPlan],
    geometry: dict,
    origin_x: float = 0.0,
) -> list:
    origin_right = float(origin_x) + geometry['left_extra'] + geometry['layout_width']
    widths = [float(column.thickness) for column in layouts]
    gaps = [
        _rich_vertical_line_gap(geometry['spacing_x'], layouts[idx], layouts[idx + 1])
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


def _forced_vertical_advance(font_size: int, mode: Optional[str]) -> Optional[int]:
    if mode == 'half':
        return max(1, int(round(font_size * 0.5)))
    if mode == 'full':
        return max(1, int(font_size))
    return None


def _horizontal_line(text: str, font_size: int, letter_spacing: float = 1.0):
    return _create_text_layout(text or '', font_size, letter_spacing)


def _line_logical_width(line) -> float:
    return float(line.naturalTextWidth())


def _line_metrics(text: str, font_size: int, letter_spacing: float = 1.0) -> dict:
    normalized, qfont, _, line = _horizontal_line(text, font_size, letter_spacing)
    metrics = QFontMetricsF(qfont)
    if line is None:
        return {'text': normalized, 'logical_width': 0.0, 'ascent': float(metrics.ascent()), 'height': float(metrics.height()), 'descent': float(metrics.descent())}
    return {'text': normalized, 'logical_width': _line_logical_width(line), 'ascent': float(line.ascent()), 'height': float(line.height()), 'descent': float(line.descent())}


def _horizontal_glyph_path(
    line_text: str,
    font_size: int,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    shear: float = 0.0,
):
    """Shape one horizontal span and return its exact vector ink path.

    QTextLayout remains responsible for glyph selection and baseline positions,
    but line fitting never consumes its ascent/descent box.  The returned path
    is the actual union of the shaped glyph outlines in QTextLine coordinates.

    ``shear`` 为斜体切变系数：pathForGlyph 轮廓以基线为原点，剪切在平移到
    笔位之前施加，因此天然绕各字形基线、不改变 advance（PS 仿斜体语义）。
    """
    stage_t0 = perf_counter() if profile_stats is not None else None
    normalized, _, layout, line = _horizontal_line(line_text, font_size, letter_spacing)
    _profile_add(profile_stats, "tr_layout_ms", stage_t0)
    if not line_text or line is None:
        return normalized, layout, line, QPainterPath()

    shear_transform = QTransform(1.0, 0.0, float(shear), 1.0, 0.0, 0.0) if shear else None
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    stage_t0 = perf_counter() if profile_stats is not None else None
    for glyph_run in layout.glyphRuns():
        raw_font = glyph_run.rawFont()
        for glyph_id, pos in zip(glyph_run.glyphIndexes(), glyph_run.positions()):
            glyph_path = raw_font.pathForGlyph(glyph_id)
            if glyph_path.isEmpty():
                continue
            if shear_transform is not None:
                glyph_path = shear_transform.map(glyph_path)
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
    shear: float = 0.0,
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
        shear,
    )
    logical_width = 0.0 if line is None else _line_logical_width(line)
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


def _line_surface(
    line_text: str,
    font_size: int,
    border_size: int,
    stroke_ratio: float = 0.07,
    reversed_direction: bool = False,
    letter_spacing: float = 1.0,
    bold: bool = False,
    profile_stats: Optional[dict] = None,
    geometry: Optional[dict] = None,
    shear: float = 0.0,
):
    # ``geometry`` 只允许来自当前一次布局调用；它不是跨字号/字体的缓存。
    # 省略时保留原有的独立测量路径，竖排和外部调用无需携带任何状态。
    # 携带 geometry 时其 path 已含 shear，本参数只在重算分支生效。
    effective_bold = bool(bold) or _state().bold
    with _bold_scope(effective_bold):
        if geometry is None:
            geometry = _line_ink_geometry(
                line_text,
                font_size,
                stroke_ratio if border_size > 0 else 0.0,
                reversed_direction,
                letter_spacing,
                profile_stats,
                shear,
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
            stroke_px = max(int(round(stroke_ratio * font_size)), 1)
            border_alpha, border_dx, border_dy = _stroke_alpha_from_text_alpha(fill_alpha, stroke_px)
            border_left, border_top = fill_left + border_dx, fill_top + border_dy
            _paste_bitmap(border_canvas, border_alpha, border_left - frame_left, border_top - frame_top)
            _profile_add(profile_stats, "tr_stroke_ms", stage_t0)
    return {
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


def _build_horizontal_run_plan(
    span: RenderSpan,
    base_font_size: int,
    global_stroke_ratio: float,
    global_stroke_color,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    geometry_sink: Optional[dict] = None,
) -> HorizontalRunPlan:
    text = span.text
    font_size = _style_font_size(base_font_size, span.style)
    stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, global_stroke_color)
    effective_bold = bool(span.style.bold) or _state().bold
    with _style_font_scope(span.style), _bold_scope(effective_bold):
        geometry = _line_ink_geometry(
            text,
            font_size,
            stroke_ratio,
            reversed_direction,
            letter_spacing,
            profile_stats,
            _style_italic_shear(span.style),
        )
    left_rel = float(geometry['left_rel'])
    if reversed_direction:
        left_rel -= float(geometry['logical_width'])
    run = HorizontalRunPlan(
        span=span,
        font_size=font_size,
        stroke_ratio=stroke_ratio,
        logical_width=float(geometry['logical_width']),
        ascent=float(geometry['ascent']),
        descent=float(geometry['descent']),
        has_ink=bool(geometry['has_ink']),
        left_rel=left_rel,
        top_rel=float(geometry['top_rel']),
        ink_width=int(geometry['width']),
        ink_height=int(geometry['height']),
    )
    if geometry_sink is not None:
        geometry_sink[id(run)] = geometry
    return run


def _build_horizontal_ruby_plan(
    run: HorizontalRunPlan,
    stroke_enabled,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    geometry_sink: Optional[dict] = None,
) -> Optional[RubyPlan]:
    """Lay ruby characters out in equal slots across the complete base run.

    The reference editor does not render the annotation as one compact string:
    each ruby character is centered in ``base_width / ruby_count``.  Measurement
    and drawing both use this plan so their paint envelopes stay identical.
    """
    span = run.span
    ruby_text = ''.join(item.text for item in (span.ruby or []))
    if not ruby_text:
        return None
    ruby_style = span.style.copy()
    ruby_style.emphasis = False
    ruby_font = max(1, int(round(run.font_size * RICH_TEXT_POLICY.horizontal_ruby_size)))
    ruby_stroke_ratio = _style_stroke_ratio(ruby_style, ruby_font, 0.0, stroke_enabled)
    raw_glyphs = []
    glyph_geometries = []
    ruby_shear = _style_italic_shear(ruby_style)
    with _style_font_scope(ruby_style):
        for char in ruby_text:
            geometry = _line_ink_geometry(
                char,
                ruby_font,
                ruby_stroke_ratio,
                False,
                letter_spacing,
                profile_stats,
                ruby_shear,
            )
            glyph_geometries.append(geometry)
            if not geometry['has_ink']:
                raw_glyphs.append((0, 0))
                continue
            out_h, out_w, _, _ = _style_layer_effects_geometry(
                int(geometry['height']), int(geometry['width']), ruby_style, ruby_font
            )
            raw_glyphs.append((int(out_w), int(out_h)))

    visible = [glyph for glyph in raw_glyphs if glyph[0] > 0 and glyph[1] > 0]
    if not visible:
        return None
    base_width = max(0.0, run.logical_width)
    placements = plan_ruby_glyphs(
        ruby_text,
        0.0,
        base_width,
        FlowAxis.HORIZONTAL,
    )
    glyphs = tuple(
        RubyGlyph(
            char=placement.char,
            main_center=placement.main_center,
            main_scale=placement.main_scale,
            paint_width=width,
            paint_height=height,
        )
        for placement, (width, height) in zip(placements, raw_glyphs)
    )
    if geometry_sink is not None:
        for glyph, geometry in zip(glyphs, glyph_geometries):
            geometry_sink[id(glyph)] = geometry
    return RubyPlan(
        source=span,
        font_size=ruby_font,
        stroke_ratio=ruby_stroke_ratio,
        glyphs=glyphs,
        paint_start=float(math.floor(min(
            [0.0] + [glyph.main_center - glyph.paint_width / 2.0 for glyph in glyphs if glyph.paint_width]
        ))),
        paint_end=float(math.ceil(max(
            [base_width] + [glyph.main_center + glyph.paint_width / 2.0 for glyph in glyphs if glyph.paint_width]
        ))),
    )


def _rich_horizontal_main_rect(run: HorizontalRunPlan) -> Rect:
    """Transformed main-ink rectangle relative to run cursor and baseline."""
    if not run.has_ink:
        return Rect(0.0, 0.0, 0.0, 0.0)
    span = run.span
    left = run.left_rel
    top = run.top_rel
    height = run.ink_height
    width = run.ink_width
    out_h, out_w, dx, dy = _style_layer_effects_geometry(height, width, span.style, run.font_size)
    return Rect(
        left + float(dx) + span.style.transform.offset_x * run.font_size / 100.0,
        top + float(dy) + span.style.transform.offset_y * run.font_size / 100.0,
        float(out_w),
        float(out_h),
    )


def _paragraph_line_spacing_values(paragraph) -> tuple[float | None, float | None]:
    line_value = next((span.style.line_kerning for span in paragraph.spans if span.style.line_kerning is not None), None)
    next_value = next((span.style.next_kerning for span in paragraph.spans if span.style.next_kerning is not None), None)
    return line_value, next_value


def _finalize_rich_horizontal_line(
    runs: list[HorizontalRunPlan],
    base_font_size: int,
    letter_spacing: float,
    line_kerning: float | None = None,
    next_kerning: float | None = None,
) -> HorizontalLinePlan:
    cursor = 0.0
    body_rects = []
    paint_rects = []
    for run in runs:
        main_rect = _rich_horizontal_main_rect(run)
        run.main_rect = main_rect
        if main_rect.width > 0 and main_rect.height > 0:
            rect = (cursor + main_rect.x, main_rect.y, main_rect.width, main_rect.height)
            body_rects.append(rect)
            paint_rects.append(rect)

            ruby = run.ruby
            if ruby is not None:
                gap = max(1, int(round(run.font_size * RICH_TEXT_POLICY.decoration_gap)))
                ruby_height = max(glyph.paint_height for glyph in ruby.glyphs)
                ruby.cross_center = main_rect.y - gap - ruby_height / 2.0
                paint_rects.append((
                    cursor + ruby.paint_start,
                    ruby.cross_center - ruby_height / 2.0,
                    ruby.paint_end - ruby.paint_start,
                    float(ruby_height),
                ))

            if run.span.style.emphasis:
                gap = max(1, int(round(run.font_size * RICH_TEXT_POLICY.decoration_gap)))
                char_cursor = cursor
                intervals = []
                for char in run.span.text:
                    advance = _measure_horizontal_text_width(char, run.font_size, letter_spacing)
                    intervals.append((char_cursor - cursor, char_cursor - cursor + advance))
                    char_cursor += advance
                emphasis = plan_emphasis(
                    run.span,
                    tuple(intervals),
                    run.font_size,
                )
                top = main_rect.y + main_rect.height + gap
                emphasis.cross_center = top + emphasis.frame_size / 2.0
                run.emphasis = emphasis
                for main_center in emphasis.main_centers:
                    paint_rects.append((
                        cursor + main_center - emphasis.frame_size / 2.0,
                        top,
                        float(emphasis.frame_size),
                        float(emphasis.frame_size),
                    ))
        cursor += run.logical_width

    logical_width = sum(run.logical_width for run in runs)
    if not paint_rects:
        half = max(float(base_font_size), 1.0) / 2.0
        bounds = Bounds(0.0, -half, logical_width, half)
        return HorizontalLinePlan(tuple(runs), logical_width, bounds, bounds, line_kerning, next_kerning)

    def bounds(rects):
        left = min(rect[0] for rect in rects)
        top = min(rect[1] for rect in rects)
        right = max(rect[0] + rect[2] for rect in rects)
        bottom = max(rect[1] + rect[3] for rect in rects)
        return left, top, right, bottom

    body_left, body_top, body_right, body_bottom = bounds(body_rects)
    paint_left, paint_top, paint_right, paint_bottom = bounds(paint_rects)
    return HorizontalLinePlan(
        tuple(runs),
        logical_width,
        Bounds(body_left, body_top, body_right, body_bottom),
        Bounds(paint_left, paint_top, paint_right, paint_bottom),
        line_kerning,
        next_kerning,
    )


def _build_rich_horizontal_layout(
    document: RichTextDocument,
    base_font_size: int,
    global_stroke_ratio: float,
    bg,
    reversed_direction: bool,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
    geometry_sink: Optional[dict] = None,
):
    """Build one content-derived ink plan for horizontal text.

    Pure and rich text share this plan.  QText baselines are retained only as
    drawing coordinates; line fitting and vertical placement consume the real
    shaped glyph/effect rectangles.  ``geometry_sink`` is an optional,
    per-render handoff to the painter so shaping is not repeated immediately.
    """
    layouts = []
    for paragraph in document.paragraphs:
        runs: list[HorizontalRunPlan] = []
        for span in paragraph.spans:
            if not span.text:
                continue
            run = _build_horizontal_run_plan(
                span,
                base_font_size,
                global_stroke_ratio,
                bg,
                reversed_direction,
                letter_spacing,
                profile_stats,
                geometry_sink,
            )
            if span.ruby and run.has_ink:
                ruby_paint = _build_horizontal_ruby_plan(
                    run,
                    bg,
                    letter_spacing,
                    profile_stats=profile_stats,
                    geometry_sink=geometry_sink,
                )
                if ruby_paint is not None:
                    run.ruby = ruby_paint
            runs.append(run)
        line_kerning, next_kerning = _paragraph_line_spacing_values(paragraph)
        layouts.append(_finalize_rich_horizontal_line(
            runs, base_font_size, letter_spacing, line_kerning, next_kerning
        ))
    return layouts


def _rich_horizontal_layout_geometry(layouts: list[HorizontalLinePlan], font_size: int, line_spacing: float) -> dict:
    """Place real line ink boxes and return their normalized render frame."""
    gap = calc_horizontal_line_spacing_px(font_size, line_spacing)
    body_width = max((layout.logical_width for layout in layouts), default=0.0)
    left_extra = max((max(0.0, -layout.paint_bounds.left) for layout in layouts), default=0.0)
    right_extra = max((max(0.0, layout.paint_bounds.right - layout.logical_width) for layout in layouts), default=0.0)

    baselines = []
    if layouts:
        baselines.append(-layouts[0].paint_bounds.top)
        for previous, current in zip(layouts, layouts[1:]):
            local_gap = gap + _adjacent_line_adjustment(previous, current, font_size)
            advance = previous.paint_bounds.bottom - current.paint_bounds.top + local_gap
            baselines.append(baselines[-1] + max(1.0, advance))

    paint_top = min((baseline + layout.paint_bounds.top for baseline, layout in zip(baselines, layouts)), default=0.0)
    paint_bottom = max((baseline + layout.paint_bounds.bottom for baseline, layout in zip(baselines, layouts)), default=0.0)
    body_top = min((baseline + layout.body_bounds.top for baseline, layout in zip(baselines, layouts)), default=0.0)
    body_bottom = max((baseline + layout.body_bounds.bottom for baseline, layout in zip(baselines, layouts)), default=0.0)
    centered_body_left = min((
        (body_width - layout.logical_width) / 2.0 + layout.body_bounds.left
        for layout in layouts
    ), default=0.0)
    centered_body_right = max((
        (body_width - layout.logical_width) / 2.0 + layout.body_bounds.right
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


def _build_tcy_plan(
    span: RenderSpan,
    base_font_size: int,
    global_stroke_ratio: float,
    bg,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
) -> Optional[TcyPlan]:
    font_size = _style_font_size(base_font_size, span.style)
    stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, bg)
    text = _normalize_horizontal_block_content(span.text)
    with _style_font_scope(span.style):
        geometry = _line_ink_geometry(
            text,
            font_size,
            stroke_ratio,
            False,
            letter_spacing,
            profile_stats,
            _style_italic_shear(span.style),
        )
    if not geometry['has_ink']:
        return None
    # 纵中横压缩（对齐参考实现）：正文墨迹宽超过 1.1 倍基准字号时整组水平
    # 压缩到上限。压缩作用于最终图层（描边/特效随之变窄），判定只看纯墨迹
    # 宽（去掉描边 pad），与参考实现按字形墨迹累计的口径一致。
    ink_width = float(geometry['width']) - 2.0 * float(geometry['pad'])
    max_width = float(base_font_size) * RICH_TEXT_POLICY.tcy_max_width
    scale_x = max_width / ink_width if ink_width > max_width else 1.0
    height, width, layer_dx, layer_dy = _style_layer_effects_geometry(
        int(geometry['height']), int(geometry['width']), span.style, font_size
    )
    if scale_x < 1.0:
        width = max(1, int(math.ceil(width * scale_x)))
        layer_dx = float(layer_dx) * scale_x
    forced_advance = _forced_vertical_advance(font_size, span.style.vertical_advance)
    advance_main = int(height)
    if forced_advance is not None:
        advance_main = _scale_advance(forced_advance, letter_spacing)
        layer_dy += (float(advance_main) - float(height)) / 2.0
    return TcyPlan(
        source=span,
        text=text,
        font_size=font_size,
        stroke_ratio=stroke_ratio,
        width=int(width),
        height=int(height),
        paint_offset_x=float(layer_dx),
        paint_offset_y=float(layer_dy),
        advance_main=advance_main,
        pre_advance=int(round(span.style.pre_kerning * font_size)),
        post_advance=int(round(span.style.kerning * font_size)),
        scale_x=float(scale_x),
    )


def _build_vertical_char_plan(
    span: RenderSpan,
    base: VerticalGlyphBase,
    font_size: int,
    fill,
    stroke,
    stroke_ratio: float,
) -> VerticalCharPlan:
    width = height = 0
    off_x = off_y = 0.0
    bitmap = base.bitmap
    if bitmap is not None and bitmap.size:
        height, width = int(bitmap.shape[0]), int(bitmap.shape[1])
        if stroke_ratio > 0 and stroke is not None:
            stroke_px = max(int(round(stroke_ratio * font_size)), 1)
            pad = max(1, stroke_px) + 1
            height += pad * 2
            width += pad * 2
            off_x = off_y = float(-pad)
        height, width, layer_dx, layer_dy = _style_layer_effects_geometry(
            height, width, span.style, font_size
        )
        off_x += layer_dx
        off_y += layer_dy
    advance_y = int(base.advance_y)
    if span.style.transform.rotation and height > 0 and span.style.vertical_advance is None:
        advance_y = _vertical_free_rotation_advance(
            base,
            span.style.transform.rotation,
        )
        # 槽位上下均分伸缩量，保持旋转墨迹以原槽位中心为锚点。
        off_y += (float(advance_y) - float(base.advance_y)) / 2.0
    return VerticalCharPlan(
        span=span,
        base=base,
        font_size=font_size,
        fill=fill,
        stroke=stroke,
        stroke_ratio=stroke_ratio,
        advance_y=advance_y,
        pre_advance_y=int(round(span.style.pre_kerning * font_size)),
        post_advance_y=int(round(span.style.kerning * font_size)),
        paint_width=int(width),
        paint_height=int(height),
        paint_offset_x=float(off_x),
        paint_offset_y=float(off_y),
    )


def _rich_vertical_tcy_layer_x(body_left: float, thickness: float, item: TcyPlan) -> float:
    body_center = body_left + thickness / 2.0
    return (
        body_center
        - float(item.width) / 2.0
        + item.source.style.transform.offset_x * item.font_size / 100.0
        + item.paint_offset_x
    )


def _rich_vertical_char_layer_x(body_left: float, thickness: float, item: VerticalCharPlan) -> float:
    # 自由旋转字符按墨迹居中（对齐 BallonsTranslator）：旋转绕图层中心，
    # advance box 居中会把标点的 side bearing 偏心带进横向，墨迹居中不会。
    char_x = _vertical_char_bitmap_x(
        body_left,
        thickness,
        item.base,
        item.font_size,
        ink_center=(
            item.span.style.vertical_advance is not None
            or bool(item.span.style.transform.rotation)
        ),
    )
    return char_x + item.span.style.transform.offset_x * item.font_size / 100.0 + item.paint_offset_x


def _vertical_item_span(item) -> Optional[RenderSpan]:
    if isinstance(item, TcyPlan):
        return item.source
    return item.span


def _rich_vertical_item_paint_extra(item, thickness: int) -> Tuple[int, int]:
    if isinstance(item, TcyPlan):
        x = _rich_vertical_tcy_layer_x(0.0, float(thickness), item)
        width = float(item.width)
    elif isinstance(item, VerticalCharPlan) and item.paint_width > 0:
        x = _rich_vertical_char_layer_x(0.0, float(thickness), item)
        width = float(item.paint_width)
    else:
        return 0, 0
    left_extra = max(0.0, -x)
    right_extra = max(0.0, x + width - float(thickness))
    return int(math.ceil(left_extra)), int(math.ceil(right_extra))


def _rich_vertical_item_paint_extent_y(item) -> Optional[Tuple[float, float]]:
    """item 图层的纵向包络区间 [y0, y1)，相对列顶（cursor 原点）。

    与绘制路径的 y 公式同源：块 = cursor_y + transform 偏移 + 特效偏移；
    字符 = cursor_y + base.y + transform 偏移 + paint_offset_y。
    无图层的占位/空白项返回 None。
    """
    if isinstance(item, TcyPlan):
        y0 = item.main_start + item.source.style.transform.offset_y * item.font_size / 100.0 + item.paint_offset_y
        return y0, y0 + float(item.height)
    if isinstance(item, VerticalCharPlan) and item.paint_height > 0:
        y0 = (
            float(item.cursor_y)
            + float(item.base.y)
            + item.span.style.transform.offset_y * item.font_size / 100.0
            + item.paint_offset_y
        )
        return y0, y0 + float(item.paint_height)
    return None


def _build_rich_vertical_layout(
    document: RichTextDocument,
    base_font_size: int,
    global_stroke_ratio: float,
    fg,
    bg,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
):
    layouts = []
    thickness = max(1, int(base_font_size))
    for paragraph in document.paragraphs:
        items = []
        paint_left_extra = 0
        paint_right_extra = 0
        ruby_extra = 0
        emphasis_cross_extent = 0
        for span in paragraph.spans:
            font_size = _style_font_size(base_font_size, span.style)
            if span.tcy:
                tcy = _build_tcy_plan(
                    span,
                    base_font_size,
                    global_stroke_ratio,
                    bg,
                    letter_spacing,
                    profile_stats,
                )
                if tcy is not None:
                    items.append(tcy)
                continue
            fill = _style_fill_color(span.style, fg)
            stroke = _style_stroke_color(span.style, bg)
            stroke_ratio = _style_stroke_ratio(span.style, font_size, global_stroke_ratio, bg)
            span_ruby_extra = _rich_vertical_ruby_space(font_size) if span.ruby else 0
            ruby_extra = max(ruby_extra, span_ruby_extra)
            # 字体作用域提升到 span 层，避免带 fontFamily 的 span 逐字符
            # 反复 set_font（每次都会清空测量/竖排缓存导致缓存永不命中）
            span_shear = _style_italic_shear(span.style)
            with _style_font_scope(span.style):
                for char in span.text:
                    if char == '＿':
                        items.append(VerticalPlaceholderPlan(
                            span=span,
                            font_size=font_size,
                            advance_y=_scale_advance(font_size, letter_spacing),
                            pre_advance_y=int(round(span.style.pre_kerning * font_size)),
                            post_advance_y=int(round(span.style.kerning * font_size)),
                        ))
                        continue
                    base = _vertical_base(
                        font_size,
                        char,
                        letter_spacing,
                        span_shear,
                        span.style.vertical_advance,
                    )
                    item = _build_vertical_char_plan(
                        span, base, font_size, fill, stroke, stroke_ratio
                    )
                    left_extra, right_extra = _rich_vertical_item_paint_extra(item, thickness)
                    paint_left_extra = max(paint_left_extra, left_extra)
                    paint_right_extra = max(paint_right_extra, right_extra)
                    items.append(item)
        cursor = 0
        laid = []
        for item in items:
            if isinstance(item, TcyPlan):
                cursor += item.pre_advance
                placed = replace(item, main_start=float(cursor))
                cursor += item.advance_main
                cursor += item.post_advance
                left_extra, right_extra = _rich_vertical_item_paint_extra(placed, thickness)
                paint_left_extra = max(paint_left_extra, left_extra)
                paint_right_extra = max(paint_right_extra, right_extra)
                laid.append(placed)
                continue
            cursor += item.pre_advance_y
            item = replace(item, cursor_y=cursor)
            cursor += item.advance_y
            cursor += item.post_advance_y
            laid.append(item)
        column_height = max(0, int(cursor))
        ruby_plans = []
        emphasis_plans = []
        item_index = 0
        while item_index < len(laid):
            item = laid[item_index]
            if isinstance(item, TcyPlan):
                item_index += 1
                continue
            span = _vertical_item_span(item)
            if span is None:
                item_index += 1
                continue
            group_end = item_index + 1
            while group_end < len(laid) and _vertical_item_span(laid[group_end]) is span:
                group_end += 1
            group_items = laid[item_index:group_end]
            ruby_items = [
                candidate for candidate in group_items
                if isinstance(candidate, (VerticalCharPlan, VerticalPlaceholderPlan))
            ]
            if span.ruby and ruby_items:
                start_y = float(ruby_items[0].cursor_y)
                end_y = max(
                    float(candidate.cursor_y) + float(candidate.advance_y)
                    for candidate in ruby_items
                )
                group_font_size = ruby_items[0].font_size
                ruby_text = ''.join(run.text for run in span.ruby)
                ruby_size = max(1, int(round(
                    group_font_size * RICH_TEXT_POLICY.vertical_ruby_size
                )))
                glyphs = plan_ruby_glyphs(
                    ruby_text,
                    start_y,
                    end_y,
                    FlowAxis.VERTICAL,
                    nominal_glyph_extent=ruby_size,
                )
                if glyphs:
                    ruby_plans.append(RubyPlan(
                        source=span,
                        font_size=group_font_size,
                        stroke_ratio=0.0,
                        glyphs=glyphs,
                        paint_start=min(
                            glyph.main_center - ruby_size * glyph.main_scale / 2.0
                            for glyph in glyphs
                        ),
                        paint_end=max(
                            glyph.main_center + ruby_size * glyph.main_scale / 2.0
                            for glyph in glyphs
                        ),
                        cross_center=max(1.0, ruby_extra / 2.0),
                    ))
            drawable_items = [
                candidate for candidate in group_items
                if isinstance(candidate, VerticalCharPlan)
                and candidate.paint_width > 0
            ]
            if span.style.emphasis and drawable_items:
                group_font_size = drawable_items[0].font_size
                intervals = tuple(
                    (
                        float(candidate.cursor_y),
                        float(candidate.cursor_y) + float(candidate.advance_y),
                    )
                    for candidate in drawable_items
                )
                emphasis = plan_emphasis(
                    span,
                    intervals,
                    group_font_size,
                )
                emphasis.cross_center = (
                    ruby_extra
                    + group_font_size * RICH_TEXT_POLICY.vertical_emphasis_offset
                )
                emphasis_plans.append(emphasis)
                emphasis_cross_extent = max(
                    emphasis_cross_extent,
                    int(round(group_font_size * RICH_TEXT_POLICY.emphasis_side_space)),
                )
            item_index = group_end

        paint_top_extra = 0.0
        paint_bottom_extra = 0.0
        for item in laid:
            extent = _rich_vertical_item_paint_extent_y(item)
            if extent is None:
                continue
            paint_top_extra = max(paint_top_extra, -extent[0])
            paint_bottom_extra = max(paint_bottom_extra, extent[1] - column_height)
        for ruby_plan in ruby_plans:
            paint_top_extra = max(paint_top_extra, -ruby_plan.paint_start)
            paint_bottom_extra = max(
                paint_bottom_extra,
                ruby_plan.paint_end - column_height,
            )
        top_extra = int(math.ceil(max(0.0, paint_top_extra)))
        bottom_extra = int(math.ceil(max(0.0, paint_bottom_extra)))
        line_kerning, next_kerning = _paragraph_line_spacing_values(paragraph)
        layouts.append(VerticalColumnPlan(
            thickness=int(thickness),
            height=column_height,
            content_paint_bounds=Bounds(
                left=-int(paint_left_extra),
                top=-top_extra,
                right=int(thickness + paint_right_extra),
                bottom=int(column_height + bottom_extra),
            ),
            ruby_cross_extent=int(ruby_extra),
            annotation_cross_extent=int(ruby_extra + emphasis_cross_extent),
            items=tuple(laid),
            ruby_plans=tuple(ruby_plans),
            emphasis_plans=tuple(emphasis_plans),
            line_kerning=line_kerning,
            next_kerning=next_kerning,
        ))
    return layouts


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


def _vertical_free_rotation_advance(
    base: VerticalGlyphBase,
    rotation: float,
) -> int:
    """按旋转角度投影竖排槽位：0° 取竖排推进，±90° 取横排推进（对齐 BallonsTranslator）。"""
    angle = math.radians(float(rotation or 0.0))
    projected_height = (
        abs(max(float(base.advance_x), 1.0) * math.sin(angle))
        + abs(max(float(base.advance_y), 1.0) * math.cos(angle))
    )
    return max(int(math.ceil(round(projected_height, 6))), 1)


def _vertical_space_advance(font_size: int, letter_spacing: float = 1.0) -> int:
    width = _measure_horizontal_text_width(' ', font_size, 1.0)
    if width <= 0:
        width = max(1, int(round(font_size * 0.25)))
    return _scale_advance(width, letter_spacing)


def _vertical_base(
    font_size: int,
    cdpt: str,
    letter_spacing: float = 1.0,
    shear: float = 0.0,
    advance_mode: Optional[str] = None,
) -> VerticalGlyphBase:
    state = _state()
    key = (
        state.font_family,
        bool(state.bold),
        int(font_size),
        cdpt,
        round(_normalize_letter_spacing(letter_spacing), 4),
        round(float(shear), 4),
        advance_mode,
    )
    cached = _cache_get(state.vertical, key)
    if cached is not None:
        return cached
    forced_advance = _forced_vertical_advance(font_size, advance_mode)
    forced = forced_advance is not None
    translated, rot = CJK_Compatibility_Forms_translate(cdpt, 1)
    if translated == ' ':
        base = VerticalGlyphBase(
            translated=translated,
            rot_degree=0,
            bitmap=None,
            advance_y=(
                _scale_advance(forced_advance, letter_spacing)
                if forced_advance is not None
                else _vertical_space_advance(font_size, letter_spacing)
            ),
            ink_x=0.0,
            ink_w=0.0,
            y=0,
            advance_x=int(max(font_size, 1)),
            glyph_left=0.0,
            frame_width=int(max(font_size, 1)),
        )
        return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)

    rotated = rot == 90
    # 斜体在字形路径阶段绕基线剪切；横躺字先剪切后旋转（R·S，PS 语义）
    glyph = _glyph_raster(translated, font_size, shear)
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

    force_compact = not forced and _vertical_force_compact_slot(translated)
    if forced:
        advance_y = forced_advance
    elif translated in _VERTICAL_HALF_ADVANCE:
        advance_y = font_size * 0.5
    elif rotated:
        advance_y = _vertical_rotated_advance(glyph, font_size, bitmap)
    elif _is_vertical_ellipsis_char(translated):
        advance_y = _vertical_ellipsis_advance(glyph, font_size, bitmap)
    else:
        advance_y = glyph.advance_y if glyph.advance_y > 0 else font_size

    if force_compact and ink_h > 0:
        if translated in _VERTICAL_PUNCT_CENTER:
            metrics = QFontMetricsF(_layout_font(font_size, letter_spacing))
            advance_y = ink_h + max(0.0, float(metrics.descent()))
        else:
            advance_y = ink_h
    advance_y = _scale_advance(int(round(advance_y)), letter_spacing)

    frame_width = max(font_size, int(round(ink_w)) if ink_w else 0, 1)
    if not rotated:
        frame_width = max(frame_width, int(glyph.advance_x))

    # 强制推进只保留真实墨迹居中；墨迹可溢出槽位，但不会反向放大推进量。
    center_gap = (advance_y - ink_h) / 2.0
    y = (center_gap if forced else max(0.0, center_gap)) - ink_y

    if not forced:
        padding = max(1, int(round(font_size * 0.05)))
        if translated in _VERTICAL_ALIGN_TOP_RIGHT or translated in _VERTICAL_ALIGN_TOP_CENTER:
            y = padding - ink_y
        elif translated in _VERTICAL_ALIGN_BOTTOM_LEFT or translated in _VERTICAL_ALIGN_BOTTOM_CENTER:
            y = advance_y - ink_h - padding - ink_y
        elif force_compact:
            y = -ink_y
            if translated in _VERTICAL_PUNCT_CENTER:
                y += max(0.0, center_gap)

    base = VerticalGlyphBase(
        translated=translated,
        rot_degree=rot,
        bitmap=bitmap,
        advance_y=int(advance_y),
        ink_x=float(ink_x),
        ink_w=float(ink_w),
        y=int(round(y)),
        advance_x=int(max(glyph.advance_x, 1)),
        glyph_left=float(glyph.left),
        frame_width=int(frame_width),
    )
    return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)


def get_vertical_char_bitmap_width(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> int:
    return _vertical_base(font_size, cdpt, letter_spacing).frame_width


def _vertical_char_bitmap_x(
    frame_left: float,
    frame_width: float,
    base: VerticalGlyphBase,
    padding_size: Optional[float] = None,
    ink_center: bool = False,
) -> float:
    """返回竖排字符位图左边缘，普通直立字按 advance 居中。

    对应 Canvas 的 textAlign='center'：先把字体 advance box 的中心放到列中心，
    再加 glyph left bearing 得到位图原点。旋转字符已经在光栅层转过 90°，其
    原始 advance 轴也随之转为纵轴，因此横向仍使用旋转后位图框居中。标点的
    顶右/底左贴边规则最后覆盖默认居中；强制推进时只保留墨迹居中。
    """
    frame_left = float(frame_left)
    frame_width = float(frame_width)
    ink_w = base.ink_w
    ink_x = base.ink_x
    translated = base.translated
    if ink_center or translated in _VERTICAL_PUNCT_UP:
        # 竖排标点的 advance/side bearing 常按横排标点设计，不能用于列内居中。
        # 它们仍按实际标点墨迹居中；正文直立字继续使用 advance box。
        x = frame_left + (frame_width - ink_w) / 2.0 - ink_x
    elif base.rot_degree == 0:
        advance_x = max(float(base.advance_x), 1.0)
        x = frame_left + (frame_width - advance_x) / 2.0 + base.glyph_left
    else:
        x = frame_left + (frame_width - ink_w) / 2.0 - ink_x

    if not ink_center:
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
    key = ('logical-width', state.font_family, bool(state.bold), int(font_size), round(_normalize_letter_spacing(letter_spacing), 4), normalized)
    cached = state.measures.get(key)
    if cached is not None:
        return cached
    _, _, _, line = _horizontal_line(normalized, font_size, letter_spacing)
    width = 0 if line is None else int(round(_line_logical_width(line)))
    if len(state.measures) >= 4096:
        state.measures.clear()
    state.measures[key] = width
    return width


def calc_horizontal_block_height(font_size: int, content: str, letter_spacing: float = 1.0) -> int:
    content = _normalize_horizontal_block_content(content)
    geometry = _line_ink_geometry(content, font_size, 0.0, False, letter_spacing)
    return font_size if not geometry['has_ink'] else int(geometry['height'])
