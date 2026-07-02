from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, ScrollArea, SegmentedWidget, TitleLabel


class _SegmentedTabWidget(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, route_prefix: str, parent=None):
        super().__init__(parent)
        self._routes: list[str] = []
        self._route_prefix = route_prefix

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.segmented_widget = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.segmented_widget)
        layout.addWidget(self.stack, 1)

        self.segmented_widget.currentItemChanged.connect(self._sync_current_route)

    def addTab(self, widget: QWidget, text: str) -> int:
        index = self.stack.addWidget(widget)
        route_key = f"{self._route_prefix}_{index}"
        self._routes.append(route_key)
        self.segmented_widget.addItem(route_key, text)
        if index == 0:
            self.segmented_widget.setCurrentItem(route_key)
            self.stack.setCurrentIndex(index)
        return index

    def count(self) -> int:
        return self.stack.count()

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def setCurrentIndex(self, index: int):
        if 0 <= index < len(self._routes):
            self.segmented_widget.setCurrentItem(self._routes[index])

    def setTabText(self, index: int, text: str):
        if 0 <= index < len(self._routes):
            self.segmented_widget.setItemText(self._routes[index], text)

    def widget(self, index: int) -> QWidget | None:
        return self.stack.widget(index)

    def _sync_current_route(self, route_key: str):
        if route_key not in self._routes:
            return
        index = self._routes.index(route_key)
        self.stack.setCurrentIndex(index)
        self.currentChanged.emit(index)


def _create_scroll_page(container: QWidget) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)

    scroll = ScrollArea()
    scroll.setWidgetResizable(True)
    page_layout.addWidget(scroll)

    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(16, 14, 16, 14)
    container_layout.setSpacing(12)
    scroll.setWidget(container)
    return page, container_layout


def create_env_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = CardWidget()
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(8)

    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    self.env_page_title_label = TitleLabel(self._t("API Management"))
    self.env_page_subtitle_label = BodyLabel(
        self._t("Manage API keys and environment variables for each translator")
    )
    self.env_page_subtitle_label.setWordWrap(True)
    title_col.addWidget(self.env_page_title_label)
    title_col.addWidget(self.env_page_subtitle_label)
    header_layout.addLayout(title_col)

    self.env_preset_layout = QHBoxLayout()
    self.env_preset_layout.setSpacing(8)
    header_layout.addLayout(self.env_preset_layout)

    page_layout.addWidget(header_card)

    self.env_tab_widget = _SegmentedTabWidget("env_tab")

    self.env_group_container = QWidget()
    self.env_translation_page, self.env_group_container_layout = _create_scroll_page(
        self.env_group_container
    )
    self.env_translation_layout = self.env_translation_page.layout()

    self.ocr_container = QWidget()
    self.env_ocr_page, self.ocr_container_layout = _create_scroll_page(self.ocr_container)
    self.env_ocr_layout = self.env_ocr_page.layout()

    self.color_container = QWidget()
    self.env_color_page, self.color_container_layout = _create_scroll_page(self.color_container)
    self.env_color_layout = self.env_color_page.layout()

    self.render_container = QWidget()
    self.env_render_page, self.render_container_layout = _create_scroll_page(self.render_container)
    self.env_render_layout = self.env_render_page.layout()

    self.env_tab_widget.addTab(self.env_translation_page, self._t("Translation"))
    self.env_tab_widget.addTab(self.env_ocr_page, self._t("OCR"))
    self.env_tab_widget.addTab(self.env_color_page, self._t("Colorization"))
    self.env_tab_widget.addTab(self.env_render_page, self._t("Render"))

    page_layout.addWidget(self.env_tab_widget, 1)
    return page
