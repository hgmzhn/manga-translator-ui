"""一次性检查:重构后由 OPEN_TO_CLOSE 派生的字符表必须覆盖旧版手抄字面量。

旧集合内容取自重构前的 chinese_linebreak.py(commit 899c316)。
预期差异只有两类:1) STRUCTURAL_BREAK_CHARS 新增空白;2) 派生表补上旧表漏抄的括号。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.rendering import chinese_linebreak as cl


OLD_STRUCTURAL = set(
    "，、。．｡､,.!?！？；;：:﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖…‥⋯︰⋮︙︴—－–−︱︲～〜〰~≀|"
)
OLD_PHRASE_PUNCT = set(
    "，。！？；：、,.!?;:．｡､﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖"
    "…‥⋯︰⋮︙︴～〜〰—－–−︱︲─│━┃═║~≀|·・﹅‚„"
    "()（）[]［］{}｛｝【】〔〕〖〗〘〙〚〛"
    "「」『』｢｣《》〈〉"
    "⁅⁆⟦⟧⟨⟩⟪⟫⦃⦄⦅⦆⦇⦈⦉⦊⦋⦌⦍⦎⦏⦐⦑⦒⧼⧽"
    "︵︶︷︸︹︺︻︼︽︾︿﹀"
    "﹁﹂﹃﹄﹙﹚﹛﹜﹝﹞﹇﹈"
)
OLD_NO_START = set(
    "，、。．｡､,.!?！？；;：:﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖"
    "…‥⋯︰⋮︙︴—－–−︱︲～〜〰~≀|·・﹅"
    "”’〞〟＂＇»›"
    "》，」』】）﹂﹄︶︸︺︼︾﹀﹚﹜﹞﹈)]｝｣》〉"
    "⁆⟧⟩⟫⦄⦆⦈⦊⦌⦎⦐⦒⧽"
)
OLD_NO_END = set("《「『【（﹁﹃︵︷︹︻︽︿﹙﹛﹝﹇([{｛｢〈⁅⟦⟨⟪⦃⦅⦇⦉⦋⦍⦏⦑⧼")


def main() -> int:
    ok = True
    for name, old, new in [
        ("STRUCTURAL_BREAK_CHARS", OLD_STRUCTURAL, cl.STRUCTURAL_BREAK_CHARS),
        ("PHRASE_PUNCT", OLD_PHRASE_PUNCT, cl.PHRASE_PUNCT),
        ("NO_START_CHARS", OLD_NO_START, cl.NO_START_CHARS),
        ("NO_END_CHARS", OLD_NO_END, cl.NO_END_CHARS),
    ]:
        missing = old - new
        added = new - old
        print(f"{name}: 缺失={ascii(sorted(missing))} 新增={ascii(sorted(added))}")
        if missing:
            ok = False
    print("OK" if ok else "FAIL: 派生表丢了旧表字符")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
