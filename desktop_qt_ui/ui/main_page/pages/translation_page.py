from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.file_list_view import FileListView
from ui.widgets.wheel_filter import NoWheelComboBox as QComboBox

def create_translation_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_translation")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(4)
    self.translation_page_title = QLabel(self._t("Normal Translation"))
    self.translation_page_title.setObjectName("page_title")
    self.translation_page_subtitle = QLabel(
        self._t("Tip: Standard translation pipeline with detection, OCR, translation and rendering")
    )
    self.translation_page_subtitle.setObjectName("page_subtitle")
    self.translation_page_subtitle.setWordWrap(True)
    header_layout.addWidget(self.translation_page_title)
    header_layout.addWidget(self.translation_page_subtitle)
    page_layout.addWidget(header_card)

    self.translation_input_card = QGroupBox("")
    self.translation_input_card.setObjectName("section_card")
    input_layout = QVBoxLayout(self.translation_input_card)
    input_layout.setContentsMargins(12, 14, 12, 12)
    input_layout.setSpacing(10)

    file_button_widget = QWidget()
    file_button_widget.setObjectName("inline_toolbar")
    file_buttons_layout = QHBoxLayout(file_button_widget)
    file_buttons_layout.setContentsMargins(0, 0, 0, 0)
    file_buttons_layout.setSpacing(8)
    self.add_files_button = QPushButton(self._t("Add Files"))
    self.add_folder_button = QPushButton(self._t("Add Folder"))
    self.clear_list_button = QPushButton(self._t("Clear List"))
    self.add_files_button.setProperty("chipButton", True)
    self.add_folder_button.setProperty("chipButton", True)
    self.clear_list_button.setProperty("chipButton", True)
    file_buttons_layout.addWidget(self.add_files_button)
    file_buttons_layout.addWidget(self.add_folder_button)
    file_buttons_layout.addWidget(self.clear_list_button)
    file_buttons_layout.addStretch()
    input_layout.addWidget(file_button_widget)

    self.file_list = FileListView(None, self)
    self.file_list.setObjectName("translation_file_list")
    input_layout.addWidget(self.file_list, 1)
    page_layout.addWidget(self.translation_input_card, 1)

    self.translation_task_card = QGroupBox(self._t("Translation Task"))
    self.translation_task_card.setObjectName("section_card")
    task_layout = QVBoxLayout(self.translation_task_card)
    task_layout.setContentsMargins(12, 14, 12, 12)
    task_layout.setSpacing(10)

    self.output_folder_label = QLabel(self._t("Output Directory:"))
    self.output_folder_label.setObjectName("row_label")
    task_layout.addWidget(self.output_folder_label)

    output_folder_widget = QWidget()
    output_folder_widget.setObjectName("inline_toolbar")
    output_folder_layout = QHBoxLayout(output_folder_widget)
    output_folder_layout.setContentsMargins(0, 0, 0, 0)
    output_folder_layout.setSpacing(8)
    self.output_folder_input = QLineEdit()
    self.output_folder_input.setPlaceholderText(self._t("Select or drag output folder..."))
    self.browse_button = QPushButton(self._t("Browse..."))
    self.open_button = QPushButton(self._t("Open"))
    self.browse_button.setProperty("chipButton", True)
    self.open_button.setProperty("chipButton", True)
    output_folder_layout.addWidget(self.output_folder_input)
    output_folder_layout.addWidget(self.browse_button)
    output_folder_layout.addWidget(self.open_button)
    task_layout.addWidget(output_folder_widget)

    self.workflow_mode_hint_label = QLabel(
        self._t("Choose translation workflow mode before starting the task.")
    )
    self.workflow_mode_hint_label.setObjectName("page_subtitle")
    self.workflow_mode_hint_label.setWordWrap(True)
    task_layout.addWidget(self.workflow_mode_hint_label)

    self.workflow_mode_label = QLabel(self._t("Translation Workflow Mode:"))
    self.workflow_mode_label.setObjectName("row_label")
    task_layout.addWidget(self.workflow_mode_label)

    self.workflow_mode_combo = QComboBox()
    self.workflow_mode_combo.addItems([
        self._t("Normal Translation"),
        self._t("Export Translation"),
        self._t("Export Original Text"),
        self._t("Translate JSON Only"),
        self._t("Import Translation and Render"),
        self._t("Colorize Only"),
        self._t("Upscale Only"),
        self._t("Inpaint Only"),
        self._t("Replace Translation")
    ])
    self.workflow_mode_combo.currentIndexChanged.connect(self._on_workflow_mode_changed)
    task_layout.addWidget(self.workflow_mode_combo)

    self.start_button = QPushButton(self._t("Start Translation"))
    self.start_button.setObjectName("start_translation_button")
    self.start_button.setProperty("primaryAction", True)
    self.start_button.setProperty("translationState", "ready")
    self.start_button.setFixedHeight(44)
    task_layout.addWidget(self.start_button)
    page_layout.addWidget(self.translation_task_card)

    self.add_files_button.clicked.connect(self._trigger_add_files)
    self.add_folder_button.clicked.connect(self.controller.add_folder)
    self.clear_list_button.clicked.connect(self.controller.clear_file_list)
    self.file_list.file_remove_requested.connect(self.controller.remove_file)
    self.browse_button.clicked.connect(self.controller.select_output_folder)
    self.open_button.clicked.connect(self.controller.open_output_folder)
    self.start_button.clicked.connect(self.controller.start_backend_task)

    return page


