
import logging

from ui.theme import get_current_theme_colors
from PyQt6.QtCore import QRect, QRegularExpression, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QIntValidator,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ColorPickerButton,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    DropDownToolButton,
    FluentIcon as FIF,
    RoundMenu,
)
from ui.secondary_pages.fluent_dialog import DialogCode, FluentSecondaryDialog
from ui.widgets.hover_hint import set_hover_hint

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
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)

        self._color = QColor(0, 0, 0)
        self._mpos = QCursor.pos()
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
        self.show()
        self.activateWindow()
        self.raise_()

    # ── 内部 ──────────────────────────────────────────────────

    def _px_color(self, lx, ly):
        if self._img is None:
            return QColor(0, 0, 0)
        px, py = int(lx * self._dpr), int(ly * self._dpr)
        if 0 <= px < self._img.width() and 0 <= py < self._img.height():
            return self._img.pixelColor(px, py)
        return QColor(0, 0, 0)

    # ── 绘制 ──────────────────────────────────────────────────

    def paintEvent(self, _event):
        if self._shot is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(self.rect(), self._shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 15))

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

    def mouseMoveEvent(self, ev):
        self._mpos = ev.globalPosition().toPoint()
        loc = self.mapFromGlobal(self._mpos)
        self._color = self._px_color(loc.x(), loc.y())
        self.update()

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


class _ColorSwatchButton(ColorPickerButton):
    """Fluent color swatch whose click is handled by ColorPickerWidget."""

    def __init__(self, color: QColor, title: str, parent=None):
        super().__init__(color, title, parent, enableAlpha=False)
        try:
            self.clicked.disconnect()
        except TypeError:
            pass


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
            p.setPen(QPen(QColor(get_current_theme_colors().get("btn_primary_bg", "#0F6CBD")), 2))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.hex_color)


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
        self._hue = (x - rect.left()) / max(1, rect.width())
        self._saturation = 1.0 - (y - rect.top()) / max(1, rect.height())
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
        c = get_current_theme_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -2, -2)
        p.drawImage(rect, self._base_image(rect.width(), rect.height()))
        # HSV 的 V 是 RGB 线性缩放，等价于叠加 alpha=(1-V) 的黑色
        if self._value < 1.0:
            p.fillRect(rect, QColor(0, 0, 0, round((1.0 - self._value) * 255)))
        p.setPen(QPen(QColor(c.get("border_input", "#d1d1d1")), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 7, 7)

        x = rect.left() + self._hue * rect.width()
        y = rect.top() + (1.0 - self._saturation) * rect.height()
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
        self._value = (max(rect.left(), min(rect.right(), int(pos.x()))) - rect.left()) / max(1, rect.width())
        self.update()
        self.value_changed.emit(self._value)

    def paintEvent(self, _event):
        c = get_current_theme_colors()
        rect = self.rect().adjusted(3, 8, -3, -8)
        end_color = QColor.fromHsvF(self._hue, self._saturation, 1.0)
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0))
        gradient.setColorAt(1.0, end_color)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(c.get("border_input", "#d1d1d1")), 1))
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


