"""Regression tests for V14 post-mortem closed-loop hardening."""

import _bootstrap  # noqa: F401, I001

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_detector import CACHE_VERSION, region_bbox_signature
from mtui_mcp.local_inpaint import add_region_to_page
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import load_page_json, save_page_json_atomic, save_rgb_image_atomic
from mtui_mcp.qa_auditor import audit_single_page, diff_qa_audits, run_qa_audit


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
    spec = importlib.util.spec_from_file_location("audit_and_compare_v14_hardening", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fig2_no_bold_heavy_double_counting_false_error(tmp_path: Path) -> None:
    folder = tmp_path / "bold_doublecount_ch"
    folder.mkdir()
    img_path = folder / "007.png"
    page = np.full((500, 520, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[50.0, 60.0], [140.0, 60.0], [140.0, 260.0], [50.0, 260.0]]],
                    "text": "ボルグの親父にギルド…!?",
                    "translation": "博尔格老爹[BR]还有公会︙!?",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                },
                {
                    "lines": [[[160.0, 60.0], [250.0, 60.0], [250.0, 260.0], [160.0, 260.0]]],
                    "text": "待ってくれ！",
                    "translation": "等等我！",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                },
                {
                    "lines": [[[270.0, 60.0], [360.0, 60.0], [360.0, 260.0], [270.0, 260.0]]],
                    "text": "絶対に許さない！",
                    "translation": "绝对不可[BR]原谅！",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                },
                {
                    "lines": [[[380.0, 60.0], [470.0, 60.0], [470.0, 260.0], [380.0, 260.0]]],
                    "text": "行くぞみんな！",
                    "translation": "大家上吧！",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                },
            ]
        },
        auto_initial_snapshot=False,
    )
    ym_cache = {
        "by_ridx": {
            "0": {
                "bbox_sig": region_bbox_signature([50.0, 60.0, 140.0, 260.0]),
                "center": [95.0, 160.0],
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.522,
                "bold_prob": 0.7588,
                "heavy_prob": 0.1814,
                "family_probs": {"KAISHO_BRUSH": 0.522, "ANTIQUE": 0.273, "KAKU_GOTHIC": 0.15},
                "top_fonts": [{"font": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "prob": 0.253}],
            },
            "1": {
                "bbox_sig": region_bbox_signature([160.0, 60.0, 250.0, 260.0]),
                "center": [205.0, 160.0],
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.62,
                "bold_prob": 0.82,
                "heavy_prob": 0.10,
                "family_probs": {"KAKU_GOTHIC": 0.62, "ANTIQUE": 0.14},
                "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "prob": 0.32}],
            },
            "2": {
                "bbox_sig": region_bbox_signature([270.0, 60.0, 360.0, 260.0]),
                "center": [315.0, 160.0],
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.96,
                "bold_prob": 0.96,
                "heavy_prob": 0.0,
                "family_probs": {"KAKU_GOTHIC": 0.96, "ANTIQUE": 0.0},
                "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "prob": 0.72}],
            },
            "3": {
                "bbox_sig": region_bbox_signature([380.0, 60.0, 470.0, 260.0]),
                "center": [425.0, 160.0],
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.92,
                "bold_prob": 0.76,
                "heavy_prob": 0.18,
                "family_probs": {"KAKU_GOTHIC": 0.92, "ANTIQUE": 0.0},
                "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "prob": 0.72}],
            },
        }
    }
    rep = audit_single_page(
        resolve_page_paths(img_path),
        check_inpaint_cv=True,
        check_font_glyphs=False,
        default_alignment="left",
        yuzumarker_page_cache=ym_cache,
    )
    bold_by_r = {i["region_index"]: i["severity"] for i in rep["issues"] if i["category"] == "yuzumarker_bold_mismatch"}
    assert 0 not in bold_by_r
    assert bold_by_r.get(1) == "warning"
    assert bold_by_r.get(2) == "error"
    assert 3 not in bold_by_r, "Double-counting bold_prob + heavy_prob (0.76 + 0.18 = 0.94) would falsely emit error!"


