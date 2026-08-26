from __future__ import annotations

from typing import Any, Optional

from .core.types import MaskType
from .document_state import (
    _UNSET,
    DisplayLayers,
    DocumentLoadFailure,
    DocumentSnapshot,
    EditorDocument,
    EditorWorkspaceState,
    ExportBase,
    LoadedInpaintSidecar,
    MaskMutation,
    _arrays_equal,
)
from .inpaint_state import InpaintArtifact, InpaintKey


class EditorSession:
    """Single owner of the active document and persistent editor workspace state."""

    def __init__(self):
        self._workspace = EditorWorkspaceState()
        self._document: Optional[EditorDocument] = None
        self._next_document_id = 0


    def _allocate_document_id(self) -> int:
        self._next_document_id += 1
        return self._next_document_id

    def _discard_document(self) -> None:
        document = self._document
        self._document = None
        self._workspace.selected_region_ids.clear()
        if document is not None:
            document.inpaint.invalidate(clear_committed=True)

    def load_document(self, snapshot: DocumentSnapshot) -> None:
        """Replace the active document as one identity-scoped state transition."""
        self._discard_document()
        document_id = self._allocate_document_id()
        self._document = EditorDocument.from_snapshot(document_id, snapshot)

    def clear_document(self) -> None:
        self._discard_document()
        self._allocate_document_id()


    def get_document_identity(self) -> Optional[tuple[int, str]]:
        document = self._document
        if document is None:
            return None
        return document.document_id, document.source_path

    def get_document_revision(self) -> int:
        document = self._document
        return 0 if document is None else document.revisions.content

    def get_document_id(self) -> int:
        document = self._document
        return self._next_document_id if document is None else document.document_id

    def get_base_revision(self) -> int:
        document = self._document
        return 0 if document is None else document.revisions.base

    def get_mask_revision(self) -> int:
        document = self._document
        return 0 if document is None else document.revisions.mask

    def get_source_image_path(self) -> Optional[str]:
        document = self._document
        return None if document is None else document.source_path

    def get_image(self) -> Any:
        document = self._document
        return None if document is None else document.source_image

    def get_source_qimage(self) -> Any:
        document = self._document
        return None if document is None else document.source_qimage

    def get_compare_image(self) -> Any:
        document = self._document
        return None if document is None else document.compare_image

    def set_compare_image(self, image: Any) -> bool:
        document = self._document
        if document is None or image is document.compare_image:
            return False
        document.compare_image = image
        document._touch()
        return True

    def set_regions(self, regions: list[dict]) -> None:
        document = self._document
        if document is None:
            return
        document.replace_regions(regions)
        self._workspace.selected_region_ids.clear()

    def update_region(self, index: int, region: dict) -> bool:
        document = self._document
        if document is None or not document.regions.update(index, region):
            return False
        document._touch()
        return True

    def insert_region(self, index: int, region: dict) -> int:
        document = self._document
        if document is None:
            raise RuntimeError("cannot insert a region without an active document")
        insert_at = document.regions.insert(index, region)
        document._touch()
        return insert_at

    def remove_region(self, index: int) -> Optional[dict]:
        document = self._document
        if document is None:
            return None
        region_id = document.regions.region_id(index)
        removed = document.regions.remove(index)
        if removed is None:
            return None
        if region_id in self._workspace.selected_region_ids:
            self._workspace.selected_region_ids.remove(region_id)
        document._touch()
        return removed

    def move_region(self, source_index: int, target_index: int) -> Optional[int]:
        document = self._document
        if document is None:
            return None
        moved_to = document.regions.move(source_index, target_index)
        if moved_to is not None and moved_to != source_index:
            document._touch()
        return moved_to

    def store_derived_regions(self, updates: dict[int, dict]) -> None:
        document = self._document
        if document is None:
            return
        changed = False
        for index, region in updates.items():
            changed = document.regions.update(index, region) or changed
        if changed:
            document._touch()

    def get_region_id(self, index: int) -> Optional[int]:
        document = self._document
        return None if document is None else document.regions.region_id(index)

    def find_region_index(self, region_id: int) -> Optional[int]:
        document = self._document
        return None if document is None else document.regions.index_of(region_id)
    def get_regions(self) -> list[dict]:
        document = self._document
        return [] if document is None else document.regions.snapshot()

    def get_region_by_index(self, index: int) -> Optional[dict]:
        document = self._document
        return None if document is None else document.regions.get(index)

    def set_selection(self, indices: list[int]) -> bool:
        document = self._document
        region_ids: list[int] = []
        if document is not None:
            for index in sorted(set(int(value) for value in indices)):
                region_id = document.regions.region_id(index)
                if region_id is not None:
                    region_ids.append(region_id)
        if region_ids == self._workspace.selected_region_ids:
            return False
        self._workspace.selected_region_ids = region_ids
        return True

    def get_selection(self) -> list[int]:
        document = self._document
        if document is None:
            return []
        indices = (
            document.regions.index_of(region_id)
            for region_id in self._workspace.selected_region_ids
        )
        return sorted(index for index in indices if index is not None)

    def replace_masks(
        self,
        *,
        raw: Any = _UNSET,
        refined: Any = _UNSET,
    ) -> MaskMutation:
        document = self._document
        if document is None:
            return MaskMutation(None, None, False, False, None, None)
        return document.replace_masks(raw=raw, refined=refined)


    def get_mask(self, mask_type: MaskType) -> Any:
        document = self._document
        return None if document is None else document.masks.get(mask_type)

    def get_effective_mask(self) -> Any:
        document = self._document
        return None if document is None else document.masks.effective()


    def get_inpaint_key(self) -> InpaintKey:
        document = self._document
        if document is None:
            return InpaintKey(self._next_document_id, 0, 0, 0)
        return document.inpaint_key()

    def get_ready_inpaint_artifact(self) -> Optional[InpaintArtifact]:
        document = self._document
        artifact = None if document is None else document.ready_inpaint_artifact()
        return None if artifact is None else artifact.snapshot()

    def get_committed_inpaint_artifact(self) -> Optional[InpaintArtifact]:
        document = self._document
        if document is None or document.inpaint.committed is None:
            return None
        return document.inpaint.committed.snapshot()


    def begin_inpaint(self, key: InpaintKey, future: Any) -> bool:
        document = self._document
        if document is None or key != document.inpaint_key():
            if future is not None and not future.done():
                future.cancel()
            return False
        return document.inpaint.begin(key, future)

    def fail_inpaint(self, key: InpaintKey) -> bool:
        document = self._document
        if document is None:
            return False
        return document.inpaint.fail(key, document.inpaint_key())

    def install_inpaint_artifact(self, artifact: InpaintArtifact) -> bool:
        document = self._document
        return bool(
            document is not None
            and document.install_inpaint_artifact(artifact)
        )


    def get_display_layers(self) -> Optional[DisplayLayers]:
        document = self._document
        if document is None:
            return None
        return document.display_layers(self._workspace.source_opacity)

    def get_export_base(self) -> Optional[ExportBase]:
        document = self._document
        return None if document is None else document.export_base()

    def set_display_mask_type(self, mask_type: str) -> bool:
        if mask_type not in {"raw", "refined", "none"}:
            return False
        if self._workspace.display_mask_type == mask_type:
            return False
        self._workspace.display_mask_type = mask_type
        return True

    def get_display_mask_type(self) -> str:
        return self._workspace.display_mask_type

    def set_region_display_mode(self, mode: str) -> bool:
        if self._workspace.region_display_mode == mode:
            return False
        self._workspace.region_display_mode = mode
        return True

    def get_region_display_mode(self) -> str:
        return self._workspace.region_display_mode

    def set_original_image_alpha_override(self, alpha: float) -> bool:
        return self._workspace.set_source_opacity(alpha)

    def get_original_image_alpha(self) -> float:
        return self._workspace.source_opacity

    def set_active_tool(self, tool: str) -> bool:
        if self._workspace.active_tool == tool:
            return False
        self._workspace.active_tool = tool
        return True

    def get_active_tool(self) -> str:
        return self._workspace.active_tool

    def set_brush_size(self, size: int) -> bool:
        size = int(size)
        if self._workspace.brush_size == size:
            return False
        self._workspace.brush_size = size
        return True

    def get_brush_size(self) -> int:
        return self._workspace.brush_size

    def set_brush_color(self, color: str) -> bool:
        value = str(color or "").strip() or "#ff0000"
        if self._workspace.brush_color == value:
            return False
        self._workspace.brush_color = value
        return True

    def get_brush_color(self) -> str:
        return self._workspace.brush_color

    def set_paint_overlay_path(self, path: Optional[str]) -> None:
        document = self._document
        if document is not None:
            document.overlays.paint_path = path

    def get_paint_overlay_path(self) -> Optional[str]:
        document = self._document
        return None if document is None else document.overlays.paint_path

    def set_paint_overlay_image(self, image: Any) -> bool:
        document = self._document
        if document is None:
            return False
        normalized = document.overlays.normalize(image)
        if _arrays_equal(document.overlays.paint, normalized):
            return False
        document.overlays.paint = normalized
        document._touch()
        return True

    def get_paint_overlay_image(self) -> Any:
        document = self._document
        return None if document is None else document.overlays.paint

    def set_stamp_overlay_image(self, image: Any) -> bool:
        document = self._document
        if document is None:
            return False
        normalized = document.overlays.normalize(image)
        if _arrays_equal(document.overlays.stamp, normalized):
            return False
        document.overlays.stamp = normalized
        document._touch()
        return True

    def get_stamp_overlay_image(self) -> Any:
        document = self._document
        return None if document is None else document.overlays.stamp


__all__ = [
    "DocumentLoadFailure",
    "DocumentSnapshot",
    "EditorSession",
    "LoadedInpaintSidecar",
]
