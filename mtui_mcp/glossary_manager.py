"""Glossary (`dict/*.yaml`, `mit_glossary.txt`, `post_dict.txt`) & Chapter Batch-Edit engine."""

from __future__ import annotations

import copy
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import get_runtime_settings
from .path_resolver import PagePaths, list_manga_pages_in_path, resolve_page_paths
from .project_store import (
    _atomic_replace_with_retry,
    ensure_region_uids,
    format_region_summary,
    load_page_json,
    page_edit_lock,
    resolve_region_colors_hex,
    resolve_region_target_index,
    save_page_json_atomic,
    scale_region_local_frames_for_font_change,
)
from .richtext_builder import (
    _merge_raw_style_patch,
    apply_span_styles_to_region,
    color_to_rgb_list,
    normalize_hex_color,
    region_is_bold,
    set_region_whole_bold,
    sync_region_text_update,
    visible_text_of_region,
)

GLOSSARY_CATEGORIES = ("Person", "Location", "Org", "Item", "Skill", "Creature")
ALLOWED_BATCH_SET_FIELDS = frozenset(
    {
        "font_size",
        "font_size_delta",
        "font_family",
        "bold",
        "direction",
        "alignment",
        "font_color",
        "stroke_color",
        "stroke_width",
        "disable_font_border",
        "line_spacing",
        "letter_spacing",
        "opacity",
        "angle",
    }
)


def resolve_glossary_path(glossary_file: Optional[str] = None) -> Path:
    """Resolve YAML/TXT glossary file from `MTUI_ROOT/dict/` or active `config.json`."""
    settings = get_runtime_settings()
    if glossary_file:
        p = Path(glossary_file).expanduser()
        if p.is_Absolute() if hasattr(p, "is_Absolute") else p.is_absolute():
            return p
        if settings.mtui_root is not None:
            cand_dict = settings.mtui_root / "dict" / glossary_file
            if cand_dict.exists() or not (settings.mtui_root / glossary_file).exists():
                return cand_dict
            return settings.mtui_root / glossary_file
        return p.resolve()

    mtui_cfg = settings.load_mtui_config_dict()
    hq_prompt = str((mtui_cfg.get("translator") or {}).get("high_quality_prompt_path") or "").strip()
    if hq_prompt and settings.mtui_root is not None:
        hp = Path(hq_prompt)
        resolved = hp if hp.is_absolute() else (settings.mtui_root / hp)
        if resolved.is_file():
            return resolved

    if settings.dict_dir is not None:
        return settings.dict_dir / "prompt_example.yaml"
    raise FileNotFoundError("Could not locate glossary file; pass glossary_file explicitly or configure MTUI_ROOT.")


def _load_yaml_prompt_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "system_prompt": "",
            "glossary": {cat: [] for cat in GLOSSARY_CATEGORIES},
        }
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("system_prompt", "")
    g = data.setdefault("glossary", {})
    if not isinstance(g, dict):
        g = {}
        data["glossary"] = g
    for cat in GLOSSARY_CATEGORIES:
        g.setdefault(cat, [])
    return data


