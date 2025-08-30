"""
渲染参数管理服务
提供字体和排列参数的计算、自定义、存储和管理功能
"""
import copy
import math
import numpy as np
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import logging

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
    font_family: str = ""
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
    
    # 效果参数
    stroke_width: float = 0.2  # 描边宽度
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
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RenderParameters':
        """从字典创建参数对象"""
        return cls(**data)

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
        
        # 存储每个区域的自定义参数
        self.region_parameters: Dict[int, RenderParameters] = {}
        
        # 全局默认参数
        self.default_parameters = RenderParameters()
        
        # 预设参数
        self.presets: Dict[str, ParameterPreset] = {}
        self._init_default_presets()
        
        self.logger.info("渲染参数管理服务初始化完成")
    
    def _init_default_presets(self):
        """初始化默认预设"""
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
                direction="v",
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
                return copy.deepcopy(self.default_parameters)
            
            # 计算区域尺寸
            all_points = [point for poly in lines for point in poly]
            if len(all_points) < 4:
                return copy.deepcopy(self.default_parameters)
            
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
                direction = "h"  # 明显的横向
                alignment = "center"
            elif aspect_ratio < 0.5:
                direction = "v"  # 明显的纵向
                alignment = "right"
            else:
                direction = "auto"  # 自动判断
                alignment = "center"
            
            # 计算行间距（基于字体大小）
            line_spacing = 1.0 + (font_size / 100.0)  # 字体越大行间距稍大
            line_spacing = min(line_spacing, 1.5)
            
            # 计算描边宽度（基于字体大小）
            stroke_width = max(font_size * 0.05, 0.1)
            stroke_width = min(stroke_width, 2.0)
            
            # 创建参数对象
            params = RenderParameters(
                font_size=font_size,
                alignment=alignment,
                direction=direction,
                line_spacing=line_spacing,
                stroke_width=stroke_width,
                fg_color=(255, 255, 255),  # 默认白色文字
                bg_color=(0, 0, 0),  # 默认黑色描边
            )
            
            self.logger.debug(f"计算默认参数: 尺寸={width}x{height}, 字体={font_size}, 方向={direction}")
            return params
            
        except Exception as e:
            self.logger.error(f"计算默认参数失败: {e}")
            return copy.deepcopy(self.default_parameters)
    
    def get_region_parameters(self, region_index: int, region_data: Dict[str, Any] = None) -> RenderParameters:
        """获取指定区域的渲染参数"""
        if region_index in self.region_parameters:
            return self.region_parameters[region_index]
        
        # 如果没有自定义参数，计算默认参数
        if region_data:
            default_params = self.calculate_default_parameters(region_data)
            self.region_parameters[region_index] = default_params
            return default_params
        
        return copy.deepcopy(self.default_parameters)
    
    def set_region_parameters(self, region_index: int, parameters: RenderParameters):
        """设置指定区域的渲染参数"""
        self.region_parameters[region_index] = copy.deepcopy(parameters)
        self.logger.debug(f"设置区域 {region_index} 的渲染参数")
    
    def update_region_parameter(self, region_index: int, param_name: str, value: Any):
        """更新指定区域的单个参数"""
        if region_index not in self.region_parameters:
            self.region_parameters[region_index] = copy.deepcopy(self.default_parameters)
        
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
    
    def export_parameters_for_backend(self, region_index: int) -> Dict[str, Any]:
        """导出参数供后端识别和执行"""
        params = self.get_region_parameters(region_index)
        
        # 转换为后端可识别的格式
        backend_params = {
            # 字体参数
            'font_size': params.font_size,
            'font_family': params.font_family,
            'bold': params.bold,
            'italic': params.italic,
            'font_weight': params.font_weight,
            
            # 颜色参数
            'fg_color': params.fg_color,
            'bg_color': params.bg_color,
            'opacity': params.opacity,
            
            # 布局参数
            'alignment': params.alignment,
            'direction': params.direction,
            'line_spacing': params.line_spacing,
            'letter_spacing': params.letter_spacing,
            
            # 效果参数
            'stroke_width': params.stroke_width,
            'shadow_radius': params.shadow_radius,
            'shadow_strength': params.shadow_strength,
            'shadow_color': params.shadow_color,
            'shadow_offset': params.shadow_offset,
            
            # 渲染选项
            'hyphenate': params.hyphenate,
            'disable_font_border': params.disable_font_border,
            
            # 添加元数据
            '_render_params_version': '1.0',
            '_generated_by': 'desktop-ui'
        }
        
        return backend_params
    
    def import_parameters_from_json(self, region_index: int, json_data: Dict[str, Any]):
        """从JSON数据导入参数"""
        try:
            # 过滤有效的参数
            valid_params = {}
            param_fields = RenderParameters.__dataclass_fields__.keys()
            
            for key, value in json_data.items():
                if key in param_fields:
                    valid_params[key] = value
            
            if valid_params:
                params = RenderParameters(**valid_params)
                self.set_region_parameters(region_index, params)
                self.logger.info(f"从JSON导入区域 {region_index} 的参数")
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

# 全局服务实例
_render_parameter_service: Optional[RenderParameterService] = None

def get_render_parameter_service() -> RenderParameterService:
    """获取渲染参数管理服务实例"""
    global _render_parameter_service
    if _render_parameter_service is None:
        _render_parameter_service = RenderParameterService()
    return _render_parameter_service