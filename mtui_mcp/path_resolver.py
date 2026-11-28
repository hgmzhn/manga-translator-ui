"""Bi-directional path resolution for manga-translator-ui projects and artifacts."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import get_runtime_settings

logger = logging.getLogger("mtui_mcp.paths")

WORK_DIR_NAME = "manga_translator_work"
JSON_SUBDIR = "json"
INPAINTED_SUBDIR = "inpainted"
ORIGINALS_SUBDIR = "originals"
TRANSLATIONS_SUBDIR = "translations"
PAINT_OVERLAY_SUBDIR = "paint_overlay"
EDITOR_BASE_SUBDIR = "editor_base"
RESULT_SUBDIR = "result"
MCP_INSPECT_SUBDIR = ".mtui_mcp_inspect"
MCP_SNAPSHOTS_SUBDIR = ".mtui_mcp_snapshots"

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".webp",
    ".avif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
}


def natural_sort_key(path: str | Path) -> list[tuple[bool, int | str]]:
    """Natural sort key matching manga-translator-ui's FileService._natural_sort_key."""
    normalized = str(path).replace("\\", "/")
    parts: list[tuple[bool, int | str]] = []
    for part in re.split(r"(\d+)", normalized):
        if part.isdigit():
            parts.append((False, int(part)))
        elif part:
            parts.append((True, part.lower()))
    return parts


def _read_json_quiet(path: Path) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_first_image_entry(json_data: dict[str, Any], source_path: Optional[Path] = None) -> tuple[Optional[str], dict[str, Any]]:
    if not json_data:
        return None, {}
    if source_path is not None:
        abs_str = os.path.abspath(str(source_path))
        if abs_str in json_data and isinstance(json_data[abs_str], dict):
            return abs_str, json_data[abs_str]
        norm_target = os.path.normcase(abs_str)
        for k, v in json_data.items():
            try:
                if os.path.normcase(os.path.abspath(k)) == norm_target and isinstance(v, dict):
                    return k, v
            except Exception:
                continue
    first_k = next(iter(json_data.keys()))
    first_v = json_data[first_k]
    if isinstance(first_v, dict):
        return first_k, first_v
    if isinstance(first_v, list):
        return first_k, {"regions": first_v}
    return first_k, {}


def _find_source_from_translation_map(image_path: Path) -> Optional[Path]:
    """Check output_dir/translation_map.json just like EditorControllerDocumentService."""
    map_path = image_path.parent / "translation_map.json"
    if not map_path.is_file():
        return None
    mapping = _read_json_quiet(map_path)
    if not mapping:
        return None
    norm_str = os.path.normpath(str(image_path))
    direct = mapping.get(norm_str)
    if isinstance(direct, str) and direct.strip() and os.path.isfile(direct):
        return Path(os.path.normpath(direct))
    norm_case = os.path.normcase(os.path.abspath(norm_str))
    for out_key, src_val in mapping.items():
        if not isinstance(out_key, str) or not isinstance(src_val, str) or not src_val.strip():
            continue
        try:
            if os.path.normcase(os.path.abspath(out_key)) == norm_case and os.path.isfile(src_val):
                return Path(os.path.normpath(src_val))
        except Exception:
            continue
    return None


