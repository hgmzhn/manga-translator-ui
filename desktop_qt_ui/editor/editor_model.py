from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .core.types import MaskType
from .session import DocumentSnapshot, EditorSession


class EditorModel(QObject):
    """
    编辑器数据模型 (Model)。

    对外保留原有 signal 接口，内部状态统一委托给 EditorSession。
    """

    image_changed = pyqtSignal(object)
    regions_changed = pyqtSignal(list)
    raw_mask_changed = pyqtSignal(object)
    refined_mask_changed = pyqtSignal(object)
    display_mask_type_changed = pyqtSignal(str)
    selection_changed = pyqtSignal(list)
    inpainted_image_changed = pyqtSignal(object)
    compare_image_changed = pyqtSignal(object)
    region_display_mode_changed = pyqtSignal(str)
    original_image_alpha_changed = pyqtSignal(float)
    region_style_updated = pyqtSignal(int)
    active_tool_changed = pyqtSignal(str)
    brush_size_changed = pyqtSignal(int)
    brush_color_changed = pyqtSignal(str)
    paint_overlay_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        from services import get_resource_manager

        self.resource_manager = get_resource_manager()
        self.session = EditorSession(self.resource_manager)
        self.controller = None

    @staticmethod
    def _normalize_binary_mask(mask: Any):
        return EditorSession._normalize_binary_mask(mask)

    def get_document_revision(self) -> int:
        return self.session.get_document_revision()

    def apply_document_snapshot(self, snapshot: DocumentSnapshot) -> None:
        previous_selection = self.get_selection()
        self.session.load_document(snapshot)
        self.image_changed.emit(self.get_image())
        self.compare_image_changed.emit(self.get_compare_image())
        self.regions_changed.emit(self.get_regions())
        self.raw_mask_changed.emit(self.get_raw_mask())
        self.refined_mask_changed.emit(self.get_refined_mask())
        self.inpainted_image_changed.emit(self.get_inpainted_image())
        self.paint_overlay_changed.emit(self.get_paint_overlay_image())
        current_selection = self.get_selection()
        if current_selection != previous_selection:
            self.selection_changed.emit(current_selection)

    def clear_document(self) -> None:
        self.session.clear_document()
        self.image_changed.emit(self.get_image())
        self.compare_image_changed.emit(self.get_compare_image())
        self.regions_changed.emit(self.get_regions())
        self.raw_mask_changed.emit(self.get_raw_mask())
        self.refined_mask_changed.emit(self.get_refined_mask())
        self.inpainted_image_changed.emit(self.get_inpainted_image())
        self.paint_overlay_changed.emit(self.get_paint_overlay_image())
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

    def set_regions(self, regions: List[Dict[str, Any]]):
        self.session.set_regions(regions)
        self.regions_changed.emit(self.get_regions())

    def set_regions_silent(self, regions: List[Dict[str, Any]]):
        self.session.set_regions_silent(regions)

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
