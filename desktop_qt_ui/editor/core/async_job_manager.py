"""异步事件循环管理器。"""

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Coroutine, Optional, Set


class AsyncJobManager:
    """管理编辑器后台协程使用的专用事件循环线程。"""
    
    def __init__(self):
        """初始化异步任务管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 由 run_coroutine_threadsafe 返回的 Future 集合
        self._futures: Set[Future] = set()
        self._lock = threading.RLock()
        
        # 事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stopping = False
        
        # 启动事件循环
        self._start_event_loop()
    
    def _start_event_loop(self) -> None:
        """启动事件循环线程"""
        if self._running:
            return
        
        def run_loop():
            """在线程中运行事件循环"""
            import sys
            # 在Windows上的工作线程中，需要手动初始化Windows Socket
            if sys.platform == 'win32':
                # 使用ctypes直接调用WSAStartup
                import ctypes
                try:
                    WSADATA_SIZE = 400
                    wsa_data = ctypes.create_string_buffer(WSADATA_SIZE)
                    ws2_32 = ctypes.WinDLL('ws2_32')
                    ws2_32.WSAStartup(0x0202, wsa_data)
                except Exception:
                    pass

                self._loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
            else:
                self._loop = asyncio.new_event_loop()

            asyncio.set_event_loop(self._loop)

            def handle_loop_exception(loop, context):
                exc = context.get("exception")
                if (
                    self._stopping
                    and isinstance(exc, ConnectionAbortedError)
                    and getattr(exc, "winerror", None) == 1236
                ):
                    self.logger.debug("Ignored expected Proactor self-pipe abort during shutdown")
                    return
                loop.default_exception_handler(context)

            self._loop.set_exception_handler(handle_loop_exception)
            self._running = True
            self.logger.info("AsyncJobManager event loop started")
            try:
                self._loop.run_forever()
            finally:
                try:
                    if self._loop and not self._loop.is_closed():
                        self._loop.close()
                except Exception as e:
                    self.logger.warning(f"Error closing event loop: {e}")
                finally:
                    asyncio.set_event_loop(None)
                    self._running = False
                    self.logger.info("AsyncJobManager event loop stopped")
        
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        
        # 等待事件循环启动
        import time
        timeout = 5.0
        start_time = time.time()
        while not self._loop and time.time() - start_time < timeout:
            time.sleep(0.01)
        
        if not self._loop:
            raise RuntimeError("Failed to start event loop")
    
    def submit_coroutine(self, coro: Coroutine) -> Optional[Future]:
        """提交协程到专用事件循环。"""
        if not self._running or self._loop is None:
            return None

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)
        return future

    def _discard_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)
    
    def cancel_all(self) -> int:
        with self._lock:
            futures = [future for future in self._futures if not future.done()]

        cancelled = 0
        for future in futures:
            if future.cancel():
                cancelled += 1
        return cancelled

    async def _drain_loop(self) -> None:
        """取消并回收事件循环中的待处理任务。"""
        current_task = asyncio.current_task()
        pending = [
            task for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]

        for task in pending:
            task.cancel()

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        try:
            await asyncio.get_running_loop().shutdown_asyncgens()
        except Exception as e:
            self.logger.debug(f"Error shutting down async generators: {e}")

        shutdown_executor = getattr(asyncio.get_running_loop(), "shutdown_default_executor", None)
        if shutdown_executor is not None:
            try:
                await shutdown_executor()
            except Exception as e:
                self.logger.debug(f"Error shutting down default executor: {e}")
    
    def shutdown(self, wait: bool = True) -> None:
        """关闭任务管理器
        
        Args:
            wait: 是否等待所有任务完成
        """
        if not self._running:
            return
        
        self.logger.info("Shutting down AsyncJobManager")
        self._stopping = True
        
        if not wait:
            self.cancel_all()
        
        # 停止事件循环
        if self._loop and not self._loop.is_closed():
            try:
                drain_future = asyncio.run_coroutine_threadsafe(self._drain_loop(), self._loop)
                drain_future.result(timeout=5.0)
            except Exception as e:
                self.logger.warning(f"Error draining event loop: {e}")

            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                # 事件循环可能已经停止
                pass
        
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        with self._lock:
            self._futures.clear()

        self._loop = None
        self._thread = None
        self._running = False
        self._stopping = False

        self.logger.info("AsyncJobManager shutdown complete")
    
    def __del__(self):
        """析构函数。

        解释器退出期对象可能处于半初始化状态（__init__ 中途失败）或依赖的
        模块已被清理，全部防御式访问，绝不让异常从 __del__ 泄漏。"""
        try:
            if getattr(self, "_running", False):
                self.shutdown(wait=False)
        except Exception:
            # 忽略析构时的错误
            pass

