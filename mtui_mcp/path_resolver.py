"""Bi-directional path resolution for manga-translator-ui projects and artifacts."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

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

_INPAINTED_FALLBACK_EXT_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys((".png", ".jpg", ".webp", *sorted(SUPPORTED_IMAGE_EXTENSIONS)))
)
_INPAINTED_FALLBACK_EXT_RANK: dict[str, int] = {
    ext_name: idx for idx, ext_name in enumerate(_INPAINTED_FALLBACK_EXT_ORDER)
}


def natural_sort_key(path: str | Path) -> list[tuple[bool, int | str]]:
    """Natural sort key matching manga-translator-ui's FileService._natural_sort_key."""
    normalized = str(path).replace("\\", "/")
    parts: list[tuple[bool, int | str]] = []
    for part in re.split(r"(\d+)", normalized):
        if part.isdecimal():
            try:
                parts.append((False, int(part)))
                continue
            except ValueError:
                pass
        if part:
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


def _find_unique_image_by_stem(
    directory: Path,
    stem: str,
    *,
    preferred_ext: Optional[str] = None,
    exclude_path: Optional[Path] = None,
) -> Optional[Path]:
    """Return ``directory / f'{stem}{preferred_ext}'`` when present, else the unique stem match across image extensions."""
    if not directory.is_dir():
        return None
    excl_norm = (
        os.path.normcase(os.path.normpath(os.path.abspath(str(exclude_path))))
        if exclude_path is not None
        else None
    )
    if preferred_ext and preferred_ext.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        same = directory / f"{stem}{preferred_ext}"
        if same.is_file():
            same_norm = os.path.normcase(os.path.normpath(os.path.abspath(str(same))))
            if same_norm != excl_norm:
                return same

    stem_cf = os.path.normcase(stem)
    matches: list[Path] = []
    seen_norms: set[str] = set()
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        if os.path.normcase(entry.stem) != stem_cf:
            continue
        enorm = os.path.normcase(os.path.normpath(os.path.abspath(str(entry))))
        if enorm == excl_norm or enorm in seen_norms:
            continue
        seen_norms.add(enorm)
        matches.append(entry)
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_source_image_path(
    path_like: str | Path,
    *,
    _preloaded_json_dict: Optional[dict[str, Any]] = None,
) -> Path:
    """Resolve any page reference (source image, work JSON, inpainted, editor_base, or translated output) to the canonical source image path."""
    raw = Path(path_like).expanduser()
    try:
        norm = Path(os.path.normpath(os.path.abspath(str(raw))))
    except Exception:
        norm = raw

    # 1. Passed a *_translations.json path directly
    if norm.suffix.lower() == ".json" and norm.name.endswith("_translations.json"):
        data = _preloaded_json_dict if _preloaded_json_dict is not None else _read_json_quiet(norm)
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
        found_from_json = _find_unique_image_by_stem(src_dir, stem)
        if found_from_json is not None:
            return found_from_json
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
        found_inp = _find_unique_image_by_stem(src_dir, stem, preferred_ext=norm.suffix)
        if found_inp is not None:
            return found_inp
    if parent.name == RESULT_SUBDIR and grandparent.name == WORK_DIR_NAME:
        src_dir = grandparent.parent
        stem = norm.stem
        if stem.endswith("-translated"):
            stem = stem[: -len("-translated")]
        found_res = _find_unique_image_by_stem(src_dir, stem, preferred_ext=norm.suffix)
        if found_res is not None:
            return found_res

    # 4. Passed a rendered output image inside `<folder>-translated/<name>` or `<stem>-translated.<ext>`
    #    when no manga_translator_work exists next to `norm` itself.
    own_work_json = norm.parent / WORK_DIR_NAME / JSON_SUBDIR / f"{norm.stem}_translations.json"
    if not own_work_json.is_file():
        if norm.stem.endswith("-translated"):
            orig_stem = norm.stem[: -len("-translated")]
            found_inline = _find_unique_image_by_stem(
                norm.parent,
                orig_stem,
                preferred_ext=norm.suffix,
                exclude_path=norm,
            )
            if found_inline is not None:
                return found_inline
        # Walk ancestor directories to see if an ancestor ends with `-translated`
        parts = list(norm.parts)
        for idx in range(len(parts) - 2, 0, -1):
            folder_name = parts[idx]
            if folder_name.endswith("-translated"):
                raw_folder = folder_name[: -len("-translated")]
                rebuilt = Path(*parts[:idx], raw_folder, *parts[idx + 1 :])
                found_sib = _find_unique_image_by_stem(
                    rebuilt.parent,
                    rebuilt.stem,
                    preferred_ext=rebuilt.suffix,
                    exclude_path=norm,
                )
                if found_sib is not None:
                    return found_sib

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


