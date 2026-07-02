import json
import logging
import os
from typing import Callable, Dict, List, Optional

from PyQt6.QtGui import QAction
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, CaptionLabel, PlainTextEdit, PrimaryPushButton, PushButton, RoundMenu, ScrollArea, SegmentedWidget, TableWidget, TitleLabel
from ui.theme import (
    monospace_font as _monospace_font,
)
from ui.widgets.hover_hint import install_hover_hint
from ui.secondary_pages.themed_text_input_dialog import themed_get_text

from manga_translator.colorization.prompt_loader import (
    load_ai_colorizer_prompt_template,
)

logger = logging.getLogger("manga_translator")


def is_ai_colorizer_prompt_data(data) -> bool:
    if not isinstance(data, dict):
        return False
    # 仅根据 AI 上色专用字段判断，避免普通翻译 JSON/YAML 被误判为上色提示词。
    return any(
        key in data
        for key in (
            "ai_colorizer_prompt",
            "colorizer_prompt",
            "colorization_rules",
            "reference_images",
            "reference_image_paths",
        )
    )


def is_ai_colorizer_prompt_file(file_path: str) -> bool:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            if ext in (".yaml", ".yml"):
                import yaml

                data = yaml.safe_load(handle)
            else:
                data = json.load(handle)
    except Exception:
        return False
    return is_ai_colorizer_prompt_data(data)


def _section_label(text: str) -> BodyLabel:
    label = BodyLabel(text)
    return label


def _dim_label(text: str) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


def _styled_text_edit(text: str = "", read_only: bool = False) -> PlainTextEdit:
    editor = PlainTextEdit()
    editor.setPlainText(text)
    editor.setReadOnly(read_only)
    editor.setFont(_monospace_font())
    editor.setTabStopDistance(28)
    return editor


def _make_reference_images_table(
    entries: Optional[List[Dict[str, str]]] = None,
    *,
    t_func: Callable = lambda x: x,
) -> QTableWidget:
    rows = entries or []
    table = TableWidget()
    table.setRowCount(len(rows))
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels([t_func("Path"), t_func("Description")])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(28)
    for row, entry in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(entry.get("path", "")))
        table.setItem(row, 1, QTableWidgetItem(entry.get("description", "")))
    return table


