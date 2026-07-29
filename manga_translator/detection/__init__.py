from typing import List, Optional

import cv2
import numpy as np

from ..config import Detector
from ..utils import Quadrilateral
from ..utils.bubble import is_bubble_advanced
from .common import CommonDetector, OfflineDetector
from .craft import CRAFTDetector
from .ctd import ComicTextDetector
from .dbnet_convnext import DBConvNextDetector
from .default import DefaultDetector

# from .paddle_rust import PaddleDetector  # 已移除
from .none import NoneDetector
from .yolo_obb import YOLOOBBDetector

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
                   device: str = 'cpu', verbose: bool = False,
                   use_yolo_obb: bool = False, yolo_obb_conf: float = 0.4, yolo_obb_overlap_threshold: float = 0.1, min_box_area_ratio: float = 0.0009,
                   result_path_fn=None, det_rearrange_min_effective_short_side: float = 341.0,
                   use_sfx_filter: bool = False):
    """
    检测调度函数，支持混合检测模式
    
    Args:
        use_yolo_obb: 是否启用YOLO OBB辅助检测器
        use_sfx_filter: 是否过滤既不在气泡内、也未被 other 包裹、也未与 YOLO 文本框重叠的主检测框
        yolo_obb_conf: YOLO OBB检测器的置信度阈值
        min_box_area_ratio: 最小检测框面积占比（相对图片总像素）
        result_path_fn: 结果路径生成函数（用于保存调试图）
        det_rearrange_min_effective_short_side: 长图检测重排后的最低有效短边分辨率
    """
    # 主检测器检测
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.load(device)
    main_textlines, mask, raw_image = await detector.detect(
        image,
        detect_size,
        text_threshold,
        box_threshold,
        unclip_ratio,
        verbose,
        min_box_area_ratio,
        result_path_fn,
        det_rearrange_min_effective_short_side,
    )
    
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
            verbose, min_box_area_ratio, result_path_fn,
            det_rearrange_min_effective_short_side,
        )
        
        # 智能合并：YOLO框可以替换过小的主检测器框，或添加新框
        combined_textlines = merge_detection_boxes(
            yolo_textlines,
            main_textlines,
            overlap_threshold=yolo_obb_overlap_threshold,
            use_sfx_filter=use_sfx_filter,
            image=image,
        )
        
        replaced_count = len(main_textlines) + len(yolo_textlines) - len(combined_textlines)
        detector.logger.info(f"混合检测: 主检测器={len(main_textlines)}, YOLO OBB={len(yolo_textlines)}, "
                           f"替换/移除={replaced_count}, "
                           f"总计={len(combined_textlines)}")
        
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

def _get_box_label(box: Quadrilateral) -> Optional[str]:
    label = getattr(box, 'det_label', None)
    if label is None:
        label = getattr(box, 'yolo_label', None)
    if isinstance(label, str):
        label = label.strip().lower()
        if label:
            return label
    return None

def _apply_yolo_label_infection(main_boxes: List[Quadrilateral], yolo_boxes: List[Quadrilateral], min_overlap_ratio: float = 0.05) -> None:
    """
    让每个 YOLO 框按照重叠率“感染”一个主检测器框标签（最多感染一个）。
    只写入标签，不改动框几何。
    """
    if not main_boxes or not yolo_boxes:
        return

    for yolo_box in yolo_boxes:
        yolo_label = _get_box_label(yolo_box)
        if not yolo_label:
            continue
        # other 仅用于包裹辅助，不参与标签感染
        if yolo_label == 'other':
            continue

        yolo_min_x = np.min(yolo_box.pts[:, 0])
        yolo_max_x = np.max(yolo_box.pts[:, 0])
        yolo_min_y = np.min(yolo_box.pts[:, 1])
        yolo_max_y = np.max(yolo_box.pts[:, 1])
        yolo_area = (yolo_max_x - yolo_min_x) * (yolo_max_y - yolo_min_y)
        if yolo_area <= 0:
            continue

        best_idx = -1
        best_overlap = 0.0
        for idx, main_box in enumerate(main_boxes):
            main_min_x = np.min(main_box.pts[:, 0])
            main_max_x = np.max(main_box.pts[:, 0])
            main_min_y = np.min(main_box.pts[:, 1])
            main_max_y = np.max(main_box.pts[:, 1])
            main_area = (main_max_x - main_min_x) * (main_max_y - main_min_y)
            if main_area <= 0:
                continue

            inter_min_x = max(yolo_min_x, main_min_x)
            inter_max_x = min(yolo_max_x, main_max_x)
            inter_min_y = max(yolo_min_y, main_min_y)
            inter_max_y = min(yolo_max_y, main_max_y)
            inter_w = inter_max_x - inter_min_x
            inter_h = inter_max_y - inter_min_y
            if inter_w <= 0 or inter_h <= 0:
                continue

            inter_area = inter_w * inter_h
            overlap_ratio = inter_area / min(yolo_area, main_area)
            if overlap_ratio > best_overlap:
                best_overlap = overlap_ratio
                best_idx = idx

        if best_idx >= 0 and best_overlap >= min_overlap_ratio:
            infected_box = main_boxes[best_idx]
            infected_box.det_label = yolo_label
            infected_box.yolo_label = yolo_label
            infected_box.yolo_infected = True


