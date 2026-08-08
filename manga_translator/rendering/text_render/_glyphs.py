"""字形层：character → GlyphSpec（字体+glyph id）→ GlyphRaster（alpha 位图与度量）。"""
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QRawFont, QTransform

from ._fonts import _create_text_layout, _state
from ._shared import _GLYPH_RASTER_CACHE_MAX, _GLYPH_SPEC_CACHE_MAX, _cache_get, _cache_put

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


def _glyph_spec(cdpt: str, font_size: int) -> GlyphSpec:
    state = _state()
    # 缓存 key 含当前字体家族和样式，避免切换字体后命中旧缓存
    key = (cdpt, int(font_size), state.font_family, state.font_style, bool(state.bold))
    cached = _cache_get(state.glyph_specs, key)
    if cached is not None:
        return cached
    spec = _glyph_spec_via_layout(cdpt, font_size)
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


def _glyph_raster(cdpt: str, font_size: int, shear: float = 0.0) -> GlyphRaster:
    """字形 alpha 位图与度量；shear 为斜体切变系数（PS 语义）。

    pathForGlyph 的轮廓坐标以基线为原点，此处对轮廓做 x' = x + shear·y
    即天然绕基线剪切：advance 不变，left/top 随剪切后包围盒自动成立，
    下游（描边/特效/布局）把斜体字形当普通字形处理。
    """
    spec = _glyph_spec(cdpt, font_size)
    state = _state()
    key = (spec.cache_key, spec.glyph_id, int(font_size), round(float(shear), 4))
    cached = _cache_get(state.glyphs, key)
    if cached is not None:
        return cached
    path = spec.raw_font.pathForGlyph(spec.glyph_id)
    if shear:
        path = QTransform(1.0, 0.0, float(shear), 1.0, 0.0, 0.0).map(path)
    alpha, left, top = _rasterize_path(path)
    advances = spec.raw_font.advancesForGlyphIndexes([spec.glyph_id]) if spec.glyph_id else []
    advance = advances[0] if advances else QPointF(float(font_size), float(font_size))
    metrics = path.boundingRect()
    advance_x = int(round(advance.x())) if advance.x() else max(int(round(metrics.width())), font_size)
    advance_y = int(round(advance.y())) if advance.y() else max(int(round(metrics.height())), font_size)
    raster = GlyphRaster(alpha, int(left), int(-top), int(advance_x), int(advance_y), int(top), max(int(round(metrics.width())), int(advance_x), 1))
    return _cache_put(state.glyphs, key, raster, _GLYPH_RASTER_CACHE_MAX)
