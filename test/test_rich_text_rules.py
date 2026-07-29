from manga_translator.rendering.rich_text_rules import _parse_rules, apply_rich_text_rules


def _rules(*common, horizontal=(), vertical=()):
    return _parse_rules({
        "common": list(common),
        "horizontal": list(horizontal),
        "vertical": list(vertical),
    })


def test_styles_literal_match_without_changing_replaced_text():
    rules = _rules({
        "enabled": True,
        "pattern": "替换后",
        "regex": False,
        "style": {"bold": True, "color": "#ff0000", "verticalAdvance": "half"},
    })
    document = apply_rich_text_rules("这是替换后的文字", 0, rules)

    assert document is not None
    assert document.plain_text() == "这是替换后的文字"
    assert [run.text for run in document.blocks[0].inlines] == ["这是", "替换后", "的文字"]
    assert document.blocks[0].inlines[1].style.bold is True
    assert document.blocks[0].inlines[1].style.color == "#ff0000"
    assert document.blocks[0].inlines[1].style.vertical_advance == "half"


def test_common_then_direction_rule_overrides_only_explicit_fields():
    rules = _rules(
        {"pattern": r"A\d", "regex": True, "style": {"bold": True, "color": "#111111"}},
        horizontal=({"pattern": "A1", "style": {"color": "#222222", "scale": 1.5}},),
    )
    document = apply_rich_text_rules("A1 A2", "horizontal", rules)

    first, separator, second = document.blocks[0].inlines
    assert first.text == "A1"
    assert first.style.bold is True
    # 自动规则之间仍按顺序覆盖；只有已有手工富文本字段受保护。
    assert first.style.color == "#222222"
    assert first.style.scale == 1.5
    assert separator.text == " "
    assert second.style.color == "#111111"


def test_existing_rich_text_is_kept_and_rule_only_adds_missing_fields():
    existing = {
        "format": "richtext.v1",
        "blocks": [{
            "type": "paragraph",
            "inlines": [{"type": "text", "text": "四个字", "style": {"color": "#ff0000"}}],
        }],
    }
    rules = _rules({
        "pattern": "个字",
        "style": {"color": "#0000ff", "bold": True},
    })

    document = apply_rich_text_rules(existing, "horizontal", rules)

    assert document is not None
    runs = document.blocks[0].inlines
    assert [run.text for run in runs] == ["四", "个字"]
    assert runs[0].style.color == "#ff0000"
    assert runs[1].style.color == "#ff0000"
    assert runs[1].style.bold is True


def test_disabled_or_unmatched_rules_return_none():
    rules = _rules({"enabled": False, "pattern": "x", "style": {"bold": True}})
    assert apply_rich_text_rules("x", 0, rules) is None


def test_line_break_markers_become_paragraphs_and_keep_styles():
    rules = _rules({"pattern": "红", "style": {"color": "#ff0000"}})
    document = apply_rich_text_rules("红[BR]普通", 0, rules)

    assert document.plain_text() == "红\n普通"
    assert len(document.blocks) == 2
    assert document.blocks[0].inlines[0].style.color == "#ff0000"


def test_br_marker_itself_is_never_emitted_as_styled_text():
    rules = _rules({"pattern": r"红.*普通", "regex": True, "style": {"bold": True}})
    document = apply_rich_text_rules("红【BR】普通", 0, rules)

    assert document.plain_text() == "红\n普通"
    assert all("BR" not in run.text for block in document.blocks for run in block.inlines)


def test_vertical_rule_can_wrap_match_as_tcy():
    rules = _rules(vertical=({"pattern": "12", "tcy": True, "style": {"bold": True}},))
    document = apply_rich_text_rules("第12话", "vertical", rules)

    assert [inline.type for inline in document.blocks[0].inlines] == ["text", "tcy", "text"]
    assert document.blocks[0].inlines[1].content[0].text == "12"
    assert document.blocks[0].inlines[1].content[0].style.bold is True


def test_tcy_is_ignored_in_horizontal_rendering():
    rules = _rules(horizontal=({"pattern": "12", "tcy": True},))
    document = apply_rich_text_rules("12", "horizontal", rules)

    assert document.blocks[0].inlines[0].type == "text"
