import os
import cv2
import numpy as np
from typing import List
from shapely import affinity
from shapely.geometry import Polygon
from tqdm import tqdm

from . import text_render
from .text_render_eng import render_textblock_list_eng
from .text_render_pillow_eng import render_textblock_list_eng as render_textblock_list_eng_pillow
from ..utils import (
    BASE_PATH,
    TextBlock,
    color_difference,
    get_logger,
    rotate_polygons,
)
from ..config import Config

logger = get_logger('render')

def parse_font_paths(path: str, default: List[str] = None) -> List[str]:
    if path:
        parsed = path.split(',')
        parsed = list(filter(lambda p: os.path.isfile(p), parsed))
    else:
        parsed = default or []
    return parsed

def fg_bg_compare(fg, bg):
    fg_avg = np.mean(fg)
    if color_difference(fg, bg) < 30:
        bg = (255, 255, 255) if fg_avg <= 127 else (0, 0, 0)
    return fg, bg

def count_text_length(text: str) -> float:
    """Calculate text length, treating っッぁぃぅぇぉ as 0.5 characters"""
    half_width_chars = 'っッぁぃぅぇぉ'  
    length = 0.0
    for char in text.strip():
        if char in half_width_chars:
            length += 0.5
        else:
            length += 1.0
    return length

