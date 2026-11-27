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
    page = np.full((500, 400, 3), 255, dtype=np.uint8)
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
            ]
        },
        auto_initial_snapshot=False,
    )
    ym_cache = {
        "by_ridx": {
            "0": {
                "pred_family": "KAISHO_BRUSH",
                "pred_conf": 0.522,
                "bold_prob": 0.7588,
                "heavy_prob": 0.1814,
                "family_probs": {"KAISHO_BRUSH": 0.522, "ANTIQUE": 0.273, "KAKU_GOTHIC": 0.15},
                "top_fonts": [{"font": "A-OTF-KaishoMCBK1Pro-DeBold.otf", "prob": 0.253}],
            },
            "1": {
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.62,
                "bold_prob": 0.82,
                "heavy_prob": 0.10,
                "family_probs": {"KAKU_GOTHIC": 0.62, "ANTIQUE": 0.14},
                "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "prob": 0.32}],
            },
            "2": {
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.96,
                "bold_prob": 0.96,
                "heavy_prob": 0.0,
                "family_probs": {"KAKU_GOTHIC": 0.96, "ANTIQUE": 0.0},
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
                "pages": {
                    "V14_013": {
                        "by_ridx": {
                            "0": {
                                "ridx": 0,
                                "pred_family": "MINCHO",
                                "pred_conf": 1.0,
                                "bold_prob": 1.0,
                                "heavy_prob": 0.98,
                                "family_probs": {"MINCHO": 1.0, "ROUND_GOTHIC": 0.0},
                                "top_fonts": [{"font": "A-OTF-RyuminPr5-Ultra.otf", "family": "MINCHO", "prob": 0.86}],
                            }
                        }
                    }
                }
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
