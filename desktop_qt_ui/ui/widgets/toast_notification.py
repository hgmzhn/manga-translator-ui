"""
Toast notification helpers backed by qfluentwidgets InfoBar.
"""

import os
import platform
import subprocess

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon as FIF, InfoBar, InfoBarPosition, PushButton
from qfluentwidgets.components.widgets.info_bar import InfoBarManager


def _install_infobar_manager_safety_patches():
    """为 qfluentwidgets InfoBarManager 增加对已销毁 C++ 对象的容错防御。

    当父控件在隐藏状态下创建 InfoBar 并提前关闭时，Qt 在父控件后续展示时仍可能补发 showEvent，
    造成 InfoBarManager 列表中残留野指针并在调用 bar.height() 时引发 RuntimeError。
    在此主动清洗列表，保证即使外部控件生命周期异常，全局单例也永远只持有有效引用。
    """
    if getattr(InfoBarManager, "_safety_patches_installed", False):
        return
    InfoBarManager._safety_patches_installed = True

    orig_add = InfoBarManager.add

    def safe_add(self, infoBar):
        if not infoBar or sip.isdeleted(infoBar):
            return
        p = infoBar.parent()
        if not p or sip.isdeleted(p):
            return

        # 清洗已析构的旧父控件
        for parent_widget in list(self.infoBars.keys()):
            try:
                if sip.isdeleted(parent_widget):
                    self.infoBars.pop(parent_widget, None)
                    if hasattr(self, "aniGroups"):
                        self.aniGroups.pop(parent_widget, None)
            except Exception:
                pass

        if p not in self.infoBars:
            try:
                p.destroyed.connect(lambda _, pw=p: self._cleanup_parent(pw))
            except Exception:
                pass
            self.infoBars[p] = []
        else:
            self.infoBars[p] = [
                b for b in self.infoBars[p] if not sip.isdeleted(b)
            ]
        return orig_add(self, infoBar)

    def _cleanup_parent(self, parent_widget):
        try:
            self.infoBars.pop(parent_widget, None)
            if hasattr(self, "aniGroups"):
                self.aniGroups.pop(parent_widget, None)
        except Exception:
            pass

    InfoBarManager.add = safe_add
    InfoBarManager._cleanup_parent = _cleanup_parent

    orig_remove = InfoBarManager.remove

    def safe_remove(self, infoBar):
        if not infoBar or sip.isdeleted(infoBar):
            for bars in self.infoBars.values():
                if infoBar in bars:
                    try:
                        bars.remove(infoBar)
                    except ValueError:
                        pass
            return
        try:
            p = infoBar.parent()
            if not p or sip.isdeleted(p):
                for bars in self.infoBars.values():
                    if infoBar in bars:
                        try:
                            bars.remove(infoBar)
                        except ValueError:
                            pass
                return
            if p in self.infoBars:
                self.infoBars[p] = [
                    b for b in self.infoBars[p] if not sip.isdeleted(b)
                ]
            return orig_remove(self, infoBar)
        except Exception:
            pass

    InfoBarManager.remove = safe_remove

    orig_updateDropAni = InfoBarManager._updateDropAni

    def safe_updateDropAni(self, parent):
        if not parent or sip.isdeleted(parent):
            return
        if parent in self.infoBars:
            self.infoBars[parent] = [
                b for b in self.infoBars[parent] if not sip.isdeleted(b)
            ]
        return orig_updateDropAni(self, parent)

    InfoBarManager._updateDropAni = safe_updateDropAni

    for mgr in InfoBarManager.managers.values():
        cls = type(mgr)
        if hasattr(cls, "_pos"):
            orig_pos = cls._pos

            def make_safe_pos(old_pos):
                def safe_pos(self, infoBar, parentSize=None):
                    if not infoBar or sip.isdeleted(infoBar):
                        return
                    p = infoBar.parent()
                    if p and not sip.isdeleted(p) and p in self.infoBars:
                        self.infoBars[p] = [
                            b for b in self.infoBars[p] if not sip.isdeleted(b)
                        ]
                    return old_pos(self, infoBar, parentSize)

                return safe_pos

            cls._pos = make_safe_pos(orig_pos)


_install_infobar_manager_safety_patches()


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

    def show_info(self, message, duration=3000):
        """Show an informational notification."""
        self._clickable = False
        self._extra_data = None
        info_duration = duration if duration > 0 else -1
        self._bar = InfoBar.info(
            title="",
            content=str(message),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=info_duration,
            position=InfoBarPosition.BOTTOM,
            parent=self.parent,
        )
        self._bar.closedSignal.connect(self._on_bar_closed)
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

    def is_active(self) -> bool:
        """检查 Toast 是否处于活跃状态（尚未被关闭且底层 C++ 对象未被销毁）。"""
        if self._bar is None:
            return False
        try:
            return not sip.isdeleted(self._bar)
        except Exception:
            return False

    def isVisible(self):
        if self._bar is None:
            return False
        try:
            if sip.isdeleted(self._bar):
                self._bar = None
                return False
            return bool(self._bar.isVisible())
        except Exception:
            self._bar = None
            return False

    def height(self):
        if self._bar is None:
            return 0
        try:
            if sip.isdeleted(self._bar):
                self._bar = None
                return 0
            return self._bar.height()
        except Exception:
            self._bar = None
            return 0

    def y(self):
        if self._bar is None:
            return 0
        try:
            if sip.isdeleted(self._bar):
                self._bar = None
                return 0
            return self._bar.y()
        except Exception:
            self._bar = None
            return 0

    def fade_out(self):
        self.close()

    def close(self):
        if self._bar is not None:
            bar = self._bar
            self._bar = None
            try:
                if not sip.isdeleted(bar):
                    bar.hide()
                    bar.close()
                    # 避免在父控件后续展示时触发延迟派发的 showEvent
                    bar.setParent(None)
            except Exception:
                pass

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
        self.active_toasts = [toast for toast in self.active_toasts if toast.is_active()]

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
        self.active_toasts = [toast for toast in self.active_toasts if toast.is_active()]
        toast = ToastNotification(self.parent)
        toast.closed.connect(lambda t=toast: self._forget_toast(t))
        toast.show_info(message, duration)
        self.active_toasts.append(toast)
        return toast

    def close_all(self):
        """Close all active notifications."""
        toasts = list(self.active_toasts)
        self.active_toasts.clear()
        for toast in toasts:
            try:
                toast.close()
            except Exception:
                pass
