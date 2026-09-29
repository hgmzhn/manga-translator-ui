"""Selected delegated agent's task context and live execution, without model calls."""

from __future__ import annotations

import json
from html import escape

from PyQt6.QtCore import QSize, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, ListWidget, PlainTextEdit, PushButton

from ui.agent.conversation_view import ConversationView, ToolRecord
from .tasks import page_label, task_pages, task_page_summary


class AgentDetailView(CardWidget):
    """Observe a child while the manager conversation keeps running offscreen."""

    open_logs = pyqtSignal(str)
    open_page = pyqtSignal(object)

    def __init__(self, translate, parent=None):
        super().__init__(parent)
        self._t = translate
        self._details = {}
        self._expanded = set()
        self._tool_ids = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.identity = BodyLabel(self)
        self.identity.setTextFormat(Qt.TextFormat.PlainText)
        self.identity.setWordWrap(False)
        self.identity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.identity, 1)
        self.details_button = PushButton(self._t("Agent task context"), self)
        self.details_button.setCheckable(True)
        self.logs_button = PushButton(self._t("Agent open execution log"), self)
        self.page_button = PushButton(self._t("Agent open task page"), self)
        header.addWidget(self.details_button)
        header.addWidget(self.logs_button)
        header.addWidget(self.page_button)
        layout.addLayout(header)
        # Task setup is optional reading; execution gets the main viewport.
        self.details_panel = QWidget(self)
        self.details_panel.setFixedHeight(220)
        details_layout = QHBoxLayout(self.details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        instructions = QVBoxLayout()
        instructions.addWidget(BodyLabel(self._t("Agent task instructions"), self.details_panel))
        self.requirements = PlainTextEdit(self.details_panel)
        self.requirements.setReadOnly(True)
        instructions.addWidget(self.requirements, 1)
        details_layout.addLayout(instructions, 3)
        scope_layout = QVBoxLayout()
        self.scope_label = BodyLabel(self._t("Agent assigned pages and scope"), self.details_panel)
        self.scope_label.setWordWrap(True)
        scope_layout.addWidget(self.scope_label)
        self.scope_list = ListWidget(self.details_panel)
        self.scope_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.scope_list.currentItemChanged.connect(self._scope_selected)
        self.scope_list.itemDoubleClicked.connect(lambda _: self._open_selected_page())
        scope_layout.addWidget(self.scope_list, 1)
        details_layout.addLayout(scope_layout, 2)
        layout.addWidget(self.details_panel)
        self.details_panel.hide()
        self.details_button.toggled.connect(self.details_panel.setVisible)
        self._scope_signature = None
        self.transcript = ConversationView(self)
        self.transcript.setPlaceholderText(self._t("Agent waiting for activity"))
        self.transcript.anchorClicked.connect(self._toggle_tool)
        layout.addWidget(self.transcript, 1)
        self.logs_button.clicked.connect(lambda: self.open_logs.emit(str(self._details.get("task_id", ""))))
        self.page_button.clicked.connect(self._open_selected_page)

    def set_details(self, details):
        previous = self._details.get("task_id")
        self._details = details if isinstance(details, dict) else {}
        task_id = str(self._details.get("task_id") or self._details.get("agent_key") or "")
        if previous != task_id:
            self._expanded.clear()
            self.details_button.setChecked(False)
            self._scope_signature = None
            self.transcript.clear()
        status = str(self._details.get("status", "queued"))
        page_text = task_page_summary(self._details, self._t)
        pages = task_pages(self._details)
        if len(pages) > 1:
            page_text = self._t("Agent assigned page count").format(count=len(pages))
        label = self._details.get("label")
        # Sidebar labels already include the assigned-page count.
        identity = label or " · ".join(filter(None, [self._t("Agent child"), page_text]))
        self.identity.setText(" · ".join([identity, self._t("Agent status " + status)]))
        self.identity.setToolTip("\n".join([task_id, *(page_label(page) for page in pages)]))
        requirements = str(self._details.get("requirements") or self._t("Agent waiting for assignment"))
        if requirements != self.requirements.toPlainText():
            position = self.requirements.verticalScrollBar().value()
            self.requirements.setPlainText(requirements)
            self.requirements.verticalScrollBar().setValue(position)
        self._update_scope(task_id)
        self.logs_button.setEnabled(bool(task_id))
        self._render_events()

    def _update_scope(self, task_id):
        pages = task_pages(self._details)
        assignments = self._details.get("assignments") or ()
        signature = json.dumps([task_id, pages, assignments, self._details.get("editable_regions")],
                               ensure_ascii=False, sort_keys=True, default=str)
        if signature == self._scope_signature:
            return
        selected = self.scope_list.currentItem()
        previous = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        previous_task = self._scope_signature
        self._scope_signature = signature
        with QSignalBlocker(self.scope_list):
            self.scope_list.clear()
            selected_item = None
            for page in pages:
                assignment = next((item for item in assignments if isinstance(item, dict)
                                   and self._same_page(item.get("page"), page)), {})
                regions = assignment.get("editable_regions")
                if regions is None and len(pages) == 1:
                    regions = self._details.get("editable_regions")
                if regions is None:
                    scope = self._t("Agent edit scope unavailable")
                elif regions == "all":
                    scope = self._t("Agent whole page scope")
                elif not regions:
                    scope = self._t("Agent no editable regions")
                else:
                    scope = self._t("Agent editable regions") + ": " + ", ".join(map(str, regions))
                text = page_label(page) + "\n" + scope
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, dict(page))
                item.setSizeHint(QSize(0, 58))
                item.setToolTip(text)
                self.scope_list.addItem(item)
                if previous_task and self._same_page(previous, page):
                    selected_item = item
            if selected_item is not None:
                self.scope_list.setCurrentItem(selected_item)
            elif self.scope_list.count():
                self.scope_list.setCurrentRow(0)
        self._scope_selected(self.scope_list.currentItem(), None)

    @staticmethod
    def _same_page(left, right):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if left.get("id") is not None and right.get("id") is not None:
            return left["id"] == right["id"]
        return bool(left.get("name") and left.get("name") == right.get("name")
                    and left.get("folder", ".") == right.get("folder", "."))

    def _scope_selected(self, current, _previous):
        self.page_button.setEnabled(current is not None)
        self.page_button.setToolTip(page_label(current.data(Qt.ItemDataRole.UserRole)) if current else "")

    def _open_selected_page(self):
        item = self.scope_list.currentItem()
        if item is not None:
            self.open_page.emit(dict(item.data(Qt.ItemDataRole.UserRole)))

    def _render_events(self):
        entries = []
        tools = {}
        ordered = []
        self._tool_ids = []
        for event in self._details.get("events", []):
            if not isinstance(event, dict):
                continue
            data = event.get("data") or {}
            kind = event.get("kind")
            if kind in {"tool_call", "tool_result", "validation_error"}:
                call_id = str(data.get("tool_call_id") or event.get("id"))
                if call_id not in tools:
                    tools[call_id] = ToolRecord(call_id, str(data.get("tool_name", "")))
                    ordered.append(tools[call_id])
                record = tools[call_id]
                if kind == "tool_call":
                    record.arguments = data.get("arguments")
                else:
                    record.finish(data.get("result"), validation_error=kind == "validation_error")
            elif kind == "model_response":
                chunks = []
                for part in data.get("parts", []):
                    if isinstance(part, dict) and part.get("part_kind") in {"text", "thinking"}:
                        content = part.get("content")
                        if isinstance(content, str) and content.strip():
                            if part.get("part_kind") == "thinking":
                                chunks.append(f'<p><b>{escape(self._t("Chat thinking summary"))}</b></p>')
                            chunks.append(self.transcript.markdown(content))
                if chunks:
                    ordered.append("".join(chunks))
            elif kind == "error":
                ordered.append(f'<p><b>{escape(self._t("Agent status failed"))}</b></p><p>{escape(str(data.get("message", "")))}</p>')
            elif kind == "task_status" and data.get("error"):
                error = data["error"]
                ordered.append(f'<p>{escape(str(error.get("message", error) if isinstance(error, dict) else error))}</p>')
        for item in ordered:
            if isinstance(item, ToolRecord):
                item.expanded = item.tool_call_id in self._expanded
                self._tool_ids.append(item.tool_call_id)
                entries.append(item.html(len(self._tool_ids) - 1, self._t))
            else:
                entries.append(item)
        self.transcript.show_entries(entries)

    def _toggle_tool(self, url):
        if url.scheme() != "tool":
            return
        try:
            key = self._tool_ids[int(url.path())]
        except (ValueError, IndexError):
            return
        if key in self._expanded:
            self._expanded.remove(key)
        else:
            self._expanded.add(key)
        self._render_events()
