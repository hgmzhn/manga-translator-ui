"""
编辑器历史管理器
支持撤销/重做操作，管理编辑器状态历史
"""
import copy
import logging
from typing import List, Any, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum

class ActionType(Enum):
    """操作类型"""
    MOVE = "move"
    RESIZE = "resize"
    ROTATE = "rotate"
    DELETE = "delete"
    ADD = "add"
    MODIFY_TEXT = "modify_text"
    MODIFY_STYLE = "modify_style"
    EDIT_MASK = "edit_mask"
    GROUP = "group" # New action type for grouped actions

@dataclass
class EditorAction:
    """编辑器操作"""
    action_type: ActionType
    region_index: Optional[int] # Can be None for grouped actions
    old_data: Any
    new_data: Any
    timestamp: float = field(default_factory=lambda: __import__('time').time())
    description: str = ""

@dataclass
class GroupedAction(EditorAction):
    """A group of actions to be treated as a single undo/redo step."""
    actions: List[EditorAction] = field(default_factory=list)
    action_type: ActionType = ActionType.GROUP
    region_index: Optional[int] = None
    old_data: Any = None
    new_data: Any = None

class EditorHistory:
    """编辑器历史管理器"""
    
    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self.history: List[EditorAction] = []
        self.current_index = -1
        self.grouping = False
        self.grouped_actions: List[EditorAction] = []
        self.logger = logging.getLogger(__name__)

    def start_action_group(self):
        if not self.grouping:
            self.grouping = True
            self.grouped_actions = []

    def end_action_group(self, description: str = "Batch Operation"):
        if self.grouping and self.grouped_actions:
            group = GroupedAction(description=description, actions=self.grouped_actions)
            self.grouping = False
            self.grouped_actions = []
            self._add_action_to_history(group)
        else:
            self.grouping = False

    def add_action(self, action: EditorAction):
        if self.grouping:
            self.grouped_actions.append(action)
        else:
            self._add_action_to_history(action)

    def _add_action_to_history(self, action: EditorAction):
        if self.current_index < len(self.history) - 1:
            self.history = self.history[:self.current_index + 1]
        
        self.history.append(action)
        self.current_index += 1
        
        if len(self.history) > self.max_history:
            self.history.pop(0)
            self.current_index -= 1
        
        self.logger.debug(f"Added action: {action.action_type.value} - {action.description}")
    
    def can_undo(self) -> bool:
        return self.current_index >= 0
    
    def can_redo(self) -> bool:
        return self.current_index < len(self.history) - 1
    
    def undo(self) -> Optional[EditorAction]:
        if not self.can_undo(): return None
        action = self.history[self.current_index]
        self.current_index -= 1
        self.logger.debug(f"Undoing action: {action.action_type.value}")
        return action
    
    def redo(self) -> Optional[EditorAction]:
        if not self.can_redo(): return None
        self.current_index += 1
        action = self.history[self.current_index]
        self.logger.debug(f"Redoing action: {action.action_type.value}")
        return action

    def clear(self):
        self.history.clear()
        self.current_index = -1
        self.logger.debug("Cleared edit history")

class EditorStateManager:
    """编辑器状态管理器"""
    
    def __init__(self):
        self.history = EditorHistory()
        self.clipboard_data = None
        self.logger = logging.getLogger(__name__)
        
    def save_state(self, action_type: ActionType, region_index: int, 
                   old_data: Any, new_data: Any, description: str = ""):
        action = EditorAction(
            action_type=action_type,
            region_index=region_index,
            old_data=copy.deepcopy(old_data),
            new_data=copy.deepcopy(new_data),
            description=description
        )
        self.history.add_action(action)

    def start_action_group(self):
        self.history.start_action_group()

    def end_action_group(self, description: str):
        self.history.end_action_group(description)
    
    def undo(self):
        return self.history.undo()
    
    def redo(self):
        return self.history.redo()
    
    def can_undo(self) -> bool:
        return self.history.can_undo()
    
    def can_redo(self) -> bool:
        return self.history.can_redo()
    
    def copy_to_clipboard(self, data: Any):
        self.clipboard_data = copy.deepcopy(data)
        self.logger.debug("Data copied to internal clipboard")
    
    def paste_from_clipboard(self) -> Any:
        if self.clipboard_data is not None:
            return copy.deepcopy(self.clipboard_data)
        return None
    
    def clear(self):
        """清除历史记录"""
        self.history.clear()
        self.logger.debug("Cleared editor state manager history")
    
    @property
    def undo_stack(self):
        """获取撤销栈，用于检查是否有未保存的修改"""
        return self.history.history[:self.history.current_index + 1]
