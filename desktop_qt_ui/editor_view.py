
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from editor.editor_controller import EditorController
from editor.editor_logic import EditorLogic
from editor.editor_model import EditorModel
from editor.graphics_view import GraphicsView
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

        # 设置controller的view引用，用于更新UI状态
        self.controller.set_view(self)

        # 主布局变为垂直，以容纳顶栏
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 1. 顶部工具栏
        self.toolbar = EditorToolbar(self)
        self.toolbar.setFixedHeight(40)
        self.layout.addWidget(self.toolbar)

        # 2. 主内容分割器
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.layout.addWidget(main_splitter)

        # --- 左侧面板 (标签页) ---
        left_panel = self._create_left_panel()

        # --- 中心画布 ---
        self.graphics_view = GraphicsView(self.model, self)

        # --- 右侧面板 (文件列表) ---
        right_panel = self._create_right_panel()

        # --- 组合布局 ---
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(self.graphics_view)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(1, 1)  # 让中心画布拉伸
        main_splitter.setSizes([345, 800, 250])  # 左侧面板345px，适应属性面板内容

        # --- 连接信号与槽 ---
        self._connect_signals()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """设置编辑器快捷键"""
        # 撤销快捷键
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self._handle_undo_shortcut)

        # 重做快捷键
        self.redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.redo_shortcut.activated.connect(self._handle_redo_shortcut)

        # 复制快捷键
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        self.copy_shortcut.activated.connect(self._handle_copy_shortcut)

        # 粘贴快捷键
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        self.paste_shortcut.activated.connect(self._handle_paste_shortcut)

        # 删除快捷键
        self.delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self)
        self.delete_shortcut.activated.connect(self._handle_delete_shortcut)

    def force_save_property_panel_edits(self):
        """强制保存property panel中的文本编辑"""
        self.property_panel.force_save_text_edits()
    
    def _handle_undo_shortcut(self):
        # Find the currently focused widget
        """处理撤销快捷键"""
        # 检查焦点是否在文本控件上
        focused_widget = self.focusWidget()
        if isinstance(focused_widget, (QTextEdit, QLineEdit)):
            # 如果焦点在文本控件上，让文本控件处理撤销
            focused_widget.undo()
        else:
            # 否则调用编辑器的撤销
            self.controller.undo()

    def _handle_redo_shortcut(self):
        """处理重做快捷键"""
        # 检查焦点是否在文本控件上
        focused_widget = self.focusWidget()
        if isinstance(focused_widget, (QTextEdit, QLineEdit)):
            # 如果焦点在文本控件上，让文本控件处理重做
            focused_widget.redo()
        else:
            # 否则调用编辑器的重做
            self.controller.redo()

    def _handle_copy_shortcut(self):
        """处理复制快捷键"""
        focused_widget = self.focusWidget()
        if isinstance(focused_widget, (QTextEdit, QLineEdit)):
            # 如果焦点在文本控件上，让文本控件处理复制
            focused_widget.copy()
        else:
            # 否则复制选中的区域
            selected_regions = self.model.get_selection()
            if selected_regions:
                # 复制最后选中的区域
                self.controller.copy_region(selected_regions[-1])

    def _handle_paste_shortcut(self):
        """处理粘贴快捷键"""
        focused_widget = self.focusWidget()
        if isinstance(focused_widget, (QTextEdit, QLineEdit)):
            # 如果焦点在文本控件上，让文本控件处理粘贴
            focused_widget.paste()
        else:
            # 否则根据是否有选中区域决定粘贴行为
            selected_regions = self.model.get_selection()
            if selected_regions and len(selected_regions) == 1:
                # 有单个选中区域时，粘贴样式
                self.controller.paste_region_style(selected_regions[0])
            else:
                # 无选中区域时，粘贴新区域到鼠标位置
                # 获取鼠标在图像中的位置
                from PyQt6.QtGui import QCursor
                if self.graphics_view and self.graphics_view._image_item:
                    mouse_pos_scene = self.graphics_view.mapToScene(self.graphics_view.mapFromGlobal(QCursor.pos()))
                    mouse_pos_image = self.graphics_view._image_item.mapFromScene(mouse_pos_scene)
                    self.controller.paste_region(mouse_pos_image)
                else:
                    self.controller.paste_region()

    def _handle_delete_shortcut(self):
        """处理删除快捷键"""
        focused_widget = self.focusWidget()
        if not isinstance(focused_widget, (QTextEdit, QLineEdit)):
            # 只有在非文本控件上才处理删除区域
            selected_regions = self.model.get_selection()
            if selected_regions:
                self.controller.delete_regions(selected_regions)
    
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
        left_tab_widget = QTabWidget()
        
        # 创建“可编辑译文”标签页
        translation_widget = QWidget()
        translation_layout = QVBoxLayout(translation_widget)
        translation_layout.setContentsMargins(0, 0, 0, 0)

        # --- 查找和替换 ---
        replace_widget = QWidget()
        replace_layout = QHBoxLayout(replace_widget)
        replace_layout.setContentsMargins(5, 5, 5, 5)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("查找")
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("替换为")
        self.replace_all_button = QPushButton("全部替换")
        replace_layout.addWidget(self.find_input)
        replace_layout.addWidget(self.replace_input)
        replace_layout.addWidget(self.replace_all_button)
        
        self.apply_translations_button = QPushButton("应用所有译文修改")
        self.region_list_view = RegionListView(self)
        
        translation_layout.addWidget(replace_widget)
        translation_layout.addWidget(self.apply_translations_button)
        translation_layout.addWidget(self.region_list_view)

        self.property_panel = PropertyPanel(self.model, self.app_logic, self)

        left_tab_widget.addTab(translation_widget, "可编辑译文")
        left_tab_widget.addTab(self.property_panel, "属性编辑")

        # 设置默认显示"属性编辑"标签页
        left_tab_widget.setCurrentIndex(1)

        return left_tab_widget

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

    def _connect_signals(self):
        # --- Model to View ---
        self.model.regions_changed.connect(self.region_list_view.update_regions)
        self.model.selection_changed.connect(self.region_list_view.update_selection)
        # Connect model selection changes to the property panel
        self.model.selection_changed.connect(self.property_panel.on_selection_changed)

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
        self.logic.file_list_changed.connect(self.update_file_list)

        # --- Toolbar (Top) to Controller/View ---
        self.toolbar.back_requested.connect(self.back_to_main_requested)
        self.toolbar.export_requested.connect(self.controller.export_image)
        self.toolbar.edit_file_requested.connect(self.controller.edit_source_file)
        self.toolbar.undo_requested.connect(self.controller.undo)
        self.toolbar.redo_requested.connect(self.controller.redo)
        self.toolbar.edit_geometry_requested.connect(self.controller.set_geometry_edit_mode)
        self.toolbar.zoom_in_requested.connect(self.graphics_view.zoom_in)
        self.toolbar.zoom_out_requested.connect(self.graphics_view.zoom_out)
        self.toolbar.fit_window_requested.connect(self.graphics_view.fit_to_window)
        self.toolbar.display_mode_changed.connect(self.controller.set_display_mode)
        self.toolbar.original_image_alpha_changed.connect(self.controller.set_original_image_alpha)
        self.toolbar.render_inpaint_requested.connect(self.controller.render_inpaint)

        # --- Model to Toolbar (同步滑块) ---
        self.model.original_image_alpha_changed.connect(self.toolbar.set_original_image_alpha_slider)

        # --- Graphics View to Controller ---
        self.graphics_view.region_geometry_changed.connect(self.controller.update_region_geometry)
        self.graphics_view.geometry_added.connect(self.controller.add_geometry_to_region)

        # --- Property Panel (Left Panel) to Controller ---
        self.property_panel.translated_text_modified.connect(self.controller.update_translated_text)
        self.property_panel.original_text_modified.connect(self.controller.update_original_text)
        self.property_panel.ocr_requested.connect(self.controller.run_ocr_for_selection)
        self.property_panel.translation_requested.connect(self.controller.run_translation_for_selection)
        self.property_panel.font_size_changed.connect(self.controller.update_font_size)
        self.property_panel.font_color_changed.connect(self.controller.update_font_color)
        self.property_panel.font_family_changed.connect(self.controller.update_font_family)
        self.property_panel.alignment_changed.connect(self.controller.update_alignment)
        self.property_panel.direction_changed.connect(self.controller.update_direction)
        self.property_panel.mask_config_changed.connect(self.controller.update_mask_config)
        self.property_panel.update_mask_requested.connect(self.controller.render_inpaint)
        self.property_panel.toggle_mask_visibility.connect(lambda state: self.controller.set_display_mask_type('refined', state))
        self.property_panel.toggle_removed_mask_visibility.connect(self.controller.set_removed_mask_visible)
        self.property_panel.copy_region_requested.connect(self._handle_copy_from_panel)
        self.property_panel.paste_region_requested.connect(self._handle_paste_from_panel)
        self.property_panel.delete_region_requested.connect(self._handle_delete_from_panel)
        # --- Connect Mask Editing Tools ---
        self.property_panel.mask_tool_changed.connect(self.controller.set_active_tool)
        self.property_panel.brush_size_changed.connect(self.controller.set_brush_size)

        # Note: Some signals from PropertyPanel might not have corresponding slots in the controller yet.
        # e.g., copy/paste/delete, mask tool changes.

        # --- Global App Logic to Controller ---
        self.app_logic.render_setting_changed.connect(self.controller.handle_global_render_setting_change)

    def _create_right_panel(self) -> QWidget:
        """创建右侧的文件列表面板"""
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # 文件操作按钮
        file_button_widget = QWidget()
        file_buttons_layout = QHBoxLayout(file_button_widget)
        file_buttons_layout.setContentsMargins(0,0,0,0)
        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.clear_list_button = QPushButton("清空列表")
        file_buttons_layout.addWidget(self.add_files_button)
        file_buttons_layout.addWidget(self.add_folder_button)
        file_buttons_layout.addWidget(self.clear_list_button)
        right_layout.addWidget(file_button_widget)

        # 文件列表
        self.file_list = FileListView(None, self)
        right_layout.addWidget(self.file_list)
        
        return right_panel

    @pyqtSlot(str)
    def _on_file_remove_requested(self, file_path: str):
        """处理文件移除请求：先在视图中移除，再更新逻辑层"""
        # 先在视图中移除文件（不会触发重新构建）
        self.file_list.remove_file(file_path)
        # 然后更新逻辑层的数据（不发射信号）
        self.logic.remove_file(file_path, emit_signal=False)
    
    @pyqtSlot(list)
    def update_file_list(self, files: list):
        """Clears and repopulates the file list view based on a signal from the logic."""
        self.file_list.clear()
        self.file_list.add_files(files)
