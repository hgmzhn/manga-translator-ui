"""Regression tests for the post-Opus audit fixes:
1. Chapter-wide & label-based `mtui_snapshots` (`create` on directory, `restore` without `snapshot_id`);
2. Warm worker `release_vram` (`mtui_status(release_vram=True)`);
3. Single-worker in-process `mtui_center_bubbles` & `mtui_render_pages(center_bubbles=True)`;
4. `audit_and_compare.py` ancestor/env repo root discovery (zero hardcoded `parents[4]`);
5. Skill & batch manual invariants (mandatory reference pre-read for shards, `<yaml|txt>` glossary, valid snapshot args).
"""

import _bootstrap  # noqa: F401, I001

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import mtui_mcp.server as server
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, load_page_json, save_page_json_atomic


def _make_two_page_chapter(root: Path) -> tuple[Path, Path, Path]:
    chapter = root / "ch01"
    (chapter / "manga_translator_work" / "json").mkdir(parents=True)
    (chapter / "manga_translator_work" / "inpainted").mkdir(parents=True)
    (chapter / "manga_translator_work" / "result").mkdir(parents=True)

    pages: list[Path] = []
    for idx, stem in enumerate(("001", "002")):
        src = chapter / f"{stem}.png"
        canvas = np.full((800, 640, 3), 200, dtype=np.uint8)
        # Draw a sealed black-bordered white oval speech bubble centered at (160, 180)
        cv2.ellipse(canvas, (160, 180), (90, 120), 0, 0, 360, (255, 255, 255), thickness=-1)
        cv2.ellipse(canvas, (160, 180), (90, 120), 0, 0, 360, (0, 0, 0), thickness=4)
        Image.fromarray(canvas).save(src)

        inp = chapter / "manga_translator_work" / "inpainted" / f"{stem}_inpainted.png"
        Image.fromarray(canvas).save(inp)

        # Simulated rendered output with off-center ink shifted right (`x=175..215, y=130..230`)
        ren = canvas.copy()
        ren[130:230, 175:215] = 10
        res_p = chapter / "manga_translator_work" / "result" / f"{stem}.png"
        Image.fromarray(ren).save(res_p)

        mask = np.zeros((800, 640), dtype=np.uint8)
        paths = resolve_page_paths(src)
        save_page_json_atomic(
            paths,
            str(src.resolve()),
            {
                "last_export_dir": str((chapter / "manga_translator_work" / "result").resolve()),
                "mask_raw": encode_mask_base64(mask),
                "regions": [
                    {
                        "lines": [[[130.0, 120.0], [215.0, 120.0], [215.0, 240.0], [130.0, 240.0]]],
                        "center": [195.0, 180.0],
                        "text": f"テスト{idx}",
                        "translation": f"初版台词{idx}",
                        "font_family": "FZHei-B01",
                        "font_size": 28,
                        "direction": "v",
                        "alignment": "left",
                    }
                ],
            },
            auto_initial_snapshot=True,
        )
        pages.append(src)
    return chapter, pages[0], pages[1]


def test_chapter_snapshots_and_label_or_latest_restore(tmp_path: Path) -> None:
    chapter, p1, p2 = _make_two_page_chapter(tmp_path)

    # 1. On-demand single-page snapshot creation on p1 (`SKILL.md` Stage 6 on-demand rule) + batch support on p2
    created_p1 = json.loads(server.mtui_snapshots(target_path=str(p1), action="create", label="pre_gate_b"))
    assert "pre_gate_b" in created_p1["snapshot_id"]
    created_p2 = json.loads(server.mtui_snapshots(target_path=str(p2), action="create", label="pre_gate_b"))
    assert "pre_gate_b" in created_p2["snapshot_id"]

    # Mutate both pages during Gate B
    for p in (p1, p2):
        pp = resolve_page_paths(p)
        k, d = load_page_json(pp)
        d["regions"][0]["translation"] = "改坏的台词"
        save_page_json_atomic(pp, k, d, auto_initial_snapshot=False)

    # 2. Restore page 1 by `label="pre_gate_b"` WITHOUT `snapshot_id`
    r1 = json.loads(server.mtui_snapshots(target_path=str(p1), action="restore", label="pre_gate_b"))
    assert r1["label"] == "pre_gate_b"
    _, d1 = load_page_json(resolve_page_paths(p1))
    assert d1["regions"][0]["translation"] == "初版台词0"

    # Page 2 is still mutated; restore page 2 omitting BOTH `snapshot_id` and `label` (picks newest checkpoint != initial)
    r2 = json.loads(server.mtui_snapshots(target_path=str(p2), action="restore"))
    assert r2["label"] == "pre_gate_b"
    _, d2 = load_page_json(resolve_page_paths(p2))
    assert d2["regions"][0]["translation"] == "初版台词1"

    # 3. Selective single-page cleanup (e.g. clean normal page p1 while keeping unresolved p2)
    c_p1 = json.loads(server.mtui_snapshots(target_path=str(p1), action="cleanup"))
    assert c_p1["deleted_count"] >= 1
    assert c_p1["freed_bytes"] > 0
    assert json.loads(server.mtui_snapshots(target_path=str(p1), action="list"))["count"] == 0
    assert json.loads(server.mtui_snapshots(target_path=str(p2), action="list"))["count"] >= 1

    # 4. Full chapter post-Stage-7 cleanup (`STATUS: DONE`) removes `.mtui_mcp_snapshots` completely
    c_all = json.loads(server.mtui_snapshots(target_path=str(chapter), action="cleanup"))
    assert c_all["deleted_count"] >= 1
    snap_root = chapter / "manga_translator_work" / ".mtui_mcp_snapshots"
    assert not snap_root.exists()