def _save_yaml_prompt_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        _atomic_replace_with_retry(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _save_txt_glossary_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _atomic_replace_with_retry(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _parse_txt_glossary_line(line: str) -> Optional[tuple[str, str, str, str]]:
    """Return `(leading_ws, original, separator, translation)` for a valid TXT glossary entry line, else `None`."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return None
    lead_len = len(line) - len(line.lstrip())
    leading_ws = line[:lead_len]
    parts = re.split(r"(\t+|\s{2,}|\s+)", stripped, maxsplit=1)
    if len(parts) == 3 and parts[0] and parts[2]:
        return (leading_ws, parts[0], parts[1], parts[2])
    return None


def _extract_txt_pairs(lines: list[str]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    for ln in lines:
        parsed = _parse_txt_glossary_line(ln)
        if parsed is not None:
            _, orig, _, trans = parsed
            pairs.append({"original": orig, "translation": trans})
    return pairs


def manage_glossary_entries(
    action: str,  # "list" | "upsert" | "delete" | "set_system_prompt" | "list_files"
    *,
    glossary_file: Optional[str] = None,
    category: str = "Person",
    entries: Optional[list[dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Read or update `manga-translator-ui` YAML prompt/glossary files or TXT dictionaries."""
    settings = get_runtime_settings()
    if action == "list_files":
        files: list[str] = []
        if settings.dict_dir is not None and settings.dict_dir.is_dir():
            files = sorted(
                p.name
                for p in settings.dict_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".yaml", ".yml", ".txt", ".json")
            )
        return {"dict_dir": str(settings.dict_dir or ""), "files": files}

    target_path = resolve_glossary_path(glossary_file)

    # Support flat TXT dictionaries (`mit_glossary.txt`, `post_dict.txt`, `pre_dict.txt`)
    if target_path.suffix.lower() == ".txt":
        if action == "set_system_prompt":
            raise ValueError(
                f"Action 'set_system_prompt' is only supported for YAML glossary files, not TXT dictionary {target_path.name!r}"
            )
        if action not in ("list", "upsert", "delete"):
            raise ValueError(f"Unsupported glossary action: {action!r}")

        raw_lines = target_path.read_text(encoding="utf-8").splitlines() if target_path.is_file() else []
        if action == "list":
            parsed_pairs = _extract_txt_pairs(raw_lines)
            return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs), "entries": parsed_pairs}

        if action == "upsert":
            out_lines = list(raw_lines)
            # Map original -> (first_line_index, leading_ws, separator) and drop duplicate entry lines on update
            orig_locs: dict[str, tuple[int, str, str]] = {}
            extra_dup_indices: dict[str, list[int]] = {}
            for idx_ln, ln in enumerate(out_lines):
                parsed = _parse_txt_glossary_line(ln)
                if parsed is not None:
                    lead_ws, orig_tok, sep_tok, _ = parsed
                    if orig_tok not in orig_locs:
                        orig_locs[orig_tok] = (idx_ln, lead_ws, sep_tok)
                    else:
                        extra_dup_indices.setdefault(orig_tok, []).append(idx_ln)

            modified = False
            drop_line_indices: set[int] = set()
            for item in entries or []:
                if not isinstance(item, dict):
                    continue
                orig = str(item.get("original", "")).strip()
                trans = str(item.get("translation", "")).strip()
                if not orig or not trans:
                    continue
                if orig in orig_locs:
                    line_i, lead_ws, sep_tok = orig_locs[orig]
                    out_lines[line_i] = f"{lead_ws}{orig}{sep_tok}{trans}"
                    for dup_i in extra_dup_indices.get(orig, []):
                        drop_line_indices.add(dup_i)
                else:
                    orig_locs[orig] = (len(out_lines), "", "\t")
                    out_lines.append(f"{orig}\t{trans}")
                modified = True

            if drop_line_indices:
                out_lines = [ln for i, ln in enumerate(out_lines) if i not in drop_line_indices]
            if modified:
                out_txt = ("\n".join(out_lines) + "\n") if out_lines else ""
                _save_txt_glossary_atomic(target_path, out_txt)
            parsed_pairs = _extract_txt_pairs(out_lines)
            return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs), "entries": parsed_pairs}

        # action == "delete"
        to_del = {
            str(item.get("original", "")).strip()
            for item in (entries or [])
            if isinstance(item, dict) and str(item.get("original", "")).strip()
        }
        kept_lines: list[str] = []
        deleted_count = 0
        for ln in raw_lines:
            parsed = _parse_txt_glossary_line(ln)
            if parsed is not None and parsed[1] in to_del:
                deleted_count += 1
                continue
            kept_lines.append(ln)
        if deleted_count > 0:
            out_txt = ("\n".join(kept_lines) + "\n") if kept_lines else ""
            _save_txt_glossary_atomic(target_path, out_txt)
        parsed_pairs = _extract_txt_pairs(kept_lines)
        return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs), "deleted": deleted_count}

    # YAML HQ glossary format (`dict/*.yaml`)
    data = _load_yaml_prompt_file(target_path)
    if action == "list":
        flat_count = sum(len(v) for v in (data.get("glossary") or {}).values() if isinstance(v, list))
        return {
            "file": str(target_path),
            "format": "yaml",
            "system_prompt": data.get("system_prompt", ""),
            "total_entries": flat_count,
            "glossary": data.get("glossary", {}),
        }

    if action == "set_system_prompt":
        data["system_prompt"] = str(system_prompt or "")
        _save_yaml_prompt_atomic(target_path, data)
        return {"file": str(target_path), "system_prompt": data["system_prompt"]}

    cat = category if category in GLOSSARY_CATEGORIES else "Person"
    cat_list: list[dict[str, Any]] = data["glossary"].setdefault(cat, [])
    # Clean out template blank placeholder (`original: ""`)
    cat_list[:] = [e for e in cat_list if isinstance(e, dict) and str(e.get("original", "")).strip()]

    if action == "upsert":
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            orig = str(item.get("original", "")).strip()
            trans = str(item.get("translation", "")).strip()
            alias_orig = str(item.get("alias") or item.get("alias_original") or orig).strip()
            has_desc = "description" in item and item.get("description") is not None
            desc = str(item.get("description") or "").strip()
            cond = str(item.get("condition") or "").strip()
            has_overwrite = "overwrite" in item and item.get("overwrite") is not None
            replace_record = bool(item.get("replace", False))
            if not orig or not trans:
                continue
            tr_obj: dict[str, Any] = {"text": trans}
            if cond:
                tr_obj["condition"] = cond

            matched_idx = next(
                (i for i, existing in enumerate(cat_list) if str(existing.get("original", "")).strip() == orig),
                None,
            )
            if matched_idx is not None and not replace_record:
                existing_rec = cat_list[matched_idx]
                aliases = existing_rec.get("aliases")
                if not isinstance(aliases, list):
                    aliases = []
                    existing_rec["aliases"] = aliases
                alias_entry = next(
                    (
                        a
                        for a in aliases
                        if isinstance(a, dict) and str(a.get("original", "")).strip() == alias_orig
                    ),
                    None,
                )
                if alias_entry is None:
                    aliases.append({"original": alias_orig, "translations": [tr_obj]})
                else:
                    tr_list = alias_entry.get("translations")
                    if not isinstance(tr_list, list):
                        tr_list = []
                        alias_entry["translations"] = tr_list
                    cond_entry = next(
                        (
                            t
                            for t in tr_list
                            if isinstance(t, dict) and str(t.get("condition") or "").strip() == cond
                        ),
                        None,
                    )
                    if cond_entry is not None:
                        cond_entry["text"] = trans
                    else:
                        tr_list.append(tr_obj)

                if has_overwrite:
                    existing_rec["overwrite"] = bool(item["overwrite"])
                elif "overwrite" not in existing_rec:
                    existing_rec["overwrite"] = False

                if has_desc:
                    if desc:
                        existing_rec["description"] = desc
                    else:
                        existing_rec.pop("description", None)
            else:
                new_record: dict[str, Any] = {
                    "original": orig,
                    "aliases": [
                        {
                            "original": alias_orig,
                            "translations": [tr_obj],
                        }
                    ],
                    "overwrite": bool(item.get("overwrite", False)),
                }
                if desc:
                    new_record["description"] = desc
                if matched_idx is not None:
                    cat_list[matched_idx] = new_record
                else:
                    cat_list.append(new_record)
        _save_yaml_prompt_atomic(target_path, data)
        return {"file": str(target_path), "category": cat, "category_entries": cat_list}

    if action == "delete":
        targets = {str(item.get("original", "")).strip() for item in (entries or [])}
        before = len(cat_list)
        cat_list[:] = [e for e in cat_list if str(e.get("original", "")).strip() not in targets]
        _save_yaml_prompt_atomic(target_path, data)
        return {"file": str(target_path), "category": cat, "deleted": before - len(cat_list)}

    raise ValueError(f"Unsupported glossary action: {action!r}")


