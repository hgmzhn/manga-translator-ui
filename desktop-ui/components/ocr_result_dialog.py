"""
OCR结果确认对话框
提供用户选择如何处理OCR识别结果的界面
"""
import customtkinter as ctk
import tkinter as tk
from typing import Callable, Optional


class OcrResultDialog(ctk.CTkToplevel):
    """OCR结果确认对话框"""
    
    def __init__(self, parent, original_text: str, recognized_text: str, 
                 on_confirm: Callable[[str, str], None] = None):
        super().__init__(parent)
        
        self.original_text = original_text
        self.recognized_text = recognized_text
        self.on_confirm = on_confirm
        self.result_action = None  # "replace", "append", "cancel"
        self.final_original_text = original_text
        self.final_recognized_text = recognized_text
        
        self._setup_dialog()
        self._create_widgets()
        
        # 设置为模态对话框
        self.transient(parent)
        self.grab_set()
        
        # 居中显示
        self._center_dialog()
        
        # 获得焦点
        self.focus_set()
    
    def _setup_dialog(self):
        """设置对话框属性"""
        self.title("OCR识别结果确认")
        self.geometry("600x500")
        self.resizable(True, True)
        
        # 设置最小尺寸
        self.minsize(500, 400)
        
        # 配置网格
        self.grid_rowconfigure(1, weight=1)  # 内容区域可扩展
        self.grid_columnconfigure(0, weight=1)
    
    def _create_widgets(self):
        """创建对话框组件"""
        # 标题区域
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        title_frame.grid_columnconfigure(0, weight=1)
        
        title_label = ctk.CTkLabel(
            title_frame,
            text="OCR识别完成，请确认如何处理识别结果",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        title_label.grid(row=0, column=0, pady=5)
        
        info_label = ctk.CTkLabel(
            title_frame,
            text="您可以选择替换原文、追加到原文末尾，或者手动编辑后确认",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        info_label.grid(row=1, column=0, pady=2)
        
        # 内容区域
        content_frame = ctk.CTkFrame(self)
        content_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        content_frame.grid_rowconfigure(1, weight=1)
        content_frame.grid_rowconfigure(3, weight=1)
        content_frame.grid_columnconfigure(0, weight=1)
        
        # 原文区域
        original_label = ctk.CTkLabel(content_frame, text="当前原文:", anchor="w", font=ctk.CTkFont(weight="bold"))
        original_label.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 5))
        
        self.original_textbox = ctk.CTkTextbox(content_frame, height=100)
        self.original_textbox.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        self.original_textbox.insert("1.0", self.original_text)
        
        # 识别结果区域
        recognized_label = ctk.CTkLabel(content_frame, text="OCR识别结果:", anchor="w", font=ctk.CTkFont(weight="bold"))
        recognized_label.grid(row=2, column=0, sticky="ew", padx=15, pady=(15, 5))
        
        self.recognized_textbox = ctk.CTkTextbox(content_frame, height=100)
        self.recognized_textbox.grid(row=3, column=0, sticky="nsew", padx=15, pady=(5, 15))
        self.recognized_textbox.insert("1.0", self.recognized_text)
        
        # 操作提示
        hint_label = ctk.CTkLabel(
            content_frame, 
            text="提示：您可以直接编辑上述文本内容，然后点击确认按钮应用修改",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        hint_label.grid(row=4, column=0, sticky="ew", padx=15, pady=(0, 10))
        
        # 按钮区域
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 10))
        button_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        
        # 快捷操作按钮
        replace_btn = ctk.CTkButton(
            button_frame,
            text="替换原文",
            command=self._on_replace,
            fg_color="#1f538d",
            hover_color="#164a7d"
        )
        replace_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        
        append_btn = ctk.CTkButton(
            button_frame,
            text="追加到原文",
            command=self._on_append,
            fg_color="#2e7d32",
            hover_color="#1b5e20"
        )
        append_btn.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # 确认和取消按钮
        confirm_btn = ctk.CTkButton(
            button_frame,
            text="确认修改",
            command=self._on_confirm_changes,
            fg_color="#ed6c02",
            hover_color="#d84315"
        )
        confirm_btn.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        cancel_btn = ctk.CTkButton(
            button_frame,
            text="取消",
            command=self._on_cancel,
            fg_color="#666666",
            hover_color="#555555"
        )
        cancel_btn.grid(row=0, column=3, padx=5, pady=5, sticky="ew")
        
        # 绑定键盘事件
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.bind("<Return>", lambda e: self._on_confirm_changes())
    
    def _center_dialog(self):
        """将对话框居中显示"""
        self.update_idletasks()
        
        # 获取对话框尺寸
        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()
        
        # 获取父窗口位置和尺寸
        parent_x = self.master.winfo_x()
        parent_y = self.master.winfo_y()
        parent_width = self.master.winfo_width()
        parent_height = self.master.winfo_height()
        
        # 计算居中位置
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        
        self.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
    
    def _on_replace(self):
        """替换原文操作"""
        self.original_textbox.delete("1.0", "end")
        self.original_textbox.insert("1.0", self.recognized_text)
        self.result_action = "replace"
    
    def _on_append(self):
        """追加到原文操作"""
        current_original = self.original_textbox.get("1.0", "end-1c")
        
        if current_original.strip():
            # 如果原文不为空，添加分隔符后追加
            separator = "\n" if not current_original.endswith("\n") else ""
            new_text = current_original + separator + self.recognized_text
        else:
            # 如果原文为空，直接使用识别结果
            new_text = self.recognized_text
        
        self.original_textbox.delete("1.0", "end")
        self.original_textbox.insert("1.0", new_text)
        self.result_action = "append"
    
    def _on_confirm_changes(self):
        """确认修改操作"""
        self.final_original_text = self.original_textbox.get("1.0", "end-1c")
        self.final_recognized_text = self.recognized_textbox.get("1.0", "end-1c")
        
        if self.on_confirm:
            self.on_confirm(self.final_original_text, self.final_recognized_text)
        
        self.result_action = "confirm"
        self.destroy()
    
    def _on_cancel(self):
        """取消操作"""
        self.result_action = "cancel"
        self.destroy()
    
    def get_result(self) -> tuple[Optional[str], Optional[str], str]:
        """获取对话框结果
        
        Returns:
            tuple: (final_original_text, final_recognized_text, action)
                   action: "confirm", "cancel"
        """
        return self.final_original_text, self.final_recognized_text, self.result_action or "cancel"


def show_ocr_result_dialog(parent, original_text: str, recognized_text: str, 
                          on_confirm: Callable[[str, str], None] = None) -> OcrResultDialog:
    """显示OCR结果确认对话框
    
    Args:
        parent: 父窗口
        original_text: 当前原文
        recognized_text: OCR识别结果
        on_confirm: 确认回调函数，接收 (final_original_text, final_recognized_text) 参数
    
    Returns:
        OcrResultDialog: 对话框实例
    """
    return OcrResultDialog(parent, original_text, recognized_text, on_confirm)