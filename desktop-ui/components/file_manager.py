import os
import json
from PIL import Image
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from tkinter import messagebox
from services import get_config_service

class FileManager:
    """
    负责处理编辑器中的文件加载和关联数据读取。
    这个类被重写以确保其功能明确，且与调用方 (EditorFrame) 的接口完全匹配。
    """
    def __init__(self):
        """初始化文件管理器。"""
        self.current_file_path: Optional[str] = None
        self.image_loaded_callback: Optional[callable] = None
        self.config_service = get_config_service()
        print("New FileManager initialized.")

    def register_callback(self, name: str, callback: callable):
        """注册回调函数，目前仅支持 'image_loaded'。"""
        if name == 'image_loaded':
            self.image_loaded_callback = callback

    def load_image_from_path(self, file_path: str):
        """
        从指定路径加载图片，并触发回调。
        """
        try:
            image = Image.open(file_path)
            self.current_file_path = file_path
            if self.image_loaded_callback:
                # 调用在 EditorFrame 中注册的 _on_image_loaded 方法
                self.image_loaded_callback(image, file_path)
            # print(f"Image loaded successfully: {os.path.basename(file_path)}")
        except FileNotFoundError:
            error_msg = f"错误：找不到文件 {file_path}"
            print(error_msg)
            messagebox.showerror("文件未找到", error_msg)
        except Exception as e:
            error_msg = f"加载图片失败: {str(e)}"
            print(error_msg)
            messagebox.showerror("加载错误", error_msg)

    def load_json_data(self, image_path: str) -> Tuple[List[Dict[str, Any]], Optional[np.ndarray], Optional[Tuple[int, int]]]:
        """
        根据给定的图片路径，加载关联的 _translations.json 文件。
        返回 regions, raw_mask, 和 original_size。
        """
        json_path = os.path.splitext(image_path)[0] + '_translations.json'
        regions = []
        raw_mask = None
        original_size = None

        if not os.path.exists(json_path):
            print(f"JSON file not found for {os.path.basename(image_path)}, returning empty data.")
            return regions, raw_mask, original_size

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 使用图片的绝对路径作为key来查找数据
            image_key = os.path.abspath(image_path)
            
            # 如果精确的key不存在，则使用文件中的第一个key作为兼容性回退
            if image_key not in data:
                if data:
                    first_key = next(iter(data))
                    print(f"Warning: Exact image path '{image_key}' not found in JSON. Using first available key '{first_key}'.")
                    image_data = data[first_key]
                else:
                    image_data = {}
            else:
                image_data = data[image_key]

            regions = image_data.get('regions', [])
            
            # Get the default target language from the config service
            config = self.config_service.get_config()
            default_target_lang = config.get('translator', {}).get('target_lang')

            # Apply fallback for target_lang
            if default_target_lang:
                for region in regions:
                    if not region.get('target_lang'):
                        region['target_lang'] = default_target_lang
                        print(f"Applied fallback target_lang '{default_target_lang}' to a region.")

            # 加载蒙版数据
            mask_data_list = image_data.get('mask_raw')
            if mask_data_list is not None:
                if isinstance(mask_data_list, list):
                    raw_mask = np.array(mask_data_list, dtype=np.uint8)
                else:
                    print(f"[ERROR] Invalid 'mask_raw' data in {os.path.basename(json_path)}. Expected a list, got {type(mask_data_list)}.")
                    raw_mask = None
            
            # 加载原始尺寸
            original_size = (image_data.get('original_width'), image_data.get('original_height'))

            print(f"Loaded {len(regions)} regions from {os.path.basename(json_path)}")

        except Exception as e:
            error_msg = f"加载或解析JSON文件失败: {json_path}\n错误: {e}"
            print(error_msg)
            messagebox.showerror("JSON 读取错误", error_msg)
            # 出错时返回空数据，防止UI崩溃
            return [], None, None

        return regions, raw_mask, original_size
