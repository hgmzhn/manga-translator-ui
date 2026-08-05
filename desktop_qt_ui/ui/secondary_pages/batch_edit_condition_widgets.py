"""批量管理页的条件行与动作块控件。

条件负责筛 region，动作各自带 pattern 负责在译文里定位子串 —— 两者分开，不存在
"哪条条件的命中区间才是目标"的歧义。值编辑器按字段类型现造（``build_value_editor``），
条件行和"改 region 属性"动作共用同一套，避免两处各写一遍类型分支。
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    CompactDoubleSpinBox,
    CompactSpinBox,
    FluentIcon as FIF,
    LineEdit,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
    ToolButton,
)

from services.batch_edit_engine import (
    FIELDS,
    FIELDS_BY_KEY,
    KIND_BOOL,
    KIND_COLOR,
    KIND_ENUM,
    KIND_NUMBER,
    KIND_TEXT,
    OP_LABELS,
    OPS_BY_KIND,
    VALUELESS_OPS,
    FieldSpec,
)
from services.batch_edit_schemes import (
    ACTION_REPLACE_TEXT,
    ACTION_RICH_TEXT,
    ACTION_SET_FIELDS,
    RICH_MODE_CLEAR,
    RICH_MODE_FILL,
    RICH_MODE_OVERWRITE,
)
from ui.secondary_pages.fluent_dialog import DialogCode, FluentSecondaryDialog
from ui.widgets.color_picker import ColorPickerWidget
from utils.font_list import FontComboBox


# ─── 值编辑器 ───


class _ValueEditor(QWidget):
    """统一契约：``value()`` 取值、``set_value()`` 回填、``changed`` 通知。"""

    changed = pyqtSignal()

    def value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError

    def refresh_ui_texts(self) -> None:
        return None


def _row(widget: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    return layout


class _TextValueEditor(_ValueEditor):
    def __init__(self, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._edit = LineEdit(self)
        self._edit.setPlaceholderText(self._t("Value"))
        self._edit.textChanged.connect(self.changed)
        _row(self).addWidget(self._edit, 1)

    def value(self) -> Any:
        return self._edit.text()

    def set_value(self, value: Any) -> None:
        self._edit.setText("" if value is None else str(value))

    def refresh_ui_texts(self) -> None:
        self._edit.setPlaceholderText(self._t("Value"))


class _EnumValueEditor(_ValueEditor):
    def __init__(self, choices: tuple[str, ...], parent=None):
        super().__init__(parent)
        self._combo = ComboBox(self)
        for choice in choices:
            self._combo.addItem(choice, userData=choice)
        self._combo.currentIndexChanged.connect(self.changed)
        _row(self).addWidget(self._combo, 1)

    def value(self) -> Any:
        return self._combo.currentData()

    def set_value(self, value: Any) -> None:
        index = self._combo.findData(str(value or ""))
        self._combo.setCurrentIndex(index if index >= 0 else 0)


class _NumberValueEditor(_ValueEditor):
    def __init__(self, integer: bool, parent=None):
        super().__init__(parent)
        if integer:
            self._spin = CompactSpinBox(self)
            self._spin.setRange(-100000, 100000)
        else:
            self._spin = CompactDoubleSpinBox(self)
            self._spin.setRange(-100000.0, 100000.0)
            self._spin.setDecimals(3)
            self._spin.setSingleStep(0.05)
        self._spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._spin.valueChanged.connect(self.changed)
        _row(self).addWidget(self._spin, 1)

    def value(self) -> Any:
        return self._spin.value()

    def set_value(self, value: Any) -> None:
        try:
            self._spin.setValue(type(self._spin.value())(value))
        except (TypeError, ValueError):
            pass


class _RangeValueEditor(_ValueEditor):
    def __init__(self, integer: bool, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._low = _NumberValueEditor(integer, self)
        self._high = _NumberValueEditor(integer, self)
        self._separator = CaptionLabel(self._t("to"), self)
        self._low.changed.connect(self.changed)
        self._high.changed.connect(self.changed)
        layout = _row(self)
        layout.addWidget(self._low, 1)
        layout.addWidget(self._separator)
        layout.addWidget(self._high, 1)

    def value(self) -> Any:
        return [self._low.value(), self._high.value()]

    def set_value(self, value: Any) -> None:
        if isinstance(value, dict):
            value = [value.get("min"), value.get("max")]
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            self._low.set_value(value[0])
            self._high.set_value(value[1])

    def refresh_ui_texts(self) -> None:
        self._separator.setText(self._t("to"))


class _ColorValueEditor(_ValueEditor):
    def __init__(self, t_func: Callable, config_service, with_tolerance: bool, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._picker = ColorPickerWidget(
            dialog_title="Select color",
            default_color="#000000",
            config_key="saved_colors",
            config_service=config_service,
            i18n_func=t_func,
        )
        self._picker.color_changed.connect(lambda _value: self.changed.emit())
        layout = _row(self)
        layout.addWidget(self._picker, 1)
        self._tolerance_label: Optional[CaptionLabel] = None
        self._tolerance: Optional[CompactSpinBox] = None
        if with_tolerance:
            self._tolerance_label = CaptionLabel(self._t("Tolerance"), self)
            self._tolerance = CompactSpinBox(self)
            self._tolerance.setRange(0, 442)  # 0..sqrt(3)*255，RGB 空间的最大距离
            self._tolerance.setValue(30)
            self._tolerance.valueChanged.connect(self.changed)
            layout.addWidget(self._tolerance_label)
            layout.addWidget(self._tolerance)

    def value(self) -> Any:
        color = self._picker.get_color()
        if self._tolerance is None:
            return color
        return {"color": color, "tolerance": self._tolerance.value()}

    def set_value(self, value: Any) -> None:
        color, tolerance = value, None
        if isinstance(value, dict):
            color, tolerance = value.get("color"), value.get("tolerance")
        if isinstance(color, (list, tuple)) and len(color) >= 3:
            color = "#{:02X}{:02X}{:02X}".format(*(int(channel) for channel in color[:3]))
        if color:
            self._picker.set_color(str(color))
        if self._tolerance is not None and tolerance is not None:
            try:
                self._tolerance.setValue(int(tolerance))
            except (TypeError, ValueError):
                pass

    def refresh_ui_texts(self) -> None:
        self._picker.refresh_ui_texts()
        if self._tolerance_label is not None:
            self._tolerance_label.setText(self._t("Tolerance"))


class _BoolValueEditor(_ValueEditor):
    def __init__(self, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._combo = ComboBox(self)
        self._combo.addItem(self._t("Yes"), userData=True)
        self._combo.addItem(self._t("No"), userData=False)
        self._combo.currentIndexChanged.connect(self.changed)
        _row(self).addWidget(self._combo, 1)

    def value(self) -> Any:
        return bool(self._combo.currentData())

    def set_value(self, value: Any) -> None:
        self._combo.setCurrentIndex(0 if bool(value) else 1)

    def refresh_ui_texts(self) -> None:
        self._combo.setItemText(0, self._t("Yes"))
        self._combo.setItemText(1, self._t("No"))


class _FontValueEditor(_ValueEditor):
    def __init__(self, locale_getter, parent=None):
        super().__init__(parent)
        self._combo = FontComboBox(self, locale_getter=locale_getter)
        self._combo.currentIndexChanged.connect(self.changed)
        _row(self).addWidget(self._combo, 1)

    def value(self) -> Any:
        return self._combo.currentFamily()

    def set_value(self, value: Any) -> None:
        if value:
            self._combo.setCurrentFamily(str(value))

    def refresh_ui_texts(self) -> None:
        self._combo.refresh_ui_texts()


def build_value_editor(
    spec: FieldSpec,
    op: str,
    t_func: Callable,
    config_service=None,
    locale_getter=None,
    parent: QWidget | None = None,
) -> Optional[_ValueEditor]:
    """按字段类型 + 运算符造值编辑器；``None`` 表示该运算符不需要值。"""
    if op in VALUELESS_OPS:
        return None
    if spec.kind == KIND_NUMBER:
        editor = _RangeValueEditor(spec.integer, t_func, parent) if op == "between" \
            else _NumberValueEditor(spec.integer, parent)
    elif spec.kind == KIND_ENUM:
        editor = _EnumValueEditor(spec.choices, parent)
    elif spec.kind == KIND_COLOR:
        editor = _ColorValueEditor(t_func, config_service, op == "color_near", parent)
    elif spec.kind == KIND_BOOL:
        editor = _BoolValueEditor(t_func, parent)
    elif spec.key == "font_family":
        editor = _FontValueEditor(locale_getter, parent)
    else:
        editor = _TextValueEditor(t_func, parent)
    return editor


# ─── 条件行 ───


class ConditionRow(QWidget):
    """``[字段 ▼] [运算符 ▼] [值] [×]``。"""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, t_func: Callable, config_service=None, locale_getter=None, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._config_service = config_service
        self._locale_getter = locale_getter
        self._editor: Optional[_ValueEditor] = None

        layout = _row(self)
        self.field_combo = ComboBox(self)
        self.field_combo.setMinimumWidth(150)
        for spec in FIELDS:
            self.field_combo.addItem(self._t(spec.label), userData=spec.key)
        self.op_combo = ComboBox(self)
        self.op_combo.setMinimumWidth(130)
        self.value_holder = QWidget(self)
        self._value_layout = _row(self.value_holder)
        self.remove_button = ToolButton(FIF.CLOSE, self)
        self.remove_button.setToolTip(self._t("Remove condition"))

        layout.addWidget(self.field_combo)
        layout.addWidget(self.op_combo)
        layout.addWidget(self.value_holder, 1)
        layout.addWidget(self.remove_button)

        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        self.op_combo.currentIndexChanged.connect(self._on_op_changed)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        self._rebuild_ops()

    # --- 内部 ---

    def _current_spec(self) -> FieldSpec:
        return FIELDS_BY_KEY.get(self.field_combo.currentData()) or FIELDS[0]

    def _rebuild_ops(self, preferred: str | None = None) -> None:
        spec = self._current_spec()
        self.op_combo.blockSignals(True)
        self.op_combo.clear()
        for op in OPS_BY_KIND.get(spec.kind, ()):
            self.op_combo.addItem(self._t(OP_LABELS.get(op, op)), userData=op)
        if preferred:
            index = self.op_combo.findData(preferred)
            if index >= 0:
                self.op_combo.setCurrentIndex(index)
        self.op_combo.blockSignals(False)
        self._rebuild_editor()

    def _rebuild_editor(self, value: Any = None) -> None:
        while self._value_layout.count():
            item = self._value_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._editor = build_value_editor(
            self._current_spec(),
            str(self.op_combo.currentData() or ""),
            self._t,
            self._config_service,
            self._locale_getter,
            self.value_holder,
        )
        if self._editor is not None:
            self._editor.changed.connect(self.changed)
            self._value_layout.addWidget(self._editor, 1)
            if value is not None:
                self._editor.set_value(value)
        self.value_holder.setVisible(self._editor is not None)

    def _on_field_changed(self) -> None:
        self._rebuild_ops()
        self.changed.emit()

    def _on_op_changed(self) -> None:
        self._rebuild_editor()
        self.changed.emit()

    # --- 契约 ---

    def to_dict(self) -> dict:
        return {
            "field": self.field_combo.currentData(),
            "op": self.op_combo.currentData(),
            "value": self._editor.value() if self._editor is not None else None,
        }

    def load(self, condition: dict) -> None:
        self.blockSignals(True)
        try:
            index = self.field_combo.findData(str(condition.get("field", "")))
            self.field_combo.blockSignals(True)
            self.field_combo.setCurrentIndex(index if index >= 0 else 0)
            self.field_combo.blockSignals(False)
            self._rebuild_ops(str(condition.get("op", "")))
            self._rebuild_editor(condition.get("value"))
        finally:
            self.blockSignals(False)

    def refresh_ui_texts(self) -> None:
        for position, spec in enumerate(FIELDS):
            self.field_combo.setItemText(position, self._t(spec.label))
        ops = OPS_BY_KIND.get(self._current_spec().kind, ())
        for position, op in enumerate(ops):
            self.op_combo.setItemText(position, self._t(OP_LABELS.get(op, op)))
        self.remove_button.setToolTip(self._t("Remove condition"))
        if self._editor is not None:
            self._editor.refresh_ui_texts()


# ─── 动作块 ───


class _ActionCard(SimpleCardWidget):
    """带启用开关的动作块基类。"""

    changed = pyqtSignal()
    action_type = ""

    def __init__(self, title_key: str, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._title_key = title_key
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 10, 12, 10)
        self._root.setSpacing(8)
        self.enabled_box = CheckBox(self._t(title_key), self)
        self.enabled_box.toggled.connect(self._on_toggled)
        self._root.addWidget(self.enabled_box)
        self.body = QWidget(self)
        self.body.setEnabled(False)
        self._root.addWidget(self.body)

    def _on_toggled(self, checked: bool) -> None:
        self.body.setEnabled(checked)
        self.changed.emit()

    def is_enabled(self) -> bool:
        return self.enabled_box.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_box.setChecked(bool(enabled))
        self.body.setEnabled(bool(enabled))

    def to_actions(self) -> list[dict]:
        """本块产出的动作；一张卡可以出多条（替换/富文本都是条目列表）。"""
        raise NotImplementedError

    def load_actions(self, actions: list[dict]) -> None:
        raise NotImplementedError

    def refresh_ui_texts(self) -> None:
        self.enabled_box.setText(self._t(self._title_key))


class _EntryListActionCard(_ActionCard):
    """条目可增删的动作块：一条条目 = 一个动作，列表顺序 = 执行顺序。"""

    add_label_key = ""
    _loading_entries = False

    def __init__(self, title_key: str, t_func: Callable, parent=None):
        super().__init__(title_key, t_func, parent)
        self._entries: list[QWidget] = []
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self._entries_host = QWidget(self.body)
        self._entries_layout = QVBoxLayout(self._entries_host)
        self._entries_layout.setContentsMargins(0, 0, 0, 0)
        self._entries_layout.setSpacing(6)
        body_layout.addWidget(self._entries_host)
        self.add_button = PushButton(self._t(self.add_label_key), self.body, FIF.ADD)
        self.add_button.clicked.connect(lambda: self._add_entry())
        body_layout.addWidget(self.add_button)

    def _new_entry(self) -> QWidget:
        raise NotImplementedError

    def _add_entry(self, action: Optional[dict] = None, silent: bool = False) -> QWidget:
        entry = self._new_entry()
        entry.changed.connect(self.changed)                       # type: ignore[attr-defined]
        entry.remove_requested.connect(lambda: self._remove_entry(entry))  # type: ignore[attr-defined]
        if action:
            entry.load(action)                                    # type: ignore[attr-defined]
        self._entries_layout.addWidget(entry)
        self._entries.append(entry)
        if not silent:
            self.changed.emit()
        return entry

    def _remove_entry(self, entry: QWidget) -> None:
        if entry in self._entries:
            self._entries.remove(entry)
        self._entries_layout.removeWidget(entry)
        entry.deleteLater()
        self.changed.emit()

    def _on_toggled(self, checked: bool) -> None:
        # 勾上却一条都没有等于开了个空块，直接给一条空白条目省一次点击。
        # 回填方案时例外：条目马上就要照方案铺出来，这里再补一条就成了多余的空条目。
        if checked and not self._entries and not self._loading_entries:
            self._add_entry(silent=True)
        super()._on_toggled(checked)

    def to_actions(self) -> list[dict]:
        if not self.is_enabled():
            return []
        actions = [entry.to_action() for entry in self._entries]  # type: ignore[attr-defined]
        return [action for action in actions if action]

    def load_actions(self, actions: list[dict]) -> None:
        for entry in list(self._entries):
            self._entries.remove(entry)
            self._entries_layout.removeWidget(entry)
            entry.deleteLater()
        self._loading_entries = True
        try:
            self.set_enabled(bool(actions))
        finally:
            self._loading_entries = False
        for action in actions:
            self._add_entry(action, silent=True)

    def refresh_ui_texts(self) -> None:
        super().refresh_ui_texts()
        self.add_button.setText(self._t(self.add_label_key))
        for entry in self._entries:
            entry.refresh_ui_texts()                              # type: ignore[attr-defined]


class _PatternRow(QWidget):
    """``pattern`` + ``regex`` 两个动作共用的头一行。"""

    changed = pyqtSignal()

    def __init__(self, t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self.label = CaptionLabel(self._t("Match text"), self)
        self.pattern = LineEdit(self)
        self.pattern.setPlaceholderText(self._t("Text or regular expression"))
        self.pattern.setClearButtonEnabled(True)
        self.regex = CheckBox(self._t("Regex"), self)
        self.pattern.textChanged.connect(self.changed)
        self.regex.toggled.connect(self.changed)
        layout = _row(self)
        layout.addWidget(self.label)
        layout.addWidget(self.pattern, 1)
        layout.addWidget(self.regex)

    def refresh_ui_texts(self) -> None:
        self.label.setText(self._t("Match text"))
        self.pattern.setPlaceholderText(self._t("Text or regular expression"))
        self.regex.setText(self._t("Regex"))


class SetFieldsActionCard(_ActionCard):
    """批量改 region 属性：一行一个字段。"""

    action_type = ACTION_SET_FIELDS

    def __init__(self, t_func: Callable, config_service=None, locale_getter=None, parent=None):
        super().__init__("Set region properties", t_func, parent)
        self._config_service = config_service
        self._locale_getter = locale_getter
        self._rows: list[QWidget] = []
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self._rows_host = QWidget(self.body)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        body_layout.addWidget(self._rows_host)
        self.add_button = PushButton(self._t("Add property"), self.body, FIF.ADD)
        self.add_button.clicked.connect(lambda: self._add_row())
        body_layout.addWidget(self.add_button)

    def _writable_specs(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in FIELDS if spec.writable)

    def _add_row(self, key: str | None = None, value: Any = None) -> QWidget:
        row = QWidget(self._rows_host)
        layout = _row(row)
        combo = ComboBox(row)
        combo.setMinimumWidth(150)
        for spec in self._writable_specs():
            combo.addItem(self._t(spec.label), userData=spec.key)
        holder = QWidget(row)
        holder_layout = _row(holder)
        remove = ToolButton(FIF.CLOSE, row)
        remove.setToolTip(self._t("Remove property"))
        layout.addWidget(combo)
        layout.addWidget(holder, 1)
        layout.addWidget(remove)

        row.field_combo = combo          # type: ignore[attr-defined]
        row.holder = holder              # type: ignore[attr-defined]
        row.holder_layout = holder_layout  # type: ignore[attr-defined]
        row.editor = None                # type: ignore[attr-defined]

        def rebuild(initial: Any = None) -> None:
            while holder_layout.count():
                item = holder_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            spec = FIELDS_BY_KEY.get(combo.currentData())
            editor = build_value_editor(spec, "set", self._t, self._config_service,
                                        self._locale_getter, holder) if spec else None
            row.editor = editor          # type: ignore[attr-defined]
            if editor is not None:
                editor.changed.connect(self.changed)
                holder_layout.addWidget(editor, 1)
                if initial is not None:
                    editor.set_value(initial)

        row.rebuild = rebuild            # type: ignore[attr-defined]
        combo.currentIndexChanged.connect(lambda: (rebuild(), self.changed.emit()))
        remove.clicked.connect(lambda: self._remove_row(row))

        if key:
            index = combo.findData(key)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        rebuild(value)
        self._rows_layout.addWidget(row)
        self._rows.append(row)
        return row

    def _remove_row(self, row: QWidget) -> None:
        if row in self._rows:
            self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        self.changed.emit()

    def to_actions(self) -> list[dict]:
        if not self.is_enabled():
            return []
        fields: dict[str, Any] = {}
        for row in self._rows:
            key = row.field_combo.currentData()          # type: ignore[attr-defined]
            editor = row.editor                          # type: ignore[attr-defined]
            if key and editor is not None:
                fields[str(key)] = editor.value()
        return [{"type": self.action_type, "fields": fields}] if fields else []

    def load_actions(self, actions: list[dict]) -> None:
        for row in list(self._rows):
            self._remove_row(row)
        # 改 region 属性天然只有一条：字段本身就是列表，再套一层没意义
        action = actions[0] if actions else None
        self.set_enabled(bool(action))
        for key, value in ((action or {}).get("fields") or {}).items():
            self._add_row(str(key), value)

    def refresh_ui_texts(self) -> None:
        super().refresh_ui_texts()
        self.add_button.setText(self._t("Add property"))
        specs = self._writable_specs()
        for row in self._rows:
            for position, spec in enumerate(specs):
                row.field_combo.setItemText(position, self._t(spec.label))  # type: ignore[attr-defined]
            editor = row.editor                                             # type: ignore[attr-defined]
            if editor is not None:
                editor.refresh_ui_texts()


# 清空模式能勾的项 = richtext.v1 TextStyle 的顶层键（json 里的 camelCase 形态）
# + ruby/tcy 两个节点。文案沿用富文本样式编辑器的既有 key，不另起一套近义词。
# transform 用 "顶层.子字段" 的点号路径拆到子项，因为旋转和偏移是两码事，
# 样式编辑器里也是分开的三个控件；stroke/glow 那几个不拆 —— "只清描边宽度
# 留着描边颜色"这种状态没人看得懂。
# noTcy 不在列：样式编辑器本来就设不了它，单给个"清空"入口没有对应的"设置"入口。
CLEAR_STYLE_KEYS: tuple[tuple[str, str], ...] = (
    ("bold", "Bold"),
    ("italic", "Italic"),
    ("underline", "Underline"),
    ("emphasis", "Emphasis"),
    ("color", "Color"),
    ("stroke", "Stroke"),
    ("outerStroke", "Outer Stroke"),
    ("glow", "Glow"),
    ("fontSize", "Font Size"),
    ("fontFamily", "Font Family"),
    ("scale", "Scale"),
    ("kerning", "Kerning"),
    ("preKerning", "Pre Kerning"),
    ("lineKerning", "Line Kerning"),
    ("nextKerning", "Next Kerning"),
    ("verticalAdvance", "Force Advance"),
    ("transform.rotation", "Rotation"),
    ("transform.offsetX", "Offset X"),
    ("transform.offsetY", "Offset Y"),
    ("ruby", "Ruby"),
    ("tcy", "TCY"),
)


class ClearStyleKeysDialog(FluentSecondaryDialog):
    """勾选清空模式要删掉哪些项；一项不勾 = 全清。"""

    def __init__(self, keys: list[str], t_func: Callable, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._boxes: dict[str, CheckBox] = {}
        self.setWindowTitle(self._t("Choose properties to clear"))
        self.setMinimumSize(420, 420)
        self.resize(460, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        self.title_label = SubtitleLabel(self._t("Choose properties to clear"))
        root.addWidget(self.title_label)
        self.hint_label = BodyLabel(
            self._t("Select none to wipe the whole style, ruby and TCY included")
        )
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(ScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        selected = set(keys or [])
        for index, (key, label) in enumerate(CLEAR_STYLE_KEYS):
            box = CheckBox(self._t(label), host)
            box.setChecked(key in selected)
            self._boxes[key] = box
            grid.addWidget(box, index // 2, index % 2)
        scroll.setWidget(host)
        scroll.enableTransparentBackground()
        root.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.all_button = PushButton(self._t("Select All"))
        self.all_button.clicked.connect(lambda: self._set_all(True))
        self.none_button = PushButton(self._t("Select None"))
        self.none_button.clicked.connect(lambda: self._set_all(False))
        self.cancel_button = PushButton(self._t("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = PushButton(self._t("OK"))
        self.ok_button.setIcon(FIF.ACCEPT)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.all_button)
        buttons.addWidget(self.none_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            box.setChecked(checked)

    def keys(self) -> list[str]:
        return [key for key, _label in CLEAR_STYLE_KEYS if self._boxes[key].isChecked()]


class _ActionEntry(QWidget):
    """条目列表里的一条。左边内容、右边一个删除按钮。"""
    changed = pyqtSignal()
    remove_requested = pyqtSignal()

    def __init__(self, t_func: Callable, remove_tip_key: str, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._remove_tip_key = remove_tip_key
        outer = _row(self)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.remove_button = ToolButton(FIF.CLOSE, self)
        self.remove_button.setToolTip(self._t(remove_tip_key))
        self.remove_button.clicked.connect(self.remove_requested)
        outer.addWidget(self.content, 1)
        outer.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)

    def to_action(self) -> Optional[dict]:
        raise NotImplementedError

    def load(self, action: dict) -> None:
        raise NotImplementedError

    def refresh_ui_texts(self) -> None:
        self.remove_button.setToolTip(self._t(self._remove_tip_key))


class _ReplaceEntry(_ActionEntry):
    def __init__(self, t_func: Callable, parent=None):
        super().__init__(t_func, "Remove replacement", parent)
        self.pattern_row = _PatternRow(self._t, self.content)
        self.pattern_row.changed.connect(self.changed)
        self.content_layout.addWidget(self.pattern_row)

        replace_host = QWidget(self.content)
        replace_layout = _row(replace_host)
        self.replace_label = CaptionLabel(self._t("Replace with"), replace_host)
        self.replace = LineEdit(replace_host)
        self.replace.setPlaceholderText(self._t("Supports backreferences like \\1 when regex is on"))
        self.replace.setClearButtonEnabled(True)
        self.replace.textChanged.connect(self.changed)
        replace_layout.addWidget(self.replace_label)
        replace_layout.addWidget(self.replace, 1)
        self.content_layout.addWidget(replace_host)

    def to_action(self) -> Optional[dict]:
        # 替换空串没意义，pattern 留空就是这条没填完
        if not self.pattern_row.pattern.text():
            return None
        return {
            "type": ACTION_REPLACE_TEXT,
            "pattern": self.pattern_row.pattern.text(),
            "regex": self.pattern_row.regex.isChecked(),
            "replace": self.replace.text(),
        }

    def load(self, action: dict) -> None:
        self.pattern_row.pattern.setText(str(action.get("pattern", "") or ""))
        self.pattern_row.regex.setChecked(bool(action.get("regex", False)))
        self.replace.setText(str(action.get("replace", "") or ""))

    def refresh_ui_texts(self) -> None:
        super().refresh_ui_texts()
        self.pattern_row.refresh_ui_texts()
        self.replace_label.setText(self._t("Replace with"))
        self.replace.setPlaceholderText(self._t("Supports backreferences like \\1 when regex is on"))


class _RichTextEntry(_ActionEntry):
    """一条富文本条目：模式 + 匹配 + （样式 | 要清空的项）。"""

    _MODES = (
        (RICH_MODE_OVERWRITE, "Overwrite", "Your properties win; the rest of the hit keeps what it has"),
        (RICH_MODE_FILL, "Fill in", "Properties the hit already has win; only the missing ones are added"),
        (RICH_MODE_CLEAR, "Clear", "Remove the selected properties; select none to wipe style, ruby and TCY"),
    )

    def __init__(self, t_func: Callable, parent=None):
        super().__init__(t_func, "Remove style entry", parent)
        self._style: dict = {}
        self._clear_keys: list[str] = []

        head = QWidget(self.content)
        head_layout = _row(head)
        self.mode_combo = ComboBox(head)
        self.mode_combo.setMinimumWidth(110)
        for mode, label, _tip in self._MODES:
            self.mode_combo.addItem(self._t(label), userData=mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.pattern_row = _PatternRow(self._t, head)
        self.pattern_row.changed.connect(self.changed)
        head_layout.addWidget(self.mode_combo)
        head_layout.addWidget(self.pattern_row, 1)
        self.content_layout.addWidget(head)

        detail = QWidget(self.content)
        detail_layout = _row(detail)
        self.style_button = PushButton(self._t("Edit Style"), detail, FIF.FONT)
        self.style_button.clicked.connect(self._edit_style)
        self.clear_button = PushButton(self._t("Choose properties to clear"), detail, FIF.ERASE_TOOL)
        self.clear_button.clicked.connect(self._edit_clear_keys)
        self.summary = CaptionLabel("", detail)
        detail_layout.addWidget(self.style_button)
        detail_layout.addWidget(self.clear_button)
        detail_layout.addWidget(self.summary, 1)
        self.content_layout.addWidget(detail)

        self.hint = CaptionLabel(self._t("Leave the pattern empty to target the whole region"), self.content)
        self.hint.setWordWrap(True)
        self.content_layout.addWidget(self.hint)
        self._on_mode_changed()

    def _mode(self) -> str:
        return self.mode_combo.currentData() or RICH_MODE_OVERWRITE

    def _on_mode_changed(self) -> None:
        clearing = self._mode() == RICH_MODE_CLEAR
        self.style_button.setVisible(not clearing)
        self.clear_button.setVisible(clearing)
        for mode, _label, tip in self._MODES:
            if mode == self._mode():
                self.mode_combo.setToolTip(self._t(tip))
        self._update_summary()
        self.changed.emit()

    def _edit_style(self) -> None:
        # 延迟导入：规则页模块会在导入时拉起字体列表等重资源
        from ui.secondary_pages.rich_text_rules_editor import RichTextStyleDialog

        dialog = RichTextStyleDialog(copy.deepcopy(self._style), self._t, self)
        if dialog.exec() == DialogCode.Accepted:
            self._style = dialog.style()
            self._update_summary()
            self.changed.emit()

    def _edit_clear_keys(self) -> None:
        dialog = ClearStyleKeysDialog(self._clear_keys, self._t, self)
        if dialog.exec() == DialogCode.Accepted:
            self._clear_keys = dialog.keys()
            self._update_summary()
            self.changed.emit()

    def _update_summary(self) -> None:
        if self._mode() == RICH_MODE_CLEAR:
            if not self._clear_keys:
                self.summary.setText(self._t("Everything, ruby and TCY included"))
            else:
                labels = dict(CLEAR_STYLE_KEYS)
                self.summary.setText("、".join(self._t(labels[key]) for key in self._clear_keys
                                               if key in labels))
            return
        from ui.secondary_pages.rich_text_rules_editor import _style_summary

        self.summary.setText(_style_summary(self._style, self._t("No style set")))

    def to_action(self) -> Optional[dict]:
        # pattern 留空 = 整条 region 的全部文字，所以这里不拦空 pattern
        mode = self._mode()
        action = {
            "type": ACTION_RICH_TEXT,
            "mode": mode,
            "pattern": self.pattern_row.pattern.text(),
            "regex": self.pattern_row.regex.isChecked(),
        }
        if mode == RICH_MODE_CLEAR:
            action["clear"] = list(self._clear_keys)
            return action
        # 控件把 ruby/tcy 塞在 style dict 里，方案文件要求它们与 style 平级
        style = copy.deepcopy(self._style)
        ruby = str(style.pop("ruby", "") or "")
        tcy = bool(style.pop("tcy", False))
        if not style and not ruby and not tcy:
            return None
        action.update({"style": style, "ruby": ruby, "tcy": tcy})
        return action

    def load(self, action: dict) -> None:
        index = self.mode_combo.findData(str(action.get("mode", "") or RICH_MODE_OVERWRITE))
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.mode_combo.blockSignals(False)
        self.pattern_row.pattern.setText(str(action.get("pattern", "") or ""))
        self.pattern_row.regex.setChecked(bool(action.get("regex", False)))
        self._clear_keys = [str(key) for key in action.get("clear") or []]
        style = copy.deepcopy(action.get("style") or {})
        if action.get("ruby"):
            style["ruby"] = action["ruby"]
        if action.get("tcy"):
            style["tcy"] = True
        self._style = style
        self._on_mode_changed()

    def refresh_ui_texts(self) -> None:
        super().refresh_ui_texts()
        self.pattern_row.refresh_ui_texts()
        for position, (_mode, label, _tip) in enumerate(self._MODES):
            self.mode_combo.setItemText(position, self._t(label))
        self.style_button.setText(self._t("Edit Style"))
        self.clear_button.setText(self._t("Choose properties to clear"))
        self.hint.setText(self._t("Leave the pattern empty to target the whole region"))
        self._update_summary()


class ReplaceTextActionCard(_EntryListActionCard):
    action_type = ACTION_REPLACE_TEXT
    add_label_key = "Add replacement"

    def __init__(self, t_func: Callable, parent=None):
        super().__init__("Replace matched text", t_func, parent)

    def _new_entry(self) -> QWidget:
        return _ReplaceEntry(self._t, self._entries_host)


class RichTextActionCard(_EntryListActionCard):
    """富文本条目列表；样式编辑直接复用规则页的 ``RichTextStyleDialog``。"""

    action_type = ACTION_RICH_TEXT
    add_label_key = "Add style entry"

    def __init__(self, t_func: Callable, parent=None):
        super().__init__("Apply rich text style to matched text", t_func, parent)

    def _new_entry(self) -> QWidget:
        return _RichTextEntry(self._t, self._entries_host)
