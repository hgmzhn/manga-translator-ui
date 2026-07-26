from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QWidget
from qfluentwidgets import CardWidget, FluentStyleSheet
from qframelesswindow import FramelessDialog


def normalize_dialog_parent(parent):
    """把任意控件父级归一化为其所属顶层窗口。

    Fluent 对话框是真正的顶层窗口。直接把嵌在堆叠页里的原生子控件
    （例如 MSFluentWindow 里的页面）当 transient parent，会让 Qt 使用
    非顶层的 QWidgetWindow，导致定位/模态异常。parent 无效或为 None
    时回退到当前活动窗口。
    """
    candidate = parent if isinstance(parent, QWidget) else QApplication.activeWindow()
    if candidate is None:
        return None
    top_level = candidate.window()
    return top_level if top_level is not None else candidate


class FluentSecondaryDialog(FramelessDialog):
    """Shared Fluent shell for secondary dialogs.

    - 父级自动归一化到顶层窗口（parent=None 时回退 activeWindow）；
    - 默认 TitleBar 隐藏，但按住背景空白区/纯展示控件可拖动窗口
      （startSystemMove，无边框窗口拖动的正规做法）；
    - 首次 show 前把最小尺寸/初始尺寸夹到屏幕可用区域的 90% 以内，
      并把窗口位置兜回工作区，保证任何屏幕/缩放下按钮不出屏。
    """

    _SCREEN_CLAMP_RATIO = 0.9

    def __init__(self, parent=None):
        super().__init__(normalize_dialog_parent(parent))
        self._screen_clamped = False
        self.titleBar.setVisible(False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContentsMargins(0, 0, 0, 0)
        self.apply_fluent_dialog_style()

    def apply_fluent_dialog_style(self):
        FluentStyleSheet.DIALOG.apply(self)

    # ─── 无边框窗口拖动 ─────────────────────────────────────
    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._is_drag_region(event.position().toPoint())
        ):
            window = self.windowHandle()
            if window is not None and window.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def _is_drag_region(self, pos: QPoint) -> bool:
        """点在背景/纯展示控件上才允许拖动，交互控件保持原有行为。

        从命中控件沿父链走到对话框本身，途中出现任何交互控件
        （按钮、输入框、树视图等）即判定为非拖动区。
        """
        widget = self.childAt(pos)
        while widget is not None and widget is not self:
            if not self._is_passive_widget(widget):
                return False
            widget = widget.parentWidget()
        return True

    @staticmethod
    def _is_passive_widget(widget: QWidget) -> bool:
        if isinstance(widget, QLabel):
            interactive_flags = (
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            return not (widget.textInteractionFlags() & interactive_flags)
        if isinstance(widget, CardWidget):
            return not widget.isClickEnabled()
        # 纯布局容器：type 精确匹配，避免把 QWidget/QFrame 的交互子类误判为背景
        return type(widget) in (QWidget, QFrame)

    # ─── 屏幕尺寸夹取 ──────────────────────────────────────
    def showEvent(self, event):
        if not self._screen_clamped:
            self._screen_clamped = True
            self._clamp_to_screen()
        super().showEvent(event)

    def _clamp_to_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_w = max(int(available.width() * self._SCREEN_CLAMP_RATIO), 320)
        max_h = max(int(available.height() * self._SCREEN_CLAMP_RATIO), 240)

        minimum = self.minimumSize()
        if minimum.width() > max_w or minimum.height() > max_h:
            self.setMinimumSize(min(minimum.width(), max_w), min(minimum.height(), max_h))
        if self.width() > max_w or self.height() > max_h:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))

        # 位置兜底：避免整窗生成在工作区外（QDialog 随后仍可能按父窗口居中，
        # 但那时窗口尺寸已被夹取，居中结果必然在屏内）。
        geo = self.geometry()
        x = min(max(geo.x(), available.left()), max(available.right() - geo.width() + 1, available.left()))
        y = min(max(geo.y(), available.top()), max(available.bottom() - geo.height() + 1, available.top()))
        if x != geo.x() or y != geo.y():
            self.move(x, y)


DialogCode = QDialog.DialogCode
