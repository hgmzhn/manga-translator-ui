"""MTUI-MCP Server: Full MCP toolset & prompts for manga-translator-ui post-editing."""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Optional

try:
    from mcp.server.mcpserver import Image, MCPServer
except ImportError:  # pragma: no cover - fallback for mcp < 2.0
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[assignment]
    from mcp.server.fastmcp import Image  # type: ignore[assignment]

from . import project_store as _project_store
from .bubble_centering import run_closed_loop_bubble_centering
from .config import _is_valid_mtui_root, get_runtime_settings
from .font_catalog import check_missing_glyphs, list_available_fonts
from .glossary_manager import batch_edit_chapter_pages, manage_glossary_entries
from .gui_bridge import (
    call_live_gui,
    is_live_gui_session_available,
    wait_for_live_gui_translation,
)
from .local_inpaint import (
    add_region_to_page,
    apply_incremental_local_repair,
    apply_mask_operations,
    delete_regions_from_page,
    manage_page_paste_overlays,
    merge_regions_on_page,
)
from .path_resolver import PagePaths, list_manga_pages_in_path, resolve_page_paths
from .project_store import (
    _atomic_replace_with_retry,
    align_regions_on_page,
    apply_region_updates_to_page,
    cleanup_snapshots_for_target,
    compute_page_revision,
    create_snapshots_for_target,
    decode_mask_base64,
    encode_mask_base64,
    ensure_initial_snapshot,
    extract_chapter_script_data,
    format_region_summary,
    get_page_details_dict,
    list_snapshots,
    load_page_json,
    open_rgb_image,
    resolve_region_target_index,
    restore_snapshot,
    save_page_json_atomic,
    save_rgb_image_atomic,
    verify_expected_revision,
)
from .qa_auditor import run_qa_audit
from .richtext_builder import (
    apply_project_richtext_rules,
    apply_span_styles_to_region,
    visible_text_of_region,
)
from .visual_inspector import (
    generate_page_inspection_image,
    generate_region_loupe_image,
)
from .worker.client import get_worker_client

logger = logging.getLogger("mtui_mcp.server")

_PYTHON_BINARY_NAME_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?w?(?:\.exe)?$", re.IGNORECASE)
_ALLOWED_MTUI_PYTHON_SUBDIRS: tuple[str, ...] = (
    ".venv",
    "packaging/python",
    "env",
    "python",
    "runtime",
)


@contextlib.contextmanager
def _acquire_page_edit_lock(
    target: PagePaths | str | Path,
    timeout_sec: float = 30.0,
) -> Iterator[Any]:
    lock_fn = getattr(_project_store, "page_edit_lock", None)
    if callable(lock_fn):
        with lock_fn(target, timeout_sec=timeout_sec) as locked:
            yield locked
    else:
        yield target


def _mask_sha1_hex(mask: Any, shape_hw: tuple[int, int]) -> str:
    import numpy as np

    if mask is None:
        arr = np.zeros(shape_hw, dtype=np.uint8)
    else:
        arr = np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8)
    return hashlib.sha1(arr.tobytes()).hexdigest()


def _file_stat_sig(path: Path) -> Optional[list[int]]:
    try:
        st = path.stat()
        return [int(st.st_size), int(st.st_mtime_ns)]
    except OSError:
        return None


def _is_prev_mask_synced(
    image_data: dict[str, Any],
    prev_mask_sha1: str,
    inpainted_path: Path,
) -> bool:
    """Return True iff `inpainted_path` exists and matches both saved mask SHA1 and file stat (R2-F47, R3-F12)."""
    cur_stat = _file_stat_sig(inpainted_path)
    if cur_stat is None:
        return False
    saved_sha1 = image_data.get("mask_inpainted_sha1")
    saved_stat = image_data.get("mask_inpainted_stat")
    return bool(
        saved_sha1
        and saved_sha1 == prev_mask_sha1
        and saved_stat is not None
        and cur_stat == saved_stat
    )


def _is_trusted_external_python_target(target_resolved: Path) -> bool:
    """Return True iff a venv interpreter symlink resolves to an explicitly trusted host/system Python binary."""
    if not target_resolved.is_file() or _PYTHON_BINARY_NAME_RE.match(target_resolved.name) is None:
        return False

    target_norm = os.path.normcase(str(target_resolved))
    trusted_exact: list[str] = []
    env_py = os.environ.get("MTUI_PYTHON", "").strip()
    if env_py:
        env_path = Path(env_py).expanduser()
        with contextlib.suppress(OSError):
            trusted_exact.append(os.path.normcase(str(env_path.resolve())))
        trusted_exact.append(os.path.normcase(os.path.abspath(str(env_path))))
    for sys_py in (sys.executable, getattr(sys, "_base_executable", "")):
        if sys_py:
            with contextlib.suppress(OSError):
                trusted_exact.append(os.path.normcase(str(Path(sys_py).resolve())))
            trusted_exact.append(os.path.normcase(os.path.abspath(sys_py)))
    if target_norm in trusted_exact:
        return True

    trusted_prefixes: list[Path] = []
    if sys.base_prefix:
        with contextlib.suppress(OSError):
            trusted_prefixes.append(Path(sys.base_prefix).resolve())
    if os.name == "nt":
        for env_dir_key, rel_parts in (
            ("ProgramFiles", ()),
            ("ProgramFiles(x86)", ()),
            ("LOCALAPPDATA", ("Programs", "Python")),
            ("APPDATA", ("uv", "python")),
            ("LOCALAPPDATA", ("uv", "python")),
        ):
            base_val = os.environ.get(env_dir_key, "").strip()
            if base_val:
                cand_dir = Path(base_val).joinpath(*rel_parts)
                with contextlib.suppress(OSError):
                    if cand_dir.is_dir():
                        trusted_prefixes.append(cand_dir.resolve())
    else:
        for posix_dir in (
            "/usr/bin",
            "/usr/local/bin",
            "/usr/libexec",
            "/opt/homebrew",
            "/opt/python",
            "/Library/Frameworks/Python.framework",
        ):
            cand_dir = Path(posix_dir)
            with contextlib.suppress(OSError):
                if cand_dir.exists():
                    trusted_prefixes.append(cand_dir.resolve())

    for prefix in trusted_prefixes:
        with contextlib.suppress(ValueError):
            if target_resolved.is_relative_to(prefix):
                return True
    return False


def _validate_mtui_python_override(
    mtui_python_raw: str,
    effective_root: Optional[Path],
) -> str:
    raw_str = str(mtui_python_raw).strip()
    if not raw_str:
        raise ValueError("mtui_python must not be empty.")

    cand_abs = Path(os.path.abspath(str(Path(raw_str).expanduser())))
    if not cand_abs.is_file():
        raise ValueError(f"mtui_python executable does not exist: {raw_str!r}")

    try:
        cand_target = cand_abs.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Failed to resolve mtui_python executable {raw_str!r}: {exc}") from exc
    if not cand_target.is_file():
        raise ValueError(f"Resolved mtui_python target is not a regular file: {raw_str!r}")

    cand_norm = os.path.normcase(str(cand_abs))
    env_py = os.environ.get("MTUI_PYTHON", "").strip()
    if env_py:
        env_norm = os.path.normcase(os.path.abspath(str(Path(env_py).expanduser())))
        if cand_norm == env_norm:
            return str(cand_abs)

    if sys.executable:
        sys_norm = os.path.normcase(os.path.abspath(sys.executable))
        if cand_norm == sys_norm:
            return str(cand_abs)

    if _PYTHON_BINARY_NAME_RE.match(cand_abs.name) is None or _PYTHON_BINARY_NAME_RE.match(cand_target.name) is None:
        raise ValueError(
            f"Rejected mtui_python {raw_str!r}: executable and resolved target must be Python interpreter binaries."
        )

    if effective_root is not None:
        try:
            root_resolved = effective_root.resolve(strict=True)
            cand_parent_resolved = cand_abs.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            root_resolved = None
            cand_parent_resolved = None
        if root_resolved is not None and cand_parent_resolved is not None:
            for rel_sub in _ALLOWED_MTUI_PYTHON_SUBDIRS:
                sub_dir = effective_root / rel_sub
                try:
                    if not sub_dir.is_dir():
                        continue
                    sub_resolved = sub_dir.resolve(strict=True)
                    # R2-F44: Verify allowed subdirectory itself resolves inside effective_root (no symlink escape)
                    if not sub_resolved.is_relative_to(root_resolved):
                        continue
                    if not cand_parent_resolved.is_relative_to(sub_resolved):
                        continue
                    # R2-F44: Verify resolved interpreter binary target is either inside sub_resolved or a trusted base Python
                    if cand_target.is_relative_to(sub_resolved) or _is_trusted_external_python_target(cand_target):
                        return str(cand_abs)
                except (OSError, RuntimeError, ValueError):
                    continue

    raise ValueError(
        "Rejected mtui_python override: interpreter must match MTUI_PYTHON / sys.executable "
        "or reside inside the validated mtui_root (.venv, packaging/python, env, python, runtime) "
        "with a verified Python binary target."
    )


