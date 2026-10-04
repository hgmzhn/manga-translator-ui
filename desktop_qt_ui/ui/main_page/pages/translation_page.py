from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    FluentIcon as FIF,
)

from ui.widgets.file_workspace import FileWorkspace


def create_translation_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(12, 10, 12, 8)
    page_layout.setSpacing(8)

    page_header = QWidget()
    page_header_layout = QHBoxLayout(page_header)
    page_header_layout.setContentsMargins(2, 0, 2, 0)
    page_header_layout.setSpacing(10)
    self.translation_page_title = BodyLabel(self._t("Normal Translation"))
    self.translation_page_title.setStyleSheet("font-size: 16px; font-weight: 600;")
    self.translation_page_subtitle = CaptionLabel(
        self._t("Tip: Standard translation pipeline with detection, OCR, translation and rendering")
    )
    self.translation_page_subtitle.setWordWrap(False)
    self.translation_page_subtitle.setToolTip(self.translation_page_subtitle.text())
    page_header_layout.addWidget(self.translation_page_title)
    page_header_layout.addWidget(self.translation_page_subtitle, 1)
    page_layout.addWidget(page_header)

    input_card = CardWidget()
    input_layout = QVBoxLayout(input_card)
    input_layout.setContentsMargins(10, 8, 10, 8)
    input_layout.setSpacing(6)

    file_toolbar = QHBoxLayout()
    file_toolbar.setContentsMargins(0, 0, 0, 0)
    file_toolbar.setSpacing(6)
    self.add_files_button = PushButton(self._t("Add Files"))
    self.add_folder_button = PushButton(self._t("Add Folder"))
    self.clear_list_button = PushButton(self._t("Clear List"))
    self.add_files_button.setIcon(FIF.ADD)
    self.add_folder_button.setIcon(FIF.FOLDER_ADD)
    self.clear_list_button.setIcon(FIF.BROOM)
    file_toolbar.addWidget(self.add_files_button)
    file_toolbar.addWidget(self.add_folder_button)
    file_toolbar.addWidget(self.clear_list_button)
    file_toolbar.addStretch(1)
    input_layout.addLayout(file_toolbar)

    self.file_workspace = FileWorkspace(
        data_service=getattr(self.controller, "file_list_data_service", None),
        translate=self._t,
    )
    self.file_list = self.file_workspace
    input_layout.addWidget(self.file_workspace, 1)
    page_layout.addWidget(input_card, 1)

    task_card = CardWidget()
    task_layout = QGridLayout(task_card)
    task_layout.setContentsMargins(12, 8, 12, 8)
    task_layout.setHorizontalSpacing(8)
    task_layout.setVerticalSpacing(4)
    self.translation_task_title = BodyLabel(self._t("Translation Task"))
    self.translation_task_title.setStyleSheet("font-weight: 600;")
    task_layout.addWidget(self.translation_task_title, 0, 0, 1, 6)

    self.output_folder_label = CaptionLabel(self._t("Output Directory:"))
    task_layout.addWidget(self.output_folder_label, 1, 0)
    self.output_folder_input = LineEdit()
    self.output_folder_input.setPlaceholderText(self._t("Select or drag output folder..."))
    task_layout.addWidget(self.output_folder_input, 1, 1, 1, 3)
    self.browse_button = PushButton(self._t("Browse..."))
    self.open_button = PushButton(self._t("Open"))
    task_layout.addWidget(self.browse_button, 1, 4)
    task_layout.addWidget(self.open_button, 1, 5)

    self.workflow_mode_label = CaptionLabel(self._t("Translation Workflow Mode:"))
    task_layout.addWidget(self.workflow_mode_label, 2, 0)
    self.workflow_mode_combo = ComboBox()
    self.workflow_mode_combo.addItems([
        self._t("Normal Translation"),
        self._t("Export Translation"),
        self._t("Export Original Text"),
        self._t("Translate JSON Only"),
        self._t("Import Translation and Render"),
        self._t("Colorize Only"),
        self._t("Upscale Only"),
        self._t("Inpaint Only"),
        self._t("Replace Translation"),
    ])
    task_layout.addWidget(self.workflow_mode_combo, 2, 1, 1, 2)
    self.workflow_mode_hint_label = CaptionLabel(
        self._t("Choose translation workflow mode before starting the task.")
    )
    self.workflow_mode_hint_label.setWordWrap(False)
    self.workflow_mode_hint_label.setToolTip(self.workflow_mode_hint_label.text())
    task_layout.addWidget(self.workflow_mode_hint_label, 2, 3, 1, 2)
    self.start_button = PrimaryPushButton(self._t("Start Translation"))
    self.start_button.setFixedHeight(36)
    task_layout.addWidget(self.start_button, 2, 5)
    page_layout.addWidget(task_card, 0)
    self.translation_task_card = task_card

    self.add_files_button.clicked.connect(self._trigger_add_files)
    self.add_folder_button.clicked.connect(self.controller.add_folder)
    self.clear_list_button.clicked.connect(self.file_workspace.clear_action)
    self.file_workspace.selected_remove_requested.connect(
        lambda paths: [self.controller.remove_file(path) for path in paths]
    )
    self.file_workspace.clear_requested.connect(self.controller.clear_file_list)
    self.file_workspace.selection_count_changed.connect(
        lambda count: self.clear_list_button.setText(
            self._t("Clear Selected") if count else self._t("Clear List")
        )
    )
    self.browse_button.clicked.connect(self.controller.select_output_folder)
    self.open_button.clicked.connect(self.controller.open_output_folder)
    self.start_button.clicked.connect(self.controller.start_backend_task)
    self.workflow_mode_combo.currentIndexChanged.connect(self._on_workflow_mode_changed)

    return page