def resize_regions_to_font_size(img: np.ndarray, text_regions: List['TextBlock'], config: Config):
    mode = config.render.layout_mode
    logger.debug(f"Resizing regions with layout mode: {mode}")

    dst_points_list = []
    for region in text_regions:
        if region is None:
            dst_points_list.append(None)
            continue
        text_render.set_font(region.font_family) 
        original_region_font_size = region.font_size if region.font_size > 0 else round((img.shape[0] + img.shape[1]) / 200)
        
        font_size_offset = config.render.font_size_offset
        min_font_size = max(config.render.font_size_minimum if config.render.font_size_minimum > 0 else 1, 1)
        target_font_size = max(original_region_font_size + font_size_offset, min_font_size)

        # --- Mode 1: disable_all (unchanged) ---
        if mode == 'disable_all':
            region.font_size = int(target_font_size)
            dst_points_list.append(region.min_rect)
            continue

        # --- Mode 2: strict (unchanged) ---
        elif mode == 'strict':
            font_size = target_font_size
            min_shrink_font_size = max(min_font_size, 8)
            while font_size >= min_shrink_font_size:
                if region.horizontal:
                    lines, _ = text_render.calc_horizontal(font_size, region.translation, max_width=region.unrotated_size[0], max_height=region.unrotated_size[1], language=region.target_lang)
                    if len(lines) <= len(region.texts):
                        break
                else:
                    lines, _ = text_render.calc_vertical(font_size, region.translation, max_height=region.unrotated_size[1])
                    if len(lines) <= len(region.texts):
                        break
                font_size -= 1
            region.font_size = int(max(font_size, min_shrink_font_size))
            dst_points_list.append(region.min_rect)
            continue

        # --- Mode 3: default (uses old logic, unchanged) ---
        elif mode == 'default':
            font_size_fixed = config.render.font_size
            font_size_offset_old = config.render.font_size_offset
            font_size_minimum_old = config.render.font_size_minimum
            if font_size_minimum_old == -1: font_size_minimum_old = round((img.shape[0] + img.shape[1]) / 200)
            font_size_minimum_old = max(1, font_size_minimum_old)
            original_region_font_size_old = region.font_size
            if original_region_font_size_old <= 0: original_region_font_size_old = font_size_minimum_old
            if font_size_fixed is not None: target_font_size_old = font_size_fixed
            else: target_font_size_old = original_region_font_size_old + font_size_offset_old
            target_font_size_old = max(target_font_size_old, font_size_minimum_old, 1)
            single_axis_expanded = False
            dst_points = None
            if region.horizontal:
                used_rows = len(region.texts)
                line_text_list, _ = text_render.calc_horizontal(region.font_size, region.translation, max_width=region.unrotated_size[0], max_height=region.unrotated_size[1], language=getattr(region, "target_lang", "en_US"))
                needed_rows = len(line_text_list)
                if needed_rows > used_rows:
                    scale_x = ((needed_rows - used_rows) / used_rows) * 1 + 1
                    try:
                        poly = Polygon(region.unrotated_min_rect[0]); minx, miny, _, _ = poly.bounds
                        poly = affinity.scale(poly, xfact=scale_x, yfact=1.0, origin=(minx, miny))
                        pts = np.array(poly.exterior.coords[:4])
                        dst_points = rotate_polygons(region.center, pts.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2).astype(np.int64)
                        single_axis_expanded = True
                    except: pass
            if region.vertical:
                used_cols = len(region.texts)
                line_text_list, _ = text_render.calc_vertical(region.font_size, region.translation, max_height=region.unrotated_size[1])
                needed_cols = len(line_text_list)
                if needed_cols > used_cols:
                    scale_x = ((needed_cols - used_cols) / used_cols) * 1 + 1
                    try:
                        poly = Polygon(region.unrotated_min_rect[0]); minx, miny, _, _ = poly.bounds
                        poly = affinity.scale(poly, xfact=1.0, yfact=scale_x, origin=(minx, miny))
                        pts = np.array(poly.exterior.coords[:4])
                        dst_points = rotate_polygons(region.center, pts.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2).astype(np.int64)
                        single_axis_expanded = True
                    except: pass
            if not single_axis_expanded:
                orig_text = getattr(region, "text_raw", region.text); char_count_orig = count_text_length(orig_text); char_count_trans = count_text_length(region.translation.strip())
                if char_count_orig > 0 and char_count_trans > char_count_orig:
                    increase_percentage = (char_count_trans - char_count_orig) / char_count_orig; font_increase_ratio = min(1.5, max(1.0, 1 + (increase_percentage * 0.3)))
                    target_font_size_old = int(target_font_size_old * font_increase_ratio)
                    target_scale = max(1, min(1 + increase_percentage * 0.3, 2))
                else: target_scale = 1
                font_size_scale = (((target_font_size_old - original_region_font_size_old) / original_region_font_size_old) * 0.4 + 1) if original_region_font_size_old > 0 else 1.0
                final_scale = max(font_size_scale, target_scale); final_scale = max(1, min(final_scale, 1.1))
                if final_scale > 1.001:
                    try:
                        poly = Polygon(region.unrotated_min_rect[0]); poly = affinity.scale(poly, xfact=final_scale, yfact=final_scale, origin='center')
                        scaled_unrotated_points = np.array(poly.exterior.coords[:4])
                        dst_points = rotate_polygons(region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2).astype(np.int64).reshape((-1, 4, 2))
                    except: dst_points = region.min_rect
                else: dst_points = region.min_rect
            dst_points_list.append(dst_points)
            region.font_size = int(target_font_size_old)
            continue

        # --- Mode 4: smart_scaling (MODIFIED to match default's quirky behavior) ---
        elif mode == 'smart_scaling':
            # First, check if wrapping is needed
            if region.horizontal:
                lines, widths = text_render.calc_horizontal(target_font_size, region.translation, max_width=region.unrotated_size[0], max_height=region.unrotated_size[1], language=region.target_lang)
            else: # Vertical
                lines, heights = text_render.calc_vertical(target_font_size, region.translation, max_height=region.unrotated_size[1])
            
            dst_points = region.min_rect # Default to original box

            if len(lines) > len(region.texts): # Wrapping is needed, apply user's algorithm
                try:
                    poly = Polygon(region.unrotated_min_rect[0])
                    minx, miny, _, _ = poly.bounds
                    
                    scale_factor_x = 1.0
                    scale_factor_y = 1.0

                    if region.horizontal:
                        # Mimic default mode: scale width for horizontal wrapping
                        needed_rows = len(lines)
                        used_rows = len(region.texts)
                        scale_factor_x = ((needed_rows - used_rows) / used_rows) * 1 + 1
                    elif region.vertical:
                        # New "pixel ratio" calculation for vertical text
                        # To get the true required height, we need to calculate the height for all text in a single column
                        # by calling calc_vertical with a very large max_height.
                        _lines_single_col, heights_single_col = text_render.calc_vertical(target_font_size, region.translation, max_height=99999)
                        if heights_single_col:
                            required_height = max(heights_single_col)
                            original_height = region.unrotated_size[1]
                            if original_height > 0:
                                # Add a small margin for safety, e.g., 5%
                                scale_factor_y = (required_height / original_height) * 1.05
                    
                    # Cap the scaling to prevent extreme results
                    scale_factor_x = min(scale_factor_x, 3.0)
                    scale_factor_y = min(scale_factor_y, 3.0)

                    # Apply scaling with conditional origin
                    if scale_factor_x > 1.001 or scale_factor_y > 1.001:
                        origin_point = 'center' if len(region.lines) == 1 else (minx, miny)
                        poly = affinity.scale(poly, xfact=scale_factor_x, yfact=scale_factor_y, origin=origin_point)
                        scaled_unrotated_points = np.array(poly.exterior.coords[:4])
                        dst_points = rotate_polygons(region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2).astype(np.int64)

                except Exception as e:
                    logger.warning(f"Failed to scale region with custom logic: {e}")
                    dst_points = region.min_rect
            
            else: # No wrapping needed, use old uniform scaling logic as a fallback
                orig_text = getattr(region, "text_raw", region.text); char_count_orig = count_text_length(orig_text); char_count_trans = count_text_length(region.translation.strip())
                if char_count_orig > 0 and char_count_trans > char_count_orig:
                    increase_percentage = (char_count_trans - char_count_orig) / char_count_orig; font_increase_ratio = min(1.5, max(1.0, 1 + (increase_percentage * 0.3)))
                    target_font_size = int(target_font_size * font_increase_ratio)
                    target_scale = max(1, min(1 + increase_percentage * 0.3, 2))
                else: target_scale = 1
                font_size_scale = (((target_font_size - original_region_font_size) / original_region_font_size) * 0.4 + 1) if original_region_font_size > 0 else 1.0
                final_scale = max(font_size_scale, target_scale); final_scale = max(1, min(final_scale, 1.1))
                if final_scale > 1.001:
                    try:
                        poly = Polygon(region.unrotated_min_rect[0]); poly = affinity.scale(poly, xfact=final_scale, yfact=final_scale, origin='center')
                        scaled_unrotated_points = np.array(poly.exterior.coords[:4])
                        dst_points = rotate_polygons(region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2).astype(np.int64).reshape((-1, 4, 2))
                    except: dst_points = region.min_rect
            
            dst_points_list.append(dst_points)
            region.font_size = int(target_font_size)
            continue

        # --- Fallback for any other modes (e.g., 'fixed_font') ---
        else:
            dst_points_list.append(region.min_rect)
            region.font_size = int(min(target_font_size, 512))
            continue

    return dst_points_list