SERVER_INSTRUCTIONS = """You are connected to MTUI-MCP, the post-translation QA, image-repair, and typesetting MCP server for `manga-translator-ui`.

Recommended Agent Workflow after `manga-translator-ui` finishes translating a manga folder/page:
1. Discovery & Audit:
   - Optional Page Discovery: Call `mtui_list_pages` on the manga folder to enumerate pages in reading order and inspect artifact / untranslated-region status.
   - Automated QA Diagnostics: Call `mtui_qa_audit` on the manga folder (or page) to detect untranslated kana/hangul, low-OCR-confidence bubbles, missing font glyphs, line-break punctuation/orphan issues, direction mismatches, and un-erased residual ink specks!
   - Call `mtui_chapter_script` to read the whole chapter's dialogue in reading order (`rtl` for Japanese manga, `ltr` for Korean/Western) and check terminology, character names, pronouns, and tone consistency alongside `mtui_manage_glossary`.
2. Visual Inspection:
   - Call `mtui_inspect_page_visual` (`mode="side_by_side"` or `"inpaint_check"`) to visually compare Original vs Inpainted+Mask vs Rendered.
   - For bubbles needing closer repair or layout tuning, call `mtui_inspect_region_visual` to view zoomed 3-panel strips with exact world `(X, Y)` pixel coordinate rulers!
3. Proofreading, Image Repair & Typesetting:
   - Text, Line-Breaking & Typography: Use `mtui_update_regions` (or `mtui_batch_edit_chapter` across all pages) to fix translations, `\\n` line breaks, `font_size`, `font_family`, `font_color`, `stroke_color`, `stroke_width`, `direction` (`h`/`v`), `alignment` (keep `"left"` for vertical manga bubbles so columns stay top-aligned / 齐顶; NEVER set `"center"` just to center a bubble — use `center-bubbles` / `center` coordinates instead!), `line_spacing`, `letter_spacing`, `center`/`move_dx`/`move_dy`, `angle`, and 4-point perspective quads (`white_frame_quad_local`; do not blindly overwrite `center`/`angle` when `has_perspective_quad=True` unless clearing perspective).
   - Line-Breaking Policy (Agent-Owned): ALL line breaks (`\\n`) MUST be judged and inserted directly by YOU (the Agent) based on semantic phrasing, emotional cadence, punctuation rules (never start a line with closing punctuation; avoid 1-char orphan tail lines), and visual speech-bubble curvature/aspect ratio. Native auto-wrap and HanLP line-breakers are strictly disabled during rendering so your exact `\\n` breaks are rendered 100% verbatim!
   - Font Styles & Rich Text: Bold, italic, underline, strikethrough, emphasis dots (着重号), Ruby furigana (注音 + `rubyInherit`), TCY horizontal-in-vertical (`!!`/`2026`), outer stroke, glow, and glyph blur/motion-blur (`blur`/`blurAngle`/`blurRadial`/`blurMotion`/`blurMotionDistance`) MUST be set via `mtui_style_region_richtext` (or `rich_style`/`rich_spans` in `mtui_update_regions`), which builds strict `richtext.v1` documents.
   - Inpainting & Artwork Restoration: Use `mtui_edit_mask_and_inpaint` with world `(X, Y)` coordinates read from the loupe ruler:
     * `add_rects` / `add_polygons` / `dilate_regions` / `auto_detect_ink_rects` to erase leftover furigana/SFX (`inpaint_method="neural"` uses `lama_large`/`lama_mpe` on GPU; `"solid_fill"` instantly fills clean speech bubbles).
     * `erase_rects` / `erase_polygons` to un-mask accidentally wiped panel lines or artwork, restoring crisp original pixels immediately!
     * `mtui_add_region` / `mtui_delete_regions` / `mtui_merge_regions` / `mtui_manage_paste_overlays` for missed bubbles, false positives, split bubbles, or clone-stamp patches.
4. Re-Render & Verify:
   - Set `re_render=True` on edit calls (or batch edits with `re_render=False` and call `mtui_render_pages` once at the end), then verify with `mtui_inspect_page_visual` or `mtui_inspect_region_visual`!
"""

def _create_mcp_server() -> MCPServer:
    init_kwargs: dict[str, Any] = {
        "name": "mtui-mcp",
        "instructions": SERVER_INSTRUCTIONS,
    }
    if "version" in inspect.signature(MCPServer.__init__).parameters:
        init_kwargs["version"] = "0.1.0"
    return MCPServer(**init_kwargs)


def _multimodal_tool() -> Any:
    if "structured_output" in inspect.signature(mcp.tool).parameters:
        return mcp.tool(structured_output=False)
    return mcp.tool()


mcp = _create_mcp_server()


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _render_single_page_via_worker(
    paths: PagePaths,
    *,
    output_dir: Optional[str] = None,
    proof_only: bool = False,
    force_backend_inpaint: bool = False,
    export_psd: bool = False,
    psd_script_only: bool = False,
    inpainter: Optional[str] = None,
) -> dict[str, Any]:
    resolved = resolve_page_paths(paths.source_path, explicit_output_dir=output_dir)
    if proof_only:
        import uuid

        proof_dir = resolved.work_dir / "proofread"
        proof_dir.mkdir(parents=True, exist_ok=True)
        out_path = proof_dir / f"{resolved.source_path.stem}-{uuid.uuid4().hex[:8]}.png"
    else:
        out_path = resolved.rendered_output_path
    worker = get_worker_client()
    return worker.call(
        "render_page",
        {
            "source_path": str(resolved.source_path),
            "display_base_path": str(resolved.display_base_path),
            "json_path": str(resolved.json_path),
            "inpainted_path": str(resolved.inpainted_path),
            "output_path": str(out_path),
            "force_backend_inpaint": force_backend_inpaint,
            "export_psd": export_psd,
            "psd_script_only": psd_script_only,
            "inpainter": inpainter,
            "proof_only": proof_only,
        },
    )


# ===========================================================================
# Group 1: Runtime, Workspace & Font Discovery
# ===========================================================================


@mcp.tool()
def mtui_status(
    mtui_root: Optional[str] = None,
    mtui_python: Optional[str] = None,
    warm_up_worker: bool = False,
    release_vram: bool = False,
) -> str:
    """Check or configure `manga-translator-ui` installation path, Python venv, GPU status, and active config defaults.

    Args:
        mtui_root: Optional path to `manga-translator-ui` root folder if not auto-detected (STDIO/local mode only).
        mtui_python: Optional path to `manga-translator-ui`'s `.venv` Python executable (STDIO/local mode only; restricted to verified venv/runtime paths).
        warm_up_worker: If True, immediately starts and pings the warm Qt6/PyTorch engine worker.
        release_vram: If True, unloads cached LaMa/OCR GPU models inside the warm worker and empties PyTorch CUDA cache.
    """
    settings = get_runtime_settings()
    if mtui_root is not None or mtui_python is not None:
        if not getattr(settings, "allow_runtime_python_override", True):
            raise ValueError(
                "Overriding mtui_root or mtui_python at runtime is disabled over HTTP/SSE transport; "
                "configure MTUI_ROOT / MTUI_PYTHON via environment variables or CLI startup flags."
            )

        effective_root = settings.mtui_root
        validated_root: Optional[str] = None
        if mtui_root is not None:
            raw_root = str(mtui_root).strip()
            if not raw_root:
                raise ValueError("mtui_root must not be empty.")
            cand_root = Path(raw_root).expanduser().resolve()
            if not _is_valid_mtui_root(cand_root):
                raise ValueError(f"Invalid manga-translator-ui root directory: {mtui_root!r}")
            effective_root = cand_root
            validated_root = str(cand_root)

        validated_python: Optional[str] = None
        if mtui_python is not None:
            validated_python = _validate_mtui_python_override(mtui_python, effective_root)

        get_worker_client().shutdown()
        settings.refresh(mtui_root=validated_root, mtui_python=validated_python)

    vram_release_info: Optional[dict[str, Any]] = None
    if release_vram:
        try:
            vram_release_info = get_worker_client().release_vram()
        except Exception as exc:
            vram_release_info = {"status": "error", "error": str(exc)}

    cfg = settings.load_mtui_config_dict()
    worker_info: Optional[dict[str, Any]] = None
    worker_error: Optional[str] = None
    if warm_up_worker or get_worker_client().is_running:
        try:
            worker_info = get_worker_client().call("ping", timeout_sec=60.0)
        except Exception as exc:
            worker_error = str(exc)

    payload: dict[str, Any] = {
        "mtui_root": str(settings.mtui_root) if settings.mtui_root else None,
        "mtui_python": str(settings.mtui_python) if settings.mtui_python else None,
        "engine_available": settings.is_engine_available,
        "config_file": str(settings.get_user_config_path() or ""),
        "fonts_dir": str(settings.fonts_dir or ""),
        "dict_dir": str(settings.dict_dir or ""),
        "worker_running": get_worker_client().is_running,
        "worker_info": worker_info,
        "worker_error": worker_error,
        "active_config_summary": {
            "translator": (cfg.get("translator") or {}).get("translator"),
            "target_lang": (cfg.get("translator") or {}).get("target_lang"),
            "high_quality_prompt_path": (cfg.get("translator") or {}).get("high_quality_prompt_path"),
            "inpainter": (cfg.get("inpainter") or {}).get("inpainter"),
            "inpainter_blur_radius": (cfg.get("inpainter") or {}).get("blur_radius"),
            "layout_mode": (cfg.get("render") or {}).get("layout_mode"),
            "default_font_family": (cfg.get("render") or {}).get("font_family"),
            "semantic_linebreak": (cfg.get("render") or {}).get("semantic_linebreak"),
            "stroke_width": (cfg.get("render") or {}).get("stroke_width"),
        },
    }
    if vram_release_info is not None:
        payload["vram_release"] = vram_release_info
    return _json_text(payload)


