"""
Fluent toggle switch adapter used by settings forms.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import SwitchButton


class ToggleSwitch(SwitchButton):
    """qfluentwidgets SwitchButton with the legacy QCheckBox-like signal."""

    stateChanged = pyqtSignal(int)  # 0 or 2, compatible with QCheckBox

    def __init__(self, parent: QWidget | None = None, checked: bool = False):
        super().__init__(parent)
        self.setText("")
        self.setOnText("")
        self.setOffText("")
        self.label.hide()
        self.setSpacing(0)
        self.setFixedSize(44, 24)
        self.checkedChanged.connect(self._emit_state_changed)
        self.setCheckedNoSignal(checked)

    def setCheckedNoSignal(self, checked: bool):
        """Set state without notifying settings bindings."""
        old_self_block = self.blockSignals(True)
        try:
            self.setChecked(checked)
        finally:
            self.blockSignals(old_self_block)

    def _emit_state_changed(self, checked: bool):
        self.stateChanged.emit(2 if checked else 0)
