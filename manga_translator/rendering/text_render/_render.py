"""渲染入口层：put_text_* / measure_* / calc_* 公共 API。

纯字符串在入口归一为单 span 富文本文档（_coerce_render_document），
此后横竖排各只有一条富文本编排。
"""
import re
from typing import Optional, Tuple

import cv2
import numpy as np

from ..rich_text import (
    RichTextDocument,
    ensure_rich_text_document,
    is_rich_text_document,
    legacy_line_breaks_to_document,
)
from ._compose import (
    _apply_style_layer_effects,
    _crop_rgba_fixed,
    _draw_rgba_disc,
    _glyph_pair_rgba,
    _paste_rgba,
    _rgba_from_alpha_pair,
    _style_fill_color,
    _style_font_size,
    _style_stroke_color,
    _stroke_bitmap_from_alpha,
)
from ._fonts import _state, _style_font_scope
from ._layout import (
    _build_rich_horizontal_layout,
    _build_rich_vertical_layout,
    _measure_horizontal_text_width,
    _line_surface,
    _rich_horizontal_layout_geometry,
    _rich_vertical_tcy_layer_x,
    _rich_vertical_char_layer_x,
    _rich_vertical_column_positions,
    _rich_vertical_layout_geometry,
    _vertical_base,
    _vertical_line_origin_y,
    calc_horizontal_block_height,
)
from ._plans import RubyPlan, TcyPlan
from ._policy import RICH_TEXT_POLICY
from ._vertical_types import VerticalCharPlan, VerticalPlaceholderPlan


def _paint_vertical_ruby(canvas: np.ndarray, plan: RubyPlan, body_right: float, main_offset: float, fg, letter_spacing: float):
    if not plan.glyphs:
        return
    ruby_size = max(1, int(round(plan.font_size * RICH_TEXT_POLICY.vertical_ruby_size)))
    ruby_style = plan.source.style.copy()
    ruby_style.emphasis = False
    fill = _style_fill_color(ruby_style, fg)
    stroke = _style_stroke_color(ruby_style, None)
    x = body_right + plan.cross_center
    with _style_font_scope(ruby_style):
        for glyph in plan.glyphs:
            base = _vertical_base(ruby_size, glyph.char, letter_spacing)
            if base.bitmap is None:
                continue
            layer, off_x, off_y = _glyph_pair_rgba(base.bitmap, None, fill, stroke)
            if layer is None:
                continue
            if glyph.main_scale != 1.0:
                scaled_h = max(1, int(round(layer.shape[0] * glyph.main_scale)))
                layer = cv2.resize(layer, (layer.shape[1], scaled_h), interpolation=cv2.INTER_LINEAR)
                off_y *= glyph.main_scale
            cy = main_offset + glyph.main_center
            _paste_rgba(
                canvas,
                layer,
                int(round(x - layer.shape[1] / 2.0 + off_x)),
                int(round(cy - layer.shape[0] / 2.0 + off_y)),
            )


def _rasterize_text_layer(
    text: str,
    style,
    font_size: int,
    stroke_ratio: float,
    reversed_direction: bool,
    fg,
    bg,
    letter_spacing: float,
    profile_stats: Optional[dict],
):
    """Rasterize one horizontal text primitive from an existing layout plan."""
    border_size = int(max(font_size * stroke_ratio, 1)) if stroke_ratio > 0 else 0
    effective_bold = bool(style.bold) or _state().bold
    with _style_font_scope(style):
        surface = _line_surface(
            text,
            font_size,
            border_size,
            stroke_ratio,
            reversed_direction,
            letter_spacing,
            effective_bold,
            profile_stats,
        )
    if surface is None:
        return None
    fill = _style_fill_color(style, fg)
    stroke = _style_stroke_color(style, bg) if stroke_ratio > 0 else None
    layer = _rgba_from_alpha_pair(surface['text'], surface['border'], fill, stroke)
    if layer is None:
        return None
    layer, _, _ = _apply_style_layer_effects(layer, style, font_size)
    return layer


