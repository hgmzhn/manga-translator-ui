from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui.theme import get_current_theme_colors

def create_font_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_fonts")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    # --- Header Card (与翻译/设置页面一致) ---
    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(4)
    self.font_page_title_label = QLabel(self._t("Font Management"))
    self.font_page_title_label.setObjectName("page_title")
    self.font_page_subtitle_label = QLabel(
        self._t("Manage and preview fonts for text rendering")
    )
    self.font_page_subtitle_label.setObjectName("page_subtitle")
    self.font_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.font_page_title_label)
    header_layout.addWidget(self.font_page_subtitle_label)
    page_layout.addWidget(header_card)

    # --- Font List Card ---
    self.font_card = QGroupBox(self._t("Font List"))
    self.font_card.setObjectName("section_card")
    font_card_layout = QVBoxLayout(self.font_card)
    font_card_layout.setContentsMargins(12, 14, 12, 12)
    font_card_layout.setSpacing(10)

    button_row = QWidget()
    button_row.setObjectName("inline_toolbar")
    button_row_layout = QHBoxLayout(button_row)
    button_row_layout.setContentsMargins(0, 0, 0, 0)
    button_row_layout.setSpacing(8)
    self.font_import_button = QPushButton(self._t("Import"))
    self.font_delete_button = QPushButton(self._t("Delete"))
    self.font_refresh_button = QPushButton(self._t("Refresh"))
    self.font_open_dir_button = QPushButton(self._t("Open Directory"))
    self.font_apply_button = QPushButton(self._t("Apply Selected Font"))
    self.font_import_button.setProperty("chipButton", True)
    self.font_delete_button.setProperty("chipButton", True)
    self.font_delete_button.setProperty("variant", "danger")
    self.font_refresh_button.setProperty("chipButton", True)
    self.font_open_dir_button.setProperty("chipButton", True)
    self.font_apply_button.setProperty("chipButton", True)
    button_row_layout.addWidget(self.font_import_button)
    button_row_layout.addWidget(self.font_delete_button)
    button_row_layout.addWidget(self.font_refresh_button)
    button_row_layout.addWidget(self.font_open_dir_button)
    button_row_layout.addWidget(self.font_apply_button)
    button_row_layout.addStretch()
    font_card_layout.addWidget(button_row)

    self.font_list_widget = QListWidget()
    self.font_list_widget.setObjectName("asset_list")
    font_card_layout.addWidget(self.font_list_widget)

    self.font_status_label = QLabel("")
    self.font_status_label.setObjectName("page_subtitle")
    self.font_status_label.setWordWrap(True)
    font_card_layout.addWidget(self.font_status_label)

    page_layout.addWidget(self.font_card, 1)

    # --- Font Preview Card ---
    self.font_preview_card = QGroupBox(self._t("Font Preview"))
    self.font_preview_card.setObjectName("section_card")
    self.font_preview_card.setFixedHeight(320)
    preview_card_layout = QVBoxLayout(self.font_preview_card)
    preview_card_layout.setContentsMargins(16, 14, 16, 14)
    preview_card_layout.setSpacing(8)

    # 1. Header Row (Font Filename + Font Size Indicator)
    header_row = QHBoxLayout()
    header_row.setSpacing(10)
    self.font_preview_name_label = QLabel(self._t("Select a font to preview"))
    self.font_preview_name_label.setObjectName("font_preview_name")
    header_row.addWidget(self.font_preview_name_label, 1)

    self.font_preview_size_indicator = QLabel("24pt")
    self.font_preview_size_indicator.setStyleSheet(
        f"color: {get_current_theme_colors()['text_muted']}; font-size: 11px; font-weight: 600; font-family: monospace;"
    )
    header_row.addWidget(self.font_preview_size_indicator)
    preview_card_layout.addLayout(header_row)

    # 2. Control Toolbar (Custom Text Input + Size Slider)
    toolbar_row = QHBoxLayout()
    toolbar_row.setSpacing(12)

    self.font_preview_input = QLineEdit()
    self.font_preview_input.setPlaceholderText(self._t("Type custom text to preview..."))
    self.font_preview_input.setClearButtonEnabled(True)
    toolbar_row.addWidget(self.font_preview_input, 3)

    self.font_preview_slider = QSlider(Qt.Orientation.Horizontal)
    self.font_preview_slider.setRange(12, 64)
    self.font_preview_slider.setValue(24)
    self.font_preview_slider.setToolTip(self._t("Adjust preview size"))
    toolbar_row.addWidget(self.font_preview_slider, 2)

    preview_card_layout.addLayout(toolbar_row)

    preview_divider = QFrame()
    preview_divider.setFrameShape(QFrame.Shape.HLine)
    preview_divider.setObjectName("settings_desc_divider")
    preview_card_layout.addWidget(preview_divider)

    # 3. Scrollable Specimen Area
    self.font_preview_scroll = QScrollArea()
    self.font_preview_scroll.setWidgetResizable(True)
    self.font_preview_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    
    scroll_content = QWidget()
    scroll_content.setStyleSheet("background: transparent;")
    self.scroll_content_layout = QVBoxLayout(scroll_content)
    self.scroll_content_layout.setContentsMargins(0, 4, 0, 4)
    self.scroll_content_layout.setSpacing(10)

    self.font_preview_labels = []
    # Create 3 specimen labels representing different sizes/contents
    for i in range(3):
        lbl = QLabel()
        lbl.setObjectName("font_preview_text")
        lbl.setWordWrap(False)
        lbl.setScaledContents(False)
        self.scroll_content_layout.addWidget(lbl)
        self.font_preview_labels.append(lbl)

    self.scroll_content_layout.addStretch()
    self.font_preview_scroll.setWidget(scroll_content)
    preview_card_layout.addWidget(self.font_preview_scroll, 1)

    self._current_preview_font_path = None
    page_layout.addWidget(self.font_preview_card)

    # --- Signals ---
    self.font_import_button.clicked.connect(self._import_fonts)
    self.font_delete_button.clicked.connect(self._delete_selected_font)
    self.font_refresh_button.clicked.connect(self._refresh_font_manager)
    self.font_open_dir_button.clicked.connect(self.controller.open_font_directory)
    self.font_apply_button.clicked.connect(self._apply_selected_font)
    self.font_list_widget.itemDoubleClicked.connect(lambda _: self._apply_selected_font())
    self.font_list_widget.currentItemChanged.connect(self._on_font_selection_changed)
    self.font_preview_input.textChanged.connect(self._update_font_preview)
    self.font_preview_slider.valueChanged.connect(self._update_font_preview)
    return page


