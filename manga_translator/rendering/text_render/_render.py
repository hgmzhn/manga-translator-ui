"""渲染入口层：put_text_* / measure_* / calc_* 公共 API。

纯字符串在入口归一为单 span 富文本文档（_coerce_render_document），
此后横竖排各只有一条富文本编排。
"""

import math
import re

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
    _apply_style_paint_effects,
    _crop_rgba_fixed,
    _draw_rgba_bar,
    _draw_rgba_disc,
    _glyph_pair_rgba,
    _paste_rgba,
    _rgba_for_paint_part,
    _stroke_bitmap_from_alpha,
    _style_fill_color,
    _style_font_size,
    _style_italic_shear,
    _style_stroke_color,
)
from ._fonts import _state, _style_font_scope
from ._layout import (
    _build_rich_horizontal_layout,
    _build_rich_vertical_layout,
    _line_surface,
    _measure_horizontal_text_width,
    _rich_horizontal_layout_geometry,
    _rich_vertical_char_layer_x,
    _rich_vertical_column_positions,
    _rich_vertical_layout_geometry,
    _rich_vertical_tcy_layer_x,
    _vertical_base,
    _vertical_line_origin_y,
    calc_horizontal_block_height,
)
from ._plans import RubyPlan, TcyPlan
from ._policy import RICH_TEXT_POLICY
from ._vertical_types import VerticalCharPlan, VerticalPlaceholderPlan


def _paint_vertical_ruby(
    canvas: np.ndarray,
    plan: RubyPlan,
    body_right: float,
    main_offset: float,
    fg,
    letter_spacing: float,
):
    if not plan.glyphs:
        return
    ruby_size = max(1, round(plan.font_size * RICH_TEXT_POLICY.vertical_ruby_size))
    ruby_style = plan.source.style.copy()
    ruby_style.emphasis = False
    ruby_style.underline = False
    ruby_style.strikethrough = False
    fill = _style_fill_color(ruby_style, fg)
    stroke = _style_stroke_color(ruby_style, None)
    x = body_right + plan.cross_center
    ruby_shear = _style_italic_shear(ruby_style)
    with _style_font_scope(ruby_style):
        for glyph in plan.glyphs:
            base = _vertical_base(
                ruby_size,
                glyph.char,
                letter_spacing,
                ruby_shear,
                scale_x=ruby_style.transform.scale_x,
                scale_y=ruby_style.transform.scale_y,
            )
            if base.bitmap is None:
                continue
            layer, off_x, off_y = _glyph_pair_rgba(base.bitmap, None, fill, stroke)
            if layer is None:
                continue
            if glyph.main_scale != 1.0:
                scaled_h = max(1, round(layer.shape[0] * glyph.main_scale))
                layer = cv2.resize(
                    layer, (layer.shape[1], scaled_h), interpolation=cv2.INTER_LINEAR
                )
                off_y *= glyph.main_scale
            cy = main_offset + glyph.main_center
            _paste_rgba(
                canvas,
                layer,
                round(x - layer.shape[1] / 2.0 + off_x),
                round(cy - layer.shape[0] / 2.0 + off_y),
            )


def _finish_layer(base_layer, style, font_size: int, effect_part: str):
    """特效 + 图层几何（旋转/镜像）后处理，effects/stroke/fill 三层共用。

    斜体已在字形路径阶段完成（_glyph_raster/_horizontal_glyph_path 的 shear），
    进入本函数的图层就是剪切后的形状。
    """
    if base_layer is None:
        return None
    layer = _apply_style_paint_effects(base_layer, style, font_size, effect_part)
    if layer is None:
        return None
    layer, _, _ = _apply_style_layer_effects(layer, style, font_size)
    return layer


def _scale_parts_x(parts, scale_x: float):
    """纵中横整组水平压缩：对 (effects, stroke, fill) 三层各按同一系数 resize。

    每层宽度不同（effects 含发光/外描边 pad），但压缩系数相同，与旧逐
    paint_part 路径逐层 resize 的结果逐像素一致。
    """
    if parts is None or scale_x >= 1.0:
        return parts
    scaled = []
    for layer in parts:
        if layer is None:
            scaled.append(None)
            continue
        scaled_w = max(1, math.ceil(layer.shape[1] * scale_x))
        scaled.append(
            cv2.resize(layer, (scaled_w, layer.shape[0]), interpolation=cv2.INTER_AREA)
        )
    return tuple(scaled)


