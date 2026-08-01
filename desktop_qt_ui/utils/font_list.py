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
from collections import Counter
from collections.abc import Callable
from functools import lru_cache

from PyQt6.QtCore import QLocale, QSignalBlocker, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication, QRawFont
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import ComboBox, LineEdit, MenuAnimationType
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu

from manga_translator.rendering.text_render import qt_family_is_ambiguous, register_font_file

from .resource_helper import resource_path

logger = logging.getLogger('manga_translator')

FONT_FILE_EXTENSIONS = ('.ttf', '.otf', '.ttc')
_REGISTERED_FONT_FAMILIES: dict[str, list[str]] = {}
_FONT_SEARCH_PLACEHOLDERS = {
    "zh_CN": "搜索字体…",
    "zh_TW": "搜尋字型…",
    "ja_JP": "フォントを検索…",
    "ko_KR": "글꼴 검색…",
    "es_ES": "Buscar fuentes…",
    "en_US": "Search fonts…",
}


def fonts_directory() -> str:
    """字体目录绝对路径（打包后位于 app.exe 同级）。"""
    return resource_path('fonts')


def list_font_files() -> list[tuple[str, str]]:
    """枚举项目字体文件，并在 Qt 已启动时将它们注册为应用字体。"""
    font_files: list[tuple[str, str]] = []
    try:
        fonts_dir = fonts_directory()
        if os.path.isdir(fonts_dir):
            for filename in os.listdir(fonts_dir):
                if filename.lower().endswith(FONT_FILE_EXTENSIONS):
                    font_files.append((os.path.splitext(filename)[0], filename))
                    path = os.path.normcase(os.path.abspath(os.path.join(fonts_dir, filename)))
                    if QGuiApplication.instance() is not None and path not in _REGISTERED_FONT_FAMILIES:
                        _REGISTERED_FONT_FAMILIES[path] = register_font_file(path)
                        _font_family_name_records.cache_clear()
    except OSError as exc:
        logger.warning(f"扫描字体目录失败: {exc}")
    font_files.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
    return font_files


def list_font_families() -> list[str]:
    """返回系统字体与已注册应用字体的 family 列表。

    过滤掉以 ``[`` 开头的家族名——它们经 Qt 的 foundry 语法解析后家族名为空，
    QFont 选中的永远是错误字体（系统级安装的工具箱字体会产生这类条目）。
    同时排除无法用于任意字号排版的位图字体，避免 Windows DirectWrite 加载旧式
    GDI 字体时持续报 ``CreateFontFaceFromHDC() failed``。
    """
    if QGuiApplication.instance() is None:
        return []
    list_font_files()
    families = {
        name for name in QFontDatabase.families()
        if name and not qt_family_is_ambiguous(name) and QFontDatabase.isScalable(name)
    }
    return sorted(families, key=str.casefold)


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
    aliases = tuple(dict.fromkeys([family, *(record[2] for record in candidates)]))
    return best[2], aliases


