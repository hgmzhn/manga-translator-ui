
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CardWidget, FluentIcon as FIF, PushButton, SmoothScrollArea


class CollapsibleFrame(CardWidget):
    """
    一个可折叠/展开的QWidget容器，借鉴自旧项目的CollapsibleFrame。
    """
    def __init__(self, title: str = "", parent: QWidget = None):
        super().__init__(parent)
        self.setBorderRadius(8)

        self.is_expanded = True

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)

        # --- 标题栏 ---
        self.header_button = PushButton(title, self, FIF.DOWN)
        self.header_button.setCheckable(True)
        self.header_button.setChecked(True)
        self.header_button.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # --- 内容容器 ---
        self.content_area = SmoothScrollArea(self)
        self.content_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_area.setWidgetResizable(True)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_area.setWidget(self.content_widget)

        # --- 动画 ---
        self.animation = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.animation.setDuration(200)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # --- 布局 ---
        self.layout.addWidget(self.header_button)
        self.layout.addWidget(self.content_area)

        # --- 连接信号 ---
        self.header_button.toggled.connect(self.toggle)

    def toggle(self, checked: bool):
        self.is_expanded = checked
        self.header_button.setIcon(FIF.DOWN if checked else FIF.CHEVRON_RIGHT)
        
        start_height = self.content_area.height()
        if checked:
            # 展开
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            end_height = self.content_widget.sizeHint().height()
        else:
            # 折叠
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            end_height = 0

        self.animation.setStartValue(start_height)
        self.animation.setEndValue(end_height)
        self.animation.start() 

    def add_widget(self, widget: QWidget):
        """向内容区域添加小部件"""
        self.content_layout.addWidget(widget)
