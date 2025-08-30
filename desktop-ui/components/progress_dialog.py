"""
进度显示和错误处理对话框
用于OCR识别和翻译操作的进度显示
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import threading
import time
from typing import Callable, Optional
import logging

class ProgressDialog(ctk.CTkToplevel):
    """进度显示对话框"""
    
    def __init__(self, parent, title="处理中...", message="请等待..."):
        super().__init__(parent)
        
        self.title(title)
        self.geometry("400x200")
        self.resizable(False, False)
        
        # 设置为模态对话框
        self.transient(parent)
        self.grab_set()
        
        # 居中显示
        self.center_on_parent(parent)
        
        self.logger = logging.getLogger(__name__)
        self.is_cancelled = False
        self.can_cancel = True
        
        self._create_widgets(message)
        
    def center_on_parent(self, parent):
        """在父窗口中心显示"""
        try:
            parent.update_idletasks()
            x = parent.winfo_x() + (parent.winfo_width() // 2) - 200
            y = parent.winfo_y() + (parent.winfo_height() // 2) - 100
            self.geometry(f"400x200+{x}+{y}")
        except:
            pass
    
    def _create_widgets(self, message):
        """创建界面组件"""
        # 主框架
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # 消息标签
        self.message_label = ctk.CTkLabel(
            main_frame, 
            text=message,
            font=ctk.CTkFont(size=14),
            wraplength=300
        )
        self.message_label.pack(pady=(20, 10))
        
        # 进度条
        self.progress_bar = ctk.CTkProgressBar(main_frame, width=300)
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0)
        
        # 状态标签
        self.status_label = ctk.CTkLabel(
            main_frame,
            text="初始化...",
            font=ctk.CTkFont(size=12)
        )
        self.status_label.pack(pady=5)
        
        # 按钮框架
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(pady=(20, 10))
        
        # 取消按钮
        self.cancel_button = ctk.CTkButton(
            button_frame,
            text="取消",
            width=100,
            command=self._on_cancel
        )
        self.cancel_button.pack(side="right", padx=5)
        
        # 绑定关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
    
    def set_progress(self, value: float, status: str = ""):
        """设置进度"""
        try:
            self.progress_bar.set(value)
            if status:
                self.status_label.configure(text=status)
            self.update()
        except:
            pass
    
    def set_message(self, message: str):
        """设置消息"""
        try:
            self.message_label.configure(text=message)
            self.update()
        except:
            pass
    
    def set_indeterminate(self, enabled: bool = True):
        """设置不确定进度模式"""
        if enabled:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
    
    def set_cancellable(self, cancellable: bool):
        """设置是否可以取消"""
        self.can_cancel = cancellable
        state = "normal" if cancellable else "disabled"
        self.cancel_button.configure(state=state)
    
    def _on_cancel(self):
        """取消按钮处理"""
        if self.can_cancel:
            self.is_cancelled = True
            self.destroy()
    
    def close_dialog(self):
        """关闭对话框"""
        try:
            self.destroy()
        except:
            pass

class OperationManager:
    """操作管理器，处理OCR和翻译的异步操作"""
    
    def __init__(self, parent_widget):
        self.parent_widget = parent_widget
        self.logger = logging.getLogger(__name__)
        self.current_dialog: Optional[ProgressDialog] = None
        
    def execute_ocr_operation(self, 
                            operation_func: Callable,
                            success_callback: Callable = None,
                            error_callback: Callable = None,
                            progress_callback: Callable = None):
        """执行OCR操作"""
        
        # 创建进度对话框
        self.current_dialog = ProgressDialog(
            self.parent_widget,
            "OCR识别", 
            "正在识别文本内容..."
        )
        
        # 设置不确定进度模式
        self.current_dialog.set_indeterminate(True)
        
        def worker():
            try:
                self.current_dialog.set_progress(0.2, "准备OCR模型...")
                time.sleep(0.5)  # 模拟延迟
                
                self.current_dialog.set_progress(0.5, "执行OCR识别...")
                
                # 执行实际操作
                result = operation_func()
                
                self.current_dialog.set_progress(0.9, "处理识别结果...")
                time.sleep(0.2)
                
                # 在主线程中执行成功回调
                if success_callback and not self.current_dialog.is_cancelled:
                    self.parent_widget.after(0, lambda: success_callback(result))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
                
            except Exception as e:
                self.logger.error(f"OCR操作失败: {e}")
                
                # 在主线程中执行错误回调
                if error_callback:
                    self.parent_widget.after(0, lambda: error_callback(str(e)))
                else:
                    self.parent_widget.after(0, lambda: self._show_error("OCR识别失败", str(e)))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
        
        # 启动工作线程
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
    
    def execute_translation_operation(self,
                                    operation_func: Callable,
                                    success_callback: Callable = None,
                                    error_callback: Callable = None,
                                    progress_callback: Callable = None):
        """执行翻译操作"""
        
        # 创建进度对话框
        self.current_dialog = ProgressDialog(
            self.parent_widget,
            "文本翻译",
            "正在翻译文本内容..."
        )
        
        # 设置不确定进度模式
        self.current_dialog.set_indeterminate(True)
        
        def worker():
            try:
                self.current_dialog.set_progress(0.2, "准备翻译器...")
                time.sleep(0.3)
                
                self.current_dialog.set_progress(0.5, "执行翻译...")
                
                # 执行实际操作
                result = operation_func()
                
                self.current_dialog.set_progress(0.9, "处理翻译结果...")
                time.sleep(0.2)
                
                # 在主线程中执行成功回调
                if success_callback and not self.current_dialog.is_cancelled:
                    self.parent_widget.after(0, lambda: success_callback(result))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
                
            except Exception as e:
                self.logger.error(f"翻译操作失败: {e}")
                
                # 在主线程中执行错误回调
                if error_callback:
                    self.parent_widget.after(0, lambda: error_callback(str(e)))
                else:
                    self.parent_widget.after(0, lambda: self._show_error("翻译失败", str(e)))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
        
        # 启动工作线程
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
    
    def execute_combined_operation(self,
                                 ocr_func: Callable,
                                 translation_func: Callable,
                                 success_callback: Callable = None,
                                 error_callback: Callable = None):
        """执行OCR识别和翻译的组合操作"""
        
        # 创建进度对话框
        self.current_dialog = ProgressDialog(
            self.parent_widget,
            "OCR识别和翻译",
            "正在执行OCR识别和翻译..."
        )
        
        def worker():
            try:
                # OCR阶段
                self.current_dialog.set_progress(0.1, "准备OCR模型...")
                time.sleep(0.3)
                
                self.current_dialog.set_progress(0.3, "执行OCR识别...")
                ocr_result = ocr_func()
                
                if self.current_dialog.is_cancelled:
                    return
                
                # 翻译阶段
                self.current_dialog.set_progress(0.6, "准备翻译器...")
                time.sleep(0.2)
                
                self.current_dialog.set_progress(0.8, "执行翻译...")
                translation_result = translation_func(ocr_result)
                
                self.current_dialog.set_progress(1.0, "完成处理...")
                time.sleep(0.2)
                
                # 在主线程中执行成功回调
                if success_callback and not self.current_dialog.is_cancelled:
                    self.parent_widget.after(0, lambda: success_callback(ocr_result, translation_result))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
                
            except Exception as e:
                self.logger.error(f"组合操作失败: {e}")
                
                # 在主线程中执行错误回调
                if error_callback:
                    self.parent_widget.after(0, lambda: error_callback(str(e)))
                else:
                    self.parent_widget.after(0, lambda: self._show_error("OCR识别和翻译失败", str(e)))
                
                # 关闭对话框
                self.parent_widget.after(0, self.current_dialog.close_dialog)
        
        # 启动工作线程
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
    
    def _show_error(self, title: str, message: str):
        """显示错误消息"""
        try:
            messagebox.showerror(title, f"操作失败：\n{message}")
        except Exception as e:
            print(f"显示错误消息失败: {e}")
            print(f"原始错误: {title} - {message}")