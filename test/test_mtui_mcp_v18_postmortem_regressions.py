"""Regression tests for Sword-Oratoria V18/V19 postmortem font-taxonomy & over-bolding hardening."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image

import mtui_mcp.qa_auditor as qa_mod
import mtui_mcp.worker.client as worker_client_mod
from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_detector import (
    CACHE_VERSION,
    classify_current_mtui_font,
    classify_vcb_font_entry,
    load_font_detection_cache,
    recommend_mtui_font_for_prediction,
    region_bbox_signature,
)
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    _pick_punctuation_fallback_font,
    region_bbox_xyxy,
    region_center,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from mtui_mcp.qa_auditor import _detect_structural_and_border_cv_issues, diff_qa_audits, run_qa_audit


def _load_audit_cli_module(monkeypatch: pytest.MonkeyPatch | None = None):
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
    spec = importlib.util.spec_from_file_location("audit_and_compare_v18_postmortem", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _draw_enclosed_bubble_with_strokes(
    page: np.ndarray,
    center: tuple[int, int],
    axes: tuple[int, int],
    *,
    stroke_thickness: int,
    glyph_count: int = 3,
    glyph_box_half: int = 13,
) -> tuple[list[list[list[float]]], list[float]]:
    cx, cy = center
    rx, ry = axes
    cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (10, 10, 10), 3)

    step_y = max(glyph_box_half * 2 + 8, int((ry * 1.15) / max(glyph_count, 1)))
    start_y = cy - int(((glyph_count - 1) * step_y) / 2)
    x1, x2 = cx - glyph_box_half - 6, cx + glyph_box_half + 6
    y1 = start_y - glyph_box_half - 6
    y2 = start_y + (glyph_count - 1) * step_y + glyph_box_half + 6
    for k in range(glyph_count):
        gy = start_y + k * step_y
        cv2.rectangle(
            page,
            (cx - glyph_box_half, gy - glyph_box_half),
            (cx + glyph_box_half, gy + glyph_box_half),
            (10, 10, 10),
            stroke_thickness,
        )
        cv2.line(
            page,
            (cx - glyph_box_half, gy),
            (cx + glyph_box_half, gy),
            (10, 10, 10),
            stroke_thickness,
        )
    lines = [[[float(x1), float(y1)], [float(x2), float(y1)], [float(x2), float(y2)], [float(x1), float(y2)]]]
    return lines, [float(x1), float(y1), float(x2), float(y2)]


def test_kyokaica_hitujyun_taxonomy_v1_v2_and_antique_bold_consistency() -> None:
    for fname, exp_wt in (
        ("A-OTF-KyokaICAPro-Medium.otf", "regular"),
        ("A-OTF-KyokaICAPro-Regular.otf", "regular"),
        ("G-OTF-GKyokaICAPro-Light.otf", "light"),
        ("G-OTF-GJKyokaICAProN-Med.otf", "regular"),
        ("G-OTF-GJKyokaICAProN-Reg.otf", "regular"),
        ("G-OTF-GHitujyun2Std-ICAM12.otf", "regular"),
        ("G-OTF-GHitujyunStd-ICAR12.otf", "regular"),
        ("G-OTF-GHitujyoStdN-ICAM1.otf", "regular"),
        ("G-OTF-GHitu2jyoStdN-ICAR3.otf", "regular"),
    ):
        assert classify_vcb_font_entry({"path": fname, "serif": False}) == ("KYOKASHO_PEN", exp_wt)

    pred_v2 = {
        "pred_family": "KYOKASHO_PEN",
        "pred_conf": 0.99,
        "bold_prob": 0.0,
        "heavy_prob": 0.0,
        "top_fonts": [
            {"font": "G-OTF-GKyokaICAPro-Medium.otf", "family": "KYOKASHO_PEN", "prob": 0.37},
            {"font": "G-OTF-GHitujyun2Std-ICAM12.otf", "family": "KYOKASHO_PEN", "prob": 0.28},
            {"font": "A-OTF-KyokaICAPro-Medium.otf", "family": "KYOKASHO_PEN", "prob": 0.24},
            {"font": "G-OTF-GJKyokaICAProN-Med.otf", "family": "KYOKASHO_PEN", "prob": 0.09},
        ],
    }
    assert recommend_mtui_font_for_prediction(pred_v2) == (
        "LXGW WenKai::Medium",
        False,
        {"KYOKASHO_PEN", "KAISHO_BRUSH"},
    )

    pred_v1_round = {**pred_v2, "pred_family": "ROUND_GOTHIC", "pred_conf": 0.6815}
    assert recommend_mtui_font_for_prediction(pred_v1_round) == (
        "LXGW WenKai::Medium",
        False,
        {"KYOKASHO_PEN", "KAISHO_BRUSH"},
    )
    pred_v1_hitujyun = {
        "pred_family": "KAKU_GOTHIC",
        "pred_conf": 0.55,
        "bold_prob": 0.0,
        "heavy_prob": 0.0,
        "top_fonts": [
            {"font": "G-OTF-GHitujyun2Std-ICAM12.otf", "family": "KAKU_GOTHIC", "prob": 0.52},
            {"font": "A-OTF-KyokaICAPro-Medium.otf", "family": "ROUND_GOTHIC", "prob": 0.41},
        ],
    }
    assert recommend_mtui_font_for_prediction(pred_v1_hitujyun) == (
        "LXGW WenKai::Medium",
        False,
        {"KYOKASHO_PEN", "KAISHO_BRUSH"},
    )

    for thin_font in (
        "A-OTF-KyokaICAPro-Regular.otf",
        "G-OTF-GJKyokaICAProN-Reg.otf",
        "G-OTF-GKyokaICAPro-Light.otf",
        "G-OTF-GHitujyunStd-ICAR12.otf",
    ):
        rec_ff, rec_b, acc = recommend_mtui_font_for_prediction(
            {
                "pred_family": "KYOKASHO_PEN",
                "pred_conf": 0.96,
                "bold_prob": 0.0,
                "heavy_prob": 0.0,
                "top_fonts": [{"font": thin_font, "family": "KYOKASHO_PEN", "prob": 0.92}],
            }
        )
        assert (rec_ff, rec_b) == ("LXGW WenKai", False)
        assert acc == {"KYOKASHO_PEN", "KAISHO_BRUSH"}

    assert recommend_mtui_font_for_prediction(
        {
            "pred_family": "KYOKASHO_PEN",
            "pred_conf": 0.95,
            "bold_prob": 0.90,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-ComicMysteryStd-DB.otf", "family": "KYOKASHO_PEN", "prob": 0.85}],
        }
    ) == ("LXGW WenKai::Medium", True, {"KYOKASHO_PEN", "KAISHO_BRUSH"})
    assert recommend_mtui_font_for_prediction(
        {
            "pred_family": "KYOKASHO_PEN",
            "pred_conf": 0.95,
            "bold_prob": 0.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "A-OTF-TakeStd-Light.otf", "family": "KYOKASHO_PEN", "prob": 0.88}],
        }
    ) == ("Xiaolai", False, {"KYOKASHO_PEN"})
    assert recommend_mtui_font_for_prediction(
        {
            "pred_family": "ROUND_GOTHIC",
            "pred_conf": 0.99,
            "bold_prob": 0.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-SeuratPro-M.otf", "family": "ROUND_GOTHIC", "prob": 0.94}],
        }
    ) == ("FZZhunYuan-M02", False, {"ROUND_GOTHIC", "KAKU_GOTHIC"})

    for ant_font in ("FOT-DNPShueiAntiStd-B.otf", "FOT-TsukuAntiqueLGoStd-B.otf", "AP-OTF-ShueiAntiStdN-B.otf"):
        rec_ff, rec_b, _ = recommend_mtui_font_for_prediction(
            {
                "pred_family": "ANTIQUE",
                "pred_conf": 0.99,
                "bold_prob": 0.99,
                "heavy_prob": 0.0,
                "top_fonts": [{"font": ant_font, "family": "ANTIQUE", "prob": 0.95}],
            }
        )
        assert (rec_ff, rec_b) == ("FZHei-B01", False)


def test_v19_028_kyokaica_uddigi_contamination_and_noisy_kaisho_anti_fp() -> None:
    base_rgb = np.full((360, 420, 3), 255, dtype=np.uint8)
    inp_rgb = np.full((360, 420, 3), 255, dtype=np.uint8)

    reg_v19_028 = {
        "text": "あ…あの……今の事態に闇派閥の残党達が関わってるって…",
        "translation": "那︙那个︙︙[BR]现在的事态[BR]和暗派阀残党有关︙",
        "font_family": "FZHei-B01",
        "bold": False,
        "font_size": 26,
        "direction": "v",
        "lines": [[[30.0, 40.0], [140.0, 40.0], [140.0, 260.0], [30.0, 260.0]]],
    }
    reg_v19_021 = {
        "text": "それじゃあ始めようか",
        "translation": "那就[BR]开始吧",
        "font_family": "FZHei-B01",
        "bold": False,
        "font_size": 28,
        "direction": "v",
        "lines": [[[170.0, 40.0], [260.0, 40.0], [260.0, 220.0], [170.0, 220.0]]],
    }
    reg_v19_089 = {
        "text": "気をつけろよ",
        "translation": "小心点啊",
        "font_family": "FZHei-B01",
        "bold": False,
        "font_size": 28,
        "direction": "v",
        "lines": [[[290.0, 40.0], [380.0, 40.0], [380.0, 220.0], [290.0, 220.0]]],
    }
    ym_cache = {
        "by_ridx": {
            "0": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(reg_v19_028)),
                "center": list(region_center(reg_v19_028)),
                "pred_family": "KYOKASHO_PEN",
                "pred_conf": 0.89,
                "bold_prob": 0.0,
                "heavy_prob": 0.0,
                "family_probs": {"KYOKASHO_PEN": 0.89, "ANTIQUE": 0.08},
                "top_fonts": [
                    {"font": "A-OTF-KyokaICAPro-Medium.otf", "family": "KYOKASHO_PEN", "prob": 0.40},
                    {"font": "G-OTF-GKyokaICAPro-Medium.otf", "family": "KYOKASHO_PEN", "prob": 0.29},
                    {"font": "UDDigiKyokashoStdN-Medium.otf", "family": "KYOKASHO_PEN", "prob": 0.19},
                ],
            },
            "1": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(reg_v19_021)),
                "center": list(region_center(reg_v19_021)),
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.98,
                "bold_prob": 0.98,
                "heavy_prob": 0.0,
                "family_probs": {"KAISHO_BRUSH": 0.98, "ANTIQUE": 0.01},
                "top_fonts": [{"font": "HOT-FKaishoStd-B.otf", "family": "KAISHO_BRUSH", "prob": 0.98}],
            },
            "2": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(reg_v19_089)),
                "center": list(region_center(reg_v19_089)),
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.85,
                "bold_prob": 0.85,
                "heavy_prob": 0.0,
                "family_probs": {"KAISHO_BRUSH": 0.85, "ANTIQUE": 0.05},
                "top_fonts": [{"font": "DFSoKaiSho-W7.otf", "family": "KAISHO_BRUSH", "prob": 0.52}],
            },
        }
    }

    _, _, acc_028 = recommend_mtui_font_for_prediction(ym_cache["by_ridx"]["0"])
    assert "ANTIQUE" in acc_028
    ff_021, b_021, acc_021 = recommend_mtui_font_for_prediction(ym_cache["by_ridx"]["1"])
    assert (ff_021, b_021) == ("FZHei-B01", False)
    assert {"ANTIQUE", "KAISHO_BRUSH"}.issubset(acc_021)

    issues = _detect_structural_and_border_cv_issues(
        base_rgb, inp_rgb, None, [reg_v19_028, reg_v19_021, reg_v19_089], yuzumarker_page_cache=ym_cache
    )
    assert not any(i["category"] in ("yuzumarker_font_mismatch", "yuzumarker_bold_mismatch") for i in issues)


def test_kaisho_brush_unbolded_clean_and_bolded_is_warning_not_error() -> None:
    page = np.full((520, 760, 3), 220, dtype=np.uint8)
    l_ref, _ = _draw_enclosed_bubble_with_strokes(page, (130, 260), (85, 165), stroke_thickness=2, glyph_count=3)
    l_k1, _ = _draw_enclosed_bubble_with_strokes(page, (340, 260), (90, 165), stroke_thickness=7, glyph_count=3)
    l_k2, _ = _draw_enclosed_bubble_with_strokes(page, (560, 260), (90, 165), stroke_thickness=4, glyph_count=3)

    inp = page.copy()
    cv2.ellipse(inp, (130, 260), (74, 152), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(inp, (340, 260), (78, 152), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(inp, (560, 260), (78, 152), 0, 0, 360, (255, 255, 255), -1)

    regs_unbold = [
        {"text": "普通の台詞だ", "translation": "普通[BR]对白", "font_family": "FZHei-B01", "bold": False, "font_size": 28, "direction": "v", "lines": l_ref},
        {"text": "挨拶は要らねぇ", "translation": "用不着[BR]打招呼", "font_family": "FZBeiWeiKaiShu-S19", "bold": False, "font_size": 34, "direction": "v", "lines": l_k1},
        {"text": "てめぇ……", "translation": "你这家伙︙", "font_family": "FZBeiWeiKaiShu-S19", "bold": False, "font_size": 32, "direction": "v", "lines": l_k2},
    ]
    ym_cache = {
        "by_ridx": {
            "0": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(regs_unbold[0])),
                "center": list(region_center(regs_unbold[0])),
                "pred_family": "ANTIQUE",
                "pred_conf": 0.95,
                "bold_prob": 0.90,
                "heavy_prob": 0.0,
                "family_probs": {"ANTIQUE": 0.95},
                "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.90}],
            },
            "1": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(regs_unbold[1])),
                "center": list(region_center(regs_unbold[1])),
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.98,
                "bold_prob": 1.0,
                "heavy_prob": 0.25,
                "family_probs": {"KAISHO_BRUSH": 0.98, "ANTIQUE": 0.0},
                "top_fonts": [{"font": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "family": "KAISHO_BRUSH", "prob": 0.95}],
            },
            "2": {
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(regs_unbold[2])),
                "center": list(region_center(regs_unbold[2])),
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.8836,
                "bold_prob": 0.8555,
                "heavy_prob": 0.1423,
                "family_probs": {"KAISHO_BRUSH": 0.8836, "ANTIQUE": 0.02},
                "top_fonts": [{"font": "DFKenRyuGyoShoStd-W7.otf", "family": "KAISHO_BRUSH", "prob": 0.2865}],
            },
        }
    }

    assert recommend_mtui_font_for_prediction(ym_cache["by_ridx"]["1"]) == ("FZBeiWeiKaiShu-S19", False, {"KAISHO_BRUSH"})
    assert recommend_mtui_font_for_prediction(ym_cache["by_ridx"]["2"])[0:2] == ("FZBeiWeiKaiShu-S19", False)

    issues_clean = _detect_structural_and_border_cv_issues(
        page, inp, None, regs_unbold, yuzumarker_page_cache=ym_cache
    )
    assert not any(
        i["category"] in ("raw_bold_mismatch", "yuzumarker_bold_mismatch", "inherently_heavy_font_bolded")
        for i in issues_clean
    )

    regs_bolded = [
        regs_unbold[0],
        {**regs_unbold[1], "bold": True},
        {**regs_unbold[2], "font_family": "FZSongHei-B07", "bold": True},
    ]
    issues_bold = _detect_structural_and_border_cv_issues(
        page, inp, None, regs_bolded, yuzumarker_page_cache=ym_cache
    )
    ih_by_r = {i["region_index"]: i["severity"] for i in issues_bold if i["category"] == "inherently_heavy_font_bolded"}
    assert ih_by_r.get(1) == "warning"
    assert ih_by_r.get(2) == "error"


def test_unjustified_faux_bold_blocks_tiny_and_zhunyuan_while_sparing_genuine_shouts(tmp_path: Path) -> None:
    page = np.full((920, 1280, 3), 185, dtype=np.uint8)

    l0, _ = _draw_enclosed_bubble_with_strokes(page, (110, 210), (78, 145), stroke_thickness=2, glyph_count=3)
    l1, _ = _draw_enclosed_bubble_with_strokes(page, (300, 210), (72, 130), stroke_thickness=2, glyph_count=2)
    l2, _ = _draw_enclosed_bubble_with_strokes(page, (490, 210), (72, 130), stroke_thickness=2, glyph_count=2)
    l3, _ = _draw_enclosed_bubble_with_strokes(page, (680, 210), (78, 145), stroke_thickness=2, glyph_count=3)
    l4, _ = _draw_enclosed_bubble_with_strokes(page, (870, 210), (78, 145), stroke_thickness=2, glyph_count=2)
    l5, _ = _draw_enclosed_bubble_with_strokes(page, (1080, 210), (78, 145), stroke_thickness=2, glyph_count=3)

    l6, _ = _draw_enclosed_bubble_with_strokes(page, (160, 650), (78, 145), stroke_thickness=2, glyph_count=3)
    l7, _ = _draw_enclosed_bubble_with_strokes(page, (400, 650), (92, 155), stroke_thickness=7, glyph_count=3)
    l8, _ = _draw_enclosed_bubble_with_strokes(page, (660, 650), (95, 160), stroke_thickness=8, glyph_count=3)
    page[560:720, 880:1040] = 160
    cv2.rectangle(page, (925, 610), (985, 670), (20, 20, 20), 4)
    l9 = [[[915.0, 595.0], [995.0, 595.0], [995.0, 685.0], [915.0, 685.0]]]

    inp = page.copy()
    for (cx, cy), (rx, ry) in (
        ((110, 210), (66, 132)),
        ((300, 210), (60, 118)),
        ((490, 210), (60, 118)),
        ((680, 210), (66, 132)),
        ((870, 210), (66, 132)),
        ((1080, 210), (66, 132)),
        ((160, 650), (66, 132)),
        ((400, 650), (78, 142)),
        ((660, 650), (80, 146)),
    ):
        cv2.ellipse(inp, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)

    regs = [
        {"text": "普通の会話だ", "translation": "普通对话", "font_family": "FZHei-B01", "bold": False, "font_size": 28, "direction": "v", "lines": l0},
        {"text": "っ!?", "translation": "唔!?", "font_family": "FZHei-B01", "bold": True, "font_size": 30, "direction": "v", "lines": l1},
        {"text": "──っ!?", "translation": "呃!?", "font_family": "FZHei-B01", "bold": True, "font_size": 32, "direction": "v", "lines": l2},
        {"text": "今日のダイダロス通り", "translation": "今天的[BR]代达罗斯街", "font_family": "FZHei-B01", "bold": True, "font_size": 28, "direction": "v", "lines": l3},
        {"text": "『傷』や", "translation": "﹃伤﹄啊", "font_family": "FZHei-B01", "bold": True, "font_size": 28, "direction": "v", "lines": l4},
        {"text": "お前が言うな", "translation": "轮不到你说", "font_family": "FZHei-B01", "bold": True, "font_size": 28, "direction": "v", "lines": l5},
        {"text": "そうだよ", "translation": "就是这样", "font_family": "FZZhunYuan-M02", "bold": True, "font_size": 28, "direction": "v", "lines": l6},
        {"text": "ぬんっ！", "translation": "嗯啊！", "font_family": "FZHei-B01", "bold": True, "font_size": 52, "direction": "v", "lines": l7},
        {"text": "っっ⁉", "translation": "唔唔!?", "font_family": "Source Han Sans SC::Bold", "bold": False, "font_size": 56, "direction": "v", "lines": l8},
        {"text": "っ!", "translation": "唔!", "font_family": "FZHei-B01", "bold": True, "font_size": 28, "direction": "v", "lines": l9},
    ]

    ym_cache_raw = {
        "0": {"n_clean_chars": 6, "pred_family": "ANTIQUE", "pred_conf": 0.96, "bold_prob": 0.92, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.96}, "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.92}]},
        "1": {"n_clean_chars": 1, "pred_family": "KAISHO_BRUSH", "pred_conf": 0.3586, "bold_prob": 0.5106, "heavy_prob": 0.2105, "family_probs": {"KAISHO_BRUSH": 0.3586, "ANTIQUE": 0.15}, "top_fonts": [{"font": "AP-OTF-Shuei4goBKanaStdN-Hv.otf", "family": "KAKU_GOTHIC", "prob": 0.1129}]},
        "2": {"n_clean_chars": 2, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.37, "bold_prob": 0.74, "heavy_prob": 0.22, "family_probs": {"KAKU_GOTHIC": 0.37, "ANTIQUE": 0.12}, "top_fonts": [{"font": "FOT-Shuei4goBStd-Hv.otf", "family": "KAKU_GOTHIC", "prob": 0.17}]},
        "3": {"n_clean_chars": 10, "pred_family": "ANTIQUE", "pred_conf": 0.99, "bold_prob": 0.99, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.99}, "top_fonts": [{"font": "FOT-DNPShueiAntiStd-B.otf", "family": "ANTIQUE", "prob": 0.96}]},
        "4": {"n_clean_chars": 2, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.95, "bold_prob": 0.65, "heavy_prob": 0.0, "family_probs": {"KAKU_GOTHIC": 0.95}, "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.59}]},
        "5": {"n_clean_chars": 6, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.92, "bold_prob": 0.52, "heavy_prob": 0.0, "family_probs": {"KAKU_GOTHIC": 0.92}, "top_fonts": [{"font": "A-OTF-KakuminPro-Medium.otf", "family": "KAKU_GOTHIC", "prob": 0.48}]},
        "6": {"n_clean_chars": 4, "pred_family": "ROUND_GOTHIC", "pred_conf": 0.96, "bold_prob": 0.05, "heavy_prob": 0.0, "family_probs": {"ROUND_GOTHIC": 0.96}, "top_fonts": [{"font": "FOT-SeuratPro-M.otf", "family": "ROUND_GOTHIC", "prob": 0.90}]},
        "7": {"n_clean_chars": 4, "pred_family": "ANTIQUE", "pred_conf": 0.85, "bold_prob": 0.95, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.85}, "top_fonts": [{"font": "FOT-DNPShueiAntiStd-B.otf", "family": "ANTIQUE", "prob": 0.80}]},
        "8": {"n_clean_chars": 3, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.98, "bold_prob": 0.99, "heavy_prob": 0.37, "family_probs": {"KAKU_GOTHIC": 0.98}, "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.82}]},
        "9": {"n_clean_chars": 1, "pred_family": "KAISHO_BRUSH", "pred_conf": 0.35, "bold_prob": 0.50, "heavy_prob": 0.15, "family_probs": {"KAISHO_BRUSH": 0.35}, "top_fonts": [{"font": "AP-OTF-Shuei4goBKanaStdN-Hv.otf", "family": "KAKU_GOTHIC", "prob": 0.11}]},
    }
    ym_cache = {
        "by_ridx": {
            k: {
                **v,
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(regs[int(k)])),
                "center": list(region_center(regs[int(k)])),
            }
            for k, v in ym_cache_raw.items()
        }
    }

    issues = _detect_structural_and_border_cv_issues(page, inp, None, regs, yuzumarker_page_cache=ym_cache)
    ufb = {i["region_index"]: i["severity"] for i in issues if i["category"] == "unjustified_faux_bold"}
    assert ufb == {1: "error", 2: "error", 3: "warning", 6: "error"}

    folder = tmp_path / "gate_b_overbold_lock"
    folder.mkdir()
    img_p = folder / "V18_053.png"
    Image.fromarray(page).save(img_p)
    paths = resolve_page_paths(img_p)
    save_rgb_image_atomic(inp, paths.inpainted_path)
    reg_before = [regs[0], {**regs[1], "bold": False}]
    save_page_json_atomic(paths, str(img_p.resolve()), {"regions": reg_before}, auto_initial_snapshot=False)
    ym_file = paths.work_dir / ".yuzumarker_font_cache.json"
    ym_file.parent.mkdir(parents=True, exist_ok=True)
    ym_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "pages": {"V18_053": {"by_ridx": {"0": ym_cache["by_ridx"]["0"], "1": ym_cache["by_ridx"]["1"]}}},
            }
        ),
        encoding="utf-8",
    )
    before_rep = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)

    reg_after = [regs[0], {**regs[1], "bold": True}]
    save_page_json_atomic(paths, str(img_p.resolve()), {"regions": reg_after}, auto_initial_snapshot=False)
    after_rep = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    diff = diff_qa_audits(before_rep, after_rep)
    assert diff["passed"] is False
    assert any(e["category"] == "unjustified_faux_bold" and e["region_index"] == 1 for e in diff["newly_introduced_errors"])


def test_raw_bold_mismatch_and_yuzumarker_bold_mismatch_short_box_guards() -> None:
    page = np.full((520, 820, 3), 210, dtype=np.uint8)
    for (cx, cy), (rx, ry), dx, dy_pair in (
        ((120, 260), (65, 130), 18, ((185, 235), (270, 320))),
        ((310, 260), (40, 120), 10, ((195, 235), (275, 315))),
        ((500, 260), (65, 130), 18, ((185, 235), (270, 320))),
        ((700, 260), (65, 130), 18, ((185, 235), (270, 320))),
    ):
        cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (10, 10, 10), 3)
        for ya, yb in dy_pair:
            cv2.line(page, (cx - dx, ya), (cx + dx, yb), (10, 10, 10), 3)

    inp = page.copy()
    for (cx, cy), (rx, ry) in (
        ((120, 260), (54, 118)),
        ((310, 260), (32, 108)),
        ((500, 260), (54, 118)),
        ((700, 260), (54, 118)),
    ):
        cv2.ellipse(inp, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)

    l0 = [[[95.0, 175.0], [145.0, 175.0], [145.0, 330.0], [95.0, 330.0]]]
    l1 = [[[296.0, 185.0], [324.0, 185.0], [324.0, 325.0], [296.0, 325.0]]]
    l2 = [[[475.0, 175.0], [525.0, 175.0], [525.0, 330.0], [475.0, 330.0]]]
    l3 = [[[675.0, 175.0], [725.0, 175.0], [725.0, 330.0], [675.0, 330.0]]]

    regs = [
        {"text": "普通の台詞だ", "translation": "普通对话", "font_family": "FZHei-B01", "bold": False, "font_size": 28, "direction": "v", "lines": l0},
        {"text": "あっ", "translation": "啊", "font_family": "FZHei-B01", "bold": False, "font_size": 26, "direction": "v", "lines": l1},
        {"text": "ッッ!", "translation": "唔!", "font_family": "FZHei-B01", "bold": False, "font_size": 48, "direction": "v", "lines": l2},
        {"text": "っっ⁉", "translation": "唔唔!?", "font_family": "FZHei-B01", "bold": False, "font_size": 22, "direction": "v", "lines": l3},
    ]
    sw_map, ref_hw = qa_mod.measure_page_stroke_widths(
        cv2.cvtColor(page, cv2.COLOR_RGB2GRAY), cv2.cvtColor(inp, cv2.COLOR_RGB2GRAY), regs
    )
    assert 2.38 <= sw_map[1] < 2.95 and (2.0 * sw_map[1]) / 28.0 >= 0.165 and sw_map[1] < ref_hw * 1.12

    ym_sb_raw = {
        "0": {"n_clean_chars": 6, "pred_family": "ANTIQUE", "pred_conf": 0.96, "bold_prob": 0.92, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.96}, "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.92}]},
        "1": {"n_clean_chars": 2, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.80, "bold_prob": 0.92, "heavy_prob": 0.10, "family_probs": {"KAKU_GOTHIC": 0.80, "ANTIQUE": 0.05}, "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.35}]},
        "2": {"n_clean_chars": 2, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.85, "bold_prob": 0.95, "heavy_prob": 0.72, "family_probs": {"KAKU_GOTHIC": 0.85, "ANTIQUE": 0.0}, "top_fonts": [{"font": "A-OTF-ShinGoPr6-Heavy.otf", "family": "KAKU_GOTHIC", "prob": 0.42}]},
        "3": {"n_clean_chars": 3, "pred_family": "KAKU_GOTHIC", "pred_conf": 0.94, "bold_prob": 0.92, "heavy_prob": 0.15, "family_probs": {"KAKU_GOTHIC": 0.94, "ANTIQUE": 0.02}, "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.62}]},
    }
    ym_cache = {
        "by_ridx": {
            k: {
                **v,
                "bbox_sig": region_bbox_signature(region_bbox_xyxy(regs[int(k)])),
                "center": list(region_center(regs[int(k)])),
            }
            for k, v in ym_sb_raw.items()
        }
    }
    issues = _detect_structural_and_border_cv_issues(page, inp, None, regs, yuzumarker_page_cache=ym_cache)
    rb = [i for i in issues if i["category"] == "raw_bold_mismatch" and i["region_index"] == 1]
    assert rb == []
    yb = {i["region_index"]: i["severity"] for i in issues if i["category"] == "yuzumarker_bold_mismatch"}
    assert yb.get(1) == "warning"
    assert yb.get(2) == "error"
    assert yb.get(3) == "error"


def _write_synthetic_ttf(dest: Path, family_name: str, chars: str, style_name: str = "Regular") -> None:
    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((900, 100))
    pen.lineTo((900, 900))
    pen.lineTo((100, 900))
    pen.closePath()
    box_glyph = pen.glyph()

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "box"])
    fb.setupCharacterMap({ord(ch): "box" for ch in chars})
    fb.setupGlyf({".notdef": box_glyph, "box": box_glyph})
    fb.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family_name, "styleName": style_name})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(dest))


def test_wenkai_punctuation_fallback_and_cache_version_refresh(tmp_path: Path, monkeypatch) -> None:
    fake_root = tmp_path / "synthetic_mtui_root"
    fonts_dir = fake_root / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    _write_synthetic_ttf(fonts_dir / "FZZhunYuan-M02.ttf", "FZZhunYuan-M02", "︙这个人")
    _write_synthetic_ttf(fonts_dir / "FZBeiWeiKaiShu-S19.ttf", "FZBeiWeiKaiShu-S19", "︙这个人")
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", fake_root)

    assert _pick_punctuation_fallback_font("LXGW WenKai", "︙") == "FZZhunYuan-M02"
    assert _pick_punctuation_fallback_font("LXGW WenKai::Medium", "︙") == "FZZhunYuan-M02"
    assert _pick_punctuation_fallback_font("FZNewKai-Z03", "︙") == "FZBeiWeiKaiShu-S19"

    assert CACHE_VERSION == 2
    folder = tmp_path / "cache_ver_ch"
    folder.mkdir()
    img_p = folder / "p01.png"
    page = np.full((300, 300, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_p)
    paths = resolve_page_paths(img_p)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_p.resolve()),
        {
            "regions": [
                {
                    "lines": [[[60.0, 50.0], [160.0, 50.0], [160.0, 220.0], [60.0, 220.0]]],
                    "text": "この人を",
                    "translation": "这个人",
                    "font_family": "LXGW WenKai::Medium",
                    "font_size": 28,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    ym_file = paths.work_dir / ".yuzumarker_font_cache.json"
    ym_file.parent.mkdir(parents=True, exist_ok=True)
    ym_file.write_text(
        json.dumps({"pages": {"p01": {"by_ridx": {"0": {"pred_family": "KYOKASHO_PEN", "pred_conf": 0.99}}}}}),
        encoding="utf-8",
    )
    loaded = load_font_detection_cache(paths.work_dir)
    assert "p01" in loaded.get("pages", {})

    calls: list[bool] = []

    class _FakeWorker:
        def detect_fonts(self, target_path, force_refresh=False):
            calls.append(bool(force_refresh))
            return {"status": "ok"}

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(qa_mod, "yuzumarker_models_available", lambda: True)
    monkeypatch.setattr(worker_client_mod, "get_worker_client", lambda: _FakeWorker())

    ym_file.write_text(json.dumps({"version": 1, "pages": {"p01": {"by_ridx": {}}}}), encoding="utf-8")
    run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(calls) == 1

    ym_file.write_text(json.dumps({"version": CACHE_VERSION, "pages": {"p01": {"by_ridx": {}}}}), encoding="utf-8")
    run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(calls) == 1

    # R2-F06 & R2-F38: Invalid `"version": "unknown"` triggers refresh without ValueError,
    # and when refresh fails, stale/invalid cache predictions are NOT passed to QA!
    stale_mismatch_payload = {
        "pages": {
            "p01": {
                "region_count": 1,
                "by_ridx": {
                    "0": {
                        "bbox_sig": region_bbox_signature([60.0, 50.0, 160.0, 220.0]),
                        "center": [110.0, 135.0],
                        "pred_family": "POP_CHISEL",
                        "pred_conf": 0.99,
                        "bold_prob": 1.0,
                        "heavy_prob": 0.0,
                        "family_probs": {"POP_CHISEL": 0.99, "KYOKASHO_PEN": 0.0},
                        "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.98}],
                    }
                },
            }
        }
    }
    ym_file.write_text(json.dumps({"version": "unknown", **stale_mismatch_payload}), encoding="utf-8")
    run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(calls) == 2

    class _FailingWorker:
        def detect_fonts(self, target_path, force_refresh=False):
            calls.append(bool(force_refresh))
            raise RuntimeError("worker offline")

    monkeypatch.setattr(worker_client_mod, "get_worker_client", lambda: _FailingWorker())
    for bad_ver in (1, "unknown"):
        ym_file.write_text(json.dumps({"version": bad_ver, **stale_mismatch_payload}), encoding="utf-8")
        rep_deg = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False, force_yuzumarker=True)
        assert rep_deg.get("yuzumarker_scan_degraded") is True
        assert not any(
            i["category"] == "yuzumarker_font_mismatch"
            for p_rep in rep_deg["pages"]
            for i in p_rep["issues"]
        )


def test_verify_crops_ym_header_anti_trap_formatting(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_audit_cli_module(monkeypatch)
    fmt = mod._format_verify_crop_ym_line

    line_fig3, _, mis_fig3 = fmt(
        {
            "n_clean_chars": 1,
            "pred_family": "KAISHO_BRUSH",
            "pred_conf": 0.36,
            "bold_prob": 0.51,
            "heavy_prob": 0.21,
            "top_fonts": [{"font": "AP-OTF-Shuei4goBKanaStdN-Hv.otf", "prob": 0.11}],
        },
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.2,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert "n_clean=1" in line_fig3
    assert "raw_sw=4.0px (reg≈4.4px)" in line_fig3
    assert "[LOW-CONF/SHORT: judge by raw_sw vs reg]" in line_fig3
    assert "+B" not in line_fig3
    assert "[LOCKED>=0.90]" not in line_fig3
    assert mis_fig3 is False

    line_ant, _, mis_ant = fmt(
        {
            "n_clean_chars": 10,
            "pred_family": "ANTIQUE",
            "pred_conf": 0.99,
            "bold_prob": 0.99,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-DNPShueiAntiStd-B.otf", "prob": 0.96}],
        },
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert "bold_p=0.99(ANTIQUE-normal)" in line_ant
    assert "rec=FZHei-B01 [LOCKED>=0.90]" in line_ant
    assert "+B" not in line_ant
    assert mis_ant is False

    line_hot, _, mis_hot = fmt(
        {
            "n_clean_chars": 8,
            "pred_family": "KAISHO_BRUSH",
            "pred_conf": 0.98,
            "bold_prob": 0.98,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "HOT-FKaishoStd-B.otf", "prob": 0.98}],
        },
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert "[LOCKED>=0.90]" not in line_hot
    assert mis_hot is False

    line_core, _, mis_core = fmt(
        {
            "n_clean_chars": 8,
            "pred_family": "KAISHO_BRUSH",
            "pred_conf": 0.98,
            "bold_prob": 1.0,
            "heavy_prob": 0.1,
            "top_fonts": [{"font": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "prob": 0.95}],
        },
        ff="FZBeiWeiKaiShu-S19",
        shw=2.5,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert "rec=FZBeiWeiKaiShu-S19 [LOCKED>=0.90]" in line_core
    assert "+B" not in line_core
    assert mis_core is False

    ym_kyoka = {
        "n_clean_chars": 4,
        "pred_family": "KYOKASHO_PEN",
        "pred_conf": 0.99,
        "bold_prob": 0.0,
        "heavy_prob": 0.0,
        "top_fonts": [
            {"font": "G-OTF-GKyokaICAPro-Medium.otf", "prob": 0.72},
            {"font": "A-OTF-KyokaICAPro-Medium.otf", "prob": 0.26},
        ],
    }
    line_ky_bad, _, mis_ky_bad = fmt(
        ym_kyoka,
        ff="FZZhunYuan-M02",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_ky_bad is True
    assert "rec=LXGW WenKai::Medium" in line_ky_bad
    assert "HIGH-CONF YM MISMATCH" in line_ky_bad

    line_ky_ok, _, mis_ky_ok = fmt(
        ym_kyoka,
        ff="LXGW WenKai::Medium",
        shw=2.0,
        reg_hw=2.0,
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_ky_ok is False
    assert "rec=LXGW WenKai::Medium [LOCKED>=0.90]" in line_ky_ok
