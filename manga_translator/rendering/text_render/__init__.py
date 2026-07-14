"""text_render 包 facade。

公共 API 与既有消费方依赖的符号在此收口；实现按职责分层：
_shared（工具/缓存原语）→ _fonts（Qt 字体运行时）→ _glyphs（字形光栅）、
_compose（图层合成与特效，独立）→ _layout（横竖排布局与包络几何）→
_render（put_text_*/measure_*/calc_* 入口）。

外部消费方（rendering/__init__.py、text_render_hq、auto_linebreak、编辑器
backend、server 路由、测试）只应通过本命名空间访问。
"""
from ..rich_text import (
    RenderSpan,
    RichTextDocument,
    TextStyle,
    ensure_rich_text_document,
    is_rich_text_document,
    normalize_rich_linebreaks,
)
from ._compose import (
    DEFAULT_ITALIC_ANGLE,
    _paste_bitmap,
    _style_font_size,
    add_color,
)
from ._fonts import (
    DEFAULT_FONT,
    DEFAULT_FONT_FAMILY,
    FALLBACK_FONTS,
    _sanitized_font_bytes,
    _state,
    _style_font_scope,
    load_font_file,
    qt_family_is_ambiguous,
    register_font_file,
    select_hyphenator,
    set_bold,
    set_font,
    strip_qt_foundry_brackets,
)
from ._layout import (
    CJK_Compatibility_Forms_translate,
    _build_rich_horizontal_layout,
    _build_rich_vertical_layout,
    _line_metrics,
    _line_surface,
    _measure_horizontal_text_width,
    _rich_horizontal_layout_geometry,
    _rich_span_surface,
    _rich_vertical_column_positions,
    _rich_vertical_layout_geometry,
    _vertical_base,
    _vertical_char_bitmap_x,
    calc_horizontal_block_height,
    calc_horizontal_line_spacing_px,
    calc_vertical_line_spacing_px,
    get_vertical_char_bitmap_width,
)
from ._render import (
    calc_horizontal,
    calc_vertical,
    calc_vertical_metrics,
    get_char_offset_x,
    get_char_offset_y,
    get_string_height,
    get_string_width,
    measure_rich_text_horizontal,
    measure_rich_text_metrics,
    measure_rich_text_vertical,
    put_text_horizontal,
    put_text_vertical,
)