@mcp.tool()
def mtui_list_pages(
    target_path: str,
    recursive: bool = False,
) -> str:
    """List all manga pages in a folder (or single file) in natural reading order with artifact & translation status.

    Accepts a source manga directory, a `-translated` output directory, or a single image/JSON path.
    """
    pages = list_manga_pages_in_path(target_path, recursive=recursive)
    records: list[dict[str, Any]] = []
    for idx, p in enumerate(pages):
        reg_count = 0
        empty_trans_count = 0
        json_valid: Optional[bool] = True if p.json_exists else None
        json_error: Optional[str] = None
        if p.json_exists:
            try:
                _, img_data = load_page_json(p)
                regs = img_data.get("regions", [])
                reg_count = len(regs)
                empty_trans_count = sum(
                    1
                    for r in regs
                    if isinstance(r, dict)
                    and str(r.get("text") or "").strip()
                    and not visible_text_of_region(r).strip()
                )
            except Exception as exc:
                json_valid = False
                json_error = str(exc)
        rec: dict[str, Any] = {
            "page_index": idx,
            "page_name": p.source_path.name,
            "source_path": str(p.source_path),
            "json_exists": p.json_exists,
            "json_valid": json_valid,
            "inpainted_exists": p.inpainted_exists,
            "rendered_output_exists": p.rendered_output_exists,
            "rendered_output_path": str(p.rendered_output_path),
            "region_count": reg_count,
            "untranslated_regions": empty_trans_count,
        }
        if json_error is not None:
            rec["json_error"] = json_error
        records.append(rec)

    return _json_text(
        {
            "total_pages": len(records),
            "pages_with_json": sum(1 for r in records if r["json_exists"]),
            "pages_with_invalid_json": sum(1 for r in records if r["json_valid"] is False),
            "pages_rendered": sum(1 for r in records if r["rendered_output_exists"]),
            "pages": records,
        }
    )


@mcp.tool()
def mtui_list_fonts(
    include_system: bool = False,
    cjk_only: bool = False,
    query: str = "",
    check_text_glyphs: str = "",
    limit: int = 80,
) -> str:
    """List available fonts in `MTUI_ROOT/fonts/` and optionally system fonts, with optional missing-glyph validation.

    Args:
        include_system: Include OS-installed system fonts in addition to custom `fonts/` files.
        cjk_only: Only return fonts with CJK ideograph coverage.
        query: Case-insensitive substring filter on family or file name (e.g. "雅黑", "黑体", "anime", "圆").
        check_text_glyphs: Optional text sample — reports `missing_glyphs` per font so you never pick a font that renders `□` tofu boxes!
        limit: Max fonts to return.
    """
    fonts = list_available_fonts(
        include_system=include_system,
        cjk_only=cjk_only,
        query=query,
        limit=limit,
    )
    if check_text_glyphs:
        for f in fonts:
            missing = check_missing_glyphs(f["selector"], check_text_glyphs)
            f["missing_glyphs"] = "".join(missing)
            f["all_glyphs_present"] = len(missing) == 0
    return _json_text({"count": len(fonts), "fonts": fonts})


# ===========================================================================
# Group 2: Inspection, Chapter Script, QA Audit & Multimodal Visual Views
# ===========================================================================


@mcp.tool()
def mtui_get_page_details(
    page_path: str,
    include_polygons: bool = False,
) -> str:
    """Get complete structured data for all text regions, typography, colors, geometry, and masks on a manga page."""
    return _json_text(get_page_details_dict(page_path, include_polygons=include_polygons))


@mcp.tool()
def mtui_chapter_script(
    target_path: str,
    reading_order: Literal["rtl", "ltr", "index"] = "rtl",
    start_page: int = 0,
    max_pages: int = 150,
) -> str:
    """Extract a compact bilingual script (`source_text` + `translation`) across an entire chapter in reading order.

    Ideal for fast 1-shot translation proofreading, checking character name/pronoun consistency, tone, and missed lines.
    Use `reading_order="rtl"` for Japanese/Traditional manga and `"ltr"` for Korean Webtoons / Western comics.
    """
    return _json_text(
        extract_chapter_script_data(
            target_path,
            reading_order=reading_order,
            start_page=start_page,
            max_pages=max_pages,
        )
    )


@mcp.tool()
def mtui_qa_audit(
    target_path: str,
    check_inpaint_cv: bool = True,
    check_font_glyphs: bool = True,
    only_pages_with_issues: bool = False,
    force_yuzumarker: bool = False,
    glossary_file: Optional[str] = None,
    save_json_path: Optional[str] = None,
    diff_against_json: Optional[str] = None,
    summary_only: bool = False,
) -> str:
    """Run automated QA diagnostics across a manga page or entire chapter folder.

    Detection groups (see `issues_by_category` in the result):
    - Text/OCR: `empty_translation`, `untranslated_kana`, `low_ocr_confidence`, `suspect_ocr_corruption_mistranslation`,
      `suspect_ocr_glossary_typo`, `translation_number_mismatch`, `invented_ellipsis_truncated_translation`,
      `same_bubble_split_regions`, `negation_or_prohibition_drop_suspect`, `inverted_japanese_calque_syntax`, `glossary_term_mismatch`
    - Fonts (local YuzuMarker 4x cache): `yuzumarker_font_mismatch`, `yuzumarker_bold_mismatch`, `raw_bold_mismatch`,
      `raw_font_consistency_suspect`, `inherently_heavy_font_bolded`, `connected_bubble_font_mismatch`, `missing_font_glyphs`
    - Layout: `unbalanced_linebreak_columns`, `linebreak_broken_breath`, `orphan_single_char_line`, `cross_lobe_dangling_clause`,
      `vertical_alignment_mismatch`, `double_rotated_vertical_symbol`, `bubble_underfilled_font`, `raw_font_size_mismatch`,
      `text_overflows_bubble`, `rendered_regions_overlap`, `unfilled_connected_lobe`, `direction_aspect_mismatch`
    - Inpaint/artwork: `inpaint_residual_ink`, `inpaint_gray_smudge`, `bubble_border_eroded` (with `suggested_restore_rect_xyxy`),
      `frameless_overmasked_artwork`, `frameless_artwork_scar`, `unmasked_adjacent_raw_text`
    - Scope: `cover_credit_translation`, `running_header_book_title`, `toc_alignment_or_merge_error`, `toc_unmasked_raw_text`,
      `preview_ad_out_of_bubble_translation`, `untranslated_boxed_sfx`

    Args:
    - `force_yuzumarker`: force a full local YuzuMarker 4x re-scan (refreshes `.yuzumarker_font_cache.json`; GPU is released afterwards).
      Without it, the scan only runs for pages missing from the cache.
    - `glossary_file`: optional glossary YAML (`dict/*.yaml`) or TXT dictionary (`mit_glossary.txt`) for `glossary_term_mismatch`.
    - `save_json_path`: write the FULL report JSON to this file (use it as the Gate B baseline / after-snapshot).
    - `diff_against_json`: path to an earlier saved report; adds a `regression_diff` block
      (`newly_introduced_error_count`, `passed`, ...) — the zero-regression lock after Gate B edits.
    - `summary_only`: return only counts + saved path + diff (keeps large chapter reports out of the context window;
      combine with `save_json_path`).
    """
    save_p = Path(save_json_path).expanduser() if save_json_path else None
    diff_p = Path(diff_against_json).expanduser() if diff_against_json else None
    if save_p is not None and diff_p is not None:
        same_file = False
        try:
            if save_p.exists() and diff_p.exists():
                same_file = os.path.samefile(save_p, diff_p)
        except OSError:
            same_file = False
        if not same_file:
            try:
                same_file = os.path.normcase(str(save_p.resolve())) == os.path.normcase(str(diff_p.resolve()))
            except OSError:
                same_file = os.path.normcase(os.path.abspath(str(save_p))) == os.path.normcase(
                    os.path.abspath(str(diff_p))
                )
        if same_file:
            raise ValueError(
                "save_json_path and diff_against_json must not point to the same file; "
                "saving first would overwrite the baseline report before diffing."
            )

    report = run_qa_audit(
        target_path,
        check_inpaint_cv=check_inpaint_cv,
        check_font_glyphs=check_font_glyphs,
        only_pages_with_issues=only_pages_with_issues,
        force_yuzumarker=force_yuzumarker,
        glossary_file=glossary_file,
    )
    result: dict[str, Any] = dict(report)
    if diff_p is not None:
        from .qa_auditor import diff_qa_audits

        if diff_p.is_file():
            before_rep = json.loads(diff_p.read_text(encoding="utf-8"))
            result["regression_diff"] = diff_qa_audits(before_rep, report)
        else:
            result["regression_diff"] = {"error": f"baseline not found: {diff_p}"}
    if save_p is not None:
        save_p.parent.mkdir(parents=True, exist_ok=True)
        save_p.write_text(_json_text(report), encoding="utf-8")
        result["saved_json_path"] = str(save_p)
    if summary_only:
        result.pop("pages", None)
    return _json_text(result)


@_multimodal_tool()
def mtui_inspect_page_visual(
    page_path: str,
    mode: Literal["side_by_side", "inpaint_check", "annotated_rendered", "clean_rendered"] = "side_by_side",
    show_grid: bool = False,
    max_edge: int = 2000,
    return_inline_image: bool = True,
) -> list[Any]:
    """Generate a ruler-annotated full-page visual comparison PNG and return it inline for Multimodal Vision review.

    Modes:
    - `side_by_side`: `Original (with numbered region polygons) | Final Rendered (with #index badges)`
    - `inpaint_check`: 3-panel `Original + Boxes | Inpainted + Red Mask Overlay | Final Rendered` (best for checking missed strokes or over-erased art!)
    - `annotated_rendered`: Final rendered page with `#index` badges, source polygons, center crosshairs, and world pixel rulers
    - `clean_rendered`: Clean un-annotated rendered output image
    """
    out_file, meta = generate_page_inspection_image(
        page_path,
        mode=mode,
        show_grid=show_grid,
        max_edge=max_edge,
    )
    if return_inline_image:
        return [_json_text(meta), Image(path=str(out_file))]
    return [_json_text(meta)]


