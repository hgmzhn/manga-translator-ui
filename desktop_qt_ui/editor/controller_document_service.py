from __future__ import annotations

import concurrent.futures
import os
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QMessageBox
from services import get_render_parameter_service
from widgets.themed_message_box import apply_message_box_style

from .session import DocumentLoadFailure, DocumentSnapshot

from manga_translator.utils.path_manager import (
    find_inpainted_path,
    find_json_path,
    find_paint_overlay_path,
    find_work_image_path,
    resolve_original_image_path,
)

if TYPE_CHECKING:
    from .editor_controller import EditorController


class EditorControllerDocumentService:
    """文档加载/清理流程。"""

    def __init__(self, controller: "EditorController"):
        self.controller = controller

    @property
    def model(self):
        return self.controller.model

    @property
    def view(self):
        return self.controller.view

    @property
    def logger(self):
        return self.controller.logger

    @property
    def async_service(self):
        return self.controller.async_service

    @property
    def history_service(self):
        return self.controller.history_service

    @property
    def resource_manager(self):
        return self.controller.resource_manager

    @property
    def file_service(self):
        return self.controller.file_service

    def clear_editor_state(self, release_image_cache: bool = False) -> None:
        loading_toast = getattr(self.controller, "_loading_toast", None)
        if loading_toast is not None:
            try:
                loading_toast.close()
            except Exception:
                pass
            self.controller._loading_toast = None

        self.async_service.cancel_all_tasks()
        self.controller.inpaint_service.invalidate_inpaint_requests()

        self.resource_manager.unload_image(release_from_cache=release_image_cache)
        self.model.clear_document()

        toolbar = self.controller.get_toolbar()
        if toolbar is not None:
            toolbar.set_export_enabled(False)

        self.history_service.clear()
        self.history_service.mark_clean()
        self.controller._update_undo_redo_buttons()

        self.controller._user_adjusted_alpha = False
        self.controller._last_export_snapshot = None
        self.controller._log_memory_snapshot("after-clear-editor-state")

        self.resource_manager.clear_cache()
        render_parameter_service = get_render_parameter_service()
        render_parameter_service.clear_cache()

        graphics_view = self.controller.get_graphics_view()
        if graphics_view is not None:
            graphics_view.render_coordinator.reset()

        load_executor = getattr(self.controller, "_load_executor", None)
        if load_executor is not None:
            try:
                load_executor.shutdown(wait=False)
            except Exception:
                pass
            delattr(self.controller, "_load_executor")

        self.logger.debug("Editor state cleared and memory released")

    def find_source_from_translation_map(self, image_path: str) -> Optional[str]:
        try:
            import json

            norm_path = os.path.normpath(image_path)
            output_dir = os.path.dirname(norm_path)
            map_path = os.path.join(output_dir, "translation_map.json")
            if not os.path.exists(map_path):
                return None

            with open(map_path, "r", encoding="utf-8") as f:
                translation_map = json.load(f)

            source_path = translation_map.get(norm_path)
            if source_path and os.path.exists(source_path):
                return os.path.normpath(source_path)
        except Exception as e:
            self.logger.error(f"Error reading translation map for {image_path}: {e}")
        return None

    def _load_paint_overlay_array(self, overlay_path: str, target_size):
        """加载 paint overlay 图层并对齐到底图尺寸，返回 RGBA uint8 numpy 数组。"""
        import numpy as np
        from PIL import Image

        try:
            with Image.open(overlay_path) as overlay_image:
                overlay_image.load()
                if overlay_image.mode != "RGBA":
                    converted = overlay_image.convert("RGBA")
                    overlay_image.close()
                    overlay_image = converted
                if target_size is not None and overlay_image.size != target_size:
                    resized = overlay_image.resize(target_size, Image.Resampling.NEAREST)
                    overlay_image.close()
                    overlay_image = resized
                array = np.array(overlay_image, dtype=np.uint8, copy=True)
            if array.ndim != 3 or array.shape[2] != 4:
                return None
            return array
        except Exception as e:
            self.logger.error(f"Failed to load paint overlay: {overlay_path} ({e})")
            return None

    def resolve_editor_image_paths(self, image_path: str) -> tuple[str, str]:
        source_path = self.find_source_from_translation_map(image_path)
        if not source_path:
            source_path = resolve_original_image_path(image_path)

        display_image_path = find_work_image_path(source_path) or source_path
        return os.path.normpath(source_path), os.path.normpath(display_image_path)

    def load_image_and_regions(self, image_path: str) -> None:
        if self.controller.export_service.has_changes_since_last_export():
            msg_box = QMessageBox(None)
            msg_box.setWindowTitle("未保存的编辑")
            msg_box.setText("当前图片有未保存的编辑")
            msg_box.setInformativeText("导出图片时会同时保存 JSON。")

            export_btn = msg_box.addButton("导出图片", QMessageBox.ButtonRole.YesRole)
            cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg_box.addButton("不保存", QMessageBox.ButtonRole.NoRole)

            msg_box.setDefaultButton(cancel_btn)
            apply_message_box_style(msg_box)
            msg_box.exec()

            clicked_button = msg_box.clickedButton()
            if clicked_button == cancel_btn:
                return
            if clicked_button == export_btn:
                export_future = self.controller.export_image()
                if export_future is None:
                    self.logger.warning("Export request was not scheduled; aborted deferred image load.")
                    return
                export_future.add_done_callback(
                    lambda future, target_path=image_path: self._continue_load_after_export(
                        target_path,
                        future,
                    )
                )
                return

        self.do_load_image(image_path)

    def _continue_load_after_export(self, image_path: str, future) -> None:
        try:
            result = future.result()
        except Exception as e:
            self.logger.error("Deferred image load skipped because export task failed: %s", e, exc_info=True)
            return

        if isinstance(result, dict) and result.get("success"):
            self.controller._deferred_load_requested.emit(image_path)
            return

        self.logger.info(
            "Deferred image load skipped because export did not complete successfully: %s",
            result,
        )

    def do_load_image(self, image_path: str) -> None:
        # 切图轻量清理：取消后台任务 + 清历史。不碰 model/画布，旧图保留。
        self.async_service.cancel_all_tasks()
        self.controller.inpaint_service.invalidate_inpaint_requests()
        self.history_service.clear()
        self.history_service.mark_clean()
        self.controller._update_undo_redo_buttons()
        self.controller._user_adjusted_alpha = False
        self.controller._last_export_snapshot = None
        from services import get_render_parameter_service
        get_render_parameter_service().clear_cache()
        load_executor = getattr(self.controller, "_load_executor", None)
        if load_executor is not None:
            try:
                load_executor.shutdown(wait=False)
            except Exception:
                pass
            delattr(self.controller, "_load_executor")

        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            self.controller._loading_toast = toast_manager.show_info("正在加载...", duration=0)

        if not hasattr(self.controller, "_load_executor"):
            self.controller._load_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def load_data():
            try:
                source_path, display_image_path = self.resolve_editor_image_paths(image_path)
                image_resource = self.resource_manager.load_image(display_image_path)
                image = image_resource.image

                compare_image = image
                if os.path.normpath(source_path) != os.path.normpath(display_image_path):
                    try:
                        compare_image = self.controller._load_detached_image_array(source_path, image.size)
                    except Exception as compare_error:
                        self.logger.warning(f"Error loading compare image: {compare_error}")

                json_path = find_json_path(source_path)
                regions = []
                raw_mask = None
                if json_path:
                    regions, raw_mask, _ = self.file_service.load_translation_json(source_path)

                inpainted_path = find_inpainted_path(source_path)
                inpainted_image = None
                if inpainted_path:
                    try:
                        inpainted_image = self.controller._load_detached_image_array(inpainted_path, image.size)
                    except Exception as e:
                        self.logger.error(f"Error loading inpainted image: {e}")
                        inpainted_path = None

                paint_overlay_path = find_paint_overlay_path(source_path)
                paint_overlay_image = None
                if paint_overlay_path:
                    try:
                        paint_overlay_image = self._load_paint_overlay_array(paint_overlay_path, image.size)
                        if paint_overlay_image is None:
                            paint_overlay_path = None
                    except Exception as e:
                        self.logger.error(f"Error loading paint overlay: {e}")
                        paint_overlay_path = None

                return DocumentSnapshot(
                    source_path=source_path,
                    image=image,
                    compare_image=compare_image,
                    regions=regions,
                    raw_mask=raw_mask,
                    inpainted_path=inpainted_path,
                    inpainted_image=inpainted_image,
                    paint_overlay_path=paint_overlay_path,
                    paint_overlay_image=paint_overlay_image,
                )
            except Exception as e:
                self.logger.error(f"Error loading image data: {e}", exc_info=True)
                return DocumentLoadFailure(str(e))

        def on_load_complete(future):
            try:
                result = future.result()
                self.controller._load_result_ready.emit(result)
            except Exception as e:
                self.logger.error(f"Load failed: {e}", exc_info=True)
                self.controller._load_result_ready.emit(DocumentLoadFailure(str(e)))

        future = self.controller._load_executor.submit(load_data)
        future.add_done_callback(on_load_complete)

    def apply_load_result(self, result: object) -> None:
        if isinstance(result, DocumentLoadFailure):
            self.handle_load_error(result.error)
            return
        if isinstance(result, DocumentSnapshot):
            self.apply_loaded_data_to_model(result)
            return
        self.handle_load_error("Unsupported load result")

    def apply_loaded_data_to_model(self, snapshot: DocumentSnapshot) -> None:
        loading_toast = getattr(self.controller, "_loading_toast", None)
        if loading_toast is not None:
            loading_toast.close()
            self.controller._loading_toast = None

        toolbar = self.controller.get_toolbar()
        if toolbar is not None:
            toolbar.set_export_enabled(True)

        if snapshot.regions:
            render_parameter_service = get_render_parameter_service()
            for index, region_data in enumerate(snapshot.regions):
                render_parameter_service.import_parameters_from_json(index, region_data)

        if not self.controller._user_adjusted_alpha:
            default_alpha = 0.0 if snapshot.inpainted_image is not None else 1.0
            self.model.set_original_image_alpha(default_alpha)

        self.model.apply_document_snapshot(snapshot)
        self.resource_manager.release_image_cache_except_current(force=True)
        self.controller._log_memory_snapshot("after-apply-loaded-document")

        if snapshot.regions and snapshot.raw_mask is not None:
            self.async_service.submit_task(self.controller.inpaint_service.async_refine_and_inpaint())

    def handle_load_error(self, error_msg: str) -> None:
        loading_toast = getattr(self.controller, "_loading_toast", None)
        if loading_toast is not None:
            loading_toast.close()
            self.controller._loading_toast = None

        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            toast_manager.show_error(f"加载失败: {error_msg}")

        self.model.clear_document()
        self.controller._log_memory_snapshot("after-load-error")
