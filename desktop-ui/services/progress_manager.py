"""
进度管理器
提供进度跟踪、用户反馈和任务管理功能
"""
import tkinter as tk
import customtkinter as ctk
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
import threading
import time
import logging
from enum import Enum

class ProgressStatus(Enum):
    """进度状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"

@dataclass
class ProgressInfo:
    """进度信息"""
    current: int = 0
    total: int = 100
    percentage: float = 0.0
    message: str = ""
    status: ProgressStatus = ProgressStatus.IDLE
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    elapsed_time: float = 0.0
    estimated_time: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

class ProgressDialog(ctk.CTkToplevel):
    """进度对话框"""
    
    def __init__(self, parent, title: str = "进度", cancellable: bool = True):
        super().__init__(parent)
        
        self.title(title)
        self.geometry("400x200")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        
        # 居中显示
        self.center_window()
        
        self.cancellable = cancellable
        self.cancelled = False
        self.cancel_callback = None
        
        self._create_widgets()
        
    def center_window(self):
        """窗口居中"""
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.winfo_screenheight() // 2) - (200 // 2)
        self.geometry(f"400x200+{x}+{y}")
    
    def _create_widgets(self):
        """创建组件"""
        # 主容器
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # 标题
        self.title_label = ctk.CTkLabel(
            main_frame, 
            text="请稍候...", 
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.title_label.pack(pady=(0, 10))
        
        # 进度条
        self.progress_bar = ctk.CTkProgressBar(main_frame, width=300)
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0)
        
        # 进度文本
        self.progress_label = ctk.CTkLabel(main_frame, text="0%")
        self.progress_label.pack(pady=5)
        
        # 状态消息
        self.status_label = ctk.CTkLabel(main_frame, text="准备中...")
        self.status_label.pack(pady=5)
        
        # 时间信息
        self.time_label = ctk.CTkLabel(main_frame, text="")
        self.time_label.pack(pady=5)
        
        # 按钮容器
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(fill="x", pady=(10, 0))
        
        if self.cancellable:
            # 取消按钮
            self.cancel_button = ctk.CTkButton(
                button_frame, 
                text="取消", 
                command=self._on_cancel,
                width=80
            )
            self.cancel_button.pack(side="right", padx=(10, 0))
        
        # 最小化按钮
        self.minimize_button = ctk.CTkButton(
            button_frame, 
            text="最小化", 
            command=self.iconify,
            width=80
        )
        self.minimize_button.pack(side="right")
    
    def update_progress(self, progress_info: ProgressInfo):
        """更新进度信息"""
        # 更新进度条
        self.progress_bar.set(progress_info.percentage / 100.0)
        
        # 更新进度文本
        self.progress_label.configure(text=f"{progress_info.percentage:.1f}% ({progress_info.current}/{progress_info.total})")
        
        # 更新状态消息
        self.status_label.configure(text=progress_info.message)
        
        # 更新时间信息
        if progress_info.status == ProgressStatus.RUNNING:
            elapsed = time.time() - progress_info.start_time
            if progress_info.percentage > 0:
                estimated_total = elapsed / (progress_info.percentage / 100.0)
                remaining = estimated_total - elapsed
                time_text = f"已用时: {self._format_time(elapsed)} | 预计剩余: {self._format_time(remaining)}"
            else:
                time_text = f"已用时: {self._format_time(elapsed)}"
        else:
            time_text = ""
        
        self.time_label.configure(text=time_text)
        
        # 更新窗口标题
        if progress_info.percentage > 0:
            self.title(f"进度 - {progress_info.percentage:.1f}%")
        
        # 完成时自动关闭
        if progress_info.status in [ProgressStatus.COMPLETED, ProgressStatus.ERROR]:
            self.after(2000, self.destroy)  # 2秒后自动关闭
    
    def _format_time(self, seconds: float) -> str:
        """格式化时间"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes:.0f}分{secs:.0f}秒"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours:.0f}小时{minutes:.0f}分"
    
    def set_cancel_callback(self, callback: Callable[[], None]):
        """设置取消回调"""
        self.cancel_callback = callback
    
    def _on_cancel(self):
        """处理取消操作"""
        self.cancelled = True
        if self.cancel_callback:
            self.cancel_callback()
        self.destroy()
    
    def is_cancelled(self) -> bool:
        """检查是否已取消"""
        return self.cancelled

