"""
提示词预览 & 编辑组件
Prompt preview & editor components for the Prompt Management page.
"""
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PopUpAniStackedWidget,
    PrimaryPushButton,
    RoundMenu,
    SegmentedWidget,
    TitleLabel,
)
from qfluentwidgets import (
    ComboBox as QComboBox,
)
from qfluentwidgets import (
    LineEdit as QLineEdit,
)
from qfluentwidgets import (
    PlainTextEdit as QPlainTextEdit,
)
from qfluentwidgets import (
    PushButton as QPushButton,
)
from qfluentwidgets import (
    ScrollArea as QScrollArea,
)
from qfluentwidgets import (
    TableWidget as QTableWidget,
)

from ui.secondary_pages.fluent_dialog import DialogCode, FluentSecondaryDialog
from ui.theme import (
    monospace_font as _monospace_font,
)
from ui.widgets.hover_hint import install_hover_hint

logger = logging.getLogger("manga_translator")

# 模块级翻译函数（由 Panel / Dialog 初始化时设置）
def _current_t(text):
    return text


def _section_label(text: str) -> BodyLabel:
    """可复用的小标题 Label。"""
    lbl = BodyLabel(text)
    return lbl


def _dim_label(text: str) -> CaptionLabel:
    lbl = CaptionLabel(text)
    lbl.setWordWrap(True)
    return lbl


def _body_label(text: str) -> BodyLabel:
    lbl = BodyLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


def _fluent_scroll(parent=None) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    scroll.enableTransparentBackground()
    return scroll


def _make_glossary_table(entries: List[Dict[str, str]]) -> QTableWidget:
    """生成一个只读的 original → translation 表。"""
    table = QTableWidget()
    table.setRowCount(len(entries))
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels([_current_t("Original"), _current_t("Translation")])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)

    for row, entry in enumerate(entries):
        table.setItem(row, 0, QTableWidgetItem(entry.get("original", "")))
        table.setItem(row, 1, QTableWidgetItem(entry.get("translation", "")))

    # auto-size height: header + rows (capped at 300px)
    row_h = 28
    header_h = table.horizontalHeader().height() if table.horizontalHeader().isVisible() else 28
    desired = header_h + row_h * len(entries) + 4
    table.setFixedHeight(min(desired, 300))
    table.verticalHeader().setDefaultSectionSize(row_h)
    return table


def _normalize_person_glossary_entry(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        entry = {}

    nicknames = entry.get("nicknames", [])
    if isinstance(nicknames, str):
        nicknames = [item.strip() for item in nicknames.split(",") if item.strip()]
    elif isinstance(nicknames, list):
        nicknames = [str(item).strip() for item in nicknames if str(item).strip()]
    else:
        nicknames = []

    description = str(
        entry.get("description")
        or entry.get("introduction")
        or entry.get("intro")
        or ""
    ).strip()

    return {
        "original": str(entry.get("original", "")).strip(),
        "translation": str(entry.get("translation", "")).strip(),
        "nicknames": nicknames,
        "description": description,
    }


def _set_person_glossary_row(table: QTableWidget, row: int, entry: Dict[str, Any]):
    normalized = _normalize_person_glossary_entry(entry)
    nicknames_text = ", ".join(normalized["nicknames"])
    description_preview = normalized["description"].replace("\r\n", "\n").replace("\n", " / ")
    values = [
        normalized["original"],
        normalized["translation"],
        nicknames_text,
        description_preview,
    ]

    for col, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setData(Qt.ItemDataRole.UserRole, dict(normalized))
        table.setItem(row, col, item)


def _get_person_glossary_row(table: QTableWidget, row: int) -> Dict[str, Any]:
    if row < 0 or row >= table.rowCount():
        return _normalize_person_glossary_entry({})

    item = table.item(row, 0)
    if item is not None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict):
            return _normalize_person_glossary_entry(payload)

    original = (table.item(row, 0) or QTableWidgetItem("")).text()
    translation = (table.item(row, 1) or QTableWidgetItem("")).text()
    nicknames_text = (table.item(row, 2) or QTableWidgetItem("")).text()
    description = (table.item(row, 3) or QTableWidgetItem("")).text()
    return _normalize_person_glossary_entry({
        "original": original,
        "translation": translation,
        "nicknames": nicknames_text,
        "description": description,
    })


def _make_person_glossary_table(entries: List[Dict[str, Any]], editable: bool = False) -> QTableWidget:
    table = QTableWidget()
    table.setRowCount(len(entries))
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels([
        _current_t("Original"),
        _current_t("Translation"),
        _current_t("Nicknames"),
        _current_t("Introduction"),
    ])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)

    for row, entry in enumerate(entries):
        _set_person_glossary_row(table, row, entry)

    row_h = 28
    header_h = table.horizontalHeader().height() if table.horizontalHeader().isVisible() else 28
    if not editable:
        desired = header_h + row_h * max(len(entries), 1) + 4
        table.setFixedHeight(min(desired, 300))
    table.verticalHeader().setDefaultSectionSize(row_h)
    return table


def _normalize_reference_images(raw_items: Any) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    if not isinstance(raw_items, list):
        return entries

    for item in raw_items:
        if isinstance(item, str):
            path = item.strip()
            if path:
                entries.append({"path": path, "description": ""})
            continue
        if not isinstance(item, dict):
            continue
        path = str(
            item.get("path")
            or item.get("image_path")
            or item.get("file")
            or item.get("value")
            or ""
        ).strip()
        description = str(
            item.get("description")
            or item.get("note")
            or item.get("label")
            or item.get("purpose")
            or ""
        ).strip()
        if path or description:
            entries.append({"path": path, "description": description})
    return entries


def _make_reference_images_table(entries: List[Dict[str, str]], editable: bool = False) -> QTableWidget:
    table = QTableWidget()
    table.setRowCount(len(entries))
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels([_current_t("Path"), _current_t("Description")])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    if not editable:
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

    for row, entry in enumerate(entries):
        table.setItem(row, 0, QTableWidgetItem(entry.get("path", "")))
        table.setItem(row, 1, QTableWidgetItem(entry.get("description", "")))

    row_h = 28
    header_h = table.horizontalHeader().height() if table.horizontalHeader().isVisible() else 28
    desired = header_h + row_h * max(len(entries), 1) + 4
    table.setFixedHeight(min(desired, 260))
    table.verticalHeader().setDefaultSectionSize(row_h)
    return table


def _is_colorizer_structured(data: Any) -> bool:
    return isinstance(data, dict) and any(
        key in data
        for key in ("ai_colorizer_prompt", "colorization_rules", "reference_images")
    )


