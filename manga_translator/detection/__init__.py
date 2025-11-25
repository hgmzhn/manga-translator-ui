import numpy as np
import cv2
from typing import List

from .default import DefaultDetector
from .dbnet_convnext import DBConvNextDetector
from .ctd import ComicTextDetector
from .craft import CRAFTDetector
# from .paddle_rust import PaddleDetector  # 已移除
from .none import NoneDetector
from .yolo_obb import YOLOOBBDetector
from .common import CommonDetector, OfflineDetector
from ..config import Detector
from ..utils import Quadrilateral

DETECTORS = {
    Detector.default: DefaultDetector,
    Detector.dbconvnext: DBConvNextDetector,
    Detector.ctd: ComicTextDetector,
    Detector.craft: CRAFTDetector,
    # Detector.paddle: PaddleDetector,  # 已移除
    Detector.none: NoneDetector,
}
detector_cache = {}

def get_detector(key: Detector, *args, **kwargs) -> CommonDetector:
    if key not in DETECTORS:
        raise ValueError(f'Could not find detector for: "{key}". Choose from the following: %s' % ','.join(DETECTORS))
    if not detector_cache.get(key):
        detector = DETECTORS[key]
        detector_cache[key] = detector(*args, **kwargs)
    return detector_cache[key]

async def prepare(detector_key: Detector):
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.download()

async def dispatch(detector_key: Detector, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                   invert: bool, gamma_correct: bool, rotate: bool, auto_rotate: bool = False, device: str = 'cpu', verbose: bool = False,
                   use_yolo_obb: bool = False, yolo_obb_conf: float = 0.4, yolo_obb_iou: float = 0.6, yolo_obb_overlap_threshold: float = 0.1, min_box_area_ratio: float = 0.0009):
    """
    检测调度函数，支持混合检测模式
    
    Args:
        use_yolo_obb: 是否启用YOLO OBB辅助检测器
        yolo_obb_conf: YOLO OBB检测器的置信度阈值
        yolo_obb_iou: YOLO OBB检测器的IoU阈值（交叉比）
        min_box_area_ratio: 最小检测框面积占比（相对图片总像素）
    """
    # 主检测器检测
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.load(device)
    main_textlines, mask, raw_image = await detector.detect(image, detect_size, text_threshold, box_threshold, unclip_ratio, invert, gamma_correct, rotate, auto_rotate, verbose, min_box_area_ratio)
    
    # 如果不启用YOLO OBB，直接返回主检测器结果
    if not use_yolo_obb:
        return main_textlines, mask, raw_image
    
    # YOLO OBB辅助检测
    try:
        yolo_detector = get_detector_instance('yolo_obb', YOLOOBBDetector)
        await yolo_detector.load(device)
        
        # YOLO OBB检测（使用yolo_obb_conf作为text_threshold）
        yolo_textlines, _, _ = await yolo_detector.detect(
            image, detect_size, yolo_obb_conf, box_threshold, unclip_ratio,
            invert, gamma_correct, rotate, auto_rotate, verbose
        )
        
        # 智能合并：YOLO框可以替换过小的主检测器框，或添加新框
        combined_textlines = merge_detection_boxes(yolo_textlines, main_textlines, overlap_threshold=yolo_obb_overlap_threshold)
        
        replaced_count = len(main_textlines) + len(yolo_textlines) - len(combined_textlines)
        detector.logger.info(f"混合检测: 主检测器={len(main_textlines)}, YOLO OBB={len(yolo_textlines)}, "
                           f"替换={replaced_count}, 总计={len(combined_textlines)}")
        
        # 生成调试图片（如果verbose=True）
        debug_img = None
        if verbose:
            debug_img = draw_detection_debug_image(image, main_textlines, yolo_textlines, yolo_obb_overlap_threshold)
            detector.logger.info("已生成混合检测调试图片")
        
        return combined_textlines, mask, debug_img if debug_img is not None else raw_image
    
    except Exception as e:
        detector.logger.error(f"YOLO OBB辅助检测失败: {e}")
        # 失败时返回主检测器结果
        return main_textlines, mask, raw_image


def get_detector_instance(key: str, detector_class):
    """获取或创建检测器实例（用于辅助检测器）"""
    if key not in detector_cache:
        detector_cache[key] = detector_class()
    return detector_cache[key]


