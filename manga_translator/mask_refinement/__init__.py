from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from ..utils import (
    Quadrilateral,
    TextBlock,
    build_bubble_mask_from_mangalens_result,
    get_cached_bubbles_with_mangalens,
    imwrite_unicode,
)
from ..utils.log import get_logger
from .text_mask_utils import complete_mask, complete_mask_fill

logger = get_logger('mask_refinement')

# “扩大气泡修复范围”保持原有整图短边比例；“膨胀不超过气泡蒙版”按气泡短边比例
BUBBLE_MASK_ERODE_RATIO = 0.02
BUBBLE_MASK_DILATION_LIMIT_ERODE_RATIO = 0.01
# line 最小外接矩形保护区外扩像素；0 表示只保护原始外接矩形内
LINE_MIN_RECT_PROTECT_EXPAND_PX = 0


def _build_line_protect_mask(
    text_regions: List[TextBlock],
    image_shape: Tuple[int, int],
    expand_px: int = LINE_MIN_RECT_PROTECT_EXPAND_PX,
) -> np.ndarray:
    h, w = int(image_shape[0]), int(image_shape[1])
    protect_mask = np.zeros((h, w), dtype=np.uint8)
    if h <= 0 or w <= 0 or expand_px < 0:
        return protect_mask

    for region in text_regions:
        lines = getattr(region, 'lines', None)
        if lines is None:
            continue
        try:
            lines_arr = np.asarray(lines, dtype=np.float32)
        except Exception:
            continue

        if lines_arr.ndim == 2:
            iter_lines = [lines_arr]
        elif lines_arr.ndim == 3:
            iter_lines = lines_arr
        else:
            continue

        for line in iter_lines:
            pts = np.asarray(line, dtype=np.float32).reshape(-1, 2)
            if pts.shape[0] < 3:
                continue
            (cx, cy), (rw, rh), angle = cv2.minAreaRect(pts)
            rw = max(float(rw) + float(expand_px) * 2.0, 1.0)
            rh = max(float(rh) + float(expand_px) * 2.0, 1.0)
            expanded_rect = ((float(cx), float(cy)), (rw, rh), float(angle))
            box = cv2.boxPoints(expanded_rect)
            box[:, 0] = np.clip(box[:, 0], 0, max(w - 1, 0))
            box[:, 1] = np.clip(box[:, 1], 0, max(h - 1, 0))
            cv2.fillPoly(protect_mask, [box.astype(np.int32)], 255)

    return protect_mask


def _keep_bubble_components_intersecting_refined_mask(
    bubble_mask: np.ndarray,
    refined_mask: np.ndarray,
) -> Tuple[np.ndarray, int, int]:
    bubble_bin = np.where(bubble_mask > 0, 255, 0).astype(np.uint8)
    refined_bin = np.where(refined_mask > 0, 255, 0).astype(np.uint8)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bubble_bin, connectivity=8)
    kept_mask = np.zeros_like(bubble_bin)

    total_components = max(num_labels - 1, 0)
    kept_components = 0

    for label_idx in range(1, num_labels):
        x, y, w, h, area = stats[label_idx]
        if area <= 0:
            continue

        label_view = labels[y:y + h, x:x + w]
        region = label_view == label_idx
        if np.any(refined_bin[y:y + h, x:x + w][region] > 0):
            dst = kept_mask[y:y + h, x:x + w]
            dst[region] = 255
            kept_mask[y:y + h, x:x + w] = dst
            kept_components += 1

    return kept_mask, total_components, kept_components


def _clip_refined_components_by_bubble_mask(
    refined_mask: np.ndarray,
    bubble_mask: np.ndarray,
) -> Tuple[np.ndarray, int, int, int]:
    """
    Clip refined-mask connected components by bubble mask:
    - If a refined component intersects bubble mask: keep only the intersection part.
    - If a refined component has no intersection: keep the whole component.
    """
    refined_bin = np.where(refined_mask > 0, 255, 0).astype(np.uint8)
    bubble_bin = np.where(bubble_mask > 0, 255, 0).astype(np.uint8)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(refined_bin, connectivity=8)
    clipped_mask = np.zeros_like(refined_bin)

    total_components = max(num_labels - 1, 0)
    intersected_components = 0
    preserved_components = 0

    for label_idx in range(1, num_labels):
        x, y, w, h, area = stats[label_idx]
        if area <= 0:
            continue

        label_view = labels[y:y + h, x:x + w]
        region = label_view == label_idx
        bubble_region = bubble_bin[y:y + h, x:x + w] > 0
        intersection = region & bubble_region

        dst = clipped_mask[y:y + h, x:x + w]
        if np.any(intersection):
            dst[intersection] = 255
            intersected_components += 1
        else:
            dst[region] = 255
            preserved_components += 1
        clipped_mask[y:y + h, x:x + w] = dst

    return clipped_mask, total_components, intersected_components, preserved_components


