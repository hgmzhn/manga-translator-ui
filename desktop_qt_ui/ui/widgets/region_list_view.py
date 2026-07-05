
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QListWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import BodyLabel, CardWidget, ListWidget, TextEdit


_REGION_KEY_ROLE = Qt.ItemDataRole.UserRole.value + 1


class RegionListView(ListWidget):
    """
    显示和管理当前图片中所有文本区域的列表。
    """
    region_selected = pyqtSignal(list)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self._block_signals = False
        self._pending_regions = None
        self._pending_drafts = {}
        self._pending_selection = []
        self.currentItemChanged.connect(self._on_item_changed)

    def on_regions_changed(self, _change):
        self.update_regions(self.model.get_regions())

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
        """在「可编辑译文」标签页真正可见时再重建列表。"""
        if self._pending_regions is None:
            return

        regions = self._pending_regions
        drafts = self._pending_drafts
        self._pending_regions = None
        self._pending_drafts = {}
        self._block_signals = True
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            for i, region in enumerate(regions):
                self._add_region_item(i, region, drafts.get(self._region_key(i)))
            self._apply_selection(self._pending_selection)
        finally:
            self.setUpdatesEnabled(True)
            self._block_signals = False

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

    def _add_region_item(self, index: int, region: dict, draft_text: str | None = None) -> None:
        item_container = CardWidget()
        layout = QVBoxLayout(item_container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        original_label = BodyLabel(f"{index + 1}: {region.get('text', '')}")
        original_label.setWordWrap(True)

        model_text = region.get("translation", "")
        translated_edit = TextEdit()
        translated_edit.setPlainText(model_text if draft_text is None else draft_text)
        translated_edit.setProperty("modelText", model_text)
        translated_edit.setPlaceholderText("译文")
        translated_edit.setFixedHeight(60)

        layout.addWidget(original_label)
        layout.addWidget(translated_edit)

        item = QListWidgetItem()
        item.setSizeHint(item_container.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, item_container)
        item.setData(Qt.ItemDataRole.UserRole, index)
        item.setData(_REGION_KEY_ROLE, self._region_key(index))

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