# ─────────────────────────────────────────────────────────
# PromptPreviewPanel  (右侧结构化预览)
# ─────────────────────────────────────────────────────────
class PromptPreviewPanel(QWidget):
    """
    右侧预览面板。
    - 如果 prompt 文件符合已知格式（有 glossary / project_data），展示结构化预览
    - 否则展示原始文本内容
    """
    edit_requested = pyqtSignal(str)  # file_path

    def __init__(self, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda x: x)
        global _current_t
        _current_t = self._t
        self._current_path: Optional[str] = None
        self._setup_ui()

    # ─── UI 搭建 ───────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._card = CardWidget()
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        # Title row
        title_row = QHBoxLayout()
        self._title_label = TitleLabel(self._t("Prompt Preview"))
        title_row.addWidget(self._title_label, 1)

        self._edit_btn = QPushButton(self._t("Edit"))
        self._edit_btn.setFixedWidth(72)
        self._edit_btn.clicked.connect(self._on_edit_clicked)
        self._edit_btn.setEnabled(False)
        title_row.addWidget(self._edit_btn)
        card_layout.addLayout(title_row)

        card_layout.addWidget(_divider())

        # 文件名
        self._filename_label = _dim_label(self._t("Select a prompt file to preview"))
        card_layout.addWidget(self._filename_label)

        # Scroll area for content
        scroll = _fluent_scroll(self._card)
        self._content_widget = QWidget(scroll)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(8)
        scroll.setWidget(self._content_widget)
        card_layout.addWidget(scroll, 1)

        root.addWidget(self._card)

    def apply_theme(self):
        """主题切换后重建本面板的局部样式。"""
        if self._current_path:
            self.load_file(self._current_path)

    def refresh_ui_texts(self):
        """语言切换后刷新固定文案，并按当前文件重绘内容。"""
        global _current_t
        _current_t = self._t
        self._title_label.setText(self._t("Prompt Preview"))
        self._edit_btn.setText(self._t("Edit"))
        if self._current_path:
            self.load_file(self._current_path)
        else:
            self.clear()

    # ─── 清空 ──────────────────────────────────────────
    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ─── 外部调用：加载文件 ─────────────────────────────
    def load_file(self, file_path: str):
        """加载 prompt 文件并展示预览。"""
        self._current_path = file_path
        self._clear_content()
        self._edit_btn.setEnabled(bool(file_path))

        if not file_path or not os.path.isfile(file_path):
            self._edit_btn.setEnabled(False)
            self._filename_label.setText(self._t("File not found"))
            return

        self._filename_label.setText(os.path.basename(file_path))

        # 尝试解析
        data = self._try_load(file_path)
        if data is not None and self._is_structured(data):
            self._render_structured(data)
        else:
            self._render_raw(file_path)

    def clear(self):
        self._current_path = None
        self._clear_content()
        self._edit_btn.setEnabled(False)
        self._filename_label.setText(self._t("Select a prompt file to preview"))

    # ─── 解析 ──────────────────────────────────────────
    @staticmethod
    def _try_load(path: str) -> Optional[dict]:
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "r", encoding="utf-8") as f:
                if ext in (".yaml", ".yml"):
                    try:
                        import yaml
                        return yaml.safe_load(f)
                    except ImportError:
                        return None
                else:
                    return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _is_structured(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        if _is_colorizer_structured(data):
            return True
        # 只要存在以下任一关键字段就认为是结构化的。
        return any(
            key in data
            for key in (
                "system_prompt",
                "glossary",
                "project_data",
                "style_guide",
                "translation_rules",
            )
        )

    # ─── 结构化渲染 ────────────────────────────────────
    def _render_structured(self, data: dict):
        layout = self._content_layout

        if _is_colorizer_structured(data):
            self._render_colorizer_structured(data)
            return

        # 1. System prompt
        system_prompt = data.get("system_prompt")
        if isinstance(system_prompt, str) and system_prompt.strip():
            layout.addWidget(_section_label("🧭 " + self._t("System Prompt")))
            layout.addWidget(_body_label(system_prompt.strip()))
            layout.addWidget(_divider())

        # 2. Project data
        project = data.get("project_data")
        if isinstance(project, dict):
            title = project.get("title")
            term = project.get("terminology")
            has_project_content = bool(title) or (isinstance(term, dict) and term)
            if has_project_content:
                if title:
                    layout.addWidget(_section_label("📚 " + self._t("Project") + f": {title}"))
                else:
                    layout.addWidget(_section_label("📚 " + self._t("Project Data")))

            if isinstance(term, dict) and term:
                layout.addWidget(_dim_label(self._t("Terminology") + f" ({len(term)})"))
                entries = [{"original": k, "translation": v} for k, v in term.items()]
                layout.addWidget(_make_glossary_table(entries))

            if has_project_content:
                layout.addWidget(_divider())

        # 3. Style Guide
        sg = data.get("style_guide")
        if isinstance(sg, list) and sg:
            layout.addWidget(_section_label("🎨 " + self._t("Style Guide")))
            for item in sg:
                layout.addWidget(_body_label("• " + str(item)))
            layout.addWidget(_divider())

        # 4. Translation Rules
        tr = data.get("translation_rules")
        if isinstance(tr, list) and tr:
            layout.addWidget(_section_label("📏 " + self._t("Translation Rules")))
            for item in tr:
                layout.addWidget(_body_label("• " + str(item)))
            layout.addWidget(_divider())

        # 5. Glossary (auto-extracted)
        glossary = data.get("glossary")
        if isinstance(glossary, dict) and glossary:
            total = sum(len(v) for v in glossary.values() if isinstance(v, list))
            layout.addWidget(_section_label("📖 " + self._t("Glossary") + f" ({total})"))

            if total <= 0:
                layout.addWidget(_dim_label(self._t("No glossary entries")))
                layout.addWidget(_divider())
            else:
                glossary_tab_container = QWidget()
                glossary_tab_layout = QVBoxLayout(glossary_tab_container)
                glossary_tab_layout.setContentsMargins(0, 0, 0, 0)
                glossary_tab_layout.setSpacing(8)
                glossary_segmented = SegmentedWidget(glossary_tab_container)
                glossary_stack = PopUpAniStackedWidget(glossary_tab_container)
                glossary_tab_layout.addWidget(glossary_segmented)
                glossary_tab_layout.addWidget(glossary_stack, 1)

                category_icons = {
                    "Person": "👤",
                    "Location": "📍",
                    "Org": "🏢",
                    "Item": "🔮",
                    "Skill": "⚡",
                    "Creature": "🐾",
                }

                for cat_key in ["Person", "Location", "Org", "Item", "Skill", "Creature"]:
                    entries = glossary.get(cat_key, [])
                    if not isinstance(entries, list) or not entries:
                        continue
                    icon = category_icons.get(cat_key, "")
                    tab_page = QWidget(glossary_stack)
                    tab_lay = QVBoxLayout(tab_page)
                    tab_lay.setContentsMargins(4, 4, 4, 4)
                    tab_lay.addWidget(_make_person_glossary_table(entries) if cat_key == "Person" else _make_glossary_table(entries))
                    route_key = f"glossary_{cat_key}"
                    page_index = glossary_stack.count()
                    glossary_stack.addWidget(tab_page)
                    glossary_segmented.addItem(
                        route_key,
                        f"{icon} {self._t(cat_key)} ({len(entries)})",
                        onClick=lambda key=route_key, index=page_index: (
                            glossary_stack.setCurrentIndex(index),
                            glossary_segmented.setCurrentItem(key),
                        ),
                    )
                    if page_index == 0:
                        glossary_stack.setCurrentIndex(page_index)
                        glossary_segmented.setCurrentItem(route_key)

                # 处理非标准分类
                standard_keys = {"Person", "Location", "Org", "Item", "Skill", "Creature"}
                for cat_key, entries in glossary.items():
                    if cat_key in standard_keys:
                        continue
                    if not isinstance(entries, list) or not entries:
                        continue
                    tab_page = QWidget(glossary_stack)
                    tab_lay = QVBoxLayout(tab_page)
                    tab_lay.setContentsMargins(4, 4, 4, 4)
                    tab_lay.addWidget(_make_person_glossary_table(entries) if cat_key == "Person" else _make_glossary_table(entries))
                    route_key = f"glossary_{cat_key}"
                    page_index = glossary_stack.count()
                    glossary_stack.addWidget(tab_page)
                    glossary_segmented.addItem(
                        route_key,
                        f"{cat_key} ({len(entries)})",
                        onClick=lambda key=route_key, index=page_index: (
                            glossary_stack.setCurrentIndex(index),
                            glossary_segmented.setCurrentItem(key),
                        ),
                    )
                    if page_index == 0:
                        glossary_stack.setCurrentIndex(page_index)
                        glossary_segmented.setCurrentItem(route_key)

                glossary_tab_container.setMinimumHeight(200)
                layout.addWidget(glossary_tab_container)
                layout.addWidget(_divider())

        layout.addStretch()

    # ─── 原始文本渲染 ──────────────────────────────────
    def _render_raw(self, file_path: str):
        layout = self._content_layout
        raw_panel = QWidget(self._content_widget)
        raw_layout = QVBoxLayout(raw_panel)
        raw_layout.setContentsMargins(12, 10, 12, 10)
        raw_layout.setSpacing(8)
        raw_layout.addWidget(_dim_label(self._t("Unrecognized format – showing raw content")))
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            raw = self._t("Error reading file: {error}", error=e)

        text_edit = _styled_text_edit(raw, read_only=True)
        raw_layout.addWidget(text_edit, 1)
        layout.addWidget(raw_panel, 1)

    # ─── 编辑按钮 ──────────────────────────────────────
    def _on_edit_clicked(self):
        if self._current_path:
            self.edit_requested.emit(self._current_path)

    def _render_colorizer_structured(self, data: dict):
        layout = self._content_layout

        prompt_text = data.get("ai_colorizer_prompt")
        if prompt_text:
            layout.addWidget(_section_label("🖌 " + self._t("Prompt Text")))
            layout.addWidget(_body_label(str(prompt_text)))
            layout.addWidget(_divider())

        rules = data.get("colorization_rules")
        if isinstance(rules, list) and rules:
            layout.addWidget(_section_label("🎨 " + self._t("Colorization Rules")))
            for item in rules:
                layout.addWidget(_body_label("• " + str(item)))
            layout.addWidget(_divider())

        reference_images = _normalize_reference_images(data.get("reference_images"))
        if reference_images:
            layout.addWidget(_section_label("🖼 " + self._t("Reference Images") + f" ({len(reference_images)})"))
            layout.addWidget(_make_reference_images_table(reference_images, editable=False))

        layout.addStretch()


# ─────────────────────────────────────────────────────────
# 可编辑 glossary 表格（支持增删行）
# ─────────────────────────────────────────────────────────
def _make_editable_glossary_table(entries: List[Dict[str, str]]) -> QTableWidget:
    """生成一个可编辑的 original → translation 表。"""
    table = QTableWidget()
    table.setRowCount(len(entries))
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels([_current_t("Original"), _current_t("Translation")])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)

    for row, entry in enumerate(entries):
        table.setItem(row, 0, QTableWidgetItem(entry.get("original", "")))
        table.setItem(row, 1, QTableWidgetItem(entry.get("translation", "")))

    row_h = 28
    table.verticalHeader().setDefaultSectionSize(row_h)
    return table


