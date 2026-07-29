"""Visual editor for automatic rich-text rules."""

from __future__ import annotations

import copy
import json
from typing import Callable, Dict

import yaml
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    CompactDoubleSpinBox,
    CompactSpinBox,
    PlainTextEdit,
    PopUpAniStackedWidget,
    PushButton,
    SegmentedWidget,
    SimpleCardWidget,
    SingleDirectionScrollArea,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)
from qfluentwidgets import (
    LineEdit as FluentLineEdit,
)

from editor.rich_text_editing import (
    normalize_text_style,
    text_style_from_control_values,
    text_style_to_control_values,
)
from services import get_config_service, get_i18n_manager
from ui.secondary_pages.fluent_dialog import DialogCode, FluentSecondaryDialog
from ui.secondary_pages.replacements_editor import YamlHighlighter, _fixed_width_font
from ui.secondary_pages.themed_message_box import themed_question, themed_warning
from ui.widgets.color_picker import ColorPickerWidget
from utils.font_list import populate_font_combo


def _rules_path() -> str:
    from manga_translator.rendering.rich_text_rules import ensure_rich_text_rules_exists

    return ensure_rich_text_rules_exists()


def _style_summary(style: dict, empty_text: str) -> str:
    if not style:
        return empty_text
    labels = {
        "bold": "B",
        "italic": "I",
        "color": "C",
        "scale": "%",
        "fontSize": "S",
        "fontFamily": "F",
        "stroke": "O",
        "outerStroke": "OS",
        "glow": "G",
        "emphasis": "D",
        "verticalAdvance": "FA",
        "kerning": "K",
        "preKerning": "PK",
        "lineKerning": "LK",
        "nextKerning": "NK",
        "transform": "XY/Rot",
        "ruby": "R",
        "tcy": "T",
    }
    return " ".join(labels.get(key, key) for key in style)


class _OptionalStyleField(QWidget):
    def __init__(self, key: str, editor: QWidget, parent=None):
        super().__init__(parent)
        self.key = key
        self.enabled = CheckBox(self)
        self.editor = editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.enabled)
        layout.addWidget(editor, 1)
        self.enabled.toggled.connect(editor.setEnabled)
        editor.setEnabled(False)

    def set_active(self, active: bool):
        self.enabled.setChecked(active)
        self.editor.setEnabled(active)


