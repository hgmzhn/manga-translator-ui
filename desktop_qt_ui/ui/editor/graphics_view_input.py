from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import QEvent, QPointF, Qt, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsView
from qfluentwidgets import Action, RoundMenu
from services import get_config_service

from .graphics_items import RegionTextItem


class GraphicsViewInputMixin:
    # 视图缩放上下限：防止滚轮缩放跑飞（极小 lod 还会造成 item 描边溢出 boundingRect 残影）
    MIN_VIEW_SCALE = 0.05
    MAX_VIEW_SCALE = 50.0

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

    def _apply_zoom(self, factor: float):
        """按倍率缩放视图，并钳制在 [MIN_VIEW_SCALE, MAX_VIEW_SCALE]。"""
        current = abs(float(self.transform().m11()))
        if current <= 0.0:
            current = 1.0
        target = min(max(current * factor, self.MIN_VIEW_SCALE), self.MAX_VIEW_SCALE)
        effective = target / current
        if abs(effective - 1.0) > 1e-9:
            self.scale(effective, effective)
        self._update_cursor()
        self._emit_view_state_changed()

    def wheelEvent(self, event):
        delta_y = int(event.angleDelta().y())
        if delta_y == 0:
            # 横向滚轮/触摸板：不是缩放手势，交回默认处理
            super().wheelEvent(event)
            return
        zoom_in_factor = 1.15
        self._apply_zoom(zoom_in_factor if delta_y > 0 else 1.0 / zoom_in_factor)

    # ------------------------- 统一交互终结 -------------------------

    def _has_active_interaction(self) -> bool:
        return bool(
            self.selection_manager.is_box_selecting
            or self._is_drawing
            or self._is_drawing_textbox
            or self._clone_drawing
            or self._region_drag_active
            or self._hand_scroll_active
        )

    def _cancel_active_interaction(self, commit: bool, *, ctrl_pressed: bool = False, allow_tool_switch: bool = True):
        """集中终结所有"进行中"的画布交互。

        右键菜单(menu.exec 抓鼠标)、模态框、窗口失活、快捷键切工具都会让
        mouseRelease 丢失，进行中状态若不在这里统一清理，框选矩形/绘制预览
        会永久留在场景里挡住输入。

        commit=True: 按当前(调用时的旧)工具语义提交——正常 release 和切工具前走这里；
        commit=False: 直接丢弃——右键菜单、Escape、焦点丢失等场合。
        allow_tool_switch=False: 提交文本框时不再回切 select 工具（切工具入口用，防重入）。
        """
        if self.selection_manager.is_box_selecting:
            if commit:
                self.selection_manager.finish_box_select(ctrl_pressed)
            else:
                self.selection_manager.cancel_box_select()

        if self._is_drawing_textbox:
            if commit:
                self._finish_textbox_drawing(switch_to_select=allow_tool_switch)
            else:
                self._abort_textbox_drawing()

        if self._clone_drawing:
            if commit:
                self._finish_clone_stroke()
            else:
                self._reset_clone_stroke_state()
                self._clear_preview()

        if self._is_drawing:
            if commit:
                self._finish_drawing()
            else:
                self._reset_drawing_state()
                self._clear_preview()

        if self._region_drag_active:
            self.region_drag_finished.emit()
        self._region_drag_candidate = False
        self._region_drag_active = False
        self._potential_drag = False
        self._drag_start_pos = None

        self._end_hand_scroll()

    # ------------------------- 中键平移（对称合成左键） -------------------------

    def _begin_hand_scroll(self, event):
        """中键按下：切 ScrollHandDrag 并合成左键 press 喂给 QGraphicsView。"""
        if self._hand_scroll_active:
            return
        self._hand_scroll_active = True
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            event.position(),
            event.globalPosition(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            event.modifiers(),
        )
        super().mousePressEvent(press)

    def _end_hand_scroll(self, event=None):
        """与 _begin_hand_scroll 对称：合成左键 release 喂给 QGraphicsView，
        让内部 handScrolling 状态正常复位，然后才切回 NoDrag。"""
        if not self._hand_scroll_active:
            return
        self._hand_scroll_active = False
        if event is not None:
            position = event.position()
            global_position = event.globalPosition()
            modifiers = event.modifiers()
        else:
            global_pos = QCursor.pos()
            position = QPointF(self.viewport().mapFromGlobal(global_pos))
            global_position = QPointF(global_pos)
            modifiers = Qt.KeyboardModifier.NoModifier
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            position,
            global_position,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            modifiers,
        )
        super().mouseReleaseEvent(release)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._has_active_interaction():
            self._cancel_active_interaction(commit=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # 模态框/窗口失活/焦点转移都会吞掉后续 release：统一丢弃进行中交互
        self._cancel_active_interaction(commit=False)
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        editor_view = getattr(self, "editor_view", None)
        if editor_view is not None and hasattr(editor_view, "force_save_property_panel_edits"):
            editor_view.force_save_property_panel_edits()

        self.setFocus()

        if self._active_tool == "draw_textbox" and event.button() == Qt.MouseButton.LeftButton:
            self._start_drawing_textbox(event.pos())
            event.accept()
            return

        if self._active_tool == "clone":
            if event.button() == Qt.MouseButton.RightButton:
                self._set_clone_sample_point(event.pos())
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                self._start_clone_stroke(event.pos())
                event.accept()
                return

        if self._active_tool in ["pen", "eraser", "brush", "paint", "paint_erase", "stamp_erase"] and event.button() == Qt.MouseButton.LeftButton:
            self._start_drawing(event.pos())
            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._begin_hand_scroll(event)
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
            self._region_drag_candidate = bool(clicked_region_item)
            self._region_drag_active = False
            self._drag_start_pos = event.pos() if clicked_region_item else None

            if item_at_pos is None or item_at_pos == self._image_item:
                self.blank_canvas_pressed.emit()
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
        if self._active_tool == "clone":
            image_pos = self._scene_to_image_point(self.mapToScene(event.pos()))
            self._update_clone_marker(image_pos)
            if self._clone_drawing and bool(event.buttons() & Qt.MouseButton.LeftButton):
                self._clone_dab_segment(image_pos)
                event.accept()
                return

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
        if (
            self._region_drag_candidate
            and not self._region_drag_active
            and self._drag_start_pos is not None
            and bool(event.buttons() & Qt.MouseButton.LeftButton)
        ):
            delta = event.pos() - self._drag_start_pos
            if abs(delta.x()) + abs(delta.y()) >= self._drag_threshold:
                self._region_drag_active = True
                self.region_drag_started.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._hand_scroll_active:
            self._end_hand_scroll(event)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # 这三种交互原本各自 accept 并 return，保持该行为
            exclusive = (
                self.selection_manager.is_box_selecting
                or self._is_drawing_textbox
                or self._clone_drawing
            )
            ctrl_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._cancel_active_interaction(commit=True, ctrl_pressed=ctrl_pressed)
            if exclusive:
                event.accept()
                return
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
            elif self._active_tool in ("paint_erase", "stamp_erase"):
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
        # paint / stamp 模式：始终匹配底图像素尺寸
        if self._active_tool in ("paint", "paint_erase", "stamp_erase"):
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

        # 仿制印章：把取样像素写入 paint overlay
        if self._active_tool == "clone":
            self._finish_clone_stroke()
            self._reset_drawing_state()
            return

        # Paint / Stamp overlay 画笔与擦除路径
        if self._active_tool in ("paint", "paint_erase", "stamp_erase"):
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
            from editor.commands import MaskEditCommand

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
        """把当前笔画应用到 paint / stamp overlay 图层。"""
        layer = "stamp" if self._active_tool == "stamp_erase" else "paint"
        try:
            shape = self._current_draw_mask_shape
            stroke_mask = self._build_stroke_mask(self._current_draw_mask_points, shape)
            if not np.any(stroke_mask):
                self._clear_preview()
                return

            old_overlay, new_overlay = self._prepare_overlay_pair(shape, layer)

            pixel_mask = stroke_mask > 0

            if self._active_tool == "paint":
                color = QColor(self._brush_color)
                if not color.isValid():
                    color = QColor(255, 0, 0)
                new_overlay[pixel_mask, 0] = color.red()
                new_overlay[pixel_mask, 1] = color.green()
                new_overlay[pixel_mask, 2] = color.blue()
                new_overlay[pixel_mask, 3] = 255
            else:  # paint_erase / stamp_erase
                new_overlay[pixel_mask] = 0

            controller = self._get_controller()
            if not controller:
                self._clear_preview()
                return

            if old_overlay is not None and np.array_equal(old_overlay, new_overlay):
                self._clear_preview()
                return

            from editor.commands import PaintOverlayEditCommand

            command = PaintOverlayEditCommand(
                model=self.model,
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                layer=layer,
            )
            controller.execute_command(command)
        except Exception as e:
            self.logger.error("Paint overlay stroke failed: %s", e, exc_info=True)
        finally:
            self._clear_preview()

    def _prepare_overlay_pair(self, shape: tuple[int, int], layer: str = "paint"):
        """取当前 paint/stamp overlay，规范成 (h,w,4) uint8，返回 (old_overlay, new_overlay 副本)。"""
        h, w = shape
        overlay = (
            self.model.get_stamp_overlay_image()
            if layer == "stamp"
            else self.model.get_paint_overlay_image()
        )
        if overlay is None:
            return None, np.zeros((h, w, 4), dtype=np.uint8)

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
            return None, np.zeros((h, w, 4), dtype=np.uint8)
        old_overlay = old_arr.astype(np.uint8, copy=False)
        return old_overlay, old_overlay.copy()

    # ------------------------- 仿制印章 -------------------------

    def _set_clone_sample_point(self, view_pos):
        """右键取样：记录取样点并清空偏移锁（再次落笔时重新锁定相对位移）。"""
        if self._image_item is None:
            return
        self._clone_sample_image_point = self._scene_to_image_point(self.mapToScene(view_pos))
        self._clone_offset = None
        self._update_clone_marker(None)

    def _update_clone_marker(self, cursor_image_point):
        """更新取样圈位置：偏移锁定后跟随光标（src = 光标 + offset），否则停在取样点。"""
        if self._clone_sample_image_point is None or self._image_item is None:
            self._clear_clone_marker()
            return
        if self._clone_offset is not None and cursor_image_point is not None:
            center_x = cursor_image_point.x() + self._clone_offset[0]
            center_y = cursor_image_point.y() + self._clone_offset[1]
        else:
            center_x = self._clone_sample_image_point.x()
            center_y = self._clone_sample_image_point.y()

        # 取样圈是场景顶层 item：挂在底图下 Z 值只在父项内部生效，
        # 会被 overlay/mask/preview 图层盖住
        marker = self._clone_marker_item
        marker_alive = marker is not None
        if marker_alive:
            try:
                marker_alive = marker.scene() is self.scene
            except RuntimeError:
                marker_alive = False
        if not marker_alive:
            self._clear_clone_marker()
            marker = QGraphicsEllipseItem()
            pen = QPen(QColor(239, 68, 68, 240), 0)
            pen.setStyle(Qt.PenStyle.DashLine)
            marker.setPen(pen)
            marker.setBrush(Qt.GlobalColor.transparent)
            marker.setZValue(10_000)
            self.scene.addItem(marker)
            self._clone_marker_item = marker

        # 图像像素坐标 → 场景坐标（底图可能带降采样补偿变换）
        radius = max(2.0, self._brush_size / 2.0)
        center_scene = self._image_item.mapToScene(QPointF(center_x, center_y))
        edge_x = self._image_item.mapToScene(QPointF(center_x + radius, center_y))
        edge_y = self._image_item.mapToScene(QPointF(center_x, center_y + radius))
        rx = abs(edge_x.x() - center_scene.x()) or radius
        ry = abs(edge_y.y() - center_scene.y()) or radius
        marker.setRect(center_scene.x() - rx, center_scene.y() - ry, rx * 2, ry * 2)

    def _clear_clone_marker(self):
        if self._clone_marker_item is not None:
            try:
                scene = self._clone_marker_item.scene()
                if scene is not None:
                    scene.removeItem(self._clone_marker_item)
            except RuntimeError:
                pass
            self._clone_marker_item = None

    def _start_clone_stroke(self, view_pos):
        """左键按下：偏移未锁定时用（取样点 - 落笔点）锁定；随后逐点实时盖印。"""
        if self._clone_sample_image_point is None or self._image_item is None:
            return
        pos = self._scene_to_image_point(self.mapToScene(view_pos))
        if self._clone_offset is None:
            self._clone_offset = (
                int(round(self._clone_sample_image_point.x() - pos.x())),
                int(round(self._clone_sample_image_point.y() - pos.y())),
            )

        pixmap = self._image_item.pixmap()
        h, w = pixmap.height(), pixmap.width()
        if h <= 0 or w <= 0:
            return
        old_overlay, working = self._prepare_overlay_pair((h, w), "stamp")
        composite = self._build_clone_composite_rgb((h, w), working)
        if composite is None:
            return
        self._clone_old_overlay = old_overlay
        self._clone_working_overlay = working
        self._clone_composite = composite
        self._clone_last_dab = None
        self._clone_drawing = True

        # 预览层：与其它绘制工具共用 preview item，自维护一张持久 pixmap 做增量盖印
        if self._preview_item is None:
            preview = QPixmap(pixmap.size())
            preview.fill(Qt.GlobalColor.transparent)
            self._preview_item = self.scene.addPixmap(preview)
            self._preview_item.setZValue(12)
            self._scale_mask_item(self._preview_item)
        self._clone_preview_pixmap = QPixmap(pixmap.size())
        self._clone_preview_pixmap.fill(Qt.GlobalColor.transparent)
        self._preview_item.setVisible(True)

        self._clone_dab_segment(pos)

    def _build_clone_composite_rgb(self, shape: tuple[int, int], working_overlay):
        """活体取样源 = 画布可见内容：修复图(有则盖住底图) + 画笔层 + 印章层工作副本。

        盖印时同步更新，保证同一笔里刚盖的内容可继续被取样传递。
        """
        if self._image_item is None:
            return None
        h, w = shape

        # 底：修复图优先（画布上它盖在底图之上），没有才用底图
        base_rgb = None
        inpainted = self.model.get_inpainted_image()
        if inpainted is not None:
            arr = np.asarray(inpainted)
            if arr.ndim == 3 and arr.shape[2] >= 3 and arr.size > 0:
                arr = arr[..., :3].astype(np.uint8, copy=False)
                if arr.shape[:2] != (h, w):
                    arr = cv2.resize(arr, (w, h), interpolation=cv2.INTER_LINEAR)
                base_rgb = arr
        if base_rgb is None:
            base = self._qimage_to_rgba_array(self._image_item.pixmap().toImage())
            if base.shape[:2] != (h, w):
                return None
            base_rgb = base[..., :3]

        composite = base_rgb.astype(np.float32)
        for overlay in (self.model.get_paint_overlay_image(), working_overlay):
            if overlay is None:
                continue
            arr = np.asarray(overlay)
            if arr.ndim != 3 or arr.shape[2] != 4 or arr.shape[:2] != (h, w):
                continue
            alpha = arr[..., 3:4].astype(np.float32) / 255.0
            composite = composite * (1.0 - alpha) + arr[..., :3].astype(np.float32) * alpha
        return np.clip(composite, 0, 255).astype(np.uint8)

    def _clone_dab_segment(self, image_pos):
        """从上一个 dab 点插值到当前点，逐点盖印，并增量刷新预览。"""
        if not self._clone_drawing or self._clone_composite is None:
            return
        px, py = float(image_pos.x()), float(image_pos.y())
        points = []
        last = self._clone_last_dab
        if last is None:
            points.append((px, py))
        else:
            dist = ((px - last[0]) ** 2 + (py - last[1]) ** 2) ** 0.5
            spacing = max(1.0, self._brush_size / 4.0)
            steps = max(1, int(dist / spacing))
            for i in range(1, steps + 1):
                t = i / steps
                points.append((last[0] + (px - last[0]) * t, last[1] + (py - last[1]) * t))
        self._clone_last_dab = (px, py)

        dirty = []
        for x, y in points:
            rect = self._clone_dab(x, y)
            if rect is not None:
                dirty.append(rect)
        if dirty:
            self._refresh_clone_preview(dirty)

    def _clone_dab(self, px: float, py: float):
        """单次盖印：硬边像素圆，从合成源按锁定偏移取样写入工作层与合成源。"""
        working = self._clone_working_overlay
        composite = self._clone_composite
        if working is None or composite is None or self._clone_offset is None:
            return None
        h, w = composite.shape[:2]
        r = max(0.5, self._brush_size / 2.0)
        # 像素格子对齐（同参考实现），保证印章边缘不随移动方向变化
        snap_x = float(np.floor(px)) + 0.5
        snap_y = float(np.floor(py)) + 0.5
        left = int(np.floor(snap_x - r))
        top = int(np.floor(snap_y - r))
        right = int(np.ceil(snap_x + r))
        bottom = int(np.ceil(snap_y + r))

        ox, oy = self._clone_offset  # src = dest + offset
        # 目标窗口与取样窗口同时裁进图像边界
        x0 = max(left, 0, -ox)
        y0 = max(top, 0, -oy)
        x1 = min(right, w, w - ox)
        y1 = min(bottom, h, h - oy)
        if x0 >= x1 or y0 >= y1:
            return None

        yy, xx = np.ogrid[y0:y1, x0:x1]
        mask = ((xx + 0.5) - snap_x) ** 2 + ((yy + 0.5) - snap_y) ** 2 <= r * r
        if not np.any(mask):
            # 1 像素单点保底
            cx, cy = int(np.floor(px)), int(np.floor(py))
            if not (x0 <= cx < x1 and y0 <= cy < y1):
                return None
            mask = np.zeros((y1 - y0, x1 - x0), dtype=bool)
            mask[cy - y0, cx - x0] = True

        src_patch = composite[y0 + oy : y1 + oy, x0 + ox : x1 + ox].copy()
        dest_work = working[y0:y1, x0:x1]
        dest_work[mask, :3] = src_patch[mask]
        dest_work[mask, 3] = 255
        composite[y0:y1, x0:x1][mask] = src_patch[mask]
        return (x0, y0, x1, y1)

    def _refresh_clone_preview(self, dirty_rects):
        """把工作层的脏区增量画进预览 pixmap 并提交显示。"""
        if self._preview_item is None or self._clone_preview_pixmap is None:
            return
        working = self._clone_working_overlay
        if working is None:
            return
        painter = QPainter(self._clone_preview_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for x0, y0, x1, y1 in dirty_rects:
            patch = np.ascontiguousarray(working[y0:y1, x0:x1])
            patch_h, patch_w = patch.shape[:2]
            patch_image = QImage(
                patch.data, patch_w, patch_h, patch_w * 4, QImage.Format.Format_RGBA8888
            )
            painter.drawImage(x0, y0, patch_image)
        painter.end()
        self._preview_item.setPixmap(self._clone_preview_pixmap)
        self.scene.update()
        self.viewport().update()

    @staticmethod
    def _qimage_to_rgba_array(image: QImage) -> np.ndarray:
        img = image.convertToFormat(QImage.Format.Format_RGBA8888)
        h, w = img.height(), img.width()
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        buf = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())[:, : w * 4]
        return buf.reshape(h, w, 4).copy()

    def _finish_clone_stroke(self):
        """笔画结束：以整笔为单位提交撤销命令（偏移保持锁定，供下一笔传递仿制）。"""
        try:
            if not self._clone_drawing:
                return
            old_overlay = self._clone_old_overlay
            new_overlay = self._clone_working_overlay
            if new_overlay is None:
                return
            controller = self._get_controller()
            if not controller:
                return
            if old_overlay is not None and np.array_equal(old_overlay, new_overlay):
                return
            if old_overlay is None and not np.any(new_overlay[..., 3]):
                return

            from editor.commands import PaintOverlayEditCommand

            command = PaintOverlayEditCommand(
                model=self.model,
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                layer="stamp",
            )
            controller.execute_command(command)
        except Exception as e:
            self.logger.error("Clone stamp stroke failed: %s", e, exc_info=True)
        finally:
            self._reset_clone_stroke_state()
            self._clear_preview()

    def _reset_clone_stroke_state(self):
        self._clone_drawing = False
        self._clone_old_overlay = None
        self._clone_working_overlay = None
        self._clone_composite = None
        self._clone_last_dab = None
        self._clone_preview_pixmap = None

    def _reset_drawing_state(self):
        self._is_drawing = False
        self._current_draw_scene_points = []
        self._current_draw_mask_points = []
        self._current_draw_mask_shape = None

    def _clear_preview(self):
        """移除预览 item 并置 None，与两个创建路径（懒创建）保持对称。

        注意不能 pixmap().fill()：QPixmap 隐式共享，fill 的是取出的副本，
        item 上的真像素不变，下次 setVisible(True) 时旧笔迹会整条闪回。
        """
        if self._preview_item is None:
            return
        try:
            if self._preview_item.scene() is not None:
                self.scene.removeItem(self._preview_item)
        except RuntimeError:
            pass
        self._preview_item = None
        self.scene.update()
        self.viewport().update()

    @pyqtSlot(str)
    def _on_active_tool_changed(self, tool: str):
        if tool == self._active_tool:
            return
        # 先按旧工具语义提交进行中的笔画/框选，再切换 _active_tool，
        # 绝不能让旧笔画落进新工具的提交分支
        self._cancel_active_interaction(commit=True, allow_tool_switch=False)
        self._active_tool = tool
        if tool != "clone":
            self._clone_sample_image_point = None
            self._clone_offset = None
            self._clear_clone_marker()
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
        if self._active_tool in ["pen", "eraser", "brush", "paint", "paint_erase", "clone", "stamp_erase"]:
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
            elif self._active_tool == "clone":
                inner_color = QColor(0, 200, 120)
            elif self._active_tool in ["pen", "brush"]:
                inner_color = QColor(Qt.GlobalColor.red)
            elif self._active_tool in ("paint_erase", "stamp_erase"):
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
        self._apply_zoom(1.15)

    @pyqtSlot()
    def zoom_out(self):
        self._apply_zoom(1 / 1.15)

    @pyqtSlot()
    def fit_to_window(self):
        if self._image_item:
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._emit_view_state_changed()

    def contextMenuEvent(self, event):
        # 仿制印章模式下右键用于取样，完全屏蔽右键菜单
        if self._active_tool == "clone":
            event.accept()
            return
        # menu.exec 会抓走鼠标，进行中交互的 release 必然丢失：先统一丢弃
        self._cancel_active_interaction(commit=False)
        selected_regions = self.model.get_selection()
        selection_count = len(selected_regions)
        menu = RoundMenu(parent=self)

        def add_item(text: str, slot):
            action = Action(text, self)
            action.triggered.connect(slot)
            menu.addAction(action)

        if selection_count > 0:
            add_item("🔍 OCR识别选中项", self._ocr_selected_regions)
            add_item("🌐 翻译选中项", self._translate_selected_regions)
            menu.addSeparator()

            if selection_count == 1:
                add_item("📋 复制区域", self._copy_selected_region)
                add_item("🎨 粘贴样式", self._paste_region_style)
                menu.addSeparator()

            add_item(f"🗑️ 删除选中的 {selection_count} 个区域", self._delete_selected_regions)
        else:
            add_item("➕ 添加文本框", self._add_text_box)
            add_item("📋 粘贴区域", self._paste_region)
            menu.addSeparator()
            add_item("🔄 刷新视图", self._refresh_view)

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

    def _abort_textbox_drawing(self):
        """丢弃进行中的文本框绘制：清状态 + 隐藏预览矩形。"""
        self._is_drawing_textbox = False
        self._textbox_start_pos = None
        if self._textbox_preview_item is not None:
            self._textbox_preview_item.setVisible(False)
            self._textbox_preview_item.setRect(0, 0, 0, 0)

    def _finish_textbox_drawing(self, switch_to_select: bool = True):
        if not self._is_drawing_textbox or self._textbox_start_pos is None:
            return

        rect = self._textbox_preview_item.rect() if self._textbox_preview_item else None
        self._abort_textbox_drawing()

        min_size = 20
        if rect is None or rect.width() < min_size or rect.height() < min_size:
            return

        self._create_new_text_region(rect)
        if switch_to_select:
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
            }

            controller = self._get_controller()
            if controller:
                from editor.commands import AddRegionCommand

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
