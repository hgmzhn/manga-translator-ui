"""Regression & contract tests for upstream manga-translator-ui db60bb48 adaptation (schema/store/path/richtext/visual/MCP)."""

import _bootstrap  # noqa: F401, I001

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

import mtui_mcp.project_store as ps
import mtui_mcp.server as server_mod
from mtui_mcp.bubble_centering import (
    _extract_page_exclusive_inks,
    partition_page_white_bubbles,
    run_closed_loop_bubble_centering,
)
from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_detector import save_font_detection_cache_atomic
from mtui_mcp.glossary_manager import batch_edit_chapter_pages, manage_glossary_entries
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    apply_region_updates_to_page,
    encode_mask_base64,
    extract_chapter_script_data,
    get_page_details_dict,
    load_page_json,
    resolve_region_colors_hex,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from mtui_mcp.richtext_builder import (
    apply_project_richtext_rules,
    apply_span_styles_to_region,
    region_max_richtext_blur,
    sync_region_text_update,
    validate_and_normalize_style,
    visible_text_of_region,
)
from mtui_mcp.visual_inspector import (
    generate_page_inspection_image,
    generate_region_loupe_image,
)


def _make_page_fixture(folder: Path, name: str = "001.png") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    img_path = folder / name
    arr = np.full((600, 500, 3), 250, dtype=np.uint8)
    arr[100:104, 140:360] = 10
    arr[416:420, 140:360] = 10
    arr[100:420, 140:144] = 10
    arr[100:420, 356:360] = 10
    Image.fromarray(arr).save(img_path)

    paths = resolve_page_paths(img_path)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(paths.inpainted_path)

    mask = np.zeros((600, 500), dtype=np.uint8)
    mask[150:350, 190:310] = 255
    image_key = str(img_path.resolve())
    image_data: dict[str, Any] = {
        "mask_raw": encode_mask_base64(mask),
        "mask_is_refined": True,
        "regions": [
            {
                "lines": [[[200.0, 150.0], [280.0, 150.0], [280.0, 320.0], [200.0, 320.0]]],
                "center": [240.0, 235.0],
                "texts": ["テスト"],
                "text": "テスト",
                "translation": "测试台词",
                "font_size": 24,
                "font_color": "#000000",
                "bg_color": [255, 255, 255],
                "direction": "v",
                "alignment": "left",
                "angle": 0.0,
            }
        ],
    }
    save_page_json_atomic(paths, image_key, image_data, auto_initial_snapshot=False)
    return img_path


def test_richtext_6_new_upstream_keys_normalization_and_patch_resets(tmp_path: Path) -> None:
    st = validate_and_normalize_style(
        {
            "blur": 1.8,
            "blur_angle": 360.0,
            "blur_radial": True,
            "blur_motion": True,
            "blur_motion_distance": 999.0,
            "ruby_inherit": False,
        }
    )
    assert st == {
        "blur": 1.0,
        "blurAngle": 0.0,
        "blurRadial": True,
        "blurMotion": True,
        "blurMotionDistance": 500.0,
        "rubyInherit": False,
    }

    st_defaults = validate_and_normalize_style(
        {
            "blur": 0.0,
            "blurAngle": None,
            "blurRadial": False,
            "blurMotion": False,
            "blurMotionDistance": 0.0,
            "rubyInherit": True,
        }
    )
    assert st_defaults == {}

    with pytest.raises(ValueError, match="Unknown richtext.v1 style field"):
        validate_and_normalize_style({"blurRadius": 0.5})

    page = _make_page_fixture(tmp_path / "rt_ch", "001.png")
    paths = resolve_page_paths(page)
    ik, data = load_page_json(paths)
    data["regions"][0]["translation"] = "梦幻回想"
    data["regions"][0]["translation_rich"] = {
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
                                "text": "梦幻",
                                "style": {
                                    "blur": 0.4,
                                    "blurAngle": 0.0,
                                    "blurRadial": True,
                                    "rubyInherit": False,
                                    "color": "#3366ff",
                                },
                            }
                        ],
                        "text": [{"type": "text", "text": "ゆめ", "style": {"blur": 0.25}}],
                    },
                    {
                        "type": "text",
                        "text": "回想",
                        "style": {"blurMotion": True, "blurMotionDistance": 40.0},
                    },
                ],
            }
        ],
    }
    save_page_json_atomic(paths, ik, data, auto_initial_snapshot=False)

    details = get_page_details_dict(page)
    assert details["regions"][0]["translation"] == "梦幻回想"
    assert region_max_richtext_blur(details["regions"][0]) == pytest.approx(0.4)
    script = extract_chapter_script_data(tmp_path / "rt_ch")
    assert script["pages"][0]["entries"][0]["translation"] == "梦幻回想"

    _, loaded = load_page_json(resolve_page_paths(page))
    r0 = loaded["regions"][0]
    assert visible_text_of_region(r0) == "梦幻回想"
    sync_region_text_update(r0, "梦幻记忆")
    inls = r0["translation_rich"]["blocks"][0]["inlines"]
    assert inls[0]["type"] == "ruby"
    assert inls[0]["base"][0]["style"]["blurAngle"] == 0.0
    assert inls[0]["base"][0]["style"]["rubyInherit"] is False
    assert inls[0]["text"][0]["style"]["blur"] == 0.25

    apply_span_styles_to_region(
        r0,
        spans=[
            {
                "match": "梦幻",
                "style": {"blur": 0.0, "blurAngle": None, "blurRadial": False, "color": None},
                "ruby_inherit": True,
                "ruby_style": {"blur": 0.0},
            }
        ],
    )
    inls_after = r0["translation_rich"]["blocks"][0]["inlines"]
    assert inls_after[0]["base"][0]["style"] == {}
    assert inls_after[0]["text"][0]["style"] == {}

    rules_file = tmp_path / "rich_text_rules.yaml"
    rules_file.write_text(
        "common:\n  - enabled: true\n    pattern: 记忆\n    regex: false\n    style:\n      blur: 0.5\n      blurAngle: 180\n",
        encoding="utf-8",
    )
    assert apply_project_richtext_rules(r0, rules_yaml_path=rules_file)