def _rasterize_vertical_char(plan: VerticalCharPlan):
    bitmap = plan.base.bitmap
    if bitmap is None or not bitmap.size:
        return None
    stroke_bitmap = (
        _stroke_bitmap_from_alpha(bitmap, plan.font_size, plan.stroke_ratio)
        if plan.stroke_ratio > 0
        else None
    )
    layer, _, _ = _glyph_pair_rgba(bitmap, stroke_bitmap, plan.fill, plan.stroke)
    if layer is None:
        return None
    layer, _, _ = _apply_style_layer_effects(layer, plan.span.style, plan.font_size)
    return layer

def _render_rich_text_horizontal(
    font_size: int,
    text: str,
    width: int,
    height: int,
    alignment: str,
    reversed_direction: bool,
    fg,
    bg,
    line_spacing: float,
    config=None,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
):
    document = ensure_rich_text_document(text)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    _ = (width, height)  # 包络由内容决定，外部最小尺寸不再参与画布
    layouts = _build_rich_horizontal_layout(
        document, font_size, stroke_ratio, bg, reversed_direction, letter_spacing, profile_stats
    )
    geometry = _rich_horizontal_layout_geometry(layouts, font_size, line_spacing)
    max_font_size = max(
        [font_size] + [run.font_size for layout in layouts for run in layout.runs]
    )

    padding = int(max(max_font_size * 2.0, 16))
    canvas_w = geometry['paint_width'] + padding * 2
    canvas_h = geometry['paint_height'] + padding * 2
    canvas = np.zeros((max(canvas_h, 1), max(canvas_w, 1), 4), dtype=np.uint8)
    body_left = padding + geometry['left_extra']
    body_width = geometry['body_width']

    for layout, normalized_baseline in zip(layouts, geometry['baselines']):
        line_width = layout.logical_width
        if reversed_direction:
            line_left = body_left + body_width - line_width if alignment == 'right' else body_left
            if alignment == 'center':
                line_left = body_left + (body_width - line_width) / 2.0
        else:
            line_left = body_left if alignment == 'left' else body_left + (body_width - line_width) / 2.0 if alignment == 'center' else body_left + body_width - line_width
        baseline_y = float(padding) + float(normalized_baseline)
        cursor_x = line_left
        for run in layout.runs:
            span = run.span
            if run.has_ink:
                layer = _rasterize_text_layer(
                    span.text,
                    span.style,
                    run.font_size,
                    run.stroke_ratio,
                    reversed_direction,
                    fg,
                    bg,
                    letter_spacing,
                    profile_stats,
                )
                if layer is not None:
                    main_rect = run.main_rect
                    if main_rect is not None:
                        _paste_rgba(
                            canvas,
                            layer,
                            int(round(cursor_x + main_rect.x)),
                            int(round(baseline_y + main_rect.y)),
                        )
            ruby = run.ruby
            if ruby is not None:
                ruby_style = span.style.copy()
                ruby_style.emphasis = False
                for glyph in ruby.glyphs:
                    layer = _rasterize_text_layer(
                        glyph.char,
                        ruby_style,
                        ruby.font_size,
                        ruby.stroke_ratio,
                        False,
                        fg,
                        bg,
                        letter_spacing,
                        profile_stats,
                    )
                    if layer is None:
                        continue
                    _paste_rgba(
                        canvas,
                        layer,
                        int(round(cursor_x + glyph.main_center - glyph.paint_width / 2.0)),
                        int(round(baseline_y + ruby.cross_center - glyph.paint_height / 2.0)),
                    )
            emphasis = run.emphasis
            if emphasis is not None:
                dot_color = _style_fill_color(emphasis.source.style, fg)
                for main_center in emphasis.main_centers:
                    _draw_rgba_disc(
                        canvas,
                        cursor_x + main_center,
                        baseline_y + emphasis.cross_center,
                        emphasis.radius,
                        dot_color,
                    )
            cursor_x += run.logical_width

    return _crop_rgba_fixed(
        canvas,
        padding,
        padding + geometry['paint_width'],
        padding,
        padding + geometry['paint_height'],
    )


