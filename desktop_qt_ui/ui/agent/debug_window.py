"""Standalone Agent debug shell, independent of the translation MainWindow."""

from __future__ import annotations

from concurrent.futures import Future
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow

from ui.agent.request_debug_page import RequestDebugPage
from ui.agent.render_preview_page import RenderPreviewPage
from ui.agent.workspace_panel import AgentWorkspacePanel
from ui.main_page.pages.chat_page import ChatPage


class AgentDebugWindow(FluentWindow):
    """Compose existing chat with independently registered debug pages."""

    shutdown_finished = pyqtSignal()

    def __init__(self, config_service, i18n):
        super().__init__()
        self.setWindowTitle("Agent Debug UI")
        self.resize(1360, 900)
        self.setMinimumSize(1000, 700)
        self.navigationInterface.setExpandWidth(220)
        self.navigationInterface.setReturnButtonVisible(False)
        self._closing = False
        self._can_close = False
        self._shutdown_future = None
        self._selected_agent = "manager"
        self.chat_page = ChatPage(i18n.translate, config_service=config_service,
                                  show_request_body=False, debug_context=True)
        self.request_page = RequestDebugPage(i18n.translate)
        self.request_page.set_external_navigation(True)
        self.render_page = RenderPreviewPage(
            i18n.translate, config_service=config_service
        )
        self.workspace_panel = AgentWorkspacePanel(i18n.translate, self)
        self.register_page("chat", self.chat_page, FIF.MESSAGE, i18n.translate("Chat"))
        self.register_page(
            "request", self.request_page, FIF.CODE, i18n.translate("Agent context debug")
        )
        self.register_page("render", self.render_page, FIF.PHOTO, i18n.translate("Agent render preview"))
        # Keep the debug workspace inside FluentWindow's own layout. Native
        # QDockWidget ownership conflicts with FluentWindow's custom window
        # lifecycle on some Windows Qt builds.
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_panel.setMinimumWidth(280)
        self.workspace_panel.setMaximumWidth(460)
        self.widgetLayout.takeAt(0)
        self.workspace_splitter.addWidget(self.stackedWidget)
        self.workspace_splitter.addWidget(self.workspace_panel)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setHandleWidth(4)
        self.workspace_splitter.setSizes([970, 320])
        self.widgetLayout.addWidget(self.workspace_splitter)
        self.chat_page.debug_event_received.connect(
            self._receive_debug_event, Qt.ConnectionType.QueuedConnection
        )
        self.stackedWidget.currentChanged.connect(self._sync_sidebar)
        self.workspace_panel.agent_selected.connect(self._select_agent)
        self.workspace_panel.agent_details_changed.connect(self._refresh_agent)
        self.workspace_panel.event_selected.connect(self.request_page.select_event)
        self.request_page.follow.toggled.connect(self.workspace_panel.log_tabs.set_follow)
        self.workspace_panel.log_tabs.follow_changed.connect(self.request_page.follow.setChecked)
        self.request_page.records_cleared.connect(self.workspace_panel.log_tabs.clear_views)
        self.workspace_panel.page_selected.connect(self.render_page.select_page)
        self.render_page.page_selected.connect(self.workspace_panel.files.select_page)
        self.chat_page.agent_detail.open_logs.connect(self._open_agent_logs)
        self.chat_page.agent_detail.open_page.connect(self._open_agent_page)
        self.chat_page.conversation_cleared.connect(self._clear_session)
        self.render_page.image_ready.connect(self.chat_page.set_current_image_context)
        self.render_page.workspace_ready.connect(self.chat_page.set_tool_context)
        self.render_page.pages_ready.connect(
            self.workspace_panel.set_files, Qt.ConnectionType.QueuedConnection
        )
        self.chat_page.canvas_updated.connect(self.render_page.show_canvas)
        self.shutdown_finished.connect(self._finish_close, Qt.ConnectionType.QueuedConnection)
        self.switchTo(self.chat_page)
        self._sync_sidebar()

    def register_page(self, key, page, icon, title):
        """One registration point for additional Agent debugging surfaces."""
        page.setObjectName(f"agent_debug_{key}")
        self.addSubInterface(page, icon, title)

    def _sync_sidebar(self, *_):
        current = self.stackedWidget.currentWidget()
        page = "request" if current is self.request_page else "render" if current is self.render_page else "chat"
        self.workspace_panel.set_active_page(page)

    def _select_agent(self, key):
        self._selected_agent = key
        self.chat_page.show_agent(key, self.workspace_panel.selected_agent_details())

    def _refresh_agent(self, key):
        if key == self._selected_agent and key not in {"manager", "page"}:
            self.chat_page.show_agent(key, self.workspace_panel.selected_agent_details())

    def _open_agent_logs(self, key):
        self.switchTo(self.request_page)
        self.workspace_panel.log_tabs.select_view(key)

    def _open_agent_page(self, page):
        self.switchTo(self.render_page)
        self.render_page.select_page(page)

    def _receive_debug_event(self, event):
        if self._closing or not isinstance(event, dict):
            return
        if event.get("_ui_epoch", self.chat_page.debug_epoch) != self.chat_page.debug_epoch:
            return
        data = event.get("data") or {}
        parent = data.get("parent_task_id")
        context = self.chat_page._tool_context
        if parent and (context is None or parent != context.task_id):
            return
        self.request_page.add_event(event)
        self.workspace_panel.add_event(event)
        if event.get("kind") == "task_status" and data.get("status") in {"completed", "failed", "conflict", "cancelled"}:
            pages = data.get("pages") or [data.get("page") or {}]
            selected = self.workspace_panel.files.current_page
            if selected and any(page.get("id") == selected.get("id") for page in pages):
                self.render_page.refresh_selected_page()

    def _clear_session(self):
        self.workspace_panel.clear(preserve_files=True)
        self.request_page._clear_records()
        self._selected_agent = "manager"
        self.chat_page.show_agent("manager")

    def shutdown(self):
        if self._shutdown_future is not None:
            return self._shutdown_future
        joined = self._shutdown_future = Future()
        pending = [future for future in (self.chat_page.shutdown(), self.render_page.shutdown()) if future is not None]

        def complete(_=None):
            if joined.done() or not all(future.done() for future in pending):
                return
            for future in pending:
                try:
                    future.result()
                except Exception:
                    logging.getLogger(__name__).exception("Agent debug shutdown failed")
            joined.set_result(None)

        for future in pending:
            future.add_done_callback(complete)
        complete()
        return joined

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