@_multimodal_tool()
def mtui_inspect_region_visual(
    page_path: str,
    region_indices: Optional[list[int]] = None,
    custom_crop_xyxy: Optional[list[float]] = None,
    padding_px: int = 55,
    show_grid: bool = True,
    return_inline_image: bool = True,
) -> list[Any]:
    """Generate zoomed 3-panel Region Loupe strip(s) (`Original | Inpainted+Mask | Rendered`) with exact world `(X, Y)` coordinate rulers!

    Use the world `(X, Y)` coordinates printed on the top/left axes directly in `mtui_edit_mask_and_inpaint`, `mtui_update_regions`, or `mtui_add_region`.
    """
    out_file, meta = generate_region_loupe_image(
        page_path,
        region_indices=region_indices,
        custom_crop_xyxy=custom_crop_xyxy,
        padding_px=padding_px,
        show_grid=show_grid,
    )
    if return_inline_image:
        return [_json_text(meta), Image(path=str(out_file))]
    return [_json_text(meta)]


# ===========================================================================
# Group 3: Proofreading, Typography, Rich-Text Styling & Smart Line-Breaking
# ===========================================================================


def _advance_revision_after_render(committed_revision: str, render_info: Optional[dict[str, Any]]) -> str:
    """Advance `committed_revision` across `render_page`'s metadata write iff no external edit intervened (R3-F01, R2-F46)."""
    if (
        isinstance(render_info, dict)
        and render_info.get("pre_persist_revision") == committed_revision
        and isinstance(render_info.get("post_persist_revision"), str)
        and render_info["post_persist_revision"]
    ):
        return render_info["post_persist_revision"]
    return committed_revision


@mcp.tool()
def mtui_update_regions(
    page_path: str,
    updates: list[dict[str, Any]],
    expected_revision: Optional[str] = None,
    re_render: bool = True,
    proof_only: bool = False,
    output_dir: Optional[str] = None,
) -> str:
    """Update translation text, line breaks, font size, font family, colors, layout, and geometry for one or more regions on a page.

    Each item in `updates` targets a region via `region_uid` (stable ID, immune to delete/merge shifts) or `region_index` (int), and supports:
    - `translation` (str): New translated text. Use `\\n` (or `[BR]`) for explicit line breaks; multi-span rich-text styles on unchanged characters are preserved automatically!
    - `clear_rich_text` (bool): Set True to strip existing `translation_rich` styling.
    - `source_text` (str): Fix OCR source text if needed.
    - `font_size` (int) or `font_size_delta` (int, e.g. `+3` or `-2`).
    - `font_family` (str): Exact Qt `font_family` or `Family::Style` from `mtui_list_fonts`.
    - `bold` (bool): First-class whole-region bold (`True`/`False`) — automatically writes/clears `richtext.v1` inline `"style": {"bold": true}` so Qt6 thickens single-weight `.ttf` fonts cleanly!
    - `font_color` (str `#RRGGBB` or `[R,G,B]`), `stroke_color` (`#RRGGBB` or `[R,G,B]`), `stroke_width` (float ratio of font_size, e.g. `0.07`..`0.20`), `disable_font_border` (bool).
    - `sample_color_from_original` (bool): Automatically sample foreground ink & background bubble colors from the original image!
    - `direction` (`"h"`, `"v"`, `"auto"`), `alignment` (`"left"`, `"center"`, `"right"`, `"auto"`).
    - `line_spacing` (float, default `1.0`), `letter_spacing` (float, default `1.0`), `opacity` (`0.0`..`1.0`).
    - `center` (`[cx, cy]`), `move_dx` (float), `move_dy` (float), `angle` (float degrees), `bbox_xyxy` (`[x1, y1, x2, y2]`), `white_frame_rect_local` (`[x1, y1, x2, y2]`), `white_frame_quad_local` (`[[x1, y1], [x2, y2], [x3, y3], [x4, y4]]` 4-point local perspective quad, or `None` to clear perspective warp).
    - `rich_style` (dict) / `rich_spans` (list[dict]): Optional `richtext.v1` styling (bold, italic, color, ruby, `rubyInherit`, tcy, stroke, glow, blur/motion-blur).
    Pass `expected_revision` (from `mtui_get_page_details`) to guard against concurrent edits, and `proof_only=True` to render a non-destructive proof in `manga_translator_work/proofread/`.
    """
    init_paths = resolve_page_paths(page_path)
    with _acquire_page_edit_lock(init_paths):
        refreshed_paths, summaries = apply_region_updates_to_page(
            page_path,
            updates,
            expected_revision=expected_revision,
        )
        committed_revision = compute_page_revision(refreshed_paths)
    render_info: Optional[dict[str, Any]] = None
    if re_render:
        render_info = _render_single_page_via_worker(
            refreshed_paths,
            output_dir=output_dir,
            proof_only=proof_only,
        )
        committed_revision = _advance_revision_after_render(committed_revision, render_info)
    return _json_text(
        {
            "page_name": refreshed_paths.source_path.name,
            "revision": committed_revision,
            "json_path": str(refreshed_paths.json_path),
            "updated_regions": summaries,
            "render_result": render_info,
        }
    )


@mcp.tool()
def mtui_style_region_richtext(
    page_path: str,
    region_index: Optional[int] = None,
    region_uid: Optional[str] = None,
    expected_revision: Optional[str] = None,
    whole_style: Optional[dict[str, Any]] = None,
    spans: Optional[list[dict[str, Any]]] = None,
    apply_project_rules: bool = False,
    reset_existing: bool = False,
    re_render: bool = True,
    proof_only: bool = False,
) -> str:
    """Apply rich-text font styles (`richtext.v1`) to a region: bold, italic, underline, strikethrough, emphasis, Ruby furigana, TCY, inline color/font/size, stroke, outerStroke, glow, blur/motion-blur, and glyph transforms.

    IMPORTANT: In `manga-translator-ui`, bold/italic/underline/ruby/TCY ONLY take effect via `translation_rich` (`richtext.v1`) — use this tool!

    Supported style fields (in `whole_style` or each item of `spans`; pass `None` or canonical defaults like `blur=0.0`, `blurAngle=None`, `rubyInherit=True`, `bold=False` to reset existing runs):
    - `bold`: bool
    - `italic`: bool (`True` = 15° shear) or float angle in degrees (e.g. `15.0`)
    - `underline`: bool, `strikethrough`: bool, `emphasis`: bool (着重号)
    - `color`: `#RRGGBB`, `fontFamily`: str, `fontSize`: float, `scale`: float (e.g. `1.3`)
    - `stroke`: `{"color": "#000000", "width": 0.12}`
    - `outerStroke`: `{"color": "#ffffff", "width": 0.10}`
    - `glow`: `{"color": "#ffee88", "blur": 0.25}`
    - `blur`: float (`0.0`..`1.0`), `blurAngle`: float | None (`0.0`..`360.0` directional gradient fade; `0.0` = L->R, `None` = uniform), `blurRadial`: bool, `blurMotion`: bool, `blurMotionDistance`: float (`0.0`..`500.0`)
    - `rubyInherit`: bool (default `True`; set `False` so Ruby furigana does not inherit base run styling)
    - `kerning`: float, `preKerning`: float, `lineKerning`: float, `nextKerning`: float, `verticalAdvance`: `"half"` | `"full"`
    - `transform`: `{"offsetX": float, "offsetY": float, "rotation": float, "mirrorX": bool, "mirrorY": bool, "scaleX": float, "scaleY": float}`

    Each item in `spans` targets text by ONE of:
    - `"match": "substring"` (exact substring match; optional `"occurrence": 0` for Nth match)
    - `"regex": "pattern"` (e.g. `"[!?！？]{2,3}"`)
    - `"start": int, "end": int` (0-based character slice on visible text)
    Plus optional `"ruby": "注音文本"`, `"ruby_style": {...}`, `"ruby_inherit": bool`, or `"tcy": True` (vertical horizontal-in-vertical 纵中横)!
    """
    paths = resolve_page_paths(page_path)
    with _acquire_page_edit_lock(paths):
        paths = resolve_page_paths(paths.source_path)
        verify_expected_revision(paths, expected_revision)
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])
        resolved_idx = resolve_region_target_index(regions, region_index=region_index, region_uid=region_uid)

        reg = regions[resolved_idx]
        target_uid = reg.get("region_uid")
        if whole_style or spans or reset_existing:
            apply_span_styles_to_region(
                reg,
                whole_style=whole_style,
                spans=spans,
                reset_existing=reset_existing,
            )

        if apply_project_rules:
            settings = get_runtime_settings()
            rules_file = (settings.config_dir / "rich_text_rules.yaml") if settings.config_dir else None
            apply_project_richtext_rules(reg, rules_yaml_path=rules_file)

        saved_json = save_page_json_atomic(paths, image_key, image_data)
        refreshed = resolve_page_paths(saved_json)
        _, saved_data = load_page_json(refreshed)
        saved_regions = saved_data.get("regions", [])
        saved_idx = (
            resolve_region_target_index(saved_regions, region_uid=target_uid)
            if target_uid
            else resolved_idx
        )
        saved_reg = saved_regions[saved_idx] if 0 <= saved_idx < len(saved_regions) else reg
        reg_summary = format_region_summary(saved_idx, saved_reg)
        committed_revision = compute_page_revision(refreshed)

    render_info = _render_single_page_via_worker(refreshed, proof_only=proof_only) if re_render else None
    committed_revision = _advance_revision_after_render(committed_revision, render_info)

    return _json_text(
        {
            "page_name": refreshed.source_path.name,
            "revision": committed_revision,
            "region": reg_summary,
            "render_result": render_info,
        }
    )


# ===========================================================================
# Group 4: Mask Repair, Incremental Inpainting, Region Add/Delete/Merge & Overlays
# ===========================================================================


