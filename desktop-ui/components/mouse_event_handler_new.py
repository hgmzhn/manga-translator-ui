import customtkinter as ctk
import numpy as np
import cv2
import copy
import math
import editing_logic
from services.transform_service import TransformService
from typing import Callable, List, Dict, Any, Set, Tuple, Optional

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

    def _get_rotation_handle_world_pos(self, region: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        """计算旋转手柄的世界坐标位置，确保与渲染一致"""
        if not region or 'lines' not in region or not region['lines']:
            return None
        
        angle = region.get('angle', 0)
        center = region.get('center') 
        if not center:
            all_model_points = [p for poly in region['lines'] for p in poly]
            center = editing_logic.get_polygon_center(all_model_points) if all_model_points else (0, 0)
        
        # 计算模型坐标中所有点的边界
        all_model_points = [p for poly in region['lines'] for p in poly]
        if not all_model_points: 
            return None
            
        min_y = min(p[1] for p in all_model_points)
        max_y = max(p[1] for p in all_model_points)
        unrotated_height = max_y - min_y
        
        # 手柄偏移量：在模型坐标中向上偏移
        handle_y_offset = -(unrotated_height / 2.0 + 30.0)
        
        # 将偏移向量旋转，然后加到中心点上得到世界坐标
        offset_x_rot, offset_y_rot = editing_logic.rotate_point(0, handle_y_offset, angle, 0, 0)
        handle_x = center[0] + offset_x_rot
        handle_y = center[1] + offset_y_rot
        
        return float(handle_x), float(handle_y)

    def _get_hit_target(self, event: Any) -> Optional[Dict[str, Any]]:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)

        if len(self.selected_indices) == 1:
            region_index = list(self.selected_indices)[0]
            region = self.regions[region_index]
            if not isinstance(region, dict): return None

            # 最高优先级：原有的旋转手柄
            handle_world_pos = self._get_rotation_handle_world_pos(region)
            if handle_world_pos:
                handle_screen_x, handle_screen_y = self.transform_service.image_to_screen(*handle_world_pos)
                if math.hypot(x - handle_screen_x, y - handle_screen_y) < 10:
                    return {'type': 'rotate', 'region_index': region_index}

            # 高优先级：原有的顶点和边编辑
            angle = region.get('angle', 0)
            center = region.get('center') or editing_logic.get_polygon_center([p for poly in region.get('lines', []) for p in poly])
            world_coords_polygons = [[editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly] for poly in region.get('lines', [])]

            for poly_idx, poly in enumerate(world_coords_polygons):
                for vertex_idx, (vx, vy) in enumerate(poly):
                    if math.hypot(img_x - vx, img_y - vy) * self.transform_service.zoom_level < 10:
                        return {'type': 'vertex_edit', 'region_index': region_index, 'poly_index': poly_idx, 'vertex_index': vertex_idx}

            for poly_idx, poly in enumerate(world_coords_polygons):
                for edge_idx in range(len(poly)):
                    if self.is_on_segment(img_x, img_y, poly[edge_idx], poly[(edge_idx + 1) % len(poly)]):
                        return {'type': 'edge_edit', 'region_index': region_index, 'poly_index': poly_idx, 'edge_index': edge_idx}

            # 低优先级：白色外框手柄（只有在没有命中原有手柄时才检测）
            white_frame_handle_hit = self._detect_white_frame_handle_hit(x, y, region_index)
            if white_frame_handle_hit:
                return white_frame_handle_hit

        # 检查是否在选中的区域内（只有按住Ctrl时才移动整个文本框）
        for i in self.selected_indices:
            if self.is_point_in_region(img_x, img_y, self.regions[i]):
                return {'type': 'move', 'region_index': i}  # 添加区域索引信息

        for i, region in reversed(list(enumerate(self.regions))):
            if i not in self.selected_indices and self.is_point_in_region(img_x, img_y, region):
                return {'type': 'select_new', 'region_index': i}

        return None
    
    def _detect_white_frame_handle_hit(self, screen_x, screen_y, region_index):
        """检测白色外框手柄点击"""
        try:
            # 检查是否点击了白色外框手柄
            handle_items = self.canvas.find_withtag("white_frame_handle")
            
            for handle_item in handle_items:
                # 检查手柄是否属于当前区域
                handle_tags = self.canvas.gettags(handle_item)
                if f"region_{region_index}" in handle_tags:
                    # 获取手柄的边界框
                    bbox = self.canvas.bbox(handle_item)
                    if bbox and bbox[0] <= screen_x <= bbox[2] and bbox[1] <= screen_y <= bbox[3]:
                        # 确定手柄类型
                        for tag in handle_tags:
                            if tag.startswith("corner_handle_"):
                                corner_idx = int(tag.split("_")[-1])
                                return {'type': 'white_frame_corner_edit', 'region_index': region_index, 'corner_index': corner_idx}
                            elif tag.startswith("edge_handle_"):
                                edge_idx = int(tag.split("_")[-1])
                                return {'type': 'white_frame_edge_edit', 'region_index': region_index, 'edge_index': edge_idx}
                            # 移除白色外框旋转手柄检测，使用原有的旋转手柄
                                
        except Exception as e:
            print(f"Error detecting white frame handle hit: {e}")
            
        return None
    
    def _get_preview_type(self, action_type):
        """根据操作类型确定预览类型"""
        if action_type in ['vertex_edit', 'edge_edit', 'move']:
            return "region_edit"
        elif action_type == 'rotate':
            return "region_edit"  # 旋转时显示蓝色框预览
        elif action_type in ['white_frame_corner_edit', 'white_frame_edge_edit']:
            return "region_edit"  # 白色框编辑时也显示蓝色框预览
        elif action_type == 'move_whole_textbox':
            return "region_edit"  # 整个文本框移动也使用蓝色框预览
        else:
            return "default"

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

        hit_target = self._get_hit_target(event)
        new_cursor = ""

        if hit_target:
            hit_type = hit_target.get('type')
            if hit_type == 'rotate':
                new_cursor = "exchange"
            elif hit_type == 'vertex_edit':
                new_cursor = "cross"
            elif hit_type == 'edge_edit':
                new_cursor = "sb_h_double_arrow"  # Placeholder, can be improved
            elif hit_type == 'white_frame_corner_edit':
                # 白色外框角点编辑 - 使用双向箭头
                new_cursor = "sizing"
            elif hit_type == 'white_frame_edge_edit':
                # 白色外框边编辑 - 使用合适的方向箭头
                new_cursor = "sb_h_double_arrow"  # 可以根据边的方向改进
            elif hit_type in ['move', 'select_new']:
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
        self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                outline="white", fill="black", width=1, tags="brush_cursor")

    def on_left_click(self, event):
        self.canvas.focus_set()
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

        hit_target = self._get_hit_target(event)
        ctrl_pressed = (event.state & 0x4) != 0

        if not hit_target:
            if not ctrl_pressed:
                if self.selected_indices:
                    self.selected_indices.clear()
                    if self.on_region_selected:
                        self.on_region_selected([])
            self.action_info = {'type': 'pan_prepare'}
            return

        hit_type = hit_target.get('type')

        if hit_type == 'select_new':
            clicked_region_index = hit_target['region_index']
            if ctrl_pressed:
                self.selected_indices.symmetric_difference_update({clicked_region_index})
            else:
                self.selected_indices = {clicked_region_index}
            
            if self.on_region_selected:
                self.on_region_selected(list(self.selected_indices))
            
            # 如果按下Ctrl键，设置为整个文本框移动模式
            if ctrl_pressed:
                self.action_info = {
                    'type': 'move_whole_textbox', 
                    'start_x_img': img_x, 
                    'start_y_img': img_y, 
                    'original_data': [copy.deepcopy(self.regions[i]) for i in self.selected_indices],
                    'ctrl_pressed': True
                }
            else:
                # 普通选择，不设置移动操作
                self.action_info = {'type': 'selected'}

        elif hit_type in ['rotate', 'vertex_edit', 'edge_edit', 'white_frame_corner_edit', 'white_frame_edge_edit']:
            region_index = hit_target['region_index']
            region = self.regions[region_index]
            self.action_info = {
                'type': hit_type,
                'original_data': copy.deepcopy(region),
                'start_x_img': img_x,
                'start_y_img': img_y,
                **hit_target
            }
            if hit_type == 'rotate':
                center = region.get('center') or editing_logic.get_polygon_center([p for poly in region.get('lines', []) for p in poly])
                center_screen_x, center_screen_y = self.transform_service.image_to_screen(center[0], center[1])
                self.action_info['center_x'] = center[0]
                self.action_info['center_y'] = center[1]
                self.action_info['start_angle_rad'] = math.atan2(y - center_screen_y, x - center_screen_x)
                self.action_info['original_angle'] = region.get('angle', 0)

        elif hit_type == 'move':
            # 检查是否按住Ctrl键来决定移动模式
            if ctrl_pressed:
                # Ctrl+拖拽：移动整个文本框
                self.action_info = {
                    'type': 'move_whole_textbox', 
                    'start_x_img': img_x, 
                    'start_y_img': img_y, 
                    'original_data': [copy.deepcopy(self.regions[i]) for i in self.selected_indices],
                    'ctrl_pressed': True
                }
            else:
                # 普通拖拽：只移动单个区域
                region_index = hit_target.get('region_index')
                if region_index is not None:
                    self.action_info = {
                        'type': 'move', 
                        'start_x_img': img_x, 
                        'start_y_img': img_y, 
                        'original_data': copy.deepcopy(self.regions[region_index]),
                        'region_index': region_index
                    }

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

        if action_type == 'draw':
            if self.on_draw_new_region_preview:
                x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
                img_x, img_y = self.transform_service.screen_to_image(x, y)
                start_x, start_y = self.action_info['start_x'], self.action_info['start_y']
                rect = [min(start_x, img_x), min(start_y, img_y), max(start_x, img_x), max(start_y, img_y)]
                self.on_draw_new_region_preview(rect)
            return

        if action_type == 'geometry_edit':
            if len(self.selected_indices) == 1:
                x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
                img_x, img_y = self.transform_service.screen_to_image(x, y)
                start_x, start_y = self.action_info['start_x'], self.action_info['start_y']
                region_index = list(self.selected_indices)[0]
                region_data = self.regions[region_index]
                angle = region_data.get('angle', 0)
                new_poly_world = editing_logic.calculate_rectangle_from_diagonal(
                    start_point=(start_x, start_y),
                    end_point=(img_x, img_y),
                    angle_deg=angle
                )
                self.on_drag_preview([new_poly_world])
            return

        if action_type == 'pan':
            self.on_pan_drag(event)
            return

        if 'original_data' not in self.action_info:
            return

        # 实时预览：跟随鼠标移动，无频率限制
        
        # 添加预览效果但不实时渲染文本
        new_data = self._get_drag_preview_data(event)
        if new_data and self.on_drag_preview:
            preview_polygons = []
            angle = new_data.get('angle', 0)
            lines = new_data.get('lines', [])
            center = new_data.get('center')
            
            # 优化：避免重复计算中心点
            if not center:
                if hasattr(self.action_info, '_cached_center'):
                    center = self.action_info._cached_center
                else:
                    all_points = [tuple(p) for poly in lines for p in poly]
                    center = editing_logic.get_polygon_center(all_points)
                    self.action_info._cached_center = center

            # 优化：减少旋转计算
            if angle != 0:
                # 只对预览的多边形进行旋转计算
                for poly in lines:
                    rotated_poly = []
                    for p in poly:
                        rotated_point = editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1])
                        rotated_poly.append(rotated_point)
                    preview_polygons.append(rotated_poly)
            else:
                preview_polygons.extend(lines)
            
            # 根据操作类型确定预览类型
            preview_type = self._get_preview_type(action_type)
            
            # 传递预览数据和预览类型
            if hasattr(self.on_drag_preview, '__code__') and self.on_drag_preview.__code__.co_argcount > 2:
                # 如果回调支持preview_type参数
                self.on_drag_preview(preview_polygons, preview_type)
            else:
                # 向后兼容
                self.on_drag_preview(preview_polygons)

    def on_drag_stop(self, event):
        if not self.is_dragging and self.action_info.get('type') != 'pan_prepare':
            self.action_info = {}
            return

        action_type = self.action_info.get('type')

        if action_type == 'mask_edit':
            if self.on_mask_edit_end:
                self.on_mask_edit_end(self.action_info['points'])
        
        if action_type in ['move', 'move_whole_textbox', 'rotate', 'vertex_edit', 'edge_edit', 'white_frame_corner_edit', 'white_frame_edge_edit']:
            if 'original_data' not in self.action_info: return
            if len(self.selected_indices) > 1 and action_type in ['move', 'move_whole_textbox']:
                 for i, original_data in enumerate(self.action_info['original_data']):
                    new_data = self._get_final_drag_data(original_data, event)
                    if self.on_region_moved:
                        self.on_region_moved(list(self.selected_indices)[i], original_data, new_data)
            elif action_type == 'move' and 'region_index' in self.action_info:
                # 单个区域移动
                region_index = self.action_info['region_index']
                new_region_data = self._get_final_drag_data(self.action_info['original_data'], event)
                if new_region_data and self.on_region_moved:
                    self.on_region_moved(region_index, self.action_info['original_data'], new_region_data)
            elif len(self.selected_indices) == 1:
                # 其他单个区域操作（旋转、编辑等）
                new_region_data = self._get_final_drag_data(self.action_info['original_data'], event)
                if new_region_data:
                    idx = list(self.selected_indices)[0]
                    old_data = self.action_info['original_data']
                    if isinstance(old_data, list):
                        old_data = old_data[0]

                    if action_type == 'move_whole_textbox' and self.on_region_moved: 
                        self.on_region_moved(idx, old_data, new_region_data)
                    elif action_type == 'rotate' and self.on_region_rotated: 
                        self.on_region_rotated(idx, old_data, new_region_data)
                    elif self.on_region_resized: 
                        self.on_region_resized(idx, old_data, new_region_data)
        
        elif action_type in ['draw', 'geometry_edit']:
            x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            img_x, img_y = self.transform_service.screen_to_image(x, y)
            start_x, start_y = self.action_info['start_x'], self.action_info['start_y']
            
            if abs(img_x - start_x) > 5 and abs(img_y - start_y) > 5:
                if action_type == 'draw' and self.on_region_created:
                    x0, y0, x1, y1 = min(start_x, img_x), min(start_y, img_y), max(start_x, img_x), max(start_y, img_y)
                    new_poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                    center_x, center_y = editing_logic.get_polygon_center(new_poly)
                    
                    new_region_data = {
                        'lines': [new_poly],
                        'texts': [''],
                        'text': '',
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
                    
                    new_poly_world = editing_logic.calculate_rectangle_from_diagonal(
                        start_point=(start_x, start_y),
                        end_point=(img_x, img_y),
                        angle_deg=angle
                    )
                    
                    self.on_geometry_added(region_index, new_poly_world)
            if self.on_draw_new_region_preview: self.on_draw_new_region_preview(None)
            self.set_mode('select')

        self.action_info = {}
        self.is_dragging = False
        if self.on_drag_preview: self.on_drag_preview(None)
        self._update_cursor(event)

    def _get_drag_preview_data(self, event):
        """预览专用：使用简化计算，避免复杂的cv2操作"""
        action_type = self.action_info.get('type')
        if not action_type: 
            return None
        
        original_data = self.action_info['original_data']
        if isinstance(original_data, list):
            original_data = original_data[0]
        
        new_data = copy.deepcopy(original_data)
        
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)

        # 简化的预览计算
        if action_type in ['move', 'move_whole_textbox']:
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
            # 区域编辑预览：使用完整计算（已经优化过）
            return self._get_final_drag_data(self.action_info['original_data'], event)
            
        elif action_type in ['white_frame_corner_edit', 'white_frame_edge_edit']:
            # 白框编辑预览：直接使用最终计算，确保预览更新
            return self._get_final_drag_data(self.action_info['original_data'], event)
        
        return new_data

    def _get_final_drag_data(self, original_data_in, event):
        action_type = self.action_info.get('type')
        if not action_type: return None
        
        original_data = original_data_in[0] if isinstance(original_data_in, list) else original_data_in
        new_data = copy.deepcopy(original_data)
        
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = self.transform_service.screen_to_image(x, y)

        if action_type in ['move', 'move_whole_textbox']:
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
            center = original_data.get('center')
            if not center:
                all_points = [p for poly in original_data.get('lines', []) for p in poly]
                center = editing_logic.get_polygon_center(all_points)

            angle = original_data.get('angle', 0)
            poly_idx = self.action_info['poly_index']
            
            # 第一步：将所有现有矩形转换为世界坐标（这是它们在屏幕上的真实位置）
            all_world_polygons = []
            for poly_model in original_data['lines']:
                poly_world = [editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly_model]
                all_world_polygons.append(poly_world)
            
            # 第二步：只修改被编辑的矩形的世界坐标
            original_poly_model = original_data['lines'][poly_idx]
            if action_type == 'vertex_edit':
                vertex_idx = self.action_info['vertex_index']
                anchor_vertex_model = original_poly_model[(vertex_idx + 2) % 4]
                anchor_point_world = editing_logic.rotate_point(anchor_vertex_model[0], anchor_vertex_model[1], angle, center[0], center[1])
                end_point_world = (img_x, img_y)
            else: # edge_edit
                # 边编辑：保持被拖拽边长度不变，改变垂直方向的距离
                edge_idx = self.action_info['edge_index']
                
                # 将原始矩形转换为世界坐标
                original_poly_world = [editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in original_poly_model]
                
                # 获取被拖拽边的两个顶点
                edge_p1 = original_poly_world[edge_idx]
                edge_p2 = original_poly_world[(edge_idx + 1) % 4]
                
                # 获取对边的两个顶点
                opposite_edge_idx = (edge_idx + 2) % 4
                opposite_p1 = original_poly_world[opposite_edge_idx]
                opposite_p2 = original_poly_world[(opposite_edge_idx + 1) % 4]
                
                # 计算边的方向向量和长度
                edge_vec = (edge_p2[0] - edge_p1[0], edge_p2[1] - edge_p1[1])
                edge_length = math.hypot(edge_vec[0], edge_vec[1])
                if edge_length > 0:
                    edge_unit = (edge_vec[0] / edge_length, edge_vec[1] / edge_length)
                else:
                    edge_unit = (1, 0)
                
                # 计算垂直于边的法向量
                edge_normal = (-edge_unit[1], edge_unit[0])
                
                # 计算鼠标到被拖拽边的投影距离
                edge_center = ((edge_p1[0] + edge_p2[0]) / 2, (edge_p1[1] + edge_p2[1]) / 2)
                to_mouse = (img_x - edge_center[0], img_y - edge_center[1])
                projection_distance = to_mouse[0] * edge_normal[0] + to_mouse[1] * edge_normal[1]
                
                # 构建新的矩形：对边保持不变，被拖拽边移动到新位置
                new_edge_p1 = (
                    edge_p1[0] + projection_distance * edge_normal[0],
                    edge_p1[1] + projection_distance * edge_normal[1]
                )
                new_edge_p2 = (
                    edge_p2[0] + projection_distance * edge_normal[0],
                    edge_p2[1] + projection_distance * edge_normal[1]
                )
                
                # 构建完整的新矩形顶点
                new_poly_world = [None] * 4
                new_poly_world[edge_idx] = new_edge_p1
                new_poly_world[(edge_idx + 1) % 4] = new_edge_p2
                new_poly_world[opposite_edge_idx] = opposite_p1
                new_poly_world[(opposite_edge_idx + 1) % 4] = opposite_p2
                
            # 对于顶点编辑，保持矩形的斜率不变，只改变大小
            if action_type == 'vertex_edit':
                vertex_idx = self.action_info['vertex_index']
                original_poly_world = all_world_polygons[poly_idx]
                
                # 获取对角点作为锚点
                anchor_idx = (vertex_idx + 2) % 4
                anchor_point = original_poly_world[anchor_idx]
                
                # 获取原矩形的两条邻边向量（保持这些方向不变）
                adj1_idx = (vertex_idx - 1 + 4) % 4
                adj2_idx = (vertex_idx + 1) % 4
                
                edge1_vec = (original_poly_world[adj1_idx][0] - anchor_point[0], 
                            original_poly_world[adj1_idx][1] - anchor_point[1])
                edge2_vec = (original_poly_world[adj2_idx][0] - anchor_point[0], 
                            original_poly_world[adj2_idx][1] - anchor_point[1])
                
                # 计算鼠标拖拽向量
                drag_vec = (img_x - anchor_point[0], img_y - anchor_point[1])
                
                # 将拖拽向量投影到两个边方向上，保持矩形的斜率
                edge1_len_sq = edge1_vec[0]**2 + edge1_vec[1]**2
                edge2_len_sq = edge2_vec[0]**2 + edge2_vec[1]**2
                
                if edge1_len_sq > 0 and edge2_len_sq > 0:
                    # 计算投影长度
                    proj1 = (drag_vec[0] * edge1_vec[0] + drag_vec[1] * edge1_vec[1]) / edge1_len_sq
                    proj2 = (drag_vec[0] * edge2_vec[0] + drag_vec[1] * edge2_vec[1]) / edge2_len_sq
                    
                    # 构建新的矩形，保持边的方向不变
                    new_adj1 = [anchor_point[0] + proj1 * edge1_vec[0], 
                                anchor_point[1] + proj1 * edge1_vec[1]]
                    new_adj2 = [anchor_point[0] + proj2 * edge2_vec[0], 
                                anchor_point[1] + proj2 * edge2_vec[1]]
                    new_drag = [anchor_point[0] + proj1 * edge1_vec[0] + proj2 * edge2_vec[0], 
                               anchor_point[1] + proj1 * edge1_vec[1] + proj2 * edge2_vec[1]]
                    
                    # 按顶点索引顺序构建新多边形
                    new_poly_world = [None] * 4
                    new_poly_world[anchor_idx] = anchor_point
                    new_poly_world[adj1_idx] = new_adj1
                    new_poly_world[adj2_idx] = new_adj2
                    new_poly_world[vertex_idx] = new_drag
                else:
                    # 备用方案
                    new_poly_world = original_poly_world
            
            # 用新编辑的矩形替换对应的世界坐标矩形
            all_world_polygons[poly_idx] = new_poly_world
            
            # 第三步：基于所有世界坐标重新计算新的中心点
            # 使用 cv2.minAreaRect 来确保与后端渲染一致
            all_vertices_world = [vertex for poly in all_world_polygons for vertex in poly]
            points_np = np.array(all_vertices_world, dtype=np.float32)
            min_area_rect = cv2.minAreaRect(points_np)
            new_center = min_area_rect[0]  # 返回 (center_x, center_y, width, height, angle)
            
            # 第四步：将所有世界坐标转换回新的模型坐标系
            # 这样可以确保世界坐标（屏幕位置）保持不变，只是改变了模型坐标和中心点
            new_lines_model = []
            for poly_world in all_world_polygons:
                poly_model = [
                    editing_logic.rotate_point(p[0], p[1], -angle, new_center[0], new_center[1])
                    for p in poly_world
                ]
                # 确保数据类型一致性，避免np.float64混用
                poly_model = [[float(p[0]), float(p[1])] for p in poly_model]
                new_lines_model.append(poly_model)
            
            new_data['lines'] = new_lines_model
            new_data['center'] = [float(new_center[0]), float(new_center[1])]
        
        elif action_type in ['white_frame_corner_edit', 'white_frame_edge_edit']:
            # 白色外框编辑逻辑 - 优化性能版本
            
            # 缓存计算结果，避免重复计算
            if '_white_frame_cache' not in self.action_info:
                center = original_data.get('center')
                if not center:
                    all_points = [p for poly in original_data.get('lines', []) for p in poly]
                    center = editing_logic.get_polygon_center(all_points)

                angle = original_data.get('angle', 0)
                
                # 缓存世界坐标转换结果
                all_world_polygons = []
                if angle != 0:
                    for poly_model in original_data['lines']:
                        poly_world = [editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly_model]
                        all_world_polygons.append(poly_world)
                else:
                    # 无旋转时直接复制，避免不必要的计算
                    all_world_polygons = [poly[:] for poly in original_data['lines']]
                
                # 计算边界框
                all_vertices_world = [vertex for poly in all_world_polygons for vertex in poly]
                min_x = min(p[0] for p in all_vertices_world)
                max_x = max(p[0] for p in all_vertices_world)
                min_y = min(p[1] for p in all_vertices_world)
                max_y = max(p[1] for p in all_vertices_world)
                
                # 缓存计算结果到字典中
                self.action_info['_white_frame_cache'] = {
                    'center': center,
                    'angle': angle,
                    'all_world_polygons': all_world_polygons,
                    'bounds': (min_x, max_x, min_y, max_y),
                    'padding': 40  # 增加到40像素
                }
            
            # 使用缓存的数据
            cache = self.action_info['_white_frame_cache']
            center = cache['center']
            angle = cache['angle']
            all_world_polygons = cache['all_world_polygons']
            min_x, max_x, min_y, max_y = cache['bounds']
            padding = cache['padding']
            
            # 构建原始白色外框
            original_white_frame = [
                [min_x - padding, min_y - padding],  # 左上
                [max_x + padding, min_y - padding],  # 右上  
                [max_x + padding, max_y + padding],  # 右下
                [min_x - padding, max_y + padding]   # 左下
            ]
            
            # 快速编辑白色外框
            if action_type == 'white_frame_corner_edit':
                corner_idx = self.action_info.get('corner_index', 0)
                anchor_corner_idx = (corner_idx + 2) % 4
                anchor_point_world = original_white_frame[anchor_corner_idx]
                end_point_world = (img_x, img_y)
                
                # 简化的矩形计算
                new_white_frame = editing_logic.calculate_rectangle_from_diagonal(
                    start_point=anchor_point_world,
                    end_point=end_point_world,
                    angle_deg=0
                )
                
            elif action_type == 'white_frame_edge_edit':
                edge_idx = self.action_info.get('edge_index', 0)
                edge_p1 = original_white_frame[edge_idx]
                edge_p2 = original_white_frame[(edge_idx + 1) % 4]
                
                # 简化边编辑计算
                if edge_idx == 0 or edge_idx == 2:  # 水平边
                    new_y = img_y
                    new_white_frame = [
                        [edge_p1[0], new_y if edge_idx == 0 else edge_p1[1]],
                        [edge_p2[0], new_y if edge_idx == 0 else edge_p2[1]],
                        [original_white_frame[2][0], original_white_frame[2][1] if edge_idx == 0 else new_y],
                        [original_white_frame[3][0], original_white_frame[3][1] if edge_idx == 0 else new_y]
                    ]
                else:  # 垂直边
                    new_x = img_x
                    new_white_frame = [
                        [original_white_frame[0][0] if edge_idx == 1 else new_x, original_white_frame[0][1]],
                        [new_x if edge_idx == 1 else original_white_frame[1][0], original_white_frame[1][1]],
                        [new_x if edge_idx == 1 else original_white_frame[2][0], original_white_frame[2][1]],
                        [original_white_frame[3][0] if edge_idx == 1 else new_x, original_white_frame[3][1]]
                    ]
            
            # 快速变换计算
            old_width = (max_x + padding) - (min_x - padding)
            old_height = (max_y + padding) - (min_y - padding)
            old_center_x = (min_x + max_x) / 2
            old_center_y = (min_y + max_y) / 2
            
            new_min_x = min(p[0] for p in new_white_frame)
            new_max_x = max(p[0] for p in new_white_frame)
            new_min_y = min(p[1] for p in new_white_frame)
            new_max_y = max(p[1] for p in new_white_frame)
            new_width = new_max_x - new_min_x
            new_height = new_max_y - new_min_y
            new_center_x = (new_min_x + new_max_x) / 2
            new_center_y = (new_min_y + new_max_y) / 2
            
            # 计算变换参数
            scale_x = new_width / old_width if old_width > 0 else 1.0
            scale_y = new_height / old_height if old_height > 0 else 1.0
            translate_x = new_center_x - old_center_x
            translate_y = new_center_y - old_center_y
            
            # 终极方法：先保持距离，再保持斜率
            new_world_polygons = []
            
            # 第一阶段：仿射变换保持相对位置（距离保持）
            transformed_polygons = []
            for poly_world in all_world_polygons:
                transformed_poly = []
                for point in poly_world:
                    rel_x = point[0] - old_center_x
                    rel_y = point[1] - old_center_y
                    new_x = new_center_x + rel_x * scale_x
                    new_y = new_center_y + rel_y * scale_y
                    transformed_poly.append([new_x, new_y])
                transformed_polygons.append(transformed_poly)
            
            # 第二阶段：矫正每个矩形的斜率（斜率保持）
            for i, (original_poly, transformed_poly) in enumerate(zip(all_world_polygons, transformed_polygons)):
                # 获取原始矩形的角度
                orig_points_np = np.array(original_poly, dtype=np.float32)
                orig_rect = cv2.minAreaRect(orig_points_np)
                original_angle = orig_rect[2]
                
                # 获取变换后矩形的参数
                trans_points_np = np.array(transformed_poly, dtype=np.float32)
                trans_rect = cv2.minAreaRect(trans_points_np)
                (trans_center_x, trans_center_y), (trans_width, trans_height), trans_angle = trans_rect
                
                # 重构矩形：保持变换后的中心和尺寸，但使用原始角度
                corrected_rect = ((trans_center_x, trans_center_y), (trans_width, trans_height), original_angle)
                corrected_poly = cv2.boxPoints(corrected_rect).tolist()
                new_world_polygons.append(corrected_poly)
            
            # 重新计算整体中心点
            all_new_vertices_world = [vertex for poly in new_world_polygons for vertex in poly]
            points_np = np.array(all_new_vertices_world, dtype=np.float32)
            min_area_rect = cv2.minAreaRect(points_np)
            final_new_center = min_area_rect[0]
            
            # 转换回模型坐标系
            new_lines_model = []
            for poly_world in new_world_polygons:
                poly_model = [
                    editing_logic.rotate_point(p[0], p[1], -angle, final_new_center[0], final_new_center[1])
                    for p in poly_world
                ]
                poly_model = [[float(p[0]), float(p[1])] for p in poly_model]
                new_lines_model.append(poly_model)
            
            new_data['lines'] = new_lines_model
            new_data['center'] = [float(final_new_center[0]), float(final_new_center[1])]
        
        else:
            return None
        
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
        if self._zoom_end_timer is None and self.on_zoom_start:
            self.on_zoom_start()

        if self._zoom_debounce_timer:
            self.canvas.after_cancel(self._zoom_debounce_timer)
        self._zoom_debounce_timer = self.canvas.after(50, lambda: self._perform_zoom(event))

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
        """检查点是否在区域内，使用世界坐标系以确保与渲染一致"""
        if not isinstance(region, dict):
            return False
        angle = region.get('angle', 0)
        center = region.get('center') or editing_logic.get_polygon_center([p for poly in region.get('lines', []) for p in poly])
        
        # 将模型坐标转换为世界坐标
        world_coords_polygons = []
        for poly in region.get('lines', []):
            world_poly = [editing_logic.rotate_point(p[0], p[1], angle, center[0], center[1]) for p in poly]
            world_coords_polygons.append(world_poly)
        
        # 在世界坐标系中检查点是否在多边形内
        for world_poly in world_coords_polygons:
            if self.is_point_in_polygon(x, y, world_poly):
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
