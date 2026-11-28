"""Unit tests for richtext.v1 builder, atomic JSON store, snapshots, and batch editing."""

import _bootstrap  # noqa: F401, I001

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import mtui_mcp.bubble_centering as bubble_centering
import mtui_mcp.glossary_manager as glossary_manager
import mtui_mcp.local_inpaint as local_inpaint
import mtui_mcp.path_resolver as path_resolver
import mtui_mcp.project_store as project_store
from mtui_mcp.bubble_centering import partition_page_white_bubbles, run_closed_loop_bubble_centering
from mtui_mcp.glossary_manager import batch_edit_chapter_pages, manage_glossary_entries
from mtui_mcp.local_inpaint import (
    add_region_to_page,
    auto_detect_ink_mask_in_rect,
    build_region_polygon_mask,
    delete_regions_from_page,
    manage_page_paste_overlays,
    merge_regions_on_page,
)
from mtui_mcp.path_resolver import (
    list_manga_pages_in_path,
    natural_sort_key,
    resolve_page_paths,
    resolve_source_image_path,
)
from mtui_mcp.project_store import (
    align_regions_on_page,
    apply_region_updates_to_page,
    cleanup_snapshots_for_target,
    compute_page_revision,
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
    validate_and_normalize_style,
    validate_richtext_document,
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
    ready_q: queue.Queue[str] = queue.Queue(maxsize=2)

    def _read_ready_line() -> None:
        try:
            ready_q.put(proc.stdout.readline() if proc.stdout is not None else "")
        except Exception:
            ready_q.put("")

    threading.Thread(target=_read_ready_line, daemon=True).start()
    try:
        try:
            raw_line = ready_q.get(timeout=5.0)
        except queue.Empty:
            proc.kill()
            _out_diag, err_diag = proc.communicate(timeout=3.0)
            pytest.fail(f"Timed out waiting for child LOCKED signal; stderr={err_diag!r}")
        line = raw_line.strip()
        if line != "LOCKED":
            proc.kill()
            _out_diag, err_diag = proc.communicate(timeout=3.0)
            assert line == "LOCKED", f"Unexpected child output {line!r}; stderr={err_diag!r}"
        with pytest.raises(TimeoutError):
            with page_edit_lock(page, timeout_sec=0.08):
                pass
    finally:
        if proc.poll() is None and proc.stdin is not None:
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
            except OSError:
                pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=3.0)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

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
    rb_snap_dir = resolve_page_paths(p_inpaint).snapshots_dir / snaps_rb[0]["snapshot_id"]
    rb_manifest = json.loads((rb_snap_dir / "manifest.json").read_text(encoding="utf-8"))
    rb_inp_backup = rb_snap_dir / rb_manifest["files"]["inpainted"]["backup"]
    assert rb_inp_backup.is_file()
    assert np.array_equal(open_rgb_image(rb_inp_backup), orig_inp)

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


def _create_dir_link_or_junction(target_dir: Path, link_dir: Path) -> None:
    try:
        os.symlink(str(target_dir), str(link_dir), target_is_directory=True)
        return
    except OSError:
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_dir), str(target_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            return
        raise


def test_r2_snapshot_output_dir_change_symlink_and_completeness_guards(tmp_path: Path) -> None:
    # R2-F03 & R2-F21: Snapshot created under custom output_dir A restores cleanly after switching to output_dir B
    ch = tmp_path / "r2_snap_ch"
    p1 = _create_sample_manga_page(ch, "001.png")
    dir_a = tmp_path / "custom_out_a"
    dir_b = tmp_path / "custom_out_b"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)

    ren_v1 = np.full((800, 600, 3), 111, dtype=np.uint8)
    ren_v2 = np.full((800, 600, 3), 222, dtype=np.uint8)
    save_rgb_image_atomic(ren_v1, dir_a / "001.png")

    paths_a = resolve_page_paths(p1)
    ik, d_a = load_page_json(paths_a)
    d_a["last_output_path"] = str((dir_a / "001.png").resolve())
    d_a["last_export_dir"] = str(dir_a.resolve())
    d_a["regions"][0]["translation"] = "目录A旧版台词"
    save_page_json_atomic(paths_a, ik, d_a, auto_initial_snapshot=False)

    snap_a = create_snapshot(resolve_page_paths(p1), label="before_dir_b")
    assert "rendered" in snap_a["saved_artifacts"]

    # R3-F46 / R3-F22: Mutate A's file after snapshot so any stale pointer to A would read mutated pixels
    ren_a_mutated = np.full((800, 600, 3), 55, dtype=np.uint8)
    save_rgb_image_atomic(ren_a_mutated, dir_a / "001.png")

    # Now switch export target to custom output_dir B and edit text
    save_rgb_image_atomic(ren_v2, dir_b / "001.png")
    paths_b = resolve_page_paths(p1)
    ik_b, d_b = load_page_json(paths_b)
    d_b["last_output_path"] = str((dir_b / "001.png").resolve())
    d_b["last_export_dir"] = str(dir_b.resolve())
    d_b["regions"][0]["translation"] = "目录B新版台词"
    save_page_json_atomic(paths_b, ik_b, d_b, auto_initial_snapshot=False)
    assert resolve_page_paths(p1).rendered_output_path == (dir_b / "001.png").resolve()

    restored_res = restore_snapshot(p1, snap_a["snapshot_id"])
    assert any(item.startswith("rendered:") for item in restored_res["restored"])
    resolved_after_p1 = resolve_page_paths(p1)
    assert resolved_after_p1.rendered_output_path == (dir_b / "001.png").resolve()
    _, d_after = load_page_json(resolved_after_p1)
    assert d_after["regions"][0]["translation"] == "目录A旧版台词"
    assert Path(d_after["last_output_path"]).resolve() == (dir_b / "001.png").resolve()
    assert Path(d_after["last_export_dir"]).resolve() == dir_b.resolve()
    assert np.array_equal(open_rgb_image(resolved_after_p1.rendered_output_path), ren_v1)

    # R2-F19: Manifest cannot self-authorize an arbitrary external directory, and symlinked project subdirs are rejected
    untrusted_dir = tmp_path / "untrusted_target"
    mf_a = Path(snap_a["snapshot_dir"]) / "manifest.json"
    mf_obj = json.loads(mf_a.read_text(encoding="utf-8"))
    mf_obj["files"]["rendered"]["original"] = str(untrusted_dir / "001.png")
    mf_a.write_text(json.dumps(mf_obj), encoding="utf-8")
    restore_snapshot(p1, snap_a["snapshot_id"])
    assert not (untrusted_dir / "001.png").exists()

    p_sym = _create_sample_manga_page(ch, "002.png")
    snap_sym = create_snapshot(resolve_page_paths(p_sym), label="sym_check")
    outside_inp = tmp_path / "outside_inpainted"
    outside_inp.mkdir(parents=True)
    inp_dir = resolve_page_paths(p_sym).work_dir / "inpainted"
    shutil.rmtree(inp_dir)
    _create_dir_link_or_junction(outside_inp, inp_dir)
    with pytest.raises(ValueError, match="escapes allowed root|must not be a symlink"):
        restore_snapshot(p_sym, snap_sym["snapshot_id"])
    assert list(outside_inp.iterdir()) == []

    # R2-F22: Missing declared backup file in snapshot aborts BEFORE copying earlier JSON entry
    p_miss = _create_sample_manga_page(ch, "003.png")
    snap_miss = create_snapshot(resolve_page_paths(p_miss), label="miss_bak")
    apply_region_updates_to_page(p_miss, [{"region_index": 0, "translation": "保持不回退"}])
    (Path(snap_miss["snapshot_dir"]) / "inpainted.png").unlink()
    with pytest.raises(FileNotFoundError, match="missing backup file"):
        restore_snapshot(p_miss, snap_miss["snapshot_id"])
    _, d_still = load_page_json(resolve_page_paths(p_miss))
    assert d_still["regions"][0]["translation"] == "保持不回退"

    # R2-F23: Incomplete initial_before_mcp (manifest exists, backup missing) does NOT block ensure_initial_snapshot
    p_inc = _create_sample_manga_page(ch, "004.png")
    inc_paths = resolve_page_paths(p_inc)
    bad_init = inc_paths.snapshots_dir / "004__20260101_000000__initial_before_mcp"
    bad_init.mkdir(parents=True)
    (bad_init / "manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": bad_init.name,
                "page": "004.png",
                "files": {"json": {"original": str(inc_paths.json_path), "backup": "translations.json"}},
            }
        ),
        encoding="utf-8",
    )
    apply_region_updates_to_page(p_inc, [{"region_index": 0, "translation": "编辑后"}])
    snaps_inc = [s for s in list_snapshots(p_inc) if s.get("label") == "initial_before_mcp"]
    assert len(snaps_inc) == 1
    valid_init_dir = inc_paths.snapshots_dir / snaps_inc[0]["snapshot_id"]
    assert (valid_init_dir / "translations.json").is_file()