@mcp.tool()
def mtui_edit_mask_and_inpaint(
    page_path: str,
    add_rects: Optional[list[list[float]]] = None,
    add_polygons: Optional[list[list[list[float]]]] = None,
    add_brush_strokes: Optional[list[dict[str, Any]]] = None,
    dilate_regions: Optional[list[dict[str, Any]]] = None,
    auto_detect_ink_rects: Optional[list[list[float]]] = None,
    tight_halo_rects: Optional[list[list[float]]] = None,
    erase_rects: Optional[list[list[float]]] = None,
    erase_polygons: Optional[list[list[list[float]]]] = None,
    erase_brush_strokes: Optional[list[dict[str, Any]]] = None,
    inpaint_method: Literal["neural", "solid_fill", "telea", "ns", "blur"] = "neural",
    neural_inpainter: Optional[str] = None,
    blur_radius: Optional[int] = None,
    full_reinpaint: bool = False,
    re_render: bool = True,
) -> str:
    """Refine the text-removal mask and repair the inpainted background image (erasing leftover strokes OR restoring over-erased artwork!).

    All coordinates are world pixel coordinates `(X, Y)` as shown on `mtui_inspect_region_visual` rulers:
    - Erase residual original text/SFX:
      * `add_rects`: `[[x1, y1, x2, y2], ...]`
      * `add_polygons`: `[[[x, y], ...], ...]`
      * `add_brush_strokes`: `[{"points": [[x, y], ...], "radius": 12}, ...]`
      * `dilate_regions`: `[{"region_index": 0, "pixels": 8}, ...]` (expand mask around a region)
      * `auto_detect_ink_rects`: `[[x1, y1, x2, y2], ...]` (auto-thresholds only dark ink inside a bubble!)
      * `tight_halo_rects`: `[[x1, y1, x2, y2], ...]` (**MANDATORY for frameless text over faces/eyes/hair/speedlines**: clears any bloated rectangular mask back to crisp `Raw` pixels and masks ONLY the tight `glyph + halo` contour!)
    - Restore accidentally erased artwork / panel borders / screentones:
      * `erase_rects` / `erase_polygons` / `erase_brush_strokes`: Clears mask in those areas and immediately restores crisp original image pixels!
    - `inpaint_method`:
      * `"neural"` (default): Uses `manga-translator-ui`'s `lama_large` / `lama_mpe` GPU inpainter on ONLY the `[x1-50..x2+50, y1-50..y2+50]` delta crop (<1s!), or full-page when `neural_inpainter="blur"`.
      * `"solid_fill"`: Instant clean fill using median surrounding bubble color (ideal for pure white speech bubbles!).
      * `"telea"` / `"ns"`: Fast local OpenCV inpainting without loading neural models.
      * `"blur"`: Full-page frosted-glass blur inpainting (`blur_radius` `0..200`, `0` = adaptive; for colored webtoon/CG translucent overlays).
    """
    paths = resolve_page_paths(page_path)
    if not paths.display_base_path.is_file():
        raise FileNotFoundError(f"Base image not found: {paths.display_base_path}")

    settings = get_runtime_settings()
    inpaint_info: dict[str, Any]
    with _acquire_page_edit_lock(paths):
        paths = resolve_page_paths(paths.source_path)
        if settings.auto_backup_on_first_edit:
            ensure_initial_snapshot(paths)

        base_rgb = open_rgb_image(paths.display_base_path)
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])
        prev_mask = decode_mask_base64(image_data.get("mask_raw"), base_rgb.shape[:2])
        prev_mask_sha1 = _mask_sha1_hex(prev_mask, base_rgb.shape[:2])
        prev_mask_synced = _is_prev_mask_synced(image_data, prev_mask_sha1, paths.inpainted_path)

        new_mask, added_delta, removed_delta = apply_mask_operations(
            prev_mask,
            base_rgb,
            regions,
            add_rects=add_rects,
            add_polygons=add_polygons,
            add_brush_strokes=add_brush_strokes,
            dilate_regions=dilate_regions,
            auto_detect_ink_rects=auto_detect_ink_rects,
            tight_halo_rects=tight_halo_rects,
            erase_rects=erase_rects,
            erase_polygons=erase_polygons,
            erase_brush_strokes=erase_brush_strokes,
        )

        new_mask_b64 = encode_mask_base64(new_mask)
        added_px = int((added_delta > 0).sum())
        removed_px = int((removed_delta > 0).sum())

        prev_inpaint_had_file = paths.inpainted_path.is_file()
        inpaint_backup_tmp = ""
        if prev_inpaint_had_file:
            paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
            fd_bak, inpaint_backup_tmp = tempfile.mkstemp(
                prefix=f".{paths.inpainted_path.name}.bak.",
                suffix=paths.inpainted_path.suffix or ".png",
                dir=str(paths.inpainted_path.parent),
            )
            os.close(fd_bak)
            shutil.copy2(paths.inpainted_path, inpaint_backup_tmp)

        actual_inp_path = paths.inpainted_path
        json_committed = False
        cleanup_backup = not bool(inpaint_backup_tmp)
        saved_json: Path
        try:
            if inpaint_method == "neural" and settings.is_engine_available:
                inpaint_info = get_worker_client().call(
                    "incremental_neural_inpaint",
                    {
                        "display_base_path": str(paths.display_base_path),
                        "inpainted_path": str(paths.inpainted_path),
                        "current_mask_b64": new_mask_b64,
                        "added_mask_b64": encode_mask_base64(added_delta) if added_px > 0 else "",
                        "removed_mask_b64": encode_mask_base64(removed_delta) if removed_px > 0 else "",
                        "inpainter": neural_inpainter,
                        "blur_radius": blur_radius,
                        "full_reinpaint": full_reinpaint,
                        "prev_mask_synced": prev_mask_synced,
                    },
                )
                actual_inp_path = Path(str(inpaint_info.get("inpainted_path") or paths.inpainted_path))
            else:
                fallback_method = "telea" if inpaint_method == "neural" else inpaint_method
                prev_inpaint = (
                    None
                    if full_reinpaint or not prev_mask_synced or not paths.inpainted_exists
                    else open_rgb_image(paths.inpainted_path)
                )
                repair_kwargs: dict[str, Any] = {}
                resolved_blur_radius: Optional[int] = None
                if fallback_method == "blur":
                    if blur_radius is not None:
                        resolved_blur_radius = max(0, min(200, int(blur_radius)))
                    else:
                        cfg_dict = settings.load_mtui_config_dict()
                        cfg_br = (cfg_dict.get("inpainter") or {}).get("blur_radius", 0)
                        resolved_blur_radius = max(0, min(200, int(cfg_br or 0)))
                    repair_kwargs["blur_radius"] = resolved_blur_radius
                repaired = apply_incremental_local_repair(
                    base_rgb,
                    prev_inpaint,
                    new_mask,
                    added_delta,
                    removed_delta,
                    method=fallback_method,
                    **repair_kwargs,
                )
                save_rgb_image_atomic(repaired, paths.inpainted_path)
                actual_inp_path = paths.inpainted_path
                inpaint_info = {
                    "status": "ok",
                    "inpainted_path": str(paths.inpainted_path),
                    "method": fallback_method,
                }
                if resolved_blur_radius is not None:
                    inpaint_info["blur_radius"] = resolved_blur_radius

            image_data["mask_raw"] = new_mask_b64
            image_data["mask_is_refined"] = True
            image_data["mask_inpainted_sha1"] = _mask_sha1_hex(new_mask, base_rgb.shape[:2])
            image_data["mask_inpainted_stat"] = _file_stat_sig(actual_inp_path)
            saved_json = save_page_json_atomic(paths, image_key, image_data)
            json_committed = True
            cleanup_backup = True
        except BaseException as orig_exc:
            if not json_committed:
                if prev_inpaint_had_file and inpaint_backup_tmp and os.path.isfile(inpaint_backup_tmp):
                    try:
                        _atomic_replace_with_retry(inpaint_backup_tmp, paths.inpainted_path)
                        inpaint_backup_tmp = ""
                        cleanup_backup = True
                    except OSError as rb_exc:
                        # R2-F45: Preserve backup file on disk when rollback fails and surface recovery path!
                        cleanup_backup = False
                        recovery_msg = (
                            f"Rollback of {paths.inpainted_path} failed ({rb_exc}); "
                            f"backup preserved at {inpaint_backup_tmp}"
                        )
                        logger.error(recovery_msg)
                        if isinstance(orig_exc, Exception):
                            raise RuntimeError(f"{orig_exc} ({recovery_msg})") from orig_exc
                        orig_exc.add_note(recovery_msg)
                elif not prev_inpaint_had_file:
                    cleanup_backup = True
                    seen_cands: dict[str, Path] = {}
                    for cand in (paths.inpainted_path, actual_inp_path):
                        norm_cand = os.path.normcase(os.path.abspath(str(cand)))
                        seen_cands.setdefault(norm_cand, cand)
                    unlink_failures: list[str] = []
                    for created_cand in seen_cands.values():
                        try:
                            created_cand.unlink()
                        except FileNotFoundError:
                            pass
                        except OSError as rb_exc:
                            unlink_failures.append(f"{created_cand} ({rb_exc})")
                    if unlink_failures:
                        residual_msg = (
                            "Rollback failed to remove newly created uncommitted inpainted file(s): "
                            + "; ".join(unlink_failures)
                        )
                        logger.error(residual_msg)
                        if isinstance(orig_exc, Exception):
                            raise RuntimeError(f"{orig_exc} ({residual_msg})") from orig_exc
                        orig_exc.add_note(residual_msg)
            raise
        finally:
            if cleanup_backup and inpaint_backup_tmp and os.path.exists(inpaint_backup_tmp):
                try:
                    os.remove(inpaint_backup_tmp)
                except OSError:
                    pass

        # R2-F43: Refresh page paths OUTSIDE the inpaint rollback scope after JSON has committed
        refreshed = resolve_page_paths(saved_json)

    refreshed = resolve_page_paths(refreshed.source_path)
    render_info = _render_single_page_via_worker(refreshed) if re_render else None

    return _json_text(
        {
            "page_name": refreshed.source_path.name,
            "added_mask_pixels": added_px,
            "removed_mask_pixels": removed_px,
            "inpaint_result": inpaint_info,
            "render_result": render_info,
        }
    )


