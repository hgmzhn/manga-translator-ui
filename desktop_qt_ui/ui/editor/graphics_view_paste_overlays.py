"""
贴片（paste overlay）画布层：显示项、模型同步与拖放导入。

坐标系约定：场景单位 == 源图像素（基图 item 未缩放）；贴片的 ``center_x/y``、
``width/height`` 均为源图分辨率数值，直接用于场景摆放。
z 序：贴片基值 50（在基图 2 / 修复预览之上、region 文本框 100 之下），
同一页多个贴片按各自 ``z`` 字段在上层继续堆叠。

本模块只做“渲染同步”，改动数据一律走 EditorController（可撤销）。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QImage, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem

from editor.paste_overlay_state import (
    png_base64_to_rgba_overlay,
    rgba_overlay_to_png_base64,
)

_PASTE_BASE_Z = 50
_PASTE_MAX_DIMENSION = 2048
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _rgba_to_qimage(rgba):
    height, width = rgba.shape[:2]
    image = QImage(
        rgba.data,
        width,
        height,
        rgba.strides[0],
        QImage.Format.Format_RGBA8888,
    )
    return image.copy()


class PasteOverlayItem(QGraphicsPixmapItem):
    """单个贴片的可视项：由 overlay 字典驱动 pixmap/几何/透明度。"""

    def __init__(self, overlay: dict):
        super().__init__()
        self.overlay = dict(overlay)
        self._rebuild()

    def update_overlay(self, overlay: dict) -> None:
        self.overlay = dict(overlay)
        self._rebuild()

    def _rebuild(self) -> None:
        overlay = self.overlay
        pixmap = QPixmap()
        image_b64 = overlay.get("image", "")
        if image_b64:
            rgba = png_base64_to_rgba_overlay(image_b64)
            if rgba is not None:
                qimage = _rgba_to_qimage(rgba)
                if not qimage.isNull():
                    raw = QPixmap.fromImage(qimage)
                    if not raw.isNull():
                        pixmap = raw
        self.setPixmap(pixmap)
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        overlay = self.overlay
        pixmap = self.pixmap()
        width = pixmap.width()
        height = pixmap.height()
        if width <= 0 or height <= 0 or pixmap.isNull():
            self.setVisible(False)
            return

        target_width = max(1, int(round(float(overlay.get("width", width)))))
        target_height = max(1, int(round(float(overlay.get("height", height)))))
        if width != target_width or height != target_height:
            pixmap = pixmap.scaled(
                target_width,
                target_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(pixmap)
            width, height = target_width, target_height

        center_x = float(overlay.get("center_x", 0.0))
        center_y = float(overlay.get("center_y", 0.0))
        rotation = float(overlay.get("rotation", 0.0))
        flip_h = -1.0 if overlay.get("flip_h") else 1.0
        flip_v = -1.0 if overlay.get("flip_v") else 1.0

        transform = QTransform()
        transform.translate(center_x, center_y)
        transform.rotate(rotation)
        transform.scale(flip_h, flip_v)
        transform.translate(-width / 2.0, -height / 2.0)
        self.setTransform(transform)

        try:
            opacity = float(overlay.get("opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        self.setOpacity(max(0.0, min(1.0, opacity)))
        self.setZValue(_PASTE_BASE_Z + int(overlay.get("z", 0)))
        self.setVisible(bool(overlay.get("visible", True)))

    def overlay_id(self) -> str:
        return str(self.overlay.get("id", ""))


class GraphicsViewPasteOverlayMixin:
    """画布贴片同步：监听模型信号重建可视项，并支持 PNG 拖放导入。"""

    def _rebuild_paste_overlay_items(self) -> None:
        items = getattr(self, "_paste_overlay_items", None)
        if items is None:
            return
        for item in list(items):
            try:
                if item.scene():
                    self.scene.removeItem(item)
            except (RuntimeError, AttributeError):
                pass
        items.clear()

        if self._image_item is None:
            return
        overlays = self.model.get_paste_overlays() if self.model else []
        for overlay in overlays:
            item = PasteOverlayItem(overlay)
            self.scene.addItem(item)
            items.append(item)
        self.scene.update()

    def _clear_paste_overlay_items(self) -> None:
        items = getattr(self, "_paste_overlay_items", None)
        if items is None:
            return
        for item in list(items):
            try:
                if item.scene():
                    self.scene.removeItem(item)
            except (RuntimeError, AttributeError):
                pass
        items.clear()

    def on_paste_overlays_changed(self, overlays=None) -> None:
        self._rebuild_paste_overlay_items()

    # --- PNG 拖放导入 ---
    def _dropped_image_paths(self, event):
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if url is None or not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if os.path.splitext(path)[1].lower() in _IMAGE_SUFFIXES:
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):  # noqa: N802 - Qt API naming
        if self._image_item is None or not self._dropped_image_paths(event):
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802 - Qt API naming
        if self._image_item is None or not self._dropped_image_paths(event):
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 - Qt API naming
        if self._image_item is None:
            event.ignore()
            return
        controller = getattr(self, "controller", None)
        if controller is None or getattr(self.model, "get_source_image_path", None) is None:
            event.ignore()
            return
        paths = self._dropped_image_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()

        scene_pos = self.mapToScene(event.position().toPoint())
        try:
            from PIL import Image

            import numpy as np

            image_path = paths[0]
            rgba = None
            with Image.open(image_path) as source:
                source.load()
                rgba = source.convert("RGBA")
                array = np.array(rgba, dtype=np.uint8, copy=True)
            if rgba is not None and getattr(rgba, "close", None):
                rgba.close()
            if array.ndim != 3 or array.shape[2] != 4:
                event.ignore()
                return
            height, width = array.shape[:2]
            longest = max(width, height)
            if longest > _PASTE_MAX_DIMENSION:
                import cv2

                scale = _PASTE_MAX_DIMENSION / longest
                array = cv2.resize(
                    array,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            overlay = {
                "name": os.path.basename(image_path),
                "center_x": float(scene_pos.x()),
                "center_y": float(scene_pos.y()),
                "width": float(array.shape[1]),
                "height": float(array.shape[0]),
                "image": rgba_overlay_to_png_base64(array),
            }
            controller.add_paste_overlay(overlay)
        except Exception as error:
            self.logger.warning("导入贴片失败: %s", error)
