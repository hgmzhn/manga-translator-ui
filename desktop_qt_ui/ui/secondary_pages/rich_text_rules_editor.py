"""Visual editor for automatic rich-text rules."""

from __future__ import annotations

import copy
import json
from typing import Callable, Dict

import yaml
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGridLayout,
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
    DoubleSpinBox,
    PlainTextEdit,
    PopUpAniStackedWidget,
    PushButton,
    SegmentedWidget,
    SimpleCardWidget,
    SingleDirectionScrollArea,
    SpinBox,
    SubtitleLabel,
    TableWidget,
    ToolButton,
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
from ui.secondary_pages.base_rule_editor import (
    BaseYamlRuleEditorPanel,
    YamlHighlighter,
    _fixed_width_font,
)
from ui.secondary_pages.themed_message_box import themed_question, themed_warning
from ui.widgets.color_picker import ColorPickerWidget
from ui.widgets.wheel_filter import TopLevelComboBox as ComboBox
from utils.font_list import FontComboBox


def _rules_path() -> str:
    from manga_translator.rendering.rich_text_rules import ensure_rich_text_rules_exists

    return ensure_rich_text_rules_exists()


def _style_summary(style: dict, empty_text: str) -> str:
    if not style:
        return empty_text
    labels = {
        "bold": "B",
        "italic": "I",
        "underline": "U",
        "strikethrough": "ST",
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
    summary = []
    for key, value in style.items():
        if key != "transform" or not isinstance(value, dict):
            summary.append(labels.get(key, key))
            continue
        if "scaleX" in value or "scaleY" in value:
            summary.append("WH")
        if any(
            transform_key in value
            for transform_key in (
                "offsetX",
                "offsetY",
                "rotation",
                "mirrorX",
                "mirrorY",
            )
        ):
            summary.append(labels["transform"])
    return " ".join(summary)


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
        self.saved_style_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.saved_style_combo.activated.connect(self._on_saved_style_activated)
        self._refresh_saved_style_combo()
        add_form_row("Saved rich text style:", self.saved_style_combo)

        self.bold = CheckBox(self._t("Bold"))
        self.underline = CheckBox(self._t("Underline"))
        self.strikethrough = CheckBox(self._t("Strikethrough"))
        self.emphasis = CheckBox(self._t("Emphasis"))
        self.tcy = CheckBox(self._t("Vertical-in-Horizontal (TCY)"))
        switches = QWidget(self)
        switch_layout = QGridLayout(switches)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setHorizontalSpacing(12)
        switch_layout.setVerticalSpacing(4)
        switch_layout.addWidget(self.bold, 0, 0)
        switch_layout.addWidget(self.underline, 0, 1)
        switch_layout.addWidget(self.strikethrough, 0, 2)
        switch_layout.addWidget(self.emphasis, 1, 0)
        switch_layout.addWidget(self.tcy, 1, 1, 1, 2)
        switch_layout.setColumnStretch(2, 1)
        add_form_row("Switches", switches)

        ruby_editor = FluentLineEdit(self)
        ruby_editor.setPlaceholderText(self._t("Ruby text"))
        self.ruby = _OptionalStyleField("ruby", ruby_editor, self)
        add_form_row("Ruby Text", self.ruby)

        self.italic = self._number("italic", -85, 85, 15, 1, 1)
        self.color = self._color(
            "color", "#E53935", "saved_colors", "Select rich text color"
        )
        self.font_size = self._integer("fontSize", 1, 1000, 24)
        self.scale = self._number("scale", 0.1, 10, 1.2, 0.05, 2)
        self.scale_x = self._number("scaleX", 0.1, 10, 1.2, 0.05, 2)
        self.scale_y = self._number("scaleY", 0.1, 10, 1.2, 0.05, 2)
        advance_choices = (("Half Advance", "half"), ("Full Advance", "full"))
        advance_editor = ComboBox(self)
        for label, value in advance_choices:
            advance_editor.addItem(self._t(label), userData=value)
        self.vertical_advance = self._register("verticalAdvance", advance_editor)
        self.vertical_advance.choice_labels = advance_choices
        self.font_family = self._font("fontFamily")
        self.stroke = self._effect(
            "stroke", "#FFFFFF", "saved_stroke_colors", "width", 0.07
        )
        self.outer_stroke = self._effect(
            "outerStroke", "#000000", "saved_outer_stroke_colors", "width", 0.20
        )
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
            ("Italic Angle", self.italic),
            ("Text Color", self.color),
            ("Font Size", self.font_size),
            ("Scale", self.scale),
            ("Width Stretch", self.scale_x),
            ("Height Stretch", self.scale_y),
            ("Force Advance", self.vertical_advance),
            ("Font Family", self.font_family),
            ("Stroke", self.stroke),
            ("Outer Stroke", self.outer_stroke),
            ("Glow", self.glow),
            ("Kerning", self.kerning),
            ("Pre Kerning", self.pre_kerning),
            ("Line Kerning", self.line_kerning),
            ("Next Kerning", self.next_kerning),
            ("Rotation", self.rotation),
            ("Offset X", self.offset_x),
            ("Offset Y", self.offset_y),
        ):
            add_form_row(label, field)

    @staticmethod
    def _spin_box(spin):
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    def _register(self, key: str, editor: QWidget) -> _OptionalStyleField:
        field = _OptionalStyleField(key, editor, self)
        self._fields[key] = field
        return field

    def _integer(self, key, minimum, maximum, default):
        editor = self._spin_box(SpinBox())
        editor.setRange(minimum, maximum)
        editor.setValue(default)
        return self._register(key, editor)

    def _number(self, key, minimum, maximum, default, step, decimals):
        editor = self._spin_box(DoubleSpinBox())
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
        locale_getter = self.i18n.get_current_locale if self.i18n else None
        editor = FontComboBox(self, locale_getter=locale_getter)
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
        value = self._spin_box(DoubleSpinBox())
        value.setRange(0, 20)
        value.setDecimals(2)
        value.setSingleStep(0.01)
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
        current_name = (
            self.saved_style_combo.currentData()
            if self.saved_style_combo.count()
            else None
        )
        self.saved_style_combo.blockSignals(True)
        try:
            self.saved_style_combo.clear()
            self.saved_style_combo.addItem(
                self._t("Select saved rich text style"), userData=None
            )
            for name in styles:
                self.saved_style_combo.addItem(name, userData=name)
            index = self.saved_style_combo.findData(current_name)
            self.saved_style_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.saved_style_combo.blockSignals(False)
        self.saved_style_combo.setToolTip(
            self._t("Choose a saved rich text style to load")
        )

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
        self.font_family.editor.refresh_ui_texts()
        self.bold.setText(self._t("Bold"))
        self.underline.setText(self._t("Underline"))
        self.strikethrough.setText(self._t("Strikethrough"))
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
        self.underline.setChecked(bool(values["underline"]))
        self.strikethrough.setChecked(bool(values["strikethrough"]))
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
            elif isinstance(field.editor, FontComboBox):
                field.editor.setCurrentFamily(str(value))
            elif isinstance(field.editor, ComboBox):
                field.editor.setCurrentIndex(max(0, field.editor.findData(value)))
            else:
                field.editor.setValue(value)

    def style(self) -> dict:
        values = {
            "bold": True,
            "underline": True,
            "strikethrough": True,
            "emphasis": True,
        }
        enabled = set()
        if self.bold.isChecked():
            enabled.add("bold")
        if self.underline.isChecked():
            enabled.add("underline")
        if self.strikethrough.isChecked():
            enabled.add("strikethrough")
        if self.emphasis.isChecked():
            enabled.add("emphasis")
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
            elif isinstance(field.editor, FontComboBox):
                values[key] = field.editor.currentFamily()
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
        self.hint_label = BodyLabel(
            self._t("Enable only the style properties this rule should apply.")
        )
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)
        # 19 行表单包进纵向滚动区，小屏时内容可滚动而不是把窗口撑出屏
        self.controls_scroll = SingleDirectionScrollArea(
            self, orient=Qt.Orientation.Vertical
        )
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
        self.hint_label.setText(
            self._t("Enable only the style properties this rule should apply.")
        )
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


