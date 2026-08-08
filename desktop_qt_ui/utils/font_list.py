"""Qt 字体数据库共享 helper。

与 BallonsTranslator 一致：系统字体和应用字体都按 family 展示；项目
``fonts/`` 中的文件仅负责注册进 Qt，不再把文件路径作为编辑器字体值。

注册统一走 ``text_render.register_font_file``：家族名以 ``[`` 开头的字体
（如 "[工具箱]xxx-简繁"）会被 Qt 的 "Family [Foundry]" 语法解析成空家族名，
QFont 匹配固定落到同一字体；注册层会自动改写为去掉方括号的内存副本。
"""
import hashlib
import logging
import os
import unicodedata
import weakref
from collections import Counter
from collections.abc import Callable
from functools import lru_cache

from PyQt6.QtCore import QEvent, QLocale, QSignalBlocker, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication, QRawFont, QWheelEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import LineEdit, MenuAnimationType
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu

from manga_translator.rendering.text_render import (
    qt_family_is_ambiguous,
    register_font_file,
    strip_qt_foundry_brackets,
)
from ui.widgets.wheel_filter import TopLevelComboBox, _stop_popup_animation

from .resource_helper import resource_path

logger = logging.getLogger('manga_translator')

FONT_FILE_EXTENSIONS = ('.ttf', '.otf', '.ttc')
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
                        _remember_original_font_names(path)
                        _font_family_name_records.cache_clear()
                        _font_face_signature.cache_clear()
    except OSError as exc:
        logger.warning(f"扫描字体目录失败: {exc}")
    font_files.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
    return font_files


def list_font_families(include_system: bool | None = None) -> list[str]:
    """返回项目字体和（可选的）系统字体 family 列表。

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
    if include_system is None:
        include_system = _SYSTEM_FONTS_ENABLED
    if not include_system:
        project_families = {
            family
            for families_for_file in _REGISTERED_FONT_FAMILIES.values()
            for family in families_for_file
        }
        families.intersection_update(project_families)
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
def _font_face_signature(family: str) -> bytes:
    """Return a stable signature for the font face selected by a Qt family."""
    try:
        raw_font = QRawFont.fromFont(QFont(family))
        if not raw_font.isValid():
            return b""
        digest = hashlib.sha256()
        found_table = False
        for tag in ("head", "maxp", "name"):
            table = bytes(raw_font.fontTable(tag))
            if not table:
                continue
            found_table = True
            digest.update(tag.encode("ascii"))
            digest.update(len(table).to_bytes(8, "big"))
            digest.update(table)
        return digest.digest() if found_table else b""
    except Exception as exc:
        logger.debug("读取字体面指纹失败 %s: %s", family, exc)
        return b""


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
    candidate_names = [record[2] for record in candidates]
    aliases = tuple(dict.fromkeys([
        family,
        *candidate_names,
        *(_original_font_display_name(name) for name in candidate_names),
    ]))
    return _original_font_display_name(best[2]), aliases


def list_font_family_entries(
    locale_code: str,
    include_system: bool | None = None,
) -> list[tuple[str, str, tuple[str, ...]]]:
    entries = []
    for family in list_font_families(include_system=include_system):
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

    # Some fonts expose both legacy family (name ID 1) and typographic family
    # (name ID 16) as separate Qt families. Merge only when their complete name
    # records overlap and Qt resolves both names to the exact same font face.
    indexes_by_complete_alias: dict[str, list[int]] = {}
    for index, (family, _display, _aliases) in enumerate(entries):
        all_aliases = tuple(dict.fromkeys((
            family,
            *(value for _name_id, _language, value in _font_family_name_records(family)),
        )))
        for alias in all_aliases:
            indexes_by_complete_alias.setdefault(_search_key(alias), []).append(index)

    for indexes in indexes_by_complete_alias.values():
        if len({find(index) for index in indexes}) < 2:
            continue
        first_by_signature: dict[bytes, int] = {}
        for index in indexes:
            signature = _font_face_signature(entries[index][0])
            if not signature:
                continue
            other = first_by_signature.setdefault(signature, index)
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
    for display, family, aliases in list_font_family_entries(
        locale_code,
        include_system=getattr(combo, "_include_system_fonts", _SYSTEM_FONTS_ENABLED),
    ):
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
    """Fluent searchable menu whose rows preview their font family.

    菜单刻意不用 ``Qt.Popup``：Popup 窗口拿不到 Windows 键盘焦点，Qt 的
    ``focusObject`` 会停在主窗口原来那个控件上，于是持有键盘焦点的窗口被解除
    输入法关联——搜索框只剩 ASCII 敲得进去，中文等靠输入法的文字全被挡掉。
    改成普通工具窗口后菜单自己拿焦点，代价是 Popup 白送的"点别处即关闭"要由
    ``event``/``mousePressEvent`` 自己接管。
    """

    _WINDOW_FLAGS = (
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )

    fontHovered = pyqtSignal(str)

    def __init__(self, families, search_terms, placeholder, parent=None):
        self._families = families
        self._search_terms = search_terms
        self._was_activated = False
        super().__init__(parent)
        self.setWindowFlags(self._WINDOW_FLAGS)
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
        self.view.setMouseTracking(True)
        self.view.itemEntered.connect(self._on_item_entered)

    def _on_item_entered(self, item) -> None:
        row = self.view.row(item)
        if 0 <= row < len(self._families):
            self.fontHovered.emit(self._families[row])

    def leaveEvent(self, event):
        self.fontHovered.emit("")
        super().leaveEvent(event)

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
        # 工具窗口不像 Popup 那样自动接管键盘，要主动激活才拿得到输入法。
        self.activateWindow()
        QTimer.singleShot(0, self.search_edit.setFocus)
        return result

    def event(self, e):
        result = super().event(e)
        if e.type() == QEvent.Type.WindowActivate:
            self._was_activated = True
        elif e.type() == QEvent.Type.WindowDeactivate and self._was_activated:
            # 顶替 Popup 的自动收起：点到别的窗口、或切走应用就关闭。激活之前
            # 的失活事件要忽略，否则菜单在拿到焦点前就把自己关了。
            self._hideMenu(True)
        return result

    def mousePressEvent(self, e):
        # 基类靠 Popup 的鼠标捕获在这里收"点到菜单外部"的点击；工具窗口只会收到
        # 菜单内部的点击（例如搜索框四周的留白），不能当成关闭信号。
        pass

    def keyPressEvent(self, e):
        # 焦点在搜索框上，Esc 先到这里；补上 Popup 原本负责的取消语义。
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
        self._include_system_fonts = _SYSTEM_FONTS_ENABLED
        self._font_search_terms: dict[str, str] = {}
        self._font_alias_to_family: dict[str, str] = {}
        super().__init__(parent)
        _FONT_COMBO_INSTANCES.add(self)
        self.currentIndexChanged.connect(self._emit_current_font_changed)
        self.refresh(QFont().family())

    def _createComboMenu(self):
        locale_code = self._locale_code()
        menu = _FontComboBoxMenu(
            [str(item.userData or item.text) for item in self.items],
            [self._font_search_terms.get(str(item.userData), _search_key(item.text)) for item in self.items],
            _FONT_SEARCH_PLACEHOLDERS.get(locale_code, _FONT_SEARCH_PLACEHOLDERS["en_US"]),
            self._popup_parent(),
        )
        menu.fontHovered.connect(self.fontPreviewChanged)
        menu.closedSignal.connect(lambda: self.fontPreviewChanged.emit(""))
        return menu

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
