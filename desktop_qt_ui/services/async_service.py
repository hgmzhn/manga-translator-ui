"""Qt 同步上下文下的后台协程提交服务。"""
import logging
from typing import Coroutine, Optional

# 使用绝对导入避免相对导入问题
from desktop_qt_ui.editor.core import AsyncJobManager


class AsyncService:
    """AsyncJobManager 的兼容层。"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._job_manager = AsyncJobManager()
        self._running = True

    def submit_task(self, coro: Coroutine):
        """提交协程到后台事件循环。"""
        if not self._running:
            self.logger.warning("AsyncService is not running, task ignored")
            coro.close()
            return None

        try:
            future = self._job_manager.submit_coroutine(coro)
            if future is None:
                self.logger.error("Event loop is not available")
                coro.close()
                return None

            self.logger.debug("Task submitted to event loop")
            return future
        except Exception as e:
            try:
                coro.close()
            except Exception:
                pass
            self.logger.error(f"Failed to submit task: {e}", exc_info=True)
            return None
    
    def cancel_all_tasks(self):
        """取消所有活跃的异步任务（非阻塞）"""
        if not self._running:
            return
        
        try:
            self._job_manager.cancel_all()
        except Exception as e:
            self.logger.error(f"Error cancelling tasks: {e}")

    def shutdown(self):
        """关闭服务"""
        self._running = False
        self._job_manager.shutdown(wait=False)

# Global instance
_async_service: Optional[AsyncService] = None

def get_async_service() -> AsyncService:
    global _async_service
    if _async_service is None:
        _async_service = AsyncService()
    return _async_service

def shutdown_async_service():
    global _async_service
    if _async_service:
        _async_service.shutdown()
        _async_service = None
