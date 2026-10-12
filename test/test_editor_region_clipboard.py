import _bootstrap  # noqa: F401, I001

import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from services.history_service import EditorStateManager
from services.render_parameter_service import RenderParameters, RenderParameterService


def _region(center, left):
    """与画布新建文本框一致：lines 和 polygons 共用同一批顶点。"""
    top = center[1] - 10.0
    bottom = center[1] + 10.0
    points = [
        [left, top],
        [left + 40.0, top],
        [left + 40.0, bottom],
        [left, bottom],
    ]
    return {
        "text": "原文",
        "translation": "译文",
        "center": [float(center[0]), float(center[1])],
        "lines": [points],
        "polygons": [points],
    }


def _model_with_regions(regions):
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.full((400, 600, 3), 3, dtype=np.uint8),
            regions=regions,
        )
    )
    return model


def _controller(model):
    history = EditorStateManager()
    with patch("editor.editor_controller.get_history_service", return_value=history):
        from editor.editor_controller import EditorController

        return EditorController(model)


def test_copy_and_paste_multiple_regions_keeps_relative_offsets():
    model = _model_with_regions(
        [_region((100.0, 100.0), 80.0), _region((300.0, 200.0), 280.0)]
    )
    controller = _controller(model)

    controller.copy_regions([0, 1])
    controller.paste_regions()

    regions = model.get_regions()
    assert len(regions) == 4
    pasted = regions[2:]
    assert [region["center"] for region in pasted] == [[120.0, 120.0], [320.0, 220.0]]
    assert pasted[1]["center"][0] - pasted[0]["center"][0] == 200.0
    assert pasted[1]["center"][1] - pasted[0]["center"][1] == 100.0
    assert pasted[0]["lines"][0][0] == [100.0, 110.0]
    assert model.get_selection() == [2, 3]


def test_multi_region_paste_is_a_single_undo_step():
    model = _model_with_regions(
        [_region((100.0, 100.0), 80.0), _region((300.0, 200.0), 280.0)]
    )
    controller = _controller(model)

    controller.copy_regions([0, 1])
    controller.paste_regions()
    assert len(model.get_regions()) == 4

    controller.undo()
    assert len(model.get_regions()) == 2

    controller.redo()
    assert len(model.get_regions()) == 4


def test_paste_moves_shared_line_and_polygon_points_once():
    model = _model_with_regions([_region((100.0, 100.0), 80.0)])
    source = model.get_regions()[0]
    assert source["lines"][0] is source["polygons"][0]

    controller = _controller(model)
    controller.copy_regions([0])
    controller.paste_regions()

    pasted = model.get_regions()[1]
    assert pasted["center"] == [120.0, 120.0]
    assert pasted["lines"][0][0] == [100.0, 110.0]
    assert pasted["polygons"][0][0] == [100.0, 110.0]


def test_paste_region_style_copies_stroke_without_changing_text_or_geometry():
    source = _region((100.0, 100.0), 80.0)
    source.update(
        font_family="Source Font",
        font_size=24,
        font_color="#123456",
        alignment="right",
        direction="v",
        line_spacing=1.2,
        letter_spacing=1.3,
        bg_colors=[18, 52, 86],
        stroke_width=0.15,
        disable_font_border=True,
    )
    target = _region((300.0, 200.0), 280.0)
    target.update(
        text="目标原文",
        translation="目标译文",
        translation_raw="目标替换前译文",
        angle=30.0,
        white_frame_rect_local=[-30.0, -15.0, 30.0, 15.0],
        has_custom_white_frame=True,
        bg_colors=[255, 255, 255],
        stroke_width=0.03,
        disable_font_border=False,
    )
    model = _model_with_regions([source, target])
    controller = _controller(model)
    before = copy.deepcopy(model.get_regions())
    render_service = RenderParameterService()
    render_service.set_region_parameters(0, RenderParameters())

    with patch(
        "editor.editor_controller.get_render_parameter_service",
        return_value=render_service,
    ):
        controller.copy_region(0)
    controller.paste_region_style(1)

    pasted = model.get_regions()[1]
    for key in (
        "font_family",
        "font_size",
        "font_color",
        "alignment",
        "direction",
        "line_spacing",
        "letter_spacing",
        "bg_colors",
        "stroke_width",
        "disable_font_border",
    ):
        assert pasted[key] == source[key], key
    for key in (
        "text",
        "translation",
        "translation_raw",
        "center",
        "lines",
        "polygons",
        "angle",
        "white_frame_rect_local",
        "has_custom_white_frame",
    ):
        assert pasted[key] == before[1][key], key
    assert model.get_regions()[0] == before[0]
    assert controller.history_service.undo_stack.count() == 1
    after = copy.deepcopy(model.get_regions())

    controller.undo()
    assert model.get_regions() == before
    controller.redo()
    assert model.get_regions() == after


def test_paste_region_style_restores_inherited_stroke_defaults():
    source = _region((100.0, 100.0), 80.0)
    target = _region((300.0, 200.0), 280.0)
    target.update(
        bg_color=[1, 2, 3],
        bg_colors=[4, 5, 6],
        stroke_width=0.25,
        disable_font_border=True,
    )
    model = _model_with_regions([source, target])
    controller = _controller(model)
    defaults = RenderParameters(bg_color=(17, 19, 23), stroke_width=0.08)
    render_service = RenderParameterService()
    render_service.set_region_parameters(0, defaults)
    render_service.set_region_parameters(
        1,
        RenderParameters(
            bg_color=(4, 5, 6), stroke_width=0.25, disable_font_border=True
        ),
    )

    with patch(
        "editor.editor_controller.get_render_parameter_service",
        return_value=render_service,
    ):
        controller.copy_region(0)
    controller.paste_region_style(1)

    pasted = model.get_regions()[1]
    source_params = render_service.get_region_parameters(0, model.get_regions()[0])
    target_params = render_service.get_region_parameters(1, pasted)
    assert target_params.bg_color == source_params.bg_color == (17, 19, 23)
    assert target_params.disable_font_border is source_params.disable_font_border is False
    assert (
        target_params.effective_stroke_width
        == source_params.effective_stroke_width
        == 0.08
    )


