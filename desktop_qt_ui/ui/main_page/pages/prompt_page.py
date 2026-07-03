from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    HeaderCardWidget,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
)

from ui.secondary_pages.prompt_preview import PromptPreviewPanel


def create_prompt_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    self.prompt_page_title_label = TitleLabel(self._t("Prompt Management"))
    self.prompt_page_subtitle_label = BodyLabel(
        self._t("Manage and apply prompt files for translation")
    )
    self.prompt_page_subtitle_label.setWordWrap(True)
    page_layout.addWidget(self.prompt_page_title_label)
    page_layout.addWidget(self.prompt_page_subtitle_label)

    prompt_splitter = QSplitter(Qt.Orientation.Horizontal)

    self.prompt_card = HeaderCardWidget(self._t("Prompt List"))
    prompt_card_layout = QVBoxLayout()
    prompt_card_layout.setContentsMargins(0, 0, 0, 0)
    prompt_card_layout.setSpacing(10)
    self.prompt_card.viewLayout.addLayout(prompt_card_layout)

    button_row_layout = QHBoxLayout()
    button_row_layout.setSpacing(8)
    self.prompt_new_button = PushButton(self._t("New"))
    self.prompt_copy_button = PushButton(self._t("Copy"))
    self.prompt_rename_button = PushButton(self._t("Rename"))
    self.prompt_delete_button = PushButton(self._t("Delete"))
    self.prompt_refresh_button = PushButton(self._t("Refresh"))
    self.prompt_open_dir_button = PushButton(self._t("Open Directory"))
    self.prompt_apply_button = PrimaryPushButton(self._t("Apply Selected Prompt"))
    self.prompt_new_button.setIcon(FIF.ADD)
    self.prompt_copy_button.setIcon(FIF.COPY)
    self.prompt_rename_button.setIcon(FIF.EDIT)
    self.prompt_delete_button.setIcon(FIF.DELETE)
    self.prompt_refresh_button.setIcon(FIF.SYNC)
    self.prompt_open_dir_button.setIcon(FIF.FOLDER)
    self.prompt_apply_button.setIcon(FIF.ACCEPT)
    button_row_layout.addWidget(self.prompt_new_button)
    button_row_layout.addWidget(self.prompt_copy_button)
    button_row_layout.addWidget(self.prompt_rename_button)
    button_row_layout.addWidget(self.prompt_delete_button)
    button_row_layout.addWidget(self.prompt_refresh_button)
    button_row_layout.addWidget(self.prompt_open_dir_button)
    button_row_layout.addWidget(self.prompt_apply_button)
    button_row_layout.addStretch()
    prompt_card_layout.addLayout(button_row_layout)

    self.prompt_list_widget = ListWidget()
    prompt_card_layout.addWidget(self.prompt_list_widget)

    self.prompt_status_label = BodyLabel("")
    self.prompt_status_label.setWordWrap(True)
    prompt_card_layout.addWidget(self.prompt_status_label)

    prompt_splitter.addWidget(self.prompt_card)

    self.prompt_preview_panel = PromptPreviewPanel(t_func=self._t, parent=self)
    prompt_splitter.addWidget(self.prompt_preview_panel)

    prompt_splitter.setStretchFactor(0, 2)
    prompt_splitter.setStretchFactor(1, 3)
    prompt_splitter.setSizes([320, 580])
    prompt_splitter.setCollapsible(0, False)
    prompt_splitter.setCollapsible(1, False)

    page_layout.addWidget(prompt_splitter, 1)

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
