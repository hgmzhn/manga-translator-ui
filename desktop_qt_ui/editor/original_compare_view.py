from main_view_parts.theme import get_current_theme, get_theme_colors
from editor.image_utils import build_display_image_frame
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class OriginalCompareView(QGraphicsView):
    """只读原图预览视图，用于和当前编辑画布做左右对比。"""
    COMPARE_PREVIEW_MAX_PIXELS = 3_000_000

    def __init__(self, source_view=None, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self._source_view = source_view

        self._image_item: QGraphicsPixmapItem | None = None
        self._q_image_ref = None
        self._last_center_scene: QPointF | None = None
        self._last_scene_rect: QRectF | None = None
        self._last_transform = QTransform()
        self._has_pending_image = False
        self._pending_image = None

        self._setup_view()

    def _setup_view(self):
        self.setInteractive(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.apply_theme()

    def apply_theme(self, theme: str | None = None):
        colors = get_theme_colors(theme or get_current_theme())
        canvas_color = QColor(colors["bg_canvas"])
        self.scene.setBackgroundBrush(canvas_color)
        self.setBackgroundBrush(canvas_color)
        self.scene.update()
        self.viewport().update()

    def set_source_view(self, source_view) -> None:
        self._source_view = source_view
        if self._last_center_scene is None:
            return

        scene_rect = self._resolve_source_scene_rect()
        if scene_rect is not None:
            self._last_scene_rect = scene_rect
            self.scene.setSceneRect(scene_rect)
            self.viewport().update()

    def set_image(self, image):
        self.scene.clear()
        self._image_item = None
        self._q_image_ref = None

        if image is None:
            return

        try:
            display_frame = build_display_image_frame(image, max_pixels=self.COMPARE_PREVIEW_MAX_PIXELS)
            if display_frame is None:
                raise ValueError("display frame is empty")
            self._q_image_ref = display_frame.qimage
        except Exception:
            self._q_image_ref = None
            return
        pixmap = QPixmap.fromImage(self._q_image_ref)
        self._image_item = self.scene.addPixmap(pixmap)
        if display_frame.is_downsampled:
            transform = QTransform()
            transform.scale(display_frame.scale_x, display_frame.scale_y)
            self._image_item.setTransform(transform)
        self.scene.setSceneRect(self._image_item.sceneBoundingRect())

        if self._last_center_scene is not None:
            self.sync_view_state(self._last_transform, self._last_center_scene)
        else:
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_image_when_visible(self, image, visible: bool):
        self._has_pending_image = True
        self._pending_image = image
        if not visible:
            return
        self.flush_pending_image()

    def flush_pending_image(self, fallback_image=None):
        image = self._pending_image if self._has_pending_image else fallback_image
        self.set_image(image)

    def sync_view_state(self, transform, center_scene):
        if transform is None or center_scene is None:
            return

        self._last_transform = QTransform(transform)
        self._last_center_scene = QPointF(center_scene)
        self._last_scene_rect = self._resolve_source_scene_rect()

        if self._image_item is None:
            return

        scene_rect = self._last_scene_rect or self._image_item.sceneBoundingRect()
        if scene_rect.isValid() and not scene_rect.isNull():
            self.scene.setSceneRect(scene_rect)
        self.setTransform(QTransform(transform))
        self.centerOn(center_scene)
        self.viewport().update()

    def _resolve_source_scene_rect(self) -> QRectF | None:
        if self._source_view is None:
            return None
        return self._source_view.get_content_scene_rect()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._image_item is not None and self._last_center_scene is not None:
            self.centerOn(self._last_center_scene)
