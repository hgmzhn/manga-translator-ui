
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget

from app_logic import MainAppLogic
from editor.editor_controller import EditorController
from editor.editor_logic import EditorLogic
from editor.editor_model import EditorModel
from editor_view import EditorView
from main_view import MainView
from services import ServiceManager, get_config_service, get_logger, get_state_manager, get_i18n_manager


class MainWindow(QMainWindow):
    """
    应用主窗口，继承自 QMainWindow。
    负责承载所有UI组件、菜单栏、工具栏等。
    """
    def __init__(self):
        super().__init__()

        self.logger = get_logger(__name__)
        self.i18n = get_i18n_manager()
        
        self.setWindowTitle(self._t("Manga Translator"))
        self.setMinimumSize(800, 600) # 设置最小窗口大小
        
        # 窗口图标已在 main.py 中设置，这里不需要重复设置

        self._setup_logic_and_models()
        
        # 恢复窗口位置和大小（需要在 config_service 初始化之后）
        self._restore_window_state()
        
        self._setup_ui()
        self._load_stylesheet()  # 加载样式表
        self._connect_signals()

        self.app_logic.initialize()
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _setup_logic_and_models(self):
        """实例化所有逻辑和数据模型"""
        self.config_service = get_config_service()
        self.state_manager = get_state_manager()

        # --- Logic Controllers ---
        self.app_logic = MainAppLogic()
        ServiceManager.register_service('app_logic', self.app_logic)
        self.editor_model = EditorModel()
        self.editor_controller = EditorController(self.editor_model)
        self.editor_logic = EditorLogic(self.editor_controller)

    def _setup_ui(self):
        """初始化UI组件"""
        # --- 菜单栏 ---
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu(self._t("&File"))
        self.add_files_action = QAction(self._t("&Add Files..."), self)
        file_menu.addAction(self.add_files_action)

        edit_menu = menu_bar.addMenu(self._t("&Edit"))
        self.undo_action = QAction(self._t("&Undo"), self)
        self.redo_action = QAction(self._t("&Redo"), self)
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        view_menu = menu_bar.addMenu(self._t("&View"))
        self.main_view_action = QAction(self._t("Main View"), self)
        self.editor_view_action = QAction(self._t("Editor View"), self)
        view_menu.addAction(self.main_view_action)
        view_menu.addAction(self.editor_view_action)
        
        # 主题菜单（顶级菜单）
        theme_menu = menu_bar.addMenu(self._t("&Theme"))
        self.light_theme_action = QAction(self._t("Light"), self)
        self.dark_theme_action = QAction(self._t("Dark"), self)
        self.gray_theme_action = QAction(self._t("Gray"), self)
        self.native_theme_action = QAction(self._t("Win (Native)"), self)
        theme_menu.addAction(self.light_theme_action)
        theme_menu.addAction(self.dark_theme_action)
        theme_menu.addAction(self.gray_theme_action)
        theme_menu.addSeparator()
        theme_menu.addAction(self.native_theme_action)
        
        # 语言菜单（顶级菜单）
        language_menu = menu_bar.addMenu(self._t("&Language"))
        if self.i18n:
            available_locales = self.i18n.get_available_locales()
            for locale_code, locale_info in available_locales.items():
                action = QAction(locale_info.name, self)
                action.triggered.connect(lambda checked, code=locale_code: self._change_language(code))
                language_menu.addAction(action)

        # --- 中心布局 (QStackedWidget) ---
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.main_view = MainView(self.app_logic, self)
        self.editor_view = EditorView(self.app_logic, self.editor_model, self.editor_controller, self.editor_logic, self)

        # 设置 app_logic 对 main_view 的引用，用于更新进度条
        self.app_logic.main_view = self.main_view

        self.stacked_widget.addWidget(self.main_view)
        self.stacked_widget.addWidget(self.editor_view)

        self.stacked_widget.setCurrentWidget(self.main_view)

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
        import os
        import sys
        
        # Windows 原生主题：清除所有样式表
        if theme == 'native':
            self.setStyleSheet('')
            return
        
        # 主题文件映射
        theme_files = {
            'light': 'modern.qss',
            'dark': 'dark.qss',
            'gray': 'gray.qss'
        }
        
        stylesheet_file = theme_files.get(theme, 'modern.qss')
        
        # 适配打包环境和开发环境
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            # 打包环境：样式文件在 _internal/desktop_qt_ui/styles/
            stylesheet_path = os.path.join(sys._MEIPASS, 'desktop_qt_ui', 'styles', stylesheet_file)
            # 图标基础路径
            icon_base_path = os.path.join(sys._MEIPASS, 'desktop_qt_ui', 'styles', 'icons')
        else:
            # 开发环境
            stylesheet_path = os.path.join(os.path.dirname(__file__), 'styles', stylesheet_file)
            # 图标基础路径
            icon_base_path = os.path.join(os.path.dirname(__file__), 'styles', 'icons')
        
        try:
            with open(stylesheet_path, 'r', encoding='utf-8') as f:
                stylesheet = f.read()
                
                # 替换样式表中的图标路径为绝对路径
                # 将 desktop_qt_ui/styles/icons/ 替换为实际的绝对路径
                stylesheet = stylesheet.replace(
                    'desktop_qt_ui/styles/icons/',
                    icon_base_path.replace('\\', '/') + '/'
                )
                
                self.setStyleSheet(stylesheet)
        except FileNotFoundError:
            self.logger.warning(f"Stylesheet not found: {stylesheet_path}")
        except Exception as e:
            self.logger.error(f"Error loading stylesheet: {e}")
    
    def _change_theme(self, theme: str):
        """切换主题并保存到配置"""
        from services import get_config_service
        
        # 应用主题
        self._apply_theme(theme)
        
        # 保存到配置（使用统一的配置管理服务）
        config_service = get_config_service()
        config = config_service.get_config()
        config.app.theme = theme
        config_service.set_config(config)
        
        # 保存到文件
        config_service.save_config_file()

    def _connect_signals(self):
        # --- MainAppLogic Connections ---
        self.app_logic.config_loaded.connect(self.main_view.set_parameters)
        self.app_logic.config_loaded.connect(self.editor_view.property_panel.repopulate_options)
        self.app_logic.files_added.connect(self.main_view.file_list.add_files)
        self.app_logic.files_cleared.connect(self.main_view.file_list.clear)
        self.app_logic.file_removed.connect(self.main_view.file_list.remove_file)
        self.app_logic.file_removed.connect(self._on_file_removed_update_editor)
        self.app_logic.files_cleared.connect(self._on_files_cleared_update_editor)
        self.app_logic.output_path_updated.connect(self.main_view.update_output_path_display)
        self.app_logic.task_completed.connect(self.on_task_completed, type=Qt.ConnectionType.QueuedConnection)
        self.app_logic.log_message.connect(self.main_view.append_log)

        # --- View to Logic Connections ---
        self.main_view.setting_changed.connect(self.app_logic.update_single_config)

        # --- Live-reload connection ---
        self.app_logic.render_setting_changed.connect(self.editor_logic.on_global_render_setting_changed)

        # --- View to Coordinator Connections ---
        self.main_view.file_list.file_selected.connect(self.on_file_selected_from_main_list)
        self.main_view.file_list.files_dropped.connect(self.app_logic.add_files)  # 拖放文件支持
        # self.main_view.enter_editor_button.clicked.connect(self.enter_editor_mode) # Example for a dedicated button

        # --- Editor related connections ---
        self.editor_view.back_to_main_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_view))

        # --- View Switching Connections ---
        self.main_view_action.triggered.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_view))
        self.editor_view_action.triggered.connect(self.switch_to_editor_view)

        # --- 撤销/重做连接到编辑器controller ---
        self.undo_action.triggered.connect(self.editor_controller.undo)
        self.redo_action.triggered.connect(self.editor_controller.redo)
        
        # --- 主题切换连接 ---
        self.light_theme_action.triggered.connect(lambda: self._change_theme("light"))
        self.dark_theme_action.triggered.connect(lambda: self._change_theme("dark"))
        self.gray_theme_action.triggered.connect(lambda: self._change_theme("gray"))
        self.native_theme_action.triggered.connect(lambda: self._change_theme("native"))

    @pyqtSlot(str)
    def on_file_selected_from_main_list(self, file_path: str):
        """
        Coordinator slot. Handles when a file is double-clicked in the main view.
        It tells the editor logic to load the file, then switches the view.
        """
        self.logger.info(f"File double-clicked from main list: {file_path}. Switching to editor.")
        self.enter_editor_mode(file_to_load=file_path)
    
    def _on_file_removed_update_editor(self, file_path: str):
        """当文件被移除时，更新编辑器"""
        if self.stacked_widget.currentWidget() == self.editor_view:
            # 检查当前加载的图片是否被移除
            current_image = self.editor_controller.model.get_source_image_path()
            should_clear_canvas = False
            
            if current_image:
                import os
                norm_current = os.path.normpath(current_image)
                norm_removed = os.path.normpath(file_path)
                
                # 如果移除的是当前图片
                if norm_current == norm_removed:
                    should_clear_canvas = True
                # 如果移除的是文件夹，检查当前图片是否在该文件夹内
                elif os.path.isdir(file_path):
                    try:
                        # 检查当前图片是否在被移除的文件夹内
                        if os.path.commonpath([norm_current, norm_removed]) == norm_removed:
                            should_clear_canvas = True
                    except ValueError:
                        # 不同驱动器，跳过
                        pass
            
            # 如果需要清空画布
            if should_clear_canvas:
                self.editor_controller.model.set_image(None)
                self.editor_controller._clear_editor_state()
            
            # 检查是否还有文件
            if not self.app_logic.source_files:
                # 没有文件了，清空编辑器
                self.editor_logic.clear_list()
                # 如果画布还没清空，清空它
                if not should_clear_canvas:
                    self.editor_controller.model.set_image(None)
                    self.editor_controller._clear_editor_state()
            # 如果还有文件，不需要重建列表，因为视图已经在 _on_file_remove_requested 中移除了
    
    def _on_files_cleared_update_editor(self):
        """当文件列表被清空时，清空编辑器"""
        if self.stacked_widget.currentWidget() == self.editor_view:
            # 如果当前在编辑器视图，清空编辑器
            self.logger.info("Files cleared. Clearing editor.")
            # 清空文件列表
            self.editor_logic.clear_list()
            # 清空画布
            self.editor_controller.model.set_image(None)
            self.editor_controller._clear_editor_state()

    def _change_language(self, locale_code: str):
        """切换语言"""
        if self.i18n and self.i18n.set_locale(locale_code):
            # 保存语言设置到配置
            config = self.config_service.get_config()
            config.app.ui_language = locale_code
            self.config_service.set_config(config)
            self.config_service.save_config_file()
            
            # 刷新UI文本
            self._refresh_ui_texts()
            self.logger.info(f"语言已切换到: {locale_code}")
    
    def _refresh_ui_texts(self):
        """刷新UI文本"""
        # 更新窗口标题
        self.setWindowTitle(self._t("Manga Translator"))
        
        # 更新菜单文本
        menu_bar = self.menuBar()
        menus = menu_bar.findChildren(QAction)
        
        # 由于菜单已经创建，我们需要重新设置文本
        # 这里简单地重新创建菜单栏
        menu_bar.clear()
        self._setup_ui_menus()
        
        # 刷新主视图的所有文本
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.refresh_ui_texts()
        
        # 刷新编辑器视图的所有文本（如果存在）
        if hasattr(self, 'editor_view') and self.editor_view:
            if hasattr(self.editor_view, 'refresh_ui_texts'):
                self.editor_view.refresh_ui_texts()
    
    def _setup_ui_menus(self):
        """设置UI菜单（用于语言切换后刷新）"""
        menu_bar = self.menuBar()
        
        # 文件菜单
        file_menu = menu_bar.addMenu(self._t("&File"))
        self.add_files_action = QAction(self._t("&Add Files..."), self)
        file_menu.addAction(self.add_files_action)
        
        # 编辑菜单
        edit_menu = menu_bar.addMenu(self._t("&Edit"))
        self.undo_action = QAction(self._t("&Undo"), self)
        self.redo_action = QAction(self._t("&Redo"), self)
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        
        # 视图菜单
        view_menu = menu_bar.addMenu(self._t("&View"))
        self.main_view_action = QAction(self._t("Main View"), self)
        self.editor_view_action = QAction(self._t("Editor View"), self)
        view_menu.addAction(self.main_view_action)
        view_menu.addAction(self.editor_view_action)
        
        # 主题菜单
        theme_menu = menu_bar.addMenu(self._t("&Theme"))
        self.light_theme_action = QAction(self._t("Light"), self)
        self.dark_theme_action = QAction(self._t("Dark"), self)
        self.gray_theme_action = QAction(self._t("Gray"), self)
        self.native_theme_action = QAction(self._t("Win (Native)"), self)
        theme_menu.addAction(self.light_theme_action)
        theme_menu.addAction(self.dark_theme_action)
        theme_menu.addAction(self.gray_theme_action)
        theme_menu.addSeparator()
        theme_menu.addAction(self.native_theme_action)
        
        # 语言菜单
        language_menu = menu_bar.addMenu(self._t("&Language"))
        if self.i18n:
            available_locales = self.i18n.get_available_locales()
            for locale_code, locale_info in available_locales.items():
                action = QAction(locale_info.name, self)
                action.triggered.connect(lambda checked, code=locale_code: self._change_language(code))
                language_menu.addAction(action)
        
        # 重新连接信号
        self.main_view_action.triggered.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_view))
        self.editor_view_action.triggered.connect(self.switch_to_editor_view)
        self.undo_action.triggered.connect(self.editor_controller.undo)
        self.redo_action.triggered.connect(self.editor_controller.redo)
        self.light_theme_action.triggered.connect(lambda: self._change_theme("light"))
        self.dark_theme_action.triggered.connect(lambda: self._change_theme("dark"))
        self.gray_theme_action.triggered.connect(lambda: self._change_theme("gray"))
        self.native_theme_action.triggered.connect(lambda: self._change_theme("native"))
    
    @pyqtSlot(list)
    def on_task_completed(self, saved_files: list):
        """
        Handles the completion of a translation task.
        Asks the user if they want to open the results in the editor.
        """
        if not saved_files:
            return

        reply = QMessageBox.question(
            self, 
            self._t('Task Completed'), 
            self._t("Translation completed, {count} files saved.\n\nOpen results in editor?", count=len(saved_files)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.logger.info("User chose to open results in editor.")
            self.enter_editor_mode(files_to_load=saved_files)

    def switch_to_editor_view(self):
        """
        Simply switches to the editor view without reloading file lists.
        Used when user manually switches views.
        """
        self.stacked_widget.setCurrentWidget(self.editor_view)

    def enter_editor_mode(self, file_to_load: str = None, files_to_load: list = None):
        """
        Switches to the editor view and loads the necessary files.
        file_to_load: 单个文件路径（双击文件时使用）
        files_to_load: 翻译后的文件列表
        """
        import os

        # 获取完整的文件夹树结构
        tree_structure = self.app_logic.get_folder_tree_structure()
        expanded_files = tree_structure['files']
        folder_tree = tree_structure['tree']
        
        self.logger.info(f"Entering editor mode with {len(expanded_files)} files")

        # 传递翻译后的图片列表给编辑器
        # 从file_to_folder_map获取翻译后的图片路径
        translated_files = []
        translated_folder_map = {}  # 翻译后文件的文件夹映射
        
        for source_file in expanded_files:
            # 根据源文件路径构造翻译后的图片路径
            source_folder = self.app_logic.file_to_folder_map.get(source_file)
            if source_folder:
                # 文件来自文件夹
                folder_name = os.path.basename(source_folder)
                output_folder = self.app_logic.config_service.get_config().app.last_output_path
                final_output_folder = os.path.join(output_folder, folder_name)
                translated_file = os.path.join(final_output_folder, os.path.basename(source_file))
                
                if os.path.exists(translated_file):
                    translated_files.append(translated_file)
                    # 映射到翻译后文件的直接父文件夹（输出目录中的文件夹）
                    translated_folder_map[translated_file] = final_output_folder
            else:
                # 单独添加的文件
                output_folder = self.app_logic.config_service.get_config().app.last_output_path
                translated_file = os.path.join(output_folder, os.path.basename(source_file))

                if os.path.exists(translated_file):
                    translated_files.append(translated_file)
                    translated_folder_map[translated_file] = output_folder

        # 传递完整的树结构给编辑器
        self.editor_logic.load_file_lists(
            source_files=expanded_files, 
            translated_files=translated_files,
            folder_tree=folder_tree
        )

        # 如果指定了要加载的文件，加载第一个翻译后的文件
        if file_to_load:
            self.editor_logic.load_image_into_editor(file_to_load)
        elif files_to_load and len(files_to_load) > 0:
            # files_to_load是翻译后的文件列表，加载第一个
            self.editor_logic.load_image_into_editor(files_to_load[0])
        elif translated_files:
            # 如果没有指定，加载第一个翻译后的文件
            self.editor_logic.load_image_into_editor(translated_files[0])

        self.stacked_widget.setCurrentWidget(self.editor_view)

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        # 保存窗口位置和大小
        self._save_window_state()
        self.app_logic.shutdown()
        event.accept()
    
    def _restore_window_state(self):
        """从配置恢复窗口位置和大小"""
        from PyQt6.QtGui import QScreen
        
        config = self.config_service.get_config()
        app_config = config.app
        
        # 获取当前屏幕可用区域
        screen = QScreen.availableGeometry(self.screen())
        
        # 检查是否有保存的窗口状态
        if app_config.window_width and app_config.window_height:
            width = app_config.window_width
            height = app_config.window_height
            
            # 确保窗口大小不超过屏幕
            width = min(width, screen.width())
            height = min(height, screen.height())
            self.resize(width, height)
        else:
            # 默认大小
            self.resize(1300, 800)
        
        # 检查是否有保存的窗口位置
        if app_config.window_x is not None and app_config.window_y is not None:
            x = app_config.window_x
            y = app_config.window_y
            
            # 确保窗口在屏幕范围内（至少部分可见）
            if x < -self.width() + 100:
                x = 0
            if y < 0:
                y = 0
            if x > screen.width() - 100:
                x = screen.width() - self.width()
            if y > screen.height() - 100:
                y = screen.height() - self.height()
            
            self.move(x, y)
        else:
            # 默认居中
            x = (screen.width() - self.width()) // 2
            y = (screen.height() - self.height()) // 2
            self.move(x, y)
        
        # 恢复最大化状态
        if app_config.window_maximized:
            self.showMaximized()
    
    def _save_window_state(self):
        """保存窗口位置和大小到配置"""
        config = self.config_service.get_config()
        
        # 保存最大化状态
        config.app.window_maximized = self.isMaximized()
        
        # 如果没有最大化，保存当前位置和大小
        if not self.isMaximized():
            geometry = self.geometry()
            config.app.window_x = geometry.x()
            config.app.window_y = geometry.y()
            config.app.window_width = geometry.width()
            config.app.window_height = geometry.height()
        
        # 保存配置
        self.config_service.set_config(config)
        self.config_service.save_config_file()
        self.logger.info("窗口状态已保存")
