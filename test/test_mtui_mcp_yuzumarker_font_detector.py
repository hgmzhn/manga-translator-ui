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


def test_lookup_cached_region_prediction_rejects_bare_ridx_and_invalid_region_count() -> None:
    reg_a = {"lines": [[[20.0, 20.0], [120.0, 20.0], [120.0, 220.0], [20.0, 220.0]]], "text": "あ"}
    reg_b = {"lines": [[[180.0, 20.0], [280.0, 20.0], [280.0, 220.0], [180.0, 220.0]]], "text": "い"}

    # R2-F36: Bare by_ridx without region_uid / bbox_sig / center MUST return None even when total_regions matches
    bare_cache = {
        "region_count": 2,
        "by_ridx": {
            "0": {"pred_family": "POP_CHISEL", "pred_conf": 0.99},
            "1": {"pred_family": "ANTIQUE", "pred_conf": 0.99},
        },
    }
    assert lookup_cached_region_prediction(bare_cache, reg_a, 0) is None
    assert lookup_cached_region_prediction(bare_cache, reg_a, 0, total_regions=2) is None
    assert lookup_cached_region_prediction({"by_ridx": {"0": {"pred_family": "POP_CHISEL"}}}, reg_a, 0, total_regions=1) is None

    # Verified center / bbox_sig / region_uid hits succeed on original order and reject swapped order
    center_cache = {
        "region_count": 2,
        "by_ridx": {
            "0": {"center": [70.0, 120.0], "pred_family": "POP_CHISEL", "pred_conf": 0.99},
            "1": {"center": [230.0, 120.0], "pred_family": "ANTIQUE", "pred_conf": 0.99},
        },
    }
    assert lookup_cached_region_prediction(center_cache, reg_a, 0, total_regions=2) is not None
    assert lookup_cached_region_prediction(center_cache, reg_b, 0, total_regions=2) is None

    uid_cache = {
        "region_count": 1,
        "by_ridx": {
            "0": {
                "region_uid": "uid-1",
                "bbox_sig": region_bbox_signature([20.0, 20.0, 120.0, 220.0]),
                "pred_family": "MINCHO",
                "pred_conf": 0.95,
            }
        },
    }
    assert lookup_cached_region_prediction(
        uid_cache, {**reg_a, "region_uid": "uid-1", "translation": "仅修改译文"}, 0, total_regions=1
    ) is not None
    assert lookup_cached_region_prediction(uid_cache, {**reg_a, "region_uid": "uid-2"}, 0, total_regions=1) is None
    # R3-F05: Same region_uid moved to a disjoint bubble crop MUST return None
    assert lookup_cached_region_prediction(uid_cache, {**reg_b, "region_uid": "uid-1"}, 0, total_regions=1) is None
    bare_uid_cache = {
        "region_count": 1,
        "by_ridx": {"0": {"region_uid": "uid-1", "pred_family": "MINCHO", "pred_conf": 0.95}},
    }
    assert lookup_cached_region_prediction(bare_uid_cache, {**reg_a, "region_uid": "uid-1"}, 0, total_regions=1) is None

    # R2-F79: Malformed non-int region_count is treated as cache miss without raising ValueError
    for bad_rc in ("unknown", "", True, -1, []):
        malformed_rc_cache = {
            "region_count": bad_rc,
            "by_ridx": {"0": {"center": [70.0, 120.0], "pred_family": "POP_CHISEL", "pred_conf": 0.99}},
        }
        assert lookup_cached_region_prediction(malformed_rc_cache, reg_a, 0, total_regions=1) is None