def _box_aabb(box: Quadrilateral):
    min_x = float(np.min(box.pts[:, 0]))
    max_x = float(np.max(box.pts[:, 0]))
    min_y = float(np.min(box.pts[:, 1]))
    max_y = float(np.max(box.pts[:, 1]))
    return min_x, max_x, min_y, max_y


def _contains_interval(outer_min: float, outer_max: float, inner_min: float, inner_max: float, eps: float = 2.0) -> bool:
    return outer_min <= inner_min + eps and outer_max >= inner_max - eps


def _aabb_contains(outer_aabb, inner_aabb, eps: float = 2.0) -> bool:
    ox1, ox2, oy1, oy2 = outer_aabb
    ix1, ix2, iy1, iy2 = inner_aabb
    return (
        _contains_interval(ox1, ox2, ix1, ix2, eps=eps)
        and _contains_interval(oy1, oy2, iy1, iy2, eps=eps)
    )


def _box_direction(box: Quadrilateral) -> Optional[str]:
    direction = getattr(box, 'assigned_direction', None) or getattr(box, 'direction', None)
    if isinstance(direction, str):
        direction = direction.strip().lower()
    return direction if direction in ('h', 'v') else None


def _is_axis_wrapped_pair(box_a: Quadrilateral, box_b: Quadrilateral, eps: float = 2.0) -> bool:
    """
    主轴包裹判定（按用户规则）：
    - 竖排(v)：看 X 区间是否一方包含另一方
    - 横排(h)：看 Y 区间是否一方包含另一方
    """
    dir_a = _box_direction(box_a)
    dir_b = _box_direction(box_b)
    if dir_a is None or dir_b is None or dir_a != dir_b:
        return False

    a_min_x, a_max_x, a_min_y, a_max_y = _box_aabb(box_a)
    b_min_x, b_max_x, b_min_y, b_max_y = _box_aabb(box_b)

    if dir_a == 'v':
        return (
            _contains_interval(a_min_x, a_max_x, b_min_x, b_max_x, eps=eps)
            or _contains_interval(b_min_x, b_max_x, a_min_x, a_max_x, eps=eps)
        )

    # dir_a == 'h'
    return (
        _contains_interval(a_min_y, a_max_y, b_min_y, b_max_y, eps=eps)
        or _contains_interval(b_min_y, b_max_y, a_min_y, a_max_y, eps=eps)
    )


def _aabb_overlap_ratio(box_a: Quadrilateral, box_b: Quadrilateral) -> float:
    """返回两个 AABB 交集占较小框面积的比例。"""
    a_min_x, a_max_x, a_min_y, a_max_y = _box_aabb(box_a)
    b_min_x, b_max_x, b_min_y, b_max_y = _box_aabb(box_b)
    a_area = max(0.0, a_max_x - a_min_x) * max(0.0, a_max_y - a_min_y)
    b_area = max(0.0, b_max_x - b_min_x) * max(0.0, b_max_y - b_min_y)
    min_area = min(a_area, b_area)
    if min_area <= 0:
        return 0.0

    inter_w = max(0.0, min(a_max_x, b_max_x) - max(a_min_x, b_min_x))
    inter_h = max(0.0, min(a_max_y, b_max_y) - max(a_min_y, b_min_y))
    return (inter_w * inter_h) / min_area


