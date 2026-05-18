from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView
from services import get_logger

from main_view_parts.theme import get_current_theme, get_theme_colors

from .editor_model import EditorModel
from .graphics_view_input import GraphicsViewInputMixin
from .graphics_view_layers import GraphicsViewLayersMixin
from .graphics_view_rendering import GraphicsViewRenderingMixin
from .render_coordinator import RenderCoordinator
from .selection_manager import SelectionManager


class GraphicsView(
    GraphicsViewLayersMixin,
    GraphicsViewRenderingMixin,
    GraphicsViewInputMixin,
    QGraphicsView,
):
    """编辑画布：主文件只保留初始化、信号接线和共享状态。"""

    region_geometry_changed = pyqtSignal(int, dict)
    _layout_result_ready = pyqtSignal(list)
    view_state_changed = pyqtSignal(object, object)

    MASK_PREVIEW_MAX_PIXELS = 2_000_000
    INPAINT_PREVIEW_MAX_PIXELS = 6_000_000

    @property
    def _text_render_cache(self):
        return self.render_coordinator.text_render_cache

    @_text_render_cache.setter
    def _text_render_cache(self, value):
        self.render_coordinator.text_render_cache = value

    @property
    def _text_blocks_cache(self):
        return self.render_coordinator.text_blocks

    @_text_blocks_cache.setter
    def _text_blocks_cache(self, value):
        self.render_coordinator.text_blocks = value

    @property
    def _dst_points_cache(self):
        return self.render_coordinator.dst_points

    @_dst_points_cache.setter
    def _dst_points_cache(self, value):
        self.render_coordinator.dst_points = value

    @property
    def _render_snapshot_cache(self):
        return self.render_coordinator.render_snapshots

    @_render_snapshot_cache.setter
    def _render_snapshot_cache(self, value):
        self.render_coordinator.render_snapshots = value

    def __init__(self, model: EditorModel, controller=None, parent=None):
        super().__init__(parent)
        self.model = model
        self.controller = controller
        self.logger = get_logger(__name__)
        self.render_coordinator = RenderCoordinator()

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self._image_item: QGraphicsPixmapItem = None
        self._raw_mask_item: QGraphicsPixmapItem = None
        self._refined_mask_item: QGraphicsPixmapItem = None
        self._inpainted_image_item: QGraphicsPixmapItem = None
        self._paint_overlay_item: QGraphicsPixmapItem = None
        self._q_image_ref = None
        self._inpainted_q_image_ref = None
        self._paint_overlay_q_image_ref = None
        self._preview_item: QGraphicsPixmapItem = None

        self._region_items = []
        self._pending_geometry_edit_kinds: dict[int, str] = {}

        self._active_tool = "select"
        self._brush_size = 30
        self._brush_color = "#ffffff"
        self._is_drawing = False
        self._current_draw_scene_points: list[QPointF] = []
        self._current_draw_mask_points: list[tuple[int, int]] = []
        self._current_draw_mask_shape: tuple[int, int] | None = None

        self._potential_drag = False
        self._drag_start_pos = None
        self._drag_threshold = 5

        self._is_drawing_textbox = False
        self._textbox_start_pos = None
        self._textbox_preview_item = None

        self.render_debounce_timer = QTimer(self)
        self.render_debounce_timer.setSingleShot(True)
        self.render_debounce_timer.setInterval(150)
        self.render_debounce_timer.timeout.connect(self._perform_render_update)

        self._setup_view()
        self._connect_model_signals()
        self._layout_result_ready.connect(self._apply_layout_result)

    def set_controller(self, controller) -> None:
        self.controller = controller

    def clear_pending_geometry_edits(self) -> None:
        self._clear_pending_geometry_edits()

    def get_live_region_state_patch(self, region_index: int) -> dict | None:
        if not (0 <= region_index < len(self._region_items)):
            return None

        item = self._region_items[region_index]
        geo = getattr(item, "geo", None) if item is not None else None
        if geo is None:
            return None

        patch = geo.to_persisted_state_patch()
        patch["center"] = list(geo.center)
        return patch

    def get_image_scene_rect(self) -> QRectF | None:
        """返回图片 item 在场景中的包围矩形，供对齐的"画布"参照模式使用。"""
        if self._image_item is not None:
            r = self._image_item.sceneBoundingRect()
            if r.isValid() and not r.isNull():
                return QRectF(r)
        return None

    def get_content_scene_rect(self) -> QRectF | None:
        rect = self.scene.itemsBoundingRect()
        if (not rect.isValid() or rect.isNull()) and self._image_item is not None:
            rect = self._image_item.sceneBoundingRect()
        if not rect.isValid() or rect.isNull():
            rect = self.scene.sceneRect()
        if rect.isValid() and not rect.isNull():
            return QRectF(rect)
        return None

    def _setup_view(self):
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.apply_theme()
        self.selection_manager = SelectionManager(self.model, self.scene, lambda: self._region_items)

    def apply_theme(self, theme: str | None = None):
        colors = get_theme_colors(theme or get_current_theme())
        canvas_color = QColor(colors["bg_canvas"])
        self.scene.setBackgroundBrush(canvas_color)
        self.setBackgroundBrush(canvas_color)
        self.scene.update()
        self.viewport().update()

    def _connect_model_signals(self):
        self.model.image_changed.connect(self.on_image_changed)
        self.model.regions_changed.connect(self.on_regions_changed)
        self.model.raw_mask_changed.connect(lambda mask: self.on_mask_data_changed("raw", mask))
        self.model.refined_mask_changed.connect(lambda mask: self.on_mask_data_changed("refined", mask))
        self.model.display_mask_type_changed.connect(self.on_display_mask_type_changed)
        self.model.inpainted_image_changed.connect(self.on_inpainted_image_changed)
        self.model.paint_overlay_changed.connect(self.on_paint_overlay_changed)
        self.model.region_display_mode_changed.connect(self.on_region_display_mode_changed)
        self.model.original_image_alpha_changed.connect(self.on_original_image_alpha_changed)
        self.model.region_style_updated.connect(self.on_region_style_updated)
        self.model.active_tool_changed.connect(self._on_active_tool_changed)
        self.model.brush_size_changed.connect(self._on_brush_size_changed)
        self.model.brush_color_changed.connect(self._on_brush_color_changed)

    def get_view_state(self):
        if self._image_item is None:
            return None, None
        center_scene = self.mapToScene(self.viewport().rect().center())
        return QTransform(self.transform()), QPointF(center_scene)

    def _emit_view_state_changed(self):
        transform, center_scene = self.get_view_state()
        if transform is None or center_scene is None:
            return
        self.view_state_changed.emit(transform, center_scene)
