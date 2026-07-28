import itertools
import os
from abc import abstractmethod
from collections import Counter
from typing import List, Union

import networkx as nx
import numpy as np

from ..config import OcrConfig
from ..utils import (
    InfererModule,
    ModelWrapper,
    Quadrilateral,
    TextBlock,
    build_bubble_mask_from_mangalens_result,
    calc_bbox_mask_overlap_ratio,
    get_cached_bubbles_with_mangalens,
)


class CommonOCR(InfererModule):
    def _get_model_bubble_mask(self, full_image: np.ndarray) -> np.ndarray:
        """
        Reads the precomputed bubble mask from cache.
        """
        cache_key = (id(full_image), full_image.shape[0], full_image.shape[1])
        if getattr(self, '_model_bubble_cache_key', None) == cache_key:
            return getattr(self, '_model_bubble_cache_mask', np.zeros(full_image.shape[:2], dtype=np.uint8))

        try:
            result = get_cached_bubbles_with_mangalens(full_image, return_annotated=False, verbose=False)
            if result is None:
                self.logger.warning("Model bubble filter cache miss, skip model gating for this image")
                bubble_mask = np.zeros(full_image.shape[:2], dtype=np.uint8)
            else:
                bubble_mask = build_bubble_mask_from_mangalens_result(result, full_image.shape[:2])
                detected = len(result.detections) if result is not None else 0
                self.logger.info(
                    f"Model bubble filter cache hit: detected {detected} bubbles, mask_pixels={int(np.count_nonzero(bubble_mask))}"
                )
        except Exception as e:
            self.logger.warning(f"Model bubble filter cache read failed, fallback to heuristic filter: {e}")
            bubble_mask = np.zeros(full_image.shape[:2], dtype=np.uint8)

        self._model_bubble_cache_key = cache_key
        self._model_bubble_cache_mask = bubble_mask
        return bubble_mask

    def _generate_text_direction(self, bboxes: List[Union[Quadrilateral, TextBlock]]):
        if len(bboxes) > 0:
            if isinstance(bboxes[0], TextBlock):
                for blk in bboxes:
                    for line_idx in range(len(blk.lines)):
                        yield blk, line_idx
            else:
                from ..utils import quadrilateral_can_merge_region

                G = nx.Graph()
                for i, box in enumerate(bboxes):
                    G.add_node(i, box = box)
                for ((u, ubox), (v, vbox)) in itertools.combinations(enumerate(bboxes), 2):
                    if quadrilateral_can_merge_region(ubox, vbox, aspect_ratio_tol=1):
                        G.add_edge(u, v)
                for node_set in nx.algorithms.components.connected_components(G):
                    nodes = list(node_set)
                    # majority vote for direction
                    dirs = [box.direction for box in [bboxes[i] for i in nodes]]
                    majority_dir = Counter(dirs).most_common(1)[0][0]
                    # sort
                    if majority_dir == 'h':
                        nodes = sorted(nodes, key = lambda x: bboxes[x].aabb.y + bboxes[x].aabb.h // 2)
                    elif majority_dir == 'v':
                        nodes = sorted(nodes, key = lambda x: -(bboxes[x].aabb.x + bboxes[x].aabb.w))
                    # yield overall bbox and sorted indices
                    for node in nodes:
                        yield bboxes[node], majority_dir

    def _should_ignore_region(self, region_img: np.ndarray, ignore_bubble: float, 
                              full_image: np.ndarray = None, textline: Quadrilateral = None,
                              ocr_config: OcrConfig = None) -> bool:
        """
        通用的气泡过滤方法，判断文本区域是否应该被忽略
        
        Args:
            region_img: 裁剪后的文本区域图像（用于简单方法）
            ignore_bubble: 忽略气泡阈值 (0-1)
            full_image: 完整图像（可选，用于高级方法）
            textline: 文本行对象（可选，用于获取坐标）
            ocr_config: OCR配置（可选，用于模型气泡过滤）
            
        Returns:
            True: 应该忽略（非气泡区域）
            False: 应该保留（气泡区域）
        """
        from ..utils.bubble import is_ignore

        # 模型气泡过滤（与 ignore_bubble 同阶段，但基于检测模型）
        use_model_filter = bool(getattr(ocr_config, 'use_model_bubble_filter', False)) if ocr_config is not None else False
        if use_model_filter and full_image is not None and textline is not None:
            bbox = textline.aabb
            text_bbox = (int(bbox.x), int(bbox.y), int(bbox.w), int(bbox.h))

            bubble_mask = self._get_model_bubble_mask(full_image)
            if np.count_nonzero(bubble_mask) == 0:
                if not getattr(self, '_model_bubble_no_boxes_logged', False):
                    self.logger.info("Model bubble filter: no bubble mask detected, skip model gating for this image")
                    self._model_bubble_no_boxes_logged = True
            else:
                overlap_threshold = float(getattr(ocr_config, 'model_bubble_overlap_threshold', 0.1))
                overlap_threshold = max(0.0, min(1.0, overlap_threshold))
                overlap_ratio = calc_bbox_mask_overlap_ratio(text_bbox, bubble_mask)
                if overlap_ratio < overlap_threshold:
                    self.logger.info(
                        f"Model bubble filter: overlap={overlap_ratio:.3f} < threshold={overlap_threshold:.3f}, filtering region"
                    )
                    return True
        
        # 如果提供了完整图像和文本行，使用高级方法
        if full_image is not None and textline is not None:
            # 获取文本行的边界框
            bbox = textline.aabb
            x, y, w, h = int(bbox.x), int(bbox.y), int(bbox.w), int(bbox.h)
            return is_ignore(region_img, ignore_bubble, full_image, [x, y, w, h])
        
        # 否则使用简单方法
        return is_ignore(region_img, ignore_bubble)

    async def recognize(self, image: np.ndarray, textlines: List[Quadrilateral], config: OcrConfig, verbose: bool = False) -> List[Quadrilateral]:
        '''
        Performs the optical character recognition, using the `textlines` as areas of interests.
        Returns a `textlines` list with the `textline.text` property set to the detected text string.
        '''
        # Reset per-image model bubble cache
        self._model_bubble_cache_key = None
        self._model_bubble_cache_mask = None
        self._model_bubble_no_boxes_logged = False
        if bool(getattr(config, 'use_model_bubble_filter', False)):
            threshold = float(getattr(config, 'model_bubble_overlap_threshold', 0.1))
            self.logger.info(f"Model bubble filter enabled (overlap_threshold={threshold:.3f})")
        return await self._recognize(image, textlines, config, verbose)

    @abstractmethod
    async def _recognize(self, image: np.ndarray, textlines: List[Quadrilateral], config: OcrConfig, verbose: bool = False) -> List[Quadrilateral]:
        pass


class OfflineOCR(CommonOCR, ModelWrapper):
    _MODEL_SUB_DIR = 'ocr'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_gpu = False  # 子类应该在 _load 中设置这个标志

    async def _recognize(self, *args, **kwargs):
        result = await self.infer(*args, **kwargs)
        return result

    @abstractmethod
    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral], args: OcrConfig, verbose: bool = False) -> List[Quadrilateral]:
        pass

    def _cleanup_ocr_memory(self, *objects, force_gpu_cleanup: bool = False):
        """
        OCR 模块统一的内存清理方法
        
        Args:
            *objects: 要删除的对象（变量名或对象引用）
            force_gpu_cleanup: 是否强制清理 GPU 显存
            
        Example:
            # 清理单个对象
            self._cleanup_ocr_memory(region)
            
            # 清理多个对象
            self._cleanup_ocr_memory(region, image_tensor, ret)
            
            # 清理并强制 GPU 清理
            self._cleanup_ocr_memory(region, image_tensor, force_gpu_cleanup=True)
        """