class RichTextRulesEditorPanel(BaseYamlRuleEditorPanel):
    """富文本规则可视化编辑面板"""

    COL_STYLE = 2

    def _get_file_path(self) -> str:
        return _rules_path()

    def _get_header_labels(self) -> list[str]:
        return [
            self._t("Enabled"),
            self._t("Pattern"),
            self._t("Rich Text Style"),
            self._t("Regex"),
            self._t("Comment"),
        ]

    def _get_filter_placeholder(self) -> str:
        return self._t("Type to filter by pattern / style / comment...")

    def _get_restore_confirm_message(self) -> str:
        return self._t(
            "Restore rich text rules to the built-in defaults? Current custom rules will be overwritten."
        )

    def _do_restore_default(self) -> None:
        from manga_translator.rendering.rich_text_rules import (
            reset_rich_text_rules_to_default,
        )

        reset_rich_text_rules_to_default(self._file_path)

    def _invalidate_cache(self) -> None:
        from manga_translator.rendering.rich_text_rules import (
            invalidate_rich_text_rules_cache,
            load_rich_text_rules,
        )

        invalidate_rich_text_rules_cache(self._file_path)
        load_rich_text_rules(self._file_path)

    def _create_rule_template(self) -> dict:
        return {
            "enabled": True,
            "pattern": "",
            "regex": False,
            "style": {},
            "comment": "",
        }

    def _create_table(self) -> TableWidget:
        table = super()._create_table()
        table.horizontalHeader().setSectionResizeMode(
            self.COL_STYLE, QHeaderView.ResizeMode.ResizeToContents
        )
        return table

    def _style_button(self, table: TableWidget, row: int, style: dict) -> PushButton:
        button = PushButton(_style_summary(style, self._t("Edit Style")))
        self._set_button_style(button, style)
        button.clicked.connect(
            lambda checked=False, target=button: self._edit_style(target)
        )
        table.setCellWidget(row, self.COL_STYLE, button)
        return button

    @staticmethod
    def _set_button_style(button: PushButton, style: dict) -> None:
        """样式与它的搜索文本一起缓存，过滤时不再逐行序列化 JSON。"""
        button.setProperty("richStyle", copy.deepcopy(style))
        button.setProperty(
            "styleHaystack", json.dumps(style, ensure_ascii=False).lower()
        )

    def _populate_row(self, table: TableWidget, row: int, rule: dict) -> None:
        for column, value in (
            (self.COL_ENABLED, self._YES if rule.get("enabled", True) else self._NO),
            (self.COL_PATTERN, str(rule.get("pattern", ""))),
            (self.COL_REGEX, self._YES if rule.get("regex", False) else self._NO),
            (self.COL_COMMENT, str(rule.get("comment", ""))),
        ):
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

    def _extract_row_data(self, table: TableWidget, row: int) -> Optional[dict]:
        button = table.cellWidget(row, self.COL_STYLE)
        editor_style = copy.deepcopy(button.property("richStyle") or {}) if button else {}
        ruby = str(editor_style.pop("ruby", "") or "")
        tcy = bool(editor_style.pop("tcy", False))
        pattern_item = table.item(row, self.COL_PATTERN)
        enabled_item = table.item(row, self.COL_ENABLED)
        regex_item = table.item(row, self.COL_REGEX)
        comment_item = table.item(row, self.COL_COMMENT)

        pattern = pattern_item.text() if pattern_item else ""
        return {
            "enabled": (enabled_item.text() == self._YES) if enabled_item else True,
            "pattern": pattern,
            "regex": (regex_item.text() == self._YES) if regex_item else False,
            "style": editor_style,
            "ruby": ruby,
            "tcy": tcy,
            "comment": comment_item.text() if comment_item else "",
        }

    def _get_row_filter_haystack(self, table: TableWidget, row: int) -> str:
        pattern = table.item(row, self.COL_PATTERN)
        comment = table.item(row, self.COL_COMMENT)
        button = table.cellWidget(row, self.COL_STYLE)
        style_haystack = str(button.property("styleHaystack") or "") if button else ""
        return " ".join([
            pattern.text() if pattern else "",
            comment.text() if comment else "",
            style_haystack,
        ]).lower()

    def _on_table_double_click(self, table: TableWidget, row: int, column: int) -> None:
        if column == self.COL_STYLE:
            button = table.cellWidget(row, column)
            if isinstance(button, PushButton):
                self._edit_style(button)

    def _edit_style(self, button: PushButton) -> None:
        dialog = RichTextStyleDialog(button.property("richStyle") or {}, self._t, self)
        if dialog.exec() == DialogCode.Accepted:
            style = dialog.style()
            self._set_button_style(button, style)
            button.setText(_style_summary(style, self._t("Edit Style")))
            self._mark_modified()

    def _refresh_ui_texts_extra(self) -> None:
        for table in self.tables.values():
            for row in range(table.rowCount()):
                button = table.cellWidget(row, self.COL_STYLE)
                if button is not None:
                    button.setText(
                        _style_summary(
                            button.property("richStyle") or {},
                            self._t("Edit Style"),
                        )
                    )

    def _apply_theme_extra(self) -> None:
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()