def test_perspective_quad_lifecycle_scaling_loupe_and_centering(tmp_path: Path) -> None:
    page = _make_page_fixture(tmp_path / "quad_ch", "001.png")

    with pytest.raises(ValueError, match="white_frame_quad_local"):
        apply_region_updates_to_page(
            page,
            [{"region_index": 0, "white_frame_quad_local": [[-20, -40], [20, -40], [20, 40]]}],
        )

    quad_init = [[-40.0, -80.0], [160.0, -60.0], [170.0, 90.0], [-30.0, 70.0]]
    _, summaries = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "white_frame_quad_local": quad_init}],
    )
    assert summaries[0]["has_perspective_quad"] is True
    assert summaries[0]["has_custom_white_frame"] is True
    assert summaries[0]["white_frame_quad_local"] == quad_init
    assert summaries[0]["white_frame_rect_local"] == [-40.0, -80.0, 170.0, 90.0]

    _, summaries_fs = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "font_size": 48}],
    )
    scaled_quad = summaries_fs[0]["white_frame_quad_local"]
    cent_x = sum(p[0] for p in quad_init) / 4.0
    cent_y = sum(p[1] for p in quad_init) / 4.0
    assert scaled_quad[1][0] == pytest.approx(cent_x + (160.0 - cent_x) * 2.0)
    assert scaled_quad[2][1] == pytest.approx(cent_y + (90.0 - cent_y) * 2.0)

    batch_edit_chapter_pages(
        tmp_path / "quad_ch",
        set_fields={"font_size_delta": -24},
        dry_run=False,
    )
    details_after_batch = get_page_details_dict(page)
    for p_actual, p_expected in zip(details_after_batch["regions"][0]["white_frame_quad_local"], quad_init):
        assert p_actual[0] == pytest.approx(p_expected[0])
        assert p_actual[1] == pytest.approx(p_expected[1])

    _, loupe_meta = generate_region_loupe_image(page, region_indices=[0], padding_px=20)
    crop_xyxy = loupe_meta["inspected_crops"][0]["world_crop_xyxy"]
    assert crop_xyxy[2] >= 425
    _, page_vis_meta = generate_page_inspection_image(page, mode="annotated_rendered")
    assert page_vis_meta["region_count"] == 1

    paths = resolve_page_paths(page)
    ik, raw_d = load_page_json(paths)
    raw_d["regions"][0]["has_custom_white_frame"] = False
    save_page_json_atomic(paths, ik, raw_d, auto_initial_snapshot=False)
    inp_gray = np.array(Image.open(paths.inpainted_path).convert("L"))
    assert partition_page_white_bubbles(inp_gray, raw_d["regions"]) == {}
    assert run_closed_loop_bubble_centering(tmp_path / "quad_ch", passes=1) == {}

    _, sum_rect = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "white_frame_rect_local": [-30.0, -50.0, 30.0, 50.0]}],
    )
    assert sum_rect[0]["has_perspective_quad"] is False
    assert sum_rect[0]["white_frame_quad_local"] is None

    apply_region_updates_to_page(page, [{"region_index": 0, "white_frame_quad_local": quad_init}])
    apply_region_updates_to_page(page, [{"region_index": 0, "angle": 12.0}])
    _, d_ang = load_page_json(resolve_page_paths(page))
    assert "render_box_quad_local" not in d_ang["regions"][0]
    assert "white_frame_quad_local" in d_ang["regions"][0]

    _, sum_center = apply_region_updates_to_page(page, [{"region_index": 0, "center": [250.0, 260.0]}])
    assert sum_center[0]["has_perspective_quad"] is False
    _, d_cen = load_page_json(resolve_page_paths(page))
    assert "white_frame_quad_local" not in d_cen["regions"][0]
    assert "render_box_quad_local" not in d_cen["regions"][0]


