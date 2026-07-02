"""
Fluent toggle switch adapter used by settings forms.
"""

from PyQt6.QtWidgets import QWidget
from qfluentwidgets import SwitchButton


class ToggleSwitch(SwitchButton):
    """Compact qfluentwidgets switch used by settings forms."""

    def __init__(self, parent: QWidget | None = None, checked: bool = False):
        super().__init__(parent)
        self.setText("")
        self.setOnText("")
        self.setOffText("")
        self.label.hide()
        self.setSpacing(0)
        self.setFixedSize(44, 24)
        self.setCheckedSilently(checked)

    def setCheckedSilently(self, checked: bool):
        """Set state without notifying settings bindings."""
        old_self_block = self.blockSignals(True)
        try:
            self.setChecked(checked)
        finally:
            self.blockSignals(old_self_block)
