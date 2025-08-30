"""
Async Service
Provides a way to run asyncio tasks from a synchronous (Tkinter) context.
"""
import asyncio
import threading
from typing import Coroutine, Optional

class AsyncService:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AsyncService")
        self._running = False
        self._task_queue = []
        self._max_concurrent_tasks = 10  # 限制并发任务数量
        self._active_tasks = set()
        
        # 设置事件循环调试和性能优化
        self._loop.set_debug(False)  # 关闭调试模式以提高性能
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._running = True
        
        # 设置任务清理器
        self._loop.call_later(30, self._cleanup_completed_tasks)
        
        try:
            self._loop.run_forever()
        except Exception as e:
            import logging
            logging.error(f"AsyncService event loop error: {e}")
        finally:
            self._running = False

    def submit_task(self, coro: Coroutine):
        """Submits a coroutine to be run on the asyncio event loop."""
        if not self._running:
            return None
            
        # 检查活跃任务数量
        if len(self._active_tasks) >= self._max_concurrent_tasks:
            # 如果任务过多，等待一些任务完成
            self._cleanup_completed_tasks()
        
        future = asyncio.run_coroutine_threadsafe(self._wrap_task(coro), self._loop)
        return future
    
    async def _wrap_task(self, coro):
        """包装任务以便于追踪和清理"""
        task = asyncio.current_task()
        self._active_tasks.add(task)
        
        try:
            result = await coro
            return result
        except Exception as e:
            import logging
            logging.error(f"Async task error: {e}")
            raise
        finally:
            self._active_tasks.discard(task)
    
    def _cleanup_completed_tasks(self):
        """清理已完成的任务"""
        completed_tasks = [task for task in self._active_tasks if task.done()]
        for task in completed_tasks:
            self._active_tasks.discard(task)
        
        # 定期清理
        if self._running:
            self._loop.call_later(30, self._cleanup_completed_tasks)

    def shutdown(self):
        self._running = False
        self._loop.call_soon_threadsafe(self._loop.stop)

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
