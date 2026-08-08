"""Qt 字体运行时。

离屏 QGuiApplication 引导、字体文件注册与 foundry 方括号净化、线程局部
FontState（字体选择与各级缓存）、QFont/QTextLayout 构造、连字器选择。
"""
import logging
import os
import sys
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field

from hyphen import Hyphenator
from hyphen.dictools import LANGUAGES as HYPHENATOR_LANGUAGES
from langcodes import standardize_tag
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication, QRawFont, QTextLayout

from ...utils import BASE_PATH
from ..rich_text import TextStyle
from ._shared import _QFONT_CACHE_MAX, _QT_FONT_PROBE_SIZE, _RAW_FONT_CACHE_MAX, _cache_get, _cache_put

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

try:
    HYPHENATOR_LANGUAGES.remove('fr')
    HYPHENATOR_LANGUAGES.append('fr_FR')
except Exception:
    pass

DEFAULT_FONT_FAMILY = 'Microsoft YaHei UI'
FONT_STYLE_SEPARATOR = '::'
_thread_state = threading.local()
_qt_runtime_lock = threading.Lock()
_qt_runtime_app = None
_font_descriptor_cache = {}
_font_registration_cache = {}
_font_families_cache = {}
_font_family_aliases = {}
_hyphenator_cache = {}


@dataclass(frozen=True)
class LayoutFontDescriptor:
    family: str
    style: str = ''


@dataclass
class FontState:
    font_family: str = ''
    font_style: str = ''
    bold: bool = False
    raw_fonts: dict = field(default_factory=OrderedDict)
    qfonts: dict = field(default_factory=OrderedDict)
    glyph_specs: dict = field(default_factory=OrderedDict)
    glyphs: dict = field(default_factory=OrderedDict)
    measures: dict = field(default_factory=dict)
    vertical: dict = field(default_factory=OrderedDict)


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


# Qt 把 "Family [Foundry]" 中方括号段当厂商名解析（qfontdatabase 的 parseFontName）；
# 家族名以 "[" 开头时会被拆成「空家族 + 厂商」，匹配退化成该厂商下任选，
# 结果与请求的字体无关（例如 [工具箱] 系列全部命中同一个文件）。
_QT_FOUNDRY_SENSITIVE_NAME_IDS = (1, 3, 4, 16, 21)


def qt_family_is_ambiguous(family: str) -> bool:
    """Return True when Qt's "Family [Foundry]" parsing yields an empty family."""
    name = (family or '').strip()
    return name.startswith('[') and name.rfind(']') > 0


def strip_qt_foundry_brackets(family: str) -> str:
    return (family or '').replace('[', '').replace(']', '').strip()


def _font_registration_key(path: str) -> str:
    return _normalize_font_path(os.path.normcase(os.path.abspath(path)))


def _sanitized_font_bytes(path: str):
    """返回 ``(名字表去掉方括号后的字体数据, 原始家族名列表)``。

    无需处理或改写失败时数据为 None。原始家族名（nameID 1/4/16 各语言记录）
    用于建立「旧名字 -> 文件」的别名映射。
    """
    if not path.lower().endswith(('.ttf', '.otf')):
        return None, []
    try:
        import io
        from fontTools.ttLib import TTFont
    except ImportError:
        logger.warning('fontTools unavailable; cannot rewrite bracketed font names: %s', path)
        return None, []
    try:
        font = TTFont(path, lazy=True)
        try:
            name_table = font['name']
            changed = False
            original_names = []
            for record in name_table.names:
                if record.nameID not in _QT_FOUNDRY_SENSITIVE_NAME_IDS:
                    continue
                try:
                    value = record.toUnicode()
                except UnicodeDecodeError:
                    continue
                if record.nameID in (1, 4, 16) and value:
                    original_names.append(value)
                if '[' in value or ']' in value:
                    record.string = strip_qt_foundry_brackets(value)
                    changed = True
            if not changed:
                return None, []
            # 缺英文首选家族名(nameID 16)时补一条：offscreen 的 freetype 字体库
            # 选名优先 nameID 16，缺英文记录会挑中文名并转成乱码。
            en_family = name_table.getName(1, 3, 1, 0x409)
            if en_family is not None and name_table.getName(16, 3, 1, 0x409) is None:
                name_table.setName(en_family.toUnicode(), 16, 3, 1, 0x409)
            if 'DSIG' in font:
                del font['DSIG']  # 名字表改动后原签名必然失效
            buffer = io.BytesIO()
            font.save(buffer)
            return buffer.getvalue(), original_names
        finally:
            font.close()
    except Exception:
        logger.exception('Failed to sanitize font name table: %s', path)
        return None, []