# ---------------------------------------------------------------------------
# Chapter-Wide Batch Search / Replace / Conditional Field & Style Engine
# ---------------------------------------------------------------------------


def _region_matches_filter(idx: int, reg: dict[str, Any], flt: Optional[dict[str, Any]]) -> bool:
    if not flt:
        return True
    vis = visible_text_of_region(reg)
    src = str(reg.get("text", "") or "")
    fs = int(round(float(reg.get("font_size") or 16)))
    dir_raw = str(reg.get("direction") or "auto").lower()
    dir_short = {"horizontal": "h", "vertical": "v", "hr": "h", "vr": "v"}.get(dir_raw, dir_raw)
    fg_hex, _ = resolve_region_colors_hex(reg)

    if flt.get("translation_contains") and str(flt["translation_contains"]) not in vis:
        return False
    if flt.get("translation_regex") and not re.search(str(flt["translation_regex"]), vis):
        return False
    if flt.get("source_contains") and str(flt["source_contains"]) not in src:
        return False
    if flt.get("source_regex") and not re.search(str(flt["source_regex"]), src):
        return False
    if flt.get("direction") and dir_short != str(flt["direction"]).lower():
        return False
    if flt.get("font_family") is not None and str(reg.get("font_family") or "") != str(flt["font_family"]):
        return False
    if flt.get("min_font_size") is not None and fs < int(flt["min_font_size"]):
        return False
    if flt.get("max_font_size") is not None and fs > int(flt["max_font_size"]):
        return False
    if flt.get("font_color") and fg_hex.lower() != str(flt["font_color"]).lower():
        return False
    if flt.get("region_indices") is not None and idx not in {int(x) for x in flt["region_indices"]}:
        return False
    return True


