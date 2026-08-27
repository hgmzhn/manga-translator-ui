from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .core.types import MaskType
from .document_state import (
    _UNSET,
    DisplayLayers,
    DocumentSnapshot,
    ExportBase,
    MaskMutation,
)
from .inpaint_state import InpaintArtifact, InpaintKey
from .region_change import RegionChange
from .session import EditorSession


class EditorModel(QObject):
    """Qt event boundary over the session-owned document aggregate."""

    regions_changed = pyqtSignal(object)
    raw_mask_changed = pyqtSignal(object)
    refined_mask_changed = pyqtSignal(object)
    effective_mask_delta_changed = pyqtSignal(object, object)
    display_mask_type_changed = pyqtSignal(str)
    selection_changed = pyqtSignal(list)
    display_layers_changed = pyqtSignal(object)
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
        self.session = EditorSession()

    def get_document_id(self) -> int:
        return self.session.get_document_id()

    def get_document_identity(self) -> Optional[tuple[int, str]]:
        return self.session.get_document_identity()

    def get_mask_revision(self) -> int:
        return self.session.get_mask_revision()

    def get_ready_inpaint_artifact(self) -> Optional[InpaintArtifact]:
        return self.session.get_ready_inpaint_artifact()

    def get_committed_inpaint_artifact(self) -> Optional[InpaintArtifact]:
        return self.session.get_committed_inpaint_artifact()

    def get_inpaint_key(self) -> InpaintKey:
        return self.session.get_inpaint_key()

    def bump_inpaint_revision(self) -> Optional[InpaintKey]:
        return self.session.bump_inpaint_revision()

    def _emit_document_projection(self) -> None:
        self.original_image_alpha_changed.emit(self.get_original_image_alpha())
        self.display_layers_changed.emit(self.get_display_layers())
        self.compare_image_changed.emit(self.get_compare_image())
        self.regions_changed.emit(RegionChange.reset())
        self.raw_mask_changed.emit(self.get_raw_mask())
        self.refined_mask_changed.emit(self.get_refined_mask())
        self.paint_overlay_changed.emit(self.get_paint_overlay_image())
        self.stamp_overlay_changed.emit(self.get_stamp_overlay_image())
        self.selection_changed.emit(self.get_selection())

    def apply_document_snapshot(self, snapshot: DocumentSnapshot) -> None:
        self.session.load_document(snapshot)
        self._emit_document_projection()

    def clear_document(self) -> None:
        self.session.clear_document()
        self._emit_document_projection()

    def get_source_image_path(self) -> Optional[str]:
        return self.session.get_source_image_path()

    def get_image(self) -> Optional[Any]:
        return self.session.get_image()

    def get_source_rgb(self) -> Optional[Any]:
        return self.session.get_source_rgb()

    def get_source_qimage(self) -> Any:
        return self.session.get_source_qimage()

    def get_display_layers(self) -> Optional[DisplayLayers]:
        return self.session.get_display_layers()

    def get_export_base(self) -> Optional[ExportBase]:
        return self.session.get_export_base()

    def update_region(
        self,
        index: int,
        region: Dict[str, Any],
        *,
        fields: Iterable[str] | None = None,
        source: str = "",
    ) -> None:
        if self.session.update_region(index, region):
            self.regions_changed.emit(
                RegionChange.updated([index], fields=fields, source=source)
            )

    def update_regions(
        self,
        updates: Dict[int, Dict[str, Any]],
        *,
        fields: Iterable[str] | None = None,
        source: str = "",
    ) -> None:
        applied: list[int] = []
        for index, region in sorted(updates.items()):
            if self.session.update_region(index, region):
                applied.append(index)
        if applied:
            self.regions_changed.emit(
                RegionChange.updated(applied, fields=fields, source=source)
            )

    def insert_region(self, index: int, region: Dict[str, Any]) -> int:
        previous_selection = self.get_selection()
        insert_at = self.session.insert_region(index, region)
        self.regions_changed.emit(RegionChange.inserted([insert_at]))
        current_selection = self.get_selection()
        if current_selection != previous_selection:
            self.selection_changed.emit(current_selection)
        return insert_at

    def remove_region(self, index: int) -> Optional[Dict[str, Any]]:
        previous_selection = self.get_selection()
        removed = self.session.remove_region(index)
        if removed is not None:
            self.regions_changed.emit(RegionChange.removed([index]))
            current_selection = self.get_selection()
            if current_selection != previous_selection:
                self.selection_changed.emit(current_selection)
        return removed

    def move_region(self, source_index: int, target_index: int) -> Optional[int]:
        previous_selection = self.get_selection()
        moved_to = self.session.move_region(source_index, target_index)
        if moved_to is None:
            return None
        if moved_to != source_index:
            self.regions_changed.emit(RegionChange.reset(source="reorder"))
            current_selection = self.get_selection()
            if current_selection != previous_selection:
                self.selection_changed.emit(current_selection)
        return moved_to

    def replace_regions(self, regions: List[Dict[str, Any]]) -> None:
        previous_selection = self.get_selection()
        self.session.set_regions(regions)
        self.regions_changed.emit(RegionChange.reset())
        if self.get_selection() != previous_selection:
            self.selection_changed.emit(self.get_selection())

    def refresh_regions(self) -> None:
        self.regions_changed.emit(RegionChange.reset())

    def store_derived_regions(self, updates: Dict[int, Dict[str, Any]]) -> None:
        self.session.store_derived_regions(updates)

    def get_region_id(self, index: int) -> Optional[int]:
        return self.session.get_region_id(index)

    def find_region_index(self, region_id: int) -> Optional[int]:
        return self.session.find_region_index(region_id)

    def get_regions(self) -> List[Dict[str, Any]]:
        return self.session.get_regions()

    def get_region_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        return self.session.get_region_by_index(index)

    def set_masks(
        self,
        *,
        raw: Any = _UNSET,
        refined: Any = _UNSET,
    ) -> MaskMutation:
        mutation = self.session.replace_masks(raw=raw, refined=refined)
        if mutation.raw_changed:
            self.raw_mask_changed.emit(mutation.raw)
        if mutation.refined_changed:
            self.refined_mask_changed.emit(mutation.refined)
            if self.session.get_display_mask_type() == "refined":
                self.display_mask_type_changed.emit("refined")
        if mutation.delta is not None:
            self.display_layers_changed.emit(self.get_display_layers())
            self.effective_mask_delta_changed.emit(mutation.effective, mutation.delta)
        return mutation

    def set_raw_mask(self, mask: Any) -> MaskMutation:
        return self.set_masks(raw=mask)

    def get_raw_mask(self) -> Optional[Any]:
        return self.session.get_mask(MaskType.RAW)

    def set_refined_mask(self, mask: Any) -> MaskMutation:
        return self.set_masks(refined=mask)

    def get_refined_mask(self) -> Optional[Any]:
        return self.session.get_mask(MaskType.REFINED)

    def get_effective_mask(self) -> Optional[Any]:
        return self.session.get_effective_mask()

    def set_display_mask_type(self, mask_type: str):
        if self.session.set_display_mask_type(mask_type):
            self.display_mask_type_changed.emit(mask_type)

    def get_display_mask_type(self) -> str:
        return self.session.get_display_mask_type()

    def set_selection(self, indices: List[int]):
        if self.session.set_selection(indices):
            self.selection_changed.emit(self.session.get_selection())

    def get_selection(self) -> List[int]:
        return self.session.get_selection()

    def begin_inpaint(self, key: InpaintKey, future: Any) -> bool:
        return self.session.begin_inpaint(key, future)

    def fail_inpaint(self, key: InpaintKey) -> bool:
        return self.session.fail_inpaint(key)

    def install_inpaint_artifact(self, artifact: InpaintArtifact) -> bool:
        if not self.session.install_inpaint_artifact(artifact):
            return False
        self.display_layers_changed.emit(self.get_display_layers())
        return True

    def set_compare_image(self, image: Any):
        if self.session.set_compare_image(image):
            self.compare_image_changed.emit(image)

    def get_compare_image(self) -> Optional[Any]:
        return self.session.get_compare_image()

    def set_region_display_mode(self, mode: str):
        if self.session.set_region_display_mode(mode):
            self.region_display_mode_changed.emit(mode)

    def get_region_display_mode(self) -> str:
        return self.session.get_region_display_mode()

    def set_original_image_alpha_override(self, alpha: float):
        if self.session.set_original_image_alpha_override(alpha):
            self.original_image_alpha_changed.emit(self.get_original_image_alpha())
            self.display_layers_changed.emit(self.get_display_layers())

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
        if self.session.set_paint_overlay_image(image):
            self.paint_overlay_changed.emit(self.get_paint_overlay_image())

    def get_paint_overlay_image(self) -> Optional[Any]:
        return self.session.get_paint_overlay_image()

    def set_stamp_overlay_image(self, image: Any):
        if self.session.set_stamp_overlay_image(image):
            self.stamp_overlay_changed.emit(self.get_stamp_overlay_image())

    def get_stamp_overlay_image(self) -> Optional[Any]:
        return self.session.get_stamp_overlay_image()