def _render_rich_text_vertical(
    font_size: int,
    text: str,
    h: int,
    alignment: str,
    fg,
    bg,
    line_spacing: float,
    config=None,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
):
    document = ensure_rich_text_document(text)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    _ = h  # 包络由内容决定，外部最小高度不再参与画布
    layouts = _build_rich_vertical_layout(document, font_size, stroke_ratio, fg, bg, letter_spacing, profile_stats)
    geometry = _rich_vertical_layout_geometry(layouts, font_size, line_spacing)
    body_height = geometry['body_height']
    padding = int(max(font_size * 2.0, 16))
    canvas = np.zeros(
        (int(max(geometry['paint_height'], 1)) + padding * 2, int(max(geometry['paint_width'], 1)) + padding * 2, 4),
        dtype=np.uint8,
    )
    content_left = padding
    content_right = padding + geometry['paint_width']
    content_top = padding
    content_bottom = padding + geometry['paint_height']
    columns = _rich_vertical_column_positions(layouts, geometry, padding)

    for idx, layout in enumerate(layouts):
        body_left, body_right, _ = columns[idx]
        thickness = float(layout.thickness)
        line_origin_y = _vertical_line_origin_y(
            float(padding + geometry['top_extra']),
            alignment,
            body_height,
            layout.height,
        )
        for item in layout.items:
            if isinstance(item, TcyPlan):
                layer = _rasterize_text_layer(
                    item.text,
                    item.source.style,
                    item.font_size,
                    item.stroke_ratio,
                    False,
                    fg,
                    bg,
                    letter_spacing,
                    profile_stats,
                )
                if layer is None:
                    continue
                x = _rich_vertical_tcy_layer_x(body_left, thickness, item)
                y = (
                    line_origin_y
                    + item.main_start
                    + item.source.style.transform.offset_y
                    + item.paint_offset_y
                )
                _paste_rgba(canvas, layer, int(round(x)), int(round(y)))
                continue
            if isinstance(item, VerticalPlaceholderPlan):
                continue
            if not isinstance(item, VerticalCharPlan):
                continue
            layer = _rasterize_vertical_char(item)
            if layer is None:
                continue
            char_x = _rich_vertical_char_layer_x(body_left, thickness, item)
            char_y = (
                line_origin_y
                + item.cursor_y
                + item.base.y
                + item.span.style.transform.offset_y
                + item.paint_offset_y
            )
            _paste_rgba(canvas, layer, int(round(char_x)), int(round(char_y)))
        for emphasis in layout.emphasis_plans:
            dot_color = _style_fill_color(emphasis.source.style, fg)
            for main_center in emphasis.main_centers:
                _draw_rgba_disc(
                    canvas,
                    body_right + emphasis.cross_center,
                    line_origin_y + main_center,
                    emphasis.radius,
                    dot_color,
                )
        for ruby_plan in layout.ruby_plans:
            _paint_vertical_ruby(
                canvas,
                ruby_plan,
                body_right,
                line_origin_y,
                fg,
                letter_spacing,
            )

    return _crop_rgba_fixed(canvas, content_left, content_right, content_top, content_bottom)


