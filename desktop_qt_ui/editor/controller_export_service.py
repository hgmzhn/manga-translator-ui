from __future__ import annotations

import concurrent.futures
import copy
import math
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import numpy as np
from manga_translator.image_formats import resolve_pil_image_format
from manga_translator.utils import save_pil_image
from manga_translator.utils.path_manager import (
    find_json_path,
    get_inpainted_path,
    get_json_path,
)

from services import get_render_parameter_service

from .document_state import ExportBase
from .image_utils import image_like_to_pil
from .inpaint_state import InpaintArtifact
from .region_geometry_state import normalize_region_geometry_data

if TYPE_CHECKING:
    from .editor_controller import EditorController


def _close_images(*images: object) -> None:
    closed: set[int] = set()
    for image in images:
        identity = id(image)
        if identity in closed:
            continue
        closed.add(identity)
        close = getattr(image, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class ExportJob:
    automatic: bool
    source_path: str
    output_path: str
    export_base: ExportBase
    regions: list[dict]
    config: dict
    paint_overlay: Optional[np.ndarray] = None
    stamp_overlay: Optional[np.ndarray] = None

    def __post_init__(self) -> None:
        if not isinstance(self.automatic, bool):
            raise TypeError("automatic export flag must be bool")
        if not self.source_path or not self.output_path:
            raise ValueError("export source and output paths are required")
        if not isinstance(self.export_base, ExportBase):
            raise TypeError("export_base must be an ExportBase")
        if (
            self.export_base.kind in {"source", "backend_inpaint"}
            and self.export_base.render_image is not self.export_base.source_image
        ):
            raise ValueError(f"{self.export_base.kind} export must render from source")
        if not isinstance(self.regions, list) or not all(
            isinstance(region, dict) for region in self.regions
        ):
            raise TypeError("export regions must be a list of dictionaries")
        if not isinstance(self.config, dict):
            raise TypeError("export config must be a dictionary")
        object.__setattr__(self, "regions", copy.deepcopy(self.regions))
        object.__setattr__(self, "config", copy.deepcopy(self.config))
        for field_name in ("paint_overlay", "stamp_overlay"):
            overlay = getattr(self, field_name)
            if overlay is None:
                continue
            array = np.asarray(overlay)
            if array.ndim != 3 or array.shape[2] != 4:
                raise ValueError(f"{field_name} must be an RGBA image")
            object.__setattr__(self, field_name, np.array(array, copy=True))

    @property
    def source_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.source_path))

    def release_resources(self) -> None:
        _close_images(
            self.export_base.source_image,
            self.export_base.render_image,
        )


@dataclass(frozen=True, slots=True)
class ExportOutcome:
    automatic: bool
    source_path: str
    output_path: str
    success: bool
    error: Optional[str] = None
    generated_artifact: Optional[InpaintArtifact] = None


