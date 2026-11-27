"""Safe atomic project store for `*_translations.json`, snapshots, and region operations."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image

from .config import get_runtime_settings
from .font_catalog import check_missing_glyphs, check_region_missing_glyphs
from .path_resolver import PagePaths, list_manga_pages_in_path, resolve_page_paths
from .richtext_builder import (
    apply_missing_glyph_inline_fallbacks,
    apply_span_styles_to_region,
    color_to_rgb_list,
    document_has_nontrivial_styling,
    is_rich_text_document,
    normalize_hex_color,
    plain_to_storage_br,
    region_is_bold,
    sanitize_vertical_richtext_rotations,
    set_region_whole_bold,
    storage_br_to_plain,
    sync_region_text_update,
    visible_text_of_region,
)

logger = logging.getLogger("mtui_mcp.store")

_WINDOWS_TRANSIENT_REPLACE_WINERRORS = frozenset({5, 32})
_ATOMIC_REPLACE_RETRY_DELAYS: tuple[float, ...] = (0.02, 0.05, 0.1, 0.15, 0.2, 0.3)


def _atomic_replace_with_retry(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
    *,
    retry_delays: tuple[float, ...] = _ATOMIC_REPLACE_RETRY_DELAYS,
) -> None:
    """Atomically replace ``dst`` with ``src``, retrying briefly on Windows access/sharing locks."""
    for attempt, delay in enumerate((*retry_delays, None)):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            is_transient_windows_lock = os.name == "nt" and (
                isinstance(exc, PermissionError)
                or winerror in _WINDOWS_TRANSIENT_REPLACE_WINERRORS
            )
            if not is_transient_windows_lock or delay is None:
                raise
            logger.warning(
                "Transient lock replacing %s -> %s (attempt %d/%d): %s; retrying in %.2fs",
                src,
                dst,
                attempt + 1,
                len(retry_delays),
                exc,
                delay,
            )
            time.sleep(delay)


class _NumpySafeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def open_rgb_image(path: Path) -> np.ndarray:
    """Load an image file (Unicode-safe on Windows) into an RGB uint8 numpy array."""
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        return np.array(rgb, dtype=np.uint8, copy=True)


def save_rgb_image_atomic(arr_rgb: np.ndarray, target_path: Path, quality: int = 95) -> Path:
    """Atomically save an RGB uint8 numpy array to ``target_path``."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    pil_img = Image.fromarray(np.asarray(arr_rgb, dtype=np.uint8))
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=target_path.suffix or ".png",
        dir=str(target_path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        ext_low = target_path.suffix.lower()
        save_kwargs: dict[str, Any] = {}
        if ext_low in (".jpg", ".jpeg", ".jfif", ".webp", ".avif"):
            save_kwargs["quality"] = max(1, min(100, int(quality)))
        pil_img.save(tmp_path, **save_kwargs)
        _atomic_replace_with_retry(tmp_path, target_path)
        return target_path
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def decode_mask_base64(mask_raw: Any, expected_hw: Optional[tuple[int, int]] = None) -> Optional[np.ndarray]:
    """Decode `mask_raw` (base64 PNG or nested list) into a 2D binary `{0, 255}` uint8 array."""
    arr: Optional[np.ndarray] = None
    if isinstance(mask_raw, str) and mask_raw.strip():
        try:
            buf = np.frombuffer(base64.b64decode(mask_raw), dtype=np.uint8)
            decoded = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if decoded is not None:
                arr = decoded
        except Exception as exc:
            logger.warning("Failed to decode base64 mask_raw: %s", exc)
    elif isinstance(mask_raw, list) and mask_raw:
        arr = np.asarray(mask_raw, dtype=np.uint8)
    elif isinstance(mask_raw, np.ndarray):
        arr = mask_raw

    if arr is None:
        return None
    if arr.ndim == 3:
        arr = arr[..., 0]
    if arr.ndim != 2:
        return None
    binary = np.where(arr > 0, 255, 0).astype(np.uint8)
    if expected_hw is not None and binary.shape != expected_hw:
        binary = cv2.resize(binary, (expected_hw[1], expected_hw[0]), interpolation=cv2.INTER_NEAREST)
        binary = np.where(binary > 0, 255, 0).astype(np.uint8)
    return binary


def encode_mask_base64(mask: np.ndarray) -> str:
    binary = np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8)
    ok, encoded = cv2.imencode(".png", binary)
    if not ok:
        raise RuntimeError("Failed to encode mask PNG")
    return base64.b64encode(encoded).decode("ascii")


def compute_page_revision(paths: PagePaths) -> str:
    """Compute a deterministic optimistic-concurrency token (`rev_<16hex>`) over JSON + inpainted sidecar."""
    h = hashlib.sha256()
    if paths.json_exists and paths.json_path.is_file():
        try:
            h.update(paths.json_path.read_bytes())
        except OSError:
            pass
    if paths.inpainted_exists and paths.inpainted_path.is_file():
        try:
            st = paths.inpainted_path.stat()
            h.update(f"|inpaint:{st.st_size}:{st.st_mtime_ns}".encode("ascii"))
        except OSError:
            pass
    return f"rev_{h.hexdigest()[:16]}"


def verify_expected_revision(paths: PagePaths, expected_revision: Optional[str]) -> str:
    """Verify optimistic concurrency token when supplied; return current revision."""
    current = compute_page_revision(paths)
    if expected_revision and expected_revision.strip() and expected_revision.strip() != current:
        raise ValueError(
            f"Revision conflict on {paths.source_path.name}: expected {expected_revision!r}, "
            f"but current on-disk revision is {current!r}. Reload via mtui_get_page_details before editing."
        )
    return current


