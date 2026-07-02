"""
滚轮事件过滤器
解决下拉框、数字输入框等控件捕获滚轮事件导致页面无法滚动的问题
"""

from PyQt6.QtCore import QEvent, QObject
from qfluentwidgets import ComboBox, DoubleSpinBox, SpinBox


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
    阻止未获得焦点的控件响应滚轮事件，从而让滚轮事件传递给父控件（滚动区域）
    """
    
    def eventFilter(self, obj, event):
        """
        过滤事件
        完全阻止下拉菜单响应滚轮事件
        """
        if event.type() == QEvent.Type.Wheel:
            # 检查是否是需要特殊处理的控件
            if isinstance(obj, (ComboBox, SpinBox, DoubleSpinBox)):
                # 完全忽略滚轮事件，无论是否有焦点
                event.ignore()
                return True
        
        # 继续正常处理其他事件
        return super().eventFilter(obj, event)


def install_wheel_filter(widget):
    """
    为指定控件及其所有子控件安装滚轮事件过滤器
    
    Args:
        widget: 需要安装过滤器的顶层控件
    """
    wheel_filter = WheelEventFilter(widget)
    
    # 递归为所有子控件安装过滤器
    def install_recursive(w):
        if isinstance(w, (ComboBox, SpinBox, DoubleSpinBox)):
            w.installEventFilter(wheel_filter)
            # 禁用控件的焦点策略为滚轮焦点
            w.setFocusPolicy(w.focusPolicy() & ~0x0008)  # 移除 WheelFocus
        
        # 递归处理子控件
        for child in w.findChildren(ComboBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
        
        for child in w.findChildren(SpinBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
        
        for child in w.findChildren(DoubleSpinBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
    
    install_recursive(widget)
    return wheel_filter
