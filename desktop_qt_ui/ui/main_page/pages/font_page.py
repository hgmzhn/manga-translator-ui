from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    HeaderCardWidget,
    HorizontalSeparator,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    Slider,
    StrongBodyLabel,
    TitleLabel,
)


def create_font_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = CardWidget()
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(4)
    self.font_page_title_label = TitleLabel(self._t("Font Management"))
    self.font_page_subtitle_label = BodyLabel(
        self._t("Manage and preview fonts for text rendering")
    )
    self.font_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.font_page_title_label)
    header_layout.addWidget(self.font_page_subtitle_label)
    page_layout.addWidget(header_card)

    self.font_card = HeaderCardWidget(self._t("Font List"))
    font_card_layout = QVBoxLayout()
    font_card_layout.setContentsMargins(0, 0, 0, 0)
    font_card_layout.setSpacing(10)
    self.font_card.viewLayout.addLayout(font_card_layout)

    button_row = QWidget()
    button_row_layout = QHBoxLayout(button_row)
    button_row_layout.setContentsMargins(0, 0, 0, 0)
    button_row_layout.setSpacing(8)
    self.font_import_button = PushButton(self._t("Import"))
    self.font_delete_button = PushButton(self._t("Delete"))
    self.font_refresh_button = PushButton(self._t("Refresh"))
    self.font_open_dir_button = PushButton(self._t("Open Directory"))
    self.font_apply_button = PrimaryPushButton(self._t("Apply Selected Font"))
    button_row_layout.addWidget(self.font_import_button)
    button_row_layout.addWidget(self.font_delete_button)
    button_row_layout.addWidget(self.font_refresh_button)
    button_row_layout.addWidget(self.font_open_dir_button)
    button_row_layout.addWidget(self.font_apply_button)
    button_row_layout.addStretch()
    font_card_layout.addWidget(button_row)

    self.font_list_widget = ListWidget()
    font_card_layout.addWidget(self.font_list_widget)

    self.font_status_label = BodyLabel("")
    self.font_status_label.setWordWrap(True)
    font_card_layout.addWidget(self.font_status_label)

    page_layout.addWidget(self.font_card, 1)

    self.font_preview_card = HeaderCardWidget(self._t("Font Preview"))
    self.font_preview_card.setFixedHeight(320)
    preview_card_layout = QVBoxLayout()
    preview_card_layout.setContentsMargins(0, 0, 0, 0)
    preview_card_layout.setSpacing(8)
    self.font_preview_card.viewLayout.addLayout(preview_card_layout)

    header_row = QHBoxLayout()
    header_row.setSpacing(10)
    self.font_preview_name_label = StrongBodyLabel(self._t("Select a font to preview"))
    header_row.addWidget(self.font_preview_name_label, 1)

    self.font_preview_size_indicator = CaptionLabel("24pt")
    header_row.addWidget(self.font_preview_size_indicator)
    preview_card_layout.addLayout(header_row)

    toolbar_row = QHBoxLayout()
    toolbar_row.setSpacing(12)

    self.font_preview_input = LineEdit()
    self.font_preview_input.setPlaceholderText(self._t("Type custom text to preview..."))
    self.font_preview_input.setClearButtonEnabled(True)
    toolbar_row.addWidget(self.font_preview_input, 3)

    self.font_preview_slider = Slider(Qt.Orientation.Horizontal)
    self.font_preview_slider.setRange(12, 64)
    self.font_preview_slider.setValue(24)
    self.font_preview_slider.setToolTip(self._t("Adjust preview size"))
    toolbar_row.addWidget(self.font_preview_slider, 2)

    preview_card_layout.addLayout(toolbar_row)
    preview_card_layout.addWidget(HorizontalSeparator())

    self.font_preview_scroll = ScrollArea()
    self.font_preview_scroll.setWidgetResizable(True)

    scroll_content = QWidget(self.font_preview_scroll)
    self.scroll_content_layout = QVBoxLayout(scroll_content)
    self.scroll_content_layout.setContentsMargins(0, 4, 0, 4)
    self.scroll_content_layout.setSpacing(10)

    self.font_preview_labels = []
    for _ in range(3):
        lbl = BodyLabel()
        lbl.setWordWrap(False)
        lbl.setScaledContents(False)
        self.scroll_content_layout.addWidget(lbl)
        self.font_preview_labels.append(lbl)

    self.scroll_content_layout.addStretch()
    self.font_preview_scroll.setWidget(scroll_content)
    self.font_preview_scroll.enableTransparentBackground()
    preview_card_layout.addWidget(self.font_preview_scroll, 1)

    self._current_preview_font_path = None
    page_layout.addWidget(self.font_preview_card)

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
