from __future__ import annotations

import concurrent.futures
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import torch

from editor.commands import MaskEditCommand
from services import get_config_service

from .document_state import normalize_binary_mask
from .image_utils import image_like_to_rgb_array
from .inpaint_state import (
    INPAINT_BBOX_PADDING,
    InpaintArtifact,
    InpaintConfigSnapshot,
    InpaintKey,
    InpaintRequest,
    MaskDelta,
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
        # 画笔命令在写回蒙版时借此压掉常规 delta 修复，避免和随后的强制重修抢同一份工作。
        self._suspend_auto_inpaint = 0
        self._forced_callback: Optional[tuple[InpaintKey, Any]] = None

    @contextlib.contextmanager
    def suspend_auto_inpaint(self):
        """临时压掉 effective_mask_delta_changed 触发的自动修复。"""
        self._suspend_auto_inpaint += 1
        try:
            yield
        finally:
            self._suspend_auto_inpaint -= 1

    @property
    def logger(self):
        return self.controller.logger

    @property
    def model(self):
        return self.controller.model

    @property
    def async_service(self):
        return self.controller.async_service

    @staticmethod
    def normalize_binary_mask(mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        return normalize_binary_mask(mask)

    def _current_mask(self) -> Optional[np.ndarray]:
        return self.normalize_binary_mask(self.model.get_effective_mask())

    def _get_base_image_array(self, key: InpaintKey) -> Optional[np.ndarray]:
        identity = self.model.get_document_identity()
        if identity is None or identity[0] != key.document_id:
            return None
        source_rgb = self.model.get_source_rgb()
        if (
            not isinstance(source_rgb, np.ndarray)
            or source_rgb.ndim != 3
            or source_rgb.shape[2] != 3
            or source_rgb.dtype != np.uint8
        ):
            return None
        return source_rgb

    def _snapshot_inpaint_config(self) -> InpaintConfigSnapshot:
        config = get_config_service().get_config()
        inpainter = config.inpainter
        device = "cuda" if config.cli.use_gpu and torch.cuda.is_available() else "cpu"
        return InpaintConfigSnapshot(
            inpainter=str(inpainter.inpainter),
            inpainting_precision=str(inpainter.inpainting_precision),
            force_use_torch_inpainting=bool(inpainter.force_use_torch_inpainting),
            inpainting_size=int(inpainter.inpainting_size),
            device=device,
        )

    def build_inpaint_request(self, mask, delta: MaskDelta) -> Optional[InpaintRequest]:
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
        if previous is not None and previous.key.document_id != key.document_id:
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
            self._take_forced_callback(result.key)
            self.model.fail_inpaint(result.key)
            return
        if not isinstance(result, InpaintArtifact):
            return
        if not self.model.install_inpaint_artifact(result):
            self._take_forced_callback(result.key)
            self.logger.debug(
                "Ignoring stale inpaint result (key %s, current %s)",
                result.key,
                self.model.get_inpaint_key(),
            )
            return
        callback = self._take_forced_callback(result.key)
        if callback is not None:
            callback(result.image)

    def _take_forced_callback(self, key: InpaintKey):
        """取出并清除该代数号登记的强制重修回调。"""
        pending = self._forced_callback
        if pending is None or pending[0] != key:
            return None
        self._forced_callback = None
        return pending[1]

    def force_inpaint_stroke(self, stroke_mask, *, on_installed=None) -> None:
        """按笔画强制重修，无视该区域此前是否已经修复过。

        优化蒙版是二值累积的：某个像素涂成 255 之后再涂一次不产生任何差异，常规
        delta 路径因此把重复涂抹整条丢弃（见 build_inpaint_request 里减去已修复
        footprint 的那段，以及同蒙版直接复用 artifact 的那段）。这里把笔画本身当作
        added 区域直接下发，绕开这两道判定，让用户能在已修复处继续手动修。
        """
        current_mask = self._current_mask()
        stroke = self.normalize_binary_mask(stroke_mask)
        if (
            current_mask is None
            or stroke is None
            or stroke.shape != current_mask.shape
            or not np.any(current_mask)
        ):
            return
        # 必须与当前蒙版求交：async_inpaint 把 added 直接当修复器蒙版，而 removed 为空时
        # 不会执行“未蒙版像素还原为底图”，否则笔画溢出蒙版的部分会改写本该保持原样的像素。
        forced = np.where((stroke > 0) & (current_mask > 0), 255, 0).astype(np.uint8)
        if not np.any(forced):
            return

        # 推进代数号：蒙版未变时新旧请求的 key 与 mask 完全相同，不推进的话晚到的旧结果
        # 会盖掉本次强制重修的结果。
        key = self.model.bump_inpaint_revision()
        if key is None:
            return
        image = self._get_base_image_array(key)
        if image is None:
            return
        previous = self.model.get_committed_inpaint_artifact()
        if previous is not None and previous.key.document_id != key.document_id:
            previous = None
        request = InpaintRequest(
            key=key,
            image=image,
            mask=current_mask,
            delta=MaskDelta(
                added=forced,
                removed=np.zeros_like(forced),
                mask_revision=key.mask_revision,
            ),
            previous_artifact=previous,
            config=self._snapshot_inpaint_config(),
        )
        future = self.async_service.submit_task(self.async_inpaint(request))
        if not self.model.begin_inpaint(request.key, future):
            return
        self._forced_callback = (
            (request.key, on_installed) if on_installed is not None else None
        )
        future.add_done_callback(
            lambda completed, key=request.key: self._emit_inpaint_result(completed, key)
        )

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
        if self._suspend_auto_inpaint:
            return
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

        # Undo to the retained mask can reuse its immutable artifact directly.
        committed = self.model.get_committed_inpaint_artifact()
        if (
            committed is not None
            and committed.key.document_id == key.document_id
            and committed.mask.shape == current_mask.shape
            and np.array_equal(committed.mask, current_mask)
        ):
            if self.model.install_inpaint_artifact(
                InpaintArtifact(key, current_mask, committed.image)
            ):
                return
        self._start_inpaint_request(current_mask, delta)

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
        rgb = None if result is None else image_like_to_rgb_array(result, copy=True)
        if rgb is not None:
            rgb.setflags(write=False)
        return rgb

    @staticmethod
    async def _full_inpaint(request: InpaintRequest) -> Optional[InpaintArtifact]:
        result = await EditorControllerInpaintService._dispatch_inpaint(
            request, request.image.copy(), request.mask.copy()
        )
        if result is None:
            return None
        return InpaintArtifact(request.key, request.mask, result)

    @staticmethod
    async def async_inpaint(request: InpaintRequest) -> Optional[InpaintArtifact]:
        """执行完整或增量修复；除不可变 request 外不读取实时状态。"""
        current_mask = request.mask
        previous = request.previous_artifact
        if (
            previous is None
            or previous.image.shape != request.image.shape
            or previous.mask.shape != current_mask.shape
            or previous.key.document_id != request.key.document_id
        ):
            return await EditorControllerInpaintService._full_inpaint(request)

        added_areas = request.delta.added
        removed_areas = request.delta.removed
        if not np.any(added_areas) and not np.any(removed_areas):
            return InpaintArtifact(request.key, current_mask, previous.image)

        full_result = previous.image.copy()
        unmasked_pixels = None
        if np.any(removed_areas):
            unmasked_pixels = current_mask == 0
            full_result[unmasked_pixels] = request.image[unmasked_pixels]

        if np.any(added_areas):
            ys, xs = np.where(added_areas > 0)
            padding = INPAINT_BBOX_PADDING
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

        full_result.setflags(write=False)
        return InpaintArtifact(request.key, current_mask, full_result)

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
                image = self.model.get_image()
                if image is None:
                    self.logger.warning("Clear all masks skipped: no active image.")
                    return
                old_mask = np.zeros(
                    (int(image.height), int(image.width)), dtype=np.uint8
                )

            if not np.any(old_mask):
                return

            new_mask = np.zeros_like(old_mask)
            command = MaskEditCommand(
                model=self.model, old_mask=old_mask, new_mask=new_mask
            )
            self.controller.execute_command(command)
        except Exception as e:
            self.logger.error(f"Clear all masks failed: {e}", exc_info=True)
