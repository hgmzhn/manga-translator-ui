from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, PopUpAniStackedWidget, ScrollArea, SegmentedWidget, TitleLabel


def _create_scroll_page() -> tuple[ScrollArea, QWidget, QVBoxLayout]:
    scroll = ScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(ScrollArea.Shape.NoFrame)

    container = QWidget(scroll)
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(12)
    scroll.setWidget(container)
    scroll.enableTransparentBackground()
    return scroll, container, container_layout


def create_env_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = CardWidget(page)
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

    env_tabs_panel = QWidget(page)
    env_tabs_layout = QVBoxLayout(env_tabs_panel)
    env_tabs_layout.setContentsMargins(0, 0, 0, 0)
    env_tabs_layout.setSpacing(8)

    self.env_tab_widget = SegmentedWidget(env_tabs_panel)
    self.env_stack = PopUpAniStackedWidget(env_tabs_panel)
    self.env_tab_routes = {}
    self.env_tab_title_keys = {}
    self.env_tab_route_widgets = {}
    env_tabs_layout.addWidget(self.env_tab_widget)
    env_tabs_layout.addWidget(self.env_stack, 1)

    def switch_env_tab(route_key: str):
        widget = self.env_tab_route_widgets.get(route_key)
        if widget is not None:
            self.env_stack.setCurrentWidget(widget)

    self.env_tab_widget.currentItemChanged.connect(switch_env_tab)

    (
        self.env_translation_page,
        self.env_group_container,
        self.env_group_container_layout,
    ) = _create_scroll_page()

    self.env_ocr_page, self.ocr_container, self.ocr_container_layout = _create_scroll_page()

    self.env_color_page, self.color_container, self.color_container_layout = _create_scroll_page()

    self.env_render_page, self.render_container, self.render_container_layout = _create_scroll_page()

    def add_env_tab(route_key: str, title_key: str, widget: ScrollArea):
        self.env_tab_routes[title_key] = route_key
        self.env_tab_title_keys[route_key] = title_key
        self.env_tab_route_widgets[route_key] = widget
        self.env_stack.addWidget(widget)
        self.env_tab_widget.addItem(route_key, self._t(title_key))

    add_env_tab("env_translation", "Translation", self.env_translation_page)
    add_env_tab("env_ocr", "OCR", self.env_ocr_page)
    add_env_tab("env_colorization", "Colorization", self.env_color_page)
    add_env_tab("env_render", "Render", self.env_render_page)

    self.env_tab_widget.setCurrentItem("env_translation")
    self.env_stack.setCurrentWidget(self.env_translation_page)

    page_layout.addWidget(env_tabs_panel, 1)
    return page
