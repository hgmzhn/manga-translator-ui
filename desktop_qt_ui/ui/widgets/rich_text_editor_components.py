"""Composable widgets for the floating rich-text editor."""

from __future__ import annotations

import copy
from collections.abc import Iterable

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QFontComboBox,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CompactDoubleSpinBox,
    CompactSpinBox,
    FluentIcon as FIF,
    PushButton,
    TextEdit,
    ToolButton,
    TransparentToolButton,
    isDarkTheme,
    themeColor,
)

from editor.rich_text_editing import StyledTextSegment

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint


STYLE_KEYS = (
    "B", "I", "C", "S", "%", "F", "O", "G", "OS", "D", "T", "R",
    "Rot", "K", "PK", "LK", "NK", "XY", "M", "MV",
)

STYLE_NAME_KEYS = {
    "B": "Bold",
    "I": "Italic",
    "C": "Text Color",
    "S": "Font Size",
    "%": "Scale",
    "F": "Font",
    "O": "Stroke",
    "G": "Glow",
    "OS": "Outer Stroke",
    "D": "Emphasis",
    "T": "TCY",
    "R": "Ruby",
    "Rot": "Rotation",
    "K": "Kerning",
    "PK": "Pre Kerning",
    "LK": "Line Kerning",
    "NK": "Next Kerning",
    "XY": "Offset",
    "M": "Mirror Horizontal",
    "MV": "Mirror Vertical",
}

STYLE_HINT_KEYS = {
    "B": "Bold",
    "I": "Italic Angle",
    "C": "Text Color",
    "S": "Font Size",
    "%": "Scale",
    "F": "Font Family",
    "O": "Stroke",
    "G": "Glow",
    "OS": "Outer Stroke",
    "D": "Emphasis",
    "T": "Vertical-in-Horizontal (TCY)",
    "R": "Ruby Text",
    "Rot": "Rotation",
    "K": "Kerning",
    "PK": "Pre Kerning",
    "LK": "Line Kerning",
    "NK": "Next Kerning",
    "XY": "X / Y Offset",
    "M": "Mirror Horizontal",
    "MV": "Mirror Vertical",
}


class RichTextBodyEdit(TextEdit):
    focus_gained = pyqtSignal()
    focus_lost = pyqtSignal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_gained.emit()

    def focusOutEvent(self, event):
        self.focus_lost.emit()
        super().focusOutEvent(event)


def _double_spin_box(value: float, minimum: float, maximum: float, decimals: int = 2) -> CompactDoubleSpinBox:
    control = CompactDoubleSpinBox()
    control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    control.setRange(minimum, maximum)
    control.setDecimals(decimals)
    control.setValue(float(value))
    return control


def _spin_box(value: int, minimum: int, maximum: int) -> CompactSpinBox:
    control = CompactSpinBox()
    control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    control.setRange(minimum, maximum)
    control.setValue(int(value))
    return control