def _validate_batch_set_fields(set_fields: Optional[dict[str, Any]]) -> None:
    if set_fields is None:
        return
    if not isinstance(set_fields, dict):
        raise TypeError("set_fields must be a dictionary")
    unknown_keys = set(set_fields.keys()) - ALLOWED_BATCH_SET_FIELDS
    if unknown_keys:
        raise ValueError(
            f"Unknown batch set_fields key(s) {sorted(unknown_keys)}; "
            f"allowed fields: {sorted(ALLOWED_BATCH_SET_FIELDS)}"
        )
    for k, v in set_fields.items():
        if v is None:
            raise ValueError(f"set_fields[{k!r}] must not be None")
        if k in ("font_size", "font_size_delta", "stroke_width", "line_spacing", "letter_spacing", "opacity", "angle"):
            val_f = float(v)
            if not math.isfinite(val_f):
                raise ValueError(f"set_fields[{k!r}] must be finite")
        elif k == "direction":
            d = str(v).strip().lower()
            if d not in ("h", "v", "auto", "horizontal", "vertical"):
                raise ValueError(f"Invalid direction {d!r}; expected 'h', 'v', or 'auto'")
        elif k == "alignment":
            al = str(v).strip().lower()
            if al not in ("left", "center", "right", "auto"):
                raise ValueError(f"Invalid alignment {al!r}; expected 'left', 'center', 'right', or 'auto'")
        elif k == "font_color":
            if not normalize_hex_color(v):
                raise ValueError(f"Invalid font_color {v!r}")
        elif k == "stroke_color":
            if not color_to_rgb_list(v):
                raise ValueError(f"Invalid stroke_color {v!r}")


