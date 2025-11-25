"""
Manga Translator Web API Server
提供 HTTP REST API 端点，支持多种翻译工作流程
"""

# 导出主要的类和函数，方便外部导入
from manga_translator.server.main import app
from manga_translator.server.request_extraction import (
    TranslateRequest,
    BatchTranslateRequest,
    get_ctx,
    get_batch_ctx,
    while_streaming,
)

from manga_translator.server.instance import ExecutorInstance, executor_instances
from manga_translator.server.myqueue import task_queue, QueueElement, BatchQueueElement
from manga_translator.server.to_json import TranslationResponse, to_translation

__all__ = [
    # FastAPI app
    'app',
    
    # Request models
    'TranslateRequest',
    'BatchTranslateRequest',
    
    # Translation functions
    'get_ctx',
    'get_batch_ctx',
    'while_streaming',
    

    
    # Instance management
    'ExecutorInstance',
    'executor_instances',
    
    # Queue management
    'task_queue',
    'QueueElement',
    'BatchQueueElement',
    
    # Response models
    'TranslationResponse',
    'to_translation',
]

__version__ = '2.0.0'
__author__ = 'Manga Translator Team'
