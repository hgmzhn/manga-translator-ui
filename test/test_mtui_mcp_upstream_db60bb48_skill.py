"""Unit tests for upstream `manga-translator-ui` skill, `audit_and_compare.py`, & `mtui_mcp/README.md` adaptations."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mtui_mcp.path_resolver import SUPPORTED_IMAGE_EXTENSIONS, resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, save_page_json_atomic, save_rgb_image_atomic
from mtui_mcp.richtext_builder import region_is_bold


def _load_aac_module(monkeypatch: pytest.MonkeyPatch | None = None):
    if monkeypatch is not None:
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
    script_path = (
        _bootstrap.ROOT
        / "mtui_mcp"
        / "skills"
        / "mtui-manga-proofreading"
        / "cli"
        / "audit_and_compare.py"
    )
    spec = importlib.util.spec_from_file_location("audit_and_compare_db60bb48_skill", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_s1_translate_raw_and_compare_respect_last_output_path_and_translated_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aac = _load_aac_module(monkeypatch)

    assert "cfg_svc.shutdown()" in aac._HEADLESS_TRANSLATE_RUNNER
    assert "finally:" in aac._HEADLESS_TRANSLATE_RUNNER

    chapter = tmp_path / "ch_last_output"
    chapter.mkdir()
    src_p = chapter / "001.jpg"
    raw_rgb = np.full((320, 240, 3), 220, dtype=np.uint8)
    Image.fromarray(raw_rgb).save(src_p)

    ext_out_dir = tmp_path / "custom_nested_exports" / "vol01"
    ext_out_dir.mkdir(parents=True)
    rendered_p = ext_out_dir / "001.webp"
    before_rgb = raw_rgb.copy()
    before_rgb[60:120, 60:140] = (30, 60, 180)
    Image.fromarray(before_rgb).save(rendered_p, lossless=True)

    paths = resolve_page_paths(src_p)
    save_page_json_atomic(
        paths,
        str(src_p.resolve()),
        {
            "last_output_path": str(rendered_p.resolve()),
            "regions": [
                {
                    "lines": [[[60.0, 60.0], [140.0, 60.0], [140.0, 120.0], [60.0, 120.0]]],
                    "text": "テスト",
                    "translation": "初译文本",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    assert not (chapter / "manga_translator_work" / "result").exists()

    monkeypatch.setattr(
        aac,
        "_discover_mtui_runtime",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("Must not re-translate already-translated chapter!")),
    )

    before_dir = chapter / "manga_translator_work" / "mtui_qa" / "before_result"
    aac.cmd_translate_raw(chapter, backup_before_dir=before_dir, overwrite=False)
    snaps = list(before_dir.iterdir())
    assert len(snaps) == 1 and snaps[0].stem == "001"

    after_rgb = raw_rgb.copy()
    after_rgb[60:120, 60:140] = (180, 40, 40)
    Image.fromarray(after_rgb).save(rendered_p, lossless=True)

    cmp_dir_auto = tmp_path / "cmp_auto"
    aac.cmd_compare(raw_dir=chapter, before_dir=before_dir, after_dir=None, out_dir=cmp_dir_auto, prefix="cmp_v1_")
    cmp_files_auto = list(cmp_dir_auto.glob("cmp_v1_*.png"))
    assert len(cmp_files_auto) == 1
    with Image.open(cmp_files_auto[0]) as cmp_im:
        assert cmp_im.width >= 240 * 3

    cmp_dir_fallback = tmp_path / "cmp_fallback"
    aac.cmd_compare(
        raw_dir=chapter,
        before_dir=before_dir,
        after_dir=chapter / "manga_translator_work" / "result",
        out_dir=cmp_dir_fallback,
        prefix="cmp_v2_",
    )
    assert len(list(cmp_dir_fallback.glob("cmp_v2_*.png"))) == 1

    ch2 = tmp_path / "ch_sibling"
    ch2.mkdir()
    src2 = ch2 / "001.png"
    Image.fromarray(raw_rgb).save(src2)
    sib_dir = tmp_path / "ch_sibling-translated"
    sib_dir.mkdir()
    sib_out = sib_dir / "001.png"
    Image.fromarray(before_rgb).save(sib_out)
    paths2 = resolve_page_paths(src2)
    save_page_json_atomic(
        paths2,
        str(src2.resolve()),
        {"regions": [{"lines": [[[20.0, 20.0], [80.0, 20.0], [80.0, 90.0], [20.0, 90.0]]], "text": "あ", "translation": "啊"}]},
        auto_initial_snapshot=False,
    )
    before_dir2 = ch2 / "manga_translator_work" / "mtui_qa" / "before_result"
    aac.cmd_translate_raw(ch2, backup_before_dir=before_dir2, overwrite=False)
    assert (before_dir2 / "001.png").is_file()


def test_s2_verify_crops_perspective_quad_bounds_badge_and_centering_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aac = _load_aac_module(monkeypatch)
    folder = tmp_path / "persp_ch"
    folder.mkdir()
    img_path = folder / "001.png"

    raw_page = np.full((620, 620, 3), 240, dtype=np.uint8)
    inp_page = raw_page.copy()
    ren_page = raw_page.copy()
    ren_page[415:455, 460:505] = (15, 20, 210)
    mask = np.zeros((620, 620), dtype=np.uint8)
    mask[390:445, 440:500] = 255
    inp_page[390:445, 440:500] = 205
    ren_page[390:415, 440:500] = 205

    Image.fromarray(raw_page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(inp_page, paths.inpainted_path)
    save_rgb_image_atomic(ren_page, paths.rendered_output_path)

    persp_reg = {
        "lines": [[[90.0, 90.0], [170.0, 90.0], [170.0, 170.0], [90.0, 170.0]]],
        "center": [130.0, 130.0],
        "angle": 0.0,
        "text": "立入禁止",
        "translation": "禁止入内",
        "font_family": "FZHei-B01",
        "font_size": 24,
        "direction": "h",
        "alignment": "center",
        "has_custom_white_frame": True,
        "white_frame_quad_local": [[-40.0, -40.0], [375.0, -30.0], [380.0, 330.0], [-35.0, 310.0]],
    }
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {"mask_raw": encode_mask_base64(mask), "regions": [persp_reg]},
        auto_initial_snapshot=False,
    )

    qbb = aac._perspective_quad_world_bbox(persp_reg)
    assert qbb == pytest.approx((90.0, 90.0, 510.0, 460.0))

    vc_out = tmp_path / "vc_persp"
    manifest = aac.cmd_verify_crops(folder, vc_out)
    assert manifest["total_bubbles_exported"] == 1
    assert manifest["orphan_mask_zones_exported"] == 0
    sheet_im = np.asarray(Image.open(manifest["sheets"][0]).convert("RGB"))
    blue_px = np.sum((sheet_im[..., 2] >= 195) & (sheet_im[..., 0] <= 35))
    assert int(blue_px) >= 400, "Perspective quad rendered ink near (485, 435) was clipped out of verify-crops!"

    bub_canvas = np.full((400, 500), 200, dtype=np.uint8)
    cv2.ellipse(bub_canvas, (130, 200), (70, 110), 0, 0, 360, 255, -1)
    cv2.ellipse(bub_canvas, (130, 200), (70, 110), 0, 0, 360, 0, 3)
    cv2.ellipse(bub_canvas, (360, 200), (70, 110), 0, 0, 360, 255, -1)
    cv2.ellipse(bub_canvas, (360, 200), (70, 110), 0, 0, 360, 0, 3)
    regs_bub = [
        {
            "center": [130.0, 200.0],
            "direction": "v",
            "angle": 0.0,
            "white_frame_quad_local": [[-30, -50], [30, -50], [35, 50], [-35, 50]],
        },
        {"center": [360.0, 200.0], "direction": "v", "angle": 0.0},
    ]
    lobes = aac.partition_page_white_bubbles(bub_canvas, regs_bub)
    assert 0 not in lobes and 1 in lobes


def test_os3_raw_fonts_no_nameerror_richtext_bold_and_11_image_exts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aac = _load_aac_module(monkeypatch)
    assert aac.IMAGE_EXTS == set(SUPPORTED_IMAGE_EXTENSIONS)
    assert {".jfif", ".tiff", ".tif", ".heic", ".heif", ".avif", ".bmp"} <= aac.IMAGE_EXTS

    folder = tmp_path / "bmp_ch"
    folder.mkdir()
    img_path = folder / "001.bmp"
    page = np.full((300, 300, 3), 255, dtype=np.uint8)
    cv2.rectangle(page, (80, 70), (160, 210), (15, 15, 15), 3)
    Image.fromarray(page).save(img_path)

    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[80.0, 70.0], [160.0, 70.0], [160.0, 210.0], [80.0, 210.0]]],
                    "text": "いくぞ！",
                    "translation": "上吧！",
                    "translation_rich": {
                        "format": "richtext.v1",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "inlines": [{"type": "text", "text": "上吧！", "style": {"bold": True}}],
                            }
                        ],
                    },
                    "font_family": "FZHei-B01",
                    "font_size": 28,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    raw_disk = json.loads(paths.json_path.read_text(encoding="utf-8"))
    disk_img_data = next(iter(raw_disk.values()))
    disk_reg = disk_img_data["regions"][0]
    disk_reg.pop("bold", None)
    paths.json_path.write_text(json.dumps(raw_disk, ensure_ascii=False, indent=2), encoding="utf-8")

    reloaded_reg = next(iter(json.loads(paths.json_path.read_text(encoding="utf-8")).values()))["regions"][0]
    assert "bold" not in reloaded_reg
    assert reloaded_reg["translation_rich"]["format"] == "richtext.v1"
    assert region_is_bold(reloaded_reg) is True

    rf_out = tmp_path / "rf_out"
    aac.cmd_raw_fonts(folder, rf_out)
    grid_bold_path = rf_out / "raw_font_grid_1.png"
    assert grid_bold_path.is_file()
    grid_bold_hdr = np.asarray(Image.open(grid_bold_path).convert("RGB"))[:34, :, :].copy()

    disk_reg["translation_rich"]["blocks"][0]["inlines"][0].pop("style", None)
    paths.json_path.write_text(json.dumps(raw_disk, ensure_ascii=False, indent=2), encoding="utf-8")
    assert region_is_bold(disk_reg) is False

    rf_out_plain = tmp_path / "rf_out_plain"
    aac.cmd_raw_fonts(folder, rf_out_plain)
    grid_plain_hdr = np.asarray(Image.open(rf_out_plain / "raw_font_grid_1.png").convert("RGB"))[:34, :, :]
    assert not np.array_equal(grid_bold_hdr, grid_plain_hdr), "cmd_raw_fonts must render +B header tag from translation_rich alone!"

    aud_out = tmp_path / "aud_out"
    aac.cmd_audit(folder, aud_out)
    rep_txt = (aud_out / "baseline_and_ghost_report.txt").read_text(encoding="utf-8")
    assert "001.bmp" in rep_txt


def test_s3_skill_md_and_readme_contracts() -> None:
    repo_root = _bootstrap.ROOT
    skill_md = (repo_root / "mtui_mcp" / "skills" / "mtui-manga-proofreading" / "SKILL.md").read_text(encoding="utf-8")
    readme_md = (repo_root / "mtui_mcp" / "README.md").read_text(encoding="utf-8")

    assert "[PERSP-QUAD]" in skill_md
    assert "white_frame_quad_local" in skill_md
    assert "last_output_path" in skill_md

    assert "24" in readme_md
    assert "mtui_center_bubbles" in readme_md
    assert "mtui_live_gui_session" in readme_md
    for key in ("last_output_path", "white_frame_quad_local", "render_box_quad_local", "blurAngle", "rubyInherit"):
        assert key in readme_md