class _SegmentedTabWidget(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys: list[str] = []
        self._segmented = SegmentedWidget(self)
        self._stack = QStackedWidget(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._segmented)
        layout.addWidget(self._stack, 1)

    def addTab(self, widget: QWidget, text: str):
        index = len(self._keys)
        key = f"tab_{index}"
        self._keys.append(key)
        self._stack.addWidget(widget)
        self._segmented.addItem(key, text, onClick=lambda i=index: self.setCurrentIndex(i))
        if index == 0:
            self.setCurrentIndex(0)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def setCurrentIndex(self, index: int):
        if index < 0 or index >= len(self._keys):
            return
        self._stack.setCurrentIndex(index)
        self._segmented.setCurrentItem(self._keys[index])
        self.currentChanged.emit(index)


class AIColorizerPromptEditorDialog(QDialog):
    _SECTION_META = {
        "prompt_text": "Prompt Text",
        "colorization_rules": "Colorization Rules",
        "reference_images": "Reference Images",
    }

    def __init__(self, file_path: str, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda x: x)
        self._file_path = file_path
        self._original_content = ""
        self._data: Dict[str, object] = {}
        self._was_saved = False

        self._prompt_text_edit: Optional[QPlainTextEdit] = None
        self._rules_edit: Optional[QPlainTextEdit] = None
        self._reference_images_table: Optional[QTableWidget] = None
        self._section_containers: List[tuple[str, QWidget]] = []

        self._setup_ui()
        self._load_file()

    def _setup_ui(self):
        self.setWindowTitle(self._t("Edit Prompt") + f" - {os.path.basename(self._file_path)}")
        self.setMinimumSize(820, 580)
        self.resize(980, 680)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = TitleLabel(self._t("Edit Prompt"))
        header.addWidget(title, 1)
        file_label = _dim_label(os.path.basename(self._file_path))
        header.addWidget(file_label)
        root.addLayout(header)
        root.addWidget(_divider())

        self._tabs = _SegmentedTabWidget()
        root.addWidget(self._tabs, 1)

        self._status = _dim_label("")
        root.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_btn = PushButton(self._t("Cancel"))
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)

        save_btn = PrimaryPushButton(self._t("Save"))
        save_btn.setFixedWidth(100)
        save_btn.clicked.connect(self._save)

        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

    def _load_file(self):
        try:
            with open(self._file_path, "r", encoding="utf-8") as handle:
                self._original_content = handle.read()
        except Exception as exc:
            self._original_content = ""
            self._set_status(f"Error: {exc}", "error")

        self._data = load_ai_colorizer_prompt_template(self._file_path)
        self._build_template_tab()
        self._build_raw_tab()
        self._set_status(self._t("Loaded successfully"), "default")

    def _build_template_tab(self):
        page = QWidget()
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._template_layout = layout
        self._template_sections_layout = QVBoxLayout()
        self._template_sections_layout.setContentsMargins(0, 0, 0, 0)
        self._template_sections_layout.setSpacing(10)
        layout.addLayout(self._template_sections_layout)
        self._insert_section("prompt_text", text=str(self._data.get("ai_colorizer_prompt", "")))
        self._insert_section(
            "colorization_rules",
            rules=self._data.get("colorization_rules", []),
        )
        self._insert_section(
            "reference_images",
            images=self._data.get("reference_images", []),
        )

        self._add_section_btn = PushButton("+ " + self._t("Add Section"))
        self._add_section_btn.clicked.connect(self._show_add_section_menu)
        layout.addWidget(self._add_section_btn)
        layout.addStretch()

        scroll.setWidget(content)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self._tabs.addTab(page, self._t("Template Edit"))

    def _build_raw_tab(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(6)
        page_layout.addWidget(_dim_label(self._t("Edit the raw file content directly")))
        self._free_editor = _styled_text_edit(self._original_content)
        page_layout.addWidget(self._free_editor, 1)
        self._tabs.addTab(page, self._t("Raw Edit"))

    def _make_section_container(self, key: str) -> tuple[QWidget, QVBoxLayout]:
        container = CardWidget()
        container.setProperty("sectionKey", key)

        outer = QVBoxLayout(container)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(_section_label(self._t(self._SECTION_META.get(key, key))))
        header.addStretch()

        btn_up = PushButton("▲")
        btn_up.setFixedSize(28, 24)
        btn_up.clicked.connect(lambda checked=False, c=container: self._request_move_section(c, -1))
        install_hover_hint(btn_up, self._t("Move Up"))

        btn_down = PushButton("▼")
        btn_down.setFixedSize(28, 24)
        btn_down.clicked.connect(lambda checked=False, c=container: self._request_move_section(c, 1))
        install_hover_hint(btn_down, self._t("Move Down"))

        btn_delete = PushButton("×")
        btn_delete.setFixedSize(28, 24)
        btn_delete.clicked.connect(lambda: self._remove_section(container, key))
        install_hover_hint(btn_delete, self._t("Delete"))
        container._move_up_button = btn_up
        container._move_down_button = btn_down

        header.addWidget(btn_up)
        header.addWidget(btn_down)
        header.addWidget(btn_delete)
        outer.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        outer.addLayout(body)
        outer.addWidget(_divider())
        return container, body

    def _insert_section(self, key: str, idx: int = -1, **kwargs):
        container, body = self._make_section_container(key)
        if key == "prompt_text":
            self._fill_prompt_text(body, kwargs.get("text", ""))
        elif key == "colorization_rules":
            self._fill_colorization_rules(body, kwargs.get("rules", []))
        elif key == "reference_images":
            self._fill_reference_images(body, kwargs.get("images", []))

        section_layout = self._template_sections_layout
        if idx < 0:
            section_layout.addWidget(container)
            self._section_containers.append((key, container))
        else:
            section_layout.insertWidget(idx, container)
            self._section_containers.insert(idx, (key, container))
        self._refresh_section_move_buttons()

    def _fill_prompt_text(self, layout: QVBoxLayout, text: str):
        self._prompt_text_edit = _styled_text_edit(text)
        self._prompt_text_edit.setFixedHeight(180)
        layout.addWidget(self._prompt_text_edit)

    def _fill_colorization_rules(self, layout: QVBoxLayout, rules: List[str]):
        layout.addWidget(_dim_label(self._t("One rule per line")))
        text = "\n".join(str(item) for item in rules) if isinstance(rules, list) else ""
        self._rules_edit = _styled_text_edit(text)
        self._rules_edit.setFixedHeight(110)
        layout.addWidget(self._rules_edit)

    def _fill_reference_images(self, layout: QVBoxLayout, images: List[Dict[str, str]]):
        self._reference_images_table = _make_reference_images_table(images, t_func=self._t)
        self._reference_images_table.setMinimumHeight(120)
        layout.addWidget(self._reference_images_table)

        row_buttons = QHBoxLayout()
        add_btn = PushButton("+ " + self._t("Add Reference Image"))
        add_btn.clicked.connect(self._prompt_and_add_reference_image)

        del_btn = PushButton("- " + self._t("Delete Row"))
        del_btn.clicked.connect(lambda: self._del_table_row(self._reference_images_table))

        row_buttons.addWidget(add_btn)
        row_buttons.addWidget(del_btn)
        row_buttons.addStretch()
        layout.addLayout(row_buttons)

    def _prompt_and_add_reference_image(self):
        if self._reference_images_table is None:
            return

        description, ok = themed_get_text(
            self,
            title=self._t("Reference Images"),
            label=self._t("Description") + ":",
            ok_text=self._t("OK"),
            cancel_text=self._t("Cancel"),
        )
        if not ok:
            return

        start_dir = os.path.dirname(os.path.abspath(self._file_path)) if self._file_path else os.getcwd()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._t("Reference Images"),
            start_dir,
            "Image Files (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if not file_path:
            return

        display_path = os.path.normpath(file_path)
        try:
            prompt_dir = os.path.dirname(os.path.abspath(self._file_path))
            display_path = os.path.relpath(display_path, prompt_dir)
        except ValueError:
            pass

        row = self._reference_images_table.rowCount()
        self._reference_images_table.insertRow(row)
        self._reference_images_table.setItem(row, 0, QTableWidgetItem(display_path.replace("\\", "/")))
        self._reference_images_table.setItem(row, 1, QTableWidgetItem(description.strip()))

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
            order.append(str(widget.property("sectionKey") or "<unknown>"))
        return order

    def _request_move_section(self, container: QWidget, direction: int):
        key = str(container.property("sectionKey") or "<unknown>")
        logger.info(
            "AI colorizer prompt move button clicked: file=%s key=%s direction=%s order=%s layout=%s",
            self._file_path,
            key,
            direction,
            self._section_order_snapshot(),
            self._layout_section_order_snapshot(),
        )
        self._move_section(container, direction)

    def _move_section(self, container: QWidget, direction: int):
        index = None
        for idx, (_, current) in enumerate(self._section_containers):
            if current is container:
                index = idx
                break
        if index is None:
            logger.warning(
                "AI colorizer prompt move ignored: file=%s container not found direction=%s order=%s",
                self._file_path,
                direction,
                self._section_order_snapshot(),
            )
            return

        new_index = index + direction
        if new_index < 0 or new_index >= len(self._section_containers):
            logger.info(
                "AI colorizer prompt move ignored: file=%s key=%s from=%s to=%s order=%s",
                self._file_path,
                self._section_containers[index][0],
                index,
                new_index,
                self._section_order_snapshot(),
            )
            return

        logger.info(
            "AI colorizer prompt move apply: file=%s key=%s from=%s to=%s order_before=%s",
            self._file_path,
            self._section_containers[index][0],
            index,
            new_index,
            self._section_order_snapshot(),
        )
        self._section_containers[index], self._section_containers[new_index] = (
            self._section_containers[new_index],
            self._section_containers[index],
        )
        self._reflow_section_widgets()

    def _reflow_section_widgets(self):
        layout = self._template_sections_layout
        logger.info(
            "AI colorizer prompt reflow start: file=%s order=%s layout_before=%s",
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
        if self._tabs is not None:
            self._tabs.update()
        logger.info(
            "AI colorizer prompt reflow end: file=%s order=%s layout_after=%s",
            self._file_path,
            self._section_order_snapshot(),
            self._layout_section_order_snapshot(),
        )

    def _remove_section(self, container: QWidget, key: str):
        self._section_containers = [(k, c) for k, c in self._section_containers if c is not container]
        self._template_sections_layout.removeWidget(container)
        container.setParent(None)
        container.deleteLater()
        self._refresh_section_move_buttons()

        if key == "prompt_text":
            self._prompt_text_edit = None
        elif key == "colorization_rules":
            self._rules_edit = None
        elif key == "reference_images":
            self._reference_images_table = None

    def _show_add_section_menu(self):
        menu = RoundMenu(parent=self)
        existing = {key for key, _ in self._section_containers}
        has_items = False
        for key, label in self._SECTION_META.items():
            if key in existing:
                continue
            action = QAction(self._t(label), self)
            action.triggered.connect(lambda checked=False, section_key=key: self._insert_section(section_key))
            menu.addAction(action)
            has_items = True

        if not has_items:
            action = QAction(self._t("All sections added"), self)
            action.setEnabled(False)
            menu.addAction(action)

        menu.exec(self._add_section_btn.mapToGlobal(self._add_section_btn.rect().topLeft()))

    @staticmethod
    def _add_table_row(table: Optional[QTableWidget], cols: int):
        if table is None:
            return
        row = table.rowCount()
        table.insertRow(row)
        for column in range(cols):
            table.setItem(row, column, QTableWidgetItem(""))

    @staticmethod
    def _del_table_row(table: Optional[QTableWidget]):
        if table is None:
            return
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not rows:
            last = table.rowCount() - 1
            if last >= 0:
                rows = [last]
        for row in rows:
            table.removeRow(row)

    def _collect_template_data(self) -> Dict[str, object]:
        data: Dict[str, object] = {}
        for key, _ in self._section_containers:
            if key == "prompt_text" and self._prompt_text_edit is not None:
                data["ai_colorizer_prompt"] = self._prompt_text_edit.toPlainText()
            elif key == "colorization_rules" and self._rules_edit is not None:
                data["colorization_rules"] = [
                    line for line in self._rules_edit.toPlainText().splitlines() if line.strip()
                ]
            elif key == "reference_images" and self._reference_images_table is not None:
                images: List[Dict[str, str]] = []
                for row in range(self._reference_images_table.rowCount()):
                    path_item = self._reference_images_table.item(row, 0) or QTableWidgetItem("")
                    desc_item = self._reference_images_table.item(row, 1) or QTableWidgetItem("")
                    path = path_item.text().strip()
                    description = desc_item.text().strip()
                    if path:
                        images.append({"path": path, "description": description})
                data["reference_images"] = images
        return data

    def _set_status(self, text: str, state: str = "default"):
        self._status.setText(text)
        del state

    def _serialize_structured(self, data: Dict[str, object]) -> str:
        ext = os.path.splitext(self._file_path)[1].lower()
        if ext in (".yaml", ".yml"):
            try:
                import yaml

                return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
            except ImportError:
                return json.dumps(data, indent=2, ensure_ascii=False)
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _validate_raw_content(self, content: str) -> Optional[str]:
        ext = os.path.splitext(self._file_path)[1].lower()
        if ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return f"JSON {self._t('Format Error')}: {exc}"
        elif ext in (".yaml", ".yml"):
            try:
                import yaml

                yaml.safe_load(content)
            except ImportError:
                return None
            except Exception as exc:
                return f"YAML {self._t('Format Error')}: {exc}"
        return None

    def _save(self):
        current_tab = self._tabs.currentIndex()
        if current_tab == 0:
            try:
                content = self._serialize_structured(self._collect_template_data())
            except Exception as exc:
                self._set_status(f"{self._t('Serialize Error')}: {exc}", "error")
                return
        else:
            content = self._free_editor.toPlainText()
            validation_error = self._validate_raw_content(content)
            if validation_error:
                self._set_status(validation_error, "error")
                return

        try:
            with open(self._file_path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except Exception as exc:
            self._set_status(f"{self._t('Save failed')}: {exc}", "error")
            return

        self._was_saved = True
        self._original_content = content
        self._free_editor.setPlainText(content)
        self._set_status(self._t("Saved successfully"), "success")
        self.accept()

    def get_was_modified(self) -> bool:
        return self._was_saved or self._free_editor.toPlainText() != self._original_content
