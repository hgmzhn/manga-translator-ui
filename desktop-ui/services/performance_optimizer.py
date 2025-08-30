"""
性能优化服务
提供图片加载优化、内存管理和UI响应性改进
"""
import os
import gc
import threading
import time
import logging
from typing import Dict, List, Optional, Callable, Any, Tuple
from PIL import Image, ImageTk
import weakref
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import psutil

class ImageCache:
    """图片缓存管理器"""
    
    def __init__(self, max_cache_size: int = 100, max_memory_mb: int = 512):
        self.max_cache_size = max_cache_size
        self.max_memory_mb = max_memory_mb
        self.cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.memory_usage = 0
        self.lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
    
    def get_cache_key(self, image_path: str, size: Optional[Tuple[int, int]] = None) -> str:
        """生成缓存键"""
        if size:
            return f"{image_path}_{size[0]}x{size[1]}"
        return image_path
    
    def get_image(self, image_path: str, size: Optional[Tuple[int, int]] = None) -> Optional[Image.Image]:
        """获取缓存的图片"""
        cache_key = self.get_cache_key(image_path, size)
        
        with self.lock:
            if cache_key in self.cache:
                # 移到最后（LRU更新）
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]['image']
        
        return None
    
    def put_image(self, image_path: str, image: Image.Image, size: Optional[Tuple[int, int]] = None):
        """缓存图片"""
        cache_key = self.get_cache_key(image_path, size)
        
        # 估算图片内存使用
        image_size_mb = (image.width * image.height * 4) / (1024 * 1024)  # RGBA
        
        with self.lock:
            # 检查内存限制
            while (self.memory_usage + image_size_mb > self.max_memory_mb or 
                   len(self.cache) >= self.max_cache_size) and self.cache:
                self._remove_oldest()
            
            # 添加到缓存
            self.cache[cache_key] = {
                'image': image,
                'size_mb': image_size_mb,
                'access_time': time.time()
            }
            self.memory_usage += image_size_mb
            
            self.logger.debug(f"缓存图片: {cache_key}, 内存使用: {self.memory_usage:.1f}MB")
    
    def _remove_oldest(self):
        """移除最旧的缓存项"""
        if self.cache:
            oldest_key, oldest_data = self.cache.popitem(last=False)
            self.memory_usage -= oldest_data['size_mb']
            self.logger.debug(f"移除缓存: {oldest_key}")
    
    def clear(self):
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            self.memory_usage = 0
            self.logger.info("清空图片缓存")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self.lock:
            return {
                'cache_size': len(self.cache),
                'max_cache_size': self.max_cache_size,
                'memory_usage_mb': self.memory_usage,
                'max_memory_mb': self.max_memory_mb,
                'cache_hit_rate': getattr(self, '_hit_rate', 0.0)
            }

class LazyImageLoader:
    """延迟图片加载器"""
    
    def __init__(self, image_cache: ImageCache, thread_pool: ThreadPoolExecutor):
        self.image_cache = image_cache
        self.thread_pool = thread_pool
        self.loading_tasks: Dict[str, Any] = {}
        self.logger = logging.getLogger(__name__)
    
    def load_image_async(self, image_path: str, size: Optional[Tuple[int, int]] = None,
                        callback: Optional[Callable] = None) -> bool:
        """异步加载图片"""
        cache_key = self.image_cache.get_cache_key(image_path, size)
        
        # 检查缓存
        cached_image = self.image_cache.get_image(image_path, size)
        if cached_image:
            if callback:
                callback(cached_image)
            return True
        
        # 检查是否已在加载
        if cache_key in self.loading_tasks:
            return False
        
        # 提交加载任务
        future = self.thread_pool.submit(self._load_image_sync, image_path, size)
        self.loading_tasks[cache_key] = {
            'future': future,
            'callback': callback,
            'start_time': time.time()
        }
        
        # 设置完成回调
        future.add_done_callback(lambda f: self._on_load_complete(cache_key, f))
        
        return True
    
    def _load_image_sync(self, image_path: str, size: Optional[Tuple[int, int]] = None) -> Optional[Image.Image]:
        """同步加载图片"""
        try:
            if not os.path.exists(image_path):
                return None
            
            # 加载图片
            image = Image.open(image_path)
            
            # 调整大小（如果需要）
            if size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            
            # 转换为RGB（如果需要）
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            return image
            
        except Exception as e:
            self.logger.error(f"加载图片失败 {image_path}: {e}")
            return None
    
    def _on_load_complete(self, cache_key: str, future):
        """加载完成回调"""
        try:
            task_info = self.loading_tasks.pop(cache_key, None)
            if not task_info:
                return
            
            image = future.result()
            if image:
                # 添加到缓存
                image_path = cache_key.split('_')[0]  # 简化的路径提取
                self.image_cache.put_image(image_path, image)
                
                # 执行回调
                if task_info['callback']:
                    task_info['callback'](image)
                
                load_time = time.time() - task_info['start_time']
                self.logger.debug(f"图片加载完成: {cache_key}, 耗时: {load_time:.2f}s")
            
        except Exception as e:
            self.logger.error(f"处理加载完成回调失败: {e}")

