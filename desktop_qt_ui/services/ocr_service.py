"""
OCR识别服务
集成后端OCR模块，实现文本框内容的光学字符识别功能
"""
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

# 添加后端模块路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), '..'))

try:
    from manga_translator.config import Ocr, OcrConfig
    from manga_translator.ocr import dispatch as dispatch_ocr
    from manga_translator.ocr import prepare as prepare_ocr
    from manga_translator.ocr import unload as unload_ocr
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
        self.current_prepared_ocr = None

        # YOLO OBB 检测缓存
        self._yolo_detector = None
        self._yolo_cache_image_id = None
        self._yolo_cache = None
        
        self.logger.info(f"OCR识别服务初始化完成，使用设备: {self.device}")

    @staticmethod
    def _resolve_ocr_config(config: Any) -> Any:
        if isinstance(config, OcrConfig):
            return config
        nested_ocr_config = getattr(config, 'ocr', None)
        if isinstance(nested_ocr_config, OcrConfig):
            return nested_ocr_config
        if nested_ocr_config is not None and hasattr(nested_ocr_config, 'ignore_bubble'):
            return nested_ocr_config
        if isinstance(config, Ocr):
            return OcrConfig(ocr=config)
        return config
    
    def _get_current_config(self) -> OcrConfig:
        """从配置服务获取当前OCR配置"""
        if not self.config_service:
            # 回退到默认配置
            return OcrConfig(
                ocr=Ocr.ocr48px,
                min_text_length=0,
                ignore_bubble=0,
                prob=0.3,
            )
        
        try:
            config = self.config_service.get_config()
            resolved_ocr_config = self._resolve_ocr_config(config)
            ocr_config_dict = resolved_ocr_config.model_dump() if hasattr(resolved_ocr_config, 'model_dump') else {}
            cli_config_dict = config.cli.model_dump() if hasattr(config, 'cli') else {}

            try:
                ocr_config = OcrConfig(**ocr_config_dict)
            except Exception as parse_error:
                self.logger.warning(f"解析OCR配置失败，使用默认配置: {parse_error}")
                ocr_config = OcrConfig(
                    ocr=Ocr.ocr48px,
                    min_text_length=0,
                    ignore_bubble=0,
                    prob=0.3,
                )
            
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
            )
        
    def _check_gpu_available(self) -> bool:
        """检查GPU是否可用"""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    async def prepare_model(self, ocr_type: Optional[Ocr] = None):
        """准备OCR模型（若切换了模型，自动销毁卸载旧模型）"""
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR后端模块不可用")
            
        ocr_to_use = ocr_type or self._get_current_config().ocr
        if isinstance(ocr_to_use, str):
            try:
                ocr_to_use = Ocr(ocr_to_use)
            except ValueError:
                pass

        # 切换模型时自动卸载销毁旧模型，释放显存与内存
        if self.current_prepared_ocr and self.current_prepared_ocr != ocr_to_use:
            self.logger.info(f"检测到编辑器 OCR 模型切换: {self.current_prepared_ocr} -> {ocr_to_use}，正在销毁卸载旧模型...")
            try:
                await unload_ocr(self.current_prepared_ocr)
                self.logger.info(f"旧 OCR 模型 {self.current_prepared_ocr} 销毁成功")
            except Exception as e:
                self.logger.warning(f"销毁旧 OCR 模型 {self.current_prepared_ocr} 失败: {e}")
            self.model_prepared = False

        if self.model_prepared and self.current_prepared_ocr == ocr_to_use:
            return

        try:
            await prepare_ocr(ocr_to_use, self.device)
            self.model_prepared = True
            self.current_prepared_ocr = ocr_to_use
            self.logger.info(f"OCR模型准备完成: {getattr(ocr_to_use, 'value', ocr_to_use)}")
        except Exception as e:
            self.logger.error(f"OCR模型准备失败: {e}")
            raise

    async def unload_current_model(self):
        """显式销毁当前已加载的 OCR 模型"""
        if self.current_prepared_ocr and OCR_AVAILABLE:
            try:
                await unload_ocr(self.current_prepared_ocr)
                self.logger.info(f"当前 OCR 模型 {self.current_prepared_ocr} 已销毁卸载")
            except Exception as e:
                self.logger.warning(f"销毁当前 OCR 模型 {self.current_prepared_ocr} 失败: {e}")
            finally:
                self.current_prepared_ocr = None
                self.model_prepared = False
    
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

    # ------------------------------------------------------------------
    # YOLO OBB 辅助拆分
    # ------------------------------------------------------------------

    async def _get_yolo_detector(self):
        """懒加载项目的 YOLOOBBDetector"""
        if self._yolo_detector is not None:
            return self._yolo_detector
        try:
            from manga_translator.detection.yolo_obb import YOLOOBBDetector
            self._yolo_detector = YOLOOBBDetector()
            return self._yolo_detector
        except Exception:
            self.logger.warning("YOLOOBBDetector 不可用")
            return None

    async def _detect_yolo_lines_cropped(self, image: np.ndarray, pts: np.ndarray) -> list:
        """对大框裁剪区域做 YOLO OBB 检测，返回原图坐标的 Quadrilateral 列表"""
        detector = await self._get_yolo_detector()
        if detector is None:
            return []

        # 计算大框 AABB 并裁剪
        im_h, im_w = image.shape[:2]
        x1 = int(np.clip(np.min(pts[:, 0]), 0, im_w - 1))
        y1 = int(np.clip(np.min(pts[:, 1]), 0, im_h - 1))
        x2 = int(np.clip(np.max(pts[:, 0]), x1 + 1, im_w))
        y2 = int(np.clip(np.max(pts[:, 1]), y1 + 1, im_h))

        cropped = image[y1:y2, x1:x2]
        if cropped.size == 0:
            return []

        await detector.load(self.device)
        textlines, _, _ = await detector.detect(
            cropped, detect_size=1600,
            text_threshold=0.4, box_threshold=0.4, unclip_ratio=1.0,
        )

        # 偏移坐标回原图
        for quad in textlines:
            quad.pts[:, 0] += x1
            quad.pts[:, 1] += y1

        return textlines

    def _split_polygon_by_yolo(
        self, yolo_lines: list, pts: np.ndarray, overlap_thresh: float = 0.3
    ) -> List[np.ndarray]:
        """用 YOLO 检测结果过滤出属于当前多边形的文本行"""
        if not yolo_lines:
            return []

        x1, y1 = int(np.min(pts[:, 0])), int(np.min(pts[:, 1]))
        x2, y2 = int(np.max(pts[:, 0])), int(np.max(pts[:, 1]))
        exclude_labels = {'other'}
        result: List[np.ndarray] = []

        for quad in yolo_lines:
            label = getattr(quad, 'det_label', None) or getattr(quad, 'yolo_label', '')
            if isinstance(label, str) and label.strip().lower() in exclude_labels:
                continue

            qx1 = int(np.min(quad.pts[:, 0]))
            qy1 = int(np.min(quad.pts[:, 1]))
            qx2 = int(np.max(quad.pts[:, 0]))
            qy2 = int(np.max(quad.pts[:, 1]))

            ix1, iy1 = max(x1, qx1), max(y1, qy1)
            ix2, iy2 = min(x2, qx2), min(y2, qy2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue

            inter = (ix2 - ix1) * (iy2 - iy1)
            det_area = max((qx2 - qx1) * (qy2 - qy1), 1)
            if inter / det_area >= overlap_thresh:
                clipped = quad.pts.copy()
                clipped[:, 0] = np.clip(clipped[:, 0], x1, x2)
                clipped[:, 1] = np.clip(clipped[:, 1], y1, y2)
                if cv2.contourArea(clipped.astype(np.int32)) >= 16:
                    result.append(clipped.astype(np.int32))

        return result

    @staticmethod
    def _sort_quads_by_direction(quads: list) -> list:
        """方向感知排序：水平按 y 上到下，竖直按 -(x+w) 右到左"""
        if len(quads) <= 1:
            return quads
        from collections import Counter
        dirs = [q.direction for q in quads]
        majority = Counter(dirs).most_common(1)[0][0]
        if majority == 'h':
            return sorted(quads, key=lambda q: q.aabb.y + q.aabb.h // 2)
        else:
            return sorted(quads, key=lambda q: -(q.aabb.x + q.aabb.w))
    
    async def recognize_region(self, image: np.ndarray, region: Dict[str, Any], 
                             config: Optional[OcrConfig] = None) -> Optional[OcrResult]:
        """识别单个文本框区域的文字（支持一个区域包含多个多边形）"""
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR后端模块不可用")

        config = config or self._get_current_config()
        ocr_config = self._resolve_ocr_config(config)
        ocr_key = ocr_config.ocr if hasattr(ocr_config, 'ocr') else ocr_config

        # --- FIX: Sanitize region data by ensuring all coordinates are rounded integers ---
        import copy
        region_clean = copy.deepcopy(region)
        if 'lines' in region_clean:
            for poly in region_clean['lines']:
                for i, point in enumerate(poly):
                    poly[i] = [int(round(point[0])), int(round(point[1]))]
        # --- END FIX ---
            
        if not self.model_prepared or self.current_prepared_ocr != ocr_key:
            await self.prepare_model(ocr_key)
        
        # Convert PIL Image to numpy array if necessary
        if isinstance(image, Image.Image):
            image = np.array(image.convert('RGB'))

        try:
            start_time = asyncio.get_event_loop().time()
            
            # Handle multiple polygons within one region
            quadrilaterals = []
            all_polygons = region_clean.get('lines', []) # Use the cleaned data
            if not all_polygons:
                return None

            unsegmented_ocr_models = {
                Ocr.mocr, Ocr.paddleocr_vl, Ocr.hayai_ocr_v2, Ocr.openai_ocr, Ocr.gemini_ocr,
                "mocr", "paddleocr_vl", "hayai_ocr_v2", "openai_ocr", "gemini_ocr"
            }
            should_try_split = len(all_polygons) == 1 and ocr_key not in unsegmented_ocr_models

            # YOLO OBB 检测（对大框裁剪区域检测）
            yolo_lines = None
            if should_try_split:
                pts_first = np.array(all_polygons[0], dtype=np.int32)
                try:
                    yolo_lines = await self._detect_yolo_lines_cropped(image, pts_first)
                except Exception as e:
                    self.logger.debug(f"YOLO detection skipped: {e}")

            for poly_points in all_polygons:
                if len(poly_points) >= 4:
                    pts = np.array(poly_points, dtype=np.int32)

                    if should_try_split and yolo_lines:
                        yolo_polys = self._split_polygon_by_yolo(yolo_lines, pts)
                        if len(yolo_polys) > 1:
                            self.logger.debug(f"YOLO split: {len(yolo_polys)} sub-lines")
                            for sp in yolo_polys:
                                quadrilaterals.append(Quadrilateral(pts=sp, text='', prob=1.0))
                            continue

                    quadrilaterals.append(Quadrilateral(pts=pts, text='', prob=1.0))

            if not quadrilaterals:
                return None

            runtime_config = config if hasattr(config, 'cli') else None
            if runtime_config is None and self.config_service:
                try:
                    runtime_config = self.config_service.get_config()
                except Exception:
                    runtime_config = None

            # Call backend OCR with all polygons from the region
            results = await dispatch_ocr(
                ocr_key,
                image, 
                quadrilaterals, 
                ocr_config,
                self.device,
                verbose=False,
                runtime_config=runtime_config,
            )
            
            processing_time = asyncio.get_event_loop().time() - start_time
            
            if results:
                # 方向感知排序后再拼接文本
                results = self._sort_quads_by_direction(results)
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

        config = config or self._get_current_config()
        ocr_config = self._resolve_ocr_config(config)
        ocr_key = ocr_config.ocr if hasattr(ocr_config, 'ocr') else ocr_config
              
        if not self.model_prepared or self.current_prepared_ocr != ocr_key:
            await self.prepare_model(ocr_key)
        
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

            runtime_config = config if hasattr(config, 'cli') else None
            if runtime_config is None and self.config_service:
                try:
                    runtime_config = self.config_service.get_config()
                except Exception:
                    runtime_config = None
             
            # 批量调用OCR
            results = await dispatch_ocr(
                ocr_key,
                image,
                quadrilaterals,
                ocr_config,
                self.device,
                verbose=False,
                runtime_config=runtime_config,
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

# The ServiceContainer in services/__init__.py is responsible for instantiation.
