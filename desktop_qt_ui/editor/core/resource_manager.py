"""Shared image LRU, prefetching, and identity-scoped rebuildable caches."""

import logging
import os
import threading
import weakref
from typing import Any, Dict, List, Optional

from manga_translator.utils import open_pil_image
from PIL import Image

from .resources import ImageResource, RebuildableCacheKey


def _release_gpu_memory():
    """释放GPU显存"""
    try:
        import torch
        if torch.cuda.is_available():
            pass
            pass
    except ImportError:
        pass
    except Exception:
        pass


def _trim_working_set() -> bool:
    """提示 Windows 回收当前进程工作集。"""
    try:
        import ctypes
        import os

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        process_id = os.getpid()
        process_handle = kernel32.OpenProcess(0x0400 | 0x0100, False, process_id)
        if not process_handle:
            return False

        empty_working_set_ok = False
        try:
            if hasattr(psapi, "EmptyWorkingSet"):
                empty_working_set_ok = bool(psapi.EmptyWorkingSet(process_handle))
                if empty_working_set_ok:
                    return True

            set_ws_ok = bool(kernel32.SetProcessWorkingSetSize(process_handle, -1, -1))
            if set_ws_ok:
                return True
            return False
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        return False


def _current_process_memory_bytes() -> int:
    try:
        import psutil

        info = psutil.Process(os.getpid()).memory_info()
        rss = getattr(info, "rss", 0) or 0
        wset = getattr(info, "wset", 0) or 0
        return max(rss, wset)
    except Exception:
        return 0


def _estimate_image_bytes(image: Image.Image | None) -> int:
    if image is None:
        return 0
    try:
        channels = max(1, len(image.getbands()))
        return int(image.width) * int(image.height) * channels
    except Exception:
        return 0




