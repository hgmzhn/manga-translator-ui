from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import BodyLabel, FluentIcon as FIF, LineEdit, ListWidget, PrimaryPushButton, PushButton
from ui.secondary_pages.fluent_dialog import DialogCode, FluentSecondaryDialog


def _default_t(text: str, **kwargs) -> str:
    if kwargs:
        return text.format(**kwargs)
    return text


class ModelSelectorDialog(FluentSecondaryDialog):
    """带搜索功能的模型选择对话框"""

    model_selected = pyqtSignal(str)

    def __init__(
        self,
        models: list[str],
        title: str = "选择模型",
        prompt: str = "可用模型：",
        parent=None,
        t_func: Callable[..., str] | None = None,
    ):
        super().__init__(parent)
        self.models = models
        self.selected_model = None
        self._t = t_func or _default_t

        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self._setup_ui(prompt)
        self._populate_list()

    def _setup_ui(self, prompt: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        prompt_label = BodyLabel(prompt)
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        self.search_input = LineEdit()
        self.search_input.setPlaceholderText(self._t("Search models..."))
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input)

        self.model_list = ListWidget()
        self.model_list.setAlternatingRowColors(False)
        self.model_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.model_list, 1)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        self.ok_button = PrimaryPushButton(self._t("OK"))
        self.ok_button.setIcon(FIF.ACCEPT)
        self.ok_button.setFixedSize(112, 38)
        self.ok_button.clicked.connect(self._on_ok_clicked)
        self.ok_button.setEnabled(False)

        cancel_button = PushButton(self._t("Cancel"))
        cancel_button.setIcon(FIF.CANCEL)
        cancel_button.setFixedSize(112, 38)
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(cancel_button)
        button_layout.addWidget(self.ok_button)
        layout.addLayout(button_layout)

        self.model_list.itemSelectionChanged.connect(self._on_selection_changed)

    def _populate_list(self, filter_text: str = ""):
        self.model_list.clear()

        filter_text = filter_text.lower()
        for model in self.models:
            if not filter_text or filter_text in model.lower():
                self.model_list.addItem(QListWidgetItem(model))

        if self.model_list.count() == 1:
            self.model_list.setCurrentRow(0)

    def _on_search_text_changed(self, text: str):
        self._populate_list(text)

    def _on_selection_changed(self):
        self.ok_button.setEnabled(bool(self.model_list.selectedItems()))

    def _on_item_double_clicked(self, item: QListWidgetItem):
        self.selected_model = item.text()
        self.accept()

    def _on_ok_clicked(self):
        selected_items = self.model_list.selectedItems()
        if selected_items:
            self.selected_model = selected_items[0].text()
            self.accept()

    def get_selected_model(self) -> str | None:
        return self.selected_model

    @staticmethod
    def get_model(
        models: list[str],
        title: str = "选择模型",
        prompt: str = "可用模型：",
        parent=None,
        t_func: Callable[..., str] | None = None,
    ) -> tuple[str | None, bool]:
        dialog = ModelSelectorDialog(models, title, prompt, parent=parent, t_func=t_func)
        result = dialog.exec()
        return dialog.get_selected_model(), result == DialogCode.Accepted
