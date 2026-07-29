
import logging

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QRegularExpression, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QIntValidator,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    DropDownPushButton,
    Flyout,
    FlyoutAnimationType,
    FlyoutViewBase,
    LineEdit,
    ToolButton,
    isDarkTheme,
    themeColor,
)
from ui.fluent_icon import themed_fluent_svg_icon
from ui.widgets.hover_hint import set_hover_hint
from ui.widgets.widget_cleanup import delete_widget

logger = logging.getLogger('manga_translator')


# ═══════════════════════════════════════════════════════════════
#  ScreenColorPicker — 全屏自定义屏幕取色器
# ═══════════════════════════════════════════════════════════════

class ScreenColorPicker(QWidget):
    """全屏屏幕取色器：稳定十字光标 + 像素放大镜 + 实时颜色/RGB 预览。

    - 左键点击拾取颜色
    - 右键 / ESC 取消
    """

    color_picked = pyqtSignal(QColor)
    canceled = pyqtSignal()

    MAG_N = 11        # 放大区域边长(像素，奇数)
    MAG_S = 10        # 每像素放大倍数
    OFFSET = 25       # 预览框离光标偏移

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)

        self._color = QColor(0, 0, 0)
        self._mpos = QCursor.pos()
        self._overlay_rect = QRect()
        self._shot: QPixmap | None = None
        self._img = None
        self._dpr = 1.0

    # ── 公开接口 ──────────────────────────────────────────────

    def start(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.virtualGeometry()
        self._shot = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
        self._img = self._shot.toImage()
        self._dpr = self._shot.devicePixelRatio()
        self.setGeometry(geo)
        self._update_cursor_state(QCursor.pos(), repaint=False)
        self.show()
        self.activateWindow()
        self.raise_()
        # 确保键盘焦点在取色器上，ESC 取消可达
        self.setFocus()

    # ── 内部 ──────────────────────────────────────────────────

    def _px_color(self, lx, ly):
        if self._img is None:
            return QColor(0, 0, 0)
        px, py = int(lx * self._dpr), int(ly * self._dpr)
        if 0 <= px < self._img.width() and 0 <= py < self._img.height():
            return self._img.pixelColor(px, py)
        return QColor(0, 0, 0)

    # ── 绘制 ──────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._shot is None:
            # WA_OpaquePaintEvent 契约：每个像素都必须被绘制，
            # 否则会把陈旧的 backing store 内容显示出来。
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0))
            p.end()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipRegion(event.region())
        p.drawPixmap(self.rect(), self._shot)
        p.fillRect(event.rect(), QColor(0, 0, 0, 15))

        loc = self.mapFromGlobal(self._mpos)
        cx, cy = loc.x(), loc.y()
        self._draw_cross(p, cx, cy)
        self._draw_panel(p, cx, cy)
        p.end()

    def _draw_cross(self, p, x, y):
        gap, ln = 6, 22
        for c, w in [(QColor(0, 0, 0, 160), 3), (QColor(255, 255, 255, 220), 1)]:
            p.setPen(QPen(c, w))
            p.drawLine(x - ln, y, x - gap, y)
            p.drawLine(x + gap, y, x + ln, y)
            p.drawLine(x, y - ln, x, y - gap)
            p.drawLine(x, y + gap, x, y + ln)

    def _panel_rect(self, cx, cy) -> QRect:
        """预览面板的位置和尺寸（避免出屏）。"""
        n, s = self.MAG_N, self.MAG_S
        mag = n * s
        pad = 12
        pw = mag + pad * 2
        ph = pad + mag + 8 + 50 + pad
        off = self.OFFSET
        bx = cx + off if cx + off + pw <= self.width() else cx - off - pw
        by = cy + off if cy + off + ph <= self.height() else cy - off - ph
        return QRect(max(bx, 0), max(by, 0), pw, ph)

    def _cursor_overlay_rect(self, cx, cy) -> QRect:
        cross_rect = QRect(cx - 28, cy - 28, 56, 56)
        panel_rect = self._panel_rect(cx, cy).adjusted(-2, -2, 2, 2)
        return cross_rect.united(panel_rect).intersected(self.rect())

    def _draw_panel(self, p, cx, cy):
        n, s = self.MAG_N, self.MAG_S
        mag = n * s
        pad = 12
        rect = self._panel_rect(cx, cy)
        bx, by = rect.x(), rect.y()

        # 背景
        p.setBrush(QColor(24, 24, 28, 235))
        p.setPen(QPen(QColor(70, 70, 70), 1))
        p.drawRoundedRect(rect, 8, 8)

        # 放大镜
        mx, my = bx + pad, by + pad
        half = n // 2
        for dy in range(n):
            for dx in range(n):
                p.fillRect(mx + dx * s, my + dy * s, s, s,
                           self._px_color(cx - half + dx, cy - half + dy))

        # 网格
        p.setPen(QPen(QColor(50, 50, 50, 80), 1))
        for i in range(1, n):
            p.drawLine(mx + i * s, my, mx + i * s, my + mag)
            p.drawLine(mx, my + i * s, mx + mag, my + i * s)
        p.setPen(QPen(QColor(100, 100, 100), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(mx, my, mag, mag)
        # 中心高亮
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawRect(mx + half * s, my + half * s, s, s)

        # 颜色信息
        iy = my + mag + 10
        sw = 26
        p.setBrush(self._color)
        p.setPen(QPen(QColor(180, 180, 180), 1))
        p.drawRoundedRect(mx, iy, sw, sw, 3, 3)

        tx = mx + sw + 8
        f = QFont("Consolas", 10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(240, 240, 240))
        p.drawText(tx, iy + 12, self._color.name().upper())
        f.setBold(False)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QColor(180, 180, 180))
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        p.drawText(tx, iy + 26, f"R:{r} G:{g} B:{b}")

    # ── 事件 ──────────────────────────────────────────────────

    def _update_cursor_state(self, global_pos: QPoint, repaint: bool = True):
        if repaint and global_pos == self._mpos:
            return

        old_rect = QRect(self._overlay_rect)
        self._mpos = global_pos
        loc = self.mapFromGlobal(global_pos)
        self._color = self._px_color(loc.x(), loc.y())
        self._overlay_rect = self._cursor_overlay_rect(loc.x(), loc.y())

        if repaint:
            dirty_rect = old_rect.united(self._overlay_rect).adjusted(-2, -2, 2, 2)
            self.update(dirty_rect.intersected(self.rect()))

    def mouseMoveEvent(self, ev):
        self._update_cursor_state(ev.globalPosition().toPoint())

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.color_picked.emit(self._color)
            self.close()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.canceled.emit()
            self.close()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.canceled.emit()
            self.close()
        else:
            # 其余按键不吞，交给默认处理
            super().keyPressEvent(ev)


class _ColorEntryButton(DropDownPushButton):
    """Compact color entry with current swatch and dropdown chevron."""

    DEFAULT_SIZE = QSize(82, 33)

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._checked = False
        self.setText("")
        self.setMinimumSize(self.DEFAULT_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(max(self.DEFAULT_SIZE.width(), self.minimumWidth()), self.DEFAULT_SIZE.height())

    def minimumSizeHint(self) -> QSize:
        return self.DEFAULT_SIZE

    def setColor(self, color: QColor):
        if self._color != color:
            self._color = QColor(color)
            self.update()

    def setChecked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        enabled = self.isEnabled()
        if self._checked and enabled:
            active_border = _fluent_accent_color(115)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(active_border, 1))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)

        swatch_height = min(22, max(16, self.height() - 10))
        swatch = QRect(9, (self.height() - swatch_height) // 2, 32, swatch_height)
        swatch_color = QColor(self._color)
        if not enabled:
            swatch_color.setAlpha(70)
        p.setBrush(swatch_color)
        if enabled:
            border = QColor("#111111") if _is_light_color(self._color) else QColor("#f5f5f5")
            border.setAlpha(150)
        else:
            border = _fluent_disabled_foreground()
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(swatch, 5, 5)
        p.end()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
            self.update()
        super().changeEvent(event)


class _PaletteSwatch(QWidget):
    """Small fixed color tile used by the in-app palette dialog."""

    clicked = pyqtSignal(str)

    def __init__(self, hex_color: str, parent=None):
        super().__init__(parent)
        self.hex_color = _normalize_hex(hex_color) or "#000000"
        self._selected = False
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        set_hover_hint(self, self.hex_color)

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def paintEvent(self, _event):
        c = QColor(self.hex_color)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)
        p.setBrush(c)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.drawRoundedRect(rect, 5, 5)

        border = QColor("#111111") if _is_light_color(c) else QColor("#f5f5f5")
        border.setAlpha(150)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 5, 5)

        if self._selected:
            p.setPen(QPen(_fluent_accent_color(), 2))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.hex_color)


