"""
贴片（paste overlay）画布层：显示项、选中装饰、交互与拖放导入。

坐标系约定：场景单位 == 源图像素（基图 item 未缩放）；贴片的 ``center_x/y``、
``width/height`` 均为源图分辨率数值，直接用于场景摆放。
z 序：贴片基值 50（在基图 2 / 修复预览之上、region 文本框 100 之下）。

选中装饰（虚线框 + 四角手柄 + 旋转手柄）由单个自绘 child item 一次性绘制，
样式对齐文本框（RegionTextItem）。改动数据一律走 EditorController（可撤销）。
"""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QCursor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsSceneMouseEvent,
)

from editor.paste_overlay_state import (
    png_base64_to_rgba_overlay,
    rgba_overlay_to_png_base64,
)

from .graphics_items import (
    _editor_pen,
    _fluent_accent,
    _fluent_surface,
    _shadow_color,
)

_PASTE_BASE_Z = 50
_PASTE_MAX_DIMENSION = 2048
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_ROTATE_OFFSET_PX = 40.0


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


def _view_lod_of(item) -> float:
    """场景视图缩放（屏幕像素 / 场景单位）。"""
    try:
        scene = item.scene()
        if scene is not None and scene.views():
            return max(abs(scene.views()[0].transform().m11()), 0.01)
    except (RuntimeError, AttributeError):
        pass
    return 1.0


class _PasteOverlaySelectionItem(QGraphicsItem):
    """贴片选中装饰：虚线框 + 文本框同款四角/旋转手柄（一次自绘，不闪烁）。"""

    def __init__(self, overlay_item: "PasteOverlayItem"):
        super().__init__(overlay_item)
        self._overlay_item = overlay_item
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(15)

    def shape(self) -> QPainterPath:
        # 纯装饰：不参与命中，鼠标事件全部落到贴片本体（本体 shape 已覆盖扩展区）
        return QPainterPath()

    def _geometry(self) -> tuple[float, float, float]:
        lod = _view_lod_of(self)
        pixmap = self._overlay_item.pixmap()
        width = float(pixmap.width())
        height = float(pixmap.height())
        if width <= 0 or height <= 0:
            lod, width, height = 1.0, 1.0, 1.0
        return lod, width, height

    def boundingRect(self) -> QRectF:
        _, width, height = self._geometry()
        lod = _view_lod_of(self)
        pad = 10.0 / lod
        top_pad = _ROTATE_OFFSET_PX / lod + 12.0 / lod + pad
        return QRectF(-pad, -top_pad, width + pad * 2.0, height + top_pad + pad)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        lod, width, height = self._geometry()
        pw = 1.15 / lod
        frame = QRectF(0.0, 0.0, width, height)
        accent = _fluent_accent(235)
        surface = _fluent_surface(246)

        # 虚线外框（双描边：阴影 + 强调色），对齐文本框选中态
        path = QPainterPath()
        path.addRect(frame)
        painter.setBrush(QBrush(_fluent_accent(16)))
        painter.setPen(_editor_pen(_shadow_color(135), 4.0 / lod))
        painter.drawPath(path)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.setPen(
            _editor_pen(accent, 1.6 / lod, Qt.PenStyle.DashLine)
        )
        painter.drawPath(path)

        # 四角缩放手柄（文本框白框同款圆角方块）
        corner_hs = 13.0 / lod
        corners = (
            QPointF(0.0, 0.0),
            QPointF(width, 0.0),
            QPointF(0.0, height),
            QPointF(width, height),
        )
        radius = min(3.5 / lod, corner_hs / 2.0)
        for corner in corners:
            rect = QRectF(
                corner.x() - corner_hs / 2.0,
                corner.y() - corner_hs / 2.0,
                corner_hs,
                corner_hs,
            )
            painter.setBrush(QBrush(surface))
            painter.setPen(_editor_pen(_shadow_color(120), pw * 2.3))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(_editor_pen(_fluent_accent(238), pw * 1.15))
            painter.drawRoundedRect(
                rect.adjusted(pw * 0.45, pw * 0.45, -pw * 0.45, -pw * 0.45),
                radius,
                radius,
            )

        # 顶部旋转手柄（文本框同款：连接杆 + 圆环 + 圆点）
        rot_hs = 14.0 / lod
        rotate_center = QPointF(width / 2.0, -_ROTATE_OFFSET_PX / lod)
        painter.setPen(_editor_pen(_shadow_color(125), pw * 3.0))
        painter.drawLine(QPointF(width / 2.0, 0.0), rotate_center)
        painter.setPen(_editor_pen(_fluent_accent(205), pw * 1.45))
        painter.drawLine(QPointF(width / 2.0, 0.0), rotate_center)
        rot_rect = QRectF(
            rotate_center.x() - rot_hs / 2.0,
            rotate_center.y() - rot_hs / 2.0,
            rot_hs,
            rot_hs,
        )
        painter.setBrush(QBrush(_fluent_surface(245)))
        painter.setPen(_editor_pen(_shadow_color(110), pw * 2.8))
        painter.drawEllipse(rot_rect)
        painter.setPen(_editor_pen(_fluent_accent(235), pw * 1.2))
        painter.drawEllipse(
            rot_rect.adjusted(pw * 0.45, pw * 0.45, -pw * 0.45, -pw * 0.45)
        )
        dot = rot_hs * 0.32
        painter.setBrush(QBrush(_fluent_accent(225)))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.drawEllipse(
            QRectF(
                rotate_center.x() - dot / 2.0,
                rotate_center.y() - dot / 2.0,
                dot,
                dot,
            )
        )


