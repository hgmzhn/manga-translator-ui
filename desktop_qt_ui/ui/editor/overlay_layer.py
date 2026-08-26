from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QPixmap

from editor.image_utils import build_display_image_frame

from .graphics_items import TransparentPixmapItem

if TYPE_CHECKING:
    from .graphics_view import GraphicsView


class PixmapOverlayLayer:
    """单个透明 pixmap 覆盖层。"""

    def __init__(
        self,
        view: "GraphicsView",
        *,
        z_value: int,
        convert_warning: str,
        empty_when_zero_size: bool = False,
        update_scene: bool = False,
    ):
        self.view = view
        self.z_value = z_value
        self.convert_warning = convert_warning
        self.empty_when_zero_size = empty_when_zero_size
        self.update_scene = update_scene
        self.item: TransparentPixmapItem | None = None
        self.qimage_ref = None
        self.image_ref = None
        self.layer_visible = True

    def clear(self) -> None:
        if self.item and self.item.scene():
            self.view.scene.removeItem(self.item)
        self.item = None
        self.qimage_ref = None
        self.image_ref = None

    def set_image(self, image, *, document_identity=None) -> None:
        if document_identity is not None and (
            document_identity != self.view._document_identity
            or document_identity != self.view.model.get_document_identity()
        ):
            return
        if self.view._image_item is None:
            self.clear()
            return

        if (
            image is self.image_ref
            and self.item is not None
            and not self.item.pixmap().isNull()
        ):
            self.item.setVisible(self.layer_visible)
            return

        if self._is_empty(image):
            self._hide(clear_pixmap=True)
            self.qimage_ref = None
            self.image_ref = None
            return

        try:
            display_frame = build_display_image_frame(
                image,
                max_pixels=self.view.INPAINT_PREVIEW_MAX_PIXELS,
            )
            if display_frame is None:
                raise ValueError("display frame is empty")
            self.qimage_ref = display_frame.qimage
            self.image_ref = image
        except Exception as convert_error:
            self.view.logger.warning(self.convert_warning, convert_error)
            self.qimage_ref = None
            self.image_ref = None
            self._hide(clear_pixmap=True)
            return

        self._show_pixmap(QPixmap.fromImage(self.qimage_ref))

    def _is_empty(self, image) -> bool:
        if image is None:
            return True
        return self.empty_when_zero_size and getattr(image, "size", 0) == 0

    def _show_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self.qimage_ref = None
            self._hide(clear_pixmap=True)
            return
        item = self._ensure_item()
        item.setPixmap(pixmap)
        item.setOpacity(1.0)
        self.view._scale_mask_item(item)
        item.setVisible(self.layer_visible)
        if self.update_scene:
            self.view.scene.update()
            self.view.viewport().update()

    def _ensure_item(self):
        if self.item is not None:
            return self.item
        self.item = TransparentPixmapItem()
        self.item.setZValue(self.z_value)
        self.item.setOpacity(1.0)
        self.view.scene.addItem(self.item)
        return self.item

    def set_layer_visible(self, visible: bool) -> None:
        """仅切换显示，不清数据；重新 set_image 时也遵循该标志。"""
        self.layer_visible = bool(visible)
        if self.item is not None and not self.item.pixmap().isNull():
            self.item.setVisible(self.layer_visible)
            if self.update_scene:
                self.view.scene.update()
                self.view.viewport().update()

    def _hide(self, *, clear_pixmap: bool = False) -> None:
        if self.item is None:
            return
        self.item.setVisible(False)
        if clear_pixmap:
            self.item.setPixmap(QPixmap())


class OverlayLayerManager:
    """管理修复图和画笔图层。"""

    def __init__(self, view: "GraphicsView"):
        self.view = view
        self.inpainted = PixmapOverlayLayer(
            view,
            z_value=1,
            convert_warning="Failed to convert inpainted image to QImage: %s",
        )
        # 位于修复图之上、文字区域之下
        self.paint_overlay = PixmapOverlayLayer(
            view,
            z_value=5,
            convert_warning="Failed to convert paint overlay to QImage: %s",
            empty_when_zero_size=True,
            update_scene=True,
        )
        # 印章层位于画笔层之上
        self.stamp_overlay = PixmapOverlayLayer(
            view,
            z_value=6,
            convert_warning="Failed to convert stamp overlay to QImage: %s",
            empty_when_zero_size=True,
            update_scene=True,
        )

    def clear(self) -> None:
        self.inpainted.clear()
        self.paint_overlay.clear()
        self.stamp_overlay.clear()

    def on_inpaint_display_changed(self, document_identity, image) -> None:
        self.inpainted.set_image(
            image,
            document_identity=document_identity,
        )

    def on_paint_overlay_changed(self, overlay) -> None:
        """彩色画笔图层数据变化时刷新对应 pixmap。"""
        self.paint_overlay.set_image(overlay)

    def on_stamp_overlay_changed(self, overlay) -> None:
        """印章图层数据变化时刷新对应 pixmap。"""
        self.stamp_overlay.set_image(overlay)

    def set_paint_overlay_visible(self, visible: bool) -> None:
        self.paint_overlay.set_layer_visible(visible)

    def set_stamp_overlay_visible(self, visible: bool) -> None:
        self.stamp_overlay.set_layer_visible(visible)