class _CurrentColorPreview(QWidget):
    """Large current-color chip shown in the custom color section."""

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(54, 54)

    def set_color(self, color: QColor):
        if self._color != color:
            self._color = QColor(color)
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(_fluent_surface_color())
        p.setPen(QPen(_fluent_border_color(), 1))
        p.drawRoundedRect(outer, 8, 8)

        inner = outer.adjusted(7, 7, -7, -7)
        p.setBrush(self._color)
        border = QColor("#111111") if _is_light_color(self._color) else QColor("#f5f5f5")
        border.setAlpha(170)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(inner, 6, 6)
        p.end()


class _ColorField(QWidget):
    """Hue/saturation field with brightness supplied by _BrightnessSlider."""

    color_changed = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self._value = 1.0
        self._cache_key = None
        self._cache_image = None
        self.setMinimumSize(320, 128)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_color(self, color: QColor):
        hue, saturation, value, _alpha = color.getHsvF()
        self._hue = hue if hue >= 0 else self._hue
        self._saturation = max(0.0, min(1.0, saturation))
        self._value = max(0.0, min(1.0, value))
        self.update()

    def set_brightness(self, value: float):
        self._value = max(0.0, min(1.0, value))
        self.update()
        self.color_changed.emit(self._current_color())

    def _current_color(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._saturation, self._value)

    def _set_from_pos(self, pos):
        rect = self.rect().adjusted(1, 1, -2, -2)
        x = max(rect.left(), min(rect.right(), int(pos.x())))
        y = max(rect.top(), min(rect.bottom(), int(pos.y())))
        hue = (x - rect.left()) / max(1, rect.width())
        saturation = 1.0 - (y - rect.top()) / max(1, rect.height())
        if hue == self._hue and saturation == self._saturation:
            return
        self._hue = hue
        self._saturation = saturation
        self.update()
        self.color_changed.emit(self._current_color())

    def _base_image(self, width: int, height: int) -> QImage:
        """明度为 1.0 的色相/饱和度底图，仅随尺寸变化重建。"""
        key = (width, height)
        if self._cache_key == key and self._cache_image is not None:
            return self._cache_image

        image = QImage(width, height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        hue_gradient = QLinearGradient(0, 0, width, 0)
        for i in range(7):
            hue_gradient.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1.0, 1.0))
        painter.fillRect(0, 0, width, height, hue_gradient)
        white_gradient = QLinearGradient(0, 0, 0, height)
        white_gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        white_gradient.setColorAt(1.0, QColor(255, 255, 255, 255))
        painter.fillRect(0, 0, width, height, white_gradient)
        painter.end()

        self._cache_key = key
        self._cache_image = image
        return image

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 7, 7)
        p.save()
        p.setClipPath(path)
        p.drawImage(rect, self._base_image(rect.width(), rect.height()))
        # HSV 的 V 是 RGB 线性缩放，等价于叠加 alpha=(1-V) 的黑色
        if self._value < 1.0:
            p.fillRect(rect, QColor(0, 0, 0, round((1.0 - self._value) * 255)))
        p.restore()
        p.setPen(QPen(_fluent_border_color(), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 7, 7)

        marker_radius = 6
        x = max(rect.left() + marker_radius, min(rect.right() - marker_radius, rect.left() + self._hue * rect.width()))
        y = max(rect.top() + marker_radius, min(rect.bottom() - marker_radius, rect.top() + (1.0 - self._saturation) * rect.height()))
        p.setPen(QPen(QColor(0, 0, 0, 180), 3))
        p.drawEllipse(int(x) - 6, int(y) - 6, 12, 12)
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawEllipse(int(x) - 6, int(y) - 6, 12, 12)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._set_from_pos(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_pos(ev.position())


class _BrightnessSlider(QWidget):
    """Horizontal value slider for the selected hue/saturation."""

    value_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue = 0.0
        self._saturation = 1.0
        self._value = 1.0
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color: QColor):
        hue, saturation, value, _alpha = color.getHsvF()
        self._hue = hue if hue >= 0 else self._hue
        self._saturation = max(0.0, min(1.0, saturation))
        self._value = max(0.0, min(1.0, value))
        self.update()

    def _set_from_pos(self, pos):
        rect = self.rect().adjusted(3, 8, -3, -8)
        value = (max(rect.left(), min(rect.right(), int(pos.x()))) - rect.left()) / max(1, rect.width())
        if value == self._value:
            return
        self._value = value
        self.update()
        self.value_changed.emit(self._value)

    def paintEvent(self, _event):
        rect = self.rect().adjusted(3, 8, -3, -8)
        end_color = QColor.fromHsvF(self._hue, self._saturation, 1.0)
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0))
        gradient.setColorAt(1.0, end_color)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(_fluent_border_color(), 1))
        p.setBrush(gradient)
        p.drawRoundedRect(rect, 5, 5)

        x = rect.left() + self._value * rect.width()
        p.setPen(QPen(QColor(0, 0, 0, 120), 3))
        p.drawLine(int(x), rect.top() - 4, int(x), rect.bottom() + 4)
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawLine(int(x), rect.top() - 4, int(x), rect.bottom() + 4)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._set_from_pos(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_pos(ev.position())


class _ColorPaletteView(FlyoutViewBase):
    """Button-anchored color palette content hosted by qfluentwidgets Flyout."""

    color_changed = pyqtSignal(QColor)
    screen_pick_requested = pyqtSignal()

    SWATCH_COLUMNS = 10
    SWATCH_SIZE = 26
    SWATCH_SPACING = 7
    H_MARGIN = 16

    _PRESETS = [
        "#000000", "#1F1F1F", "#4A4A4A", "#808080", "#C8C8C8", "#FFFFFF",
        "#7A1F1F", "#C62828", "#EF5350", "#FF8A80", "#F57C00", "#FFB74D",
        "#FBC02D", "#FFF176", "#388E3C", "#66BB6A", "#00897B", "#4DB6AC",
        "#1976D2", "#42A5F5", "#3949AB", "#7986CB", "#7B1FA2", "#BA68C8",
        "#D81B60", "#F06292", "#795548", "#A1887F", "#263238", "#607D8B",
    ]

    def __init__(self, current: QColor, saved_colors: list[str], title: str, t, parent=None):
        super().__init__(parent)
        self._t = t
        self._selected = QColor(current)
        self._saved_colors = list(saved_colors)
        self._swatches: list[_PaletteSwatch] = []
        self._recent_swatches: list[_PaletteSwatch] = []
        self._recent_layout: QGridLayout | None = None
        self._updating_inputs = False
        self._title = title
        self.setWindowTitle(title)
        self.setMinimumWidth(self._preferred_dialog_width())
        self._init_ui(saved_colors)
        self._set_selected_color(self._selected, emit=False)

    @property
    def color(self) -> QColor:
        return QColor(self._selected)

    def _init_ui(self, saved_colors: list[str]):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(self.H_MARGIN, 14, self.H_MARGIN, 14)

        root.addWidget(CaptionLabel(self._t("Palette"), self))
        self.color_field = _ColorField(self)
        root.addWidget(self.color_field)

        root.addWidget(CaptionLabel(self._t("Brightness"), self))
        self.brightness_slider = _BrightnessSlider(self)
        root.addWidget(self.brightness_slider)

        root.addWidget(CaptionLabel(self._t("Custom"), self))
        input_row = QHBoxLayout()
        input_row.setSpacing(14)

        self.current_preview = _CurrentColorPreview(self._selected, self)
        input_row.addWidget(self.current_preview)

        input_fields = QVBoxLayout()
        input_fields.setContentsMargins(0, 0, 0, 0)
        input_fields.setSpacing(8)

        hex_row = QHBoxLayout()
        hex_row.setContentsMargins(0, 0, 0, 0)
        hex_row.setSpacing(8)
        hex_label = CaptionLabel("HEX", self)
        hex_label.setFixedWidth(34)

        self.hex_input = LineEdit(self)
        self.hex_input.setFixedWidth(148)
        self.hex_input.setPlaceholderText("#000000")
        self.hex_input.setValidator(QRegularExpressionValidator(QRegularExpression("^#?[0-9A-Fa-f]{0,6}$"), self))
        hex_row.addWidget(hex_label)
        hex_row.addWidget(self.hex_input)
        self.screen_button = ToolButton(_create_eyedropper_icon(), self)
        self.screen_button.setFixedSize(32, 32)
        self.screen_button.setIconSize(QSize(16, 16))
        set_hover_hint(self.screen_button, self._t("Pick screen color"))
        hex_row.addWidget(self.screen_button)
        hex_row.addStretch(1)

        rgb_row = QHBoxLayout()
        rgb_row.setContentsMargins(0, 0, 0, 0)
        rgb_row.setSpacing(8)
        rgb_label = CaptionLabel("RGB", self)
        rgb_label.setFixedWidth(34)

        self.r_input = self._make_channel_input("R")
        self.g_input = self._make_channel_input("G")
        self.b_input = self._make_channel_input("B")
        rgb_row.addWidget(rgb_label)
        rgb_row.addWidget(self.r_input)
        rgb_row.addWidget(self.g_input)
        rgb_row.addWidget(self.b_input)
        rgb_row.addStretch(1)

        input_fields.addLayout(hex_row)
        input_fields.addLayout(rgb_row)
        input_row.addLayout(input_fields)
        input_row.addStretch(1)
        root.addLayout(input_row)

        self._add_swatch_section(root, self._t("Common"), self._PRESETS)
        recent = [_normalize_hex(c) for c in saved_colors if _normalize_hex(c)]
        root.addWidget(CaptionLabel(self._t("Recent"), self))
        self._recent_layout = self._make_swatch_grid(recent[:20], recent=True)
        root.addLayout(self._recent_layout)

        self.color_field.color_changed.connect(self._on_field_color_changed)
        self.brightness_slider.value_changed.connect(self._on_brightness_changed)
        self.hex_input.textChanged.connect(self._on_hex_changed)
        self.r_input.textChanged.connect(self._on_rgb_changed)
        self.g_input.textChanged.connect(self._on_rgb_changed)
        self.b_input.textChanged.connect(self._on_rgb_changed)
        self.screen_button.clicked.connect(self.screen_pick_requested.emit)

    def addWidget(self, widget: QWidget, stretch=0, align=Qt.AlignmentFlag.AlignLeft):
        self.layout().addWidget(widget, stretch, align)

    def _add_swatch_section(self, root: QVBoxLayout, label: str, colors: list[str]):
        root.addWidget(CaptionLabel(label, self))
        root.addLayout(self._make_swatch_grid(colors))

    def _preferred_dialog_width(self) -> int:
        swatch_width = self.SWATCH_COLUMNS * self.SWATCH_SIZE + (self.SWATCH_COLUMNS - 1) * self.SWATCH_SPACING
        return self.H_MARGIN * 2 + swatch_width

    def _make_swatch_grid(self, colors: list[str], recent: bool = False) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self.SWATCH_SPACING)
        grid.setVerticalSpacing(self.SWATCH_SPACING)
        for column in range(self.SWATCH_COLUMNS):
            grid.setColumnMinimumWidth(column, self.SWATCH_SIZE)
        self._fill_swatch_grid(grid, colors, recent=recent)
        return grid

    def _fill_swatch_grid(self, grid: QGridLayout, colors: list[str], recent: bool = False):
        for i, color in enumerate(colors):
            row = i // self.SWATCH_COLUMNS
            column = i % self.SWATCH_COLUMNS
            swatch = _PaletteSwatch(color, self)
            swatch.clicked.connect(self._on_swatch_clicked)
            self._swatches.append(swatch)
            if recent:
                self._recent_swatches.append(swatch)
            grid.addWidget(swatch, row, column)

    def update_saved_colors(self, colors: list[str]) -> None:
        """颜色被确认（记入最近使用）后刷新「最近使用」分组。"""
        self._saved_colors = list(colors)
        self._refresh_recent_swatches()

    def _refresh_recent_swatches(self):
        if self._recent_layout is None:
            return
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            if widget in self._swatches:
                self._swatches.remove(widget)
            if widget in self._recent_swatches:
                self._recent_swatches.remove(widget)
            delete_widget(widget)
        self._fill_swatch_grid(self._recent_layout, self._saved_colors[:20], recent=True)
        self._set_selected_color(self._selected, emit=False)

    def _make_channel_input(self, label: str) -> LineEdit:
        widget = LineEdit(self)
        widget.setFixedWidth(54)
        widget.setPlaceholderText(label)
        widget.setValidator(QIntValidator(0, 255, self))
        return widget

    def _on_swatch_clicked(self, hex_color: str):
        self._set_selected_color(QColor(hex_color))

    def _on_field_color_changed(self, color: QColor):
        self._set_selected_color(color, source="field")

    def _on_brightness_changed(self, value: float):
        self.color_field.set_brightness(value)

    def _on_hex_changed(self, text: str):
        if self._updating_inputs:
            return
        color = QColor(_normalize_hex(text) or "")
        if color.isValid():
            self._set_selected_color(color, source="hex")

    def _on_rgb_changed(self):
        if self._updating_inputs:
            return
        values = []
        for edit in (self.r_input, self.g_input, self.b_input):
            text = edit.text().strip()
            if text == "":
                return
            values.append(max(0, min(255, int(text))))
        self._set_selected_color(QColor(*values), source="rgb")

    def _set_selected_color(self, color: QColor, source: str | None = None, emit: bool = True):
        if not color.isValid():
            return
        next_color = QColor(color)
        hex_color = next_color.name().upper()
        if emit and hex_color == self._selected.name().upper():
            return
        self._selected = next_color
        rgb_text = self._t(
            "RGB: {r},{g},{b}",
            r=self._selected.red(),
            g=self._selected.green(),
            b=self._selected.blue(),
        )
        self.current_preview.set_color(self._selected)
        set_hover_hint(self.current_preview, f"{hex_color} | {rgb_text}")
        if source != "field":
            self.color_field.set_color(self._selected)
        if source != "brightness":
            self.brightness_slider.set_color(self._selected)
        for swatch in self._swatches:
            swatch.set_selected(swatch.hex_color.upper() == hex_color)

        self._updating_inputs = True
        try:
            if source != "hex":
                self.hex_input.setText(hex_color)
            if source != "rgb":
                self.r_input.setText(str(self._selected.red()))
                self.g_input.setText(str(self._selected.green()))
                self.b_input.setText(str(self._selected.blue()))
        finally:
            self._updating_inputs = False
        if emit:
            self.color_changed.emit(QColor(self._selected))

