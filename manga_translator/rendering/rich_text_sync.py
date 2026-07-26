"""编辑操作驱动的富文本同步。

纯文本译文框的每次编辑由 QTextDocument.contentsChange 产出精确的
``(位置, 删除数, 插入文本)`` 操作记录;本模块把这些操作按同样位置回放到
richtext.v1 文档的字符条目上,让样式跟随未改动的字符,不做任何 diff 推测。

坐标口径:所有文本均为 ``\n`` 换行的"编辑框规范形"(编辑框中的字面 ``↵``
由调用方先换成 ``\n``),与文档 ``plain_text()`` 一一对应;模型层的
``[BR]`` 形式不在本模块出现。

样式策略(用户拍板):
- 编辑插入的字符只在"中间"继承样式——前后邻居都存在、都不是换行、
  且样式相同才继承;插在文本或样式段的头尾一律不继承(空样式)。
- 替换规则命中的区间整体换血,新字符取被替换段首字符的样式。
- ruby/tcy 节点:插入点前后邻居属于同一节点时新字符并入该节点
  (避免节点被从中切成两份);替换区间完整落在同一节点内时结果保留节点。

任何校验不通过(文本对不上、操作越界)都返回 ``None``,由调用方回退
"删除富文本、只留纯文本"的安全路径。
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, List, Optional, Sequence

from .rich_text import RichTextDocument, ensure_rich_text_document
from .rich_text_rules import (
    _document_from_rule_entries,
    _rule_entries_from_document,
    _RuleEntry,
)
from .text_replacements import load_replacements

logger = logging.getLogger(__name__)


def _text_of(entries: Sequence[_RuleEntry]) -> str:
    return "".join(entry.char for entry in entries)


def _copy_entries(entries: Sequence[_RuleEntry]) -> List[_RuleEntry]:
    # node 保持同一对象引用:重建文档时按 identity 分组,深拷贝会拆散节点。
    return [
        _RuleEntry(entry.char, copy.deepcopy(entry.style), node=entry.node)
        for entry in entries
    ]


def _collapse_linebreak_entries(entries: Sequence[_RuleEntry]) -> List[_RuleEntry]:
    """连续换行压成一个,与模型层 ``\\n+ -> [BR]`` 的口径对齐。"""
    out: List[_RuleEntry] = []
    for entry in entries:
        if entry.char == "\n" and out and out[-1].char == "\n":
            continue
        out.append(entry)
    return out


def _apply_edit_ops(entries: List[_RuleEntry], ops: Sequence[Any]) -> List[_RuleEntry]:
    """按顺序回放编辑操作;位置越界抛 ValueError。"""
    for op in ops:
        position, removed, inserted = int(op[0]), int(op[1]), str(op[2])
        if position < 0 or removed < 0 or position > len(entries):
            raise ValueError(
                f"edit op out of range: pos={position} removed={removed} len={len(entries)}"
            )
        # Qt 怪癖:改动涉及文档末尾时 contentsChange 会把末尾段落分隔符
        # 计入 charsRemoved(全选替换 6 字符报 removed=7),这里钳到实际长度;
        # 钳错了也会被调用方的 post_text 校验兜住。
        removed = min(removed, len(entries) - position)
        del entries[position:position + removed]

        prev_entry = entries[position - 1] if position > 0 else None
        next_entry = entries[position] if position < len(entries) else None
        inherited_style: dict = {}
        if (
            prev_entry is not None
            and next_entry is not None
            and prev_entry.char != "\n"
            and next_entry.char != "\n"
            and prev_entry.style == next_entry.style
        ):
            inherited_style = prev_entry.style
        # 节点归属独立判断:夹在同一节点内部必须并入,否则节点会被切成两份。
        inherited_node = None
        if (
            prev_entry is not None
            and next_entry is not None
            and prev_entry.node is not None
            and prev_entry.node is next_entry.node
        ):
            inherited_node = prev_entry.node

        new_entries: List[_RuleEntry] = []
        for char in inserted:
            if char == "\n":
                new_entries.append(_RuleEntry("\n", {}))
            else:
                new_entries.append(
                    _RuleEntry(char, copy.deepcopy(inherited_style), node=inherited_node)
                )
        entries[position:position] = new_entries
    return entries


def apply_replacements_to_entries(
    entries: List[_RuleEntry],
    direction: int,
    replacements: Optional[dict] = None,
) -> List[_RuleEntry]:
    """在字符条目列表上跑替换规则(text_replacements 的条目版)。

    与纯文本版的差异:纯文本版用占位符保护 ``[BR]`` 等换行标记,这里文本
    是 ``\\n`` 口径,等价地跳过跨换行的命中。替换出的新字符继承被替换段
    首字符的样式;区间未完整落在同一 ruby/tcy 节点内时结果不带节点。
    """
    if replacements is None:
        replacements = load_replacements()

    group_key = "vertical" if direction == 1 else "horizontal"
    for key in ("common", group_key):
        for pattern, repl in replacements.get(key, []):
            text = _text_of(entries)
            matches = list(pattern.finditer(text))
            for match in reversed(matches):
                if match.start() == match.end():
                    continue
                if "\n" in match.group(0):
                    continue
                try:
                    replacement_text = match.expand(repl)
                except Exception:
                    replacement_text = repl
                span = entries[match.start():match.end()]
                first = span[0]
                node = first.node if all(e.node is first.node for e in span) else None
                new_entries: List[_RuleEntry] = []
                for char in replacement_text:
                    if char == "\n":
                        new_entries.append(_RuleEntry("\n", {}))
                    else:
                        new_entries.append(
                            _RuleEntry(char, copy.deepcopy(first.style), node=node)
                        )
                entries[match.start():match.end()] = new_entries
    return entries


def _map_styles_to_raw(
    raw_text: str,
    doc_entries: List[_RuleEntry],
    direction: int,
    replacements: Optional[dict],
) -> Optional[List[_RuleEntry]]:
    """把锚在替换后文本上的样式搬回 raw 坐标。

    用"每个字符带自己下标"的哨兵样式重跑一遍条目版替换,得到
    译文位置 -> raw 位置的对应关系:没被替换的字符一一对应(精确),
    被替换区间的输出字符都对应区间首字符(与替换的段首样式策略一致)。
    对应不上(规则版本变了等)返回 None。
    """
    doc_text = _text_of(doc_entries)
    if raw_text == doc_text:
        return _copy_entries(doc_entries)

    corr = [_RuleEntry(char, {"__src": i}) for i, char in enumerate(raw_text)]
    corr = apply_replacements_to_entries(corr, direction, replacements)
    if _text_of(corr) != doc_text:
        return None

    style_by_src: dict = {}
    node_by_src: dict = {}
    for pos, entry in enumerate(corr):
        src = entry.style.get("__src")
        if src is None or src in style_by_src:
            continue
        style_by_src[src] = doc_entries[pos].style
        node_by_src[src] = doc_entries[pos].node

    result: List[_RuleEntry] = []
    last_style: dict = {}
    last_node = None
    for i, char in enumerate(raw_text):
        if i in style_by_src:
            last_style = style_by_src[i]
            last_node = node_by_src[i]
        # 未映射到的字符(被替换段的非首字符)沿用段首样式(向左继承)。
        result.append(_RuleEntry(char, copy.deepcopy(last_style), node=last_node))
    return result


def document_after_edit_ops(
    document_value: Any,
    ops: Sequence[Any],
    pre_text: str,
    post_text: str,
) -> Optional[RichTextDocument]:
    """直接编辑"译文"模式:操作位置与文档正文同坐标,原地回放。"""
    try:
        document = ensure_rich_text_document(document_value)
    except Exception as exc:
        logger.warning("富文本文档解析失败: %s", exc)
        return None

    entries = _collapse_linebreak_entries(_rule_entries_from_document(document))
    if _text_of(entries) != pre_text:
        return None
    try:
        entries = _apply_edit_ops(_copy_entries(entries), ops)
    except ValueError as exc:
        logger.warning("编辑操作回放失败: %s", exc)
        return None
    if _text_of(entries) != post_text:
        return None
    return _document_from_rule_entries(_text_of(entries), entries)


def sync_document_for_raw_edit(
    document_value: Any,
    raw_pre_text: str,
    ops: Sequence[Any],
    raw_post_text: str,
    direction: int,
    replacements: Optional[dict] = None,
) -> Optional[RichTextDocument]:
    """编辑"替换前译文"模式:样式搬回 raw 坐标 → 回放操作 → 重跑替换。"""
    try:
        document = ensure_rich_text_document(document_value)
    except Exception as exc:
        logger.warning("富文本文档解析失败: %s", exc)
        return None

    doc_entries = _collapse_linebreak_entries(_rule_entries_from_document(document))
    entries = _map_styles_to_raw(raw_pre_text, doc_entries, direction, replacements)
    if entries is None:
        return None
    try:
        entries = _apply_edit_ops(entries, ops)
    except ValueError as exc:
        logger.warning("编辑操作回放失败: %s", exc)
        return None
    if _text_of(entries) != raw_post_text:
        return None
    entries = apply_replacements_to_entries(entries, direction, replacements)
    return _document_from_rule_entries(_text_of(entries), entries)


def document_has_styling(document: RichTextDocument) -> bool:
    """文档是否还有任何样式或 ruby/tcy 节点;没有就不值得保留富文本字段。"""
    entries = _rule_entries_from_document(document)
    return any(entry.style or entry.node is not None for entry in entries)


def _direction_to_int(direction_value: Any) -> int:
    """区域排版方向值 → 替换规则的 direction 参数(0=横排, 1=竖排)。"""
    return 0 if direction_value in ("h", "horizontal", "hr") else 1


def sync_region_rich_translation(
    old_rich: Any,
    edit_info: Any,
    *,
    raw_mode: bool,
    new_translation: str,
    direction_value: Any = "h",
    replacements: Optional[dict] = None,
) -> Optional[dict]:
    """区域译文编辑的富文本对齐统一入口。

    校验操作记录 → 同步文档 → 正文折算回模型口径([BR])与新译文比对 →
    无样式则退化。返回可直接写回 ``translation_rich`` 的 dict;没有旧富文本、
    没有操作记录或任何校验失败返回 ``None``(调用方删除富文本退回纯文本)。
    """
    if not old_rich:
        return None
    info = edit_info if isinstance(edit_info, dict) else None
    ops = info.get("ops") if info else None
    if not ops:
        logger.info("译文整段替换,无编辑操作记录,富文本退回纯文本")
        return None

    try:
        if raw_mode:
            document = sync_document_for_raw_edit(
                old_rich,
                info.get("pre_text", ""),
                ops,
                info.get("post_text", ""),
                _direction_to_int(direction_value),
                replacements,
            )
        else:
            document = document_after_edit_ops(
                old_rich,
                ops,
                info.get("pre_text", ""),
                info.get("post_text", ""),
            )
    except Exception as exc:
        logger.warning("富文本样式同步异常,退回纯文本: %s", exc)
        return None
    if document is None:
        logger.warning("富文本样式同步校验失败,退回纯文本")
        return None

    if re.sub(r"\n+", "[BR]", document.plain_text()) != new_translation:
        logger.warning("同步后富文本正文与译文不一致,退回纯文本")
        return None
    if not document_has_styling(document):
        return None
    return document.to_dict()
