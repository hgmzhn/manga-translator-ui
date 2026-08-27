from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


def _owned_array(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value)
    if array.flags.writeable or not array.flags.owndata:
        array = np.array(array, copy=True)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class InpaintKey:
    document_id: int
    mask_revision: int


@dataclass(frozen=True, slots=True)
class MaskDelta:
    added: np.ndarray
    removed: np.ndarray
    mask_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "added", _owned_array(self.added))
        object.__setattr__(self, "removed", _owned_array(self.removed))


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


@dataclass(slots=True)
class InpaintState:
    """Own the active job and last immutable artifact for one document."""

    _active_future: Any = None
    _active_key: Optional[InpaintKey] = None
    _committed: Optional[InpaintArtifact] = None

    @property
    def committed(self) -> Optional[InpaintArtifact]:
        return self._committed

    def active_future(self, current_key: InpaintKey) -> Any:
        if self._active_key != current_key:
            return None
        return self._active_future

    def _cancel_active(self) -> None:
        future = self._active_future
        self._active_future = None
        self._active_key = None
        if future is not None and not future.done():
            future.cancel()

    def invalidate(self, *, clear_committed: bool) -> None:
        self._cancel_active()
        if clear_committed:
            self._committed = None

    def begin(self, key: InpaintKey, future: Any) -> bool:
        self._cancel_active()
        if future is None:
            return False
        self._active_key = key
        self._active_future = future
        return True

    def fail(self, key: InpaintKey, current_key: InpaintKey) -> bool:
        if key != self._active_key or key != current_key:
            return False
        self._active_key = None
        self._active_future = None
        return True

    def install_ready(self, artifact: InpaintArtifact) -> None:
        self._cancel_active()
        self._committed = artifact

    def ready_artifact(
        self,
        current_key: InpaintKey,
        current_mask: Optional[np.ndarray],
    ) -> Optional[InpaintArtifact]:
        artifact = self._committed
        if (
            artifact is None
            or artifact.key != current_key
            or current_mask is None
            or not np.any(current_mask)
            or artifact.mask.shape != current_mask.shape
            or not np.array_equal(artifact.mask, current_mask)
        ):
            return None
        return artifact
