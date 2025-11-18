"""
智能长图拼接器模块

功能:
1. 将连续漫画图片拼接成长图（高度≤15000px）
2. 检测边界气泡，避免分切文字
3. 合并跨页分割的气泡
4. 优化翻译上下文连贯性
"""

import cv2
import numpy as np
from typing import List, Tuple, Dict, Optional
from PIL import Image
import logging

logger = logging.getLogger('manga_translator')


class BubbleBoundaryDetector:
    """气泡边界检测器"""
    
    def __init__(self, margin: int = 100, min_bubble_area: int = 200):
        """
        Args:
            margin: 边界检测区域高度（像素）
            min_bubble_area: 最小气泡面积（像素²）
        """
        self.margin = margin
        self.min_bubble_area = min_bubble_area
    
    def detect_boundary_bubbles(self, img_above: np.ndarray, img_below: np.ndarray) -> Dict[str, any]:
        """
        检测两张图片边界的气泡情况
        
        Args:
            img_above: 上方图片
            img_below: 下方图片
        
        Returns:
            {
                'status': 'safe' | 'bottom_bubble' | 'top_bubble' | 'both',
                'bottom_bubbles': List[dict],  # 底部气泡列表
                'top_bubbles': List[dict],     # 顶部气泡列表
                'safe_to_split': bool          # 是否可以安全分段
            }
        """
        # 提取边界区域
        h_above = img_above.shape[0]
        h_below = img_below.shape[0]
        
        bottom_region = img_above[max(0, h_above - self.margin):h_above, :]
        top_region = img_below[0:min(self.margin, h_below), :]
        
        # 检测气泡
        bottom_bubbles = self._detect_bubbles_in_region(bottom_region)
        top_bubbles = self._detect_bubbles_in_region(top_region)
        
        # 判断边界状态
        has_bottom = len(bottom_bubbles) > 0
        has_top = len(top_bubbles) > 0
        
        if not has_bottom and not has_top:
            status = 'safe'
            safe = True
        elif has_bottom and not has_top:
            status = 'bottom_bubble'
            safe = False
        elif not has_bottom and has_top:
            status = 'top_bubble'
            safe = False
        else:
            status = 'both'  # 可能是跨页气泡
            safe = False
        
        return {
            'status': status,
            'bottom_bubbles': bottom_bubbles,
            'top_bubbles': top_bubbles,
            'safe_to_split': safe
        }
    
    def _detect_bubbles_in_region(self, region: np.ndarray) -> List[Dict]:
        """
        检测区域中的气泡（基于轮廓检测）
        
        Args:
            region: 图像区域
        
        Returns:
            气泡列表，每个气泡包含 x, y, w, h, area
        """
        if region.size == 0:
            return []
        
        # 转灰度
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        
        # 自适应阈值
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            11, 2
        )
        
        # 形态学操作，连接断开的文字
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        # 查找轮廓
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        
        # 过滤有效气泡
        bubbles = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.min_bubble_area:
                continue
            
            x, y, w, h = cv2.boundingRect(contour)
            
            # 过滤太小或太大的区域
            if w < 10 or h < 10:
                continue
            if w > region.shape[1] * 0.95:  # 太宽，可能是整行
                continue
            
            bubbles.append({
                'x': x,
                'y': y,
                'w': w,
                'h': h,
                'area': area
            })
        
        return bubbles