def test_r3_overflow_center_malformed_pages_and_moved_uid_refresh(tmp_path, monkeypatch) -> None:
    import json
    from pathlib import Path

    from PIL import Image

    import mtui_mcp.qa_auditor as qa_mod
    import mtui_mcp.worker.client as worker_client_mod
    from mtui_mcp.font_detector import CACHE_VERSION, load_font_detection_cache
    from mtui_mcp.path_resolver import resolve_page_paths
    from mtui_mcp.project_store import (
        load_page_json,
        save_page_json_atomic,
        save_rgb_image_atomic,
    )
    from mtui_mcp.qa_auditor import audit_single_page, run_qa_audit

    reg_a = {"lines": [[[20.0, 20.0], [120.0, 20.0], [120.0, 220.0], [20.0, 220.0]]], "text": "あ"}
    # R3-F54: Oversized JSON integers in `center` must not raise OverflowError
    for huge_center in ([10**400, 120.0], [70.0, -(10**400)]):
        overflow_cache = {
            "region_count": 1,
            "by_ridx": {"0": {"center": huge_center, "pred_family": "POP_CHISEL", "pred_conf": 0.99}},
        }
        assert lookup_cached_region_prediction(overflow_cache, reg_a, 0, total_regions=1) is None

    folder = Path(tmp_path) / "r3_ym_ch"
    folder.mkdir()
    img_p = folder / "001.png"
    page = np.full((320, 360, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_p)
    paths = resolve_page_paths(img_p)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_p.resolve()),
        {
            "regions": [
                {
                    "region_uid": "uid-move-1",
                    "lines": [[[20.0, 20.0], [120.0, 20.0], [120.0, 220.0], [20.0, 220.0]]],
                    "text": "テストです",
                    "translation": "测试ましろ",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    # R3-F27: Non-dict `pages` (e.g. `["001"]`) is treated as cache miss in loader, single-page, and batch QA
    ym_file = paths.work_dir / ".yuzumarker_font_cache.json"
    ym_file.parent.mkdir(parents=True, exist_ok=True)
    ym_file.write_text(json.dumps({"version": CACHE_VERSION, "pages": ["001"]}), encoding="utf-8")
    assert load_font_detection_cache(paths.work_dir) == {}

    refresh_calls: list[bool] = []

    class _RecordingWorker:
        def detect_fonts(self, target_path, force_refresh=False):
            refresh_calls.append(bool(force_refresh))
            return {"status": "ok"}

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(qa_mod, "yuzumarker_models_available", lambda: True)
    monkeypatch.setattr(worker_client_mod, "get_worker_client", lambda: _RecordingWorker())

    # Even if load_font_detection_cache returns malformed `pages: ["001"]`, single-page QA keeps other checks
    # without AttributeError and batch QA triggers refresh.
    monkeypatch.setattr(
        qa_mod,
        "load_font_detection_cache",
        lambda _wdir: {"version": CACHE_VERSION, "pages": ["001"]},
    )
    single_rep = audit_single_page(resolve_page_paths(img_p), check_inpaint_cv=True, check_font_glyphs=False)
    assert any(i["category"] == "untranslated_kana" for i in single_rep["issues"])
    batch_rep = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 1
    assert any(i["category"] == "untranslated_kana" for i in batch_rep["pages"][0]["issues"])
    monkeypatch.setattr(qa_mod, "load_font_detection_cache", load_font_detection_cache)

    # R3-F05: Moving `uid-move-1` to a disjoint bubble triggers refresh in batch QA and never reuses old crop font
    ym_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "pages": {
                    "001": {
                        "region_count": 1,
                        "by_bbox_sig": {
                            region_bbox_signature([20.0, 20.0, 120.0, 220.0]): {
                                "ridx": 0,
                                "region_uid": "uid-move-1",
                                "bbox_sig": region_bbox_signature([20.0, 20.0, 120.0, 220.0]),
                                "center": [70.0, 120.0],
                                "pred_family": "POP_CHISEL",
                                "pred_conf": 0.99,
                                "bold_prob": 1.0,
                                "heavy_prob": 0.0,
                                "family_probs": {"POP_CHISEL": 0.99},
                                "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.96}],
                            }
                        },
                        "by_ridx": {
                            "0": {
                                "ridx": 0,
                                "region_uid": "uid-move-1",
                                "bbox_sig": region_bbox_signature([20.0, 20.0, 120.0, 220.0]),
                                "center": [70.0, 120.0],
                                "pred_family": "POP_CHISEL",
                                "pred_conf": 0.99,
                                "bold_prob": 1.0,
                                "heavy_prob": 0.0,
                                "family_probs": {"POP_CHISEL": 0.99},
                                "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.96}],
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    refresh_calls.clear()
    rep_before_move = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 0
    assert any(i["category"] == "yuzumarker_font_mismatch" for i in rep_before_move["pages"][0]["issues"])

    _, jdata = load_page_json(resolve_page_paths(img_p))
    jdata["regions"][0]["lines"] = [[[210.0, 20.0], [310.0, 20.0], [310.0, 220.0], [210.0, 220.0]]]
    jdata["regions"][0]["translation"] = "测试对白"
    save_page_json_atomic(resolve_page_paths(img_p), str(img_p.resolve()), jdata, auto_initial_snapshot=False)

    rep_after_move = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 1
    assert not any(i["category"] == "yuzumarker_font_mismatch" for i in rep_after_move["pages"][0]["issues"])


def test_r3_font_catalog_shared_dir_index_lazy_system_and_order_consistency(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    import mtui_mcp.font_catalog as fc_mod
    from mtui_mcp.config import get_runtime_settings

    fake_root = Path(tmp_path) / "fc_root"
    fonts_dir = fake_root / "fonts"
    sub_dir = fonts_dir / "A"
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", fake_root)

    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((800, 100))
    pen.lineTo((800, 800))
    pen.closePath()
    g_box = pen.glyph()

    def _build_ttf(dest: Path, fam: str, chars: str) -> None:
        fb = FontBuilder(1000, isTTF=True)
        fb.setupGlyphOrder([".notdef", "box"])
        fb.setupCharacterMap({ord(c): "box" for c in chars})
        fb.setupGlyf({".notdef": g_box, "box": g_box})
        fb.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100)})
        fb.setupHorizontalHeader(ascent=800, descent=-200)
        fb.setupNameTable({"familyName": fam, "styleName": "Regular"})
        fb.setupOS2()
        fb.setupPost()
        fb.save(str(dest))

    # R3-F62: Root `Z.ttf` (covers 甲乙) vs subdir `A/Sub.ttf` (covers only 乙) for the same family `DemoUnified`
    _build_ttf(fonts_dir / "Z.ttf", "DemoUnified", "甲乙")
    _build_ttf(sub_dir / "Sub.ttf", "DemoUnified", "乙")
    _build_ttf(fonts_dir / "SecondFam.ttf", "SecondFam", "丙")

    listed = fc_mod.list_available_fonts(include_system=False)
    demo_items = [item for item in listed if item["font_family"] == "DemoUnified"]
    assert len(demo_items) == 1
    assert demo_items[0]["file_name"] == "Z.ttf"
    assert fc_mod.check_missing_glyphs("DemoUnified", "甲乙", include_system=False) == []

    # R3-F61 / R4-F07: Shared single-walk directory index across distinct selectors + zero system font enumeration on project hit
    iter_calls: list[Path] = []
    sys_dir_calls: list[bool] = []
    orig_scan = fc_mod._scan_font_dir_snapshot

    def _spy_scan(d: Path):
        iter_calls.append(d)
        return orig_scan(d)

    def _spy_sys_dirs():
        sys_dir_calls.append(True)
        return []

    fc_mod._FONT_DIR_INDEX_CACHE.clear()
    fc_mod._FONT_DIR_SNAPSHOTS_BY_TOKEN.clear()
    fc_mod._FONT_DIR_PATHS_BY_TOKEN.clear()
    fc_mod._resolve_target_font_codepoints.cache_clear()
    monkeypatch.setattr(fc_mod, "_scan_font_dir_snapshot", _spy_scan)
    monkeypatch.setattr(fc_mod, "_system_font_dirs", _spy_sys_dirs)

    assert fc_mod.check_missing_glyphs("DemoUnified", "甲", include_system=True) == []
    assert fc_mod.check_missing_glyphs("SecondFam", "丙", include_system=True) == []
    assert len(iter_calls) == 1
    assert sys_dir_calls == []

    # Updating or adding a font file refreshes the shared index immediately (even within the same millisecond)
    _build_ttf(fonts_dir / "ThirdFam.ttf", "ThirdFam", "丁")
    _build_ttf(fonts_dir / "SecondFam.ttf", "SecondFam", "丙戊")
    assert fc_mod.has_font("ThirdFam", include_system=False) is True
    # R4-F48: Include unmapped '己' so the assertion proves both `SecondFam` lookup hit AND `'戊'` coverage!
    assert fc_mod.check_missing_glyphs("SecondFam", "丙戊己", include_system=False) == ["己"]
    assert len(iter_calls) == 2


def test_r4_font_catalog_single_walk_consistency_eviction_and_cold_system_perf(tmp_path, monkeypatch) -> None:
    import os
    import time
    from pathlib import Path

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    import mtui_mcp.font_catalog as fc_mod
    from mtui_mcp.config import get_runtime_settings

    fake_root = Path(tmp_path) / "r4_fc_root"
    fonts_dir = fake_root / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", fake_root)

    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((800, 100))
    pen.lineTo((800, 800))
    pen.closePath()
    g_box = pen.glyph()

    def _build_ttf(dest: Path, fam: str, chars: str) -> None:
        fb = FontBuilder(1000, isTTF=True)
        fb.setupGlyphOrder([".notdef", "box"])
        fb.setupCharacterMap({ord(c): "box" for c in chars})
        fb.setupGlyf({".notdef": g_box, "box": g_box})
        fb.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100)})
        fb.setupHorizontalHeader(ascent=800, descent=-200)
        fb.setupNameTable({"familyName": fam, "styleName": "Regular"})
        fb.setupOS2()
        fb.setupPost()
        fb.save(str(dest))

    _build_ttf(fonts_dir / "BaseFam.ttf", "BaseFam", "甲乙")

    fc_mod._FONT_DIR_INDEX_CACHE.clear()
    fc_mod._FONT_DIR_SNAPSHOTS_BY_TOKEN.clear()
    fc_mod._FONT_DIR_PATHS_BY_TOKEN.clear()
    fc_mod._resolve_target_font_codepoints.cache_clear()

    # R4-F07: Simulate a new font installed mid-scan right after `os.scandir` reads initial directory entries.
    # The mid-mutation scan MUST NOT be published as a verified index or poison `@lru_cache`, and the next call
    # MUST immediately see `MidScanFam`.
    real_scandir = os.scandir
    injected = {"done": False}
    norm_fonts_key = os.path.normcase(os.path.abspath(str(fonts_dir)))

    class _InjectAfterScandirCtx:
        def __init__(self, inner_ctx):
            self._inner = inner_ctx

        def __enter__(self):
            entries = list(self._inner.__enter__())
            if not injected["done"]:
                injected["done"] = True
                _build_ttf(fonts_dir / "MidScanFam.ttf", "MidScanFam", "庚辛")
            return iter(entries)

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

    def _mutating_scandir(p):
        if os.path.normcase(os.path.abspath(str(p))) == norm_fonts_key and not injected["done"]:
            return _InjectAfterScandirCtx(real_scandir(p))
        return real_scandir(p)

    monkeypatch.setattr(fc_mod.os, "scandir", _mutating_scandir)
    assert fc_mod.has_font("MidScanFam", include_system=False) is False
    assert norm_fonts_key not in fc_mod._FONT_DIR_INDEX_CACHE
    assert fc_mod._resolve_target_font_codepoints.cache_info().currsize == 0

    monkeypatch.setattr(fc_mod.os, "scandir", real_scandir)
    assert fc_mod.has_font("MidScanFam", include_system=False) is True
    assert fc_mod.check_missing_glyphs("MidScanFam", "庚辛壬", include_system=False) == ["壬"]
    assert norm_fonts_key in fc_mod._FONT_DIR_INDEX_CACHE
    assert fc_mod._FONT_DIR_INDEX_CACHE[norm_fonts_key].verified is True

    # R4-F43 + Contract #5: Evicted `_FONT_DIR_SNAPSHOTS_BY_TOKEN` tokens never act as empty `()` or poison `@lru_cache`,
    # while `.cache_clear()` and `.cache_info()` remain preserved on `_resolve_target_font_codepoints`.
    tokens = fc_mod._get_font_dirs_tokens([fonts_dir])
    assert len(tokens) == 1
    fc_mod._resolve_target_font_codepoints.cache_clear()
    assert fc_mod._resolve_target_font_codepoints.cache_info().currsize == 0

    # Evict `tokens[0]` by inserting > `_MAX_DIR_SNAPSHOT_TOKENS` synthetic tokens AND clearing `_FONT_DIR_INDEX_CACHE`
    for idx in range(fc_mod._MAX_DIR_SNAPSHOT_TOKENS + 8):
        fc_mod._remember_snapshot_for_token(f"evict_tok_{idx}", ())
    assert tokens[0] not in fc_mod._FONT_DIR_SNAPSHOTS_BY_TOKEN
    fc_mod._FONT_DIR_INDEX_CACHE.clear()

    resolved_after_evict = fc_mod._resolve_target_font_codepoints(tokens, "project", 0, "midscanfam", None)
    assert resolved_after_evict is not None
    assert resolved_after_evict[1] is not None and ord("庚") in resolved_after_evict[1]

    # Unminted/unknown missing token must NOT populate `@lru_cache` with a poisoned `None`
    fc_mod._resolve_target_font_codepoints.cache_clear()
    assert fc_mod._resolve_target_font_codepoints(("unknown_missing_token",), "project", 0, "midscanfam", None) is None
    assert fc_mod._resolve_target_font_codepoints.cache_info().currsize == 0

    # Preserve <500ms cold Windows system font lookup performance
    sys_dirs = fc_mod._system_font_dirs()
    if sys_dirs:
        fc_mod._FONT_DIR_INDEX_CACHE.clear()
        fc_mod._FONT_DIR_SNAPSHOTS_BY_TOKEN.clear()
        fc_mod._FONT_DIR_PATHS_BY_TOKEN.clear()
        fc_mod._match_font_names_only.cache_clear()
        fc_mod._inspect_font_file.cache_clear()
        fc_mod._resolve_target_font_codepoints.cache_clear()
        t0 = time.perf_counter()
        _ = fc_mod.has_font("Arial", include_system=True)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"Cold system font lookup took {elapsed:.3f}s (>= 0.500s)"


def test_r4_ym_cache_validates_base_image_sig_and_rejects_unicode_digit_keys(tmp_path, monkeypatch) -> None:
    import json
    from pathlib import Path

    from PIL import Image

    import mtui_mcp.qa_auditor as qa_mod
    import mtui_mcp.worker.client as worker_client_mod
    from mtui_mcp.config import get_runtime_settings
    from mtui_mcp.font_detector import CACHE_VERSION
    from mtui_mcp.path_resolver import resolve_page_paths
    from mtui_mcp.project_store import (
        load_page_json,
        save_page_json_atomic,
        save_rgb_image_atomic,
    )
    from mtui_mcp.qa_auditor import audit_single_page, run_qa_audit

    monkeypatch.setattr(
        get_runtime_settings(),
        "load_mtui_config_dict",
        lambda: {"app": {"last_output_path": ""}, "cli": {}},
    )

    reg_0 = {"lines": [[[20.0, 20.0], [120.0, 20.0], [120.0, 220.0], [20.0, 220.0]]], "text": "あ"}
    # R4-F12: Unicode superscript digit `"²"` (`"²".isdigit() is True`, `int("²")` -> `ValueError`) in `by_ridx`
    # MUST return `None` cleanly without raising `ValueError`, while valid ASCII keys still match by center.
    assert "²".isdigit() is True
    bad_unicode_ridx_cache = {
        "region_count": 2,
        "by_ridx": {
            "0": {"center": [70.0, 120.0], "pred_family": "POP_CHISEL", "pred_conf": 0.99},
            "²": {"center": [230.0, 120.0], "pred_family": "ANTIQUE", "pred_conf": 0.99},
        },
    }
    assert lookup_cached_region_prediction(bad_unicode_ridx_cache, reg_0, 0, total_regions=2) is None
    assert lookup_cached_region_prediction(bad_unicode_ridx_cache, reg_0, 0) is None

    folder = Path(tmp_path) / "r4_ym_ch"
    folder.mkdir()
    img_p = folder / "001.png"
    page_v1 = np.full((320, 360, 3), 255, dtype=np.uint8)
    Image.fromarray(page_v1).save(img_p)
    paths = resolve_page_paths(img_p)
    save_rgb_image_atomic(page_v1, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_p.resolve()),
        {
            "regions": [
                {
                    "region_uid": "uid-same-1",
                    "lines": [[[20.0, 20.0], [120.0, 20.0], [120.0, 220.0], [20.0, 220.0]]],
                    "text": "テストです",
                    "translation": "测试ましろ",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    ym_file = paths.work_dir / ".yuzumarker_font_cache.json"
    ym_file.parent.mkdir(parents=True, exist_ok=True)
    ym_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "pages": {
                    "001": {
                        "region_count": 1,
                        "by_ridx": {
                            "0": {"center": [70.0, 120.0], "pred_family": "POP_CHISEL", "pred_conf": 0.99},
                            "²": {"center": [200.0, 120.0], "pred_family": "ANTIQUE", "pred_conf": 0.99},
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    refresh_calls: list[bool] = []
    worker_mode = {"fail": False, "update_to_antique": False}

    class _FakeWorker:
        def detect_fonts(self, target_path, force_refresh=False):
            refresh_calls.append(bool(force_refresh))
            if worker_mode["fail"]:
                raise RuntimeError("simulated worker failure")
            if worker_mode["update_to_antique"]:
                cur_paths = resolve_page_paths(img_p)
                st_now = cur_paths.display_base_path.stat()
                bsig = region_bbox_signature([20.0, 20.0, 120.0, 220.0])
                rec = {
                    "ridx": 0,
                    "region_uid": "uid-same-1",
                    "bbox_sig": bsig,
                    "center": [70.0, 120.0],
                    "pred_family": "ANTIQUE",
                    "pred_conf": 0.99,
                    "bold_prob": 0.2,
                    "heavy_prob": 0.0,
                    "family_probs": {"ANTIQUE": 0.99},
                    "top_fonts": [{"font": "FOT-AnticCezannePro-DB.otf", "family": "ANTIQUE", "prob": 0.96}],
                }
                ym_file.write_text(
                    json.dumps(
                        {
                            "version": CACHE_VERSION,
                            "pages": {
                                "001": {
                                    "base_image_sig": [int(st_now.st_size), int(st_now.st_mtime_ns)],
                                    "display_base_name": cur_paths.display_base_path.name,
                                    "region_count": 1,
                                    "total_regions": 1,
                                    "by_bbox_sig": {bsig: rec},
                                    "by_ridx": {"0": rec},
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            return {"status": "ok"}

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(qa_mod, "yuzumarker_models_available", lambda: True)
    monkeypatch.setattr(worker_client_mod, "get_worker_client", lambda: _FakeWorker())

    # R4-F12 end-to-end: `"²"` in `by_ridx` triggers clean refresh instead of crashing chapter audit!
    rep_bad_idx = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 1
    assert any(i["category"] == "untranslated_kana" for i in rep_bad_idx["pages"][0]["issues"])

    # R4-F11: Populate worker-style cache with `base_image_sig` & `display_base_name` matching `page_v1`
    st_v1 = resolve_page_paths(img_p).display_base_path.stat()
    bsig0 = region_bbox_signature([20.0, 20.0, 120.0, 220.0])
    pop_rec = {
        "ridx": 0,
        "region_uid": "uid-same-1",
        "bbox_sig": bsig0,
        "center": [70.0, 120.0],
        "pred_family": "POP_CHISEL",
        "pred_conf": 0.99,
        "bold_prob": 1.0,
        "heavy_prob": 0.0,
        "family_probs": {"POP_CHISEL": 0.99},
        "top_fonts": [{"font": "FOT-ComicReggaeStd-B.otf", "family": "POP_CHISEL", "prob": 0.96}],
    }
    ym_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "pages": {
                    "001": {
                        "base_image_sig": [int(st_v1.st_size), int(st_v1.st_mtime_ns)],
                        "display_base_name": "001.png",
                        "region_count": 1,
                        "total_regions": 1,
                        "by_bbox_sig": {bsig0: pop_rec},
                        "by_ridx": {"0": pop_rec},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Editing ONLY translation reuses cache (`refresh_calls == 0`) while keeping `yuzumarker_font_mismatch` active
    _, j_edit = load_page_json(resolve_page_paths(img_p))
    j_edit["regions"][0]["translation"] = "仅改译文あ"
    save_page_json_atomic(resolve_page_paths(img_p), str(img_p.resolve()), j_edit, auto_initial_snapshot=False)
    refresh_calls.clear()
    rep_same_img = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 0
    assert any(i["category"] == "yuzumarker_font_mismatch" for i in rep_same_img["pages"][0]["issues"])

    # Replace `001.png` in-place with a different image (different dimensions/bytes -> different `st_size`)
    # while keeping `uid-same-1`, `lines`, and `.yuzumarker_font_cache.json` unchanged!
    rng = np.random.default_rng(42)
    page_v2 = rng.integers(200, 256, size=(360, 400, 3), dtype=np.uint8)
    Image.fromarray(page_v2).save(img_p)
    save_rgb_image_atomic(page_v2, resolve_page_paths(img_p).inpainted_path)
    assert resolve_page_paths(img_p).display_base_path.stat().st_size != st_v1.st_size

    # 1) When refresh fails, degrade cleanly: keep other checks (`untranslated_kana`), NEVER reuse stale `POP_CHISEL`!
    refresh_calls.clear()
    worker_mode["fail"] = True
    rep_degraded = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 1
    assert rep_degraded.get("yuzumarker_scan_degraded") is True
    assert any(i["category"] == "untranslated_kana" for i in rep_degraded["pages"][0]["issues"])
    assert not any(i["category"] == "yuzumarker_font_mismatch" for i in rep_degraded["pages"][0]["issues"])

    single_stale = audit_single_page(resolve_page_paths(img_p), check_inpaint_cv=True, check_font_glyphs=False)
    assert not any(i["category"] == "yuzumarker_font_mismatch" for i in single_stale["issues"])

    # 2) When refresh succeeds, worker updates cache for `page_v2` (`ANTIQUE` matches `FZHei-B01`)
    refresh_calls.clear()
    worker_mode["fail"] = False
    worker_mode["update_to_antique"] = True
    rep_refreshed = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert len(refresh_calls) == 1
    assert not rep_refreshed.get("yuzumarker_scan_degraded")
    assert not any(i["category"] == "yuzumarker_font_mismatch" for i in rep_refreshed["pages"][0]["issues"])
