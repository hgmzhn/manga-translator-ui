"""
文件列表模型 - 统一处理编辑器中的原图入口
"""
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from manga_translator.image_formats import (
    SUPPORTED_IMAGE_EXTENSIONS as _SUPPORTED_IMAGE_EXTENSIONS,
)
from manga_translator.utils.path_manager import resolve_original_image_path

# Backward-compatible export for editor callers; the canonical definition lives
# in manga_translator.image_formats.
SUPPORTED_IMAGE_EXTENSIONS = _SUPPORTED_IMAGE_EXTENSIONS


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


class FileType(Enum):
    """文件类型枚举"""
    SOURCE = "source"           # 原图（有JSON）
    UNTRANSLATED = "untranslated"  # 未翻译的原图（暂无JSON）


@dataclass
class FileItem:
    """文件项数据类"""
    path: str                    # 文件路径
    file_type: FileType          # 文件类型
    json_path: Optional[str] = None      # JSON路径（如果是原图）


class FileListModel:
    """
    文件列表模型 - 统一处理编辑器中的原图入口
    
    核心逻辑：
    1. 检查目录中是否有 JSON 文件 → 原图
    2. 都没有 → 未翻译的图
    
    translation_map 仅用于把“翻译结果图路径”解析回原图路径，不直接作为编辑器列表项。
    """
    
    def __init__(self):
        self.files: List[FileItem] = []
        self._map_cache: Dict[str, dict] = {}  # 缓存 translation_map.json
        self._path_index: Dict[str, FileItem] = {}

    @staticmethod
    def is_supported_image_file(file_path: str) -> bool:
        """检查是否是编辑器支持的图片文件。"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in SUPPORTED_IMAGE_EXTENSIONS
    
    def clear(self):
        """清空文件列表"""
        self.files.clear()
        self._map_cache.clear()
        self._path_index.clear()
    
    def add_files(self, file_paths: List[str]) -> List[FileItem]:
        """
        添加文件到列表
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            添加的文件项列表
        """
        added_items = []
        
        for file_path in file_paths:
            file_path = self.resolve_entry_path(file_path)
            if not os.path.exists(file_path):
                continue
            if not os.path.isfile(file_path):
                continue
            if not self.is_supported_image_file(file_path):
                continue
            
            # 检查是否已存在
            norm_path = os.path.abspath(os.path.normpath(file_path))
            path_key = _path_key(norm_path)
            if path_key in self._path_index:
                continue
            
            # 识别文件类型
            file_item = self._identify_file(file_path)
            self.files.append(file_item)
            self._path_index[path_key] = file_item
            added_items.append(file_item)
        
        return added_items
    
    def remove_file(self, file_path: str) -> bool:
        """
        移除文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否成功移除
        """
        path_key = _path_key(file_path)
        item = self._path_index.pop(path_key, None)
        if item is None:
            return False
        self.files.remove(item)
        return True
    
    def get_file_item(self, file_path: str) -> Optional[FileItem]:
        """
        获取文件项
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件项，如果不存在返回 None
        """
        return self._path_index.get(_path_key(file_path))

    def replace_from_snapshot(self, snapshot) -> List[FileItem]:
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
            self._path_index[_path_key(normalized)] = item
        return list(self.files)
    
    def resolve_entry_path(self, file_path: str) -> str:
        """将工作底图/翻译结果图统一解析为原图路径。"""
        norm_path = os.path.abspath(os.path.normpath(file_path))

        source_from_map = self._find_source_from_map(norm_path)
        if source_from_map:
            return os.path.abspath(os.path.normpath(source_from_map))

        return os.path.abspath(os.path.normpath(resolve_original_image_path(norm_path)))

    def _identify_file(self, file_path: str) -> FileItem:
        """
        识别文件类型
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件项
        """
        norm_path = os.path.normpath(file_path)
        file_dir = os.path.dirname(norm_path)
        file_name = os.path.basename(norm_path)
        file_name_no_ext = os.path.splitext(file_name)[0]
        
        # 1. 检查是否有对应的 JSON 文件（原图）
        json_path = self._find_json_file(file_dir, file_name_no_ext)
        if json_path:
            return FileItem(
                path=norm_path,
                file_type=FileType.SOURCE,
                json_path=json_path
            )
        
        # 没有 JSON，视为未翻译原图
        return FileItem(
            path=norm_path,
            file_type=FileType.UNTRANSLATED
        )
    
    def _find_json_file(self, file_dir: str, file_name_no_ext: str) -> Optional[str]:
        """
        查找对应的 JSON 文件
        
        优先从新目录结构查找，支持向后兼容
        """
        # 新目录结构：manga_translator_work/json/xxx_translations.json
        new_json_path = os.path.join(
            file_dir, 
            'manga_translator_work', 
            'json', 
            f'{file_name_no_ext}_translations.json'
        )
        if os.path.exists(new_json_path):
            return new_json_path
        
        # 旧目录结构：同目录下的 xxx_translations.json
        old_json_path = os.path.join(file_dir, f'{file_name_no_ext}_translations.json')
        if os.path.exists(old_json_path):
            return old_json_path
        
        return None
    
    def _find_source_from_map(self, translated_path: str) -> Optional[str]:
        """
        从 translation_map.json 中查找源文件路径
        
        Args:
            translated_path: 翻译后的文件路径
            
        Returns:
            源文件路径，如果不存在返回 None
        """
        try:
            map_path = os.path.join(os.path.dirname(translated_path), 'translation_map.json')
            if not os.path.exists(map_path):
                return None

            # 使用缓存
            if map_path not in self._map_cache:
                with open(map_path, 'r', encoding='utf-8') as f:
                    raw_map = json.load(f)
                self._map_cache[map_path] = {
                    _path_key(key): value
                    for key, value in raw_map.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
             
            translation_map = self._map_cache[map_path]
            norm_translated = _path_key(translated_path)
            
            # translation_map 的格式：{translated_path: source_path}
            source_path = translation_map.get(norm_translated)
            if source_path and os.path.exists(source_path):
                return source_path
        except Exception:
            pass
        
        return None
    
