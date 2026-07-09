import logging
import math
import os
import re
import threading
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass, field
from time import perf_counter
from typing import Optional, Tuple

import cv2
import numpy as np
from hyphen import Hyphenator
from hyphen.dictools import LANGUAGES as HYPHENATOR_LANGUAGES
from langcodes import standardize_tag
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPainterPathStroker, QRawFont, QTextLayout

from ..utils import BASE_PATH, parse_color
from .rich_text import RichTextDocument, RenderSpan, TextStyle, ensure_rich_text_document, is_rich_text_document, normalize_rich_linebreaks

try:
    HYPHENATOR_LANGUAGES.remove('fr')
    HYPHENATOR_LANGUAGES.append('fr_FR')
except Exception:
    pass

DEFAULT_FONT = os.path.join(BASE_PATH, 'fonts', 'Arial-Unicode-Regular.ttf')
FALLBACK_FONTS = [
    os.path.join(BASE_PATH, 'fonts/Arial-Unicode-Regular.ttf'),
    os.path.join(BASE_PATH, 'fonts/msyh.ttc'),
    os.path.join(BASE_PATH, 'fonts/msgothic.ttc'),
]
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


def _profile_add(profile_stats: Optional[dict], key: str, start_time: Optional[float]) -> None:
    if profile_stats is not None and start_time is not None:
        profile_stats[key] = profile_stats.get(key, 0.0) + (perf_counter() - start_time) * 1000.0

_QT_FONT_PROBE_SIZE = 32.0
_thread_state = threading.local()
_qt_runtime_lock = threading.Lock()
_qt_runtime_app = None
_font_descriptor_cache = {}
_font_registration_cache = {}
_hyphenator_cache = {}
_RAW_FONT_CACHE_MAX = 128
_QFONT_CACHE_MAX = 192
_GLYPH_SPEC_CACHE_MAX = 4096
_GLYPH_RASTER_CACHE_MAX = 2048
_STROKE_CACHE_MAX = 1024
_VERTICAL_CACHE_MAX = 2048
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class GlyphSpec:
    raw_font: QRawFont
    glyph_id: int
    cache_key: Tuple


@dataclass(frozen=True)
class GlyphRaster:
    alpha: np.ndarray
    left: int
    top: int
    advance_x: int
    advance_y: int
    vert_bearing_y: int
    frame_width: int


@dataclass(frozen=True)
class LayoutFontDescriptor:
    family: str
    style: str = ''


@dataclass
class FontState:
    font: str = ''
    font_selection: list = field(default_factory=list)
    raw_fonts: dict = field(default_factory=OrderedDict)
    qfonts: dict = field(default_factory=OrderedDict)
    glyph_specs: dict = field(default_factory=OrderedDict)
    glyphs: dict = field(default_factory=OrderedDict)
    strokes: dict = field(default_factory=OrderedDict)
    measures: dict = field(default_factory=dict)
    vertical: dict = field(default_factory=OrderedDict)


def CJK_Compatibility_Forms_translate(cdpt: str, direction: int):
    """渲染层不替换字符，只返回方向相关的旋转信息。"""
    if direction == 1 and (cdpt == 'ー' or cdpt in _VERTICAL_ROTATE_CHARS):
        return cdpt, 90
    return cdpt, 0


def compact_special_symbols(text: str, *, convert_ascii_ellipsis: bool = True) -> str:
    # 渲染层不再做字符替换，统一交给外部的 text_replacements.yaml 规则处理
    return text or ''


def normalize_vertical_ellipsis_text(text: str) -> str:
    # 渲染层不再做字符替换，统一交给外部的 text_replacements.yaml 规则处理
    return text or ''


def _normalize_horizontal_block_content(content: str) -> str:
    # F14：BR 编解码统一走 rich_text.normalize_rich_linebreaks（BR→\n 后去换行，
    # 与旧的 _BR_RE.sub('') 输出一致）
    content = normalize_rich_linebreaks(content).replace('\r', '').replace('\n', '')
    return content.translate(_HORIZONTAL_SYMBOL_HALFWIDTH_MAP) if re.fullmatch(r'[!?！？]+', content) else content


def add_color(bw_char_map, color, stroke_char_map, stroke_color):
    """合成文字和描边为 RGBA 图层。

    关键做法（解决灰边/脏边，同时防止描边层缺失时整段透明）：
    1. 强制 stroke_alpha = max(stroke_alpha, text_alpha)，保证描边在空间上完全
       覆盖文字的所有抗锯齿像素，消除因两次独立光栅化造成的对齐偏差；
       同时保证描边层为全零（stroke_ratio=0 等场景）时输出 alpha 仍含正文。
    2. 将描边视作文字的"底色"，抗锯齿过渡像素直接在描边纯色上混合，而不是
       两个半透明层的 over 叠加 —— 这是原来灰边的根源。
    """
    H, W = bw_char_map.shape[:2]
    if bw_char_map.size == 0:
        return np.zeros((H, W, 4), dtype=np.uint8)

    out = np.zeros((H, W, 4), dtype=np.uint8)
    color_arr = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)

    # 无描边：直接输出文字图层
    if stroke_color is None or stroke_char_map is None:
        out[:, :, :3] = np.clip(color_arr, 0, 255).astype(np.uint8)
        out[:, :, 3] = bw_char_map
        return out

    stroke_color_arr = np.asarray(stroke_color, dtype=np.float32).reshape(1, 1, 3)

    # 1) 强制 stroke_alpha >= text_alpha —— 描边层全零时输出退化为纯文字
    text_alpha_u8 = bw_char_map
    stroke_alpha_u8 = np.maximum(stroke_char_map, text_alpha_u8)

    # 2) 文字在描边纯色底上混合（局部不透明，无半透明层叠加）
    text_af = (text_alpha_u8.astype(np.float32) / 255.0)[:, :, None]
    rgb = color_arr * text_af + stroke_color_arr * (1.0 - text_af)

    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = stroke_alpha_u8
    return out


def _crop_and_color(canvas_text: np.ndarray, canvas_border: np.ndarray, fg, bg):
    """按有效 alpha 区域裁剪后再上色，避免给大面积透明 padding 做无用合成。"""
    combined = cv2.add(canvas_text, canvas_border)
    x, y, w, h = cv2.boundingRect(combined)
    if w == 0 or h == 0:
        return None
    text_crop = canvas_text[y:y + h, x:x + w]
    border_crop = canvas_border[y:y + h, x:x + w]
    return add_color(text_crop, fg, border_crop, bg)


def _parse_rgb(value, fallback=(0, 0, 0)) -> Tuple[int, int, int]:
    """F16：薄委托 utils.generic.parse_color（#RGB/#RRGGBB/序列、钳制、回退）。

    在公共 helper 之上保证恒返回 RGB 三元组：fallback 本身也允许是
    hex 字符串/序列，两级都解析失败时回退黑色。
    """
    color = parse_color(value, None)
    if color is None:
        color = parse_color(fallback, None)
    return tuple(color) if color is not None else (0, 0, 0)


def _style_font_size(base_font_size: int, style: TextStyle) -> int:
    if style.font_size is not None:
        return max(1, int(round(style.font_size)))
    return max(1, int(round(base_font_size * (style.scale or 1.0))))


def _style_bold_extra(font_size: int) -> int:
    return max(1, int(round(font_size * 0.035)))


def _style_fill_color(style: TextStyle, fallback) -> Tuple[int, int, int]:
    return _parse_rgb(style.color, fallback)


def _style_stroke_color(style: TextStyle, fallback):
    if style.stroke and style.stroke.color:
        return _parse_rgb(style.stroke.color, fallback or (0, 0, 0))
    return None if fallback is None else _parse_rgb(fallback, (0, 0, 0))


def _style_stroke_ratio(style: TextStyle, font_size: int, global_stroke_ratio: float, global_stroke_color) -> float:
    if style.stroke:
        if style.stroke.width is not None:
            return max(0.0, float(style.stroke.width))
        return max(float(global_stroke_ratio), 0.07)
    return max(float(global_stroke_ratio), 0.0) if global_stroke_color is not None else 0.0


def _rgba_from_alpha_pair(text_alpha: np.ndarray, border_alpha: Optional[np.ndarray], fill_color, stroke_color):
    if text_alpha is None or text_alpha.size == 0:
        return None
    if stroke_color is None or border_alpha is None or border_alpha.size == 0:
        border_alpha = None
    return add_color(text_alpha, fill_color, border_alpha, stroke_color)


