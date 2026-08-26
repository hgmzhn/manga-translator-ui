from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import numpy as np

from .core.types import MaskType
from .inpaint_state import InpaintArtifact, InpaintKey, InpaintState, MaskDelta
from .region_geometry_state import normalize_region_geometry_data

_UNSET = object()



def normalize_binary_mask(mask: Any) -> Optional[np.ndarray]:
    if mask is None:
        return None
    array = np.asarray(mask)
    if array.ndim == 3:
        array = array[..., 0]
    if array.ndim != 2:
        raise ValueError(f"mask must be 2D, got shape {array.shape}")
    normalized = np.where(array > 0, 255, 0).astype(np.uint8)
    normalized.setflags(write=False)
    return normalized


def _arrays_equal(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> bool:

    if left is None or right is None:
        return left is right
    return left.shape == right.shape and np.array_equal(left, right)

def _copy_image(image: Any) -> Any:
    if image is None:
        raise ValueError("active document source image is required")
    if isinstance(image, np.ndarray):
        owned = np.array(image, copy=True)
        owned.setflags(write=False)
        return owned
    copy_method = getattr(image, "copy", None)
    if callable(copy_method):
        return copy_method()
    return copy.deepcopy(image)


def _normalize_opacity(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _mask_delta(
    previous: Optional[np.ndarray],
    current: Optional[np.ndarray],
    mask_revision: int,
) -> MaskDelta:
    if current is None:
        if previous is None:
            raise ValueError("unchanged empty masks have no delta")
        added = np.zeros_like(previous)
        removed = previous
    elif previous is None or previous.shape != current.shape:
        added = current
        removed = np.zeros_like(current)
    else:
        added = np.where((current > 0) & (previous == 0), 255, 0).astype(
            np.uint8
        )
        removed = np.where((previous > 0) & (current == 0), 255, 0).astype(
            np.uint8
        )
    return MaskDelta(added, removed, mask_revision)


@dataclass(frozen=True, slots=True)
class LoadedInpaintSidecar:
    """Decoded same-page inpaint sidecar ready for document installation."""

    image: np.ndarray

    def __post_init__(self) -> None:
        image = np.asarray(self.image)
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError(f"invalid inpainted image shape: {image.shape}")
        owned = np.array(image[..., :3], copy=True)
        owned.setflags(write=False)
        object.__setattr__(self, "image", owned)


@dataclass(slots=True)
class DocumentSnapshot:
    """Complete load result; installing it replaces the active document atomically."""

    source_path: str
    image: Any
    display_image_path: Optional[str] = None
    source_qimage: Any = None
    compare_image: Any = None
    regions: list[dict] = field(default_factory=list)
    raw_mask: Any = None
    inpaint_sidecar: Optional[LoadedInpaintSidecar] = None
    paint_overlay_path: Optional[str] = None
    paint_overlay_image: Any = None
    stamp_overlay_image: Any = None


@dataclass(frozen=True, slots=True)
class DocumentLoadFailure:
    error: str


@dataclass(slots=True)
class DocumentRevisions:
    content: int
    base: int = 1
    mask: int = 0


@dataclass(slots=True)
class RegionRecord:
    region_id: int
    data: dict


class RegionCollection:
    """Ordered regions with stable IDs owned by one document."""

    def __init__(self, regions: list[dict] | None = None):
        self._records: list[RegionRecord] = []
        self._next_id = 0
        if regions:
            self.replace(regions)

    def replace(self, regions: list[dict]) -> None:
        self._records = [self._new_record(region) for region in regions]

    def _new_record(self, region: dict) -> RegionRecord:
        record = RegionRecord(
            self._next_id,
            copy.deepcopy(normalize_region_geometry_data(region)),
        )
        self._next_id += 1
        return record


    def snapshot(self) -> list[dict]:
        return [copy.deepcopy(record.data) for record in self._records]

    def get(self, index: int) -> Optional[dict]:
        if 0 <= index < len(self._records):
            return copy.deepcopy(self._records[index].data)
        return None

    def region_id(self, index: int) -> Optional[int]:
        if 0 <= index < len(self._records):
            return self._records[index].region_id
        return None

    def index_of(self, region_id: int) -> Optional[int]:
        for index, record in enumerate(self._records):
            if record.region_id == region_id:
                return index
        return None

    def update(self, index: int, region: dict) -> bool:
        if not (0 <= index < len(self._records)):
            return False
        self._records[index].data = copy.deepcopy(
            normalize_region_geometry_data(region)
        )
        return True

    def insert(self, index: int, region: dict) -> int:
        insert_at = max(0, min(int(index), len(self._records)))
        self._records.insert(insert_at, self._new_record(region))
        return insert_at

    def remove(self, index: int) -> Optional[dict]:
        if not (0 <= index < len(self._records)):
            return None
        return self._records.pop(index).data

    def move(self, source_index: int, target_index: int) -> Optional[int]:
        if not (0 <= source_index < len(self._records)):
            return None
        target_index = max(0, min(int(target_index), len(self._records) - 1))
        if source_index != target_index:
            self._records.insert(target_index, self._records.pop(source_index))
        return target_index


@dataclass(slots=True)
class MaskState:
    raw: Optional[np.ndarray] = None
    refined: Optional[np.ndarray] = None

    @classmethod
    def from_raw(cls, raw_mask: Any) -> "MaskState":
        raw = normalize_binary_mask(raw_mask)
        refined = None if raw is None else normalize_binary_mask(raw)
        return cls(raw=raw, refined=refined)

    def get(self, mask_type: MaskType) -> Optional[np.ndarray]:
        return self.raw if mask_type == MaskType.RAW else self.refined

    def effective(self) -> Optional[np.ndarray]:
        return self.refined if self.refined is not None else self.raw


@dataclass(frozen=True, slots=True)
class MaskMutation:
    raw: Optional[np.ndarray]
    refined: Optional[np.ndarray]
    raw_changed: bool
    refined_changed: bool
    effective: Optional[np.ndarray]
    delta: Optional[MaskDelta]


@dataclass(slots=True)
class OverlayState:
    paint_path: Optional[str] = None
    paint: Optional[np.ndarray] = None
    stamp: Optional[np.ndarray] = None

    @staticmethod
    def normalize(image: Any) -> Optional[np.ndarray]:
        if image is None:
            return None
        array = np.asarray(image)
        if array.ndim != 3 or array.shape[2] != 4:
            raise ValueError(f"overlay must be RGBA, got shape {array.shape}")
        normalized = array.astype(np.uint8, copy=True)
        if not np.any(normalized[..., 3]):
            return None
        normalized.setflags(write=False)
        return normalized


@dataclass(frozen=True, slots=True)
class DisplayLayers:
    document_id: int
    source_path: str
    source_image: Any
    inpaint_display_image: Any
    source_opacity: float


    def __post_init__(self) -> None:
        if self.source_image is None or self.inpaint_display_image is None:
            raise ValueError("both editor display layers are required")
        object.__setattr__(self, "source_opacity", _normalize_opacity(self.source_opacity))
    @property
    def identity(self) -> tuple[int, str]:
        return self.document_id, self.source_path


@dataclass(frozen=True, slots=True)
class ExportBase:
    kind: Literal["source", "paired", "backend_inpaint"]
    source_image: Any
    render_image: Any
    mask: Optional[np.ndarray]
    inpaint_key: Optional[InpaintKey] = None

    def __post_init__(self) -> None:
        if self.source_image is None or self.render_image is None:
            raise ValueError("export source and render images are required")
        has_mask = self.mask is not None and bool(np.any(self.mask))
        if self.kind == "source":
            if has_mask or self.inpaint_key is not None:
                raise ValueError("source export cannot carry a mask or inpaint key")
            if self.render_image is not self.source_image:
                raise ValueError("source export must render the current source image")
            return
        if self.kind not in {"paired", "backend_inpaint"}:
            raise ValueError(f"unsupported export base kind: {self.kind}")
        if not has_mask or self.inpaint_key is None:
            raise ValueError(f"{self.kind} export requires a non-empty mask and key")
        object.__setattr__(self, "mask", np.array(self.mask, copy=True))
        if self.kind == "backend_inpaint" and self.render_image is not self.source_image:
            raise ValueError("backend inpaint must start from the current source image")

@dataclass(slots=True)
class EditorWorkspaceState:
    display_mask_type: str = "none"
    selected_region_ids: list[int] = field(default_factory=list)
    region_display_mode: str = "full"
    source_opacity: float = 0.0
    active_tool: str = "select"
    brush_size: int = 30
    brush_color: str = "#ffffff"

    def set_source_opacity(self, value: float) -> bool:
        normalized = _normalize_opacity(value)
        if normalized == self.source_opacity:
            return False
        self.source_opacity = normalized
        return True

@dataclass(slots=True)
class EditorDocument:
    document_id: int
    source_path: str
    source_image: Any
    source_qimage: Any
    compare_image: Any
    regions: RegionCollection
    masks: MaskState
    overlays: OverlayState
    revisions: DocumentRevisions
    inpaint_display_image: Any
    inpaint: InpaintState = field(default_factory=InpaintState)

    @classmethod
    def from_snapshot(
        cls,
        document_id: int,
        snapshot: DocumentSnapshot,
    ) -> "EditorDocument":
        masks = MaskState.from_raw(snapshot.raw_mask)
        mask_revision = 1 if masks.effective() is not None else 0
        document = cls(
            document_id=document_id,
            source_path=snapshot.source_path,
            source_image=snapshot.image,
            source_qimage=snapshot.source_qimage,
            compare_image=(
                snapshot.compare_image
                if snapshot.compare_image is not None
                else snapshot.image
            ),
            regions=RegionCollection(snapshot.regions),
            masks=masks,
            overlays=OverlayState(
                paint_path=snapshot.paint_overlay_path,
                paint=OverlayState.normalize(snapshot.paint_overlay_image),
                stamp=OverlayState.normalize(snapshot.stamp_overlay_image),
            ),
            revisions=DocumentRevisions(
                content=(int(document_id) << 32) + 1,
                base=1,
                mask=mask_revision,
            ),
            inpaint_display_image=_copy_image(snapshot.image),
        )
        stored = snapshot.inpaint_sidecar
        current_mask = masks.effective()
        if (
            stored is not None
            and current_mask is not None
            and np.any(current_mask)
            and stored.image.shape[:2] == current_mask.shape
        ):
            artifact = InpaintArtifact(
                document.inpaint_key(),
                current_mask,
                stored.image,
            )
            document.inpaint.install_ready(artifact)
            document.inpaint_display_image = artifact.image
        return document

    def _touch(self) -> None:
        self.revisions.content += 1

    def inpaint_key(self) -> InpaintKey:
        return InpaintKey(
            self.document_id,
            self.revisions.base,
            self.revisions.mask,
            self.inpaint.generation,
        )



    def replace_regions(self, regions: list[dict]) -> None:
        self.regions.replace(regions)
        self._touch()

    def replace_masks(
        self,
        *,
        raw: Any = _UNSET,
        refined: Any = _UNSET,
    ) -> MaskMutation:
        previous_raw = self.masks.raw
        previous_refined = self.masks.refined
        previous_effective = self.masks.effective()

        next_raw = previous_raw if raw is _UNSET else normalize_binary_mask(raw)
        next_refined = (
            previous_refined if refined is _UNSET else normalize_binary_mask(refined)
        )
        raw_changed = not _arrays_equal(previous_raw, next_raw)
        refined_changed = not _arrays_equal(previous_refined, next_refined)
        if not raw_changed and not refined_changed:
            return MaskMutation(
                previous_raw,
                previous_refined,
                False,
                False,
                previous_effective,
                None,
            )

        self.masks.raw = next_raw
        self.masks.refined = next_refined
        current_effective = self.masks.effective()
        delta = None
        if not _arrays_equal(previous_effective, current_effective):
            self.revisions.mask += 1
            clear_committed = current_effective is None or not np.any(current_effective)
            committed = self.inpaint.committed
            self.inpaint.invalidate(clear_committed=clear_committed)
            if clear_committed or committed is None:
                self.inpaint_display_image = _copy_image(self.source_image)
            else:
                self.inpaint_display_image = committed.image
            delta = _mask_delta(
                previous_effective,
                current_effective,
                self.revisions.mask,
            )
        self._touch()
        return MaskMutation(
            self.masks.raw,
            self.masks.refined,
            raw_changed,
            refined_changed,
            current_effective,
            delta,
        )

    def ready_inpaint_artifact(self) -> Optional[InpaintArtifact]:
        return self.inpaint.ready_artifact(
            self.inpaint_key(),
            self.masks.effective(),
        )

    def install_inpaint_artifact(
        self,
        artifact: InpaintArtifact,
    ) -> bool:
        current_mask = self.masks.effective()
        if (
            artifact.key != self.inpaint_key()
            or current_mask is None
            or not np.any(current_mask)
            or artifact.mask.shape != current_mask.shape
            or not np.array_equal(artifact.mask, current_mask)
            or artifact.image.ndim != 3
            or artifact.image.shape[:2] != current_mask.shape
        ):
            return False
        self.inpaint.install_ready(artifact)
        self.inpaint_display_image = artifact.image
        self._touch()
        return True

    def display_layers(self, source_opacity: float) -> DisplayLayers:
        return DisplayLayers(
            self.document_id,
            self.source_path,
            self.source_image,
            self.inpaint_display_image,
            _normalize_opacity(source_opacity),
        )

    def export_base(self) -> ExportBase:
        mask = self.masks.effective()
        if mask is None or not np.any(mask):
            return ExportBase(
                "source",
                self.source_image,
                self.source_image,
                None,
                None,
            )
        artifact = self.ready_inpaint_artifact()
        if artifact is not None:
            return ExportBase(
                "paired",
                self.source_image,
                artifact.image,
                artifact.mask,
                artifact.key,
            )
        return ExportBase(
            "backend_inpaint",
            self.source_image,
            self.source_image,
            mask,
            self.inpaint_key(),
        )


__all__ = [
    "DisplayLayers",
    "DocumentLoadFailure",
    "DocumentSnapshot",
    "EditorDocument",
    "EditorWorkspaceState",
    "ExportBase",
    "LoadedInpaintSidecar",
]
