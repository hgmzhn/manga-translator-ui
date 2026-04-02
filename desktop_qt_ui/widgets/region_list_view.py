
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
        self.currentItemChanged.connect(self._on_item_changed)

    def update_regions(self, regions):
        """用新的区域列表填充UI,现在显示原文和可编辑的译文。"""
        self._block_signals = True
        self.clear()
        for i, region in enumerate(regions):
            original_text = region.get('text', '')
            trans_raw = region.get('translation', '')
            if isinstance(trans_raw, dict):
                lang_key = region.get('target_lang', '')
                trans_raw = trans_raw.get(lang_key) or next(iter(trans_raw.values()), '')
            translated_text = trans_raw

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
        self._block_signals = True
        self.clearSelection()
        if not selected_indices:
            self._block_signals = False
            return
        
        for i in range(self.count()):
            item = self.item(i)
            item_index = item.data(Qt.ItemDataRole.UserRole)
            if item_index in selected_indices:
                item.setSelected(True)
        self._block_signals = False

    def _on_item_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """当用户在列表中点击一个项目时发出信号"""
        if self._block_signals or not current:
            return
        
        selected_index = current.data(Qt.ItemDataRole.UserRole)
        self.region_selected.emit([selected_index])