class RichTextStyleControls(SimpleCardWidget):
    """规则页复用浮动富文本编辑器同口径的 Fluent 样式控件。"""

    def __init__(self, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._fields: dict[str, _OptionalStyleField] = {}
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()
        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self._form_label_keys: dict[QWidget, str] = {}

        def add_form_row(label_key: str, field: QWidget) -> None:
            form.addRow(self._t(label_key), field)
            label = form.labelForField(field)
            if label is not None:
                self._form_label_keys[label] = label_key

        self.saved_style_combo = ComboBox(self)
        self.saved_style_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.saved_style_combo.activated.connect(self._on_saved_style_activated)
        self._refresh_saved_style_combo()
        add_form_row("Saved rich text style:", self.saved_style_combo)

        self.bold = CheckBox(self._t("Bold"))
        self.emphasis = CheckBox(self._t("Emphasis"))
        self.tcy = CheckBox(self._t("Vertical-in-Horizontal (TCY)"))
        switches = QWidget(self)
        switch_layout = QHBoxLayout(switches)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.addWidget(self.bold)
        switch_layout.addWidget(self.emphasis)
        switch_layout.addWidget(self.tcy)
        switch_layout.addStretch()
        add_form_row("Switches", switches)

        ruby_editor = FluentLineEdit(self)
        ruby_editor.setPlaceholderText(self._t("Ruby text"))
        self.ruby = _OptionalStyleField("ruby", ruby_editor, self)
        add_form_row("Ruby Text", self.ruby)

        self.italic = self._number("italic", -85, 85, 15, 1, 1)
        self.color = self._color("color", "#E53935", "saved_colors", "Select rich text color")
        self.font_size = self._integer("fontSize", 1, 1000, 24)
        self.scale = self._number("scale", 0.1, 10, 1.2, 0.05, 2)
        advance_choices = (("Half Advance", "half"), ("Full Advance", "full"))
        advance_editor = ComboBox(self)
        for label, value in advance_choices:
            advance_editor.addItem(self._t(label), userData=value)
        self.vertical_advance = self._register("verticalAdvance", advance_editor)
        self.vertical_advance.choice_labels = advance_choices
        self.font_family = self._font("fontFamily")
        self.stroke = self._effect("stroke", "#FFFFFF", "saved_stroke_colors", "width", 0.07)
        self.outer_stroke = self._effect("outerStroke", "#000000", "saved_outer_stroke_colors", "width", 0.20)
        self.glow = self._effect("glow", "#00FFFF", "saved_glow_colors", "blur", 0.10)
        self.kerning = self._number("kerning", -5, 5, 0, 0.05, 2)
        self.pre_kerning = self._number("preKerning", -5, 5, 0, 0.05, 2)
        self.line_kerning = self._number("lineKerning", -5, 5, 0, 0.05, 2)
        self.next_kerning = self._number("nextKerning", -5, 5, 0, 0.05, 2)
        self.rotation = self._number("rotation", -180, 180, 0, 1, 1)
        self.offset_x = self._number("offsetX", -500, 500, 0, 1, 1)
        self.offset_y = self._number("offsetY", -500, 500, 0, 1, 1)
        self.offset_x.editor.setSuffix("%")
        self.offset_y.editor.setSuffix("%")

        for label, field in (
            ("Italic Angle", self.italic), ("Text Color", self.color),
            ("Font Size", self.font_size), ("Scale", self.scale),
            ("Force Advance", self.vertical_advance),
            ("Font Family", self.font_family), ("Stroke", self.stroke),
            ("Outer Stroke", self.outer_stroke), ("Glow", self.glow),
            ("Kerning", self.kerning), ("Pre Kerning", self.pre_kerning),
            ("Line Kerning", self.line_kerning), ("Next Kerning", self.next_kerning),
            ("Rotation", self.rotation), ("Offset X", self.offset_x), ("Offset Y", self.offset_y),
        ):
            add_form_row(label, field)

    @staticmethod
    def _spin_box(spin):
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    def _register(self, key: str, editor: QWidget) -> _OptionalStyleField:
        field = _OptionalStyleField(key, editor, self)
        self._fields[key] = field
        return field

    def _integer(self, key, minimum, maximum, default):
        editor = self._spin_box(CompactSpinBox())
        editor.setRange(minimum, maximum)
        editor.setValue(default)
        return self._register(key, editor)

    def _number(self, key, minimum, maximum, default, step, decimals):
        editor = self._spin_box(CompactDoubleSpinBox())
        editor.setRange(minimum, maximum)
        editor.setValue(default)
        editor.setSingleStep(step)
        editor.setDecimals(decimals)
        return self._register(key, editor)

    def _color(self, key, default, config_key, title):
        editor = ColorPickerWidget(
            # Keep the translation key here; ColorPickerWidget resolves it
            # when the flyout opens so a live language switch is reflected.
            dialog_title=title,
            default_color=default,
            config_key=config_key,
            config_service=self.config_service,
            i18n_func=self._t,
        )
        return self._register(key, editor)

    def _font(self, key):
        editor = QFontComboBox(self)
        editor.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        populate_font_combo(editor)
        return self._register(key, editor)

    def _effect(self, key, color, config_key, value_name, default):
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        picker = ColorPickerWidget(
            dialog_title={
                "stroke": "Select stroke color",
                "outerStroke": "Select outer stroke color",
                "glow": "Select glow color",
            }[key],
            default_color=color,
            config_key=config_key,
            config_service=self.config_service,
            i18n_func=self._t,
        )
        value = self._spin_box(CompactDoubleSpinBox())
        value.setRange(0, 20)
        value.setDecimals(2)
        value.setSingleStep(0.05)
        value.setValue(default)
        color_label = CaptionLabel(self._t("Color"))
        value_label = CaptionLabel(self._t(value_name.title()))
        layout.addWidget(color_label)
        layout.addWidget(picker, 1)
        layout.addWidget(value_label)
        layout.addWidget(value)
        field = self._register(key, container)
        field.color_picker = picker
        field.value_input = value
        field.value_name = value_name
        field.color_label = color_label
        field.value_label = value_label
        return field

    def _saved_rule_styles(self) -> dict[str, dict]:
        config_service = self.config_service or get_config_service()
        if config_service is None:
            return {}
        self.config_service = config_service
        try:
            config_ref = config_service.get_config_reference()
        except Exception:
            return {}
        raw = getattr(getattr(config_ref, "app", None), "saved_rich_text_presets", None)
        if not isinstance(raw, dict):
            return {}

        styles: dict[str, dict] = {}
        for name, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            try:
                style = normalize_text_style(payload.get("style") or {})
            except (TypeError, ValueError):
                continue
            if payload.get("tcy", False):
                style["tcy"] = True
            ruby_text = payload.get("ruby", "")
            if isinstance(ruby_text, str) and ruby_text:
                style["ruby"] = ruby_text
            clean_name = str(name).strip()
            if clean_name and style:
                styles[clean_name] = style
        return styles

    def _refresh_saved_style_combo(self) -> None:
        styles = self._saved_rule_styles()
        current_name = self.saved_style_combo.currentData() if self.saved_style_combo.count() else None
        self.saved_style_combo.blockSignals(True)
        try:
            self.saved_style_combo.clear()
            self.saved_style_combo.addItem(self._t("Select saved rich text style"), userData=None)
            for name in styles:
                self.saved_style_combo.addItem(name, userData=name)
            index = self.saved_style_combo.findData(current_name)
            self.saved_style_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.saved_style_combo.blockSignals(False)
        self.saved_style_combo.setToolTip(self._t("Choose a saved rich text style to load"))

    def _on_saved_style_activated(self, index: int) -> None:
        name = self.saved_style_combo.itemData(index)
        if not name:
            return
        style = self._saved_rule_styles().get(str(name))
        if style:
            self.load_style(style)

    def refresh_saved_styles(self) -> None:
        """重新读取共享的富文本预设，供规则页重新激活时调用。"""
        self._refresh_saved_style_combo()

    def refresh_ui_texts(self) -> None:
        self.bold.setText(self._t("Bold"))
        self.emphasis.setText(self._t("Emphasis"))
        self.tcy.setText(self._t("Vertical-in-Horizontal (TCY)"))
        self.ruby.editor.setPlaceholderText(self._t("Ruby text"))
        for label, key in self._form_label_keys.items():
            if hasattr(label, "setText"):
                label.setText(self._t(key))
        for field in self._fields.values():
            if hasattr(field, "choice_labels"):
                for index, (label_key, _value) in enumerate(field.choice_labels):
                    field.editor.setItemText(index, self._t(label_key))
            if hasattr(field, "color_label"):
                field.color_label.setText(self._t("Color"))
            if hasattr(field, "value_label"):
                field.value_label.setText(self._t(field.value_name.title()))
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_ui_texts()
        self.refresh_saved_styles()

    def load_style(self, style: dict):
        style = copy.deepcopy(style or {})
        ruby_text = str(style.pop("ruby", "") or "")
        self.tcy.setChecked(bool(style.pop("tcy", False)))
        self.ruby.editor.setText(ruby_text)
        self.ruby.set_active(bool(ruby_text))
        values = text_style_to_control_values(style)
        self.bold.setChecked(bool(values["bold"]))
        self.emphasis.setChecked(bool(values["emphasis"]))
        for key, field in self._fields.items():
            value = values.get(key)
            field.set_active(value is not None)
            if value is None:
                continue
            if hasattr(field, "color_picker"):
                field.color_picker.set_color(str(value.get("color") or "#000000"))
                field.value_input.setValue(float(value.get(field.value_name, 0) or 0))
            elif isinstance(field.editor, ColorPickerWidget):
                field.editor.set_color(str(value))
            elif isinstance(field.editor, QFontComboBox):
                field.editor.setCurrentFont(QFont(str(value)))
            elif isinstance(field.editor, ComboBox):
                field.editor.setCurrentIndex(max(0, field.editor.findData(value)))
            else:
                field.editor.setValue(value)

    def style(self) -> dict:
        values = {"bold": True, "emphasis": True}
        enabled = set()
        if self.bold.isChecked(): enabled.add("bold")
        if self.emphasis.isChecked(): enabled.add("emphasis")
        for key, field in self._fields.items():
            if not field.enabled.isChecked():
                continue
            enabled.add(key)
            if hasattr(field, "color_picker"):
                values[key] = {
                    "color": field.color_picker.get_color(),
                    field.value_name: float(field.value_input.value()),
                }
            elif isinstance(field.editor, ColorPickerWidget):
                values[key] = field.editor.get_color()
            elif isinstance(field.editor, QFontComboBox):
                values[key] = field.editor.currentFont().family()
            elif isinstance(field.editor, ComboBox):
                values[key] = field.editor.currentData()
            else:
                values[key] = field.editor.value()
        style = text_style_from_control_values(values, enabled)
        ruby_text = self.ruby.editor.text()
        if self.ruby.enabled.isChecked() and ruby_text:
            style["ruby"] = ruby_text
        if self.tcy.isChecked():
            style["tcy"] = True
        return style

    def reset(self):
        self.load_style({})


class RichTextStyleDialog(FluentSecondaryDialog):
    def __init__(self, style: dict, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._result_style = copy.deepcopy(style or {})
        self.setWindowTitle(self._t("Edit Rich Text Style"))
        self.setMinimumSize(620, 520)
        self.resize(660, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        self.title_label = SubtitleLabel(self._t("Edit Rich Text Style"))
        root.addWidget(self.title_label)
        self.hint_label = BodyLabel(self._t("Enable only the style properties this rule should apply."))
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)
        # 19 行表单包进纵向滚动区，小屏时内容可滚动而不是把窗口撑出屏
        self.controls_scroll = SingleDirectionScrollArea(self, orient=Qt.Orientation.Vertical)
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(SingleDirectionScrollArea.Shape.NoFrame)
        self.controls = RichTextStyleControls(self._t, self)
        self.controls.load_style(style or {})
        self.controls_scroll.setWidget(self.controls)
        self.controls_scroll.enableTransparentBackground()
        root.addWidget(self.controls_scroll, 1)
        buttons = QHBoxLayout()
        self.reset_button = PushButton(self._t("Reset"))
        self.cancel_button = PushButton(self._t("Cancel"))
        self.ok_button = PushButton(self._t("OK"))
        self.ok_button.setIcon(FIF.ACCEPT)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)
        self.reset_button.clicked.connect(self.controls.reset)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._accept)

    def refresh_ui_texts(self) -> None:
        self.setWindowTitle(self._t("Edit Rich Text Style"))
        self.title_label.setText(self._t("Edit Rich Text Style"))
        self.hint_label.setText(self._t("Enable only the style properties this rule should apply."))
        self.controls.refresh_ui_texts()
        self.reset_button.setText(self._t("Reset"))
        self.cancel_button.setText(self._t("Cancel"))
        self.ok_button.setText(self._t("OK"))

    def _accept(self):
        try:
            self._result_style = self.controls.style()
        except Exception as exc:
            themed_warning(self, self._t("Invalid Style"), str(exc))
            return
        self.accept()

    def style(self) -> dict:
        return copy.deepcopy(self._result_style)


