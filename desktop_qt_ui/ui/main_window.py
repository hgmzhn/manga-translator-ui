import logging
import os
import re

from manga_translator.utils.system_proxy import set_system_proxy_enabled
from PyQt6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator, QUrl, pyqtSlot
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from app_logic import MainAppLogic
from services import (
    ServiceManager,
    get_config_service,
    get_i18n_manager,
    get_logger,
    get_state_manager,
)
from services.file_list_data_service import FileCatalogSnapshot, FileListDataService
from theme_registry import THEME_OPTIONS
from ui.main_page.view import MainView
from ui.secondary_pages.themed_message_box import show_error_dialog
from utils.app_version import format_app_title, get_app_version


class MainWindow(FluentWindow):
    """
    应用主窗口。
    负责承载所有UI组件、侧边导航、页面切换等。
    侧边栏默认收起为窄图标条，点左上角汉堡按钮可展开（参照 AiNiee 的配置）。
    """

    def __init__(self):
        super().__init__()

        self.logger = get_logger(__name__)
        self.i18n = get_i18n_manager()
        self.app_version = get_app_version()
        self._qt_translator = None
        self._apply_qt_translator(
            self.i18n.get_current_locale() if self.i18n else "en_US"
        )

        self._update_window_title()
        self.resize(1300, 800)  # 设置默认窗口大小（增加20像素）
        self.setMinimumSize(800, 600)  # 设置最小窗口大小
        # 不设置最大大小，允许无限制调整

        # 窗口居中显示
        from PyQt6.QtGui import QScreen

        screen = QScreen.availableGeometry(self.screen())
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

        # 侧边栏：默认收起（48px 窄图标条），悬停显示提示，点汉堡按钮展开
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setUpdateIndicatorPosOnCollapseFinished(True)
        self.navigationInterface.setReturnButtonVisible(False)

        # 顶部标题栏压窄：默认 48 → 36，内容区上边距同步收紧
        self.titleBar.setFixedHeight(36)
        self.widgetLayout.setContentsMargins(0, 36, 0, 0)

        # 窗口图标已在 main.py 中设置，这里不需要重复设置

        # 当前应用的主题（用于逻辑判断）
        self.current_applied_theme = "light"

        self._setup_logic_and_models()
        self._setup_ui()
        self._load_theme()
        self._connect_signals()

        self.app_logic.initialize()

    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _update_window_title(self):
        """Keep the product name stable across interface languages."""
        self.setWindowTitle(format_app_title("Manga Translator UI", self.app_version))

    def _setup_logic_and_models(self):
        """实例化所有逻辑和数据模型"""
        self.config_service = get_config_service()
        self.state_manager = get_state_manager()
        config = self.config_service.get_config()
        set_system_proxy_enabled(bool(config.app.use_system_proxy))

        initial_theme = config.app.theme
        if initial_theme == "system":
            detected_theme = self._detect_system_theme()
            if detected_theme == "dark":
                initial_theme = "dark"
            else:
                initial_theme = config.app.theme_user_preference

        from ui.theme import apply_application_theme

        apply_application_theme(initial_theme, QApplication.instance())
        self.current_applied_theme = initial_theme

        # --- Logic Controllers ---
        self.app_logic = MainAppLogic()
        self.file_list_data_service = FileListDataService(self, max_workers=2)
        self.app_logic.file_list_data_service = self.file_list_data_service
        self._file_catalog_snapshot = FileCatalogSnapshot.empty()
        self._main_catalog_generation = 0
        self._main_catalog_loading = False
        self._pending_editor_open = None
        ServiceManager.register_service("app_logic", self.app_logic)
        self.editor_model = None
        self.editor_controller = None
        self.editor_logic = None
        self.editor_view = None

    def _setup_ui(self):
        """初始化UI组件"""
        # 不显示顶部菜单栏，菜单功能统一整合到设置区域
        self._create_ui_actions()

        self.main_view = MainView(self.app_logic, self)

        # 设置 app_logic 对 main_view 的引用，用于更新进度条
        self.app_logic.main_view = self.main_view

        self.stacked_widget = self.stackedWidget
        self._main_navigation_items = {}
        self._register_main_interfaces()
        self._ensure_editor_initialized()
        self.stacked_widget.currentChanged.connect(self._on_fluent_page_changed)

    def _register_main_interfaces(self):
        pages = [
            (
                "translation",
                self.main_view.translation_interface,
                FIF.HOME,
                self._t("Translation Interface"),
            ),
            (
                "settings",
                self.main_view.settings_page,
                FIF.SETTING,
                self._t("Settings"),
            ),
            ("env", self.main_view.env_page, FIF.CONNECT, self._t("API Management")),
            (
                "prompts",
                self.main_view.prompt_page,
                FIF.DOCUMENT,
                self._t("Prompt Management"),
            ),
            (
                "replacements",
                self.main_view.replacements_page,
                FIF.EDIT,
                self._t("Replacement Rules"),
            ),
            (
                "rich_text_rules",
                self.main_view.rich_text_rules_page,
                FIF.FONT,
                self._t("Rich Text Rules"),
            ),
            (
                "batch_edit",
                self.main_view.batch_edit_page,
                FIF.LIBRARY,
                self._t("Batch Management"),
            ),
            (
                "about",
                self.main_view.about_page,
                FIF.INFO,
                self._t("About Application"),
            ),
        ]
        for key, page, icon, text in pages:
            page.setObjectName(f"main_{key}_page")
            self._main_navigation_items[key] = self.addSubInterface(page, icon, text)

        self._main_pages_by_widget = {page: key for key, page, _icon, _text in pages}
        self.main_view.set_navigation_switcher(self._switch_main_page)
        self.switchTo(self.main_view.translation_interface)

    def _switch_main_page(self, page_key: str):
        page = (
            self.main_view.page_widgets.get(page_key)
            if hasattr(self.main_view, "page_widgets")
            else None
        )
        if page is not None:
            self.switchTo(page)
            self._on_main_page_activated(page_key)

    def _on_fluent_page_changed(self, index: int):
        widget = self.stacked_widget.widget(index)
        page_key = getattr(self, "_main_pages_by_widget", {}).get(widget)
        if page_key:
            self._on_main_page_activated(page_key)

    def _on_main_page_activated(self, page_key: str):
        if page_key == "settings" and not getattr(
            self.main_view, "_settings_ui_ready", False
        ):
            self.main_view.set_parameters(self.config_service.get_config().model_dump())
        elif page_key == "about":
            self.main_view._refresh_about_page_texts()
        elif page_key == "env":
            self.main_view._refresh_env_api_groups()
        elif page_key == "replacements":
            if hasattr(self.main_view, "replacements_editor_panel"):
                self.main_view.replacements_editor_panel.refresh()
        elif page_key == "rich_text_rules":
            if hasattr(self.main_view, "rich_text_rules_editor_panel"):
                self.main_view.rich_text_rules_editor_panel.refresh()
        elif page_key == "batch_edit":
            if hasattr(self.main_view, "batch_edit_panel"):
                self.main_view.batch_edit_panel.set_catalog_snapshot(
                    self._file_catalog_snapshot
                )
                self.main_view.batch_edit_panel.refresh()

    def _ensure_editor_initialized(self):
        if self.editor_view is not None:
            return

        from editor.editor_controller import EditorController
        from editor.editor_logic import EditorLogic
        from editor.editor_model import EditorModel
        from ui.editor.view import EditorView

        self.editor_model = EditorModel()
        self.editor_controller = EditorController(self.editor_model)
        self.editor_logic = EditorLogic(
            self.editor_controller,
            parent=self,
            file_data_service=self.file_list_data_service,
        )
        self.editor_view = EditorView(
            self.app_logic,
            self.editor_model,
            self.editor_controller,
            self.editor_logic,
            self,
        )
        self.editor_view.setObjectName("editor_page")
        self.addSubInterface(
            self.editor_view,
            FIF.EDIT,
            self._t("Editor View"),
            position=NavigationItemPosition.BOTTOM,
        )

        self.app_logic.config_loaded.connect(
            self.editor_view.property_panel.repopulate_options
        )

        self.editor_view._apply_editor_style(self.current_applied_theme)
        self.editor_view.property_panel.repopulate_options()

        if hasattr(self.main_view, "batch_edit_panel"):
            # 编辑器把 region 常驻内存且不监听文件变化，批量写回后必须让它重新
            # 加载，否则切图时的自动保存会用旧数据覆盖掉刚写进去的修改。
            self.main_view.batch_edit_panel.set_editor_context(
                self.editor_model.get_source_image_path,
                self.editor_controller.document_service.load_image_and_regions,
            )

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

    def _load_theme(self):
        """根据配置初始化 qfluentwidgets 主题。"""
        from services import get_config_service

        config_service = get_config_service()
        config = config_service.get_config()

        # 获取主题设置，Pydantic会自动使用默认值'light'
        theme = config.app.theme
        self._apply_theme(theme)

    def _apply_theme(self, theme: str):
        """应用指定的主题"""

        # 处理系统主题逻辑：如果是 'system'，则解析为实际主题
        if theme == "system":
            sys_theme = self._detect_system_theme()
            if sys_theme == "dark":
                self._apply_theme("dark")
            else:
                config = self.config_service.get_config()
                # 使用用户偏好（所有非 dark 主题）
                self._apply_theme(config.app.theme_user_preference)
            return

        # 记录当前实际应用的主题
        self.current_applied_theme = theme

        app = QApplication.instance()
        from ui.theme import apply_application_theme

        apply_application_theme(theme, app)

        # 通知各视图刷新局部 Fluent 主题状态（MainView 是纯逻辑对象，无需 update）
        if hasattr(self, "main_view") and self.main_view:
            self.main_view.apply_fluent_theme(theme)
        if hasattr(self, "editor_view") and self.editor_view:
            self.editor_view._apply_editor_style(theme)
            self.editor_view.update()
        if hasattr(self, "stacked_widget") and self.stacked_widget:
            self.stacked_widget.update()
        self.update()
        # 延迟到事件循环下一拍统一应用一次原生标题栏主题，避免同一次切换重复设置
        QTimer.singleShot(
            0,
            lambda active_theme=theme: self._apply_native_title_bar_theme(active_theme),
        )

    def _apply_native_title_bar_theme(self, theme: str):
        """同步 Windows 原生标题栏颜色，避免深色内容区配浅色系统标题栏。"""
        from ui.theme import apply_native_title_bar_theme

        apply_native_title_bar_theme(self, theme, logger=self.logger)

    def _detect_system_theme(self) -> str | None:
        """通过 Qt 检测系统主题；平台未提供外观信息时返回 None。"""
        scheme = QApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
        return None

    def _on_system_theme_changed(self, scheme: Qt.ColorScheme):
        """响应系统外观通知，仅更新跟随系统模式下的界面。"""
        config = self.config_service.get_config()
        if config.app.theme != "system":
            return

        if scheme == Qt.ColorScheme.Dark:
            theme = "dark"
        elif scheme == Qt.ColorScheme.Light:
            theme = config.app.theme_user_preference
        else:
            # 无法识别外观时保留当前主题，不覆盖用户偏好。
            return

        if theme != self.current_applied_theme:
            self.logger.info(f"System color scheme changed: {scheme.name}; applying {theme}")
            self._apply_theme(theme)

    def _change_theme(self, theme: str):
        """切换主题并保存到配置"""
        from services import get_config_service

        config_service = get_config_service()
        config = config_service.get_config()

        self._apply_theme(theme)

        # 仅保存手动选择的非 dark 主题，供浅色系统下恢复。
        if theme not in ("dark", "system"):
            config.app.theme_user_preference = theme

        # 保存到配置
        config.app.theme = theme
        config_service.set_config(config)

        # 保存到文件
        config_service.save_config_file()

    def _connect_signals(self):
        # --- MainAppLogic Connections ---
        self.app_logic.config_loaded.connect(self.main_view.set_parameters)
        self.app_logic.file_sources_changed.connect(self._request_main_file_snapshot)
        self.app_logic.file_removed.connect(self.main_view.file_list.remove_file)
        self.app_logic.file_removed.connect(self._on_file_removed_update_editor)
        self.app_logic.files_cleared.connect(self.main_view.file_list.clear)
        self.app_logic.files_cleared.connect(self._on_files_cleared_update_editor)
        self.app_logic.output_path_updated.connect(
            self.main_view.update_output_path_display
        )
        self.app_logic.task_completed.connect(
            self.on_task_completed, type=Qt.ConnectionType.QueuedConnection
        )
        self.app_logic.error_dialog_requested.connect(
            self._show_error_dialog, type=Qt.ConnectionType.QueuedConnection
        )
        self.app_logic.warning_dialog_requested.connect(
            self._show_warning_dialog, type=Qt.ConnectionType.QueuedConnection
        )
        self.config_service.write_failed.connect(
            self._show_config_write_failed,
            type=Qt.ConnectionType.QueuedConnection,
        )
        deferred_write_error = self.config_service.take_deferred_write_error()
        if deferred_write_error:
            QTimer.singleShot(
                0,
                lambda error=deferred_write_error: self._show_config_write_failed(
                    error
                ),
            )
        self.file_list_data_service.loading.connect(
            self._on_main_catalog_loading,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self.file_list_data_service.snapshot_ready.connect(
            self._on_main_catalog_ready,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self.file_list_data_service.error.connect(
            self._on_main_catalog_error,
            type=Qt.ConnectionType.QueuedConnection,
        )

        # --- View to Logic Connections ---
        self.main_view.setting_changed.connect(self.app_logic.update_single_config)
        self.main_view.env_var_changed.connect(self.app_logic.save_env_var)
        self.main_view.editor_view_requested.connect(self.switch_to_editor_view)
        self.main_view.theme_change_requested.connect(self._change_theme)
        self.main_view.language_change_requested.connect(
            self._change_language, type=Qt.ConnectionType.QueuedConnection
        )

        # --- View to Coordinator Connections ---
        self.main_view.file_list.file_selected.connect(
            self.on_file_selected_from_main_list
        )
        self.main_view.file_list.files_dropped.connect(
            self.app_logic.add_files
        )  # 拖放文件支持
        # self.main_view.enter_editor_button.clicked.connect(self.enter_editor_mode) # Example for a dedicated button

        # --- View Switching Connections ---
        self.main_view_action.triggered.connect(
            lambda: self.switchTo(self.main_view.translation_interface)
        )
        self.editor_view_action.triggered.connect(self.switch_to_editor_view)

        # --- 撤销/重做延迟转发到编辑器controller ---
        self.undo_action.triggered.connect(self._handle_undo)
        self.redo_action.triggered.connect(self._handle_redo)

        # --- 主题切换连接 ---
        # Qt 发出通知时仍使用旧调色板，排队到其更新完成后再应用自定义主题。
        QApplication.styleHints().colorSchemeChanged.connect(
            self._on_system_theme_changed,
            type=Qt.ConnectionType.QueuedConnection,
        )
        for theme_key, action in getattr(self, "theme_actions", {}).items():
            action.triggered.connect(
                lambda checked=False, selected_theme=theme_key: self._change_theme(
                    selected_theme
                )
            )

    @pyqtSlot()
    def _request_main_file_snapshot(self):
        self._main_catalog_loading = True
        self.main_view.file_list.set_loading(keep_items=True)
        try:
            self._main_catalog_generation = (
                self.file_list_data_service.request_snapshot(
                    "main",
                    tuple(self.app_logic.source_files),
                    tuple(self.app_logic.excluded_subfolders),
                    tuple(self.app_logic.excluded_files),
                )
            )
        except RuntimeError as exc:
            self._main_catalog_loading = False
            self.main_view.file_list.set_error(str(exc))

    @pyqtSlot(str, int)
    def _on_main_catalog_loading(self, channel: str, generation: int):
        if channel != "main":
            return
        self._main_catalog_loading = True
        self._main_catalog_generation = generation

    @pyqtSlot(str, int, object)
    def _on_main_catalog_ready(self, channel: str, generation: int, snapshot: object):
        if channel != "main" or generation != self._main_catalog_generation:
            return
        self._main_catalog_loading = False
        self._file_catalog_snapshot = snapshot
        self.main_view.file_list.set_snapshot(snapshot)
        if hasattr(self.main_view, "batch_edit_panel"):
            # 批量管理的作用范围跟随主页文件列表，快照一变就同步过去
            self.main_view.batch_edit_panel.set_catalog_snapshot(snapshot)
        for warning in snapshot.warnings:
            self.logger.warning(warning)

        pending = self._pending_editor_open
        self._pending_editor_open = None
        if pending is not None:
            file_to_load, files_to_load = pending
            QTimer.singleShot(
                0,
                lambda: self.enter_editor_mode(
                    file_to_load=file_to_load,
                    files_to_load=files_to_load,
                ),
            )

    @pyqtSlot(str, int, str)
    def _on_main_catalog_error(self, channel: str, generation: int, message: str):
        if channel != "main" or generation != self._main_catalog_generation:
            return
        self._main_catalog_loading = False
        self.main_view.file_list.set_error(message)

    @pyqtSlot(str)
    def on_file_selected_from_main_list(self, file_path: str):
        """
        Coordinator slot. Handles when a file is double-clicked in the main view.
        It tells the editor logic to load the file, then switches the view.
        """
        self.logger.info(
            f"File double-clicked from main list: {file_path}. Switching to editor."
        )
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
                    self.editor_controller.document_service.clear_editor_state()
                # 如果移除的是文件夹，检查当前图片是否在该文件夹内
                elif os.path.isdir(file_path):
                    try:
                        # 检查当前图片是否在被移除的文件夹内
                        if (
                            os.path.commonpath([norm_current, norm_removed])
                            == norm_removed
                        ):
                            self.editor_controller.document_service.clear_editor_state()
                    except ValueError:
                        # 不同驱动器，跳过
                        pass

            # 注意：编辑器有自己独立的文件列表，不需要同步主页的删除操作
            # 只有当主页文件全部清空时，才清空编辑器列表

    def _on_files_cleared_update_editor(self):
        """当文件列表被清空时，清空编辑器"""
        if not self.editor_view or not self.editor_logic:
            return
        self.logger.info("Files cleared. Clearing editor.")
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
            self.logger.info(f"Language switched to: {locale_code}")

    def _apply_qt_translator(self, locale_code: str):
        """加载 Qt 内建控件翻译（如 QColorDialog），使其跟随应用语言。"""
        app = QApplication.instance()
        if app is None:
            return

        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator.deleteLater()
            self._qt_translator = None

        translator = QTranslator(self)
        qt_translations_dir = QLibraryInfo.path(
            QLibraryInfo.LibraryPath.TranslationsPath
        )

        # locale_code 形如 zh_CN / en_US，依次尝试精确与语言级别匹配
        language = QLocale(locale_code).name().split("_", 1)[0]
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
        if hasattr(self, "main_view") and self.main_view:
            self.main_view.refresh_ui_texts()
            self._refresh_navigation_texts()

        # 刷新编辑器视图的所有文本（如果存在）
        if hasattr(self, "editor_view") and self.editor_view:
            if hasattr(self.editor_view, "refresh_ui_texts"):
                self.editor_view.refresh_ui_texts()

    def _refresh_action_texts(self):
        """刷新内部动作文本（菜单栏隐藏时仍保留动作对象）"""
        if hasattr(self, "add_files_action"):
            self.add_files_action.setText(self._t("&Add Files..."))
        if hasattr(self, "undo_action"):
            self.undo_action.setText(self._t("&Undo"))
        if hasattr(self, "redo_action"):
            self.redo_action.setText(self._t("&Redo"))
        if hasattr(self, "main_view_action"):
            self.main_view_action.setText(self._t("Main View"))
        if hasattr(self, "editor_view_action"):
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

            # 翻译会新建/更新 JSON 元数据；先刷新完整快照，编辑器打开请求会自动等待。
            self._request_main_file_snapshot()

            if not self._should_prompt_open_results_in_editor():
                return

            # qfluentwidgets 的无边框对话框若以最小化主窗口为父窗口弹出，
            # Windows 11 上恢复后可能停止合成窗口新内容；弹框前先恢复可避免该状态。
            if self.isMinimized():
                self.showNormal()

            from PyQt6.QtWidgets import QMessageBox

            reply = show_error_dialog(
                self,
                self._t("Task Completed"),
                "",
                self._t(
                    "Translation completed, {count} files saved.\n\nOpen results in editor?",
                    count=len(saved_files),
                ),
                icon=QMessageBox.Icon.Question,
                buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                default_button=QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.enter_editor_mode(files_to_load=saved_files)
        except Exception as e:
            self.logger.error(f"Error in on_task_completed: {e}", exc_info=True)
            import traceback

            traceback.print_exc()

    def _should_prompt_open_results_in_editor(self) -> bool:
        """Only prompt for workflows that produce editor-meaningful results."""
        try:
            config = self.config_service.get_config()
            cli = getattr(config, "cli", None)
            if cli is None:
                return True

            if getattr(cli, "replace_translation", False):
                return True

            if getattr(cli, "load_text", False):
                return True

            incompatible_modes = (
                getattr(cli, "translate_json_only", False),
                getattr(cli, "template", False),
                getattr(cli, "generate_and_export", False),
                getattr(cli, "colorize_only", False),
                getattr(cli, "upscale_only", False),
                getattr(cli, "inpaint_only", False),
            )
            return not any(incompatible_modes)
        except Exception as e:
            self.logger.warning(f"Failed to determine whether to show editor prompt; showing it by default: {e}")
            return True

    @pyqtSlot(str)
    def _show_error_dialog(self, error_message: str):
        """弹出翻译错误提示框"""
        try:
            log_dir = self._resolve_log_folder_from_message(error_message)
            show_error_dialog(
                self,
                self._t("Translation Error"),
                "",
                error_message,
                extra_button_text=self._t("Open log folder"),
                extra_button_callback=lambda: self._open_log_folder(log_dir),
            )
        except Exception as e:
            self.logger.error(f"_show_error_dialog error: {e}", exc_info=True)

    def _resolve_log_folder_from_message(self, message: str) -> str:
        match = re.search(r"日志文件[：:]\s*(.+)", str(message or ""))
        if match:
            log_path = match.group(1).strip()
            log_path = log_path.splitlines()[0].strip()
            if log_path:
                return os.path.dirname(os.path.normpath(os.path.abspath(log_path)))
        for handler in reversed(logging.getLogger().handlers):
            if isinstance(handler, logging.FileHandler):
                log_path = str(getattr(handler, "baseFilename", "") or "").strip()
                if log_path:
                    return os.path.dirname(os.path.normpath(os.path.abspath(log_path)))
        return os.path.normpath(os.path.abspath(os.path.join(os.getcwd(), "result")))

    def _open_log_folder(self, folder: str):
        target = os.path.normpath(
            os.path.abspath(folder or os.path.join(os.getcwd(), "result"))
        )
        os.makedirs(target, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    @pyqtSlot(str)
    def _show_warning_dialog(self, message: str):
        """弹出任务提示框"""
        try:
            show_error_dialog(
                self,
                self._t("Warning"),
                "",
                message,
            )
        except Exception as e:
            self.logger.error(f"_show_warning_dialog error: {e}", exc_info=True)

    @pyqtSlot(str)
    def _show_config_write_failed(self, error: str):
        """明确提示配置写入失败，避免用户误以为 API Key 已保存。"""
        try:
            guidance = self._t(
                "Configuration save failed. Changes were not saved. Check file permissions and antivirus or security software blocking access."
            )
            detail = str(error or "").strip()
            message = f"{guidance}\n\n{detail}" if detail else guidance
            show_error_dialog(self, self._t("Error"), "", message)
        except Exception as exc:
            self.logger.error(f"_show_config_write_failed error: {exc}", exc_info=True)

    def switch_to_editor_view(self):
        """
        Simply switches to the editor view without reloading file lists.
        Used when user manually switches views.
        """
        self._ensure_editor_initialized()
        if self.editor_view and self.editor_view.property_panel:
            self.editor_view.property_panel.repopulate_options()
        self.switchTo(self.editor_view)

    def enter_editor_mode(self, file_to_load: str = None, files_to_load: list = None):
        """
        Switches to the editor view and loads the necessary files.
        file_to_load: 单个文件路径（双击文件时使用）
        files_to_load: 保存结果列表（从翻译完成进入时使用，用于定位要打开的原图）
        """
        try:
            self._ensure_editor_initialized()
            if self.editor_view and self.editor_view.property_panel:
                self.editor_view.property_panel.repopulate_options()

            if self._main_catalog_loading:
                self._pending_editor_open = (
                    file_to_load,
                    list(files_to_load) if files_to_load else None,
                )
                self.editor_view.file_list.set_loading()
                self.switchTo(self.editor_view)
                return

            if self.app_logic.source_files and not self._file_catalog_snapshot.sources:
                self._pending_editor_open = (
                    file_to_load,
                    list(files_to_load) if files_to_load else None,
                )
                self.editor_view.file_list.set_loading()
                self._request_main_file_snapshot()
                self.switchTo(self.editor_view)
                return

            editor_snapshot = self._file_catalog_snapshot.images_only()
            self.editor_logic.apply_file_snapshot(
                editor_snapshot,
                excluded_folders=self.app_logic.excluded_subfolders,
                excluded_files=self.app_logic.excluded_files,
            )

            target_path = file_to_load
            if files_to_load:
                target_path = (
                    self.app_logic.resolve_completed_source(files_to_load[0])
                    or files_to_load[0]
                )
            if target_path:
                self.editor_logic.load_image_into_editor(target_path)
            elif editor_snapshot.editor_files:
                self.editor_logic.load_image_into_editor(
                    editor_snapshot.editor_files[0]
                )

            self.switchTo(self.editor_view)
        except Exception as e:
            self.logger.error(f"Error in enter_editor_mode: {e}", exc_info=True)
            import traceback

            traceback.print_exc()

    def _refresh_navigation_texts(self):
        nav_labels = {
            "translation": self._t("Translation Interface"),
            "settings": self._t("Settings"),
            "about": self._t("About Application"),
            "env": self._t("API Management"),
            "prompts": self._t("Prompt Management"),
            "replacements": self._t("Replacement Rules"),
            "rich_text_rules": self._t("Rich Text Rules"),
            "batch_edit": self._t("Batch Management"),
        }
        for key, text in nav_labels.items():
            item = getattr(self, "_main_navigation_items", {}).get(key)
            if item is not None and hasattr(item, "setText"):
                item.setText(text)
                # 收起状态下条目靠悬停提示识别，语言切换时一并刷新
                item.setToolTip(text)

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        if hasattr(self, "main_view") and hasattr(self.main_view, "update_checker"):
            self.main_view.update_checker.stop()
        unfinished_exports = 0
        if self.editor_controller is not None:
            unfinished_exports = (
                self.editor_controller.export_service.unfinished_count()
            )
        if unfinished_exports:
            from PyQt6.QtWidgets import QMessageBox

            reply = show_error_dialog(
                self,
                "后台任务尚未完成",
                "",
                f"还有 {unfinished_exports} 个保存或导出任务正在处理。\n\n等待全部任务完成后退出？",
                icon=QMessageBox.Icon.Question,
                buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                default_button=QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            self.editor_controller.shutdown()

        # 先等待 API 测试/取模型等后台线程结束（带超时），
        # 避免 QThread: Destroyed while thread is still running。
        if hasattr(self, "main_view") and self.main_view:
            self.main_view.shutdown_background_threads(3000)
            if hasattr(self.main_view, "batch_edit_panel"):
                self.main_view.batch_edit_panel.shutdown()
        if self.editor_logic is not None:
            self.editor_logic.shutdown()
        self.file_list_data_service.shutdown()
        self.app_logic.shutdown()
        event.accept()
