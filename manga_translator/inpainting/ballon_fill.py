"""
纯色气泡直接填色（移植自 BallonsTranslator 的 check_need_inpaint 机制）。

修复前逐文本区域检测其所在气泡：若气泡内非文字背景接近纯色，
则跳过修复模型，直接用背景中位色填充气泡内部，并从修复掩码中移除该区域。

注意：气泡检测/背景采样必须用贴合笔画的瘦掩码（ctx.mask_raw，对应 BT 自身的文字掩码）；
本仓库精修后的修复掩码膨胀幅度大（mask_dilation_offset 默认 20），常盖过气泡边线，
若直接用它检测会把气泡边缘的 Canny 边缘一并擦掉，导致永远找不到气泡轮廓。
"""
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..rendering.ballon_extractor import enlarge_window


def extract_ballon_mask(img: np.ndarray, mask: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    '''
    Given original img and text mask (cropped)
    return ballon mask & non text mask
    '''
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)

    img = cv2.GaussianBlur(img, (3, 3), cv2.BORDER_DEFAULT)
    h, w = img.shape[:2]
    text_sum = np.sum(mask)
    cannyed = cv2.Canny(img, 70, 140, L2gradient=True, apertureSize=3)
    e_size = 1
    element = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * e_size + 1, 2 * e_size + 1), (e_size, e_size))
    cannyed = cv2.dilate(cannyed, element, iterations=1)
    br = cv2.boundingRect(cv2.findNonZero(mask))
    br_xyxy = [br[0], br[1], br[0] + br[2], br[1] + br[3]]

    # draw the bounding rect in case there is no closed ballon
    cv2.rectangle(cannyed, (0, 0), (w - 1, h - 1), (255, 255, 255), 1, cv2.LINE_8)
    cannyed = cv2.bitwise_and(cannyed, 255 - mask)

    cons, _ = cv2.findContours(cannyed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    min_ballon_area = w * h
    ballon_mask = None
    non_text_mask = None
    # minimum contour which covers all text mask must be the ballon
    for ii, con in enumerate(cons):
        br_c = cv2.boundingRect(con)
        br_c = [br_c[0], br_c[1], br_c[0] + br_c[2], br_c[1] + br_c[3]]
        if br_c[0] > br_xyxy[0] or br_c[1] > br_xyxy[1] or br_c[2] < br_xyxy[2] or br_c[3] < br_xyxy[3]:
            continue
        tmp = np.zeros_like(cannyed)
        cv2.drawContours(tmp, cons, ii, (255, 255, 255), -1, cv2.LINE_8)
        if cv2.bitwise_and(tmp, mask).sum() >= text_sum:
            con_area = cv2.contourArea(con)
            if con_area < min_ballon_area:
                min_ballon_area = con_area
                ballon_mask = tmp
    if ballon_mask is not None:
        non_text_mask = cv2.bitwise_and(ballon_mask, 255 - mask)

    return ballon_mask, non_text_mask


def solid_fill_pure_bubbles(img: np.ndarray, mask: np.ndarray, text_regions: List,
                            mask_tight: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray, int]:
    """
    对纯色气泡跳过修复模型，直接用气泡内背景中位色填充。

    Args:
        img: RGB 或 RGBA 工作图
        mask: 精修（膨胀）后的修复掩码，与 img 同宽高；已填色区域会从中清零
        text_regions: 带 xyxy 属性的文本区域列表
        mask_tight: 贴合笔画的瘦掩码（如检测原始掩码），用于气泡检测与背景采样；
                    缺省时退回用 mask（掩码膨胀大时会显著降低命中率）

    Returns:
        (filled_img, remaining_mask, filled_count)，不修改输入。
    """
    if img is None or mask is None or not text_regions:
        return img, mask, 0

    filled_img = img.copy()
    remaining_mask = mask.copy()
    if mask_tight is None:
        mask_tight = mask
    # RGBA 时只对 RGB 通道填色，alpha 保持不变（切片是视图，原地修改会写回 filled_img）
    rgb = filled_img[:, :, :3] if filled_img.ndim == 3 and filled_img.shape[2] == 4 else filled_img

    # 填色成功后把气泡外扩一圈内的掩码一并清掉：膨胀掩码越过气泡边线的环若残留给模型，
    # 模型会在气泡边线上重绘造成糊边。只清局部一圈，避免误伤相邻气泡的掩码。
    clear_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))

    im_h, im_w = rgb.shape[:2]
    filled_count = 0
    for region in text_regions:
        try:
            x1, y1, x2, y2 = [int(round(float(v))) for v in region.xyxy]
        except Exception:
            continue
        x1, x2 = max(x1, 0), min(x2, im_w)
        y1, y2 = max(y1, 0), min(y2, im_h)
        if x2 <= x1 or y2 <= y1:
            continue
        ex1, ey1, ex2, ey2 = enlarge_window([x1, y1, x2, y2], im_w, im_h, ratio=1.7)
        if ex2 <= ex1 or ey2 <= ey1:
            continue

        im = rgb[ey1:ey2, ex1:ex2]
        msk = remaining_mask[ey1:ey2, ex1:ex2]
        tight_bin = np.where(mask_tight[ey1:ey2, ex1:ex2] >= 127, 255, 0).astype(np.uint8)
        if not tight_bin.any():
            continue

        ballon_msk, non_text_msk = extract_ballon_mask(im, tight_bin)
        if ballon_msk is None:
            continue
        non_text_px = im[np.where(non_text_msk > 0)]
        if non_text_px.shape[0] == 0:
            continue
        average_bg_color = np.median(non_text_px, axis=0)
        std_rgb = np.std(non_text_px - average_bg_color, axis=0)
        std_max = np.max(std_rgb)
        inpaint_thresh = 7 if np.std(std_rgb) > 1 else 10
        if std_max >= inpaint_thresh:
            continue

        fill_region = np.where(ballon_msk > 0)
        im[fill_region] = np.clip(np.round(average_bg_color), 0, 255).astype(np.uint8)
        msk[cv2.dilate(ballon_msk, clear_kernel) > 0] = 0
        filled_count += 1

    return filled_img, remaining_mask, filled_count


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
