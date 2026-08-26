import _bootstrap  # noqa: F401

"""旋转富文本区域的测量与编辑器导出锚点回归。"""

import copy

import pytest
from editor.controller_export_service import EditorControllerExportService
from editor.region_geometry_state import RegionGeometryState
from editor.region_render_snapshot import RegionRenderSnapshot

from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import ensure_rich_text_document
from manga_translator.rendering.text_render._render import _vertical_char_parts
from manga_translator.rendering.text_render._vertical_types import VerticalCharPlan


def _problem_document():
    return {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {"type": "text", "text": "要去了啊啊啊", "style": {}},
                    {
                        "type": "text",
                        "text": "～～～～",
                        "style": {"transform": {"rotation": -90.0}},
                    },
                ],
            }
        ],
    }


def _problem_region():
    return {
        "lines": [
            [
                [1234.16811802008, -31.752058079439053],
                [1381.83186345011, -31.105308069501007],
                [1381.83188197992, 1202.752058079439],
                [1234.16813654989, 1202.105308069501],
            ]
        ],
        "texts": ["いっくうぅ～～～♥"],
        "translation": "要去了啊啊啊～～～～",
        "translation_rich": _problem_document(),
        "angle": -31.6805128525027,
        "font_size": 100,
        "direction": "vertical",
        "white_frame_rect_local": [
            -59.719924571412854,
            -626.848390478557,
            64.28007542858714,
            394.151609521443,
        ],
        "has_custom_white_frame": True,
        "render_box_rect_local": [
            -52.719953443729004,
            -619.8484009770774,
            57.28004655627098,
            387.1515990229225,
        ],
    }


def test_export_without_saved_center_uses_editor_white_frame_anchor():
    region = _problem_region()
    editor_snapshot = RegionRenderSnapshot.from_sources(
        0,
        region,
        RegionGeometryState.from_region_data(region),
    )
    exported = copy.deepcopy(region)

    EditorControllerExportService.apply_white_frame_center(exported)

    assert exported["center"] == pytest.approx(editor_snapshot.render_center)
    for field in ("white_frame_rect_local", "render_box_rect_local"):
        left, top, right, bottom = exported[field]
        assert (left + right) / 2.0 == pytest.approx(0.0, abs=1e-4)
        assert (top + bottom) / 2.0 == pytest.approx(0.0, abs=1e-4)


def test_vertical_minus_90_measurement_matches_rendered_layers():
    text_render.set_font("SimHei")
    document = ensure_rich_text_document(_problem_document())
    layouts = text_render._build_rich_vertical_layout(
        document,
        100,
        0.07,
        (49, 48, 48),
        (255, 255, 255),
        1.0,
    )

    rotated_items = [
        item
        for layout in layouts
        for item in layout.items
        if isinstance(item, VerticalCharPlan)
        and item.span.style.transform.rotation == -90.0
    ]
    assert len(rotated_items) == 4
    for item in rotated_items:
        parts = _vertical_char_parts(item)
        assert parts is not None
        for layer in parts:
            if layer is not None:
                assert layer.shape[:2] == (item.paint_height, item.paint_width)

    metrics = text_render.measure_rich_text_metrics(
        100,
        document,
        False,
        1.0,
        stroke_width=0.07,
    )
    rendered = text_render.put_text_vertical(
        100,
        document,
        metrics["height"],
        "center",
        (49, 48, 48),
        (255, 255, 255),
        1.0,
        stroke_width=0.07,
    )
    assert rendered is not None
    assert (rendered.shape[1], rendered.shape[0]) == (
        metrics["width"],
        metrics["height"],
    )


def main() -> int:
    test_vertical_minus_90_measurement_matches_rendered_layers()
    test_export_without_saved_center_uses_editor_white_frame_anchor()
    print("PASS: rotated rich-text measurement and export anchoring match the editor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
