"""
渲染参数管理服务
提供字体和排列参数的计算、自定义、存储和管理功能
"""
import copy
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Tuple

VALID_LAYOUT_MODES = {"smart_scaling", "strict", "balloon_fill"}


class Alignment(Enum):
    """对齐方式枚举"""
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    AUTO = "auto"

class Direction(Enum):
    """文本方向枚举"""
    HORIZONTAL = "h"
    VERTICAL = "v"
    HORIZONTAL_REVERSED = "hr"
    VERTICAL_REVERSED = "vr"
    AUTO = "auto"

@dataclass
class RenderParameters:
    """渲染参数数据类"""
    # 字体参数
    font_size: int = 12
    font_path: str = ""
    font_weight: int = 50  # 字重
    bold: bool = False
    italic: bool = False
    underline: bool = False
    
    # 颜色参数
    fg_color: Tuple[int, int, int] = (255, 255, 255)  # 前景色
    bg_color: Tuple[int, int, int] = (0, 0, 0)  # 背景色/描边色
    opacity: float = 1.0  # 透明度
    
    # 布局参数
    alignment: str = "center"
    direction: str = "auto"
    line_spacing: float = 1.0  # 行间距倍数
    letter_spacing: float = 1.0  # 字间距倍数
    layout_mode: str = "smart_scaling"  # 布局模式
    disable_auto_wrap: bool = False  # 禁用自动换行（AI断句）
    font_size_offset: int = 0  # 字体大小偏移
    font_size_minimum: int = 0  # 最小字体大小
    max_font_size: int = 0  # 最大字体大小
    font_scale_ratio: float = 1.0  # 字体缩放比例
    center_text_in_bubble: bool = False  # AI断句时文本居中
    optimize_line_breaks: bool = False  # 自动优化断句
    semantic_linebreak: bool = False  # 中文语义断句
    remove_linebreak_punctuation: bool = False  # 去除换行符周围的逗号句号
    strict_smart_scaling: bool = False  # AI断句自动扩大文字下不扩大文本框

    # 效果参数
    stroke_width: float = 0.07  # 描边宽度（相对字体大小的比例）
    shadow_radius: float = 0.0  # 阴影半径
    shadow_strength: float = 1.0  # 阴影强度
    shadow_color: Tuple[int, int, int] = (0, 0, 0)  # 阴影颜色
    shadow_offset: List[float] = None  # 阴影偏移
    
    # 渲染选项
    hyphenate: bool = True  # 是否启用连字符
    disable_font_border: bool = False  # 是否禁用字体边框
    
    def __post_init__(self):
        if self.shadow_offset is None:
            self.shadow_offset = [0.0, 0.0]
        if self.layout_mode not in VALID_LAYOUT_MODES:
            raise ValueError(
                f"Invalid layout_mode: {self.layout_mode!r}. "
                f"Supported values: {', '.join(sorted(VALID_LAYOUT_MODES))}"
            )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RenderParameters':
        """从字典创建参数对象"""
        clean = dict(data or {})
        return cls(**clean)

@dataclass
class ParameterPreset:
    """参数预设"""
    name: str
    description: str
    parameters: RenderParameters