class ProgressManager:
    """进度管理器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.active_tasks: Dict[str, ProgressInfo] = {}
        self.observers: Dict[str, List[Callable[[ProgressInfo], None]]] = {}
        self.dialogs: Dict[str, ProgressDialog] = {}
        self._lock = threading.Lock()
        
    def create_task(self, task_id: str, total: int = 100, message: str = "") -> ProgressInfo:
        """创建新的进度任务"""
        with self._lock:
            progress_info = ProgressInfo(
                total=total,
                message=message,
                status=ProgressStatus.IDLE
            )
            self.active_tasks[task_id] = progress_info
            self.observers[task_id] = []
            
        self.logger.info(f"创建进度任务: {task_id}")
        return progress_info
    
    def start_task(self, task_id: str) -> bool:
        """开始任务"""
        with self._lock:
            if task_id not in self.active_tasks:
                return False
            
            progress_info = self.active_tasks[task_id]
            progress_info.status = ProgressStatus.RUNNING
            progress_info.start_time = time.time()
            
        self._notify_observers(task_id, progress_info)
        self.logger.info(f"开始任务: {task_id}")
        return True
    
    def update_task(self, task_id: str, current: int = None, message: str = None, 
                   details: Dict[str, Any] = None) -> bool:
        """更新任务进度"""
        with self._lock:
            if task_id not in self.active_tasks:
                return False
            
            progress_info = self.active_tasks[task_id]
            
            if current is not None:
                progress_info.current = min(current, progress_info.total)
                progress_info.percentage = (progress_info.current / progress_info.total) * 100 if progress_info.total > 0 else 0
            
            if message is not None:
                progress_info.message = message
            
            if details is not None:
                progress_info.details.update(details)
            
            # 更新时间信息
            if progress_info.status == ProgressStatus.RUNNING:
                progress_info.elapsed_time = time.time() - progress_info.start_time
                
                if progress_info.percentage > 0:
                    progress_info.estimated_time = progress_info.elapsed_time / (progress_info.percentage / 100.0)
        
        self._notify_observers(task_id, progress_info)
        return True
    
    def complete_task(self, task_id: str, message: str = "完成") -> bool:
        """完成任务"""
        with self._lock:
            if task_id not in self.active_tasks:
                return False
            
            progress_info = self.active_tasks[task_id]
            progress_info.status = ProgressStatus.COMPLETED
            progress_info.current = progress_info.total
            progress_info.percentage = 100.0
            progress_info.message = message
            progress_info.end_time = time.time()
            progress_info.elapsed_time = progress_info.end_time - progress_info.start_time
        
        self._notify_observers(task_id, progress_info)
        self.logger.info(f"完成任务: {task_id} (用时: {progress_info.elapsed_time:.2f}秒)")
        
        # 延迟清理任务
        threading.Timer(5.0, lambda: self.remove_task(task_id)).start()
        return True
    
    def cancel_task(self, task_id: str, message: str = "已取消") -> bool:
        """取消任务"""
        with self._lock:
            if task_id not in self.active_tasks:
                return False
            
            progress_info = self.active_tasks[task_id]
            progress_info.status = ProgressStatus.CANCELLED
            progress_info.message = message
            progress_info.end_time = time.time()
            progress_info.elapsed_time = progress_info.end_time - progress_info.start_time
        
        self._notify_observers(task_id, progress_info)
        self.logger.info(f"取消任务: {task_id}")
        return True
    
    def error_task(self, task_id: str, error_message: str) -> bool:
        """任务出错"""
        with self._lock:
            if task_id not in self.active_tasks:
                return False
            
            progress_info = self.active_tasks[task_id]
            progress_info.status = ProgressStatus.ERROR
            progress_info.message = f"错误: {error_message}"
            progress_info.end_time = time.time()
            progress_info.elapsed_time = progress_info.end_time - progress_info.start_time
        
        self._notify_observers(task_id, progress_info)
        self.logger.error(f"任务出错: {task_id} - {error_message}")
        return True
    
    def remove_task(self, task_id: str) -> bool:
        """移除任务"""
        with self._lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
                del self.observers[task_id]
                
                # 关闭对话框
                if task_id in self.dialogs:
                    dialog = self.dialogs[task_id]
                    try:
                        dialog.destroy()
                    except:
                        pass
                    del self.dialogs[task_id]
                
                self.logger.info(f"移除任务: {task_id}")
                return True
        return False
    
    def get_task_info(self, task_id: str) -> Optional[ProgressInfo]:
        """获取任务信息"""
        with self._lock:
            return self.active_tasks.get(task_id)
    
    def get_all_tasks(self) -> Dict[str, ProgressInfo]:
        """获取所有任务"""
        with self._lock:
            return self.active_tasks.copy()
    
    def subscribe(self, task_id: str, callback: Callable[[ProgressInfo], None]):
        """订阅任务进度更新"""
        with self._lock:
            if task_id not in self.observers:
                self.observers[task_id] = []
            self.observers[task_id].append(callback)
    
    def unsubscribe(self, task_id: str, callback: Callable[[ProgressInfo], None]):
        """取消订阅"""
        with self._lock:
            if task_id in self.observers and callback in self.observers[task_id]:
                self.observers[task_id].remove(callback)
    
    def _notify_observers(self, task_id: str, progress_info: ProgressInfo):
        """通知观察者"""
        observers = self.observers.get(task_id, []).copy()
        for observer in observers:
            try:
                observer(progress_info)
            except Exception as e:
                self.logger.error(f"通知进度观察者失败: {e}")
    
    def show_dialog(self, task_id: str, parent, title: str = "进度", cancellable: bool = True) -> ProgressDialog:
        """显示进度对话框"""
        dialog = ProgressDialog(parent, title, cancellable)
        self.dialogs[task_id] = dialog
        
        # 订阅进度更新
        def update_dialog(progress_info):
            try:
                if not dialog.winfo_exists():
                    return
                dialog.update_progress(progress_info)
            except:
                # 对话框已销毁
                self.unsubscribe(task_id, update_dialog)
        
        self.subscribe(task_id, update_dialog)
        
        # 设置取消回调
        if cancellable:
            dialog.set_cancel_callback(lambda: self.cancel_task(task_id))
        
        return dialog
    
    def create_status_bar_progress(self, parent) -> ctk.CTkProgressBar:
        """创建状态栏进度条"""
        progress_bar = ctk.CTkProgressBar(parent, width=200, height=10)
        progress_bar.set(0)
        return progress_bar
    
    def cleanup_completed_tasks(self):
        """清理已完成的任务"""
        with self._lock:
            completed_tasks = [
                task_id for task_id, info in self.active_tasks.items()
                if info.status in [ProgressStatus.COMPLETED, ProgressStatus.CANCELLED, ProgressStatus.ERROR]
                and info.end_time and (time.time() - info.end_time) > 300  # 5分钟后清理
            ]
        
        for task_id in completed_tasks:
            self.remove_task(task_id)
        
        if completed_tasks:
            self.logger.info(f"清理了 {len(completed_tasks)} 个已完成的任务")

# 全局进度管理器实例
_progress_manager = None

def get_progress_manager() -> ProgressManager:
    """获取全局进度管理器实例"""
    global _progress_manager
    if _progress_manager is None:
        _progress_manager = ProgressManager()
    return _progress_manager

# 便捷函数
def create_progress_task(task_id: str, total: int = 100, message: str = "") -> ProgressInfo:
    """创建进度任务的便捷函数"""
    return get_progress_manager().create_task(task_id, total, message)

def update_progress(task_id: str, current: int = None, message: str = None) -> bool:
    """更新进度的便捷函数"""
    return get_progress_manager().update_task(task_id, current, message)

def complete_progress(task_id: str, message: str = "完成") -> bool:
    """完成进度的便捷函数"""
    return get_progress_manager().complete_task(task_id, message)