class PasteOverlayItem(QGraphicsPixmapItem):
    """单个贴片：由 overlay 字典驱动 pixmap/几何/透明度；自带选择与拖拽交互。"""

    def __init__(self, overlay: dict, view=None):
        super().__init__()
        self.overlay = dict(overlay)
        self._paste_view = view
        self._selection_item: _PasteOverlaySelectionItem | None = None
        self._drag_mode: str | None = None
        self._drag_start_scene = QPointF()
        self._drag_start_center = (0.0, 0.0)
        self._drag_start_size = (0.0, 0.0)
        self._drag_start_rotation = 0.0
        self._drag_start_dist = 1.0
        self._drag_start_angle = 0.0
        self._source_pixmap: QPixmap = QPixmap()
        self.setAcceptHoverEvents(True)
        self._rebuild()

    # ------------------------------------------------------------------
    # 基础数据 / 视图同步
    # ------------------------------------------------------------------

    def overlay_id(self) -> str:
        return str(self.overlay.get("id", ""))

    def set_view(self, view) -> None:
        self._paste_view = view

    def update_overlay(self, overlay: dict) -> None:
        self.overlay = dict(overlay)
        self._rebuild()

    def set_selected(self, selected: bool) -> None:
        if selected:
            if self._selection_item is None:
                self.prepareGeometryChange()
                self._selection_item = _PasteOverlaySelectionItem(self)
        elif self._selection_item is not None:
            self.prepareGeometryChange()
            try:
                scene = self._selection_item.scene()
                if scene:
                    scene.removeItem(self._selection_item)
            except (RuntimeError, AttributeError):
                pass
            self._selection_item = None

    def _view_lod(self) -> float:
        return _view_lod_of(self)

    def boundingRect(self) -> QRectF:
        pixmap = self.pixmap()
        width = float(pixmap.width())
        height = float(pixmap.height())
        if self._selection_item is not None:
            lod = self._view_lod()
            pad = 10.0 / lod
            top_pad = _ROTATE_OFFSET_PX / lod + 12.0 / lod + pad
            return QRectF(-pad, -top_pad, width + pad * 2.0, height + top_pad + pad)
        return QRectF(0.0, 0.0, width, height)

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
        # 缓存解码原图：缩放永远从原图重采样，避免拖动时逐帧叠加重采样丢失细节
        self._source_pixmap = pixmap
        self.prepareGeometryChange()
        self.setPixmap(pixmap)
        self._apply_geometry()

    def _apply_geometry(self, *, rasterize: bool = True) -> None:
        overlay = self.overlay
        # 解码原图仅作为重采样来源，最终尺寸以 overlay 的 width/height 为准
        source = (
            self._source_pixmap
            if not self._source_pixmap.isNull()
            else self.pixmap()
        )
        if source.isNull() or source.width() <= 0 or source.height() <= 0:
            self.setVisible(False)
            return

        target_width = max(
            1, int(round(float(overlay.get("width", source.width()))))
        )
        target_height = max(
            1, int(round(float(overlay.get("height", source.height()))))
        )
        # 拖动缩放期间（rasterize=False）不重采样 pixmap，把目标/当前比例折进
        # item 变换；只有松手提交重建时才做一次最终栅格化，避免逐帧分配大 pixmap
        display = self.pixmap()
        if rasterize:
            if (
                display.isNull()
                or display.width() != target_width
                or display.height() != target_height
            ):
                display = source.scaled(
                    target_width,
                    target_height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.prepareGeometryChange()
                self.setPixmap(display)
        else:
            if display.isNull() or display.width() <= 0 or display.height() <= 0:
                display = source.scaled(
                    target_width,
                    target_height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.prepareGeometryChange()
                self.setPixmap(display)
                rasterize = True

        # 变换锚点必须用“当前显示尺寸”
        width = display.width()
        height = display.height()
        if width <= 0 or height <= 0:
            self.setVisible(False)
            return

        center_x = float(overlay.get("center_x", 0.0))
        center_y = float(overlay.get("center_y", 0.0))
        rotation = float(overlay.get("rotation", 0.0))
        flip_h = -1.0 if overlay.get("flip_h") else 1.0
        flip_v = -1.0 if overlay.get("flip_v") else 1.0

        scale_x = 1.0
        scale_y = 1.0
        if not rasterize:
            scale_x = target_width / float(width)
            scale_y = target_height / float(height)

        transform = QTransform()
        transform.translate(center_x, center_y)
        transform.rotate(rotation)
        transform.scale(flip_h * scale_x, flip_v * scale_y)
        transform.translate(-width / 2.0, -height / 2.0)
        self.setTransform(transform)

        try:
            opacity = float(overlay.get("opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        self.setOpacity(max(0.0, min(1.0, opacity)))
        self.setZValue(_PASTE_BASE_Z + int(overlay.get("z", 0)))
        self.setVisible(bool(overlay.get("visible", True)))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        pixmap = self.pixmap()
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            return path
        width = float(pixmap.width())
        height = float(pixmap.height())
        path.addRect(QRectF(0.0, 0.0, width, height))
        if self._selection_item is not None:
            lod = self._view_lod()
            pad = 8.0 / lod
            path.addRect(
                QRectF(
                    -pad,
                    -_ROTATE_OFFSET_PX / lod - 14.0 / lod - pad,
                    width + pad * 2.0,
                    height + _ROTATE_OFFSET_PX / lod + 14.0 / lod + pad * 2.0,
                )
            )
        return path

    # ------------------------------------------------------------------
    # 鼠标交互：移动 / 角缩放 / 旋转（提交走 undo 栈）
    # ------------------------------------------------------------------

    def _hover_cursor_for(self, local_pos: QPointF):
        """命中判定对应的 Qt 光标（对齐文本框：角=对角拉伸, 旋转=移动四向）。"""
        if self._selection_item is None:
            return None
        pixmap = self.pixmap()
        width = float(pixmap.width())
        height = float(pixmap.height())
        if width <= 0 or height <= 0:
            return None
        lod = self._view_lod()
        hit = 10.0 / lod
        rotate_center = QPointF(width / 2.0, -_ROTATE_OFFSET_PX / lod)
        if (
            math.hypot(
                local_pos.x() - rotate_center.x(), local_pos.y() - rotate_center.y()
            )
            <= hit + 7.0 / lod
        ):
            return Qt.CursorShape.SizeAllCursor
        corners = (
            QPointF(0.0, 0.0),
            QPointF(width, 0.0),
            QPointF(0.0, height),
            QPointF(width, height),
        )
        for corner in corners:
            if math.hypot(local_pos.x() - corner.x(), local_pos.y() - corner.y()) <= hit:
                top_left = local_pos.x() <= width / 2.0 and local_pos.y() <= height / 2.0
                bottom_right = local_pos.x() > width / 2.0 and local_pos.y() > height / 2.0
                return (
                    Qt.CursorShape.SizeFDiagCursor
                    if top_left or bottom_right
                    else Qt.CursorShape.SizeBDiagCursor
                )
        return None

    def _apply_hover_cursor(self, shape) -> None:
        self.setCursor(QCursor(shape))
        view = self._paste_view
        if view is not None and view.model.get_active_tool() == "select":
            view.viewport().setCursor(QCursor(shape))

    def _clear_hover_cursor(self) -> None:
        self.unsetCursor()
        view = self._paste_view
        if view is not None and view.model.get_active_tool() == "select":
            view.viewport().unsetCursor()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802 - Qt API naming
        try:
            view = self._paste_view
            if (
                self._selection_item is None
                or not self.isVisible()
                or view is None
                or view.model.get_active_tool() != "select"
            ):
                self._clear_hover_cursor()
                super().hoverMoveEvent(event)
                return
            shape = self._hover_cursor_for(QPointF(event.pos()))
            if shape is None:
                self._clear_hover_cursor()
            else:
                self._apply_hover_cursor(shape)
            super().hoverMoveEvent(event)
        except Exception:
            pass

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API naming
        self._clear_hover_cursor()
        super().hoverLeaveEvent(event)

    def _drag_enabled(self) -> bool:
        view = self._paste_view
        if view is None or not self.isVisible() or self.pixmap().isNull():
            return False
        model = getattr(view, "model", None)
        return model is None or model.get_active_tool() == "select"

    def _hit_mode(self, local_pos: QPointF) -> str | None:
        """按局部坐标命中判定：旋转手柄 / 四角缩放手柄 / 内部移动。"""
        pixmap = self.pixmap()
        width = float(pixmap.width())
        height = float(pixmap.height())
        lod = self._view_lod()
        hit = 10.0 / lod
        rotate_center = QPointF(width / 2.0, -_ROTATE_OFFSET_PX / lod)
        if (
            math.hypot(local_pos.x() - rotate_center.x(), local_pos.y() - rotate_center.y())
            <= hit + 7.0 / lod
        ):
            return "rotate"
        for corner in (
            QPointF(0.0, 0.0),
            QPointF(width, 0.0),
            QPointF(0.0, height),
            QPointF(width, height),
        ):
            if math.hypot(local_pos.x() - corner.x(), local_pos.y() - corner.y()) <= hit:
                return "resize"
        return "move"

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._drag_enabled():
            super().mousePressEvent(event)
            return
        view = self._paste_view
        if view is not None and getattr(view, "model", None) is not None:
            view.model.set_selection([])
        # 注意：这里不立即补“选中框”。选中框的创建会触发 prepareGeometryChange，
        # 若在鼠标按下的事件分发中改几何，可能出现按下瞬间整张贴片偏移一帧、
        # 松手又恢复的闪烁。选中态统一放到 mouseRelease（提交后重建）再补。
        local_pos = self.mapFromScene(event.scenePos())
        self._drag_mode = self._hit_mode(local_pos)
        self._drag_start_scene = event.scenePos()
        self._drag_start_center = (
            float(self.overlay.get("center_x", 0.0)),
            float(self.overlay.get("center_y", 0.0)),
        )
        self._drag_start_size = (
            float(self.overlay.get("width", 1.0)),
            float(self.overlay.get("height", 1.0)),
        )
        self._drag_start_rotation = float(self.overlay.get("rotation", 0.0))
        delta = event.scenePos() - QPointF(
            self._drag_start_center[0], self._drag_start_center[1]
        )
        self._drag_start_dist = max(1.0, math.hypot(delta.x(), delta.y()))
        self._drag_start_angle = math.degrees(math.atan2(delta.y(), delta.x()))
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_mode is None or not self._drag_enabled():
            super().mouseMoveEvent(event)
            return
        overlay = self.overlay
        center = QPointF(
            float(overlay.get("center_x", 0.0)),
            float(overlay.get("center_y", 0.0)),
        )
        if self._drag_mode == "move":
            delta = event.scenePos() - self._drag_start_scene
            overlay["center_x"] = self._drag_start_center[0] + delta.x()
            overlay["center_y"] = self._drag_start_center[1] + delta.y()
        elif self._drag_mode == "rotate":
            delta = event.scenePos() - center
            current_angle = math.degrees(math.atan2(delta.y(), delta.x()))
            overlay["rotation"] = self._drag_start_rotation + (
                current_angle - self._drag_start_angle
            )
        elif self._drag_mode == "resize":
            delta = event.scenePos() - center
            distance = math.hypot(delta.x(), delta.y())
            factor = distance / self._drag_start_dist
            # 防失控放大：限制到源贴片解码上限的 4 倍，避免单次拖拽申请超大 pixmap
            max_side = float(_PASTE_MAX_DIMENSION * 4)
            longest = max(self._drag_start_size) * factor
            if longest > max_side:
                factor *= max_side / longest
            overlay["width"] = max(2.0, self._drag_start_size[0] * factor)
            overlay["height"] = max(2.0, self._drag_start_size[1] * factor)
        # 缩放拖拽中只改变换不重采样；移动/旋转仍即时按几何刷新
        self._apply_geometry(rasterize=self._drag_mode != "resize")
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        mode = self._drag_mode
        self._drag_mode = None
        if mode is None or not self._drag_enabled():
            super().mouseReleaseEvent(event)
            return
        overlay_id = self.overlay_id()
        view = self._paste_view
        overlay = self.overlay
        if mode == "move":
            patch = {
                "center_x": float(overlay.get("center_x", 0.0)),
                "center_y": float(overlay.get("center_y", 0.0)),
            }
        elif mode == "rotate":
            patch = {"rotation": float(overlay.get("rotation", 0.0))}
        else:
            patch = {
                "width": float(overlay.get("width", 0.0)),
                "height": float(overlay.get("height", 0.0)),
            }
        # 提交后模型整表重建贴片项（当前 item 将被移除），提交后不要再触碰 self
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
        existing = {item.overlay_id(): item for item in items}
        items.clear()

        if self._image_item is None:
            self._remove_paste_items(existing.values())
            self._selected_paste_overlay_id = None
            return
        overlays = self.model.get_paste_overlays() if self.model else []
        selected_id = getattr(self, "_selected_paste_overlay_id", None)
        new_items = []
        for overlay in overlays:
            overlay_id = str(overlay.get("id", ""))
            old_item = existing.pop(overlay_id, None)
            if (
                old_item is not None
                and old_item.overlay.get("image") == overlay.get("image")
            ):
                # 图片未变：复用 item，只更新几何/属性，不重建不解码
                old_item.overlay = dict(overlay)
                old_item._apply_geometry()
                item = old_item
            else:
                if old_item is not None:
                    self._remove_paste_items([old_item])
                item = PasteOverlayItem(overlay, self)
                self.scene.addItem(item)
            item.set_selected(item.overlay_id() == selected_id)
            new_items.append(item)
        # 已不存在的贴片项清理出场景
        self._remove_paste_items(existing.values())

        # 重新按列表顺序入场景：Qt 同 z 的叠放顺序按插入顺序决定，
        # 复用 item 不会改变原顺序，这里统一重建插入序，保证画布与
        # 导出合成（compose_paste_overlays 按 z 升序、同 z 保序）一致
        for item in new_items:
            try:
                if item.scene():
                    item.scene().removeItem(item)
            except (RuntimeError, AttributeError):
                pass
        for item in new_items:
            self.scene.addItem(item)

        self._paste_overlay_items = new_items
        self.scene.update()

    @staticmethod
    def _remove_paste_items(items) -> None:
        for item in items:
            try:
                if item is not None and item.scene():
                    item.scene().removeItem(item)
            except (RuntimeError, AttributeError):
                pass

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
        if hasattr(self, "model") and self.model:
            self.model.set_selection([])
        for item in self._paste_overlay_items:
            item.set_selected(item.overlay_id() == overlay_id)
        self.scene.update()

    def clear_paste_overlay_selection(self) -> None:
        if not getattr(self, "_selected_paste_overlay_id", None):
            return
        self._selected_paste_overlay_id = None
        for item in self._paste_overlay_items:
            item.set_selected(False)
        self.scene.update()

    def clear_paste_overlay_selection_for_press(self, event) -> None:
        """点按命中目标不是贴片（文本框/空白等）时，隐藏贴片手柄。"""
        if not getattr(self, "_selected_paste_overlay_id", None):
            return
        try:
            top_item = self.itemAt(event.position().toPoint())
        except AttributeError:
            top_item = self.itemAt(event.pos())
        while top_item is not None:
            if isinstance(top_item, PasteOverlayItem):
                return
            top_item = top_item.parentItem()
        self.clear_paste_overlay_selection()

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
            import numpy as np
            from PIL import Image

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
                    (
                        max(1, int(round(width * scale))),
                        max(1, int(round(height * scale))),
                    ),
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
