"""Clipboard image input and thumbnail attachments for the chat UI."""
from __future__ import annotations

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PlainTextEdit


class ImagePasteTextEdit(PlainTextEdit):
    """Plain text editor that turns pasted clipboard images into PNG attachments."""

    image_pasted = pyqtSignal(bytes, int, int)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if not source.hasImage():
            super().insertFromMimeData(source)
            return

        image = QImage(source.imageData())
        if image.isNull():
            return
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            return
        try:
            if not image.save(buffer, "PNG"):
                return
        finally:
            buffer.close()
        self.image_pasted.emit(bytes(data), image.width(), image.height())


class ImageAttachmentStrip(QWidget):
    """Horizontally scrollable thumbnails; it stores no domain objects."""

    attachment_removed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._next_attachment_id = 0
        self._items = {}
        self._remove_tooltip = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget(self._scroll_area)
        self._content_layout = QHBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scroll_area.setWidget(self._content)
        layout.addWidget(self._scroll_area)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(118)
        self.hide()

    def set_remove_tooltip(self, text: str):
        self._remove_tooltip = text
        for item in self._items.values():
            item.findChild(QToolButton).setToolTip(text)

    def add_png(self, data: bytes, width: int, height: int) -> int:
        attachment_id = self._next_attachment_id
        self._next_attachment_id += 1
        item = self._create_item(attachment_id, data, width, height)
        self._items[attachment_id] = item
        self._content_layout.addWidget(item)
        self._content.adjustSize()
        self.show()
        return attachment_id

    def remove(self, attachment_id: int):
        item = self._items.pop(attachment_id, None)
        if item is None:
            return
        self._content_layout.removeWidget(item)
        item.deleteLater()
        self._content.adjustSize()
        if not self._items:
            self.hide()

    def clear(self):
        for attachment_id in tuple(self._items):
            self.remove(attachment_id)

    def _create_item(self, attachment_id: int, data: bytes, width: int, height: int) -> QWidget:
        item = QWidget(self._content)
        item.setFixedWidth(132)
        item_layout = QVBoxLayout(item)
        item_layout.setContentsMargins(4, 4, 4, 4)
        item_layout.setSpacing(2)

        thumbnail = QLabel(item)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setFixedSize(124, 84)
        pixmap = QPixmap()
        pixmap.loadFromData(data, "PNG")
        if not pixmap.isNull():
            thumbnail.setPixmap(pixmap.scaled(
                QSize(124, 84),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        item_layout.addWidget(thumbnail)

        footer = QHBoxLayout()
        size_label = QLabel(f"{width} × {height}", item)
        footer.addWidget(size_label, 1)
        remove_button = QToolButton(item)
        remove_button.setText("×")
        remove_button.setToolTip(self._remove_tooltip)
        remove_button.clicked.connect(lambda: self.attachment_removed.emit(attachment_id))
        footer.addWidget(remove_button)
        item_layout.addLayout(footer)
        return item
