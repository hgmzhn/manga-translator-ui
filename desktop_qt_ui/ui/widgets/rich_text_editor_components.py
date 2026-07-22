"""Composable widgets for the floating rich-text editor."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
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
from qfluentwidgets import CaptionLabel, CompactDoubleSpinBox, CompactSpinBox, TextEdit

from editor.rich_text_editing import StyledTextSegment

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint


STYLE_KEYS = (
    "B", "I", "C", "S", "%", "F", "O", "G", "OS", "D", "T", "R",
    "Rot", "K", "PK", "LK", "NK", "XY", "M", "MV",
)

STYLE_HINTS = {
    "B": "加粗",
    "I": "斜体角度",
    "C": "文字颜色",
    "S": "绝对字号",
    "%": "字号倍率",
    "F": "字体文件",
    "O": "描边颜色 / 宽度",
    "G": "发光颜色 / 模糊",
    "OS": "外描边颜色 / 宽度",
    "D": "着重号",
    "T": "纵中横",
    "R": "注音文本",
    "Rot": "旋转角度",
    "K": "字后间距倍率",
    "PK": "字前间距倍率",
    "LK": "前行距倍率",
    "NK": "后行距倍率",
    "XY": "X / Y 偏移",
    "M": "水平镜像",
    "MV": "垂直镜像",
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

    BUTTONS = (
        ("B", "加粗"), ("I", "斜体"), ("C", "文字颜色"), ("S", "绝对字号"),
        ("%", "字号倍率"), ("F", "字体"), ("O", "描边"), ("G", "发光"),
        ("OS", "外描边"), ("D", "着重号"), ("T", "纵中横"), ("R", "注音"),
        ("Rot", "旋转"), ("K", "字后距"), ("PK", "字前距"), ("LK", "前行距"),
        ("NK", "后行距"), ("XY", "偏移"), ("M", "水平镜像"), ("MV", "垂直镜像"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QGridLayout

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.buttons: dict[str, QToolButton] = {}
        for index, (key, hint) in enumerate(self.BUTTONS):
            button = QToolButton(self)
            button.setText(key)
            button.setCheckable(True)
            button.setFixedSize(34, 30)
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("""
                QToolButton { color: #20242a; background: rgba(255,255,255,235);
                    border: 1px solid rgba(120,130,145,75); border-radius: 6px;
                    font-size: 13px; font-weight: 600; }
                QToolButton:hover { background: #edf3fa; border-color: #8aa9c7; }
                QToolButton:checked { color: white; background: #0078d4; border-color: #0078d4; }
            """)
            set_hover_hint(button, hint)
            button.clicked.connect(
                lambda checked=False, style_key=key: self.toggled.emit(style_key, checked)
            )
            layout.addWidget(button, index // 10, index % 10)
            self.buttons[key] = button

    def set_checked(self, key: str, checked: bool) -> None:
        button = self.buttons.get(key)
        if button is not None:
            button.setChecked(bool(checked))


class RubyEditBar(QWidget):
    apply_requested = pyqtSignal(str)
    editing_finished = pyqtSignal(str)
    editing_started = pyqtSignal()
    text_changed = pyqtSignal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("注音")
        self.input.setText(str(text or ""))
        self.input.installEventFilter(self)
        self.apply_button = QToolButton(self)
        self.apply_button.setText("应用")
        self.apply_button.setMinimumSize(48, 28)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.apply_button)
        self.apply_button.clicked.connect(lambda: self.apply_requested.emit(self.input.text()))
        self.input.returnPressed.connect(lambda: self.apply_requested.emit(self.input.text()))
        self.input.editingFinished.connect(lambda: self.editing_finished.emit(self.input.text()))
        self.input.textChanged.connect(self.text_changed)

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
    patch_requested = pyqtSignal(int, int, object)
    remove_requested = pyqtSignal(int, int, str)
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
        self.setObjectName("richTextRunCard")
        self.setStyleSheet("""
            QWidget#richTextRunCard { background: rgba(249,250,252,252);
                border: 1px solid rgba(120,130,145,80); border-radius: 9px; }
            QWidget#richTextPropertyRow { background: white;
                border: 1px solid rgba(125,135,150,60); border-radius: 5px; }
            CaptionLabel#styleKey { color: #20242a; font-weight: 700; border: none; }
            QToolButton#removeStyle { color: #b42318; background: #fff1f0;
                border: 1px solid #f0a39d; border-radius: 6px; font-weight: 700; }
            QToolButton#runHeader { color: #005fb8; background: #f0f6ff;
                border: none; border-radius: 5px; padding: 5px 8px; text-align: left;
                font-weight: 700; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        self.header = QToolButton(self)
        self.header.setObjectName("runHeader")
        self.header.setText(segment.text)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.clicked.connect(lambda: self.activated.emit(segment.start, segment.end))
        layout.addWidget(self.header)

        for key in self.keys:
            layout.addWidget(self._create_property_row(key, ruby_draft_text))

        self.estimated_height = 44 + 39 * len(self.keys)

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
        control = self._create_control(key, ruby_draft_text)
        remove = QToolButton(row)
        remove.setObjectName("removeStyle")
        remove.setText("X")
        remove.setFixedSize(24, 26)
        start, end = self._target_for_key(key)
        remove.clicked.connect(
            lambda _checked=False, a=start, b=end, style_key=key:
            self.remove_requested.emit(a, b, style_key)
        )
        set_hover_hint(key_label, STYLE_HINTS[key])
        layout.addWidget(key_label)
        layout.addWidget(control, 1)
        layout.addWidget(remove)
        self.controls[key] = control
        return row

    def _create_control(self, key: str, ruby_draft_text: str | None) -> QWidget:
        style = self.segment.style or {}
        transform = style.get("transform") or {}
        if key in {"B", "D", "T", "M", "MV"}:
            return CaptionLabel(STYLE_HINTS[key])
        if key == "I":
            value = style.get("italic", 15.0)
            control = _double_spin_box(15.0 if isinstance(value, bool) else value, -85.0, 85.0, 1)
            control.valueChanged.connect(lambda value: self._emit_patch({"italic": float(value)}))
            return control
        if key == "C":
            control = self._color_picker("Select rich text color", style.get("color", "#E53935"), "saved_colors")
            control.color_changed.connect(lambda value: self._emit_patch({"color": value}))
            return control
        if key == "S":
            control = _spin_box(int(style.get("fontSize", 24)), 1, 1000)
            control.valueChanged.connect(lambda value: self._emit_patch({"fontSize": int(value)}))
            return control
        if key == "%":
            control = _double_spin_box(style.get("scale", 1.2), 0.1, 10.0)
            control.valueChanged.connect(lambda value: self._emit_patch({"scale": float(value)}))
            return control
        if key == "F":
            control = QFontComboBox(self)
            control.setCurrentFont(QFont(str(style.get("fontFamily") or "")))
            control.currentIndexChanged.connect(
                lambda _index: self._emit_patch({"fontFamily": control.currentFont().family()})
            )
            return control
        if key in {"O", "G", "OS"}:
            source = {"O": "stroke", "G": "glow", "OS": "outerStroke"}[key]
            values = style.get(source) or {}
            color_default = {"O": "#ffffff", "G": "#00ffff", "OS": "#000000"}[key]
            number_key = "blur" if key == "G" else "width"
            number_default = {"O": 0.07, "G": 0.10, "OS": 0.20}[key]
            color = self._color_picker(f"Select {source} color", values.get("color", color_default), f"saved_{source}_colors")
            number = _double_spin_box(values.get(number_key, number_default), 0.0, 5.0)
            color.color_changed.connect(lambda value, field=source: self._emit_patch({field: {"color": value}}))
            number.valueChanged.connect(
                lambda value, field=source, part=number_key:
                self._emit_patch({field: {part: float(value)}})
            )
            return self._pair(color, number, "颜色", "模糊" if key == "G" else "宽度")
        if key == "R":
            text = self.segment.ruby_text if ruby_draft_text is None else ruby_draft_text
            control = RubyEditBar(text, self)
            start, end = self._target_for_key(key)
            control.editing_started.connect(lambda: self.ruby_started.emit(start, end, control.text()))
            control.apply_requested.connect(lambda value: self.ruby_apply_requested.emit(start, end, value))
            control.editing_finished.connect(lambda value: self.ruby_finished.emit(start, end, value))
            control.text_changed.connect(lambda value: self.ruby_changed.emit(start, end, value))
            return control
        if key == "Rot":
            control = _double_spin_box(transform.get("rotation", 0.0), -180.0, 180.0, 1)
            control.valueChanged.connect(lambda value: self._emit_patch({"transform": {"rotation": float(value)}}))
            return control
        if key in {"K", "PK", "LK", "NK"}:
            field = {"K": "kerning", "PK": "preKerning", "LK": "lineKerning", "NK": "nextKerning"}[key]
            control = _double_spin_box(style.get(field, 0.0), -5.0, 5.0)
            control.valueChanged.connect(lambda value, name=field: self._emit_patch({name: float(value)}))
            return control
        if key == "XY":
            x_control = _double_spin_box(transform.get("offsetX", 0.0), -500.0, 500.0, 1)
            y_control = _double_spin_box(transform.get("offsetY", 0.0), -500.0, 500.0, 1)
            x_control.valueChanged.connect(lambda value: self._emit_patch({"transform": {"offsetX": float(value)}}))
            y_control.valueChanged.connect(lambda value: self._emit_patch({"transform": {"offsetY": float(value)}}))
            return self._pair(x_control, y_control, "X", "Y")
        return QWidget(self)

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

    def _emit_patch(self, patch: dict) -> None:
        self.patch_requested.emit(self.segment.start, self.segment.end, patch)


