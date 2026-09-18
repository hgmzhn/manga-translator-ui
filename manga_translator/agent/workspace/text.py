"""Unicode text coordinates and lossless rich-text edits shared by commands."""

from __future__ import annotations

import time

import regex

from ..domain.tool_models import ToolError

MAX_TEXT = 200_000
MAX_MATCHES = 10_000
REGEX_SECONDS = 0.2


def document(region: dict) -> dict:
    from manga_translator.rendering.rich_text import (
        ensure_rich_text_document,
        legacy_line_breaks_to_document,
    )

    rich = region.get("translation_rich")
    try:
        if rich is not None:
            return ensure_rich_text_document(rich).to_dict()
        return legacy_line_breaks_to_document(region.get("translation") or "").to_dict()
    except (ValueError, TypeError) as exc:
        raise ToolError("invalid_rich_text", str(exc)) from exc


def visible(doc: dict) -> str:
    from manga_translator.rendering.rich_text import ensure_rich_text_document

    return ensure_rich_text_document(doc).plain_text()


def text_of(region: dict, field: str) -> str | None:
    if field == "translation":
        return visible(document(region))
    if field == "source":
        value = region.get("text")
        if value is None and region.get("texts") is not None:
            value = "\n".join(region["texts"])
        return value
    raise ToolError("invalid_field", "field must be source or translation")


def boundaries(text: str) -> set[int]:
    if len(text) > MAX_TEXT:
        raise ToolError(
            "resource_limit", "Region text exceeds the supported character limit"
        )
    try:
        return {
            0,
            *(
                match.end()
                for match in regex.finditer(r"\X", text, timeout=REGEX_SECONDS)
            ),
        }
    except TimeoutError as exc:
        raise ToolError(
            "regex_timeout", "Grapheme segmentation exceeded its execution deadline"
        ) from exc


def check_range(text: str, start: int, end: int) -> None:
    edges = boundaries(text)
    if start not in edges or end not in edges or start >= end:
        raise ToolError(
            "invalid_text_boundary",
            "Text ranges must be nonempty Unicode grapheme boundaries",
        )


def matcher(query: str, mode: str):
    if not query or len(query) > 4096:
        raise ToolError("invalid_query", "Query must contain 1–4096 characters")
    if mode not in {"literal", "regex"}:
        raise ToolError("invalid_mode", "Only literal and regex matching are supported")
    try:
        compiled = regex.compile(regex.escape(query) if mode == "literal" else query)
        if compiled.search("", timeout=REGEX_SECONDS) is not None:
            raise ToolError(
                "empty_match", "Patterns matching empty text are unsupported"
            )
        return compiled
    except regex.error as exc:
        raise ToolError("invalid_regex", str(exc)) from exc
    except TimeoutError as exc:
        raise ToolError(
            "regex_timeout", "Regular expression exceeded its execution deadline"
        ) from exc


def matches(
    text: str, compiled, replacement: str | None = None, deadline: float | None = None
) -> list[tuple[int, int, str]]:
    edges = boundaries(text)
    remaining = (
        REGEX_SECONDS
        if deadline is None
        else min(REGEX_SECONDS, deadline - time.monotonic())
    )
    if remaining <= 0:
        raise ToolError("regex_timeout", "Search exceeded its execution deadline")
    result = []
    try:
        for match in compiled.finditer(text, timeout=remaining):
            start, end = match.span()
            if start == end:
                raise ToolError("empty_match", "Zero-width matches are unsupported")
            if start not in edges or end not in edges:
                raise ToolError(
                    "invalid_text_boundary", "Query splits a Unicode grapheme"
                )
            result.append(
                (
                    start,
                    end,
                    match.group() if replacement is None else match.expand(replacement),
                )
            )
            if len(result) > MAX_MATCHES:
                raise ToolError(
                    "resource_limit", "Too many matches; narrow the search scope"
                )
    except TimeoutError as exc:
        raise ToolError(
            "regex_timeout", "Regular expression exceeded its execution deadline"
        ) from exc
    except (regex.error, IndexError) as exc:
        raise ToolError("invalid_replacement", str(exc)) from exc
    return result


def _check_ruby(doc: dict, start: int, end: int) -> None:
    offset = 0
    for block in doc["blocks"]:
        for node in block["inlines"]:
            kind = node["type"]
            runs = (
                node.get("base", [])
                if kind == "ruby"
                else node.get("content", [])
                if kind == "tcy"
                else [node]
            )
            length = sum(len(run["text"]) for run in runs)
            if kind == "ruby" and start < offset + length and end > offset:
                raise ToolError(
                    "unsupported_ruby_edit",
                    "Editing ruby base spans is unsupported; provide a complete rich-text document",
                )
            offset += length
        offset += 1


def replace_ranges(doc: dict, ranges: list[tuple[int, int, str]]) -> dict:
    # Bulk replacement inherits the matched text, not an editor cursor at the
    # run boundary. Reuse the existing node mapper/rebuilder without GUI state.
    from desktop_qt_ui.editor.rich_text_editing import (
        _CharEntry,
        _document_from_entries,
        _visible_entries,
    )

    text = visible(doc)
    entries = _visible_entries(doc)
    for start, end, replacement in reversed(ranges):
        check_range(text, start, end)
        if text[start:end] == replacement:
            continue
        _check_ruby(doc, start, end)
        matched = entries[start:end]
        node = matched[0].node
        if any(entry.node is not node for entry in matched) or (
            node is not None and "\n" in replacement
        ):
            raise ToolError(
                "unsupported_tcy_edit",
                "Replacement cannot cross special-node boundaries or split a tcy node",
            )
        updated = text[:start] + replacement + text[end:]
        if len(updated) > MAX_TEXT:
            raise ToolError(
                "resource_limit", "Replacement exceeds the region character limit"
            )
        inserted = [
            _CharEntry(char, matched[0].style, None, node) for char in replacement
        ]
        entries[start:end] = inserted
        text = updated
    result = _document_from_entries(entries)
    if visible(result) != text:
        raise ToolError(
            "unsupported_text_edit",
            "Rich-text helper could not preserve the requested text",
        )
    return result


def style_range(doc: dict, start: int, end: int, patch: dict) -> dict:
    from desktop_qt_ui.editor.rich_text_editing import apply_style_to_range

    before = visible(doc)
    check_range(before, start, end)
    _check_ruby(doc, start, end)
    updated = apply_style_to_range(doc, start, end, patch)
    if visible(updated) != before:
        raise ToolError(
            "unsupported_text_edit", "Style operation must preserve all visible text"
        )
    return updated


def store_document(region: dict, doc: dict) -> None:
    # Keep exact paragraph/newline count; the renderer already accepts real LF.
    region["translation_rich"] = doc
    region["translation"] = visible(doc)


def content_signature(doc: dict) -> tuple:
    """Include ruby annotation text, which visible base-text coordinates omit."""
    annotations = []
    offset = 0
    for block in doc["blocks"]:
        for node in block["inlines"]:
            kind = node["type"]
            runs = (
                node.get("base", [])
                if kind == "ruby"
                else node.get("content", [])
                if kind == "tcy"
                else [node]
            )
            length = sum(len(run["text"]) for run in runs)
            if kind == "ruby":
                annotations.append(
                    (offset, length, "".join(run["text"] for run in node["text"]))
                )
            offset += length
        offset += 1
    return visible(doc), tuple(annotations)
