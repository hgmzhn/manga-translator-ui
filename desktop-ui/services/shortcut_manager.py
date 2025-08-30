"""
快捷键管理器
提供键盘快捷键注册、管理和处理功能
"""
import tkinter as tk
from typing import Dict, Callable, List, Optional, Any
import logging
from dataclasses import dataclass
from enum import Enum

class KeyModifier(Enum):
    """按键修饰符"""
    CTRL = "Control"
    ALT = "Alt"
    SHIFT = "Shift"
    CMD = "Cmd"  # macOS

@dataclass
class Shortcut:
    """快捷键数据类"""
    key: str
    modifiers: List[KeyModifier]
    callback: Callable[[], None]
    description: str
    context: str = "global"  # 快捷键上下文
    enabled: bool = True

class ShortcutManager:
    """快捷键管理器"""
    
    def __init__(self, root_widget):
        self.root_widget = root_widget
        self.shortcuts: Dict[str, Shortcut] = {}
        self.context_stack: List[str] = ["global"]  # 上下文栈
        self.logger = logging.getLogger(__name__)
        
        # 系统平台检测
        import platform
        self.is_mac = platform.system() == "Darwin"
        
        # 设置全局键盘监听
        self._setup_global_bindings()
        
    def _setup_global_bindings(self):
        """设置全局键盘绑定"""
        # 绑定所有可能的按键组合
        self.root_widget.bind_all("<Key>", self._on_key_press)
        self.root_widget.bind_all("<KeyRelease>", self._on_key_release)
        
        # 绑定常用的修饰符组合
        for modifier in ["Control", "Alt", "Shift"]:
            for key_char in "abcdefghijklmnopqrstuvwxyz0123456789":
                key_combo = f"<{modifier}-{key_char}>"
                self.root_widget.bind_all(key_combo, self._on_shortcut)
                
                # 双修饰符组合
                for modifier2 in ["Control", "Alt", "Shift"]:
                    if modifier != modifier2:
                        key_combo = f"<{modifier}-{modifier2}-{key_char}>"
                        self.root_widget.bind_all(key_combo, self._on_shortcut)
        
        # 功能键
        for i in range(1, 13):  # F1-F12
            self.root_widget.bind_all(f"<F{i}>", self._on_shortcut)
            self.root_widget.bind_all(f"<Control-F{i}>", self._on_shortcut)
            self.root_widget.bind_all(f"<Alt-F{i}>", self._on_shortcut)
        
        # 特殊键（使用正确的Tkinter键名）
        special_keys = ["Return", "Escape", "Delete", "BackSpace", "Tab", "space"]
        for key in special_keys:
            try:
                self.root_widget.bind_all(f"<{key}>", self._on_shortcut)
                self.root_widget.bind_all(f"<Control-{key}>", self._on_shortcut)
            except Exception as e:
                self.logger.debug(f"无法绑定键 {key}: {e}")
    
    def register_shortcut(self, 
                         key: str, 
                         callback: Callable[[], None], 
                         modifiers: List[KeyModifier] = None,
                         description: str = "",
                         context: str = "global") -> bool:
        """
        注册快捷键
        
        Args:
            key: 按键（如 'o', 'F5', 'Return'）
            callback: 回调函数
            modifiers: 修饰符列表
            description: 快捷键描述
            context: 上下文
            
        Returns:
            bool: 是否注册成功
        """
        try:
            if modifiers is None:
                modifiers = []
            
            # 生成快捷键字符串
            shortcut_str = self._generate_shortcut_string(key, modifiers)
            
            # 检查冲突
            if shortcut_str in self.shortcuts:
                self.logger.warning(f"快捷键 {shortcut_str} 已存在，将被覆盖")
            
            # 创建快捷键对象
            shortcut = Shortcut(
                key=key,
                modifiers=modifiers,
                callback=callback,
                description=description,
                context=context
            )
            
            self.shortcuts[shortcut_str] = shortcut
            
            # 绑定到tkinter
            self._bind_shortcut(shortcut_str)
            
            self.logger.info(f"注册快捷键: {shortcut_str} - {description}")
            return True
            
        except Exception as e:
            self.logger.error(f"注册快捷键失败 {key}: {e}")
            return False
    
    def unregister_shortcut(self, key: str, modifiers: List[KeyModifier] = None) -> bool:
        """取消注册快捷键"""
        try:
            if modifiers is None:
                modifiers = []
            
            shortcut_str = self._generate_shortcut_string(key, modifiers)
            
            if shortcut_str in self.shortcuts:
                del self.shortcuts[shortcut_str]
                self._unbind_shortcut(shortcut_str)
                self.logger.info(f"取消注册快捷键: {shortcut_str}")
                return True
            else:
                self.logger.warning(f"快捷键 {shortcut_str} 不存在")
                return False
                
        except Exception as e:
            self.logger.error(f"取消注册快捷键失败: {e}")
            return False
    
    def _generate_shortcut_string(self, key: str, modifiers: List[KeyModifier]) -> str:
        """生成快捷键字符串"""
        parts = []
        
        # 修饰符顺序：Control, Alt, Shift
        modifier_order = [KeyModifier.CTRL, KeyModifier.ALT, KeyModifier.SHIFT]
        for modifier in modifier_order:
            if modifier in modifiers:
                if self.is_mac and modifier == KeyModifier.CTRL:
                    parts.append("Cmd")
                else:
                    parts.append(modifier.value)
        
        parts.append(key)
        
        return "<" + "-".join(parts) + ">"
    
    def _bind_shortcut(self, shortcut_str: str):
        """绑定快捷键到tkinter"""
        self.root_widget.bind_all(shortcut_str, self._on_shortcut)
    
    def _unbind_shortcut(self, shortcut_str: str):
        """解绑快捷键"""
        try:
            self.root_widget.unbind_all(shortcut_str)
        except Exception as e:
            self.logger.debug(f"解绑快捷键失败 {shortcut_str}: {e}")
    
    def _on_key_press(self, event):
        """处理按键按下事件"""
        # 可以在这里添加全局按键处理逻辑
        pass
    
    def _on_key_release(self, event):
        """处理按键释放事件"""
        pass
    
    def _on_shortcut(self, event):
        """处理快捷键事件"""
        try:
            shortcut_str = str(event.keysym_num) if hasattr(event, 'keysym_num') else str(event.keysym)
            
            # 构造完整的快捷键字符串
            parts = []
            if event.state & 0x4:  # Control
                parts.append("Control")
            if event.state & 0x8:  # Alt
                parts.append("Alt")
            if event.state & 0x1:  # Shift
                parts.append("Shift")
            
            parts.append(event.keysym)
            full_shortcut = "<" + "-".join(parts) + ">"
            
            # 查找匹配的快捷键
            shortcut = self.shortcuts.get(full_shortcut)
            if shortcut and shortcut.enabled:
                # 检查上下文
                if self._is_context_active(shortcut.context):
                    try:
                        shortcut.callback()
                        self.logger.debug(f"执行快捷键: {full_shortcut}")
                        return "break"  # 阻止事件传播
                    except Exception as e:
                        self.logger.error(f"执行快捷键回调失败 {full_shortcut}: {e}")
            
        except Exception as e:
            self.logger.debug(f"处理快捷键事件失败: {e}")
    
    def _is_context_active(self, context: str) -> bool:
        """检查上下文是否激活"""
        return context == "global" or context in self.context_stack
    
    def push_context(self, context: str):
        """推入新的上下文"""
        if context not in self.context_stack:
            self.context_stack.append(context)
            self.logger.debug(f"推入上下文: {context}")
    
    def pop_context(self, context: str = None):
        """弹出上下文"""
        if context is None:
            if len(self.context_stack) > 1:  # 保留global上下文
                removed = self.context_stack.pop()
                self.logger.debug(f"弹出上下文: {removed}")
        else:
            if context in self.context_stack and context != "global":
                self.context_stack.remove(context)
                self.logger.debug(f"移除上下文: {context}")
    
    def get_current_context(self) -> str:
        """获取当前上下文"""
        return self.context_stack[-1] if self.context_stack else "global"
    
    def enable_shortcut(self, key: str, modifiers: List[KeyModifier] = None):
        """启用快捷键"""
        shortcut_str = self._generate_shortcut_string(key, modifiers or [])
        if shortcut_str in self.shortcuts:
            self.shortcuts[shortcut_str].enabled = True
    
    def disable_shortcut(self, key: str, modifiers: List[KeyModifier] = None):
        """禁用快捷键"""
        shortcut_str = self._generate_shortcut_string(key, modifiers or [])
        if shortcut_str in self.shortcuts:
            self.shortcuts[shortcut_str].enabled = False
    
    def get_shortcuts_by_context(self, context: str = None) -> List[Shortcut]:
        """获取指定上下文的快捷键"""
        if context is None:
            context = self.get_current_context()
        
        return [s for s in self.shortcuts.values() if s.context == context]
    
    def get_all_shortcuts(self) -> Dict[str, Shortcut]:
        """获取所有快捷键"""
        return self.shortcuts.copy()
    
    def register_common_shortcuts(self, app_controller):
        """注册常用快捷键"""
        common_shortcuts = [
            # 文件操作
            ("o", [KeyModifier.CTRL], "打开文件", lambda: app_controller.add_files()),
            ("s", [KeyModifier.CTRL], "保存配置", lambda: app_controller.save_config()),
            ("q", [KeyModifier.CTRL], "退出应用", lambda: app_controller.on_close()),
            
            # 翻译操作
            ("t", [KeyModifier.CTRL], "开始翻译", lambda: app_controller.start_translation()),
            ("Escape", [], "停止翻译", lambda: app_controller.stop_translation() if hasattr(app_controller, 'stop_translation') else None),
            
            # 视图切换
            ("e", [KeyModifier.CTRL], "切换到编辑器", lambda: app_controller.show_view("EditorView")),
            ("m", [KeyModifier.CTRL], "切换到主视图", lambda: app_controller.show_view("MainView")),
            
            # 列表操作
            ("a", [KeyModifier.CTRL], "全选文件", lambda: self._select_all_files(app_controller)),
            ("Delete", [], "删除选中文件", lambda: app_controller.remove_selected_files() if hasattr(app_controller, 'remove_selected_files') else None),
            ("F5", [], "刷新", lambda: app_controller.refresh() if hasattr(app_controller, 'refresh') else None),
            
            # 编辑器操作
            ("z", [KeyModifier.CTRL], "撤销", lambda: self._undo_action(app_controller), "editor"),
            ("y", [KeyModifier.CTRL], "重做", lambda: self._redo_action(app_controller), "editor"),
            ("c", [KeyModifier.CTRL], "复制", lambda: self._copy_action(app_controller), "editor"),
            ("v", [KeyModifier.CTRL], "粘贴", lambda: self._paste_action(app_controller), "editor"),
            
            # 帮助
            ("F1", [], "显示帮助", lambda: self._show_help()),
        ]
        
        for shortcut_data in common_shortcuts:
            key, modifiers, description, callback = shortcut_data[:4]
            context = shortcut_data[4] if len(shortcut_data) > 4 else "global"
            
            self.register_shortcut(key, callback, modifiers, description, context)
    
    def _select_all_files(self, app_controller):
        """全选文件"""
        if hasattr(app_controller, 'main_view_widgets') and 'file_listbox' in app_controller.main_view_widgets:
            listbox = app_controller.main_view_widgets['file_listbox']
            listbox.select_set(0, 'end')
    
    def _undo_action(self, app_controller):
        """撤销操作"""
        # 实现撤销逻辑
        pass
    
    def _redo_action(self, app_controller):
        """重做操作"""
        # 实现重做逻辑
        pass
    
    def _copy_action(self, app_controller):
        """复制操作"""
        # 实现复制逻辑
        pass
    
    def _paste_action(self, app_controller):
        """粘贴操作"""
        # 实现粘贴逻辑
        pass
    
    def _show_help(self):
        """显示帮助"""
        import tkinter.messagebox as msgbox
        
        help_text = "快捷键帮助:\n\n"
        shortcuts = self.get_shortcuts_by_context("global")
        for shortcut in shortcuts:
            if shortcut.description:
                key_str = self._format_shortcut_display(shortcut)
                help_text += f"{key_str}: {shortcut.description}\n"
        
        msgbox.showinfo("快捷键帮助", help_text)
    
    def _format_shortcut_display(self, shortcut: Shortcut) -> str:
        """格式化快捷键显示"""
        parts = []
        
        for modifier in shortcut.modifiers:
            if modifier == KeyModifier.CTRL:
                parts.append("Ctrl" if not self.is_mac else "Cmd")
            elif modifier == KeyModifier.ALT:
                parts.append("Alt")
            elif modifier == KeyModifier.SHIFT:
                parts.append("Shift")
        
        parts.append(shortcut.key)
        
        return "+".join(parts)
    
    def export_shortcuts(self, file_path: str) -> bool:
        """导出快捷键配置"""
        try:
            import json
            
            shortcuts_data = {}
            for key, shortcut in self.shortcuts.items():
                shortcuts_data[key] = {
                    'key': shortcut.key,
                    'modifiers': [m.value for m in shortcut.modifiers],
                    'description': shortcut.description,
                    'context': shortcut.context,
                    'enabled': shortcut.enabled
                }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(shortcuts_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"导出快捷键配置到: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"导出快捷键配置失败: {e}")
            return False
    
    def import_shortcuts(self, file_path: str) -> bool:
        """导入快捷键配置"""
        try:
            import json
            
            with open(file_path, 'r', encoding='utf-8') as f:
                shortcuts_data = json.load(f)
            
            for key, data in shortcuts_data.items():
                modifiers = [KeyModifier(m) for m in data.get('modifiers', [])]
                # 注意：这里无法恢复callback函数，需要重新注册
                # 这个功能主要用于保存和恢复快捷键配置
            
            self.logger.info(f"导入快捷键配置从: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"导入快捷键配置失败: {e}")
            return False
    
    def register_shortcut_simple(self, key_combo: str, callback: Callable, context: str = "global", description: str = "") -> bool:
        """
        简化的快捷键注册方法
        
        Args:
            key_combo: 快捷键组合，如 'ctrl+s', 'alt+f4'
            callback: 回调函数
            context: 上下文
            description: 描述
        """
        try:
            # 解析键的组合
            parts = key_combo.lower().split('+')
            modifiers = []
            key = parts[-1]  # 最后一个是主键
            
            # 解析修饰符
            for part in parts[:-1]:
                if part == 'ctrl':
                    modifiers.append(KeyModifier.CTRL)
                elif part == 'alt':
                    modifiers.append(KeyModifier.ALT)
                elif part == 'shift':
                    modifiers.append(KeyModifier.SHIFT)
            
            # 特殊键名映射
            key_mapping = {
                'plus': 'plus',
                'minus': 'minus',
                'equal': 'equal',
                'delete': 'Delete',
                'return': 'Return',
                'escape': 'Escape',
                'space': 'space',
                'tab': 'Tab'
            }
            
            key = key_mapping.get(key, key)
            
            return self.register_shortcut(key, callback, modifiers, description, context)
            
        except Exception as e:
            self.logger.error(f"注册快捷键失败 {key_combo}: {e}")
            return False
    
    def activate_context(self, context: str):
        """激活上下文（新的别名方法）"""
        self.push_context(context)
    
    def get_shortcuts_info(self) -> List[Dict[str, str]]:
        """获取快捷键信息列表"""
        info_list = []
        for shortcut in self.shortcuts.values():
            if shortcut.enabled and shortcut.description:
                key_str = self._format_shortcut_display(shortcut)
                info_list.append({
                    'key': key_str,
                    'description': shortcut.description,
                    'context': shortcut.context
                })
        return info_list