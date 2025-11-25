"""异步任务管理器

提供统一的异步任务管理，包括任务队列、优先级、取消机制等。
"""

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from .resources import AsyncJob
from .types import JobPriority, JobState


class AsyncJobManager:
    """异步任务管理器
    
    管理所有后台异步任务，提供任务队列、优先级管理、取消机制等功能。
    """
    
    def __init__(self):
        """初始化异步任务管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 任务存储
        self._jobs: Dict[str, AsyncJob] = {}
        self._lock = threading.RLock()
        
        # 事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        
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
                except:
                    pass
                
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._running = True
            self.logger.info("AsyncJobManager event loop started")
            self._loop.run_forever()
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
    
    def submit(
        self,
        job_func: Callable,
        *args,
        priority: int = JobPriority.NORMAL,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
        **kwargs
    ) -> AsyncJob:
        """提交异步任务
        
        Args:
            job_func: 要执行的函数（可以是同步或异步函数）
            *args: 函数参数
            priority: 任务优先级
            on_complete: 完成回调
            on_error: 错误回调
            on_cancelled: 取消回调
            **kwargs: 函数关键字参数
        
        Returns:
            AsyncJob: 任务句柄
        """
        if not self._running:
            raise RuntimeError("AsyncJobManager is not running")
        
        # 创建任务对象
        job = AsyncJob(
            priority=priority,
            on_complete=on_complete,
            on_error=on_error,
            on_cancelled=on_cancelled,
        )
        
        with self._lock:
            self._jobs[job.id] = job
        
        # 提交到事件循环
        asyncio.run_coroutine_threadsafe(
            self._execute_job(job, job_func, *args, **kwargs),
            self._loop
        )
        
        self.logger.debug(f"Submitted job {job.id} with priority {priority}")
        return job
    
    async def _execute_job(
        self,
        job: AsyncJob,
        job_func: Callable,
        *args,
        **kwargs
    ) -> None:
        """执行任务
        
        Args:
            job: 任务对象
            job_func: 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数
        """
        try:
            # 检查任务是否已取消
            if job.state == JobState.CANCELLED:
                return
            
            # 标记为运行中
            job.mark_running()
            self.logger.debug(f"Job {job.id} started")
            
            # 执行函数
            if asyncio.iscoroutinefunction(job_func):
                result = await job_func(*args, **kwargs)
            else:
                # 在线程池中执行同步函数
                result = await self._loop.run_in_executor(None, job_func, *args, **kwargs)
            
            # 检查任务是否已取消
            if job.state == JobState.CANCELLED:
                return
            
            # 标记完成
            job.mark_completed(result)
            self.logger.debug(f"Job {job.id} completed")
            
        except asyncio.CancelledError:
            # 任务被取消
            job.mark_cancelled()
            self.logger.info(f"Job {job.id} cancelled")
            
        except Exception as e:
            # 任务失败
            job.mark_failed(e)
            self.logger.error(f"Job {job.id} failed: {e}")
        
        finally:
            # 清理已完成的任务
            with self._lock:
                if job.id in self._jobs and job.is_finished():
                    # 保留一段时间，以便查询结果
                    pass
    
    def cancel(self, job: AsyncJob) -> bool:
        """取消任务
        
        Args:
            job: 要取消的任务
        
        Returns:
            bool: 是否成功取消
        """
        with self._lock:
            if job.id not in self._jobs:
                return False
            
            if job.is_finished():
                return False
            
            job.mark_cancelled()
            self.logger.debug(f"Cancelled job {job.id}")
            return True
    
    def cancel_all(self) -> int:
        """取消所有活跃任务
        
        Returns:
            int: 取消的任务数量
        """
        count = 0
        with self._lock:
            for job in list(self._jobs.values()):
                if job.is_active():
                    job.mark_cancelled()
                    count += 1
        
        self.logger.info(f"Cancelled {count} active jobs")
        return count
    
    def get_job(self, job_id: str) -> Optional[AsyncJob]:
        """获取任务
        
        Args:
            job_id: 任务ID
        
        Returns:
            Optional[AsyncJob]: 任务对象，如果不存在返回None
        """
        with self._lock:
            return self._jobs.get(job_id)
    
    def get_active_jobs(self) -> List[AsyncJob]:
        """获取所有活跃任务
        
        Returns:
            List[AsyncJob]: 活跃任务列表
        """
        with self._lock:
            return [job for job in self._jobs.values() if job.is_active()]
    
    def wait_for(self, job: AsyncJob, timeout: Optional[float] = None) -> Any:
        """等待任务完成
        
        Args:
            job: 要等待的任务
            timeout: 超时时间（秒），None表示无限等待
        
        Returns:
            Any: 任务结果
        
        Raises:
            TimeoutError: 超时
            Exception: 任务执行失败时的异常
        """
        import time
        start_time = time.time()
        
        while not job.is_finished():
            if timeout and time.time() - start_time > timeout:
                raise TimeoutError(f"Job {job.id} timeout after {timeout}s")
            time.sleep(0.01)
        
        if job.state == JobState.COMPLETED:
            return job.result
        elif job.state == JobState.CANCELLED:
            raise asyncio.CancelledError(f"Job {job.id} was cancelled")
        elif job.state == JobState.FAILED:
            raise job.error
    
    def cleanup_finished_jobs(self, keep_recent: int = 10) -> int:
        """清理已完成的任务
        
        Args:
            keep_recent: 保留最近的N个已完成任务
        
        Returns:
            int: 清理的任务数量
        """
        with self._lock:
            finished_jobs = [
                job for job in self._jobs.values()
                if job.is_finished()
            ]
            
            # 按完成时间排序
            finished_jobs.sort(key=lambda j: j.completed_at or 0, reverse=True)
            
            # 删除旧任务
            to_remove = finished_jobs[keep_recent:]
            for job in to_remove:
                del self._jobs[job.id]
            
            count = len(to_remove)
            if count > 0:
                self.logger.debug(f"Cleaned up {count} finished jobs")
            
            return count
    
    def shutdown(self, wait: bool = True) -> None:
        """关闭任务管理器
        
        Args:
            wait: 是否等待所有任务完成
        """
        if not self._running:
            return
        
        self.logger.info("Shutting down AsyncJobManager")
        
        if not wait:
            self.cancel_all()
        
        # 停止事件循环
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        
        self._running = False
        self.logger.info("AsyncJobManager shutdown complete")
    
    def __del__(self):
        """析构函数"""
        self.shutdown(wait=False)

