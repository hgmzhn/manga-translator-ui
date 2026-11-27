"""Regression tests for V17/V14 7 visual QA failures (`图1` ~ `图7`) + mandatory `verify-crops` self-check sheet."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mtui_mcp.local_inpaint import apply_mask_operations
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, save_page_json_atomic, save_rgb_image_atomic
from mtui_mcp.qa_auditor import audit_single_page


def test_fig1_white_bg_bubble_border_shield_and_erosion_detector(tmp_path: Path) -> None:
    folder = tmp_path / "fig1_ch"
    folder.mkdir()
    img_path = folder / "p01.png"

    page = np.full((500, 400, 3), 255, dtype=np.uint8)
    cv2.ellipse(page, (200, 250), (90, 130), 0, 0, 360, (12, 12, 12), 2, cv2.LINE_AA)
    for cy in (190, 240, 290):
        cv2.rectangle(page, (175, cy - 12), (205, cy + 12), (10, 10, 10), 4)
        cv2.circle(page, (218, cy), 3, (10, 10, 10), -1)

    new_mask, _, _ = apply_mask_operations(
        None,
        page,
        [],
        add_rects=[[145, 100, 255, 400]],
    )
    assert new_mask[120, 200] == 0, "Top curved bubble border must be protected by border_shield!"
    assert new_mask[380, 200] == 0, "Bottom curved bubble border must be protected by border_shield!"
    assert new_mask[240, 218] == 255, "Interior emphasis dot must still be masked!"

    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    bad_mask = np.zeros((500, 400), dtype=np.uint8)
    bad_mask[110:390, 150:250] = 255
    inp = page.copy()
    inp[bad_mask > 0] = 255
    save_rgb_image_atomic(inp, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "mask_raw": encode_mask_base64(bad_mask),
            "regions": [
                {
                    "lines": [[[165.0, 170.0], [230.0, 170.0], [230.0, 320.0], [165.0, 320.0]]],
                    "text": "いんやみんなや",
                    "translation": "不[BR]是所有人",
                    "font_family": "FZCuSong-B09",
                    "font_size": 28,
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    rep = audit_single_page(resolve_page_paths(img_path), check_font_glyphs=False)
    eroded = [i for i in rep["issues"] if i["category"] == "bubble_border_eroded"]
    assert len(eroded) >= 1 and eroded[0]["severity"] == "error"


def test_fig2_and_fig6_interior_valley_and_rendered_overlap(tmp_path: Path) -> None:
    folder = tmp_path / "fig2_6_ch"
    folder.mkdir()
    img_path = folder / "p01.png"
    page = np.full((700, 500, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)

    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[300.0, 100.0], [460.0, 100.0], [460.0, 320.0], [300.0, 320.0]]],
                    "text": "えっとっ……もし良かったら…次も会えたら今度は『下層』まで行かない？",
                    "translation": "那个︙[BR]如果方便的话︙[BR]下次还能见面的话[BR]这回[BR]要不要去﹃下层﹄？",
                    "font_family": "FZZhunYuan-M02",
                    "font_size": 26,
                    "direction": "v",
                },
                {
                    "lines": [[[125.0, 90.0], [238.0, 90.0], [238.0, 352.0], [125.0, 352.0]]],
                    "center": [181.5, 221.0],
                    "text": "強くなるだけで第一級冒険者の伴侶になれるんだ！",
                    "translation": "只要变强就能成为[BR]第一级冒险者的伴侣！",
                    "font_family": "FZLanTingYuan-B-GBK",
                    "font_size": 27,
                    "direction": "v",
                },
                {
                    "lines": [[[69.0, 343.0], [185.0, 343.0], [185.0, 526.0], [69.0, 526.0]]],
                    "center": [122.5, 429.4],
                    "text": "競争率が半端ない雄の奥さんに!!",
                    "translation": "当上[BR]竞争率超高的雄性的[BR]老婆!!",
                    "font_family": "FZLanTingYuan-B-GBK",
                    "font_size": 26,
                    "direction": "v",
                },
                {
                    "lines": [[[280.0, 360.0], [380.0, 360.0], [380.0, 560.0], [280.0, 560.0]]],
                    "text": "ああああああああああああ",
                    "translation": "那个人[BR]其实早就已经知道一切[BR]了",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                },
                {
                    "lines": [[[390.0, 360.0], [485.0, 360.0], [485.0, 540.0], [390.0, 540.0]]],
                    "text": "競争率が半端ない雄の奥さんに!!",
                    "translation": "当上竞争率超高[BR]的雄性的[BR]老婆!!",
                    "font_family": "FZLanTingYuan-B-GBK",
                    "font_size": 26,
                    "direction": "v",
                },
                {
                    "lines": [[[40.0, 560.0], [160.0, 560.0], [160.0, 690.0], [40.0, 690.0]]],
                    "text": "競争率が半端ない雄の奥さんに!!",
                    "translation": "当上竞争率[BR]超高的雄性的[BR]老婆!!",
                    "font_family": "FZLanTingYuan-B-GBK",
                    "font_size": 26,
                    "direction": "v",
                },
                {
                    "lines": [[[200.0, 560.0], [340.0, 560.0], [340.0, 690.0], [200.0, 690.0]]],
                    "text": "神様に嘘の懺悔は無駄やっちゅーに",
                    "translation": "都说了跟神明[BR]编假话认罪[BR]是没有用的嘛",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                },
            ]
        },
        auto_initial_snapshot=False,
    )
    rep = audit_single_page(resolve_page_paths(img_path), check_inpaint_cv=False, check_font_glyphs=False)
    cats_by_r = {(i["region_index"], i["category"]): i["severity"] for i in rep["issues"]}
    assert cats_by_r.get((0, "unbalanced_linebreak_columns")) == "error"
    assert cats_by_r.get((1, "unbalanced_linebreak_columns")) == "warning"
    assert cats_by_r.get((2, "unbalanced_linebreak_columns")) == "error"
    assert cats_by_r.get((3, "unbalanced_linebreak_columns")) == "error"
    assert cats_by_r.get((4, "linebreak_broken_breath")) == "warning"
    assert (5, "unbalanced_linebreak_columns") not in cats_by_r
    assert (5, "linebreak_broken_breath") not in cats_by_r
    assert (6, "unbalanced_linebreak_columns") not in cats_by_r
    assert cats_by_r.get((1, "rendered_regions_overlap")) == "error"


def test_fig3_fig4_fig5_fig7_font_bold_residual_and_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    folder = tmp_path / "fig3_4_5_7_ch"
    folder.mkdir()
    img_path = folder / "p01.png"

    page = np.full((960, 960, 3), 255, dtype=np.uint8)
    cv2.rectangle(page, (30, 30), (930, 500), (0, 0, 0), 3)
    cv2.ellipse(page, (730, 265), (110, 180), 0, 0, 360, (10, 10, 10), 2)
    cv2.ellipse(page, (200, 265), (110, 180), 0, 0, 360, (10, 10, 10), 2)
    for bx in (710, 755, 180, 225):
        for by in (165, 225, 285, 345):
            cv2.rectangle(page, (bx - 16, by - 16), (bx + 16, by + 16), (8, 8, 8), 7)

    cv2.rectangle(page, (40, 620), (500, 740), (0, 0, 0), 2)
    cv2.rectangle(page, (85, 676), (140, 683), (10, 10, 10), -1)
    cv2.rectangle(page, (155, 662), (435, 698), (10, 10, 10), 6)

    cv2.ellipse(page, (620, 710), (86, 146), 0, 0, 360, (10, 10, 10), 2)
    for by in (638, 708, 778):
        cv2.rectangle(page, (592, by - 24), (648, by + 24), (8, 8, 8), 8)

    cv2.ellipse(page, (835, 710), (88, 118), 0, 0, 360, (10, 10, 10), 2)
    for by in (675, 705, 735):
        cv2.rectangle(page, (827, by - 7), (843, by + 7), (12, 12, 12), 2)

    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    mask = np.zeros((960, 960), dtype=np.uint8)
    mask[135:380, 680:785] = 255
    mask[135:380, 150:255] = 255
    mask[655:705, 150:445] = 255
    mask[605:810, 582:658] = 255
    mask[660:750, 820:850] = 255
    inp = page.copy()
    inp[mask > 0] = 255
    save_rgb_image_atomic(inp, paths.inpainted_path)
    save_rgb_image_atomic(inp, paths.rendered_output_path)

    regs = [
        {
            "lines": [[[685.0, 145.0], [780.0, 145.0], [780.0, 370.0], [685.0, 370.0]]],
            "text": "それじゃあ私が雑魚じゃなくなったら",
            "translation": "那么[BR]如果我不再是[BR]杂鱼的话",
            "font_family": "FZLanTingYuan-B-GBK",
            "font_size": 38,
            "direction": "v",
        },
        {
            "lines": [[[155.0, 145.0], [250.0, 145.0], [250.0, 370.0], [155.0, 370.0]]],
            "text": "ベート・ローガの隣にいていいってこと!?",
            "translation": "也就是说[BR]我可以待在[BR]伯特身边!?",
            "font_family": "FZHei-B01::Regular",
            "font_size": 38,
            "direction": "v",
        },
        {
            "lines": [[[80.0, 658.0], [445.0, 658.0], [445.0, 702.0], [80.0, 702.0]]],
            "text": "――吠え……やがった",
            "translation": "——竟敢……冲我吠",
            "font_family": "FZCuSong-B09",
            "font_size": 36,
            "direction": "h",
        },
        {
            "lines": [[[588.0, 612.0], [652.0, 612.0], [652.0, 804.0], [588.0, 804.0]]],
            "text": "ていっ",
            "translation": "喝！",
            "font_family": "FZLanTingYuan-B-GBK",
            "font_size": 32,
            "direction": "v",
        },
        {
            "lines": [[[822.0, 665.0], [848.0, 665.0], [848.0, 748.0], [822.0, 748.0]]],
            "text": "…………は？",
            "translation": "︙︙哈？",
            "font_family": "FZYaSong-DB-GBK",
            "font_size": 36,
            "direction": "v",
        },
    ]
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {"mask_raw": encode_mask_base64(mask), "regions": regs},
        auto_initial_snapshot=False,
    )

    ym_cache = {
        "by_ridx": {
            "0": {
                "pred_family": "ROUND_GOTHIC",
                "pred_conf": 0.99,
                "bold_prob": 1.0,
                "heavy_prob": 0.2,
                "family_probs": {"ROUND_GOTHIC": 0.99, "ANTIQUE": 0.0},
                "top_fonts": [{"font": "FOT-RodinHappyPro-B.otf", "family": "ROUND_GOTHIC", "prob": 0.92}],
            },
            "1": {
                "pred_family": "ROUND_GOTHIC",
                "pred_conf": 0.51,
                "bold_prob": 0.97,
                "heavy_prob": 0.3,
                "family_probs": {"ROUND_GOTHIC": 0.51, "POP_CHISEL": 0.48, "ANTIQUE": 0.0},
                "top_fonts": [{"font": "FOT-RodinHappyPro-B.otf", "family": "ROUND_GOTHIC", "prob": 0.51}],
            },
        }
    }
    rep = audit_single_page(resolve_page_paths(img_path), yuzumarker_page_cache=ym_cache, check_font_glyphs=False)
    by_r_cat = {(i["region_index"], i["category"]): i["severity"] for i in rep["issues"]}
    assert by_r_cat.get((1, "yuzumarker_font_mismatch")) == "error"
    assert by_r_cat.get((1, "raw_font_consistency_suspect")) == "warning"
    assert by_r_cat.get((2, "inpaint_residual_ink")) == "error"
    assert by_r_cat.get((3, "bubble_underfilled_font")) == "error"
    assert by_r_cat.get((3, "raw_font_size_mismatch")) == "error"
    assert (4, "raw_font_size_mismatch") not in by_r_cat
    assert (4, "bubble_underfilled_font") not in by_r_cat

    regs[1]["font_family"] = "FZJianZhi-M23"
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {"mask_raw": encode_mask_base64(mask), "regions": regs},
        auto_initial_snapshot=False,
    )
    ym_true_switch = {
        "by_ridx": {
            "0": ym_cache["by_ridx"]["0"],
            "1": {
                "pred_family": "POP_CHISEL",
                "pred_conf": 0.94,
                "bold_prob": 1.0,
                "heavy_prob": 0.4,
                "family_probs": {"POP_CHISEL": 0.94, "ROUND_GOTHIC": 0.05},
                "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.86}],
            },
        }
    }
    rep_sw = audit_single_page(resolve_page_paths(img_path), yuzumarker_page_cache=ym_true_switch, check_font_glyphs=False)
    assert not any(i["category"] == "raw_font_consistency_suspect" for i in rep_sw["issues"])

    page[850:950, 100:320] = 175
    for gx in range(105, 315, 6):
        cv2.line(page, (gx, 855), (gx, 945), (95, 95, 95), 1)
    Image.fromarray(page).save(img_path)
    inp[850:950, 100:320] = 175
    mask[860:940, 120:300] = 255
    save_rgb_image_atomic(inp, paths.inpainted_path)
    save_rgb_image_atomic(inp, paths.rendered_output_path)
    regs.append(
        {
            "lines": [[[140.0, 868.0], [210.0, 868.0], [210.0, 932.0], [140.0, 932.0]]],
            "text": "ぐぬぬ",
            "translation": "可恶︙",
            "font_family": "FZHei-B01",
            "font_size": 24,
            "stroke_width": 0.25,
            "direction": "v",
        }
    )
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {"mask_raw": encode_mask_base64(mask), "regions": regs},
        auto_initial_snapshot=False,
    )
    rep_fl = audit_single_page(resolve_page_paths(img_path), yuzumarker_page_cache=ym_true_switch, check_font_glyphs=False)
    assert any(
        i["region_index"] == 5 and i["category"] in ("frameless_artwork_scar", "frameless_overmasked_artwork")
        for i in rep_fl["issues"]
    )

    script_path = (
        _bootstrap.ROOT
        / "mtui_mcp"
        / "skills"
        / "mtui-manga-proofreading"
        / "cli"
        / "audit_and_compare.py"
    )
    spec = importlib.util.spec_from_file_location("audit_and_compare", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    vc_out = tmp_path / "verify_out"
    manifest = mod.cmd_verify_crops(folder, vc_out)
    assert manifest["total_bubbles_exported"] == 6
    assert manifest["frameless_regions_exported"] >= 1
    assert manifest["verify_sheet_count"] == 2
    assert Path(manifest["sheets"][0]).is_file()