class MemoryManager:
    """内存管理器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.weak_refs: List[weakref.ref] = []
        self.cleanup_threshold_mb = 500  # MB
        self.last_cleanup = time.time()
        self.cleanup_interval = 30  # 秒
    
    def register_object(self, obj):
        """注册对象用于内存监控"""
        weak_ref = weakref.ref(obj, self._object_deleted)
        self.weak_refs.append(weak_ref)
    
    def _object_deleted(self, weak_ref):
        """对象被删除时的回调"""
        try:
            self.weak_refs.remove(weak_ref)
        except ValueError:
            pass
    
    def get_memory_usage(self) -> Dict[str, float]:
        """获取内存使用情况"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                'rss_mb': memory_info.rss / (1024 * 1024),  # 物理内存
                'vms_mb': memory_info.vms / (1024 * 1024),  # 虚拟内存
                'percent': process.memory_percent(),
                'available_mb': psutil.virtual_memory().available / (1024 * 1024)
            }
        except Exception as e:
            self.logger.error(f"获取内存使用失败: {e}")
            return {}
    
    def should_cleanup(self) -> bool:
        """检查是否需要清理内存"""
        current_time = time.time()
        if current_time - self.last_cleanup < self.cleanup_interval:
            return False
        
        memory_usage = self.get_memory_usage()
        if memory_usage.get('rss_mb', 0) > self.cleanup_threshold_mb:
            return True
        
        return False
    
    def cleanup_memory(self, force: bool = False):
        """清理内存"""
        if not force and not self.should_cleanup():
            return
        
        self.logger.info("开始内存清理...")
        
        # 清理弱引用
        self.weak_refs = [ref for ref in self.weak_refs if ref() is not None]
        
        # 强制垃圾回收
        collected = gc.collect()
        
        self.last_cleanup = time.time()
        
        memory_after = self.get_memory_usage()
        self.logger.info(f"内存清理完成，回收对象: {collected}, 当前内存: {memory_after.get('rss_mb', 0):.1f}MB")