def _set_basic_glossary_row(table: QTableWidget, row: int, entry: Dict[str, Any]):
    normalized = {
        "original": str(entry.get("original", "")).strip(),
        "translation": str(entry.get("translation", "")).strip(),
    }
    table.setItem(row, 0, QTableWidgetItem(normalized["original"]))
    table.setItem(row, 1, QTableWidgetItem(normalized["translation"]))


def _get_basic_glossary_row(table: QTableWidget, row: int) -> Dict[str, str]:
    if row < 0 or row >= table.rowCount():
        return {"original": "", "translation": ""}
    return {
        "original": (table.item(row, 0) or QTableWidgetItem("")).text().strip(),
        "translation": (table.item(row, 1) or QTableWidgetItem("")).text().strip(),
    }


def _styled_text_edit(text: str = "", read_only: bool = False) -> QPlainTextEdit:
    """统一风格的文本编辑框。"""
    te = QPlainTextEdit()
    te.setAutoFillBackground(False)
    te.viewport().setAutoFillBackground(False)
    te.setPlainText(text)
    te.setReadOnly(read_only)
    te.setFont(_monospace_font())
    te.setTabStopDistance(28)
    return te

_GLOSSARY_CATEGORIES = ["Person", "Location", "Org", "Item", "Skill", "Creature"]
_GLOSSARY_CATEGORY_ICONS = {
    "Person": "👤",
    "Location": "📍",
    "Org": "🏢",
    "Item": "🔮",
    "Skill": "⚡",
    "Creature": "🐾",
}