class RenderParameterService:
    """渲染参数管理服务"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        from services import get_config_service
        self.config_service = get_config_service()

        # 存储每个区域的自定义参数
        self.region_parameters: Dict[int, RenderParameters] = {}

        # 不再维护独立的默认参数，直接使用配置服务
        # 预设参数
        self.presets: Dict[str, ParameterPreset] = {}
        self._init_default_presets()

        self.logger.info("渲染参数管理服务初始化完成")

    def get_default_parameters(self) -> RenderParameters:
        """获取当前配置服务的默认参数"""
        config = self.config_service.get_config()
        render_fields = RenderParameters.__dataclass_fields__.keys()
        global_render_config = config.render.model_dump()
        # 过滤掉 None 值，让 dataclass 默认值生效
        valid_global_config = {k: v for k, v in global_render_config.items() if k in render_fields}
        return RenderParameters(**valid_global_config)

    def _init_default_presets(self):
        # 漫画标准预设
        self.presets["manga_standard"] = ParameterPreset(
            name="漫画标准",
            description="适合大部分漫画的标准设置",
            parameters=RenderParameters(
                font_size=16,
                alignment="center",
                direction="auto",
                line_spacing=1.2,
                letter_spacing=1.0,
                fg_color=(255, 255, 255),
                bg_color=(0, 0, 0),
                stroke_width=0.15
            )
        )
        
        # 轻小说预设
        self.presets["novel_standard"] = ParameterPreset(
            name="轻小说标准",
            description="适合轻小说的横排文本设置",
            parameters=RenderParameters(
                font_size=14,
                alignment="left",
                direction="h",
                line_spacing=1.4,
                letter_spacing=1.1,
                fg_color=(0, 0, 0),
                bg_color=(255, 255, 255),
                stroke_width=0.0
            )
        )
        
        # 古典文学预设
        self.presets["classical_vertical"] = ParameterPreset(
            name="古典竖排",
            description="适合古典文学的竖排文本设置",
            parameters=RenderParameters(
                font_size=18,
                alignment="right",
                direction="vertical",
                line_spacing=1.0,
                letter_spacing=0.9,
                fg_color=(0, 0, 0),
                bg_color=(255, 255, 255),
                stroke_width=0.0
            )
        )
    
    def calculate_default_parameters(self, region_data: Dict[str, Any]) -> RenderParameters:
        """基于原始文本框计算默认渲染参数"""
        try:
            # 提取区域信息
            lines = region_data.get('lines', [])
            if not lines or not lines[0]:
                return self.get_default_parameters()

            # 计算区域尺寸
            all_points = [point for poly in lines for point in poly]
            if len(all_points) < 4:
                return self.get_default_parameters()
            
            x_coords = [p[0] for p in all_points]
            y_coords = [p[1] for p in all_points]
            
            width = max(x_coords) - min(x_coords)
            height = max(y_coords) - min(y_coords)
            
            # 计算字体大小（基于区域高度的80%）
            if height > 0:
                font_size = max(int(height * 0.6), 8)  # 最小8像素
                font_size = min(font_size, 72)  # 最大72像素
            else:
                font_size = 12
            
            # 判断文本方向
            aspect_ratio = width / height if height > 0 else 1.0
            if aspect_ratio > 2.0:
                direction = "horizontal"  # 明显的横向
                alignment = "center"
            elif aspect_ratio < 0.5:
                direction = "vertical"  # 明显的纵向
                alignment = "right"
            else:
                direction = "auto"  # 自动判断
                alignment = "center"
            
            # 创建参数对象，以配置服务的默认值为基础
            config = self.config_service.get_config()
            render_fields = RenderParameters.__dataclass_fields__.keys()
            global_render_config = config.render.model_dump()
            valid_global_config = {k: v for k, v in global_render_config.items() if k in render_fields}
            params = RenderParameters(**valid_global_config)

            # 然后，仅覆盖根据几何计算出的值
            params.font_size = font_size
            params.alignment = alignment
            params.direction = direction
            
            # 从 region_data 读取描边宽度（优先 stroke_width，兼容 default_stroke_width）
            stroke_width_val = region_data.get('stroke_width') or region_data.get('default_stroke_width')
            if stroke_width_val is not None:
                params.stroke_width = stroke_width_val
            
            # 注意：不覆盖line_spacing，使用配置服务的值
            
            self.logger.debug(f"计算默认参数: 尺寸={width}x{height}, 字体={font_size}, 方向={direction}")
            return params
            
        except Exception as e:
            self.logger.error(f"计算默认参数失败: {e}")
            # 从配置服务获取默认参数
            config = self.config_service.get_config()
            render_fields = RenderParameters.__dataclass_fields__.keys()
            global_render_config = config.render.model_dump()
            valid_global_config = {k: v for k, v in global_render_config.items() if k in render_fields}
            return RenderParameters(**valid_global_config)
    
    def get_region_parameters(self, region_index: int, region_data: Dict[str, Any] = None) -> RenderParameters:
        """获取指定区域的渲染参数"""
        if region_index in self.region_parameters:
            params = self.region_parameters[region_index]

            # 如果有 region_data,从中读取可能被用户修改的字段
            if region_data:
                # 字体大小
                if 'font_size' in region_data and region_data['font_size']:
                    params.font_size = region_data['font_size']

                # 对齐方式
                if 'alignment' in region_data and region_data['alignment']:
                    params.alignment = region_data['alignment']

                # 文本方向
                if 'direction' in region_data and region_data['direction']:
                    params.direction = region_data['direction']

                # 字体颜色 - 优先使用 font_color (hex格式)
                if 'font_color' in region_data and region_data['font_color']:
                    # 将 hex 转换为 RGB tuple
                    hex_color = region_data['font_color']
                    if isinstance(hex_color, str) and hex_color.startswith('#'):
                        try:
                            r = int(hex_color[1:3], 16)
                            g = int(hex_color[3:5], 16)
                            b = int(hex_color[5:7], 16)
                            params.fg_color = (r, g, b)
                        except ValueError:
                            pass
                elif 'fg_colors' in region_data and region_data['fg_colors']:
                    params.fg_color = tuple(region_data['fg_colors'])

                # 描边颜色
                if 'bg_colors' in region_data and region_data['bg_colors']:
                    params.bg_color = tuple(region_data['bg_colors'])
                elif 'bg_color' in region_data and region_data['bg_color']:
                    params.bg_color = tuple(region_data['bg_color'])

                # 描边宽度 - 优先读取 stroke_width，兼容旧的 default_stroke_width
                stroke_width_val = region_data.get('stroke_width') or region_data.get('default_stroke_width')
                if stroke_width_val is not None:
                    params.stroke_width = stroke_width_val

                # 行间距 - 读取 line_spacing
                if 'line_spacing' in region_data and region_data['line_spacing'] is not None:
                    params.line_spacing = region_data['line_spacing']
                if 'letter_spacing' in region_data and region_data['letter_spacing'] is not None:
                    params.letter_spacing = region_data['letter_spacing']

                # 字体样式
                if 'bold' in region_data:
                    params.bold = region_data['bold']
                if 'italic' in region_data:
                    params.italic = region_data['italic']
                if 'underline' in region_data:
                    params.underline = region_data['underline']
                if 'font_weight' in region_data:
                    params.font_weight = region_data['font_weight']

                # 字体路径
                if 'font_path' in region_data and region_data['font_path']:
                    params.font_path = region_data['font_path']

            return params

        # 动态从配置服务获取最新的默认参数
        config = self.config_service.get_config()
        render_fields = RenderParameters.__dataclass_fields__.keys()
        global_render_config = config.render.model_dump()
        valid_global_config = {k: v for k, v in global_render_config.items() if k in render_fields}

        # 创建基于配置服务的默认参数
        default_params = RenderParameters(**valid_global_config)

        # 如果有区域数据，基于它计算参数
        if region_data:
            calculated_params = self.calculate_default_parameters(region_data)
            
            # 从 region_data 中读取 line_spacing（倍率），如果没有则使用配置的默认值
            if 'line_spacing' in region_data and region_data['line_spacing'] is not None:
                calculated_params.line_spacing = region_data['line_spacing']
            else:
                calculated_params.line_spacing = default_params.line_spacing
            if 'letter_spacing' in region_data and region_data['letter_spacing'] is not None:
                calculated_params.letter_spacing = region_data['letter_spacing']
            else:
                calculated_params.letter_spacing = default_params.letter_spacing

            # 从 region_data 中读取用户设置的字段
            # 字体大小
            if 'font_size' in region_data and region_data['font_size']:
                calculated_params.font_size = region_data['font_size']

            # 对齐方式
            if 'alignment' in region_data and region_data['alignment']:
                calculated_params.alignment = region_data['alignment']

            # 文本方向
            if 'direction' in region_data and region_data['direction']:
                calculated_params.direction = region_data['direction']

            # 字体颜色 - 优先使用 font_color (hex格式)
            if 'font_color' in region_data and region_data['font_color']:
                hex_color = region_data['font_color']
                if isinstance(hex_color, str) and hex_color.startswith('#'):
                    try:
                        r = int(hex_color[1:3], 16)
                        g = int(hex_color[3:5], 16)
                        b = int(hex_color[5:7], 16)
                        calculated_params.fg_color = (r, g, b)
                    except ValueError:
                        pass
            elif 'fg_colors' in region_data and region_data['fg_colors']:
                calculated_params.fg_color = tuple(region_data['fg_colors'])

            # 描边颜色
            if 'bg_colors' in region_data and region_data['bg_colors']:
                calculated_params.bg_color = tuple(region_data['bg_colors'])
            elif 'bg_color' in region_data and region_data['bg_color']:
                calculated_params.bg_color = tuple(region_data['bg_color'])

            # 描边宽度 - 优先读取 stroke_width，兼容旧的 default_stroke_width
            stroke_width_val = region_data.get('stroke_width') or region_data.get('default_stroke_width')
            if stroke_width_val is not None:
                calculated_params.stroke_width = stroke_width_val

            # 字体样式
            if 'bold' in region_data:
                calculated_params.bold = region_data['bold']
            if 'italic' in region_data:
                calculated_params.italic = region_data['italic']
            if 'underline' in region_data:
                calculated_params.underline = region_data['underline']
            if 'font_weight' in region_data:
                calculated_params.font_weight = region_data['font_weight']

            # 字体路径
            if 'font_path' in region_data and region_data['font_path']:
                calculated_params.font_path = region_data['font_path']

            # 可以添加其他需要从配置服务获取的参数
            self.region_parameters[region_index] = calculated_params
            return calculated_params

        return default_params
    
    def set_region_parameters(self, region_index: int, parameters: RenderParameters):
        """设置指定区域的渲染参数"""
        self.region_parameters[region_index] = copy.deepcopy(parameters)
        self.logger.debug(f"设置区域 {region_index} 的渲染参数")
    
    def update_region_parameter(self, region_index: int, param_name: str, value: Any):
        """更新指定区域的单个参数"""
        if region_index not in self.region_parameters:
            self.region_parameters[region_index] = self.get_default_parameters()

        if hasattr(self.region_parameters[region_index], param_name):
            setattr(self.region_parameters[region_index], param_name, value)
            self.logger.debug(f"更新区域 {region_index} 参数 {param_name} = {value}")
        else:
            self.logger.warning(f"未知参数: {param_name}")
    
    def apply_preset(self, region_index: int, preset_name: str) -> bool:
        """应用预设参数到指定区域"""
        if preset_name not in self.presets:
            self.logger.warning(f"未找到预设: {preset_name}")
            return False
        
        preset_params = copy.deepcopy(self.presets[preset_name].parameters)
        self.set_region_parameters(region_index, preset_params)
        self.logger.info(f"应用预设 '{preset_name}' 到区域 {region_index}")
        return True
    
    def create_custom_preset(self, name: str, description: str, parameters: RenderParameters):
        """创建自定义预设"""
        self.presets[name] = ParameterPreset(
            name=name,
            description=description,
            parameters=copy.deepcopy(parameters)
        )
        self.logger.info(f"创建自定义预设: {name}")
    
    def get_preset_list(self) -> List[Dict[str, str]]:
        """获取预设列表"""
        return [
            {
                "name": preset.name,
                "key": key,
                "description": preset.description
            }
            for key, preset in self.presets.items()
        ]
    
    def export_parameters_for_backend(self, region_index: int, region_data: Dict[str, Any]) -> Dict[str, Any]:
        """导出参数供后端识别和执行"""
        params = self.get_region_parameters(region_index, region_data)
        
        # 转换为后端可识别的格式
        # 如果font_path为空，使用配置服务中的默认字体
        font_path_to_use = params.font_path
        if not font_path_to_use:
            default_params = self.get_default_parameters()
            font_path_to_use = default_params.font_path
        
        backend_params = {
            # 字体参数
            'font_size': params.font_size,
            'font_path': font_path_to_use,
            'bold': params.bold,
            'italic': params.italic,
            'font_weight': params.font_weight,
            
            # 颜色参数
            'font_color': f"#{params.fg_color[0]:02x}{params.fg_color[1]:02x}{params.fg_color[2]:02x}" if isinstance(params.fg_color, (list, tuple)) and len(params.fg_color) == 3 else params.fg_color,
            'opacity': params.opacity,
            
            # 布局参数
            'alignment': params.alignment,
            'direction': {'h': 'horizontal', 'v': 'vertical', 'hr': 'horizontal', 'vr': 'vertical'}.get(params.direction, params.direction if params.direction in ['horizontal', 'vertical', 'auto'] else 'auto'),
            'vertical': params.direction in ['v', 'vr', 'vertical'], # Added vertical flag
            'line_spacing': params.line_spacing,
            'letter_spacing': params.letter_spacing,
            
            # 效果参数
            'stroke_width': params.stroke_width,  # 统一使用 stroke_width（后端会转换为 default_stroke_width）
            'text_stroke_width': params.stroke_width,  # text_renderer_backend 期望的参数名
            'shadow_radius': params.shadow_radius,
            'shadow_strength': params.shadow_strength,
            'shadow_color': params.shadow_color,
            'shadow_offset': params.shadow_offset,
            
            # 渲染选项
            'hyphenate': params.hyphenate,
            'disable_font_border': params.disable_font_border,
            'disable_auto_wrap': params.disable_auto_wrap,
            'layout_mode': params.layout_mode,
            'font_size_offset': params.font_size_offset,
            'font_size_minimum': params.font_size_minimum,
            'max_font_size': params.max_font_size,
            'font_scale_ratio': params.font_scale_ratio,
            'center_text_in_bubble': params.center_text_in_bubble,
            'semantic_linebreak': params.semantic_linebreak,
            'remove_linebreak_punctuation': params.remove_linebreak_punctuation,
            # 添加元数据
            '_render_params_version': '1.0',
            '_generated_by': 'desktop-ui'
        }
        
        # 处理描边颜色 - 使用 params.bg_color 作为描边颜色
        backend_params['text_stroke_color'] = params.bg_color

        # 覆盖 line_spacing / letter_spacing 和 stroke_width（如果 region_data 中有的话）
        if region_data:
            if 'line_spacing' in region_data:
                backend_params['line_spacing'] = region_data['line_spacing']
            if 'letter_spacing' in region_data:
                backend_params['letter_spacing'] = region_data['letter_spacing']
            # 优先使用 stroke_width，兼容旧的 default_stroke_width
            stroke_width_val = region_data.get('stroke_width') or region_data.get('default_stroke_width')
            if stroke_width_val is not None:
                backend_params['default_stroke_width'] = stroke_width_val
                backend_params['text_stroke_width'] = stroke_width_val
        
        return backend_params
    
    def import_parameters_from_json(self, region_index: int, json_data: Dict[str, Any]):
        """从JSON数据导入参数"""
        try:
            # 过滤有效的参数
            valid_params = {}
            param_fields = RenderParameters.__dataclass_fields__.keys()
            
            for key, value in json_data.items():
                # 特殊处理颜色键名的不一致 (JSON是复数, dataclass是单数)
                if key == 'fg_colors':
                    key = 'fg_color'
                    value = tuple(value) if isinstance(value, list) else value
                elif key == 'bg_colors':
                    key = 'bg_color'
                    value = tuple(value) if isinstance(value, list) else value
                elif key == 'text_stroke_color':
                    key = 'bg_color'
                    # 处理 hex 格式颜色
                    if isinstance(value, str) and value.startswith('#'):
                        try:
                            r = int(value[1:3], 16)
                            g = int(value[3:5], 16)
                            b = int(value[5:7], 16)
                            value = (r, g, b)
                        except ValueError:
                            continue
                    else:
                         value = tuple(value) if isinstance(value, list) else value
                elif key == 'font_color':
                    # 处理 hex 格式颜色
                    if isinstance(value, str) and value.startswith('#'):
                        try:
                            r = int(value[1:3], 16)
                            g = int(value[3:5], 16)
                            b = int(value[5:7], 16)
                            key = 'fg_color'
                            value = (r, g, b)
                        except ValueError:
                            continue

                if key in param_fields:
                    valid_params[key] = value
            
            if valid_params:
                # 先获取配置服务的默认参数作为基础
                base_params = self.get_default_parameters()
                base_dict = base_params.to_dict()

                # 用JSON中的值覆盖（但跳过了line_spacing）
                base_dict.update(valid_params)

                params = RenderParameters(**base_dict)
                self.set_region_parameters(region_index, params)
                self.logger.debug(f"从JSON导入区域 {region_index} 的参数")
                return True
            else:
                self.logger.warning("JSON中没有有效的渲染参数")
                return False
                
        except Exception as e:
            self.logger.error(f"导入参数失败: {e}")
            return False
    
    def batch_update_parameters(self, updates: Dict[int, Dict[str, Any]]):
        """批量更新多个区域的参数"""
        for region_index, param_updates in updates.items():
            for param_name, value in param_updates.items():
                self.update_region_parameter(region_index, param_name, value)
    
    def copy_parameters(self, from_region: int, to_region: int):
        """复制参数从一个区域到另一个区域"""
        if from_region in self.region_parameters:
            source_params = copy.deepcopy(self.region_parameters[from_region])
            self.set_region_parameters(to_region, source_params)
            self.logger.info(f"复制参数从区域 {from_region} 到区域 {to_region}")
            return True
        return False
    
    def reset_region_parameters(self, region_index: int):
        """重置区域参数为默认值"""
        if region_index in self.region_parameters:
            del self.region_parameters[region_index]
            self.logger.info(f"重置区域 {region_index} 的参数")

    def clear_cache(self):
        """清空所有区域的自定义参数缓存"""
        self.region_parameters.clear()
    
    def get_parameter_summary(self, region_index: int) -> Dict[str, str]:
        """获取参数摘要信息"""
        params = self.get_region_parameters(region_index)
        
        direction_map = {
            "h": "水平",
            "v": "垂直", 
            "hr": "水平从右到左",
            "vr": "垂直从右到左",
            "auto": "自动"
        }
        
        alignment_map = {
            "left": "左对齐",
            "center": "居中",
            "right": "右对齐",
            "auto": "自动"
        }
        
        return {
            "字体大小": f"{params.font_size}px",
            "对齐方式": alignment_map.get(params.alignment, params.alignment),
            "文本方向": direction_map.get(params.direction, params.direction),
            "行间距": f"{params.line_spacing:.1f}倍",
            "字间距": f"{params.letter_spacing:.1f}倍",
            "描边宽度": f"{params.stroke_width:.2f}",
            "前景色": f"RGB{params.fg_color}",
            "背景色": f"RGB{params.bg_color}"
        }

# Instantiation is handled by the ServiceContainer in services/__init__.py
