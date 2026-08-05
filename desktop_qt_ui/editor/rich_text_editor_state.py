"""State machine for the floating rich-text editor.

The UI owns widgets; this module owns the bound region, Python-indexed selection,
document mutations, debounced body changes, and the fixed target of a Ruby edit.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .rich_text_editing import (
    apply_qt_text_change,
    apply_ruby_to_range,
    document_from_region,
    document_to_storage_text,
    editor_text_to_plain_text,
    python_index_to_utf16_offset,
    storage_text_to_editor_text,
    utf16_range_to_python_range,
    visible_text_from_document,
)


def _empty_document() -> dict:
    return {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": []}],
    }


@dataclass
class RubyEditDraft:
    target_start: int
    target_end: int
    text: str
    committed_text: str

    @property
    def target_range(self) -> tuple[int, int]:
        return self.target_start, self.target_end


@dataclass
class PendingStyleEdit:
    target_start: int
    target_end: int
    keys: set[str] = field(default_factory=set)

    @property
    def target_range(self) -> tuple[int, int]:
        return self.target_start, self.target_end


class RichTextEditorState:
    """Non-visual editing state with Python character offsets only."""

    def __init__(self) -> None:
        self.region_index = -1
        self.region_data: dict = {}
        self.document: dict = _empty_document()
        self.editor_text = ""
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft: RubyEditDraft | None = None
        self.pending_style_edit: PendingStyleEdit | None = None
        # 编辑时自动应用富文本规则的开关查询（由 UI 层注入，None=关闭）
        self.auto_rules_provider = None

    @property
    def has_region(self) -> bool:
        return self.region_index >= 0

    @property
    def selected_range(self) -> tuple[int, int]:
        text_length = len(self.editor_text)
        start = max(0, min(self.selection_start, text_length))
        end = max(start, min(self.selection_end, text_length))
        return start, end

    def bind_region(self, region_index: int, region_data: dict) -> str:
        self.region_index = int(region_index)
        self.region_data = copy.deepcopy(dict(region_data or {}))
        self.document = document_from_region(self.region_data)
        self.editor_text = storage_text_to_editor_text(self.document)
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft = None
        self.pending_style_edit = None
        return self.editor_text

    def clear_region(self) -> None:
        self.region_index = -1
        self.region_data = {}
        self.document = _empty_document()
        self.editor_text = ""
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft = None
        self.pending_style_edit = None

    def set_selection(self, start: int, end: int) -> bool:
        text_length = len(self.editor_text)
        start = max(0, min(int(start), text_length))
        end = max(0, min(int(end), text_length))
        if end < start:
            start, end = end, start
        changed = (start, end) != self.selected_range
        self.selection_start = start
        self.selection_end = end
        if (
            changed
            and self.pending_style_edit is not None
            and self.pending_style_edit.target_range != (start, end)
        ):
            self.pending_style_edit = None
        return changed

    def set_selection_from_qt(self, start: int, end: int) -> bool:
        py_start, py_end = utf16_range_to_python_range(self.editor_text, start, end)
        return self.set_selection(py_start, py_end)

    def selection_as_qt_range(self) -> tuple[int, int]:
        start, end = self.selected_range
        return (
            python_index_to_utf16_offset(self.editor_text, start),
            python_index_to_utf16_offset(self.editor_text, end),
        )

    def apply_qt_contents_change(
        self,
        new_editor_text: str,
        position: int,
        chars_removed: int,
        chars_added: int,
    ) -> bool:
        self.pending_style_edit = None
        previous = self.document
        old_editor_text = self.editor_text
        self.document = apply_qt_text_change(
            self.document,
            self.editor_text,
            new_editor_text,
            position,
            chars_removed,
            chars_added,
        )
        if self._auto_rules_enabled():
            self.document = self._with_auto_rules(old_editor_text, self.document)
        self.editor_text = str(new_editor_text or "")
        self.set_selection(self.selection_start, self.selection_end)
        changed = self.document != previous
        self.pending_document_change = self.pending_document_change or changed
        return changed

    def _auto_rules_enabled(self) -> bool:
        provider = self.auto_rules_provider
        try:
            return bool(provider()) if callable(provider) else False
        except Exception:
            return False

    def _with_auto_rules(self, old_editor_text: str, document: dict) -> dict:
        """打字后按新旧文本匹配对比应用自动富文本规则（规则只加样式不改字）。"""
        from manga_translator.rendering.rich_text_rules import apply_rich_text_rules

        try:
            ruled = apply_rich_text_rules(
                document,
                self.region_data.get("direction", "h"),
                previous_text=editor_text_to_plain_text(old_editor_text),
                styled_match_policy="skip",
            )
        except Exception:
            return document
        if ruled is None:
            return document
        ruled_dict = ruled.to_dict()
        if visible_text_from_document(ruled_dict) != visible_text_from_document(document):
            return document
        return ruled_dict

    def replace_document(self, document: dict) -> bool:
        if document == self.document:
            return False
        self.document = document
        self.editor_text = visible_text_from_document(document)
        self.set_selection(self.selection_start, self.selection_end)
        self.pending_document_change = True
        return True

    def begin_ruby_edit(self, start: int, end: int, text: str) -> bool:
        start, end = sorted((int(start), int(end)))
        start = max(0, min(start, len(self.editor_text)))
        end = max(start, min(end, len(self.editor_text)))
        if start == end:
            self.ruby_draft = None
            return False
        value = str(text or "")
        self.ruby_draft = RubyEditDraft(start, end, value, value)
        return True

    def set_ruby_draft_text(self, text: str) -> None:
        if self.ruby_draft is not None:
            self.ruby_draft.text = str(text or "")

    def commit_ruby(self, text: str | None = None) -> bool:
        draft = self.ruby_draft
        if draft is None:
            return False
        if text is not None:
            draft.text = str(text or "")
        if draft.text == draft.committed_text:
            return False

        updated = apply_ruby_to_range(
            self.document,
            draft.target_start,
            draft.target_end,
            draft.text,
        )
        draft.committed_text = draft.text
        if updated == self.document:
            return False
        self.document = updated
        self.pending_document_change = True
        return True

    def finish_ruby_edit(self, text: str | None = None) -> bool:
        changed = self.commit_ruby(text)
        self.ruby_draft = None
        return changed

    def begin_pending_style_edit(self, key: str, start: int, end: int) -> bool:
        start, end = sorted((int(start), int(end)))
        start = max(0, min(start, len(self.editor_text)))
        end = max(start, min(end, len(self.editor_text)))
        if start == end:
            return False
        target = (start, end)
        if self.pending_style_edit is None or self.pending_style_edit.target_range != target:
            self.pending_style_edit = PendingStyleEdit(start, end)
        if key in self.pending_style_edit.keys:
            return False
        self.pending_style_edit.keys.add(str(key))
        return True

    def discard_pending_style(self, key: str, start: int, end: int) -> bool:
        draft = self.pending_style_edit
        if draft is None or draft.target_range != (int(start), int(end)) or key not in draft.keys:
            return False
        draft.keys.remove(key)
        if not draft.keys:
            self.pending_style_edit = None
        return True

    def has_pending_style(self, key: str, start: int, end: int) -> bool:
        draft = self.pending_style_edit
        return bool(
            draft is not None
            and draft.target_range == (int(start), int(end))
            and key in draft.keys
        )

    def clear_pending_style_edit(self) -> bool:
        if self.pending_style_edit is None:
            return False
        self.pending_style_edit = None
        return True

    def mark_document_emitted(self) -> tuple[int, dict, str] | None:
        if not self.has_region or not self.pending_document_change:
            return None
        self.pending_document_change = False
        storage_text = document_to_storage_text(self.document)
        text_changed = storage_text != self.region_data.get("translation", "")
        self.region_data["translation"] = storage_text
        if text_changed or "translation_raw" not in self.region_data:
            # 正文改变后无法可靠反推替换前译文；仅改样式时则必须保留 raw。
            self.region_data["translation_raw"] = storage_text
        self.region_data["translation_rich"] = copy.deepcopy(self.document)
        return self.region_index, copy.deepcopy(self.document), storage_text

    def same_bound_content(self, region_index: int, region_data: dict) -> bool:
        if int(region_index) != self.region_index or not self.region_data:
            return False
        region_data = dict(region_data or {})
        return (
            self.region_data.get("translation") == region_data.get("translation")
            and self.region_data.get("translation_rich") == region_data.get("translation_rich")
        )

    def refresh_cached_region_data(self, region_data: dict) -> None:
        self.region_data = copy.deepcopy(dict(region_data or {}))
