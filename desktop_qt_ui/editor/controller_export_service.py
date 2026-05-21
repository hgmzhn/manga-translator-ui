from __future__ import annotations

import asyncio
import copy
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox
from services import get_render_parameter_service

from manga_translator.utils.path_manager import (
    find_inpainted_path,
    find_json_path,
    get_inpainted_path,
    get_json_path,
    get_paint_overlay_path,
)

from .image_utils import image_like_to_pil, image_like_to_rgb_array

if TYPE_CHECKING:
    from .editor_controller import EditorController


class EditorControllerExportService:
    """导出与导出前持久化流程。"""

    def __init__(self, controller: "EditorController"):
        self.controller = controller

    @property
    def model(self):
        return self.controller.model

    @property
    def logger(self):
        return self.controller.logger

    @property
    def config_service(self):
        return self.controller.config_service

    @property
    def resource_manager(self):
        return self.controller.resource_manager

    @property
    def async_service(self):
        return self.controller.async_service

    def generate_export_snapshot(self) -> dict:
        regions = self.controller._get_regions()
        snapshot_data = []
        for region in regions:
            region_key = {
                "translation": region.get("translation", ""),
                "font_size": region.get("font_size"),
                "font_color": region.get("font_color"),
                "alignment": region.get("alignment"),
                "direction": region.get("direction"),
                "xyxy": region.get("xyxy"),
                "lines": str(region.get("lines", [])),
            }
            snapshot_data.append(str(region_key))

        mask = self.model.get_refined_mask()
        if mask is None:
            mask = self.model.get_raw_mask()
        mask_signature = ""
        if mask is not None:
            mask_signature = f"{mask.shape}_{mask.sum()}_{np.count_nonzero(mask)}"

        overlay = self.model.get_paint_overlay_image()
        overlay_signature = ""
        if overlay is not None:
            overlay_arr = np.asarray(overlay)
            overlay_signature = (
                f"{overlay_arr.shape}_{int(overlay_arr.sum())}_{int(np.count_nonzero(overlay_arr))}"
            )

        return {
            "regions_hash": hash("|".join(snapshot_data)),
            "mask_signature": mask_signature,
            "overlay_signature": overlay_signature,
            "source_path": self.model.get_source_image_path(),
        }

    def has_changes_since_last_export(self) -> bool:
        if self.controller._last_export_snapshot is None:
            return self.controller.history_service.can_undo()

        current_snapshot = self.generate_export_snapshot()
        if current_snapshot["source_path"] != self.controller._last_export_snapshot["source_path"]:
            return self.controller.history_service.can_undo()

        return (
            current_snapshot["regions_hash"] != self.controller._last_export_snapshot["regions_hash"]
            or current_snapshot["mask_signature"] != self.controller._last_export_snapshot["mask_signature"]
            or current_snapshot.get("overlay_signature", "")
            != self.controller._last_export_snapshot.get("overlay_signature", "")
        )

    def save_export_snapshot(self) -> None:
        self.controller._last_export_snapshot = self.generate_export_snapshot()
        self.logger.debug(f"Export snapshot saved: {self.controller._last_export_snapshot}")

    def export_image(self):
        try:
            image = self.controller._get_current_image()
            regions = self.controller._get_regions()
            source_path = self.model.get_source_image_path()

            if image is None:
                self.logger.warning("Cannot export: missing image data")
                toast_manager = self.controller.get_toast_manager()
                if toast_manager is not None:
                    toast_manager.show_error("导出失败：缺少图像数据")
                return

            if regions is None:
                regions = []

            mask = self.model.get_refined_mask()
            if mask is None:
                mask = self.model.get_raw_mask()
            if mask is None and regions:
                self.logger.warning("Cannot export: no mask data available for regions")
                toast_manager = self.controller.get_toast_manager()
                if toast_manager is not None:
                    toast_manager.show_error("导出失败：没有可用的蒙版数据")
                return None

            self.controller._export_toast = None
            toast_manager = self.controller.get_toast_manager()
            if toast_manager is not None:
                self.controller._export_toast = toast_manager.show_info("正在导出...", duration=0)

            image_snapshot = self.controller._snapshot_image_for_export(image, "base image")
            inpainted_snapshot = self.controller._snapshot_image_for_export(
                self.model.get_inpainted_image(),
                "inpainted image",
            )
            regions_snapshot = copy.deepcopy(regions)
            mask_snapshot = None if mask is None else np.array(mask, copy=True)

            paint_overlay = self.model.get_paint_overlay_image()
            overlay_snapshot = None
            if paint_overlay is not None:
                overlay_arr = np.asarray(paint_overlay)
                if overlay_arr.ndim == 3 and overlay_arr.shape[2] == 4 and np.any(overlay_arr[..., 3]):
                    overlay_snapshot = overlay_arr.copy()

            return self.async_service.submit_task(
                self.async_export_with_desktop_ui_service(
                    image_snapshot,
                    regions_snapshot,
                    mask_snapshot,
                    source_path,
                    inpainted_snapshot,
                    overlay_snapshot,
                )
            )
        except Exception as e:
            self.logger.error(f"Error during export request: {e}", exc_info=True)
            toast_manager = self.controller.get_toast_manager()
            if toast_manager is not None:
                toast_manager.show_error("导出失败")
            return None

    @staticmethod
    def resolve_effective_box_local(region: dict):
        if not isinstance(region, dict):
            return None

        custom_box = region.get("white_frame_rect_local")
        render_box = region.get("render_box_rect_local")
        has_custom = bool(region.get("has_custom_white_frame", False))

        if isinstance(render_box, (list, tuple)) and len(render_box) == 4:
            return render_box
        if isinstance(custom_box, (list, tuple)) and len(custom_box) == 4 and has_custom:
            return custom_box
        if isinstance(custom_box, (list, tuple)) and len(custom_box) == 4:
            return custom_box
        return None

    @classmethod
    def apply_white_frame_center(cls, region: dict) -> None:
        """将 center 重算为白框世界中心，并同步平移 local 坐标以免漂移。"""
        wf_local = cls.resolve_effective_box_local(region)
        base_center = region.get("center")
        if not (
            isinstance(wf_local, (list, tuple))
            and len(wf_local) == 4
            and isinstance(base_center, (list, tuple))
            and len(base_center) >= 2
        ):
            return
        try:
            left, top, right, bottom = (float(v) for v in wf_local)
            lx = (left + right) / 2.0
            ly = (top + bottom) / 2.0
            if abs(lx) < 0.001 and abs(ly) < 0.001:
                return
            cx, cy = float(base_center[0]), float(base_center[1])
            angle = float(region.get("angle") or 0.0)
            rad = math.radians(angle)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            region["center"] = [cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a]
            # 同步平移 local 坐标，以新 center 为原点，防止存/读漂移
            if "white_frame_rect_local" in region:
                wf = region["white_frame_rect_local"]
                if isinstance(wf, (list, tuple)) and len(wf) == 4:
                    region["white_frame_rect_local"] = [
                        float(wf[0]) - lx, float(wf[1]) - ly,
                        float(wf[2]) - lx, float(wf[3]) - ly,
                    ]
            if "render_box_rect_local" in region:
                rb = region["render_box_rect_local"]
                if isinstance(rb, (list, tuple)) and len(rb) == 4:
                    region["render_box_rect_local"] = [
                        float(rb[0]) - lx, float(rb[1]) - ly,
                        float(rb[2]) - lx, float(rb[3]) - ly,
                    ]
        except (TypeError, ValueError):
            return

    def resolve_editor_json_path(self, source_path: str) -> str:
        json_path = find_json_path(source_path)
        if not json_path:
            json_path = get_json_path(source_path, create_dir=True)
            self.logger.info(f"No existing JSON found, will create new one at: {json_path}")
        else:
            self.logger.info(f"Found existing JSON, will replace: {json_path}")
        return json_path

    def save_current_inpainted_image(
        self,
        source_path: str,
        config_dict: dict,
        mask: Optional[np.ndarray],
        current_inpainted_image: Optional[object] = None,
        has_regions: bool = False,
    ) -> None:
        try:
            image_to_save = current_inpainted_image
            if image_to_save is None:
                image_to_save = self.model.get_inpainted_image()
            if image_to_save is None:
                if mask is not None or has_regions:
                    existing_inpainted_path = find_inpainted_path(source_path)
                    if existing_inpainted_path and os.path.exists(existing_inpainted_path):
                        self.logger.info(
                            "No live inpainted preview during export, keep existing inpainted image: %s",
                            existing_inpainted_path,
                        )
                    else:
                        self.logger.warning(
                            "Skipped updating inpainted image during export because no inpainted preview is available yet: %s",
                            source_path,
                        )
                    return
                image_to_save = self.model.get_image()
            if image_to_save is None:
                return

            inpainted_path = get_inpainted_path(source_path, create_dir=True)
            save_quality = config_dict.get("cli", {}).get("save_quality", 95)

            save_image = image_like_to_pil(image_to_save)
            if save_image is None:
                return
            try:
                save_kwargs = {}
                if inpainted_path.lower().endswith((".jpg", ".jpeg")):
                    if save_image.mode in ("RGBA", "LA"):
                        converted_image = save_image.convert("RGB")
                        save_image.close()
                        save_image = converted_image
                    save_kwargs["quality"] = save_quality
                elif inpainted_path.lower().endswith(".webp"):
                    save_kwargs["quality"] = save_quality

                save_image.save(inpainted_path, **save_kwargs)

                if self.controller._is_same_source_image(self.model.get_source_image_path(), source_path):
                    self.model.set_inpainted_image_path(inpainted_path)
                    self.resource_manager.set_cache(
                        self.controller.CACHE_LAST_INPAINTED,
                        image_like_to_rgb_array(save_image, copy=True),
                    )
                    if mask is not None:
                        mask_to_cache = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY) if len(mask.shape) == 3 else mask
                        self.resource_manager.set_cache(
                            self.controller.CACHE_LAST_MASK,
                            np.array(mask_to_cache, copy=True),
                        )
                else:
                    self.logger.debug(
                        "Skipped runtime inpaint cache update because active image changed during export"
                    )

                self.logger.info(f"已更新修复图片: {inpainted_path}")
            finally:
                try:
                    save_image.close()
                except Exception:
                    pass
        except Exception as e:
            self.logger.warning(f"更新inpainted图片失败: {e}")

    def save_paint_overlay_image(
        self,
        source_path: str,
        overlay: Optional[np.ndarray],
    ) -> Optional[str]:
        """将 paint overlay 落盘到 manga_translator_work/paint_overlay 目录。

        若 overlay 为 None 或全透明，且已存在旧文件，则保留旧文件不删除；
        若从未保存过则不创建空文件。
        """
        try:
            if overlay is None:
                return None
            overlay_arr = np.asarray(overlay)
            if overlay_arr.ndim != 3 or overlay_arr.shape[2] < 4:
                return None
            if not np.any(overlay_arr[..., 3]):
                return None

            from PIL import Image as _PILImage

            overlay_path = get_paint_overlay_path(source_path, create_dir=True)
            pil_overlay = _PILImage.fromarray(overlay_arr.astype(np.uint8, copy=False), mode="RGBA")
            try:
                pil_overlay.save(overlay_path, format="PNG", optimize=False)
            finally:
                pil_overlay.close()
            self.logger.info(f"已更新彩色画笔图层: {overlay_path}")
            return overlay_path
        except Exception as e:
            self.logger.warning(f"保存彩色画笔图层失败: {e}")
            return None

    @staticmethod
    def compose_image_with_overlay(
        base_image: Optional[object],
        overlay: Optional[np.ndarray],
    ) -> Optional[object]:
        """把 paint overlay（RGBA）合成到 inpainted 底图上，返回 numpy RGB 数组。

        若 overlay 为空或无有效 alpha，则原样返回 base_image（不复制）。
        """
        if base_image is None:
            return base_image
        if overlay is None:
            return base_image

        overlay_arr = np.asarray(overlay)
        if overlay_arr.ndim != 3 or overlay_arr.shape[2] < 4:
            return base_image
        if not np.any(overlay_arr[..., 3]):
            return base_image

        base_rgb = image_like_to_rgb_array(base_image, copy=True)
        if base_rgb is None:
            return base_image

        h, w = base_rgb.shape[:2]
        overlay_resized = overlay_arr
        if overlay_arr.shape[:2] != (h, w):
            try:
                overlay_resized = cv2.resize(
                    overlay_arr,
                    (w, h),
                    interpolation=cv2.INTER_NEAREST,
                )
            except Exception:
                return base_image

        alpha = overlay_resized[..., 3].astype(np.float32) / 255.0
        alpha3 = np.repeat(alpha[..., None], 3, axis=2)
        rgb = overlay_resized[..., :3].astype(np.float32)
        composed = base_rgb.astype(np.float32) * (1.0 - alpha3) + rgb * alpha3
        return np.clip(composed, 0, 255).astype(np.uint8, copy=False)

    def persist_editor_state_for_export(
        self,
        export_service,
        source_path: str,
        regions: list,
        mask: Optional[np.ndarray],
        config_dict: dict,
        inpainted_image: Optional[object] = None,
    ) -> str:
        json_path = self.resolve_editor_json_path(source_path)
        json_regions = [dict(region) for region in regions]
        for region in json_regions:
            self.apply_white_frame_center(region)
        export_service._save_regions_data_with_path(json_regions, json_path, source_path, mask, config_dict)
        self.save_current_inpainted_image(
            source_path,
            config_dict,
            mask,
            current_inpainted_image=inpainted_image,
            has_regions=bool(regions),
        )
        return json_path

    def _build_output_path(self, config, source_path: Optional[str]) -> str:
        save_to_source_dir = getattr(config.cli, "save_to_source_dir", False) if hasattr(config, "cli") else False
        if save_to_source_dir and source_path:
            output_dir = os.path.join(os.path.dirname(source_path), "manga_translator_work", "result")
            os.makedirs(output_dir, exist_ok=True)
        else:
            output_dir = getattr(config.app, "last_output_path", None) if hasattr(config, "app") else None
            if not output_dir or not os.path.exists(output_dir):
                output_dir = os.path.dirname(source_path) if source_path else os.getcwd()

        if source_path:
            base_name = os.path.splitext(os.path.basename(source_path))[0]
            output_format = getattr(config.cli, "format", "") if hasattr(config, "cli") else ""
            if output_format == "不指定":
                output_format = None
            if output_format and output_format.strip():
                output_filename = f"{base_name}.{output_format.lower()}"
            else:
                original_ext = os.path.splitext(source_path)[1].lower()
                output_filename = f"{base_name}{original_ext}" if original_ext else f"{base_name}.png"
        else:
            output_filename = f"exported_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

        return os.path.join(output_dir, output_filename)

    @staticmethod
    def _build_config_dict(config) -> dict:
        if hasattr(config, "model_dump"):
            return config.model_dump()
        if hasattr(config, "dict"):
            return config.dict()
        return {}

    @staticmethod
    def _prepare_render_config(config_dict: dict) -> None:
        render_config = config_dict.setdefault("render", {})
        render_config["disable_auto_wrap"] = True

    def _build_enhanced_regions(self, regions: list[dict]) -> list[dict]:
        render_service = get_render_parameter_service()
        enhanced_regions = []
        for index, region in enumerate(regions):
            enhanced_region = region.copy()
            if not enhanced_region.get("translation"):
                enhanced_region["translation"] = enhanced_region.get("text", "")
            if not enhanced_region.get("font_size"):
                enhanced_region["font_size"] = 16
            if not enhanced_region.get("alignment"):
                enhanced_region["alignment"] = "center"
            if not enhanced_region.get("direction"):
                enhanced_region["direction"] = "auto"

            self.apply_white_frame_center(enhanced_region)
            enhanced_region.update(render_service.export_parameters_for_backend(index, enhanced_region))
            enhanced_regions.append(enhanced_region)
        return enhanced_regions

    async def async_export_with_desktop_ui_service(
        self,
        image,
        regions,
        mask,
        source_path: Optional[str] = None,
        inpainted_image=None,
        paint_overlay: Optional[np.ndarray] = None,
    ):
        outcome = {
            "success": False,
            "error": None,
            "output_path": None,
            "json_path": None,
        }
        try:
            from services.export_service import ExportService

            config = self.config_service.get_config()
            output_path = self._build_output_path(config, source_path)
            outcome["output_path"] = output_path
            export_service = ExportService()
            config_dict = self._build_config_dict(config)
            self._prepare_render_config(config_dict)

            persisted_json_path = None
            if source_path:
                persisted_json_path = self.persist_editor_state_for_export(
                    export_service=export_service,
                    source_path=source_path,
                    regions=regions,
                    mask=mask,
                    config_dict=config_dict,
                    inpainted_image=inpainted_image,
                )
                outcome["json_path"] = persisted_json_path
                # 同步持久化彩色画笔图层
                self.save_paint_overlay_image(source_path, paint_overlay)
            else:
                self.logger.warning("Exporting without source image path, skipped JSON persistence")

            # 在交给后端渲染前，先把彩色画笔图层合成到修复底图上，
            # 这样后端在复用 inpainted 图时文字会叠在用户涂抹的结果之上。
            render_inpainted_image = inpainted_image
            if paint_overlay is not None and inpainted_image is not None:
                composed = self.compose_image_with_overlay(inpainted_image, paint_overlay)
                if composed is not None and composed is not inpainted_image:
                    render_inpainted_image = composed

            def progress_callback(_message):
                return None

            def success_callback(_message):
                outcome["success"] = True
                success_message = f"导出成功\n{output_path}"
                if persisted_json_path:
                    success_message += "\n已同步 JSON"
                self.controller._show_toast_signal.emit(success_message, 5000, True, output_path)

                if self.controller._is_same_source_image(self.model.get_source_image_path(), source_path):
                    self.save_export_snapshot()
                    self.resource_manager.release_memory_after_export()
                    self.resource_manager.release_image_cache_except_current()
                    self.controller._log_memory_snapshot("after-export-cleanup")
                else:
                    self.logger.debug("Skipped export snapshot update because active image changed during export")

            def error_callback(message):
                outcome["error"] = str(message)
                self.logger.error(f"Export error: {message}")
                self.controller._show_toast_signal.emit(f"导出失败：{message}", 5000, False, "")

            enhanced_regions = self._build_enhanced_regions(regions)
            await asyncio.to_thread(
                export_service._perform_backend_render_export,
                image,
                enhanced_regions,
                config_dict,
                output_path,
                mask,
                progress_callback,
                success_callback,
                error_callback,
                source_path,
                False,
                render_inpainted_image,
            )
            if not outcome["success"] and outcome["error"] is None:
                outcome["error"] = "导出未返回成功状态"
            return outcome
        except Exception as e:
            self.logger.error(f"Error during async export: {e}", exc_info=True)
            err_msg = str(e)
            outcome["error"] = err_msg
            QTimer.singleShot(
                0,
                lambda: QMessageBox.critical(None, "导出失败", f"导出过程中发生意外错误:\n{err_msg}"),
            )
            return outcome

    async def async_batch_export(self, file_paths: list[str], config,
                                  progress_callback=None) -> tuple[int, int]:
        """不经过编辑器，按顺序逐个读取 JSON 和修复图并导出。

        Args:
            file_paths: 源图片路径列表
            config: 配置对象
            progress_callback: 进度回调 fn(done_count)

        Returns:
            (成功数, 失败数)
        """
        from services.export_service import ExportService
        from manga_translator.utils.path_manager import find_json_path, find_inpainted_path

        config_dict = self._build_config_dict(config)
        self._prepare_render_config(config_dict)
        export_service = ExportService()

        sem = asyncio.Semaphore(4)

        async def _export_one(fp: str) -> bool:
            async with sem:
                if getattr(self.controller, '_export_cancel_flag', False):
                    return False
                try:
                    json_path = find_json_path(fp)
                    if not json_path:
                        self.logger.warning(f"[批量导出] 未找到 JSON: {fp}")
                        return False

                    with open(json_path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                    if isinstance(raw, list):
                        regions = raw
                    elif isinstance(raw, dict):
                        if fp in raw:
                            entry = raw[fp]
                        else:
                            entry = raw
                        for key in ('regions', 'text_regions', 'region'):
                            if isinstance(entry, dict) and key in entry:
                                regions = entry[key]
                                break
                        else:
                            if isinstance(entry, list):
                                regions = entry
                            else:
                                self.logger.warning(f'[批量导出] 无法解析 JSON regions: {fp}')
                                return False
                    else:
                        self.logger.warning(f'[批量导出] JSON 根类型异常: {type(raw).__name__}, {fp}')
                        return False

                    img = self.controller.resource_manager.load_detached_image(fp)
                    if img is None:
                        self.logger.warning(f"[批量导出] 加载图片失败: {fp}")
                        return False

                    inpainted_path = find_inpainted_path(fp)
                    inpainted_img = None
                    if inpainted_path:
                        from PIL import Image
                        inpainted_img = Image.open(inpainted_path).convert('RGBA')

                    # 从 JSON 中提取 mask_raw（base64 PNG → numpy）
                    mask_arr = None
                    if isinstance(raw, dict):
                        entry = raw[fp] if fp in raw else None
                        if entry is None and isinstance(next(iter(raw.values()), None), dict):
                            entry = next(iter(raw.values()))
                        if isinstance(entry, dict):
                            mask_raw_b64 = entry.get('mask_raw')
                            if mask_raw_b64:
                                import base64, io
                                try:
                                    mask_bytes = base64.b64decode(mask_raw_b64)
                                    mask_img = Image.open(io.BytesIO(mask_bytes))
                                    mask_arr = np.array(mask_img.convert('L'))
                                except Exception as e:
                                    self.logger.warning(f'[批量导出] mask 解码失败: {e}')

                    output_path = self._build_output_path(config, fp)
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)

                    enhanced = self._build_enhanced_regions(regions)
                    await asyncio.to_thread(
                        export_service._perform_backend_render_export,
                        img, enhanced, config_dict, output_path,
                        mask_arr, lambda _: None, lambda _: None, lambda _: None,
                        fp, False, inpainted_img,
                    )
                    return True
                except Exception as e:
                    self.logger.error(f"[批量导出] 失败 {fp}: {e}")
                    return False

        success = 0
        fail = 0
        coros = [_export_one(fp) for fp in file_paths]
        for task in asyncio.as_completed(coros):
            ok = await task
            if ok:
                success += 1
            else:
                fail += 1
            if progress_callback:
                progress_callback(success + fail)
        return success, fail
