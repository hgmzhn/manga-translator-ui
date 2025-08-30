"""
应用业务逻辑层
处理应用的核心业务逻辑，与UI层分离
"""
import os
import json
import asyncio
import threading
from typing import List, Dict, Optional, Callable, Any
import logging
from dataclasses import dataclass

# 导入服务层
from services import (
    get_config_service, get_translation_service, get_file_service,
    get_state_manager, get_logger, get_progress_manager
)
from services.state_manager import AppStateKey
from services.translation_service import TranslationProgress

@dataclass
class AppConfig:
    """应用配置信息"""
    window_size: tuple = (1200, 800)
    theme: str = "dark"
    language: str = "zh_CN"
    auto_save: bool = True
    max_recent_files: int = 10

class AppLogic:
    """应用业务逻辑控制器"""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self.config_service = get_config_service()
        self.translation_service = get_translation_service()
        self.file_service = get_file_service()
        self.state_manager = get_state_manager()
        self.progress_manager = get_progress_manager()
        
        self.app_config = AppConfig()
        self.ui_callbacks: Dict[str, Callable] = {}
        self.logger.info("应用业务逻辑初始化完成")
    
    def register_ui_callback(self, event_name: str, callback: Callable):
        self.ui_callbacks[event_name] = callback
    
    def notify_ui(self, event_name: str, data: Any = None):
        callback = self.ui_callbacks.get(event_name)
        if callback:
            try:
                callback(data)
            except Exception as e:
                self.logger.error(f"UI回调执行失败 {event_name}: {e}")

    # region 配置管理
    def load_config_file(self, config_path: str) -> bool:
        try:
            success = self.config_service.load_config_file(config_path)
            if success:
                config = self.config_service.get_config()
                self.state_manager.set_current_config(config)
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, config_path)
                self.logger.info(f"配置文件加载成功: {config_path}")
                self.notify_ui('config_loaded', config)
                return True
            else:
                self.logger.error(f"配置文件加载失败: {config_path}")
                return False
        except Exception as e:
            self.logger.error(f"加载配置文件异常: {e}")
            return False
    
    def save_config_file(self, config_path: str = None) -> bool:
        try:
            success = self.config_service.save_config_file(config_path)
            if success:
                self.logger.info("配置文件保存成功")
                self.notify_ui('config_saved')
                return True
            return False
        except Exception as e:
            self.logger.error(f"保存配置文件异常: {e}")
            return False
    
    def update_config(self, config_updates: Dict[str, Any]) -> bool:
        try:
            self.config_service.update_config(config_updates)
            updated_config = self.config_service.get_config()
            self.state_manager.set_current_config(updated_config)
            self.logger.info("配置更新成功")
            self.notify_ui('config_updated', updated_config)
            return True
        except Exception as e:
            self.logger.error(f"更新配置异常: {e}")
            return False
    # endregion

    # region 文件管理
    def add_files(self, file_paths: List[str]) -> int:
        try:
            valid_files = self.file_service.filter_valid_image_files(file_paths)
            current_files = self.state_manager.get_current_files()
            new_files = [f for f in valid_files if f not in current_files]
            current_files.extend(new_files)
            self.state_manager.set_current_files(current_files)
            self.logger.info(f"添加了 {len(new_files)} 个文件")
            self.notify_ui('files_added', new_files)
            return len(new_files)
        except Exception as e:
            self.logger.error(f"添加文件异常: {e}")
            return 0

    def clear_file_list(self) -> bool:
        try:
            self.state_manager.clear_files()
            self.logger.info("文件列表已清空")
            self.notify_ui('files_cleared')
            return True
        except Exception as e:
            self.logger.error(f"清空文件列表异常: {e}")
            return False
    # endregion

    # region 核心任务逻辑
    def _build_backend_args(self) -> Any:
        """
        根据当前的UI设置，动态构建用于后端调用的命令行参数对象。
        这是决定核心工作流（翻译 vs. 修图）的关键。
        """
        try:
            from manga_translator.args import reparse
            
            current_config = self.config_service.get_config()
            args_list = []

            # 映射UI设置到命令行参数
            # 这里的 key (例如 'load_text') 需要与UI设置中的key一致
            if current_config.get('load_text', False):
                args_list.append('--load-text')
            if current_config.get('save_text', False):
                args_list.append('--save-text')
            if current_config.get('prep_manual', False):
                args_list.append('--prep-manual')
            
            self.logger.info(f"为后端动态构建参数: {args_list}")
            return reparse(args_list)
        except ImportError:
            self.logger.error("无法导入后端参数解析器 'reparse'。")
            return None
        except Exception as e:
            self.logger.error(f"构建后端参数时出错: {e}")
            return None

    def start_backend_task(self) -> bool:
        """开始执行后端任务（翻译或修图）"""
        try:
            files = self.state_manager.get_current_files()
            if not files:
                self.logger.warning("文件列表为空，任务中止")
                self.state_manager.add_error_message("请先添加文件再开始任务")
                return False

            # 1. 获取最新的JSON配置 (用于翻译器本身)
            translator_json_config = self.config_service.get_config()
            if not translator_json_config:
                self.logger.error("无法获取JSON配置，任务中止")
                self.state_manager.add_error_message("无法获取JSON配置")
                return False

            # 2. 动态构建决定工作流的 'args' 上下文
            workflow_args_context = self._build_backend_args()
            if workflow_args_context is None:
                self.logger.error("无法构建后端工作流参数，任务中止")
                self.state_manager.add_error_message("无法构建后端参数")
                return False

            # 3. 检查翻译器服务是否就绪
            if not self.translation_service.is_translator_ready():
                if not self.translation_service.initialize_translator():
                    self.logger.error("翻译器初始化失败")
                    self.state_manager.add_error_message("翻译器初始化失败")
                    return False
            
            # 4. 设置UI状态并启动后台线程
            task_id = "backend_task"
            self.progress_manager.create_task(task_id, len(files), "准备执行任务...")
            self.state_manager.set_translating(True)
            self.state_manager.set_status_message("正在执行任务...")
            
            def progress_callback(progress: TranslationProgress):
                self.progress_manager.update_task(task_id, progress.current_step, progress.message)
                self.state_manager.set_translation_progress(progress.percentage)
            
            threading.Thread(
                target=self._run_backend_task_async,
                args=(files, task_id, progress_callback, translator_json_config, workflow_args_context),
                daemon=True
            ).start()
            
            self.logger.info(f"开始为 {len(files)} 个文件执行后端任务")
            self.notify_ui('task_started')
            
            return True
            
        except Exception as e:
            self.logger.error(f"开始任务时发生异常: {e}")
            self.state_manager.set_translating(False)
            return False

    def _run_backend_task_async(self, files: List[str], task_id: str, progress_callback: Callable, config: Any, args: Any):
        """异步执行后端任务"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            results = loop.run_until_complete(
                self.translation_service.translate_batch_async(
                    files,
                    progress_callback,
                    config=config,
                    args=args
                )
            )
            
            self._handle_task_results(results, task_id)
            
        except Exception as e:
            self.logger.error(f"后端任务执行异常: {e}")
            self.progress_manager.error_task(task_id, str(e))
            self.state_manager.set_translating(False)
            self.state_manager.add_error_message(f"任务失败: {str(e)}")
        finally:
            try:
                loop.close()
            except:
                pass
    
    def _handle_task_results(self, results: List[Any], task_id: str):
        """处理后端任务结果"""
        try:
            # 假设 result 对象有 .success 和 .error_message 属性
            success_count = sum(1 for r in results if r.success)
            failure_count = len(results) - success_count
            
            self.state_manager.set_translating(False)
            self.state_manager.set_translation_progress(100.0)
            
            if failure_count == 0:
                message = f"任务完成！成功处理 {success_count} 个文件"
            else:
                message = f"任务完成！成功 {success_count} 个，失败 {failure_count} 个"
                for result in results:
                    if not result.success:
                        error_msg = f"任务失败: {os.path.basename(result.image_path)} - {result.error_message}"
                        self.state_manager.add_error_message(error_msg)

            self.state_manager.set_status_message(message)
            self.progress_manager.complete_task(task_id, message)
            
            self.logger.info(f"任务结果: 成功 {success_count}, 失败 {failure_count}")
            self.notify_ui('task_completed', {
                'results': results,
                'success_count': success_count,
                'failure_count': failure_count
            })
            
        except Exception as e:
            self.logger.error(f"处理任务结果异常: {e}")

    def stop_task(self) -> bool:
        """停止当前任务"""
        try:
            self.translation_service.stop_translation() # 假设服务有停止方法
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("任务已停止")
            self.logger.info("任务已停止")
            self.notify_ui('task_stopped')
            return True
        except Exception as e:
            self.logger.error(f"停止任务异常: {e}")
            return False
    # endregion

    # region 应用生命周期
    def initialize(self) -> bool:
        """初始化应用"""
        try:
            default_config_path = self.config_service.get_default_config_path()
            if os.path.exists(default_config_path):
                self.load_config_file(default_config_path)
            
            self.state_manager.set_app_ready(True)
            self.state_manager.set_status_message("就绪")
            self.logger.info("应用初始化完成")
            return True
        except Exception as e:
            self.logger.error(f"应用初始化异常: {e}")
            return False
    
    def shutdown(self):
        """关闭应用"""
        try:
            if self.state_manager.is_translating():
                self.stop_task()
            if self.translation_service:
                self.translation_service.cleanup()
            self.logger.info("应用正常关闭")
        except Exception as e:
            self.logger.error(f"应用关闭异常: {e}")
    # endregion