class SmartImageStitcher:
    """智能图片拼接器"""
    
    def __init__(
        self,
        max_height: int = 15000,
        bubble_margin: int = 100,
        min_images_per_segment: int = 2,
        search_range: int = 5
    ):
        """
        Args:
            max_height: 长图最大高度（像素）
            bubble_margin: 边界检测区域高度（像素）
            min_images_per_segment: 每段最少图片数
            search_range: 向前搜索安全点的范围
        """
        self.max_height = max_height
        self.bubble_margin = bubble_margin
        self.min_images_per_segment = min_images_per_segment
        self.search_range = search_range
        
        self.detector = BubbleBoundaryDetector(margin=bubble_margin)
    
    def stitch_images(
        self,
        images_with_configs: List[Tuple]
    ) -> List[Tuple[np.ndarray, List[Tuple], Dict]]:
        """
        智能拼接图片
        
        Args:
            images_with_configs: [(image, config), ...] 原始图片和配置列表
        
        Returns:
            [
                (stitched_image, original_images_configs, metadata),
                ...
            ]
            每个元组包含:
            - stitched_image: 拼接后的长图
            - original_images_configs: 该段包含的原始(image, config)列表
            - metadata: 元数据（分段信息、气泡信息等）
        """
        if not images_with_configs:
            return []
        
        total_images = len(images_with_configs)
        logger.info(f"[长图拼接] 开始处理 {total_images} 张图片")
        
        # 1. 计算每张图片的高度
        images = [img for img, _ in images_with_configs]
        heights = [self._get_image_height(img) for img in images]
        
        # 2. 找到最优分段点
        segment_indices = self._find_optimal_segments(images, heights)
        
        logger.info(f"[长图拼接] 分段策略: {len(segment_indices)} 个段")
        for i, seg_idx in enumerate(segment_indices):
            seg_heights = [heights[j] for j in seg_idx]
            total_h = sum(seg_heights)
            logger.info(f"  段{i+1}: {len(seg_idx)}张图, 总高度{total_h}px, 图片索引{seg_idx[0]}-{seg_idx[-1]}")
        
        # 3. 拼接每个段
        stitched_segments = []
        for seg_idx in segment_indices:
            # 获取该段的图片和配置
            segment_images_configs = [images_with_configs[i] for i in seg_idx]
            segment_images = [img for img, _ in segment_images_configs]
            
            # 拼接图片
            stitched_img = self._merge_images_vertical(segment_images)
            
            # 元数据
            metadata = {
                'image_count': len(seg_idx),
                'total_height': stitched_img.shape[0],
                'image_indices': seg_idx,
                'individual_heights': [heights[i] for i in seg_idx]
            }
            
            stitched_segments.append((stitched_img, segment_images_configs, metadata))
        
        logger.info(f"[长图拼接] 完成: {total_images}张图 → {len(stitched_segments)}个长图段")
        
        return stitched_segments
    
    def _find_optimal_segments(
        self,
        images: List[np.ndarray],
        heights: List[int]
    ) -> List[List[int]]:
        """
        找到最优分段点
        
        Args:
            images: 图片列表
            heights: 每张图片的高度
        
        Returns:
            分段索引列表，例如 [[0,1,2], [3,4,5,6], [7,8,9]]
        """
        segments = []
        current_segment = []
        current_height = 0
        
        for i, (img, h) in enumerate(zip(images, heights)):
            predicted_height = current_height + h
            
            # 检查是否会超过阈值
            if predicted_height <= self.max_height:
                # 未超限，直接添加
                current_segment.append(i)
                current_height += h
            else:
                # 超限，需要分段
                if len(current_segment) >= self.min_images_per_segment:
                    # 当前段有足够的图片，寻找最佳分段点
                    split_point = self._find_safe_split_point(
                        images,
                        current_segment,
                        i
                    )
                    
                    # 保存当前段
                    segments.append(current_segment[:split_point])
                    
                    # 开始新段（包含未拼接的图片）
                    remaining_indices = current_segment[split_point:] + [i]
                    current_segment = remaining_indices
                    current_height = sum(heights[j] for j in current_segment)
                else:
                    # 当前段图片太少，强制保存并开始新段
                    logger.warning(f"[长图拼接] 段{len(segments)+1}图片数不足({len(current_segment)}张)，强制分段")
                    segments.append(current_segment)
                    current_segment = [i]
                    current_height = h
        
        # 保存最后一段
        if current_segment:
            segments.append(current_segment)
        
        return segments
    
    def _find_safe_split_point(
        self,
        images: List[np.ndarray],
        current_segment: List[int],
        next_idx: int
    ) -> int:
        """
        在当前段中寻找安全的分段点
        
        Args:
            images: 所有图片
            current_segment: 当前段的图片索引
            next_idx: 下一张要添加的图片索引
        
        Returns:
            分段点索引（相对于current_segment）
        """
        # 从后向前搜索
        search_count = min(self.search_range, len(current_segment) - 1)
        
        best_point = len(current_segment)  # 默认：当前段末尾
        best_score = -1
        
        for i in range(search_count):
            # 检查倒数第i个位置
            check_idx = len(current_segment) - i - 1
            
            if check_idx <= 0:
                break
            
            # 获取边界的两张图片
            img_above_idx = current_segment[check_idx - 1]
            img_below_idx = current_segment[check_idx]
            
            img_above = self._load_image_as_array(images[img_above_idx])
            img_below = self._load_image_as_array(images[img_below_idx])
            
            # 检测边界
            boundary_info = self.detector.detect_boundary_bubbles(img_above, img_below)
            
            # 评分
            if boundary_info['safe_to_split']:
                # 找到安全点，立即返回
                logger.info(f"[长图拼接] 找到安全分段点: 第{img_above_idx+1}-{img_below_idx+1}张图之间")
                return check_idx
            
            # 计算分数（优先选择只有一侧气泡的点）
            status = boundary_info['status']
            if status == 'top_bubble':
                score = 2  # 下图顶部有气泡（较好）
            elif status == 'bottom_bubble':
                score = 1  # 上图底部有气泡
            else:  # both
                score = 0  # 两侧都有气泡（最差）
            
            if score > best_score:
                best_score = score
                best_point = check_idx
        
        if best_score >= 0:
            logger.warning(f"[长图拼接] 未找到完全安全的分段点，使用最佳位置（分数:{best_score}）")
        
        return best_point
    
    def _merge_images_vertical(self, images: List) -> np.ndarray:
        """
        纵向合并图片
        
        Args:
            images: 图片列表（可以是PIL.Image或np.ndarray）
        
        Returns:
            合并后的图片（np.ndarray）
        """
        if len(images) == 1:
            return self._load_image_as_array(images[0])
        
        # 转换所有图片为numpy数组
        img_arrays = [self._load_image_as_array(img) for img in images]
        
        # 获取最大宽度
        max_width = max(img.shape[1] for img in img_arrays)
        total_height = sum(img.shape[0] for img in img_arrays)
        
        # 创建空白画布（白色背景）
        result = np.ones((total_height, max_width, 3), dtype=np.uint8) * 255
        
        # 拼接图片（居中对齐）
        y_offset = 0
        for img in img_arrays:
            h, w = img.shape[:2]
            x_offset = (max_width - w) // 2  # 居中
            result[y_offset:y_offset+h, x_offset:x_offset+w] = img
            y_offset += h
        
        return result
    
    def _load_image_as_array(self, img) -> np.ndarray:
        """
        将图片加载为numpy数组
        
        Args:
            img: PIL.Image 或 np.ndarray 或文件路径
        
        Returns:
            np.ndarray (H, W, 3) BGR格式
        """
        if isinstance(img, np.ndarray):
            # 已经是数组
            if len(img.shape) == 2:
                # 灰度图，转换为BGR
                return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            elif img.shape[2] == 4:
                # RGBA，转换为BGR
                return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            return img
        elif isinstance(img, Image.Image):
            # PIL Image
            img_array = np.array(img)
            if len(img_array.shape) == 2:
                return cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)
            elif img_array.shape[2] == 4:
                return cv2.cvtColor(img_array, cv2.COLOR_RGBA2BGR)
            elif img_array.shape[2] == 3:
                # RGB转BGR
                return cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            return img_array
        elif isinstance(img, str):
            # 文件路径
            return cv2.imread(img)
        else:
            # 尝试作为PIL Image处理
            try:
                img_array = np.array(img)
                if len(img_array.shape) == 2:
                    return cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)
                return img_array
            except Exception as e:
                logger.error(f"[长图拼接] 无法加载图片: {type(img)}, 错误: {e}")
                raise
    
    def _get_image_height(self, img) -> int:
        """获取图片高度"""
        if isinstance(img, np.ndarray):
            return img.shape[0]
        elif isinstance(img, Image.Image):
            return img.size[1]
        elif isinstance(img, str):
            img_obj = cv2.imread(img)
            return img_obj.shape[0] if img_obj is not None else 0
        else:
            # 尝试获取属性
            try:
                img_array = np.array(img)
                return img_array.shape[0]
            except:
                return 0


