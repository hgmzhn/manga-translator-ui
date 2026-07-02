
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets import BodyLabel, CardWidget, FluentIcon as FIF, TransparentToolButton


class FileListItemWidget(CardWidget):
    """
    用于文件列表的自定义项小部件，包含缩略图和文件名。
    """
    remove_requested = pyqtSignal(str)

    def __init__(self, file_path: str, thumbnail: QPixmap, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setBorderRadius(8)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(8, 6, 8, 6)
        self.layout.setSpacing(10)

        # 缩略图
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(QSize(40, 40))
        self.thumbnail_label.setPixmap(thumbnail.scaled(
            self.thumbnail_label.size(), 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        ))
        self.layout.addWidget(self.thumbnail_label)

        # 文件名
        import os
        self.filename_label = BodyLabel(os.path.basename(file_path), self)
        self.layout.addWidget(self.filename_label)
        self.layout.addStretch()

        # 移除按钮
        self.remove_button = TransparentToolButton(FIF.CLOSE, self)
        self.remove_button.setFixedSize(QSize(28, 28))
        self.remove_button.setIconSize(QSize(12, 12))
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_button.clicked.connect(self._on_remove)
        self.layout.addWidget(self.remove_button)

    def _on_remove(self):
        self.remove_requested.emit(self.file_path)
