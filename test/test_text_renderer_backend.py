import _bootstrap  # noqa: F401
import numpy as np
import pytest
from editor.text_renderer_backend import (
    render_text_for_region,
    render_text_image_for_region,
)
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPixmap, QTransform

from manga_translator.utils import TextBlock


def _region(*, direction="h", translation="编辑器排版", texts=None):
    return TextBlock(
        lines=[[[10, 20], [210, 20], [210, 100], [10, 100]]],
        texts=["原文"] if texts is None else texts,
        translation=translation,
        font_size=32,
        font_family="Arial-Unicode-Regular.ttf",
        direction=direction,
        alignment="center",
        target_lang="CHS",
        adjust_bg_color=False,
        default_stroke_width=0.0,
    )


def _render_params():
    return {
        "font_family": "Arial-Unicode-Regular.ttf",
        "font_color": "#ff0000",
        "text_stroke_color": "#000000",
        "stroke_width": 0.0,
    }


def _horizontal_dst_points():
    return np.asarray(
        [[[10, 20], [210, 20], [210, 100], [10, 100]]], dtype=np.float32
    )


def _vertical_dst_points():
    return np.asarray(
        [[[10, 20], [90, 20], [90, 220], [10, 220]]], dtype=np.float32
    )


def test_editor_text_layout_returns_native_premultiplied_image():
    result = render_text_image_for_region(
        _region(),
        _horizontal_dst_points(),
        None,
        _render_params(),
    )

    assert result is not None
    image, position, native_dst_points = result
    assert image.format() == QImage.Format.Format_RGBA8888_Premultiplied
    assert image.width() > 0
    assert image.height() > 0
    assert image.hasAlphaChannel()

    target_center = np.mean(_horizontal_dst_points()[0], axis=0)
    assert position.x() == pytest.approx(target_center[0] - image.width() / 2.0)
    assert position.y() == pytest.approx(target_center[1] - image.height() / 2.0)

    native_rect = native_dst_points[0]
    assert native_rect[:, 0].max() - native_rect[:, 0].min() == pytest.approx(image.width())
    assert native_rect[:, 1].max() - native_rect[:, 1].min() == pytest.approx(image.height())


def test_editor_text_layout_renders_vertical_region():
    result = render_text_image_for_region(
        _region(direction="v"),
        _vertical_dst_points(),
        None,
        _render_params(),
    )

    assert result is not None
    image, _position, _native_dst_points = result
    assert image.width() > 0
    assert image.height() > 0
    assert image.hasAlphaChannel()


def test_editor_text_layout_applies_qt_transform_before_positioning():
    transform = QTransform()
    transform.translate(30.0, 40.0)
    result = render_text_image_for_region(
        _region(),
        _horizontal_dst_points(),
        transform,
        _render_params(),
    )

    assert result is not None
    image, position, _native_dst_points = result
    transformed_center = transform.map(QPointF(110.0, 60.0))
    assert position.x() == pytest.approx(transformed_center.x() - image.width() / 2.0)
    assert position.y() == pytest.approx(transformed_center.y() - image.height() / 2.0)


def test_editor_text_render_wrapper_returns_qpixmap():
    result = render_text_for_region(
        _region(),
        _horizontal_dst_points(),
        None,
        _render_params(),
    )

    assert result is not None
    pixmap, _position, _native_dst_points = result
    assert isinstance(pixmap, QPixmap)
    assert not pixmap.isNull()
    assert pixmap.width() > 0
    assert pixmap.height() > 0


def test_editor_text_layout_skips_empty_text_and_degenerate_target():
    empty_region = _region(translation="", texts=[""])
    assert (
        render_text_image_for_region(
            empty_region,
            _horizontal_dst_points(),
            None,
            _render_params(),
        )
        is None
    )

    degenerate_points = np.asarray(
        [[[50, 50], [50, 50], [50, 50], [50, 50]]], dtype=np.float32
    )
    assert (
        render_text_image_for_region(
            _region(),
            degenerate_points,
            None,
            _render_params(),
        )
        is None
    )

def test_editor_text_layout_uses_region_color_and_can_disable_border():
    params = _render_params()
    params.pop("font_color")
    params["disable_font_border"] = True

    result = render_text_image_for_region(
        _region(),
        _horizontal_dst_points(),
        None,
        params,
    )

    assert result is not None
    assert result[0].hasAlphaChannel()


def main() -> int:
    test_editor_text_layout_returns_native_premultiplied_image()
    test_editor_text_layout_renders_vertical_region()
    test_editor_text_layout_applies_qt_transform_before_positioning()
    test_editor_text_render_wrapper_returns_qpixmap()
    test_editor_text_layout_skips_empty_text_and_degenerate_target()
    test_editor_text_layout_uses_region_color_and_can_disable_border()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
