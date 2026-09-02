"""
贴片（paste overlay）画布层：显示项、模型同步与拖放导入。

坐标系约定：场景单位 == 源图像素（基图 item 未缩放）；贴片的 ``center_x/y``、
``width/height`` 均为源图分辨率数值，直接用于场景摆放。
z 序：贴片基值 50（在基图 2 / 修复预览之上、region 文本框 100 之下），
同一页多个贴片按各自 ``z`` 字段在上层继续堆叠。

本模块只做“渲染同步”，改动数据一律走 EditorController（可撤销）。
"""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
)

from editor.paste_overlay_state import (
    png_base64_to_rgba_overlay,
    rgba_overlay_to_png_base64,
)

_PASTE_BASE_Z = 50
_PASTE_MAX_DIMENSION = 2048
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_HANDLE_SIZE = 10.0
_ROTATE_HANDLE_OFFSET = 16.0
_ACCENT = QColor(31, 155, 240)


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


class _PasteOverlayHandle(QGraphicsRectItem):
    """选中贴片的角缩放手柄 / 顶部旋转手柄（child item，随贴片变换）。"""

    def __init__(self, overlay_item: "PasteOverlayItem", kind: str, rect):
        super().__init__(rect, overlay_item)
        self._overlay_item = overlay_item
        self.kind = kind
        self._drag_start_scene = None
        self._start_width = 0.0
        self._start_height = 0.0
        self._start_rotation = 0.0
        self._start_dist = 1.0
        self._start_angle = 0.0

        color = _ACCENT if kind == "rotate" else QColor("#ffffff")
        self.setBrush(QBrush(color))
        pen = QPen(_ACCENT if kind != "rotate" else QColor("#1f9bf0"), 1)
        self.setPen(pen)
        self.setZValue(20)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def _can_drag(self) -> bool:
        view = getattr(self._overlay_item, "_paste_view", None)
        if view is None:
            return False
        model = getattr(view, "model", None)
        return model is None or model.get_active_tool() == "select"

    def _scene_center(self) -> QPointF:
        overlay = self._overlay_item.overlay
        return QPointF(
            float(overlay.get("center_x", 0.0)),
            float(overlay.get("center_y", 0.0)),
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._can_drag():
            event.ignore()
            return
        overlay = self._overlay_item.overlay
        self._drag_start_scene = event.scenePos()
        self._start_width = float(overlay.get("width", 0.0))
        self._start_height = float(overlay.get("height", 0.0))
        self._start_rotation = float(overlay.get("rotation", 0.0))
        delta = self._drag_start_scene - self._scene_center()
        self._start_dist = max(1.0, math.hypot(delta.x(), delta.y()))
        self._start_angle = math.degrees(math.atan2(delta.y(), delta.x()))
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_start_scene is None or not self._can_drag():
            event.ignore()
            return
        overlay = self._overlay_item.overlay
        center = self._scene_center()
        delta = event.scenePos() - center
        if self.kind == "rotate":
            current_angle = math.degrees(math.atan2(delta.y(), delta.x()))
            overlay["rotation"] = self._start_rotation + (
                current_angle - self._start_angle
            )
        else:
            distance = math.hypot(delta.x(), delta.y())
            factor = distance / self._start_dist
            overlay["width"] = max(2.0, self._start_width * factor)
            overlay["height"] = max(2.0, self._start_height * factor)
        self._overlay_item._apply_geometry()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_start_scene is None:
            event.ignore()
            return
        self._drag_start_scene = None
        overlay_item = self._overlay_item
        overlay_id = overlay_item.overlay_id()
        view = getattr(overlay_item, "_paste_view", None)
        overlay = overlay_item.overlay
        patch = (
            {"rotation": float(overlay.get("rotation", 0.0))}
            if self.kind == "rotate"
            else {
                "width": float(overlay.get("width", 0.0)),
                "height": float(overlay.get("height", 0.0)),
            }
        )
        if view is not None and getattr(view, "controller", None) is not None:
            view.controller.update_paste_overlay(overlay_id, patch)
            view.select_paste_overlay(overlay_id)
        event.accept()


class PasteOverlayItem(QGraphicsPixmapItem):
    """单个贴片的可视项：由 overlay 字典驱动 pixmap/几何/透明度，支持选择与拖动。"""

    def __init__(self, overlay: dict, view=None):
        super().__init__()
        self.overlay = dict(overlay)
        self._paste_view = view
        self._drag_active = False
        self._drag_start_center = (0.0, 0.0)
        self._drag_scene_start = QPointF()
        self._selection_rect: QGraphicsRectItem | None = None
        self._handle_items: list[QGraphicsRectItem] = []
        self._rebuild()

    def overlay_id(self) -> str:
        return str(self.overlay.get("id", ""))

    def set_view(self, view) -> None:
        self._paste_view = view

    def update_overlay(self, overlay: dict) -> None:
        self.overlay = dict(overlay)
        self._rebuild()

    def set_selected(self, selected: bool) -> None:
        if selected:
            self._ensure_selection_affordances()
        else:
            self._remove_selection_affordances()

    def _ensure_selection_affordances(self) -> None:
        if self._selection_rect is not None:
            return
        width = self.pixmap().width()
        height = self.pixmap().height()
        if width <= 0 or height <= 0:
            return
        rect = QGraphicsRectItem(0.0, 0.0, width, height, self)
        pen = QPen(_ACCENT, 0)
        pen.setStyle(Qt.PenStyle.DashLine)
        rect.setPen(pen)
        rect.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        rect.setZValue(1)
        self._selection_rect = rect

        half = _HANDLE_SIZE / 2.0
        corners = (
            (0.0, 0.0),
            (width, 0.0),
            (0.0, height),
            (width, height),
        )
        for index, (corner_x, corner_y) in enumerate(corners):
            handle = _PasteOverlayHandle(
                self,
                "resize",
                QRectF(corner_x - half, corner_y - half, _HANDLE_SIZE, _HANDLE_SIZE),
            )
            self._handle_items.append(handle)

        rotate_handle = _PasteOverlayHandle(
            self,
            "rotate",
            QRectF(
                width / 2.0 - _HANDLE_SIZE / 2.0,
                -_ROTATE_HANDLE_OFFSET - _HANDLE_SIZE / 2.0,
                _HANDLE_SIZE,
                _HANDLE_SIZE,
            ),
        )
        self._handle_items.append(rotate_handle)

    def _remove_selection_affordances(self) -> None:
        children = [self._selection_rect] + list(self._handle_items)
        self._selection_rect = None
        self._handle_items = []
        for child in children:
            if child is None:
                continue
            try:
                scene = child.scene()
                if scene:
                    scene.removeItem(child)
            except (RuntimeError, AttributeError):
                pass

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

    # --- 鼠标交互：选择 + 拖动移动（提交走 undo 栈） ---
    def _drag_enabled(self) -> bool:
        view = self._paste_view
        if view is None or not self.isVisible() or self.pixmap().isNull():
            return False
        model = getattr(view, "model", None)
        return model is None or model.get_active_tool() == "select"

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._drag_enabled():
            super().mousePressEvent(event)
            return
        view = self._paste_view
        if view is not None:
            view.select_paste_overlay(self.overlay_id())
        self._drag_active = True
        self._drag_scene_start = event.scenePos()
        self._drag_start_center = (
            float(self.overlay.get("center_x", 0.0)),
            float(self.overlay.get("center_y", 0.0)),
        )
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._drag_active or not self._drag_enabled():
            super().mouseMoveEvent(event)
            return
        delta = event.scenePos() - self._drag_scene_start
        self.overlay["center_x"] = self._drag_start_center[0] + delta.x()
        self.overlay["center_y"] = self._drag_start_center[1] + delta.y()
        self._apply_geometry()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        dragging = self._drag_active
        self._drag_active = False
        if not dragging or not self._drag_enabled():
            super().mouseReleaseEvent(event)
            return
        overlay_id = self.overlay_id()
        view = self._paste_view
        patch = {
            "center_x": float(self.overlay.get("center_x", 0.0)),
            "center_y": float(self.overlay.get("center_y", 0.0)),
        }
        # 提交后模型会整表重建贴片项（当前 item 将被移除），不要再触碰 self
        if view is not None and getattr(view, "controller", None) is not None:
            view.controller.update_paste_overlay(overlay_id, patch)
            view.select_paste_overlay(overlay_id)
        event.accept()


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
            self._selected_paste_overlay_id = None
            return
        overlays = self.model.get_paste_overlays() if self.model else []
        selected_id = getattr(self, "_selected_paste_overlay_id", None)
        for overlay in overlays:
            item = PasteOverlayItem(overlay, self)
            if item.overlay_id() == selected_id:
                item.set_selected(True)
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
        self._selected_paste_overlay_id = None

    def select_paste_overlay(self, overlay_id: str) -> None:
        """选中唯一贴片（画布级状态，不进入 region 选择体系）。"""
        self._selected_paste_overlay_id = overlay_id
        for item in self._paste_overlay_items:
            item.set_selected(item.overlay_id() == overlay_id)
        self.scene.update()

    def clear_paste_overlay_selection(self) -> None:
        self._selected_paste_overlay_id = None
        for item in self._paste_overlay_items:
            item.set_selected(False)
        self.scene.update()

    def delete_selected_paste_overlay(self) -> bool:
        overlay_id = getattr(self, "_selected_paste_overlay_id", None)
        controller = getattr(self, "controller", None)
        if not overlay_id or controller is None:
            return False
        if controller.remove_paste_overlay(overlay_id):
            self._selected_paste_overlay_id = None
            return True
        return False

    def keyPressEvent(self, event):  # noqa: N802 - Qt API naming
        if event.key() == Qt.Key.Key_Delete and self.delete_selected_paste_overlay():
            event.accept()
            return
        super().keyPressEvent(event)

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
            if controller.add_paste_overlay(overlay):
                current = self.model.get_paste_overlays()
                if current:
                    self.select_paste_overlay(current[-1]["id"])
        except Exception as error:
            self.logger.warning("导入贴片失败: %s", error)
