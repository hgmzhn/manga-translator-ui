"""Contextual sidebar for the chat, execution-context and render pages."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, SubtitleLabel

from .workspace import AgentLogTabs, SubagentTaskList, WorkspaceFileList
from .workspace.tasks import task_pages


class AgentWorkspacePanel(QWidget):
    """Show one relevant navigator at a time while keeping live data intact."""

    page_selected = pyqtSignal(object)
    agent_selected = pyqtSignal(str)
    agent_details_changed = pyqtSignal(str)
    event_selected = pyqtSignal(str)

    def __init__(self, t_func, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._tasks: dict[str, dict] = {}
        self._logs: dict[str, dict] = {}
        self._event_owners: dict[str, str] = {}
        self._turn_info: dict[str, dict] = {}
        self._manager_status = "idle"
        self._active_page = "chat"
        self._file_count = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 20, 16, 16)
        root.setSpacing(12)
        self.title = SubtitleLabel(self)
        root.addWidget(self.title)
        self.description = BodyLabel(self)
        self.description.setWordWrap(True)
        root.addWidget(self.description)
        self.stack = QStackedWidget(self)
        root.addWidget(self.stack, 1)

        self.subagents = SubagentTaskList(translate=t_func)
        self.task_page = QWidget(self.stack)
        tasks_layout = QVBoxLayout(self.task_page)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.setSpacing(10)
        tasks_layout.addWidget(self.subagents, 1)
        self.tasks_empty = BodyLabel(t_func("Agent sub-agents empty"), self.task_page)
        self.tasks_empty.setWordWrap(True)
        tasks_layout.addWidget(self.tasks_empty)
        self.log_tabs = AgentLogTabs(translate=t_func)
        self.files = WorkspaceFileList()
        self.file_page = QWidget(self.stack)
        files_layout = QVBoxLayout(self.file_page)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(10)
        self.files_empty = BodyLabel(t_func("Agent files empty"), self.file_page)
        self.files_empty.setWordWrap(True)
        files_layout.addWidget(self.files_empty)
        files_layout.addWidget(self.files, 1)
        self.stack.addWidget(self.task_page)
        self.stack.addWidget(self.log_tabs)
        self.stack.addWidget(self.file_page)
        self.subagents.task_selected.connect(self._select_agent)
        self.log_tabs.event_selected.connect(self.event_selected)
        self.files.page_selected.connect(self.page_selected)
        self.set_active_page("chat")

    def set_active_page(self, page: str) -> None:
        """Follow the left navigation; there are no duplicate sidebar tabs."""
        pages = {"chat": self.task_page, "request": self.log_tabs, "render": self.file_page}
        if page not in pages:
            raise ValueError(f"unknown Agent debug page: {page!r}")
        self._active_page = page
        self.stack.setCurrentWidget(pages[page])
        titles = {"chat": "Agent tasks sidebar", "request": "Agent logs", "render": "Agent files"}
        hints = {
            "chat": "Agent tasks sidebar hint", "request": "Agent logs sidebar hint",
            "render": "Agent files sidebar hint",
        }
        self.title.setText(self._t(titles[page]))
        self.description.setText(self._t(hints[page]))

    def set_files(self, pages):
        pages = list(pages or ())
        self.files.set_files(pages)
        self._file_count = sum(isinstance(page, dict) for page in pages)
        self.files_empty.setVisible(self._file_count == 0)

    def selected_agent_details(self) -> dict:
        return self.get_agent_details(self.subagents.selected_task_id)

    def get_agent_details(self, key: str) -> dict:
        task = self._tasks.get(key, {})
        pages = task_pages(task)
        return {
            "task_id": key,
            "agent_key": key,
            "label": self._agent_label(key),
            "status": self._manager_status if key == "manager" else task.get("status", "idle"),
            "page": dict(pages[0]) if len(pages) == 1 else {},
            "pages": pages,
            "assignments": list(task.get("assignments") or ()),
            "editable_regions": task.get("editable_regions"),
            "result": task.get("result"),
            "requirements": task.get("requirements", ""),
            "events": [event for event_id, event in self._logs.items()
                       if self._event_owners.get(event_id) == key],
        }

    def _agent_label(self, key: str) -> str:
        if key == "manager":
            return self._t("Manager Agent")
        if key == "page":
            return self._t("Chat page agent")
        if key in self._tasks:
            number = tuple(self._tasks).index(key) + 1
            label = f"{self._t('Agent child')} {number}"
            pages = task_pages(self._tasks[key])
            if len(pages) > 1:
                label += " · " + self._t("Agent assigned page count").format(count=len(pages))
            return label
        return key[:12]

    def _select_agent(self, key: str) -> None:
        self.agent_selected.emit(key)

    def _update_task(self, task_id: str, data: dict) -> None:
        task = self._tasks.setdefault(task_id, {"status": "queued", "page": {}})
        for field in ("status", "requirements", "parent_task_id",
                      "assignments", "editable_regions", "result"):
            if data.get(field) is not None:
                task[field] = data[field]
        page = data.get("page")
        if not isinstance(page, dict):
            page = {field: data[field] for field in ("id", "folder", "name") if data.get(field) is not None}
        if page:
            task["page"] = {**task.get("page", {}), **page}
        if isinstance(data.get("pages"), list):
            # Later status-only events must not discard the original delegation scope.
            task["pages"] = task_pages({"pages": [*task_pages(task), *data["pages"]]})
        elif not task.get("pages"):
            task["pages"] = task_pages(task)
        if len(task_pages(task)) > 1:
            task.pop("page", None)

    def add_event(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            return
        data = event["data"]
        turn_id = str(event.get("turn_id") or "")
        info = self._turn_info.setdefault(turn_id, {}) if turn_id else {}
        for field in ("agent_role", "task_id", "parent_task_id"):
            if data.get(field):
                info[field] = data[field]
        role = data.get("agent_role") or info.get("agent_role") or "manager"
        task_id = str(data.get("task_id") or info.get("task_id") or "")
        parent_id = data.get("parent_task_id") or info.get("parent_task_id")
        kind = str(event.get("kind") or "event")
        is_child = bool(task_id and (parent_id or task_id in self._tasks or kind == "task_status"))
        owner = task_id if is_child else ("page" if role == "page" else "manager")
        changed = {owner}
        if is_child:
            self._update_task(task_id, data)
            task = self._tasks[task_id]
            if kind == "turn_start" and task.get("status") in {"queued", "pending"}:
                task["status"] = "running"
            elif kind in {"error", "cancelled"}:
                task["status"] = "failed" if kind == "error" else "cancelled"
        elif owner == "manager":
            status = {
                "turn_start": "working", "tool_call": "calling tool",
                "tool_result": "working", "turn_end": "idle", "turn_complete": "idle",
                "error": "error", "cancelled": "idle",
            }.get(kind)
            result = data.get("result")
            if kind == "tool_result" and isinstance(result, dict) and result.get("status") == "error":
                status = "error"
            if status is not None:
                self._manager_status = status
                self.subagents.set_manager_status(status)

        result = data.get("result")
        if isinstance(result, dict):
            if data.get("tool_name") == "revise_pages" and result.get("status") != "error":
                task_ids = result.get("task_ids") or ()
                for child_id in task_ids if isinstance(task_ids, (list, tuple)) else ():
                    child_id = str(child_id)
                    self._tasks.setdefault(child_id, {"status": "queued", "page": {}})
                    changed.add(child_id)
            items = result.get("items")
            for item in items if isinstance(items, list) else ():
                if not isinstance(item, dict) or not item.get("task_id"):
                    continue
                child_id = str(item["task_id"])
                self._update_task(child_id, item)
                changed.add(child_id)
        self.subagents.replace_tasks(self._tasks)
        self.tasks_empty.setVisible(not self._tasks)
        event_id = str(event.get("id") or f"event:{len(self._logs)}")
        self._logs[event_id] = event
        self._event_owners[event_id] = owner
        self.log_tabs.add_event(event, owner, self._agent_label(owner))
        for key in changed:
            self.log_tabs.ensure_view(key, self._agent_label(key))
            self.agent_details_changed.emit(key)

    def clear(self, *, preserve_files=False):
        self._tasks.clear()
        self._logs.clear()
        self._event_owners.clear()
        self._turn_info.clear()
        self._manager_status = "idle"
        self.subagents.clear_tasks()
        self.log_tabs.clear_views()
        if not preserve_files:
            self.files.clear()
            self._file_count = 0
        self.tasks_empty.show()
        self.files_empty.setVisible(self._file_count == 0)
        self.agent_details_changed.emit("manager")