@mcp.tool()
def mtui_add_region(
    page_path: str,
    bbox_xyxy: Optional[list[float]] = None,
    polygon: Optional[list[list[float]]] = None,
    source_text: str = "",
    translation: str = "",
    font_size: Optional[int] = None,
    font_family: str = "",
    bold: bool = False,
    font_color: str = "#000000",
    stroke_color: str = "#ffffff",
    stroke_width: float = 0.07,
    direction: Literal["h", "v", "auto"] = "auto",
    alignment: Literal["left", "center", "right", "auto"] = "auto",
    angle: float = 0.0,
    auto_mask_ink: bool = True,
    inpaint_method: Literal["neural", "solid_fill", "telea", "none"] = "solid_fill",
    re_render: bool = True,
) -> str:
    """Add a missed speech bubble or SFX text region to a page, optionally masking & erasing its original ink and re-rendering.

    Note on `alignment`: defaults to `"auto"` (inherits MTUI `config.json` `render.alignment`, typically `"left"` = top-aligned for vertical manga columns).
    Do NOT pass `alignment="center"` for standard vertical manga dialogue bubbles — use `center-bubbles` to center the whole text block inside the bubble instead.
    """
    local_method = inpaint_method if inpaint_method in ("solid_fill", "telea") else None
    init_paths = resolve_page_paths(page_path)
    # R2-F18 & R2-F48: Hold `page_edit_lock` continuously across region insertion and neural inpaint,
    # and compute true pre-edit mask sync state + full mask pixel delta so `mask_inpainted_sha1` never
    # marks unsynced pixels outside `rect` as synced!
    with _acquire_page_edit_lock(init_paths):
        pre_paths = resolve_page_paths(init_paths.source_path)
        pre_mask: Any = None
        pre_mask_sha1 = ""
        prev_mask_synced = False
        if auto_mask_ink and pre_paths.display_base_path.is_file():
            pre_base_rgb = open_rgb_image(pre_paths.display_base_path)
            pre_shape_hw = pre_base_rgb.shape[:2]
            pre_img_data: dict[str, Any] = {}
            if pre_paths.json_exists:
                _, pre_img_data = load_page_json(pre_paths)
            pre_mask = decode_mask_base64(pre_img_data.get("mask_raw"), pre_shape_hw)
            pre_mask_sha1 = _mask_sha1_hex(pre_mask, pre_shape_hw)
            prev_mask_synced = _is_prev_mask_synced(pre_img_data, pre_mask_sha1, pre_paths.inpainted_path)

        refreshed, reg_summary = add_region_to_page(
            page_path,
            bbox_xyxy=bbox_xyxy,
            polygon=polygon,
            source_text=source_text,
            translation=translation,
            font_size=font_size,
            font_family=font_family,
            bold=bold,
            font_color=font_color,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            direction=direction,
            alignment=alignment,
            angle=angle,
            auto_mask_ink=auto_mask_ink,
            local_inpaint_method=local_method,
        )

        if auto_mask_ink and refreshed.display_base_path.is_file():
            img_key, img_data = load_page_json(refreshed)
            base_rgb = open_rgb_image(refreshed.display_base_path)
            cur_mask = decode_mask_base64(img_data.get("mask_raw"), base_rgb.shape[:2])
            cur_mask_sha1 = _mask_sha1_hex(cur_mask, base_rgb.shape[:2])
            if inpaint_method == "neural" and get_runtime_settings().is_engine_available:
                if cur_mask is not None:
                    import numpy as np

                    if pre_mask is not None and pre_mask.shape == cur_mask.shape:
                        added_m = np.where((cur_mask > 0) & (pre_mask == 0), 255, 0).astype(np.uint8)
                        removed_m = np.where((pre_mask > 0) & (cur_mask == 0), 255, 0).astype(np.uint8)
                    else:
                        added_m = np.where(cur_mask > 0, 255, 0).astype(np.uint8)
                        removed_m = np.zeros_like(cur_mask)
                    try:
                        inp_res = get_worker_client().call(
                            "incremental_neural_inpaint",
                            {
                                "display_base_path": str(refreshed.display_base_path),
                                "inpainted_path": str(refreshed.inpainted_path),
                                "current_mask_b64": encode_mask_base64(cur_mask),
                                "added_mask_b64": encode_mask_base64(added_m) if np.any(added_m) else "",
                                "removed_mask_b64": encode_mask_base64(removed_m) if np.any(removed_m) else "",
                                "prev_mask_synced": prev_mask_synced,
                            },
                        )
                        actual_inp = Path(str(inp_res.get("inpainted_path") or refreshed.inpainted_path))
                        img_data["mask_inpainted_sha1"] = cur_mask_sha1
                        img_data["mask_inpainted_stat"] = _file_stat_sig(actual_inp)
                        save_page_json_atomic(refreshed, img_key, img_data, auto_initial_snapshot=False)
                    except BaseException:
                        img_data["mask_inpainted_sha1"] = ""
                        img_data["mask_inpainted_stat"] = _file_stat_sig(refreshed.inpainted_path)
                        save_page_json_atomic(refreshed, img_key, img_data, auto_initial_snapshot=False)
                        raise
                    refreshed = resolve_page_paths(refreshed.source_path)
            elif local_method in ("solid_fill", "telea"):
                if prev_mask_synced and refreshed.inpainted_path.is_file():
                    img_data["mask_inpainted_sha1"] = cur_mask_sha1
                    img_data["mask_inpainted_stat"] = _file_stat_sig(refreshed.inpainted_path)
                    save_page_json_atomic(refreshed, img_key, img_data, auto_initial_snapshot=False)
                    refreshed = resolve_page_paths(refreshed.source_path)
                elif not prev_mask_synced and "mask_inpainted_sha1" in img_data:
                    img_data["mask_inpainted_sha1"] = ""
                    img_data["mask_inpainted_stat"] = _file_stat_sig(refreshed.inpainted_path)
                    save_page_json_atomic(refreshed, img_key, img_data, auto_initial_snapshot=False)
                    refreshed = resolve_page_paths(refreshed.source_path)
            elif cur_mask_sha1 != pre_mask_sha1:
                img_data["mask_inpainted_sha1"] = ""
                img_data["mask_inpainted_stat"] = _file_stat_sig(refreshed.inpainted_path)
                save_page_json_atomic(refreshed, img_key, img_data, auto_initial_snapshot=False)
                refreshed = resolve_page_paths(refreshed.source_path)

    render_info = _render_single_page_via_worker(refreshed) if re_render else None
    return _json_text(
        {
            "added_region": reg_summary,
            "render_result": render_info,
        }
    )


@mcp.tool()
def mtui_delete_regions(
    page_path: str,
    region_indices: list[int],
    restore_artwork: bool = True,
    re_render: bool = True,
) -> str:
    """Delete false-positive or unwanted region(s) and automatically restore the underlying original artwork pixels (`restore_artwork=True`)."""
    refreshed, info = delete_regions_from_page(
        page_path,
        region_indices,
        restore_artwork=restore_artwork,
    )
    info["render_result"] = _render_single_page_via_worker(refreshed) if re_render else None
    return _json_text(info)


@mcp.tool()
def mtui_merge_regions(
    page_path: str,
    region_indices: list[int],
    merged_translation: Optional[str] = None,
    force_merge: bool = False,
    re_render: bool = True,
) -> str:
    """Merge 2 or more fragmented regions belonging to the same single-chamber speech bubble into one clean region (automatically refuses to merge across inward cusps of connected multi-lobe bubbles unless `force_merge=True`)."""
    refreshed, merged_summary = merge_regions_on_page(
        page_path,
        region_indices,
        merged_translation=merged_translation,
        force_merge=force_merge,
    )
    render_info = _render_single_page_via_worker(refreshed) if re_render else None
    return _json_text({"merged_region": merged_summary, "render_result": render_info})


@mcp.tool()
def mtui_align_regions(
    page_path: str,
    region_indices: list[int],
    mode: Literal[
        "left",
        "horizontal_center",
        "right",
        "top",
        "vertical_center",
        "bottom",
        "distribute_horizontal",
        "distribute_vertical",
    ],
    reference: Literal["selection", "canvas"] = "selection",
    re_render: bool = True,
) -> str:
    """Align or evenly distribute multiple text regions on a page."""
    refreshed, summaries = align_regions_on_page(page_path, region_indices, mode=mode, reference=reference)
    render_info = _render_single_page_via_worker(refreshed) if re_render else None
    return _json_text({"aligned_regions": summaries, "render_result": render_info})


@mcp.tool()
def mtui_manage_paste_overlays(
    page_path: str,
    action: Literal["list", "add_clone_patch", "add_image_file", "remove", "clear"] = "list",
    overlay_id: Optional[str] = None,
    name: str = "MCP修补贴片",
    source_rect_xyxy: Optional[list[float]] = None,
    image_file_path: Optional[str] = None,
    target_center_xy: Optional[list[float]] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
    rotation: float = 0.0,
    opacity: float = 1.0,
    feather_px: int = 3,
    z: int = 0,
    flip_h: bool = False,
    flip_v: bool = False,
    re_render: bool = True,
) -> str:
    """Manage `paste_overlays` on a page — including `add_clone_patch` (Clone-Stamp a clean screentone/background patch from `source_rect_xyxy` onto `target_center_xy`!)."""
    refreshed, info = manage_page_paste_overlays(
        page_path,
        action=action,
        overlay_id=overlay_id,
        name=name,
        source_rect_xyxy=source_rect_xyxy,
        image_file_path=image_file_path,
        target_center_xy=target_center_xy,
        width=width,
        height=height,
        rotation=rotation,
        opacity=opacity,
        feather_px=feather_px,
        z=z,
        flip_h=flip_h,
        flip_v=flip_v,
    )
    if re_render and action != "list":
        info["render_result"] = _render_single_page_via_worker(refreshed)
    return _json_text(info)


