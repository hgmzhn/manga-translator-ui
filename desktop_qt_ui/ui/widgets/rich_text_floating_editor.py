from __future__ import annotations

import copy

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import SimpleCardWidget

from editor.rich_text_editing import (
    apply_ruby_to_range,
    apply_style_to_range,
    apply_tcy_to_range,
    clear_styles_from_range,
    normalize_text_style,
    remove_ruby_from_range,
    remove_tcy_from_range,
    style_for_range,
    style_row_coverage,
    styled_segments_for_range,
    utf16_range_to_python_range,
)
from editor.rich_text_editor_state import RichTextEditorState
from services import get_config_service, get_i18n_manager

from .rich_text_editor_components import (
    STYLE_KEYS,
    RichTextBodyEdit,
    RichTextPresetSidebar,
    RichTextToolbar,
    StyledRunList,
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
        self._rich_text_presets_memory: dict[str, dict] = {}

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

        self.toolbar = RichTextToolbar(self.main_panel, self._t)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.toolbar)

        # One card per actual contiguous run; every property is its own row.
        self.run_list = StyledRunList(self.config_service, self._t, self.main_panel)
        layout.addWidget(self.run_list)

        self.preset_sidebar = RichTextPresetSidebar(self._t, self)
        self.preset_sidebar.setFixedHeight(self.minimumHeight())
        root_layout.addWidget(self.main_panel)
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
        self.toolbar.refresh_ui_texts()
        self.preset_sidebar.refresh_ui_texts()
        if self._state.has_region:
            self._refresh_inspector()
        else:
            self._queue_layout_refresh()

    def refresh_theme(self) -> None:
        """Refresh custom surfaces while Fluent controls follow the app theme."""
        self.toolbar.refresh_theme()
        self.preset_sidebar.refresh_theme()
        for card in self.run_list.run_cards:
            card.refresh_theme()
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
        selection.format.setBackground(QColor(80, 145, 255, 70))
        self.text_box.setExtraSelections([selection])

    # ------------------------------------------------------------------
    # Run and toolbar editing
    # ------------------------------------------------------------------

    def _on_toolbar_toggled(self, key: str, checked: bool) -> None:
        if self._updating or not self._state.has_region:
            return
        start, end = self._state.selected_range
        if start == end:
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
        patch = self._default_patch(key) if checked else self._clear_patch(key)
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

    @staticmethod
    def _default_patch(key: str) -> dict:
        return {
            "B": {"bold": True},
            "I": {"italic": 15.0},
            "C": {"color": "#E53935"},
            "S": {"fontSize": 24},
            "%": {"scale": 1.20},
            "F": {"fontFamily": QFont().family()},
            "O": {"stroke": {"color": "#ffffff", "width": 0.07}},
            "G": {"glow": {"color": "#00ffff", "blur": 0.10}},
            "OS": {"outerStroke": {"color": "#000000", "width": 0.20}},
            "D": {"emphasis": True},
            "Rot": {"transform": {"rotation": 0.0}},
            "K": {"kerning": 0.0},
            "PK": {"preKerning": 0.0},
            "LK": {"lineKerning": 0.0},
            "NK": {"nextKerning": 0.0},
            "XY": {"transform": {"offsetX": 0.0, "offsetY": 0.0}},
            "M": {"transform": {"mirrorX": True}},
            "MV": {"transform": {"mirrorY": True}},
        }.get(key, {})

    @staticmethod
    def _clear_patch(key: str) -> dict:
        return {
            "B": {"bold": None}, "I": {"italic": None}, "C": {"color": None},
            "S": {"fontSize": None}, "%": {"scale": None}, "F": {"fontFamily": None},
            "O": {"stroke": None}, "G": {"glow": None}, "OS": {"outerStroke": None},
            "D": {"emphasis": None}, "Rot": {"transform": {"rotation": None}},
            "K": {"kerning": None}, "PK": {"preKerning": None},
            "LK": {"lineKerning": None}, "NK": {"nextKerning": None},
            "XY": {"transform": {"offsetX": None, "offsetY": None}},
            "M": {"transform": {"mirrorX": None}}, "MV": {"transform": {"mirrorY": None}},
        }.get(key, {})

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
            patch = self._clear_patch(key)
            if not patch:
                return
            document = apply_style_to_range(self._state.document, start, end, patch)
        self._commit_document(document)

    # ------------------------------------------------------------------
    # Rich-text presets and whole-run clearing
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_rich_text_preset(payload: object) -> dict | None:
        if not isinstance(payload, dict):
            return None
        try:
            style = normalize_text_style(payload.get("style") or {})
        except (TypeError, ValueError):
            return None
        ruby = payload.get("ruby", "")
        if not isinstance(ruby, str):
            return None
        tcy = bool(payload.get("tcy", False))
        if not style and not ruby and not tcy:
            return None
        return {
            "style": style,
            "ruby": ruby,
            "tcy": tcy,
        }

    def _saved_rich_text_presets(self) -> dict[str, dict]:
        if self.config_service is None:
            return copy.deepcopy(self._rich_text_presets_memory)
        config_ref = self.config_service.get_config_reference()
        raw = getattr(getattr(config_ref, "app", None), "saved_rich_text_presets", None)
        if not isinstance(raw, dict):
            return {}
        presets: dict[str, dict] = {}
        for name, payload in raw.items():
            normalized = self._normalize_rich_text_preset(payload)
            clean_name = str(name).strip()
            if clean_name and normalized is not None:
                presets[clean_name] = normalized
        return presets

    def _refresh_preset_sidebar(self) -> None:
        self.preset_sidebar.set_presets(self._saved_rich_text_presets())
        self._queue_layout_refresh()

    def _persist_rich_text_presets(
        self,
        presets: dict[str, dict],
        previous: dict[str, dict],
    ) -> bool:
        from PyQt6.QtWidgets import QMessageBox

        if self.config_service is None:
            self._rich_text_presets_memory = copy.deepcopy(presets)
            self.preset_sidebar.set_presets(presets)
            return True
        config_ref = self.config_service.get_config_reference()
        config_ref.app.saved_rich_text_presets = copy.deepcopy(presets) or None
        if self.config_service.save_config_file():
            self.preset_sidebar.set_presets(presets)
            return True
        config_ref.app.saved_rich_text_presets = copy.deepcopy(previous) or None
        QMessageBox.critical(self, self._t("Error"), self._t("Failed to save style preset"))
        return False

    def _save_preset_from_explicit_range(
        self,
        start: int,
        end: int,
        payload: object,
    ) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        preset = self._normalize_rich_text_preset(payload)
        if preset is None:
            return

        from PyQt6.QtWidgets import QMessageBox
        from ui.secondary_pages.themed_text_input_dialog import themed_get_text

        self._select_python_range(start, end)
        current = self._saved_rich_text_presets()
        default_name = f"{self._t('Rich Text Preset')} {len(current) + 1}"
        name, accepted = themed_get_text(
            self,
            title=self._t("Save Style"),
            label=self._t("Enter style preset name:"),
            text=default_name,
            ok_text=self._t("Save"),
            cancel_text=self._t("Cancel"),
        )
        if not accepted:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Style preset name cannot be empty"))
            return
        if name in current:
            reply = QMessageBox.question(
                self,
                self._t("Confirm"),
                self._t("Style preset '{name}' already exists. Overwrite?", name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        updated = copy.deepcopy(current)
        updated[name] = preset
        self._persist_rich_text_presets(updated, current)

    def _rename_rich_text_preset(self, old_name: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        from ui.secondary_pages.themed_text_input_dialog import themed_get_text

        current = self._saved_rich_text_presets()
        if old_name not in current:
            self._refresh_preset_sidebar()
            return
        new_name, accepted = themed_get_text(
            self,
            title=self._t("Rename style preset"),
            label=self._t("Enter a new style preset name:"),
            text=old_name,
            ok_text=self._t("Rename"),
            cancel_text=self._t("Cancel"),
        )
        if not accepted:
            return
        new_name = str(new_name).strip()
        if not new_name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Style preset name cannot be empty"))
            return
        if new_name == old_name:
            return
        if new_name in current:
            reply = QMessageBox.question(
                self,
                self._t("Confirm"),
                self._t("Style preset '{name}' already exists. Overwrite?", name=new_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        updated: dict[str, dict] = {}
        for name, payload in current.items():
            if name == old_name:
                updated[new_name] = copy.deepcopy(payload)
            elif name != new_name:
                updated[name] = copy.deepcopy(payload)
        self._persist_rich_text_presets(updated, current)

    def _delete_rich_text_preset(self, name: str) -> None:
        from PyQt6.QtWidgets import QMessageBox

        current = self._saved_rich_text_presets()
        if name not in current:
            self._refresh_preset_sidebar()
            return
        reply = QMessageBox.question(
            self,
            self._t("Confirm"),
            self._t("Delete style preset '{name}'?", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
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
        preset = self._saved_rich_text_presets().get(name)
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
        self._select_python_range(start, end)
        self._state.ruby_draft = None
        self._state.clear_pending_style_edit()
        self._commit_document(clear_styles_from_range(self._state.document, start, end))

    def _sync_window_width_to_sidebar(self) -> None:
        self.setFixedWidth(self._MAIN_PANEL_WIDTH + self.preset_sidebar.width())

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

    def hideEvent(self, event):
        self.flush_pending_changes()
        if self._state.clear_pending_style_edit() and self._state.has_region:
            self._refresh_inspector()
        super().hideEvent(event)

    def closeEvent(self, event):
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
