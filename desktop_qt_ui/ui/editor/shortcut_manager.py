"""
快捷键管理模块
负责统一管理Qt UI的所有快捷键设置和处理
"""

from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QWidget


class ShortcutManager(QObject):
    """
    快捷键管理器
    统一管理应用程序的所有快捷键
    """
    
    def __init__(self, parent: QWidget):
        """
        初始化快捷键管理器
        
        Args:
            parent: 父窗口部件
        """
        super().__init__(parent)
        self.parent_widget = parent
        self.shortcuts = {}
    
    def register_shortcut(
        self,
        name: str,
        key_sequence: QKeySequence.StandardKey,
        callback: Callable,
        context_aware: bool = False
    ) -> QShortcut:
        """
        注册一个快捷键
        
        Args:
            name: 快捷键名称（用于标识）
            key_sequence: 按键序列
            callback: 回调函数
            context_aware: 是否需要上下文感知（检查焦点控件）
            
        Returns:
            创建的QShortcut对象
        """
        shortcut = QShortcut(key_sequence, self.parent_widget)
        
        if context_aware:
            # 包装回调函数，添加上下文检查
            def context_aware_callback():
                focused_widget = self.parent_widget.focusWidget()
                callback(focused_widget)
            shortcut.activated.connect(context_aware_callback)
        else:
            shortcut.activated.connect(callback)
        
        self.shortcuts[name] = shortcut
        return shortcut
    
    def get_shortcut(self, name: str) -> Optional[QShortcut]:
        """
        获取快捷键对象
        
        Args:
            name: 快捷键名称
            
        Returns:
            QShortcut对象，如果不存在则返回None
        """
        return self.shortcuts.get(name)
    
    @staticmethod
    def is_text_widget(widget) -> bool:
        """
        检查控件是否为文本编辑控件
        
        Args:
            widget: 要检查的控件
            
        Returns:
            是否为文本编辑控件
        """
        return isinstance(widget, (QTextEdit, QLineEdit))