#         import gc
        
        # 删除传入的对象
        for obj in objects:
            try:
                del obj
            except Exception:
                pass
        
        # 如果使用 GPU 或强制清理，清理 GPU 显存
        if force_gpu_cleanup or (hasattr(self, 'use_gpu') and self.use_gpu):
            try:
                import torch
                if torch.cuda.is_available():
                    pass
            except Exception:
                pass
        
        # 轻量级垃圾回收（不强制完整 GC，避免性能影响）
        # 主进程会在批次结束时进行完整的 gc.collect()
        
    def _cleanup_batch_data(self, *data_lists, force_gpu_cleanup: bool = False):
        """
        清理批量数据（列表、字典等容器）
        
        Args:
            *data_lists: 要清理的数据容器（list, dict 等）
            force_gpu_cleanup: 是否强制清理 GPU 显存
            
        Example:
            # 清理列表
            self._cleanup_batch_data(region_imgs, quadrilaterals)
            
            # 清理字典
            self._cleanup_batch_data(out_regions, texts)
        """
#         import gc
        
        for data in data_lists:
            if data is None:
                continue
                
            try:
                if isinstance(data, list):
                    data.clear()
                elif isinstance(data, dict):
                    data.clear()
                del data
            except Exception:
                pass
        
        # GPU 清理
        if force_gpu_cleanup or (hasattr(self, 'use_gpu') and self.use_gpu):
            try:
                import torch
                if torch.cuda.is_available():
                    pass
            except Exception:
                pass

    def _get_ocr_canvas_width(self, valid_widths: List[int], base_align: int = 4, extra_pad: int = 0) -> int:
        """
        Normalize OCR canvas width to reduce dynamic-shape explosion on cuDNN.
        Env vars:
        - MANGA_OCR_FIXED_WIDTH: force a minimum fixed width when > 0
        - MANGA_OCR_WIDTH_BUCKET: round width up to this bucket on GPU (default: 256)
        """
        max_content_width = max(valid_widths) + max(0, int(extra_pad))
        base_align = max(1, int(base_align))
        aligned_width = base_align * ((max_content_width + base_align - 1) // base_align)

        # CPU path keeps the original fine-grained width for lower memory overhead.
        if not (hasattr(self, 'use_gpu') and self.use_gpu):
            return aligned_width

        fixed_width = int(os.environ.get("MANGA_OCR_FIXED_WIDTH", "0") or 0)
        if fixed_width > 0:
            return max(aligned_width, fixed_width)

        bucket = int(os.environ.get("MANGA_OCR_WIDTH_BUCKET", "256") or 1)
        if bucket <= 1:
            return aligned_width
        return bucket * ((aligned_width + bucket - 1) // bucket)

