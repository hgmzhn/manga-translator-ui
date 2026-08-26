from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

import numpy as np


def _owned_array(value: np.ndarray) -> np.ndarray:
    array = np.array(value, copy=True)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class InpaintKey:
    document_id: int
    base_revision: int
    mask_revision: int
    generation: int


@dataclass(frozen=True, slots=True)
class MaskDelta:
    added: np.ndarray
    removed: np.ndarray
    mask_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "added", _owned_array(self.added))
        object.__setattr__(self, "removed", _owned_array(self.removed))

    def snapshot(self) -> "MaskDelta":
        return MaskDelta(self.added, self.removed, self.mask_revision)


@dataclass(frozen=True, slots=True)
class MaskRefineRequest:
    document_id: int
    base_revision: int
    mask_revision: int
    raw_mask: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_mask", _owned_array(self.raw_mask))


@dataclass(frozen=True, slots=True)
class MaskRefineResult:
    document_id: int
    base_revision: int
    mask_revision: int
    refined_mask: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "refined_mask", _owned_array(self.refined_mask))


@dataclass(frozen=True, slots=True)
class InpaintConfigSnapshot:
    inpainter: str
    inpainting_precision: str
    force_use_torch_inpainting: bool
    inpainting_size: int
    device: str


@dataclass(frozen=True, slots=True)
class InpaintArtifact:
    key: InpaintKey
    mask: np.ndarray
    image: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "mask", _owned_array(self.mask))
        object.__setattr__(self, "image", _owned_array(self.image))

    def snapshot(self) -> "InpaintArtifact":
        return InpaintArtifact(self.key, self.mask, self.image)


@dataclass(frozen=True, slots=True)
class InpaintRequest:
    key: InpaintKey
    image: np.ndarray
    mask: np.ndarray
    delta: MaskDelta
    previous_artifact: Optional[InpaintArtifact]
    config: InpaintConfigSnapshot

    def __post_init__(self) -> None:
        object.__setattr__(self, "image", _owned_array(self.image))
        object.__setattr__(self, "mask", _owned_array(self.mask))
        object.__setattr__(self, "delta", self.delta.snapshot())
        if self.previous_artifact is not None:
            object.__setattr__(
                self, "previous_artifact", self.previous_artifact.snapshot()
            )


@dataclass(frozen=True, slots=True)
class InpaintResult:
    key: InpaintKey
    mask: np.ndarray
    image: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "mask", _owned_array(self.mask))
        object.__setattr__(self, "image", _owned_array(self.image))


InpaintStatus = Literal["idle", "running", "ready", "error"]


@dataclass(slots=True)
class InpaintState:
    """Mutable inpaint lifecycle with read-only public state.

    All transitions go through methods on this object so callers cannot leave a
    future, expected key, status, and committed artifact out of sync.
    """

    _generation: int = 0
    _status: InpaintStatus = "idle"
    _active_future: Any = None
    _expected_key: Optional[InpaintKey] = None
    _committed: Optional[InpaintArtifact] = None

    def __post_init__(self) -> None:
        if self._status not in {"idle", "running", "ready", "error"}:
            raise ValueError(f"Unsupported inpaint status: {self._status}")
        if self._committed is not None:
            self._committed = self._committed.snapshot()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def status(self) -> InpaintStatus:
        return self._status

    @property
    def active_future(self) -> Any:
        return self._active_future

    @property
    def expected_key(self) -> Optional[InpaintKey]:
        return self._expected_key

    @property
    def committed(self) -> Optional[InpaintArtifact]:
        return self._committed

    def _cancel_active(self) -> None:
        future = self._active_future
        self._active_future = None
        if future is not None and not future.done():
            future.cancel()

    def invalidate(self, *, clear_committed: bool) -> None:
        self._cancel_active()
        self._generation += 1
        self._status = "idle"
        self._expected_key = None
        if clear_committed:
            self._committed = None

    def begin(self, key: InpaintKey, future: Any) -> bool:
        self._cancel_active()
        self._expected_key = key
        self._active_future = future
        self._status = "running" if future is not None else "error"
        return future is not None

    def fail(self, key: InpaintKey, current_key: InpaintKey) -> bool:
        if key != self._expected_key or key != current_key:
            return False
        self._status = "error"
        self._active_future = None
        return True


    def install_ready(self, artifact: InpaintArtifact) -> None:
        self._cancel_active()
        self._committed = artifact.snapshot()
        self._status = "ready"
        self._expected_key = artifact.key

    def ready_artifact(
        self,
        current_key: InpaintKey,
        current_mask: Optional[np.ndarray],
    ) -> Optional[InpaintArtifact]:
        artifact = self._committed
        if (
            self._status != "ready"
            or artifact is None
            or self._expected_key != current_key
            or artifact.key != current_key
            or current_mask is None
            or not np.any(current_mask)
            or artifact.mask.shape != current_mask.shape
            or not np.array_equal(artifact.mask, current_mask)
        ):
            return None
        return artifact

