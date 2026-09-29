"""
日志管理模块

负责日志队列管理、任务日志隔离和日志导出功能。
"""

import contextvars
import logging
import threading
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

# 基于任务ID的日志队列（每个任务有独立的日志队列）
task_logs = defaultdict(lambda: deque(maxlen=1000))  # 每个任务最多保存1000条日志
task_logs_lock = threading.Lock()

# 全局日志队列（用于管理员查看所有日志）
# 限制为1000条，避免日志过多导致内存占用和卡顿
global_log_queue = deque(maxlen=1000)

# 当前任务ID的线程本地存储
current_task_id = contextvars.ContextVar('current_task_id', default=None)

# 当前会话ID的上下文变量（用于按会话过滤日志）
current_session_id = contextvars.ContextVar('current_session_id', default=None)


def generate_task_id() -> str:
    """生成唯一的任务ID"""
    return str(uuid.uuid4())


def set_task_id(task_id: str):
    """设置当前任务ID"""
    current_task_id.set(task_id)


def get_task_id() -> Optional[str]:
    """获取当前任务ID"""
    return current_task_id.get()


def set_session_id(session_id: str):
    """设置当前会话ID"""
    current_session_id.set(session_id)


def get_session_id() -> Optional[str]:
    """获取当前会话ID"""
    return current_session_id.get()


def add_log(message: str, level: str = "INFO", task_id: Optional[str] = None, session_id: Optional[str] = None, skip_print: bool = False):
    """
    添加日志到队列（支持任务隔离和会话隔离）
    
    Args:
        message: 日志消息
        level: 日志级别
        task_id: 任务ID（可选，如果不提供则从上下文获取）
        session_id: 会话ID（可选，如果不提供则从上下文获取）
        skip_print: 是否跳过控制台输出（避免与 logging handler 重复输出）
    """
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message
    }
    
    # 如果没有指定task_id，尝试从上下文获取
    if task_id is None:
        task_id = get_task_id()
    
    # 如果没有指定session_id，尝试从上下文获取
    if session_id is None:
        session_id = get_session_id()
    
    # 添加会话ID到日志条目
    if session_id:
        log_entry['session_id'] = session_id
    
    with task_logs_lock:
        # 添加到全局日志队列
        global_log_queue.append(log_entry)
        
        # 如果有task_id，也添加到任务专属日志队列
        if task_id:
            log_entry_with_id = log_entry.copy()
            log_entry_with_id['task_id'] = task_id
            task_logs[task_id].append(log_entry_with_id)
    
    # 同时输出到控制台（除非 skip_print=True，避免与 logging handler 重复）
    if not skip_print:
        task_prefix = task_id[:8] if task_id else 'GLOBAL'
        session_prefix = f" S:{session_id[:8]}" if session_id else ""
        print(f"[{level}] [{task_prefix}{session_prefix}] {message}")


def get_logs(level: Optional[str] = None, limit: int = 100, task_id: Optional[str] = None, session_id: Optional[str] = None) -> list:
    """
    获取日志
    
    Args:
        level: 日志级别过滤（INFO, WARNING, ERROR等）
        limit: 返回的日志数量限制
        task_id: 任务ID（如果指定，只返回该任务的日志）
        session_id: 会话ID（如果指定，只返回该会话的日志）
    
    Returns:
        日志列表
    """
    with task_logs_lock:
        if task_id:
            # 返回指定任务的日志
            logs = list(task_logs.get(task_id, []))
        else:
            # 返回全局日志
            logs = list(global_log_queue)
    
    # 按会话ID过滤
    if session_id:
        logs = [log for log in logs if log.get('session_id') == session_id]
    
    # 按级别过滤
    if level and level.lower() != 'all':
        logs = [log for log in logs if log['level'].lower() == level.lower()]
    
    # 限制数量（返回最新的）
    if len(logs) > limit:
        logs = logs[-limit:]
    
    return logs


def get_task_logs(task_id: str, limit: int = 50) -> list:
    """
    获取指定任务的日志（简化接口）
    
    Args:
        task_id: 任务ID
        limit: 返回的日志数量限制
    
    Returns:
        日志列表
    """
    return get_logs(task_id=task_id, limit=limit)


