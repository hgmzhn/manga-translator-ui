"""Regression tests for Sword-Oratoria V20 P0 (`DFSoGei` -> `ZHSRXT2011-GB`) & P1 (narrow pure-kana stretch & thin-stem `FZCuSong-B09` guardrail)."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import sys

import cv2
import numpy as np
import pytest

from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_catalog import check_missing_glyphs
from mtui_mcp.font_detector import (
    CLEAN_STRIP_RE,
    classify_current_mtui_font,
    is_narrow_kana_stretch_suspect,
    page_resolution_scale,
    recommend_mtui_font_for_prediction,
    region_bbox_signature,
)
from mtui_mcp.qa_auditor import (
    _detect_structural_and_border_cv_issues,
    _measure_raw_stroke_stem_p90,
    measure_page_stroke_metrics,
    measure_page_stroke_widths,
)


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
    spec = importlib.util.spec_from_file_location("audit_and_compare_v20_p0_p1", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _draw_bubble_with_glyphs(
    canvas: np.ndarray,
    center: tuple[int, int],
    axes: tuple[int, int],
    *,
    stroke_thickness: int,
    glyph_count: int = 3,
) -> list[list[list[float]]]:
    cx, cy = center
    rx, ry = axes
    cv2.ellipse(canvas, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(canvas, (cx, cy), (rx, ry), 0, 0, 360, (12, 12, 12), 4)
    y_start = cy - int(ry * 0.52)
    step = max(24, int((ry * 1.04) / max(1, glyph_count)))
    for k in range(glyph_count):
        gy = y_start + k * step
        cv2.rectangle(canvas, (cx - 14, gy - 9), (cx + 14, gy + 9), (10, 10, 10), stroke_thickness)
    x1, y1 = float(cx - 24), float(y_start - 18)
    x2, y2 = float(cx + 24), float(y_start + (glyph_count - 1) * step + 18)
    return [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]]


def test_p0_sogei_classification_recommendation_and_zero_loop_audit() -> None:
    assert classify_current_mtui_font("ZHSRXT2011-GB") == "POP_CHISEL"
    assert classify_current_mtui_font("ZCOOL QingKe HuangYou") == "POP_CHISEL"
    assert classify_current_mtui_font("FZBeiWeiKaiShu-S19") == "KAISHO_BRUSH"

    for jp_font in (
        "DFSoGeiStd-W5.otf",
        "DFSoGeiStd-W7.otf",
        "DFSoGeiStd-W9.otf",
        "DFZongYi-GBOtf-W7.otf",
        "华康新综艺 Std W7.otf",
        "Arphic Singeitai Heavy JIS.ttc",
    ):
        rec = recommend_mtui_font_for_prediction(
            {
                "pred_family": "POP_CHISEL",
                "pred_conf": 1.0,
                "bold_prob": 0.0 if "W5" in jp_font else 1.0,
                "heavy_prob": 1.0 if ("W9" in jp_font or "Heavy" in jp_font) else 0.0,
                "top_fonts": [{"font": jp_font, "family": "POP_CHISEL", "prob": 0.80}],
            }
        )
        assert rec == ("ZHSRXT2011-GB", False, {"POP_CHISEL"})

    base_rgb = np.full((340, 340, 3), 255, dtype=np.uint8)
    ym_w5 = {
        "pred_family": "POP_CHISEL",
        "pred_conf": 1.0,
        "bold_prob": 0.0,
        "heavy_prob": 0.0,
        "family_probs": {"POP_CHISEL": 1.0, "ROUND_GOTHIC": 0.0, "ANTIQUE": 0.0},
        "top_fonts": [{"font": "DFSoGeiStd-W5.otf", "family": "POP_CHISEL", "prob": 0.7534}],
    }
    base_reg = {
        "text": "いいや歌うのはガレスだ",
        "translation": "不是我[BR]要唱的是[BR]格瑞斯",
        "font_size": 36,
        "direction": "v",
        "lines": [[[80.0, 60.0], [200.0, 60.0], [200.0, 184.0], [80.0, 184.0]]],
    }
    sig = region_bbox_signature([80.0, 60.0, 200.0, 184.0])
    ym_cache = {"by_bbox_sig": {sig: ym_w5}}

    for bad_ff in ("FZHei-B01", "FZZhunYuan-M02"):
        iss = _detect_structural_and_border_cv_issues(
            base_rgb, base_rgb, None, [{**base_reg, "font_family": bad_ff}], yuzumarker_page_cache=ym_cache
        )
        fm = [i for i in iss if i["category"] == "yuzumarker_font_mismatch"]
        assert len(fm) == 1
        assert fm[0]["severity"] == "error"
        assert fm[0]["recommended_font_family"] == "ZHSRXT2011-GB"
        assert fm[0]["recommended_bold"] is False

    for ok_ff in ("ZHSRXT2011-GB", "ZCOOL QingKe HuangYou"):
        iss_ok = _detect_structural_and_border_cv_issues(
            base_rgb, base_rgb, None, [{**base_reg, "font_family": ok_ff, "bold": False}], yuzumarker_page_cache=ym_cache
        )
        assert not any(i["category"] == "yuzumarker_font_mismatch" for i in iss_ok)


def test_p0_zhsrxt_inherently_heavy_no_faux_bold_and_w7_w9_suppression() -> None:
    page = np.full((520, 560, 3), 210, dtype=np.uint8)
    l_ref = _draw_bubble_with_glyphs(page, (130, 260), (80, 150), stroke_thickness=2, glyph_count=3)
    l_w7 = _draw_bubble_with_glyphs(page, (370, 260), (90, 160), stroke_thickness=7, glyph_count=3)
    inp = page.copy()
    cv2.ellipse(inp, (130, 260), (74, 144), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(inp, (370, 260), (84, 154), 0, 0, 360, (255, 255, 255), -1)

    regs = [
        {"text": "普通の台詞", "translation": "普通台词", "font_family": "FZHei-B01", "bold": False, "font_size": 28, "direction": "v", "lines": l_ref},
        {"text": "いいや歌うのは", "translation": "才不是[BR]我唱的", "font_family": "ZHSRXT2011-GB", "bold": False, "font_size": 34, "direction": "v", "lines": l_w7},
    ]
    for jp_font, hp in (("DFSoGeiStd-W7.otf", 0.0), ("DFSoGeiStd-W9.otf", 1.0)):
        ym_cache = {
            "by_ridx": {
                "0": {"pred_family": "ANTIQUE", "pred_conf": 0.98, "bold_prob": 0.9, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.98}, "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.95}]},
                "1": {"pred_family": "POP_CHISEL", "pred_conf": 1.0, "bold_prob": 1.0, "heavy_prob": hp, "family_probs": {"POP_CHISEL": 1.0}, "top_fonts": [{"font": jp_font, "family": "POP_CHISEL", "prob": 0.93}]},
            }
        }
        iss = _detect_structural_and_border_cv_issues(page, inp, None, regs, yuzumarker_page_cache=ym_cache)
        r1_cats = {i["category"] for i in iss if i.get("region_index") == 1}
        assert "raw_bold_mismatch" not in r1_cats
        assert "yuzumarker_bold_mismatch" not in r1_cats
        assert "yuzumarker_font_mismatch" not in r1_cats

    regs_bold = [regs[0], {**regs[1], "bold": True}]
    ym_w7 = {
        "by_ridx": {
            "0": {"pred_family": "ANTIQUE", "pred_conf": 0.98, "bold_prob": 0.9, "heavy_prob": 0.0, "family_probs": {"ANTIQUE": 0.98}, "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.95}]},
            "1": {"pred_family": "POP_CHISEL", "pred_conf": 1.0, "bold_prob": 1.0, "heavy_prob": 0.0, "family_probs": {"POP_CHISEL": 1.0}, "top_fonts": [{"font": "DFSoGeiStd-W7.otf", "family": "POP_CHISEL", "prob": 0.93}]},
        }
    }
    iss_b = _detect_structural_and_border_cv_issues(page, inp, None, regs_bold, yuzumarker_page_cache=ym_w7)
    r1_b = [i for i in iss_b if i.get("region_index") == 1]
    assert any(i["category"] == "inherently_heavy_font_bolded" and i["severity"] == "error" for i in r1_b)
    assert not any(i["category"] == "yuzumarker_bold_mismatch" for i in r1_b)


def test_p0_zhsrxt_glyph_coverage_and_style_doc_contract() -> None:
    settings = get_runtime_settings()
    if settings.fonts_dir and (settings.fonts_dir / "张海山锐谐体.ttf").is_file():
        sample = "不是我要唱的是格瑞斯剑姬神圣谭20完国警赢魔龙黑霸震撼︙…—─│︱！？⁉‼「」『』﹁﹂﹃﹄【】︻︼（）︵︶·・～︴!?"
        assert check_missing_glyphs("ZHSRXT2011-GB", sample) == []

    repo_root = _bootstrap.ROOT
    style_md = (
        repo_root / "mtui_mcp" / "skills" / "mtui-manga-proofreading" / "references" / "style_and_pitfalls.md"
    ).read_text(encoding="utf-8")
    assert "`ZHSRXT2011-GB`" in style_md
    assert "ZCOOL QingKe HuangYou" not in style_md
    assert "ZHSRXT2011-GB`（炸毛折线）" not in style_md
    assert "[NARROW-KANA-STRETCH]" in style_md
    assert "[THIN-STEM]" in style_md


def test_p1_clean_strip_re_and_spread_safe_narrow_kana_suspect() -> None:
    assert CLEAN_STRIP_RE.sub("", "キャー‼") == "キャ"
    assert CLEAN_STRIP_RE.sub("", "んだと⁉") == "んだと"
    assert CLEAN_STRIP_RE.sub("", "けけけ……") == "けけけ"
    assert CLEAN_STRIP_RE.sub("", "あ〜～︙│") == "あ"

    assert page_resolution_scale(None) == pytest.approx(1.0)
    assert page_resolution_scale((400, 400)) == pytest.approx(1.0)
    assert page_resolution_scale((1350, 1920)) == pytest.approx(1.0)
    assert page_resolution_scale((2700, 1920)) == pytest.approx(1.0)
    assert page_resolution_scale((2700, 3840)) == pytest.approx(2.0)
    assert page_resolution_scale((5400, 3840)) == pytest.approx(2.0)
    assert page_resolution_scale((800, 10000)) == pytest.approx(0.60)

    pred_ryumin_heavy = {
        "pred_family": "MINCHO",
        "pred_conf": 0.97,
        "bold_prob": 0.97,
        "heavy_prob": 0.90,
        "top_fonts": [{"font": "A-OTF-RyuminPr6-Heavy.otf", "family": "MINCHO", "prob": 0.50}],
    }
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(26, 137), source_text="けけけ……", raw_stem_hw=2.10, page_wh=(1350, 1920)) is True
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(26, 137), source_text="けけけ……", raw_stem_hw=2.10, page_wh=(2700, 1920)) is True
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(52, 274), source_text="けけけ……", raw_stem_hw=4.20, page_wh=(2700, 3840)) is True
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(34, 122), source_text="キャー‼", raw_stem_hw=2.00, page_wh=(1350, 1920)) is True

    assert is_narrow_kana_stretch_suspect({**pred_ryumin_heavy, "neighbor_propagated_family": "MINCHO"}, bbox_wh=(26, 137), source_text="けけけ……", page_wh=(1350, 1920)) is False
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(80, 310), source_text="だが――", raw_stem_hw=4.20, page_wh=(1350, 1920)) is False
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(26, 137), source_text="けけけ……", raw_stem_hw=3.00, page_wh=(1350, 1920)) is False
    assert is_narrow_kana_stretch_suspect(pred_ryumin_heavy, bbox_wh=(26, 137), source_text="勇気！", raw_stem_hw=2.00, page_wh=(1350, 1920)) is False
    pred_thin_mincho = {
        "pred_family": "MINCHO",
        "pred_conf": 0.96,
        "bold_prob": 0.01,
        "heavy_prob": 0.0,
        "top_fonts": [{"font": "A-OTF-RyuminPr6N-Medium.otf", "family": "MINCHO", "prob": 0.70}],
    }
    assert is_narrow_kana_stretch_suspect(pred_thin_mincho, bbox_wh=(26, 126), source_text="―ううん", raw_stem_hw=1.80, page_wh=(1350, 1920)) is False

    worker_src = (_bootstrap.ROOT / "mtui_mcp" / "worker" / "engine_worker.py").read_text(encoding="utf-8")
    assert "CLEAN_STRIP_RE" in worker_src
    assert "def _crop_exact_data_py" in worker_src


def test_p1_thin_stem_downgrade_and_spread_preservation() -> None:
    pred_cusong = {
        "pred_family": "MINCHO",
        "pred_conf": 0.99,
        "bold_prob": 0.99,
        "heavy_prob": 0.69,
        "top_fonts": [{"font": "A-OTF-RyuminPr6-Heavy.otf", "family": "MINCHO", "prob": 0.50}],
    }
    assert recommend_mtui_font_for_prediction(
        pred_cusong, raw_stem_hw=2.08, bbox_wh=(109, 124), source_text="おいオシアン……！", page_wh=(1350, 1920)
    ) == ("FZYaSong-DB-GBK", False, {"MINCHO"})

    assert recommend_mtui_font_for_prediction(
        pred_cusong, raw_stroke_hw=1.40, raw_stem_hw=0.0, bbox_wh=(110, 260), source_text="剣姫の決断", page_wh=(1350, 1920)
    ) == ("FZCuSong-B09", False, {"MINCHO"})

    for pwh in ((1350, 1920), (2700, 1920)):
        assert recommend_mtui_font_for_prediction(
            pred_cusong, raw_stem_hw=3.00, bbox_wh=(100, 240), source_text="剣姫の決断", page_wh=pwh
        ) == ("FZCuSong-B09", False, {"MINCHO"})


def test_p1_stem_p90_safeguards_and_check9a_warning_cap() -> None:
    base_pkt = np.full((240, 240), 150, dtype=np.uint8)
    base_pkt[60:92, 90:122] = 250
    base_pkt[64:88, 96:104] = 20
    base_pkt[64:88, 108:116] = 20
    tiny_bub = np.zeros((240, 240), dtype=bool)
    tiny_bub[60:92, 90:122] = True
    reg_pkt = {"lines": [[[80.0, 50.0], [160.0, 50.0], [160.0, 190.0], [80.0, 190.0]]]}
    assert _measure_raw_stroke_stem_p90(base_pkt, reg_pkt, tiny_bub) == 0.0

    base_halo = np.full((240, 240), 170, dtype=np.uint8)
    base_halo[70:170, 90:115] = 250
    base_halo[75:165, 115:145] = 20
    assert _measure_raw_stroke_stem_p90(base_halo, reg_pkt, None) == 0.0

    page = np.full((520, 760, 3), 210, dtype=np.uint8)
    cv2.ellipse(page, (120, 240), (75, 130), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (120, 240), (75, 130), 0, 0, 360, (10, 10, 10), 4)
    for gy in (190, 235, 280):
        cv2.rectangle(page, (112, gy - 8), (128, gy + 8), (10, 10, 10), 2)
    l_narrow = [[[107.0, 170.0], [133.0, 170.0], [133.0, 307.0], [107.0, 307.0]]]

    cv2.ellipse(page, (340, 240), (75, 130), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (340, 240), (75, 130), 0, 0, 360, (10, 10, 10), 4)
    for gy in (190, 235, 280):
        cv2.rectangle(page, (332, gy - 8), (348, gy + 8), (10, 10, 10), 2)
    l_kaisho = [[[327.0, 175.0], [353.0, 175.0], [353.0, 305.0], [327.0, 305.0]]]

    l_heavy = _draw_bubble_with_glyphs(page, (570, 240), (95, 145), stroke_thickness=8, glyph_count=3)

    inp = page.copy()
    cv2.ellipse(inp, (120, 240), (69, 124), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(inp, (340, 240), (69, 124), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(inp, (570, 240), (89, 139), 0, 0, 360, (255, 255, 255), -1)

    regs = [
        {"text": "けけけ……", "translation": "咯咯咯︙", "font_family": "FZHei-B01", "bold": False, "font_size": 24, "direction": "v", "lines": l_narrow},
        {"text": "っ……", "translation": "唔︙", "font_family": "FZHei-B01", "bold": False, "font_size": 24, "direction": "v", "lines": l_kaisho},
        {"text": "剣姫の決断", "translation": "剑姬的[BR]决断", "font_family": "FZHei-B01", "bold": False, "font_size": 32, "direction": "v", "lines": l_heavy},
    ]
    base_g = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
    inp_g = cv2.cvtColor(inp, cv2.COLOR_RGB2GRAY)
    shw_2, ref_2 = measure_page_stroke_widths(base_g, inp_g, regs)
    shw_3, stem_3, ref_3 = measure_page_stroke_metrics(base_g, inp_g, regs)
    assert shw_2 == shw_3 and ref_2 == ref_3
    assert stem_3[2] >= 3.0

    ym_cache = {
        "by_ridx": {
            "0": {"pred_family": "MINCHO", "pred_conf": 0.97, "bold_prob": 0.97, "heavy_prob": 0.90, "family_probs": {"MINCHO": 0.97, "ANTIQUE": 0.02}, "top_fonts": [{"font": "A-OTF-RyuminPr6-Heavy.otf", "family": "MINCHO", "prob": 0.50}]},
            "1": {"pred_family": "KAISHO_BRUSH", "pred_conf": 0.95, "bold_prob": 0.95, "heavy_prob": 0.10, "family_probs": {"KAISHO_BRUSH": 0.95, "ANTIQUE": 0.02}, "top_fonts": [{"font": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "family": "KAISHO_BRUSH", "prob": 0.88}]},
            "2": {"pred_family": "MINCHO", "pred_conf": 0.98, "bold_prob": 1.0, "heavy_prob": 0.95, "family_probs": {"MINCHO": 0.98, "ANTIQUE": 0.0}, "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "family": "MINCHO", "prob": 0.85}]},
        }
    }
    issues = _detect_structural_and_border_cv_issues(page, inp, None, regs, yuzumarker_page_cache=ym_cache)
    fm_by_r = {i["region_index"]: i for i in issues if i["category"] == "yuzumarker_font_mismatch"}

    assert fm_by_r[0]["severity"] == "warning"
    assert fm_by_r[0]["recommended_font_family"] == "FZYaSong-DB-GBK"
    assert "FZHei-B01" in fm_by_r[0]["message"] and "FZYaSong-DB-GBK" in fm_by_r[0]["message"]

    assert fm_by_r[1]["severity"] == "warning"
    assert "FZHei-B01" in fm_by_r[1]["message"]

    assert fm_by_r[2]["severity"] == "error"
    assert fm_by_r[2]["recommended_font_family"] == "FZCuSong-B09"


def test_p0_p1_verify_crops_ym_line_unlock_and_1arg_mock_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    aac = _load_aac_module(monkeypatch)
    fmt = aac._format_verify_crop_ym_line

    ym_v20_008 = {
        "n_clean_chars": 3,
        "pred_family": "MINCHO",
        "pred_conf": 0.97,
        "bold_prob": 0.97,
        "heavy_prob": 0.90,
        "top_fonts": [{"font": "A-OTF-RyuminPr6-Heavy.otf", "family": "MINCHO", "prob": 0.50}],
    }
    line_nk, _, mis_nk = fmt(
        ym_v20_008,
        ff="FZHei-B01",
        shw=2.0,
        reg_hw=1.5,
        stem_hw=2.10,
        bbox_wh=(26.0, 137.0),
        source_text="けけけ……",
        page_wh=(1350, 1920),
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_nk is False
    assert "[LOCKED>=0.90]" not in line_nk
    assert "HIGH-CONF YM MISMATCH" not in line_nk
    assert "[NARROW-KANA-STRETCH" in line_nk
    assert "rec=FZYaSong-DB-GBK" in line_nk

    ym_v20_051 = {
        "n_clean_chars": 6,
        "pred_family": "MINCHO",
        "pred_conf": 0.99,
        "bold_prob": 0.99,
        "heavy_prob": 0.69,
        "top_fonts": [{"font": "TelopMinProN-H.otf", "family": "MINCHO", "prob": 0.34}],
    }
    line_ts, _, mis_ts = fmt(
        ym_v20_051,
        ff="FZYaSong-DB-GBK",
        shw=2.0,
        reg_hw=2.0,
        stem_hw=2.08,
        bbox_wh=(109.0, 124.0),
        source_text="おいオシアン……！",
        page_wh=(1350, 1920),
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_ts is False
    assert "[LOCKED>=0.90]" not in line_ts
    assert "[THIN-STEM:" in line_ts and "no CuSong lock]" in line_ts
    assert "rec=FZYaSong-DB-GBK" in line_ts

    line_heavy, _, mis_heavy = fmt(
        {
            "n_clean_chars": 5,
            "pred_family": "MINCHO",
            "pred_conf": 0.99,
            "bold_prob": 1.0,
            "heavy_prob": 0.98,
            "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "family": "MINCHO", "prob": 0.85}],
        },
        ff="FZCuSong-B09",
        shw=3.8,
        reg_hw=2.0,
        stem_hw=4.20,
        bbox_wh=(80.0, 310.0),
        source_text="だが――",
        page_wh=(1350, 1920),
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_heavy is False
    assert "rec=FZCuSong-B09 [LOCKED>=0.90]" in line_heavy

    line_sogei, _, mis_sogei = fmt(
        {
            "n_clean_chars": 11,
            "pred_family": "POP_CHISEL",
            "pred_conf": 1.0,
            "bold_prob": 0.0,
            "heavy_prob": 0.0,
            "top_fonts": [{"font": "DFSoGeiStd-W5.otf", "family": "POP_CHISEL", "prob": 0.7534}],
        },
        ff="ZHSRXT2011-GB",
        shw=2.0,
        reg_hw=1.54,
        stem_hw=2.2,
        bbox_wh=(120.0, 124.0),
        source_text="いいや歌うのはガレスだ",
        page_wh=(1350, 1920),
        recommend_fn=recommend_mtui_font_for_prediction,
        classify_fn=classify_current_mtui_font,
    )
    assert mis_sogei is False
    assert "rec=ZHSRXT2011-GB [LOCKED>=0.90]" in line_sogei

    line_mock, _, mis_mock = fmt(
        ym_v20_051,
        ff="FZCuSong-B09",
        shw=2.0,
        reg_hw=2.0,
        stem_hw=2.08,
        bbox_wh=(109.0, 124.0),
        source_text="おいオシアン……！",
        page_wh=(1350, 1920),
        recommend_fn=lambda pred: ("FZCuSong-B09", False, {"MINCHO"}),
        classify_fn=classify_current_mtui_font,
    )
    assert mis_mock is False
    assert "rec=FZCuSong-B09 [LOCKED>=0.90]" in line_mock
