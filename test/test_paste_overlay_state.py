import _bootstrap  # noqa: F401, I001

import json

import numpy as np

from editor.paste_overlay_state import (
    PAGE_KEY,
    compose_paste_overlays,
    new_overlay_id,
    normalize_paste_overlay,
    parse_page_paste_overlays,
    png_base64_to_rgba_overlay,
    rgba_overlay_to_png_base64,
    serialize_paste_overlays,
)


def _valid_overlay(**overrides):
    data = {
        "id": "ovl-1",
        "name": "特效字",
        "z": 2,
        "visible": True,
        "opacity": 0.6,
        "center_x": 12.5,
        "center_y": -3.25,
        "width": 64.0,
        "height": 32.0,
        "rotation": 15.0,
        "flip_h": True,
        "flip_v": False,
        "image": "",
    }
    data.update(overrides)
    return data


def test_normalize_fills_defaults_and_coerces():
    raw = {"width": "80", "height": "40", "z": "1", "visible": 1, "opacity": "0.5"}
    overlay = normalize_paste_overlay(raw)
    assert overlay["width"] == 80.0
    assert overlay["height"] == 40.0
    assert overlay["z"] == 1
    assert overlay["visible"] is True
    assert overlay["opacity"] == 0.5
    assert overlay["name"] == "贴片"
    assert overlay["image"] == ""
    assert overlay["center_x"] == 0.0
    assert overlay["center_y"] == 0.0
    assert overlay["rotation"] == 0.0
    assert overlay["flip_h"] is False
    assert overlay["flip_v"] is False
    assert overlay["id"]


def test_opacity_is_clamped_to_unit_range():
    assert normalize_paste_overlay(_valid_overlay(opacity=-1))["opacity"] == 0.0
    assert normalize_paste_overlay(_valid_overlay(opacity=3.5))["opacity"] == 1.0


def test_geometry_must_be_positive():
    for kwargs in ({"width": 0}, {"height": -1}, {"width": -5.0, "height": 3.0}):
        try:
            normalize_paste_overlay(_valid_overlay(**kwargs))
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


def test_invalid_image_payload_raises():
    try:
        normalize_paste_overlay(_valid_overlay(image="not-base64!!"))
    except ValueError:
        return
    raise AssertionError("expected ValueError for invalid base64 image")


def test_serialize_assigns_unique_ids():
    overlays = serialize_paste_overlays(
        [
            _valid_overlay(id="same"),
            _valid_overlay(id="same"),
            _valid_overlay(id=""),
        ]
    )
    ids = [item["id"] for item in overlays]
    assert len(ids) == len(set(ids))
    assert ids[0] == "same"


def test_parse_missing_key_returns_empty():
    assert parse_page_paste_overlays({"regions": []}) == []
    assert parse_page_paste_overlays({"paste_overlays": []}) == []


def test_parse_skips_invalid_entries():
    page = {
        "paste_overlays": [
            _valid_overlay(id="keep-1"),
            {"width": -1, "height": 5},  # 非法几何 → 跳过
            _valid_overlay(id="keep-2"),
        ]
    }
    parsed = parse_page_paste_overlays(page)
    assert [item["id"] for item in parsed] == ["keep-1", "keep-2"]


def test_json_round_trip_keeps_structure():
    overlays = serialize_paste_overlays(
        [_valid_overlay(), _valid_overlay(id="", name="背景补块", z=0)]
    )
    text = json.dumps(
        {"regions": [], PAGE_KEY: overlays}, ensure_ascii=False
    )
    loaded_page = json.loads(text)
    parsed = parse_page_paste_overlays(loaded_page)
    assert parsed == overlays


def test_png_base64_helpers_round_trip():
    rgba = np.zeros((6, 5, 4), dtype=np.uint8)
    rgba[..., 0] = np.arange(6 * 5).reshape(6, 5) % 256
    rgba[..., 1] = 128
    rgba[..., 2] = 64
    rgba[..., 3] = 200
    encoded = rgba_overlay_to_png_base64(rgba)
    assert isinstance(encoded, str) and encoded
    decoded = png_base64_to_rgba_overlay(encoded)
    assert decoded is not None
    assert decoded.shape == rgba.shape
    assert decoded.dtype == np.uint8
    assert np.array_equal(decoded, rgba)
    assert not decoded.flags.writeable
    assert png_base64_to_rgba_overlay("") is None
    assert png_base64_to_rgba_overlay("not-base64!!") is None


def test_ids_survive_round_trip_without_duplicates():
    first = new_overlay_id()
    overlays = serialize_paste_overlays([_valid_overlay(id=first)])
    parsed = parse_page_paste_overlays({"paste_overlays": overlays})
    assert parsed[0]["id"] == first


def _solid_rgba(width, height, color):
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = color
    rgba[..., 3] = 255
    return rgba


def _overlay_with_image(center_x, center_y, width=10, height=10, **overrides):
    image = rgba_overlay_to_png_base64(_solid_rgba(width, height, (255, 0, 0)))
    data = {
        "image": image,
        "center_x": center_x,
        "center_y": center_y,
        "width": width,
        "height": height,
        "rotation": 0.0,
        "flip_h": False,
        "flip_v": False,
        "opacity": 1.0,
    }
    data.update(overrides)
    return _valid_overlay(**data)


def test_compose_paste_overlays_places_image_at_center():
    composite = compose_paste_overlays([_overlay_with_image(20, 30)], (80, 60))
    assert composite is not None
    assert composite.shape == (60, 80, 4)
    # 中心应是不透明红色
    assert composite[30, 20, 0] > 200
    assert composite[30, 20, 1] < 50
    assert composite[30, 20, 3] > 200
    # 远离贴片的位置保持透明
    assert composite[5, 5, 3] == 0


def test_compose_paste_overlays_honors_visibility_and_opacity():
    invisible = _overlay_with_image(20, 30, visible=False)
    assert compose_paste_overlays([invisible], (80, 60)) is None

    faded = _overlay_with_image(20, 30, opacity=0.5)
    composite = compose_paste_overlays([faded], (80, 60))
    assert composite is not None
    assert 60 < composite[30, 20, 3] < 200


def test_compose_paste_overlays_rotation_smoke():
    rotated = _overlay_with_image(40, 30, width=20, height=10, rotation=45)
    composite = compose_paste_overlays([rotated], (80, 60))
    assert composite is not None
    assert np.any(composite[..., 3] > 0)