def _text_layer_parts(
    text: str,
    style,
    font_size: int,
    stroke_ratio: float,
    reversed_direction: bool,
    fg,
    bg,
    letter_spacing: float,
    profile_stats: dict | None,
    geometry: dict | None = None,
):
    """一次光栅化，派生横排文字的 (effects, stroke, fill) 三层，位置对齐。

    昂贵的 shaping/描边距离变换只做一次（旧路径按 paint_part 重复三遍）；
    三层随后各自走相同的特效 + 图层几何后处理，输出与旧路径逐像素一致。
    无墨迹返回 None。
    """
    border_size = max(round(font_size * stroke_ratio), 1) if stroke_ratio > 0 else 0
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
            geometry,
            _style_italic_shear(style),
            style.transform.scale_x,
            style.transform.scale_y,
        )
    if surface is None:
        return None
    fill = _style_fill_color(style, fg)
    stroke = _style_stroke_color(style, bg) if stroke_ratio > 0 else None
    text_alpha, border_alpha = surface["text"], surface["border"]
    return (
        _finish_layer(
            _rgba_for_paint_part(text_alpha, border_alpha, fill, stroke, "all"),
            style,
            font_size,
            "effects",
        ),
        _finish_layer(
            _rgba_for_paint_part(text_alpha, border_alpha, fill, stroke, "stroke"),
            style,
            font_size,
            "body",
        ),
        _finish_layer(
            _rgba_for_paint_part(text_alpha, border_alpha, fill, stroke, "fill"),
            style,
            font_size,
            "body",
        ),
    )


