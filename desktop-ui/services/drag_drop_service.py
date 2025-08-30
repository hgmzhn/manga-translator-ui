"""
拖拽功能支持
处理文件和文件夹拖拽到应用程序中
"""
import os
import tkinter as tk
from typing import List, Callable, Optional
import logging


class DragDropHandler:
    """拖拽处理器"""
    
    def __init__(self, target_widget, 
                 drop_callback: Callable[[List[str]], None],
                 file_service=None):
        """
        初始化拖拽处理器
        
        Args:
            target_widget: 目标组件
            drop_callback: 文件放下时的回调函数
            file_service: 文件服务实例
        """
        self.target_widget = target_widget
        self.drop_callback = drop_callback
        self.file_service = file_service
        self.logger = logging.getLogger(__name__)
        
        # 拖拽状态
        self.is_dragging = False
        self.drag_enter_count = 0
        
        # 设置拖拽功能
        self._setup_drag_drop()
        
    def _setup_drag_drop(self):
        """设置拖拽功能"""
        try:
            # 注册拖拽目标
            self.target_widget.drop_target_register('DND_Files')
            
            # 绑定拖拽事件
            self.target_widget.dnd_bind('<<Drop>>', self._on_drop)
            self.target_widget.dnd_bind('<<DragEnter>>', self._on_drag_enter)
            self.target_widget.dnd_bind('<<DragLeave>>', self._on_drag_leave)
            self.target_widget.dnd_bind('<<DragOver>>', self._on_drag_over)
            
        except Exception as e:
            # 如果系统不支持原生拖拽，使用备用方案
            self.logger.warning(f"原生拖拽不可用，使用备用方案: {e}")
            self._setup_fallback_drag_drop()
    
    def _setup_fallback_drag_drop(self):
        """设置备用拖拽方案（基于tkinter事件）"""
        # 绑定鼠标事件作为备用
        self.target_widget.bind('<Button-1>', self._on_click)
        self.target_widget.bind('<B1-Motion>', self._on_drag_motion)
        self.target_widget.bind('<ButtonRelease-1>', self._on_release)
        
        # 绑定键盘事件处理粘贴
        self.target_widget.bind('<Control-v>', self._on_paste)
        self.target_widget.focus_set()  # 确保能接收键盘事件
    
    def _on_drop(self, event):
        """处理文件放下事件"""
        try:
            self.logger.info("检测到文件拖拽放下")
            
            # 重置拖拽状态
            self._reset_drag_state()
            
            # 获取拖拽的文件路径
            files = self._extract_file_paths(event.data)
            
            if files:
                self.logger.info(f"拖拽了 {len(files)} 个文件/文件夹")
                # 处理拖拽的文件
                self._process_dropped_files(files)
            else:
                self.logger.warning("拖拽数据中没有找到有效文件")
                
        except Exception as e:
            self.logger.error(f"处理拖拽放下失败: {e}")
            self._reset_drag_state()
    
    def _on_drag_enter(self, event):
        """处理拖拽进入事件"""
        self.drag_enter_count += 1
        if not self.is_dragging:
            self.is_dragging = True
            self._show_drag_indicator(True)
            self.logger.debug("拖拽进入")
    
    def _on_drag_leave(self, event):
        """处理拖拽离开事件"""
        self.drag_enter_count -= 1
        if self.drag_enter_count <= 0:
            self.is_dragging = False
            self.drag_enter_count = 0
            self._show_drag_indicator(False)
            self.logger.debug("拖拽离开")
    
    def _on_drag_over(self, event):
        """处理拖拽悬停事件"""
        # 可以在这里添加视觉反馈
        pass
    
    def _on_click(self, event):
        """处理点击事件（备用方案）"""
        pass
    
    def _on_drag_motion(self, event):
        """处理拖拽移动事件（备用方案）"""
        pass
    
    def _on_release(self, event):
        """处理释放事件（备用方案）"""
        pass
    
    def _on_paste(self, event):
        """处理粘贴事件（备用方案）"""
        try:
            # 从剪贴板获取文件路径
            clipboard_data = self.target_widget.clipboard_get()
            if clipboard_data:
                files = self._parse_clipboard_data(clipboard_data)
                if files:
                    self._process_dropped_files(files)
        except Exception as e:
            self.logger.debug(f"剪贴板中没有文件数据: {e}")
    
    def _extract_file_paths(self, drop_data) -> List[str]:
        """从拖拽数据中提取文件路径"""
        files = []
        
        try:
            if hasattr(drop_data, 'split'):
                # 字符串数据
                lines = drop_data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            else:
                # 列表数据
                lines = drop_data if isinstance(drop_data, list) else [str(drop_data)]
            
            for line in lines:
                line = line.strip()
                if line:
                    # 处理URI格式
                    file_path = self._normalize_file_path(line)
                    if file_path and os.path.exists(file_path):
                        files.append(file_path)
                        
        except Exception as e:
            self.logger.error(f"解析拖拽数据失败: {e}")
            
        return files
    
    def _parse_clipboard_data(self, clipboard_data: str) -> List[str]:
        """解析剪贴板数据中的文件路径"""
        files = []
        
        # 按行分割
        lines = clipboard_data.split('\n')
        for line in lines:
            line = line.strip()
            if line and os.path.exists(line):
                files.append(os.path.abspath(line))
                
        return files
    
    def _normalize_file_path(self, path: str) -> Optional[str]:
        """标准化文件路径"""
        try:
            # 移除URI前缀
            if path.startswith('file:///'):
                path = path[8:]  # Windows: file:///C:/path
            elif path.startswith('file://'):
                path = path[7:]  # Unix: file://path
            
            # URL解码
            import urllib.parse
            path = urllib.parse.unquote(path)
            
            # 标准化路径
            path = os.path.normpath(path)
            
            return path if os.path.exists(path) else None
            
        except Exception as e:
            self.logger.error(f"标准化路径失败 {path}: {e}")
            return None
    
    def _process_dropped_files(self, file_paths: List[str]):
        """处理拖拽的文件"""
        try:
            if self.file_service:
                # 使用文件服务处理
                valid_files, errors = self.file_service.process_dropped_files('\n'.join(file_paths))
                
                if errors:
                    for error in errors:
                        self.logger.warning(error)
                
                if valid_files:
                    self.drop_callback(valid_files)
                else:
                    self.logger.warning("没有找到有效的图片文件")
            else:
                # 直接传递文件路径
                self.drop_callback(file_paths)
                
        except Exception as e:
            self.logger.error(f"处理拖拽文件失败: {e}")
    
    def _show_drag_indicator(self, show: bool):
        """显示拖拽指示器"""
        try:
            if show:
                # 显示拖拽提示
                self.target_widget.configure(relief="raised", borderwidth=2)
                # 可以添加更多视觉效果
            else:
                # 隐藏拖拽提示
                self.target_widget.configure(relief="flat", borderwidth=0)
                
        except Exception as e:
            self.logger.debug(f"设置拖拽指示器失败: {e}")
    
    def _reset_drag_state(self):
        """重置拖拽状态"""
        self.is_dragging = False
        self.drag_enter_count = 0
        self._show_drag_indicator(False)
    
    def enable(self):
        """启用拖拽功能"""
        try:
            self.target_widget.drop_target_register('DND_Files')
        except Exception:
            pass
    
    def disable(self):
        """禁用拖拽功能"""
        try:
            self.target_widget.drop_target_unregister()
        except Exception:
            pass
    
    def destroy(self):
        """销毁拖拽处理器"""
        self.disable()
        self._reset_drag_state()


