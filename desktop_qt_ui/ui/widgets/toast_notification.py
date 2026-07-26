"""
Toast notification helpers backed by qfluentwidgets InfoBar.
"""

import os
import platform
import subprocess

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon as FIF, InfoBar, InfoBarPosition, PushButton


class ToastNotification(QObject):
    """Thin compatibility wrapper around qfluentwidgets InfoBar."""

    clicked = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.parent = parent
        self._bar: InfoBar | None = None
        self._extra_data: str | None = None
        self._clickable = False

    def show_toast(self, message, duration=3000, success=True, clickable_path=None):
        """
        Show a non-blocking notification.

        duration <= 0 keeps the InfoBar visible until closed explicitly.
        """
        self._clickable = clickable_path is not None
        self._extra_data = clickable_path
        info_duration = duration if duration > 0 else -1
        content = str(message)
        if self._clickable:
            content = f"{content}\n点击打开所在文件夹"

        factory = InfoBar.success if success else InfoBar.error
        self._bar = factory(
            title="",
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=info_duration,
            position=InfoBarPosition.BOTTOM,
            parent=self.parent,
        )
        self._bar.closedSignal.connect(self._on_bar_closed)

        if self._clickable:
            self._bar.setCursor(Qt.CursorShape.PointingHandCursor)
            self._bar.installEventFilter(self)
            open_button = PushButton("打开", self._bar, FIF.FOLDER)
            open_button.clicked.connect(self._open_extra_location)
            self._bar.addWidget(open_button)

        return self

    def eventFilter(self, obj, event):
        # 在 release 而不是 press 处理点击：press 就 close 会让后续事件
        # 继续派发给正在销毁的 InfoBar。处理后 return True 终止派发。
        if obj is self._bar and event.type() == QEvent.Type.MouseButtonRelease:
            if self._clickable and self._extra_data:
                self._open_extra_location()
                return True
        return super().eventFilter(obj, event)

    def _open_extra_location(self):
        if not self._extra_data:
            return
        self.open_file_location(self._extra_data)
        self.clicked.emit(self._extra_data)
        self.close()

    def _on_bar_closed(self):
        self._bar = None
        self.closed.emit()

    def isVisible(self):
        return bool(self._bar and self._bar.isVisible())

    def height(self):
        return self._bar.height() if self._bar else 0

    def y(self):
        return self._bar.y() if self._bar else 0

    def fade_out(self):
        self.close()

    def close(self):
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    @staticmethod
    def open_file_location(file_path):
        """Open the containing folder and select the file when supported."""
        if not os.path.exists(file_path):
            return

        system = platform.system()

        try:
            if system == "Windows":
                subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
            elif system == "Darwin":
                subprocess.run(["open", "-R", file_path])
            else:
                subprocess.run(["xdg-open", os.path.dirname(file_path)])
        except Exception as e:
            print(f"无法打开文件位置: {e}")


class ToastManager:
    """Toast manager that tracks active InfoBars."""

    def __init__(self, parent):
        self.parent = parent
        self.active_toasts: list[ToastNotification] = []

    def show_toast(self, message, duration=3000, success=True, clickable_path=None):
        """Show a notification."""
        self.active_toasts = [toast for toast in self.active_toasts if toast.isVisible()]

        toast = ToastNotification(self.parent)
        toast.closed.connect(lambda t=toast: self._forget_toast(t))
        toast.show_toast(message, duration, success, clickable_path)
        self.active_toasts.append(toast)

        return toast

    def _forget_toast(self, toast: ToastNotification):
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)

    def show_success(self, message, duration=3000, clickable_path=None):
        """Show a success notification."""
        return self.show_toast(message, duration, True, clickable_path)

    def show_error(self, message, duration=3000):
        """Show an error notification."""
        return self.show_toast(message, duration, False, None)

    def show_info(self, message, duration=3000):
        """Show an informational notification."""
        self.active_toasts = [toast for toast in self.active_toasts if toast.isVisible()]
        toast = ToastNotification(self.parent)
        toast.closed.connect(lambda t=toast: self._forget_toast(t))
        toast._bar = InfoBar.info(
            title="",
            content=str(message),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=duration if duration > 0 else -1,
            position=InfoBarPosition.BOTTOM,
            parent=self.parent,
        )
        toast._bar.closedSignal.connect(toast._on_bar_closed)
        self.active_toasts.append(toast)
        return toast

    def close_all(self):
        """Close all active notifications."""
        for toast in list(self.active_toasts):
            if toast.isVisible():
                toast.close()
        self.active_toasts.clear()
