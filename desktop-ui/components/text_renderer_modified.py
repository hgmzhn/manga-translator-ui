"""
文本渲染器
负责WYSIWYG渲染、后端渲染集成和文本显示
"""
import customtkinter as ctk
from PIL import Image, ImageTk, ImageFont
import numpy as np
import os
import sys
import time
import hashlib
import math
from typing import Dict, Any, List, Optional, Tuple

# 导入editing_logic模块用于手柄位置计算
import editing_logic

# 尝试导入后端渲染模块
try:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from manga_translator.rendering import text_render
    from manga_translator import BASE_PATH
    BACKEND_RENDER_AVAILABLE = True
except ImportError as e:
    print(f"后端渲染模块导入失败: {e}")
    text_render = None
    BACKEND_RENDER_AVAILABLE = False


class TextRenderer:
    """文本渲染器"""
    
    def __init__(self, canvas: ctk.CTkCanvas):
        self.canvas = canvas
        
        # 渲染设置
        self.wysiwyg_mode: bool = True
        self.backend_available = BACKEND_RENDER_AVAILABLE
        
        # 缓存管理
        self.render_cache: Dict[str, Image.Image] = {}
        self.max_cache_size = 50
        
        # 外部服务
        self.param_service = None
        
        # 字体管理
        self.default_font_path = None
        self._initialize_fonts()
    
    def _initialize_fonts(self):
        """初始化字体 - 确保与后端使用完全相同的字体处理逻辑"""
        if self.backend_available and text_render:
            try:
                # 设置默认字体，与后端保持一致
                # 优先使用NotoSansCJK字体，这是后端默认字体
                preferred_fonts = [
                    os.path.join(BASE_PATH, 'fonts/NotoSansCJK-VF.ttc'),
                    os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttc'),
                    os.path.join(BASE_PATH, 'fonts/Arial-Unicode-Regular.ttf'),
                    os.path.join(BASE_PATH, 'fonts/msyh.ttc'),
                    os.path.join(BASE_PATH, 'fonts/msgothic.ttc'),
                ]
                
                # 查找第一个存在的字体文件
                selected_font = None
                for font_path in preferred_fonts:
                    if os.path.exists(font_path):
                        selected_font = font_path
                        break
                
                if selected_font:
                    text_render.set_font(selected_font)
                    self.default_font_path = selected_font
                    print(f"设置渲染字体: {selected_font}")
                else:
                    print("未找到默认字体文件，使用后端默认字体")
            except Exception as e:
                print(f"初始化后端渲染字体失败: {e}")
    
    def set_param_service(self, param_service):
        """设置参数服务"""
        self.param_service = param_service
    
    def set_wysiwyg_mode(self, enabled: bool):
        """设置所见即所得模式"""
        if self.wysiwyg_mode != enabled:
            self.wysiwyg_mode = enabled
            self.clear_cache()
            print(f"WYSIWYG模式: {'启用' if enabled else '禁用'}")
    
    def update_font_config(self, font_filename: str):
        """更新字体配置"""
        if not font_filename:
            return
            
        # 构建完整的字体路径
        full_font_path = os.path.join(os.path.dirname(__file__), '..', '..', 'fonts', font_filename)
        full_font_path = os.path.abspath(full_font_path)
        
        if os.path.exists(full_font_path):
            self.default_font_path = full_font_path
            
            # 如果后端渲染可用，也更新后端字体
            if self.backend_available and text_render:
                try:
                    text_render.set_font(full_font_path)
                    print(f"[TextRenderer] 字体已更新: {full_font_path}")
                except Exception as e:
                    print(f"[TextRenderer] 更新后端字体失败: {e}")
            
            # 清除缓存以强制重新渲染
            self.clear_cache()
        else:
            print(f"[TextRenderer] 字体文件不存在: {full_font_path}")
    
    def draw_region(self, region: Dict[str, Any], index: int, canvas: ctk.CTkCanvas, 
                   zoom_level: float, is_selected: bool = False):
        """绘制文本区域"""
        # 绘制区域边框
        self._draw_region_border(region, index, canvas, zoom_level, is_selected)
        
        # 绘制文本内容
        text_to_draw = region.get('translation', region.get('text', ''))
        if text_to_draw:
            if self.wysiwyg_mode and self.backend_available:
                self._render_text_wysiwyg(region, index, text_to_draw, canvas, zoom_level)
            else:
                self._render_text_simple(region, index, text_to_draw, canvas, zoom_level)
    
    def _draw_region_border(self, region: Dict[str, Any], index: int, 
                           canvas: ctk.CTkCanvas, zoom_level: float, is_selected: bool):
        """绘制区域边框"""
        outline_color = "cyan" if is_selected else "green"
        fill_color = "#00A0A0" if is_selected else "#008000"
        rotation_angle = region.get('angle', 0)
        
        # 计算区域边界
        all_coords_flat = [c for poly in region.get('lines', []) for point in poly for c in point]
        if not all_coords_flat:
            return
            
        min_x, max_x = min(all_coords_flat[::2]), max(all_coords_flat[::2])
        min_y, max_y = min(all_coords_flat[1::2]), max(all_coords_flat[1::2])
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        
        # 绘制多边形边框
        for polygon_coords in region.get('lines', []):
            rotated_coords = [
                self._rotate_point(x, y, rotation_angle, center_x, center_y) 
                for x, y in polygon_coords
            ]
            canvas_poly = [[c * zoom_level for c in p] for p in rotated_coords]
            flat_coords = [c for point in canvas_poly for c in point]
            
            poly_id = canvas.create_polygon(
                flat_coords, 
                outline=outline_color, 
                fill=fill_color, 
                width=2, 
                stipple='gray25', 
                tags=(f"region_{index}", "text_region")
            )
            
            # 保存画布项目引用
            if 'canvas_items' not in region:
                region['canvas_items'] = []
            region['canvas_items'].append(poly_id)
        
        # 如果区域被选中，绘制手柄
        if is_selected:
            # 导入editing_logic模块用于计算手柄位置
            import editing_logic
            
            # 绘制旋转手柄
            rot_handle_x = center_x * zoom_level
            rot_handle_y = (min_y - 20) * zoom_level
            rot_handle_id = canvas.create_oval(
                rot_handle_x - 5, rot_handle_y - 5,
                rot_handle_x + 5, rot_handle_y + 5,
                fill="cyan", outline="black", width=1,
                tags=(f"region_{index}", "rot_handle")
            )
            # 保存画布项目引用
            if 'canvas_items' not in region:
                region['canvas_items'] = []
            region['canvas_items'].append(rot_handle_id)
            
            # 绘制缩放手柄（仅对矩形区域）
            if len(region.get('lines', [])) == 1 and len(region['lines'][0]) == 4:
                vertices = region['lines'][0]
                # 计算缩放手柄位置
                handle_positions = editing_logic.get_scale_handle_positions(vertices)
                for i, (hx, hy) in enumerate(handle_positions):
                    handle_x = hx * zoom_level
                    handle_y = hy * zoom_level
                    handle_id = canvas.create_rectangle(
                        handle_x - 4, handle_y - 4,
                        handle_x + 4, handle_y + 4,
                        fill="cyan", outline="black", width=1,
                        tags=(f"region_{index}", f"scale_handle", str(i))
                    )
                    # 保存画布项目引用
                    if 'canvas_items' not in region:
                        region['canvas_items'] = []
                    region['canvas_items'].append(handle_id)
    
    def _render_text_wysiwyg(self, region: Dict[str, Any], index: int, text_to_draw: str,
                            canvas: ctk.CTkCanvas, zoom_level: float):
        """使用后端渲染引擎进行WYSIWYG渲染"""
        try:
            # 生成缓存键
            cache_key = self._generate_text_cache_key(region, text_to_draw, zoom_level)
            
            # 检查缓存
            if cache_key in self.render_cache:
                rendered_image = self.render_cache[cache_key]
            else:
                # 执行后端渲染
                rendered_image = self._execute_backend_render(region, text_to_draw, zoom_level)
                if rendered_image is not None:
                    # 缓存结果
                    self._cache_rendered_image(cache_key, rendered_image)
            
            if rendered_image is not None:
                # 显示渲染结果
                self._display_rendered_text(rendered_image, region, index, canvas, zoom_level)
            else:
                # 回退到简单渲染
                self._render_text_simple(region, index, text_to_draw, canvas, zoom_level)
                
        except Exception as e:
            print(f"WYSIWYG渲染失败: {e}")
            self._render_text_simple(region, index, text_to_draw, canvas, zoom_level)
    
    def _render_text_simple(self, region: Dict[str, Any], index: int, text_to_draw: str,
                           canvas: ctk.CTkCanvas, zoom_level: float):
        """简单文本渲染"""
        # 计算中心点
        bbox = self._get_bounding_box(region)
        if not bbox:
            return
            
        center_x, center_y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        
        # 获取字体
        font_size = int(region.get('font_size', 12) * zoom_level)
        font_size = max(font_size, 4)
        
        try:
            # 如果有自定义字体路径并且文件存在，则使用它
            if self.default_font_path and os.path.exists(self.default_font_path):
                font = ctk.CTkFont(family=self.default_font_path, size=font_size)
            else:
                # 否则使用默认字体
                font = ctk.CTkFont(size=font_size)
        except Exception as e:
            print(f"创建字体失败 (路径: {self.default_font_path})，使用备用字体。错误: {e}")
            font = ctk.CTkFont(size=12)
        
        # 绘制文本
        text_id = canvas.create_text(
            center_x * zoom_level, 
            center_y * zoom_level, 
            text=text_to_draw, 
            font=font, 
            fill="white", 
            anchor="center", 
            tags=(f"region_{index}", "text_content")
        )
        
        # 保存画布项目引用
        if 'canvas_items' not in region:
            region['canvas_items'] = []
        region['canvas_items'].append(text_id)
    
    def _execute_backend_render(self, region: Dict[str, Any], text_to_draw: str, 
                               zoom_level: float) -> Optional[Image.Image]:
        """执行后端渲染"""
        try:
            # 获取区域尺寸（使用原始坐标，避免重复缩放）
            bbox = self._get_bounding_box(region)
            if not bbox:
                print("无法获取区域边界框")
                return None
            
            # 使用原始尺寸，不应用zoom_level（渲染时会根据显示需要缩放）
            width = int(bbox[2] - bbox[0])
            height = int(bbox[3] - bbox[1])
            
            if width <= 0 or height <= 0:
                print(f"无效的渲染尺寸: {width}x{height}")
                return None
            
            # 获取渲染参数
            params = self._get_render_parameters(region)
            
            # 检查文本内容
            if not text_to_draw or not text_to_draw.strip():
                print("渲染文本为空")
                return None
            
            # 检查后端渲染模块是否可用
            if not self.backend_available or not text_render:
                print("后端渲染模块不可用")
                return None
            
            # 判断文本方向（与后端保持一致的判断逻辑）
            # 参考后端rendering/__init__.py中的判断逻辑
            is_horizontal = True  # 默认水平
            direction_setting = region.get('direction', 'auto')
            
            if direction_setting != "auto":
                # 强制方向设置
                if direction_setting in ["horizontal", "h"]:
                    is_horizontal = True
                elif direction_setting in ["vertical", "v"]:
                    is_horizontal = False
            else:
                # 自动判断方向，与后端保持一致
                # 使用宽高比判断，与后端逻辑一致
                aspect_ratio = width / height if height > 0 else 1
                is_horizontal = aspect_ratio >= 1
            
            rendered_text = None
            
            if is_horizontal:
                # 水平文本渲染（与后端参数完全一致）
                try:
                    rendered_text = text_render.put_text_horizontal(
                        font_size=params['font_size'],
                        text=text_to_draw,
                        width=width,
                        height=height,
                        alignment=params['alignment'],
                        reversed_direction=params['reversed_direction'],
                        fg=params['fg_color'],
                        bg=params['bg_color'],
                        lang=params['lang'],
                        hyphenate=params['hyphenate'],
                        line_spacing=params['line_spacing']
                    )
                except Exception as e:
                    print(f"水平渲染失败: {e}")
                    return None
            else:
                # 垂直文本渲染（与后端参数完全一致）
                try:
                    rendered_text = text_render.put_text_vertical(
                        font_size=params['font_size'],
                        text=text_to_draw,
                        h=height,
                        alignment=params['alignment'],
                        fg=params['fg_color'],
                        bg=params['bg_color'],
                        line_spacing=params['line_spacing']
                    )
                except Exception as e:
                    print(f"垂直渲染失败: {e}")
                    return None
            
            # 检查渲染结果
            if rendered_text is None:
                print("后端渲染返回空结果")
                return None
            
            # 检查是否有bitmap属性
            if not hasattr(rendered_text, 'bitmap'):
                # 如果没有bitmap属性，可能是直接返回的数组
                if hasattr(rendered_text, 'shape') and len(rendered_text.shape) > 0:
                    # 直接使用数组
                    bitmap_data = rendered_text
                else:
                    print("渲染结果格式不支持")
                    return None
            else:
                # 使用bitmap属性
                bitmap_data = rendered_text.bitmap
            
            # 检查数组有效性
            if bitmap_data is None:
                print("渲染数据为空")
                return None
                
            if not hasattr(bitmap_data, 'shape') or len(bitmap_data.shape) == 0:
                print("无效的渲染数据格式")
                return None
            
            if bitmap_data.size == 0:
                print("渲染数据大小为零")
                return None
            
            # 转换为PIL图像
            try:
                if len(bitmap_data.shape) == 3 and bitmap_data.shape[2] == 4:
                    # RGBA格式
                    pil_image = Image.fromarray(bitmap_data.astype(np.uint8), 'RGBA')
                elif len(bitmap_data.shape) == 3 and bitmap_data.shape[2] == 3:
                    # RGB格式
                    pil_image = Image.fromarray(bitmap_data.astype(np.uint8), 'RGB')
                elif len(bitmap_data.shape) == 2:
                    # 灰度图，转换为RGB
                    rgb_data = np.stack([bitmap_data] * 3, axis=-1)
                    pil_image = Image.fromarray(rgb_data.astype(np.uint8), 'RGB')
                else:
                    print(f"不支持的数据形状: {bitmap_data.shape}")
                    return None
                
                # 验证图像有效性
                if pil_image.size[0] == 0 or pil_image.size[1] == 0:
                    print(f"生成的图像尺寸无效: {pil_image.size}")
                    return None
                
                return pil_image
                
            except Exception as e:
                print(f"转换PIL图像失败: {e}")
                return None
            
        except Exception as e:
            print(f"后端渲染执行失败: {e}")
            return None
    
    def _get_render_parameters(self, region: Dict[str, Any]) -> Dict[str, Any]:
        """获取渲染参数 - 完善参数映射，确保与后端put_text_horizontal/vertical函数参数格式完全一致"""
        if self.param_service:
            # 获取区域索引
            region_index = None
            if hasattr(self.param_service, 'get_region_parameters'):
                try:
                    params = self.param_service.get_region_parameters(region_index, region)
                    # 修正行间距计算，与后端保持一致
                    line_spacing = params.line_spacing
                    # 后端水平文本默认行间距为0.01，垂直文本默认为0.2
                    # 前端需要根据文本方向调整行间距计算方式
                    return {
                        'font_size': max(int(params.font_size), 8),
                        'alignment': params.alignment,
                        'fg_color': self._parse_color(params.fg_color),
                        'bg_color': self._parse_color(params.bg_color),
                        'line_spacing': line_spacing,  # 保持原始值，后端会处理
                        'hyphenate': params.hyphenate,
                        # 添加后端需要的其他参数
                        'reversed_direction': False,  # 默认从左到右
                        'lang': 'zh_CN',  # 默认语言
                    }
                except Exception as e:
                    print(f"获取参数服务数据失败: {e}")
        
        # 使用默认参数，确保与后端默认值一致
        font_color = region.get('font_color', '#FFFFFF')
        bg_color = region.get('bg_color', None)  # 默认无描边
        
        # 获取行间距设置，与后端保持一致的默认值
        line_spacing = region.get('line_spacing', 0)  # 默认值与后端一致
        
        return {
            'font_size': max(int(region.get('font_size', 12)), 8),
            'alignment': self._normalize_alignment(region.get('alignment', 'center')),
            'fg_color': self._parse_color(font_color),
            'bg_color': self._parse_color(bg_color) if bg_color else None,
            'line_spacing': line_spacing,  # 保持原始值，后端会处理
            'hyphenate': region.get('hyphenate', False),
            'reversed_direction': False,  # 默认从左到右
            'lang': 'zh_CN',  # 默认语言
        }
    
    def _parse_color(self, color) -> tuple:
        """解析颜色为(R, G, B)元组"""
        try:
            if isinstance(color, tuple) and len(color) >= 3:
                return (int(color[0]), int(color[1]), int(color[2]))
            elif isinstance(color, str):
                if color.startswith('#'):
                    # 十六进制颜色
                    hex_color = color[1:]
                    if len(hex_color) == 6:
                        return (
                            int(hex_color[0:2], 16),
                            int(hex_color[2:4], 16),
                            int(hex_color[4:6], 16)
                        )
                    elif len(hex_color) == 3:
                        return (
                            int(hex_color[0] * 2, 16),
                            int(hex_color[1] * 2, 16),
                            int(hex_color[2] * 2, 16)
                        )
                # 默认白色
                return (255, 255, 255)
            else:
                return (255, 255, 255)
        except Exception as e:
            print(f"颜色解析失败: {color}, 错误: {e}")
            return (255, 255, 255)
    
    def _normalize_alignment(self, alignment: str) -> str:
        """标准化对齐方式"""
        alignment_map = {
            '左对齐': 'left',
            '居中': 'center', 
            '右对齐': 'right',
            'left': 'left',
            'center': 'center',
            'right': 'right'
        }
        return alignment_map.get(alignment, 'center')
    
    def _display_rendered_text(self, rendered_image: Image.Image, region: Dict[str, Any], 
                              index: int, canvas: ctk.CTkCanvas, zoom_level: float):
        """显示渲染的文本图像 - 统一坐标计算系统，使其与后端put_char_horizontal/vertical的坐标体系保持一致"""
        try:
            # 获取区域边界框
            bbox = self._get_bounding_box(region)
            if not bbox:
                return
            
            # 使用后端渲染图像的原始尺寸，不进行额外的缩放和居中处理
            # 后端已经根据文本框尺寸进行了适当的渲染
            image_width, image_height = rendered_image.size
            
            # 获取文本框的左上角坐标
            box_x = bbox[0]
            box_y = bbox[1]
            
            # 直接在文本框的左上角位置显示渲染图像，不进行额外的缩放
            # 这样可以确保前后端渲染位置一致
            image_id = canvas.create_image(
                box_x * zoom_level,  # 应用zoom_level进行最终显示缩放
                box_y * zoom_level,  # 应用zoom_level进行最终显示缩放
                image=ImageTk.PhotoImage(rendered_image),
                anchor="nw",  # 左上角对齐
                tags=(f"region_{index}", "text_content")
            )
            
            # 保存引用防止垃圾回收
            if 'canvas_items' not in region:
                region['canvas_items'] = []
            region['canvas_items'].append(image_id)
            
        except Exception as e:
            print(f"显示渲染文本失败: {e}")
    
    def _generate_text_cache_key(self, region: Dict[str, Any], text_to_draw: str, 
                                zoom_level: float) -> str:
        """生成文本渲染缓存键"""
        try:
            bbox = self._get_bounding_box(region)
            
            # 获取参数信息
            params = self._get_render_parameters(region)
            param_hash = hash(str(sorted(params.items())))
            
            key_data = {
                'text': text_to_draw,
                'param_hash': param_hash,
                'angle': region.get('angle', 0),
                'zoom': round(zoom_level, 2),
                'bbox': bbox,
                'wysiwyg': self.wysiwyg_mode
            }
            
            cache_str = str(sorted(key_data.items()))
            return hashlib.md5(cache_str.encode()).hexdigest()[:16]
            
        except Exception:
            return f"fallback_{hash(text_to_draw)}_{time.time():.0f}"
    
    def _cache_rendered_image(self, cache_key: str, image: Image.Image):
        """缓存渲染图像"""
        # 限制缓存大小
        if len(self.render_cache) >= self.max_cache_size:
            # 移除最旧的缓存项
            oldest_key = next(iter(self.render_cache))
            del self.render_cache[oldest_key]
        
        self.render_cache[cache_key] = image
    
    def _get_bounding_box(self, region: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
        """获取区域边界框"""
        try:
            all_coords = [c for poly in region.get('lines', []) for point in poly for c in point]
            if not all_coords:
                return None
            min_x, max_x = min(all_coords[::2]), max(all_coords[::2])
            min_y, max_y = min(all_coords[1::2]), max(all_coords[1::2])
            return min_x, min_y, max_x, max_y
        except:
            return None
    
    def _rotate_point(self, x: float, y: float, angle: float, cx: float, cy: float) -> Tuple[float, float]:
        """旋转点"""
        rad = math.radians(angle)
        x_new = cx + (x - cx) * math.cos(rad) - (y - cy) * math.sin(rad)
        y_new = cy + (x - cx) * math.sin(rad) + (y - cy) * math.cos(rad)
        return x_new, y_new
    
    def clear_cache(self):
        """清空渲染缓存"""
        self.render_cache.clear()
        print("文本渲染缓存已清空")
    
    def get_cache_size(self) -> int:
        """获取缓存大小"""
        return len(self.render_cache)
    
    def is_backend_available(self) -> bool:
        """后端渲染是否可用"""
        return self.backend_available
    
    def is_wysiwyg_enabled(self) -> bool:
        """WYSIWYG模式是否启用"""
        return self.wysiwyg_mode
    
    def set_font(self, font_path: str) -> bool:
        """设置字体"""
        self.default_font_path = font_path
        self.clear_cache()  # 清空缓存以应用新字体
        
        if self.backend_available and text_render:
            try:
                text_render.set_font(font_path)
                return True
            except Exception as e:
                print(f"后端渲染器设置字体失败: {e}")
                return False
        return True
    
    def get_current_font(self) -> Optional[str]:
        """获取当前字体路径"""
        return self.default_font_path