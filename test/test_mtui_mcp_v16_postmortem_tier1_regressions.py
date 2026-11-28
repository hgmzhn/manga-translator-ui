"""Regression tests for V16 postmortem Tier-1 font and bubble underfill rules."""

import _bootstrap  # noqa: F401, I001

import cv2
import numpy as np

from mtui_mcp.font_detector import recommend_mtui_font_for_prediction, region_bbox_signature
from mtui_mcp.qa_auditor import _detect_structural_and_border_cv_issues


def test_fig1_kaimin_does_not_auto_bold_regular_antique() -> None:
    """图1 (`V14_031 #2`, `V15_192 #3`): `KaiminSoStd-Heavy` / `KaiminTuStd-Heavy` must NOT trigger auto-bold."""
    ym_kaimin = {
        "pred_family": "MINCHO",
        "pred_conf": 0.85,
        "family_probs": {"MINCHO": 0.85, "ANTIQUE": 0.14},
        "bold_prob": 1.00,
        "heavy_prob": 0.77,
        "top_fonts": [{"font": "A-OTF-KaiminSoStd-Heavy.otf", "family": "MINCHO", "prob": 0.77}],
    }
    ff, b, acc = recommend_mtui_font_for_prediction(ym_kaimin)
    assert ff == "FZHei-B01"
    assert b is False
    assert "ANTIQUE" in acc

    base_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    inp_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    reg = {
        "text": "姿を見せろ！",
        "translation": "给我[BR]现身！",
        "font_family": "FZHei-B01",
        "bold": False,
        "font_size": 30,
        "direction": "v",
        "lines": [[[80.0, 60.0], [160.0, 60.0], [160.0, 220.0], [80.0, 220.0]]],
    }
    sig = region_bbox_signature([80.0, 60.0, 160.0, 220.0])
    issues = _detect_structural_and_border_cv_issues(
        base_rgb,
        inp_rgb,
        None,
        [reg],
        yuzumarker_page_cache={"by_bbox_sig": {sig: ym_kaimin}},
    )
    cats = {i["category"] for i in issues}
    assert "yuzumarker_bold_mismatch" not in cats
    assert "yuzumarker_font_mismatch" not in cats


def test_fig2_kakumin_precedence_and_no_cross_family_top1_escalation() -> None:
    """图2 (`V14_042 #7`): `KakuminPro-Bold` top1 must beat coin-flip `MINCHO`, and foreign `top1_prob` must never escalate another family."""
    ym_v14_042 = {
        "pred_family": "MINCHO",
        "pred_conf": 0.502,
        "family_probs": {"MINCHO": 0.502, "KAKU_GOTHIC": 0.496, "ANTIQUE": 0.0},
        "bold_prob": 0.99,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.491}],
    }
    ff, b, acc = recommend_mtui_font_for_prediction(ym_v14_042)
    assert ff == "FZHei-B01"
    assert b is True
    assert "MINCHO" not in acc
    assert "ANTIQUE" in acc

    ym_real_mincho = {
        "pred_family": "MINCHO",
        "pred_conf": 0.88,
        "family_probs": {"MINCHO": 0.88, "KAKU_GOTHIC": 0.08},
        "bold_prob": 0.50,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.08}],
    }
    ff_m, b_m, acc_m = recommend_mtui_font_for_prediction(ym_real_mincho)
    assert (ff_m, b_m, acc_m) == ("FZYaSong-DB-GBK", False, {"MINCHO"})

    base_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    inp_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    reg = {
        "text": "僕等は――",
        "translation": "我们││",
        "font_family": "FZHei-B01",
        "bold": False,
        "font_size": 32,
        "direction": "v",
        "lines": [[[100.0, 40.0], [160.0, 40.0], [160.0, 260.0], [100.0, 260.0]]],
    }
    sig = region_bbox_signature([100.0, 40.0, 160.0, 260.0])
    ym_coinflip_other = {
        "pred_family": "MINCHO",
        "pred_conf": 0.502,
        "family_probs": {"MINCHO": 0.502, "KAKU_GOTHIC": 0.496, "ANTIQUE": 0.0},
        "bold_prob": 0.85,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.491}],
    }
    issues = _detect_structural_and_border_cv_issues(
        base_rgb,
        inp_rgb,
        None,
        [reg],
        yuzumarker_page_cache={"by_bbox_sig": {sig: ym_coinflip_other}},
    )
    assert not any(i["category"] == "yuzumarker_font_mismatch" for i in issues)

    ym_foreign_top1 = {
        "pred_family": "MINCHO",
        "pred_conf": 0.72,
        "family_probs": {"MINCHO": 0.72, "KAKU_GOTHIC": 0.28, "ANTIQUE": 0.0},
        "bold_prob": 0.50,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "family": "KAKU_GOTHIC", "prob": 0.49}],
    }
    issues_w = _detect_structural_and_border_cv_issues(
        base_rgb,
        inp_rgb,
        None,
        [reg],
        yuzumarker_page_cache={"by_bbox_sig": {sig: ym_foreign_top1}},
    )
    fm_issues = [i for i in issues_w if i["category"] == "yuzumarker_font_mismatch"]
    assert len(fm_issues) == 1
    assert fm_issues[0]["severity"] == "warning"


