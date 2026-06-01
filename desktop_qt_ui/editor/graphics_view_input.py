from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import QPointF, Qt, pyqtSlot
from PyQt6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QGraphicsView, QMenu
from services import get_config_service

from .graphics_items import RegionTextItem


class GraphicsViewInputMixin:
    def _region_item_at_view_pos(self, view_pos):
        item_at_pos = self.itemAt(view_pos)
        check_item = item_at_pos
        while check_item:
            if isinstance(check_item, RegionTextItem):
                return check_item
            check_item = check_item.parentItem()
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._emit_view_state_changed()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._emit_view_state_changed()

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor

        if event.angleDelta().y() > 0:
            self.scale(zoom_in_factor, zoom_in_factor)
        else:
            self.scale(zoom_out_factor, zoom_out_factor)

        self._update_cursor()
        self._emit_view_state_changed()

    def mousePressEvent(self, event):
        parent_view = self.parent()
        if hasattr(parent_view, "force_save_property_panel_edits"):
            parent_view.force_save_property_panel_edits()

        self.setFocus()

        if self._active_tool == "draw_textbox" and event.button() == Qt.MouseButton.LeftButton:
            self._start_drawing_textbox(event.pos())
            event.accept()
            return

        if self._active_tool in ["pen", "eraser", "brush", "paint", "paint_erase"] and event.button() == Qt.MouseButton.LeftButton:
            self._start_drawing(event.pos())
            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            dummy_event = QMouseEvent(
                event.type(),
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(dummy_event)
        elif event.button() == Qt.MouseButton.RightButton:
            clicked_region_item = self._region_item_at_view_pos(event.pos())
            if clicked_region_item is not None:
                ctrl_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if not clicked_region_item.isSelected():
                    if not ctrl_pressed:
                        self.scene.clearSelection()
                    clicked_region_item.setSelected(True)
                event.accept()
                return
            super().mousePressEvent(event)
        elif event.button() == Qt.MouseButton.LeftButton:
            item_at_pos = self.itemAt(event.pos())

            clicked_region_item = self._region_item_at_view_pos(event.pos()) is not None

            if item_at_pos is None or item_at_pos == self._image_item:
                self.selection_manager.start_box_select(self.mapToScene(event.pos()))
                event.accept()
                return

            if clicked_region_item:
                super().mousePressEvent(event)
            else:
                super().mousePressEvent(event)
                ctrl_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if not ctrl_pressed:
                    self.scene.clearSelection()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.selection_manager.is_box_selecting:
            current_pos = self.mapToScene(event.pos())
            if self.selection_manager.update_box_select(current_pos):
                event.accept()
                return
            return

        if self._is_drawing_textbox:
            self._update_textbox_drawing(event.pos())
            event.accept()
            return

        if self._is_drawing:
            self._update_preview_drawing(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.selection_manager.is_box_selecting and event.button() == Qt.MouseButton.LeftButton:
            ctrl_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.selection_manager.finish_box_select(ctrl_pressed)
            event.accept()
            return

        if self._is_drawing_textbox and event.button() == Qt.MouseButton.LeftButton:
            self._finish_textbox_drawing()
            event.accept()
            return

        if self._is_drawing and event.button() == Qt.MouseButton.LeftButton:
            self._finish_drawing()

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            if self._drag_start_pos:
                self._potential_drag = False
                self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drawing(self, pos):
        if self._image_item is None:
            return
        mask_shape = self._get_edit_mask_shape()
        if mask_shape is None:
            return
        self._is_drawing = True
        self._current_draw_scene_points = []
        self._current_draw_mask_points = []
        self._current_draw_mask_shape = mask_shape

        scene_point = self.mapToScene(pos)
        self._append_draw_point(scene_point)

        if self._preview_item is None:
            pixmap = QPixmap(self._image_item.pixmap().size())
            pixmap.fill(Qt.GlobalColor.transparent)
            self._preview_item = self.scene.addPixmap(pixmap)
            # 预览应该在蒙版之上、文字之下，避免盖住文本渲染
            self._preview_item.setZValue(12)
            self._scale_mask_item(self._preview_item)
        self._preview_item.setVisible(True)
        self._redraw_preview_drawing()

    def _update_preview_drawing(self, pos):
        if not self._is_drawing:
            return
        self._append_draw_point(self.mapToScene(pos))
        self._redraw_preview_drawing()

    def _redraw_preview_drawing(self):
        if self._preview_item is None:
            return

        pixmap = self._preview_item.pixmap()
        pixmap.fill(Qt.GlobalColor.transparent)
        if self._current_draw_scene_points:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            if self._active_tool == "paint":
                preview_color = QColor(self._brush_color)
                if not preview_color.isValid():
                    preview_color = QColor(255, 0, 0, 200)
                else:
                    preview_color.setAlpha(220)
                preview_pen = QPen(
                    preview_color,
                    self._brush_size,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            elif self._active_tool == "paint_erase":
                preview_pen = QPen(
                    QColor(0, 200, 255, 120),
                    self._brush_size,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            elif self._active_tool in ["pen", "brush"]:
                preview_pen = QPen(
                    QColor(255, 0, 0, 128),
                    self._brush_size,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            else:
                preview_pen = QPen(
                    QColor(0, 150, 255, 100),
                    self._brush_size,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            painter.setPen(preview_pen)

            draw_points = [self._scene_to_image_point(point) for point in self._current_draw_scene_points]
            if len(draw_points) == 1:
                radius = max(1.0, self._brush_size / 2.0)
                painter.drawEllipse(draw_points[0], radius, radius)
            else:
                for idx in range(1, len(draw_points)):
                    painter.drawLine(draw_points[idx - 1], draw_points[idx])

            painter.end()

        self._preview_item.setPixmap(pixmap)
        self.scene.update()
        self.viewport().update()

    def _get_edit_mask_shape(self):
        # paint 模式：始终匹配底图像素尺寸
        if self._active_tool in ("paint", "paint_erase"):
            if self._image_item is None:
                return None
            pixmap = self._image_item.pixmap()
            return pixmap.height(), pixmap.width()

        current_mask = self.model.get_refined_mask()
        if current_mask is not None:
            mask = np.array(current_mask)
            if mask.ndim == 3:
                mask = mask[:, :, 0]
            if mask.ndim == 2 and mask.size > 0:
                return mask.shape[0], mask.shape[1]
        if self._image_item is None:
            return None
        return self._image_item.pixmap().height(), self._image_item.pixmap().width()

    def _scene_to_image_point(self, scene_point: QPointF) -> QPointF:
        if self._image_item is None:
            return scene_point
        image_point = self._image_item.mapFromScene(scene_point)
        image_w = self._image_item.pixmap().width()
        image_h = self._image_item.pixmap().height()
        if image_w <= 0 or image_h <= 0:
            return QPointF(0.0, 0.0)
        x = min(max(float(image_point.x()), 0.0), float(image_w - 1))
        y = min(max(float(image_point.y()), 0.0), float(image_h - 1))
        return QPointF(x, y)

    def _scene_to_mask_point(self, scene_point: QPointF, mask_shape: tuple[int, int]):
        if self._image_item is None:
            return None
        mask_h, mask_w = mask_shape
        if mask_h <= 0 or mask_w <= 0:
            return None

        image_point = self._scene_to_image_point(scene_point)
        image_w = self._image_item.pixmap().width()
        image_h = self._image_item.pixmap().height()
        if image_w <= 0 or image_h <= 0:
            return None

        x_ratio = float(image_point.x()) / float(max(image_w - 1, 1))
        y_ratio = float(image_point.y()) / float(max(image_h - 1, 1))
        x_mask = int(round(x_ratio * float(max(mask_w - 1, 0))))
        y_mask = int(round(y_ratio * float(max(mask_h - 1, 0))))
        x_mask = min(max(x_mask, 0), mask_w - 1)
        y_mask = min(max(y_mask, 0), mask_h - 1)
        return x_mask, y_mask

    def _append_draw_point(self, scene_point: QPointF):
        if not self._is_drawing or self._current_draw_mask_shape is None:
            return
        self._current_draw_scene_points.append(scene_point)
        mask_point = self._scene_to_mask_point(scene_point, self._current_draw_mask_shape)
        if mask_point is None:
            return
        if not self._current_draw_mask_points or self._current_draw_mask_points[-1] != mask_point:
            self._current_draw_mask_points.append(mask_point)

    def _build_stroke_mask(self, points: list[tuple[int, int]], mask_shape: tuple[int, int]) -> np.ndarray:
        mask_h, mask_w = mask_shape
        stroke_mask = np.zeros((mask_h, mask_w), dtype=np.uint8)
        if not points:
            return stroke_mask

        image_w = self._image_item.pixmap().width() if self._image_item else mask_w
        image_h = self._image_item.pixmap().height() if self._image_item else mask_h
        scale_x = float(mask_w) / float(max(image_w, 1))
        scale_y = float(mask_h) / float(max(image_h, 1))
        stroke_size = max(1, int(round(self._brush_size * (scale_x + scale_y) * 0.5)))
        radius = max(1, stroke_size // 2)

        if len(points) == 1:
            cv2.circle(stroke_mask, points[0], radius, 255, thickness=-1, lineType=cv2.LINE_8)
            return stroke_mask

        for idx in range(1, len(points)):
            cv2.line(
                stroke_mask,
                points[idx - 1],
                points[idx],
                255,
                thickness=stroke_size,
                lineType=cv2.LINE_8,
            )
        for point in points:
            cv2.circle(stroke_mask, point, radius, 255, thickness=-1, lineType=cv2.LINE_8)
        return stroke_mask

    def _normalize_binary_mask_array(self, mask: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
        if mask is None:
            return np.zeros(target_shape, dtype=np.uint8)
        mask_np = np.array(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np[:, :, 0]
        mask_np = np.where(mask_np > 0, 255, 0).astype(np.uint8)
        if mask_np.shape[:2] != target_shape:
            mask_np = cv2.resize(mask_np, (target_shape[1], target_shape[0]), interpolation=cv2.INTER_NEAREST)
            mask_np = np.where(mask_np > 0, 255, 0).astype(np.uint8)
        return mask_np

    def _finish_drawing(self):
        if not self._is_drawing or not self._current_draw_mask_points or self._current_draw_mask_shape is None:
            self._reset_drawing_state()
            self._clear_preview()
            return

        # Paint overlay 彩色画笔 / 擦除路径
        if self._active_tool in ("paint", "paint_erase"):
            self._finish_paint_overlay_drawing()
            self._reset_drawing_state()
            return

        current_mask = self.model.get_refined_mask()
        old_mask_np = (
            self._normalize_binary_mask_array(current_mask, self._current_draw_mask_shape)
            if current_mask is not None
            else None
        )
        base_mask = self._normalize_binary_mask_array(current_mask, self._current_draw_mask_shape)
        stroke_mask = self._build_stroke_mask(self._current_draw_mask_points, self._current_draw_mask_shape)

        new_mask_np = base_mask.copy()
        if self._active_tool in ["pen", "brush"]:
            new_mask_np[stroke_mask > 0] = 255
        elif self._active_tool == "eraser":
            new_mask_np[stroke_mask > 0] = 0
        else:
            self._reset_drawing_state()
            self._clear_preview()
            return

        self._clear_preview()

        controller = self._get_controller()
        if not controller:
            self._reset_drawing_state()
            return

        mask_changed = (old_mask_np is None and np.any(new_mask_np)) or \
                       (old_mask_np is not None and not np.array_equal(old_mask_np, new_mask_np))

        if mask_changed:
            from .commands import MaskEditCommand

        if self._active_tool in ["pen", "brush"] and np.any(stroke_mask):
            if mask_changed:
                controller._suppress_refined_mask_autoinpaint = True
                try:
                    command = MaskEditCommand(model=self.model, old_mask=old_mask_np, new_mask=new_mask_np.copy())
                    controller.execute_command(command)
                finally:
                    controller._suppress_refined_mask_autoinpaint = False
            
            controller.force_inpaint_stroke(stroke_mask)
        else:
            if mask_changed:
                command = MaskEditCommand(model=self.model, old_mask=old_mask_np, new_mask=new_mask_np.copy())
                controller.execute_command(command)

        self._reset_drawing_state()

    def _finish_paint_overlay_drawing(self):
        """把当前笔画应用到 paint overlay 图层。"""
        try:
            shape = self._current_draw_mask_shape
            stroke_mask = self._build_stroke_mask(self._current_draw_mask_points, shape)
            if not np.any(stroke_mask):
                self._clear_preview()
                return

            overlay = self.model.get_paint_overlay_image()
            h, w = shape
            if overlay is None:
                old_overlay = None
                new_overlay = np.zeros((h, w, 4), dtype=np.uint8)
            else:
                old_arr = np.asarray(overlay)
                if old_arr.ndim == 2:
                    tmp = np.zeros((old_arr.shape[0], old_arr.shape[1], 4), dtype=np.uint8)
                    tmp[..., 3] = old_arr.astype(np.uint8)
                    old_arr = tmp
                elif old_arr.ndim == 3 and old_arr.shape[2] == 3:
                    tmp = np.zeros((old_arr.shape[0], old_arr.shape[1], 4), dtype=np.uint8)
                    tmp[..., :3] = old_arr
                    tmp[..., 3] = 255
                    old_arr = tmp
                elif old_arr.ndim != 3 or old_arr.shape[2] != 4:
                    old_arr = None

                if old_arr is None or old_arr.shape[:2] != (h, w):
                    # 尺寸不匹配：重置图层
                    old_overlay = None
                    new_overlay = np.zeros((h, w, 4), dtype=np.uint8)
                else:
                    old_overlay = old_arr.astype(np.uint8, copy=False)
                    new_overlay = old_overlay.copy()

            pixel_mask = stroke_mask > 0

            if self._active_tool == "paint":
                color = QColor(self._brush_color)
                if not color.isValid():
                    color = QColor(255, 0, 0)
                new_overlay[pixel_mask, 0] = color.red()
                new_overlay[pixel_mask, 1] = color.green()
                new_overlay[pixel_mask, 2] = color.blue()
                new_overlay[pixel_mask, 3] = 255
            else:  # paint_erase
                new_overlay[pixel_mask] = 0

            controller = self._get_controller()
            if not controller:
                self._clear_preview()
                return

            if old_overlay is not None and np.array_equal(old_overlay, new_overlay):
                self._clear_preview()
                return

            from .commands import PaintOverlayEditCommand

            command = PaintOverlayEditCommand(
                model=self.model,
                old_overlay=old_overlay,
                new_overlay=new_overlay,
            )
            controller.execute_command(command)
        except Exception as e:
            self.logger.error("Paint overlay stroke failed: %s", e, exc_info=True)
        finally:
            self._clear_preview()

    def _reset_drawing_state(self):
        self._is_drawing = False
        self._current_draw_scene_points = []
        self._current_draw_mask_points = []
        self._current_draw_mask_shape = None

    def _clear_preview(self):
        if self._preview_item:
            self._preview_item.pixmap().fill(Qt.GlobalColor.transparent)
            self._preview_item.setVisible(False)
            self.scene.update()
            self.viewport().update()

    @pyqtSlot(str)
    def _on_active_tool_changed(self, tool: str):
        self._active_tool = tool
        self._update_cursor()
        self.viewport().update()

    @pyqtSlot(int)
    def _on_brush_size_changed(self, size: int):
        self._brush_size = size
        self._update_cursor()

    @pyqtSlot(str)
    def _on_brush_color_changed(self, color: str):
        self._brush_color = color or "#ffffff"
        # 只有当前是 paint 工具时刷新光标
        if self._active_tool == "paint":
            self._update_cursor()

    def _update_cursor(self):
        if self._active_tool in ["pen", "eraser", "brush", "paint", "paint_erase"]:
            size = max(10, int(self._brush_size * self.transform().m11()))
            cursor_size = size + 6
            pixmap = QPixmap(cursor_size, cursor_size)
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            center = cursor_size // 2
            radius = size // 2

            painter.setPen(QPen(Qt.GlobalColor.black, 2))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawEllipse(center - radius, center - radius, radius * 2, radius * 2)

            if self._active_tool == "paint":
                inner_color = QColor(self._brush_color)
                if not inner_color.isValid():
                    inner_color = QColor(255, 0, 0)
            elif self._active_tool in ["pen", "brush"]:
                inner_color = QColor(Qt.GlobalColor.red)
            elif self._active_tool == "paint_erase":
                inner_color = QColor(0, 200, 255)
            else:
                inner_color = QColor(Qt.GlobalColor.blue)
            painter.setPen(QPen(inner_color, 1))
            painter.drawEllipse(center - radius + 1, center - radius + 1, (radius - 1) * 2, (radius - 1) * 2)
            painter.end()

            cursor = QCursor(pixmap, center, center)
            self.setCursor(cursor)
            self.viewport().setCursor(cursor)
        elif self._active_tool == "draw_textbox":
            cursor = QCursor(Qt.CursorShape.CrossCursor)
            self.setCursor(cursor)
            self.viewport().setCursor(cursor)
        else:
            self.unsetCursor()
            self.viewport().unsetCursor()

    def enterEvent(self, event):
        self._update_cursor()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        self.viewport().unsetCursor()
        super().leaveEvent(event)

    @pyqtSlot()
    def zoom_in(self):
        self.scale(1.15, 1.15)
        self._emit_view_state_changed()

    @pyqtSlot()
    def zoom_out(self):
        self.scale(1 / 1.15, 1 / 1.15)
        self._emit_view_state_changed()

    @pyqtSlot()
    def fit_to_window(self):
        if self._image_item:
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._emit_view_state_changed()

    def contextMenuEvent(self, event):
        selected_regions = self.model.get_selection()
        selection_count = len(selected_regions)
        menu = QMenu(self)

        if selection_count > 0:
            menu.addAction("🔍 OCR识别选中项", self._ocr_selected_regions)
            menu.addAction("🌐 翻译选中项", self._translate_selected_regions)
            menu.addSeparator()

            if selection_count == 1:
                menu.addAction("📋 复制区域", self._copy_selected_region)
                menu.addAction("🎨 粘贴样式", self._paste_region_style)
                menu.addSeparator()

            menu.addAction(f"🗑️ 删除选中的 {selection_count} 个区域", self._delete_selected_regions)
        else:
            menu.addAction("➕ 添加文本框", self._add_text_box)
            menu.addAction("📋 粘贴区域", self._paste_region)
            menu.addSeparator()
            menu.addAction("🔄 刷新视图", self._refresh_view)

        menu.exec(event.globalPos())

    def _get_controller(self):
        return getattr(self, "controller", None)

    def _ocr_selected_regions(self):
        selected_regions = self.model.get_selection()
        controller = self._get_controller()
        if controller and selected_regions:
            controller.ocr_regions(selected_regions)

    def _translate_selected_regions(self):
        selected_regions = self.model.get_selection()
        controller = self._get_controller()
        if controller and selected_regions:
            controller.translate_regions(selected_regions)

    def _copy_selected_region(self):
        selected_regions = self.model.get_selection()
        controller = self._get_controller()
        if len(selected_regions) == 1 and controller:
            controller.copy_region(selected_regions[0])

    def _paste_region_style(self):
        selected_regions = self.model.get_selection()
        controller = self._get_controller()
        if len(selected_regions) == 1 and controller:
            controller.paste_region_style(selected_regions[0])

    def _delete_selected_regions(self):
        controller = self._get_controller()
        if controller:
            controller.delete_regions(self.model.get_selection())

    def _add_text_box(self):
        controller = self._get_controller()
        if controller:
            controller.enter_drawing_mode()

    def _paste_region(self):
        controller = self._get_controller()
        if controller and self._image_item:
            mouse_pos_scene = self.mapToScene(self.mapFromGlobal(QCursor.pos()))
            mouse_pos_image = self._image_item.mapFromScene(mouse_pos_scene)
            controller.paste_region(mouse_pos_image)

    def _refresh_view(self):
        self.scene.update()
        self.update()

    def _start_drawing_textbox(self, pos):
        if self._image_item is None:
            return

        self._is_drawing_textbox = True
        self._textbox_start_pos = self.mapToScene(pos)

        if self._textbox_preview_item is None:
            pen = QPen(QColor(255, 0, 0, 200))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            brush = QColor(255, 0, 0, 50)
            self._textbox_preview_item = self.scene.addRect(0, 0, 0, 0, pen, brush)
            self._textbox_preview_item.setZValue(200)

        self._textbox_preview_item.setRect(0, 0, 0, 0)
        self._textbox_preview_item.setVisible(True)

    def _update_textbox_drawing(self, pos):
        if not self._is_drawing_textbox or self._textbox_start_pos is None:
            return

        current_pos = self.mapToScene(pos)
        x1, y1 = self._textbox_start_pos.x(), self._textbox_start_pos.y()
        x2, y2 = current_pos.x(), current_pos.y()
        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        if self._textbox_preview_item:
            self._textbox_preview_item.setRect(left, top, width, height)

    def _finish_textbox_drawing(self):
        if not self._is_drawing_textbox or self._textbox_start_pos is None:
            return

        rect = self._textbox_preview_item.rect()
        if self._textbox_preview_item:
            self._textbox_preview_item.setVisible(False)
            self._textbox_preview_item.setRect(0, 0, 0, 0)

        min_size = 20
        if rect.width() < min_size or rect.height() < min_size:
            self._is_drawing_textbox = False
            self._textbox_start_pos = None
            return

        self._create_new_text_region(rect)
        self._is_drawing_textbox = False
        self._textbox_start_pos = None
        self.model.set_active_tool("select")

    def _create_new_text_region(self, rect):
        if not self._image_item:
            return

        image_transform = self._image_item.transform()
        try:
            inverse_transform = image_transform.inverted()[0]

            template_angle = 0
            controller = self._get_controller()
            if controller:
                selected_regions = self.model.get_selection()
                if selected_regions:
                    template_region = self.model.get_region_by_index(selected_regions[-1])
                    if template_region:
                        template_angle = template_region.get("angle", 0)

            rect_center_x = (rect.left() + rect.right()) / 2
            rect_center_y = (rect.top() + rect.bottom()) / 2
            rect_width = rect.right() - rect.left()
            rect_height = rect.bottom() - rect.top()

            half_width = rect_width / 2
            half_height = rect_height / 2
            relative_points = [
                (-half_width, -half_height),
                (half_width, -half_height),
                (half_width, half_height),
                (-half_width, half_height),
            ]

            if template_angle != 0:
                import math

                cos_a = math.cos(math.radians(template_angle))
                sin_a = math.sin(math.radians(template_angle))
                rotated_points = []
                for x, y in relative_points:
                    new_x = x * cos_a - y * sin_a
                    new_y = x * sin_a + y * cos_a
                    rotated_points.append((new_x, new_y))
                relative_points = rotated_points

            scene_points = [QPointF(rect_center_x + x, rect_center_y + y) for x, y in relative_points]
            image_points = []
            for point in scene_points:
                image_point = inverse_transform.map(point)
                image_points.append([image_point.x(), image_point.y()])

            center_scene = QPointF(rect_center_x, rect_center_y)
            center_image = inverse_transform.map(center_scene)
            cx, cy = center_image.x(), center_image.y()

            xs = [p[0] for p in image_points]
            ys = [p[1] for p in image_points]
            white_frame_rect_local = [min(xs) - cx, min(ys) - cy, max(xs) - cx, max(ys) - cy]
            box_w = max(xs) - min(xs)
            box_h = max(ys) - min(ys)
            inferred_direction = "vertical" if box_h > box_w else "horizontal"

            template_data = {}
            if controller:
                selected_regions = self.model.get_selection()
                if selected_regions:
                    template_region = self.model.get_region_by_index(selected_regions[-1])
                    if template_region:
                        template_data = {
                            "font_family": template_region.get("font_family", "Arial"),
                            "font_size": template_region.get("font_size", 24),
                            "font_color": template_region.get("font_color", "#000000"),
                            "bg_colors": template_region.get(
                                "bg_colors", template_region.get("bg_color", [255, 255, 255])
                            ),
                            "alignment": template_region.get("alignment", "center"),
                            "direction": template_region.get("direction", "auto"),
                            "angle": template_region.get("angle", 0),
                            "letter_spacing": template_region.get("letter_spacing", 1.0),
                        }

            config = get_config_service().get_config()
            default_line_spacing = config.render.line_spacing if hasattr(config.render, "line_spacing") else 1.0
            default_letter_spacing = (
                config.render.letter_spacing if hasattr(config.render, "letter_spacing") else 1.0
            )
            default_stroke_width = config.render.stroke_width if hasattr(config.render, "stroke_width") else 0.07
            if default_line_spacing is None:
                default_line_spacing = 1.0
            if default_letter_spacing is None:
                default_letter_spacing = 1.0
            if default_stroke_width is None:
                default_stroke_width = 0.07

            new_region_data = {
                "text": "",
                "texts": [""],
                "translation": "",
                "translation_raw": "",
                "polygons": [image_points],
                "lines": [image_points],
                "white_frame_rect_local": white_frame_rect_local,
                "has_custom_white_frame": True,
                "center": [cx, cy],
                "font_family": template_data.get("font_family", "Arial"),
                "font_size": template_data.get("font_size", 24),
                "font_color": template_data.get("font_color", "#000000"),
                "bg_colors": template_data.get("bg_colors", [255, 255, 255]),
                "alignment": template_data.get("alignment", "center"),
                "direction": inferred_direction,
                "angle": template_data.get("angle", 0),
                "line_spacing": default_line_spacing,
                "letter_spacing": template_data.get("letter_spacing", default_letter_spacing),
                "stroke_width": default_stroke_width,
                "font_path": "",
            }

            controller = self._get_controller()
            if controller:
                from .commands import AddRegionCommand

                self._clear_pending_geometry_edits()
                command = AddRegionCommand(
                    model=self.model,
                    region_data=new_region_data,
                    description="Add New Text Box",
                )
                controller.execute_command(command)
                new_index = len(self.model.get_regions()) - 1
                self.model.set_selection([new_index])
                self.viewport().update()
                self.scene.update()

        except Exception as e:
            self.logger.error("创建文本区域失败: %s", e, exc_info=True)
