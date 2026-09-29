"""Stable, incremental agent navigation for the chat sidebar."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidgetItem
from qfluentwidgets import ListWidget


def page_label(page: dict) -> str:
    """Use the same public page number and folder/name as the file list."""
    if not isinstance(page, dict):
        return ""
    try:
        identity = f"#{int(page.get('id')):03d}"
    except (TypeError, ValueError):
        identity = ""
    name = str(page.get("name") or "")
    folder = str(page.get("folder") or ".")
    path = f"{folder}/{name}" if name else ""
    return "  ".join(part for part in (identity, path) if part)


def task_pages(task: dict) -> list[dict]:
    """Return every assigned public identity, including legacy single-page tasks."""
    candidates = list(task.get("pages") or ())
    candidates.extend(assignment.get("page") for assignment in task.get("assignments") or ()
                      if isinstance(assignment, dict))
    if not candidates and task.get("page"):
        candidates.append(task["page"])
    pages = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        page = {key: candidate[key] for key in ("id", "folder", "name") if candidate.get(key) is not None}
        if not page:
            continue
        existing = next((item for item in pages if (
            page.get("id") is not None and item.get("id") == page["id"]
        ) or (page.get("name") and item.get("name") == page["name"]
              and item.get("folder") == page.get("folder"))), None)
        if existing is None:
            pages.append(page)
        else:
            existing.update(page)
    return pages


def task_page_summary(task: dict, translate) -> str:
    pages = task_pages(task)
    if len(pages) == 1:
        return page_label(pages[0])
    if not pages:
        return ""
    numbers = []
    for page in pages:
        try:
            numbers.append(f"#{int(page.get('id')):03d}")
        except (TypeError, ValueError):
            numbers.append(str(page.get("name") or "?"))
    count = translate("Agent assigned page count").format(count=len(pages))
    # Explicit numbers preserve gaps; #001, #003 must never become #001–003.
    return f"{count} · {', '.join(numbers)}"


class SubagentTaskList(ListWidget):
    """Keep the manager first and update child rows without losing selection."""

    task_selected = pyqtSignal(str)

    def __init__(self, translate=None, parent=None):
        super().__init__(parent)
        self._t = translate or (lambda key: key)
        self._tasks: dict[str, dict] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._manager_status = "idle"
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._manager = QListWidgetItem(self)
        self._manager.setData(Qt.ItemDataRole.UserRole, "manager")
        self._manager.setSizeHint(QSize(0, 64))
        self._update_manager()
        self.setCurrentRow(0)
        self.currentRowChanged.connect(self._emit_selection)

    @property
    def tasks(self) -> dict[str, dict]:
        return self._tasks

    @property
    def selected_task_id(self) -> str:
        item = self.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else "manager"

    def replace_tasks(self, tasks: dict[str, dict]) -> None:
        self._tasks = {str(key): dict(value) for key, value in tasks.items()}
        selected = self.selected_task_id
        for key in tuple(self._items):
            if key not in self._tasks:
                self.takeItem(self.row(self._items.pop(key)))
        for index, (task_id, task) in enumerate(self._tasks.items(), 1):
            item = self._items.get(task_id)
            if item is None:
                item = QListWidgetItem(self)
                item.setData(Qt.ItemDataRole.UserRole, task_id)
                item.setSizeHint(QSize(0, 64))
                self._items[task_id] = item
            status = self._status_text(task.get("status", "queued"))
            identity = task_page_summary(task, self._t) or task_id[:12]
            item.setText(f"{self._t('Agent child')} {index} · {status}\n{identity}")
            requirement = str(task.get("requirements") or "").strip()
            all_pages = "\n".join(page_label(page) for page in task_pages(task))
            item.setToolTip("\n".join(part for part in (all_pages, task_id, requirement) if part))
        self.setCurrentItem(self._items.get(selected, self._manager))

    def set_tasks(self, tasks: dict[str, dict]) -> None:
        self.replace_tasks(tasks)

    def clear_tasks(self) -> None:
        self.replace_tasks({})
        self.set_manager_status("idle")

    def set_manager_status(self, status: str) -> None:
        self._manager_status = status or "idle"
        self._update_manager()

    def select_task(self, task_id: str) -> None:
        item = self._manager if task_id == "manager" else self._items.get(task_id)
        if item is not None:
            self.setCurrentItem(item)

    def _status_text(self, status: str) -> str:
        keys = {
            "idle": "idle", "working": "working",
            "calling tool": "calling tool", "delegating": "delegating",
            "queued": "Agent status queued", "pending": "Agent status queued",
            "running": "Agent status running", "completed": "Agent status completed",
            "complete": "Agent status completed", "succeeded": "Agent status completed",
            "failed": "Agent status failed", "error": "Agent status failed",
            "cancelled": "Agent status cancelled", "canceled": "Agent status cancelled",
            "interrupted": "Agent status cancelled",
        }
        return self._t(keys.get(str(status), str(status)))

    def _update_manager(self) -> None:
        self._manager.setText(
            f"{self._t('Manager Agent')} · {self._status_text(self._manager_status)}\n"
            f"{self._t('Agent manager navigation hint')}"
        )

    def _emit_selection(self, row: int) -> None:
        item = self.item(row)
        if item is not None:
            task_id = item.data(Qt.ItemDataRole.UserRole)
            if task_id:
                self.task_selected.emit(str(task_id))
