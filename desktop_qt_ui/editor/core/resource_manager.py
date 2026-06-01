"""资源管理器

统一管理编辑器的所有资源，包括图片、蒙版、区域等。
"""

import copy
import logging
import os
import weakref
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from manga_translator.utils import open_pil_image

from .resources import ImageResource, MaskResource, RegionResource
from .types import MaskType


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


def _estimate_cache_value_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, Image.Image):
        return _estimate_image_bytes(value)
    nbytes = getattr(value, "nbytes", None)
    if isinstance(nbytes, int):
        return int(nbytes)
    return 0


class ResourceManager:
    """资源管理器
    
    统一管理所有编辑器资源的生命周期。
    """
    
    def __init__(self):
        """初始化资源管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 当前加载的资源
        self._current_image: Optional[ImageResource] = None
        self._masks: Dict[MaskType, MaskResource] = {}
        self._regions: Dict[int, RegionResource] = {}
        self._next_region_id = 0
        
        # 资源缓存（用于快速切换）
        self._image_cache: Dict[str, ImageResource] = {}
        self._cache_limit = 5  # 最多缓存5张图片
        
        # 通用缓存（用于存储临时数据）
        self._temp_cache: Dict[str, Any] = {}
        self._weak_cache: Dict[str, weakref.ReferenceType[Any]] = {}
        self._export_cleanup_threshold_bytes = 2 * 1024 * 1024 * 1024

    def _release_cached_value(self, value: Any) -> None:
        """释放缓存中的大对象，避免等待 GC 才回收文件句柄和内存。"""
        if value is None:
            return

        protected_images = [
            resource.image
            for resource in self._image_cache.values()
            if resource is not None and getattr(resource, "image", None) is not None
        ]
        current_image = self._current_image.image if self._current_image is not None else None
        if current_image is not None:
            protected_images.append(current_image)

        if isinstance(value, Image.Image):
            if not any(value is image for image in protected_images):
                try:
                    value.close()
                except Exception:
                    pass
            return

        release = getattr(value, "release", None)
        if callable(release):
            try:
                release()
            except Exception:
                pass
            return

        close = getattr(value, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    
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

        if image_path in self._image_cache:
            self.logger.debug(f"Image loaded from cache: {image_path}")
            resource = self._image_cache[image_path]
            self._current_image = resource
            return resource

        try:
            self.logger.debug(f"Loading image: {image_path}")
            image = open_pil_image(image_path, eager=False)
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
        except Exception as e:
            self.logger.error(f"Failed to load image {image_path}: {e}")
            raise

    def prefetch_image(self, image_path: str) -> ImageResource:
        """预读图片资源到 LRU，不切换 current_image。"""
        image_path = self._resolve_image_path(image_path)

        if image_path in self._image_cache:
            return self._image_cache[image_path]

        image = open_pil_image(image_path, eager=False)
        resource = ImageResource(
            path=image_path,
            image=image,
            width=image.width,
            height=image.height,
        )
        self._add_to_cache(image_path, resource)
        return resource

    def load_detached_image(self, image_path: str) -> Image.Image:
        """加载辅助图片，不写入 current_image，也不污染缓存。"""
        image_path = self._resolve_image_path(image_path)
        self.logger.debug(f"Loading detached image: {image_path}")
        return open_pil_image(image_path, eager=False)
    
    def _add_to_cache(self, path: str, resource: ImageResource) -> None:
        """添加图片到缓存
        
        Args:
            path: 图片路径
            resource: 图片资源
        """
        # 如果缓存已满，删除最旧的
        if len(self._image_cache) >= self._cache_limit:
            # 按加载时间排序，删除最旧的
            oldest_path = min(self._image_cache.items(), key=lambda x: x[1].load_time)[0]
            old_resource = self._image_cache.pop(oldest_path)
            old_resource.release()
            self.logger.debug(f"Removed oldest image from cache: {oldest_path}")
            
            # 释放内存
            pass
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
        if path in self._image_cache:
            resource = self._image_cache.pop(path)
            resource.release()
            pass
            self.logger.debug(f"Released image from cache: {path}")
            return True
        return False
    
    def clear_image_cache(self) -> None:
        """清空所有图片缓存"""
        for resource in self._image_cache.values():
            resource.release()
        self._image_cache.clear()
        pass
        _release_gpu_memory()
        self.logger.info("Cleared all image cache")

    def release_image_cache_except_current(self, force: bool = False) -> int:
        """只保留当前图，释放 image_cache 中的其他图片。"""
        if not force and _current_process_memory_bytes() < self._export_cleanup_threshold_bytes:
            return 0

        current_path = self._current_image.path if self._current_image is not None else None
        removed = 0

        for path in list(self._image_cache.keys()):
            if path == current_path:
                continue
            resource = self._image_cache.pop(path, None)
            if resource is not None:
                resource.release()
                removed += 1
        return removed
    
    def unload_image(self, release_from_cache: bool = False) -> None:
        """卸载当前图片及所有关联资源
        
        Args:
            release_from_cache: 是否同时从缓存中释放该图片
        """
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
        
        # 清空所有关联资源
        self.clear_masks()
        self.clear_regions()
        self.clear_cache()
        self.clear_weak_cache()
        
        # 强制垃圾回收
        pass
        _release_gpu_memory()
        
        self.logger.debug("Image unloaded and memory released")
    
    def get_current_image(self) -> Optional[ImageResource]:
        """获取当前图片资源
        
        Returns:
            Optional[ImageResource]: 当前图片资源，如果没有加载返回None
        """
        return self._current_image

    def get_managed_images(self) -> List[Image.Image]:
        """返回当前资源管理器仍在持有的图像对象。"""
        images: List[Image.Image] = []
        if self._current_image is not None and getattr(self._current_image, "image", None) is not None:
            images.append(self._current_image.image)
        for resource in self._image_cache.values():
            image = getattr(resource, "image", None)
            if image is not None and not any(image is existing for existing in images):
                images.append(image)
        return images

    def get_memory_snapshot(self) -> Dict[str, Any]:
        """返回当前资源持有情况，便于切图/导出后观测内存。"""
        managed_images = self.get_managed_images()
        managed_image_bytes = sum(_estimate_image_bytes(image) for image in managed_images)
        mask_bytes = sum(int(mask.data.nbytes) for mask in self._masks.values() if getattr(mask, "data", None) is not None)
        temp_cache_bytes = sum(_estimate_cache_value_bytes(value) for value in self._temp_cache.values())
        weak_cache_live_entries = 0
        for key, value_ref in list(self._weak_cache.items()):
            if value_ref() is None:
                self._weak_cache.pop(key, None)
                continue
            weak_cache_live_entries += 1

        return {
            "process_bytes": _current_process_memory_bytes(),
            "managed_image_count": len(managed_images),
            "managed_image_bytes": managed_image_bytes,
            "image_cache_entries": len(self._image_cache),
            "mask_count": len(self._masks),
            "mask_bytes": mask_bytes,
            "region_count": len(self._regions),
            "temp_cache_entries": len(self._temp_cache),
            "temp_cache_bytes": temp_cache_bytes,
            "temp_cache_keys": sorted(self._temp_cache.keys()),
            "weak_cache_entries": len(self._weak_cache),
            "weak_cache_live_entries": weak_cache_live_entries,
            "current_image_path": self._current_image.path if self._current_image is not None else None,
        }

    def log_memory_snapshot(self, stage: str, logger=None) -> Dict[str, Any]:
        target_logger = logger or self.logger
        if not target_logger.isEnabledFor(logging.DEBUG):
            return {}

        snapshot = self.get_memory_snapshot()
        target_logger.debug(
            "Memory snapshot [%s]: process=%.2fMB managed_images=%s managed=%.2fMB masks=%.2fMB temp_cache=%.2fMB weak_cache=%s/%s keys=%s",
            stage,
            snapshot["process_bytes"] / (1024 * 1024),
            snapshot["managed_image_count"],
            snapshot["managed_image_bytes"] / (1024 * 1024),
            snapshot["mask_bytes"] / (1024 * 1024),
            snapshot["temp_cache_bytes"] / (1024 * 1024),
            snapshot["weak_cache_live_entries"],
            snapshot["weak_cache_entries"],
            ",".join(snapshot["temp_cache_keys"]) or "-",
        )
        return snapshot
    
    # ==================== 蒙版管理 ====================
    
    def set_mask(self, mask_type: MaskType, mask_data: np.ndarray) -> MaskResource:
        """设置蒙版
        
        Args:
            mask_type: 蒙版类型
            mask_data: 蒙版数据
        
        Returns:
            MaskResource: 蒙版资源
        """
        if not self._current_image:
            raise RuntimeError("No image loaded")
        
        # 创建蒙版资源
        resource = MaskResource(
            mask_type=mask_type,
            data=mask_data.copy(),
            width=mask_data.shape[1],
            height=mask_data.shape[0],
        )
        
        # 释放旧蒙版
        if mask_type in self._masks:
            self._masks[mask_type].release()
        
        self._masks[mask_type] = resource
        self.logger.debug(f"Set mask: {mask_type}")
        return resource
    
    def get_mask(self, mask_type: MaskType) -> Optional[MaskResource]:
        """获取蒙版
        
        Args:
            mask_type: 蒙版类型
        
        Returns:
            Optional[MaskResource]: 蒙版资源，如果不存在返回None
        """
        return self._masks.get(mask_type)
    
    def clear_masks(self) -> None:
        """清空所有蒙版"""
        for mask in self._masks.values():
            mask.release()
        self._masks.clear()
        self.logger.debug("Cleared all masks")

    def clear_mask(self, mask_type: MaskType) -> None:
        """清空指定类型的蒙版。"""
        resource = self._masks.pop(mask_type, None)
        if resource is not None:
            resource.release()
            self.logger.debug(f"Cleared mask: {mask_type}")
    
    # ==================== 区域管理 ====================
    
    def add_region(self, region_data: Dict) -> RegionResource:
        """添加文本区域
        
        Args:
            region_data: 区域数据
        
        Returns:
            RegionResource: 区域资源
        """
        region_id = self._next_region_id
        self._next_region_id += 1
        
        resource = RegionResource(
            region_id=region_id,
            data=copy.deepcopy(region_data),
        )
        
        self._regions[region_id] = resource
        self.logger.debug(f"Added region: {region_id}")
        return resource

    def get_all_regions(self) -> List[RegionResource]:
        """获取所有区域（按region_id排序）
        
        Returns:
            List[RegionResource]: 区域列表，按region_id升序排列
        """
        # 按region_id排序，确保顺序正确
        return [self._regions[rid] for rid in sorted(self._regions.keys())]
    
    def clear_regions(self) -> None:
        """清空所有区域"""
        self._regions.clear()
        self._next_region_id = 0
        self.logger.debug("Cleared all regions")
    
    # ==================== 缓存管理 ====================
    
    def set_cache(self, key: str, value: Any) -> None:
        """设置缓存数据
        
        Args:
            key: 缓存键
            value: 缓存值
        """
        old_value = self._temp_cache.get(key)
        if old_value is not value:
            self._release_cached_value(old_value)
        self._temp_cache[key] = value
        self.logger.debug(f"Set cache: {key}")
    
    def get_cache(self, key: str, default=None) -> Any:
        """获取缓存数据
        
        Args:
            key: 缓存键
            default: 默认值
        
        Returns:
            缓存值，如果不存在返回default
        """
        return self._temp_cache.get(key, default)

    def set_weak_cache(self, key: str, value: Any) -> None:
        """设置弱引用缓存，不让缓存本身阻止回收。"""
        if value is None:
            self._weak_cache.pop(key, None)
            return
        try:
            self._weak_cache[key] = weakref.ref(value)
            self.logger.debug(f"Set weak cache: {key}")
        except TypeError:
            self._weak_cache.pop(key, None)
            self.logger.debug(f"Skip weak cache for non-weakrefable value: {key}")

    def get_weak_cache(self, key: str, default=None) -> Any:
        value_ref = self._weak_cache.get(key)
        if value_ref is None:
            return default
        value = value_ref()
        if value is None:
            self._weak_cache.pop(key, None)
            return default
        return value

    def clear_weak_cache(self, key: Optional[str] = None) -> None:
        if key:
            self._weak_cache.pop(key, None)
            self.logger.debug(f"Cleared weak cache: {key}")
            return
        self._weak_cache.clear()
        self.logger.debug("Cleared all weak cache")
    
    def clear_cache(self, key: Optional[str] = None) -> None:
        """清空缓存
        
        Args:
            key: 如果指定，只清空该键；否则清空所有缓存
        """
        if key:
            if key in self._temp_cache:
                value = self._temp_cache.pop(key)
                self._release_cached_value(value)
                self.logger.debug(f"Cleared cache: {key}")
        else:
            for value in self._temp_cache.values():
                self._release_cached_value(value)
            self._temp_cache.clear()
            self.logger.debug("Cleared all cache")
    
    # ==================== 资源清理 ====================
    
    def cleanup_all(self) -> None:
        """清理所有资源"""
        self.logger.info("Cleaning up all resources")
        
        # 卸载当前图片（不从缓存释放，因为下面会清空缓存）
        if self._current_image:
            self._current_image = None
        
        # 清空蒙版
        self.clear_masks()
        
        # 清空区域
        self.clear_regions()
        
        # 清空临时缓存
        self.clear_cache()
        self.clear_weak_cache()
        
        # 清理图片缓存
        for resource in self._image_cache.values():
            resource.release()
        self._image_cache.clear()
        
        # 强制垃圾回收和GPU显存释放
        pass
        _release_gpu_memory()
        
        self.logger.info("All resources cleaned up")
    
    def release_memory_after_export(self) -> None:
        """导出后释放内存
        
        清理临时缓存和GPU显存，但保留图片缓存以便快速切换
        """
        if _current_process_memory_bytes() < self._export_cleanup_threshold_bytes:
            return

        # 清空临时缓存（inpainted图片等）
        self.clear_cache()
        self.clear_weak_cache()
        
        # 强制垃圾回收
        import gc
        gc.collect()
        # 释放GPU显存
        _release_gpu_memory()

        _trim_working_set()
    
    def __del__(self):
        """析构函数"""
        self.cleanup_all()



