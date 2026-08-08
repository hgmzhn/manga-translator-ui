"""
滚轮事件过滤器

统一约定：滑块 / 数值框 / 下拉框在未获得键盘焦点时不响应滚轮，
事件直通父级滚动区域；获得键盘焦点（点击 / Tab）后保持控件默认滚轮行为。
"""

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QSlider, QWidget
from qfluentwidgets import ComboBox

# 需要接管滚轮语义的控件类型（注意：不含 QScrollBar，滚动条必须始终响应滚轮）
_WHEEL_TARGET_TYPES = (QAbstractSpinBox, QComboBox, QSlider, ComboBox)


class NoWheelComboBox(ComboBox):
    """禁用滚轮事件的下拉框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editable = False

    def addItem(self, text: str, userData=None):
        super().addItem(text, userData=userData)

    def insertItem(self, index: int, text: str, userData=None):
        super().insertItem(index, text, userData=userData)

    def setEditable(self, editable: bool):
        self._editable = bool(editable)

    def isEditable(self) -> bool:
        return self._editable

    def mouseReleaseEvent(self, event):
        super(ComboBox, self).mouseReleaseEvent(event)
        self.showPopup()

    def showPopup(self):
        self._toggleComboMenu()

    def hidePopup(self):
        self._closeComboMenu()

    def wheelEvent(self, event):
        """完全忽略滚轮事件"""
        event.ignore()


class WheelEventFilter(QObject):
    """
    滚轮事件过滤器

    无焦点时拦下控件自身的滚轮处理并保持事件未接受状态——Qt 的滚轮
    传播机制会把未接受的事件继续交给父级（滚动区域）；有焦点时不干预。
    """

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Type.Wheel
            and isinstance(obj, QWidget)
            and not obj.hasFocus()
            and not bool(obj.property("wheel_switch_enabled"))
        ):
            event.ignore()
            return True
        return super().eventFilter(obj, event)


def _demote_wheel_focus(widget: QWidget):
    """WheelFocus → StrongFocus：滚轮不再夺取焦点，点击 / Tab 仍可聚焦。"""
    if widget.focusPolicy() == Qt.FocusPolicy.WheelFocus:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def install_wheel_filter(widget: QWidget) -> WheelEventFilter:
    """
    为指定控件及其所有滑块 / 数值框 / 下拉框子控件安装滚轮事件过滤器

    Args:
        widget: 需要安装过滤器的顶层控件

    Returns:
        安装好的过滤器实例（父对象为 widget，随其销毁）
    """
    wheel_filter = WheelEventFilter(widget)

    targets = {
        child
        for target_type in _WHEEL_TARGET_TYPES
        for child in widget.findChildren(target_type)
    }
    if isinstance(widget, _WHEEL_TARGET_TYPES):
        targets.add(widget)

    for target in targets:
        target.installEventFilter(wheel_filter)
        _demote_wheel_focus(target)

    return wheel_filter
