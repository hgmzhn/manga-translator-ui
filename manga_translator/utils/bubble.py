import cv2
import numpy as np


def calc_bbox_mask_overlap_ratio(
    text_bbox: tuple[int, int, int, int],
    bubble_mask: np.ndarray,
) -> float:
    """Return the fraction of a text bounding box covered by a bubble mask."""
    x, y, box_w, box_h = map(int, text_bbox)
    x1, y1 = max(x, 0), max(y, 0)
    x2 = min(x + box_w, bubble_mask.shape[1])
    y2 = min(y + box_h, bubble_mask.shape[0])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return np.count_nonzero(bubble_mask[y1:y2, x1:x2]) / max(float(box_w * box_h), 1.0)


def build_region_reference_mask(
    region,
    bubble_mask: np.ndarray,
    label_map: np.ndarray,
) -> np.ndarray:
    """Return complete bubble components intersecting a text region."""
    h, w = bubble_mask.shape[:2]
    region_mask = np.zeros((h, w), dtype=np.uint8)
    polygons = np.asarray(getattr(region, 'lines', []))
    if not polygons.size:
        polygons = np.asarray(region.min_rect)
    try:
        polygons = polygons.astype(np.int32).reshape(-1, 4, 2)
    except (TypeError, ValueError):
        return region_mask
    polygons[..., 0] = np.clip(polygons[..., 0], 0, max(w - 1, 0))
    polygons[..., 1] = np.clip(polygons[..., 1], 0, max(h - 1, 0))
    cv2.fillPoly(region_mask, list(polygons), 255)

    labels = np.unique(label_map[(region_mask > 0) & (bubble_mask > 0)])
    labels = labels[labels > 0]
    return np.where(np.isin(label_map, labels), 255, 0).astype(np.uint8)


def check_color(image):
    """
    Determine whether there are colors in non-black, gray, white, and other gray areas in an RGB color image.
    params：
    image -- np.array
    return：
    True -- Colors with non black, gray, white, and other grayscale areas
    False -- Images are all grayscale areas
    """
    # Calculate grayscale version of the image using vectorized operations
    gray_image = np.dot(image[...,:3], [0.299, 0.587, 0.114])
    gray_image = gray_image[..., np.newaxis]

    # Calculate color distance for all pixels in a vectorized manner
    color_distance = np.sum((image - gray_image) ** 2, axis=-1)

    # Count the number of pixels where color distance exceeds the threshold
    n = np.sum(color_distance > 100)

    # Return True if there are more than 10 such pixels
    # TODO:
    # Proportion should be used
    return n > 10


# 基于边缘检测的简单方法（原有实现）
def is_ignore_simple(region_img, ignore_bubble = 0):
    """
    Simple edge-based bubble detection.
    Checks black/white pixel ratio at the edges of the text region.
    
    Args:
        region_img: Text region image
        ignore_bubble: Threshold 0-1 (0=disabled, higher=more strict)
    
    Returns:
        True if should be ignored (non-bubble area), False otherwise
    """
    if ignore_bubble <= 0 or ignore_bubble > 1:
        return False
    
    # Convert threshold from 0-1 to 1-50 range for compatibility
    threshold = int(ignore_bubble * 50)
    
    _, binary_raw_mask = cv2.threshold(region_img, 127, 255, cv2.THRESH_BINARY)
    height, width = binary_raw_mask.shape[:2]

    total=0
    val0=0

    val0+= sum(binary_raw_mask[0:2, 0:width].ravel() == 0)
    total+= binary_raw_mask[0:2, 0:width].size

    val0+= sum(binary_raw_mask[height-2:height, 0:width].ravel() == 0)
    total+= binary_raw_mask[height-2:height, 0:width].size

    val0+= sum(binary_raw_mask[2:height-2, 0:2].ravel() == 0)
    total+= binary_raw_mask[2:height-2, 0:2].size

    val0+= sum(binary_raw_mask[2:height-2, width-2:width].ravel() == 0)
    total += binary_raw_mask[2:height-2, width-2:width].size

    ratio = round( val0 / total, 6)*100
    # ignore
    if ratio>=threshold and ratio<=(100-threshold):
        return True
    # To determine if there is color, consider the colored text as invalid information and skip it without translation
    if check_color(region_img):
        return True
    return False


