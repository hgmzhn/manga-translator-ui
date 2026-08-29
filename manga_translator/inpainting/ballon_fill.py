"""纯色气泡填色与逐块修复辅助。"""
from typing import List, Tuple

import cv2
import numpy as np

from ..rendering.ballon_extractor import enlarge_window
from ..utils.bubble import calc_bbox_mask_overlap_ratio


MODEL_BUBBLE_SHRINK_RATIO = 0.02


def solid_fill_pure_bubbles(
    img: np.ndarray,
    mask: np.ndarray,
    text_regions: List,
    mask_tight: np.ndarray,
    bubble_mask: np.ndarray,
    overlap_threshold: float,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """
    对匹配的纯色气泡，只在修复蒙版与气泡蒙版的交集内直接用背景中位色填充，避免覆盖修复蒙版之外的气泡内容。

    Args:
        img: RGB 或 RGBA 工作图
        mask: 精修（膨胀）后的修复掩码，与 img 同高宽；填色区域会从中清零
        text_regions: 文本区域列表，用现有模型气泡重叠逻辑选择对应气泡
        mask_tight: 膨胀后的原始文字蒙版，仅用于从气泡中扣除文字像素、采样背景色
        bubble_mask: 已按比例内缩的气泡模型输出蒙版，用于识别匹配的气泡连通块
        overlap_threshold: 文本框位于模型气泡内的最小重叠率

    Returns:
        (filled_img, remaining_mask, filled_region_count)，不修改输入。
    """
    filled_img = img.copy()
    remaining_mask = mask.copy()
    rgb = filled_img[:, :, :3] if filled_img.ndim == 3 and filled_img.shape[2] == 4 else filled_img
    tight_bin = np.where(mask_tight >= 127, 255, 0).astype(np.uint8)
    bubble_bin = np.where(bubble_mask > 0, 255, 0).astype(np.uint8)
    if not np.any(bubble_bin):
        return filled_img, remaining_mask, 0

    num_labels, label_map = cv2.connectedComponents(
        np.where(bubble_bin > 0, 1, 0).astype(np.uint8),
        connectivity=8,
    )
    overlap_threshold = max(0.0, min(float(overlap_threshold), 1.0))

    region_bboxes = []
    for region in text_regions:
        try:
            x1, y1, x2, y2 = [int(round(float(v))) for v in region.xyxy]
        except Exception:
            continue
        if x2 > x1 and y2 > y1:
            region_bboxes.append((x1, y1, x2 - x1, y2 - y1))

    filled_regions = set()
    for label_idx in range(1, num_labels):
        region_bubble = np.where(label_map == label_idx, 255, 0).astype(np.uint8)
        matched_regions = {
            idx for idx, bbox in enumerate(region_bboxes)
            if calc_bbox_mask_overlap_ratio(bbox, region_bubble) >= overlap_threshold
        }
        if not matched_regions:
            continue

        non_text_mask = cv2.bitwise_and(region_bubble, 255 - tight_bin)
        non_text_px = rgb[non_text_mask > 0]
        if not non_text_px.size:
            continue
        average_bg_color = np.median(non_text_px, axis=0)
        std_rgb = np.std(non_text_px - average_bg_color, axis=0)
        inpaint_thresh = 7 if np.std(std_rgb) > 1 else 10
        if np.max(std_rgb) >= inpaint_thresh:
            continue

        # 气泡蒙版只负责识别候选气泡；实际填色严格限制在修复蒙版内。
        fill_region = (region_bubble > 0) & (mask > 0)
        if not np.any(fill_region):
            continue
        rgb[fill_region] = np.clip(np.round(average_bg_color), 0, 255).astype(np.uint8)
        remaining_mask[fill_region] = 0
        filled_regions.update(matched_regions)

    return filled_img, remaining_mask, len(filled_regions)


async def inpaint_regions_per_block(img: np.ndarray, remaining_mask: np.ndarray,
                                    inpaint_fn) -> Tuple[np.ndarray, int]:
    """
    逐块修复：将填色后优化蒙版的每个孤立连通块，
    按自身外接框裁 2 倍窗口单独修复再贴回。

    与整页修复的差异：
    - 逐块小窗口内掩码占比大，LaMa 修复质量远好于整页长条掩码（整页会留文字鬼影）
    - 每次只传入当前连通块的优化蒙版，不受文本行框和邻近蒙版影响
    - 图与掩码一起反射补成正方形：给模型足够上下文；掩码同步反射，
      否则镜像出来的文字没有掩码，模型会照着镜像把文字原样画回来
    - 补成正方形后调用普通修复入口，长宽比为 1，不会进入长图切片流程

    Args:
        inpaint_fn: async (crop, mask) -> inpainted crop
    Returns:
        (result_img, inpainted_block_count)。img 不被修改；
        remaining_mask 会被原地清零已修复连通块。
    """
    result = img.copy()
    im_h, im_w = result.shape[:2]
    mask_bin = np.where(remaining_mask > 0, 255, 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_bin, connectivity=8)
    count = 0
    for label_idx in range(1, num_labels):
        x1, y1, w, h, area = map(int, stats[label_idx])
        if area <= 0:
            continue
        x2, y2 = x1 + w, y1 + h
        ex1, ey1, ex2, ey2 = enlarge_window([x1, y1, x2, y2], im_w, im_h, ratio=2.0)
        if ex2 <= ex1 or ey2 <= ey1:
            continue
        msk = np.where(labels[ey1:ey2, ex1:ex2] == label_idx, 255, 0).astype(np.uint8)
        crop = result[ey1:ey2, ex1:ex2].copy()
        ch, cw = crop.shape[:2]
        longer = max(ch, cw)
        pad_bottom, pad_right = longer - ch, longer - cw
        crop_sq = cv2.copyMakeBorder(crop, 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT)
        msk_sq = cv2.copyMakeBorder(np.ascontiguousarray(msk), 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT)
        out = await inpaint_fn(crop_sq, msk_sq)
        result[ey1:ey2, ex1:ex2] = out[:ch, :cw]
        remaining_view = remaining_mask[y1:y2, x1:x2]
        remaining_view[labels[y1:y2, x1:x2] == label_idx] = 0
        count += 1
    return result, count
