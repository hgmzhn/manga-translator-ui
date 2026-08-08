import _bootstrap  # noqa: F401

import numpy as np

from editor.rich_text_editing import (
    apply_style_to_range,
    style_for_range,
    style_row_coverage,
    styled_text_for_key,
    text_style_from_control_values,
    text_style_to_control_values,
)
from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import ensure_rich_text_document


def _document(text: str, style: dict) -> dict:
    return {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [{"type": "text", "text": text, "style": style}],
            }
        ],
    }


def _set_test_font() -> None:
    text_render.set_font("Arial-Unicode-Regular.ttf")
    text_render.set_bold(False)


def test_strikethrough_style_round_trips_through_richtext_v1():
    document = ensure_rich_text_document(
        _document("删除", {"strikethrough": True})
    )

    assert document.blocks[0].inlines[0].style.strikethrough is True
    assert document.to_dict()["blocks"][0]["inlines"][0]["style"] == {
        "strikethrough": True
    }


def test_editor_reports_strikethrough_selection_state():
    document = apply_style_to_range(
        _document("甲乙", {}), 0, 1, {"strikethrough": True}
    )

    assert style_for_range(document, 0, 1)["strikethrough"] is True
    assert style_row_coverage(document, 0, 0, "ST") == (True, False)
    assert style_row_coverage(document, 0, 1, "ST") == (True, True)
    assert styled_text_for_key(document, 0, 0, "ST") == "甲"


def test_rule_control_values_preserve_strikethrough():
    values = text_style_to_control_values({"strikethrough": True})

    assert values["strikethrough"] is True
    assert text_style_from_control_values(values, {"strikethrough"}) == {
        "strikethrough": True
    }


def test_strikethrough_reuses_underline_geometry_on_both_axes():
    _set_test_font()
    document = ensure_rich_text_document(
        _document("文字", {"underline": True, "strikethrough": True})
    )

    horizontal = text_render._build_rich_horizontal_layout(
        document, 32, 0.0, (0, 0, 0), False, 1.0
    )[0].runs[0]
    assert horizontal.underline is not None
    assert horizontal.strikethrough is not None
    assert horizontal.strikethrough.thickness == horizontal.underline.thickness
    assert horizontal.strikethrough.main_start == horizontal.underline.main_start
    assert horizontal.strikethrough.main_end == horizontal.underline.main_end
    assert horizontal.strikethrough.cross_center < 0 < horizontal.underline.cross_center

    vertical = text_render._build_rich_vertical_layout(
        document, 32, 0.0, (255, 255, 255), (0, 0, 0), 1.0
    )[0]
    underline = vertical.underline_plans[0]
    strikethrough = vertical.strikethrough_plans[0]
    assert strikethrough.thickness == underline.thickness
    assert (strikethrough.main_start, strikethrough.main_end) == (
        underline.main_start,
        underline.main_end,
    )
    assert strikethrough.cross_center == -vertical.thickness / 2.0
    assert underline.cross_center > 0


def test_strikethrough_renders_for_whitespace_on_both_axes():
    _set_test_font()
    document = ensure_rich_text_document(
        _document("  ", {"strikethrough": True})
    )

    horizontal = text_render.put_text_horizontal(
        32,
        document,
        200,
        100,
        "left",
        False,
        (255, 255, 255),
        (0, 0, 0),
        line_spacing=1.0,
        stroke_width=0.0,
    )
    vertical = text_render.put_text_vertical(
        32,
        document,
        200,
        "left",
        (255, 255, 255),
        (0, 0, 0),
        1.0,
        stroke_width=0.0,
    )

    assert horizontal is not None
    assert vertical is not None
    assert np.any(horizontal[:, :, 3])
    assert np.any(vertical[:, :, 3])


def main() -> int:
    tests = (
        test_strikethrough_style_round_trips_through_richtext_v1,
        test_editor_reports_strikethrough_selection_state,
        test_rule_control_values_preserve_strikethrough,
        test_strikethrough_reuses_underline_geometry_on_both_axes,
        test_strikethrough_renders_for_whitespace_on_both_axes,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