# 基于气泡边界检测的高级方法（旧版本逻辑）
def offset_margin(x, y, text_w, text_h, img_gray, sd=10, white_threshold=0.9):
    """
    Check white pixel ratio around text block edges.
    
    Args:
        white_threshold: Threshold for considering edge as white (0.85-0.95)
    
    Returns:
        gt9: Number of edges with high white ratio (0-4)
        pall: Sum of all edge white ratios (0.0-4.0)
    """
    img_h, img_w = img_gray.shape[:2]
    # left top->bottom
    roi1 = img_gray[max(y - sd, 0):min(y + text_h + sd, img_h), max(x - sd, 0):x]
    # right top->bottom
    roi2 = img_gray[max(y - sd, 0):min(y + text_h + sd, img_h), x + text_w:min(x + text_w + sd, img_w)]
    # top x->text_w
    roi3 = img_gray[max(y - sd, 0):y, x:x + text_w]
    # bottom x->text_w
    roi4 = img_gray[y + text_h:min(y + text_h + sd, img_h), x:x + text_w]
    roi1_flat, roi2_flat, roi3_flat, roi4_flat = roi1.ravel(), roi2.ravel(), roi3.ravel(), roi4.ravel()
    len_roi1, len_roi2, len_roi3, len_roi4 = len(roi1_flat), len(roi2_flat), len(roi3_flat), len(roi4_flat)
    if len_roi1 < 1 or len_roi2 < 1 or len_roi3 < 1 or len_roi4 < 1:
        return None, None
    
    r1gt200 = np.count_nonzero(roi1_flat > 200)
    r2gt200 = np.count_nonzero(roi2_flat > 200)
    r3gt200 = np.count_nonzero(roi3_flat > 200)
    r4gt200 = np.count_nonzero(roi4_flat > 200)
    pc1, pc2, pc3, pc4 = r1gt200 / len_roi1, r2gt200 / len_roi2, r3gt200 / len_roi3, r4gt200 / len_roi4
    pall = pc1 + pc2 + pc3 + pc4
    gt9 = 0
    if pc1 >= white_threshold:
        gt9 += 1
    if pc2 >= white_threshold:
        gt9 += 1
    if pc3 >= white_threshold:
        gt9 += 1
    if pc4 >= white_threshold:
        gt9 += 1
    return gt9, pall


def clear_outerwhite(x, y, text_w, text_h, new_mask_thresh):
    """Scale text_block, delete outer black area"""
    # ===================left
    n, dis, start = 0, 0, max(x - 1, 0)
    while n < text_w // 3:
        n += 1
        start += 1
        pxpoint = new_mask_thresh[y:y + text_h, start:start + 1]
        pe = np.count_nonzero(pxpoint == 0)
        top, bot = pxpoint.size * 0.98, pxpoint.size * 0.02
        if pe >= top or pe <= bot:
            dis += 1
            new_mask_thresh[y:y + text_h, start:start + 1] = 0
        else:
            break
    x += dis
    text_w -= dis
    ## ==================right
    n, dis, start = 0, 0, x + text_w + 1
    while n < text_w // 3:
        n += 1
        start -= 1
        pxpoint = new_mask_thresh[y:y + text_h, start - 1:start]
        pe = np.count_nonzero(pxpoint == 0)
        top, bot = pxpoint.size * 0.98, pxpoint.size * 0.02
        if pe >= top or pe <= bot:
            dis += 1
            new_mask_thresh[y:y + text_h, start - 1:start] = 0
        else:
            break
    text_w -= dis
    # ======================top
    n, dis, start = 0, 0, max(y - 1, 0)
    while n < text_h // 3:
        n += 1
        start += 1
        pxpoint = new_mask_thresh[start:start + 1, x:x + text_w]
        pe = np.count_nonzero(pxpoint == 0)
        top, bot = pxpoint.size * 0.98, pxpoint.size * 0.02
        if pe >= top or pe <= bot:
            dis += 1
            new_mask_thresh[start:start + 1, x:x + text_w] = 0
        else:
            break
    y += dis
    text_h -= dis
    # ======================bottom
    n, dis, start = 0, 0, y + text_h + 1
    while n < text_h // 3:
        n += 1
        start -= 1
        pxpoint = new_mask_thresh[start - 1:start, x:x + text_w]
        pe = np.count_nonzero(pxpoint == 0)
        top, bot = pxpoint.size * 0.98, pxpoint.size * 0.02
        if pe >= top or pe <= bot:
            dis += 1
            new_mask_thresh[start - 1:start, x:x + text_w] = 0
        else:
            break
    text_h -= dis
    return x, y, text_w, text_h


