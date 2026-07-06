"""
应用状态管理器
实现响应式状态管理，支持状态订阅和通知机制
"""
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


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

from PyQt6.QtCore import QObject, pyqtSignal


class StateManager(QObject):
    """
    状态管理器 (Qt Refactored)
    使用信号/槽机制进行状态通知
    """
    # --- 定义信号 ---
    is_translating_changed = pyqtSignal(bool)
    translation_progress_changed = pyqtSignal(float)
    current_files_changed = pyqtSignal(list)
    translation_results_changed = pyqtSignal(list)
    current_config_changed = pyqtSignal(dict)
    config_path_changed = pyqtSignal(object) # Can be None
    env_vars_changed = pyqtSignal(dict)
    current_view_changed = pyqtSignal(str)
    selected_files_changed = pyqtSignal(list)
    editor_state_changed = pyqtSignal(dict)
    app_ready_changed = pyqtSignal(bool)
    error_messages_changed = pyqtSignal(list)
    status_message_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self._state: Dict[AppStateKey, Any] = {}
        self._lock = threading.RLock()
        
        self._initialize_default_state()

        # 信号映射，用于在 set_state 中动态发射信号
        self._signal_map = {
            AppStateKey.IS_TRANSLATING: self.is_translating_changed,
            AppStateKey.TRANSLATION_PROGRESS: self.translation_progress_changed,
            AppStateKey.CURRENT_FILES: self.current_files_changed,
            AppStateKey.TRANSLATION_RESULTS: self.translation_results_changed,
            AppStateKey.CURRENT_CONFIG: self.current_config_changed,
            AppStateKey.CONFIG_PATH: self.config_path_changed,
            AppStateKey.ENV_VARS: self.env_vars_changed,
            AppStateKey.CURRENT_VIEW: self.current_view_changed,
            AppStateKey.SELECTED_FILES: self.selected_files_changed,
            AppStateKey.EDITOR_STATE: self.editor_state_changed,
            AppStateKey.APP_READY: self.app_ready_changed,
            AppStateKey.ERROR_MESSAGES: self.error_messages_changed,
            AppStateKey.STATUS_MESSAGE: self.status_message_changed,
        }
    
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
        """设置状态值并根据键发射对应的信号"""
        with self._lock:
            old_value = self._state.get(key)
            # 使用 deepcopy 或其他方式进行复杂对象的值比较可能更稳健
            if old_value == value:
                return
            
            self._state[key] = value
            
            if notify:
                signal = self._signal_map.get(key)
                if signal:
                    try:
                        signal.emit(value)
                    except Exception as e:
                        self.logger.error(f"发射信号失败 {key.value}: {e}")

    def update_state(self, updates: Dict[AppStateKey, Any]) -> None:
        """批量更新状态"""
        for key, value in updates.items():
            self.set_state(key, value)

    def get_all_state(self) -> Dict[AppStateKey, Any]:
        """获取所有状态"""
        with self._lock:
            return self._state.copy()

    def reset_state(self) -> None:
        """重置所有状态到默认值并通知"""
        self._initialize_default_state()
        for key in AppStateKey:
            self.set_state(key, self._state.get(key))

    # --- 便捷方法 ---
    
    def is_translating(self) -> bool:
        return self.get_state(AppStateKey.IS_TRANSLATING) or False
    
    def set_translating(self, translating: bool) -> None:
        self.set_state(AppStateKey.IS_TRANSLATING, translating)
    
    def get_current_files(self) -> List[str]:
        return self.get_state(AppStateKey.CURRENT_FILES) or []
    
    def set_current_files(self, files: List[str]) -> None:
        self.set_state(AppStateKey.CURRENT_FILES, files)

    def get_translation_progress(self) -> float:
        return self.get_state(AppStateKey.TRANSLATION_PROGRESS) or 0.0
    
    def set_translation_progress(self, progress: float) -> None:
        self.set_state(AppStateKey.TRANSLATION_PROGRESS, max(0.0, min(100.0, progress)))
    
    def get_current_config(self) -> Dict[str, Any]:
        return self.get_state(AppStateKey.CURRENT_CONFIG) or {}
    
    def set_current_config(self, config: Any) -> None:
        if hasattr(config, 'dict') and callable(getattr(config, 'dict')):
            self.set_state(AppStateKey.CURRENT_CONFIG, config.model_dump())
        else:
            self.set_state(AppStateKey.CURRENT_CONFIG, config)
    
    def get_status_message(self) -> str:
        return self.get_state(AppStateKey.STATUS_MESSAGE) or ""
    
    def set_status_message(self, message: str) -> None:
        self.set_state(AppStateKey.STATUS_MESSAGE, message)
    
    def add_error_message(self, error: str) -> None:
        errors = self.get_state(AppStateKey.ERROR_MESSAGES) or []
        # 避免重复添加完全相同的错误信息
        if any(e['message'] == error for e in errors):
            return
        errors.append({'message': error, 'timestamp': __import__('time').time()})
        if len(errors) > 20: # 增加错误消息保留数量
            errors = errors[-20:]
        self.set_state(AppStateKey.ERROR_MESSAGES, errors)
    
    def clear_error_messages(self) -> None:
        self.set_state(AppStateKey.ERROR_MESSAGES, [])
    
    def get_selected_files(self) -> List[str]:
        return self.get_state(AppStateKey.SELECTED_FILES) or []
    
    def set_selected_files(self, files: List[str]) -> None:
        self.set_state(AppStateKey.SELECTED_FILES, files)
    
    def get_current_view(self) -> str:
        return self.get_state(AppStateKey.CURRENT_VIEW) or "main"
    
    def set_current_view(self, view: str) -> None:
        self.set_state(AppStateKey.CURRENT_VIEW, view)
    
    def is_app_ready(self) -> bool:
        return self.get_state(AppStateKey.APP_READY) or False
    
    def set_app_ready(self, ready: bool) -> None:
        self.set_state(AppStateKey.APP_READY, ready)


# 全局状态管理器实例
_state_manager = None

def get_state_manager() -> StateManager:
    """获取全局状态管理器实例"""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager