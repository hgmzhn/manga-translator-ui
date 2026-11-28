"""Unit tests for QA Auditor, CV residual ink detection, mask repair, and visual inspection."""

import _bootstrap  # noqa: F401, I001

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from mtui_mcp.local_inpaint import apply_incremental_local_repair, apply_mask_operations
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    encode_mask_base64,
    open_rgb_image,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from mtui_mcp.qa_auditor import run_qa_audit
from mtui_mcp.visual_inspector import (
    generate_page_inspection_image,
    generate_region_loupe_image,
)


def test_qa_auditor_cv_residual_ink_and_mask_repair(tmp_path: Path) -> None:
    folder = tmp_path / "qa_chapter"
    folder.mkdir(parents=True)
    img_path = folder / "page01.png"

    # Create 700x500 gray manga page with a bright white speech bubble at x=120..380, y=80..360
    page = np.full((700, 500, 3), 180, dtype=np.uint8)
    cv2.ellipse(page, (250, 220), (115, 135), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (250, 220), (115, 135), 0, 0, 360, (15, 15, 15), 4)
    # Draw panel border line at y=420 across the page
    page[418:424, 30:470] = (10, 10, 10)

    # Main Japanese text at x=220..260, y=150..290 (erased by mask)
    page[150:290, 230:255] = (20, 20, 20)
    # Un-erased residual furigana speck at x=272..282, y=170..195 inside the white bubble!
    page[170:195, 272:280] = (25, 25, 25)
    Image.fromarray(page).save(img_path)

    paths = resolve_page_paths(img_path)
    mask = np.zeros((700, 500), dtype=np.uint8)
    mask[145:295, 225:260] = 255
    # Simulate an accidental over-erase wiping part of the panel border at y=415..428, x=200..260!
    mask[415:428, 200:260] = 255

    inpainted = page.copy()
    inpainted[145:295, 225:260] = 255  # Erased main text, but left furigana at x=272..280!
    inpainted[415:428, 200:260] = 180  # Accidentally wiped panel border!
    save_rgb_image_atomic(inpainted, paths.inpainted_path)

    image_data = {
        "mask_raw": encode_mask_base64(mask),
        "mask_is_refined": True,
        "regions": [
            {
                # Tall narrow box (w=50, h=150) mistakenly set to horizontal, with leftover kana + 1-char orphan!
                "lines": [[[225.0, 145.0], [275.0, 145.0], [275.0, 295.0], [225.0, 295.0]]],
                "texts": ["本当ですか"],
                "text": "本当ですか",
                "translation": "真的是ましろ同学[BR]吗",
                "font_size": 20,
                "font_color": "#000000",
                "bg_color": [255, 255, 255],
                "direction": "h",
                "alignment": "center",
            }
        ],
    }
    save_page_json_atomic(paths, str(img_path.resolve()), image_data, auto_initial_snapshot=False)

    audit = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats = set(audit["issues_by_category"].keys())
    assert "untranslated_kana" in cats
    assert "orphan_single_char_line" in cats
    assert "direction_aspect_mismatch" in cats
    assert "inpaint_residual_ink" in cats

    # Extract the suggested_mask_rect_xyxy from CV detector and fix BOTH the residual furigana AND the over-erased border!
    res_issue = next(i for i in audit["pages"][0]["issues"] if i["category"] == "inpaint_residual_ink")
    suggested_rect = res_issue["suggested_mask_rect_xyxy"]

    new_mask, added, removed = apply_mask_operations(
        mask,
        page,
        image_data["regions"],
        add_rects=[suggested_rect],
        erase_rects=[[195, 410, 265, 432]],
    )
    repaired = apply_incremental_local_repair(
        page,
        inpainted,
        new_mask,
        added,
        removed,
        method="solid_fill",
    )
    # Furigana at x=272..280, y=170..195 should now be clean white (~255)
    assert float(np.mean(repaired[170:195, 272:280])) > 250.0
    # Panel border at y=418:424, x=200:260 should be restored to exact original dark pixels (10)!
    assert np.array_equal(repaired[418:424, 200:260], page[418:424, 200:260])

    # Test visual inspection generators
    page_vis_file, page_meta = generate_page_inspection_image(img_path, mode="inpaint_check", show_grid=True)
    assert page_vis_file.is_file()
    assert page_meta["region_count"] == 1

    loupe_file, loupe_meta = generate_region_loupe_image(img_path, region_indices=[0], show_grid=True)
    assert loupe_file.is_file()
    assert loupe_meta["inspected_crops"][0]["region_index"] == 0
    loupe_arr = open_rgb_image(loupe_file)
    assert loupe_arr.shape[1] > loupe_arr.shape[0]


def test_qa_auditor_font_geometry_and_heavy_bold(tmp_path: Path) -> None:
    folder = tmp_path / "font_cv_chapter"
    folder.mkdir(parents=True)
    img_path = folder / "page01.png"

    page = np.full((600, 600, 3), 255, dtype=np.uint8)
    # Region #0 (x=60..110, y=60..280): uniform 4px monoline Gothic kana ('フ', 'ト', 'レ') wrongly set to FZYaSong-DB-GBK!
    for cy in [85, 145, 205]:
        cv2.line(page, (72, cy - 10), (98, cy - 10), (10, 10, 10), 4, cv2.LINE_AA)
        cv2.line(page, (92, cy - 10), (84, cy + 12), (10, 10, 10), 4, cv2.LINE_AA)

    # Region #1 (x=220..280, y=60..280): high-contrast Mincho strokes (1px thin horizontal + 6px thick vertical) left at FZHei-B01!
    for cy in [85, 145, 205]:
        page[cy - 10 : cy - 9, 232:268] = (70, 70, 70)
        page[cy + 8 : cy + 9, 232:268] = (70, 70, 70)
        page[cy - 14 : cy + 14, 248:254] = (8, 8, 8)

    # Region #2 (x=380..440, y=60..180): inherently heavy FZJianZhi-M23 with bold=True!
    cv2.rectangle(page, (395, 80), (425, 140), (10, 10, 10), 6)

    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    inpainted = np.full_like(page, 255)
    save_rgb_image_atomic(inpainted, paths.inpainted_path)
    mask = np.zeros((600, 600), dtype=np.uint8)
    mask[55:290, 55:450] = 255

    image_data = {
        "mask_raw": encode_mask_base64(mask),
        "mask_is_refined": True,
        "regions": [
            {
                "lines": [[[60.0, 60.0], [110.0, 60.0], [110.0, 240.0], [60.0, 240.0]]],
                "text": "コロニ",
                "translation": "可洛尼",
                "font_family": "FZYaSong-DB-GBK",
                "font_size": 26,
                "direction": "v",
            },
            {
                "lines": [[[225.0, 60.0], [275.0, 60.0], [275.0, 240.0], [225.0, 240.0]]],
                "text": "王の剣",
                "translation": "王之剑",
                "font_family": "FZHei-B01",
                "font_size": 28,
                "direction": "v",
            },
            {
                "lines": [[[385.0, 65.0], [435.0, 65.0], [435.0, 165.0], [385.0, 165.0]]],
                "text": "俺が",
                "translation": "我懂了～",
                "font_family": "FZJianZhi-M23",
                "bold": True,
                "font_size": 30,
                "direction": "v",
            },
        ],
    }
    save_page_json_atomic(paths, str(img_path.resolve()), image_data, auto_initial_snapshot=False)

    audit = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats = {iss["category"] for iss in audit["pages"][0]["issues"]}
    assert "suspect_fake_songti" in cats
    assert "suspect_missed_songti" in cats
    assert "inherently_heavy_font_bolded" in cats
    assert "unrotated_vertical_symbol" not in cats