def _embolden_alpha(alpha: np.ndarray, font_size: int):
    if alpha is None or alpha.size == 0:
        return alpha, 0, 0
    radius = _style_bold_extra(font_size)
    canvas = np.zeros((alpha.shape[0] + radius * 2, alpha.shape[1] + radius * 2), dtype=np.uint8)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            y = radius + dy
            x = radius + dx
            target = canvas[y:y + alpha.shape[0], x:x + alpha.shape[1]]
            np.maximum(target, alpha, out=target)
    nz = cv2.findNonZero(canvas)
    if nz is None:
        return np.zeros((0, 0), dtype=np.uint8), 0, 0
    x, y, w, h = cv2.boundingRect(nz)
    return canvas[y:y + h, x:x + w], x - radius, y - radius


def _stroke_alpha_from_text_alpha(text_alpha: np.ndarray, stroke_px: int):
    if text_alpha is None or text_alpha.size == 0:
        return np.zeros((0, 0), dtype=np.uint8), 0, 0
    pad = max(1, int(stroke_px)) + 1
    padded = cv2.copyMakeBorder(text_alpha, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    fg_mask = (padded >= 128).astype(np.uint8) * 255
    bg_mask = cv2.bitwise_not(fg_mask)
    dist = cv2.distanceTransform(bg_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    stroke_region = np.clip((stroke_px + 0.5 - dist), 0.0, 1.0)
    stroke_alpha = (stroke_region * 255).astype(np.uint8)
    return np.maximum(stroke_alpha, padded), -pad, -pad


def _crop_rgba(canvas: np.ndarray):
    if canvas is None or canvas.size == 0 or canvas.shape[2] != 4:
        return None
    alpha = canvas[:, :, 3]
    nz = cv2.findNonZero(alpha)
    if nz is None:
        return None
    x, y, w, h = cv2.boundingRect(nz)
    return None if w == 0 or h == 0 else canvas[y:y + h, x:x + w]


def _crop_rgba_preserve_x(canvas: np.ndarray, x0: int, x1: int):
    if canvas is None or canvas.size == 0 or canvas.shape[2] != 4:
        return None
    alpha = canvas[:, :, 3]
    nz = cv2.findNonZero(alpha)
    if nz is None:
        return None
    _, y, _, h = cv2.boundingRect(nz)
    x0 = max(0, min(int(x0), canvas.shape[1]))
    x1 = max(x0, min(int(x1), canvas.shape[1]))
    return None if x1 <= x0 or h == 0 else canvas[y:y + h, x0:x1]


def _paste_rgba(dst: np.ndarray, src: np.ndarray, x: int, y: int):
    if src is None or src.size == 0:
        return
    rows, width = src.shape[:2]
    x2, y2 = x + width, y + rows
    sx1, sy1, sx2, sy2 = max(0, x), max(0, y), min(dst.shape[1], x2), min(dst.shape[0], y2)
    if sx1 >= sx2 or sy1 >= sy2:
        return
    bx1, by1 = sx1 - x, sy1 - y
    src_view = src[by1:by1 + (sy2 - sy1), bx1:bx1 + (sx2 - sx1)]
    dst_view = dst[sy1:sy2, sx1:sx2]
    if not dst_view[:, :, 3].any():
        # F26 快路径：目标区域完全透明（竖排逐字符粘贴的常见情形），
        # alpha-over 退化为直接覆盖，跳过全量浮点混合
        dst_view[:] = src_view
        return
    src_crop = src_view.astype(np.float32)
    dst_crop = dst_view.astype(np.float32)
    src_a = src_crop[:, :, 3:4] / 255.0
    dst_a = dst_crop[:, :, 3:4] / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.where(out_a <= 0, 1.0, out_a)
    out_rgb = (src_crop[:, :, :3] * src_a + dst_crop[:, :, :3] * dst_a * (1.0 - src_a)) / safe_a
    out = np.zeros_like(dst_crop)
    out[:, :, :3] = np.clip(out_rgb, 0, 255)
    out[:, :, 3:4] = np.clip(out_a * 255.0, 0, 255)
    dst[sy1:sy2, sx1:sx2] = out.astype(np.uint8)


def _warp_geometry(height: int, width: int, matrix: np.ndarray):
    """仿射变换的纯几何计算：输入框 (height, width) 经 matrix 变换后的
    输出框尺寸与偏移。渲染（_warp_rgba_layer）与度量共用，保证几何一致。"""
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]]).reshape(-1, 1, 2)
    transformed = cv2.transform(corners, matrix).reshape(-1, 2)
    min_x, min_y = transformed.min(axis=0)
    max_x, max_y = transformed.max(axis=0)
    out_w = max(1, int(math.ceil(max_x - min_x)))
    out_h = max(1, int(math.ceil(max_y - min_y)))
    return out_h, out_w, min_x, min_y


def _warp_rgba_layer(layer: np.ndarray, matrix: np.ndarray):
    if layer is None or layer.size == 0:
        return layer, 0.0, 0.0
    h, w = layer.shape[:2]
    out_h, out_w, min_x, min_y = _warp_geometry(h, w, matrix)
    adjusted = matrix.copy()
    adjusted[0, 2] -= min_x
    adjusted[1, 2] -= min_y
    warped = cv2.warpAffine(
        layer,
        adjusted,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    return warped, float(min_x), float(min_y)


def _apply_style_layer_effects(layer: np.ndarray, style: TextStyle, font_size: int):
    if layer is None or layer.size == 0:
        return layer, 0.0, 0.0

    result = layer
    offset_x = 0.0
    offset_y = 0.0

    if style.transform.mirror_x:
        result = cv2.flip(result, 1)
    if style.transform.mirror_y:
        result = cv2.flip(result, 0)

    if style.italic:
        shear = -0.22
        matrix = np.float32([[1.0, shear, 0.0], [0.0, 1.0, 0.0]])
        result, dx, dy = _warp_rgba_layer(result, matrix)
        offset_x += dx
        offset_y += dy

    if style.transform.rotation:
        h, w = result.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), float(style.transform.rotation), 1.0)
        result, dx, dy = _warp_rgba_layer(result, matrix)
        offset_x += dx
        offset_y += dy

    return result, offset_x, offset_y


def _style_layer_effects_geometry(height: int, width: int, style: TextStyle):
    """_apply_style_layer_effects 的纯几何版本（度量用，F21）。

    mirror 不改变尺寸；italic/rotation 用与渲染路径相同的矩阵经 _warp_geometry
    按角点计算输出框与偏移，不做实际 warp。返回 (height, width, offset_x, offset_y)。
    """
    if height <= 0 or width <= 0:
        return height, width, 0.0, 0.0

    offset_x = 0.0
    offset_y = 0.0

    if style.italic:
        shear = -0.22
        matrix = np.float32([[1.0, shear, 0.0], [0.0, 1.0, 0.0]])
        height, width, dx, dy = _warp_geometry(height, width, matrix)
        offset_x += float(dx)
        offset_y += float(dy)

    if style.transform.rotation:
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), float(style.transform.rotation), 1.0)
        height, width, dx, dy = _warp_geometry(height, width, matrix)
        offset_x += float(dx)
        offset_y += float(dy)

    return height, width, offset_x, offset_y


