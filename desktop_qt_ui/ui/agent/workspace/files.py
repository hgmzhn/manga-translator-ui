"""File list used by the Agent debug workspace."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QAbstractItemView, QListWidgetItem
from qfluentwidgets import ListWidget


class WorkspaceFileList(ListWidget):
    """Display the loaded pages using their stable number and file path."""

    page_selected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.currentItemChanged.connect(self._selection_changed)

    @property
    def current_page(self):
        item = self.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return dict(identity) if isinstance(identity, dict) else None

    def set_files(self, pages) -> None:
        selected = self.current_page
        with QSignalBlocker(self):
            self.clear()
            for page in pages or ():
                if not isinstance(page, dict):
                    continue
                page_id = page.get("id")
                folder = str(page.get("folder") or ".")
                name = str(page.get("name") or "")
                identity = {"id": page_id, "folder": folder, "name": name}
                number = f"#{page_id:03d}" if type(page_id) is int else "#?"
                path = f"{folder}/{name}"
                item = QListWidgetItem(f"{number}  {path}")
                item.setData(Qt.ItemDataRole.UserRole, identity)
                item.setToolTip(f"{number}  {path}")
                self.addItem(item)
            self.select_page(selected)

    @pyqtSlot(object)
    def select_page(self, page) -> None:
        """Synchronize preview selection without emitting another navigation."""
        with QSignalBlocker(self):
            if not isinstance(page, dict):
                self.setCurrentRow(-1)
                return
            for row in range(self.count()):
                identity = self.item(row).data(Qt.ItemDataRole.UserRole)
                if all(identity.get(key) == page[key] for key in ("id", "folder", "name") if key in page):
                    self.setCurrentRow(row)
                    self.scrollToItem(self.item(row))
                    return
            self.setCurrentRow(-1)

    def _selection_changed(self, current, previous):
        if current is not None:
            identity = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(identity, dict):
                self.page_selected.emit(dict(identity))

    # ``set_pages`` is the name used by the render preview signal.
    def set_pages(self, pages) -> None:
        self.set_files(pages)
