from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem

from .graphics_items import RegionTextItem, TransparentPixmapItem
from .image_utils import build_display_image_frame, image_like_to_qimage


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

            if self._inpainted_image_item and self._inpainted_image_item.scene():
                self.scene.removeItem(self._inpainted_image_item)
                self._inpainted_image_item = None
            self._q_image_ref = None
            self._inpainted_q_image_ref = None

            if self._paint_overlay_item and self._paint_overlay_item.scene():
                self.scene.removeItem(self._paint_overlay_item)
                self._paint_overlay_item = None
            self._paint_overlay_q_image_ref = None

            if self._raw_mask_item and self._raw_mask_item.scene():
                self.scene.removeItem(self._raw_mask_item)
                self._raw_mask_item = None

            if self._refined_mask_item and self._refined_mask_item.scene():
                self.scene.removeItem(self._refined_mask_item)
                self._refined_mask_item = None

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
        self.render_coordinator.invalidate_document(self.model.get_document_revision())

        # 先构建新图 QPixmap，旧图保留不动
        new_pixmap = None
        new_qimage = None
        if image is not None:
            try:
                new_qimage = image_like_to_qimage(image)
                new_pixmap = QPixmap.fromImage(new_qimage)
            except Exception as convert_error:
                self.logger.warning("Failed to convert image to QImage: %s", convert_error)

        # 清理旧状态（保留 _image_item，直接换图避免黑屏）
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
            for attr in ('_raw_mask_item', '_refined_mask_item',
                         '_inpainted_image_item', '_paint_overlay_item',
                         '_preview_item', '_textbox_preview_item'):
                item = getattr(self, attr, None)
                if item and item.scene():
                    self.scene.removeItem(item)
                setattr(self, attr, None)
            self._inpainted_q_image_ref = None
            self._paint_overlay_q_image_ref = None
            self.render_coordinator.reset()
            self._clear_pending_geometry_edits()
        finally:
            self.selection_manager.suppress_forward_sync(False)
        self.selection_manager.on_scene_cleared()

        if new_pixmap is None:
            if self._image_item:
                self._image_item.setPixmap(QPixmap())
            return

        # 原地换图：旧 item 直接 setPixmap，不删不加
        self._q_image_ref = new_qimage
        if self._image_item and self._image_item.scene():
            self._image_item.setPixmap(new_pixmap)
        else:
            self._image_item = self.scene.addPixmap(new_pixmap)
            self._image_item.setZValue(2)
        self._image_item.setOpacity(self.model.get_original_image_alpha())
        self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._emit_view_state_changed()
        self.on_regions_changed(self.model.get_regions())

    def on_mask_data_changed(self, mask_type: str, mask_array: np.ndarray):
        target_item = self._raw_mask_item if mask_type == "raw" else self._refined_mask_item

        if mask_array is None or mask_array.size == 0:
            if target_item:
                target_item.setPixmap(QPixmap())
            return

        h, w = mask_array.shape[:2]
        color_mask = np.zeros((h, w, 4), dtype=np.uint8)
        color_mask[mask_array > 0] = [255, 0, 0, 128]
        display_frame = build_display_image_frame(color_mask, max_pixels=self.MASK_PREVIEW_MAX_PIXELS)
        if display_frame is None:
            return
        pixmap = QPixmap.fromImage(display_frame.qimage)

        if target_item is None or target_item.scene() is None:
            if mask_type == "raw":
                if self._raw_mask_item and self._raw_mask_item.scene():
                    self.scene.removeItem(self._raw_mask_item)
                self._raw_mask_item = TransparentPixmapItem()
                self._raw_mask_item.setPixmap(pixmap)
                self._raw_mask_item.setZValue(10)
                self.scene.addItem(self._raw_mask_item)
                self._scale_mask_item(self._raw_mask_item)
                self._raw_mask_item.setVisible(self.model.get_display_mask_type() == "raw")
                target_item = self._raw_mask_item
            else:
                if self._refined_mask_item and self._refined_mask_item.scene():
                    self.scene.removeItem(self._refined_mask_item)
                self._refined_mask_item = TransparentPixmapItem()
                self._refined_mask_item.setPixmap(pixmap)
                self._refined_mask_item.setZValue(11)
                self.scene.addItem(self._refined_mask_item)
                self._scale_mask_item(self._refined_mask_item)
                self._refined_mask_item.setVisible(self.model.get_display_mask_type() == "refined")
                target_item = self._refined_mask_item
        else:
            target_item.setPixmap(pixmap)
            self._scale_mask_item(target_item)

        self.scene.update()
        self.viewport().update()
        self.update()

        current_display_type = self.model.get_display_mask_type()
        if mask_type == current_display_type and target_item:
            target_item.setVisible(True)

    def on_display_mask_type_changed(self, mask_type: str):
        if mask_type == "raw" and self._raw_mask_item is None and self.model.get_raw_mask() is not None:
            self.on_mask_data_changed("raw", self.model.get_raw_mask())

        if mask_type == "refined" and self._refined_mask_item is None and self.model.get_refined_mask() is not None:
            self.on_mask_data_changed("refined", self.model.get_refined_mask())

        if self._raw_mask_item:
            self._raw_mask_item.setVisible(mask_type == "raw")
        if self._refined_mask_item:
            self._refined_mask_item.setVisible(mask_type == "refined")

        self.scene.update()
        self.viewport().update()
        self.update()
        self.repaint()

    def on_inpainted_image_changed(self, image):
        if self._image_item is None:
            return

        if image is None:
            if self._inpainted_image_item:
                self._inpainted_image_item.setVisible(False)
            self._inpainted_q_image_ref = None
            return

        try:
            display_frame = build_display_image_frame(image, max_pixels=self.INPAINT_PREVIEW_MAX_PIXELS)
            if display_frame is None:
                raise ValueError("display frame is empty")
            self._inpainted_q_image_ref = display_frame.qimage
        except Exception as convert_error:
            self.logger.warning("Failed to convert inpainted image to QImage: %s", convert_error)
            self._inpainted_q_image_ref = None
            if self._inpainted_image_item:
                self._inpainted_image_item.setVisible(False)
            return

        pixmap = QPixmap.fromImage(self._inpainted_q_image_ref)
        if self._inpainted_image_item is None:
            self._inpainted_image_item = TransparentPixmapItem()
            self._inpainted_image_item.setPixmap(pixmap)
            self._inpainted_image_item.setZValue(1)
            self._inpainted_image_item.setOpacity(1.0)
            self.scene.addItem(self._inpainted_image_item)
        else:
            self._inpainted_image_item.setPixmap(pixmap)
            self._inpainted_image_item.setOpacity(1.0)

        self._scale_mask_item(self._inpainted_image_item)
        self._inpainted_image_item.setVisible(True)

    def on_paint_overlay_changed(self, overlay):
        """彩色画笔图层数据变化时刷新对应 pixmap。"""
        if self._image_item is None:
            return

        if overlay is None or getattr(overlay, "size", 0) == 0:
            if self._paint_overlay_item is not None:
                self._paint_overlay_item.setVisible(False)
                self._paint_overlay_item.setPixmap(QPixmap())
            self._paint_overlay_q_image_ref = None
            return

        try:
            display_frame = build_display_image_frame(overlay, max_pixels=self.INPAINT_PREVIEW_MAX_PIXELS)
            if display_frame is None:
                raise ValueError("display frame is empty")
            self._paint_overlay_q_image_ref = display_frame.qimage
        except Exception as convert_error:
            self.logger.warning("Failed to convert paint overlay to QImage: %s", convert_error)
            self._paint_overlay_q_image_ref = None
            if self._paint_overlay_item is not None:
                self._paint_overlay_item.setVisible(False)
            return

        pixmap = QPixmap.fromImage(self._paint_overlay_q_image_ref)
        if self._paint_overlay_item is None:
            self._paint_overlay_item = TransparentPixmapItem()
            self._paint_overlay_item.setPixmap(pixmap)
            # 位于修复图之上、文字区域之下
            self._paint_overlay_item.setZValue(5)
            self._paint_overlay_item.setOpacity(1.0)
            self.scene.addItem(self._paint_overlay_item)
        else:
            self._paint_overlay_item.setPixmap(pixmap)

        self._scale_mask_item(self._paint_overlay_item)
        self._paint_overlay_item.setVisible(True)
        self.scene.update()
        self.viewport().update()

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