class BubbleMerger:
    """跨页气泡合并器"""
    
    def __init__(self, overlap_threshold: float = 0.5):
        """
        Args:
            overlap_threshold: 重叠阈值（0-1），用于判断是否为同一气泡
        """
        self.overlap_threshold = overlap_threshold
    
    def merge_cross_page_bubbles(
        self,
        text_regions_list: List[List],
        split_points: List[int]
    ) -> List[List]:
        """
        合并跨页分割的气泡
        
        Args:
            text_regions_list: 每张图的文字区域列表
            split_points: 原始图片的分段点（图片索引）
        
        Returns:
            合并后的文字区域列表
        """
        # TODO: 实现跨页气泡合并逻辑
        # 1. 检测分段点附近的气泡
        # 2. 判断是否为跨页气泡（位置、尺寸相似）
        # 3. 合并文本内容
        # 4. 调整坐标
        
        return text_regions_list


# 便捷函数
def create_stitcher(
    max_height: int = 15000,
    bubble_margin: int = 100,
    **kwargs
) -> SmartImageStitcher:
    """
    创建智能拼接器实例
    
    Args:
        max_height: 长图最大高度
        bubble_margin: 边界检测区域
        **kwargs: 其他参数
    
    Returns:
        SmartImageStitcher实例
    """
    return SmartImageStitcher(
        max_height=max_height,
        bubble_margin=bubble_margin,
        **kwargs
    )
