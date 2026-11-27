"""Glossary (`dict/*.yaml`, `mit_glossary.txt`, `post_dict.txt`) & Chapter Batch-Edit engine."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import get_runtime_settings
from .path_resolver import PagePaths, list_manga_pages_in_path
from .project_store import (
    _atomic_replace_with_retry,
    format_region_summary,
    load_page_json,
    resolve_region_colors_hex,
    save_page_json_atomic,
    scale_region_local_frames_for_font_change,
)
from .richtext_builder import (
    apply_span_styles_to_region,
    sync_region_text_update,
    visible_text_of_region,
)

GLOSSARY_CATEGORIES = ("Person", "Location", "Org", "Item", "Skill", "Creature")


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
        _atomic_replace_with_retry(tmp_name, path)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


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
        lines = target_path.read_text(encoding="utf-8").splitlines() if target_path.is_file() else []
        parsed_pairs: list[dict[str, str]] = []
        for ln in lines:
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("//"):
                continue
            parts = re.split(r"\t+|\s{2,}|\s+", s, maxsplit=1)
            if len(parts) == 2:
                parsed_pairs.append({"original": parts[0], "translation": parts[1]})
        if action == "list":
            return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs), "entries": parsed_pairs}
        if action == "upsert" and entries:
            existing_map = {p["original"]: idx for idx, p in enumerate(parsed_pairs)}
            for item in entries:
                orig = str(item.get("original", "")).strip()
                trans = str(item.get("translation", "")).strip()
                if not orig or not trans:
                    continue
                if orig in existing_map:
                    parsed_pairs[existing_map[orig]]["translation"] = trans
                else:
                    existing_map[orig] = len(parsed_pairs)
                    parsed_pairs.append({"original": orig, "translation": trans})
            out_txt = "\n".join(f"{p['original']}\t{p['translation']}" for p in parsed_pairs) + "\n"
            target_path.write_text(out_txt, encoding="utf-8")
            return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs), "entries": parsed_pairs}
        if action == "delete" and entries:
            to_del = {str(item.get("original", "")).strip() for item in entries}
            parsed_pairs = [p for p in parsed_pairs if p["original"] not in to_del]
            out_txt = "\n".join(f"{p['original']}\t{p['translation']}" for p in parsed_pairs) + "\n"
            target_path.write_text(out_txt, encoding="utf-8")
            return {"file": str(target_path), "format": "txt", "count": len(parsed_pairs)}

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
            orig = str(item.get("original", "")).strip()
            trans = str(item.get("translation", "")).strip()
            desc = str(item.get("description", "")).strip()
            cond = str(item.get("condition", "")).strip()
            if not orig or not trans:
                continue
            tr_obj: dict[str, Any] = {"text": trans}
            if cond:
                tr_obj["condition"] = cond
            new_record: dict[str, Any] = {
                "original": orig,
                "aliases": [
                    {
                        "original": orig,
                        "translations": [tr_obj],
                    }
                ],
                "overwrite": bool(item.get("overwrite", False)),
            }
            if desc:
                new_record["description"] = desc
            matched_idx = next(
                (i for i, existing in enumerate(cat_list) if str(existing.get("original", "")).strip() == orig),
                None,
            )
            if matched_idx is not None:
                cat_list[matched_idx].update(new_record)
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
    pages = list_manga_pages_in_path(target_path)
    modified_page_paths: list[PagePaths] = []
    hits: list[dict[str, Any]] = []

    for paths in pages:
        if not paths.json_exists:
            continue
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])
        page_changed = False

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
                else:
                    after_vis = before_vis
            else:
                after_vis = before_vis

            if set_fields:
                for k, v in set_fields.items():
                    if k == "font_size":
                        old_fs = int(round(float(reg.get("font_size") or 16)))
                        new_fs = max(1, int(round(float(v))))
                        reg["font_size"] = new_fs
                        scale_region_local_frames_for_font_change(reg, old_fs, new_fs)
                    elif k == "font_size_delta":
                        old_fs = int(round(float(reg.get("font_size") or 16)))
                        new_fs = max(1, old_fs + int(round(float(v))))
                        reg["font_size"] = new_fs
                        scale_region_local_frames_for_font_change(reg, old_fs, new_fs)
                    elif k == "font_family":
                        reg["font_family"] = str(v).strip()
                    elif k == "direction":
                        reg["direction"] = {"h": "horizontal", "v": "vertical"}.get(str(v).lower(), str(v).lower())
                    elif k == "alignment":
                        reg["alignment"] = str(v).lower()
                    elif k == "font_color":
                        from .richtext_builder import normalize_hex_color

                        hx = normalize_hex_color(v)
                        if hx:
                            reg["font_color"] = hx
                            reg.pop("fg_colors", None)
                            reg.pop("fg_color", None)
                    elif k == "stroke_color":
                        from .richtext_builder import color_to_rgb_list

                        rgb = color_to_rgb_list(v)
                        if rgb:
                            reg["bg_color"] = rgb
                            reg.pop("bg_colors", None)
                            reg.pop("text_stroke_color", None)
                    elif k == "stroke_width":
                        sw = max(0.0, float(v))
                        reg["stroke_width"] = sw
                        reg["default_stroke_width"] = sw
                    elif k == "line_spacing":
                        reg["line_spacing"] = max(0.1, float(v))
                    elif k == "letter_spacing":
                        reg["letter_spacing"] = max(0.1, float(v))
                    reg_changed = True

            if rich_style or rich_spans:
                apply_span_styles_to_region(reg, whole_style=rich_style, spans=rich_spans, reset_existing=False)
                reg_changed = True

            if reg_changed:
                page_changed = True
                hits.append(
                    {
                        "page_name": paths.source_path.name,
                        "region_index": r_idx,
                        "before_translation": before_vis,
                        "after": format_region_summary(r_idx, reg),
                    }
                )

        if page_changed and not dry_run:
            saved_json = save_page_json_atomic(paths, image_key, image_data)
            from .path_resolver import resolve_page_paths

            modified_page_paths.append(resolve_page_paths(saved_json))
        elif page_changed and dry_run:
            modified_page_paths.append(paths)

    return modified_page_paths, {
        "dry_run": dry_run,
        "scanned_pages": len(pages),
        "modified_pages": len(modified_page_paths),
        "matched_regions": len(hits),
        "changes": hits[:120],
    }
