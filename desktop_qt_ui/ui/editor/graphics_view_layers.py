from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem

from editor.image_utils import image_like_to_qimage

from .graphics_items import RegionTextItem


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
            self._end_stroke(commit=False)
            self._abort_textbox_drawing()
            if self.render_debounce_timer.isActive():
                self.render_debounce_timer.stop()

            for item in list(self._region_items):
                try:
                    if item and item.scene():
                        self.scene.removeItem(item)
                except (RuntimeError, AttributeError):
                    pass
            self._region_items.clear()

            if self._image_item is not None:
                if self._image_item.scene() is not None:
                    self.scene.removeItem(self._image_item)
                self._image_item.setPixmap(QPixmap())
                self._image_item = None

            self.overlay_layers.clear()
            self._q_image_ref = None
            self._document_identity = None
            self._display_source_image_ref = None

            self.mask_layer.clear()

            if self._textbox_preview_item and self._textbox_preview_item.scene():
                self.scene.removeItem(self._textbox_preview_item)
                self._textbox_preview_item = None

            # 仿制印章：取样圈是场景顶层 item，取样点/偏移属于当前图片，
            # 切图时必须一并清掉，否则旧取样点会带到新图
            self._clone_sample_image_point = None
            self._clone_offset = None
            self._clear_clone_marker()

            self.selection_manager.clear_state()
            self.render_coordinator.reset()
            self._clear_pending_geometry_edits()
        except (RuntimeError, AttributeError) as e:
            self.logger.warning("Error during clear_all_state: %s", e)
        finally:
            self.selection_manager.suppress_forward_sync(False)

    def _apply_image_scene_rect(self):
        """换图时显式钉住 sceneRect（图片矩形适当外扩）。

        不能依赖隐式 sceneRect：它取 itemsBoundingRect 且只增不减，
        旋转辅助线等超长临时 item 会把滚动范围永久撑大。"""
        if self._image_item is None:
            self.scene.setSceneRect(QRectF())
            return
        rect = self._image_item.sceneBoundingRect()
        margin_x = max(rect.width() * 0.25, 64.0)
        margin_y = max(rect.height() * 0.25, 64.0)
        self.scene.setSceneRect(rect.adjusted(-margin_x, -margin_y, margin_x, margin_y))

    def on_display_layers_changed(self, layers):
        """Atomically install the current document's two base display layers."""
        current_identity = self.model.get_document_identity()
        if layers is None:
            # A queued clear from an older document must not erase a document
            # that has already been installed.
            if current_identity is not None:
                return
            self.setUpdatesEnabled(False)
            try:
                self.clear_all_state()
                self._apply_image_scene_rect()
            finally:
                self.setUpdatesEnabled(True)
            return

        incoming_identity = layers.identity
        if current_identity != incoming_identity:
            return

        is_new_document = self._document_identity != incoming_identity
        source_changed = (
            is_new_document
            or self._image_item is None
            or self._display_source_image_ref is not layers.source_image
        )

        self.setUpdatesEnabled(False)
        try:
            if is_new_document:
                self.clear_all_state()
                self._document_identity = incoming_identity

            if source_changed and not self._set_source_display_image(
                layers.source_image,
                incoming_identity,
            ):
                # Conversion failure is a hard clear: retaining either old
                # pixmap would misrepresent the active document.
                self.clear_all_state()
                self._apply_image_scene_rect()
                return

            if self.model.get_document_identity() != incoming_identity:
                return

            self.overlay_layers.inpainted.set_image(
                layers.inpaint_display_image,
                document_identity=incoming_identity,
            )
            if self._image_item is not None:
                self._image_item.setOpacity(layers.source_opacity)

            if source_changed:
                self._apply_image_scene_rect()
                if is_new_document and self._image_item is not None:
                    self.fitInView(
                        self._image_item,
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                    self._emit_view_state_changed()
        finally:
            self.setUpdatesEnabled(True)
            self.viewport().update()

    def _set_source_display_image(self, image, identity) -> bool:
        if image is None or self.model.get_document_identity() != identity:
            return False

        qimage = self.model.get_source_qimage()
        if qimage is None:
            try:
                qimage = image_like_to_qimage(image)
            except Exception as convert_error:
                self.logger.warning(
                    "Failed to convert source image to QImage: %s",
                    convert_error,
                )
                return False
        if qimage is None or qimage.isNull():
            return False

        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            return False
        if self.model.get_document_identity() != identity:
            return False

        self._q_image_ref = qimage
        self._display_source_image_ref = image
        if self._image_item is None:
            self._image_item = self.scene.addPixmap(pixmap)
            self._image_item.setZValue(2)
        else:
            self._image_item.setPixmap(pixmap)
            if self._image_item.scene() is None:
                self.scene.addItem(self._image_item)
            self._image_item.setZValue(2)
        return True

    def on_region_display_mode_changed(self, mode: str, *, render_missing: bool = True):
        if render_missing and mode in {"full", "text_only"}:
            for index, item in enumerate(self._region_items):
                text_item = getattr(item, "text_item", None)
                if text_item is not None and text_item.pixmap().isNull():
                    self._render_region_text_visual(index)

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
        self.scene.update()
