import logging

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QFormLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    CompactDoubleSpinBox,
    CompactSpinBox,
    PopUpAniStackedWidget,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SimpleCardWidget,
    Slider,
    StrongBodyLabel,
    TextEdit,
    TogglePushButton,
    ToolButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from editor.region_geometry_state import normalize_region_geometry_data
from services import get_config_service, get_i18n_manager

# from .collapsible_frame import CollapsibleFrame  # 不再使用折叠框
from ui.secondary_pages.themed_text_input_dialog import themed_get_text
from utils.font_list import FontComboBox

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint
from .sidebar import FluentScrollArea
from .wheel_filter import TopLevelComboBox as ComboBox
from .wheel_filter import install_wheel_filter

logger = logging.getLogger("manga_translator")


class PanelSettingCardGroup(QWidget):
    """Fluent card group for complex editor forms."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.titleLabel = StrongBodyLabel(title, self)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(8)
        self.vBoxLayout.addWidget(self.titleLabel)
        self._card: CardWidget | None = None
        self._syncing_height = False

    def addSettingCard(self, card):
        card.setParent(self)
        self._card = card
        self.vBoxLayout.addWidget(card)
        QTimer.singleShot(0, self.sync_content_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.sync_content_height)

    def sync_content_height(self):
        if self._syncing_height:
            return
        if self._card is None:
            return

        card_layout = self._card.layout()
        card_height = (
            card_layout.totalSizeHint().height()
            if card_layout is not None
            else self._card.sizeHint().height()
        )
        title_height = self.titleLabel.sizeHint().height()
        height = max(title_height + card_height + self.vBoxLayout.spacing(), 1)
        if (
            height == self.height()
            and height == self.minimumHeight()
            and height == self.maximumHeight()
        ):
            return
        self._syncing_height = True
        try:
            self.setFixedHeight(height)
            self.updateGeometry()
        finally:
            self._syncing_height = False


def strip_legacy_horizontal_tags(text: str) -> str:
    """剥除已废除的 <H>...</H> 局部横排标记（保留内文）。

    渲染管线已删除全部 <H> 消费方，字面标记会被当普通字符画上成品图；
    局部横排改用富文本 tcy（旧 <H> 协议已废除，⇄→<H> 生产链已随
    mark_horizontal_button 一并移除）。
    """
    if "<H>" not in text and "</H>" not in text:
        return text
    return text.replace("<H>", "").replace("</H>", "")


class CustomSlider(Slider):
    """自定义滑块：持焦点时滚轮一格步进 1；无焦点时滚轮直通父级滚动。

    与 wheel_filter.install_wheel_filter 的约定一致：控件未获得键盘焦点时
    不改值、不 accept，让滚动区域接管滚轮事件。
    """

    def wheelEvent(self, event: QWheelEvent):
        if not self.hasFocus():
            event.ignore()
            return

        delta = event.angleDelta().y()
        if delta > 0:
            self.setValue(self.value() + 1)
        elif delta < 0:
            self.setValue(self.value() - 1)
        event.accept()


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


class PropertyPanel(QWidget):
    """
    左侧属性面板，功能完整版。
    """

    MASK_ROUTE = "property_mask_page"
    PAINT_ROUTE = "property_paint_page"
    STAMP_ROUTE = "property_stamp_page"

    # --- Define all required signals ---
    # 第三个参数是编辑操作记录 {'ops': [[pos, removed, inserted], ...],
    # 'pre_text': str, 'post_text': str}(\n 口径),供富文本样式同步用
    translated_text_modified = pyqtSignal(int, str, object)
    translation_raw_modified = pyqtSignal(int, str, object)
    original_text_modified = pyqtSignal(int, str)
    ocr_requested = pyqtSignal()
    translation_requested = pyqtSignal()
    font_size_changed = pyqtSignal(int, int)
    font_color_changed = pyqtSignal(int, str)
    stroke_color_changed = pyqtSignal(int, str)
    stroke_width_changed = pyqtSignal(int, float)
    line_spacing_changed = pyqtSignal(int, float)
    letter_spacing_changed = pyqtSignal(int, float)
    angle_changed = pyqtSignal(int, float)
    font_family_changed = pyqtSignal(int, str)  # New signal for font family
    alignment_changed = pyqtSignal(int, str)
    direction_changed = pyqtSignal(int, str)
    style_patch_requested = pyqtSignal(list, dict)
    font_family_preview_requested = pyqtSignal(list, str)
    copy_region_requested = pyqtSignal()
    paste_region_requested = pyqtSignal()
    delete_region_requested = pyqtSignal()

    # Mask signals
    mask_tool_changed = pyqtSignal(str)
    brush_size_changed = pyqtSignal(int)
    toggle_mask_visibility = pyqtSignal(bool)
    clear_all_masks_requested = pyqtSignal()
    # Paint overlay signals
    brush_color_changed = pyqtSignal(str)
    clear_paint_overlay_requested = pyqtSignal()
    clear_stamp_overlay_requested = pyqtSignal()
    paint_overlay_visibility_changed = pyqtSignal(bool)
    stamp_overlay_visibility_changed = pyqtSignal(bool)

    def __init__(self, model, app_logic, parent=None):
        super().__init__(parent)
        self.model = model
        self.app_logic = app_logic
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()
        self.scroll_area: FluentScrollArea | None = None
        self.content_widget: QWidget | None = None
        self.paint_segmented_widget: SegmentedWidget | None = None
        self.paint_stack: PopUpAniStackedWidget | None = None
        self._paint_route_indexes: dict[str, int] = {}
        self._updating_paint_route = False

        # 译文框编辑操作记录器(采集/收窄逻辑在后端 text_edit_ops)
        from manga_translator.utils.text_edit_ops import EditOpRecorder

        self._translation_edit_recorder = EditOpRecorder()

        self._init_ui()
        self._connect_signals()
        self._connect_model_signals()  # Connect to model signals
        self.block_updates = False
        self.current_region_index = -1
        self.clear_and_disable_selection_dependent()

    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _init_ui(self):
        # 创建主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(main_layout)

        # 创建滚动区域
        scroll_area = FluentScrollArea()
        self.scroll_area = scroll_area

        # 创建内容容器
        content_widget = QWidget(scroll_area)
        self.content_widget = content_widget
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(10)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._create_mask_edit_section(content_layout)
        self._create_text_section(content_layout)
        self._create_style_section(content_layout)
        self._create_action_section(content_layout)

        # 添加一个弹性空间，将所有内容向上推，使布局更紧凑
        content_layout.addStretch()

        # 将内容容器放入滚动区域
        scroll_area.setWidget(content_widget)
        scroll_area.enableTransparentBackground()
        main_layout.addWidget(scroll_area)
        # 统一滚轮语义：无焦点的滑块/数值框/下拉框不吞滚轮，事件直通滚动区域
        self._wheel_filter = install_wheel_filter(self)
        self.sync_sidebar_layout()

        # 不再使用语法高亮器,改用符号替换
        # self.highlighter = HorizontalTagHighlighter(self.translated_text_box.document())

    def _make_group(self, title: str) -> tuple[PanelSettingCardGroup, CardWidget]:
        group = PanelSettingCardGroup(title, self)
        card = SimpleCardWidget(group)
        return group, card

    def _finish_group(self, group: PanelSettingCardGroup, card: CardWidget):
        group.addSettingCard(card)
        if hasattr(group, "sync_content_height"):
            QTimer.singleShot(0, group.sync_content_height)
        QTimer.singleShot(0, self.sync_sidebar_layout)

    def sync_sidebar_layout(self):
        for group in self.findChildren(PanelSettingCardGroup):
            group.sync_content_height()
        if self.scroll_area is not None:
            self.scroll_area.sync_layout()

    def _set_group_title(self, group: PanelSettingCardGroup, title: str):
        group.titleLabel.setText(title)
        group.titleLabel.adjustSize()
        group.sync_content_height()

    def _set_selection_controls_blocked(self, blocked: bool):
        """统一阻止/恢复与区域样式相关控件信号，避免切换选区时误写回。"""
        for child in self.findChildren(QWidget):
            if isinstance(child, (TextEdit, ComboBox, Slider, QAbstractSpinBox)):
                child.blockSignals(blocked)

    @staticmethod
    def _repopulate_combo(combo, items, *, current_text=None, current_index=None):
        """clear+addItems 的统一入口：全程 blockSignals，并恢复选中项。

        重新填充是纯 UI 刷新，绝不能触发 currentTextChanged/currentIndexChanged
        把「变成第一项」当成用户操作写回所有选中 region。

        Args:
            combo: 目标下拉框
            items: 新选项列表
            current_text: 优先按文本恢复选中；None 时保持原选中文本（仍存在才恢复）
            current_index: 按索引恢复选中（用于语言切换后文本变化的场景）
        """
        items = list(items)
        if current_text is None and current_index is None:
            current_text = combo.currentText()

        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(items)
            if current_index is not None:
                if 0 <= current_index < combo.count():
                    combo.setCurrentIndex(current_index)
            elif current_text and current_text in items:
                combo.setCurrentText(current_text)
        finally:
            combo.blockSignals(False)

    def _create_mask_edit_section(self, layout):
        self.mask_edit_frame, mask_card = self._make_group(self._t("Image Editing"))
        frame_layout = QVBoxLayout(mask_card)
        frame_layout.setContentsMargins(6, 8, 6, 6)
        frame_layout.setSpacing(6)

        self.paint_segmented_widget = SegmentedWidget(mask_card)
        self.paint_segmented_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.paint_stack = PopUpAniStackedWidget(mask_card)
        self.paint_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # 选择按钮组（两个 tab 共享同一个按钮组，保持互斥）
        self.mask_tool_group = QButtonGroup(self)
        self.mask_tool_group.setExclusive(True)

        # ======= Tab 1：蒙版 =======
        mask_tab = SimpleCardWidget(self.paint_stack)
        mask_layout = QVBoxLayout(mask_tab)
        mask_layout.setContentsMargins(6, 8, 6, 6)
        mask_layout.setSpacing(8)

        mask_tools_layout = QHBoxLayout()
        mask_tools_layout.setContentsMargins(0, 0, 0, 0)
        mask_tools_layout.setSpacing(6)

        self.brush_button = TogglePushButton()
        self.brush_button.setText(self._t("Brush"))
        self.brush_button.setIcon(FIF.BRUSH)
        set_hover_hint(self.brush_button, self._t("Brush Tool") + " (W)")
        self.eraser_button = TogglePushButton()
        self.eraser_button.setText(self._t("Eraser"))
        self.eraser_button.setIcon(FIF.ERASE_TOOL)
        set_hover_hint(self.eraser_button, self._t("Eraser Tool") + " (E)")
        self.select_button = TogglePushButton()
        self.select_button.setText(self._t("No Selection"))
        self.select_button.setIcon(FIF.CLEAR_SELECTION)
        set_hover_hint(self.select_button, self._t("Selection Tool") + " (Q)")

        self.mask_tool_group.addButton(self.select_button, 0)
        self.mask_tool_group.addButton(self.brush_button, 1)
        self.mask_tool_group.addButton(self.eraser_button, 2)
        self.select_button.setChecked(True)
        for button in (self.select_button, self.brush_button, self.eraser_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        mask_tools_layout.addWidget(self.select_button)
        mask_tools_layout.addWidget(self.brush_button)
        mask_tools_layout.addWidget(self.eraser_button)
        mask_layout.addLayout(mask_tools_layout)

        brush_size_layout = QHBoxLayout()
        brush_size_layout.setContentsMargins(0, 0, 0, 0)
        brush_size_layout.setSpacing(6)
        self.brush_size_title_label = BodyLabel(self._t("Brush Size:"))
        brush_size_layout.addWidget(self.brush_size_title_label)
        self.brush_size_slider = Slider(Qt.Orientation.Horizontal)
        self.brush_size_slider.setRange(5, 200)
        self.brush_size_value_label = CaptionLabel("30")
        self.brush_size_value_label.setFixedWidth(28)
        self.brush_size_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.brush_size_slider.setValue(30)
        brush_size_layout.addWidget(self.brush_size_slider)
        brush_size_layout.addWidget(self.brush_size_value_label)
        mask_layout.addLayout(brush_size_layout)

        self.show_refined_mask_checkbox = CheckBox(self._t("Show Refined Mask"))
        self.show_refined_mask_checkbox.setChecked(False)
        mask_layout.addWidget(self.show_refined_mask_checkbox)

        self.clear_all_masks_button = PushButton()
        self.clear_all_masks_button.setText(self._t("Clear All Masks"))
        self.clear_all_masks_button.setIcon(FIF.BROOM)
        mask_layout.addWidget(self.clear_all_masks_button)
        mask_layout.addStretch()

        # ======= Tab 2：画笔（彩色涂鸦） =======
        paint_tab = SimpleCardWidget(self.paint_stack)
        paint_layout = QVBoxLayout(paint_tab)
        paint_layout.setContentsMargins(6, 8, 6, 6)
        paint_layout.setSpacing(8)

        paint_tools_layout = QHBoxLayout()
        paint_tools_layout.setContentsMargins(0, 0, 0, 0)
        paint_tools_layout.setSpacing(6)

        self.paint_select_button = TogglePushButton()
        self.paint_select_button.setText(self._t("No Selection"))
        self.paint_select_button.setIcon(FIF.CLEAR_SELECTION)
        set_hover_hint(self.paint_select_button, self._t("Selection Tool") + " (Q)")

        self.paint_brush_button = TogglePushButton()
        self.paint_brush_button.setText(self._t("Brush"))
        self.paint_brush_button.setIcon(FIF.BRUSH)
        set_hover_hint(self.paint_brush_button, self._t("Brush Tool") + " (W)")

        self.paint_eraser_button = TogglePushButton()
        self.paint_eraser_button.setText(self._t("Eraser"))
        self.paint_eraser_button.setIcon(FIF.ERASE_TOOL)
        set_hover_hint(self.paint_eraser_button, self._t("Eraser Tool") + " (E)")

        # 复用同一个互斥按钮组，保证和蒙版页工具互相切换时正确取消选中
        self.mask_tool_group.addButton(self.paint_select_button, 3)
        self.mask_tool_group.addButton(self.paint_brush_button, 4)
        self.mask_tool_group.addButton(self.paint_eraser_button, 5)
        for button in (
            self.paint_select_button,
            self.paint_brush_button,
            self.paint_eraser_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        paint_tools_layout.addWidget(self.paint_select_button)
        paint_tools_layout.addWidget(self.paint_brush_button)
        paint_tools_layout.addWidget(self.paint_eraser_button)
        paint_layout.addLayout(paint_tools_layout)

        # 画笔大小（与蒙版页共享同一个模型字段）
        paint_size_layout = QHBoxLayout()
        paint_size_layout.setContentsMargins(0, 0, 0, 0)
        paint_size_layout.setSpacing(6)
        self.paint_size_title_label = BodyLabel(self._t("Brush Size:"))
        paint_size_layout.addWidget(self.paint_size_title_label)
        self.paint_size_slider = Slider(Qt.Orientation.Horizontal)
        self.paint_size_slider.setRange(5, 200)
        self.paint_size_slider.setValue(30)
        self.paint_size_value_label = CaptionLabel("30")
        self.paint_size_value_label.setFixedWidth(28)
        self.paint_size_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        paint_size_layout.addWidget(self.paint_size_slider)
        paint_size_layout.addWidget(self.paint_size_value_label)
        paint_layout.addLayout(paint_size_layout)

        # 画笔颜色（复用 ColorPickerWidget）
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self.paint_color_label = BodyLabel(self._t("Brush Color:"))
        color_row.addWidget(self.paint_color_label)
        self.paint_color_picker = ColorPickerWidget(
            dialog_title="Select brush color",
            default_color="#ffffff",
            config_key="saved_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        color_row.addWidget(self.paint_color_picker)
        color_row.addStretch()
        paint_layout.addLayout(color_row)

        self.show_paint_overlay_checkbox = CheckBox(self._t("Show Paint Layer"))
        self.show_paint_overlay_checkbox.setChecked(True)
        paint_layout.addWidget(self.show_paint_overlay_checkbox)

        self.clear_paint_overlay_button = PushButton()
        self.clear_paint_overlay_button.setText(self._t("Clear Paint Layer"))
        self.clear_paint_overlay_button.setIcon(FIF.BROOM)
        paint_layout.addWidget(self.clear_paint_overlay_button)
        paint_layout.addStretch()

        # ======= Tab 3：印章（仿制印章，与画笔页结构一致） =======
        stamp_tab = SimpleCardWidget(self.paint_stack)
        stamp_layout = QVBoxLayout(stamp_tab)
        stamp_layout.setContentsMargins(6, 8, 6, 6)
        stamp_layout.setSpacing(8)

        stamp_tools_layout = QHBoxLayout()
        stamp_tools_layout.setContentsMargins(0, 0, 0, 0)
        stamp_tools_layout.setSpacing(6)

        self.stamp_select_button = TogglePushButton()
        self.stamp_select_button.setText(self._t("No Selection"))
        self.stamp_select_button.setIcon(FIF.CLEAR_SELECTION)
        set_hover_hint(self.stamp_select_button, self._t("Selection Tool") + " (Q)")

        self.paint_clone_button = TogglePushButton()
        self.paint_clone_button.setText(self._t("Clone Stamp"))
        self.paint_clone_button.setIcon(FIF.COPY)
        set_hover_hint(self.paint_clone_button, self._t("Clone Stamp Hint") + " (W)")

        self.stamp_eraser_button = TogglePushButton()
        self.stamp_eraser_button.setText(self._t("Eraser"))
        self.stamp_eraser_button.setIcon(FIF.ERASE_TOOL)
        set_hover_hint(self.stamp_eraser_button, self._t("Eraser Tool") + " (E)")

        self.mask_tool_group.addButton(self.stamp_select_button, 6)
        self.mask_tool_group.addButton(self.paint_clone_button, 7)
        self.mask_tool_group.addButton(self.stamp_eraser_button, 8)
        for button in (
            self.stamp_select_button,
            self.paint_clone_button,
            self.stamp_eraser_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        stamp_tools_layout.addWidget(self.stamp_select_button)
        stamp_tools_layout.addWidget(self.paint_clone_button)
        stamp_tools_layout.addWidget(self.stamp_eraser_button)
        stamp_layout.addLayout(stamp_tools_layout)

        # 印章大小（与蒙版/画笔页共享同一个模型字段）
        stamp_size_layout = QHBoxLayout()
        stamp_size_layout.setContentsMargins(0, 0, 0, 0)
        stamp_size_layout.setSpacing(6)
        self.stamp_size_title_label = BodyLabel(self._t("Brush Size:"))
        stamp_size_layout.addWidget(self.stamp_size_title_label)
        self.stamp_size_slider = Slider(Qt.Orientation.Horizontal)
        self.stamp_size_slider.setRange(5, 200)
        self.stamp_size_slider.setValue(30)
        self.stamp_size_value_label = CaptionLabel("30")
        self.stamp_size_value_label.setFixedWidth(28)
        self.stamp_size_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        stamp_size_layout.addWidget(self.stamp_size_slider)
        stamp_size_layout.addWidget(self.stamp_size_value_label)
        stamp_layout.addLayout(stamp_size_layout)

        self.show_stamp_overlay_checkbox = CheckBox(self._t("Show Stamp Layer"))
        self.show_stamp_overlay_checkbox.setChecked(True)
        stamp_layout.addWidget(self.show_stamp_overlay_checkbox)

        self.clear_stamp_overlay_button = PushButton()
        self.clear_stamp_overlay_button.setText(self._t("Clear Stamp Layer"))
        self.clear_stamp_overlay_button.setIcon(FIF.BROOM)
        stamp_layout.addWidget(self.clear_stamp_overlay_button)
        stamp_layout.addStretch()

        self._add_paint_page(self.MASK_ROUTE, mask_tab, self._t("Mask"))
        self._add_paint_page(self.PAINT_ROUTE, paint_tab, self._t("Paint"))
        self._add_paint_page(self.STAMP_ROUTE, stamp_tab, self._t("Clone Stamp"))
        self._set_paint_route(self.MASK_ROUTE, emit_changed=False)
        frame_layout.addWidget(self.paint_segmented_widget)
        frame_layout.addWidget(self.paint_stack)
        self._finish_group(self.mask_edit_frame, mask_card)
        layout.addWidget(self.mask_edit_frame)

    def _add_paint_page(self, route_key: str, widget: QWidget, text: str):
        if self.paint_stack is None or self.paint_segmented_widget is None:
            return

        index = self.paint_stack.count()
        self.paint_stack.addWidget(widget)
        self._paint_route_indexes[route_key] = index
        self.paint_segmented_widget.addItem(route_key, text)
        shortcut_hints = {
            self.MASK_ROUTE: self._t("Mask") + " (1)",
            self.PAINT_ROUTE: self._t("Paint") + " (2)",
            self.STAMP_ROUTE: self._t("Clone Stamp") + " (3)",
        }
        set_hover_hint(
            self.paint_segmented_widget.items[route_key],
            shortcut_hints[route_key],
        )

    def _set_paint_route(self, route_key: str, emit_changed: bool = True):
        if self.paint_stack is None or self.paint_segmented_widget is None:
            return

        index = self._paint_route_indexes.get(route_key)
        if index is None:
            return

        previous = self.paint_stack.currentIndex()
        self._updating_paint_route = True
        try:
            self.paint_stack.setCurrentIndex(index)
            self.paint_segmented_widget.setCurrentItem(route_key)
        finally:
            self._updating_paint_route = False

        if emit_changed and previous != index:
            self._on_paint_tab_changed(index)
        self.sync_sidebar_layout()

    def _on_paint_route_changed(self, route_key: str):
        if self._updating_paint_route or self.paint_stack is None:
            return

        index = self._paint_route_indexes.get(route_key)
        if index is None:
            return

        previous = self.paint_stack.currentIndex()
        self.paint_stack.setCurrentIndex(index)
        if previous != index:
            self._on_paint_tab_changed(index)
        self.sync_sidebar_layout()

    def _paint_current_index(self) -> int:
        if self.paint_stack is None:
            return 0
        return self.paint_stack.currentIndex()

    def _paint_route_for_index(self, index: int) -> str:
        return {1: self.PAINT_ROUTE, 2: self.STAMP_ROUTE}.get(index, self.MASK_ROUTE)

    def activate_image_edit_tab(self, index: int):
        """切换图像编辑页；页签切换沿用现有逻辑回到该页的“不选择”。"""
        if index not in (0, 1, 2):
            return
        self._set_paint_route(self._paint_route_for_index(index))

    def activate_image_edit_tool(self, position: int):
        """按当前图像编辑页激活第 position 个工具按钮。"""
        page_buttons = (
            (self.select_button, self.brush_button, self.eraser_button),
            (
                self.paint_select_button,
                self.paint_brush_button,
                self.paint_eraser_button,
            ),
            (
                self.stamp_select_button,
                self.paint_clone_button,
                self.stamp_eraser_button,
            ),
        )
        if position not in (0, 1, 2):
            return
        page_buttons[self._paint_current_index()][position].click()

    def _create_text_section(self, layout):
        self.text_edit_frame, text_card = self._make_group(self._t("Text Content"))
        text_layout = QVBoxLayout(text_card)
        text_layout.setContentsMargins(8, 8, 8, 6)
        text_layout.setSpacing(8)
        ocr_trans_config_layout = QFormLayout()
        ocr_trans_config_layout.setContentsMargins(0, 0, 0, 0)
        ocr_trans_config_layout.setHorizontalSpacing(8)
        ocr_trans_config_layout.setVerticalSpacing(8)
        ocr_trans_config_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.ocr_model_combo = ComboBox()
        self.ocr_model_combo.setMinimumWidth(110)
        self.translator_combo = ComboBox()
        self.translator_combo.setMinimumWidth(110)
        self.target_language_combo = ComboBox()
        self.target_language_combo.setMinimumWidth(110)
        ocr_row = QHBoxLayout()
        ocr_row.setContentsMargins(0, 0, 0, 0)
        ocr_row.setSpacing(6)
        ocr_row.addWidget(self.ocr_model_combo)
        self.ocr_button = PushButton()
        self.ocr_button.setText(self._t("Recognize"))
        self.ocr_button.setIcon(FIF.ROBOT)
        self.ocr_button.setMinimumWidth(72)
        self.ocr_button.setMaximumWidth(92)
        ocr_row.addWidget(self.ocr_button)
        translator_row = QHBoxLayout()
        translator_row.setContentsMargins(0, 0, 0, 0)
        translator_row.setSpacing(6)
        translator_row.addWidget(self.translator_combo)
        self.translate_button = PrimaryPushButton()
        self.translate_button.setText(self._t("Translate"))
        self.translate_button.setIcon(FIF.LANGUAGE)
        self.translate_button.setMinimumWidth(80)
        self.translate_button.setMaximumWidth(108)
        translator_row.addWidget(self.translate_button)
        self.ocr_model_row_label = BodyLabel(self._t("OCR Model:"))
        self.translator_row_label = BodyLabel(self._t("Translator:"))
        self.target_lang_row_label = BodyLabel(self._t("Target Language:"))
        ocr_trans_config_layout.addRow(self.ocr_model_row_label, ocr_row)
        ocr_trans_config_layout.addRow(self.translator_row_label, translator_row)
        ocr_trans_config_layout.addRow(
            self.target_lang_row_label, self.target_language_combo
        )
        text_layout.addLayout(ocr_trans_config_layout)

        # 原文文本框
        self.original_text_box = TextEdit()
        self.original_text_box.setUndoRedoEnabled(True)
        self.original_text_box.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.original_text_box.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.original_text_box.setMinimumHeight(72)
        self.original_text_box.setMaximumHeight(132)

        self.translated_text_box = TextEdit()
        self.translated_text_box.setUndoRedoEnabled(True)
        self.translated_text_box.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.translated_text_box.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.translated_text_box.setMinimumHeight(72)
        self.translated_text_box.setMaximumHeight(132)

        self.original_text_label = BodyLabel(self._t("Original Text:"))
        text_layout.addWidget(self.original_text_label)
        text_layout.addWidget(self.original_text_box)
        # 复选框:勾选时让"译文"框显示"替换前译文"(translation_raw),编辑会实时跑替换写回译文
        self.translation_raw_checkbox = CheckBox(self._t("Show Translation (Raw)"))
        self.translation_raw_checkbox.setChecked(True)
        self.translation_raw_checkbox.toggled.connect(
            self._on_translation_raw_mode_toggled
        )
        text_layout.addWidget(self.translation_raw_checkbox)
        self.translated_text_label = BodyLabel(self._t("Translated Text:"))
        text_layout.addWidget(self.translated_text_label)
        text_layout.addWidget(self.translated_text_box)
        self.text_stats_label = CaptionLabel(self._t("Character count: 0"))
        text_layout.addWidget(self.text_stats_label)
        self._finish_group(self.text_edit_frame, text_card)
        layout.addWidget(self.text_edit_frame)

    def _create_style_section(self, layout):
        self.style_edit_frame, style_card = self._make_group(self._t("Style Settings"))
        style_layout = QFormLayout(style_card)
        style_layout.setContentsMargins(8, 8, 8, 6)
        style_layout.setHorizontalSpacing(8)
        style_layout.setVerticalSpacing(8)

        preset_widget = QWidget()
        preset_layout = QHBoxLayout(preset_widget)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)
        self.style_preset_combo = ComboBox()
        self.style_preset_combo.setMinimumWidth(140)
        self.style_preset_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        preset_layout.addWidget(self.style_preset_combo, 1)
        self.save_style_preset_button = ToolButton()
        self.save_style_preset_button.setFixedSize(30, 30)
        self.save_style_preset_button.setIconSize(QSize(16, 16))
        preset_layout.addWidget(self.save_style_preset_button)
        self.delete_style_preset_button = ToolButton()
        self.delete_style_preset_button.setFixedSize(30, 30)
        self.delete_style_preset_button.setIconSize(QSize(16, 16))
        preset_layout.addWidget(self.delete_style_preset_button)
        self._refresh_style_preset_action_buttons()
        self.style_preset_label = BodyLabel(self._t("Style Preset:"))
        style_preset_row = QWidget()
        style_preset_row_layout = QHBoxLayout(style_preset_row)
        style_preset_row_layout.setContentsMargins(0, 0, 0, 0)
        style_preset_row_layout.setSpacing(8)
        style_preset_row_layout.addWidget(self.style_preset_label)
        style_preset_row_layout.addWidget(preset_widget, 1)
        style_layout.addRow(style_preset_row)
        self._refresh_style_preset_combo()

        locale_getter = self.i18n.get_current_locale if self.i18n else None
        self.font_family_combo = FontComboBox(self, locale_getter=locale_getter)
        self.font_family_combo.setMinimumWidth(120)
        self.font_label = BodyLabel(self._t("Font:"))
        style_layout.addRow(self.font_label, self.font_family_combo)

        # Font size
        self.font_size_input = _compact_spin_box()
        self.font_size_input.setRange(8, 1000)
        self.font_size_input.setKeyboardTracking(False)
        self.font_size_slider = CustomSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setRange(8, 150)
        self.font_size_label = BodyLabel(self._t("Font Size:"))
        style_layout.addRow(self.font_size_label, self.font_size_input)
        style_layout.addRow(CaptionLabel(""), self.font_size_slider)

        # Font color
        self.font_color_picker = ColorPickerWidget(
            dialog_title="Select font color",
            default_color="#000000",
            config_key="saved_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.font_color_label = BodyLabel(self._t("Font Color:"))
        style_layout.addRow(self.font_color_label, self.font_color_picker)

        # Stroke color (描边颜色)
        self.stroke_color_picker = ColorPickerWidget(
            dialog_title="Select stroke color",
            default_color="#ffffff",
            config_key="saved_stroke_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.stroke_color_label = BodyLabel(self._t("Stroke Color:"))
        style_layout.addRow(self.stroke_color_label, self.stroke_color_picker)

        # Stroke width (描边宽度)
        self.stroke_width_spinbox = _compact_double_spin_box()
        self.stroke_width_spinbox.setRange(0.0, 1.0)
        self.stroke_width_spinbox.setSingleStep(0.01)
        self.stroke_width_spinbox.setDecimals(2)
        self.stroke_width_spinbox.setValue(0.07)
        self.stroke_width_label = BodyLabel(self._t("Stroke Width:"))
        style_layout.addRow(self.stroke_width_label, self.stroke_width_spinbox)

        # Line spacing (行间距倍率)
        self.line_spacing_spinbox = _compact_double_spin_box()
        self.line_spacing_spinbox.setRange(0.1, 5.0)
        self.line_spacing_spinbox.setSingleStep(0.1)
        self.line_spacing_spinbox.setDecimals(1)
        self.line_spacing_spinbox.setValue(1.0)
        self.line_spacing_label = BodyLabel(self._t("Line Spacing:"))
        style_layout.addRow(self.line_spacing_label, self.line_spacing_spinbox)

        self.letter_spacing_spinbox = _compact_double_spin_box()
        self.letter_spacing_spinbox.setRange(0.1, 5.0)
        self.letter_spacing_spinbox.setSingleStep(0.1)
        self.letter_spacing_spinbox.setDecimals(1)
        self.letter_spacing_spinbox.setValue(1.0)
        self.letter_spacing_label = BodyLabel(self._t("Letter Spacing:"))
        style_layout.addRow(self.letter_spacing_label, self.letter_spacing_spinbox)

        self.angle_spinbox = _compact_double_spin_box()
        self.angle_spinbox.setRange(-9999.0, 9999.0)
        self.angle_spinbox.setSingleStep(1.0)
        self.angle_spinbox.setDecimals(1)
        self.angle_spinbox.setKeyboardTracking(False)
        self.angle_spinbox.setSuffix("°")
        self.angle_spinbox.setValue(0.0)
        self.angle_style_label = BodyLabel(self._t("Angle:"))
        style_layout.addRow(self.angle_style_label, self.angle_spinbox)

        # Alignment and direction
        self.alignment_combo = ComboBox()
        self.direction_combo = ComboBox()
        self.alignment_combo.setMinimumWidth(96)
        self.direction_combo.setMinimumWidth(96)
        self.alignment_label = BodyLabel(self._t("Alignment:"))
        self.direction_label = BodyLabel(self._t("Direction:"))
        style_layout.addRow(self.alignment_label, self.alignment_combo)
        style_layout.addRow(self.direction_label, self.direction_combo)

        self._finish_group(self.style_edit_frame, style_card)
        layout.addWidget(self.style_edit_frame)

    def _create_action_section(self, layout):
        self.action_frame, action_card = self._make_group(self._t("Actions"))
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(8, 8, 8, 6)
        action_layout.setSpacing(6)
        self.copy_button = PushButton()
        self.copy_button.setText(self._t("Copy"))
        self.copy_button.setIcon(FIF.COPY)
        set_hover_hint(self.copy_button, self._t("Copy") + " (Ctrl+C)")
        self.paste_button = PushButton()
        self.paste_button.setText(self._t("Paste"))
        self.paste_button.setIcon(FIF.PASTE)
        set_hover_hint(self.paste_button, self._t("Paste") + " (Ctrl+V)")
        self.delete_button = PushButton()
        self.delete_button.setText(self._t("Delete"))
        self.delete_button.setIcon(FIF.DELETE)
        set_hover_hint(self.delete_button, self._t("Delete") + " (Del)")
        action_layout.addWidget(self.copy_button)
        action_layout.addWidget(self.paste_button)
        action_layout.addWidget(self.delete_button)
        # 添加弹性空间，将按钮推向左侧，使它们更紧凑
        action_layout.addStretch()
        self._finish_group(self.action_frame, action_card)
        layout.addWidget(self.action_frame)

    def _connect_signals(self):
        # Mask
        self.mask_tool_group.buttonClicked.connect(self._on_mask_tool_changed)
        self.brush_size_slider.valueChanged.connect(self._on_brush_size_changed)
        self.paint_size_slider.valueChanged.connect(self._on_brush_size_changed)
        self.stamp_size_slider.valueChanged.connect(self._on_brush_size_changed)
        self.show_refined_mask_checkbox.stateChanged.connect(
            lambda state: self.toggle_mask_visibility.emit(bool(state))
        )
        self.clear_all_masks_button.clicked.connect(self.clear_all_masks_requested.emit)

        # Paint overlay
        self.paint_color_picker.color_changed.connect(self._on_paint_color_changed)
        self.clear_paint_overlay_button.clicked.connect(
            self.clear_paint_overlay_requested.emit
        )
        self.clear_stamp_overlay_button.clicked.connect(
            self.clear_stamp_overlay_requested.emit
        )
        self.show_paint_overlay_checkbox.toggled.connect(
            self.paint_overlay_visibility_changed.emit
        )
        self.show_stamp_overlay_checkbox.toggled.connect(
            self.stamp_overlay_visibility_changed.emit
        )
        self.paint_segmented_widget.currentItemChanged.connect(
            self._on_paint_route_changed
        )

        # Style
        self.font_family_combo.currentIndexChanged.connect(self._on_font_family_changed)
        self.font_family_combo.fontPreviewChanged.connect(
            self._on_font_family_preview_changed
        )
        self.font_size_input.valueChanged.connect(self._on_font_size_input_changed)
        self.font_size_slider.valueChanged.connect(self._on_font_size_slider_changed)
        self.font_color_picker.color_changed.connect(self._on_font_color_changed)
        self.stroke_color_picker.color_changed.connect(self._on_stroke_color_changed)
        self.stroke_width_spinbox.valueChanged.connect(self._on_stroke_width_changed)
        self.line_spacing_spinbox.valueChanged.connect(self._on_line_spacing_changed)
        self.letter_spacing_spinbox.valueChanged.connect(
            self._on_letter_spacing_changed
        )
        self.angle_spinbox.valueChanged.connect(self._on_angle_changed)
        self.style_preset_combo.activated.connect(self._on_style_preset_activated)
        self.save_style_preset_button.clicked.connect(
            self._on_save_style_preset_clicked
        )
        self.delete_style_preset_button.clicked.connect(
            self._on_delete_style_preset_clicked
        )
        # 实时更新（textChanged）
        # contentsChange 在 textChanged 之前触发,提供精确的编辑位置记录
        self.translated_text_box.document().contentsChange.connect(
            self._on_translated_contents_change
        )
        self.translated_text_box.textChanged.connect(self._on_translated_text_changed)
        self.alignment_combo.currentTextChanged.connect(self._on_alignment_changed)
        self.direction_combo.currentTextChanged.connect(self._on_direction_changed)

        # Text
        # 实时更新（textChanged）
        self.original_text_box.textChanged.connect(self._on_original_text_changed)
        self.ocr_model_combo.currentTextChanged.connect(self._on_ocr_model_change)
        self.translator_combo.currentTextChanged.connect(self._on_translator_change)
        self.target_language_combo.currentTextChanged.connect(
            self._on_target_language_change
        )
        self.ocr_button.clicked.connect(self.ocr_requested.emit)
        self.translate_button.clicked.connect(self.translation_requested.emit)

        # Action buttons
        self.copy_button.clicked.connect(self.copy_region_requested.emit)
        self.paste_button.clicked.connect(self.paste_region_requested.emit)
        self.delete_button.clicked.connect(self.delete_region_requested.emit)

    def _connect_model_signals(self):
        self.model.display_mask_type_changed.connect(self._on_display_mask_type_changed)
        self.model.refined_mask_changed.connect(self._on_refined_mask_changed)
        self.model.regions_changed.connect(self.on_regions_changed)

    def _on_display_mask_type_changed(self, mask_type: str):
        """响应显示蒙版类型变化"""
        # Block signals to prevent recursive calls
        self.show_refined_mask_checkbox.blockSignals(True)
        self.show_refined_mask_checkbox.setChecked(mask_type == "refined")
        self.show_refined_mask_checkbox.blockSignals(False)

    def _on_refined_mask_changed(self, mask):
        """响应refined mask数据变化"""
        # 不自动勾选checkbox，让用户自己决定是否显示
        pass

    def repopulate_options(self):
        """Public method to populate combo boxes from config. Should be called after config is loaded."""
        if not self.app_logic:
            return

        config = self.app_logic.config_service.get_config()
        ocr_config = config.ocr
        translator_config = config.translator

        # OCR
        ocr_options = self.app_logic.get_options_for_key("ocr")
        if ocr_options:
            self._repopulate_combo(
                self.ocr_model_combo, ocr_options, current_text=ocr_config.ocr
            )

        # Translator
        translator_map = self.app_logic.get_display_mapping("translator")
        if translator_map:
            self.translator_display_to_key = {v: k for k, v in translator_map.items()}
            self._repopulate_combo(
                self.translator_combo,
                list(translator_map.values()),
                current_text=translator_map.get(translator_config.translator),
            )

        # Target Language
        lang_map = self.app_logic.get_display_mapping("target_lang")
        if lang_map:
            self.lang_name_to_code = {v: k for k, v in lang_map.items()}
            self._repopulate_combo(
                self.target_language_combo,
                list(lang_map.values()),
                current_text=lang_map.get(translator_config.target_lang),
            )

        # Alignment（保持原选中文本，绝不能借机改写选中 region 的对齐）
        alignment_map = self.app_logic.get_display_mapping("alignment")
        if alignment_map:
            self._repopulate_combo(self.alignment_combo, list(alignment_map.values()))

        # Direction（同上，保持原选中文本）
        direction_map = self.app_logic.get_display_mapping("direction")
        if direction_map:
            self._repopulate_combo(
                self.direction_combo,
                [v for k, v in direction_map.items() if k != "auto"],
            )

    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）"""
        # 刷新分组框标题
        if hasattr(self, "mask_edit_frame"):
            self._set_group_title(self.mask_edit_frame, self._t("Image Editing"))
        if hasattr(self, "text_edit_frame"):
            self._set_group_title(self.text_edit_frame, self._t("Text Content"))
        if hasattr(self, "style_edit_frame"):
            self._set_group_title(self.style_edit_frame, self._t("Style Settings"))
        if hasattr(self, "action_frame"):
            self._set_group_title(self.action_frame, self._t("Actions"))

        # 刷新标签
        if hasattr(self, "brush_size_title_label"):
            self.brush_size_title_label.setText(self._t("Brush Size:"))
        if hasattr(self, "ocr_model_row_label"):
            self.ocr_model_row_label.setText(self._t("OCR Model:"))
        if hasattr(self, "translator_row_label"):
            self.translator_row_label.setText(self._t("Translator:"))
        if hasattr(self, "target_lang_row_label"):
            self.target_lang_row_label.setText(self._t("Target Language:"))
        if hasattr(self, "font_label"):
            self.font_label.setText(self._t("Font:"))
        if hasattr(self, "font_family_combo"):
            self.font_family_combo.refresh_ui_texts()
        if hasattr(self, "style_preset_label"):
            self.style_preset_label.setText(self._t("Style Preset:"))
        if hasattr(self, "font_size_label"):
            self.font_size_label.setText(self._t("Font Size:"))
        if hasattr(self, "font_color_label"):
            self.font_color_label.setText(self._t("Font Color:"))
        if hasattr(self, "stroke_color_label"):
            self.stroke_color_label.setText(self._t("Stroke Color:"))

        # 刷新颜色选择器内部文本
        if hasattr(self, "font_color_picker"):
            self.font_color_picker.refresh_ui_texts()
        if hasattr(self, "stroke_color_picker"):
            self.stroke_color_picker.refresh_ui_texts()

        if hasattr(self, "stroke_width_label"):
            self.stroke_width_label.setText(self._t("Stroke Width:"))
        if hasattr(self, "line_spacing_label"):
            self.line_spacing_label.setText(self._t("Line Spacing:"))
        if hasattr(self, "letter_spacing_label"):
            self.letter_spacing_label.setText(self._t("Letter Spacing:"))
        if hasattr(self, "angle_style_label"):
            self.angle_style_label.setText(self._t("Angle:"))
        if hasattr(self, "alignment_label"):
            self.alignment_label.setText(self._t("Alignment:"))
        if hasattr(self, "direction_label"):
            self.direction_label.setText(self._t("Direction:"))
        if hasattr(self, "original_text_label"):
            self.original_text_label.setText(self._t("Original Text:"))
        if hasattr(self, "translation_raw_checkbox"):
            self.translation_raw_checkbox.setText(self._t("Show Translation (Raw)"))
        if hasattr(self, "translated_text_label"):
            self.translated_text_label.setText(self._t("Translated Text:"))
        if hasattr(self, "text_stats_label"):
            self.text_stats_label.setText(self._t("Character count: 0"))

        # 刷新按钮
        if hasattr(self, "ocr_button"):
            self.ocr_button.setText(self._t("Recognize"))
        if hasattr(self, "translate_button"):
            self.translate_button.setText(self._t("Translate"))
        if hasattr(self, "brush_button"):
            self.brush_button.setText(self._t("Brush"))
            set_hover_hint(self.brush_button, self._t("Brush Tool") + " (W)")
        if hasattr(self, "eraser_button"):
            self.eraser_button.setText(self._t("Eraser"))
            set_hover_hint(self.eraser_button, self._t("Eraser Tool") + " (E)")
        if hasattr(self, "select_button"):
            self.select_button.setText(self._t("No Selection"))
            set_hover_hint(self.select_button, self._t("Selection Tool") + " (Q)")
        if hasattr(self, "paint_select_button"):
            self.paint_select_button.setText(self._t("No Selection"))
            set_hover_hint(self.paint_select_button, self._t("Selection Tool") + " (Q)")
        if hasattr(self, "paint_brush_button"):
            self.paint_brush_button.setText(self._t("Brush"))
            set_hover_hint(self.paint_brush_button, self._t("Brush Tool") + " (W)")
        if hasattr(self, "paint_eraser_button"):
            self.paint_eraser_button.setText(self._t("Eraser"))
            set_hover_hint(self.paint_eraser_button, self._t("Eraser Tool") + " (E)")
        if hasattr(self, "stamp_select_button"):
            self.stamp_select_button.setText(self._t("No Selection"))
            set_hover_hint(self.stamp_select_button, self._t("Selection Tool") + " (Q)")
        if hasattr(self, "paint_clone_button"):
            self.paint_clone_button.setText(self._t("Clone Stamp"))
            set_hover_hint(
                self.paint_clone_button, self._t("Clone Stamp Hint") + " (W)"
            )
        if hasattr(self, "stamp_eraser_button"):
            self.stamp_eraser_button.setText(self._t("Eraser"))
            set_hover_hint(self.stamp_eraser_button, self._t("Eraser Tool") + " (E)")
        if hasattr(self, "stamp_size_title_label"):
            self.stamp_size_title_label.setText(self._t("Brush Size:"))
        if hasattr(self, "clear_stamp_overlay_button"):
            self.clear_stamp_overlay_button.setText(self._t("Clear Stamp Layer"))
        if hasattr(self, "show_paint_overlay_checkbox"):
            self.show_paint_overlay_checkbox.setText(self._t("Show Paint Layer"))
        if hasattr(self, "show_stamp_overlay_checkbox"):
            self.show_stamp_overlay_checkbox.setText(self._t("Show Stamp Layer"))
        if hasattr(self, "paint_size_title_label"):
            self.paint_size_title_label.setText(self._t("Brush Size:"))
        if hasattr(self, "paint_color_label"):
            self.paint_color_label.setText(self._t("Brush Color:"))
        if hasattr(self, "paint_color_picker"):
            self.paint_color_picker.refresh_ui_texts()
        if hasattr(self, "paint_segmented_widget"):
            self.paint_segmented_widget.setItemText(self.MASK_ROUTE, self._t("Mask"))
            self.paint_segmented_widget.setItemText(self.PAINT_ROUTE, self._t("Paint"))
            self.paint_segmented_widget.setItemText(
                self.STAMP_ROUTE, self._t("Clone Stamp")
            )
            shortcut_hints = {
                self.MASK_ROUTE: self._t("Mask") + " (1)",
                self.PAINT_ROUTE: self._t("Paint") + " (2)",
                self.STAMP_ROUTE: self._t("Clone Stamp") + " (3)",
            }
            for route_key, hint in shortcut_hints.items():
                set_hover_hint(self.paint_segmented_widget.items[route_key], hint)
        if hasattr(self, "copy_button"):
            self.copy_button.setText(self._t("Copy"))
            set_hover_hint(self.copy_button, self._t("Copy") + " (Ctrl+C)")
        if hasattr(self, "paste_button"):
            self.paste_button.setText(self._t("Paste"))
            set_hover_hint(self.paste_button, self._t("Paste") + " (Ctrl+V)")
        if hasattr(self, "delete_button"):
            self.delete_button.setText(self._t("Delete"))
            set_hover_hint(self.delete_button, self._t("Delete") + " (Del)")
        if hasattr(self, "save_style_preset_button") or hasattr(
            self, "delete_style_preset_button"
        ):
            self._refresh_style_preset_action_buttons()

        # 刷新复选框
        if hasattr(self, "show_refined_mask_checkbox"):
            self.show_refined_mask_checkbox.setText(self._t("Show Refined Mask"))
        if hasattr(self, "clear_all_masks_button"):
            self.clear_all_masks_button.setText(self._t("Clear All Masks"))

        # 刷新下拉菜单（重新填充以使用新的翻译）
        self._refresh_combo_boxes()
        self._refresh_style_preset_combo()
        self.sync_sidebar_layout()

    def _refresh_combo_boxes(self):
        """刷新所有下拉菜单的选项"""
        # 保存当前选中的索引（而不是文本，因为文本会随语言变化）
        current_translator_index = self.translator_combo.currentIndex()
        current_target_lang_index = self.target_language_combo.currentIndex()
        current_alignment_index = self.alignment_combo.currentIndex()
        current_direction_index = self.direction_combo.currentIndex()

        # 重新填充翻译器下拉菜单
        translator_map = self.app_logic.get_display_mapping("translator")
        if translator_map:
            self._repopulate_combo(
                self.translator_combo,
                list(translator_map.values()),
                current_index=current_translator_index,
            )

        # 重新填充目标语言下拉菜单
        lang_map = self.app_logic.get_display_mapping("target_lang")
        if lang_map:
            self._repopulate_combo(
                self.target_language_combo,
                list(lang_map.values()),
                current_index=current_target_lang_index,
            )

        # 重新填充对齐下拉菜单
        alignment_map = self.app_logic.get_display_mapping("alignment")
        if alignment_map:
            self._repopulate_combo(
                self.alignment_combo,
                list(alignment_map.values()),
                current_index=current_alignment_index,
            )

        # 重新填充方向下拉菜单
        direction_map = self.app_logic.get_display_mapping("direction")
        if direction_map:
            self._repopulate_combo(
                self.direction_combo,
                [v for k, v in direction_map.items() if k != "auto"],
                current_index=current_direction_index,
            )

    def _get_saved_style_presets(self):
        config_ref = self.config_service.get_config_reference()
        presets = getattr(getattr(config_ref, "app", None), "saved_style_presets", None)
        return presets if isinstance(presets, dict) else {}

    def _refresh_style_preset_combo(self, selected_name: str | None = None):
        if not hasattr(self, "style_preset_combo"):
            return

        current_name = (
            selected_name
            if selected_name is not None
            else self.style_preset_combo.currentData()
        )
        presets = self._get_saved_style_presets()

        self.style_preset_combo.blockSignals(True)
        try:
            self.style_preset_combo.clear()
            self.style_preset_combo.addItem(
                self._t("Select saved style"), userData=None
            )
            for name in presets.keys():
                self.style_preset_combo.addItem(name, userData=name)

            if current_name in presets:
                target_index = self.style_preset_combo.findData(current_name)
                self.style_preset_combo.setCurrentIndex(
                    target_index if target_index >= 0 else 0
                )
            else:
                self.style_preset_combo.setCurrentIndex(0)
        finally:
            self.style_preset_combo.blockSignals(False)

        self.style_preset_combo.setToolTip(self._t("Choose a saved style to apply"))

    def _normalize_region_style_state(self, region_data):
        if not isinstance(region_data, dict):
            return {}

        default_font_color = (
            self.config_service.get_config().render.font_color or "#000000"
        )
        normalized = {}
        font_value = region_data.get("font_family", "")
        normalized["font_family"] = "" if font_value is None else str(font_value)

        font_color = region_data.get("font_color")
        fg_colors = region_data.get("fg_colors")
        if (
            not font_color
            and isinstance(fg_colors, (list, tuple))
            and len(fg_colors) == 3
        ):
            font_color = f"#{int(fg_colors[0]):02x}{int(fg_colors[1]):02x}{int(fg_colors[2]):02x}"
        font_color = str(font_color or default_font_color).strip()
        normalized["font_color"] = (
            QColor(font_color).name() if QColor(font_color).isValid() else "#000000"
        )

        stroke_color = region_data.get("stroke_color")
        if not stroke_color:
            bg_color = region_data.get("bg_color")
            bg_colors = region_data.get("bg_colors")
            if isinstance(bg_color, (list, tuple)) and len(bg_color) == 3:
                stroke_color = f"#{int(bg_color[0]):02x}{int(bg_color[1]):02x}{int(bg_color[2]):02x}"
            elif isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_color = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
        stroke_color = str(stroke_color or "#ffffff").strip()
        normalized["stroke_color"] = (
            QColor(stroke_color).name() if QColor(stroke_color).isValid() else "#ffffff"
        )

        try:
            normalized["stroke_width"] = float(region_data.get("stroke_width", 0.07))
        except (TypeError, ValueError):
            normalized["stroke_width"] = 0.07

        try:
            normalized["line_spacing"] = float(region_data.get("line_spacing", 1.0))
        except (TypeError, ValueError):
            normalized["line_spacing"] = 1.0

        try:
            normalized["letter_spacing"] = float(region_data.get("letter_spacing", 1.0))
        except (TypeError, ValueError):
            normalized["letter_spacing"] = 1.0

        normalized["alignment"] = self._alignment_value_from_text(
            region_data.get("alignment", "auto")
        )
        normalized["direction"] = self._direction_value_from_text(
            region_data.get("direction", "horizontal")
        )
        return normalized

    def _find_matching_style_preset_name(self, region_data) -> str | None:
        normalized_region_style = self._normalize_region_style_state(region_data)
        if not normalized_region_style:
            return None

        for name, preset_data in self._get_saved_style_presets().items():
            if (
                self._normalize_saved_style_preset(preset_data)
                == normalized_region_style
            ):
                return str(name)
        return None

    def _refresh_style_preset_action_buttons(self):
        if hasattr(self, "save_style_preset_button"):
            self.save_style_preset_button.setText("")
            self.save_style_preset_button.setIcon(FIF.SAVE)
            set_hover_hint(
                self.save_style_preset_button, self._t("Save current style combination")
            )
            self.save_style_preset_button.setAccessibleName(self._t("Save Style"))

        if hasattr(self, "delete_style_preset_button"):
            self.delete_style_preset_button.setText("")
            self.delete_style_preset_button.setIcon(FIF.DELETE)
            set_hover_hint(
                self.delete_style_preset_button, self._t("Delete selected saved style")
            )
            self.delete_style_preset_button.setAccessibleName(self._t("Delete Style"))

    def _alignment_value_from_text(self, text: str) -> str:
        raw_text = str(text or "").strip()
        if raw_text in {"auto", "left", "center", "right"}:
            return raw_text

        alignment_map = self.app_logic.get_display_mapping("alignment") or {}
        reverse_map = {display: value for value, display in alignment_map.items()}
        if raw_text in reverse_map:
            return reverse_map[raw_text]

        fallback_map = {
            "自动": "auto",
            "左对齐": "left",
            "居中": "center",
            "右对齐": "right",
        }
        return fallback_map.get(raw_text, "auto")

    def _alignment_text_for_value(self, value: str) -> str:
        alignment_map = self.app_logic.get_display_mapping("alignment") or {}
        normalized_value = self._alignment_value_from_text(value)
        fallback_map = {
            "auto": "自动",
            "left": "左对齐",
            "center": "居中",
            "right": "右对齐",
        }
        return alignment_map.get(
            normalized_value, fallback_map.get(normalized_value, normalized_value)
        )

    def _direction_value_from_text(self, text: str) -> str:
        raw_text = str(text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in {"h", "horizontal"}:
            return "horizontal"
        if lower_text in {"v", "vertical"}:
            return "vertical"

        direction_map = self.app_logic.get_display_mapping("direction") or {}
        horizontal_text = direction_map.get("h", self._t("direction_horizontal"))
        vertical_text = direction_map.get("v", self._t("direction_vertical"))
        if raw_text == vertical_text or raw_text == "竖排":
            return "vertical"
        if raw_text == horizontal_text or raw_text == "横排":
            return "horizontal"
        return "horizontal"

    def _direction_text_for_value(self, value: str) -> str:
        direction_map = self.app_logic.get_display_mapping("direction") or {}
        horizontal_text = direction_map.get("h", self._t("direction_horizontal"))
        vertical_text = direction_map.get("v", self._t("direction_vertical"))
        normalized_value = self._direction_value_from_text(value)
        return vertical_text if normalized_value == "vertical" else horizontal_text

    def _set_font_family_combo_value(self, font_value: str):
        self.font_family_combo.setCurrentFamily(font_value)

    def _normalize_saved_style_preset(self, style_data):
        if not isinstance(style_data, dict):
            return {}

        normalized = {}
        font_value = style_data.get("font_family", "")
        normalized["font_family"] = "" if font_value is None else str(font_value)

        font_color = str(style_data.get("font_color") or "#000000").strip()
        normalized["font_color"] = (
            QColor(font_color).name() if QColor(font_color).isValid() else "#000000"
        )

        stroke_color = style_data.get("stroke_color")
        if not stroke_color:
            bg_colors = style_data.get("bg_colors", style_data.get("bg_color"))
            if isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_color = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
        stroke_color = str(stroke_color or "#ffffff").strip()
        normalized["stroke_color"] = (
            QColor(stroke_color).name() if QColor(stroke_color).isValid() else "#ffffff"
        )

        try:
            normalized["stroke_width"] = float(style_data.get("stroke_width", 0.07))
        except (TypeError, ValueError):
            normalized["stroke_width"] = 0.07

        try:
            normalized["line_spacing"] = float(style_data.get("line_spacing", 1.0))
        except (TypeError, ValueError):
            normalized["line_spacing"] = 1.0

        try:
            normalized["letter_spacing"] = float(style_data.get("letter_spacing", 1.0))
        except (TypeError, ValueError):
            normalized["letter_spacing"] = 1.0

        normalized["alignment"] = self._alignment_value_from_text(
            style_data.get("alignment", "auto")
        )
        normalized["direction"] = self._direction_value_from_text(
            style_data.get("direction", "horizontal")
        )
        return normalized

    def _collect_current_style_preset(self):
        current_font = self.font_family_combo.currentFamily()

        return {
            "font_family": str(current_font or ""),
            "font_color": self.font_color_picker.get_color(),
            "stroke_color": self.stroke_color_picker.get_color(),
            "stroke_width": float(self.stroke_width_spinbox.value()),
            "line_spacing": float(self.line_spacing_spinbox.value()),
            "letter_spacing": float(self.letter_spacing_spinbox.value()),
            "alignment": self._alignment_value_from_text(
                self.alignment_combo.currentText()
            ),
            "direction": self._direction_value_from_text(
                self.direction_combo.currentText()
            ),
        }

    def _set_style_controls_from_preset(self, style_data):
        normalized = self._normalize_saved_style_preset(style_data)
        if not normalized:
            return

        self._set_font_family_combo_value(normalized.get("font_family", ""))
        self.font_color_picker.set_color(normalized.get("font_color", "#000000"))
        self.stroke_color_picker.set_color(normalized.get("stroke_color", "#ffffff"))
        self.stroke_width_spinbox.setValue(normalized.get("stroke_width", 0.07))
        self.line_spacing_spinbox.setValue(normalized.get("line_spacing", 1.0))
        self.letter_spacing_spinbox.setValue(normalized.get("letter_spacing", 1.0))
        self.alignment_combo.setCurrentText(
            self._alignment_text_for_value(normalized.get("alignment", "auto"))
        )
        self.direction_combo.setCurrentText(
            self._direction_text_for_value(normalized.get("direction", "horizontal"))
        )

    def _apply_saved_style_to_selection(self, preset_name: str):
        from PyQt6.QtWidgets import QMessageBox

        selected_indices = self.model.get_selection()
        if not selected_indices:
            QMessageBox.warning(
                self, self._t("Warning"), self._t("Please select at least one region")
            )
            self._refresh_style_preset_combo()
            return

        style_data = self._normalize_saved_style_preset(
            self._get_saved_style_presets().get(preset_name)
        )
        if not style_data:
            QMessageBox.warning(
                self, self._t("Warning"), self._t("Selected style preset is invalid")
            )
            self._refresh_style_preset_combo()
            return

        self.block_updates = True
        try:
            self._set_style_controls_from_preset(style_data)
        finally:
            self.block_updates = False

        self.style_patch_requested.emit(list(selected_indices), dict(style_data))

        self._refresh_style_preset_combo(selected_name=preset_name)

    def _on_style_preset_activated(self, index: int):
        preset_name = self.style_preset_combo.itemData(index)
        if preset_name:
            self._apply_saved_style_to_selection(str(preset_name))

    def _on_save_style_preset_clicked(self):
        import copy

        from PyQt6.QtWidgets import QMessageBox

        default_name = self.style_preset_combo.currentData() or ""
        preset_name, ok = themed_get_text(
            self,
            title=self._t("Save Style"),
            label=self._t("Enter style preset name:"),
            text=str(default_name),
            ok_text=self._t("Save"),
            cancel_text=self._t("Cancel"),
        )
        if not ok:
            return

        preset_name = preset_name.strip()
        if not preset_name:
            QMessageBox.warning(
                self, self._t("Warning"), self._t("Style preset name cannot be empty")
            )
            return

        current_presets = copy.deepcopy(self._get_saved_style_presets())
        if preset_name in current_presets:
            reply = QMessageBox.question(
                self,
                self._t("Confirm"),
                self._t(
                    "Style preset '{name}' already exists. Overwrite?", name=preset_name
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        new_presets = copy.deepcopy(current_presets)
        new_presets[preset_name] = self._collect_current_style_preset()

        config_ref = self.config_service.get_config_reference()
        config_ref.app.saved_style_presets = new_presets
        if not self.config_service.save_config_file():
            config_ref.app.saved_style_presets = current_presets or None
            QMessageBox.critical(
                self, self._t("Error"), self._t("Failed to save style preset")
            )
            return

        self._refresh_style_preset_combo(selected_name=preset_name)

    def _on_delete_style_preset_clicked(self):
        import copy

        from PyQt6.QtWidgets import QMessageBox

        preset_name = self.style_preset_combo.currentData()
        if not preset_name:
            QMessageBox.warning(
                self, self._t("Warning"), self._t("Please select a saved style")
            )
            return

        reply = QMessageBox.question(
            self,
            self._t("Confirm"),
            self._t("Delete style preset '{name}'?", name=preset_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        current_presets = copy.deepcopy(self._get_saved_style_presets())
        if preset_name not in current_presets:
            self._refresh_style_preset_combo()
            return

        new_presets = copy.deepcopy(current_presets)
        del new_presets[preset_name]

        config_ref = self.config_service.get_config_reference()
        config_ref.app.saved_style_presets = new_presets or None
        if not self.config_service.save_config_file():
            config_ref.app.saved_style_presets = current_presets or None
            QMessageBox.critical(
                self, self._t("Error"), self._t("Failed to delete style preset")
            )
            return

        self._refresh_style_preset_combo()

    def on_regions_changed(self, change):
        """选中 region 的数据变化时刷新面板；本面板发起的修改只跟进信息标签。"""
        selected_indices = self.model.get_selection()
        if not selected_indices or len(selected_indices) > 1:
            return

        region_index = selected_indices[0]
        if change.kind != "reset" and region_index not in change.indices:
            return

        region_data = self.model.get_region_by_index(region_index)
        if not region_data:
            return

        force_text_fields = set(change.fields) if change.source == "async" else set()
        self._update_display(
            region_data,
            region_index,
            update_focused_text=False,
            force_text_fields=force_text_fields,
        )

    def on_selection_changed(self, selected_indices):
        """Slot to update the panel when the selection in the model changes."""
        if not selected_indices:
            # 没有选择，禁用所有控件
            self.clear_and_disable_selection_dependent()
        elif len(selected_indices) == 1:
            # 单选，显示该区域的详细信息
            self.text_edit_frame.setEnabled(True)
            self.style_edit_frame.setEnabled(True)
            self.action_frame.setEnabled(True)
            region_index = selected_indices[0]
            self.current_region_index = region_index
            regions = self.model.get_regions()
            if 0 <= region_index < len(regions):
                self._update_display(
                    regions[region_index], region_index, update_focused_text=True
                )
        else:
            # 多选，启用样式编辑，但禁用文本编辑
            self.text_edit_frame.setEnabled(False)
            self.style_edit_frame.setEnabled(True)  # 启用样式编辑
            self.action_frame.setEnabled(True)
            self.current_region_index = -1

            # 清空显示但不禁用样式控件
            self.block_updates = True
            try:
                self.original_text_box.clear()
                self.translated_text_box.clear()
                self._refresh_style_preset_combo(selected_name="")
            finally:
                self.block_updates = False

    def clear_and_disable_selection_dependent(self):
        """Clears selection-dependent fields and disables their sections."""
        # Disable sections that depend on a selection
        self.text_edit_frame.setEnabled(False)
        self.style_edit_frame.setEnabled(False)
        self.action_frame.setEnabled(False)

        self.current_region_index = -1

        self.block_updates = True
        self._set_selection_controls_blocked(True)
        try:
            self.original_text_box.clear()
            self.translated_text_box.clear()
            self.font_size_input.setValue(12)
            self.stroke_width_spinbox.setValue(0.07)  # 重置为默认值
            self.line_spacing_spinbox.setValue(1.0)  # 重置为默认值
            self.letter_spacing_spinbox.setValue(1.0)  # 重置为默认值
            self.angle_spinbox.setValue(0.0)
            default_color = (
                self.config_service.get_config().render.font_color or "#000000"
            )
            self.font_color_picker.reset(default_color)
            self.stroke_color_picker.reset("#ffffff")
            self._refresh_style_preset_combo(selected_name="")
        finally:
            self._set_selection_controls_blocked(False)
            self.block_updates = False

    def _update_display(
        self,
        region_data,
        region_index,
        *,
        update_focused_text: bool = True,
        force_text_fields: set[str] | None = None,
    ):
        """Populate all widgets with data from the selected region.

        Args:
            region_data: 区域数据字典
            region_index: 区域索引
            update_focused_text: 是否覆盖正在编辑的文本框
        """
        force_text_fields = force_text_fields or set()
        self.block_updates = True
        self._set_selection_controls_blocked(True)
        try:
            # --- Update Text & Styles ---
            # 统一使用 text 字段（用户编辑和OCR识别都使用这个字段）
            original_text = region_data.get("text", "")
            update_original_text = update_focused_text or "text" in force_text_fields
            if (
                update_original_text or not self.original_text_box.hasFocus()
            ) and self.original_text_box.toPlainText() != original_text:
                self.original_text_box.setText(original_text)

            import re

            # 复选框选中 → 显示"替换前译文"(translation_raw),否则显示"译文"(translation)
            show_raw = bool(
                getattr(self, "translation_raw_checkbox", None)
                and self.translation_raw_checkbox.isChecked()
            )
            field_key = "translation_raw" if show_raw else "translation"
            translation_text = region_data.get(field_key, "") or region_data.get(
                "translation", ""
            )
            update_translation_text = (
                update_focused_text
                or field_key in force_text_fields
                or (
                    field_key == "translation_raw"
                    and "translation" in force_text_fields
                    and not region_data.get("translation_raw")
                )
            )

            # 将所有 AI 换行符 ([BR], <br>, 【BR】) 转换为真实换行
            translation_text = re.sub(
                r"\s*(\[BR\]|<br>|【BR】)\s*",
                "\n",
                translation_text,
                flags=re.IGNORECASE,
            )

            # 剥除存量的旧 <H> 局部横排标记（协议已废除，保留内文显示）
            display_text = strip_legacy_horizontal_tags(translation_text)

            if (
                update_translation_text or not self.translated_text_box.hasFocus()
            ) and self.translated_text_box.toPlainText() != display_text:
                self.translated_text_box.setText(display_text)
            # 重置编辑操作基线:无论是否覆盖了文本,都以框内当前内容为准
            self._translation_edit_recorder.reset(
                self.translated_text_box.toPlainText()
            )

            font_size = int(region_data.get("font_size", 12) or 12)
            self.font_size_input.setValue(font_size)
            self.font_size_slider.setValue(font_size)

            default_color = (
                self.config_service.get_config().render.font_color or "#000000"
            )
            color_hex = default_color
            fg_colors = region_data.get("fg_colors")
            font_color = region_data.get("font_color")

            # 优先使用用户设置的font_color，然后才是原始的fg_colors
            if font_color:
                color_hex = font_color
            elif isinstance(fg_colors, (list, tuple)) and len(fg_colors) == 3:
                color_hex = f"#{int(fg_colors[0]):02x}{int(fg_colors[1]):02x}{int(fg_colors[2]):02x}"

            self.font_color_picker.set_color(color_hex)

            # Update stroke color display
            bg_colors = region_data.get("bg_colors")
            bg_color = region_data.get("bg_color")
            stroke_hex = "#ffffff"
            if isinstance(bg_color, (list, tuple)) and len(bg_color) == 3:
                stroke_hex = f"#{int(bg_color[0]):02x}{int(bg_color[1]):02x}{int(bg_color[2]):02x}"
            elif isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_hex = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
            self.stroke_color_picker.set_color(stroke_hex)

            # Update stroke width
            stroke_width = region_data.get("stroke_width", 0.07)
            self.stroke_width_spinbox.setValue(
                stroke_width if stroke_width is not None else 0.07
            )

            # Update line spacing
            line_spacing = region_data.get("line_spacing", 1.0)
            self.line_spacing_spinbox.setValue(
                line_spacing if line_spacing is not None else 1.0
            )

            letter_spacing = region_data.get("letter_spacing", 1.0)
            self.letter_spacing_spinbox.setValue(
                letter_spacing if letter_spacing is not None else 1.0
            )
            self.angle_spinbox.setValue(float(region_data.get("angle", 0.0) or 0.0))

            self._set_font_family_combo_value(region_data.get("font_family", ""))
            self.alignment_combo.setCurrentText(
                self._alignment_text_for_value(region_data.get("alignment", "auto"))
            )

            display_direction_map = (
                self.app_logic.get_display_mapping("direction") or {}
            )
            horizontal_text = display_direction_map.get(
                "h", self._t("direction_horizontal")
            )
            vertical_text = display_direction_map.get(
                "v", self._t("direction_vertical")
            )

            direction_value = str(region_data.get("direction", "")).strip().lower()
            if direction_value in ("v", "vertical"):
                direction_display = vertical_text
            elif direction_value in ("h", "horizontal"):
                direction_display = horizontal_text
            else:
                wf_info = self._calculate_white_frame_info(region_data)
                if wf_info:
                    _, _, w, h = wf_info
                    direction_display = vertical_text if h > w else horizontal_text
                else:
                    direction_display = horizontal_text
            self.direction_combo.setCurrentText(direction_display)

            # --- Update Mask Checkboxes ---
            display_mask_type = self.model.get_display_mask_type()
            self.show_refined_mask_checkbox.setChecked(display_mask_type == "refined")
            self._refresh_style_preset_combo(
                selected_name=self._find_matching_style_preset_name(region_data) or ""
            )
        finally:
            self._set_selection_controls_blocked(False)
            self.block_updates = False

    @staticmethod
    def _editor_text_to_model_text(raw_text: str) -> str:
        """把文本框的真实换行转换为模型存储形式（[BR]）。

        不再从 ⇄ 生产 <H> 标记（旧局部横排协议已废除，改用富文本 tcy）；
        存量/手输的字面 <H></H> 在此剥除（保留内文），避免被当普通字符
        画上成品图。
        """
        import re

        text_without_tags = strip_legacy_horizontal_tags(raw_text)
        return re.sub(r"\n+", "[BR]", text_without_tags)

    def force_save_text_edits(self):
        """强制保存当前文本框的编辑内容（在失去焦点前）"""
        if self.current_region_index == -1:
            return

        # 保存原文编辑
        current_original = self.original_text_box.toPlainText()
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            # 比较当前编辑的文本与original_text（如果没有则与text比较）
            stored_original = region_data.get("original_text") or region_data.get(
                "text", ""
            )
            if stored_original != current_original:
                self.original_text_modified.emit(
                    self.current_region_index, current_original
                )

        # 保存译文编辑
        self._save_translated_text()

    def _save_translated_text(self):
        """保存译文编辑（内容有变化时按当前模式写回对应字段）"""
        if self.current_region_index == -1:
            return

        raw_text = self.translated_text_box.toPlainText()
        text_with_br = self._editor_text_to_model_text(raw_text)

        # 按当前模式决定写入哪个字段
        show_raw = bool(
            getattr(self, "translation_raw_checkbox", None)
            and self.translation_raw_checkbox.isChecked()
        )
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            field_key = "translation_raw" if show_raw else "translation"
            if region_data.get(field_key, "") != text_with_br:
                edit_info = self._take_translation_edit_info()
                if show_raw:
                    self.translation_raw_modified.emit(
                        self.current_region_index, text_with_br, edit_info
                    )
                else:
                    self.translated_text_modified.emit(
                        self.current_region_index, text_with_br, edit_info
                    )

    def _on_original_text_changed(self):
        if self.current_region_index != -1 and not self.block_updates:
            text = self.original_text_box.toPlainText()
            self.original_text_modified.emit(self.current_region_index, text)

    def _take_translation_edit_info(self) -> dict:
        """取走累积的编辑操作记录(采集/收窄逻辑在后端 EditOpRecorder)。"""
        return self._translation_edit_recorder.take_edit_info(
            self.translated_text_box.toPlainText()
        )

    def _on_translated_contents_change(
        self, position: int, chars_removed: int, chars_added: int
    ):
        """转发译文框的编辑事件(在 textChanged 之前触发);逻辑在后端。"""
        current = self.translated_text_box.toPlainText()
        if getattr(self, "block_updates", True):
            # 程序化 setText:操作作废,基线由 _update_display 统一重置
            self._translation_edit_recorder.invalidate(current)
            return
        self._translation_edit_recorder.record_change(
            current, position, chars_removed, chars_added
        )

    def _on_translated_text_changed(self):
        if self.current_region_index != -1 and not self.block_updates:
            raw_text = self.translated_text_box.toPlainText()
            text_with_br = self._editor_text_to_model_text(raw_text)
            edit_info = self._take_translation_edit_info()

            # 复选框选中 → 当前编辑的是"替换前译文",走 raw 信号(controller 会跑替换更新 translation);
            # 否则编辑的是"译文",走原信号
            show_raw = bool(
                getattr(self, "translation_raw_checkbox", None)
                and self.translation_raw_checkbox.isChecked()
            )
            if show_raw:
                self.translation_raw_modified.emit(
                    self.current_region_index, text_with_br, edit_info
                )
            else:
                self.translated_text_modified.emit(
                    self.current_region_index, text_with_br, edit_info
                )

    def _on_translation_raw_mode_toggled(self, checked: bool):
        """复选框切换:重新刷新当前 region 的文本框内容(读取对应字段)。"""
        if self.current_region_index == -1:
            return
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            self._update_display(
                region_data, self.current_region_index, update_focused_text=True
            )

    def get_selected_ocr_model(self) -> str:
        """获取当前选择的OCR模型"""
        return self.ocr_model_combo.currentText()

    def get_selected_translator(self) -> str:
        """获取当前选择的翻译器（返回key而不是display name）"""
        display_name = self.translator_combo.currentText()
        return self.translator_display_to_key.get(display_name, display_name)

    def get_selected_target_language(self) -> str:
        """获取当前选择的目标语言（返回key而不是display name）"""
        display_name = self.target_language_combo.currentText()
        # 使用 lang_name_to_code 映射（在 populate_options_from_config 中创建）
        if hasattr(self, "lang_name_to_code"):
            return self.lang_name_to_code.get(display_name, display_name)
        return display_name

    def _emit_style_patch(self, patch: dict) -> None:
        if self.block_updates or not patch:
            return
        selected_indices = self.model.get_selection()
        if selected_indices:
            self.style_patch_requested.emit(list(selected_indices), dict(patch))

    def _on_font_size_input_changed(self, value: int):
        if self.block_updates:
            return
        value = max(8, min(1000, int(value)))
        if self.font_size_slider.minimum() <= value <= self.font_size_slider.maximum():
            if self.font_size_slider.value() != value:
                self.font_size_slider.blockSignals(True)
                try:
                    self.font_size_slider.setValue(value)
                finally:
                    self.font_size_slider.blockSignals(False)
        self._emit_style_patch({"font_size": value})

    def _on_font_size_slider_changed(self, value):
        if self.block_updates:
            return
        if self.font_size_input.value() != value:
            self.font_size_input.blockSignals(True)
            try:
                self.font_size_input.setValue(value)
            finally:
                self.font_size_input.blockSignals(False)
        self._emit_style_patch({"font_size": int(value)})

    def _on_font_family_changed(self, index):
        if self.block_updates:
            return
        if index < 0:
            return

        # Get the font filename from combo box data
        font_filename = self.font_family_combo.currentFamily()
        self._emit_style_patch({"font_family": font_filename})

    def _on_font_family_preview_changed(self, family: str):
        if self.block_updates:
            return
        selected_indices = self.model.get_selection()
        if selected_indices:
            self.font_family_preview_requested.emit(
                list(selected_indices), str(family or "")
            )

    def _on_font_color_changed(self, hex_color):
        """字体颜色变化时的处理"""
        if self.block_updates:
            return
        self._emit_style_patch({"font_color": hex_color})

    def _on_stroke_color_changed(self, hex_color):
        """描边颜色变化时的处理"""
        if self.block_updates:
            return
        self._emit_style_patch({"stroke_color": hex_color})

    def _on_stroke_width_changed(self, value):
        """处理描边宽度变化"""
        if self.block_updates:
            return
        self._emit_style_patch({"stroke_width": float(value)})

    def _on_line_spacing_changed(self, value):
        """处理行间距倍率变化"""
        if self.block_updates:
            return
        self._emit_style_patch({"line_spacing": float(value)})

    def _on_letter_spacing_changed(self, value):
        """处理字间距倍率变化"""
        if self.block_updates:
            return
        self._emit_style_patch({"letter_spacing": float(value)})

    def _on_angle_changed(self, value):
        if self.block_updates:
            return
        self._emit_style_patch({"angle": float(value)})

    def _on_mask_tool_changed(self, button):
        if (
            button is self.select_button
            or button is self.paint_select_button
            or button is self.stamp_select_button
        ):
            self.mask_tool_changed.emit("select")
        elif button is self.brush_button:
            self.mask_tool_changed.emit("brush")
        elif button is self.eraser_button:
            self.mask_tool_changed.emit("eraser")
        elif button is self.paint_brush_button:
            self.mask_tool_changed.emit("paint")
        elif button is self.paint_eraser_button:
            self.mask_tool_changed.emit("paint_erase")
        elif button is self.paint_clone_button:
            self.mask_tool_changed.emit("clone")
        elif button is self.stamp_eraser_button:
            self.mask_tool_changed.emit("stamp_erase")

    def _on_brush_size_changed(self, value):
        """三个大小滑块共享同一个模型字段；同步其余滑块显示，避免循环触发。"""
        sender = self.sender()
        for slider, label in (
            (self.brush_size_slider, self.brush_size_value_label),
            (self.paint_size_slider, self.paint_size_value_label),
            (self.stamp_size_slider, self.stamp_size_value_label),
        ):
            label.setText(str(value))
            if slider is not sender and slider.value() != value:
                slider.blockSignals(True)
                slider.setValue(value)
                slider.blockSignals(False)
        self.brush_size_changed.emit(value)

    def _on_paint_color_changed(self, hex_color: str):
        self.brush_color_changed.emit(hex_color)

    def _on_paint_tab_changed(self, index: int):
        """切换标签页时，自动把活跃工具切回当前页的选择工具，避免跨页工具冲突。"""
        try:
            page_buttons = {
                0: (self.select_button, self.brush_button, self.eraser_button),
                1: (
                    self.paint_select_button,
                    self.paint_brush_button,
                    self.paint_eraser_button,
                ),
                2: (
                    self.stamp_select_button,
                    self.paint_clone_button,
                    self.stamp_eraser_button,
                ),
            }
            buttons = page_buttons.get(index, page_buttons[0])
            checked = self.mask_tool_group.checkedButton()
            if checked not in buttons:
                buttons[0].setChecked(True)
                self.mask_tool_changed.emit("select")
        except Exception:
            pass

    def sync_brush_size_from_model(self, size: int):
        """从模型同步画笔大小到UI（不触发信号）"""
        for slider, label in (
            (self.brush_size_slider, self.brush_size_value_label),
            (
                getattr(self, "paint_size_slider", None),
                getattr(self, "paint_size_value_label", None),
            ),
            (
                getattr(self, "stamp_size_slider", None),
                getattr(self, "stamp_size_value_label", None),
            ),
        ):
            if slider is None:
                continue
            slider.blockSignals(True)
            slider.setValue(size)
            slider.blockSignals(False)
            if label is not None:
                label.setText(str(size))

    def sync_brush_color_from_model(self, hex_color: str):
        """从模型同步画笔颜色到 UI（不触发信号）"""
        if hasattr(self, "paint_color_picker") and self.paint_color_picker is not None:
            self.paint_color_picker.set_color(hex_color or "#ffffff")

    def sync_active_tool_from_model(self, tool: str):
        """当 model 的 active_tool 变化时，UI 同步高亮对应按钮并切换标签页。"""
        # 'select' 在蒙版页和画板页都有按钮，按当前所在标签页决定亮哪个，
        # 避免在画板页点击「选择」时被强制切回蒙版页。
        if tool == "select":
            current_index = self._paint_current_index()
            select_buttons = {
                0: self.select_button,
                1: self.paint_select_button,
                2: self.stamp_select_button,
            }
            button = select_buttons.get(current_index, self.select_button)
            tab_index = current_index if current_index in select_buttons else 0
        else:
            mapping = {
                "brush": (self.brush_button, 0),
                "eraser": (self.eraser_button, 0),
                "paint": (getattr(self, "paint_brush_button", None), 1),
                "paint_erase": (getattr(self, "paint_eraser_button", None), 1),
                "clone": (getattr(self, "paint_clone_button", None), 2),
                "stamp_erase": (getattr(self, "stamp_eraser_button", None), 2),
            }
            info = mapping.get(tool)
            if not info:
                return
            button, tab_index = info
        if button is None:
            return
        try:
            self.mask_tool_group.blockSignals(True)
            if self.paint_segmented_widget is not None:
                self.paint_segmented_widget.blockSignals(True)
            button.setChecked(True)
            if self._paint_current_index() != tab_index:
                self._set_paint_route(
                    self._paint_route_for_index(tab_index), emit_changed=False
                )
        finally:
            self.mask_tool_group.blockSignals(False)
            if self.paint_segmented_widget is not None:
                self.paint_segmented_widget.blockSignals(False)
        self.sync_sidebar_layout()

    def _on_alignment_changed(self, text: str):
        if self.block_updates:
            return
        self._emit_style_patch({"alignment": text})

    def _on_direction_changed(self, text: str):
        if self.block_updates:
            return
        self._emit_style_patch({"direction": text})

    def _calculate_white_frame_info(self, region_data):
        """计算白框中心世界坐标和宽高，返回 (cx, cy, w, h) 或 None。"""
        import math

        region_data = normalize_region_geometry_data(region_data)
        has_custom = bool(region_data.get("has_custom_white_frame", False))
        wf_local = region_data.get("render_box_rect_local")
        if has_custom:
            wf_local = region_data.get("white_frame_rect_local")
        center = region_data.get("center")
        angle = float(region_data.get("angle", 0))

        if wf_local and len(wf_local) == 4:
            left, top, right, bottom = wf_local
            w = max(0.0, right - left)
            h = max(0.0, bottom - top)
            lx = (left + right) / 2.0
            ly = (top + bottom) / 2.0
            if center and len(center) >= 2:
                rad = math.radians(angle)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                cx_base, cy_base = float(center[0]), float(center[1])
                cx = cx_base + lx * cos_a - ly * sin_a
                cy = cy_base + lx * sin_a + ly * cos_a
            else:
                cx, cy = lx, ly
            return (cx, cy, w, h)

        # 兜底：从 lines[0] bbox 计算
        lines = region_data.get("lines", [])
        if not lines or not lines[0]:
            return None
        all_points = lines[0]
        if not all_points:
            return None
        x_coords = [p[0] for p in all_points]
        y_coords = [p[1] for p in all_points]
        x0, x1 = min(x_coords), max(x_coords)
        y0, y1 = min(y_coords), max(y_coords)
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0)

    # _mark_horizontal 已删除：局部横排改用富文本 tcy（浮动编辑器 T 按钮），
    # 旧 <H> 协议已废除，渲染管线不再有任何 <H> 消费方。

    def _on_ocr_model_change(self, text):
        """OCR模型变化时保存配置"""
        self.app_logic.update_single_config("ocr.ocr", text)

    def _on_translator_change(self, display_name):
        """翻译器变化时保存配置"""
        translator_key = self.translator_display_to_key.get(display_name, display_name)
        self.app_logic.update_single_config("translator.translator", translator_key)

    def _on_target_language_change(self, display_name):
        """目标语言变化时保存配置"""
        lang_code = self.lang_name_to_code.get(display_name, "CHS")
        self.app_logic.update_single_config("translator.target_lang", lang_code)
        # 同时更新翻译服务的目标语言
        self.app_logic.translation_service.set_target_language(lang_code)
