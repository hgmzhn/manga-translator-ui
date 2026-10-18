"""Regression tests for the perspective/blur review findings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))


def test_perspective_predicate_keeps_skewed_parallelogram() -> None:
    from manga_translator.rendering import _is_perspective_quad

    axis_aligned = np.array(
        [[0, 0], [100, 0], [100, 50], [0, 50]], dtype=np.float32
    )
    assert not _is_perspective_quad(axis_aligned)

    rotated_rectangle = np.array(
        [[50, 0], [100, 50], [50, 100], [0, 50]], dtype=np.float32
    )
    assert not _is_perspective_quad(rotated_rectangle)

    # Opposite sides equal and parallel, but the corners are not right angles:
    # this is a valid perspective quad and must keep corner mapping.
    skewed_parallelogram = np.array(
        [[0, 0], [100, 0], [120, 50], [20, 50]], dtype=np.float32
    )
    assert _is_perspective_quad(skewed_parallelogram)

    trapezoid = np.array(
        [[10, 0], [90, 0], [100, 50], [0, 50]], dtype=np.float32
    )
    assert _is_perspective_quad(trapezoid)


def test_blur_angle_normalized_in_text_style() -> None:
    from manga_translator.rendering.rich_text import TextStyle

    assert TextStyle.from_dict({"blurAngle": -90}).blur_angle == 270.0
    assert TextStyle.from_dict({"blurAngle": 360}).blur_angle == 0.0
    assert TextStyle.from_dict({"blurAngle": 30}).blur_angle == 30.0
    assert TextStyle.from_dict({"blurAngle": None}).blur_angle is None


def test_clear_blur_style_removes_direction() -> None:
    from desktop_qt_ui.ui.widgets.rich_text_editor_components import (
        clear_style_patch,
    )

    patch = clear_style_patch("BL")
    assert patch["blur"] is None
    assert patch["blurAngle"] is None


def test_perspective_render_stays_inside_quad_and_clips_to_canvas() -> None:
    from manga_translator.config import Config
    from manga_translator.rendering import render
    from manga_translator.utils import TextBlock

    def _render(quad: np.ndarray, dst_points: np.ndarray):
        image = np.zeros((240, 240, 3), dtype=np.uint8)
        region = TextBlock(
            lines=[[[80, 100], [220, 100], [220, 160], [80, 160]]],
            texts=["原文"],
            translation="测试",
            fg_color=(255, 255, 255),
            bg_color=(0, 0, 0),
            direction="h",
            target_lang="CHS",
        )
        region.font_size = 32
        region.font_family = "Arial Unicode MS"
        region.perspective_quad = quad
        rendered = render(
            image,
            region,
            dst_points,
            hyphenate=True,
            line_spacing=1.0,
            disable_font_border=False,
            config=Config(),
        )
        ys, xs = np.nonzero(rendered.max(axis=2))
        return xs, ys

    quad = np.array(
        [[100, 100], [200, 100], [220, 160], [80, 160]], dtype=np.float32
    )
    xs, ys = _render(quad, quad.reshape(1, 4, 2))
    assert len(xs) > 0
    assert 70 <= int(xs.min()) and int(xs.max()) <= 230
    assert 90 <= int(ys.min()) and int(ys.max()) <= 170

    # A quad crossing the canvas edge must clip to the intersection instead of
    # allocating an offset layer or crashing.
    clipped_quad = np.array(
        [[-20, 100], [100, 100], [120, 160], [-40, 160]], dtype=np.float32
    )
    clipped_dst = np.array(
        [[[0, 100], [100, 100], [120, 160], [0, 160]]], dtype=np.float32
    )
    xs, ys = _render(clipped_quad, clipped_dst)
    assert len(xs) > 0
    assert int(xs.min()) >= 0


def test_directional_blur_is_applied_per_layer() -> None:
    from manga_translator.rendering.rich_text import TextStyle
    from manga_translator.rendering.text_render._compose import (
        _apply_style_paint_effects,
    )

    layer = np.zeros((60, 80, 4), dtype=np.uint8)
    layer[20:40, 20:60, :3] = 255
    layer[20:40, 20:60, 3] = 255
    style = TextStyle.from_dict({"blur": 0.2, "blurAngle": 0})

    result = _apply_style_paint_effects(layer, style, 20, "body")
    assert result is not None
    assert result.shape[0] >= layer.shape[0]

    mid = result.shape[1] // 2
    left_alpha = result[:, :mid, 3].astype(np.float32).sum()
    right_alpha = result[:, mid:, 3].astype(np.float32).sum()
    # Angle 0 fades left -> right; the two halves must not get the same blur.
    assert abs(left_alpha - right_alpha) > 1.0


def test_thin_skewed_parallelogram_keeps_perspective_mapping() -> None:
    from manga_translator.rendering import _is_perspective_quad

    # A 1 px tall parallelogram with a 0.5 px skew: the old absolute dot
    # tolerance treated it as a rectangle and replaced its corners.
    quad = np.array(
        [[0, 0], [100, 0], [100.5, 1], [0.5, 1]], dtype=np.float32
    )
    assert _is_perspective_quad(quad)


def test_blur_values_are_finite_and_bounded() -> None:
    from manga_translator.rendering.rich_text import TextStyle

    assert TextStyle.from_dict({"blur": 1_000_000_000}).blur == 1.0
    assert TextStyle.from_dict({"blur": float("inf")}).blur == 0.0
    assert TextStyle.from_dict({"blur": float("nan")}).blur == 0.0
    assert TextStyle.from_dict({"blurAngle": float("inf")}).blur_angle is None


def test_ruby_style_inheritance_switch() -> None:
    from types import SimpleNamespace

    from manga_translator.rendering.rich_text import (
        TextStyle,
        TextRun,
        ruby_text_style_for,
    )

    base = TextStyle(bold=True, color="#ff0000")
    span = SimpleNamespace(
        style=base,
        ruby=[TextRun(text="x", style=TextStyle())],
    )
    inherited = ruby_text_style_for(span)
    assert inherited.bold and inherited.color == "#ff0000"

    base.ruby_inherit = False
    own = ruby_text_style_for(span)
    assert not own.bold and own.color is None

    parsed = TextStyle.from_dict({"rubyInherit": False})
    assert parsed.ruby_inherit is False
    assert parsed.to_dict()["rubyInherit"] is False
    assert "rubyInherit" not in TextStyle().to_dict()


def test_perspective_quad_scales_with_font_size() -> None:
    from desktop_qt_ui.editor.editor_controller import (
        _sync_white_frame_size_for_font_change,
    )

    quad = [[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]]
    new_data = {
        "font_size": 40,
        "white_frame_quad_local": [point[:] for point in quad],
        "white_frame_rect_local": [-10.0, -10.0, 10.0, 10.0],
        "has_custom_white_frame": True,
    }
    old_data = {
        "font_size": 20,
        "white_frame_quad_local": [point[:] for point in quad],
        "white_frame_rect_local": [-10.0, -10.0, 10.0, 10.0],
        "has_custom_white_frame": True,
    }

    _sync_white_frame_size_for_font_change(new_data, old_data, None, None)

    assert new_data["white_frame_quad_local"] == [
        [-20.0, -20.0],
        [20.0, -20.0],
        [20.0, 20.0],
        [-20.0, 20.0],
    ]
    assert new_data["white_frame_rect_local"] == [-20.0, -20.0, 20.0, 20.0]

    # A perspective render box without a custom white-frame quad also scales.
    render_data = {
        "font_size": 40,
        "render_box_quad_local": [point[:] for point in quad],
        "render_box_rect_local": [-10.0, -10.0, 10.0, 10.0],
    }
    render_old = {
        "font_size": 20,
        "render_box_quad_local": [point[:] for point in quad],
        "render_box_rect_local": [-10.0, -10.0, 10.0, 10.0],
    }
    _sync_white_frame_size_for_font_change(render_data, render_old, None, None)
    assert render_data["render_box_quad_local"] == [
        [-20.0, -20.0],
        [20.0, -20.0],
        [20.0, 20.0],
        [-20.0, 20.0],
    ]
    assert render_data["render_box_rect_local"] == [-20.0, -20.0, 20.0, 20.0]
