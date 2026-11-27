"""Unit tests for richtext.v1 builder, atomic JSON store, snapshots, and batch editing."""

import _bootstrap  # noqa: F401, I001

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mtui_mcp.glossary_manager import batch_edit_chapter_pages, manage_glossary_entries
from mtui_mcp.local_inpaint import (
    add_region_to_page,
    delete_regions_from_page,
    manage_page_paste_overlays,
    merge_regions_on_page,
)
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    align_regions_on_page,
    apply_region_updates_to_page,
    encode_mask_base64,
    extract_chapter_script_data,
    get_page_details_dict,
    list_snapshots,
    load_page_json,
    restore_snapshot,
    save_page_json_atomic,
)
from mtui_mcp.richtext_builder import (
    apply_project_richtext_rules,
    apply_span_styles_to_region,
    is_rich_text_document,
    sync_region_text_update,
    visible_text_of_region,
)


def _create_sample_manga_page(folder: Path, name: str = "001.png") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    img_path = folder / name
    arr = np.full((800, 600, 3), 245, dtype=np.uint8)
    arr[120:160, 380:460] = 20
    arr[300:360, 120:220] = 30
    Image.fromarray(arr).save(img_path)

    paths = resolve_page_paths(img_path)
    mask = np.zeros((800, 600), dtype=np.uint8)
    mask[115:165, 375:465] = 255

    inpainted = arr.copy()
    inpainted[mask > 0] = 245
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(inpainted).save(paths.inpainted_path)

    image_key = str(img_path.resolve())
    image_data = {
        "original_width": 600,
        "original_height": 800,
        "upscale_ratio": 2,
        "upscaler": "mangajanai",
        "mask_raw": encode_mask_base64(mask),
        "mask_is_refined": True,
        "regions": [
            {
                "lines": [[[380.0, 100.0], [480.0, 100.0], [480.0, 260.0], [380.0, 260.0]]],
                "texts": ["ましろちゃん！？"],
                "text": "ましろちゃん！？",
                "translation": "真白酱！？",
                "font_size": 24,
                "font_color": "#000000",
                "bg_color": [255, 255, 255],
                "direction": "v",
                "alignment": "center",
                "angle": 0.0,
            },
            {
                "lines": [[[100.0, 280.0], [260.0, 280.0], [260.0, 380.0], [100.0, 380.0]]],
                "texts": ["行くぞ！"],
                "text": "行くぞ！",
                "translation": "出发吧！",
                "font_size": 22,
                "font_color": "#112233",
                "bg_color": [255, 255, 255],
                "direction": "h",
                "alignment": "center",
                "angle": 0.0,
            },
        ],
    }
    save_page_json_atomic(paths, image_key, image_data, auto_initial_snapshot=False)
    return img_path


def test_richtext_builder_and_diff_sync() -> None:
    reg = {
        "translation": "看我的[BR]超电磁炮！？",
        "direction": "v",
    }
    doc = apply_span_styles_to_region(
        reg,
        spans=[
            {
                "match": "超电磁炮",
                "bold": True,
                "color": "#ff3300",
                "ruby": "Railgun",
                "stroke": {"color": "#000000", "width": 0.12},
            },
            {"regex": r"[!?！？]{2}", "tcy": True},
        ],
    )
    assert is_rich_text_document(doc)
    assert visible_text_of_region(reg) == "看我的\n超电磁炮！？"

    sync_region_text_update(reg, "接招吧\n超电磁炮！？")
    assert reg["translation"] == "接招吧[BR]超电磁炮！？"
    rich_after = reg["translation_rich"]
    assert is_rich_text_document(rich_after)
    second_para = rich_after["blocks"][1]["inlines"]
    assert any(node.get("type") == "ruby" and node["text"][0]["text"] == "Railgun" for node in second_para)
    assert any(node.get("type") == "tcy" for node in second_para)

    reg2 = {"translation": "“真的吗！！”", "direction": "v"}
    assert apply_project_richtext_rules(reg2)
    assert is_rich_text_document(reg2.get("translation_rich"))


