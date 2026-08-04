"""Typed vertical glyph and item plans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

import numpy as np

from ..rich_text import RenderSpan
from ._plans import Bounds, EmphasisPlan, RubyPlan, TcyPlan, UnderlinePlan


@dataclass(frozen=True)
class VerticalGlyphBase:
    translated: str
    rot_degree: int
    bitmap: Optional[np.ndarray]
    advance_y: int
    ink_x: float
    ink_w: float
    y: int
    advance_x: int
    glyph_left: float
    frame_width: int


@dataclass(frozen=True)
class VerticalCharPlan:
    span: RenderSpan
    base: VerticalGlyphBase
    font_size: int
    fill: tuple[int, int, int]
    stroke: Optional[tuple[int, int, int]]
    stroke_ratio: float
    advance_y: int
    pre_advance_y: int
    post_advance_y: int
    paint_width: int
    paint_height: int
    paint_offset_x: float
    paint_offset_y: float
    cursor_y: int = 0


@dataclass(frozen=True)
class VerticalPlaceholderPlan:
    span: RenderSpan
    font_size: int
    advance_y: int
    pre_advance_y: int
    post_advance_y: int
    cursor_y: int = 0


VerticalItemPlan = Union[VerticalCharPlan, VerticalPlaceholderPlan, TcyPlan]


@dataclass(frozen=True)
class VerticalColumnPlan:
    thickness: int
    height: int
    content_paint_bounds: Bounds
    ruby_cross_extent: int
    annotation_cross_extent: int
    items: tuple[VerticalItemPlan, ...]
    ruby_plans: tuple[RubyPlan, ...]
    emphasis_plans: tuple[EmphasisPlan, ...]
    underline_plans: tuple[UnderlinePlan, ...] = ()
    line_kerning: float | None = None
    next_kerning: float | None = None