def _get_sfx_filtered_main_indices(
    main_boxes: List[Quadrilateral],
    yolo_boxes: List[Quadrilateral],
    overlap_threshold: float,
    wrap_eps: float = 2.0,
    image: Optional[np.ndarray] = None,
) -> set[int]:
    """
    找出缺少 YOLO 支持的主检测框：
    - YOLO `other` 必须完整包裹主框；或
    - 任一非 `other` YOLO 框与主框的重叠率达到阈值。
    两项均不满足时，再复用气泡检测；气泡内文本仍保留。
    """
    # 即使用户把合并阈值设为 0，也仍要求存在真实交集，避免任意 YOLO 框
    # 让整页所有主检测框都通过过滤。
    threshold = max(1e-6, min(1.0, float(overlap_threshold)))
    filtered_indices = set()

    for main_idx, main_box in enumerate(main_boxes):
        main_aabb = _box_aabb(main_box)
        supported = False
        for yolo_box in yolo_boxes:
            yolo_label = _get_box_label(yolo_box)
            if yolo_label == 'other':
                if _aabb_contains(_box_aabb(yolo_box), main_aabb, eps=wrap_eps):
                    supported = True
                    break
                continue

            if _aabb_overlap_ratio(main_box, yolo_box) >= threshold:
                supported = True
                break

        if not supported and image is not None:
            min_x, max_x, min_y, max_y = main_aabb
            image_h, image_w = image.shape[:2]
            x1 = max(0, int(np.floor(min_x)))
            y1 = max(0, int(np.floor(min_y)))
            x2 = min(image_w, int(np.ceil(max_x)))
            y2 = min(image_h, int(np.ceil(max_y)))
            supported = x2 > x1 and y2 > y1 and is_bubble_advanced(
                image, x1, y1, x2 - x1, y2 - y1)

        if not supported:
            filtered_indices.add(main_idx)

    return filtered_indices