def merge_detection_boxes(yolo_boxes: List[Quadrilateral], main_boxes: List[Quadrilateral], overlap_threshold: float = 0.1) -> List[Quadrilateral]:
    """
    合并主检测器和YOLO检测器的框，智能替换逻辑：
    1. 如果YOLO框与主检测器框重叠
    2. 且YOLO框完全包含主检测器框
    3. 且YOLO框面积 >= 主检测器框面积 * 2
    4. 且YOLO框与其他未替换的主检测器框的重叠率 < overlap_threshold
    5. 则删除被包含的主检测器框，使用YOLO框替代
    6. 其他情况：如果重叠率 >= overlap_threshold，删除重叠的YOLO框，保留主检测器框
    7. 不重叠或重叠率 < overlap_threshold 的YOLO框直接添加
    
    Args:
        yolo_boxes: YOLO OBB检测器的检测框
        main_boxes: 主检测器的检测框
        overlap_threshold: 重叠率阈值（0.0-1.0）。重叠率 >= 该值时删除YOLO框。设为1.0则保留所有框。
    
    Returns:
        合并后的检测框列表
    """
    if len(main_boxes) == 0:
        return yolo_boxes
    
    if len(yolo_boxes) == 0:
        return main_boxes
    
    # 标记要移除的主检测器框索引
    main_boxes_to_remove = set()
    # 标记要移除的YOLO框索引
    yolo_boxes_to_remove = set()
    # 要添加的YOLO框（用于替换）
    yolo_boxes_to_add_set = set()  # 使用set避免重复
    
    for yolo_idx, yolo_box in enumerate(yolo_boxes):
        # 计算YOLO框的AABB和面积
        yolo_min_x = np.min(yolo_box.pts[:, 0])
        yolo_max_x = np.max(yolo_box.pts[:, 0])
        yolo_min_y = np.min(yolo_box.pts[:, 1])
        yolo_max_y = np.max(yolo_box.pts[:, 1])
        yolo_area = (yolo_max_x - yolo_min_x) * (yolo_max_y - yolo_min_y)
        
        # 检查这个YOLO框是否满足任何替换条件
        can_replace = False
        max_overlap_ratio_with_others = 0.0  # 与其他未替换主框的最大重叠率
        replaced_main_indices = set()  # 被这个YOLO框替换的主框索引
        
        for main_idx, main_box in enumerate(main_boxes):
            # 计算主检测器框的AABB和面积
            main_min_x = np.min(main_box.pts[:, 0])
            main_max_x = np.max(main_box.pts[:, 0])
            main_min_y = np.min(main_box.pts[:, 1])
            main_max_y = np.max(main_box.pts[:, 1])
            main_area = (main_max_x - main_min_x) * (main_max_y - main_min_y)
            
            # 检查是否有重叠
            if not (yolo_max_x < main_min_x or yolo_min_x > main_max_x or
                    yolo_max_y < main_min_y or yolo_min_y > main_max_y):
                # 有重叠，计算重叠面积
                inter_min_x = max(yolo_min_x, main_min_x)
                inter_max_x = min(yolo_max_x, main_max_x)
                inter_min_y = max(yolo_min_y, main_min_y)
                inter_max_y = min(yolo_max_y, main_max_y)
                inter_area = (inter_max_x - inter_min_x) * (inter_max_y - inter_min_y)
                
                # 计算重叠率（相对于较小框的比例）
                overlap_ratio = inter_area / min(yolo_area, main_area) if min(yolo_area, main_area) > 0 else 0
                
                # 检查YOLO框是否完全包含主检测器框
                contains = (yolo_min_x <= main_min_x and yolo_max_x >= main_max_x and
                           yolo_min_y <= main_min_y and yolo_max_y >= main_max_y)
                
                # 检查面积条件
                area_ratio = yolo_area / main_area if main_area > 0 else 0
                
                if contains and area_ratio >= 2.0:
                    # 满足替换条件：标记主检测器框为待删除，记录索引
                    replaced_main_indices.add(main_idx)
                    can_replace = True
                else:
                    # 不满足替换条件，记录与其他主框的重叠率
                    max_overlap_ratio_with_others = max(max_overlap_ratio_with_others, overlap_ratio)
        
        # 决定这个YOLO框的命运
        if can_replace:
            # 满足了替换条件，但还需要检查与其他未替换主框的重叠率
            if max_overlap_ratio_with_others >= overlap_threshold:
                # 与其他主框重叠率过高，删除这个YOLO框，不进行替换
                yolo_boxes_to_remove.add(yolo_idx)
            else:
                # 可以安全替换：删除被替换的主框，添加YOLO框
                for main_idx in replaced_main_indices:
                    main_boxes_to_remove.add(main_idx)
                yolo_boxes_to_add_set.add(yolo_idx)
        else:
            # 不满足替换条件，按原有逻辑处理
            if max_overlap_ratio_with_others >= overlap_threshold:
                # 有重叠且重叠率 >= 阈值，删除这个YOLO框
                yolo_boxes_to_remove.add(yolo_idx)
            # else: 没有重叠或重叠率 < 阈值，会在后面作为新框添加
    
    # 构建最终结果
    result = []
    
    # 添加未被移除的主检测器框
    for idx, main_box in enumerate(main_boxes):
        if idx not in main_boxes_to_remove:
            result.append(main_box)
    
    # 添加YOLO框（替换的 + 不重叠的新框）
    for idx, yolo_box in enumerate(yolo_boxes):
        # 如果不在删除列表中，就添加（包括替换框和新框）
        if idx not in yolo_boxes_to_remove:
            result.append(yolo_box)
    
    return result

