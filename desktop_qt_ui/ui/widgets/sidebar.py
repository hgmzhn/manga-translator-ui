from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QStackedWidget, QSizePolicy, QWidget
from qfluentwidgets import Pivot, ScrollArea, VBoxLayout as FluentVBoxLayout


class PivotStack(QWidget):
    """Pivot navigation with pages that always fill the stack area."""

    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None, fixed_width: int | None = None):
        super().__init__(parent)
        self._fixed_width = fixed_width
        self._routes: list[str] = []
        self._pages: list[QWidget] = []
        self._content_widgets: list[QWidget] = []

        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)

        if fixed_width is not None:
            self.setFixedWidth(fixed_width)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.pivot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = FluentVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.pivot)
        layout.addWidget(self.stack, 1, Qt.AlignmentFlag(0))

    def addTab(self, widget: QWidget, text: str):
        index = len(self._pages)
        route = f"tab_{index}"

        page = QWidget(self.stack)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        page_layout = FluentVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        page_layout.addWidget(widget, 1, Qt.AlignmentFlag(0))

        self._routes.append(route)
        self._pages.append(page)
        self._content_widgets.append(widget)
        self.stack.addWidget(page)
        self.pivot.addItem(route, text, lambda checked=False, i=index: self.setCurrentIndex(i))

        if index == 0:
            self.setCurrentIndex(0)
        else:
            self.sync_layout()

    def setCurrentIndex(self, index: int):
        if not 0 <= index < len(self._pages):
            return

        previous = self.stack.currentIndex()
        self.stack.setCurrentIndex(index)
        self.pivot.setCurrentItem(self._routes[index])
        self.sync_layout()

        if previous != index:
            self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def setTabText(self, index: int, text: str):
        if 0 <= index < len(self._routes):
            self.pivot.setItemText(self._routes[index], text)
            self.sync_layout()

    def sync_layout(self):
        self.layout().activate()
        self.stack.updateGeometry()

        index = self.stack.currentIndex()
        if 0 <= index < len(self._pages):
            page = self._pages[index]
            page.layout().activate()
            content = self._content_widgets[index]
            sync_content = getattr(content, "sync_sidebar_layout", None)
            if callable(sync_content):
                sync_content()

        adjust_indicator = getattr(self.pivot, "_adjustIndicatorPos", None)
        if callable(adjust_indicator):
            adjust_indicator()
        self.pivot.update()

    def sizeHint(self):
        hint = super().sizeHint()
        if self._fixed_width is not None:
            hint.setWidth(self._fixed_width)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        if self._fixed_width is not None:
            hint.setWidth(self._fixed_width)
        return hint

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_layout()


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