def register_font_file(path: str) -> list:
    """注册字体文件并返回可安全用于 QFont 匹配的 family 列表。

    家族名以 "[" 开头的字体（如 "[工具箱]xxx-简繁"）改为注册去掉方括号的
    内存副本，绕开 Qt 的 foundry 语法；返回列表已过滤空名和带方括号的名字。
    """
    key = _font_registration_key(path)
    cached = _font_families_cache.get(key)
    if cached is not None:
        return cached
    if QGuiApplication.instance() is None:
        return []

    families = []
    font_id = -1
    try:
        font_id = QFontDatabase.addApplicationFont(path)
        families = list(QFontDatabase.applicationFontFamilies(font_id)) if font_id >= 0 else []
        if any(qt_family_is_ambiguous(name) for name in families):
            sanitized, original_names = _sanitized_font_bytes(path)
            if sanitized is not None:
                QFontDatabase.removeApplicationFont(font_id)
                font_id = QFontDatabase.addApplicationFontFromData(sanitized)
                families = list(QFontDatabase.applicationFontFamilies(font_id)) if font_id >= 0 else []
                # 旧配置/富文本样式里可能仍存着原始名字（含中文变体），
                # 记下「原名/去括号名 -> 文件」映射供 set_font 兜底。
                if font_id >= 0:
                    for name in original_names:
                        for variant in (name, strip_qt_foundry_brackets(name)):
                            if variant:
                                _font_family_aliases.setdefault(variant.casefold(), path)
                logger.info(
                    'Registered bracketed font with sanitized families: %s -> %s',
                    os.path.basename(path), families,
                )
            else:
                logger.warning(
                    'Font family uses Qt foundry brackets and could not be rewritten; '
                    'QFont matching may pick a wrong font: %s', path,
                )
    except Exception:
        logger.exception('Failed to register font: %s', path)

    families = [name for name in families if name and not qt_family_is_ambiguous(name)]
    _font_registration_cache[key] = font_id
    _font_families_cache[key] = families
    return families


def _register_project_fonts() -> None:
    """Register custom project fonts in Qt; rendering still addresses them by family."""
    font_dir = os.path.join(BASE_PATH, 'fonts')
    if not os.path.isdir(font_dir):
        return
    for root, _, filenames in os.walk(font_dir):
        for filename in filenames:
            if not filename.lower().endswith(('.ttf', '.otf', '.ttc', '.pfb')):
                continue
            register_font_file(os.path.join(root, filename))


def _system_font_dirs() -> list:
    if sys.platform == 'win32':
        dirs = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')]
        local_appdata = os.environ.get('LOCALAPPDATA')
        if local_appdata:
            # 当前 Windows 默认把用户安装的字体放这里
            dirs.append(os.path.join(local_appdata, 'Microsoft', 'Windows', 'Fonts'))
    elif sys.platform == 'darwin':
        dirs = ['/System/Library/Fonts', '/Library/Fonts', os.path.expanduser('~/Library/Fonts')]
    else:
        dirs = ['/usr/share/fonts', '/usr/local/share/fonts',
                os.path.expanduser('~/.local/share/fonts'), os.path.expanduser('~/.fonts')]
    return [font_dir for font_dir in dirs if os.path.isdir(font_dir)]


_system_fonts_registered = False


def _register_system_fonts() -> bool:
    """offscreen/minimal 平台的字体库不枚举系统字体（只扫 QT_QPA_FONTDIR），
    桌面模式选的系统字体在 CLI/服务端会查不到；首次未命中家族名时把系统
    字体目录整体注册进 Qt，进程内只扫一次。返回是否值得重查家族名。"""
    global _system_fonts_registered
    if _system_fonts_registered:
        return False
    _system_fonts_registered = True
    app = QGuiApplication.instance()
    if app is None or app.platformName() not in ('offscreen', 'minimal'):
        return False
    count = 0
    for font_dir in _system_font_dirs():
        for root, _, filenames in os.walk(font_dir):
            for filename in filenames:
                if not filename.lower().endswith(('.ttf', '.otf', '.ttc', '.otc')):
                    continue
                register_font_file(os.path.join(root, filename))
                count += 1
    logger.info('Registered system fonts for headless Qt: %d files', count)
    return count > 0


def _state() -> FontState:
    state = getattr(_thread_state, 'value', None)
    if state is None:
        state = FontState()
        _thread_state.value = state
        set_font(DEFAULT_FONT_FAMILY)
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

    registered_families = register_font_file(path)

    family = ''
    style = ''
    try:
        raw = _raw_font(path, _QT_FONT_PROBE_SIZE)
        if raw.isValid():
            family = raw.familyName() or ''
            style = raw.styleName() or ''
    except Exception:
        pass

    # 文件里读出的家族名带方括号时不能直接交给 QFont 匹配（foundry 语法），
    # 换用注册环节返回的净化名。
    if (not family or qt_family_is_ambiguous(family)) and registered_families:
        family = registered_families[0]

    if not family:
        raw = QRawFont(path, _QT_FONT_PROBE_SIZE)
        if raw.isValid():
            family = raw.familyName() or ''
            style = style or raw.styleName() or ''

    if not family:
        raise RuntimeError(f'Could not resolve Qt font family: {path}')
    if qt_family_is_ambiguous(family):
        logger.warning('Bracketed font family may not match correctly in Qt: %s (%s)', family, path)
    descriptor = LayoutFontDescriptor(family=family, style=style)
    _font_descriptor_cache[path] = descriptor
    return descriptor