def merge_detection_boxes(
    yolo_boxes: List[Quadrilateral],
    main_boxes: List[Quadrilateral],
    overlap_threshold: float = 0.1,
    use_sfx_filter: bool = False,
    image: Optional[np.ndarray] = None,
) -> List[Quadrilateral]:
    """
    合并主检测器和YOLO检测器的框，智能替换逻辑：
    0. 高优先级结构替换：
       若同方向主框中存在“主轴包裹”成对关系（竖排看X、横排看Y），且某个YOLO框完整覆盖该对主框，
       则直接用该YOLO框替换这对主框。
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
        use_sfx_filter: 过滤既不在气泡内、也未被 YOLO other 框包裹、也未与其他 YOLO 框达到重叠阈值的主检测框。
        image: 原图，供现有气泡检测函数判断未获 YOLO 支持的主框。
    
    Returns:
        合并后的检测框列表
    """
    if len(main_boxes) == 0:
        return yolo_boxes
    
    # 先进行标签感染：每个 YOLO 框最多感染一个主框
    _apply_yolo_label_infection(main_boxes, yolo_boxes, min_overlap_ratio=max(0.01, overlap_threshold * 0.5))
    
    # 标记要移除的主检测器框索引。拟声词过滤只作用于主检测器框，
    # YOLO 自身框仍按下方原有的替换/去重规则处理。
    main_boxes_to_remove = (
        _get_sfx_filtered_main_indices(
            main_boxes,
            yolo_boxes,
            overlap_threshold,
            image=image,
        )
        if use_sfx_filter
        else set()
    )
    # 标记要移除的YOLO框索引
    yolo_boxes_to_remove = set()
    # 要添加的YOLO框（用于替换）
    yolo_boxes_to_add_set = set()  # 使用set避免重复
    # 坐标判定容差（像素）
    axis_eps = 2.0

    # 规则0：高优先级结构替换（先于既有面积倍率规则）
    # 仅当 YOLO 框完整覆盖主框对，且主框对满足“主轴包裹”时触发。
    for yolo_idx, yolo_box in enumerate(yolo_boxes):
        yolo_label = _get_box_label(yolo_box)
        # 与既有逻辑一致：other 不参与几何替换
        if yolo_label == 'other':
            continue

        yolo_aabb = _box_aabb(yolo_box)
        covered_main_indices = []
        for main_idx, main_box in enumerate(main_boxes):
            if _aabb_contains(yolo_aabb, _box_aabb(main_box), eps=axis_eps):
                covered_main_indices.append(main_idx)

        if len(covered_main_indices) < 2:
            continue

        wrapped_pair_indices = set()
        for i in range(len(covered_main_indices)):
            for j in range(i + 1, len(covered_main_indices)):
                idx_i = covered_main_indices[i]
                idx_j = covered_main_indices[j]
                if _is_axis_wrapped_pair(main_boxes[idx_i], main_boxes[idx_j], eps=axis_eps):
                    wrapped_pair_indices.add(idx_i)
                    wrapped_pair_indices.add(idx_j)

        if len(wrapped_pair_indices) >= 2:
            main_boxes_to_remove.update(wrapped_pair_indices)
            yolo_boxes_to_add_set.add(yolo_idx)
    
    for yolo_idx, yolo_box in enumerate(yolo_boxes):
        # 规则0已判定为强替换的 YOLO 框，跳过后续面积/重叠判定
        if yolo_idx in yolo_boxes_to_add_set:
            continue

        yolo_label = _get_box_label(yolo_box)
        # other 仅用于包裹辅助：不参与替换/去除主框的几何决策
        if yolo_label == 'other':
            continue

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
        contained_main_boxes_total_area = 0.0  # 被完全包含的主框总面积
        
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
                
                if contains:
                    # YOLO框完全包含这个主检测器框
                    replaced_main_indices.add(main_idx)
                    contained_main_boxes_total_area += main_area
                else:
                    # 不完全包含，记录与其他主框的重叠率
                    max_overlap_ratio_with_others = max(max_overlap_ratio_with_others, overlap_ratio)
        
        # 检查面积条件：YOLO框面积 >= 所有被包含的主框总面积 × 2
        if len(replaced_main_indices) > 0:
            area_ratio = yolo_area / contained_main_boxes_total_area if contained_main_boxes_total_area > 0 else 0
            if area_ratio >= 2.0:
                can_replace = True
        
        # 决定这个YOLO框的命运
        if len(replaced_main_indices) > 0:
            # YOLO框包含了至少一个主检测器框
            if can_replace:
                # 满足了替换条件（面积 >= 2倍），但还需要检查与其他未包含主框的重叠率
                # 对于满足2倍面积条件的框，允许更高的重叠率（阈值+0.1）
                adjusted_threshold = overlap_threshold + 0.1
                if max_overlap_ratio_with_others >= adjusted_threshold:
                    # 与其他主框重叠率过高，删除这个YOLO框，不进行替换
                    yolo_boxes_to_remove.add(yolo_idx)
                else:
                    # 可以安全替换：删除被替换的主框，添加YOLO框
                    for main_idx in replaced_main_indices:
                        main_boxes_to_remove.add(main_idx)
                    yolo_boxes_to_add_set.add(yolo_idx)
            else:
                # 包含了主框但面积条件不满足（< 2倍），说明YOLO框可能检测错了，删除
                yolo_boxes_to_remove.add(yolo_idx)
        else:
            # YOLO框没有完全包含任何主检测器框，按原有逻辑处理
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
            if not hasattr(yolo_box, 'det_label'):
                yolo_label = _get_box_label(yolo_box)
                if yolo_label:
                    yolo_box.det_label = yolo_label
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
        yolo_label = _get_box_label(yolo_box) or 'unknown'
        # other 仅用于包裹辅助，不在该调试图中绘制
        if yolo_label == 'other':
            continue
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
            label = f"YOLO({yolo_label}):{max_overlap_ratio:.2f}(X)"
        else:
            # 重叠率低于阈值，用蓝色表示（会保留）
            color = (0, 0, 255)  # Blue
            label = f"YOLO({yolo_label}):{max_overlap_ratio:.2f}"
        
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
    detector = detector_cache.pop(detector_key, None)
    if isinstance(detector, OfflineDetector):
        await detector.unload()

    # YOLO OBB 作为辅助检测器使用字符串 key 缓存，主检测器卸载时一并释放。
    yolo_detector = detector_cache.pop('yolo_obb', None)
    if isinstance(yolo_detector, OfflineDetector):
        await yolo_detector.unload()
