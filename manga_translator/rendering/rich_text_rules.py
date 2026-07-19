"""Automatic rich-text styling rules applied after text replacements."""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from manga_translator.runtime_paths import get_config_path

from ..utils import get_logger
from .rich_text import Paragraph, RichTextDocument, TcyRun, TextRun, TextStyle


logger = get_logger("rich_text_rules")

_DEFAULT_RULES_PATH = get_config_path("rich_text_rules.yaml")
_rules_cache: Dict[str, Tuple[float, dict]] = {}
_LINE_BREAK_RE = re.compile(r"(?:\[BR\]|【BR】|<br\s*/?>|\r\n|\r|\n)", re.IGNORECASE)

_DEFAULT_RULES_YAML = """# 富文本规则配置
# 执行顺序：common -> horizontal / vertical；规则匹配的是文本替换完成后的译文。
# 后面的规则会覆盖前面规则中重复设置的样式字段，但不会修改文字内容。
common:
  - enabled: false
    pattern: "示例"
    regex: false
    style: {}
    tcy: false
    comment: "示例规则（启用前请配置需要的富文本样式）"

horizontal: []
vertical:
  - enabled: true
    pattern: '["''ー⸺–—～﹏…⋯●•（《〈【〖〔［｛）》〉】〗〕］｝]'
    regex: true
    style:
      transform:
        rotation: 90
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
    tcy = bool(rule.get("tcy", False))
    if not style and not tcy:
        return None
    return {
        "pattern": compiled,
        "style": style,
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
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_style(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _runs_for_slice(text: str, styles: List[dict], start: int, end: int) -> List[TextRun]:
    runs: List[TextRun] = []
    cursor = start
    while cursor < end:
        style = styles[cursor]
        next_cursor = cursor + 1
        while next_cursor < end and styles[next_cursor] == style:
            next_cursor += 1
        runs.append(TextRun(text[cursor:next_cursor], TextStyle.from_dict(style)))
        cursor = next_cursor
    return runs


def _inlines_for_slice(
    text: str,
    styles: List[dict],
    tcy_flags: List[bool],
    start: int,
    end: int,
) -> list:
    inlines = []
    cursor = start
    while cursor < end:
        tcy = tcy_flags[cursor]
        next_cursor = cursor + 1
        while next_cursor < end and tcy_flags[next_cursor] == tcy:
            next_cursor += 1
        runs = _runs_for_slice(text, styles, cursor, next_cursor)
        if tcy:
            inlines.append(TcyRun(content=runs))
        else:
            inlines.extend(runs)
        cursor = next_cursor
    return inlines


def apply_rich_text_rules(
    text: str,
    direction: Any,
    rules: Optional[dict] = None,
    file_path: Optional[str] = None,
) -> Optional[RichTextDocument]:
    """Return a styled document, or ``None`` when no rule matched."""
    if not isinstance(text, str) or not text:
        return None
    rules = rules if rules is not None else load_rich_text_rules(file_path)
    styles: List[dict] = [{} for _ in text]
    tcy_flags = [False for _ in text]
    allow_tcy = _direction_group(direction) == "vertical"
    matched = False
    for rule in _iter_rules(rules, direction):
        for match in rule["pattern"].finditer(text):
            start, end = match.span()
            if start == end:
                continue
            matched = True
            for index in range(start, end):
                styles[index] = _merge_style(styles[index], rule["style"])
                if allow_tcy and rule.get("tcy", False):
                    tcy_flags[index] = True
    if not matched:
        return None

    blocks: List[Paragraph] = []
    cursor = 0
    for line_break in _LINE_BREAK_RE.finditer(text):
        blocks.append(Paragraph(inlines=_inlines_for_slice(text, styles, tcy_flags, cursor, line_break.start())))
        cursor = line_break.end()
    blocks.append(Paragraph(inlines=_inlines_for_slice(text, styles, tcy_flags, cursor, len(text))))
    return RichTextDocument(blocks=blocks)


def apply_rich_text_rules_to_region(region: Any, direction: Any = None) -> bool:
    """Apply automatic rules without overwriting an existing rich-text document."""
    if region is None or getattr(region, "translation_rich", None) is not None:
        return False
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