class ResourceManager:
    """Shared image LRU/prefetch store and identity-scoped weak cache."""
    
    def __init__(self):
        """初始化资源管理器"""
        self.logger = logging.getLogger(__name__)

        # 图片缓存与 current 会被预读线程、加载线程和主线程同时访问，
        # 用可重入锁保护；解码（open_pil_image）一律放在锁外，只锁字典读写。
        self._lock = threading.RLock()

        # 当前加载的资源
        self._current_image: Optional[ImageResource] = None
        
        # 资源缓存（用于快速切换）
        self._image_cache: Dict[str, ImageResource] = {}
        self._cache_limit = 5  # 最多缓存5张图片
        
        self._weak_cache: Dict[RebuildableCacheKey, weakref.ReferenceType[Any]] = {}
        self._export_cleanup_threshold_bytes = 2 * 1024 * 1024 * 1024

    # ==================== 图片管理 ====================

    @staticmethod
    def _resolve_image_path(image_path: str) -> str:
        """规范化图片路径并校验存在性。"""
        from pathlib import Path

        path_obj = Path(image_path)
        if not path_obj.exists():
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")

        return str(path_obj.resolve())

    def load_image(self, image_path: str) -> ImageResource:
        """加载当前编辑底图资源，并更新 current_image。"""
        image_path = self._resolve_image_path(image_path)

        with self._lock:
            cached = self._image_cache.get(image_path)
            if cached is not None:
                self.logger.debug(f"Image loaded from cache: {image_path}")
                cached.touch()
                self._current_image = cached
                return cached

        try:
            self.logger.debug(f"Loading image: {image_path}")
            # 解码放在锁外：慢操作不该阻塞其他线程读缓存
            image = open_pil_image(image_path, eager=True)
        except Exception as e:
            self.logger.error(f"Failed to load image {image_path}: {e}")
            raise

        with self._lock:
            # 双检：解码期间可能已被别的线程（如预读）放进缓存
            cached = self._image_cache.get(image_path)
            if cached is not None:
                cached.touch()
                self._current_image = cached
                return cached

            resource = ImageResource(
                path=image_path,
                image=image,
                width=image.width,
                height=image.height,
            )
            self._add_to_cache(image_path, resource)
            self._current_image = resource
            self.logger.debug(f"Image loaded successfully: {image_path} ({image.width}x{image.height})")
            return resource

    def prefetch_image(self, image_path: str) -> ImageResource:
        """预读图片资源到 LRU，不切换 current_image。"""
        image_path = self._resolve_image_path(image_path)

        with self._lock:
            cached = self._image_cache.get(image_path)
            if cached is not None:
                return cached

        image = open_pil_image(image_path, eager=True)

        with self._lock:
            cached = self._image_cache.get(image_path)
            if cached is not None:
                return cached

            resource = ImageResource(
                path=image_path,
                image=image,
                width=image.width,
                height=image.height,
            )
            self._add_to_cache(image_path, resource)
            return resource

    def activate_prefetched_image(
        self,
        image_path: str,
        image: Image.Image,
        *,
        qimage: Any = None,
    ) -> ImageResource:
        """Pin a prefetched document image without decoding it again."""
        image_path = self._resolve_image_path(image_path)
        with self._lock:
            resource = self._image_cache.get(image_path)
            if resource is None or resource.image is None:
                resource = ImageResource(
                    path=image_path,
                    image=image,
                    width=image.width,
                    height=image.height,
                    qimage=qimage,
                )
                self._add_to_cache(image_path, resource)
            elif resource.qimage is None and qimage is not None:
                resource.qimage = qimage
            resource.touch()
            self._current_image = resource
            return resource

    def load_detached_image(self, image_path: str) -> Image.Image:
        """加载辅助图片，不写入 current_image，也不污染缓存。"""
        image_path = self._resolve_image_path(image_path)
        self.logger.debug(f"Loading detached image: {image_path}")
        return open_pil_image(image_path, eager=True)

    def _add_to_cache(self, path: str, resource: ImageResource) -> None:
        """添加图片到缓存（调用方须持有 self._lock）

        淘汰策略为真 LRU：按 last_access 取最久未访问者，且**永不淘汰当前页**——
        当前页被淘汰会让 _current_image 与缓存失联，切回时还要重新解码。

        Args:
            path: 图片路径
            resource: 图片资源
        """
        if len(self._image_cache) >= self._cache_limit:
            current = self._current_image
            candidates = [
                (cached_path, cached)
                for cached_path, cached in self._image_cache.items()
                if cached is not current and cached_path != path
            ]
            if candidates:
                oldest_path = min(candidates, key=lambda item: item[1].last_access)[0]
                old_resource = self._image_cache.pop(oldest_path)
                old_resource.release()
                self.logger.debug(f"Removed least recently used image from cache: {oldest_path}")
            else:
                # 极端情况：缓存里只剩当前页，宁可超限也不淘汰它
                self.logger.debug("Cache eviction skipped: only the current image is cached")

        self._image_cache[path] = resource
    
    def release_image_from_cache(self, path: str) -> bool:
        """从缓存中释放指定图片

        Args:
            path: 图片路径

        Returns:
            bool: 是否成功释放
        """
        from pathlib import Path

        # 规范化路径以匹配缓存中的键
        path = str(Path(path).resolve())
        with self._lock:
            resource = self._image_cache.pop(path, None)
            if resource is None:
                return False
            resource.release()
        self.logger.debug(f"Released image from cache: {path}")
        return True

    def clear_image_cache(self) -> None:
        """清空所有图片缓存"""
        with self._lock:
            for resource in self._image_cache.values():
                resource.release()
            self._image_cache.clear()
        _release_gpu_memory()

    def release_image_cache_except_current(self, force: bool = False) -> int:
        """只保留当前图，释放 image_cache 中的其他图片。"""
        if not force and _current_process_memory_bytes() < self._export_cleanup_threshold_bytes:
            return 0

        removed = 0
        with self._lock:
            current = self._current_image
            current_path = current.path if current is not None else None

            for path in list(self._image_cache.keys()):
                if path == current_path:
                    continue
                resource = self._image_cache.pop(path, None)
                if resource is not None and resource is not current:
                    resource.release()
                    removed += 1
        return removed

    def unload_image(self, release_from_cache: bool = False) -> None:
        """卸载当前图片及所有关联资源

        Args:
            release_from_cache: 是否同时从缓存中释放该图片
        """
        with self._lock:
            if self._current_image:
                current_path = self._current_image.path

                # 如果需要从缓存中释放
                if release_from_cache and current_path in self._image_cache:
                    resource = self._image_cache.pop(current_path)
                    resource.release()
                    self.logger.debug(f"Released image from cache: {current_path}")

                self._current_image = None

        if release_from_cache:
            self.clear_image_cache()


        _release_gpu_memory()

        self.logger.debug("Image unloaded and memory released")

    def get_current_image(self) -> Optional[ImageResource]:
        """获取当前图片资源

        Returns:
            Optional[ImageResource]: 当前图片资源，如果没有加载返回None
        """
        with self._lock:
            return self._current_image

    def get_managed_images(self) -> List[Image.Image]:
        """返回当前资源管理器仍在持有的图像对象。"""
        images: List[Image.Image] = []
        with self._lock:
            if self._current_image is not None and getattr(self._current_image, "image", None) is not None:
                images.append(self._current_image.image)
            cached_resources = list(self._image_cache.values())
        for resource in cached_resources:
            image = getattr(resource, "image", None)
            if image is not None and not any(image is existing for existing in images):
                images.append(image)
        return images

    def get_memory_snapshot(self) -> Dict[str, Any]:
        """Return image-LRU and live identity-cache ownership metrics."""
        managed_images = self.get_managed_images()
        managed_image_bytes = sum(
            _estimate_image_bytes(image) for image in managed_images
        )
        weak_cache_live_entries = 0
        for key, value_ref in list(self._weak_cache.items()):
            if value_ref() is None:
                self._weak_cache.pop(key, None)
            else:
                weak_cache_live_entries += 1
        return {
            "process_bytes": _current_process_memory_bytes(),
            "managed_image_count": len(managed_images),
            "managed_image_bytes": managed_image_bytes,
            "image_cache_entries": len(self._image_cache),
            "weak_cache_entries": len(self._weak_cache),
            "weak_cache_live_entries": weak_cache_live_entries,
            "current_image_path": (
                self._current_image.path if self._current_image is not None else None
            ),
        }

    def log_memory_snapshot(self, stage: str, logger=None) -> Dict[str, Any]:
        target_logger = logger or self.logger
        if not target_logger.isEnabledFor(logging.DEBUG):
            return {}
        snapshot = self.get_memory_snapshot()
        target_logger.debug(
            "Memory snapshot [%s]: process=%.2fMB managed_images=%s "
            "managed=%.2fMB weak_cache=%s/%s",
            stage,
            snapshot["process_bytes"] / (1024 * 1024),
            snapshot["managed_image_count"],
            snapshot["managed_image_bytes"] / (1024 * 1024),
            snapshot["weak_cache_live_entries"],
            snapshot["weak_cache_entries"],
        )
        return snapshot
    
    def set_weak_cache(
        self,
        identity: tuple[int, str],
        name: str,
        value: Any,
    ) -> None:
        """Cache reproducible derived data without extending its lifetime."""
        key = RebuildableCacheKey.from_identity(identity, name)
        if value is None:
            self._weak_cache.pop(key, None)
            return
        try:
            self._weak_cache[key] = weakref.ref(value)
        except TypeError:
            self._weak_cache.pop(key, None)

    def get_weak_cache(
        self,
        identity: tuple[int, str],
        name: str,
        default=None,
    ) -> Any:
        key = RebuildableCacheKey.from_identity(identity, name)
        value_ref = self._weak_cache.get(key)
        if value_ref is None:
            return default
        value = value_ref()
        if value is None:
            self._weak_cache.pop(key, None)
            return default
        return value

    def clear_weak_cache(
        self,
        identity: Optional[tuple[int, str]] = None,
        name: Optional[str] = None,
    ) -> None:
        if identity is None:
            self._weak_cache.clear()
            return
        document_id, source_path = identity
        keys = [
            key
            for key in self._weak_cache
            if key.document_id == int(document_id)
            and key.source_path == str(source_path)
            and (name is None or key.name == name)
        ]
        for key in keys:
            self._weak_cache.pop(key, None)
    
    # ==================== 资源清理 ====================

    def cleanup_all(self) -> None:
        """Release the image LRU and all reconstructible weak entries."""
        self.clear_weak_cache()
        with self._lock:
            self._current_image = None
            for resource in self._image_cache.values():
                resource.release()
            self._image_cache.clear()
        _release_gpu_memory()


    def release_memory_after_export(self) -> None:
        """Trim optional caches under memory pressure while retaining the LRU."""
        if _current_process_memory_bytes() < self._export_cleanup_threshold_bytes:
            return
        self.clear_weak_cache()
        import gc

        gc.collect()
        _release_gpu_memory()
        _trim_working_set()
    
    def __del__(self):
        """析构函数"""
        self.cleanup_all()