def test_in_process_center_bubbles_and_vram_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chapter, p1, _ = _make_two_page_chapter(tmp_path)
    render_calls: list[str] = []

    def fake_render_single_page(paths, **kwargs):
        render_calls.append(paths.source_path.name)
        _, page_data = load_page_json(paths)
        inp_rgb = np.asarray(Image.open(paths.inpainted_path).convert("RGB"), dtype=np.uint8).copy()
        for reg in page_data.get("regions") or []:
            rcx, rcy = reg.get("center") or [160.0, 180.0]
            x0 = max(0, int(round(float(rcx) - 20.0)))
            x1 = min(inp_rgb.shape[1], int(round(float(rcx) + 20.0)))
            y0 = max(0, int(round(float(rcy) - 50.0)))
            y1 = min(inp_rgb.shape[0], int(round(float(rcy) + 50.0)))
            inp_rgb[y0:y1, x0:x1] = 10
        prev_mtime = paths.rendered_output_path.stat().st_mtime if paths.rendered_output_path.is_file() else 0.0
        paths.rendered_output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(inp_rgb).save(paths.rendered_output_path)
        new_mtime = max(paths.rendered_output_path.stat().st_mtime, prev_mtime + 0.05)
        os.utime(paths.rendered_output_path, (new_mtime, new_mtime))
        return {"status": "ok", "output_path": str(paths.rendered_output_path)}

    monkeypatch.setattr(server, "_render_single_page_via_worker", fake_render_single_page)

    centers_json = tmp_path / "centers.json"
    out = json.loads(server.mtui_center_bubbles(target_path=str(chapter), passes=2, save_json_path=str(centers_json)))
    assert out["centered_page_count"] == 2
    assert centers_json.is_file()
    assert len(render_calls) >= 2

    # Off-center x=195.0 inside bubble centered at x=160.0 must converge within two-sided tolerance of 160.0 (never double-shifting to ~125)!
    _, d1 = load_page_json(resolve_page_paths(p1))
    new_cx = float(d1["regions"][0]["center"][0])
    assert abs(new_cx - 160.0) <= 8.0, f"Expected optical center near 160.0, got {new_cx}"

    # Verify `mtui_status(release_vram=True)` works cleanly when worker is idle
    st = json.loads(server.mtui_status(release_vram=True))
    assert "vram_release" in st
    assert st["vram_release"]["status"] in ("idle", "ok")


def test_script_root_discovery_and_skill_doc_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    repo_root = _bootstrap.ROOT
    skill_dir = repo_root / "mtui_mcp" / "skills" / "mtui-manga-proofreading"
    script_path = skill_dir / "cli" / "audit_and_compare.py"
    script_src = script_path.read_text(encoding="utf-8")

    # Zero brittle `parents[4]` left anywhere in `audit_and_compare.py`
    assert "parents[4]" not in script_src

    # Copy script to an arbitrary shallow temp path and verify `_ensure_repo_src_on_path()` still locates `mtui_mcp` via env
    relocated = tmp_path / "moved_script.py"
    shutil.copy2(script_path, relocated)
    monkeypatch.setenv("MTUI_MCP_SRC", str(repo_root.resolve()))
    spec = importlib.util.spec_from_file_location("relocated_aac", relocated)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    found_src = mod._ensure_repo_src_on_path()
    assert found_src is not None and (found_src / "mtui_mcp" / "__init__.py").is_file()

    # Check SKILL.md and unattended_batch_ops.md contracts
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    batch_md = (skill_dir / "references" / "unattended_batch_ops.md").read_text(encoding="utf-8")

    assert "--glossary <json>" not in skill_md
    assert "<yaml|txt>" in skill_md
    assert 'mtui_snapshots(target_path="<PAGE>", action="create", label="pre_gate_b")' in skill_md
    assert 'mtui_snapshots(target_path="<PAGE>", action="restore", label="pre_gate_b")' in skill_md
    assert 'mtui_snapshots(target_path="<CHAPTER>", action="cleanup", snapshot_id="<RECORDED_ID>")' in skill_md
    assert 'mtui_snapshots(target_path="<CHAPTER>", action="cleanup", snapshot_id="<RECORDED_ID>")' in batch_md
    assert 'action="cleanup", label=' not in skill_md
    assert 'action="cleanup", label=' not in batch_md
    assert 'mtui_snapshots(target_path="<CHAPTER>", action="cleanup")' not in skill_md
    assert 'mtui_snapshots(target_path="<CHAPTER>", action="create", label="pre_gate_b")' not in skill_md
    assert "style_and_pitfalls.md" in batch_md
    assert "mistranslation_checklist.md" in batch_md
    assert "stage7_compare: ok" in batch_md
    assert "前一话进入 Stage 7 时，可以先开始下一话的 Stage 0~1" not in batch_md
