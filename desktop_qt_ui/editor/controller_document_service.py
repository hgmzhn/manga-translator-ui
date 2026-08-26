from __future__ import annotations

import concurrent.futures
import os
import threading
from typing import TYPE_CHECKING, Optional

from manga_translator.utils.path_manager import (
    find_json_path,
    find_work_image_path,
    resolve_original_image_path,
)
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Dialog, PushButton

from services import get_render_parameter_service

from .document_load_worker import DocumentLoadWorker
from .document_state import DocumentLoadFailure, DocumentSnapshot

if TYPE_CHECKING:
    from .editor_controller import EditorController


class EditorControllerDocumentService:
    """文档加载/清理流程。"""

    def __init__(self, controller: "EditorController"):
        self.controller = controller

        # 常驻单 worker 线程池：max_workers=1 保证加载请求严格按提交顺序执行。
        # 不随 clear_editor_state 销毁——每次切图重建线程池会打破"单 worker
        # 按序"的前提，导致旧图结果晚于新图到达（画面与选中文件错位）。
        self._load_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="editor-doc-load"
        )
        self._prefetch_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="editor-prefetch"
        )
        self._aux_load_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=DocumentLoadWorker.AUX_WORKERS,
            thread_name_prefix="editor-doc-aux",
        )
        # 加载代号：每次作废在途加载时 +1；结果只有携带当前代号才会被应用
        self._load_generation = 0
        self._active_load_future: Optional[concurrent.futures.Future] = None
        self._active_prefetch_future: Optional[concurrent.futures.Future] = None
        self._prefetch_generation = 0
        self._prefetch_lock = threading.RLock()
        self._prefetched_documents: dict[str, DocumentSnapshot] = {}
        self._desired_prefetch_keys: set[str] = set()
        self._is_shutdown = False
        self._pending_prefetch_paths: list[str] = []

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

        # Invalidate the load identity before touching presentation state so a
        # completion racing with this switch can never be installed.
        self._cancel_pending_load()
        self.model.clear_document()

        # Only cancellable editor work lives in AsyncService. Export jobs use a
        # dedicated queue and intentionally survive document switches.
        self.async_service.cancel_all_tasks()

        self.resource_manager.unload_image(release_from_cache=release_image_cache)
        toolbar = self.controller.get_toolbar()
        if toolbar is not None:
            toolbar.set_export_enabled(False)

        self.history_service.clear()
        self.history_service.mark_clean()
        self.controller._update_undo_redo_buttons()

        self.controller._log_memory_snapshot("after-clear-editor-state")

        # Normal page switches retain the rebuildable image LRU. Explicit
        # release paths already evict the current resource via unload_image.
        render_parameter_service = get_render_parameter_service()
        render_parameter_service.clear_cache()

        if release_image_cache:
            self._cancel_pending_prefetch()
            self._clear_prefetched_documents()

        self.logger.debug("Editor state cleared and memory released")

    def _cancel_pending_load(self) -> int:
        """作废所有在途加载请求，返回新的当前代号。

        已在执行的任务无法中断，靠代号校验在完成时丢弃其结果。"""
        self._load_generation += 1
        future = self._active_load_future
        if future is not None:
            future.cancel()
            self._active_load_future = None
        return self._load_generation

    def _cancel_pending_prefetch(self) -> int:
        self._prefetch_generation += 1
        future = self._active_prefetch_future
        if future is not None:
            future.cancel()
            self._active_prefetch_future = None
        return self._prefetch_generation

    @staticmethod
    def _prefetch_key(image_path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(image_path)))

    def _take_prefetched_document(
        self,
        image_path: str,
    ) -> Optional[DocumentSnapshot]:
        key = self._prefetch_key(image_path)
        with self._prefetch_lock:
            return self._prefetched_documents.pop(key, None)

    def _clear_prefetched_documents(self) -> None:
        with self._prefetch_lock:
            self._prefetched_documents.clear()
            self._desired_prefetch_keys.clear()

    def shutdown(self) -> None:
        """退出清理：取消挂起任务并关闭常驻线程池。

        wait=False + cancel_futures=True，避免 concurrent.futures 的 atexit
        钩子 join 未关闭的线程池卡住进程退出。"""
        if self._is_shutdown:
            return
        self._is_shutdown = True
        self._cancel_pending_load()
        self._cancel_pending_prefetch()
        self._clear_prefetched_documents()
        for executor in (
            self._load_executor,
            self._prefetch_executor,
            self._aux_load_executor,
        ):
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

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
            self.logger.warning(
                f"Failed to read JSON for editor_base staleness check: {e}"
            )
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
            self.logger.warning(
                f"Failed to remove stale editor_base image {work_image_path}: {e}"
            )

    def load_image_and_regions(
        self,
        image_path: str,
        *,
        prefetch_paths: list[str] | None = None,
    ) -> None:
        if self._is_shutdown:
            return
        self._pending_prefetch_paths = list(prefetch_paths or [])
        self.controller.commit_pending_edits()
        if self.controller.export_service.has_unsaved_changes():
            auto_export = self._auto_export_on_switch_enabled()
            auto_save = self._auto_save_on_switch_enabled()
            if auto_export and self.controller.export_image(automatic=True) is None:
                self.logger.warning("Auto-export rejected; image switch aborted")
                return
            if auto_save and not self.controller.save_editor_state():
                self.logger.warning("Auto-save rejected; image switch aborted")
                return
            if (
                not auto_export
                and not auto_save
                and not self._suppress_unsaved_warning_enabled()
            ):
                action = self._ask_unsaved_action()
                if action == "cancel":
                    return
                if action == "save" and not self.controller.save_editor_state():
                    self.logger.warning("Save request failed; image switch aborted")
                    return
        self.do_load_image(image_path)

    def _auto_export_on_switch_enabled(self) -> bool:
        try:
            config = self.controller.config_service.get_config()
            return bool(
                getattr(
                    getattr(config, "app", None), "editor_auto_export_on_switch", True
                )
            )
        except Exception:
            return True

    def _auto_save_on_switch_enabled(self) -> bool:
        try:
            config = self.controller.config_service.get_config()
            return bool(
                getattr(
                    getattr(config, "app", None), "editor_auto_save_on_switch", True
                )
            )
        except Exception:
            return True

    def _suppress_unsaved_warning_enabled(self) -> bool:
        try:
            config = self.controller.config_service.get_config()
            return bool(
                getattr(
                    getattr(config, "app", None),
                    "editor_suppress_unsaved_warning",
                    False,
                )
            )
        except Exception:
            return False

    def _ask_unsaved_action(self) -> str:
        """弹未保存编辑对话框，返回 save/discard/cancel。"""
        dialog_parent = (
            self.view if self.view is not None else QApplication.activeWindow()
        )
        dialog = Dialog(
            "未保存的编辑",
            "当前图片有未保存的编辑\n\n保存工程数据后再切换图片。",
            dialog_parent,
        )
        dialog.setTitleBarVisible(True)
        dialog.yesButton.setText("保存")
        dialog.cancelButton.setText("取消")
        discard_button = PushButton("不保存", dialog.buttonGroup)
        dialog.buttonLayout.insertWidget(1, discard_button, 1)
        selected_action = {"value": "save"}
        discard_button.clicked.connect(lambda: selected_action.update(value="discard"))
        discard_button.clicked.connect(dialog.accept)
        dialog.setFixedSize(max(dialog.width(), 460), max(dialog.height(), 220))
        if dialog.exec() != Dialog.DialogCode.Accepted:
            return "cancel"
        return selected_action["value"]

    def do_load_image(self, image_path: str) -> None:
        if self._is_shutdown:
            return

        # A document switch is a hard presentation boundary: invalidate the
        # old identity and remove both source/inpaint layers before loading.
        self.clear_editor_state()

        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            self.controller._loading_toast = toast_manager.show_info(
                "正在加载...", duration=0
            )

        generation = self._load_generation
        prefetched = self._take_prefetched_document(image_path)
        if prefetched is not None and prefetched.display_image_path:
            try:
                self.resource_manager.activate_prefetched_image(
                    prefetched.display_image_path,
                    prefetched.image,
                    qimage=prefetched.source_qimage,
                )
            except Exception as error:
                self.logger.debug(
                    "Discarding unusable prefetched document %s: %s",
                    image_path,
                    error,
                )
            else:
                self.controller._load_result_ready.emit((generation, prefetched))
                return

        def on_load_complete(future):
            if future.cancelled():
                return
            try:
                result = future.result()
            except Exception as e:
                self.logger.error(f"Load failed: {e}", exc_info=True)
                result = DocumentLoadFailure(str(e))
            # 工作线程侧先粗筛，避免给主线程发注定作废的信号；
            # 权威校验在主线程 apply_load_result 里再做一次
            if generation != self._load_generation:
                self.logger.debug("Discarding stale load result for %s", image_path)
                return
            self.controller._load_result_ready.emit((generation, result))

        worker = DocumentLoadWorker(self, image_path, self._aux_load_executor)
        future = self._load_executor.submit(worker.load)
        self._active_load_future = future
        future.add_done_callback(on_load_complete)

    def apply_load_result(self, payload: object) -> None:
        # 载荷为 (generation, result)：主线程权威校验"仍是当前代"，
        # 过期结果（快速翻页时旧图晚到）直接丢弃，防止画面与选中文件错位
        if not isinstance(payload, tuple) or len(payload) != 2:
            self.logger.warning("Ignoring untagged editor load result")
            return
        generation, result = payload

        if generation != self._load_generation:
            self.logger.debug(
                "Ignoring stale load result (generation %s, current %s)",
                generation,
                self._load_generation,
            )
            return

        if self._active_load_future is not None and self._active_load_future.done():
            self._active_load_future = None

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

        self.model.apply_document_snapshot(snapshot)
        self.controller.inpaint_service.ensure_current_mask_inpaint()

        self.resource_manager.release_image_cache_except_current()

        prefetch_paths = self._pending_prefetch_paths
        self._pending_prefetch_paths = []
        self.prefetch_images(prefetch_paths)
        self.controller._log_memory_snapshot("after-apply-loaded-document")

    def prefetch_images(self, image_paths: list[str]) -> None:
        """后台加载邻页完整文档快照，切图时直接安装。"""
        if self._is_shutdown:
            return

        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        for path in image_paths:
            if not path:
                continue
            key = self._prefetch_key(path)
            if key in seen:
                continue
            seen.add(key)
            ordered.append((key, path))

        generation = self._cancel_pending_prefetch()
        with self._prefetch_lock:
            self._desired_prefetch_keys = {key for key, _ in ordered}
            self._prefetched_documents = {
                key: snapshot
                for key, snapshot in self._prefetched_documents.items()
                if key in self._desired_prefetch_keys
            }
        if not ordered:
            return

        self._active_prefetch_future = self._prefetch_executor.submit(
            self._prefetch_documents_worker,
            ordered,
            generation,
        )

    def _prefetch_documents_worker(
        self,
        documents: list[tuple[str, str]],
        generation: int,
    ) -> None:
        for key, image_path in documents:
            if self._is_shutdown or generation != self._prefetch_generation:
                return
            with self._prefetch_lock:
                if key in self._prefetched_documents:
                    continue
            result = DocumentLoadWorker(
                self,
                image_path,
                self._aux_load_executor,
                prefetch=True,
            ).load()
            if not isinstance(result, DocumentSnapshot):
                self.logger.debug(
                    "Editor document prefetch skipped for %s: %s",
                    image_path,
                    getattr(result, "error", "unsupported result"),
                )
                continue
            if self._is_shutdown or generation != self._prefetch_generation:
                return
            with self._prefetch_lock:
                if key in self._desired_prefetch_keys:
                    self._prefetched_documents[key] = result

    def handle_load_error(self, error_msg: str) -> None:
        loading_toast = getattr(self.controller, "_loading_toast", None)
        if loading_toast is not None:
            loading_toast.close()
            self.controller._loading_toast = None

        toast_manager = self.controller.get_toast_manager()
        if toast_manager is not None:
            toast_manager.show_error(f"加载失败: {error_msg}")

        self.controller._log_memory_snapshot("after-load-error")
