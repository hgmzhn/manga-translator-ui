"""Unit tests for richtext.v1 builder, atomic JSON store, snapshots, and batch editing."""

import _bootstrap  # noqa: F401, I001

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import mtui_mcp.project_store as project_store
from mtui_mcp.bubble_centering import partition_page_white_bubbles, run_closed_loop_bubble_centering
from mtui_mcp.glossary_manager import batch_edit_chapter_pages, manage_glossary_entries
from mtui_mcp.local_inpaint import (
    add_region_to_page,
    auto_detect_ink_mask_in_rect,
    delete_regions_from_page,
    manage_page_paste_overlays,
    merge_regions_on_page,
)
from mtui_mcp.path_resolver import (
    list_manga_pages_in_path,
    resolve_page_paths,
    resolve_source_image_path,
)
from mtui_mcp.project_store import (
    align_regions_on_page,
    apply_region_updates_to_page,
    cleanup_snapshots_for_target,
    create_snapshot,
    decode_mask_base64,
    encode_mask_base64,
    extract_chapter_script_data,
    get_page_details_dict,
    list_snapshots,
    load_page_json,
    open_rgb_image,
    page_edit_lock,
    restore_snapshot,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from mtui_mcp.richtext_builder import (
    apply_project_richtext_rules,
    apply_span_styles_to_region,
    is_rich_text_document,
    region_is_bold,
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


def test_page_edit_lock_reentrant_cross_thread_and_cross_process(tmp_path: Path) -> None:
    page = _create_sample_manga_page(tmp_path / "lock_ch", "001.png")

    # 1. Re-entrant on the same thread without self-deadlock
    with page_edit_lock(page, timeout_sec=2.0) as lp1:
        with page_edit_lock(resolve_page_paths(page), timeout_sec=2.0) as lp2:
            assert lp1 == lp2
            assert lp1.name == ".001_translations.lock"

    # 2. Cross-thread contention raises TimeoutError when held
    entered = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        with page_edit_lock(page, timeout_sec=5.0):
            entered.set()
            release.wait(timeout=5.0)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert entered.wait(timeout=3.0)
    try:
        with pytest.raises(TimeoutError):
            with page_edit_lock(page, timeout_sec=0.05):
                pass
    finally:
        release.set()
        t.join(timeout=3.0)

    # 3. Cross-process advisory OS file lock exclusion
    child_code = (
        "import sys, time; "
        f"sys.path.insert(0, {str(_bootstrap.ROOT)!r}); "
        "from mtui_mcp.project_store import page_edit_lock; "
        f"ctx = page_edit_lock({str(page)!r}, timeout_sec=5.0); "
        "ctx.__enter__(); "
        "print('LOCKED', flush=True); "
        "sys.stdin.readline(); "
        "ctx.__exit__(None, None, None)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "LOCKED"
        with pytest.raises(TimeoutError):
            with page_edit_lock(page, timeout_sec=0.08):
                pass
    finally:
        if proc.stdin is not None:
            proc.stdin.write("\n")
            proc.stdin.flush()
        proc.wait(timeout=5.0)

    # Once child exits, lock is immediately acquirable again
    with page_edit_lock(page, timeout_sec=2.0):
        pass


def test_f04_f102_unbold_partial_unbold_and_clear_rich_text(tmp_path: Path) -> None:
    page = _create_sample_manga_page(tmp_path / "f04_ch", "001.png")
    apply_region_updates_to_page(page, [{"region_index": 0, "bold": True}])

    # Partial un-bold via apply_span_styles_to_region + save_page_json_atomic must NOT re-bold whole region
    paths = resolve_page_paths(page)
    ik, data = load_page_json(paths)
    reg0 = data["regions"][0]
    assert reg0.get("bold") is True
    apply_span_styles_to_region(reg0, spans=[{"match": "真白", "style": {"bold": False}}])
    assert "bold" not in reg0
    assert region_is_bold(reg0) is False
    save_page_json_atomic(paths, ik, data, auto_initial_snapshot=False)

    _, after_partial = load_page_json(resolve_page_paths(page))
    r0_partial = after_partial["regions"][0]
    assert "bold" not in r0_partial
    inlines = r0_partial["translation_rich"]["blocks"][0]["inlines"]
    assert inlines[0]["text"] == "真白" and "bold" not in inlines[0].get("style", {})
    assert any(inl.get("style", {}).get("bold") is True for inl in inlines[1:])

    # Whole un-bold via whole_style={"bold": False} clears top-level bold and redundant translation_rich
    apply_region_updates_to_page(page, [{"region_index": 0, "bold": True}])
    paths2 = resolve_page_paths(page)
    ik2, data2 = load_page_json(paths2)
    apply_span_styles_to_region(data2["regions"][0], whole_style={"bold": False})
    save_page_json_atomic(paths2, ik2, data2, auto_initial_snapshot=False)
    _, after_unbold = load_page_json(resolve_page_paths(page))
    assert "bold" not in after_unbold["regions"][0]
    assert "translation_rich" not in after_unbold["regions"][0]

    # F102: clear_rich_text=True alone must also clear bold
    apply_region_updates_to_page(page, [{"region_index": 0, "bold": True}])
    _, sum_cleared = apply_region_updates_to_page(page, [{"region_index": 0, "clear_rich_text": True}])
    assert sum_cleared[0]["bold"] is False
    assert sum_cleared[0]["has_rich_text"] is False
    _, after_clear = load_page_json(resolve_page_paths(page))
    assert "bold" not in after_clear["regions"][0]
    assert "translation_rich" not in after_clear["regions"][0]


def test_f34_f35_f37_f38_f41_snapshot_hardening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ch = tmp_path / "snap_ch"
    p1 = _create_sample_manga_page(ch, "001.png")
    p2 = _create_sample_manga_page(ch, "002.png")

    snap_p2 = create_snapshot(resolve_page_paths(p2), label="proof")
    create_snapshot(resolve_page_paths(p2), label="proof_old")

    # F34: Cross-page snapshot_id must be rejected before copying any file
    with pytest.raises(ValueError, match="belongs to page"):
        restore_snapshot(p1, snap_p2["snapshot_id"])

    # F37: cleanup with label="proof" must NOT delete "proof_old"
    cleaned = cleanup_snapshots_for_target(p2, label="proof")
    assert cleaned["deleted_count"] == 1
    remaining_p2 = list_snapshots(p2)
    assert [s["label"] for s in remaining_p2] == ["proof_old"]

    # F41: Poisoned manifest paths (escaping backup or foreign original target) must fail without copying
    evil_target = tmp_path / "evil_outside.json"
    snap_p1 = create_snapshot(resolve_page_paths(p1), label="manual")
    mf_path = Path(snap_p1["snapshot_dir"]) / "manifest.json"
    mf_data = json.loads(mf_path.read_text(encoding="utf-8"))
    mf_orig_files = json.loads(json.dumps(mf_data["files"]))

    mf_data["files"]["rendered"] = {"original": str(evil_target), "backup": "translations.json"}
    mf_path.write_text(json.dumps(mf_data), encoding="utf-8")
    with pytest.raises(ValueError, match="does not belong to page"):
        restore_snapshot(p1, snap_p1["snapshot_id"])
    assert not evil_target.exists()

    escape_src = tmp_path / "secret.txt"
    escape_src.write_text("secret", encoding="utf-8")
    mf_data["files"] = json.loads(json.dumps(mf_orig_files))
    mf_data["files"]["json"]["backup"] = "../../../secret.txt"
    mf_path.write_text(json.dumps(mf_data), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        restore_snapshot(p1, snap_p1["snapshot_id"])

    # F38: Incomplete snapshot directory (missing manifest.json) must not block ensure_initial_snapshot
    p3 = _create_sample_manga_page(ch, "003.png")
    p3_paths = resolve_page_paths(p3)
    broken_dir = p3_paths.snapshots_dir / "003__20260101_000000__initial_before_mcp"
    broken_dir.mkdir(parents=True, exist_ok=True)
    (broken_dir / "translations.json").write_text("{}", encoding="utf-8")

    real_copy2 = shutil.copy2

    def _fail_once(src, dst, *args, **kwargs):
        raise OSError("disk full simulated")

    monkeypatch.setattr(shutil, "copy2", _fail_once)
    with pytest.raises(OSError, match="disk full"):
        create_snapshot(p3_paths, label="checkpoint")
    monkeypatch.setattr(shutil, "copy2", real_copy2)
    assert not any(d.name.startswith(".tmp_") for d in p3_paths.snapshots_dir.iterdir())

    apply_region_updates_to_page(p3, [{"region_index": 0, "translation": "编辑后台词"}])
    p3_snaps = list_snapshots(p3)
    assert any(s["label"] == "initial_before_mcp" for s in p3_snaps)

    # F35: Legacy JSON snapshot restored after migration to manga_translator_work/json updates active JSON
    leg_dir = tmp_path / "legacy_ch"
    leg_dir.mkdir(parents=True)
    leg_img = leg_dir / "001.png"
    Image.fromarray(np.full((200, 200, 3), 255, dtype=np.uint8)).save(leg_img)
    leg_json = leg_dir / "001_translations.json"
    leg_payload = {
        str(leg_img.resolve()): {
            "regions": [
                {
                    "lines": [[[20.0, 20.0], [100.0, 20.0], [100.0, 140.0], [20.0, 140.0]]],
                    "text": "旧形式",
                    "translation": "旧版初始译文",
                    "direction": "v",
                }
            ]
        }
    }
    leg_json.write_text(json.dumps(leg_payload, ensure_ascii=False), encoding="utf-8")
    apply_region_updates_to_page(leg_img, [{"region_index": 0, "translation": "迁移后的新译文"}])
    migrated_paths = resolve_page_paths(leg_img)
    assert migrated_paths.json_path.parent.name == "json"
    restore_snapshot(leg_img, label="initial_before_mcp")
    _, restored_leg = load_page_json(resolve_page_paths(leg_img))
    assert restored_leg["regions"][0]["translation"] == "旧版初始译文"


def test_f95_f100_tcy_parentheses_preserved_and_post_save_summary(tmp_path: Path) -> None:
    page = _create_sample_manga_page(tmp_path / "tcy_ch", "001.png")
    _, summaries = apply_region_updates_to_page(
        page,
        [
            {
                "region_index": 0,
                "translation": "第(12)小队...",
                "direction": "v",
                "rich_spans": [{"match": "(12)", "tcy": True}],
            }
        ],
    )
    # F95: (12) inside TCY stays ASCII "(12)", while non-TCY "..." becomes "︙"
    # F100: Returned summary reflects post-save normalized text
    assert summaries[0]["translation"] == "第(12)小队︙"
    _, saved = load_page_json(resolve_page_paths(page))
    r0 = saved["regions"][0]
    assert r0["translation"] == "第(12)小队︙"
    inlines = r0["translation_rich"]["blocks"][0]["inlines"]
    tcy_nodes = [n for n in inlines if n.get("type") == "tcy"]
    assert len(tcy_nodes) == 1
    assert tcy_nodes[0]["content"][0]["text"] == "(12)"


def test_richtext_span_validation_and_fallback_font_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # F90: Partial ruby_style patch on multi-char ruby node raises ValueError without mutating node
    reg_ruby = {"translation": "前往东京塔", "direction": "v"}
    apply_span_styles_to_region(
        reg_ruby,
        spans=[{"match": "东京", "ruby": "とうきょう", "ruby_style": {"color": "#112233"}}],
    )
    with pytest.raises(ValueError, match="partially covers ruby node"):
        apply_span_styles_to_region(
            reg_ruby,
            spans=[{"match": "东", "ruby_style": {"color": "#ff0000"}}],
        )
    ruby_node = reg_ruby["translation_rich"]["blocks"][0]["inlines"][1]
    assert ruby_node["text"][0]["style"]["color"] == "#112233"

    # F91: Out-of-range occurrence does NOT style all matches
    reg_occ = {"translation": "猫与猫", "direction": "h"}
    apply_span_styles_to_region(reg_occ, spans=[{"match": "猫", "occurrence": 2, "bold": True}])
    assert "translation_rich" not in reg_occ
    apply_span_styles_to_region(reg_occ, spans=[{"regex": "猫", "occurrence": 5, "bold": True}])
    assert "translation_rich" not in reg_occ

    # F92: Explicit non-empty ruby wins when tcy=False is also present in serialized span
    reg_r_tcy = {"translation": "超电磁炮", "direction": "v"}
    doc_r = apply_span_styles_to_region(
        reg_r_tcy,
        spans=[{"match": "超电磁炮", "ruby": "Railgun", "tcy": False}],
    )
    assert doc_r["blocks"][0]["inlines"][0]["type"] == "ruby"

    # F93: Malformed richtext.v1 blocks=[None] rejected with field path before creating snapshot
    page = _create_sample_manga_page(tmp_path / "f93_ch", "001.png")
    with pytest.raises(ValueError, match=r"translation_rich\.blocks\[0\]"):
        apply_region_updates_to_page(
            page,
            [{"region_index": 0, "translation_rich": {"format": "richtext.v1", "blocks": [None]}}],
        )
    assert list_snapshots(page) == []

    # F99: _pick_punctuation_fallback_font skips uninstalled fonts
    monkeypatch.setattr(project_store, "has_font", lambda fam, **kw: fam == "FZHei-B01")
    monkeypatch.setattr(project_store, "check_missing_glyphs", lambda fam, txt, **kw: [])
    assert project_store._pick_punctuation_fallback_font("Arphic Tankuin Bold GB", "﹁") == "FZHei-B01"
    monkeypatch.setattr(project_store, "has_font", lambda fam, **kw: False)
    assert project_store._pick_punctuation_fallback_font("Arphic Tankuin Bold GB", "﹁") == ""


def test_path_resolver_inpaint_centering_alignment_and_glossary_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # F45, F97, F98: result reverse resolution, recursive dedup, invalid translation_map entry guard
    root = tmp_path / "vol"
    ch = root / "ch01"
    p1 = _create_sample_manga_page(ch, "001.png")
    res_img = ch / "manga_translator_work" / "result" / "001.png"
    res_img.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p1, res_img)
    assert resolve_source_image_path(res_img) == p1.resolve()

    sib_tr = root / "ch01-translated"
    sib_tr.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p1, sib_tr / "001.png")
    (sib_tr / "translation_map.json").write_text(
        json.dumps({str((sib_tr / "001.png").resolve()): ["not_a_path_string"]}),
        encoding="utf-8",
    )
    assert resolve_source_image_path(sib_tr / "001.png") == p1.resolve()
    pages_rec = list_manga_pages_in_path(root, recursive=True)
    assert [p.source_path for p in pages_rec] == [p1.resolve()]

    # F42: Low-contrast uniform region returns empty zeros mask instead of wiping whole rect
    low_contrast = np.full((120, 120, 3), 210, dtype=np.uint8)
    low_contrast[40:80, 40:80] = 205
    assert not np.any(auto_detect_ink_mask_in_rect(low_contrast, [20, 20, 100, 100]))

    # F01/F44: add_region captures initial_before_mcp BEFORE mutating inpainted_path & rolls back on JSON save failure
    p_inpaint = _create_sample_manga_page(tmp_path / "inp_rb", "001.png")
    orig_inp = open_rgb_image(resolve_page_paths(p_inpaint).inpainted_path).copy()
    real_save_json = project_store.save_page_json_atomic

    def _boom_save(*args, **kwargs):
        raise RuntimeError("simulated json write failure")

    monkeypatch.setattr("mtui_mcp.local_inpaint.save_page_json_atomic", _boom_save)
    with pytest.raises(RuntimeError, match="simulated json write failure"):
        add_region_to_page(p_inpaint, bbox_xyxy=[115, 295, 225, 365], source_text="x", translation="y")
    monkeypatch.setattr("mtui_mcp.local_inpaint.save_page_json_atomic", real_save_json)

    # Inpainted image was rolled back AND initial_before_mcp snapshot holds original pre-edit inpainted image
    assert np.array_equal(open_rgb_image(resolve_page_paths(p_inpaint).inpainted_path), orig_inp)
    snaps_rb = list_snapshots(p_inpaint)
    assert len(snaps_rb) == 1 and snaps_rb[0]["label"] == "initial_before_mcp"

    # F43 & F96: Shared mask component preserved for surviving region; missing sidecar reports artwork_restored=False
    p_shared = _create_sample_manga_page(tmp_path / "shared_mask", "001.png")
    sp = resolve_page_paths(p_shared)
    ik_s, d_s = load_page_json(sp)
    shared_m = np.zeros((800, 600), dtype=np.uint8)
    shared_m[95:390, 95:485] = 255  # Single connected mask component spanning both region 0 and region 1
    d_s["mask_raw"] = encode_mask_base64(shared_m)
    save_page_json_atomic(sp, ik_s, d_s, auto_initial_snapshot=False)
    ref_del, del_res = delete_regions_from_page(p_shared, [0], restore_artwork=True)
    assert del_res["artwork_restored"] is True
    _, d_after_del = load_page_json(ref_del)
    m_after_del = decode_mask_base64(d_after_del["mask_raw"], (800, 600))
    assert m_after_del is not None
    # Surviving region (formerly index 1 at [100..260, 280..380]) keeps its mask!
    assert int((m_after_del[290:370, 110:250] > 0).sum()) > 10000

    p_no_inp = _create_sample_manga_page(tmp_path / "no_inp", "001.png")
    resolve_page_paths(p_no_inp).inpainted_path.unlink()
    _, del_no_inp = delete_regions_from_page(p_no_inp, [0], restore_artwork=True)
    assert del_no_inp["artwork_restored"] is False

    # F94: Diagonal boxes on full-page white background are NOT rejected as multi-lobe bubble
    p_diag = _create_sample_manga_page(tmp_path / "diag_bg", "001.png")
    add_region_to_page(p_diag, bbox_xyxy=[20, 500, 80, 600], source_text="a", translation="甲", auto_mask_ink=False)
    _, merged_diag = merge_regions_on_page(p_diag, [1, 2])
    assert "甲" in merged_diag["translation"]

    # F101: Asymmetric white_frame_rect_local aligns actual world left edge
    p_align = _create_sample_manga_page(tmp_path / "align_ch", "001.png")
    apply_region_updates_to_page(
        p_align,
        [
            {
                "region_index": 0,
                "bbox_xyxy": [100.0, 100.0, 120.0, 140.0],
                "center": [110.0, 120.0],
                "white_frame_rect_local": [-10.0, -20.0, 50.0, 20.0],
            }
        ],
    )
    _, aligned_sum = align_regions_on_page(p_align, [0], mode="left", reference="canvas")
    # Local left offset is -10 from center -> world left edge 0 requires center.x == 10.0
    assert aligned_sum[0]["center"][0] == pytest.approx(10.0)

    # F27, F29, F106: Centering runs 1 pass without render_page_fn, preserves white_frame_rect_local, and clamps edge center
    edge_gray = np.full((100, 100), 200, dtype=np.uint8)
    edge_gray[15:85, 15:99] = 255
    edge_regs = [
        {"center": [100.0, 35.0], "direction": "v", "angle": 0.0},
        {"center": [50.0, 65.0], "direction": "v", "angle": 0.0},
    ]
    partition_page_white_bubbles(edge_gray, edge_regs)  # Must not raise IndexError on cx=100 for w=100

    bub_dir = tmp_path / "bub_center"
    bub_dir.mkdir(parents=True)
    bub_img = bub_dir / "001.png"
    bub_canvas = np.full((800, 640, 3), 180, dtype=np.uint8)
    bub_canvas[80:320, 100:300] = 255
    bub_canvas[80:84, 100:300] = 0
    bub_canvas[316:320, 100:300] = 0
    bub_canvas[80:320, 100:104] = 0
    bub_canvas[80:320, 296:300] = 0
    Image.fromarray(bub_canvas).save(bub_img)
    bp = resolve_page_paths(bub_img)
    save_rgb_image_atomic(bub_canvas, bp.inpainted_path)
    ren_canvas = bub_canvas.copy()
    ren_canvas[140:260, 220:260] = 10
    bub_rendered = bub_dir / "manga_translator_work" / "result" / "001.png"
    save_rgb_image_atomic(ren_canvas, bub_rendered)
    save_page_json_atomic(
        bp,
        str(bub_img.resolve()),
        {
            "last_output_path": str(bub_rendered),
            "regions": [
                {
                    "lines": [[[210.0, 130.0], [270.0, 130.0], [270.0, 270.0], [210.0, 270.0]]],
                    "center": [240.0, 200.0],
                    "translation": "居中测试",
                    "font_size": 26,
                    "direction": "v",
                    "has_custom_white_frame": True,
                    "white_frame_rect_local": [-35.0, -70.0, 35.0, 70.0],
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    run_closed_loop_bubble_centering(bub_dir, passes=3, render_page_fn=None)
    _, centered_d = load_page_json(resolve_page_paths(bub_img))
    c_reg = centered_d["regions"][0]
    assert c_reg["white_frame_rect_local"] == [-35.0, -70.0, 35.0, 70.0]
    assert c_reg["has_custom_white_frame"] is True
    assert 190.0 <= c_reg["center"][0] <= 210.0

    # F30, F31, F104, F32, F33: Glossary TXT/YAML & batch set_fields validation
    txt_g = tmp_path / "post_dict.txt"
    txt_g.write_text("# header comment\nfoo\tbar\nunparsed_line\nbaz  qux\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        manage_glossary_entries("set_system_prompt", glossary_file=str(txt_g), system_prompt="hi")
    manage_glossary_entries("upsert", glossary_file=str(txt_g), entries=[{"original": "foo", "translation": "BAR2"}])
    txt_after = txt_g.read_text(encoding="utf-8")
    assert "# header comment" in txt_after
    assert "unparsed_line" in txt_after
    assert "foo\tBAR2" in txt_after

    yaml_g = tmp_path / "hq_glossary.yaml"
    yaml_g.write_text(
        "system_prompt: keep\nglossary:\n  Person:\n    - original: ましろ\n      aliases:\n        - original: ましろ\n          translations:\n            - text: 旧真白\n            - text: 椎名大人\n              condition: 敬称\n        - original: マシロ\n          translations:\n            - text: 真白\n      overwrite: true\n      description: 女主角\n",
        encoding="utf-8",
    )
    res_yaml = manage_glossary_entries(
        "upsert",
        glossary_file=str(yaml_g),
        category="Person",
        entries=[{"original": "ましろ", "translation": "真白"}],
    )
    rec_m = res_yaml["category_entries"][0]
    assert rec_m["overwrite"] is True
    assert rec_m["description"] == "女主角"
    assert len(rec_m["aliases"]) == 2
    assert len(rec_m["aliases"][0]["translations"]) == 2
    assert rec_m["aliases"][0]["translations"][0]["text"] == "真白"
    assert rec_m["aliases"][0]["translations"][1] == {"text": "椎名大人", "condition": "敬称"}

    with pytest.raises(ValueError, match="Unknown batch set_fields"):
        batch_edit_chapter_pages(ch, set_fields={"font_colour": "#ffffff"})
    nop_pages, nop_rep = batch_edit_chapter_pages(ch, set_fields={"font_size": 24}, filter_criteria={"region_indices": [0]})
    assert nop_pages == []
    assert nop_rep["matched_regions"] == 0
