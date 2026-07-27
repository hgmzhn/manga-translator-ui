from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPalette, QTransform
from PyQt6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView
from services import get_logger
from ui.theme import is_dark_theme

from editor.editor_model import EditorModel
from editor.render_coordinator import RenderCoordinator

from .graphics_view_input import GraphicsViewInputMixin
from .graphics_view_layers import GraphicsViewLayersMixin
from .graphics_view_rendering import GraphicsViewRenderingMixin
from .mask_layer import MaskLayer
from .overlay_layer import OverlayLayerManager
from .selection_manager import SelectionManager


def canvas_background_color(theme: str | None = None) -> QColor:
    """画布底色。深浅判定统一走 ui.theme（gray/forest/sunset/rose 也是深色主题）。"""
    return QColor("#1A1C20" if is_dark_theme(theme) else "#F7F7F7")


class GraphicsView(
    GraphicsViewLayersMixin,
    GraphicsViewRenderingMixin,
    GraphicsViewInputMixin,
    QGraphicsView,
):
    """编辑画布：主文件只保留初始化、信号接线和共享状态。"""

    region_geometry_changed = pyqtSignal(int, dict)
    view_state_changed = pyqtSignal(object, object)
    region_drag_started = pyqtSignal()
    region_drag_finished = pyqtSignal()
    blank_canvas_pressed = pyqtSignal()

    MASK_PREVIEW_MAX_PIXELS = 2_000_000
    INPAINT_PREVIEW_MAX_PIXELS = 6_000_000

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

    def __init__(self, model: EditorModel, controller=None, parent=None, editor_view=None):
        super().__init__(parent)
        self.model = model
        self.controller = controller
        # 显式保存 EditorView 引用：addWidget 会把本视图换父到画布容器，
        # 事后再靠 parent() 摸 EditorView 已经失效
        self.editor_view = editor_view if editor_view is not None else parent
        self.logger = get_logger(__name__)
        self.render_coordinator = RenderCoordinator()

        self.scene = QGraphicsScene(self)
        # 编辑器频繁整批重建少量文本框；禁用 BSP 索引可避免 add/remove 时维护索引的额外开销。
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setScene(self.scene)

        self._image_item: QGraphicsPixmapItem = None
        self._q_image_ref = None
        self._preview_item: QGraphicsPixmapItem = None
        self.mask_layer = MaskLayer(self)
        self.overlay_layers = OverlayLayerManager(self)

        self._region_items = []
        self._snap_enabled = False
        self._center_scale_enabled = False
        self._pending_geometry_edit_kinds: dict[int, str] = {}
        self._immediate_render_update_pending = False

        self._active_tool = "select"
        self._brush_size = 30
        self._brush_color = "#ffffff"
        self._is_drawing = False
        self._current_draw_scene_points: list[QPointF] = []
        self._current_draw_mask_points: list[tuple[int, int]] = []
        self._current_draw_mask_shape: tuple[int, int] | None = None

        # 仿制印章：右键取样点（图像像素坐标）；偏移在采样后首次落笔锁定，
        # 跨笔画保持（传递仿制），再次右键取样时重置
        self._clone_sample_image_point = None
        self._clone_offset = None  # (dx, dy)：src = dest + offset
        self._clone_marker_item = None
        # 笔画进行时状态
        self._clone_drawing = False
        self._clone_old_overlay = None
        self._clone_working_overlay = None
        self._clone_composite = None
        self._clone_last_dab = None
        self._clone_preview_pixmap = None

        self._potential_drag = False
        self._drag_start_pos = None
        self._drag_threshold = 5
        self._region_drag_candidate = False
        self._region_drag_active = False
        self._hand_scroll_active = False

        self._is_drawing_textbox = False
        self._textbox_start_pos = None
        self._textbox_preview_item = None

        self.render_debounce_timer = QTimer(self)
        self.render_debounce_timer.setSingleShot(True)
        self.render_debounce_timer.setInterval(150)
        self.render_debounce_timer.timeout.connect(self._perform_render_update)

        self._setup_view()
        self._connect_model_signals()

    def set_controller(self, controller) -> None:
        self.controller = controller

    def set_snap_enabled(self, enabled: bool) -> None:
        """同步画布现有文本框，并作为后续新建文本框的默认吸附状态。"""
        self._snap_enabled = bool(enabled)
        for item in self._region_items:
            if item is not None:
                item.set_snap_enabled(self._snap_enabled)
        self.scene.update()

    def set_center_scale_enabled(self, enabled: bool) -> None:
        """设置文本框边/角拖拽是否围绕中心对称缩放。"""
        self._center_scale_enabled = bool(enabled)

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

    def _setup_view(self):
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        # 不用 CacheBackground：背景是纯色，缓存反而多付一张视口大小 pixmap 的分配+blit。
        # 不用 DontAdjustForAntialiasing：它把更新区域余量从 2px 砍到 0，
        # 与 1/lod 缩放的粗描边组合会留下残影。
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.apply_theme()
        self.selection_manager = SelectionManager(self.model, self.scene, lambda: self._region_items)

    def apply_theme(self, theme: str | None = None):
        canvas_color = canvas_background_color(theme)
        self.scene.setBackgroundBrush(canvas_color)
        self.setBackgroundBrush(canvas_color)
        self.setAutoFillBackground(True)
        palette = self.viewport().palette()
        palette.setColor(QPalette.ColorRole.Base, canvas_color)
        palette.setColor(QPalette.ColorRole.Window, canvas_color)
        self.viewport().setPalette(palette)
        self.viewport().setAutoFillBackground(True)
        self.scene.update()
        self.viewport().update()

    def _connect_model_signals(self):
        self.model.image_changed.connect(self.on_image_changed)
        self.model.regions_changed.connect(self.on_regions_changed)
        self.model.raw_mask_changed.connect(lambda mask: self.mask_layer.on_mask_data_changed("raw", mask))
        self.model.refined_mask_changed.connect(lambda mask: self.mask_layer.on_mask_data_changed("refined", mask))
        self.model.display_mask_type_changed.connect(self.mask_layer.on_display_mask_type_changed)
        self.model.inpainted_image_changed.connect(self.overlay_layers.on_inpainted_image_changed)
        self.model.paint_overlay_changed.connect(self.overlay_layers.on_paint_overlay_changed)
        self.model.stamp_overlay_changed.connect(self.overlay_layers.on_stamp_overlay_changed)
        self.model.region_display_mode_changed.connect(self.on_region_display_mode_changed)
        self.model.original_image_alpha_changed.connect(self.on_original_image_alpha_changed)
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
