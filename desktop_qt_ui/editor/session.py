from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .core.resource_manager import ResourceManager
from .core.types import MaskType
from .region_geometry_state import normalize_region_geometry_data


INPAINTED_IMAGE_CACHE_KEY = "inpainted_image"
PAINT_OVERLAY_IMAGE_CACHE_KEY = "paint_overlay_image"
STAMP_OVERLAY_IMAGE_CACHE_KEY = "stamp_overlay_image"


@dataclass(slots=True)
class DocumentSnapshot:
    source_path: str
    image: Any
    compare_image: Any = None
    regions: list[dict] = field(default_factory=list)
    raw_mask: Any = None
    inpainted_path: Optional[str] = None
    inpainted_image: Any = None
    paint_overlay_path: Optional[str] = None
    paint_overlay_image: Any = None
    stamp_overlay_image: Any = None


@dataclass(slots=True)
class DocumentLoadFailure:
    error: str


class EditorSession:
    """文档级编辑状态中枢。"""

    def __init__(self, resource_manager: ResourceManager):
        self.resource_manager = resource_manager
        self._source_image_path: Optional[str] = None
        self._image = None
        self._inpainted_image_path: Optional[str] = None
        self._paint_overlay_path: Optional[str] = None
        self._display_mask_type: str = "none"
        self._selected_indices: list[int] = []
        self._region_display_mode: str = "full"
        self._original_image_alpha: float = 0.0
        self._compare_image = None
        self._active_tool: str = "select"
        self._brush_size: int = 30
        self._brush_color: str = "#ffffff"
        self._document_revision: int = 0

    @staticmethod
    def _normalize_binary_mask(mask: Any) -> Optional[np.ndarray]:
        if mask is None:
            return None
        mask_np = np.asarray(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np[:, :, 0]
        return np.where(mask_np > 0, 255, 0).astype(np.uint8)

    def _bump_document_revision(self) -> None:
        self._document_revision += 1

    def get_document_revision(self) -> int:
        return self._document_revision

    def set_source_image_path(self, path: Optional[str]) -> None:
        self._source_image_path = path

    def get_source_image_path(self) -> Optional[str]:
        return self._source_image_path

    def set_image(self, image: Any) -> None:
        # 图以 eager 方式打开，不持有文件句柄，也可能同时被 ResourceManager
        # 缓存和导出快照持有，因此换图时只丢引用，不做任何关闭判断。
        self._image = image
        self._bump_document_revision()

    def get_image(self) -> Any:
        return self._image

    def set_regions(self, regions: list[dict]) -> None:
        """整体重建 region 列表（文档级操作），region_id 重新分配。"""
        self.resource_manager.clear_regions()
        for region_data in regions:
            self.resource_manager.add_region(normalize_region_geometry_data(region_data))
        self._bump_document_revision()

    def update_region(self, index: int, region: dict) -> bool:
        if not self.resource_manager.update_region(index, normalize_region_geometry_data(region)):
            return False
        self._bump_document_revision()
        return True

    def insert_region(self, index: int, region: dict) -> int:
        insert_at = self.resource_manager.insert_region(index, normalize_region_geometry_data(region))
        self._bump_document_revision()
        return insert_at

    def remove_region(self, index: int) -> Optional[dict]:
        removed = self.resource_manager.remove_region(index)
        if removed is not None:
            self._bump_document_revision()
        return removed

    def store_derived_regions(self, updates: dict[int, dict]) -> None:
        """按索引覆盖指定 region 数据（region_id 不变），用于渲染派生字段写回。"""
        applied = False
        for index, region in updates.items():
            if self.resource_manager.update_region(index, normalize_region_geometry_data(region)):
                applied = True
        if applied:
            self._bump_document_revision()

    def get_region_id(self, index: int) -> Optional[int]:
        return self.resource_manager.get_region_id(index)

    def find_region_index(self, region_id: int) -> Optional[int]:
        return self.resource_manager.find_region_index(region_id)

    def get_regions(self) -> list[dict]:
        resources = self.resource_manager.get_all_regions()
        return [resource.data for resource in resources]

    def set_mask(self, mask_type: MaskType, mask: Any) -> Optional[np.ndarray]:
        normalized = self._normalize_binary_mask(mask)
        if normalized is None:
            self.resource_manager.clear_mask(mask_type)
        else:
            self.resource_manager.set_mask(mask_type, normalized)
        self._bump_document_revision()
        return normalized

    def get_mask(self, mask_type: MaskType) -> Any:
        resource = self.resource_manager.get_mask(mask_type)
        return resource.data if resource else None

    def set_display_mask_type(self, mask_type: str) -> bool:
        if mask_type not in {"raw", "refined", "none"}:
            return False
        if self._display_mask_type == mask_type:
            return False
        self._display_mask_type = mask_type
        return True

    def get_display_mask_type(self) -> str:
        return self._display_mask_type

    def set_inpainted_image_path(self, path: Optional[str]) -> None:
        self._inpainted_image_path = path

    def get_inpainted_image_path(self) -> Optional[str]:
        return self._inpainted_image_path

    def set_selection(self, indices: list[int]) -> bool:
        normalized = sorted(indices)
        if self._selected_indices == normalized:
            return False
        self._selected_indices = normalized
        return True

    def get_selection(self) -> list[int]:
        return list(self._selected_indices)

    def get_region_by_index(self, index: int) -> Optional[dict]:
        regions = self.get_regions()
        if 0 <= index < len(regions):
            return regions[index]
        return None

    def set_inpainted_image(self, image: Any) -> None:
        if image is None:
            self.resource_manager.clear_cache(INPAINTED_IMAGE_CACHE_KEY)
        else:
            self.resource_manager.set_cache(INPAINTED_IMAGE_CACHE_KEY, image)
        self._bump_document_revision()

    def get_inpainted_image(self) -> Any:
        return self.resource_manager.get_cache(INPAINTED_IMAGE_CACHE_KEY)

    def set_compare_image(self, image: Any) -> None:
        # 对照图可能就是底图本身（无独立原图时），同样只丢引用，不做关闭判断。
        self._compare_image = image
        self._bump_document_revision()

    def get_compare_image(self) -> Any:
        return self._compare_image

    def set_region_display_mode(self, mode: str) -> bool:
        if self._region_display_mode == mode:
            return False
        self._region_display_mode = mode
        return True

    def get_region_display_mode(self) -> str:
        return self._region_display_mode

    def set_original_image_alpha(self, alpha: float) -> bool:
        if self._original_image_alpha == alpha:
            return False
        self._original_image_alpha = alpha
        return True

    def get_original_image_alpha(self) -> float:
        return self._original_image_alpha

    def set_active_tool(self, tool: str) -> bool:
        if self._active_tool == tool:
            return False
        self._active_tool = tool
        return True

    def get_active_tool(self) -> str:
        return self._active_tool

    def set_brush_size(self, size: int) -> bool:
        if self._brush_size == size:
            return False
        self._brush_size = size
        return True

    def get_brush_size(self) -> int:
        return self._brush_size

    def set_brush_color(self, color: str) -> bool:
        color_value = str(color or "").strip() or "#ff0000"
        if self._brush_color == color_value:
            return False
        self._brush_color = color_value
        return True

    def get_brush_color(self) -> str:
        return self._brush_color

    def set_paint_overlay_path(self, path: Optional[str]) -> None:
        self._paint_overlay_path = path

    def get_paint_overlay_path(self) -> Optional[str]:
        return self._paint_overlay_path

    def set_paint_overlay_image(self, image: Any) -> None:
        if image is None:
            self.resource_manager.clear_cache(PAINT_OVERLAY_IMAGE_CACHE_KEY)
        else:
            self.resource_manager.set_cache(PAINT_OVERLAY_IMAGE_CACHE_KEY, image)
        self._bump_document_revision()

    def get_paint_overlay_image(self) -> Any:
        return self.resource_manager.get_cache(PAINT_OVERLAY_IMAGE_CACHE_KEY)

    def set_stamp_overlay_image(self, image: Any) -> None:
        if image is None:
            self.resource_manager.clear_cache(STAMP_OVERLAY_IMAGE_CACHE_KEY)
        else:
            self.resource_manager.set_cache(STAMP_OVERLAY_IMAGE_CACHE_KEY, image)
        self._bump_document_revision()

    def get_stamp_overlay_image(self) -> Any:
        return self.resource_manager.get_cache(STAMP_OVERLAY_IMAGE_CACHE_KEY)

    def load_document(self, snapshot: DocumentSnapshot) -> None:
        self.set_source_image_path(snapshot.source_path)
        self.set_image(snapshot.image)
        self.set_compare_image(snapshot.compare_image if snapshot.compare_image is not None else snapshot.image)
        self.set_regions(snapshot.regions)
        self.set_mask(MaskType.RAW, snapshot.raw_mask)
        self.set_mask(MaskType.REFINED, None)
        self.set_inpainted_image_path(snapshot.inpainted_path)
        self.set_inpainted_image(snapshot.inpainted_image)
        self.set_paint_overlay_path(snapshot.paint_overlay_path)
        self.set_paint_overlay_image(snapshot.paint_overlay_image)
        self.set_stamp_overlay_image(snapshot.stamp_overlay_image)
        self.set_selection([])

    def clear_document(self) -> None:
        self.set_source_image_path(None)
        self.set_image(None)
        self.set_compare_image(None)
        self.set_regions([])
        self.set_mask(MaskType.RAW, None)
        self.set_mask(MaskType.REFINED, None)
        self.set_inpainted_image_path(None)
        self.set_inpainted_image(None)
        self.set_paint_overlay_path(None)
        self.set_paint_overlay_image(None)
        self.set_stamp_overlay_image(None)
        self.set_selection([])
