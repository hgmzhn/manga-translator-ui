import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
)
from qfluentwidgets import BodyLabel, CardWidget, CaptionLabel, PlainTextEdit, PrimaryPushButton, PushButton, TitleLabel
from ui.secondary_pages.themed_message_box import themed_critical
from ui.theme import (
    monospace_font as _monospace_font,
)


class SimplePromptEditorDialog(QDialog):
    def __init__(
        self,
        file_path: str,
        title_text: str,
        description_text: str,
        section_text: str,
        hint_text: str,
        default_prompt_text: str,
        ensure_prompt_func,
        load_prompt_func,
        save_prompt_func,
        t_func=None,
        parent=None,
    ):
        super().__init__(parent)
        self._t = t_func or (lambda key, **kwargs: key)
        self._file_path = ensure_prompt_func(file_path)
        self._title_text = title_text
        self._description_text = description_text
        self._section_text = section_text
        self._hint_text = hint_text
        self._default_prompt_text = default_prompt_text
        self._load_prompt_func = load_prompt_func
        self._save_prompt_func = save_prompt_func
        self._was_modified = False
        self._status_label = None
        self.editor = None

        self._setup_ui()
        self._load_prompt()

    def _setup_ui(self):
        self.setWindowTitle(self._t("Edit") + f": {os.path.basename(self._file_path)}")
        self.setMinimumSize(880, 620)
        self.resize(980, 720)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = TitleLabel(self._title_text)
        subtitle = BodyLabel(self._description_text)
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        card = CardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(8)

        section = BodyLabel(self._section_text)
        card_layout.addWidget(section)

        hint = CaptionLabel(self._hint_text)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(hint)

        self.editor = PlainTextEdit(self)
        self.editor.setFont(_monospace_font())
        card_layout.addWidget(self.editor, 1)

        root.addWidget(card, 1)

        self._status_label = CaptionLabel("")
        root.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.cancel_button = PushButton(self._t("Cancel"))
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = PrimaryPushButton(self._t("Save"))
        self.save_button.clicked.connect(self._save)

        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        root.addLayout(button_row)

    def _load_prompt(self):
        self.editor.setPlainText(self._load_prompt_func(self._file_path) or self._default_prompt_text)
        if self._status_label is not None:
            self._status_label.setText(self._hint_text)

    def _save(self):
        try:
            self._save_prompt_func(self._file_path, self.editor.toPlainText().rstrip())
            self._was_modified = True
            self.accept()
        except Exception as e:
            themed_critical(self, self._t("Error"), str(e))

    def get_was_modified(self) -> bool:
        return self._was_modified
