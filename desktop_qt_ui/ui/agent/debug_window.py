"""Standalone Agent debug shell, independent of the translation MainWindow."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow

from ui.agent.request_debug_page import RequestDebugPage
from ui.main_page.pages.chat_page import ChatPage


class AgentDebugWindow(FluentWindow):
    """Compose existing chat with independently registered debug pages."""

    def __init__(self, config_service, i18n):
        super().__init__()
        self.setWindowTitle("Agent Debug UI")
        self.resize(1280, 850)
        self.setMinimumSize(850, 650)
        self.navigationInterface.setExpandWidth(220)
        self.navigationInterface.setReturnButtonVisible(False)
        self.chat_page = ChatPage(i18n.translate, config_service=config_service)
        self.request_page = RequestDebugPage(i18n.translate)
        self.register_page("chat", self.chat_page, FIF.MESSAGE, i18n.translate("Chat"))
        self.register_page(
            "request", self.request_page, FIF.CODE, i18n.translate("Chat request body")
        )
        self.chat_page.request_body_received.connect(
            self.request_page.set_request_body, Qt.ConnectionType.QueuedConnection
        )
        self.switchTo(self.chat_page)

    def register_page(self, key, page, icon, title):
        """One registration point for additional Agent debugging surfaces."""
        page.setObjectName(f"agent_debug_{key}")
        self.addSubInterface(page, icon, title)

    def shutdown(self):
        self.chat_page.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
