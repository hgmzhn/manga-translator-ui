from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog
from qfluentwidgets import FluentStyleSheet
from qframelesswindow import FramelessDialog


class FluentSecondaryDialog(FramelessDialog):
    """Shared Fluent shell for secondary dialogs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleBar.setVisible(False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContentsMargins(0, 0, 0, 0)
        self.apply_fluent_dialog_style()

    def apply_fluent_dialog_style(self):
        FluentStyleSheet.DIALOG.apply(self)


DialogCode = QDialog.DialogCode
