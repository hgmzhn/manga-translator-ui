
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, ProgressBar

from services import get_config_service, get_i18n_manager
from ui.main_page import dynamic_settings as main_view_dynamic
from ui.main_page import env_management as main_view_env
from ui.main_page import layout as layout_parts
from ui.main_page import runtime as main_view_runtime
from ui.main_page.pages.env_page import create_env_page
from ui.main_page.pages.prompt_page import create_prompt_page
from ui.main_page.pages.replacements_page import create_replacements_page
from ui.main_page.pages.rich_text_rules_page import create_rich_text_rules_page
from ui.main_page.pages.settings_page import create_settings_page
from ui.main_page.pages.translation_page import create_translation_page
from utils.app_version import get_app_version


class MainView(QObject):
    """
    主页面逻辑控制器（纯 QObject，不是控件）。

    各页面控件（translation_interface / settings_page / env_page 等）由本对象
    创建后交给 FluentWindow.addSubInterface 托管；MainView 自身从不进入任何
    布局，只负责信号、状态与页面构建逻辑。需要控件父级时用 _dialog_parent()。
    """
    setting_changed = pyqtSignal(str, object)
    env_var_changed = pyqtSignal(str, str)
    api_task_finished = pyqtSignal(str, object)
    editor_view_requested = pyqtSignal()
    theme_change_requested = pyqtSignal(str)
    language_change_requested = pyqtSignal(str)

    _open_filter_list = main_view_dynamic._open_filter_list
    _open_ai_ocr_prompt_editor = main_view_dynamic._open_ai_ocr_prompt_editor
    _open_ai_colorizer_prompt_editor = main_view_dynamic._open_ai_colorizer_prompt_editor
    _open_ai_renderer_prompt_editor = main_view_dynamic._open_ai_renderer_prompt_editor
    _process_next_setting_chunk = main_view_dynamic._process_next_setting_chunk
    _finalize_settings_ui = main_view_dynamic._finalize_settings_ui
    _create_dynamic_settings = main_view_dynamic._create_dynamic_settings
    _on_setting_changed = main_view_dynamic._on_setting_changed
    _on_upscale_ratio_changed = main_view_dynamic._on_upscale_ratio_changed
    _on_numeric_input_changed = main_view_dynamic._on_numeric_input_changed
    _update_upscale_ratio_options = main_view_dynamic._update_upscale_ratio_options
    _create_param_widgets = main_view_dynamic._create_param_widgets

    _create_translation_page = create_translation_page
    _create_settings_page = create_settings_page
    _create_env_page = create_env_page
    _create_prompt_page = create_prompt_page
    _populate_theme_combo = layout_parts.populate_theme_combo
    _populate_language_combo = layout_parts.populate_language_combo
    _on_theme_combo_changed = layout_parts.on_theme_combo_changed
    _on_language_combo_changed = layout_parts.on_language_combo_changed
    _refresh_prompt_manager = layout_parts.refresh_prompt_manager
    _apply_selected_prompt = layout_parts.apply_selected_prompt
    _on_prompt_selection_changed = layout_parts.on_prompt_selection_changed
    _open_prompt_editor = layout_parts.open_prompt_editor
    _create_new_prompt = layout_parts.create_new_prompt
    _copy_selected_prompt = layout_parts.copy_selected_prompt
    _rename_selected_prompt = layout_parts.rename_selected_prompt
    _delete_selected_prompt = layout_parts.delete_selected_prompt

    _create_replacements_page = create_replacements_page
    _create_rich_text_rules_page = create_rich_text_rules_page
    update_progress = main_view_runtime.update_progress
    reset_progress = main_view_runtime.reset_progress

    _create_env_widgets = main_view_env.create_env_widgets
    _create_api_rotation_widgets = main_view_env.create_api_rotation_widgets
    _refresh_env_api_groups = main_view_dynamic._refresh_env_api_groups
    _get_env_default_placeholder = main_view_env.get_env_default_placeholder
    _debounced_save_env_var = main_view_env.debounced_save_env_var
    _flush_env_var_immediately = main_view_env.flush_env_var_immediately
    _flush_all_pending_env_vars = main_view_env.flush_all_pending_env_vars
    _refresh_api_slot_status_styles = main_view_env.refresh_api_slot_status_styles
    shutdown_background_threads = main_view_env.shutdown_background_threads
    _on_api_task_future_finished = main_view_env.on_api_task_future_finished
    _on_open_custom_api_params_file = main_view_env.on_open_custom_api_params_file
    _refresh_api_feature_selectors = main_view_env.refresh_api_feature_selectors
    _on_api_feature_combo_changed = main_view_env.on_api_feature_combo_changed
    _create_api_feature_selector_row = main_view_env.create_api_feature_selector_row
    _validate_api_candidate_availability = main_view_env.validate_api_candidate_availability
    _on_test_api_clicked = main_view_env.on_test_api_clicked
    _on_test_current_api_section_clicked = main_view_env.on_test_current_api_section_clicked
    _on_get_models_clicked = main_view_env.on_get_models_clicked
    _refresh_preset_list = main_view_env.refresh_preset_list
    _on_add_preset_clicked = main_view_env.on_add_preset_clicked
    _on_delete_preset_clicked = main_view_env.on_delete_preset_clicked
    _on_preset_changed = main_view_env.on_preset_changed
    update_output_path_display = main_view_env.update_output_path_display
    _trigger_add_files = main_view_env.trigger_add_files

    _enable_stop_button = main_view_runtime.enable_stop_button
    set_stopping_state = main_view_runtime.set_stopping_state
    _sync_workflow_mode_from_config = main_view_runtime.sync_workflow_mode_from_config
    _on_workflow_mode_changed = main_view_runtime.on_workflow_mode_changed
    _update_workflow_mode_description = main_view_runtime.update_workflow_mode_description
    update_start_button_text = main_view_runtime.update_start_button_text

    def set_navigation_switcher(self, switcher):
        self._navigation_switcher = switcher

    def _switch_content_page(self, page_key: str):
        if callable(getattr(self, "_navigation_switcher", None)):
            self._navigation_switcher(page_key)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()
        self.app_version = get_app_version()
        self.env_widgets = {}
        self._active_api_tasks = {}
        self.api_task_finished.connect(self._on_api_task_future_finished)

        self.env_var_changed.connect(self.controller.save_env_var)
        self._navigation_switcher = None

        self.translation_page = self._create_translation_page()
        self.translation_interface = self._create_translation_interface(self.translation_page)
        self.settings_page = self._create_settings_page()
        self.env_page = self._create_env_page()
        self.prompt_page = self._create_prompt_page()
        self.replacements_page = self._create_replacements_page()
        self.rich_text_rules_page = self._create_rich_text_rules_page()
        self.page_widgets = {
            "translation": self.translation_interface,
            "settings": self.settings_page,
            "env": self.env_page,
            "prompts": self.prompt_page,
            "replacements": self.replacements_page,
            "rich_text_rules": self.rich_text_rules_page,
        }

        # 不在这里调用 _create_dynamic_settings，等待 app_logic.initialize 发送 config_loaded 信号
        # self._create_dynamic_settings()  # 删除这行，避免重复创建

        # Connect signals for button state management
        self.controller.state_manager.is_translating_changed.connect(self.on_translation_state_changed, type=Qt.ConnectionType.QueuedConnection)
        self.controller.state_manager.current_config_changed.connect(self.update_start_button_text)
        QTimer.singleShot(100, self.update_start_button_text) # Set initial text
        QTimer.singleShot(100, self._sync_workflow_mode_from_config) # Sync workflow mode dropdown
        self.apply_fluent_theme()

    def _create_translation_interface(self, translation_page: QWidget) -> QWidget:
        interface = QWidget()
        interface.setObjectName("main_translation_interface")
        layout = QVBoxLayout(interface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(translation_page, 1)

        progress_card = CardWidget()
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(16, 12, 16, 12)
        progress_layout.setSpacing(8)

        progress_header = QWidget()
        progress_header_layout = QHBoxLayout(progress_header)
        progress_header_layout.setContentsMargins(0, 0, 0, 0)
        progress_header_layout.setSpacing(8)

        self.progress_info_label = BodyLabel("")
        self.progress_info_label.setWordWrap(True)
        progress_header_layout.addWidget(self.progress_info_label, 1)

        self.progress_count_label = CaptionLabel("0/0 (0%)")
        self.progress_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_header_layout.addWidget(self.progress_count_label)
        progress_layout.addWidget(progress_header)

        self.progress_bar = ProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self.progress_bar)
        layout.addWidget(progress_card, 0)
        return interface
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _dialog_parent(self) -> QWidget | None:
        """返回可用作对话框/控件父级的 QWidget。

        MainView 是纯逻辑 QObject，不能充当控件父级；这里返回创建时传入的
        父对象（主窗口）——若它不是 QWidget 则返回 None，让对话框顶层显示。
        """
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def apply_fluent_theme(self, theme: str | None = None):
        del theme
        if hasattr(self, "prompt_preview_panel") and self.prompt_preview_panel:
            self.prompt_preview_panel.apply_theme()
        if hasattr(self, "replacements_editor_panel") and self.replacements_editor_panel:
            self.replacements_editor_panel.apply_theme()
        if hasattr(self, "rich_text_rules_editor_panel") and self.rich_text_rules_editor_panel:
            self.rich_text_rules_editor_panel.apply_theme()
        if hasattr(self, "settings_page") and self.settings_page:
            self.settings_page.update()
        self._refresh_api_slot_status_styles()
        self.file_list.refresh_empty_state_text()
        self.progress_bar.update()

    @pyqtSlot(dict)
    def set_parameters(self, config: dict):
        main_view_dynamic.set_parameters(self, config)

    def _show_setting_description(self, key: str, name: str, description: str):
        """更新右侧描述面板"""
        if hasattr(self, 'settings_desc_name'):
            self.settings_desc_name.setText(name)
        if hasattr(self, 'settings_desc_key'):
            self.settings_desc_key.setText(self._t("Settings Desc Key", config_key=key))
        if hasattr(self, 'settings_desc_text'):
            self.settings_desc_text.setText(description or self._t("Settings Desc No Description"))

    def refresh_tab_titles(self):
        """刷新标签页标题（用于语言切换）。"""
        tab_title_by_route = getattr(self, "settings_tab_title_key_by_route", None)
        if tab_title_by_route:
            for route_key, title_key in tab_title_by_route.items():
                self.settings_tabs.setItemText(route_key, self._t(title_key))
            return

        tab_titles = ["Application Settings", "Basic Settings", "Advanced Settings", "Options"]
        routes = getattr(self, "settings_tab_routes", [])
        for route_key, title_key in zip(routes, tab_titles):
            self.settings_tabs.setItemText(route_key, self._t(title_key))

    def _translated_setting_label(self, full_key: str) -> str:
        key = str(full_key or "").rsplit(".", 1)[-1]
        if full_key == "app.theme":
            return self._t("Theme:").rstrip(":：")
        if full_key == "app.ui_language":
            return self._t("Language:").rstrip(":：")
        if full_key == "app.unload_models_after_translation":
            translated = self._t("label_unload_models_after_translation")
            return translated if translated != "label_unload_models_after_translation" else "Unload Models After Translation"

        fixed_prompt_spec = main_view_dynamic._get_fixed_prompt_editor_spec(self, full_key)
        if fixed_prompt_spec:
            return fixed_prompt_spec["label"]

        labels = self.controller.get_display_mapping("labels") if self.controller else None
        if labels and labels.get(key):
            return labels[key]
        return key

    def _refresh_dynamic_setting_texts(self):
        """Refresh dynamic settings labels in place during language switch."""
        for panel in getattr(self, "tab_frames", {}).values():
            layout = panel.layout() if panel is not None else None
            if layout is None:
                continue
            for index in range(layout.count()):
                widget = layout.itemAt(index).widget()
                full_key = getattr(widget, "_full_key", None)
                if not full_key or not hasattr(widget, "setText"):
                    continue
                widget.setText(self._translated_setting_label(full_key))
                if full_key in {
                    "ocr.ai_ocr_prompt_path",
                    "colorizer.ai_colorizer_prompt_path",
                    "render.ai_renderer_prompt_path",
                }:
                    for child in getattr(widget, "_widgets", []):
                        if hasattr(child, "setText"):
                            child.setText(self._t("Edit"))

        highlighted_rows = list(getattr(self, "_highlighted_rows", []))
        if highlighted_rows:
            row = highlighted_rows[-1]
            if hasattr(row, "_activate"):
                try:
                    row._activate()
                except RuntimeError:
                    pass

    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）。"""
        self.refresh_tab_titles()

        if hasattr(self, "theme_label"):
            self.theme_label.setText(self._t("Theme:"))
        if hasattr(self, "language_label"):
            self.language_label.setText(self._t("Language:"))
        self._populate_theme_combo()

        if hasattr(self, "translation_page_title"):
            self.translation_page_title.setText(self._t("Translation Interface"))
        if hasattr(self, "translation_input_card") and hasattr(self.translation_input_card, "setTitle"):
            self.translation_input_card.setTitle("")
        if hasattr(self, "translation_task_card"):
            self.translation_task_card.setTitle(self._t("Translation Task"))
        if hasattr(self, "add_files_button"):
            self.add_files_button.setText(self._t("Add Files"))
        if hasattr(self, "add_folder_button"):
            self.add_folder_button.setText(self._t("Add Folder"))
        if hasattr(self, "clear_list_button"):
            self.clear_list_button.setText(self._t("Clear List"))

        if hasattr(self, "output_folder_label"):
            self.output_folder_label.setText(self._t("Output Directory:"))
        if hasattr(self, "output_folder_input"):
            self.output_folder_input.setPlaceholderText(self._t("Select or drag output folder..."))
        if hasattr(self, "browse_button"):
            self.browse_button.setText(self._t("Browse..."))
        if hasattr(self, "open_button"):
            self.open_button.setText(self._t("Open"))

        if hasattr(self, "workflow_mode_hint_label"):
            self.workflow_mode_hint_label.setText(
                self._t("Choose translation workflow mode before starting the task.")
            )
        if hasattr(self, "workflow_mode_label"):
            self.workflow_mode_label.setText(self._t("Translation Workflow Mode:"))
        current_index = 0
        if hasattr(self, "workflow_mode_combo"):
            current_index = self.workflow_mode_combo.currentIndex()
            self.workflow_mode_combo.blockSignals(True)
            try:
                self.workflow_mode_combo.clear()
                self.workflow_mode_combo.addItems(
                    [
                        self._t("Normal Translation"),
                        self._t("Export Translation"),
                        self._t("Export Original Text"),
                        self._t("Translate JSON Only"),
                        self._t("Import Translation and Render"),
                        self._t("Colorize Only"),
                        self._t("Upscale Only"),
                        self._t("Inpaint Only"),
                        self._t("Replace Translation"),
                    ]
                )
                self.workflow_mode_combo.setCurrentIndex(current_index)
            finally:
                self.workflow_mode_combo.blockSignals(False)
        self._update_workflow_mode_description(current_index)

        self.update_start_button_text()

        if hasattr(self, "export_config_button"):
            self.export_config_button.setText(self._t("Export Config"))
        if hasattr(self, "import_config_button"):
            self.import_config_button.setText(self._t("Import Config"))

        if hasattr(self, "settings_page_title"):
            self.settings_page_title.setText(self._t("Settings Page Title"))
        if hasattr(self, "settings_page_subtitle"):
            self.settings_page_subtitle.setText(self._t("Settings Page Subtitle"))
        if hasattr(self, "settings_desc_header_label"):
            self.settings_desc_header_label.setText(self._t("Settings Desc Header"))
        if hasattr(self, "settings_desc_name"):
            self.settings_desc_name.setText("")
        if hasattr(self, "settings_desc_key"):
            self.settings_desc_key.setText("")
        if hasattr(self, "settings_desc_text"):
            self.settings_desc_text.setText(self._t("Settings Desc Placeholder"))
        self._refresh_dynamic_setting_texts()

        if hasattr(self, "env_page_title_label"):
            self.env_page_title_label.setText(self._t("API Management"))
        if hasattr(self, "env_page_subtitle_label"):
            self.env_page_subtitle_label.setText(
                self._t("Manage API keys and environment variables for each translator")
            )
        if hasattr(self, "env_tab_widget"):
            for route_key, title_key in getattr(self, "env_tab_title_keys", {}).items():
                self.env_tab_widget.setItemText(route_key, self._t(title_key))
        if hasattr(self, "_refresh_api_feature_selectors"):
            self._refresh_api_feature_selectors()

        if hasattr(self, "file_list") and hasattr(self.file_list, "refresh_empty_state_text"):
            self.file_list.refresh_empty_state_text()

        if hasattr(self, "prompt_page_title_label"):
            self.prompt_page_title_label.setText(self._t("Prompt Management"))
        if hasattr(self, "prompt_page_subtitle_label"):
            self.prompt_page_subtitle_label.setText(
                self._t("Manage and apply prompt files for translation")
            )
        if hasattr(self, "prompt_card"):
            self.prompt_card.setTitle(self._t("Prompt List"))
        if hasattr(self, "prompt_refresh_button"):
            self.prompt_refresh_button.setText(self._t("Refresh"))
        if hasattr(self, "prompt_open_dir_button"):
            self.prompt_open_dir_button.setText(self._t("Open Directory"))
        if hasattr(self, "prompt_apply_button"):
            self.prompt_apply_button.setText(self._t("Apply Selected Prompt"))
        if hasattr(self, "prompt_new_button"):
            self.prompt_new_button.setText(self._t("New"))
        if hasattr(self, "prompt_copy_button"):
            self.prompt_copy_button.setText(self._t("Copy"))
        if hasattr(self, "prompt_rename_button"):
            self.prompt_rename_button.setText(self._t("Rename"))
        if hasattr(self, "prompt_delete_button"):
            self.prompt_delete_button.setText(self._t("Delete"))
        if hasattr(self, "prompt_preview_panel") and hasattr(self.prompt_preview_panel, "refresh_ui_texts"):
            self.prompt_preview_panel.refresh_ui_texts()

        if hasattr(self, "replacements_page_title_label"):
            self.replacements_page_title_label.setText(self._t("Replacement Rules"))
        if hasattr(self, "replacements_page_subtitle_label"):
            self.replacements_page_subtitle_label.setText(
                self._t("Manage text replacement rules applied to translations before rendering")
            )
        if hasattr(self, "replacements_editor_panel"):
            self.replacements_editor_panel.refresh_ui_texts()

        if hasattr(self, "rich_text_rules_page_title_label"):
            self.rich_text_rules_page_title_label.setText(self._t("Rich Text Rules"))
        if hasattr(self, "rich_text_rules_page_subtitle_label"):
            self.rich_text_rules_page_subtitle_label.setText(
                self._t("Automatically style text matched after replacement rules are applied")
            )
        if hasattr(self, "rich_text_rules_editor_panel"):
            self.rich_text_rules_editor_panel.refresh_ui_texts()

    def _clear_dynamic_settings(self):
        """清理所有动态创建的设置控件。"""
        self._settings_ui_ready = False
        self._settings_rendered_signature = None
        self._settings_pending_signature = None
        self._env_api_groups_signature = None

        if hasattr(self, "env_group_container_layout"):
            main_view_dynamic._clear_layout_widgets(self.env_group_container_layout)

        for panel in getattr(self, "tab_frames", {}).values():
            if panel and panel.layout():
                main_view_dynamic._clear_layout_widgets(panel.layout(), restore_stretch=True)
        main_view_dynamic._drop_cached_settings_widget_refs(self)

    @pyqtSlot(bool)
    def on_translation_state_changed(self, is_translating: bool):
        main_view_runtime.on_translation_state_changed(self, is_translating)