class RichTextToolbar(QWidget):
    toggled = pyqtSignal(str, bool)

    BUTTONS = STYLE_KEYS

    def __init__(self, parent=None, i18n_func=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QGridLayout

        self._t = i18n_func or (lambda key, **_kwargs: key)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.buttons: dict[str, QToolButton] = {}
        for index, key in enumerate(self.BUTTONS):
            button = QToolButton(self)
            button.setText(key)
            button.setCheckable(True)
            button.setFixedSize(34, 30)
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, style_key=key: self.toggled.emit(style_key, checked)
            )
            layout.addWidget(button, index // 10, index % 10)
            self.buttons[key] = button
        self.refresh_theme()
        self.refresh_ui_texts()

    def refresh_ui_texts(self) -> None:
        for key, button in self.buttons.items():
            text = self._t(STYLE_HINT_KEYS[key])
            set_hover_hint(button, text)
            button.setAccessibleName(text)

    def refresh_theme(self) -> None:
        dark = isDarkTheme()
        accent = QColor(themeColor())
        accent_hover = QColor(accent).lighter(110 if dark else 106)
        accent_pressed = QColor(accent).darker(108)
        text = "rgba(255,255,255,230)" if dark else "rgba(0,0,0,220)"
        background = "rgba(255,255,255,13)" if dark else "rgba(255,255,255,235)"
        border = "rgba(255,255,255,24)" if dark else "rgba(0,0,0,28)"
        hover = "rgba(255,255,255,20)" if dark else "rgba(0,0,0,10)"
        self.setStyleSheet(f"""
            QToolButton {{
                color: {text}; background: {background}; border: 1px solid {border};
                border-radius: 6px; font-size: 13px; font-weight: 600;
            }}
            QToolButton:hover {{ background: {hover}; }}
            QToolButton:pressed {{ background: rgba(128,128,128,32); }}
            QToolButton:checked {{
                color: white; background: {accent.name()}; border-color: {accent.name()};
            }}
            QToolButton:checked:hover {{ background: {accent_hover.name()}; }}
            QToolButton:checked:pressed {{ background: {accent_pressed.name()}; }}
        """)

    def set_checked(self, key: str, checked: bool) -> None:
        button = self.buttons.get(key)
        if button is not None:
            button.setChecked(bool(checked))


class RichTextPresetSidebar(QWidget):
    """Collapsible preset list used by the floating rich-text editor."""

    preset_applied = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    collapsed_changed = pyqtSignal(bool)

    EXPANDED_WIDTH = 248
    COLLAPSED_WIDTH = 38

    def __init__(self, i18n_func=None, parent=None):
        super().__init__(parent)
        self._t = i18n_func or (lambda key, **_kwargs: key)
        self._presets: dict[str, dict] = {}
        self._collapsed = False
        self.setObjectName("richTextPresetSidebar")
        self.setFixedWidth(self.EXPANDED_WIDTH)
        self.refresh_theme()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.title = CaptionLabel(self._t("Rich Text Presets"), self)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle_button = TransparentToolButton(FIF.CARE_LEFT_SOLID, self)
        self.toggle_button.setFixedSize(26, 28)
        self.toggle_button.setObjectName("richTextPresetAction")
        self.toggle_button.clicked.connect(self._toggle_collapsed)
        header.addWidget(self.title, 1)
        header.addWidget(self.toggle_button)
        root.addLayout(header)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget(self.scroll)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(5)
        self.empty_label = CaptionLabel(self._t("No saved styles"), self.content)
        self.empty_label.setWordWrap(True)
        self.content_layout.addWidget(self.empty_label)
        self.content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self._refresh_toggle_state(emit=False)

    def presets(self) -> dict[str, dict]:
        return copy.deepcopy(self._presets)

    def set_presets(self, presets: dict | None) -> None:
        self._presets = {
            str(name): copy.deepcopy(value)
            for name, value in (presets or {}).items()
            if str(name).strip() and isinstance(value, dict)
        }
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self._presets:
            self.empty_label = CaptionLabel(self._t("No saved styles"), self.content)
            self.empty_label.setWordWrap(True)
            self.content_layout.addWidget(self.empty_label)
        else:
            self.empty_label = None
            for name in self._presets:
                self.content_layout.addWidget(self._create_preset_row(name))
        self.content_layout.addStretch(1)

    def set_collapsed(self, collapsed: bool, *, emit: bool = True) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            self._refresh_toggle_state(emit=False)
            return
        self._collapsed = collapsed
        self._refresh_toggle_state(emit=emit)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def refresh_ui_texts(self) -> None:
        self.title.setText(self._t("Rich Text Presets"))
        self.set_presets(self._presets)
        self._refresh_toggle_state(emit=False)

    def refresh_theme(self) -> None:
        dark = isDarkTheme()
        background = "rgba(32,32,32,245)" if dark else "rgba(249,249,249,245)"
        border = "rgba(255,255,255,22)" if dark else "rgba(0,0,0,20)"
        self.setStyleSheet(f"""
            QWidget#richTextPresetSidebar {{
                background: {background}; border-left: 1px solid {border};
            }}
        """)

    def _create_preset_row(self, name: str) -> QWidget:
        row = QWidget(self.content)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        apply_button = PushButton(name, row)
        apply_button.setObjectName("richTextPresetItem")
        apply_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_hover_hint(apply_button, self._t("Choose a saved style to apply"))
        apply_button.setAccessibleName(self._t("Choose a saved style to apply"))
        apply_button.clicked.connect(lambda _checked=False, preset_name=name: self.preset_applied.emit(preset_name))

        rename_button = TransparentToolButton(FIF.EDIT, row)
        rename_button.setObjectName("richTextPresetAction")
        rename_button.setFixedSize(24, 28)
        set_hover_hint(rename_button, self._t("Rename preset"))
        rename_button.setAccessibleName(self._t("Rename preset"))
        rename_button.clicked.connect(lambda _checked=False, preset_name=name: self.rename_requested.emit(preset_name))

        delete_button = TransparentToolButton(FIF.DELETE, row)
        delete_button.setObjectName("richTextPresetAction")
        delete_button.setFixedSize(24, 28)
        set_hover_hint(delete_button, self._t("Delete preset"))
        delete_button.setAccessibleName(self._t("Delete preset"))
        delete_button.clicked.connect(lambda _checked=False, preset_name=name: self.delete_requested.emit(preset_name))

        layout.addWidget(apply_button, 1)
        layout.addWidget(rename_button)
        layout.addWidget(delete_button)
        return row

    def _toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _refresh_toggle_state(self, *, emit: bool) -> None:
        self.setFixedWidth(self.COLLAPSED_WIDTH if self._collapsed else self.EXPANDED_WIDTH)
        self.title.setVisible(not self._collapsed)
        self.scroll.setVisible(not self._collapsed)
        self.toggle_button.setIcon(
            FIF.CARE_RIGHT_SOLID if self._collapsed else FIF.CARE_LEFT_SOLID
        )
        hint = self._t("Expand preset sidebar") if self._collapsed else self._t("Collapse preset sidebar")
        set_hover_hint(self.toggle_button, hint)
        self.toggle_button.setAccessibleName(hint)
        if emit:
            self.collapsed_changed.emit(self._collapsed)


class RubyEditBar(QWidget):
    apply_requested = pyqtSignal(str)
    editing_finished = pyqtSignal(str)
    editing_started = pyqtSignal()
    text_changed = pyqtSignal(str)

    def __init__(self, text: str = "", parent=None, i18n_func=None):
        super().__init__(parent)
        self._t = i18n_func or (lambda key, **_kwargs: key)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.input = QLineEdit(self)
        self.input.setText(str(text or ""))
        self.input.installEventFilter(self)
        self.apply_button = PushButton(self)
        self.apply_button.setMinimumSize(48, 28)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.apply_button)
        self.apply_button.clicked.connect(lambda: self.apply_requested.emit(self.input.text()))
        self.input.returnPressed.connect(lambda: self.apply_requested.emit(self.input.text()))
        self.input.editingFinished.connect(lambda: self.editing_finished.emit(self.input.text()))
        self.input.textChanged.connect(self.text_changed)
        self.refresh_ui_texts()

    def refresh_ui_texts(self) -> None:
        self.input.setPlaceholderText(self._t("Ruby text"))
        self.apply_button.setText(self._t("Apply"))
        self.apply_button.setAccessibleName(self._t("Apply"))

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.Type.FocusIn:
            self.editing_started.emit()
        return super().eventFilter(watched, event)

    def text(self) -> str:
        return self.input.text()

    def focus_input(self) -> None:
        self.input.setFocus()
        self.input.selectAll()


