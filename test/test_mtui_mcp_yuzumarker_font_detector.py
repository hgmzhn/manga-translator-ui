"""Unit tests for YuzuMarker 6,150-font commercial taxonomy, font recommendation, and QA auditor integration."""

import _bootstrap  # noqa: F401, I001

import numpy as np

from mtui_mcp.font_detector import (
    classify_current_mtui_font,
    classify_vcb_font_entry,
    lookup_cached_region_prediction,
    recommend_mtui_font_for_prediction,
    region_bbox_signature,
)
from mtui_mcp.qa_auditor import _detect_structural_and_border_cv_issues


def test_vcb_commercial_font_taxonomy() -> None:
    assert classify_vcb_font_entry({"path": "FOT-AnticCezannePro-DB.otf", "serif": False}) == ("ANTIQUE", "bold")
    assert classify_vcb_font_entry({"path": "G-OTF-KAntiqueStd-ANM.otf", "serif": False}) == ("ANTIQUE", "regular")
    assert classify_vcb_font_entry({"path": "FOT-ManyoKoinLargeStd-B.otf", "serif": False}) == ("GUYIN", "bold")
    assert classify_vcb_font_entry({"path": "FOT-ComicReggaeStd-B.otf", "serif": False}) == ("POP_CHISEL", "bold")
    assert classify_vcb_font_entry({"path": "A-OTF-TakeStd-Bold.otf", "serif": False}) == ("POP_CHISEL", "bold")
    assert classify_vcb_font_entry({"path": "DFSoGeiStd-W5.otf", "serif": False}) == ("POP_CHISEL", "regular")
    assert classify_vcb_font_entry({"path": "DFSoGeiStd-W7.otf", "serif": False}) == ("POP_CHISEL", "bold")
    assert classify_vcb_font_entry({"path": "A-OTF-TakeStd-Light.otf", "serif": False}) == ("KYOKASHO_PEN", "light")
    assert classify_vcb_font_entry({"path": "FOT-ComicMysteryStd-DB.otf", "serif": False}) == ("KYOKASHO_PEN", "bold")
    assert classify_vcb_font_entry({"path": "A-OTF-Reisho101Std-Medium.otf", "serif": False}) == ("REISHO", "regular")
    assert classify_vcb_font_entry({"path": "A-OTF-LikureiStd-Regular.otf", "serif": False}) == ("REISHO", "regular")
    assert classify_vcb_font_entry({"path": "FOT-HoureiStd-EB.otf", "serif": False}) == ("REISHO", "heavy")
    assert classify_vcb_font_entry({"path": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "serif": False}) == ("KAISHO_BRUSH", "bold")
    assert classify_vcb_font_entry({"path": "RoBrushStd-UB.otf", "serif": False}) == ("KAKU_GOTHIC", "heavy")
    assert classify_vcb_font_entry({"path": "FOT-TsukuARdGothicStd-B.otf", "serif": False}) == ("ROUND_GOTHIC", "bold")
    assert classify_vcb_font_entry({"path": "FOT-RodinHappyPro-B.otf", "serif": False}) == ("ROUND_GOTHIC", "bold")
    assert classify_vcb_font_entry({"path": "A-OTF-GothicBBBPro-Medium.otf", "serif": False}) == ("KAKU_GOTHIC", "regular")
    assert classify_vcb_font_entry({"path": "A-OTF-KakuminPro-Bold.otf", "serif": True}) == ("KAKU_GOTHIC", "bold")
    assert classify_vcb_font_entry({"path": "A-OTF-RyuminPr5-Ultra.otf", "serif": True}) == ("MINCHO", "heavy")
    assert classify_vcb_font_entry({"path": "DFLeiGaSoStd-W7.otf", "serif": False}) == ("MINCHO", "bold")