def test_paste_region_style_accepts_saved_stroke_color_and_survives_save():
    from services.export_service import ExportService
    from services.file_service import FileService

    source = _region((100.0, 100.0), 80.0)
    source.update(bg_color=[18, 52, 86], stroke_width=0.0, disable_font_border=False)
    target = _region((300.0, 200.0), 280.0)
    target.update(bg_colors=[255, 255, 255], stroke_width=0.02, disable_font_border=True)
    model = _model_with_regions([source, target])
    controller = _controller(model)
    render_service = RenderParameterService()
    render_service.set_region_parameters(0, RenderParameters())
    render_service.set_region_parameters(1, RenderParameters())

    with patch(
        "editor.editor_controller.get_render_parameter_service",
        return_value=render_service,
    ):
        controller.copy_region(0)
    controller.paste_region_style(1)

    pasted_params = render_service.get_region_parameters(1, model.get_regions()[1])
    assert pasted_params.bg_color == (18, 52, 86)
    assert pasted_params.stroke_width == pasted_params.effective_stroke_width == 0.0
    assert pasted_params.disable_font_border is False
    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = str(Path(temp_dir) / "page.png")
        ExportService().save_editor_project(image_path, model.get_regions(), None, None)
        config_service = SimpleNamespace(get_config=lambda: None)
        with patch("services.get_config_service", return_value=config_service):
            reloaded, _, _, _ = FileService().load_translation_json(image_path)
    reloaded_params = render_service.get_region_parameters(1, reloaded[1])
    assert reloaded_params.bg_color == pasted_params.bg_color
    assert reloaded_params.effective_stroke_width == pasted_params.effective_stroke_width
    assert reloaded_params.disable_font_border is False
    assert reloaded[1]["translation"] == target["translation"]


def test_paste_region_style_preserves_target_rich_text_overrides():
    source = _region((100.0, 100.0), 80.0)
    source.update(bg_colors=[18, 52, 86], stroke_width=0.12, disable_font_border=False)
    source["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [{"type": "text", "text": "源译文"}],
            }
        ],
    }
    target = _region((300.0, 200.0), 280.0)
    target.update(translation="漢字", bg_colors=[255, 255, 255], stroke_width=0.02)
    target["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {
                        "type": "ruby",
                        "base": [
                            {
                                "type": "text",
                                "text": "漢字",
                                "style": {
                                    "stroke": {"color": "#abcdef", "width": 0.2}
                                },
                            }
                        ],
                        "text": [{"type": "text", "text": "かんじ"}],
                    }
                ],
            }
        ],
    }
    model = _model_with_regions([source, target])
    controller = _controller(model)
    original_rich = copy.deepcopy(model.get_regions()[1]["translation_rich"])
    render_service = RenderParameterService()
    render_service.set_region_parameters(0, RenderParameters())

    with patch(
        "editor.editor_controller.get_render_parameter_service",
        return_value=render_service,
    ):
        controller.copy_region(0)
    controller.paste_region_style(1)

    pasted = model.get_regions()[1]
    assert pasted["translation"] == "漢字"
    assert pasted["translation_rich"] == original_rich
    assert pasted["bg_colors"] == source["bg_colors"]
    assert pasted["stroke_width"] == source["stroke_width"]


def test_paste_region_style_uses_stroke_snapshot_from_copy_time():
    source = _region((100.0, 100.0), 80.0)
    target = _region((300.0, 200.0), 280.0)
    model = _model_with_regions([source, target])
    controller = _controller(model)
    render_service = RenderParameterService()
    render_service.set_region_parameters(
        0, RenderParameters(bg_color=(18, 52, 86), stroke_width=0.12)
    )

    with patch(
        "editor.editor_controller.get_render_parameter_service",
        return_value=render_service,
    ):
        controller.copy_region(0)
    changed_source = copy.deepcopy(model.get_regions()[0])
    changed_source.update(bg_colors=[255, 0, 0], stroke_width=0.3, disable_font_border=True)
    model.update_region(0, changed_source)
    render_service.set_region_parameters(
        0, RenderParameters(bg_color=(255, 0, 0), stroke_width=0.3, disable_font_border=True)
    )
    controller.paste_region_style(1)

    pasted = model.get_regions()[1]
    assert pasted["bg_colors"] == [18, 52, 86]
    assert pasted["stroke_width"] == 0.12
    assert pasted["disable_font_border"] is False


def main() -> int:
    test_copy_and_paste_multiple_regions_keeps_relative_offsets()
    test_multi_region_paste_is_a_single_undo_step()
    test_paste_moves_shared_line_and_polygon_points_once()
    test_paste_region_style_copies_stroke_without_changing_text_or_geometry()
    test_paste_region_style_restores_inherited_stroke_defaults()
    test_paste_region_style_accepts_saved_stroke_color_and_survives_save()
    test_paste_region_style_preserves_target_rich_text_overrides()
    test_paste_region_style_uses_stroke_snapshot_from_copy_time()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
