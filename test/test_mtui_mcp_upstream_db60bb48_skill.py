"""Unit tests for upstream `manga-translator-ui` skill, `audit_and_compare.py`, & `mtui_mcp/README.md` adaptations."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_detector import (
    CACHE_VERSION,
    classify_current_mtui_font,
    recommend_mtui_font_for_prediction,
    region_bbox_signature,
)
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
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
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
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
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
    assert paths.rendered_output_path.resolve().is_relative_to(tmp_path.resolve())
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
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
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
    assert "PYTHONPATH" in readme_md
    assert "mtui_center_bubbles" in readme_md
    assert "mtui_live_gui_session" in readme_md
    for key in ("last_output_path", "white_frame_quad_local", "render_box_quad_local", "blurAngle", "rubyInherit"):
        assert key in readme_md


def test_s4_slice5_cli_audit_translate_backup_symlink_and_verify_crops_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
    aac = _load_aac_module(monkeypatch)

    # F63: _discover_mtui_runtime fallback preserves venv python path without dereferencing symlinks
    fake_root = tmp_path / "fake_mtui_root"
    (fake_root / "manga_translator").mkdir(parents=True)
    (fake_root / "manga_translator" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "manga_translator" / "manga_translator.py").write_text("# stub\n", encoding="utf-8")
    venv_py = fake_root / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n", encoding="utf-8")
    real_path_resolve = Path.resolve
    with monkeypatch.context() as mp_fb:
        mp_fb.setattr(
            "mtui_mcp.config.get_runtime_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("force fallback branch")),
        )
        mp_fb.setattr(
            Path,
            "resolve",
            lambda self, *a, **kw: (
                tmp_path / "dereferenced_sys_python"
                if self.name == "python"
                else real_path_resolve(self, *a, **kw)
            ),
        )
        disc_root, disc_py = aac._discover_mtui_runtime(fake_root)
    assert disc_root == real_path_resolve(fake_root)
    assert disc_py == Path(os.path.abspath(str(venv_py)))

    # F60 + F61 + F62: translate-raw excludes `*-translated`, passes temp files (not -c cmdline JSON), and backfills missing backups
    ch_tr = tmp_path / "ch_tr"
    ch_tr.mkdir()
    raw_rgb = np.full((120, 100, 3), 230, dtype=np.uint8)
    for fn in ("001.png", "001-translated.png", "002.png"):
        Image.fromarray(raw_rgb).save(ch_tr / fn)

    res_dir = ch_tr / "manga_translator_work" / "result"
    res_dir.mkdir(parents=True)
    for fn in ("001.png", "002.png"):
        Image.fromarray(raw_rgb).save(res_dir / fn)

    captured_subproc: dict[str, object] = {}

    def fake_subproc_run(cmd, **kwargs):
        captured_subproc["cmd"] = list(cmd)
        runner_file = Path(cmd[1])
        payload_file = Path(cmd[3])
        assert runner_file.is_file() and runner_file.suffix == ".py"
        assert payload_file.is_file() and payload_file.suffix == ".json"
        captured_subproc["runner_tmp"] = runner_file
        captured_subproc["payload_tmp"] = payload_file
        captured_subproc["payload"] = json.loads(payload_file.read_text(encoding="utf-8"))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(aac, "_discover_mtui_runtime", lambda *_a, **_kw: (fake_root, venv_py))
    monkeypatch.setattr(aac.subprocess, "run", fake_subproc_run)

    before_dir = tmp_path / "before_partial"
    before_dir.mkdir()
    # Pre-seed only page 1 in before_dir to simulate an interrupted earlier backup
    Image.fromarray(raw_rgb).save(before_dir / "001.png")

    aac.cmd_translate_raw(ch_tr, backup_before_dir=before_dir, overwrite=True)
    cmd_used = captured_subproc["cmd"]
    assert "-c" not in cmd_used
    payload_files = [Path(p).name for p in captured_subproc["payload"]["files"]]
    assert payload_files == ["001.png", "002.png"], "001-translated.png must not be re-translated as a raw source page"
    assert not captured_subproc["runner_tmp"].exists()
    assert not captured_subproc["payload_tmp"].exists()

    # R2-F74: Seed distinct baseline content in `before_dir / "001.png"` and distinct result in `res_dir / "002.png"`
    # so `overwrite=False` resume backfill proves existing `001.png` is NOT overwritten while `002.png` is backfilled.
    baseline_001_rgb = np.full((120, 100, 3), (18, 92, 214), dtype=np.uint8)
    res_002_rgb = np.full((120, 100, 3), (212, 48, 86), dtype=np.uint8)
    Image.fromarray(baseline_001_rgb).save(before_dir / "001.png")
    Image.fromarray(res_002_rgb).save(res_dir / "002.png")
    baseline_001_bytes = (before_dir / "001.png").read_bytes()
    expected_002_bytes = (res_dir / "002.png").read_bytes()
    assert baseline_001_bytes != (res_dir / "001.png").read_bytes()
    (before_dir / "002.png").unlink()
    kept_plus_copied = aac._backup_result_to_before(
        res_dir,
        before_dir,
        overwrite=False,
        source_pages=[ch_tr / "001.png", ch_tr / "002.png"],
    )
    assert kept_plus_copied == 2
    assert (before_dir / "001.png").is_file() and (before_dir / "002.png").is_file()
    assert (before_dir / "001.png").read_bytes() == baseline_001_bytes
    assert np.array_equal(np.asarray(Image.open(before_dir / "001.png").convert("RGB")), baseline_001_rgb)
    assert (before_dir / "002.png").read_bytes() == expected_002_bytes
    assert np.array_equal(np.asarray(Image.open(before_dir / "002.png").convert("RGB")), res_002_rgb)
    assert list(before_dir.glob("*.tmp")) == []

    # F59: cmd_audit decodes nested array `mask_raw` via decode_mask_base64 without TypeError
    ch_aud = tmp_path / "ch_aud"
    ch_aud.mkdir()
    p007 = ch_aud / "007.png"
    Image.fromarray(np.full((40, 40, 3), 255, dtype=np.uint8)).save(p007)
    p007_paths = resolve_page_paths(p007)
    save_rgb_image_atomic(np.full((40, 40, 3), 255, dtype=np.uint8), p007_paths.inpainted_path)
    save_rgb_image_atomic(np.full((40, 40, 3), 255, dtype=np.uint8), p007_paths.rendered_output_path)
    nested_mask = [[255 if (10 <= r < 20 and 10 <= c < 20) else 0 for c in range(40)] for r in range(40)]
    p007_paths.json_path.parent.mkdir(parents=True, exist_ok=True)
    p007_paths.json_path.write_text(
        json.dumps(
            {
                str(p007): {
                    "mask_raw": nested_mask,
                    "regions": [
                        {
                            "lines": [[[8.0, 8.0], [32.0, 8.0], [32.0, 32.0], [8.0, 32.0]]],
                            "text": "テスト",
                            "translation": "测试",
                            "font_family": "FZHei-B01",
                            "font_size": 16,
                            "direction": "v",
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    aud_out = tmp_path / "aud_nested"
    aac.cmd_audit(ch_aud, aud_out)
    assert (aud_out / "baseline_and_ghost_report.txt").is_file()

    # F134 + F64: `--pages 007` matches stem `007.png` when p_no==1, and missing rendered output is recorded in skipped_pages
    vc_007 = tmp_path / "vc_007"
    mf_007 = aac.cmd_verify_crops(ch_aud, vc_007, pages=["007"])
    assert mf_007["selected_pages"] == ["007.png"]
    assert mf_007["exported_pages"] == ["007.png"]
    assert mf_007["skipped_pages"] == []
    assert mf_007["verify_sheet_count"] == 1

    p008 = ch_aud / "008.png"
    Image.fromarray(np.full((40, 40, 3), 255, dtype=np.uint8)).save(p008)
    p008_paths = resolve_page_paths(p008)
    save_page_json_atomic(
        p008_paths,
        str(p008),
        {"regions": [{"lines": [[[8.0, 8.0], [32.0, 8.0], [32.0, 32.0], [8.0, 32.0]]], "text": "あ", "translation": "啊"}]},
        auto_initial_snapshot=False,
    )
    # Intentionally omit p008 rendered output
    vc_all = tmp_path / "vc_all"
    mf_all = aac.cmd_verify_crops(ch_aud, vc_all)
    assert mf_all["selected_pages"] == ["007.png", "008.png"]
    assert mf_all["exported_pages"] == ["007.png"]
    assert len(mf_all["missing_artifact_pages"]) == 1
    assert mf_all["missing_artifact_pages"][0]["page"] == "008.png"
    assert "missing_rendered_output" in mf_all["missing_artifact_pages"][0]["reason"]
    assert "missing_inpainted" in mf_all["missing_artifact_pages"][0]["reason"]


def test_s5_r2_slice5_ym_top1_gate_missing_inpainted_snapshot_cleanup_and_compare_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
    aac = _load_aac_module(monkeypatch)
    fmt = aac._format_verify_crop_ym_line

    # R2-F07: `top1 < 0.68` and all Step-1 handbook pre-exceptions must NEVER lock `[LOCKED>=0.90]`
    # or emit `[⚠ HIGH-CONF YM MISMATCH!]`, even when `pred_conf >= 0.90`.
    ym_low_top1 = {
        "n_clean_chars": 5,
        "pred_family": "ROUND_GOTHIC",
        "pred_conf": 0.95,
        "bold_prob": 0.10,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "FOT-SeuratPro-M.otf", "family": "ROUND_GOTHIC", "prob": 0.50}],
    }
    line_t1_match, _, mis_t1_match = fmt(
        ym_low_top1,
        ff="FZZhunYuan-M02",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_t1_match is False
    assert "[LOCKED>=0.90]" not in line_t1_match
    assert "HIGH-CONF YM MISMATCH" not in line_t1_match
    assert "[TOP1<0.68:" in line_t1_match

    line_t1_diff, _, mis_t1_diff = fmt(
        ym_low_top1,
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_t1_diff is False
    assert "[LOCKED>=0.90]" not in line_t1_diff
    assert "HIGH-CONF YM MISMATCH" not in line_t1_diff
    assert "[TOP1<0.68:" in line_t1_diff

    # When `top1 >= 0.68` and no Step-1 pre-exceptions apply, lock / mismatch work normally.
    ym_hi_top1 = {
        **ym_low_top1,
        "top_fonts": [{"font": "FOT-SeuratPro-M.otf", "family": "ROUND_GOTHIC", "prob": 0.72}],
    }
    line_hi_ok, _, mis_hi_ok = fmt(
        ym_hi_top1,
        ff="FZZhunYuan-M02",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_hi_ok is False
    assert "rec=FZZhunYuan-M02 [LOCKED>=0.90]" in line_hi_ok

    line_hi_mis, _, mis_hi_mis = fmt(
        ym_hi_top1,
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_hi_mis is True
    assert "[⚠ HIGH-CONF YM MISMATCH!]" in line_hi_mis

    # Step-1 `[THIN-STEM]` and non-core `KAISHO_BRUSH` pre-exceptions also suppress high-conf mismatch/lock.
    line_thin_ant, _, mis_thin_ant = fmt(
        {
            "n_clean_chars": 6,
            "pred_family": "MINCHO",
            "pred_conf": 0.99,
            "bold_prob": 0.99,
            "heavy_prob": 0.70,
            "top_fonts": [{"font": "A-OTF-RyuminPr6-Heavy.otf", "family": "MINCHO", "prob": 0.82}],
        },
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.0,
        stem_hw=2.05,
        bbox_wh=(110.0, 125.0),
        source_text="おいオシアン……！",
        page_wh=(1350, 1920),
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_thin_ant is False
    assert "[THIN-STEM:" in line_thin_ant
    assert "HIGH-CONF YM MISMATCH" not in line_thin_ant
    assert "[LOCKED>=0.90]" not in line_thin_ant

    line_noncore_kai, _, mis_noncore_kai = fmt(
        {
            "n_clean_chars": 8,
            "pred_family": "KAISHO_BRUSH",
            "pred_conf": 0.98,
            "bold_prob": 0.95,
            "heavy_prob": 0.10,
            "top_fonts": [{"font": "DFKKaiShoC-W7.ttc", "family": "KAISHO_BRUSH", "prob": 0.91}],
        },
        ff="FZYaSong-DB-GBK",
        shw=2.2,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_noncore_kai is False
    assert "[LOCKED>=0.90]" not in line_noncore_kai
    assert "HIGH-CONF YM MISMATCH" not in line_noncore_kai

    # R2-F87: Missing `inpainted` sidecar on a page requiring inpaint verification is flagged in `missing_artifact_pages`
    # instead of silently falling back to `_classify_page_regions_in_bubble(None, regs)` 2-panel bubble cards,
    # whereas a genuinely textless page (`regions: []`, no `mask_raw`) remains in `skipped_pages` (`no_visible_regions`).
    ch_inp = tmp_path / "ch_missing_inp"
    ch_inp.mkdir()
    p1 = ch_inp / "001.png"
    p2 = ch_inp / "002.png"
    dark_art = np.full((240, 240, 3), 150, dtype=np.uint8)
    Image.fromarray(dark_art).save(p1)
    Image.fromarray(dark_art).save(p2)

    p1_paths = resolve_page_paths(p1)
    save_rgb_image_atomic(dark_art, p1_paths.rendered_output_path)
    save_page_json_atomic(
        p1_paths,
        str(p1.resolve()),
        {
            "regions": [
                {
                    "lines": [[[60.0, 60.0], [150.0, 60.0], [150.0, 170.0], [60.0, 170.0]]],
                    "center": [105.0, 115.0],
                    "text": "ぐぬぬ",
                    "translation": "可恶",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                    "alignment": "left",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    p2_paths = resolve_page_paths(p2)
    save_rgb_image_atomic(dark_art, p2_paths.rendered_output_path)
    save_page_json_atomic(p2_paths, str(p2.resolve()), {"regions": []}, auto_initial_snapshot=False)

    mf_missing = aac.cmd_verify_crops(ch_inp, tmp_path / "vc_missing_inp")
    assert mf_missing["selected_pages"] == ["001.png", "002.png"]
    assert mf_missing["exported_pages"] == []
    assert mf_missing["total_bubbles_exported"] == 0
    assert mf_missing["missing_artifact_pages"] == [
        {"page": "001.png", "page_number": 1, "reason": "missing_inpainted"}
    ]
    assert {"page": "002.png", "page_number": 2, "reason": "no_visible_regions"} in mf_missing["skipped_pages"]

    # Once `inpainted` is present, page 1 exports as a 3-panel frameless card with zero missing artifacts.
    save_rgb_image_atomic(dark_art, p1_paths.inpainted_path)
    mf_complete = aac.cmd_verify_crops(ch_inp, tmp_path / "vc_complete_inp")
    assert mf_complete["missing_artifact_pages"] == []
    assert mf_complete["exported_pages"] == ["001.png"]
    assert mf_complete["frameless_regions_exported"] == 1

    # R2-F88 + R2-F49: `SKILL.md` / `unattended_batch_ops.md` contracts & `cmd_compare` coverage check
    skill_dir = _bootstrap.ROOT / "mtui_mcp" / "skills" / "mtui-manga-proofreading"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    batch_md = (skill_dir / "references" / "unattended_batch_ops.md").read_text(encoding="utf-8")

    assert 'mtui_snapshots(target_path="<CHAPTER>", action="cleanup", snapshot_id="<RECORDED_ID>")' in skill_md
    assert 'mtui_snapshots(target_path="<CHAPTER>", action="cleanup", snapshot_id="<RECORDED_ID>")' in batch_md
    assert 'action="cleanup", label=' not in skill_md
    assert 'action="cleanup", label=' not in batch_md
    assert "initial_before_mcp" in skill_md
    assert "initial_before_mcp" in batch_md
    assert "created_snapshot_ids" in skill_md
    assert "created_snapshot_ids" in batch_md
    assert "missing_before == 0" in batch_md and "missing_after == 0" in batch_md

    before_cmp = tmp_path / "before_cmp"
    before_cmp.mkdir()
    Image.fromarray(dark_art).save(before_cmp / "001.png")
    Image.fromarray(dark_art).save(before_cmp / "002.png")
    cmp_full = aac.cmd_compare(
        raw_dir=ch_inp,
        before_dir=before_cmp,
        after_dir=None,
        out_dir=tmp_path / "cmp_zero_diff_ok",
    )
    assert cmp_full["sheets_generated"] == 0
    assert cmp_full["compared_page_count"] == 2
    assert cmp_full["complete_coverage"] is True

    p2_paths.rendered_output_path.unlink()
    cmp_incomplete = aac.cmd_compare(
        raw_dir=ch_inp,
        before_dir=before_cmp,
        after_dir=None,
        out_dir=tmp_path / "cmp_incomplete",
    )
    assert cmp_incomplete["sheets_generated"] == 0
    assert cmp_incomplete["missing_after_pages"] == ["002.png"]
    assert cmp_incomplete["complete_coverage"] is False


def test_s6_r3_slice5_skipped_regions_corrupt_mask_ym_version_compare_pairing_and_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", None)
    aac = _load_aac_module(monkeypatch)

    # 1. R3-F33 & R3-F08 doc contracts in SKILL.md and unattended_batch_ops.md
    skill_dir = _bootstrap.ROOT / "mtui_mcp" / "skills" / "mtui-manga-proofreading"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    batch_md = (skill_dir / "references" / "unattended_batch_ops.md").read_text(encoding="utf-8")

    assert "regression_diff.passed == True" in skill_md
    assert "unaudited_pages == []" in skill_md
    assert "passed=True" in batch_md and "unaudited_pages == []" in batch_md
    assert "skipped_regions" in skill_md and "mtui_inspect_region_visual" in skill_md
    assert "skipped_regions" in batch_md and "mtui_inspect_region_visual" in batch_md

    # 2. R3-F08: Visible regions skipped due to `< 24px` crop do NOT inflate `total_bubbles_exported` /
    #    `frameless_regions_exported` and are recorded in `manifest["skipped_regions"]`.
    ch_skip_reg = tmp_path / "ch_skip_reg"
    ch_skip_reg.mkdir()
    p_sr = ch_skip_reg / "001.png"
    canvas_sr = np.full((300, 300, 3), 180, dtype=np.uint8)
    cv2.ellipse(canvas_sr, (120, 140), (60, 85), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(canvas_sr, (120, 140), (60, 85), 0, 0, 360, (10, 10, 10), 3)
    Image.fromarray(canvas_sr).save(p_sr)
    sr_paths = resolve_page_paths(p_sr)
    save_rgb_image_atomic(canvas_sr, sr_paths.inpainted_path)
    save_rgb_image_atomic(canvas_sr, sr_paths.rendered_output_path)
    save_page_json_atomic(
        sr_paths,
        str(p_sr.resolve()),
        {
            "regions": [
                {
                    "lines": [[[90.0, 85.0], [150.0, 85.0], [150.0, 195.0], [90.0, 195.0]]],
                    "center": [120.0, 140.0],
                    "text": "大丈夫だ",
                    "translation": "没问题",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                    "alignment": "left",
                },
                {
                    # Visible region pushed near/past right boundary so clamped crop width < 24px
                    "lines": [[[342.0, 120.0], [356.0, 120.0], [356.0, 150.0], [342.0, 150.0]]],
                    "center": [349.0, 135.0],
                    "text": "あ",
                    "translation": "啊",
                    "font_family": "FZHei-B01",
                    "font_size": 12,
                    "direction": "v",
                    "alignment": "left",
                },
            ]
        },
        auto_initial_snapshot=False,
    )
    vc_sr_out = tmp_path / "vc_skip_reg"
    mf_sr = aac.cmd_verify_crops(ch_skip_reg, vc_sr_out)
    assert mf_sr["exported_pages"] == ["001.png"]
    assert mf_sr["total_bubbles_exported"] == 1
    assert mf_sr["frameless_regions_exported"] == 0
    assert mf_sr["verify_sheet_count"] == 1
    assert mf_sr["skipped_regions"] == [
        {"page": "001.png", "page_number": 1, "region_index": 1, "reason": "crop_too_small"}
    ]
    disk_mf_sr = json.loads((vc_sr_out / "verify_crops_manifest.json").read_text(encoding="utf-8"))
    assert disk_mf_sr["skipped_regions"] == mf_sr["skipped_regions"]

    # 3. R3-F30: Corrupt non-empty `mask_raw` on a page with no visible regions is flagged as `corrupt_mask`
    #    in `missing_artifact_pages`, whereas a valid all-zero `mask_raw` skips cleanly as `no_visible_regions`.
    ch_mask = tmp_path / "ch_corrupt_mask"
    ch_mask.mkdir()
    pm1 = ch_mask / "001.png"
    pm2 = ch_mask / "002.png"
    blank_img = np.full((120, 120, 3), 245, dtype=np.uint8)
    Image.fromarray(blank_img).save(pm1)
    Image.fromarray(blank_img).save(pm2)

    pm1_paths = resolve_page_paths(pm1)
    save_rgb_image_atomic(blank_img, pm1_paths.rendered_output_path)
    pm1_paths.json_path.parent.mkdir(parents=True, exist_ok=True)
    pm1_paths.json_path.write_text(
        json.dumps({str(pm1.resolve()): {"mask_raw": "!!!NOT_VALID_BASE64_MASK!!!", "regions": []}}),
        encoding="utf-8",
    )

    pm2_paths = resolve_page_paths(pm2)
    save_rgb_image_atomic(blank_img, pm2_paths.rendered_output_path)
    zero_mask_b64 = encode_mask_base64(np.zeros((120, 120), dtype=np.uint8))
    save_page_json_atomic(
        pm2_paths,
        str(pm2.resolve()),
        {"mask_raw": zero_mask_b64, "regions": []},
        auto_initial_snapshot=False,
    )

    mf_mask = aac.cmd_verify_crops(ch_mask, tmp_path / "vc_corrupt_mask")
    assert mf_mask["missing_artifact_pages"] == [
        {"page": "001.png", "page_number": 1, "reason": "corrupt_mask"}
    ]
    assert mf_mask["skipped_pages"] == [
        {"page": "001.png", "page_number": 1, "reason": "corrupt_mask"},
        {"page": "002.png", "page_number": 2, "reason": "no_visible_regions"},
    ]

    # 4. R3-F32: `cmd_verify_crops` enforces `_parse_ym_cache_version(w_ym_cache) >= CACHE_VERSION`
    #    and `isinstance(pages, dict)` so stale/invalid caches never emit `[⚠ HIGH-CONF YM MISMATCH!]`.
    ym_sig = region_bbox_signature([90.0, 85.0, 150.0, 195.0])
    ym_pred_entry = {
        "ridx": 0,
        "bbox_sig": ym_sig,
        "center": [120.0, 140.0],
        "n_clean_chars": 6,
        "pred_family": "POP_CHISEL",
        "pred_conf": 0.99,
        "bold_prob": 1.0,
        "heavy_prob": 0.2,
        "family_probs": {"POP_CHISEL": 0.99, "ANTIQUE": 0.01},
        "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.92}],
    }
    ym_pages_payload = {
        "001": {
            "region_count": 2,
            "by_bbox_sig": {ym_sig: ym_pred_entry},
            "by_ridx": {"0": ym_pred_entry},
        }
    }
    ym_cache_path = sr_paths.work_dir / ".yuzumarker_font_cache.json"
    for idx_bad, bad_cache in enumerate(
        (
            {"version": 1, "pages": ym_pages_payload},
            {"version": "unknown", "pages": ym_pages_payload},
            {"pages": ym_pages_payload},
            {"version": CACHE_VERSION, "pages": ["001"]},
        )
    ):
        ym_cache_path.write_text(json.dumps(bad_cache, ensure_ascii=False), encoding="utf-8")
        mf_bad_ym = aac.cmd_verify_crops(ch_skip_reg, tmp_path / f"vc_ym_bad_{idx_bad}")
        assert mf_bad_ym["ym_high_conf_mismatch_flagged"] == 0

    ym_cache_path.write_text(
        json.dumps({"version": CACHE_VERSION, "pages": ym_pages_payload}, ensure_ascii=False),
        encoding="utf-8",
    )
    mf_good_ym = aac.cmd_verify_crops(ch_skip_reg, tmp_path / "vc_ym_current")
    assert mf_good_ym["ym_high_conf_mismatch_flagged"] == 1

    # 5. R3-F31: `cmd_compare` pairs by exact filename first, restricts cross-extension stem fallback to unique stems,
    #    and computes `complete_coverage` from paired expected raw pages rather than `compared_count`.
    raw_dup = tmp_path / "raw_dup_stem"
    before_dup = tmp_path / "before_dup_stem"
    after_dup = tmp_path / "after_dup_stem"
    raw_dup.mkdir()
    before_dup.mkdir()
    after_dup.mkdir()

    raw_p1_png = np.full((80, 80, 3), (210, 40, 40), dtype=np.uint8)
    raw_p1_jpg = np.full((80, 80, 3), (220, 190, 30), dtype=np.uint8)
    bef_p1_png = np.full((80, 80, 3), (40, 200, 50), dtype=np.uint8)
    bef_p1_jpg = np.full((80, 80, 3), (190, 40, 200), dtype=np.uint8)
    bef_p2_png = np.full((80, 80, 3), (120, 90, 40), dtype=np.uint8)
    aft_p1_png = np.full((80, 80, 3), (40, 60, 220), dtype=np.uint8)
    aft_p1_jpg = np.full((80, 80, 3), (30, 200, 210), dtype=np.uint8)
    aft_p2_png = np.full((80, 80, 3), (50, 130, 110), dtype=np.uint8)
    raw_ambig_bmp = np.full((80, 80, 3), (170, 170, 230), dtype=np.uint8)

    # Raw has BOTH `001.png` and `001.jpg`; Before & After only have `001.png` + stale orphan `002.png`.
    Image.fromarray(raw_p1_png).save(raw_dup / "001.png")
    Image.fromarray(raw_p1_jpg).save(raw_dup / "001.jpg", quality=98)
    Image.fromarray(bef_p1_png).save(before_dup / "001.png")
    Image.fromarray(bef_p2_png).save(before_dup / "002.png")
    Image.fromarray(aft_p1_png).save(after_dup / "001.png")
    Image.fromarray(aft_p2_png).save(after_dup / "002.png")

    cmp_dup_inc_dir = tmp_path / "cmp_dup_incomplete"
    cmp_dup_res = aac.cmd_compare(
        raw_dir=raw_dup,
        before_dir=before_dup,
        after_dir=after_dup,
        out_dir=cmp_dup_inc_dir,
    )
    assert cmp_dup_res["expected_page_count"] == 2
    assert cmp_dup_res["compared_page_count"] == 1
    assert cmp_dup_res["sheets_generated"] == 1
    assert cmp_dup_res["missing_before_pages"] == ["001.jpg"]
    assert cmp_dup_res["missing_after_pages"] == ["001.jpg"]
    assert cmp_dup_res["complete_coverage"] is False
    # Verify center pixels of Raw / Before / After columns (`x = 54, 150, 246`, `y = 96`) match `001.png`'s distinct colors
    inc_sheet_rgb = np.asarray(Image.open(cmp_dup_inc_dir / "cmp_p02_001.png").convert("RGB"))
    assert np.allclose(inc_sheet_rgb[96, 54], raw_p1_png[0, 0], atol=3)
    assert np.allclose(inc_sheet_rgb[96, 150], bef_p1_png[0, 0], atol=3)
    assert np.allclose(inc_sheet_rgb[96, 246], aft_p1_png[0, 0], atol=3)

    # Once `001.jpg` is also present in Before and After, both raw pages pair cleanly by exact filename.
    Image.fromarray(bef_p1_jpg).save(before_dup / "001.jpg", quality=98)
    Image.fromarray(aft_p1_jpg).save(after_dup / "001.jpg", quality=98)
    cmp_dup_ok_dir = tmp_path / "cmp_dup_complete"
    cmp_dup_ok = aac.cmd_compare(
        raw_dir=raw_dup,
        before_dir=before_dup,
        after_dir=after_dup,
        out_dir=cmp_dup_ok_dir,
    )
    assert cmp_dup_ok["expected_page_count"] == 2
    assert cmp_dup_ok["compared_page_count"] == 2
    assert cmp_dup_ok["sheets_generated"] == 2
    assert cmp_dup_ok["missing_before_pages"] == []
    assert cmp_dup_ok["missing_after_pages"] == []
    assert cmp_dup_ok["complete_coverage"] is True
    # `001.jpg` sorts before `001.png` (`cmp_p01_001.png` = `001.jpg`, `cmp_p02_001.png` = `001.png`); verify both sheets' column pixels!
    jpg_sheet_rgb = np.asarray(Image.open(cmp_dup_ok_dir / "cmp_p01_001.png").convert("RGB"))
    assert np.allclose(jpg_sheet_rgb[96, 54], raw_p1_jpg[0, 0], atol=8)
    assert np.allclose(jpg_sheet_rgb[96, 150], bef_p1_jpg[0, 0], atol=8)
    assert np.allclose(jpg_sheet_rgb[96, 246], aft_p1_jpg[0, 0], atol=8)
    png_sheet_rgb = np.asarray(Image.open(cmp_dup_ok_dir / "cmp_p02_001.png").convert("RGB"))
    assert np.allclose(png_sheet_rgb[96, 54], raw_p1_png[0, 0], atol=3)
    assert np.allclose(png_sheet_rgb[96, 150], bef_p1_png[0, 0], atol=3)
    assert np.allclose(png_sheet_rgb[96, 246], aft_p1_png[0, 0], atol=3)

    # Ambiguous duplicate stems in `before_dir` (`001.png` + `001.jpg`) must not guess-match `raw / 001.bmp`.
    raw_ambig = tmp_path / "raw_ambig_target"
    raw_ambig.mkdir()
    Image.fromarray(raw_ambig_bmp).save(raw_ambig / "001.bmp")
    cmp_ambig = aac.cmd_compare(
        raw_dir=raw_ambig,
        before_dir=before_dup,
        after_dir=after_dup,
        out_dir=tmp_path / "cmp_ambig_target",
    )
    assert cmp_ambig["missing_before_pages"] == ["001.bmp"]
    assert cmp_ambig["missing_after_pages"] == ["001.bmp"]
    assert cmp_ambig["complete_coverage"] is False

    # 6. R3-F63: Explicit non-existent (or non-directory) `raw_dir` raises FileNotFoundError instead of falling back
    #    to 2-column mode, while `raw_dir=None` still runs 2-column mode cleanly.
    with pytest.raises(FileNotFoundError, match="raw_dir"):
        aac.cmd_compare(
            raw_dir=tmp_path / "does_not_exist_raw_dir",
            before_dir=before_dup,
            after_dir=after_dup,
            out_dir=tmp_path / "cmp_bad_raw",
        )
    not_a_dir = tmp_path / "raw_is_a_file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="raw_dir"):
        aac.cmd_compare(
            raw_dir=not_a_dir,
            before_dir=before_dup,
            after_dir=after_dup,
            out_dir=tmp_path / "cmp_file_raw",
        )

    cmp_2col_dir = tmp_path / "cmp_2col_ok"
    cmp_2col = aac.cmd_compare(
        raw_dir=None,
        before_dir=before_dup,
        after_dir=after_dup,
        out_dir=cmp_2col_dir,
    )
    assert cmp_2col["expected_page_count"] == 3
    assert cmp_2col["compared_page_count"] == 3
    assert cmp_2col["complete_coverage"] is True
    with Image.open(next(cmp_2col_dir.glob("cmp_*.png"))) as im_2c:
        assert im_2c.width < 80 * 3 + 40
    # Verify 2-column Before/After center pixels (`x = 54, 150`, `y = 96`) for all 3 pages including `002.png`
    c2_jpg = np.asarray(Image.open(cmp_2col_dir / "cmp_p01_001.png").convert("RGB"))
    assert np.allclose(c2_jpg[96, 54], bef_p1_jpg[0, 0], atol=8)
    assert np.allclose(c2_jpg[96, 150], aft_p1_jpg[0, 0], atol=8)
    c2_png = np.asarray(Image.open(cmp_2col_dir / "cmp_p02_001.png").convert("RGB"))
    assert np.allclose(c2_png[96, 54], bef_p1_png[0, 0], atol=3)
    assert np.allclose(c2_png[96, 150], aft_p1_png[0, 0], atol=3)
    c2_p2 = np.asarray(Image.open(cmp_2col_dir / "cmp_p03_002.png").convert("RGB"))
    assert np.allclose(c2_p2[96, 54], bef_p2_png[0, 0], atol=3)
    assert np.allclose(c2_p2[96, 150], aft_p2_png[0, 0], atol=3)


def test_s7_r4_slice5_thin_orphan_mask_zero_dim_mask_compare_json_and_doc_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = get_runtime_settings()
    monkeypatch.setattr(rt, "mtui_root", None)
    monkeypatch.setattr(rt, "load_mtui_config_dict", lambda: {"app": {"last_output_path": ""}, "cli": {}})
    aac = _load_aac_module(monkeypatch)

    # 1. R4-F20, R4-F44, R4-F45, R4-F47: SKILL.md & unattended_batch_ops.md contract assertions
    skill_dir = _bootstrap.ROOT / "mtui_mcp" / "skills" / "mtui-manga-proofreading"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    batch_md = (skill_dir / "references" / "unattended_batch_ops.md").read_text(encoding="utf-8")

    # R4-F44: review scope strictly follows current manifest `sheets` list (never leftover globbed files)
    assert "严格以当前 `verify_crops_manifest.json` 的 `sheets` 列表为准" in skill_md
    assert "本轮最新 `verify_crops_manifest.json` 的 `sheets` 列表" in skill_md
    # R4-F45: `crops_too_small` pages allowed in `skipped_pages` after full region fallback verification; `no_visible_regions` still requires Raw check
    assert "crops_too_small" in skill_md and "no_visible_regions" in skill_md
    assert "crops_too_small" in batch_md and "no_visible_regions" in batch_md
    # R4-F20: both initial Gate B and modified_pages re-check specify full-page Raw + Rendered fallback + coordinate fix + Gate B block
    assert skill_md.count("整页原图与成图核实") >= 2
    assert "不得通过 Gate B 门禁" in skill_md
    assert "仍无法确认时不得通过门禁" in skill_md
    # R4-F47: unattended_batch_ops.md restores by recorded `snapshot_id`, not `label="pre_gate_b"`
    assert 'mtui_snapshots(target_path="<PAGE>", action="restore", snapshot_id="<RECORDED_ID>")' in batch_md
    assert 'action="restore", label="pre_gate_b"' not in batch_md

    # 2. R4-F15: `cmd_verify_crops` reuses probed mask with thin-preserving downscale (preserving 1px odd column `x=1001`
    #    on `2000x2000 -> 1000x1000`) and skips resizing when mask dimensions already match `(ih, iw)`.
    ch_thin = tmp_path / "ch_thin_orphan"
    ch_thin.mkdir()
    p_thin = ch_thin / "001.png"
    raw_1k = np.full((1000, 1000, 3), 190, dtype=np.uint8)
    ren_1k = raw_1k.copy()
    ren_1k[200:800, 498:503] = (20, 30, 220)
    Image.fromarray(raw_1k).save(p_thin)
    thin_paths = resolve_page_paths(p_thin)
    save_rgb_image_atomic(raw_1k, thin_paths.inpainted_path)
    save_rgb_image_atomic(ren_1k, thin_paths.rendered_output_path)

    mask_2k = np.zeros((2000, 2000), dtype=np.uint8)
    mask_2k[200:1800, 1001] = 255  # 1px-wide odd column: dropped completely by INTER_NEAREST, 800px under INTER_AREA > 0
    save_page_json_atomic(
        thin_paths,
        str(p_thin.resolve()),
        {"mask_raw": encode_mask_base64(mask_2k), "regions": []},
        auto_initial_snapshot=False,
    )
    mf_thin = aac.cmd_verify_crops(ch_thin, tmp_path / "vc_thin_odd")
    assert mf_thin["missing_artifact_pages"] == []
    assert mf_thin["orphan_mask_zones_exported"] == 1
    assert mf_thin["verify_sheet_count"] == 1

    # Equal-size `1000x1000` mask must NOT invoke `cv2.resize` for mask scaling
    mask_1k = np.zeros((1000, 1000), dtype=np.uint8)
    mask_1k[200:700, 500:502] = 255
    save_page_json_atomic(
        thin_paths,
        str(p_thin.resolve()),
        {"mask_raw": encode_mask_base64(mask_1k), "regions": []},
        auto_initial_snapshot=False,
    )
    real_cv2_resize = aac.cv2.resize
    resize_calls_1k: list[tuple[int, int]] = []

    def spy_cv2_resize(src, dsize, *args, **kwargs):
        resize_calls_1k.append(tuple(dsize))
        return real_cv2_resize(src, dsize, *args, **kwargs)

    with monkeypatch.context() as mp_eq:
        mp_eq.setattr(aac.cv2, "resize", spy_cv2_resize)
        mf_eq = aac.cmd_verify_crops(ch_thin, tmp_path / "vc_thin_eq")
    assert mf_eq["orphan_mask_zones_exported"] == 1
    assert (1000, 1000) not in resize_calls_1k

    # 3. R4-F16: Zero-size decoded array `mask_raw=[[]]` is recorded as `corrupt_mask` without aborting subsequent pages,
    #    while a valid all-zero 2D list mask passes cleanly as `no_visible_regions`.
    ch_zmask = tmp_path / "ch_zero_dim_mask"
    ch_zmask.mkdir()
    pz1 = ch_zmask / "001.png"
    pz2 = ch_zmask / "002.png"
    pz3 = ch_zmask / "003.png"
    base_z = np.full((160, 160, 3), 240, dtype=np.uint8)
    cv2.rectangle(base_z, (30, 30), (130, 130), (10, 10, 10), 2)
    for pz in (pz1, pz2, pz3):
        Image.fromarray(base_z).save(pz)
        pz_paths = resolve_page_paths(pz)
        save_rgb_image_atomic(base_z, pz_paths.inpainted_path)
        save_rgb_image_atomic(base_z, pz_paths.rendered_output_path)

    pz1_paths = resolve_page_paths(pz1)
    pz1_paths.json_path.parent.mkdir(parents=True, exist_ok=True)
    pz1_paths.json_path.write_text(
        json.dumps({str(pz1.resolve()): {"mask_raw": [[]], "regions": []}}),
        encoding="utf-8",
    )
    pz2_paths = resolve_page_paths(pz2)
    pz2_paths.json_path.write_text(
        json.dumps({str(pz2.resolve()): {"mask_raw": [[0] * 160 for _ in range(160)], "regions": []}}),
        encoding="utf-8",
    )
    pz3_paths = resolve_page_paths(pz3)
    save_page_json_atomic(
        pz3_paths,
        str(pz3.resolve()),
        {
            "regions": [
                {
                    "lines": [[[45.0, 45.0], [115.0, 45.0], [115.0, 115.0], [45.0, 115.0]]],
                    "center": [80.0, 80.0],
                    "text": "よし",
                    "translation": "好",
                    "font_family": "FZHei-B01",
                    "font_size": 20,
                    "direction": "v",
                    "alignment": "left",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    vc_zmask_dir = tmp_path / "vc_zero_dim_mask"
    mf_zmask = aac.cmd_verify_crops(ch_zmask, vc_zmask_dir)
    assert (vc_zmask_dir / "verify_crops_manifest.json").is_file()
    assert mf_zmask["missing_artifact_pages"] == [
        {"page": "001.png", "page_number": 1, "reason": "corrupt_mask"}
    ]
    assert {"page": "002.png", "page_number": 2, "reason": "no_visible_regions"} in mf_zmask["skipped_pages"]
    assert mf_zmask["exported_pages"] == ["003.png"]

    # 4. R4-F17: Check ALL exact normalized paths before filename fallback; require unique filename on fallback.
    ch_f17 = tmp_path / "ch_f17"
    before_f17 = tmp_path / "before_f17"
    out_f17_store = tmp_path / "out_f17_store"
    ch_f17.mkdir()
    before_f17.mkdir()
    out_f17_store.mkdir()

    raw_f17 = np.full((80, 80, 3), (210, 210, 210), dtype=np.uint8)
    bef_f17 = np.full((80, 80, 3), (40, 185, 65), dtype=np.uint8)
    old_aft_rgb = np.full((80, 80, 3), (220, 35, 35), dtype=np.uint8)
    cur_aft_rgb = np.full((80, 80, 3), (35, 75, 225), dtype=np.uint8)
    Image.fromarray(raw_f17).save(ch_f17 / "001.png")
    Image.fromarray(bef_f17).save(before_f17 / "001.png")
    old_aft_p = out_f17_store / "old_after.png"
    cur_aft_p = out_f17_store / "cur_after.png"
    Image.fromarray(old_aft_rgb).save(old_aft_p)
    Image.fromarray(cur_aft_rgb).save(cur_aft_p)

    ch_f17_json = ch_f17 / "ch_f17_translations.json"
    ch_f17_json.write_text(
        json.dumps(
            {
                str((tmp_path / "old_chapter" / "001.png").resolve()): {"last_output_path": str(old_aft_p.resolve())},
                str((ch_f17 / "001.png").resolve()): {"last_output_path": str(cur_aft_p.resolve())},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cmp_f17_dir = tmp_path / "cmp_f17_exact_wins"
    res_f17_exact = aac.cmd_compare(raw_dir=ch_f17, before_dir=before_f17, after_dir=None, out_dir=cmp_f17_dir)
    assert res_f17_exact["complete_coverage"] is True
    assert res_f17_exact["sheets_generated"] == 1
    f17_sheet = np.asarray(Image.open(cmp_f17_dir / "cmp_p01_001.png").convert("RGB"))
    assert np.allclose(f17_sheet[96, 246], cur_aft_rgb[0, 0], atol=3)

    # Ambiguous duplicate non-matching filenames in chapter JSON must NOT match
    ch_f17_json.write_text(
        json.dumps(
            {
                str((tmp_path / "old_a" / "001.png").resolve()): {"last_output_path": str(old_aft_p.resolve())},
                str((tmp_path / "old_b" / "001.png").resolve()): {"last_output_path": str(cur_aft_p.resolve())},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_f17_ambig = aac.cmd_compare(
        raw_dir=ch_f17,
        before_dir=before_f17,
        after_dir=None,
        out_dir=tmp_path / "cmp_f17_ambig",
    )
    assert res_f17_ambig["missing_after_pages"] == ["001.png"]
    assert res_f17_ambig["complete_coverage"] is False

    # Single migrated entry with unique filename still falls back cleanly
    ch_f17_json.write_text(
        json.dumps(
            {
                str((tmp_path / "migrated_prev" / "001.png").resolve()): {
                    "last_output_path": str(cur_aft_p.resolve())
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cmp_f17_mig_dir = tmp_path / "cmp_f17_migrated"
    res_f17_mig = aac.cmd_compare(raw_dir=ch_f17, before_dir=before_f17, after_dir=None, out_dir=cmp_f17_mig_dir)
    assert res_f17_mig["complete_coverage"] is True
    assert res_f17_mig["sheets_generated"] == 1
    f17_mig_sheet = np.asarray(Image.open(cmp_f17_mig_dir / "cmp_p01_001.png").convert("RGB"))
    assert np.allclose(f17_mig_sheet[96, 246], cur_aft_rgb[0, 0], atol=3)

    # 5. R4-F18: Single-entry chapter JSON recording `002.png` must NEVER be treated as `001.png`'s metadata.
    ch_f18 = tmp_path / "ch_f18"
    before_f18 = tmp_path / "before_f18"
    ch_f18.mkdir()
    before_f18.mkdir()
    for fn in ("001.png", "002.png"):
        Image.fromarray(raw_f17).save(ch_f18 / fn)
        Image.fromarray(bef_f17).save(before_f18 / fn)
    p2_only_after = out_f17_store / "p2_only_after.png"
    Image.fromarray(cur_aft_rgb).save(p2_only_after)
    (ch_f18 / "ch_f18_translations.json").write_text(
        json.dumps(
            {str((ch_f18 / "002.png").resolve()): {"last_output_path": str(p2_only_after.resolve())}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_f18 = aac.cmd_compare(
        raw_dir=ch_f18,
        before_dir=before_f18,
        after_dir=None,
        out_dir=tmp_path / "cmp_f18",
    )
    assert res_f18["compared_page_count"] == 1
    assert res_f18["missing_after_pages"] == ["001.png"]
    assert res_f18["complete_coverage"] is False

    # 6. R4-F46: Legacy single-page `<stem>_translations.json` in source directory is searched while preserving
    #    exact page identity when duplicate stems (`001.png` + `001.jpg`) exist.
    ch_f46 = tmp_path / "ch_f46"
    before_f46 = tmp_path / "before_f46"
    ch_f46.mkdir()
    before_f46.mkdir()
    Image.fromarray(raw_f17).save(ch_f46 / "001.png")
    Image.fromarray(raw_f17).save(ch_f46 / "001.jpg", quality=98)
    Image.fromarray(bef_f17).save(before_f46 / "001.png")
    Image.fromarray(bef_f17).save(before_f46 / "001.jpg", quality=98)
    legacy_png_after = out_f17_store / "legacy_001_png_after.webp"
    Image.fromarray(cur_aft_rgb).save(legacy_png_after, lossless=True)
    (ch_f46 / "001_translations.json").write_text(
        json.dumps(
            {str((ch_f46 / "001.png").resolve()): {"last_output_path": str(legacy_png_after.resolve())}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cmp_f46_dir = tmp_path / "cmp_f46_legacy"
    res_f46 = aac.cmd_compare(raw_dir=ch_f46, before_dir=before_f46, after_dir=None, out_dir=cmp_f46_dir)
    assert res_f46["expected_page_count"] == 2
    assert res_f46["compared_page_count"] == 1
    assert res_f46["sheets_generated"] == 1
    assert res_f46["missing_after_pages"] == ["001.jpg"]
    assert res_f46["complete_coverage"] is False
    f46_sheet = np.asarray(Image.open(cmp_f46_dir / "cmp_p02_001.png").convert("RGB"))
    assert np.allclose(f46_sheet[96, 246], cur_aft_rgb[0, 0], atol=3)

    # 7. R4-F19: Second-hop `target_name_cf_unique` in `raw_dir=None` mode is computed from `chapter_cand_files`.
    ch_f19 = tmp_path / "ch_f19"
    res_f19 = ch_f19 / "manga_translator_work" / "result"
    sib_f19 = tmp_path / "ch_f19-translated"
    before_f19 = tmp_path / "before_f19"
    res_f19.mkdir(parents=True)
    sib_f19.mkdir()
    before_f19.mkdir()
    Image.fromarray(raw_f17).save(ch_f19 / "001.PNG")
    Image.fromarray(bef_f17).save(before_f19 / "001.PNG")
    Image.fromarray(cur_aft_rgb).save(sib_f19 / "001.png")

    real_list_dir_images = aac._list_dir_images

    def fake_case_sensitive_chapter_list(d: Path, *, exclude_translated_suffix: bool = False) -> list[Path]:
        if os.path.normcase(os.path.abspath(str(d))) == os.path.normcase(os.path.abspath(str(ch_f19))):
            # Simulate case-sensitive chapter directory containing BOTH `001.PNG` and `001.png`
            return [ch_f19 / "001.PNG", ch_f19 / "001.png"]
        return real_list_dir_images(d, exclude_translated_suffix=exclude_translated_suffix)

    with monkeypatch.context() as mp_f19:
        mp_f19.setattr(aac, "_list_dir_images", fake_case_sensitive_chapter_list)
        res_f19_ambig = aac.cmd_compare(
            raw_dir=None,
            before_dir=before_f19,
            after_dir=res_f19,
            out_dir=tmp_path / "cmp_f19_ambig",
        )
    assert res_f19_ambig["missing_after_pages"] == ["001.PNG"]
    assert res_f19_ambig["complete_coverage"] is False

    # When `<chapter>-translated` has exact-case `001.PNG`, exact filename match still pairs cleanly
    (sib_f19 / "001.png").unlink()
    Image.fromarray(cur_aft_rgb).save(sib_f19 / "001.PNG")
    with monkeypatch.context() as mp_f19_exact:
        mp_f19_exact.setattr(aac, "_list_dir_images", fake_case_sensitive_chapter_list)
        res_f19_exact = aac.cmd_compare(
            raw_dir=None,
            before_dir=before_f19,
            after_dir=res_f19,
            out_dir=tmp_path / "cmp_f19_exact",
        )
    assert res_f19_exact["missing_after_pages"] == []
    assert res_f19_exact["complete_coverage"] is True

    # When `ch_f19` only has unique `001.PNG`, case-folded fallback to `sib_f19 / "001.png"` remains active
    (sib_f19 / "001.PNG").unlink()
    Image.fromarray(cur_aft_rgb).save(sib_f19 / "001.png")
    res_f19_unique_cf = aac.cmd_compare(
        raw_dir=None,
        before_dir=before_f19,
        after_dir=res_f19,
        out_dir=tmp_path / "cmp_f19_unique_cf",
    )
    assert res_f19_unique_cf["missing_after_pages"] == []
    assert res_f19_unique_cf["complete_coverage"] is True
