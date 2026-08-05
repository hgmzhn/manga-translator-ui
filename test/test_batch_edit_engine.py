"""批量管理引擎回归测试（纯逻辑，无 Qt）。

运行：
    uv run python test/test_batch_edit_engine.py
或：
    uv run python -m pytest test/test_batch_edit_engine.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from services import batch_edit_engine as engine  # noqa: E402
from services import batch_edit_schemes as schemes  # noqa: E402


# ─── 夹具 ───


def make_region(**overrides) -> dict:
    region = {
        "lines": [[[0.0, 0.0], [10.0, 0.0], [10.0, 20.0], [0.0, 20.0]]],
        "center": [5.0, 10.0],
        "texts": ["原文"],
        "text": "原文",
        "translation": "你好[BR]世界",
        "translation_raw": "你好[BR]世界",
        "angle": 0,
        "font_size": 24,
        "fg_colors": [0, 0, 0],
        "bg_colors": [255, 255, 255],
        "direction": "v",
        "alignment": "left",
        "target_lang": "CHS",
        "source_lang": "ja",
        "line_spacing": 1.0,
        "letter_spacing": 1.0,
        "stroke_width": 0.07,
        "prob": 0.99,
        "font_family": "Arial",
    }
    region.update(overrides)
    return region


def make_scheme(conditions=None, actions=None, logic="all") -> dict:
    return schemes.normalize_scheme({
        "name": "t",
        "enabled": True,
        "match": {"logic": logic, "conditions": conditions or []},
        "actions": actions or [],
    })


def write_page(directory: str, name: str, regions, indent: int = 4, extra=None) -> str:
    image_key = os.path.join(directory, f"{name}.png")
    page = {"regions": regions, "mask_raw": "iVBORw0KGgo=", "mask_is_refined": True,
            "original_width": 909, "original_height": 1269}
    page.update(extra or {})
    json_path = os.path.join(directory, f"{name}_translations.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({image_key: page}, handle, indent=indent, ensure_ascii=False)
    return json_path


def read_page(json_path: str) -> dict:
    with open(json_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ─── 条件求值 ───


def test_text_conditions():
    region = make_region(translation="abc[BR]def")
    assert engine.evaluate_condition(region, {"field": "translation", "op": "contains", "value": "abc"})
    # 匹配跑在富文本正文上：[BR] 已折成 \n，不该按字面命中
    assert not engine.evaluate_condition(region, {"field": "translation", "op": "contains", "value": "[BR]"})
    assert engine.evaluate_condition(region, {"field": "translation", "op": "regex", "value": r"abc\ndef"})
    assert engine.evaluate_condition(region, {"field": "translation", "op": "not_contains", "value": "zzz"})
    assert engine.evaluate_condition(region, {"field": "translation", "op": "not_empty", "value": None})
    assert engine.evaluate_condition(make_region(translation=""), {"field": "translation", "op": "empty", "value": None})
    # 非法正则不该抛，直接判不匹配
    assert not engine.evaluate_condition(region, {"field": "translation", "op": "regex", "value": "([unclosed"})


def test_enum_condition_normalizes_direction_aliases():
    # 后端写 'v'，编辑器写 'vertical'，两边都要认
    assert engine.evaluate_condition(make_region(direction="v"),
                                     {"field": "direction", "op": "eq", "value": "vertical"})
    assert engine.evaluate_condition(make_region(direction="vertical"),
                                     {"field": "direction", "op": "eq", "value": "v"})
    assert engine.evaluate_condition(make_region(direction="h"),
                                     {"field": "direction", "op": "ne", "value": "v"})


def test_number_conditions():
    region = make_region(font_size=24)
    for op, value, expected in (("eq", 24, True), ("ne", 24, False), ("gt", 20, True),
                                ("gte", 24, True), ("lt", 20, False), ("lte", 24, True)):
        assert engine.evaluate_condition(region, {"field": "font_size", "op": op, "value": value}) is expected, op
    assert engine.evaluate_condition(region, {"field": "font_size", "op": "between", "value": [20, 30]})
    assert engine.evaluate_condition(region, {"field": "font_size", "op": "between", "value": {"min": 30, "max": 20}})
    assert not engine.evaluate_condition(region, {"field": "font_size", "op": "between", "value": [1, 5]})


def test_color_conditions_accept_both_storage_forms():
    assert engine.evaluate_condition(make_region(fg_colors=[255, 0, 0]),
                                     {"field": "fg_colors", "op": "color_eq", "value": "#FF0000"})
    # 编辑器保存的是 font_color 十六进制串，没有 fg_colors
    hex_region = make_region(fg_colors=None, font_color="#FF0000")
    assert engine.evaluate_condition(hex_region, {"field": "fg_colors", "op": "color_eq", "value": [255, 0, 0]})
    assert engine.evaluate_condition(make_region(fg_colors=[250, 5, 5]),
                                     {"field": "fg_colors", "op": "color_near",
                                      "value": {"color": "#FF0000", "tolerance": 20}})
    assert not engine.evaluate_condition(make_region(fg_colors=[0, 255, 0]),
                                         {"field": "fg_colors", "op": "color_near",
                                          "value": {"color": "#FF0000", "tolerance": 20}})


def test_bool_and_derived_conditions():
    assert engine.evaluate_condition(make_region(translation="a[BR]b[BR]c"),
                                     {"field": "line_count", "op": "eq", "value": 3})
    assert engine.evaluate_condition(make_region(), {"field": "has_rich_text", "op": "is_false"})
    styled = make_region()
    styled["translation_rich"] = {"format": "richtext.v1", "blocks": [
        {"type": "paragraph", "inlines": [{"type": "text", "text": "x", "style": {"bold": True}}]}]}
    assert engine.evaluate_condition(styled, {"field": "has_rich_text", "op": "is_true"})

    paragraph_only = make_region(translation="a[BR]b")
    paragraph_only["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {"type": "paragraph", "inlines": [{"type": "text", "text": "a", "style": {}}]},
            {"type": "paragraph", "inlines": [{"type": "text", "text": "b", "style": {}}]},
        ],
    }
    assert engine.evaluate_condition(
        paragraph_only, {"field": "has_rich_text", "op": "is_false"}
    )


def test_dead_region_flags_are_not_exposed():
    """region 级 bold/italic/underline/font_weight 没有任何 UI 会写、渲染也基本不读，
    摆进批量表只会让人点了没反应 —— 真正生效的加粗/斜体在富文本样式里。"""
    for key in ("bold", "italic", "underline", "font_weight"):
        assert key not in engine.FIELDS_BY_KEY, key


def test_logic_all_vs_any_and_empty_conditions():
    region = make_region(font_size=24, direction="v")
    hit = {"field": "direction", "op": "eq", "value": "v"}
    miss = {"field": "font_size", "op": "gt", "value": 999}
    assert engine.evaluate_conditions(region, {"logic": "all", "conditions": [hit, miss]}) is False
    assert engine.evaluate_conditions(region, {"logic": "any", "conditions": [hit, miss]}) is True
    # 条件留空 = 全部命中
    assert engine.evaluate_conditions(region, {"logic": "all", "conditions": []}) is True


def test_unknown_field_or_op_never_matches():
    region = make_region()
    assert not engine.evaluate_condition(region, {"field": "nope", "op": "eq", "value": 1})
    assert not engine.evaluate_condition(region, {"field": "font_size", "op": "contains", "value": "2"})


# ─── 动作 ───


def test_set_fields_coerces_types_and_skips_readonly():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {
        "font_size": "30", "line_spacing": "1.5", "prob": 0.1, "fg_colors": "#FF0000"}}])
    updated = engine.apply_scheme_to_region(make_region(), scheme)
    assert updated["font_size"] == 30 and isinstance(updated["font_size"], int)
    assert updated["line_spacing"] == 1.5
    assert updated["fg_colors"] == [255, 0, 0]
    # prob 是只读派生字段，不该被写
    assert updated["prob"] == 0.99


def test_replace_text_rewrites_storage_form():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "你好", "replace": "再见"}])
    updated = engine.apply_scheme_to_region(make_region(translation="你好[BR]世界"), scheme)
    assert updated["translation"] == "再见[BR]世界"
    assert updated["translation_raw"] == "再见[BR]世界"


def test_replace_text_regex_backreference():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": r"(\d+)话",
                                   "regex": True, "replace": r"第\1話"}])
    updated = engine.apply_scheme_to_region(make_region(translation="12话"), scheme)
    assert updated["translation"] == "第12話"


def test_replace_text_bad_backreference_falls_back_to_literal():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "a", "regex": True, "replace": r"\9"}])
    updated = engine.apply_scheme_to_region(make_region(translation="a"), scheme)
    assert updated is not None and updated["translation"] == r"\9"


def test_replace_text_multiple_hits_keep_offsets_correct():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "x", "replace": "YY"}])
    updated = engine.apply_scheme_to_region(make_region(translation="axbxc"), scheme)
    assert updated["translation"] == "aYYbYYc"


def test_replace_text_preserves_untouched_styles():
    region = make_region(translation="ab")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": "a", "style": {"bold": True}},
        {"type": "text", "text": "b", "style": {}},
    ]}]}
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "b", "replace": "Z"}])
    updated = engine.apply_scheme_to_region(region, scheme)
    assert updated["translation"] == "aZ"
    # 未被改动的 'a' 保住自己的 bold
    inlines = updated["translation_rich"]["blocks"][0]["inlines"]
    assert inlines[0]["text"] == "a" and inlines[0]["style"].get("bold") is True


def test_replace_text_drops_rich_text_when_no_styling_left():
    region = make_region(translation="ab")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": "ab", "style": {}}]}]}
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "a", "replace": "Z"}])
    updated = engine.apply_scheme_to_region(region, scheme)
    assert "translation_rich" not in updated


def test_rich_text_action_styles_only_the_hit():
    scheme = make_scheme(actions=[{"type": "rich_text", "pattern": "世界",
                                   "style": {"color": "#FF0000"}}])
    updated = engine.apply_scheme_to_region(make_region(translation="你好世界"), scheme)
    inlines = updated["translation_rich"]["blocks"][0]["inlines"]
    styled = [run for run in inlines if run.get("style")]
    assert len(styled) == 1
    assert styled[0]["text"] == "世界" and styled[0]["style"]["color"] == "#FF0000"
    # 正文不变
    assert updated["translation"] == "你好世界"


def _styled_region(translation: str, text: str, style: dict) -> dict:
    """整段带同一套样式的 region（正文与 translation 一致）。"""
    region = make_region(translation=translation)
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": text, "style": dict(style)}]}]}
    return region


def _first_style(updated: dict) -> dict:
    return updated["translation_rich"]["blocks"][0]["inlines"][0].get("style") or {}


def test_rich_text_overwrite_wins_on_same_key():
    region = _styled_region("ab", "ab", {"color": "#0000ff"})
    scheme = make_scheme(actions=[{"type": "rich_text", "mode": "overwrite", "pattern": "ab",
                                   "style": {"color": "#ff0000", "bold": True}}])
    style = _first_style(engine.apply_scheme_to_region(region, scheme))
    # 覆盖 = 你编的那几项赢；区间上的其他项（这里没有）原样保留
    assert style["color"] == "#ff0000" and style["bold"] is True


def test_rich_text_fill_yields_to_existing_key():
    region = _styled_region("ab", "ab", {"color": "#0000ff"})
    scheme = make_scheme(actions=[{"type": "rich_text", "mode": "fill", "pattern": "ab",
                                   "style": {"color": "#ff0000", "bold": True}}])
    style = _first_style(engine.apply_scheme_to_region(region, scheme))
    # 添加 = 同名绕过，只补它没有的
    assert style["color"] == "#0000ff" and style["bold"] is True


def test_rich_text_fill_covers_unstyled_gaps():
    """styled_segments_for_range 不报无样式文字，空白段要自己补回来。"""
    region = make_region(translation="abc")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": "a", "style": {"bold": True}},
        {"type": "text", "text": "bc", "style": {}},
    ]}]}
    scheme = make_scheme(actions=[{"type": "rich_text", "mode": "fill", "pattern": "abc",
                                   "style": {"bold": False, "color": "#ff0000"}}])
    inlines = engine.apply_scheme_to_region(region, scheme)["translation_rich"]["blocks"][0]["inlines"]
    styles = {run["text"]: run.get("style") or {} for run in inlines}
    # "a" 已经有 bold，让位；"bc" 没有，补上。两段都拿到缺的 color
    assert styles["a"]["bold"] is True and styles["a"]["color"] == "#ff0000"
    assert styles["bc"].get("bold") in (None, False) and styles["bc"]["color"] == "#ff0000"


def test_rich_text_style_filter_is_anded_with_text_match():
    region = make_region(translation="aba")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": "a", "style": {"bold": True}},
        {"type": "text", "text": "b", "style": {"bold": True}},
        {"type": "text", "text": "a", "style": {"italic": 15}},
    ]}]}
    scheme = make_scheme(actions=[{
        "type": "rich_text", "mode": "overwrite", "pattern": "a",
        "match_style": {"bold": True}, "match_style_logic": "all",
        "style": {"color": "#ff0000"},
    }])
    inlines = engine.apply_scheme_to_region(region, scheme)["translation_rich"]["blocks"][0]["inlines"]
    assert inlines[0]["text"] == "a" and inlines[0]["style"] == {"bold": True, "color": "#ff0000"}
    assert inlines[-1]["text"] == "a" and inlines[-1]["style"] == {"italic": 15.0}


def test_rich_text_match_all_vs_any_and_replace_preserves_br():
    region = make_region(translation="ab[BR]c")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [
        {"type": "paragraph", "inlines": [
            {"type": "text", "text": "a", "style": {"bold": True}},
            {"type": "text", "text": "b", "style": {"color": "#0000ff"}},
        ]},
        {"type": "paragraph", "inlines": [
            {"type": "text", "text": "c", "style": {"bold": True, "color": "#0000ff"}},
        ]},
    ]}
    all_scheme = make_scheme(actions=[{
        "type": "rich_text", "mode": "replace", "pattern": "",
        "match_style": {"bold": True, "color": "#0000FF"}, "match_style_logic": "all",
        "style": {"underline": True},
    }])
    updated = engine.apply_scheme_to_region(region, all_scheme)
    assert updated["translation"] == "ab[BR]c"
    assert len(updated["translation_rich"]["blocks"]) == 2
    styles = {
        run["text"]: run.get("style") or {}
        for block in updated["translation_rich"]["blocks"] for run in block["inlines"]
    }
    assert styles["a"] == {"bold": True}
    assert styles["b"] == {"color": "#0000ff"}
    assert styles["c"] == {"underline": True}

    any_scheme = make_scheme(actions=[{
        "type": "rich_text", "mode": "overwrite", "pattern": "",
        "match_style": {"bold": True, "color": "#0000ff"}, "match_style_logic": "any",
        "style": {"underline": True},
    }])
    spans = engine._rich_text_spans(engine.document_from_region(region), any_scheme["actions"][0])
    assert spans == [(0, 2), (3, 4)]


def test_legacy_rich_text_clear_action_is_dropped():
    scheme = make_scheme(actions=[{
        "type": "rich_text", "mode": "clear", "pattern": "", "clear": [],
    }])
    assert scheme["actions"] == []


def test_rich_text_empty_pattern_targets_whole_region():
    scheme = make_scheme(actions=[{"type": "rich_text", "pattern": "", "style": {"bold": True}}])
    updated = engine.apply_scheme_to_region(make_region(translation="你好世界"), scheme)
    inlines = updated["translation_rich"]["blocks"][0]["inlines"]
    assert len(inlines) == 1 and inlines[0]["text"] == "你好世界"
    assert inlines[0]["style"]["bold"] is True


def test_multiple_rich_text_actions_run_in_authoring_order():
    scheme = make_scheme(actions=[
        {"type": "rich_text", "mode": "replace", "pattern": "", "style": {"underline": True}},
        {"type": "rich_text", "mode": "overwrite", "pattern": "好", "style": {"bold": True}},
    ])
    assert len(scheme["actions"]) == 2
    region = _styled_region("你好", "你好", {"color": "#0000ff"})
    inlines = engine.apply_scheme_to_region(region, scheme)["translation_rich"]["blocks"][0]["inlines"]
    styles = {run["text"]: run.get("style") or {} for run in inlines}
    # 先整套替换再覆盖局部：原来的蓝色没了，下划线保留。
    assert styles["你"] == {"underline": True}
    assert styles["好"] == {"underline": True, "bold": True}


def test_replace_text_carries_style_onto_new_text():
    """加了样式的词被替换后样式不该消失（取命中区间首字的样式）。"""
    region = make_region(translation="他说爱丽丝很强")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": "他说", "style": {}},
        {"type": "text", "text": "爱丽丝", "style": {"bold": True, "color": "#FF0000"}},
        {"type": "text", "text": "很强", "style": {}},
    ]}]}
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "爱丽丝", "replace": "Alice"}])
    updated = engine.apply_scheme_to_region(region, scheme)
    assert updated["translation"] == "他说Alice很强"
    styles = {run["text"]: run.get("style") or {}
              for run in updated["translation_rich"]["blocks"][0]["inlines"]}
    assert styles["Alice"] == {"bold": True, "color": "#FF0000"}
    assert styles["他说"] == {} and styles["很强"] == {}


def test_replace_text_carries_style_across_collapsed_line_breaks():
    """ops 坐标压过换行，读原文档样式却要用原下标 —— 两套下标不能串。"""
    region = make_region(translation="a")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [
        {"type": "paragraph", "inlines": [{"type": "text", "text": "a", "style": {}}]},
        {"type": "paragraph", "inlines": []},
        {"type": "paragraph", "inlines": [{"type": "text", "text": "b", "style": {"bold": True}}]},
    ]}
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "b", "replace": "B"}])
    updated = engine.apply_scheme_to_region(region, scheme)
    styles = {run["text"]: run.get("style") or {}
              for block in updated["translation_rich"]["blocks"] for run in block["inlines"]}
    assert styles["B"] == {"bold": True}


def test_replace_text_without_style_stays_plain():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "你好", "replace": "再见"}])
    updated = engine.apply_scheme_to_region(make_region(translation="你好世界"), scheme)
    assert "translation_rich" not in updated


def test_rich_text_tcy_and_ruby():
    tcy = make_scheme(actions=[{"type": "rich_text", "pattern": "12", "tcy": True}])
    updated = engine.apply_scheme_to_region(make_region(translation="a12b"), tcy)
    assert any(item.get("type") == "tcy" for item in updated["translation_rich"]["blocks"][0]["inlines"])

    ruby = make_scheme(actions=[{"type": "rich_text", "pattern": "漢", "ruby": "かん"}])
    updated = engine.apply_scheme_to_region(make_region(translation="漢字"), ruby)
    nodes = [item for item in updated["translation_rich"]["blocks"][0]["inlines"] if item.get("type") == "ruby"]
    assert nodes and nodes[0]["text"][0]["text"] == "かん"


def test_rich_text_empty_action_is_dropped_at_normalize():
    # style/ruby/tcy 三者全空的富文本动作什么也做不了
    scheme = make_scheme(actions=[{"type": "rich_text", "pattern": "a"}])
    assert scheme["actions"] == []


def test_action_order_is_forced_regardless_of_authoring_order():
    scheme = make_scheme(actions=[
        {"type": "rich_text", "pattern": "再见", "style": {"bold": True}},
        {"type": "replace_text", "pattern": "你好", "replace": "再见"},
        {"type": "set_fields", "fields": {"font_size": 30}},
    ])
    assert [action["type"] for action in scheme["actions"]] == list(schemes.ACTION_ORDER)
    # 先替换后加样式：新文字才拿得到样式
    updated = engine.apply_scheme_to_region(make_region(translation="你好"), scheme)
    assert updated["font_size"] == 30
    assert updated["translation"] == "再见"
    inlines = updated["translation_rich"]["blocks"][0]["inlines"]
    assert inlines[0]["text"] == "再见" and inlines[0]["style"].get("bold") is True


def test_set_fields_translation_drops_stale_rich_text():
    """回归：改 translation 却留着旧 translation_rich，渲染仍走旧文字（"翻译有的会绕过"）。"""
    region = make_region(translation="爱丽丝[BR]你稍微")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [
        {"type": "paragraph", "inlines": [{"type": "text", "text": "爱丽丝", "style": {"bold": True}}]},
        {"type": "paragraph", "inlines": [{"type": "text", "text": "你稍微", "style": {}}]},
    ]}
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"translation": "1"}}])
    updated = engine.apply_scheme_to_region(region, scheme)
    assert updated["translation"] == "1"
    assert updated["translation_raw"] == "1"
    assert "translation_rich" not in updated
    # 预览取的就是渲染口径，必须跟着变
    assert engine.region_visible_text(updated) == "1"


def test_set_fields_translation_normalizes_line_breaks():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"translation": "第一行[BR]第二行"}}])
    updated = engine.apply_scheme_to_region(make_region(), scheme)
    assert updated["translation"] == "第一行[BR]第二行"
    assert engine.region_visible_text(updated) == "第一行\n第二行"


def test_set_fields_keeps_explicit_translation_raw():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {
        "translation": "新译文", "translation_raw": "替换前"}}])
    updated = engine.apply_scheme_to_region(make_region(), scheme)
    assert updated["translation"] == "新译文"
    assert updated["translation_raw"] == "替换前"


def test_replace_text_replays_ops_across_paragraphs():
    """局部替换走 ops 回放：未改动段落的样式与段落结构都要原样保住。"""
    region = make_region(translation="爱丽丝[BR]你稍微[BR]等一下")
    region["translation_rich"] = {"format": "richtext.v1", "blocks": [
        {"type": "paragraph", "inlines": [{"type": "text", "text": "爱丽丝", "style": {"bold": True}}]},
        {"type": "paragraph", "inlines": [{"type": "text", "text": "你稍微", "style": {}}]},
        {"type": "paragraph", "inlines": [{"type": "text", "text": "等一下", "style": {}}]},
    ]}
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "等一下", "replace": "稍等"}])
    updated = engine.apply_scheme_to_region(region, scheme)
    assert updated["translation"] == "爱丽丝[BR]你稍微[BR]稍等"
    blocks = updated["translation_rich"]["blocks"]
    assert len(blocks) == 3
    assert blocks[0]["inlines"][0]["style"].get("bold") is True
    assert blocks[2]["inlines"][0]["text"] == "稍等"


def test_no_match_returns_none():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "zzz", "replace": "x"}])
    assert engine.apply_scheme_to_region(make_region(), scheme) is None


def test_region_is_sane_guards():
    assert engine.region_is_sane(make_region())
    assert not engine.region_is_sane(make_region(texts=[]))
    assert not engine.region_is_sane(make_region(lines=[]))
    assert not engine.region_is_sane(make_region(lines=[[[0, 0], [1, 1]]]))
    assert not engine.region_is_sane("not a dict")


# ─── 扫描与写回 ───


def test_scan_reports_matches_and_skips_insane_regions():
    scheme = make_scheme(conditions=[{"field": "direction", "op": "eq", "value": "v"}],
                         actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [
            make_region(direction="v"),
            make_region(direction="h"),
            make_region(direction="v", texts=[]),
        ])
        result = engine.scan_matches([json_path], scheme)
    assert len(result.matches) == 1
    assert result.matches[0].region_index == 0
    assert result.skipped_regions == 1
    assert "font_size" in result.matches[0].summary
    assert result.file_count == 1


def test_apply_preserves_every_other_key():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [make_region()],
                               extra={"skip_font_scaling": True, "custom_third_party_key": {"a": 1}})
        before = read_page(json_path)
        result = engine.scan_matches([json_path], scheme)
        report = engine.apply_matches([item.key for item in result.matches], scheme, backup=False)
        after = read_page(json_path)

    assert report.changed_regions == 1
    page_before = before[next(iter(before))]
    page_after = after[next(iter(after))]
    for key in ("mask_raw", "mask_is_refined", "original_width", "original_height",
                "skip_font_scaling", "custom_third_party_key"):
        assert page_after[key] == page_before[key], key
    assert page_after["regions"][0]["font_size"] == 30
    # 未涉及的 region 字段逐个不变
    for key, value in page_before["regions"][0].items():
        if key != "font_size":
            assert page_after["regions"][0][key] == value, key


def test_apply_preserves_original_indent():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    for indent in (2, 4):
        with tempfile.TemporaryDirectory() as directory:
            json_path = write_page(directory, "p1", [make_region()], indent=indent)
            result = engine.scan_matches([json_path], scheme)
            engine.apply_matches([item.key for item in result.matches], scheme, backup=False)
            with open(json_path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        assert engine.detect_indent(raw) == indent, indent
        assert "\r\n" not in raw


def test_apply_writes_backup_by_default():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [make_region()])
        result = engine.scan_matches([json_path], scheme)
        report = engine.apply_matches([item.key for item in result.matches], scheme, backup=True)
        assert report.backups == [json_path + ".bak"]
        assert os.path.exists(json_path + ".bak")
        assert read_page(json_path + ".bak")[next(iter(read_page(json_path)))]["regions"][0]["font_size"] == 24


def test_restore_rolls_back_and_consumes_the_backup():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [make_region()])
        result = engine.scan_matches([json_path], scheme)
        engine.apply_matches([item.key for item in result.matches], scheme, backup=True)
        assert engine.has_backup(json_path)

        report = engine.restore_files([json_path])
        assert report.restored_files == [os.path.abspath(json_path)]
        assert report.missing_files == [] and report.errors == []
        page = read_page(json_path)
        assert page[next(iter(page))]["regions"][0]["font_size"] == 24
        # .bak 用掉就没了，不留第二层
        assert not engine.has_backup(json_path)


def test_restore_reports_files_without_a_backup():
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [make_region()])
        report = engine.restore_files([json_path])
        assert report.restored_files == []
        assert report.missing_files == [os.path.abspath(json_path)]
        # 没备份就是不动它，不是报错
        assert report.errors == []


def test_apply_skips_files_with_no_change_and_leaves_no_temp():
    scheme = make_scheme(actions=[{"type": "replace_text", "pattern": "zzz", "replace": "x"}])
    with tempfile.TemporaryDirectory() as directory:
        json_path = write_page(directory, "p1", [make_region()])
        mtime = os.path.getmtime(json_path)
        report = engine.apply_matches([(json_path, os.path.join(directory, "p1.png"), 0)], scheme)
        assert report.written_files == []
        assert os.path.getmtime(json_path) == mtime
        leftovers = [name for name in os.listdir(directory) if name.startswith(".batch_edit_")]
        assert leftovers == []


def test_apply_only_touches_selected_regions():
    scheme = make_scheme(actions=[{"type": "set_fields", "fields": {"font_size": 30}}])
    with tempfile.TemporaryDirectory() as directory:
        image_key = os.path.join(directory, "p1.png")
        json_path = write_page(directory, "p1", [make_region(), make_region()])
        engine.apply_matches([(json_path, image_key, 1)], scheme, backup=False)
        regions = read_page(json_path)[image_key]["regions"]
    assert regions[0]["font_size"] == 24
    assert regions[1]["font_size"] == 30


def test_scan_records_error_for_broken_json():
    with tempfile.TemporaryDirectory() as directory:
        json_path = os.path.join(directory, "bad_translations.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = engine.scan_matches([json_path], make_scheme())
    assert len(result.errors) == 1 and result.matches == []


def test_cancel_event_stops_scan():
    import threading
    cancel = threading.Event()
    cancel.set()
    try:
        engine.scan_matches(["whatever.json"], make_scheme(), cancel_event=cancel)
    except engine.BatchEditCancelled:
        return
    raise AssertionError("expected BatchEditCancelled")


# ─── 方案文件 ───


def test_scheme_roundtrip_and_normalize():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "batch_edit_schemes.yaml")
        original = [schemes.new_scheme("A"), schemes.new_scheme("B")]
        original[0]["actions"] = [{"type": "set_fields", "fields": {"font_size": 30}}]
        schemes.save_schemes(original, path)
        loaded = schemes.load_schemes(path)
    assert [item["name"] for item in loaded] == ["A", "B"]
    assert loaded[0]["actions"][0]["fields"]["font_size"] == 30


def test_scheme_normalize_drops_garbage():
    assert schemes.normalize_scheme({"name": ""}) is None
    assert schemes.normalize_scheme("nope") is None
    cleaned = schemes.normalize_scheme({
        "name": " X ",
        "match": {"logic": "weird", "conditions": [{"field": "", "op": "eq"}, {"field": "bold", "op": "is_true"}]},
        "actions": [{"type": "unknown"}, {"type": "set_fields", "fields": {}}],
    })
    assert cleaned["name"] == "X"
    assert cleaned["match"]["logic"] == "all"
    assert len(cleaned["match"]["conditions"]) == 1
    assert cleaned["actions"] == []


def test_default_scheme_file_is_loadable():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "batch_edit_schemes.yaml")
        schemes.ensure_schemes_exists(path)
        loaded = schemes.load_schemes(path)
    assert len(loaded) == 1 and loaded[0]["enabled"] is False


def main() -> int:
    failures = []
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 汇总所有失败再退出
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for line in failures:
        print("FAIL", line)
    total = sum(1 for name, func in globals().items() if name.startswith("test_") and callable(func))
    print(f"{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
