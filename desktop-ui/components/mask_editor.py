"""
蒙版编辑器
负责蒙版编辑模式、画笔工具和蒙版绘制功能
"""
import customtkinter as ctk
import numpy as np
import cv2
from typing import Optional, Tuple, Callable, Dict, Any
from PIL import Image
import logging
from manga_translator.utils.generic import imwrite_unicode


class MaskEditor:
    """蒙版编辑器"""
    
    def __init__(self, canvas: ctk.CTkCanvas):
        self.canvas = canvas
        
        # 蒙版数据
        self.mask_data: Optional[np.ndarray] = None
        self.image_size: Optional[Tuple[int, int]] = None
        
        # 编辑状态
        self.edit_mode: bool = False
        self.current_tool: str = "brush"  # brush, eraser
        self.brush_size: int = 10
        self.brush_hardness: float = 1.0
        
        # 绘制状态
        self.is_painting: bool = False
        self.last_paint_pos: Optional[Tuple[float, float]] = None
        
        # 显示设置
        self.zoom_level: float = 1.0
        
        # 回调函数
        self.callbacks: Dict[str, Callable] = {}
        
        # 历史管理（简单实现）
        self.mask_history = []
        self.history_index = -1
        self.max_history = 20
    
    def set_data_sources(self, mask_data: Optional[np.ndarray] = None, 
                        zoom_level: float = 1.0):
        """设置数据源"""
        if mask_data is not None:
            self.set_mask_data(mask_data)
        self.set_zoom_level(zoom_level)
    
    def set_mask_data(self, mask_data: Optional[np.ndarray]):
        """设置蒙版数据"""
        self.mask_data = mask_data
        if mask_data is not None:
            self._ensure_mask_format()
    
    def set_image_size(self, width: int, height: int):
        """设置图像尺寸"""
        self.image_size = (width, height)
        
        # 如果没有蒙版数据，创建空蒙版
        if self.mask_data is None:
            self.mask_data = np.zeros((height, width), dtype=np.uint8)
            print(f"创建空蒙版: {width}x{height}")
    
    def set_zoom_level(self, zoom_level: float):
        """设置缩放级别"""
        self.zoom_level = zoom_level
    
    def register_callback(self, event_name: str, callback: Callable):
        """注册回调函数"""
        self.callbacks[event_name] = callback
    
    def is_edit_mode(self) -> bool:
        """检查是否在编辑模式"""
        return self.edit_mode
    
    def toggle_edit_mode(self):
        """切换编辑模式"""
        self.edit_mode = not self.edit_mode
        
        if self.edit_mode:
            self._enter_edit_mode()
        else:
            self._exit_edit_mode()
        
        self._execute_callback('mode_changed', self.edit_mode)
    
    def _enter_edit_mode(self):
        """进入编辑模式"""
        print("进入蒙版编辑模式")
        self._init_mask_for_editing()
        self._setup_edit_cursor()
    
    def _exit_edit_mode(self):
        """退出编辑模式"""
        print("退出蒙版编辑模式")
        self._cleanup_edit_cursor()
        self.is_painting = False
        self.last_paint_pos = None
    
    def _init_mask_for_editing(self):
        """初始化蒙版用于编辑"""
        if self.mask_data is None and self.image_size:
            w, h = self.image_size
            self.mask_data = np.zeros((h, w), dtype=np.uint8)
            print(f"创建空蒙版: {w}x{h}")
        elif self.mask_data is not None:
            self._ensure_mask_format()
    
    def _ensure_mask_format(self):
        """确保蒙版格式正确"""
        if self.mask_data.ndim == 3:
            # 多通道转为单通道
            self.mask_data = cv2.cvtColor(self.mask_data, cv2.COLOR_RGB2GRAY)
        self.mask_data = self.mask_data.astype(np.uint8)
    
    def _setup_edit_cursor(self):
        """设置编辑游标"""
        self.canvas.configure(cursor="crosshair")
    
    def _cleanup_edit_cursor(self):
        """清理编辑游标"""
        self.canvas.configure(cursor="")
        self.canvas.delete("brush_cursor")
    
    def set_tool(self, tool: str):
        """设置工具"""
        if tool in ["brush", "eraser"]:
            self.current_tool = tool
            print(f"切换到工具: {tool}")
            self._execute_callback('tool_changed', tool)
    
    def set_brush_size(self, size: int):
        """设置画笔大小"""
        self.brush_size = max(1, min(100, size))
        self._execute_callback('brush_size_changed', self.brush_size)
    
    def set_brush_hardness(self, hardness: float):
        """设置画笔硬度"""
        self.brush_hardness = max(0.0, min(1.0, hardness))
        self._execute_callback('brush_hardness_changed', self.brush_hardness)
    
    def update_brush_cursor(self, x: float, y: float):
        """更新画笔游标显示"""
        if not self.edit_mode:
            return
            
        # 删除旧游标
        self.canvas.delete("brush_cursor")
        
        # 绘制新游标
        radius = self.brush_size * self.zoom_level / 2
        color = "red" if self.current_tool == "brush" else "blue"
        
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            outline=color, width=2, tags="brush_cursor"
        )
    
    def start_painting(self, event):
        """开始绘制"""
        if not self.edit_mode or self.mask_data is None:
            return False
        
        self.is_painting = True
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = x / self.zoom_level, y / self.zoom_level
        self.last_paint_pos = (img_x, img_y)
        
        # 保存历史状态
        self._save_mask_state()
        
        # 绘制单点
        self._paint_at_position(img_x, img_y)
        return True
    
    def continue_painting(self, event):
        """继续绘制"""
        if not self.edit_mode or not self.is_painting or self.mask_data is None:
            return False
        
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        img_x, img_y = x / self.zoom_level, y / self.zoom_level
        
        # 从上次位置到当前位置绘制线条
        if self.last_paint_pos:
            self._paint_line(self.last_paint_pos, (img_x, img_y))
        
        self.last_paint_pos = (img_x, img_y)
        
        # 触发重绘
        self._execute_callback('request_redraw')
        return True
    
    def stop_painting(self):
        """停止绘制"""
        if not self.edit_mode:
            return False
            
        self.is_painting = False
        self.last_paint_pos = None
        
        # 触发修改事件
        self._execute_callback('mask_modified')
        return True
    
    def _paint_at_position(self, img_x: float, img_y: float):
        """在指定位置绘制"""
        if self.mask_data is None:
            return
        
        h, w = self.mask_data.shape
        
        # 边界检查
        if img_x < 0 or img_y < 0 or img_x >= w or img_y >= h:
            return
        
        brush_value = 255 if self.current_tool == "brush" else 0
        radius = max(1, self.brush_size // 2)
        
        # 使用opencv绘制圆形
        cv2.circle(self.mask_data, (int(img_x), int(img_y)), radius, brush_value, -1)
    
    def _paint_line(self, start_pos: Tuple[float, float], end_pos: Tuple[float, float]):
        """在两点之间绘制线条"""
        if self.mask_data is None:
            return
        
        start_x, start_y = int(start_pos[0]), int(start_pos[1])
        end_x, end_y = int(end_pos[0]), int(end_pos[1])
        
        brush_value = 255 if self.current_tool == "brush" else 0
        thickness = max(1, self.brush_size)
        
        # 绘制线条
        cv2.line(self.mask_data, (start_x, start_y), (end_x, end_y), brush_value, thickness)
    
    def clear_mask(self):
        """清空蒙版"""
        if self.mask_data is not None:
            self._save_mask_state()
            self.mask_data.fill(0)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print("蒙版已清空")
    
    def fill_mask(self):
        """填满蒙版"""
        if self.mask_data is not None:
            self._save_mask_state()
            self.mask_data.fill(255)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print("蒙版已填满")
    
    def invert_mask(self):
        """反转蒙版"""
        if self.mask_data is not None:
            self._save_mask_state()
            self.mask_data = 255 - self.mask_data
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print("蒙版已反转")
    
    def flood_fill(self, x: float, y: float, tolerance: int = 10):
        """漫水填充"""
        if self.mask_data is None:
            return
        
        h, w = self.mask_data.shape
        if x < 0 or y < 0 or x >= w or y >= h:
            return
        
        self._save_mask_state()
        
        # 创建种子点
        seed_point = (int(x), int(y))
        fill_value = 255 if self.current_tool == "brush" else 0
        
        # 执行漫水填充
        cv2.floodFill(self.mask_data, None, seed_point, fill_value, 
                      loDiff=tolerance, upDiff=tolerance)
        
        self._execute_callback('mask_modified')
        self._execute_callback('request_redraw')
        print(f"漫水填充完成: {seed_point}")
    
    def auto_threshold(self, threshold: int = 128):
        """自动阈值化"""
        if self.mask_data is not None:
            self._save_mask_state()
            _, self.mask_data = cv2.threshold(self.mask_data, threshold, 255, cv2.THRESH_BINARY)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print(f"自动阈值化完成: {threshold}")
    
    def dilate_mask(self, iterations: int = 1):
        """膨胀操作"""
        if self.mask_data is not None:
            self._save_mask_state()
            kernel = np.ones((3, 3), np.uint8)
            self.mask_data = cv2.dilate(self.mask_data, kernel, iterations=iterations)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print(f"膨胀操作完成: {iterations}次")
    
    def erode_mask(self, iterations: int = 1):
        """腐蚀操作"""
        if self.mask_data is not None:
            self._save_mask_state()
            kernel = np.ones((3, 3), np.uint8)
            self.mask_data = cv2.erode(self.mask_data, kernel, iterations=iterations)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print(f"腐蚀操作完成: {iterations}次")
    
    def smooth_mask(self, kernel_size: int = 5):
        """平滑蒙版"""
        if self.mask_data is not None:
            self._save_mask_state()
            self.mask_data = cv2.GaussianBlur(self.mask_data, (kernel_size, kernel_size), 0)
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print(f"平滑操作完成: 核大小{kernel_size}")
    
    def _save_mask_state(self):
        """保存蒙版状态到历史"""
        if self.mask_data is None:
            return
        
        # 清除历史索引之后的状态
        self.mask_history = self.mask_history[:self.history_index + 1]
        
        # 添加当前状态
        self.mask_history.append(self.mask_data.copy())
        self.history_index += 1
        
        # 限制历史大小
        if len(self.mask_history) > self.max_history:
            self.mask_history.pop(0)
            self.history_index -= 1
    
    def undo_mask(self):
        """撤销蒙版操作"""
        if self.history_index > 0:
            self.history_index -= 1
            self.mask_data = self.mask_history[self.history_index].copy()
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print("蒙版操作已撤销")
            return True
        return False
    
    def redo_mask(self):
        """重做蒙版操作"""
        if self.history_index < len(self.mask_history) - 1:
            self.history_index += 1
            self.mask_data = self.mask_history[self.history_index].copy()
            self._execute_callback('mask_modified')
            self._execute_callback('request_redraw')
            print("蒙版操作已重做")
            return True
        return False
    
    def can_undo(self) -> bool:
        """是否可以撤销"""
        return self.history_index > 0
    
    def can_redo(self) -> bool:
        """是否可以重做"""
        return self.history_index < len(self.mask_history) - 1
    
    def load_mask_from_file(self, file_path: str) -> bool:
        """从文件加载蒙版"""
        try:
            mask_image = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
            if mask_image is not None:
                self._save_mask_state()
                self.mask_data = mask_image
                self._execute_callback('mask_modified')
                self._execute_callback('request_redraw')
                print(f"蒙版已从文件加载: {file_path}")
                return True
        except Exception as e:
            print(f"加载蒙版文件失败: {e}")
        return False
    
    def save_mask_to_file(self, file_path: str) -> bool:
        """保存蒙版到文件"""
        if self.mask_data is None:
            return False
        
        try:
            logger = logging.getLogger(__name__)
            imwrite_unicode(file_path, self.mask_data, logger)
            print(f"蒙版已保存到文件: {file_path}")
            return True
        except Exception as e:
            print(f"保存蒙版文件失败: {e}")
        return False
    
    def get_mask_data(self) -> Optional[np.ndarray]:
        """获取蒙版数据"""
        return self.mask_data.copy() if self.mask_data is not None else None
    
    def get_mask_statistics(self) -> Dict[str, Any]:
        """获取蒙版统计信息"""
        if self.mask_data is None:
            return {}
        
        total_pixels = self.mask_data.size
        masked_pixels = np.count_nonzero(self.mask_data)
        mask_percentage = (masked_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        
        return {
            'total_pixels': total_pixels,
            'masked_pixels': masked_pixels,
            'mask_percentage': mask_percentage,
            'shape': self.mask_data.shape
        }
    
    def is_edit_mode(self) -> bool:
        """是否处于编辑模式"""
        return self.edit_mode
    
    def is_painting_active(self) -> bool:
        """是否正在绘制"""
        return self.is_painting
    
    def get_current_tool(self) -> str:
        """获取当前工具"""
        return self.current_tool
    
    def get_brush_size(self) -> int:
        """获取画笔大小"""
        return self.brush_size
    
    def get_brush_hardness(self) -> float:
        """获取画笔硬度"""
        return self.brush_hardness
    
    def _execute_callback(self, event_name: str, *args):
        """执行回调函数"""
        callback = self.callbacks.get(event_name)
        if callback:
            try:
                callback(*args)
            except Exception as e:
                print(f"蒙版编辑器回调执行失败 {event_name}: {e}")