def test_r2_revision_unset_page_lock_consistency_and_distribute_dedup(tmp_path: Path) -> None:
    # R2-F20: compute_page_revision(paths, json_bytes=None) hashes empty JSON rather than re-reading a newer file
    ch = tmp_path / "r2_rev_ch"
    ch.mkdir(parents=True)
    raw_img = ch / "001.png"
    Image.fromarray(np.full((120, 120, 3), 240, dtype=np.uint8)).save(raw_img)
    paths_absent = resolve_page_paths(raw_img)
    assert paths_absent.json_exists is False

    # Create JSON on disk AFTER paths_absent was resolved, then pass json_bytes=None
    _create_sample_manga_page(ch, "001.png")
    paths_present = resolve_page_paths(raw_img)
    rev_explicit_none = compute_page_revision(paths_present, json_bytes=None)
    rev_actual_disk = compute_page_revision(paths_present)
    assert rev_explicit_none != rev_actual_disk

    # R2-F33: get_page_details_dict waits on page_edit_lock instead of reading half-committed (new_inpainted, old_json)
    writer_entered = threading.Event()
    allow_commit = threading.Event()
    old_inp = open_rgb_image(paths_present.inpainted_path)
    new_inp = np.full_like(old_inp, 123)

    def _slow_writer() -> None:
        with page_edit_lock(raw_img, timeout_sec=5.0):
            save_rgb_image_atomic(new_inp, paths_present.inpainted_path)
            writer_entered.set()
            allow_commit.wait(timeout=5.0)
            ik, data = load_page_json(resolve_page_paths(raw_img))
            data["regions"][0]["translation"] = "新版本已提交"
            save_page_json_atomic(resolve_page_paths(raw_img), ik, data, auto_initial_snapshot=False)

    wt = threading.Thread(target=_slow_writer, daemon=True)
    wt.start()
    assert writer_entered.wait(timeout=3.0)
    read_box: dict[str, object] = {}

    def _reader() -> None:
        read_box["details"] = get_page_details_dict(raw_img)

    rt = threading.Thread(target=_reader, daemon=True)
    rt.start()
    time.sleep(0.06)
    # Reader must still be blocked while writer holds page_edit_lock mid-transaction
    assert rt.is_alive()
    allow_commit.set()
    wt.join(timeout=3.0)
    rt.join(timeout=3.0)
    details = read_box["details"]
    assert isinstance(details, dict)
    assert details["regions"][0]["translation"] == "新版本已提交"
    assert details["revision"] == compute_page_revision(resolve_page_paths(raw_img))

    # R2-F56: Duplicate indices [0, 0, 1] rejected for distribute_horizontal without moving endpoint 0
    before_c0 = get_page_details_dict(raw_img)["regions"][0]["center"]
    with pytest.raises(ValueError, match="at least 3 regions"):
        align_regions_on_page(raw_img, [0, 0, 1], mode="distribute_horizontal")
    assert get_page_details_dict(raw_img)["regions"][0]["center"] == before_c0


def test_r2_richtext_nonfinite_rejection_ruby_inherit_bold_and_null_inlines(tmp_path: Path) -> None:
    # R2-F28: Reject NaN / inf / -inf across all numeric style fields
    bad_styles = [
        {"kerning": "nan"},
        {"preKerning": float("inf")},
        {"lineKerning": float("-inf")},
        {"nextKerning": float("nan")},
        {"scale": float("nan")},
        {"fontSize": "inf"},
        {"italic": float("nan")},
        {"stroke": {"width": float("inf")}},
        {"outerStroke": {"width": "nan"}},
        {"glow": {"blur": float("nan")}},
        {"transform": {"offsetX": float("nan")}},
        {"transform": {"offsetY": float("inf")}},
        {"transform": {"rotation": "-inf"}},
        {"color": [float("nan"), 0, 0]},
    ]
    for bs in bad_styles:
        with pytest.raises(ValueError):
            validate_and_normalize_style(bs)

    with pytest.raises(ValueError, match=r"translation_rich\.blocks\[0\]\.inlines\[0\]\.style"):
        validate_richtext_document(
            {
                "format": "richtext.v1",
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "测试", "style": {"kerning": "nan"}}],
                    }
                ],
            }
        )
    assert validate_and_normalize_style({"kerning": 0.15}) == {"kerning": 0.15}

    # R2-F61: ruby_style on non-ruby plain text must NOT infer rubyInherit=False or create empty rich text
    reg_plain = {"translation": "甲"}
    apply_span_styles_to_region(reg_plain, spans=[{"match": "甲", "ruby_style": {"color": "#112233"}}])
    assert "translation_rich" not in reg_plain

    # R2-F62: Unmatched span must NOT strip existing whole-region bold
    reg_bold = {"translation": "甲乙", "bold": True}
    apply_span_styles_to_region(reg_bold, spans=[{"match": "丙", "color": "#ff0000"}])
    assert reg_bold.get("bold") is True
    assert region_is_bold(reg_bold) is True

    # R2-F63: Empty region with reset_existing=True and explicit whole_style={"bold": True} preserves bold=True
    reg_empty = {"translation": ""}
    apply_span_styles_to_region(reg_empty, whole_style={"bold": True}, reset_existing=True)
    assert reg_empty.get("bold") is True

    # R2-F64: Legacy translation_rich with inlines: None saves cleanly without TypeError
    page = _create_sample_manga_page(tmp_path / "r2_null_inl", "001.png")
    paths = resolve_page_paths(page)
    ik, data = load_page_json(paths)
    data["regions"][0]["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {"type": "paragraph", "inlines": [{"type": "text", "text": "第一行", "style": {"bold": True}}]},
            {"type": "paragraph", "inlines": None},
        ],
    }
    save_page_json_atomic(paths, ik, data, auto_initial_snapshot=False)
    _, reloaded = load_page_json(resolve_page_paths(page))
    assert reloaded["regions"][0]["translation_rich"]["blocks"][1]["inlines"] == []