def _split_font_value(value: str) -> tuple[str, str]:
    """Parse the optional style suffix used by desktop font selectors."""
    family, separator, style = str(value or '').rpartition(FONT_STYLE_SEPARATOR)
    if separator and family and style:
        return family, style
    return str(value or ''), ''


def _set_family(state: FontState, family: str, style: str = ''):
    if state.font_family == family and state.font_style == style:
        return
    state.font_family = family
    state.font_style = style
    state.qfonts.clear()
    # glyph_specs/glyphs 的 key 含字体家族，切换字体时无需清空，
    # 保留缓存避免重复解析大字体文件的字形数据
    state.measures.clear()
    state.vertical.clear()


def _match_family(requested: str):
    """在 Qt 字体库中解析家族名；返回可用家族名或 None。"""
    if not requested:
        return None
    available = {name.casefold(): name for name in QFontDatabase.families()}
    family = available.get(requested.casefold())
    if family is None or qt_family_is_ambiguous(family):
        # 旧配置/系统安装的 "[工具箱]xxx" 家族名走不了 QFont 匹配（foundry 语法），
        # 映射到注册环节生成的去括号名字。
        stripped = strip_qt_foundry_brackets(requested)
        stripped_family = available.get(stripped.casefold()) if stripped else None
        if stripped_family and not qt_family_is_ambiguous(stripped_family):
            return stripped_family
        # offscreen 等环境的字体库可能拿不到中文家族名，退回按文件注册并改用其家族名
        alias_path = _font_family_aliases.get(requested.casefold()) or (
            _font_family_aliases.get(stripped.casefold()) if stripped else None)
        if alias_path and os.path.exists(alias_path):
            try:
                return _font_descriptor(alias_path).family
            except Exception:
                logger.exception('Could not load font file: %s', alias_path)
    return family


def set_font(font: str):
    """Select a Qt family/style; a legacy font file path is mapped to its face."""
    state = getattr(_thread_state, 'value', None) or FontState()
    _thread_state.value = state
    requested = str(font or '').strip()
    resolved = _resolve_existing_font_path(requested)
    if resolved:
        try:
            descriptor = _font_descriptor(resolved)
            _set_family(state, descriptor.family, descriptor.style)
            return
        except Exception:
            logger.exception('Could not load font file: %s', resolved)

    _ensure_qt_runtime()
    _register_project_fonts()
    requested_family, requested_style = _split_font_value(requested)
    family = _match_family(requested_family)
    if family is None and requested and _register_system_fonts():
        family = _match_family(requested_family)
    if family is not None and qt_family_is_ambiguous(family):
        logger.warning('Bracketed font family may not match correctly in Qt: %s', family)
    if family is None:
        # 无头下 QGuiApplication.font() 是逻辑字体 "Sans Serif"，匹配结果随机，
        # 直接回退到自带字体目录里保证存在的默认家族。
        family = DEFAULT_FONT_FAMILY
        if requested:
            logger.warning('Qt font family not found: %s; using %s', requested, family)
    _set_family(state, family, requested_style)


def load_font_file(path: str) -> str:
    """Register a font file and return its Qt family without persisting the path."""
    resolved = _resolve_existing_font_path(path)
    if not resolved:
        raise FileNotFoundError(path)
    return _font_descriptor(resolved).family


def set_bold(bold: bool):
    state = _state()
    value = bool(bold)
    if state.bold == value:
        return
    state.bold = value
    state.measures.clear()
    state.vertical.clear()


@contextmanager
def _bold_scope(bold: bool):
    state = _state()
    previous = state.bold
    state.bold = bool(bold)
    try:
        yield
    finally:
        state.bold = previous


@contextmanager
def _style_font_scope(style: TextStyle):
    state = _state()
    previous_family = state.font_family
    previous_style = state.font_style
    previous_bold = state.bold
    requested_font = getattr(style, 'font_family', None)
    if requested_font:
        set_font(requested_font)
    state.bold = bool(getattr(style, 'bold', False))
    try:
        yield
    finally:
        _set_family(state, previous_family or DEFAULT_FONT_FAMILY, previous_style)
        state.bold = previous_bold


def _layout_font(font_size: int, letter_spacing: float) -> QFont:
    state = _state()
    family = state.font_family or DEFAULT_FONT_FAMILY
    style = state.font_style
    key = (family, style, bool(state.bold), int(max(font_size, 1)), round(float(letter_spacing), 4))
    qfont = _cache_get(state.qfonts, key)
    if qfont is None:
        qfont = QFontDatabase.font(family, style, 12) if style else QFont()
        if not style:
            qfont.setFamilies([family])
        if state.bold or not style:
            qfont.setBold(state.bold)
        qfont.setPixelSize(key[3])
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