def draw_detection_debug_image(image: np.ndarray, main_boxes: List[Quadrilateral], yolo_boxes: List[Quadrilateral], overlap_threshold: float = 0.1) -> np.ndarray:
    """
    绘制检测框调试图片，并标注重叠率
    
    Args:
        image: 原始图像
        main_boxes: 主检测器的检测框
        yolo_boxes: YOLO检测器的检测框
        overlap_threshold: 重叠率阈值
    
    Returns:
        绘制了检测框的调试图片
    """
    # 创建图像副本
    debug_img = image.copy()
    
    # 绘制主检测器的框（绿色）
    for box in main_boxes:
        pts = box.pts.astype(np.int32)
        cv2.polylines(debug_img, [pts], True, (0, 255, 0), 2)
        # 添加标签
        cv2.putText(debug_img, "Main", tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    
    # 绘制YOLO检测器的框（蓝色），并计算重叠率
    for yolo_idx, yolo_box in enumerate(yolo_boxes):
        pts = yolo_box.pts.astype(np.int32)
        
        # 计算与主检测器框的最大重叠率
        yolo_min_x = np.min(yolo_box.pts[:, 0])
        yolo_max_x = np.max(yolo_box.pts[:, 0])
        yolo_min_y = np.min(yolo_box.pts[:, 1])
        yolo_max_y = np.max(yolo_box.pts[:, 1])
        yolo_area = (yolo_max_x - yolo_min_x) * (yolo_max_y - yolo_min_y)
        
        max_overlap_ratio = 0.0
        for main_box in main_boxes:
            main_min_x = np.min(main_box.pts[:, 0])
            main_max_x = np.max(main_box.pts[:, 0])
            main_min_y = np.min(main_box.pts[:, 1])
            main_max_y = np.max(main_box.pts[:, 1])
            main_area = (main_max_x - main_min_x) * (main_max_y - main_min_y)
            
            # 检查是否有重叠
            if not (yolo_max_x < main_min_x or yolo_min_x > main_max_x or
                    yolo_max_y < main_min_y or yolo_min_y > main_max_y):
                # 计算重叠面积
                inter_min_x = max(yolo_min_x, main_min_x)
                inter_max_x = min(yolo_max_x, main_max_x)
                inter_min_y = max(yolo_min_y, main_min_y)
                inter_max_y = min(yolo_max_y, main_max_y)
                inter_area = (inter_max_x - inter_min_x) * (inter_max_y - inter_min_y)
                
                # 计算重叠率
                overlap_ratio = inter_area / min(yolo_area, main_area) if min(yolo_area, main_area) > 0 else 0
                max_overlap_ratio = max(max_overlap_ratio, overlap_ratio)
        
        # 根据重叠率选择颜色 (RGB格式)
        if max_overlap_ratio >= overlap_threshold:
            # 重叠率超过阈值，用红色表示（会被删除）
            color = (255, 0, 0)  # Red
            label = f"YOLO:{max_overlap_ratio:.2f}(X)"
        else:
            # 重叠率低于阈值，用蓝色表示（会保留）
            color = (0, 0, 255)  # Blue
            label = f"YOLO:{max_overlap_ratio:.2f}"
        
        cv2.polylines(debug_img, [pts], True, color, 2)
        cv2.putText(debug_img, label, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    
    # 在图像顶部添加阈值信息
    info_text = f"Overlap Threshold: {overlap_threshold:.2f} | Green=Main, Blue=YOLO(Keep), Red=YOLO(Removed)"
    cv2.putText(debug_img, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(debug_img, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    
    # 添加说明：YOLO框已经过NMS去重
    note_text = "Note: YOLO boxes are already NMS-filtered"
    cv2.putText(debug_img, note_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    cv2.putText(debug_img, note_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    
    return debug_img

async def unload(detector_key: Detector):
    detector_cache.pop(detector_key, None)
