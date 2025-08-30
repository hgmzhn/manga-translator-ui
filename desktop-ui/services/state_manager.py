"""
应用状态管理器
实现响应式状态管理，支持状态订阅和通知机制
"""
import logging
from typing import Dict, List, Any, Callable, Optional
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
import threading

class AppStateKey(Enum):
    """应用状态键枚举"""
    # 翻译相关状态
    IS_TRANSLATING = "is_translating"
    TRANSLATION_PROGRESS = "translation_progress"
    CURRENT_FILES = "current_files"
    TRANSLATION_RESULTS = "translation_results"
    
    # 配置相关状态
    CURRENT_CONFIG = "current_config"
    CONFIG_PATH = "config_path"
    ENV_VARS = "env_vars"
    
    # UI相关状态
    CURRENT_VIEW = "current_view"
    SELECTED_FILES = "selected_files"
    EDITOR_STATE = "editor_state"
    
    # 应用相关状态
    APP_READY = "app_ready"
    ERROR_MESSAGES = "error_messages"
    STATUS_MESSAGE = "status_message"

@dataclass
class StateChange:
    """状态变化事件"""
    key: AppStateKey
    old_value: Any
    new_value: Any
    timestamp: float = field(default_factory=lambda: __import__('time').time())

class StateManager:
    """状态管理器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._state: Dict[AppStateKey, Any] = {}
        self._observers: Dict[AppStateKey, List[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()
        
        # 初始化默认状态
        self._initialize_default_state()
    
    def _initialize_default_state(self):
        """初始化默认状态值"""
        default_state = {
            AppStateKey.IS_TRANSLATING: False,
            AppStateKey.TRANSLATION_PROGRESS: 0.0,
            AppStateKey.CURRENT_FILES: [],
            AppStateKey.TRANSLATION_RESULTS: [],
            AppStateKey.CURRENT_CONFIG: {},
            AppStateKey.CONFIG_PATH: None,
            AppStateKey.ENV_VARS: {},
            AppStateKey.CURRENT_VIEW: "main",
            AppStateKey.SELECTED_FILES: [],
            AppStateKey.EDITOR_STATE: {},
            AppStateKey.APP_READY: False,
            AppStateKey.ERROR_MESSAGES: [],
            AppStateKey.STATUS_MESSAGE: "就绪"
        }
        
        with self._lock:
            self._state.update(default_state)
    
    def get_state(self, key: AppStateKey) -> Any:
        """获取状态值"""
        with self._lock:
            return self._state.get(key)
    
    def set_state(self, key: AppStateKey, value: Any, notify: bool = True) -> None:
        """设置状态值"""
        with self._lock:
            old_value = self._state.get(key)
            if old_value != value:
                self._state[key] = value
                if notify:
                    self._notify_observers(key, value, old_value)
    
    def update_state(self, updates: Dict[AppStateKey, Any]) -> None:
        """批量更新状态"""
        changes = []
        
        with self._lock:
            for key, value in updates.items():
                old_value = self._state.get(key)
                if old_value != value:
                    self._state[key] = value
                    changes.append((key, value, old_value))
        
        # 在锁外部发送通知，避免死锁
        for key, new_value, old_value in changes:
            self._notify_observers(key, new_value, old_value)
    
    def get_all_state(self) -> Dict[AppStateKey, Any]:
        """获取所有状态"""
        with self._lock:
            return self._state.copy()
    
    def subscribe(self, key: AppStateKey, callback: Callable[[Any], None]) -> None:
        """订阅状态变化"""
        with self._lock:
            self._observers[key].append(callback)
            self.logger.debug(f"订阅状态变化: {key.value}")
    
    def unsubscribe(self, key: AppStateKey, callback: Callable[[Any], None]) -> None:
        """取消订阅状态变化"""
        with self._lock:
            if callback in self._observers[key]:
                self._observers[key].remove(callback)
                self.logger.debug(f"取消订阅状态变化: {key.value}")
    
    def _notify_observers(self, key: AppStateKey, new_value: Any, old_value: Any) -> None:
        """通知观察者状态变化"""
        observers = self._observers[key].copy()  # 创建副本避免并发修改
        
        for observer in observers:
            try:
                observer(new_value)
            except Exception as e:
                self.logger.error(f"通知观察者失败 {key.value}: {e}")
    
    def clear_state(self, key: AppStateKey) -> None:
        """清除特定状态"""
        self.set_state(key, None)
    
    def reset_state(self) -> None:
        """重置所有状态到默认值"""
        self._initialize_default_state()
        
        # 通知所有观察者状态已重置
        for key in AppStateKey:
            self._notify_observers(key, self._state.get(key), None)
    
    # 便捷方法
    def is_translating(self) -> bool:
        """是否正在翻译"""
        return self.get_state(AppStateKey.IS_TRANSLATING) or False
    
    def set_translating(self, translating: bool) -> None:
        """设置翻译状态"""
        self.set_state(AppStateKey.IS_TRANSLATING, translating)
    
    def get_current_files(self) -> List[str]:
        """获取当前文件列表"""
        return self.get_state(AppStateKey.CURRENT_FILES) or []
    
    def set_current_files(self, files: List[str]) -> None:
        """设置当前文件列表"""
        self.set_state(AppStateKey.CURRENT_FILES, files)
    
    def add_file(self, file_path: str) -> None:
        """添加文件到当前列表"""
        current_files = self.get_current_files()
        if file_path not in current_files:
            current_files.append(file_path)
            self.set_current_files(current_files)
    
    def remove_file(self, file_path: str) -> None:
        """从当前列表移除文件"""
        current_files = self.get_current_files()
        if file_path in current_files:
            current_files.remove(file_path)
            self.set_current_files(current_files)
    
    def clear_files(self) -> None:
        """清空文件列表"""
        self.set_current_files([])
    
    def get_translation_progress(self) -> float:
        """获取翻译进度"""
        return self.get_state(AppStateKey.TRANSLATION_PROGRESS) or 0.0
    
    def set_translation_progress(self, progress: float) -> None:
        """设置翻译进度"""
        self.set_state(AppStateKey.TRANSLATION_PROGRESS, max(0.0, min(100.0, progress)))
    
    def get_current_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return self.get_state(AppStateKey.CURRENT_CONFIG) or {}
    
    def set_current_config(self, config: Dict[str, Any]) -> None:
        """设置当前配置"""
        self.set_state(AppStateKey.CURRENT_CONFIG, config)
    
    def get_status_message(self) -> str:
        """获取状态消息"""
        return self.get_state(AppStateKey.STATUS_MESSAGE) or ""
    
    def set_status_message(self, message: str) -> None:
        """设置状态消息"""
        self.set_state(AppStateKey.STATUS_MESSAGE, message)
    
    def add_error_message(self, error: str) -> None:
        """添加错误消息"""
        errors = self.get_state(AppStateKey.ERROR_MESSAGES) or []
        errors.append({
            'message': error,
            'timestamp': __import__('time').time()
        })
        # 只保留最近的10个错误
        if len(errors) > 10:
            errors = errors[-10:]
        self.set_state(AppStateKey.ERROR_MESSAGES, errors)
    
    def clear_error_messages(self) -> None:
        """清除错误消息"""
        self.set_state(AppStateKey.ERROR_MESSAGES, [])
    
    def get_selected_files(self) -> List[str]:
        """获取选中的文件"""
        return self.get_state(AppStateKey.SELECTED_FILES) or []
    
    def set_selected_files(self, files: List[str]) -> None:
        """设置选中的文件"""
        self.set_state(AppStateKey.SELECTED_FILES, files)
    
    def get_current_view(self) -> str:
        """获取当前视图"""
        return self.get_state(AppStateKey.CURRENT_VIEW) or "main"
    
    def set_current_view(self, view: str) -> None:
        """设置当前视图"""
        self.set_state(AppStateKey.CURRENT_VIEW, view)
    
    def is_app_ready(self) -> bool:
        """应用是否就绪"""
        return self.get_state(AppStateKey.APP_READY) or False
    
    def set_app_ready(self, ready: bool) -> None:
        """设置应用就绪状态"""
        self.set_state(AppStateKey.APP_READY, ready)

# 全局状态管理器实例
_state_manager = None

def get_state_manager() -> StateManager:
    """获取全局状态管理器实例"""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager