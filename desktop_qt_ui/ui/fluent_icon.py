import os

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QIcon, QIconEngine, QImage, QPainter, QPixmap
from qfluentwidgets.common.icon import drawSvgIcon, writeSvg

from ui.theme import get_current_theme_colors
from utils.resource_helper import resource_path


class _ThemedFluentSvgIconEngine(QIconEngine):
    def __init__(self, icon_path: str, color_token: str):
        super().__init__()
        self.icon_path = icon_path
        self.color_token = color_token

    def paint(self, painter: QPainter, rect, mode, state):
        colors = get_current_theme_colors()
        color = QColor(colors.get(self.color_token, "#1f1f1f")).name()
        svg = writeSvg(self.icon_path, fill=color)
        if not svg:
            QIcon(self.icon_path).paint(painter, rect, Qt.AlignmentFlag.AlignCenter, mode, state)
            return

        painter.save()
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(0.5)
        elif mode == QIcon.Mode.Selected:
            painter.setOpacity(0.7)

        drawSvgIcon(svg.encode(), painter, rect)
        painter.restore()

    def clone(self):
        return _ThemedFluentSvgIconEngine(self.icon_path, self.color_token)

    def pixmap(self, size, mode, state):
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        pixmap = QPixmap.fromImage(image, Qt.ImageConversionFlag.NoFormatConversion)

        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap


def themed_fluent_svg_icon(filename: str, color_token: str = "text_primary") -> QIcon:
    icon_path = resource_path(os.path.join("desktop_qt_ui", "ui", "icons", filename))
    return QIcon(_ThemedFluentSvgIconEngine(icon_path, color_token))