def style_keys_for_segment(segment: StyledTextSegment, forced_keys: Iterable[str] = ()) -> list[str]:
    style = segment.style or {}
    transform = style.get("transform") or {}
    present = {
        "B": bool(style.get("bold")),
        "I": "italic" in style,
        "C": "color" in style,
        "S": "fontSize" in style,
        "%": "scale" in style,
        "F": "fontFamily" in style,
        "O": bool(style.get("stroke")),
        "G": bool(style.get("glow")),
        "OS": bool(style.get("outerStroke")),
        "D": bool(style.get("emphasis")),
        "T": segment.node_type == "tcy" and segment.start == segment.node_start,
        "R": segment.node_type == "ruby" and segment.start == segment.node_start,
        "Rot": "rotation" in transform,
        "K": "kerning" in style,
        "PK": "preKerning" in style,
        "LK": "lineKerning" in style,
        "NK": "nextKerning" in style,
        "XY": "offsetX" in transform or "offsetY" in transform,
        "M": bool(transform.get("mirrorX")),
        "MV": bool(transform.get("mirrorY")),
    }
    present.update({key: True for key in forced_keys})
    return [key for key in STYLE_KEYS if present.get(key)]


class StyleRunCard(QWidget):
    activated = pyqtSignal(int, int)
    patch_requested = pyqtSignal(int, int, str, object)
    remove_requested = pyqtSignal(int, int, str)
    save_preset_requested = pyqtSignal(int, int, object)
    clear_styles_requested = pyqtSignal(int, int)
    ruby_started = pyqtSignal(int, int, str)
    ruby_apply_requested = pyqtSignal(int, int, str)
    ruby_finished = pyqtSignal(int, int, str)
    ruby_changed = pyqtSignal(int, int, str)

    def __init__(
        self,
        segment: StyledTextSegment,
        config_service,
        i18n_func,
        *,
        forced_keys: Iterable[str] = (),
        ruby_draft_text: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.segment = segment
        self.config_service = config_service
        self.i18n_func = i18n_func
        self.keys = style_keys_for_segment(segment, forced_keys)
        self.controls: dict[str, QWidget] = {}
        self.name_labels: dict[str, CaptionLabel] = {}
        self.setObjectName("richTextRunCard")
        self.refresh_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        header_row = QWidget(self)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(5)

        self.header = PushButton(segment.text, header_row)
        self.header.setObjectName("runHeader")
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.clicked.connect(lambda: self.activated.emit(segment.start, segment.end))
        save_preset = ToolButton(FIF.ADD, header_row)
        save_preset.setObjectName("savePreset")
        save_preset.setFixedSize(28, 30)
        set_hover_hint(save_preset, self.i18n_func("Save Style"))
        save_preset.setAccessibleName(self.i18n_func("Save Style"))
        clear_styles = ToolButton(FIF.REMOVE, header_row)
        clear_styles.setObjectName("clearStyles")
        clear_styles.setFixedSize(28, 30)
        set_hover_hint(clear_styles, self.i18n_func("Clear all styles from this text"))
        clear_styles.setAccessibleName(self.i18n_func("Clear all styles from this text"))
        header_layout.addWidget(self.header, 1)
        header_layout.addWidget(save_preset)
        header_layout.addWidget(clear_styles)
        layout.addWidget(header_row)

        target_start, target_end = self._target_range()
        preset_payload = {
            "style": copy.deepcopy(segment.style or {}),
            "ruby": str(segment.ruby_text or "") if segment.node_type == "ruby" else "",
            "tcy": segment.node_type == "tcy",
        }
        save_preset.clicked.connect(
            lambda _checked=False, a=target_start, b=target_end, payload=preset_payload:
            self.save_preset_requested.emit(a, b, copy.deepcopy(payload))
        )
        clear_styles.clicked.connect(
            lambda _checked=False, a=target_start, b=target_end:
            self.clear_styles_requested.emit(a, b)
        )

        for key in self.keys:
            layout.addWidget(self._create_property_row(key, ruby_draft_text))

    def _target_range(self) -> tuple[int, int]:
        if self.segment.node_type in {"ruby", "tcy"} and self.segment.node_start is not None:
            return self.segment.node_start, self.segment.node_end or self.segment.end
        return self.segment.start, self.segment.end

    def _target_for_key(self, key: str) -> tuple[int, int]:
        if key in {"R", "T"} and self.segment.node_start is not None:
            return self.segment.node_start, self.segment.node_end or self.segment.end
        return self.segment.start, self.segment.end

    def _create_property_row(self, key: str, ruby_draft_text: str | None) -> QWidget:
        row = QWidget(self)
        row.setObjectName("richTextPropertyRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(5)
        key_label = CaptionLabel(key)
        key_label.setObjectName("styleKey")
        key_label.setFixedWidth(30 if len(key) <= 2 else 38)
        key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label = CaptionLabel(self.i18n_func(STYLE_NAME_KEYS[key]), row)
        name_label.setObjectName("styleName")
        name_label.setMinimumWidth(84)
        name_label.setMaximumWidth(116)
        name_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        control = self._create_control(key, ruby_draft_text)
        remove = TransparentToolButton(FIF.CLOSE, row)
        remove.setObjectName("removeStyle")
        remove.setFixedSize(24, 26)
        start, end = self._target_for_key(key)
        remove.clicked.connect(
            lambda _checked=False, a=start, b=end, style_key=key:
            self.remove_requested.emit(a, b, style_key)
        )
        hint = self.i18n_func(STYLE_HINT_KEYS[key])
        set_hover_hint(key_label, hint)
        set_hover_hint(name_label, hint)
        set_hover_hint(remove, self.i18n_func("Remove this style"))
        remove.setAccessibleName(self.i18n_func("Remove this style"))
        layout.addWidget(key_label)
        layout.addWidget(name_label)
        if control is None:
            layout.addStretch(1)
        else:
            layout.addWidget(control, 1)
            self.controls[key] = control
        layout.addWidget(remove)
        self.name_labels[key] = name_label
        return row

    def _create_control(self, key: str, ruby_draft_text: str | None) -> QWidget | None:
        style = self.segment.style or {}
        transform = style.get("transform") or {}
        if key in {"B", "D", "T", "M", "MV"}:
            return None
        if key == "I":
            value = style.get("italic", 15.0)
            control = _double_spin_box(15.0 if isinstance(value, bool) else value, -85.0, 85.0, 1)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"italic": float(value)}))
            return control
        if key == "C":
            control = self._color_picker(
                "Select rich text color",
                style.get("color", "#E53935"),
                "saved_colors",
            )
            control.color_changed.connect(lambda value: self._emit_patch(key, {"color": value}))
            return control
        if key == "S":
            control = _spin_box(int(style.get("fontSize", 24)), 1, 1000)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"fontSize": int(value)}))
            return control
        if key == "%":
            control = _double_spin_box(style.get("scale", 1.2), 0.1, 10.0)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"scale": float(value)}))
            return control
        if key == "F":
            control = QFontComboBox(self)
            control.setCurrentFont(QFont(str(style.get("fontFamily") or "")))
            control.currentIndexChanged.connect(
                lambda _index: self._emit_patch(key, {"fontFamily": control.currentFont().family()})
            )
            return control
        if key in {"O", "G", "OS"}:
            source = {"O": "stroke", "G": "glow", "OS": "outerStroke"}[key]
            values = style.get(source) or {}
            color_default = {"O": "#ffffff", "G": "#00ffff", "OS": "#000000"}[key]
            number_key = "blur" if key == "G" else "width"
            number_default = {"O": 0.07, "G": 0.10, "OS": 0.20}[key]
            color_title = {
                "O": "Select stroke color",
                "G": "Select glow color",
                "OS": "Select outer stroke color",
            }[key]
            color = self._color_picker(
                color_title,
                values.get("color", color_default),
                f"saved_{source}_colors",
            )
            number = _double_spin_box(values.get(number_key, number_default), 0.0, 5.0)
            color.color_changed.connect(
                lambda value, field=source: self._emit_patch(key, {field: {"color": value}})
            )
            number.valueChanged.connect(
                lambda value, field=source, part=number_key:
                self._emit_patch(key, {field: {part: float(value)}})
            )
            return self._pair(
                color,
                number,
                self.i18n_func("Color"),
                self.i18n_func("Blur" if key == "G" else "Width"),
            )
        if key == "R":
            text = self.segment.ruby_text if ruby_draft_text is None else ruby_draft_text
            control = RubyEditBar(text, self, self.i18n_func)
            start, end = self._target_for_key(key)
            control.editing_started.connect(lambda: self.ruby_started.emit(start, end, control.text()))
            control.apply_requested.connect(lambda value: self.ruby_apply_requested.emit(start, end, value))
            control.editing_finished.connect(lambda value: self.ruby_finished.emit(start, end, value))
            control.text_changed.connect(lambda value: self.ruby_changed.emit(start, end, value))
            return control
        if key == "Rot":
            control = _double_spin_box(transform.get("rotation", 0.0), -180.0, 180.0, 1)
            control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"rotation": float(value)}})
            )
            return control
        if key in {"K", "PK", "LK", "NK"}:
            field = {"K": "kerning", "PK": "preKerning", "LK": "lineKerning", "NK": "nextKerning"}[key]
            control = _double_spin_box(style.get(field, 0.0), -5.0, 5.0)
            control.valueChanged.connect(
                lambda value, name=field: self._emit_patch(key, {name: float(value)})
            )
            return control
        if key == "XY":
            x_control = _double_spin_box(transform.get("offsetX", 0.0), -500.0, 500.0, 1)
            y_control = _double_spin_box(transform.get("offsetY", 0.0), -500.0, 500.0, 1)
            x_control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"offsetX": float(value)}})
            )
            y_control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"offsetY": float(value)}})
            )
            return self._pair(x_control, y_control, "X", "Y")
        return None

    def _color_picker(self, title: str, default: str, config_key: str) -> ColorPickerWidget:
        return ColorPickerWidget(
            dialog_title=title,
            default_color=str(default),
            config_key=config_key,
            config_service=self.config_service,
            i18n_func=self.i18n_func,
            parent=self,
        )

    def _pair(self, left: QWidget, right: QWidget, left_label: str, right_label: str) -> QWidget:
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(CaptionLabel(left_label))
        layout.addWidget(left, 1)
        layout.addWidget(CaptionLabel(right_label))
        layout.addWidget(right, 1)
        return widget

    def _emit_patch(self, key: str, patch: dict) -> None:
        self.patch_requested.emit(self.segment.start, self.segment.end, key, patch)

    def refresh_theme(self) -> None:
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()
        dark = isDarkTheme()
        card_background = "rgba(39,39,39,248)" if dark else "rgba(250,250,250,248)"
        row_background = "rgba(255,255,255,10)" if dark else "rgba(255,255,255,235)"
        border = "rgba(255,255,255,24)" if dark else "rgba(0,0,0,24)"
        key_color = "rgba(255,255,255,230)" if dark else "rgba(0,0,0,220)"
        name_color = "rgba(255,255,255,158)" if dark else "rgba(0,0,0,150)"
        self.setStyleSheet(f"""
            QWidget#richTextRunCard {{
                background: {card_background}; border: 1px solid {border}; border-radius: 9px;
            }}
            QWidget#richTextPropertyRow {{
                background: {row_background}; border: 1px solid {border}; border-radius: 5px;
            }}
            CaptionLabel#styleKey {{ color: {key_color}; font-weight: 700; border: none; }}
            CaptionLabel#styleName {{ color: {name_color}; border: none; }}
        """)


