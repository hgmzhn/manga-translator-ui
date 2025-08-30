"""
蒙版擦除预览服务
统一管理多种擦除算法的实时预览功能，支持异步处理、状态管理和错误处理
"""
import asyncio
import logging
import numpy as np
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
from enum import Enum
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future

from .erase_config_service import InpainterType, EraseConfigService, get_erase_config_service
from .lightweight_inpainter import LightweightInpainter, PreviewConfig, PreviewResult, get_lightweight_inpainter

class PreviewStatus(Enum):
    """预览状态"""
    IDLE = "idle"           # 空闲
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"    # 完成
    FAILED = "failed"          # 失败
    CANCELLED = "cancelled"    # 取消

@dataclass
class PreviewRequest:
    """预览请求"""
    request_id: str
    image: np.ndarray
    mask: np.ndarray
    algorithm: InpainterType
    config: PreviewConfig
    callback: Optional[Callable] = None
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

@dataclass
class PreviewState:
    """预览状态信息"""
    status: PreviewStatus
    result: Optional[PreviewResult] = None
    error: Optional[str] = None
    progress: float = 0.0  # 进度 0.0-1.0
    start_time: Optional[float] = None
    end_time: Optional[float] = None

class MaskErasePreviewService:
    """蒙版擦除预览服务"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # 服务依赖
        self.config_service = get_erase_config_service()
        self.lightweight_inpainter = get_lightweight_inpainter()
        
        # 请求管理
        self.active_requests: Dict[str, PreviewRequest] = {}
        self.request_states: Dict[str, PreviewState] = {}
        self.request_counter = 0
        
        # 线程管理
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="erase_preview")
        self.futures: Dict[str, Future] = {}
        
        # 状态锁
        self._lock = threading.Lock()
        
        # 回调管理
        self.global_callbacks: List[Callable] = []
        
        # 性能统计
        self.stats = {
            "total_requests": 0,
            "completed_requests": 0,
            "failed_requests": 0,
            "cancelled_requests": 0,
            "average_process_time": 0.0
        }
        
        self.logger.info("蒙版擦除预览服务初始化完成")
    
    def generate_request_id(self) -> str:
        """生成请求ID"""
        with self._lock:
            self.request_counter += 1
            return f"erase_preview_{self.request_counter}_{int(time.time()*1000)}"
    
    def submit_preview_request(self, image: np.ndarray, mask: np.ndarray,
                             algorithm: Optional[InpainterType] = None,
                             config: Optional[PreviewConfig] = None,
                             callback: Optional[Callable] = None) -> str:
        """提交预览请求"""
        if algorithm is None:
            algorithm = self.config_service.get_recommended_preview_algorithm()
        
        if config is None:
            config = PreviewConfig()
        
        request_id = self.generate_request_id()
        request = PreviewRequest(
            request_id=request_id,
            image=image.copy(),
            mask=mask.copy(),
            algorithm=algorithm,
            config=config,
            callback=callback
        )
        
        # 初始化状态
        state = PreviewState(
            status=PreviewStatus.IDLE,
            start_time=time.time()
        )
        
        with self._lock:
            self.active_requests[request_id] = request
            self.request_states[request_id] = state
            self.stats["total_requests"] += 1
        
        # 提交到线程池
        future = self.executor.submit(self._process_request, request_id)
        self.futures[request_id] = future
        
        self.logger.debug(f"提交预览请求: {request_id}, 算法: {algorithm.value}")
        return request_id
    
    def _process_request(self, request_id: str):
        """处理预览请求（在线程池中执行）"""
        try:
            request = self.active_requests[request_id]
            state = self.request_states[request_id]
            
            # 更新状态为处理中
            with self._lock:
                state.status = PreviewStatus.PROCESSING
                state.progress = 0.1
            
            self._notify_callbacks(request_id, state)
            
            # 检查算法是否适合预览
            if not self.config_service.is_preview_suitable(request.algorithm):
                self.logger.warning(f"算法 {request.algorithm.value} 不适合实时预览，切换到推荐算法")
                request.algorithm = self.config_service.get_recommended_preview_algorithm()
            
            # 执行预览处理
            with self._lock:
                state.progress = 0.3
            self._notify_callbacks(request_id, state)
            
            # 调用轻量级擦除算法
            result = self.lightweight_inpainter.preview_sync(
                request.image, 
                request.mask, 
                request.algorithm, 
                request.config
            )
            
            # 更新状态为完成
            with self._lock:
                state.status = PreviewStatus.COMPLETED
                state.result = result
                state.progress = 1.0
                state.end_time = time.time()
                self.stats["completed_requests"] += 1
                
                # 更新平均处理时间
                process_time = state.end_time - state.start_time
                total_completed = self.stats["completed_requests"]
                current_avg = self.stats["average_process_time"]
                self.stats["average_process_time"] = (current_avg * (total_completed - 1) + process_time) / total_completed
            
            self._notify_callbacks(request_id, state)
            self.logger.debug(f"预览请求完成: {request_id}, 耗时: {process_time:.3f}s")
            
        except Exception as e:
            self.logger.error(f"预览请求处理失败: {request_id}, 错误: {e}")
            with self._lock:
                state = self.request_states[request_id]
                state.status = PreviewStatus.FAILED
                state.error = str(e)
                state.end_time = time.time()
                self.stats["failed_requests"] += 1
            
            self._notify_callbacks(request_id, state)
        
        finally:
            # 清理资源
            self._cleanup_request(request_id)
    
    def _notify_callbacks(self, request_id: str, state: PreviewState):
        """通知回调函数"""
        try:
            # 请求特定回调
            request = self.active_requests.get(request_id)
            if request and request.callback:
                request.callback(request_id, state)
            
            # 全局回调
            for callback in self.global_callbacks:
                callback(request_id, state)
                
        except Exception as e:
            self.logger.error(f"回调通知失败: {e}")
    
    def get_request_state(self, request_id: str) -> Optional[PreviewState]:
        """获取请求状态"""
        return self.request_states.get(request_id)
    
    def cancel_request(self, request_id: str) -> bool:
        """取消预览请求"""
        future = self.futures.get(request_id)
        if future and not future.done():
            cancelled = future.cancel()
            if cancelled:
                with self._lock:
                    state = self.request_states.get(request_id)
                    if state:
                        state.status = PreviewStatus.CANCELLED
                        state.end_time = time.time()
                        self.stats["cancelled_requests"] += 1
                
                self._cleanup_request(request_id)
                self.logger.debug(f"预览请求已取消: {request_id}")
                return True
        
        return False
    
    def cancel_all_requests(self):
        """取消所有预览请求"""
        request_ids = list(self.futures.keys())
        cancelled_count = 0
        
        for request_id in request_ids:
            if self.cancel_request(request_id):
                cancelled_count += 1
        
        self.logger.info(f"取消了 {cancelled_count} 个预览请求")
    
    def _cleanup_request(self, request_id: str):
        """清理请求资源"""
        # 延迟清理，保留状态一段时间供查询
        def delayed_cleanup():
            time.sleep(30)  # 30秒后清理
            with self._lock:
                self.active_requests.pop(request_id, None)
                self.request_states.pop(request_id, None)
                self.futures.pop(request_id, None)
        
        cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
        cleanup_thread.start()
    
    def add_global_callback(self, callback: Callable):
        """添加全局回调函数"""
        self.global_callbacks.append(callback)
    
    def remove_global_callback(self, callback: Callable):
        """移除全局回调函数"""
        if callback in self.global_callbacks:
            self.global_callbacks.remove(callback)
    
    def get_active_requests(self) -> List[str]:
        """获取活跃请求列表"""
        return [
            request_id for request_id, state in self.request_states.items()
            if state.status in [PreviewStatus.IDLE, PreviewStatus.PROCESSING]
        ]
    
    def get_completed_requests(self) -> List[str]:
        """获取已完成请求列表"""
        return [
            request_id for request_id, state in self.request_states.items()
            if state.status == PreviewStatus.COMPLETED
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取服务统计信息"""
        with self._lock:
            stats = self.stats.copy()
        
        # 添加实时统计
        active_count = len(self.get_active_requests())
        completed_count = len(self.get_completed_requests())
        
        stats.update({
            "active_requests": active_count,
            "completed_requests_cached": completed_count,
            "cache_info": self.lightweight_inpainter.get_cache_info(),
            "supported_algorithms": [algo.value for algo in InpainterType],
            "recommended_algorithm": self.config_service.get_recommended_preview_algorithm().value
        })
        
        return stats
    
    def clear_cache(self):
        """清空缓存"""
        self.lightweight_inpainter.clear_cache()
        self.logger.info("预览缓存已清空")
    
    def set_algorithm(self, algorithm: InpainterType):
        """设置默认算法"""
        self.config_service.set_algorithm(algorithm)
        self.logger.info(f"设置默认擦除算法: {algorithm.value}")
    
    def get_current_algorithm(self) -> InpainterType:
        """获取当前算法"""
        return self.config_service.get_current_config().inpainter
    
    def is_algorithm_suitable(self, algorithm: InpainterType) -> bool:
        """检查算法是否适合预览"""
        return self.config_service.is_preview_suitable(algorithm)
    
    def get_suitable_algorithms(self) -> List[InpainterType]:
        """获取适合预览的算法列表"""
        return self.config_service.get_preview_suitable_algorithms()
    
    async def preview_async(self, image: np.ndarray, mask: np.ndarray,
                          algorithm: Optional[InpainterType] = None,
                          config: Optional[PreviewConfig] = None) -> PreviewResult:
        """异步预览（简化版本，直接调用轻量级算法）"""
        return await self.lightweight_inpainter.preview_async(image, mask, algorithm, config)
    
    def preview_sync(self, image: np.ndarray, mask: np.ndarray,
                   algorithm: Optional[InpainterType] = None,
                   config: Optional[PreviewConfig] = None) -> PreviewResult:
        """同步预览（简化版本，直接调用轻量级算法）"""
        return self.lightweight_inpainter.preview_sync(image, mask, algorithm, config)
    
    def shutdown(self):
        """关闭服务"""
        self.logger.info("正在关闭蒙版擦除预览服务...")
        
        # 取消所有活跃请求
        self.cancel_all_requests()
        
        # 关闭线程池
        self.executor.shutdown(wait=True)
        
        # 清理资源
        with self._lock:
            self.active_requests.clear()
            self.request_states.clear()
            self.futures.clear()
        
        # 关闭依赖服务
        self.lightweight_inpainter.shutdown()
        
        self.logger.info("蒙版擦除预览服务已关闭")

# 全局服务实例
_mask_erase_preview_service: Optional[MaskErasePreviewService] = None

def get_mask_erase_preview_service() -> MaskErasePreviewService:
    """获取蒙版擦除预览服务实例"""
    global _mask_erase_preview_service
    if _mask_erase_preview_service is None:
        _mask_erase_preview_service = MaskErasePreviewService()
    return _mask_erase_preview_service