def _apply_batch_set_fields_to_region(reg: dict[str, Any], set_fields: dict[str, Any]) -> bool:
    changed = False
    for k, v in set_fields.items():
        if k == "font_size":
            old_fs = int(round(float(reg.get("font_size") or 16)))
            new_fs = max(1, int(round(float(v))))
            before_snap = copy.deepcopy(reg)
            reg["font_size"] = new_fs
            scale_region_local_frames_for_font_change(reg, old_fs, new_fs)
            if reg != before_snap:
                changed = True
        elif k == "font_size_delta":
            old_fs = int(round(float(reg.get("font_size") or 16)))
            new_fs = max(1, old_fs + int(round(float(v))))
            before_snap = copy.deepcopy(reg)
            reg["font_size"] = new_fs
            scale_region_local_frames_for_font_change(reg, old_fs, new_fs)
            if reg != before_snap:
                changed = True
        elif k == "font_family":
            new_ff = str(v).strip()
            if str(reg.get("font_family") or "") != new_ff:
                reg["font_family"] = new_ff
                changed = True
        elif k == "bold":
            want_bold = bool(v)
            if region_is_bold(reg) != want_bold:
                set_region_whole_bold(reg, want_bold)
                changed = True
        elif k == "direction":
            new_dir = {"h": "horizontal", "v": "vertical"}.get(str(v).strip().lower(), str(v).strip().lower())
            cur_raw = str(reg.get("direction") or "").strip().lower()
            cur_norm = {"h": "horizontal", "v": "vertical"}.get(cur_raw, cur_raw)
            if cur_norm != new_dir or reg.get("direction") != new_dir:
                reg["direction"] = new_dir
                changed = True
        elif k == "alignment":
            new_al = str(v).strip().lower()
            if str(reg.get("alignment") or "").strip().lower() != new_al or reg.get("alignment") != new_al:
                reg["alignment"] = new_al
                changed = True
        elif k == "font_color":
            hx = normalize_hex_color(v)
            if hx and (reg.get("font_color") != hx or "fg_colors" in reg or "fg_color" in reg):
                reg["font_color"] = hx
                reg.pop("fg_colors", None)
                reg.pop("fg_color", None)
                changed = True
        elif k == "stroke_color":
            rgb = color_to_rgb_list(v)
            if rgb and (reg.get("bg_color") != rgb or "bg_colors" in reg or "text_stroke_color" in reg):
                reg["bg_color"] = rgb
                reg.pop("bg_colors", None)
                reg.pop("text_stroke_color", None)
                changed = True
        elif k == "stroke_width":
            sw = max(0.0, float(v))
            if reg.get("stroke_width") != sw or reg.get("default_stroke_width") != sw:
                reg["stroke_width"] = sw
                reg["default_stroke_width"] = sw
                changed = True
        elif k == "disable_font_border":
            dfb = bool(v)
            if bool(reg.get("disable_font_border", False)) != dfb:
                reg["disable_font_border"] = dfb
                changed = True
        elif k == "line_spacing":
            ls = max(0.1, float(v))
            if reg.get("line_spacing") != ls:
                reg["line_spacing"] = ls
                changed = True
        elif k == "letter_spacing":
            lts = max(0.1, float(v))
            if reg.get("letter_spacing") != lts:
                reg["letter_spacing"] = lts
                changed = True
        elif k == "opacity":
            op = max(0.0, min(1.0, float(v)))
            if reg.get("opacity") != op:
                reg["opacity"] = op
                changed = True
        elif k == "angle":
            ang = float(v)
            if float(reg.get("angle", 0.0) or 0.0) != ang:
                reg["angle"] = ang
                changed = True
    return changed


