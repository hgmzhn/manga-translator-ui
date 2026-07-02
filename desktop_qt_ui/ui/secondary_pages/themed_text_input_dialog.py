from __future__ import annotations

from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QDialog
from qfluentwidgets import Dialog, LineEdit


class ThemedTextInputDialog(Dialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        label: str,
        text: str = "",
        ok_text: str = "OK",
        cancel_text: str = "Cancel",
        placeholder: str = "",
    ):
        super().__init__(title, label, parent)
        self.setWindowTitle(title)
        self.setTitleBarVisible(True)
        self.setModal(True)

        self.line_edit = LineEdit(self)
        self.line_edit.setText(text)
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.returnPressed.connect(self.accept)
        self.textLayout.addWidget(self.line_edit)

        self.yesButton.setText(ok_text)
        self.cancelButton.setText(cancel_text)
        self.yesButton.clicked.disconnect()
        self.cancelButton.clicked.disconnect()
        self.yesButton.clicked.connect(self.accept)
        self.cancelButton.clicked.connect(self.reject)
        self.yesButton.setDefault(True)
        self.yesButton.setAutoDefault(True)

        self.setFixedSize(460, max(self.sizeHint().height() + 24, 230))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.line_edit.setFocus()
        self.line_edit.selectAll()

    def text_value(self) -> str:
        return self.line_edit.text()


def themed_get_text(
    parent,
    title: str,
    label: str,
    text: str = "",
    ok_text: str = "OK",
    cancel_text: str = "Cancel",
    placeholder: str = "",
) -> tuple[str, bool]:
    dialog = ThemedTextInputDialog(
        parent,
        title=title,
        label=label,
        text=text,
        ok_text=ok_text,
        cancel_text=cancel_text,
        placeholder=placeholder,
    )
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.text_value(), accepted