def test_r2_path_resolver_inpaint_rotated_mask_rollback_and_centering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R2-F26: Ambiguous multi-extension reverse lookup from result/<stem>.webp is refused; unique & same-ext succeed
    amb_ch = tmp_path / "amb_ch"
    _create_sample_manga_page(amb_ch, "001.png")
    Image.fromarray(np.full((100, 100, 3), 200, dtype=np.uint8)).save(amb_ch / "001.jpg")
    _create_sample_manga_page(amb_ch, "002.png")
    res_dir = amb_ch / "manga_translator_work" / "result"
    res_dir.mkdir(parents=True, exist_ok=True)
    for fn in ("001.webp", "001.jpg", "002.webp"):
        Image.fromarray(np.full((100, 100, 3), 210, dtype=np.uint8)).save(res_dir / fn)
    assert resolve_source_image_path(res_dir / "001.webp") == (res_dir / "001.webp").resolve()
    assert resolve_source_image_path(res_dir / "001.jpg") == (amb_ch / "001.jpg").resolve()
    assert resolve_source_image_path(res_dir / "002.webp") == (amb_ch / "002.png").resolve()

    # R2-F55: Recursive page listing orders by canonical source path even when 00-proof maps 002.png earlier
    vol = tmp_path / "r2_vol"
    ch01 = vol / "ch01"
    p_01 = _create_sample_manga_page(ch01, "001.png")
    p_02 = _create_sample_manga_page(ch01, "002.png")
    proof_dir = vol / "00-proof"
    proof_dir.mkdir(parents=True)
    proof_img = proof_dir / "002_proof.png"
    shutil.copy2(p_02, proof_img)
    (proof_dir / "translation_map.json").write_text(
        json.dumps({str(proof_img.resolve()): str(p_02.resolve())}),
        encoding="utf-8",
    )
    listed = list_manga_pages_in_path(vol, recursive=True)
    assert [p.source_path for p in listed] == [p_01.resolve(), p_02.resolve()]

    # R2-F31: Rollback failure preserves backup_tmp_path on disk and includes recovery path in error
    p_rb = _create_sample_manga_page(tmp_path / "r2_rb_fail", "001.png")
    rb_orig_inp = open_rgb_image(resolve_page_paths(p_rb).inpainted_path).copy()
    monkeypatch.setattr(local_inpaint, "save_page_json_atomic", lambda *a, **kw: (_ for _ in ()).throw(OSError("json boom")))
    monkeypatch.setattr(
        local_inpaint,
        "_atomic_replace_with_retry",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError("locked")),
    )
    with pytest.raises(OSError, match=r"\.rollback_") as exc_info:
        add_region_to_page(p_rb, bbox_xyxy=[115, 295, 225, 365], source_text="a", translation="b")
    inp_parent = resolve_page_paths(p_rb).inpainted_path.parent
    preserved = list(inp_parent.glob(".rollback_*"))
    assert len(preserved) == 1
    assert str(preserved[0]) in str(exc_info.value)
    assert np.array_equal(open_rgb_image(preserved[0]), rb_orig_inp)
    monkeypatch.undo()

    # R2-F32: Rotated angle=90.0 region polygon mask aligns with vertical world coordinates during delete
    rot_reg_90 = {
        "lines": [[[100.0, 180.0], [300.0, 180.0], [300.0, 220.0], [100.0, 220.0]]],
        "center": [200.0, 200.0],
        "angle": 90.0,
    }
    rot_reg_0 = {**rot_reg_90, "angle": 0.0}
    m_90 = build_region_polygon_mask(rot_reg_90, (400, 400))
    m_0 = build_region_polygon_mask(rot_reg_0, (400, 400))
    assert m_90[120, 200] == 255 and m_90[200, 120] == 0
    assert m_0[200, 120] == 255 and m_0[120, 200] == 0

    rot_dir = tmp_path / "r2_rot_del"
    rot_dir.mkdir(parents=True)
    rot_img = rot_dir / "001.png"
    raw_rot = np.full((400, 400, 3), 240, dtype=np.uint8)
    raw_rot[110:290, 185:215] = 25
    Image.fromarray(raw_rot).save(rot_img)
    rp = resolve_page_paths(rot_img)
    inp_rot = np.full((400, 400, 3), 240, dtype=np.uint8)
    save_rgb_image_atomic(inp_rot, rp.inpainted_path)
    vert_mask = np.zeros((400, 400), dtype=np.uint8)
    vert_mask[110:290, 185:215] = 255
    save_page_json_atomic(
        rp,
        str(rot_img.resolve()),
        {
            "mask_raw": encode_mask_base64(vert_mask),
            "regions": [
                {
                    **rot_reg_90,
                    "text": "縦",
                    "translation": "竖",
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    ref_rot, del_rot_info = delete_regions_from_page(rot_img, [0], restore_artwork=True)
    assert del_rot_info["artwork_restored"] is True
    assert np.array_equal(open_rgb_image(ref_rot.inpainted_path)[110:290, 185:215], raw_rot[110:290, 185:215])

    # R2-F60: Diagonal regions inside a single unsplit white rectangle merge without false multi-lobe rejection
    diag_dir = tmp_path / "r2_diag_rect"
    diag_dir.mkdir(parents=True)
    diag_img = diag_dir / "001.png"
    diag_canvas = np.full((600, 600, 3), 160, dtype=np.uint8)
    cv2.rectangle(diag_canvas, (100, 100), (420, 460), (255, 255, 255), -1)
    cv2.rectangle(diag_canvas, (100, 100), (420, 460), (10, 10, 10), 4)
    Image.fromarray(diag_canvas).save(diag_img)
    dp = resolve_page_paths(diag_img)
    save_rgb_image_atomic(diag_canvas, dp.inpainted_path)
    save_page_json_atomic(
        dp,
        str(diag_img.resolve()),
        {
            "regions": [
                {
                    "lines": [[[130.0, 130.0], [210.0, 130.0], [210.0, 230.0], [130.0, 230.0]]],
                    "text": "上",
                    "translation": "甲",
                    "direction": "v",
                },
                {
                    "lines": [[[300.0, 310.0], [380.0, 310.0], [380.0, 420.0], [300.0, 420.0]]],
                    "text": "下",
                    "translation": "乙",
                    "direction": "v",
                },
            ]
        },
        auto_initial_snapshot=False,
    )
    _, merged_rect = merge_regions_on_page(diag_img, [0, 1])
    assert "甲" in merged_rect["translation"] and "乙" in merged_rect["translation"]

    # R2-F24, R2-F25, R2-F58: Stale ink ignored on pass 0, direction change invalidates lobe cache, and render_status surfaced
    bc_dir = tmp_path / "r2_bc"
    bc_dir.mkdir(parents=True)
    bc_img = bc_dir / "001.png"
    bc_canvas = np.full((500, 500, 3), 180, dtype=np.uint8)
    cv2.rectangle(bc_canvas, (100, 100), (300, 340), (255, 255, 255), -1)
    cv2.rectangle(bc_canvas, (100, 100), (300, 340), (0, 0, 0), 4)
    Image.fromarray(bc_canvas).save(bc_img)
    bcp = resolve_page_paths(bc_img)
    save_rgb_image_atomic(bc_canvas, bcp.inpainted_path)
    # Stale rendered output has old ink shifted far left (x=140..170), written BEFORE region center moved to x=200
    stale_ren = bc_canvas.copy()
    stale_ren[170:270, 140:170] = 10
    bc_res_path = bc_dir / "manga_translator_work" / "result" / "001.png"
    save_rgb_image_atomic(stale_ren, bc_res_path)
    old_t = time.time() - 10.0
    os.utime(bc_res_path, (old_t, old_t))
    save_page_json_atomic(
        bcp,
        str(bc_img.resolve()),
        {
            "last_output_path": str(bc_res_path),
            "regions": [
                {
                    "lines": [[[170.0, 160.0], [230.0, 160.0], [230.0, 280.0], [170.0, 280.0]]],
                    "center": [200.0, 220.0],
                    "translation": "已居中",
                    "font_size": 26,
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    # Without render_page_fn, stale ink at x=140..170 must NOT push center=200 rightward to ~245!
    rep_stale = run_closed_loop_bubble_centering(bc_dir, passes=1, render_page_fn=None)
    assert rep_stale["001.png"][0]["render_status"] == "not_requested"
    _, d_no_shift = load_page_json(resolve_page_paths(bc_img))
    assert abs(d_no_shift["regions"][0]["center"][0] - 200.0) <= 5.0

    # R3-F47 (1): When callback returns "ok" after flipping direction="h", Pass 1 runs, invalidates cached vertical lobe,
    # and does NOT move the newly horizontal region back to center!
    apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "v", "center": [245.0, 220.0]}])
    os.utime(bc_res_path, (old_t, old_t))
    render_out_ok: dict[str, object] = {}
    call_idx_ok = {"n": 0}

    def _mock_render_ok(pp):
        call_idx_ok["n"] += 1
        fresh_ren = bc_canvas.copy()
        _, cur_d = load_page_json(pp)
        cx_cur = int(round(cur_d["regions"][0]["center"][0]))
        fresh_ren[170:270, max(0, cx_cur - 15) : min(500, cx_cur + 15)] = 10
        save_rgb_image_atomic(fresh_ren, bc_res_path)
        nt = time.time() + call_idx_ok["n"]
        os.utime(bc_res_path, (nt, nt))
        if call_idx_ok["n"] == 2:
            # Flip to horizontal at off-center x=245 before Pass 1; Pass 1 must enter and stop via lobe cache invalidation!
            apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "h", "center": [245.0, 220.0]}])
            os.utime(bc_res_path, (nt + 2.0, nt + 2.0))
        return {"status": "ok", "metadata_saved": True, "output_path": str(bc_res_path)}

    rep_loop_ok = run_closed_loop_bubble_centering(
        bc_dir,
        passes=3,
        render_page_fn=_mock_render_ok,
        render_results_out=render_out_ok,
    )
    assert call_idx_ok["n"] == 2  # Pass 0 pre-render + Pass 0 post-update; Pass 1 entered & stopped because direction=="h"!
    assert rep_loop_ok["001.png"][0]["render_status"] == "ok"
    assert isinstance(render_out_ok.get("001.png"), dict)
    assert render_out_ok["001.png"]["status"] == "ok"  # type: ignore[index]
    _, d_horiz_kept = load_page_json(resolve_page_paths(bc_img))
    assert d_horiz_kept["regions"][0]["center"][0] == pytest.approx(245.0)

    # R3-F47 (2): When callback returns "partial", the loop stops early and preserves render_status == "partial"
    apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "v", "center": [245.0, 220.0]}])
    os.utime(bc_res_path, (old_t, old_t))
    render_out: dict[str, object] = {}
    call_idx = {"n": 0}

    def _mock_render_partial(pp):
        call_idx["n"] += 1
        fresh_ren = bc_canvas.copy()
        _, cur_d = load_page_json(pp)
        cx_cur = int(round(cur_d["regions"][0]["center"][0]))
        fresh_ren[170:270, max(0, cx_cur - 15) : min(500, cx_cur + 15)] = 10
        save_rgb_image_atomic(fresh_ren, bc_res_path)
        nt = time.time() + call_idx["n"]
        os.utime(bc_res_path, (nt, nt))
        if call_idx["n"] == 2:
            return {"status": "partial", "metadata_saved": False, "output_path": str(bc_res_path)}
        return {"status": "ok", "metadata_saved": True, "output_path": str(bc_res_path)}

    rep_loop = run_closed_loop_bubble_centering(
        bc_dir,
        passes=3,
        render_page_fn=_mock_render_partial,
        render_results_out=render_out,
    )
    assert call_idx["n"] == 2
    assert rep_loop["001.png"][0]["render_status"] == "partial"
    assert isinstance(render_out.get("001.png"), dict)
    assert render_out["001.png"]["status"] == "partial"  # type: ignore[index]


