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


def test_boolean_strings_parsed_explicitly():
    assert normalize_paste_overlay(_valid_overlay(visible="false"))["visible"] is False
    assert normalize_paste_overlay(_valid_overlay(visible="true"))["visible"] is True
    try:
        normalize_paste_overlay(_valid_overlay(visible="sometimes"))
    except ValueError:
        return
    raise AssertionError("expected ValueError for unparsable boolean string")


def test_compose_skips_decompression_bomb_png_before_decode():
    import base64 as _base64
    import struct as _struct
    import zlib as _zlib

    # 生成小 PNG，再把 IHDR 宽高改写成超大值（并重算 CRC 保持合法），
    # 验证合成在 imdecode 之前就跳过
    tiny = rgba_overlay_to_png_base64(_solid_rgba(2, 2, (255, 0, 0)))
    raw = bytearray(_base64.b64decode(tiny))
    raw[16:24] = _struct.pack(">II", 200_000, 200_000)
    raw[29:33] = _struct.pack(">I", _zlib.crc32(raw[12:29]) & 0xFFFFFFFF)
    overlay = _valid_overlay(image=_base64.b64encode(bytes(raw)).decode("ascii"))
    assert compose_paste_overlays([overlay], (80, 60)) is None


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


def _solid_overlay(color, center, size=10, z=0, **overrides):
    image = rgba_overlay_to_png_base64(_solid_rgba(size, size, color))
    data = {
        "visible": True,
        "image": image,
        "center_x": center[0],
        "center_y": center[1],
        "width": size,
        "height": size,
        "rotation": 0.0,
        "flip_h": False,
        "flip_v": False,
        "opacity": 1.0,
        "z": z,
    }
    data.update(overrides)
    return _valid_overlay(**data)


def test_compose_respects_persisted_z_order():
    # 列表顺序故意与 z 相反：z=1 的红色应压住 z=0 的绿色
    green_low = _solid_overlay((0, 255, 0), (30, 30), z=0)
    red_high = _solid_overlay((255, 0, 0), (30, 30), z=1)
    composite = compose_paste_overlays([red_high, green_low], (80, 60))
    assert composite is not None
    assert composite[30, 30, 0] > 200  # 顶层为红色
    assert composite[30, 30, 1] < 60


def test_compose_source_over_blends_semi_transparent_overlay():
    red_bottom = _solid_overlay((255, 0, 0), (30, 30), z=0)
    blue_half = _solid_overlay((0, 0, 255), (30, 30), z=1, opacity=0.5)
    composite = compose_paste_overlays([red_bottom, blue_half], (80, 60))
    assert composite is not None
    # source-over：50% 蓝叠不透明红 → 品红，且整体不透明
    assert composite[30, 30, 3] > 250
    assert 100 < composite[30, 30, 0] < 160
    assert composite[30, 30, 1] < 40
    assert 100 < composite[30, 30, 2] < 160


def test_compose_premultiplies_before_affine_interpolation():
    # 源为 2x1：左红不透明、右全透明；横向放大 16 倍会形成半透明过渡像素。
    # 预乘后再插值可避免透明边缘渗黑（straight-alpha 插值会把 RGB 一起拉暗）。
    source = np.zeros((1, 2, 4), dtype=np.uint8)
    source[0, 0] = (255, 0, 0, 255)
    overlay = _valid_overlay(
        image=rgba_overlay_to_png_base64(source),
        center_x=20.0,
        center_y=0.5,
        width=32.0,
        height=1.0,
        rotation=0.0,
        flip_h=False,
        flip_v=False,
        opacity=1.0,
    )
    composite = compose_paste_overlays([overlay], (40, 2))
    assert composite is not None
    partial = np.where((composite[..., 3] > 0) & (composite[..., 3] < 255))
    assert len(partial[0]) > 0, "应存在半透明过渡像素"
    reds = composite[partial[0], partial[1], 0]
    assert np.all(reds > 200), f"半透明边缘反预乘后不应变暗: {reds}"


def test_compose_large_canvas_small_overlay_uses_bounded_box():
    # 大画布 + 小贴片：验证包围盒路径结果正确且不越界崩溃（内存按包围盒裁剪）
    overlay = _overlay_with_image(1024, 1024, width=16, height=16)
    composite = compose_paste_overlays([overlay], (2048, 2048))
    assert composite is not None
    assert composite.shape == (2048, 2048, 4)
    assert composite[1024, 1024, 3] > 250
    assert composite[1024, 1024, 0] > 200


def test_compose_clamps_overlay_partially_outside_canvas():
    # 贴片中心在画布外但部分可见：包围盒被裁剪到画布内，不崩溃且可见像素保留
    overlay = _overlay_with_image(-4, -4, width=16, height=16)
    composite = compose_paste_overlays([overlay], (40, 30))
    assert composite is not None
    assert composite[0, 0, 3] > 250
    assert composite[0, 0, 0] > 200