class _ColorPaletteDialog(FluentSecondaryDialog):
    """Focused replacement for the stock color dialog used in the editor panel."""

    saved_color = pyqtSignal(QColor)

    SCREEN_PICK = 100

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
        self.setModal(True)
        self.setMinimumWidth(560)
        self._init_ui(saved_colors)
        self._apply_dialog_theme()
        self._set_selected_color(self._selected)

    @property
    def color(self) -> QColor:
        return QColor(self._selected)

    def _init_ui(self, saved_colors: list[str]):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(34, 24, 34, 18)

        title = BodyLabel(self._title, self)
        title.setObjectName("colorPickerTitle")
        root.addWidget(title)

        root.addWidget(CaptionLabel(self._t("Palette"), self))
        self.color_field = _ColorField(self)
        root.addWidget(self.color_field)

        root.addWidget(CaptionLabel(self._t("Brightness"), self))
        self.brightness_slider = _BrightnessSlider(self)
        root.addWidget(self.brightness_slider)

        root.addWidget(CaptionLabel(self._t("Custom"), self))
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.hex_input = LineEdit(self)
        self.hex_input.setFixedWidth(92)
        self.hex_input.setPlaceholderText("#000000")
        self.hex_input.setValidator(QRegularExpressionValidator(QRegularExpression("^#?[0-9A-Fa-f]{0,6}$"), self))
        input_row.addWidget(self.hex_input)

        self.r_input = self._make_channel_input("R")
        self.g_input = self._make_channel_input("G")
        self.b_input = self._make_channel_input("B")
        input_row.addWidget(self.r_input)
        input_row.addWidget(self.g_input)
        input_row.addWidget(self.b_input)
        root.addLayout(input_row)

        self._add_swatch_section(root, self._t("Common"), self._PRESETS)
        recent = [_normalize_hex(c) for c in saved_colors if _normalize_hex(c)]
        root.addWidget(CaptionLabel(self._t("Recent"), self))
        self._recent_layout = self._make_swatch_grid(recent[:20], recent=True)
        root.addLayout(self._recent_layout)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.screen_button = PushButton(self._t("Screen"), self)
        self.screen_button.setIcon(FIF.PALETTE)
        self.save_button = PushButton(self._t("Save"), self)
        self.save_button.setIcon(FIF.SAVE)
        self.cancel_button = PushButton(self._t("Cancel"), self)
        self.ok_button = PrimaryPushButton(self._t("OK"), self)
        button_row.addWidget(self.screen_button)
        button_row.addWidget(self.save_button)
        button_row.addStretch()
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.ok_button)
        root.addLayout(button_row)

        self.color_field.color_changed.connect(self._on_field_color_changed)
        self.brightness_slider.value_changed.connect(self._on_brightness_changed)
        self.hex_input.textChanged.connect(self._on_hex_changed)
        self.r_input.textChanged.connect(self._on_rgb_changed)
        self.g_input.textChanged.connect(self._on_rgb_changed)
        self.b_input.textChanged.connect(self._on_rgb_changed)
        self.screen_button.clicked.connect(lambda: self.done(self.SCREEN_PICK))
        self.save_button.clicked.connect(self._save_current_color)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)

    def _add_swatch_section(self, root: QVBoxLayout, label: str, colors: list[str]):
        root.addWidget(CaptionLabel(label, self))
        root.addLayout(self._make_swatch_grid(colors))

    def _make_swatch_grid(self, colors: list[str], recent: bool = False) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        self._fill_swatch_grid(grid, colors, recent=recent)
        return grid

    def _fill_swatch_grid(self, grid: QGridLayout, colors: list[str], recent: bool = False):
        for i, color in enumerate(colors):
            swatch = _PaletteSwatch(color, self)
            swatch.clicked.connect(self._on_swatch_clicked)
            self._swatches.append(swatch)
            if recent:
                self._recent_swatches.append(swatch)
            grid.addWidget(swatch, i // 10, i % 10)

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
            widget.deleteLater()
        self._fill_swatch_grid(self._recent_layout, self._saved_colors[:20], recent=True)
        self._set_selected_color(self._selected)

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

    def _save_current_color(self):
        hex_color = self._selected.name().upper()
        self._saved_colors = [c for c in self._saved_colors if c.upper() != hex_color]
        self._saved_colors.insert(0, hex_color)
        self._saved_colors = self._saved_colors[:20]
        self._refresh_recent_swatches()
        self.saved_color.emit(QColor(self._selected))

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

    def _set_selected_color(self, color: QColor, source: str | None = None):
        if not color.isValid():
            return
        self._selected = QColor(color)
        hex_color = self._selected.name().upper()
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

    def _apply_dialog_theme(self):
        c = get_current_theme_colors()
        self.setStyleSheet(self.styleSheet() + f"""
            #colorPickerTitle {{
                color: {c.get("text_primary", "#1f1f1f")};
                font-weight: 600;
            }}
        """)


# ═══════════════════════════════════════════════════════════════
#  ColorPickerWidget
# ═══════════════════════════════════════════════════════════════

class ColorPickerWidget(CardWidget):
    """可复用的颜色选择器组件，包含颜色按钮和常用颜色菜单。"""

    color_changed = pyqtSignal(str)  # 颜色变化时发出 hex 颜色值

    # 类级别的颜色剪贴板，所有实例共享
    _color_clipboard = None

    def __init__(self, dialog_title="Select color", default_color="#000000",
                 config_key="saved_colors", config_service=None, i18n_func=None,
                 parent=None):
        super().__init__(parent)
        self.setBorderRadius(8)
        self._dialog_title = dialog_title
        self._default_color = default_color
        self._current_color = default_color
        self._config_key = config_key
        self._config_service = config_service
        self._t = i18n_func or (lambda s, **kw: s)

        self._saved_colors = []
        self._load_saved_colors()
        self._init_ui()
        self._connect_signals()

    # ── UI ────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 主颜色按钮
        self.color_button = _ColorSwatchButton(
            QColor(self._current_color),
            self._t(self._dialog_title),
            self,
        )
        self.color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_button.setFixedSize(42, 32)
        set_hover_hint(self.color_button, self._t("Click to select color"))
        layout.addWidget(self.color_button, 0)

        # ★ 常用颜色按钮
        self.saved_colors_button = DropDownToolButton(FIF.PALETTE, self)
        set_hover_hint(self.saved_colors_button, self._t("Saved colors menu"))
        self.saved_colors_button.setFixedSize(36, 32)
        layout.addWidget(self.saved_colors_button, 0)

        self._apply_component_theme()
        self._update_color_tooltips(self._current_color)
        self._rebuild_saved_colors_menu()

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
        self._rebuild_saved_colors_menu()

    def _apply_component_theme(self):
        color = QColor(self._current_color)
        if color.isValid():
            self.color_button.setColor(color)
        self.update()

    # ── 颜色对话框 ───────────────────────────────────────────────

    def _on_color_clicked(self):
        current = QColor(self._current_color) if self._current_color else QColor("black")

        dialog = _ColorPaletteDialog(
            current,
            self._saved_colors,
            self._t(self._dialog_title),
            self._t,
            self.window(),
        )
        dialog.saved_color.connect(lambda color: self._remember_color(color.name()))

        result = dialog.exec()
        if result == DialogCode.Accepted:
            hex_color = dialog.color.name()
            self._apply_color(hex_color)
            self._remember_color(hex_color)
        elif result == _ColorPaletteDialog.SCREEN_PICK:
            self._launch_screen_pick()

    def _launch_screen_pick(self):
        """Start the custom full-screen picker from the Fluent color menu."""
        QTimer.singleShot(150, self._do_screen_pick)

    def _do_screen_pick(self):
        picker = ScreenColorPicker()

        def on_picked(color):
            hex_color = color.name()
            self._apply_color(hex_color)
            self._remember_color(hex_color)

        def on_cancel():
            self.activateWindow()
            self.raise_()

        picker.color_picked.connect(on_picked)
        picker.canceled.connect(on_cancel)
        # 保持引用防止被 GC
        self._screen_picker = picker
        picker.start()

    def _apply_color(self, hex_color: str):
        """应用颜色并发射信号。"""
        self.set_color(hex_color)
        self.color_changed.emit(hex_color)

    def _remember_color(self, hex_color: str):
        """保存最近使用颜色并刷新菜单。"""
        if hex_color not in self._saved_colors:
            self._saved_colors.insert(0, hex_color)
            if len(self._saved_colors) > 20:
                self._saved_colors = self._saved_colors[:20]
            self._persist_saved_colors()
            self._rebuild_saved_colors_menu()

    # ── 复制 / 粘贴 ──────────────────────────────────────────────

    def _on_copy(self):
        if self._current_color:
            ColorPickerWidget._color_clipboard = self._current_color
            for widget in self._all_instances():
                widget._rebuild_saved_colors_menu()

    def _on_paste(self):
        if ColorPickerWidget._color_clipboard:
            self._apply_color(ColorPickerWidget._color_clipboard)

    def _all_instances(self):
        """获取同一父层级中所有 ColorPickerWidget 实例。"""
        top = self.window()
        if top:
            return top.findChildren(ColorPickerWidget)
        return [self]

    # ── RGB 标签 ──────────────────────────────────────────────────

    def _update_color_tooltips(self, hex_color: str):
        try:
            c = QColor(hex_color)
            rgb_text = f"{c.red()},{c.green()},{c.blue()}"
            tooltip = f"{c.name().upper()} | RGB: {rgb_text}"
            set_hover_hint(self.color_button, tooltip)
            set_hover_hint(self.saved_colors_button, tooltip)
        except Exception:
            set_hover_hint(self.color_button, self._t("Click to select color"))
            set_hover_hint(self.saved_colors_button, self._t("Saved colors menu"))

    # ── 常用颜色菜单 ─────────────────────────────────────────────

    def _rebuild_saved_colors_menu(self):
        menu = RoundMenu(parent=self)

        current_color = QColor(self._current_color) if self._current_color else QColor()
        if current_color.isValid():
            current_action = Action(self)
            current_action.setEnabled(False)
            current_action.setIcon(self._create_color_icon(current_color.name()))
            current_action.setText(
                f"{current_color.name().upper()}  (R:{current_color.red()} G:{current_color.green()} B:{current_color.blue()})"
            )
            menu.addAction(current_action)
            menu.addSeparator()

        copy_action = Action(FIF.COPY, self._t("Copy current color"), self)
        copy_action.triggered.connect(self._on_copy)
        menu.addAction(copy_action)

        paste_action = Action(FIF.PASTE, self._t("Paste copied color"), self)
        paste_action.setEnabled(ColorPickerWidget._color_clipboard is not None)
        paste_action.triggered.connect(self._on_paste)
        menu.addAction(paste_action)

        pick_action = Action(FIF.PALETTE, self._t("Pick screen color"), self)
        pick_action.triggered.connect(self._launch_screen_pick)
        menu.addAction(pick_action)
        menu.addSeparator()

        if self._saved_colors:
            for color_hex in self._saved_colors:
                action = Action(self)
                action.setIcon(self._create_color_icon(color_hex))
                c = QColor(color_hex)
                action.setText(f"{color_hex}  (R:{c.red()} G:{c.green()} B:{c.blue()})")
                action.triggered.connect(lambda checked, ch=color_hex: self._apply_color(ch))
                menu.addAction(action)
            menu.addSeparator()

        save_action = Action(FIF.SAVE, self._t("Save current color"), self)
        save_action.triggered.connect(self._save_current_color)
        menu.addAction(save_action)

        if self._saved_colors:
            clear_action = Action(FIF.DELETE, self._t("Clear saved colors"), self)
            clear_action.triggered.connect(self._clear_saved_colors)
            menu.addAction(clear_action)

        self.saved_colors_button.setMenu(menu)

    def _save_current_color(self):
        if self._current_color:
            self._remember_color(self._current_color)

    def _clear_saved_colors(self):
        self._saved_colors = []
        self._persist_saved_colors()
        self._rebuild_saved_colors_menu()

    # ── 持久化 ────────────────────────────────────────────────────

    def _load_saved_colors(self):
        if not self._config_service:
            return
        try:
            config = self._config_service.get_config()
            colors = getattr(config.app, self._config_key, None)
            if colors:
                self._saved_colors = list(colors)
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

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _create_color_icon(hex_color: str) -> QIcon:
        c = get_current_theme_colors()
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(hex_color))
        painter = QPainter(pixmap)
        painter.setPen(QColor(c["border_input"]))
        painter.drawRect(0, 0, 15, 15)
        painter.end()
        return QIcon(pixmap)


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


def _is_light_color(color: QColor) -> bool:
    return (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114) > 175