def batch_edit_chapter_pages(
    target_path: str | Path,
    *,
    filter_criteria: Optional[dict[str, Any]] = None,
    find_text: Optional[str] = None,
    replace_text: Optional[str] = None,
    use_regex: bool = False,
    set_fields: Optional[dict[str, Any]] = None,
    rich_style: Optional[dict[str, Any]] = None,
    rich_spans: Optional[list[dict[str, Any]]] = None,
    dry_run: bool = False,
) -> tuple[list[PagePaths], dict[str, Any]]:
    """Batch-edit regions across a page or entire chapter folder (preserving `mask_raw` & rich-text styles!)."""
    # Validate all field names & rich-text style specs upfront before scanning any page
    _validate_batch_set_fields(set_fields)
    if rich_style is not None:
        _merge_raw_style_patch({}, rich_style)
    if find_text is not None and use_regex:
        re.compile(find_text)

    pages = list_manga_pages_in_path(target_path)
    modified_page_paths: list[PagePaths] = []
    hits: list[dict[str, Any]] = []

    def _process_single_page(paths: PagePaths) -> None:
        if not paths.json_exists:
            return
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])
        ensure_region_uids(regions)
        page_changed = False
        page_hits: list[tuple[int, str, str, dict[str, Any]]] = []

        for r_idx, reg in enumerate(regions):
            if not _region_matches_filter(r_idx, reg, filter_criteria):
                continue

            before_vis = visible_text_of_region(reg)
            reg_changed = False

            if find_text is not None:
                matched_find = (
                    bool(re.search(find_text, before_vis))
                    if use_regex
                    else (find_text in before_vis)
                )
                if not matched_find:
                    continue
                if replace_text is not None:
                    after_vis = (
                        re.sub(find_text, replace_text, before_vis)
                        if use_regex
                        else before_vis.replace(find_text, replace_text)
                    )
                    if after_vis != before_vis:
                        sync_region_text_update(reg, after_vis)
                        reg_changed = True

            if set_fields:
                if _apply_batch_set_fields_to_region(reg, set_fields):
                    reg_changed = True

            if rich_style or rich_spans:
                reg_before_rt = copy.deepcopy(reg)
                prev_rich = copy.deepcopy(reg.get("translation_rich"))
                prev_bold = region_is_bold(reg)
                prev_tr = reg.get("translation")
                apply_span_styles_to_region(reg, whole_style=rich_style, spans=rich_spans, reset_existing=False)
                if (
                    reg.get("translation_rich") != prev_rich
                    or region_is_bold(reg) != prev_bold
                    or reg.get("translation") != prev_tr
                ):
                    reg_changed = True
                else:
                    reg.clear()
                    reg.update(reg_before_rt)

            if reg_changed:
                page_changed = True
                uid = str(reg.get("region_uid") or "")
                page_hits.append((r_idx, uid, before_vis, reg))

        if page_changed and not dry_run:
            saved_json = save_page_json_atomic(paths, image_key, image_data)
            refreshed = resolve_page_paths(saved_json)
            _, saved_data = load_page_json(refreshed)
            saved_regs = saved_data.get("regions", [])
            modified_page_paths.append(refreshed)
            for r_idx, uid, before_vis, fallback_reg in page_hits:
                try:
                    s_idx = resolve_region_target_index(saved_regs, region_uid=uid) if uid else r_idx
                    after_summary = format_region_summary(s_idx, saved_regs[s_idx])
                except (KeyError, IndexError):
                    after_summary = format_region_summary(r_idx, fallback_reg)
                hits.append(
                    {
                        "page_name": paths.source_path.name,
                        "region_index": r_idx,
                        "before_translation": before_vis,
                        "after": after_summary,
                    }
                )
        elif page_changed and dry_run:
            modified_page_paths.append(paths)
            for r_idx, _uid, before_vis, reg in page_hits:
                hits.append(
                    {
                        "page_name": paths.source_path.name,
                        "region_index": r_idx,
                        "before_translation": before_vis,
                        "after": format_region_summary(r_idx, reg),
                    }
                )

    for p in pages:
        if not p.json_exists:
            continue
        if dry_run:
            _process_single_page(p)
        else:
            with page_edit_lock(p):
                _process_single_page(resolve_page_paths(p.source_path))

    return modified_page_paths, {
        "dry_run": dry_run,
        "scanned_pages": len(pages),
        "modified_pages": len(modified_page_paths),
        "matched_regions": len(hits),
        "changes": hits[:120],
    }
