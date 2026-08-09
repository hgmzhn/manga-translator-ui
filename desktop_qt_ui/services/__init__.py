"""
服务初始化模块
统一管理和初始化所有服务组件
"""
import logging
import os
from typing import Any, Dict, Optional

# 导入编辑器核心模块（使用绝对导入）
from desktop_qt_ui.editor.core import ResourceManager

from .async_service import AsyncService

# 导入所有服务
from .config_service import ConfigService
from .file_service import FileService
from .history_service import EditorStateManager as HistoryService
from .i18n_service import I18nManager
from .log_service import LogService, setup_logging
from .ocr_service import OcrService
from .preset_service import PresetService
from .render_parameter_service import RenderParameterService
from .state_manager import StateManager
from .translation_service import TranslationService


class ServiceContainer:
    """服务容器 - 依赖注入容器"""
    
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.services: Dict[str, Any] = {}
        self.initialized = False
        self._root_widget = None
        
        # 初始化日志
        self._setup_logging()
        self.logger = logging.getLogger(__name__)
        
    def _setup_logging(self):
        """设置日志系统"""
        log_dir = os.path.join(self.root_dir, "logs")
        setup_logging(log_dir, "MangaTranslatorUI")
        
    def initialize_services(self, root_widget=None) -> bool:
        """快速初始化服务 - 分阶段异步加载"""
        try:
            self._root_widget = root_widget
            # All services are now initialized synchronously
            self._init_essential_services()
            self._init_heavy_services()
            
            # UI services can still be deferred if they depend on a fully-drawn widget
            if self._root_widget:
                # In Qt, we might not need .after(), can be called directly
                # For now, keeping the structure but it might be simplified
                self._init_ui_services()
            
            self.initialized = True
            return True
            
        except Exception as e:
            self.logger.error(f"服务初始化失败: {e}")
            return False
    
    def _init_essential_services(self):
        """初始化必需的基础服务"""
        self.services['log'] = LogService()
        self.services['state'] = StateManager()
        self.services['config'] = ConfigService(self.root_dir)
        
        # 初始化i18n服务 - 从配置读取语言设置
        locale_dir = os.path.join(self.root_dir, "desktop_qt_ui", "locales")
        config = self.services['config'].get_config()
        ui_language = config.app.ui_language if hasattr(config.app, 'ui_language') else "auto"
        self.services['i18n'] = I18nManager(locale_dir=locale_dir, fallback_locale="zh_CN", config_language=ui_language)
        
        # 初始化预设服务
        self.services['preset'] = PresetService(config_service=self.services['config'])
        
        # 根据配置设置日志级别
        try:
            config = self.services['config'].get_config()
            if hasattr(config, 'cli') and hasattr(config.cli, 'verbose'):
                verbose = config.cli.verbose
                self.services['log'].set_console_log_level(verbose)
        except Exception as e:
            self.logger.warning(f"设置日志级别失败: {e}")
        
        self.services['state'].set_app_ready(True)
    
    def _init_heavy_services(self):
        """在后台线程初始化非UI的重量级服务"""
        try:
            
            self.services['file'] = FileService()
            self.services['translation'] = TranslationService()
            self.services['ocr'] = OcrService()
            self.services['async'] = AsyncService()
            self.services['history'] = HistoryService()
            self.services['render_parameter'] = RenderParameterService()
            self.services['resource_manager'] = ResourceManager()  # 新的资源管理器

            
        except Exception as e:
            self.logger.error(f"后台重量级服务初始化失败: {e}")

    def _init_ui_services(self):
        """在UI主线程初始化UI相关服务"""
        # This method is now mostly obsolete as ShortcutManager and DragDropService are removed.
        # Kept for potential future UI-specific services.
    
    def _default_drop_callback(self, files):
        """默认拖拽回调"""
        state_manager = self.get_service('state')
        if state_manager:
            current_files = state_manager.get_current_files()
            current_files.extend(files)
            state_manager.set_current_files(current_files)
    
    def get_service(self, service_name: str) -> Optional[Any]:
        """获取服务实例"""
        return self.services.get(service_name)
    
    def register_service(self, name: str, service_instance: Any):
        """注册新服务"""
        self.services[name] = service_instance

    def _call_service_hook(self, service_name: str, *hook_names: str):
        """按顺序调用服务支持的关闭钩子。"""
        service = self.get_service(service_name)
        if not service:
            return

        for hook_name in hook_names:
            hook = getattr(service, hook_name, None)
            if callable(hook):
                hook()
                return

        self.logger.debug(f"服务 {service_name} 没有可用的关闭钩子: {hook_names}")
    
    def shutdown_services(self):
        """关闭所有服务"""
        
        shutdown_steps = [
            ("async", ("shutdown",)),
            ("resource_manager", ("cleanup_all", "shutdown", "cleanup")),
            ("translation", ("cleanup", "shutdown", "close")),
            ("log", ("shutdown", "cleanup", "close")),
        ]

        for service_name, hook_names in shutdown_steps:
            try:
                self._call_service_hook(service_name, *hook_names)
            except Exception as e:
                self.logger.error(f"关闭服务 {service_name} 时出错: {e}", exc_info=True)
        
        self.services.clear()
        self.initialized = False
        print("所有服务已关闭")

