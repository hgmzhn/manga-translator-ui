"""
滚轮事件过滤器
解决Qt控件（如QComboBox、QSpinBox等）捕获滚轮事件导致页面无法滚动的问题
"""

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox


class WheelEventFilter(QObject):
    """
    滚轮事件过滤器
    阻止未获得焦点的控件响应滚轮事件，从而让滚轮事件传递给父控件（滚动区域）
    """
    
    def eventFilter(self, obj, event):
        """
        过滤事件
        如果是滚轮事件且控件未获得焦点，则忽略该事件，让其传递给父控件
        """
        if event.type() == QEvent.Type.Wheel:
            # 检查是否是需要特殊处理的控件
            if isinstance(obj, (QComboBox, QSpinBox, QDoubleSpinBox)):
                # 如果控件没有焦点，则忽略滚轮事件
                if not obj.hasFocus():
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
        if isinstance(w, (QComboBox, QSpinBox, QDoubleSpinBox)):
            w.installEventFilter(wheel_filter)
            # 禁用控件的焦点策略为滚轮焦点
            w.setFocusPolicy(w.focusPolicy() & ~0x0008)  # 移除 WheelFocus
        
        # 递归处理子控件
        for child in w.findChildren(QComboBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
        
        for child in w.findChildren(QSpinBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
        
        for child in w.findChildren(QDoubleSpinBox):
            child.installEventFilter(wheel_filter)
            child.setFocusPolicy(child.focusPolicy() & ~0x0008)
    
    install_recursive(widget)
    return wheel_filter