def test_blurred_richtext_ink_threshold_fallback_in_bubble_centering() -> None:
    inp_g = np.full((200, 200), 250, dtype=np.uint8)
    res_g = np.full((200, 200), 250, dtype=np.uint8)
    res_g[85:115, 92:108] = 226

    reg_crisp = {"center": [100.0, 100.0], "translation": "普通", "font_size": 24, "direction": "v"}
    assert _extract_page_exclusive_inks(inp_g, res_g, [reg_crisp]) == {}

    reg_blurred = {
        "center": [100.0, 100.0],
        "translation": "残影",
        "font_size": 24,
        "direction": "v",
        "translation_rich": {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "残影", "style": {"blur": 0.45}}],
                }
            ],
        },
    }
    excl = _extract_page_exclusive_inks(inp_g, res_g, [reg_blurred])
    assert 0 in excl
    assert int(excl[0].sum()) == 30 * 16


def test_last_output_path_resolution_bg_colors_precedence_and_windows_atomic_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
    ch_dir = tmp_path / "vol1" / "ch01"
    page = _make_page_fixture(ch_dir, "001.png")
    sib_dir = tmp_path / "vol1" / "ch01-translated"
    sib_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 600), (200, 200, 200)).save(sib_dir / "001.png")

    nested_out = tmp_path / "custom_exports" / "vol1" / "ch01" / "001.webp"
    nested_out.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (500, 600), (120, 180, 240)).save(nested_out)

    paths = resolve_page_paths(page)
    ik, data = load_page_json(paths)
    data["last_output_path"] = str(nested_out)
    data["last_export_dir"] = str(sib_dir)
    save_page_json_atomic(paths, ik, data, auto_initial_snapshot=False)

    resolved = resolve_page_paths(page)
    assert resolved.rendered_output_exists is True
    assert resolved.rendered_output_path == nested_out.resolve()
    details = get_page_details_dict(page)
    assert details["last_output_path"] == str(nested_out)

    fg_hex, bg_hex = resolve_region_colors_hex(
        {"font_color": "#112233", "bg_color": [0, 0, 0], "bg_colors": [255, 128, 0]}
    )
    assert fg_hex == "#112233"
    assert bg_hex == "#ff8000"

    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(src: Any, dst: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] % 3 != 0:
            err = PermissionError("sharing violation")
            err.winerror = 32  # type: ignore[attr-defined]
            raise err
        real_replace(src, dst)

    monkeypatch.setattr(ps, "_is_windows", lambda: True)
    monkeypatch.setattr(ps.os, "replace", flaky_replace)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    atomic_img_p = tmp_path / "atomic_img.png"
    atomic_glossary_p = tmp_path / "atomic_glossary.yaml"
    save_page_json_atomic(resolved, ik, data, auto_initial_snapshot=False)
    save_rgb_image_atomic(np.zeros((32, 32, 3), dtype=np.uint8), atomic_img_p)
    save_font_detection_cache_atomic(resolved.work_dir, {"version": 2})
    manage_glossary_entries(
        action="upsert",
        glossary_file=str(atomic_glossary_p),
        category="Person",
        entries=[{"original": "アイズ", "translation": "艾丝"}],
    )
    assert attempts["count"] == 12
    assert resolved.json_path.is_file()
    assert atomic_img_p.is_file()
    assert (resolved.work_dir / ".yuzumarker_font_cache.json").is_file()
    assert atomic_glossary_p.is_file()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_server_24_tools_enhancements_and_worker_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _make_page_fixture(tmp_path / "srv_ch", "001.png")
    assert "15° shear" in (server_mod.mtui_style_region_richtext.__doc__ or "")
    assert "10° shear" not in (server_mod.mtui_style_region_richtext.__doc__ or "")
    assert "blurAngle" in (server_mod.mtui_style_region_richtext.__doc__ or "")
    assert "rubyInherit" in (server_mod.mtui_style_region_richtext.__doc__ or "")
    assert "white_frame_quad_local" in (server_mod.mtui_update_regions.__doc__ or "")
    assert "GUI" in (server_mod.mtui_live_gui_session.__doc__ or "")
    prompt_txt = server_mod.proofread_and_polish_chapter("demo_folder")
    assert "rubyInherit" in prompt_txt and "white_frame_quad_local" in prompt_txt

    monkeypatch.setattr(
        server_mod.get_runtime_settings(),
        "load_mtui_config_dict",
        lambda: {"inpainter": {"inpainter": "blur", "blur_radius": 35}},
    )
    status_data = json.loads(server_mod.mtui_status())
    assert status_data["active_config_summary"]["inpainter_blur_radius"] == 35

    recorded_rpcs: list[tuple[str, dict[str, Any]]] = []

    class DummyWorker:
        is_running = True

        def call(self, action: str, params: dict[str, Any] | None = None, timeout_sec: float = 60.0) -> dict[str, Any]:
            recorded_rpcs.append((action, dict(params or {})))
            return {"status": "ok", "output_path": str(tmp_path / "out.png")}

    monkeypatch.setattr(server_mod, "get_worker_client", lambda: DummyWorker())
    monkeypatch.setattr(type(server_mod.get_runtime_settings()), "is_engine_available", property(lambda _s: True))

    server_mod.mtui_edit_mask_and_inpaint(
        str(page),
        add_rects=[[210, 160, 250, 200]],
        inpaint_method="neural",
        neural_inpainter="blur",
        blur_radius=42,
        re_render=False,
    )
    assert recorded_rpcs[-1][0] == "incremental_neural_inpaint"
    assert recorded_rpcs[-1][1]["inpainter"] == "blur"
    assert recorded_rpcs[-1][1]["blur_radius"] == 42

    captured_local_repair: dict[str, Any] = {}

    def fake_local_repair(
        base_rgb: np.ndarray,
        prev_inpaint: Any,
        new_mask: np.ndarray,
        added_delta: np.ndarray,
        removed_delta: np.ndarray,
        method: str = "telea",
        **kwargs: Any,
    ) -> np.ndarray:
        captured_local_repair["method"] = method
        captured_local_repair.update(kwargs)
        return base_rgb.copy()

    monkeypatch.setattr(server_mod, "apply_incremental_local_repair", fake_local_repair)
    blur_res = json.loads(
        server_mod.mtui_edit_mask_and_inpaint(
            str(page),
            add_rects=[[210, 160, 250, 200]],
            inpaint_method="blur",
            blur_radius=None,
            re_render=False,
        )
    )
    assert captured_local_repair == {"method": "blur", "blur_radius": 35}
    assert blur_res["inpaint_result"]["method"] == "blur"
    assert blur_res["inpaint_result"]["blur_radius"] == 35

    captured_overlay_kwargs: dict[str, Any] = {}

    def fake_manage_overlays(page_p: str, **kw: Any) -> tuple[Any, dict[str, Any]]:
        captured_overlay_kwargs.update(kw)
        return resolve_page_paths(page_p), {"count": 1}

    monkeypatch.setattr(server_mod, "manage_page_paste_overlays", fake_manage_overlays)
    server_mod.mtui_manage_paste_overlays(
        str(page),
        action="add_clone_patch",
        source_rect_xyxy=[10, 10, 50, 50],
        target_center_xy=[120, 120],
        z=3,
        flip_h=True,
        flip_v=True,
        re_render=False,
    )
    assert captured_overlay_kwargs["z"] == 3
    assert captured_overlay_kwargs["flip_h"] is True
    assert captured_overlay_kwargs["flip_v"] is True

    server_mod.mtui_render_pages(str(page), export_psd=True, psd_script_only=True)
    assert recorded_rpcs[-1][0] == "render_page"
    assert recorded_rpcs[-1][1]["export_psd"] is True
    assert recorded_rpcs[-1][1]["psd_script_only"] is True

    server_mod.mtui_ocr_or_translate_regions(str(page), region_indices=[0], ocr="paddleocr_vl")
    assert recorded_rpcs[-1][0] == "ocr_or_translate_regions"
    assert recorded_rpcs[-1][1]["ocr"] == "paddleocr_vl"

    server_mod.mtui_run_translation_pipeline(
        inputs=[str(tmp_path / "srv_ch")],
        use_subprocess=True,
        memory_limit=8192,
        memory_percent=75.0,
        batch_per_restart=10,
        batch_size=4,
        attempts=2,
        disable_onnx_gpu=True,
    )
    assert recorded_rpcs[-1][0] == "run_cli_pipeline"
    assert recorded_rpcs[-1][1]["subprocess"] is True
    assert recorded_rpcs[-1][1]["memory_limit"] == 8192
    assert recorded_rpcs[-1][1]["memory_percent"] == 75.0
    assert recorded_rpcs[-1][1]["batch_per_restart"] == 10
    assert recorded_rpcs[-1][1]["batch_size"] == 4
    assert recorded_rpcs[-1][1]["attempts"] == 2
    assert recorded_rpcs[-1][1]["disable_onnx_gpu"] is True
