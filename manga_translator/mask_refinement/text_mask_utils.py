import logging
import math
from typing import List, Tuple

import cv2
import numpy as np
from shapely.geometry import Polygon
from tqdm import tqdm

# from sklearn.mixture import BayesianGaussianMixture
# from functools import reduce
# from collections import defaultdict
# from scipy.optimize import linear_sum_assignment
from ..utils import (
    Quadrilateral,
    build_det_rearrange_plan,
    det_collect_plan_patches,
    det_rearrange_patch_array,
    det_rearrange_split_view,
    det_unrearrange_patch_maps,
    imwrite_unicode,
)

COLOR_RANGE_SIGMA = 1.5 # how many stddev away is considered the same color

def save_rgb(fn, img, logger=None):
    if logger is None:
        logger = logging.getLogger('manga_translator')
    if len(img.shape) == 3 and img.shape[2] == 3:
        imwrite_unicode(fn, cv2.cvtColor(img, cv2.COLOR_RGB2BGR), logger)
    else:
        imwrite_unicode(fn, img, logger)

def area_overlap(x1, y1, w1, h1, x2, y2, w2, h2):  # returns None if rectangles don't intersect
    x_overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    y_overlap = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    return x_overlap * y_overlap

def dist(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))

def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:             # rectangles intersect
        return 0
    
def extend_rect(x, y, w, h, max_x, max_y, extend_size):
    x1 = max(x - extend_size, 0)
    y1 = max(y - extend_size, 0)
    w1 = min(w + extend_size * 2, max_x - x1 - 1)
    h1 = min(h + extend_size * 2, max_y - y1 - 1)
    return x1, y1, w1, h1

def complete_mask_fill(text_lines: List[Tuple[int, int, int, int]], mask_shape):
    final_mask = np.zeros(mask_shape, dtype=np.uint8)
    for (x, y, w, h) in text_lines:
        final_mask = cv2.rectangle(final_mask, (x, y), (x + w, y + h), (255), -1)
    return final_mask

import pydensecrf.densecrf as dcrf
from pydensecrf.utils import unary_from_softmax

# 兼容不同版本的 pydensecrf
DIAG_KERNEL = getattr(dcrf, 'DIAG_KERNEL', 0)
NO_NORMALIZATION = getattr(dcrf, 'NO_NORMALIZATION', 0)

def refine_mask(rgbimg, rawmask):
    # Optimization: Early exit for empty or trivial masks
    if rawmask is None or rawmask.size == 0:
        return rawmask
    if np.max(rawmask) == 0: # Check if mask is completely black
        return rawmask
        
    # Optimization: Skip expensive CRF for very small regions (e.g. < 100 pixels)
    # The overhead of creating DenseCRF2D outweighs the benefit for tiny spots
    if rawmask.size < 100:
        return rawmask

    if len(rawmask.shape) == 2:
        rawmask = rawmask[:, :, None]
    
    # 复用数组，减少内存分配
    # Optimization: Assuming rawmask is 0 or 255. 
    # Pre-calculating probabilities without division if possible, but modern CPUs handle this fast enough.
    # We stick to standard float conversion but can optimize if needed.
    mask_softmax = np.empty((rawmask.shape[0], rawmask.shape[1], 2), dtype=np.float32)
    # Norm to 0-1
    float_mask = rawmask[:, :, 0].astype(np.float32) / 255.0
    mask_softmax[:, :, 0] = 1.0 - float_mask
    mask_softmax[:, :, 1] = float_mask
    
    n_classes = 2
    feat_first = mask_softmax.transpose((2, 0, 1)).reshape((n_classes, -1))
    unary = unary_from_softmax(feat_first)
    unary = np.ascontiguousarray(unary)

    d = dcrf.DenseCRF2D(rgbimg.shape[1], rgbimg.shape[0], n_classes)

    d.setUnaryEnergy(unary)
    d.addPairwiseGaussian(sxy=1, compat=3, kernel=DIAG_KERNEL,
                            normalization=NO_NORMALIZATION)

    d.addPairwiseBilateral(sxy=23, srgb=7, rgbim=rgbimg,
                        compat=20,
                        kernel=DIAG_KERNEL,
                        normalization=NO_NORMALIZATION)
    
    # Reverted to 5 steps as per user request to maintain quality.
    Q = d.inference(5)
    res = np.argmax(Q, axis=0).reshape((rgbimg.shape[0], rgbimg.shape[1]))
    
    # 直接转换，避免额外复制
    return (res * 255).astype(np.uint8)

