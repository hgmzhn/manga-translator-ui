import customtkinter as ctk
from components.canvas_renderer_new import CanvasRenderer
from components.mouse_event_handler_new import MouseEventHandler
from services.transform_service import TransformService
from typing import Callable, List, Dict, Any, Tuple

class CanvasFrame(ctk.CTkFrame):
    def __init__(self, parent, transform_service: TransformService, 
                 on_region_selected: Callable[[List[int]], None] = None, 
                 on_region_moved: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_resized: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_rotated: Callable[[int, Dict[str, Any], Dict[str, Any]], None] = None, 
                 on_region_created: Callable[[Dict[str, Any]], None] = None, 
                 on_geometry_added: Callable[[int, List[List[float]]], None] = None,
                 on_mask_draw_preview: Callable[[List[Tuple[int, int]]], None] = None,
                 on_mask_edit_start: Callable[[], None] = None,
                 on_mask_edit_end: Callable[[List[Tuple[int, int]]], None] = None):
        super().__init__(parent)
        self.transform_service = transform_service
        self.on_region_selected = on_region_selected
        self.on_region_moved = on_region_moved
        self.on_region_resized = on_region_resized
        self.on_region_rotated = on_region_rotated
        self.on_region_created = on_region_created
        self.on_geometry_added = on_geometry_added
        self.on_mask_draw_preview = on_mask_draw_preview
        self.on_mask_edit_start = on_mask_edit_start
        self.on_mask_edit_end = on_mask_edit_end
        
        self.render_config = {}
        self.is_previewing = False
        self.view_mode = 'normal'
        self.raw_mask = None
        self.original_size = None
        self.inpainted_image = None
        self.inpainted_alpha = 0.0

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas = ctk.CTkCanvas(self, bg="#2B2B2B", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.x_scrollbar = ctk.CTkScrollbar(self, orientation="horizontal", command=self.canvas.xview)
        self.x_scrollbar.grid(row=1, column=0, sticky="ew")

        self.y_scrollbar = ctk.CTkScrollbar(self, orientation="vertical", command=self.canvas.yview)
        self.y_scrollbar.grid(row=0, column=1, sticky="ns")

        self.canvas.configure(xscrollcommand=self.x_scrollbar.set, yscrollcommand=self.y_scrollbar.set)

        self.renderer = CanvasRenderer(self.canvas, self.transform_service)
        self.regions = []
        self.selected_indices: List[int] = []
        self.mouse_handler = MouseEventHandler(self.canvas, self.regions, self.transform_service, 
                                             on_region_selected=self._on_region_selected, 
                                             on_region_moved=self._on_region_moved, 
                                             on_region_resized=self._on_region_resized,
                                             on_region_rotated=self._on_region_rotated, 
                                             on_region_created=self._on_region_created,
                                             on_draw_new_region_preview=self._on_draw_new_region_preview,
                                             on_geometry_added=self._on_geometry_added,
                                             on_drag_preview=self._on_drag_preview,
                                             on_zoom_start=self._on_zoom_start,
                                             on_zoom_end=self._on_zoom_end,
                                             on_mask_draw_preview=self.on_mask_draw_preview,
                                             on_mask_edit_start=self.on_mask_edit_start,
                                             on_mask_edit_end=self.on_mask_edit_end)

    def set_render_config(self, config: Dict[str, Any]):
        # print("--- TRACE: canvas_frame.set_render_config called ---")
        self.render_config = config
        self.redraw_canvas()

    def redraw_canvas(self, fast_mode=False, use_debounce=False):
        # print("--- TRACE: canvas_frame.redraw_canvas called ---")
        hyphenate = not self.render_config.get('no_hyphenation', False)
        line_spacing = self.render_config.get('line_spacing')
        
        disable_font_border = self.render_config.get('disable_font_border', False)

        redraw_kwargs = {
            'regions': self.regions,
            'selected_indices': self.selected_indices,
            'fast_mode': fast_mode,
            'view_mode': self.view_mode,
            'raw_mask': self.raw_mask,
            'original_size': self.original_size,
            'hyphenate': hyphenate,
            'line_spacing': line_spacing,
            'disable_font_border': disable_font_border
        }
        
        if use_debounce and not fast_mode:
            # 使用防抖重绘，适用于频繁的交互操作
            self.renderer.redraw_debounced(**redraw_kwargs)
        else:
            # 立即重绘，适用于重要的状态变化
            self.renderer.redraw_all(**redraw_kwargs)

    def load_image(self, image_path):
        self.renderer.set_image(image_path)
        self.redraw_canvas()
    
    def clear_image(self):
        """清空画布中的图片和相关状态"""
        self.renderer.set_image(None)
        self.regions = []
        self.raw_mask = None
        self.original_size = None
        self.inpainted_image = None
        self.inpainted_alpha = 0.0
        self.mouse_handler.regions = []
        self.redraw_canvas()

    def set_regions(self, regions):
        self.regions = regions
        self.mouse_handler.regions = regions
        self.renderer.recalculate_render_data(regions, self.render_config)
        self.redraw_canvas()

    def set_mask(self, mask):
        self.raw_mask = mask
        self.redraw_canvas()

    def set_original_size(self, size):
        self.original_size = size
        self.redraw_canvas()

    def set_refined_mask(self, mask):
        self.renderer.set_refined_mask(mask)
        self.redraw_canvas()

    def set_mask_visibility(self, visible: bool):
        self.renderer.set_mask_visibility(visible)
        self.redraw_canvas()

    def set_view_mode(self, mode):
        self.view_mode = mode
        self.redraw_canvas()

    def set_inpainted_image(self, image):
        self.inpainted_image = image
        self.renderer.set_inpainted_image(image)
        self.redraw_canvas()  # 添加重绘调用以立即显示新的渲染图像

    def set_inpainted_alpha(self, alpha):
        self.inpainted_alpha = alpha
        self.renderer.set_inpainted_alpha(alpha)
        self.redraw_canvas()

    def _on_region_selected(self, indices: List[int]):
        self.selected_indices = set(indices)
        self.redraw_canvas()
        if self.on_region_selected:
            self.on_region_selected(indices)

    def _on_region_moved(self, index, old_region_data, new_region_data):
        print(f"--- CANVAS_FRAME: Committing MOVE for index {index} ---")
        print(f"--- UNDO_DEBUG: OLD_DATA: {old_region_data}")
        print(f"--- UNDO_DEBUG: NEW_DATA: {new_region_data}")
        self.is_previewing = False
        self.regions[index] = new_region_data
        # Directly trigger recalculation and redraw to ensure immediate UI update.
        self.renderer.recalculate_render_data(self.regions, self.render_config)
        self.redraw_canvas()
        if self.on_region_moved:
            self.on_region_moved(index, old_region_data, new_region_data)

    def _on_region_resized(self, index, old_region_data, new_region_data):
        print(f"--- CANVAS_FRAME: Committing RESIZE for index {index} ---")
        print(f"--- UNDO_DEBUG: OLD_DATA: {old_region_data}")
        print(f"--- UNDO_DEBUG: NEW_DATA: {new_region_data}")
        self.is_previewing = False
        self.regions[index] = new_region_data
        # Directly trigger recalculation and redraw to ensure immediate UI update.
        self.renderer.recalculate_render_data(self.regions, self.render_config)
        self.redraw_canvas()
        if self.on_region_resized:
            self.on_region_resized(index, old_region_data, new_region_data)

    def _on_region_rotated(self, index, old_region_data, new_region_data):
        print(f"--- CANVAS_FRAME: Committing ROTATE for index {index} ---")
        print(f"--- UNDO_DEBUG: OLD_DATA: {old_region_data}")
        print(f"--- UNDO_DEBUG: NEW_DATA: {new_region_data}")
        self.is_previewing = False
        self.regions[index] = new_region_data
        # Directly trigger recalculation and redraw to ensure immediate UI update.
        self.renderer.recalculate_render_data(self.regions, self.render_config)
        self.redraw_canvas()
        if self.on_region_rotated:
            self.on_region_rotated(index, old_region_data, new_region_data)

    def _on_region_created(self, new_region):
        if self.on_region_created:
            self.on_region_created(new_region)

    def _on_draw_new_region_preview(self, rect):
        if rect:
            x0, y0, x1, y1 = rect
            poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            self.renderer.draw_preview([poly])
        else:
            self.renderer.draw_preview(None)

    def _on_drag_preview(self, polygons):
        if polygons and not self.is_previewing:
            self.is_previewing = True
            self.renderer.redraw_all(self.regions, self.selected_indices)
        
        self.renderer.draw_preview(polygons)

    def _on_geometry_added(self, region_index, new_polygon):
        if self.on_geometry_added:
            self.on_geometry_added(region_index, new_polygon)

    def draw_mask_preview(self, points: List[Tuple[int, int]], brush_size: int, tool: str):
        self.renderer.draw_mask_preview(points, brush_size, tool)

    def set_text_visibility(self, visible: bool):
        self.renderer.text_renderer.set_text_visibility(visible)
        self.redraw_canvas()

    def set_boxes_visibility(self, visible: bool):
        self.renderer.text_renderer.set_boxes_visibility(visible)
        self.redraw_canvas()

    def toggle_boxes_visibility(self):
        self.renderer.text_renderer.toggle_boxes_visibility()
        self.redraw_canvas()

    def fit_to_window(self):
        self.renderer.fit_to_window(self.canvas.winfo_width(), self.canvas.winfo_height())
        self.redraw_canvas()

    def _on_zoom_start(self):
        self.redraw_canvas(fast_mode=True)

    def _on_zoom_end(self):
        self.redraw_canvas(fast_mode=False)