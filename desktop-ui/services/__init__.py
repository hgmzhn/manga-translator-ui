"""
服务初始化模块
统一管理和初始化所有服务组件
"""
import os
import logging
from typing import Dict, Any, Optional

# 导入所有服务
from .config_service import ConfigService
from .translation_service import TranslationService
from .file_service import FileService
from .state_manager import StateManager, get_state_manager as get_state_manager_singleton
from .log_service import LogService, get_log_service, setup_logging
from .drag_drop_service import DragDropHandler, MultiWidgetDragDropHandler
from .shortcut_manager import ShortcutManager
from .progress_manager import ProgressManager, get_progress_manager
from .ocr_service import OcrService, get_ocr_service

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
            # 第一阶段：同步初始化基础服务
            self._init_essential_services()
            
            # 第二阶段：在后台线程初始化非UI的重量级服务
            import threading
            threading.Thread(target=self._init_heavy_services, daemon=True).start()

            # 第三阶段：在UI主线程延迟初始化UI相关服务
            if self._root_widget:
                self._root_widget.after(100, self._init_ui_services)
            
            self.initialized = True
            return True
            
        except Exception as e:
            self.logger.error(f"服务初始化失败: {e}")
            return False
    
    def _init_essential_services(self):
        """初始化必需的基础服务"""
        self.services['log'] = get_log_service()
        self.services['state'] = get_state_manager_singleton()
        self.services['config'] = ConfigService(self.root_dir)
        self.services['state'].set_app_ready(True)
        self.logger.info("基础服务初始化完成")
    
    def _init_heavy_services(self):
        """在后台线程初始化非UI的重量级服务"""
        try:
            import time
            
            self.services['progress'] = get_progress_manager()
            time.sleep(0.01)
            
            self.services['file'] = FileService()
            time.sleep(0.01)
            
            self.services['translation'] = TranslationService()
            time.sleep(0.01)
            
            self.services['ocr'] = OcrService()
            time.sleep(0.01)
            
            self.logger.info("后台重量级服务初始化完成")
            
        except Exception as e:
            self.logger.error(f"后台重量级服务初始化失败: {e}")

    def _init_ui_services(self):
        """在UI主线程初始化UI相关服务"""
        try:
            if self._root_widget:
                from .drag_drop_service import MultiWidgetDragDropHandler
                from .shortcut_manager import ShortcutManager
                
                self.services['shortcut'] = ShortcutManager(self._root_widget)
                self.services['drag_drop'] = MultiWidgetDragDropHandler(
                    self._default_drop_callback,
                    self.services.get('file')
                )
                self.logger.info("UI相关服务初始化完成")
            else:
                self.logger.warning("无法初始化UI服务：缺少root_widget")
        except Exception as e:
            self.logger.error(f"UI服务初始化失败: {e}")
    
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
        self.logger.info(f"注册服务: {name}")
    
    def shutdown_services(self):
        """关闭所有服务"""
        self.logger.info("开始关闭服务...")
        
        try:
            # 翻译服务清理
            translation_service = self.get_service('translation')
            if translation_service:
                translation_service.cleanup()
            
            # 拖拽服务清理
            drag_drop_service = self.get_service('drag_drop')
            if drag_drop_service:
                drag_drop_service.destroy_all()
            
            # 进度管理器清理
            progress_manager = self.get_service('progress')
            if progress_manager:
                progress_manager.cleanup_completed_tasks()
            
            # 日志服务关闭
            log_service = self.get_service('log')
            if log_service:
                log_service.shutdown()
            
        except Exception as e:
            print(f"关闭服务时出错: {e}")
        
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
    def get_shortcut_manager(cls) -> Optional[ShortcutManager]:
        """获取快捷键管理器"""
        return cls.get_service('shortcut')
    
    @classmethod
    def get_progress_manager(cls) -> Optional[ProgressManager]:
        """获取进度管理器"""
        return cls.get_service('progress')
    
    @classmethod
    def get_drag_drop_service(cls) -> Optional[MultiWidgetDragDropHandler]:
        """获取拖拽服务"""
        return cls.get_service('drag_drop')
    
    @classmethod
    def get_ocr_service(cls) -> Optional[OcrService]:
        """获取OCR服务"""
        return cls.get_service('ocr')
    
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

def get_shortcut_manager() -> Optional[ShortcutManager]:
    """获取快捷键管理器的便捷函数"""
    return ServiceManager.get_shortcut_manager()

def get_progress_manager() -> Optional[ProgressManager]:
    """获取进度管理器的便捷函数"""
    return ServiceManager.get_progress_manager()

def get_ocr_service() -> Optional[OcrService]:
    """获取OCR服务的便捷函数"""
    return ServiceManager.get_ocr_service()

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