def test_fig3_4_5_vertical_alignment_check_and_add_region_default(tmp_path: Path) -> None:
    folder = tmp_path / "align_ch"
    folder.mkdir()
    img_path = folder / "051.png"
    page = np.full((600, 500, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 60.0], [160.0, 60.0], [160.0, 340.0], [40.0, 340.0]]],
                    "text": "黙って潰れてろ",
                    "translation": "要么闭嘴[BR]等着被碾碎",
                    "font_family": "FZCuSong-B09",
                    "font_size": 34,
                    "direction": "v",
                    "alignment": "center",
                },
                {
                    "lines": [[[190.0, 60.0], [250.0, 60.0], [250.0, 260.0], [190.0, 260.0]]],
                    "text": "うわっ",
                    "translation": "哇啊",
                    "font_family": "FZHei-B01",
                    "font_size": 28,
                    "direction": "v",
                    "alignment": "center",
                },
                {
                    "lines": [[[280.0, 60.0], [390.0, 60.0], [390.0, 300.0], [280.0, 300.0]]],
                    "text": "ふっふざけんじゃないよぉ",
                    "translation": "别、[BR]别开玩笑[BR]啊︙!!",
                    "font_family": "FZHei-B01",
                    "font_size": 28,
                    "direction": "v",
                    "alignment": "left",
                },
                {
                    "lines": [[[60.0, 420.0], [420.0, 420.0], [420.0, 480.0], [60.0, 480.0]]],
                    "text": "第56話 暗派閥",
                    "translation": "第56话 暗派阀",
                    "font_family": "FZCuSong-B09",
                    "font_size": 36,
                    "direction": "h",
                    "alignment": "center",
                },
            ]
        },
        auto_initial_snapshot=False,
    )

    rep_left = audit_single_page(
        resolve_page_paths(img_path),
        check_inpaint_cv=False,
        check_font_glyphs=False,
        default_alignment="left",
    )
    al_by_r = {i["region_index"]: i["severity"] for i in rep_left["issues"] if i["category"] == "vertical_alignment_mismatch"}
    assert al_by_r == {0: "error", 1: "warning"}

    rep_center = audit_single_page(
        resolve_page_paths(img_path),
        check_inpaint_cv=False,
        check_font_glyphs=False,
        default_alignment="center",
    )
    assert not [i for i in rep_center["issues"] if i["category"] == "vertical_alignment_mismatch"]

    _, added_summary = add_region_to_page(
        img_path,
        bbox_xyxy=[410.0, 80.0, 470.0, 260.0],
        source_text="おい",
        translation="喂！",
        direction="v",
        auto_mask_ink=False,
        local_inpaint_method=None,
    )
    assert added_summary["alignment"] != "center"


