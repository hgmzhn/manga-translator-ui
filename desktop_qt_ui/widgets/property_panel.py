
import logging

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIntValidator, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from services import get_config_service, get_i18n_manager

from .color_picker import ColorPickerWidget
from .hover_hint import set_hover_hint

# from .collapsible_frame import CollapsibleFrame  # 不再使用折叠框
from .themed_text_input_dialog import themed_get_text

logger = logging.getLogger('manga_translator')


def convert_arrows_to_tags(raw_text: str) -> str:
    """
    将文本中的 ⇄ 符号转换为 <H> 标签

    Args:
        raw_text: 包含 ⇄ 符号的原始文本

    Returns:
        转换后的文本，⇄ 符号被替换为成对的 <H></H> 标签

    Note:
        - 如果 ⇄ 是偶数个，会正确配对为 <H></H>
        - 如果 ⇄ 是奇数个，最后一个会被转换为 <H>，但会记录警告
    """
    if '⇄' not in raw_text:
        return raw_text

    parts = raw_text.split('⇄')
    text_with_tags = ''

    for i, part in enumerate(parts):
        text_with_tags += part
        if i < len(parts) - 1:  # 不是最后一个部分
            if i % 2 == 0:  # 偶数索引,添加开始标签
                text_with_tags += '<H>'
            else:  # 奇数索引,添加结束标签
                text_with_tags += '</H>'

    # 检查是否有未闭合的标签（奇数个⇄）
    arrow_count = len(parts) - 1
    if arrow_count % 2 != 0:
        logger.warning(f"检测到奇数个⇄符号({arrow_count}个)，最后一个<H>标签未闭合")

    return text_with_tags


class CustomSlider(QSlider):
    """自定义滑块，鼠标滚轮滚动一次数字变1"""

    def wheelEvent(self, event: QWheelEvent):
        """重写滚轮事件，让滚动一次数字变1"""
        # 获取滚轮滚动方向
        delta = event.angleDelta().y()

        # 根据滚动方向增加或减少1
        if delta > 0:
            self.setValue(self.value() + 1)
        elif delta < 0:
            self.setValue(self.value() - 1)

        # 接受事件，防止传递给父控件
        event.accept()