def rect_offset(rawx, rawy, text_w, text_h, img_gray, white_threshold=0.9):
    """Check if corners have white borders (bubble characteristic)"""
    img_h, img_w = img_gray.shape[:2]
    numbers, exceptpos, total_ok, offset = 0, '', 0, 15
    
    while numbers < 2:
        # lt
        if exceptpos != 'lt' and rawy - offset >= 0 and rawx - offset >= 0:
            x, y = rawx, rawy
            roi1 = img_gray[y - 15:y + 15, x - 15:x].ravel()
            roi1_1 = img_gray[y - 15:y, x:x + 15].ravel()
            percent = 0 if len(roi1) < 1 else np.count_nonzero(roi1 > 200) / len(roi1)
            percent_1 = 0 if len(roi1_1) < 1 else np.count_nonzero(roi1_1 > 200) / len(roi1_1)
            if percent > white_threshold and percent_1 > white_threshold:
                total_ok += 1
                exceptpos = 'lt'
        # rt
        if exceptpos != 'rt' and rawy - offset >= 0 and rawx + text_w + offset <= img_w:
            x, y = rawx + text_w, rawy
            roi1 = img_gray[y - 15:y + 15, x:x + 15].ravel()
            roi1_1 = img_gray[y - 15:y, x - 15:x].ravel()
            percent = 0 if len(roi1) < 1 else np.count_nonzero(roi1 > 200) / len(roi1)
            percent_1 = 0 if len(roi1_1) < 1 else np.count_nonzero(roi1_1 > 200) / len(roi1_1)
            if percent > white_threshold and percent_1 > white_threshold:
                total_ok += 1
                exceptpos = 'rt'
        if total_ok > 1:
            return True
        # rb
        if exceptpos != 'rb' and rawy + text_h + offset <= img_h and rawx + text_w + offset <= img_w:
            x, y = rawx + text_w, rawy + text_h
            roi1 = img_gray[y - 15:y + 15, x:x + 15].ravel()
            roi1_1 = img_gray[y:y + 15, x - 15:x].ravel()
            percent = 0 if len(roi1) < 1 else np.count_nonzero(roi1 > 200) / len(roi1)
            percent_1 = 0 if len(roi1_1) < 1 else np.count_nonzero(roi1_1 > 200) / len(roi1_1)
            if percent > white_threshold and percent_1 > white_threshold:
                total_ok += 1
                exceptpos = 'rb'
        if total_ok > 1:
            return True
        # lb
        if exceptpos != 'lb' and rawy + text_h + offset <= img_h and rawx - offset >= 0:
            x, y = rawx, rawy + text_h
            roi1 = img_gray[y - 15:y + 15, x - 15:x].ravel()
            roi1_1 = img_gray[y:y + 15, x:x + 15].ravel()
            percent = 0 if len(roi1) < 1 else np.count_nonzero(roi1 > 200) / len(roi1)
            percent_1 = 0 if len(roi1_1) < 1 else np.count_nonzero(roi1_1 > 200) / len(roi1_1)
            if percent > white_threshold and percent_1 > white_threshold:
                total_ok += 1
                exceptpos = 'lb'
        if total_ok > 1:
            return True
        offset = 8
        numbers += 1
    return False


