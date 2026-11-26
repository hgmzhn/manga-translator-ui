import _bootstrap  # noqa: F401

import random
import re

import pytest

from manga_translator.rendering import auto_linebreak
from manga_translator.rendering.auto_linebreak import (
    KINSOKU_NO_END_CHARS,
    KINSOKU_NO_START_CHARS,
    apply_linebreak_kinsoku,
)

FONT = 10


def _segments(text):
    return text.split("[BR]")


def _assert_kinsoku_clean(lines):
    for index, line in enumerate(lines):
        if index > 0 and line:
            assert line[0] not in KINSOKU_NO_START_CHARS, f"列首出现禁则标点: {lines!r}"
        if index < len(lines) - 1 and line:
            assert line[-1] not in KINSOKU_NO_END_CHARS, f"列尾出现开口括号: {lines!r}"


@pytest.mark.parametrize(
    "source, expected",
    [
        ("没有立即死亡[BR]︐勉强活了下来", "没有立即死亡︐[BR]勉强活了下来"),
        ("不再是人类了[BR]︐所以也会", "不再是人类了︐[BR]所以也会"),
        ("一带吧[BR]？其实并没有", "一带吧？[BR]其实并没有"),
        ("变成了大百足本身[BR]」︒最初讨论", "变成了大百足本身」︒[BR]最初讨论"),
        ("这是爱好呢︵[BR]笑︶︒", "这是爱好呢[BR]︵笑︶︒"),
        ("正是[BR]︽Shnoid︾︐我想", "正是[BR]︽Shnoid︾︐我想"),
        ("用想象做成「[BR]动画」", "用想象做成[BR]「动画」"),
    ],
)
def test_br_kinsoku_moves_punctuation(source, expected):
    result = apply_linebreak_kinsoku(source)
    assert result == expected
    assert result.replace("[BR]", "") == source.replace("[BR]", "")
    _assert_kinsoku_clean(_segments(result))


def test_br_kinsoku_drops_punctuation_only_segment():
    assert apply_linebreak_kinsoku("就是这么回事[BR]︒[BR]嗯") == "就是这么回事︒[BR]嗯"


def test_br_kinsoku_keeps_other_marker_spellings():
    assert apply_linebreak_kinsoku("活了下来 【BR】 ，然后") == "活了下来， 【BR】 然后"
    assert apply_linebreak_kinsoku("活了下来<br>，然后") == "活了下来，<br>然后"


def test_br_kinsoku_leaves_clean_text_untouched():
    for text in (
        "",
        "没有断句的文本︒",
        "第一列︐[BR]第二列︒",
        "Version[BR].NET is fine",
        "dash[BR]——破折号可以起列",
        "︐开头的标点没有上一列可挂",
    ):
        assert apply_linebreak_kinsoku(text) == text


@pytest.fixture
def fixed_advance(monkeypatch):
    monkeypatch.setattr(auto_linebreak, "_vert_char_advance", lambda font_size, cdpt, letter_spacing=1.0: font_size)
    monkeypatch.setattr(
        auto_linebreak,
        "_vert_char_metrics",
        lambda font_size, cdpt, letter_spacing=1.0: (font_size, font_size),
    )


ENGINES = [
    lambda text, max_height: auto_linebreak._layout_vertical(FONT, text, max_height)[:2],
    lambda text, max_height: auto_linebreak._layout_vertical_metrics(FONT, text, max_height)[:2],
]


@pytest.mark.parametrize("engine", ENGINES)
def test_vertical_wrap_hangs_closing_punctuation(fixed_advance, engine):
    lines, heights = engine("一二三四︐五六", 4 * FONT)
    assert lines == ["一二三四︐", "五六"]
    assert heights == [5 * FONT, 2 * FONT]


@pytest.mark.parametrize("engine", ENGINES)
def test_vertical_wrap_hangs_punctuation_run(fixed_advance, engine):
    lines, _ = engine("一二三四」︒五六", 4 * FONT)
    assert lines == ["一二三四」︒", "五六"]


@pytest.mark.parametrize("engine", ENGINES)
def test_vertical_wrap_pushes_opening_bracket(fixed_advance, engine):
    lines, heights = engine("一二三「四五", 4 * FONT)
    assert lines == ["一二三", "「四五"]
    assert heights == [3 * FONT, 3 * FONT]


@pytest.mark.parametrize("engine", ENGINES)
def test_vertical_wrap_plain_text_unchanged(fixed_advance, engine):
    lines, heights = engine("一二三四五六七八九", 4 * FONT)
    assert lines == ["一二三四", "五六七八", "九"]
    assert heights == [4 * FONT, 4 * FONT, FONT]


@pytest.mark.parametrize("engine", ENGINES)
def test_vertical_wrap_long_sentence_is_kinsoku_clean(fixed_advance, engine):
    text = "这个男人被推下去后没有立即死亡︐勉强活了下来︒即便如此︐他还是与「大百足」相遇︐经历种种后获得了力量︒"
    for rows in range(3, 12):
        lines, _ = engine(text, rows * FONT)
        assert "".join(lines) == text
        _assert_kinsoku_clean(lines)


def test_vertical_metrics_widths_follow_split(monkeypatch):
    widths = {"「": 14}
    monkeypatch.setattr(
        auto_linebreak,
        "_vert_char_metrics",
        lambda font_size, cdpt, letter_spacing=1.0: (font_size, widths.get(cdpt, font_size)),
    )
    lines, _, line_widths = auto_linebreak._layout_vertical_metrics(FONT, "一二三「四五", 4 * FONT)
    assert lines == ["一二三", "「四五"]
    assert line_widths == [FONT, 14]