# ═══════════════════════════════════════════════════════════════
#  ColorPickerWidget
# ═══════════════════════════════════════════════════════════════

class ColorPickerWidget(QWidget):
    """可复用的颜色选择器组件，包含颜色按钮和常用颜色菜单。"""

    color_changed = pyqtSignal(str)  # 颜色变化时发出 hex 颜色值

    def __init__(self, dialog_title="Select color", default_color="#000000",
                 config_key="saved_colors", config_service=None, i18n_func=None,
                 parent=None):
        super().__init__(parent)
        self._dialog_title = dialog_title
        self._default_color = default_color
        self._current_color = default_color
        self._config_key = config_key
        self._config_service = config_service
        self._t = i18n_func or (lambda s, **kw: s)
        self._flyout = None
        self._palette_view = None
        self._pending_palette_color = None
        self._ignore_next_color_click = False
        self._screen_picker = None

        self._saved_colors = []
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._load_saved_colors()
        self._init_ui()
        self._connect_signals()

    def sizeHint(self) -> QSize:
        if hasattr(self, "color_button"):
            return self.color_button.sizeHint()
        return _ColorEntryButton.DEFAULT_SIZE

    def minimumSizeHint(self) -> QSize:
        if hasattr(self, "color_button"):
            return self.color_button.minimumSizeHint()
        return _ColorEntryButton.DEFAULT_SIZE

    # ── UI ────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.color_button = _ColorEntryButton(
            QColor(self._current_color),
            self,
        )
        set_hover_hint(self.color_button, self._t("Click to select color"))
        layout.addWidget(self.color_button, 1)

        self._apply_component_theme()
        self._update_color_tooltips(self._current_color)

    def _connect_signals(self):
        self.color_button.clicked.connect(self._on_color_clicked)

    # ── Public API ────────────────────────────────────────────────

    def set_color(self, hex_color: str):
        """设置当前颜色（更新按钮样式和 RGB 标签），不发射信号。"""
        self._current_color = hex_color
        self._apply_component_theme()
        self._update_color_tooltips(hex_color)

    def get_color(self) -> str:
        """获取当前颜色 hex 值。"""
        return self._current_color

    def reset(self, default_color: str | None = None):
        """重置为默认颜色，不发射信号。"""
        color = default_color or self._default_color
        self.set_color(color)

    def refresh_ui_texts(self):
        """语言切换时刷新按钮文本。"""
        self.refresh_theme()

    def refresh_theme(self):
        """主题切换时刷新组件自身和常用颜色菜单样式。"""
        self._apply_component_theme()
        self._update_color_tooltips(self._current_color)

    def _apply_component_theme(self):
        color = QColor(self._current_color)
        if color.isValid():
            self.color_button.setColor(color)
        self.update()

    # ── 颜色对话框 ───────────────────────────────────────────────

    def _on_color_clicked(self):
        if self._ignore_next_color_click:
            self._ignore_next_color_click = False
            return
        if self._flyout is not None:
            self._close_color_flyout()
            return
        self._open_color_flyout()

    def _open_color_flyout(self, initial_color: QColor | None = None):
        if initial_color is not None and initial_color.isValid():
            current = QColor(initial_color)
        else:
            current = QColor(self._current_color) if self._current_color else QColor("black")
        if self._flyout is not None:
            self._close_color_flyout()
            if initial_color is None:
                return

        view = _ColorPaletteView(
            current,
            self._saved_colors,
            self._t(self._dialog_title),
            self._t,
            self.window(),
        )
        self._palette_view = view
        self._pending_palette_color = None
        view.color_changed.connect(self._on_palette_color_changed)
        view.screen_pick_requested.connect(self._on_palette_screen_pick_requested)

        self._flyout = _show_color_flyout_above_target(
            view,
            target=self.color_button,
            parent=self.window(),
        )
        self.color_button.setChecked(True)
        self._flyout.closed.connect(self._on_palette_flyout_closed)

    def _close_color_flyout(self):
        if self._flyout is not None:
            self._flyout.close()
        else:
            self.color_button.setChecked(False)

    def _on_palette_color_changed(self, color: QColor):
        hex_color = color.name()
        self._pending_palette_color = hex_color
        self._apply_color(hex_color)

    def _on_palette_screen_pick_requested(self):
        self._close_color_flyout()
        self._launch_screen_pick(reopen_dialog=True)

    def _on_palette_flyout_closed(self):
        if self._should_suppress_next_color_click():
            self._suppress_next_color_click()
        if self._pending_palette_color:
            self._remember_color(self._pending_palette_color)
        self._pending_palette_color = None
        self._flyout = None
        self._palette_view = None
        self.color_button.setChecked(False)

    def _should_suppress_next_color_click(self) -> bool:
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            return False
        return self.color_button.rect().contains(self.color_button.mapFromGlobal(QCursor.pos()))

    def _suppress_next_color_click(self):
        self._ignore_next_color_click = True
        QTimer.singleShot(0, self._clear_suppressed_color_click_after_release)

    def _clear_suppressed_color_click_after_release(self):
        if not self._ignore_next_color_click:
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            QTimer.singleShot(30, self._clear_suppressed_color_click_after_release)
            return
        self._ignore_next_color_click = False

    def _launch_screen_pick(self, reopen_dialog: bool = False):
        """Start the custom full-screen picker from the Fluent color menu."""
        QTimer.singleShot(150, lambda: self._do_screen_pick(reopen_dialog=reopen_dialog))

    def _do_screen_pick(self, reopen_dialog: bool = False):
        picker = ScreenColorPicker()

        def on_picked(color):
            hex_color = color.name()
            self._apply_color(hex_color)
            self._remember_color(hex_color)
            if reopen_dialog:
                QTimer.singleShot(150, lambda: self._open_color_flyout(QColor(hex_color)))

        def on_cancel():
            if reopen_dialog:
                QTimer.singleShot(150, self._open_color_flyout)
            else:
                # 取消后要重新激活的是所属顶层窗口；
                # 对非窗口控件调 activateWindow/raise_ 无效
                window = self.window()
                if window is not None:
                    window.activateWindow()
                    window.raise_()

        picker.color_picked.connect(on_picked)
        picker.canceled.connect(on_cancel)
        # 保持引用防止被 GC；取色器 WA_DeleteOnClose，销毁时清引用防悬空
        picker.destroyed.connect(self._on_screen_picker_destroyed)
        self._screen_picker = picker
        picker.start()

    def _on_screen_picker_destroyed(self, _obj=None):
        self._screen_picker = None

    def _apply_color(self, hex_color: str):
        """应用颜色并发射信号。"""
        hex_color = _normalize_hex(hex_color) or hex_color
        self.set_color(hex_color)
        self.color_changed.emit(hex_color)

    def _remember_color(self, hex_color: str):
        """保存最近使用颜色并刷新菜单。"""
        normalized = _normalize_hex(hex_color)
        if not normalized:
            return
        self._saved_colors = [c for c in self._saved_colors if _normalize_hex(c) != normalized]
        self._saved_colors.insert(0, normalized)
        self._saved_colors = self._saved_colors[:20]
        self._persist_saved_colors()
        # 弹层还开着时同步刷新「最近使用」分组
        if self._palette_view is not None:
            self._palette_view.update_saved_colors(self._saved_colors)

    # ── RGB 标签 ──────────────────────────────────────────────────

    def _update_color_tooltips(self, hex_color: str):
        try:
            c = QColor(hex_color)
            rgb_text = f"{c.red()},{c.green()},{c.blue()}"
            tooltip = f"{c.name().upper()} | RGB: {rgb_text}"
            set_hover_hint(self.color_button, tooltip)
        except Exception:
            set_hover_hint(self.color_button, self._t("Click to select color"))

    # ── 持久化 ────────────────────────────────────────────────────

    def _load_saved_colors(self):
        if not self._config_service:
            return
        try:
            config = self._config_service.get_config()
            colors = getattr(config.app, self._config_key, None)
            if colors:
                normalized_colors = []
                for color in colors:
                    normalized = _normalize_hex(color)
                    if normalized and normalized not in normalized_colors:
                        normalized_colors.append(normalized)
                self._saved_colors = normalized_colors[:20]
            else:
                self._saved_colors = []
        except Exception as e:
            logger.warning(f"加载保存的颜色失败 ({self._config_key}): {e}")
            self._saved_colors = []

    def _persist_saved_colors(self):
        if not self._config_service:
            return
        try:
            self._config_service.update_config({
                'app': {
                    self._config_key: self._saved_colors
                }
            })
            self._config_service.save_config_file()
        except Exception as e:
            logger.error(f"保存颜色失败 ({self._config_key}): {e}")

