"""Automatic rich-text styling rules applied after text replacements."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from manga_translator.runtime_paths import get_config_path

from ..utils import get_logger
from .rich_text import (
    Paragraph,
    RichTextDocument,
    TextRun,
    TextStyle,
    ensure_rich_text_document,
    is_rich_text_document,
)


logger = get_logger("rich_text_rules")

_DEFAULT_RULES_PATH = get_config_path("rich_text_rules.yaml")
_rules_cache: Dict[str, Tuple[float, dict]] = {}
_LINE_BREAK_RE = re.compile(r"(?:\[BR\]|【BR】|<br\s*/?>|\r\n|\r|\n)", re.IGNORECASE)

_DEFAULT_RULES_YAML = """# 富文本规则配置
# 界面顺序：通用（始终执行）-> 横排 / 竖排；YAML 键为 common -> horizontal / vertical。
# 规则匹配文本替换完成后的译文。自动规则之间后面的规则可覆盖前面的同字段，
# 但已有手工富文本的相同字段会保留，自动规则只追加尚未设置的字段。
common:
  - enabled: false
    pattern: "示例"
    regex: false
    style: {}
    ruby: ""
    tcy: false
    comment: "示例规则（启用前请配置需要的富文本样式）"

horizontal: []
vertical:
  - enabled: true
    pattern: '["''ー⸺–—～﹏●•（《〈【〖〔［｛）》〉】〗〕］｝]'
    regex: true
    style:
      transform:
        rotation: 90
    ruby: ""
    tcy: false
    comment: "竖排中将无专用替换字形的符号包装为富文本并旋转90度"