class StyledRunList(QScrollArea):
    range_selected = pyqtSignal(int, int)
    patch_requested = pyqtSignal(int, int, str, object)
    remove_requested = pyqtSignal(int, int, str)
    save_preset_requested = pyqtSignal(int, int, object)
    clear_styles_requested = pyqtSignal(int, int)
    ruby_started = pyqtSignal(int, int, str)
    ruby_apply_requested = pyqtSignal(int, int, str)
    ruby_finished = pyqtSignal(int, int, str)
    ruby_changed = pyqtSignal(int, int, str)

    MIN_VISIBLE_HEIGHT = 52
    MAX_VISIBLE_HEIGHT = 360

    def __init__(self, config_service, i18n_func, parent=None):
        super().__init__(parent)
        self.config_service = config_service
        self.i18n_func = i18n_func
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.setWidget(self.content)
        self.run_cards: list[StyleRunCard] = []
        self._content_height = 0
        self.hide()

    def content_height(self) -> int:
        return self._content_height

    def set_segments(
        self,
        segments: Iterable[StyledTextSegment],
        *,
        ruby_draft: tuple[int, int, str, str] | None = None,
        pending_styles: tuple[int, int, str, Iterable[str]] | None = None,
    ) -> None:
        values = list(segments)
        forced_by_range: dict[tuple[int, int], set[str]] = {}
        draft_text_by_range: dict[tuple[int, int], str] = {}

        def ensure_forced_target(start: int, end: int, text: str, keys: Iterable[str]) -> tuple[int, int]:
            target = (int(start), int(end))
            matching = next(
                (
                    segment for segment in values
                    if (segment.node_start, segment.node_end) == target
                    or (segment.start, segment.end) == target
                ),
                None,
            )
            if matching is None:
                values[:] = [
                    segment for segment in values
                    if segment.end <= target[0] or segment.start >= target[1]
                ]
                values.append(StyledTextSegment(target[0], target[1], text, {}))
                values.sort(key=lambda segment: segment.start)
            forced_by_range.setdefault(target, set()).update(keys)
            return target

        if pending_styles is not None:
            start, end, base_text, keys = pending_styles
            ensure_forced_target(start, end, base_text, keys)
        if ruby_draft is not None:
            start, end, base_text, ruby_text = ruby_draft
            target = ensure_forced_target(start, end, base_text, {"R"})
            draft_text_by_range[target] = ruby_text

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.run_cards = []
        forced_assigned: set[tuple[int, int]] = set()
        for segment in values:
            node_target = (segment.node_start, segment.node_end)
            own_target = (segment.start, segment.end)
            matched_target = node_target if node_target in forced_by_range else own_target
            forced = set()
            draft_text = None
            if matched_target in forced_by_range and matched_target not in forced_assigned:
                forced = forced_by_range[matched_target]
                draft_text = draft_text_by_range.get(matched_target)
                forced_assigned.add(matched_target)
            card = StyleRunCard(
                segment,
                self.config_service,
                self.i18n_func,
                forced_keys=forced,
                ruby_draft_text=draft_text,
                parent=self.content,
            )
            card.activated.connect(self.range_selected)
            card.patch_requested.connect(self.patch_requested)
            card.remove_requested.connect(self.remove_requested)
            card.save_preset_requested.connect(self.save_preset_requested)
            card.clear_styles_requested.connect(self.clear_styles_requested)
            card.ruby_started.connect(self.ruby_started)
            card.ruby_apply_requested.connect(self.ruby_apply_requested)
            card.ruby_finished.connect(self.ruby_finished)
            card.ruby_changed.connect(self.ruby_changed)
            self.content_layout.addWidget(card)
            self.run_cards.append(card)
        self.setVisible(bool(values))
        self.recalculate_height()

    def recalculate_height(self) -> int:
        """Use the current layout's real hint as the only run-list height source."""
        self.content_layout.invalidate()
        self.content_layout.activate()
        self.content.adjustSize()
        if not self.run_cards:
            target = 0
        else:
            content_hint = int(self.content_layout.sizeHint().height())
            target = min(self.MAX_VISIBLE_HEIGHT, max(self.MIN_VISIBLE_HEIGHT, content_hint))
        if target != self._content_height:
            self._content_height = target
            self.setFixedHeight(target)
            self.updateGeometry()
        return target

    def focus_ruby(self, start: int, end: int) -> None:
        target = (int(start), int(end))
        for card in self.run_cards:
            if "R" not in card.controls:
                continue
            if card._target_for_key("R") != target:
                continue
            control = card.controls["R"]
            if isinstance(control, RubyEditBar):
                control.focus_input()
            return

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()
