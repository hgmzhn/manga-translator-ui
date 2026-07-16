"""Central rich-text rendering ratios.

These values describe layout policy rather than implementation details.  Keep
them here so measurement and painting cannot silently drift apart.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RichTextRenderPolicy:
    horizontal_ruby_size: float = 0.42
    vertical_ruby_size: float = 0.36
    vertical_ruby_side_space: float = 0.45
    emphasis_side_space: float = 0.35
    decoration_gap: float = 0.08
    emphasis_radius: float = 0.055
    vertical_emphasis_offset: float = 0.20
    ruby_overflow_ratio: float = 1.20


RICH_TEXT_POLICY = RichTextRenderPolicy()
