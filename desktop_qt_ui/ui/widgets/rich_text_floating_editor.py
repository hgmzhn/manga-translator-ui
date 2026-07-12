from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QAbstractSpinBox, QFontComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QSizePolicy, QTextEdit, QToolButton, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CompactDoubleSpinBox,
    CompactSpinBox,
    SimpleCardWidget,
    TextEdit,
)

from editor.rich_text_editing import (
    apply_ruby_to_range,
    apply_text_change,
    apply_style_to_range,
    apply_tcy_to_range,
    document_from_region,
    document_to_storage_text,
    remove_ruby_from_range,
    storage_text_to_editor_text,
    styled_text_for_key,
    style_for_range,
)
from services import get_config_service, get_i18n_manager
from utils.font_list import populate_font_combo

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint


def _compact_double_spin_box() -> CompactDoubleSpinBox:
    spin_box = CompactDoubleSpinBox()
    spin_box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    spin_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return spin_box


def _compact_spin_box() -> CompactSpinBox:
    spin_box = CompactSpinBox()
    spin_box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    spin_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return spin_box


class RichTextFloatingEditor(SimpleCardWidget):
    rich_text_changed = pyqtSignal(int, object, str)

    # 渲染器尚未实现这些样式字段的绘制（glow / outerStroke / lineKerning /
    # nextKerning，审查 F18）：schema 与编辑逻辑保留，UI 暂不开放；
    # 渲染实现后把对应按钮加回工具栏并从此集合移除即可恢复。
    _UNRENDERED_STYLE_ROW_KEYS = frozenset({"G", "OS", "LK", "NK"})

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(460)
        self.setMinimumHeight(210)
        self._region_index = -1
        self._region_data: dict = {}
        self._document: dict = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": []}]}
        self._selection_start = 0
        self._selection_end = 0
        self._updating = False
        # 自己发出的写回正在广播中（供 view 的 regions_changed handler 防自回环，F08）
        self._applying_own_change = False
        # F22：contentsChange → 模型写回的去抖（重置式 singleShot）。
        # 文档结构仍随每次按键同步更新（apply_text_change 必须逐次消费
        # contentsChange 增量），去抖的只是昂贵的 rich_text_changed 发射。
        self._pending_document_change = False
        self._emit_debounce_timer = QTimer(self)
        self._emit_debounce_timer.setSingleShot(True)
        self._emit_debounce_timer.setInterval(180)
        self._emit_debounce_timer.timeout.connect(self._flush_pending_document_change)
        self._style_rows: dict[str, tuple[QWidget, QWidget]] = {}
        self._style_row_hints: dict[str, str] = {}
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.text_box = TextEdit()
        self.text_box.setMinimumHeight(92)
        self.text_box.setMaximumHeight(150)
        self.text_box.setUndoRedoEnabled(True)
        self.text_box.installEventFilter(self)
        layout.addWidget(self.text_box)

        toolbar = QGridLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setHorizontalSpacing(4)
        toolbar.setVerticalSpacing(4)
        layout.addLayout(toolbar)

        self.bold_button = self._make_tool_button("B", "加粗")
        self.italic_button = self._make_tool_button("I", "斜体")
        self.color_button = self._make_tool_button("C", "文字颜色")
        self.size_button = self._make_tool_button("S", "绝对字号")
        self.scale_button = self._make_tool_button("%", "字号倍率")
        self.font_button = self._make_tool_button("F", "字体文件")
        self.stroke_button = self._make_tool_button("O", "描边颜色和宽度")
        self.glow_button = self._make_tool_button("G", "发光颜色和模糊")
        self.outer_stroke_button = self._make_tool_button("OS", "外描边颜色和宽度")
        self.emphasis_button = self._make_tool_button("D", "着重号")
        self.tcy_button = self._make_tool_button("T", "把选区包装成纵中横")
        self.no_tcy_button = self._make_tool_button("NT", "禁用自动纵中横")
        self.ruby_button = self._make_tool_button("R", "把选区包装成注音")
        self.rotation_button = self._make_tool_button("Rot", "局部旋转")
        self.kerning_button = self._make_tool_button("K", "字后间距")
        self.pre_kerning_button = self._make_tool_button("PK", "字前间距")
        self.line_kerning_button = self._make_tool_button("LK", "本行行距")
        self.next_kerning_button = self._make_tool_button("NK", "下一行行距")
        self.offset_button = self._make_tool_button("XY", "局部偏移")
        self.mirror_x_button = self._make_tool_button("M", "水平镜像")
        self.mirror_y_button = self._make_tool_button("MV", "垂直镜像")

        for index, button in enumerate((
            self.bold_button,
            self.italic_button,
            self.color_button,
            self.size_button,
            self.scale_button,
            self.font_button,
            self.stroke_button,
            self.emphasis_button,
            self.tcy_button,
            self.ruby_button,
            self.rotation_button,
            self.kerning_button,
            self.pre_kerning_button,
            self.offset_button,
            self.mirror_x_button,
            self.mirror_y_button,
        )):
            toolbar.addWidget(button, index // 10, index % 10)

        # F18：以下按钮对应的样式字段渲染器尚未实现绘制（有 UI 无渲染），
        # 暂不开放；底层 schema 与编辑逻辑保留，渲染实现后加回上方 tuple 即可。
        for button in (
            self.glow_button,
            self.outer_stroke_button,
            self.no_tcy_button,
            self.line_kerning_button,
            self.next_kerning_button,
        ):
            button.hide()

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(4)
        form.setVerticalSpacing(4)

        self.color_picker = ColorPickerWidget(
            dialog_title="Select rich text color",
            default_color="#E53935",
            config_key="saved_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.stroke_color_picker = ColorPickerWidget(
            dialog_title="Select rich text stroke color",
            default_color="#ffffff",
            config_key="saved_stroke_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.stroke_width_input = _compact_double_spin_box()
        self.stroke_width_input.setRange(0.0, 5.0)
        self.stroke_width_input.setSingleStep(0.01)
        self.stroke_width_input.setDecimals(2)
        self.stroke_width_input.setValue(0.07)
        self.stroke_row = self._two_part_row(self.stroke_color_picker, self.stroke_width_input)

        self.glow_color_picker = ColorPickerWidget(
            dialog_title="Select rich text glow color",
            default_color="#00ffff",
            config_key="saved_glow_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.glow_blur_input = _compact_double_spin_box()
        self.glow_blur_input.setRange(0.0, 5.0)
        self.glow_blur_input.setSingleStep(0.05)
        self.glow_blur_input.setDecimals(2)
        self.glow_blur_input.setValue(0.10)
        self.glow_row = self._two_part_row(self.glow_color_picker, self.glow_blur_input)

        self.outer_stroke_color_picker = ColorPickerWidget(
            dialog_title="Select rich text outer stroke color",
            default_color="#000000",
            config_key="saved_outer_stroke_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.outer_stroke_width_input = _compact_double_spin_box()
        self.outer_stroke_width_input.setRange(0.0, 5.0)
        self.outer_stroke_width_input.setSingleStep(0.01)
        self.outer_stroke_width_input.setDecimals(2)
        self.outer_stroke_width_input.setValue(0.20)
        self.outer_stroke_row = self._two_part_row(self.outer_stroke_color_picker, self.outer_stroke_width_input)

        self.font_size_input = _compact_spin_box()
        self.font_size_input.setRange(1, 1000)
        self.font_size_input.setKeyboardTracking(False)
        self.font_size_input.setValue(24)

        self.scale_input = _compact_double_spin_box()
        self.scale_input.setRange(0.1, 10.0)
        self.scale_input.setSingleStep(0.05)
        self.scale_input.setDecimals(2)
        self.scale_input.setValue(1.20)

        self.font_combo = QFontComboBox()
        self.font_combo.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        self.font_combo.setMinimumWidth(140)
        self.font_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._populate_font_combo()

        self.rotation_input = _compact_double_spin_box()
        self.rotation_input.setRange(-180.0, 180.0)
        self.rotation_input.setSingleStep(1.0)
        self.rotation_input.setDecimals(1)

        self.kerning_input = _compact_double_spin_box()
        self.kerning_input.setRange(-5.0, 5.0)
        self.kerning_input.setSingleStep(0.05)
        self.kerning_input.setDecimals(2)

        self.pre_kerning_input = _compact_double_spin_box()
        self.pre_kerning_input.setRange(-5.0, 5.0)
        self.pre_kerning_input.setSingleStep(0.05)
        self.pre_kerning_input.setDecimals(2)

        self.line_kerning_input = _compact_double_spin_box()
        self.line_kerning_input.setRange(-5.0, 5.0)
        self.line_kerning_input.setSingleStep(0.05)
        self.line_kerning_input.setDecimals(2)

        self.next_kerning_input = _compact_double_spin_box()
        self.next_kerning_input.setRange(-5.0, 5.0)
        self.next_kerning_input.setSingleStep(0.05)
        self.next_kerning_input.setDecimals(2)

        self.offset_x_input = _compact_double_spin_box()
        self.offset_x_input.setRange(-500.0, 500.0)
        self.offset_x_input.setSingleStep(1.0)
        self.offset_x_input.setDecimals(1)
        self.offset_y_input = _compact_double_spin_box()
        self.offset_y_input.setRange(-500.0, 500.0)
        self.offset_y_input.setSingleStep(1.0)
        self.offset_y_input.setDecimals(1)
        self.offset_row = self._two_part_row(self.offset_x_input, self.offset_y_input)

        self.ruby_text_input = QLineEdit()
        self.ruby_text_input.setPlaceholderText("注音")
        self.ruby_text_input.setMinimumHeight(28)

        self._add_style_row(form, "C", self.color_picker, "文字颜色")
        self._add_style_row(form, "S", self.font_size_input, "绝对字号")
        self._add_style_row(form, "%", self.scale_input, "字号倍率")
        self._add_style_row(form, "F", self.font_combo, "字体文件")
        self._add_style_row(form, "O", self.stroke_row, "描边颜色 / 宽度")
        self._add_style_row(form, "G", self.glow_row, "发光颜色 / 模糊")
        self._add_style_row(form, "OS", self.outer_stroke_row, "外描边颜色 / 宽度")
        self._add_style_row(form, "R", self.ruby_text_input, "注音文本")
        self._add_style_row(form, "Rot", self.rotation_input, "旋转角度")
        self._add_style_row(form, "K", self.kerning_input, "字后间距倍率")
        self._add_style_row(form, "PK", self.pre_kerning_input, "字前间距倍率")
        self._add_style_row(form, "LK", self.line_kerning_input, "本行行距倍率")
        self._add_style_row(form, "NK", self.next_kerning_input, "下一行行距倍率")
        self._add_style_row(form, "XY", self.offset_row, "X / Y 偏移")
        layout.addLayout(form)

        self.text_box.document().contentsChange.connect(self._on_text_changed)
        self.text_box.cursorPositionChanged.connect(self._on_cursor_position_changed)
        self.bold_button.clicked.connect(lambda checked: self._apply_toggle("bold", checked))
        self.italic_button.clicked.connect(lambda checked: self._apply_toggle("italic", checked))
        self.color_button.clicked.connect(lambda checked: self._toggle_row_and_apply("C", checked, {"color": self.color_picker.get_color()}))
        self.size_button.clicked.connect(lambda checked: self._toggle_row_and_apply("S", checked, {"fontSize": int(self.font_size_input.value())}))
        self.scale_button.clicked.connect(lambda checked: self._toggle_row_and_apply("%", checked, {"scale": float(self.scale_input.value())}))
        self.font_button.clicked.connect(self._on_font_button_clicked)
        self.stroke_button.clicked.connect(lambda checked: self._toggle_row_and_apply("O", checked, self._stroke_patch()))
        self.glow_button.clicked.connect(lambda checked: self._toggle_row_and_apply("G", checked, self._glow_patch()))
        self.outer_stroke_button.clicked.connect(lambda checked: self._toggle_row_and_apply("OS", checked, self._outer_stroke_patch()))
        self.emphasis_button.clicked.connect(lambda checked: self._apply_toggle("emphasis", checked))
        self.tcy_button.clicked.connect(lambda _checked: self._apply_tcy())
        self.no_tcy_button.clicked.connect(lambda checked: self._apply_toggle("noTcy", checked))
        self.ruby_button.clicked.connect(self._toggle_ruby_row)
        self.rotation_button.clicked.connect(lambda checked: self._toggle_row_and_apply("Rot", checked, {"transform": {"rotation": float(self.rotation_input.value())}}))
        self.kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("K", checked, {"kerning": float(self.kerning_input.value())}))
        self.pre_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("PK", checked, {"preKerning": float(self.pre_kerning_input.value())}))
        self.line_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("LK", checked, {"lineKerning": float(self.line_kerning_input.value())}))
        self.next_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("NK", checked, {"nextKerning": float(self.next_kerning_input.value())}))
        self.offset_button.clicked.connect(lambda checked: self._toggle_row_and_apply("XY", checked, {"transform": {"offsetX": float(self.offset_x_input.value()), "offsetY": float(self.offset_y_input.value())}}))
        self.mirror_x_button.clicked.connect(lambda checked: self._apply_style({"transform": {"mirrorX": bool(checked)}}))
        self.mirror_y_button.clicked.connect(lambda checked: self._apply_style({"transform": {"mirrorY": bool(checked)}}))
        self.color_picker.color_changed.connect(lambda color: self._apply_style({"color": color}))
        self.stroke_color_picker.color_changed.connect(lambda color: self._apply_style({"stroke": {"color": color}}))
        self.stroke_width_input.valueChanged.connect(lambda value: self._apply_style({"stroke": {"width": float(value)}}))
        self.glow_color_picker.color_changed.connect(lambda color: self._apply_style({"glow": {"color": color}}))
        self.glow_blur_input.valueChanged.connect(lambda value: self._apply_style({"glow": {"blur": float(value)}}))
        self.outer_stroke_color_picker.color_changed.connect(lambda color: self._apply_style({"outerStroke": {"color": color}}))
        self.outer_stroke_width_input.valueChanged.connect(lambda value: self._apply_style({"outerStroke": {"width": float(value)}}))
        self.font_size_input.valueChanged.connect(lambda value: self._apply_style({"fontSize": int(value)}))
        self.scale_input.valueChanged.connect(lambda value: self._apply_style({"scale": float(value)}))
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        self.ruby_text_input.returnPressed.connect(self._apply_ruby)
        self.rotation_input.valueChanged.connect(lambda value: self._apply_style({"transform": {"rotation": float(value)}}))
        self.kerning_input.valueChanged.connect(lambda value: self._apply_style({"kerning": float(value)}))
        self.pre_kerning_input.valueChanged.connect(lambda value: self._apply_style({"preKerning": float(value)}))
        self.line_kerning_input.valueChanged.connect(lambda value: self._apply_style({"lineKerning": float(value)}))
        self.next_kerning_input.valueChanged.connect(lambda value: self._apply_style({"nextKerning": float(value)}))
        self.offset_x_input.valueChanged.connect(lambda value: self._apply_style({"transform": {"offsetX": float(value)}}))
        self.offset_y_input.valueChanged.connect(lambda value: self._apply_style({"transform": {"offsetY": float(value)}}))
        self._hide_all_style_rows()
        self.hide()

    def eventFilter(self, obj, event):
        if hasattr(self, "text_box") and obj is self.text_box and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            if event.type() == QEvent.Type.FocusOut:
                # F22：失焦立即写回去抖期内的待发内容，防丢键
                self._flush_pending_document_change()
            self._update_selection_paint()
        return super().eventFilter(obj, event)

    def _t(self, key: str, **kwargs) -> str:
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def set_region(self, region_index: int, region_data: dict):
        # F22：换绑前先把上一个文档去抖期内的待发内容写回，防丢键
        self._flush_pending_document_change()
        self._region_index = int(region_index)
        self._region_data = dict(region_data or {})
        self._document = document_from_region(self._region_data)
        display_text = storage_text_to_editor_text(self._document)
        self._updating = True
        try:
            if self.text_box.toPlainText() != display_text:
                self.text_box.setPlainText(display_text)
            self._remember_editor_selection()
            self._update_selection_paint()
            self._refresh_controls()
        finally:
            self._updating = False

    def clear_region(self):
        # F22：解除绑定（取消选中/多选）前先写回待发内容，防丢键
        self._flush_pending_document_change()
        self._region_index = -1
        self._region_data = {}
        self._selection_start = 0
        self._selection_end = 0
        self.text_box.setExtraSelections([])
        self.hide()

    def focus_text(self):
        self.text_box.setFocus()

    def flush_pending_changes(self):
        """把去抖期内累计的文档变更立即写回模型（F22 防丢键）。

        失焦、选区切换、set_region/clear_region 换绑前都会调用。
        """
        self._flush_pending_document_change()

    def is_applying_own_change(self) -> bool:
        """正在广播自己的写回（view 的 regions_changed handler 据此防自回环，F08）。"""
        return self._applying_own_change

    def refresh_region_if_changed(self, region_index: int, region_data: dict):
        """模型区域数据变化时的再同步入口（F08）。

        译文内容（translation / translation_rich）未变时只更新缓存的
        region 数据、不重建文本框（保住光标位置）；变了才走 set_region
        以模型数据为准全量刷新，防止陈旧文档在下次样式操作/输入时覆盖模型。
        """
        region_data = dict(region_data or {})
        if int(region_index) == self._region_index and self._region_data:
            if (
                self._region_data.get("translation") == region_data.get("translation")
                and self._region_data.get("translation_rich") == region_data.get("translation_rich")
            ):
                self._region_data = region_data
                return
        self.set_region(region_index, region_data)

    def _make_tool_button(self, text: str, hint: str) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setCheckable(True)
        button.setFixedSize(30, 26)
        button.setIconSize(QSize(14, 14))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_hover_hint(button, hint)
        return button

    def _two_part_row(self, left: QWidget, right: QWidget) -> QWidget:
        widget = QWidget(self)
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(left, 1)
        row.addWidget(right, 1)
        return widget

    def _add_style_row(self, form: QFormLayout, key: str, widget: QWidget, hint: str):
        label = CaptionLabel(f"当前文本 · {key} {hint}")
        label.setFixedWidth(190)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        set_hover_hint(label, hint)
        set_hover_hint(widget, hint)
        form.addRow(label, widget)
        self._style_rows[key] = (label, widget)
        self._style_row_hints[key] = hint

    def _set_style_row_visible(self, key: str, visible: bool):
        if visible and key in self._UNRENDERED_STYLE_ROW_KEYS:
            visible = False  # 渲染未实现，暂不开放（F18）
        row = self._style_rows.get(key)
        if not row:
            return
        for widget in row:
            widget.setVisible(bool(visible))

    def _hide_all_style_rows(self):
        for key in self._style_rows:
            self._set_style_row_visible(key, False)

    def _populate_font_combo(self):
        populate_font_combo(self.font_combo)

    def _set_font_combo_value(self, font_value: str):
        if not font_value:
            self.font_combo.setCurrentIndex(-1)
            return
        self.font_combo.setCurrentFont(QFont(font_value))

    def _on_text_changed(self, position: int, chars_removed: int, chars_added: int):
        if self._updating or self._region_index < 0:
            return
        self._document = apply_text_change(
            self._document,
            self.text_box.toPlainText(),
            position,
            chars_removed,
            chars_added,
        )
        self._remember_editor_selection()
        # F22：文档结构每键同步更新（上面），昂贵的模型写回走去抖
        self._queue_document_changed()
        self._refresh_controls()

    def _on_cursor_position_changed(self):
        if self._updating:
            return
        if self.text_box.hasFocus():
            self._remember_editor_selection()
        self._update_selection_paint()
        self._refresh_controls()

    def _remember_editor_selection(self):
        cursor = self.text_box.textCursor()
        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        text_len = len(self.text_box.toPlainText())
        self._selection_start = max(0, min(start, text_len))
        self._selection_end = max(self._selection_start, min(end, text_len))

    def _selected_range(self) -> tuple[int, int]:
        text_len = len(self.text_box.toPlainText())
        start = max(0, min(self._selection_start, text_len))
        end = max(start, min(self._selection_end, text_len))
        return start, end

    def _update_selection_paint(self):
        start, end = self._selected_range()
        if start == end:
            self.text_box.setExtraSelections([])
            return
        cursor = QTextCursor(self.text_box.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = QTextCharFormat()
        selection.format.setBackground(QColor(80, 145, 255, 70))
        self.text_box.setExtraSelections([selection])

    def _on_font_changed(self, index: int):
        if self._updating or index < 0:
            return
        font_family = self.font_combo.currentFont().family()
        self._apply_style({"fontFamily": str(font_family)})

    def _on_font_button_clicked(self, checked: bool):
        if self._updating:
            return
        self._set_style_row_visible("F", checked)
        if checked:
            font_family = self.font_combo.currentFont().family()
            if font_family:
                self._apply_style({"fontFamily": str(font_family)})
            return
        self._apply_style({"fontFamily": None})

    def _apply_toggle(self, key: str, checked: bool):
        self._apply_style({key: True if checked else None})

    def _toggle_row_and_apply(self, row_key: str, checked: bool, patch: dict):
        if self._updating:
            return
        self._set_style_row_visible(row_key, checked)
        if checked:
            self._apply_style(patch)
        else:
            self._apply_style(self._clear_patch_for_row(row_key))

    def _toggle_ruby_row(self, checked: bool):
        if self._updating:
            return
        self._set_style_row_visible("R", checked)
        if checked:
            self.ruby_text_input.setFocus()
            return
        start, end = self._selected_range()
        self._document = remove_ruby_from_range(self._document, start, end)
        self.ruby_text_input.clear()
        self._emit_document_changed()
        self._refresh_controls()

    def _apply_tcy(self):
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        if start == end:
            return
        self._document = apply_tcy_to_range(self._document, start, end)
        self._emit_document_changed()
        self._refresh_controls()

    def _apply_ruby(self):
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        if start == end:
            return
        self._document = apply_ruby_to_range(self._document, start, end, self.ruby_text_input.text())
        if not self.ruby_text_input.text():
            self.ruby_button.setChecked(False)
            self._set_style_row_visible("R", False)
        self._emit_document_changed()
        self._refresh_controls()

    def _apply_style(self, patch: dict):
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        self._document = apply_style_to_range(self._document, start, end, patch)
        self._emit_document_changed()
        self._refresh_controls()

    def _queue_document_changed(self):
        # F22：每次按键直发 rich_text_changed 会触发白框全量测量 + 全量
        # 渲染；这里只登记待发标志并重置去抖定时器（singleShot 重置式）。
        # 增量本身已由 apply_text_change 逐次并入 _document，去抖不丢内容。
        self._pending_document_change = True
        self._emit_debounce_timer.start()

    def _flush_pending_document_change(self):
        if not self._pending_document_change:
            return
        self._emit_document_changed()

    def _emit_document_changed(self):
        self._emit_debounce_timer.stop()
        self._pending_document_change = False
        if self._region_index < 0:
            return
        storage_text = document_to_storage_text(self._document)
        # F08：标记"自己的写回正在广播"，view 的 regions_changed handler
        # 见此标志跳过刷新，防止自回环导致光标跳动。
        self._applying_own_change = True
        try:
            self.rich_text_changed.emit(
                self._region_index,
                self._document,
                storage_text,
            )
        finally:
            self._applying_own_change = False
        # 让缓存的 region 数据与刚写回模型的内容保持一致（与
        # editor_controller.update_translation_rich 的写入字段一致），
        # refresh_region_if_changed 的"内容未变"比较才不会误判重建。
        self._region_data["translation"] = storage_text
        self._region_data["translation_raw"] = storage_text
        self._region_data["translation_rich"] = self._document

    def _refresh_controls(self):
        if self._region_index < 0:
            return
        start, end = self._selected_range()
        style = style_for_range(self._document, start, end)
        self._updating = True
        try:
            self.bold_button.setChecked(bool(style.get("bold")))
            self.italic_button.setChecked(bool(style.get("italic")))
            self.emphasis_button.setChecked(bool(style.get("emphasis")))
            self.no_tcy_button.setChecked(bool(style.get("noTcy")))
            self.mirror_x_button.setChecked(bool(style.get("mirrorX")))
            self.mirror_y_button.setChecked(bool(style.get("mirrorY")))
            self.ruby_button.setChecked(bool(style.get("ruby")))
            if "rubyText" in style:
                self.ruby_text_input.setText(str(style.get("rubyText") or ""))
            elif not self.ruby_text_input.hasFocus():
                self.ruby_text_input.clear()

            self._set_button_and_row(self.color_button, "C", "color" in style)
            if "color" in style:
                self.color_picker.set_color(str(style.get("color") or "#E53935"))

            self._set_button_and_row(self.size_button, "S", "fontSize" in style)
            if "fontSize" in style:
                self.font_size_input.setValue(int(style.get("fontSize") or 24))

            self._set_button_and_row(self.scale_button, "%", "scale" in style)
            if "scale" in style:
                self.scale_input.setValue(float(style.get("scale") or 1.0))

            self._set_button_and_row(self.font_button, "F", "fontFamily" in style)
            self._set_font_combo_value(str(style.get("fontFamily") or ""))

            has_stroke = "strokeColor" in style or "strokeWidth" in style
            self._set_button_and_row(self.stroke_button, "O", has_stroke)
            if "strokeColor" in style:
                self.stroke_color_picker.set_color(str(style.get("strokeColor") or "#ffffff"))
            if "strokeWidth" in style:
                self.stroke_width_input.setValue(float(style.get("strokeWidth") or 0.0))

            has_glow = "glowColor" in style or "glowBlur" in style
            self._set_button_and_row(self.glow_button, "G", has_glow)
            if "glowColor" in style:
                self.glow_color_picker.set_color(str(style.get("glowColor") or "#00ffff"))
            if "glowBlur" in style:
                self.glow_blur_input.setValue(float(style.get("glowBlur") or 0.0))

            has_outer_stroke = "outerStrokeColor" in style or "outerStrokeWidth" in style
            self._set_button_and_row(self.outer_stroke_button, "OS", has_outer_stroke)
            if "outerStrokeColor" in style:
                self.outer_stroke_color_picker.set_color(str(style.get("outerStrokeColor") or "#000000"))
            if "outerStrokeWidth" in style:
                self.outer_stroke_width_input.setValue(float(style.get("outerStrokeWidth") or 0.0))

            self._set_button_and_row(self.rotation_button, "Rot", "rotation" in style)
            if "rotation" in style:
                self.rotation_input.setValue(float(style.get("rotation") or 0.0))

            self._set_button_and_row(self.kerning_button, "K", "kerning" in style)
            if "kerning" in style:
                self.kerning_input.setValue(float(style.get("kerning") or 0.0))

            self._set_button_and_row(self.pre_kerning_button, "PK", "preKerning" in style)
            if "preKerning" in style:
                self.pre_kerning_input.setValue(float(style.get("preKerning") or 0.0))

            self._set_button_and_row(self.line_kerning_button, "LK", "lineKerning" in style)
            if "lineKerning" in style:
                self.line_kerning_input.setValue(float(style.get("lineKerning") or 0.0))

            self._set_button_and_row(self.next_kerning_button, "NK", "nextKerning" in style)
            if "nextKerning" in style:
                self.next_kerning_input.setValue(float(style.get("nextKerning") or 0.0))

            has_offset = "offsetX" in style or "offsetY" in style
            self._set_button_and_row(self.offset_button, "XY", has_offset)
            if "offsetX" in style:
                self.offset_x_input.setValue(float(style.get("offsetX") or 0.0))
            if "offsetY" in style:
                self.offset_y_input.setValue(float(style.get("offsetY") or 0.0))

            self._set_style_row_visible("R", bool(style.get("ruby")) or self.ruby_button.isChecked())
            self._refresh_style_row_labels()
        finally:
            self._updating = False

    def _set_button_and_row(self, button: QToolButton, row_key: str, enabled: bool):
        button.setChecked(bool(enabled))
        self._set_style_row_visible(row_key, bool(enabled))

    def _refresh_style_row_labels(self):
        start, end = self._selected_range()
        for key, (label, widget) in self._style_rows.items():
            if label.isHidden() and widget.isHidden():
                continue
            style_text = styled_text_for_key(self._document, start, end, key) or "当前文本"
            hint = self._style_row_hints.get(key, key)
            label_text = f"{style_text} · {key} {hint}"
            label.setText(label_text)
            set_hover_hint(label, label_text)

    def _stroke_patch(self) -> dict:
        return {"stroke": {"color": self.stroke_color_picker.get_color(), "width": float(self.stroke_width_input.value())}}

    def _glow_patch(self) -> dict:
        return {"glow": {"color": self.glow_color_picker.get_color(), "blur": float(self.glow_blur_input.value())}}

    def _outer_stroke_patch(self) -> dict:
        return {"outerStroke": {"color": self.outer_stroke_color_picker.get_color(), "width": float(self.outer_stroke_width_input.value())}}

    def _clear_patch_for_row(self, row_key: str) -> dict:
        return {
            "C": {"color": None},
            "S": {"fontSize": None},
            "%": {"scale": None},
            "F": {"fontFamily": None},
            "O": {"stroke": None},
            "G": {"glow": None},
            "OS": {"outerStroke": None},
            "Rot": {"transform": {"rotation": None}},
            "K": {"kerning": None},
            "PK": {"preKerning": None},
            "LK": {"lineKerning": None},
            "NK": {"nextKerning": None},
            "XY": {"transform": {"offsetX": None, "offsetY": None}},
        }.get(row_key, {})