def test_fig6_no_cross_family_bold_and_never_faux_bold_songti() -> None:
    """图6 (`V16_172 #2`): Low-conf `POP_CHISEL` must NEVER force `bold=True` onto `FZYaSong-DB-GBK` Songti."""
    base_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    inp_rgb = np.full((320, 320, 3), 255, dtype=np.uint8)
    reg = {
        "text": "ふへへへへへぇ……！",
        "translation": "呼嘿嘿嘿[BR]嘿嘿︙！",
        "font_family": "FZYaSong-DB-GBK",
        "bold": False,
        "font_size": 28,
        "direction": "v",
        "lines": [[[80.0, 50.0], [160.0, 50.0], [160.0, 230.0], [80.0, 230.0]]],
    }
    sig = region_bbox_signature([80.0, 50.0, 160.0, 230.0])
    ym_pop_low = {
        "pred_family": "POP_CHISEL",
        "pred_conf": 0.641,
        "family_probs": {"POP_CHISEL": 0.641, "MINCHO": 0.31, "ANTIQUE": 0.0},
        "bold_prob": 0.94,
        "heavy_prob": 0.05,
        "top_fonts": [{"font": "DFPOPCornStd-W7.otf", "family": "POP_CHISEL", "prob": 0.409}],
    }
    issues = _detect_structural_and_border_cv_issues(
        base_rgb,
        inp_rgb,
        None,
        [reg],
        yuzumarker_page_cache={"by_bbox_sig": {sig: ym_pop_low}},
    )
    cats = {i["category"] for i in issues}
    assert "yuzumarker_font_mismatch" not in cats
    assert "yuzumarker_bold_mismatch" not in cats

    ff_g, b_g, _ = recommend_mtui_font_for_prediction(
        {
            "pred_family": "MINCHO",
            "pred_conf": 0.80,
            "bold_prob": 0.92,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-GrecoStd-B.otf", "family": "MINCHO", "prob": 0.60}],
        }
    )
    assert (ff_g, b_g) == ("FZYaSong-DB-GBK", False)

    ym_heavy_mincho = {
        "pred_family": "MINCHO",
        "pred_conf": 0.96,
        "family_probs": {"MINCHO": 0.96, "ANTIQUE": 0.0},
        "bold_prob": 1.0,
        "heavy_prob": 0.96,
        "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "family": "MINCHO", "prob": 0.82}],
    }
    issues_heavy = _detect_structural_and_border_cv_issues(
        base_rgb,
        inp_rgb,
        None,
        [reg],
        yuzumarker_page_cache={"by_bbox_sig": {sig: ym_heavy_mincho}},
    )
    bm = [i for i in issues_heavy if i["category"] == "yuzumarker_bold_mismatch"]
    assert len(bm) == 1
    assert bm[0]["recommended_font_family"] == "FZCuSong-B09"
    assert bm[0]["recommended_bold"] is False
    assert "set bold=True" not in bm[0]["message"]
    assert "FZCuSong-B09" in bm[0]["message"]


