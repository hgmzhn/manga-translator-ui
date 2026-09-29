"""Full execution context for the standalone Agent debugger, kept in memory."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CheckBox, ListWidget, PlainTextEdit, PushButton, TitleLabel

from ui.theme import monospace_font


class RequestDebugPage(QWidget):
    """Browse requests, responses, tools, and failures without changing model history."""

    records_cleared = pyqtSignal()

    def __init__(self, t_func, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._records = []
        self._rows = {}
        self._external_navigation = False
        self._selected_event_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(TitleLabel(t_func("Agent context debug"), self))
        description = BodyLabel(t_func("Agent context debug description"), self)
        description.setWordWrap(True)
        layout.addWidget(description)
        splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.events = ListWidget(splitter)
        self.events.setMinimumWidth(240)
        self.events.currentRowChanged.connect(self._show_record)
        self.body = PlainTextEdit(splitter)
        self.body.setReadOnly(True)
        self.body.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.body.setFont(monospace_font())
        self.body.setPlaceholderText(t_func("Agent context debug empty"))
        splitter.addWidget(self.events)
        splitter.addWidget(self.body)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 850])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.follow = CheckBox(t_func("Agent context follow latest"), self)
        self.follow.setChecked(True)
        self.follow.toggled.connect(self._follow_latest)
        self.events.itemClicked.connect(lambda _: self.follow.setChecked(False))
        actions.addWidget(self.follow)
        actions.addStretch()
        self.copy_button = PushButton(t_func("Copy"), self)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_body)
        actions.addWidget(self.copy_button)
        self.copy_all_button = PushButton(t_func("Agent context copy all"), self)
        self.copy_all_button.setEnabled(False)
        self.copy_all_button.clicked.connect(self._copy_all)
        actions.addWidget(self.copy_all_button)
        clear_button = PushButton(t_func("Agent context clear"), self)
        clear_button.clicked.connect(self._clear_records)
        actions.addWidget(clear_button)
        layout.addLayout(actions)

    @pyqtSlot(object)
    def add_event(self, event):
        row = self._rows.get(event["id"])
        if row is None:
            row = len(self._records)
            self._rows[event["id"]] = row
            self._records.append(event)
            label = self._t("Agent context " + event["kind"])
            detail = event["data"].get("tool_name") or event["data"].get("exception_type") or ""
            previous = self.events.blockSignals(self._external_navigation)
            try:
                self.events.addItem(f"{row + 1:03d} · {label}" + (f" · {detail}" if detail else ""))
            finally:
                self.events.blockSignals(previous)
        else:
            self._records[row] = event
        if self._external_navigation:
            if event["id"] == self._selected_event_id:
                position = self.body.verticalScrollBar().value()
                self._show_record(row)
                self.body.verticalScrollBar().setValue(position)
            self.copy_all_button.setEnabled(True)
            return
        if self.follow.isChecked():
            self.events.setCurrentRow(len(self._records) - 1)
            self.events.scrollToBottom()
        if self.events.currentRow() == row:
            scrollbar = self.body.verticalScrollBar()
            position = scrollbar.value()
            self._show_record(row)
            scrollbar.setValue(position)
        self.copy_all_button.setEnabled(True)

    def set_external_navigation(self, enabled: bool) -> None:
        """Let the contextual sidebar own event navigation when enabled."""
        self._external_navigation = bool(enabled)
        self.events.setVisible(not self._external_navigation)
        if self._external_navigation:
            self.splitter.setSizes([0, 1])
        else:
            self.splitter.setSizes([300, 850])

    def select_event(self, event_id: str) -> None:
        """Select an event received from the sidebar without changing follow mode."""
        same_event = self._selected_event_id == str(event_id)
        position = self.body.verticalScrollBar().value()
        self._selected_event_id = str(event_id) or None
        if not event_id:
            self.events.setCurrentRow(-1)
            self.body.clear()
            self.copy_button.setEnabled(False)
            return
        row = self._rows.get(str(event_id))
        if row is None:
            return
        self.events.blockSignals(True)
        self.events.setCurrentRow(row)
        self.events.blockSignals(False)
        self._show_record(row)
        if same_event:
            self.body.verticalScrollBar().setValue(position)

    def select_agent(self, agent_key: str) -> None:
        """Select the latest event belonging to an agent key."""
        if self._external_navigation:
            return
        key = str(agent_key)
        for row in range(len(self._records) - 1, -1, -1):
            data = self._records[row].get("data", {})
            if data.get("agent_role") == key or data.get("task_id") == key:
                self.select_event(str(self._records[row].get("id", "")))
                return

    def _show_record(self, row):
        if 0 <= row < len(self._records):
            self.body.setPlainText(json.dumps(self._records[row], ensure_ascii=False, indent=2))
            self.copy_button.setEnabled(True)

    def _follow_latest(self, checked):
        if checked and self._records and not self._external_navigation:
            self.events.setCurrentRow(len(self._records) - 1)
            self.events.scrollToBottom()

    def _clear_records(self):
        self._selected_event_id = None
        self._records.clear()
        self._rows.clear()
        self.events.clear()
        self.body.clear()
        self.copy_button.setEnabled(False)
        self.copy_all_button.setEnabled(False)
        self.records_cleared.emit()

    def _copy_body(self):
        QApplication.clipboard().setText(self.body.toPlainText())

    def _copy_all(self):
        QApplication.clipboard().setText(json.dumps(self._records, ensure_ascii=False, indent=2))