def _dir_norm_key(dir_like: Any) -> str:
    if isinstance(dir_like, (str, os.PathLike)):
        return os.path.normcase(os.path.abspath(os.fspath(dir_like)))
    return f"obj:{id(dir_like)}"


@dataclass(frozen=True)
class _SourceDirIndex:
    dir_key: str
    enumeration_ok: bool
    by_norm_stem: dict[str, tuple[Any, ...]]
    unverifiable_stems: frozenset[str] = frozenset()


def _build_src_dir_index(
    src_dir: Any,
    *,
    entries: Optional[list[Any]] = None,
) -> _SourceDirIndex:
    d_key = _dir_norm_key(src_dir)
    if entries is None:
        try:
            raw_entries = list(src_dir.iterdir())
        except OSError:
            return _SourceDirIndex(dir_key=d_key, enumeration_ok=False, by_norm_stem={})
    else:
        raw_entries = entries

    buckets: dict[str, list[Any]] = {}
    bad_stems: set[str] = set()
    for src_entry in raw_entries:
        src_suffix = str(getattr(src_entry, "suffix", ""))
        if src_suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        src_stem = str(getattr(src_entry, "stem", ""))
        norm_stem = os.path.normcase(src_stem)
        try:
            if not src_entry.is_file():
                continue
        except OSError:
            bad_stems.add(norm_stem)
            continue
        buckets.setdefault(norm_stem, []).append(src_entry)

    return _SourceDirIndex(
        dir_key=d_key,
        enumeration_ok=True,
        by_norm_stem={k: tuple(v) for k, v in buckets.items()},
        unverifiable_stems=frozenset(bad_stems),
    )


def _is_same_source_entry(src_dir: Path, req_name: str, other_entry: Any) -> bool:
    other_name = str(getattr(other_entry, "name", ""))
    if other_name == req_name:
        return True
    if os.path.normcase(other_name) != os.path.normcase(req_name):
        return False
    try:
        return os.path.samefile(src_dir / req_name, other_entry)
    except (OSError, TypeError, ValueError):
        return False


