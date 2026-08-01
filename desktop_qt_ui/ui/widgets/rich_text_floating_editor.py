from __future__ import annotations

import copy
from collections.abc import Iterable

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QSizePolicy, QTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import SimpleCardWidget, VerticalSeparator, themeColor

from editor.rich_text_editing import (
    apply_ruby_to_range,
    apply_style_to_range,
    apply_tcy_to_range,
    clear_styles_from_range,
    remove_ruby_from_range,
    remove_tcy_from_range,
    style_for_range,
    style_row_coverage,
    styled_segments_for_range,
    utf16_range_to_python_range,
)
from editor.rich_text_editor_state import RichTextEditorState
from editor.rich_text_presets import RichTextPresetStore, normalize_rich_text_preset
from services import get_config_service, get_i18n_manager
from ui.secondary_pages.themed_message_box import themed_critical, themed_question, themed_warning
from ui.secondary_pages.themed_text_input_dialog import themed_get_text
from utils.font_list import FontComboBox

from .color_picker import ColorPickerWidget
from .rich_text_editor_components import (
    STYLE_KEYS,
    RichTextBodyEdit,
    RichTextPresetSidebar,
    RichTextToolbar,
    StyledRunList,
    clear_style_patch,
    default_style_patch,
)


class RichTextFloatingEditor(SimpleCardWidget):
    """Independent floating ``richtext.v1`` editor.

    The editor is a top-level tool window rather than a child overlay of the
    canvas. Child widgets are clipped by the canvas viewport and cannot be
    dragged onto another panel or monitor.
    """

    rich_text_changed = pyqtSignal(int, object, str)
    # ``True`` means the consumer should preserve the current top edge while
    # applying the new size (important for an editor docked above the canvas).
    layout_size_changed = pyqtSignal(bool)

    _DRAG_BORDER_WIDTH = 12
    _WINDOW_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
    _MAIN_PANEL_WIDTH = 440

    def __init__(self, parent=None):
        super().__init__(parent)
        # Keep the editor page as the QObject owner while making this widget a
        # real top-level tool window. ``Tool`` keeps it associated with the
        # application without putting a task-bar entry on Windows.
        self.setWindowFlags(self._WINDOW_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        # Showing the editor for a newly selected region must not steal focus
        # from the canvas; clicking the editor still focuses it normally.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setMinimumHeight(210)

        self._state = RichTextEditorState()
        self._updating = False
        self._applying_own_change = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._manually_positioned = False

        self._emit_debounce_timer = QTimer(self)
        self._emit_debounce_timer.setSingleShot(True)
        self._emit_debounce_timer.setInterval(180)
        self._emit_debounce_timer.timeout.connect(self._emit_pending_document)

        # All geometry changes go through this coalesced refresh.  Rebuilding
        # style cards uses ``deleteLater()``, so measuring the window from the
        # signal handler itself can observe a mixture of old and new cards.
        self._layout_refresh_timer = QTimer(self)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.timeout.connect(self._apply_queued_layout_refresh)
        self._layout_position_signal_pending = False

        self.i18n = get_i18n_manager()
        self.config_service = get_config_service()
        self._preset_store = RichTextPresetStore(self.config_service)
        self._state.auto_rules_provider = self._auto_rich_text_rules_enabled

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.main_panel = QWidget(self)
        self.main_panel.setFixedWidth(self._MAIN_PANEL_WIDTH)
        layout = QVBoxLayout(self.main_panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.text_box = RichTextBodyEdit(self.main_panel)
        text_font = self.text_box.font()
        text_font.setPointSize(14)
        self.text_box.setFont(text_font)
        self.text_box.setFixedHeight(120)
        self.text_box.setUndoRedoEnabled(True)
        layout.addWidget(self.text_box)

        self.toolbar = RichTextToolbar(self.main_panel)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.toolbar)

        # One card per actual contiguous run; every property is its own row.
        self.run_list = StyledRunList(self.main_panel)
        layout.addWidget(self.run_list)

        self.preset_sidebar = RichTextPresetSidebar(self)
        self.preset_sidebar.setFixedHeight(self.minimumHeight())
        self._sidebar_separator = VerticalSeparator(self)
        root_layout.addWidget(self.main_panel)
        root_layout.addWidget(self._sidebar_separator)
        root_layout.addWidget(self.preset_sidebar)
        self._sync_window_width_to_sidebar()
        self._refresh_preset_sidebar()

        self._connect_signals()
        self.hide()

    # ------------------------------------------------------------------
    # Required public interface
    # ------------------------------------------------------------------

    def set_region(self, region_index: int, region_data: dict):
        self.flush_pending_changes()
        display_text = self._state.bind_region(region_index, region_data)
        self._updating = True
        try:
            # Rebuild unconditionally so equal-text regions never share cursor/undo state.
            self.text_box.setUndoRedoEnabled(False)
            self.text_box.setPlainText(display_text)
            cursor = self.text_box.textCursor()
            cursor.setPosition(0)
            self.text_box.setTextCursor(cursor)
            self.text_box.setUndoRedoEnabled(True)
            self._state.set_selection(0, 0)
            self.text_box.setExtraSelections([])
            self._refresh_inspector()
        finally:
            self._updating = False

    def clear_region(self):
        self.flush_pending_changes()
        self._state.clear_region()
        self._updating = True
        try:
            self.text_box.setUndoRedoEnabled(False)
            self.text_box.clear()
            self.text_box.setUndoRedoEnabled(True)
            self.text_box.setExtraSelections([])
            self.run_list.set_segments([])
        finally:
            self._updating = False
        self._queue_layout_refresh()
        self.hide()

    def flush_pending_changes(self):
        """Synchronously commit both body debounce and the fixed Ruby draft."""
        if self._state.ruby_draft is not None:
            self._state.commit_ruby()
        self._emit_debounce_timer.stop()
        self._emit_pending_document()

    def refresh_region_if_changed(self, region_index: int, region_data: dict):
        if self._state.same_bound_content(region_index, region_data):
            self._state.refresh_cached_region_data(region_data)
            return
        self.set_region(region_index, region_data)

    def is_applying_own_change(self) -> bool:
        return self._applying_own_change

    def focus_text(self):
        self.text_box.setFocus()

    def is_manually_positioned(self) -> bool:
        return self._manually_positioned

    def reset_manual_position(self):
        self._manually_positioned = False

    def refresh_ui_texts(self) -> None:
        """Retranslate the complete floating editor after a locale change."""
        for combo in self.findChildren(FontComboBox):
            combo.refresh_ui_texts()
        self.toolbar.refresh_ui_texts()
        self.preset_sidebar.refresh_ui_texts()
        if self._state.has_region:
            self._refresh_inspector()
        else:
            self._queue_layout_refresh()

    def refresh_theme(self) -> None:
        """Fluent controls restyle themselves; push the change to color swatches."""
        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()
        self.update()

    # ------------------------------------------------------------------
    # Wiring and selection
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.text_box.document().contentsChange.connect(self._on_text_changed)
        self.text_box.cursorPositionChanged.connect(self._on_cursor_position_changed)
        self.text_box.focus_gained.connect(self._finish_active_ruby_before_target_change)
        self.text_box.focus_lost.connect(self.flush_pending_changes)
        self.toolbar.toggled.connect(self._on_toolbar_toggled)
        self.run_list.range_selected.connect(self._select_python_range)
        self.run_list.patch_requested.connect(self._apply_style_to_explicit_range)
        self.run_list.remove_requested.connect(self._remove_style_from_explicit_range)
        self.run_list.save_preset_requested.connect(self._save_preset_from_explicit_range)
        self.run_list.clear_styles_requested.connect(self._clear_all_styles_from_explicit_range)
        self.run_list.ruby_started.connect(self._start_ruby_edit)
        self.run_list.ruby_changed.connect(self._update_ruby_draft)
        self.run_list.ruby_apply_requested.connect(self._apply_ruby_text)
        self.run_list.ruby_finished.connect(self._finish_ruby_text)
        self.preset_sidebar.preset_applied.connect(self._apply_rich_text_preset)
        self.preset_sidebar.rename_requested.connect(self._rename_rich_text_preset)
        self.preset_sidebar.delete_requested.connect(self._delete_rich_text_preset)
        self.preset_sidebar.collapsed_changed.connect(self._on_preset_sidebar_collapsed)

    def _auto_rich_text_rules_enabled(self) -> bool:
        """编辑时自动应用富文本规则的开关（与编辑器菜单/配置共用）。"""
        service = self.config_service
        if service is None:
            return False
        try:
            config = service.get_config()
        except Exception:
            return False
        return bool(getattr(getattr(config, "app", None), "editor_auto_rich_text_rules", True))

    def _on_text_changed(self, position: int, chars_removed: int, chars_added: int):
        if self._updating or not self._state.has_region:
            return
        self._state.apply_qt_contents_change(
            self.text_box.toPlainText(),
            position,
            chars_removed,
            chars_added,
        )
        cursor = self.text_box.textCursor()
        self._state.set_selection_from_qt(cursor.selectionStart(), cursor.selectionEnd())
        self._emit_debounce_timer.start()
        self._refresh_inspector()

    def _on_cursor_position_changed(self):
        if self._updating or not self._state.has_region:
            return
        cursor = self.text_box.textCursor()
        new_range = utf16_range_to_python_range(
            self._state.editor_text,
            cursor.selectionStart(),
            cursor.selectionEnd(),
        )
        if new_range != self._state.selected_range:
            self._finish_active_ruby_before_target_change()
            self._state.set_selection(*new_range)
        self._update_selection_paint()
        self._refresh_inspector()

    def _select_python_range(self, start: int, end: int) -> None:
        if not self._state.has_region:
            return
        if (start, end) != self._state.selected_range:
            self._finish_active_ruby_before_target_change()
        self._state.set_selection(start, end)
        qt_start, qt_end = self._state.selection_as_qt_range()
        self._updating = True
        try:
            cursor = QTextCursor(self.text_box.document())
            cursor.setPosition(qt_start)
            cursor.setPosition(qt_end, QTextCursor.MoveMode.KeepAnchor)
            self.text_box.setTextCursor(cursor)
        finally:
            self._updating = False
        self._update_selection_paint()
        self._refresh_inspector()

    def _update_selection_paint(self) -> None:
        start, end = self._state.selected_range
        if start == end:
            self.text_box.setExtraSelections([])
            return
        qt_start, qt_end = self._state.selection_as_qt_range()
        cursor = QTextCursor(self.text_box.document())
        cursor.setPosition(qt_start)
        cursor.setPosition(qt_end, QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = QTextCharFormat()
        highlight = QColor(themeColor())
        highlight.setAlpha(70)
        selection.format.setBackground(highlight)
        self.text_box.setExtraSelections([selection])

    # ------------------------------------------------------------------
    # Run and toolbar editing
    # ------------------------------------------------------------------

    def _on_toolbar_toggled(self, key: str, checked: bool) -> None:
        if self._updating or not self._state.has_region:
            return
        start, end = self._state.selected_range
        if start == end:
            # 未选中 = 作用于全文：先把选区扩到全文再走正常流程，
            # 与查询侧「空选区显示全文概览」的语义对齐。
            self._select_python_range(0, len(self._state.editor_text))
            start, end = self._state.selected_range
            if start == end:  # 空文本
                self._refresh_inspector()
                return
        if key == "R":
            if checked:
                existing = str(style_for_range(self._state.document, start, end).get("rubyText") or "")
                self._state.begin_ruby_edit(start, end, existing)
                self._refresh_inspector()
                QTimer.singleShot(0, lambda: self.run_list.focus_ruby(start, end))
            else:
                self._state.ruby_draft = None
                self._commit_document(remove_ruby_from_range(self._state.document, start, end))
            return
        if key == "T":
            document = (
                apply_tcy_to_range(self._state.document, start, end)
                if checked
                else remove_tcy_from_range(self._state.document, start, end)
            )
            self._commit_document(document)
            return
        patch = default_style_patch(key) if checked else clear_style_patch(key)
        if not patch:
            return
        if not checked and self._state.discard_pending_style(key, start, end):
            self._refresh_inspector()
            return
        if checked:
            document = apply_style_to_range(self._state.document, start, end, patch)
            if document == self._state.document:
                self._state.begin_pending_style_edit(key, start, end)
                self._refresh_inspector()
            else:
                self._state.discard_pending_style(key, start, end)
                self._commit_document(document)
            return
        self._apply_style_to_explicit_range(start, end, key, patch)

    def _apply_style_to_explicit_range(
        self,
        start: int,
        end: int,
        key: str,
        patch: dict,
    ) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        self._state.discard_pending_style(key, start, end)
        document = apply_style_to_range(self._state.document, start, end, patch)
        # 结构签名不变时 run list 会自动就地复用卡片（持焦点的控件不覆盖），
        # 无需再显式标记 allow_reuse。
        self._commit_document(document)

    def _remove_style_from_explicit_range(self, start: int, end: int, key: str) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        if self._state.discard_pending_style(key, start, end):
            self._refresh_inspector()
            return
        if key == "R":
            self._state.ruby_draft = None
            document = remove_ruby_from_range(self._state.document, start, end)
        elif key == "T":
            document = remove_tcy_from_range(self._state.document, start, end)
        else:
            patch = clear_style_patch(key)
            if not patch:
                return
            document = apply_style_to_range(self._state.document, start, end, patch)
        self._commit_document(document)

    # ------------------------------------------------------------------
    # Rich-text presets and whole-run clearing
    # ------------------------------------------------------------------

    def _refresh_preset_sidebar(self) -> None:
        self.preset_sidebar.set_presets(self._preset_store.load())
        self._queue_layout_refresh()

    def _persist_rich_text_presets(
        self,
        presets: dict[str, dict],
        previous: dict[str, dict],
    ) -> bool:
        if self._preset_store.save_all(presets, previous):
            self.preset_sidebar.set_presets(presets)
            return True
        themed_critical(self, self._t("Error"), self._t("Failed to save style preset"))
        return False

    def _confirm(self, title: str, text: str) -> bool:
        reply = themed_question(self, title, text, default_button=QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    def _prompt_preset_name(
        self,
        *,
        title: str,
        label: str,
        initial: str,
        ok_text: str,
        existing: Iterable[str],
        skip: str | None = None,
    ) -> str | None:
        """Shared name prompt with empty-name and overwrite validation."""
        name, accepted = themed_get_text(
            self,
            title=title,
            label=label,
            text=initial,
            ok_text=ok_text,
            cancel_text=self._t("Cancel"),
        )
        if not accepted:
            return None
        name = str(name).strip()
        if not name:
            themed_warning(self, self._t("Warning"), self._t("Style preset name cannot be empty"))
            return None
        if name != skip and name in existing and not self._confirm(
            self._t("Confirm"),
            self._t("Style preset '{name}' already exists. Overwrite?", name=name),
        ):
            return None
        return name

    def _preset_payload_for_range(self, start: int, end: int) -> dict | None:
        """Read the live document so in-place spin edits are never stale."""
        segments = styled_segments_for_range(self._state.document, start, end, expand_empty=False)
        if len(segments) != 1:
            return None
        segment = segments[0]
        if (segment.start, segment.end) != (int(start), int(end)):
            return None
        return {
            "style": segment.style,
            "ruby": segment.ruby_text if segment.node_type == "ruby" else "",
            "tcy": segment.node_type == "tcy",
        }

    def _save_preset_from_explicit_range(self, start: int, end: int) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        preset = normalize_rich_text_preset(self._preset_payload_for_range(start, end))
        if preset is None:
            return
        current = self._preset_store.load()
        name = self._prompt_preset_name(
            title=self._t("Save Style"),
            label=self._t("Enter style preset name:"),
            initial=f"{self._t('Rich Text Preset')} {len(current) + 1}",
            ok_text=self._t("Save"),
            existing=current,
        )
        if name is None:
            return
        updated = copy.deepcopy(current)
        updated[name] = preset
        self._persist_rich_text_presets(updated, current)

    def _rename_rich_text_preset(self, old_name: str) -> None:
        current = self._preset_store.load()
        if old_name not in current:
            self._refresh_preset_sidebar()
            return
        new_name = self._prompt_preset_name(
            title=self._t("Rename style preset"),
            label=self._t("Enter a new style preset name:"),
            initial=old_name,
            ok_text=self._t("Rename"),
            existing=current,
            skip=old_name,
        )
        if new_name is None or new_name == old_name:
            return
        updated: dict[str, dict] = {}
        for name, payload in current.items():
            if name == old_name:
                updated[new_name] = copy.deepcopy(payload)
            elif name != new_name:
                updated[name] = copy.deepcopy(payload)
        self._persist_rich_text_presets(updated, current)

    def _delete_rich_text_preset(self, name: str) -> None:
        current = self._preset_store.load()
        if name not in current:
            self._refresh_preset_sidebar()
            return
        if not self._confirm(self._t("Confirm"), self._t("Delete style preset '{name}'?", name=name)):
            return
        updated = copy.deepcopy(current)
        del updated[name]
        self._persist_rich_text_presets(updated, current)

    def _apply_rich_text_preset(self, name: str) -> None:
        if self._updating or not self._state.has_region:
            return
        start, end = self._state.selected_range
        if start >= end:
            return
        preset = self._preset_store.load().get(name)
        if preset is None:
            self._refresh_preset_sidebar()
            return

        self._state.ruby_draft = None
        self._state.clear_pending_style_edit()
        document = clear_styles_from_range(self._state.document, start, end)
        if preset["style"]:
            document = apply_style_to_range(document, start, end, preset["style"])
        if preset["ruby"]:
            document = apply_ruby_to_range(document, start, end, preset["ruby"])
        elif preset["tcy"]:
            document = apply_tcy_to_range(document, start, end)
        self._commit_document(document)

    def _clear_all_styles_from_explicit_range(self, start: int, end: int) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        self._state.ruby_draft = None
        self._state.clear_pending_style_edit()
        self._commit_document(clear_styles_from_range(self._state.document, start, end))

    def _sync_window_width_to_sidebar(self) -> None:
        self.setFixedWidth(
            self._MAIN_PANEL_WIDTH
            + self._sidebar_separator.width()
            + self.preset_sidebar.width()
        )

    def _on_preset_sidebar_collapsed(self, _collapsed: bool) -> None:
        self._sync_window_width_to_sidebar()
        self._queue_layout_refresh(emit_position=True)

    def _commit_document(self, document: dict) -> None:
        if self._state.replace_document(document):
            self._emit_pending_document()
        self._refresh_inspector()

    # ------------------------------------------------------------------
    # Fixed-target Ruby editing
    # ------------------------------------------------------------------

    def _start_ruby_edit(self, start: int, end: int, text: str) -> None:
        if self._updating or not self._state.has_region:
            return
        draft = self._state.ruby_draft
        if draft is not None and draft.target_range == (start, end):
            return
        self._finish_active_ruby_before_target_change()
        self._state.begin_ruby_edit(start, end, text)

    def _update_ruby_draft(self, start: int, end: int, text: str) -> None:
        if self._updating or not self._state.has_region:
            return
        draft = self._state.ruby_draft
        if draft is None or draft.target_range != (start, end):
            self._start_ruby_edit(start, end, text)
        self._state.set_ruby_draft_text(text)

    def _apply_ruby_text(self, start: int, end: int, text: str) -> None:
        self._update_ruby_draft(start, end, text)
        changed = self._state.commit_ruby(text)
        if not text:
            self._state.ruby_draft = None
        if changed:
            self._emit_pending_document()
        self._refresh_inspector()

    def _finish_ruby_text(self, start: int, end: int, text: str) -> None:
        self._update_ruby_draft(start, end, text)
        changed = self._state.finish_ruby_edit(text)
        if changed:
            self._emit_pending_document()
        self._refresh_inspector()

    def _finish_active_ruby_before_target_change(self) -> None:
        if self._state.ruby_draft is None:
            return
        changed = self._state.finish_ruby_edit()
        if changed:
            self._emit_pending_document()

    # ------------------------------------------------------------------
    # Inspector and emission
    # ------------------------------------------------------------------

    def _refresh_inspector(self) -> None:
        if not self._state.has_region:
            return
        start, end = self._state.selected_range
        self._updating = True
        try:
            for key in STYLE_KEYS:
                _present, fully_applied = style_row_coverage(
                    self._state.document, start, end, key
                )
                self.toolbar.set_checked(
                    key,
                    fully_applied
                    or self._state.has_pending_style(key, start, end)
                    or (key == "R" and self._state.ruby_draft is not None),
                )

            segments = styled_segments_for_range(
                self._state.document,
                start,
                end,
                expand_empty=True,
            )
            ruby_draft = None
            if self._state.ruby_draft is not None:
                draft = self._state.ruby_draft
                ruby_draft = (
                    draft.target_start,
                    draft.target_end,
                    self._state.editor_text[draft.target_start:draft.target_end],
                    draft.text,
                )
            pending_styles = None
            if self._state.pending_style_edit is not None:
                draft = self._state.pending_style_edit
                pending_styles = (
                    draft.target_start,
                    draft.target_end,
                    self._state.editor_text[draft.target_start:draft.target_end],
                    set(draft.keys),
                )
            self.run_list.set_segments(
                segments,
                ruby_draft=ruby_draft,
                pending_styles=pending_styles,
            )
        finally:
            self._updating = False
        self._queue_layout_refresh()

    def _emit_pending_document(self) -> None:
        self._emit_debounce_timer.stop()
        payload = self._state.mark_document_emitted()
        if payload is None:
            return
        region_index, document, storage_text = payload
        self._applying_own_change = True
        try:
            self.rich_text_changed.emit(region_index, document, storage_text)
        finally:
            self._applying_own_change = False

    def _queue_layout_refresh(self, *, emit_position: bool = False) -> None:
        """Schedule one geometry pass after all child updates have settled."""
        self._layout_position_signal_pending |= bool(emit_position)
        self._layout_refresh_timer.start(0)

    def _apply_queued_layout_refresh(self) -> None:
        emit_position = self._layout_position_signal_pending
        self._layout_position_signal_pending = False
        old_size = self.size()
        self._refresh_layout_size()
        if emit_position or self.size() != old_size:
            self.layout_size_changed.emit(True)

    def _refresh_layout_size(self) -> None:
        """Recalculate the complete floating editor height in one place."""
        self.run_list.recalculate_height()

        main_layout = self.main_panel.layout()
        if main_layout is not None:
            main_layout.invalidate()
            main_layout.activate()
        sidebar_layout = self.preset_sidebar.layout()
        if sidebar_layout is not None:
            sidebar_layout.invalidate()
            sidebar_layout.activate()
        root_layout = self.layout()
        if root_layout is not None:
            root_layout.invalidate()
            root_layout.activate()

        # The main editor owns the window height.  The preset sidebar must
        # scroll inside that height; its content hint must never enlarge the
        # whole floating window when many presets exist.
        main_height = int(self.main_panel.sizeHint().height())
        target_height = max(self.minimumHeight(), main_height)
        if self.preset_sidebar.height() != target_height:
            self.preset_sidebar.setFixedHeight(target_height)
        if self.height() != target_height:
            self.resize(self.width(), target_height)

    def _end_drag(self) -> None:
        """复位拖拽状态并恢复光标；隐藏/关闭时左键 release 不会再来。"""
        if self._dragging:
            self._dragging = False
            self.unsetCursor()

    def hideEvent(self, event):
        self._end_drag()
        self.flush_pending_changes()
        if self._state.clear_pending_style_edit() and self._state.has_region:
            self._refresh_inspector()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._end_drag()
        self.flush_pending_changes()
        if self._state.clear_pending_style_edit() and self._state.has_region:
            self._refresh_inspector()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Drag boundary
    # ------------------------------------------------------------------

    def _t(self, key: str, **kwargs) -> str:
        return self.i18n.translate(key, **kwargs) if self.i18n else key

    def _is_drag_border(self, pos) -> bool:
        border = self._DRAG_BORDER_WIDTH
        return (
            pos.x() <= border or pos.y() <= border
            or pos.x() >= self.width() - border or pos.y() >= self.height() - border
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_drag_border(event.position()):
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.mapToGlobal(QPoint(0, 0))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            target_global = event.globalPosition().toPoint() - self._drag_offset
            # A top-level widget's ``move`` expects desktop coordinates even
            # when it still has a QObject parent for lifetime management.
            if self.isWindow():
                self.move(target_global)
            else:
                parent = self.parentWidget()
                self.move(parent.mapFromGlobal(target_global) if parent is not None else target_global)
            self._manually_positioned = True
        else:
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if self._is_drag_border(event.position()) else Qt.CursorShape.ArrowCursor
            )
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._manually_positioned = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        event.accept()

    def leaveEvent(self, event):
        if not self._dragging:
            self.unsetCursor()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()