def test_atomic_store_preserves_metadata_and_snapshots(tmp_path: Path) -> None:
    page = _create_sample_manga_page(tmp_path / "ch1", "001.png")

    refreshed, updated = apply_region_updates_to_page(
        page,
        [
            {
                "region_index": 0,
                "translation": "真白同学\n快看那边！",
                "font_size": 28,
                "font_color": "#223344",
                "stroke_width": 0.12,
                "line_spacing": 1.15,
                "move_dx": 5.0,
                "move_dy": -4.0,
            }
        ],
    )
    assert updated[0]["font_size"] == 28
    assert updated[0]["font_color"] == "#223344"
    assert updated[0]["translation"] == "真白同学\n快看那边！"

    _, saved_data = load_page_json(refreshed)
    assert saved_data["upscale_ratio"] == 2
    assert saved_data["upscaler"] == "mangajanai"
    assert saved_data["mask_raw"]

    snaps = list_snapshots(page)
    assert len(snaps) == 1
    assert snaps[0]["label"] == "initial_before_mcp"

    restore_snapshot(page, snaps[0]["snapshot_id"])
    _, restored_data = load_page_json(resolve_page_paths(page))
    assert restored_data["regions"][0]["translation"] == "真白酱！？"


def test_chapter_script_batch_edit_and_structural_ops(tmp_path: Path) -> None:
    ch = tmp_path / "chapter_02"
    p1 = _create_sample_manga_page(ch, "001.png")
    p2 = _create_sample_manga_page(ch, "002.png")

    script = extract_chapter_script_data(ch, reading_order="rtl")
    assert script["total_pages_in_folder"] == 2
    assert script["total_regions"] == 4
    assert script["pages"][0]["entries"][0]["region_index"] == 0

    mod_pages, rep = batch_edit_chapter_pages(
        ch,
        find_text="真白酱",
        replace_text="椎名真白",
        set_fields={"font_size": 26},
        dry_run=False,
    )
    assert len(mod_pages) == 2
    assert rep["matched_regions"] == 2

    _, added = add_region_to_page(p1, bbox_xyxy=[20, 420, 160, 520], source_text="ドキッ", translation="扑通")
    assert added["index"] == 2

    _, aligned = align_regions_on_page(p1, [1, 2], mode="left")
    assert len(aligned) == 2

    _, merged = merge_regions_on_page(p1, [1, 2])
    assert "出发吧" in merged["translation"] and "扑通" in merged["translation"]

    _, ov = manage_page_paste_overlays(
        p1,
        action="add_clone_patch",
        source_rect_xyxy=[10, 10, 60, 60],
        target_center_xy=[300, 500],
    )
    assert ov["count"] == 1

    _, del_info = delete_regions_from_page(p2, [0], restore_artwork=True)
    assert del_info["remaining_region_count"] == 1

    g_file = tmp_path / "my_glossary.yaml"
    res = manage_glossary_entries(
        action="upsert",
        glossary_file=str(g_file),
        category="Person",
        entries=[{"original": "ましろ", "translation": "真白", "description": "女主角"}],
    )
    assert res["category_entries"][0]["aliases"][0]["translations"][0]["text"] == "真白"


