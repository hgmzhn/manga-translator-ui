from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import numpy as np
import torch

from editor.commands import MaskEditCommand
from services import get_config_service

from .image_utils import image_like_to_rgb_array
from .inpaint_state import (
    InpaintArtifact,
    InpaintConfigSnapshot,
    InpaintKey,
    InpaintRequest,
    InpaintResult,
    MaskDelta,
    MaskRefineRequest,
    MaskRefineResult,
)

if TYPE_CHECKING:
    from .editor_controller import EditorController


@dataclass(frozen=True, slots=True)
class _InpaintFailure:
    key: InpaintKey


class EditorControllerInpaintService:
    """蒙版与 inpaint 流程；可变请求状态只存放在 EditorSession。"""

    def __init__(self, controller: "EditorController"):
        self.controller = controller

    @property
    def logger(self):
        return self.controller.logger

    @property
    def model(self):
        return self.controller.model

    @property
    def async_service(self):
        return self.controller.async_service

    @property
    def resource_manager(self):
        return self.controller.resource_manager

    @staticmethod
    def normalize_binary_mask(mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if mask is None:
            return None
        mask_np = np.asarray(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np[:, :, 0]
        return np.where(mask_np > 0, 255, 0).astype(np.uint8)

    def _current_mask(self) -> Optional[np.ndarray]:
        return self.normalize_binary_mask(self.model.get_effective_mask())


    def _base_rgb_cache_name(self, base_revision: Optional[int] = None) -> str:
        revision = (
            self.model.get_base_revision()
            if base_revision is None
            else int(base_revision)
        )
        return f"{self.controller.WEAK_CACHE_BASE_IMAGE_RGB}:{revision}"

    def _get_base_image_array(self, key: InpaintKey) -> Optional[np.ndarray]:
        """Get source RGB from the current domain projection and identity cache."""
        identity = self.model.get_document_identity()
        display_layers = self.model.get_display_layers()
        if (
            identity is None
            or identity[0] != key.document_id
            or display_layers is None
            or display_layers.identity != identity
            or key.base_revision != self.model.get_base_revision()
        ):
            return None
        cache_name = self._base_rgb_cache_name(key.base_revision)
        cached_array = self.resource_manager.get_weak_cache(identity, cache_name)
        if (
            not isinstance(cached_array, np.ndarray)
            or cached_array.ndim != 3
            or cached_array.shape[2] != 3
            or cached_array.dtype != np.uint8
        ):
            cached_array = image_like_to_rgb_array(
                display_layers.source_image, copy=False
            )
            if cached_array is None:
                return None
            self.resource_manager.set_weak_cache(
                identity, cache_name, cached_array
            )
        return cached_array

    def build_mask_refine_request(self, raw_mask) -> Optional[MaskRefineRequest]:
        raw_mask_snapshot = self.normalize_binary_mask(raw_mask)
        current_raw = self.normalize_binary_mask(self.model.get_raw_mask())
        identity = self.model.get_document_identity()
        key = self.model.get_inpaint_key()
        if (
            raw_mask_snapshot is None
            or current_raw is None
            or identity is None
            or identity[0] != key.document_id
            or raw_mask_snapshot.shape != current_raw.shape
            or not np.array_equal(raw_mask_snapshot, current_raw)
        ):
            return None
        return MaskRefineRequest(
            document_id=identity[0],
            base_revision=key.base_revision,
            mask_revision=key.mask_revision,
            raw_mask=raw_mask_snapshot,
        )

    def _snapshot_inpaint_config(self) -> InpaintConfigSnapshot:
        config = get_config_service().get_config()
        inpainter = config.inpainter
        device = "cuda" if config.cli.use_gpu and torch.cuda.is_available() else "cpu"
        return InpaintConfigSnapshot(
            inpainter=str(inpainter.inpainter),
            inpainting_precision=str(inpainter.inpainting_precision),
            force_use_torch_inpainting=bool(
                inpainter.force_use_torch_inpainting
            ),
            inpainting_size=int(inpainter.inpainting_size),
            device=device,
        )

    def build_inpaint_request(
        self, mask, delta: MaskDelta
    ) -> Optional[InpaintRequest]:
        key = self.model.get_inpaint_key()
        supplied_mask = self.normalize_binary_mask(mask)
        current_mask = self._current_mask()
        if (
            supplied_mask is None
            or current_mask is None
            or supplied_mask.shape != current_mask.shape
            or not np.array_equal(supplied_mask, current_mask)
            or not np.any(current_mask)
            or not isinstance(delta, MaskDelta)
            or delta.mask_revision != key.mask_revision
            or delta.added.shape != current_mask.shape
            or delta.removed.shape != current_mask.shape
            or (not np.any(delta.added) and not np.any(delta.removed))
        ):
            return None

        image = self._get_base_image_array(key)
        if image is None or key != self.model.get_inpaint_key():
            return None
        previous = self.model.get_committed_inpaint_artifact()
        if previous is not None and (
            previous.key.document_id != key.document_id
            or previous.key.base_revision != key.base_revision
        ):
            previous = None
        effective_delta = delta
        if previous is not None and previous.mask.shape == current_mask.shape:
            effective_delta = MaskDelta(
                added=np.where(
                    (current_mask > 0) & (previous.mask == 0), 255, 0
                ).astype(np.uint8),
                removed=np.where(
                    (previous.mask > 0) & (current_mask == 0), 255, 0
                ).astype(np.uint8),
                mask_revision=key.mask_revision,
            )
            if not np.any(effective_delta.added) and not np.any(
                effective_delta.removed
            ):
                return None
        return InpaintRequest(
            key=key,
            image=image,
            mask=current_mask,
            delta=effective_delta,
            previous_artifact=previous,
            config=self._snapshot_inpaint_config(),
        )

    def apply_inpaint_result(self, result: object) -> None:
        if isinstance(result, _InpaintFailure):
            self.model.fail_inpaint(result.key)
            return
        if not isinstance(result, InpaintResult):
            return

        current_key = self.model.get_inpaint_key()
        current_mask = self._current_mask()
        if (
            result.key != current_key
            or current_mask is None
            or result.mask.shape != current_mask.shape
            or not np.array_equal(result.mask, current_mask)
        ):
            self.logger.debug(
                "Ignoring stale inpaint result (key %s, current %s)",
                result.key,
                current_key,
            )
            return

        mask_2d = self.normalize_binary_mask(result.mask)
        image_np = image_like_to_rgb_array(result.image, copy=True)
        if mask_2d is None or image_np is None:
            self.model.fail_inpaint(result.key)
            return
        artifact = InpaintArtifact(result.key, mask_2d, image_np)
        if not self.model.install_inpaint_artifact(artifact):
            self.logger.debug(
                "Ignoring inpaint result rejected during atomic installation "
                "(key %s)",
                result.key,
            )

    def submit_mask_refine_request(self, request: MaskRefineRequest) -> None:
        future = self.async_service.submit_task(self.async_refine_mask(request))
        if future is not None:
            future.add_done_callback(self._emit_mask_refine_result)

    def _start_inpaint_request(self, mask, delta: MaskDelta) -> None:
        request = self.build_inpaint_request(mask, delta)
        if request is None:
            return
        future = self.async_service.submit_task(self.async_inpaint(request))
        if not self.model.begin_inpaint(request.key, future):
            return
        future.add_done_callback(
            lambda completed, key=request.key: self._emit_inpaint_result(completed, key)
        )

    def _emit_mask_refine_result(self, future) -> None:
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            return
        except Exception as e:
            self.logger.error(f"Mask refinement failed: {e}", exc_info=True)
            return
        if result is not None:
            self.controller._refined_mask_ready.emit(result)

    def _emit_inpaint_result(self, future, key: InpaintKey) -> None:
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            return
        except Exception as e:
            self.logger.error(f"Inpainting failed: {e}", exc_info=True)
            self.controller._inpaint_result_ready.emit(_InpaintFailure(key))
            return
        if result is None:
            self.controller._inpaint_result_ready.emit(_InpaintFailure(key))
        else:
            self.controller._inpaint_result_ready.emit(result)

    def ensure_current_mask_inpaint(self) -> None:
        """Start one full repair for a loaded non-empty mask without an artifact."""
        if self.model.get_ready_inpaint_artifact() is not None:
            return
        current_mask = self._current_mask()
        key = self.model.get_inpaint_key()
        if current_mask is None or not np.any(current_mask):
            return
        delta = MaskDelta(
            added=current_mask,
            removed=np.zeros_like(current_mask),
            mask_revision=key.mask_revision,
        )
        self.on_effective_mask_delta_changed(current_mask, delta)

    def on_effective_mask_delta_changed(self, mask, delta: MaskDelta) -> None:
        supplied_mask = self.normalize_binary_mask(mask)
        current_mask = self._current_mask()
        key = self.model.get_inpaint_key()
        if (
            not isinstance(delta, MaskDelta)
            or delta.mask_revision != key.mask_revision
            or (supplied_mask is None) != (current_mask is None)
            or (
                supplied_mask is not None
                and current_mask is not None
                and (
                    supplied_mask.shape != current_mask.shape
                    or not np.array_equal(supplied_mask, current_mask)
                )
            )
        ):
            return
        if current_mask is None or not np.any(current_mask):
            return

        # Undo to an already computed mask only rekeys the retained artifact.
        committed = self.model.get_committed_inpaint_artifact()
        if (
            committed is not None
            and committed.key.document_id == key.document_id
            and committed.key.base_revision == key.base_revision
            and committed.mask.shape == current_mask.shape
            and np.array_equal(committed.mask, current_mask)
        ):
            artifact = InpaintArtifact(key, current_mask, committed.image)
            if self.model.install_inpaint_artifact(artifact):
                return
        self._start_inpaint_request(current_mask, delta)

    @staticmethod
    async def async_refine_mask(
        request: MaskRefineRequest,
    ) -> Optional[MaskRefineResult]:
        refined_mask = EditorControllerInpaintService.normalize_binary_mask(
            request.raw_mask
        )
        if refined_mask is None or refined_mask.size == 0:
            return None
        return MaskRefineResult(
            request.document_id,
            request.base_revision,
            request.mask_revision,
            refined_mask,
        )

    def apply_refined_mask_result(self, result: object) -> None:
        if not isinstance(result, MaskRefineResult):
            return
        identity = self.model.get_document_identity()
        current_raw = self.normalize_binary_mask(self.model.get_raw_mask())
        refined_mask = self.normalize_binary_mask(result.refined_mask)
        if (
            identity is None
            or result.document_id != identity[0]
            or result.base_revision != self.model.get_base_revision()
            or result.mask_revision != self.model.get_mask_revision()
            or current_raw is None
            or refined_mask is None
            or refined_mask.shape != current_raw.shape
            or not np.array_equal(refined_mask, current_raw)
        ):
            self.logger.debug("Ignoring stale refined mask result")
            return
        self.model.set_refined_mask(refined_mask)

    @staticmethod
    async def _dispatch_inpaint(
        request: InpaintRequest, image: np.ndarray, mask: np.ndarray
    ) -> Optional[np.ndarray]:
        from manga_translator.config import Inpainter, InpainterConfig, InpaintPrecision
        from manga_translator.inpainting import dispatch as inpaint_dispatch

        inpainter_config = InpainterConfig()
        inpainter_config.inpainting_precision = InpaintPrecision(
            request.config.inpainting_precision
        )
        inpainter_config.force_use_torch_inpainting = (
            request.config.force_use_torch_inpainting
        )
        try:
            inpainter_key = Inpainter(request.config.inpainter)
        except ValueError:
            inpainter_key = Inpainter.lama_large
        result = await inpaint_dispatch(
            inpainter_key=inpainter_key,
            image=image,
            mask=mask,
            config=inpainter_config,
            inpainting_size=request.config.inpainting_size,
            device=request.config.device,
        )
        return None if result is None else image_like_to_rgb_array(result, copy=True)

    @staticmethod
    async def _full_inpaint(request: InpaintRequest) -> Optional[InpaintResult]:
        result = await EditorControllerInpaintService._dispatch_inpaint(
            request, request.image.copy(), request.mask.copy()
        )
        if result is None:
            return None
        return InpaintResult(request.key, request.mask, result)

    @staticmethod
    async def async_inpaint(request: InpaintRequest) -> Optional[InpaintResult]:
        """执行完整或增量修复；除不可变 request 外不读取实时状态。"""
        current_mask = request.mask
        previous = request.previous_artifact
        if (
            previous is None
            or previous.image.shape != request.image.shape
            or previous.mask.shape != current_mask.shape
            or previous.key.document_id != request.key.document_id
            or previous.key.base_revision != request.key.base_revision
        ):
            return await EditorControllerInpaintService._full_inpaint(request)

        added_areas = request.delta.added
        removed_areas = request.delta.removed
        if not np.any(added_areas) and not np.any(removed_areas):
            return InpaintResult(request.key, current_mask, previous.image)

        full_result = previous.image.copy()
        unmasked_pixels = None
        if np.any(removed_areas):
            unmasked_pixels = current_mask == 0
            full_result[unmasked_pixels] = request.image[unmasked_pixels]

        if np.any(added_areas):
            ys, xs = np.where(added_areas > 0)
            padding = 50
            height, width = current_mask.shape
            y_min = max(0, int(np.min(ys)) - padding)
            y_max = min(height, int(np.max(ys)) + padding + 1)
            x_min = max(0, int(np.min(xs)) - padding)
            x_max = min(width, int(np.max(xs)) + padding + 1)
            bbox_result = await EditorControllerInpaintService._dispatch_inpaint(
                request,
                full_result[y_min:y_max, x_min:x_max].copy(),
                added_areas[y_min:y_max, x_min:x_max].copy(),
            )
            if bbox_result is None:
                return None
            full_result[y_min:y_max, x_min:x_max] = bbox_result

        if unmasked_pixels is not None:
            full_result[unmasked_pixels] = request.image[unmasked_pixels]

        return InpaintResult(request.key, current_mask, full_result)

    def set_display_mask_type(self, mask_type: str, visible: bool) -> None:
        self.model.set_display_mask_type(mask_type if visible else "none")

    def set_active_tool(self, tool: str) -> None:
        self.model.set_active_tool(tool)

    def set_brush_size(self, size: int) -> None:
        self.model.set_brush_size(size)

    def set_brush_color(self, color: str) -> None:
        self.model.set_brush_color(color)

    def clear_paint_overlay(self) -> None:
        self._clear_overlay_layer("paint")

    def clear_stamp_overlay(self) -> None:
        self._clear_overlay_layer("stamp")

    def _clear_overlay_layer(self, layer: str) -> None:
        try:
            from editor.commands import PaintOverlayEditCommand

            is_stamp = layer == "stamp"
            old = (
                self.model.get_stamp_overlay_image()
                if is_stamp
                else self.model.get_paint_overlay_image()
            )
            if old is None:
                return
            import numpy as np

            def _set(image):
                if is_stamp:
                    self.model.set_stamp_overlay_image(image)
                else:
                    self.model.set_paint_overlay_image(image)

            old_arr = np.asarray(old)
            if old_arr.size == 0:
                _set(None)
                return
            if old_arr.ndim == 3 and old_arr.shape[2] == 4:
                has_content = bool(np.any(old_arr[..., 3]))
            else:
                has_content = bool(np.any(old_arr))
            if not has_content:
                _set(None)
                return
            command = PaintOverlayEditCommand(
                model=self.model,
                old_overlay=old_arr.copy(),
                new_overlay=None,
                layer=layer,
            )
            self.controller.execute_command(command)
        except Exception as e:
            self.logger.error(f"Clear {layer} overlay failed: {e}", exc_info=True)

    def clear_all_masks(self) -> None:
        try:
            source_mask = self.model.get_effective_mask()

            old_mask = None
            if source_mask is not None:
                old_mask = np.array(source_mask)
                if old_mask.ndim == 3:
                    old_mask = old_mask[:, :, 0]
                old_mask = np.where(old_mask > 0, 255, 0).astype(np.uint8)

            if old_mask is None:
                image = self.controller._get_current_image()
                if image is None:
                    self.logger.warning("Clear all masks skipped: no active image.")
                    return
                old_mask = np.zeros((int(image.height), int(image.width)), dtype=np.uint8)

            if not np.any(old_mask):
                return

            new_mask = np.zeros_like(old_mask)
            command = MaskEditCommand(model=self.model, old_mask=old_mask, new_mask=new_mask)
            self.controller.execute_command(command)
        except Exception as e:
            self.logger.error(f"Clear all masks failed: {e}", exc_info=True)
