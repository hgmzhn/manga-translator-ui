"""编辑器实时富文本规则回归：新旧匹配对比、手工痕迹跳过、同步管道第三级、IME 收窄。

核心语义（用户拍板）：
- 只应用"编辑前不存在"的新命中（打「你」后补「好」→ 整个「你好」上样式）；
- 未改动文字上的老命中永不重复应用（清掉的样式不会被顶回）；
- 命中区间带本规则给不出的富文本（手工痕迹）→ 整段跳过；
  只有本规则自己的残留样式 → 允许整体补齐；
- 整段替换（无操作记录）按渲染管线全量语义。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

import re  # noqa: E402

from manga_translator.rendering.rich_text_rules import (  # noqa: E402
    _parse_rules,
    apply_rich_text_rules,
)
from manga_translator.rendering.rich_text_sync import (  # noqa: E402
    sync_region_rich_translation,
)


def _rules(*common, horizontal=(), vertical=()):
    return _parse_rules({
        "common": list(common),
        "horizontal": list(horizontal),
        "vertical": list(vertical),
    })


RED_RULE = {"pattern": "你好", "style": {"color": "#ff0000"}}


def _doc(*inlines):
    return {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": list(inlines)}],
    }


def _text_run(text, style=None):
    return {"type": "text", "text": text, "style": style or {}}


def _runs_of(document):
    dumped = document.to_dict() if hasattr(document, "to_dict") else document
    return [
        (inline["text"], inline["style"])
        for inline in dumped["blocks"][0]["inlines"]
        if inline.get("type", "text") == "text"
    ]


# ─── 引擎：previous_text 新旧匹配对比 ───


def test_typed_char_completes_match_applies_whole_range():
    # 先打「你」无命中；补「好」后整个「你好」是新命中，两个字全部上色
    rules = _rules(RED_RULE)
    document = apply_rich_text_rules(
        "你好", 0, rules, previous_text="你", styled_match_policy="skip"
    )
    assert document is not None
    assert _runs_of(document) == [("你好", {"color": "#ff0000"})]


def test_old_match_is_never_reapplied():
    # 「你好」在编辑前就命中过（样式已被用户清掉），在别处打字不顶回
    rules = _rules(RED_RULE)
    plain = _doc(_text_run("你好xy", {}))
    document = apply_rich_text_rules(
        plain, 0, rules, previous_text="你好x", styled_match_policy="skip"
    )
    assert document is None  # 无新命中 → 无变化


def test_deletion_merge_creates_new_match():
    # 「第1x话」删掉 x 拼成「第1话」：旧文本无此命中 → 新命中
    rules = _rules({"pattern": "第1话", "style": {"bold": True}})
    document = apply_rich_text_rules(
        "第1话", 0, rules, previous_text="第1x话", styled_match_policy="skip"
    )
    assert document is not None
    assert _runs_of(document) == [("第1话", {"bold": True})]


def test_lookahead_match_outside_window_counts_as_new():
    # 环视：命中区间 [0,1) 完全落在公共前缀里，但旧文本同位置不命中 → 新命中
    rules = _rules({"pattern": "你(?=好)", "regex": True, "style": {"bold": True}})
    document = apply_rich_text_rules(
        "你好", 0, rules, previous_text="你", styled_match_policy="skip"
    )
    assert document is not None
    assert _runs_of(document) == [("你", {"bold": True}), ("好", {})]


def test_residue_style_backfills_whole_match():
    # 「你」带的是本规则自己的残留样式（删掉好再补回好）→ 整体补齐，好也上色
    rules = _rules(RED_RULE)
    residue = _doc(_text_run("你", {"color": "#ff0000"}), _text_run("好", {}))
    document = apply_rich_text_rules(
        residue, 0, rules, previous_text="你", styled_match_policy="skip"
    )
    assert document is not None
    assert _runs_of(document) == [("你好", {"color": "#ff0000"})]


def test_manual_trace_skips_whole_match():
    # 「你」带规则给不出的颜色（手工痕迹）→ 整段跳过，一个字段都不加
    rules = _rules(RED_RULE)
    manual = _doc(_text_run("你", {"color": "#0000ff"}), _text_run("好", {}))
    document = apply_rich_text_rules(
        manual, 0, rules, previous_text="你", styled_match_policy="skip"
    )
    assert document is None


def test_manual_node_counts_as_trace():
    rules = _rules(RED_RULE)
    ruby = _doc(
        {
            "type": "ruby",
            "base": [_text_run("你", {})],
            "text": [_text_run("ニー", {})],
        },
        _text_run("好", {}),
    )
    document = apply_rich_text_rules(
        ruby, 0, rules, previous_text="你", styled_match_policy="skip"
    )
    assert document is None


def test_full_semantics_when_previous_text_is_none():
    # 整段替换：previous_text=None → 所有命中都算新（等同管线）
    rules = _rules(RED_RULE)
    document = apply_rich_text_rules(
        "你好在这你好", 0, rules, styled_match_policy="skip"
    )
    assert document is not None
    runs = _runs_of(document)
    assert runs[0] == ("你好", {"color": "#ff0000"})
    assert runs[-1] == ("你好", {"color": "#ff0000"})


# ─── 同步管道：sync_region_rich_translation 尾接规则级 ───


def _edit_info(ops, pre, post):
    return {"ops": ops, "pre_text": pre, "post_text": post}


def test_sync_plain_typing_grows_rule_style():
    # 纯文本区域（无旧富文本）打一个「好」拼出「你好」→ 长出富文本
    result = sync_region_rich_translation(
        None,
        _edit_info([[1, 0, "好"]], "你", "你好"),
        raw_mode=False,
        new_translation="你好",
        apply_rules=True,
        old_translation="你",
        rules=_rules(RED_RULE),
    )
    assert result is not None
    assert result["blocks"][0]["inlines"][0] == {
        "type": "text",
        "text": "你好",
        "style": {"color": "#ff0000"},
    }


def test_sync_cleared_match_stays_clear_on_unrelated_edit():
    # 清光样式后（富文本字段已删）在别处打字：老命中不顶回 → 仍是纯文本
    result = sync_region_rich_translation(
        None,
        _edit_info([[3, 0, "y"]], "你好x", "你好xy"),
        raw_mode=False,
        new_translation="你好xy",
        apply_rules=True,
        old_translation="你好x",
        rules=_rules(RED_RULE),
    )
    assert result is None


def test_sync_full_replacement_applies_all_matches():
    # 整段替换（无编辑操作记录）→ 全量语义
    result = sync_region_rich_translation(
        None,
        None,
        raw_mode=False,
        new_translation="你好",
        apply_rules=True,
        rules=_rules(RED_RULE),
    )
    assert result is not None
    assert result["blocks"][0]["inlines"][0]["style"] == {"color": "#ff0000"}


def test_sync_keeps_manual_styles_and_adds_new_match():
    # 有旧富文本：ops 回放保样式，同时新命中上样式，互不干扰
    old_rich = _doc(_text_run("蓝", {"color": "#0000ff"}), _text_run("你", {}))
    result = sync_region_rich_translation(
        old_rich,
        _edit_info([[2, 0, "好"]], "蓝你", "蓝你好"),
        raw_mode=False,
        new_translation="蓝你好",
        apply_rules=True,
        old_translation="蓝你",
        rules=_rules(RED_RULE),
    )
    assert result is not None
    runs = [
        (inline["text"], inline["style"])
        for inline in result["blocks"][0]["inlines"]
    ]
    assert runs == [("蓝", {"color": "#0000ff"}), ("你好", {"color": "#ff0000"})]


def test_sync_raw_edit_rule_hits_replacement_product():
    # 替换前译文框打「...」→ 替换出「…」→ 规则命中替换产物
    replacements = {
        "common": [(re.compile(re.escape("...")), "…")],
        "horizontal": [],
        "vertical": [],
    }
    old_rich = _doc(_text_run("什么", {"bold": True}))
    result = sync_region_rich_translation(
        old_rich,
        _edit_info([[2, 0, "..."]], "什么", "什么..."),
        raw_mode=True,
        new_translation="什么…",
        direction_value="h",
        replacements=replacements,
        apply_rules=True,
        old_translation="什么",
        rules=_rules({"pattern": "…", "style": {"scale": 2.0}}),
    )
    assert result is not None
    runs = [
        (inline["text"], inline["style"])
        for inline in result["blocks"][0]["inlines"]
    ]
    assert runs == [("什么", {"bold": True}), ("…", {"scale": 2.0})]


def test_sync_apply_rules_false_keeps_legacy_behavior():
    # 开关关闭：无旧富文本一律返回 None，与旧版完全一致
    result = sync_region_rich_translation(
        None,
        _edit_info([[1, 0, "好"]], "你", "你好"),
        raw_mode=False,
        new_translation="你好",
        old_translation="你",
        rules=_rules(RED_RULE),
    )
    assert result is None


# ─── 浮动编辑器：IME 全量替换收窄 + 状态机规则级 ───


def test_ime_full_replace_report_preserves_styles():
    from editor.rich_text_editing import apply_qt_text_change

    doc = _doc(_text_run("你好", {"color": "#ff0000"}))
    # IME 提交把「你好」→「你好呀」报成整篇替换（removed=2, added=3）
    updated = apply_qt_text_change(doc, "你好", "你好呀", 0, 2, 3)
    assert _runs_of(updated) == [("你好", {"color": "#ff0000"}), ("呀", {})]


def test_state_applies_rules_on_typing():
    from editor.rich_text_editor_state import RichTextEditorState
    from manga_translator.rendering import rich_text_rules as rules_module

    state = RichTextEditorState()
    state.auto_rules_provider = lambda: True
    state.bind_region(0, {"translation": "你", "direction": "h"})

    fake_rules = _rules(RED_RULE)
    original_loader = rules_module.load_rich_text_rules
    rules_module.load_rich_text_rules = lambda file_path=None: fake_rules
    try:
        state.apply_qt_contents_change("你好", 1, 0, 1)
    finally:
        rules_module.load_rich_text_rules = original_loader

    assert _runs_of(state.document) == [("你好", {"color": "#ff0000"})]
    emitted = state.mark_document_emitted()
    assert emitted is not None
    assert emitted[2] == "你好"


def test_state_provider_off_leaves_document_plain():
    from editor.rich_text_editor_state import RichTextEditorState

    state = RichTextEditorState()
    state.bind_region(0, {"translation": "你", "direction": "h"})
    state.apply_qt_contents_change("你好", 1, 0, 1)
    assert _runs_of(state.document) == [("你好", {})]


def main():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
