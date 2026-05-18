
from typing import Any

from editor.editor_controller import EditorController
from editor.editor_logic import EditorLogic
from editor.editor_model import EditorModel
from editor.graphics_view import GraphicsView
from editor.original_compare_view import OriginalCompareView
from main_view_parts.theme import get_current_theme
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from services import get_i18n_manager
from utils.shortcut_manager import EditorShortcutManager
from widgets.editor_toolbar import EditorToolbar
from widgets.file_list_view import FileListView
from widgets.property_panel import PropertyPanel
from widgets.region_list_view import RegionListView


class EditorView(QWidget):
    """
    编辑器主视图，包含文件列表、画布和属性面板。
    """
    # --- 定义信号 ---
    back_to_main_requested = pyqtSignal()
    
    def __init__(self, app_logic: Any, model: EditorModel, controller: EditorController, logic: EditorLogic, parent=None):
        super().__init__(parent)
        self.app_logic = app_logic
        self.model = model
        self.controller = controller
        self.logic = logic
        self.i18n = get_i18n_manager()
        self._compare_mode_enabled = False
        self.toolbar: EditorToolbar | None = None
        self.main_splitter: QSplitter | None = None
        self.left_tab_widget: QTabWidget | None = None
        self.find_input: QLineEdit | None = None
        self.replace_input: QLineEdit | None = None
        self.replace_all_button: QPushButton | None = None
        self.apply_translations_button: QPushButton | None = None
        self.region_list_view: RegionListView | None = None
        self.property_panel: PropertyPanel | None = None
        self.compare_preview_container: QWidget | None = None
        self.original_compare_view: OriginalCompareView | None = None
        self.edit_canvas_container: QWidget | None = None
        self.graphics_view: GraphicsView | None = None
        self.add_files_button: QPushButton | None = None
        self.add_folder_button: QPushButton | None = None
        self.clear_list_button: QPushButton | None = None
        self.file_list: FileListView | None = None

        # 设置controller的view引用，用于更新UI状态
        self.controller.set_view(self)

        self.setObjectName("editor_view_root")

        # 主布局变为垂直，以容纳顶栏
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 1. 顶部工具栏
        self.toolbar = EditorToolbar(self)
        self.toolbar.setObjectName("editor_toolbar")
        self.toolbar.setFixedHeight(40)
        self.layout.addWidget(self.toolbar)

        # 2. 主内容分割器
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.setObjectName("editor_main_splitter")
        main_splitter.setHandleWidth(6)
        self.main_splitter = main_splitter
        self.layout.addWidget(main_splitter)

        # --- 左侧面板 (标签页) ---
        left_panel = self._create_left_panel()

        # --- 中心画布区域（包含画布和缩放滑块） ---
        center_panel = self._create_center_panel()

        # --- 右侧面板 (文件列表) ---
        right_panel = self._create_right_panel()

        # --- 组合布局 ---
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)  # 让中心画布拉伸
        main_splitter.setStretchFactor(2, 0)

        # --- 连接信号与槽 ---
        self._connect_signals()
        
        # --- 设置快捷键管理器 ---
        self.shortcut_manager = EditorShortcutManager(self)

        # --- 应用编辑器样式（与主页统一） ---
        self._apply_editor_style()
        self._apply_initial_splitter_sizes()
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def force_save_property_panel_edits(self):
        """强制保存property panel中的文本编辑"""
        self.property_panel.force_save_text_edits()
    
    def _handle_copy_from_panel(self):
        """处理属性面板的复制按钮"""
        selected_regions = self.model.get_selection()
        if selected_regions:
            self.controller.copy_region(selected_regions[0])
    
    def _handle_paste_from_panel(self):
        """处理属性面板的粘贴按钮"""
        selected_regions = self.model.get_selection()
        if selected_regions and len(selected_regions) == 1:
            # 有单个选中区域时，粘贴样式
            self.controller.paste_region_style(selected_regions[0])
        else:
            # 无选中区域时，粘贴新区域
            self.controller.paste_region()
    
    def _handle_delete_from_panel(self):
        """处理属性面板的删除按钮"""
        selected_regions = self.model.get_selection()
        if selected_regions:
            self.controller.delete_regions(selected_regions)

    def _create_left_panel(self) -> QWidget:
        """创建左侧的标签页，包含区域列表和属性面板"""
        self.left_tab_widget = QTabWidget()
        self.left_tab_widget.setObjectName("editor_left_tabs")
        self.left_tab_widget.setMinimumWidth(292)
        self.left_tab_widget.setMaximumWidth(360)
        
        # 创建“可编辑译文”标签页
        translation_widget = QWidget()
        translation_widget.setObjectName("editor_translation_page")
        translation_layout = QVBoxLayout(translation_widget)
        translation_layout.setContentsMargins(0, 0, 0, 0)

        # --- 查找和替换 ---
        replace_widget = QWidget()
        replace_widget.setObjectName("editor_search_bar")
        replace_layout = QHBoxLayout(replace_widget)
        replace_layout.setContentsMargins(5, 5, 5, 5)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText(self._t("Find"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText(self._t("Replace with"))
        self.replace_all_button = QPushButton(self._t("Replace All"))
        self.replace_all_button.setProperty("chipButton", True)
        replace_layout.addWidget(self.find_input)
        replace_layout.addWidget(self.replace_input)
        replace_layout.addWidget(self.replace_all_button)
        
        self.apply_translations_button = QPushButton(self._t("Apply All Translation Changes"))
        self.apply_translations_button.setObjectName("editor_apply_button")
        self.region_list_view = RegionListView(self)
        self.region_list_view.setObjectName("editor_region_list")
        
        translation_layout.addWidget(replace_widget)
        translation_layout.addWidget(self.apply_translations_button)
        translation_layout.addWidget(self.region_list_view)

        self.property_panel = PropertyPanel(self.model, self.app_logic, self)
        self.property_panel.setObjectName("editor_property_panel")

        self.left_tab_widget.addTab(translation_widget, self._t("Editable Translation"))
        self.left_tab_widget.addTab(self.property_panel, self._t("Property Editor"))

        # 设置默认显示"属性编辑"标签页
        self.left_tab_widget.setCurrentIndex(1)

        return self.left_tab_widget

    def refresh_tab_titles(self):
        """刷新标签页标题（用于语言切换）"""
        if self.left_tab_widget is None:
            return

        self.left_tab_widget.setTabText(0, self._t("Editable Translation"))
        self.left_tab_widget.setTabText(1, self._t("Property Editor"))
    
    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）"""
        # 刷新标签页标题
        self.refresh_tab_titles()
        
        # 刷新查找替换按钮
        if self.find_input is not None:
            self.find_input.setPlaceholderText(self._t("Find"))
        if self.replace_input is not None:
            self.replace_input.setPlaceholderText(self._t("Replace with"))
        if self.replace_all_button is not None:
            self.replace_all_button.setText(self._t("Replace All"))
        if self.apply_translations_button is not None:
            self.apply_translations_button.setText(self._t("Apply All Translation Changes"))
        
        # 刷新工具栏
        if self.toolbar is not None:
            self.toolbar.refresh_ui_texts()
        
        # 刷新属性面板
        if self.property_panel is not None:
            self.property_panel.refresh_ui_texts()
        
        # 刷新右侧文件列表按钮
        if self.add_files_button is not None:
            self.add_files_button.setText(self._t("Add Files"))
        if self.add_folder_button is not None:
            self.add_folder_button.setText(self._t("Add Folder"))
        if self.clear_list_button is not None:
            self.clear_list_button.setText(self._t("Clear List"))
        
        # 文件项文本不需要重建，语言切换时只需重绘空列表占位提示。
        if self.file_list is not None:
            self.file_list.refresh_empty_state_text()

    def _apply_initial_splitter_sizes(self):
        """用左栏的实际 sizeHint 作为初始宽度，而不是写死常量。"""
        if self.main_splitter is None or self.left_tab_widget is None:
            return

        self.left_tab_widget.ensurePolished()
        left_width = self.left_tab_widget.sizeHint().width()
        left_width = max(self.left_tab_widget.minimumWidth(), left_width)
        left_width = min(self.left_tab_widget.maximumWidth(), left_width)

        self.main_splitter.setSizes([left_width, 860, 236])
    
    def _on_apply_changes_clicked(self):
        """应用所有在列表中修改的译文"""
        translations = self.region_list_view.get_all_translations()
        self.controller.update_multiple_translations(translations)

    def _on_replace_all_clicked(self):
        """在所有译文中执行查找和替换"""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not find_text:
            return

        self.region_list_view.find_and_replace_in_all_translations(find_text, replace_text)

    def _on_align_requested(self, mode: str):
        """处理对齐按钮点击。"""
        reference = self.toolbar.get_align_reference()
        self.controller.align_regions(mode, reference)

    def _on_distribute_requested(self, mode: str):
        """处理分布按钮点击。"""
        self.controller.distribute_regions(mode)

    def _on_selection_changed_for_toolbar(self, selected_indices: list):
        """根据选区数量更新对齐/分布按钮的启用状态。"""
        count = len(selected_indices) if selected_indices else 0
        self.toolbar.update_align_distribute_buttons(count)

    def _connect_signals(self):
        # --- Model to View ---
        self.model.regions_changed.connect(self.region_list_view.update_regions)
        self.model.selection_changed.connect(self.region_list_view.update_selection)
        # Connect model selection changes to the property panel
        self.model.selection_changed.connect(self.property_panel.on_selection_changed)
        # Connect model selection changes to toolbar align/distribute button states
        self.model.selection_changed.connect(self._on_selection_changed_for_toolbar)
        # Connect model brush size changes to the property panel
        self.model.brush_size_changed.connect(self.property_panel.sync_brush_size_from_model)
        # Connect model brush color changes to the property panel
        self.model.brush_color_changed.connect(self.property_panel.sync_brush_color_from_model)
        # Keep tool buttons in sync with model
        self.model.active_tool_changed.connect(self.property_panel.sync_active_tool_from_model)
        self.model.compare_image_changed.connect(self._on_compare_image_changed)

        # --- View to Controller ---
        self.region_list_view.region_selected.connect(self.controller.set_selection_from_list)
        self.apply_translations_button.clicked.connect(self._on_apply_changes_clicked)
        self.replace_all_button.clicked.connect(self._on_replace_all_clicked)

        # --- File List (Right Panel) to Logic ---
        self.add_files_button.clicked.connect(self.logic.open_and_add_files)
        self.add_folder_button.clicked.connect(self.logic.open_and_add_folder)
        self.clear_list_button.clicked.connect(self.logic.clear_list)
        self.file_list.file_remove_requested.connect(self._on_file_remove_requested)
        self.file_list.file_selected.connect(self.logic.load_image_into_editor)
        self.file_list.files_dropped.connect(self.logic.add_files_from_paths)  # 拖放文件支持
        self.logic.file_list_changed.connect(self.update_file_list)
        self.logic.file_list_with_tree_changed.connect(self.update_file_list_with_tree)  # 支持树形结构

        # --- Toolbar (Top) to Controller/View ---
        self.toolbar.back_requested.connect(self.back_to_main_requested)
        self.toolbar.export_requested.connect(self.controller.export_image)
        self.toolbar.undo_requested.connect(self.controller.undo)
        self.toolbar.redo_requested.connect(self.controller.redo)
        self.toolbar.zoom_in_requested.connect(self.graphics_view.zoom_in)
        self.toolbar.zoom_out_requested.connect(self.graphics_view.zoom_out)
        self.toolbar.fit_window_requested.connect(self.graphics_view.fit_to_window)
        self.toolbar.display_mode_changed.connect(self.controller.set_display_mode)
        self.toolbar.original_image_alpha_changed.connect(self.controller.set_original_image_alpha)
        self.toolbar.align_requested.connect(self._on_align_requested)
        self.toolbar.distribute_requested.connect(self._on_distribute_requested)

        # --- Model to Toolbar (同步滑块) ---
        self.model.original_image_alpha_changed.connect(self.toolbar.set_original_image_alpha_slider)

        # --- Graphics View to Controller ---
        self.graphics_view.region_geometry_changed.connect(self.controller.update_region_geometry)
        self.graphics_view.view_state_changed.connect(self.original_compare_view.sync_view_state)

        # --- Property Panel (Left Panel) to Controller ---
        self.property_panel.translated_text_modified.connect(self.controller.update_translated_text)
        self.property_panel.original_text_modified.connect(self.controller.update_original_text)
        self.property_panel.ocr_requested.connect(self.controller.run_ocr_for_selection)
        self.property_panel.translation_requested.connect(self.controller.run_translation_for_selection)
        self.property_panel.font_size_changed.connect(self.controller.update_font_size)
        self.property_panel.font_color_changed.connect(self.controller.update_font_color)
        self.property_panel.stroke_color_changed.connect(self.controller.update_stroke_color)
        self.property_panel.stroke_width_changed.connect(self.controller.update_stroke_width)
        self.property_panel.line_spacing_changed.connect(self.controller.update_line_spacing)
        self.property_panel.letter_spacing_changed.connect(self.controller.update_letter_spacing)
        self.property_panel.angle_changed.connect(self.controller.update_angle)
        self.property_panel.font_family_changed.connect(self.controller.update_font_family)
        self.property_panel.alignment_changed.connect(self.controller.update_alignment)
        self.property_panel.direction_changed.connect(self.controller.update_direction)
        self.property_panel.toggle_mask_visibility.connect(lambda state: self.controller.set_display_mask_type('refined', state))
        self.property_panel.copy_region_requested.connect(self._handle_copy_from_panel)
        self.property_panel.paste_region_requested.connect(self._handle_paste_from_panel)
        self.property_panel.delete_region_requested.connect(self._handle_delete_from_panel)
        self.property_panel.clear_all_masks_requested.connect(self.controller.clear_all_masks)
        # --- Connect Mask Editing Tools ---
        self.property_panel.mask_tool_changed.connect(self.controller.set_active_tool)
        self.property_panel.brush_size_changed.connect(self.controller.set_brush_size)
        # --- Connect Paint Overlay Tools ---
        self.property_panel.brush_color_changed.connect(self.controller.set_brush_color)
        self.property_panel.clear_paint_overlay_requested.connect(self.controller.clear_paint_overlay)

        # Note: Some signals from PropertyPanel might not have corresponding slots in the controller yet.
        # e.g., copy/paste/delete, mask tool changes.

        # --- Global App Logic to Controller ---
        self.app_logic.render_setting_changed.connect(self.controller.handle_global_render_setting_change)

    def _create_center_panel(self) -> QWidget:
        """创建中心画布区域"""
        center_widget = QWidget()
        center_widget.setObjectName("editor_center_panel")
        center_layout = QHBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(6)

        self.compare_preview_container = QWidget()
        self.compare_preview_container.setObjectName("editor_compare_preview_container")
        compare_layout = QVBoxLayout(self.compare_preview_container)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(0)
        self.original_compare_view = OriginalCompareView(parent=self)
        self.original_compare_view.setObjectName("editor_original_compare_view")
        compare_layout.addWidget(self.original_compare_view)
        self.compare_preview_container.hide()

        self.edit_canvas_container = QWidget()
        self.edit_canvas_container.setObjectName("editor_edit_canvas_container")
        edit_canvas_layout = QVBoxLayout(self.edit_canvas_container)
        edit_canvas_layout.setContentsMargins(0, 0, 0, 0)
        edit_canvas_layout.setSpacing(0)

        # 画布（滚动条已在 GraphicsView 中配置）
        self.graphics_view = GraphicsView(self.model, controller=self.controller, parent=self)
        self.graphics_view.setObjectName("editor_graphics_view")
        self.original_compare_view.set_source_view(self.graphics_view)
        edit_canvas_layout.addWidget(self.graphics_view)

        center_layout.addWidget(self.compare_preview_container, 1)
        center_layout.addWidget(self.edit_canvas_container, 1)

        return center_widget

    def _create_right_panel(self) -> QWidget:
        """创建右侧的文件列表面板"""
        right_panel = QWidget()
        right_panel.setObjectName("editor_right_panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)

        # 文件操作按钮
        file_button_widget = QWidget()
        file_button_widget.setObjectName("editor_file_actions")
        file_buttons_layout = QHBoxLayout(file_button_widget)
        file_buttons_layout.setContentsMargins(0,0,0,0)
        self.add_files_button = QPushButton(self._t("Add Files"))
        self.add_folder_button = QPushButton(self._t("Add Folder"))
        self.clear_list_button = QPushButton(self._t("Clear List"))
        self.add_files_button.setProperty("chipButton", True)
        self.add_folder_button.setProperty("chipButton", True)
        self.clear_list_button.setProperty("chipButton", True)
        file_buttons_layout.addWidget(self.add_files_button)
        file_buttons_layout.addWidget(self.add_folder_button)
        file_buttons_layout.addWidget(self.clear_list_button)
        right_layout.addWidget(file_button_widget)

        # 文件列表
        self.file_list = FileListView(None, self)
        self.file_list.setObjectName("editor_file_list")
        right_layout.addWidget(self.file_list)
        
        return right_panel

    @pyqtSlot(str)
    def _on_file_remove_requested(self, file_path: str):
        """处理文件移除请求：只处理编辑器自己的文件列表"""
        # 先在视图中移除（避免重建列表）
        self.file_list.remove_file(file_path)
        
        # 调用 editor_logic 移除文件（会检查是否需要清空画布）
        self.logic.remove_file(file_path, emit_signal=False)
        
        # 编辑器有自己独立的文件列表，不需要同步到主页的 app_logic
    
    @pyqtSlot(list)
    def update_file_list(self, files: list):
        """Clears and repopulates the file list view based on a signal from the logic."""
        self.file_list.clear()
        self.file_list.add_files(files)
    
    @pyqtSlot(list, dict)
    def update_file_list_with_tree(self, files: list, folder_tree: dict):
        """使用树形结构更新文件列表"""
        self.file_list.clear()
        self.file_list.add_files_from_tree(folder_tree)

    def _apply_editor_style(self, theme: str | None = None):
        """编辑器局部样式：根据主题应用配色，与主页风格统一。"""
        from main_view_parts.style_generator import generate_editor_style
        from main_view_parts.theme import apply_widget_stylesheet
        from widgets.color_picker import ColorPickerWidget

        theme = theme or get_current_theme()
        apply_widget_stylesheet(self, generate_editor_style(theme))
        if self.graphics_view is not None:
            self.graphics_view.apply_theme(theme)
        if self.original_compare_view is not None:
            self.original_compare_view.apply_theme(theme)
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()

    @pyqtSlot(object)
    def _on_compare_image_changed(self, image):
        if self.original_compare_view is None:
            return

        self.original_compare_view.set_image(image)
        if self._compare_mode_enabled:
            self._sync_compare_view_from_main()

    def _sync_compare_view_from_main(self):
        if self.graphics_view is None or self.original_compare_view is None:
            return
        transform, center_scene = self.graphics_view.get_view_state()
        if transform is None or center_scene is None:
            return
        self.original_compare_view.sync_view_state(transform, center_scene)

    def set_compare_mode(self, enabled: bool):
        self._compare_mode_enabled = bool(enabled)
        if self.compare_preview_container is not None:
            self.compare_preview_container.setVisible(self._compare_mode_enabled)
        if self._compare_mode_enabled:
            self._sync_compare_view_from_main()