# ===========================================================================
# Group 5: Chapter Batch Operations, Glossary & Undo Snapshots
# ===========================================================================


@mcp.tool()
def mtui_batch_edit_chapter(
    target_path: str,
    filter_criteria: Optional[dict[str, Any]] = None,
    find_text: Optional[str] = None,
    replace_text: Optional[str] = None,
    use_regex: bool = False,
    set_fields: Optional[dict[str, Any]] = None,
    rich_style: Optional[dict[str, Any]] = None,
    rich_spans: Optional[list[dict[str, Any]]] = None,
    dry_run: bool = False,
    re_render: bool = True,
) -> str:
    """Batch search-and-replace or conditionally update typography/styles across an entire manga chapter!

    `filter_criteria` optional keys: `translation_contains`, `translation_regex`, `source_contains`, `source_regex`,
    `direction` (`"h"`/`"v"`), `font_family`, `min_font_size`, `max_font_size`, `font_color`, `region_indices`.
    `set_fields` optional keys: `font_size`, `font_size_delta` (scales perspective quads automatically), `font_family`, `direction`, `alignment`,
    `font_color`, `stroke_color`, `stroke_width`, `line_spacing`, `letter_spacing`.
    `rich_style` / `rich_spans` support all `richtext.v1` keys (including `blur`/`blurAngle`/`blurMotion` & `rubyInherit`).
    Set `dry_run=True` to preview all matching regions before writing changes.
    """
    modified_pages, report = batch_edit_chapter_pages(
        target_path,
        filter_criteria=filter_criteria,
        find_text=find_text,
        replace_text=replace_text,
        use_regex=use_regex,
        set_fields=set_fields,
        rich_style=rich_style,
        rich_spans=rich_spans,
        dry_run=dry_run,
    )
    rendered_files: list[str] = []
    if not dry_run and re_render and modified_pages:
        for p in modified_pages:
            r_res = _render_single_page_via_worker(p)
            rendered_files.append(r_res.get("output_path", ""))
    report["rendered_outputs"] = rendered_files
    return _json_text(report)