def test_fig4_underfilled_font_splits_multilobe_and_skips_unsplit_guardrail() -> None:
    """图4 (`V15_078 #0 & #2`): Check 8c must measure per-lobe height (and skip unsplit multi-region bubbles)."""
    img = np.full((700, 420, 3), 200, dtype=np.uint8)
    cv2.ellipse(img, (210, 175), (95, 115), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (210, 395), (95, 115), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (210, 175), (95, 115), 0, 0, 360, (10, 10, 10), 4)
    cv2.ellipse(img, (210, 395), (95, 115), 0, 0, 360, (10, 10, 10), 4)
    cv2.ellipse(img, (210, 285), (44, 24), 0, 0, 360, (255, 255, 255), -1)

    regs_good = [
        {
            "text": "じゃあもし落とし穴の類に引っかかったとして",
            "translation": "那么︙也是啊[BR]假如他们真是[BR]中了陷阱[BR]之类的话︙",
            "font_family": "FZHei-B01",
            "font_size": 25,
            "direction": "v",
            "center": [210.0, 175.0],
            "lines": [[[150.0, 95.0], [270.0, 95.0], [270.0, 255.0], [150.0, 255.0]]],
        },
        {
            "text": "フィン達は自力で脱出できると思うか？",
            "translation": "你觉得芬恩他们[BR]能自己脱身吗？",
            "font_family": "FZHei-B01",
            "font_size": 27,
            "direction": "v",
            "center": [210.0, 395.0],
            "lines": [[[165.0, 305.0], [255.0, 305.0], [255.0, 485.0], [165.0, 485.0]]],
        },
    ]
    issues_good = _detect_structural_and_border_cv_issues(img, img, None, regs_good)
    assert not any(i["category"] == "bubble_underfilled_font" for i in issues_good)

    regs_tiny = [regs_good[0], {**regs_good[1], "font_size": 15}]
    issues_tiny = _detect_structural_and_border_cv_issues(img, img, None, regs_tiny)
    uf_tiny = [i for i in issues_tiny if i["category"] == "bubble_underfilled_font"]
    assert any(i["region_index"] == 1 for i in uf_tiny)

    rect_img = np.full((700, 420, 3), 200, dtype=np.uint8)
    cv2.rectangle(rect_img, (90, 80), (330, 560), (255, 255, 255), -1)
    cv2.rectangle(rect_img, (90, 80), (330, 560), (10, 10, 10), 4)
    regs_unsplit = [
        {
            "text": "上段の台詞です",
            "translation": "上半部分的[BR]正常台词",
            "font_family": "FZHei-B01",
            "font_size": 27,
            "direction": "v",
            "center": [210.0, 200.0],
            "lines": [[[160.0, 120.0], [260.0, 120.0], [260.0, 280.0], [160.0, 280.0]]],
        },
        {
            "text": "下段の台詞です",
            "translation": "下半部分的[BR]正常台词",
            "font_family": "FZHei-B01",
            "font_size": 27,
            "direction": "v",
            "center": [210.0, 440.0],
            "lines": [[[160.0, 360.0], [260.0, 360.0], [260.0, 520.0], [160.0, 520.0]]],
        },
    ]
    issues_unsplit = _detect_structural_and_border_cv_issues(rect_img, rect_img, None, regs_unsplit)
    assert not any(i["category"] == "bubble_underfilled_font" for i in issues_unsplit)