def resolve_source_image_path(path_like: str | Path) -> Path:
    """Resolve any page reference (source image, work JSON, inpainted, editor_base, or translated output) to the canonical source image path."""
    raw = Path(path_like).expanduser()
    try:
        norm = Path(os.path.normpath(os.path.abspath(str(raw))))
    except Exception:
        norm = raw

    # 1. Passed a *_translations.json path directly
    if norm.suffix.lower() == ".json" and norm.name.endswith("_translations.json"):
        data = _read_json_quiet(norm)
        if data:
            first_key, _ = _extract_first_image_entry(data)
            if first_key and os.path.isfile(first_key):
                return Path(os.path.normpath(first_key))
        # Infer source directory from manga_translator_work/json/<stem>_translations.json
        stem = norm.name[: -len("_translations.json")]
        if norm.parent.name == JSON_SUBDIR and norm.parent.parent.name == WORK_DIR_NAME:
            src_dir = norm.parent.parent.parent
        else:
            src_dir = norm.parent
        for ext in SUPPORTED_IMAGE_EXTENSIONS:
            candidate = src_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
        return norm

    # 2. Check translation_map.json in output directory
    mapped = _find_source_from_translation_map(norm)
    if mapped is not None:
        return mapped

    # 3. Passed manga_translator_work/editor_base/<name>, inpainted/<stem>_inpainted.<ext>, or result/<stem>.<ext>
    parent = norm.parent
    grandparent = parent.parent
    if parent.name == EDITOR_BASE_SUBDIR and grandparent.name == WORK_DIR_NAME:
        candidate = grandparent.parent / norm.name
        if candidate.is_file():
            return candidate
    if parent.name == INPAINTED_SUBDIR and grandparent.name == WORK_DIR_NAME:
        src_dir = grandparent.parent
        stem = norm.stem
        if stem.endswith("_inpainted"):
            stem = stem[: -len("_inpainted")]
        same_ext = src_dir / f"{stem}{norm.suffix}"
        if same_ext.is_file():
            return same_ext
        for ext in SUPPORTED_IMAGE_EXTENSIONS:
            candidate = src_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    if parent.name == RESULT_SUBDIR and grandparent.name == WORK_DIR_NAME:
        src_dir = grandparent.parent
        stem = norm.stem
        if stem.endswith("-translated"):
            stem = stem[: -len("-translated")]
        same_ext = src_dir / f"{stem}{norm.suffix}"
        if same_ext.is_file():
            return same_ext
        for ext in SUPPORTED_IMAGE_EXTENSIONS:
            candidate = src_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate

    # 4. Passed a rendered output image inside `<folder>-translated/<name>` or `<stem>-translated.<ext>`
    #    when no manga_translator_work exists next to `norm` itself.
    own_work_json = norm.parent / WORK_DIR_NAME / JSON_SUBDIR / f"{norm.stem}_translations.json"
    if not own_work_json.is_file():
        if norm.stem.endswith("-translated"):
            orig_stem = norm.stem[: -len("-translated")]
            for ext in (norm.suffix, *SUPPORTED_IMAGE_EXTENSIONS):
                candidate = norm.parent / f"{orig_stem}{ext}"
                if candidate.is_file():
                    return candidate
        # Walk ancestor directories to see if an ancestor ends with `-translated`
        parts = list(norm.parts)
        for idx in range(len(parts) - 2, 0, -1):
            folder_name = parts[idx]
            if folder_name.endswith("-translated"):
                raw_folder = folder_name[: -len("-translated")]
                rebuilt = Path(*parts[:idx], raw_folder, *parts[idx + 1 :])
                if rebuilt.is_file():
                    return rebuilt
                for ext in SUPPORTED_IMAGE_EXTENSIONS:
                    alt = rebuilt.with_suffix(ext)
                    if alt.is_file():
                        return alt

    return norm


def resolve_folder_path(folder_like: str | Path) -> Path:
    """Resolve a manga folder; if a `-translated` output folder is passed, map back to source folder if it has `manga_translator_work`."""
    norm = Path(os.path.normpath(os.path.abspath(str(Path(folder_like).expanduser()))))
    if norm.is_file():
        return resolve_source_image_path(norm).parent
    if norm.name == WORK_DIR_NAME:
        return norm.parent
    if norm.parent.name == WORK_DIR_NAME:
        return norm.parent.parent
    if norm.name.endswith("-translated") and not (norm / WORK_DIR_NAME).is_dir():
        raw_sibling = norm.parent / norm.name[: -len("-translated")]
        if raw_sibling.is_dir() and (raw_sibling / WORK_DIR_NAME).is_dir():
            return raw_sibling
    return norm


@dataclass(frozen=True)
class PagePaths:
    """All canonical and discovered artifact paths for a single manga page."""

    source_path: Path
    display_base_path: Path
    work_dir: Path
    json_path: Path
    json_exists: bool
    inpainted_path: Path
    inpainted_exists: bool
    editor_base_path: Path
    editor_base_valid: bool
    paint_overlay_path: Path
    paint_overlay_exists: bool
    rendered_output_path: Path
    rendered_output_exists: bool
    inspect_dir: Path
    snapshots_dir: Path


