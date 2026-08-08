from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .core.types import MaskType
from .region_change import RegionChange
from .session import DocumentSnapshot, EditorSession


class EditorModel(QObject):
    """
    编辑器数据模型：region 状态的单一事实来源。

    所有 region 改动必须通过 update_region / update_regions / insert_region /
    remove_region / move_region / replace_regions 进入；「API 即事件」——每个
    mutation 直接发出对应的 RegionChange，经 regions_changed 广播，视图按
    kind 做最小刷新。
    渲染派生数据经 store_derived_regions 写回，不发通知。
    """

    image_changed = pyqtSignal(object)
    regions_changed = pyqtSignal(object)
    raw_mask_changed = pyqtSignal(object)
    refined_mask_changed = pyqtSignal(object)
    display_mask_type_changed = pyqtSignal(str)
    selection_changed = pyqtSignal(list)
    inpainted_image_changed = pyqtSignal(object)
    compare_image_changed = pyqtSignal(object)
    region_display_mode_changed = pyqtSignal(str)
    original_image_alpha_changed = pyqtSignal(float)
    active_tool_changed = pyqtSignal(str)
    brush_size_changed = pyqtSignal(int)
    brush_color_changed = pyqtSignal(str)
    paint_overlay_changed = pyqtSignal(object)
    stamp_overlay_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        from services import get_resource_manager

        self.resource_manager = get_resource_manager()
        self.session = EditorSession(self.resource_manager)

    def get_document_revision(self) -> int:
        return self.session.get_document_revision()

    def apply_document_snapshot(self, snapshot: DocumentSnapshot) -> None:
        previous_selection = self.get_selection()
        self.session.load_document(snapshot)
        self.image_changed.emit(self.get_image())
        self.compare_image_changed.emit(self.get_compare_image())
        self.regions_changed.emit(RegionChange.reset())
        self.raw_mask_changed.emit(self.get_raw_mask())
        self.refined_mask_changed.emit(self.get_refined_mask())
        self.inpainted_image_changed.emit(self.get_inpainted_image())
        self.paint_overlay_changed.emit(self.get_paint_overlay_image())
        self.stamp_overlay_changed.emit(self.get_stamp_overlay_image())
        current_selection = self.get_selection()
        if current_selection != previous_selection:
            self.selection_changed.emit(current_selection)

    def clear_document(self) -> None:
        self.session.clear_document()
        self.image_changed.emit(self.get_image())
        self.compare_image_changed.emit(self.get_compare_image())
        self.regions_changed.emit(RegionChange.reset())
        self.raw_mask_changed.emit(self.get_raw_mask())
        self.refined_mask_changed.emit(self.get_refined_mask())
        self.inpainted_image_changed.emit(self.get_inpainted_image())
        self.paint_overlay_changed.emit(self.get_paint_overlay_image())
        self.stamp_overlay_changed.emit(self.get_stamp_overlay_image())
        self.selection_changed.emit(self.get_selection())

    def set_source_image_path(self, path: Optional[str]):
        self.session.set_source_image_path(path)

    def get_source_image_path(self) -> Optional[str]:
        return self.session.get_source_image_path()

    def set_image(self, image: Any):
        self.session.set_image(image)
        self.image_changed.emit(image)

    def get_image(self) -> Optional[Any]:
        return self.session.get_image()

    def update_region(
        self,
        index: int,
        region: Dict[str, Any],
        *,
        fields: Iterable[str] | None = None,
        source: str = "",
    ) -> None:
        """更新单个 region，发出 updated([index])。"""
        if self.session.update_region(index, region):
            self.regions_changed.emit(RegionChange.updated([index], fields=fields, source=source))

    def update_regions(
        self,
        updates: Dict[int, Dict[str, Any]],
        *,
        fields: Iterable[str] | None = None,
        source: str = "",
    ) -> None:
        """批量更新多个 region，合并为一次 updated(indices) 通知。"""
        applied: list[int] = []
        for index, region in sorted(updates.items()):
            if self.session.update_region(index, region):
                applied.append(index)
        if applied:
            self.regions_changed.emit(RegionChange.updated(applied, fields=fields, source=source))

    def insert_region(self, index: int, region: Dict[str, Any]) -> int:
        """在 index 处插入 region，发出 inserted([实际位置])，返回实际位置。"""
        insert_at = self.session.insert_region(index, region)
        selection_changed = self.session.set_selection([])
        self.regions_changed.emit(RegionChange.inserted([insert_at]))
        if selection_changed:
            self.selection_changed.emit(self.session.get_selection())
        return insert_at

    def remove_region(self, index: int) -> Optional[Dict[str, Any]]:
        """移除 index 处的 region，发出 removed([index])，返回被移除的数据。"""
        removed = self.session.remove_region(index)
        if removed is not None:
            selection_changed = self.session.set_selection([])
            self.regions_changed.emit(RegionChange.removed([index]))
            if selection_changed:
                self.selection_changed.emit(self.session.get_selection())
        return removed

    def move_region(self, source_index: int, target_index: int) -> Optional[int]:
        """调整 region 顺序，并按稳定 ID 保留当前选区。"""
        region_count = len(self.get_regions())
        if not (0 <= source_index < region_count):
            return None
        target_index = max(0, min(int(target_index), region_count - 1))
        if source_index == target_index:
            return target_index

        selected_region_ids = [
            region_id
            for index in self.get_selection()
            if (region_id := self.get_region_id(index)) is not None
        ]
        moved_to = self.session.move_region(source_index, target_index)
        if moved_to is None:
            return None

        remapped_selection = sorted(
            index
            for region_id in selected_region_ids
            if (index := self.find_region_index(region_id)) is not None
        )
        selection_changed = self.session.set_selection(remapped_selection)
        self.regions_changed.emit(RegionChange.reset(source="reorder"))
        if selection_changed:
            self.selection_changed.emit(self.session.get_selection())
        return moved_to

    def replace_regions(self, regions: List[Dict[str, Any]]) -> None:
        """整体替换 region 列表（换图/清空/导入），发出 reset。"""
        self.session.set_regions(regions)
        self.regions_changed.emit(RegionChange.reset())

    def refresh_regions(self) -> None:
        """region 数据未变化，但所有 region 派生视图需要重新计算。"""
        self.regions_changed.emit(RegionChange.reset())

    def store_derived_regions(self, updates: Dict[int, Dict[str, Any]]) -> None:
        """写回渲染管线派生的 region 字段（render_box_rect_local 等），只传有变化的条目。

        派生数据由渲染计算产生，不属于用户编辑，因此不发出 regions_changed。
        """
        self.session.store_derived_regions(updates)

    def get_region_id(self, index: int) -> Optional[int]:
        """返回 index 处 region 的稳定 id，供异步任务跨时间定位。"""
        return self.session.get_region_id(index)

    def find_region_index(self, region_id: int) -> Optional[int]:
        return self.session.find_region_index(region_id)

    def get_regions(self) -> List[Dict[str, Any]]:
        return self.session.get_regions()

    def set_raw_mask(self, mask: Any):
        normalized = self.session.set_mask(MaskType.RAW, mask)
        self.raw_mask_changed.emit(normalized)

    def get_raw_mask(self) -> Optional[Any]:
        return self.session.get_mask(MaskType.RAW)

    def set_refined_mask(self, mask: Any):
        normalized = self.session.set_mask(MaskType.REFINED, mask)
        self.refined_mask_changed.emit(normalized)
        if self.session.get_display_mask_type() == "refined":
            self.display_mask_type_changed.emit("refined")

    def get_refined_mask(self) -> Optional[Any]:
        return self.session.get_mask(MaskType.REFINED)

    def set_display_mask_type(self, mask_type: str):
        if self.session.set_display_mask_type(mask_type):
            self.display_mask_type_changed.emit(mask_type)

    def get_display_mask_type(self) -> str:
        return self.session.get_display_mask_type()

    def set_inpainted_image_path(self, path: Optional[str]):
        self.session.set_inpainted_image_path(path)

    def get_inpainted_image_path(self) -> Optional[str]:
        return self.session.get_inpainted_image_path()

    def set_selection(self, indices: List[int]):
        if self.session.set_selection(indices):
            self.selection_changed.emit(self.session.get_selection())

    def get_selection(self) -> List[int]:
        return self.session.get_selection()

    def get_region_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        return self.session.get_region_by_index(index)

    def set_inpainted_image(self, image: Any):
        self.session.set_inpainted_image(image)
        self.inpainted_image_changed.emit(image)

    def get_inpainted_image(self) -> Optional[Any]:
        return self.session.get_inpainted_image()

    def set_compare_image(self, image: Any):
        self.session.set_compare_image(image)
        self.compare_image_changed.emit(image)

    def get_compare_image(self) -> Optional[Any]:
        return self.session.get_compare_image()

    def set_region_display_mode(self, mode: str):
        if self.session.set_region_display_mode(mode):
            self.region_display_mode_changed.emit(mode)

    def get_region_display_mode(self) -> str:
        return self.session.get_region_display_mode()

    def set_original_image_alpha(self, alpha: float):
        if self.session.set_original_image_alpha(alpha):
            self.original_image_alpha_changed.emit(alpha)

    def get_original_image_alpha(self) -> float:
        return self.session.get_original_image_alpha()

    def set_active_tool(self, tool: str):
        if self.session.set_active_tool(tool):
            self.active_tool_changed.emit(tool)

    def get_active_tool(self) -> str:
        return self.session.get_active_tool()

    def set_brush_size(self, size: int):
        if self.session.set_brush_size(size):
            self.brush_size_changed.emit(size)

    def get_brush_size(self) -> int:
        return self.session.get_brush_size()

    def set_brush_color(self, color: str):
        if self.session.set_brush_color(color):
            self.brush_color_changed.emit(self.session.get_brush_color())

    def get_brush_color(self) -> str:
        return self.session.get_brush_color()

    def set_paint_overlay_path(self, path: Optional[str]):
        self.session.set_paint_overlay_path(path)

    def get_paint_overlay_path(self) -> Optional[str]:
        return self.session.get_paint_overlay_path()

    def set_paint_overlay_image(self, image: Any):
        self.session.set_paint_overlay_image(image)
        self.paint_overlay_changed.emit(image)

    def get_paint_overlay_image(self) -> Optional[Any]:
        return self.session.get_paint_overlay_image()

    def set_stamp_overlay_image(self, image: Any):
        self.session.set_stamp_overlay_image(image)
        self.stamp_overlay_changed.emit(image)

    def get_stamp_overlay_image(self) -> Optional[Any]:
        return self.session.get_stamp_overlay_image()
