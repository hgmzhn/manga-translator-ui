import asyncio
import copy
import math
import os
import sys
from typing import Optional

import cv2
import numpy as np
import torch
from editor.commands import MaskEditCommand, UpdateRegionCommand
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
from widgets.themed_message_box import apply_message_box_style

from manga_translator.utils import open_pil_image

from .editor_model import EditorModel

# 添加项目根目录到路径以便导入path_manager
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from manga_translator.utils.path_manager import (
    find_inpainted_path,
    find_json_path,
    find_work_image_path,
    get_inpainted_path,
    get_json_path,
    resolve_original_image_path,
)


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
    _ocr_completed = pyqtSignal()
    _translation_completed = pyqtSignal()
    
    # Signal for thread-safe Toast notifications
    _show_toast_signal = pyqtSignal(str, int, bool, str)  # message, duration, success, clickable_path
    
    # Signal for thread-safe image loading
    _load_result_ready = pyqtSignal(dict)  # 加载结果信号

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
        
        # 用户透明度调整标志
        self._user_adjusted_alpha = False
        
        # 上次导出时的状态快照（用于检测是否有更改）
        self._last_export_snapshot = None

        # 只允许最新一笔/最新一次蒙版变更写回修复结果。
        self._active_inpaint_future = None
        self._inpaint_request_generation = 0
        self._suppress_refined_mask_autoinpaint = False

        # Connect internal signals for thread-safe updates
        self._update_refined_mask.connect(self.model.set_refined_mask)
        self._update_display_mask_type.connect(self.model.set_display_mask_type)
        self._regions_update_finished.connect(self.on_regions_update_finished)
        self._ocr_completed.connect(self._on_ocr_completed)
        self._translation_completed.connect(self._on_translation_completed)
        self._load_result_ready.connect(self._apply_load_result)  # 连接加载结果信号
        
        self._connect_model_signals()
        if hasattr(self.history_service, "undo_redo_state_changed"):
            self.history_service.undo_redo_state_changed.connect(self._on_history_undo_redo_state_changed)
    
    # ========== Resource Access Helpers (新的资源访问辅助方法) ==========
    
    def _get_current_image(self) -> Optional[Image.Image]:
        """获取当前图片（PIL Image）
        
        优先从ResourceManager获取，如果失败则从Model获取（向后兼容）
        """
        resource = self.resource_manager.get_current_image()
        if resource:
            return resource.image
        # 向后兼容：如果ResourceManager没有，尝试从Model获取
        return self.model.get_image()

    @staticmethod
    def _normalize_binary_mask(mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if mask is None:
            return None
        mask_np = np.array(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np[:, :, 0]
        return np.where(mask_np > 0, 255, 0).astype(np.uint8)

    def _get_cached_mask_snapshot(self) -> Optional[np.ndarray]:
        cached_mask = self.resource_manager.get_cache(self.CACHE_LAST_MASK)
        normalized = self._normalize_binary_mask(cached_mask)
        return None if normalized is None else normalized.copy()

    def _get_cached_inpainted_snapshot(self) -> Optional[np.ndarray]:
        cached_image = self.resource_manager.get_cache(self.CACHE_LAST_INPAINTED)
        if cached_image is None:
            return None
        image_np = np.array(cached_image)
        if image_np.ndim == 2:
            image_np = cv2.cvtColor(image_np, cv2.COLOR_GRAY2RGB)
        elif image_np.ndim == 3 and image_np.shape[2] == 4:
            image_np = cv2.cvtColor(image_np, cv2.COLOR_RGBA2RGB)
        return np.ascontiguousarray(image_np.copy())

    def _cancel_active_inpaint_task(self) -> None:
        future = self._active_inpaint_future
        self._active_inpaint_future = None
        if future is not None and not future.done():
            future.cancel()

    def _invalidate_inpaint_requests(self) -> None:
        self._cancel_active_inpaint_task()
        self._inpaint_request_generation += 1

    def _begin_inpaint_request(self) -> int:
        self._invalidate_inpaint_requests()
        return self._inpaint_request_generation

    def _is_inpaint_request_current(self, generation: int) -> bool:
        return generation == self._inpaint_request_generation
    
    @staticmethod
    def _normalize_image_path(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        return os.path.normcase(os.path.normpath(path))

    def _is_same_source_image(self, left: Optional[str], right: Optional[str]) -> bool:
        left_path = self._normalize_image_path(left)
        right_path = self._normalize_image_path(right)
        return bool(left_path and right_path and left_path == right_path)

    def _snapshot_image_for_export(self, image_obj, label: str) -> Optional[Image.Image]:
        """为导出创建独立的图像副本，避免切图时原图被关闭。"""
        if image_obj is None:
            return None
        try:
            if isinstance(image_obj, Image.Image):
                return image_obj.copy()
            return Image.fromarray(np.array(image_obj))
        except Exception as e:
            self.logger.error(f"Failed to snapshot {label} for export: {e}", exc_info=True)
            raise
    
    def _get_regions(self):
        """获取所有区域
        
        Returns:
            List[Dict]: 区域列表
        """
        # 从ResourceManager获取
        resources = self.resource_manager.get_all_regions()
        if resources:
            return [r.data for r in resources]
        # 向后兼容
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
            gv = getattr(self.view, "graphics_view", None) if self.view else None
            if not gv or not hasattr(gv, "_region_items") or not (0 <= region_index < len(gv._region_items)):
                return region_data

            item = gv._region_items[region_index]
            geo = getattr(item, "geo", None) if item is not None else None
            if geo is None:
                return region_data

            merged_region_data = copy.deepcopy(region_data)
            merged_region_data.update(geo.to_persisted_state_patch())
            return merged_region_data
        except Exception:
            return region_data

    def set_view(self, view):
        """设置view引用，用于更新UI状态"""
        self.view = view
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
            if hasattr(self, 'toast_manager'):
                if success:
                    self.toast_manager.show_success(message, duration, clickable_path if clickable_path else None)
                else:
                    self.toast_manager.show_error(message, duration)
        except Exception as e:
            self.logger.error(f"Exception in _show_toast_in_main_thread: {e}", exc_info=True)

    def _connect_model_signals(self):
        """监听模型的变化，可能需要触发一些后续逻辑"""
        self.model.regions_changed.connect(self.on_regions_changed)
        # 监听蒙版编辑后触发 inpainting
        self.model.refined_mask_changed.connect(self.on_refined_mask_changed)

    def on_regions_changed(self, regions):
        """模型中的区域数据变化时的槽函数"""
        # print(f"Controller: Regions changed, {len(regions)} regions total.")
        # This is a placeholder for where you might trigger a repaint or update.
        # For example, if you have a graphics scene, you might update it here.
        pass

    def on_refined_mask_changed(self, mask):
        """refined mask 变化时的槽函数，触发增量 inpainting"""
        if self._suppress_refined_mask_autoinpaint:
            return

        image = self._get_current_image()
        if image is None or mask is None:
            self._invalidate_inpaint_requests()
            return

        cached_mask = self._get_cached_mask_snapshot()
        generation = self._begin_inpaint_request()
        if cached_mask is not None:
            future = self.async_service.submit_task(
                self._async_incremental_inpaint(mask, generation)
            )
        else:
            future = self.async_service.submit_task(
                self._async_full_inpaint_with_cache(mask, generation)
            )
        self._active_inpaint_future = future

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

        macro_name = f"Batch Update Translations ({len(commands)})"
        if hasattr(self.history_service, "macro"):
            with self.history_service.macro(macro_name):
                for command in commands:
                    self.execute_command(command, update_ui=False)
        elif hasattr(self.history_service, "begin_macro") and hasattr(self.history_service, "end_macro"):
            self.history_service.begin_macro(macro_name)
            try:
                for command in commands:
                    self.execute_command(command, update_ui=False)
            finally:
                self.history_service.end_macro()
        else:
            for command in commands:
                self.execute_command(command, update_ui=False)

        self._update_undo_redo_buttons()

    def _generate_export_snapshot(self) -> dict:
        """生成当前状态的快照，用于检测导出后是否有更改
        
        使用轻量级的特征值而不是完整哈希，避免阻塞主线程
        """
        regions = self._get_regions()
        
        # 提取关键数据生成哈希
        snapshot_data = []
        for region in regions:
            # 只关注会影响导出结果的字段
            region_key = {
                'translation': region.get('translation', ''),
                'font_size': region.get('font_size'),
                'font_color': region.get('font_color'),
                'alignment': region.get('alignment'),
                'direction': region.get('direction'),
                'xyxy': region.get('xyxy'),
                'lines': str(region.get('lines', [])),
            }
            snapshot_data.append(str(region_key))
        
        # 使用蒙版的轻量级特征（形状+总和+非零像素数）而不是完整哈希
        mask = self.model.get_refined_mask()
        if mask is None:
            mask = self.model.get_raw_mask()
        mask_signature = ""
        if mask is not None:
            # 使用形状、总和、非零像素数作为快速特征
            mask_signature = f"{mask.shape}_{mask.sum()}_{np.count_nonzero(mask)}"
        
        # 使用简单的字符串哈希
        regions_str = '|'.join(snapshot_data)
        
        return {
            'regions_hash': hash(regions_str),
            'mask_signature': mask_signature,
            'source_path': self.model.get_source_image_path(),
        }
    
    def _has_changes_since_last_export(self) -> bool:
        """检查自上次导出后是否有更改"""
        if self._last_export_snapshot is None:
            # 从未导出过，检查是否有撤销历史
            return self.history_service.can_undo()
        
        current_snapshot = self._generate_export_snapshot()
        
        # 比较快照
        if current_snapshot['source_path'] != self._last_export_snapshot['source_path']:
            # 不同的图片，不需要比较
            return self.history_service.can_undo()
        
        return (current_snapshot['regions_hash'] != self._last_export_snapshot['regions_hash'] or
                current_snapshot['mask_signature'] != self._last_export_snapshot['mask_signature'])
    
    def _save_export_snapshot(self):
        """保存当前状态快照（导出成功后调用）"""
        self._last_export_snapshot = self._generate_export_snapshot()
        self.logger.debug(f"Export snapshot saved: {self._last_export_snapshot}")

    def _clear_editor_state(self, release_image_cache: bool = False):
        """清空编辑器状态
        
        Args:
            release_image_cache: 是否同时释放图片缓存（切换文件时通常不需要）
        """
        # 关闭加载提示（如果存在）
        if hasattr(self, '_loading_toast') and self._loading_toast:
            try:
                self._loading_toast.close()
                self._loading_toast = None
            except Exception:
                pass
        
        # 取消所有正在运行的后台任务
        self.async_service.cancel_all_tasks()
        self._invalidate_inpaint_requests()

        # 使用ResourceManager卸载当前资源
        self.resource_manager.unload_image(release_from_cache=release_image_cache)

        # 清空模型数据（向后兼容，View仍然监听Model）
        self.model.set_regions([])
        self.model.set_raw_mask(None)
        self.model.set_refined_mask(None)
        self.model.set_inpainted_image(None)
        self.model.set_compare_image(None)
        self.model.set_selection([])

        # 禁用导出功能（无图片时不可导出）
        if self.view and hasattr(self.view, 'toolbar'):
            self.view.toolbar.set_export_enabled(False)

        # 清空历史记录
        self.history_service.clear()
        if hasattr(self.history_service, "mark_clean"):
            self.history_service.mark_clean()
        self._update_undo_redo_buttons()
        
        # 清空导出快照（每张图片独立）
        self._last_export_snapshot = None

        # 清空缓存（使用ResourceManager）
        self.resource_manager.clear_cache()

        # 清空渲染参数缓存
        from services import get_render_parameter_service
        render_parameter_service = get_render_parameter_service()
        render_parameter_service.clear_cache()
        
        # 清空GraphicsView的缓存
        if self.view and hasattr(self.view, 'graphics_view'):
            gv = self.view.graphics_view
            if hasattr(gv, '_text_render_cache'):
                gv._text_render_cache.clear()
            if hasattr(gv, '_text_blocks_cache'):
                gv._text_blocks_cache = []
            if hasattr(gv, '_dst_points_cache'):
                gv._dst_points_cache = []
        
        # 关闭加载线程池（如果存在）
        if hasattr(self, '_load_executor'):
            try:
                self._load_executor.shutdown(wait=False)
                del self._load_executor
            except Exception:
                pass
        
        # 强制垃圾回收
        pass
        # 释放GPU显存
        try:
            import torch
            if torch.cuda.is_available():
                pass
        except ImportError:
            pass
        
        self.logger.debug("Editor state cleared and memory released")

    def _find_source_from_translation_map(self, image_path: str) -> Optional[str]:
        """从 translation_map.json 中解析翻译结果对应的原图路径。"""
        try:
            import json

            norm_path = os.path.normpath(image_path)
            output_dir = os.path.dirname(norm_path)
            map_path = os.path.join(output_dir, 'translation_map.json')
            if not os.path.exists(map_path):
                return None

            with open(map_path, 'r', encoding='utf-8') as f:
                translation_map = json.load(f)

            source_path = translation_map.get(norm_path)
            if source_path and os.path.exists(source_path):
                return os.path.normpath(source_path)
        except Exception as e:
            self.logger.error(f"Error reading translation map for {image_path}: {e}")

        return None

    def _resolve_editor_image_paths(self, image_path: str) -> tuple[str, str]:
        """
        解析编辑器加载用的路径：
        1. source_path: 逻辑原图路径（用于 JSON / 输出路径）
        2. display_image_path: 编辑器底图（优先使用 work/editor_base）
        """
        source_path = self._find_source_from_translation_map(image_path)
        if not source_path:
            source_path = resolve_original_image_path(image_path)

        display_image_path = find_work_image_path(source_path)
        if not display_image_path:
            display_image_path = source_path

        return os.path.normpath(source_path), os.path.normpath(display_image_path)

    def load_image_and_regions(self, image_path: str):
        """加载图像及其关联的区域数据，并触发后台处理"""
        # 检查是否有未导出的更改（基于快照比较，而不仅仅是撤销历史）
        has_changes = self._has_changes_since_last_export()
        if has_changes:
            from PyQt6.QtWidgets import QMessageBox
            msg_box = QMessageBox(None)
            msg_box.setWindowTitle("未保存的编辑")
            msg_box.setText("当前图片有未保存的编辑")
            msg_box.setInformativeText("导出图片时会同时保存 JSON。")
            
            # 添加按钮
            export_btn = msg_box.addButton("导出图片", QMessageBox.ButtonRole.YesRole)
            cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg_box.addButton("不保存", QMessageBox.ButtonRole.NoRole)
            
            msg_box.setDefaultButton(cancel_btn)
            apply_message_box_style(msg_box)
            msg_box.exec()
            
            clicked_button = msg_box.clickedButton()
            
            if clicked_button == cancel_btn:
                return
            elif clicked_button == export_btn:
                self.export_image()
                # 使用QTimer延迟加载，避免阻塞UI
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(500, lambda: self._do_load_image(image_path))
                return
            # 如果点击"不保存"，继续执行下面的加载逻辑

        self._do_load_image(image_path)
    
    def _do_load_image(self, image_path: str):
        """实际执行图片加载的内部方法 - 使用线程池避免阻塞UI"""
        import concurrent.futures
        
        # 清空旧状态
        self._clear_editor_state()
        
        # 显示加载提示
        if hasattr(self, 'toast_manager'):
            self._loading_toast = self.toast_manager.show_info("正在加载...", duration=0)
        
        # 使用线程池加载数据
        if not hasattr(self, '_load_executor'):
            self._load_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        
        def load_data():
            """在后台线程加载数据"""
            try:
                source_path, display_image_path = self._resolve_editor_image_paths(image_path)

                # 1. 加载编辑底图
                image_resource = self.resource_manager.load_image(display_image_path)
                image = image_resource.image
                compare_image = image

                if os.path.normpath(source_path) != os.path.normpath(display_image_path):
                    try:
                        compare_image = self.resource_manager.load_detached_image(source_path)
                        if compare_image.size != image.size:
                            compare_image = compare_image.resize(image.size, Image.Resampling.LANCZOS)
                    except Exception as compare_error:
                        self.logger.warning(f"Error loading compare image: {compare_error}")
                        compare_image = image

                # 2. 加载JSON
                json_path = find_json_path(source_path)

                if not json_path:
                    regions = []
                    raw_mask = None
                    inpainted_path = find_inpainted_path(source_path)
                    inpainted_image = None
                    if inpainted_path:
                        try:
                            inpainted_image = open_pil_image(inpainted_path, eager=False)
                            if inpainted_image.size != image.size:
                                inpainted_image = inpainted_image.resize(image.size, Image.Resampling.LANCZOS)
                        except Exception as e:
                            self.logger.error(f"Error loading inpainted image: {e}")
                            inpainted_path = None
                            inpainted_image = None
                else:
                    regions, raw_mask, _ = self.file_service.load_translation_json(source_path)
    
                    # 3. 查找和加载inpainted图片
                    inpainted_path = find_inpainted_path(source_path)
                    inpainted_image = None
                    if inpainted_path:
                        try:
                            inpainted_image = open_pil_image(inpainted_path, eager=False)
                            if inpainted_image.size != image.size:
                                inpainted_image = inpainted_image.resize(image.size, Image.Resampling.LANCZOS)
                        except Exception as e:
                            self.logger.error(f"Error loading inpainted image: {e}")
                            inpainted_path = None
                            inpainted_image = None

                return {
                    'type': 'normal',
                    'source_path': source_path,
                    'image': image,
                    'compare_image': compare_image,
                    'regions': regions,
                    'raw_mask': raw_mask,
                    'inpainted_path': inpainted_path,
                    'inpainted_image': inpainted_image
                }
            except Exception as e:
                self.logger.error(f"Error loading image data: {e}", exc_info=True)
                return {'type': 'error', 'error': str(e)}
        
        def on_load_complete(future):
            """加载完成回调 - 使用信号确保在主线程更新UI"""
            try:
                result = future.result()
                self._load_result_ready.emit(result)
            except Exception as e:
                self.logger.error(f"Load failed: {e}", exc_info=True)
                self._load_result_ready.emit({'type': 'error', 'error': str(e)})
        
        future = self._load_executor.submit(load_data)
        future.add_done_callback(on_load_complete)
    
    @pyqtSlot(dict)
    def _apply_load_result(self, result: dict):
        """在主线程应用加载结果"""
        try:
            if result['type'] == 'error':
                self._handle_load_error(result['error'])
            else:
                self._apply_loaded_data_to_model(
                    result['source_path'],
                    result['image'],
                    result.get('compare_image'),
                    result['regions'],
                    result['raw_mask'],
                    result['inpainted_path'],
                    result['inpainted_image']
                )
        except Exception as e:
            self.logger.error(f"Exception in _apply_load_result: {e}", exc_info=True)
    
    def _apply_loaded_data_to_model(self, source_path, image, compare_image, regions, raw_mask, inpainted_path, inpainted_image):
        """在主线程应用加载的数据到Model"""
        try:
            # 关闭加载提示
            if hasattr(self, '_loading_toast') and self._loading_toast:
                self._loading_toast.close()
                self._loading_toast = None
            
            # 启用导出功能
            if self.view and hasattr(self.view, 'toolbar'):
                self.view.toolbar.set_export_enabled(True)
            
            # 导入渲染参数
            if regions:
                from services import get_render_parameter_service
                render_parameter_service = get_render_parameter_service()
                for i, region_data in enumerate(regions):
                    render_parameter_service.import_parameters_from_json(i, region_data)

            self.model.set_source_image_path(source_path)

            if not hasattr(self, '_user_adjusted_alpha') or not self._user_adjusted_alpha:
                default_alpha = 0.0 if inpainted_image is not None else 1.0
                self.model.set_original_image_alpha(default_alpha)

            self.model.set_image(image)
            self.model.set_compare_image(compare_image if compare_image is not None else image)
            self._set_regions(regions)

            if raw_mask is not None:
                from desktop_qt_ui.editor.core.types import MaskType
                self.resource_manager.set_mask(MaskType.RAW, raw_mask)
                self.model.set_raw_mask(raw_mask)

            self.model.set_refined_mask(None)

            if inpainted_path:
                self.model.set_inpainted_image_path(inpainted_path)
            else:
                self.model.set_inpainted_image_path(None)

            if inpainted_image:
                self.model.set_inpainted_image(inpainted_image)
            else:
                self.model.set_inpainted_image(None)

            # 触发后台处理
            if regions and raw_mask is not None:
                self.async_service.submit_task(self._async_refine_and_inpaint())
                
        except Exception as e:
            self.logger.error(f"Error applying loaded data to model: {e}", exc_info=True)
    
    def _handle_load_error(self, error_msg: str):
        """处理加载错误"""
        # 关闭加载提示
        if hasattr(self, '_loading_toast') and self._loading_toast:
            self._loading_toast.close()
            self._loading_toast = None
        
        if hasattr(self, 'toast_manager'):
            self.toast_manager.show_error(f"加载失败: {error_msg}")
        
        self.model.set_image(None)
        self.model.set_compare_image(None)
        self.model.set_regions([])
        self.model.set_raw_mask(None)
        self.model.set_refined_mask(None)

    async def _async_refine_and_inpaint(self):
        """Prepare refined mask and warm caches for editor inpainting."""
        try:
            raw_mask = self.model.get_raw_mask() # Use the raw mask for refinement
            regions = self._get_regions()

            if raw_mask is None or not regions:
                self.logger.warning("Refinement/Inpainting skipped: image, mask, or regions not available.")
                return

            # JSON 中保存的已经是优化后的蒙版，直接使用
            refined_mask = self._normalize_binary_mask(raw_mask)

            if refined_mask is None:
                self.logger.error("Mask refinement failed.")
                return

            # Ensure refined_mask is a valid numpy array
            if not isinstance(refined_mask, np.ndarray):
                self.logger.error(f"Refined mask is not a numpy array: {type(refined_mask)}")
                return

            if refined_mask.size == 0:
                self.logger.error("Refined mask is empty")
                return

            current_inpainted_image = self.model.get_inpainted_image()
            if current_inpainted_image is not None:
                inpainted_image_np = np.array(current_inpainted_image.convert("RGB"))
                self.resource_manager.set_cache(self.CACHE_LAST_INPAINTED, inpainted_image_np.copy())
                self.resource_manager.set_cache(self.CACHE_LAST_MASK, refined_mask.copy())
                if not getattr(self, '_user_adjusted_alpha', False):
                    self.model.set_original_image_alpha(0.0)
            else:
                self.resource_manager.clear_cache(self.CACHE_LAST_INPAINTED)
                self.resource_manager.clear_cache(self.CACHE_LAST_MASK)

            self._suppress_refined_mask_autoinpaint = True
            try:
                self.model.set_refined_mask(refined_mask)
            finally:
                self._suppress_refined_mask_autoinpaint = False

        except asyncio.CancelledError:
            raise  # 重新抛出，让任务正确取消
        except Exception as e:
            self.logger.error(f"Error during async refine and inpaint: {e}")

    async def _async_incremental_inpaint(self, current_mask, generation: int):
        """按最新 mask 增量修复，只对新增区域跑局部 inpaint。"""
        try:
            if not self._is_inpaint_request_current(generation):
                return

            image = self._get_current_image()

            if image is None or current_mask is None:
                self.logger.warning("Incremental inpainting skipped: missing data.")
                return

            last_processed_mask = self._get_cached_mask_snapshot()
            if last_processed_mask is None:
                await self._async_full_inpaint_with_cache(current_mask, generation)
                return

            current_mask_2d = self._normalize_binary_mask(current_mask)
            if current_mask_2d is None:
                return

            if current_mask_2d.shape != last_processed_mask.shape:
                self.logger.warning(
                    "Incremental inpainting fell back to full: mask shape changed from %s to %s",
                    last_processed_mask.shape,
                    current_mask_2d.shape,
                )
                await self._async_full_inpaint_with_cache(current_mask_2d, generation)
                return

            added_areas = cv2.bitwise_and(current_mask_2d, cv2.bitwise_not(last_processed_mask))
            removed_areas = cv2.bitwise_and(last_processed_mask, cv2.bitwise_not(current_mask_2d))

            if not np.any(added_areas) and not np.any(removed_areas):
                return

            image_np = np.array(image.convert("RGB"))
            full_result = self._get_cached_inpainted_snapshot()
            if full_result is None or full_result.shape != image_np.shape:
                full_result = image_np.copy()

            if np.any(removed_areas):
                removed_pixels = removed_areas > 0
                full_result[removed_pixels] = image_np[removed_pixels]

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

                config = self.config_service.get_config()
                inpainter_config_model = config.inpainter

                try:
                    from manga_translator.config import (
                        Inpainter,
                        InpainterConfig,
                        InpaintPrecision,
                    )
                    from manga_translator.inpainting import dispatch as inpaint_dispatch
                except ImportError as e:
                    self.logger.error(f"Failed to import backend modules: {e}")
                    return

                inpainter_config = InpainterConfig()
                inpainter_config.inpainting_precision = InpaintPrecision(inpainter_config_model.inpainting_precision)
                inpainter_config.force_use_torch_inpainting = inpainter_config_model.force_use_torch_inpainting

                inpainter_name = inpainter_config_model.inpainter
                try:
                    inpainter_key = Inpainter(inpainter_name)
                except ValueError:
                    inpainter_key = Inpainter.lama_large

                inpainting_size = inpainter_config_model.inpainting_size
                cli_config = config.cli
                device = 'cuda' if cli_config.use_gpu and torch.cuda.is_available() else 'cpu'

                bbox_result = await inpaint_dispatch(
                    inpainter_key=inpainter_key,
                    image=bbox_image,
                    mask=bbox_mask,
                    config=inpainter_config,
                    inpainting_size=inpainting_size,
                    device=device
                )

                if bbox_result is None:
                    self.logger.error("Incremental inpainting failed, returned None.")
                    return
                if not self._is_inpaint_request_current(generation):
                    return

                full_result[y_min:y_max, x_min:x_max] = bbox_result

            if not self._is_inpaint_request_current(generation):
                return

            self.resource_manager.set_cache(self.CACHE_LAST_INPAINTED, full_result.copy())
            self.resource_manager.set_cache(self.CACHE_LAST_MASK, current_mask_2d.copy())

            final_image = Image.fromarray(full_result)
            self.model.set_inpainted_image(final_image)
            if not getattr(self, '_user_adjusted_alpha', False):
                self.model.set_original_image_alpha(0.0)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error during bounding box inpainting: {e}", exc_info=True)

    async def _async_full_inpaint_with_cache(self, mask, generation: int):
        """执行完整修复并缓存结果"""
        try:
            if not self._is_inpaint_request_current(generation):
                return

            image = self._get_current_image()

            if image is None or mask is None:
                return

            # 延迟导入后端模块
            try:
                from manga_translator.config import (
                    Inpainter,
                    InpainterConfig,
                    InpaintPrecision,
                )
                from manga_translator.inpainting import dispatch as inpaint_dispatch
            except ImportError as e:
                self.logger.error(f"Failed to import backend modules: {e}")
                return

            image_np = np.array(image.convert("RGB"))

            mask_2d = self._normalize_binary_mask(mask)
            if mask_2d is None:
                return

            # 获取配置
            config = self.config_service.get_config()
            inpainter_config_model = config.inpainter

            inpainter_config = InpainterConfig()
            inpainter_config.inpainting_precision = InpaintPrecision(inpainter_config_model.inpainting_precision)
            inpainter_config.force_use_torch_inpainting = inpainter_config_model.force_use_torch_inpainting

            inpainter_name = inpainter_config_model.inpainter
            try:
                inpainter_key = Inpainter(inpainter_name)
            except ValueError:
                self.logger.warning(f"Unknown inpainter model: {inpainter_name}, defaulting to lama_large")
                inpainter_key = Inpainter.lama_large

            inpainting_size = inpainter_config_model.inpainting_size

            cli_config = config.cli
            use_gpu = cli_config.use_gpu
            device = 'cuda' if use_gpu and torch.cuda.is_available() else 'cpu'

            inpainted_image_np = await inpaint_dispatch(
                inpainter_key=inpainter_key,
                image=image_np,
                mask=mask_2d,
                config=inpainter_config,
                inpainting_size=inpainting_size,
                device=device
            )

            if inpainted_image_np is not None:
                if not self._is_inpaint_request_current(generation):
                    return

                # 缓存结果
                self.resource_manager.set_cache(self.CACHE_LAST_INPAINTED, inpainted_image_np.copy())
                self.resource_manager.set_cache(self.CACHE_LAST_MASK, mask_2d.copy())

                # 更新模型
                inpainted_image = Image.fromarray(inpainted_image_np)
                self.model.set_inpainted_image(inpainted_image)
                if not getattr(self, '_user_adjusted_alpha', False):
                    self.model.set_original_image_alpha(0.0)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Error during full inpainting with cache: {e}", exc_info=True)

    @pyqtSlot(str, bool)
    def set_display_mask_type(self, mask_type: str, visible: bool):
        """Slot to control which mask is displayed ('raw' or 'refined') or if none is."""
        if visible:
            self.model.set_display_mask_type(mask_type)
        else:
            self.model.set_display_mask_type('none')

    @pyqtSlot(str)
    def set_active_tool(self, tool: str):
        """Sets the active tool in the model (e.g., 'pen', 'eraser')."""
        self.model.set_active_tool(tool)

    @pyqtSlot(int)
    def set_brush_size(self, size: int):
        """Sets the brush size in the model."""
        self.model.set_brush_size(size)

    @pyqtSlot()
    def clear_all_masks(self):
        """清除当前图片的所有可编辑蒙版（refined mask），支持撤销/重做。"""
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
                image = self._get_current_image()
                if image is None:
                    self.logger.warning("Clear all masks skipped: no active image.")
                    return
                old_mask = np.zeros((int(image.height), int(image.width)), dtype=np.uint8)

            new_mask = np.zeros_like(old_mask, dtype=np.uint8)
            if np.array_equal(old_mask, new_mask):
                self.logger.debug("Clear all masks skipped: mask is already empty.")
                return

            self.execute_command(MaskEditCommand(self.model, old_mask, new_mask))
            self.logger.info("Cleared all masks for current image.")
        except Exception as e:
            self.logger.error(f"Failed to clear all masks: {e}", exc_info=True)

    @pyqtSlot(int, str)
    def update_translated_text(self, region_index: int, text: str):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('translation') == text:
            return

        new_region_data = old_region_data.copy()
        new_region_data['translation'] = text
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Translation Region {region_index}",
            merge_key=f"region:{region_index}:translation",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_original_text(self, region_index: int, text: str):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data or old_region_data.get('text') == text:
            return

        new_region_data = old_region_data.copy()
        # 统一使用 text 字段，用户编辑和OCR识别都更新这个字段
        new_region_data['text'] = text
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Original Text Region {region_index}",
            merge_key=f"region:{region_index}:text",
        )
        self.execute_command(command)

    @pyqtSlot(int, int)
    def update_font_size(self, region_index: int, size: int):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)

        if old_region_data.get('font_size') == size:
            return

        new_region_data = old_region_data.copy()
        new_region_data['font_size'] = size
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Font Size Region {region_index}",
            merge_key=f"region:{region_index}:font_size",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_font_color(self, region_index: int, color: str):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('font_color') == color:
            return

        new_region_data = old_region_data.copy()
        new_region_data['font_color'] = color
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Font Color Region {region_index}",
            merge_key=f"region:{region_index}:font_color",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_stroke_color(self, region_index: int, hex_color: str):
        from PyQt6.QtGui import QColor
        c = QColor(hex_color)
        new_bg_colors = [c.red(), c.green(), c.blue()]
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('bg_colors') == new_bg_colors:
            return

        new_region_data = old_region_data.copy()
        new_region_data['bg_colors'] = new_bg_colors
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Stroke Color Region {region_index}",
            merge_key=f"region:{region_index}:bg_colors",
        )
        self.execute_command(command)

    @pyqtSlot(int, float)
    def update_stroke_width(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('stroke_width') == value:
            return

        new_region_data = old_region_data.copy()
        new_region_data['stroke_width'] = value
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Stroke Width Region {region_index}",
            merge_key=f"region:{region_index}:stroke_width",
        )
        self.execute_command(command)

    @pyqtSlot(int, float)
    def update_line_spacing(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        current_value = old_region_data.get('line_spacing')
        if current_value is None:
            current_value = self.config_service.get_config().render.line_spacing or 1.0
        if current_value == value:
            return

        new_region_data = old_region_data.copy()
        new_region_data['line_spacing'] = value
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Line Spacing Region {region_index}",
            merge_key=f"region:{region_index}:line_spacing",
        )
        self.execute_command(command)

    @pyqtSlot(int, float)
    def update_letter_spacing(self, region_index: int, value: float):
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        current_value = old_region_data.get('letter_spacing')
        if current_value is None:
            current_value = self.config_service.get_config().render.letter_spacing or 1.0
        if current_value == value:
            return

        new_region_data = old_region_data.copy()
        new_region_data['letter_spacing'] = value
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Letter Spacing Region {region_index}",
            merge_key=f"region:{region_index}:letter_spacing",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_font_family(self, region_index: int, font_filename: str):
        """Update the font family for a specific region.
        
        Args:
            region_index: Index of the region
            font_filename: Font filename (e.g., 'Arial.ttf') or empty string for default
        """
        import os

        from manga_translator.utils import BASE_PATH
        
        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        
        # Convert selected value to region font_path
        if font_filename:
            if os.path.isabs(font_filename):
                norm_path = os.path.normpath(font_filename)
                base_path = os.path.normpath(BASE_PATH)
                fonts_dir = os.path.normpath(os.path.join(base_path, 'fonts'))
                try:
                    if os.path.commonpath([norm_path, fonts_dir]) == fonts_dir:
                        # Prefer repo-relative path for bundled fonts
                        font_path = os.path.relpath(norm_path, base_path).replace('\\', '/')
                    elif os.path.commonpath([norm_path, base_path]) == base_path:
                        font_path = os.path.relpath(norm_path, base_path).replace('\\', '/')
                    else:
                        # External absolute path: keep absolute
                        font_path = norm_path
                except ValueError:
                    font_path = norm_path
            else:
                # Persist relative path to keep JSON portable
                if font_filename.lower().startswith('fonts/') or font_filename.lower().startswith('fonts\\'):
                    font_path = font_filename.replace('\\', '/')
                else:
                    font_path = f"fonts/{font_filename}".replace('\\', '/')
        else:
            font_path = ""
        
        # Check if font_path changed
        if old_region_data.get('font_path') == font_path:
            return

        new_region_data = old_region_data.copy()
        new_region_data['font_path'] = font_path
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Font Family Region {region_index}",
            merge_key=f"region:{region_index}:font_path",
        )
        self.execute_command(command)

    @pyqtSlot(int, str)
    def update_alignment(self, region_index: int, alignment_text: str):
        """槽：响应UI中的对齐方式修改"""
        raw_text = str(alignment_text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in ("auto", "left", "center", "right"):
            alignment_value = lower_text
        else:
            i18n = get_i18n_manager()
            alignment_value = None
            if i18n:
                localized_map = {
                    i18n.translate("alignment_auto"): "auto",
                    i18n.translate("alignment_left"): "left",
                    i18n.translate("alignment_center"): "center",
                    i18n.translate("alignment_right"): "right",
                }
                alignment_value = localized_map.get(raw_text)

            if alignment_value is None:
                fallback_map = {"自动": "auto", "左对齐": "left", "居中": "center", "右对齐": "right"}
                alignment_value = fallback_map.get(raw_text, "auto")

        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('alignment') == alignment_value:
            return

        new_region_data = old_region_data.copy()
        new_region_data['alignment'] = alignment_value
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Alignment to {alignment_value}",
            merge_key=f"region:{region_index}:alignment",
        )
        self.execute_command(command)

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

    @pyqtSlot(int, str)
    def update_direction(self, region_index: int, direction_text: str):
        """槽：响应UI中的方向修改"""
        direction_value = "horizontal"
        raw_text = str(direction_text or "").strip()
        lower_text = raw_text.lower()
        if lower_text in ("v", "vertical"):
            direction_value = "vertical"
        elif lower_text in ("h", "horizontal"):
            direction_value = "horizontal"
        else:
            i18n = get_i18n_manager()
            if i18n:
                horizontal_label = i18n.translate("direction_horizontal")
                vertical_label = i18n.translate("direction_vertical")
                if raw_text == vertical_label:
                    direction_value = "vertical"
                elif raw_text == horizontal_label:
                    direction_value = "horizontal"
            # 兼容历史中文值
            if raw_text in ("竖排",):
                direction_value = "vertical"
            elif raw_text in ("横排",):
                direction_value = "horizontal"

        old_region_data = self._get_region_by_index(region_index)
        if not old_region_data:
            return

        old_region_data = self._merge_live_geometry_state(region_index, old_region_data)
        if old_region_data.get('direction') == direction_value:
            return

        new_region_data = old_region_data.copy()
        new_region_data['direction'] = direction_value
        
        command = UpdateRegionCommand(
            model=self.model,
            region_index=region_index,
            old_data=old_region_data,
            new_data=new_region_data,
            description=f"Update Direction to {direction_value}",
            merge_key=f"region:{region_index}:direction",
        )
        self.execute_command(command)

    def execute_command(self, command, update_ui: bool = True):
        """执行命令并更新UI - 使用 Qt 的 QUndoStack"""
        if command:
            if hasattr(self.history_service, "execute"):
                self.history_service.execute(command)
            else:
                self.history_service.push_command(command)
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
        """删除指定的区域

        删除逻辑:
        - 如果区域有紫色多边形(active_polygon_index >= 0),只删除那个多边形
        - 如果区域没有紫色多边形(active_polygon_index == -1),删除整个区域
        """
        if not region_indices:
            return

        # 清理视图里的几何编辑上下文，确保删除触发正确刷新
        if self.view and hasattr(self.view, 'graphics_view'):
            graphics_view = self.view.graphics_view
            if graphics_view and hasattr(graphics_view, '_clear_pending_geometry_edits'):
                graphics_view._clear_pending_geometry_edits()

        # 按索引倒序处理，避免索引变化问题
        sorted_indices = sorted(region_indices, reverse=True)

        regions_to_delete = []  # 需要完全删除的区域索引
        pending_commands = []  # 批量提交，合并为单次撤回

        for region_index in sorted_indices:
            if 0 <= region_index < len(self.model.get_regions()):
                # 获取对应的 region_item,检查 active_polygon_index
                region_item = None
                if self.view and hasattr(self.view, 'graphics_view'):
                    graphics_view = self.view.graphics_view
                    if hasattr(graphics_view, '_region_items') and region_index < len(graphics_view._region_items):
                        region_item = graphics_view._region_items[region_index]

                active_polygon_index = -1
                if region_item and hasattr(region_item, 'active_polygon_index'):
                    active_polygon_index = region_item.active_polygon_index



                # 获取区域数据
                regions = self.model.get_regions()
                region_data = regions[region_index]
                lines = region_data.get('lines', [])

                if active_polygon_index >= 0 and active_polygon_index < len(lines):
                    # 有紫色多边形,只删除那个多边形
                    old_data = region_data.copy()
                    new_data = region_data.copy()

                    # 删除指定的多边形
                    new_lines_model = [line for i, line in enumerate(lines) if i != active_polygon_index]

                    if len(new_lines_model) == 0:
                        # 如果删除后没有多边形了,删除整个区域
                        regions_to_delete.append(region_index)
                    else:
                        # 获取旧的 center 和 angle
                        old_center = region_data.get('center', [0, 0])
                        old_angle = region_data.get('angle', 0)

                        # 重新计算新的 center (基于剩余的多边形)
                        from .desktop_ui_geometry import (
                            get_polygon_center,
                            rotate_point,
                        )
                        all_pts = [pt for ln in new_lines_model for pt in ln]
                        new_cx, new_cy = get_polygon_center(all_pts)

                        # 为了保持视觉位置不变,需要将 lines 转换到新的坐标系
                        # 步骤:
                        # 1. 将旧的 lines (模型坐标) 转换为世界坐标 (旋转)
                        # 2. 将世界坐标转换为新的模型坐标 (反旋转,使用新的 center)

                        final_lines_model = []
                        for poly_model in new_lines_model:
                            # 转换为世界坐标
                            poly_world = [rotate_point(x, y, old_angle, old_center[0], old_center[1]) for x, y in poly_model]
                            # 转换为新的模型坐标
                            poly_new_model = [rotate_point(x, y, -old_angle, new_cx, new_cy) for x, y in poly_world]
                            final_lines_model.append(poly_new_model)

                        # 更新数据
                        new_data['lines'] = final_lines_model
                        new_data['center'] = [new_cx, new_cy]

                        # 创建更新命令
                        command = UpdateRegionCommand(
                            model=self.model,
                            region_index=region_index,
                            old_data=old_data,
                            new_data=new_data,
                            description=f"Delete Polygon {active_polygon_index} from Region {region_index}"
                        )
                        pending_commands.append(command)
                else:
                    # 没有紫色多边形,删除整个区域
                    regions_to_delete.append(region_index)

        # 删除需要完全删除的区域
        if regions_to_delete:
            # regions_to_delete 已经是倒序的,直接从后往前删除
            # 使用命令模式以支持撤销
            from editor.commands import DeleteRegionCommand

            for region_index in regions_to_delete:
                regions = self.model.get_regions()
                if 0 <= region_index < len(regions):
                    region_data = regions[region_index]
                    command = DeleteRegionCommand(
                        model=self.model,
                        region_index=region_index,
                        region_data=region_data,
                        description=f"Delete Region {region_index}"
                    )
                    pending_commands.append(command)

        if pending_commands:
            macro_name = f"Delete Regions ({len(pending_commands)} ops)"
            if hasattr(self.history_service, "macro"):
                with self.history_service.macro(macro_name):
                    for command in pending_commands:
                        self.execute_command(command, update_ui=False)
            elif hasattr(self.history_service, "begin_macro") and hasattr(self.history_service, "end_macro"):
                self.history_service.begin_macro(macro_name)
                try:
                    for command in pending_commands:
                        self.execute_command(command, update_ui=False)
                finally:
                    self.history_service.end_macro()
            else:
                for command in pending_commands:
                    self.execute_command(command, update_ui=False)

            self._update_undo_redo_buttons()

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
        if hasattr(self, 'view') and self.view:
            if hasattr(self.view, 'toolbar') and self.view.toolbar:
                self.view.toolbar.update_undo_redo_state(can_undo, can_redo)

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
        """导出基于编辑器当前数据的图片（使用编辑器的蒙版和样式设置）"""
        try:
            image = self._get_current_image()
            regions = self._get_regions()
            source_path = self.model.get_source_image_path()
            
            if not image:
                self.logger.warning("Cannot export: missing image data")
                if hasattr(self, 'toast_manager'):
                    self.toast_manager.show_error("导出失败：缺少图像数据")
                return
            
            # regions 可以为空列表，此时导出原图（可能经过上色/超分处理）
            if regions is None:
                regions = []

            mask = self.model.get_refined_mask()
            if mask is None:
                mask = self.model.get_raw_mask()
            # 如果没有区域，mask 可以为 None，后端会处理
            if mask is None and regions:
                self.logger.warning("Cannot export: no mask data available for regions")
                if hasattr(self, 'toast_manager'):
                    self.toast_manager.show_error("导出失败：没有可用的蒙版数据")
                return

            # 显示开始Toast，保存引用以便后续关闭
            self._export_toast = None
            if hasattr(self, 'toast_manager'):
                self._export_toast = self.toast_manager.show_info("正在导出...", duration=0)

            image_snapshot = self._snapshot_image_for_export(image, "base image")
            inpainted_snapshot = self._snapshot_image_for_export(
                self.model.get_inpainted_image(),
                "inpainted image",
            )
            regions_snapshot = copy.deepcopy(regions)
            mask_snapshot = None if mask is None else np.array(mask, copy=True)

            self.async_service.submit_task(
                self._async_export_with_desktop_ui_service(
                    image_snapshot,
                    regions_snapshot,
                    mask_snapshot,
                    source_path,
                    inpainted_snapshot,
                )
            )
        except Exception as e:
            self.logger.error(f"Error during export request: {e}", exc_info=True)
            if hasattr(self, 'toast_manager'):
                self.toast_manager.show_error("导出失败")

    @staticmethod
    def _apply_white_frame_center(region: dict):
        """若存在有效框局部坐标，将其中心世界坐标覆盖写入 region['center']。

        region_data 里 center 是旋转中心，white_frame_rect_local 是以 center
        为原点、angle 为旋转角度的局部坐标 [left, top, right, bottom]。
        白框中心世界坐标 = center + local_to_world(wf_cx, wf_cy)。
        """
        wf_local = EditorController._resolve_effective_box_local(region)
        base_center = region.get('center')
        if not (
            isinstance(wf_local, (list, tuple)) and len(wf_local) == 4 and
            isinstance(base_center, (list, tuple)) and len(base_center) >= 2
        ):
            return
        try:
            left, top, right, bottom = (float(v) for v in wf_local)
            lx = (left + right) / 2.0
            ly = (top + bottom) / 2.0
            cx, cy = float(base_center[0]), float(base_center[1])
            angle = float(region.get('angle') or 0.0)
            rad = math.radians(angle)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            region['center'] = [cx + lx * cos_a - ly * sin_a,
                                 cy + lx * sin_a + ly * cos_a]
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _resolve_effective_box_local(region: dict):
        if not isinstance(region, dict):
            return None

        custom_box = region.get('white_frame_rect_local')
        render_box = region.get('render_box_rect_local')
        has_custom = bool(region.get('has_custom_white_frame', False))

        if isinstance(render_box, (list, tuple)) and len(render_box) == 4:
            return render_box
        if isinstance(custom_box, (list, tuple)) and len(custom_box) == 4 and has_custom:
            return custom_box
        if isinstance(custom_box, (list, tuple)) and len(custom_box) == 4:
            return custom_box
        return None

    def _resolve_editor_json_path(self, source_path: str) -> str:
        """解析编辑器当前图片对应的 JSON 路径。"""
        json_path = find_json_path(source_path)
        if not json_path:
            json_path = get_json_path(source_path, create_dir=True)
            self.logger.info(f"No existing JSON found, will create new one at: {json_path}")
        else:
            self.logger.info(f"Found existing JSON, will replace: {json_path}")
        return json_path

    def _save_current_inpainted_image(
        self,
        source_path: str,
        config_dict: dict,
        mask: Optional[np.ndarray],
        current_inpainted_image: Optional[Image.Image] = None,
        has_regions: bool = False,
    ) -> None:
        """将当前修复图同步到工作目录，确保下次打开时能复用。"""
        try:
            image_to_save = current_inpainted_image or self.model.get_inpainted_image()
            if image_to_save is None:
                # 导出时若修复预览尚未就绪，绝不能把原图误存为 inpainted。
                if mask is not None or has_regions:
                    existing_inpainted_path = find_inpainted_path(source_path)
                    if existing_inpainted_path and os.path.exists(existing_inpainted_path):
                        self.logger.info(
                            f"No live inpainted preview during export, keep existing inpainted image: {existing_inpainted_path}"
                        )
                    else:
                        self.logger.warning(
                            f"Skipped updating inpainted image during export because no inpainted preview is available yet: {source_path}"
                        )
                    return
                image_to_save = self.model.get_image()
            if image_to_save is None:
                return

            inpainted_path = get_inpainted_path(source_path, create_dir=True)
            save_quality = config_dict.get('cli', {}).get('save_quality', 95)

            if isinstance(image_to_save, Image.Image):
                save_image = image_to_save.copy()
            else:
                save_image = Image.fromarray(np.array(image_to_save))

            save_kwargs = {}
            if inpainted_path.lower().endswith(('.jpg', '.jpeg')):
                if save_image.mode in ('RGBA', 'LA'):
                    save_image = save_image.convert('RGB')
                save_kwargs['quality'] = save_quality
            elif inpainted_path.lower().endswith('.webp'):
                save_kwargs['quality'] = save_quality

            save_image.save(inpainted_path, **save_kwargs)

            if self._is_same_source_image(self.model.get_source_image_path(), source_path):
                self.model.set_inpainted_image_path(inpainted_path)
                self.resource_manager.set_cache(
                    self.CACHE_LAST_INPAINTED,
                    np.array(save_image.convert('RGB'))
                )
                if mask is not None:
                    mask_to_cache = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY) if len(mask.shape) == 3 else mask
                    self.resource_manager.set_cache(self.CACHE_LAST_MASK, np.array(mask_to_cache, copy=True))
            else:
                self.logger.debug("Skipped runtime inpaint cache update because active image changed during export")

            self.logger.info(f"已更新修复图片: {inpainted_path}")
        except Exception as e:
            self.logger.warning(f"更新inpainted图片失败: {e}")

    def _persist_editor_state_for_export(
        self,
        export_service,
        source_path: str,
        regions: list,
        mask: Optional[np.ndarray],
        config_dict: dict,
        inpainted_image: Optional[Image.Image] = None,
    ) -> str:
        """导出图片前同步当前编辑器状态到 JSON 和工作目录资源。"""
        json_path = self._resolve_editor_json_path(source_path)

        json_regions = [dict(region) for region in regions]
        for region in json_regions:
            self._apply_white_frame_center(region)
        export_service._save_regions_data_with_path(json_regions, json_path, source_path, mask, config_dict)

        self._save_current_inpainted_image(
            source_path,
            config_dict,
            mask,
            current_inpainted_image=inpainted_image,
            has_regions=bool(regions),
        )
        return json_path

    async def _async_export_with_desktop_ui_service(self, image, regions, mask, source_path=None, inpainted_image=None):
        """使用desktop-ui导出服务进行异步导出"""
        try:
            import os

            from PyQt6.QtCore import QTimer
            from PyQt6.QtWidgets import QMessageBox

            # 获取配置
            config = self.config_service.get_config()

            # 确定输出路径和文件名
            save_to_source_dir = getattr(config.cli, 'save_to_source_dir', False) if hasattr(config, 'cli') else False
            
            if save_to_source_dir and source_path:
                # 输出到原图所在目录的 manga_translator_work/result 子目录
                output_dir = os.path.join(os.path.dirname(source_path), 'manga_translator_work', 'result')
                os.makedirs(output_dir, exist_ok=True)
            else:
                # 原有逻辑：使用配置的输出目录
                output_dir = getattr(config.app, 'last_output_path', None) if hasattr(config, 'app') else None
                if not output_dir or not os.path.exists(output_dir):
                    if source_path:
                        output_dir = os.path.dirname(source_path)
                    else:
                        output_dir = os.getcwd()

            # 生成输出文件名（保持原文件名和格式）
            if source_path:
                base_name = os.path.splitext(os.path.basename(source_path))[0]
                # 获取输出格式
                output_format = getattr(config.cli, 'format', '') if hasattr(config, 'cli') else ''
                if output_format == "不指定":
                    output_format = None

                if output_format and output_format.strip():
                    output_filename = f"{base_name}.{output_format.lower()}"
                else:
                    original_ext = os.path.splitext(source_path)[1].lower()
                    output_filename = f"{base_name}{original_ext}" if original_ext else f"{base_name}.png"
            else:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_filename = f"exported_image_{timestamp}.png"

            output_path = os.path.join(output_dir, output_filename)


            # 使用本地desktop_qt_ui的导出服务
            import os
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from services.export_service import ExportService
            export_service = ExportService()

            # 转换配置为字典
            if hasattr(config, 'model_dump'):
                config_dict = config.model_dump()
            elif hasattr(config, 'dict'):
                config_dict = config.dict()
            else:
                config_dict = {}

            persisted_json_path = None
            if source_path:
                persisted_json_path = self._persist_editor_state_for_export(
                    export_service=export_service,
                    source_path=source_path,
                    regions=regions,
                    mask=mask,
                    config_dict=config_dict,
                    inpainted_image=inpainted_image,
                )
            else:
                self.logger.warning("Exporting without source image path, skipped JSON persistence")

            def progress_callback(message):
                pass

            def success_callback(message):
                # 使用信号在主线程显示Toast
                success_message = f"导出成功\n{output_path}"
                if persisted_json_path:
                    success_message += "\n已同步 JSON"
                self._show_toast_signal.emit(success_message, 5000, True, output_path)

                if self._is_same_source_image(self.model.get_source_image_path(), source_path):
                    # 保存导出快照，用于检测后续是否有更改
                    self._save_export_snapshot()

                    # 导出成功后释放内存
                    self.resource_manager.release_memory_after_export()
                    self.resource_manager.release_image_cache_except_current()
                else:
                    self.logger.debug("Skipped export snapshot update because active image changed during export")

            def error_callback(message):
                self.logger.error(f"Export error: {message}")
                # 使用信号在主线程显示Toast
                self._show_toast_signal.emit(f"导出失败：{message}", 5000, False, "")
            
            # 强制开启AI断句模式，保留用户手动编辑的换行符
            if 'render' not in config_dict:
                config_dict['render'] = {}
            config_dict['render']['disable_auto_wrap'] = True
            
            # 强制开启AI断句模式，确保用户手动编辑的换行符被保留
            if 'render' not in config_dict:
                config_dict['render'] = {}
            config_dict['render']['disable_auto_wrap'] = True
            


            # 确保区域数据包含渲染所需的所有信息
            enhanced_regions = []
            for i, region in enumerate(regions):
                enhanced_region = region.copy()

                # 确保有翻译文本（兼容多语言 dict 格式，渲染前展开为字符串）
                trans_val = enhanced_region.get('translation')
                if isinstance(trans_val, dict):
                    lang_key = enhanced_region.get('target_lang', '')
                    trans_val = trans_val.get(lang_key) or next(iter(trans_val.values()), '')
                    enhanced_region['translation'] = trans_val
                if not enhanced_region.get('translation'):
                    enhanced_region['translation'] = enhanced_region.get('text', '')

                # 确保有字体大小
                if not enhanced_region.get('font_size'):
                    enhanced_region['font_size'] = 16

                # 确保有对齐方式
                if not enhanced_region.get('alignment'):
                    enhanced_region['alignment'] = 'center'

                # 确保有方向
                if not enhanced_region.get('direction'):
                    enhanced_region['direction'] = 'auto'

                # 白框编辑后：将白框中心世界坐标覆盖到 center，确保后端渲染位置与预览一致
                self._apply_white_frame_center(enhanced_region)

                # 从渲染参数服务获取完整的渲染参数
                from services import get_render_parameter_service
                render_service = get_render_parameter_service()
                render_params = render_service.export_parameters_for_backend(i, enhanced_region)
                enhanced_region.update(render_params)

                enhanced_regions.append(enhanced_region)

            # 调用本地的导出服务
            export_service.export_rendered_image(
                image=image,
                regions_data=enhanced_regions,  # 使用增强的区域数据
                config=config_dict,
                output_path=output_path,
                mask=mask,
                progress_callback=progress_callback,
                success_callback=success_callback,
                error_callback=error_callback,
                source_image_path=source_path,  # 传递原图路径用于PSD导出
                editor_inpainted_image=inpainted_image
            )

        except Exception as e:
            self.logger.error(f"Error during async export: {e}", exc_info=True)
            err_msg = str(e)
            QTimer.singleShot(0, lambda: QMessageBox.critical(None, "导出失败", f"导出过程中发生意外错误:\n{err_msg}"))

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
        if self.view and hasattr(self.view, "set_compare_mode"):
            self.view.set_compare_mode(compare_enabled)
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
        selected_regions_data = [all_regions[i] for i in selected_indices]
        
        # 显示开始Toast，保存引用以便后续关闭
        self._ocr_toast = None
        if hasattr(self, 'toast_manager'):
            self._ocr_toast = self.toast_manager.show_info("正在识别...", duration=0)
        
        self.async_service.submit_task(self._async_ocr_task(image, selected_regions_data, selected_indices))

    @pyqtSlot(list)
    def on_regions_update_finished(self, updated_regions: list):
        """Slot to safely update regions from the main thread."""
        # 直接使用 set_regions，它会自动同步到 resource_manager
        self.model.set_regions(updated_regions)
        
        # 强制刷新属性栏（忽略焦点状态）
        if hasattr(self, 'view') and self.view and hasattr(self.view, 'property_panel'):
            self.view.property_panel.force_refresh_from_model()
    
    @pyqtSlot()
    def _on_ocr_completed(self):
        """OCR完成后在主线程处理Toast"""
        # 关闭"正在识别"Toast
        if hasattr(self, '_ocr_toast') and self._ocr_toast:
            try:
                self._ocr_toast.close()
                self._ocr_toast = None
            except Exception:
                pass
        
        # 显示完成Toast
        if hasattr(self, 'toast_manager'):
            self.toast_manager.show_success("识别完成")
    
    @pyqtSlot()
    def _on_translation_completed(self):
        """翻译完成后在主线程处理Toast"""
        # 关闭"正在翻译"Toast
        if hasattr(self, '_translation_toast') and self._translation_toast:
            try:
                self._translation_toast.close()
                self._translation_toast = None
            except Exception:
                pass
        
        # 显示完成Toast
        if hasattr(self, 'toast_manager'):
            self.toast_manager.show_success("翻译完成")

    async def _async_ocr_task(self, image, regions_to_process, indices):
        current_regions = self.model.get_regions()
        updated_regions = list(current_regions) # Create a shallow copy of the list

        # 从属性面板获取用户选择的OCR配置
        ocr_config = None
        if self.view and hasattr(self.view, 'property_panel'):
            selected_ocr = self.view.property_panel.get_selected_ocr_model()
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

        success_count = 0
        for i, region_data in enumerate(regions_to_process):
            region_idx = indices[i]
            try:
                ocr_result = await self.ocr_service.recognize_region(image, region_data, config=ocr_config)
                if ocr_result and ocr_result.text:
                    # Create a copy of the specific region dict to modify
                    new_region_data = updated_regions[region_idx].copy()
                    new_region_data['text'] = ocr_result.text
                    updated_regions[region_idx] = new_region_data # Replace the old dict with the new one
                    success_count += 1
            except Exception as e:
                self.logger.error(f"OCR识别失败: {e}")

        # Emit a signal to have the model updated on the main thread
        self._regions_update_finished.emit(updated_regions)
        
        # 发送OCR完成信号（在主线程处理Toast）
        self._ocr_completed.emit()
        

    @pyqtSlot()
    def run_translation_for_selection(self):
        selected_indices = self.model.get_selection()
        if not selected_indices:
            return
        image = self._get_current_image()
        if not image:
            return

        all_regions = self.model.get_regions()
        selected_regions_data = [all_regions[i] for i in selected_indices]
        texts_to_translate = [r.get('text', '') for r in selected_regions_data]
        
        # 显示开始Toast，保存引用以便后续关闭
        self._translation_toast = None
        if hasattr(self, 'toast_manager'):
            self._translation_toast = self.toast_manager.show_info("正在翻译...", duration=0)
        
        # 传递所有区域以提供上下文，但只翻译选中的文本
        self.async_service.submit_task(self._async_translation_task(texts_to_translate, selected_indices, image, all_regions))

    async def _async_translation_task(self, texts, indices, image, regions):
        # 从属性面板获取用户选择的翻译器配置
        translator_to_use = None
        target_lang_to_use = None
        
        if self.view and hasattr(self.view, 'property_panel'):
            selected_translator = self.view.property_panel.get_selected_translator()
            selected_target_lang = self.view.property_panel.get_selected_target_language()
            
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
        success_count = 0
        try:
            results = await self.translation_service.translate_text_batch(
                texts, 
                translator=translator_to_use,
                target_lang=target_lang_to_use,
                image=image, 
                regions=regions
            )
            # 重新获取最新的区域数据，避免覆盖其他修改
            current_regions = self.model.get_regions()
            updated_regions = list(current_regions) # Create a shallow copy

            for i, result in enumerate(results):
                if result and result.translated_text:
                    region_idx = indices[i]
                    # Create a copy of the specific region dict to modify
                    new_region_data = updated_regions[region_idx].copy()
                    new_region_data['translation'] = result.translated_text
                    updated_regions[region_idx] = new_region_data # Replace the old dict
                    success_count += 1

            # Emit a signal to have the model updated on the main thread
            self._regions_update_finished.emit(updated_regions)
            
            # 发送翻译完成信号
            self._translation_completed.emit()
        except Exception as e:
            self.logger.error(f"翻译失败: {e}")
            # TODO: 添加翻译失败的信号处理

    @pyqtSlot(list)
    def set_selection_from_list(self, indices: list):
        """Slot to handle selection changes originating from the RegionListView."""
        self.model.set_selection(indices)

