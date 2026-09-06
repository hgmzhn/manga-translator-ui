"""
编辑器历史管理器
基于 Qt 原生 QUndoStack，统一封装命令执行、宏操作和剪贴板能力。
"""

import copy
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QUndoCommand, QUndoStack


class ClipboardManager:
    """剪贴板管理器，处理内部数据的复制粘贴。"""

    def __init__(self):
        self.clipboard_data = None
        self.logger = logging.getLogger(__name__)

    def copy_to_clipboard(self, data: Any):
        """复制数据到内部剪贴板。"""
        self.clipboard_data = copy.deepcopy(data)
        self.logger.debug("Data copied to internal clipboard")

    def paste_from_clipboard(self) -> Any:
        """从内部剪贴板粘贴数据。"""
        if self.clipboard_data is not None:
            return copy.deepcopy(self.clipboard_data)
        return None

    def has_data(self) -> bool:
        """检查剪贴板是否有数据。"""
        return self.clipboard_data is not None


class EditorStateManager(QObject):
    """
    编辑器状态管理器。

    职责：
    - 统一执行 QUndoCommand
    - 支持宏命令（批量操作一次撤回）
    - 对外提供 can_undo/can_redo 状态信号
    - 提供内部剪贴板能力
    """

    undo_redo_state_changed = pyqtSignal(bool, bool)  # can_undo, can_redo
    stack_index_changed = pyqtSignal(int)

    def __init__(self, undo_limit: int = 50, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(__name__)
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(max(1, int(undo_limit)))
        self.clipboard = ClipboardManager()
        self._macro_depth = 0
        self._revision = 0

        self.undo_stack.canUndoChanged.connect(self._on_undo_redo_changed)
        self.undo_stack.canRedoChanged.connect(self._on_undo_redo_changed)
        self.undo_stack.indexChanged.connect(self._on_index_changed)

    def execute(self, command: Optional[QUndoCommand]):
        """
        执行一个命令。
        QUndoStack.push() 会立即调用 command.redo()。
        """
        if command is None:
            return
        self.undo_stack.push(command)

    def push_command(self, command: Optional[QUndoCommand]):
        """兼容旧接口。"""
        self.execute(command)

    def begin_macro(self, text: str):
        """开始宏命令。"""
        self.undo_stack.beginMacro(text)
        self._macro_depth += 1

    def end_macro(self):
        """结束宏命令。"""
        if self._macro_depth <= 0:
            self.logger.warning("end_macro called without active macro")
            return
        self.undo_stack.endMacro()
        self._macro_depth -= 1

    @contextmanager
    def macro(self, text: str) -> Iterator[None]:
        """上下文管理器形式的宏命令。"""
        self.begin_macro(text)
        try:
            yield
        finally:
            self.end_macro()

    def undo(self):
        """撤销上一个操作。"""
        if self.undo_stack.canUndo():
            self.undo_stack.undo()

    def redo(self):
        """重做上一个被撤销的操作。"""
        if self.undo_stack.canRedo():
            self.undo_stack.redo()

    def can_undo(self) -> bool:
        """检查是否可以撤销。"""
        return self.undo_stack.canUndo()

    def can_redo(self) -> bool:
        """检查是否可以重做。"""
        return self.undo_stack.canRedo()

    def set_undo_limit(self, limit: int):
        """动态设置撤销栈上限。"""
        self.undo_stack.setUndoLimit(max(1, int(limit)))

    def copy_to_clipboard(self, data: Any):
        """复制数据到内部剪贴板。"""
        self.clipboard.copy_to_clipboard(data)

    def paste_from_clipboard(self) -> Any:
        """从内部剪贴板粘贴数据。"""
        return self.clipboard.paste_from_clipboard()

    def has_clipboard_data(self) -> bool:
        """检查剪贴板是否有数据。"""
        return self.clipboard.has_data()

    def clear(self):
        """清除历史记录。"""
        while self._macro_depth > 0:
            self.end_macro()
        self.undo_stack.clear()
        self.logger.debug("Cleared undo stack")

    def mark_clean(self):
        """标记当前状态为已保存。"""
        self.undo_stack.setClean()

    def mark_dirty(self):
        """清除已保存标记（如导出失败后回退乐观标记）。"""
        self.undo_stack.resetClean()

    def is_clean(self) -> bool:
        """是否处于已保存状态。"""
        return self.undo_stack.isClean()

    def current_revision(self) -> int:
        return self._revision

    def create_undo_action(self, parent, text: str = "撤销"):
        """创建撤销动作（用于菜单/工具栏）。"""
        return self.undo_stack.createUndoAction(parent, text)

    def create_redo_action(self, parent, text: str = "重做"):
        """创建重做动作（用于菜单/工具栏）。"""
        return self.undo_stack.createRedoAction(parent, text)

    def _on_undo_redo_changed(self, _):
        self.undo_redo_state_changed.emit(
            self.undo_stack.canUndo(), self.undo_stack.canRedo()
        )

    def _on_index_changed(self, index: int):
        self._revision += 1
        self.stack_index_changed.emit(index)


# --- Singleton Pattern ---
_history_service_instance: Optional[EditorStateManager] = None


def get_history_service() -> EditorStateManager:
    """获取历史记录服务的单例。"""
    global _history_service_instance
    if _history_service_instance is None:
        _history_service_instance = EditorStateManager()
    return _history_service_instance
