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

    # R3-F61: Shared directory index across distinct selectors + zero system font enumeration on project hit
    iter_calls: list[Path] = []
    sys_dir_calls: list[bool] = []
    orig_iter = fc_mod._iter_font_files

    def _spy_iter(d: Path):
        iter_calls.append(d)
        return orig_iter(d)

    def _spy_sys_dirs():
        sys_dir_calls.append(True)
        return []

    fc_mod._FONT_DIR_INDEX_CACHE.clear()
    fc_mod._FONT_DIR_SNAPSHOTS_BY_TOKEN.clear()
    fc_mod._resolve_target_font_codepoints.cache_clear()
    monkeypatch.setattr(fc_mod, "_iter_font_files", _spy_iter)
    monkeypatch.setattr(fc_mod, "_system_font_dirs", _spy_sys_dirs)

    assert fc_mod.check_missing_glyphs("DemoUnified", "甲", include_system=True) == []
    assert fc_mod.check_missing_glyphs("SecondFam", "丙", include_system=True) == []
    assert len(iter_calls) == 1
    assert sys_dir_calls == []

    # Updating or adding a font file refreshes the shared index immediately (even within the same millisecond)
    _build_ttf(fonts_dir / "ThirdFam.ttf", "ThirdFam", "丁")
    _build_ttf(fonts_dir / "SecondFam.ttf", "SecondFam", "丙戊")
    assert fc_mod.has_font("ThirdFam", include_system=False) is True
    assert fc_mod.check_missing_glyphs("SecondFam", "丙戊", include_system=False) == []
    assert len(iter_calls) == 2
