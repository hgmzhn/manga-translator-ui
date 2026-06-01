import logging
import math
import os
import re
import threading
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

from ..utils import BASE_PATH

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
_H_BLOCK_RE = re.compile(r'(<H>.*?</H>)', re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r'\s*(?:\[BR\]|<br\s*/?>|【BR】|\r\n|\r|\n)\s*', re.IGNORECASE)
_HORIZONTAL_SYMBOL_HALFWIDTH_MAP = str.maketrans({'！': '!', '？': '?'})
_VERTICAL_OPEN_BRACKETS = {'「', '『', '（', '《', '〈', '【', '〔', '［', '｛', '(', '“', '‘', '﹁', '﹃', '︵', '︷', '︹', '︻', '︽', '︿', '﹇'}
_VERTICAL_CLOSE_BRACKETS = {'」', '』', '）', '》', '〉', '】', '〕', '］', '｝', ')', '”', '’', '﹂', '﹄', '︶', '︸', '︺', '︼', '︾', '﹀', '﹈'}
_VERTICAL_PUNCT_UP = {'。', '．', '，', '、', '·', '：', '；', '！', '？', '︒', '︐', '︑', '︓', '︔', '︕', '︖', '﹅', '﹆'}
_VERTICAL_COMPACT_SLOT = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS | _VERTICAL_PUNCT_UP
_VERTICAL_HALF_ADVANCE = _VERTICAL_OPEN_BRACKETS | _VERTICAL_CLOSE_BRACKETS

_VERTICAL_ALIGN_TOP_RIGHT = {'﹁', '﹃'}
_VERTICAL_ALIGN_BOTTOM_LEFT = {'﹂', '﹄'}
_VERTICAL_ALIGN_TOP_CENTER = {'︵', '︷', '︹', '︻', '︽', '︿', '﹇'}
_VERTICAL_ALIGN_BOTTOM_CENTER = {'︶', '︸', '︺', '︼', '︾', '﹀', '﹈'}


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
    """渲染层不再做字符替换，全部交给翻译后处理阶段。"""
    if cdpt == 'ー' and direction == 1:
        return 'ー', 90
    return cdpt, 0


def compact_special_symbols(text: str, *, convert_ascii_ellipsis: bool = True) -> str:
    # 渲染层不再做字符替换，统一交给外部的 text_replacements.yaml 规则处理
    return text or ''


def normalize_vertical_ellipsis_text(text: str) -> str:
    # 渲染层不再做字符替换，统一交给外部的 text_replacements.yaml 规则处理
    return text or ''


