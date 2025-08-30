"""
编辑器工具栏组件
提供常用的编辑工具和操作按钮
"""
import customtkinter as ctk
import tkinter as tk
from typing import Callable, Optional, Dict, Any
import logging

class EditorToolbar(ctk.CTkFrame):
    """编辑器工具栏"""
    
    def __init__(self, parent, back_callback: Optional[Callable] = None, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.logger = logging.getLogger(__name__)
        self.callbacks: Dict[str, Callable] = {}
        self.back_callback = back_callback
        
        # 工具状态
        self.zoom_level = 1.0
        
        self._create_toolbar()
        self._setup_layout()
    
    def _create_toolbar(self):
        """创建工具栏"""
        # 文件与导航组
        self.file_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.back_btn = ctk.CTkButton(
            self.file_frame,
            text="⬅️ 返回",
            width=70,
            command=self.back_callback
        )

        self.load_btn = ctk.CTkButton(
            self.file_frame, 
            text="📂 加载", 
            width=70,
            command=lambda: self._execute_callback('load_image')
        )
        
        self.save_btn = ctk.CTkButton(
            self.file_frame, 
            text="💾 保存", 
            width=70,
            command=lambda: self._execute_callback('save_file')
        )
        
        # 编辑操作组
        self.edit_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.undo_btn = ctk.CTkButton(
            self.edit_frame, 
            text="↶ 撤销", 
            width=70,
            command=lambda: self._execute_callback('undo'),
            state="disabled"
        )
        
        self.redo_btn = ctk.CTkButton(
            self.edit_frame, 
            text="↷ 重做", 
            width=70,
            command=lambda: self._execute_callback('redo'),
            state="disabled"
        )

        self.draw_textbox_btn = ctk.CTkButton(
            self.edit_frame,
            text="编辑形状",
            width=70,
            command=lambda: self._execute_callback('edit_geometry')
        )

        self.render_btn = ctk.CTkButton(
            self.edit_frame,
            text="✨ 渲染",
            width=70,
            command=lambda: self._execute_callback('render_inpaint')
        )
        
        # 视图控制组
        self.view_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.zoom_out_btn = ctk.CTkButton(
            self.view_frame,
            text="🔍-",
            width=40,
            command=lambda: self._execute_callback('zoom_out')
        )
        
        self.zoom_label = ctk.CTkLabel(
            self.view_frame,
            text="100%",
            width=60
        )
        
        self.zoom_in_btn = ctk.CTkButton(
            self.view_frame,
            text="🔍+",
            width=40,
            command=lambda: self._execute_callback('zoom_in')
        )
        
        self.fit_btn = ctk.CTkButton(
            self.view_frame,
            text="适应",
            width=50,
            command=lambda: self._execute_callback('fit_window')
        )
        
        # 显示选项
        self.display_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.display_menu = ctk.CTkOptionMenu(
            self.display_frame,
            values=["文字文本框显示", "只显示文字", "只显示框线", "都不显示", "蒙版视图"],
            width=120,
            command=self._on_display_option_changed
        )
        self.display_menu.set("都不显示")

        # 修复预览组
        self.preview_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="原图:")
        self.preview_slider = ctk.CTkSlider(self.preview_frame, from_=0, to=100, command=lambda val: self._execute_callback('preview_alpha_changed', val))
        self.preview_slider.set(0)
    
    def _on_display_option_changed(self, choice: str):
        self._execute_callback('display_mode_changed', choice)

    def _setup_layout(self):
        """设置布局"""
        # 使用grid布局，水平排列各组
        self.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=0)
        self.grid_columnconfigure(6, weight=1)  # 右侧弹性空间
        
        # 文件操作
        self.file_frame.grid(row=0, column=0, padx=(5, 10), pady=5, sticky="w")
        self.back_btn.pack(side="left", padx=2)
        self.load_btn.pack(side="left", padx=2)
        self.save_btn.pack(side="left", padx=2)
        
        # 编辑操作
        self.edit_frame.grid(row=0, column=1, padx=10, pady=5, sticky="w")
        self.undo_btn.pack(side="left", padx=2)
        self.redo_btn.pack(side="left", padx=2)
        self.draw_textbox_btn.pack(side="left", padx=2)
        self.render_btn.pack(side="left", padx=2)
        
        # 视图控制
        self.view_frame.grid(row=0, column=3, padx=10, pady=5, sticky="w")
        self.zoom_out_btn.pack(side="left", padx=2)
        self.zoom_label.pack(side="left", padx=2)
        self.zoom_in_btn.pack(side="left", padx=2)
        self.fit_btn.pack(side="left", padx=2)
        
        # 显示选项
        self.display_frame.grid(row=0, column=4, padx=10, pady=5, sticky="w")
        self.display_menu.pack(side="left", padx=2)

        # 修复预览
        self.preview_frame.grid(row=0, column=5, padx=10, pady=5, sticky="w")
        self.preview_label.pack(side="left", padx=2)
        self.preview_slider.pack(side="left", padx=2)
    
    def _execute_callback(self, action: str, *args):
        """执行回调"""
        callback = self.callbacks.get(action)
        if callback:
            try:
                self.logger.debug(f"--- TOOLBAR_DEBUG: Executing callback for '{action}' with args: {args} ---")
                callback(*args)
            except Exception as e:
                self.logger.error(f"工具栏回调执行失败 {action}: {e}")
                import traceback
                traceback.print_exc()
    
    def register_callback(self, action: str, callback: Callable):
        """注册回调"""
        self.callbacks[action] = callback
    
    def update_undo_redo_state(self, can_undo: bool, can_redo: bool):
        """更新撤销/重做按钮状态"""
        self.undo_btn.configure(state="normal" if can_undo else "disabled")
        self.redo_btn.configure(state="normal" if can_redo else "disabled")
    
    def update_paste_state(self, can_paste: bool):
        """更新粘贴按钮状态"""
        self.paste_btn.configure(state="normal" if can_paste else "disabled")
    
    def update_zoom_level(self, zoom_level: float):
        """更新缩放级别显示"""
        self.zoom_level = zoom_level
        self.zoom_label.configure(text=f"{zoom_level:.0%}")

    def set_render_button_state(self, state: str):
        """设置渲染按钮的状态 ('normal' or 'disabled')"""
        self.render_btn.configure(state=state)
    
    def get_text_visibility(self) -> bool:
        """获取文本可见性"""
        return self.show_text_var.get()
    
    def get_boxes_visibility(self) -> bool:
        """获取框线可见性"""
        return self.show_boxes_var.get()
    
    def set_text_visibility(self, visible: bool):
        """设置文本可见性"""
        self.show_text_var.set(visible)
    
    def set_boxes_visibility(self, visible: bool):
        """设置框线可见性"""
        self.show_boxes_var.set(visible)