def _check_inpainted_sidecar_ownership(
    inpainted_entry: Any,
    src_dir: Optional[Path],
    req_stem: str,
    req_ext: str,
    *,
    src_entries: Optional[list[Any]] = None,
    src_dir_index: Optional[_SourceDirIndex] = None,
) -> Literal["owned", "conflict", "unverifiable"]:
    """Classify sidecar ownership for ``(req_stem, req_ext)`` as ``'owned'``, ``'conflict'``, or ``'unverifiable'``."""
    cand_stem = str(getattr(inpainted_entry, "stem", ""))
    if not cand_stem.lower().endswith("_inpainted"):
        return "conflict"
    cand_src_stem = cand_stem[: -len("_inpainted")]
    cand_ext = str(getattr(inpainted_entry, "suffix", ""))
    req_name = f"{req_stem}{req_ext}"
    if f"{cand_src_stem}{cand_ext}" == req_name:
        return "owned"
    if src_dir is None:
        return "owned"

    idx = src_dir_index
    if idx is None or idx.dir_key != _dir_norm_key(src_dir):
        idx = _build_src_dir_index(src_dir, entries=src_entries)
    if not idx.enumeration_ok:
        return "unverifiable"

    target_norm_stem = os.path.normcase(req_stem)
    cand_norm_stem = os.path.normcase(cand_src_stem)
    if target_norm_stem in idx.unverifiable_stems or cand_norm_stem in idx.unverifiable_stems:
        return "unverifiable"

    same_stem_entries = list(idx.by_norm_stem.get(target_norm_stem, ()))
    if cand_norm_stem != target_norm_stem:
        same_stem_entries.extend(idx.by_norm_stem.get(cand_norm_stem, ()))

    other_same_stem_pages = [
        src_entry
        for src_entry in same_stem_entries
        if not _is_same_source_entry(src_dir, req_name, src_entry)
    ]
    if not other_same_stem_pages:
        return "owned"

    # Multiple same-stem source pages exist in src_dir: only accept candidates uniquely matching (req_stem, req_ext).
    if cand_src_stem != req_stem or cand_ext.lower() != req_ext.lower():
        return "conflict"
    for other_entry in other_same_stem_pages:
        other_suffix = str(getattr(other_entry, "suffix", ""))
        if other_suffix.lower() == req_ext.lower():
            return "conflict"
    return "owned"


def _inpainted_belongs_to_other_source_page(
    inpainted_entry: Any,
    src_dir: Optional[Path],
    req_stem: str,
    req_ext: str,
    *,
    src_entries: Optional[list[Any]] = None,
    src_dir_index: Optional[_SourceDirIndex] = None,
) -> bool:
    """Return True if ``inpainted_entry`` is not uniquely owned by ``(req_stem, req_ext)`` in ``src_dir``."""
    cand_stem = str(getattr(inpainted_entry, "stem", ""))
    if not cand_stem.lower().endswith("_inpainted"):
        return False
    return (
        _check_inpainted_sidecar_ownership(
            inpainted_entry,
            src_dir,
            req_stem,
            req_ext,
            src_entries=src_entries,
            src_dir_index=src_dir_index,
        )
        != "owned"
    )


def _resolve_inpainted_sidecar(
    inpainted_dir: Path,
    stem: str,
    ext: str,
    src_dir: Optional[Path] = None,
    *,
    src_dir_index: Optional[_SourceDirIndex] = None,
) -> tuple[Path, bool]:
    """Resolve `<stem>_inpainted<ext>`, keeping exact primary match first then verified unambiguous fallback."""
    primary = inpainted_dir / f"{stem}_inpainted{ext}"
    if primary.is_file():
        return primary, True
    if not inpainted_dir.is_dir():
        return primary, False

    expected_stem = f"{stem}_inpainted"
    candidates: list[Path] = []
    cached_src_index: Optional[_SourceDirIndex] = (
        src_dir_index
        if (src_dir is not None and src_dir_index is not None and src_dir_index.dir_key == _dir_norm_key(src_dir))
        else None
    )
    try:
        for entry in inpainted_dir.iterdir():
            if not entry.is_file():
                continue
            ext_low = entry.suffix.lower()
            if ext_low not in _INPAINTED_FALLBACK_EXT_RANK:
                continue
            if entry.stem == expected_stem or os.path.normcase(entry.stem) == os.path.normcase(expected_stem):
                if src_dir is not None:
                    if cached_src_index is None:
                        cached_src_index = _build_src_dir_index(src_dir)
                    if (
                        _check_inpainted_sidecar_ownership(
                            entry,
                            src_dir,
                            stem,
                            ext,
                            src_dir_index=cached_src_index,
                        )
                        != "owned"
                    ):
                        continue
                candidates.append(entry)
    except OSError:
        candidates = []

    if candidates:
        candidates.sort(
            key=lambda p: (
                0 if p.stem == expected_stem else 1,
                _INPAINTED_FALLBACK_EXT_RANK[p.suffix.lower()],
                0 if p.suffix == p.suffix.lower() else 1,
                p.name,
            )
        )
        return candidates[0], True

    return primary, False


