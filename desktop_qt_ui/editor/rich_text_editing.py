"""Helpers for editing structured rich text from the Qt floating editor.

richtext.v1 协议的解析/序列化唯一实现位于 manga_translator.rendering.rich_text
（F11 收口）；本模块只负责编辑器侧的结构化编辑操作：光标级增删
（apply_text_change）、样式补丁（apply_style_to_range）、ruby/tcy 包装与解除。

所有编辑操作共享同一套「节点归属拍平」表示（F01/F17）：
文档 → 逐可见字符条目 _CharEntry(char, style, run, node)，其中
style/run/node 均为对原文档 dict 的共享引用（只读，不做逐字符深拷贝，F25）；
编辑 = 对条目序列做拼接/改写，再由 _document_from_entries 重建 —
连续同 node 的字符重组回原类型节点（ruby 保留原注音、tcy 重建 content），
node 为 None 的段按样式分组为 text run。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from manga_translator.rendering.rich_text import (
    RICH_TEXT_FORMAT,
    TextStyle,
    ensure_rich_text_document,
    is_rich_text_document,
    legacy_line_breaks_to_document,
    normalize_rich_linebreaks,
    plain_text_of,
)


def editor_text_to_plain_text(text: str) -> str:
    return str(text or "").replace("↵", "\n")


def utf16_length(text: str) -> int:
    """返回 Qt 文本 API 使用的 UTF-16 code unit 数。"""
    return len(str(text or "").encode("utf-16-le")) // 2


def python_index_to_utf16_offset(text: str, index: int) -> int:
    """把 Python 字符索引转换为 QTextCursor/contentsChange offset。"""
    text = str(text or "")
    index = max(0, min(int(index), len(text)))
    return utf16_length(text[:index])


def utf16_offset_to_python_index(
    text: str, offset: int, *, round_up: bool = False
) -> int:
    """把 UTF-16 offset 转换为 Python 字符索引。

    Qt 正常只会给出字符边界。若调用方传入代理对中间的位置，范围起点向下
    取整、范围终点可通过 ``round_up=True`` 向上取整，避免拆开非 BMP 字符。
    """
    text = str(text or "")
    offset = max(0, min(int(offset), utf16_length(text)))
    units = 0
    for index, char in enumerate(text):
        next_units = units + (2 if ord(char) > 0xFFFF else 1)
        if offset < next_units:
            return index + 1 if round_up else index
        if offset == next_units:
            return index + 1
        units = next_units
    return len(text)


def utf16_range_to_python_range(text: str, start: int, end: int) -> tuple[int, int]:
    """把 Qt UTF-16 选区安全转换为 Python 半开区间。"""
    start, end = sorted((int(start), int(end)))
    py_start = utf16_offset_to_python_index(text, start)
    py_end = utf16_offset_to_python_index(text, end, round_up=True)
    return py_start, max(py_start, py_end)


def plain_text_to_storage_text(text: str) -> str:
    # 刻意保留：写回 translation 字段时用 [BR] 标记换行，维持 PSD 导出等
    # 下游对 [BR] 形式的兼容（见审查报告 F31 备注）。
    return re.sub(r"\n+", "[BR]", str(text or ""))


def storage_text_to_editor_text(text: Any) -> str:
    if is_rich_text_document(text):
        return plain_text_of(text)
    # 薄委托：BR 标记 → 换行 的唯一实现在 rich_text.py（F14）。
    return normalize_rich_linebreaks(str(text or ""))


def document_from_region(region_data: dict) -> dict:
    rich = region_data.get("translation_rich")
    if is_rich_text_document(rich):
        try:
            # 严格解析 + to_dict：既做隔离拷贝，又把 RichTextDocument 实例
            # 归一成编辑器使用的 dict 形态；非法文档降级回纯文本而不是让
            # 编辑器崩溃（加载边界的整体降级见 F04）。
            return ensure_rich_text_document(rich).to_dict()
        except (ValueError, TypeError):
            pass
    # 换行/BR 标记 → 段落 的唯一实现在 rich_text.py（F11）。
    return legacy_line_breaks_to_document(
        str(region_data.get("translation", "") or "")
    ).to_dict()


def visible_text_from_document(document: Any) -> str:
    # 薄委托（F11）：plain_text_of 同时兼容 RichTextDocument 实例与 dict，
    # 纯字符串原样返回 —— 不再有 str(document) 垃圾输出分支。
    return plain_text_of(document)


def normalize_text_style(style: Any) -> dict:
    """富文本编辑器与规则编辑器共享的样式校验/归一化入口。"""
    if not isinstance(style, dict):
        style = {}
    return TextStyle.from_dict(copy.deepcopy(style)).to_dict()


def text_style_to_control_values(style: Any) -> dict:
    """把 richtext.v1 嵌套样式展开成控件可直接读写的扁平值。"""
    style = normalize_text_style(style)
    transform = style.get("transform") or {}
    stroke = style.get("stroke") or {}
    outer_stroke = style.get("outerStroke") or {}
    glow = style.get("glow") or {}
    italic = style.get("italic")
    # The renderer treats the legacy boolean form ``italic: true`` as the
    # reference 15-degree shear.  Expose that same numeric value in the rule
    # editor's angle control instead of coercing ``True`` to 1 degree.
    if italic is True:
        italic = 15.0
    return {
        "bold": bool(style.get("bold", False)),
        "underline": bool(style.get("underline", False)),
        "strikethrough": bool(style.get("strikethrough", False)),
        "emphasis": bool(style.get("emphasis", False)),
        "verticalAdvance": style.get("verticalAdvance"),
        "italic": italic,
        "color": style.get("color"),
        "fontSize": style.get("fontSize"),
        "scale": style.get("scale"),
        "fontFamily": style.get("fontFamily"),
        "stroke": copy.deepcopy(stroke) or None,
        "outerStroke": copy.deepcopy(outer_stroke) or None,
        "glow": copy.deepcopy(glow) or None,
        "kerning": style.get("kerning"),
        "preKerning": style.get("preKerning"),
        "lineKerning": style.get("lineKerning"),
        "nextKerning": style.get("nextKerning"),
        "rotation": transform.get("rotation"),
        "offsetX": transform.get("offsetX"),
        "offsetY": transform.get("offsetY"),
        "scaleX": transform.get("scaleX"),
        "scaleY": transform.get("scaleY"),
    }


def text_style_from_control_values(values: dict, enabled: set[str]) -> dict:
    """由共享控件值构建严格的 richtext.v1 style。未启用字段不写入。"""
    style: dict[str, Any] = {}
    for key in (
        "bold",
        "underline",
        "strikethrough",
        "emphasis",
        "italic",
        "color",
        "fontSize",
        "scale",
        "fontFamily",
        "stroke",
        "outerStroke",
        "glow",
        "kerning",
        "preKerning",
        "lineKerning",
        "nextKerning",
        "verticalAdvance",
    ):
        if key in enabled:
            style[key] = copy.deepcopy(values.get(key))
    transform = {
        key: values.get(key)
        for key in ("rotation", "offsetX", "offsetY", "scaleX", "scaleY")
        if key in enabled
    }
    if transform:
        style["transform"] = transform
    return normalize_text_style(style)


# ---------------------------------------------------------------------------
# 节点归属拍平（共享 walk，F01/F17/F25）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CharEntry:
    """一个可见字符及其原 text run、ruby/tcy 节点归属。"""

    char: str
    style: dict
    run: dict | None
    node: dict | None


def _visible_entries(document: Any) -> list[_CharEntry]:
    """把文档拍平成逐可见字符的归属条目（唯一的 blocks→inlines 游标行走）。

    ruby 的注音 runs 不占可见位置，不产出条目；base/content 依协议只含
    text run，非法嵌套节点在此被忽略（严格解析在渲染侧本就会拒绝它们）。
    """
    entries: list[_CharEntry] = []
    if not is_rich_text_document(document):
        return entries
    if not isinstance(document, dict):
        # RichTextDocument 实例 → 编辑器统一以 dict 形态操作
        document = ensure_rich_text_document(document).to_dict()
    blocks = document.get("blocks", [])
    if not isinstance(blocks, list):
        return entries
    for block_index, block in enumerate(blocks):
        inlines = block.get("inlines", []) if isinstance(block, dict) else []
        for inline in inlines:
            if not isinstance(inline, dict):
                continue
            inline_type = inline.get("type", "text")
            if inline_type == "ruby":
                _append_run_entries(inline.get("base", []), inline, entries)
            elif inline_type == "tcy":
                _append_run_entries(inline.get("content", []), inline, entries)
            else:
                _append_run_entries([inline], None, entries)
        if block_index < len(blocks) - 1:
            entries.append(_CharEntry("\n", {}, None, None))
    return entries


def _append_run_entries(
    runs: Any, node: dict | None, entries: list[_CharEntry]
) -> None:
    if not isinstance(runs, list):
        return
    for run in runs:
        if not isinstance(run, dict) or run.get("type", "text") != "text":
            continue
        style = run.get("style") if isinstance(run.get("style"), dict) else {}
        for char in str(run.get("text", "")):
            entries.append(_CharEntry(char, style, run, node))


def _document_from_entries(entries: list[_CharEntry]) -> dict:
    """Rebuild paragraphs and inline nodes from the canonical visible entries."""
    blocks: list[dict] = []
    line_start = 0
    while True:
        line_end = line_start
        while line_end < len(entries) and entries[line_end].char != "\n":
            line_end += 1

        inlines: list[dict] = []
        index = line_start
        while index < line_end:
            node = entries[index].node
            group_start = index
            index += 1
            while index < line_end and entries[index].node is node:
                index += 1
            runs = _runs_from_group(entries, group_start, index)
            if node is None:
                inlines.extend(runs)
            elif runs:
                if node.get("type") == "ruby":
                    inlines.append(
                        {
                            "type": "ruby",
                            "base": runs,
                            "text": copy.deepcopy(node.get("text", [])),
                        }
                    )
                else:
                    inlines.append({"type": "tcy", "content": runs})
        blocks.append({"type": "paragraph", "inlines": inlines})
        if line_end == len(entries):
            break
        line_start = line_end + 1

    return {"format": RICH_TEXT_FORMAT, "blocks": blocks}


def _runs_from_group(entries: list[_CharEntry], start: int, end: int) -> list[dict]:
    """Group adjacent entries with equal styles into the canonical text runs."""
    if start >= end:
        return []
    runs: list[dict] = []
    run_start = start
    current_style = entries[start].style
    for index in range(start + 1, end):
        style = entries[index].style
        if style is current_style or style == current_style:
            continue
        runs.append(
            {
                "type": "text",
                "text": "".join(
                    entries[offset].char for offset in range(run_start, index)
                ),
                "style": copy.deepcopy(current_style or {}),
            }
        )
        run_start = index
        current_style = style
    runs.append(
        {
            "type": "text",
            "text": "".join(entries[offset].char for offset in range(run_start, end)),
            "style": copy.deepcopy(current_style or {}),
        }
    )
    return runs


def _normalize_range(
    entries: list[_CharEntry], start: int, end: int, expand_empty: bool
) -> tuple[int, int]:
    length = len(entries)
    start = int(start)
    end = int(end)
    if expand_empty and start == end:
        return 0, length
    start = max(0, min(start, length))
    end = max(start, min(end, length))
    return start, end


def _runs_text(runs: Any) -> str:
    if not isinstance(runs, list):
        return ""
    return "".join(str(run.get("text", "")) for run in runs if isinstance(run, dict))


# ---------------------------------------------------------------------------
# 编辑操作
# ---------------------------------------------------------------------------


def apply_text_change(
    document: dict,
    editor_text: str,
    position: int,
    chars_removed: int,
    chars_added: int,
) -> dict:
    """按 QTextDocument.contentsChange 语义更新文档，保留 ruby/tcy 节点（F01）。"""
    return _apply_plain_text_change(
        document,
        editor_text_to_plain_text(editor_text),
        position,
        chars_removed,
        chars_added,
    )


def _apply_plain_text_change(
    document: dict,
    new_text: str,
    position: int,
    chars_removed: int,
    chars_added: int,
) -> dict:
    entries = _visible_entries(document)
    position = max(0, min(int(position), len(entries)))
    chars_removed = max(0, int(chars_removed))
    chars_added = max(0, int(chars_added))
    inherited_style, inherited_node = _insertion_inheritance(entries, position)
    inserted = [
        _CharEntry(char, inherited_style, None, inherited_node)
        for char in new_text[position : position + chars_added]
    ]
    new_entries = entries[:position] + inserted + entries[position + chars_removed :]
    for index, char in enumerate(new_text):
        if index < len(new_entries):
            new_entries[index].char = char
        else:
            new_entries.append(_CharEntry(char, {}, None, None))
    del new_entries[len(new_text) :]
    return _document_from_entries(new_entries)


def apply_qt_text_change(
    document: dict,
    old_editor_text: str,
    new_editor_text: str,
    position: int,
    chars_removed: int,
    chars_added: int,
) -> dict:
    """按 ``QTextDocument.contentsChange`` 的 UTF-16 语义更新文档。

    ``apply_text_change`` 的编辑逻辑使用 Python 字符索引；这里同时查看修改前
    后文本，把 Qt 给出的 position/removed/added code units 转换成不会拆开
    emoji 等非 BMP 字符的 Python 区间。
    """
    old_text = editor_text_to_plain_text(old_editor_text)
    new_text = editor_text_to_plain_text(new_editor_text)
    position = max(0, int(position))
    chars_removed = max(0, int(chars_removed))
    chars_added = max(0, int(chars_added))

    old_start = utf16_offset_to_python_index(old_text, position)
    old_end = utf16_offset_to_python_index(
        old_text,
        position + chars_removed,
        round_up=True,
    )
    new_start = utf16_offset_to_python_index(new_text, position)
    new_end = utf16_offset_to_python_index(
        new_text,
        position + chars_added,
        round_up=True,
    )
    # 修改点之前的文本相同，理论上 old_start == new_start。遇到异常 signal
    # 参数时取较小值，仍保证范围落在两份文本共同前缀内。
    change_start = min(old_start, new_start)
    removed_seg = old_text[change_start:old_end]
    added_seg = new_text[change_start:new_end]
    # IME 提交可能把整篇文档报成一次"全量替换"。对照前后文本裁掉报告区间
    # 首尾未变的部分，收窄成最小真实操作——未改动字符原地保留自己的样式
    # 与节点归属，而不是被当作新插入重建（样式丢失）。
    prefix = 0
    limit = min(len(removed_seg), len(added_seg))
    while prefix < limit and removed_seg[prefix] == added_seg[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and removed_seg[len(removed_seg) - 1 - suffix]
        == added_seg[len(added_seg) - 1 - suffix]
    ):
        suffix += 1
    return _apply_plain_text_change(
        document,
        new_text,
        change_start + prefix,
        len(removed_seg) - prefix - suffix,
        len(added_seg) - prefix - suffix,
    )


def _insertion_inheritance(
    entries: list[_CharEntry], position: int
) -> tuple[dict, dict | None]:
    """插入字符的样式与节点归属。

    样式：插入点严格位于同一 text run 内部（前后字符同 run）才继承该 run
    的样式，run 边缘不继承 —— 与旧实现语义一致。
    节点：插入点严格位于同一 ruby/tcy 节点内部（前后字符同节点）才并入该
    节点；节点前/后边缘或纯文本处插入 → 普通文本（F01 决策）。
    邻居按插入点在旧文档中的前后字符（position-1 / position）判定。
    """
    if position <= 0 or position >= len(entries):
        return {}, None
    prev = entries[position - 1]
    nxt = entries[position]
    style = prev.style if (prev.run is not None and prev.run is nxt.run) else {}
    node = prev.node if (prev.node is not None and prev.node is nxt.node) else None
    return style, node


def _mutate_range(document: dict, start: int, end: int, mutate) -> dict:
    """对区间内每个非换行条目应用 ``mutate`` 后重建文档。

    区间为空时返回原文档深拷贝；空区间不再展开为全文 —— 所有调用方都已
    持有真实选区，隐式改写整篇文本只会掩盖上层守卫的缺失。
    """
    entries = _visible_entries(document)
    start, end = _normalize_range(entries, start, end, expand_empty=False)
    if start == end:
        return copy.deepcopy(document)
    for entry in entries[start:end]:
        if entry.char != "\n":
            mutate(entry)
    return _document_from_entries(entries)


def _drop_node_of_type(node_type: str):
    def drop(entry: _CharEntry) -> None:
        if isinstance(entry.node, dict) and entry.node.get("type") == node_type:
            entry.node = None

    return drop


def apply_style_to_range(document: dict, start: int, end: int, patch: dict) -> dict:
    merged_by_style: dict[int, dict] = {}

    def restyle(entry: _CharEntry) -> None:
        merged = merged_by_style.get(id(entry.style))
        if merged is None:
            # Style editing and run inspection must share the exact same
            # richtext.v1 canonical form.  In particular, neutral transform
            # and spacing values (rotation/offset/kerning = 0) are omitted by
            # the protocol and must not survive only in the editor document.
            merged = normalize_text_style(_merge_style(entry.style, patch))
            merged_by_style[id(entry.style)] = merged
        entry.style = merged

    return _mutate_range(document, start, end, restyle)


def apply_tcy_to_range(document: dict, start: int, end: int) -> dict:
    return _wrap_range_as_node(document, start, end, "tcy")


def apply_ruby_to_range(document: dict, start: int, end: int, ruby_text: str) -> dict:
    ruby_text = str(ruby_text or "")
    if not ruby_text:
        return remove_ruby_from_range(document, start, end)
    return _wrap_range_as_node(document, start, end, "ruby", ruby_text=ruby_text)


def remove_ruby_from_range(document: dict, start: int, end: int) -> dict:
    return _mutate_range(document, start, end, _drop_node_of_type("ruby"))


def remove_tcy_from_range(document: dict, start: int, end: int) -> dict:
    return _mutate_range(document, start, end, _drop_node_of_type("tcy"))


def clear_styles_from_range(document: dict, start: int, end: int) -> dict:
    """Remove every inline style and ruby/tcy wrapper from a visible range."""

    def reset(entry: _CharEntry) -> None:
        entry.style = {}
        entry.node = None

    return _mutate_range(document, start, end, reset)


def _wrap_range_as_node(
    document: dict, start: int, end: int, node_type: str, ruby_text: str = ""
) -> dict:
    entries = _visible_entries(document)
    start, end = _normalize_range(entries, start, end, expand_empty=False)
    if start >= end:
        return copy.deepcopy(document)

    # 范围外的既有 ruby/tcy 原样保留（条目携带节点归属，重建时复原）。
    # 范围与既有节点部分/全部重叠：拆散被重叠的旧节点 —— 其全部字符降级为
    # 携带原样式的普通文本（协议不允许节点嵌套，部分重叠时保留残半节点会
    # 产生歧义），随后把范围包成新节点。
    overlapped_ids = {
        id(entry.node) for entry in entries[start:end] if entry.node is not None
    }
    if overlapped_ids:
        for entry in entries:
            if entry.node is not None and id(entry.node) in overlapped_ids:
                entry.node = None

    if node_type == "ruby":
        new_node = {
            "type": "ruby",
            "base": [],
            "text": [{"type": "text", "text": ruby_text, "style": {}}],
        }
    else:
        new_node = {"type": "tcy", "content": []}
    for entry in entries[start:end]:
        entry.node = new_node
    # 注：范围跨段落时按段落各自重组为一个节点（ruby 注音随之复制到每段），
    # 与旧实现的逐行包装行为一致。
    return _document_from_entries(entries)


# ---------------------------------------------------------------------------
# 查询（浮动编辑器工具栏状态）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StyledTextSegment:
    """文档中一个真实、连续且带局部样式的可见文字区间。"""

    start: int
    end: int
    text: str
    style: dict
    node_type: str | None = None
    ruby_text: str = ""
    node_start: int | None = None
    node_end: int | None = None


def styled_segments_for_range(
    document: dict,
    start: int = 0,
    end: int | None = None,
    *,
    expand_empty: bool = True,
) -> list[StyledTextSegment]:
    """按文本顺序返回真实样式片段，不包含默认/空样式文字。

    只合并相邻且样式、节点类型与 Ruby 文本完全相同的字符。中间只要隔着
    无样式文字或其他样式，即使两端样式值相同也会保留为两个片段。
    """
    entries = _visible_entries(document)
    if end is None:
        end = len(entries)
    start, end = _normalize_range(entries, start, end, expand_empty=expand_empty)
    if start >= end:
        return []

    normalized_styles: dict[int, dict] = {}
    node_ranges: dict[int, tuple[int, int]] = {}
    for index, entry in enumerate(entries):
        if entry.node is None or entry.char == "\n":
            continue
        node_id = id(entry.node)
        node_start, node_end = node_ranges.get(node_id, (index, index + 1))
        node_ranges[node_id] = (min(node_start, index), max(node_end, index + 1))
    segments: list[StyledTextSegment] = []
    current: dict[str, Any] | None = None

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        segments.append(
            StyledTextSegment(
                start=current["start"],
                end=current["end"],
                text="".join(current["chars"]),
                style=copy.deepcopy(current["style"]),
                node_type=current["node_type"],
                ruby_text=current["ruby_text"],
                node_start=current["node_start"],
                node_end=current["node_end"],
            )
        )
        current = None

    for index in range(start, end):
        entry = entries[index]
        if entry.char == "\n":
            finish_current()
            continue

        style_id = id(entry.style)
        style = normalized_styles.get(style_id)
        if style is None:
            style = normalize_text_style(entry.style)
            normalized_styles[style_id] = style

        node = entry.node if isinstance(entry.node, dict) else None
        node_type = node.get("type") if node is not None else None
        ruby_text = _runs_text(node.get("text", [])) if node_type == "ruby" else ""
        node_range = node_ranges.get(id(node)) if node is not None else None
        if not style and node_type not in {"ruby", "tcy"}:
            finish_current()
            continue

        can_merge = (
            current is not None
            and current["end"] == index
            and current["style"] == style
            and current["node_type"] == node_type
            and current["ruby_text"] == ruby_text
            and (
                node_type is None
                or (current["node_start"], current["node_end"]) == node_range
            )
        )
        if not can_merge:
            finish_current()
            current = {
                "start": index,
                "end": index + 1,
                "chars": [entry.char],
                "style": style,
                "node_type": node_type,
                "ruby_text": ruby_text,
                "node_start": node_range[0] if node_range else None,
                "node_end": node_range[1] if node_range else None,
            }
        else:
            current["end"] = index + 1
            current["chars"].append(entry.char)

    finish_current()
    return segments


def selected_range_from_editor(text_edit) -> tuple[int, int]:
    cursor = text_edit.textCursor()
    return utf16_range_to_python_range(
        text_edit.toPlainText(),
        cursor.selectionStart(),
        cursor.selectionEnd(),
    )


def style_for_range(document: dict, start: int, end: int) -> dict:
    entries = _visible_entries(document)
    start, end = _normalize_range(entries, start, end, expand_empty=True)
    styles = _styles_in_range(entries, start, end)
    result: dict[str, Any] = {}
    ruby_texts = _ruby_texts_in_range(entries, start, end)
    if ruby_texts:
        result["ruby"] = True
        result["rubyText"] = ruby_texts[0]
    if any(
        isinstance(entry.node, dict) and entry.node.get("type") == "tcy"
        for entry in entries[start:end]
    ):
        result["tcy"] = True
    for style in styles:
        if not isinstance(style, dict):
            continue
        for key in (
            "bold",
            "italic",
            "underline",
            "strikethrough",
            "scale",
            "emphasis",
            "noTcy",
            "verticalAdvance",
            "kerning",
            "preKerning",
            "lineKerning",
            "nextKerning",
        ):
            if key in style and key not in result:
                result[key] = style.get(key)
        if "color" in style and "color" not in result:
            result["color"] = style.get("color")
        if "fontSize" in style and "fontSize" not in result:
            result["fontSize"] = style.get("fontSize")
        if "fontFamily" in style and "fontFamily" not in result:
            result["fontFamily"] = style.get("fontFamily")
        stroke = style.get("stroke")
        if isinstance(stroke, dict):
            if "color" in stroke and "strokeColor" not in result:
                result["strokeColor"] = stroke.get("color")
            if "width" in stroke and "strokeWidth" not in result:
                result["strokeWidth"] = stroke.get("width")
        outer_stroke = style.get("outerStroke")
        if isinstance(outer_stroke, dict):
            if "color" in outer_stroke and "outerStrokeColor" not in result:
                result["outerStrokeColor"] = outer_stroke.get("color")
            if "width" in outer_stroke and "outerStrokeWidth" not in result:
                result["outerStrokeWidth"] = outer_stroke.get("width")
        glow = style.get("glow")
        if isinstance(glow, dict):
            if "color" in glow and "glowColor" not in result:
                result["glowColor"] = glow.get("color")
            if "blur" in glow and "glowBlur" not in result:
                result["glowBlur"] = glow.get("blur")
        transform = style.get("transform")
        if isinstance(transform, dict):
            for source, target in (
                ("offsetX", "offsetX"),
                ("offsetY", "offsetY"),
                ("rotation", "rotation"),
                ("mirrorX", "mirrorX"),
                ("mirrorY", "mirrorY"),
            ):
                if source in transform and target not in result:
                    result[target] = transform.get(source)
    return result


def style_row_coverage(
    document: dict, start: int, end: int, row_key: str
) -> tuple[bool, bool]:
    """返回样式在范围内的 (任意文字使用, 全部文字使用)。

    空选区沿用工具栏的全文查看语义；换行符不参与覆盖率计算。
    """
    entries = _visible_entries(document)
    start, end = _normalize_range(entries, start, end, expand_empty=True)
    visible_entries = [entry for entry in entries[start:end] if entry.char != "\n"]
    if not visible_entries:
        return False, False

    matched = [
        _style_row_value(entry, row_key) is not _UNSET for entry in visible_entries
    ]
    return any(matched), all(matched)


def _styles_in_range(entries: list[_CharEntry], start: int, end: int) -> list[dict]:
    """范围内各 text run 的样式（共享引用，仅供只读），按出现顺序去重相邻。"""
    styles: list[dict] = []
    last_run: Any = _UNSET
    for entry in entries[start:end]:
        if entry.char == "\n":
            continue
        if entry.run is not last_run:
            styles.append(entry.style)
            last_run = entry.run
    return styles


_UNSET = object()
_BOOLEAN_STYLE_ROWS = {
    "B": "bold",
    "U": "underline",
    "ST": "strikethrough",
    "D": "emphasis",
}
_DIRECT_STYLE_ROWS = {
    "C": "color",
    "I": "italic",
    "S": "fontSize",
    "%": "scale",
    "F": "fontFamily",
    "FA": "verticalAdvance",
    "K": "kerning",
    "PK": "preKerning",
    "LK": "lineKerning",
    "NK": "nextKerning",
}
_NESTED_STYLE_ROWS = {"O": "stroke", "G": "glow", "OS": "outerStroke"}


def _ruby_texts_in_range(entries: list[_CharEntry], start: int, end: int) -> list[str]:
    texts: list[str] = []
    seen: set[int] = set()
    for entry in entries[start:end]:
        node = entry.node
        if (
            not (isinstance(node, dict) and node.get("type") == "ruby")
            or id(node) in seen
        ):
            continue
        seen.add(id(node))
        ruby_text = _runs_text(node.get("text", []))
        if ruby_text:
            texts.append(ruby_text)
    return texts


def styled_text_for_key(document: dict, start: int, end: int, row_key: str) -> str:
    entries = _visible_entries(document)
    start, end = _normalize_range(entries, start, end, expand_empty=True)
    matches: list[str] = []
    current_chars: list[str] = []
    current_signature: Any = _UNSET

    def finish_match() -> None:
        nonlocal current_signature
        if not current_chars:
            return
        matches.append(_compact_display_text("".join(current_chars)))
        current_chars.clear()
        current_signature = _UNSET

    for entry in entries[start:end]:
        if entry.char == "\n":
            finish_match()
            continue
        signature = _style_row_value(entry, row_key)
        if signature is _UNSET:
            finish_match()
            continue
        if current_chars and signature != current_signature:
            finish_match()
        current_signature = signature
        current_chars.append(entry.char)
    finish_match()
    return " / ".join(item for item in matches if item)


def _style_row_value(entry: _CharEntry, row_key: str) -> Any:
    """Return a row's canonical value, or ``_UNSET`` when it is not active."""
    node = entry.node if isinstance(entry.node, dict) else None
    if row_key == "R":
        if node is not None and node.get("type") == "ruby":
            return ("ruby", _runs_text(node.get("text", [])))
        return _UNSET
    if row_key == "T":
        return True if node is not None and node.get("type") == "tcy" else _UNSET

    style = entry.style or {}
    boolean_key = _BOOLEAN_STYLE_ROWS.get(row_key)
    if boolean_key is not None:
        return True if style.get(boolean_key) else _UNSET

    direct_key = _DIRECT_STYLE_ROWS.get(row_key)
    if direct_key is not None:
        return style[direct_key] if direct_key in style else _UNSET

    nested_key = _NESTED_STYLE_ROWS.get(row_key)
    if nested_key is not None:
        value = style.get(nested_key)
        return copy.deepcopy(value) if isinstance(value, dict) and value else _UNSET

    transform = style.get("transform")
    if not isinstance(transform, dict):
        return _UNSET
    if row_key == "Rot":
        return transform["rotation"] if "rotation" in transform else _UNSET
    if row_key == "XY":
        if "offsetX" in transform or "offsetY" in transform:
            return (transform.get("offsetX"), transform.get("offsetY"))
        return _UNSET
    if row_key == "WH":
        if "scaleX" in transform or "scaleY" in transform:
            return (transform.get("scaleX"), transform.get("scaleY"))
        return _UNSET
    if row_key in {"M", "MV"}:
        key = "mirrorX" if row_key == "M" else "mirrorY"
        return True if transform.get(key) else _UNSET
    return _UNSET


def _compact_display_text(text: str, limit: int = 12) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _merge_style(style: dict, patch: dict) -> dict:
    merged = copy.deepcopy(style) if isinstance(style, dict) else {}
    for key, value in patch.items():
        if key in {"stroke", "outerStroke", "glow", "transform"}:
            if value is None:
                merged.pop(key, None)
                continue
            nested = merged.get(key) if isinstance(merged.get(key), dict) else {}
            nested.update({k: v for k, v in value.items() if v is not None})
            for nested_key, nested_value in list(value.items()):
                if nested_value is None:
                    nested.pop(nested_key, None)
            if nested:
                merged[key] = nested
            else:
                merged.pop(key, None)
        elif value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
