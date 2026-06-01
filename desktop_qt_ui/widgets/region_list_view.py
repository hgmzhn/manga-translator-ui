
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class RegionListView(QListWidget):
    """
    显示和管理当前图片中所有文本区域的列表。
    """
    region_selected = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block_signals = False
        self._pending_regions = None
        self._pending_selection = []
        self.currentItemChanged.connect(self._on_item_changed)

    def update_regions(self, regions):
        """用新的区域列表填充UI,现在显示原文和可编辑的译文。"""
        self._pending_regions = regions
        if not self.isVisible():
            self._block_signals = True
            try:
                self.clear()
            finally:
                self._block_signals = False
            return

        self.flush_pending_regions()

    def flush_pending_regions(self):
        """在“可编辑译文”标签页真正可见时再重建列表。"""
        if self._pending_regions is None:
            return

        regions = self._pending_regions
        self._pending_regions = None
        self._block_signals = True
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            for i, region in enumerate(regions):
                original_text = region.get('text', '')
                translated_text = region.get('translation', '')

                item_container = QWidget()
                layout = QVBoxLayout(item_container)
                layout.setContentsMargins(5, 5, 5, 5)
                layout.setSpacing(3)

                original_label = QLabel(f"<b>{i+1}:</b> {original_text}")
                original_label.setWordWrap(True)

                translated_edit = QTextEdit(translated_text)
                translated_edit.setPlaceholderText("译文")
                translated_edit.setFixedHeight(60)
                translated_edit.setObjectName("translated_edit")

                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)

                layout.addWidget(original_label)
                layout.addWidget(translated_edit)
                layout.addWidget(separator)

                item = QListWidgetItem(self)
                item.setSizeHint(item_container.sizeHint())
                self.addItem(item)
                self.setItemWidget(item, item_container)
                item.setData(Qt.ItemDataRole.UserRole, i)
            self._apply_selection(self._pending_selection)
        finally:
            self.setUpdatesEnabled(True)
            self._block_signals = False

    def get_all_translations(self):
        """获取列表中所有编辑后的译文"""
        translations = {}
        for i in range(self.count()):
            item = self.item(i)
            item_index = item.data(Qt.ItemDataRole.UserRole)
            widget = self.itemWidget(item)
            if widget:
                translated_edit = widget.findChild(QTextEdit, "translated_edit")
                if translated_edit:
                    translations[item_index] = translated_edit.toPlainText()
        return translations

    def find_and_replace_in_all_translations(self, find_text, replace_text):
        """在所有译文编辑框中执行查找和替换"""
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                translated_edit = widget.findChild(QTextEdit, "translated_edit")
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