def list_font_family_entries(locale_code: str) -> list[tuple[str, str, tuple[str, ...]]]:
    entries = []
    for family in list_font_families():
        display, aliases = localized_font_family(family, locale_code)
        entries.append((family, display, aliases))

    parents = list(range(len(entries)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    first_by_alias: dict[str, int] = {}
    for index, (_family, _display, aliases) in enumerate(entries):
        for alias in aliases:
            other = first_by_alias.setdefault(_search_key(alias), index)
            left, right = find(index), find(other)
            if left != right:
                parents[right] = left

    grouped: dict[int, list[int]] = {}
    for index in range(len(entries)):
        grouped.setdefault(find(index), []).append(index)

    merged = []
    for indexes in grouped.values():
        families = [entries[index][0] for index in indexes]
        canonical = min(families, key=lambda family: (not family.isascii(), _search_key(family)))
        display, _aliases = localized_font_family(canonical, locale_code)
        aliases = tuple(dict.fromkeys(
            alias
            for index in indexes
            for alias in (entries[index][0], *entries[index][2])
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
    return sorted(result, key=lambda entry: (_search_key(entry[0]), _search_key(entry[1])))


def populate_font_combo(combo, current: str | None = None, locale_code: str = "en_US") -> None:
    """清空并填充字体下拉框：显示本地化名称，userData 保留 Qt family。

    ``current`` 传当前 family 时选中对应条目；
    条目不在列表里则追加一条（userData 保留原值）再选中。
    """
    combo.clear()
    combo._font_search_terms = {}
    combo._font_alias_to_family = {}
    for display, family, aliases in list_font_family_entries(locale_code):
        combo.addItem(display, userData=family)
        combo._font_search_terms[family] = _search_key(" ".join((display, *aliases)))
        for alias in aliases:
            combo._font_alias_to_family[_search_key(alias)] = family
    if not current:
        return
    current = combo._font_alias_to_family.get(_search_key(current), current)
    for index in range(combo.count()):
        item_data = combo.itemData(index)
        if item_data == current:
            combo.setCurrentIndex(index)
            return
    display, aliases = localized_font_family(current, locale_code)
    combo.addItem(display, userData=current)
    combo._font_search_terms[current] = _search_key(" ".join((display, *aliases)))
    combo.setCurrentIndex(combo.count() - 1)


class _FontComboBoxMenu(ComboBoxMenu):
    """Fluent searchable menu whose rows preview their font family."""

    def __init__(self, families, search_terms, placeholder, parent=None):
        self._families = families
        self._search_terms = search_terms
        super().__init__(parent)
        self.hBoxLayout.removeWidget(self.view)
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

    def _createActionItem(self, action, before=None):
        row = len(self._actions)
        item = super()._createActionItem(action, before)
        font = QFont(item.font())
        if row < len(self._families):
            font.setFamily(self._families[row])
        item.setFont(font)
        return item

    def _filter_items(self, text: str) -> None:
        query = _search_key(text.strip())
        for row in range(self.view.count()):
            item = self.view.item(row)
            item.setHidden(bool(query) and query not in self._search_terms[row])
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
        QTimer.singleShot(0, self.search_edit.setFocus)
        return result


class FontComboBox(ComboBox):
    """QFontComboBox-compatible selector backed by Fluent ComboBox styling."""

    currentFontChanged = pyqtSignal(QFont)

    def __init__(self, parent=None, locale_getter: Callable[[], str] | None = None):
        self._locale_getter = locale_getter
        self._font_search_terms: dict[str, str] = {}
        self._font_alias_to_family: dict[str, str] = {}
        super().__init__(parent)
        self.currentIndexChanged.connect(self._emit_current_font_changed)
        self.refresh(QFont().family())

    def _createComboMenu(self):
        locale_code = self._locale_code()
        return _FontComboBoxMenu(
            [str(item.userData or item.text) for item in self.items],
            [self._font_search_terms.get(str(item.userData), _search_key(item.text)) for item in self.items],
            _FONT_SEARCH_PLACEHOLDERS.get(locale_code, _FONT_SEARCH_PLACEHOLDERS["en_US"]),
            self,
        )

    def _showComboMenu(self):
        self.refresh()
        super()._showComboMenu()

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
        self.refresh()

    def _locale_code(self) -> str:
        if self._locale_getter is not None:
            try:
                locale_code = str(self._locale_getter() or "")
                if locale_code:
                    return locale_code
            except RuntimeError:
                pass
        return QLocale.system().name()

    def currentFamily(self) -> str:
        return str(self.currentData() or self.currentText() or "")

    def setCurrentFamily(self, family: str) -> None:
        family = str(family or "")
        if not family:
            self.setCurrentIndex(-1)
            return
        family = self._font_alias_to_family.get(_search_key(family), family)
        index = self.findData(family)
        if index < 0:
            display, aliases = localized_font_family(family, self._locale_code())
            self.addItem(display, userData=family)
            self._font_search_terms[family] = _search_key(" ".join((display, *aliases)))
            index = self.count() - 1
        self.setCurrentIndex(index)

    def currentFont(self) -> QFont:
        return QFont(self.currentFamily())

    def setCurrentFont(self, font: QFont) -> None:
        self.setCurrentFamily(font.family())

    def _emit_current_font_changed(self, _index: int) -> None:
        self.currentFontChanged.emit(self.currentFont())
