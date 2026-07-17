from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QAbstractSpinBox, QFontComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QSizePolicy, QTextEdit, QToolButton, QVBoxLayout, QWidget
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
    remove_tcy_from_range,
    storage_text_to_editor_text,
    styled_text_for_key,
    style_row_coverage,
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


class _WheelGuardScrollArea(QScrollArea):
    """样式列表专用滚动区：滚轮到边界后也不向下层画布传播。"""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        content = self.widget()
        if content is not None:
            # 横向内容始终压缩到可视区内，不显示左右滚动条。
            content.setFixedWidth(self.viewport().width())

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class RichTextFloatingEditor(SimpleCardWidget):
    rich_text_changed = pyqtSignal(int, object, str)
    layout_size_changed = pyqtSignal()

    # 保留集中式禁用入口；当前已实现的 G / OS / LS 均直接开放。
    _UNRENDERED_STYLE_ROW_KEYS = frozenset()
    _DRAG_BORDER_WIDTH = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.setFixedWidth(420)
        self.setMinimumHeight(210)
        self._region_index = -1
        self._region_data: dict = {}
        self._document: dict = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": []}]}
        self._selection_start = 0
        self._selection_end = 0
        self._updating = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._manually_positioned = False
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
        self._style_row_cards: dict[str, QWidget] = {}
        self._style_remove_buttons: dict[str, QToolButton] = {}
        self._style_row_hints: dict[str, str] = {}
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()

        layout = QVBoxLayout(self)
        # 四周 12px 留作拖动热区；内部控件不会抢占这一圈。
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.text_box = TextEdit()
        text_box_font = self.text_box.font()
        text_box_font.setPointSize(14)
        self.text_box.setFont(text_box_font)
        self.text_box.setMinimumHeight(92)
        self.text_box.setFixedHeight(120)
        self.text_box.setUndoRedoEnabled(True)
        self.text_box.installEventFilter(self)
        layout.addWidget(self.text_box)

        toolbar = QGridLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setHorizontalSpacing(4)
        toolbar.setVerticalSpacing(4)
        layout.addLayout(toolbar)

        self.bold_button = self._make_tool_button("B", "加粗")
        self.italic_button = self._make_tool_button("I", "斜体（切变角度，默认15）")
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
        self.line_kerning_button = self._make_tool_button("LK", "与前一行的局部行距")
        self.next_kerning_button = self._make_tool_button("NK", "与后一行的局部行距")
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
            self.glow_button,
            self.outer_stroke_button,
            self.emphasis_button,
            self.tcy_button,
            self.ruby_button,
            self.rotation_button,
            self.kerning_button,
            self.pre_kerning_button,
            self.line_kerning_button,
            self.next_kerning_button,
            self.offset_button,
            self.mirror_x_button,
            self.mirror_y_button,
        )):
            toolbar.addWidget(button, index // 10, index % 10)

        # 不启用自动 TCY，因此 noTcy 暂不开放。
        for button in (
            self.no_tcy_button,
        ):
            button.hide()

        self.style_panel = QWidget(self)
        self.style_panel.setObjectName("richTextStylePanel")
        self.style_panel.setStyleSheet("""
            QWidget#richTextStylePanel {
                background-color: rgba(249, 250, 252, 252);
                border: 1px solid rgba(120, 130, 145, 75);
                border-radius: 10px;
            }
            QWidget#richTextStyleRow {
                background-color: rgba(255, 255, 255, 255);
                border: 1px solid rgba(125, 135, 150, 65);
                border-radius: 5px;
            }
            QWidget#richTextStyleRow CaptionLabel {
                color: #20242a;
                font-size: 11px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            CaptionLabel#selectedTextLabel {
                color: #005fb8;
                background-color: #f0f6ff;
                border: none;
                border-radius: 4px;
                padding: 2px 4px;
            }
            CaptionLabel#styleNameLabel {
                color: #20242a;
                background: transparent;
                border: none;
                font-weight: 700;
                font-size: 11px;
            }
            QWidget#styleRowDivider {
                background-color: #aeb8c6;
                border: none;
            }
            QWidget#richTextStyleRow QLineEdit,
            QWidget#richTextStyleRow QAbstractSpinBox,
            QWidget#richTextStyleRow QComboBox {
                min-height: 25px;
                font-size: 12px;
            }
            QToolButton#removeStyleButton {
                color: #b42318;
                background-color: #fff1f0;
                border: 1px solid #f0a39d;
                border-radius: 6px;
                font-size: 15px;
                font-weight: 700;
            }
            QToolButton#removeStyleButton:hover {
                color: white;
                background-color: #d92d20;
                border-color: #d92d20;
            }
        """)
        style_panel_layout = QVBoxLayout(self.style_panel)
        style_panel_layout.setContentsMargins(7, 7, 7, 7)
        style_panel_layout.setSpacing(6)
        self.style_panel_title = CaptionLabel("当前文字样式")
        title_font = self.style_panel_title.font()
        title_font.setPointSize(11)
        title_font.setBold(True)
        self.style_panel_title.setFont(title_font)
        self.style_panel_title.setStyleSheet("color: #20242a; background: transparent; border: none;")
        style_panel_layout.addWidget(self.style_panel_title)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(0)
        form.setVerticalSpacing(6)

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
        self.stroke_row = self._labeled_two_part_row("颜色", self.stroke_color_picker, "宽度", self.stroke_width_input)

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
        self.glow_row = self._labeled_two_part_row("颜色", self.glow_color_picker, "模糊", self.glow_blur_input)

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
        self.outer_stroke_row = self._labeled_two_part_row("颜色", self.outer_stroke_color_picker, "宽度", self.outer_stroke_width_input)

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

        # 斜体 = 切变角度（度），对齐参考实现 [i=15]：按钮开 = 写入角度，默认 15
        self.italic_angle_input = _compact_double_spin_box()
        self.italic_angle_input.setRange(-85.0, 85.0)
        self.italic_angle_input.setSingleStep(1.0)
        self.italic_angle_input.setDecimals(1)
        self.italic_angle_input.setValue(15.0)

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
        self.offset_x_input.setMinimumWidth(55)
        self.offset_y_input = _compact_double_spin_box()
        self.offset_y_input.setRange(-500.0, 500.0)
        self.offset_y_input.setSingleStep(1.0)
        self.offset_y_input.setDecimals(1)
        self.offset_y_input.setMinimumWidth(55)
        self.offset_row = self._labeled_two_part_row("X", self.offset_x_input, "Y", self.offset_y_input)

        # 无输入参数的样式使用空白占位；每一行的删除 X 由
        # _add_style_row 统一追加到最右侧。
        self.bold_style_placeholder = QWidget(self)
        self.emphasis_style_placeholder = QWidget(self)
        self.tcy_style_placeholder = QWidget(self)
        self.mirror_x_style_placeholder = QWidget(self)
        self.mirror_y_style_placeholder = QWidget(self)

        self.ruby_text_input = QLineEdit()
        self.ruby_text_input.setPlaceholderText("注音")
        self.ruby_text_input.setMinimumHeight(28)

        # 与上方工具栏保持相同顺序：B I C S % F O D T R Rot K PK XY M MV。
        self._add_style_row(form, "B", self.bold_style_placeholder, "加粗")
        self._add_style_row(form, "I", self.italic_angle_input, "斜体角度")
        self._add_style_row(form, "C", self.color_picker, "文字颜色")
        self._add_style_row(form, "S", self.font_size_input, "绝对字号")
        self._add_style_row(form, "%", self.scale_input, "字号倍率")
        self._add_style_row(form, "F", self.font_combo, "字体文件")
        self._add_style_row(form, "O", self.stroke_row, "描边颜色 / 宽度")
        self._add_style_row(form, "G", self.glow_row, "发光颜色 / 模糊")
        self._add_style_row(form, "OS", self.outer_stroke_row, "外描边颜色 / 宽度")
        self._add_style_row(form, "D", self.emphasis_style_placeholder, "着重号")
        self._add_style_row(form, "T", self.tcy_style_placeholder, "纵中横")
        self._add_style_row(form, "R", self.ruby_text_input, "注音文本")
        self._add_style_row(form, "Rot", self.rotation_input, "旋转角度")
        self._add_style_row(form, "K", self.kerning_input, "字后间距倍率")
        self._add_style_row(form, "PK", self.pre_kerning_input, "字前间距倍率")
        self._add_style_row(form, "LK", self.line_kerning_input, "前行距倍率")
        self._add_style_row(form, "NK", self.next_kerning_input, "后行距倍率")
        self._add_style_row(form, "XY", self.offset_row, "X / Y 偏移")
        self._add_style_row(form, "M", self.mirror_x_style_placeholder, "水平镜像")
        self._add_style_row(form, "MV", self.mirror_y_style_placeholder, "垂直镜像")
        style_panel_layout.addLayout(form)
        self.style_scroll = _WheelGuardScrollArea(self)
        self.style_scroll.setWidgetResizable(True)
        self.style_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.style_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.style_scroll.setMaximumHeight(245)
        self.style_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
            QScrollBar::handle:vertical { background: #9aa4b2; border-radius: 4px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.style_scroll.setWidget(self.style_panel)
        layout.addWidget(self.style_scroll)

        self.text_box.document().contentsChange.connect(self._on_text_changed)
        self.text_box.cursorPositionChanged.connect(self._on_cursor_position_changed)
        self.bold_button.clicked.connect(lambda checked: self._apply_toggle("bold", checked))
        self.italic_button.clicked.connect(lambda checked: self._toggle_row_and_apply("I", checked, {"italic": float(self.italic_angle_input.value())}))
        self.color_button.clicked.connect(lambda checked: self._toggle_row_and_apply("C", checked, {"color": self.color_picker.get_color()}))
        self.size_button.clicked.connect(lambda checked: self._toggle_row_and_apply("S", checked, {"fontSize": int(self.font_size_input.value())}))
        self.scale_button.clicked.connect(lambda checked: self._toggle_row_and_apply("%", checked, {"scale": float(self.scale_input.value())}))
        self.font_button.clicked.connect(self._on_font_button_clicked)
        self.stroke_button.clicked.connect(lambda checked: self._toggle_row_and_apply("O", checked, self._stroke_patch()))
        self.glow_button.clicked.connect(lambda checked: self._toggle_row_and_apply("G", checked, self._glow_patch()))
        self.outer_stroke_button.clicked.connect(lambda checked: self._toggle_row_and_apply("OS", checked, self._outer_stroke_patch()))
        self.emphasis_button.clicked.connect(lambda checked: self._apply_toggle("emphasis", checked))
        self.tcy_button.clicked.connect(self._toggle_tcy)
        self.no_tcy_button.clicked.connect(lambda checked: self._apply_toggle("noTcy", checked))
        self.ruby_button.clicked.connect(self._toggle_ruby_row)
        self.rotation_button.clicked.connect(lambda checked: self._toggle_row_and_apply("Rot", checked, {"transform": {"rotation": float(self.rotation_input.value())}}))
        self.kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("K", checked, {"kerning": float(self.kerning_input.value())}))
        self.pre_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("PK", checked, {"preKerning": float(self.pre_kerning_input.value())}))
        self.line_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("LK", checked, {"lineKerning": float(self.line_kerning_input.value())}))
        self.next_kerning_button.clicked.connect(lambda checked: self._toggle_row_and_apply("NK", checked, {"nextKerning": float(self.next_kerning_input.value())}))
        self.offset_button.clicked.connect(lambda checked: self._toggle_row_and_apply("XY", checked, self._offset_patch()))
        self.mirror_x_button.clicked.connect(lambda checked: self._apply_transform_toggle("mirrorX", checked))
        self.mirror_y_button.clicked.connect(lambda checked: self._apply_transform_toggle("mirrorY", checked))
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
        self.italic_angle_input.valueChanged.connect(lambda value: self._apply_style({"italic": float(value)}))
        self.kerning_input.valueChanged.connect(lambda value: self._apply_style({"kerning": float(value)}))
        self.pre_kerning_input.valueChanged.connect(lambda value: self._apply_style({"preKerning": float(value)}))
        self.line_kerning_input.valueChanged.connect(lambda value: self._apply_style({"lineKerning": float(value)}))
        self.next_kerning_input.valueChanged.connect(lambda value: self._apply_style({"nextKerning": float(value)}))
        self.offset_x_input.valueChanged.connect(lambda value: self._apply_style({"transform": {"offsetX": float(value)}}))
        self.offset_y_input.valueChanged.connect(lambda value: self._apply_style({"transform": {"offsetY": float(value)}}))
        self._hide_all_style_rows()
        self._update_style_panel_visibility()
        self.hide()

    # 浮窗覆盖在画布 viewport 上；标签、空白卡片等控件默认会忽略鼠标事件，
    # 若继续向父级传播就会触发画布选择逻辑并让浮窗消失。这里把整个浮窗
    # 设为事件边界，子控件正常处理自己的交互，未处理事件在此被吃掉。
    def _is_drag_border(self, pos) -> bool:
        border = self._DRAG_BORDER_WIDTH
        return (
            pos.x() <= border
            or pos.y() <= border
            or pos.x() >= self.width() - border
            or pos.y() >= self.height() - border
        )

    def is_manually_positioned(self) -> bool:
        return self._manually_positioned

    def reset_manual_position(self):
        self._manually_positioned = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_drag_border(event.position()):
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.mapToGlobal(QPoint(0, 0))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            parent = self.parentWidget()
            target_global = event.globalPosition().toPoint() - self._drag_offset
            self.move(parent.mapFromGlobal(target_global) if parent is not None else target_global)
            self._manually_positioned = True
            event.accept()
            return
        self.setCursor(
            Qt.CursorShape.SizeAllCursor
            if self._is_drag_border(event.position())
            else Qt.CursorShape.ArrowCursor
        )
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._manually_positioned = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        event.accept()

    def leaveEvent(self, event):
        if not self._dragging:
            self.unsetCursor()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

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
        button.setFixedSize(34, 30)
        button.setIconSize(QSize(14, 14))
        button.setStyleSheet("""
            QToolButton {
                color: #20242a;
                background-color: rgba(255, 255, 255, 235);
                border: 1px solid rgba(120, 130, 145, 75);
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
            }
            QToolButton:hover {
                background-color: #edf3fa;
                border-color: #8aa9c7;
            }
            QToolButton:checked {
                color: white;
                background-color: #0078d4;
                border-color: #0078d4;
            }
            QToolButton:pressed { background-color: #005fb8; }
        """)
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

    def _labeled_two_part_row(self, left_text: str, left: QWidget, right_text: str, right: QWidget) -> QWidget:
        widget = QWidget(self)
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        left_label = CaptionLabel(left_text)
        right_label = CaptionLabel(right_text)
        left_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(left_label)
        row.addWidget(left, 1)
        row.addWidget(right_label)
        row.addWidget(right, 1)
        return widget

    def _make_remove_style_button(self, hint: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("removeStyleButton")
        button.setText("X")
        button.setFixedSize(24, 26)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_hover_hint(button, hint)
        return button

    def _trailing_action_row(self, button: QToolButton) -> QWidget:
        widget = QWidget(self)
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(button)
        return widget

    def _add_style_row(self, form: QFormLayout, key: str, widget: QWidget, hint: str):
        card = QWidget(self.style_panel)
        card.setObjectName("richTextStyleRow")
        card.setMinimumHeight(34)
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(4, 3, 4, 3)
        card_layout.setSpacing(3)
        label = CaptionLabel("当前文本")
        label.setObjectName("selectedTextLabel")
        label.setFixedWidth(46)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 行内只显示协议前缀，完整名称放在悬停提示中。
        style_label = CaptionLabel(key)
        style_label.setObjectName("styleNameLabel")
        style_label.setFixedWidth(24 if len(key) <= 2 else 30)
        style_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        divider = QWidget(card)
        divider.setObjectName("styleRowDivider")
        divider.setFixedWidth(1)
        divider.setMinimumHeight(22)
        set_hover_hint(label, hint)
        set_hover_hint(style_label, f"{key}：{hint}")
        set_hover_hint(widget, f"{key}：{hint}")
        remove_button = self._make_remove_style_button(f"移除 {key} {hint}")
        remove_button.clicked.connect(lambda _checked=False, row_key=key: self._remove_style_by_row(row_key))
        card_layout.addWidget(label)
        card_layout.addWidget(divider)
        card_layout.addWidget(style_label)
        card_layout.addWidget(widget, 1)
        card_layout.addWidget(remove_button)
        card_layout.setStretch(3, 1)
        card_layout.setStretch(4, 0)
        form.addRow(card)
        self._style_rows[key] = (label, widget)
        self._style_row_cards[key] = card
        self._style_remove_buttons[key] = remove_button
        self._style_row_hints[key] = hint

    def _set_style_row_visible(self, key: str, visible: bool):
        if visible and key in self._UNRENDERED_STYLE_ROW_KEYS:
            visible = False  # 渲染未实现，暂不开放（F18）
        card = self._style_row_cards.get(key)
        if card is None:
            return
        card.setVisible(bool(visible))

    def _hide_all_style_rows(self):
        for key in self._style_rows:
            self._set_style_row_visible(key, False)

    def _update_style_panel_visibility(self):
        visible_count = sum(not card.isHidden() for card in self._style_row_cards.values())
        if visible_count <= 0:
            self.style_scroll.hide()
        else:
            # 少量样式紧贴内容，多量样式限制高度后使用 Fluent 风格细滚动条。
            self.style_scroll.setFixedHeight(min(245, 38 + visible_count * 44))
            self.style_scroll.show()

        # 浮动编辑器是 viewport 的手动定位子控件，不会像普通顶层窗口一样
        # 自动采用新的 sizeHint。显式调整自身高度，新增的样式区只会向下
        # 扩展，不再压缩文本框和工具栏、把按钮顶上去。
        current_layout = self.layout()
        if current_layout is not None:
            current_layout.activate()
        target_height = max(self.minimumHeight(), int(self.sizeHint().height()))
        if self.height() != target_height:
            self.resize(self.width(), target_height)
            QTimer.singleShot(0, self.layout_size_changed.emit)

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
        start, end = self._selected_range()
        if start == end and checked:
            self._refresh_controls()
            return
        self._set_style_row_visible("F", checked)
        if checked:
            font_family = self.font_combo.currentFont().family()
            if font_family:
                self._apply_style({"fontFamily": str(font_family)})
            return
        self._apply_style({"fontFamily": None}, allow_empty_range=True)

    def _apply_toggle(self, key: str, checked: bool):
        self._apply_style(
            {key: True if checked else None},
            allow_empty_range=not checked,
        )

    def _apply_transform_toggle(self, key: str, checked: bool):
        self._apply_style(
            {"transform": {key: True if checked else None}},
            allow_empty_range=not checked,
        )

    def _toggle_row_and_apply(self, row_key: str, checked: bool, patch: dict):
        if self._updating:
            return
        self._set_style_row_visible(row_key, checked)
        if checked:
            self._apply_style(patch)
        else:
            self._apply_style(
                self._clear_patch_for_row(row_key),
                allow_empty_range=True,
            )

    def _toggle_ruby_row(self, checked: bool):
        if self._updating:
            return
        self._set_style_row_visible("R", checked)
        if checked:
            start, end = self._selected_range()
            if start == end:
                self.ruby_button.setChecked(False)
                self._set_style_row_visible("R", False)
                return
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
            self._refresh_controls()
            return
        self._document = apply_tcy_to_range(self._document, start, end)
        self._emit_document_changed()
        self._refresh_controls()

    def _toggle_tcy(self, checked: bool):
        """纵中横按钮按选中状态执行添加或取消。"""
        if checked:
            self._apply_tcy()
        else:
            self._remove_tcy()

    def _remove_tcy(self):
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        # 无选区时工具栏按全文覆盖率显示状态；若全文都是 TCY，T 会保持
        # 选中。remove_tcy_from_range 对空范围已有“扩展到全文”的语义，不能
        # 在这里提前返回，否则蓝色 T 按钮无法再次点击取消。
        self._document = remove_tcy_from_range(self._document, start, end)
        self._emit_document_changed()
        self._refresh_controls()

    def _remove_style_by_row(self, row_key: str):
        """行尾 X 的统一删除入口。

        有选区时只移除选区；无选区时该行代表全文样式汇总，因此 X 移除
        全文中的这一种样式。普通参数控件仍禁止无选区写入。
        """
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        if row_key == "T":
            self._document = remove_tcy_from_range(self._document, start, end)
        elif row_key == "R":
            self._document = remove_ruby_from_range(self._document, start, end)
        else:
            patch = self._clear_patch_for_row(row_key)
            if not patch:
                return
            self._document = apply_style_to_range(self._document, start, end, patch)
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

    def _apply_style(self, patch: dict, *, allow_empty_range: bool = False):
        if self._updating or self._region_index < 0:
            return
        start, end = self._selected_range()
        # 逐字样式必须有明确选区。旧逻辑把零长度选区扩展为全文，光标
        # 偶尔丢失选区时就会把一个字的参数写到其他字，造成“串样式”。
        if start == end and not allow_empty_range:
            self._refresh_controls()
            return
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
        # 无具体文字选区时展示全文已经使用的样式，方便查看“哪些文字用了
        # 哪些样式”；写入仍由 _apply_style 的非空选区保护负责，展示全文
        # 不代表后续操作会修改全文。
        style = style_for_range(self._document, start, end)
        coverage = {
            key: style_row_coverage(self._document, start, end, key)
            for key in ("B", "I", "C", "S", "%", "F", "O", "G", "OS", "D", "T", "R", "Rot", "K", "PK", "LK", "NK", "XY", "M", "MV")
        }
        self._updating = True
        try:
            has_bold, all_bold = coverage["B"]
            self.bold_button.setChecked(all_bold)
            self._set_style_row_visible("B", has_bold)
            has_italic, all_italic = coverage["I"]
            self._set_button_and_row(self.italic_button, "I", has_italic, all_italic)
            if has_italic:
                italic_value = style.get("italic")
                # 旧文档的 italic: true 显示为参考默认角度 15（渲染层同口径）
                self.italic_angle_input.setValue(15.0 if isinstance(italic_value, bool) else float(italic_value))
            else:
                self.italic_angle_input.setValue(15.0)
            has_emphasis, all_emphasis = coverage["D"]
            self.emphasis_button.setChecked(all_emphasis)
            self._set_style_row_visible("D", has_emphasis)
            has_tcy, all_tcy = coverage["T"]
            self.tcy_button.setChecked(all_tcy)
            self._set_style_row_visible("T", has_tcy)
            self.no_tcy_button.setChecked(bool(style.get("noTcy")))
            has_mirror_x, all_mirror_x = coverage["M"]
            has_mirror_y, all_mirror_y = coverage["MV"]
            self.mirror_x_button.setChecked(all_mirror_x)
            self.mirror_y_button.setChecked(all_mirror_y)
            self._set_style_row_visible("M", has_mirror_x)
            self._set_style_row_visible("MV", has_mirror_y)
            has_ruby, all_ruby = coverage["R"]
            self.ruby_button.setChecked(all_ruby)
            if "rubyText" in style:
                self.ruby_text_input.setText(str(style.get("rubyText") or ""))
            elif not self.ruby_text_input.hasFocus():
                self.ruby_text_input.clear()

            self._set_button_and_row(self.color_button, "C", *coverage["C"])
            self.color_picker.set_color(str(style.get("color") or "#E53935"))

            self._set_button_and_row(self.size_button, "S", *coverage["S"])
            self.font_size_input.setValue(int(style.get("fontSize") or 24))

            self._set_button_and_row(self.scale_button, "%", *coverage["%"])
            self.scale_input.setValue(float(style.get("scale") if "scale" in style else 1.20))

            self._set_button_and_row(self.font_button, "F", *coverage["F"])
            self._set_font_combo_value(str(style.get("fontFamily") or ""))

            has_stroke = "strokeColor" in style or "strokeWidth" in style
            self._set_button_and_row(self.stroke_button, "O", *coverage["O"])
            self.stroke_color_picker.set_color(str(style.get("strokeColor") or "#ffffff"))
            self.stroke_width_input.setValue(float(style.get("strokeWidth") if "strokeWidth" in style else 0.07))

            has_glow = "glowColor" in style or "glowBlur" in style
            self._set_button_and_row(self.glow_button, "G", *coverage["G"])
            self.glow_color_picker.set_color(str(style.get("glowColor") or "#00ffff"))
            self.glow_blur_input.setValue(float(style.get("glowBlur") if "glowBlur" in style else 0.10))

            has_outer_stroke = "outerStrokeColor" in style or "outerStrokeWidth" in style
            self._set_button_and_row(self.outer_stroke_button, "OS", *coverage["OS"])
            self.outer_stroke_color_picker.set_color(str(style.get("outerStrokeColor") or "#000000"))
            self.outer_stroke_width_input.setValue(float(style.get("outerStrokeWidth") if "outerStrokeWidth" in style else 0.20))

            self._set_button_and_row(self.rotation_button, "Rot", *coverage["Rot"])
            self.rotation_input.setValue(float(style.get("rotation") or 0.0))

            self._set_button_and_row(self.kerning_button, "K", *coverage["K"])
            self.kerning_input.setValue(float(style.get("kerning") or 0.0))

            self._set_button_and_row(self.pre_kerning_button, "PK", *coverage["PK"])
            self.pre_kerning_input.setValue(float(style.get("preKerning") or 0.0))

            self._set_button_and_row(self.line_kerning_button, "LK", *coverage["LK"])
            self.line_kerning_input.setValue(float(style.get("lineKerning") or 0.0))
            self._set_button_and_row(self.next_kerning_button, "NK", *coverage["NK"])
            self.next_kerning_input.setValue(float(style.get("nextKerning") or 0.0))

            has_offset = "offsetX" in style or "offsetY" in style
            self._set_button_and_row(self.offset_button, "XY", *coverage["XY"])
            # 选区切换时必须完整加载该选区自己的 XY。之前仅在字段存在时
            # 更新输入框，导致无偏移字符沿用上一字符残留的数值；再次开启
            # XY 时就会把上一字符的偏移复制过来。缺失轴明确归零，两个字
            # 的编辑状态由各自 style 决定，互不串值。
            self.offset_x_input.setValue(float(style.get("offsetX") or 0.0))
            self.offset_y_input.setValue(float(style.get("offsetY") or 0.0))

            self._set_style_row_visible("R", has_ruby)
            self._refresh_style_row_labels()
            self._update_style_panel_visibility()
        finally:
            self._updating = False

    def _set_button_and_row(self, button: QToolButton, row_key: str, present: bool, fully_applied: bool):
        button.setChecked(bool(fully_applied))
        self._set_style_row_visible(row_key, bool(present))

    def _refresh_style_row_labels(self):
        start, end = self._selected_range()
        for key, (label, widget) in self._style_rows.items():
            card = self._style_row_cards.get(key)
            if card is None or card.isHidden():
                continue
            style_text = styled_text_for_key(self._document, start, end, key) or "当前文本"
            hint = self._style_row_hints.get(key, key)
            label.setText(style_text)
            set_hover_hint(label, f"选中文字：{style_text}；样式：{key} {hint}")

    def _stroke_patch(self) -> dict:
        return {"stroke": {"color": self.stroke_color_picker.get_color(), "width": float(self.stroke_width_input.value())}}

    def _glow_patch(self) -> dict:
        return {"glow": {"color": self.glow_color_picker.get_color(), "blur": float(self.glow_blur_input.value())}}

    def _outer_stroke_patch(self) -> dict:
        return {"outerStroke": {"color": self.outer_stroke_color_picker.get_color(), "width": float(self.outer_stroke_width_input.value())}}

    def _offset_patch(self) -> dict:
        return {
            "transform": {
                "offsetX": float(self.offset_x_input.value()),
                "offsetY": float(self.offset_y_input.value()),
            }
        }

    def _clear_patch_for_row(self, row_key: str) -> dict:
        return {
            "B": {"bold": None},
            "C": {"color": None},
            "I": {"italic": None},
            "S": {"fontSize": None},
            "%": {"scale": None},
            "F": {"fontFamily": None},
            "O": {"stroke": None},
            "G": {"glow": None},
            "OS": {"outerStroke": None},
            "D": {"emphasis": None},
            "Rot": {"transform": {"rotation": None}},
            "K": {"kerning": None},
            "PK": {"preKerning": None},
            "LK": {"lineKerning": None},
            "NK": {"nextKerning": None},
            "XY": {"transform": {"offsetX": None, "offsetY": None}},
            "M": {"transform": {"mirrorX": None}},
            "MV": {"transform": {"mirrorY": None}},
        }.get(row_key, {})