class PropertyPanel(QWidget):
    """
    左侧属性面板，功能完整版。
    """
    # --- Define all required signals ---
    translated_text_modified = pyqtSignal(int, str)
    translation_raw_modified = pyqtSignal(int, str)
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

    def __init__(self, model, app_logic, parent=None):
        super().__init__(parent)
        self.model = model
        self.app_logic = app_logic
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()

        self._init_ui()
        self._connect_signals()
        self._connect_model_signals() # Connect to model signals
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
        from PyQt6.QtWidgets import QScrollArea
        scroll_area = QScrollArea()
        scroll_area.setObjectName("editor_property_scroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        
        # 创建内容容器
        content_widget = QWidget()
        content_widget.setObjectName("editor_property_content")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 2, 4, 2)
        content_layout.setSpacing(10)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self._create_region_info_section(content_layout)
        self._create_mask_edit_section(content_layout)
        self._create_text_section(content_layout)
        self._create_style_section(content_layout)
        self._create_action_section(content_layout)

        # 添加一个弹性空间，将所有内容向上推，使布局更紧凑
        content_layout.addStretch()
        
        # 将内容容器放入滚动区域
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # 不再使用语法高亮器,改用符号替换
        # self.highlighter = HorizontalTagHighlighter(self.translated_text_box.document())

    def _set_selection_controls_blocked(self, blocked: bool):
        """统一阻止/恢复与区域样式相关控件信号，避免切换选区时误写回。"""
        for child in self.findChildren(QWidget):
            if isinstance(child, (QLineEdit, QTextEdit, QComboBox, QSlider, QAbstractSpinBox)):
                child.blockSignals(blocked)

    def _create_region_info_section(self, layout):
        self.info_group = QGroupBox(self._t("Region Info"))
        self.info_group.setObjectName("editor_info_group")
        info_layout = QFormLayout(self.info_group)
        info_layout.setContentsMargins(8, 8, 8, 6)
        info_layout.setHorizontalSpacing(10)
        info_layout.setVerticalSpacing(6)
        self.index_label = QLabel("-")
        self.bbox_label = QLabel("-")
        self.size_label = QLabel("-")
        self.angle_label = QLabel("-")
        self.index_row_label = QLabel(self._t("Index:"))
        self.bbox_row_label = QLabel(self._t("Position:"))
        self.size_row_label = QLabel(self._t("Size:"))
        self.angle_row_label = QLabel(self._t("Angle:"))
        info_layout.addRow(self.index_row_label, self.index_label)
        info_layout.addRow(self.bbox_row_label, self.bbox_label)
        info_layout.addRow(self.size_row_label, self.size_label)
        info_layout.addRow(self.angle_row_label, self.angle_label)
        layout.addWidget(self.info_group)

    def _create_mask_edit_section(self, layout):
        self.mask_edit_frame = QGroupBox(self._t("Mask Editing"))
        self.mask_edit_frame.setObjectName("editor_mask_group")
        frame_layout = QVBoxLayout(self.mask_edit_frame)
        frame_layout.setContentsMargins(6, 8, 6, 6)
        frame_layout.setSpacing(6)

        self.paint_tab_widget = QTabWidget(self.mask_edit_frame)
        self.paint_tab_widget.setObjectName("editor_paint_tabs")

        # 选择按钮组（两个 tab 共享同一个按钮组，保持互斥）
        self.mask_tool_group = QButtonGroup(self)
        self.mask_tool_group.setExclusive(True)

        # ======= Tab 1：蒙版 =======
        mask_tab = QWidget()
        mask_tab.setObjectName("editor_mask_tab")
        mask_layout = QVBoxLayout(mask_tab)
        mask_layout.setContentsMargins(6, 8, 6, 6)
        mask_layout.setSpacing(8)

        mask_tools_layout = QHBoxLayout()
        mask_tools_layout.setContentsMargins(0, 0, 0, 0)
        mask_tools_layout.setSpacing(6)

        self.brush_button = QPushButton(self._t("Brush"))
        self.brush_button.setObjectName("editor_mask_brush_button")
        self.brush_button.setProperty("editorToolButton", True)
        self.brush_button.setProperty("softAction", True)
        self.brush_button.setCheckable(True)
        set_hover_hint(self.brush_button, self._t("Brush Tool") + " (W)")
        self.eraser_button = QPushButton(self._t("Eraser"))
        self.eraser_button.setObjectName("editor_mask_eraser_button")
        self.eraser_button.setProperty("editorToolButton", True)
        self.eraser_button.setProperty("softAction", True)
        self.eraser_button.setCheckable(True)
        set_hover_hint(self.eraser_button, self._t("Eraser Tool") + " (E)")
        self.select_button = QPushButton(self._t("No Selection"))
        self.select_button.setObjectName("editor_mask_select_button")
        self.select_button.setProperty("editorToolButton", True)
        self.select_button.setProperty("softAction", True)
        self.select_button.setCheckable(True)
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
        self.brush_size_title_label = QLabel(self._t("Brush Size:"))
        brush_size_layout.addWidget(self.brush_size_title_label)
        self.brush_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.brush_size_slider.setObjectName("editor_brush_size_slider")
        self.brush_size_slider.setRange(5, 200)
        self.brush_size_value_label = QLabel("30")
        self.brush_size_value_label.setObjectName("editor_brush_size_value_label")
        self.brush_size_value_label.setFixedWidth(28)
        self.brush_size_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.brush_size_slider.setValue(30)
        brush_size_layout.addWidget(self.brush_size_slider)
        brush_size_layout.addWidget(self.brush_size_value_label)
        mask_layout.addLayout(brush_size_layout)

        self.show_refined_mask_checkbox = QCheckBox(self._t("Show Refined Mask"))
        self.show_refined_mask_checkbox.setChecked(False)
        mask_layout.addWidget(self.show_refined_mask_checkbox)

        self.clear_all_masks_button = QPushButton(self._t("Clear All Masks"))
        self.clear_all_masks_button.setObjectName("editor_clear_masks_button")
        self.clear_all_masks_button.setProperty("softAction", True)
        mask_layout.addWidget(self.clear_all_masks_button)
        mask_layout.addStretch()

        # ======= Tab 2：画笔（彩色涂鸦） =======
        paint_tab = QWidget()
        paint_tab.setObjectName("editor_paint_tab")
        paint_layout = QVBoxLayout(paint_tab)
        paint_layout.setContentsMargins(6, 8, 6, 6)
        paint_layout.setSpacing(8)

        paint_tools_layout = QHBoxLayout()
        paint_tools_layout.setContentsMargins(0, 0, 0, 0)
        paint_tools_layout.setSpacing(6)

        self.paint_select_button = QPushButton(self._t("No Selection"))
        self.paint_select_button.setObjectName("editor_paint_select_button")
        self.paint_select_button.setProperty("editorToolButton", True)
        self.paint_select_button.setProperty("softAction", True)
        self.paint_select_button.setCheckable(True)
        set_hover_hint(self.paint_select_button, self._t("Selection Tool") + " (Q)")

        self.paint_brush_button = QPushButton(self._t("Brush"))
        self.paint_brush_button.setObjectName("editor_paint_brush_button")
        self.paint_brush_button.setProperty("editorToolButton", True)
        self.paint_brush_button.setProperty("softAction", True)
        self.paint_brush_button.setCheckable(True)
        set_hover_hint(self.paint_brush_button, self._t("Brush Tool"))

        self.paint_eraser_button = QPushButton(self._t("Eraser"))
        self.paint_eraser_button.setObjectName("editor_paint_eraser_button")
        self.paint_eraser_button.setProperty("editorToolButton", True)
        self.paint_eraser_button.setProperty("softAction", True)
        self.paint_eraser_button.setCheckable(True)
        set_hover_hint(self.paint_eraser_button, self._t("Eraser Tool"))

        # 复用同一个互斥按钮组，保证和蒙版页工具互相切换时正确取消选中
        self.mask_tool_group.addButton(self.paint_select_button, 3)
        self.mask_tool_group.addButton(self.paint_brush_button, 4)
        self.mask_tool_group.addButton(self.paint_eraser_button, 5)
        for button in (self.paint_select_button, self.paint_brush_button, self.paint_eraser_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        paint_tools_layout.addWidget(self.paint_select_button)
        paint_tools_layout.addWidget(self.paint_brush_button)
        paint_tools_layout.addWidget(self.paint_eraser_button)
        paint_layout.addLayout(paint_tools_layout)

        # 画笔大小（与蒙版页共享同一个模型字段）
        paint_size_layout = QHBoxLayout()
        paint_size_layout.setContentsMargins(0, 0, 0, 0)
        paint_size_layout.setSpacing(6)
        self.paint_size_title_label = QLabel(self._t("Brush Size:"))
        paint_size_layout.addWidget(self.paint_size_title_label)
        self.paint_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.paint_size_slider.setObjectName("editor_paint_size_slider")
        self.paint_size_slider.setRange(5, 200)
        self.paint_size_slider.setValue(30)
        self.paint_size_value_label = QLabel("30")
        self.paint_size_value_label.setObjectName("editor_paint_size_value_label")
        self.paint_size_value_label.setFixedWidth(28)
        self.paint_size_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        paint_size_layout.addWidget(self.paint_size_slider)
        paint_size_layout.addWidget(self.paint_size_value_label)
        paint_layout.addLayout(paint_size_layout)

        # 画笔颜色（复用 ColorPickerWidget）
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self.paint_color_label = QLabel(self._t("Brush Color:"))
        color_row.addWidget(self.paint_color_label)
        self.paint_color_picker = ColorPickerWidget(
            dialog_title="Select brush color",
            default_color="#ffffff",
            config_key="saved_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.paint_color_picker.setFixedWidth(96)
        color_row.addWidget(self.paint_color_picker)
        color_row.addStretch()
        paint_layout.addLayout(color_row)

        self.clear_paint_overlay_button = QPushButton(self._t("Clear Paint Layer"))
        self.clear_paint_overlay_button.setObjectName("editor_clear_paint_button")
        self.clear_paint_overlay_button.setProperty("softAction", True)
        paint_layout.addWidget(self.clear_paint_overlay_button)
        paint_layout.addStretch()

        self.paint_tab_widget.addTab(mask_tab, self._t("Mask"))
        self.paint_tab_widget.addTab(paint_tab, self._t("Paint"))
        frame_layout.addWidget(self.paint_tab_widget)
        layout.addWidget(self.mask_edit_frame)

    def _create_text_section(self, layout):
        self.text_edit_frame = QGroupBox(self._t("Text Content"))
        self.text_edit_frame.setObjectName("editor_text_group")
        text_layout = QVBoxLayout(self.text_edit_frame)
        text_layout.setContentsMargins(8, 8, 8, 6)
        text_layout.setSpacing(8)
        ocr_trans_config_layout = QFormLayout()
        ocr_trans_config_layout.setContentsMargins(0, 0, 0, 0)
        ocr_trans_config_layout.setHorizontalSpacing(8)
        ocr_trans_config_layout.setVerticalSpacing(8)
        self.ocr_model_combo = QComboBox()
        self.ocr_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ocr_model_combo.setMinimumContentsLength(8)
        self.translator_combo = QComboBox()
        self.translator_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.translator_combo.setMinimumContentsLength(8)
        self.translator_combo.setMinimumWidth(110)
        self.target_language_combo = QComboBox()
        self.target_language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target_language_combo.setMinimumContentsLength(8)
        ocr_row = QHBoxLayout()
        ocr_row.setContentsMargins(0, 0, 0, 0)
        ocr_row.setSpacing(6)
        ocr_row.addWidget(self.ocr_model_combo)
        self.ocr_button = QPushButton(self._t("Recognize"))
        self.ocr_button.setObjectName("editor_recognize_button")
        self.ocr_button.setProperty("softAction", True)
        self.ocr_button.setMinimumWidth(72)
        self.ocr_button.setMaximumWidth(92)
        ocr_row.addWidget(self.ocr_button)
        translator_row = QHBoxLayout()
        translator_row.setContentsMargins(0, 0, 0, 0)
        translator_row.setSpacing(6)
        translator_row.addWidget(self.translator_combo)
        self.translate_button = QPushButton(self._t("Translate"))
        self.translate_button.setObjectName("editor_translate_button")
        self.translate_button.setProperty("softAction", True)
        self.translate_button.setMinimumWidth(80)
        self.translate_button.setMaximumWidth(108)
        translator_row.addWidget(self.translate_button)
        self.translator_row_label = QLabel(self._t("Translator:"))
        self.target_lang_row_label = QLabel(self._t("Target Language:"))
        ocr_trans_config_layout.addRow(self._t("OCR Model:"), ocr_row)
        ocr_trans_config_layout.addRow(self.translator_row_label, translator_row)
        ocr_trans_config_layout.addRow(self.target_lang_row_label, self.target_language_combo)
        text_layout.addLayout(ocr_trans_config_layout)
        
        # 原文文本框
        self.original_text_box = QTextEdit()
        self.original_text_box.setUndoRedoEnabled(True)
        self.original_text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.original_text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.original_text_box.setMinimumHeight(72)
        self.original_text_box.setMaximumHeight(132)
        
        self.translated_text_box = QTextEdit()
        self.translated_text_box.setObjectName("translationEdit")
        self.translated_text_box.setUndoRedoEnabled(True)
        self.translated_text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.translated_text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.translated_text_box.setMinimumHeight(72)
        self.translated_text_box.setMaximumHeight(132)

        self.original_text_label = QLabel(self._t("Original Text:"))
        text_layout.addWidget(self.original_text_label)
        text_layout.addWidget(self.original_text_box)
        # 复选框:勾选时让"译文"框显示"替换前译文"(translation_raw),编辑会实时跑替换写回译文
        self.translation_raw_checkbox = QCheckBox(self._t("Show Translation (Raw)"))
        self.translation_raw_checkbox.setChecked(True)
        self.translation_raw_checkbox.toggled.connect(self._on_translation_raw_mode_toggled)
        text_layout.addWidget(self.translation_raw_checkbox)
        self.translated_text_label = QLabel(self._t("Translated Text:"))
        text_layout.addWidget(self.translated_text_label)
        text_layout.addWidget(self.translated_text_box)
        insert_buttons_layout = QHBoxLayout()
        insert_buttons_layout.setContentsMargins(0, 0, 0, 0)
        insert_buttons_layout.setSpacing(6)
        self.insert_placeholder_button = QPushButton(self._t("Placeholder"))
        self.insert_placeholder_button.setProperty("chipButton", True)
        set_hover_hint(self.insert_placeholder_button, self._t("Insert placeholder ＿"))
        self.insert_newline_button = QPushButton(self._t("Newline↵"))
        self.insert_newline_button.setProperty("chipButton", True)
        set_hover_hint(self.insert_newline_button, self._t("Insert newline"))
        self.mark_horizontal_button = QPushButton(self._t("Horizontal⇄"))
        self.mark_horizontal_button.setProperty("chipButton", True)
        set_hover_hint(self.mark_horizontal_button, self._t("Mark selected text as horizontal display"))
        for button in (self.insert_placeholder_button, self.insert_newline_button, self.mark_horizontal_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        insert_buttons_layout.addWidget(self.insert_placeholder_button)
        insert_buttons_layout.addWidget(self.insert_newline_button)
        insert_buttons_layout.addWidget(self.mark_horizontal_button)
        text_layout.addLayout(insert_buttons_layout)
        self.text_stats_label = QLabel(self._t("Character count: 0"))
        text_layout.addWidget(self.text_stats_label)
        layout.addWidget(self.text_edit_frame)

    def _create_style_section(self, layout):
        self.style_edit_frame = QGroupBox(self._t("Style Settings"))
        self.style_edit_frame.setObjectName("editor_style_group")
        style_layout = QFormLayout(self.style_edit_frame)
        style_layout.setContentsMargins(8, 8, 8, 6)
        style_layout.setHorizontalSpacing(8)
        style_layout.setVerticalSpacing(8)

        preset_widget = QWidget()
        preset_layout = QHBoxLayout(preset_widget)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)
        self.style_preset_combo = QComboBox()
        self.style_preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.style_preset_combo.setMinimumContentsLength(12)
        preset_layout.addWidget(self.style_preset_combo, 1)
        self.save_style_preset_button = QPushButton()
        self.save_style_preset_button.setObjectName("editor_style_preset_save_button")
        self.save_style_preset_button.setProperty("chipButton", True)
        self.save_style_preset_button.setFixedSize(30, 30)
        self.save_style_preset_button.setIconSize(QSize(16, 16))
        preset_layout.addWidget(self.save_style_preset_button)
        self.delete_style_preset_button = QPushButton()
        self.delete_style_preset_button.setObjectName("editor_style_preset_delete_button")
        self.delete_style_preset_button.setProperty("chipButton", True)
        self.delete_style_preset_button.setFixedSize(30, 30)
        self.delete_style_preset_button.setIconSize(QSize(16, 16))
        preset_layout.addWidget(self.delete_style_preset_button)
        self._refresh_style_preset_action_buttons()
        self.style_preset_label = QLabel(self._t("Style Preset:"))
        style_layout.addRow(self.style_preset_label, preset_widget)
        self._refresh_style_preset_combo()
        
        # Font family selector with refresh capability
        class RefreshableComboBox(QComboBox):
            """可刷新的下拉框，在下拉时自动刷新字体列表"""
            def __init__(self, parent_widget, parent=None):
                super().__init__(parent)
                self.parent_widget = parent_widget
            
            def showPopup(self):
                # 保存当前选中的文本
                current_text = self.currentText()
                current_data = self.itemData(self.currentIndex())
                
                # 刷新字体列表
                self.blockSignals(True)
                try:
                    self.parent_widget._populate_font_list()

                    # 恢复之前选择的值
                    restored_index = -1
                    if isinstance(current_data, str) and current_data:
                        import os
                        current_filename = os.path.basename(current_data)
                        for i in range(self.count()):
                            item_data = self.itemData(i)
                            if item_data == current_data or item_data == current_filename:
                                restored_index = i
                                break

                        # 外部字体不在 fonts 目录时，保留并恢复当前项
                        if restored_index < 0:
                            display_name = os.path.splitext(current_filename)[0] or current_text or current_data
                            self.addItem(display_name, current_data)
                            restored_index = self.count() - 1
                    elif current_text:
                        restored_index = self.findText(current_text)

                    self.setCurrentIndex(restored_index if restored_index >= 0 else -1)
                finally:
                    self.blockSignals(False)
                
                super().showPopup()
        
        self.font_family_combo = RefreshableComboBox(self)
        self.font_family_combo.setEditable(False)
        self.font_family_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_family_combo.setMinimumContentsLength(10)
        self._populate_font_list()
        self.font_label = QLabel(self._t("Font:"))
        style_layout.addRow(self.font_label, self.font_family_combo)
        
        # Font size
        font_size_layout = QHBoxLayout()
        font_size_layout.setContentsMargins(0, 0, 0, 0)
        font_size_layout.setSpacing(6)
        self.font_size_input = QLineEdit()
        self.font_size_input.setValidator(QIntValidator(8, 1000, self))
        self.font_size_input.setFixedWidth(64)
        font_size_layout.addWidget(self.font_size_input)
        self.font_size_slider = CustomSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setObjectName("editor_font_size_slider")
        self.font_size_slider.setRange(8, 150)
        self.font_size_label = QLabel(self._t("Font Size:"))
        style_layout.addRow(self.font_size_label, font_size_layout)
        style_layout.addRow("", self.font_size_slider)
        
        # Font color
        self.font_color_picker = ColorPickerWidget(
            dialog_title="Select font color",
            default_color="#000000",
            config_key="saved_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.font_color_picker.setFixedWidth(96)
        self.font_color_label = QLabel(self._t("Font Color:"))
        style_layout.addRow(self.font_color_label, self.font_color_picker)

        # Stroke color (描边颜色)
        self.stroke_color_picker = ColorPickerWidget(
            dialog_title="Select stroke color",
            default_color="#ffffff",
            config_key="saved_stroke_colors",
            config_service=self.config_service,
            i18n_func=self._t,
        )
        self.stroke_color_picker.setFixedWidth(96)
        self.stroke_color_label = QLabel(self._t("Stroke Color:"))
        style_layout.addRow(self.stroke_color_label, self.stroke_color_picker)

        # Stroke width (描边宽度)
        stroke_width_layout = QHBoxLayout()
        stroke_width_layout.setContentsMargins(0, 0, 0, 0)
        self.stroke_width_spinbox = QDoubleSpinBox()
        self.stroke_width_spinbox.setRange(0.0, 1.0)
        self.stroke_width_spinbox.setSingleStep(0.01)
        self.stroke_width_spinbox.setDecimals(2)
        self.stroke_width_spinbox.setValue(0.07)
        self.stroke_width_spinbox.setMaximumWidth(96)
        stroke_width_layout.addWidget(self.stroke_width_spinbox)
        self.stroke_width_label = QLabel(self._t("Stroke Width:"))
        style_layout.addRow(self.stroke_width_label, stroke_width_layout)
        
        # Line spacing (行间距倍率)
        line_spacing_layout = QHBoxLayout()
        line_spacing_layout.setContentsMargins(0, 0, 0, 0)
        self.line_spacing_spinbox = QDoubleSpinBox()
        self.line_spacing_spinbox.setRange(0.1, 5.0)
        self.line_spacing_spinbox.setSingleStep(0.1)
        self.line_spacing_spinbox.setDecimals(1)
        self.line_spacing_spinbox.setValue(1.0)
        self.line_spacing_spinbox.setMaximumWidth(96)
        line_spacing_layout.addWidget(self.line_spacing_spinbox)
        self.line_spacing_label = QLabel(self._t("Line Spacing:"))
        style_layout.addRow(self.line_spacing_label, line_spacing_layout)

        letter_spacing_layout = QHBoxLayout()
        letter_spacing_layout.setContentsMargins(0, 0, 0, 0)
        self.letter_spacing_spinbox = QDoubleSpinBox()
        self.letter_spacing_spinbox.setRange(0.1, 5.0)
        self.letter_spacing_spinbox.setSingleStep(0.1)
        self.letter_spacing_spinbox.setDecimals(1)
        self.letter_spacing_spinbox.setValue(1.0)
        self.letter_spacing_spinbox.setMaximumWidth(96)
        letter_spacing_layout.addWidget(self.letter_spacing_spinbox)
        self.letter_spacing_label = QLabel(self._t("Letter Spacing:"))
        style_layout.addRow(self.letter_spacing_label, letter_spacing_layout)

        angle_layout = QHBoxLayout()
        angle_layout.setContentsMargins(0, 0, 0, 0)
        self.angle_spinbox = QDoubleSpinBox()
        self.angle_spinbox.setRange(-9999.0, 9999.0)
        self.angle_spinbox.setSingleStep(1.0)
        self.angle_spinbox.setDecimals(1)
        self.angle_spinbox.setKeyboardTracking(False)
        self.angle_spinbox.setSuffix("°")
        self.angle_spinbox.setValue(0.0)
        self.angle_spinbox.setMaximumWidth(110)
        angle_layout.addWidget(self.angle_spinbox)
        self.angle_style_label = QLabel(self._t("Angle:"))
        style_layout.addRow(self.angle_style_label, angle_layout)
        
        # Alignment and direction
        self.alignment_combo = QComboBox()
        self.direction_combo = QComboBox()
        self.alignment_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.alignment_combo.setMinimumContentsLength(6)
        self.direction_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.direction_combo.setMinimumContentsLength(6)
        self.alignment_label = QLabel(self._t("Alignment:"))
        self.direction_label = QLabel(self._t("Direction:"))
        style_layout.addRow(self.alignment_label, self.alignment_combo)
        style_layout.addRow(self.direction_label, self.direction_combo)
        
        layout.addWidget(self.style_edit_frame)
    
    def _populate_font_list(self):
        """Populate font combo box with available fonts from fonts folder"""
        import os

        from manga_translator.utils import BASE_PATH
        
        # 清空现有列表
        self.font_family_combo.clear()
        
        fonts_dir = os.path.join(BASE_PATH, 'fonts')
        font_files = []
        
        if os.path.exists(fonts_dir):
            for filename in os.listdir(fonts_dir):
                if filename.lower().endswith(('.ttf', '.otf', '.ttc')):
                    # Display name without extension
                    display_name = os.path.splitext(filename)[0]
                    font_files.append((display_name, filename))
        
        # Sort by display name
        font_files.sort(key=lambda x: x[0])

        # Add font files
        for display_name, filename in font_files:
            self.font_family_combo.addItem(display_name, filename)

    def _create_action_section(self, layout):
        self.action_frame = QGroupBox(self._t("Actions"))
        self.action_frame.setObjectName("editor_action_group")
        action_layout = QHBoxLayout(self.action_frame)
        action_layout.setContentsMargins(8, 8, 8, 6)
        action_layout.setSpacing(6)
        self.copy_button = QPushButton(self._t("Copy"))
        self.copy_button.setObjectName("editor_copy_action_button")
        self.copy_button.setProperty("softAction", True)
        set_hover_hint(self.copy_button, self._t("Copy") + " (Ctrl+C)")
        self.paste_button = QPushButton(self._t("Paste"))
        self.paste_button.setObjectName("editor_paste_action_button")
        self.paste_button.setProperty("softAction", True)
        set_hover_hint(self.paste_button, self._t("Paste") + " (Ctrl+V)")
        self.delete_button = QPushButton(self._t("Delete"))
        self.delete_button.setObjectName("editor_delete_action_button")
        self.delete_button.setProperty("variant", "danger")
        set_hover_hint(self.delete_button, self._t("Delete") + " (Del)")
        action_layout.addWidget(self.copy_button)
        action_layout.addWidget(self.paste_button)
        action_layout.addWidget(self.delete_button)
        # 添加弹性空间，将按钮推向左侧，使它们更紧凑
        action_layout.addStretch()
        layout.addWidget(self.action_frame)
    
    def _connect_signals(self):
        # Mask
        self.mask_tool_group.buttonClicked.connect(self._on_mask_tool_changed)
        self.brush_size_slider.valueChanged.connect(self._on_brush_size_changed)
        self.paint_size_slider.valueChanged.connect(self._on_brush_size_changed)
        self.show_refined_mask_checkbox.stateChanged.connect(lambda state: self.toggle_mask_visibility.emit(bool(state)))
        self.clear_all_masks_button.clicked.connect(self.clear_all_masks_requested.emit)

        # Paint overlay
        self.paint_color_picker.color_changed.connect(self._on_paint_color_changed)
        self.clear_paint_overlay_button.clicked.connect(self.clear_paint_overlay_requested.emit)
        self.paint_tab_widget.currentChanged.connect(self._on_paint_tab_changed)

        # Style
        self.font_family_combo.currentIndexChanged.connect(self._on_font_family_changed)
        self.font_size_input.editingFinished.connect(self._on_font_size_editing_finished)
        self.font_size_slider.valueChanged.connect(self._on_font_size_slider_changed)
        self.font_color_picker.color_changed.connect(self._on_font_color_changed)
        self.stroke_color_picker.color_changed.connect(self._on_stroke_color_changed)
        self.stroke_width_spinbox.valueChanged.connect(self._on_stroke_width_changed)
        self.line_spacing_spinbox.valueChanged.connect(self._on_line_spacing_changed)
        self.letter_spacing_spinbox.valueChanged.connect(self._on_letter_spacing_changed)
        self.angle_spinbox.valueChanged.connect(self._on_angle_changed)
        self.style_preset_combo.activated.connect(self._on_style_preset_activated)
        self.save_style_preset_button.clicked.connect(self._on_save_style_preset_clicked)
        self.delete_style_preset_button.clicked.connect(self._on_delete_style_preset_clicked)
        # 实时更新（textChanged）
        self.translated_text_box.textChanged.connect(self._on_translated_text_changed)
        # self.translated_text_box.focusOutEvent = self._make_focus_out_handler(self.translated_text_box, self._on_translated_text_focus_out)
        self.alignment_combo.currentTextChanged.connect(self._on_alignment_changed)
        self.direction_combo.currentTextChanged.connect(self._on_direction_changed)

        # Text
        # 实时更新（textChanged）
        self.original_text_box.textChanged.connect(self._on_original_text_changed)
        # self.original_text_box.focusOutEvent = self._make_focus_out_handler(self.original_text_box, self._on_original_text_focus_out)
        self.ocr_model_combo.currentTextChanged.connect(self._on_ocr_model_change)
        self.translator_combo.currentTextChanged.connect(self._on_translator_change)
        self.target_language_combo.currentTextChanged.connect(self._on_target_language_change)
        self.ocr_button.clicked.connect(self.ocr_requested.emit)
        self.translate_button.clicked.connect(self.translation_requested.emit)
        self.insert_placeholder_button.clicked.connect(self._insert_placeholder)
        self.insert_newline_button.clicked.connect(self._insert_newline)
        self.mark_horizontal_button.clicked.connect(self._mark_horizontal)
        
        # Action buttons
        self.copy_button.clicked.connect(self.copy_region_requested.emit)
        self.paste_button.clicked.connect(self.paste_region_requested.emit)
        self.delete_button.clicked.connect(self.delete_region_requested.emit)
    def _connect_model_signals(self):
        self.model.display_mask_type_changed.connect(self._on_display_mask_type_changed)
        self.model.refined_mask_changed.connect(self._on_refined_mask_changed)
        self.model.regions_changed.connect(self.on_regions_updated)
        self.model.region_style_updated.connect(self.on_single_region_updated)

    def _on_display_mask_type_changed(self, mask_type: str):
        """响应显示蒙版类型变化"""
        # Block signals to prevent recursive calls
        self.show_refined_mask_checkbox.blockSignals(True)
        self.show_refined_mask_checkbox.setChecked(mask_type == 'refined')
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

        # OCR - 阻止信号避免触发不必要的配置更新
        self.ocr_model_combo.blockSignals(True)
        ocr_options = self.app_logic.get_options_for_key('ocr')
        if ocr_options:
            self.ocr_model_combo.clear()
            self.ocr_model_combo.addItems(ocr_options)
            current_ocr = ocr_config.ocr
            if current_ocr in ocr_options:
                self.ocr_model_combo.setCurrentText(current_ocr)
        self.ocr_model_combo.blockSignals(False)

        # Translator - 阻止信号避免触发不必要的配置更新
        self.translator_combo.blockSignals(True)
        translator_map = self.app_logic.get_display_mapping('translator')
        if translator_map:
            self.translator_display_to_key = {v: k for k, v in translator_map.items()}
            self.translator_combo.clear()
            self.translator_combo.addItems(list(translator_map.values()))
            current_translator_key = translator_config.translator
            current_translator_display = translator_map.get(current_translator_key)
            if current_translator_display:
                self.translator_combo.setCurrentText(current_translator_display)
        self.translator_combo.blockSignals(False)

        # Target Language - 阻止信号避免触发不必要的配置更新
        self.target_language_combo.blockSignals(True)
        lang_map = self.app_logic.get_display_mapping('target_lang')
        if lang_map:
            self.lang_name_to_code = {v: k for k, v in lang_map.items()}
            self.target_language_combo.clear()
            self.target_language_combo.addItems(list(lang_map.values()))
            current_lang_key = translator_config.target_lang
            current_lang_display = lang_map.get(current_lang_key)
            if current_lang_display:
                self.target_language_combo.setCurrentText(current_lang_display)
        self.target_language_combo.blockSignals(False)

        # Alignment
        alignment_map = self.app_logic.get_display_mapping('alignment')
        if alignment_map:
            self.alignment_combo.clear()
            self.alignment_combo.addItems(list(alignment_map.values()))

        # Direction
        direction_map = self.app_logic.get_display_mapping('direction')
        if direction_map:
            self.direction_combo.clear()
            direction_items = [v for k, v in direction_map.items() if k != 'auto']
            self.direction_combo.addItems(direction_items)
    
    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）"""
        # 刷新分组框标题
        if hasattr(self, 'info_group'):
            self.info_group.setTitle(self._t("Region Info"))
        if hasattr(self, 'mask_edit_frame'):
            self.mask_edit_frame.setTitle(self._t("Mask Editing"))
        if hasattr(self, 'text_edit_frame'):
            self.text_edit_frame.setTitle(self._t("Text Content"))
        if hasattr(self, 'style_edit_frame'):
            self.style_edit_frame.setTitle(self._t("Style Settings"))
        if hasattr(self, 'action_frame'):
            self.action_frame.setTitle(self._t("Actions"))
        
        # 刷新标签
        if hasattr(self, 'index_row_label'):
            self.index_row_label.setText(self._t("Index:"))
        if hasattr(self, 'bbox_row_label'):
            self.bbox_row_label.setText(self._t("Position:"))
        if hasattr(self, 'size_row_label'):
            self.size_row_label.setText(self._t("Size:"))
        if hasattr(self, 'angle_row_label'):
            self.angle_row_label.setText(self._t("Angle:"))
        if hasattr(self, 'brush_size_title_label'):
            self.brush_size_title_label.setText(self._t("Brush Size:"))
        if hasattr(self, 'translator_row_label'):
            self.translator_row_label.setText(self._t("Translator:"))
        if hasattr(self, 'target_lang_row_label'):
            self.target_lang_row_label.setText(self._t("Target Language:"))
        if hasattr(self, 'font_label'):
            self.font_label.setText(self._t("Font:"))
        if hasattr(self, 'style_preset_label'):
            self.style_preset_label.setText(self._t("Style Preset:"))
        if hasattr(self, 'font_size_label'):
            self.font_size_label.setText(self._t("Font Size:"))
        if hasattr(self, 'font_color_label'):
            self.font_color_label.setText(self._t("Font Color:"))
        if hasattr(self, 'stroke_color_label'):
            self.stroke_color_label.setText(self._t("Stroke Color:"))

        # 刷新颜色选择器内部文本
        if hasattr(self, 'font_color_picker'):
            self.font_color_picker.refresh_ui_texts()
        if hasattr(self, 'stroke_color_picker'):
            self.stroke_color_picker.refresh_ui_texts()

        if hasattr(self, 'stroke_width_label'):
            self.stroke_width_label.setText(self._t("Stroke Width:"))
        if hasattr(self, 'line_spacing_label'):
            self.line_spacing_label.setText(self._t("Line Spacing:"))
        if hasattr(self, 'letter_spacing_label'):
            self.letter_spacing_label.setText(self._t("Letter Spacing:"))
        if hasattr(self, 'angle_style_label'):
            self.angle_style_label.setText(self._t("Angle:"))
        if hasattr(self, 'alignment_label'):
            self.alignment_label.setText(self._t("Alignment:"))
        if hasattr(self, 'direction_label'):
            self.direction_label.setText(self._t("Direction:"))
        if hasattr(self, 'original_text_label'):
            self.original_text_label.setText(self._t("Original Text:"))
        if hasattr(self, 'translation_raw_checkbox'):
            self.translation_raw_checkbox.setText(self._t("Show Translation (Raw)"))
        if hasattr(self, 'translated_text_label'):
            self.translated_text_label.setText(self._t("Translated Text:"))
        if hasattr(self, 'text_stats_label'):
            self.text_stats_label.setText(self._t("Character count: 0"))
        
        # 刷新按钮
        if hasattr(self, 'ocr_button'):
            self.ocr_button.setText(self._t("Recognize"))
        if hasattr(self, 'translate_button'):
            self.translate_button.setText(self._t("Translate"))
        if hasattr(self, 'brush_button'):
            self.brush_button.setText(self._t("Brush"))
            set_hover_hint(self.brush_button, self._t("Brush Tool") + " (W)")
        if hasattr(self, 'eraser_button'):
            self.eraser_button.setText(self._t("Eraser"))
            set_hover_hint(self.eraser_button, self._t("Eraser Tool") + " (E)")
        if hasattr(self, 'select_button'):
            self.select_button.setText(self._t("No Selection"))
            set_hover_hint(self.select_button, self._t("Selection Tool") + " (Q)")
        if hasattr(self, 'paint_select_button'):
            self.paint_select_button.setText(self._t("No Selection"))
            set_hover_hint(self.paint_select_button, self._t("Selection Tool") + " (Q)")
        if hasattr(self, 'paint_brush_button'):
            self.paint_brush_button.setText(self._t("Brush"))
            set_hover_hint(self.paint_brush_button, self._t("Brush Tool"))
        if hasattr(self, 'paint_eraser_button'):
            self.paint_eraser_button.setText(self._t("Eraser"))
            set_hover_hint(self.paint_eraser_button, self._t("Eraser Tool"))
        if hasattr(self, 'paint_size_title_label'):
            self.paint_size_title_label.setText(self._t("Brush Size:"))
        if hasattr(self, 'paint_color_label'):
            self.paint_color_label.setText(self._t("Brush Color:"))
        if hasattr(self, 'paint_color_picker'):
            self.paint_color_picker.refresh_ui_texts()
        if hasattr(self, 'paint_tab_widget'):
            self.paint_tab_widget.setTabText(0, self._t("Mask"))
            self.paint_tab_widget.setTabText(1, self._t("Paint"))
        if hasattr(self, 'clear_paint_overlay_button'):
            self.clear_paint_overlay_button.setText(self._t("Clear Paint Layer"))
        if hasattr(self, 'insert_placeholder_button'):
            self.insert_placeholder_button.setText(self._t("Placeholder"))
            set_hover_hint(self.insert_placeholder_button, self._t("Insert placeholder ＿"))
        if hasattr(self, 'insert_newline_button'):
            self.insert_newline_button.setText(self._t("Newline↵"))
            set_hover_hint(self.insert_newline_button, self._t("Insert newline"))
        if hasattr(self, 'mark_horizontal_button'):
            self.mark_horizontal_button.setText(self._t("Horizontal⇄"))
            set_hover_hint(self.mark_horizontal_button, self._t("Mark selected text as horizontal display"))
        if hasattr(self, 'copy_button'):
            self.copy_button.setText(self._t("Copy"))
            set_hover_hint(self.copy_button, self._t("Copy") + " (Ctrl+C)")
        if hasattr(self, 'paste_button'):
            self.paste_button.setText(self._t("Paste"))
            set_hover_hint(self.paste_button, self._t("Paste") + " (Ctrl+V)")
        if hasattr(self, 'delete_button'):
            self.delete_button.setText(self._t("Delete"))
            set_hover_hint(self.delete_button, self._t("Delete") + " (Del)")
        if hasattr(self, 'save_style_preset_button') or hasattr(self, 'delete_style_preset_button'):
            self._refresh_style_preset_action_buttons()
        
        # 刷新复选框
        if hasattr(self, 'show_refined_mask_checkbox'):
            self.show_refined_mask_checkbox.setText(self._t("Show Refined Mask"))
        if hasattr(self, 'clear_all_masks_button'):
            self.clear_all_masks_button.setText(self._t("Clear All Masks"))
        
        # 刷新下拉菜单（重新填充以使用新的翻译）
        self._refresh_combo_boxes()
        self._refresh_style_preset_combo()
    
    def _refresh_combo_boxes(self):
        """刷新所有下拉菜单的选项"""
        # 保存当前选中的索引（而不是文本，因为文本会随语言变化）
        current_translator_index = self.translator_combo.currentIndex()
        current_target_lang_index = self.target_language_combo.currentIndex()
        current_alignment_index = self.alignment_combo.currentIndex()
        current_direction_index = self.direction_combo.currentIndex()
        
        # 重新填充翻译器下拉菜单
        translator_map = self.app_logic.get_display_mapping('translator')
        if translator_map:
            self.translator_combo.blockSignals(True)
            self.translator_combo.clear()
            self.translator_combo.addItems(list(translator_map.values()))
            # 恢复选中的索引
            if 0 <= current_translator_index < self.translator_combo.count():
                self.translator_combo.setCurrentIndex(current_translator_index)
            self.translator_combo.blockSignals(False)
        
        # 重新填充目标语言下拉菜单
        lang_map = self.app_logic.get_display_mapping('target_lang')
        if lang_map:
            self.target_language_combo.blockSignals(True)
            self.target_language_combo.clear()
            self.target_language_combo.addItems(list(lang_map.values()))
            # 恢复选中的索引
            if 0 <= current_target_lang_index < self.target_language_combo.count():
                self.target_language_combo.setCurrentIndex(current_target_lang_index)
            self.target_language_combo.blockSignals(False)
        
        # 重新填充对齐下拉菜单
        alignment_map = self.app_logic.get_display_mapping('alignment')
        if alignment_map:
            self.alignment_combo.blockSignals(True)
            self.alignment_combo.clear()
            self.alignment_combo.addItems(list(alignment_map.values()))
            # 恢复选中的索引
            if 0 <= current_alignment_index < self.alignment_combo.count():
                self.alignment_combo.setCurrentIndex(current_alignment_index)
            self.alignment_combo.blockSignals(False)
        
        # 重新填充方向下拉菜单
        direction_map = self.app_logic.get_display_mapping('direction')
        if direction_map:
            self.direction_combo.blockSignals(True)
            self.direction_combo.clear()
            direction_items = [v for k, v in direction_map.items() if k != 'auto']
            self.direction_combo.addItems(direction_items)
            # 恢复选中的索引
            if 0 <= current_direction_index < self.direction_combo.count():
                self.direction_combo.setCurrentIndex(current_direction_index)
            self.direction_combo.blockSignals(False)

    def _get_saved_style_presets(self):
        config_ref = self.config_service.get_config_reference()
        presets = getattr(getattr(config_ref, "app", None), "saved_style_presets", None)
        return presets if isinstance(presets, dict) else {}

    def _refresh_style_preset_combo(self, selected_name: str | None = None):
        if not hasattr(self, "style_preset_combo"):
            return

        current_name = selected_name if selected_name is not None else self.style_preset_combo.currentData()
        presets = self._get_saved_style_presets()

        self.style_preset_combo.blockSignals(True)
        try:
            self.style_preset_combo.clear()
            self.style_preset_combo.addItem(self._t("Select saved style"), None)
            for name in presets.keys():
                self.style_preset_combo.addItem(name, name)

            if current_name in presets:
                target_index = self.style_preset_combo.findData(current_name)
                self.style_preset_combo.setCurrentIndex(target_index if target_index >= 0 else 0)
            else:
                self.style_preset_combo.setCurrentIndex(0)
        finally:
            self.style_preset_combo.blockSignals(False)

        self.style_preset_combo.setToolTip(self._t("Choose a saved style to apply"))

    def _normalize_region_style_state(self, region_data):
        if not isinstance(region_data, dict):
            return {}

        default_font_color = self.config_service.get_config().render.font_color or "#000000"
        normalized = {}
        font_value = region_data.get("font_path", region_data.get("font_family", ""))
        normalized["font_family"] = "" if font_value is None else str(font_value)

        font_color = region_data.get("font_color")
        fg_colors = region_data.get("fg_colors")
        if not font_color and isinstance(fg_colors, (list, tuple)) and len(fg_colors) == 3:
            font_color = f"#{int(fg_colors[0]):02x}{int(fg_colors[1]):02x}{int(fg_colors[2]):02x}"
        font_color = str(font_color or default_font_color).strip()
        normalized["font_color"] = QColor(font_color).name() if QColor(font_color).isValid() else "#000000"

        stroke_color = region_data.get("stroke_color")
        if not stroke_color:
            bg_color = region_data.get("bg_color")
            bg_colors = region_data.get("bg_colors")
            if isinstance(bg_color, (list, tuple)) and len(bg_color) == 3:
                stroke_color = f"#{int(bg_color[0]):02x}{int(bg_color[1]):02x}{int(bg_color[2]):02x}"
            elif isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_color = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
        stroke_color = str(stroke_color or "#ffffff").strip()
        normalized["stroke_color"] = QColor(stroke_color).name() if QColor(stroke_color).isValid() else "#ffffff"

        try:
            normalized["stroke_width"] = float(region_data.get("stroke_width", region_data.get("default_stroke_width", 0.07)))
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

        normalized["alignment"] = self._alignment_value_from_text(region_data.get("alignment", "auto"))
        normalized["direction"] = self._direction_value_from_text(region_data.get("direction", "horizontal"))
        return normalized

    def _find_matching_style_preset_name(self, region_data) -> str | None:
        normalized_region_style = self._normalize_region_style_state(region_data)
        if not normalized_region_style:
            return None

        for name, preset_data in self._get_saved_style_presets().items():
            if self._normalize_saved_style_preset(preset_data) == normalized_region_style:
                return str(name)
        return None

    def _refresh_style_preset_action_buttons(self):
        if hasattr(self, "save_style_preset_button"):
            self.save_style_preset_button.setText("")
            self.save_style_preset_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
            )
            set_hover_hint(self.save_style_preset_button, self._t("Save current style combination"))
            self.save_style_preset_button.setAccessibleName(self._t("Save Style"))

        if hasattr(self, "delete_style_preset_button"):
            self.delete_style_preset_button.setText("")
            self.delete_style_preset_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
            )
            set_hover_hint(self.delete_style_preset_button, self._t("Delete selected saved style"))
            self.delete_style_preset_button.setAccessibleName(self._t("Delete Style"))

    def _alignment_value_from_text(self, text: str) -> str:
        raw_text = str(text or "").strip()
        if raw_text in {"auto", "left", "center", "right"}:
            return raw_text

        alignment_map = self.app_logic.get_display_mapping('alignment') or {}
        reverse_map = {display: value for value, display in alignment_map.items()}
        if raw_text in reverse_map:
            return reverse_map[raw_text]

        fallback_map = {"自动": "auto", "左对齐": "left", "居中": "center", "右对齐": "right"}
        return fallback_map.get(raw_text, "auto")

    def _alignment_text_for_value(self, value: str) -> str:
        alignment_map = self.app_logic.get_display_mapping('alignment') or {}
        normalized_value = self._alignment_value_from_text(value)
        fallback_map = {"auto": "自动", "left": "左对齐", "center": "居中", "right": "右对齐"}
        return alignment_map.get(normalized_value, fallback_map.get(normalized_value, normalized_value))

    def _direction_value_from_text(self, text: str) -> str:
        raw_text = str(text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in {"h", "horizontal"}:
            return "horizontal"
        if lower_text in {"v", "vertical"}:
            return "vertical"

        direction_map = self.app_logic.get_display_mapping('direction') or {}
        horizontal_text = direction_map.get('h', self._t("direction_horizontal"))
        vertical_text = direction_map.get('v', self._t("direction_vertical"))
        if raw_text == vertical_text or raw_text == "竖排":
            return "vertical"
        if raw_text == horizontal_text or raw_text == "横排":
            return "horizontal"
        return "horizontal"

    def _direction_text_for_value(self, value: str) -> str:
        direction_map = self.app_logic.get_display_mapping('direction') or {}
        horizontal_text = direction_map.get('h', self._t("direction_horizontal"))
        vertical_text = direction_map.get('v', self._t("direction_vertical"))
        normalized_value = self._direction_value_from_text(value)
        return vertical_text if normalized_value == "vertical" else horizontal_text

    def _set_font_family_combo_value(self, font_value: str):
        if not font_value:
            self.font_family_combo.setCurrentIndex(-1)
            return

        import os

        font_filename = os.path.basename(font_value)
        target_index = -1
        for i in range(self.font_family_combo.count()):
            item_data = self.font_family_combo.itemData(i)
            if item_data == font_value or item_data == font_filename:
                target_index = i
                break

        if target_index < 0:
            display_name = os.path.splitext(font_filename)[0] or font_filename
            self.font_family_combo.addItem(display_name, font_value)
            target_index = self.font_family_combo.count() - 1

        self.font_family_combo.setCurrentIndex(target_index)

    def _normalize_saved_style_preset(self, style_data):
        if not isinstance(style_data, dict):
            return {}

        normalized = {}
        font_value = style_data.get("font_family", style_data.get("font_path", ""))
        normalized["font_family"] = "" if font_value is None else str(font_value)

        font_color = str(style_data.get("font_color") or "#000000").strip()
        normalized["font_color"] = QColor(font_color).name() if QColor(font_color).isValid() else "#000000"

        stroke_color = style_data.get("stroke_color")
        if not stroke_color:
            bg_colors = style_data.get("bg_colors", style_data.get("bg_color"))
            if isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_color = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
        stroke_color = str(stroke_color or "#ffffff").strip()
        normalized["stroke_color"] = QColor(stroke_color).name() if QColor(stroke_color).isValid() else "#ffffff"

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

        normalized["alignment"] = self._alignment_value_from_text(style_data.get("alignment", "auto"))
        normalized["direction"] = self._direction_value_from_text(style_data.get("direction", "horizontal"))
        return normalized

    def _collect_current_style_preset(self):
        current_font = self.font_family_combo.itemData(self.font_family_combo.currentIndex())

        return {
            "font_family": "" if current_font is None else str(current_font),
            "font_color": self.font_color_picker.get_color(),
            "stroke_color": self.stroke_color_picker.get_color(),
            "stroke_width": float(self.stroke_width_spinbox.value()),
            "line_spacing": float(self.line_spacing_spinbox.value()),
            "letter_spacing": float(self.letter_spacing_spinbox.value()),
            "alignment": self._alignment_value_from_text(self.alignment_combo.currentText()),
            "direction": self._direction_value_from_text(self.direction_combo.currentText()),
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
        self.alignment_combo.setCurrentText(self._alignment_text_for_value(normalized.get("alignment", "auto")))
        self.direction_combo.setCurrentText(self._direction_text_for_value(normalized.get("direction", "horizontal")))

    def _apply_saved_style_to_selection(self, preset_name: str):
        from PyQt6.QtWidgets import QMessageBox

        selected_indices = self.model.get_selection()
        if not selected_indices:
            QMessageBox.warning(self, self._t("Warning"), self._t("Please select at least one region"))
            self._refresh_style_preset_combo()
            return

        style_data = self._normalize_saved_style_preset(self._get_saved_style_presets().get(preset_name))
        if not style_data:
            QMessageBox.warning(self, self._t("Warning"), self._t("Selected style preset is invalid"))
            self._refresh_style_preset_combo()
            return

        self.block_updates = True
        try:
            self._set_style_controls_from_preset(style_data)
        finally:
            self.block_updates = False

        for region_index in selected_indices:
            self.font_family_changed.emit(region_index, style_data.get("font_family", ""))
            self.font_color_changed.emit(region_index, style_data.get("font_color", "#000000"))
            self.stroke_color_changed.emit(region_index, style_data.get("stroke_color", "#ffffff"))
            self.stroke_width_changed.emit(region_index, style_data.get("stroke_width", 0.07))
            self.line_spacing_changed.emit(region_index, style_data.get("line_spacing", 1.0))
            self.letter_spacing_changed.emit(region_index, style_data.get("letter_spacing", 1.0))
            self.alignment_changed.emit(region_index, style_data.get("alignment", "auto"))
            self.direction_changed.emit(region_index, style_data.get("direction", "horizontal"))

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
            QMessageBox.warning(self, self._t("Warning"), self._t("Style preset name cannot be empty"))
            return

        current_presets = copy.deepcopy(self._get_saved_style_presets())
        if preset_name in current_presets:
            reply = QMessageBox.question(
                self,
                self._t("Confirm"),
                self._t("Style preset '{name}' already exists. Overwrite?", name=preset_name),
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
            QMessageBox.critical(self, self._t("Error"), self._t("Failed to save style preset"))
            return

        self._refresh_style_preset_combo(selected_name=preset_name)

    def _on_delete_style_preset_clicked(self):
        import copy

        from PyQt6.QtWidgets import QMessageBox

        preset_name = self.style_preset_combo.currentData()
        if not preset_name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Please select a saved style"))
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
            QMessageBox.critical(self, self._t("Error"), self._t("Failed to delete style preset"))
            return

        self._refresh_style_preset_combo()

    def on_single_region_updated(self, index: int):
        """Slot to refresh the panel when a single region is updated in a targeted way."""
        selected_indices = self.model.get_selection()
        if not selected_indices or len(selected_indices) > 1 or selected_indices[0] != index:
            return # Not the currently selected item, do nothing

        region_data = self.model.get_region_by_index(index)
        if region_data:
            self._update_display(region_data, index)
    
    def force_refresh_from_model(self):
        """强制刷新属性栏，忽略焦点状态（用于OCR/翻译完成后）"""
        selected_indices = self.model.get_selection()
        if selected_indices and len(selected_indices) == 1:
            region_index = selected_indices[0]
            region_data = self.model.get_region_by_index(region_index)
            if region_data:
                self._update_display(region_data, region_index, force=True)

    def on_regions_updated(self, regions):
        """Slot to refresh the panel if the currently selected region's data has changed."""
        selected_indices = self.model.get_selection()
        if not selected_indices or len(selected_indices) > 1:
            return
        
        region_index = selected_indices[0]
        if 0 <= region_index < len(regions):
            # 直接使用信号传递过来的最新regions数据来更新显示
            self._update_display(regions[region_index], region_index)

    def on_selection_changed(self, selected_indices):
        """Slot to update the panel when the selection in the model changes."""
        if not selected_indices:
            # 没有选择，禁用所有控件
            self.clear_and_disable_selection_dependent()
        elif len(selected_indices) == 1:
            # 单选，显示该区域的详细信息
            self.info_group.setEnabled(True)
            self.text_edit_frame.setEnabled(True)
            self.style_edit_frame.setEnabled(True)
            self.action_frame.setEnabled(True)
            region_index = selected_indices[0]
            self.current_region_index = region_index
            regions = self.model.get_regions()
            if 0 <= region_index < len(regions):
                self._update_display(regions[region_index], region_index)
        else:
            # 多选，启用样式编辑，但禁用文本编辑和信息显示
            self.info_group.setEnabled(False)
            self.text_edit_frame.setEnabled(False)
            self.style_edit_frame.setEnabled(True)  # 启用样式编辑
            self.action_frame.setEnabled(True)
            self.current_region_index = -1
            
            # 清空显示但不禁用样式控件
            self.block_updates = True
            self.index_label.setText(f"多选 ({len(selected_indices)})")
            self.original_text_box.clear()
            self.translated_text_box.clear()
            self._refresh_style_preset_combo(selected_name="")
            self.block_updates = False

    def clear_and_disable_selection_dependent(self):
        """Clears selection-dependent fields and disables their sections."""
        # Disable sections that depend on a selection
        self.info_group.setEnabled(False)
        self.text_edit_frame.setEnabled(False)
        self.style_edit_frame.setEnabled(False)
        self.action_frame.setEnabled(False)

        self.current_region_index = -1

        self.block_updates = True
        self._set_selection_controls_blocked(True)
        try:
            self.original_text_box.clear()
            self.translated_text_box.clear()
            self.font_size_input.clear()
            self.stroke_width_spinbox.setValue(0.07)  # 重置为默认值
            self.line_spacing_spinbox.setValue(1.0)  # 重置为默认值
            self.letter_spacing_spinbox.setValue(1.0)  # 重置为默认值
            self.angle_spinbox.setValue(0.0)
            default_color = self.config_service.get_config().render.font_color or "#000000"
            self.font_color_picker.reset(default_color)
            self.stroke_color_picker.reset("#ffffff")
            self.index_label.setText("-")
            self.bbox_label.setText("-")
            self.size_label.setText("-")
            self.angle_label.setText("-")
            self._refresh_style_preset_combo(selected_name="")
        finally:
            self._set_selection_controls_blocked(False)
            self.block_updates = False

    def _update_display(self, region_data, region_index, force=False):
        """Populate all widgets with data from the selected region.
        
        Args:
            region_data: 区域数据字典
            region_index: 区域索引
            force: 是否强制更新文本框（忽略焦点状态），用于OCR/翻译完成后
        """
        self.block_updates = True
        self._set_selection_controls_blocked(True)
        try:
            # --- Update Region Info ---
            self.index_label.setText(str(region_index))
            wf_info = self._calculate_white_frame_info(region_data)
            if wf_info:
                cx, cy, w, h = wf_info
                self.bbox_label.setText(f"({cx:.0f}, {cy:.0f})")
                self.size_label.setText(f"{w:.0f} × {h:.0f}")
            else:
                self.bbox_label.setText("-")
                self.size_label.setText("-")
            angle = region_data.get('angle', 0)
            self.angle_label.setText(f"{angle:.1f}°")

            # --- Update Text & Styles ---
            # 如果force=True（OCR/翻译完成），或文本框没有焦点时才更新
            if force or not self.original_text_box.hasFocus():
                # 统一使用 text 字段（用户编辑和OCR识别都使用这个字段）
                original_text = region_data.get("text", "")
                self.original_text_box.setText(original_text)

            # 如果force=True（OCR/翻译完成），或文本框没有焦点时才更新
            if force or not self.translated_text_box.hasFocus():
                import re

                # 复选框选中 → 显示"替换前译文"(translation_raw),否则显示"译文"(translation)
                show_raw = bool(getattr(self, 'translation_raw_checkbox', None)
                                and self.translation_raw_checkbox.isChecked())
                field_key = "translation_raw" if show_raw else "translation"
                translation_text = region_data.get(field_key, "") or region_data.get("translation", "")

                # 1. 将所有 AI 换行符 ([BR], <br>, 【BR】) 转换为 \n
                translation_text = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', translation_text, flags=re.IGNORECASE)

                # 2. 将 <H> 标签替换为符号 ⇄ 显示在文本框中
                display_text = translation_text.replace('<H>', '⇄').replace('</H>', '⇄')

                # 3. 将 \n 替换为 ↵ 显示在文本框中
                display_text = display_text.replace('\n', '↵')
                self.translated_text_box.setText(display_text)
            
            self.font_size_input.setText(str(region_data.get("font_size", "")))
            self.font_size_slider.setValue(region_data.get("font_size", 12))
            
            default_color = self.config_service.get_config().render.font_color or "#000000"
            color_hex = default_color
            fg_colors = region_data.get('fg_colors')
            font_color = region_data.get("font_color")

            # 优先使用用户设置的font_color，然后才是原始的fg_colors
            if font_color:
                 color_hex = font_color
            elif isinstance(fg_colors, (list, tuple)) and len(fg_colors) == 3:
                 color_hex = f"#{int(fg_colors[0]):02x}{int(fg_colors[1]):02x}{int(fg_colors[2]):02x}"

            self.font_color_picker.set_color(color_hex)

            # Update stroke color display
            bg_colors = region_data.get('bg_colors')
            bg_color = region_data.get('bg_color')
            stroke_hex = "#ffffff"
            if isinstance(bg_color, (list, tuple)) and len(bg_color) == 3:
                stroke_hex = f"#{int(bg_color[0]):02x}{int(bg_color[1]):02x}{int(bg_color[2]):02x}"
            elif isinstance(bg_colors, (list, tuple)) and len(bg_colors) == 3:
                stroke_hex = f"#{int(bg_colors[0]):02x}{int(bg_colors[1]):02x}{int(bg_colors[2]):02x}"
            self.stroke_color_picker.set_color(stroke_hex)

            # Update stroke width
            stroke_width = region_data.get("stroke_width", region_data.get("default_stroke_width", 0.07))
            self.stroke_width_spinbox.setValue(stroke_width if stroke_width is not None else 0.07)
            
            # Update line spacing
            line_spacing = region_data.get("line_spacing", 1.0)
            self.line_spacing_spinbox.setValue(line_spacing if line_spacing is not None else 1.0)

            letter_spacing = region_data.get("letter_spacing", 1.0)
            self.letter_spacing_spinbox.setValue(letter_spacing if letter_spacing is not None else 1.0)
            self.angle_spinbox.setValue(float(region_data.get("angle", 0.0) or 0.0))
            
            self._set_font_family_combo_value(region_data.get("font_path", ""))
            self.alignment_combo.setCurrentText(self._alignment_text_for_value(region_data.get("alignment", "auto")))
            
            display_direction_map = self.app_logic.get_display_mapping('direction') or {}
            horizontal_text = display_direction_map.get('h', self._t("direction_horizontal"))
            vertical_text = display_direction_map.get('v', self._t("direction_vertical"))

            direction_value = str(region_data.get("direction", "")).strip().lower()
            if direction_value in ("v", "vertical"):
                direction_display = vertical_text
            elif direction_value in ("h", "horizontal"):
                direction_display = horizontal_text
            else:
                # 旧数据的 auto 或空值：在编辑器内按框形状回显横/竖
                if wf_info:
                    _, _, w, h = wf_info
                    direction_display = vertical_text if h > w else horizontal_text
                else:
                    direction_display = horizontal_text
            self.direction_combo.setCurrentText(direction_display)

            # --- Update Mask Checkboxes ---
            display_mask_type = self.model.get_display_mask_type()
            self.show_refined_mask_checkbox.setChecked(display_mask_type == 'refined')
            self._refresh_style_preset_combo(selected_name=self._find_matching_style_preset_name(region_data) or "")
        finally:
            self._set_selection_controls_blocked(False)
            self.block_updates = False

    def _make_focus_out_handler(self, text_edit, callback):
        """创建一个焦点丢失事件处理器，保存原始的focusOutEvent"""
        original_focus_out = text_edit.focusOutEvent
        
        def focus_out_wrapper(event):
            # 先调用原始的focusOutEvent
            original_focus_out(event)
            # 然后调用我们的回调
            callback()
        
        return focus_out_wrapper
    
    def force_save_text_edits(self):
        """强制保存当前文本框的编辑内容（在失去焦点前）"""
        if self.current_region_index == -1:
            return
        
        # 保存原文编辑
        current_original = self.original_text_box.toPlainText()
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            # 比较当前编辑的文本与original_text（如果没有则与text比较）
            stored_original = region_data.get("original_text") or region_data.get("text", "")
            if stored_original != current_original:
                self.original_text_modified.emit(self.current_region_index, current_original)
        
        # 保存译文编辑
        self._save_translated_text()
    
    def _save_translated_text(self):
        """保存译文编辑（执行与_on_translated_text_focus_out相同的逻辑）"""
        if self.current_region_index == -1:
            return

        import re

        # 1. 将 ⇄ 替换回 <H> 标签
        raw_text = self.translated_text_box.toPlainText()
        text_with_tags = convert_arrows_to_tags(raw_text)

        # 2. 将 ↵ 替换回 \n
        text_with_newlines = text_with_tags.replace('↵', '\n')

        # 3. 将 \n 转换回 AI 换行符 [BR]
        text_with_br = re.sub(r'\n+', '[BR]', text_with_newlines)

        # 按当前模式决定写入哪个字段
        show_raw = bool(getattr(self, 'translation_raw_checkbox', None)
                        and self.translation_raw_checkbox.isChecked())
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            field_key = "translation_raw" if show_raw else "translation"
            if region_data.get(field_key, "") != text_with_br:
                if show_raw:
                    self.translation_raw_modified.emit(self.current_region_index, text_with_br)
                else:
                    self.translated_text_modified.emit(self.current_region_index, text_with_br)
    
    def _on_original_text_focus_out(self):
        """当原文文本框失去焦点时更新model"""
        if self.current_region_index != -1:
            self.original_text_modified.emit(self.current_region_index, self.original_text_box.toPlainText())
    
    def _on_translated_text_focus_out(self):
        """当译文文本框失去焦点时更新model"""
        if self.current_region_index != -1:
            # 执行与_save_translated_text相同的转换逻辑
            import re

            # 1. 将 ⇄ 替换回 <H> 标签
            raw_text = self.translated_text_box.toPlainText()
            text_with_tags = convert_arrows_to_tags(raw_text)

            # 2. 将 ↵ 替换回 \n
            text_with_newlines = text_with_tags.replace('↵', '\n')

            # 3. 将 \n 转换回 AI 换行符 [BR]
            text_with_br = re.sub(r'\n+', '[BR]', text_with_newlines)

            self.translated_text_modified.emit(self.current_region_index, text_with_br)
    
    def _on_original_text_changed(self):
        """保留这个方法以防需要，但现在不使用"""
        if self.current_region_index != -1 and not self.block_updates:
            self.original_text_modified.emit(self.current_region_index, self.original_text_box.toPlainText())
    def _on_translated_text_changed(self):
        if self.current_region_index != -1 and not self.block_updates:
            import re

            # 1. 将 ⇄ 替换回 <H> 标签
            raw_text = self.translated_text_box.toPlainText()
            text_with_tags = convert_arrows_to_tags(raw_text)

            # 2. 将 ↵ 替换回 \n
            text_with_newlines = text_with_tags.replace('↵', '\n')

            # 3. 将 \n 转换回 AI 换行符 [BR]（与 _on_translated_text_focus_out 保持一致）
            text_with_br = re.sub(r'\n+', '[BR]', text_with_newlines)

            # 复选框选中 → 当前编辑的是"替换前译文",走 raw 信号(controller 会跑替换更新 translation);
            # 否则编辑的是"译文",走原信号
            show_raw = bool(getattr(self, 'translation_raw_checkbox', None)
                            and self.translation_raw_checkbox.isChecked())
            if show_raw:
                self.translation_raw_modified.emit(self.current_region_index, text_with_br)
            else:
                self.translated_text_modified.emit(self.current_region_index, text_with_br)

    def _on_translation_raw_mode_toggled(self, checked: bool):
        """复选框切换:重新刷新当前 region 的文本框内容(读取对应字段)。"""
        if self.current_region_index == -1:
            return
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data:
            self._update_display(region_data, self.current_region_index, force=True)
    
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
        if hasattr(self, 'lang_name_to_code'):
            return self.lang_name_to_code.get(display_name, display_name)
        return display_name
    def _on_font_size_editing_finished(self):
        if self.block_updates:
            return
        text = self.font_size_input.text()
        if text.isdigit():
            value = int(text)
            value = max(8, min(1000, value))
            self.font_size_input.setText(str(value))
            if self.font_size_slider.minimum() <= value <= self.font_size_slider.maximum():
                if self.font_size_slider.value() != value:
                    self.font_size_slider.setValue(value)
            
            # 支持多选批量设置
            selected_indices = self.model.get_selection()
            for region_index in selected_indices:
                self.font_size_changed.emit(region_index, value)

    def _on_font_size_slider_changed(self, value): 
        if self.block_updates:
            return
        # 支持多选批量设置
        selected_indices = self.model.get_selection()
        if selected_indices:
            self.font_size_input.setText(str(value))
            for region_index in selected_indices:
                self.font_size_changed.emit(region_index, value)
    def _on_font_family_changed(self, index):
        if self.block_updates:
            return
        if index < 0:
            return
        
        # 支持多选批量设置
        selected_indices = self.model.get_selection()
        if not selected_indices:
            return
        
        # Get the font filename from combo box data
        font_filename = self.font_family_combo.itemData(index)
        if font_filename is None:
            font_filename = ""
        
        # 批量应用到所有选中的区域
        for region_index in selected_indices:
            self.font_family_changed.emit(region_index, font_filename)
    
    def _on_font_color_changed(self, hex_color):
        """字体颜色变化时的处理"""
        if self.block_updates:
            return
        for idx in self.model.get_selection():
            self.font_color_changed.emit(idx, hex_color)

    def _on_stroke_color_changed(self, hex_color):
        """描边颜色变化时的处理"""
        if self.block_updates:
            return
        for idx in self.model.get_selection():
            self.stroke_color_changed.emit(idx, hex_color)

    def _on_stroke_width_changed(self, value):
        """处理描边宽度变化"""
        if self.block_updates:
            return
        for region_index in self.model.get_selection():
            self.stroke_width_changed.emit(region_index, value)

    def _on_line_spacing_changed(self, value):
        """处理行间距倍率变化"""
        if self.block_updates:
            return
        # 支持多选批量设置
        selected_indices = self.model.get_selection()
        for region_index in selected_indices:
            self.line_spacing_changed.emit(region_index, value)

    def _on_letter_spacing_changed(self, value):
        """处理字间距倍率变化"""
        if self.block_updates:
            return
        selected_indices = self.model.get_selection()
        for region_index in selected_indices:
            self.letter_spacing_changed.emit(region_index, value)

    def _on_angle_changed(self, value):
        if self.block_updates:
            return
        selected_indices = self.model.get_selection()
        for region_index in selected_indices:
            self.angle_changed.emit(region_index, float(value))

    def _on_mask_tool_changed(self, button):
        if button is self.select_button or button is self.paint_select_button:
            self.mask_tool_changed.emit('select')
        elif button is self.brush_button:
            self.mask_tool_changed.emit('brush')
        elif button is self.eraser_button:
            self.mask_tool_changed.emit('eraser')
        elif button is self.paint_brush_button:
            self.mask_tool_changed.emit('paint')
        elif button is self.paint_eraser_button:
            self.mask_tool_changed.emit('paint_erase')

    def _on_brush_size_changed(self, value):
        """两个画笔大小滑块共享同一个模型字段；同步另一个滑块显示。"""
        self.brush_size_value_label.setText(str(value))
        self.paint_size_value_label.setText(str(value))
        # 同步另一个滑块，避免循环触发
        sender = self.sender()
        if sender is self.brush_size_slider:
            if self.paint_size_slider.value() != value:
                self.paint_size_slider.blockSignals(True)
                self.paint_size_slider.setValue(value)
                self.paint_size_slider.blockSignals(False)
        else:
            if self.brush_size_slider.value() != value:
                self.brush_size_slider.blockSignals(True)
                self.brush_size_slider.setValue(value)
                self.brush_size_slider.blockSignals(False)
        self.brush_size_changed.emit(value)

    def _on_paint_color_changed(self, hex_color: str):
        self.brush_color_changed.emit(hex_color)

    def _on_paint_tab_changed(self, index: int):
        """切换标签页时，自动把活跃工具切回当前页的选择工具，避免跨页工具冲突。"""
        try:
            if index == 0:
                # 蒙版页：若当前按钮在 paint 页，切回蒙版选择工具
                checked = self.mask_tool_group.checkedButton()
                if checked in (self.paint_select_button, self.paint_brush_button, self.paint_eraser_button):
                    self.select_button.setChecked(True)
                    self.mask_tool_changed.emit('select')
            else:
                checked = self.mask_tool_group.checkedButton()
                if checked in (self.select_button, self.brush_button, self.eraser_button):
                    self.paint_select_button.setChecked(True)
                    self.mask_tool_changed.emit('select')
        except Exception:
            pass

    def sync_brush_size_from_model(self, size: int):
        """从模型同步画笔大小到UI（不触发信号）"""
        for slider, label in (
            (self.brush_size_slider, self.brush_size_value_label),
            (getattr(self, 'paint_size_slider', None), getattr(self, 'paint_size_value_label', None)),
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
        if hasattr(self, 'paint_color_picker') and self.paint_color_picker is not None:
            self.paint_color_picker.set_color(hex_color or "#ffffff")

    def sync_active_tool_from_model(self, tool: str):
        """当 model 的 active_tool 变化时，UI 同步高亮对应按钮并切换标签页。"""
        # 'select' 在蒙版页和画板页都有按钮，按当前所在标签页决定亮哪个，
        # 避免在画板页点击「选择」时被强制切回蒙版页。
        if tool == 'select':
            if self.paint_tab_widget.currentIndex() == 1:
                button, tab_index = self.paint_select_button, 1
            else:
                button, tab_index = self.select_button, 0
        else:
            mapping = {
                'brush': (self.brush_button, 0),
                'eraser': (self.eraser_button, 0),
                'paint': (getattr(self, 'paint_brush_button', None), 1),
                'paint_erase': (getattr(self, 'paint_eraser_button', None), 1),
            }
            info = mapping.get(tool)
            if not info:
                return
            button, tab_index = info
        if button is None:
            return
        try:
            self.mask_tool_group.blockSignals(True)
            self.paint_tab_widget.blockSignals(True)
            button.setChecked(True)
            if self.paint_tab_widget.currentIndex() != tab_index:
                self.paint_tab_widget.setCurrentIndex(tab_index)
        finally:
            self.mask_tool_group.blockSignals(False)
            self.paint_tab_widget.blockSignals(False)

    def _on_alignment_changed(self, text: str):
        if self.block_updates:
            return
        # 支持多选批量设置
        selected_indices = self.model.get_selection()
        for region_index in selected_indices:
            self.alignment_changed.emit(region_index, text)

    def _on_direction_changed(self, text: str):
        if self.block_updates:
            return
        # 支持多选批量设置
        selected_indices = self.model.get_selection()
        for region_index in selected_indices:
            self.direction_changed.emit(region_index, text)

    def _calculate_white_frame_info(self, region_data):
        """计算白框中心世界坐标和宽高，返回 (cx, cy, w, h) 或 None。"""
        import math
        has_custom = bool(region_data.get('has_custom_white_frame', False))
        wf_local = None
        wf_local = region_data.get('render_box_rect_local')
        if not wf_local and has_custom:
            wf_local = region_data.get('white_frame_rect_local')
        if not wf_local:
            wf_local = region_data.get('white_frame_rect_local')
        center = region_data.get('center')
        angle = float(region_data.get('angle', 0))

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
        lines = region_data.get('lines', [])
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

    def _insert_placeholder(self):
        """插入占位符 ＿ (全角下划线)"""
        # 确保文本框有焦点,避免光标位置丢失
        self.translated_text_box.setFocus()
        self.translated_text_box.insertPlainText("＿")

    def _insert_newline(self):
        """插入换行符 ↵ (向下箭头符号,用于在文本框中显示换行)"""
        # 确保文本框有焦点,避免光标位置丢失
        self.translated_text_box.setFocus()
        self.translated_text_box.insertPlainText("↵")

    def _mark_horizontal(self):
        """标记横排：有选中则两侧包裹，无选中则在光标处插入一个 ⇄。"""
        # 确保文本框有焦点,避免光标位置丢失
        self.translated_text_box.setFocus()
        cursor = self.translated_text_box.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            # Qt 的 selectedText() 会将段落分隔符转换为 \u2029,需要替换回 ↵
            selected_text = selected_text.replace('\u2029', '↵')
            cursor.insertText(f"⇄{selected_text}⇄")
        else:
            cursor.insertText("⇄")
        self.translated_text_box.setTextCursor(cursor)

    def _on_ocr_model_change(self, text):
        """OCR模型变化时保存配置"""
        self.app_logic.update_single_config('ocr.ocr', text)

    def _on_translator_change(self, display_name):
        """翻译器变化时保存配置"""
        translator_key = self.translator_display_to_key.get(display_name, display_name)
        self.app_logic.update_single_config('translator.translator', translator_key)

    def _on_target_language_change(self, display_name):
        """目标语言变化时保存配置"""
        lang_code = self.lang_name_to_code.get(display_name, "CHS")
        self.app_logic.update_single_config('translator.target_lang', lang_code)
        # 同时更新翻译服务的目标语言
        self.app_logic.translation_service.set_target_language(lang_code)