def _vertical_char_parts(plan: VerticalCharPlan):
    """一次描边距离变换，派生竖排字符的 (effects, stroke, fill) 三层。"""
    bitmap = plan.base.bitmap
    if bitmap is None or not bitmap.size:
        return None
    stroke_bitmap = (
        _stroke_bitmap_from_alpha(bitmap, plan.font_size, plan.stroke_ratio)
        if plan.stroke_ratio > 0
        else None
    )
    style, font_size = plan.span.style, plan.font_size
    combined, _, _ = _glyph_pair_rgba(
        bitmap, stroke_bitmap, plan.fill, plan.stroke, "all"
    )
    stroke_base, _, _ = _glyph_pair_rgba(
        bitmap, stroke_bitmap, plan.fill, plan.stroke, "stroke"
    )
    fill_base, _, _ = _glyph_pair_rgba(
        bitmap, stroke_bitmap, plan.fill, plan.stroke, "fill"
    )
    return (
        _finish_layer(combined, style, font_size, "effects"),
        _finish_layer(stroke_base, style, font_size, "body"),
        _finish_layer(fill_base, style, font_size, "body"),
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
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
    profile_stats: dict | None = None,
):
    document = ensure_rich_text_document(text)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    _ = (width, height)  # 包络由内容决定，外部最小尺寸不再参与画布
    # Layout and paint belong to one call.  Keep the handoff local instead of
    # retaining glyph data across font/size/style changes.
    glyph_geometries = {}
    layouts = _build_rich_horizontal_layout(
        document,
        font_size,
        stroke_ratio,
        bg,
        reversed_direction,
        letter_spacing,
        profile_stats,
        glyph_geometries,
    )
    geometry = _rich_horizontal_layout_geometry(layouts, font_size, line_spacing)
    max_font_size = max(
        [font_size] + [run.font_size for layout in layouts for run in layout.runs]
    )

    padding = int(max(max_font_size * 2.0, 16))
    canvas_w = geometry["paint_width"] + padding * 2
    canvas_h = geometry["paint_height"] + padding * 2
    canvas = np.zeros((max(canvas_h, 1), max(canvas_w, 1), 4), dtype=np.uint8)
    body_left = padding + geometry["left_extra"]
    body_width = geometry["body_width"]

    # 一次遍历构建绘制项（每个字形只光栅化一次，派生 effects/stroke/fill
    # 三层），再按全局 effects → stroke → fill 顺序三次粘贴；emphasis 圆点
    # 与文字装饰属 fill 层，按其在遍历序中的位置插入，逐像素等价于旧的三遍
    # 光栅化。
    glyph_items = []  # (parts, x, y)
    fill_extras = []  # (index_in_glyph_items, 'disc'|'bar', args)
    for layout, normalized_baseline in zip(layouts, geometry["baselines"]):
        line_width = layout.logical_width
        if reversed_direction:
            line_left = (
                body_left + body_width - line_width
                if alignment == "right"
                else body_left
            )
            if alignment == "center":
                line_left = body_left + (body_width - line_width) / 2.0
        else:
            line_left = (
                body_left
                if alignment == "left"
                else body_left + (body_width - line_width) / 2.0
                if alignment == "center"
                else body_left + body_width - line_width
            )
        baseline_y = float(padding) + float(normalized_baseline)
        cursor_x = line_left
        for run in layout.runs:
            span = run.span
            if run.has_ink and run.main_rect is not None:
                parts = _text_layer_parts(
                    span.text,
                    span.style,
                    run.font_size,
                    run.stroke_ratio,
                    reversed_direction,
                    fg,
                    bg,
                    letter_spacing,
                    profile_stats,
                    glyph_geometries.get(id(run)),
                )
                if parts is not None:
                    glyph_items.append(
                        (
                            parts,
                            round(cursor_x + run.main_rect.x),
                            round(baseline_y + run.main_rect.y),
                        )
                    )
            ruby = run.ruby
            if ruby is not None:
                ruby_style = span.style.copy()
                ruby_style.emphasis = False
                ruby_style.underline = False
                ruby_style.strikethrough = False
                for glyph in ruby.glyphs:
                    parts = _text_layer_parts(
                        glyph.char,
                        ruby_style,
                        ruby.font_size,
                        ruby.stroke_ratio,
                        False,
                        fg,
                        bg,
                        letter_spacing,
                        profile_stats,
                        glyph_geometries.get(id(glyph)),
                    )
                    if parts is not None:
                        glyph_items.append(
                            (
                                parts,
                                round(
                                    cursor_x
                                    + glyph.main_center
                                    - glyph.paint_width / 2.0
                                ),
                                round(
                                    baseline_y
                                    + ruby.cross_center
                                    - glyph.paint_height / 2.0
                                ),
                            )
                        )
            if run.emphasis is not None:
                dot_color = _style_fill_color(run.emphasis.source.style, fg)
                for main_center in run.emphasis.main_centers:
                    fill_extras.append(
                        (
                            len(glyph_items),
                            "disc",
                            (
                                cursor_x + main_center,
                                baseline_y + run.emphasis.cross_center,
                                run.emphasis.radius,
                                dot_color,
                            ),
                        )
                    )
            if run.underline is not None:
                fill_extras.append(
                    (
                        len(glyph_items),
                        "bar",
                        (
                            cursor_x + run.underline.main_start,
                            cursor_x + run.underline.main_end,
                            baseline_y + run.underline.cross_center,
                            run.underline.thickness,
                            _style_fill_color(run.underline.source.style, fg),
                        ),
                    )
                )
            if run.strikethrough is not None:
                fill_extras.append(
                    (
                        len(glyph_items),
                        "bar",
                        (
                            cursor_x + run.strikethrough.main_start,
                            cursor_x + run.strikethrough.main_end,
                            baseline_y + run.strikethrough.cross_center,
                            run.strikethrough.thickness,
                            _style_fill_color(run.strikethrough.source.style, fg),
                        ),
                    )
                )
            cursor_x += run.logical_width

    def _run_fill_extra(kind, args):
        if kind == "disc":
            cx, cy, radius, color = args
            _draw_rgba_disc(canvas, cx, cy, radius, color)
        else:
            # 文字装饰：主轴 [x0, x1) × 交叉轴以 center_y 为中心的实心横条
            x0, x1, center_y, thickness, color = args
            _draw_rgba_bar(
                canvas, x0, center_y - thickness / 2.0, x1 - x0, thickness, color
            )

    for part_index in (0, 1):  # effects, stroke
        for parts, x, y in glyph_items:
            _paste_rgba(canvas, parts[part_index], x, y)
    extra_cursor = 0
    for glyph_index, (parts, x, y) in enumerate(glyph_items):
        while (
            extra_cursor < len(fill_extras)
            and fill_extras[extra_cursor][0] == glyph_index
        ):
            _run_fill_extra(fill_extras[extra_cursor][1], fill_extras[extra_cursor][2])
            extra_cursor += 1
        _paste_rgba(canvas, parts[2], x, y)
    while extra_cursor < len(fill_extras):
        _run_fill_extra(fill_extras[extra_cursor][1], fill_extras[extra_cursor][2])
        extra_cursor += 1

    return _crop_rgba_fixed(
        canvas,
        padding,
        padding + geometry["paint_width"],
        padding,
        padding + geometry["paint_height"],
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
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
    profile_stats: dict | None = None,
):
    document = ensure_rich_text_document(text)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    _ = h  # 包络由内容决定，外部最小高度不再参与画布
    layouts = _build_rich_vertical_layout(
        document, font_size, stroke_ratio, fg, bg, letter_spacing, profile_stats
    )
    geometry = _rich_vertical_layout_geometry(layouts, font_size, line_spacing)
    body_height = geometry["body_height"]
    padding = int(max(font_size * 2.0, 16))
    canvas = np.zeros(
        (
            int(max(geometry["paint_height"], 1)) + padding * 2,
            int(max(geometry["paint_width"], 1)) + padding * 2,
            4,
        ),
        dtype=np.uint8,
    )
    content_left = padding
    content_right = padding + geometry["paint_width"]
    content_top = padding
    content_bottom = padding + geometry["paint_height"]
    columns = _rich_vertical_column_positions(layouts, geometry, padding)

    # 与横排同构：一次遍历构建 (effects, stroke, fill) 三层绘制项，按全局
    # 顺序三次粘贴。emphasis 圆点与列注音属 fill 层，按列尾在遍历序中的
    # 位置插入，绘制顺序与旧三遍路径完全相同。
    glyph_items = []  # (parts, x, y)
    fill_extras = []  # (index_in_glyph_items, 'disc'|'ruby', args)
    for idx, layout in enumerate(layouts):
        body_left, body_right, _ = columns[idx]
        thickness = float(layout.thickness)
        line_origin_y = _vertical_line_origin_y(
            float(padding + geometry["top_extra"]),
            alignment,
            body_height,
            layout.height,
        )
        for item in layout.items:
            if isinstance(item, TcyPlan):
                parts = _text_layer_parts(
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
                if parts is None:
                    continue
                # 纵中横整组水平压缩：与计划几何同一公式（ceil），保证压缩
                # 后 fill 层宽 == item.width。
                parts = _scale_parts_x(parts, item.scale_x)
                x = _rich_vertical_tcy_layer_x(body_left, thickness, item)
                y = (
                    line_origin_y
                    + item.main_start
                    + item.source.style.transform.offset_y * item.font_size / 100.0
                    + item.paint_offset_y
                )
                glyph_items.append((parts, round(x), round(y)))
                continue
            if isinstance(item, VerticalPlaceholderPlan):
                continue
            if not isinstance(item, VerticalCharPlan):
                continue
            parts = _vertical_char_parts(item)
            if parts is None:
                continue
            char_x = _rich_vertical_char_layer_x(body_left, thickness, item)
            char_y = (
                line_origin_y
                + item.cursor_y
                + item.base.y
                + item.span.style.transform.offset_y * item.font_size / 100.0
                + item.paint_offset_y
            )
            glyph_items.append((parts, round(char_x), round(char_y)))
        for emphasis in layout.emphasis_plans:
            dot_color = _style_fill_color(emphasis.source.style, fg)
            for main_center in emphasis.main_centers:
                fill_extras.append(
                    (
                        len(glyph_items),
                        "disc",
                        (
                            body_right + emphasis.cross_center,
                            line_origin_y + main_center,
                            emphasis.radius,
                            dot_color,
                        ),
                    )
                )
        for ruby_plan in layout.ruby_plans:
            fill_extras.append(
                (
                    len(glyph_items),
                    "ruby",
                    (ruby_plan, body_right, line_origin_y),
                )
            )
        for underline in layout.underline_plans:
            fill_extras.append(
                (
                    len(glyph_items),
                    "bar",
                    (
                        line_origin_y + underline.main_start,
                        line_origin_y + underline.main_end,
                        body_right + underline.cross_center,
                        underline.thickness,
                        _style_fill_color(underline.source.style, fg),
                    ),
                )
            )
        for strikethrough in layout.strikethrough_plans:
            fill_extras.append(
                (
                    len(glyph_items),
                    "bar",
                    (
                        line_origin_y + strikethrough.main_start,
                        line_origin_y + strikethrough.main_end,
                        body_right + strikethrough.cross_center,
                        strikethrough.thickness,
                        _style_fill_color(strikethrough.source.style, fg),
                    ),
                )
            )

    def _run_fill_extra(kind, args):
        if kind == "disc":
            cx, cy, radius, color = args
            _draw_rgba_disc(canvas, cx, cy, radius, color)
        elif kind == "bar":
            # 文字装饰：主轴 [y0, y1) × 交叉轴以 center_x 为中心的实心竖条
            y0, y1, center_x, thickness, color = args
            _draw_rgba_bar(
                canvas, center_x - thickness / 2.0, y0, thickness, y1 - y0, color
            )
        else:
            ruby_plan, right, origin_y = args
            _paint_vertical_ruby(canvas, ruby_plan, right, origin_y, fg, letter_spacing)

    for part_index in (0, 1):  # effects, stroke
        for parts, x, y in glyph_items:
            _paste_rgba(canvas, parts[part_index], x, y)
    extra_cursor = 0
    for glyph_index, (parts, x, y) in enumerate(glyph_items):
        while (
            extra_cursor < len(fill_extras)
            and fill_extras[extra_cursor][0] == glyph_index
        ):
            _run_fill_extra(fill_extras[extra_cursor][1], fill_extras[extra_cursor][2])
            extra_cursor += 1
        _paste_rgba(canvas, parts[2], x, y)
    while extra_cursor < len(fill_extras):
        _run_fill_extra(fill_extras[extra_cursor][1], fill_extras[extra_cursor][2])
        extra_cursor += 1

    return _crop_rgba_fixed(
        canvas, content_left, content_right, content_top, content_bottom
    )


def measure_rich_text_metrics(
    font_size: int,
    text,
    is_horizontal: bool,
    line_spacing: float,
    config=None,
    stroke_width: float | None = None,
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
            document,
            base_font,
            stroke_ratio,
            measure_bg,
            False,
            letter_spacing,
        )
        if not layouts:
            return {"width": 0, "height": 0, "n_lines": 0, "body_center": (0.0, 0.0)}
        geometry = _rich_horizontal_layout_geometry(layouts, base_font, line_spacing)
        return {
            "width": int(geometry["paint_width"]),
            "height": int(geometry["paint_height"]),
            "n_lines": max(1, len(layouts)),
            "body_center": geometry["body_center"],
        }

    # 与横排同口径：全局描边直接参与竖排几何（字符/TCY 的描边 pad 与 TCY
    # 压缩系数都依赖 stroke 存在性），dummy 颜色即可。渲染路径约定
    # bg=None ⟺ 描边禁用（此时 stroke_ratio 同为 0），测量按此对齐。
    measure_bg = (0, 0, 0) if stroke_ratio > 0 else None
    layouts = _build_rich_vertical_layout(
        document, base_font, stroke_ratio, (0, 0, 0), measure_bg, letter_spacing
    )
    if not layouts:
        return {"width": 0, "height": 0, "n_lines": 0, "body_center": (0.0, 0.0)}
    geometry = _rich_vertical_layout_geometry(layouts, base_font, line_spacing)
    return {
        "width": int(geometry["paint_width"]),
        "height": int(geometry["paint_height"]),
        "n_lines": len(layouts),
        "body_center": (
            float(geometry["body_center_x"]),
            float(geometry["body_center_y"]),
        ),
    }


def measure_rich_text_horizontal(
    font_size: int,
    text,
    line_spacing: float,
    config=None,
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
) -> tuple[int, int, int]:
    metrics = measure_rich_text_metrics(
        font_size,
        text,
        True,
        line_spacing,
        config=config,
        stroke_width=stroke_width,
        letter_spacing=letter_spacing,
    )
    return metrics["width"], metrics["height"], metrics["n_lines"]


def measure_rich_text_vertical(
    font_size: int,
    text,
    line_spacing: float,
    config=None,
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
) -> tuple[int, int, int]:
    metrics = measure_rich_text_metrics(
        font_size,
        text,
        False,
        line_spacing,
        config=config,
        stroke_width=stroke_width,
        letter_spacing=letter_spacing,
    )
    return metrics["width"], metrics["height"], metrics["n_lines"]


def _resolve_stroke_ratio(config=None, stroke_width: float | None = None) -> float:
    render_cfg = getattr(config, "render", None)
    if bool(getattr(render_cfg, "disable_font_border", False)):
        return 0.0
    if stroke_width is not None:
        return float(stroke_width)
    return float(getattr(render_cfg, "stroke_width", 0.07))


def get_char_offset_x(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _measure_horizontal_text_width(
        "　" if cdpt == "＿" else cdpt, font_size, letter_spacing
    )


def get_string_width(font_size: int, text: str, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        max_width = 0
        for paragraph in document.paragraphs:
            width = 0
            for span in paragraph.spans:
                with _style_font_scope(span.style):
                    width += _measure_horizontal_text_width(
                        span.text,
                        _style_font_size(font_size, span.style),
                        letter_spacing,
                    )
            max_width = max(max_width, width)
        return max_width
    return _measure_horizontal_text_width(text, font_size, letter_spacing)


def get_char_offset_y(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _vertical_base(
        font_size, "　" if cdpt == "＿" else cdpt, letter_spacing
    ).advance_y


def get_string_height(font_size: int, text: str, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        max_height = 0
        for paragraph in document.paragraphs:
            max_height = max(
                max_height,
                _rich_paragraph_vertical_metrics(font_size, paragraph, letter_spacing)[
                    0
                ],
            )
        return max_height
    text = text or ""
    total = 0
    for char in re.sub(r"\s*(?:\[BR\]|<br>|【BR】)\s*", "", text, flags=re.IGNORECASE):
        total += get_char_offset_y(font_size, char, letter_spacing)
    return total


def _rich_paragraph_horizontal_width(
    font_size: int, paragraph, letter_spacing: float = 1.0
) -> int:
    width = 0
    for span in paragraph.spans:
        with _style_font_scope(span.style):
            width += _measure_horizontal_text_width(
                span.text, _style_font_size(font_size, span.style), letter_spacing
            )
    return width


def _rich_paragraph_vertical_metrics(
    font_size: int, paragraph, letter_spacing: float = 1.0
) -> tuple[int, int]:
    line_height = 0
    line_width = font_size
    for span in paragraph.spans:
        span_font_size = _style_font_size(font_size, span.style)
        if span.tcy:
            with _style_font_scope(span.style):
                line_height += calc_horizontal_block_height(
                    span_font_size, span.text, letter_spacing=letter_spacing
                )
        else:
            with _style_font_scope(span.style):
                line_height += sum(
                    get_char_offset_y(span_font_size, c, letter_spacing)
                    for c in span.text
                )
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
    return legacy_line_breaks_to_document(text or "")


def put_text_horizontal(
    font_size: int,
    text: str,
    width: int,
    height: int,
    alignment: str,
    reversed_direction: bool,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    lang: str = "en_US",
    hyphenate: bool = True,
    line_spacing: int = 0,
    config=None,
    region_count: int = 1,
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
    profile_stats: dict | None = None,
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
    fg: tuple[int, int, int],
    bg: tuple[int, int, int] | None,
    line_spacing: int,
    config=None,
    region_count: int = 1,
    stroke_width: float | None = None,
    letter_spacing: float = 1.0,
    profile_stats: dict | None = None,
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


def calc_horizontal(
    font_size: int,
    text: str,
    max_width: int,
    max_height: int,
    language: str = "en_US",
    hyphenate: bool = True,
    letter_spacing: float = 1.0,
):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        return document.paragraphs, [
            _rich_paragraph_horizontal_width(
                font_size, paragraph, letter_spacing=letter_spacing
            )
            for paragraph in document.paragraphs
        ]
    from ..auto_linebreak import _calc_horizontal_layout

    _ = max_height
    return _calc_horizontal_layout(
        font_size, text, max_width, language, hyphenate, letter_spacing=letter_spacing
    )


def calc_vertical(
    font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0
):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        metrics = [
            _rich_paragraph_vertical_metrics(
                font_size, paragraph, letter_spacing=letter_spacing
            )
            for paragraph in document.paragraphs
        ]
        return document.paragraphs, [height for height, _ in metrics]
    from ..auto_linebreak import _calc_vertical_layout

    return _calc_vertical_layout(
        font_size, text, max_height, config, letter_spacing=letter_spacing
    )


def calc_vertical_metrics(
    font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0
):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        metrics = [
            _rich_paragraph_vertical_metrics(
                font_size, paragraph, letter_spacing=letter_spacing
            )
            for paragraph in document.paragraphs
        ]
        return (
            document.paragraphs,
            [height for height, _ in metrics],
            [width for _, width in metrics],
        )
    from ..auto_linebreak import _layout_vertical_metrics

    return _layout_vertical_metrics(
        font_size, text, max_height, config, letter_spacing=letter_spacing
    )