def test_mtui_font_recommendation_and_classification() -> None:
    assert classify_current_mtui_font("Arphic Tankuin Bold GB") == "GUYIN"
    assert classify_current_mtui_font("LiSu") == "REISHO"
    assert classify_current_mtui_font("FZJianZhi-M23") == "POP_CHISEL"
    assert classify_current_mtui_font("ZHSRXT2011-GB") == "POP_CHISEL"
    assert classify_current_mtui_font("ZCOOL QingKe HuangYou") == "POP_CHISEL"
    assert classify_current_mtui_font("FZZhunYuan-M02") == "ROUND_GOTHIC"
    assert classify_current_mtui_font("FZLanTingYuan-B-GBK") == "ROUND_GOTHIC"
    assert classify_current_mtui_font("FZCuSong-B09") == "MINCHO"
    assert classify_current_mtui_font("FZYaSong-DB-GBK") == "MINCHO"
    assert classify_current_mtui_font("FZYaSong-M-GBK") == "MINCHO"
    assert classify_current_mtui_font("FZBeiWeiKaiShu-S19") == "KAISHO_BRUSH"
    assert classify_current_mtui_font("LXGW WenKai::Medium") == "KYOKASHO_PEN"
    assert classify_current_mtui_font("FZHei-B01") == "ANTIQUE"

    ff, b, acc = recommend_mtui_font_for_prediction(
        {
            "pred_family": "KAKU_GOTHIC",
            "pred_conf": 0.99,
            "bold_prob": 0.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "A-OTF-GothicBBBPro-Medium.otf", "prob": 0.52}],
        }
    )
    assert (ff, b) == ("FZZhunYuan-M02", False)
    assert "MINCHO" not in acc

    ff_m, b_m, acc_m = recommend_mtui_font_for_prediction(
        {
            "pred_family": "MINCHO",
            "pred_conf": 0.96,
            "bold_prob": 1.0,
            "heavy_prob": 1.0,
            "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "prob": 0.54}],
        }
    )
    assert (ff_m, b_m, acc_m) == ("FZCuSong-B09", False, {"MINCHO"})

    ff_r, b_r, _ = recommend_mtui_font_for_prediction(
        {
            "pred_family": "ROUND_GOTHIC",
            "pred_conf": 1.0,
            "bold_prob": 1.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-RodinHappyPro-B.otf", "prob": 1.0}],
        }
    )
    assert (ff_r, b_r) == ("FZLanTingYuan-B-GBK", False)

    ff2, b2, acc2 = recommend_mtui_font_for_prediction(
        {
            "pred_family": "MINCHO",
            "neighbor_propagated_family": "GUYIN",
            "pred_conf": 0.31,
            "bold_prob": 0.44,
            "heavy_prob": 0.05,
            "top_fonts": [{"font": "KozMinPro-Bold.otf", "prob": 0.09}],
        }
    )
    assert (ff2, b2, acc2) == ("Arphic Tankuin Bold GB", False, {"GUYIN"})

    ff_s5, b_s5, acc_s5 = recommend_mtui_font_for_prediction(
        {
            "pred_family": "POP_CHISEL",
            "pred_conf": 1.0,
            "bold_prob": 0.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "DFSoGeiStd-W5.otf", "family": "POP_CHISEL", "prob": 0.75}],
        }
    )
    assert (ff_s5, b_s5, acc_s5) == ("ZHSRXT2011-GB", False, {"POP_CHISEL"})

    ff_s7, b_s7, acc_s7 = recommend_mtui_font_for_prediction(
        {
            "pred_family": "POP_CHISEL",
            "pred_conf": 1.0,
            "bold_prob": 1.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "DFSoGeiStd-W7.otf", "family": "POP_CHISEL", "prob": 0.92}],
        }
    )
    assert (ff_s7, b_s7, acc_s7) == ("ZHSRXT2011-GB", False, {"POP_CHISEL"})

    ff_cr, b_cr, acc_cr = recommend_mtui_font_for_prediction(
        {
            "pred_family": "POP_CHISEL",
            "pred_conf": 1.0,
            "bold_prob": 1.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.98}],
        }
    )
    assert (ff_cr, b_cr, acc_cr) == ("FZJianZhi-M23", False, {"POP_CHISEL"})


def test_qa_auditor_yuzumarker_catches_false_songti_and_missed_guyin() -> None:
    base_rgb = np.full((300, 300, 3), 255, dtype=np.uint8)
    inp_rgb = np.full((300, 300, 3), 255, dtype=np.uint8)
    reg0 = {
        "text": "あやつ等を乗せた船は",
        "translation": "载着那两人的船",
        "font_family": "FZYaSong-DB-GBK",
        "lines": [[[20, 20], [120, 20], [120, 220], [20, 220]]],
    }
    reg1 = {
        "text": "ぐ…",
        "translation": "咕︙",
        "font_family": "FZHei-B01",
        "lines": [[[150, 20], [210, 20], [210, 120], [150, 120]]],
    }
    sig0 = region_bbox_signature([20.0, 20.0, 120.0, 220.0])
    sig1 = region_bbox_signature([150.0, 20.0, 210.0, 120.0])
    ym_cache = {
        "by_bbox_sig": {
            sig0: {
                "ridx": 0,
                "bbox_sig": sig0,
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.99,
                "family_probs": {"KAKU_GOTHIC": 0.99, "MINCHO": 0.0},
                "bold_prob": 0.0,
                "heavy_prob": 0.0,
                "top_fonts": [{"font": "A-OTF-GothicBBBPro-Medium.otf", "prob": 0.52}],
            },
            sig1: {
                "ridx": 1,
                "bbox_sig": sig1,
                "pred_family": "MINCHO",
                "pred_conf": 0.31,
                "neighbor_propagated_family": "GUYIN",
                "neighbor_source_ridx": 0,
                "family_probs": {"MINCHO": 0.31, "GUYIN": 0.22},
                "bold_prob": 0.44,
                "heavy_prob": 0.05,
                "top_fonts": [{"font": "FOT-ManyoKoinStd-B.otf", "prob": 0.10}],
            },
        }
    }

    assert lookup_cached_region_prediction(ym_cache, reg0, 0) is not None
    issues = _detect_structural_and_border_cv_issues(
        base_rgb, inp_rgb, None, [reg0, reg1], yuzumarker_page_cache=ym_cache
    )
    ym_issues = [i for i in issues if i["category"] == "yuzumarker_font_mismatch"]
    assert len(ym_issues) == 2
    assert ym_issues[0]["recommended_font_family"] == "FZZhunYuan-M02"
    assert ym_issues[1]["recommended_font_family"] == "Arphic Tankuin Bold GB"

    reg0["font_family"] = "FZZhunYuan-M02"
    reg1["font_family"] = "Arphic Tankuin Bold GB"
    issues_fixed = _detect_structural_and_border_cv_issues(
        base_rgb, inp_rgb, None, [reg0, reg1], yuzumarker_page_cache=ym_cache
    )
    assert [i for i in issues_fixed if i["category"] == "yuzumarker_font_mismatch"] == []
