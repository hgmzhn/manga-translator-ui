"""
擦除算法配置管理服务
支持从配置文件读取inpainter设置，管理多种擦除算法选择
"""
import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import os

class InpainterType(Enum):
    """擦除算法类型枚举"""
    DEFAULT = "default"  # AOT算法
    LAMA_LARGE = "lama_large"  # Lama Large算法 
    LAMA_MPE = "lama_mpe"  # Lama MPE算法
    STABLE_DIFFUSION = "sd"  # Stable Diffusion算法
    NONE = "none"  # 不进行擦除，填充白色
    ORIGINAL = "original"  # 保持原图不变

class InpaintPrecision(Enum):
    """修复精度枚举"""
    FP32 = "fp32"
    FP16 = "fp16" 
    BF16 = "bf16"

@dataclass
class InpainterConfig:
    """擦除算法配置"""
    inpainter: InpainterType = InpainterType.LAMA_LARGE
    inpainting_size: int = 2048
    inpainting_precision: InpaintPrecision = InpaintPrecision.BF16
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "inpainter": self.inpainter.value,
            "inpainting_size": self.inpainting_size,
            "inpainting_precision": self.inpainting_precision.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InpainterConfig':
        """从字典创建配置对象"""
        return cls(
            inpainter=InpainterType(data.get("inpainter", "lama_large")),
            inpainting_size=data.get("inpainting_size", 2048),
            inpainting_precision=InpaintPrecision(data.get("inpainting_precision", "bf16"))
        )

@dataclass  
class AlgorithmInfo:
    """算法信息"""
    name: str
    display_name: str
    description: str
    supports_gpu: bool = True
    supports_precision: bool = True
    preview_suitable: bool = True  # 是否适合实时预览

class EraseConfigService:
    """擦除算法配置管理服务"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # 当前配置
        self.current_config = InpainterConfig()
        
        # 算法信息映射
        self.algorithm_info = {
            InpainterType.DEFAULT: AlgorithmInfo(
                name="default",
                display_name="默认 (AOT)",
                description="默认的AOT图像修复算法，速度较快",
                supports_gpu=True,
                supports_precision=False,
                preview_suitable=True
            ),
            InpainterType.LAMA_LARGE: AlgorithmInfo(
                name="lama_large", 
                display_name="Lama Large",
                description="高质量的Lama修复算法，效果最佳",
                supports_gpu=True,
                supports_precision=True,
                preview_suitable=False  # 模型较大，不适合实时预览
            ),
            InpainterType.LAMA_MPE: AlgorithmInfo(
                name="lama_mpe",
                display_name="Lama MPE", 
                description="轻量级Lama算法，平衡速度与质量",
                supports_gpu=True,
                supports_precision=True,
                preview_suitable=True
            ),
            InpainterType.STABLE_DIFFUSION: AlgorithmInfo(
                name="sd",
                display_name="Stable Diffusion",
                description="基于扩散模型的修复算法，质量很高但速度慢",
                supports_gpu=True,
                supports_precision=True,
                preview_suitable=False  # 速度太慢，不适合实时预览
            ),
            InpainterType.NONE: AlgorithmInfo(
                name="none",
                display_name="无擦除",
                description="不进行擦除，将蒙版区域填充为白色",
                supports_gpu=False,
                supports_precision=False,
                preview_suitable=True
            ),
            InpainterType.ORIGINAL: AlgorithmInfo(
                name="original", 
                display_name="保持原图",
                description="保持原图不变，不进行任何处理",
                supports_gpu=False,
                supports_precision=False,
                preview_suitable=True
            )
        }
        
        self.logger.info("擦除算法配置服务初始化完成")
    
    def load_config_from_file(self, config_path: str) -> bool:
        """从配置文件加载设置"""
        try:
            if not os.path.exists(config_path):
                self.logger.warning(f"配置文件不存在: {config_path}")
                return False
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # 提取inpainter配置
            inpainter_data = config_data.get("inpainter", {})
            if inpainter_data:
                self.current_config = InpainterConfig.from_dict(inpainter_data)
                self.logger.info(f"从配置文件加载擦除算法设置: {self.current_config.inpainter.value}")
                return True
            else:
                self.logger.warning("配置文件中未找到inpainter配置")
                return False
                
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
            return False
    
    def save_config_to_file(self, config_path: str) -> bool:
        """保存配置到文件"""
        try:
            # 读取现有配置
            config_data = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
            
            # 更新inpainter配置
            config_data["inpainter"] = self.current_config.to_dict()
            
            # 保存配置
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"配置已保存到: {config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False
    
    def get_algorithm_list(self) -> List[Dict[str, Any]]:
        """获取可用算法列表"""
        return [
            {
                "type": algo_type,
                "name": info.name,
                "display_name": info.display_name,
                "description": info.description,
                "supports_gpu": info.supports_gpu,
                "supports_precision": info.supports_precision,
                "preview_suitable": info.preview_suitable
            }
            for algo_type, info in self.algorithm_info.items()
        ]
    
    def get_preview_suitable_algorithms(self) -> List[InpainterType]:
        """获取适合实时预览的算法"""
        return [
            algo_type for algo_type, info in self.algorithm_info.items()
            if info.preview_suitable
        ]
    
    def set_algorithm(self, algorithm: InpainterType):
        """设置当前算法"""
        self.current_config.inpainter = algorithm
        self.logger.info(f"切换擦除算法: {algorithm.value}")
    
    def set_inpainting_size(self, size: int):
        """设置修复尺寸"""
        if size < 512 or size > 4096:
            raise ValueError("修复尺寸必须在512-4096之间")
        self.current_config.inpainting_size = size
        self.logger.info(f"设置修复尺寸: {size}")
    
    def set_precision(self, precision: InpaintPrecision):
        """设置修复精度"""
        if not self.algorithm_info[self.current_config.inpainter].supports_precision:
            self.logger.warning(f"当前算法不支持精度设置: {self.current_config.inpainter.value}")
            return
        self.current_config.inpainting_precision = precision
        self.logger.info(f"设置修复精度: {precision.value}")
    
    def get_current_config(self) -> InpainterConfig:
        """获取当前配置"""
        return self.current_config
    
    def get_algorithm_info(self, algorithm: InpainterType) -> Optional[AlgorithmInfo]:
        """获取算法信息"""
        return self.algorithm_info.get(algorithm)
    
    def is_preview_suitable(self, algorithm: InpainterType) -> bool:
        """检查算法是否适合实时预览"""
        info = self.algorithm_info.get(algorithm)
        return info.preview_suitable if info else False
    
    def get_recommended_preview_algorithm(self) -> InpainterType:
        """获取推荐的预览算法"""
        # 优先推荐适合预览且效果较好的算法
        preview_algorithms = self.get_preview_suitable_algorithms()
        
        # 优先级顺序：lama_mpe > default > none > original
        priority_order = [
            InpainterType.LAMA_MPE,
            InpainterType.DEFAULT, 
            InpainterType.NONE,
            InpainterType.ORIGINAL
        ]
        
        for algo in priority_order:
            if algo in preview_algorithms:
                return algo
        
        # 如果都不可用，返回第一个
        return preview_algorithms[0] if preview_algorithms else InpainterType.NONE

# 全局服务实例
_erase_config_service: Optional[EraseConfigService] = None

def get_erase_config_service() -> EraseConfigService:
    """获取擦除配置服务实例"""
    global _erase_config_service
    if _erase_config_service is None:
        _erase_config_service = EraseConfigService()
    return _erase_config_service