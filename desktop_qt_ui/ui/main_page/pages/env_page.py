from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

def create_env_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_env")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    # --- Header Card ---
    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(8)

    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    self.env_page_title_label = QLabel(self._t("API Management"))
    self.env_page_title_label.setObjectName("page_title")
    self.env_page_subtitle_label = QLabel(
        self._t("Manage API keys and environment variables for each translator")
    )
    self.env_page_subtitle_label.setObjectName("page_subtitle")
    self.env_page_subtitle_label.setWordWrap(True)
    title_col.addWidget(self.env_page_title_label)
    title_col.addWidget(self.env_page_subtitle_label)
    header_layout.addLayout(title_col)

    self.env_preset_layout = QHBoxLayout()
    self.env_preset_layout.setSpacing(8)
    header_layout.addLayout(self.env_preset_layout)

    page_layout.addWidget(header_card)

    # --- Native QTabWidget Setup ---
    self.env_tab_widget = QTabWidget()
    self.env_tab_widget.setObjectName("settings_tab_widget")
    
    # 1. Translation Tab Content
    self.env_translation_page = QWidget()
    self.env_translation_layout = QVBoxLayout(self.env_translation_page)
    self.env_translation_layout.setContentsMargins(0, 0, 0, 0)
    
    env_scroll = QScrollArea()
    env_scroll.setWidgetResizable(True)
    env_scroll.setObjectName("settings_scroll_area")
    
    self.env_group_container = QWidget()
    self.env_group_container.setObjectName("settings_scroll_content")
    self.env_group_container_layout = QVBoxLayout(self.env_group_container)
    self.env_group_container_layout.setContentsMargins(0, 0, 0, 0)
    self.env_group_container_layout.setSpacing(12)
    env_scroll.setWidget(self.env_group_container)
    self.env_translation_layout.addWidget(env_scroll)
    
    # 2. OCR Tab Content
    self.env_ocr_page = QWidget()
    self.env_ocr_layout = QVBoxLayout(self.env_ocr_page)
    self.env_ocr_layout.setContentsMargins(0, 0, 0, 0)
    
    ocr_scroll = QScrollArea()
    ocr_scroll.setWidgetResizable(True)
    ocr_scroll.setObjectName("settings_scroll_area")
    self.ocr_container = QWidget()
    self.ocr_container.setObjectName("settings_scroll_content")
    self.ocr_container_layout = QVBoxLayout(self.ocr_container)
    self.ocr_container_layout.setContentsMargins(0, 0, 0, 0)
    self.ocr_container_layout.setSpacing(12)
    ocr_scroll.setWidget(self.ocr_container)
    self.env_ocr_layout.addWidget(ocr_scroll)
    
    # 3. Colorization Tab Content
    self.env_color_page = QWidget()
    self.env_color_layout = QVBoxLayout(self.env_color_page)
    self.env_color_layout.setContentsMargins(0, 0, 0, 0)
    
    color_scroll = QScrollArea()
    color_scroll.setWidgetResizable(True)
    color_scroll.setObjectName("settings_scroll_area")
    self.color_container = QWidget()
    self.color_container.setObjectName("settings_scroll_content")
    self.color_container_layout = QVBoxLayout(self.color_container)
    self.color_container_layout.setContentsMargins(0, 0, 0, 0)
    self.color_container_layout.setSpacing(12)
    color_scroll.setWidget(self.color_container)
    self.env_color_layout.addWidget(color_scroll)
    
    # 4. Render Tab Content
    self.env_render_page = QWidget()
    self.env_render_layout = QVBoxLayout(self.env_render_page)
    self.env_render_layout.setContentsMargins(0, 0, 0, 0)
    
    render_scroll = QScrollArea()
    render_scroll.setWidgetResizable(True)
    render_scroll.setObjectName("settings_scroll_area")
    self.render_container = QWidget()
    self.render_container.setObjectName("settings_scroll_content")
    self.render_container_layout = QVBoxLayout(self.render_container)
    self.render_container_layout.setContentsMargins(0, 0, 0, 0)
    self.render_container_layout.setSpacing(12)
    render_scroll.setWidget(self.render_container)
    self.env_render_layout.addWidget(render_scroll)
    
    self.env_tab_widget.addTab(self.env_translation_page, self._t("Translation"))
    self.env_tab_widget.addTab(self.env_ocr_page, self._t("OCR"))
    self.env_tab_widget.addTab(self.env_color_page, self._t("Colorization"))
    self.env_tab_widget.addTab(self.env_render_page, self._t("Render"))
    
    page_layout.addWidget(self.env_tab_widget, 1)
    return page