def test_r2_glossary_partial_bold_txt_tabs_locking_fsync_and_batch_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R2-F04 / R2-F67: Partial inline bold cleared by batch set_fields={"bold": False}; already-unbold is a no-op
    ch_b = tmp_path / "r2_batch_bold"
    pb1 = _create_sample_manga_page(ch_b, "001.png")
    p_paths = resolve_page_paths(pb1)
    ik, d = load_page_json(p_paths)
    apply_span_styles_to_region(d["regions"][0], spans=[{"match": "真白", "bold": True}])
    save_page_json_atomic(p_paths, ik, d, auto_initial_snapshot=False)
    _, d_check = load_page_json(resolve_page_paths(pb1))
    assert region_is_bold(d_check["regions"][0]) is False
    assert is_rich_text_document(d_check["regions"][0].get("translation_rich"))

    mod_b, rep_b = batch_edit_chapter_pages(ch_b, set_fields={"bold": False})
    assert len(mod_b) == 1
    assert rep_b["matched_regions"] == 1
    _, d_cleared = load_page_json(resolve_page_paths(pb1))
    assert "bold" not in d_cleared["regions"][0]
    assert "translation_rich" not in d_cleared["regions"][0]

    # Second run on already-unbold page must be a no-op
    mod_b2, rep_b2 = batch_edit_chapter_pages(ch_b, set_fields={"bold": False})
    assert mod_b2 == []
    assert rep_b2["matched_regions"] == 0

    # R2-F66: Multi-word TXT glossary entry "New York\t纽约" round-trips through upsert, list, update, delete
    txt_g = tmp_path / "places.txt"
    manage_glossary_entries(
        "upsert",
        glossary_file=str(txt_g),
        entries=[{"original": "New York", "translation": "纽约"}],
    )
    listed_ny = manage_glossary_entries("list", glossary_file=str(txt_g))
    assert listed_ny["entries"] == [{"original": "New York", "translation": "纽约"}]
    manage_glossary_entries(
        "upsert",
        glossary_file=str(txt_g),
        entries=[{"original": "New York", "translation": "新约克"}],
    )
    listed_ny2 = manage_glossary_entries("list", glossary_file=str(txt_g))
    assert listed_ny2["count"] == 1
    assert listed_ny2["entries"] == [{"original": "New York", "translation": "新约克"}]
    del_ny = manage_glossary_entries("delete", glossary_file=str(txt_g), entries=[{"original": "New York"}])
    assert del_ny["deleted"] == 1 and del_ny["count"] == 0

    # R2-F29: Concurrent TXT glossary upserts serialize cleanly without lost updates
    conc_txt = tmp_path / "concurrent.txt"
    errs: list[Exception] = []

    def _upsert_worker(tid: int) -> None:
        try:
            manage_glossary_entries(
                "upsert",
                glossary_file=str(conc_txt),
                entries=[{"original": f"Term {tid}", "translation": f"词条{tid}"}],
            )
        except Exception as exc:
            errs.append(exc)

    threads = [threading.Thread(target=_upsert_worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert errs == []
    final_list = manage_glossary_entries("list", glossary_file=str(conc_txt))
    assert final_list["count"] == 12

    # R2-F65: Parent directory fsync invoked after atomic YAML & TXT saves
    fsynced_dirs: list[str] = []
    monkeypatch.setattr(glossary_manager, "_fsync_parent_dir_best_effort", lambda p: fsynced_dirs.append(str(p.parent)))
    manage_glossary_entries("upsert", glossary_file=str(txt_g), entries=[{"original": "A", "translation": "甲"}])
    manage_glossary_entries(
        "upsert",
        glossary_file=str(tmp_path / "sync.yaml"),
        category="Person",
        entries=[{"original": "B", "translation": "乙"}],
    )
    assert len(fsynced_dirs) == 2
    monkeypatch.undo()

    # R2-F30: Pre-validate spans across ALL target pages before writing any page to disk
    ch_stage = tmp_path / "r2_stage_ch"
    ps1 = _create_sample_manga_page(ch_stage, "001.png")
    ps2 = _create_sample_manga_page(ch_stage, "002.png")
    p2_paths = resolve_page_paths(ps2)
    ik2, d2 = load_page_json(p2_paths)
    d2["regions"][0]["translation"] = "前往东京塔"
    apply_span_styles_to_region(
        d2["regions"][0],
        spans=[{"match": "东京", "ruby": "とうきょう", "ruby_style": {"color": "#112233"}}],
    )
    save_page_json_atomic(p2_paths, ik2, d2, auto_initial_snapshot=False)

    with pytest.raises(ValueError, match="partially covers ruby node"):
        batch_edit_chapter_pages(
            ch_stage,
            set_fields={"font_size_delta": 4},
            rich_spans=[{"match": "东", "ruby_style": {"color": "#ff0000"}}],
        )
    # Page 1 must NOT have had font_size_delta=+4 saved!
    _, d1_untouched = load_page_json(resolve_page_paths(ps1))
    assert d1_untouched["regions"][0]["font_size"] == 24
    assert list_snapshots(ps1) == []

    # Mid-commit OS failure reports committed_pages and failed_page so callers never double-apply font_size_delta
    real_save = glossary_manager.save_page_json_atomic
    save_calls = {"n": 0}

    def _fail_second_page(*args, **kwargs):
        save_calls["n"] += 1
        if save_calls["n"] == 2:
            raise OSError("disk full on page 2")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(glossary_manager, "save_page_json_atomic", _fail_second_page)
    with pytest.raises(RuntimeError, match=r"committed_pages=\['001\.png'\].*failed_page='002\.png'") as mid_err:
        batch_edit_chapter_pages(ch_stage, set_fields={"font_size_delta": 4})
    assert getattr(mid_err.value, "committed_pages", None) == ["001.png"]
    assert getattr(mid_err.value, "failed_page", None) == "002.png"


def test_r3_f21_f23_f24_f25_store_richtext_null_blocks_and_transactional_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R3-F21: `translation_rich` with `blocks: None` or missing `blocks` must fall back to plain `translation`
    # and never wipe `translation` on load or save, while still fixing `inlines: None` inside valid list `blocks`.
    p_null = _create_sample_manga_page(tmp_path / "r3_f21_ch", "001.png")
    p_null_paths = resolve_page_paths(p_null)
    raw_obj = json.loads(p_null_paths.json_path.read_text(encoding="utf-8"))
    page_entry = next(iter(raw_obj.values()))
    page_entry["regions"][0]["translation"] = "保留纯文本[BR]第二行"
    page_entry["regions"][0]["translation_rich"] = {"format": "richtext.v1", "blocks": None}
    page_entry["regions"][1]["translation"] = "缺失blocks回退"
    page_entry["regions"][1]["translation_rich"] = {"format": "richtext.v1"}
    page_entry["regions"].append(
        {
            "lines": [[[40.0, 420.0], [180.0, 420.0], [180.0, 520.0], [40.0, 520.0]]],
            "text": "スタイル",
            "translation": "样式段[BR]",
            "direction": "h",
            "translation_rich": {
                "format": "richtext.v1",
                "blocks": [
                    {"type": "paragraph", "inlines": [{"type": "text", "text": "样式段", "style": {"color": "#ff3300"}}]},
                    {"type": "paragraph", "inlines": None},
                ],
            },
        }
    )
    p_null_paths.json_path.write_text(json.dumps(raw_obj, ensure_ascii=False), encoding="utf-8")

    details_f21 = get_page_details_dict(p_null)
    assert details_f21["regions"][0]["translation"] == "保留纯文本\n第二行"
    assert details_f21["regions"][0]["has_rich_text"] is False
    assert details_f21["regions"][1]["translation"] == "缺失blocks回退"
    assert details_f21["regions"][1]["has_rich_text"] is False
    assert details_f21["regions"][2]["has_rich_text"] is True

    ik_f21, loaded_f21 = load_page_json(resolve_page_paths(p_null))
    save_page_json_atomic(resolve_page_paths(p_null), ik_f21, loaded_f21, auto_initial_snapshot=False)
    _, saved_f21 = load_page_json(resolve_page_paths(p_null))
    assert saved_f21["regions"][0]["translation"] == "保留纯文本[BR]第二行"
    assert "translation_rich" not in saved_f21["regions"][0]
    assert saved_f21["regions"][1]["translation"] == "缺失blocks回退"
    assert "translation_rich" not in saved_f21["regions"][1]
    assert saved_f21["regions"][2]["translation_rich"]["blocks"][0]["inlines"][0]["style"]["color"] == "#ff3300"
    assert saved_f21["regions"][2]["translation_rich"]["blocks"][1]["inlines"] == []

    # R3-F23: Restoring `.png` inpainted snapshot for `001.jpg` when `001_inpainted.jpg` exists must make `.png` active
    ch_f23 = tmp_path / "r3_f23_ch"
    p_jpg = _create_sample_manga_page(ch_f23, "001.jpg")
    paths_jpg = resolve_page_paths(p_jpg)
    # Replace initial `.jpg` sidecar with `.png` sidecar before taking snapshot
    paths_jpg.inpainted_path.unlink()
    inp_png_path = paths_jpg.work_dir / "inpainted" / "001_inpainted.png"
    inp_snap_pixels = np.full((800, 600, 3), 77, dtype=np.uint8)
    save_rgb_image_atomic(inp_snap_pixels, inp_png_path)
    assert resolve_page_paths(p_jpg).inpainted_path == inp_png_path
    snap_png_inp = create_snapshot(resolve_page_paths(p_jpg), label="png_inpaint_base")

    # Now create a newer `.jpg` sidecar (which shadows `.png` for `001.jpg`) and edit text
    inp_jpg_path = paths_jpg.work_dir / "inpainted" / "001_inpainted.jpg"
    save_rgb_image_atomic(np.full((800, 600, 3), 199, dtype=np.uint8), inp_jpg_path)
    apply_region_updates_to_page(p_jpg, [{"region_index": 0, "translation": "新版JPG修复图台词"}])
    assert resolve_page_paths(p_jpg).inpainted_path == inp_jpg_path

    restore_snapshot(p_jpg, snap_png_inp["snapshot_id"])
    after_f23 = resolve_page_paths(p_jpg)
    assert after_f23.inpainted_exists is True
    assert after_f23.inpainted_path == inp_png_path
    assert not inp_jpg_path.exists()
    with Image.open(after_f23.inpainted_path) as im_chk:
        assert im_chk.format == "PNG"
    assert np.array_equal(open_rgb_image(after_f23.inpainted_path), inp_snap_pixels)

    # R3-F24 (1): Transactional multi-artifact restore rolls back JSON if replacing inpainted fails mid-restore
    ch_f24 = tmp_path / "r3_f24_ch"
    p_tx = _create_sample_manga_page(ch_f24, "001.png")
    snap_tx = create_snapshot(resolve_page_paths(p_tx), label="tx_base")
    apply_region_updates_to_page(p_tx, [{"region_index": 0, "translation": "失败前保持的新译文"}])
    new_inp_tx = np.full((800, 600, 3), 144, dtype=np.uint8)
    save_rgb_image_atomic(new_inp_tx, resolve_page_paths(p_tx).inpainted_path)

    real_copy_atomic = project_store._copy_file_atomic

    def _fail_on_inpainted(src: Path, dst: Path) -> None:
        if dst.parent.name == "inpainted":
            raise PermissionError("inpainted file locked")
        real_copy_atomic(src, dst)

    monkeypatch.setattr(project_store, "_copy_file_atomic", _fail_on_inpainted)
    with pytest.raises(PermissionError, match="inpainted file locked"):
        restore_snapshot(p_tx, snap_tx["snapshot_id"])
    monkeypatch.setattr(project_store, "_copy_file_atomic", real_copy_atomic)

    _, tx_after = load_page_json(resolve_page_paths(p_tx))
    assert tx_after["regions"][0]["translation"] == "失败前保持的新译文"
    assert np.array_equal(open_rgb_image(resolve_page_paths(p_tx).inpainted_path), new_inp_tx)
    assert list(resolve_page_paths(p_tx).json_path.parent.glob(".rollback_*")) == []

    # R3-F24 (2): If rollback itself fails, preserve backup file and report affected target + backup path
    monkeypatch.setattr(project_store, "_copy_file_atomic", _fail_on_inpainted)
    real_replace = project_store._atomic_replace_with_retry

    def _fail_rollback_replace(src, dst, **kwargs):
        if str(Path(src).name).startswith(".rollback_"):
            raise PermissionError("json rollback locked")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(project_store, "_atomic_replace_with_retry", _fail_rollback_replace)
    with pytest.raises(OSError, match=r"Snapshot restore rollback failed.*\.rollback_") as rb_double_err:
        restore_snapshot(p_tx, snap_tx["snapshot_id"])
    monkeypatch.setattr(project_store, "_atomic_replace_with_retry", real_replace)
    monkeypatch.setattr(project_store, "_copy_file_atomic", real_copy_atomic)

    json_baks = list(resolve_page_paths(p_tx).json_path.parent.glob(".rollback_*"))
    assert len(json_baks) == 1
    assert str(json_baks[0]) in str(rb_double_err.value)
    assert str(resolve_page_paths(p_tx).json_path) in str(rb_double_err.value)

    # R3-F25: `001__extra.png` initial snapshot must NOT suppress `001.png`'s own `initial_before_mcp` snapshot
    ch_f25 = tmp_path / "r3_f25_ch"
    p_001 = _create_sample_manga_page(ch_f25, "001.png")
    p_extra = _create_sample_manga_page(ch_f25, "001__extra.png")
    apply_region_updates_to_page(p_extra, [{"region_index": 0, "translation": "附页修改"}])
    apply_region_updates_to_page(p_001, [{"region_index": 0, "translation": "主页修改"}])
    snaps_001 = [s for s in list_snapshots(p_001) if s.get("label") == "initial_before_mcp"]
    assert len(snaps_001) == 1
    assert snaps_001[0]["page"] == "001.png"
    restore_snapshot(p_001, label="initial_before_mcp")
    _, restored_001 = load_page_json(resolve_page_paths(p_001))
    assert restored_001["regions"][0]["translation"] == "真白酱！？"


def test_r3_f06_f51_f43_f44_paths_and_inpaint_clipping_and_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R3-F06: Non-decimal Unicode superscripts like `²` (`"²".isdigit() == True`) must never crash `natural_sort_key`
    assert natural_sort_key("page².png") == [(True, "page².png")]
    assert natural_sort_key("1²2.png") == [(False, 1), (True, "²"), (False, 2), (True, ".png")]
    sup_dir = tmp_path / "r3_sup_ch"
    p_norm = _create_sample_manga_page(sup_dir, "001.png")
    p_sup = _create_sample_manga_page(sup_dir, "page².png")
    p_mid = _create_sample_manga_page(sup_dir, "1²2.png")
    listed_sup = list_manga_pages_in_path(sup_dir)
    assert [p.source_path for p in listed_sup] == [p_norm.resolve(), p_mid.resolve(), p_sup.resolve()]

    # R3-F51: Uppercase `.PNG` sidecar fallback lookup + deterministic selection priority
    up_dir = tmp_path / "r3_upper_inp"
    p_up_jpg = _create_sample_manga_page(up_dir, "001.jpg")
    up_paths = resolve_page_paths(p_up_jpg)
    up_paths.inpainted_path.unlink()
    upper_png = up_paths.work_dir / "inpainted" / "001_inpainted.PNG"
    save_rgb_image_atomic(np.full((800, 600, 3), 230, dtype=np.uint8), upper_png)
    resolved_up = resolve_page_paths(p_up_jpg)
    assert resolved_up.inpainted_exists is True
    assert resolved_up.inpainted_path.name == "001_inpainted.PNG"

    # Simulate a case-sensitive filesystem directory containing both `.PNG` and `.JPG` (and lowercase vs uppercase tie-break)
    class _FakeDirEntry:
        def __init__(self, name: str) -> None:
            self.name = name
            p = Path(name)
            self.stem = p.stem
            self.suffix = p.suffix

        def is_file(self) -> bool:
            return True

    class _FakeCaseSensitiveDir:
        def __init__(self, names: list[str]) -> None:
            self._entries = [_FakeDirEntry(n) for n in names]

        def __truediv__(self, child: str):
            for e in self._entries:
                if e.name == child:
                    return e
            missing = _FakeDirEntry(child)
            missing.is_file = lambda: False  # type: ignore[method-assign]
            return missing

        def is_dir(self) -> bool:
            return True

        def iterdir(self):
            return iter(self._entries)

    chosen_1, ok_1 = path_resolver._resolve_inpainted_sidecar(
        _FakeCaseSensitiveDir(["001_inpainted.JPG", "001_inpainted.PNG"]),  # type: ignore[arg-type]
        "001",
        ".jpg",
    )
    assert ok_1 is True and chosen_1.name == "001_inpainted.PNG"

    chosen_2, ok_2 = path_resolver._resolve_inpainted_sidecar(
        _FakeCaseSensitiveDir(["001_inpainted.PNG", "001_inpainted.png"]),  # type: ignore[arg-type]
        "001",
        ".bmp",
    )
    assert ok_2 is True and chosen_2.name == "001_inpainted.png"

    chosen_3, ok_3 = path_resolver._resolve_inpainted_sidecar(
        _FakeCaseSensitiveDir(["001_inpainted.PNG", "001_inpainted.jpg"]),  # type: ignore[arg-type]
        "001",
        ".jpg",
    )
    assert ok_3 is True and chosen_3.name == "001_inpainted.jpg"

    # R3-F43: Report residual uncommitted `inpainted_path` when rollback `unlink()` fails on newly created sidecar
    p_new_rb = _create_sample_manga_page(tmp_path / "r3_f43_ch", "001.png")
    new_rb_paths = resolve_page_paths(p_new_rb)
    new_rb_paths.inpainted_path.unlink()
    assert resolve_page_paths(p_new_rb).inpainted_exists is False

    monkeypatch.setattr(
        local_inpaint,
        "save_page_json_atomic",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("json save boom")),
    )
    real_path_unlink = Path.unlink

    def _deny_inpainted_unlink(self: Path, *args, **kwargs) -> None:
        if self == new_rb_paths.inpainted_path:
            raise PermissionError("unlink denied")
        real_path_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _deny_inpainted_unlink)
    with pytest.raises(RuntimeError, match=r"json save boom.*Failed to remove uncommitted inpainted image.*unlink denied") as f43_err:
        add_region_to_page(p_new_rb, bbox_xyxy=[115, 295, 225, 365], source_text="x", translation="y")
    assert f43_err.value.__cause__ is not None
    assert str(new_rb_paths.inpainted_path) in str(f43_err.value)
    monkeypatch.setattr(Path, "unlink", real_path_unlink)
    monkeypatch.undo()

    # R3-F44: Off-canvas rotated/shifted polygons produce an empty mask rather than clamping onto canvas edges
    off_left = {
        "lines": [[[-90.0, 60.0], [-15.0, 60.0], [-15.0, 180.0], [-90.0, 180.0]]],
        "center": [-52.5, 120.0],
        "angle": 0.0,
    }
    assert not np.any(build_region_polygon_mask(off_left, (240, 240), expand_px=8))

    rot_outside = {
        "lines": [[[10.0, 80.0], [70.0, 80.0], [70.0, 120.0], [10.0, 120.0]]],
        "center": [5.0, 100.0],
        "angle": 180.0,  # Rotates [10..70] around cx=5 into [-60..0], completely outside canvas interior!
    }
    assert not np.any(build_region_polygon_mask(rot_outside, (240, 240), expand_px=6))

    partial_overlap = {
        "lines": [[[-30.0, 50.0], [25.0, 50.0], [25.0, 110.0], [-30.0, 110.0]]],
        "center": [-2.5, 80.0],
        "angle": 0.0,
    }
    m_part = build_region_polygon_mask(partial_overlap, (200, 200), expand_px=0)
    assert int((m_part[55:105, 0:24] > 0).sum()) > 1000
    assert not np.any(m_part[:, 30:])


