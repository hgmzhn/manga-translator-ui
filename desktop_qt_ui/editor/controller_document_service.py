from __future__ import annotations

import concurrent.futures
import os
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QMessageBox
from services import get_render_parameter_service
from widgets.themed_message_box import apply_message_box_style

from .document_load_worker import DocumentLoadWorker
from .session import DocumentLoadFailure, DocumentSnapshot

from manga_translator.utils.path_manager import (
    find_json_path,
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

    def clear_editor_state(self, release_image_cache: bool = False, keep_document: bool = False) -> None:
        loading_toast = getattr(self.controller, "_loading_toast", None)
        if loading_toast is not None:
            try:
                loading_toast.close()
            except Exception:
                pass
            self.controller._loading_toast = None

        self.async_service.cancel_all_tasks()
        self.controller.inpaint_service.invalidate_inpaint_requests()

        if not keep_document:
            # keep_document=True: 切图场景,跳过 image_changed(None) 让旧画面留到新数据覆盖时
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

        if not keep_document:
            # 切图时不清缓存:LRU 还要保留 QImage 让来回切换瞬时
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

        if release_image_cache:
            prefetch_executor = getattr(self.controller, "_prefetch_executor", None)
            if prefetch_executor is not None:
                try:
                    prefetch_executor.shutdown(wait=False)
                except Exception:
                    pass
                delattr(self.controller, "_prefetch_executor")

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

    def resolve_editor_image_paths(self, image_path: str) -> tuple[str, str]:
        source_path = self.find_source_from_translation_map(image_path)
        if not source_path:
            source_path = resolve_original_image_path(image_path)

        work_image_path = find_work_image_path(source_path)
        if work_image_path and self._is_editor_base_stale(source_path):
            self._delete_stale_editor_base(work_image_path)
            work_image_path = None

        display_image_path = work_image_path or source_path
        return os.path.normpath(source_path), os.path.normpath(display_image_path)

    def _is_editor_base_stale(self, source_path: str) -> bool:
        """editor_base 只在最近一次运行真的做了超分或上色时才有意义；
        否则视为过期残留，避免编辑器加载到与当前 JSON 不匹配的旧底图。"""
        import json as _json

        json_path = find_json_path(source_path)
        if not json_path:
            # 没有 JSON 可参考 → 无法证明 editor_base 有效，按过期处理
            return True
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception as e:
            self.logger.warning(f"Failed to read JSON for editor_base staleness check: {e}")
            return False

        image_data = None
        if isinstance(data, dict):
            key = os.path.abspath(source_path)
            image_data = data.get(key)
            if image_data is None and data:
                image_data = next(iter(data.values()))
        if not isinstance(image_data, dict):
            return True

        has_upscale = bool(image_data.get("upscale_ratio"))
        colorizer = image_data.get("colorizer")
        has_colorizer = bool(colorizer) and str(colorizer).lower() != "none"
        return not (has_upscale or has_colorizer)

    def _delete_stale_editor_base(self, work_image_path: str) -> None:
        try:
            os.remove(work_image_path)
            self.logger.info(f"Removed stale editor_base image: {work_image_path}")
        except FileNotFoundError:
            pass
        except Exception as e:
            self.logger.warning(f"Failed to remove stale editor_base image {work_image_path}: {e}")

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
        # 切图:保留旧画面 + 旧 LRU 缓存,等新数据信号到达再覆盖,避免黑闪
        self.clear_editor_state(keep_document=True)

        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            self.controller._loading_toast = toast_manager.show_info("正在加载...", duration=0)

        if not hasattr(self.controller, "_load_executor"):
            self.controller._load_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def on_load_complete(future):
            try:
                result = future.result()
                self.controller._load_result_ready.emit(result)
            except Exception as e:
                self.logger.error(f"Load failed: {e}", exc_info=True)
                self.controller._load_result_ready.emit(DocumentLoadFailure(str(e)))

        worker = DocumentLoadWorker(self, image_path)
        future = self.controller._load_executor.submit(worker.load)
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
        self.resource_manager.release_image_cache_except_current()
        self.prefetch_images(getattr(self.controller, "_pending_editor_prefetch_paths", []))
        self.controller._log_memory_snapshot("after-apply-loaded-document")

        if snapshot.regions and snapshot.raw_mask is not None:
            self.async_service.submit_task(self.controller.inpaint_service.async_refine_and_inpaint())

    def prefetch_images(self, image_paths: list[str]) -> None:
        """后台预读相邻图片和 QImage，降低下一次切图等待。"""
        paths = [path for path in image_paths if path]
        if not paths:
            return

        executor = getattr(self.controller, "_prefetch_executor", None)
        if executor is None:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            self.controller._prefetch_executor = executor

        executor.submit(self._prefetch_images_worker, paths)

    def _prefetch_images_worker(self, image_paths: list[str]) -> None:
        for image_path in image_paths:
            try:
                _, display_image_path = self.resolve_editor_image_paths(image_path)
                resource = self.resource_manager.prefetch_image(display_image_path)
                if getattr(resource, "qimage", None) is not None:
                    continue

                from PyQt6.QtGui import QImageReader

                reader = QImageReader(resource.path)
                reader.setAutoTransform(True)
                qimage = reader.read()
                if not qimage.isNull():
                    resource.qimage = qimage
            except Exception as e:
                self.logger.debug("Editor image prefetch skipped for %s: %s", image_path, e)

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
