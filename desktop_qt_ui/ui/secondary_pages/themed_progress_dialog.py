from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import (
    Dialog,
    FluentIcon as FIF,
    IndeterminateProgressBar,
    ProgressBar,
    TransparentToolButton,
)


class ThemedProgressDialog(Dialog):
    def __init__(self, label_text: str, cancel_button_text: str | None, parent=None):
        super().__init__("", label_text, parent)
        self._was_canceled = False
        self._minimum = 0
        self._maximum = 0
        self._value = 0

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setTitleBarVisible(False)
        self.setMinimumWidth(420)

        self.close_button = TransparentToolButton(FIF.CLOSE, self)
        self.close_button.setFixedSize(32, 32)
        self.close_button.clicked.connect(self.cancel)
        self.close_button.setVisible(bool(cancel_button_text))
        if cancel_button_text:
            self.close_button.setToolTip(cancel_button_text)

        self.progress_bar = IndeterminateProgressBar(self, start=True)
        self.progress_bar.setFixedHeight(4)
        self.textLayout.addWidget(self.progress_bar)

        self.yesButton.hide()
        self.cancelButton.hide()
        self.buttonGroup.hide()

        self.setFixedSize(460, 150)
        self._sync_close_button_geometry()

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
        self.close_button.setVisible(True)
        self.close_button.setToolTip(text)

    def setCancelButton(self, button):
        if button is None:
            self.close_button.hide()

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

    def _sync_close_button_geometry(self):
        self.close_button.move(self.width() - self.close_button.width() - 12, 10)
        self.close_button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_close_button_geometry()


def apply_progress_dialog_style(dialog: Dialog) -> Dialog:
    return dialog


def create_progress_dialog(parent, title: str, label_text: str, cancel_button_text: str | None = None) -> ThemedProgressDialog:
    dialog = ThemedProgressDialog(label_text, cancel_button_text, parent)
    dialog.setWindowTitle(title)
    return dialog
