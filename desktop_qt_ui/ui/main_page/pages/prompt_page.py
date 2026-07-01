from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ui.secondary_pages.prompt_preview import PromptPreviewPanel

def create_prompt_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_prompts")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    # --- Header Card ---
    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(4)
    self.prompt_page_title_label = QLabel(self._t("Prompt Management"))
    self.prompt_page_title_label.setObjectName("page_title")
    self.prompt_page_subtitle_label = QLabel(
        self._t("Manage and apply prompt files for translation")
    )
    self.prompt_page_subtitle_label.setObjectName("page_subtitle")
    self.prompt_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.prompt_page_title_label)
    header_layout.addWidget(self.prompt_page_subtitle_label)
    page_layout.addWidget(header_card)

    # --- 左右 Splitter ---
    prompt_splitter = QSplitter(Qt.Orientation.Horizontal)
    prompt_splitter.setObjectName("settings_body_splitter")

    # ===== 左侧: Prompt 列表 =====
    left_widget = QWidget()
    left_layout = QVBoxLayout(left_widget)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(0)

    self.prompt_card = QGroupBox(self._t("Prompt List"))
    self.prompt_card.setObjectName("section_card")
    prompt_card_layout = QVBoxLayout(self.prompt_card)
    prompt_card_layout.setContentsMargins(12, 14, 12, 12)
    prompt_card_layout.setSpacing(10)

    button_row = QWidget()
    button_row.setObjectName("inline_toolbar")
    button_row_layout = QHBoxLayout(button_row)
    button_row_layout.setContentsMargins(0, 0, 0, 0)
    button_row_layout.setSpacing(8)
    self.prompt_new_button = QPushButton(self._t("New"))
    self.prompt_copy_button = QPushButton(self._t("Copy"))
    self.prompt_rename_button = QPushButton(self._t("Rename"))
    self.prompt_delete_button = QPushButton(self._t("Delete"))
    self.prompt_refresh_button = QPushButton(self._t("Refresh"))
    self.prompt_open_dir_button = QPushButton(self._t("Open Directory"))
    self.prompt_apply_button = QPushButton(self._t("Apply Selected Prompt"))
    self.prompt_new_button.setProperty("chipButton", True)
    self.prompt_copy_button.setProperty("chipButton", True)
    self.prompt_rename_button.setProperty("chipButton", True)
    self.prompt_delete_button.setProperty("chipButton", True)
    self.prompt_delete_button.setProperty("variant", "danger")
    self.prompt_refresh_button.setProperty("chipButton", True)
    self.prompt_open_dir_button.setProperty("chipButton", True)
    self.prompt_apply_button.setProperty("chipButton", True)
    button_row_layout.addWidget(self.prompt_new_button)
    button_row_layout.addWidget(self.prompt_copy_button)
    button_row_layout.addWidget(self.prompt_rename_button)
    button_row_layout.addWidget(self.prompt_delete_button)
    button_row_layout.addWidget(self.prompt_refresh_button)
    button_row_layout.addWidget(self.prompt_open_dir_button)
    button_row_layout.addWidget(self.prompt_apply_button)
    button_row_layout.addStretch()
    prompt_card_layout.addWidget(button_row)

    self.prompt_list_widget = QListWidget()
    self.prompt_list_widget.setObjectName("asset_list")
    prompt_card_layout.addWidget(self.prompt_list_widget)

    self.prompt_status_label = QLabel("")
    self.prompt_status_label.setObjectName("page_subtitle")
    self.prompt_status_label.setWordWrap(True)
    prompt_card_layout.addWidget(self.prompt_status_label)
    left_layout.addWidget(self.prompt_card, 1)

    prompt_splitter.addWidget(left_widget)

    # ===== 右侧: 预览面板 =====
    self.prompt_preview_panel = PromptPreviewPanel(t_func=self._t, parent=self)
    prompt_splitter.addWidget(self.prompt_preview_panel)

    prompt_splitter.setStretchFactor(0, 2)
    prompt_splitter.setStretchFactor(1, 3)
    prompt_splitter.setSizes([320, 580])
    prompt_splitter.setCollapsible(0, False)
    prompt_splitter.setCollapsible(1, False)

    page_layout.addWidget(prompt_splitter, 1)

    # --- 信号连接 ---
    self.prompt_new_button.clicked.connect(self._create_new_prompt)
    self.prompt_copy_button.clicked.connect(self._copy_selected_prompt)
    self.prompt_rename_button.clicked.connect(self._rename_selected_prompt)
    self.prompt_delete_button.clicked.connect(self._delete_selected_prompt)
    self.prompt_refresh_button.clicked.connect(self._refresh_prompt_manager)
    self.prompt_open_dir_button.clicked.connect(self.controller.open_dict_directory)
    self.prompt_apply_button.clicked.connect(self._apply_selected_prompt)
    self.prompt_list_widget.itemDoubleClicked.connect(lambda _: self._apply_selected_prompt())
    self.prompt_list_widget.currentItemChanged.connect(self._on_prompt_selection_changed)
    self.prompt_preview_panel.edit_requested.connect(self._open_prompt_editor)
    return page