def ensure_region_uids(regions: list[dict[str, Any]]) -> bool:
    """Ensure every region carries a deterministic, stable `region_uid` immune to index shifts after delete/merge."""
    changed = False
    seen: set[str] = set()
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        uid = str(reg.get("region_uid") or "").strip()
        if not uid or uid in seen:
            seed_payload = json.dumps(
                [reg.get("text", ""), reg.get("center"), reg.get("lines")],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            digest = hashlib.sha256(seed_payload.encode("utf-8")).hexdigest()
            uid = f"reg_{digest[:8]}"
            salt = 1
            while uid in seen:
                uid = f"reg_{hashlib.sha256(f'{seed_payload}:{salt}'.encode('utf-8')).hexdigest()[:8]}"
                salt += 1
            reg["region_uid"] = uid
            changed = True
        seen.add(uid)
    return changed


def resolve_region_target_index(
    regions: list[dict[str, Any]],
    region_index: Optional[int] = None,
    region_uid: Optional[str] = None,
) -> int:
    """Resolve a region by stable `region_uid` (preferred when supplied) or 0-based `region_index`."""
    if region_uid:
        target_uid = str(region_uid).strip()
        for idx, reg in enumerate(regions):
            if isinstance(reg, dict) and str(reg.get("region_uid") or "") == target_uid:
                return idx
        raise KeyError(f"Unknown region_uid {target_uid!r} on this page")
    if region_index is None:
        raise ValueError("Either region_index or region_uid is required")
    idx = int(region_index)
    if not (0 <= idx < len(regions)):
        raise IndexError(f"region_index {idx} out of range (page has {len(regions)} regions: 0..{len(regions) - 1})")
    return idx


def _with_ensured_uids(data: dict[str, Any]) -> dict[str, Any]:
    regs = data.get("regions")
    if isinstance(regs, list):
        ensure_region_uids(regs)
    return data


def load_page_json(paths: PagePaths) -> tuple[str, dict[str, Any]]:
    """Load `(image_key, image_data_dict)` from `*_translations.json`, preserving all keys."""
    canonical_key = os.path.abspath(str(paths.source_path))
    if not paths.json_exists:
        return canonical_key, {"regions": []}

    with open(paths.json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict) or not raw:
        return canonical_key, {"regions": []}

    if canonical_key in raw:
        val = raw[canonical_key]
        out = copy.deepcopy(val) if isinstance(val, dict) else {"regions": copy.deepcopy(val)}
        return canonical_key, _with_ensured_uids(out)

    norm_target = os.path.normcase(canonical_key)
    for k, v in raw.items():
        try:
            if os.path.normcase(os.path.abspath(k)) == norm_target:
                out = copy.deepcopy(v) if isinstance(v, dict) else {"regions": copy.deepcopy(v)}
                return k, _with_ensured_uids(out)
        except Exception:
            continue

    first_k = next(iter(raw.keys()))
    first_v = raw[first_k]
    out = copy.deepcopy(first_v) if isinstance(first_v, dict) else {"regions": copy.deepcopy(first_v)}
    return first_k, _with_ensured_uids(out)


_VERT_CHAR_PAIRS: tuple[tuple[str, str], ...] = (
    ("...", "︙"),
    ("……", "︙"),
    ("…", "︙"),
    ("⋯", "︙"),
    ("‥", "︰"),
    ("─", "│"),
    ("━", "┃"),
    ("═", "║"),
    ("—", "│"),
    ("―", "│"),
    ("–", "︲"),
    ("（", "︵"),
    ("）", "︶"),
    ("(", "︵"),
    (")", "︶"),
    ("｛", "︷"),
    ("｝", "︸"),
    ("{", "︷"),
    ("}", "︸"),
    ("〔", "︹"),
    ("〕", "︺"),
    ("【", "︻"),
    ("】", "︼"),
    ("《", "︽"),
    ("》", "︾"),
    ("〈", "︿"),
    ("〉", "﹀"),
    ("「", "﹁"),
    ("」", "﹂"),
    ("『", "﹃"),
    ("』", "﹄"),
    ("〜", "︴"),
    ("〰", "︴"),
    ("～", "︴"),
    ("・", "·"),
    ("︐", "，"),
    ("︒", "。"),
)

_HORIZ_CHAR_PAIRS: tuple[tuple[str, str], ...] = (
    ("︙", "……"),
    ("︰", "‥"),
    ("││", "——"),
    ("︱︱", "——"),
    ("│", "—"),
    ("︱", "—"),
    ("┃", "━"),
    ("║", "═"),
    ("︲", "–"),
    ("︴", "～"),
    ("︵", "（"),
    ("︶", "）"),
    ("︷", "{"),
    ("︸", "}"),
    ("︹", "〔"),
    ("︺", "〕"),
    ("︻", "【"),
    ("︼", "】"),
    ("︽", "《"),
    ("︾", "》"),
    ("︿", "〈"),
    ("﹀", "〉"),
    ("﹁", "「"),
    ("﹂", "」"),
    ("﹃", "『"),
    ("﹄", "』"),
    ("・", "·"),
    ("︐", "，"),
    ("︒", "。"),
)


def normalize_text_for_direction(text: str, *, is_vertical: bool, font_family: str = "", has_custom_rotation: bool = False) -> str:
    if not text:
        return text
    out = text
    if is_vertical:
        ff_low = (font_family or "").casefold()
        use_fe31 = ("mf shanghei" in ff_low) or ("smiley sans" in ff_low)
        for src, dst in _VERT_CHAR_PAIRS:
            actual_dst = "︱" if (use_fe31 and dst == "│") else dst
            out = out.replace(src, actual_dst)
        if not has_custom_rotation and not use_fe31:
            out = out.replace("～", "︴")
    else:
        for src, dst in _HORIZ_CHAR_PAIRS:
            out = out.replace(src, dst)
    return out


def _pick_punctuation_fallback_font(font_family: str, ch: str) -> str:
    """Return a style-matched complete CJK fallback font for missing/zero-contour punctuation ``ch`` (empty if ``ch`` is alphanumeric/hanzi)."""
    if not ch or unicodedata.category(ch)[:1] not in ("P", "S"):
        return ""
    fl = (font_family or "").casefold()
    if any(k in fl for k in ("tankuin", "guyin", "fzcusong")) or ("serif" in fl and ("heavy" in fl or "bold" in fl)):
        cands = ("Source Han Serif CN::Heavy", "FZYaSong-DB-GBK", "FZHei-B01")
    elif any(k in fl for k in ("yasong", "soukou", "huiwen", "serif", "simsun", "song", "fangsong")):
        cands = ("FZYaSong-DB-GBK", "Source Han Serif CN::SemiBold", "FZHei-B01")
    elif any(k in fl for k in ("fzlantingyuan-b", "dffangyuan")) or ("rounded" in fl and "bold" in fl):
        cands = ("Resource Han Rounded CN::Bold", "FZZhunYuan-M02", "FZHei-B01")
    elif any(k in fl for k in ("wenkai", "zhunyuan", "lantingyuan", "rounded", "youyuan", "xiaolai", "zhiyi", "yozai")):
        cands = ("FZZhunYuan-M02", "Resource Han Rounded CN::Medium", "FZHei-B01")
    elif any(k in fl for k in ("lisu", "stliti", "lishu", "隶", "糸柳", "willow", "kai", "weibei", "cangsang", "shuti", "xing", "cao")):
        cands = ("FZBeiWeiKaiShu-S19", "FZYaSong-DB-GBK", "FZHei-B01")
    else:
        cands = ("Source Han Sans SC::Bold", "FZHei-B01", "FZZhunYuan-M02")

    for cand in cands:
        if not check_missing_glyphs(cand, ch):
            return cand
    return ""


def _normalize_richtext_inlines_for_direction(node: Any, *, is_vertical: bool, font_family: str = "") -> None:
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            # Do NOT rotate ASCII ()!? inside horizontal-in-vertical TCY runs
            if not node.get("tcy"):
                node["text"] = normalize_text_for_direction(
                    node["text"],
                    is_vertical=is_vertical,
                    font_family=font_family,
                    has_custom_rotation=False,
                )
        for k, v in node.items():
            if k != "style" and isinstance(v, (dict, list)):
                _normalize_richtext_inlines_for_direction(v, is_vertical=is_vertical, font_family=font_family)
    elif isinstance(node, list):
        for item in node:
            _normalize_richtext_inlines_for_direction(item, is_vertical=is_vertical, font_family=font_family)


def _is_effectively_vertical_region(rc: dict[str, Any]) -> bool:
    d_val = str(rc.get("direction") or "auto").strip().lower()
    if d_val in ("vertical", "v", "vr"):
        return True
    if d_val in ("horizontal", "h", "hr"):
        return False
    # For 'auto': check aspect ratio and line breaks
    bbox = region_bbox_xyxy(rc)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tr = str(rc.get("translation") or "")
    if bw > 0 and bh > 0 and (bw / max(bh, 1.0)) > 1.45 and "[BR]" not in tr and "\n" not in tr:
        return False
    return True


def save_page_json_atomic(
    paths: PagePaths,
    image_key: str,
    image_data: dict[str, Any],
    *,
    auto_initial_snapshot: bool = True,
) -> Path:
    """Atomically write `{image_key: image_data}` to `paths.json_path`, preserving all non-region metadata."""
    if auto_initial_snapshot and get_runtime_settings().auto_backup_on_first_edit:
        ensure_initial_snapshot(paths)

    data_copy = copy.deepcopy(image_data)
    regions = data_copy.get("regions", [])
    ensure_region_uids(regions)
    normalized_regions: list[dict[str, Any]] = []
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        rc = copy.deepcopy(reg)
        # Ensure `translation_raw` exists for Desktop UI editor compatibility
        if "translation" not in rc:
            rc["translation"] = plain_to_storage_br(str(rc.get("text", "") or ""))
        if "translation_raw" not in rc or rc["translation_raw"] is None:
            rc["translation_raw"] = rc["translation"]
        # Honor top-level `bold: True` by materializing `richtext.v1` inline bold runs
        if rc.get("bold") is True:
            set_region_whole_bold(rc, True)
        # Omit redundant unstyled `translation_rich`
        if "translation_rich" in rc and not document_has_nontrivial_styling(
            rc.get("translation_rich"), str(rc.get("translation", ""))
        ):
            rc.pop("translation_rich", None)
        # Normalize direction for backend/editor compatibility
        d_val = str(rc.get("direction") or "auto").strip().lower()
        if d_val == "h":
            rc["direction"] = "horizontal"
        elif d_val == "v":
            rc["direction"] = "vertical"
        # Match MTUI Desktop Editor's direction-aware text_replacements.yaml on BOTH plain and rich text!
        is_vert = _is_effectively_vertical_region(rc)
        ff_str = str(rc.get("font_family") or "").strip()
        # Strip explicit `::Regular` selector when `bold=True` so Qt synthetic bolding is never suppressed!
        if region_is_bold(rc) and ff_str.lower().endswith("::regular"):
            ff_str = ff_str[: -len("::regular")].strip()
            rc["font_family"] = ff_str

        rc["translation"] = normalize_text_for_direction(
            str(rc.get("translation", "")),
            is_vertical=is_vert,
            font_family=ff_str,
            has_custom_rotation=False,
        )
        if isinstance(rc.get("translation_rich"), dict):
            _normalize_richtext_inlines_for_direction(
                rc["translation_rich"],
                is_vertical=is_vert,
                font_family=ff_str,
            )
            if is_vert:
                # Prevent double-rotation bug (`图8`: `～` + `-90°` normalized to `︴` + `-90°` rendering sideways!)
                sanitize_vertical_richtext_rotations(rc)

        # Auto-convert ‼/⁉ on fonts lacking U+203C/U+2049 and auto-wrap explicit !!/!?/?!/?? into TCY!
        if is_vert:
            tr_cur = str(rc.get("translation", ""))
            ff_low_v = ff_str.casefold()
            needs_unicode_decomp = bool(
                any(
                    k in ff_low_v
                    for k in ("zongyi", "fangyuan", "fzcusong", "fzlantingyuan", "jianzhi", "tankuin", "lisu", "xiaolai")
                )
                or (ff_str and check_missing_glyphs(ff_str, "‼⁉"))
            )
            has_explicit_pair = any(tok in tr_cur for tok in ("!!", "!?", "?!", "??"))
            has_unicode_pair = needs_unicode_decomp and any(
                tok in tr_cur for tok in ("‼", "⁉", "⁈", "⁇", "！！", "！？", "？！", "？？")
            )
            if has_explicit_pair or has_unicode_pair:
                if needs_unicode_decomp:
                    new_tr = (
                        tr_cur.replace("‼", "!!")
                        .replace("⁉", "!?")
                        .replace("⁈", "?!")
                        .replace("⁇", "??")
                        .replace("！！", "!!")
                        .replace("！？", "!?")
                        .replace("？！", "?!")
                        .replace("？？", "??")
                    )
                    if new_tr != tr_cur:
                        sync_region_text_update(rc, storage_br_to_plain(new_tr))
                        tr_cur = str(rc.get("translation", ""))
                apply_span_styles_to_region(
                    rc,
                    spans=[{"regex": r"[!?]{2,4}", "tcy": True}],
                    reset_existing=False,
                )

        # Universal inline `fontFamily` fallback for missing or zero-contour punctuation/symbols (`图2`: `Arphic Tankuin Bold GB` `﹁﹂`, `FZCuSong` `︙`, etc.)!
        if ff_str:
            missing_syms = check_region_missing_glyphs(rc)
            if missing_syms:
                fb_map = {
                    ch: fb
                    for ch in missing_syms
                    if (fb := _pick_punctuation_fallback_font(ff_str, ch))
                }
                if fb_map:
                    apply_missing_glyph_inline_fallbacks(rc, fb_map)
        if region_is_bold(rc):
            rc["bold"] = True
        else:
            rc.pop("bold", None)
        # Strip legacy font_path (MTUI uses font_family)
        rc.pop("font_path", None)
        normalized_regions.append(rc)

    data_copy["regions"] = normalized_regions
    # Editor-saved state uses skip_text_replacements=True so manual edits aren't overwritten by post_dict
    data_copy["skip_text_replacements"] = True
    if "skip_font_scaling" not in data_copy:
        data_copy["skip_font_scaling"] = True

    out_json_path = paths.work_dir / "json" / f"{paths.source_path.stem}_translations.json"
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {image_key: data_copy}

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out_json_path.name}.",
        suffix=".tmp",
        dir=str(out_json_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, cls=_NumpySafeEncoder)
            f.flush()
            os.fsync(f.fileno())
        _atomic_replace_with_retry(tmp_name, out_json_path)
        tmp_name = ""
        return out_json_path
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Snapshots (Backup & Restore)
# ---------------------------------------------------------------------------