class RichTextRulesEditorPanel(CardWidget):
    data_changed = pyqtSignal()
    _AUTOSAVE_DELAY_MS = 600
    COL_ENABLED, COL_PATTERN, COL_STYLE, COL_REGEX, COL_COMMENT = range(5)
    _YES, _NO = "✓", "✗"

    def __init__(self, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda value, **kwargs: value)
        self._file_path = _rules_path()
        self._modified = False
        self._raw_mode = False
        self._current_group = "common"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._save)
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        toolbar = SimpleCardWidget(self)
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(10, 8, 10, 8)
        self.add_button = PushButton(self._t("Add Rule"), icon=FIF.ADD)
        self.delete_button = PushButton(self._t("Delete"), icon=FIF.DELETE)
        self.up_button = PushButton("↑", icon=FIF.UP)
        self.down_button = PushButton("↓", icon=FIF.DOWN)
        self.toggle_enabled_button = PushButton(self._t("Enable"), icon=FIF.ACCEPT)
        self.toggle_regex_button = PushButton(self._t("Regex"), icon=FIF.CODE)
        self.restore_button = PushButton(self._t("Restore Default"), icon=FIF.SYNC)
        for button in (self.add_button, self.delete_button, self.up_button, self.down_button,
                       self.toggle_enabled_button, self.toggle_regex_button):
            bar.addWidget(button)
        bar.addStretch()
        bar.addWidget(self.restore_button)
        root.addWidget(toolbar)

        filter_card = SimpleCardWidget(self)
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(10, 8, 10, 8)
        self.filter_label = CaptionLabel(self._t("Filter:"))
        filter_layout.addWidget(self.filter_label)
        self.search = FluentLineEdit()
        self.search.setPlaceholderText(self._t("Type to filter by pattern / style / comment..."))
        self.search.setClearButtonEnabled(True)
        filter_layout.addWidget(self.search, 1)
        root.addWidget(filter_card)
        self.filter_card = filter_card

        self.mode_segment = SegmentedWidget(self)
        self.mode_stack = PopUpAniStackedWidget(self)
        self.mode_pages: Dict[str, QWidget] = {}
        table_page = SimpleCardWidget(self.mode_stack)
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(10, 10, 10, 10)
        self.group_segment = SegmentedWidget(table_page)
        self.group_stack = PopUpAniStackedWidget(table_page)
        self.tables: Dict[str, TableWidget] = {}
        for key, label in (("common", "Common (Always)"), ("horizontal", "Horizontal"), ("vertical", "Vertical")):
            table = self._create_table()
            self.tables[key] = table
            self.group_stack.addWidget(table)
            self.group_segment.addItem(key, self._t(label), onClick=lambda checked=False, value=key: self._set_group(value))
        table_layout.addWidget(self.group_segment)
        table_layout.addWidget(self.group_stack, 1)
        self.mode_stack.addWidget(table_page)
        self.mode_pages["table"] = table_page

        raw_page = SimpleCardWidget(self.mode_stack)
        raw_layout = QVBoxLayout(raw_page)
        raw_layout.setContentsMargins(10, 10, 10, 10)
        self.raw_hint = CaptionLabel(self._t("Edit raw YAML content directly. Changes are saved automatically."))
        self.raw_editor = PlainTextEdit()
        self.raw_editor.setFont(_fixed_width_font(10))
        self.raw_editor.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = YamlHighlighter(self.raw_editor.document())
        raw_layout.addWidget(self.raw_hint)
        raw_layout.addWidget(self.raw_editor, 1)
        self.mode_stack.addWidget(raw_page)
        self.mode_pages["raw"] = raw_page
        self.mode_segment.addItem("table", self._t("Table View"), onClick=lambda checked=False: self._set_mode(False))
        self.mode_segment.addItem("raw", self._t("Raw Edit"), onClick=lambda checked=False: self._set_mode(True))
        root.addWidget(self.mode_segment)
        root.addWidget(self.mode_stack, 1)
        self.status = CaptionLabel("")
        root.addWidget(self.status)

        self.add_button.clicked.connect(self._add_rule)
        self.delete_button.clicked.connect(self._delete_rule)
        self.up_button.clicked.connect(lambda: self._move_rule(-1))
        self.down_button.clicked.connect(lambda: self._move_rule(1))
        self.toggle_enabled_button.clicked.connect(lambda: self._toggle_column(self.COL_ENABLED))
        self.toggle_regex_button.clicked.connect(lambda: self._toggle_column(self.COL_REGEX))
        self.restore_button.clicked.connect(self._restore)
        self.search.textChanged.connect(self._filter)
        self.raw_editor.textChanged.connect(self._changed)
        self._set_group("common")
        self._set_mode(False)

    def _create_table(self) -> TableWidget:
        table = TableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([self._t("Enabled"), self._t("Pattern"), self._t("Rich Text Style"), self._t("Regex"), self._t("Comment")])
        table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(TableWidget.SelectionMode.ExtendedSelection)
        header = table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ENABLED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_PATTERN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_STYLE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_REGEX, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_COMMENT, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(self.COL_ENABLED, 55)
        table.setColumnWidth(self.COL_REGEX, 55)
        table.cellChanged.connect(self._changed)
        table.cellDoubleClicked.connect(self._double_click)
        return table

    def _set_group(self, group: str):
        self._current_group = group
        self.group_stack.setCurrentWidget(self.tables[group])
        self.group_segment.setCurrentItem(group)

    def _set_mode(self, raw: bool):
        if raw and not self._raw_mode:
            self._sync_raw_from_tables()
        elif not raw and self._raw_mode:
            if not self._sync_tables_from_raw(show_error=True):
                self.mode_segment.setCurrentItem("raw")
                return
        self._raw_mode = raw
        self.mode_stack.setCurrentWidget(self.mode_pages["raw" if raw else "table"])
        self.mode_segment.setCurrentItem("raw" if raw else "table")
        self.filter_card.setVisible(not raw)

    def _style_button(self, table: TableWidget, row: int, style: dict) -> PushButton:
        button = PushButton(_style_summary(style, self._t("Edit Style")))
        self._set_button_style(button, style)
        button.clicked.connect(lambda checked=False, target=button: self._edit_style(target))
        table.setCellWidget(row, self.COL_STYLE, button)
        return button

    @staticmethod
    def _set_button_style(button: PushButton, style: dict) -> None:
        """样式与它的搜索文本一起缓存，过滤时不再逐行序列化 JSON。"""
        button.setProperty("richStyle", copy.deepcopy(style))
        button.setProperty("styleHaystack", json.dumps(style, ensure_ascii=False).lower())

    def _insert(self, table: TableWidget, rule: dict, row: int | None = None):
        row = table.rowCount() if row is None else row
        table.insertRow(row)
        for column, value in ((self.COL_ENABLED, self._YES if rule.get("enabled", True) else self._NO),
                              (self.COL_PATTERN, str(rule.get("pattern", ""))),
                              (self.COL_REGEX, self._YES if rule.get("regex", False) else self._NO),
                              (self.COL_COMMENT, str(rule.get("comment", "")))):
            item = QTableWidgetItem(value)
            if column in (self.COL_ENABLED, self.COL_REGEX):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)
        editor_style = copy.deepcopy(rule.get("style") or {})
        ruby = rule.get("ruby") or ""
        if isinstance(ruby, str) and ruby:
            editor_style["ruby"] = ruby
        if rule.get("tcy", False):
            editor_style["tcy"] = True
        self._style_button(table, row, editor_style)

    def _row_data(self, table: TableWidget, row: int) -> dict:
        button = table.cellWidget(row, self.COL_STYLE)
        editor_style = copy.deepcopy(button.property("richStyle") or {})
        ruby = str(editor_style.pop("ruby", "") or "")
        tcy = bool(editor_style.pop("tcy", False))
        return {
            "enabled": table.item(row, self.COL_ENABLED).text() == self._YES,
            "pattern": table.item(row, self.COL_PATTERN).text(),
            "regex": table.item(row, self.COL_REGEX).text() == self._YES,
            "style": editor_style,
            "ruby": ruby,
            "tcy": tcy,
            "comment": table.item(row, self.COL_COMMENT).text(),
        }

    def _table_data(self) -> dict:
        return {key: [self._row_data(table, row) for row in range(table.rowCount())] for key, table in self.tables.items()}

    def _load(self):
        try:
            raw = open(self._file_path, "r", encoding="utf-8").read()
            data = yaml.safe_load(raw) or {}
        except Exception as exc:
            self.status.setText(f"{self._t('Load error')}: {exc}")
            return
        self._populate(data)
        self.raw_editor.blockSignals(True)
        self.raw_editor.setPlainText(raw)
        self.raw_editor.blockSignals(False)
        self._modified = False
        self._timer.stop()
        self.status.setText(self._t("All changes saved"))

    def _populate(self, data: dict):
        for key, table in self.tables.items():
            table.blockSignals(True)
            table.setRowCount(0)
            for rule in data.get(key, []) if isinstance(data.get(key, []), list) else []:
                if isinstance(rule, dict): self._insert(table, rule)
            table.blockSignals(False)

    def _sync_raw_from_tables(self):
        self.raw_editor.blockSignals(True)
        self.raw_editor.setPlainText(yaml.safe_dump(self._table_data(), allow_unicode=True, sort_keys=False, width=120))
        self.raw_editor.blockSignals(False)

    def _sync_tables_from_raw(self, show_error=False) -> bool:
        try:
            data = yaml.safe_load(self.raw_editor.toPlainText()) or {}
            if not isinstance(data, dict): raise ValueError(self._t("YAML root must be a mapping"))
            for group in ("common", "horizontal", "vertical"):
                if group in data and not isinstance(data[group], list):
                    raise ValueError(self._t("Rule group '{group}' must be a list", group=group))
        except Exception as exc:
            if show_error: themed_warning(self, self._t("YAML Error"), str(exc))
            return False
        self._populate(data)
        return True

    def _changed(self, *args):
        self._modified = True
        self.status.setText(self._t("Saving..."))
        self._timer.start(self._AUTOSAVE_DELAY_MS)

    def _save(self):
        raw = self.raw_editor.toPlainText() if self._raw_mode else yaml.safe_dump(self._table_data(), allow_unicode=True, sort_keys=False, width=120)
        try:
            data = yaml.safe_load(raw) or {}
            if not isinstance(data, dict): raise ValueError(self._t("YAML root must be a mapping"))
            with open(self._file_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(raw.rstrip() + "\n")
            from manga_translator.rendering.rich_text_rules import (
                invalidate_rich_text_rules_cache,
                load_rich_text_rules,
            )

            invalidate_rich_text_rules_cache(self._file_path)
            load_rich_text_rules(self._file_path)
        except Exception as exc:
            self.status.setText(f"{self._t('Save error')}: {exc}")
            return
        if not self._raw_mode:
            self._sync_raw_from_tables()
        self._modified = False
        self.status.setText(self._t("All changes saved"))
        self.data_changed.emit()

    def _add_rule(self):
        if self._raw_mode: return
        table = self.tables[self._current_group]
        table.blockSignals(True)
        self._insert(table, {"enabled": True, "pattern": "", "regex": False, "style": {}, "comment": ""})
        table.blockSignals(False)
        row = table.rowCount() - 1
        table.selectRow(row)
        table.editItem(table.item(row, self.COL_PATTERN))
        self._changed()

    def _delete_rule(self):
        if self._raw_mode: return
        table = self.tables[self._current_group]
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        for row in rows: table.removeRow(row)
        if rows: self._changed()

    def _move_rule(self, delta: int):
        if self._raw_mode: return
        table = self.tables[self._current_group]
        row = table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= table.rowCount(): return
        first, second = self._row_data(table, row), self._row_data(table, target)
        table.blockSignals(True)
        table.removeRow(max(row, target))
        table.removeRow(min(row, target))
        low = min(row, target)
        ordered = (first, second) if row < target else (second, first)
        self._insert(table, ordered[1], low)
        self._insert(table, ordered[0], low + 1)
        table.blockSignals(False)
        table.selectRow(target)
        self._changed()

    def _toggle_column(self, column: int):
        table = self.tables[self._current_group]
        rows = sorted({index.row() for index in table.selectedIndexes()})
        if not rows and table.currentRow() >= 0: rows = [table.currentRow()]
        if not rows: return
        enable = any(table.item(row, column).text() != self._YES for row in rows)
        table.blockSignals(True)
        for row in rows: table.item(row, column).setText(self._YES if enable else self._NO)
        table.blockSignals(False)
        self._changed()

    def _double_click(self, row: int, column: int):
        if column in (self.COL_ENABLED, self.COL_REGEX):
            table = self.tables[self._current_group]
            item = table.item(row, column)
            item.setText(self._NO if item.text() == self._YES else self._YES)
            self._changed()
        elif column == self.COL_STYLE:
            self._edit_style(self.tables[self._current_group].cellWidget(row, column))

    def _edit_style(self, button: PushButton):
        dialog = RichTextStyleDialog(button.property("richStyle") or {}, self._t, self)
        if dialog.exec() == DialogCode.Accepted:
            style = dialog.style()
            self._set_button_style(button, style)
            button.setText(_style_summary(style, self._t("Edit Style")))
            self._changed()

    def _filter(self, text: str):
        # 样式(含 ruby/tcy)的搜索文本在设置样式时已缓存到按钮属性，
        # 每次按键只做字符串拼接与包含判断。
        query = text.strip().lower()
        for table in self.tables.values():
            for row in range(table.rowCount()):
                haystack = " ".join((
                    table.item(row, self.COL_PATTERN).text(),
                    table.item(row, self.COL_COMMENT).text(),
                    str(table.cellWidget(row, self.COL_STYLE).property("styleHaystack") or ""),
                )).lower()
                table.setRowHidden(row, bool(query and query not in haystack))

    def _restore(self):
        reply = themed_question(
            self,
            self._t("Restore Default"),
            self._t("Restore rich text rules to the built-in defaults? Current custom rules will be overwritten."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from manga_translator.rendering.rich_text_rules import (
            reset_rich_text_rules_to_default,
        )

        reset_rich_text_rules_to_default(self._file_path)
        self._load()

    def refresh(self):
        if not self._modified: self._load()

    def refresh_ui_texts(self):
        self.add_button.setText(self._t("Add Rule"))
        self.delete_button.setText(self._t("Delete"))
        self.toggle_enabled_button.setText(self._t("Enable"))
        self.toggle_regex_button.setText(self._t("Regex"))
        self.restore_button.setText(self._t("Restore Default"))
        self.filter_label.setText(self._t("Filter:"))
        self.search.setPlaceholderText(self._t("Type to filter by pattern / style / comment..."))
        self.raw_hint.setText(self._t("Edit raw YAML content directly. Changes are saved automatically."))
        self.mode_segment.setItemText("table", self._t("Table View"))
        self.mode_segment.setItemText("raw", self._t("Raw Edit"))
        for key, label in (
            ("common", "Common (Always)"),
            ("horizontal", "Horizontal"),
            ("vertical", "Vertical"),
        ):
            self.group_segment.setItemText(key, self._t(label))
        for table in self.tables.values():
            table.setHorizontalHeaderLabels([
                self._t("Enabled"),
                self._t("Pattern"),
                self._t("Rich Text Style"),
                self._t("Regex"),
                self._t("Comment"),
            ])
            for row in range(table.rowCount()):
                button = table.cellWidget(row, self.COL_STYLE)
                if button is not None:
                    button.setText(_style_summary(
                        button.property("richStyle") or {},
                        self._t("Edit Style"),
                    ))
        if not self._modified:
            self.status.setText(self._t("All changes saved"))

    def apply_theme(self):
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()
        self.update()
