import copy
import json
import os
from typing import Optional

import numpy as np
from editor.commands import UpdateRegionCommand
from editor.geometry_commit_pipeline import build_rotate_region_data
from editor.region_geometry_state import RegionGeometryState
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from services import (
    get_async_service,
    get_config_service,
    get_file_service,
    get_history_service,
    get_i18n_manager,
    get_logger,
    get_ocr_service,
    get_resource_manager,
    get_translation_service,
)

from .image_utils import copy_image_like, image_like_to_display_array
from .controller_document_service import EditorControllerDocumentService
from .controller_export_service import EditorControllerExportService
from .controller_inpaint_service import EditorControllerInpaintService
from .editor_model import EditorModel
from .session import DocumentSnapshot

_UNSET = object()


# 改变这些字段会影响字号反算的文字像素尺寸，需要同步刷新白框：
# 保持白框中心不变，把宽高更新为 calc_box_from_font(新参数) 的结果。
_FONT_AFFECTING_FIELDS = frozenset({
    "translation", "text", "font_size", "font_path",
    "letter_spacing", "line_spacing", "direction",
    "stroke_width", "text_stroke_width",
})


def _sync_white_frame_size_for_font_change(region_data: dict) -> None:
    """字体/译文/描边/字间距等属性改变后，把白框尺寸同步成字号反算尺寸。

    保持白框中心不变；尺寸 = calc_box_from_font(font_size, translation, ...)。
    标记 has_custom_white_frame=True 让其优先于 render_box 主导渲染中心。
    """
    try:
        from manga_translator.rendering import calc_box_from_font

        font_size = int(region_data.get("font_size") or 0)
        translation = (region_data.get("translation") or "").strip()
        if font_size <= 0 or not translation:
            return

        direction = region_data.get("direction", "h")
        is_horizontal = direction in ("h", "horizontal", "hr")
        line_spacing = float(region_data.get("line_spacing") or 1.0)
        letter_spacing = float(region_data.get("letter_spacing") or 1.0)

        w, h, _ = calc_box_from_font(
            font_size, translation, is_horizontal, line_spacing,
            None, None, center=None, angle=0, letter_spacing=letter_spacing,
        )
        if w <= 0 or h <= 0:
            return

        wf = region_data.get("white_frame_rect_local")
        if isinstance(wf, (list, tuple)) and len(wf) == 4:
            local_cx = (float(wf[0]) + float(wf[2])) / 2.0
            local_cy = (float(wf[1]) + float(wf[3])) / 2.0
        else:
            local_cx = local_cy = 0.0

        half_w, half_h = float(w) / 2.0, float(h) / 2.0
        region_data["white_frame_rect_local"] = [
            local_cx - half_w, local_cy - half_h,
            local_cx + half_w, local_cy + half_h,
        ]
        region_data["has_custom_white_frame"] = True
    except Exception:
        return