def measure_rich_text_metrics(
    font_size: int,
    text,
    is_horizontal: bool,
    line_spacing: float,
    config=None,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
) -> dict:
    """测量 richtext.v1 文档，返回渲染框尺寸与正文中心点。

    纯字符串输入在此归一为单样式文档（BR/换行 → 段落），与 put_text_* 的
    归一口径一致：测量框 == 绘制输出面尺寸对所有文本成立。
    横排正文框是实际主文字墨迹（含主文字 transform/描边，不含 ruby 与
    emphasis）的联合包络；竖排沿用列正文定义。body_center 是正文框中心在
    渲染框内的坐标。横排测量、全局描边和绘制输出面消费同一个墨迹计划。
    """
    document = _coerce_render_document(text)
    base_font = max(1, int(font_size))
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    if is_horizontal:
        # Horizontal ink plans include the global stroke directly.  A dummy
        # color is sufficient because only stroke presence affects geometry.
        measure_bg = (0, 0, 0) if stroke_ratio > 0 else None
        layouts = _build_rich_horizontal_layout(
            document, base_font, stroke_ratio, measure_bg,
            False, letter_spacing,
        )
        if not layouts:
            return {'width': 0, 'height': 0, 'n_lines': 0, 'body_center': (0.0, 0.0)}
        geometry = _rich_horizontal_layout_geometry(layouts, base_font, line_spacing)
        return {
            'width': int(geometry['paint_width']),
            'height': int(geometry['paint_height']),
            'n_lines': max(1, len(layouts)),
            'body_center': geometry['body_center'],
        }

    # Vertical layout retains its existing outer-padding contract.
    measure_bg = None
    layouts = _build_rich_vertical_layout(
        document, base_font, stroke_ratio, (0, 0, 0), measure_bg, letter_spacing
    )
    if not layouts:
        return {'width': 0, 'height': 0, 'n_lines': 0, 'body_center': (0.0, 0.0)}
    geometry = _rich_vertical_layout_geometry(layouts, base_font, line_spacing)
    return {
        'width': int(geometry['paint_width']),
        'height': int(geometry['paint_height']),
        'n_lines': len(layouts),
        'body_center': (float(geometry['body_center_x']), float(geometry['body_center_y'])),
    }


def measure_rich_text_horizontal(
    font_size: int,
    text,
    line_spacing: float,
    config=None,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
) -> Tuple[int, int, int]:
    metrics = measure_rich_text_metrics(
        font_size, text, True, line_spacing,
        config=config, stroke_width=stroke_width, letter_spacing=letter_spacing,
    )
    return metrics['width'], metrics['height'], metrics['n_lines']


def measure_rich_text_vertical(
    font_size: int,
    text,
    line_spacing: float,
    config=None,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
) -> Tuple[int, int, int]:
    metrics = measure_rich_text_metrics(
        font_size, text, False, line_spacing,
        config=config, stroke_width=stroke_width, letter_spacing=letter_spacing,
    )
    return metrics['width'], metrics['height'], metrics['n_lines']


def _resolve_stroke_ratio(config=None, stroke_width: Optional[float] = None) -> float:
    render_cfg = getattr(config, 'render', None)
    if bool(getattr(render_cfg, 'disable_font_border', False)):
        return 0.0
    if stroke_width is not None:
        return float(stroke_width)
    return float(getattr(render_cfg, 'stroke_width', 0.07))


