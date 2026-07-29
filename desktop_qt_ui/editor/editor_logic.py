import os
from typing import List

from manga_translator.image_formats import IMAGE_FILE_DIALOG_FILTER
from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from editor.file_list_model import SUPPORTED_IMAGE_EXTENSIONS, FileListModel, FileType
from services import get_config_service, get_logger
from services.file_list_data_service import (
    KIND_FOLDER,
    FileCatalogSnapshot,
    FileListDataService,
    canonical_path_key,
)
from ui.secondary_pages.folder_dialog import select_folders


class EditorLogic(QObject):
    """
    Handles the business logic for the editor view, including file list management.
    """
    file_list_changed = pyqtSignal(list)
    file_list_with_tree_changed = pyqtSignal(list, dict)  # (files, folder_map)
    file_snapshot_changed = pyqtSignal(object)
    file_list_loading = pyqtSignal(str)
    file_list_error = pyqtSignal(str)

    def __init__(self, controller, parent=None, file_data_service=None):
        super().__init__(parent)
        self.controller = controller
        self.config_service = get_config_service()
        self.logger = get_logger(__name__)
        
        # 使用新的文件列表模型
        self.file_model = FileListModel()
        
        # 保留树形结构支持
        self.folder_tree: dict = {}  # 保存文件夹树结构
        self._snapshot = FileCatalogSnapshot.empty()
        self._source_by_key: dict[str, str] = {}
        self._source_paths: list[str] = []
        self._source_keys: set[str] = set()
        self._source_folders: dict[str, str] = {}
        self._excluded_folders: set[str] = set()
        self._excluded_files: set[str] = set()
        self._pending_load_path: str | None = None
        self._load_first_after_snapshot = False
        self._owns_file_data_service = file_data_service is None
        self.file_data_service = file_data_service or FileListDataService(self, max_workers=1)
        self._file_channel = f"editor-files-{id(self)}"
        self.file_data_service.loading.connect(
            self._on_snapshot_loading,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self.file_data_service.snapshot_ready.connect(
            self._on_snapshot_ready,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self.file_data_service.error.connect(
            self._on_snapshot_error,
            type=Qt.ConnectionType.QueuedConnection,
        )

    @staticmethod
    def _path_is_within(path: str, folder: str) -> bool:
        path_key = canonical_path_key(path)
        folder_key = canonical_path_key(folder)
        try:
            return os.path.commonpath([path_key, folder_key]) == folder_key
        except ValueError:
            return False

    def _set_sources(self, paths: List[str], folder_keys: set[str] | None = None) -> None:
        self._source_paths = []
        self._source_keys = set()
        self._source_folders = {}
        for path in paths:
            if not path:
                continue
            normalized = os.path.abspath(os.path.normpath(path))
            key = canonical_path_key(normalized)
            if key in self._source_keys:
                continue
            self._source_keys.add(key)
            self._source_paths.append(normalized)
            if folder_keys and key in folder_keys:
                self._source_folders[key] = normalized

    def _add_sources(self, paths: List[str], *, load_first: bool = False) -> None:
        source_by_key = {
            canonical_path_key(path): path for path in self._source_paths
        }
        folder_by_key = dict(self._source_folders)
        changed = False
        for path in paths:
            if not path:
                continue
            normalized = os.path.abspath(os.path.normpath(path))
            key = canonical_path_key(normalized)
            is_dir = os.path.isdir(normalized)
            folder_matches = {
                excluded
                for excluded in self._excluded_folders
                if self._path_is_within(normalized, excluded)
                or (is_dir and self._path_is_within(excluded, normalized))
            }
            file_matches = {
                excluded
                for excluded in self._excluded_files
                if excluded == key or (is_dir and self._path_is_within(excluded, normalized))
            }
            if folder_matches:
                self._excluded_folders.difference_update(folder_matches)
                changed = True
            if file_matches:
                self._excluded_files.difference_update(file_matches)
                changed = True
            if any(
                key != folder_key and self._path_is_within(normalized, folder)
                for folder_key, folder in folder_by_key.items()
            ):
                changed = source_by_key.pop(key, None) is not None or changed
                folder_by_key.pop(key, None)
                continue
            if is_dir:
                redundant_keys = [
                    source_key
                    for source_key, source in source_by_key.items()
                    if source_key != key and self._path_is_within(source, normalized)
                ]
                for source_key in redundant_keys:
                    source_by_key.pop(source_key, None)
                    folder_by_key.pop(source_key, None)
                folder_by_key[key] = normalized
                changed = bool(redundant_keys) or changed
            if key not in source_by_key:
                source_by_key[key] = normalized
                changed = True
        if changed:
            self._source_paths = list(source_by_key.values())
            self._source_keys = set(source_by_key)
            self._source_folders = folder_by_key
            self._load_first_after_snapshot = load_first
            self._request_snapshot()

    def _request_snapshot(self) -> None:
        self.file_data_service.request_snapshot(
            self._file_channel,
            tuple(self._source_paths),
            tuple(self._excluded_folders),
            tuple(self._excluded_files),
        )

    @pyqtSlot(str, int)
    def _on_snapshot_loading(self, channel: str, _generation: int) -> None:
        if channel == self._file_channel:
            self.file_list_loading.emit("正在加载文件列表...")

    @pyqtSlot(str, int, object)
    def _on_snapshot_ready(self, channel: str, _generation: int, snapshot: object) -> None:
        if channel != self._file_channel:
            return
        self.apply_file_snapshot(snapshot.images_only())

    @pyqtSlot(str, int, str)
    def _on_snapshot_error(self, channel: str, _generation: int, message: str) -> None:
        if channel == self._file_channel:
            self.file_list_error.emit(message)

    def apply_file_snapshot(
        self,
        snapshot: FileCatalogSnapshot,
        *,
        excluded_folders=None,
        excluded_files=None,
    ) -> None:
        editor_snapshot = snapshot.images_only()
        self._snapshot = editor_snapshot
        self._source_by_key = {
            canonical_path_key(path): source
            for path, source in editor_snapshot.source_by_file.items()
        }
        source_keys = {canonical_path_key(path) for path in editor_snapshot.sources}
        folder_keys = {
            canonical_path_key(node.path)
            for node in editor_snapshot.roots
            if node.kind == KIND_FOLDER and canonical_path_key(node.path) in source_keys
        }
        self._set_sources(list(editor_snapshot.sources), folder_keys)
        self._excluded_folders = {
            canonical_path_key(path)
            for path in (
                editor_snapshot.excluded_folders
                if excluded_folders is None
                else excluded_folders
            )
        }
        self._excluded_files = {
            canonical_path_key(path)
            for path in (
                editor_snapshot.excluded_files
                if excluded_files is None
                else excluded_files
            )
        }
        items = self.file_model.replace_from_snapshot(editor_snapshot)
        self.folder_tree.clear()
        self.file_snapshot_changed.emit(editor_snapshot)
        self.file_list_changed.emit([item.path for item in items])

        pending_path = self._pending_load_path
        self._pending_load_path = None
        if pending_path:
            resolved = self._source_by_key.get(
                canonical_path_key(pending_path),
                os.path.abspath(os.path.normpath(pending_path)),
            )
            if self.file_model.get_file_item(resolved):
                self._load_resolved_image(resolved)
        elif self._load_first_after_snapshot and items:
            self._load_resolved_image(items[0].path)
        self._load_first_after_snapshot = False

    # --- File Management Methods ---

    @pyqtSlot()
    def open_and_add_files(self):
        """Opens a file dialog to add files to the editor's list."""
        last_dir = self.config_service.get_config().app.last_open_dir
        file_paths, _ = QFileDialog.getOpenFileNames(
            None, 
            "添加文件到编辑器", 
            last_dir, 
            IMAGE_FILE_DIALOG_FILTER
        )
        if file_paths:
            self.add_files(file_paths)
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
            self.add_folders(folders)

    def add_files(self, files: List[str]):
        """后台添加文件，不在 GUI 线程识别 JSON 或缩略图。"""
        if not files:
            return
        self._add_sources(files, load_first=not self.file_model.files)

    def add_folder(self, folder_path: str):
        """添加文件夹到列表"""
        self.add_folders([folder_path])

    def add_folders(self, folder_paths: List[str]):
        """添加目录源；完整目录树由常驻后台线程池构建。"""
        self._add_sources(folder_paths, load_first=not self.file_model.files)

    @pyqtSlot(list)
    def add_files_from_paths(self, paths: List[str]):
        """
        从拖放的路径列表中添加文件和文件夹
        
        Args:
            paths: 拖放的文件或文件夹路径列表
        """
        self._add_sources(paths, load_first=not self.file_model.files)

    @pyqtSlot(str)
    def remove_file(self, file_path: str, emit_signal: bool = False):
        """从内存模型移除；后续后台重建通过 exclusion 保持删除结果。"""
        target_key = canonical_path_key(file_path)
        target_node = None
        stack = list(self._snapshot.roots)
        while stack:
            node = stack.pop()
            if canonical_path_key(node.path) == target_key:
                target_node = node
                break
            stack.extend(node.children)

        is_folder = target_node is not None and target_node.kind == KIND_FOLDER
        if target_key in self._source_keys:
            self._source_keys.remove(target_key)
            self._source_folders.pop(target_key, None)
            self._source_paths = [
                path for path in self._source_paths if canonical_path_key(path) != target_key
            ]
            covered_by_folder = any(
                target_key != folder_key and self._path_is_within(file_path, folder)
                for folder_key, folder in self._source_folders.items()
            )
            if covered_by_folder:
                if is_folder:
                    self._excluded_folders.add(target_key)
                else:
                    self._excluded_files.add(target_key)
            elif is_folder:
                self._excluded_folders = {
                    key for key in self._excluded_folders if not key.startswith(target_key + os.sep)
                }
                self._excluded_files = {
                    key for key in self._excluded_files if not key.startswith(target_key + os.sep)
                }
        elif is_folder:
            self._excluded_folders.add(target_key)
        else:
            self._excluded_files.add(target_key)

        displayed_paths: list[str] = []
        if target_node is not None:
            pending_nodes = [target_node]
            while pending_nodes:
                node = pending_nodes.pop()
                if node.kind != KIND_FOLDER:
                    displayed_paths.append(node.path)
                pending_nodes.extend(node.children)
        else:
            displayed_paths.append(file_path)

        source_paths = {
            self._source_by_key.get(canonical_path_key(path), os.path.abspath(os.path.normpath(path)))
            for path in displayed_paths
        }
        for source_path in source_paths:
            self.file_model.remove_file(source_path)
            if hasattr(self.controller, 'resource_manager'):
                self.controller.resource_manager.release_image_from_cache(source_path)

        current_image_path = self.controller.model.get_source_image_path()
        if current_image_path and canonical_path_key(current_image_path) in {
            canonical_path_key(path) for path in source_paths
        }:
            self.controller._clear_editor_state(release_image_cache=True)
        
        # 检查是否还有文件，如果没有了就清空画布
        if len(self.file_model.files) == 0:
            self.controller._clear_editor_state(release_image_cache=True)
            
            # 清空所有图片缓存
            if hasattr(self.controller, 'resource_manager'):
                self.controller.resource_manager.clear_image_cache()
        
        # 如果需要发射信号，更新UI
        if emit_signal:
            file_paths = [item.path for item in self.file_model.files]
            self.file_list_changed.emit(file_paths)

    @pyqtSlot()
    def clear_list(self):
        """清空文件列表"""
        self.file_data_service.cancel(self._file_channel)
        self._source_paths.clear()
        self._source_keys.clear()
        self._source_folders.clear()
        self._excluded_folders.clear()
        self._excluded_files.clear()
        self._source_by_key.clear()
        self._snapshot = FileCatalogSnapshot.empty(self._snapshot.generation + 1)
        self.file_model.clear()
        self.folder_tree.clear()
        self.file_snapshot_changed.emit(self._snapshot)
        self.file_list_changed.emit([])
        
        # 先清空画布图片，这样后台任务会检测到图片为None而提前返回
        # 然后清空编辑器状态（包括取消后台任务）
        self.controller._clear_editor_state(release_image_cache=True)
        
        # 清空所有图片缓存
        if hasattr(self.controller, 'resource_manager'):
            self.controller.resource_manager.clear_image_cache()

    # --- Image Loading Methods ---

    def _adjacent_image_paths(self, resolved_path: str) -> List[str]:
        norm_current = os.path.normcase(os.path.normpath(resolved_path))
        file_paths = [item.path for item in self.file_model.files]
        norm_paths = [os.path.normcase(os.path.normpath(path)) for path in file_paths]
        if norm_current not in norm_paths:
            return []

        index = norm_paths.index(norm_current)
        adjacent = []
        for next_index in (index + 1, index - 1):
            if 0 <= next_index < len(file_paths):
                adjacent.append(file_paths[next_index])
        return adjacent

    def load_file_lists(self, source_files: List[str], folder_tree: dict = None):
        """旧调用兼容入口：只提交源路径，扫描与 JSON 解析全部在后台。"""
        roots: list[str] = []
        if folder_tree:
            folders = [os.path.abspath(os.path.normpath(path)) for path in folder_tree]
            for folder in folders:
                if not any(
                    canonical_path_key(folder) != canonical_path_key(candidate)
                    and self._path_is_within(folder, candidate)
                    for candidate in folders
                ):
                    roots.append(folder)

        standalone = [
            path
            for path in source_files
            if not any(self._path_is_within(path, folder) for folder in roots)
        ]
        self._set_sources(
            roots + standalone,
            {canonical_path_key(path) for path in roots},
        )
        self._excluded_folders.clear()
        self._excluded_files.clear()
        self._request_snapshot()

    @pyqtSlot(str)
    def load_image_into_editor(self, file_path: str):
        """
        加载图片到编辑器（统一接口）
        """
        resolved_path = self._source_by_key.get(canonical_path_key(file_path))
        if resolved_path is None and self.file_model.get_file_item(file_path):
            resolved_path = os.path.abspath(os.path.normpath(file_path))

        if resolved_path is None:
            self._pending_load_path = file_path
            previous_keys = set(self._source_keys)
            self._add_sources([file_path])
            if previous_keys == self._source_keys:
                self._request_snapshot()
            return

        self._load_resolved_image(resolved_path)

    def _load_resolved_image(self, resolved_path: str) -> None:
        resolved_path = os.path.abspath(os.path.normpath(resolved_path))

        # 获取文件项
        if not FileListModel.is_supported_image_file(resolved_path):
            self.logger.warning(f"不支持的编辑器文件类型，已忽略: {resolved_path}")
            return

        file_item = self.file_model.get_file_item(resolved_path)
        if not file_item:
            self.logger.error(f"无法识别文件: {resolved_path}")
            return

        if file_item.file_type == FileType.UNTRANSLATED:
            self.logger.warning(f"未翻译的图片: {resolved_path}")

        self.controller._pending_editor_prefetch_paths = self._adjacent_image_paths(resolved_path)
        self.controller.load_image_and_regions(resolved_path)

    def shutdown(self) -> None:
        self.file_data_service.cancel(self._file_channel)
        if self._owns_file_data_service:
            self.file_data_service.shutdown()