MASK_REARRANGE_TARGET_SIZE = 1280

def _copy_quadrilateral_with_pts(src: Quadrilateral, pts: np.ndarray) -> Quadrilateral:
    q = Quadrilateral(pts, src.text, src.prob)
    q.font_size = src.font_size
    return q


def _build_split_view_textlines(textlines: List[Quadrilateral], transpose: bool) -> List[Quadrilateral]:
    split_textlines = []
    for txtln in textlines:
        pts = txtln.pts.copy()
        if transpose:
            pts = pts[:, ::-1]
        split_textlines.append(_copy_quadrilateral_with_pts(txtln, pts))
    return split_textlines


def _assign_textlines_to_rearrange_stripes(
    textlines: List[Quadrilateral],
    plan: dict,
):
    h = int(plan['h'])
    patch_size = int(plan['patch_size'])
    ph_num = int(plan['ph_num'])
    rel_step_list = plan['rel_step_list']

    intervals = []
    for pidx in range(ph_num):
        t = int(round(float(rel_step_list[pidx]) * h))
        b = min(t + patch_size, h)
        intervals.append((t, b, (t + b) * 0.5))

    buckets = [[] for _ in range(ph_num)]
    for txtln in textlines:
        bbox = txtln.aabb
        top = float(bbox.y)
        bottom = float(bbox.y + bbox.h)

        # A center-point owner is insufficient here: a text line can straddle
        # a rearrange seam while its center falls entirely in the next stripe.
        # Prefer one complete view; expose all partial views only when no stripe
        # contains the whole line so the caller can handle it separately.
        intersecting = [idx for idx, (t, b, _) in enumerate(intervals) if bottom > t and top < b]
        fully_contained = [idx for idx, (t, b, _) in enumerate(intervals) if t <= top and bottom < b]
        if fully_contained:
            center_y = (top + bottom) * 0.5
            candidates = [min(fully_contained, key=lambda idx: abs(center_y - intervals[idx][2]))]
        else:
            candidates = intersecting
        if not candidates:
            center_y = (top + bottom) * 0.5
            candidates = [min(
                range(ph_num),
                key=lambda idx: min(abs(center_y - intervals[idx][0]), abs(center_y - intervals[idx][1])),
            )]
        for selected in candidates:
            buckets[selected].append(txtln)
    return buckets, intervals


def _collect_rearrange_patch_textlines(
    patch_idx: int,
    stripe_textline_buckets: List[List[Quadrilateral]],
    stripe_intervals: List[Tuple[int, int, float]],
    plan: dict,
) -> List[Quadrilateral]:
    patch_textlines = []
    pw_num = int(plan['pw_num'])
    ph_num = int(plan['ph_num'])
    short_side_w = int(plan['w'])
    transpose = bool(plan['transpose'])

    for jj in range(pw_num):
        pidx = patch_idx * pw_num + jj
        if pidx >= ph_num:
            break
        t, _b, _mid = stripe_intervals[pidx]
        for txtln in stripe_textline_buckets[pidx]:
            new_pts = txtln.pts.copy()
            if transpose:
                # transpose=True 时 patch 内是按高度拼条带: [x, y] <- [y - t, x + jj * short_side_w]
                local_x = new_pts[:, 1] - t
                local_y = new_pts[:, 0] + jj * short_side_w
                new_pts[:, 0] = local_x
                new_pts[:, 1] = local_y
            else:
                new_pts[:, 0] += jj * short_side_w
                new_pts[:, 1] -= t
            patch_textlines.append(_copy_quadrilateral_with_pts(txtln, new_pts))
    return patch_textlines


