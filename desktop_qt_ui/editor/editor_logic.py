import json
import os
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from services import get_config_service
from widgets.folder_dialog import select_folders


class EditorLogic(QObject):
    """
    Handles the business logic for the editor view, including file list management.
    """
    file_list_changed = pyqtSignal(list)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.source_files: List[str] = []
        self.translated_files: List[str] = []
        self.translation_map_cache = {}
        self.config_service = get_config_service()

    # --- File Management Methods ---

    @pyqtSlot()
    def open_and_add_files(self):
        """Opens a file dialog to add files to the editor's list."""
        last_dir = self.config_service.get_config().app.last_open_dir
        file_paths, _ = QFileDialog.getOpenFileNames(
            None, 
            "添加文件到编辑器", 
            last_dir, 
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if file_paths:
            self.add_files(file_paths)
            os.path.dirname(file_paths[0])
            # TODO: Find a way to save last_open_dir back to config service

    @pyqtSlot()
    def open_and_add_folder(self):
        """Opens a dialog to select folders (supports multiple selection) and adds all containing images to the list."""
        last_dir = self.config_service.get_config().app.last_open_dir

        # 使用自定义的现代化文件夹选择器
        folders = select_folders(
            parent=None,
            start_dir=last_dir,
            multi_select=True,
            config_service=self.config_service
        )

        if folders:
            # 扫描文件夹，添加所有图片文件路径
            for folder_path in folders:
                self.add_folder(folder_path)

    def add_files(self, files: List[str]):
        if not files:
            return
        new_files = [f for f in files if f not in self.source_files]
        if new_files:
            # 检查是否是第一次添加文件（列表为空）
            is_first_add = len(self.source_files) == 0

            self.source_files.extend(new_files)
            self.file_list_changed.emit(self.source_files)

            # 如果是第一次添加文件，自动加载第一个
            if is_first_add and len(new_files) > 0:
                self.load_image_into_editor(new_files[0])

    def add_folder(self, folder_path: str):
        if not folder_path or not os.path.isdir(folder_path):
            return
        
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
        try:
            files_in_folder = [
                os.path.join(folder_path, f) 
                for f in os.listdir(folder_path) 
                if os.path.splitext(f)[1].lower() in image_extensions
            ]
            self.add_files(files_in_folder)
        except OSError as e:
            print(f"Error reading folder {folder_path}: {e}")

    @pyqtSlot(str)
    def remove_file(self, file_path: str, emit_signal: bool = False):
        """
        移除文件（可能是源文件或翻译后的文件）
        
        Args:
            file_path: 要移除的文件路径
            emit_signal: 是否发射 file_list_changed 信号（默认 False，由视图自己处理）
        """
        removed = False
        norm_file = os.path.normpath(file_path) if file_path else None
        
        # 查找文件对（源文件和翻译文件）
        source_path, translated_path = self._find_file_pair(file_path)
        
        # 规范化路径进行比较
        norm_source = os.path.normpath(source_path) if source_path else None
        norm_translated = os.path.normpath(translated_path) if translated_path else None
        
        # 移除源文件（使用规范化路径）
        for sf in self.source_files[:]:  # 复制列表以避免修改迭代
            if os.path.normpath(sf) == norm_source:
                self.source_files.remove(sf)
                removed = True
                break

        # 移除翻译文件（使用规范化路径）
        for tf in self.translated_files[:]:
            if os.path.normpath(tf) == norm_translated:
                self.translated_files.remove(tf)
                removed = True
                break

        # 如果没有找到文件对，尝试直接移除（使用规范化路径）
        if not removed:
            for sf in self.source_files[:]:
                if os.path.normpath(sf) == norm_file:
                    self.source_files.remove(sf)
                    removed = True
                    break
            
            for tf in self.translated_files[:]:
                if os.path.normpath(tf) == norm_file:
                    self.translated_files.remove(tf)
                    removed = True
                    break

        if removed:
            # 只有在明确要求时才发射信号（用于清空列表等操作）
            if emit_signal:
                self.file_list_changed.emit(self.translated_files if self.translated_files else self.source_files)

            # 检查当前加载的图片是否是被移除的文件
            current_image_path = self.controller.model.get_source_image_path()
            if current_image_path:
                # 规范化所有路径以确保比较准确
                norm_current = os.path.normpath(current_image_path)
                
                # 如果当前图片是被移除的文件（可能是源文件或翻译文件）
                if norm_current == norm_file or norm_current == norm_source or norm_current == norm_translated:
                    # 先清空画布图片，再清空编辑器状态
                    self.controller.model.set_image(None)
                    self.controller._clear_editor_state()

    @pyqtSlot()
    def clear_list(self):
        self.source_files.clear()
        self.translated_files.clear()
        # 清空列表时发射空列表
        self.file_list_changed.emit([])
        
        # 先清空画布图片，这样后台任务会检测到图片为None而提前返回
        self.controller.model.set_image(None)
        # 然后清空编辑器状态（包括取消后台任务）
        self.controller._clear_editor_state()

    # --- Image Loading Methods ---

    def load_file_lists(self, source_files: List[str], translated_files: List[str], folder_map: dict = None):
        """
        Receives the file lists from the coordinator to populate the editor.
        folder_map: 文件到文件夹的映射，用于支持文件夹分组显示
        """
        self.source_files = source_files
        self.translated_files = translated_files
        self.translation_map_cache.clear() # Clear cache when lists change
        
        # 如果提供了folder_map，将文件按文件夹分组
        files_to_show = self.translated_files if self.translated_files else self.source_files
        
        if folder_map:
            # 按文件夹分组
            folder_groups = {}
            single_files = []
            
            for file_path in files_to_show:
                folder = folder_map.get(file_path)
                if folder:
                    if folder not in folder_groups:
                        folder_groups[folder] = []
                    folder_groups[folder].append(file_path)
                else:
                    single_files.append(file_path)
            
            # 构建文件列表：先是文件夹，再是单独文件
            grouped_list = []
            for folder, files in sorted(folder_groups.items()):
                grouped_list.append(folder)  # 添加文件夹路径
                # 注意：FileListView 会自动展开文件夹
            grouped_list.extend(single_files)  # 添加单独的文件
            
            self.file_list_changed.emit(grouped_list)
        else:
            # 发射翻译后的文件列表，而不是源文件列表
            self.file_list_changed.emit(files_to_show)

    @pyqtSlot(str)
    def load_image_into_editor(self, file_path: str):
        """
        Loads a specific image into the editor view by finding its pair and calling the controller.
        如果是翻译后的图片，直接加载翻译后的图片（查看器模式）
        如果是源文件，加载源文件（编辑模式）
        """
        source_path, translated_path = self._find_file_pair(file_path)

        # 如果传入的是翻译后的文件（translated_path == file_path），直接加载翻译后的文件
        if translated_path and os.path.normpath(file_path) == os.path.normpath(translated_path):
            self.controller.load_image_and_regions(translated_path)
        elif source_path:
            self.controller.load_image_and_regions(source_path)
        else:
            # Fallback for safety
            self.controller.load_image_and_regions(file_path)

    def _find_file_pair(self, file_path: str) -> (str, Optional[str]):
        """Given a file path, find its source/translated pair using _source_path.txt."""
        norm_path = os.path.normpath(file_path)

        # Case 1: The given file is a translated file (has _source_path.txt)
        try:
            output_dir = os.path.dirname(norm_path)
            source_path_file = os.path.join(output_dir, '_source_path.txt')
            if os.path.exists(source_path_file):
                with open(source_path_file, 'r', encoding='utf-8') as f:
                    source = f.read().strip()
                if source and os.path.exists(source):
                    return source, file_path
        except Exception: pass
        
        # Case 2: The given file is a source file (search for translated files that reference it)
        try:
            for trans_file in self.translated_files:
                if not trans_file: continue
                norm_trans = os.path.normpath(trans_file)
                output_dir = os.path.dirname(norm_trans)
                source_path_file = os.path.join(output_dir, '_source_path.txt')
                if os.path.exists(source_path_file):
                    with open(source_path_file, 'r', encoding='utf-8') as f:
                        source = f.read().strip()
                    if source and os.path.normpath(source) == norm_path:
                        return file_path, trans_file
        except Exception: pass

        # Case 3: No pair found, it's a source file with no known translation.
        return file_path, None

    @pyqtSlot()
    def on_global_render_setting_changed(self):
        """Slot to handle changes in global render settings."""
        self.controller.handle_global_render_setting_change()