class EditorShortcutManager(ShortcutManager):
    """
    编辑器快捷键管理器
    专门用于编辑器视图的快捷键管理
    """
    
    def __init__(self, editor_view):
        """
        初始化编辑器快捷键管理器
        
        Args:
            editor_view: 编辑器视图对象
        """
        super().__init__(editor_view)
        self.editor_view = editor_view
        self.controller = editor_view.controller
        self._setup_editor_shortcuts()
        self._setup_wheel_shortcuts()
    
    def _setup_editor_shortcuts(self):
        """设置编辑器的所有快捷键"""
        # 撤销快捷键
        self.register_shortcut(
            'undo',
            QKeySequence.StandardKey.Undo,
            self._handle_undo,
            context_aware=True
        )
        
        # 重做快捷键
        self.register_shortcut(
            'redo',
            QKeySequence.StandardKey.Redo,
            self._handle_redo,
            context_aware=True
        )
        
        # 复制快捷键
        self.register_shortcut(
            'copy',
            QKeySequence.StandardKey.Copy,
            self._handle_copy,
            context_aware=True
        )
        
        # 粘贴快捷键
        self.register_shortcut(
            'paste',
            QKeySequence.StandardKey.Paste,
            self._handle_paste,
            context_aware=True
        )

        # 全选快捷键
        self.register_shortcut(
            'select_all',
            QKeySequence.StandardKey.SelectAll,
            self._handle_select_all,
            context_aware=True
        )
        
        # 删除快捷键
        self.register_shortcut(
            'delete',
            QKeySequence.StandardKey.Delete,
            self._handle_delete,
            context_aware=True
        )
        
        # 导出快捷键 (Ctrl+Q)
        self.register_shortcut(
            'export',
            QKeySequence("Ctrl+Q"),
            self._handle_export,
            context_aware=True
        )
        
        # 工具快捷键 Q (选择)
        self.register_shortcut(
            'tool_select',
            QKeySequence("Q"),
            self._handle_tool_select,
            context_aware=True
        )
        
        # 工具快捷键 W (画笔)
        self.register_shortcut(
            'tool_brush',
            QKeySequence("W"),
            self._handle_tool_brush,
            context_aware=True
        )
        
        # 工具快捷键 E (橡皮擦)
        self.register_shortcut(
            'tool_eraser',
            QKeySequence("E"),
            self._handle_tool_eraser,
            context_aware=True
        )

        # 上一张图片 (A)
        self.register_shortcut(
            'prev_image',
            QKeySequence("A"),
            self._handle_prev_image,
            context_aware=True
        )
        
        # 下一张图片 (D)
        self.register_shortcut(
            'next_image',
            QKeySequence("D"),
            self._handle_next_image,
            context_aware=True
        )
    
    def _handle_undo(self, focused_widget):
        """处理撤销快捷键"""
        if self.is_text_widget(focused_widget):
            # 如果焦点在文本控件上，让文本控件处理撤销
            focused_widget.undo()
        else:
            # 否则调用编辑器的撤销
            self.controller.undo()
    
    def _handle_redo(self, focused_widget):
        """处理重做快捷键"""
        if self.is_text_widget(focused_widget):
            # 如果焦点在文本控件上，让文本控件处理重做
            focused_widget.redo()
        else:
            # 否则调用编辑器的重做
            self.controller.redo()
    
    def _handle_copy(self, focused_widget):
        """处理复制快捷键"""
        if self.is_text_widget(focused_widget):
            # 如果焦点在文本控件上，让文本控件处理复制
            focused_widget.copy()
        else:
            # 否则复制选中的区域
            selected_regions = self.editor_view.model.get_selection()
            if selected_regions:
                # 复制最后选中的区域
                self.controller.copy_region(selected_regions[-1])
    
    def _handle_paste(self, focused_widget):
        """处理粘贴快捷键"""
        if self.is_text_widget(focused_widget):
            # 如果焦点在文本控件上，让文本控件处理粘贴
            focused_widget.paste()
        else:
            # 否则根据是否有选中区域决定粘贴行为
            selected_regions = self.editor_view.model.get_selection()
            if selected_regions and len(selected_regions) == 1:
                # 有单个选中区域时，粘贴样式
                self.controller.paste_region_style(selected_regions[0])
            else:
                # 无选中区域时，粘贴新区域到鼠标位置
                from PyQt6.QtGui import QCursor
                if self.editor_view.graphics_view and self.editor_view.graphics_view._image_item:
                    mouse_pos_scene = self.editor_view.graphics_view.mapToScene(
                        self.editor_view.graphics_view.mapFromGlobal(QCursor.pos())
                    )
                    mouse_pos_image = self.editor_view.graphics_view._image_item.mapFromScene(mouse_pos_scene)
                    self.controller.paste_region(mouse_pos_image)
                else:
                    self.controller.paste_region()
    
    def _handle_select_all(self, focused_widget):
        """处理全选快捷键"""
        if self.is_text_widget(focused_widget):
            focused_widget.selectAll()
        else:
            regions = self.editor_view.model.get_regions()
            self.editor_view.model.set_selection(list(range(len(regions))))

    def _handle_delete(self, focused_widget):
        """处理删除快捷键"""
        if not self.is_text_widget(focused_widget):
            # 只有在非文本控件上才处理删除区域
            selected_regions = self.editor_view.model.get_selection()
            if selected_regions:
                self.controller.delete_regions(selected_regions)
    
    def _handle_export(self, focused_widget):
        """处理导出快捷键 (Ctrl+Q)"""
        # 与工具栏共用同一入口，确保读取模型前先 flush 富文本正文和 Ruby。
        self.editor_view.export_image()
        
    def _forward_key_to_widget(self, widget, key_code, text, shortcut_name):
        """
        将按键事件转发给控件，同时临时禁用对应的快捷键以防止递归
        """
        shortcut = self.get_shortcut(shortcut_name)
        if shortcut:
            shortcut.setEnabled(False)
            
            # 发送KeyPress
            event_press = QKeyEvent(QEvent.Type.KeyPress, key_code, Qt.KeyboardModifier.NoModifier, text)
            QApplication.sendEvent(widget, event_press)
            
            # 发送KeyRelease (部分输入法或控件可能依赖它)
            event_release = QKeyEvent(QEvent.Type.KeyRelease, key_code, Qt.KeyboardModifier.NoModifier, text)
            QApplication.sendEvent(widget, event_release)
            
            shortcut.setEnabled(True)

    def _handle_tool_select(self, focused_widget):
        """处理选择工具快捷键 (Q)"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, Qt.Key.Key_Q, "q", 'tool_select')
        else:
            self.controller.set_active_tool('select')

    def _handle_tool_brush(self, focused_widget):
        """处理画笔工具快捷键 (W)"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, Qt.Key.Key_W, "w", 'tool_brush')
        else:
            self.controller.set_active_tool('brush')

    def _handle_tool_eraser(self, focused_widget):
        """处理橡皮擦工具快捷键 (E)"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, Qt.Key.Key_E, "e", 'tool_eraser')
        else:
            self.controller.set_active_tool('eraser')

    def _handle_prev_image(self, focused_widget):
        """处理上一张图片快捷键 (A)"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, Qt.Key.Key_A, "a", 'prev_image')
        else:
            if hasattr(self.editor_view, 'file_list'):
                self.editor_view.file_list.select_prev_image()

    def _handle_next_image(self, focused_widget):
        """处理下一张图片快捷键 (D)"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, Qt.Key.Key_D, "d", 'next_image')
        else:
            if hasattr(self.editor_view, 'file_list'):
                self.editor_view.file_list.select_next_image()

    def _setup_wheel_shortcuts(self):
        """设置鼠标滚轮快捷键（通过事件过滤器实现）"""
        # 为 graphics_view 的 viewport 安装事件过滤器
        if hasattr(self.editor_view, 'graphics_view'):
            # 滚轮事件会先到达 viewport
            self.editor_view.graphics_view.viewport().installEventFilter(self)
    
    def eventFilter(self, obj, event):
        """
        事件过滤器，用于处理鼠标滚轮快捷键
        
        支持的快捷键：
        - Ctrl + 滚轮：等比例缩放选中文本框（包括框的大小和字体）
        - Shift + 滚轮：调整蒙版画笔大小
        """
        if event.type() == QEvent.Type.Wheel:
            # 检查是否是 graphics_view 的 viewport
            if obj == self.editor_view.graphics_view.viewport():
                modifiers = event.modifiers()
                
                # Shift + 滚轮：调整画笔大小（无论当前是什么工具）
                if modifiers == Qt.KeyboardModifier.ShiftModifier:
                    current_size = self.editor_view.model.get_brush_size()
                    # 尝试获取滚轮方向
                    angle_delta = event.angleDelta().y()
                    if angle_delta == 0:
                        angle_delta = event.pixelDelta().y()
                    
                    delta = 1 if angle_delta > 0 else -1
                    new_size = max(5, min(200, current_size + delta))
                    self.controller.set_brush_size(new_size)
                    return True  # 阻止事件继续传递
                
                # Ctrl + 滚轮：调整选中文本框的字体大小
                elif modifiers == Qt.KeyboardModifier.ControlModifier:
                    selected_regions = self.editor_view.model.get_selection()
                    if selected_regions:
                        angle_delta = event.angleDelta().y()
                        if angle_delta == 0:
                            angle_delta = event.pixelDelta().y()
                        for region_index in selected_regions:
                            region_data = self.controller._get_region_by_index(region_index)
                            if region_data:
                                old_size = region_data.get('font_size', 20)
                                delta = max(1, int(old_size * 0.05))
                                new_size = max(1, old_size + (delta if angle_delta > 0 else -delta))
                                self.controller.update_font_size(region_index, new_size)
                        return True  # 阻止事件继续传递
        
        # 其他事件继续传递
        return super().eventFilter(obj, event)
