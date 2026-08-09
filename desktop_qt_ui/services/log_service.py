"""
日志服务
提供结构化日志记录、日志管理和监控功能
"""
import copy
import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


_RECENT_LOGS = deque(maxlen=200)
_RECENT_LOGS_LOCK = threading.Lock()
_QUEUE_LOGGING_LOCK = threading.Lock()
_QUEUE_HANDLER: Optional["BoundedQueueHandler"] = None
_QUEUE_LISTENER: Optional[logging.handlers.QueueListener] = None
_QUEUE_DOWNSTREAM_HANDLERS: tuple[logging.Handler, ...] = ()


class RecentLogHandler(logging.Handler):
    """Collect recent records; runs on the logging listener thread."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                'level': record.levelname,
                'module': record.name,
                'message': record.getMessage(),
                'function': record.funcName,
                'line': record.lineno,
            }
            if record.exc_info or record.exc_text:
                entry['exception'] = self.format(record)
            with _RECENT_LOGS_LOCK:
                _RECENT_LOGS.append(entry)
        except Exception:
            self.handleError(record)


class BoundedQueueHandler(logging.handlers.QueueHandler):
    """Bounded queue handler that sheds low-priority records under pressure."""

    def __init__(self, log_queue: queue.Queue, warning_timeout: float = 0.05):
        super().__init__(log_queue)
        self.warning_timeout = warning_timeout
        self._accepting = True
        self._dropped_low_priority = 0
        self._drop_lock = threading.Lock()

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # A local threading.Queue needs no pickling. Defer message/exception
        # formatting to the listener instead of doing it on the GUI/worker.
        return copy.copy(record)

    def _take_dropped_count(self) -> int:
        with self._drop_lock:
            count = self._dropped_low_priority
            self._dropped_low_priority = 0
            return count

    def _restore_dropped_count(self, count: int) -> None:
        if count:
            with self._drop_lock:
                self._dropped_low_priority += count

    @staticmethod
    def _drop_summary_record(count: int) -> logging.LogRecord:
        return logging.LogRecord(
            name="desktop_qt_ui.logging",
            level=logging.WARNING,
            pathname=__file__,
            lineno=0,
            msg="日志队列曾满，已丢弃 %d 条 DEBUG/INFO 日志",
            args=(count,),
            exc_info=None,
        )

    def _enqueue_drop_summary(self, *, block: bool = False) -> None:
        count = self._take_dropped_count()
        if not count:
            return
        try:
            record = self.prepare(self._drop_summary_record(count))
            if block:
                self.queue.put(record, timeout=1.0)
            else:
                self.queue.put_nowait(record)
        except queue.Full:
            self._restore_dropped_count(count)

    def enqueue(self, record: logging.LogRecord) -> None:
        if not self._accepting:
            return

        self._enqueue_drop_summary()
        try:
            if record.levelno >= logging.WARNING:
                self.queue.put(record, timeout=self.warning_timeout)
            else:
                self.queue.put_nowait(record)
        except queue.Full:
            if record.levelno < logging.WARNING:
                with self._drop_lock:
                    self._dropped_low_priority += 1
                return

            # Keep high-priority failures visible even if the listener is wedged.
            try:
                sys.__stderr__.write(
                    f"{record.levelname}: [{record.name}] {record.getMessage()}\n"
                )
                sys.__stderr__.flush()
            except Exception:
                pass

    def stop_accepting(self) -> None:
        self._accepting = False

    def flush_dropped_summary(self) -> None:
        self._enqueue_drop_summary(block=True)


class DrainingQueueListener(logging.handlers.QueueListener):
    """QueueListener whose stop sentinel cannot be lost when the queue is full."""

    def enqueue_sentinel(self) -> None:
        self.queue.put(self._sentinel)


def configure_queue_logging(
    downstream_handlers: Iterable[logging.Handler],
    *,
    queue_size: int = 10_000,
) -> BoundedQueueHandler:
    """Install one root QueueHandler and start the shared listener thread."""
    global _QUEUE_HANDLER, _QUEUE_LISTENER, _QUEUE_DOWNSTREAM_HANDLERS

    shutdown_queue_logging()
    handlers = tuple(downstream_handlers) + (RecentLogHandler(),)
    log_queue: queue.Queue = queue.Queue(maxsize=queue_size)
    queue_handler = BoundedQueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)
    listener = DrainingQueueListener(
        log_queue,
        *handlers,
        respect_handler_level=True,
    )

    with _QUEUE_LOGGING_LOCK:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(queue_handler)
        _QUEUE_HANDLER = queue_handler
        _QUEUE_LISTENER = listener
        _QUEUE_DOWNSTREAM_HANDLERS = handlers
        listener.start()
    return queue_handler


def is_queue_logging_active() -> bool:
    with _QUEUE_LOGGING_LOCK:
        return _QUEUE_LISTENER is not None


def shutdown_queue_logging() -> None:
    """Stop producers, drain queued records, then close downstream handlers."""
    global _QUEUE_HANDLER, _QUEUE_LISTENER, _QUEUE_DOWNSTREAM_HANDLERS

    with _QUEUE_LOGGING_LOCK:
        queue_handler = _QUEUE_HANDLER
        listener = _QUEUE_LISTENER
        handlers = _QUEUE_DOWNSTREAM_HANDLERS
        if queue_handler is None and listener is None:
            return

        root_logger = logging.getLogger()
        if queue_handler is not None:
            root_logger.removeHandler(queue_handler)
            queue_handler.flush_dropped_summary()
            queue_handler.stop_accepting()
        _QUEUE_HANDLER = None
        _QUEUE_LISTENER = None
        _QUEUE_DOWNSTREAM_HANDLERS = ()

    if listener is not None:
        listener.stop()
    if queue_handler is not None:
        queue_handler.close()
    for handler in handlers:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass


class LogLevel:
    """日志级别常量"""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

class LogService:
    """日志服务"""
    
    def __init__(self, log_dir: str = "logs", app_name: str = "MangaTranslatorUI"):
        self.log_dir = log_dir
        self.app_name = app_name
        self.loggers = {}
        self.log_handlers = []
        self.max_recent_logs = _RECENT_LOGS.maxlen
        
        # 注意：不再自动创建日志目录，由调用方根据需要创建
        # 实际日志文件写入到 result 目录，由 main.py 管理
        
        # 初始化主日志器
        self._setup_main_logger()
        
    def _setup_main_logger(self):
        """设置主日志器"""
        # 初始化 manga_translator 的日志系统
        try:
            from manga_translator.utils.log import init_logging
            init_logging()
        except Exception as e:
            logging.warning(f"无法初始化manga_translator日志: {e}")

        # Library logging initialization may lower the root level. The queue must
        # continue accepting DEBUG records so the file handler can decide.
        logging.getLogger().setLevel(logging.DEBUG)
        
        logger = logging.getLogger(self.app_name)
        logger.setLevel(logging.DEBUG)  # 设为 DEBUG 以允许所有日志通过
        logger.propagate = True
        
        # 清除现有处理器
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        self.console_handler = None

        # 主程序已经配置 root 控制台输出时，不再为 UI logger 额外挂一份 stdout handler，
        # 否则同一条日志会先在这里打印，再向 root 传播后再打印一次。
        if not self._get_root_console_handlers():
            class FlushingStreamHandler(logging.StreamHandler):
                def emit(self, record):
                    super().emit(record)
                    self.flush()

            console_handler = FlushingStreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            simple_formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%H:%M:%S'
            )
            console_handler.setFormatter(simple_formatter)
            logger.addHandler(console_handler)
            self.console_handler = console_handler
        
        # In the desktop app recent logs are already a downstream listener
        # handler. Keep a direct fallback for isolated service/tests only.
        memory_handler = None
        if not is_queue_logging_active():
            memory_handler = RecentLogHandler()
            logger.addHandler(memory_handler)
        
        self.loggers[self.app_name] = logger
        if self.console_handler is not None:
            self.log_handlers.append(self.console_handler)
        if memory_handler is not None:
            self.log_handlers.append(memory_handler)

    def _get_root_console_handlers(self) -> List[logging.Handler]:
        """返回 root logger 上负责控制台输出的 handler。"""
        with _QUEUE_LOGGING_LOCK:
            queued_handlers = list(_QUEUE_DOWNSTREAM_HANDLERS)
        if queued_handlers:
            return [
                handler
                for handler in queued_handlers
                if not isinstance(handler, (logging.FileHandler, RecentLogHandler))
                and getattr(handler, 'stream', None) is not None
            ]

        root_logger = logging.getLogger()
        handlers: List[logging.Handler] = []
        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                continue
            if getattr(handler, 'stream', None) is None:
                continue
            handlers.append(handler)
        return handlers
    
    def set_console_log_level(self, verbose: bool = False):
        """
        根据 verbose 配置设置控制台日志级别
        
        Args:
            verbose: 是否启用详细日志（DEBUG 级别）
        """
        level = logging.DEBUG if verbose else logging.INFO
        
        # 设置控制台处理器级别
        if self.console_handler is not None:
            self.console_handler.setLevel(level)

        for handler in self._get_root_console_handlers():
            handler.setLevel(level)
        
        # 根日志器和 QueueHandler 始终保持 DEBUG；由监听线程上的各目标
        # handler 决定是否输出，避免正常模式把文件 DEBUG 一并截断。
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        for handler in root_logger.handlers:
            if isinstance(handler, logging.handlers.QueueHandler):
                handler.setLevel(logging.DEBUG)
        
        # 设置主应用logger级别
        if self.app_name in self.loggers:
            self.loggers[self.app_name].setLevel(logging.DEBUG)
        
        # 同步设置manga_translator的日志级别
        logging.getLogger('manga-translator').setLevel(level)
        
    
    def get_logger(self, name: str = None) -> logging.Logger:
        """获取日志器"""
        if name is None:
            name = self.app_name
        
        if name not in self.loggers:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)  # 设为 DEBUG 以允许所有日志通过
            
            # 如果是子日志器，继承主日志器的配置
            if name != self.app_name:
                parent_logger = self.loggers.get(self.app_name)
                if parent_logger:
                    logger.parent = parent_logger
            
            self.loggers[name] = logger
        
        return self.loggers[name]
    
    def log_operation(self, operation: str, details: Dict[str, Any] = None, level: int = LogLevel.INFO):
        """记录操作日志"""
        logger = self.get_logger()
        message = f"Operation: {operation}"
        
        if details:
            message += f" | Details: {json.dumps(details, ensure_ascii=False)}"
        
        logger.log(level, message)
    
    def log_error(self, error: Exception, context: Dict[str, Any] = None, operation: str = None):
        """记录错误日志"""
        logger = self.get_logger()
        
        message = f"Error: {str(error)}"
        if operation:
            message = f"Operation '{operation}' failed: {str(error)}"
        
        if context:
            message += f" | Context: {json.dumps(context, ensure_ascii=False)}"
        
        logger.error(message, exc_info=True)
    
    def log_translation_start(self, files: List[str], config: Dict[str, Any]):
        """记录翻译开始"""
        self.log_operation("translation_start", {
            'file_count': len(files),
            'files': [os.path.basename(f) for f in files],
            'translator': config.get('translator', 'unknown'),
            'target_lang': config.get('target_lang', 'unknown')
        })
    
    def log_translation_complete(self, results: List[Dict[str, Any]], duration: float):
        """记录翻译完成"""
        success_count = sum(1 for r in results if r.get('success', False))
        self.log_operation("translation_complete", {
            'total_files': len(results),
            'success_count': success_count,
            'failure_count': len(results) - success_count,
            'duration_seconds': round(duration, 2)
        })
    
    def log_config_change(self, config_path: str, changes: Dict[str, Any] = None):
        """记录配置变更"""
        self.log_operation("config_change", {
            'config_path': config_path,
            'changes': changes
        })
    
    def log_file_operation(self, operation: str, file_path: str, success: bool = True, error: str = None):
        """记录文件操作"""
        details = {
            'file_path': file_path,
            'success': success
        }
        if error:
            details['error'] = error
        
        level = LogLevel.INFO if success else LogLevel.ERROR
        self.log_operation(f"file_{operation}", details, level)
    
    def log_performance(self, operation: str, duration: float, details: Dict[str, Any] = None):
        """记录性能指标"""
        perf_details = {
            'duration_seconds': round(duration, 3)
        }
        if details:
            perf_details.update(details)
        
        self.log_operation(f"performance_{operation}", perf_details)
    
    def get_recent_logs(self, level: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近的日志"""
        with _RECENT_LOGS_LOCK:
            logs = list(_RECENT_LOGS)
        
        if level:
            logs = [log for log in logs if log.get('level') == level.upper()]
        
        return logs[-limit:] if limit > 0 else logs
    
    def get_log_summary(self) -> Dict[str, Any]:
        """获取日志摘要"""
        with _RECENT_LOGS_LOCK:
            logs = list(_RECENT_LOGS)
        
        summary = {
            'total_logs': len(logs),
            'levels': {},
            'recent_errors': []
        }
        
        for log in logs:
            level = log.get('level', 'UNKNOWN')
            summary['levels'][level] = summary['levels'].get(level, 0) + 1
            
            if level == 'ERROR':
                summary['recent_errors'].append({
                    'timestamp': log.get('timestamp'),
                    'message': log.get('message'),
                    'module': log.get('module')
                })
        
        # 只保留最近的10个错误
        summary['recent_errors'] = summary['recent_errors'][-10:]
        
        return summary
    
    def clear_recent_logs(self):
        """清除最近的日志"""
        with _RECENT_LOGS_LOCK:
            _RECENT_LOGS.clear()
    
    def set_log_level(self, level: int):
        """设置日志级别"""
        for logger in self.loggers.values():
            logger.setLevel(level)
    
    def export_logs(self, output_path: str, level: str = None, start_time: datetime = None, end_time: datetime = None) -> bool:
        """导出日志到文件"""
        try:
            logs = self.get_recent_logs(level=level)
            
            # 时间过滤
            if start_time or end_time:
                filtered_logs = []
                for log in logs:
                    log_time = datetime.fromisoformat(log.get('timestamp', ''))
                    if start_time and log_time < start_time:
                        continue
                    if end_time and log_time > end_time:
                        continue
                    filtered_logs.append(log)
                logs = filtered_logs
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
            
            self.log_operation("export_logs", {
                'output_path': output_path,
                'log_count': len(logs)
            })
            return True
            
        except Exception as e:
            self.log_error(e, operation="export_logs")
            return False
    
    def cleanup_old_logs(self, days: int = 30):
        """清理旧日志文件"""
        try:
            import time
            current_time = time.time()
            cutoff_time = current_time - (days * 24 * 3600)
            
            cleaned_files = []
            for root, dirs, files in os.walk(self.log_dir):
                for file in files:
                    if file.endswith('.log') or file.endswith('.log.1'):
                        file_path = os.path.join(root, file)
                        if os.path.getmtime(file_path) < cutoff_time:
                            os.remove(file_path)
                            cleaned_files.append(file_path)
            
            if cleaned_files:
                self.log_operation("cleanup_logs", {
                    'cleaned_files': len(cleaned_files),
                    'days': days
                })
            
        except Exception as e:
            self.log_error(e, operation="cleanup_logs")
    
    def shutdown(self):
        """关闭日志服务"""
        for handler in self.log_handlers:
            try:
                handler.close()
            except Exception:
                pass
        
        self.log_handlers.clear()
        self.loggers.clear()

# 全局日志服务实例
_log_service = None

def get_log_service() -> LogService:
    """获取全局日志服务实例"""
    global _log_service
    if _log_service is None:
        _log_service = LogService()
    return _log_service

def setup_logging(log_dir: str = "logs", app_name: str = "MangaTranslatorUI"):
    """设置全局日志"""
    global _log_service
    _log_service = LogService(log_dir, app_name)
    return _log_service
