from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog
from qfluentwidgets import Dialog, IndeterminateProgressBar, ProgressBar


class ThemedProgressDialog(Dialog):
    def __init__(self, label_text: str, cancel_button_text: str | None, parent=None):
        super().__init__("", label_text, parent)
        self._was_canceled = False
        self._minimum = 0
        self._maximum = 0
        self._value = 0

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setTitleBarVisible(True)
        self.setMinimumWidth(420)

        self.progress_bar = IndeterminateProgressBar(self, start=True)
        self.progress_bar.setFixedHeight(4)
        self.textLayout.addWidget(self.progress_bar)

        self.yesButton.hide()
        if cancel_button_text:
            self.cancelButton.setText(cancel_button_text)
            self.cancelButton.clicked.disconnect()
            self.cancelButton.clicked.connect(self.cancel)
        else:
            self.cancelButton.hide()
            self.buttonGroup.hide()

        self.setFixedSize(460, 190 if cancel_button_text else 150)

    def setWindowTitle(self, title: str):
        super().setWindowTitle(title)
        if hasattr(self, "windowTitleLabel"):
            self.windowTitleLabel.setText(title)
        if hasattr(self, "titleLabel"):
            self.titleLabel.setText(title)

    def setLabelText(self, text: str):
        self.content = text
        self.contentLabel.setText(text)

    def labelText(self) -> str:
        return self.contentLabel.text()

    def setCancelButtonText(self, text: str):
        self.cancelButton.show()
        self.buttonGroup.show()
        self.cancelButton.setText(text)

    def setCancelButton(self, button):
        if button is None:
            self.cancelButton.hide()
            self.buttonGroup.hide()

    def setMinimumDuration(self, duration: int):
        del duration

    def setRange(self, minimum: int, maximum: int):
        self._minimum = minimum
        self._maximum = maximum
        if maximum > minimum and isinstance(self.progress_bar, IndeterminateProgressBar):
            old_bar = self.progress_bar
            self.textLayout.removeWidget(old_bar)
            old_bar.deleteLater()
            self.progress_bar = ProgressBar(self)
            self.progress_bar.setFixedHeight(4)
            self.textLayout.addWidget(self.progress_bar)
        self.progress_bar.setRange(minimum, maximum)

    def setMinimum(self, minimum: int):
        self.setRange(minimum, self._maximum)

    def setMaximum(self, maximum: int):
        self.setRange(self._minimum, maximum)

    def setValue(self, value: int):
        self._value = value
        if hasattr(self.progress_bar, "setValue"):
            self.progress_bar.setValue(value)

    def value(self) -> int:
        return self._value

    def cancel(self):
        self._was_canceled = True
        self.reject()

    def wasCanceled(self) -> bool:
        return self._was_canceled


def apply_progress_dialog_style(dialog: QDialog) -> QDialog:
    return dialog


def create_progress_dialog(parent, title: str, label_text: str, cancel_button_text: str | None = None) -> ThemedProgressDialog:
    dialog = ThemedProgressDialog(label_text, cancel_button_text, parent)
    dialog.setWindowTitle(title)
    return dialog
