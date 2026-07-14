"""渲染入口层：put_text_* / measure_* / calc_* 公共 API。

纯字符串在入口归一为单 span 富文本文档（_coerce_render_document），
此后横竖排各只有一条富文本编排。
"""
import re
from typing import Optional, Tuple

import numpy as np

from ..rich_text import (
    RichTextDocument,
    ensure_rich_text_document,
    is_rich_text_document,
    legacy_line_breaks_to_document,
)
from ._compose import _crop_rgba_fixed, _draw_rgba_disc, _paste_rgba, _style_fill_color, _style_font_size
from ._fonts import _style_font_scope
from ._layout import (
    _build_rich_horizontal_layout,
    _build_rich_vertical_layout,
    _draw_vertical_ruby,
    _measure_horizontal_text_width,
    _rich_colorized_surface,
    _rich_horizontal_layout_geometry,
    _rich_vertical_block_layer_x,
    _rich_vertical_char_layer_x,
    _rich_vertical_column_positions,
    _rich_vertical_layout_geometry,
    _vertical_base,
    _vertical_line_origin_y,
    calc_horizontal_block_height,
)

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
        document, font_size, stroke_ratio, fg, bg, reversed_direction, letter_spacing, profile_stats
    )
    geometry = _rich_horizontal_layout_geometry(layouts, font_size, line_spacing)
    spacing_y = geometry['spacing_y']
    max_font_size = max(
        [font_size] + [run['font_size'] for layout in layouts for run in layout['runs']]
    )

    padding = int(max(max_font_size * 2.0, 16))
    canvas_w = geometry['paint_width'] + padding * 2
    canvas_h = geometry['paint_height'] + padding * 2
    canvas = np.zeros((max(canvas_h, 1), max(canvas_w, 1), 4), dtype=np.uint8)
    body_left = padding + geometry['left_extra']
    body_width = geometry['body_width']
    y = float(padding) + geometry['top_extra']

    for layout in layouts:
        line_width = layout['logical_width']
        if reversed_direction:
            line_left = body_left + body_width - line_width if alignment == 'right' else body_left
            if alignment == 'center':
                line_left = body_left + (body_width - line_width) / 2.0
        else:
            line_left = body_left if alignment == 'left' else body_left + (body_width - line_width) / 2.0 if alignment == 'center' else body_left + body_width - line_width
        baseline_y = y + layout['ruby_extra'] + layout['ascent']
        cursor_x = line_left
        for run in layout['runs']:
            surface = run['surface']
            span = run['span']
            if surface is None:
                # 空白 spacer：只推进游标（宽度已计入行宽与包络）
                cursor_x += run['logical_width']
                continue
            layer, layer_dx, layer_dy = _rich_colorized_surface(run, fg, bg)
            if layer is not None:
                draw_x = cursor_x + surface['left_rel'] + span.style.transform.offset_x + layer_dx
                if reversed_direction:
                    # reversed 的 _line_surface 以"行右端"为原点相对化墨迹
                    # （origin_x = -logical_width），left_rel 因此带 +行宽偏移；
                    # 摆放按 run 左边缘游标，需要减回一个行宽（对齐旧纯文本
                    # 编排的 pen_x 补偿公式）。
                    draw_x -= run['logical_width']
                draw_y = baseline_y + surface['top_rel'] + span.style.transform.offset_y + layer_dy
                _paste_rgba(canvas, layer, int(round(draw_x)), int(round(draw_y)))
            ruby = run.get('ruby')
            if ruby:
                gap = max(1, int(round(run['font_size'] * 0.08)))
                ruby_x = cursor_x + run['logical_width'] / 2.0 - ruby['layer'].shape[1] / 2.0
                main_top = baseline_y - run['ascent']
                ruby_y = main_top - gap - ruby['layer'].shape[0]
                # 注音不越出本行预留区顶（包络按度量式 ruby_extra 计算）
                ruby_y = max(ruby_y, y)
                _paste_rgba(
                    canvas,
                    ruby['layer'],
                    int(round(ruby_x + ruby.get('offset_x', 0.0))),
                    int(round(ruby_y + ruby.get('offset_y', 0.0))),
                )
            if span.style.emphasis:
                dot_color = _style_fill_color(span.style, fg)
                char_cursor = cursor_x
                for char in span.text:
                    advance = get_char_offset_x(run['font_size'], char, letter_spacing)
                    radius = max(1.0, run['font_size'] * 0.055)
                    _draw_rgba_disc(
                        canvas,
                        char_cursor + advance / 2.0,
                        baseline_y + layout['descent'] + radius * 2.0,
                        radius,
                        dot_color,
                    )
                    char_cursor += advance
            cursor_x += run['logical_width']
        y += layout['height'] + spacing_y

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
        thickness = float(layout['thickness'])
        line_origin_y = _vertical_line_origin_y(float(padding + geometry['top_extra']), alignment, body_height, layout['height'])
        for item in layout['items']:
            if item['kind'] == 'block':
                x = _rich_vertical_block_layer_x(body_left, thickness, item)
                y = line_origin_y + item['cursor_y']
                y += item['span'].style.transform.offset_y + item.get('offset_y', 0.0)
                _paste_rgba(canvas, item['layer'], int(round(x)), int(round(y)))
                continue
            if item['kind'] == 'placeholder':
                continue
            layer = item.get('layer')
            if layer is None:
                continue
            char_x = _rich_vertical_char_layer_x(body_left, thickness, item)
            char_y = (
                line_origin_y
                + item['cursor_y']
                + int(item['base']['y'])
                + item['span'].style.transform.offset_y
                + float(item.get('paint_offset_y', 0.0))
            )
            _paste_rgba(canvas, layer, int(round(char_x)), int(round(char_y)))
            if item['span'].style.emphasis:
                radius = max(1.0, item['font_size'] * 0.055)
                _draw_rgba_disc(
                    canvas,
                    body_right + layout['ruby_extra'] + item['font_size'] * 0.20,
                    line_origin_y + item['cursor_y'] + item['advance_y'] / 2.0,
                    radius,
                    item['fill'],
                )
            if item['span'].ruby:
                ruby_x = body_right + max(1.0, layout['ruby_extra'] / 2.0)
                _draw_vertical_ruby(
                    canvas,
                    ''.join(run.text for run in item['span'].ruby),
                    ruby_x,
                    line_origin_y + item['cursor_y'],
                    line_origin_y + item['cursor_y'] + item['advance_y'],
                    item['font_size'],
                    item['span'].style,
                    fg,
                    bg,
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
    正文框 = 渲染框去掉框外装饰（横排的首行注音/末行着重号、竖排的首列
    注音与字形左右溢出）后，纯文本本体占据的区域。body_center 是正文框
    中心在渲染框内的坐标（相对渲染框左上角）；无框外装饰时恒为渲染框正中心。
    """
    document = _coerce_render_document(text)
    base_font = max(1, int(font_size))
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    # 度量沿用旧口径：全局描边不进包络（由 calc_box_from_font 的四边对称
    # effect padding 覆盖），带描边的绘制输出面比框大出描边外扩属既有行为。
    # span 局部描边仍按其比例进包络（bg=None 时 _style_stroke_ratio 只认局部）。
    measure_bg = None

    if is_horizontal:
        # 与绘制共用同一 builder/geometry（F21 口径）：行度量、包络 extras、
        # 正文中心全部同源，测量框 == 绘制输出面。
        layouts = _build_rich_horizontal_layout(
            document, base_font, stroke_ratio, (0, 0, 0), measure_bg,
            False, letter_spacing, measure_only=True,
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

    layouts = _build_rich_vertical_layout(
        document, base_font, stroke_ratio, (0, 0, 0), measure_bg, letter_spacing, measure_only=True
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
    if stroke_width is not None:
        return float(stroke_width)
    render_cfg = getattr(config, 'render', None)
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
    return _vertical_base(font_size, '　' if cdpt == '＿' else cdpt, letter_spacing)['advance_y']


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
