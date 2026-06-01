from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem

from .graphics_items import RegionTextItem
from .image_utils import image_like_to_qimage


class GraphicsViewLayersMixin:
    def _scale_mask_item(self, mask_item: QGraphicsPixmapItem):
        """将覆盖层缩放到与底图一致的场景尺寸。"""
        if not self._image_item or not mask_item:
            return

        img_rect = self._image_item.boundingRect()
        mask_rect = mask_item.boundingRect()

        if mask_rect.width() > 0 and mask_rect.height() > 0:
            scale_x = img_rect.width() / mask_rect.width()
            scale_y = img_rect.height() / mask_rect.height()
            transform = QTransform()
            transform.scale(scale_x, scale_y)
            mask_item.setTransform(transform)

    def clear_all_state(self):
        """清空所有状态,包括items、缓存、计时器"""
        self.selection_manager.suppress_forward_sync(True)
        try:
            self._reset_drawing_state()
            if self.render_debounce_timer.isActive():
                self.render_debounce_timer.stop()

            for item in list(self._region_items):
                try:
                    if item and item.scene():
                        self.scene.removeItem(item)
                except (RuntimeError, AttributeError):
                    pass
            self._region_items.clear()

            if self._image_item and self._image_item.scene():
                self.scene.removeItem(self._image_item)
                self._image_item = None

            self.overlay_layers.clear()
            self._q_image_ref = None

            self.mask_layer.clear()

            if self._textbox_preview_item and self._textbox_preview_item.scene():
                self.scene.removeItem(self._textbox_preview_item)
                self._textbox_preview_item = None

            if self._preview_item and self._preview_item.scene():
                self.scene.removeItem(self._preview_item)
                self._preview_item = None

            self.selection_manager.clear_state()
            self.render_coordinator.reset()
            self._is_drawing = False
            self._is_drawing_textbox = False
            self._clear_pending_geometry_edits()

            if hasattr(self, "_render_executor"):
                try:
                    self._render_executor.shutdown(wait=False)
                    del self._render_executor
                except Exception:
                    pass
        except (RuntimeError, AttributeError) as e:
            self.logger.warning("Error during clear_all_state: %s", e)
        finally:
            self.selection_manager.suppress_forward_sync(False)

    def on_image_changed(self, image):
        """切图: 复用 _image_item + 用 LRU 里的预转 QImage,主线程零阻塞、无中间帧。

        关键技巧:
        - 切前先 scene.removeItem(_image_item) 把它卸离 scene; clear_all_state 里
          `if X and X.scene()` 守卫会让它既不被 removeItem 也不被置 None,引用保留
        - QImage 优先从 ResourceManager._current_image.qimage 取(走 LRU,A/D 来回切换瞬时);
          缺失则同步 fallback 转换
        - setUpdatesEnabled(False/True) 包裹整个切换,viewport 不出中间帧
        """
        self.setUpdatesEnabled(False)
        try:
            # 1) 把 _image_item 暂时从 scene 卸下(clear_all_state 不会动它)
            keep = self._image_item
            if keep is not None and keep.scene() is self.scene:
                self.scene.removeItem(keep)

            # 2) 复用原版全清逻辑(它有 `if X and X.scene()` 守卫,detached 的 keep 不受影响)
            self.clear_all_state()
            self._image_item = keep   # 显式恢复(clear_all_state 因条件不满足未清掉)

            self.render_coordinator.invalidate_document(self.model.get_document_revision())

            if image is None:
                if self._image_item is not None and self._image_item.scene() is self.scene:
                    self.scene.removeItem(self._image_item)
                self._image_item = None
                self._q_image_ref = None
                self._render_update_immediate_once = False
                return

            # 3) 优先用 LRU 缓存的预转 QImage(主线程零阻塞)
            qimage = None
            try:
                resource_mgr = getattr(self.controller, "resource_manager", None) if hasattr(self, "controller") else None
                current_resource = getattr(resource_mgr, "_current_image", None) if resource_mgr else None
                if current_resource is not None:
                    qimage = getattr(current_resource, "qimage", None)
            except Exception:
                qimage = None
            if qimage is None:
                try:
                    qimage = image_like_to_qimage(image)
                except Exception as convert_error:
                    self.logger.warning("Failed to convert image to QImage: %s", convert_error)
            if qimage is None:
                if self._image_item is not None and self._image_item.scene() is self.scene:
                    self.scene.removeItem(self._image_item)
                self._image_item = None
                self._q_image_ref = None
                return

            self._q_image_ref = qimage
            pixmap = QPixmap.fromImage(qimage)

            # 4) 原地复用旧 item;若已无 item 才新建
            if self._image_item is not None:
                self._image_item.setPixmap(pixmap)
                self.scene.addItem(self._image_item)   # 重新加回 scene
            else:
                self._image_item = self.scene.addPixmap(pixmap)
                self._image_item.setZValue(2)

            self._image_item.setOpacity(self.model.get_original_image_alpha())
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._emit_view_state_changed()
            self._render_update_immediate_once = True
        finally:
            self.setUpdatesEnabled(True)

    @pyqtSlot(float)
    def on_original_image_alpha_changed(self, alpha: float):
        if self._image_item:
            self._image_item.setOpacity(alpha)

    @pyqtSlot(int)
    def on_region_style_updated(self, region_index: int):
        self._perform_single_item_update(region_index)

    def on_region_display_mode_changed(self, mode: str):
        for item in self.scene.items():
            if isinstance(item, RegionTextItem):
                if mode == "full":
                    item.setVisible(True)
                    item.set_text_visible(True)
                    item.set_box_visible(True)
                    item.set_white_box_visible(True)
                elif mode == "text_only":
                    item.setVisible(True)
                    item.set_text_visible(True)
                    item.set_box_visible(False)
                    item.set_white_box_visible(False)
                elif mode == "box_only":
                    item.setVisible(True)
                    item.set_text_visible(False)
                    item.set_box_visible(True)
                    item.set_white_box_visible(True)
                elif mode == "none":
                    item.setVisible(False)
                    item.set_white_box_visible(False)
