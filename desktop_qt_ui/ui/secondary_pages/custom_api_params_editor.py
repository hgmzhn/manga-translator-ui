import json
from typing import Any, Callable

from manga_translator.custom_api_params import (
    CUSTOM_API_PARAM_SECTIONS,
    DEFAULT_CUSTOM_API_PARAMS_PRESET,
    build_custom_api_params_payload,
    create_empty_custom_api_params_preset,
    migrate_legacy_custom_api_params_payload,
    normalize_custom_api_params_presets,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    LineEdit,
    PlainTextEdit,
    PopUpAniStackedWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    SimpleCardWidget,
    TitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from ui.secondary_pages.fluent_dialog import FluentSecondaryDialog
from ui.secondary_pages.themed_text_input_dialog import themed_get_text
from ui.theme import (
    monospace_font as _monospace_font,
)


def _identity_translate(text: str, **kwargs) -> str:
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    return "json"


def _fluent_scroll(parent=None) -> ScrollArea:
    scroll = ScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(ScrollArea.Shape.NoFrame)
    scroll.enableTransparentBackground()
    return scroll


class CustomApiParamRow(SimpleCardWidget):
    remove_requested = pyqtSignal(QWidget)

    def __init__(self, t_func: Callable[..., str] | None = None, parent=None):
        super().__init__(parent)
        self._t = t_func or _identity_translate
        self._is_placeholder_row = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        key_col = QVBoxLayout()
        key_col.setSpacing(6)
        key_label = BodyLabel(self._t("Key"))
        self.key_input = LineEdit()
        self.key_input.setPlaceholderText("temperature")
        key_col.addWidget(key_label)
        key_col.addWidget(self.key_input)
        layout.addLayout(key_col, 3)

        type_col = QVBoxLayout()
        type_col.setSpacing(6)
        type_label = BodyLabel(self._t("Type"))
        self.type_combo = ComboBox()
        for label, value in [
            (self._t("String"), "string"),
            (self._t("Number"), "number"),
            (self._t("Boolean"), "boolean"),
            (self._t("Null"), "null"),
            ("JSON", "json"),
        ]:
            self.type_combo.addItem(label, userData=value)
        type_col.addWidget(type_label)
        type_col.addWidget(self.type_combo)
        layout.addLayout(type_col, 2)

        value_col = QVBoxLayout()
        value_col.setSpacing(6)
        value_label = BodyLabel(self._t("Value"))
        self.value_stack = PopUpAniStackedWidget(self)

        self.string_input = LineEdit()
        self.string_input.setPlaceholderText("gpt-4o-mini")

        self.number_input = LineEdit()
        self.number_input.setPlaceholderText("0.2")
        self.number_input.setFont(_monospace_font(10))

        self.boolean_input = ComboBox()
        self.boolean_input.addItem("true", userData=True)
        self.boolean_input.addItem("false", userData=False)

        self.null_label = CaptionLabel("null")
        self.null_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.json_input = LineEdit()
        self.json_input.setPlaceholderText('{"type": "json"}')
        self.json_input.setFont(_monospace_font(10))

        self._value_pages = {
            "string": self.string_input,
            "number": self.number_input,
            "boolean": self.boolean_input,
            "null": self.null_label,
            "json": self.json_input,
        }
        for editor in self._value_pages.values():
            self.value_stack.addWidget(editor)

        value_col.addWidget(value_label)
        value_col.addWidget(self.value_stack)
        layout.addLayout(value_col, 4)

        remove_col = QVBoxLayout()
        remove_col.setSpacing(6)
        remove_col.addWidget(CaptionLabel(""))
        self.remove_button = PushButton(self._t("Delete"))
        self.remove_button.setIcon(FIF.DELETE)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        remove_col.addWidget(self.remove_button)
        remove_col.addStretch(1)
        layout.addLayout(remove_col)

        self.type_combo.currentIndexChanged.connect(self._sync_type_editor)
        self.key_input.textEdited.connect(self._mark_user_edited)
        self.string_input.textEdited.connect(self._mark_user_edited)
        self.number_input.textEdited.connect(self._mark_user_edited)
        self.json_input.textChanged.connect(self._mark_user_edited)
        self.type_combo.currentIndexChanged.connect(self._mark_user_edited)
        self.boolean_input.currentIndexChanged.connect(self._mark_user_edited)
        self._sync_type_editor()

    def _sync_type_editor(self):
        current_type = self.type_combo.currentData()
        editor = self._value_pages.get(current_type, self.string_input)
        self.value_stack.setCurrentIndex(self.value_stack.indexOf(editor))

    def _mark_user_edited(self, *args):
        del args
        self._is_placeholder_row = False

    def set_entry(self, key: str, value: Any):
        self._is_placeholder_row = False
        self.key_input.setText(key)
        value_type = _infer_type(value)
        combo_index = self.type_combo.findData(value_type)
        if combo_index >= 0:
            self.type_combo.setCurrentIndex(combo_index)

        if value_type == "string":
            self.string_input.setText(value)
        elif value_type == "number":
            self.number_input.setText(str(value))
        elif value_type == "boolean":
            bool_index = self.boolean_input.findData(bool(value))
            self.boolean_input.setCurrentIndex(max(bool_index, 0))
        elif value_type == "json":
            self.json_input.setText(json.dumps(value, ensure_ascii=False))

        self._sync_type_editor()

    def is_empty_placeholder(self) -> bool:
        return self._is_placeholder_row and not self.key_input.text().strip()

    def _parse_number(self) -> int | float:
        raw = self.number_input.text().strip()
        if not raw:
            raise ValueError(self._t("Number value is empty"))
        parsed = json.loads(raw)
        if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
            raise ValueError(self._t("Number value is invalid"))
        return parsed

    def _parse_json_value(self) -> Any:
        raw = self.json_input.text().strip()
        if not raw:
            raise ValueError(self._t("JSON value is empty"))
        return json.loads(raw)

    def get_entry(self) -> tuple[str, Any]:
        key = self.key_input.text().strip()
        if not key:
            raise ValueError(self._t("Parameter name cannot be empty"))

        value_type = self.type_combo.currentData()
        if value_type == "string":
            value = self.string_input.text()
        elif value_type == "number":
            value = self._parse_number()
        elif value_type == "boolean":
            value = self.boolean_input.currentData()
        elif value_type == "null":
            value = None
        else:
            value = self._parse_json_value()
        return key, value


class CustomApiParamsEditorDialog(FluentSecondaryDialog):
    def __init__(self, file_path: str, t_func: Callable[..., str] | None = None, parent=None):
        super().__init__(parent)
        self._t = t_func or _identity_translate
        self._file_path = file_path
        self._original_content = ""
        self._presets: dict[str, dict[str, dict[str, Any]]] = {}
        self._current_preset: str | None = None
        self._switching_preset = False
        self.section_segmented: SegmentedWidget | None = None
        self.section_stack: PopUpAniStackedWidget | None = None
        self.section_layouts: dict[str, QVBoxLayout] = {}
        self.section_contents: dict[str, QWidget] = {}
        self._setup_ui()
        self._load_from_disk()

    def _setup_ui(self):
        self.setWindowTitle(self._t("Edit Custom API Params"))
        self.setMinimumSize(880, 620)
        self.resize(980, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header_card = SimpleCardWidget(self)
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(4)

        title = TitleLabel(self._t("Edit Custom API Params"), header_card)
        subtitle = BodyLabel(
            self._t(
                "At runtime, each API module selects the preset named after its current model and falls back to General. "
                "Only common and that module's section are merged."
            ),
            header_card,
        )
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(BodyLabel(self._t("Model Preset"), header_card))

        self.preset_combo = ComboBox(header_card)
        self.preset_combo.setMinimumWidth(260)
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)

        self.add_preset_button = PushButton(self._t("Add Preset"), header_card)
        self.add_preset_button.setIcon(FIF.ADD)
        self.add_preset_button.clicked.connect(self._add_preset)

        self.rename_preset_button = PushButton(self._t("Rename"), header_card)
        self.rename_preset_button.setIcon(FIF.EDIT)
        self.rename_preset_button.clicked.connect(self._rename_preset)

        self.delete_preset_button = PushButton(self._t("Delete"), header_card)
        self.delete_preset_button.setIcon(FIF.DELETE)
        self.delete_preset_button.clicked.connect(self._delete_preset)

        preset_row.addWidget(self.add_preset_button)
        preset_row.addWidget(self.rename_preset_button)
        preset_row.addWidget(self.delete_preset_button)
        header_layout.addLayout(preset_row)
        root.addWidget(header_card)

        self.tab_segmented = SegmentedWidget(self)
        self.tab_stack = PopUpAniStackedWidget(self)
        root.addWidget(self.tab_segmented)
        root.addWidget(self.tab_stack, 1)

        self._build_params_tab()
        self._build_raw_tab()

        self.status_label = CaptionLabel("")
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.refresh_button = PushButton(self._t("Refresh"))
        self.refresh_button.setIcon(FIF.SYNC)
        self.refresh_button.clicked.connect(self._load_from_disk)

        self.cancel_button = PushButton(self._t("Cancel"))
        self.cancel_button.setIcon(FIF.CANCEL)
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = PrimaryPushButton(self._t("Save"))
        self.save_button.setIcon(FIF.SAVE)
        self.save_button.clicked.connect(self._save)

        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        root.addLayout(button_row)

    def _build_params_tab(self):
        page = SimpleCardWidget(self.tab_stack)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(14, 12, 14, 14)
        page_layout.setSpacing(8)

        title = BodyLabel(self._t("Grouped API Params"), page)
        hint = CaptionLabel(
            self._t(
                "Each preset contains common, translator, OCR, colorizer, and render sections. "
                "Parameters are never sent across modules."
            ),
            page,
        )
        hint.setWordWrap(True)

        page_layout.addWidget(title)
        page_layout.addWidget(hint)

        self.section_segmented = SegmentedWidget(page)
        self.section_stack = PopUpAniStackedWidget(page)
        for section in CUSTOM_API_PARAM_SECTIONS:
            section_page = SimpleCardWidget(self.section_stack)
            section_page_layout = QVBoxLayout(section_page)
            section_page_layout.setContentsMargins(12, 12, 12, 12)
            section_page_layout.setSpacing(8)

            scroll = _fluent_scroll(section_page)

            content = QWidget(scroll)

            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 4, 0, 4)
            layout.setSpacing(10)
            layout.addStretch(1)

            scroll.setWidget(content)
            scroll.enableTransparentBackground()

            add_row = QHBoxLayout()
            add_row.addStretch(1)
            add_button = PushButton(self._t("Add Row"), section_page)
            add_button.setIcon(FIF.ADD)
            add_button.clicked.connect(lambda _=False, s=section: self._append_row(s))
            add_row.addWidget(add_button)

            section_page_layout.addWidget(scroll, 1)
            section_page_layout.addLayout(add_row)

            self.section_contents[section] = content
            self.section_layouts[section] = layout
            section_index = self.section_stack.count()
            self.section_stack.addWidget(section_page)
            self.section_segmented.addItem(
                section,
                self._section_title(section),
                onClick=lambda checked=False, route_key=section, index=section_index: (
                    self.section_stack.setCurrentIndex(index),
                    self.section_segmented.setCurrentItem(route_key),
                ),
            )
            if section_index == 0:
                self.section_stack.setCurrentIndex(section_index)
                self.section_segmented.setCurrentItem(section)

        page_layout.addWidget(self.section_segmented)
        page_layout.addWidget(self.section_stack, 1)

        route_key = "template_edit"
        page_index = self.tab_stack.count()
        self.tab_stack.addWidget(page)
        self.tab_segmented.addItem(
            route_key,
            self._t("Template Edit"),
            onClick=lambda checked=False: (
                self.tab_stack.setCurrentIndex(page_index),
                self.tab_segmented.setCurrentItem(route_key),
            ),
        )
        self.tab_stack.setCurrentIndex(page_index)
        self.tab_segmented.setCurrentItem(route_key)

    def _build_raw_tab(self):
        page = SimpleCardWidget(self.tab_stack)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(12, 10, 12, 10)
        page_layout.setSpacing(8)

        hint = CaptionLabel(self._t("Edit the raw file content directly"), page)
        page_layout.addWidget(hint)

        self.raw_editor = PlainTextEdit(page)
        self.raw_editor.setFont(_monospace_font())
        self.raw_editor.setTabStopDistance(28)
        self.raw_editor.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        page_layout.addWidget(self.raw_editor, 1)

        route_key = "raw_edit"
        page_index = self.tab_stack.count()
        self.tab_stack.addWidget(page)
        self.tab_segmented.addItem(
            route_key,
            self._t("Raw Edit"),
            onClick=lambda checked=False: (
                self.tab_stack.setCurrentIndex(page_index),
                self.tab_segmented.setCurrentItem(route_key),
            ),
        )
        if page_index == 0:
            self.tab_stack.setCurrentIndex(page_index)
            self.tab_segmented.setCurrentItem(route_key)

    def _section_title(self, section: str) -> str:
        if section == "common":
            return self._t("General")
        if section == "translator":
            return self._t("label_translator")
        if section == "ocr":
            return self._t("label_ocr")
        if section == "render":
            return self._t("label_renderer")
        if section == "colorizer":
            return self._t("label_colorizer")
        return section

    def _refresh_preset_selector(self, selected_name: str | None = None):
        names = list(self._presets)
        if DEFAULT_CUSTOM_API_PARAMS_PRESET not in names:
            self._presets = {
                DEFAULT_CUSTOM_API_PARAMS_PRESET: create_empty_custom_api_params_preset(),
                **self._presets,
            }
            names = list(self._presets)

        target = selected_name if selected_name in self._presets else DEFAULT_CUSTOM_API_PARAMS_PRESET
        self._switching_preset = True
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(names)
        self.preset_combo.setCurrentText(target)
        self.preset_combo.blockSignals(False)
        self._switching_preset = False
        self._current_preset = target
        self._populate_rows(self._presets[target])
        self._update_preset_buttons()

    def _on_preset_changed(self, preset_name: str):
        if self._switching_preset or not preset_name or preset_name == self._current_preset:
            return
        previous = self._current_preset
        try:
            self._store_current_preset()
        except ValueError as exc:
            self._set_status(str(exc), kind="error")
            self._switching_preset = True
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText(previous or DEFAULT_CUSTOM_API_PARAMS_PRESET)
            self.preset_combo.blockSignals(False)
            self._switching_preset = False
            return

        if preset_name not in self._presets:
            return
        self._current_preset = preset_name
        self._populate_rows(self._presets[preset_name])
        self._update_preset_buttons()

    def _update_preset_buttons(self):
        editable = self._current_preset not in {None, DEFAULT_CUSTOM_API_PARAMS_PRESET}
        self.rename_preset_button.setEnabled(editable)
        self.delete_preset_button.setEnabled(editable)

    def _add_preset(self, *args):
        del args
        try:
            self._store_current_preset()
        except ValueError as exc:
            self._set_status(str(exc), kind="error")
            return

        name, accepted = themed_get_text(
            self,
            title=self._t("Add Preset"),
            label=self._t("Enter preset name:"),
            ok_text=self._t("OK"),
            cancel_text=self._t("Cancel"),
        )
        name = name.strip()
        if not accepted:
            return
        if not name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Preset name cannot be empty"))
            return
        if name in self._presets:
            QMessageBox.warning(
                self,
                self._t("Warning"),
                self._t("Preset '{name}' already exists", name=name),
            )
            return

        self._presets[name] = create_empty_custom_api_params_preset()
        self._refresh_preset_selector(name)

    def _rename_preset(self, *args):
        del args
        current = self._current_preset
        if not current or current == DEFAULT_CUSTOM_API_PARAMS_PRESET:
            return
        try:
            self._store_current_preset()
        except ValueError as exc:
            self._set_status(str(exc), kind="error")
            return

        name, accepted = themed_get_text(
            self,
            title=self._t("Rename Preset"),
            label=self._t("Enter preset name:"),
            text=current,
            ok_text=self._t("OK"),
            cancel_text=self._t("Cancel"),
        )
        name = name.strip()
        if not accepted or name == current:
            return
        if not name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Preset name cannot be empty"))
            return
        if name in self._presets:
            QMessageBox.warning(
                self,
                self._t("Warning"),
                self._t("Preset '{name}' already exists", name=name),
            )
            return

        self._presets = {
            (name if preset_name == current else preset_name): preset
            for preset_name, preset in self._presets.items()
        }
        self._refresh_preset_selector(name)

    def _delete_preset(self, *args):
        del args
        current = self._current_preset
        if not current or current == DEFAULT_CUSTOM_API_PARAMS_PRESET:
            return
        reply = QMessageBox.question(
            self,
            self._t("Confirm"),
            self._t("Are you sure you want to delete preset '{name}'?", name=current),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(current, None)
        self._refresh_preset_selector(DEFAULT_CUSTOM_API_PARAMS_PRESET)

    def _insert_row_widget(self, section: str, row: CustomApiParamRow):
        row.remove_requested.connect(self._remove_row)
        layout = self.section_layouts[section]
        insert_index = max(layout.count() - 1, 0)
        layout.insertWidget(insert_index, row)

    def _append_row(self, section: str, key: str = "", value: Any = ""):
        row = CustomApiParamRow(t_func=self._t, parent=self.section_contents[section])
        if key:
            row.set_entry(key, value)
        self._insert_row_widget(section, row)
        return row

    def _clear_rows(self):
        for layout in self.section_layouts.values():
            while layout.count() > 1:
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

    def _remove_row(self, row: QWidget):
        row.setParent(None)
        row.deleteLater()

    def _load_from_disk(self):
        try:
            with open(self._file_path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
        except FileNotFoundError:
            content = "{}"
        except Exception as exc:
            self._set_status(f"{self._t('Load failed')}: {exc}", kind="error")
            return

        if not content:
            content = "{}"

        self.raw_editor.setPlainText(content)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            self._presets = {
                DEFAULT_CUSTOM_API_PARAMS_PRESET: create_empty_custom_api_params_preset()
            }
            self._refresh_preset_selector()
            self._original_content = content
            self._set_status(f"{self._t('JSON format error')}: {exc}", kind="error")
            return

        if not isinstance(parsed, dict):
            self._presets = {
                DEFAULT_CUSTOM_API_PARAMS_PRESET: create_empty_custom_api_params_preset()
            }
            self._refresh_preset_selector()
            self._original_content = content
            self._set_status(self._t("JSON root must be an object"), kind="error")
            return

        migrated, _ = migrate_legacy_custom_api_params_payload(parsed)
        self._presets = normalize_custom_api_params_presets(migrated)
        canonical_content = json.dumps(self._presets, indent=2, ensure_ascii=False)
        self._original_content = canonical_content
        self.raw_editor.setPlainText(canonical_content)
        self._refresh_preset_selector(DEFAULT_CUSTOM_API_PARAMS_PRESET)
        self._set_status(self._t("Loaded successfully"))

    def _populate_rows(self, preset: dict[str, Any]):
        self._clear_rows()
        for section in CUSTOM_API_PARAM_SECTIONS:
            values = preset.get(section) or {}
            if not values:
                self._append_row(section)
                continue
            for key, value in values.items():
                self._append_row(section, key, value)

    def _collect_current_preset(self) -> dict[str, dict[str, Any]]:
        section_data: dict[str, dict[str, Any]] = {
            section: {} for section in CUSTOM_API_PARAM_SECTIONS
        }

        for section in CUSTOM_API_PARAM_SECTIONS:
            container = self.section_contents.get(section)
            if container is None:
                continue

            row_widgets = container.findChildren(
                CustomApiParamRow,
                options=Qt.FindChildOption.FindDirectChildrenOnly,
            )
            for row in row_widgets:
                if row.is_empty_placeholder():
                    continue
                key, value = row.get_entry()
                if key in section_data[section]:
                    raise ValueError(self._t("Duplicate parameter name: {name}", name=key))
                section_data[section][key] = value

        return section_data

    def _store_current_preset(self):
        if self._current_preset:
            self._presets[self._current_preset] = self._collect_current_preset()

    def _collect_structured_data(self) -> dict[str, Any]:
        self._store_current_preset()
        return build_custom_api_params_payload(self._presets)

    def _collect_raw_data(self) -> dict[str, Any]:
        content = self.raw_editor.toPlainText().strip() or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError(self._t("JSON root must be an object"))
        migrated, _ = migrate_legacy_custom_api_params_payload(parsed)
        return build_custom_api_params_payload(migrated)

    def _set_status(self, message: str, kind: str = "default"):
        del kind
        self.status_label.setText(message)

    def _save(self):
        try:
            if self.tab_stack.currentIndex() == 0:
                data = self._collect_structured_data()
            else:
                data = self._collect_raw_data()
        except json.JSONDecodeError as exc:
            self._set_status(f"{self._t('JSON format error')}: {exc}", kind="error")
            return
        except ValueError as exc:
            self._set_status(str(exc), kind="error")
            return

        content = json.dumps(data, indent=2, ensure_ascii=False)
        try:
            with open(self._file_path, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.write("\n")
        except Exception as exc:
            self._set_status(f"{self._t('Save failed')}: {exc}", kind="error")
            return

        self._original_content = content
        self.raw_editor.setPlainText(content)
        selected_preset = self._current_preset
        self._presets = normalize_custom_api_params_presets(data)
        self._refresh_preset_selector(selected_preset)
        self._set_status(self._t("Saved successfully"), kind="success")

    def get_was_modified(self) -> bool:
        try:
            if self.tab_stack.currentIndex() == 0:
                current = json.dumps(
                    self._collect_structured_data(),
                    indent=2,
                    ensure_ascii=False,
                )
            else:
                current = self.raw_editor.toPlainText().strip()
        except (ValueError, json.JSONDecodeError):
            return True
        return current != self._original_content.strip()
