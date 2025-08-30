import customtkinter as ctk
from PIL import Image, ImageTk
import numpy as np
import math
import cv2
import hashlib
from typing import List, Dict, Any, Set
import editing_logic

# Import backend rendering functions and data structures
from manga_translator.rendering.text_render import put_text_horizontal, put_text_vertical, set_font
from manga_translator.rendering import resize_regions_to_font_size
from manga_translator.utils import TextBlock
from services.transform_service import TransformService

def get_bounding_box_center(unrotated_lines: List[List[List[float]]]) -> tuple:
    """Calculates the center of the bounding box for a list of polygons."""
    all_points = [p for poly in unrotated_lines for p in poly]
    if not all_points: return (0, 0)
    min_x = min(p[0] for p in all_points)
    max_x = max(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_y = max(p[1] for p in all_points)
    return (min_x + max_x) / 2, (min_y + max_y) / 2

class BackendTextRenderer:
    def __init__(self, canvas: ctk.CTkCanvas):
        self.canvas = canvas
        self.text_visible = False
        self.boxes_visible = False
        self._image_references = {}
        
        # 性能优化缓存
        self._text_render_cache = {}
        self._last_draw_time = {}
        self._draw_debounce_delay = 0.03  # 30ms防抖
    
    def _generate_render_cache_key(self, text_block, dst_points, hyphenate, line_spacing, disable_font_border):
        """生成渲染缓存键"""
        import hashlib
        
        key_components = [
            text_block.get_translation_for_rendering(),
            str(text_block.font_size),
            text_block.font_family or "default",
            str(text_block.alignment),
            str(text_block.horizontal),
            str(hyphenate),
            str(line_spacing),
            str(disable_font_border),
            str(dst_points.shape),
            str(dst_points.flatten()[:8])
        ]
        
        key_string = "|".join(key_components)
        return hashlib.md5(key_string.encode()).hexdigest()[:16]
    
    def _cache_render_result(self, cache_key, temp_box, render_w, render_h, norm_h, norm_v):
        """缓存渲染结果"""
        if len(self._text_render_cache) > 50:
            oldest_key = next(iter(self._text_render_cache))
            del self._text_render_cache[oldest_key]
        
        self._text_render_cache[cache_key] = (temp_box, render_w, render_h, norm_h, norm_v)

    def draw_regions(self, text_blocks: List[TextBlock], dst_points_list: List[np.ndarray], selected_indices: List[int] = [], transform_service: TransformService = None, hide_indices: Set[int] = None, fast_mode: bool = False, hyphenate: bool = True, line_spacing: float = None, disable_font_border: bool = False):
        if not transform_service or text_blocks is None or dst_points_list is None:
            return

        hide_indices = hide_indices or set()
        self.canvas.delete("region_text_backend")
        self.canvas.delete("region_box")
        self.canvas.delete("region_original_shape")
        self._image_references.clear()

        for i, (text_block, dst_points) in enumerate(zip(text_blocks, dst_points_list)):
            if i in hide_indices or text_block is None:
                continue
            
            if self.boxes_visible:
                self._draw_original_shape(i, text_block, selected_indices, transform_service)
                if dst_points is not None:
                    self._draw_region_box(i, dst_points, selected_indices, transform_service)

            if self.text_visible and not fast_mode and dst_points is not None:
                self._draw_region_text(i, text_block, dst_points, transform_service, hyphenate, line_spacing, disable_font_border)

    def _draw_region_text(self, i: int, text_block: TextBlock, dst_points: np.ndarray, transform_service: TransformService, hyphenate: bool, line_spacing: float, disable_font_border: bool = False):
        text_to_draw = text_block.translation or text_block.text
        if not text_to_draw:
            return

        cache_key = self._generate_render_cache_key(text_block, dst_points, hyphenate, line_spacing, disable_font_border)
        
        if cache_key in self._text_render_cache:
            temp_box, render_w, render_h, norm_h, norm_v = self._text_render_cache[cache_key]
        else:
            try:
                fg_color, bg_color = text_block.get_font_colors()
                if disable_font_border:
                    bg_color = None
                
                middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
                norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
                norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
                render_w, render_h = int(round(norm_h[0])), int(round(norm_v[0]))
                if render_w <= 0 or render_h <= 0:
                    return

                set_font(text_block.font_family)
                temp_box = None
                if text_block.horizontal:
                    temp_box = put_text_horizontal(text_block.font_size, text_block.get_translation_for_rendering(), render_w, render_h, text_block.alignment, text_block.direction == 'hl', fg_color, bg_color, text_block.target_lang, hyphenate, line_spacing)
                else:
                    temp_box = put_text_vertical(text_block.font_size, text_block.get_translation_for_rendering(), render_h, text_block.alignment, fg_color, bg_color, line_spacing)

                if temp_box is None or temp_box.size == 0:
                    return
                
                self._cache_render_result(cache_key, temp_box, render_w, render_h, norm_h, norm_v)
            except Exception as e:
                print(f"ERROR during text rendering pre-computation for region {i}: {e}")
                import traceback
                traceback.print_exc()
                return

        try:
            h_temp, w_temp, _ = temp_box.shape
            r_temp = w_temp / h_temp if h_temp > 0 else 0
            r_orig = norm_h[0] / norm_v[0] if norm_v[0] > 0 else 0
            box = None
            if text_block.horizontal:
                if r_temp > r_orig and r_orig > 0:   
                    h_ext = int((w_temp / r_orig - h_temp) // 2)
                    if h_ext >= 0: 
                        box = np.zeros((h_temp + h_ext * 2, w_temp, 4), dtype=np.uint8)
                        box[h_ext:h_ext+h_temp, 0:w_temp] = temp_box
                else:   
                    w_ext = int((h_temp * r_orig - w_temp) // 2)  
                    if w_ext >= 0: 
                        box = np.zeros((h_temp, w_temp + w_ext * 2, 4), dtype=np.uint8)
                        box[0:h_temp, 0:w_temp] = temp_box
            else:  
                if r_temp > r_orig and r_orig > 0:   
                    h_ext = int(w_temp / (2 * r_orig) - h_temp / 2)
                    if h_ext >= 0: 
                        box = np.zeros((h_temp + h_ext * 2, w_temp, 4), dtype=np.uint8)
                        box[0:h_temp, 0:w_temp] = temp_box
                else:   
                    w_ext = int((h_temp * r_orig - w_temp) // 2)  
                    if w_ext >= 0: 
                        box = np.zeros((h_temp, w_temp + w_ext * 2, 4), dtype=np.uint8)
                        box[0:h_temp, w_ext:w_ext+w_temp] = temp_box
            if box is None: 
                box = temp_box.copy()

            src_points = np.float32([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]])
            dst_points_screen = np.float32([transform_service.image_to_screen(p[0], p[1]) for p in dst_points[0]])

            x_coords, y_coords = dst_points_screen[:, 0], dst_points_screen[:, 1]
            if (np.max(x_coords) - np.min(x_coords)) < 1.0 or (np.max(y_coords) - np.min(y_coords)) < 1.0:
                return
            
            x_s, y_s, w_s, h_s = cv2.boundingRect(dst_points_screen.astype(np.int32))
            if w_s <= 0 or h_s <= 0: 
                return

            dst_points_warp = dst_points_screen - [x_s, y_s]
            matrix, _ = cv2.findHomography(src_points, dst_points_warp, cv2.RANSAC, 5.0)
            if matrix is None:
                return

            warped_image = cv2.warpPerspective(box, matrix, (w_s, h_s), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0,0))

            pil_image = Image.fromarray(warped_image)
            region_tag = f"region_text_{i}"
            photo_image = ImageTk.PhotoImage(pil_image)
            self._image_references[region_tag] = photo_image

            self.canvas.create_image(x_s, y_s, image=photo_image, anchor='nw', tags=(region_tag, "region_text_backend"))

        except Exception as e:
            print(f"Error during backend text rendering for region {i}: {e}")

    def _draw_region_box(self, i: int, dst_points: np.ndarray, selected_indices: List[int], transform_service: TransformService):
        if dst_points is None or dst_points.size == 0 or i not in selected_indices:
            return
            
        screen_coords = [c for p in dst_points[0] for c in transform_service.image_to_screen(p[0], p[1])]
        if len(screen_coords) < 2: 
            return
        
        region_tag = f"region_box_{i}"
        self.canvas.create_polygon(screen_coords, outline="green", fill="", width=1, tags=(region_tag, "region_box"))

    def _draw_original_shape(self, i: int, text_block: TextBlock, selected_indices: List[int], transform_service: TransformService):
        if not text_block or text_block.lines.size == 0:
            return
        
        region_tag = f"region_original_shape_{i}"
        is_selected = (i in selected_indices)

        outline_color = "#3a7ebf" if is_selected else "yellow"
        width = 2
        dash = ()
        
        world_coords_polygons = []
        for poly in text_block.lines:
            angle = text_block.angle if hasattr(text_block, 'angle') else 0
            if angle != 0:
                center = text_block.center if hasattr(text_block, 'center') else get_bounding_box_center(text_block.lines)
                rotated_poly = [editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly]
                world_coords_polygons.append(rotated_poly)
            else:
                world_coords_polygons.append(poly)

        for poly in world_coords_polygons:
            screen_coords = [c for p in poly for c in transform_service.image_to_screen(p[0], p[1])]
            if len(screen_coords) < 2: continue

            self.canvas.create_polygon(screen_coords, outline=outline_color, fill="", width=width, dash=dash, tags=(region_tag, "region_original_shape"))

        if is_selected:
            handle_size = 8
            for poly_idx, poly in enumerate(world_coords_polygons):
                for vertex_idx, (x, y) in enumerate(poly):
                    sx, sy = transform_service.image_to_screen(x, y)
                    self.canvas.create_oval(sx - handle_size / 2, sy - handle_size / 2, sx + handle_size / 2, sy + handle_size / 2,
                                            fill="blue", outline="white", width=1, tags=(f"region_{i}", "handle", f"vertex_{poly_idx}_{vertex_idx}"))

            if world_coords_polygons:
                first_poly = world_coords_polygons[0]
                center_x = sum(p[0] for p in first_poly) / len(first_poly)
                center_y = sum(p[1] for p in first_poly) / len(first_poly)
                
                handle_y_offset = -30 
                angle = text_block.angle if hasattr(text_block, 'angle') else 0
                offset_x_rot, offset_y_rot = editing_logic.rotate_point(0, handle_y_offset, angle, 0, 0)
                handle_x, handle_y = center_x + offset_x_rot, center_y + offset_y_rot
                
                sx_center, sy_center = transform_service.image_to_screen(center_x, center_y)
                sx_handle, sy_handle = transform_service.image_to_screen(handle_x, handle_y)

                self.canvas.create_line(sx_handle, sy_handle, sx_center, sy_center, fill="red", width=2, tags=(f"region_{i}", "handle", "rotate_line"))
                self.canvas.create_oval(sx_handle - handle_size / 2, sy_handle - handle_size / 2, sx_handle + handle_size / 2, sy_handle + handle_size / 2,
                                        fill="red", outline="white", width=1, tags=(f"region_{i}", "handle", "rotation_handle"))

    def set_text_visibility(self, visible: bool):
        self.text_visible = visible

    def set_boxes_visibility(self, visible: bool):
        self.boxes_visible = visible

    def toggle_boxes_visibility(self):
        self.boxes_visible = not self.boxes_visible