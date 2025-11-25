
import os
from functools import partial

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSettings, QByteArray
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from services import get_config_service
from widgets.file_list_view import FileListView
from widgets.custom_comic_panel import CustomComicPanel
from utils.resource_helper import resource_path


class MainView(QWidget):
    """
    主翻译视图，对应旧UI的 MainView。
    包含文件列表、设置和日志。
    """
    setting_changed = pyqtSignal(str, object)
    env_var_changed = pyqtSignal(str, str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.config_service = get_config_service()
        self.env_widgets = {}
        self._env_debounce_timer = QTimer(self)
        self._env_debounce_timer.setSingleShot(True)
        self._env_debounce_timer.setInterval(500) # 500ms debounce delay

        self.layout = QHBoxLayout(self)
        self.env_var_changed.connect(self.controller.save_env_var)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # --- 创建主分割器 (左右) ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.layout.addWidget(main_splitter)

        # --- 左侧面板 ---
        left_panel = self._create_left_panel()

        # --- 右侧面板 ---
        right_panel = self._create_right_panel()

        # --- 组合布局 ---
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(1, 1) # 让右侧面板拉伸
        main_splitter.setSizes([300, 900]) # 设置初始大致比例

        self._create_dynamic_settings()

        # Connect signals for button state management
        self.controller.state_manager.is_translating_changed.connect(self.on_translation_state_changed, type=Qt.ConnectionType.QueuedConnection)
        self.controller.state_manager.current_config_changed.connect(self.update_start_button_text)
        QTimer.singleShot(100, self.update_start_button_text) # Set initial text
        QTimer.singleShot(100, self._sync_workflow_mode_from_config) # Sync workflow mode dropdown

    @pyqtSlot(dict)
    def set_parameters(self, config: dict):
        """
        Receives a config dictionary and starts the incremental creation of setting widgets.
        """
        # Store config and sections to process
        self._config_to_process = config
        self._sections_to_process = [
            "translator", "cli", "detector", "inpainter",
            "render", "upscale", "colorizer", "ocr", "global"
        ]
        
        # Clear existing widgets immediately
        for panel in self.tab_frames.values():
            layout = panel.layout()
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

        # Schedule the first chunk of work
        QTimer.singleShot(0, self._process_next_setting_chunk)

    def _process_next_setting_chunk(self):
        """
        Processes one section of the settings UI and schedules the next one.
        """
        if not self._sections_to_process:
            self._finalize_settings_ui()
            return

        section = self._sections_to_process.pop(0)
        config = self._config_to_process

        panel_map = {
            "translator": self.tab_frames["基础设置_left"],
            "cli": self.tab_frames["基础设置_right"],
            "detector": self.tab_frames["高级设置_left"],
            "inpainter": self.tab_frames["高级设置_left"],
            "render": self.tab_frames["高级设置_right"],
            "upscale": self.tab_frames["高级设置_right"],
            "colorizer": self.tab_frames["高级设置_right"],
            "ocr": self.tab_frames["选项_left"],
            "global": self.tab_frames["选项_right"],
        }

        panel = panel_map.get(section)
        if section == "global":
            # 处理顶层的全局参数
            global_params = {k: v for k, v in config.items() if k not in ["translator", "cli", "detector", "inpainter", "render", "upscale", "colorizer", "ocr", "app"]}
            if global_params and panel:
                self._create_param_widgets(global_params, panel.layout(), "")
        elif panel and section in config:
            self._create_param_widgets(config[section], panel.layout(), section)

        # Schedule the next chunk
        QTimer.singleShot(0, self._process_next_setting_chunk)

    def _finalize_settings_ui(self):
        """
        Called after all incremental updates are done. Sets up dependent UI like .env section.
        """
        translator_combo = self.findChild(QComboBox, "translator.translator")
        if translator_combo:
            parent_layout = translator_combo.parent().layout()
            if parent_layout and not hasattr(self, 'env_group_box'):
                self.env_group_box = QGroupBox("API密钥 (.env)")
                self.env_layout = QFormLayout(self.env_group_box)
                parent_layout.addWidget(self.env_group_box)
            try:
                translator_combo.currentTextChanged.disconnect(self._on_translator_changed)
            except TypeError:
                pass
            translator_combo.currentTextChanged.connect(self._on_translator_changed)
            self._on_translator_changed(translator_combo.currentText())

    def _create_dynamic_settings(self):
        """读取配置文件并动态创建所有设置控件"""
        try:
            config = self.config_service.get_config().dict() # Get default config
            self.set_parameters(config)
        except Exception as e:
            print(f"Error creating dynamic settings: {e}")

    def _on_setting_changed(self, value, full_key, display_map=None):
        """A slot to handle when any setting widget is changed by the user."""
        final_value = value
        # Handle reverse mapping for QComboBox
        if display_map:
            reverse_map = {v: k for k, v in display_map.items()}
            final_value = reverse_map.get(value, value) # Fallback to value itself if not in map
        
        # 特殊处理：当 upscaler 变化时，更新 upscale_ratio 动态下拉框
        if full_key == "upscale.upscaler":
            self._update_upscale_ratio_options(value)
        
        self.setting_changed.emit(full_key, final_value)

    def _on_upscale_ratio_changed(self, text, full_key):
        """处理 upscale_ratio 动态下拉框的变化"""
        config = self.config_service.get_config()
        
        if config.upscale.upscaler == "realcugan":
            # 当前是 realcugan
            if text == "不使用":
                # 禁用超分
                self.setting_changed.emit("upscale.upscale_ratio", None)
                self.setting_changed.emit("upscale.realcugan_model", None)
            else:
                # text 可能是中文显示名称，需要转换回英文值
                display_map = self.controller.get_display_mapping("realcugan_model")
                model_value = text
                
                # 如果有display_map，进行反向查找
                if display_map:
                    reverse_map = {v: k for k, v in display_map.items()}
                    model_value = reverse_map.get(text, text)
                
                # 从模型名称中提取倍率
                scale_str = model_value.split('x')[0] if 'x' in model_value else None
                if scale_str and scale_str.isdigit():
                    scale = int(scale_str)
                    # 同时更新 realcugan_model 和 upscale_ratio
                    self.setting_changed.emit("upscale.realcugan_model", model_value)
                    self.setting_changed.emit("upscale.upscale_ratio", scale)
                else:
                    # 无法解析倍率，只更新模型
                    self.setting_changed.emit("upscale.realcugan_model", model_value)
        else:
            # 当前是其他超分模型，text 是倍率
            if text == "不使用":
                self.setting_changed.emit(full_key, None)
            else:
                try:
                    ratio = int(text)
                    self.setting_changed.emit(full_key, ratio)
                except ValueError:
                    self.setting_changed.emit(full_key, None)
    
    def _on_numeric_input_changed(self, text, full_key, value_type):
        """统一处理数值类型输入框的变化（支持 int 和 float）"""
        if not text or not text.strip():
            # 空值 = 使用默认值 (None)
            self.setting_changed.emit(full_key, None)
        else:
            try:
                value = value_type(text)
                self.setting_changed.emit(full_key, value)
            except ValueError:
                # 无效输入 = 使用默认值
                self.setting_changed.emit(full_key, None)
    
    def _update_upscale_ratio_options(self, upscaler):
        """当 upscaler 变化时，更新 upscale_ratio 下拉框的选项"""
        # 查找 upscale_ratio_dynamic widget
        upscale_ratio_widget = self.findChild(QComboBox, "upscale_ratio_dynamic")
        if not upscale_ratio_widget:
            return
        
        # 阻止信号触发
        upscale_ratio_widget.blockSignals(True)
        
        # 清空并重新填充
        upscale_ratio_widget.clear()
        
        if upscaler == "realcugan":
            # 显示 Real-CUGAN 模型列表（使用中文显示）
            realcugan_models = self.controller.get_options_for_key("realcugan_model")
            display_map = self.controller.get_display_mapping("realcugan_model")
            
            if realcugan_models:
                # 如果有display_map，使用中文名称
                if display_map:
                    display_options = [display_map.get(model, model) for model in realcugan_models]
                    all_options = ["不使用"] + display_options
                else:
                    all_options = ["不使用"] + realcugan_models
                
                upscale_ratio_widget.addItems(all_options)
            
            # 设置默认值
            config = self.config_service.get_config()
            if config.upscale.realcugan_model:
                # 如果有display_map，显示中文名称
                if display_map:
                    display_name = display_map.get(config.upscale.realcugan_model, config.upscale.realcugan_model)
                    upscale_ratio_widget.setCurrentText(display_name)
                else:
                    upscale_ratio_widget.setCurrentText(config.upscale.realcugan_model)
            elif config.upscale.upscale_ratio is None:
                upscale_ratio_widget.setCurrentText("不使用")
            elif realcugan_models:
                if display_map:
                    upscale_ratio_widget.setCurrentText(display_map.get(realcugan_models[0], realcugan_models[0]))
                else:
                    upscale_ratio_widget.setCurrentText(realcugan_models[0])
        else:
            # 显示普通倍率选项
            ratio_options = ["不使用", "2", "3", "4"]
            upscale_ratio_widget.addItems(ratio_options)
            # 设置默认值
            config = self.config_service.get_config()
            if config.upscale.upscale_ratio is None:
                upscale_ratio_widget.setCurrentText("不使用")
            else:
                upscale_ratio_widget.setCurrentText(str(config.upscale.upscale_ratio))
        
        # 恢复信号
        upscale_ratio_widget.blockSignals(False)

    def _create_param_widgets(self, data, parent_layout, prefix=""):
        if not isinstance(data, dict):
            return

        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key

            # 跳过这些选项，因为已经用下拉框替代或不需要在UI中显示
            # realcugan_model 将通过 upscale_ratio 动态下拉框处理
            # batch_concurrent 并发处理已隐藏
            # gimp_font 已废弃，使用 font_path 代替
            if full_key in ["cli.load_text", "cli.template", "cli.generate_and_export", "cli.colorize_only", "cli.upscale_only", "upscale.realcugan_model", "cli.batch_concurrent", "render.gimp_font"]:
                continue

            label_text = key
            if self.controller.get_display_mapping('labels') and self.controller.get_display_mapping('labels').get(key):
                label_text = self.controller.get_display_mapping('labels').get(key)
            label = QLabel(f"{label_text}:")
            widget = None

            options = self.controller.get_options_for_key(key)
            display_map = self.controller.get_display_mapping(key)

            if full_key == "render.font_path":
                container = QWidget()
                hbox = QHBoxLayout(container)
                hbox.setContentsMargins(0, 0, 0, 0)
                
                # 创建自定义ComboBox,在下拉时刷新字体列表
                class RefreshableComboBox(QComboBox):
                    def showPopup(self):
                        current_text = self.currentText()
                        self.clear()
                        try:
                            fonts_dir = resource_path('fonts')
                            if os.path.isdir(fonts_dir):
                                font_files = sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
                                self.addItems(font_files)
                        except Exception as e:
                            print(f"Error scanning fonts directory: {e}")
                        # 恢复之前选择的值
                        if current_text:
                            index = self.findText(current_text)
                            if index >= 0:
                                self.setCurrentIndex(index)
                            else:
                                self.setCurrentText(current_text)
                        super().showPopup()
                
                combo = RefreshableComboBox()
                try:
                    fonts_dir = resource_path('fonts')
                    if os.path.isdir(fonts_dir):
                        font_files = sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
                        combo.addItems(font_files)
                except Exception as e:
                    print(f"Error scanning fonts directory: {e}")
                combo.setCurrentText(str(value) if value else "")
                combo.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(text, k, None))
                button = QPushButton("打开目录")
                button.clicked.connect(self.controller.open_font_directory)
                hbox.addWidget(combo)
                hbox.addWidget(button)
                widget = container

            elif full_key == "translator.high_quality_prompt_path":
                container = QWidget()
                hbox = QHBoxLayout(container)
                hbox.setContentsMargins(0, 0, 0, 0)
                
                # 创建自定义ComboBox,在下拉时刷新提示词列表
                class RefreshablePromptComboBox(QComboBox):
                    def __init__(self, controller_ref, parent=None):
                        super().__init__(parent)
                        self.controller_ref = controller_ref
                    
                    def showPopup(self):
                        current_text = self.currentText()
                        self.clear()
                        prompt_files = self.controller_ref.get_hq_prompt_options()
                        if prompt_files:
                            self.addItems(prompt_files)
                        # 恢复之前选择的值
                        if current_text:
                            index = self.findText(current_text)
                            if index >= 0:
                                self.setCurrentIndex(index)
                            else:
                                self.setCurrentText(current_text)
                        super().showPopup()
                
                combo = RefreshablePromptComboBox(self.controller)
                prompt_files = self.controller.get_hq_prompt_options()
                if prompt_files:
                    combo.addItems(prompt_files)
                filename = os.path.basename(value) if value else ""
                combo.setCurrentText(filename)
                combo.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(os.path.join('dict', text).replace('\\', '/') if text else None, k, None))
                button = QPushButton("打开目录")
                button.clicked.connect(self.controller.open_dict_directory)
                hbox.addWidget(combo)
                hbox.addWidget(button)
                widget = container

            elif isinstance(value, bool):
                widget = QCheckBox()
                widget.setChecked(value)
                widget.stateChanged.connect(lambda state, k=full_key: self._on_setting_changed(bool(state), k, None))

            # 特殊处理：upscale_ratio 动态下拉框（必须在 int/float 判断之前）
            elif full_key == "upscale.upscale_ratio":
                widget = QComboBox()
                widget.setObjectName("upscale_ratio_dynamic")
                
                # 获取当前的 upscaler 值来决定显示什么选项
                config = self.config_service.get_config()
                current_upscaler = config.upscale.upscaler
                
                if current_upscaler == "realcugan":
                    # 显示 Real-CUGAN 模型列表（使用中文显示）
                    realcugan_models = self.controller.get_options_for_key("realcugan_model")
                    display_map = self.controller.get_display_mapping("realcugan_model")
                    
                    if realcugan_models:
                        # 如果有display_map，使用中文名称
                        if display_map:
                            display_options = [display_map.get(model, model) for model in realcugan_models]
                            all_options = ["不使用"] + display_options
                        else:
                            all_options = ["不使用"] + realcugan_models
                        widget.addItems(all_options)
                    
                    # 设置当前值（从 realcugan_model 获取）
                    current_model = config.upscale.realcugan_model
                    if current_model:
                        # 如果有display_map，显示中文名称
                        if display_map:
                            display_name = display_map.get(current_model, current_model)
                            widget.setCurrentText(display_name)
                        else:
                            widget.setCurrentText(current_model)
                    elif value is None:
                        widget.setCurrentText("不使用")
                    elif realcugan_models:
                        widget.setCurrentText(realcugan_models[0])
                else:
                    # 显示普通倍率选项
                    ratio_options = ["不使用", "2", "3", "4"]
                    widget.addItems(ratio_options)
                    # 设置当前值
                    if value is None:
                        widget.setCurrentText("不使用")
                    else:
                        widget.setCurrentText(str(value))
                
                widget.currentTextChanged.connect(lambda text, k=full_key: self._on_upscale_ratio_changed(text, k))
            
            elif isinstance(value, (int, float)):
                widget = QLineEdit(str(value))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, float if isinstance(value, float) else int))

            elif value is None and key in ['tile_size', 'line_spacing', 'font_size']:
                # 处理值为 None 的数值类型参数（Optional[int] 或 Optional[float]）
                widget = QLineEdit("")
                # 根据参数名设置提示文本
                if key == 'tile_size':
                    widget.setPlaceholderText("默认: 400")
                    widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, int))
                elif key == 'line_spacing':
                    widget.setPlaceholderText("横排默认: 0.01, 竖排默认: 0.2")
                    widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, float))
                elif key == 'font_size':
                    widget.setPlaceholderText("自动")
                    widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, int))

            elif (isinstance(value, str) or value is None) and (options or display_map):
                widget = QComboBox()
                if key == "translator":
                    widget.setObjectName("translator.translator")
                
                if display_map:
                    widget.addItems(list(display_map.values()))
                    current_display_name = display_map.get(value) if value is not None else None
                    if current_display_name:
                        widget.setCurrentText(current_display_name)
                    widget.currentTextChanged.connect(lambda text, k=full_key, dm=display_map: self._on_setting_changed(text, k, dm))
                else:
                    widget.addItems(options)
                    if value is not None:
                        widget.setCurrentText(value)
                    else:
                        # 对于 None 值，设置第一个选项为默认值（通常是 "不使用"）
                        if options:
                            widget.setCurrentText(options[0])
                    widget.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(text, k, None))

            elif isinstance(value, str):
                widget = QLineEdit(value)
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_setting_changed(w.text(), k, None))
            
            if widget:
                parent_layout.addRow(label, widget)


    def _create_left_panel(self) -> QWidget:
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # 文件操作按钮
        file_button_widget = QWidget()
        file_buttons_layout = QHBoxLayout(file_button_widget)
        file_buttons_layout.setContentsMargins(0,0,0,0)
        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.advanced_folder_button = QPushButton("高级文件夹")
        self.clear_list_button = QPushButton("清空列表")
        file_buttons_layout.addWidget(self.add_files_button)
        file_buttons_layout.addWidget(self.add_folder_button)
        file_buttons_layout.addWidget(self.advanced_folder_button)
        file_buttons_layout.addWidget(self.clear_list_button)
        left_layout.addWidget(file_button_widget)

        # 文件列表
        self.file_list = FileListView(None, self) # Pass None for model initially
        left_layout.addWidget(self.file_list)

        # --- Output Folder ---
        output_folder_label = QLabel("输出目录:")
        left_layout.addWidget(output_folder_label)

        output_folder_widget = QWidget()
        output_folder_layout = QHBoxLayout(output_folder_widget)
        output_folder_layout.setContentsMargins(0,0,0,0)
        self.output_folder_input = QLineEdit()
        self.output_folder_input.setPlaceholderText("选择或拖入输出文件夹...")
        self.browse_output_button = QPushButton("浏览...")
        self.open_output_button = QPushButton("打开")
        output_folder_layout.addWidget(self.output_folder_input)
        output_folder_layout.addWidget(self.browse_output_button)
        output_folder_layout.addWidget(self.open_output_button)
        left_layout.addWidget(output_folder_widget)

        # 翻译流程模式选择（放在开始翻译按钮上面）
        workflow_label = QLabel("翻译流程模式:")
        left_layout.addWidget(workflow_label)

        from PyQt6.QtWidgets import QComboBox
        self.workflow_mode_combo = QComboBox()
        self.workflow_mode_combo.addItems([
            "正常翻译流程",
            "导出翻译",
            "导出原文",
            "导入翻译并渲染",
            "仅上色",
            "仅超分"
        ])
        self.workflow_mode_combo.currentIndexChanged.connect(self._on_workflow_mode_changed)
        left_layout.addWidget(self.workflow_mode_combo)

        self.start_button = QPushButton("开始翻译")
        self.start_button.setFixedHeight(40)
        left_layout.addWidget(self.start_button)

        # Connect all signals at the end, after all widgets are created
        self.add_files_button.clicked.connect(self._trigger_add_files)
        self.add_folder_button.clicked.connect(self.controller.add_folder)
        self.advanced_folder_button.clicked.connect(self.controller.open_advanced_folder)
        self.clear_list_button.clicked.connect(self.controller.clear_file_list)
        self.file_list.file_remove_requested.connect(self.controller.remove_file)
        self.browse_output_button.clicked.connect(self.controller.select_output_folder)
        self.open_output_button.clicked.connect(self.controller.open_output_folder)
        self.start_button.clicked.connect(self.controller.start_backend_task)

        return left_panel

    def _create_right_panel(self) -> QWidget:
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # 右侧的上下分割器 (设置 vs 日志)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_layout.addWidget(self.right_splitter)

        # 设置标签页
        self.settings_tabs = QTabWidget()
        self.right_splitter.addWidget(self.settings_tabs)

        # --- 动态创建标签页和其内部布局 ---
        self.tab_frames = {}
        tabs_to_create = ["基础设置", "高级设置", "选项"]
        for tab_name in tabs_to_create:
            tab_content_widget = QWidget()
            tab_layout = QHBoxLayout(tab_content_widget)
            tab_layout.setContentsMargins(0,0,0,0)
            
            tab_splitter = QSplitter(Qt.Orientation.Horizontal)
            tab_layout.addWidget(tab_splitter)

            # 每一页都有一左一右两个滚动区域
            left_scroll = QScrollArea()
            left_scroll.setWidgetResizable(True)
            right_scroll = QScrollArea()
            right_scroll.setWidgetResizable(True)

            # 用一个空的QWidget作为滚动区域的内容，后续动态添加控件
            left_scroll_content = QWidget()
            right_scroll_content = QWidget()
            left_scroll.setWidget(left_scroll_content)
            right_scroll.setWidget(right_scroll_content)

            # 给滚动区域的内容设置布局，以便添加控件
            QFormLayout(left_scroll_content)
            QFormLayout(right_scroll_content)

            tab_splitter.addWidget(left_scroll)
            tab_splitter.addWidget(right_scroll)

            self.settings_tabs.addTab(tab_content_widget, tab_name)
            
            # 保存对滚动区域内容面板的引用，以便后续添加控件
            self.tab_frames[f"{tab_name}_left"] = left_scroll_content
            self.tab_frames[f"{tab_name}_right"] = right_scroll_content
        
        # 添加自定义标签页（单列布局）
        self.custom_panel = CustomComicPanel(self)
        self.settings_tabs.addTab(self.custom_panel, "自定义")

        # 日志框
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("日志输出...")
        self.right_splitter.addWidget(self.log_box)

        self.right_splitter.setStretchFactor(0, 2) # 让设置面板占据更多空间
        self.right_splitter.setStretchFactor(1, 1)

        # 监听标签页切换，自定义标签页隐藏全局日志
        self.settings_tabs.currentChanged.connect(self._on_tab_changed)
        
        # 监听 Splitter 移动，保存位置（signal 带参数，这里用 lambda 丢弃参数）
        self.right_splitter.splitterMoved.connect(lambda pos, index: self._save_right_splitter_state())
        
        # 恢复 Splitter 位置
        QTimer.singleShot(0, self._restore_right_splitter_state)
        # 退出前保存一次位置
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._save_right_splitter_state)

        return right_panel
    
    def _on_tab_changed(self, index: int):
        """标签页切换时，控制全局日志框的显示/隐藏"""
        current_tab_name = self.settings_tabs.tabText(index)
        if current_tab_name == "自定义":
            # 切换到自定义标签页，隐藏全局日志
            # 进入自定义前保存一次当前大小
            self._save_right_splitter_state()
            self.log_box.setVisible(False)
        else:
            # 切换到其他标签页（基础设置、高级设置、选项），显示全局日志
            self.log_box.setVisible(True)
            # 日志重新可见后再恢复一次分割条位置
            QTimer.singleShot(0, self._restore_right_splitter_state)
    
    def _save_right_splitter_state(self):
        """保存主 Splitter（设置区 vs 日志区）的位置"""
        try:
            settings = QSettings("MangaTranslator", "MainView")
            settings.setValue("ui/right_splitter_state", self.right_splitter.saveState())
            settings.setValue("ui/right_splitter_sizes", self.right_splitter.sizes())
        except Exception:
            pass
    
    def _restore_right_splitter_state(self):
        """恢复主 Splitter 的位置"""
        try:
            settings = QSettings("MangaTranslator", "MainView")
            state = settings.value("ui/right_splitter_state", None, type=QByteArray)
            if state:
                try:
                    # 优先使用更稳健的状态恢复
                    self.right_splitter.restoreState(state)
                    return
                except Exception:
                    pass
            sizes = settings.value("ui/right_splitter_sizes")
            if sizes:
                try:
                    if isinstance(sizes, list):
                        sizes = [int(x) for x in sizes]
                    self.right_splitter.setSizes(sizes)
                except Exception:
                    pass
        except Exception:
            pass

    def append_log(self, message):
        """安全地将消息追加到日志框。"""
        self.log_box.append(message.strip())
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _on_translator_changed(self, display_name: str):
        """当翻译器下拉菜单变化时，动态更新所需的.env输入字段"""
        translator_key = self.controller.get_display_mapping('translator').get(display_name, display_name.lower())
        reverse_map = {v: k for k, v in self.controller.get_display_mapping('translator').items()}
        translator_key = reverse_map.get(display_name, display_name.lower())

        # Clear previous env widgets
        while self.env_layout.rowCount() > 0:
            self.env_layout.removeRow(0)
        self.env_widgets.clear()

        if not translator_key:
            self.env_group_box.setVisible(False)
            return

        all_vars = self.config_service.get_all_env_vars(translator_key)
        if not all_vars:
            self.env_group_box.setVisible(False)
            return
            
        self.env_group_box.setVisible(True)
        current_env_values = self.config_service.load_env_vars()
        self._create_env_widgets(all_vars, current_env_values)

    def _create_env_widgets(self, keys: list, current_values: dict):
        """为给定的键创建标签和输入框"""
        for key in keys:
            value = current_values.get(key, "")
            label_text = self.controller.get_display_mapping('labels').get(key, key)
            label = QLabel(f"{label_text}:")
            widget = QLineEdit(value)
            widget.textChanged.connect(partial(self._debounced_save_env_var, key))
            self.env_layout.addRow(label, widget)
            self.env_widgets[key] = (label, widget)

    def _debounced_save_env_var(self, key: str, text: str):
        """防抖保存.env变量"""
        self._env_debounce_timer.stop()
        try:
            self._env_debounce_timer.timeout.disconnect()
        except TypeError:
            pass  # No connection to disconnect, which is fine.
        self._env_debounce_timer.timeout.connect(lambda: self.env_var_changed.emit(key, text))
        self._env_debounce_timer.start()

    def update_output_path_display(self, path: str):
        """Slot to update the text of the output folder input field."""
        self.output_folder_input.setText(path)

    def _trigger_add_files(self):
        """触发添加文件对话框"""
        last_dir = self.controller.get_last_open_dir()
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, 
            "添加文件", 
            last_dir, 
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if file_paths:
            self.controller.add_files(file_paths)
            # Save the directory of the first selected file for next time
            new_dir = os.path.dirname(file_paths[0])
            self.controller.set_last_open_dir(new_dir)

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        self.app_logic.shutdown()
        event.accept()

    @pyqtSlot(bool)
    def on_translation_state_changed(self, is_translating: bool):
        """Handles the change of the translation state to update the start/stop button."""
        if is_translating:
            self.start_button.setText("停止翻译")
            self.start_button.setStyleSheet("background-color: #C53929; color: white;")
            try:
                self.start_button.clicked.disconnect()
            except TypeError:
                pass
            self.start_button.clicked.connect(self.controller.stop_task)
        else:
            self.start_button.setStyleSheet("")
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self.start_button.update()
            
            try:
                self.start_button.clicked.disconnect()
            except TypeError:
                pass
            self.start_button.clicked.connect(self.controller.start_backend_task)
            self.update_start_button_text()

    def _sync_workflow_mode_from_config(self):
        """从配置同步下拉框的选择"""
        try:
            config = self.config_service.get_config()

            # 阻止信号触发，避免循环
            self.workflow_mode_combo.blockSignals(True)

            if config.cli.upscale_only:
                self.workflow_mode_combo.setCurrentIndex(5)  # 仅超分
            elif config.cli.colorize_only:
                self.workflow_mode_combo.setCurrentIndex(4)  # 仅上色
            elif config.cli.load_text:
                self.workflow_mode_combo.setCurrentIndex(3)  # 导入翻译并渲染
            elif config.cli.template:
                self.workflow_mode_combo.setCurrentIndex(2)  # 导出原文
            elif config.cli.generate_and_export:
                self.workflow_mode_combo.setCurrentIndex(1)  # 导出翻译
            else:
                self.workflow_mode_combo.setCurrentIndex(0)  # 正常翻译流程

            self.workflow_mode_combo.blockSignals(False)
        except Exception as e:
            print(f"Error syncing workflow mode: {e}")

    def _on_workflow_mode_changed(self, index: int):
        """处理翻译流程模式改变"""
        # 根据下拉框选择更新配置
        # 0: 正常翻译流程
        # 1: 导出翻译
        # 2: 导出原文
        # 3: 导入翻译并渲染
        # 4: 仅上色
        # 5: 仅超分

        config = self.config_service.get_config()

        # 重置所有选项
        config.cli.load_text = False
        config.cli.template = False
        config.cli.generate_and_export = False
        config.cli.colorize_only = False
        config.cli.upscale_only = False

        if index == 1:  # 导出翻译
            config.cli.generate_and_export = True
        elif index == 2:  # 导出原文
            config.cli.template = True
        elif index == 3:  # 导入翻译并渲染
            config.cli.load_text = True
        elif index == 4:  # 仅上色
            config.cli.colorize_only = True
        elif index == 5:  # 仅超分
            config.cli.upscale_only = True

        # ✅ 保存配置到内存和文件
        self.config_service.set_config(config)
        self.config_service.save_config_file()

        # 立即更新按钮文字
        self.update_start_button_text()

    def update_start_button_text(self):
        """Updates the start button text based on the current configuration."""
        if self.controller.state_manager.is_translating():
            return  # Don't change text while a task is running

        try:
            config = self.config_service.get_config()
            if config.cli.upscale_only:
                self.start_button.setText("开始超分")
            elif config.cli.colorize_only:
                self.start_button.setText("开始上色")
            elif config.cli.load_text:
                self.start_button.setText("导入翻译并渲染")
            elif config.cli.template:
                self.start_button.setText("仅生成原文模板")
            elif config.cli.generate_and_export:
                self.start_button.setText("导出翻译")
            else:
                self.start_button.setText("开始翻译")
        except Exception as e:
            # Fallback in case config is not ready
            self.start_button.setText("开始翻译")
            print(f"Could not update button text: {e}")


