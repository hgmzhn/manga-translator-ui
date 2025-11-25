
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget

from app_logic import MainAppLogic
from editor.editor_controller import EditorController
from editor.editor_logic import EditorLogic
from editor.editor_model import EditorModel
from editor_view import EditorView
from main_view import MainView
from services import ServiceManager, get_config_service, get_logger, get_state_manager


class MainWindow(QMainWindow):
    """
    应用主窗口，继承自 QMainWindow。
    负责承载所有UI组件、菜单栏、工具栏等。
    """
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Manga Translator")
        self.resize(1280, 800) # 设置默认窗口大小
        
        # 窗口居中显示
        from PyQt6.QtGui import QScreen
        screen = QScreen.availableGeometry(self.screen())
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)
        
        # 窗口图标已在 main.py 中设置，这里不需要重复设置

        self.logger = get_logger(__name__)

        self._setup_logic_and_models()
        self._setup_ui()
        self._load_stylesheet()  # 加载样式表
        self._connect_signals()

        self.app_logic.initialize()

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
        file_menu = menu_bar.addMenu("&文件")
        self.add_files_action = QAction("&添加文件...", self)
        file_menu.addAction(self.add_files_action)

        edit_menu = menu_bar.addMenu("&编辑")
        self.undo_action = QAction("&撤销", self)
        self.redo_action = QAction("&重做", self)
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        view_menu = menu_bar.addMenu("&视图")
        self.main_view_action = QAction("主视图", self)
        self.editor_view_action = QAction("编辑器视图", self)
        view_menu.addAction(self.main_view_action)
        view_menu.addAction(self.editor_view_action)
        
        # 主题菜单（顶级菜单）
        theme_menu = menu_bar.addMenu("&主题")
        self.light_theme_action = QAction("浅色", self)
        self.dark_theme_action = QAction("深色", self)
        self.gray_theme_action = QAction("灰色", self)
        theme_menu.addAction(self.light_theme_action)
        theme_menu.addAction(self.dark_theme_action)
        theme_menu.addAction(self.gray_theme_action)

        # --- 中心布局 (QStackedWidget) ---
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.main_view = MainView(self.app_logic, self)
        self.editor_view = EditorView(self.app_logic, self.editor_model, self.editor_controller, self.editor_logic, self)

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
        self.app_logic.output_path_updated.connect(self.main_view.update_output_path_display)
        self.app_logic.task_completed.connect(self.on_task_completed, type=Qt.ConnectionType.QueuedConnection)
        self.app_logic.log_message.connect(self.main_view.append_log)

        # --- View to Logic Connections ---
        self.main_view.setting_changed.connect(self.app_logic.update_single_config)

        # --- Live-reload connection ---
        self.app_logic.render_setting_changed.connect(self.editor_logic.on_global_render_setting_changed)

        # --- View to Coordinator Connections ---
        self.main_view.file_list.file_selected.connect(self.on_file_selected_from_main_list)
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

    @pyqtSlot(str)
    def on_file_selected_from_main_list(self, file_path: str):
        """
        Coordinator slot. Handles when a file is double-clicked in the main view.
        It tells the editor logic to load the file, then switches the view.
        """
        self.logger.info(f"File double-clicked from main list: {file_path}. Switching to editor.")
        self.enter_editor_mode(file_to_load=file_path)

    @pyqtSlot(list)
    def on_task_completed(self, saved_files: list):
        """
        Handles the completion of a translation task.
        Asks the user if they want to open the results in the editor.
        """
        if not saved_files:
            return

        reply = QMessageBox.question(self, '任务完成', 
                                     f"翻译完成，成功保存 {len(saved_files)} 个文件。\n\n是否在编辑器中打开结果？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

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
        files_to_load: 翻译后的文件列表
        """
        import os

        # 展开文件夹为文件列表，但保留文件夹信息
        expanded_files = []
        folder_map = {}  # 文件到文件夹的映射
        
        for path in self.app_logic.source_files:
            if os.path.isdir(path):
                # 展开文件夹
                folder_files = self.app_logic.file_service.get_image_files_from_folder(path, recursive=True)
                for f in folder_files:
                    folder_map[f] = path  # 记录文件来自哪个文件夹
                expanded_files.extend(folder_files)
            elif os.path.isfile(path):
                expanded_files.append(path)
                folder_map[path] = None  # 单独的文件

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
                    translated_folder_map[translated_file] = final_output_folder  # 记录翻译后文件所属文件夹
            else:
                # 单独添加的文件
                output_folder = self.app_logic.config_service.get_config().app.last_output_path
                translated_file = os.path.join(output_folder, os.path.basename(source_file))

                if os.path.exists(translated_file):
                    translated_files.append(translated_file)
                    translated_folder_map[translated_file] = None

        # 传递文件列表和文件夹映射
        self.editor_logic.load_file_lists(
            source_files=expanded_files, 
            translated_files=translated_files,
            folder_map=translated_folder_map if translated_files else folder_map
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
        self.app_logic.shutdown()
        event.accept()