def test_r3_f02_f19_f48_f49_f50_f26_f45_centering_and_glossary_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R3-F49: `_regions_partition_signature` preserves sub-pixel/degree boundaries (`100.499` vs `100.501`, `2.999` vs `3.001`)
    sig_c1 = bubble_centering._regions_partition_signature([{"region_uid": "u1", "center": [100.499, 200.0], "direction": "v", "angle": 0.0}])
    sig_c2 = bubble_centering._regions_partition_signature([{"region_uid": "u1", "center": [100.501, 200.0], "direction": "v", "angle": 0.0}])
    assert sig_c1 != sig_c2
    sig_a1 = bubble_centering._regions_partition_signature([{"region_uid": "u1", "center": [100.0, 200.0], "direction": "v", "angle": 2.999}])
    sig_a2 = bubble_centering._regions_partition_signature([{"region_uid": "u1", "center": [100.0, 200.0], "direction": "v", "angle": 3.001}])
    assert sig_a1 != sig_a2

    # Shared white-bubble fixture centered at (200, 220)
    bc_dir = tmp_path / "r3_bc"
    bc_dir.mkdir(parents=True)
    bc_img = bc_dir / "001.png"
    bc_canvas = np.full((500, 500, 3), 180, dtype=np.uint8)
    cv2.rectangle(bc_canvas, (100, 100), (300, 340), (255, 255, 255), -1)
    cv2.rectangle(bc_canvas, (100, 100), (300, 340), (0, 0, 0), 4)
    Image.fromarray(bc_canvas).save(bc_img)
    bcp = resolve_page_paths(bc_img)
    save_rgb_image_atomic(bc_canvas, bcp.inpainted_path)
    bc_res_path = bc_dir / "manga_translator_work" / "result" / "001.png"

    def _write_rendered_at(cx_pos: float, mtime_val: float) -> None:
        ren = bc_canvas.copy()
        cxi = int(round(cx_pos))
        ren[170:270, max(0, cxi - 15) : min(500, cxi + 15)] = 10
        save_rgb_image_atomic(ren, bc_res_path)
        os.utime(bc_res_path, (mtime_val, mtime_val))

    old_t = time.time() - 20.0
    _write_rendered_at(245.0, old_t)
    save_page_json_atomic(
        bcp,
        str(bc_img.resolve()),
        {
            "last_output_path": str(bc_res_path),
            "regions": [
                {
                    "lines": [[[215.0, 160.0], [275.0, 160.0], [275.0, 280.0], [215.0, 280.0]]],
                    "center": [245.0, 220.0],
                    "translation": "并发预渲染",
                    "font_size": 26,
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )

    # R3-F02 / R3-F20 (a): Concurrent flip to `direction="h"` during Pass-0 pre-render must NOT overwrite horizontal region!
    def _prerender_flip_horiz(pp):
        _write_rendered_at(245.0, time.time() + 1.0)
        apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "h", "center": [238.0, 220.0]}])
        return {"status": "ok", "output_path": str(bc_res_path)}

    rep_f02_h = run_closed_loop_bubble_centering(bc_dir, passes=2, render_page_fn=_prerender_flip_horiz)
    assert rep_f02_h == {}
    _, d_f02_h = load_page_json(resolve_page_paths(bc_img))
    assert d_f02_h["regions"][0]["direction"] == "horizontal"
    assert d_f02_h["regions"][0]["center"][0] == pytest.approx(238.0)

    # R3-F02 / R3-F20 (b): Concurrent move to `[200.0, 220.0]` during Pass-0 pre-render must NOT apply old `-45px` shift!
    apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "v", "center": [245.0, 220.0]}])
    os.utime(bc_res_path, (old_t, old_t))

    def _prerender_concurrent_center(pp):
        _write_rendered_at(245.0, time.time() + 1.0)
        apply_region_updates_to_page(bc_img, [{"region_index": 0, "center": [200.0, 220.0]}])
        return {"status": "ok", "output_path": str(bc_res_path)}

    run_closed_loop_bubble_centering(bc_dir, passes=2, render_page_fn=_prerender_concurrent_center)
    _, d_f02_c = load_page_json(resolve_page_paths(bc_img))
    assert abs(d_f02_c["regions"][0]["center"][0] - 200.0) <= 5.0  # Never double-shifted to ~155!

    # R3-F19: Final-pass callback returning `"ok"` without updating rendered output marks status `"partial"`
    apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "v", "center": [245.0, 220.0]}])
    _write_rendered_at(245.0, time.time() + 5.0)
    stale_out_res: dict[str, object] = {}

    def _noop_render_claiming_ok(pp):
        # Intentionally does NOT update bc_res_path!
        return {"status": "ok", "output_path": str(bc_res_path)}

    rep_f19 = run_closed_loop_bubble_centering(
        bc_dir,
        passes=1,
        render_page_fn=_noop_render_claiming_ok,
        render_results_out=stale_out_res,
    )
    assert rep_f19["001.png"][0]["render_status"] == "partial"
    assert stale_out_res["001.png"]["status"] == "partial"  # type: ignore[index]
    assert stale_out_res["001.png"]["output_updated"] is False  # type: ignore[index]

    # R3-F48: Already-centered page with fresh rendered output keeps `render_status == "unchanged"` without invoking callback
    apply_region_updates_to_page(bc_img, [{"region_index": 0, "direction": "v", "center": [200.0, 220.0]}])
    _write_rendered_at(200.0, time.time() + 10.0)
    unchanged_calls: list[str] = []
    unchanged_out: dict[str, object] = {}
    rep_f48 = run_closed_loop_bubble_centering(
        bc_dir,
        passes=2,
        render_page_fn=lambda pp: unchanged_calls.append(pp.source_path.name) or {"status": "ok"},
        render_results_out=unchanged_out,
    )
    assert unchanged_calls == []
    assert unchanged_out == {}
    assert rep_f48["001.png"][0]["render_status"] == "unchanged"

    # R3-F50: First-time `last_export_dir` / `last_output_path` write validates `verified_for_rev`, while concurrent region edit taints `post_fp`
    first_dir = tmp_path / "r3_f50_first"
    first_dir.mkdir(parents=True)
    first_img = first_dir / "001.png"
    Image.fromarray(bc_canvas).save(first_img)
    fp_first = resolve_page_paths(first_img)
    save_rgb_image_atomic(bc_canvas, fp_first.inpainted_path)
    save_page_json_atomic(
        fp_first,
        str(first_img.resolve()),
        {
            "regions": [
                {
                    "lines": [[[215.0, 160.0], [275.0, 160.0], [275.0, 280.0], [215.0, 280.0]]],
                    "center": [245.0, 220.0],
                    "translation": "首次导出元数据",
                    "font_size": 26,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    custom_exp = tmp_path / "r3_f50_out"
    custom_exp.mkdir(parents=True)
    f50_calls = {"n": 0}

    def _first_export_render(pp):
        f50_calls["n"] += 1
        out_f = custom_exp / "001.png"
        _, d_cur = load_page_json(pp)
        cx_now = int(round(d_cur["regions"][0]["center"][0]))
        ren = bc_canvas.copy()
        ren[170:270, max(0, cx_now - 15) : min(500, cx_now + 15)] = 10
        save_rgb_image_atomic(ren, out_f)
        # Persist last_export_dir / last_output_path on first render just like engine_worker._persist_last_export_dir
        raw_j = json.loads(pp.json_path.read_bytes().decode("utf-8"))
        ent = next(iter(raw_j.values()))
        if ent.get("last_output_path") != str(out_f):
            ent["last_output_path"] = str(out_f)
            ent["last_export_dir"] = str(custom_exp)
            pp.json_path.write_text(json.dumps(raw_j, ensure_ascii=False), encoding="utf-8")
        nt = time.time() + f50_calls["n"] * 2.0
        os.utime(out_f, (nt, nt))
        return {"status": "ok", "output_path": str(out_f)}

    rep_f50 = run_closed_loop_bubble_centering(first_dir, passes=2, render_page_fn=_first_export_render)
    assert rep_f50["001.png"][0]["render_status"] == "ok"
    _, d_f50 = load_page_json(resolve_page_paths(first_img))
    assert abs(d_f50["regions"][0]["center"][0] - 200.0) <= 5.0

    # Concurrent region edit during post-write render with equal mtime must NOT treat stale ink as fresh on Pass 1
    taint_dir = tmp_path / "r3_f50_taint"
    taint_dir.mkdir(parents=True)
    taint_img = taint_dir / "001.png"
    Image.fromarray(bc_canvas).save(taint_img)
    tp = resolve_page_paths(taint_img)
    save_rgb_image_atomic(bc_canvas, tp.inpainted_path)
    taint_out = taint_dir / "manga_translator_work" / "result" / "001.png"
    save_page_json_atomic(
        tp,
        str(taint_img.resolve()),
        {
            "last_output_path": str(taint_out),
            "regions": [
                {
                    "lines": [[[215.0, 160.0], [275.0, 160.0], [275.0, 280.0], [215.0, 280.0]]],
                    "center": [245.0, 220.0],
                    "translation": "初版",
                    "font_size": 26,
                    "direction": "v",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    taint_calls = {"n": 0}

    def _tainted_post_render(pp):
        taint_calls["n"] += 1
        ren = bc_canvas.copy()
        if taint_calls["n"] == 1:
            ren[170:270, 230:260] = 10
            save_rgb_image_atomic(ren, taint_out)
            os.utime(taint_out, (time.time() + 1.0, time.time() + 1.0))
        else:
            # Write deceptive far-left ink (x=135..165) BEFORE concurrent edit sets center=200 & equal mtime!
            ren[170:270, 135:165] = 10
            save_rgb_image_atomic(ren, taint_out)
            apply_region_updates_to_page(taint_img, [{"region_index": 0, "translation": "并发改稿", "center": [200.0, 220.0]}])
            same_ts = resolve_page_paths(taint_img).json_path.stat().st_mtime + 1.0
            os.utime(taint_out, (same_ts, same_ts))
        return {"status": "ok", "output_path": str(taint_out)}

    run_closed_loop_bubble_centering(taint_dir, passes=2, render_page_fn=_tainted_post_render)
    _, d_taint = load_page_json(resolve_page_paths(taint_img))
    # Stale far-left ink (135..165) was tainted by concurrent edit, so center=200 was NOT pushed right to ~245!
    assert abs(d_taint["regions"][0]["center"][0] - 200.0) <= 5.0

    # R3-F26: Record page in `committed_pages` immediately after `save_page_json_atomic` before post-commit reload
    ch_f26 = tmp_path / "r3_f26_ch"
    _create_sample_manga_page(ch_f26, "001.png")
    p2_f26 = _create_sample_manga_page(ch_f26, "002.png")
    real_load_json = glossary_manager.load_page_json
    p2_saved_seen = {"flag": False}
    real_save_f26 = glossary_manager.save_page_json_atomic

    def _track_save(cur_p, *a, **kw):
        res = real_save_f26(cur_p, *a, **kw)
        if cur_p.source_path.name == "002.png":
            p2_saved_seen["flag"] = True
        return res

    def _fail_post_commit_reload(pp):
        if p2_saved_seen["flag"] and pp.source_path.name == "002.png":
            raise OSError("post-commit summary read boom")
        return real_load_json(pp)

    monkeypatch.setattr(glossary_manager, "save_page_json_atomic", _track_save)
    monkeypatch.setattr(glossary_manager, "load_page_json", _fail_post_commit_reload)
    with pytest.raises(RuntimeError, match="post-commit summary reload failed") as f26_err:
        batch_edit_chapter_pages(ch_f26, set_fields={"font_size_delta": 4})
    monkeypatch.setattr(glossary_manager, "save_page_json_atomic", real_save_f26)
    monkeypatch.setattr(glossary_manager, "load_page_json", real_load_json)

    assert getattr(f26_err.value, "committed_pages", None) == ["001.png", "002.png"]
    assert getattr(f26_err.value, "failed_page", None) == "002.png"
    assert getattr(f26_err.value, "page_committed", None) is True
    assert getattr(f26_err.value, "failure_stage", None) == "post_commit_summary"
    _, d2_f26 = load_page_json(resolve_page_paths(p2_f26))
    assert d2_f26["regions"][0]["font_size"] == 28

    # R3-F45: Updating double-space TXT entry with tab-containing translation upgrades separator so list/delete round-trip `orig`
    txt_f45 = tmp_path / "r3_f45_places.txt"
    txt_f45.write_text("New York  纽约\n", encoding="utf-8")
    manage_glossary_entries(
        "upsert",
        glossary_file=str(txt_f45),
        entries=[{"original": "New York", "translation": "纽约\t大苹果"}],
    )
    listed_f45 = manage_glossary_entries("list", glossary_file=str(txt_f45))
    assert listed_f45["entries"] == [{"original": "New York", "translation": "纽约\t大苹果"}]
    deleted_f45 = manage_glossary_entries(
        "delete",
        glossary_file=str(txt_f45),
        entries=[{"original": "New York"}],
    )
    assert deleted_f45 == {"file": str(txt_f45), "format": "txt", "count": 0, "deleted": 1}
