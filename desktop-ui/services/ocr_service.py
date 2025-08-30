"""
OCR识别服务
集成后端OCR模块，实现文本框内容的光学字符识别功能
"""
import asyncio
import logging
import numpy as np
import cv2
import sys
import os
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
from PIL import Image

# 添加后端模块路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), '..'))

try:
    from manga_translator.ocr import dispatch as dispatch_ocr, prepare as prepare_ocr
    from manga_translator.config import Ocr, OcrConfig
    from manga_translator.utils import Quadrilateral
    OCR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"OCR后端模块导入失败: {e}")
    OCR_AVAILABLE = False

@dataclass
class OcrResult:
    """OCR识别结果"""
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # (x, y, width, height)
    processing_time: float

class OcrService:
    """OCR识别服务"""
    
    def __init__(self, config_service=None):
        self.logger = logging.getLogger(__name__)
        
        # OCR配置
        self.default_config = OcrConfig(
            ocr=Ocr.ocr48px,  # 默认使用48px OCR模型
            min_text_length=0,
            ignore_bubble=0,
            prob=0.3,  # 最小置信度阈值
            use_mocr_merge=False
        )
        
        # 配置服务依赖
        self.config_service = config_service
        if not self.config_service:
            # 懒加载配置服务，避免循环依赖
            from . import get_config_service
            self.config_service = get_config_service()
        
        # 设备配置
        self.device = 'cpu'
        if self._check_gpu_available():
            self.device = 'cuda'
            
        # OCR模型缓存
        self.model_prepared = False
        
        self.logger.info(f"OCR识别服务初始化完成，使用设备: {self.device}")
    
    def _get_current_config(self) -> OcrConfig:
        """从配置服务获取当前OCR配置"""
        if not self.config_service:
            # 回退到默认配置
            return OcrConfig(
                ocr=Ocr.ocr48px,
                min_text_length=0,
                ignore_bubble=0,
                prob=0.3,
                use_mocr_merge=False
            )
        
        try:
            config = self.config_service.get_config()
            ocr_config_dict = config.get('ocr', {})
            cli_config_dict = config.get('cli', {})
            
            # 从配置构建OcrConfig
            ocr_config = OcrConfig()
            
            # OCR模型设置
            if 'ocr' in ocr_config_dict:
                try:
                    ocr_config.ocr = Ocr(ocr_config_dict['ocr'])
                except ValueError:
                    self.logger.warning(f"未知OCR模型: {ocr_config_dict['ocr']}，使用默认模型")
                    ocr_config.ocr = Ocr.ocr48px
            
            # 其他OCR参数
            ocr_config.min_text_length = ocr_config_dict.get('min_text_length', 0)
            ocr_config.ignore_bubble = ocr_config_dict.get('ignore_bubble', 0)
            ocr_config.prob = ocr_config_dict.get('prob', 0.3)
            ocr_config.use_mocr_merge = ocr_config_dict.get('use_mocr_merge', False)
            
            # GPU设置从CLI配置获取
            if cli_config_dict.get('use_gpu', False) and self._check_gpu_available():
                self.device = 'cuda'
            else:
                self.device = 'cpu'
                
            return ocr_config
            
        except Exception as e:
            self.logger.error(f"获取OCR配置失败，使用默认配置: {e}")
            return OcrConfig(
                ocr=Ocr.ocr48px,
                min_text_length=0,
                ignore_bubble=0,
                prob=0.3,
                use_mocr_merge=False
            )
        
    def _check_gpu_available(self) -> bool:
        """检查GPU是否可用"""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    async def prepare_model(self, ocr_type: Optional[Ocr] = None):
        """准备OCR模型"""
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR后端模块不可用")
        if self.model_prepared:
            return
            
        ocr_to_use = ocr_type or self.default_config.ocr
        
        try:
            await prepare_ocr(ocr_to_use, self.device)
            self.model_prepared = True
            self.logger.info(f"OCR模型准备完成: {ocr_to_use.value}")
        except Exception as e:
            self.logger.error(f"OCR模型准备失败: {e}")
            raise
    
    def _region_to_quadrilateral(self, region: Dict[str, Any], image_shape: Tuple[int, int]) -> Quadrilateral:
        """将文本框区域转换为OCR所需的Quadrilateral格式"""
        try:
            # 获取文本框的四个角点
            lines = region.get('lines', [[]])
            if not lines or not lines[0]:
                return None
                
            # 获取第一个多边形的所有点
            points = lines[0]
            if len(points) < 4:
                return None
            
            # 转换为numpy数组格式
            pts = np.array(points, dtype=np.float32)
            
            # 创建Quadrilateral对象
            quadrilateral = Quadrilateral(
                pts=pts,
                text='',  # 待识别
                prob=1.0
            )
            
            return quadrilateral
            
        except Exception as e:
            self.logger.error(f"区域转换失败: {e}")
            return None
    
    def _extract_region_image(self, image: np.ndarray, region: Dict[str, Any]) -> Optional[np.ndarray]:
        """从图像中提取文本框区域"""
        try:
            lines = region.get('lines', [[]])
            if not lines or not lines[0]:
                return None
                
            points = np.array(lines[0], dtype=np.int32)
            
            # 获取边界框
            x, y, w, h = cv2.boundingRect(points)
            
            # 确保边界框在图像范围内
            x = max(0, x)
            y = max(0, y)
            w = min(w, image.shape[1] - x)
            h = min(h, image.shape[0] - y)
            
            if w <= 0 or h <= 0:
                return None
            
            # 提取区域
            region_image = image[y:y+h, x:x+w]
            
            return region_image
            
        except Exception as e:
            self.logger.error(f"区域图像提取失败: {e}")
            return None
    
    async def recognize_region(self, image: np.ndarray, region: Dict[str, Any], 
                             config: Optional[OcrConfig] = None) -> Optional[OcrResult]:
        """识别单个文本框区域的文字（支持一个区域包含多个多边形）"""
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR后端模块不可用")

        # --- FIX: Sanitize region data by ensuring all coordinates are rounded integers ---
        import copy
        region_clean = copy.deepcopy(region)
        if 'lines' in region_clean:
            for poly in region_clean['lines']:
                for i, point in enumerate(poly):
                    poly[i] = [int(round(point[0])), int(round(point[1]))]
        # --- END FIX ---
            
        if not self.model_prepared:
            await self.prepare_model()
        
        config = config or self.default_config
        
        try:
            start_time = asyncio.get_event_loop().time()
            
            # Handle multiple polygons within one region
            quadrilaterals = []
            all_polygons = region_clean.get('lines', []) # Use the cleaned data
            if not all_polygons:
                return None

            for poly_points in all_polygons:
                if len(poly_points) >= 4:
                    pts = np.array(poly_points, dtype=np.int32)
                    quadrilaterals.append(Quadrilateral(pts=pts, text='', prob=1.0))

            if not quadrilaterals:
                return None

            # Call backend OCR with all polygons from the region
            results = await dispatch_ocr(
                config.ocr, 
                image, 
                quadrilaterals, 
                config, 
                self.device,
                verbose=False
            )
            
            processing_time = asyncio.get_event_loop().time() - start_time
            
            if results:
                # Combine text from all recognized polygons
                combined_text = ''.join([res.text for res in results if res.text])
                # Average the confidence
                avg_confidence = sum(res.prob for res in results) / len(results) if results else 0
                
                # The bbox for a multi-polygon region is the bounding box of all polygons
                all_points = [p for poly in all_polygons for p in poly]
                min_x = int(min(p[0] for p in all_points))
                max_x = int(max(p[0] for p in all_points))
                min_y = int(min(p[1] for p in all_points))
                max_y = int(max(p[1] for p in all_points))
                bbox_tuple = (min_x, min_y, max_x - min_x, max_y - min_y)

                return OcrResult(
                    text=combined_text,
                    confidence=avg_confidence,
                    bbox=bbox_tuple,
                    processing_time=processing_time
                )
            else:
                self.logger.warning("OCR识别无结果")
                return None
                
        except Exception as e:
            self.logger.error(f"OCR识别失败: {e}")
            return None
    
    async def recognize_multiple_regions(self, image: np.ndarray, regions: List[Dict[str, Any]], 
                                       config: Optional[OcrConfig] = None) -> List[Optional[OcrResult]]:
        """批量识别多个文本框区域"""
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR后端模块不可用")
            
        if not self.model_prepared:
            await self.prepare_model()
        
        config = config or self.default_config
        
        try:
            start_time = asyncio.get_event_loop().time()
            
            # 转换所有区域格式
            quadrilaterals = []
            valid_indices = []
            
            for i, region in enumerate(regions):
                quad = self._region_to_quadrilateral(region, image.shape[:2])
                if quad is not None:
                    quadrilaterals.append(quad)
                    valid_indices.append(i)
            
            if not quadrilaterals:
                return [None] * len(regions)
            
            # 批量调用OCR
            results = await dispatch_ocr(
                config.ocr,
                image,
                quadrilaterals,
                config,
                self.device,
                verbose=False
            )
            
            processing_time = asyncio.get_event_loop().time() - start_time
            
            # 构建结果列表
            ocr_results = [None] * len(regions)
            
            for i, result in enumerate(results):
                if i < len(valid_indices):
                    original_index = valid_indices[i]
                    bbox = result.aabb
                    bbox_tuple = (bbox.x, bbox.y, bbox.w, bbox.h)
                    
                    ocr_results[original_index] = OcrResult(
                        text=result.text,
                        confidence=result.prob,
                        bbox=bbox_tuple,
                        processing_time=processing_time / len(results)
                    )
            
            return ocr_results
            
        except Exception as e:
            self.logger.error(f"批量OCR识别失败: {e}")
            return [None] * len(regions)
    
    def get_available_models(self) -> List[str]:
        """获取可用的OCR模型列表"""
        if not OCR_AVAILABLE:
            return []
            
        return [ocr.value for ocr in Ocr]
    
    def set_model(self, model_name: str):
        """设置OCR模型，支持通过value或name设置"""
        if not OCR_AVAILABLE:
            return
        
        try:
            # 先尝试通过name查找（最常见的情况）
            if hasattr(Ocr, model_name):
                self.default_config.ocr = Ocr[model_name]
                self.model_prepared = False  # 重置模型准备状态
                self.logger.info(f"通过name设置OCR模型: {model_name}")
                return
            
            # 如果name查找失败，尝试通过value查找
            for ocr_model in Ocr:
                if ocr_model.value == model_name:
                    self.default_config.ocr = ocr_model
                    self.model_prepared = False  # 重置模型准备状态
                    self.logger.info(f"通过value设置OCR模型: {model_name} -> {ocr_model.name}")
                    return
                    
            # 如果都没找到，记录警告
            self.logger.warning(f"未找到OCR模型: {model_name}，保持当前设置")
        except Exception as e:
            self.logger.error(f"设置OCR模型时发生错误: {e}")
    
    def get_current_model(self) -> str:
        """获取当前OCR模型名称"""
        return self.default_config.ocr.value
    
    
    def set_config(self, **kwargs):
        """设置OCR配置"""
        for key, value in kwargs.items():
            if hasattr(self.default_config, key):
                setattr(self.default_config, key, value)
                self.logger.info(f"OCR配置更新: {key} = {value}")
    
    def get_config(self) -> Dict[str, Any]:
        """获取当前OCR配置"""
        return {
            'ocr': self.default_config.ocr.value,
            'min_text_length': self.default_config.min_text_length,
            'ignore_bubble': self.default_config.ignore_bubble,
            'prob': self.default_config.prob,
            'use_mocr_merge': self.default_config.use_mocr_merge,
            'device': self.device
        }
    
    def is_available(self) -> bool:
        """检查OCR服务是否可用"""
        return OCR_AVAILABLE
    
    def _is_valid_quadrilateral(self, pts: np.ndarray) -> bool:
        """检查四边形是否适合OCR识别"""
        if len(pts) < 4:
            return False
        
        # 计算面积
        area = cv2.contourArea(pts)
        if area < 100:  # 面积太小
            return False
        
        # 检查是否过于扭曲（长宽比过大）
        rect = cv2.boundingRect(pts)
        if rect[2] <= 0 or rect[3] <= 0:
            return False
        
        aspect_ratio = max(rect[2], rect[3]) / min(rect[2], rect[3])
        if aspect_ratio > 20:  # 长宽比超过20:1，可能过于扭曲
            return False
        
        return True

# 全局服务实例
_ocr_service: Optional[OcrService] = None

def get_ocr_service() -> OcrService:
    """获取OCR识别服务实例"""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OcrService()
    return _ocr_service