def _build_bubble_clip_debug_image(
    raw_image: np.ndarray,
    bubble_mask: np.ndarray,
    mask_before_clip: np.ndarray,
    mask_after_clip: np.ndarray,
    protected_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    h, w = bubble_mask.shape[:2]

    if raw_image.ndim == 3 and raw_image.shape[2] == 3:
        image_bgr = cv2.cvtColor(raw_image, cv2.COLOR_RGB2BGR)
    elif raw_image.ndim == 2:
        image_bgr = cv2.cvtColor(raw_image, cv2.COLOR_GRAY2BGR)
    else:
        image_bgr = np.zeros((h, w, 3), dtype=np.uint8)

    bubble_bin = np.where(bubble_mask > 0, 255, 0).astype(np.uint8)
    before_bin = np.where(mask_before_clip > 0, 255, 0).astype(np.uint8)
    after_bin = np.where(mask_after_clip > 0, 255, 0).astype(np.uint8)
    final_bin = np.where(after_bin > 0, 255, 0).astype(np.uint8)
    removed_bin = np.where((before_bin > 0) & (after_bin == 0), 255, 0).astype(np.uint8)
    protected_bin = (
        np.where(protected_mask > 0, 255, 0).astype(np.uint8)
        if protected_mask is not None
        else np.zeros_like(final_bin)
    )

    overlay = image_bgr.copy()
    # Blue: bubble mask
    overlay[bubble_bin > 0] = (
        overlay[bubble_bin > 0] * 0.55 + np.array([255, 0, 0], dtype=np.float32) * 0.45
    ).astype(np.uint8)
    # Green: final kept mask
    overlay[final_bin > 0] = (
        overlay[final_bin > 0] * 0.45 + np.array([0, 255, 0], dtype=np.float32) * 0.55
    ).astype(np.uint8)
    # Yellow: protected (rescued from being removed)
    overlay[protected_bin > 0] = np.array([0, 255, 255], dtype=np.uint8)
    # Red: removed by clipping (highest priority)
    overlay[removed_bin > 0] = np.array([0, 0, 255], dtype=np.uint8)

    legend = "Blue=bubble, Green=final, Yellow=protected, Red=removed"
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(overlay, legend, (8, 22), font, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(overlay, legend, (8, 22), font, 0.58, (0, 0, 0), 1, cv2.LINE_AA)

    return overlay

async def dispatch(
    text_regions: List[TextBlock],
    raw_image: np.ndarray,
    raw_mask: np.ndarray,
    method: str = 'fit_text',
    dilation_offset: int = 0,
    verbose: bool = False,
    kernel_size: int = 3,
    use_model_bubble_repair_intersection: bool = False,
    limit_mask_dilation_to_bubble_mask: bool = False,
    debug_path_fn: Optional[Callable[[str], str]] = None,
) -> np.ndarray:
    # Larger sized mask images will probably have crisper and thinner mask segments due to being able to fit the text pixels better
    # so we dont want to size them down as much to not lose information
    scale_factor = max(min((raw_mask.shape[0] - raw_image.shape[0] / 3) / raw_mask.shape[0], 1), 0.5)

    img_resized = cv2.resize(raw_image, (int(raw_image.shape[1] * scale_factor), int(raw_image.shape[0] * scale_factor)), interpolation = cv2.INTER_LINEAR)
    mask_resized = cv2.resize(raw_mask, (int(raw_image.shape[1] * scale_factor), int(raw_image.shape[0] * scale_factor)), interpolation = cv2.INTER_LINEAR)

    mask_resized[mask_resized > 0] = 255
    textlines = []
    for region in text_regions:
        for l in region.lines:
            q = Quadrilateral(l * scale_factor, '', 0)
            textlines.append(q)

    final_mask = complete_mask(img_resized, mask_resized, textlines, dilation_offset=dilation_offset,kernel_size=kernel_size) if method == 'fit_text' else complete_mask_fill([txtln.aabb.xywh for txtln in textlines], mask_resized.shape[:2])
    if final_mask is None:
        final_mask = np.zeros((raw_image.shape[0], raw_image.shape[1]), dtype = np.uint8)
    else:
        final_mask = cv2.resize(final_mask, (raw_image.shape[1], raw_image.shape[0]), interpolation = cv2.INTER_LINEAR)
        final_mask[final_mask > 0] = 255

    if use_model_bubble_repair_intersection or limit_mask_dilation_to_bubble_mask:
        try:
            result = get_cached_bubbles_with_mangalens(raw_image, return_annotated=False, verbose=False)
            if result is None:
                logger.warning("Model bubble mask cache miss in mask refinement; skip bubble-constrained post-process")
                detections = []
                bubble_source = 'none'
            else:
                detections = result.detections
                raw_result = getattr(result, 'raw_result', None)
                bubble_source = 'mask' if getattr(raw_result, 'masks', None) is not None else 'box'

            if use_model_bubble_repair_intersection:
                bubble_mask = build_bubble_mask_from_mangalens_result(
                    result,
                    final_mask.shape[:2],
                    erode_ratio=BUBBLE_MASK_ERODE_RATIO,
                    erode_per_component=False,
                )
                if np.count_nonzero(bubble_mask) == 0:
                    logger.info(
                        "Bubble repair intersection enabled, but no bubble detections found; keep refined mask unchanged"
                    )
                else:
                    filtered_mask, total_components, kept_components = _keep_bubble_components_intersecting_refined_mask(
                        bubble_mask=bubble_mask,
                        refined_mask=final_mask,
                    )
                    merged_mask = cv2.bitwise_or(final_mask, filtered_mask)
                    added_pixels = int(np.count_nonzero((filtered_mask > 0) & (final_mask == 0)))
                    logger.info(
                        f"Bubble repair intersection: detections={len(detections)}, source={bubble_source}, "
                        f"bubble_components={total_components}, kept_components={kept_components}, "
                        f"refined_pixels={int(np.count_nonzero(final_mask))}, "
                        f"bubble_pixels={int(np.count_nonzero(filtered_mask))}, "
                        f"added_pixels={added_pixels}, output_pixels={int(np.count_nonzero(merged_mask))}"
                    )
                    final_mask = merged_mask

            if limit_mask_dilation_to_bubble_mask:
                bubble_mask = build_bubble_mask_from_mangalens_result(
                    result, final_mask.shape[:2],
                    erode_ratio=BUBBLE_MASK_DILATION_LIMIT_ERODE_RATIO)
                if np.count_nonzero(bubble_mask) == 0:
                    logger.info(
                        "Bubble constrained dilation enabled, but no bubble detections found; keep refined mask unchanged"
                    )
                    return final_mask

                mask_before_clip = final_mask.copy()
                clipped_mask, total_components, intersected_components, preserved_components = _clip_refined_components_by_bubble_mask(
                    refined_mask=final_mask,
                    bubble_mask=bubble_mask,
                )
                # 仅保护，不扩张：只回填“裁剪前已有且落在保护区且被裁掉”的像素
                line_protect_mask = _build_line_protect_mask(
                    text_regions=text_regions,
                    image_shape=final_mask.shape[:2],
                    expand_px=LINE_MIN_RECT_PROTECT_EXPAND_PX,
                )
                protected_restore_mask = (mask_before_clip > 0) & (line_protect_mask > 0) & (clipped_mask == 0)
                protected_pixels = int(np.count_nonzero(protected_restore_mask))
                if protected_pixels > 0:
                    clipped_mask[protected_restore_mask] = 255
                removed_pixels = int(np.count_nonzero((final_mask > 0) & (clipped_mask == 0)))
                logger.info(
                    f"Bubble constrained dilation: detections={len(detections)}, source={bubble_source}, "
                    f"refined_components={total_components}, intersected_components={intersected_components}, "
                    f"preserved_components={preserved_components}, removed_pixels={removed_pixels}, "
                    f"protected_pixels={protected_pixels}, "
                    f"output_pixels={int(np.count_nonzero(clipped_mask))}"
                )
                final_mask = clipped_mask

                if verbose and debug_path_fn is not None:
                    try:
                        debug_img = _build_bubble_clip_debug_image(
                            raw_image=raw_image,
                            bubble_mask=bubble_mask,
                            mask_before_clip=mask_before_clip,
                            mask_after_clip=clipped_mask,
                            protected_mask=protected_restore_mask.astype(np.uint8) * 255,
                        )
                        debug_path = debug_path_fn('mask_bubble_clip_debug.png')
                        imwrite_unicode(debug_path, debug_img, logger)
                        logger.info(f"Bubble constrained dilation debug image saved: {debug_path}")
                    except Exception as debug_exc:
                        logger.warning(f"Failed to save bubble constrained dilation debug image: {debug_exc}")
        except Exception as exc:
            logger.warning(f"Model bubble mask cache read failed, keep refined mask unchanged: {exc}")

    return final_mask
