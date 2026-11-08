"""State machine for the floating rich-text editor.

The UI owns widgets; this module owns the bound region, Python-indexed selection,
document mutations, debounced body changes, and the fixed target of a Ruby edit.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

from manga_translator.rendering import rich_text_sync
from manga_translator.rendering.rich_text import (
    ensure_rich_text_document,
    plain_text_of,
)
from manga_translator.rendering.rich_text_rules import (
    EditorRuleContext,
    _document_from_rule_entries,
    _rule_entries_from_document,
)
from manga_translator.rendering.rich_text_sync import (
    _direction_to_int,
    _map_styles_to_raw,
    apply_replacements_to_document,
)

from .rich_text_editing import (
    apply_qt_text_change,
    apply_ruby_to_range,
    document_from_region,
    plain_text_to_storage_text,
    python_index_to_utf16_offset,
    qt_text_change_range,
    utf16_range_to_python_range,
)


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
        """Keep manual input, displayed styles and prepared output as separate session state."""
        self.region_index = -1
        self.region_data: dict = {}
        self.document: dict = document_from_region({})
        self.editor_text = ""
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft: RubyEditDraft | None = None
        self.pending_style_edit: PendingStyleEdit | None = None
        # 编辑时自动应用富文本规则的开关查询（由 UI 层注入，None=关闭）
        self.auto_rules_provider = None
        self._reset_format_state()

    def _reset_format_state(self) -> None:
        """Treat bound content as manual and suppress its existing rule matches."""
        self._manual_document = copy.deepcopy(self.document)
        self._prepared_document = copy.deepcopy(self.document)
        self._source_ids = list(range(len(plain_text_of(self.document))))
        self._next_source_id = len(self._source_ids)
        self._rule_context = EditorRuleContext([(source,) for source in self._source_ids])
        try:
            self._rule_context.suppress_existing(plain_text_of(self.document), self.region_data.get("direction", "h"))
        except Exception as exc:
            logging.getLogger(__name__).warning("Failed to seed floating text rules; preserving input: %s", exc)
            self._rule_context.defer_existing(plain_text_of(self.document))
        self._needs_replacements = False

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
        """Bind a fresh document without sharing automatic provenance across regions."""
        self.region_index = int(region_index)
        self.region_data = copy.deepcopy(dict(region_data or {}))
        self.document = document_from_region(self.region_data)
        self.editor_text = plain_text_of(self.document)
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft = None
        self.pending_style_edit = None
        self._reset_format_state()
        return self.editor_text

    def clear_region(self) -> None:
        """Discard document and formatting provenance when no region is bound."""
        self.region_index = -1
        self.region_data = {}
        self.document = document_from_region({})
        self.editor_text = ""
        self.selection_start = 0
        self.selection_end = 0
        self.pending_document_change = False
        self.ruby_draft = None
        self.pending_style_edit = None
        self._reset_format_state()

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
        """Apply the same precise Qt edit to manual text and stable character identities."""
        self.pending_style_edit = None
        previous = self.document
        old_editor_text = self.editor_text
        preview = apply_qt_text_change(
            previous, old_editor_text, new_editor_text, position, chars_removed, chars_added
        )
        self._manual_document = apply_qt_text_change(
            self._manual_document,
            self.editor_text,
            new_editor_text,
            position,
            chars_removed,
            chars_added,
        )
        new_text, start, removed, added = qt_text_change_range(
            old_editor_text, new_editor_text, position, chars_removed, chars_added
        )
        self._source_ids[start:start + removed] = range(self._next_source_id, self._next_source_id + added)
        self._next_source_id += added
        live = set(self._source_ids)
        self._rule_context.suppressed = {key for key in self._rule_context.suppressed if key[-1] <= live}
        self._rule_context.approved = {key for key in self._rule_context.approved if key[-1] <= live}
        self._rule_context.overrides = {key: fields for key, fields in self._rule_context.overrides.items() if key[-1] <= live}
        self._rule_context.previous_effects = {
            key: effect for key, effect in self._rule_context.previous_effects.items() if set(key[0]) <= live
        }
        if new_text != plain_text_of(previous):
            self._needs_replacements = True
        self._prepare_document(preview)
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

    def _prepare_document(self, preview_document: dict) -> None:
        """Format manual input in output coordinates, then project only its preview back."""
        direction = self.region_data.get("direction", "h")
        context = self._rule_context
        context.source_ids = [(source,) for source in self._source_ids]
        manual_entries = _rule_entries_from_document(ensure_rich_text_document(self._manual_document))
        preview_entries = _rule_entries_from_document(ensure_rich_text_document(preview_document))
        context.automatic_styles = [
            {key: value for key, value in preview.style.items() if key not in manual.style}
            for manual, preview in zip(manual_entries, preview_entries)
        ]
        context.automatic_nodes = [
            preview.node if preview.node != manual.node else None
            for manual, preview in zip(manual_entries, preview_entries)
        ]
        enabled = self._auto_rules_enabled()
        context.allow_new = enabled
        try:
            replacements = rich_text_sync.load_replacements() if self._needs_replacements else {}
            prepared = apply_replacements_to_document(
                self._manual_document, direction, replacements,
                apply_rules=True, editor_context=context,
            )
            raw_text = plain_text_of(self._manual_document)
            preview = _map_styles_to_raw(
                raw_text, _rule_entries_from_document(prepared), _direction_to_int(direction), replacements
            )
            self._prepared_document = prepared.to_dict()
            self.document = (
                _document_from_rule_entries(raw_text, preview).to_dict()
                if preview is not None else copy.deepcopy(self._manual_document)
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Floating text formatting failed; preserving input: %s", exc)
            self.document = copy.deepcopy(preview_document)
            self._prepared_document = copy.deepcopy(preview_document)

    def replace_document(self, document: dict, *, cleared_range: tuple[int, int] | None = None) -> bool:
        """Apply explicit style changes to manual input and suppress affected automatic matches."""
        if document == self.document and cleared_range is None:
            return False
        if plain_text_of(document) != plain_text_of(self.document):
            self._manual_document = copy.deepcopy(document)
            count = len(plain_text_of(document))
            self._source_ids = list(range(self._next_source_id, self._next_source_id + count))
            self._next_source_id += count
            self._needs_replacements = True
        else:
            before = _rule_entries_from_document(ensure_rich_text_document(self.document))
            after = _rule_entries_from_document(ensure_rich_text_document(document))
            manual = _rule_entries_from_document(ensure_rich_text_document(self._manual_document))
            cleared_sources = set(self._source_ids[slice(*cleared_range)]) if cleared_range is not None else set()
            self._rule_context.clear_output_effects(cleared_sources)
            changed_fields: dict[int, set[str]] = {}
            for source, old, new, base in zip(self._source_ids, before, after, manual):
                if source in cleared_sources:
                    base.style = copy.deepcopy(new.style)
                    base.node = new.node
                for key in old.style.keys() | new.style.keys():
                    if old.style.get(key) != new.style.get(key):
                        if key in new.style:
                            base.style[key] = copy.deepcopy(new.style[key])
                        else:
                            base.style.pop(key, None)
                        changed_fields.setdefault(source, set()).add(key)
                if old.node != new.node:
                    base.node = new.node
                    changed_fields.setdefault(source, set()).add("__node")
            for output_key, (style, node) in self._rule_context.previous_effects.items():
                fields = set().union(*(changed_fields.get(source, set()) for source in output_key[0]))
                for field in fields:
                    style.pop(field, None)
                if "__node" in fields:
                    self._rule_context.previous_effects[output_key] = (style, None)
            self._rule_context.record_overrides(changed_fields)
            self._manual_document = _document_from_rule_entries(plain_text_of(document), manual).to_dict()
        self._prepare_document(document)
        self.editor_text = plain_text_of(document)
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
        """Commit the Ruby draft through the same manual-style provenance boundary."""
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
        return self.replace_document(updated)

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
        if (
            self.pending_style_edit is None
            or self.pending_style_edit.target_range != target
        ):
            self.pending_style_edit = PendingStyleEdit(start, end)
        if key in self.pending_style_edit.keys:
            return False
        self.pending_style_edit.keys.add(str(key))
        return True

    def discard_pending_style(self, key: str, start: int, end: int) -> bool:
        draft = self.pending_style_edit
        if (
            draft is None
            or draft.target_range != (int(start), int(end))
            or key not in draft.keys
        ):
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

    def mark_document_emitted(self) -> tuple[int, dict, str, bool] | None:
        """取出待提交文档，并以浮窗自己的输入历史判断正文是否变化。"""
        if not self.has_region or not self.pending_document_change:
            return None
        self.pending_document_change = False
        storage_text = plain_text_to_storage_text(plain_text_of(self.document))
        text_changed = storage_text != self.region_data.get("translation", "")
        self.region_data["translation"] = storage_text
        if text_changed or "translation_raw" not in self.region_data:
            # 正文改变后无法可靠反推替换前译文；仅改样式时则必须保留 raw。
            self.region_data["translation_raw"] = storage_text
        self.region_data["translation_rich"] = copy.deepcopy(self.document)
        return self.region_index, copy.deepcopy(self._prepared_document), storage_text, text_changed

    def same_bound_content(self, region_index: int, region_data: dict) -> bool:
        if int(region_index) != self.region_index or not self.region_data:
            return False
        region_data = dict(region_data or {})
        return self.region_data.get("translation") == region_data.get(
            "translation"
        ) and self.region_data.get("translation_rich") == region_data.get(
            "translation_rich"
        )

    def refresh_cached_region_data(self, region_data: dict) -> None:
        self.region_data = copy.deepcopy(dict(region_data or {}))