def _coerce_preloaded_json_dict(raw_val: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw_val, dict):
        return raw_val
    if isinstance(raw_val, (bytes, bytearray, memoryview)):
        try:
            loaded = json.loads(bytes(raw_val).decode("utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except Exception:
            return None
    if isinstance(raw_val, str):
        try:
            loaded = json.loads(raw_val)
            return loaded if isinstance(loaded, dict) else None
        except Exception:
            return None
    return None


def resolve_page_paths(
    image_or_json_path: str | Path,
    explicit_output_dir: Optional[str | Path] = None,
    *,
    preloaded_raw_json: Optional[Any] = None,
    _src_dir_index: Optional[_SourceDirIndex] = None,
) -> PagePaths:
    """Compute all work and output paths for a manga page, honoring MTUI's exact rules."""
    preloaded_dict = _coerce_preloaded_json_dict(preloaded_raw_json) if preloaded_raw_json is not None else None
    source_path = resolve_source_image_path(image_or_json_path, _preloaded_json_dict=preloaded_dict)
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
    if preloaded_raw_json is not None:
        if preloaded_dict:
            _, image_meta = _extract_first_image_entry(preloaded_dict, source_path)
    elif json_exists:
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
    inpainted_path, inpainted_exists = _resolve_inpainted_sidecar(
        work_dir / INPAINTED_SUBDIR,
        stem,
        ext,
        src_dir=src_dir,
        src_dir_index=_src_dir_index,
    )

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
    src_dir_indices: dict[str, _SourceDirIndex] = {}
    skip_dirs = {WORK_DIR_NAME, MCP_INSPECT_SUBDIR, MCP_SNAPSHOTS_SUBDIR, "backups", ".git"}

    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted([d for d in dirs if d not in skip_dirs], key=natural_sort_key)
            root_path = Path(root)
            img_candidates = [
                root_path / f
                for f in files
                if Path(f).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            ]
            dir_idx = _build_src_dir_index(root_path, entries=img_candidates)
            src_dir_indices[dir_idx.dir_key] = dir_idx
            page_files = [
                entry
                for bucket in dir_idx.by_norm_stem.values()
                for entry in bucket
                if not entry.stem.endswith("-translated")
            ]
            page_files.sort(key=natural_sort_key)
            found_images.extend(page_files)
    else:
        folder_entries = list(folder.iterdir())
        dir_idx = _build_src_dir_index(folder, entries=folder_entries)
        src_dir_indices[dir_idx.dir_key] = dir_idx
        page_files = [
            entry
            for bucket in dir_idx.by_norm_stem.values()
            for entry in bucket
            if not entry.stem.endswith("-translated")
        ]
        page_files.sort(key=natural_sort_key)
        found_images.extend(page_files)

    resolved_pages: list[PagePaths] = []
    seen_sources: set[str] = set()
    for img in found_images:
        src_p = resolve_source_image_path(img)
        norm_src = os.path.normcase(os.path.normpath(os.path.abspath(str(src_p))))
        if norm_src in seen_sources:
            continue
        seen_sources.add(norm_src)
        src_dir_key = _dir_norm_key(src_p.parent)
        src_idx = src_dir_indices.get(src_dir_key)
        if src_idx is None:
            src_idx = _build_src_dir_index(src_p.parent)
            src_dir_indices[src_dir_key] = src_idx
        page_paths = resolve_page_paths(src_p, _src_dir_index=src_idx)
        resolved_pages.append(page_paths)
    resolved_pages.sort(
        key=lambda p: (
            natural_sort_key(os.path.normpath(os.path.abspath(str(p.source_path.parent)))),
            natural_sort_key(p.source_path.name),
        )
    )
    return resolved_pages