class UIPerformanceOptimizer:
    """UI性能优化器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.update_queue = []
        self.update_lock = threading.Lock()
        self.batch_update_delay = 0.016  # 60 FPS
        self.last_update = 0
        
    def schedule_update(self, update_func: Callable, priority: int = 0):
        """调度UI更新"""
        with self.update_lock:
            self.update_queue.append({
                'func': update_func,
                'priority': priority,
                'timestamp': time.time()
            })
    
    def process_updates(self, max_updates: int = 10):
        """处理UI更新队列"""
        current_time = time.time()
        if current_time - self.last_update < self.batch_update_delay:
            return
        
        updates_to_process = []
        with self.update_lock:
            # 按优先级排序
            self.update_queue.sort(key=lambda x: (-x['priority'], x['timestamp']))
            
            # 取出要处理的更新
            updates_to_process = self.update_queue[:max_updates]
            self.update_queue = self.update_queue[max_updates:]
        
        # 执行更新
        for update in updates_to_process:
            try:
                update['func']()
            except Exception as e:
                self.logger.error(f"UI更新失败: {e}")
        
        self.last_update = current_time
    
    def debounce_update(self, key: str, update_func: Callable, delay: float = 0.1):
        """防抖更新"""
        if not hasattr(self, '_debounce_timers'):
            self._debounce_timers = {}
        
        # 取消之前的定时器
        if key in self._debounce_timers:
            self._debounce_timers[key].cancel()
        
        # 创建新的定时器
        timer = threading.Timer(delay, update_func)
        self._debounce_timers[key] = timer
        timer.start()

class PerformanceOptimizer:
    """性能优化器主类"""
    
    def __init__(self, max_cache_size: int = 100, max_memory_mb: int = 512, max_workers: int = 4):
        self.logger = logging.getLogger(__name__)
        
        # 组件初始化
        self.image_cache = ImageCache(max_cache_size, max_memory_mb)
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ImageLoader")
        self.lazy_loader = LazyImageLoader(self.image_cache, self.thread_pool)
        self.memory_manager = MemoryManager()
        self.ui_optimizer = UIPerformanceOptimizer()
        
        # 性能监控
        self.performance_stats = {
            'image_loads': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'memory_cleanups': 0
        }
        
        # 启动性能监控线程
        self._start_monitoring()
    
    def load_image_optimized(self, image_path: str, size: Optional[Tuple[int, int]] = None,
                           callback: Optional[Callable] = None) -> Optional[Image.Image]:
        """优化的图片加载"""
        self.performance_stats['image_loads'] += 1
        
        # 尝试从缓存获取
        cached_image = self.image_cache.get_image(image_path, size)
        if cached_image:
            self.performance_stats['cache_hits'] += 1
            if callback:
                callback(cached_image)
            return cached_image
        
        # 缓存未命中，异步加载
        self.performance_stats['cache_misses'] += 1
        self.lazy_loader.load_image_async(image_path, size, callback)
        return None
    
    def optimize_ui_update(self, update_func: Callable, priority: int = 0):
        """优化UI更新"""
        self.ui_optimizer.schedule_update(update_func, priority)
    
    def process_ui_updates(self):
        """处理UI更新队列"""
        self.ui_optimizer.process_updates()
    
    def cleanup_memory(self, force: bool = False):
        """清理内存"""
        self.memory_manager.cleanup_memory(force)
        if force:
            self.image_cache.clear()
            self.performance_stats['memory_cleanups'] += 1
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        stats = self.performance_stats.copy()
        stats.update({
            'cache_stats': self.image_cache.get_stats(),
            'memory_usage': self.memory_manager.get_memory_usage(),
            'thread_pool_active': self.thread_pool._threads.__len__() if hasattr(self.thread_pool, '_threads') else 0
        })
        
        # 计算缓存命中率
        total_requests = stats['cache_hits'] + stats['cache_misses']
        if total_requests > 0:
            stats['cache_hit_rate'] = stats['cache_hits'] / total_requests
        else:
            stats['cache_hit_rate'] = 0.0
        
        return stats
    
    def _start_monitoring(self):
        """启动性能监控"""
        def monitor_loop():
            while True:
                try:
                    # 检查内存使用
                    if self.memory_manager.should_cleanup():
                        self.cleanup_memory()
                    
                    # 处理UI更新
                    self.process_ui_updates()
                    
                    time.sleep(1)  # 1秒检查一次
                    
                except Exception as e:
                    self.logger.error(f"性能监控出错: {e}")
                    time.sleep(5)
        
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True, name="PerformanceMonitor")
        monitor_thread.start()
        self.logger.info("性能监控线程已启动")
    
    def shutdown(self):
        """关闭性能优化器"""
        self.logger.info("关闭性能优化器...")
        
        try:
            self.thread_pool.shutdown(wait=True, timeout=5)
            self.image_cache.clear()
            self.cleanup_memory(force=True)
        except Exception as e:
            self.logger.error(f"关闭性能优化器失败: {e}")

# 全局性能优化器实例
_performance_optimizer = None

def get_performance_optimizer() -> PerformanceOptimizer:
    """获取全局性能优化器"""
    global _performance_optimizer
    if _performance_optimizer is None:
        _performance_optimizer = PerformanceOptimizer()
    return _performance_optimizer

def optimize_image_loading(image_path: str, size: Optional[Tuple[int, int]] = None,
                         callback: Optional[Callable] = None) -> Optional[Image.Image]:
    """优化图片加载的便捷函数"""
    return get_performance_optimizer().load_image_optimized(image_path, size, callback)

def optimize_ui_update(update_func: Callable, priority: int = 0):
    """优化UI更新的便捷函数"""
    get_performance_optimizer().optimize_ui_update(update_func, priority)

def cleanup_memory():
    """清理内存的便捷函数"""
    get_performance_optimizer().cleanup_memory()