def resolve_page_paths(
    image_or_json_path: str | Path,
    explicit_output_dir: Optional[str | Path] = None,
) -> PagePaths:
    """Compute all work and output paths for a manga page, honoring MTUI's exact rules."""
    source_path = resolve_source_image_path(image_or_json_path)
    src_dir = source_path.parent
    stem = source_path.stem
    ext = source_path.suffix or ".png"

    work_dir = src_dir / WORK_DIR_NAME
    new_json_path = work_dir / JSON_SUBDIR / f"{stem}_translations.json"
    legacy_json_path = src_dir / f"{stem}_translations.json"
    if new_json_path.is_file():
        json_path = new_json_path
        json_exists = True
    elif legacy_json_path.is_file():
        json_path = legacy_json_path
        json_exists = True
    else:
        json_path = new_json_path
        json_exists = False

    image_meta: dict[str, Any] = {}
    if json_exists:
        raw_json = _read_json_quiet(json_path)
        if raw_json:
            _, image_meta = _extract_first_image_entry(raw_json, source_path)

    # Check editor_base validity (valid ONLY when upscale_ratio or colorizer was active)
    editor_base_path = work_dir / EDITOR_BASE_SUBDIR / source_path.name
    legacy_root_base = work_dir / source_path.name
    actual_editor_base = (
        editor_base_path
        if editor_base_path.is_file()
        else (legacy_root_base if legacy_root_base.is_file() else editor_base_path)
    )
    has_upscale = bool(image_meta.get("upscale_ratio"))
    colorizer_val = image_meta.get("colorizer")
    has_colorizer = bool(colorizer_val) and str(colorizer_val).lower() != "none"
    editor_base_valid = actual_editor_base.is_file() and (has_upscale or has_colorizer)
    display_base_path = actual_editor_base if editor_base_valid else source_path

    # Inpainted sidecar
    inpainted_path = work_dir / INPAINTED_SUBDIR / f"{stem}_inpainted{ext}"
    inpainted_exists = inpainted_path.is_file()
    if not inpainted_exists and (work_dir / INPAINTED_SUBDIR).is_dir():
        for alt_ext in (".png", ".jpg", ".webp", *SUPPORTED_IMAGE_EXTENSIONS):
            alt_inpaint = work_dir / INPAINTED_SUBDIR / f"{stem}_inpainted{alt_ext}"
            if alt_inpaint.is_file():
                inpainted_path = alt_inpaint
                inpainted_exists = True
                break

    paint_overlay_path = work_dir / PAINT_OVERLAY_SUBDIR / f"{stem}_overlay.png"
    paint_overlay_exists = paint_overlay_path.is_file()

    # Resolve rendered output path (matching ExportService.build_output_path + CLI conventions)
    mtui_cfg = get_runtime_settings().load_mtui_config_dict()
    cli_cfg = mtui_cfg.get("cli", {}) if isinstance(mtui_cfg.get("cli"), dict) else {}
    app_cfg = mtui_cfg.get("app", {}) if isinstance(mtui_cfg.get("app"), dict) else {}

    cfg_fmt = str(cli_cfg.get("format") or "").strip().lower()
    out_ext = f".{cfg_fmt}" if (cfg_fmt and cfg_fmt != "不指定") else ext

    rendered_output_path: Optional[Path] = None
    if explicit_output_dir:
        out_dir = Path(os.path.normpath(os.path.abspath(str(explicit_output_dir))))
        rendered_output_path = out_dir / f"{stem}{out_ext}"
    else:
        saved_last_output = image_meta.get("last_output_path")
        saved_last_output_dir: Optional[Path] = None
        if isinstance(saved_last_output, str) and saved_last_output.strip():
            cand_last_out = Path(os.path.normpath(os.path.abspath(saved_last_output.strip())))
            if (
                cand_last_out.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                and os.path.normcase(str(cand_last_out)) != os.path.normcase(str(source_path))
            ):
                saved_last_output_dir = cand_last_out.parent
                if cand_last_out.is_file():
                    rendered_output_path = cand_last_out

        saved_export_dir = image_meta.get("last_export_dir")
        candidate_dirs: list[Path] = []
        if saved_last_output_dir is not None:
            candidate_dirs.append(saved_last_output_dir)
        if isinstance(saved_export_dir, str) and saved_export_dir.strip():
            candidate_dirs.append(Path(os.path.normpath(saved_export_dir.strip())))
        if cli_cfg.get("save_to_source_dir", False):
            candidate_dirs.append(work_dir / RESULT_SUBDIR)
        sibling_translated_dir = src_dir.parent / f"{src_dir.name}-translated"
        candidate_dirs.append(sibling_translated_dir)
        candidate_dirs.append(work_dir / RESULT_SUBDIR)
        last_out = app_cfg.get("last_output_path")
        if isinstance(last_out, str) and last_out.strip() and os.path.isdir(last_out.strip()):
            candidate_dirs.append(Path(os.path.normpath(last_out.strip())))

        # Prefer an existing rendered output file first
        if rendered_output_path is None:
            for cdir in candidate_dirs:
                for test_ext in (out_ext, ext, ".png", ".jpg", ".webp"):
                    cand = cdir / f"{stem}{test_ext}"
                    if cand.is_file() and os.path.normcase(str(cand)) != os.path.normcase(str(source_path)):
                        rendered_output_path = cand
                        break
                if rendered_output_path is not None:
                    break

        # Also check single-file CLI convention `<stem>-translated.<ext>` in same folder
        if rendered_output_path is None:
            for test_ext in (out_ext, ext, ".png", ".jpg", ".webp"):
                cand_inline = src_dir / f"{stem}-translated{test_ext}"
                if cand_inline.is_file():
                    rendered_output_path = cand_inline
                    break

        # If not yet rendered, pick canonical target location matching ExportService
        if rendered_output_path is None:
            if isinstance(saved_export_dir, str) and saved_export_dir.strip():
                target_dir = Path(os.path.normpath(saved_export_dir.strip()))
            elif saved_last_output_dir is not None:
                target_dir = saved_last_output_dir
            elif cli_cfg.get("save_to_source_dir", False):
                target_dir = work_dir / RESULT_SUBDIR
            elif isinstance(last_out, str) and last_out.strip() and os.path.isdir(last_out.strip()):
                target_dir = Path(os.path.normpath(last_out.strip()))
            else:
                target_dir = sibling_translated_dir
            rendered_output_path = target_dir / f"{stem}{out_ext}"

    return PagePaths(
        source_path=source_path,
        display_base_path=display_base_path,
        work_dir=work_dir,
        json_path=json_path,
        json_exists=json_exists,
        inpainted_path=inpainted_path,
        inpainted_exists=inpainted_exists,
        editor_base_path=actual_editor_base,
        editor_base_valid=editor_base_valid,
        paint_overlay_path=paint_overlay_path,
        paint_overlay_exists=paint_overlay_exists,
        rendered_output_path=rendered_output_path,
        rendered_output_exists=rendered_output_path.is_file(),
        inspect_dir=work_dir / MCP_INSPECT_SUBDIR,
        snapshots_dir=work_dir / MCP_SNAPSHOTS_SUBDIR,
    )


