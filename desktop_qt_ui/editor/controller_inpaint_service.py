from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np
import torch
from editor.commands import MaskEditCommand
from services import get_config_service

from .image_utils import image_like_to_rgb_array

if TYPE_CHECKING:
    from .editor_controller import EditorController


class EditorControllerInpaintService:
    """蒙版与 inpaint 流程。"""

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
        mask_np = np.array(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np[:, :, 0]
        return np.where(mask_np > 0, 255, 0).astype(np.uint8)

    def get_cached_mask_snapshot(self) -> Optional[np.ndarray]:
        cached_mask = self.resource_manager.get_cache(self.controller.CACHE_LAST_MASK)
        normalized = self.normalize_binary_mask(cached_mask)
        return None if normalized is None else normalized.copy()

    def get_cached_inpainted_snapshot(self) -> Optional[np.ndarray]:
        cached_image = self.resource_manager.get_cache(self.controller.CACHE_LAST_INPAINTED)
        return image_like_to_rgb_array(cached_image, copy=True)

    def clear_document_cache(self) -> None:
        self.resource_manager.clear_cache(self.controller.CACHE_LAST_INPAINTED)
        self.resource_manager.clear_cache(self.controller.CACHE_LAST_MASK)
        self.resource_manager.clear_weak_cache(self.controller.WEAK_CACHE_BASE_IMAGE_RGB)

    def get_base_image_rgb_array(self) -> Optional[np.ndarray]:
        image = self.controller._get_current_image()
        if image is None:
            return None

        expected_shape = (int(image.height), int(image.width), 3)
        cached_array = self.resource_manager.get_weak_cache(self.controller.WEAK_CACHE_BASE_IMAGE_RGB)
        if isinstance(cached_array, np.ndarray) and cached_array.shape == expected_shape and cached_array.dtype == np.uint8:
            return cached_array

        image_array = image_like_to_rgb_array(image, copy=False)
        if image_array is None:
            return None
        self.resource_manager.set_weak_cache(self.controller.WEAK_CACHE_BASE_IMAGE_RGB, image_array)
        return image_array

    def cancel_active_inpaint_task(self) -> None:
        future = self.controller._active_inpaint_future
        self.controller._active_inpaint_future = None
        if future is not None and not future.done():
            future.cancel()

    def invalidate_inpaint_requests(self) -> None:
        self.cancel_active_inpaint_task()
        self.controller._inpaint_request_generation += 1

    def begin_inpaint_request(self) -> int:
        self.invalidate_inpaint_requests()
        return self.controller._inpaint_request_generation

    def is_inpaint_request_current(self, generation: int) -> bool:
        return generation == self.controller._inpaint_request_generation

    def on_refined_mask_changed(self, mask) -> None:
        if self.controller._suppress_refined_mask_autoinpaint:
            return

        image = self.controller._get_current_image()
        if image is None or mask is None:
            self.invalidate_inpaint_requests()
            return

        cached_mask = self.get_cached_mask_snapshot()
        generation = self.begin_inpaint_request()
        if cached_mask is not None:
            future = self.async_service.submit_task(self.async_incremental_inpaint(mask, generation))
        else:
            future = self.async_service.submit_task(self.async_full_inpaint_with_cache(mask, generation))
        self.controller._active_inpaint_future = future

    async def async_refine_and_inpaint(self):
        try:
            raw_mask = self.model.get_raw_mask()
            regions = self.controller._get_regions()

            if raw_mask is None or not regions:
                self.logger.warning("Refinement/Inpainting skipped: image, mask, or regions not available.")
                return

            refined_mask = self.normalize_binary_mask(raw_mask)
            if refined_mask is None:
                self.logger.error("Mask refinement failed.")
                return
            if not isinstance(refined_mask, np.ndarray):
                self.logger.error(f"Refined mask is not a numpy array: {type(refined_mask)}")
                return
            if refined_mask.size == 0:
                self.logger.error("Refined mask is empty")
                return

            current_inpainted_image = self.model.get_inpainted_image()
            if current_inpainted_image is not None:
                inpainted_image_np = image_like_to_rgb_array(current_inpainted_image, copy=False)
                if inpainted_image_np is None:
                    self.logger.warning("Current inpainted image could not be normalized to RGB array.")
                    return
                self.resource_manager.set_cache(self.controller.CACHE_LAST_INPAINTED, inpainted_image_np)
                self.resource_manager.set_cache(self.controller.CACHE_LAST_MASK, refined_mask.copy())
                if not self.controller._user_adjusted_alpha:
                    self.model.set_original_image_alpha(0.0)
            else:
                self.resource_manager.clear_cache(self.controller.CACHE_LAST_INPAINTED)
                self.resource_manager.clear_cache(self.controller.CACHE_LAST_MASK)

            self.controller._suppress_refined_mask_autoinpaint = True
            try:
                self.model.set_refined_mask(refined_mask)
            finally:
                self.controller._suppress_refined_mask_autoinpaint = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error during async refine and inpaint: {e}")

    def force_inpaint_stroke(self, stroke_mask: np.ndarray) -> None:
        if self.controller._suppress_refined_mask_autoinpaint:
            return

        current_mask = self.model.get_refined_mask()
        if current_mask is None:
            return

        generation = self.begin_inpaint_request()
        future = self.async_service.submit_task(self.async_incremental_inpaint(current_mask, generation, stroke_mask=stroke_mask))
        self.controller._active_inpaint_future = future

    async def async_incremental_inpaint(self, current_mask, generation: int, stroke_mask: Optional[np.ndarray] = None):
        try:
            if not self.is_inpaint_request_current(generation):
                return

            image = self.controller._get_current_image()
            if image is None or current_mask is None:
                self.logger.warning("Incremental inpainting skipped: missing data.")
                return

            current_mask_2d = self.normalize_binary_mask(current_mask)
            if current_mask_2d is None:
                return

            if stroke_mask is not None:
                stroke_mask_2d = self.normalize_binary_mask(stroke_mask)
                if stroke_mask_2d is None:
                    return
                added_areas = stroke_mask_2d
                removed_areas = np.zeros_like(stroke_mask_2d)
            else:
                last_processed_mask = self.get_cached_mask_snapshot()
                if last_processed_mask is None:
                    await self.async_full_inpaint_with_cache(current_mask, generation)
                    return
    
                if current_mask_2d.shape != last_processed_mask.shape:
                    self.logger.warning(
                        "Incremental inpainting fell back to full: mask shape changed from %s to %s",
                        last_processed_mask.shape,
                        current_mask_2d.shape,
                    )
                    await self.async_full_inpaint_with_cache(current_mask_2d, generation)
                    return
    
                added_areas = cv2.bitwise_and(current_mask_2d, cv2.bitwise_not(last_processed_mask))
                removed_areas = cv2.bitwise_and(last_processed_mask, cv2.bitwise_not(current_mask_2d))

            if not np.any(added_areas) and not np.any(removed_areas):
                return

            full_result = self.get_cached_inpainted_snapshot()
            base_image_np = None
            expected_shape = (int(image.height), int(image.width), 3)
            if full_result is None or full_result.shape != expected_shape:
                base_image_np = self.get_base_image_rgb_array()
                if base_image_np is None:
                    self.logger.warning("Incremental inpainting skipped: failed to normalize base image.")
                    return
                full_result = base_image_np.copy()

            if np.any(removed_areas):
                if base_image_np is None:
                    base_image_np = self.get_base_image_rgb_array()
                    if base_image_np is None:
                        self.logger.warning("Incremental inpainting restore skipped: failed to normalize base image.")
                        return
                removed_pixels = removed_areas > 0
                full_result[removed_pixels] = base_image_np[removed_pixels]

            if np.any(added_areas):
                coords = np.where(added_areas > 0)
                if len(coords[0]) == 0:
                    return

                y_min, y_max = np.min(coords[0]), np.max(coords[0])
                x_min, x_max = np.min(coords[1]), np.max(coords[1])

                padding = 50
                h, w = current_mask_2d.shape
                y_min = max(0, y_min - padding)
                y_max = min(h, y_max + padding + 1)
                x_min = max(0, x_min - padding)
                x_max = min(w, x_max + padding + 1)

                bbox_image = full_result[y_min:y_max, x_min:x_max].copy()
                bbox_mask = added_areas[y_min:y_max, x_min:x_max].copy()

                config = get_config_service().get_config()
                inpainter_config_model = config.inpainter
                try:
                    from manga_translator.config import Inpainter, InpainterConfig, InpaintPrecision
                    from manga_translator.inpainting import dispatch as inpaint_dispatch
                except ImportError as e:
                    self.logger.error(f"Failed to import backend modules: {e}")
                    return

                inpainter_config = InpainterConfig()
                inpainter_config.inpainting_precision = InpaintPrecision(inpainter_config_model.inpainting_precision)
                inpainter_config.force_use_torch_inpainting = inpainter_config_model.force_use_torch_inpainting

                try:
                    inpainter_key = Inpainter(inpainter_config_model.inpainter)
                except ValueError:
                    inpainter_key = Inpainter.lama_large

                device = "cuda" if config.cli.use_gpu and torch.cuda.is_available() else "cpu"
                bbox_result = await inpaint_dispatch(
                    inpainter_key=inpainter_key,
                    image=bbox_image,
                    mask=bbox_mask,
                    config=inpainter_config,
                    inpainting_size=inpainter_config_model.inpainting_size,
                    device=device,
                )
                if bbox_result is None:
                    self.logger.error("Incremental inpainting failed, returned None.")
                    return
                if not self.is_inpaint_request_current(generation):
                    return
                full_result[y_min:y_max, x_min:x_max] = bbox_result

            if not self.is_inpaint_request_current(generation):
                return

            self.resource_manager.set_cache(self.controller.CACHE_LAST_INPAINTED, full_result)
            self.resource_manager.set_cache(self.controller.CACHE_LAST_MASK, current_mask_2d)
            self.model.set_inpainted_image(full_result)
            if not self.controller._user_adjusted_alpha:
                self.model.set_original_image_alpha(0.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error during bounding box inpainting: {e}", exc_info=True)

    async def async_full_inpaint_with_cache(self, mask, generation: int):
        try:
            if not self.is_inpaint_request_current(generation):
                return

            image_np = self.get_base_image_rgb_array()
            if image_np is None or mask is None:
                self.logger.warning("Full inpainting skipped: failed to normalize base image.")
                return

            try:
                from manga_translator.config import Inpainter, InpainterConfig, InpaintPrecision
                from manga_translator.inpainting import dispatch as inpaint_dispatch
            except ImportError as e:
                self.logger.error(f"Failed to import backend modules: {e}")
                return

            mask_2d = self.normalize_binary_mask(mask)
            if mask_2d is None:
                return

            config = get_config_service().get_config()
            inpainter_config_model = config.inpainter

            inpainter_config = InpainterConfig()
            inpainter_config.inpainting_precision = InpaintPrecision(inpainter_config_model.inpainting_precision)
            inpainter_config.force_use_torch_inpainting = inpainter_config_model.force_use_torch_inpainting

            try:
                inpainter_key = Inpainter(inpainter_config_model.inpainter)
            except ValueError:
                self.logger.warning(f"Unknown inpainter model: {inpainter_config_model.inpainter}, defaulting to lama_large")
                inpainter_key = Inpainter.lama_large

            device = "cuda" if config.cli.use_gpu and torch.cuda.is_available() else "cpu"
            inpainted_image_np = await inpaint_dispatch(
                inpainter_key=inpainter_key,
                image=image_np,
                mask=mask_2d,
                config=inpainter_config,
                inpainting_size=inpainter_config_model.inpainting_size,
                device=device,
            )

            if inpainted_image_np is not None and self.is_inpaint_request_current(generation):
                self.resource_manager.set_cache(self.controller.CACHE_LAST_INPAINTED, inpainted_image_np)
                self.resource_manager.set_cache(self.controller.CACHE_LAST_MASK, mask_2d)
                self.model.set_inpainted_image(inpainted_image_np)
                if not self.controller._user_adjusted_alpha:
                    self.model.set_original_image_alpha(0.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error during full inpainting with cache: {e}", exc_info=True)

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
            source_mask = self.model.get_refined_mask()
            if source_mask is None:
                source_mask = self.model.get_raw_mask()

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