class MultiWidgetDragDropHandler:
    """多组件拖拽处理器"""
    
    def __init__(self, drop_callback: Callable[[List[str]], None], file_service=None):
        self.drop_callback = drop_callback
        self.file_service = file_service
        self.handlers = []
        self.logger = logging.getLogger(__name__)
    
    def add_widget(self, widget):
        """添加支持拖拽的组件"""
        handler = DragDropHandler(widget, self.drop_callback, self.file_service)
        self.handlers.append(handler)
        return handler
    
    def remove_widget(self, widget):
        """移除组件的拖拽支持"""
        self.handlers = [h for h in self.handlers if h.target_widget != widget]
    
    def enable_all(self):
        """启用所有组件的拖拽功能"""
        for handler in self.handlers:
            handler.enable()
    
    def disable_all(self):
        """禁用所有组件的拖拽功能"""
        for handler in self.handlers:
            handler.disable()
    
    def destroy_all(self):
        """销毁所有拖拽处理器"""
        for handler in self.handlers:
            handler.destroy()
        self.handlers.clear()


def create_drag_drop_label(parent, text: str = "拖拽文件到这里") -> tk.Widget:
    """创建一个支持拖拽的标签组件"""
    import customtkinter as ctk
    
    label = ctk.CTkLabel(
        parent,
        text=text,
        height=100,
        fg_color=("gray80", "gray20"),
        corner_radius=10
    )
    
    return label