async def dispatch(
    img: np.ndarray,
    text_regions: List[TextBlock],
    font_path: str = '',
    config: Config = None
    ) -> np.ndarray:

    if config is None:
        from ..config import Config
        config = Config()

    text_render.set_font(font_path)
    text_regions = list(filter(lambda region: region.translation, text_regions))

    dst_points_list = resize_regions_to_font_size(img, text_regions, config)

    for region, dst_points in tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions)):
        img = render(img, region, dst_points, not config.render.no_hyphenation, config.render.line_spacing, config.render.disable_font_border)
    return img

def render(
    img,
    region: TextBlock,
    dst_points,
    hyphenate,
    line_spacing,
    disable_font_border
):
    fg, bg = region.get_font_colors()
    fg, bg = fg_bg_compare(fg, bg)

    if disable_font_border :
        bg = None

    middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
    norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
    norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
    r_orig = np.mean(norm_h / norm_v)

    forced_direction = region._direction if hasattr(region, "_direction") else region.direction
    if forced_direction != "auto":
        if forced_direction in ["horizontal", "h"]:
            render_horizontally = True
        elif forced_direction in ["vertical", "v"]:
            render_horizontally = False
        else:
            render_horizontally = region.horizontal
    else:
        render_horizontally = region.horizontal

    if render_horizontally:
        temp_box = text_render.put_text_horizontal(
            region.font_size,
            region.get_translation_for_rendering(),
            round(norm_h[0]),
            round(norm_v[0]),
            region.alignment,
            region.direction == 'hl',
            fg,
            bg,
            region.target_lang,
            hyphenate,
            line_spacing,
        )
    else:
        temp_box = text_render.put_text_vertical(
            region.font_size,
            region.get_translation_for_rendering(),
            round(norm_v[0]),
            region.alignment,
            fg,
            bg,
            line_spacing,
        )
    h, w, _ = temp_box.shape
    if h == 0 or w == 0:
        logger.warning(f"Skipping rendering for region with invalid dimensions (w={w}, h={h}). Text: '{region.translation}'")
        return img
    r_temp = w / h

    box = None  
    if region.horizontal:  
        if r_temp > r_orig:   
            h_ext = int((w / r_orig - h) // 2) if r_orig > 0 else 0  
            if h_ext >= 0:  
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)  
                box[h_ext:h_ext+h, 0:w] = temp_box  
            else:  
                box = temp_box.copy()  
        else:   
            w_ext = int((h * r_orig - w) // 2)  
            if w_ext >= 0:  
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)  
                box[0:h, 0:w] = temp_box  
            else:  
                box = temp_box.copy()  
    else:  
        if r_temp > r_orig:   
            h_ext = int(w / (2 * r_orig) - h / 2) if r_orig > 0 else 0   
            if h_ext >= 0:   
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)  
                box[0:h, 0:w] = temp_box  
            else:   
                box = temp_box.copy()   
        else:   
            w_ext = int((h * r_orig - w) / 2)  
            if w_ext >= 0:  
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)  
                box[0:h, w_ext:w_ext+w] = temp_box  
            else:   
                box = temp_box.copy()   

    src_points = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]]).astype(np.float32)

    M, _ = cv2.findHomography(src_points, dst_points, cv2.RANSAC, 5.0)
    rgba_region = cv2.warpPerspective(box, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    x, y, w, h = cv2.boundingRect(dst_points.astype(np.int32))
    canvas_region = rgba_region[y:y+h, x:x+w, :3]
    mask_region = rgba_region[y:y+h, x:x+w, 3:4].astype(np.float32) / 255.0
    img[y:y+h, x:x+w] = np.clip((img[y:y+h, x:x+w].astype(np.float32) * (1 - mask_region) + canvas_region.astype(np.float32) * mask_region), 0, 255).astype(np.uint8)
    return img

async def dispatch_eng_render(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/comic shanns 2.ttf')
    text_render.set_font(font_path)

    return render_textblock_list_eng(img_canvas, text_regions, line_spacing=line_spacing, size_tol=1.2, original_img=original_img, downscale_constraint=0.8,disable_font_border=disable_font_border)

async def dispatch_eng_render_pillow(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttf.ttc')
    text_render.set_font(font_path)

    return render_textblock_list_eng_pillow(font_path, img_canvas, text_regions, original_img=original_img, downscale_constraint=0.95)