def list_manga_pages_in_path(target_path: str | Path, recursive: bool = False) -> list[PagePaths]:
    """List all manga pages in a folder (or single file) in natural reading order, skipping work dirs."""
    p = Path(target_path).expanduser()
    if p.is_file():
        return [resolve_page_paths(p)]

    folder = resolve_folder_path(p)
    if not folder.is_dir():
        return []

    found_images: list[Path] = []
    skip_dirs = {WORK_DIR_NAME, MCP_INSPECT_SUBDIR, MCP_SNAPSHOTS_SUBDIR, "backups", ".git"}

    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted([d for d in dirs if d not in skip_dirs], key=natural_sort_key)
            root_path = Path(root)
            page_files = [
                root_path / f
                for f in files
                if Path(f).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                and not Path(f).stem.endswith("-translated")
            ]
            page_files.sort(key=natural_sort_key)
            found_images.extend(page_files)
    else:
        page_files = [
            entry
            for entry in folder.iterdir()
            if entry.is_file()
            and entry.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            and not entry.stem.endswith("-translated")
        ]
        page_files.sort(key=natural_sort_key)
        found_images.extend(page_files)

    resolved_pages: list[PagePaths] = []
    seen_sources: set[str] = set()
    for img in found_images:
        page_paths = resolve_page_paths(img)
        norm_src = os.path.normcase(os.path.normpath(os.path.abspath(str(page_paths.source_path))))
        if norm_src in seen_sources:
            continue
        seen_sources.add(norm_src)
        resolved_pages.append(page_paths)
    return resolved_pages