@pytest.mark.parametrize(
    "source, expected",
    [
        ("正文[BR][BR]下一段", "正文[BR][BR]下一段"),
        ("[BR]正文[BR]", "[BR]正文[BR]"),
        ("[BR][BR]", "[BR][BR]"),
        ("正文「[BR]「[BR]后文", "正文[BR]「「后文"),
        ("正文「[BR]」[BR]后文", "正文「」[BR]后文"),
        ("正文[BR][BR]︐下一段", "正文[BR][BR]︐下一段"),
    ],
)
def test_explicit_breaks_keep_blank_columns_and_normalize_once(source, expected):
    result = apply_linebreak_kinsoku(source)
    assert result == expected
    assert apply_linebreak_kinsoku(result) == result
    assert result.replace("[BR]", "") == source.replace("[BR]", "")


def test_seeded_explicit_breaks_preserve_content_and_are_idempotent():
    source = "甲「乙」︐丙︵丁︶︒戊︽己︾？" * 5
    markers = ["[BR]", "【BR】", "<br>"]
    for seed in range(1000):
        rng = random.Random(seed)
        pieces = []
        for char in source:
            pieces.append(char)
            if rng.random() < 0.4:
                pieces.append(rng.choice(markers))
        text = "".join(pieces)
        result = apply_linebreak_kinsoku(text)
        assert re.sub(r"\[BR\]|【BR】|<br>", "", result) == source, f"seed={seed}"
        assert apply_linebreak_kinsoku(result) == result, f"seed={seed}"
        _assert_kinsoku_clean(re.split(r"\[BR\]|【BR】|<br>", result))


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("rows", [0, 1, 2])
def test_narrow_vertical_columns_keep_nested_openers_with_text(fixed_advance, engine, rows):
    text = "「︵甲︶」乙︐丙"
    lines, heights = engine(text, rows * FONT)
    assert "".join(lines) == text
    assert all(lines)
    assert heights == [len(line) * FONT for line in lines]
    _assert_kinsoku_clean(lines)


def test_seeded_variable_metrics_match_both_vertical_engines(monkeypatch):
    alphabet = "甲乙丙丁「」︵︶︐︒？"
    advances = {char: 4 + index % 7 for index, char in enumerate(alphabet)}
    widths = {char: 8 + index % 9 for index, char in enumerate(alphabet)}
    monkeypatch.setattr(
        auto_linebreak, "_vert_char_advance",
        lambda font_size, cdpt, letter_spacing=1.0: advances[cdpt],
    )
    monkeypatch.setattr(
        auto_linebreak, "_vert_char_metrics",
        lambda font_size, cdpt, letter_spacing=1.0: (advances[cdpt], widths[cdpt]),
    )
    for seed in range(1000):
        rng = random.Random(seed)
        text = "甲" + "".join(rng.choices(alphabet, k=60)) + "乙"
        height = rng.randint(0, 100)
        lines, heights = auto_linebreak._layout_vertical(FONT, text, height)
        measured_lines, measured_heights, measured_widths = auto_linebreak._layout_vertical_metrics(FONT, text, height)
        assert lines == measured_lines, f"seed={seed}"
        assert heights == measured_heights
        assert "".join(lines) == text
        assert all(lines)
        _assert_kinsoku_clean(lines)
        assert heights == [sum(advances[c] for c in line) for line in lines]
        assert measured_widths == [max(FONT, *(widths[c] for c in line)) for line in lines]


@pytest.mark.parametrize("font_name", ["msyh.ttc", "msgothic.ttc", "Arial-Unicode-Regular.ttf"])
@pytest.mark.parametrize("optimize_breaks", [False, True])
def test_real_font_layout_and_render_preserve_vertical_text(monkeypatch, font_name, optimize_breaks):
    import numpy as np

    from manga_translator.config import Config
    from manga_translator.rendering import render, resize_regions_to_font_size
    from manga_translator.rendering.text_render import _fonts
    from manga_translator.utils import TextBlock

    monkeypatch.setattr(_fonts._thread_state, "value", _fonts.FontState(), raising=False)
    font_family = _fonts.load_font_file(str(_bootstrap.ROOT / "fonts" / font_name))
    source = "没有立即死亡[BR]︐勉强活了下来︒[BR]这是爱好呢︵[BR]笑︶︒"
    region = TextBlock(
        lines=[
            [[160, 40], [260, 40], [260, 400], [160, 400]],
            [[60, 40], [160, 40], [160, 400], [60, 400]],
        ],
        texts=["原文", "原文"],
        translation=source,
        font_size=32,
        direction="v",
        target_lang="CHS",
        fg_color=(0, 0, 0),
        bg_color=(255, 255, 255),
    )
    region.font_family = font_family
    config = Config()
    config.render.layout_mode = "strict"
    config.render.disable_auto_wrap = True
    config.render.semantic_linebreak = True
    config.render.optimize_line_breaks = optimize_breaks
    config.render.remove_linebreak_punctuation = False
    image = np.full((480, 320, 3), 255, dtype=np.uint8)
    dst = resize_regions_to_font_size(image, [region], config, skip_text_replacements=True)[0]
    assert _fonts._state().font_family == font_family
    assert region.translation.replace("[BR]", "") == source.replace("[BR]", "")
    _assert_kinsoku_clean(_segments(region.translation))
    first = render(image.copy(), region, dst, hyphenate=True, line_spacing=1.0, disable_font_border=False, config=config)
    second = render(image.copy(), region, dst, hyphenate=True, line_spacing=1.0, disable_font_border=False, config=config)
    assert first.shape == image.shape
    assert np.any(first != image)
    assert np.array_equal(first, second)


def main() -> int:
    return pytest.main([__file__, "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