class EditorControllerExportService:
    """编辑器工程保存与渲染图片导出流程。"""

    def __init__(self, controller: "EditorController"):
        self.controller = controller
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="editor-export",
        )
        self._state_lock = threading.RLock()
        self._accepting = True
        self._unfinished = 0
        self._pending_auto: dict[str, concurrent.futures.Future] = {}

    @property
    def model(self):
        return self.controller.model

    @property
    def logger(self):
        return self.controller.logger

    @property
    def config_service(self):
        return self.controller.config_service

    def has_unsaved_changes(self) -> bool:
        """脏检测唯一真相源：仅成功保存工程数据后标记 clean。"""
        return not self.controller.history_service.is_clean()

    def save_editor_state(self) -> bool:
        """同步保存 JSON 与当前修复图，不执行图片渲染导出。"""
        self.controller.commit_pending_edits()
        source_path = self.model.get_source_image_path()
        if not source_path:
            self._reject_export("保存失败：当前图片没有来源路径")
            return False

        inpainted_snapshot = None
        delete_inpainted_sidecar = False
        try:
            image = self.controller._get_current_image()
            if image is None:
                self._reject_export("保存失败：缺少图像数据")
                return False
            regions = copy.deepcopy(self.controller._get_regions() or [])
            current_mask = self.model.get_refined_mask()
            if current_mask is None:
                current_mask = self.model.get_raw_mask()

            if current_mask is not None and np.any(current_mask):
                artifact = self.model.get_ready_inpaint_artifact()
                if artifact is None:
                    self._reject_export("保存失败：蒙版修复处理中，请稍后重试")
                    return False
                mask_snapshot = artifact.mask
                inpainted_snapshot = artifact.image
            else:
                mask_snapshot = (
                    None
                    if current_mask is None
                    else np.array(current_mask, copy=True)
                )
                delete_inpainted_sidecar = True
            config = self.config_service.get_config()
            config_dict = self._build_config_dict(config)
            paint_snapshot = self._snapshot_overlay(
                self.model.get_paint_overlay_image()
            )
            stamp_snapshot = self._snapshot_overlay(
                self.model.get_stamp_overlay_image()
            )
            from services.export_service import ExportService

            persistence_service = ExportService()
            self.save_editor_json(
                export_service=persistence_service,
                source_path=os.path.abspath(source_path),
                regions=regions,
                mask=mask_snapshot,
                config_dict=config_dict,
                last_export_dir=self._read_saved_export_dir(source_path),
                paint_overlay=paint_snapshot,
                stamp_overlay=stamp_snapshot,
            )
            if delete_inpainted_sidecar:
                self._delete_inpainted_sidecar(os.path.abspath(source_path))
            if inpainted_snapshot is not None:
                self.save_inpainted_image(
                    os.path.abspath(source_path),
                    config_dict,
                    inpainted_snapshot,
                )
            self.controller.history_service.mark_clean()
            toast_manager = self.controller.get_toast_manager()
            if toast_manager is not None:
                toast_manager.show_success("保存成功", 2500)
            return True
        except Exception as e:
            self.logger.error("Failed to save editor state", exc_info=True)
            self._reject_export(f"保存失败：{e}")
            return False
        finally:
            _close_images(inpainted_snapshot)

    def export_image(
        self,
        automatic: bool = False,
    ) -> Optional[concurrent.futures.Future]:
        source_path = self.model.get_source_image_path()
        if not source_path:
            return self._reject_export("导出失败：当前图片没有来源路径")

        source_snapshot = None
        render_snapshot = None
        try:
            export_base = self.model.get_export_base()
            if export_base is None:
                return self._reject_export("导出失败：缺少活动文档")
            regions = self.controller._get_regions() or []

            source_snapshot = self.controller._snapshot_image_for_export(
                export_base.source_image,
                "export source image",
            )
            if export_base.kind == "paired":
                render_snapshot = self.controller._snapshot_image_for_export(
                    export_base.render_image,
                    "paired render image",
                )
            else:
                render_snapshot = source_snapshot
            base_snapshot = ExportBase(
                export_base.kind,
                source_snapshot,
                render_snapshot,
                export_base.mask,
                export_base.inpaint_key,
            )
            paint_snapshot = self._snapshot_overlay(
                self.model.get_paint_overlay_image()
            )
            stamp_snapshot = self._snapshot_overlay(
                self.model.get_stamp_overlay_image()
            )
            config = self.config_service.get_config()
            config_dict = self._build_config_dict(config)
            self._prepare_render_config(config_dict)
            output_path = self._build_output_path(config, source_path)

            job = ExportJob(
                automatic=bool(automatic),
                source_path=os.path.abspath(source_path),
                output_path=output_path,
                export_base=base_snapshot,
                regions=regions,
                config=config_dict,
                paint_overlay=paint_snapshot,
                stamp_overlay=stamp_snapshot,
            )
            future = self._submit_job(job)
            if future is None:
                job.release_resources()
                return self._reject_export("导出队列已经关闭")
            return future
        except Exception as e:
            self.logger.error(f"Error during export request: {e}", exc_info=True)
            _close_images(source_snapshot, render_snapshot)
            return self._reject_export(f"导出快照创建失败：{e}")

    def _reject_export(self, message: str):
        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            toast_manager.show_error(message, 5000)
        return None

    def _submit_job(self, job: ExportJob) -> Optional[concurrent.futures.Future]:
        source_key = job.source_key if job.automatic else None
        with self._state_lock:
            if not self._accepting:
                return None
            previous = self._pending_auto.get(source_key) if source_key else None
            try:
                future = self._executor.submit(self.execute_export_job, job)
            except RuntimeError:
                return None
            self._unfinished += 1
            if source_key:
                self._pending_auto[source_key] = future

        future.add_done_callback(
            lambda done, queued_job=job, key=source_key: self._on_job_done(
                done,
                queued_job,
                key,
            )
        )
        if previous is not None:
            previous.cancel()
        self.controller._export_queue_status_signal.emit(self.unfinished_count())
        return future

    def _on_job_done(
        self,
        future: concurrent.futures.Future,
        job: ExportJob,
        source_key: Optional[str],
    ) -> None:
        outcome = None
        if not future.cancelled():
            try:
                outcome = future.result()
            except Exception as e:
                self.logger.exception("Unhandled editor export error")
                outcome = ExportOutcome(
                    automatic=job.automatic,
                    source_path=job.source_path,
                    output_path=job.output_path,
                    success=False,
                    error=str(e),
                )

        with self._state_lock:
            self._unfinished = max(0, self._unfinished - 1)
            if source_key and self._pending_auto.get(source_key) is future:
                self._pending_auto.pop(source_key, None)
            unfinished = self._unfinished

        job.release_resources()
        self.controller._export_queue_status_signal.emit(unfinished)
        if outcome is not None:
            self.controller._export_job_finished_signal.emit(outcome)

    def unfinished_count(self) -> int:
        with self._state_lock:
            return self._unfinished

    def shutdown(self) -> None:
        with self._state_lock:
            if not self._accepting:
                return
            self._accepting = False
        self._executor.shutdown(wait=True, cancel_futures=False)

    @staticmethod
    def resolve_effective_box_local(region: dict):
        if not isinstance(region, dict):
            return None
        region = normalize_region_geometry_data(region)

        custom_box = region.get("white_frame_rect_local")
        render_box = region.get("render_box_rect_local")
        has_custom = bool(region.get("has_custom_white_frame", False))

        # 解绑：与编辑器 snapshot 同步——用户手动白框存在时优先白框，
        # 让导出和预览的渲染中心走同一条路。
        if (
            has_custom
            and isinstance(custom_box, (list, tuple))
            and len(custom_box) == 4
        ):
            return custom_box
        if isinstance(render_box, (list, tuple)) and len(render_box) == 4:
            return render_box
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
            cx, cy = float(base_center[0]), float(base_center[1])
            angle = float(region.get("angle") or 0.0)
            rad = math.radians(angle)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            region["center"] = [
                cx + lx * cos_a - ly * sin_a,
                cy + lx * sin_a + ly * cos_a,
            ]
            # 同步平移 local 坐标，以新 center 为原点，防止存/读漂移
            if "white_frame_rect_local" in region:
                wf = region["white_frame_rect_local"]
                if isinstance(wf, (list, tuple)) and len(wf) == 4:
                    region["white_frame_rect_local"] = [
                        float(wf[0]) - lx,
                        float(wf[1]) - ly,
                        float(wf[2]) - lx,
                        float(wf[3]) - ly,
                    ]
            if "render_box_rect_local" in region:
                rb = region["render_box_rect_local"]
                if isinstance(rb, (list, tuple)) and len(rb) == 4:
                    region["render_box_rect_local"] = [
                        float(rb[0]) - lx,
                        float(rb[1]) - ly,
                        float(rb[2]) - lx,
                        float(rb[3]) - ly,
                    ]
        except (TypeError, ValueError):
            return

    def _delete_inpainted_sidecar(self, source_path: str) -> None:
        inpainted_path = get_inpainted_path(source_path, create_dir=False)
        try:
            os.remove(inpainted_path)
        except FileNotFoundError:
            return
        self.logger.info(f"已删除过期修复图片: {inpainted_path}")

    def resolve_editor_json_path(self, source_path: str) -> str:
        json_path = find_json_path(source_path)
        if not json_path:
            json_path = get_json_path(source_path, create_dir=True)
            self.logger.info(
                f"No existing JSON found, will create new one at: {json_path}"
            )
        else:
            self.logger.info(f"Found existing JSON, will replace: {json_path}")
        return json_path

    def save_inpainted_image(
        self,
        source_path: str,
        config_dict: dict,
        image: object,
    ) -> str:
        inpainted_path = get_inpainted_path(source_path, create_dir=True)
        save_quality = config_dict.get("cli", {}).get("save_quality", 95)
        save_image = image_like_to_pil(image)
        if save_image is None:
            raise ValueError("inpainted image snapshot is empty")

        image_format = resolve_pil_image_format(inpainted_path)
        temp_path = f"{inpainted_path}.{uuid.uuid4().hex}.tmp"
        try:
            save_pil_image(
                save_image,
                temp_path,
                quality=save_quality,
                format=image_format,
            )
            os.replace(temp_path, inpainted_path)
            self.logger.info(f"已更新修复图片: {inpainted_path}")
            return inpainted_path
        finally:
            try:
                save_image.close()
            except Exception:
                pass
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def _snapshot_overlay(overlay) -> Optional[np.ndarray]:
        """有有效 alpha 内容时返回 overlay 的 RGBA 副本，否则 None。"""
        if overlay is None:
            return None
        overlay_arr = np.asarray(overlay)
        if overlay_arr.ndim != 3 or overlay_arr.shape[2] != 4:
            return None
        if not np.any(overlay_arr[..., 3]):
            return None
        return overlay_arr.copy()

    def save_editor_json(
        self,
        export_service,
        source_path: str,
        regions: list,
        mask: Optional[np.ndarray],
        config_dict: dict,
        last_export_dir: Optional[str] = None,
        paint_overlay: Optional[np.ndarray] = None,
        stamp_overlay: Optional[np.ndarray] = None,
    ) -> None:
        json_path = self.resolve_editor_json_path(source_path)
        # 写盘的 region 保持 center=源区域中心、white_frame_rect_local 相对该中心。
        # 给后端 load_text 渲染用的副本（_build_enhanced_regions）才需要把
        # center 平移到白框中心；两条路径不能共用，否则下次编辑器加载会再叠加一次偏移。
        json_regions = [dict(region) for region in regions]
        export_service._save_regions_data_with_path(
            json_regions,
            json_path,
            source_path,
            mask,
            config_dict,
            last_export_dir=last_export_dir,
            paint_overlay=paint_overlay,
            stamp_overlay=stamp_overlay,
        )

    def _read_saved_export_dir(self, source_path: Optional[str]) -> Optional[str]:
        """从该图片对应的 _translations.json 中读取主翻译流程记录的输出目录。"""
        if not source_path:
            return None
        try:
            json_path = find_json_path(source_path)
            if not json_path or not os.path.exists(json_path):
                return None
            import json as _json

            with open(json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if not isinstance(data, dict) or not data:
                return None
            image_key = os.path.abspath(source_path)
            image_data = data.get(image_key)
            if not isinstance(image_data, dict):
                image_data = next(iter(data.values()), None)
            if not isinstance(image_data, dict):
                return None
            saved_dir = image_data.get("last_export_dir")
            if isinstance(saved_dir, str) and saved_dir.strip():
                return os.path.normpath(saved_dir.strip())
        except Exception as e:
            self.logger.debug(f"Failed to read saved export dir for {source_path}: {e}")
        return None

    def _build_output_path(self, config, source_path: Optional[str]) -> str:
        saved_export_dir = self._read_saved_export_dir(source_path)
        if saved_export_dir:
            output_dir = saved_export_dir
        else:
            save_to_source_dir = (
                getattr(config.cli, "save_to_source_dir", False)
                if hasattr(config, "cli")
                else False
            )
            if save_to_source_dir and source_path:
                output_dir = os.path.join(
                    os.path.dirname(source_path), "manga_translator_work", "result"
                )
            else:
                output_dir = (
                    getattr(config.app, "last_output_path", None)
                    if hasattr(config, "app")
                    else None
                )
                if not output_dir or not os.path.exists(output_dir):
                    output_dir = (
                        os.path.dirname(source_path) if source_path else os.getcwd()
                    )
        os.makedirs(output_dir, exist_ok=True)

        if source_path:
            base_name = os.path.splitext(os.path.basename(source_path))[0]
            output_format = (
                getattr(config.cli, "format", "") if hasattr(config, "cli") else ""
            )
            if output_format == "不指定":
                output_format = None
            if output_format and output_format.strip():
                output_filename = f"{base_name}.{output_format.lower()}"
            else:
                original_ext = os.path.splitext(source_path)[1].lower()
                output_filename = (
                    f"{base_name}{original_ext}" if original_ext else f"{base_name}.png"
                )
        else:
            output_filename = (
                f"exported_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )

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
            enhanced_region.update(
                render_service.export_parameters_for_backend(index, enhanced_region)
            )
            enhanced_regions.append(enhanced_region)
        return enhanced_regions

    def execute_export_job(self, job: ExportJob) -> ExportOutcome:
        """Render one immutable three-state snapshot without editor mutation."""
        from services.export_service import ExportService

        render_error: Optional[str] = None
        backend_outcome = None
        try:
            def error_callback(message):
                nonlocal render_error
                render_error = str(message)

            backend_outcome = ExportService()._perform_backend_render_export(
                job.export_base,
                self._build_enhanced_regions(job.regions),
                job.config,
                job.output_path,
                error_callback=error_callback,
                source_image_path=job.source_path,
                paint_overlay=job.paint_overlay,
                stamp_overlay=job.stamp_overlay,
            )
        except Exception as e:
            render_error = str(e)
            self.logger.error("Failed to render editor export job", exc_info=True)

        if backend_outcome is None:
            return ExportOutcome(
                automatic=job.automatic,
                source_path=job.source_path,
                output_path=job.output_path,
                success=False,
                error=render_error or "导出未返回成功状态",
            )

        generated_artifact = None
        generated_image = backend_outcome.generated_inpainted_image
        if job.export_base.kind == "backend_inpaint":
            if generated_image is None:
                return ExportOutcome(
                    automatic=job.automatic,
                    source_path=job.source_path,
                    output_path=job.output_path,
                    success=False,
                    error="后端修复未返回修复图",
                )
            generated_artifact = InpaintArtifact(
                job.export_base.inpaint_key,
                job.export_base.mask,
                generated_image,
            )
        elif generated_image is not None:
            return ExportOutcome(
                automatic=job.automatic,
                source_path=job.source_path,
                output_path=job.output_path,
                success=False,
                error=f"{job.export_base.kind} 导出意外返回后端修复图",
            )

        return ExportOutcome(
            automatic=job.automatic,
            source_path=job.source_path,
            output_path=job.output_path,
            success=True,
            generated_artifact=generated_artifact,
        )