@mcp.tool()
def mtui_manage_glossary(
    action: Literal["list", "upsert", "delete", "set_system_prompt", "list_files"] = "list",
    glossary_file: Optional[str] = None,
    category: Literal["Person", "Location", "Org", "Item", "Skill", "Creature"] = "Person",
    entries: Optional[list[dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """Read or update `manga-translator-ui` YAML glossaries (`dict/*.yaml`) or TXT dictionaries (`mit_glossary.txt`, `post_dict.txt`).

    Each entry in `entries` supports `{"original": "ましろ", "translation": "真白", "description": "女主角", "condition": ""}`.
    """
    return _json_text(
        manage_glossary_entries(
            action=action,
            glossary_file=glossary_file,
            category=category,
            entries=entries,
            system_prompt=system_prompt,
        )
    )


@mcp.tool()
def mtui_snapshots(
    target_path: str,
    action: Literal["list", "create", "restore", "cleanup"] = "list",
    label: Optional[str] = None,
    snapshot_id: Optional[str] = None,
) -> str:
    """Create, list, restore, or clean up point-in-time backups of `_translations.json`, `inpainted`, and rendered output.

    Supports BOTH single-page image paths AND whole-chapter directories:
    - `action="create"`: snapshots a single page (preferred on-demand before editing a page in Gate B) or a folder under `label`.
    - `action="restore"`: restores a single page (or folder) by exact `snapshot_id`, by `label` (e.g. `label="pre_gate_b"`),
      or — if both are omitted — restores the latest checkpoint snapshot.
    - `action="cleanup"`: deletes temporary `.mtui_mcp_snapshots` (filtered by `label` / `snapshot_id`, or ALL snapshots when omitted)
      once a chapter finishes Stage 7 cleanly to prevent disk bloat during multi-volume unattended batch runs.
    """
    if action == "list":
        snaps = list_snapshots(target_path)
        return _json_text({"count": len(snaps), "snapshots": snaps})
    if action == "create":
        return _json_text(create_snapshots_for_target(target_path, label=label or "checkpoint"))
    if action == "restore":
        return _json_text(restore_snapshot(target_path, snapshot_id=snapshot_id, label=label))
    if action == "cleanup":
        return _json_text(cleanup_snapshots_for_target(target_path, label=label, snapshot_id=snapshot_id))
    raise ValueError(f"Unknown snapshot action: {action!r}")


# ===========================================================================
# Group 6: Native Rendering, PSD Export, OCR / Re-Translate & Pipeline Runner
# ===========================================================================


def _is_render_result_incomplete(res: Any) -> bool:
    if not isinstance(res, dict):
        return False
    st = res.get("status")
    return st not in (None, "ok", "success") or res.get("metadata_saved") is False


def _invoke_closed_loop_centering(
    target_path: str,
    *,
    passes: int,
    render_page_fn: Any,
    render_results_out: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    def _tracking_render(pp: PagePaths) -> Any:
        res = render_page_fn(pp)
        render_results_out[pp.source_path.name] = res
        return res

    call_kwargs: dict[str, Any] = {
        "passes": passes,
        "render_page_fn": _tracking_render,
    }
    try:
        sig = inspect.signature(run_closed_loop_bubble_centering)
        if "render_results_out" in sig.parameters:
            call_kwargs["render_results_out"] = render_results_out
    except (TypeError, ValueError):
        pass
    return run_closed_loop_bubble_centering(target_path, **call_kwargs)


_COMPLETE_CLOSED_LOOP_STATUSES = (None, "ok", "success", "unchanged", "not_requested")


def _is_closed_loop_render_result_incomplete(res: Any) -> bool:
    if not isinstance(res, dict):
        return False
    st = res.get("status")
    return (
        st not in _COMPLETE_CLOSED_LOOP_STATUSES
        or res.get("metadata_saved") is False
        or res.get("output_updated") is False
    )


def _has_incomplete_closed_loop_renders(
    report: dict[str, list[dict[str, Any]]],
    render_results: dict[str, Any],
) -> bool:
    if any(_is_closed_loop_render_result_incomplete(v) for v in render_results.values()):
        return True
    for items in report.values():
        for item in items or []:
            if isinstance(item, dict) and item.get("render_status") not in _COMPLETE_CLOSED_LOOP_STATUSES:
                return True
    return False


@mcp.tool()
def mtui_render_pages(
    target_path: str,
    output_dir: Optional[str] = None,
    proof_only: bool = False,
    force_backend_inpaint: bool = False,
    export_psd: bool = False,
    psd_script_only: bool = False,
    inpainter: Optional[str] = None,
    center_bubbles: bool = False,
    center_passes: int = 2,
) -> str:
    """Render one page or all pages in a folder using `manga-translator-ui`'s native Qt6 offscreen `ExportService` engine.

    Set `center_bubbles=True` to also run 2-pass closed-loop optical bubble centering inside the same warm worker
    (zero extra subprocesses or duplicate GPU memory!).
    Set `proof_only=True` to render non-destructive proof image(s) into `manga_translator_work/proofread/` before overwriting final output.
    Set `export_psd=True` to also generate layered editable Photoshop `.psd` files alongside the rendered images (`psd_script_only=True` emits only the JSX script)!
    """
    pages = [p for p in list_manga_pages_in_path(target_path) if p.json_exists]
    if not pages:
        single = resolve_page_paths(target_path, explicit_output_dir=output_dir)
        if single.json_exists:
            pages = [single]
    if not pages:
        raise FileNotFoundError(f"No pages with *_translations.json found at {target_path}")

    results: list[dict[str, Any]] = []
    for p in pages:
        res = _render_single_page_via_worker(
            p,
            output_dir=output_dir,
            proof_only=proof_only,
            force_backend_inpaint=force_backend_inpaint,
            export_psd=export_psd,
            psd_script_only=psd_script_only,
            inpainter=inpainter,
        )
        results.append(res)

    has_incomplete = any(_is_render_result_incomplete(r) for r in results)
    payload: dict[str, Any] = {
        "status": "partial" if has_incomplete else "ok",
        "rendered_count": len(results),
        "results": results,
    }
    if center_bubbles and not proof_only:
        center_render_results: dict[str, Any] = {}
        centered = _invoke_closed_loop_centering(
            target_path,
            passes=center_passes,
            render_page_fn=lambda pp: _render_single_page_via_worker(
                pp,
                output_dir=output_dir,
                proof_only=False,
                force_backend_inpaint=False,
                export_psd=export_psd,
                psd_script_only=psd_script_only,
                inpainter=inpainter,
            ),
            render_results_out=center_render_results,
        )
        if _has_incomplete_closed_loop_renders(centered, center_render_results):
            payload["status"] = "partial"
        payload["centered_page_count"] = len(centered)
        payload["centered_pages"] = centered
        payload["render_results"] = center_render_results
    return _json_text(payload)


@mcp.tool()
def mtui_center_bubbles(
    target_path: str,
    passes: int = 2,
    save_json_path: Optional[str] = None,
) -> str:
    """Run closed-loop optical speech-bubble centering + Qt6 re-rendering on a page or chapter inside the shared warm worker.

    Uses Cusp-Bisector Multi-Lobe Split + Gestalt Ellipse Completion + Orthogonal Ink Equal-Clearance without spawning
    a second MCP server or GPU process.
    """
    render_results: dict[str, Any] = {}
    report = _invoke_closed_loop_centering(
        target_path,
        passes=passes,
        render_page_fn=_render_single_page_via_worker,
        render_results_out=render_results,
    )
    has_incomplete = _has_incomplete_closed_loop_renders(report, render_results)
    out: dict[str, Any] = {
        "status": "partial" if has_incomplete else "ok",
        "centered_page_count": len(report),
        "passes": passes,
        "pages": report,
        "render_results": render_results,
    }
    if save_json_path:
        dst = Path(save_json_path).expanduser()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(_json_text(report), encoding="utf-8")
        out["saved_json_path"] = str(dst)
    return _json_text(out)


@mcp.tool()
def mtui_live_gui_session(
    action: Literal["status", "wait_for_translation", "open_page", "undo", "redo", "save_page"] = "status",
    path: Optional[str] = None,
    revision: Optional[str] = None,
    timeout_seconds: int = 25,
    session_name: Optional[str] = None,
) -> str:
    """Interact with a live `manga-translator-ui` desktop GUI window when one is running with MCP enabled!

    Connects automatically to the desktop GUI's IPC bridge (`McpService` / `--mcp` session key). When no desktop GUI window is open
    (or on older stock upstream `hgmzhn/manga-translator-ui` builds without the live GUI bridge), `action="status"` cleanly returns
    `live_gui_connected=False` — use all other `mtui_*` tools directly (they run headlessly via MTUI-MCP's warm GPU worker).
    Supports waiting for an active GUI translation batch (`wait_for_translation`), opening a page on the live Qt canvas (`open_page`),
    stepping the GUI's shared undo/redo history (`undo` / `redo`), or saving the live GUI state (`save_page`).
    """
    if action == "status":
        available = is_live_gui_session_available(session_name)
        info = call_live_gui("list_pages", session_name=session_name, limit=20) if available else None
        return _json_text({"live_gui_connected": available, "gui_state": info})
    if action == "wait_for_translation":
        return _json_text(wait_for_live_gui_translation(timeout_seconds=timeout_seconds, session_name=session_name))
    if action == "open_page":
        if not path:
            raise ValueError("path is required for action='open_page'")
        return _json_text(call_live_gui("open_page", session_name=session_name, path=path))
    if action in ("undo", "redo"):
        cur_rev = revision or call_live_gui("read_page", session_name=session_name, limit=1)["revision"]
        return _json_text(call_live_gui("history_step", session_name=session_name, revision=cur_rev, action=action))
    if action == "save_page":
        cur_rev = revision or call_live_gui("read_page", session_name=session_name, limit=1)["revision"]
        return _json_text(call_live_gui("save_page", session_name=session_name, revision=cur_rev))
    raise ValueError(f"Unsupported live GUI action: {action!r}")


@mcp.tool()
def mtui_ocr_or_translate_regions(
    page_path: str,
    region_indices: Optional[list[int]] = None,
    custom_boxes_xyxy: Optional[list[list[float]]] = None,
    run_ocr: bool = True,
    run_translate: bool = False,
    translator: Optional[str] = None,
    target_lang: Optional[str] = None,
    ocr: Optional[str] = None,
    commit: bool = False,
    re_render: bool = False,
) -> str:
    """Run `manga-translator-ui`'s native OCR (`48px`/`mocr`/`paddleocr_vl`/etc.) and/or Translator on specific regions or `[x1, y1, x2, y2]` boxes."""
    paths = resolve_page_paths(page_path)
    res = get_worker_client().call(
        "ocr_or_translate_regions",
        {
            "source_path": str(paths.source_path),
            "display_base_path": str(paths.display_base_path),
            "json_path": str(paths.json_path) if paths.json_exists else None,
            "region_indices": region_indices or [],
            "custom_boxes_xyxy": custom_boxes_xyxy or [],
            "run_ocr": run_ocr,
            "run_translate": run_translate,
            "translator": translator,
            "target_lang": target_lang,
            "ocr": ocr,
            "commit": commit,
        },
    )
    if commit and re_render and paths.json_exists:
        res["render_result"] = _render_single_page_via_worker(resolve_page_paths(paths.source_path))
    return _json_text(res)


@mcp.tool()
def mtui_run_translation_pipeline(
    inputs: list[str],
    output_dir: Optional[str] = None,
    config_path: Optional[str] = None,
    overwrite: bool = True,
    use_gpu: Optional[bool] = None,
    output_format: Optional[str] = None,
    use_subprocess: bool = False,
    memory_limit: Optional[int] = None,
    memory_percent: Optional[float] = None,
    batch_per_restart: Optional[int] = None,
    batch_size: Optional[int] = None,
    attempts: Optional[int] = None,
    disable_onnx_gpu: bool = False,
) -> str:
    """Execute `manga-translator-ui` (`python -m manga_translator local`) on untranslated image(s) or folder(s)."""
    res = get_worker_client().call(
        "run_cli_pipeline",
        {
            "inputs": inputs,
            "output_dir": output_dir,
            "config_path": config_path,
            "overwrite": overwrite,
            "use_gpu": use_gpu,
            "output_format": output_format,
            "subprocess": use_subprocess,
            "memory_limit": memory_limit,
            "memory_percent": memory_percent,
            "batch_per_restart": batch_per_restart,
            "batch_size": batch_size,
            "attempts": attempts,
            "disable_onnx_gpu": disable_onnx_gpu,
        },
        timeout_sec=1800.0,
    )
    return _json_text(res)


# ===========================================================================
# MCP Workflow Prompts (Expert Manga Post-Editing SOPs)
# ===========================================================================


@mcp.prompt()
def proofread_and_polish_chapter(target_folder: str) -> str:
    """Full end-to-end SOP for proofreading translations, repairing inpainting defects, and polishing typesetting across a manga chapter."""
    return f"""请对漫画目录 `{target_folder}` 执行完整的翻译后精修流程（译文校对 + 图片修复补充 + 排版与富文本优化）：

1. **全局扫描与术语核查**
   - 调用 `mtui_qa_audit(target_path="{target_folder}")` 自动筛查漏翻假名/韩文、OCR 低置信度框、断句孤字/行首标点、竖横排错配、缺字（豆腐块）以及残余未擦除墨迹（`inpaint_residual_ink`）。
   - 调用 `mtui_chapter_script(target_path="{target_folder}", reading_order="rtl")` 通读整话台词，核对人名、称呼、人称代词、语气及拟声词前后一致性；必要时通过 `mtui_manage_glossary` 同步术语表，并用 `mtui_batch_edit_chapter` 批量统一术语。

2. **逐页视觉检查（原图 vs 修复图 vs 渲染成图）**
   - 对存在问题或关键页面调用 `mtui_inspect_page_visual(mode="inpaint_check")` 查看全局效果。
   - 对需要精修的气泡或残字区域调用 `mtui_inspect_region_visual`，读取边缘标尺上的精确世界像素坐标 `(X, Y)`。

3. **图片擦除补充与误擦还原**
   - 若气泡内或拟声词旁残留假名/注音/标点：用 `mtui_edit_mask_and_inpaint` 的 `add_rects` / `add_polygons` / `dilate_regions` 精确擦除（纯白气泡可用 `inpaint_method="solid_fill"`，网点/复杂背景用 `inpaint_method="neural"`）。
   - 若原图分镜框线、人物线条或背景网点被误擦模糊：用 `erase_rects` / `erase_polygons` 擦除多余蒙版，直接无损还原原图像素；复杂网点也可用 `mtui_manage_paste_overlays(action="add_clone_patch")` 克隆修补；误检的非文字框用 `mtui_delete_regions(restore_artwork=True)` 删除并还原画面；漏检气泡用 `mtui_add_region` 补齐。

4. **排版、字号、字体、颜色、样式与断句精修**
   - 用 `mtui_list_fonts(cjk_only=True)` 查看可用漫画字体（对生僻字传入 `check_text_glyphs` 防止缺字）。
   - 对普通对白、内心独白、旁白、拟声词分别挑选合适字体、字号、字色/描边色（可用 `sample_color_from_original=True` 自动吸取原图墨色）、竖排/横排（`direction`）和字行距。
   - 需要加粗、斜体、着重号、注音（Ruby 及 `rubyInherit`）、竖排内横排（TCY 如 `！？`、数字）、双层描边、发光或文字模糊/动感模糊（`blur`/`blurMotion`）时，调用 `mtui_style_region_richtext` 生成 `richtext.v1` 样式；四点透视招牌/屏幕使用 `white_frame_quad_local` 保持透视形变。
   - **文字断句统一由你（Agent）自行判断**：结合漫画气泡的宽窄弧度、台词语义节奏与情绪停顿，直接在 `mtui_update_regions` 的 `translation` 中用 `\\n` 编写最自然的分行（避免行首出现句读标点、避免末行出现单字寡行），并搭配合适的 `font_size` 与 `center` 微调。后端已关闭一切原生自动换行，100% 原样渲染你指定的断句。

5. **重渲染与复检交付**
   - 确保所有修改页完成 `mtui_render_pages` 重渲染，并用 `mtui_inspect_page_visual(mode="side_by_side")` 复检最终成图，最后汇总修改清单。
"""


@mcp.prompt()
def inspect_and_fix_page(page_path: str) -> str:
    """Single-page deep visual QA, inpaint repair, and typesetting polish SOP."""
    return f"""请对单页漫画 `{page_path}` 进行深度检查与精修：
1. 调用 `mtui_get_page_details` 与 `mtui_qa_audit` 获取全部文本框信息与自动诊断结果。
2. 调用 `mtui_inspect_page_visual(page_path="{page_path}", mode="inpaint_check")` 检查原图、消字底图与最终嵌字图。
3. 对任何存在翻译生硬、断句不佳、字号/字体/颜色不协调、漏擦残字或误擦背景的区域，先用 `mtui_inspect_region_visual` 查看带 `(X, Y)` 像素坐标轴的高清放大图，由你自行结合语义和气泡形状决定最佳 `\\n` 断句与字号位置，再通过 `mtui_edit_mask_and_inpaint`、`mtui_update_regions`、`mtui_style_region_richtext` 修正并重渲染。
4. 修正完成后再次调用 `mtui_inspect_page_visual(page_path="{page_path}", mode="side_by_side")` 确认最终视觉品质。
"""