class EditorController(QObject):
    """
    编辑器控制器 (Controller)

    负责处理编辑器的所有业务逻辑和用户交互。
    它响应来自视图(View)的信号，调用服务(Service)执行任务，并更新模型(Model)。
    """
    # Signal for thread-safe model updates
    _update_refined_mask = pyqtSignal(object)
    _update_display_mask_type = pyqtSignal(str)
    _regions_update_finished = pyqtSignal(list)
    _ocr_finished = pyqtSignal(str, str)
    _translation_finished = pyqtSignal(str, str)
    
    # Signal for thread-safe Toast notifications
    _show_toast_signal = pyqtSignal(str, int, bool, str)  # message, duration, success, clickable_path
    
    # Signal for thread-safe image loading
    _load_result_ready = pyqtSignal(object)  # 加载结果信号
    _deferred_load_requested = pyqtSignal(str)

    def __init__(self, model: EditorModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.view = None  # 将在 EditorView 中设置
        self.logger = get_logger(__name__)

        # 获取所需的服务
        self.ocr_service = get_ocr_service()
        self.translation_service = get_translation_service()
        self.async_service = get_async_service()
        self.history_service = get_history_service() # 用于撤销/重做
        self.file_service = get_file_service()
        self.config_service = get_config_service()
        self.resource_manager = get_resource_manager()  # 新的资源管理器

        # 缓存键常量
        self.CACHE_LAST_INPAINTED = "last_inpainted_image"
        self.CACHE_LAST_MASK = "last_processed_mask"
        self.WEAK_CACHE_BASE_IMAGE_RGB = "weak_base_image_rgb"
        
        # 用户透明度调整标志
        self._user_adjusted_alpha = False
        
        # 上次导出时的状态快照（用于检测是否有更改）
        self._last_export_snapshot = None

        # 只允许最新一笔/最新一次蒙版变更写回修复结果。
        self._active_inpaint_future = None
        self._inpaint_request_generation = 0
        self._suppress_refined_mask_autoinpaint = False

        self.document_service = EditorControllerDocumentService(self)
        self.inpaint_service = EditorControllerInpaintService(self)
        self.export_service = EditorControllerExportService(self)

        # Connect internal signals for thread-safe updates
        self._update_refined_mask.connect(self.model.set_refined_mask)
        self._update_display_mask_type.connect(self.model.set_display_mask_type)
        self._regions_update_finished.connect(self.on_regions_update_finished)
        self._ocr_finished.connect(self._on_ocr_finished)
        self._translation_finished.connect(self._on_translation_finished)
        self._load_result_ready.connect(self._apply_load_result)  # 连接加载结果信号
        self._deferred_load_requested.connect(self.document_service.do_load_image)
        
        self._connect_model_signals()
        self.history_service.undo_redo_state_changed.connect(self._on_history_undo_redo_state_changed)
    
    # ========== Resource Access Helpers (新的资源访问辅助方法) ==========
    
    def _get_current_image(self) -> Optional[Image.Image]:
        """获取当前图片（PIL Image）
        
        优先从 Session/Model 获取，如果失败再回退到 ResourceManager。
        """
        image = self.model.get_image()
        if image is not None:
            return image
        resource = self.resource_manager.get_current_image()
        if resource:
            return resource.image
        return None

    @staticmethod
    def _normalize_binary_mask(mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        return EditorControllerInpaintService.normalize_binary_mask(mask)

    def _get_cached_mask_snapshot(self) -> Optional[np.ndarray]:
        return self.inpaint_service.get_cached_mask_snapshot()

    def _get_cached_inpainted_snapshot(self) -> Optional[np.ndarray]:
        return self.inpaint_service.get_cached_inpainted_snapshot()

    def _get_base_image_rgb_array(self) -> Optional[np.ndarray]:
        return self.inpaint_service.get_base_image_rgb_array()

    def _cancel_active_inpaint_task(self) -> None:
        self.inpaint_service.cancel_active_inpaint_task()

    def _invalidate_inpaint_requests(self) -> None:
        self.inpaint_service.invalidate_inpaint_requests()

    def _begin_inpaint_request(self) -> int:
        return self.inpaint_service.begin_inpaint_request()

    def _is_inpaint_request_current(self, generation: int) -> bool:
        return self.inpaint_service.is_inpaint_request_current(generation)
    
    @staticmethod
    def _normalize_image_path(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        return os.path.normcase(os.path.normpath(path))

    def _is_same_source_image(self, left: Optional[str], right: Optional[str]) -> bool:
        left_path = self._normalize_image_path(left)
        right_path = self._normalize_image_path(right)
        return bool(left_path and right_path and left_path == right_path)

    def _snapshot_image_for_export(self, image_obj, label: str):
        """为导出创建独立快照，避免切图时原图/数组被后续编辑覆盖。"""
        if image_obj is None:
            return None
        try:
            return copy_image_like(image_obj)
        except Exception as e:
            self.logger.error(f"Failed to snapshot {label} for export: {e}", exc_info=True)
            raise

    def _load_detached_image_array(self, image_path: str, target_size: tuple[int, int]) -> np.ndarray:
        """加载辅助图并直接归一化为 numpy，避免 PIL/ndarray 双持有。"""
        detached_image = self.resource_manager.load_detached_image(image_path)
        resized_image = detached_image
        try:
            if detached_image.size != target_size:
                resized_image = detached_image.resize(target_size, Image.Resampling.LANCZOS)
            return image_like_to_display_array(resized_image, copy=False)
        finally:
            if resized_image is not detached_image:
                try:
                    resized_image.close()
                except Exception:
                    pass
            try:
                detached_image.close()
            except Exception:
                pass

    def _log_memory_snapshot(self, stage: str) -> None:
        try:
            self.resource_manager.log_memory_snapshot(stage, logger=self.logger)
        except Exception as e:
            self.logger.debug(f"Failed to log memory snapshot at {stage}: {e}")
    
    def _get_regions(self):
        """获取所有区域
        
        Returns:
            List[Dict]: 区域列表
        """
        return self.model.get_regions()
    
    def _set_regions(self, regions: list):
        """设置所有区域
        
        Args:
            regions: 区域列表
        """
        # Model now handles synchronization with ResourceManager
        self.model.set_regions(regions)
    
    def _get_region_by_index(self, index: int):
        """根据索引获取区域
        
        Args:
            index: 区域索引
        
        Returns:
            Dict: 区域数据，如果不存在返回None
        """
        regions = self._get_regions()
        if 0 <= index < len(regions):
            return regions[index]
        return None

    def _merge_live_geometry_state(self, region_index: int, region_data: dict) -> dict:
        """为样式类更新保留当前 item 的合法持久化几何状态。"""
        if not isinstance(region_data, dict):
            return region_data

        try:
            gv = self.get_graphics_view()
            if gv is None:
                return region_data

            live_patch = gv.get_live_region_state_patch(region_index)
            if not live_patch:
                return region_data

            merged_region_data = copy.deepcopy(region_data)
            merged_region_data.update(live_patch)
            return merged_region_data
        except Exception:
            return region_data

    @staticmethod
    def _normalize_region_identity_value(value):
        if isinstance(value, float):
            return round(value, 4)
        if isinstance(value, list):
            return [EditorController._normalize_region_identity_value(item) for item in value]
        if isinstance(value, tuple):
            return [EditorController._normalize_region_identity_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: EditorController._normalize_region_identity_value(val)
                for key, val in sorted(value.items())
            }
        return value

    @classmethod
    def _build_region_identity(cls, region_data: dict) -> str:
        if not isinstance(region_data, dict):
            return ""

        payload = {
            "center": cls._normalize_region_identity_value(region_data.get("center")),
            "lines": cls._normalize_region_identity_value(region_data.get("lines")),
            "angle": cls._normalize_region_identity_value(region_data.get("angle", 0)),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _resolve_region_update_index(
        self,
        regions: list[dict],
        preferred_index: int,
        region_identity: str,
    ) -> int | None:
        if 0 <= preferred_index < len(regions):
            if self._build_region_identity(regions[preferred_index]) == region_identity:
                return preferred_index

        matches = [
            index
            for index, region in enumerate(regions)
            if self._build_region_identity(region) == region_identity
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _apply_async_region_updates(
        self,
        updates: list[tuple[int, str, str]],
        *,
        field_name: str,
        request_revision: int,
        source_image_path: Optional[str],
    ) -> tuple[int, int, bool]:
        if not updates:
            return 0, 0, False

        start_path = self._normalize_image_path(source_image_path)
        current_path = self._normalize_image_path(self.model.get_source_image_path())
        if start_path != current_path:
            return 0, len(updates), True

        current_regions = self.model.get_regions()
        updated_regions = list(current_regions)
        document_changed = self.model.get_document_revision() != request_revision

        applied_count = 0
        skipped_count = 0
        for preferred_index, region_identity, value in updates:
            target_index = self._resolve_region_update_index(updated_regions, preferred_index, region_identity)
            if target_index is None:
                skipped_count += 1
                continue

            new_region_data = updated_regions[target_index].copy()
            new_region_data[field_name] = value
            updated_regions[target_index] = new_region_data
            applied_count += 1

        if applied_count > 0:
            self._regions_update_finished.emit(updated_regions)
        return applied_count, skipped_count, document_changed

    def _finalize_progress_toast(self, toast_attr: str, status: str, message: str) -> None:
        toast = getattr(self, toast_attr, None)
        if toast is not None:
            try:
                toast.close()
            except Exception:
                pass
            setattr(self, toast_attr, None)

        toast_manager = getattr(self, "toast_manager", None)
        if toast_manager is None or not message:
            return

        if status == "success":
            toast_manager.show_success(message)
        elif status == "error":
            toast_manager.show_error(message)
        else:
            toast_manager.show_info(message)

    def get_graphics_view(self):
        return getattr(self.view, "graphics_view", None) if self.view else None

    def get_property_panel(self):
        return getattr(self.view, "property_panel", None) if self.view else None

    def get_toolbar(self):
        return getattr(self.view, "toolbar", None) if self.view else None

    def get_toast_manager(self):
        return getattr(self, "toast_manager", None)

    def set_compare_mode(self, enabled: bool) -> None:
        if self.view is None:
            return
        set_compare_mode = getattr(self.view, "set_compare_mode", None)
        if callable(set_compare_mode):
            set_compare_mode(enabled)

    def set_view(self, view):
        """设置view引用，用于更新UI状态"""
        self.view = view
        graphics_view = self.get_graphics_view()
        if graphics_view is not None:
            graphics_view.set_controller(self)
        # 初始化Toast管理器
        from desktop_qt_ui.widgets.toast_notification import ToastManager
        self.toast_manager = ToastManager(view)
        # 连接Toast信号到主线程槽函数
        self._show_toast_signal.connect(self._show_toast_in_main_thread)
        # 初始化撤销/重做按钮状态
        self._update_undo_redo_buttons()
    
    @pyqtSlot(str, int, bool, str)
    def _show_toast_in_main_thread(self, message: str, duration: int, success: bool, clickable_path: str):
        """在主线程显示Toast通知的槽函数"""
        try:
            # 先关闭"正在导出"Toast（在主线程中安全关闭）
            if hasattr(self, '_export_toast') and self._export_toast:
                try:
                    self._export_toast.close()
                    self._export_toast = None
                except Exception as e:
                    self.logger.warning(f"Failed to close export toast: {e}")

            # 显示新Toast
            toast_manager = self.get_toast_manager()
            if toast_manager is not None:
                if success:
                    toast_manager.show_success(message, duration, clickable_path if clickable_path else None)
                else:
                    toast_manager.show_error(message, duration)
        except Exception as e:
            self.logger.error(f"Exception in _show_toast_in_main_thread: {e}", exc_info=True)

    def _connect_model_signals(self):
        """监听模型的变化，可能需要触发一些后续逻辑"""
        self.model.regions_changed.connect(self.on_regions_changed)
        # 监听蒙版编辑后触发 inpainting
        self.model.refined_mask_changed.connect(self.on_refined_mask_changed)

    def on_regions_changed(self, regions):
        """模型中的区域数据变化时的槽函数"""
        pass

    def on_refined_mask_changed(self, mask):
        self.inpaint_service.on_refined_mask_changed(mask)

    @pyqtSlot(dict)
    def update_multiple_translations(self, translations: dict):
        """
        批量更新多个区域的译文。
        `translations` 是一个 {index: text} 格式的字典。
        """
        if not translations:
            return

        commands = []
        for raw_index, text in translations.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue

            old_region_data = self._get_region_by_index(index)
            if not old_region_data:
                continue

            old_region_data = self._merge_live_geometry_state(index, old_region_data)
            if old_region_data.get("translation", "") == text:
                continue

            new_region_data = old_region_data.copy()
            new_region_data["translation"] = text
            # 译文批量更新时同步覆盖 translation_raw(规则不可逆,只能粗暴同步)
            new_region_data["translation_raw"] = text
            commands.append(
                UpdateRegionCommand(
                    model=self.model,
                    region_index=index,
                    old_data=old_region_data,
                    new_data=new_region_data,
                    description=f"Batch Update Translation Region {index}",
                    merge_key=f"region:{index}:translation",
                )
            )

        if not commands:
            return

        self._execute_command_batch(commands, f"Batch Update Translations ({len(commands)})")

    def _generate_export_snapshot(self) -> dict:
        return self.export_service.generate_export_snapshot()
    
    def _has_changes_since_last_export(self) -> bool:
        return self.export_service.has_changes_since_last_export()
    
    def _save_export_snapshot(self):
        self.export_service.save_export_snapshot()

    def _clear_editor_state(self, release_image_cache: bool = False):
        self.document_service.clear_editor_state(release_image_cache=release_image_cache)

    def _find_source_from_translation_map(self, image_path: str) -> Optional[str]:
        return self.document_service.find_source_from_translation_map(image_path)

    def _resolve_editor_image_paths(self, image_path: str) -> tuple[str, str]:
        return self.document_service.resolve_editor_image_paths(image_path)

    def load_image_and_regions(self, image_path: str):
        self.document_service.load_image_and_regions(image_path)
    
    def _do_load_image(self, image_path: str):
        self.document_service.do_load_image(image_path)
    
    @pyqtSlot(object)
    def _apply_load_result(self, result: object):
        self.document_service.apply_load_result(result)
    
    def _apply_loaded_data_to_model(self, snapshot: DocumentSnapshot):
        self.document_service.apply_loaded_data_to_model(snapshot)
    
    def _handle_load_error(self, error_msg: str):
        self.document_service.handle_load_error(error_msg)

    async def _async_refine_and_inpaint(self):
        return await self.inpaint_service.async_refine_and_inpaint()

    async def _async_incremental_inpaint(self, current_mask, generation: int):
        return await self.inpaint_service.async_incremental_inpaint(current_mask, generation)

    async def _async_full_inpaint_with_cache(self, mask, generation: int):
        return await self.inpaint_service.async_full_inpaint_with_cache(mask, generation)

    def force_inpaint_stroke(self, stroke_mask: np.ndarray):
        self.inpaint_service.force_inpaint_stroke(stroke_mask)

    @pyqtSlot(str, bool)
    def set_display_mask_type(self, mask_type: str, visible: bool):
        self.inpaint_service.set_display_mask_type(mask_type, visible)

    @pyqtSlot(str)
    def set_active_tool(self, tool: str):
        self.inpaint_service.set_active_tool(tool)

    @pyqtSlot(int)
    def set_brush_size(self, size: int):
        self.inpaint_service.set_brush_size(size)

    @pyqtSlot(str)
    def set_brush_color(self, color: str):
        self.inpaint_service.set_brush_color(color)

    @pyqtSlot()
    def clear_all_masks(self):
        self.inpaint_service.clear_all_masks()

    @pyqtSlot()
    def clear_paint_overlay(self):
        self.inpaint_service.clear_paint_overlay()

    def _build_region_update_command(
        self,
        *,
        region_index: int,
        old_data: dict,
        new_data: dict,
        description: str,
        merge_key: str,
    ) -> UpdateRegionCommand:
        return UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_data,
            new_data=new_data,
            description=description,
            merge_key=merge_key,
        )

    def _update_region_field(
        self,
        region_index: int,
        field_name: str,
        value,
        *,
        description: str,
        merge_key: str | None = None,
        merge_live_geometry: bool = True,
        current_value=_UNSET,
    ) -> bool:
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return False

        if merge_live_geometry:
            old_region_data = self._merge_live_geometry_state(region_index, old_region_data)

        existing_value = old_region_data.get(field_name) if current_value is _UNSET else current_value
        if existing_value == value:
            return False

        new_region_data = old_region_data.copy()
        new_region_data[field_name] = value

        # 字体/译文等属性改变 → 同步白框尺寸（保持白框中心），让 UI 立即跟上新字号。
        if field_name in _FONT_AFFECTING_FIELDS:
            _sync_white_frame_size_for_font_change(new_region_data)

        command = self._build_region_update_command(
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=description,
            merge_key=merge_key or f"region:{region_index}:{field_name}",
        )
        self.execute_command(command)
        return True

    @staticmethod
    def _normalize_font_path(font_filename: str) -> str:
        from manga_translator.utils import BASE_PATH

        if not font_filename:
            return ""

        if os.path.isabs(font_filename):
            norm_path = os.path.normpath(font_filename)
            base_path = os.path.normpath(BASE_PATH)
            fonts_dir = os.path.normpath(os.path.join(base_path, "fonts"))
            try:
                if os.path.commonpath([norm_path, fonts_dir]) == fonts_dir:
                    return os.path.relpath(norm_path, base_path).replace("\\", "/")
                if os.path.commonpath([norm_path, base_path]) == base_path:
                    return os.path.relpath(norm_path, base_path).replace("\\", "/")
            except ValueError:
                return norm_path
            return norm_path

        if font_filename.lower().startswith("fonts/") or font_filename.lower().startswith("fonts\\"):
            return font_filename.replace("\\", "/")
        return f"fonts/{font_filename}".replace("\\", "/")

    @staticmethod
    def _normalize_alignment_value(alignment_text: str) -> str:
        raw_text = str(alignment_text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in ("auto", "left", "center", "right"):
            return lower_text

        i18n = get_i18n_manager()
        if i18n:
            localized_map = {
                i18n.translate("alignment_auto"): "auto",
                i18n.translate("alignment_left"): "left",
                i18n.translate("alignment_center"): "center",
                i18n.translate("alignment_right"): "right",
            }
            mapped = localized_map.get(raw_text)
            if mapped is not None:
                return mapped

        fallback_map = {"自动": "auto", "左对齐": "left", "居中": "center", "右对齐": "right"}
        return fallback_map.get(raw_text, "auto")

    @staticmethod
    def _normalize_direction_value(direction_text: str) -> str:
        raw_text = str(direction_text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in ("v", "vertical"):
            return "vertical"
        if lower_text in ("h", "horizontal"):
            return "horizontal"

        i18n = get_i18n_manager()
        if i18n:
            horizontal_label = i18n.translate("direction_horizontal")
            vertical_label = i18n.translate("direction_vertical")
            if raw_text == vertical_label:
                return "vertical"
            if raw_text == horizontal_label:
                return "horizontal"

        if raw_text in ("竖排",):
            return "vertical"
        if raw_text in ("横排",):
            return "horizontal"
        return "horizontal"

    @pyqtSlot(int, str)
    def update_translated_text(self, region_index: int, text: str):
        # 译文编辑:同步覆盖 translation_raw(规则不可逆,只能粗暴同步)
        self._update_translation_pair(
            region_index,
            translation=text,
            translation_raw=text,
            description=f"Update Translation Region {region_index}",
            merge_key=f"region:{region_index}:translation",
        )

    @pyqtSlot(int, str)
    def update_translation_raw(self, region_index: int, raw_text: str):
        """编辑替换前译文:实时跑 apply_replacements 同步到 translation 字段。"""
        from manga_translator.rendering.text_replacements import apply_replacements

        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        # 推 direction(参考 L57: ('h','horizontal','hr') 为横排,其它视为竖排)
        direction_val = old_region_data.get("direction", "h")
        direction = 0 if direction_val in ("h", "horizontal", "hr") else 1
        try:
            new_translation = apply_replacements(raw_text, direction)
        except Exception as e:
            # 替换规则编译失败等,回退用原文
            self.logger.warning(f"apply_replacements failed for region {region_index}: {e}")
            new_translation = raw_text

        self._update_translation_pair(
            region_index,
            translation=new_translation,
            translation_raw=raw_text,
            description=f"Update Translation Raw Region {region_index}",
            merge_key=f"region:{region_index}:translation_raw",
        )

    def _update_translation_pair(
        self,
        region_index: int,
        *,
        translation: str,
        translation_raw: str,
        description: str,
        merge_key: str,
    ) -> bool:
        """同时更新 translation 和 translation_raw,共用一个 Undo Command(撤销时一起回滚)。"""
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return False

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)

        if (old_region_data.get("translation", "") == translation
                and old_region_data.get("translation_raw", "") == translation_raw):
            return False

        new_region_data = old_region_data.copy()
        new_region_data["translation"] = translation
        new_region_data["translation_raw"] = translation_raw

        # translation 是 _FONT_AFFECTING_FIELDS 成员,改动后同步白框尺寸
        _sync_white_frame_size_for_font_change(new_region_data)

        command = self._build_region_update_command(
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=description,
            merge_key=merge_key,
        )
        self.execute_command(command)
        return True

    @pyqtSlot(int, str)
    def update_original_text(self, region_index: int, text: str):
        self._update_region_field(
            region_index,
            "text",
            text,
            description=f"Update Original Text Region {region_index}",
        )

    @pyqtSlot(int, int)
    def update_font_size(self, region_index: int, size: int):
        self._update_region_field(
            region_index,
            "font_size",
            size,
            description=f"Update Font Size Region {region_index}",
        )

    @pyqtSlot(int, str)
    def update_font_color(self, region_index: int, color: str):
        self._update_region_field(
            region_index,
            "font_color",
            color,
            description=f"Update Font Color Region {region_index}",
        )

    @pyqtSlot(int, str)
    def update_stroke_color(self, region_index: int, hex_color: str):
        from PyQt6.QtGui import QColor
        c = QColor(hex_color)
        new_bg_colors = [c.red(), c.green(), c.blue()]
        self._update_region_field(
            region_index,
            "bg_colors",
            new_bg_colors,
            description=f"Update Stroke Color Region {region_index}",
        )

    @pyqtSlot(int, float)
    def update_stroke_width(self, region_index: int, value: float):
        self._update_region_field(
            region_index,
            "stroke_width",
            value,
            description=f"Update Stroke Width Region {region_index}",
        )

    @pyqtSlot(int, float)
    def update_line_spacing(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return
        current_value = old_region_data.get("line_spacing")
        if current_value is None:
            current_value = self.config_service.get_config().render.line_spacing or 1.0
        self._update_region_field(
            region_index,
            "line_spacing",
            value,
            description=f"Update Line Spacing Region {region_index}",
            current_value=current_value,
        )

    @pyqtSlot(int, float)
    def update_letter_spacing(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return
        current_value = old_region_data.get("letter_spacing")
        if current_value is None:
            current_value = self.config_service.get_config().render.letter_spacing or 1.0
        self._update_region_field(
            region_index,
            "letter_spacing",
            value,
            description=f"Update Letter Spacing Region {region_index}",
            current_value=current_value,
        )

    @pyqtSlot(int, float)
    def update_angle(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        target_angle = float(value)
        current_angle = float(old_region_data.get("angle", 0.0) or 0.0)
        if np.isclose(current_angle, target_angle, atol=1e-6):
            return

        geo = RegionGeometryState.from_region_data(old_region_data)
        wf_local = geo.white_frame_local
        if wf_local is not None and len(wf_local) == 4:
            left, top, right, bottom = wf_local
            pivot_lx = (left + right) / 2.0
            pivot_ly = (top + bottom) / 2.0
        else:
            pivot_lx = 0.0
            pivot_ly = 0.0

        pivot_scene_x, pivot_scene_y = geo.local_to_world(pivot_lx, pivot_ly)
        theta = np.radians(target_angle)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        new_cx = pivot_scene_x - (pivot_lx * cos_t - pivot_ly * sin_t)
        new_cy = pivot_scene_y - (pivot_lx * sin_t + pivot_ly * cos_t)

        old_center = geo.center if len(geo.center) >= 2 else [new_cx, new_cy]
        delta_x = float(new_cx) - float(old_center[0])
        delta_y = float(new_cy) - float(old_center[1])

        new_lines = []
        for poly in old_region_data.get("lines", []):
            new_poly = []
            for point in poly:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    new_poly.append([float(point[0]) + delta_x, float(point[1]) + delta_y])
            if new_poly:
                new_lines.append(new_poly)

        new_region_data = build_rotate_region_data(
            old_region_data,
            target_angle,
            new_center=[new_cx, new_cy],
            new_lines=new_lines or None,
        )
        command = self._build_region_update_command(
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Rotation Region {region_index}",
            merge_key=f"region:{region_index}:angle",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_font_family(self, region_index: int, font_filename: str):
        font_path = self._normalize_font_path(font_filename)
        self._update_region_field(
            region_index,
            "font_path",
            font_path,
            description=f"Update Font Family Region {region_index}",
        )

    @pyqtSlot(int, str)
    def update_alignment(self, region_index: int, alignment_text: str):
        alignment_value = self._normalize_alignment_value(alignment_text)
        self._update_region_field(
            region_index,
            "alignment",
            alignment_value,
            description=f"Update Alignment to {alignment_value}",
        )

    @pyqtSlot(int, dict)
    def update_region_geometry(self, region_index: int, new_region_data: dict):
        """处理来自视图的区域几���变化。"""
        # 现在RegionTextItem在调用callback之前不会修改self.region_data
        # 所以我们可以从模型中获取正确的旧数据
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return
            
        # 深拷贝以避免引用问题
        old_region_data = copy.deepcopy(old_region_data)

        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Resize/Move/Rotate Region {region_index}"
        )
        self.execute_command(command)

    # ------------------------------------------------------------------
    # 对齐与分布
    # ------------------------------------------------------------------

    def align_regions(self, mode: str, reference: str) -> None:
        """批量对齐选中的区域。

        mode: top / vertical_center / bottom / left / horizontal_center / right
        reference: "selection" | "canvas"
        """
        from .alignment_service import align_items

        view = self.get_graphics_view()
        if view is None:
            return
        items = [item for item in view._region_items if item.isSelected()]
        if not items:
            return

        canvas_rect = None
        if reference == "canvas":
            r = view.get_image_scene_rect()
            if r is not None:
                canvas_rect = (r.left(), r.top(), r.right(), r.bottom())

        results = align_items(items, mode, reference, canvas_rect)
        if not results:
            return

        # 构建单条批量命令：修改 model center + 同��移动 item 白框和文字
        regions = self.model.get_regions()
        old_regions = [dict(r) for r in regions]
        new_regions = [dict(r) for r in regions]
        for idx, new_cx, new_cy in results:
            new_regions[idx]["center"] = [new_cx, new_cy]

        from .commands import MultiRegionUpdateCommand
        mode_names = {
            "top": "Top Align", "vertical_center": "Vertical Center",
            "bottom": "Bottom Align", "left": "Left Align",
            "horizontal_center": "Horizontal Center", "right": "Right Align",
        }
        cmd = MultiRegionUpdateCommand(self.model, old_regions, new_regions,
                                       description=f"{mode_names.get(mode, 'Align')} ({reference})")
        self.execute_command(cmd)

        # 不依赖 debounce → 异步重建，仿照拖拽逻辑立刻移动 item 的白框和文字
        self._sync_items_positions(results, items)

    def _sync_items_positions(self, results, items):
        """对齐后即时同步 item.center 到新位置（只动 center，不动 wf_local）。

        白框在本地坐标相对 center 不变，只改 center 让整个 item 移到目标位置。
        模型 center 已由 MultiRegionUpdateCommand 更新，这里仅刷新 Qt item 视觉。
        """
        from PyQt6.QtCore import QPointF

        for idx, new_cx, new_cy in results:
            item = None
            for it in items:
                if it.region_index == idx:
                    item = it
                    break
            if item is None or item.scene() is None:
                continue

            old_pos = item.pos()
            dx = new_cx - float(old_pos.x())
            dy = new_cy - float(old_pos.y())
            if abs(dx) < 0.01 and abs(dy) < 0.01:
                continue

            old_rect = item.sceneBoundingRect()
            item.prepareGeometryChange()
            item._shape_path = None
            item.geo.center = [new_cx, new_cy]
            item.visual_center = QPointF(new_cx, new_cy)
            item.setPos(new_cx, new_cy)
            item.update()
            item._invalidate_scene_rect(old_rect)

    def distribute_regions(self, mode: str) -> None:
        """批量均分选中区域的间距。

        mode: top / vertical_center / bottom / left / horizontal_center / right
        """
        view = self.get_graphics_view()
        if view is None:
            return
        items = [item for item in view._region_items if item.isSelected()]

        # 间距分布 vs 边缘分布
        if mode in ("spacing_v", "spacing_h"):
            if len(items) < 3:
                return
            from .alignment_service import distribute_spacing_items
            orientation = "vertical" if mode == "spacing_v" else "horizontal"
            results = distribute_spacing_items(items, orientation)
            desc = "Distribute Spacing V" if mode == "spacing_v" else "Distribute Spacing H"
        else:
            from .alignment_service import distribute_items
            if len(items) < 3:
                return
            results = distribute_items(items, mode)
            mode_names = {
                "top": "Top Distribute", "vertical_center": "Vertical Center Distribute",
                "bottom": "Bottom Distribute", "left": "Left Distribute",
                "horizontal_center": "Horizontal Center Distribute", "right": "Right Distribute",
            }
            desc = mode_names.get(mode, "Distribute")

        if not results:
            return

        regions = self.model.get_regions()
        old_regions = [dict(r) for r in regions]
        new_regions = [dict(r) for r in regions]
        for idx, new_cx, new_cy in results:
            new_regions[idx]["center"] = [new_cx, new_cy]

        from .commands import MultiRegionUpdateCommand
        cmd = MultiRegionUpdateCommand(self.model, old_regions, new_regions, description=desc)
        self.execute_command(cmd)

        self._sync_items_positions(results, items)

    @pyqtSlot(int, str)
    def update_direction(self, region_index: int, direction_text: str):
        direction_value = self._normalize_direction_value(direction_text)
        self._update_region_field(
            region_index,
            "direction",
            direction_value,
            description=f"Update Direction to {direction_value}",
        )

    def _execute_command_batch(self, commands: list, macro_name: str) -> None:
        with self.history_service.macro(macro_name):
            for command in commands:
                self.execute_command(command, update_ui=False)
        self._update_undo_redo_buttons()

    def execute_command(self, command, update_ui: bool = True):
        """执行命令并更新UI - 使用 Qt 的 QUndoStack"""
        if command is None:
            return
        self.history_service.execute(command)
        if update_ui:
            self._update_undo_redo_buttons()

    def undo(self):
        """撤销操作 - 使用 Qt 的 QUndoStack"""
        self.history_service.undo()
        self._update_undo_redo_buttons()

    def redo(self):
        """重做操作 - 使用 Qt 的 QUndoStack"""
        self.history_service.redo()
        self._update_undo_redo_buttons()

    # --- 右键菜单相关方法 ---
    def ocr_regions(self, region_indices: list):
        """对指定区域进行OCR识别，使用与UI按钮相同的逻辑"""
        if not region_indices:
            return
        
        # 临时保存当前选择
        original_selection = self.model.get_selection()
        
        # 设置选择为要OCR的区域
        self.model.set_selection(region_indices)
        
        # 调用现有的OCR方法（这会使用UI配置的OCR模型）
        self.run_ocr_for_selection()
        
        # 恢复原始选择
        self.model.set_selection(original_selection)

    def translate_regions(self, region_indices: list):
        """翻译指定区域的文本，使用与UI按钮相同的逻辑"""
        if not region_indices:
            return
        
        # 临时保存当前选择
        original_selection = self.model.get_selection()
        
        # 设置选择为要翻译的区域
        self.model.set_selection(region_indices)
        
        # 调用现有的翻译方法（这会使用UI配置的翻译器和目标语言）
        self.run_translation_for_selection()
        
        # 恢复原始选择
        self.model.set_selection(original_selection)

    def copy_region(self, region_index: int):
        """复制指定区域的数据"""
        region_data = self.model.get_region_by_index(region_index)
        if not region_data:
            self.logger.error(f"区域 {region_index} 不存在")
            return

        # 将区域数据保存到历史服务的剪贴板
        self.history_service.copy_to_clipboard(copy.deepcopy(region_data))

    def paste_region_style(self, region_index: int):
        """将复制的样式粘贴到指定区域"""
        clipboard_data = self.history_service.paste_from_clipboard()
        if not clipboard_data:
            self.logger.warning("没有复制的区域数据")
            return
        
        region_data = self.model.get_region_by_index(region_index)
        if not region_data:
            self.logger.error(f"区域 {region_index} 不存在")
            return
        
        # 复制样式相关属性，但保留位置和文本
        old_region_data = region_data.copy()
        new_region_data = region_data.copy()
        
        # 复制样式属性
        style_keys = ['font_path', 'font_family', 'font_size', 'font_color', 'alignment', 'direction', 'bold', 'italic', 'line_spacing', 'letter_spacing']
        for key in style_keys:
            if key in clipboard_data:
                new_region_data[key] = clipboard_data[key]
        
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Paste Style to Region {region_index}"
        )
        self.execute_command(command)

    def delete_regions(self, region_indices: list):
        """删除指定的区域。"""
        if not region_indices:
            return

        graphics_view = self.get_graphics_view()
        if graphics_view is not None:
            graphics_view.clear_pending_geometry_edits()

        from editor.commands import DeleteRegionCommand

        regions = self.model.get_regions()
        pending_commands = []
        sorted_indices = sorted(
            {
                int(region_index)
                for region_index in region_indices
                if isinstance(region_index, int)
            },
            reverse=True,
        )

        for region_index in sorted_indices:
            if 0 <= region_index < len(regions):
                pending_commands.append(
                    DeleteRegionCommand(
                        model=self.model,
                        region_index=region_index,
                        region_data=regions[region_index],
                        description=f"Delete Region {region_index}",
                    )
                )

        if pending_commands:
            self._execute_command_batch(
                pending_commands,
                f"Delete Regions ({len(pending_commands)} ops)",
            )

        # 清除选择
        self.model.set_selection([])

    def enter_drawing_mode(self):
        """进入绘制模式以添加新文本框"""
        # 清除当前选择
        self.model.set_selection([])

        # 设置工具为绘制文本框
        self.model.set_active_tool('draw_textbox')

    def paste_region(self, mouse_pos=None):
        """粘贴复制的区域到新位置

        参数:
            mouse_pos: 鼠标位置 (scene coordinates),如果提供则在该位置粘贴
        """
        clipboard_data = self.history_service.paste_from_clipboard()
        if not clipboard_data:
            self.logger.warning("没有复制的区域数据")
            return

        # 创建新区域
        new_region_data = copy.deepcopy(clipboard_data)

        # 计算原区域的中心点
        if 'center' in new_region_data:
            old_center_x, old_center_y = new_region_data['center']
        elif 'lines' in new_region_data and new_region_data['lines']:
            # 从 lines 计算中心点
            all_points = [point for line in new_region_data['lines'] for point in line]
            if all_points:
                old_center_x = sum(p[0] for p in all_points) / len(all_points)
                old_center_y = sum(p[1] for p in all_points) / len(all_points)
            else:
                old_center_x, old_center_y = 0, 0
        else:
            old_center_x, old_center_y = 0, 0

        # 计算新的中心点
        if mouse_pos:
            # 如果提供了鼠标位置,在该位置粘贴
            new_center_x, new_center_y = mouse_pos.x(), mouse_pos.y()
        else:
            # 否则稍微偏移避免重叠
            new_center_x = old_center_x + 20
            new_center_y = old_center_y + 20

        # 计算偏移量
        offset_x = new_center_x - old_center_x
        offset_y = new_center_y - old_center_y

        # 应用偏移到所有坐标
        if 'center' in new_region_data:
            new_region_data['center'] = [new_center_x, new_center_y]

        if 'lines' in new_region_data and new_region_data['lines']:
            for line in new_region_data['lines']:
                for point in line:
                    point[0] += offset_x
                    point[1] += offset_y

        if 'polygons' in new_region_data and new_region_data['polygons']:
            for polygon in new_region_data['polygons']:
                for point in polygon:
                    point[0] += offset_x
                    point[1] += offset_y

        # 添加到模型 - 使用命令模式以支持撤销
        from editor.commands import AddRegionCommand

        command = AddRegionCommand(
            model=self.model,
            region_data=new_region_data,
            description="Paste Region"
        )
        self.execute_command(command)

        # 选中新粘贴的区域
        new_index = len(self.model.get_regions()) - 1
        self.model.set_selection([new_index])

    @pyqtSlot(bool, bool)
    def _on_history_undo_redo_state_changed(self, can_undo: bool, can_redo: bool):
        """历史栈状态变化回调。"""
        toolbar = self.get_toolbar()
        if toolbar is not None:
            toolbar.update_undo_redo_state(can_undo, can_redo)

    def _update_undo_redo_buttons(self):
        """主动刷新撤销/重做按钮状态。"""
        # 检查history_service是否已初始化
        if not hasattr(self, 'history_service') or self.history_service is None:
            return
        
        can_undo = self.history_service.can_undo()
        can_redo = self.history_service.can_redo()
        self._on_history_undo_redo_state_changed(can_undo, can_redo)

    @pyqtSlot()
    def export_image(self):
        return self.export_service.export_image()

    @staticmethod
    def _apply_white_frame_center(region: dict):
        EditorControllerExportService.apply_white_frame_center(region)

    @staticmethod
    def _resolve_effective_box_local(region: dict):
        return EditorControllerExportService.resolve_effective_box_local(region)

    def _resolve_editor_json_path(self, source_path: str) -> str:
        return self.export_service.resolve_editor_json_path(source_path)

    def _save_current_inpainted_image(
        self,
        source_path: str,
        config_dict: dict,
        mask: Optional[np.ndarray],
        current_inpainted_image: Optional[object] = None,
        has_regions: bool = False,
    ) -> None:
        self.export_service.save_current_inpainted_image(
            source_path,
            config_dict,
            mask,
            current_inpainted_image=current_inpainted_image,
            has_regions=has_regions,
        )

    def _persist_editor_state_for_export(
        self,
        export_service,
        source_path: str,
        regions: list,
        mask: Optional[np.ndarray],
        config_dict: dict,
        inpainted_image: Optional[object] = None,
    ) -> str:
        return self.export_service.persist_editor_state_for_export(
            export_service=export_service,
            source_path=source_path,
            regions=regions,
            mask=mask,
            config_dict=config_dict,
            inpainted_image=inpainted_image,
        )

    async def _async_export_with_desktop_ui_service(self, image, regions, mask, source_path=None, inpainted_image=None, paint_overlay=None):
        return await self.export_service.async_export_with_desktop_ui_service(
            image,
            regions,
            mask,
            source_path=source_path,
            inpainted_image=inpainted_image,
            paint_overlay=paint_overlay,
        )

    @pyqtSlot(str)
    def set_display_mode(self, mode: str):
        """设置编辑器显示模式。"""
        compare_enabled = (mode == "compare_original_split")
        region_mode = "full" if compare_enabled else mode
        if region_mode not in {"full", "text_only", "box_only", "none"}:
            region_mode = "full"

        self.logger.info(
            f"Toolbar: Display mode changed to '{mode}' (region mode: '{region_mode}', compare={compare_enabled})."
        )
        self.set_compare_mode(compare_enabled)
        self.model.set_region_display_mode(region_mode)
    
    @pyqtSlot(int)
    def set_original_image_alpha(self, alpha: int):
        """设置原图的不透明度 (0-100)，值越大越不透明（越显示原图）"""
        # slider = 0 -> alpha = 0.0（完全透明，显示inpainted）
        # slider = 100 -> alpha = 1.0（完全不透明，显示原图）
        alpha_float = alpha / 100.0
        self.model.set_original_image_alpha(alpha_float)
        # 标记用户已手动调整透明度
        self._user_adjusted_alpha = True

    def handle_global_render_setting_change(self):
        """Forces a re-render of all regions when a global render setting has changed."""

        # Clear the parameter service cache to ensure new global defaults are used
        from services import get_render_parameter_service
        render_parameter_service = get_render_parameter_service()
        render_parameter_service.clear_cache()

        # A heavy-handed but reliable way to force a full redraw of all regions with new global defaults
        self.model.set_regions(self.model.get_regions())

    @pyqtSlot()
    def run_ocr_for_selection(self):
        selected_indices = self.model.get_selection()
        if not selected_indices:
            return
        image = self._get_current_image()
        if not image:
            return

        all_regions = self.model.get_regions()
        valid_indices = [index for index in selected_indices if 0 <= index < len(all_regions)]
        if not valid_indices:
            return

        selected_regions_data = [copy.deepcopy(all_regions[i]) for i in valid_indices]
        region_identities = [self._build_region_identity(region) for region in selected_regions_data]
        request_revision = self.model.get_document_revision()
        source_image_path = self.model.get_source_image_path()
        
        # 显示开始Toast，保存引用以便后续关闭
        self._ocr_toast = None
        toast_manager = self.get_toast_manager()
        if toast_manager is not None:
            self._ocr_toast = toast_manager.show_info("正在识别...", duration=0)
        
        self.async_service.submit_task(
            self._async_ocr_task(
                image,
                selected_regions_data,
                valid_indices,
                region_identities,
                request_revision,
                source_image_path,
            )
        )

    @pyqtSlot(list)
    def on_regions_update_finished(self, updated_regions: list):
        """Slot to safely update regions from the main thread."""
        # 直接使用 set_regions，它会自动同步到 resource_manager
        self.model.set_regions(updated_regions)
        
        # 强制刷新属性栏（忽略焦点状态）
        property_panel = self.get_property_panel()
        if property_panel is not None:
            property_panel.force_refresh_from_model()
    
    @pyqtSlot(str, str)
    def _on_ocr_finished(self, status: str, message: str):
        """OCR完成后在主线程处理Toast。"""
        self._finalize_progress_toast("_ocr_toast", status, message)
    
    @pyqtSlot(str, str)
    def _on_translation_finished(self, status: str, message: str):
        """翻译完成后在主线程处理Toast。"""
        self._finalize_progress_toast("_translation_toast", status, message)

    async def _async_ocr_task(
        self,
        image,
        regions_to_process,
        indices,
        region_identities,
        request_revision: int,
        source_image_path: Optional[str],
    ):

        # 从属性面板获取用户选择的OCR配置
        ocr_config = None
        property_panel = self.get_property_panel()
        if property_panel is not None:
            selected_ocr = property_panel.get_selected_ocr_model()
            if selected_ocr:
                # 获取当前的OCR配置并更新ocr字段
                from manga_translator.config import Ocr, OcrConfig
                full_config = self.config_service.get_config()
                current_ocr_config = full_config.ocr if hasattr(full_config, 'ocr') else OcrConfig()
                try:
                    # 将字符串转换为Ocr枚举
                    ocr_enum = Ocr(selected_ocr) if selected_ocr else current_ocr_config.ocr
                    ocr_payload = (
                        current_ocr_config.model_dump()
                        if hasattr(current_ocr_config, "model_dump")
                        else {}
                    )
                    ocr_payload["ocr"] = ocr_enum
                    ocr_config = OcrConfig(**ocr_payload)
                    self.logger.info(f"Using OCR model from property panel: {selected_ocr}")
                except Exception as e:
                    self.logger.warning(f"Invalid OCR selection '{selected_ocr}', using default: {e}")
                    ocr_config = None

        pending_updates: list[tuple[int, str, str]] = []
        error_count = 0
        for i, region_data in enumerate(regions_to_process):
            try:
                ocr_result = await self.ocr_service.recognize_region(image, region_data, config=ocr_config)
                if ocr_result and ocr_result.text:
                    pending_updates.append((indices[i], region_identities[i], ocr_result.text))
            except Exception as e:
                self.logger.error(f"OCR识别失败: {e}")
                error_count += 1

        applied_count, skipped_count, document_changed = self._apply_async_region_updates(
            pending_updates,
            field_name="text",
            request_revision=request_revision,
            source_image_path=source_image_path,
        )

        if applied_count > 0 and skipped_count == 0 and error_count == 0:
            self._ocr_finished.emit("success", "识别完成")
            return
        if applied_count > 0:
            self._ocr_finished.emit(
                "warning",
                f"识别部分完成，已应用 {applied_count} 项，跳过 {skipped_count + error_count} 项",
            )
            return
        if document_changed and pending_updates:
            self._ocr_finished.emit("warning", "识别结果未应用，当前文档已变化")
            return
        if error_count > 0:
            self._ocr_finished.emit("error", "识别失败")
            return
        self._ocr_finished.emit("warning", "未识别到可更新的文本")
        

    @pyqtSlot()
    def run_translation_for_selection(self):
        selected_indices = self.model.get_selection()
        if not selected_indices:
            return
        image = self._get_current_image()
        if not image:
            return

        all_regions = self.model.get_regions()
        valid_indices = [index for index in selected_indices if 0 <= index < len(all_regions)]
        if not valid_indices:
            return

        selected_regions_data = [copy.deepcopy(all_regions[i]) for i in valid_indices]
        texts_to_translate = [r.get('text', '') for r in selected_regions_data]
        region_identities = [self._build_region_identity(region) for region in selected_regions_data]
        request_revision = self.model.get_document_revision()
        source_image_path = self.model.get_source_image_path()
        regions_context = copy.deepcopy(all_regions)
        
        # 显示开始Toast，保存引用以便后续关闭
        self._translation_toast = None
        toast_manager = self.get_toast_manager()
        if toast_manager is not None:
            self._translation_toast = toast_manager.show_info("正在翻译...", duration=0)
        
        # 传递所有区域以提供上下文，但只翻译选中的文本
        self.async_service.submit_task(
            self._async_translation_task(
                texts_to_translate,
                valid_indices,
                region_identities,
                image,
                regions_context,
                request_revision,
                source_image_path,
            )
        )

    async def _async_translation_task(
        self,
        texts,
        indices,
        region_identities,
        image,
        regions,
        request_revision: int,
        source_image_path: Optional[str],
    ):
        # 从属性面板获取用户选择的翻译器配置
        translator_to_use = None
        target_lang_to_use = None
        
        property_panel = self.get_property_panel()
        if property_panel is not None:
            selected_translator = property_panel.get_selected_translator()
            selected_target_lang = property_panel.get_selected_target_language()
            
            if selected_translator:
                from manga_translator.config import Translator
                try:
                    # 将字符串转换为Translator枚举
                    translator_to_use = Translator(selected_translator)
                    self.logger.info(f"Using translator from property panel: {selected_translator}")
                except (ValueError, AttributeError) as e:
                    self.logger.warning(f"Invalid translator selection '{selected_translator}', using default: {e}")
            
            if selected_target_lang:
                target_lang_to_use = selected_target_lang
                self.logger.info(f"Using target language from property panel: {selected_target_lang}")
        
        # 将image和所有regions信息传递给翻译服务以提供完整上下文
        try:
            results = await self.translation_service.translate_text_batch(
                texts, 
                translator=translator_to_use,
                target_lang=target_lang_to_use,
                image=image, 
                regions=regions
            )
            pending_updates: list[tuple[int, str, str]] = []
            for i, result in enumerate(results):
                if result and result.translated_text:
                    pending_updates.append((indices[i], region_identities[i], result.translated_text))

            applied_count, skipped_count, document_changed = self._apply_async_region_updates(
                pending_updates,
                field_name="translation",
                request_revision=request_revision,
                source_image_path=source_image_path,
            )

            if applied_count > 0 and skipped_count == 0:
                self._translation_finished.emit("success", "翻译完成")
                return
            if applied_count > 0:
                self._translation_finished.emit(
                    "warning",
                    f"翻译部分完成，已应用 {applied_count} 项，跳过 {skipped_count} 项",
                )
                return
            if document_changed and pending_updates:
                self._translation_finished.emit("warning", "翻译结果未应用，当前文档已变化")
                return
            self._translation_finished.emit("warning", "未生成可应用的翻译结果")
        except Exception as e:
            self.logger.error(f"翻译失败: {e}", exc_info=True)
            self._translation_finished.emit("error", "翻译失败")

    @pyqtSlot(list)
    def set_selection_from_list(self, indices: list):
        """Slot to handle selection changes originating from the RegionListView."""
        self.model.set_selection(indices)

