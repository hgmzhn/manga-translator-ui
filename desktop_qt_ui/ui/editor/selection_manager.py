from PyQt6.QtCore import QObject, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QGraphicsRectItem
from qfluentwidgets import themeColor
from services import get_logger


class SelectionManager(QObject):
    """
    集中管理编辑器的选择逻辑：
    - 正向同步：Qt scene.selectionChanged → model.set_selection
    - 反向同步：model.selection_changed → Qt items setSelected
    - 框选：start/update/finish/cancel
    - _syncing 标志：防止循环同步，仅在本类内部管理
    """

    def __init__(self, model, scene, get_region_items_fn):
        """
        Args:
            model: EditorModel 实例
            scene: QGraphicsScene 实例
            get_region_items_fn: Callable，返回当前 region items 列表
        """
        super().__init__(scene)
        self._model = model
        self._scene = scene
        self._get_region_items = get_region_items_fn
        self._logger = get_logger(__name__)

        # 同步守卫
        self._syncing = False

        # 框选状态
        self._is_box_selecting = False
        self._box_select_start_pos = None
        self._box_select_rect_item: QGraphicsRectItem = None

        # 连接信号
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._model.selection_changed.connect(self._sync_qt_from_model)

    def _region_items(self):
        return list(self._get_region_items() or [])

    @staticmethod
    def _is_live_item(item) -> bool:
        try:
            return bool(item and hasattr(item, "scene") and item.scene())
        except (RuntimeError, AttributeError):
            return False

    def _set_item_selected(self, item, selected: bool, *, update: bool = False) -> None:
        if not self._is_live_item(item):
            return
        try:
            if item.isSelected() != selected:
                item.setSelected(selected)
            if update:
                item.update()
        except (RuntimeError, AttributeError):
            pass

    def _clear_box_select_rect(self) -> None:
        if self._box_select_rect_item is None:
            return
        self._box_select_rect_item.setVisible(False)
        self._box_select_rect_item.setRect(0, 0, 0, 0)

    def _selected_region_indices_from_scene(self) -> list[int]:
        from .graphics_items import RegionTextItem

        return sorted(
            item.region_index
            for item in self._region_items()
            if isinstance(item, RegionTextItem) and self._is_live_item(item) and item.isSelected()
        )

    # ------------------------------------------------------------------ #
    #  公开 API
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  框选 API
    # ------------------------------------------------------------------ #

    def start_box_select(self, scene_pos):
        """开始框选"""
        self._is_box_selecting = True
        self._box_select_start_pos = scene_pos

        # 创建或复用框选矩形
        need_create = self._box_select_rect_item is None
        if not need_create:
            try:
                _ = self._box_select_rect_item.scene()
            except RuntimeError:
                need_create = True
                self._box_select_rect_item = None

        if need_create:
            self._box_select_rect_item = self._scene.addRect(0, 0, 0, 0)
            self._box_select_rect_item.setZValue(300)

        # 每次开始框选时现取主题色，避免缓存的旧 themeColor 在换主题后残留
        accent = themeColor().toRgb()
        if not accent.isValid():
            accent = QColor("#0F6CBD")
        fill = QColor(accent)
        accent.setAlpha(190)
        fill.setAlpha(36)
        pen = QPen(accent)
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._box_select_rect_item.setPen(pen)
        self._box_select_rect_item.setBrush(QBrush(fill))

        self._box_select_rect_item.setVisible(True)

    def update_box_select(self, scene_pos):
        """更新框选矩形"""
        if not self._is_box_selecting or self._box_select_start_pos is None:
            return False

        if self._box_select_rect_item is None:
            self._is_box_selecting = False
            self._box_select_start_pos = None
            return False

        try:
            rect = QRectF(self._box_select_start_pos, scene_pos).normalized()
            self._box_select_rect_item.setRect(rect)
            return True
        except RuntimeError:
            self._box_select_rect_item = None
            self._is_box_selecting = False
            self._box_select_start_pos = None
            return False

    def finish_box_select(self, ctrl_pressed):
        """完成框选，计算相交区域并更新选择"""
        self._is_box_selecting = False
        self._box_select_start_pos = None

        if not self._box_select_rect_item:
            return

        try:
            select_rect = self._box_select_rect_item.rect()
            self._clear_box_select_rect()

            # 使用 item shape 做精确命中，避免仅按 boundingRect 误选旋转/细长区域
            from .graphics_items import RegionTextItem
            region_items = self._region_items()
            hit_items = self._scene.items(select_rect, Qt.ItemSelectionMode.IntersectsItemShape)
            selected_indices = sorted(
                {
                    int(item.region_index)
                    for item in hit_items
                    if isinstance(item, RegionTextItem)
                    and self._is_live_item(item)
                }
            )

            # 批量设置 Qt item 选择状态
            self._syncing = True
            try:
                if not ctrl_pressed:
                    for item in region_items:
                        self._set_item_selected(item, False)

                for idx in selected_indices:
                    if 0 <= idx < len(region_items):
                        self._set_item_selected(region_items[idx], True)
            finally:
                self._syncing = False

            # 手动触发一次同步
            self._on_scene_selection_changed()

        except RuntimeError:
            self._box_select_rect_item = None

    def cancel_box_select(self):
        """中断框选（右键菜单/失焦/Escape 等 release 会丢失的场合）：
        不改变选择，只清理进行中状态与预览矩形。"""
        self._is_box_selecting = False
        self._box_select_start_pos = None
        try:
            self._clear_box_select_rect()
        except RuntimeError:
            self._box_select_rect_item = None

    @property
    def is_box_selecting(self):
        return self._is_box_selecting

    # ------------------------------------------------------------------ #
    #  同步（内部）
    # ------------------------------------------------------------------ #

    def _on_scene_selection_changed(self):
        """正向同步：Qt scene → model"""
        if self._syncing:
            return

        selected_indices = self._selected_region_indices_from_scene()

        if selected_indices != self._model.get_selection():
            self._model.set_selection(selected_indices)

    def _sync_qt_from_model(self, selected_indices):
        """反向同步：model → Qt items"""
        self._syncing = True
        try:
            region_items = self._region_items()

            # 清除所有 item 的选择
            for item in region_items:
                self._set_item_selected(item, False, update=True)

            # 设置新选中的 items
            for idx in selected_indices:
                if 0 <= idx < len(region_items):
                    self._set_item_selected(region_items[idx], True, update=True)

            # 强制场景更新
            if self._scene:
                self._scene.update()
        except Exception as e:
            self._logger.warning("Selection sync failed: %s", e, exc_info=True)
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ #
    #  生命周期
    # ------------------------------------------------------------------ #

    def suppress_forward_sync(self, suppress):
        """批量操作时暂停/恢复正向同步"""
        self._syncing = suppress

    def restore_selection_after_rebuild(self):
        """items 重建后恢复选择状态（从 model 同步到 Qt）"""
        self._sync_qt_from_model(self._model.get_selection())

    def clear_state(self):
        """清理所有框选状态（切换图片等场景）"""
        if self._box_select_rect_item:
            try:
                if self._box_select_rect_item.scene():
                    self._scene.removeItem(self._box_select_rect_item)
            except RuntimeError:
                pass
        self._box_select_rect_item = None
        self._is_box_selecting = False
        self._box_select_start_pos = None

    def on_scene_cleared(self):
        """当 scene.clear() 被调用后，重置框选矩形引用（已被场景删除）"""
        self._box_select_rect_item = None