"""


def ensure_rich_text_rules_exists(file_path: Optional[str] = None) -> str:
    file_path = os.path.abspath(file_path or _DEFAULT_RULES_PATH)
    if not os.path.exists(file_path):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        reset_rich_text_rules_to_default(file_path)
    return file_path


def reset_rich_text_rules_to_default(file_path: Optional[str] = None) -> str:
    file_path = os.path.abspath(file_path or _DEFAULT_RULES_PATH)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_DEFAULT_RULES_YAML)
    invalidate_rich_text_rules_cache(file_path)
    return file_path


def invalidate_rich_text_rules_cache(file_path: Optional[str] = None) -> None:
    if file_path is None:
        _rules_cache.clear()
        return
    _rules_cache.pop(os.path.abspath(file_path), None)


def _compile_rule(rule: dict) -> Optional[dict]:
    if not isinstance(rule, dict) or not rule.get("enabled", True):
        return None
    pattern = rule.get("pattern", "")
    if not isinstance(pattern, str) or not pattern:
        return None
    style_value = rule.get("style") or {}
    try:
        style = TextStyle.from_dict(style_value).to_dict()
        compiled = re.compile(pattern if rule.get("regex", False) else re.escape(pattern))
    except (TypeError, ValueError, re.error) as exc:
        logger.warning("富文本规则编译失败: pattern=%r error=%s", pattern, exc)
        return None
    ruby = rule.get("ruby", "")
    if not isinstance(ruby, str):
        logger.warning("富文本规则编译失败: pattern=%r ruby 必须是字符串", pattern)
        return None
    tcy = bool(rule.get("tcy", False))
    if not style and not ruby and not tcy:
        return None
    return {
        "pattern": compiled,
        "style": style,
        "ruby": ruby,
        "tcy": tcy,
        "comment": str(rule.get("comment", "")),
    }


def _parse_rules(data: Any) -> dict:
    data = data if isinstance(data, dict) else {}
    parsed = {"common": [], "horizontal": [], "vertical": []}
    for group in parsed:
        values = data.get(group, [])
        if not isinstance(values, list):
            continue
        parsed[group] = [compiled for rule in values if (compiled := _compile_rule(rule)) is not None]
    return parsed


def load_rich_text_rules(file_path: Optional[str] = None) -> dict:
    file_path = ensure_rich_text_rules_exists(file_path)
    mtime = os.path.getmtime(file_path)
    cached = _rules_cache.get(file_path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(file_path, "r", encoding="utf-8") as handle:
        parsed = _parse_rules(yaml.safe_load(handle) or {})
    _rules_cache[file_path] = (mtime, parsed)
    return parsed


def _direction_group(direction: Any) -> str:
    if isinstance(direction, bool):
        return "vertical" if direction else "horizontal"
    if isinstance(direction, int):
        return "vertical" if direction else "horizontal"
    value = getattr(direction, "value", direction)
    value = str(value or "").lower()
    return "vertical" if value in {"v", "vertical", "vr"} else "horizontal"


def _iter_rules(rules: dict, direction: Any) -> Iterable[dict]:
    yield from rules.get("common", [])
    yield from rules.get(_direction_group(direction), [])


def _merge_style(base: dict, overlay: dict) -> dict:
    """Merge one automatic rule over an earlier automatic rule."""
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_style(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _add_missing_style(base: dict, automatic: dict) -> dict:
    """Add automatic fields without replacing existing rich-text fields."""
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (automatic or {}).items():
        if key not in result or result[key] is None:
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _add_missing_style(result[key], value)
    return result


@dataclass
class _RuleEntry:
    """One visible character while applying a rule.

    ``node`` is a manual or automatic ruby/tcy node (or ``None`` for a normal
    text run). Keeping that identity lets reconstruction split a run without
    destroying node structure.
    """

    char: str
    style: dict
    node: Optional[dict] = None
    automatic_style: dict = field(default_factory=dict)


def _append_rule_run_entries(runs: Any, node: Optional[dict], entries: List[_RuleEntry]) -> None:
    if not isinstance(runs, list):
        return
    for run in runs:
        if not isinstance(run, dict) or run.get("type", "text") != "text":
            continue
        style = run.get("style") if isinstance(run.get("style"), dict) else {}
        for char in str(run.get("text", "")):
            entries.append(
                _RuleEntry(
                    char=char,
                    style=copy.deepcopy(style),
                    node=node,
                )
            )


def _rule_entries_from_document(document: RichTextDocument) -> List[_RuleEntry]:
    entries: List[_RuleEntry] = []
    for block_index, block in enumerate(document.blocks):
        for inline in block.inlines:
            if isinstance(inline, TextRun):
                _append_rule_run_entries(
                    [{"type": "text", "text": inline.text, "style": inline.style.to_dict()}],
                    None,
                    entries,
                )
            elif inline.type == "ruby":
                _append_rule_run_entries(
                    [{"type": "text", "text": run.text, "style": run.style.to_dict()} for run in inline.base],
                    {"type": "ruby", "text": [run.to_dict() for run in inline.text]},
                    entries,
                )
            elif inline.type == "tcy":
                _append_rule_run_entries(
                    [{"type": "text", "text": run.text, "style": run.style.to_dict()} for run in inline.content],
                    {"type": "tcy"},
                    entries,
                )
        if block_index < len(document.blocks) - 1:
            entries.append(_RuleEntry("\n", {}))
    return entries


def _rule_entries_from_text(text: str) -> List[_RuleEntry]:
    return [_RuleEntry(char, {}) for char in text]


def _runs_from_rule_entries(text: str, entries: List[_RuleEntry]) -> List[dict]:
    if not text:
        return []
    runs: List[dict] = []
    start = 0
    current = entries[0].style if entries else {}
    for index in range(1, len(text)):
        style = entries[index].style if index < len(entries) else {}
        if style == current:
            continue
        runs.append({"type": "text", "text": text[start:index], "style": copy.deepcopy(current)})
        start = index
        current = style
    runs.append({"type": "text", "text": text[start:], "style": copy.deepcopy(current)})
    return runs


def _document_from_rule_entries(text: str, entries: List[_RuleEntry]) -> RichTextDocument:
    """Rebuild a document while retaining ruby/tcy node ownership."""
    if len(entries) < len(text):
        entries = entries + [_RuleEntry("", {}) for _ in range(len(text) - len(entries))]

    blocks: List[Paragraph] = []
    cursor = 0
    for line in text.split("\n"):
        line_end = cursor + len(line)
        inlines: List[Any] = []
        index = cursor
        while index < line_end:
            entry = entries[index]
            node = entry.node
            if node is None:
                index_end = index + 1
                while index_end < line_end and entries[index_end].node is None:
                    index_end += 1
            else:
                index_end = index + 1
                while index_end < line_end and entries[index_end].node is node:
                    index_end += 1

            runs = _runs_from_rule_entries(line[index - cursor:index_end - cursor], entries[index:index_end])
            if node is None:
                inlines.extend(runs)
            elif node.get("type") == "ruby":
                inlines.append({
                    "type": "ruby",
                    "base": runs,
                    "text": copy.deepcopy(node.get("text", [])),
                })
            else:
                inlines.append({"type": "tcy", "content": runs})
            index = index_end
        blocks.append(Paragraph.from_dict({"type": "paragraph", "inlines": inlines}))
        cursor = line_end + 1
    return RichTextDocument(blocks=blocks or [Paragraph()])


def _normalize_rule_linebreak_entries(text: str, entries: List[_RuleEntry]) -> tuple[str, List[_RuleEntry]]:
    """Convert legacy BR spellings to paragraph separators after matching.

    Matching intentionally happens on the original string for backwards
    compatibility (``.*`` can match a literal ``[BR]``).  The marker itself
    is never allowed to carry an automatic style into the resulting document.
    """
    normalized_text: List[str] = []
    normalized_entries: List[_RuleEntry] = []
    cursor = 0
    for match in _LINE_BREAK_RE.finditer(text):
        for index in range(cursor, match.start()):
            normalized_text.append(text[index])
            normalized_entries.append(entries[index])
        normalized_text.append("\n")
        normalized_entries.append(_RuleEntry("\n", {}))
        cursor = match.end()
    for index in range(cursor, len(text)):
        normalized_text.append(text[index])
        normalized_entries.append(entries[index])
    return "".join(normalized_text), normalized_entries


def apply_rich_text_rules(
    text: Any,
    direction: Any,
    rules: Optional[dict] = None,
    file_path: Optional[str] = None,
) -> Optional[RichTextDocument]:
    """Return an additively styled document, or ``None`` when nothing changed.

    ``text`` may be a plain string or an existing ``richtext.v1`` document.
    Existing style fields are intentionally retained; rules only fill fields
    that are absent, so automatic rules cannot overwrite editor-authored rich
    text.
    """
    existing_document = None
    if is_rich_text_document(text):
        existing_document = ensure_rich_text_document(text)
        match_text = existing_document.plain_text()
        entries = _rule_entries_from_document(existing_document)
    elif isinstance(text, str):
        match_text = text
        entries = _rule_entries_from_text(text)
    else:
        return None

    if not match_text:
        return None
    rules = rules if rules is not None else load_rich_text_rules(file_path)
    allow_tcy = _direction_group(direction) == "vertical"
    matched = False
    changed = False
    for rule in _iter_rules(rules, direction):
        for match in rule["pattern"].finditer(match_text):
            start, end = match.span()
            if start == end:
                continue
            matched = True
            for index in range(start, end):
                if index >= len(entries) or entries[index].char == "\n":
                    continue
                entry = entries[index]
                entry.automatic_style = _merge_style(entry.automatic_style, rule["style"])
            node = None
            if rule.get("ruby"):
                node = {
                    "type": "ruby",
                    "text": [{"type": "text", "text": rule["ruby"], "style": {}}],
                }
            elif allow_tcy and rule.get("tcy", False):
                node = {"type": "tcy"}
            target = entries[start:end]
            if (
                node is not None
                and _LINE_BREAK_RE.search(match_text, start, end) is None
                and all(entry.node is None for entry in target)
            ):
                for entry in target:
                    entry.node = node
                changed = True
    for entry in entries:
        if entry.char == "\n":
            continue
        merged_style = _add_missing_style(entry.style, entry.automatic_style)
        if merged_style != entry.style:
            entry.style = merged_style
            changed = True
    if not changed and (existing_document is not None or not matched):
        return None

    normalized_text, normalized_entries = _normalize_rule_linebreak_entries(match_text, entries)
    return _document_from_rule_entries(normalized_text, normalized_entries)


def apply_rich_text_rules_to_region(region: Any, direction: Any = None) -> bool:
    """Apply automatic rules while preserving existing rich-text fields."""
    if region is None:
        return False
    text = getattr(region, "translation_rich", None)
    if text is None:
        text = getattr(region, "translation", "")
    resolved_direction = direction if direction is not None else getattr(region, "direction", "horizontal")
    try:
        document = apply_rich_text_rules(text, resolved_direction)
    except Exception as exc:
        logger.warning("应用富文本规则失败，保留纯文本: %s", exc)
        return False
    if document is None:
        return False
    if hasattr(region, "set_translation_rich"):
        region.set_translation_rich(document)
    else:
        region.translation_rich = document.to_dict()
    region._rich_text_rules_applied = True
    return True
