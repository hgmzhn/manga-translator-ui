#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出服务
负责将编辑器中的内容导出为后端渲染的图片
"""

import asyncio
import copy
import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image
from editor.image_utils import image_like_to_pil
from utils.asyncio_cleanup import shutdown_event_loop
from utils.json_encoder import CustomJSONEncoder

from manga_translator.utils import open_pil_image, save_pil_image
from manga_translator.utils.path_manager import get_inpainted_path

# 全局输出目录存储
_global_output_directory = None

def set_global_output_directory(output_dir: str):
    """设置全局输出目录"""
    global _global_output_directory
    _global_output_directory = output_dir

def get_global_output_directory() -> Optional[str]:
    """获取全局输出目录"""
    return _global_output_directory


class ExportService:
    """导出服务类"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _build_backend_export_config(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """编辑器导出时直接渲染当前图像，不再重复跑上色/超分。"""
        export_config = copy.deepcopy(config) if config else {}
        upscale_config = export_config.setdefault('upscale', {})
        upscale_config['upscale_ratio'] = None
        colorizer_config = export_config.setdefault('colorizer', {})
        colorizer_config['colorizer'] = 'none'
        return export_config

    def _save_temp_inpainted_image(
        self,
        temp_image_path: str,
        editor_inpainted_image: Optional[Any],
        base_size=None,
        source_image: Optional[Image.Image] = None,
    ) -> Optional[str]:
        """将编辑器当前修复图临时落盘，供 load_text 导出流程直接复用。"""
        if editor_inpainted_image is None:
            return None

        temp_inpainted_path = get_inpainted_path(temp_image_path, create_dir=True)
        save_image = editor_inpainted_image
        owns_image = False
        try:
            if not isinstance(save_image, Image.Image):
                save_image = image_like_to_pil(save_image)
                owns_image = True
            if save_image is None:
                return None
            if base_size and save_image.size != base_size:
                resized_image = save_image.resize(base_size, Image.Resampling.LANCZOS)
                if owns_image:
                    save_image.close()
                save_image = resized_image
                owns_image = True
            if save_image.mode == 'CMYK':
                rgb_image = save_image.convert('RGB')
                if owns_image:
                    save_image.close()
                save_image = rgb_image
                owns_image = True

            save_pil_image(save_image, temp_inpainted_path, source_image=source_image)
            self.logger.info(f"已写入临时修复图供导出复用: {temp_inpainted_path}")
            return temp_inpainted_path
        finally:
            if owns_image and save_image is not None:
                save_image.close()

    def _persist_backend_inpainted_image(
        self,
        source_image_path: Optional[str],
        inpainted_image: Any,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """将后端实际生成的修复图回写到工作目录，避免下次编辑回退到原图底图。"""
        if not source_image_path or inpainted_image is None:
            return None

        save_image = None
        source_image = None
        try:
            if isinstance(inpainted_image, Image.Image):
                save_image = inpainted_image
            else:
                save_image = image_like_to_pil(inpainted_image)
                if save_image is None:
                    return None

            inpainted_path = get_inpainted_path(source_image_path, create_dir=True)
            save_quality = (config or {}).get('cli', {}).get('save_quality', 95)
            try:
                source_image = open_pil_image(source_image_path, eager=True)
            except Exception as metadata_error:
                self.logger.warning(f"读取原图元数据失败，将继续保存但不继承ICC: {source_image_path}, error={metadata_error}")
                source_image = None
            save_pil_image(
                save_image,
                inpainted_path,
                source_image=source_image,
                quality=save_quality,
            )
            self.logger.info(f"已回写导出后的修复图: {inpainted_path}")
            return inpainted_path
        except Exception as e:
            self.logger.warning(f"回写导出后的修复图失败: {e}")
            return None
        finally:
            if source_image is not None:
                try:
                    source_image.close()
                except Exception:
                    pass
            if save_image is not None and not isinstance(inpainted_image, Image.Image):
                try:
                    save_image.close()
                except Exception:
                    pass
    
    def get_output_directory(self) -> Optional[str]:
        """获取设置的输出目录"""
        # 首先检查全局存储的输出目录
        global_dir = get_global_output_directory()
        if global_dir and os.path.exists(global_dir):
            self.logger.info(f"使用全局输出目录: {global_dir}")
            return global_dir
        
        try:
            # 作为备选方案，尝试通过UI控件获取
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
                                # 更新全局存储
                                set_global_output_directory(output_dir)
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
    
    def generate_output_filename(self, original_image_path: str, output_format: str = "", add_prefix: bool = False) -> str:
        """生成输出文件名，可选择是否添加前缀"""
        base_name = os.path.splitext(os.path.basename(original_image_path))[0]
        
        # 根据参数决定是否添加前缀
        if add_prefix:
            output_name = f"translated_{base_name}"
        else:
            # 使用原始文件名（编辑器导出时的默认行为）
            output_name = base_name
        
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
                            error_callback: Optional[callable] = None,
                            source_image_path: Optional[str] = None,
                            save_inpainted_only: bool = False,
                            editor_inpainted_image: Optional[Any] = None):
        """
        导出后端渲染的图片
        
        Args:
            image: 当前图片（仅用于获取尺寸和模式信息）
            regions_data: 区域数据
            config: 配置字典
            output_path: 输出路径
            mask: (新增) 预计算的蒙版
            progress_callback: 进度回调
            success_callback: 成功回调
            error_callback: 错误回调
            source_image_path: 原图路径（用于PSD导出）
            save_inpainted_only: 是否只保存修复后的图片（不渲染翻译文字）
            editor_inpainted_image: 编辑器当前修复图，导出时优先直接复用
        """
        if not image:
            if error_callback:
                error_callback("没有图片可导出")
            return
        
        # regions_data 可以为空列表，此时导出原图（可能经过上色/超分处理）
        if regions_data is None:
            regions_data = []
        
        if progress_callback:
            progress_callback("开始导出渲染图片...")
        
        # 在后台线程中执行导出
        export_thread = threading.Thread(
            target=self._perform_backend_render_export,
            args=(image, regions_data, config, output_path, mask, progress_callback, success_callback, error_callback, source_image_path, save_inpainted_only, editor_inpainted_image),
            daemon=True
        )
        export_thread.start()
    
    def _perform_backend_render_export(self, image: Image.Image, regions_data: List[Dict[str, Any]],
                                     config: Dict[str, Any], output_path: str,
                                     mask: Optional[np.ndarray] = None,
                                     progress_callback: Optional[callable] = None,
                                     success_callback: Optional[callable] = None,
                                     error_callback: Optional[callable] = None,
                                     source_image_path: Optional[str] = None,
                                     save_inpainted_only: bool = False,
                                     editor_inpainted_image: Optional[Any] = None):
        """在后台线程中执行后端渲染导出"""
        import os
        
        temp_dir = None
        rendered_image = None
        
        try:
            self.logger.info(f"开始导出图片到: {output_path}")
            
            if progress_callback:
                progress_callback("准备导出环境...")

            # 验证输出路径
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            # 创建临时目录
            temp_dir = tempfile.mkdtemp()
            backend_config = self._build_backend_export_config(config)
            
            # 保存当前图片到临时文件
            temp_image_path = os.path.join(temp_dir, "temp_image.png")
            save_pil_image(image, temp_image_path, source_image=image)

            self._save_temp_inpainted_image(temp_image_path, editor_inpainted_image, image.size, source_image=image)
            
            # 保存区域数据到JSON文件
            base_name = os.path.splitext(os.path.basename(temp_image_path))[0]
            regions_json_path = os.path.join(temp_dir, f"{base_name}_translations.json")
            self._save_regions_data(regions_data, regions_json_path, mask, backend_config)
            
            if progress_callback:
                progress_callback("初始化翻译引擎...")
            
            # 准备翻译器参数
            translator_params = self._prepare_translator_params(backend_config)
            
            # 执行后端渲染
            rendered_image = self._execute_backend_render(
                temp_image_path, regions_json_path, translator_params, backend_config, progress_callback, output_path, source_image_path, save_inpainted_only
            )
            
            if not rendered_image:
                raise Exception("后端渲染没有生成结果")
            
            # 保存渲染结果
            self._save_rendered_image(rendered_image, output_path, config, source_image=image)
            
            self.logger.info(f"图片已成功导出到: {output_path}")
            
            if success_callback:
                success_callback(f"图片已导出到: {output_path}")

        except Exception as e:
            error_msg = f"后端渲染导出失败: {e}"
            self.logger.error(error_msg)
            import traceback
            self.logger.error(traceback.format_exc())
            if error_callback:
                error_callback(error_msg)
        
        finally:
            # 清理资源
            try:
                if rendered_image:
                    rendered_image.close()
            except Exception:
                pass
            
            try:
                if image:
                    image.close()
            except Exception:
                pass

            try:
                if editor_inpainted_image is not None:
                    editor_inpainted_image.close()
            except Exception:
                pass
            
            try:
                if temp_dir and os.path.exists(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            
            # 强制垃圾回收
            pass
            # 清理GPU显存
            try:
                import torch
                if torch.cuda.is_available():
                    pass
            except Exception:
                pass
    
    def _save_regions_data_with_path(self, regions_data: List[Dict[str, Any]], json_path: str, image_path: str, mask: Optional[np.ndarray] = None, config: Optional[Dict[str, Any]] = None):
        """保存区域数据到JSON文件，使用正确的图片路径作为键（用于编辑器保存）"""
        # 使用图片的绝对路径作为键，与加载时保持一致
        image_key = os.path.abspath(image_path)
        self._save_regions_data_internal(regions_data, json_path, image_key, mask, config)

    def _save_regions_data(self, regions_data: List[Dict[str, Any]], json_path: str, mask: Optional[np.ndarray] = None, config: Optional[Dict[str, Any]] = None):
        """保存区域数据到JSON文件，确保格式与TextBlock兼容（用于导出）"""
        # 使用文件名作为键（向后兼容）
        image_key = os.path.splitext(os.path.basename(json_path.replace('_translations.json', '')))[0]
        self._save_regions_data_internal(regions_data, json_path, image_key, mask, config)

    def _normalize_font_path_for_save(self, font_path: str) -> str:
        """Normalize font path to portable relative form when possible."""
        if not font_path:
            return ''

        from manga_translator.utils import BASE_PATH

        if os.path.isabs(font_path):
            norm_path = os.path.normpath(font_path)
            base_path = os.path.normpath(BASE_PATH)
            fonts_dir = os.path.normpath(os.path.join(base_path, 'fonts'))
            try:
                if os.path.commonpath([norm_path, fonts_dir]) == fonts_dir:
                    return os.path.relpath(norm_path, base_path).replace('\\', '/')
                if os.path.commonpath([norm_path, base_path]) == base_path:
                    return os.path.relpath(norm_path, base_path).replace('\\', '/')
            except ValueError:
                return norm_path
            return norm_path

        if font_path.lower().startswith('fonts/') or font_path.lower().startswith('fonts\\'):
            return font_path.replace('\\', '/')
        return f"fonts/{font_path}".replace('\\', '/')
    
    def _save_regions_data_internal(self, regions_data: List[Dict[str, Any]], json_path: str, image_key: str, mask: Optional[np.ndarray] = None, config: Optional[Dict[str, Any]] = None):
        """保存区域数据到JSON文件的内部实现"""
        # 获取超分倍率，用于放大坐标
        upscale_ratio = 1
        default_region_font_path = ''
        if config:
            upscale_config = config.get('upscale', {})
            upscale_ratio = upscale_config.get('upscale_ratio', 0) or 1
            render_config = config.get('render', {})
            default_region_font_path = self._normalize_font_path_for_save(render_config.get('font_path') or '')
        
        # 准备保存数据，确保数据格式正确
        save_data = []
        for idx, region in enumerate(regions_data):
            region_copy = region.copy()

            # 确保必要字段存在
            if 'translation' not in region_copy:
                region_copy['translation'] = region_copy.get('text', '')
            
            # 保留富文本信息（如果存在）
            # rich_text 字段包含 HTML 格式的文本，保存了字体大小、颜色、粗体、斜体等格式
            # 如果没有富文本格式，该字段为空字符串或不存在
            if 'rich_text' not in region_copy:
                region_copy['rich_text'] = ''
            
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
                # 如果已经是numpy数组，验证并修正形状
                lines_arr = lines_data
                if lines_arr.ndim == 2 and lines_arr.shape == (4, 2):
                    # 单个多边形，需要添加一个维度变成 (1, 4, 2)
                    lines_arr = lines_arr.reshape(1, 4, 2)
                    self.logger.debug("Reshaped lines from (4, 2) to (1, 4, 2)")
                elif lines_arr.ndim != 3 or lines_arr.shape[1] != 4 or lines_arr.shape[2] != 2:
                    self.logger.warning(f"Invalid lines array shape: {lines_arr.shape}, expected (N, 4, 2)")
                    continue
                region_copy['lines'] = lines_arr.astype(np.float64)
            else:
                self.logger.warning(f"Lines data is not a list or numpy array: {type(lines_data)}")
                continue
            
            # --- Foreground Color ---
            # 优先使用 font_color (hex格式),如果没有才使用 fg_colors/fg_color (tuple格式)
            if 'font_color' not in region_copy or region_copy['font_color'] is None:
                fg_tuple = region_copy.pop('fg_colors', None)
                if fg_tuple is None:
                    fg_tuple = region_copy.pop('fg_color', None) # Fallback for singular

                if isinstance(fg_tuple, (list, tuple)) and len(fg_tuple) == 3:
                    try:
                        r, g, b = fg_tuple
                        region_copy['font_color'] = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                    except (ValueError, TypeError) as e:
                        self.logger.warning(f"Could not convert fg_color tuple to hex for saving: {e}")
            else:
                # font_color 已存在,移除 fg_colors/fg_color 避免冲突
                region_copy.pop('fg_colors', None)
                region_copy.pop('fg_color', None)

            # --- Background/Stroke Color ---
            bg_tuple = region_copy.pop('bg_colors', None)
            if bg_tuple is None:
                bg_tuple = region_copy.pop('bg_color', None) # Fallback
            
            # Ensure bg_color (singular) is present in the final dict if it exists
            if bg_tuple:
                region_copy['bg_color'] = bg_tuple

            # 确保其他必要字段存在
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

            # 统一保存字体路径格式（优先相对路径）
            region_font_path = region_copy.get('font_path')
            if region_font_path:
                region_copy['font_path'] = self._normalize_font_path_for_save(region_font_path)

            # 区域未显式设置字体时，补全当前全局字体到区域字段
            if not region_copy.get('font_path') and default_region_font_path:
                region_copy['font_path'] = default_region_font_path
            
            # 转换 direction 值：'v' -> 'vertical', 'h' -> 'horizontal'
            if 'direction' in region_copy:
                direction_value = region_copy['direction']
                if direction_value == 'v':
                    region_copy['direction'] = 'vertical'
                elif direction_value == 'h':
                    region_copy['direction'] = 'horizontal'
            
            save_data.append(region_copy)
        
        # load_text模式期望的格式：字典，键为图片路径，值为包含regions的字典
        # image_key 由调用方传入（可以是完整路径或文件名）
        formatted_data = {
            image_key: {
                'regions': save_data
            }
        }
        
        # 添加超分和上色配置信息
        if config:
            upscale_config = config.get('upscale', {})
            upscale_ratio = upscale_config.get('upscale_ratio', 0)
            if upscale_ratio:
                formatted_data[image_key]['upscale_ratio'] = upscale_ratio
                upscaler = upscale_config.get('upscaler', '')
                if upscaler:
                    formatted_data[image_key]['upscaler'] = upscaler
                self.logger.info(f"在JSON中记录超分信息: ratio={upscale_ratio}, upscaler={upscaler}")
            
            colorizer_config = config.get('colorizer', {})
            colorizer = colorizer_config.get('colorizer', '')
            if colorizer and colorizer != 'none':
                formatted_data[image_key]['colorizer'] = colorizer
                self.logger.info(f"在JSON中记录上色信息: colorizer={colorizer}")
        
        # 如果有蒙版数据，则添加到JSON中
        if mask is not None:
            self.logger.info("在导出JSON中加入预计算的蒙版（已编辑的refined mask）。")
            # 使用base64编码保存蒙版，避免JSON文件过大
            import base64

            import cv2
            _, encoded_mask = cv2.imencode('.png', mask)
            mask_base64 = base64.b64encode(encoded_mask).decode('utf-8')
            formatted_data[image_key]['mask_raw'] = mask_base64
            formatted_data[image_key]['mask_is_refined'] = True  # 标记为已精炼的蒙版，跳过后端的蒙版优化
            self.logger.info("蒙版已保存（base64编码），标记为已精炼，后端将跳过蒙版优化")

        # 添加调试信息
        self.logger.info(f"保存区域数据到: {json_path}")
        self.logger.info(f"区域数量: {len(save_data)}")
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(formatted_data, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)
    
    def _save_rendered_image(
        self,
        image: Image.Image,
        output_path: str,
        config: Dict[str, Any],
        source_image: Optional[Image.Image] = None,
    ):
        """
        保存渲染后的图像到文件
        
        Args:
            image: 要保存的图像
            output_path: 输出路径
            config: 配置字典
        """
        temp_output_path = output_path + ".tmp"
        
        try:
            output_lower = output_path.lower()
            save_quality = config.get('cli', {}).get('save_quality', 95)
            
            # 需要转换为RGB的格式（不支持透明度或CMYK）
            if output_lower.endswith(('.jpg', '.jpeg')):
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    quality=save_quality,
                    format='JPEG',
                )
                
            elif output_lower.endswith('.webp'):
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    quality=save_quality,
                    format='WEBP',
                )
                
            elif output_lower.endswith('.avif'):
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    quality=save_quality,
                    format='AVIF',
                )
                
            elif output_lower.endswith(('.heic', '.heif')):
                # HEIC/HEIF格式：需要 pillow-heif 库支持
                try:
                    import pillow_heif
                    # 注册 HEIF 插件
                    pillow_heif.register_heif_opener()
                    save_pil_image(
                        image,
                        temp_output_path,
                        source_image=source_image,
                        quality=save_quality,
                        format='HEIF',
                    )
                except ImportError:
                    self.logger.warning("HEIC/HEIF 格式需要安装 pillow-heif 库，降级为 PNG 格式")
                    # 修改输出路径为 PNG
                    temp_output_path = output_path.rsplit('.', 1)[0] + '.png.tmp'
                    output_path = output_path.rsplit('.', 1)[0] + '.png'
                    save_pil_image(
                        image,
                        temp_output_path,
                        source_image=source_image,
                        format='PNG',
                    )
                
            elif output_lower.endswith('.bmp'):
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    format='BMP',
                )
                
            elif output_lower.endswith(('.tiff', '.tif')):
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    format='TIFF',
                )
                
            else:
                save_pil_image(
                    image,
                    temp_output_path,
                    source_image=source_image,
                    format='PNG',
                )
            
            # 确保文件已写入
            if not os.path.exists(temp_output_path):
                raise Exception(f"临时文件未成功创建: {temp_output_path}")
            
            # 原子性替换
            os.replace(temp_output_path, output_path)
            self.logger.info(f"图片已保存: {output_path}")
            
        except Exception as e:
            self.logger.error(f"保存图片失败: {e}")
            # 清理临时文件
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            raise
            raise
    
    def _prepare_translator_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """准备翻译器参数"""
        translator_params = {}
        
        # 字体路径透传：后端会在渲染时解析路径并回退默认字体
        render_config = config.get('render', {})
        font_path_value = render_config.get('font_path')
        if font_path_value:
            translator_params['font_path'] = font_path_value
            self.logger.info(f"透传字体路径: {font_path_value}")
        else:
            self.logger.info("未设置全局字体路径，使用区域字体或后端默认字体")
        
        # 提取输出格式
        output_format = self.get_output_format_from_config(config)
        if output_format:
            translator_params['format'] = output_format
            self.logger.info(f"设置输出格式: {output_format}")
        
        # 提取并传递GPU配置
        cli_config = config.get('cli', {})
        if 'use_gpu' in cli_config:
            translator_params['use_gpu'] = cli_config['use_gpu']
            self.logger.info(f"设置GPU配置: use_gpu={cli_config['use_gpu']}")
        
        # 设置其他参数
        translator_params.update(config)
        translator_params['load_text'] = True  # 关键：启用加载文本模式
        translator_params['save_text'] = False  # 不保存文本
        translator_params['skip_text_replacements'] = True  # 编辑器导出跳过文本替换规则
        
        # 添加调试日志
        self.logger.info(f"Config keys: {list(config.keys())}")
        if 'upscale' in config:
            self.logger.info(f"Upscale config: {config['upscale']}")
        else:
            self.logger.warning("No upscale config found in config")
        if 'colorizer' in config:
            self.logger.info(f"Colorizer config: {config['colorizer']}")
        else:
            self.logger.warning("No colorizer config found in config")
        
        # 关键：设置翻译器为none，跳过翻译步骤，直接渲染
        translator_params['translator'] = 'none'
        self.logger.info("设置翻译器为none，启用load_text模式，跳过翻译步骤，直接进行渲染")
        
        return translator_params
    
    def _execute_backend_render(self, image_path: str, regions_json_path: str,
                              translator_params: Dict[str, Any], config: Dict[str, Any],
                              progress_callback: Optional[callable] = None,
                              output_path: str = None,
                              source_image_path: str = None,
                              save_inpainted_only: bool = False) -> Optional[Image.Image]:
        """执行后端渲染"""
        image = None
        try:
            from manga_translator.config import Config, RenderConfig
            from manga_translator.manga_translator import MangaTranslator

            if progress_callback:
                progress_callback("创建翻译器实例...")

            # 创建翻译器实例
            translator = MangaTranslator(params=translator_params)

            if progress_callback:
                progress_callback("加载图片和配置...")

            # 加载图片
            image = open_pil_image(image_path, eager=True)
            image.name = image_path  # 确保图片名称正确，用于load_text模式查找翻译文件

            # 创建配置对象
            render_config = config.get('render', {}).copy()  # 使用copy避免修改原配置
            
            # 转换 direction 值：'v' -> 'vertical', 'h' -> 'horizontal'
            if 'direction' in render_config:
                direction_value = render_config['direction']
                if direction_value == 'v':
                    render_config['direction'] = 'vertical'
                elif direction_value == 'h':
                    render_config['direction'] = 'horizontal'
            
            render_config['font_color'] = None # Explicitly disable global font color
            render_cfg = RenderConfig(**render_config)

            # 创建翻译器配置，设置为none以跳过翻译
            from manga_translator.config import (
                ColorizerConfig,
                InpainterConfig,
                TranslatorConfig,
                UpscaleConfig,
            )
            translator_cfg = TranslatorConfig(translator='none')
            
            # 从config中提取upscale、colorizer、inpainter和cli配置
            upscale_config = config.get('upscale', {})
            colorizer_config = config.get('colorizer', {})
            inpainter_config = config.get('inpainter', {})
            cli_config = config.get('cli', {})
            upscale_cfg = UpscaleConfig(**upscale_config) if upscale_config else UpscaleConfig()
            colorizer_cfg = ColorizerConfig(**colorizer_config) if colorizer_config else ColorizerConfig()
            inpainter_cfg = InpainterConfig(**inpainter_config) if inpainter_config else InpainterConfig()
            
            # 创建CliConfig对象（包含PSD导出配置）
            from manga_translator.config import CliConfig
            cli_cfg = CliConfig(**cli_config) if cli_config else CliConfig()
            
            self.logger.info(f"Creating Config with upscale_ratio={upscale_cfg.upscale_ratio}, colorizer={colorizer_cfg.colorizer}, inpainting_size={inpainter_cfg.inpainting_size}")
            self.logger.info(f"PSD导出配置: export_editable_psd={cli_cfg.export_editable_psd}, psd_font={cli_cfg.psd_font}, psd_script_only={cli_cfg.psd_script_only}")

            cfg = Config(render=render_cfg, translator=translator_cfg, upscale=upscale_cfg, colorizer=colorizer_cfg, inpainter=inpainter_cfg, cli=cli_cfg)

            if progress_callback:
                progress_callback("执行后端渲染...")

            # 执行翻译（实际是渲染）
            import sys
            # 在Windows上的工作线程中，需要手动初始化Windows Socket
            if sys.platform == 'win32':
                # 使用ctypes直接调用WSAStartup
                import ctypes
                try:
                    WSADATA_SIZE = 400
                    wsa_data = ctypes.create_string_buffer(WSADATA_SIZE)
                    ws2_32 = ctypes.WinDLL('ws2_32')
                    ws2_32.WSAStartup(0x0202, wsa_data)
                except Exception:
                    pass
                
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                ctx = loop.run_until_complete(translator.translate(image, cfg, image_name=image.name))
                translation_error = getattr(ctx, 'translation_error', None) or getattr(ctx, 'error', None)
                if translation_error:
                    raise RuntimeError(f"translator.translate returned translation_error: {translation_error}")
                self._persist_backend_inpainted_image(
                    source_image_path=source_image_path,
                    inpainted_image=getattr(ctx, 'img_inpainted', None),
                    config=config,
                )

                # 根据参数决定返回inpainted还是result
                if save_inpainted_only:
                    # 只保存修复后的图片（不渲染翻译文字）
                    # 注意：需要在批次清理之前获取img_inpainted
                    if hasattr(ctx, 'img_inpainted') and ctx.img_inpainted is not None:
                        # 将numpy数组转换为PIL Image（立即复制，避免被清理）
                        import cv2
                        import numpy as np
                        inpainted_copy = np.copy(ctx.img_inpainted)  # 立即复制
                        inpainted_bgr = cv2.cvtColor(inpainted_copy, cv2.COLOR_RGB2BGR)
                        inpainted_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
                        result_image = Image.fromarray(inpainted_rgb)
                        
                        self.logger.info("返回修复后的图片（inpainted）")
                    else:
                        self.logger.warning("ctx.img_inpainted不存在，回退到result")
                        if ctx.result is not None:
                            result_image = ctx.result.copy()
                        else:
                            return None
                else:
                    # 返回翻译后的图片（带翻译文字）
                    if ctx.result is not None:
                        result_image = ctx.result.copy()
                    else:
                        return None
                
                # 导出可编辑PSD（如果启用）
                if cfg.cli.export_editable_psd and not save_inpainted_only:
                        try:
                            from manga_translator.utils.photoshop_export import (
                                get_psd_output_path,
                                photoshop_export,
                            )
                            
                            # 优先使用原图路径生成PSD路径，其次使用输出路径，最后使用临时路径
                            if source_image_path:
                                # 使用原图路径生成PSD路径（正确的做法）
                                psd_path = get_psd_output_path(source_image_path)
                            elif output_path:
                                # 如果没有原图路径，使用输出路径
                                psd_path = get_psd_output_path(output_path)
                            else:
                                # 如果都没有，使用临时路径（向后兼容）
                                psd_path = get_psd_output_path(image_path)
                            
                            default_font = cfg.cli.psd_font
                            line_spacing = cfg.render.line_spacing if hasattr(cfg.render, 'line_spacing') else None
                            script_only = cfg.cli.psd_script_only
                            
                            # 使用原图路径查找inpainted图片，而不是临时路径
                            image_path_for_psd = source_image_path if source_image_path else (output_path if output_path else image_path)
                            
                            self.logger.info(f"开始导出PSD: {psd_path}")
                            self.logger.info(f"使用图片路径查找inpainted: {image_path_for_psd}")
                            photoshop_export(psd_path, ctx, default_font, image_path_for_psd, False, None, line_spacing, script_only)
                            self.logger.info(f"✅ [PSD] 已导出可编辑PSD: {os.path.basename(psd_path)}")
                            
                            if progress_callback:
                                progress_callback(f"已导出PSD: {os.path.basename(psd_path)}")
                        except Exception as psd_err:
                            self.logger.error(f"导出PSD失败: {psd_err}")
                            import traceback
                            self.logger.error(traceback.format_exc())
                
                # 关闭原始结果图像
                if hasattr(ctx, 'result') and ctx.result is not None:
                    try:
                        ctx.result.close()
                    except Exception as close_error:
                        self.logger.error(f"关闭ctx.result失败: {close_error}")
                
                # 关闭输入图像以释放内存
                if image:
                    try:
                        image.close()
                        image = None
                    except Exception as close_error:
                        self.logger.error(f"关闭输入图像失败: {close_error}")
                
                return result_image

            except Exception as translate_error:
                self.logger.error(f"translator.translate执行失败: {translate_error}")
                self.logger.error(f"错误类型: {type(translate_error).__name__}")
                import traceback
                self.logger.error(f"完整堆栈:\n{traceback.format_exc()}")
                raise
            finally:
                shutdown_event_loop(loop, logger=self.logger, label="backend export loop")

        except Exception as e:
            self.logger.error(f"执行后端渲染时出错: {type(e).__name__}: {e}")
            import traceback
            self.logger.error(f"完整堆栈:\n{traceback.format_exc()}")
            raise
        finally:
            # 确保输入图像被关闭
            if image:
                try:
                    image.close()
                except Exception as close_error:
                    self.logger.error(f"finally块中关闭输入图像失败: {close_error}")
    
    def export_regions_json(self, regions_data: List[Dict[str, Any]], output_path: str, config: Optional[Dict[str, Any]] = None) -> bool:
        """导出区域数据为JSON文件"""
        try:
            self._save_regions_data(regions_data, output_path, None, config)
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


