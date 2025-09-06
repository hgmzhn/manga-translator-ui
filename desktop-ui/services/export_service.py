#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出服务
负责将编辑器中的内容导出为后端渲染的图片
"""

import os
import json
import asyncio
import tempfile
import threading
from typing import Dict, Any, List, Optional
from PIL import Image
import logging
import numpy as np

from utils.json_encoder import CustomJSONEncoder


class ExportService:
    """导出服务类"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def get_output_directory(self) -> Optional[str]:
        """获取设置的输出目录"""
        try:
            # 尝试通过全局变量获取应用实例
            import tkinter as tk
            
            # 获取根窗口
            root = tk._default_root
            if root is None:
                return None
            
            # 查找应用控制器
            for child in root.winfo_children():
                if hasattr(child, 'controller'):
                    controller = child.controller
                    if hasattr(controller, 'main_view_widgets'):
                        output_entry = controller.main_view_widgets.get('output_folder_entry')
                        if output_entry:
                            output_dir = output_entry.get().strip()
                            if output_dir and os.path.exists(output_dir):
                                self.logger.info(f"找到输出目录: {output_dir}")
                                return output_dir
                            elif output_dir:
                                self.logger.warning(f"输出目录不存在: {output_dir}")
                            break
                    break
                    
        except Exception as e:
            self.logger.warning(f"无法获取输出目录: {e}")
        
        return None
    
    def get_output_format_from_config(self, config: Dict[str, Any]) -> str:
        """从配置中获取输出格式"""
        cli_config = config.get('cli', {})
        output_format = cli_config.get('format', '').strip()
        
        # 如果没有指定格式或格式为空，返回空字符串表示使用原格式
        if not output_format or output_format == "不指定":
            return ""
        
        return output_format.lower()
    
    def generate_output_filename(self, original_image_path: str, output_format: str = "") -> str:
        """生成输出文件名，参考正常翻译流程的命名规则"""
        base_name = os.path.splitext(os.path.basename(original_image_path))[0]
        
        # 使用与正常翻译流程相同的前缀
        output_name = f"translated_{base_name}"
        
        # 确定文件扩展名
        if output_format:
            # 使用配置中指定的格式
            extension = f".{output_format}"
        else:
            # 使用原文件的格式
            original_ext = os.path.splitext(original_image_path)[1].lower()
            extension = original_ext if original_ext else ".png"
        
        return output_name + extension
    
    def export_rendered_image(self, image: Image.Image, regions_data: List[Dict[str, Any]], 
                            config: Dict[str, Any], output_path: str, 
                            mask: Optional[np.ndarray] = None,
                            progress_callback: Optional[callable] = None,
                            success_callback: Optional[callable] = None,
                            error_callback: Optional[callable] = None):
        """
        导出后端渲染的图片
        
        Args:
            image: 当前图片
            regions_data: 区域数据
            config: 配置字典
            output_path: 输出路径
            mask: (新增) 预计算的蒙版
            progress_callback: 进度回调
            success_callback: 成功回调
            error_callback: 错误回调
        """
        if not image or not regions_data:
            if error_callback:
                error_callback("没有图片或区域数据可导出")
            return
        
        if progress_callback:
            progress_callback("开始导出渲染图片...")
        
        # 在后台线程中执行导出
        export_thread = threading.Thread(
            target=self._perform_backend_render_export,
            args=(image, regions_data, config, output_path, mask, progress_callback, success_callback, error_callback),
            daemon=True
        )
        export_thread.start()
    
    def _perform_backend_render_export(self, image: Image.Image, regions_data: List[Dict[str, Any]], 
                                     config: Dict[str, Any], output_path: str,
                                     mask: Optional[np.ndarray] = None,
                                     progress_callback: Optional[callable] = None,
                                     success_callback: Optional[callable] = None,
                                     error_callback: Optional[callable] = None):
        """在后台线程中执行后端渲染导出"""
        try:
            if progress_callback:
                progress_callback("准备导出环境...")
            
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                # 保存当前图片到临时文件
                temp_image_path = os.path.join(temp_dir, "temp_image.png")
                image.save(temp_image_path)
                
                # 保存区域数据到JSON文件，使用load_text模式期望的文件名格式
                base_name = os.path.splitext(os.path.basename(temp_image_path))[0]
                regions_json_path = os.path.join(temp_dir, f"{base_name}_translations.json")
                self._save_regions_data(regions_data, regions_json_path, mask)
                
                if progress_callback:
                    progress_callback("初始化翻译引擎...")
                
                # 准备翻译器参数
                translator_params = self._prepare_translator_params(config)
                
                # 创建翻译器实例并执行渲染
                result_image = self._execute_backend_render(
                    temp_image_path, regions_json_path, translator_params, config, progress_callback
                )
                
                if result_image:
                    # Handle RGBA to RGB conversion for JPEG
                    if output_path.lower().endswith(('.jpg', '.jpeg')):
                        self.logger.info("Output is JPEG, converting from RGBA to RGB...")
                        if result_image.mode == 'RGBA':
                            # Create a white background and paste the image onto it
                            background = Image.new('RGB', result_image.size, (255, 255, 255))
                            background.paste(result_image, mask=result_image.split()[3])  # 3 is the alpha channel
                            result_image = background
                            
                    # 保存结果图片
                    result_image.save(output_path)
                    
                    if success_callback:
                        success_callback(f"图片已导出到: {output_path}")
                    
                    self.logger.info(f"图片已成功导出到: {output_path}")
                else:
                    if error_callback:
                        error_callback("导出失败: 没有生成结果图片")
                    
        except Exception as e:
            self.logger.error(f"后端渲染导出失败: {e}")
            import traceback
            traceback.print_exc()
            
            if error_callback:
                error_callback(f"后端渲染导出失败: {e}")
    
    def _save_regions_data(self, regions_data: List[Dict[str, Any]], json_path: str, mask: Optional[np.ndarray] = None):
        """保存区域数据到JSON文件，确保格式与TextBlock兼容"""
        # 准备保存数据，确保数据格式正确
        save_data = []
        for region in regions_data:
            region_copy = region.copy()
            
            # 确保必要字段存在
            if 'translation' not in region_copy:
                region_copy['translation'] = region_copy.get('text', '')
            
            # 确保lines字段存在且格式正确
            if 'lines' not in region_copy:
                self.logger.warning(f"Region missing 'lines' field: {region_copy}")
                continue
            
            # 验证和转换lines数据格式
            lines_data = region_copy['lines']
            if isinstance(lines_data, list):
                # 确保每个多边形都有足够的点
                valid_polygons = []
                for poly in lines_data:
                    if isinstance(poly, list) and len(poly) >= 4:
                        # 确保每个点都是[x, y]格式
                        valid_points = []
                        for point in poly:
                            if isinstance(point, (list, tuple)) and len(point) >= 2:
                                valid_points.append([float(point[0]), float(point[1])])
                            else:
                                self.logger.warning(f"Invalid point format in polygon: {point}")
                                break
                        else:
                            if len(valid_points) >= 4:
                                # 确保是矩形格式（4个点）
                                if len(valid_points) == 4:
                                    valid_polygons.append(valid_points)
                                else:
                                    # 如果超过4个点，取前4个点
                                    self.logger.warning(f"Polygon has {len(valid_points)} points, using first 4")
                                    valid_polygons.append(valid_points[:4])
                    else:
                        self.logger.warning(f"Invalid polygon format: {poly}")
                
                if valid_polygons:
                    # 恢复到正确的 (N, 4, 2) 形状
                    region_copy['lines'] = np.array(valid_polygons, dtype=np.float64)
                else:
                    self.logger.warning(f"No valid polygons found in region: {region_copy}")
                    continue
            elif isinstance(lines_data, np.ndarray):
                # 如果已经是numpy数组，直接使用
                region_copy['lines'] = lines_data
            else:
                self.logger.warning(f"Lines data is not a list or numpy array: {type(lines_data)}")
                continue
            
            # 确保texts字段存在（TextBlock需要）
            if 'texts' not in region_copy:
                region_copy['texts'] = [region_copy.get('text', '')]
            
            # 确保其他必要字段存在
            if 'language' not in region_copy:
                region_copy['language'] = 'unknown'
            if 'font_size' not in region_copy:
                region_copy['font_size'] = 12
            if 'angle' not in region_copy:
                region_copy['angle'] = 0
            if 'target_lang' not in region_copy:
                region_copy['target_lang'] = 'CHS'  # 默认目标语言
            
            save_data.append(region_copy)
        
        # load_text模式期望的格式：字典，键为图片路径，值为包含regions的字典
        # 使用临时图片路径作为键
        image_key = os.path.splitext(os.path.basename(json_path.replace('_translations.json', '')))[0]
        formatted_data = {
            image_key: {
                'regions': save_data
            }
        }
        
        # 如果有蒙版数据，则添加到JSON中
        if mask is not None:
            self.logger.info("在导出JSON中加入预计算的蒙版。")
            formatted_data[image_key]['mask_raw'] = mask.tolist()
            formatted_data[image_key]['mask_is_refined'] = True

        # 添加调试信息
        self.logger.info(f"保存区域数据到: {json_path}")
        self.logger.info(f"区域数量: {len(save_data)}")
        for i, region in enumerate(save_data):
            lines = region.get('lines', [])
            shape = np.array(lines).shape if lines is not None else 'N/A'
            self.logger.info(f"区域 {i}: lines形状={shape}, translation='{region.get('translation', '')[:50]}...'")
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(formatted_data, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)
    
    def _prepare_translator_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """准备翻译器参数"""
        translator_params = {}
        
        # 提取字体路径
        render_config = config.get('render', {})
        font_filename = render_config.get('font_path')
        if font_filename:
            font_full_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), 'fonts', font_filename
            )
            font_full_path = os.path.abspath(font_full_path)
            if os.path.exists(font_full_path):
                translator_params['font_path'] = font_full_path
                self.logger.info(f"设置字体路径: {font_full_path}")
        
        # 提取输出格式
        output_format = self.get_output_format_from_config(config)
        if output_format:
            translator_params['format'] = output_format
            self.logger.info(f"设置输出格式: {output_format}")
        
        # 设置其他参数
        translator_params.update(config)
        translator_params['is_ui_mode'] = True
        translator_params['load_text'] = True  # 关键：启用加载文本模式
        translator_params['save_text'] = False  # 不保存文本
        
        # 关键：设置翻译器为none，跳过翻译步骤，直接渲染
        translator_params['translator'] = 'none'
        self.logger.info("设置翻译器为none，启用load_text模式，跳过翻译步骤，直接进行渲染")
        
        return translator_params
    
    def _execute_backend_render(self, image_path: str, regions_json_path: str, 
                              translator_params: Dict[str, Any], config: Dict[str, Any],
                              progress_callback: Optional[callable] = None) -> Optional[Image.Image]:
        """执行后端渲染"""
        try:
            from manga_translator.manga_translator import MangaTranslator
            from manga_translator.config import Config, RenderConfig
            
            if progress_callback:
                progress_callback("创建翻译器实例...")
            
            # 创建翻译器实例
            translator = MangaTranslator(params=translator_params)
            
            if progress_callback:
                progress_callback("加载图片和配置...")
            
            # 加载图片
            image = Image.open(image_path)
            image.name = image_path  # 确保图片名称正确，用于load_text模式查找翻译文件
            
            # 创建配置对象
            render_config = config.get('render', {})
            render_cfg = RenderConfig(**render_config)
            
            # 创建翻译器配置，设置为none以跳过翻译
            from manga_translator.config import TranslatorConfig
            translator_cfg = TranslatorConfig(translator='none')
            
            cfg = Config(render=render_cfg, translator=translator_cfg)
            
            if progress_callback:
                progress_callback("执行后端渲染...")
            
            # 执行翻译（实际是渲染）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                ctx = loop.run_until_complete(translator.translate(image, cfg, image_name=image.name))
                
                if ctx.result is not None:
                    # ctx.result is already a PIL Image object, no conversion needed.
                    result_image = ctx.result
                    return result_image
                else:
                    self.logger.error("后端渲染没有生成结果")
                    return None
                    
            finally:
                loop.close()
                
        except Exception as e:
            self.logger.error(f"执行后端渲染时出错: {e}")
            raise
    
    def export_regions_json(self, regions_data: List[Dict[str, Any]], output_path: str) -> bool:
        """导出区域数据为JSON文件"""
        try:
            self._save_regions_data(regions_data, output_path)
            self.logger.info(f"区域数据已导出到: {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"导出区域数据失败: {e}")
            return False


# 创建全局导出服务实例
_export_service = None

def get_export_service() -> ExportService:
    """获取导出服务实例"""
    global _export_service
    if _export_service is None:
        _export_service = ExportService()
    return _export_service