class ServiceManager:
    """服务管理器 - 全局服务访问点"""
    
    _instance = None
    _container = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def initialize(cls, root_dir: str, root_widget=None) -> bool:
        """初始化服务管理器"""
        if cls._container is None:
            cls._container = ServiceContainer(root_dir)
            return cls._container.initialize_services(root_widget)
        return True
    
    @classmethod
    def get_service(cls, service_name: str) -> Optional[Any]:
        """获取服务"""
        if cls._container:
            return cls._container.get_service(service_name)
        return None

    @classmethod
    def register_service(cls, name: str, service_instance: Any):
        """注册一个新服务。"""
        if cls._container:
            cls._container.register_service(name, service_instance)

    
    @classmethod
    def get_config_service(cls) -> Optional[ConfigService]:
        """获取配置服务"""
        return cls.get_service('config')
    
    @classmethod
    def get_translation_service(cls) -> Optional[TranslationService]:
        """获取翻译服务"""
        return cls.get_service('translation')
    
    @classmethod
    def get_file_service(cls) -> Optional[FileService]:
        """获取文件服务"""
        return cls.get_service('file')
    
    @classmethod
    def get_state_manager(cls) -> Optional[StateManager]:
        """获取状态管理器"""
        return cls.get_service('state')
    
    @classmethod
    def get_log_service(cls) -> Optional[LogService]:
        """获取日志服务"""
        return cls.get_service('log')
    
    @classmethod
    def get_ocr_service(cls) -> Optional[OcrService]:
        """获取OCR服务"""
        return cls.get_service('ocr')

    @classmethod
    def get_render_parameter_service(cls) -> Optional[RenderParameterService]:
        """获取渲染参数服务"""
        return cls.get_service('render_parameter')

    @classmethod
    def get_async_service(cls) -> Optional[AsyncService]:
        """获取异步服务"""
        return cls.get_service('async')

    @classmethod
    def get_history_service(cls) -> Optional[HistoryService]:
        """获取历史记录服务"""
        return cls.get_service('history')
    
    @classmethod
    def get_resource_manager(cls) -> Optional[ResourceManager]:
        """获取资源管理器"""
        return cls.get_service('resource_manager')
    
    @classmethod
    def get_i18n_manager(cls) -> Optional[I18nManager]:
        """获取国际化管理器"""
        return cls.get_service('i18n')
    
    @classmethod
    def get_preset_service(cls) -> Optional[PresetService]:
        """获取预设服务"""
        return cls.get_service('preset')
    
    @classmethod
    def shutdown(cls):
        """关闭服务管理器"""
        if cls._container:
            cls._container.shutdown_services()
            cls._container = None

# 便捷函数
def init_services(root_dir: str, root_widget=None) -> bool:
    """初始化服务的便捷函数"""
    return ServiceManager.initialize(root_dir, root_widget)

def get_config_service() -> Optional[ConfigService]:
    """获取配置服务的便捷函数"""
    return ServiceManager.get_config_service()

def get_translation_service() -> Optional[TranslationService]:
    """获取翻译服务的便捷函数"""
    return ServiceManager.get_translation_service()

def get_file_service() -> Optional[FileService]:
    """获取文件服务的便捷函数"""
    return ServiceManager.get_file_service()

def get_state_manager() -> Optional[StateManager]:
    """获取状态管理器的便捷函数"""
    return ServiceManager.get_state_manager()

def get_logger(name: str = None) -> logging.Logger:
    """获取日志器的便捷函数"""
    log_service = ServiceManager.get_log_service()
    if log_service:
        return log_service.get_logger(name)
    return logging.getLogger(name or __name__)

def get_ocr_service() -> Optional[OcrService]:
    """获取OCR服务的便捷函数"""
    return ServiceManager.get_ocr_service()

def get_render_parameter_service() -> Optional[RenderParameterService]:
    """获取渲染参数服务的便捷函数"""
    return ServiceManager.get_render_parameter_service()

def get_async_service() -> Optional[AsyncService]:
    """获取异步服务的便捷函数"""
    return ServiceManager.get_async_service()

def get_history_service() -> Optional[HistoryService]:
    """获取历史记录服务的便捷函数"""
    return ServiceManager.get_history_service()

def get_resource_manager() -> Optional[ResourceManager]:
    """获取资源管理器的便捷函数"""
    return ServiceManager.get_resource_manager()

def get_i18n_manager() -> Optional[I18nManager]:
    """获取国际化管理器的便捷函数"""
    return ServiceManager.get_i18n_manager()

def get_preset_service() -> Optional[PresetService]:
    """获取预设服务的便捷函数"""
    return ServiceManager.get_preset_service()

def shutdown_services():
    """关闭服务的便捷函数"""
    ServiceManager.shutdown()

# 依赖注入装饰器
def inject_service(service_name: str):
    """服务注入装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            service = ServiceManager.get_service(service_name)
            return func(*args, **kwargs, **{service_name: service})
        return wrapper
    return decorator

# 服务健康检查
def check_services_health() -> Dict[str, bool]:
    """检查所有服务的健康状态"""
    health_status = {}
    
    if ServiceManager._container:
        for service_name, service in ServiceManager._container.services.items():
            try:
                # 基本的健康检查
                if hasattr(service, 'is_healthy'):
                    health_status[service_name] = service.is_healthy()
                else:
                    health_status[service_name] = service is not None
            except Exception:
                health_status[service_name] = False
    
    return health_status
