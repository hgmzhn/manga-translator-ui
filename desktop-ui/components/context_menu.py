"""
右键上下文菜单组件
支持文本框的OCR识别和翻译操作
"""
import customtkinter as ctk
import tkinter as tk
from typing import Dict, Any, Callable, Optional
import logging

class ContextMenu:
    """右键上下文菜单"""
    
    def __init__(self, parent_widget):
        self.parent_widget = parent_widget
        self.logger = logging.getLogger(__name__)
        self.callbacks: Dict[str, Callable] = {}
        self.selected_region_index = None
        self.selected_region_data = None
        self.menu = None
        
    def register_callback(self, event_name: str, callback: Callable):
        self.callbacks[event_name] = callback
        
    def set_selected_region(self, region_index: Optional[int], region_data: Optional[Dict[str, Any]]):
        self.selected_region_index = region_index
        self.selected_region_data = region_data
    
    def show_menu(self, event, selection_count=0):
        try:
            self.menu = tk.Menu(self.parent_widget, tearoff=0)
            if selection_count > 0:
                self._add_region_menu_items(selection_count)
            else:
                self._add_general_menu_items()
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            self.logger.error(f"显示右键菜单失败: {e}")
        finally:
            if self.menu:
                self.menu = None
    
    def _add_region_menu_items(self, selection_count=0):
        # To be overridden by subclass
        pass
    
    def _add_general_menu_items(self):
        self.menu.add_command(label="➕ 新建文本框", command=lambda: self._execute_callback('add_text_box'))
        self.menu.add_command(label="📋 粘贴区域", command=lambda: self._execute_callback('paste_region'))
        self.menu.add_separator()
        self.menu.add_command(label="🔄 刷新视图", command=lambda: self._execute_callback('refresh_view'))
    
    def _execute_callback(self, event_name: str, *args):
        callback = self.callbacks.get(event_name)
        if callback:
            try:
                callback(*args)
            except Exception as e:
                self.logger.error(f"右键菜单回调执行失败 {event_name}: {e}")

class EditorContextMenu(ContextMenu):
    """编辑器专用右键菜单"""
    
    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self.ocr_model = "48px"
        self.translator = "sugoi"
        self.target_language = "CHS"
        
    def set_ocr_config(self, ocr_model: str, translator: str, target_language: str):
        self.ocr_model = ocr_model
        self.translator = translator
        self.target_language = target_language
    
    def _add_region_menu_items(self, selection_count=0):
        # OCR and Translate are always available for multi-selection
        self.menu.add_command(label="🔍 OCR识别选中项", command=lambda: self._execute_callback('ocr_recognize'))
        self.menu.add_command(label="🌐 翻译选中项", command=lambda: self._execute_callback('translate_text'))
        self.menu.add_separator()

        # Copy/Paste only available for single selection
        if selection_count == 1:
            self.menu.add_command(label="📝 编辑属性", command=lambda: self._execute_callback('edit_properties'))
            self.menu.add_command(label="📋 复制样式+内容", command=lambda: self._execute_callback('copy_region'))
            self.menu.add_command(label="🎨 粘贴样式+内容", command=lambda: self._execute_callback('paste_style'))
            self.menu.add_separator()
        
        # Delete is always available for any selection
        self.menu.add_command(label=f"🗑️ 删除选中的 {selection_count} 个项目", command=lambda: self._execute_callback('delete_region'))