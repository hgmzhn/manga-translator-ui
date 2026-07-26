from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout
from qfluentwidgets import (
    Dialog,
    FluentIcon as FIF,
    IndeterminateProgressBar,
    ProgressBar,
    TransparentToolButton,
)

from ui.secondary_pages.fluent_dialog import normalize_dialog_parent


class ThemedProgressDialog(Dialog):
    def __init__(self, label_text: str, cancel_button_text: str | None, parent=None):
        super().__init__("", label_text, parent)
        self._was_canceled = False
        self._minimum = 0
        self._maximum = 0
        self._value = 0

        # parent 归一化后仍为 None 时 WindowModal 等于不模态，退到 ApplicationModal
        self.setWindowModality(
            Qt.WindowModality.WindowModal
            if self.parent() is not None
            else Qt.WindowModality.ApplicationModal
        )
        self.setTitleBarVisible(False)
        self.setMinimumWidth(420)

        self.close_button = TransparentToolButton(FIF.CLOSE, self)
        self.close_button.setFixedSize(32, 32)
        self.close_button.clicked.connect(self.cancel)
        self.close_button.setVisible(bool(cancel_button_text))
        if cancel_button_text:
            self.close_button.setToolTip(cancel_button_text)

        # 顶部真正的标题行：标题 + stretch + 关闭按钮，取代手动 move/raise_
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.textLayout.removeWidget(self.titleLabel)
        header_layout.addWidget(self.titleLabel, 1, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.textLayout.insertLayout(0, header_layout)

        # 两条进度条常驻布局、按模式切换可见性，不再销毁重建
        self._indeterminate_bar = IndeterminateProgressBar(self, start=True)
        self._indeterminate_bar.setFixedHeight(4)
        self._determinate_bar = ProgressBar(self)
        self._determinate_bar.setFixedHeight(4)
        self._determinate_bar.hide()
        self.textLayout.addWidget(self._indeterminate_bar)
        self.textLayout.addWidget(self._determinate_bar)

        self.yesButton.hide()
        self.cancelButton.hide()
        self.buttonGroup.hide()

        self.setFixedSize(460, 150)

    @property
    def progress_bar(self):
        """当前生效的进度条（兼容旧属性访问）。"""
        if self._determinate_bar.isVisibleTo(self):
            return self._determinate_bar
        return self._indeterminate_bar

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
        determinate = maximum > minimum
        if determinate:
            self._determinate_bar.setRange(minimum, maximum)
            self._indeterminate_bar.stop()
        else:
            self._indeterminate_bar.start()
        self._determinate_bar.setVisible(determinate)
        self._indeterminate_bar.setVisible(not determinate)

    def setMinimum(self, minimum: int):
        self.setRange(minimum, self._maximum)

    def setMaximum(self, maximum: int):
        self.setRange(self._minimum, maximum)

    def setValue(self, value: int):
        self._value = value
        self._determinate_bar.setValue(value)

    def value(self) -> int:
        return self._value

    def cancel(self):
        self._was_canceled = True
        self.reject()

    def wasCanceled(self) -> bool:
        return self._was_canceled


def apply_progress_dialog_style(dialog: Dialog) -> Dialog:
    return dialog


def create_progress_dialog(parent, title: str, label_text: str, cancel_button_text: str | None = None) -> ThemedProgressDialog:
    dialog = ThemedProgressDialog(label_text, cancel_button_text, normalize_dialog_parent(parent))
    dialog.setWindowTitle(title)
    return dialog
