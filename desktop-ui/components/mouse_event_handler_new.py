import customtkinter as ctk
import copy
import math
import editing_logic
from services.transform_service import TransformService
from typing import Callable, List, Dict, Any, Set, Tuple
from .text_renderer_backend import get_bounding_box_center

class MouseEventHandler:
    def __init__(self, canvas, regions, transform_service: TransformService, 
                 on_region_selected: Callable[[List[int]], None] = None, 
                 on_region_moved: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_resized: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_rotated: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_created: Callable[[Dict[str, Any]], None] = None, 
                 on_draw_new_region_preview: Callable[[List[float]], None] = None, 
                 on_geometry_added: Callable[[int, List[List[float]]], None] = None,
                 on_drag_preview: Callable[[List[List[float]]], None] = None,
                 on_zoom_start: Callable[[], None] = None,
                 on_zoom_end: Callable[[], None] = None,
                 on_mask_draw_preview: Callable[[List[Tuple[int, int]]], None] = None,
                 on_mask_edit_start: Callable[[], None] = None,
                 on_mask_edit_end: Callable[[List[Tuple[int, int]]], None] = None):
        self.canvas = canvas
        self.regions = regions
        self.transform_service = transform_service
        self.on_region_selected = on_region_selected
        self.on_region_moved = on_region_moved
        self.on_region_resized = on_region_resized
        self.on_region_rotated = on_region_rotated
        self.on_region_created = on_region_created
        self.on_draw_new_region_preview = on_draw_new_region_preview
        self.on_geometry_added = on_geometry_added
        self.on_drag_preview = on_drag_preview
        self.on_zoom_start = on_zoom_start
        self.on_zoom_end = on_zoom_end
        self.on_mask_draw_preview = on_mask_draw_preview
        self.on_mask_edit_start = on_mask_edit_start
        self.on_mask_edit_end = on_mask_edit_end
        
        self.action_info: Dict[str, Any] = {}
        self.selected_indices: Set[int] = set()
        self._zoom_debounce_timer = None
        self._zoom_end_timer = None
        self.mode = 'select'
        self.is_dragging = False
        self.brush_size = 20

        self.canvas.bind("<Button-1>", self.on_left_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag_stop)
        self.canvas.bind("<Button-2>", self.on_pan_start)
        self.canvas.bind("<B2-Motion>", self.on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self.on_pan_stop)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Motion>", self._update_cursor)

    def set_mode(self, mode: str):
        if mode in ['select', 'draw', 'geometry_edit', 'mask_edit']:
            self.mode = mode
            self.canvas.config(cursor="crosshair" if self.mode != 'select' else "")

    def set_brush_size(self, size):
        self.brush_size = size

    def _update_cursor(self, event):
        if self.action_info.get('type'): return

        if self.mode == 'mask_edit':
            self.canvas.config(cursor="none")
            self._draw_brush_cursor(event)
            return
        else:
            self.canvas.delete("brush_cursor")

        if self.mode != 'select':
            self.canvas.config(cursor="crosshair")
            return

        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)
        new_cursor = ""

        if len(self.selected_indices) == 1:
            region_index = list(self.selected_indices)[0]
            region = self.regions[region_index]

            if not isinstance(region, dict):
                return

            angle = region.get('angle', 0)
            center = region.get('center') or get_bounding_box_center(region.get('lines', []))
            rotated_lines = region.get('lines', [])
            if angle != 0:
                rotated_lines = [[self.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly] for poly in region.get('lines', [])]

            rotation_handle_items = self.canvas.find_withtag("rotation_handle")
            overlapping_items = self.canvas.find_overlapping(x, y, x, y)
            if overlapping_items and overlapping_items[-1] in rotation_handle_items and f"region_{region_index}" in self.canvas.gettags(overlapping_items[-1]):
                new_cursor = "exchange"

            if not new_cursor:
                for poly_idx, poly in enumerate(rotated_lines):
                    for vertex_idx, (vx, vy) in enumerate(poly):
                        if math.hypot(img_x - vx, img_y - vy) * self.transform_service.zoom_level < 10:
                            new_cursor = "cross"
                            break
                    if not new_cursor:
                        for edge_idx in range(len(poly)):
                            if self.is_on_segment(img_x, img_y, poly[edge_idx], poly[(edge_idx + 1) % len(poly)]):
                                edge_vector = (poly[(edge_idx + 1) % len(poly)][0] - poly[edge_idx][0], 
                                              poly[(edge_idx + 1) % len(poly)][1] - poly[edge_idx][1])
                                edge_angle = math.degrees(math.atan2(edge_vector[1], edge_vector[0]))
                                new_cursor = self._get_edge_cursor(edge_angle)
                                break
                    if new_cursor: break
        
        if not new_cursor and any(self.is_point_in_region(img_x, img_y, self.regions[i]) for i in self.selected_indices):
            new_cursor = "fleur"

        if self.canvas["cursor"] != new_cursor:
            self.canvas.config(cursor=new_cursor)

    def _get_edge_cursor(self, angle):
        angle = angle % 360
        if angle < 0:
            angle += 360
        if 45 <= angle < 135 or 225 <= angle < 315:
            return "sb_v_double_arrow"
        else:
            return "sb_h_double_arrow"

    def _draw_brush_cursor(self, event):
        self.canvas.delete("brush_cursor")
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        radius = self.brush_size / 2
        # Draw a black circle with a white outline for better visibility
        self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                outline="white", fill="black", width=1, tags="brush_cursor")

    def on_left_click(self, event):
        self.action_info = {}
        self.is_dragging = False
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)

        if self.mode == 'mask_edit':
            self.action_info = {'type': 'mask_edit', 'points': []}
            if self.on_mask_edit_start:
                self.on_mask_edit_start()
            return

        if self.mode != 'select':
            self.action_info = {'type': self.mode, 'start_x': img_x, 'start_y': img_y}
            return

        if len(self.selected_indices) == 1:
            region_index = list(self.selected_indices)[0]
            region = self.regions[region_index]

            if not isinstance(region, dict):
                return

            angle = region.get('angle', 0)
            center = region.get('center') or get_bounding_box_center(region.get('lines', []))

            rotated_lines = region.get('lines', [])
            if angle != 0:
                rotated_lines = [[self.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly] for poly in region.get('lines', [])]

            rotation_handle_items = self.canvas.find_withtag("rotation_handle")
            overlapping_items = self.canvas.find_overlapping(x, y, x, y)
            if overlapping_items and overlapping_items[-1] in rotation_handle_items and f"region_{region_index}" in self.canvas.gettags(overlapping_items[-1]):
                center_screen_x, center_screen_y = self.transform_service.image_to_screen(center[0], center[1])
                self.action_info = {
                    'type': 'rotate', 
                    'original_data': copy.deepcopy(region), 
                    'center_x': center[0], 
                    'center_y': center[1], 
                    'start_angle_rad': math.atan2(y - center_screen_y, x - center_screen_x), 
                    'original_angle': angle
                }
                return

            for poly_idx, poly in enumerate(rotated_lines):
                for vertex_idx, (vx, vy) in enumerate(poly):
                    if math.hypot(img_x - vx, img_y - vy) * self.transform_service.zoom_level < 10:
                        self.action_info = {'type': 'vertex_edit', 'poly_index': poly_idx, 'vertex_index': vertex_idx, 'original_data': copy.deepcopy(region), 'start_x_img': img_x, 'start_y_img': img_y}
                        return
                for edge_idx in range(len(poly)):
                    if self.is_on_segment(img_x, img_y, poly[edge_idx], poly[(edge_idx + 1) % len(poly)]):
                        self.action_info = {'type': 'edge_edit', 'poly_index': poly_idx, 'edge_index': edge_idx, 'original_data': copy.deepcopy(region), 'start_x_img': img_x, 'start_y_img': img_y}
                        return

        clicked_region_index = -1
        for i, region in reversed(list(enumerate(self.regions))):
            if self.is_point_in_region(img_x, img_y, self.regions[i]):
                clicked_region_index = i
                break

        ctrl_pressed = (event.state & 0x4) != 0
        if ctrl_pressed:
            if clicked_region_index != -1:
                self.selected_indices.symmetric_difference_update({clicked_region_index})
        else:
            if clicked_region_index != -1:
                if clicked_region_index not in self.selected_indices:
                    self.selected_indices = {clicked_region_index}
                self.action_info = {'type': 'move', 'start_x_img': img_x, 'start_y_img': img_y, 'original_data': [copy.deepcopy(self.regions[i]) for i in self.selected_indices]}
            else:
                if self.selected_indices:
                    self.selected_indices = set()
                self.action_info = {'type': 'pan_prepare'}

        if self.on_region_selected:
            self.on_region_selected(list(self.selected_indices))

    def on_drag(self, event):
        if not self.is_dragging:
            self.is_dragging = True
            if self.action_info.get('type') == 'pan_prepare':
                self.on_pan_start(event)
        
        action_type = self.action_info.get('type')
        if not action_type or action_type == 'pan_prepare': return

        if action_type == 'mask_edit':
            if self.on_mask_draw_preview:
                x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
                img_x, img_y = self.transform_service.screen_to_image(x, y)
                self.action_info['points'].append((img_x, img_y))
                self.on_mask_draw_preview(self.action_info['points'])
            return

        # --- Preview for Draw ---
        if action_type == 'draw':
            if self.on_draw_new_region_preview:
                x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
                img_x, img_y = self.transform_service.screen_to_image(x, y)
                start_x, start_y = self.action_info['start_x'], self.action_info['start_y']
                rect = [min(start_x, img_x), min(start_y, img_y), max(start_x, img_x), max(start_y, img_y)]
                self.on_draw_new_region_preview(rect)
            return

        # --- Preview for Geometry Edit (add parallel shape) ---
        if action_type == 'geometry_edit':
            if len(self.selected_indices) == 1:
                x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
                img_x, img_y = self.transform_service.screen_to_image(x, y)
                start_x, start_y = self.action_info['start_x'], self.action_info['start_y']

                region_index = list(self.selected_indices)[0]
                region_data = self.regions[region_index]
                angle = region_data.get('angle', 0)

                # Use the new logic to calculate the preview shape based on diagonal
                new_poly_world = editing_logic.calculate_rectangle_from_diagonal(
                    start_point=(start_x, start_y),
                    end_point=(img_x, img_y),
                    angle_deg=angle
                )

                # We want to preview the existing shapes plus the new one
                center = region_data.get('center')
                if not center: # Fallback if center not available
                    self.on_drag_preview([new_poly_world])
                    return

                # Convert new shape to model space to add it to the list
                new_poly_model = [
                    list(editing_logic.rotate_point(p[0], p[1], -angle, center[0], center[1]))
                    for p in new_poly_world
                ]
                all_model_polygons = region_data.get('lines', []) + [new_poly_model]

                # Rotate all polygons for the final preview
                preview_polygons = [
                    [self.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly]
                    for poly in all_model_polygons
                ]
                self.on_drag_preview(preview_polygons)
            return

        # --- Pan ---
        if action_type == 'pan':
            self.on_pan_drag(event)
            return

        if 'original_data' not in self.action_info:
            return

        # --- Preview for Move, Resize, Rotate ---
        new_data = self._get_drag_preview_data(event)
        if new_data and self.on_drag_preview:
            regions_to_preview = new_data if isinstance(new_data, list) else [new_data]
            all_rotated_polygons = []

            for i, region in enumerate(regions_to_preview):
                angle = region.get('angle', 0)
                lines = region.get('lines', [])
                
                if angle != 0:
                    center_data_source = self.action_info['original_data']
                    if isinstance(center_data_source, list):
                        center_data_source = center_data_source[i]

                    center = center_data_source.get('center')
                    if not center:
                        all_points = [tuple(p) for poly in lines for p in poly]
                        center = editing_logic.get_polygon_center(all_points)

                    rotated_polygons = [[self.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly] for poly in lines]
                    all_rotated_polygons.extend(rotated_polygons)
                else:
                    all_rotated_polygons.extend(lines)
            
            self.on_drag_preview(all_rotated_polygons)

    def on_drag_stop(self, event):
        if not self.is_dragging and self.action_info.get('type') != 'pan_prepare':
            self.action_info = {}
            return

        action_type = self.action_info.get('type')

        if action_type == 'mask_edit':
            if self.on_mask_edit_end:
                self.on_mask_edit_end(self.action_info['points'])
        
        if action_type in ['move', 'rotate', 'vertex_edit', 'edge_edit']:
            if 'original_data' not in self.action_info: return
            if len(self.selected_indices) > 1 and action_type == 'move':
                 for i, original_data in enumerate(self.action_info['original_data']):
                    new_data = self._get_final_drag_data(original_data, event)
                    if self.on_region_moved:
                        self.on_region_moved(list(self.selected_indices)[i], original_data, new_data)
            elif len(self.selected_indices) == 1:
                new_region_data = self._get_final_drag_data(self.action_info['original_data'], event)
                if new_region_data:
                    idx = list(self.selected_indices)[0]
                    old_data = self.action_info['original_data']
                    if isinstance(old_data, list):
                        old_data = old_data[0]

                    if action_type == 'move' and self.on_region_moved: self.on_region_moved(idx, old_data, new_region_data)
                    elif action_type == 'rotate' and self.on_region_rotated: self.on_region_rotated(idx, old_data, new_region_data)
                    elif self.on_region_resized: self.on_region_resized(idx, old_data, new_region_data)
        
        elif action_type in ['draw', 'geometry_edit']:
            x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            img_x, img_y = self.transform_service.screen_to_image(x, y)
            start_x, start_y = self.action_info['start_x'], self.action_info['start_y']
            
            if abs(img_x - start_x) > 5 and abs(img_y - start_y) > 5:
                if action_type == 'draw' and self.on_region_created:
                    x0, y0, x1, y1 = min(start_x, img_x), min(start_y, img_y), max(start_x, img_x), max(start_y, img_y)
                    new_poly = [[int(x0), int(y0)], [int(x1), int(y0)], [int(x1), int(y1)], [int(x0), int(y1)]]
                    center_x, center_y = editing_logic.get_polygon_center(new_poly)
                    
                    new_region_data = {
                        'lines': [new_poly],
                        'texts': [''],
                        'text': '', # Keep for potential compatibility
                        'translation': '',
                        'font_size': 40,
                        'angle': 0,
                        'fg_color': (0, 0, 0),
                        'bg_color': (255, 255, 255),
                        'alignment': 'center',
                        'direction': 'h',
                        'target_lang': 'CHS',
                        'source_lang': '',
                        'line_spacing': 1.0,
                        'default_stroke_width': 0.2,
                        'adjust_bg_color': True,
                        'prob': 1.0,
                        'center': [center_x, center_y]
                    }
                    self.on_region_created(new_region_data)
                
                elif action_type == 'geometry_edit' and self.on_geometry_added and self.selected_indices:
                    region_index = list(self.selected_indices)[0]
                    region_data = self.regions[region_index]
                    angle = region_data.get('angle', 0)
                    
                    # Calculate the new polygon in world coordinates based on the drag
                    new_poly_world = editing_logic.calculate_rectangle_from_diagonal(
                        start_point=(start_x, start_y),
                        end_point=(img_x, img_y),
                        angle_deg=angle
                    )
                    
                    # Pass the raw world-coordinate polygon to the higher-level handler
                    self.on_geometry_added(region_index, new_poly_world)
            if self.on_draw_new_region_preview: self.on_draw_new_region_preview(None)
            self.set_mode('select')

        self.action_info = {}
        self.is_dragging = False
        if self.on_drag_preview: self.on_drag_preview(None)
        self._update_cursor(event)

    def _get_drag_preview_data(self, event):
        return self._get_final_drag_data(self.action_info['original_data'], event)

    def _get_final_drag_data(self, original_data_in, event):
        action_type = self.action_info.get('type')
        if not action_type: return None
        
        original_data = original_data_in[0] if isinstance(original_data_in, list) else original_data_in
        new_data = copy.deepcopy(original_data)
        
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)

        if action_type == 'move':
            offset_x = img_x - self.action_info['start_x_img']
            offset_y = img_y - self.action_info['start_y_img']
            for poly in new_data.get('lines', []):
                for p in poly:
                    p[0] += offset_x
                    p[1] += offset_y
            if new_data.get('center'):
                new_data['center'][0] += offset_x
                new_data['center'][1] += offset_y

        elif action_type == 'rotate':
            center_x, center_y = self.action_info['center_x'], self.action_info['center_y']
            x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            center_screen_x, center_screen_y = self.transform_service.image_to_screen(center_x, center_y)
            current_angle_rad = math.atan2(y - center_screen_y, x - center_screen_x)
            delta_angle = math.degrees(current_angle_rad - self.action_info['start_angle_rad'])
            new_angle = self.action_info['original_angle'] + delta_angle
            new_data['angle'] = new_angle

        elif action_type in ['vertex_edit', 'edge_edit']:
            region_center = original_data.get('center') or get_bounding_box_center(original_data.get('lines', []))

            if action_type == 'vertex_edit':
                poly_idx = self.action_info['poly_index']
                vertex_idx = self.action_info['vertex_index']
                new_lines = editing_logic.calculate_new_vertices_on_drag(
                    original_vertices=[tuple(p) for p in original_data['lines'][poly_idx]],
                    dragged_vertex_index=vertex_idx,
                    new_mouse_position=(img_x, img_y),
                    angle=original_data.get('angle', 0),
                    center=region_center
                )
                new_data['lines'][poly_idx] = [list(p) for p in new_lines]
            
            elif action_type == 'edge_edit':
                poly_idx = self.action_info['poly_index']
                edge_idx = self.action_info['edge_index']
                new_lines = editing_logic.calculate_new_edge_on_drag(
                    original_vertices=[tuple(p) for p in original_data['lines'][poly_idx]],
                    dragged_edge_index=edge_idx,
                    new_mouse_position=(img_x, img_y),
                    angle=original_data.get('angle', 0),
                    center=region_center
                )
                new_data['lines'][poly_idx] = [list(p) for p in new_lines]

            new_data['center'] = list(get_bounding_box_center(new_data['lines']))
        
        else:
            return None

        # Universal fix: Ensure all line coordinates are integers before returning.
        if new_data and 'lines' in new_data:
            for poly in new_data['lines']:
                for i, point in enumerate(poly):
                    poly[i] = [int(point[0]), int(point[1])]
        
        return new_data

    def on_pan_start(self, event):
        self.action_info = {'type': 'pan', 'start_x': event.x, 'start_y': event.y}
        self.canvas.config(cursor="fleur")

    def on_pan_drag(self, event):
        if self.action_info.get('type') != 'pan': return
        dx = event.x - self.action_info['start_x']
        dy = event.y - self.action_info['start_y']
        self.transform_service.pan(dx, dy)
        self.action_info['start_x'] = event.x
        self.action_info['start_y'] = event.y

    def on_pan_stop(self, event):
        if self.action_info.get('type') == 'pan':
            self.action_info = {}
            self.canvas.config(cursor="")

    def on_mouse_wheel(self, event):
        # On first wheel event, trigger the fast redraw
        if self._zoom_end_timer is None and self.on_zoom_start:
            self.on_zoom_start()

        # Debounce the zoom action
        if self._zoom_debounce_timer:
            self.canvas.after_cancel(self._zoom_debounce_timer)
        self._zoom_debounce_timer = self.canvas.after(50, lambda: self._perform_zoom(event))

        # Use a separate timer to detect the end of the zoom sequence
        if self._zoom_end_timer:
            self.canvas.after_cancel(self._zoom_end_timer)
        
        def _zoom_end_callback():
            self._zoom_end_timer = None
            if self.on_zoom_end:
                self.on_zoom_end()
        
        self._zoom_end_timer = self.canvas.after(200, _zoom_end_callback)

    def _perform_zoom(self, event):
        self._zoom_debounce_timer = None
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self.transform_service.zoom(factor, event.x, event.y)

    def is_on_segment(self, px, py, p1, p2, threshold=5):
        x1, y1 = p1; x2, y2 = p2
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(px - x1, py - y1) * self.transform_service.zoom_level < threshold
        t = ((px - x1) * dx + (py - y1) * dy) / (dx**2 + dy**2)
        t = max(0, min(1, t))
        closest_x, closest_y = x1 + t * dx, y1 + t * dy
        return math.hypot(px - closest_x, py - closest_y) * self.transform_service.zoom_level < threshold

    def is_point_in_region(self, x, y, region):
        if not isinstance(region, dict):
            return False
        angle = region.get('angle', 0)
        center = region.get('center') or get_bounding_box_center(region.get('lines', []))
        if angle != 0:
            x, y = self.rotate_point(x, y, -angle, center[0], center[1])
        for poly in region.get('lines', []):
            if self.is_point_in_polygon(x, y, poly):
                return True
        return False

    def is_point_in_polygon(self, x, y, poly):
        n = len(poly)
        inside = False
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y) and y <= max(p1y, p2y) and x <= max(p1x, p2x):
                if p1y != p2y:
                    xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if p1x == p2x or x <= xinters:
                    inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def rotate_point(self, x, y, angle, cx, cy):
        angle_rad = math.radians(angle)
        cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
        x_new = cx + (x - cx) * cos_a - (y - cy) * sin_a
        y_new = cy + (x - cx) * sin_a + (y - cy) * cos_a
        return x_new, y_new
