
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QWheelEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services import get_config_service

# from .collapsible_frame import CollapsibleFrame  # 不再使用折叠框
from .syntax_highlighter import HorizontalTagHighlighter
import logging

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
    original_text_modified = pyqtSignal(int, str)
    ocr_requested = pyqtSignal()
    translation_requested = pyqtSignal()
    font_size_changed = pyqtSignal(int, int)
    font_color_changed = pyqtSignal(int, str)
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
    toggle_removed_mask_visibility = pyqtSignal(bool)
    mask_config_changed = pyqtSignal(dict) # New signal with dict payload
    update_mask_requested = pyqtSignal()

    def __init__(self, model, app_logic, parent=None):
        super().__init__(parent)
        self.model = model
        self.app_logic = app_logic
        self.config_service = get_config_service()
        self._init_ui()
        self._connect_signals()
        self._connect_model_signals() # Connect to model signals
        self.block_updates = False
        self.current_region_index = -1
        self.clear_and_disable_selection_dependent()
        # 初始化时从配置加载蒙版参数
        self._load_mask_config_from_settings()

    def _init_ui(self):
        # 创建主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(main_layout)
        
        # 创建滚动区域
        from PyQt6.QtWidgets import QScrollArea
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        
        # 创建内容容器
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
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

    def _create_region_info_section(self, layout):
        self.info_group = QGroupBox("区域信息")
        info_layout = QFormLayout(self.info_group)
        self.index_label = QLabel("-")
        self.bbox_label = QLabel("-")
        self.size_label = QLabel("-")
        self.angle_label = QLabel("-")
        info_layout.addRow("索引:", self.index_label)
        info_layout.addRow("位置:", self.bbox_label)
        info_layout.addRow("尺寸:", self.size_label)
        info_layout.addRow("角度:", self.angle_label)
        layout.addWidget(self.info_group)

    def _create_mask_edit_section(self, layout):
        self.mask_edit_frame = QGroupBox("蒙版编辑")
        mask_layout = QVBoxLayout(self.mask_edit_frame)
        tools_layout = QHBoxLayout()

        self.mask_tool_group = QButtonGroup(self)
        self.mask_tool_group.setExclusive(True)

        self.brush_button = QPushButton("画笔")
        self.brush_button.setCheckable(True)
        self.eraser_button = QPushButton("橡皮擦")
        self.eraser_button.setCheckable(True)
        self.select_button = QPushButton("不选择")
        self.select_button.setCheckable(True)

        self.mask_tool_group.addButton(self.select_button, 0)
        self.mask_tool_group.addButton(self.brush_button, 1)
        self.mask_tool_group.addButton(self.eraser_button, 2)
        self.select_button.setChecked(True) # Default to select

        tools_layout.addWidget(self.select_button)
        tools_layout.addWidget(self.brush_button)
        tools_layout.addWidget(self.eraser_button)

        mask_layout.addLayout(tools_layout)
        brush_size_layout = QHBoxLayout()
        brush_size_layout.addWidget(QLabel("笔刷大小:"))
        self.brush_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.brush_size_slider.setRange(1, 100)
        self.brush_size_label = QLabel("20")
        self.brush_size_slider.setValue(20)
        brush_size_layout.addWidget(self.brush_size_slider)
        brush_size_layout.addWidget(self.brush_size_label)
        mask_layout.addLayout(brush_size_layout)
        mask_params_layout = QFormLayout()
        self.mask_dilation_offset_entry = QLineEdit()
        self.mask_kernel_size_entry = QLineEdit()
        mask_params_layout.addRow("扩张偏移:", self.mask_dilation_offset_entry)
        mask_params_layout.addRow("内核大小:", self.mask_kernel_size_entry)
        mask_layout.addLayout(mask_params_layout)
        self.ignore_bubble_checkbox = QCheckBox("忽略气泡")
        self.update_mask_button = QPushButton("更新蒙版")
        self.show_refined_mask_checkbox = QCheckBox("显示优化蒙版")
        self.show_refined_mask_checkbox.setChecked(False)  # 默认关闭
        self.show_removed_checkbox = QCheckBox("显示被优化区域")
        mask_layout.addWidget(self.ignore_bubble_checkbox)
        mask_layout.addWidget(self.update_mask_button)
        mask_layout.addWidget(self.show_refined_mask_checkbox)
        mask_layout.addWidget(self.show_removed_checkbox)
        layout.addWidget(self.mask_edit_frame)

    def _create_text_section(self, layout):
        self.text_edit_frame = QGroupBox("文本内容")
        text_layout = QVBoxLayout(self.text_edit_frame)
        ocr_trans_config_layout = QFormLayout()
        self.ocr_model_combo = QComboBox()
        self.translator_combo = QComboBox()
        self.target_language_combo = QComboBox()
        ocr_row = QHBoxLayout()
        ocr_row.addWidget(self.ocr_model_combo)
        self.ocr_button = QPushButton("识别")
        ocr_row.addWidget(self.ocr_button)
        translator_row = QHBoxLayout()
        translator_row.addWidget(self.translator_combo)
        self.translate_button = QPushButton("翻译")
        translator_row.addWidget(self.translate_button)
        ocr_trans_config_layout.addRow("OCR模型:", ocr_row)
        ocr_trans_config_layout.addRow("翻译器:", translator_row)
        ocr_trans_config_layout.addRow("目标语言:", self.target_language_combo)
        text_layout.addLayout(ocr_trans_config_layout)
        
        # 原文文本框
        self.original_text_box = QTextEdit()
        self.original_text_box.setUndoRedoEnabled(True)
        self.original_text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.original_text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.original_text_box.setMinimumHeight(80)
        self.original_text_box.setMaximumHeight(150)
        
        self.translated_text_box = QTextEdit()
        self.translated_text_box.setObjectName("translationEdit")
        self.translated_text_box.setUndoRedoEnabled(True)
        self.translated_text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.translated_text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.translated_text_box.setMinimumHeight(80)
        self.translated_text_box.setMaximumHeight(150)
        
        text_layout.addWidget(QLabel("原文:"))
        self.original_edit_status_label = QLabel("点击编辑原文")
        text_layout.addWidget(self.original_edit_status_label)
        text_layout.addWidget(QLabel("译文:"))
        text_layout.addWidget(self.translated_text_box)
        insert_buttons_layout = QHBoxLayout()
        insert_buttons_layout.setSpacing(4)
        self.insert_placeholder_button = QPushButton("占位符")
        self.insert_placeholder_button.setToolTip("插入占位符 ＿")
        self.insert_newline_button = QPushButton("换行↵")
        self.insert_newline_button.setToolTip("插入换行符")
        self.mark_horizontal_button = QPushButton("横排⇄")
        self.mark_horizontal_button.setToolTip("标记选中文字为横排显示")
        insert_buttons_layout.addWidget(self.insert_placeholder_button)
        insert_buttons_layout.addWidget(self.insert_newline_button)
        insert_buttons_layout.addWidget(self.mark_horizontal_button)
        text_layout.addLayout(insert_buttons_layout)
        self.text_stats_label = QLabel("字符数: 0")
        text_layout.addWidget(self.text_stats_label)
        layout.addWidget(self.text_edit_frame)

    def _create_style_section(self, layout):
        self.style_edit_frame = QGroupBox("样式设置")
        style_layout = QFormLayout(self.style_edit_frame)
        
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
                self.parent_widget._populate_font_list()
                
                # 恢复之前选择的值
                if current_data:
                    # 根据 itemData 查找
                    for i in range(self.count()):
                        if self.itemData(i) == current_data:
                            self.setCurrentIndex(i)
                            break
                elif current_text:
                    # 根据文本查找
                    index = self.findText(current_text)
                    if index >= 0:
                        self.setCurrentIndex(index)
                
                super().showPopup()
        
        self.font_family_combo = RefreshableComboBox(self)
        self.font_family_combo.setEditable(False)
        self._populate_font_list()
        style_layout.addRow("字体:", self.font_family_combo)
        
        # Font size
        font_size_layout = QHBoxLayout()
        self.font_size_input = QLineEdit()
        font_size_layout.addWidget(self.font_size_input)
        self.font_size_slider = CustomSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setRange(8, 72)
        style_layout.addRow("字体大小:", font_size_layout)
        style_layout.addRow("", self.font_size_slider)
        
        # Font color
        self.font_color_button = QPushButton()
        style_layout.addRow("字体颜色:", self.font_color_button)
        
        # Alignment and direction
        self.alignment_combo = QComboBox()
        self.direction_combo = QComboBox()
        style_layout.addRow("对齐:", self.alignment_combo)
        style_layout.addRow("方向:", self.direction_combo)
        
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
        
        # Add default option
        self.font_family_combo.addItem("默认字体", "")
        
        # Add font files
        for display_name, filename in font_files:
            self.font_family_combo.addItem(display_name, filename)

    def _create_action_section(self, layout):
        self.action_frame = QGroupBox("操作")
        action_layout = QHBoxLayout(self.action_frame)
        action_layout.setSpacing(4)
        self.copy_button = QPushButton("复制")
        self.paste_button = QPushButton("粘贴")
        self.delete_button = QPushButton("删除")
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
        self.show_refined_mask_checkbox.stateChanged.connect(lambda state: self.toggle_mask_visibility.emit(bool(state)))
        self.show_removed_checkbox.stateChanged.connect(lambda state: self.toggle_removed_mask_visibility.emit(bool(state)))
        self.update_mask_button.clicked.connect(self.update_mask_requested) # Changed
        self.mask_dilation_offset_entry.textChanged.connect(self._on_mask_config_changed) # Changed
        self.mask_kernel_size_entry.textChanged.connect(self._on_mask_config_changed) # Changed
        self.ignore_bubble_checkbox.stateChanged.connect(self._on_mask_config_changed) # Changed

        # Style
        self.font_family_combo.currentIndexChanged.connect(self._on_font_family_changed)
        self.font_size_input.editingFinished.connect(self._on_font_size_editing_finished)
        self.font_size_slider.valueChanged.connect(self._on_font_size_slider_changed)
        self.font_color_button.clicked.connect(self._on_font_color_clicked)
        # 实时更新（textChanged）
        self.translated_text_box.textChanged.connect(self._on_translated_text_changed)
        # self.translated_text_box.focusOutEvent = self._make_focus_out_handler(self.translated_text_box, self._on_translated_text_focus_out)
        self.alignment_combo.currentTextChanged.connect(self._on_alignment_changed)
        self.direction_combo.currentTextChanged.connect(self._on_direction_changed)

        # Text
        # 实时更新（textChanged）
        self.original_text_box.textChanged.connect(self._on_original_text_changed)
        # self.original_text_box.focusOutEvent = self._make_focus_out_handler(self.original_text_box, self._on_original_text_focus_out)
        self.ocr_button.clicked.connect(self.ocr_requested.emit)
        self.translate_button.clicked.connect(self.translation_requested.emit)
        self.insert_placeholder_button.clicked.connect(self._insert_placeholder)
        self.insert_newline_button.clicked.connect(self._insert_newline)
        self.mark_horizontal_button.clicked.connect(self._mark_horizontal)
        
        # Action buttons
        self.copy_button.clicked.connect(self.copy_region_requested.emit)
        self.paste_button.clicked.connect(self.paste_region_requested.emit)
        self.delete_button.clicked.connect(self.delete_region_requested.emit)


    def _on_mask_config_changed(self):
        """Gathers mask settings from UI and emits a signal."""
        try:
            dilation_offset = int(self.mask_dilation_offset_entry.text())
            kernel_size = int(self.mask_kernel_size_entry.text())
            ignore_bubble = self.ignore_bubble_checkbox.isChecked()

            # Basic validation
            if kernel_size <= 0 or kernel_size % 2 == 0:
                # Kernel size must be a positive odd number
                return

            update_dict = {
                "mask_dilation_offset": dilation_offset,
                "kernel_size": kernel_size,
                "ocr": {"ignore_bubble": ignore_bubble}
            }
            self.mask_config_changed.emit(update_dict)
        except ValueError:
            # Handle cases where text is not a valid integer
            pass

    def update_view_from_config(self, config_dict):
        """Updates the mask setting widgets based on the provided config dictionary."""
        # Block signals to prevent feedback loops
        self.mask_dilation_offset_entry.blockSignals(True)
        self.mask_kernel_size_entry.blockSignals(True)
        self.ignore_bubble_checkbox.blockSignals(True)

        self.mask_dilation_offset_entry.setText(str(config_dict.get("mask_dilation_offset", 70)))
        self.mask_kernel_size_entry.setText(str(config_dict.get("kernel_size", 3)))
        
        ocr_settings = config_dict.get("ocr", {})
        self.ignore_bubble_checkbox.setChecked(ocr_settings.get("ignore_bubble", False))

        # Unblock signals
        self.mask_dilation_offset_entry.blockSignals(False)
        self.mask_kernel_size_entry.blockSignals(False)
        self.ignore_bubble_checkbox.blockSignals(False)

    def _load_mask_config_from_settings(self):
        """从主页配置加载蒙版参数"""
        try:
            config = self.config_service.get_config()

            # 读取配置中的蒙版参数
            mask_dilation_offset = getattr(config, 'mask_dilation_offset', 70)
            kernel_size = getattr(config, 'kernel_size', 3)
            ignore_bubble = getattr(config.ocr, 'ignore_bubble', False) if hasattr(config, 'ocr') else False

            # 更新UI控件
            self.mask_dilation_offset_entry.blockSignals(True)
            self.mask_kernel_size_entry.blockSignals(True)
            self.ignore_bubble_checkbox.blockSignals(True)

            self.mask_dilation_offset_entry.setText(str(mask_dilation_offset))
            self.mask_kernel_size_entry.setText(str(kernel_size))
            self.ignore_bubble_checkbox.setChecked(ignore_bubble)

            self.mask_dilation_offset_entry.blockSignals(False)
            self.mask_kernel_size_entry.blockSignals(False)
            self.ignore_bubble_checkbox.blockSignals(False)

        except Exception as e:
            print(f"Error loading mask config from settings: {e}")
            # 设置默认值
            self.mask_dilation_offset_entry.setText("70")
            self.mask_kernel_size_entry.setText("3")
            self.ignore_bubble_checkbox.setChecked(False)


    def reload_config_settings(self):
        """公共方法：重新加载主页配置设置"""
        self._load_mask_config_from_settings()
        self.repopulate_options()  # 也重新加载其他选项

    def _connect_model_signals(self):
        self.model.display_mask_type_changed.connect(self._on_display_mask_type_changed)
        self.model.refined_mask_changed.connect(self._on_refined_mask_changed)
        self.model.regions_changed.connect(self.on_regions_updated)
        self.model.region_text_updated.connect(self.on_single_region_updated)
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

        # OCR
        ocr_options = self.app_logic.get_options_for_key('ocr')
        if ocr_options:
            self.ocr_model_combo.clear()
            self.ocr_model_combo.addItems(ocr_options)
            current_ocr = ocr_config.ocr
            if current_ocr in ocr_options:
                self.ocr_model_combo.setCurrentText(current_ocr)

        # Translator
        translator_map = self.app_logic.get_display_mapping('translator')
        if translator_map:
            self.translator_display_to_key = {v: k for k, v in translator_map.items()}
            self.translator_combo.clear()
            self.translator_combo.addItems(list(translator_map.values()))
            current_translator_key = translator_config.translator
            current_translator_display = translator_map.get(current_translator_key)
            if current_translator_display:
                self.translator_combo.setCurrentText(current_translator_display)

        # Target Language
        lang_map = self.app_logic.get_display_mapping('target_lang')
        if lang_map:
            self.lang_name_to_code = {v: k for k, v in lang_map.items()}
            self.target_language_combo.clear()
            self.target_language_combo.addItems(list(lang_map.values()))
            current_lang_key = translator_config.target_lang
            current_lang_display = lang_map.get(current_lang_key)
            if current_lang_display:
                self.target_language_combo.setCurrentText(current_lang_display)

        # Alignment
        alignment_map = self.app_logic.get_display_mapping('alignment')
        if alignment_map:
            self.alignment_combo.clear()
            self.alignment_combo.addItems(list(alignment_map.values()))

        # Direction
        direction_map = self.app_logic.get_display_mapping('direction')
        if direction_map:
            self.direction_combo.clear()
            self.direction_combo.addItems(list(direction_map.values()))

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
        if not selected_indices or len(selected_indices) > 1:
            self.clear_and_disable_selection_dependent()
        else:
            self.info_group.setEnabled(True)
            self.text_edit_frame.setEnabled(True)
            self.style_edit_frame.setEnabled(True)
            self.action_frame.setEnabled(True)
            region_index = selected_indices[0]
            self.current_region_index = region_index
            regions = self.model.get_regions()
            if 0 <= region_index < len(regions):
                self._update_display(regions[region_index], region_index)

    def clear_and_disable_selection_dependent(self):
        """Clears selection-dependent fields and disables their sections."""
        # Disable sections that depend on a selection
        self.info_group.setEnabled(False)
        self.text_edit_frame.setEnabled(False)
        self.style_edit_frame.setEnabled(False)
        self.action_frame.setEnabled(False)

        self.current_region_index = -1

        # Block signals to prevent them from firing during programmatic clear
        for child in self.findChildren(QWidget):
            if isinstance(child, (QLineEdit, QTextEdit, QComboBox, QSlider)):
                child.blockSignals(True)
        
        self.original_text_box.clear()
        self.translated_text_box.clear()
        self.font_size_input.clear()
        default_color = self.config_service.get_config().render.font_color or "#000000"
        self.font_color_button.setStyleSheet(f"background-color: {default_color};")
        self.index_label.setText("-")
        self.bbox_label.setText("-")
        self.size_label.setText("-")
        self.angle_label.setText("-")
        
        # Re-enable signals
        for child in self.findChildren(QWidget):
            if isinstance(child, (QLineEdit, QTextEdit, QComboBox, QSlider)):
                child.blockSignals(False)

    def _update_display(self, region_data, region_index, force=False):
        """Populate all widgets with data from the selected region.
        
        Args:
            region_data: 区域数据字典
            region_index: 区域索引
            force: 是否强制更新文本框（忽略焦点状态），用于OCR/翻译完成后
        """
        # Block signals on all widgets to prevent feedback loops
        for child in self.findChildren(QWidget):
            if isinstance(child, (QLineEdit, QTextEdit, QComboBox, QSlider)):
                child.blockSignals(True)

        # --- Update Region Info ---
        self.index_label.setText(str(region_index))
        bbox = self._calculate_bbox(region_data)
        if bbox:
            self.bbox_label.setText(f"({bbox[0]:.0f}, {bbox[1]:.0f})")
            self.size_label.setText(f"{bbox[2]-bbox[0]:.0f} × {bbox[3]-bbox[1]:.0f}")
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

            # 1. 将所有 AI 换行符 ([BR], <br>, 【BR】) 转换为 \n
            translation_text = region_data.get("translation", "")
            translation_text = re.sub(r'\s*(\[BR\]|<br>|【BR】)\s*', '\n', translation_text, flags=re.IGNORECASE)

            # 2. 如果是竖排且开启了自动旋转符号,自动添加 <H> 标签(仅用于显示)
            direction = region_data.get('direction', 'auto')
            is_vertical = direction in ('v', 'vertical')
            if direction == 'auto':
                is_vertical = not region_data.get('horizontal', True)

            if is_vertical and self.config_service.get_config().render.auto_rotate_symbols:
                # 使用与后端一致的正则表达式(英文数字2+字符，符号2-4字符)
                # 但是要避免重复添加:如果已经有 <H> 标签,就不添加
                if '<H>' not in translation_text.upper():
                    horizontal_char_pattern = r'([a-zA-Z0-9_.-]{2,}|[!?！？]{2,4})'
                    translation_text = re.sub(horizontal_char_pattern, r'<H>\1</H>', translation_text)

            # 3. 将 <H> 标签替换为符号 ⇄ 显示在文本框中
            display_text = translation_text.replace('<H>', '⇄').replace('</H>', '⇄')

            # 4. 将 \n 替换为 ↵ 显示在文本框中
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

        self.font_color_button.setStyleSheet(f"background-color: {color_hex};")
        
        # Update font family selector
        font_path = region_data.get("font_path", "")
        if font_path:
            # Extract filename from path
            import os
            font_filename = os.path.basename(font_path)
            # Find and select the item with this filename
            for i in range(self.font_family_combo.count()):
                if self.font_family_combo.itemData(i) == font_filename:
                    self.font_family_combo.setCurrentIndex(i)
                    break
            else:
                # Not found, set to default
                self.font_family_combo.setCurrentIndex(0)
        else:
            # Use default font
            self.font_family_combo.setCurrentIndex(0)
        
        alignment_map = {"auto": "自动", "left": "左对齐", "center": "居中", "right": "右对齐"}
        self.alignment_combo.setCurrentText(alignment_map.get(region_data.get("alignment", "auto"), "自动"))
        
        direction_map = {"auto": "自动", "horizontal": "横排", "vertical": "竖排", "h": "横排", "v": "竖排"}
        self.direction_combo.setCurrentText(direction_map.get(region_data.get("direction", "auto"), "自动"))

        # --- Update Mask Checkboxes ---
        display_mask_type = self.model.get_display_mask_type()
        self.show_refined_mask_checkbox.setChecked(display_mask_type == 'refined')

        # Unblock signals
        for child in self.findChildren(QWidget):
            if isinstance(child, (QLineEdit, QTextEdit, QComboBox, QSlider)):
                child.blockSignals(False)

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

        # 检查是否有变化
        region_data = self.model.get_region_by_index(self.current_region_index)
        if region_data and region_data.get("translation", "") != text_with_br:
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
        if self.current_region_index != -1:
            self.original_text_modified.emit(self.current_region_index, self.original_text_box.toPlainText())
    def _on_translated_text_changed(self):
        if self.current_region_index != -1:
            import re

            # 1. 将 ⇄ 替换回 <H> 标签
            raw_text = self.translated_text_box.toPlainText()
            text_with_tags = convert_arrows_to_tags(raw_text)

            # 2. 将 ↵ 替换回 \n
            text_with_newlines = text_with_tags.replace('↵', '\n')

            self.translated_text_modified.emit(self.current_region_index, text_with_newlines)
    
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
        text = self.font_size_input.text()
        if text.isdigit() and self.current_region_index != -1:
            value = int(text)
            if self.font_size_slider.value() != value:
                self.font_size_slider.setValue(value)
            self.font_size_changed.emit(self.current_region_index, value)

    def _on_font_size_slider_changed(self, value): 
        if self.current_region_index != -1:
            self.font_size_input.setText(str(value))
            self.font_size_changed.emit(self.current_region_index, value)
    def _on_font_family_changed(self, index):
        if self.current_region_index == -1 or self.block_updates:
            return
        # Get the font filename from combo box data
        font_filename = self.font_family_combo.itemData(index)
        if font_filename is None:
            font_filename = ""
        self.font_family_changed.emit(self.current_region_index, font_filename)
    
    def _on_font_color_clicked(self):
        if self.current_region_index == -1:
            return
        current_color_str = self.font_color_button.styleSheet().replace("background-color: ", "")
        current_color = QColor(current_color_str) if current_color_str else QColor("black")
        color = QColorDialog.getColor(current_color, self, "选择字体颜色")
        if color.isValid():
            hex_color = color.name()
            self.font_color_button.setStyleSheet(f"background-color: {hex_color};")
            self.font_color_changed.emit(self.current_region_index, hex_color)

    def _on_mask_tool_changed(self, button):
        if button == self.select_button:
            self.mask_tool_changed.emit('select')
        elif button == self.brush_button:
            self.mask_tool_changed.emit('brush')
        elif button == self.eraser_button:
            self.mask_tool_changed.emit('eraser')

    def _on_brush_size_changed(self, value):
        self.brush_size_label.setText(str(value))
        self.brush_size_changed.emit(value)

    def _on_alignment_changed(self, text: str):
        if self.current_region_index != -1:
            self.alignment_changed.emit(self.current_region_index, text)

    def _on_direction_changed(self, text: str):
        if self.current_region_index != -1:
            self.direction_changed.emit(self.current_region_index, text)

    def _calculate_bbox(self, region_data):
        """计算区域边界框"""
        lines = region_data.get('lines', [])
        if not lines or not lines[0]:
            return None
        
        all_points = lines[0]
        if not all_points:
            return None
        
        x_coords = [p[0] for p in all_points]
        y_coords = [p[1] for p in all_points]
        
        return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))

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
        """用 ⇄ 符号包裹选中的文本,标记为横排"""
        cursor = self.translated_text_box.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            # Qt 的 selectedText() 会将段落分隔符转换为 \u2029,需要替换回 ↵
            selected_text = selected_text.replace('\u2029', '↵')
            cursor.insertText(f"⇄{selected_text}⇄")

    def _on_ocr_model_change(self, text):
        # Placeholder: This should emit a signal to the controller
        print(f"OCR Model changed to: {text}")

    def _on_translator_change(self, display_name):
        translator_key = self.translator_display_to_key.get(display_name, display_name)
        # Placeholder: This should emit a signal to the controller
        print(f"Translator changed to: {translator_key}")

    def _on_target_language_change(self, display_name):
        lang_code = self.lang_name_to_code.get(display_name, "CHS")
        # Placeholder: This should emit a signal to the controller
        print(f"Target language changed to: {lang_code}")

