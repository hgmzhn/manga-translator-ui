"""Composable widgets for the floating rich-text editor.

Every control here is backed by qfluentwidgets, so theming stays with the
library instead of hand-written style sheets.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CompactDoubleSpinBox,
    CompactSpinBox,
    FluentIcon as FIF,
    LineEdit,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    TextEdit,
    TogglePushButton,
    ToolButton,
    TransparentToolButton,
    isDarkTheme,
)

from editor.rich_text_editing import StyledTextSegment
from services import get_config_service, get_i18n_manager
from utils.font_list import FontComboBox

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint
from .widget_cleanup import clear_layout
from .wheel_filter import TopLevelComboBox as ComboBox


def _tr(key: str, **kwargs) -> str:
    """Translate through the app-wide i18n singleton (identity before init)."""
    i18n = get_i18n_manager()
    return i18n.translate(key, **kwargs) if i18n else key


def _current_locale() -> str:
    i18n = get_i18n_manager()
    return i18n.get_current_locale() if i18n else "en_US"


@dataclass(frozen=True)
class StyleSpec:
    """Single source of truth for one style key."""

    name: str            # property-row label (i18n key)
    hint: str            # toolbar / tooltip text (i18n key)
    default_patch: dict  # style patch applied when the toolbar button is checked


# ``F`` resolves its default family lazily (QFont queries the runtime font
# database); ``T``/``R`` are node operations rather than style patches.
STYLE_SPECS: dict[str, StyleSpec] = {
    "B": StyleSpec("Bold", "Bold", {"bold": True}),
    "I": StyleSpec("Italic", "Italic Angle", {"italic": 15.0}),
    "U": StyleSpec("Underline", "Underline", {"underline": True}),
    "C": StyleSpec("Text Color", "Text Color", {"color": "#E53935"}),
    "S": StyleSpec("Font Size", "Font Size", {"fontSize": 24}),
    "%": StyleSpec("Scale", "Scale", {"scale": 1.20}),
    "F": StyleSpec("Font", "Font Family", {}),
    "O": StyleSpec("Stroke", "Stroke", {"stroke": {"color": "#ffffff", "width": 0.07}}),
    "G": StyleSpec("Glow", "Glow", {"glow": {"color": "#00ffff", "blur": 0.10}}),
    "OS": StyleSpec("Outer Stroke", "Outer Stroke", {"outerStroke": {"color": "#000000", "width": 0.20}}),
    "D": StyleSpec("Emphasis", "Emphasis", {"emphasis": True}),
    "FA": StyleSpec("Force Advance", "Force Advance", {"verticalAdvance": "half"}),
    "T": StyleSpec("TCY", "Vertical-in-Horizontal (TCY)", {}),
    "R": StyleSpec("Ruby", "Ruby Text", {}),
    "Rot": StyleSpec("Rotation", "Rotation", {"transform": {"rotation": 90.0}}),
    "K": StyleSpec("Kerning", "Kerning", {"kerning": 0.0}),
    "PK": StyleSpec("Pre Kerning", "Pre Kerning", {"preKerning": 0.0}),
    "LK": StyleSpec("Line Kerning", "Line Kerning", {"lineKerning": 0.0}),
    "NK": StyleSpec("Next Kerning", "Next Kerning", {"nextKerning": 0.0}),
    "XY": StyleSpec("Offset", "X / Y Offset", {"transform": {"offsetX": 0.0, "offsetY": 0.0}}),
    "M": StyleSpec("Mirror Horizontal", "Mirror Horizontal", {"transform": {"mirrorX": True}}),
    "MV": StyleSpec("Mirror Vertical", "Mirror Vertical", {"transform": {"mirrorY": True}}),
}

STYLE_KEYS = tuple(STYLE_SPECS)

# key -> (style field, color dialog title, default color, number field, number default)
_EFFECT_SPECS = {
    "O": ("stroke", "Select stroke color", "#ffffff", "width", 0.07),
    "G": ("glow", "Select glow color", "#00ffff", "blur", 0.10),
    "OS": ("outerStroke", "Select outer stroke color", "#000000", "width", 0.20),
}


def default_style_patch(key: str) -> dict:
    if key == "F":
        return {"fontFamily": QFont().family()}
    spec = STYLE_SPECS.get(key)
    return copy.deepcopy(spec.default_patch) if spec else {}


def clear_style_patch(key: str) -> dict:
    """Derive the removing patch from the default patch structure.

    Top-level style fields clear as ``field: None``; ``transform`` clears its
    individual sub-keys so sibling transform values survive.
    """
    return {
        field: ({sub: None for sub in value} if field == "transform" else None)
        for field, value in default_style_patch(key).items()
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
    # 步进跟随精度：两位小数的量（缩放/字距/描边宽度）按 0.05 微调，
    # 一位小数的量（角度/像素偏移)按 1 步进。
    control.setSingleStep(0.05 if decimals >= 2 else 1.0)
    control.setAccelerated(True)
    control.setValue(float(value))
    return control


def _spin_box(value: int, minimum: int, maximum: int) -> CompactSpinBox:
    control = CompactSpinBox()
    control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    control.setRange(minimum, maximum)
    control.setAccelerated(True)
    control.setValue(int(value))
    return control


class RichTextToolbar(QWidget):
    """Grid of Fluent toggle buttons, one per style key."""

    toggled = pyqtSignal(str, bool)

    _COLUMNS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)
        self.buttons: dict[str, TogglePushButton] = {}
        for index, key in enumerate(STYLE_KEYS):
            button = TogglePushButton(key, self)
            button.setFixedSize(48, 30)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, style_key=key: self.toggled.emit(style_key, checked)
            )
            layout.addWidget(button, index // self._COLUMNS, index % self._COLUMNS)
            self.buttons[key] = button
        self.refresh_ui_texts()

    def refresh_ui_texts(self) -> None:
        for key, button in self.buttons.items():
            text = _tr(STYLE_SPECS[key].hint)
            set_hover_hint(button, text)
            button.setAccessibleName(text)

    def set_checked(self, key: str, checked: bool) -> None:
        button = self.buttons.get(key)
        if button is not None:
            button.setChecked(bool(checked))


class RichTextPresetSidebar(SimpleCardWidget):
    """Collapsible saved-preset list; the floating editor owns the payloads."""

    preset_applied = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    collapsed_changed = pyqtSignal(bool)

    EXPANDED_WIDTH = 248
    COLLAPSED_WIDTH = 38

    def _normalBackgroundColor(self) -> QColor:
        """Use an opaque Fluent surface because the panel owns its background."""
        return QColor(32, 32, 32) if isDarkTheme() else QColor(250, 250, 250)

    def __init__(self, parent=None):
        super().__init__(parent)
        # This is a panel inside the floating card, not another rounded card.
        # Keeping it Fluent-backed makes qconfig.themeChanged propagate to it.
        self.setBorderRadius(0)
        self._names: list[str] = []
        self._collapsed = False
        self.setFixedWidth(self.EXPANDED_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.title = CaptionLabel(_tr("Rich Text Presets"), self)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle_button = TransparentToolButton(FIF.CARE_LEFT_SOLID, self)
        self.toggle_button.setFixedSize(26, 28)
        self.toggle_button.clicked.connect(self._toggle_collapsed)
        header.addWidget(self.title, 1)
        header.addWidget(self.toggle_button)
        root.addLayout(header)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.enableTransparentBackground()
        self.content = QWidget(self.scroll)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(5)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        self._rebuild_rows()
        self._refresh_toggle_state(emit=False)

    def set_presets(self, names: Iterable[str] | None) -> None:
        self._names = [str(name) for name in (names or ()) if str(name).strip()]
        self._rebuild_rows()

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
        self.title.setText(_tr("Rich Text Presets"))
        self._rebuild_rows()
        self._refresh_toggle_state(emit=False)

    def refresh_theme(self) -> None:
        """Bridge the native scroll viewport to the Fluent panel surface."""
        surface = self._normalBackgroundColor()
        palette = self.palette()
        roles = (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Button,
            QPalette.ColorRole.Mid,
            QPalette.ColorRole.Dark,
            QPalette.ColorRole.Midlight,
        )
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            for role in roles:
                palette.setColor(group, role, surface)
        for widget in (self, self.scroll, self.scroll.viewport(), self.content):
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)
            widget.update()

    def _rebuild_rows(self) -> None:
        clear_layout(self.content_layout)
        if not self._names:
            empty = CaptionLabel(_tr("No saved styles"), self.content)
            empty.setWordWrap(True)
            self.content_layout.addWidget(empty)
        else:
            for name in self._names:
                self.content_layout.addWidget(self._create_preset_row(name))
        self.content_layout.addStretch(1)

    def _create_preset_row(self, name: str) -> QWidget:
        row = QWidget(self.content)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        apply_button = PushButton(name, row)
        apply_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_hover_hint(apply_button, _tr("Choose a saved style to apply"))
        apply_button.setAccessibleName(_tr("Choose a saved style to apply"))
        apply_button.clicked.connect(lambda _checked=False, preset_name=name: self.preset_applied.emit(preset_name))

        rename_button = TransparentToolButton(FIF.EDIT, row)
        rename_button.setFixedSize(24, 28)
        set_hover_hint(rename_button, _tr("Rename preset"))
        rename_button.setAccessibleName(_tr("Rename preset"))
        rename_button.clicked.connect(lambda _checked=False, preset_name=name: self.rename_requested.emit(preset_name))

        delete_button = TransparentToolButton(FIF.DELETE, row)
        delete_button.setFixedSize(24, 28)
        set_hover_hint(delete_button, _tr("Delete preset"))
        delete_button.setAccessibleName(_tr("Delete preset"))
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
        hint = _tr("Expand preset sidebar") if self._collapsed else _tr("Collapse preset sidebar")
        set_hover_hint(self.toggle_button, hint)
        self.toggle_button.setAccessibleName(hint)
        if emit:
            self.collapsed_changed.emit(self._collapsed)


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
        self.input = LineEdit(self)
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
        self.input.setPlaceholderText(_tr("Ruby text"))
        self.apply_button.setText(_tr("Apply"))
        self.apply_button.setAccessibleName(_tr("Apply"))

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
        "U": bool(style.get("underline")),
        "C": "color" in style,
        "S": "fontSize" in style,
        "%": "scale" in style,
        "F": "fontFamily" in style,
        "O": bool(style.get("stroke")),
        "G": bool(style.get("glow")),
        "OS": bool(style.get("outerStroke")),
        "D": bool(style.get("emphasis")),
        "FA": "verticalAdvance" in style,
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


# ``StyleRunCard`` signals are re-emitted 1:1 by ``StyledRunList``.
RUN_CARD_SIGNALS = (
    "range_selected",
    "patch_requested",
    "remove_requested",
    "save_preset_requested",
    "clear_styles_requested",
    "ruby_started",
    "ruby_apply_requested",
    "ruby_finished",
    "ruby_changed",
)


class StyleRunCard(SimpleCardWidget):
    range_selected = pyqtSignal(int, int)
    patch_requested = pyqtSignal(int, int, str, object)
    remove_requested = pyqtSignal(int, int, str)
    save_preset_requested = pyqtSignal(int, int)
    clear_styles_requested = pyqtSignal(int, int)
    ruby_started = pyqtSignal(int, int, str)
    ruby_apply_requested = pyqtSignal(int, int, str)
    ruby_finished = pyqtSignal(int, int, str)
    ruby_changed = pyqtSignal(int, int, str)

    # Paired controls share the compact width available after the key/name
    # columns.  Keeping this policy here makes every pair (effects and offsets)
    # behave consistently as the editor width changes.
    _PAIR_CONTROL_MIN_WIDTH = 88
    _PAIR_CONTROL_MAX_WIDTH = 132

    def refresh_theme(self) -> None:
        """Refresh the card surface after a runtime Fluent theme change."""
        self.backgroundColorAni.stop()
        self.setBackgroundColor(self._normalBackgroundColor())
        self.update()

    def __init__(
        self,
        segment: StyledTextSegment,
        *,
        forced_keys: Iterable[str] = (),
        ruby_draft_text: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.segment = segment
        self.keys = style_keys_for_segment(segment, forced_keys)
        self.controls: dict[str, QWidget] = {}
        self.name_labels: dict[str, CaptionLabel] = {}
        # key -> [applier(style, transform, segment, draft)]：结构不变时就地刷新控件值
        self._value_appliers: dict[str, list] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        header_row = QWidget(self)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(5)

        self.header = PushButton(segment.text, header_row)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.clicked.connect(lambda: self.range_selected.emit(segment.start, segment.end))
        save_preset = ToolButton(FIF.ADD, header_row)
        save_preset.setFixedSize(28, 30)
        set_hover_hint(save_preset, _tr("Save Style"))
        save_preset.setAccessibleName(_tr("Save Style"))
        clear_styles = ToolButton(FIF.REMOVE, header_row)
        clear_styles.setFixedSize(28, 30)
        set_hover_hint(clear_styles, _tr("Clear all styles from this text"))
        clear_styles.setAccessibleName(_tr("Clear all styles from this text"))
        header_layout.addWidget(self.header, 1)
        header_layout.addWidget(save_preset)
        header_layout.addWidget(clear_styles)
        layout.addWidget(header_row)

        # The floating editor reads the live document when these fire, so the
        # card never snapshots its style payload (it would go stale after
        # in-place spin-box edits).
        target_start, target_end = self._target_range()
        save_preset.clicked.connect(
            lambda _checked=False, a=target_start, b=target_end:
            self.save_preset_requested.emit(a, b)
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

    # ------------------------------------------------------------------
    # 结构签名相同时的就地刷新（避免整卡重建打断用户输入/点击）
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_control_value(control: QWidget, setter, value) -> None:
        """把新值写进控件；持焦点的控件不覆盖（值本来就来自它）。"""
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and (
            focus_widget is control or control.isAncestorOf(focus_widget)
        ):
            return
        control.blockSignals(True)
        try:
            setter(value)
        finally:
            control.blockSignals(False)

    def _register_applier(self, key: str, control: QWidget, getter, setter) -> None:
        def applier(style, transform, segment, draft, _control=control, _getter=getter, _setter=setter):
            self._apply_control_value(_control, _setter, _getter(style, transform, segment, draft))

        self._value_appliers.setdefault(key, []).append(applier)

    def update_values(self, segment: StyledTextSegment, ruby_draft_text: str | None = None) -> None:
        """结构签名相同时就地刷新：把新的样式值写进现有控件，不重建行。

        由 StyledRunList.set_segments 在签名比对通过后调用；start/end、
        node 边界、key 集合都包含在签名里，所以构造时捕获的目标范围
        （header/删除按钮等连接）在复用期间保持有效。
        """
        self.segment = segment
        style = segment.style or {}
        transform = style.get("transform") or {}
        for appliers in self._value_appliers.values():
            for applier in appliers:
                applier(style, transform, segment, ruby_draft_text)

    def _target_for_key(self, key: str) -> tuple[int, int]:
        if key in {"R", "T"} and self.segment.node_start is not None:
            return self.segment.node_start, self.segment.node_end or self.segment.end
        return self.segment.start, self.segment.end

    def _create_property_row(self, key: str, ruby_draft_text: str | None) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        key_label = CaptionLabel(key)
        key_font = key_label.font()
        key_font.setBold(True)
        key_label.setFont(key_font)
        key_label.setFixedWidth(30 if len(key) <= 2 else 38)
        key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label = CaptionLabel(_tr(STYLE_SPECS[key].name), row)
        name_label.setTextColor(QColor(0, 0, 0, 150), QColor(255, 255, 255, 158))
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
        hint = _tr(STYLE_SPECS[key].hint)
        set_hover_hint(key_label, hint)
        set_hover_hint(name_label, hint)
        set_hover_hint(remove, _tr("Remove this style"))
        remove.setAccessibleName(_tr("Remove this style"))
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
        if key in {"B", "U", "D", "T", "M", "MV"}:
            return None
        if key == "I":
            value = style.get("italic", 15.0)
            control = _double_spin_box(15.0 if isinstance(value, bool) else value, -85.0, 85.0, 1)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"italic": float(value)}))
            self._register_applier(
                key, control,
                lambda s, *_: 15.0 if isinstance(s.get("italic", 15.0), bool) else float(s.get("italic", 15.0)),
                control.setValue,
            )
            return control
        if key == "C":
            control = self._color_picker(
                "Select rich text color",
                style.get("color", "#E53935"),
                "saved_colors",
            )
            control.color_changed.connect(lambda value: self._emit_patch(key, {"color": value}))
            self._register_applier(
                key, control,
                lambda s, *_: str(s.get("color", "#E53935")),
                control.set_color,
            )
            return control
        if key == "S":
            control = _spin_box(int(style.get("fontSize", 24)), 1, 1000)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"fontSize": int(value)}))
            self._register_applier(
                key, control,
                lambda s, *_: int(s.get("fontSize", 24)),
                control.setValue,
            )
            return control
        if key == "%":
            control = _double_spin_box(style.get("scale", 1.2), 0.1, 10.0)
            control.valueChanged.connect(lambda value: self._emit_patch(key, {"scale": float(value)}))
            self._register_applier(
                key, control,
                lambda s, *_: float(s.get("scale", 1.2)),
                control.setValue,
            )
            return control
        if key == "FA":
            control = ComboBox(self)
            control.addItem(_tr("Half Advance"), userData="half")
            control.addItem(_tr("Full Advance"), userData="full")
            control.setCurrentIndex(max(0, control.findData(style.get("verticalAdvance", "half"))))
            control.currentIndexChanged.connect(
                lambda _index: self._emit_patch(
                    key,
                    {"verticalAdvance": str(control.currentData() or "half")},
                )
            )
            self._register_applier(
                key,
                control,
                lambda s, *_: str(s.get("verticalAdvance", "half")),
                lambda value, _control=control: _control.setCurrentIndex(
                    max(0, _control.findData(value))
                ),
            )
            return control
        if key == "F":
            control = FontComboBox(self, locale_getter=_current_locale)
            control.setCurrentFamily(str(style.get("fontFamily") or ""))
            control.currentIndexChanged.connect(
                lambda _index: self._emit_patch(key, {"fontFamily": control.currentFamily()})
            )
            self._register_applier(
                key, control,
                lambda s, *_: str(s.get("fontFamily") or ""),
                lambda value, _control=control: _control.setCurrentFamily(value),
            )
            return control
        if key in _EFFECT_SPECS:
            source, color_title, color_default, number_key, number_default = _EFFECT_SPECS[key]
            values = style.get(source) or {}
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
            self._register_applier(
                key, color,
                lambda s, *_, field=source, default=color_default:
                str((s.get(field) or {}).get("color", default)),
                color.set_color,
            )
            self._register_applier(
                key, number,
                lambda s, *_, field=source, part=number_key, default=number_default:
                float((s.get(field) or {}).get(part, default)),
                number.setValue,
            )
            return self._pair(
                color,
                number,
                _tr("Color"),
                _tr("Blur" if key == "G" else "Width"),
            )
        if key == "R":
            text = self.segment.ruby_text if ruby_draft_text is None else ruby_draft_text
            control = RubyEditBar(text, self)
            start, end = self._target_for_key(key)
            control.editing_started.connect(lambda: self.ruby_started.emit(start, end, control.text()))
            control.apply_requested.connect(lambda value: self.ruby_apply_requested.emit(start, end, value))
            control.editing_finished.connect(lambda value: self.ruby_finished.emit(start, end, value))
            control.text_changed.connect(lambda value: self.ruby_changed.emit(start, end, value))
            self._register_applier(
                key, control.input,
                lambda s, t, seg, draft: str(seg.ruby_text if draft is None else draft),
                control.input.setText,
            )
            return control
        if key == "Rot":
            control = _double_spin_box(transform.get("rotation", 0.0), -180.0, 180.0, 1)
            control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"rotation": float(value)}})
            )
            self._register_applier(
                key, control,
                lambda s, t, *_: float(t.get("rotation", 0.0)),
                control.setValue,
            )
            return control
        if key in {"K", "PK", "LK", "NK"}:
            field = {"K": "kerning", "PK": "preKerning", "LK": "lineKerning", "NK": "nextKerning"}[key]
            control = _double_spin_box(style.get(field, 0.0), -5.0, 5.0)
            control.valueChanged.connect(
                lambda value, name=field: self._emit_patch(key, {name: float(value)})
            )
            self._register_applier(
                key, control,
                lambda s, *_, name=field: float(s.get(name, 0.0)),
                control.setValue,
            )
            return control
        if key == "XY":
            x_control = _double_spin_box(transform.get("offsetX", 0.0), -500.0, 500.0, 1)
            y_control = _double_spin_box(transform.get("offsetY", 0.0), -500.0, 500.0, 1)
            x_control.setSuffix("%")
            y_control.setSuffix("%")
            x_control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"offsetX": float(value)}})
            )
            y_control.valueChanged.connect(
                lambda value: self._emit_patch(key, {"transform": {"offsetY": float(value)}})
            )
            self._register_applier(
                key, x_control,
                lambda s, t, *_: float(t.get("offsetX", 0.0)),
                x_control.setValue,
            )
            self._register_applier(
                key, y_control,
                lambda s, t, *_: float(t.get("offsetY", 0.0)),
                y_control.setValue,
            )
            return self._pair(x_control, y_control, "X", "Y")
        return None

    def _color_picker(self, title: str, default: str, config_key: str) -> ColorPickerWidget:
        return ColorPickerWidget(
            dialog_title=title,
            default_color=str(default),
            config_key=config_key,
            config_service=get_config_service(),
            i18n_func=_tr,
            parent=self,
        )

    def _pair(self, left: QWidget, right: QWidget, left_label: str, right_label: str) -> QWidget:
        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for control in (left, right):
            control.setMinimumWidth(self._PAIR_CONTROL_MIN_WIDTH)
            control.setMaximumWidth(self._PAIR_CONTROL_MAX_WIDTH)

        left_caption = CaptionLabel(left_label, widget)
        right_caption = CaptionLabel(right_label, widget)
        # Keep axis labels from consuming the stretch allocated to the inputs.
        left_caption.setFixedWidth(
            max(12, left_caption.fontMetrics().horizontalAdvance(left_label) + 2)
        )
        right_caption.setFixedWidth(
            max(12, right_caption.fontMetrics().horizontalAdvance(right_label) + 2)
        )
        left_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(left_caption)
        layout.addWidget(left, 1)
        layout.addWidget(right_caption)
        layout.addWidget(right, 1)
        return widget

    def _emit_patch(self, key: str, patch: dict) -> None:
        self.patch_requested.emit(self.segment.start, self.segment.end, key, patch)


class StyledRunList(ScrollArea):
    range_selected = pyqtSignal(int, int)
    patch_requested = pyqtSignal(int, int, str, object)
    remove_requested = pyqtSignal(int, int, str)
    save_preset_requested = pyqtSignal(int, int)
    clear_styles_requested = pyqtSignal(int, int)
    ruby_started = pyqtSignal(int, int, str)
    ruby_apply_requested = pyqtSignal(int, int, str)
    ruby_finished = pyqtSignal(int, int, str)
    ruby_changed = pyqtSignal(int, int, str)

    MIN_VISIBLE_HEIGHT = 52
    MAX_VISIBLE_HEIGHT = 360

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)
        self.enableTransparentBackground()
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.setWidget(self.content)
        self.run_cards: list[StyleRunCard] = []
        self._content_height = 0
        self._cards_signature: tuple = ()
        self.hide()

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

        plans: list[tuple[StyledTextSegment, set[str], str | None]] = []
        forced_assigned: set[tuple[int, int]] = set()
        for segment in values:
            node_target = (segment.node_start, segment.node_end)
            own_target = (segment.start, segment.end)
            matched_target = node_target if node_target in forced_by_range else own_target
            forced: set[str] = set()
            draft_text = None
            if matched_target in forced_by_range and matched_target not in forced_assigned:
                forced = forced_by_range[matched_target]
                draft_text = draft_text_by_range.get(matched_target)
                forced_assigned.add(matched_target)
            plans.append((segment, forced, draft_text))

        # Card layout identity: everything except style *values* (ruby text
        # included — it is a value edited through a live input).  When only a
        # value changed (spin box, color swatch, ruby text), the existing
        # cards are refreshed in place: rebuilding them would tear the widget
        # out from under the user's cursor, break spin auto-repeat and swallow
        # the first click after a ruby ``editingFinished``.
        signature = tuple(
            (
                segment.start, segment.end, segment.text,
                segment.node_type,
                segment.node_start, segment.node_end,
                tuple(style_keys_for_segment(segment, forced)),
            )
            for segment, forced, _draft in plans
        )
        if (
            self.run_cards
            and signature == self._cards_signature
            and len(self.run_cards) == len(plans)
        ):
            for card, (segment, _forced, draft_text) in zip(self.run_cards, plans):
                card.update_values(segment, draft_text)
            self.setVisible(bool(values))
            return
        self._cards_signature = signature

        clear_layout(self.content_layout)
        self.run_cards = []
        for segment, forced, draft_text in plans:
            card = StyleRunCard(
                segment,
                forced_keys=forced,
                ruby_draft_text=draft_text,
                parent=self.content,
            )
            for signal_name in RUN_CARD_SIGNALS:
                getattr(card, signal_name).connect(getattr(self, signal_name))
            self.content_layout.addWidget(card)
            self.run_cards.append(card)
        self.setVisible(bool(values))
        self.recalculate_height()

    def refresh_theme(self) -> None:
        surface = QColor(32, 32, 32) if isDarkTheme() else QColor(250, 250, 250)
        palette = self.palette()
        roles = (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Button,
            QPalette.ColorRole.Mid,
            QPalette.ColorRole.Dark,
            QPalette.ColorRole.Midlight,
        )
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            for role in roles:
                palette.setColor(group, role, surface)
        for widget in (self, self.viewport(), self.content):
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)
            widget.update()
        for card in self.run_cards:
            card.refresh_theme()
        self.update()

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