def test_fig1_diff_qa_audits_and_true_1x_verify_crops_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_runtime_settings(),
        "load_mtui_config_dict",
        lambda: {"render": {"alignment": "left"}},
    )
    folder = tmp_path / "closed_loop_ch"
    folder.mkdir()
    img_path = folder / "V14_013.png"
    page = np.full((900, 900, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_rgb_image_atomic(page, paths.rendered_output_path)

    reg_good = {
        "lines": [[[180.0, 120.0], [640.0, 120.0], [640.0, 620.0], [180.0, 620.0]]],
        "text": "けどうちが本当に気になっとるのはこんなチャチなトリックやない",
        "translation": "不过我真正[BR]在意的[BR]可不是这种[BR]拙劣把戏",
        "font_family": "FZCuSong-B09",
        "font_size": 34,
        "direction": "v",
        "alignment": "left",
    }
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {"regions": [reg_good]},
        auto_initial_snapshot=False,
    )
    ym_file = paths.work_dir / ".yuzumarker_font_cache.json"
    ym_file.parent.mkdir(parents=True, exist_ok=True)
    ym_file.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "pages": {
                    "V14_013": {
                        "region_count": 1,
                        "by_ridx": {
                            "0": {
                                "ridx": 0,
                                "bbox_sig": region_bbox_signature([180.0, 120.0, 640.0, 620.0]),
                                "center": [410.0, 370.0],
                                "pred_family": "MINCHO",
                                "pred_conf": 1.0,
                                "bold_prob": 1.0,
                                "heavy_prob": 0.98,
                                "family_probs": {"MINCHO": 1.0, "ROUND_GOTHIC": 0.0},
                                "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "family": "MINCHO", "prob": 0.86}],
                            }
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    before_rep = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    assert before_rep["error_count"] == 0

    _, data = load_page_json(resolve_page_paths(img_path))
    data["regions"][0]["font_family"] = "FZLanTingYuan-B-GBK"
    data["regions"][0]["alignment"] = "center"
    save_page_json_atomic(resolve_page_paths(img_path), str(img_path.resolve()), data, auto_initial_snapshot=False)

    after_rep = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    diff = diff_qa_audits(before_rep, after_rep)
    assert diff["passed"] is False
    assert diff["newly_introduced_high_conf_yuzumarker_count"] == 1
    new_cats = {e["category"] for e in diff["newly_introduced_errors"]}
    assert "yuzumarker_font_mismatch" in new_cats
    assert "vertical_alignment_mismatch" in new_cats

    mod = _load_audit_cli_module(monkeypatch)
    vc_out = tmp_path / "vc_out"
    manifest = mod.cmd_verify_crops(folder, vc_out, pages=["V14_013"], cards_per_sheet=2)
    assert manifest["total_bubbles_exported"] == 1
    assert manifest["min_scale_used"] >= 1.0
    assert manifest["ym_high_conf_mismatch_flagged"] == 1
    assert manifest["alignment_flagged_bubbles"] == 1
    assert Path(manifest["sheets"][0]).is_file()


def test_r2_diff_qa_audits_scope_coverage_and_uid_dedup_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_runtime_settings(),
        "load_mtui_config_dict",
        lambda: {"render": {"alignment": "left"}},
    )
    folder = tmp_path / "scope_ch"
    folder.mkdir()
    page_arr = np.full((400, 400, 3), 255, dtype=np.uint8)
    p1 = folder / "001.png"
    p2 = folder / "002.png"
    Image.fromarray(page_arr).save(p1)
    Image.fromarray(page_arr).save(p2)
    paths1 = resolve_page_paths(p1)
    paths2 = resolve_page_paths(p2)
    save_rgb_image_atomic(page_arr, paths1.inpainted_path)
    save_rgb_image_atomic(page_arr, paths2.inpainted_path)

    save_page_json_atomic(
        paths1,
        str(p1.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 50.0], [160.0, 50.0], [160.0, 260.0], [40.0, 260.0]]],
                    "text": "大丈夫だ",
                    "translation": "没问题[BR]放心吧",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    save_page_json_atomic(
        paths2,
        str(p2.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 50.0], [160.0, 50.0], [160.0, 260.0], [40.0, 260.0]]],
                    "text": "行くぞ",
                    "translation": "走吧[BR]快跟上",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "center",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    before_rep = run_qa_audit(folder, check_inpaint_cv=False, check_font_glyphs=False)
    assert before_rep["audited_page_names"] == ["001.png", "002.png"]
    assert before_rep["error_count"] == 1

    partial_after = run_qa_audit(p1, check_inpaint_cv=False, check_font_glyphs=False)
    assert partial_after["audited_page_names"] == ["001.png"]
    diff_partial = diff_qa_audits(before_rep, partial_after)
    assert diff_partial["passed"] is False
    assert diff_partial["resolved_error_count"] == 0
    assert diff_partial["unaudited_pages"] == ["002.png"]
    assert diff_partial["scope_complete"] is False

    diff_scoped_ok = diff_qa_audits(before_rep, partial_after, pages={"001.png"})
    assert diff_scoped_ok["passed"] is True
    assert diff_scoped_ok["unaudited_pages"] == []

    diff_scoped_incomplete = diff_qa_audits(before_rep, partial_after, pages={"001.png", "002.png"})
    assert diff_scoped_incomplete["passed"] is False
    assert diff_scoped_incomplete["unaudited_pages"] == ["002.png"]

    paths2_fresh = resolve_page_paths(p2)
    _, d2 = load_page_json(paths2_fresh)
    d2["regions"][0]["alignment"] = "left"
    save_page_json_atomic(paths2_fresh, str(p2.resolve()), d2, auto_initial_snapshot=False)
    after_filtered = run_qa_audit(
        folder, check_inpaint_cv=False, check_font_glyphs=False, only_pages_with_issues=True
    )
    assert after_filtered["audited_page_names"] == ["001.png", "002.png"]
    diff_full_clean = diff_qa_audits(before_rep, after_filtered)
    assert diff_full_clean["passed"] is True
    assert diff_full_clean["resolved_error_count"] == 1
    assert diff_full_clean["unaudited_pages"] == []

    # R2-F39: UID-less entry must never absorb a subsequent distinct UID entry in `_collect_errors`
    before_uid_a = {
        "audited_page_names": ["p.png"],
        "pages": [
            {
                "page_name": "p.png",
                "issues": [
                    {
                        "severity": "error",
                        "category": "vertical_alignment_mismatch",
                        "region_index": 0,
                        "region_uid": "uid-A",
                        "region_bbox_sig": "sig-same",
                        "region_source_text": "同じ台詞",
                    }
                ],
            }
        ],
    }
    after_uidless_then_uid_b = {
        "audited_page_names": ["p.png"],
        "pages": [
            {
                "page_name": "p.png",
                "issues": [
                    {
                        "severity": "error",
                        "category": "vertical_alignment_mismatch",
                        "region_index": 0,
                        "region_bbox_sig": "sig-same",
                        "region_source_text": "同じ台詞",
                    },
                    {
                        "severity": "error",
                        "category": "vertical_alignment_mismatch",
                        "region_index": 1,
                        "region_uid": "uid-B",
                        "region_bbox_sig": "sig-same",
                        "region_source_text": "同じ台詞",
                    },
                ],
            }
        ],
    }
    diff_uid = diff_qa_audits(before_uid_a, after_uidless_then_uid_b)
    assert diff_uid["passed"] is False
    assert diff_uid["newly_introduced_error_count"] == 1
    assert diff_uid["newly_introduced_errors"][0]["region_uid"] == "uid-B"

    # Same region_index=0 emitted twice (first UID-less, then with uid-B) upgrades in-place to uid-B
    after_same_idx_upgrade = {
        "audited_page_names": ["p.png"],
        "pages": [
            {
                "page_name": "p.png",
                "issues": [
                    {
                        "severity": "error",
                        "category": "vertical_alignment_mismatch",
                        "region_index": 0,
                        "region_bbox_sig": "sig-same",
                        "region_source_text": "同じ台詞",
                    },
                    {
                        "severity": "error",
                        "category": "vertical_alignment_mismatch",
                        "region_index": 0,
                        "region_uid": "uid-B",
                        "region_bbox_sig": "sig-same",
                        "region_source_text": "同じ台詞",
                    },
                ],
            }
        ],
    }
    diff_up_vs_a = diff_qa_audits(before_uid_a, after_same_idx_upgrade)
    assert diff_up_vs_a["passed"] is False
    assert diff_up_vs_a["newly_introduced_error_count"] == 1
    assert diff_up_vs_a["newly_introduced_errors"][0]["region_uid"] == "uid-B"


def test_r3_diff_qa_audits_bipartite_order_invariance_path_identity_dedup_and_dotted_stems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_runtime_settings(),
        "load_mtui_config_dict",
        lambda: {"render": {"alignment": "left"}},
    )

    # R3-F07: Order-independent 1-to-1 bipartite fallback matching (UID-less item never starves constrained UID item)
    b_uidless = {
        "severity": "error",
        "category": "vertical_alignment_mismatch",
        "region_index": 0,
        "region_bbox_sig": "sig-shared",
        "region_source_text": "同じ台詞",
    }
    b_uid_a = {
        "severity": "error",
        "category": "vertical_alignment_mismatch",
        "region_index": 1,
        "region_uid": "uid-A",
        "region_bbox_sig": "sig-shared",
        "region_source_text": "同じ台詞",
    }
    a_uidless = {
        "severity": "error",
        "category": "vertical_alignment_mismatch",
        "region_index": 0,
        "region_bbox_sig": "sig-shared",
        "region_source_text": "同じ台詞",
    }
    a_uid_b = {
        "severity": "error",
        "category": "vertical_alignment_mismatch",
        "region_index": 1,
        "region_uid": "uid-B",
        "region_bbox_sig": "sig-shared",
        "region_source_text": "同じ台詞",
    }
    for b_issues in ([b_uidless, b_uid_a], [b_uid_a, b_uidless]):
        for a_issues in ([a_uidless, a_uid_b], [a_uid_b, a_uidless]):
            d_perm = diff_qa_audits(
                {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": b_issues}]},
                {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": a_issues}]},
            )
            assert d_perm["passed"] is True
            assert d_perm["newly_introduced_error_count"] == 0
            assert d_perm["resolved_error_count"] == 0

    # R3-F29: Complementary 1-UID overlap pairs `(A, None)` and `(None, B)` on same indices/sig/text are NOT merged
    pair_a_none = {
        "severity": "error",
        "category": "overlapping_regions",
        "region_index": 0,
        "other_region_index": 1,
        "overlap_pair_indices": [0, 1],
        "overlap_pair_uids": ["uid-A", None],
        "overlap_pair_bbox_sigs": ["sig-0", "sig-1"],
        "overlap_pair_source_texts": ["あ", "い"],
    }
    pair_none_b = {
        "severity": "error",
        "category": "overlapping_regions",
        "region_index": 0,
        "other_region_index": 1,
        "overlap_pair_indices": [0, 1],
        "overlap_pair_uids": [None, "uid-B"],
        "overlap_pair_bbox_sigs": ["sig-0", "sig-1"],
        "overlap_pair_source_texts": ["あ", "い"],
    }
    pair_none_a = {
        **pair_none_b,
        "overlap_pair_uids": [None, "uid-A"],
    }
    d_comp_diff = diff_qa_audits(
        {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": [pair_a_none]}]},
        {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": [pair_a_none, pair_none_b]}]},
    )
    assert d_comp_diff["after_error_count"] == 2
    assert d_comp_diff["newly_introduced_error_count"] == 1
    assert d_comp_diff["passed"] is False

    d_comp_same = diff_qa_audits(
        {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": [pair_a_none]}]},
        {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": [pair_a_none, pair_none_a]}]},
    )
    assert d_comp_same["after_error_count"] == 1
    assert d_comp_same["passed"] is True

    # R3-F28: Canonical page path identity distinguishes `ch01/001.png` from `ch02/001.png`
    page_arr = np.full((320, 320, 3), 255, dtype=np.uint8)
    ch1 = tmp_path / "ch01"
    ch2 = tmp_path / "ch02"
    ch1.mkdir()
    ch2.mkdir()
    p_ch1 = ch1 / "001.png"
    p_ch2 = ch2 / "001.png"
    Image.fromarray(page_arr).save(p_ch1)
    Image.fromarray(page_arr).save(p_ch2)
    paths_ch1 = resolve_page_paths(p_ch1)
    paths_ch2 = resolve_page_paths(p_ch2)
    save_rgb_image_atomic(page_arr, paths_ch1.inpainted_path)
    save_rgb_image_atomic(page_arr, paths_ch2.inpainted_path)
    save_page_json_atomic(
        paths_ch1,
        str(p_ch1.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 40.0], [160.0, 40.0], [160.0, 240.0], [40.0, 240.0]]],
                    "text": "行くぞ",
                    "translation": "走吧[BR]快跟上",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "center",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    save_page_json_atomic(
        paths_ch2,
        str(p_ch2.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 40.0], [160.0, 40.0], [160.0, 240.0], [40.0, 240.0]]],
                    "text": "大丈夫だ",
                    "translation": "没问题[BR]放心吧",
                    "font_family": "FZHei-B01",
                    "font_size": 26,
                    "direction": "v",
                    "alignment": "left",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    rep_ch1 = run_qa_audit(ch1, check_inpaint_cv=False, check_font_glyphs=False)
    rep_ch2 = run_qa_audit(ch2, check_inpaint_cv=False, check_font_glyphs=False, only_pages_with_issues=True)
    assert rep_ch1["audited_page_names"] == ["001.png"]
    assert rep_ch2["audited_page_names"] == ["001.png"]
    assert len(rep_ch1["audited_page_paths"]) == 1 and len(rep_ch2["audited_page_paths"]) == 1
    assert rep_ch1["audited_page_paths"] != rep_ch2["audited_page_paths"]

    diff_wrong_ch = diff_qa_audits(rep_ch1, rep_ch2)
    assert diff_wrong_ch["passed"] is False
    assert diff_wrong_ch["scope_complete"] is False
    assert diff_wrong_ch["resolved_error_count"] == 0
    assert len(diff_wrong_ch["unaudited_pages"]) == 1
    assert "ch01" in diff_wrong_ch["unaudited_pages"][0].lower()

    # R3-F60: Dotted stems like `pages={"001.final"}` satisfy scope coverage on `001.final.png`,
    # while explicit mismatched image extensions like `001.final.jpg` do NOT.
    dotted_dir = tmp_path / "dotted_ch"
    dotted_dir.mkdir()
    p_dot = dotted_dir / "001.final.png"
    Image.fromarray(page_arr).save(p_dot)
    paths_dot = resolve_page_paths(p_dot)
    save_rgb_image_atomic(page_arr, paths_dot.inpainted_path)
    save_page_json_atomic(paths_dot, str(p_dot.resolve()), {"regions": []}, auto_initial_snapshot=False)

    rep_dot_b = run_qa_audit(dotted_dir, check_inpaint_cv=False, check_font_glyphs=False)
    rep_dot_a = run_qa_audit(dotted_dir, check_inpaint_cv=False, check_font_glyphs=False)
    diff_dot_ok = diff_qa_audits(rep_dot_b, rep_dot_a, pages={"001.final"})
    assert diff_dot_ok["unaudited_pages"] == []
    assert diff_dot_ok["passed"] is True

    diff_dot_wrong_ext = diff_qa_audits(rep_dot_b, rep_dot_a, pages={"001.final.jpg"})
    assert diff_dot_wrong_ext["unaudited_pages"] == ["001.final.jpg"]
    assert diff_dot_wrong_ext["passed"] is False


def test_r4_diff_qa_audits_legacy_ambiguous_page_names_and_full_uid_pair_first_dedup(
    tmp_path: Path,
) -> None:
    import itertools

    # R4-F13: A single legacy pathless `001.png` entry MUST NEVER prove coverage of two same-named path-bearing pages
    # (`/ch01/001.png` and `/ch02/001.png`), while unambiguous legacy single-page reports continue to pair cleanly.
    p1_str = str((tmp_path / "ch01" / "001.png").resolve())
    p2_str = str((tmp_path / "ch02" / "001.png").resolve())
    p3_str = str((tmp_path / "ch01" / "002.png").resolve())

    before_two_same_name = {
        "audited_pages": 2,
        "audited_page_names": ["001.png", "001.png"],
        "audited_page_paths": [p1_str, p2_str],
        "pages": [
            {"page_name": "001.png", "source_path": p1_str, "issues": []},
            {"page_name": "001.png", "source_path": p2_str, "issues": []},
        ],
    }
    legacy_single_after = {
        "audited_pages": 1,
        "audited_page_names": ["001.png"],
        "pages": [{"page_name": "001.png", "issues": []}],
    }
    diff_ambig = diff_qa_audits(before_two_same_name, legacy_single_after)
    assert diff_ambig["scope_complete"] is False
    assert diff_ambig["passed"] is False
    assert len(diff_ambig["unaudited_pages"]) == 2

    # Symmetric reverse: legacy single `001.png` baseline vs two same-named path-bearing pages in `after`
    diff_ambig_rev = diff_qa_audits(legacy_single_after, before_two_same_name)
    assert diff_ambig_rev["scope_complete"] is False
    assert diff_ambig_rev["passed"] is False
    assert diff_ambig_rev["unaudited_pages"] == ["001.png"]

    # Mixed report: unambiguous `002.png` still pairs and resolves its error even when `001.png` is ambiguous
    before_mixed = {
        "audited_pages": 3,
        "audited_page_names": ["001.png", "001.png", "002.png"],
        "audited_page_paths": [p1_str, p2_str, p3_str],
        "pages": [
            {
                "page_name": "001.png",
                "source_path": p1_str,
                "issues": [{"severity": "error", "category": "vertical_alignment_mismatch", "region_index": 0}],
            },
            {
                "page_name": "002.png",
                "source_path": p3_str,
                "issues": [{"severity": "error", "category": "vertical_alignment_mismatch", "region_index": 0}],
            },
        ],
    }
    legacy_mixed_after = {
        "audited_pages": 2,
        "audited_page_names": ["001.png", "002.png"],
        "pages": [
            {"page_name": "001.png", "issues": []},
            {"page_name": "002.png", "issues": []},
        ],
    }
    diff_mixed = diff_qa_audits(before_mixed, legacy_mixed_after)
    assert diff_mixed["resolved_error_count"] == 1
    assert diff_mixed["scope_complete"] is False
    assert diff_mixed["passed"] is False
    assert len(diff_mixed["unaudited_pages"]) == 2

    # Single unambiguous legacy page continues to compare cleanly against a path-bearing baseline
    before_unambig = {
        "audited_pages": 1,
        "audited_page_names": ["001.png"],
        "audited_page_paths": [p1_str],
        "pages": [{"page_name": "001.png", "source_path": p1_str, "issues": []}],
    }
    diff_unambig_ok = diff_qa_audits(before_unambig, legacy_single_after)
    assert diff_unambig_ok["scope_complete"] is True
    assert diff_unambig_ok["passed"] is True
    assert diff_unambig_ok["unaudited_pages"] == []

    # R4-F14: Full 2-UID overlap pair `(uid-A, uid-B)` deduplicates before partial 1-UID pairs `(uid-A, None)` and
    # `(None, uid-B)` regardless of arrival order, while preserving complementary partial pairs when `(uid-A, uid-B)` is absent.
    pair_a_b = {
        "severity": "error",
        "category": "overlapping_regions",
        "region_index": 0,
        "other_region_index": 1,
        "overlap_pair_indices": [0, 1],
        "overlap_pair_uids": ["uid-A", "uid-B"],
        "overlap_pair_bbox_sigs": ["sig-0", "sig-1"],
        "overlap_pair_source_texts": ["あ", "い"],
    }
    pair_a_none = {**pair_a_b, "overlap_pair_uids": ["uid-A", None]}
    pair_none_b = {**pair_a_b, "overlap_pair_uids": [None, "uid-B"]}

    baseline_ab = {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": [pair_a_b]}]}
    for perm in itertools.permutations([pair_a_none, pair_none_b, pair_a_b]):
        diff_perm = diff_qa_audits(
            baseline_ab,
            {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": list(perm)}]},
        )
        assert diff_perm["after_error_count"] == 1
        assert diff_perm["newly_introduced_error_count"] == 0
        assert diff_perm["passed"] is True

    for partial_only in ([pair_a_none, pair_none_b], [pair_none_b, pair_a_none]):
        diff_partial_only = diff_qa_audits(
            baseline_ab,
            {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": partial_only}]},
        )
        assert diff_partial_only["after_error_count"] == 2
        assert diff_partial_only["newly_introduced_error_count"] == 1
        assert diff_partial_only["passed"] is False


def test_r5_diff_qa_audits_missing_font_glyphs_delta_regression() -> None:
    def _mk_glyph_rep(issues: list[dict]) -> dict:
        return {"audited_page_names": ["p.png"], "pages": [{"page_name": "p.png", "issues": issues}]}

    def _mk_glyph_iss(glyphs: list[str], *, uid: str | None = "uid-g1", ridx: int = 0, font: str = "DemoFont") -> dict:
        iss: dict = {
            "severity": "error",
            "category": "missing_font_glyphs",
            "region_index": ridx,
            "region_bbox_sig": "sig-g1",
            "region_source_text": "テスト",
            "font_family": font,
            "missing_glyphs": glyphs,
            "message": f"Region #{ridx}: font {font!r} is missing glyph(s) {''.join(glyphs)!r} — tofu!",
        }
        if uid is not None:
            iss["region_uid"] = uid
        return iss

    # 1. Unchanged missing glyphs (`['甲']` -> `['甲']`) passes with 0 regressions and 0 false resolutions
    d_same = diff_qa_audits(_mk_glyph_rep([_mk_glyph_iss(["甲"])]), _mk_glyph_rep([_mk_glyph_iss(["甲"])]))
    assert d_same["passed"] is True
    assert d_same["newly_introduced_error_count"] == 0
    assert d_same["resolved_error_count"] == 0

    # 2. Shrunk missing glyphs (`['甲', '乙']` -> `['甲']`) passes with 0 regressions
    d_shrunk = diff_qa_audits(_mk_glyph_rep([_mk_glyph_iss(["甲", "乙"])]), _mk_glyph_rep([_mk_glyph_iss(["甲"])]))
    assert d_shrunk["passed"] is True
    assert d_shrunk["newly_introduced_error_count"] == 0
    assert d_shrunk["resolved_error_count"] == 0

    # 3. Completely fixed missing glyphs (`['甲']` -> `[]`) passes and counts 1 resolved error
    d_fixed = diff_qa_audits(_mk_glyph_rep([_mk_glyph_iss(["甲"])]), _mk_glyph_rep([]))
    assert d_fixed["passed"] is True
    assert d_fixed["newly_introduced_error_count"] == 0
    assert d_fixed["resolved_error_count"] == 1

    # 4. Newly added missing glyph (`['甲']` -> `['甲', '乙']`) on same `(region, font)` MUST block `passed`
    # and report ONLY the newly introduced character `['乙']` without falsely resolving `['甲']`!
    d_added = diff_qa_audits(_mk_glyph_rep([_mk_glyph_iss(["甲"])]), _mk_glyph_rep([_mk_glyph_iss(["甲", "乙"])]))
    assert d_added["passed"] is False
    assert d_added["newly_introduced_error_count"] == 1
    assert d_added["resolved_error_count"] == 0
    assert d_added["newly_introduced_errors"][0]["missing_glyphs"] == ["乙"]
    assert d_added["newly_introduced_errors"][0]["newly_missing_glyphs"] == ["乙"]

    # 5. Swapped missing glyph (`['甲']` -> `['乙']`) also blocks `passed` with `['乙']`
    d_swapped = diff_qa_audits(_mk_glyph_rep([_mk_glyph_iss(["甲"])]), _mk_glyph_rep([_mk_glyph_iss(["乙"])]))
    assert d_swapped["passed"] is False
    assert d_swapped["newly_introduced_error_count"] == 1
    assert d_swapped["resolved_error_count"] == 0
    assert d_swapped["newly_introduced_errors"][0]["missing_glyphs"] == ["乙"]

    # 6. Pass 2 UID-less fallback prefers zero-delta edges across all permutations before reporting delta regressions
    b_u0 = _mk_glyph_iss(["甲"], uid=None, ridx=0)
    b_u1 = _mk_glyph_iss(["甲", "乙"], uid=None, ridx=1)
    a_u0 = _mk_glyph_iss(["甲"], uid=None, ridx=1)
    a_u1 = _mk_glyph_iss(["甲", "乙"], uid=None, ridx=0)
    for b_list in ([b_u0, b_u1], [b_u1, b_u0]):
        for a_list in ([a_u0, a_u1], [a_u1, a_u0]):
            d_perm_ok = diff_qa_audits(_mk_glyph_rep(b_list), _mk_glyph_rep(a_list))
            assert d_perm_ok["passed"] is True
            assert d_perm_ok["newly_introduced_error_count"] == 0

    a_u1_reg = _mk_glyph_iss(["甲", "乙", "丙"], uid=None, ridx=0)
    for b_list in ([b_u0, b_u1], [b_u1, b_u0]):
        for a_list in ([a_u0, a_u1_reg], [a_u1_reg, a_u0]):
            d_perm_reg = diff_qa_audits(_mk_glyph_rep(b_list), _mk_glyph_rep(a_list))
            assert d_perm_reg["passed"] is False
            assert d_perm_reg["newly_introduced_error_count"] == 1
            assert d_perm_reg["resolved_error_count"] == 0
            assert d_perm_reg["newly_introduced_errors"][0]["missing_glyphs"] == ["丙"]
