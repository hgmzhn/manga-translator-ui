"""Request inspection page for the standalone Agent debugger."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, PlainTextEdit, PushButton, TitleLabel

from ui.theme import monospace_font


class RequestDebugPage(QWidget):
    """Show the last serialized SDK request without saving it to disk."""

    def __init__(self, t_func, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(TitleLabel(t_func("Chat request body"), self))
        description = BodyLabel(t_func("Chat request body placeholder"), self)
        description.setWordWrap(True)
        layout.addWidget(description)
        self.body = PlainTextEdit(self)
        self.body.setReadOnly(True)
        self.body.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.body.setFont(monospace_font())
        layout.addWidget(self.body, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.copy_button = PushButton(t_func("Copy"), self)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_body)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)

    @pyqtSlot(str)
    def set_request_body(self, body: str):
        self.body.setPlainText(body)
        self.copy_button.setEnabled(bool(body))

    def _copy_body(self):
        QApplication.clipboard().setText(self.body.toPlainText())
