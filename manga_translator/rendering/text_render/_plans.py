"""Typed, renderer-independent layout plans.

This module contains semantic geometry only: no Qt objects, numpy layers, font
state, or canvas operations.  Measurement and painting may attach their own
glyph data, but they must consume the same plans produced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..rich_text import RenderSpan
from ._policy import RICH_TEXT_POLICY


class FlowAxis(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Bounds:
    left: float
    top: float
    right: float
    bottom: float


@dataclass
class RubyGlyph:
    char: str
    main_center: float
    main_scale: float = 1.0
    paint_width: int = 0
    paint_height: int = 0


@dataclass
class RubyPlan:
    source: RenderSpan
    font_size: int
    stroke_ratio: float
    glyphs: tuple[RubyGlyph, ...]
    paint_start: float
    paint_end: float
    cross_center: float = 0.0


@dataclass
class EmphasisPlan:
    source: RenderSpan
    main_centers: tuple[float, ...]
    radius: int
    frame_size: int
    cross_center: float = 0.0


@dataclass
class UnderlinePlan:
    """下划线：沿排版主轴（横排行方向 / 竖排列方向）的一条实心线。

    与着重号同构的列/行级装饰：``main_start``/``main_end`` 是主轴上的区间，
    ``cross_center`` 是交叉轴上线条中心的位置（横排相对基线向下，竖排相对
    列正文右边缘向右）。装饰跟排版方向走，不跟单个字形走 —— 字形自身的
    旋转/镜像/斜体切变都不作用在它上面（竖排里被旋转 90° 的括号旁边，线
    仍然是上下方向的一条）。
    """

    source: RenderSpan
    main_start: float
    main_end: float
    thickness: int
    cross_center: float = 0.0


@dataclass(frozen=True)
class TcyPlan:
    source: RenderSpan
    text: str
    font_size: int
    stroke_ratio: float
    width: int
    height: int
    paint_offset_x: float
    paint_offset_y: float
    advance_main: int
    pre_advance: int
    post_advance: int
    main_start: float = 0.0
    # 整组水平压缩系数（<=1）：墨迹宽超过 tcy_max_width*基准字号时按比例
    # 压缩，作用于最终图层（含描边/特效）。width/paint_offset_x 已按此压缩。
    scale_x: float = 1.0

@dataclass
class HorizontalRunPlan:
    span: RenderSpan
    font_size: int
    stroke_ratio: float
    logical_width: float
    ascent: float
    descent: float
    has_ink: bool
    left_rel: float
    top_rel: float
    ink_width: int
    ink_height: int
    main_rect: Rect | None = None
    ruby: RubyPlan | None = None
    emphasis: EmphasisPlan | None = None
    underline: UnderlinePlan | None = None


@dataclass(frozen=True)
class HorizontalLinePlan:
    runs: tuple[HorizontalRunPlan, ...]
    logical_width: float
    body_bounds: Bounds
    paint_bounds: Bounds
    line_kerning: float | None = None
    next_kerning: float | None = None
    # Bounds used to place the next line.  Paint-only effects such as glow
    # belong to ``paint_bounds`` but must not become extra line spacing.
    spacing_bounds: Bounds | None = None


def plan_emphasis(
    source: RenderSpan,
    main_intervals: tuple[tuple[float, float], ...],
    font_size: int,
) -> EmphasisPlan:
    radius = max(1, int(round(font_size * RICH_TEXT_POLICY.emphasis_radius)))
    return EmphasisPlan(
        source=source,
        main_centers=tuple((start + end) / 2.0 for start, end in main_intervals),
        radius=radius,
        frame_size=radius * 2 + 3,
    )


def _underline_thickness_px(font_size: int) -> int:
    """下划线线宽（像素）：按字号比例缩放，横竖排共用一个口径。"""
    return max(1, int(round(font_size * RICH_TEXT_POLICY.underline_thickness)))


def plan_underline(
    source: RenderSpan,
    main_start: float,
    main_end: float,
    font_size: int,
) -> UnderlinePlan:
    return UnderlinePlan(
        source=source,
        main_start=float(main_start),
        main_end=float(main_end),
        thickness=_underline_thickness_px(font_size),
    )


def plan_ruby_glyphs(
    text: str,
    base_start: float,
    base_end: float,
    axis: FlowAxis,
    nominal_glyph_extent: float = 0.0,
    overflow_ratio: float = RICH_TEXT_POLICY.ruby_overflow_ratio,
) -> tuple[RubyGlyph, ...]:
    text = str(text or "")
    start = float(base_start)
    end = max(start, float(base_end))
    extent = max(1.0, end - start)
    if not text:
        return ()

    count = len(text)
    raw_extent = max(0.0, float(nominal_glyph_extent)) * count
    should_compress = (
        axis is FlowAxis.VERTICAL
        and nominal_glyph_extent > 0
        and raw_extent > extent
    )
    if should_compress:
        scale = (extent * max(0.0, float(overflow_ratio))) / raw_extent
        glyph_extent = float(nominal_glyph_extent) * scale
        paint_start = start + (extent - raw_extent * scale) / 2.0
        return tuple(
            RubyGlyph(char, paint_start + glyph_extent * (index + 0.5), scale)
            for index, char in enumerate(text)
        )

    slot_extent = extent / count
    return tuple(
        RubyGlyph(char, start + slot_extent * (index + 0.5))
        for index, char in enumerate(text)
    )
