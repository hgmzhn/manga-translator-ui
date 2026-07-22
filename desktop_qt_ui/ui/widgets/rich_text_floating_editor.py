from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout
from qfluentwidgets import SimpleCardWidget

from editor.rich_text_editing import (
    apply_style_to_range,
    apply_tcy_to_range,
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
    RichTextToolbar,
    StyledRunList,
)


class RichTextFloatingEditor(SimpleCardWidget):
    """Floating ``richtext.v1`` editor coordinated around real document runs."""

    rich_text_changed = pyqtSignal(int, object, str)
    layout_size_changed = pyqtSignal()

    _DRAG_BORDER_WIDTH = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)
        self.setMouseTracking(True)
        self.setFixedWidth(440)
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

        self.i18n = get_i18n_manager()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self.text_box = RichTextBodyEdit(self)
        text_font = self.text_box.font()
        text_font.setPointSize(14)
        self.text_box.setFont(text_font)
        self.text_box.setFixedHeight(120)
        self.text_box.setUndoRedoEnabled(True)
        layout.addWidget(self.text_box)

        self.toolbar = RichTextToolbar(self)
        layout.addWidget(self.toolbar)

        # One card per actual contiguous run; every property is its own row.
        self.run_list = StyledRunList(get_config_service(), self._t, self)
        layout.addWidget(self.run_list)

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
        self.run_list.ruby_started.connect(self._start_ruby_edit)
        self.run_list.ruby_changed.connect(self._update_ruby_draft)
        self.run_list.ruby_apply_requested.connect(self._apply_ruby_text)
        self.run_list.ruby_finished.connect(self._finish_ruby_text)

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
        if patch:
            self._apply_style_to_explicit_range(start, end, patch)

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

    def _apply_style_to_explicit_range(self, start: int, end: int, patch: dict) -> None:
        if self._updating or not self._state.has_region or start >= end:
            return
        document = apply_style_to_range(self._state.document, start, end, patch)
        self._commit_document(document)

    def _remove_style_from_explicit_range(self, start: int, end: int, key: str) -> None:
        if self._updating or not self._state.has_region or start >= end:
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
                    fully_applied or (key == "R" and self._state.ruby_draft is not None),
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
            self.run_list.set_segments(segments, ruby_draft=ruby_draft)
        finally:
            self._updating = False
        self._refresh_layout_size()

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

    def _refresh_layout_size(self) -> None:
        if self.layout() is not None:
            self.layout().activate()
        target_height = max(self.minimumHeight(), int(self.sizeHint().height()))
        if self.height() != target_height:
            self.resize(self.width(), target_height)
            QTimer.singleShot(0, self.layout_size_changed.emit)

    def hideEvent(self, event):
        self.flush_pending_changes()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.flush_pending_changes()
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
            parent = self.parentWidget()
            target_global = event.globalPosition().toPoint() - self._drag_offset
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
