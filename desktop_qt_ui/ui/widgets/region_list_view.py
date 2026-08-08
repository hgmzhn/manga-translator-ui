
from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    ListWidget,
    TextEdit,
    ToolButton,
)
from services import get_i18n_manager

from .hover_hint import set_hover_hint
from .widget_cleanup import delete_widget


_REGION_KEY_ROLE = Qt.ItemDataRole.UserRole.value + 1


class _RegionDragHandle(ToolButton):
    drag_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(FIF.MOVE)
        self._drag_start: QPoint | None = None
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._drag_start = None
            self.drag_requested.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class RegionListView(ListWidget):
    """
    显示和管理当前图片中所有文本区域的列表。
    """
    region_selected = pyqtSignal(list)
    region_move_requested = pyqtSignal(int, int)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.i18n = get_i18n_manager()
        self._block_signals = False
        self._pending_regions = None
        self._pending_drafts = {}
        self._pending_selection = []
        self._drag_source_row: int | None = None
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.currentItemChanged.connect(self._on_item_changed)

    def _t(self, key: str) -> str:
        return self.i18n.translate(key) if self.i18n is not None else key

    def on_regions_changed(self, change):
        regions = self.model.get_regions()
        kind = getattr(change, "kind", "reset")
        if kind == "reset" or self._pending_regions is not None or not self.isVisible():
            self.update_regions(regions)
            return

        indices = tuple(getattr(change, "indices", ()) or ())
        if kind not in {"updated", "inserted", "removed"} or not indices:
            self.update_regions(regions)
            return

        drafts = self._collect_dirty_translations()
        fallback_to_full_sync = False
        self._block_signals = True
        self.setUpdatesEnabled(False)
        try:
            if kind == "updated":
                for index in indices:
                    if 0 <= index < min(self.count(), len(regions)):
                        self._update_region_item(
                            index,
                            regions[index],
                            drafts.get(self._region_key(index)),
                        )
            elif kind == "inserted" and self.count() + len(indices) == len(regions):
                first_changed = min(indices)
                for index in sorted(indices):
                    if 0 <= index < len(regions):
                        self._add_region_item(
                            index,
                            regions[index],
                            drafts.get(self._region_key(index)),
                            insert=True,
                        )
                self._update_rows_from(first_changed, regions, drafts)
            elif kind == "removed" and self.count() - len(indices) == len(regions):
                first_changed = min(indices)
                for index in sorted(indices, reverse=True):
                    if 0 <= index < self.count():
                        self._remove_region_row(index)
                self._update_rows_from(first_changed, regions, drafts)
            else:
                self._pending_regions = list(regions)
                self._pending_drafts = drafts
                fallback_to_full_sync = True
            if not fallback_to_full_sync:
                self._apply_selection(self._pending_selection)
        finally:
            self.setUpdatesEnabled(True)
            self._block_signals = False

        if self._pending_regions is not None:
            self.flush_pending_regions()

    def update_regions(self, regions):
        """用新的区域列表填充UI,现在显示原文和可编辑的译文。"""
        drafts = self._pending_drafts.copy()
        drafts.update(self._collect_dirty_translations())
        self._pending_regions = list(regions)
        self._pending_drafts = drafts
        if not self.isVisible():
            self._block_signals = True
            try:
                self.clear()
            finally:
                self._block_signals = False
            return

        self.flush_pending_regions()

    def flush_pending_regions(self):
        """在「译文列表」标签页真正可见时按差量更新列表。

        按行位置复用现有行（只更新文本/数据），仅增删数量变化的行，
        避免整表 clear+重建销毁正在输入的 TextEdit（丢焦点/光标/吃 IME
        组合字）；持有焦点的译文框不覆盖文本。
        """
        if self._pending_regions is None:
            return

        regions = self._pending_regions
        drafts = self._pending_drafts
        self._pending_regions = None
        self._pending_drafts = {}
        self._block_signals = True
        self.setUpdatesEnabled(False)
        try:
            for i, region in enumerate(regions):
                draft = drafts.get(self._region_key(i))
                if i < self.count():
                    self._update_region_item(i, region, draft)
                else:
                    self._add_region_item(i, region, draft)
            while self.count() > len(regions):
                self._remove_region_row(self.count() - 1)
            self._apply_selection(self._pending_selection)
        finally:
            self.setUpdatesEnabled(True)
            self._block_signals = False

    def _remove_region_row(self, row: int) -> None:
        item = self.item(row)
        widget = self.itemWidget(item)
        if widget is not None:
            self.removeItemWidget(item)
            delete_widget(widget)
        self.takeItem(row)

    def _update_rows_from(self, start: int, regions, drafts: dict[str, str]) -> None:
        for index in range(max(0, start), min(self.count(), len(regions))):
            self._update_region_item(
                index,
                regions[index],
                drafts.get(self._region_key(index)),
            )

    def _region_key(self, index: int) -> str:
        region_id = self.model.get_region_id(index)
        return f"id:{region_id}" if region_id is not None else f"index:{index}"

    def _collect_dirty_translations(self) -> dict[str, str]:
        drafts = {}
        for row in range(self.count()):
            item = self.item(row)
            widget = self.itemWidget(item) if item is not None else None
            translated_edit = widget.findChild(TextEdit) if widget is not None else None
            if translated_edit is None:
                continue
            model_text = translated_edit.property("modelText")
            current_text = translated_edit.toPlainText()
            if current_text != str(model_text or ""):
                drafts[item.data(_REGION_KEY_ROLE)] = current_text
        return drafts

    def _add_region_item(
        self,
        index: int,
        region: dict,
        draft_text: str | None = None,
        *,
        insert: bool = False,
    ) -> None:
        item = QListWidgetItem()
        item_container = CardWidget()
        layout = QVBoxLayout(item_container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        drag_handle = _RegionDragHandle(item_container)
        set_hover_hint(drag_handle, self._t("Drag to reorder regions"))
        drag_handle.pressed.connect(lambda item=item: self._select_drag_item(item))
        drag_handle.drag_requested.connect(lambda item=item: self._start_row_drag(item))

        original_label = BodyLabel(f"{index + 1}: {region.get('text', '')}")
        original_label.setWordWrap(True)
        header_layout.addWidget(drag_handle, 0, Qt.AlignmentFlag.AlignTop)
        header_layout.addWidget(original_label, 1)

        model_text = region.get("translation", "")
        translated_edit = TextEdit()
        translated_edit.setPlainText(model_text if draft_text is None else draft_text)
        translated_edit.setProperty("modelText", model_text)
        translated_edit.setPlaceholderText(self._t("Translation"))
        translated_edit.setFixedHeight(60)

        layout.addLayout(header_layout)
        layout.addWidget(translated_edit)

        # 差量更新时直接取用，避免 findChild
        item_container.original_label = original_label
        item_container.translated_edit = translated_edit
        item_container.drag_handle = drag_handle

        if insert:
            self.insertItem(index, item)
        else:
            self.addItem(item)
        self.setItemWidget(item, item_container)
        item.setData(Qt.ItemDataRole.UserRole, index)
        item.setData(_REGION_KEY_ROLE, self._region_key(index))
        self._sync_row_size(item, item_container)

    def _update_region_item(self, index: int, region: dict, draft_text: str | None) -> None:
        """就地刷新已有行；正在编辑（持焦点）的译文框不覆盖文本。"""
        item = self.item(index)
        widget = self.itemWidget(item)
        if widget is None:
            return

        original_label = getattr(widget, "original_label", None)
        if original_label is None:
            original_label = widget.findChild(BodyLabel)
        if original_label is not None:
            new_text = f"{index + 1}: {region.get('text', '')}"
            if original_label.text() != new_text:
                original_label.setText(new_text)

        translated_edit = getattr(widget, "translated_edit", None)
        if translated_edit is None:
            translated_edit = widget.findChild(TextEdit)
        if translated_edit is not None:
            same_region = item.data(_REGION_KEY_ROLE) == self._region_key(index)
            model_text = region.get("translation", "")
            translated_edit.setProperty("modelText", model_text)
            target_text = model_text if draft_text is None else draft_text
            if (
                (not translated_edit.hasFocus() or not same_region)
                and translated_edit.toPlainText() != target_text
            ):
                translated_edit.setPlainText(target_text)

        item.setData(Qt.ItemDataRole.UserRole, index)
        item.setData(_REGION_KEY_ROLE, self._region_key(index))
        self._sync_row_size(item, widget)

    def _sync_row_size(self, item: QListWidgetItem, widget) -> None:
        """按当前视口宽度计算行高：原文 label 折行后高度会变化，
        不能用加入列表前的 sizeHint 定死（那时 label 还没有真实宽度）。"""
        width = max(50, self.viewport().width())
        layout = widget.layout()
        if layout is not None and layout.hasHeightForWidth():
            height = layout.heightForWidth(width)
        else:
            height = widget.sizeHint().height()
        item.setSizeHint(QSize(width, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for row in range(self.count()):
            item = self.item(row)
            widget = self.itemWidget(item)
            if widget is not None:
                self._sync_row_size(item, widget)

    def refresh_ui_texts(self) -> None:
        for row in range(self.count()):
            item = self.item(row)
            widget = self.itemWidget(item)
            if widget is None:
                continue
            translated_edit = getattr(widget, "translated_edit", None)
            if translated_edit is not None:
                translated_edit.setPlaceholderText(self._t("Translation"))
            drag_handle = getattr(widget, "drag_handle", None)
            if drag_handle is not None:
                set_hover_hint(drag_handle, self._t("Drag to reorder regions"))

    def _select_drag_item(self, item: QListWidgetItem) -> None:
        if self.row(item) < 0:
            return
        self.setCurrentItem(item)
        item.setSelected(True)

    def _start_row_drag(self, item: QListWidgetItem) -> None:
        source_row = self.row(item)
        if source_row < 0:
            return
        self._select_drag_item(item)
        self._drag_source_row = source_row
        try:
            self.startDrag(Qt.DropAction.MoveAction)
        finally:
            self._drag_source_row = None

    def dropEvent(self, event) -> None:
        source_row = self._drag_source_row
        if source_row is None or not (0 <= source_row < self.count()):
            event.ignore()
            return

        position = event.position().toPoint()
        target_index = self.indexAt(position)
        if target_index.isValid():
            insertion_row = target_index.row()
            if position.y() > self.visualRect(target_index).center().y():
                insertion_row += 1
        else:
            insertion_row = self.count()

        target_row = insertion_row - 1 if source_row < insertion_row else insertion_row
        target_row = max(0, min(target_row, self.count() - 1))
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if target_row != source_row:
            self.region_move_requested.emit(source_row, target_row)

    def get_all_translations(self):
        """获取列表中所有编辑后的译文"""
        translations = {}
        for i in range(self.count()):
            item = self.item(i)
            item_index = item.data(Qt.ItemDataRole.UserRole)
            widget = self.itemWidget(item)
            if widget:
                translated_edit = widget.findChild(TextEdit)
                if translated_edit:
                    translations[item_index] = translated_edit.toPlainText()
        return translations

    def find_and_replace_in_all_translations(self, find_text, replace_text):
        """在所有译文编辑框中执行查找和替换"""
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                translated_edit = widget.findChild(TextEdit)
                if translated_edit:
                    current_text = translated_edit.toPlainText()
                    new_text = current_text.replace(find_text, replace_text)
                    if current_text != new_text:
                        translated_edit.setPlainText(new_text)

    def update_selection(self, selected_indices):
        """根据外部变化（如画布点击）更新列表中的选中项"""
        self._pending_selection = list(selected_indices or [])
        if self._pending_regions is not None:
            return

        self._block_signals = True
        try:
            self._apply_selection(self._pending_selection)
        finally:
            self._block_signals = False

    def _apply_selection(self, selected_indices):
        self.clearSelection()
        if not selected_indices:
            return

        for i in range(self.count()):
            item = self.item(i)
            item_index = item.data(Qt.ItemDataRole.UserRole)
            if item_index in selected_indices:
                item.setSelected(True)

    def _on_item_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """当用户在列表中点击一个项目时发出信号"""
        if self._block_signals or not current:
            return

        selected_index = current.data(Qt.ItemDataRole.UserRole)
        self.region_selected.emit([selected_index])
