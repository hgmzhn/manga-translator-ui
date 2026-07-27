"""中文语义断句空格处理回归测试。

纯函数部分不依赖 HanLP 模型;端到端部分在本机模型缺失时打印 SKIP。
直接运行:python test/test_chinese_linebreak_spaces.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.rendering.chinese_linebreak import (
    SemanticUnit,
    _attach_suffix_tokens,
    _inject_space_units,
    _is_structural_break_unit,
    _semantic_units,
    candidate_semantic_break_penalty,
    chinese_linebreak_models_available,
    layout_chinese_cjk,
)


# ---------------------------------------------------------------------------
# 纯函数:空白回填
# ---------------------------------------------------------------------------


def test_inject_space_units_flat_siblings():
    units = (SemanticUnit("HELLO"), SemanticUnit("WORLD"), SemanticUnit("你好"))
    result = _inject_space_units(units, "HELLO WORLD 你好")
    assert result is not None
    assert [unit.text for unit in result] == ["HELLO", " ", "WORLD", " ", "你好"]
    assert "".join(unit.text for unit in result) == "HELLO WORLD 你好"


def test_inject_space_units_nested_lca_placement():
    phrase = SemanticUnit("HELLOWORLD", (SemanticUnit("HELLO"), SemanticUnit("WORLD")))
    result = _inject_space_units((phrase, SemanticUnit("你好")), "HELLO WORLD 你好")
    assert result is not None
    # 短语内部的空格留在短语内,短语与后文之间的空格是顶层兄弟节点
    assert [unit.text for unit in result] == ["HELLO WORLD", " ", "你好"]
    assert [child.text for child in result[0].children] == ["HELLO", " ", "WORLD"]


def test_inject_space_units_leading_trailing_and_runs():
    result = _inject_space_units((SemanticUnit("你好"), SemanticUnit("世界")), " 你好  世界　")
    assert result is not None
    assert [unit.text for unit in result] == [" ", "你好", "  ", "世界", "　"]


def test_inject_space_units_no_space_passthrough():
    units = (SemanticUnit("今天"), SemanticUnit("天气"))
    result = _inject_space_units(units, "今天天气")
    assert result is not None
    assert [unit.text for unit in result] == ["今天", "天气"]


def test_inject_space_units_merged_entity_token():
    # 粗分词可能把 "HELLO WORLD" 合并成一个 token(去空白后为 "HELLOWORLD")
    result = _inject_space_units((SemanticUnit("HELLOWORLD"),), "HELLO WORLD")
    assert result is not None
    assert [unit.text for unit in result] == ["HELLO WORLD"]
    assert [child.text for child in result[0].children] == ["HELLO", " ", "WORLD"]


def test_inject_space_units_mismatch_returns_none():
    assert _inject_space_units((SemanticUnit("你好"),), "你坏") is None
    assert _inject_space_units((SemanticUnit("你好"),), "你好多") is None


def test_space_unit_is_structural_break():
    assert _is_structural_break_unit(SemanticUnit(" "))
    assert _is_structural_break_unit(SemanticUnit("　"))
    assert _is_structural_break_unit(SemanticUnit("，"))
    assert not _is_structural_break_unit(SemanticUnit("你"))
    assert not _is_structural_break_unit(SemanticUnit(""))


def test_suffix_not_attached_across_space():
    units = [SemanticUnit("ABC"), SemanticUnit(" "), SemanticUnit("的")]
    result = _attach_suffix_tokens(units)
    assert [unit.text for unit in result] == ["ABC", " ", "的"]

    units = [SemanticUnit("我"), SemanticUnit("的")]
    result = _attach_suffix_tokens(units)
    assert [unit.text for unit in result] == ["我的"]


# ---------------------------------------------------------------------------
# 纯函数:penalty 对齐(允许断点处丢空白,拒绝内容篡改)
# ---------------------------------------------------------------------------


def test_penalty_allows_space_dropped_at_break():
    assert candidate_semantic_break_penalty("HELLO WORLD", "HELLO[BR]WORLD") < 1000000


def test_penalty_exact_match_no_break():
    assert candidate_semantic_break_penalty("HELLO WORLD", "HELLO WORLD") == 0


def test_penalty_keeps_inline_space():
    assert candidate_semantic_break_penalty("A B C", "A[BR]B C") < 1000000


def test_penalty_rejects_space_dropped_without_break():
    assert candidate_semantic_break_penalty("A B C", "AB[BR]C") == 1000000


def test_penalty_rejects_tampered_content():
    assert candidate_semantic_break_penalty("HELLO WORLD", "HELLO[BR]W0RLD") == 1000000
    assert candidate_semantic_break_penalty("HELLO WORLD", "HELLO[BR]WORL") == 1000000
    assert candidate_semantic_break_penalty("HELLO WORLD", "HELLO[BR]WORLDS") == 1000000


def test_penalty_plain_chinese_regression():
    assert candidate_semantic_break_penalty("今天天气真好", "今天[BR]天气真好") < 1000000
    assert candidate_semantic_break_penalty("今天天气真好", "今日[BR]天气真好") == 1000000


# ---------------------------------------------------------------------------
# 端到端(需要本机 HanLP 模型)
# ---------------------------------------------------------------------------


def _models_ready() -> bool:
    if chinese_linebreak_models_available():
        return True
    print("SKIP: HanLP 模型不可用,跳过端到端用例")
    return False


def test_semantic_units_mixed_text_no_fallback():
    if not _models_ready():
        return
    for text in (
        "HELLO WORLD 这是一个测试",
        "我在 GITHUB 上看到 HELLO WORLD 项目",
        "你好　世界",
    ):
        units = _semantic_units(text)
        assert units is not None, f"含空白文本不应再回退: {text!r}"
        assert "".join(unit.text for unit in units) == text


def test_semantic_units_pure_chinese_regression():
    if not _models_ready():
        return
    text = "今天天气真好，我们出去玩吧。"
    units = _semantic_units(text)
    assert units is not None
    assert "".join(unit.text for unit in units) == text


def test_layout_space_rules():
    if not _models_ready():
        return
    text = "HELLO WORLD 你好世界"
    wide = layout_chinese_cjk(30, text, 10000, horizontal=True)
    assert wide is not None
    wide_lines = wide[0]
    assert wide_lines == [text], f"整体放得下时行内空格应完整保留: {wide_lines!r}"

    narrow = layout_chinese_cjk(30, text, 200, horizontal=True)
    assert narrow is not None
    narrow_lines = narrow[0]
    assert len(narrow_lines) > 1
    for line in narrow_lines:
        assert line == line.strip(), f"断点处空格应删除,行首尾不应有空白: {narrow_lines!r}"
    # 删掉断点空格后其余字符应与原文一致
    rejoined = "".join(narrow_lines)
    assert rejoined.replace(" ", "") == text.replace(" ", "")


def test_layout_pure_chinese_roundtrip():
    if not _models_ready():
        return
    text = "今天天气真好，我们出去玩吧。"
    layout = layout_chinese_cjk(30, text, 300, horizontal=True)
    assert layout is not None
    assert "".join(layout[0]) == text, "无空格文本排版必须无损还原"


def main() -> int:
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("ALL OK" if failures == 0 else f"{failures} FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
