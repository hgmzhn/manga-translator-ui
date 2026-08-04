"""批量管理引擎 —— 条件求值、批量动作、``_translations.json`` 读改写。

纯逻辑，零 Qt 依赖，线程封装在 ``batch_edit_service`` 里；这样条件求值和写回
语义可以直接单测（与 ``file_list_data_service.build_file_catalog_snapshot``
把纯函数入口独立出来是同一个理由）。

三条必须守住的约定（来自对现有链路的调研）：

1. **全量读 → 局部改 → 全量写。** ``export_service`` 保存时是从零重建 dict 的，
   会丢 ``mask_raw`` / ``original_width|height`` / overlays。这里只替换命中的
   region 条目，其余键原样保留。
2. **匹配跑在富文本正文（``\\n``）上**，不是 ``translation``（``[BR]``）上，
   否则 ``[BR]`` 四个字符会污染字符下标。
3. **改文字后富文本必须跟着走。** 用 ``apply_text_change`` 做区间拼接（未改动
   字符保住自己的样式），结果无样式时删掉 ``translation_rich`` 字段而不是留个
   空文档。
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

from editor.rich_text_editing import (
    apply_ruby_to_range,
    apply_style_to_range,
    apply_tcy_to_range,
    clear_styles_from_range,
    document_from_region,
    plain_text_to_storage_text,
    remove_ruby_from_range,
    remove_tcy_from_range,
    storage_text_to_editor_text,
    styled_segments_for_range,
    visible_text_from_document,
)
from editor.rich_text_presets import normalize_rich_text_preset
from manga_translator.rendering.rich_text import ensure_rich_text_document
from manga_translator.rendering.rich_text_sync import document_has_styling
from utils.json_encoder import CustomJSONEncoder

from .batch_edit_schemes import (
    ACTION_REPLACE_TEXT,
    ACTION_RICH_TEXT,
    ACTION_SET_FIELDS,
    LOGIC_ANY,
    RICH_MODE_CLEAR,
    RICH_MODE_FILL,
    RICH_MODE_OVERWRITE,
)


class BatchEditCancelled(RuntimeError):
    """扫描/执行被调用方取消。"""


# ─── 字段表 ───

KIND_TEXT = "text"
KIND_ENUM = "enum"
KIND_NUMBER = "number"
KIND_COLOR = "color"
KIND_BOOL = "bool"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: str
    label: str
    choices: tuple[str, ...] = ()
    #: 派生字段只能做条件，不能被 set_fields 写
    writable: bool = True
    integer: bool = False


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("translation", KIND_TEXT, "Translation"),
    FieldSpec("text", KIND_TEXT, "Source Text", writable=False),
    FieldSpec("translation_raw", KIND_TEXT, "Translation (pre-replacement)"),
    FieldSpec("font_family", KIND_TEXT, "Font Family"),
    FieldSpec("target_lang", KIND_TEXT, "Target Language"),
    FieldSpec("source_lang", KIND_TEXT, "Source Language"),
    FieldSpec("direction", KIND_ENUM, "Direction", ("h", "v", "hr", "vr", "auto")),
    FieldSpec("alignment", KIND_ENUM, "Alignment", ("left", "center", "right", "auto")),
    FieldSpec("font_size", KIND_NUMBER, "Font Size", integer=True),
    FieldSpec("angle", KIND_NUMBER, "Angle"),
    FieldSpec("line_spacing", KIND_NUMBER, "Line Spacing"),
    FieldSpec("letter_spacing", KIND_NUMBER, "Letter Spacing"),
    FieldSpec("stroke_width", KIND_NUMBER, "Stroke Width"),
    FieldSpec("prob", KIND_NUMBER, "OCR Confidence", writable=False),
    FieldSpec("fg_colors", KIND_COLOR, "Text Color"),
    FieldSpec("bg_colors", KIND_COLOR, "Stroke Color"),
    # region 级的 bold/italic/underline/font_weight 虽然在 TextBlock.to_dict() 里，
    # 但没有任何 UI 写它们，渲染侧 italic/underline/font_weight 更是无人读取
    # （bold 只有 rendering/__init__.py 读，而值永远是默认 False）。真正生效的
    # 加粗/斜体在富文本样式里，所以这四个字段不进批量表，免得点了没反应。
    FieldSpec("has_rich_text", KIND_BOOL, "Has Rich Text", writable=False),
    FieldSpec("line_count", KIND_NUMBER, "Line Count", writable=False, integer=True),
    FieldSpec("region_index", KIND_NUMBER, "Region Index", writable=False, integer=True),
)

FIELDS_BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in FIELDS}

OPS_BY_KIND: dict[str, tuple[str, ...]] = {
    KIND_TEXT: ("contains", "not_contains", "eq", "ne", "regex", "not_regex", "empty", "not_empty"),
    KIND_ENUM: ("eq", "ne"),
    KIND_NUMBER: ("eq", "ne", "gt", "gte", "lt", "lte", "between"),
    KIND_COLOR: ("color_eq", "color_near"),
    KIND_BOOL: ("is_true", "is_false"),
}

#: 这些运算符不需要值，UI 应隐藏值编辑器
VALUELESS_OPS = frozenset({"empty", "not_empty", "is_true", "is_false"})

OP_LABELS: dict[str, str] = {
    "contains": "contains",
    "not_contains": "does not contain",
    "eq": "equals",
    "ne": "not equal to",
    "regex": "matches regex",
    "not_regex": "does not match regex",
    "empty": "is empty",
    "not_empty": "is not empty",
    "gt": "greater than",
    "gte": "at least",
    "lt": "less than",
    "lte": "at most",
    "between": "between",
    "color_eq": "equals color",
    "color_near": "close to color",
    "is_true": "is yes",
    "is_false": "is no",
}

_DIRECTION_ALIASES = {
    "horizontal": "h",
    "vertical": "v",
    "h": "h",
    "v": "v",
    "hr": "hr",
    "vr": "vr",
    "auto": "auto",
}

#: 编辑器保存时把 fg_colors/bg_colors 写成了 font_color/bg_color 十六进制串，
#: 两种形态都要认，否则同一批图只有一半能匹配上。
_COLOR_FALLBACKS = {"fg_colors": "font_color", "bg_colors": "bg_color"}


# ─── 取值 / 归一化 ───


def region_visible_text(region: dict) -> str:
    """region 的译文正文，``\\n`` 口径（富文本优先，其次 ``translation`` 的 BR）。"""
    try:
        return visible_text_from_document(document_from_region(region))
    except (TypeError, ValueError):
        return str(region.get("translation", "") or "")


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_rgb(value: Any) -> Optional[tuple[float, float, float]]:
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) == 6:
            try:
                return tuple(float(int(text[i:i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        channels = [_to_float(channel) for channel in value[:3]]
        if all(channel is not None for channel in channels):
            return tuple(channels)  # type: ignore[return-value]
    return None


def _normalize_direction(value: Any) -> str:
    return _DIRECTION_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def region_field_value(region: dict, key: str, region_index: int = 0) -> Any:
    """按字段表取值，吸收 region 里的历史形态差异。"""
    if key == "translation":
        return region_visible_text(region)
    if key == "has_rich_text":
        return isinstance(region.get("translation_rich"), dict)
    if key == "line_count":
        return region_visible_text(region).count("\n") + 1
    if key == "region_index":
        return region_index
    if key == "direction":
        return _normalize_direction(region.get("direction"))
    if key in _COLOR_FALLBACKS:
        value = region.get(key)
        if value in (None, "", []):
            value = region.get(_COLOR_FALLBACKS[key])
        return value
    return region.get(key)


# ─── 条件求值 ───


def _match_text(value: Any, op: str, expected: Any) -> bool:
    text = "" if value is None else str(value)
    if op == "empty":
        return not text.strip()
    if op == "not_empty":
        return bool(text.strip())
    needle = "" if expected is None else str(expected)
    if op == "contains":
        return needle in text
    if op == "not_contains":
        return needle not in text
    if op == "eq":
        return text == needle
    if op == "ne":
        return text != needle
    if op in ("regex", "not_regex"):
        try:
            hit = re.search(needle, text) is not None
        except re.error:
            return False
        return hit if op == "regex" else not hit
    return False


def _match_enum(value: Any, op: str, expected: Any) -> bool:
    left = str(value or "").strip().lower()
    right = str(expected or "").strip().lower()
    if left in _DIRECTION_ALIASES or right in _DIRECTION_ALIASES:
        left = _DIRECTION_ALIASES.get(left, left)
        right = _DIRECTION_ALIASES.get(right, right)
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    return False


def _match_number(value: Any, op: str, expected: Any) -> bool:
    left = _to_float(value)
    if left is None:
        return False
    if op == "between":
        bounds = expected
        if isinstance(bounds, dict):
            low, high = _to_float(bounds.get("min")), _to_float(bounds.get("max"))
        elif isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
            low, high = _to_float(bounds[0]), _to_float(bounds[1])
        else:
            return False
        if low is None or high is None:
            return False
        if low > high:
            low, high = high, low
        return low <= left <= high
    right = _to_float(expected)
    if right is None:
        return False
    if op == "eq":
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    if op == "ne":
        return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    return False


def _match_color(value: Any, op: str, expected: Any) -> bool:
    left = _to_rgb(value)
    if left is None:
        return False
    if isinstance(expected, dict):
        right = _to_rgb(expected.get("color"))
        tolerance = _to_float(expected.get("tolerance"))
    else:
        right = _to_rgb(expected)
        tolerance = None
    if right is None:
        return False
    distance = math.dist(left, right)
    if op == "color_eq":
        return distance == 0
    if op == "color_near":
        return distance <= (tolerance if tolerance is not None else 30.0)
    return False


def _match_bool(value: Any, op: str) -> bool:
    truthy = bool(value)
    return truthy if op == "is_true" else not truthy


def evaluate_condition(region: dict, condition: dict, region_index: int = 0) -> bool:
    spec = FIELDS_BY_KEY.get(str(condition.get("field", "")))
    if spec is None:
        return False
    op = str(condition.get("op", ""))
    if op not in OPS_BY_KIND.get(spec.kind, ()):
        return False
    value = region_field_value(region, spec.key, region_index)
    expected = condition.get("value")
    if spec.kind == KIND_TEXT:
        return _match_text(value, op, expected)
    if spec.kind == KIND_ENUM:
        return _match_enum(value, op, expected)
    if spec.kind == KIND_NUMBER:
        return _match_number(value, op, expected)
    if spec.kind == KIND_COLOR:
        return _match_color(value, op, expected)
    if spec.kind == KIND_BOOL:
        return _match_bool(value, op)
    return False


def evaluate_conditions(region: dict, match: dict, region_index: int = 0) -> bool:
    """无条件 = 匹配全部 region（与"筛选器留空"的直觉一致）。"""
    conditions = (match or {}).get("conditions") or []
    if not conditions:
        return True
    results = (evaluate_condition(region, condition, region_index) for condition in conditions)
    if str((match or {}).get("logic", "")).lower() == LOGIC_ANY:
        return any(results)
    return all(results)


# ─── 动作 ───


def _compile_pattern(action: dict) -> Optional[re.Pattern]:
    pattern = str(action.get("pattern", "") or "")
    if not pattern:
        return None
    try:
        return re.compile(pattern if action.get("regex") else re.escape(pattern))
    except re.error:
        return None


def _coerce_field_value(spec: Optional[FieldSpec], value: Any) -> Any:
    if spec is None:
        return value
    if spec.kind == KIND_BOOL:
        return bool(value)
    if spec.kind == KIND_NUMBER:
        number = _to_float(value)
        if number is None:
            return value
        return int(round(number)) if spec.integer else number
    if spec.kind == KIND_COLOR:
        rgb = _to_rgb(value)
        return [int(round(channel)) for channel in rgb] if rgb else value
    return str(value)


_COLLAPSE_BREAKS_RE = re.compile(r"\n+")


def _region_direction(region: dict) -> Any:
    return region.get("direction", "h")


def _sync_translation(
    region: dict,
    *,
    ops: Optional[list],
    pre_text: str,
    post_text: str,
    keep_raw: bool = False,
) -> None:
    """写回译文三件套，富文本走编辑器的 ``sync_region_rich_translation``。

    ``ops`` 给出 ``[pos, removed_len, inserted_text]`` 序列（同
    ``manga_translator/utils/text_edit_ops.py`` 的口径）时回放编辑：未改动字符
    原地保住自己的样式与 ruby/tcy 节点归属，只有被替换掉的那几个字失去样式。
    ``ops=None`` 表示整段改写译文，旧富文本与新正文对不上，只能丢。

    两条路都以"产物无样式就删掉 translation_rich"收尾 —— 富文本的渲染优先级
    高于纯文本，留着旧的等于改了个寂寞。
    """
    storage_text = plain_text_to_storage_text(post_text)
    rich = None
    if ops is not None:
        from manga_translator.rendering.rich_text_sync import sync_region_rich_translation

        try:
            rich = sync_region_rich_translation(
                region.get("translation_rich"),
                {"ops": ops, "pre_text": pre_text, "post_text": post_text},
                raw_mode=False,
                new_translation=storage_text,
                direction_value=_region_direction(region),
                old_translation=str(region.get("translation", "") or ""),
            )
        except Exception:
            rich = None

    region["translation"] = storage_text
    if not keep_raw:
        region["translation_raw"] = storage_text
    if rich is not None:
        region["translation_rich"] = rich
    else:
        region.pop("translation_rich", None)


def _apply_set_fields(region: dict, action: dict) -> None:
    translation_value: Optional[str] = None
    raw_written = False
    for key, value in (action.get("fields") or {}).items():
        spec = FIELDS_BY_KEY.get(key)
        if spec is not None and not spec.writable:
            continue
        if key == "translation":
            translation_value = str(value)
            continue  # 延后统一走同步管线
        if key == "translation_raw":
            region[key] = plain_text_to_storage_text(storage_text_to_editor_text(str(value)))
            raw_written = True
            continue
        region[key] = _coerce_field_value(spec, value)

    if translation_value is not None:
        # 用户可能直接敲 [BR]/<br>，先归一到 \n 口径再走管线
        post_text = storage_text_to_editor_text(translation_value)
        _sync_translation(
            region,
            ops=None,
            pre_text="",
            post_text=post_text,
            keep_raw=raw_written,
        )


def _store_document(region: dict, document: dict) -> None:
    """无样式的文档不值得占一个字段 —— 删掉而不是留个空壳。"""
    try:
        has_styling = document_has_styling(ensure_rich_text_document(document))
    except (TypeError, ValueError):
        has_styling = False
    if has_styling:
        region["translation_rich"] = document
    else:
        region.pop("translation_rich", None)


def _expand_replacement(match: re.Match, action: dict) -> str:
    replacement = str(action.get("replace", "") or "")
    if not action.get("regex"):
        return replacement
    try:
        return match.expand(replacement)
    except (re.error, IndexError):
        # 用户把 \d 之类写进了替换串：按字面量处理，别让整批任务崩在这
        return replacement


def _collapsed_index_map(raw_text: str) -> list[int]:
    """压缩换行后的下标 → 原文档下标。

    ops 跑在"连续换行压成一个"的坐标系上，读原文档的样式却要用原下标，
    两边差多少取决于文中有几处连续换行，只能逐字符记一份对照。
    """
    mapping: list[int] = []
    previous_is_break = False
    for index, char in enumerate(raw_text):
        if char == "\n" and previous_is_break:
            continue
        mapping.append(index)
        previous_is_break = char == "\n"
    return mapping


def _style_at(document: dict, index: int) -> tuple[dict, Optional[str], str]:
    """取正文某个位置上的样式与 ruby/tcy 归属；无样式返回空。"""
    for segment in styled_segments_for_range(document, index, index + 1, expand_empty=False):
        if segment.start <= index < segment.end:
            return segment.style or {}, segment.node_type, segment.ruby_text
    return {}, None, ""


def _restore_replaced_styles(region: dict, carried: Sequence[tuple]) -> None:
    """把被替换掉那段文字的样式接到替换出来的新字上。

    ops 回放只在"插入点前后邻居样式相同"时才让新字继承样式，而替换出的新字
    多半落在样式边界上继承不到 —— 结果就是加了样式的词一被替换样式就没了。
    这里按用户拍板的口径补一刀：整段取命中区间首字的样式（区间内本来有多种
    样式时只能取一种）。
    """
    if not carried:
        return
    document = document_from_region(region)
    text_length = len(visible_text_from_document(document))
    changed = False
    for start, end, style, node_type, ruby_text in carried:
        if start >= end or end > text_length:
            continue
        if style:
            document = apply_style_to_range(document, start, end, style)
            changed = True
        if node_type == "ruby" and ruby_text:
            document = apply_ruby_to_range(document, start, end, ruby_text)
            changed = True
        elif node_type == "tcy":
            document = apply_tcy_to_range(document, start, end)
            changed = True
    if changed:
        _store_document(region, document)


def _apply_replace_text(region: dict, action: dict) -> None:
    pattern = _compile_pattern(action)
    if pattern is None:
        return
    document = document_from_region(region)
    raw_text = visible_text_from_document(document)
    # ops 的坐标口径 = 文档正文且连续换行压成一个（同 _collapse_linebreak_entries）
    pre_text = _COLLAPSE_BREAKS_RE.sub("\n", raw_text)
    matches = [item for item in pattern.finditer(pre_text) if item.start() != item.end()]
    if not matches:
        return

    raw_index = _collapsed_index_map(raw_text)

    # 命中按升序生成，每条 op 的位置落在"前面的 op 都已回放"的坐标系里
    ops: list[list] = []
    carried: list[tuple] = []
    post_text = pre_text
    shift = 0
    for item in matches:
        start, end = item.span()
        replacement = _expand_replacement(item, action)
        ops.append([start + shift, end - start, replacement])
        post_text = post_text[:start + shift] + replacement + post_text[end + shift:]
        if replacement:
            style, node_type, ruby_text = _style_at(document, raw_index[start])
            if style or node_type:
                carried.append((
                    start + shift, start + shift + len(replacement), style, node_type, ruby_text,
                ))
        shift += len(replacement) - (end - start)

    _sync_translation(
        region,
        ops=ops,
        pre_text=pre_text,
        post_text=post_text,
    )
    _restore_replaced_styles(region, carried)


def _rich_text_spans(document: dict, action: dict) -> list[tuple[int, int]]:
    """富文本动作在正文里的目标区间。

    pattern 留空 = 整条 region 的全部文字 —— 筛哪些 region 是匹配条件的活儿，
    这里只管在选中的 region 里定位子串。
    """
    text = visible_text_from_document(document)
    if not text:
        return []
    if not str(action.get("pattern", "") or ""):
        return [(0, len(text))]
    pattern = _compile_pattern(action)
    if pattern is None:
        return []
    return [item.span() for item in pattern.finditer(text) if item.start() != item.end()]


def _uniform_style_spans(document: dict, start: int, end: int) -> list[tuple[int, int, dict, bool]]:
    """把 ``[start, end)`` 切成若干样式一致的子区间。

    ``styled_segments_for_range`` 只报带样式的片段，中间的空白段要自己补回来
    —— 添加模式靠这份切分判断每个位置缺哪些项，漏掉空白段等于漏补。
    每项是 ``(起, 止, 该段已有样式, 该段是否已在 ruby/tcy 节点里)``。
    """
    spans: list[tuple[int, int, dict, bool]] = []
    cursor = start
    for segment in styled_segments_for_range(document, start, end, expand_empty=False):
        seg_start, seg_end = max(segment.start, start), min(segment.end, end)
        if seg_start >= seg_end:
            continue
        if cursor < seg_start:
            spans.append((cursor, seg_start, {}, False))
        spans.append((seg_start, seg_end, segment.style or {}, segment.node_type is not None))
        cursor = seg_end
    if cursor < end:
        spans.append((cursor, end, {}, False))
    return spans


def _overwrite_rich_text(document: dict, start: int, end: int, preset: dict) -> dict:
    """覆盖：你编的那几项赢，命中区间上的其他项原样保留。"""
    if preset["style"]:
        document = apply_style_to_range(document, start, end, preset["style"])
    if preset["ruby"]:
        document = apply_ruby_to_range(document, start, end, preset["ruby"])
    elif preset["tcy"]:
        document = apply_tcy_to_range(document, start, end)
    return document


def _fill_rich_text(document: dict, start: int, end: int, preset: dict) -> dict:
    """添加：命中区间已有的同名项赢，只补它没有的。"""
    spans = _uniform_style_spans(document, start, end)
    for span_start, span_end, existing, _ in reversed(spans):
        # 嵌套项（stroke/glow/transform…）按顶层键判断：已经有描边就整个不动
        patch = {key: value for key, value in preset["style"].items() if key not in existing}
        if patch:
            document = apply_style_to_range(document, span_start, span_end, patch)
    # ruby/tcy 是整段一个节点，不能按样式子区间拆着加（会碎成几个同名注音），
    # 所以区间内只要已有任何节点就整段让位
    if not any(has_node for _, _, _, has_node in spans):
        if preset["ruby"]:
            document = apply_ruby_to_range(document, start, end, preset["ruby"])
        elif preset["tcy"]:
            document = apply_tcy_to_range(document, start, end)
    return document


def _clear_style_patch(keys: Sequence[str]) -> dict:
    """勾选的项 → 一份全是 ``None`` 的 patch（``_merge_style`` 里 None 就是删键）。

    键可以是 ``transform.rotation`` 这样的点号路径，展开成嵌套 patch 后
    ``_merge_style`` 会只删这个子字段，删空了再把整个 transform 一起丢掉。
    """
    patch: dict = {}
    for key in keys:
        if key in ("ruby", "tcy"):
            continue
        head, _, tail = key.partition(".")
        if not tail:
            patch[head] = None            # 整块清，盖掉先前收集的子字段
            continue
        if patch.get(head, {}) is None:
            continue                      # 整块已经要清了，子字段不用再管
        patch.setdefault(head, {})[tail] = None
    return patch


def _clear_rich_text(document: dict, start: int, end: int, keys: Sequence[str]) -> dict:
    """清空：删掉勾选的项；一项没勾 = 连样式带 ruby/tcy 全清。"""
    if not keys:
        return clear_styles_from_range(document, start, end)
    patch = _clear_style_patch(keys)
    if patch:
        document = apply_style_to_range(document, start, end, patch)
    if "ruby" in keys:
        document = remove_ruby_from_range(document, start, end)
    if "tcy" in keys:
        document = remove_tcy_from_range(document, start, end)
    return document


def _apply_rich_text(region: dict, action: dict) -> None:
    document = document_from_region(region)
    spans = _rich_text_spans(document, action)
    if not spans:
        return

    mode = str(action.get("mode", "") or RICH_MODE_OVERWRITE)
    if mode == RICH_MODE_CLEAR:
        keys = [str(key) for key in action.get("clear") or []]
        # 这几个原语都不改字符序列，下标稳定；逆序只为与替换动作保持同一习惯
        for start, end in reversed(spans):
            document = _clear_rich_text(document, start, end, keys)
        _store_document(region, document)
        return

    preset = normalize_rich_text_preset({
        "style": action.get("style") or {},
        "ruby": action.get("ruby", ""),
        "tcy": action.get("tcy", False),
    })
    if preset is None:
        return
    apply_span = _fill_rich_text if mode == RICH_MODE_FILL else _overwrite_rich_text
    for start, end in reversed(spans):
        document = apply_span(document, start, end, preset)
    _store_document(region, document)


_ACTION_HANDLERS: dict[str, Callable[[dict, dict], None]] = {
    ACTION_SET_FIELDS: _apply_set_fields,
    ACTION_REPLACE_TEXT: _apply_replace_text,
    ACTION_RICH_TEXT: _apply_rich_text,
}


def region_is_sane(region: Any) -> bool:
    """后端 ``TextBlock(**region)`` 能不能吃下这条 region。

    ``texts`` 空或 ``lines`` 形状不对时后端会整条跳过（并触发保险丝停写），
    这种 region 我们也不碰 —— 改了反而可能把它写成看似合法的坏数据。
    """
    if not isinstance(region, dict):
        return False
    texts = region.get("texts")
    if not isinstance(texts, list) or not texts:
        return False
    lines = region.get("lines")
    if not isinstance(lines, list) or not lines:
        return False
    for line in lines:
        if not isinstance(line, list) or len(line) < 4:
            return False
        for point in line[:4]:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                return False
    return True


def apply_scheme_to_region(region: dict, scheme: dict) -> Optional[dict]:
    """返回改过的 region 副本；没有任何变化时返回 ``None``。"""
    updated = copy.deepcopy(region)
    for action in scheme.get("actions") or []:
        handler = _ACTION_HANDLERS.get(str(action.get("type", "")))
        if handler is not None:
            handler(updated, action)
    return None if updated == region else updated


# ─── 扫描 ───


@dataclass(frozen=True)
class MatchItem:
    json_path: str
    image_key: str
    region_index: int
    image_name: str
    before_text: str
    after_text: str
    summary: str

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.json_path, self.image_key, self.region_index)


@dataclass
class ScanResult:
    matches: list[MatchItem] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    scanned_files: int = 0
    scanned_regions: int = 0
    skipped_regions: int = 0

    @property
    def file_count(self) -> int:
        return len({item.json_path for item in self.matches})


@dataclass
class ApplyReport:
    written_files: list[str] = field(default_factory=list)
    changed_regions: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)


def _summarize(before: dict, after: dict) -> str:
    changes: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key == "translation_rich":
            had, has = "translation_rich" in before, "translation_rich" in after
            if before.get(key) != after.get(key):
                changes.append("rich text" if has else ("rich text removed" if had else "rich text"))
            continue
        if before.get(key) != after.get(key):
            changes.append(key)
    return ", ".join(changes)


def _check_cancelled(cancel_event: Optional[threading.Event]) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise BatchEditCancelled()


def iter_pages(data: Any) -> Iterable[tuple[str, dict]]:
    """遍历顶层的每个图片条目。

    现有写者都只产出一个 key，但跨机器搬迁后 key 是旧机器的绝对路径，读端一律
    靠"取第一个 value"兜底；这里直接遍历全部，比挑一个更稳。
    """
    if not isinstance(data, dict):
        return
    for image_key, page in data.items():
        if isinstance(page, dict) and isinstance(page.get("regions"), list):
            yield str(image_key), page


def detect_indent(raw: str, default: int = 4) -> int:
    """探测原文件缩进并沿用（后端写 4，编辑器写 2），让 diff 最小。"""
    for line in raw.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if not stripped:
            continue
        return len(line) - len(stripped) or default
    return default


def read_json_document(json_path: str) -> tuple[Any, int]:
    with open(json_path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    return json.loads(raw), detect_indent(raw)


def write_json_document(json_path: str, data: Any, indent: int = 4, backup: bool = True) -> Optional[str]:
    """原子写；返回备份路径（未备份时为 ``None``）。

    现有链路完全没有备份机制，而批量修改是一次改几十上百个文件的破坏性操作，
    所以默认自带 ``.bak``。
    """
    backup_path = None
    if backup and os.path.exists(json_path):
        backup_path = json_path + ".bak"
        shutil.copy2(json_path, backup_path)

    directory = os.path.dirname(os.path.abspath(json_path)) or "."
    handle_fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".batch_edit_", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=indent, ensure_ascii=False, cls=CustomJSONEncoder)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, json_path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return backup_path


def scan_file(json_path: str, scheme: dict, result: Optional[ScanResult] = None) -> ScanResult:
    result = result if result is not None else ScanResult()
    try:
        data, _indent = read_json_document(json_path)
    except (OSError, ValueError) as exc:
        result.errors.append((json_path, str(exc)))
        return result

    result.scanned_files += 1
    for image_key, page in iter_pages(data):
        image_name = os.path.basename(image_key) or image_key
        for index, region in enumerate(page.get("regions") or []):
            if not region_is_sane(region):
                result.skipped_regions += 1
                continue
            result.scanned_regions += 1
            if not evaluate_conditions(region, scheme.get("match") or {}, index):
                continue
            updated = apply_scheme_to_region(region, scheme)
            if updated is None:
                continue
            result.matches.append(MatchItem(
                json_path=json_path,
                image_key=image_key,
                region_index=index,
                image_name=image_name,
                before_text=region_visible_text(region),
                after_text=region_visible_text(updated),
                summary=_summarize(region, updated),
            ))
    return result


def scan_matches(
    json_paths: Sequence[str],
    scheme: dict,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ScanResult:
    result = ScanResult()
    total = len(json_paths)
    for position, json_path in enumerate(json_paths, start=1):
        _check_cancelled(cancel_event)
        scan_file(json_path, scheme, result)
        if progress is not None:
            progress(position, total)
    return result


def apply_matches(
    selected: Iterable[tuple[str, str, int]],
    scheme: dict,
    backup: bool = True,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ApplyReport:
    """把方案落到选中的 ``(json_path, image_key, region_index)`` 上。

    执行时重新读盘再跑一遍方案（而不是套用预览缓存的结果），预览与执行之间
    文件被别的进程改过时不会写出基于陈旧数据的结果。
    """
    grouped: dict[str, dict[str, set[int]]] = {}
    for json_path, image_key, region_index in selected:
        grouped.setdefault(json_path, {}).setdefault(image_key, set()).add(int(region_index))

    report = ApplyReport()
    total = len(grouped)
    for position, (json_path, pages) in enumerate(sorted(grouped.items()), start=1):
        _check_cancelled(cancel_event)
        try:
            data, indent = read_json_document(json_path)
        except (OSError, ValueError) as exc:
            report.errors.append((json_path, str(exc)))
            continue

        changed = 0
        for image_key, page in iter_pages(data):
            wanted = pages.get(image_key)
            if not wanted:
                continue
            regions = page.get("regions") or []
            for index in sorted(wanted):
                if index >= len(regions):
                    continue
                region = regions[index]
                if not region_is_sane(region):
                    continue
                if not evaluate_conditions(region, scheme.get("match") or {}, index):
                    continue
                updated = apply_scheme_to_region(region, scheme)
                if updated is None:
                    continue
                regions[index] = updated
                changed += 1

        if not changed:
            continue
        try:
            backup_path = write_json_document(json_path, data, indent=indent, backup=backup)
        except OSError as exc:
            report.errors.append((json_path, str(exc)))
            continue
        report.written_files.append(json_path)
        report.changed_regions += changed
        if backup_path:
            report.backups.append(backup_path)
        if progress is not None:
            progress(position, total)
    return report


# ─── 恢复 ───


def backup_path_for(json_path: str) -> str:
    return json_path + ".bak"


def has_backup(json_path: str) -> bool:
    return os.path.isfile(backup_path_for(json_path))


@dataclass
class RestoreReport:
    restored_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def restore_files(
    json_paths: Sequence[str],
    progress: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> RestoreReport:
    """把每个 json 还原成它旁边的 ``.bak``，还原后 ``.bak`` 就地消耗掉。

    用 ``os.replace`` 而不是"读出来再原子写回"：改的只是目录项，不搬数据，
    既比逐字节拷贝快，本身也已经是原子的 —— 没有折中。
    """
    report = RestoreReport()
    paths = sorted({os.path.abspath(path) for path in json_paths})
    total = len(paths)
    for position, json_path in enumerate(paths, start=1):
        _check_cancelled(cancel_event)
        backup = backup_path_for(json_path)
        if not os.path.isfile(backup):
            report.missing_files.append(json_path)
            continue
        try:
            os.replace(backup, json_path)
        except OSError as exc:
            report.errors.append((json_path, str(exc)))
            continue
        report.restored_files.append(json_path)
        if progress is not None:
            progress(position, total)
    return report
