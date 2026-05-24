from app_logic import MainAppLogic
from main_view import MainView
from PyQt6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMainWindow, QStackedWidget
from services import (
    ServiceManager,
    get_config_service,
    get_i18n_manager,
    get_logger,
    get_state_manager,
)
from theme_registry import THEME_OPTIONS
from utils.app_version import format_app_title, get_app_version
from widgets.themed_message_box import show_error_dialog


class MainWindow(QMainWindow):
    """
    应用主窗口，继承自 QMainWindow。
    负责承载所有UI组件、菜单栏、工具栏等。
    """
    def __init__(self):
        super().__init__()

        self.logger = get_logger(__name__)
        self.i18n = get_i18n_manager()
        self.app_version = get_app_version()
        self._qt_translator = None
        self._apply_qt_translator(self.i18n.get_current_locale() if self.i18n else "en_US")
        
        self._update_window_title()
        self.resize(1300, 800) # 设置默认窗口大小（增加20像素）
        self.setMinimumSize(800, 600) # 设置最小窗口大小
        # 不设置最大大小，允许无限制调整
        
        # 窗口居中显示
        from PyQt6.QtGui import QScreen
        screen = QScreen.availableGeometry(self.screen())
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)
        
        # 窗口图标已在 main.py 中设置，这里不需要重复设置
        
        # 当前应用的主题（用于逻辑判断）
        self.current_applied_theme = "light"

        self._setup_logic_and_models()
        self._setup_ui()
        self._load_stylesheet()  # 加载样式表
        self._connect_signals()

        self.app_logic.initialize()

        # 启动内嵌 HTTP API 服务器（用于 Claude 等外部工具获取/修改 region data）
        self._start_api_server()

        # 检查是否需要启动系统主题监听
        config = self.config_service.get_config()
        if config.app.theme == "system":
            self.last_system_theme = self._detect_windows_theme()
            self.theme_check_timer = QTimer(self)
            self.theme_check_timer.timeout.connect(self._check_system_theme_change)
            self.theme_check_timer.start(5000)  # 每5秒检查一次
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _update_window_title(self):
        """更新窗口标题，保持标题与版本号同步。"""
        self.setWindowTitle(format_app_title(self._t("Manga Translator"), self.app_version))

    def _setup_logic_and_models(self):
        """实例化所有逻辑和数据模型"""
        self.config_service = get_config_service()
        self.state_manager = get_state_manager()
        config = self.config_service.get_config()

        initial_theme = config.app.theme
        if initial_theme == "system":
            detected_theme = self._detect_windows_theme()
            if detected_theme == "dark":
                initial_theme = "dark"
            else:
                initial_theme = config.app.theme_user_preference

        from main_view_parts.theme import set_current_theme
        set_current_theme(initial_theme)
        self.current_applied_theme = initial_theme

        # --- Logic Controllers ---
        self.app_logic = MainAppLogic()
        ServiceManager.register_service('app_logic', self.app_logic)
        self.editor_model = None
        self.editor_controller = None
        self.editor_logic = None
        self.editor_view = None
        self._api_server = None

    def _setup_ui(self):
        """初始化UI组件"""
        # 不显示顶部菜单栏，菜单功能统一整合到设置区域
        self._create_ui_actions()
        self.menuBar().hide()

        # --- 中心布局 (QStackedWidget) ---
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.main_view = MainView(self.app_logic, self)

        # 设置 app_logic 对 main_view 的引用，用于更新进度条
        self.app_logic.main_view = self.main_view

        self.stacked_widget.addWidget(self.main_view)

        self.stacked_widget.setCurrentWidget(self.main_view)

    def _ensure_editor_initialized(self):
        if self.editor_view is not None:
            return

        from editor.editor_controller import EditorController
        from editor.editor_logic import EditorLogic
        from editor.editor_model import EditorModel
        from editor_view import EditorView

        self.editor_model = EditorModel()
        self.editor_controller = EditorController(self.editor_model)
        self.editor_logic = EditorLogic(self.editor_controller)
        self.editor_view = EditorView(
            self.app_logic,
            self.editor_model,
            self.editor_controller,
            self.editor_logic,
            self,
        )
        self.stacked_widget.addWidget(self.editor_view)

        self.app_logic.config_loaded.connect(self.editor_view.property_panel.repopulate_options)
        self.editor_view.back_to_main_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_view))

        self.editor_view._apply_editor_style(self.current_applied_theme)
        self.editor_view.property_panel.repopulate_options()

        # 将 controller 注入 API 服务器
        if self._api_server is not None:
            self._api_server.set_controller(self.editor_controller)

    def _start_api_server(self):
        """启动内嵌 HTTP API 服务器（后台 daemon 线程）。"""
        try:
            from editor_api_server import EditorApiServer, DEFAULT_PORT

            self._api_server = EditorApiServer(port=DEFAULT_PORT)
            self._api_server.start()
        except Exception:
            self.logger.exception("Failed to start EditorApiServer")

    def _create_ui_actions(self):
        """创建内部动作对象（无顶部菜单栏）"""
        self.add_files_action = QAction(self._t("&Add Files..."), self)
        self.undo_action = QAction(self._t("&Undo"), self)
        self.redo_action = QAction(self._t("&Redo"), self)
        self.main_view_action = QAction(self._t("Main View"), self)
        self.editor_view_action = QAction(self._t("Editor View"), self)
        self.theme_actions = {}
        for theme_key, theme_label in THEME_OPTIONS:
            action = QAction(self._t(theme_label), self)
            self.theme_actions[theme_key] = action
            setattr(self, f"{theme_key}_theme_action", action)

    def _load_stylesheet(self):
        """加载样式表，根据配置选择主题"""
        from services import get_config_service
        config_service = get_config_service()
        config = config_service.get_config()
        
        # 获取主题设置，Pydantic会自动使用默认值'light'
        theme = config.app.theme
        self._apply_theme(theme)
    
    def _apply_theme(self, theme: str):
        """应用指定的主题"""
        
        # 处理系统主题逻辑：如果是 'system'，则解析为实际主题
        if theme == 'system':
            sys_theme = self._detect_windows_theme()
            if sys_theme == 'dark':
                self._apply_theme('dark')
            else:
                config = self.config_service.get_config()
                # 使用用户偏好（所有非 dark 主题）
                self._apply_theme(config.app.theme_user_preference)
            return

        # 记录当前实际应用的主题
        self.current_applied_theme = theme
        
        from main_view_parts.theme import apply_application_theme, build_theme_palette

        apply_application_theme(theme, QApplication.instance())
        palette = build_theme_palette(theme)
        self.setPalette(palette)
        self.setStyleSheet("")

        # 通知各视图应用对应主题的内联样式
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view._apply_reference_ui_style(theme)
            self.main_view.update()
        if hasattr(self, 'editor_view') and self.editor_view:
            self.editor_view._apply_editor_style(theme)
            self.editor_view.update()
        if hasattr(self, "stacked_widget") and self.stacked_widget:
            self.stacked_widget.update()
        self.update()
        self._apply_native_title_bar_theme(theme)
        QTimer.singleShot(0, lambda active_theme=theme: self._apply_native_title_bar_theme(active_theme))

    def _apply_native_title_bar_theme(self, theme: str):
        """同步 Windows 原生标题栏颜色，避免深色内容区配浅色系统标题栏。"""
        from main_view_parts.theme import apply_native_title_bar_theme

        apply_native_title_bar_theme(self, theme, logger=self.logger)
    
    def _detect_windows_theme(self) -> str:
        """检测Windows系统主题（深色/浅色）
        返回: 'dark' 或 'light'
        """
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value == 1 else "dark"
        except Exception:
            # 默认返回浅色（或记录日志）
            # self.logger.warning(f"无法检测系统主题: {e}")
            return "light"

    def _check_system_theme_change(self):
        """检查系统主题是否变化"""
        config = self.config_service.get_config()
        if config.app.theme != "system":
            # 如果用户切换到其他主题，停止监听
            if hasattr(self, 'theme_check_timer'):
                self.theme_check_timer.stop()
            return
        
        current_system_theme = self._detect_windows_theme()
        if current_system_theme != self.last_system_theme:
            self.logger.info(f"系统主题变化: {self.last_system_theme} -> {current_system_theme}")
            
            if current_system_theme == "dark":
                # 系统切换到深色
                if self.current_applied_theme != "dark":
                    # 保存用户偏好（浅色或灰色）
                    config.app.theme_user_preference = self.current_applied_theme
                    self.config_service.save_config_file()
                    self.logger.info(f"保存用户偏好: {self.current_applied_theme}")
                # 切换到深色主题
                self._apply_theme("dark")
            else:
                # 系统切换到浅色
                # 恢复用户偏好
                user_pref = config.app.theme_user_preference
                self._apply_theme(user_pref)
                self.logger.info(f"恢复用户偏好: {user_pref}")
            
            self.last_system_theme = current_system_theme

    def _change_theme(self, theme: str):
        """切换主题并保存到配置"""
        from services import get_config_service
        config_service = get_config_service()
        config = config_service.get_config()
        
        if theme == "system":
            # 应用主题（逻辑主题）
            self._apply_theme("system")
            
            # 启动监听
            self.last_system_theme = self._detect_windows_theme()
            if not hasattr(self, 'theme_check_timer'):
                self.theme_check_timer = QTimer(self)
                self.theme_check_timer.timeout.connect(self._check_system_theme_change)
            
            if not self.theme_check_timer.isActive():
                self.theme_check_timer.start(5000)
        else:
            # 停止监听
            if hasattr(self, 'theme_check_timer'):
                self.theme_check_timer.stop()
                
            # 应用主题
            self._apply_theme(theme)
            
            # 保存所有非 dark 主题，供“跟随系统”在浅色系统下恢复。
            if theme != "dark":
                config.app.theme_user_preference = theme
        
        # 保存到配置
        config.app.theme = theme
        config_service.set_config(config)
        
        # 保存到文件
        config_service.save_config_file()

    def _connect_signals(self):
        # --- MainAppLogic Connections ---
        self.app_logic.config_loaded.connect(self.main_view.set_parameters)
        self.app_logic.files_added.connect(self.main_view.file_list.add_files)
        self.app_logic.files_cleared.connect(self.main_view.file_list.clear)
        self.app_logic.file_removed.connect(self.main_view.file_list.remove_file)
        self.app_logic.file_removed.connect(self._on_file_removed_update_editor)
        self.app_logic.files_cleared.connect(self._on_files_cleared_update_editor)
        self.app_logic.output_path_updated.connect(self.main_view.update_output_path_display)
        self.app_logic.task_completed.connect(self.on_task_completed, type=Qt.ConnectionType.QueuedConnection)
        self.app_logic.error_dialog_requested.connect(self._show_error_dialog, type=Qt.ConnectionType.QueuedConnection)

        # --- View to Logic Connections ---
        self.main_view.setting_changed.connect(self.app_logic.update_single_config)
        self.main_view.editor_view_requested.connect(self.switch_to_editor_view)
        self.main_view.theme_change_requested.connect(self._change_theme)
        self.main_view.language_change_requested.connect(self._change_language)

        # --- View to Coordinator Connections ---
        self.main_view.file_list.file_selected.connect(self.on_file_selected_from_main_list)
        self.main_view.file_list.files_dropped.connect(self.app_logic.add_files)  # 拖放文件支持
        # self.main_view.enter_editor_button.clicked.connect(self.enter_editor_mode) # Example for a dedicated button

        # --- View Switching Connections ---
        self.main_view_action.triggered.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_view))
        self.editor_view_action.triggered.connect(self.switch_to_editor_view)

        # --- 撤销/重做延迟转发到编辑器controller ---
        self.undo_action.triggered.connect(self._handle_undo)
        self.redo_action.triggered.connect(self._handle_redo)
        
        # --- 主题切换连接 ---
        for theme_key, action in getattr(self, "theme_actions", {}).items():
            action.triggered.connect(lambda checked=False, selected_theme=theme_key: self._change_theme(selected_theme))

    @pyqtSlot(str)
    def on_file_selected_from_main_list(self, file_path: str):
        """
        Coordinator slot. Handles when a file is double-clicked in the main view.
        It tells the editor logic to load the file, then switches the view.
        """
        self.logger.info(f"File double-clicked from main list: {file_path}. Switching to editor.")
        self.enter_editor_mode(file_to_load=file_path)
    
    def _on_file_removed_update_editor(self, file_path: str):
        """当主页文件被移除时，更新编辑器（如果编辑器正在显示该文件）"""
        if not self.editor_view or not self.editor_controller:
            return
        if self.stacked_widget.currentWidget() == self.editor_view:
            # 检查当前加载的图片是否被移除
            current_image = self.editor_controller.model.get_source_image_path()
            
            if current_image:
                import os
                norm_current = os.path.normpath(current_image)
                norm_removed = os.path.normpath(file_path)
                
                # 如果移除的是当前图片
                if norm_current == norm_removed:
                    self.editor_controller._clear_editor_state()
                # 如果移除的是文件夹，检查当前图片是否在该文件夹内
                elif os.path.isdir(file_path):
                    try:
                        # 检查当前图片是否在被移除的文件夹内
                        if os.path.commonpath([norm_current, norm_removed]) == norm_removed:
                            self.editor_controller._clear_editor_state()
                    except ValueError:
                        # 不同驱动器，跳过
                        pass
            
            # 注意：编辑器有自己独立的文件列表，不需要同步主页的删除操作
            # 只有当主页文件全部清空时，才清空编辑器列表
    
    def _on_files_cleared_update_editor(self):
        """当文件列表被清空时，清空编辑器"""
        if not self.editor_view or not self.editor_logic:
            return
        if self.stacked_widget.currentWidget() == self.editor_view:
            # 如果当前在编辑器视图，清空编辑器
            self.logger.info("Files cleared. Clearing editor.")
            # 清空文件列表（内部会自动清空画布和状态）
            self.editor_logic.clear_list()

    def _change_language(self, locale_code: str):
        """切换语言"""
        if self.i18n and self.i18n.set_locale(locale_code):
            self._apply_qt_translator(locale_code)
            # 保存语言设置到配置
            config = self.config_service.get_config()
            config.app.ui_language = locale_code
            self.config_service.set_config(config)
            self.config_service.save_config_file()
            
            # 刷新UI文本
            self._refresh_ui_texts()
            self.logger.info(f"语言已切换到: {locale_code}")

    def _apply_qt_translator(self, locale_code: str):
        """加载 Qt 内建控件翻译（如 QColorDialog），使其跟随应用语言。"""
        app = QApplication.instance()
        if app is None:
            return

        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator = None

        translator = QTranslator(self)
        qt_translations_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)

        # locale_code 形如 zh_CN / en_US，依次尝试精确与语言级别匹配
        language = QLocale(locale_code).name().split('_', 1)[0]
        candidates = (
            f"qtbase_{locale_code}",
            f"qtbase_{language}",
            f"qt_{locale_code}",
            f"qt_{language}",
        )

        loaded = any(translator.load(name, qt_translations_dir) for name in candidates)
        if loaded:
            app.installTranslator(translator)
            self._qt_translator = translator
    
    def _refresh_ui_texts(self):
        """刷新UI文本"""
        self._update_window_title()
        self._refresh_action_texts()
        
        # 刷新主视图的所有文本
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.refresh_ui_texts()
        
        # 刷新编辑器视图的所有文本（如果存在）
        if hasattr(self, 'editor_view') and self.editor_view:
            if hasattr(self.editor_view, 'refresh_ui_texts'):
                self.editor_view.refresh_ui_texts()

    def _refresh_action_texts(self):
        """刷新内部动作文本（菜单栏隐藏时仍保留动作对象）"""
        if hasattr(self, 'add_files_action'):
            self.add_files_action.setText(self._t("&Add Files..."))
        if hasattr(self, 'undo_action'):
            self.undo_action.setText(self._t("&Undo"))
        if hasattr(self, 'redo_action'):
            self.redo_action.setText(self._t("&Redo"))
        if hasattr(self, 'main_view_action'):
            self.main_view_action.setText(self._t("Main View"))
        if hasattr(self, 'editor_view_action'):
            self.editor_view_action.setText(self._t("Editor View"))
        for theme_key, theme_label in THEME_OPTIONS:
            action = getattr(self, "theme_actions", {}).get(theme_key)
            if action is not None:
                action.setText(self._t(theme_label))

    def _handle_undo(self):
        if self.editor_controller:
            self.editor_controller.undo()

    def _handle_redo(self):
        if self.editor_controller:
            self.editor_controller.redo()
    
    @pyqtSlot(list)
    def on_task_completed(self, saved_files: list):
        """
        Handles the completion of a translation task.
        Asks the user if they want to open the results in the editor.
        """
        try:
            if not saved_files:
                return

            if not self._should_prompt_open_results_in_editor():
                return

            from PyQt6.QtWidgets import QMessageBox

            reply = show_error_dialog(
                self,
                self._t('Task Completed'),
                "",
                self._t("Translation completed, {count} files saved.\n\nOpen results in editor?", count=len(saved_files)),
                icon=QMessageBox.Icon.Question,
                buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                default_button=QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.enter_editor_mode(files_to_load=saved_files)
        except Exception as e:
            self.logger.error(f"on_task_completed 发生异常: {e}", exc_info=True)
            import traceback
            traceback.print_exc()

    def _should_prompt_open_results_in_editor(self) -> bool:
        """Only prompt for workflows that produce editor-meaningful results."""
        try:
            config = self.config_service.get_config()
            cli = getattr(config, 'cli', None)
            if cli is None:
                return True

            if getattr(cli, 'replace_translation', False):
                return True

            if getattr(cli, 'load_text', False):
                return True

            incompatible_modes = (
                getattr(cli, 'translate_json_only', False),
                getattr(cli, 'template', False),
                getattr(cli, 'generate_and_export', False),
                getattr(cli, 'colorize_only', False),
                getattr(cli, 'upscale_only', False),
                getattr(cli, 'inpaint_only', False),
            )
            return not any(incompatible_modes)
        except Exception as e:
            self.logger.warning(f"判断是否显示编辑器提示框失败，回退为显示提示框: {e}")
            return True

    @pyqtSlot(str)
    def _show_error_dialog(self, error_message: str):
        """弹出翻译错误提示框"""
        try:
            show_error_dialog(
                self,
                self._t("Translation Error"),
                "",
                error_message,
            )
        except Exception as e:
            self.logger.error(f"_show_error_dialog error: {e}", exc_info=True)

    def switch_to_editor_view(self):
        """
        Simply switches to the editor view without reloading file lists.
        Used when user manually switches views.
        """
        self._ensure_editor_initialized()
        self.stacked_widget.setCurrentWidget(self.editor_view)

    def enter_editor_mode(self, file_to_load: str = None, files_to_load: list = None):
        """
        Switches to the editor view and loads the necessary files.
        file_to_load: 单个文件路径（双击文件时使用）
        files_to_load: 保存结果列表（从翻译完成进入时使用，用于定位要打开的原图）
        """
        try:
            self._ensure_editor_initialized()

            # 获取完整的文件夹树结构
            tree_structure = self.app_logic.get_folder_tree_structure()
            expanded_files = tree_structure['files']
            folder_tree = tree_structure['tree']

            # 判断是否从翻译完成进入（有 files_to_load 参数）
            if files_to_load and len(files_to_load) > 0:
                self.editor_logic.load_file_lists(
                    source_files=expanded_files,
                    folder_tree=folder_tree,
                )
                self.editor_logic.load_image_into_editor(files_to_load[0])
            else:
                # 手动打开编辑器：显示源文件列表
                self.editor_logic.load_file_lists(
                    source_files=expanded_files,
                    folder_tree=folder_tree
                )
                # 如果指定了要加载的文件
                if file_to_load:
                    self.editor_logic.load_image_into_editor(file_to_load)
                elif expanded_files:
                    self.editor_logic.load_image_into_editor(expanded_files[0])

            self.stacked_widget.setCurrentWidget(self.editor_view)
        except Exception as e:
            self.logger.error(f"enter_editor_mode 发生异常: {e}", exc_info=True)
            import traceback
            traceback.print_exc()

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        self.app_logic.shutdown()
        event.accept()