def is_bubble_advanced(img: np.ndarray, x: int, y: int, text_w: int, text_h: int, threshold: float = 0.5):
    """
    Advanced bubble detection based on boundary analysis.
    
    Args:
        img: RGB image
        x, y, text_w, text_h: Text region coordinates
        threshold: 0-1
            - 0.5: Default (white_threshold=0.90, checkset=[3.2, 2.9])
            - <0.5: More loose (lower white_threshold, lower checkset)
            - >0.5: More strict (higher white_threshold, higher checkset)
    
    Returns:
        True if it's a bubble (should keep), False if non-bubble (should ignore)
    """
    img_gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    mask = np.zeros_like(img_gray)
    mask[y:y + text_h, x:x + text_w] = 255
    _, new_mask_thresh = cv2.threshold(cv2.bitwise_and(img_gray, mask), 127, 255, cv2.THRESH_BINARY_INV)
    new_mask_thresh[0:y, :] = 0
    new_mask_thresh[y + text_h:, :] = 0
    new_mask_thresh[y:y + text_h, 0:x] = 0
    new_mask_thresh[y:y + text_h, x + text_w:] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    new_mask_thresh = cv2.morphologyEx(new_mask_thresh, cv2.MORPH_CLOSE, kernel)
    new_mask_thresh = cv2.dilate(new_mask_thresh, kernel)
    new_mask_thresh = cv2.erode(new_mask_thresh, kernel)
    
    # text_block new position
    x, y, text_w, text_h = clear_outerwhite(x, y, text_w, text_h, new_mask_thresh)
    
    # 正比例：阈值越大越严格，原先的 0.5 映射到 0.8
    # threshold 0.1 -> white_threshold 0.55 (非常宽松，保留几乎所有区域)
    # threshold 0.5 -> white_threshold 0.75 (原默认值)
    # threshold 0.8 -> white_threshold 0.90 (对应原 0.5，新的推荐默认值)
    # threshold 1.0 -> white_threshold 0.99 (非常严格)
    white_threshold = 0.50 + threshold * 0.5  # 线性映射
    
    # 正比例：阈值越大，checkset 越大（越严格），原先的 0.5 映射到 0.8
    # threshold 0.1 -> [1.0, 0.7] (非常宽松)
    # threshold 0.5 -> [2.6, 2.3] (原默认值)
    # threshold 0.8 -> [3.2, 2.9] (对应原 0.5，新的推荐默认值)
    # threshold 1.0 -> [4.0, 3.7] (非常严格)
    base_check = 0.6 + threshold * 4.0  # 线性映射
    checkset = [base_check, base_check - 0.3]
    
    # sd add to 10
    gt9, pall = offset_margin(x, y, text_w, text_h, img_gray, 10, white_threshold)
    if gt9 is None and pall is None:
        return False
    
    # gt9: 0-4 (number of edges with high white ratio)
    # pall: 0.0-4.0 (sum of all edge white ratios)
    if gt9 >= 3 or (gt9 >= 1 and pall >= checkset[0]) or (gt9 <= 1 and pall < 1.2):
        # sd add to 20
        gt9, pall = offset_margin(x, y, text_w, text_h, img_gray, 20, white_threshold)
        if gt9 >= 3 or pall >= checkset[1] or pall <= 1.5:
            return True
    
    # Check four corners
    if rect_offset(x, y, text_w, text_h, img_gray, white_threshold):
        return True
    
    return False


def is_ignore(region_img, ignore_bubble=0, full_img=None, bbox=None):
    """
    Main function to determine if a text region should be ignored.
    
    Args:
        region_img: Text region image (RGB, 48px height for OCR)
        ignore_bubble: Threshold 0-1
            - 0: Disabled (keep all regions)
            - 0.01-0.3: Loose (keep most regions, only filter obvious non-bubbles)
            - 0.3-0.7: Medium (balanced filtering)
            - 0.7-1.0: Strict (aggressive filtering, may miss some bubbles)
        full_img: Full image (optional, for advanced method)
        bbox: Bounding box [x, y, w, h] (optional, for advanced method)
    
    Returns:
        True if should be ignored (non-bubble), False if should keep (bubble)
    """
    if ignore_bubble <= 0 or ignore_bubble > 1:
        return False
    
    # Use advanced method if full image and bbox are provided
    if full_img is not None and bbox is not None:
        x, y, w, h = bbox
        # is_bubble_advanced returns True for bubble, False for non-bubble
        # We need to invert it: return True to ignore (non-bubble)
        is_bubble = is_bubble_advanced(full_img, x, y, w, h, ignore_bubble)
        return not is_bubble  # Invert: True to ignore, False to keep
    
    # Fall back to simple method
    return is_ignore_simple(region_img, ignore_bubble)