def auto_add_horizontal_tags(text: str) -> str:
    if not text or '<H>' in text or '<h>' in text.lower():
        return text

    br_tokens = []

    def _mask_br(match):
        br_tokens.append(match.group(0))
        return chr(0xE000 + len(br_tokens) - 1)

    seg = _BR_RE.sub(_mask_br, text)
    word_chars = r'a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19_-'
    seg = re.sub(fr'[{word_chars}]+(?:\s+[{word_chars}]+)+', r'<H>\g<0></H>', seg)

    def _wrap_word(match):
        prefix = seg[:match.start()]
        if prefix.rfind('<H>') > prefix.rfind('</H>'):
            return match.group(0)
        return f'<H>{match.group(1)}</H>'

    seg = re.sub(fr'(?<![{word_chars}])([{word_chars}]{{2,}})(?![{word_chars}])', _wrap_word, seg)
    seg = re.sub(r'[!?！？]{2,4}', r'<H>\g<0></H>', seg)
    for i, token in enumerate(br_tokens):
        seg = seg.replace(chr(0xE000 + i), token)
    pair_re = re.compile(r'<H>([!?！？]{2,4})</H>\s*(\r\n|\r|\n|\[BR\]|<br\s*/?>|【BR】)\s*<H>([!?！？]{2,4})</H>', re.IGNORECASE)
    while True:
        updated = pair_re.sub(lambda m: f'{m.group(1)}{m.group(2)}{m.group(3)}', seg)
        if updated == seg:
            break
        seg = updated
    merge_re = re.compile(
        r'<H>([a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19]+)</H>\s*(?:\r\n|\r|\n|\[BR\]|<br\s*/?>|【BR】)\s*<H>([a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19]+)</H>',
        re.IGNORECASE,
    )
    while True:
        updated = merge_re.sub(r'<H>\1[BR]\2</H>', seg)
        if updated == seg:
            break
        seg = updated
    seg = re.sub(
        r'(?<![a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19])([a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19])\s*(?:\r\n|\r|\n|\[BR\]|<br\s*/?>|【BR】)\s*([a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19])(?![a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19])',
        r'<H>\1[BR]\2</H>',
        seg,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r'<H>(.*?)</H>',
        lambda m: f"<H>{m.group(1).replace(chr(13)+chr(10), '[BR]').replace(chr(13), '[BR]').replace(chr(10), '[BR]')}</H>",
        seg,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _normalize_horizontal_block_content(content: str) -> str:
    content = _BR_RE.sub('', content or '').replace('\r', '').replace('\n', '')
    return content.translate(_HORIZONTAL_SYMBOL_HALFWIDTH_MAP) if re.fullmatch(r'[!?！？]+', content) else content


def prepare_text_for_direction_rendering(text: str, is_horizontal: bool, auto_rotate_symbols: bool = False) -> str:
    text = text or ''
    if is_horizontal:
        return re.sub(r'<H>(.*?)</H>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    _ = auto_rotate_symbols
    return text


def _convert_br_outside_h_tags(text: str) -> str:
    converted = []
    for part in _H_BLOCK_RE.split(text or ''):
        if not part:
            continue
        if part.lower().startswith('<h>') and part.lower().endswith('</h>'):
            chunks = [c for c in _BR_RE.split(part[3:-4]) if c]
            normalized = [f'<H>{clean}</H>' for clean in (_normalize_horizontal_block_content(c) for c in chunks) if clean]
            converted.append('\n'.join(normalized) or part)
        else:
            converted.append(_BR_RE.sub('\n', part))
    return ''.join(converted)


def should_rotate_horizontal_block_90(content: str) -> bool:
    content = _normalize_horizontal_block_content(content).strip()
    return bool(content and re.fullmatch(r'[a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19_-]+(?:[ \t]+[a-zA-Z0-9\uff21-\uff3a\uff41-\uff5a\uff10-\uff19_-]+)*', content))


def add_color(bw_char_map, color, stroke_char_map, stroke_color):
    """合成文字和描边为 RGBA 图层。

    关键做法（解决灰边/脏边）：
    1. 强制 stroke_alpha = max(stroke_alpha, text_alpha)，保证描边在空间上完全
       覆盖文字的所有抗锯齿像素，消除因两次独立光栅化造成的对齐偏差。
    2. 将描边视作文字的"底色"，抗锯齿过渡像素直接在描边纯色上混合，而不是
       两个半透明层的 over 叠加 —— 这是原来灰边的根源。
    3. 全程 float32 计算 + np.clip，避免 uint8 溢出导致的脏点。
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

    # 1) 强制 stroke_alpha >= text_alpha —— 关键修复
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
    # 距离变换描边：比椭圆膨胀更贴合字形轮廓，比矢量路径描边快得多。
    # 原理：对二值化 alpha 图计算每像素到最近前景像素的欧氏距离，
    # 距离 <= stroke_px 的区域即为描边，再用原始 alpha 做抗锯齿过渡。
    src = raster.alpha
    pad = stroke_px + 1
    padded = cv2.copyMakeBorder(src, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    # 前景 mask（二值化，阈值 128 避免抗锯齿半透明像素干扰距离计算）
    fg_mask = (padded >= 128).astype(np.uint8) * 255
    # 对背景像素计算到最近前景的距离
    bg_mask = cv2.bitwise_not(fg_mask)
    dist = cv2.distanceTransform(bg_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    # 描边区域：距离 <= stroke_px，平滑过渡避免硬边
    stroke_region = np.clip((stroke_px + 0.5 - dist), 0.0, 1.0)
    stroke_alpha = (stroke_region * 255).astype(np.uint8)
    # 合并描边与原始 alpha（保留文字内部抗锯齿），保留 padding 以包含扩展区域
    full_alpha = np.maximum(stroke_alpha, padded)
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
    value = _normalize_line_spacing(line_spacing)
    return 1.0 + (value - 1.0) * 10.0 if value > 1.0 else value


def calc_horizontal_line_spacing_px(font_size: int, line_spacing: float) -> int:
    return int(font_size * 0.01 * resolve_horizontal_line_spacing_multiplier(line_spacing))


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
    if border_size > 0:
        stage_t0 = perf_counter() if profile_stats is not None else None
        stroke_px = max(int(stroke_ratio * font_size), 1)
        # 距离变换描边：贴合字形轮廓，比椭圆膨胀更精准
        pad = stroke_px + 1
        padded = cv2.copyMakeBorder(fill_alpha, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        fg_mask = (padded >= 128).astype(np.uint8) * 255
        bg_mask = cv2.bitwise_not(fg_mask)
        dist = cv2.distanceTransform(bg_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        stroke_region = np.clip((stroke_px + 0.5 - dist), 0.0, 1.0)
        stroke_alpha_padded = (stroke_region * 255).astype(np.uint8)
        border_alpha_padded = np.maximum(stroke_alpha_padded, padded)
        border_alpha = border_alpha_padded  # 含 pad，坐标需偏移
        border_left, border_top = fill_left - pad, fill_top - pad
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
    rotate_90: bool = False,
    letter_spacing: float = 1.0,
    profile_stats: Optional[dict] = None,
):
    content = _normalize_horizontal_block_content(content)
    surface = _line_surface(content, font_size, border_size, stroke_ratio, False, letter_spacing, profile_stats)
    if surface is None:
        return None
    text_bitmap, border_bitmap = surface['text'], surface['border']
    if rotate_90:
        text_bitmap = cv2.rotate(text_bitmap, cv2.ROTATE_90_CLOCKWISE)
        border_bitmap = cv2.rotate(border_bitmap, cv2.ROTATE_90_CLOCKWISE)
        cropped = _crop_pair(text_bitmap, border_bitmap)
        if cropped is None:
            return None
        text_bitmap, border_bitmap, _, _, w, h = cropped
    else:
        h, w = text_bitmap.shape
    return {'text': text_bitmap, 'border': border_bitmap, 'width': int(w), 'height': int(h)}


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


def _vertical_base(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> dict:
    state = _state()
    key = (int(font_size), cdpt, round(_normalize_letter_spacing(letter_spacing), 4))
    cached = _cache_get(state.vertical, key)
    if cached is not None:
        return cached
    translated, rot = CJK_Compatibility_Forms_translate(cdpt, 1)
    glyph = _glyph_raster(translated, font_size)
    bitmap = glyph.alpha if glyph.alpha.size else None
    if bitmap is not None and rot == 90:
        bitmap = cv2.rotate(bitmap, cv2.ROTATE_90_CLOCKWISE)
    advance_y = _vertical_ellipsis_advance(glyph, font_size, bitmap) if _is_vertical_ellipsis_char(translated) else (glyph.advance_y if glyph.advance_y > 0 else font_size)
    if translated in _VERTICAL_HALF_ADVANCE:
        advance_y = font_size * 0.5
    advance_y = _scale_advance(int(advance_y), letter_spacing)
    slot_height = advance_y if translated in _VERTICAL_HALF_ADVANCE else max(1, advance_y)
    ink_x, ink_y = 0.0, 0.0
    ink_w = float(bitmap.shape[1]) if bitmap is not None else 0.0
    ink_h = float(bitmap.shape[0]) if bitmap is not None else 0.0
    if bitmap is not None:
        rect = _bitmap_ink_rect(bitmap)
        if rect is not None:
            ink_x, ink_y, ink_w, ink_h = rect
    frame_width = max(font_size, int(glyph.advance_x), int(round(ink_w)) if ink_w else 0, 1)
    slot_origin_y = max(0, int(round((advance_y - slot_height) / 2.0)))
    
    # 默认居中对齐真实墨迹（考虑到 ink_y 和 ink_h）
    y = slot_origin_y + max(0, int(round((slot_height - ink_h) / 2.0))) - ink_y
    
    padding = max(1, int(round(font_size * 0.05)))
    if translated in _VERTICAL_ALIGN_TOP_RIGHT or translated in _VERTICAL_ALIGN_TOP_CENTER:
        y = padding - ink_y
    elif translated in _VERTICAL_ALIGN_BOTTOM_LEFT or translated in _VERTICAL_ALIGN_BOTTOM_CENTER:
        y = advance_y - ink_h - padding - ink_y

    base = {
        'translated': translated, 'rot_degree': rot, 'bitmap': bitmap, 'advance_y': int(advance_y),
        'ink_x': float(ink_x), 'ink_w': float(ink_w), 'y': int(round(y)),
        'frame_width': int(frame_width),
    }
    return _cache_put(state.vertical, key, base, _VERTICAL_CACHE_MAX)


def get_vertical_char_bitmap_width(font_size: int, cdpt: str, letter_spacing: float = 1.0) -> int:
    bitmap = _vertical_base(font_size, cdpt, letter_spacing)['bitmap']
    return font_size if bitmap is None or bitmap.size == 0 else int(bitmap.shape[1])


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
    surface = _block_surface(font_size, content, 0, 0.0, should_rotate_horizontal_block_90(content), letter_spacing)
    return font_size if surface is None or surface['height'] <= 0 else int(surface['height'])


def get_char_offset_x(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _measure_horizontal_text_width('　' if cdpt == '＿' else cdpt, font_size, letter_spacing)


def get_string_width(font_size: int, text: str, letter_spacing: float = 1.0):
    return _measure_horizontal_text_width(text, font_size, letter_spacing)


def get_char_offset_y(font_size: int, cdpt: str, letter_spacing: float = 1.0):
    return _vertical_base(font_size, '　' if cdpt == '＿' else cdpt, letter_spacing)['advance_y']


def get_string_height(font_size: int, text: str, letter_spacing: float = 1.0):
    text = normalize_vertical_ellipsis_text(compact_special_symbols(text))
    total = 0
    for part in _H_BLOCK_RE.split(re.sub(r'\s*(?:\[BR\]|<br>|【BR】)\s*', '', text or '', flags=re.IGNORECASE)):
        if not part:
            continue
        if part.lower().startswith('<h>') and part.lower().endswith('</h>'):
            total += calc_horizontal_block_height(font_size, part[3:-4], letter_spacing)
        else:
            total += sum(get_char_offset_y(font_size, c, letter_spacing) for c in part)
    return total


def _vertical_border_bitmap(translated: str, font_size: int, stroke_ratio: float, rot_degree: int):
    bitmap = _glyph_stroke_alpha(translated, font_size, stroke_ratio)
    if bitmap.size == 0:
        return None
    return cv2.rotate(bitmap, cv2.ROTATE_90_CLOCKWISE) if rot_degree == 90 else bitmap


def _build_vertical_layout(
    font_size: int,
    line_text: str,
    border_size: int,
    stroke_ratio: float,
    letter_spacing: float,
    block_cache: dict,
    profile_stats: Optional[dict] = None,
) -> dict:
    line_width, items = font_size, []
    for part in _H_BLOCK_RE.split(line_text):
        if not part:
            continue
        if part.lower().startswith('<h>') and part.lower().endswith('</h>'):
            raw = part[3:-4]
            key = (font_size, raw, border_size, round(float(stroke_ratio), 4), round(_normalize_letter_spacing(letter_spacing), 4))
            surface = block_cache.get(key)
            if surface is None:
                surface = _block_surface(
                    font_size,
                    raw,
                    border_size,
                    stroke_ratio,
                    should_rotate_horizontal_block_90(raw),
                    letter_spacing,
                    profile_stats,
                )
                block_cache[key] = surface
            if surface is not None:
                line_width = max(line_width, int(surface['width']))
                items.append(('block', surface))
            continue
        for char in part:
            if char == '＿':
                items.append(('placeholder', _scale_advance(font_size, letter_spacing)))
                continue
            base = _vertical_base(font_size, char, letter_spacing)
            line_width = max(line_width, int(base['frame_width']))
            items.append(('char', base))
    cursor, laid = 0, []
    for kind, value in items:
        if kind == 'block':
            laid.append({'kind': kind, 'surface': value, 'width': int(value['width']), 'height': int(value['height']), 'cursor_y': cursor})
            cursor += int(value['height'])
        elif kind == 'placeholder':
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
    surface = _line_surface(char, font_size, border_size, _resolve_stroke_ratio(config, stroke_width), False, letter_spacing)
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
    text = compact_special_symbols(text, convert_ascii_ellipsis=False)
    if not text:
        return None
    _ = (width, height, lang, hyphenate, region_count)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    bg_size = int(max(font_size * stroke_ratio, 1)) if bg is not None else 0
    spacing_y = calc_horizontal_line_spacing_px(font_size, line_spacing)
    line_texts = _BR_RE.sub('\n', text).split('\n')
    surfaces, metrics, tops, extents, logical_widths = [], [], [], [], []
    logical_y = min_ink_top = max_ink_bottom = 0.0
    for idx, line_text in enumerate(line_texts):
        surface = _line_surface(line_text, font_size, bg_size, stroke_ratio, reversed_direction, letter_spacing, profile_stats)
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
    text = normalize_vertical_ellipsis_text(compact_special_symbols(text))
    if not text:
        return None
    _ = (h, region_count)
    stroke_ratio = _resolve_stroke_ratio(config, stroke_width)
    bg_size = int(max(font_size * stroke_ratio, 1)) if bg is not None else 0
    spacing_x = int(font_size * 0.2 * (_normalize_line_spacing(line_spacing) or 1.0))
    block_cache = {}
    stage_t0 = perf_counter() if profile_stats is not None else None
    layouts = [
        _build_vertical_layout(font_size, line, bg_size, stroke_ratio, letter_spacing, block_cache, profile_stats)
        for line in _convert_br_outside_h_tags(text).split('\n')
    ]
    _profile_add(profile_stats, "tr_vertical_layout_ms", stage_t0)
    line_widths = [layout['width'] for layout in layouts]
    max_height = max((layout['height'] for layout in layouts), default=0)
    content_width = sum(line_widths) + spacing_x * max(0, len(line_widths) - 1)
    canvas_text = np.zeros((max_height + (font_size + bg_size) * 2, content_width + (font_size + bg_size) * 2), dtype=np.uint8)
    canvas_border = np.zeros_like(canvas_text)
    current_edge = font_size + bg_size + content_width
    columns = []
    for width in line_widths:
        columns.append((current_edge - width / 2.0, current_edge))
        current_edge -= width + spacing_x
    stage_t0 = perf_counter() if profile_stats is not None else None
    for idx, layout in enumerate(layouts):
        line_width = layout['width']
        center_x, _ = columns[idx]
        line_start_x = int(round(center_x - line_width / 2.0))
        line_origin_y = font_size + bg_size
        if alignment == 'center':
            line_origin_y += round((max_height - layout['height']) / 2.0)
        elif alignment == 'right':
            line_origin_y += max_height - layout['height']
        for item in layout['items']:
            if item['kind'] == 'block':
                surface = item['surface']
                _paste_surface(canvas_text, canvas_border, surface, line_start_x + round((line_width - item['width']) / 2.0), line_origin_y + item['cursor_y'])
            elif item['kind'] == 'char' and item['bitmap'] is not None:
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
    from .auto_linebreak import _calc_horizontal_layout
    _ = max_height
    return _calc_horizontal_layout(font_size, text, max_width, language, hyphenate, letter_spacing=letter_spacing)


def calc_vertical(font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0):
    from .auto_linebreak import _calc_vertical_layout
    return _calc_vertical_layout(font_size, text, max_height, config, letter_spacing=letter_spacing)


def calc_vertical_metrics(font_size: int, text: str, max_height: int, config=None, letter_spacing: float = 1.0):
    from .auto_linebreak import _layout_vertical_metrics
    return _layout_vertical_metrics(font_size, text, max_height, config, letter_spacing=letter_spacing)