def _complete_mask_with_det_rearrange(
    img: np.ndarray,
    mask: np.ndarray,
    textlines: List[Quadrilateral],
    keep_threshold: float,
    dilation_offset: int,
    kernel_size: int,
):
    plan = build_det_rearrange_plan(img, tgt_size=MASK_REARRANGE_TARGET_SIZE)
    if plan is None:
        return None

    image_patch_array = det_rearrange_patch_array(plan)
    mask_plan = dict(plan)
    mask_plan['patch_list'] = det_collect_plan_patches(mask, plan)
    mask_patch_array = det_rearrange_patch_array(mask_plan)

    split_textlines = _build_split_view_textlines(textlines, bool(plan['transpose']))
    stripe_buckets, stripe_intervals = _assign_textlines_to_rearrange_stripes(split_textlines, plan)
    overlaps = [
        stripe_intervals[idx][1] - stripe_intervals[idx + 1][0]
        for idx in range(len(stripe_intervals) - 1)
    ]
    overlap_summary = (
        f"{min(overlaps)}-{max(overlaps)}px" if overlaps else "none"
    )
    logging.getLogger(__name__).info(
        "Mask refinement rearrange plan: stripes=%d, packed_patches=%d, "
        "stripes_per_patch=%d, stripe_size=%d, overlap=%s.",
        int(plan['ph_num']),
        int(plan['p_num']),
        int(plan['pw_num']),
        int(plan['patch_size']),
        overlap_summary,
    )

    # A line whose complete box is not contained in any stripe cannot be
    # reconstructed reliably from a clipped local view: _complete_mask_core
    # removes the line box before matching connected components. Process these
    # rare seam-crossing lines in a small context crop, then merge them with the
    # regular rearranged result.
    split_img = det_rearrange_split_view(img, bool(plan['transpose']))
    split_mask = det_rearrange_split_view(mask, bool(plan['transpose']))
    boundary_textlines = []
    for txtln in split_textlines:
        bbox = txtln.aabb
        top = float(bbox.y)
        bottom = float(bbox.y + bbox.h)
        fully_contained = any(
            t <= top and bottom < b
            for t, b, _ in stripe_intervals
        )
        if not fully_contained:
            boundary_textlines.append(txtln)

    boundary_line_ids = {id(txtln) for txtln in boundary_textlines}
    boundary_results = []
    if boundary_textlines:
        logging.getLogger(__name__).info(
            "Mask refinement handles %d seam-crossing textlines with local context crops.",
            len(boundary_textlines),
        )
    for txtln in boundary_textlines:
        bbox = txtln.aabb
        margin = max(int(math.ceil(txtln.font_size)), 16)
        x0 = max(int(math.floor(bbox.x)) - margin, 0)
        y0 = max(int(math.floor(bbox.y)) - margin, 0)
        x1 = min(int(math.ceil(bbox.x + bbox.w)) + margin, split_mask.shape[1])
        y1 = min(int(math.ceil(bbox.y + bbox.h)) + margin, split_mask.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue

        local_pts = txtln.pts.copy()
        local_pts[:, 0] -= x0
        local_pts[:, 1] -= y0
        local_txtln = _copy_quadrilateral_with_pts(txtln, local_pts)
        local_result = _complete_mask_core(
            np.ascontiguousarray(split_img[y0:y1, x0:x1]),
            split_mask[y0:y1, x0:x1].copy(),
            [local_txtln],
            keep_threshold,
            dilation_offset,
            kernel_size,
            show_progress=False,
        )
        if local_result is not None:
            boundary_results.append((x0, y0, x1, y1, local_result))

    if boundary_line_ids:
        for bucket_idx, bucket in enumerate(stripe_buckets):
            stripe_buckets[bucket_idx] = [
                txtln for txtln in bucket if id(txtln) not in boundary_line_ids
            ]

    patch_mask_results = []
    for patch_idx, (img_patch, mask_patch) in enumerate(zip(image_patch_array, mask_patch_array)):
        patch_textlines = _collect_rearrange_patch_textlines(
            patch_idx,
            stripe_buckets,
            stripe_intervals,
            plan,
        )
        if not patch_textlines:
            patch_mask_results.append(np.zeros(mask_patch.shape, dtype=np.float32))
            continue

        patch_result = _complete_mask_core(
            img_patch,
            mask_patch.copy(),
            patch_textlines,
            keep_threshold,
            dilation_offset,
            kernel_size,
        )
        if patch_result is None:
            patch_result = np.zeros(mask_patch.shape, dtype=np.float32)
        patch_mask_results.append(patch_result.astype(np.float32))

    merged_mask = det_unrearrange_patch_maps(patch_mask_results, plan, data_format='hw')
    for x0, y0, x1, y1, local_result in boundary_results:
        if plan['transpose']:
            target = merged_mask[x0:x1, y0:y1]
            local_result = local_result.T
        else:
            target = merged_mask[y0:y1, x0:x1]
        np.maximum(
            target,
            local_result.astype(merged_mask.dtype, copy=False),
            out=target,
        )
    merged_mask = np.clip(merged_mask, 0, 255).astype(np.uint8)
    if np.max(merged_mask) == 0:
        return None
    return merged_mask


def _complete_mask_core(
    img: np.ndarray,
    mask: np.ndarray,
    textlines: List[Quadrilateral],
    keep_threshold=1e-2,
    dilation_offset=0,
    kernel_size=3,
    show_progress=True,
):
    """complete_mask 的核心实现，处理单个区块"""
    bboxes = [txtln.aabb.xywh for txtln in textlines]
    polys = [Polygon(txtln.pts) for txtln in textlines]
    for (x, y, w, h) in bboxes:
        cv2.rectangle(mask, (x, y), (x + w, y + h), (0), 1)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)

    M = len(textlines)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.debug("--- MASK_REFINEMENT_DEBUG: Entering _complete_mask_core ---")
    logger.debug(f"--- MASK_REFINEMENT_DEBUG: Number of textlines (M) = {M} ---")
    logger.debug(f"--- MASK_REFINEMENT_DEBUG: Number of connected components (num_labels) = {num_labels} ---")

    textline_ccs = [np.zeros_like(mask) for _ in range(M)]
    iinfo = np.iinfo(labels.dtype)
    textline_rects = np.full(shape = (M, 4), fill_value = [iinfo.max, iinfo.max, iinfo.min, iinfo.min], dtype = labels.dtype)
    ratio_mat = np.zeros(shape = (num_labels, M), dtype = np.float32)
    dist_mat = np.zeros(shape = (num_labels, M), dtype = np.float32)
    valid = False
    for label in range(1, num_labels):
        if stats[label, cv2.CC_STAT_AREA] <= 9:
            continue

        x1 = stats[label, cv2.CC_STAT_LEFT]
        y1 = stats[label, cv2.CC_STAT_TOP]
        w1 = stats[label, cv2.CC_STAT_WIDTH]
        h1 = stats[label, cv2.CC_STAT_HEIGHT]
        area1 = stats[label, cv2.CC_STAT_AREA]
        cc_pts = np.array([[x1, y1], [x1 + w1, y1], [x1 + w1, y1 + h1], [x1, y1 + h1]])
        cc_poly = Polygon(cc_pts)

        for tl_idx in range(M):
            area2 = polys[tl_idx].area
            try:
                overlapping_area = polys[tl_idx].intersection(cc_poly).area
            except Exception as _e:
                try:
                    fixed_poly = polys[tl_idx].buffer(0)
                    fixed_cc_poly = cc_poly.buffer(0)
                    overlapping_area = fixed_poly.intersection(fixed_cc_poly).area
                except Exception:
                    overlapping_area = 0
            
            ratio_mat[label, tl_idx] = overlapping_area / min(area1, area2)
            
            try:
                dist_mat[label, tl_idx] = polys[tl_idx].distance(cc_poly.centroid)
            except Exception:
                dist_mat[label, tl_idx] = float('inf')

        avg = np.argmax(ratio_mat[label])
        max_overlap = ratio_mat[label, avg]

        if max_overlap < 0.1:
            continue
            
        area2 = polys[avg].area
        if area1 >= area2:
            continue
        if ratio_mat[label, avg] <= keep_threshold:
            avg = np.argmin(dist_mat[label])
            area2 = polys[avg].area
            unit = max(min([textlines[avg].font_size, w1, h1]), 10)
            if dist_mat[label, avg] >= 0.5 * unit:
                continue

        textline_ccs[avg][y1:y1+h1, x1:x1+w1][labels[y1:y1+h1, x1:x1+w1] == label] = 255
        textline_rects[avg, 0] = min(textline_rects[avg, 0], x1)
        textline_rects[avg, 1] = min(textline_rects[avg, 1], y1)
        textline_rects[avg, 2] = max(textline_rects[avg, 2], x1 + w1)
        textline_rects[avg, 3] = max(textline_rects[avg, 3], y1 + h1)
        valid = True

    if not valid:
        return None
    
    textline_rects[:, 2] -= textline_rects[:, 0]
    textline_rects[:, 3] -= textline_rects[:, 1]
    
    final_mask = np.zeros_like(mask)
    img = cv2.bilateralFilter(img, 17, 80, 80)
    for i, cc in enumerate(tqdm(textline_ccs, '[mask]', disable=not show_progress)):
        x1, y1, w1, h1 = textline_rects[i]
        text_size = min(w1, h1, textlines[i].font_size)
        x1, y1, w1, h1 = extend_rect(x1, y1, w1, h1, img.shape[1], img.shape[0], int(text_size * 0.1))
        dilate_size = max((int((text_size + dilation_offset) * 0.3) // 2) * 2 + 1, 3)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        cc_region = np.ascontiguousarray(cc[y1: y1 + h1, x1: x1 + w1])
        if cc_region.size == 0:
            continue
        img_region = np.ascontiguousarray(img[y1: y1 + h1, x1: x1 + w1])
        cc_region = refine_mask(img_region, cc_region)
        cc[y1: y1 + h1, x1: x1 + w1] = cc_region
        x2, y2, w2, h2 = extend_rect(x1, y1, w1, h1, img.shape[1], img.shape[0], -(-dilate_size // 2))
        cc[y2:y2+h2, x2:x2+w2] = cv2.dilate(cc[y2:y2+h2, x2:x2+w2], kern)
        final_mask[y2:y2+h2, x2:x2+w2] = cv2.bitwise_or(final_mask[y2:y2+h2, x2:x2+w2], cc[y2:y2+h2, x2:x2+w2])
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.dilate(final_mask, kern)


def complete_mask(img: np.ndarray, mask: np.ndarray, textlines: List[Quadrilateral], keep_threshold = 1e-2, dilation_offset = 0, kernel_size=3):
    """
    完成 mask 精修。是否切割仅由 detector rearrange 条件决定。
    """
    import logging
    logger = logging.getLogger(__name__)

    # 仅使用与检测器统一的重排切割/回拼逻辑；是否切割由 build_det_rearrange_plan 条件决定。
    rearranged_mask = _complete_mask_with_det_rearrange(
        img,
        mask,
        textlines,
        keep_threshold,
        dilation_offset,
        kernel_size,
    )
    if rearranged_mask is not None:
        logger.info(
            f"Mask refinement chunking uses detector rearrange pipeline "
            f"({mask.shape[0]}x{mask.shape[1]}, {len(textlines)} textlines)."
        )
        return rearranged_mask

    # 不满足重排条件时，走常规路径
    return _complete_mask_core(img, mask, textlines, keep_threshold, dilation_offset, kernel_size)


def unsharp(image):
    gaussian_3 = cv2.GaussianBlur(image, (3, 3), 2.0)
    return cv2.addWeighted(image, 1.5, gaussian_3, -0.5, 0, image)
