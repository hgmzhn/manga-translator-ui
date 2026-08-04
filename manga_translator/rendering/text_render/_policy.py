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
    # 下划线：线宽与偏移都是基准字号的比例（与描边 stroke_ratio 同口径）。
    # underline_offset 是横排基线到线条上沿的距离；vertical_underline_offset
    # 是竖排列正文边缘到线条中心的距离（与 vertical_emphasis_offset 同口径，
    # 取值小于它，使下划线落在正文与着重号之间而不重叠）。
    underline_thickness: float = 0.06
    underline_offset: float = 0.14
    vertical_underline_offset: float = 0.10
    ruby_overflow_ratio: float = 1.20
    # 纵中横块允许的最大墨迹宽度（基准字号倍数），超出按比例整组水平压缩。
    # 与参考实现（mtu-json-gui）一致：留 1.1 倍余量，防止全角数字挤压过度。
    tcy_max_width: float = 1.10


RICH_TEXT_POLICY = RichTextRenderPolicy()