def get_char_offset_x(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _measure_horizontal_text_width('　' if cdpt == '＿' else cdpt, font_size, letter_spacing)


def get_string_width(font_size: int, text: str, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        max_width = 0
        for paragraph in document.paragraphs:
            width = 0
            for span in paragraph.spans:
                with _style_font_scope(span.style):
                    width += _measure_horizontal_text_width(span.text, _style_font_size(font_size, span.style), letter_spacing)
            max_width = max(max_width, width)
        return max_width
    return _measure_horizontal_text_width(text, font_size, letter_spacing)


def get_char_offset_y(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _vertical_base(font_size, '　' if cdpt == '＿' else cdpt, letter_spacing).advance_y


def get_string_height(font_size: int, text: str, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        max_height = 0
        for paragraph in document.paragraphs:
            max_height = max(max_height, _rich_paragraph_vertical_metrics(font_size, paragraph, letter_spacing)[0])
        return max_height
    text = text or ''
    total = 0
    for char in re.sub(r'\s*(?:\[BR\]|<br>|【BR】)\s*', '', text, flags=re.IGNORECASE):
        total += get_char_offset_y(font_size, char, letter_spacing)
    return total


def _rich_paragraph_horizontal_width(font_size: int, paragraph, letter_spacing: float = 1.0) -> int:
    width = 0
    for span in paragraph.spans:
        with _style_font_scope(span.style):
            width += _measure_horizontal_text_width(span.text, _style_font_size(font_size, span.style), letter_spacing)
    return width


def _rich_paragraph_vertical_metrics(font_size: int, paragraph, letter_spacing: float = 1.0) -> Tuple[int, int]:
    line_height = 0
    line_width = font_size
    for span in paragraph.spans:
        span_font_size = _style_font_size(font_size, span.style)
        if span.tcy:
            with _style_font_scope(span.style):
                line_height += calc_horizontal_block_height(span_font_size, span.text, letter_spacing=letter_spacing)
        else:
            with _style_font_scope(span.style):
                line_height += sum(get_char_offset_y(span_font_size, c, letter_spacing) for c in span.text)
    return int(line_height), int(line_width)


def _coerce_render_document(text) -> RichTextDocument:
    """渲染/测量入口的统一归一：纯字符串转单样式 richtext 文档。

    纯文本 == "单 span、默认样式"的富文本特例：BR/换行拆成多段落，
    每段一个默认样式 TextRun。归一后横竖排只剩富文本一套编排，
    输出面尺寸恒等于测量框（测/渲同源契约对纯文本同样成立）。
    """
    if is_rich_text_document(text):
        # F24：入口解析一次，向下传实例（内部 ensure 对实例是短路）
        return ensure_rich_text_document(text)
    return legacy_line_breaks_to_document(text or '')


def put_text_horizontal(
    font_size: int,
    text: str,
    width: int,
    height: int,
    alignment: str,
    reversed_direction: bool,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
    lang: str = 'en_US',
    hyphenate: bool = True,
    line_spacing: int = 0,
    config=None,
    region_count: int = 1,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
):
    _ = (width, height, lang, hyphenate, region_count)
    document = _coerce_render_document(text)
    if not document.paragraphs:
        return None
    return _render_rich_text_horizontal(
        font_size,
        document,
        width,
        height,
        alignment,
        reversed_direction,
        fg,
        bg,
        line_spacing,
        config,
        stroke_width,
        letter_spacing,
        profile_stats,
    )


def put_text_vertical(
    font_size: int,
    text: str,
    h: int,
    alignment: str,
    fg: Tuple[int, int, int],
    bg: Optional[Tuple[int, int, int]],
    line_spacing: int,
    config=None,
    region_count: int = 1,
    stroke_width: float = None,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
):
    _ = (h, region_count)
    document = _coerce_render_document(text)
    if not document.paragraphs:
        return None
    return _render_rich_text_vertical(
        font_size,
        document,
        h,
        alignment,
        fg,
        bg,
        line_spacing,
        config,
        stroke_width,
        letter_spacing,
        profile_stats,
    )


def calc_horizontal(font_size: int, text: str, max_width: int, max_height: int, language: str = 'en_US', hyphenate: bool = True, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        return document.paragraphs, [
            _rich_paragraph_horizontal_width(font_size, paragraph, letter_spacing=letter_spacing)
            for paragraph in document.paragraphs
        ]
    from ..auto_linebreak import _calc_horizontal_layout
    _ = max_height
    return _calc_horizontal_layout(font_size, text, max_width, language, hyphenate, letter_spacing=letter_spacing)


def calc_vertical(font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        metrics = [
            _rich_paragraph_vertical_metrics(font_size, paragraph, letter_spacing=letter_spacing)
            for paragraph in document.paragraphs
        ]
        return document.paragraphs, [height for height, _ in metrics]
    from ..auto_linebreak import _calc_vertical_layout
    return _calc_vertical_layout(font_size, text, max_height, config, letter_spacing=letter_spacing)


def calc_vertical_metrics(font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        metrics = [
            _rich_paragraph_vertical_metrics(font_size, paragraph, letter_spacing=letter_spacing)
            for paragraph in document.paragraphs
        ]
        return document.paragraphs, [height for height, _ in metrics], [width for _, width in metrics]
    from ..auto_linebreak import _layout_vertical_metrics
    return _layout_vertical_metrics(font_size, text, max_height, config, letter_spacing=letter_spacing)
