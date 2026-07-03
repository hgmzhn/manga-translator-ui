from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import ScrollArea


class FluentScrollArea(ScrollArea):
    """Fluent scroll area that delegates sizing to Qt's layout system."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(ScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def setWidget(self, widget: QWidget):
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        super().setWidget(widget)
        self.enableTransparentBackground()

    def sync_layout(self):
        widget = self.widget()
        if widget is None:
            return

        layout = widget.layout()
        if layout is not None:
            layout.activate()
        widget.updateGeometry()
        self.viewport().update()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_layout()