class StyledRunList(QScrollArea):
    range_selected = pyqtSignal(int, int)
    patch_requested = pyqtSignal(int, int, object)
    remove_requested = pyqtSignal(int, int, str)
    ruby_started = pyqtSignal(int, int, str)
    ruby_apply_requested = pyqtSignal(int, int, str)
    ruby_finished = pyqtSignal(int, int, str)
    ruby_changed = pyqtSignal(int, int, str)

    def __init__(self, config_service, i18n_func, parent=None):
        super().__init__(parent)
        self.config_service = config_service
        self.i18n_func = i18n_func
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMaximumHeight(360)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.setWidget(self.content)
        self.run_cards: list[StyleRunCard] = []
        self.hide()

    def set_segments(
        self,
        segments: Iterable[StyledTextSegment],
        *,
        ruby_draft: tuple[int, int, str, str] | None = None,
    ) -> None:
        values = list(segments)
        forced_by_range: dict[tuple[int, int], set[str]] = {}
        draft_text_by_range: dict[tuple[int, int], str] = {}
        if ruby_draft is not None:
            start, end, base_text, ruby_text = ruby_draft
            target = (start, end)
            matching = next(
                (
                    segment for segment in values
                    if (segment.node_start, segment.node_end) == target
                    or (segment.start, segment.end) == target
                ),
                None,
            )
            if matching is None:
                values = [segment for segment in values if segment.end <= start or segment.start >= end]
                values.append(StyledTextSegment(start, end, base_text, {}))
                values.sort(key=lambda segment: segment.start)
            forced_by_range[target] = {"R"}
            draft_text_by_range[target] = ruby_text

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.run_cards = []
        estimated_height = 0
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
            card.ruby_started.connect(self.ruby_started)
            card.ruby_apply_requested.connect(self.ruby_apply_requested)
            card.ruby_finished.connect(self.ruby_finished)
            card.ruby_changed.connect(self.ruby_changed)
            self.content_layout.addWidget(card)
            self.run_cards.append(card)
            estimated_height += card.estimated_height + 6
        self.setVisible(bool(values))
        if values:
            self.setFixedHeight(min(360, max(52, estimated_height)))

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