class PersonGlossaryEntryDialog(FluentSecondaryDialog):
    def __init__(
        self,
        entry: Optional[Dict[str, Any]] = None,
        category: str = "Person",
        available_categories: Optional[List[str]] = None,
        t_func: Callable = None,
        parent=None,
    ):
        super().__init__(parent)
        self._t = t_func or (lambda x: x)
        self._entry = _normalize_person_glossary_entry(entry or {})
        self._category = category if category else "Person"
        category_options = available_categories or list(_GLOSSARY_CATEGORIES)
        self._available_categories = list(dict.fromkeys([*category_options, self._category]))
        self._setup_ui()

    def _setup_ui(self):
        self.setMinimumSize(520, 420)
        self.resize(560, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self._title_label = TitleLabel("")
        root.addWidget(self._title_label)
        root.addWidget(_divider())

        root.addWidget(_dim_label(self._t("Category")))
        self._category_combo = QComboBox()
        for item in self._available_categories:
            self._category_combo.addItem(self._t(item), userData=item)
        combo_index = self._category_combo.findData(self._category)
        if combo_index >= 0:
            self._category_combo.setCurrentIndex(combo_index)
        self._category_combo.currentIndexChanged.connect(self._sync_category_ui)
        root.addWidget(self._category_combo)

        root.addWidget(_dim_label(self._t("Original")))
        self._original_edit = QLineEdit()
        self._original_edit.setText(self._entry.get("original", ""))
        root.addWidget(self._original_edit)

        root.addWidget(_dim_label(self._t("Translation")))
        self._translation_edit = QLineEdit()
        self._translation_edit.setText(self._entry.get("translation", ""))
        root.addWidget(self._translation_edit)

        self._person_fields = QWidget()
        person_layout = QVBoxLayout(self._person_fields)
        person_layout.setContentsMargins(0, 0, 0, 0)
        person_layout.setSpacing(10)

        person_layout.addWidget(_dim_label(self._t("Nicknames")))
        self._nicknames_edit = QLineEdit()
        self._nicknames_edit.setText(", ".join(self._entry.get("nicknames", [])))
        person_layout.addWidget(self._nicknames_edit)

        person_layout.addWidget(_dim_label(self._t("Introduction")))
        self._description_edit = _styled_text_edit(self._entry.get("description", ""))
        self._description_edit.setFixedHeight(160)
        person_layout.addWidget(self._description_edit, 1)
        root.addWidget(self._person_fields, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(self._t("Cancel"))
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)

        save_btn = PrimaryPushButton(self._t("Save"))
        save_btn.setFixedWidth(100)
        save_btn.clicked.connect(self.accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)
        self._sync_category_ui()

    def _current_category(self) -> str:
        category = self._category_combo.currentData()
        if not isinstance(category, str) or not category:
            return self._category
        return category

    def _sync_category_ui(self):
        is_person = self._current_category() == "Person"
        self._person_fields.setVisible(is_person)
        title_text = self._t(self._current_category()) + " · " + self._t("Edit")
        self.setWindowTitle(title_text)
        self._title_label.setText(title_text)

    def get_entry(self) -> Dict[str, Any]:
        nicknames = [item.strip() for item in self._nicknames_edit.text().split(",") if item.strip()]
        return _normalize_person_glossary_entry({
            "original": self._original_edit.text(),
            "translation": self._translation_edit.text(),
            "nicknames": nicknames,
            "description": self._description_edit.toPlainText(),
        })

    def get_category(self) -> str:
        return self._current_category()


class PromptEditorDialog(FluentSecondaryDialog):
    """
    弹窗式编辑器，支持两种模式：
    - 模板编辑 (Tab 1): 结构化表单编辑各字段
    - 自由编辑 (Tab 2): 直接编辑原始文本
    不符合格式的文件只显示自由编辑 Tab。
    """

    def __init__(self, file_path: str, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda x: x)
        global _current_t
        _current_t = self._t
        self._file_path = file_path
        self._original_content = ""
        self._data: Optional[dict] = None  # 解析后的结构化数据
        self._is_structured = False
        self._template_dirty = False  # 模板 tab 是否有修改
        self._free_dirty = False  # 自由 tab 是否有修改
        self._was_saved = False

        # 模板编辑的控件引用
        self._system_prompt_edit: Optional[QPlainTextEdit] = None
        self._style_guide_edit: Optional[QPlainTextEdit] = None
        self._rules_edit: Optional[QPlainTextEdit] = None
        self._term_table: Optional[QTableWidget] = None
        self._title_edit = None
        self._glossary_tables: Dict[str, QTableWidget] = {}
        self._glossary_tab_segmented: Optional[SegmentedWidget] = None
        self._glossary_tab_stack: Optional[PopUpAniStackedWidget] = None
        self._glossary_tab_pages: Dict[str, QWidget] = {}

        self._setup_ui()
        self._load_file()

    # ─── UI ────────────────────────────────────────────
    def _setup_ui(self):
        self.setWindowTitle(self._t("Edit Prompt") + f" – {os.path.basename(self._file_path)}")
        self.setMinimumSize(820, 580)
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = TitleLabel(self._t("Edit Prompt"))
        hdr.addWidget(title, 1)
        hdr.addWidget(_dim_label(os.path.basename(self._file_path)))
        root.addLayout(hdr)
        root.addWidget(_divider())

        # Tabs
        self._tab_segmented = SegmentedWidget(self)
        self._tab_stack = PopUpAniStackedWidget(self)
        root.addWidget(self._tab_segmented)
        root.addWidget(self._tab_stack, 1)

        # Status
        self._status = _dim_label("")
        root.addWidget(self._status)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton(self._t("Cancel"))
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(self.reject)

        self._save_btn = PrimaryPushButton(self._t("Save"))
        self._save_btn.setFixedWidth(100)
        self._save_btn.clicked.connect(self._save)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

    # ─── 加载 ──────────────────────────────────────────
    def _load_file(self):
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                self._original_content = f.read()
        except Exception as e:
            self._original_content = ""
            self._status.setText(self._t("Error: {error}", error=e))

        # 尝试解析
        self._data = PromptPreviewPanel._try_load(self._file_path)
        self._is_structured = (
            self._data is not None and PromptPreviewPanel._is_structured(self._data)
        )

        if self._is_structured:
            self._build_template_tab()

        self._build_free_tab()
        self._status.setText(self._t("Loaded successfully"))

    # ─── 模板编辑 Tab ──────────────────────────────────
    def _build_template_tab(self):
        page = _fluent_scroll(self)
        content = QWidget(page)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # 保存 layout 引用，供动态添加字段用
        self._template_layout = layout
        self._template_sections_layout = QVBoxLayout()
        self._template_sections_layout.setContentsMargins(0, 0, 0, 0)
        self._template_sections_layout.setSpacing(10)
        layout.addLayout(self._template_sections_layout)
        # 有序容器列表 [(key, container_widget), ...]
        self._section_containers: list = []

        data = self._data

        # System prompt
        if isinstance(data, dict) and "system_prompt" in data:
            self._insert_section("system_prompt", text=str(data.get("system_prompt") or ""))

        # Project title
        project = data.get("project_data")
        if isinstance(project, dict):
            title = str(project.get("title", "")).strip()
            if title:
                self._insert_section("project_title", title=title)

            # Terminology
            term = project.get("terminology")
            if isinstance(term, dict):
                self._insert_section("terminology", term=term)

        # Style Guide
        sg = data.get("style_guide")
        if isinstance(sg, list):
            self._insert_section("style_guide", rules=sg)

        # Translation Rules
        tr = data.get("translation_rules")
        if isinstance(tr, list):
            self._insert_section("translation_rules", rules=tr)

        # Glossary
        glossary = data.get("glossary")
        if isinstance(glossary, dict):
            self._insert_section("glossary", glossary=glossary)

        # ── "+ 添加字段" 按钮 ──
        self._add_section_btn = QPushButton("＋ " + self._t("Add Section"))
        self._add_section_btn.clicked.connect(self._show_add_section_menu)
        layout.addWidget(self._add_section_btn)

        layout.addStretch()
        page.setWidget(content)
        route_key = "template_edit"
        page_index = self._tab_stack.count()
        self._tab_stack.addWidget(page)
        self._tab_segmented.addItem(
            route_key,
            "📝 " + self._t("Template Edit"),
            onClick=lambda: (
                self._tab_stack.setCurrentIndex(page_index),
                self._tab_segmented.setCurrentItem(route_key),
            ),
        )
        self._tab_stack.setCurrentIndex(page_index)
        self._tab_segmented.setCurrentItem(route_key)

    # ─── 容器创建 & 操作栏 ─────────────────────────────
    _SECTION_META = {
        "system_prompt":     ("🧭", "System Prompt"),
        "project_title":     ("📚", "Project Title"),
        "terminology":       ("📝", "Terminology"),
        "style_guide":       ("🎨", "Style Guide"),
        "translation_rules": ("📏", "Translation Rules"),
        "glossary":          ("📖", "Glossary"),
    }

    def _make_section_container(self, key: str) -> tuple:
        """创建带操作栏的容器 Widget，返回 (container, body_layout)。"""
        icon, label = self._SECTION_META.get(key, ("📌", key))
        container = CardWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        # 标题行
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_lbl = _section_label(f"{icon} {self._t(label)}")
        header.addWidget(title_lbl)
        header.addStretch()

        btn_up = QPushButton("▲")
        btn_up.setFixedSize(28, 24)
        btn_up.clicked.connect(lambda checked=False, c=container: self._request_move_section(c, -1))
        install_hover_hint(btn_up, self._t("Move Up"))

        btn_down = QPushButton("▼")
        btn_down.setFixedSize(28, 24)
        btn_down.clicked.connect(lambda checked=False, c=container: self._request_move_section(c, 1))
        install_hover_hint(btn_down, self._t("Move Down"))

        btn_del = QPushButton("×")
        btn_del.setFixedSize(28, 24)
        btn_del.clicked.connect(lambda: self._remove_section(container, key))
        install_hover_hint(btn_del, self._t("Delete"))
        container._move_up_button = btn_up
        container._move_down_button = btn_down

        header.addWidget(btn_up)
        header.addWidget(btn_down)
        header.addWidget(btn_del)
        outer.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        outer.addLayout(body)
        outer.addWidget(_divider())

        return container, body

    def _insert_section(self, key: str, idx: int = -1, **kwargs):
        """创建并插入一个字段区域到 layout。"""
        container, body = self._make_section_container(key)

        # 根据 key 填充 body
        if key == "system_prompt":
            self._fill_system_prompt(body, kwargs.get("text", ""))
        elif key == "project_title":
            self._fill_project_title(body, kwargs.get("title", ""))
        elif key == "terminology":
            self._fill_terminology(body, kwargs.get("term", {}))
        elif key == "style_guide":
            self._fill_style_guide(body, kwargs.get("rules", []))
        elif key == "translation_rules":
            self._fill_translation_rules(body, kwargs.get("rules", []))
        elif key == "glossary":
            self._fill_glossary(body, kwargs.get("glossary", {}))

        section_layout = self._template_sections_layout
        if idx < 0:
            section_layout.addWidget(container)
            self._section_containers.append((key, container))
        else:
            section_layout.insertWidget(idx, container)
            self._section_containers.insert(idx, (key, container))
        self._refresh_section_move_buttons()

    # ─── 各字段的填充方法 ──────────────────────────────
    def _fill_system_prompt(self, layout: QVBoxLayout, text: str = ""):
        self._system_prompt_edit = _styled_text_edit(text)
        self._system_prompt_edit.setFixedHeight(180)
        layout.addWidget(self._system_prompt_edit)

    def _fill_project_title(self, layout: QVBoxLayout, title: str = ""):
        self._title_edit = QLineEdit()
        self._title_edit.setText(title)
        layout.addWidget(self._title_edit)

    def _fill_terminology(self, layout: QVBoxLayout, term: dict = None):
        if term is None:
            term = {}
        entries = [{"original": k, "translation": v} for k, v in term.items()]
        self._term_table = _make_editable_glossary_table(entries)
        self._term_table.setMinimumHeight(100)
        layout.addWidget(self._term_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ " + self._t("Add Row"))
        add_btn.clicked.connect(lambda: self._add_table_row(self._term_table, 2))
        up_btn = QPushButton("↑ " + self._t("Move Up"))
        up_btn.clicked.connect(lambda: self._move_table_row(self._term_table, -1))
        down_btn = QPushButton("↓ " + self._t("Move Down"))
        down_btn.clicked.connect(lambda: self._move_table_row(self._term_table, 1))
        del_btn = QPushButton("- " + self._t("Delete Row"))
        del_btn.clicked.connect(lambda: self._del_table_row(self._term_table))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _fill_style_guide(self, layout: QVBoxLayout, rules: list = None):
        layout.addWidget(_dim_label(self._t("One rule per line")))
        text = "\n".join(str(x) for x in rules) if rules else ""
        self._style_guide_edit = _styled_text_edit(text)
        self._style_guide_edit.setFixedHeight(100)
        layout.addWidget(self._style_guide_edit)

    def _fill_translation_rules(self, layout: QVBoxLayout, rules: list = None):
        layout.addWidget(_dim_label(self._t("One rule per line")))
        text = "\n".join(str(x) for x in rules) if rules else ""
        self._rules_edit = _styled_text_edit(text)
        self._rules_edit.setFixedHeight(100)
        layout.addWidget(self._rules_edit)

    def _fill_glossary(self, layout: QVBoxLayout, glossary: dict = None):
        if glossary is None:
            glossary = {}

        glossary_tabs = QWidget()
        glossary_tabs_layout = QVBoxLayout(glossary_tabs)
        glossary_tabs_layout.setContentsMargins(0, 0, 0, 0)
        glossary_tabs_layout.setSpacing(8)
        self._glossary_tab_segmented = SegmentedWidget(glossary_tabs)
        self._glossary_tab_stack = PopUpAniStackedWidget(glossary_tabs)
        glossary_tabs_layout.addWidget(self._glossary_tab_segmented)
        glossary_tabs_layout.addWidget(self._glossary_tab_stack, 1)
        glossary_tabs.setMinimumHeight(220)
        self._glossary_tables = {}
        self._glossary_tab_pages = {}

        all_cats = list(dict.fromkeys(
            [c for c in _GLOSSARY_CATEGORIES if c in glossary] +
            [c for c in glossary if c not in _GLOSSARY_CATEGORIES]
        ))
        if not all_cats:
            all_cats = list(_GLOSSARY_CATEGORIES)

        for cat_key in all_cats:
            entries = glossary.get(cat_key, [])
            if not isinstance(entries, list):
                entries = []
            self._add_glossary_category_tab(cat_key, entries)

        layout.addWidget(glossary_tabs)

    def _glossary_tab_title(self, cat_key: str, count: int) -> str:
        icon = _GLOSSARY_CATEGORY_ICONS.get(cat_key, "📌")
        return f"{icon} {self._t(cat_key)} ({count})"

    def _glossary_category_options(self) -> List[str]:
        categories = list(_GLOSSARY_CATEGORIES)
        for cat_key in self._glossary_tables:
            if cat_key not in categories:
                categories.append(cat_key)
        return categories

    def _add_glossary_category_tab(self, cat_key: str, entries: Optional[List[Dict[str, Any]]] = None) -> QTableWidget:
        if self._glossary_tab_segmented is None or self._glossary_tab_stack is None:
            raise RuntimeError("Glossary tab widget is not initialized")

        if cat_key in self._glossary_tables:
            return self._glossary_tables[cat_key]

        normalized_entries = entries if isinstance(entries, list) else []
        tab_page = QWidget(self._glossary_tab_stack)
        tab_lay = QVBoxLayout(tab_page)
        tab_lay.setContentsMargins(6, 6, 6, 6)
        tab_lay.setSpacing(6)

        if cat_key == "Person":
            tbl = _make_person_glossary_table(normalized_entries, editable=True)
            tbl.itemDoubleClicked.connect(
                lambda item, category=cat_key, t=tbl: self._edit_person_glossary_row(category, t, item.row())
            )
            tab_lay.addWidget(_dim_label(self._t("Double-click a row to edit details")))
        else:
            tbl = _make_editable_glossary_table(normalized_entries)

        tbl.setMinimumHeight(120)
        self._glossary_tables[cat_key] = tbl
        self._glossary_tab_pages[cat_key] = tab_page
        tab_lay.addWidget(tbl)

        g_btn_row = QHBoxLayout()
        add_btn = QPushButton("+ " + self._t("Add Row"))
        g_btn_row.addWidget(add_btn)
        if cat_key == "Person":
            add_btn.clicked.connect(lambda checked=False, category=cat_key, t=tbl: self._add_person_glossary_row(category, t))
            edit_btn = QPushButton(self._t("Edit"))
            edit_btn.clicked.connect(
                lambda checked=False, category=cat_key, t=tbl: self._edit_selected_person_glossary_row(category, t)
            )
            g_btn_row.addWidget(edit_btn)
        else:
            add_btn.clicked.connect(
                lambda checked=False, category=cat_key, t=tbl: self._add_basic_glossary_row(category, t)
            )
        move_up_btn = QPushButton("↑ " + self._t("Move Up"))
        move_up_btn.clicked.connect(lambda checked=False, t=tbl: self._move_table_row(t, -1))
        g_btn_row.addWidget(move_up_btn)
        move_down_btn = QPushButton("↓ " + self._t("Move Down"))
        move_down_btn.clicked.connect(lambda checked=False, t=tbl: self._move_table_row(t, 1))
        g_btn_row.addWidget(move_down_btn)
        del_btn = QPushButton("- " + self._t("Delete Row"))
        del_btn.clicked.connect(lambda checked=False, category=cat_key, t=tbl: self._delete_glossary_row(category, t))
        g_btn_row.addWidget(del_btn)
        g_btn_row.addStretch()
        tab_lay.addLayout(g_btn_row)

        route_key = f"glossary_{cat_key}"
        page_index = self._glossary_tab_stack.count()
        self._glossary_tab_stack.addWidget(tab_page)
        self._glossary_tab_segmented.addItem(
            route_key,
            self._glossary_tab_title(cat_key, tbl.rowCount()),
            onClick=lambda key=route_key, index=page_index: (
                self._glossary_tab_stack.setCurrentIndex(index),
                self._glossary_tab_segmented.setCurrentItem(key),
            ),
        )
        if page_index == 0:
            self._glossary_tab_stack.setCurrentIndex(page_index)
            self._glossary_tab_segmented.setCurrentItem(route_key)
        return tbl

    def _ensure_glossary_category_tab(self, cat_key: str) -> QTableWidget:
        if cat_key in self._glossary_tables:
            return self._glossary_tables[cat_key]
        table = self._add_glossary_category_tab(cat_key, [])
        self._refresh_glossary_tab_titles()
        return table

    def _refresh_glossary_tab_titles(self):
        if self._glossary_tab_segmented is None:
            return
        for cat_key in self._glossary_tab_pages:
            row_count = self._glossary_tables.get(cat_key).rowCount() if cat_key in self._glossary_tables else 0
            self._glossary_tab_segmented.setItemText(
                f"glossary_{cat_key}",
                self._glossary_tab_title(cat_key, row_count),
            )

    def _delete_glossary_row(self, category: str, table: QTableWidget):
        self._del_table_row(table)
        if category in self._glossary_tables:
            self._refresh_glossary_tab_titles()

    def _add_basic_glossary_row(self, category: str, table: QTableWidget):
        self._add_table_row(table, 2)
        if category in self._glossary_tables:
            self._refresh_glossary_tab_titles()

    def _apply_person_glossary_result(
        self,
        source_category: str,
        source_table: QTableWidget,
        source_row: Optional[int],
        dialog: PersonGlossaryEntryDialog,
    ):
        target_category = dialog.get_category()
        entry = dialog.get_entry()
        target_table = self._ensure_glossary_category_tab(target_category)

        if source_row is not None and source_row >= 0 and source_category == target_category:
            _set_person_glossary_row(target_table, source_row, entry)
            target_table.selectRow(source_row)
        else:
            target_row = target_table.rowCount()
            target_table.insertRow(target_row)
            if target_category == "Person":
                _set_person_glossary_row(target_table, target_row, entry)
            else:
                _set_basic_glossary_row(target_table, target_row, entry)

            if source_row is not None and source_row >= 0:
                source_table.removeRow(source_row)

            page = self._glossary_tab_pages.get(target_category)
            if page is not None and self._glossary_tab_stack is not None and self._glossary_tab_segmented is not None:
                self._glossary_tab_stack.setCurrentWidget(page)
                self._glossary_tab_segmented.setCurrentItem(f"glossary_{target_category}")
            target_table.selectRow(target_row)

        self._refresh_glossary_tab_titles()

    def _add_person_glossary_row(self, category: str, table: QTableWidget):
        dialog = PersonGlossaryEntryDialog(
            category=category,
            available_categories=self._glossary_category_options(),
            t_func=self._t,
            parent=self,
        )
        if dialog.exec() != DialogCode.Accepted:
            return
        self._apply_person_glossary_result(category, table, None, dialog)

    def _edit_selected_person_glossary_row(self, category: str, table: QTableWidget):
        row = table.currentRow()
        if row < 0:
            return
        self._edit_person_glossary_row(category, table, row)

    def _edit_person_glossary_row(self, category: str, table: QTableWidget, row: int):
        if row < 0:
            return
        dialog = PersonGlossaryEntryDialog(
            _get_person_glossary_row(table, row),
            category=category,
            available_categories=self._glossary_category_options(),
            t_func=self._t,
            parent=self,
        )
        if dialog.exec() != DialogCode.Accepted:
            return
        self._apply_person_glossary_result(category, table, row, dialog)

    # ─── 字段操作：移动 & 删除 ─────────────────────────
    def _refresh_section_move_buttons(self):
        total = len(self._section_containers)
        for index, (_, container) in enumerate(self._section_containers):
            up_button = getattr(container, "_move_up_button", None)
            down_button = getattr(container, "_move_down_button", None)
            if up_button is not None:
                up_button.setEnabled(total > 1 and index > 0)
            if down_button is not None:
                down_button.setEnabled(total > 1 and index < total - 1)

    def _section_order_snapshot(self) -> List[str]:
        return [key for key, _ in self._section_containers]

    def _section_key_for_container(self, container: QWidget) -> str:
        for key, registered_container in self._section_containers:
            if registered_container is container:
                return key
        return "<unknown>"

    def _layout_section_order_snapshot(self) -> List[str]:
        order: List[str] = []
        layout = getattr(self, "_template_sections_layout", None)
        if layout is None:
            return order
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            order.append(self._section_key_for_container(widget))
        return order

    def _request_move_section(self, container: QWidget, direction: int):
        key = self._section_key_for_container(container)
        logger.info(
            "Prompt editor move button clicked: file=%s key=%s direction=%s order=%s layout=%s",
            self._file_path,
            key,
            direction,
            self._section_order_snapshot(),
            self._layout_section_order_snapshot(),
        )
        self._move_section(container, direction)

    def _reflow_section_widgets(self):
        layout = self._template_sections_layout
        logger.info(
            "Prompt editor reflow start: file=%s order=%s layout_before=%s",
            self._file_path,
            self._section_order_snapshot(),
            self._layout_section_order_snapshot(),
        )
        for _, widget in self._section_containers:
            layout.removeWidget(widget)
        for _, widget in self._section_containers:
            layout.addWidget(widget)
            widget.show()
        self._refresh_section_move_buttons()
        layout.invalidate()
        layout.activate()
        if self._tab_stack is not None:
            self._tab_stack.update()
        logger.info(
            "Prompt editor reflow end: file=%s order=%s layout_after=%s",
            self._file_path,
            self._section_order_snapshot(),
            self._layout_section_order_snapshot(),
        )

    def _move_section(self, container: QWidget, direction: int):
        """direction: -1=上移, +1=下移"""
        idx = None
        for i, (k, c) in enumerate(self._section_containers):
            if c is container:
                idx = i
                break
        if idx is None:
            logger.warning(
                "Prompt editor move ignored: file=%s container not found direction=%s order=%s",
                self._file_path,
                direction,
                self._section_order_snapshot(),
            )
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._section_containers):
            logger.info(
                "Prompt editor move ignored: file=%s key=%s from=%s to=%s order=%s",
                self._file_path,
                self._section_containers[idx][0],
                idx,
                new_idx,
                self._section_order_snapshot(),
            )
            return

        # 交换 list
        logger.info(
            "Prompt editor move apply: file=%s key=%s from=%s to=%s order_before=%s",
            self._file_path,
            self._section_containers[idx][0],
            idx,
            new_idx,
            self._section_order_snapshot(),
        )
        self._section_containers[idx], self._section_containers[new_idx] = \
            self._section_containers[new_idx], self._section_containers[idx]
        self._reflow_section_widgets()

    def _remove_section(self, container: QWidget, key: str):
        """删除字段区域并清空对应控件引用。"""
        # 从列表中移除
        self._section_containers = [(k, c) for k, c in self._section_containers if c is not container]

        # 从 layout 中移除
        self._template_sections_layout.removeWidget(container)
        container.setParent(None)
        container.deleteLater()
        self._refresh_section_move_buttons()

        # 清空控件引用
        if key == "system_prompt":
            self._system_prompt_edit = None
        elif key == "project_title":
            self._title_edit = None
        elif key == "terminology":
            self._term_table = None
        elif key == "style_guide":
            self._style_guide_edit = None
        elif key == "translation_rules":
            self._rules_edit = None
        elif key == "glossary":
            self._glossary_tables.clear()
            self._glossary_tab_segmented = None
            self._glossary_tab_stack = None
            self._glossary_tab_pages.clear()

    # ─── 添加字段菜单 ──────────────────────────────────
    _SECTION_DEFS = [
        ("system_prompt",     "🧭", "System Prompt"),
        ("project_title",     "📚", "Project Title"),
        ("terminology",       "📝", "Terminology"),
        ("style_guide",       "🎨", "Style Guide"),
        ("translation_rules", "📏", "Translation Rules"),
        ("glossary",          "📖", "Glossary"),
    ]

    def _get_existing_sections(self) -> set:
        return {k for k, _ in self._section_containers}

    def _show_add_section_menu(self):
        menu = RoundMenu(parent=self)
        existing = self._get_existing_sections()
        has_items = False
        for key, icon, label in self._SECTION_DEFS:
            if key not in existing:
                action = Action(f"{icon}  {self._t(label)}", self)
                action.triggered.connect(lambda checked=False, k=key: self._on_add_section(k))
                menu.addAction(action)
                has_items = True

        if not has_items:
            action = Action(self._t("All sections added"), self)
            action.setEnabled(False)
            menu.addAction(action)

        menu.exec(self._add_section_btn.mapToGlobal(
            self._add_section_btn.rect().topLeft()
        ))

    def _on_add_section(self, key: str):
        """在"添加字段"按钮上方插入新的字段区域。"""
        self._insert_section(key)

    # ─── 自由编辑 Tab ──────────────────────────────────
    def _build_free_tab(self):
        page = QWidget(self._tab_stack)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(12, 10, 12, 10)
        page_layout.setSpacing(6)
        page_layout.addWidget(_dim_label(self._t("Edit the raw file content directly")))
        self._free_editor = _styled_text_edit(self._original_content)
        page_layout.addWidget(self._free_editor, 1)
        route_key = "raw_edit"
        page_index = self._tab_stack.count()
        self._tab_stack.addWidget(page)
        self._tab_segmented.addItem(
            route_key,
            "📄 " + self._t("Raw Edit"),
            onClick=lambda: (
                self._tab_stack.setCurrentIndex(page_index),
                self._tab_segmented.setCurrentItem(route_key),
            ),
        )
        if page_index == 0:
            self._tab_stack.setCurrentIndex(page_index)
            self._tab_segmented.setCurrentItem(route_key)

    # ─── 表格行增删 ────────────────────────────────────
    @staticmethod
    def _add_table_row(table: QTableWidget, cols: int):
        row = table.rowCount()
        table.insertRow(row)
        for c in range(cols):
            table.setItem(row, c, QTableWidgetItem(""))

    @staticmethod
    def _del_table_row(table: QTableWidget):
        rows = sorted(set(idx.row() for idx in table.selectedIndexes()), reverse=True)
        if not rows:
            last = table.rowCount() - 1
            if last >= 0:
                rows = [last]
        for r in rows:
            table.removeRow(r)

    @staticmethod
    def _move_table_row(table: Optional[QTableWidget], direction: int) -> bool:
        if table is None or table.rowCount() <= 1:
            return False

        current_row = table.currentRow()
        if current_row < 0:
            selected_rows = sorted({index.row() for index in table.selectedIndexes()})
            if not selected_rows:
                return False
            current_row = selected_rows[0]

        target_row = current_row + direction
        if target_row < 0 or target_row >= table.rowCount():
            return False

        column_count = table.columnCount()
        current_items = [table.takeItem(current_row, col) for col in range(column_count)]
        target_items = [table.takeItem(target_row, col) for col in range(column_count)]

        for col, item in enumerate(target_items):
            if item is not None:
                table.setItem(current_row, col, item)
        for col, item in enumerate(current_items):
            if item is not None:
                table.setItem(target_row, col, item)

        table.clearSelection()
        table.selectRow(target_row)
        table.setCurrentCell(target_row, 0)
        return True

    # ─── 从模板收集数据 ────────────────────────────────
    def _collect_template_data(self) -> dict:
        """从模板编辑控件收集数据，并按当前 section 顺序重建结构。"""
        base_data = self._data if isinstance(self._data, dict) else {}
        managed_keys = {
            "system_prompt",
            "project_data",
            "style_guide",
            "translation_rules",
            "glossary",
            "output_format",
            "persona",
        }
        passthrough_items = [(key, value) for key, value in base_data.items() if key not in managed_keys]
        section_order = [key for key, _ in self._section_containers]
        data: Dict[str, Any] = {}

        base_project = base_data.get("project_data", {})
        if not isinstance(base_project, dict):
            base_project = {}
        passthrough_project_items = [
            (key, value)
            for key, value in base_project.items()
            if key not in {"title", "terminology", "character_list"}
        ]

        project_section_order = [key for key in section_order if key in {"project_title", "terminology"}]
        project_data: Optional[Dict[str, Any]] = None
        if project_section_order or passthrough_project_items:
            project_data = {}
            for key in project_section_order:
                if key == "project_title" and self._title_edit is not None:
                    project_data["title"] = self._title_edit.text()
                elif key == "terminology" and self._term_table is not None:
                    terms = {}
                    for row in range(self._term_table.rowCount()):
                        entry = _get_basic_glossary_row(self._term_table, row)
                        if entry["original"]:
                            terms[entry["original"]] = entry["translation"]
                    project_data["terminology"] = terms
            for key, value in passthrough_project_items:
                project_data[key] = value

        glossary_data: Optional[Dict[str, List[Dict[str, Any]]]] = None
        if "glossary" in section_order:
            glossary_data = {}
            for cat_key, tbl in self._glossary_tables.items():
                entries: List[Dict[str, Any]] = []
                if cat_key == "Person":
                    for row in range(tbl.rowCount()):
                        person_entry = _get_person_glossary_row(tbl, row)
                        if person_entry["original"]:
                            item: Dict[str, Any] = {
                                "original": person_entry["original"],
                                "translation": person_entry["translation"],
                            }
                            if person_entry["nicknames"]:
                                item["nicknames"] = person_entry["nicknames"]
                            if person_entry["description"]:
                                item["description"] = person_entry["description"]
                            entries.append(item)
                else:
                    for row in range(tbl.rowCount()):
                        entry = _get_basic_glossary_row(tbl, row)
                        if entry["original"]:
                            entries.append(entry)
                # 保留空分类，避免保存后 glossary 被塌缩成 {}。
                glossary_data[cat_key] = entries

        project_inserted = False
        for key in section_order:
            if key == "system_prompt" and self._system_prompt_edit is not None:
                data["system_prompt"] = self._system_prompt_edit.toPlainText()
                continue
            if key in {"project_title", "terminology"}:
                if not project_inserted and project_data is not None:
                    data["project_data"] = project_data
                    project_inserted = True
                continue
            if key == "style_guide" and self._style_guide_edit is not None:
                data["style_guide"] = [
                    line for line in self._style_guide_edit.toPlainText().split("\n") if line.strip()
                ]
            elif key == "translation_rules" and self._rules_edit is not None:
                data["translation_rules"] = [
                    line for line in self._rules_edit.toPlainText().split("\n") if line.strip()
                ]
            elif key == "glossary" and glossary_data is not None:
                data["glossary"] = glossary_data

        if not project_inserted and project_data is not None:
            data["project_data"] = project_data

        for key, value in passthrough_items:
            data[key] = value

        return data

    # ─── 保存 ──────────────────────────────────────────
    def _save(self):
        current_tab = self._tab_stack.currentIndex()

        # 判断用哪个 Tab 的内容
        if self._is_structured and current_tab == 0:
            # 模板编辑 → 收集数据 → 序列化
            data = self._collect_template_data()
            ext = os.path.splitext(self._file_path)[1].lower()
            try:
                if ext in (".yaml", ".yml"):
                    try:
                        import yaml
                        content = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    except ImportError:
                        content = json.dumps(data, indent=2, ensure_ascii=False)
                else:
                    content = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e:
                self._status.setText(f"❌ {self._t('Serialize Error')}: {e}")
                return
        else:
            # 自由编辑
            content = self._free_editor.toPlainText()

            # 格式验证
            ext = os.path.splitext(self._file_path)[1].lower()
            if ext == ".json":
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    self._status.setText(f"❌ JSON {self._t('Format Error')}: {e}")
                    return
            elif ext in (".yaml", ".yml"):
                try:
                    import yaml
                    yaml.safe_load(content)
                except ImportError:
                    pass
                except Exception as e:
                    self._status.setText(f"❌ YAML {self._t('Format Error')}: {e}")
                    return

        # 写入文件
        try:
            with open(self._file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self._status.setText(f"✅ {self._t('Saved successfully')}")
            self._was_saved = True
            self._original_content = content
            # 同步另一个 tab
            if self._is_structured and current_tab == 0:
                self._free_editor.setPlainText(content)
            self.accept()
        except Exception as e:
            self._status.setText(f"❌ {self._t('Save failed')}: {e}")

    def get_was_modified(self) -> bool:
        if self._is_structured:
            # 简单比较自由编辑内容
            return self._was_saved or self._free_editor.toPlainText() != self._original_content
        return self._was_saved or self._free_editor.toPlainText() != self._original_content
