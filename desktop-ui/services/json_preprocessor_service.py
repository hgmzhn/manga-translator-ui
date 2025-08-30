"""
JSON预处理服务
处理加载文本+模板模式下的翻译写回功能
"""
import json
import os
from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)


class JsonPreprocessorService:
    """JSON预处理服务"""
    
    def __init__(self):
        self.processed_files = set()  # 跟踪已处理的文件，避免重复处理
    
    def restore_translation_to_text(self, json_path: str) -> bool:
        """
        将翻译结果写回到原文字段
        在加载文本+模板模式下使用，确保模板输出翻译而不是原文
        
        Args:
            json_path: JSON文件路径
            
        Returns:
            bool: 是否有修改并成功写回
        """
        try:
            # 避免重复处理同一文件
            if json_path in self.processed_files:
                logger.debug(f"File {json_path} already processed, skipping")
                return False
            
            if not os.path.exists(json_path):
                logger.warning(f"JSON file not found: {json_path}")
                return False
                
            # 读取JSON文件
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            modified = False
            processed_regions = 0
            
            # 遍历所有图片的数据
            for image_key, image_data in data.items():
                if isinstance(image_data, dict) and 'regions' in image_data:
                    regions = image_data['regions']
                    
                    for region in regions:
                        if isinstance(region, dict):
                            # 获取翻译和原文
                            translation = region.get('translation', '').strip()
                            original_text = region.get('text', '').strip()
                            
                            # 只有翻译不为空且与原文不同时才写回
                            if translation and translation != original_text:
                                # 将翻译写回到原文字段
                                region['text'] = translation
                                
                                # 同时更新texts数组
                                if 'texts' in region and isinstance(region['texts'], list):
                                    if len(region['texts']) > 0:
                                        region['texts'][0] = translation
                                    else:
                                        region['texts'] = [translation]
                                
                                modified = True
                                processed_regions += 1
                                logger.debug(f"Restored translation to text: '{original_text}' -> '{translation}'")
            
            # 如果有修改，写回文件
            if modified:
                # 创建备份（可选）
                # backup_path = json_path + '.backup'
                # shutil.copy2(json_path, backup_path)
                
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                
                logger.info(f"Processed {processed_regions} regions in {json_path}")
                self.processed_files.add(json_path)
                
            return modified
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format in {json_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error processing {json_path}: {e}")
            return False
    
    def batch_process_folder(self, folder_path: str, pattern: str = "*_translations.json") -> Tuple[int, int]:
        """
        批量处理文件夹中的JSON文件
        
        Args:
            folder_path: 文件夹路径
            pattern: 文件匹配模式
            
        Returns:
            Tuple[int, int]: (成功处理的文件数, 总文件数)
        """
        import glob
        
        if not os.path.isdir(folder_path):
            logger.warning(f"Folder not found: {folder_path}")
            return 0, 0
        
        # 查找所有匹配的JSON文件
        search_pattern = os.path.join(folder_path, "**", pattern)
        json_files = glob.glob(search_pattern, recursive=True)
        
        successful = 0
        total = len(json_files)
        
        logger.info(f"Found {total} JSON files in {folder_path}")
        
        for json_file in json_files:
            try:
                if self.restore_translation_to_text(json_file):
                    successful += 1
                    logger.info(f"Successfully processed: {os.path.basename(json_file)}")
                else:
                    logger.debug(f"No changes needed for: {os.path.basename(json_file)}")
            except Exception as e:
                logger.error(f"Failed to process {json_file}: {e}")
        
        return successful, total
    
    def process_file_list(self, file_paths: List[str]) -> Tuple[int, int]:
        """
        处理指定的图片文件列表，查找对应的JSON文件并处理
        
        Args:
            file_paths: 图片文件路径列表
            
        Returns:
            Tuple[int, int]: (成功处理的文件数, 总文件数)
        """
        successful = 0
        total = 0
        
        for file_path in file_paths:
            # 生成对应的JSON文件路径
            json_path = os.path.splitext(file_path)[0] + "_translations.json"
            
            if os.path.exists(json_path):
                total += 1
                try:
                    if self.restore_translation_to_text(json_path):
                        successful += 1
                        logger.info(f"Processed JSON for: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.error(f"Failed to process JSON for {file_path}: {e}")
            else:
                logger.debug(f"No JSON file found for: {os.path.basename(file_path)}")
        
        return successful, total
    
    def should_process(self, load_text_enabled: bool, template_enabled: bool) -> bool:
        """
        检查是否应该执行预处理
        
        Args:
            load_text_enabled: 是否启用了加载文本模式
            template_enabled: 是否启用了模板模式
            
        Returns:
            bool: 是否应该处理
        """
        return load_text_enabled and template_enabled
    
    def clear_processed_cache(self):
        """清除已处理文件的缓存"""
        self.processed_files.clear()
        logger.debug("Cleared processed files cache")


# 全局服务实例
_json_preprocessor_service = None

def get_json_preprocessor_service() -> JsonPreprocessorService:
    """获取JSON预处理服务实例"""
    global _json_preprocessor_service
    if _json_preprocessor_service is None:
        _json_preprocessor_service = JsonPreprocessorService()
    return _json_preprocessor_service