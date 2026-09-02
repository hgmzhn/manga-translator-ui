"""
文件服务层
处理文件和文件夹的选择、验证、拖拽等操作
"""
import base64
import json
import logging
import mimetypes
import os
import shutil
import sys
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from PIL import Image

# 添加项目根目录到路径以便导入path_manager
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from manga_translator.utils import open_pil_image
from manga_translator.utils.path_manager import find_json_path


class FileService:
    """文件操作服务"""
    
    def __init__(self):
        from services import get_config_service
        self.logger = logging.getLogger(__name__)
        self.config_service = get_config_service()
        # 支持的图片格式
        self.supported_image_extensions = set(SUPPORTED_IMAGE_EXTENSIONS)
        # 支持的压缩包/文档格式
        self.supported_archive_extensions = {
            '.pdf', '.epub', '.cbz', '.cbr', '.zip'
        }
        # 支持的配置文件格式
        self.supported_config_extensions = {
            '.json', '.yaml', '.yml', '.toml'
        }

    def load_translation_json(self, image_path: str, image: Image.Image = None) -> Tuple[List[dict], Optional[np.ndarray], Optional[Tuple[int, int]], Dict[str, Any]]:
        """
        根据给定的图片路径，加载关联的 _translations.json 文件。
        优先从新目录结构加载，支持向后兼容。
        返回 regions, raw_mask, original_size, overlays。
        overlays 为 {'paint': RGBA数组|None, 'stamp': RGBA数组|None,
                    'paste_overlays': [贴片字典...]}（base64 PNG 解码，未对齐尺寸）。
        """
        # 使用path_manager查找JSON文件（新位置优先）
        json_path = find_json_path(image_path)
        regions = []
        raw_mask = None
        original_size = None
        overlays: Dict[str, Any] = {
            'paint': None,
            'stamp': None,
            'paste_overlays': [],
        }

        if not json_path:
            self.logger.warning(f"JSON file not found for {os.path.basename(image_path)}")
            return regions, raw_mask, original_size, overlays

        self.logger.debug(f"Loading JSON from: {json_path}")

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            image_key = os.path.abspath(image_path)
            
            if image_key not in data:
                if data:
                    first_key = next(iter(data))
                    self.logger.warning(f"Exact image path '{image_key}' not found in JSON. Using first available key '{first_key}'.")
                    image_data = data[first_key]
                else:
                    image_data = {}
            else:
                image_data = data[image_key]

            regions = image_data.get('regions', [])

            config = self.config_service.get_config()
            default_target_lang = config.translator.target_lang if config else None

            if default_target_lang:
                for region in regions:
                    if not region.get('target_lang'):
                        region['target_lang'] = default_target_lang

            # 旧 JSON 兼容:缺 translation_raw 时用 translation 回填,
            # 保证编辑器"替换前译文"框始终有值显示
            for region in regions:
                if 'translation_raw' not in region:
                    region['translation_raw'] = region.get('translation', '')

            mask_data = image_data.get('mask_raw')
            if isinstance(mask_data, str):
                try:
                    img_bytes = base64.b64decode(mask_data)
                    img_array = np.frombuffer(img_bytes, dtype=np.uint8)
                    raw_mask = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
                except Exception as e:
                    self.logger.error(f"Failed to decode base64 mask in {os.path.basename(json_path)}: {e}")
                    raw_mask = None
            elif isinstance(mask_data, list):
                raw_mask = np.array(mask_data, dtype=np.uint8)
            
            original_size = (image_data.get('original_width'), image_data.get('original_height'))

            # 画笔层/印章层（base64 PNG，RGBA）
            for overlay_name, json_key in (('paint', 'paint_overlay'), ('stamp', 'stamp_overlay')):
                overlay_b64 = image_data.get(json_key)
                if not isinstance(overlay_b64, str) or not overlay_b64:
                    continue
                try:
                    overlay_bytes = np.frombuffer(base64.b64decode(overlay_b64), dtype=np.uint8)
                    overlay_bgra = cv2.imdecode(overlay_bytes, cv2.IMREAD_UNCHANGED)
                    if overlay_bgra is not None and overlay_bgra.ndim == 3 and overlay_bgra.shape[2] == 4:
                        overlays[overlay_name] = cv2.cvtColor(overlay_bgra, cv2.COLOR_BGRA2RGBA)
                except Exception as e:
                    self.logger.error(f"Failed to decode base64 {json_key} in {os.path.basename(json_path)}: {e}")

            self.logger.debug(f"Loaded {len(regions)} regions from {os.path.basename(json_path)}")

            # 贴片（图块叠加）列表：逐项规范化，脏数据跳过并告警
            paste_raw = image_data.get('paste_overlays')
            if isinstance(paste_raw, list) and paste_raw:
                try:
                    from editor.paste_overlay_state import parse_page_paste_overlays

                    overlays['paste_overlays'] = parse_page_paste_overlays(image_data)
                except Exception as e:
                    self.logger.error(f"Failed to parse paste_overlays in {os.path.basename(json_path)}: {e}")

        except Exception as e:
            import traceback
            self.logger.error(f"Failed to load or parse JSON file {json_path}: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return [], None, None, {'paint': None, 'stamp': None, 'paste_overlays': []}

        return regions, raw_mask, original_size, overlays
        
    def validate_image_file(self, file_path: str) -> bool:
        """验证是否为有效的图片文件或压缩包文件"""
        try:
            if not os.path.exists(file_path):
                return False
                
            # 检查文件扩展名
            _, ext = os.path.splitext(file_path)
            ext_lower = ext.lower()
            
            # 支持压缩包格式
            if ext_lower in self.supported_archive_extensions:
                return os.access(file_path, os.R_OK)
            
            if ext_lower not in self.supported_image_extensions:
                return False
                
            # 检查MIME类型
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type and not mime_type.startswith('image/'):
                return False
                
            # 检查文件是否可读
            if not os.access(file_path, os.R_OK):
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证图片文件失败 {file_path}: {e}")
            return False
    
    def is_archive_file(self, file_path: str) -> bool:
        """检查文件是否是压缩包/文档格式"""
        _, ext = os.path.splitext(file_path)
        return ext.lower() in self.supported_archive_extensions
    
    def validate_config_file(self, file_path: str) -> bool:
        """验证是否为有效的配置文件"""
        try:
            if not os.path.exists(file_path):
                return False
                
            _, ext = os.path.splitext(file_path)
            return ext.lower() in self.supported_config_extensions
            
        except Exception as e:
            self.logger.error(f"验证配置文件失败 {file_path}: {e}")
            return False
    
    def _natural_sort_key(self, path: str):
        """
        生成自然排序的键，支持数字排序
        例如: file1.jpg, file2.jpg, file10.jpg 会按 1, 2, 10 排序
        而不是按字符串 1, 10, 2 排序
        
        对于包含路径的文件，会对整个路径进行自然排序，确保子文件夹也能正确排序
        例如: 第1话/001.jpg, 第2话/001.jpg, 第10话/001.jpg 会按 1, 2, 10 排序
        """
        import re
        
        # 规范化路径分隔符
        normalized_path = path.replace('\\', '/')
        
        # 将整个路径分割成文本和数字部分
        # 使用元组确保类型安全：(是否为数字, 排序值)
        # 数字用整数排序，文本用字符串排序，通过第一个元素区分类型避免跨类型比较
        parts = []
        for part in re.split(r'(\d+)', normalized_path):
            if part.isdigit():
                # 数字部分：(False, 整数值) - False 排在 True 前面
                parts.append((False, int(part)))
            elif part:  # 忽略空字符串
                # 文本部分：(True, 小写文本) - True 排在 False 后面
                parts.append((True, part.lower()))
        
        return parts
    
    def get_supported_files_from_folder(
        self, folder_path: str, recursive: bool = True
    ) -> tuple[List[str], List[str]]:
        """一次遍历返回图片与压缩包，忽略 manga_translator_work。"""
        image_files: List[str] = []
        archive_files: List[str] = []
        try:
            if not os.path.isdir(folder_path):
                return image_files, archive_files

            entries = os.walk(folder_path) if recursive else [(folder_path, [], os.listdir(folder_path))]
            for root, dirs, files in entries:
                if 'manga_translator_work' in dirs:
                    dirs.remove('manga_translator_work')
                dirs.sort(key=self._natural_sort_key)
                current_images: List[str] = []
                current_archives: List[str] = []
                for file in files:
                    file_path = os.path.join(root, file)
                    ext = os.path.splitext(file)[1].lower()
                    if ext in self.supported_image_extensions and os.path.isfile(file_path):
                        current_images.append(file_path)
                    elif ext in self.supported_archive_extensions and os.path.isfile(file_path):
                        current_archives.append(file_path)
                current_images.sort(key=self._natural_sort_key)
                current_archives.sort(key=self._natural_sort_key)
                image_files.extend(current_images)
                archive_files.extend(current_archives)
        except Exception as e:
            self.logger.error(f"获取文件夹支持文件失败 {folder_path}: {e}")
        return image_files, archive_files

    def get_image_files_from_folder(self, folder_path: str, recursive: bool = True) -> List[str]:
        return self.get_supported_files_from_folder(folder_path, recursive)[0]

    def get_archive_files_from_folder(self, folder_path: str, recursive: bool = True) -> List[str]:
        return self.get_supported_files_from_folder(folder_path, recursive)[1]
    
    def filter_valid_image_files(self, file_paths: List[str]) -> List[str]:
        """过滤出有效的图片文件"""
        valid_files = []
        
        for file_path in file_paths:
            if self.validate_image_file(file_path):
                valid_files.append(file_path)
            else:
                self.logger.warning(f"跳过无效文件: {file_path}")
                
        return valid_files
    
    def process_dropped_files(self, dropped_data: str) -> Tuple[List[str], List[str]]:
        """处理拖拽的文件数据
        
        Returns:
            Tuple[List[str], List[str]]: (有效的图片文件列表, 错误信息列表)
        """
        image_files = []
        errors = []
        
        try:
            # 解析拖拽数据
            file_paths = self._parse_drop_data(dropped_data)
            
            for file_path in file_paths:
                if os.path.isfile(file_path):
                    if self.validate_image_file(file_path):
                        image_files.append(file_path)
                    else:
                        errors.append(f"不支持的图片格式: {os.path.basename(file_path)}")
                        
                elif os.path.isdir(file_path):
                    # 处理文件夹
                    folder_images = self.get_image_files_from_folder(file_path)
                    if folder_images:
                        image_files.extend(folder_images)
                    else:
                        errors.append(f"文件夹中没有找到图片: {os.path.basename(file_path)}")
                else:
                    errors.append(f"文件不存在: {os.path.basename(file_path)}")
                    
        except Exception as e:
            self.logger.error(f"处理拖拽文件失败: {e}")
            errors.append(f"处理拖拽文件时出错: {str(e)}")
            
        return image_files, errors
    
    def _parse_drop_data(self, dropped_data: str) -> List[str]:
        """解析拖拽数据，提取文件路径"""
        file_paths = []
        
        # 处理不同操作系统的换行符
        lines = dropped_data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        
        for line in lines:
            line = line.strip()
            if line:
                # 移除可能的URI前缀
                if line.startswith('file:///'):
                    line = line[8:]  # 移除 'file:///'
                elif line.startswith('file://'):
                    line = line[7:]  # 移除 'file://'
                
                # URL解码
                try:
                    import urllib.parse
                    line = urllib.parse.unquote(line)
                except Exception:
                    pass
                
                if os.path.exists(line):
                    file_paths.append(os.path.abspath(line))
                    
        return file_paths
    
    def get_file_info(self, file_path: str) -> dict:
        """获取文件信息"""
        try:
            if not os.path.exists(file_path):
                return {'error': '文件不存在'}
                
            stat = os.stat(file_path)
            file_info = {
                'name': os.path.basename(file_path),
                'path': os.path.abspath(file_path),
                'size': stat.st_size,
                'size_human': self._format_file_size(stat.st_size),
                'modified': stat.st_mtime,
                'is_readable': os.access(file_path, os.R_OK),
                'is_writable': os.access(file_path, os.W_OK)
            }
            
            if self.validate_image_file(file_path):
                file_info['type'] = 'image'
                # 获取图片尺寸
                try:
                    with open_pil_image(file_path, eager=False) as img:
                        file_info['width'] = img.width
                        file_info['height'] = img.height
                        file_info['format'] = img.format
                except Exception as e:
                    self.logger.warning(f"获取图片信息失败 {file_path}: {e}")
                    
            return file_info
            
        except Exception as e:
            self.logger.error(f"获取文件信息失败 {file_path}: {e}")
            return {'error': str(e)}
    
    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes/1024:.1f} KB"
        elif size_bytes < 1024**3:
            return f"{size_bytes/(1024**2):.1f} MB"
        else:
            return f"{size_bytes/(1024**3):.1f} GB"
    
    def create_backup(self, file_path: str, backup_dir: Optional[str] = None) -> str:
        """创建文件备份"""
        try:
            if backup_dir is None:
                backup_dir = os.path.join(os.path.dirname(file_path), 'backups')
                
            os.makedirs(backup_dir, exist_ok=True)
            
            # 生成备份文件名
            import time
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            name, ext = os.path.splitext(os.path.basename(file_path))
            backup_name = f"{name}_{timestamp}{ext}"
            backup_path = os.path.join(backup_dir, backup_name)
            
            # 复制文件
            shutil.copy2(file_path, backup_path)
            self.logger.info(f"创建备份: {backup_path}")
            
            return backup_path
            
        except Exception as e:
            self.logger.error(f"创建备份失败 {file_path}: {e}")
            raise
    
    def cleanup_temp_files(self, temp_dir: str, max_age_hours: int = 24) -> None:
        """清理临时文件"""
        try:
            if not os.path.exists(temp_dir):
                return
                
            import time
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        if current_time - os.path.getmtime(file_path) > max_age_seconds:
                            os.remove(file_path)
                            self.logger.info(f"删除过期临时文件: {file_path}")
                    except Exception as e:
                        self.logger.warning(f"删除临时文件失败 {file_path}: {e}")
                        
        except Exception as e:
            self.logger.error(f"清理临时文件失败: {e}")
    
    def get_supported_image_extensions(self) -> Set[str]:
        """获取支持的图片文件扩展名"""
        return self.supported_image_extensions.copy()
    
    def get_supported_config_extensions(self) -> Set[str]:
        """获取支持的配置文件扩展名"""
        return self.supported_config_extensions.copy()
    
    def normalize_path(self, path: str) -> str:
        """标准化路径"""
        return os.path.normpath(os.path.abspath(path))