def _draw_rgba_disc(dst: np.ndarray, center_x: float, center_y: float, radius: float, color):
    radius = max(1, int(round(radius)))
    size = radius * 2 + 3
    alpha = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(alpha, (size // 2, size // 2), radius, 255, -1, lineType=cv2.LINE_AA)
    layer = np.zeros((size, size, 4), dtype=np.uint8)
    layer[:, :, :3] = np.asarray(_parse_rgb(color), dtype=np.uint8)
    layer[:, :, 3] = alpha
    _paste_rgba(dst, layer, int(round(center_x)) - size // 2, int(round(center_y)) - size // 2)


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
    return {
        'spacing_x': int(spacing_x),
        'body_width': int(body_width),
        'layout_width': int(layout_width),
        'paint_width': int(layout_width + left_extra + right_extra),
        'left_extra': int(left_extra),
        'right_extra': int(right_extra),
        'body_center_x': float(left_extra) + float(layout_width) / 2.0,
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


def _bootstrap_qt_fontdir_for_offscreen() -> None:
    """Help Qt's offscreen/freetype font database find bundled fonts.

    Qt's offscreen plugin may rely on QT_QPA_FONTDIR or Qt6/lib/fonts. Our
    packaged fonts live under the project fonts/ directory, so expose that as a
    default font directory when running in offscreen mode and the caller did not
    already provide one.
    """
    if os.environ.get('QT_QPA_FONTDIR'):
        return
    if os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
        return
    font_dir = os.path.join(BASE_PATH, 'fonts')
    if os.path.isdir(font_dir):
        os.environ['QT_QPA_FONTDIR'] = font_dir
        logger.info('Using bundled fonts for Qt offscreen mode: %s', font_dir)


def _ensure_qt_runtime():
    global _qt_runtime_app
    app = QGuiApplication.instance()
    if app is not None:
        return app
    with _qt_runtime_lock:
        app = QGuiApplication.instance()
        if app is None:
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            _bootstrap_qt_fontdir_for_offscreen()
            _qt_runtime_app = QGuiApplication([])
            return _qt_runtime_app
        return app


def _normalize_font_path(path: str) -> str:
    return path.replace('\\', '/')


def _cache_get(cache: dict, key):
    if key not in cache:
        return None
    value = cache.pop(key)
    cache[key] = value
    return value


def _cache_put(cache: dict, key, value, max_entries: int):
    if key in cache:
        cache.pop(key)
    cache[key] = value
    while len(cache) > max_entries:
        cache.popitem(last=False)
    return value


def _clear_shape_caches(state: FontState):
    state.glyph_specs.clear()
    state.glyphs.clear()
    state.strokes.clear()
    state.measures.clear()
    state.vertical.clear()


def _resolve_existing_font_path(path: str) -> str:
    if not path:
        return ''
    path = _normalize_font_path(path)
    candidates = [path]
    if not os.path.isabs(path):
        candidates.extend([
            _normalize_font_path(os.path.join(BASE_PATH, 'fonts', os.path.basename(path))),
            _normalize_font_path(os.path.join(BASE_PATH, path)),
        ])
    return next((candidate for candidate in candidates if candidate and os.path.exists(candidate)), '')


def _state() -> FontState:
    state = getattr(_thread_state, 'value', None)
    if state is None:
        state = FontState()
        _thread_state.value = state
        set_font(DEFAULT_FONT)
    return _thread_state.value


def _raw_font(path: str, pixel_size: float) -> QRawFont:
    state = _state()
    _ensure_qt_runtime()
    norm_path = _normalize_font_path(path)
    pixel_size = float(max(pixel_size, 1.0))
    key = (norm_path, pixel_size)
    font = _cache_get(state.raw_fonts, key)
    if font is not None:
        return font
    # 复用已有实例：找同路径任意 size 的 font，拷贝后 setPixelSize
    for cached_key in reversed(state.raw_fonts):
        if cached_key[0] == norm_path:
            base = state.raw_fonts[cached_key]
            font = QRawFont(base)
            font.setPixelSize(pixel_size)
            return _cache_put(state.raw_fonts, key, font, _RAW_FONT_CACHE_MAX)
    # 首次加载：从文件创建
    font = QRawFont(norm_path, pixel_size)
    if not font.isValid():
        raise RuntimeError(f'Could not load Qt font: {norm_path}')
    return _cache_put(state.raw_fonts, key, font, _RAW_FONT_CACHE_MAX)


def _font_descriptor(path: str) -> LayoutFontDescriptor:
    _ensure_qt_runtime()
    path = _normalize_font_path(path)
    descriptor = _font_descriptor_cache.get(path)
    if descriptor:
        return descriptor

    if path not in _font_registration_cache:
        try:
            _font_registration_cache[path] = QFontDatabase.addApplicationFont(path)
        except Exception:
            _font_registration_cache[path] = -1

    family = ''
    style = ''
    try:
        raw = _raw_font(path, _QT_FONT_PROBE_SIZE)
        if raw.isValid():
            family = raw.familyName() or ''
            style = raw.styleName() or ''
    except Exception:
        pass

    if not family:
        font_id = _font_registration_cache.get(path, -1)
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
        if not family:
            raw = QRawFont(path, _QT_FONT_PROBE_SIZE)
            if raw.isValid():
                family = raw.familyName() or ''
                style = style or raw.styleName() or ''

    if not family:
        raise RuntimeError(f'Could not resolve Qt font family: {path}')
    descriptor = LayoutFontDescriptor(family=family, style=style)
    _font_descriptor_cache[path] = descriptor
    return descriptor


def _refresh_font_selection(state: FontState):
    selection = [state.font] if state.font else []
    for font_path in FALLBACK_FONTS:
        try:
            resolved = _resolve_existing_font_path(font_path)
            if resolved:
                _raw_font(resolved, _QT_FONT_PROBE_SIZE)
                if resolved not in selection:
                    selection.append(resolved)
        except Exception as exc:
            logger.error(f'Failed to load fallback font: {font_path} - {exc}')
    if selection != state.font_selection:
        state.font_selection = selection
        state.qfonts.clear()
        # glyph_specs/glyphs/strokes 的 key 含字体路径，切换字体时无需清空，
        # 保留缓存避免重复解析大字体文件的字形数据
        state.measures.clear()
        state.vertical.clear()


def set_font(path: str):
    state = getattr(_thread_state, 'value', None) or FontState()
    _thread_state.value = state
    resolved = _resolve_existing_font_path(path) or _resolve_existing_font_path(DEFAULT_FONT)
    if not resolved:
        state.font = ''
        state.font_selection = []
        _clear_shape_caches(state)
        return
    try:
        _raw_font(resolved, _QT_FONT_PROBE_SIZE)
        state.font = resolved
    except Exception:
        logger.error(f'Could not load font: {resolved}')
        state.font = _resolve_existing_font_path(DEFAULT_FONT)
    _refresh_font_selection(state)


@contextmanager
def _style_font_scope(style: TextStyle):
    font_path = getattr(style, 'font_path', None)
    if not font_path:
        yield
        return

    state = _state()
    previous_font = state.font
    set_font(font_path)
    try:
        yield
    finally:
        set_font(previous_font or DEFAULT_FONT)


def _layout_font_descriptor(state: FontState) -> Tuple[Tuple[str, ...], str]:
    families, seen = [], set()
    primary_style = ''
    selection = state.font_selection or [state.font or DEFAULT_FONT]
    for index, path in enumerate(selection):
        try:
            descriptor = _font_descriptor(path)
        except Exception as exc:
            logger.error(f'Failed to resolve layout font family: {path} - {exc}')
            continue
        if index == 0 and descriptor.style:
            primary_style = descriptor.style
        if descriptor.family and descriptor.family not in seen:
            seen.add(descriptor.family)
            families.append(descriptor.family)

    if families:
        return tuple(families), primary_style

    fallback_path = _resolve_existing_font_path(DEFAULT_FONT)
    fallback_descriptor = _font_descriptor(fallback_path)
    return (fallback_descriptor.family,), fallback_descriptor.style


def _layout_font(font_size: int, letter_spacing: float) -> QFont:
    state = _state()
    families, primary_style = _layout_font_descriptor(state)
    font_paths = tuple(
        _normalize_font_path(path)
        for path in (state.font_selection or [state.font or DEFAULT_FONT])
    )
    key = (font_paths, primary_style, int(max(font_size, 1)), round(float(letter_spacing), 4))
    qfont = _cache_get(state.qfonts, key)
    if qfont is None:
        qfont = QFont()
        qfont.setFamilies(list(families))
        if primary_style:
            qfont.setStyleName(primary_style)
        qfont.setPixelSize(key[2])
        qfont.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        qfont.setStyleStrategy(QFont.StyleStrategy.PreferOutline)
        qfont.setKerning(True)
        qfont.setLetterSpacing(QFont.SpacingType.PercentageSpacing, float(letter_spacing) * 100.0)
        _cache_put(state.qfonts, key, qfont, _QFONT_CACHE_MAX)
    return QFont(qfont)


def _create_text_layout(text: str, font_size: int, letter_spacing: float = 1.0):
    qfont = _layout_font(font_size, letter_spacing)
    if not text:
        return text, qfont, None, None
    layout = QTextLayout(text, qfont)
    layout.beginLayout()
    line = layout.createLine()
    if not line.isValid():
        layout.endLayout()
        return text, qfont, None, None
    line.setLineWidth(1_000_000.0)
    line.setPosition(QPointF(0.0, 0.0))
    layout.endLayout()
    return text, qfont, layout, line


def _font_supports_character(raw_font: QRawFont, cdpt: str) -> bool:
    try:
        return bool(raw_font.supportsCharacter(cdpt))
    except Exception:
        return True


def _glyph_has_advance(raw_font: QRawFont, glyph_id: int) -> bool:
    if not glyph_id:
        return False
    try:
        advances = raw_font.advancesForGlyphIndexes([glyph_id])
    except Exception:
        return False
    return bool(advances and (advances[0].x() or advances[0].y()))


def _glyph_renderable(raw_font: QRawFont, glyph_id: int, cdpt: str = '') -> bool:
    if not glyph_id:
        return False
    if cdpt.isspace() and _glyph_has_advance(raw_font, glyph_id):
        return True
    try:
        if not raw_font.pathForGlyph(glyph_id).isEmpty():
            return True
    except Exception:
        pass
    try:
        alpha = raw_font.alphaMapForGlyph(glyph_id)
        return not alpha.isNull() and alpha.width() > 0 and alpha.height() > 0
    except Exception:
        return False


def _raw_font_key(raw_font: QRawFont) -> Tuple[str, str, str]:
    try:
        family = raw_font.familyName() or ''
    except Exception:
        family = ''
    try:
        style = raw_font.styleName() or ''
    except Exception:
        style = ''
    try:
        weight = raw_font.weight()
        weight = getattr(weight, 'value', weight)
        weight = str(int(weight))
    except Exception:
        weight = ''
    return family, style, weight


def _glyph_spec_via_layout(cdpt: str, font_size: int) -> Optional[GlyphSpec]:
    _, _, layout, _ = _create_text_layout(cdpt, font_size, 1.0)
    if layout is None:
        return None
    whitespace = None
    for run in layout.glyphRuns():
        raw_font = run.rawFont()
        for glyph_id in run.glyphIndexes():
            if _glyph_renderable(raw_font, glyph_id, cdpt):
                return GlyphSpec(raw_font, int(glyph_id), ('qt-layout',) + _raw_font_key(raw_font))
            if whitespace is None and cdpt.isspace() and _glyph_has_advance(raw_font, glyph_id):
                whitespace = GlyphSpec(raw_font, int(glyph_id), ('qt-layout',) + _raw_font_key(raw_font))
    return whitespace


def _glyph_spec_from_selection(cdpt: str, font_size: int) -> Optional[GlyphSpec]:
    state = _state()
    for path in state.font_selection:
        raw_font = _raw_font(path, font_size)
        # Avoid raw_font.supportsCharacter() because it freezes on some fonts
        glyphs = raw_font.glyphIndexesForString(cdpt)
        glyph_id = glyphs[0] if glyphs else 0
        # glyph_id > 0 足以判断字体支持该字符，跳过 _glyph_renderable 避免
        # 对复杂字形调 pathForGlyph 导致首次渲染极慢
        if glyph_id > 0:
            return GlyphSpec(raw_font, int(glyph_id), ('font-path', _normalize_font_path(path)))
        # Space character might legitimately have glyph_id == 0 or map to advance
        if glyph_id == 0 and cdpt.isspace() and _glyph_has_advance(raw_font, glyph_id):
            return GlyphSpec(raw_font, int(glyph_id), ('font-path', _normalize_font_path(path)))
    return None


def _glyph_spec(cdpt: str, font_size: int) -> GlyphSpec:
    state = _state()
    # 缓存 key 含当前主字体路径，避免切换字体后命中旧缓存
    font_key = state.font_selection[0] if state.font_selection else ''
    key = (cdpt, int(font_size), font_key)
    cached = _cache_get(state.glyph_specs, key)
    if cached is not None:
        return cached
    # 优先使用 font_selection 手动查找，修复 Qt layout 找不到某些字体的问题
    spec = _glyph_spec_from_selection(cdpt, font_size) or _glyph_spec_via_layout(cdpt, font_size)
    if spec is None:
        if cdpt in (' ', '?', '□'):
            raise RuntimeError(f"Character '{cdpt}' not found in any font.")
        for placeholder in ('?', '□', ' '):
            if placeholder != cdpt:
                try:
                    spec = _glyph_spec(placeholder, font_size)
                    break
                except RuntimeError:
                    continue
    if spec is None:
        raise RuntimeError('No placeholder character found in any font.')
    return _cache_put(state.glyph_specs, key, spec, _GLYPH_SPEC_CACHE_MAX)


def _qimage_alpha_to_array(image: QImage) -> np.ndarray:
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((image.height(), image.bytesPerLine() // 4, 4))
    return arr[:, :image.width(), 3].copy()


def _rasterize_path(path: QPainterPath) -> Tuple[np.ndarray, int, int]:
    if path.isEmpty():
        return np.zeros((0, 0), dtype=np.uint8), 0, 0
    rect = path.boundingRect()
    left, top = math.floor(rect.left()), math.floor(rect.top())
    width = max(0, math.ceil(rect.right()) - left)
    height = max(0, math.ceil(rect.bottom()) - top)
    if width <= 0 or height <= 0:
        return np.zeros((0, 0), dtype=np.uint8), left, top
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 255))
    painter.translate(-left, -top)
    painter.drawPath(path)
    painter.end()
    return _qimage_alpha_to_array(image), left, top


def _stroke_path(path: QPainterPath, stroke_px: int) -> QPainterPath:
    if path.isEmpty() or stroke_px <= 0:
        return QPainterPath()
    stroker = QPainterPathStroker()
    stroker.setWidth(float(stroke_px * 2))
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    # 不要使用 subtracted(path)，否则会导致内部中空，在抗锯齿边缘产生脏边
    return stroker.createStroke(path).united(path)


def _glyph_raster(cdpt: str, font_size: int) -> GlyphRaster:
    spec = _glyph_spec(cdpt, font_size)
    state = _state()
    key = (spec.cache_key, spec.glyph_id, int(font_size))
    cached = _cache_get(state.glyphs, key)
    if cached is not None:
        return cached
    path = spec.raw_font.pathForGlyph(spec.glyph_id)
    alpha, left, top = _rasterize_path(path)
    advances = spec.raw_font.advancesForGlyphIndexes([spec.glyph_id]) if spec.glyph_id else []
    advance = advances[0] if advances else QPointF(float(font_size), float(font_size))
    metrics = path.boundingRect()
    advance_x = int(round(advance.x())) if advance.x() else max(int(round(metrics.width())), font_size)
    advance_y = int(round(advance.y())) if advance.y() else max(int(round(metrics.height())), font_size)
    raster = GlyphRaster(alpha, int(left), int(-top), int(advance_x), int(advance_y), int(top), max(int(round(metrics.width())), int(advance_x), 1))
    return _cache_put(state.glyphs, key, raster, _GLYPH_RASTER_CACHE_MAX)


def _glyph_stroke_alpha(cdpt: str, font_size: int, stroke_ratio: float) -> np.ndarray:
    spec = _glyph_spec(cdpt, font_size)
    state = _state()
    key = (spec.cache_key, spec.glyph_id, int(font_size), round(float(stroke_ratio), 4))
    cached = _cache_get(state.strokes, key)
    if cached is not None:
        return cached
    raster = _glyph_raster(cdpt, font_size)
    if raster.alpha.size == 0:
        return _cache_put(state.strokes, key, np.zeros((0, 0), dtype=np.uint8), _STROKE_CACHE_MAX)
    stroke_px = max(int(stroke_ratio * font_size), 1)
    full_alpha, _, _ = _stroke_alpha_from_text_alpha(raster.alpha, stroke_px)
    nz = cv2.findNonZero(full_alpha)
    if nz is None:
        return _cache_put(state.strokes, key, np.zeros((0, 0), dtype=np.uint8), _STROKE_CACHE_MAX)
    x, y, w, h = cv2.boundingRect(nz)
    result = full_alpha[y:y + h, x:x + w]
    return _cache_put(state.strokes, key, result, _STROKE_CACHE_MAX)


def _paste_bitmap(canvas: np.ndarray, bitmap_arr: np.ndarray, x: int, y: int, mode: str = 'max'):
    if bitmap_arr is None or bitmap_arr.size == 0:
        return
    rows, width = bitmap_arr.shape
    x2, y2 = x + width, y + rows
    sx1, sy1, sx2, sy2 = max(0, x), max(0, y), min(canvas.shape[1], x2), min(canvas.shape[0], y2)
    if sx1 >= sx2 or sy1 >= sy2:
        return
    bx1, by1 = sx1 - x, sy1 - y
    bitmap = bitmap_arr[by1:by1 + (sy2 - sy1), bx1:bx1 + (sx2 - sx1)]
    target = canvas[sy1:sy2, sx1:sx2]
    if mode == 'add':
        # 使用 cv2.add 避免 numpy uint8 加法溢出导致的脏斑点
        cv2.add(target, bitmap, dst=target)
    else:
        np.maximum(target, bitmap, out=target)


def _paste_surface(canvas_text: np.ndarray, canvas_border: np.ndarray, surface: dict, x: int, y: int):
    _paste_bitmap(canvas_text, surface['text'], int(round(x)), int(round(y)))
    _paste_bitmap(canvas_border, surface['border'], int(round(x)), int(round(y)))


def _paste_glyph_pair(
    canvas_text: np.ndarray,
    canvas_border: np.ndarray,
    bitmap_char: np.ndarray,
    draw_x: int,
    draw_y: int,
    bitmap_border: Optional[np.ndarray] = None,
):
    _paste_bitmap(canvas_text, bitmap_char, draw_x, draw_y, mode='max')
    if bitmap_border is None or bitmap_border.size == 0:
        return
    border_x = draw_x - round((bitmap_border.shape[1] - bitmap_char.shape[1]) / 2.0)
    border_y = draw_y - round((bitmap_border.shape[0] - bitmap_char.shape[0]) / 2.0)
    _paste_bitmap(canvas_border, bitmap_border, border_x, border_y, mode='add')


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


def resolve_horizontal_line_spacing_multiplier(line_spacing: float) -> float:
    return _normalize_line_spacing(line_spacing)

def calc_horizontal_line_spacing_px(font_size: int, line_spacing: float) -> int:
    value = _normalize_line_spacing(line_spacing)
    return int(font_size * (value - 1.0))


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


def _normalize_horizontal_measure_text(text: str) -> str:
    return ''.join(c if c in '\r\n' else CJK_Compatibility_Forms_translate(c, 0)[0] for c in (text or ''))


def _horizontal_line(text: str, font_size: int, letter_spacing: float = 1.0):
    return _create_text_layout(_normalize_horizontal_measure_text(text), font_size, letter_spacing)


def _line_logical_width(line, text_length: int) -> float:
    cursor_x = line.cursorToX(text_length)
    return float(cursor_x[0] if isinstance(cursor_x, tuple) else cursor_x)


def _sorted_glyph_positions(layout, reversed_direction: bool):
    positions = [pos for run in layout.glyphRuns() for pos in run.positions()]
    positions.sort(key=lambda p: p.x(), reverse=reversed_direction)
    return positions


def _horizontal_ellipsis_tracking_offsets(
    text: str,
    font_size: int,
    letter_spacing: float,
    positions: list,
    reversed_direction: bool = False,
) -> list:
    if reversed_direction or _normalize_letter_spacing(letter_spacing) == 1.0 or '……' not in (text or ''):
        return [0.0] * len(positions)
    _, _, base_layout, _ = _horizontal_line(text, font_size, 1.0)
    if base_layout is None:
        return [0.0] * len(positions)
    base_positions = _sorted_glyph_positions(base_layout, False)
    limit = min(len(text), len(positions), len(base_positions))
    offsets = [0.0] * len(positions)
    idx = 0
    while idx < limit:
        if text[idx] != '…':
            idx += 1
            continue
        run_start = idx
        while idx < limit and text[idx] == '…':
            idx += 1
        if idx - run_start < 2:
            continue
        start_spaced_x = positions[run_start].x()
        start_base_x = base_positions[run_start].x()
        for run_idx in range(run_start + 1, idx):
            spaced_delta = positions[run_idx].x() - start_spaced_x
            base_delta = base_positions[run_idx].x() - start_base_x
            offsets[run_idx] = spaced_delta - base_delta
    return offsets


def _line_metrics(text: str, font_size: int, letter_spacing: float = 1.0) -> dict:
    normalized, qfont, _, line = _horizontal_line(text, font_size, letter_spacing)
    metrics = QFontMetricsF(qfont)
    if line is None:
        return {'text': normalized, 'logical_width': 0.0, 'ascent': float(metrics.ascent()), 'height': float(metrics.height()), 'descent': float(metrics.descent())}
    return {'text': normalized, 'logical_width': _line_logical_width(line, len(normalized)), 'ascent': float(line.ascent()), 'height': float(line.height()), 'descent': float(line.descent())}


def _crop_pair(text_canvas: np.ndarray, border_canvas: np.ndarray):
    combined = cv2.add(text_canvas, border_canvas)
    if not np.any(combined):
        return None
    x, y, w, h = cv2.boundingRect(combined)
    return None if w == 0 or h == 0 else (text_canvas[y:y+h, x:x+w], border_canvas[y:y+h, x:x+w], x, y, w, h)


def _get_fallback_glyph(glyph_id: int, run_font: QRawFont, char: str, font_size: int) -> Tuple[QRawFont, int]:
    if glyph_id > 0 or not char:
        return run_font, glyph_id
    try:
        spec = _glyph_spec(char, font_size)
        return spec.raw_font, spec.glyph_id
    except Exception:
        return run_font, glyph_id


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
    stage_t0 = perf_counter() if profile_stats is not None else None
    normalized, _, layout, line = _horizontal_line(line_text, font_size, letter_spacing)
    _profile_add(profile_stats, "tr_layout_ms", stage_t0)
    if not line_text or line is None:
        return None
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    # Qt/DirectWrite 把 family name 以 '[' 开头的字体归入同一字体集合，
    # 导致 shaping 时 character-to-glyph 映射返回错误的 glyph_id。
    # 修复：用 _glyph_spec 查字形路径（走 font_selection 直接加载的 QRawFont，
    # 绕开 Qt 字体数据库），位置（pos）仍从 QTextLayout 取。
    # glyphRuns() 返回的 run 顺序不保证与字符串字符顺序一致（混合脚本时
    # 如 CJK + ASCII 会分成多个 run，run 顺序不定），按 x 坐标排序以
    # 确保位置与字符的逻辑顺序匹配
    stage_t0 = perf_counter() if profile_stats is not None else None
    all_positions = _sorted_glyph_positions(layout, reversed_direction)
    position_offsets = _horizontal_ellipsis_tracking_offsets(
        normalized,
        font_size,
        letter_spacing,
        all_positions,
        reversed_direction,
    )
    for idx, char in enumerate(normalized):
        if idx >= len(all_positions):
            break
        pos = all_positions[idx]
        try:
            spec = _glyph_spec(char, font_size)
        except Exception:
            continue
        glyph_path = spec.raw_font.pathForGlyph(spec.glyph_id)
        if not glyph_path.isEmpty():
            offset_x = position_offsets[idx] if idx < len(position_offsets) else 0.0
            glyph_path.translate(pos.x() - offset_x, pos.y())
            path.addPath(glyph_path)
    _profile_add(profile_stats, "tr_path_ms", stage_t0)
                
    if path.isEmpty():
        return None
    stage_t0 = perf_counter() if profile_stats is not None else None
    fill_alpha, fill_left, fill_top = _rasterize_path(path)
    _profile_add(profile_stats, "tr_raster_ms", stage_t0)
    if fill_alpha.size == 0:
        return None
    if bold:
        fill_alpha, bold_dx, bold_dy = _embolden_alpha(fill_alpha, font_size)
        fill_left += bold_dx
        fill_top += bold_dy
    if border_size > 0:
        stage_t0 = perf_counter() if profile_stats is not None else None
        stroke_px = max(int(stroke_ratio * font_size), 1)
        border_alpha, border_dx, border_dy = _stroke_alpha_from_text_alpha(fill_alpha, stroke_px)
        border_left, border_top = fill_left + border_dx, fill_top + border_dy
        left = min(fill_left, border_left)
        top = min(fill_top, border_top)
        right = max(fill_left + fill_alpha.shape[1], border_left + border_alpha.shape[1])
        bottom = max(fill_top + fill_alpha.shape[0], border_top + border_alpha.shape[0])
        text_canvas = np.zeros((bottom - top, right - left), dtype=np.uint8)
        border_canvas = np.zeros((bottom - top, right - left), dtype=np.uint8)
        _paste_bitmap(text_canvas, fill_alpha, fill_left - left, fill_top - top)
        _paste_bitmap(border_canvas, border_alpha, border_left - left, border_top - top)
        _profile_add(profile_stats, "tr_stroke_ms", stage_t0)
    else:
        left, top = fill_left, fill_top
        text_canvas, border_canvas = fill_alpha, np.zeros_like(fill_alpha)
    stage_t0 = perf_counter() if profile_stats is not None else None
    cropped = _crop_pair(text_canvas, border_canvas)
    if cropped is None:
        return None
    text_bitmap, border_bitmap, x, y, w, h = cropped
    logical_width = _line_logical_width(line, len(normalized))
    origin_x = -logical_width if reversed_direction else 0.0
    ascent, height = float(line.ascent()), float(line.height())
    result = {
        'text': text_bitmap, 'border': border_bitmap, 'left_rel': left + x - origin_x,
        'right_rel': left + x - origin_x + w, 'top_rel': top + y - ascent, 'width': w, 'height': h,
        'logical_width': logical_width,
        'line_ascent': ascent, 'line_descent': float(line.descent()), 'line_height': height,
        'ink_top': float(top + y), 'ink_bottom': float(top + y + h),
    }
    _profile_add(profile_stats, "tr_crop_ms", stage_t0)
    return result


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
        'height': float(surface.get('line_height', font_size)),
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
    spacing_y = calc_horizontal_line_spacing_px(font_size, line_spacing)
    layouts = []
    max_logical_width = 0.0
    max_font_size = font_size

    for paragraph in document.paragraphs:
        runs = [
            run for span in paragraph.spans
            for run in [_rich_span_surface(span, font_size, stroke_ratio, bg, reversed_direction, letter_spacing, profile_stats)]
            if run is not None
        ]
        if not runs:
            metrics = _line_metrics('', font_size, letter_spacing)
            layouts.append({'runs': [], 'logical_width': 0.0, 'ascent': metrics['ascent'], 'descent': metrics['descent'], 'ruby_extra': 0.0, 'dot_extra': 0.0, 'height': metrics['height']})
            continue
        ascent = max(run['ascent'] for run in runs)
        descent = max(run['descent'] for run in runs)
        logical_width = sum(run['logical_width'] for run in runs)
        max_logical_width = max(max_logical_width, logical_width)
        max_font_size = max(max_font_size, *(run['font_size'] for run in runs))
        ruby_extra = 0.0
        dot_extra = 0.0
        for run in runs:
            if run['span'].ruby:
                ruby = _rich_ruby_surface(run['span'], run['font_size'], fg, bg, letter_spacing, profile_stats)
                run['ruby'] = ruby
                if ruby:
                    ruby_extra = max(ruby_extra, ruby['layer'].shape[0] + max(1, run['font_size'] * 0.08))
            if run['span'].style.emphasis:
                dot_extra = max(dot_extra, run['font_size'] * 0.25)
        line_height = ruby_extra + ascent + descent + dot_extra
        layouts.append({'runs': runs, 'logical_width': logical_width, 'ascent': ascent, 'descent': descent, 'ruby_extra': ruby_extra, 'dot_extra': dot_extra, 'height': line_height})

    padding = int(max(max_font_size * 2.0, 16))
    canvas_w = int(math.ceil(max(max_logical_width, float(width), 1.0) + padding * 2))
    content_h = sum(layout['height'] for layout in layouts) + spacing_y * max(0, len(layouts) - 1)
    canvas_h = int(math.ceil(max(content_h, float(height), 1.0) + padding * 2))
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    y = float(padding)

    for layout in layouts:
        line_width = layout['logical_width']
        if reversed_direction:
            line_left = padding + max_logical_width - line_width if alignment == 'right' else padding
            if alignment == 'center':
                line_left = padding + (max_logical_width - line_width) / 2.0
        else:
            line_left = padding if alignment == 'left' else padding + (max_logical_width - line_width) / 2.0 if alignment == 'center' else padding + max_logical_width - line_width
        baseline_y = y + layout['ruby_extra'] + layout['ascent']
        cursor_x = line_left
        for run in layout['runs']:
            surface = run['surface']
            span = run['span']
            layer, layer_dx, layer_dy = _rich_colorized_surface(run, fg, bg)
            if layer is not None:
                draw_x = cursor_x + surface['left_rel'] + span.style.transform.offset_x + layer_dx
                draw_y = baseline_y + surface['top_rel'] + span.style.transform.offset_y + layer_dy
                _paste_rgba(canvas, layer, int(round(draw_x)), int(round(draw_y)))
            ruby = run.get('ruby')
            if ruby:
                gap = max(1, int(round(run['font_size'] * 0.08)))
                ruby_x = cursor_x + run['logical_width'] / 2.0 - ruby['layer'].shape[1] / 2.0
                main_top = baseline_y - run['ascent']
                ruby_y = main_top - gap - ruby['layer'].shape[0]
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

    return _crop_rgba(canvas)


def _glyph_pair_rgba(char_bitmap: np.ndarray, border_bitmap: Optional[np.ndarray], fill, stroke):
    if char_bitmap is None or char_bitmap.size == 0:
        return None
    if border_bitmap is None or border_bitmap.size == 0 or stroke is None:
        return _rgba_from_alpha_pair(char_bitmap, None, fill, None), 0, 0
    border_x = -round((border_bitmap.shape[1] - char_bitmap.shape[1]) / 2.0)
    border_y = -round((border_bitmap.shape[0] - char_bitmap.shape[0]) / 2.0)
    left = min(0, border_x)
    top = min(0, border_y)
    right = max(char_bitmap.shape[1], border_x + border_bitmap.shape[1])
    bottom = max(char_bitmap.shape[0], border_y + border_bitmap.shape[0])
    text_alpha = np.zeros((bottom - top, right - left), dtype=np.uint8)
    border_alpha = np.zeros_like(text_alpha)
    _paste_bitmap(text_alpha, char_bitmap, -left, -top)
    _paste_bitmap(border_alpha, border_bitmap, border_x - left, border_y - top, mode='add')
    return _rgba_from_alpha_pair(text_alpha, border_alpha, fill, stroke), left, top


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
    if item['span'].style.bold:
        bitmap, bitmap_dx, bitmap_dy = _embolden_alpha(bitmap, item['font_size'])
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
    ink_w = float(base['ink_w'])
    ink_x = float(base['ink_x'])
    body_right = body_left + thickness
    body_center = body_left + thickness / 2.0
    char_x = body_center - ink_w / 2.0 - ink_x
    padding_small = max(1, int(round(item['font_size'] * 0.05)))
    if base['translated'] in _VERTICAL_ALIGN_TOP_RIGHT:
        char_x = body_right - ink_w - ink_x - padding_small
    elif base['translated'] in _VERTICAL_ALIGN_BOTTOM_LEFT:
        char_x = body_left - ink_x + padding_small
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
            # F23：字体作用域提升到 span 层，避免带 fontPath 的 span 逐字符
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
        layouts.append({
            'width': int(thickness),
            'body_width': int(thickness),
            'thickness': int(thickness),
            'paint_left_extra': int(paint_left_extra),
            'paint_right_extra': int(paint_right_extra),
            'ruby_extra': int(ruby_extra),
            'dot_extra': int(dot_extra),
            'height': max(0, int(cursor)),
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
    layouts = _build_rich_vertical_layout(document, font_size, stroke_ratio, fg, bg, letter_spacing, profile_stats)
    geometry = _rich_vertical_layout_geometry(layouts, font_size, line_spacing)
    max_height = max((layout['height'] for layout in layouts), default=0)
    padding = int(max(font_size * 2.0, 16))
    canvas = np.zeros((int(max(max_height, h, 1)) + padding * 2, int(max(geometry['paint_width'], 1)) + padding * 2, 4), dtype=np.uint8)
    content_left = padding
    content_right = padding + geometry['paint_width']
    columns = _rich_vertical_column_positions(layouts, geometry, padding)

    for idx, layout in enumerate(layouts):
        body_left, body_right, _ = columns[idx]
        thickness = float(layout['thickness'])
        line_origin_y = _vertical_line_origin_y(float(padding), alignment, max_height, layout['height'])
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

    return _crop_rgba_preserve_x(canvas, content_left, content_right)


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

    正文框 = 渲染框去掉框外装饰（横排的首行注音/末行着重号、竖排的首列
    注音与字形左右溢出）后，纯文本本体占据的区域。body_center 是正文框
    中心在渲染框内的坐标（相对渲染框左上角）；无框外装饰时恒为渲染框正中心。
    """
    document = ensure_rich_text_document(text)
    base_font = max(1, int(font_size))
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)

    if is_horizontal:
        spacing_y = calc_horizontal_line_spacing_px(base_font, line_spacing)
        widths = []
        heights = []
        ruby_extras = []
        dot_extras = []
        for paragraph in document.paragraphs:
            line_width = 0.0
            line_ascent = float(base_font)
            line_descent = 0.0
            ruby_extra = 0.0
            dot_extra = 0.0
            for span in paragraph.spans:
                span_font = _style_font_size(base_font, span.style)
                # F21：度量不做光栅化。_line_metrics 与渲染路径（_rich_span_surface
                # → _line_surface）读取的是同一条 QTextLayout line，logical_width/
                # ascent/descent 三个值逐位一致；bold/描边只影响位图不影响这三项。
                if span.text.strip():
                    with _style_font_scope(span.style):
                        metrics = _line_metrics(span.text, span_font, letter_spacing)
                    line_width += float(metrics['logical_width'])
                    line_ascent = max(line_ascent, float(metrics['ascent']))
                    line_descent = max(line_descent, float(metrics['descent']))
                elif span.text:
                    # 空白 span 渲染时产不出 surface（无墨迹），与渲染路径一致：
                    # 只计宽度，不参与行高
                    with _style_font_scope(span.style):
                        line_width += _measure_horizontal_text_width(span.text, span_font, letter_spacing)
                if span.ruby:
                    ruby_extra = max(ruby_extra, span_font * 0.50)
                if span.style.emphasis:
                    dot_extra = max(dot_extra, span_font * 0.25)
            widths.append(int(math.ceil(line_width)))
            heights.append(int(math.ceil(ruby_extra + line_ascent + line_descent + dot_extra)))
            ruby_extras.append(ruby_extra)
            dot_extras.append(dot_extra)
        n_lines = max(1, len(document.paragraphs))
        width = max(widths, default=0)
        height = sum(heights) + spacing_y * max(0, n_lines - 1)
        # 首行注音占据渲染框顶部、末行着重号占据底部，都在正文区间之外。
        body_top = ruby_extras[0] if ruby_extras else 0.0
        body_bottom = float(height) - (dot_extras[-1] if dot_extras else 0.0)
        body_center_y = (body_top + body_bottom) / 2.0 if height > 0 else 0.0
        return {
            'width': int(width),
            'height': int(height),
            'n_lines': n_lines,
            'body_center': (float(width) / 2.0, float(body_center_y)),
        }

    layouts = _build_rich_vertical_layout(
        document, base_font, stroke_ratio, (0, 0, 0), None, letter_spacing, measure_only=True
    )
    if not layouts:
        return {'width': 0, 'height': 0, 'n_lines': 0, 'body_center': (0.0, 0.0)}
    geometry = _rich_vertical_layout_geometry(layouts, base_font, line_spacing)
    height = max((int(layout['height']) for layout in layouts), default=0)
    return {
        'width': int(geometry['paint_width']),
        'height': int(height),
        'n_lines': len(layouts),
        'body_center': (float(geometry['body_center_x']), float(height) / 2.0),
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


def _bitmap_ink_rect(bitmap: Optional[np.ndarray]) -> Optional[Tuple[int, int, int, int]]:
    if bitmap is None or bitmap.size == 0:
        return None
    nz = cv2.findNonZero(bitmap)
    return None if nz is None else tuple(map(int, cv2.boundingRect(nz)))


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
    key = (int(font_size), cdpt, round(_normalize_letter_spacing(letter_spacing), 4))
    cached = _cache_get(state.vertical, key)
    if cached is not None:
        return cached
    translated, rot = CJK_Compatibility_Forms_translate(cdpt, 1)
    if translated == ' ':
        base = {
            'translated': translated, 'rot_degree': 0, 'bitmap': None,
            'advance_y': _vertical_space_advance(font_size, letter_spacing),
            'ink_x': 0.0, 'ink_w': 0.0, 'y': 0,
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
        'frame_width': int(frame_width),
    }
    return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)


def get_vertical_char_bitmap_width(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> int:
    return int(_vertical_base(font_size, cdpt, letter_spacing)['frame_width'])


def _measure_horizontal_text_width(text: str, font_size: int, letter_spacing: float = 1.0) -> int:
    normalized = _normalize_horizontal_measure_text(text)
    if not normalized:
        return 0
    if '\n' in normalized or '\r' in normalized:
        return max((_measure_horizontal_text_width(part, font_size, letter_spacing) for part in normalized.splitlines()), default=0)
    state = _state()
    key = ('logical-width', tuple(state.font_selection), int(font_size), round(_normalize_letter_spacing(letter_spacing), 4), normalized)
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
    text = normalize_vertical_ellipsis_text(compact_special_symbols(text))
    total = 0
    for char in re.sub(r'\s*(?:\[BR\]|<br>|【BR】)\s*', '', text or '', flags=re.IGNORECASE):
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


def _vertical_border_bitmap(translated: str, font_size: int, stroke_ratio: float, rot_degree: int):
    bitmap = _glyph_stroke_alpha(translated, font_size, stroke_ratio)
    if bitmap.size == 0:
        return None
    return cv2.rotate(bitmap, cv2.ROTATE_90_CLOCKWISE) if rot_degree == 90 else bitmap


def _stroke_bitmap_from_alpha(text_alpha: np.ndarray, font_size: int, stroke_ratio: float):
    stroke_px = max(int(stroke_ratio * font_size), 1)
    bitmap, _, _ = _stroke_alpha_from_text_alpha(text_alpha, stroke_px)
    return None if bitmap.size == 0 else bitmap


def _build_vertical_layout(
    font_size: int,
    line_text: str,
    border_size: int,
    stroke_ratio: float,
    letter_spacing: float,
    profile_stats: Optional[dict] = None,
) -> dict:
    line_width, items = font_size, []
    for char in line_text:
        if char == '＿':
            items.append(('placeholder', _scale_advance(font_size, letter_spacing)))
            continue
        base = _vertical_base(font_size, char, letter_spacing)
        line_width = max(line_width, int(base['frame_width']))
        items.append(('char', base))
    cursor, laid = 0, []
    for kind, value in items:
        if kind == 'placeholder':
            laid.append({'kind': kind, 'advance_y': int(value), 'cursor_y': cursor})
            cursor += int(value)
        else:
            char_t = value['translated']
            ink_w = value['ink_w']
            ink_x = value['ink_x']

            x = round((line_width - ink_w) / 2.0) - ink_x

            padding = max(1, int(round(font_size * 0.05)))
            if char_t in _VERTICAL_ALIGN_TOP_RIGHT:
                x = line_width - ink_w - ink_x - padding
            elif char_t in _VERTICAL_ALIGN_BOTTOM_LEFT:
                x = -ink_x + padding

            laid.append({
                'kind': kind, 'translated': char_t, 'rot_degree': value['rot_degree'], 'bitmap': value['bitmap'],
                'cursor_y': cursor, 'x': int(round(x)), 'y': int(value['y']),
            })
            cursor += int(value['advance_y'])
    return {'width': int(line_width), 'height': max(0, int(cursor)), 'items': laid}


def put_char_horizontal(font_size: int, cdpt: str, pen_l: Tuple[int, int], canvas_text: np.ndarray, canvas_border: np.ndarray, border_size: int, config=None, stroke_width: float = None, letter_spacing: float = 1.0):
    char = '　' if cdpt == '＿' else CJK_Compatibility_Forms_translate(cdpt, 0)[0]
    char_offset_x = get_char_offset_x(font_size, char, letter_spacing)
    surface = _line_surface(char, font_size, border_size, _resolve_stroke_ratio(config, stroke_width), False, letter_spacing, False)
    if surface is not None:
        _paste_surface(canvas_text, canvas_border, surface, pen_l[0] + surface['left_rel'], pen_l[1] + surface['top_rel'])
    return char_offset_x


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
    if is_rich_text_document(text):
        # F24：入口解析一次，向下传实例（内部 ensure 对实例是短路）
        document = ensure_rich_text_document(text)
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
    text = compact_special_symbols(text, convert_ascii_ellipsis=False)
    if not text:
        return None
    _ = (width, height, lang, hyphenate, region_count)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    bg_size = int(max(font_size * stroke_ratio, 1)) if bg is not None else 0
    spacing_y = calc_horizontal_line_spacing_px(font_size, line_spacing)
    line_texts = normalize_rich_linebreaks(text).split('\n')
    surfaces, metrics, tops, extents, logical_widths = [], [], [], [], []
    logical_y = min_ink_top = max_ink_bottom = 0.0
    for idx, line_text in enumerate(line_texts):
        surface = _line_surface(line_text, font_size, bg_size, stroke_ratio, reversed_direction, letter_spacing, False, profile_stats)
        frame = {'ascent': surface['line_ascent'], 'height': surface['line_height'], 'descent': surface['line_descent']} if surface else _line_metrics(line_text, font_size, letter_spacing)
        surfaces.append(surface)
        metrics.append(frame)
        tops.append(logical_y)
        left, right = (surface['left_rel'], surface['right_rel']) if surface else (0.0, 0.0)
        logical_widths.append(float(surface['logical_width']) if surface else float(frame.get('logical_width', 0.0)))
        if surface:
            min_ink_top = min(min_ink_top, logical_y + surface['ink_top'])
            max_ink_bottom = max(max_ink_bottom, logical_y + surface['ink_bottom'])
        extents.append((left, right))
        logical_y += frame['height'] + (spacing_y if idx < len(line_texts) - 1 else 0)
    max_visual_width = max((max(0.0, right - left) for left, right in extents), default=0.0)
    canvas_w = int(math.ceil(max(max_visual_width, max(logical_widths, default=0.0)) + (font_size + bg_size) * 2))
    canvas_h = int(math.ceil(logical_y + max(0.0, -min_ink_top) + max(0.0, max_ink_bottom - logical_y) + bg_size * 2))
    canvas_text = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    canvas_border = np.zeros_like(canvas_text)
    base_x = canvas_w - bg_size - 10 if reversed_direction else font_size + bg_size
    base_y = bg_size + max(0.0, -min_ink_top)
    stage_t0 = perf_counter() if profile_stats is not None else None
    for i, surface in enumerate(surfaces):
        if surface is None:
            continue
        left, right = extents[i]
        line_width = max(0.0, right - left)
        if reversed_direction:
            slot_right = base_x
            slot_left = slot_right - max_visual_width
            target_left = slot_left if alignment == 'left' else slot_left + round((max_visual_width - line_width) / 2.0) if alignment == 'center' else slot_right - line_width
            pen_x = round(target_left + line_width - right)
        else:
            slot_left = base_x
            target_left = slot_left if alignment == 'left' else slot_left + round((max_visual_width - line_width) / 2.0) if alignment == 'center' else slot_left + (max_visual_width - line_width)
            pen_x = round(target_left - left)
        baseline_y = base_y + tops[i] + metrics[i]['ascent']
        _paste_surface(canvas_text, canvas_border, surface, pen_x + surface['left_rel'], baseline_y + surface['top_rel'])
    _profile_add(profile_stats, "tr_paste_ms", stage_t0)
    stage_t0 = perf_counter() if profile_stats is not None else None
    result = _crop_and_color(canvas_text, canvas_border, fg, bg)
    _profile_add(profile_stats, "tr_color_ms", stage_t0)
    return result


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
    if is_rich_text_document(text):
        # F24：入口解析一次，向下传实例（内部 ensure 对实例是短路）
        document = ensure_rich_text_document(text)
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
    text = normalize_vertical_ellipsis_text(compact_special_symbols(text))
    if not text:
        return None
    _ = (h, region_count)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    bg_size = int(max(font_size * stroke_ratio, 1)) if bg is not None else 0

    spacing_x = calc_vertical_line_spacing_px(font_size, line_spacing)
    stage_t0 = perf_counter() if profile_stats is not None else None
    layouts = [
        _build_vertical_layout(font_size, line, bg_size, stroke_ratio, letter_spacing, profile_stats)
        for line in normalize_rich_linebreaks(text).split('\n')
    ]
    _profile_add(profile_stats, "tr_vertical_layout_ms", stage_t0)
    line_widths = [layout['width'] for layout in layouts]
    max_height = max((layout['height'] for layout in layouts), default=0)
    content_width = sum(line_widths) + spacing_x * max(0, len(line_widths) - 1)
    canvas_text = np.zeros((max_height + (font_size + bg_size) * 2, content_width + (font_size + bg_size) * 2), dtype=np.uint8)
    canvas_border = np.zeros_like(canvas_text)
    columns = _vertical_column_walk(
        line_widths,
        [spacing_x] * max(0, len(line_widths) - 1),
        font_size + bg_size + content_width,
    )
    stage_t0 = perf_counter() if profile_stats is not None else None
    for idx, layout in enumerate(layouts):
        line_start_x = int(round(columns[idx][0]))
        line_origin_y = _vertical_line_origin_y(font_size + bg_size, alignment, max_height, layout['height'])
        for item in layout['items']:
            if item['kind'] == 'char' and item['bitmap'] is not None:
                draw_x = line_start_x + int(item['x'])
                draw_y = line_origin_y + item['cursor_y'] + int(item['y'])
                sub_t0 = perf_counter() if profile_stats is not None else None
                border_bitmap = _vertical_border_bitmap(item['translated'], font_size, stroke_ratio, item['rot_degree']) if bg_size > 0 else None
                _profile_add(profile_stats, "tr_vborder_ms", sub_t0)
                sub_t0 = perf_counter() if profile_stats is not None else None
                _paste_glyph_pair(canvas_text, canvas_border, item['bitmap'], draw_x, draw_y, border_bitmap)
                _profile_add(profile_stats, "tr_vpaste_ms", sub_t0)
    _profile_add(profile_stats, "tr_paste_ms", stage_t0)
    stage_t0 = perf_counter() if profile_stats is not None else None
    result = _crop_and_color(canvas_text, canvas_border, fg, bg)
    _profile_add(profile_stats, "tr_color_ms", stage_t0)
    return result


def select_hyphenator(lang: str):
    lang = standardize_tag(lang or 'en_US')
    if lang not in HYPHENATOR_LANGUAGES:
        lang = next((avail for avail in reversed(HYPHENATOR_LANGUAGES) if avail.startswith(lang)), '')
    if not lang:
        return None
    if lang not in _hyphenator_cache:
        try:
            _hyphenator_cache[lang] = Hyphenator(lang)
        except Exception:
            _hyphenator_cache[lang] = None
    return _hyphenator_cache[lang]


def calc_horizontal(font_size: int, text: str, max_width: int, max_height: int, language: str = 'en_US', hyphenate: bool = True, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        return document.paragraphs, [
            _rich_paragraph_horizontal_width(font_size, paragraph, letter_spacing=letter_spacing)
            for paragraph in document.paragraphs
        ]
    from .auto_linebreak import _calc_horizontal_layout
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
    from .auto_linebreak import _calc_vertical_layout
    return _calc_vertical_layout(font_size, text, max_height, config, letter_spacing=letter_spacing)


def calc_vertical_metrics(font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0):
    if is_rich_text_document(text):
        document = ensure_rich_text_document(text)
        metrics = [
            _rich_paragraph_vertical_metrics(font_size, paragraph, letter_spacing=letter_spacing)
            for paragraph in document.paragraphs
        ]
        return document.paragraphs, [height for height, _ in metrics], [width for _, width in metrics]
    from .auto_linebreak import _layout_vertical_metrics
    return _layout_vertical_metrics(font_size, text, max_height, config, letter_spacing=letter_spacing)
