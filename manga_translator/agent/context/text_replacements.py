"""Use the host's text replacement rules without discarding authored styles."""

from __future__ import annotations

import re


_BREAK_RE = re.compile(r"(?:\[BR\]|【BR】|<br\s*/?>|\r\n|\r|\n)", re.IGNORECASE)


def normalize_breaks(text: str) -> str:
    """Normalize break markers without applying replacement rules a second time."""
    return _BREAK_RE.sub("\n", text or "")


def normalize_document(value, direction: str, replacements: dict | None = None):
    """Project text replacements onto a copy; this does not run auto-style rules."""
    from manga_translator.rendering.rich_text import (
        ensure_rich_text_document, legacy_line_breaks_to_document,
    )
    from manga_translator.rendering.rich_text_rules import (
        _RuleEntry, _document_from_rule_entries, _rule_entries_from_document,
    )
    from manga_translator.rendering.rich_text_sync import apply_replacements_to_entries
    from manga_translator.rendering.text_replacements import load_replacements

    document = (legacy_line_breaks_to_document(normalize_breaks(value))
                if isinstance(value, str) else ensure_rich_text_document(value))
    entries = _rule_entries_from_document(document)
    def normalize_entry_breaks(items):
        source = "".join(entry.char for entry in items)
        for match in reversed(list(_BREAK_RE.finditer(source))):
            items[match.start():match.end()] = [_RuleEntry("\n", {})]
        return items

    entries = normalize_entry_breaks(entries)
    if replacements is None:
        replacements = load_replacements()
    vertical = str(getattr(direction, "value", direction)).lower() in {"v", "vertical", "vr"}
    result = []
    start = 0
    # Do not let a regex spanning plain text and a manually authored ruby/tcy
    # node dissolve that node. Plain styled runs still share replacement spans.
    while start < len(entries):
        end = start + 1
        while end < len(entries) and entries[end].node is entries[start].node:
            end += 1
        result.extend(apply_replacements_to_entries(entries[start:end], int(vertical), replacements))
        start = end
    result = normalize_entry_breaks(result)
    return _document_from_rule_entries("".join(entry.char for entry in result), result)


def normalize_translation(text: str, direction: str) -> str:
    """Apply the configured common/direction rules, preserving every paragraph."""
    return normalize_document(text, direction).plain_text() if text else text