def export_logs(task_id: Optional[str] = None) -> tuple[str, str]:
    """
    导出日志为文本文件
    
    Args:
        task_id: 任务ID（可选）
    
    Returns:
        (filename, log_text) 元组
    """
    with task_logs_lock:
        if task_id:
            logs = list(task_logs.get(task_id, []))
            filename = f"logs_{task_id[:8]}.txt"
        else:
            logs = list(global_log_queue)
            from datetime import timezone
            filename = f"logs_all_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
    
    # 生成日志文本
    log_text = "\n".join([
        f"[{log['timestamp']}] [{log['level']}] {log['message']}"
        for log in logs
    ])
    
    return filename, log_text


class WebLogHandler(logging.Handler):
    """自定义日志处理器，捕获manga_translator的日志"""
    
    def __init__(self):
        super().__init__()
        # 过滤掉不需要的日志源
        self.ignored_loggers = {'uvicorn.access', 'uvicorn.error', 'httpcore', 'httpx'}
    
    def emit(self, record):
        try:
            # 跳过 uvicorn 访问日志等噪音日志
            if record.name in self.ignored_loggers:
                return
            
            msg = self.format(record)
            # 提取日志级别和消息
            level = record.levelname
            # 从上下文获取task_id和session_id
            task_id = get_task_id()
            session_id = get_session_id()
            # skip_print=True 避免与 logging 的 root handler 重复输出
            add_log(msg, level, task_id, session_id, skip_print=True)
        except Exception:
            self.handleError(record)


# 标记是否已经设置过日志处理器
_log_handler_initialized = False


def setup_log_handler():
    """设置日志处理器"""
    global _log_handler_initialized
    
    # 防止重复初始化
    if _log_handler_initialized:
        return
    
    # 初始化翻译器的日志系统（确保 manga-translator logger 被正确设置）
    try:
        from manga_translator.utils.log import init_logging
        init_logging()
    except ImportError:
        pass
    
    web_log_handler = WebLogHandler()
    formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    web_log_handler.setFormatter(formatter)
    web_log_handler.setLevel(logging.INFO)  # 捕获INFO及以上级别的日志
    
    # 添加到 manga_translator logger（下划线命名空间，用于服务器模块）
    mt_logger = logging.getLogger('manga_translator')
    mt_logger.addHandler(web_log_handler)
    mt_logger.propagate = False
    if mt_logger.level == logging.NOTSET or mt_logger.level > logging.INFO:
        mt_logger.setLevel(logging.INFO)
    
    # 添加到 manga-translator logger（连字符命名空间，用于翻译器等核心模块）
    # 这是翻译器、OCR、检测等模块使用的命名空间
    mt_hyphen_logger = logging.getLogger('manga-translator')
    mt_hyphen_logger.addHandler(web_log_handler)
    # 不设置 propagate = False，让子 logger 的日志能传播到这里
    if mt_hyphen_logger.level == logging.NOTSET or mt_hyphen_logger.level > logging.INFO:
        mt_hyphen_logger.setLevel(logging.INFO)
    
    # 确保子模块的日志也能被捕获
    submodules = ['translators', 'detection', 'ocr', 'inpainting', 'rendering', 'upscaling', 'colorization']
    
    # 翻译器类名列表（这些是实际使用的 logger 名称）
    translator_names = [
        'OpenAITranslator', 'OpenAIHighQualityTranslator', 
        'GeminiTranslator', 'GeminiHighQualityTranslator',
        'VertexHighQualityTranslator',
        'SakuraTranslator', 'Qwen2Translator',
        'DeepLTranslator', 'GoogleTranslator', 'BaiduTranslator',
        'PapagoTranslator', 'YandexTranslator', 'ChatGPTTranslator'
    ]
    
    for submodule in submodules:
        # 下划线命名空间
        sub_logger = logging.getLogger(f'manga_translator.{submodule}')
        if sub_logger.level == logging.NOTSET or sub_logger.level > logging.INFO:
            sub_logger.setLevel(logging.INFO)
        # 连字符命名空间
        sub_logger_hyphen = logging.getLogger(f'manga-translator.{submodule}')
        if sub_logger_hyphen.level == logging.NOTSET or sub_logger_hyphen.level > logging.INFO:
            sub_logger_hyphen.setLevel(logging.INFO)
    
    # 为每个翻译器类名设置 logger 级别
    for name in translator_names:
        translator_logger = logging.getLogger(f'manga-translator.{name}')
        if translator_logger.level == logging.NOTSET or translator_logger.level > logging.INFO:
            translator_logger.setLevel(logging.INFO)
    
    _log_handler_initialized = True
    
    # 添加一条测试日志确认系统工作
    add_log("Logging system initialized", "INFO")
