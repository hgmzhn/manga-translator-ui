"""Qt 字体数据库共享 helper。

与 BallonsTranslator 一致：系统字体和应用字体都按 family 展示；项目
``fonts/`` 中的文件仅负责注册进 Qt，不再把文件路径作为编辑器字体值。

注册统一走 ``text_render.register_font_file``：家族名以 ``[`` 开头的字体
（如 "[工具箱]xxx-简繁"）会被 Qt 的 "Family [Foundry]" 语法解析成空家族名，
QFont 匹配固定落到同一字体；注册层会自动改写为去掉方括号的内存副本。
"""
import logging
import os
import unicodedata
import weakref
from collections import Counter
from collections.abc import Callable
from functools import lru_cache

from PyQt6.QtCore import (
    QAbstractListModel,
    QEvent,
    QLocale,
    QModelIndex,
    QPoint,
    QSignalBlocker,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QFont, QFontDatabase, QFontInfo, QGuiApplication, QRawFont, QWheelEvent
from PyQt6.QtWidgets import QListView, QVBoxLayout, QWidget
from qfluentwidgets import LineEdit, MenuAnimationType
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu
from qfluentwidgets.components.widgets.menu import IndicatorMenuItemDelegate, MenuAnimationManager
from qfluentwidgets.components.widgets.scroll_bar import SmoothScrollDelegate

from manga_translator.rendering.text_render import (
    qt_family_is_ambiguous,
    register_font_file,
    strip_qt_foundry_brackets,
)
from ui.widgets.wheel_filter import TopLevelComboBox, _stop_popup_animation

from .resource_helper import resource_path

logger = logging.getLogger('manga_translator')

FONT_FILE_EXTENSIONS = ('.ttf', '.otf', '.ttc')
FONT_STYLE_SEPARATOR = '::'
_REGISTERED_FONT_FAMILIES: dict[str, list[str]] = {}
_ORIGINAL_FONT_DISPLAY_NAMES: dict[str, str] = {}
_FONT_SEARCH_PLACEHOLDERS = {
    "zh_CN": "搜索字体…",
    "zh_TW": "搜尋字型…",
    "ja_JP": "フォントを検索…",
    "ko_KR": "글꼴 검색…",
    "es_ES": "Buscar fuentes…",
    "en_US": "Search fonts…",
}
_SYSTEM_FONTS_ENABLED = True
_FONT_COMBO_INSTANCES: weakref.WeakSet = weakref.WeakSet()
_FONT_DIRECTORY_SIGNATURE: tuple | None = None
_FONT_FILE_LIST_CACHE: tuple[tuple[str, str], ...] = ()
_FONT_FAMILY_CACHE: dict[bool, tuple[str, ...]] = {}

def _clear_font_catalog_caches() -> None:
    _FONT_FAMILY_CACHE.clear()
    localized_font_family.cache_clear()
    _list_font_family_entries_cached.cache_clear()
    _font_styles.cache_clear()
    _list_font_style_entries_cached.cache_clear()
    _cached_qfont_for_value.cache_clear()


def fonts_directory() -> str:
    """字体目录绝对路径（打包后位于 app.exe 同级）。"""
    return resource_path('fonts')


def list_font_files() -> list[tuple[str, str]]:
    """Enumerate project font files, registering newly discovered faces once."""
    global _FONT_DIRECTORY_SIGNATURE, _FONT_FILE_LIST_CACHE
    try:
        fonts_dir = fonts_directory()
        try:
            stat = os.stat(fonts_dir)
            signature = (stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            signature = None
        if signature != _FONT_DIRECTORY_SIGNATURE:
            font_files: list[tuple[str, str]] = []
            if signature is not None:
                for entry in os.scandir(fonts_dir):
                    if entry.is_file() and entry.name.lower().endswith(FONT_FILE_EXTENSIONS):
                        font_files.append((os.path.splitext(entry.name)[0], entry.name))
            font_files.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
            _FONT_FILE_LIST_CACHE = tuple(font_files)
            _FONT_DIRECTORY_SIGNATURE = signature
    except OSError as exc:
        logger.warning(f"扫描字体目录失败: {exc}")
        _FONT_FILE_LIST_CACHE = ()

    catalog_changed = False
    if QGuiApplication.instance() is not None:
        fonts_dir = fonts_directory()
        for _stem, filename in _FONT_FILE_LIST_CACHE:
            path = os.path.normcase(os.path.abspath(os.path.join(fonts_dir, filename)))
            if path in _REGISTERED_FONT_FAMILIES:
                continue
            _REGISTERED_FONT_FAMILIES[path] = register_font_file(path)
            _remember_original_font_names(path)
            catalog_changed = True
        if catalog_changed:
            _font_family_name_records.cache_clear()
            _resolved_font_identity.cache_clear()
            _clear_font_catalog_caches()
    return list(_FONT_FILE_LIST_CACHE)


def list_font_families(include_system: bool | None = None) -> list[str]:
    """Return the scalable project and optional system font families."""
    if QGuiApplication.instance() is None:
        return []
    list_font_files()
    if include_system is None:
        include_system = _SYSTEM_FONTS_ENABLED
    include_system = bool(include_system)
    cached = _FONT_FAMILY_CACHE.get(include_system)
    if cached is not None:
        return list(cached)
    families = {
        name for name in QFontDatabase.families()
        if name and not qt_family_is_ambiguous(name) and QFontDatabase.isScalable(name)
    }
    if not include_system:
        project_families = {
            family
            for families_for_file in _REGISTERED_FONT_FAMILIES.values()
            for family in families_for_file
        }
        families.intersection_update(project_families)
    result = tuple(sorted(families, key=str.casefold))
    _FONT_FAMILY_CACHE[include_system] = result
    return list(result)


def font_family_for_file(filename: str) -> str:
    """Return the first Qt family registered from a project font file."""
    if not filename or QGuiApplication.instance() is None:
        return ''
    list_font_files()
    path = os.path.normcase(os.path.abspath(os.path.join(fonts_directory(), filename)))
    families = _REGISTERED_FONT_FAMILIES.get(path) or []
    return families[0] if families else ''


def _search_key(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _remember_original_font_names(path: str) -> None:
    """Remember bracketed names that registration sanitizes for safe Qt use."""
    if not path.lower().endswith((".ttf", ".otf")):
        return
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(path, lazy=True)
        try:
            for record in font["name"].names:
                if record.nameID not in (1, 16, 21):
                    continue
                try:
                    original = record.toUnicode().strip()
                except UnicodeDecodeError:
                    continue
                sanitized = strip_qt_foundry_brackets(original)
                if original and sanitized != original:
                    _ORIGINAL_FONT_DISPLAY_NAMES.setdefault(_search_key(sanitized), original)
        finally:
            font.close()
    except Exception as exc:
        logger.debug("读取字体原始名称失败 %s: %s", path, exc)


def _original_font_display_name(name: str) -> str:
    return _ORIGINAL_FONT_DISPLAY_NAMES.get(_search_key(name), name)


@lru_cache(maxsize=None)
def _font_family_name_records(family: str) -> tuple[tuple[int, str, str], ...]:
    """Return ``(name id, language tag, value)`` records from Qt's font face."""
    records: list[tuple[int, str, str]] = []
    try:
        from fontTools.ttLib import TTFont, newTable
        from fontTools.ttLib.tables._n_a_m_e import _MAC_LANGUAGES, _WINDOWS_LANGUAGES

        data = bytes(QRawFont.fromFont(QFont(family)).fontTable("name"))
        if data:
            table = newTable("name")
            table.decompile(data, TTFont())
            for record in table.names:
                if record.nameID not in (1, 16, 21):
                    continue
                try:
                    value = record.toUnicode().strip()
                except UnicodeDecodeError:
                    continue
                if not value:
                    continue
                if record.platformID == 3:
                    language = _WINDOWS_LANGUAGES.get(record.langID, "")
                elif record.platformID == 1:
                    language = _MAC_LANGUAGES.get(record.langID, "")
                elif record.platformID == 0 and record.langID >= 0x8000:
                    tags = getattr(table, "langTagRecord", ())
                    tag_index = record.langID - 0x8000
                    language = tags[tag_index].toUnicode() if tag_index < len(tags) else ""
                else:
                    language = ""
                records.append((record.nameID, language, value))
    except Exception as exc:
        logger.debug("读取字体本地化名称失败 %s: %s", family, exc)
    return tuple(dict.fromkeys(records))


@lru_cache(maxsize=None)
def _resolved_font_identity(family: str, style: str = '') -> tuple:
    """Return KDE-style attributes for the font Qt actually resolves.

    Candidate grouping already establishes that names may refer to the same
    family. These lightweight attributes distinguish its concrete faces without
    reading and hashing physical font tables.
    """
    try:
        resolved_style = style
        if not resolved_style:
            styles = [str(value) for value in QFontDatabase.styles(family) if str(value)]
            resolved_style = styles[0] if styles else ''
        font = (
            QFontDatabase.font(family, resolved_style, 12)
            if resolved_style
            else QFont(family, 12)
        )
        info = QFontInfo(font)
        qt_style = info.style()
        return (
            int(info.weight()),
            int(getattr(qt_style, 'value', qt_style)),
            int(font.stretch()),
            _search_key(info.styleName() or resolved_style),
        )
    except Exception as exc:
        logger.debug("解析字体样式身份失败 %s (%s): %s", family, style, exc)
        return ()


def _font_face_signature(family: str):
    """Compatibility wrapper for callers that used the old private helper."""
    return _resolved_font_identity(family)


def _font_style_signature(family: str, style: str):
    """Compatibility wrapper for callers that used the old private helper."""
    return _resolved_font_identity(family, style)


def _language_score(language: str, locale_code: str) -> int:
    language = str(language or "").replace("_", "-").casefold()
    locale_code = str(locale_code or "").replace("_", "-").casefold()
    locale_language = locale_code.split("-", 1)[0]
    if language == locale_code:
        return 5
    if locale_code.startswith("zh-cn") and language in {"zh", "zh-cn", "zh-hans", "zh-sg"}:
        return 5
    if locale_code.startswith("zh-tw") and language in {"zh-tw", "zh-hant", "zh-hk", "zh-mo"}:
        return 5
    if language.split("-", 1)[0] == locale_language:
        return 4
    if language.split("-", 1)[0] == "en":
        return 2
    return 1 if not language else 0


@lru_cache(maxsize=None)
def localized_font_family(family: str, locale_code: str) -> tuple[str, tuple[str, ...]]:
    """Return the localized display family and all searchable aliases."""
    records = _font_family_name_records(family)
    if not records:
        return family, (family,)

    family_key = _search_key(family)
    matching_name_ids = {
        name_id for name_id, _language, value in records
        if _search_key(value) == family_key
    }
    candidates = [
        record for record in records
        if not matching_name_ids or record[0] in matching_name_ids
    ]
    name_id_score = {16: 3, 21: 2, 1: 1}
    best = max(
        candidates,
        key=lambda record: (
            _language_score(record[1], locale_code),
            name_id_score.get(record[0], 0),
        ),
    )
    candidate_names = [record[2] for record in candidates]
    aliases = tuple(dict.fromkeys([
        family,
        *candidate_names,
        *(_original_font_display_name(name) for name in candidate_names),
    ]))
    return _original_font_display_name(best[2]), aliases


@lru_cache(maxsize=None)
def _list_font_family_entries_cached(
    locale_code: str,
    include_system: bool,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    entries = []
    for family in list_font_families(include_system=include_system):
        display, aliases = localized_font_family(family, locale_code)
        entries.append((family, display, aliases))

    # Qt may expose one physical face through legacy, typographic, or localized
    # family names. The resolved face identity is language-independent; complete name
    # records are only a fallback for environments where Qt cannot provide one.
    parents = list(range(len(entries)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    candidate_parents = list(range(len(entries)))

    def candidate_find(index: int) -> int:
        while candidate_parents[index] != index:
            candidate_parents[index] = candidate_parents[candidate_parents[index]]
            index = candidate_parents[index]
        return index

    def candidate_union(left: int, right: int) -> None:
        left, right = candidate_find(left), candidate_find(right)
        if left != right:
            candidate_parents[right] = left

    first_by_name: dict[str, int] = {}
    for index, (family, _display, _aliases) in enumerate(entries):
        complete_names = tuple(dict.fromkeys((
            family,
            *(value for _name_id, _language, value in _font_family_name_records(family)),
        )))
        for name in complete_names:
            key = _search_key(name)
            previous = first_by_name.setdefault(key, index)
            candidate_union(index, previous)

    candidate_groups: dict[int, list[int]] = {}
    for index in range(len(entries)):
        candidate_groups.setdefault(candidate_find(index), []).append(index)
    for indexes in candidate_groups.values():
        if len(indexes) == 1:
            continue
        identities = {}
        for index in indexes:
            identity = _font_face_signature(entries[index][0])
            if identity:
                previous = identities.setdefault(identity, index)
                union(index, previous)
        if not identities:
            for index in indexes[1:]:
                union(indexes[0], index)

    grouped: dict[int, list[tuple[str, str, tuple[str, ...]]]] = {}
    for index, entry in enumerate(entries):
        grouped.setdefault(find(index), []).append(entry)

    merged = []
    for group in grouped.values():
        canonical = min(
            group,
            key=lambda entry: (
                not entry[0].isascii(),
                len(_search_key(entry[0])),
                _search_key(entry[0]),
            ),
        )[0]
        display, _aliases = localized_font_family(canonical, locale_code)
        aliases = tuple(dict.fromkeys(
            alias
            for family, _display, family_aliases in group
            for alias in (family, *family_aliases)
        ))
        merged.append((display, canonical, aliases))

    display_counts = Counter(_search_key(display) for display, _family, _aliases in merged)
    result = [
        (
            f"{display} ({family})" if display_counts[_search_key(display)] > 1 and display != family else display,
            family,
            aliases,
        )
        for display, family, aliases in merged
    ]
    return tuple(sorted(result, key=lambda entry: (_search_key(entry[0]), _search_key(entry[1]))))


def list_font_family_entries(
    locale_code: str,
    include_system: bool | None = None,
) -> list[tuple[str, str, tuple[str, ...]]]:
    if include_system is None:
        include_system = _SYSTEM_FONTS_ENABLED
    return list(_list_font_family_entries_cached(locale_code, bool(include_system)))


@lru_cache(maxsize=None)
def _font_styles(family: str) -> list[str]:
    """Return Qt styles for a family, with a stable default first."""
    try:
        styles = [str(style) for style in QFontDatabase.styles(family) if str(style)]
    except Exception:
        styles = []
    if not styles:
        return ['']
    return sorted(styles, key=lambda style: (style.casefold() not in {'regular', 'normal'}, style.casefold()))


def font_value(family: str, style: str = '', default_style: str = '') -> str:
    """Serialize a selectable Qt family/style pair for persisted font fields.

    Existing settings store only a family, so the default style intentionally keeps
    that representation. Non-default styles use an unambiguous suffix parsed by
    the renderer before the value is passed to Qt.
    """
    if not style or style == default_style:
        return family
    return f'{family}{FONT_STYLE_SEPARATOR}{style}'


def split_font_value(value: str) -> tuple[str, str]:
    """Return the family and optional style represented by ``font_value``."""
    family, separator, style = str(value or '').rpartition(FONT_STYLE_SEPARATOR)
    if separator and family and style:
        return family, style
    return str(value or ''), ''


@lru_cache(maxsize=None)
def _list_font_style_entries_cached(
    locale_code: str,
    include_system: bool,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return selectable family/style entries for the font controls.

    A font collection commonly contains several styles under one family. Showing
    them independently makes an explicit Light or Bold choice survive rendering,
    while legacy family-only values still select the default style.
    """
    entries = []
    for display, family, aliases in list_font_family_entries(locale_code, include_system):
        styles = _font_styles(family)
        default_style = styles[0]
        for style in styles:
            value = font_value(family, style, default_style)
            style_display = f'{display} - {style}' if len(styles) > 1 else display
            style_aliases = tuple(dict.fromkeys((
                value,
                *(f'{alias}{FONT_STYLE_SEPARATOR}{style}' for alias in aliases if style),
                *(f'{alias} {style}' for alias in aliases if style),
                *(aliases if style == default_style else ()),
            )))
            entries.append((style_display, value, style_aliases, family, style))

    # Qt can expose a style both as ``Family - Light`` and as a compatibility
    # family named ``Family Light``. First form small candidates from family/style
    # tokens, then compare exact faces only inside those candidates.
    known_styles = {
        _search_key(style)
        for _display, _value, _aliases, _family, style in entries
        if style
    } | {'regular', 'normal'}

    def style_hint(family: str, style: str) -> tuple[str, str]:
        family_key = _search_key(family)
        style_key = _search_key(style or 'regular')
        for suffix in sorted(known_styles, key=len, reverse=True):
            marker = f' {suffix}'
            if family_key.endswith(marker):
                family_key = family_key[:-len(marker)]
                if style_key in {'regular', 'normal'}:
                    style_key = suffix
                break
        return family_key, style_key

    candidates: dict[tuple[str, str], list[int]] = {}
    for index, entry in enumerate(entries):
        candidates.setdefault(style_hint(entry[3], entry[4]), []).append(index)

    grouped: dict[tuple[str, tuple | int], list[tuple[str, str, tuple[str, ...], str, str]]] = {}
    for indexes in candidates.values():
        if len(indexes) == 1:
            entry = entries[indexes[0]]
            grouped[('entry', indexes[0])] = [entry]
            continue
        for index in indexes:
            entry = entries[index]
            identity = _font_style_signature(entry[3], entry[4])
            key: tuple[str, tuple | int] = ('face', identity) if identity else ('entry', index)
            grouped.setdefault(key, []).append(entry)

    merged = []
    for group in grouped.values():
        canonical = min(
            group,
            key=lambda entry: (
                len(_search_key(entry[3])),
                _search_key(entry[3]),
                _search_key(entry[4]),
            ),
        )
        aliases = tuple(dict.fromkeys(
            alias
            for _display, _value, entry_aliases, _family, _style in group
            for alias in entry_aliases
        ))
        merged.append((canonical[0], canonical[1], aliases))
    return tuple(sorted(merged, key=lambda entry: (_search_key(entry[0]), _search_key(entry[1]))))


def list_font_style_entries(
    locale_code: str,
    include_system: bool | None = None,
) -> list[tuple[str, str, tuple[str, ...]]]:
    if include_system is None:
        include_system = _SYSTEM_FONTS_ENABLED
    return list(_list_font_style_entries_cached(locale_code, bool(include_system)))


@lru_cache(maxsize=4096)
def _cached_qfont_for_value(value: str) -> QFont:
    family, style = split_font_value(value)
    return QFontDatabase.font(family, style, 12) if style else QFont(family)


def qfont_for_value(value: str) -> QFont:
    """Build a preview font from a persisted family/style selection."""
    return QFont(_cached_qfont_for_value(str(value or "")))


def populate_font_combo(combo, current: str | None = None, locale_code: str = "en_US") -> None:
    """清空并填充字体下拉框：显示本地化名称，userData 保留 family/style。

    ``current`` 传当前 font value 时选中对应条目；
    条目不在列表里则追加一条（userData 保留原值）再选中。
    """
    combo.clear()
    combo._font_search_terms = {}
    combo._font_alias_to_family = {}
    for display, value, aliases in list_font_style_entries(
        locale_code,
        include_system=getattr(combo, "_include_system_fonts", _SYSTEM_FONTS_ENABLED),
    ):
        combo.addItem(display, userData=value)
        combo._font_search_terms[value] = _search_key(" ".join((display, *aliases)))
        for alias in aliases:
            combo._font_alias_to_family.setdefault(_search_key(alias), value)
    if not current:
        return
    current = combo._font_alias_to_family.get(_search_key(current), current)
    for index in range(combo.count()):
        item_data = combo.itemData(index)
        if item_data == current:
            combo.setCurrentIndex(index)
            return
    family, style = split_font_value(current)
    display, aliases = localized_font_family(family, locale_code)
    if style:
        display = f'{display} - {style}'
    combo.addItem(display, userData=current)
    combo._font_search_terms[current] = _search_key(" ".join((display, *aliases)))
    combo.setCurrentIndex(combo.count() - 1)


class _FontMenuModel(QAbstractListModel):
    """Full font catalog model with precomputed searchable metadata."""

    SearchRole = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self._entries = tuple(entries)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        display, value, search_terms = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return display
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == self.SearchRole:
            return search_terms
        if role == Qt.ItemDataRole.SizeHintRole:
            width = self.parent().fontMetrics().horizontalAdvance(display) if self.parent() else len(display) * 8
            return QSize(40 + width, 33)
        return None


class _FontFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""

    def set_filter(self, text: str):
        query = _search_key(text.strip())
        if query == self._query:
            return
        self._query = query
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._query:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        return self._query in str(model.data(index, _FontMenuModel.SearchRole) or "")


class _FontMenuDelegate(IndicatorMenuItemDelegate):
    """Resolve and paint preview fonts only for rows requested by the viewport."""

    def paint(self, painter, option, index):
        value = index.model().data(index, Qt.ItemDataRole.UserRole)
        option.font = _cached_qfont_for_value(str(value or ""))
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        hint = index.model().data(index, Qt.ItemDataRole.SizeHintRole)
        return QSize(max(hint.width(), 1), 33)


class _FontMenuListView(QListView):
    """QListView replacement retaining MenuActionListWidget sizing semantics."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self._itemHeight = 33
        self._maxVisibleItems = -1
        self._font_model = _FontMenuModel(entries, self)
        self._filter_model = _FontFilterProxyModel(self)
        self._filter_model.setSourceModel(self._font_model)
        self.setModel(self._filter_model)
        self.setObjectName("comboListWidget")
        self.setViewportMargins(0, 2, 0, 6)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setItemDelegate(_FontMenuDelegate(self))
        self.scrollDelegate = SmoothScrollDelegate(self)
        self._natural_width = 1

    def recalculateNaturalWidth(self):
        self._natural_width = max(
            (40 + self.fontMetrics().boundingRect(entry[0]).width() for entry in self._font_model._entries),
            default=1,
        )

    def setItemHeight(self, height: int):
        self._itemHeight = int(height)
        self.adjustSize()

    def setMaxVisibleItems(self, num: int):
        self._maxVisibleItems = int(num)
        self.adjustSize()

    def maxVisibleItems(self):
        return self._maxVisibleItems

    def itemsHeight(self):
        rows = self._filter_model.rowCount()
        if self._maxVisibleItems > 0:
            rows = min(rows, self._maxVisibleItems)
        margins = self.viewportMargins()
        return rows * self._itemHeight + margins.top() + margins.bottom()

    def heightForAnimation(self, pos, aniType):
        manager = MenuAnimationManager.make(self, aniType)
        _width, available_height = manager.availableViewSize(pos)
        return min(self.itemsHeight(), available_height)

    def adjustSize(self, pos=None, aniType=MenuAnimationType.NONE):
        manager = MenuAnimationManager.make(self, aniType)
        available_width, available_height = manager.availableViewSize(pos)
        margins = self.viewportMargins()
        width = max(
            min(available_width, self._natural_width + margins.left() + margins.right() + 2),
            self.minimumWidth(),
        )
        height = min(available_height, self.itemsHeight() + 2)
        if self._maxVisibleItems > 0:
            height = min(height, self._maxVisibleItems * self._itemHeight + margins.top() + margins.bottom() + 2)
        self.setFixedSize(max(width, 1), max(height, 1))

    def setCurrentRow(self, source_row: int):
        source_index = self._font_model.index(source_row, 0)
        index = self._filter_model.mapFromSource(source_index)
        if not index.isValid():
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            return
        self.setCurrentIndex(index)
        self.selectionModel().select(index, self.selectionModel().SelectionFlag.ClearAndSelect)

    def set_filter(self, text: str):
        self._filter_model.set_filter(text)

    def source_row(self, index: QModelIndex) -> int:
        if not index.isValid():
            return -1
        return self._filter_model.mapToSource(index).row()




class _FontComboBoxMenu(ComboBoxMenu):
    """Fluent searchable menu backed by a full model and virtualized view."""

    _WINDOW_FLAGS = (
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )

    fontHovered = pyqtSignal(str)
    fontSelected = pyqtSignal(int)

    def __init__(self, font_entries, placeholder, parent=None):
        self._font_entries = tuple(font_entries)
        self._was_activated = False
        super().__init__(parent)
        self.setWindowFlags(self._WINDOW_FLAGS)
        old_view = self.view
        self.hBoxLayout.removeWidget(old_view)
        old_view.hide()
        old_view.deleteLater()
        self.view = _FontMenuListView(self._font_entries, self)
        self.setShadowEffect()
        self._container = QWidget(self)
        self._content_layout = QVBoxLayout(self._container)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        self.search_edit = LineEdit(self._container)
        self.search_edit.setPlaceholderText(placeholder)
        self.search_edit.setClearButtonEnabled(True)
        self._content_layout.addWidget(self.search_edit)
        self._content_layout.addWidget(self.view)
        self.hBoxLayout.addWidget(self._container, 1)
        self.search_edit.textChanged.connect(self._filter_items)
        self.view.entered.connect(self._on_index_entered)
        self.view.clicked.connect(self._on_index_clicked)
        self._apply_view_style()

    def _apply_view_style(self):
        self.view.setStyleSheet(
            self.styleSheet().replace("MenuActionListWidget", "_FontMenuListView")
        )
        self.view.recalculateNaturalWidth()
        self.view.adjustSize()


    def _on_index_entered(self, index):
        row = self.view.source_row(index)
        if 0 <= row < len(self._font_entries):
            self.fontHovered.emit(self._font_entries[row][1])
        else:
            self.fontHovered.emit("")

    def _on_index_clicked(self, index):
        row = self.view.source_row(index)
        if not 0 <= row < len(self._font_entries):
            return
        self._hideMenu(False)
        self.fontSelected.emit(row)

    def leaveEvent(self, event):
        self.fontHovered.emit("")
        super().leaveEvent(event)

    def _filter_items(self, text: str) -> None:
        self.view.set_filter(text)
        self.view.scrollToTop()

    def adjustSize(self):
        if not hasattr(self, "_container"):
            return super().adjustSize()
        margins = self.layout().contentsMargins()
        hint = self._container.sizeHint()
        self.setFixedSize(
            hint.width() + margins.left() + margins.right(),
            hint.height() + margins.top() + margins.bottom(),
        )

    def exec(self, pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        result = super().exec(pos, ani, aniType)
        self.activateWindow()
        QTimer.singleShot(0, self.search_edit.setFocus)
        return result

    def event(self, e):
        result = super().event(e)
        if e.type() == QEvent.Type.WindowActivate:
            self._was_activated = True
        elif e.type() == QEvent.Type.WindowDeactivate and self._was_activated:
            self._hideMenu(True)
        return result

    def mousePressEvent(self, e):
        pass

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._hideMenu(True)
            return
        super().keyPressEvent(e)

    def closeEvent(self, event):
        _stop_popup_animation(self)
        super().closeEvent(event)


class FontComboBox(TopLevelComboBox):
    """QFontComboBox-compatible selector backed by Fluent ComboBox styling."""

    currentFontChanged = pyqtSignal(QFont)
    fontPreviewChanged = pyqtSignal(str)

    def __init__(self, parent=None, locale_getter: Callable[[], str] | None = None):
        self._locale_getter = locale_getter
        self._cached_locale_code: str | None = None
        self._include_system_fonts = _SYSTEM_FONTS_ENABLED
        self._font_search_terms: dict[str, str] = {}
        self._font_alias_to_family: dict[str, str] = {}
        super().__init__(parent)
        _FONT_COMBO_INSTANCES.add(self)
        self.currentIndexChanged.connect(self._emit_current_font_changed)
        self.refresh(QFont().family())

    def _createComboMenu(self):
        locale_code = self._locale_code()
        entries = [
            (
                item.text,
                str(item.userData or item.text),
                self._font_search_terms.get(str(item.userData), _search_key(item.text)),
            )
            for item in self.items
        ]
        menu = _FontComboBoxMenu(
            entries,
            _FONT_SEARCH_PLACEHOLDERS.get(locale_code, _FONT_SEARCH_PLACEHOLDERS["en_US"]),
            self._popup_parent(),
        )
        menu.fontHovered.connect(self.fontPreviewChanged)
        menu.fontSelected.connect(self._on_menu_font_selected)
        menu.closedSignal.connect(lambda: self.fontPreviewChanged.emit(""))
        menu.adjustSize()
        return menu

    def _showComboMenu(self):
        self.refresh()
        if not self.items:
            return
        menu = self._createComboMenu()
        if menu.view.width() < self.width():
            menu.view.setMinimumWidth(self.width())
            menu.view.adjustSize()
            menu.adjustSize()
        menu.setMaxVisibleItems(self.maxVisibleItems())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.closedSignal.connect(self._onDropMenuClosed)
        self.dropMenu = menu
        menu.view.setCurrentRow(self.currentIndex())

        x = -menu.width() // 2 + menu.layout().contentsMargins().left() + self.width() // 2
        pd = self.mapToGlobal(QPoint(x, self.height()))
        hd = menu.view.heightForAnimation(pd, MenuAnimationType.DROP_DOWN)
        pu = self.mapToGlobal(QPoint(x, 0))
        hu = menu.view.heightForAnimation(pu, MenuAnimationType.PULL_UP)
        if hd >= hu:
            menu.view.adjustSize(pd, MenuAnimationType.DROP_DOWN)
            menu.exec(pd, aniType=MenuAnimationType.DROP_DOWN)
        else:
            menu.view.adjustSize(pu, MenuAnimationType.PULL_UP)
            menu.exec(pu, aniType=MenuAnimationType.PULL_UP)

    def _on_menu_font_selected(self, row: int):
        if 0 <= row < self.count():
            self._onItemClicked(row)

    def refresh(self, current_family: str | None = None) -> None:
        family = self.currentFamily() if current_family is None else str(current_family or "")
        blocker = QSignalBlocker(self)
        try:
            populate_font_combo(self, family or None, self._locale_code())
            if not family:
                self.setCurrentIndex(-1)
        finally:
            del blocker

    def refresh_ui_texts(self) -> None:
        self._cached_locale_code = None
        self.refresh()

    def set_include_system_fonts(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._include_system_fonts == enabled:
            return
        current = self.currentFamily()
        self._include_system_fonts = enabled
        self.refresh(current)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Font selection is explicit: scrolling never changes the family."""
        event.ignore()

    def _locale_code(self) -> str:
        if self._cached_locale_code is not None:
            return self._cached_locale_code
        locale_code = ""
        if self._locale_getter is not None:
            try:
                locale_code = str(self._locale_getter() or "")
            except RuntimeError:
                pass
        self._cached_locale_code = locale_code or QLocale.system().name()
        return self._cached_locale_code

    def currentFamily(self) -> str:
        return str(self.currentData() or self.currentText() or "")

    def setCurrentFamily(self, family: str) -> None:
        value = str(family or "")
        if not value:
            self.setCurrentIndex(-1)
            return
        value = self._font_alias_to_family.get(_search_key(value), value)
        index = self.findData(value)
        if index < 0:
            family_name, style = split_font_value(value)
            display, aliases = localized_font_family(family_name, self._locale_code())
            if style:
                display = f"{display} - {style}"
            self.addItem(display, userData=value)
            self._font_search_terms[value] = _search_key(" ".join((display, *aliases, style)))
            index = self.count() - 1
        self.setCurrentIndex(index)

    def currentFont(self) -> QFont:
        return qfont_for_value(self.currentFamily())

    def setCurrentFont(self, font: QFont) -> None:
        self.setCurrentFamily(font_value(font.family(), font.styleName()))

    def _emit_current_font_changed(self, _index: int) -> None:
        self.currentFontChanged.emit(self.currentFont())


def set_system_fonts_enabled(enabled: bool) -> None:
    """Update the shared font source policy and refresh existing selectors."""
    global _SYSTEM_FONTS_ENABLED
    enabled = bool(enabled)
    _SYSTEM_FONTS_ENABLED = enabled
    for combo in list(_FONT_COMBO_INSTANCES):
        try:
            combo.set_include_system_fonts(enabled)
        except RuntimeError:
            continue