def create_snapshot(paths: PagePaths, label: str = "manual") -> dict[str, Any]:
    """Create a point-in-time backup of JSON, inpainted sidecar, and rendered output for a page."""
    safe_label = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", label.strip() or "snapshot")[:48]
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_id = f"{paths.source_path.stem}__{ts}__{safe_label}"
    snap_dir = paths.snapshots_dir / snap_id
    if snap_dir.exists():
        idx_dup = 2
        while (paths.snapshots_dir / f"{snap_id}_{idx_dup}").exists():
            idx_dup += 1
        snap_id = f"{snap_id}_{idx_dup}"
        snap_dir = paths.snapshots_dir / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    manifest: dict[str, Any] = {
        "snapshot_id": snap_id,
        "page": paths.source_path.name,
        "source_path": str(paths.source_path),
        "label": label,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "created_ns": time.time_ns(),
        "files": {},
    }

    if paths.json_exists and paths.json_path.is_file():
        dst = snap_dir / "translations.json"
        shutil.copy2(paths.json_path, dst)
        manifest["files"]["json"] = {"original": str(paths.json_path), "backup": dst.name}
        saved_files.append("json")

    if paths.inpainted_exists and paths.inpainted_path.is_file():
        dst = snap_dir / f"inpainted{paths.inpainted_path.suffix}"
        shutil.copy2(paths.inpainted_path, dst)
        manifest["files"]["inpainted"] = {"original": str(paths.inpainted_path), "backup": dst.name}
        saved_files.append("inpainted")

    if paths.rendered_output_exists and paths.rendered_output_path.is_file():
        dst = snap_dir / f"rendered{paths.rendered_output_path.suffix}"
        shutil.copy2(paths.rendered_output_path, dst)
        manifest["files"]["rendered"] = {"original": str(paths.rendered_output_path), "backup": dst.name}
        saved_files.append("rendered")

    with open(snap_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {
        "snapshot_id": snap_id,
        "snapshot_dir": str(snap_dir),
        "saved_artifacts": saved_files,
        "created_at": manifest["created_at"],
    }


def ensure_initial_snapshot(paths: PagePaths) -> Optional[dict[str, Any]]:
    """Automatically create a baseline `initial_before_mcp` snapshot once before the first MCP edit."""
    if not paths.json_exists and not paths.inpainted_exists:
        return None
    if paths.snapshots_dir.is_dir():
        prefix = f"{paths.source_path.stem}__"
        for entry in paths.snapshots_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(prefix) and entry.name.endswith("__initial_before_mcp"):
                return None
    return create_snapshot(paths, label="initial_before_mcp")


def list_snapshots(target_path: str | Path) -> list[dict[str, Any]]:
    raw_target = Path(target_path)
    single_page_filter: Optional[str] = None
    if raw_target.is_file():
        p = resolve_page_paths(raw_target)
        pages = [p]
        single_page_filter = p.source_path.name
    else:
        pages = list_manga_pages_in_path(target_path)
        if not pages:
            p = resolve_page_paths(target_path)
            pages = [p]
            single_page_filter = p.source_path.name
    seen_dirs: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in pages:
        if not p.snapshots_dir.is_dir():
            continue
        norm_d = os.path.normcase(str(p.snapshots_dir))
        if norm_d in seen_dirs:
            continue
        seen_dirs.add(norm_d)
        page_snaps: list[tuple[int, str, dict[str, Any]]] = []
        for snap_sub in p.snapshots_dir.iterdir():
            mf = snap_sub / "manifest.json"
            if mf.is_file():
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if single_page_filter and data.get("page") != single_page_filter:
                        continue
                    c_ns = int(data.get("created_ns") or mf.stat().st_mtime_ns)
                    page_snaps.append((c_ns, snap_sub.name, data))
                except Exception:
                    continue
        page_snaps.sort(key=lambda item: (item[0], item[1]), reverse=True)
        out.extend(item[2] for item in page_snaps)
    return out


def restore_snapshot(target_path: str | Path, snapshot_id: Optional[str] = None, *, label: Optional[str] = None) -> dict[str, Any]:
    raw_target = Path(target_path).expanduser()
    # Folder-level batch restore (when target_path is a directory and snapshot_id is omitted)
    if raw_target.is_dir() and not snapshot_id:
        pages = [p for p in list_manga_pages_in_path(raw_target) if p.snapshots_dir.is_dir()]
        if not pages:
            raise FileNotFoundError(f"No page snapshots found under directory {raw_target}")
        page_results: list[dict[str, Any]] = []
        for p in pages:
            try:
                page_results.append(restore_snapshot(p.source_path, snapshot_id=None, label=label))
            except FileNotFoundError:
                continue
        if not page_results:
            raise FileNotFoundError(
                f"No snapshots matching label={label!r} found under directory {raw_target}"
                if label
                else f"No restorable snapshots found under directory {raw_target}"
            )
        return {"restored_page_count": len(page_results), "label": label, "pages": page_results}

    paths = resolve_page_paths(target_path)
    resolved_id = snapshot_id
    if not resolved_id:
        candidates = list_snapshots(paths.source_path)
        if label:
            safe_label = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", label.strip() or "snapshot")[:48]
            candidates = [
                c
                for c in candidates
                if c.get("label") == label or str(c.get("snapshot_id") or "").endswith(f"__{safe_label}")
            ]
        if not candidates:
            raise FileNotFoundError(
                f"No snapshot matching label={label!r} found for {paths.source_path.name}"
                if label
                else f"No snapshots found for {paths.source_path.name}"
            )
        # Prefer newest non-initial_before_mcp snapshot when label is unspecified and manual checkpoints exist
        if not label:
            non_initial = [
                c for c in candidates if not str(c.get("snapshot_id") or "").endswith("__initial_before_mcp")
            ]
            if non_initial:
                candidates = non_initial
        resolved_id = str(candidates[0]["snapshot_id"])

    snap_dir = paths.snapshots_dir / resolved_id
    if not snap_dir.is_dir():
        # Search across folder snapshots
        if paths.work_dir.is_dir():
            alt = paths.work_dir / ".mtui_mcp_snapshots" / resolved_id
            if alt.is_dir():
                snap_dir = alt
    mf_path = snap_dir / "manifest.json"
    if not mf_path.is_file():
        raise FileNotFoundError(f"Snapshot {resolved_id!r} not found under {paths.snapshots_dir}")

    with open(mf_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    restored: list[str] = []
    for kind, info in (manifest.get("files") or {}).items():
        src = snap_dir / info["backup"]
        dst = Path(info["original"])
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(f"{kind}:{dst}")
    return {"page": paths.source_path.name, "snapshot_id": resolved_id, "label": manifest.get("label"), "restored": restored}


def create_snapshots_for_target(target_path: str | Path, label: str = "checkpoint") -> dict[str, Any]:
    """Create a snapshot for a single page OR batch-snapshot every translated/inpainted page in a chapter folder."""
    raw_target = Path(target_path).expanduser()
    if raw_target.is_dir():
        pages = [
            p
            for p in list_manga_pages_in_path(raw_target)
            if p.json_exists or p.inpainted_exists or p.rendered_output_exists
        ]
        if not pages:
            raise FileNotFoundError(f"No translated/inpainted pages found to snapshot under {raw_target}")
        created = [create_snapshot(p, label=label) for p in pages]
        return {
            "label": label,
            "page_count": len(created),
            "snapshots": created,
        }
    p = resolve_page_paths(target_path)
    return create_snapshot(p, label=label)


def cleanup_snapshots_for_target(
    target_path: str | Path,
    *,
    label: Optional[str] = None,
    snapshot_id: Optional[str] = None,
) -> dict[str, Any]:
    """Delete temporary snapshots for a single page or entire chapter directory to reclaim disk space."""
    raw_target = Path(target_path).expanduser()
    single_page_name: Optional[str] = None
    single_stem_prefix: Optional[str] = None
    snap_dirs: list[Path] = []
    seen_dirs: set[str] = set()

    def _add_dir(d: Path) -> None:
        if d.is_dir():
            key = os.path.normcase(str(d.resolve()))
            if key not in seen_dirs:
                seen_dirs.add(key)
                snap_dirs.append(d)

    if raw_target.is_file():
        p = resolve_page_paths(raw_target)
        single_page_name = p.source_path.name
        single_stem_prefix = f"{p.source_path.stem}__"
        _add_dir(p.snapshots_dir)
    elif raw_target.is_dir():
        for p in list_manga_pages_in_path(raw_target):
            _add_dir(p.snapshots_dir)
        _add_dir(raw_target / "manga_translator_work" / ".mtui_mcp_snapshots")
    else:
        p = resolve_page_paths(target_path)
        single_page_name = p.source_path.name
        single_stem_prefix = f"{p.source_path.stem}__"
        _add_dir(p.snapshots_dir)

    safe_label = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", label.strip() or "snapshot")[:48] if label else None
    deleted_count = 0
    freed_bytes = 0

    for sdir in snap_dirs:
        if not sdir.is_dir():
            continue
        for sub in list(sdir.iterdir()):
            if not sub.is_dir():
                continue
            if snapshot_id and sub.name != snapshot_id:
                continue
            mf = sub / "manifest.json"
            manifest: dict[str, Any] = {}
            if mf.is_file():
                try:
                    manifest = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
            if single_page_name:
                m_page = manifest.get("page")
                if m_page and m_page != single_page_name:
                    continue
                if not m_page and single_stem_prefix and not sub.name.startswith(single_stem_prefix):
                    continue
            if label:
                if manifest.get("label") != label and not (
                    safe_label and (sub.name.endswith(f"__{safe_label}") or f"__{safe_label}_" in sub.name)
                ):
                    continue

            sub_bytes = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
            shutil.rmtree(sub, ignore_errors=True)
            if not sub.exists():
                deleted_count += 1
                freed_bytes += sub_bytes

        try:
            if sdir.is_dir() and not any(sdir.iterdir()):
                sdir.rmdir()
        except OSError:
            pass

    return {
        "deleted_count": deleted_count,
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "label": label,
    }


# ---------------------------------------------------------------------------
# Region Geometry, Color Sampling & Inspection Formatting
# ---------------------------------------------------------------------------


def is_perspective_quad(value: Any) -> bool:
    """Return True if ``value`` is a 4-point ``[[x, y], ...]`` local/world quad with finite coordinates."""
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 4:
        return False
    for pt in value:
        if not isinstance(pt, (list, tuple, np.ndarray)) or len(pt) < 2:
            return False
        try:
            fx, fy = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return False
    return True


def _parse_perspective_quad(value: Any) -> list[list[float]]:
    """Validate and normalize a 4-point ``[[x, y], ...]`` perspective quad."""
    if not is_perspective_quad(value):
        raise ValueError(
            "white_frame_quad_local must be a 4-point [[x1, y1], [x2, y2], [x3, y3], [x4, y4]] list with finite coordinates, or None"
        )
    return [[float(pt[0]), float(pt[1])] for pt in value[:4]]


def region_has_perspective_quad(region: dict[str, Any]) -> bool:
    """Return True if ``region`` carries an active 4-point ``white_frame_quad_local`` or ``render_box_quad_local``."""
    return is_perspective_quad(region.get("white_frame_quad_local")) or is_perspective_quad(
        region.get("render_box_quad_local")
    )


def region_perspective_quad_world(region: dict[str, Any]) -> Optional[list[list[float]]]:
    """Project a region's local perspective quad into world coordinates (mirroring ``EditorControllerExportService._perspective_quad_world``)."""
    if not isinstance(region, dict):
        return None
    quad = None
    if region.get("has_custom_white_frame") and is_perspective_quad(region.get("white_frame_quad_local")):
        quad = region.get("white_frame_quad_local")
    elif is_perspective_quad(region.get("render_box_quad_local")):
        quad = region.get("render_box_quad_local")
    elif is_perspective_quad(region.get("white_frame_quad_local")):
        quad = region.get("white_frame_quad_local")
    if quad is None:
        return None

    cx, cy = region_center(region)
    angle = float(region.get("angle") or 0.0)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return [
        [
            cx + float(pt[0]) * cos_a - float(pt[1]) * sin_a,
            cy + float(pt[0]) * sin_a + float(pt[1]) * cos_a,
        ]
        for pt in quad[:4]
    ]


def region_render_world_bbox_xyxy(region: dict[str, Any]) -> list[float]:
    """Return the union world ``[x1, y1, x2, y2]`` bbox covering OCR ``lines`` AND any perspective quad / custom white frame."""
    base_bbox = region_bbox_xyxy(region)
    xs: list[float] = []
    ys: list[float] = []
    if base_bbox != [0.0, 0.0, 0.0, 0.0]:
        xs.extend([base_bbox[0], base_bbox[2]])
        ys.extend([base_bbox[1], base_bbox[3]])

    quad_world = region_perspective_quad_world(region)
    if quad_world is not None:
        for wx, wy in quad_world:
            xs.append(float(wx))
            ys.append(float(wy))
    else:
        local_rect = None
        if region.get("has_custom_white_frame") and isinstance(region.get("white_frame_rect_local"), (list, tuple)) and len(region["white_frame_rect_local"]) >= 4:
            local_rect = region["white_frame_rect_local"]
        elif isinstance(region.get("render_box_rect_local"), (list, tuple)) and len(region["render_box_rect_local"]) >= 4:
            local_rect = region["render_box_rect_local"]
        if local_rect is not None:
            try:
                lx1, ly1, lx2, ly2 = (float(v) for v in local_rect[:4])
                cx, cy = region_center(region)
                rad = math.radians(float(region.get("angle") or 0.0))
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                for lx, ly in ((lx1, ly1), (lx2, ly1), (lx2, ly2), (lx1, ly2)):
                    xs.append(cx + lx * cos_a - ly * sin_a)
                    ys.append(cy + lx * sin_a + ly * cos_a)
            except (TypeError, ValueError):
                pass

    if not xs or not ys:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def scale_region_local_frames_for_font_change(
    region: dict[str, Any],
    old_fs: float | int,
    new_fs: float | int,
) -> None:
    """Scale ``white_frame_quad_local`` / ``render_box_quad_local`` around centroid on ``font_size`` change (mirroring ``editor_controller.py:113-155``)."""
    old_f = float(old_fs or 0)
    new_f = float(new_fs or 0)
    if old_f <= 0.0 or new_f <= 0.0 or math.isclose(old_f, new_f):
        return
    scale = new_f / old_f
    for quad_key, bbox_key in (
        ("white_frame_quad_local", "white_frame_rect_local"),
        ("render_box_quad_local", "render_box_rect_local"),
    ):
        quad = region.get(quad_key)
        if not is_perspective_quad(quad):
            continue
        cent_x = sum(float(pt[0]) for pt in quad[:4]) / 4.0
        cent_y = sum(float(pt[1]) for pt in quad[:4]) / 4.0
        scaled = [
            [
                cent_x + (float(pt[0]) - cent_x) * scale,
                cent_y + (float(pt[1]) - cent_y) * scale,
            ]
            for pt in quad[:4]
        ]
        region[quad_key] = scaled
        q_xs = [pt[0] for pt in scaled]
        q_ys = [pt[1] for pt in scaled]
        region[bbox_key] = [min(q_xs), min(q_ys), max(q_xs), max(q_ys)]


def region_bbox_xyxy(region: dict[str, Any]) -> list[float]:
    lines = region.get("lines") or []
    pts = [pt for poly in lines for pt in poly if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not pts:
        return [0.0, 0.0, 0.0, 0.0]
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def region_center(region: dict[str, Any]) -> list[float]:
    c = region.get("center")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        return [round(float(c[0]), 1), round(float(c[1]), 1)]
    x1, y1, x2, y2 = region_bbox_xyxy(region)
    return [round((x1 + x2) / 2.0, 1), round((y1 + y2) / 2.0, 1)]


def resolve_region_colors_hex(region: dict[str, Any]) -> tuple[str, str]:
    """Return `(font_color_hex, stroke_color_hex)` for a region."""
    fg_hex = "#000000"
    for k in ("font_color", "fg_colors", "fg_color"):
        v = region.get(k)
        if v is not None:
            try:
                parsed = normalize_hex_color(v)
                if parsed:
                    fg_hex = parsed
                    break
            except ValueError:
                continue

    bg_hex = "#ffffff"
    for k in ("text_stroke_color", "bg_colors", "bg_color"):
        v = region.get(k)
        if v is not None:
            try:
                parsed = normalize_hex_color(v)
                if parsed:
                    bg_hex = parsed
                    break
            except ValueError:
                continue
    return fg_hex, bg_hex


def sample_original_text_colors(source_rgb: np.ndarray, bbox_xyxy: list[float]) -> tuple[str, str]:
    """Sample dominant foreground ink color and background bubble color from original image crop."""
    h, w = source_rgb.shape[:2]
    x1 = max(0, min(w - 1, int(math.floor(bbox_xyxy[0]))))
    y1 = max(0, min(h - 1, int(math.floor(bbox_xyxy[1]))))
    x2 = max(x1 + 1, min(w, int(math.ceil(bbox_xyxy[2]))))
    y2 = max(y1 + 1, min(h, int(math.ceil(bbox_xyxy[3]))))
    crop = source_rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return "#000000", "#ffffff"

    luma = 0.299 * crop[..., 0] + 0.587 * crop[..., 1] + 0.114 * crop[..., 2]
    border_pixels = np.concatenate(
        [crop[0, :], crop[-1, :], crop[:, 0], crop[:, -1]], axis=0
    )
    bg_median = np.median(border_pixels, axis=0)
    bg_luma = 0.299 * bg_median[0] + 0.587 * bg_median[1] + 0.114 * bg_median[2]

    if bg_luma >= 128:
        # Dark ink on bright bubble: take darkest 15% pixels
        thresh = np.percentile(luma, 15)
        ink_pixels = crop[luma <= thresh]
    else:
        # Bright ink on dark bubble: take brightest 15% pixels
        thresh = np.percentile(luma, 85)
        ink_pixels = crop[luma >= thresh]

    fg_median = np.median(ink_pixels, axis=0) if ink_pixels.size > 0 else (255 - bg_median)
    fg_hex = normalize_hex_color(fg_median.tolist()) or "#000000"
    bg_hex = normalize_hex_color(bg_median.tolist()) or "#ffffff"
    return fg_hex, bg_hex


def format_region_summary(idx: int, region: dict[str, Any]) -> dict[str, Any]:
    bbox = region_bbox_xyxy(region)
    w = round(bbox[2] - bbox[0], 1)
    h = round(bbox[3] - bbox[1], 1)
    fg_hex, bg_hex = resolve_region_colors_hex(region)
    dir_raw = str(region.get("direction") or "auto").lower()
    dir_short = {"horizontal": "h", "vertical": "v", "hr": "h", "vr": "v"}.get(dir_raw, dir_raw)
    vis_text = visible_text_of_region(region)
    rich = region.get("translation_rich")

    return {
        "index": idx,
        "region_uid": str(region.get("region_uid") or ""),
        "source_text": str(region.get("text", "") or ""),
        "source_lines": list(region.get("texts") or []),
        "ocr_prob": round(float(region.get("prob", 1.0) or 1.0), 3),
        "translation": vis_text,
        "line_count": vis_text.count("\n") + 1 if vis_text else 0,
        "has_rich_text": is_rich_text_document(rich),
        "translation_rich": rich if is_rich_text_document(rich) else None,
        "font_size": int(round(float(region.get("font_size") or 16))),
        "font_family": str(region.get("font_family") or ""),
        "bold": region_is_bold(region),
        "font_color": fg_hex,
        "stroke_color": bg_hex,
        "stroke_width": round(float(region.get("stroke_width", region.get("default_stroke_width", 0.07)) or 0.07), 3),
        "disable_font_border": bool(region.get("disable_font_border", False)),
        "direction": dir_short,
        "alignment": str(region.get("alignment") or "auto"),
        "line_spacing": round(float(region.get("line_spacing", 1.0) or 1.0), 3),
        "letter_spacing": round(float(region.get("letter_spacing", 1.0) or 1.0), 3),
        "angle": round(float(region.get("angle", 0.0) or 0.0), 2),
        "bbox_xyxy": bbox,
        "size_wh": [w, h],
        "center": region_center(region),
        "polygon_count": len(region.get("lines") or []),
        "has_custom_white_frame": bool(region.get("has_custom_white_frame", False)),
        "white_frame_rect_local": region.get("white_frame_rect_local"),
        "has_perspective_quad": region_has_perspective_quad(region),
        "white_frame_quad_local": region.get("white_frame_quad_local")
        if is_perspective_quad(region.get("white_frame_quad_local"))
        else None,
    }


def get_page_details_dict(page_path: str | Path, include_polygons: bool = False) -> dict[str, Any]:
    paths = resolve_page_paths(page_path)
    img_w, img_h = 0, 0
    if paths.display_base_path.is_file():
        with Image.open(paths.display_base_path) as im:
            img_w, img_h = im.size

    _, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])
    ensure_region_uids(regions)
    formatted_regions = []
    for i, reg in enumerate(regions):
        summary = format_region_summary(i, reg)
        if include_polygons:
            summary["lines"] = reg.get("lines", [])
        formatted_regions.append(summary)

    return {
        "page_name": paths.source_path.name,
        "revision": compute_page_revision(paths),
        "source_path": str(paths.source_path),
        "display_base_path": str(paths.display_base_path),
        "json_path": str(paths.json_path),
        "json_exists": paths.json_exists,
        "inpainted_path": str(paths.inpainted_path),
        "inpainted_exists": paths.inpainted_exists,
        "rendered_output_path": str(paths.rendered_output_path),
        "rendered_output_exists": paths.rendered_output_exists,
        "last_output_path": image_data.get("last_output_path"),
        "image_size": [img_w, img_h],
        "has_mask_raw": bool(image_data.get("mask_raw")),
        "mask_is_refined": bool(image_data.get("mask_is_refined", False)),
        "skip_font_scaling": bool(image_data.get("skip_font_scaling", True)),
        "upscale_ratio": image_data.get("upscale_ratio"),
        "colorizer": image_data.get("colorizer"),
        "paste_overlay_count": len(image_data.get("paste_overlays") or []),
        "has_paint_overlay": bool(image_data.get("paint_overlay")) or paths.paint_overlay_exists,
        "has_stamp_overlay": bool(image_data.get("stamp_overlay")),
        "region_count": len(formatted_regions),
        "regions": formatted_regions,
    }


def _reading_order_sort_key(reg_summary: dict[str, Any], img_w: int, rtl: bool = True) -> tuple[float, float]:
    cx, cy = reg_summary["center"]
    # Group into vertical bands (~12% of page height or 180px) for comic panel reading order
    band = math.floor(cy / 180.0)
    horiz = -cx if rtl else cx
    return (band, horiz)


def extract_chapter_script_data(
    target_path: str | Path,
    *,
    reading_order: str = "rtl",  # "rtl" (Japanese/Traditional manga) | "ltr" (Webtoon/Western) | "index"
    start_page: int = 0,
    max_pages: int = 200,
) -> dict[str, Any]:
    pages = list_manga_pages_in_path(target_path)
    sliced = pages[max(0, start_page) : max(0, start_page) + max(1, max_pages)]
    script_pages: list[dict[str, Any]] = []
    total_regions = 0

    for p_offset, paths in enumerate(sliced, start=max(0, start_page)):
        if not paths.json_exists:
            script_pages.append(
                {
                    "page_index": p_offset,
                    "page_name": paths.source_path.name,
                    "source_path": str(paths.source_path),
                    "json_exists": False,
                    "entries": [],
                }
            )
            continue

        details = get_page_details_dict(paths.source_path, include_polygons=False)
        regs = list(details["regions"])
        img_w = details["image_size"][0] or 1600
        if reading_order in ("rtl", "ltr"):
            regs.sort(key=lambda r: _reading_order_sort_key(r, img_w, rtl=(reading_order == "rtl")))

        entries = [
            {
                "region_index": r["index"],
                "source_text": r["source_text"],
                "ocr_prob": r["ocr_prob"],
                "translation": r["translation"],
                "direction": r["direction"],
                "font_size": r["font_size"],
                "font_family": r["font_family"],
                "bbox_xyxy": r["bbox_xyxy"],
            }
            for r in regs
        ]
        total_regions += len(entries)
        script_pages.append(
            {
                "page_index": p_offset,
                "page_name": paths.source_path.name,
                "source_path": str(paths.source_path),
                "json_exists": True,
                "entries": entries,
            }
        )

    return {
        "total_pages_in_folder": len(pages),
        "returned_pages": len(script_pages),
        "reading_order": reading_order,
        "total_regions": total_regions,
        "pages": script_pages,
    }


ALLOWED_REGION_PATCH_KEYS = frozenset(
    {
        "region_index",
        "region_uid",
        "translation",
        "clear_rich_text",
        "source_text",
        "translation_rich",
        "rich_style",
        "rich_spans",
        "reset_rich_style",
        "font_size",
        "font_size_delta",
        "font_family",
        "bold",
        "sample_color_from_original",
        "font_color",
        "stroke_color",
        "stroke_width",
        "disable_font_border",
        "direction",
        "alignment",
        "line_spacing",
        "letter_spacing",
        "opacity",
        "lines",
        "bbox_xyxy",
        "center",
        "move_dx",
        "move_dy",
        "move_source_box",
        "angle",
        "white_frame_rect_local",
        "white_frame_quad_local",
    }
)


def apply_region_updates_to_page(
    page_path: str | Path,
    updates: list[dict[str, Any]],
    *,
    expected_revision: Optional[str] = None,
) -> tuple[PagePaths, list[dict[str, Any]]]:
    """Apply structured updates to one or more regions on a page and atomically save JSON."""
    paths = resolve_page_paths(page_path)
    verify_expected_revision(paths, expected_revision)
    image_key, image_data = load_page_json(paths)
    regions = image_data.setdefault("regions", [])
    ensure_region_uids(regions)

    source_rgb: Optional[np.ndarray] = None
    modified_summaries: list[dict[str, Any]] = []

    for patch in updates:
        if not isinstance(patch, dict):
            raise TypeError("Each item in updates must be a dictionary")
        unknown_keys = set(patch.keys()) - ALLOWED_REGION_PATCH_KEYS
        if unknown_keys:
            raise ValueError(
                f"Unknown region update field(s) {sorted(unknown_keys)}; "
                f"allowed fields: {sorted(ALLOWED_REGION_PATCH_KEYS)}"
            )
        idx = resolve_region_target_index(
            regions,
            region_index=patch.get("region_index"),
            region_uid=patch.get("region_uid"),
        )
        reg = regions[idx]

        # 1. Text & Rich-text updates
        clear_rt = bool(patch.get("clear_rich_text", False))
        if "translation" in patch and patch["translation"] is not None:
            sync_region_text_update(reg, str(patch["translation"]), clear_rich_text=clear_rt)
        elif clear_rt:
            reg.pop("translation_rich", None)

        if "source_text" in patch and patch["source_text"] is not None:
            st = str(patch["source_text"])
            reg["text"] = st
            reg["texts"] = [st]

        if "translation_rich" in patch and patch["translation_rich"] is not None:
            rt_val = patch["translation_rich"]
            if not is_rich_text_document(rt_val):
                raise ValueError("translation_rich must be a valid richtext.v1 dict")
            reg["translation_rich"] = copy.deepcopy(rt_val)
            vis = visible_text_of_region(reg)
            reg["translation"] = plain_to_storage_br(vis)
            reg["translation_raw"] = reg["translation"]

        # Optional inline/whole rich-text styling right inside `update_regions`!
        if patch.get("rich_style") or patch.get("rich_spans"):
            apply_span_styles_to_region(
                reg,
                whole_style=patch.get("rich_style"),
                spans=patch.get("rich_spans"),
                reset_existing=bool(patch.get("reset_rich_style", False)),
            )

        # 2. Font size & Font family
        old_fs = int(round(float(reg.get("font_size") or 16)))
        if patch.get("font_size") is not None:
            new_fs = max(1, int(round(float(patch["font_size"]))))
            reg["font_size"] = new_fs
            if "white_frame_quad_local" not in patch and "white_frame_rect_local" not in patch:
                scale_region_local_frames_for_font_change(reg, old_fs, new_fs)
        elif patch.get("font_size_delta") is not None:
            new_fs = max(1, old_fs + int(round(float(patch["font_size_delta"]))))
            reg["font_size"] = new_fs
            if "white_frame_quad_local" not in patch and "white_frame_rect_local" not in patch:
                scale_region_local_frames_for_font_change(reg, old_fs, new_fs)

        if patch.get("font_family") is not None:
            reg["font_family"] = str(patch["font_family"]).strip()

        if patch.get("bold") is not None:
            set_region_whole_bold(reg, bool(patch["bold"]))

        # 3. Colors & Stroke
        if patch.get("sample_color_from_original"):
            if source_rgb is None and paths.display_base_path.is_file():
                source_rgb = open_rgb_image(paths.display_base_path)
            if source_rgb is not None:
                fg_auto, bg_auto = sample_original_text_colors(source_rgb, region_bbox_xyxy(reg))
                reg["font_color"] = fg_auto
                reg.pop("fg_colors", None)
                reg.pop("fg_color", None)
                rgb_bg = color_to_rgb_list(bg_auto)
                if rgb_bg:
                    reg["bg_color"] = rgb_bg
                    reg.pop("bg_colors", None)
                    reg.pop("text_stroke_color", None)

        if patch.get("font_color") is not None:
            fg_hex = normalize_hex_color(patch["font_color"])
            if fg_hex:
                reg["font_color"] = fg_hex
                reg.pop("fg_colors", None)
                reg.pop("fg_color", None)

        if patch.get("stroke_color") is not None:
            bg_rgb = color_to_rgb_list(patch["stroke_color"])
            if bg_rgb is not None:
                reg["bg_color"] = bg_rgb
                reg.pop("bg_colors", None)
                reg.pop("text_stroke_color", None)

        if patch.get("stroke_width") is not None:
            sw = max(0.0, float(patch["stroke_width"]))
            reg["stroke_width"] = sw
            reg["default_stroke_width"] = sw

        if patch.get("disable_font_border") is not None:
            reg["disable_font_border"] = bool(patch["disable_font_border"])

        # 4. Layout, Direction, Alignment, Spacing
        if patch.get("direction") is not None:
            d = str(patch["direction"]).strip().lower()
            if d not in ("h", "v", "auto", "horizontal", "vertical"):
                raise ValueError(f"Invalid direction {d!r}; expected 'h', 'v', or 'auto'")
            reg["direction"] = {"h": "horizontal", "v": "vertical"}.get(d, d)

        if patch.get("alignment") is not None:
            al = str(patch["alignment"]).strip().lower()
            if al not in ("left", "center", "right", "auto"):
                raise ValueError(f"Invalid alignment {al!r}; expected 'left', 'center', 'right', or 'auto'")
            reg["alignment"] = al

        if patch.get("line_spacing") is not None:
            reg["line_spacing"] = max(0.1, float(patch["line_spacing"]))

        if patch.get("letter_spacing") is not None:
            reg["letter_spacing"] = max(0.1, float(patch["letter_spacing"]))

        if patch.get("opacity") is not None:
            reg["opacity"] = max(0.0, min(1.0, float(patch["opacity"])))

        # 5. Geometry: bbox_xyxy, lines, center, move_dx/dy, angle, white_frame
        if patch.get("lines") is not None:
            reg["lines"] = patch["lines"]
            if "center" not in patch:
                reg["center"] = region_center({"lines": patch["lines"]})
        elif patch.get("bbox_xyxy") is not None:
            x1, y1, x2, y2 = (float(v) for v in patch["bbox_xyxy"])
            reg["lines"] = [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]]
            if "center" not in patch:
                reg["center"] = [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

        if patch.get("center") is not None:
            cx, cy = (float(v) for v in patch["center"][:2])
            reg["center"] = [cx, cy]
            # Clear local offset & stale quads unless caller explicitly sets white_frame_quad_local/rect_local in the same patch
            if "white_frame_quad_local" not in patch and "white_frame_rect_local" not in patch:
                reg.pop("white_frame_rect_local", None)
                reg.pop("render_box_rect_local", None)
                reg.pop("white_frame_quad_local", None)
                reg.pop("render_box_quad_local", None)
                reg["has_custom_white_frame"] = False

        dx = float(patch.get("move_dx", 0.0) or 0.0)
        dy = float(patch.get("move_dy", 0.0) or 0.0)
        if dx != 0.0 or dy != 0.0:
            cur_c = region_center(reg)
            reg["center"] = [cur_c[0] + dx, cur_c[1] + dy]
            # Also shift polygon lines if requested via `move_source_box`
            if patch.get("move_source_box", False) and isinstance(reg.get("lines"), list):
                reg["lines"] = [
                    [[float(pt[0]) + dx, float(pt[1]) + dy] for pt in poly]
                    for poly in reg["lines"]
                ]

        if patch.get("angle") is not None:
            reg["angle"] = float(patch["angle"])
            reg.pop("render_box_rect_local", None)
            if "white_frame_quad_local" not in patch:
                reg.pop("render_box_quad_local", None)

        if "white_frame_quad_local" in patch:
            if patch["white_frame_quad_local"] is None:
                reg.pop("white_frame_quad_local", None)
                reg.pop("render_box_quad_local", None)
                if patch.get("white_frame_rect_local") is None:
                    reg.pop("white_frame_rect_local", None)
                    reg.pop("render_box_rect_local", None)
                    reg["has_custom_white_frame"] = False
            else:
                quad = _parse_perspective_quad(patch["white_frame_quad_local"])
                q_xs = [pt[0] for pt in quad]
                q_ys = [pt[1] for pt in quad]
                q_bbox = [min(q_xs), min(q_ys), max(q_xs), max(q_ys)]
                reg["white_frame_quad_local"] = quad
                reg["render_box_quad_local"] = [list(pt) for pt in quad]
                reg["white_frame_rect_local"] = q_bbox
                reg["render_box_rect_local"] = list(q_bbox)
                reg["has_custom_white_frame"] = True

        if patch.get("white_frame_rect_local") is not None and patch.get("white_frame_quad_local") is None:
            wf = [float(v) for v in patch["white_frame_rect_local"][:4]]
            if len(wf) < 4:
                raise ValueError("white_frame_rect_local must have 4 numbers [x1, y1, x2, y2]")
            reg["white_frame_rect_local"] = wf
            reg["has_custom_white_frame"] = True
            reg.pop("white_frame_quad_local", None)
            reg.pop("render_box_quad_local", None)

        modified_summaries.append(format_region_summary(idx, reg))

    saved_json = save_page_json_atomic(paths, image_key, image_data)
    refreshed = resolve_page_paths(saved_json)
    return refreshed, modified_summaries


def align_regions_on_page(
    page_path: str | Path,
    region_indices: list[int],
    mode: str,
    reference: str = "selection",  # "selection" | "canvas"
) -> tuple[PagePaths, list[dict[str, Any]]]:
    """Align or distribute multiple regions on a page (matching `alignment_service.py`)."""
    paths = resolve_page_paths(page_path)
    image_key, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])

    valid_indices = [int(i) for i in region_indices if 0 <= int(i) < len(regions)]
    if len(valid_indices) < (1 if reference == "canvas" else 2):
        raise ValueError("Not enough valid region_indices for alignment/distribution")

    img_w, img_h = 0.0, 0.0
    if paths.display_base_path.is_file():
        with Image.open(paths.display_base_path) as im:
            img_w, img_h = float(im.width), float(im.height)

    boxes = {i: region_render_world_bbox_xyxy(regions[i]) for i in valid_indices}
    centers = {i: region_center(regions[i]) for i in valid_indices}

    if mode in ("distribute_horizontal", "distribute_vertical"):
        if len(valid_indices) < 3:
            raise ValueError("Distribution requires at least 3 regions")
        axis = 0 if mode == "distribute_horizontal" else 1
        ordered = sorted(valid_indices, key=lambda idx: centers[idx][axis])
        first_c = centers[ordered[0]][axis]
        last_c = centers[ordered[-1]][axis]
        n = len(ordered)
        for pos, idx in enumerate(ordered[1:-1], start=1):
            target_c = first_c + (last_c - first_c) * pos / (n - 1)
            new_c = list(centers[idx])
            new_c[axis] = target_c
            regions[idx]["center"] = new_c
    else:
        if reference == "canvas" and img_w > 0 and img_h > 0:
            min_x, min_y, max_x, max_y = 0.0, 0.0, img_w, img_h
        else:
            min_x = min(boxes[i][0] for i in valid_indices)
            min_y = min(boxes[i][1] for i in valid_indices)
            max_x = max(boxes[i][2] for i in valid_indices)
            max_y = max(boxes[i][3] for i in valid_indices)

        for idx in valid_indices:
            x1, y1, x2, y2 = boxes[idx]
            cx, cy = centers[idx]
            hw, hh = (x2 - x1) / 2.0, (y2 - y1) / 2.0
            if mode == "left":
                cx = min_x + hw
            elif mode == "horizontal_center":
                cx = (min_x + max_x) / 2.0
            elif mode == "right":
                cx = max_x - hw
            elif mode == "top":
                cy = min_y + hh
            elif mode == "vertical_center":
                cy = (min_y + max_y) / 2.0
            elif mode == "bottom":
                cy = max_y - hh
            else:
                raise ValueError(
                    f"Unsupported align mode {mode!r}; choose from left, horizontal_center, right, top, vertical_center, bottom, distribute_horizontal, distribute_vertical"
                )
            regions[idx]["center"] = [cx, cy]

    saved_json = save_page_json_atomic(paths, image_key, image_data)
    refreshed = resolve_page_paths(saved_json)
    return refreshed, [format_region_summary(i, regions[i]) for i in valid_indices]
