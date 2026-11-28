"""Regression tests for the 5 Sword-Oratoria V14 failures (`图1` ~ `图5`)."""

import _bootstrap  # noqa: F401, I001

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mtui_mcp.local_inpaint import (
    apply_incremental_local_repair,
    apply_mask_operations,
    merge_regions_on_page,
)
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, save_page_json_atomic, save_rgb_image_atomic
from mtui_mcp.qa_auditor import run_qa_audit


def test_fig2_fig3_connected_two_chamber_bubble_blocks_merge_and_flags_overmerge(tmp_path: Path) -> None:
    folder = tmp_path / "cusp_ch"
    folder.mkdir(parents=True)
    img_path = folder / "012.png"

    page = np.full((600, 600, 3), 165, dtype=np.uint8)
    cv2.ellipse(page, (230, 295), (105, 145), 0, 0, 360, (12, 12, 12), 10)
    cv2.ellipse(page, (370, 320), (105, 145), 0, 0, 360, (12, 12, 12), 10)
    cv2.ellipse(page, (230, 295), (101, 141), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (370, 320), (101, 141), 0, 0, 360, (255, 255, 255), -1)
    Image.fromarray(page).save(img_path)

    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[345.0, 200.0], [435.0, 200.0], [435.0, 415.0], [345.0, 415.0]]],
                    "text": "このメレン近海のここ最近の平和も",
                    "translation": "梅伦近海[BR]最近这阵子的太平",
                    "direction": "v",
                    "font_size": 27,
                },
                {
                    "lines": [[[165.0, 215.0], [270.0, 215.0], [270.0, 425.0], [165.0, 425.0]]],
                    "text": "魔法の粉をもっとらんかった船が襲われたのも",
                    "translation": "没带魔法粉末的[BR]迦梨她们的船[BR]遭到袭击也好",
                    "direction": "v",
                    "font_size": 26,
                },
            ]
        },
        auto_initial_snapshot=True,
    )

    audit_ok = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats_ok = {i["category"] for i in audit_ok["pages"][0]["issues"]}
    assert "same_bubble_split_regions" not in cats_ok

    with pytest.raises(ValueError, match="Refusing to merge"):
        merge_regions_on_page(img_path, [0, 1])

    save_page_json_atomic(
        resolve_page_paths(img_path),
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[345.0, 200.0], [435.0, 200.0], [435.0, 415.0], [345.0, 415.0]]],
                    "text": "このメレン近海のここ最近の平和も",
                    "translation": "梅伦近海[BR]最近这阵子的太平",
                    "direction": "v",
                    "font_size": 28,
                },
                {
                    "lines": [[[165.0, 215.0], [270.0, 215.0], [270.0, 425.0], [165.0, 425.0]]],
                    "text": "魔法の粉をもっとらんかった船が襲われたのも",
                    "translation": "没带魔法粉末的[BR]迦梨她们的船[BR]遭到袭击也好",
                    "direction": "v",
                    "font_size": 26,
                },
            ]
        },
        auto_initial_snapshot=True,
    )
    merge_regions_on_page(img_path, [0, 1], force_merge=True)
    audit_bad = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats_bad = {i["category"] for i in audit_bad["pages"][0]["issues"]}
    assert "unfilled_connected_lobe" in cats_bad


def test_fig5_frameless_eye_wipe_detected_and_healed_by_tight_halo_rects(tmp_path: Path) -> None:
    folder = tmp_path / "eye_ch"
    folder.mkdir(parents=True)
    img_path = folder / "070.png"

    raw = np.full((460, 440, 3), 205, dtype=np.uint8)
    cv2.ellipse(raw, (218, 180), (34, 20), 0, 0, 360, (250, 250, 250), -1)
    cv2.ellipse(raw, (218, 180), (34, 20), 0, 0, 360, (12, 12, 12), 4)
    cv2.circle(raw, (220, 180), 9, (10, 10, 10), -1)
    for cy in (145, 220, 295):
        cv2.rectangle(raw, (286, cy - 26), (334, cy + 26), (255, 255, 255), -1)
        cv2.rectangle(raw, (293, cy - 19), (327, cy + 19), (15, 15, 15), -1)
    Image.fromarray(raw).save(img_path)

    paths = resolve_page_paths(img_path)
    bad_mask = np.zeros((460, 440), dtype=np.uint8)
    bad_mask[95:365, 195:355] = 255
    bad_inp = raw.copy()
    bad_inp[95:365, 195:355] = 205
    save_rgb_image_atomic(bad_inp, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "mask_raw": encode_mask_base64(bad_mask),
            "regions": [
                {
                    "lines": [[[275.0, 105.0], [348.0, 105.0], [348.0, 345.0], [275.0, 345.0]]],
                    "text": "もうすぐ",
                    "translation": "很快便会",
                    "direction": "v",
                    "font_size": 60,
                    "stroke_width": 0.24,
                }
            ],
        },
        auto_initial_snapshot=False,
    )

    bad_inp_blur = cv2.GaussianBlur(bad_inp, (9, 9), 2.5)
    grad_ramp = np.linspace(185, 220, 365 - 95, dtype=np.uint8)[:, None, None]
    bad_inp_blur[95:365, 195:355] = grad_ramp
    save_rgb_image_atomic(bad_inp_blur, paths.inpainted_path)

    audit = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats = {i["category"] for i in audit["pages"][0]["issues"]}
    assert "frameless_overmasked_artwork" in cats

    new_m, added, removed = apply_mask_operations(
        bad_mask,
        raw,
        [],
        tight_halo_rects=[[190, 90, 360, 370]],
    )
    healed = apply_incremental_local_repair(raw, bad_inp_blur, new_m, added, removed, method="telea")
    assert int((new_m[150:210, 180:255] > 0).sum()) == 0
    assert np.array_equal(healed[150:210, 180:255], raw[150:210, 180:255])
    assert int((new_m[125:165, 293:327] > 0).sum()) > 800


def test_fig1_fig4_inverted_calque_and_unbalanced_columns(tmp_path: Path) -> None:
    folder = tmp_path / "rhythm_ch"
    folder.mkdir(parents=True)
    img_path = folder / "006.png"
    Image.fromarray(np.full((500, 500, 3), 255, dtype=np.uint8)).save(img_path)
    paths = resolve_page_paths(img_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[50.0, 50.0], [160.0, 50.0], [160.0, 240.0], [50.0, 240.0]]],
                    "text": "神に嘘の罪の告白とか無駄やっちゅーに…",
                    "translation": "跟神明[BR]编假话认罪[BR]都说了没用嘛︙",
                    "direction": "v",
                },
                {
                    "lines": [[[200.0, 50.0], [320.0, 50.0], [320.0, 260.0], [200.0, 260.0]]],
                    "text": "あれならとっくにうちの子らが片づけたわ",
                    "translation": "那玩意儿[BR]早就被我家的孩子们[BR]收拾掉了",
                    "direction": "v",
                },
            ]
        },
        auto_initial_snapshot=False,
    )

    audit = run_qa_audit(folder, check_inpaint_cv=False, check_font_glyphs=False)
    by_idx = {(i["region_index"], i["category"]) for i in audit["pages"][0]["issues"]}
    assert (0, "inverted_japanese_calque_syntax") in by_idx
    assert (0, "unbalanced_linebreak_columns") in by_idx
    assert (1, "unbalanced_linebreak_columns") in by_idx