def test_absorbed_codex_strengths_multi_span_revision_uid_and_strict_keys(tmp_path: Path) -> None:
    # 1. Multi-span SequenceMatcher opcode diff: edit BOTH prefix and suffix while middle Ruby+Bold survives!
    reg = {"translation": "喂，看我的超电磁炮！！", "direction": "v"}
    apply_span_styles_to_region(
        reg,
        spans=[
            {"match": "超电磁炮", "bold": True, "color": "#ff2200", "ruby": "Railgun"},
        ],
    )
    sync_region_text_update(reg, "呐，看我的超电磁炮！？")
    inlines = reg["translation_rich"]["blocks"][0]["inlines"]
    ruby_nodes = [n for n in inlines if n.get("type") == "ruby"]
    assert len(ruby_nodes) == 1
    assert ruby_nodes[0]["base"][0]["text"] == "超电磁炮"
    assert ruby_nodes[0]["base"][0]["style"]["bold"] is True
    assert ruby_nodes[0]["text"][0]["text"] == "Railgun"

    # 2. Optimistic concurrency revision check, stable region_uid across delete, and strict field validation
    page = _create_sample_manga_page(tmp_path / "rev_ch", "001.png")
    apply_region_updates_to_page(page, [{"region_index": 0, "font_size": 25}])
    details = get_page_details_dict(page)
    rev1 = details["revision"]
    second_uid = details["regions"][1]["region_uid"]
    assert second_uid.startswith("reg_")

    delete_regions_from_page(page, [0], restore_artwork=False)

    with pytest.raises(ValueError, match="Revision conflict"):
        apply_region_updates_to_page(
            page,
            [{"region_uid": second_uid, "translation": "不会写入"}],
            expected_revision=rev1,
        )

    rev2 = get_page_details_dict(page)["revision"]
    with pytest.raises(ValueError, match="Unknown region update field"):
        apply_region_updates_to_page(
            page,
            [{"region_uid": second_uid, "font_colour": "#000000"}],
            expected_revision=rev2,
        )

    _, updated = apply_region_updates_to_page(
        page,
        [{"region_uid": second_uid, "translation": "通过稳定UID精准命中！"}],
        expected_revision=rev2,
    )
    assert updated[0]["index"] == 0
    assert updated[0]["translation"] == "通过稳定UID精准命中！"


def test_first_class_bold_roundtrip_and_text_update(tmp_path: Path) -> None:
    page = _create_sample_manga_page(tmp_path / "bold_ch", "001.png")
    refreshed, updated = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "font_family": "FZBeiWeiKaiShu-S19", "bold": True}],
    )
    assert updated[0]["bold"] is True
    assert updated[0]["has_rich_text"] is True
    _, saved = load_page_json(refreshed)
    r0 = saved["regions"][0]
    assert r0["bold"] is True
    assert r0["translation_rich"]["blocks"][0]["inlines"][0]["style"]["bold"] is True

    refreshed2, updated2 = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "translation": "少废话\n闭嘴！"}],
    )
    assert updated2[0]["bold"] is True
    _, saved2 = load_page_json(refreshed2)
    for blk in saved2["regions"][0]["translation_rich"]["blocks"]:
        for inl in blk["inlines"]:
            assert inl["style"]["bold"] is True

    refreshed3, updated3 = apply_region_updates_to_page(
        page,
        [{"region_index": 0, "bold": False}],
    )
    assert updated3[0]["bold"] is False
    _, saved3 = load_page_json(refreshed3)
    assert "bold" not in saved3["regions"][0]
    assert "translation_rich" not in saved3["regions"][0]


def test_richtext_v1_blur_and_ruby_inherit_roundtrip_and_reset() -> None:
    reg = {"translation": "轰隆隆！！", "direction": "v"}
    doc = apply_span_styles_to_region(
        reg,
        whole_style={
            "blur": 0.35,
            "blur_angle": 0.0,
            "blur_motion": True,
            "blur_motion_distance": 24.0,
            "color": "#ff3300",
        },
        spans=[
            {
                "match": "轰隆隆",
                "ruby": "ゴゴゴ",
                "ruby_style": {"color": "#000000"},
            }
        ],
    )
    assert is_rich_text_document(doc)
    ruby_inline = doc["blocks"][0]["inlines"][0]
    assert ruby_inline["type"] == "ruby"
    base_st = ruby_inline["base"][0]["style"]
    assert base_st["blur"] == 0.35
    assert base_st["blurAngle"] == 0.0
    assert base_st["blurMotion"] is True
    assert base_st["blurMotionDistance"] == 24.0
    assert base_st["rubyInherit"] is False
    assert ruby_inline["text"][0]["style"]["color"] == "#000000"

    apply_span_styles_to_region(
        reg,
        whole_style={
            "blur": 0.0,
            "blurAngle": None,
            "blurMotion": False,
            "blurMotionDistance": 0.0,
            "color": None,
            "rubyInherit": True,
        },
        spans=[{"match": "轰隆隆", "ruby": ""}],
    )
    assert "translation_rich" not in reg
