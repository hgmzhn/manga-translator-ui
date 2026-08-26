"""
文件列表模型 - 统一处理编辑器中的原图入口
"""

import os
from dataclasses import dataclass
from enum import Enum

from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS

from services.file_list_data_service import canonical_path_key


class FileType(Enum):
    """文件类型枚举"""

    SOURCE = "source"  # 原图（有JSON）
    UNTRANSLATED = "untranslated"  # 未翻译的原图（暂无JSON）


@dataclass
class FileItem:
    """文件项数据类"""

    path: str  # 文件路径
    file_type: FileType  # 文件类型
    json_path: str | None = None  # JSON路径（如果是原图）


class FileListModel:
    """In-memory image metadata projected from a background catalog snapshot."""

    def __init__(self):
        self.files: list[FileItem] = []
        self._path_index: dict[str, FileItem] = {}

    @staticmethod
    def is_supported_image_file(file_path: str) -> bool:
        """检查是否是编辑器支持的图片文件。"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in SUPPORTED_IMAGE_EXTENSIONS

    def clear(self):
        """清空文件列表"""
        self.files.clear()
        self._path_index.clear()

    def remove_file(self, file_path: str) -> bool:
        """
        移除文件

        Args:
            file_path: 文件路径

        Returns:
            是否成功移除
        """
        path_key = canonical_path_key(file_path)
        item = self._path_index.pop(path_key, None)
        if item is None:
            return False
        self.files.remove(item)
        return True

    def get_file_item(self, file_path: str) -> FileItem | None:
        """Return the item with the same canonical path identity."""
        return self._path_index.get(canonical_path_key(file_path))

    def replace_from_snapshot(self, snapshot) -> None:
        """用后台快照一次性替换编辑器列表，不在 GUI 线程读取元数据。"""
        self.clear()
        for file_path in snapshot.editor_files:
            normalized = os.path.abspath(os.path.normpath(file_path))
            json_path = snapshot.json_by_file.get(normalized)
            item = FileItem(
                path=normalized,
                file_type=FileType.SOURCE if json_path else FileType.UNTRANSLATED,
                json_path=json_path,
            )
            self.files.append(item)
            self._path_index[canonical_path_key(normalized)] = item
