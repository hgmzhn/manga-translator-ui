"""
快捷键管理模块
负责统一管理Qt UI的所有快捷键设置和处理
"""

from functools import partial
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
        context_aware: bool = False,
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
                # parent_widget.focusWidget() 不跨窗口：焦点在浮动编辑器
                # （Qt.Tool 顶层窗）里时它仍返回主窗口内旧焦点，导致误删画布选中区。
                focused_widget = QApplication.focusWidget()
                if (
                    focused_widget is not None
                    and focused_widget.window() is not self.parent_widget.window()
                ):
                    # 焦点在其它顶层窗口（如浮动富文本编辑器）：编辑器快捷键一律不处理
                    return
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
        """Register editor shortcuts from one explicit policy table."""
        panel = self.editor_view.property_panel
        shortcuts = (
            ("undo", QKeySequence.StandardKey.Undo, self._handle_undo, True),
            ("redo", QKeySequence.StandardKey.Redo, self._handle_redo, True),
            ("copy", QKeySequence.StandardKey.Copy, self._handle_copy, True),
            ("paste", QKeySequence.StandardKey.Paste, self._handle_paste, True),
            (
                "select_all",
                QKeySequence.StandardKey.SelectAll,
                self._handle_select_all,
                True,
            ),
            ("delete", QKeySequence.StandardKey.Delete, self._handle_delete, True),
            ("save", QKeySequence.StandardKey.Save, self._handle_save, True),
            ("export", QKeySequence("Ctrl+Q"), self._handle_export, True),
            (
                "toggle_rich_text_popup",
                QKeySequence("Ctrl+Shift+R"),
                self._handle_toggle_rich_text_popup,
                False,
            ),
            (
                "tool_select",
                QKeySequence("Q"),
                partial(
                    self._handle_panel_shortcut,
                    0,
                    Qt.Key.Key_Q,
                    "q",
                    "tool_select",
                    panel.activate_image_edit_tool,
                ),
                True,
            ),
            (
                "tool_brush",
                QKeySequence("W"),
                partial(
                    self._handle_panel_shortcut,
                    1,
                    Qt.Key.Key_W,
                    "w",
                    "tool_brush",
                    panel.activate_image_edit_tool,
                ),
                True,
            ),
            (
                "tool_eraser",
                QKeySequence("E"),
                partial(
                    self._handle_panel_shortcut,
                    2,
                    Qt.Key.Key_E,
                    "e",
                    "tool_eraser",
                    panel.activate_image_edit_tool,
                ),
                True,
            ),
            (
                "image_edit_tab_mask",
                QKeySequence("1"),
                partial(
                    self._handle_panel_shortcut,
                    0,
                    Qt.Key.Key_1,
                    "1",
                    "image_edit_tab_mask",
                    panel.activate_image_edit_tab,
                ),
                True,
            ),
            (
                "image_edit_tab_paint",
                QKeySequence("2"),
                partial(
                    self._handle_panel_shortcut,
                    1,
                    Qt.Key.Key_2,
                    "2",
                    "image_edit_tab_paint",
                    panel.activate_image_edit_tab,
                ),
                True,
            ),
            (
                "image_edit_tab_stamp",
                QKeySequence("3"),
                partial(
                    self._handle_panel_shortcut,
                    2,
                    Qt.Key.Key_3,
                    "3",
                    "image_edit_tab_stamp",
                    panel.activate_image_edit_tab,
                ),
                True,
            ),
            (
                "prev_image",
                QKeySequence("A"),
                partial(
                    self._handle_navigation,
                    Qt.Key.Key_A,
                    "a",
                    "prev_image",
                    self.editor_view.file_list.select_prev_image,
                ),
                True,
            ),
            (
                "next_image",
                QKeySequence("D"),
                partial(
                    self._handle_navigation,
                    Qt.Key.Key_D,
                    "d",
                    "next_image",
                    self.editor_view.file_list.select_next_image,
                ),
                True,
            ),
            (
                "toggle_text_direction",
                QKeySequence("V"),
                self._handle_toggle_text_direction,
                True,
            ),
        )
        for name, key, callback, context_aware in shortcuts:
            self.register_shortcut(name, key, callback, context_aware)

        # This one must also close the Qt.Tool popup when that window has focus.
        self.get_shortcut("toggle_rich_text_popup").setContext(
            Qt.ShortcutContext.ApplicationShortcut
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

                if (
                    self.editor_view.graphics_view
                    and self.editor_view.graphics_view._image_item
                ):
                    mouse_pos_scene = self.editor_view.graphics_view.mapToScene(
                        self.editor_view.graphics_view.mapFromGlobal(QCursor.pos())
                    )
                    mouse_pos_image = (
                        self.editor_view.graphics_view._image_item.mapFromScene(
                            mouse_pos_scene
                        )
                    )
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
                return
            # 无选中区域时，若画布上有选中的贴片则删除贴片
            graphics_view = getattr(self.editor_view, "graphics_view", None)
            if (
                graphics_view is not None
                and getattr(graphics_view, "_selected_paste_overlay_id", None)
            ):
                graphics_view.delete_selected_paste_overlay()

    def _handle_save(self, focused_widget):
        """处理保存快捷键 (Ctrl+S)。"""
        self.editor_view.save_editor_state()

    def _handle_export(self, focused_widget):
        """处理导出快捷键 (Ctrl+Q)"""
        # 与工具栏共用同一入口，确保读取模型前先 flush 富文本正文和 Ruby。
        self.editor_view.export_image()

    def _handle_toggle_rich_text_popup(self):
        """切换富文本浮动编辑器显示状态 (Ctrl+Shift+R)。"""
        if not self.editor_view.isVisible():
            return
        toolbar = getattr(self.editor_view, "toolbar", None)
        if toolbar is None:
            return
        toolbar.set_rich_text_popup_enabled(
            not toolbar.is_rich_text_popup_enabled(), emit=True
        )

    def _forward_key_to_widget(self, widget, key_code, text, shortcut_name):
        """Forward a text key while preventing its editor shortcut from recurring."""
        shortcut = self.get_shortcut(shortcut_name)
        if shortcut is None:
            return
        shortcut.setEnabled(False)
        try:
            for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(
                    widget,
                    QKeyEvent(
                        event_type,
                        key_code,
                        Qt.KeyboardModifier.NoModifier,
                        text,
                    ),
                )
        finally:
            shortcut.setEnabled(True)

    def _handle_panel_shortcut(
        self, index, key_code, text, name, activate, focused_widget
    ):
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, key_code, text, name)
        else:
            activate(index)

    def _handle_toggle_text_direction(self, focused_widget):
        """按 V 在横排与竖排之间切换选中文本框。"""
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(
                focused_widget, Qt.Key.Key_V, "v", "toggle_text_direction"
            )
            return

        selected_regions = self.editor_view.model.get_selection()
        if not selected_regions:
            return

        regions = self.editor_view.model.get_regions()
        anchor_index = selected_regions[-1]
        if not 0 <= anchor_index < len(regions):
            return

        anchor_region = regions[anchor_index]
        direction = str(anchor_region.get("direction", "")).strip().lower()
        if direction in ("v", "vertical"):
            is_vertical = True
        elif direction in ("h", "horizontal"):
            is_vertical = False
        else:
            white_frame = self.editor_view.property_panel._calculate_white_frame_info(
                anchor_region
            )
            is_vertical = bool(white_frame and white_frame[3] > white_frame[2])

        next_direction = "horizontal" if is_vertical else "vertical"
        self.controller.update_region_style_patch(
            selected_regions, {"direction": next_direction}
        )

    def _handle_navigation(self, key_code, text, name, navigate, focused_widget):
        if self.is_text_widget(focused_widget):
            self._forward_key_to_widget(focused_widget, key_code, text, name)
        else:
            navigate()

    def _setup_wheel_shortcuts(self):
        """设置鼠标滚轮快捷键（通过事件过滤器实现）"""
        # 为 graphics_view 的 viewport 安装事件过滤器
        if hasattr(self.editor_view, "graphics_view"):
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
                    self.editor_view.model.set_brush_size(new_size)
                    return True  # 阻止事件继续传递

                # Ctrl + 滚轮（含 Ctrl+Shift 等组合）：调整选中文本框的字体大小；
                # 无文本框选中但有选中贴片时，等比缩放贴片。
                # 无论有无选中都吞掉事件——这是"调字号/缩放贴片"语义，
                # 决不能穿透成画布缩放，让用户以为在调字号实际在缩放。
                elif modifiers & Qt.KeyboardModifier.ControlModifier:
                    selected_regions = self.editor_view.model.get_selection()
                    angle_delta = event.angleDelta().y()
                    if angle_delta == 0:
                        angle_delta = event.pixelDelta().y()
                    if selected_regions:
                        for region_index in selected_regions:
                            region_data = self.editor_view.model.get_region_by_index(
                                region_index
                            )
                            if region_data:
                                old_size = region_data.get("font_size", 20)
                                delta = max(1, int(old_size * 0.05))
                                new_size = max(
                                    1,
                                    old_size
                                    + (delta if angle_delta > 0 else -delta),
                                )
                                self.controller.update_font_size(
                                    region_index, new_size
                                )
                    else:
                        graphics_view = getattr(
                            self.editor_view, "graphics_view", None
                        )
                        overlay_id = getattr(
                            graphics_view, "_selected_paste_overlay_id", None
                        )
                        if overlay_id:
                            step = 1.05 if angle_delta > 0 else 1.0 / 1.05
                            for overlay in self.editor_view.model.get_paste_overlays():
                                if overlay.get("id") == overlay_id:
                                    width = float(overlay.get("width", 1.0))
                                    height = float(overlay.get("height", 1.0))
                                    self.controller.update_paste_overlay(
                                        overlay_id,
                                        {
                                            "width": round(width * step, 1),
                                            "height": round(height * step, 1),
                                        },
                                    )
                                    break
                    return True  # 阻止事件继续传递

        # 其他事件继续传递
        return super().eventFilter(obj, event)
