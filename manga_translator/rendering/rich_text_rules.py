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
    pattern: '["''ー⸺～﹏●•〖〗［］]'
    regex: true
    style:
      transform:
        rotation: -90
    ruby: ""
    tcy: false
    comment: "竖排中将无专用替换字形的符号包装为富文本并旋转90度（引擎正角度为逆时针，竖排取顺时针即 -90；有竖排形字符的括号已在文本替换表映射）"
  - enabled: true
    pattern: '[!?！？]{2,4}'
    regex: true
    style: {}
    ruby: ""
    tcy: true
    comment: "竖排中将连续的问号和感叹号按每组 2–4 个应用纵中横"
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
    # ``ruby: null``（YAML 空值）等价于没有注音，而不是整条规则非法。
    ruby = rule.get("ruby") or ""
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


def _common_affixes(old_text: str, new_text: str) -> Tuple[int, int]:
    """两份文本的公共前缀/后缀长度（后缀不与前缀重叠）。"""
    limit = min(len(old_text), len(new_text))
    prefix = 0
    while prefix < limit and old_text[prefix] == new_text[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old_text[len(old_text) - 1 - suffix] == new_text[len(new_text) - 1 - suffix]
    ):
        suffix += 1
    return prefix, suffix


def _is_old_match(
    start: int,
    end: int,
    affixes: Tuple[int, int],
    old_len: int,
    new_len: int,
    old_spans: set,
) -> bool:
    """新文本命中是否在编辑前就已存在（编辑器增量语义）。

    完全落在公共前缀/后缀里的命中可以平移回旧文本坐标，去旧命中集查表；
    查不到（如锚点/环视让旧文本同位置不命中）仍算新命中。碰到变化窗的
    命中在旧文本里不可能有对应 —— 一律算新命中。
    """
    prefix, suffix = affixes
    if end <= prefix:
        return (start, end) in old_spans
    if start >= new_len - suffix:
        shift = old_len - new_len
        return (start + shift, end + shift) in old_spans
    return False


def _style_is_subset(style: Any, product: Any) -> bool:
    """style 的每个字段是否都能由 product（规则产物）原样给出。"""
    if not style:
        return True
    if not isinstance(style, dict) or not isinstance(product, dict):
        return False
    for key, value in style.items():
        if key not in product:
            return False
        expected = product[key]
        if isinstance(value, dict) or isinstance(expected, dict):
            if not (
                isinstance(value, dict)
                and isinstance(expected, dict)
                and _style_is_subset(value, expected)
            ):
                return False
        elif value != expected:
            return False
    return True


def _node_ruby_text(node: dict) -> str:
    runs = node.get("text", [])
    if not isinstance(runs, list):
        return ""
    return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict))


def _match_has_manual_trace(
    entries: List["_RuleEntry"],
    start: int,
    end: int,
    rule: dict,
    allow_tcy: bool,
) -> bool:
    """命中区间是否带有"本规则给不出"的富文本（手工痕迹）。

    区间上只有与规则产物一致的残留样式/节点 → 视为上次自动应用的残余，
    允许整体补齐；出现任何规则产不出的字段、注音文本不同、节点越出命中
    区间等情况 → 视为手工痕迹，调用方应整段跳过该命中。
    """
    rule_style = rule.get("style") or {}
    rule_ruby = rule.get("ruby") or ""
    rule_tcy = bool(allow_tcy and rule.get("tcy", False))
    nodes_inside: set = set()
    for entry in entries[start:end]:
        if entry.char == "\n":
            continue
        if not _style_is_subset(entry.style, rule_style):
            return True
        node = entry.node
        if node is None:
            continue
        nodes_inside.add(id(node))
        node_type = node.get("type")
        if node_type == "ruby":
            if not rule_ruby or _node_ruby_text(node) != rule_ruby:
                return True
        elif node_type == "tcy":
            if not rule_tcy:
                return True
        else:
            return True
    if nodes_inside:
        for index, entry in enumerate(entries):
            if start <= index < end:
                continue
            if entry.node is not None and id(entry.node) in nodes_inside:
                return True
    return False


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
    *,
    previous_text: Optional[str] = None,
    styled_match_policy: str = "fill",
) -> Optional[RichTextDocument]:
    """Return an additively styled document, or ``None`` when nothing changed.

    ``text`` may be a plain string or an existing ``richtext.v1`` document.

    ``previous_text``（编辑器增量语义）：给出编辑前正文时，规则在新旧两份
    文本上各匹配一遍，只应用"编辑前不存在"的新命中 —— 未改动文字上的老
    命中永不重复应用（手动清掉的样式不会被顶回来）。``None`` 时全部命中
    都算新命中（渲染管线语义）。

    ``styled_match_policy``：``fill``（管线现行为）按字段补缺，已有字段
    保留；``skip``（编辑器）命中区间带任何本规则给不出的富文本（手工
    痕迹）时整段跳过，只有规则自己的残留样式允许整体补齐。
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
    affixes = (
        _common_affixes(previous_text, match_text) if previous_text is not None else None
    )
    allow_tcy = _direction_group(direction) == "vertical"
    matched = False
    changed = False
    for rule in _iter_rules(rules, direction):
        old_spans = (
            {m.span() for m in rule["pattern"].finditer(previous_text)}
            if previous_text is not None
            else None
        )
        for match in rule["pattern"].finditer(match_text):
            start, end = match.span()
            if start == end:
                continue
            if old_spans is not None and _is_old_match(
                start, end, affixes, len(previous_text), len(match_text), old_spans
            ):
                continue
            if styled_match_policy == "skip" and _match_has_manual_trace(
                entries, start, end, rule, allow_tcy
            ):
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
            if node is not None and _LINE_BREAK_RE.search(match_text, start, end) is None:
                if all(entry.node is None for entry in target):
                    for entry in target:
                        entry.node = node
                    changed = True
                elif styled_match_policy == "skip":
                    # 手工痕迹检查已保证区间内只有本规则的残留节点；若尚未被
                    # 单个节点完整覆盖（部分删除后的残余、相邻两个同注音节点），
                    # 整体重包补齐，已完整覆盖则维持原节点不动。
                    first_node = target[0].node
                    if first_node is None or any(
                        entry.node is not first_node for entry in target
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
