"""rich_text_sync 回归:编辑操作回放、样式继承策略、raw 链路替换同步。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.rendering.rich_text_sync import (  # noqa: E402
    document_after_edit_ops,
    document_has_styling,
    sync_document_for_raw_edit,
)


def _doc(*inlines):
    return {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": list(inlines)}],
    }


def _text_run(text, style=None):
    return {"type": "text", "text": text, "style": style or {}}


def _runs_of(document):
    return [
        (inline["text"], inline["style"])
        for inline in document.to_dict()["blocks"][0]["inlines"]
        if inline["type"] == "text"
    ]


def test_middle_insert_inherits_style():
    doc = _doc(_text_run("你好", {"color": "#ff0000"}))
    result = document_after_edit_ops(doc, [[1, 0, "呀"]], "你好", "你呀好")
    assert result is not None
    assert result.plain_text() == "你呀好"
    # 同样式段中间插入 → 继承,合并为单个 run
    assert _runs_of(result) == [("你呀好", {"color": "#ff0000"})]


def test_edge_insert_does_not_inherit():
    doc = _doc(_text_run("你好", {"color": "#ff0000"}))

    head = document_after_edit_ops(doc, [[0, 0, "哦"]], "你好", "哦你好")
    assert _runs_of(head) == [("哦", {}), ("你好", {"color": "#ff0000"})]

    tail = document_after_edit_ops(doc, [[2, 0, "呀"]], "你好", "你好呀")
    assert _runs_of(tail) == [("你好", {"color": "#ff0000"}), ("呀", {})]


def test_insert_between_different_styles_does_not_inherit():
    doc = _doc(_text_run("A", {"color": "#f00"}), _text_run("B", {"color": "#00f"}))
    result = document_after_edit_ops(doc, [[1, 0, "x"]], "AB", "AxB")
    assert _runs_of(result) == [
        ("A", {"color": "#f00"}),
        ("x", {}),
        ("B", {"color": "#00f"}),
    ]


def test_delete_keeps_remaining_styles():
    doc = _doc(_text_run("你好", {}), _text_run("真的", {"color": "#ff0000"}))
    result = document_after_edit_ops(doc, [[1, 1, ""]], "你好真的", "你真的")
    assert _runs_of(result) == [("你", {}), ("真的", {"color": "#ff0000"})]


def test_sequential_ops_like_typing():
    doc = _doc(_text_run("你好", {"color": "#ff0000"}))
    result = document_after_edit_ops(doc, [[1, 0, "a"], [2, 0, "b"]], "你好", "你ab好")
    assert result is not None
    assert result.plain_text() == "你ab好"
    assert _runs_of(result) == [("你ab好", {"color": "#ff0000"})]


def test_pre_text_mismatch_returns_none():
    doc = _doc(_text_run("你好", {}))
    assert document_after_edit_ops(doc, [[0, 0, "x"]], "不匹配", "x你好") is None


def test_post_text_mismatch_returns_none():
    doc = _doc(_text_run("你好", {}))
    assert document_after_edit_ops(doc, [[0, 0, "x"]], "你好", "错误结果") is None


def test_op_out_of_range_returns_none():
    doc = _doc(_text_run("你好", {}))
    assert document_after_edit_ops(doc, [[5, 1, "x"]], "你好", "你好") is None


def test_select_all_replace_with_qt_offbyone_removed():
    # Qt contentsChange 在改动涉及末尾时把段落分隔符计入 charsRemoved:
    # 6 字符全选替换报 removed=7,应钳制而不是判为越界
    doc = _doc(_text_run("ABCDEF", {"color": "#f00"}))
    result = document_after_edit_ops(doc, [[0, 7, "XYZ"]], "ABCDEF", "XYZ")
    assert result is not None
    assert result.plain_text() == "XYZ"
    # 全量替换没有可继承的邻居 → 无样式
    assert _runs_of(result) == [("XYZ", {})]


def test_trailing_delete_with_qt_offbyone_removed():
    doc = _doc(_text_run("你好", {}), _text_run("呀", {"color": "#00f"}))
    # 删除末尾 1 字符,Qt 报 removed=2(含段落分隔符)
    result = document_after_edit_ops(doc, [[2, 2, ""]], "你好呀", "你好")
    assert result is not None
    assert _runs_of(result) == [("你好", {})]


_REPLACEMENTS = {
    "common": [(re.compile(re.escape("...")), "…")],
    "horizontal": [],
    "vertical": [],
}


def test_raw_edit_maps_styles_through_replacements():
    # 文档正文是替换后的 "什么…真的吗","真的" 红色;raw 是替换前形式
    doc = _doc(
        _text_run("什么…", {}),
        _text_run("真的", {"color": "#ff0000"}),
        _text_run("吗", {}),
    )
    result = sync_document_for_raw_edit(
        doc, "什么...真的吗", [[8, 0, "呀"]], "什么...真的吗呀", 0, _REPLACEMENTS
    )
    assert result is not None
    assert result.plain_text() == "什么…真的吗呀"
    assert _runs_of(result) == [
        ("什么…", {}),
        ("真的", {"color": "#ff0000"}),
        ("吗呀", {}),
    ]


def test_raw_edit_keeps_style_on_replaced_span():
    # "…" 本身带样式(蓝色):搬回 raw 时整段 "..." 继承,再替换回 "…" 仍是蓝色
    doc = _doc(
        _text_run("什么", {}),
        _text_run("…", {"color": "#00f"}),
        _text_run("真的吗", {}),
    )
    result = sync_document_for_raw_edit(
        doc, "什么...真的吗", [[8, 0, "呀"]], "什么...真的吗呀", 0, _REPLACEMENTS
    )
    assert result is not None
    assert result.plain_text() == "什么…真的吗呀"
    assert _runs_of(result) == [
        ("什么", {}),
        ("…", {"color": "#00f"}),
        ("真的吗呀", {}),
    ]


def test_raw_identity_when_no_replacement_hits():
    doc = _doc(_text_run("你好", {"color": "#ff0000"}))
    result = sync_document_for_raw_edit(
        doc, "你好", [[1, 0, "呀"]], "你呀好", 0, _REPLACEMENTS
    )
    assert _runs_of(result) == [("你呀好", {"color": "#ff0000"})]


def _ruby_doc():
    return _doc(
        _text_run("A", {}),
        {
            "type": "ruby",
            "base": [_text_run("漢字", {})],
            "text": [_text_run("かんじ", {})],
        },
        _text_run("B", {}),
    )


def _inline_types(document):
    return [inline["type"] for inline in document.to_dict()["blocks"][0]["inlines"]]


def test_insert_inside_ruby_base_joins_node():
    result = document_after_edit_ops(_ruby_doc(), [[2, 0, "X"]], "A漢字B", "A漢X字B")
    assert result is not None
    inlines = result.to_dict()["blocks"][0]["inlines"]
    assert _inline_types(result) == ["text", "ruby", "text"]
    ruby = inlines[1]
    assert "".join(run["text"] for run in ruby["base"]) == "漢X字"
    assert "".join(run["text"] for run in ruby["text"]) == "かんじ"


def test_insert_at_ruby_boundary_stays_outside():
    result = document_after_edit_ops(_ruby_doc(), [[1, 0, "Y"]], "A漢字B", "AY漢字B")
    assert result is not None
    inlines = result.to_dict()["blocks"][0]["inlines"]
    assert _inline_types(result) == ["text", "ruby", "text"]
    assert inlines[0]["text"] == "AY"
    assert "".join(run["text"] for run in inlines[1]["base"]) == "漢字"


def test_document_has_styling():
    from manga_translator.rendering.rich_text import ensure_rich_text_document

    plain = ensure_rich_text_document(_doc(_text_run("你好", {})))
    styled = ensure_rich_text_document(_doc(_text_run("你好", {"bold": True})))
    ruby = ensure_rich_text_document(_ruby_doc())
    assert not document_has_styling(plain)
    assert document_has_styling(styled)
    assert document_has_styling(ruby)


def test_linebreak_insert_and_collapse_alignment():
    # 插入换行:新段落边界;文档中已有的连续换行在同步前被压成一个
    doc = _doc(_text_run("你好", {"color": "#ff0000"}))
    result = document_after_edit_ops(doc, [[1, 0, "\n"]], "你好", "你\n好")
    assert result is not None
    assert result.plain_text() == "你\n好"
    blocks = result.to_dict()["blocks"]
    assert len(blocks) == 2
    assert blocks[0]["inlines"][0]["style"] == {"color": "#ff0000"}
    assert blocks[1]["inlines"][0]["style"] == {"color": "#ff0000"}


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
