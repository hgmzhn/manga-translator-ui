"""Per-agent event navigation; full records live in the context page."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, ListWidget


def event_summary(event: dict) -> str:
    """Show actual response/tool content instead of bare event-kind markers."""
    data = event.get("data", {})
    if event.get("kind") == "request_budget":
        return (f"{data.get('images_before', 0)} → {data.get('images_kept', 0)} images · "
                f"{data.get('estimated_request_bytes', 0):,} / {data.get('request_budget_bytes', 0):,} bytes")
    result = data.get("result")
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if data.get("tool_name"):
            return f"{data['tool_name']} · {result.get('status', '')}".strip(" ·")
    if event.get("kind") == "model_response":
        parts = data.get("parts") or ()
        content = []
        for part in parts if isinstance(parts, (list, tuple)) else ():
            if not isinstance(part, dict):
                continue
            text = part.get("content") or part.get("text") or part.get("tool_name")
            if text:
                content.append(str(text))
        return " ".join(content) or str(data.get("state") or "")
    for key in ("message", "requirements", "text", "tool_name", "status", "model"):
        if data.get(key):
            return str(data[key])
    body = data.get("body")
    return str(body.get("model") or "") if isinstance(body, dict) else ""


class AgentLogTabs(QWidget):
    """Compatibility name for the sidebar's agent filter and event list."""

    event_selected = pyqtSignal(str)
    agent_selected = pyqtSignal(str)
    follow_changed = pyqtSignal(bool)

    def __init__(self, parent=None, *, translate=None):
        super().__init__(parent)
        self._t = translate or (lambda key: key)
        self._records: dict[str, dict] = {}
        self._agents: dict[str, str] = {}
        self._owners: dict[str, str] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._follow = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.agent_filter = ComboBox(self)
        self.agent_filter.addItem(self._t("Agent all agents"), userData="all")
        self.agent_filter.currentIndexChanged.connect(self._filter_changed)
        layout.addWidget(self.agent_filter)
        self.empty = BodyLabel(self._t("Agent logs empty"), self)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.events = ListWidget(self)
        self.events.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.events.currentItemChanged.connect(self._selection_changed)
        self.events.itemClicked.connect(lambda _: self._stop_following())
        layout.addWidget(self.events, 1)
        self.ensure_view("manager", self._t("Manager Agent"))

    def ensure_view(self, key, label=None) -> None:
        key = str(key)
        if key not in self._agents:
            self._agents[key] = label or key
            self.agent_filter.addItem(label or key, userData=key)
        elif label and label != self._agents[key]:
            self._agents[key] = label
            self.agent_filter.setItemText(self.agent_filter.findData(key), label)
            for event_id, item in self._items.items():
                if self._owners.get(event_id) == key:
                    self._update_item(item, self._records[event_id], label)

    def add_event(self, event: dict, agent_key: str, label: str) -> None:
        key = str(event.get("id") or f"event:{len(self._records)}")
        self.ensure_view(agent_key, label)
        self._records[key] = event
        self._owners[key] = agent_key
        active = self.agent_filter.currentData() or "all"
        if active not in {"all", agent_key}:
            return
        item = self._items.get(key)
        is_new = item is None
        if is_new:
            item = QListWidgetItem(self.events)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, 66))
            self._items[key] = item
        self._update_item(item, event, label)
        self.empty.hide()
        if self._follow and is_new:
            self.events.setCurrentItem(item)
            self.events.scrollToBottom()
        elif self.events.currentItem() is item:
            self.event_selected.emit(key)

    def select_view(self, key) -> None:
        index = self.agent_filter.findData(str(key))
        if index >= 0:
            self.agent_filter.setCurrentIndex(index)

    def select_key(self, key) -> None:
        self.select_view(key)

    def set_follow(self, enabled: bool) -> None:
        enabled = bool(enabled)
        changed = self._follow != enabled
        self._follow = enabled
        if enabled and self.events.count():
            self.events.setCurrentRow(self.events.count() - 1)
            self.events.scrollToBottom()
        if changed:
            self.follow_changed.emit(enabled)

    def selected_event(self) -> dict | None:
        item = self.events.currentItem()
        return self._records.get(str(item.data(Qt.ItemDataRole.UserRole))) if item else None

    def clear_views(self) -> None:
        self._records.clear()
        self._owners.clear()
        self._items.clear()
        self._agents.clear()
        self.events.clear()
        self.agent_filter.blockSignals(True)
        self.agent_filter.clear()
        self.agent_filter.addItem(self._t("Agent all agents"), userData="all")
        self.ensure_view("manager", self._t("Manager Agent"))
        self.agent_filter.blockSignals(False)
        self.empty.show()
        self.set_follow(True)
        self.event_selected.emit("")

    def _update_item(self, item, event, label) -> None:
        kind = str(event.get("kind") or "event")
        title = self._t("Agent context " + kind)
        if title == "Agent context " + kind:
            title = kind.replace("_", " ")
        summary = " ".join(event_summary(event).split())
        item.setText(f"{title} · {label}\n{summary[:180]}")
        item.setToolTip(summary[:1200] or title)

    def _filter_changed(self, _index) -> None:
        selected = self.selected_event()
        selected_id = str(selected.get("id")) if selected else None
        active = self.agent_filter.currentData() or "all"
        self.events.blockSignals(True)
        self.events.clear()
        self._items.clear()
        for key, event in self._records.items():
            owner = self._owners[key]
            if active not in {"all", owner}:
                continue
            item = QListWidgetItem(self.events)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, 66))
            self._items[key] = item
            self._update_item(item, event, self._agents.get(owner, owner))
        self.events.blockSignals(False)
        self.empty.setVisible(not self._items)
        self.agent_selected.emit(str(active))
        item = self._items.get(selected_id)
        if self._follow and self.events.count():
            self.events.setCurrentRow(self.events.count() - 1)
        elif item is not None:
            self.events.setCurrentItem(item)
        elif self.events.count():
            self.events.setCurrentRow(self.events.count() - 1)
        else:
            self.event_selected.emit("")

    def _selection_changed(self, current, _previous) -> None:
        if current is not None:
            self.event_selected.emit(str(current.data(Qt.ItemDataRole.UserRole)))

    def _stop_following(self) -> None:
        self.set_follow(False)
