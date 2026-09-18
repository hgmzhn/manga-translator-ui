"""Standalone Agent debug shell, independent of the translation MainWindow."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow

from ui.agent.request_debug_page import RequestDebugPage
from ui.agent.render_preview_page import RenderPreviewPage
from ui.main_page.pages.chat_page import ChatPage


class AgentDebugWindow(FluentWindow):
    """Compose existing chat with independently registered debug pages."""

    shutdown_finished = pyqtSignal()

    def __init__(self, config_service, i18n):
        super().__init__()
        self.setWindowTitle("Agent Debug UI")
        self.resize(1280, 850)
        self.setMinimumSize(850, 650)
        self.navigationInterface.setExpandWidth(220)
        self.navigationInterface.setReturnButtonVisible(False)
        self._closing = False
        self._can_close = False
        self.chat_page = ChatPage(i18n.translate, config_service=config_service,
                                  show_request_body=False, debug_context=True)
        self.request_page = RequestDebugPage(i18n.translate)
        self.render_page = RenderPreviewPage(i18n.translate)
        self.register_page("chat", self.chat_page, FIF.MESSAGE, i18n.translate("Chat"))
        self.register_page(
            "request", self.request_page, FIF.CODE, i18n.translate("Agent context debug")
        )
        self.register_page("render", self.render_page, FIF.PHOTO, i18n.translate("Agent render preview"))
        self.chat_page.debug_event_received.connect(
            self.request_page.add_event, Qt.ConnectionType.QueuedConnection
        )
        self.render_page.image_ready.connect(self.chat_page.set_current_image_context)
        self.render_page.workspace_ready.connect(self.chat_page.set_tool_context)
        self.chat_page.canvas_updated.connect(self.render_page.show_canvas)
        self.shutdown_finished.connect(self._finish_close, Qt.ConnectionType.QueuedConnection)
        self.switchTo(self.chat_page)

    def register_page(self, key, page, icon, title):
        """One registration point for additional Agent debugging surfaces."""
        page.setObjectName(f"agent_debug_{key}")
        self.addSubInterface(page, icon, title)

    def shutdown(self):
        self.chat_page.shutdown()
        return self.render_page.shutdown()

    def load_file(self, path):
        self.switchTo(self.render_page)
        self.render_page.load_file(path)

    def _finish_close(self):
        self._can_close = True
        self.close()

    def closeEvent(self, event):
        if self._can_close:
            super().closeEvent(event)
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            future = self.shutdown()
            future.add_done_callback(lambda _: self.shutdown_finished.emit())
