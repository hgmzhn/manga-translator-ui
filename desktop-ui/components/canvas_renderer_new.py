from PIL import Image, ImageTk
import copy
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import cv2
from manga_translator.utils import TextBlock
from manga_translator.rendering import resize_regions_to_font_size
from .text_renderer_backend import BackendTextRenderer
from services.transform_service import TransformService

class CanvasRenderer:
    def __init__(self, canvas, transform_service: TransformService):
        self.canvas = canvas
        self.transform_service = transform_service
        self.image = None
        self.tk_image = None
        self.text_renderer = BackendTextRenderer(self.canvas)
        self.text_blocks_cache: List[TextBlock] = []
        self.dst_points_cache: List[np.ndarray] = []
        self.inpainted_image = None
        self.inpainted_alpha = 0.0
        self.tk_inpainted_image = None
        self.refined_mask = None
        self.removed_mask = None
        self.show_removed_mask = False
        
        # 性能优化缓存
        self._resized_image_cache = {}
        self._last_zoom_level = None
        self._last_dimensions = None
        self._redraw_scheduled = False
        
        # 防抖定时器
        self._debounce_timer = None
        self.show_mask = True

    def set_image(self, image_path):
        if image_path is None:
            self.image = None
            self._clear_cache()
            self.redraw_all()
            return
            
        self.image = Image.open(image_path)
        self._clear_cache()  # 清除缓存因为图像已更改
        self.redraw_all()
    
    def _clear_cache(self):
        """清除图像缓存"""
        self._resized_image_cache.clear()
        self._last_zoom_level = None
        self._last_dimensions = None
    
    def redraw_debounced(self, delay=0.05, **kwargs):
        """防抖重绘 - 避免频繁重绘"""
        if self._debounce_timer:
            self._debounce_timer.cancel()
        
        import threading
        self._debounce_timer = threading.Timer(delay, lambda: self.redraw_all(**kwargs))
        self._debounce_timer.start()

    def set_inpainted_image(self, image):
        self.inpainted_image = image
        # 清除inpainted图像的缓存，确保显示新渲染的图像
        keys_to_remove = [key for key in self._resized_image_cache.keys() if key.startswith('inpaint_')]
        for key in keys_to_remove:
            del self._resized_image_cache[key]

    def set_inpainted_alpha(self, alpha):
        self.inpainted_alpha = alpha

    def set_refined_mask(self, mask):
        self.refined_mask = mask
        self.redraw_mask_overlay()

    def set_removed_mask(self, mask):
        self.removed_mask = mask

    def set_removed_mask_visibility(self, visible: bool):
        self.show_removed_mask = visible
        self.redraw_mask_overlay()

    def set_mask_visibility(self, visible: bool):
        self.show_mask = visible

    def fit_to_window(self, window_width, window_height):
        if not self.image:
            return

        img_width, img_height = self.image.size
        if img_width == 0 or img_height == 0:
            return

        scale_w = window_width / img_width
        scale_h = window_height / img_height
        zoom_level = min(scale_w, scale_h) * 0.95 # Add a little padding

        x_offset = (window_width - int(img_width * zoom_level)) / 2
        y_offset = (window_height - int(img_height * zoom_level)) / 2

        self.transform_service.set_transform(zoom_level, x_offset, y_offset)

    def redraw_mask_overlay(self):
        self.canvas.delete("mask_overlay")
        self.canvas.delete("removed_mask_overlay")
        
        # Draw refined mask in blue
        if self.refined_mask is not None and self.show_mask:
            zoom_level = self.transform_service.zoom_level
            x_offset = self.transform_service.x_offset
            y_offset = self.transform_service.y_offset
            new_width = int(self.image.width * zoom_level)
            new_height = int(self.image.height * zoom_level)

            if new_width > 0 and new_height > 0:
                mask_overlay = np.zeros((self.refined_mask.shape[0], self.refined_mask.shape[1], 4), dtype=np.uint8)
                mask_overlay[self.refined_mask > 0] = [0, 0, 255, 200]
                mask_pil = Image.fromarray(mask_overlay)
                resized_mask = mask_pil.resize((new_width, new_height), Image.NEAREST)
                self.tk_mask_image = ImageTk.PhotoImage(resized_mask)
                self.canvas.create_image(x_offset, y_offset, anchor="nw", image=self.tk_mask_image, tags="mask_overlay")

        # Draw removed mask in red
        if self.removed_mask is not None and self.show_removed_mask:
            zoom_level = self.transform_service.zoom_level
            x_offset = self.transform_service.x_offset
            y_offset = self.transform_service.y_offset
            new_width = int(self.image.width * zoom_level)
            new_height = int(self.image.height * zoom_level)

            if new_width > 0 and new_height > 0:
                removed_overlay = np.zeros((self.removed_mask.shape[0], self.removed_mask.shape[1], 4), dtype=np.uint8)
                removed_overlay[self.removed_mask > 0] = [255, 0, 0, 150]  # Red with transparency
                removed_pil = Image.fromarray(removed_overlay)
                resized_removed = removed_pil.resize((new_width, new_height), Image.NEAREST)
                self.tk_removed_mask_image = ImageTk.PhotoImage(resized_removed)
                self.canvas.create_image(x_offset, y_offset, anchor="nw", image=self.tk_removed_mask_image, tags="removed_mask_overlay")

    def redraw_all(self, regions=None, selected_indices=None, hide_indices=None, fast_mode=False, view_mode='normal', raw_mask=None, original_size=None, hyphenate: bool = True, line_spacing: float = None, disable_font_border: bool = False):
        self.canvas.delete("all")
        if not self.image:
            return

        zoom_level = self.transform_service.zoom_level
        x_offset = self.transform_service.x_offset
        y_offset = self.transform_service.y_offset

        new_width = int(self.image.width * zoom_level)
        new_height = int(self.image.height * zoom_level)
        
        if new_width <= 0 or new_height <= 0: return

        # 使用缓存的调整尺寸图像
        cache_key = f"main_{new_width}x{new_height}"
        current_dimensions = (new_width, new_height)
        
        # 检查是否需要重新生成缓存的图像
        if (cache_key not in self._resized_image_cache or 
            self._last_zoom_level != zoom_level or 
            self._last_dimensions != current_dimensions):
            
            # 生成新的调整尺寸图像
            resized_image = self.image.resize((new_width, new_height), Image.LANCZOS)
            self.tk_image = ImageTk.PhotoImage(resized_image)
            self._resized_image_cache[cache_key] = self.tk_image
            
            # 更新缓存状态
            self._last_zoom_level = zoom_level
            self._last_dimensions = current_dimensions
            
            # 限制缓存大小
            if len(self._resized_image_cache) > 5:
                # 移除最旧的缓存项
                oldest_key = next(iter(self._resized_image_cache))
                del self._resized_image_cache[oldest_key]
        else:
            # 使用缓存的图像
            self.tk_image = self._resized_image_cache[cache_key]

        self.canvas.create_image(x_offset, y_offset, anchor="nw", image=self.tk_image)

        # Draw inpainted image if available and alpha > 0 (优化类似)
        if self.inpainted_image and self.inpainted_alpha > 0:
            inpaint_cache_key = f"inpaint_{new_width}x{new_height}_{self.inpainted_alpha}"
            
            if inpaint_cache_key not in self._resized_image_cache:
                # 调整修复图像大小
                resized_inpainted = self.inpainted_image.resize((new_width, new_height), Image.LANCZOS)
                # 创建透明版本
                alpha_img = resized_inpainted.copy()
                alpha_img.putalpha(int(self.inpainted_alpha * 255))
                self.tk_inpainted_image = ImageTk.PhotoImage(alpha_img)
                self._resized_image_cache[inpaint_cache_key] = self.tk_inpainted_image
            else:
                self.tk_inpainted_image = self._resized_image_cache[inpaint_cache_key]
            
            self.canvas.create_image(x_offset, y_offset, anchor="nw", image=self.tk_inpainted_image)

        if view_mode == 'mask':
            # Only redraw the full mask overlay when the zoom action is finished (not in fast_mode)
            if not fast_mode:
                self.redraw_mask_overlay()
        else:
            # NORMAL VIEW MODE
            if regions:
                self.text_renderer.draw_regions(
                    self.text_blocks_cache, 
                    self.dst_points_cache, 
                    selected_indices, 
                    self.transform_service, 
                    hide_indices=hide_indices,
                    fast_mode=fast_mode,
                    hyphenate=hyphenate,
                    line_spacing=line_spacing,
                    disable_font_border=disable_font_border
                )

        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def recalculate_render_data(self, regions: List[Dict[str, Any]], render_config: Dict[str, Any] = None):
        """Performs the expensive calculation and caches the result."""
        if not self.image or regions is None:
            self.text_blocks_cache = []
            self.dst_points_cache = []
            return

        render_config = render_config or {}

        text_blocks = []
        for region_dict in regions:
            constructor_args = region_dict.copy()
            # Ensure 'lines' is a numpy array, as expected by the backend logic
            if 'lines' in constructor_args and isinstance(constructor_args['lines'], list):
                constructor_args['lines'] = np.array(constructor_args['lines'])

            # Convert UI color format to TextBlock format
            if 'font_color' in constructor_args:
                # Convert hex color string to RGB tuple
                hex_color = constructor_args.pop('font_color', '#FFFFFF')
                if hex_color.startswith('#') and len(hex_color) == 7:
                    try:
                        r = int(hex_color[1:3], 16)
                        g = int(hex_color[3:5], 16) 
                        b = int(hex_color[5:7], 16)
                        constructor_args['fg_color'] = (r, g, b)
                    except ValueError:
                        constructor_args['fg_color'] = (255, 255, 255)  # 默认白色
                else:
                    constructor_args['fg_color'] = (255, 255, 255)  # 默认白色
                    
            if 'fg_colors' in constructor_args: constructor_args['fg_color'] = constructor_args.pop('fg_colors')
            if 'bg_colors' in constructor_args: constructor_args['bg_color'] = constructor_args.pop('bg_colors')
            try:
                text_block = TextBlock(**constructor_args)
                text_blocks.append(text_block)
            except Exception:
                text_blocks.append(None)

        try:
            image_np = np.array(self.image.convert("RGB"))
            
            # Create a valid Config object to pass to the backend function
            from manga_translator.config import Config, RenderConfig
            
            # The render_config dict comes from the UI. We use it to create a RenderConfig object.
            # Note: The original code had a typo 'font_size_fixed'. The correct attribute in RenderConfig is 'font_size'.
            # We will handle this by checking for both for compatibility.
            if 'font_size_fixed' in render_config and 'font_size' not in render_config:
                render_config['font_size'] = render_config.pop('font_size_fixed')

            config = Config(render=RenderConfig(**render_config))

            # Re-enabled with precision fixes to prevent visual displacement
            self.dst_points_cache = resize_regions_to_font_size(image_np, text_blocks, config)
            self.text_blocks_cache = text_blocks # Cache the text blocks as they are modified by the function
        except Exception as e:
            print(f"[DEBUG] Error during resize_regions_to_font_size: {e}")
            import traceback
            traceback.print_exc() # Print full traceback for better debugging
            self.dst_points_cache = [tb.min_rect if tb else None for tb in text_blocks]
            self.text_blocks_cache = text_blocks

    def draw_preview(self, polygons, color="cyan"):
        """
        Draws a lightweight preview on the canvas, typically during a drag operation.
        Clears any previous preview.
        `polygons` is a list of polygons, where each polygon is a list of (x, y) image coordinates.
        """
        # Delete previous preview items
        self.canvas.delete("preview")

        if not polygons:
            return

        for poly_coords in polygons:
            # Convert image coordinates to screen coordinates
            screen_poly = []
            for x, y in poly_coords:
                sx, sy = self.transform_service.image_to_screen(x, y)
                screen_poly.extend([sx, sy])
            
            if len(screen_poly) > 2:
                self.canvas.create_polygon(
                    screen_poly,
                    outline=color, 
                    fill="",
                    width=2,
                    tags="preview"
                )

    def draw_mask_preview(self, points: List[Tuple[int, int]], brush_size: int, tool: str):
        self.canvas.delete("mask_preview")
        if not points:
            return

        color = "blue" if tool == "画笔" else "black"
        for i in range(len(points) - 1):
            p1 = self.transform_service.image_to_screen(points[i][0], points[i][1])
            p2 = self.transform_service.image_to_screen(points[i+1][0], points[i+1][1])
            self.canvas.create_line(p1, p2, fill=color, width=brush_size, tags="mask_preview", capstyle="round", joinstyle="round")