def _show_color_flyout_above_target(view: FlyoutViewBase, target: QWidget, parent=None) -> Flyout:
    """算好位置后只走一次 exec 显示弹层。

    先 show() 再 exec() 会在左上角闪一帧并两次抢焦点。布局边距已收为 0，
    弹层矩形即内容矩形，无需再打 setMask（一次性 mask 在尺寸变化后会失效）。
    """
    flyout = Flyout(view, parent)
    # Keep the Fluent flyout animation, but remove the transparent shadow margin
    # from the mouse hit area so the popup only catches clicks on its content.
    flyout.hBoxLayout.setContentsMargins(0, 0, 0, 0)
    flyout.view.setGraphicsEffect(None)

    size = flyout.sizeHint()
    target_pos = target.mapToGlobal(QPoint(0, 0))
    x = target_pos.x() + target.width() // 2 - size.width() // 2
    y = target_pos.y() - size.height() - 6
    flyout.exec(QPoint(x, y), FlyoutAnimationType.PULL_UP)
    return flyout


def _normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return f"#{text.upper()}"


def _create_eyedropper_icon() -> QIcon:
    return themed_fluent_svg_icon("ic_fluent_eyedropper_24_regular.svg")


def _is_light_color(color: QColor) -> bool:
    return (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114) > 175


def _fluent_accent_color(alpha: int = 255) -> QColor:
    color = QColor(themeColor())
    color.setAlpha(max(0, min(255, alpha)))
    return color


def _fluent_surface_color() -> QColor:
    return QColor(43, 43, 43) if isDarkTheme() else QColor(255, 255, 255)


def _fluent_border_color() -> QColor:
    return QColor(255, 255, 255, 38) if isDarkTheme() else QColor(0, 0, 0, 34)


def _fluent_disabled_foreground() -> QColor:
    return QColor(255, 255, 255, 92) if isDarkTheme() else QColor(0, 0, 0, 92)
