"""Automated translation, typesetting, font-glyph, and CV inpaint QA auditor."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .bubble_centering import split_multilobe_cusp_bisector
from .config import get_runtime_settings
from .font_catalog import check_region_missing_glyphs
from .font_detector import (
    classify_current_mtui_font,
    classify_vcb_font_entry,
    is_narrow_kana_stretch_suspect,
    load_font_detection_cache,
    lookup_cached_region_prediction,
    recommend_mtui_font_for_prediction,
    yuzumarker_models_available,
)

try:
    from .font_detector import CACHE_VERSION
except ImportError:
    CACHE_VERSION = 2
from .local_inpaint import (
    detect_inward_bubble_cusps,
    extract_tight_halo_glyph_mask,
    pair_divided_by_bubble_cusps,
)
from .path_resolver import PagePaths, list_manga_pages_in_path
from .project_store import (
    decode_mask_base64,
    load_page_json,
    open_rgb_image,
    region_bbox_xyxy,
    region_center,
)
from .richtext_builder import (
    ALREADY_VERTICAL_GLYPHS,
    region_is_bold,
    visible_text_of_region,
)
from .translation_auditor import (
    GlossaryTerm,
    audit_region_translation_accuracy,
    load_active_glossary_terms,
)

_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_CLOSING_START_PUNCT = set("，、。．｡,.!?！？；;：:）)］]｝}】」』》〉”’")
_OPENING_END_PUNCT = set("（(［[｛{【「『《〈“‘")
_CHAPTER_OR_ENDNOTE_HEADER_RE = re.compile(
    r"(?:extra\s*contents|contents|目\s*[次录錄]|quest\s*\d+|chapter\s*\d+|"
    r"第\s*[\d一二三四五六七八九十百零〇]+\s*[話话章回幕節节]|つづく|おわり|待续|完$|次号)",
    re.IGNORECASE,
)
_RUNNING_TITLE_VOL_RE = re.compile(
    r"(?:[\u30a0-\u30ff\u4e00-\u9fff]{2,}[\s・·:：\-—]*(?:\d{1,2}|[①-⑳]|[\(（\[［]\d{1,2}[\)）\]］])\s*$)"
)
_TOC_LINE_RE = re.compile(
    r"(?:quest\s*\d+|chapter\s*\d+|ep(?:isode)?\.?\s*\d+|第\s*[\d一二三四五六七八九十百零〇]+\s*[話话章回幕]).*?(?:\d{2,3}|[…⋯\.]{2,})",
    re.IGNORECASE,
)
_VALID_CLITIC_START_WORDS_RE = re.compile(
    r"^(?:了解|了结|了不起|了断|了然|了却|了事|了得|了无|"
    r"着急|着迷|着手|着落|着想|着重|着眼|着陆|着装|着火|着凉|着魔|着实|"
    r"过去|过来|过度|过分|过程|过错|过失|过奖|过目|过夜|过世|过关|过虑|过头|过往|过日子|过不去|过意不去|过招|过瘾|过问|"
    r"的确|的话|的当|的卢|"
    r"得到|得以|得知|得意|得罪|得救|得手|得体|得失|得多|得力|得逞|得空|得胜|得宠|得寸进尺|得亏|得了吧)"
)


def _count_vertical_cells_in_line(line: str) -> int:
    """Count vertical character cells in a line, treating 2..4 char `!?` TCY clusters as 1 vertical cell."""
    collapsed = re.sub(r"[!?！？]{2,4}", "X", line)
    return len(collapsed)


def _find_double_rotated_vertical_glyphs(rich: Any) -> list[str]:
    """Return sorted already-vertical glyphs (`︴`, `︙`, `│`, `﹁﹂`, etc.) carrying non-zero `transform.rotation`."""
    bad: set[str] = set()
    if not isinstance(rich, dict) or not isinstance(rich.get("blocks"), list):
        return []

    def _walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = node.get("type", "text")
            if ntype == "text":
                st = node.get("style") if isinstance(node.get("style"), dict) else {}
                tf = st.get("transform") if isinstance(st.get("transform"), dict) else {}
                rot = float(tf.get("rotation") or 0.0)
                if abs(rot) >= 45.0:
                    for ch in str(node.get("text") or ""):
                        if ch in ALREADY_VERTICAL_GLYPHS:
                            bad.add(ch)
            elif ntype == "ruby":
                _walk(node.get("base"))
                _walk(node.get("text"))
            elif ntype == "tcy":
                _walk(node.get("content"))

    for blk in rich.get("blocks") or []:
        if isinstance(blk, dict):
            _walk(blk.get("inlines"))
    return sorted(bad)


def _boxes_iou(a: list[float], b: list[float]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


def _detect_residual_ink_around_regions(
    base_rgb: np.ndarray,
    inpainted_rgb: np.ndarray,
    mask_u8: Optional[np.ndarray],
    regions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect un-erased dark raw ink (missed furigana, emphasis dots, leading/trailing `――` dashes) strictly floating
    inside bright speech bubbles / white boxes WITHOUT jumping across 2-4px black bubble borders onto hair/frames (`图5`).
    """
    findings: list[dict[str, Any]] = []
    h, w = base_rgb.shape[:2]
    if inpainted_rgb.shape[:2] != (h, w):
        return findings
    inpaint_gray = cv2.cvtColor(inpainted_rgb, cv2.COLOR_RGB2GRAY)
    mask_bin = (mask_u8 > 0) if (mask_u8 is not None and mask_u8.shape == (h, w)) else np.zeros((h, w), dtype=bool)

    for r_idx, reg in enumerate(regions):
        x1, y1, x2, y2 = region_bbox_xyxy(reg)
        bw, bh = x2 - x1, y2 - y1
        if bw < 6 or bh < 6:
            continue
        vis_r = visible_text_of_region(reg).strip()
        d_raw = str(reg.get("direction") or "auto").lower()
        is_horiz = d_raw in ("h", "horizontal", "hr") or (bw > 1.75 * bh and "\n" not in vis_r)
        pad_x = max(28, min(115, int(round(bw * 0.28)))) if is_horiz else max(16, min(48, int(round(bw * 0.28))))
        pad_y = max(12, min(36, int(round(bh * 0.30)))) if is_horiz else max(16, min(48, int(round(bh * 0.22))))
        sx1 = max(0, int(math.floor(x1)) - pad_x)
        sy1 = max(0, int(math.floor(y1)) - pad_y)
        sx2 = min(w, int(math.ceil(x2)) + pad_x)
        sy2 = min(h, int(math.ceil(y2)) + pad_y)

        sub_gray = inpaint_gray[sy1:sy2, sx1:sx2]
        sub_mask = mask_bin[sy1:sy2, sx1:sx2]
        if sub_gray.size == 0:
            continue

        bg_median = float(np.median(sub_gray))
        if bg_median < 225.0:
            continue

        # NEVER MORPH_CLOSE across unmasked dark lines (which jumps over 2-4px bubble outlines)!
        # Instead, keep unmasked dark lines as impenetrable walls, extract the single bubble component,
        # and fill ONLY completely enclosed internal dark holes (missed glyphs/dashes floating inside white paper).
        unmasked_white = cv2.erode((sub_gray >= 224).astype(np.uint8), np.ones((3, 3), dtype=np.uint8))
        walkable = np.where(sub_mask | (unmasked_white > 0), 1, 0).astype(np.uint8)

        cx, cy = region_center(reg)
        lcx = max(0, min(walkable.shape[1] - 1, int(round(cx)) - sx1))
        lcy = max(0, min(walkable.shape[0] - 1, int(round(cy)) - sy1))
        _, w_labels = cv2.connectedComponents(walkable, connectivity=4)
        center_lbl = int(w_labels[lcy, lcx])
        if center_lbl == 0:
            patch = w_labels[max(0, lcy - 5) : min(walkable.shape[0], lcy + 6), max(0, lcx - 5) : min(walkable.shape[1], lcx + 6)]
            nz = patch[patch > 0]
            if nz.size > 0:
                center_lbl = int(np.bincount(nz).argmax())
        if center_lbl == 0:
            continue

        bub_core = (w_labels == center_lbl).astype(np.uint8)
        bub_open = (cv2.dilate(bub_core, np.ones((3, 3), dtype=np.uint8)) > 0) & (sub_mask | (sub_gray >= 205))
        # Flood-fill exterior of `bub_open` from crop borders so ONLY internal holes surrounded 360° by bubble white are filled!
        inv_u8 = np.where(bub_open, 0, 255).astype(np.uint8)
        sh_i, sw_i = inv_u8.shape
        ff_canvas = np.pad(inv_u8, 1, mode="constant", constant_values=255)
        ff_mask = np.zeros((sh_i + 4, sw_i + 4), dtype=np.uint8)
        cv2.floodFill(ff_canvas, ff_mask, (0, 0), 128)
        ext_dark = ff_canvas[1:-1, 1:-1] == 128
        bub_filled = bub_open | (~ext_dark)
        bubble_safe_interior = cv2.erode(
            bub_filled.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
            iterations=1,
        ) > 0

        unmasked_dark = bubble_safe_interior & (~sub_mask) & (sub_gray < 150)
        if not np.any(unmasked_dark):
            continue

        n_comp, _, stats, _ = cv2.connectedComponentsWithStats(unmasked_dark.astype(np.uint8), connectivity=8)
        suspect_rects: list[list[int]] = []
        total_px = 0
        max_comp_px = 0
        max_span_w = max(bw * 0.95, 165.0)
        max_span_h = max(bh * 0.95, 165.0)
        for cid in range(1, n_comp):
            lx, ly, lw, lh, area = stats[cid]
            if 10 <= area <= 3800 and lw <= max_span_w and lh <= max_span_h:
                suspect_rects.append([sx1 + int(lx), sy1 + int(ly), sx1 + int(lx + lw), sy1 + int(ly + lh)])
                total_px += int(area)
                max_comp_px = max(max_comp_px, int(area))

        if suspect_rects:
            ux1 = min(r[0] for r in suspect_rects)
            uy1 = min(r[1] for r in suspect_rects)
            ux2 = max(r[2] for r in suspect_rects)
            uy2 = max(r[3] for r in suspect_rects)
            sev = "error" if (total_px >= 45 or max_comp_px >= 35) else "warning"
            findings.append(
                {
                    "severity": sev,
                    "category": "inpaint_residual_ink",
                    "region_index": r_idx,
                    "message": (
                        f"Region #{r_idx}: detected {len(suspect_rects)} un-erased raw ink component(s) "
                        f"({total_px}px total) floating inside the white bubble/box interior "
                        f"(e.g. missed `――` dash, emphasis dots `・・・`, or furigana at [{ux1}, {uy1}, {ux2}, {uy2}]) — "
                        "wipe cleanly via `mtui_edit_mask_and_inpaint(..., inpaint_method='solid_fill')`!"
                    ),
                    "suggested_mask_rect_xyxy": [max(0, ux1 - 3), max(0, uy1 - 3), min(w, ux2 + 3), min(h, uy2 + 3)],
                    "component_rects_xyxy": suspect_rects[:8],
                }
            )

    return findings

_CREDIT_RE = re.compile(r"(原作\s*[:：]|漫画\s*[:：]|キャラクター原案|角色原案|SBクリエイティブ|SB\s*Creative)", re.IGNORECASE)


def _min_dist_along_segment(dist_map: np.ndarray, p1: tuple[float, float], p2: tuple[float, float]) -> float:
    h, w = dist_map.shape[:2]
    steps = max(8, int(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 2.0))
    vals: list[float] = []
    for i in range(steps + 1):
        t = i / steps
        x = max(0, min(w - 1, int(round(p1[0] + (p2[0] - p1[0]) * t))))
        y = max(0, min(h - 1, int(round(p1[1] + (p2[1] - p1[1]) * t))))
        vals.append(float(dist_map[y, x]))
    return min(vals) if vals else 0.0


def _load_initial_snapshot_regions(paths: PagePaths) -> list[dict[str, Any]]:
    """Load pre-MCP `initial_before_mcp` regions if present so stepped multi-chamber over-merges are caught."""
    if not paths.snapshots_dir.is_dir():
        return []
    prefix = f"{paths.source_path.stem}__"
    for entry in paths.snapshots_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix) and entry.name.endswith("__initial_before_mcp"):
            tj = entry / "translations.json"
            if tj.is_file():
                try:
                    raw = json.loads(tj.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw:
                        val = next(iter(raw.values()))
                        regs = val.get("regions", []) if isinstance(val, dict) else val
                        if isinstance(regs, list):
                            return [r for r in regs if isinstance(r, dict)]
                except Exception:
                    return []
    return []


def _measure_raw_stroke_halfwidth(base_gray: np.ndarray, reg: dict[str, Any], bubble_comp: Optional[np.ndarray] = None) -> float:
    """Measure 78th-percentile distance-transform ridge radius of raw glyphs (supports both dark-on-white & white-on-black bubbles)."""
    h, w = base_gray.shape[:2]
    x1, y1, x2, y2 = region_bbox_xyxy(reg)
    sx1, sy1 = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
    sx2, sy2 = min(w, int(math.ceil(x2))), min(h, int(math.ceil(y2)))
    if sx2 - sx1 < 14 or sy2 - sy1 < 14:
        return 0.0
    sub_b = base_gray[sy1:sy2, sx1:sx2]
    if bubble_comp is not None:
        sub_bub = bubble_comp[sy1:sy2, sx1:sx2]
        if np.any(sub_bub) and float(np.percentile(sub_b[sub_bub], 80)) >= 235.0:
            fg = (sub_b < 125) & sub_bub
        elif float(np.percentile(sub_b, 25)) <= 45.0 and float(np.percentile(sub_b, 92)) >= 195.0:
            fg = sub_b >= 175
        else:
            return 0.0
    else:
        if float(np.percentile(sub_b, 25)) <= 45.0 and float(np.percentile(sub_b, 92)) >= 195.0:
            fg = sub_b >= 175
        else:
            return 0.0
    if int(np.sum(fg)) < 45:
        return 0.0
    # Exclude tiny furigana specks (< 24px area) so furigana doesn't dilute main kanji/kana stroke width
    nc, cl, cs, _ = cv2.connectedComponentsWithStats(fg.astype(np.uint8), connectivity=8)
    main_fg = np.zeros_like(fg, dtype=np.uint8)
    for cid in range(1, nc):
        if cs[cid, cv2.CC_STAT_AREA] >= 24:
            main_fg[cl == cid] = 1
    if int(np.sum(main_fg)) < 40:
        return 0.0
    dt = cv2.distanceTransform(main_fg, cv2.DIST_L2, 5)
    local_max = cv2.dilate(dt, np.ones((3, 3), dtype=np.uint8))
    ridge = dt[(main_fg > 0) & (dt >= local_max - 1e-4) & (dt >= 0.95)]
    if ridge.size < 8:
        return 0.0
    return float(np.percentile(ridge, 78))


def _measure_raw_stroke_stem_p90(
    base_gray: np.ndarray,
    reg: dict[str, Any],
    bubble_comp: Optional[np.ndarray] = None,
) -> float:
    """Measure 90th-percentile distance-transform ridge radius of raw glyph stems.

    Resistant to thin Mincho hairlines diluting vertical stems, frameless text intersecting tiny
    white artwork pockets (`bub_cov < 0.45`), and white-haloed dark glyphs (`p50 > 60`).
    Returns `0.0` when foreground cannot be cleanly isolated (unmeasured).
    """
    h, w = base_gray.shape[:2]
    x1, y1, x2, y2 = region_bbox_xyxy(reg)
    sx1, sy1 = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
    sx2, sy2 = min(w, int(math.ceil(x2))), min(h, int(math.ceil(y2)))
    if sx2 - sx1 < 14 or sy2 - sy1 < 14:
        return 0.0
    sub_b = base_gray[sy1:sy2, sx1:sx2]
    if bubble_comp is not None:
        sub_bub = bubble_comp[sy1:sy2, sx1:sx2]
        if (
            np.any(sub_bub)
            and float(np.mean(sub_bub)) >= 0.45
            and float(np.percentile(sub_b[sub_bub], 80)) >= 235.0
        ):
            fg = (sub_b < 125) & sub_bub
        elif (
            float(np.percentile(sub_b, 25)) <= 45.0
            and float(np.percentile(sub_b, 50)) <= 60.0
            and float(np.percentile(sub_b, 92)) >= 195.0
        ):
            fg = sub_b >= 175
        else:
            return 0.0
    else:
        if (
            float(np.percentile(sub_b, 25)) <= 45.0
            and float(np.percentile(sub_b, 50)) <= 60.0
            and float(np.percentile(sub_b, 92)) >= 195.0
        ):
            fg = sub_b >= 175
        else:
            return 0.0
    if int(np.sum(fg)) < 45:
        return 0.0
    nc, cl, cs, _ = cv2.connectedComponentsWithStats(fg.astype(np.uint8), connectivity=8)
    main_fg = np.zeros_like(fg, dtype=np.uint8)
    for cid in range(1, nc):
        if cs[cid, cv2.CC_STAT_AREA] >= 24:
            main_fg[cl == cid] = 1
    if int(np.sum(main_fg)) < 40:
        return 0.0
    dt = cv2.distanceTransform(main_fg, cv2.DIST_L2, 5)
    local_max = cv2.dilate(dt, np.ones((3, 3), dtype=np.uint8))
    ridge = dt[(main_fg > 0) & (dt >= local_max - 1e-4) & (dt >= 0.95)]
    if ridge.size < 8:
        return 0.0
    return float(np.percentile(ridge, 90))


def measure_page_stroke_metrics(
    base_gray: np.ndarray,
    inp_gray: Optional[np.ndarray],
    regions: list[dict[str, Any]],
) -> tuple[dict[int, float], dict[int, float], float]:
    """Compute per-region raw stroke half-width (`reg_stroke_hw`), p90 stem half-width (`reg_stem_hw`), and `regular_ref_hw`.

    Uses the local enclosed white-patch mask (decoupled from frameless artwork protection) and mirrors
    `_detect_structural_and_border_cv_issues` so `verify-crops` and `qa_auditor` report identical stroke metrics.
    """
    h, w = base_gray.shape[:2]
    reg_to_bub_mask: dict[int, np.ndarray] = {}
    if inp_gray is not None and inp_gray.shape == (h, w) and regions:
        bright = (inp_gray >= 236).astype(np.uint8)
        bright_clean = cv2.erode(bright, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
        n_cc, cc_labels, cc_stats, _ = cv2.connectedComponentsWithStats(bright_clean, connectivity=4)
        valid_enclosed_lbls: set[int] = set()
        for lb_cand in range(1, n_cc):
            x0, y0, bw0, bh0, area0 = cc_stats[lb_cand]
            edges0 = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw0 >= w - 2) + int(y0 + bh0 >= h - 2)
            if area0 > 0.35 * w * h or edges0 > 2 or bw0 < 24 or bh0 < 24:
                continue
            rx1, ry1 = max(0, x0 - 5), max(0, y0 - 5)
            rx2, ry2 = min(w, x0 + bw0 + 5), min(h, y0 + bh0 + 5)
            comp_m = cc_labels[ry1:ry2, rx1:rx2] == lb_cand
            ring_m = (cv2.dilate(comp_m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0) & (~comp_m)
            if np.any(ring_m) and float(np.mean(base_gray[ry1:ry2, rx1:rx2][ring_m] < 175)) >= 0.28:
                valid_enclosed_lbls.add(lb_cand)

        bubble_to_regs: dict[int, list[int]] = {}
        for r_idx, reg in enumerate(regions):
            cx_f, cy_f = region_center(reg)
            ix = max(0, min(w - 1, int(round(cx_f))))
            iy = max(0, min(h - 1, int(round(cy_f))))
            lb = int(cc_labels[iy, ix])
            if lb == 0:
                patch = cc_labels[max(0, iy - 4) : min(h, iy + 5), max(0, ix - 4) : min(w, ix + 5)]
                nz = patch[patch > 0]
                if nz.size > 0:
                    lb = int(np.bincount(nz).argmax())
            if lb in valid_enclosed_lbls:
                bubble_to_regs.setdefault(lb, []).append(r_idx)

        for lbl, r_list in bubble_to_regs.items():
            this_bub = cv2.dilate((cc_labels == lbl).astype(np.uint8), np.ones((5, 5), dtype=np.uint8), iterations=1) > 0
            for r_idx in r_list:
                reg_to_bub_mask[r_idx] = this_bub

    reg_stroke_hw: dict[int, float] = {}
    reg_stem_hw: dict[int, float] = {}
    regular_samples: list[float] = []
    for r_idx, reg in enumerate(regions):
        bub_m = reg_to_bub_mask.get(r_idx)
        shw = _measure_raw_stroke_halfwidth(base_gray, reg, bub_m)
        if shw > 0:
            reg_stroke_hw[r_idx] = shw
            ff_cur = str(reg.get("font_family") or "").strip()
            if ff_cur.split("::")[0].strip() in ("FZHei-B01", "") and not region_is_bold(reg):
                regular_samples.append(shw)
        stem = _measure_raw_stroke_stem_p90(base_gray, reg, bub_m)
        if stem > 0:
            reg_stem_hw[r_idx] = stem
    regular_ref_hw = min(2.60, float(np.median(regular_samples))) if regular_samples else 2.20
    return reg_stroke_hw, reg_stem_hw, regular_ref_hw


def measure_page_stroke_widths(
    base_gray: np.ndarray,
    inp_gray: Optional[np.ndarray],
    regions: list[dict[str, Any]],
) -> tuple[dict[int, float], float]:
    """Compute per-region raw stroke half-width (`reg_stroke_hw`) and page regular reference (`regular_ref_hw`).

    Preserves the legacy 2-tuple return contract; see `measure_page_stroke_metrics` for `(reg_stroke_hw, reg_stem_hw, regular_ref_hw)`.
    """
    reg_stroke_hw, _, regular_ref_hw = measure_page_stroke_metrics(base_gray, inp_gray, regions)
    return reg_stroke_hw, regular_ref_hw


_INHERENTLY_HEAVY_NO_BOLD_KEYS = (
    "fzjianzhi",
    "tankuin",
    "dfzongyi",
    "dffangyuan",
    "fzcusong",
    "fzlantingyuan-b",
    "::heavy",
    "::bold",
    "cangsang",
    "guyin",
    "songhei",
    "zhsrxt",
)
_NO_FAUX_BOLD_REQUIRED_KEYS = _INHERENTLY_HEAVY_NO_BOLD_KEYS + ("fzbeiweikaishu",)


def _analyze_raw_glyph_font_geometry(
    base_gray: np.ndarray,
    inp_gray: Optional[np.ndarray],
    reg: dict[str, Any],
) -> Optional[dict[str, float]]:
    """Measure lossless 4x-cubic sub-pixel photometric stroke-width modulation, kana hv-ratio, corner sharpness, and stroke fragmentation."""
    h, w = base_gray.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in region_bbox_xyxy(reg)]
    x1, y1 = max(0, x1 - 2), max(0, y1 - 2)
    x2, y2 = min(w, x2 + 2), min(h, y2 + 2)
    if x2 - x1 < 16 or y2 - y1 < 16:
        return None

    sub = base_gray[y1:y2, x1:x2].astype(np.float32)
    p50, p85 = np.percentile(sub, [50, 85])
    if p85 >= 220.0 and int(np.sum(sub < 155.0)) >= 45:
        bg_val = float(np.percentile(sub, 88))
        ink_val = float(np.percentile(sub, 2))
        ink = np.clip((bg_val - sub) / max(80.0, bg_val - ink_val), 0.0, 1.0)
        if inp_gray is not None and inp_gray.shape == base_gray.shape:
            sub_i = inp_gray[y1:y2, x1:x2].astype(np.float32)
            border_keep = (sub_i < 130.0) & ((sub_i - sub) < 45.0)
            ink[border_keep] = 0.0
    elif p50 <= 85.0 and float(np.percentile(sub, 92)) >= 185.0:
        bg_val = float(np.percentile(sub, 15))
        ink_val = float(np.percentile(sub, 97))
        ink = np.clip((sub - bg_val) / max(80.0, ink_val - bg_val), 0.0, 1.0)
    else:
        return None

    fg1x = (ink >= 0.36).astype(np.uint8)
    if int(fg1x.sum()) < 45:
        return None

    n0, l0, s0, _ = cv2.connectedComponentsWithStats(fg1x, connectivity=8)
    clean1x = np.zeros_like(fg1x)
    for i in range(1, n0):
        if s0[i, cv2.CC_STAT_AREA] >= 6:
            clean1x[l0 == i] = 1

    # Strip narrow furigana columns via vertical column x-projection
    col_proj = np.sum(clean1x, axis=0)
    active_x = col_proj >= max(2, int(0.03 * clean1x.shape[0]))
    raw_spans: list[tuple[int, int]] = []
    in_span = False
    st = 0
    for xi, val in enumerate(active_x):
        if val and not in_span:
            st = xi
            in_span = True
        elif not val and in_span:
            if xi - st >= 5:
                raw_spans.append((st, xi))
            in_span = False
    if in_span and len(active_x) - st >= 5:
        raw_spans.append((st, len(active_x)))
    if not raw_spans:
        return None

    # Split wide spans (`>36px`) ONLY when a narrow (`<=12px`) side-track consists of >=3 tiny disconnected emphasis dots (`み・ん・な・や`)
    col_spans: list[tuple[int, int]] = []
    for s0, s1 in raw_spans:
        if s1 - s0 > 36:
            sub_p = col_proj[s0:s1]
            pk = float(np.max(sub_p))
            split_done = False
            for vi in range(5, len(sub_p) - 5):
                if sub_p[vi] <= max(2.0, 0.10 * pk):
                    left_w, right_w = vi, (s1 - s0) - vi
                    if min(left_w, right_w) <= 12 and max(left_w, right_w) >= 20:
                        side_slice = clean1x[:, s0 : s0 + vi] if left_w < right_w else clean1x[:, s0 + vi + 1 : s1]
                        n_sc, _, sc_s, _ = cv2.connectedComponentsWithStats(side_slice, connectivity=8)
                        small_dots = sum(1 for k in range(1, n_sc) if 4 <= sc_s[k, cv2.CC_STAT_AREA] <= 120 and sc_s[k, cv2.CC_STAT_HEIGHT] <= 16)
                        if small_dots >= 3 and (n_sc - 1) == small_dots:
                            col_spans.append((s0, s0 + vi) if left_w > right_w else (s0 + vi + 1, s1))
                            split_done = True
                            break
            if not split_done:
                col_spans.append((s0, s1))
        else:
            col_spans.append((s0, s1))

    max_col_w = max(sp[1] - sp[0] for sp in col_spans)
    main_col_spans = [sp for sp in col_spans if (sp[1] - sp[0]) >= max(11, 0.50 * max_col_w)] or col_spans
    col_mask = np.zeros_like(clean1x)
    for cx1, cx2 in main_col_spans:
        col_mask[:, max(0, cx1 - 1) : min(clean1x.shape[1], cx2 + 1)] = 1
    ink_main = ink * col_mask.astype(np.float32)
    fg_main = (ink_main >= 0.36).astype(np.uint8)
    if int(fg_main.sum()) < 45:
        return None

    est_char_w = float(np.median([sp[1] - sp[0] for sp in main_col_spans]))
    char_boxes: list[tuple[int, int, int, int]] = []
    for cx1, cx2 in main_col_spans:
        col_slice = fg_main[:, cx1:cx2]
        y_proj = np.sum(col_slice, axis=1) > 0
        y_bridged = (
            cv2.morphologyEx(
                y_proj.astype(np.uint8).reshape(-1, 1),
                cv2.MORPH_CLOSE,
                np.ones((4, 1), dtype=np.uint8),
            ).ravel()
            > 0
        )
        in_c = False
        cy0 = 0
        for yi, v in enumerate(y_bridged):
            if v and not in_c:
                cy0 = yi
                in_c = True
            elif not v and in_c:
                ch = yi - cy0
                if 0.32 * est_char_w <= ch <= 1.45 * est_char_w:
                    char_boxes.append((cx1, cy0, cx2, yi))
                elif ch > 1.45 * est_char_w:
                    n_split = max(2, int(round(ch / max(est_char_w, 12.0))))
                    step = ch / n_split
                    for k in range(n_split):
                        char_boxes.append((cx1, int(round(cy0 + k * step)), cx2, int(round(cy0 + (k + 1) * step))))
                in_c = False
        if in_c:
            ch = len(y_bridged) - cy0
            if 0.32 * est_char_w <= ch <= 1.45 * est_char_w:
                char_boxes.append((cx1, cy0, cx2, len(y_bridged)))

    solid_fg = (ink_main >= 0.55).astype(np.uint8)
    n_solid_raw, _, s_raw, _ = cv2.connectedComponentsWithStats(solid_fg, connectivity=8)
    cnt_raw = sum(1 for i in range(1, n_solid_raw) if s_raw[i, cv2.CC_STAT_AREA] >= 5)
    src_clean = re.sub(r"[\s…‥・！？!?、。,.「」『』（）()【】―─ー]+", "", str(reg.get("text") or ""))
    n_chars_ref = max(1, len(char_boxes), len(src_clean))
    solid_cc_per_char = float(cnt_raw) / n_chars_ref

    # Guard memory on giant SFX boxes: cap upsampled patch dimensions
    if ink_main.shape[0] * ink_main.shape[1] > 180_000:
        return None
    ink4 = cv2.resize(ink_main, (0, 0), fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
    fg4 = (ink4 >= 0.46).astype(np.uint8)
    dt4 = cv2.distanceTransform(fg4, cv2.DIST_L2, 5) / 4.0
    lmax4 = cv2.dilate(dt4, np.ones((5, 5), dtype=np.uint8))
    skel4 = (fg4 > 0) & (dt4 >= lmax4 - 0.02) & (dt4 >= 0.45)

    gx4 = cv2.Sobel(ink4, cv2.CV_32F, 1, 0, ksize=5)
    gy4 = cv2.Sobel(ink4, cv2.CV_32F, 0, 1, ksize=5)
    jxx = cv2.GaussianBlur(gx4 * gx4, (13, 13), 3.0)
    jxy = cv2.GaussianBlur(gx4 * gy4, (13, 13), 3.0)
    jyy = cv2.GaussianBlur(gy4 * gy4, (13, 13), 3.0)
    theta_t = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy) + (np.pi / 2.0)
    theta_t = np.where(theta_t > np.pi / 2.0, theta_t - np.pi, theta_t)
    deg_t = np.degrees(theta_t)
    aniso = np.hypot(jxx - jyy, 2.0 * jxy) / (jxx + jyy + 1e-6)

    skel_dir = skel4 & (aniso >= 0.30)
    sy_idx, sx_idx = np.nonzero(skel_dir)
    if sy_idx.size < 30:
        return None
    ang_t_pts = theta_t[sy_idx, sx_idx]
    nx_pts = -np.sin(ang_t_pts)
    ny_pts = np.cos(ang_t_pts)
    w_photo = np.zeros(sy_idx.size, dtype=np.float32)
    h4, w4 = ink4.shape[:2]
    for sign in (-1.0, 1.0):
        alive = np.ones(sy_idx.size, dtype=bool)
        for step_i in range(1, 21):
            px = np.clip(np.rint(sx_idx + sign * step_i * nx_pts).astype(np.int32), 0, w4 - 1)
            py = np.clip(np.rint(sy_idx + sign * step_i * ny_pts).astype(np.int32), 0, h4 - 1)
            val = ink4[py, px]
            alive &= val >= 0.10
            w_photo += np.where(alive, val * 0.25, 0.0)
    w_photo += ink4[sy_idx, sx_idx] * 0.25

    w_map4 = np.zeros_like(dt4)
    w_map4[sy_idx, sx_idx] = w_photo
    horiz_pts = skel_dir & (np.abs(deg_t) <= 22.0)
    vert_pts = skel_dir & (np.abs(deg_t) >= 68.0)

    shw_p50 = float(np.median(w_photo)) * 0.5
    h_hw_all = float(np.median(w_map4[horiz_pts])) * 0.5 if int(np.sum(horiz_pts)) >= 20 else shw_p50
    v_hw_all = float(np.median(w_map4[vert_pts])) * 0.5 if int(np.sum(vert_pts)) >= 20 else shw_p50
    hv_all = h_hw_all / max(v_hw_all, 1e-4)

    kana_p15_p85: list[float] = []
    kana_cv: list[float] = []
    kana_h_hw: list[float] = []
    all_char_p15_p85: list[float] = []
    for bx1, by1, bx2, by2 in char_boxes:
        s4_x1, s4_y1, s4_x2, s4_y2 = bx1 * 4, by1 * 4, bx2 * 4, by2 * 4
        c_skel = skel_dir[s4_y1:s4_y2, s4_x1:s4_x2]
        n_sk = int(np.sum(c_skel))
        if n_sk < 20:
            continue
        c_w = w_map4[s4_y1:s4_y2, s4_x1:s4_x2][c_skel]
        norm_skel_len = (n_sk / 4.0) / max(est_char_w, 12.0)
        p15_c, _, p85_c = np.percentile(c_w, [15, 50, 85])
        ratio_c = float(p15_c / max(p85_c, 1e-4))
        cv_c = float(np.std(c_w) / max(np.mean(c_w), 1e-4))
        all_char_p15_p85.append(ratio_c)
        if 0.70 <= norm_skel_len <= 2.35:
            kana_p15_p85.append(ratio_c)
            kana_cv.append(cv_c)
            c_h = horiz_pts[s4_y1:s4_y2, s4_x1:s4_x2]
            if int(np.sum(c_h)) >= 8:
                kana_h_hw.append(float(np.median(w_map4[s4_y1:s4_y2, s4_x1:s4_x2][c_h])) * 0.5)

    kana_unif = float(np.median(kana_p15_p85)) if kana_p15_p85 else (float(np.median(all_char_p15_p85)) if all_char_p15_p85 else 0.6)
    kana_cv_med = float(np.median(kana_cv)) if kana_cv else 0.25
    kana_hv = (float(np.median(kana_h_hw)) / max(v_hw_all, 1e-4)) if kana_h_hw else hv_all

    contours, _ = cv2.findContours(fg4, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    sharp_corners = 0
    total_perim = 0.0
    for cnt in contours:
        per = cv2.arcLength(cnt, True)
        if per < 40:
            continue
        total_perim += per
        approx = cv2.approxPolyDP(cnt, 2.2, True)
        pts = approx.reshape(-1, 2).astype(np.float32)
        m = len(pts)
        if m < 4:
            continue
        for k in range(m):
            v1 = pts[(k - 1) % m] - pts[k]
            v2 = pts[(k + 1) % m] - pts[k]
            n1, n2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
            if n1 < 3.5 or n2 < 3.5:
                continue
            cos_a = float(np.dot(v1, v2) / (n1 * n2))
            ang = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
            if ang <= 108.0 and (v1[0] * v2[1] - v1[1] * v2[0]) < 0:
                sharp_corners += 1
    corner_per_100px = (sharp_corners / max(total_perim / 4.0, 1.0)) * 100.0

    return {
        "cw": est_char_w,
        "hv_all": hv_all,
        "kana_hv": kana_hv,
        "kana_unif": kana_unif,
        "kana_cv": kana_cv_med,
        "n_kana": float(len(kana_p15_p85)),
        "solid_cc": solid_cc_per_char,
        "sharp_c": corner_per_100px,
        "n_chars": float(n_chars_ref),
    }


def _estimate_raw_main_char_size(
    base_gray: np.ndarray,
    inp_gray: Optional[np.ndarray],
    reg: dict[str, Any],
    pre_geom: Optional[dict[str, float]] = None,
) -> float:
    """Estimate primary raw Japanese glyph size in pixels (excluding furigana & emphasis dots) for `图7` scale matching."""
    x1, y1, x2, y2 = [int(round(v)) for v in region_bbox_xyxy(reg)]
    bw, bh = x2 - x1, y2 - y1
    vis_t = visible_text_of_region(reg).strip()
    d_r = str(reg.get("direction") or "auto").lower()
    is_horiz_single = (d_r in ("h", "horizontal", "hr") or (bw > 1.65 * max(bh, 1) and "\n" not in vis_t)) and "\n" not in vis_t
    if is_horiz_single and bw >= 28 and bh >= 16:
        h, w = base_gray.shape[:2]
        sx1, sy1 = max(0, x1), max(0, y1)
        sx2, sy2 = min(w, x2), min(h, y2)
        sub = base_gray[sy1:sy2, sx1:sx2]
        if sub.size > 0 and float(np.percentile(sub, 80)) >= 220.0:
            fg = (sub < 140).astype(np.uint8)
            if inp_gray is not None and inp_gray.shape == base_gray.shape:
                sub_i = inp_gray[sy1:sy2, sx1:sx2]
                fg[(sub_i < 130) & ((sub_i.astype(np.int16) - sub.astype(np.int16)) < 45)] = 0
            row_proj = np.sum(fg, axis=1)
            act_y = row_proj >= max(3, int(0.04 * fg.shape[1]))
            spans_y: list[int] = []
            in_s = False
            s0 = 0
            for yi, v in enumerate(act_y):
                if v and not in_s:
                    s0 = yi
                    in_s = True
                elif not v and in_s:
                    if yi - s0 >= 8:
                        spans_y.append(yi - s0)
                    in_s = False
            if in_s and len(act_y) - s0 >= 8:
                spans_y.append(len(act_y) - s0)
            if spans_y:
                return float(max(spans_y))
    geom = pre_geom if pre_geom is not None else _analyze_raw_glyph_font_geometry(base_gray, inp_gray, reg)
    if geom is not None:
        return float(geom["cw"])
    # Fallback for tightly-cropped heavy shout/kiai glyphs (`图7` `ていっ` where thick ink exceeds 15% of tight OCR bbox so `p85 < 220`)
    h, w = base_gray.shape[:2]
    px1, py1 = max(0, x1 - 6), max(0, y1 - 6)
    px2, py2 = min(w, x2 + 6), min(h, y2 + 6)
    sub_fb = base_gray[py1:py2, px1:px2]
    if sub_fb.size > 0 and float(np.percentile(sub_fb, 92)) >= 235.0:
        fg_fb = (sub_fb < 140).astype(np.uint8)
        if inp_gray is not None and inp_gray.shape == base_gray.shape:
            sub_ifb = inp_gray[py1:py2, px1:px2]
            fg_fb[(sub_ifb < 130) & ((sub_ifb.astype(np.int16) - sub_fb.astype(np.int16)) < 45)] = 0
        c_proj = np.sum(fg_fb, axis=0) >= max(3, int(0.06 * fg_fb.shape[0]))
        spans_x: list[int] = []
        in_x = False
        sx0 = 0
        for xi, vx in enumerate(c_proj):
            if vx and not in_x:
                sx0 = xi
                in_x = True
            elif not vx and in_x:
                if xi - sx0 >= 8:
                    spans_x.append(xi - sx0)
                in_x = False
        if in_x and len(c_proj) - sx0 >= 8:
            spans_x.append(len(c_proj) - sx0)
        if spans_x:
            mx_w = max(spans_x)
            main_ws = [sw_i for sw_i in spans_x if sw_i >= max(12, 0.50 * mx_w)] or spans_x
            return float(np.median(main_ws))
    return 0.0


def _detect_structural_and_border_cv_issues(
    base_rgb: np.ndarray,
    inpainted_rgb: np.ndarray,
    mask_u8: Optional[np.ndarray],
    regions: list[dict[str, Any]],
    *,
    rendered_rgb: Optional[np.ndarray] = None,
    initial_regions: Optional[list[dict[str, Any]]] = None,
    yuzumarker_page_cache: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Detect:
    1) `same_bubble_split_regions`: >=2 horizontal-band-aligned regions split inside a single open speech bubble;
    2) `unfilled_connected_lobe`: multi-chamber / stepped connected bubble where raw text was erased in one chamber but merged into another;
    3) `bubble_border_eroded`: mask overflow that cut a gap through a continuous black bubble/panel border;
    4) `untranslated_boxed_sfx`: enclosed white bubble or white SFX box with crisp untranslated strokes and 0 regions;
    5) `cover_credit_translation`: translated author/illustrator credits on color cover/insert pages;
    6) `text_overflows_bubble`: rendered glyph ink touching or crossing out of enclosed white bubble/box borders;
    7) `frameless_artwork_scar`: flat `solid_fill`/`telea` gray patch ruining speed lines or limb contours;
    8) `raw_bold_mismatch`: heavy raw strokes inside bubble rendered with unbolded regular-weight font.
    """
    findings: list[dict[str, Any]] = []
    h, w = base_rgb.shape[:2]
    if inpainted_rgb.shape[:2] != (h, w):
        return findings
    base_gray = cv2.cvtColor(base_rgb, cv2.COLOR_RGB2GRAY)
    inp_gray = cv2.cvtColor(inpainted_rgb, cv2.COLOR_RGB2GRAY)
    res_gray = (
        cv2.cvtColor(rendered_rgb, cv2.COLOR_RGB2GRAY)
        if (rendered_rgb is not None and rendered_rgb.shape[:2] == (h, w))
        else None
    )
    mask_bin = (mask_u8 > 0) if (mask_u8 is not None and mask_u8.shape == (h, w)) else np.zeros((h, w), dtype=bool)

    # Check 5: Color cover / title insert author credits
    hsv = cv2.cvtColor(base_rgb, cv2.COLOR_RGB2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))
    if mean_sat >= 22.0:
        for r_idx, reg in enumerate(regions):
            combined = f"{reg.get('text', '')} {visible_text_of_region(reg)}"
            if _CREDIT_RE.search(combined):
                findings.append(
                    {
                        "severity": "warning",
                        "category": "cover_credit_translation",
                        "region_index": r_idx,
                        "message": (
                            f"Region #{r_idx} translates cover/color-insert author credits ({visible_text_of_region(reg)!r}); "
                            "delete region with restore_artwork=True to preserve original cover graphic design."
                        ),
                    }
                )

    # Segment enclosed bright white speech bubbles / white boxes on inpainted image
    bright = (inp_gray >= 236).astype(np.uint8)
    bright_clean = cv2.erode(bright, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
    n_cc, cc_labels, cc_stats, _ = cv2.connectedComponentsWithStats(bright_clean, connectivity=4)

    valid_enclosed_lbls: set[int] = set()
    for lb_cand in range(1, n_cc):
        x0, y0, bw0, bh0, area0 = cc_stats[lb_cand]
        edges0 = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw0 >= w - 2) + int(y0 + bh0 >= h - 2)
        if area0 > 0.35 * w * h or edges0 > 2 or bw0 < 24 or bh0 < 24:
            continue
        rx1, ry1 = max(0, x0 - 5), max(0, y0 - 5)
        rx2, ry2 = min(w, x0 + bw0 + 5), min(h, y0 + bh0 + 5)
        comp_m = cc_labels[ry1:ry2, rx1:rx2] == lb_cand
        ring_m = (cv2.dilate(comp_m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0) & (~comp_m)
        # Enclosed speech bubble / narration box (including panel-edge-clipped bubbles) has dark border along >= 28% of its perimeter
        if np.any(ring_m) and float(np.mean(base_gray[ry1:ry2, rx1:rx2][ring_m] < 175)) >= 0.28:
            valid_enclosed_lbls.add(lb_cand)

    def _lookup_bubble_label(cx_f: float, cy_f: float) -> int:
        ix = max(0, min(w - 1, int(round(cx_f))))
        iy = max(0, min(h - 1, int(round(cy_f))))
        lb = int(cc_labels[iy, ix])
        if lb == 0:
            patch = cc_labels[max(0, iy - 4) : min(h, iy + 5), max(0, ix - 4) : min(w, ix + 5)]
            nz = patch[patch > 0]
            if nz.size > 0:
                lb = int(np.bincount(nz).argmax())
        return lb if lb in valid_enclosed_lbls else 0

    bubble_to_regs: dict[int, list[tuple[int, float, float]]] = {}
    reg_centers: list[tuple[float, float]] = []
    for r_idx, reg in enumerate(regions):
        cx, cy = region_center(reg)
        reg_centers.append((cx, cy))
        lbl = _lookup_bubble_label(cx, cy)
        if lbl > 0:
            bubble_to_regs.setdefault(lbl, []).append((r_idx, cx, cy))

    init_bubble_to_regs: dict[int, list[tuple[dict[str, Any], float, float]]] = {}
    for ireg in initial_regions or []:
        icx, icy = region_center(ireg)
        ilbl = _lookup_bubble_label(icx, icy)
        if ilbl > 0 and str(ireg.get("text", "") or "").strip():
            init_bubble_to_regs.setdefault(ilbl, []).append((ireg, icx, icy))

    text_ink_all = (
        (inp_gray.astype(np.int16) - res_gray.astype(np.int16)) >= 45
        if res_gray is not None
        else None
    )
    safe_white_interior = (
        cv2.erode((inp_gray >= 218).astype(np.uint8), np.ones((3, 3), dtype=np.uint8), iterations=1) > 0
        if res_gray is not None
        else None
    )

    # Measure raw stroke & p90 stem half-widths across enclosed-bubble & dark-bubble regions for bold / CuSong guardrails
    reg_to_bub_mask: dict[int, np.ndarray] = {}
    for lbl, r_list in bubble_to_regs.items():
        this_bub = cv2.dilate((cc_labels == lbl).astype(np.uint8), np.ones((5, 5), dtype=np.uint8), iterations=1) > 0
        for r_idx, _, _ in r_list:
            reg_to_bub_mask[r_idx] = this_bub
    reg_stroke_hw, reg_stem_hw, regular_ref_hw = measure_page_stroke_metrics(base_gray, inp_gray, regions)

    for r_idx, shw in reg_stroke_hw.items():
        reg = regions[r_idx]
        vis_clean = re.sub(r"[\W_]+", "", visible_text_of_region(reg))
        if len(vis_clean) < 1:
            continue
        eff_ff = str(reg.get("font_family") or "").strip()
        inherently_heavy = any(k in eff_ff.lower() for k in _NO_FAUX_BOLD_REQUIRED_KEYS)
        if not inherently_heavy and not region_is_bold(reg):
            rx1, ry1, rx2, ry2 = region_bbox_xyxy(reg)
            n_cols = max(1, len([ln for ln in visible_text_of_region(reg).split("\n") if ln.strip()]))
            est_col_w = max(18.0, (rx2 - rx1) / n_cols)
            rel_stroke = (2.0 * shw) / est_col_w
            if (
                shw >= 2.95
                or (shw >= 2.52 and shw >= regular_ref_hw * 1.18)
                or (shw >= 2.38 and rel_stroke >= 0.165 and shw >= regular_ref_hw * 1.12)
            ):
                rb_sev = "error" if (shw >= 3.35 and shw >= regular_ref_hw * 1.32) else "warning"
                findings.append(
                    {
                        "severity": rb_sev,
                        "category": "raw_bold_mismatch",
                        "region_index": int(r_idx),
                        "message": (
                            f"Region #{r_idx} ({visible_text_of_region(reg)!r}, font={eff_ff!r}) has heavy raw strokes "
                            f"(stroke_width≈{2.0 * shw:.1f}px vs regular≈{2.0 * regular_ref_hw:.1f}px) but `bold` is False — "
                            "verify raw image and switch to native bold font (e.g. `FZLanTingYuan-B-GBK` / `FZCuSong-B09` / `Source Han Sans SC::Bold`) or enable `bold=True`!"
                        ),
                    }
                )

    # Precompute OpenCV raw glyph geometry once per dialogue region (`<=80` chars) for font consistency & scale checks
    reg_raw_geom: dict[int, dict[str, float]] = {}
    for r_idx, reg in enumerate(regions):
        vt = visible_text_of_region(reg).strip()
        if vt and len(vt) <= 80:
            gm = _analyze_raw_glyph_font_geometry(base_gray, inp_gray, reg)
            if gm is not None:
                reg_raw_geom[r_idx] = gm

    # Check 9: Local lossless OpenCV sub-pixel font-family mismatch detector (prevents remote LLM font hallucinations!)
    for r_idx, reg in enumerate(regions):
        vis_txt_f = visible_text_of_region(reg).strip()
        if not vis_txt_f:
            continue
        eff_ff = str(reg.get("font_family") or "FZHei-B01").strip() or "FZHei-B01"
        is_b = region_is_bold(reg)
        eff_ff_l = eff_ff.lower()
        if is_b and any(k in eff_ff_l for k in _INHERENTLY_HEAVY_NO_BOLD_KEYS):
            findings.append(
                {
                    "severity": "error",
                    "category": "inherently_heavy_font_bolded",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) enables `bold=True` on inherently heavy font {eff_ff!r} — "
                        "synthetic faux-bolding clogs counters; set `bold=False`!"
                    ),
                }
            )
        elif is_b and "fzbeiweikaishu" in eff_ff_l:
            findings.append(
                {
                    "severity": "warning",
                    "category": "inherently_heavy_font_bolded",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) enables synthetic `bold=True` on chiseled Kaisho font {eff_ff!r} — "
                        "Qt faux-bolding bloats and clogs brush counters; set `bold=False`!"
                    ),
                }
            )
        if is_b and "fzzhunyuan-m02" in eff_ff_l:
            findings.append(
                {
                    "severity": "error",
                    "category": "unjustified_faux_bold",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) enables synthetic faux `bold=True` on {eff_ff!r} — "
                        "never faux-bold `FZZhunYuan-M02`; use native bold `FZLanTingYuan-B-GBK` with `bold=False` (or unset `bold`)!"
                    ),
                }
            )
        # Skip dense novel/colophon text blocks (>80 visible chars) for dialogue bubble font-geometry checks
        if len(vis_txt_f) > 80:
            continue

        # 9a. Local YuzuMarker (ResNet-50 4x Ensemble) exact Japanese commercial font detector (primary ground-truth check)
        ym_pred = lookup_cached_region_prediction(yuzumarker_page_cache, reg, r_idx)
        if ym_pred is not None:
            pred_conf = float(ym_pred.get("pred_conf") or 0.0)
            has_prop = bool(ym_pred.get("neighbor_propagated_family"))
            eff_pred_fam = str(ym_pred.get("neighbor_propagated_family") or ym_pred.get("pred_family") or "")
            top_fonts = ym_pred.get("top_fonts") or []
            top1_entry = top_fonts[0] if top_fonts else {}
            top1_name = str(top1_entry.get("font") or "unknown")
            top1_prob = float(top1_entry.get("prob") or 0.0)
            top1_fam = str(top1_entry.get("family") or "")
            if not top1_fam and top1_name and top1_name != "unknown":
                top1_fam = classify_vcb_font_entry({"path": top1_name, "serif": False})[0]
            top1_matches_eff_fam = bool(top1_fam and top1_fam == eff_pred_fam)
            bx1_ym, by1_ym, bx2_ym, by2_ym = region_bbox_xyxy(reg)
            reg_wh_ym = (max(1.0, bx2_ym - bx1_ym), max(1.0, by2_ym - by1_ym))
            src_txt_ym = str(reg.get("text") or "").strip()
            shw_ym = reg_stroke_hw.get(r_idx)
            stem_ym = reg_stem_hw.get(r_idx, 0.0)
            rec_ff, rec_bold, acceptable_fams = recommend_mtui_font_for_prediction(
                ym_pred,
                raw_stroke_hw=shw_ym,
                raw_stem_hw=stem_ym,
                bbox_wh=reg_wh_ym,
                source_text=src_txt_ym,
                page_wh=(w, h),
            )
            narrow_kana_stretch = is_narrow_kana_stretch_suspect(
                ym_pred,
                bbox_wh=reg_wh_ym,
                source_text=src_txt_ym,
                raw_stem_hw=(stem_ym if stem_ym > 0.0 else None),
                page_wh=(w, h),
            )
            cur_fam = classify_current_mtui_font(eff_ff)
            fam_probs = ym_pred.get("family_probs") or {}
            mincho_prob = float(fam_probs.get("MINCHO") or 0.0)
            cur_fam_prob = float(fam_probs.get(cur_fam) or 0.0)
            antique_prob = float(fam_probs.get("ANTIQUE") or 0.0)

            is_false_songti = cur_fam == "MINCHO" and mincho_prob < 0.15 and pred_conf >= 0.55 and top1_prob >= 0.28
            # Also catch cases like `图3` (`V17_019 #3`: `RodinHappyPro-B` top1=0.51, `ROUND_GOTHIC=0.51 + POP_CHISEL=0.48`, while `ANTIQUE=0.00`!),
            # but NEVER let a top-1 font from family A trigger/escalate a mismatch for a DIFFERENT summed family B!
            is_high_conf_mismatch = cur_fam not in acceptable_fams and (
                (pred_conf >= 0.68 and top1_prob >= 0.28)
                or (cur_fam_prob < 0.12 and top1_matches_eff_fam and top1_prob >= 0.42)
                or has_prop
            )
            if is_false_songti or is_high_conf_mismatch:
                sev = (
                    "error"
                    if (is_false_songti or pred_conf >= 0.82 or (top1_matches_eff_fam and top1_prob >= 0.48) or has_prop)
                    else "warning"
                )
                narrow_note = ""
                if narrow_kana_stretch and not is_false_songti:
                    sev = "warning"
                    narrow_note = (
                        " [narrow pure-kana strip may be aspect-stretched by YuzuMarker — "
                        "keep 'FZHei-B01' if Raw kana matches regular Antique dialogue, "
                        f"only switch to {rec_ff!r} if 1:1 Raw strokes genuinely confirm it]"
                    )
                prop_note = (
                    f" [neighbor-propagated from #{ym_pred.get('neighbor_source_ridx')}]"
                    if has_prop
                    else ""
                )
                findings.append(
                    {
                        "severity": sev,
                        "category": "yuzumarker_font_mismatch",
                        "region_index": int(r_idx),
                        "detected_jp_font": top1_name,
                        "detected_family": eff_pred_fam,
                        "pred_conf": round(pred_conf, 4),
                        "top1_prob": round(top1_prob, 4),
                        "recommended_font_family": rec_ff,
                        "recommended_bold": rec_bold,
                        "message": (
                            f"Region #{r_idx} ({vis_txt_f!r}) uses {eff_ff!r} ({cur_fam}), "
                            f"but local YuzuMarker 4x Ensemble detected raw Japanese font {top1_name!r} "
                            f"(top1={top1_prob:.2f}, family={eff_pred_fam} {pred_conf:.2f}{prop_note}) -> "
                            f"recommended {rec_ff!r} (bold={rec_bold})!{narrow_note}"
                        ),
                    }
                )
            else:
                # Check 9a-bold: `bold_prob` from `engine_worker.py` ALREADY equals `wp['bold'] + wp['heavy']`!
                # Never add `bold_p + heavy_p` (that double-counts `heavy_prob` and creates bogus errors on low-conf Antique splits).
                bold_p = float(ym_pred.get("bold_prob") or 0.0)
                heavy_p = float(ym_pred.get("heavy_prob") or 0.0)
                eff_bold_p = max(bold_p, heavy_p)
                n_clean_raw = ym_pred.get("n_clean_chars")
                n_clean_val = int(n_clean_raw) if n_clean_raw is not None else None
                shw_cur = reg_stroke_hw.get(r_idx, 0.0)
                inherently_heavy_cur = any(k in eff_ff.lower() for k in _NO_FAUX_BOLD_REQUIRED_KEYS)
                is_cur_songti = any(k in eff_ff.lower() for k in ("fzyasong", "source han serif"))
                rec_is_native_heavy_mincho = "fzcusong" in rec_ff.lower()
                raw_needs_bold_or_heavy = bool(
                    rec_bold
                    or any(k in rec_ff.lower() for k in ("::bold", "::heavy", "fzcusong", "fzlantingyuan-b", "fzjianzhi"))
                )
                # Iron gates:
                # 1) `cur_fam in acceptable_fams` — NEVER let a low-conf prediction from another family force bold onto an incompatible family (e.g. POP_CHISEL -> Songti).
                # 2) NEVER suggest Qt faux-bold (`bold=True`) on Songti (`FZYaSong` / `Source Han Serif`); only recommend native heavy Mincho (`FZCuSong-B09`, bold=False).
                songti_bold_blocked = is_cur_songti and not rec_is_native_heavy_mincho
                if (
                    raw_needs_bold_or_heavy
                    and cur_fam in acceptable_fams
                    and not songti_bold_blocked
                    and not is_b
                    and not inherently_heavy_cur
                    and eff_pred_fam != "ANTIQUE"
                    and antique_prob < 0.25
                    and eff_bold_p >= 0.78
                    and top1_prob >= 0.28
                ):
                    is_high_conf_bold = bool(
                        eff_bold_p >= 0.90
                        and antique_prob < 0.12
                        and (top1_prob >= 0.40 or pred_conf >= 0.78 or heavy_p >= 0.45)
                    )
                    if is_high_conf_bold and n_clean_val is not None and n_clean_val <= 2 and not has_prop:
                        if not (
                            top1_prob >= 0.45
                            or heavy_p >= 0.55
                            or shw_cur >= max(2.55, regular_ref_hw * 1.18)
                        ):
                            is_high_conf_bold = False
                    if narrow_kana_stretch:
                        is_high_conf_bold = False
                    b_sev = "error" if is_high_conf_bold else "warning"
                    if is_cur_songti:
                        verify_hint = (
                            f"switch to native heavy Mincho {rec_ff!r} with bold=False (NEVER enable faux bold=True on Songti)!"
                            if is_high_conf_bold
                            else f"low/medium confidence — compare 1:1 raw strokes first; if genuinely heavy Mincho, switch to {rec_ff!r} (bold=False), NEVER faux bold=True on Songti!"
                        )
                    else:
                        verify_hint = (
                            "set bold=True (or switch to recommended native bold font)!"
                            if is_high_conf_bold
                            else "low/medium confidence — compare 1:1 raw strokes against adjacent same-speaker bubbles BEFORE bolding!"
                        )
                    findings.append(
                        {
                            "severity": b_sev,
                            "category": "yuzumarker_bold_mismatch",
                            "region_index": int(r_idx),
                            "detected_jp_font": top1_name,
                            "detected_family": eff_pred_fam,
                            "pred_conf": round(pred_conf, 4),
                            "top1_prob": round(top1_prob, 4),
                            "bold_prob": round(eff_bold_p, 4),
                            "heavy_prob": round(heavy_p, 4),
                            "recommended_font_family": rec_ff,
                            "recommended_bold": rec_bold,
                            "message": (
                                f"Region #{r_idx} ({vis_txt_f!r}) uses unbolded {eff_ff!r} (bold=False), "
                                f"but local YuzuMarker 4x Ensemble detected bold/heavy raw font {top1_name!r} "
                                f"(top1={top1_prob:.2f}, family={eff_pred_fam} {pred_conf:.2f}, bold_prob={eff_bold_p:.2f}, heavy_prob={heavy_p:.2f}) -> "
                                f"{verify_hint} (recommended {rec_ff!r} bold={rec_bold})"
                            ),
                        }
                    )

            # Check 9a-unjustified-bold: Symmetric reverse guardrail blocking Stage 6.4 over-bolding of tiny/regular `FZHei-B01` bubbles
            if is_b and eff_ff_l.split("::")[0].strip() == "fzhei-b01" and r_idx in reg_to_bub_mask:
                top1_l = top1_name.lower()
                if "kakumin" not in top1_l:
                    n_clean_ub_raw = ym_pred.get("n_clean_chars")
                    n_clean_ub = int(n_clean_ub_raw) if n_clean_ub_raw is not None else None
                    heavy_p_ub = float(ym_pred.get("heavy_prob") or 0.0)
                    shw_ub = reg_stroke_hw.get(r_idx, 0.0)
                    if (
                        not has_prop
                        and n_clean_ub is not None
                        and n_clean_ub <= 2
                        and top1_prob < 0.25
                        and pred_conf < 0.55
                        and heavy_p_ub < 0.45
                        and 0.0 < shw_ub < 2.50
                        and shw_ub <= regular_ref_hw * 1.12
                    ):
                        findings.append(
                            {
                                "severity": "error",
                                "category": "unjustified_faux_bold",
                                "region_index": int(r_idx),
                                "detected_jp_font": top1_name,
                                "detected_family": eff_pred_fam,
                                "pred_conf": round(pred_conf, 4),
                                "top1_prob": round(top1_prob, 4),
                                "n_clean_chars": n_clean_ub,
                                "raw_stroke_width_px": round(2.0 * shw_ub, 2),
                                "page_regular_stroke_width_px": round(2.0 * regular_ref_hw, 2),
                                "message": (
                                    f"Region #{r_idx} ({vis_txt_f!r}) sets synthetic `bold=True` on short `FZHei-B01` bubble "
                                    f"(n_clean={n_clean_ub}, raw_sw={2.0 * shw_ub:.1f}px vs reg≈{2.0 * regular_ref_hw:.1f}px, "
                                    f"low-conf YM={eff_pred_fam} {pred_conf:.2f} / {top1_name} {top1_prob:.2f}) — "
                                    "uncorroborated over-bolding of a regular tiny bubble; revert to `bold=False`!"
                                ),
                            }
                        )
                    elif (
                        eff_pred_fam == "ANTIQUE"
                        and antique_prob >= 0.45
                        and 0.0 < shw_ub < 2.50
                        and shw_ub <= regular_ref_hw * 1.12
                    ):
                        raw_cw_ub = _estimate_raw_main_char_size(base_gray, inp_gray, reg, pre_geom=reg_raw_geom.get(r_idx))
                        if raw_cw_ub == 0.0 or raw_cw_ub < 40.0:
                            findings.append(
                                {
                                    "severity": "warning",
                                    "category": "unjustified_faux_bold",
                                    "region_index": int(r_idx),
                                    "detected_jp_font": top1_name,
                                    "detected_family": eff_pred_fam,
                                    "pred_conf": round(pred_conf, 4),
                                    "top1_prob": round(top1_prob, 4),
                                    "n_clean_chars": n_clean_ub,
                                    "raw_stroke_width_px": round(2.0 * shw_ub, 2),
                                    "page_regular_stroke_width_px": round(2.0 * regular_ref_hw, 2),
                                    "message": (
                                        f"Region #{r_idx} ({vis_txt_f!r}) sets synthetic `bold=True` on standard `ANTIQUE` bubble "
                                        f"({top1_name}, ANTIQUE={antique_prob:.2f}, raw_sw={2.0 * shw_ub:.1f}px vs reg≈{2.0 * regular_ref_hw:.1f}px) — "
                                        "regular Japanese Antique dialogue (`-DB`/`-B` labels) must use `FZHei-B01` with `bold=False`!"
                                    ),
                                }
                            )
            continue

        # 9b. Fallback OpenCV photometric glyph check (used only when YuzuMarker cache/models are unavailable)
        fg_geom = reg_raw_geom.get(r_idx)
        if fg_geom is None:
            continue
        is_song = any(k in eff_ff for k in ("FZYaSong", "Source Han Serif"))
        is_hei = eff_ff == "FZHei-B01"
        is_guyin = any(k in eff_ff.lower() for k in ("tankuin", "guyin"))

        if is_song and fg_geom["n_kana"] >= 2 and (
            (fg_geom["cw"] >= 23.0 and fg_geom["hv_all"] >= 0.86 and fg_geom["kana_hv"] >= 0.83 and fg_geom["kana_unif"] >= 0.63)
            or (fg_geom["hv_all"] >= 0.92 and fg_geom["kana_hv"] >= 0.90 and fg_geom["kana_unif"] >= 0.62)
        ):
            findings.append(
                {
                    "severity": "warning",
                    "category": "suspect_fake_songti",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}, font={eff_ff!r}) uses Songti, but local OpenCV photometric glyph analysis "
                        f"shows monoline Gothic/Round kana (hv={fg_geom['hv_all']:.2f}, kana_hv={fg_geom['kana_hv']:.2f}, "
                        f"kana_unif={fg_geom['kana_unif']:.2f}). Standard Japanese `アンチック体` (Mincho kanji + Gothic kana) "
                        "must use `FZHei-B01` (or `FZZhunYuan-M02` if rounded), NEVER Songti!"
                    ),
                }
            )
        elif is_hei and fg_geom["cw"] >= 20.0 and fg_geom["n_chars"] >= 2 and (
            (fg_geom["hv_all"] <= 0.58 and fg_geom["kana_hv"] <= 0.55 and fg_geom["kana_unif"] <= 0.47 and fg_geom["n_kana"] >= 2)
            or (fg_geom["hv_all"] <= 0.44 and fg_geom["kana_unif"] <= 0.40 and fg_geom["cw"] >= 26.0)
        ):
            findings.append(
                {
                    "severity": "warning",
                    "category": "suspect_missed_songti",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) is left at default `FZHei-B01`, but local OpenCV photometric glyph analysis "
                        f"detects strong Mincho stroke contrast (hv={fg_geom['hv_all']:.2f}, kana_hv={fg_geom['kana_hv']:.2f}, "
                        f"kana_unif={fg_geom['kana_unif']:.2f}) — inspect at 1:1 native scale and switch to `FZYaSong-DB-GBK` if `明朝体`!"
                    ),
                }
            )
        elif (
            is_hei
            and fg_geom["cw"] >= 21.0
            and fg_geom["n_kana"] >= 3
            and fg_geom["hv_all"] >= 0.88
            and fg_geom["kana_hv"] >= 0.87
            and fg_geom["kana_unif"] >= 0.77
            and fg_geom["kana_cv"] <= 0.21
            and fg_geom["sharp_c"] <= 1.38
        ):
            findings.append(
                {
                    "severity": "info",
                    "category": "suspect_missed_round_gothic",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) is `FZHei-B01`, but local OpenCV detects monoline rounded terminals "
                        f"(hv={fg_geom['hv_all']:.2f}, kana_unif={fg_geom['kana_unif']:.2f}, sharp_corners={fg_geom['sharp_c']:.2f}) — "
                        "inspect at 1:1 native scale for `丸ゴシック` (`FZZhunYuan-M02`)!"
                    ),
                }
            )
        elif (
            not is_guyin
            and is_hei
            and fg_geom["cw"] >= 18.0
            and fg_geom["n_chars"] >= 4
            and fg_geom["solid_cc"] >= 2.85
            and fg_geom["hv_all"] >= 0.98
            and fg_geom["kana_unif"] >= 0.60
        ):
            findings.append(
                {
                    "severity": "warning",
                    "category": "suspect_missed_guyin",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}) has high intra-stroke fragmentation (solid_cc/char={fg_geom['solid_cc']:.2f}) — "
                        "inspect at 1:1 native scale for broken/worm-eaten `古印体` (`Arphic Tankuin Bold GB`)!"
                    ),
                }
            )
        elif (
            is_guyin
            and fg_geom["n_chars"] >= 5
            and fg_geom["hv_all"] <= 0.65
            and fg_geom["kana_unif"] <= 0.48
            and fg_geom["solid_cc"] <= 2.30
        ):
            findings.append(
                {
                    "severity": "warning",
                    "category": "suspect_false_guyin",
                    "region_index": int(r_idx),
                    "message": (
                        f"Region #{r_idx} ({vis_txt_f!r}, font={eff_ff!r}) uses Guyin/Tankuin, but local OpenCV shows continuous "
                        f"Mincho/brush strokes (hv={fg_geom['hv_all']:.2f}, solid_cc/char={fg_geom['solid_cc']:.2f})!"
                    ),
                }
            )

    # Check 9c (`图4` V17_030 #0 & #1): Pure Raw-Glyph Font Consistency vs Low-Confidence Misclassification check!
    # ZERO situational assumptions about "same panel / same speaker" (manga often switches fonts mid-panel!).
    # Strictly flags when a low-confidence YuzuMarker region (`pred_conf < 0.84` or `top1 < 0.68`) diverged in Chinese font
    # from a high-confidence region (`pred_conf >= 0.88` & `top1 >= 0.72`) whose MEASURED RAW GLYPH GEOMETRY closely matches!
    cand_ridxs_for_font = [
        ri
        for ri, rg in enumerate(regions)
        if len(re.sub(r"[\W_]+", "", visible_text_of_region(rg))) >= 4
        and float(rg.get("stroke_width", 0.07) or 0.07) < 0.20
    ]
    for idx_i in range(len(cand_ridxs_for_font)):
        ra_i = cand_ridxs_for_font[idx_i]
        reg_a = regions[ra_i]
        ff_a = str(reg_a.get("font_family") or "FZHei-B01").strip() or "FZHei-B01"
        b_a = region_is_bold(reg_a)
        fam_a = classify_current_mtui_font(ff_a)
        box_a = region_bbox_xyxy(reg_a)
        gm_a = reg_raw_geom.get(ra_i)
        shw_a = reg_stroke_hw.get(ra_i, 0.0)
        ym_a = lookup_cached_region_prediction(yuzumarker_page_cache, reg_a, ra_i)
        for idx_j in range(idx_i + 1, len(cand_ridxs_for_font)):
            rb_i = cand_ridxs_for_font[idx_j]
            reg_b = regions[rb_i]
            ff_b = str(reg_b.get("font_family") or "FZHei-B01").strip() or "FZHei-B01"
            b_b = region_is_bold(reg_b)
            fam_b = classify_current_mtui_font(ff_b)
            if fam_a == fam_b and b_a == b_b:
                continue
            if any(ra_i in [t[0] for t in rl] and rb_i in [t[0] for t in rl] for rl in bubble_to_regs.values()):
                continue
            ym_b = lookup_cached_region_prediction(yuzumarker_page_cache, reg_b, rb_i)
            if ym_a is None or ym_b is None:
                continue
            pa_conf = float(ym_a.get("pred_conf") or 0.0)
            pb_conf = float(ym_b.get("pred_conf") or 0.0)
            ta_top = float(((ym_a.get("top_fonts") or [{}])[0]).get("prob") or 0.0)
            tb_top = float(((ym_b.get("top_fonts") or [{}])[0]).get("prob") or 0.0)
            a_high = pa_conf >= 0.88 and ta_top >= 0.72
            b_high = pb_conf >= 0.88 and tb_top >= 0.72
            a_low = pa_conf < 0.84 or ta_top < 0.68
            b_low = pb_conf < 0.84 or tb_top < 0.68
            if not ((a_high and b_low) or (b_high and a_low)):
                continue
            gm_b = reg_raw_geom.get(rb_i)
            shw_b = reg_stroke_hw.get(rb_i, 0.0)
            if shw_a <= 0 or shw_b <= 0 or (min(shw_a, shw_b) / max(shw_a, shw_b, 0.1)) < 0.80:
                continue
            if gm_a is not None and gm_b is not None:
                cw_a_est, cw_b_est = gm_a["cw"], gm_b["cw"]
                if (
                    min(cw_a_est, cw_b_est) / max(cw_a_est, cw_b_est, 1.0) < 0.76
                    or abs(gm_a["hv_all"] - gm_b["hv_all"]) > 0.22
                    or abs(gm_a["sharp_c"] - gm_b["sharp_c"]) > 0.55
                ):
                    continue
            else:
                box_b = region_bbox_xyxy(reg_b)
                lns_a_sp = [ln.strip() for ln in visible_text_of_region(reg_a).split("\n") if ln.strip()]
                lns_b_sp = [ln.strip() for ln in visible_text_of_region(reg_b).split("\n") if ln.strip()]
                cw_a_est = (box_a[2] - box_a[0]) / max(1, len(lns_a_sp))
                cw_b_est = (box_b[2] - box_b[0]) / max(1, len(lns_b_sp))
                if min(cw_a_est, cw_b_est) / max(cw_a_est, cw_b_est, 1.0) < 0.76:
                    continue
            hi_idx, lo_idx = (ra_i, rb_i) if a_high else (rb_i, ra_i)
            hi_ff = ff_a if a_high else ff_b
            lo_ff = ff_b if a_high else ff_a
            findings.append(
                {
                    "severity": "warning",
                    "category": "raw_font_consistency_suspect",
                    "region_index": int(lo_idx),
                    "message": (
                        f"Region #{lo_idx} ({lo_ff!r}) had lower font-detection confidence while its measured raw glyph geometry "
                        f"closely matches high-confidence Region #{hi_idx} ({hi_ff!r}; raw_cw={cw_a_est:.0f} vs {cw_b_est:.0f}px, "
                        f"stroke_w={2.0 * shw_a:.1f} vs {2.0 * shw_b:.1f}px) — inspect BOTH Raw text crops directly "
                        f"(NEVER assume by panel/speaker!): if their Raw Japanese font is identical, unify to {hi_ff!r}; "
                        "if the Raw font genuinely differs, keep each matched to its own Raw font!"
                    ),
                }
            )

    # Check 8e (`图7` `ていっ` -> `喝！`): Undersized Rendered Font vs Prominent Raw Lettering (`raw_font_size_mismatch`).
    # NOTE: Deliberately NEVER penalize making tiny raw text larger (`…………は？`), as larger fill improves readability!
    bubble_wh_by_reg: dict[int, tuple[float, float]] = {}
    for lbl_b, rl_b in bubble_to_regs.items():
        _, _, b_w, b_h, _ = cc_stats[lbl_b]
        for r_i_b, _, _ in rl_b:
            bubble_wh_by_reg[r_i_b] = (float(b_w), float(b_h))
    for r_idx, reg in enumerate(regions):
        vis_s = visible_text_of_region(reg).strip()
        vis_s_alnum = re.sub(r"[\W_]+", "", vis_s)
        vis_s_cells = _count_vertical_cells_in_line(vis_s.replace("\n", ""))
        if len(vis_s_alnum) < 1 or len(vis_s) > 65:
            continue
        if float(reg.get("stroke_width", 0.07) or 0.07) >= 0.22:
            continue
        raw_cw = _estimate_raw_main_char_size(base_gray, inp_gray, reg, pre_geom=reg_raw_geom.get(r_idx))
        if raw_cw < 16.0 or (vis_s_cells < 2 and raw_cw < 32.0):
            continue
        fs_cur = int(round(float(reg.get("font_size") or 16)))
        ratio_fs = fs_cur / raw_cw
        if ratio_fs <= 0.74 and (raw_cw - fs_cur) >= 8.0:
            lns_s = [ln.strip() for ln in vis_s.split("\n") if ln.strip()]
            if not lns_s:
                continue
            d_s = str(reg.get("direction") or "auto").lower()
            bx1_s, by1_s, bx2_s, by2_s = region_bbox_xyxy(reg)
            is_v_s = d_s in ("v", "vertical", "vr") or (d_s == "auto" and not ((bx2_s - bx1_s) / max(1.0, by2_s - by1_s) > 1.45 and len(lns_s) == 1))
            lsp_s = max(0.85, float(reg.get("line_spacing", 1.0) or 1.0))
            csp_s = max(0.85, float(reg.get("letter_spacing", 1.0) or 1.0))
            if is_v_s:
                ew_s = len(lns_s) * fs_cur * lsp_s
                eh_s = max(_count_vertical_cells_in_line(ln) for ln in lns_s) * fs_cur * csp_s
            else:
                ew_s = max(len(ln) for ln in lns_s) * fs_cur * csp_s
                eh_s = len(lns_s) * fs_cur * lsp_s
            bub_wh = bubble_wh_by_reg.get(r_idx)
            has_room = True
            cap_fs = int(round(raw_cw * 1.12)) if vis_s_cells <= 3 else int(round(raw_cw * 1.06))
            if bub_wh is not None:
                bw_lim, bh_lim = bub_wh
                has_room = ew_s <= bw_lim * 0.72 and eh_s <= bh_lim * 0.72
                if is_v_s:
                    cap_fs = min(
                        max(cap_fs, int(round(bh_lim * 0.55 / max(vis_s_cells * csp_s, 1.5)))) if vis_s_cells <= 3 else cap_fs,
                        int(
                            min(
                                (bw_lim * 0.74) / max(len(lns_s) * lsp_s, 1.0),
                                (bh_lim * 0.74) / max(max(_count_vertical_cells_in_line(ln) for ln in lns_s) * csp_s, 1.0),
                            )
                        ),
                    )
            if has_room and cap_fs >= fs_cur + 5:
                findings.append(
                    {
                        "severity": "error" if ratio_fs <= 0.62 else "warning",
                        "category": "raw_font_size_mismatch",
                        "region_index": int(r_idx),
                        "raw_char_px": round(raw_cw, 1),
                        "recommended_font_size": cap_fs,
                        "message": (
                            f"Region #{r_idx} ({vis_s!r}) uses tiny font_size={fs_cur}px ({ratio_fs:.2f}x of raw glyph size≈{raw_cw:.0f}px, `图7`) "
                            f"despite ample bubble space — increase font_size to ~{cap_fs}px to match bubble inner diameter & Raw punch!"
                        ),
                    }
                )

    # Check 1, Check 2 & Check 8: Same-bubble split vs Unfilled stepped/connected lobe vs Text overflow
    for lbl, r_list in bubble_to_regs.items():
        x0, y0, bw, bh, area = cc_stats[lbl]
        pad = 6
        sx1, sy1 = max(0, x0 - pad), max(0, y0 - pad)
        sx2, sy2 = min(w, x0 + bw + pad), min(h, y0 + bh + pad)
        sub_comp = (cc_labels[sy1:sy2, sx1:sx2] == lbl).astype(np.uint8)
        dist = cv2.distanceTransform(sub_comp, cv2.DIST_L2, 5)
        sub_cusps = detect_inward_bubble_cusps(sub_comp * 255)

        # Check 8: Rendered text ink overflowing or touching black bubble/box border (`图11` & `图6` giant shout-bubble overflow)
        emitted_overflow_ridxs: set[int] = set()
        if text_ink_all is not None and safe_white_interior is not None:
            sub_bub_full = cv2.dilate(sub_comp, np.ones((5, 5), dtype=np.uint8), iterations=1) > 0
            sub_bub_safe = sub_bub_full & safe_white_interior[sy1:sy2, sx1:sx2]
            for r_idx, rcx, rcy in r_list:
                reg = regions[r_idx]
                sw_val = float(reg.get("stroke_width", reg.get("default_stroke_width", 0.07)) or 0.07)
                fs_val = int(round(float(reg.get("font_size") or 16)))
                if sw_val >= 0.20:
                    continue
                rx1, ry1, rx2, ry2 = region_bbox_xyxy(reg)
                wx1 = max(sx1, int(math.floor(min(rx1, rcx - bw * 0.48))) - 36)
                wy1 = max(sy1, int(math.floor(min(ry1, rcy - bh * 0.48))) - 36)
                wx2 = min(sx2, int(math.ceil(max(rx2, rcx + bw * 0.48))) + 36)
                wy2 = min(sy2, int(math.ceil(max(ry2, rcy + bh * 0.48))) + 36)
                if wx2 <= wx1 or wy2 <= wy1:
                    continue
                win_ink = text_ink_all[wy1:wy2, wx1:wx2]
                if not np.any(win_ink):
                    continue
                win_safe = sub_bub_safe[wy1 - sy1 : wy2 - sy1, wx1 - sx1 : wx2 - sx1]
                # Connect adjacent glyph strokes slightly (3x3) and keep only glyph strokes anchored inside this bubble
                win_ink_grp = cv2.dilate(win_ink.astype(np.uint8), np.ones((3, 3), dtype=np.uint8))
                ni, ilbl_arr, _, _ = cv2.connectedComponentsWithStats(win_ink_grp, connectivity=8)
                inside_lbls = np.unique(ilbl_arr[win_safe & win_ink])
                inside_lbls = inside_lbls[inside_lbls != 0]
                if inside_lbls.size == 0:
                    continue
                anchored_ink = win_ink & np.isin(ilbl_arr, inside_lbls)
                overflow_px = int(np.sum(anchored_ink & (~win_safe)))
                if overflow_px >= 10:
                    emitted_overflow_ridxs.add(int(r_idx))
                    findings.append(
                        {
                            "severity": "error",
                            "category": "text_overflows_bubble",
                            "region_index": int(r_idx),
                            "message": (
                                f"Region #{r_idx} ({visible_text_of_region(reg)!r}, font_size={fs_val}px) overflows or touches "
                                f"the enclosed bubble/box border by {overflow_px}px (bubble_wh=[{int(bw)}, {int(bh)}]) — "
                                "re-break lines to match container aspect ratio and/or reduce font_size!"
                            ),
                        }
                    )

        # Partition multi-lobe connected bubbles into isolated per-region chambers BEFORE Check 8c / 8f lobe measurements.
        # Guardrail: if `len(r_list) >= 2` and cusp-bisector cannot isolate a region into its own single chamber,
        # skip `bubble_underfilled_font` / `unbalanced_linebreak_columns` for that region instead of measuring the combined multi-lobe span!
        lobe_masks_by_ridx: dict[int, np.ndarray] = {}
        if len(r_list) == 1:
            lobe_masks_by_ridx[r_list[0][0]] = sub_comp
        elif len(r_list) >= 2:
            local_pts = [(rcx - sx1, rcy - sy1) for _, rcx, rcy in r_list]
            split_sub = split_multilobe_cusp_bisector(sub_comp, local_pts)
            _, sl_lbl = cv2.connectedComponents(split_sub, connectivity=4)
            sh_s, sw_s = sl_lbl.shape[:2]
            lobe_groups: dict[int, list[int]] = {}
            for r_idx_s, rcx_s, rcy_s in r_list:
                lx_s = max(0, min(sw_s - 1, int(round(rcx_s - sx1))))
                ly_s = max(0, min(sh_s - 1, int(round(rcy_s - sy1))))
                k_s = int(sl_lbl[ly_s, lx_s])
                if k_s > 0:
                    lobe_groups.setdefault(k_s, []).append(r_idx_s)
            for k_s, group_ridxs in lobe_groups.items():
                if len(group_ridxs) == 1:
                    lobe_masks_by_ridx[group_ridxs[0]] = (sl_lbl == k_s).astype(np.uint8)

        # Check 8b & Check 8c: Geometric overflow fallback + Under-filled bubble font-size check (`图7`)
        for r_idx, rcx, rcy in r_list:
            reg = regions[r_idx]
            vis_r = visible_text_of_region(reg).strip()
            if not vis_r:
                continue
            sw_val = float(reg.get("stroke_width", reg.get("default_stroke_width", 0.07)) or 0.07)
            if sw_val >= 0.20:
                continue
            fs_val = int(round(float(reg.get("font_size") or 16)))
            line_sp = max(0.85, float(reg.get("line_spacing", 1.0) or 1.0))
            let_sp = max(0.85, float(reg.get("letter_spacing", 1.0) or 1.0))
            non_empty_lns = [ln.strip() for ln in vis_r.split("\n") if ln.strip()]
            if not non_empty_lns:
                continue
            d_r = str(reg.get("direction") or "auto").lower()
            rx1, ry1, rx2, ry2 = region_bbox_xyxy(reg)
            rbw, rbh = max(1.0, rx2 - rx1), max(1.0, ry2 - ry1)
            is_v = d_r in ("v", "vertical", "vr") or (d_r == "auto" and not (rbw / rbh > 1.45 and len(non_empty_lns) == 1))
            if is_v:
                n_cols = len(non_empty_lns)
                max_cells = max(_count_vertical_cells_in_line(ln) for ln in non_empty_lns)
                est_w = n_cols * fs_val * line_sp
                est_h = max_cells * fs_val * let_sp
            else:
                est_w = max(len(ln) for ln in non_empty_lns) * fs_val * let_sp
                est_h = len(non_empty_lns) * fs_val * line_sp

            if int(r_idx) not in emitted_overflow_ridxs and (est_w > bw * 1.08 or est_h > bh * 1.08):
                emitted_overflow_ridxs.add(int(r_idx))
                findings.append(
                    {
                        "severity": "error",
                        "category": "text_overflows_bubble",
                        "region_index": int(r_idx),
                        "message": (
                            f"Region #{r_idx} ({vis_r!r}, font_size={fs_val}px) has estimated text block "
                            f"[{int(round(est_w))}x{int(round(est_h))}px] exceeding bubble [{int(bw)}x{int(bh)}px] — "
                            "reduce font_size (especially when expanding 1 column to 2+ columns)!"
                        ),
                    }
                )

            # Check 8c (`图7` `ていっ` -> `喝！` & under-filled dialogue): Per-lobe bubble inner-diameter occupancy check!
            # Covers BOTH short 1-5 char exclamations (`喝！` at 34px inside a 290px tall bubble!) AND multi-column dialogue, with NO `fs<=25` or `min(35)` ceiling!
            vis_alnum = re.sub(r"[\W_]+", "", vis_r)
            lobe_mask = lobe_masks_by_ridx.get(r_idx)
            if (
                is_v
                and lobe_mask is not None
                and len(vis_alnum) >= 1
                and int(np.count_nonzero(lobe_mask)) >= 9500
                and rbh >= 75.0
            ):
                lx_i = max(0, min(lobe_mask.shape[1] - 1, int(round(rcx - sx1))))
                ly_i = max(0, min(lobe_mask.shape[0] - 1, int(round(rcy - sy1))))
                col_band = lobe_mask[:, max(0, lx_i - 25) : min(lobe_mask.shape[1], lx_i + 26)]
                row_band = lobe_mask[max(0, ly_i - 25) : min(lobe_mask.shape[0], ly_i + 26), :]
                lobe_h = float(np.max(np.sum(col_band > 0, axis=0))) if col_band.size > 0 else float(bh)
                lobe_w = float(np.max(np.sum(row_band > 0, axis=1))) if row_band.size > 0 else float(bw)
                fill_h = est_h / max(lobe_h, 1.0)
                fill_w = est_w / max(lobe_w, 1.0)
                # Case A: Short 1-3 cell exclamation / kiai (`喝！`, `哈啊!?`, `住手!!`) where raw filled >=40% of bubble height yet rendered shrinks <38%!
                if n_cols == 1 and max_cells <= 3 and lobe_h >= 145.0 and lobe_w >= 90.0 and rbh >= 0.40 * lobe_h:
                    if fill_h < 0.39 and fill_w < 0.36:
                        cap_by_h = (lobe_h * 0.56) / max(max_cells * let_sp, 1.4)
                        cap_by_w = (lobe_w * 0.52) / max(n_cols * line_sp, 1.0)
                        rec_fs = min(92, int(round(min(cap_by_h, cap_by_w))))
                        if rec_fs >= fs_val + 8:
                            findings.append(
                                {
                                    "severity": "error" if fill_h < 0.34 else "warning",
                                    "category": "bubble_underfilled_font",
                                    "region_index": int(r_idx),
                                    "recommended_font_size": rec_fs,
                                    "message": (
                                        f"Short exclamation Region #{r_idx} ({vis_r!r}) uses tiny font_size={fs_val}px filling only "
                                        f"{fill_w * 100:.0f}%x{fill_h * 100:.0f}% of bubble [{int(lobe_w)}x{int(lobe_h)}px] "
                                        f"(raw text height={int(rbh)}px, `{rbh / lobe_h * 100:.0f}%` of bubble, `图7`) — "
                                        f"increase font_size to ~{rec_fs}px (~56% inner height fill)!"
                                    ),
                                }
                            )
                # Case B: Multi-char dialogue (`>=4` chars) under-filling its bubble chamber across any page resolution
                elif len(vis_alnum) >= 4 and lobe_h >= 175.0 and lobe_w >= 125.0 and rbh >= 0.34 * lobe_h:
                    if fill_h < 0.54 and fill_w < 0.45:
                        cap_by_h = (lobe_h * 0.65) / max(max_cells * let_sp, 2.5)
                        cap_by_w = (lobe_w * 0.70) / max(n_cols * line_sp, 1.5)
                        rec_fs = min(68, int(round(min(cap_by_h, cap_by_w))))
                        if rec_fs >= fs_val + 5:
                            findings.append(
                                {
                                    "severity": "error" if fill_h < 0.38 else "warning",
                                    "category": "bubble_underfilled_font",
                                    "region_index": int(r_idx),
                                    "recommended_font_size": rec_fs,
                                    "message": (
                                        f"Region #{r_idx} ({vis_r!r}) uses small font_size={fs_val}px filling only "
                                        f"{fill_w * 100:.0f}%x{fill_h * 100:.0f}% of its bubble lobe [{int(lobe_w)}x{int(lobe_h)}px] "
                                        f"(raw text height={int(rbh)}px) — increase font_size to ~{rec_fs}px (~65% inner fill)!"
                                    ),
                                }
                            )
                # Check 8f: Real bubble chamber aspect-ratio vs column-count mismatch (e.g. 1-2 tall skinny columns inside a wide round bubble!)
                total_cells_b = sum(_count_vertical_cells_in_line(ln) for ln in non_empty_lns)
                if total_cells_b >= 14 and n_cols <= 2 and max_cells >= 9 and lobe_w >= 0.68 * lobe_h:
                    bub_asp = lobe_w / max(lobe_h, 1.0)
                    txt_asp = (n_cols * 1.15) / max(max_cells, 1)
                    if txt_asp < 0.36 * bub_asp:
                        rec_cols = max(3, int(round(math.sqrt(total_cells_b * bub_asp))))
                        findings.append(
                            {
                                "severity": "warning",
                                "category": "unbalanced_linebreak_columns",
                                "region_index": int(r_idx),
                                "message": (
                                    f"Region #{r_idx} ({vis_r!r}) packs {total_cells_b} cells into {n_cols} tall column(s) "
                                    f"(max={max_cells} cells) inside a wide/round bubble [{int(lobe_w)}x{int(lobe_h)}px] — "
                                    f"re-break at natural breath pauses into ~{rec_cols} columns to conform to the bubble contour!"
                                ),
                            }
                        )

        if len(r_list) >= 2:
            # Check 8d (`图7` clause split): Dangling modifier + invented ellipsis at end of an upper lobe in a multi-lobe connected bubble
            ordered_lobes = sorted(r_list, key=lambda t: (-t[1], t[2]))
            for r_idx_l, _, _ in ordered_lobes[:-1]:
                reg_l = regions[r_idx_l]
                src_l = str(reg_l.get("text", "") or "")
                vis_l = visible_text_of_region(reg_l).strip()
                if (
                    vis_l.endswith(("︙", "…"))
                    and not any(ep in src_l for ep in ("…", "‥", "...", "︙"))
                    and re.search(r"(?:往往|而且|甚至|简直|居然|竟然|明明|偏偏|因为|所以|虽然|虽说|为了|关于|对于|把|被|向|往)[︙…]$", vis_l)
                ):
                    findings.append(
                        {
                            "severity": "warning",
                            "category": "cross_lobe_dangling_clause",
                            "region_index": int(r_idx_l),
                            "message": (
                                f"Connected multi-lobe Region #{r_idx_l} ({vis_l!r}) splits a modifier phrase across lobes with an invented trailing ellipsis "
                                f"(raw={src_l!r} has no ellipsis) — break cleanly at the natural clause boundary between lobes!"
                            ),
                        }
                    )
            eff_styles = [
                (
                    str(regions[r_idx].get("font_family") or "").strip(),
                    region_is_bold(regions[r_idx]),
                )
                for r_idx, _, _ in r_list
            ]
            if len(set(eff_styles)) > 1:
                desc = ", ".join(f"#{r_list[k][0]}={eff_styles[k][0]!r}(bold={eff_styles[k][1]})" for k in range(len(r_list)))
                findings.append(
                    {
                        "severity": "warning",
                        "category": "connected_bubble_font_mismatch",
                        "region_index": r_list[0][0],
                        "message": (
                            f"Connected multi-lobe bubble has inconsistent font/weight across lobes ({desc}) — "
                            "verify raw strokes; connected lobes spoken in one breath must share the same font & weight unless raw explicitly changes!"
                        ),
                    }
                )

            for i in range(len(r_list)):
                idx_a, cxa, cya = r_list[i]
                box_a = region_bbox_xyxy(regions[idx_a])
                wa = max(1.0, box_a[2] - box_a[0])
                ha = max(1.0, box_a[3] - box_a[1])
                pa = (cxa - sx1, cya - sy1)
                ra = float(dist[max(0, min(dist.shape[0] - 1, int(round(pa[1])))), max(0, min(dist.shape[1] - 1, int(round(pa[0]))))])
                for j in range(i + 1, len(r_list)):
                    idx_b, cxb, cyb = r_list[j]
                    box_b = region_bbox_xyxy(regions[idx_b])
                    wb = max(1.0, box_b[2] - box_b[0])
                    hb = max(1.0, box_b[3] - box_b[1])
                    # Stepped diagonal multi-chamber bubbles (`右上+左下`) have BOTH low v_overlap AND low h_overlap — NEVER merge!
                    v_overlap = max(0.0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1])) / min(ha, hb)
                    h_overlap = max(0.0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])) / min(wa, wb)
                    cy_diff_ratio = abs(cya - cyb) / min(ha, hb)
                    is_side_by_side = v_overlap >= 0.62 and cy_diff_ratio <= 0.35
                    is_vertical_col_split = h_overlap >= 0.45 and abs(cxa - cxb) <= 0.65 * max(wa, wb)
                    if not (is_side_by_side or is_vertical_col_split):
                        continue
                    pb = (cxb - sx1, cyb - sy1)
                    # NEVER merge across inward chamber-dividing contour cusps/spikes (`图2` V14_012 & `图3` V14_014)!
                    if pair_divided_by_bubble_cusps(sub_cusps, pa, pb):
                        continue
                    # Also do NOT urge merging two already-complete horizontal-band clauses (>=8 raw alnum chars each, >=75px apart)
                    if is_side_by_side and not is_vertical_col_split and abs(cxa - cxb) >= 75.0:
                        sa_len = len(re.sub(r"[\W_]+", "", str(regions[idx_a].get("text") or "")))
                        sb_len = len(re.sub(r"[\W_]+", "", str(regions[idx_b].get("text") or "")))
                        if sa_len >= 8 and sb_len >= 8:
                            continue
                    rb = float(dist[max(0, min(dist.shape[0] - 1, int(round(pb[1])))), max(0, min(dist.shape[1] - 1, int(round(pb[0]))))])
                    min_r = min(ra, rb)
                    max_r = max(ra, rb)
                    if min_r < 8.0:
                        continue
                    neck = _min_dist_along_segment(dist, pa, pb)
                    if neck >= 0.78 * min_r and neck >= 0.68 * max_r:
                        if is_vertical_col_split and not is_side_by_side:
                            findings.append(
                                {
                                    "severity": "error",
                                    "category": "same_bubble_split_regions",
                                    "region_index": idx_a,
                                    "message": (
                                        f"Regions #{idx_a} and #{idx_b} split a vertical column top-to-bottom inside the SAME open bubble "
                                        f"(h_overlap={h_overlap:.2f}, neck_radius={neck:.1f}px vs lobe_min={min_r:.1f}px) — "
                                        "broken column fragments cause severe out-of-context mistranslations! Read Raw bubble as one sentence, merge via `mtui_merge_regions`, and re-translate!"
                                    ),
                                }
                            )
                        else:
                            findings.append(
                                {
                                    "severity": "warning",
                                    "category": "same_bubble_split_regions",
                                    "region_index": idx_a,
                                    "message": (
                                        f"Regions #{idx_a} and #{idx_b} sit side-by-side inside the SAME single-chamber speech bubble "
                                        f"(v_overlap={v_overlap:.2f}, neck_radius={neck:.1f}px vs lobe_min={min_r:.1f}px, 0 dividing cusps) — merge via `mtui_merge_regions`!"
                                    ),
                                }
                            )

        # Check 2a: Pre-MCP `initial_before_mcp` connected multi-chamber over-merge detector (`图2 V14_012`, `图3 V14_014`, `V14_055/064`!)
        emitted_unfilled_for_lbl = False
        init_in_lbl = init_bubble_to_regs.get(lbl) or []
        if len(init_in_lbl) >= 2 and len(r_list) < len(init_in_lbl):
            for idx_i in range(len(init_in_lbl)):
                ireg_a, icxa, icya = init_in_lbl[idx_i]
                ibox_a = region_bbox_xyxy(ireg_a)
                iwa, iha = max(1.0, ibox_a[2] - ibox_a[0]), max(1.0, ibox_a[3] - ibox_a[1])
                for idx_j in range(idx_i + 1, len(init_in_lbl)):
                    ireg_b, icxb, icyb = init_in_lbl[idx_j]
                    ibox_b = region_bbox_xyxy(ireg_b)
                    iwb, ihb = max(1.0, ibox_b[2] - ibox_b[0]), max(1.0, ibox_b[3] - ibox_b[1])
                    iv_ov = max(0.0, min(ibox_a[3], ibox_b[3]) - max(ibox_a[1], ibox_b[1])) / min(iha, ihb)
                    ih_ov = max(0.0, min(ibox_a[2], ibox_b[2]) - max(ibox_a[0], ibox_b[0])) / min(iwa, iwb)
                    is_diag_stepped = (iv_ov < 0.58 and ih_ov < 0.45) or abs(icya - icyb) > 0.45 * min(iha, ihb)
                    is_cusp_divided = pair_divided_by_bubble_cusps(
                        sub_cusps, (icxa - sx1, icya - sy1), (icxb - sx1, icyb - sy1)
                    )
                    if not (is_diag_stepped or is_cusp_divided):
                        continue
                    has_cur_a = any(math.hypot(icxa - rcx, icya - rcy) < 46.0 for _, rcx, rcy in r_list)
                    has_cur_b = any(math.hypot(icxb - rcx, icyb - rcy) < 46.0 for _, rcx, rcy in r_list)
                    if not (has_cur_a and has_cur_b):
                        miss_box = ibox_a if not has_cur_a else ibox_b
                        miss_reg = ireg_a if not has_cur_a else ireg_b
                        nearest_reg = min(r_list, key=lambda t: math.hypot(icxa - t[1], icya - t[2]))
                        emitted_unfilled_for_lbl = True
                        findings.append(
                            {
                                "severity": "error",
                                "category": "unfilled_connected_lobe",
                                "region_index": nearest_reg[0],
                                "message": (
                                    f"Connected 2-chamber bubble [{int(miss_box[0])}, {int(miss_box[1])}, {int(miss_box[2])}, {int(miss_box[3])}] "
                                    f"(raw={str(miss_reg.get('text', ''))!r}, cusp_divided={is_cusp_divided}) was wrongly merged into Region #{nearest_reg[0]}! "
                                    "Initial translation ALREADY separated these two chambers — split back into 2 independent regions (1 per chamber)!"
                                ),
                                "lobe_bbox_xyxy": [int(round(v)) for v in miss_box],
                            }
                        )
                        break
                if emitted_unfilled_for_lbl:
                    break

        # Check 2b: General raw-ink + cusp/pinch chamber detector (works even without snapshots)
        if not emitted_unfilled_for_lbl:
            sub_mask = mask_bin[sy1:sy2, sx1:sx2]
            sub_base = base_gray[sy1:sy2, sx1:sx2]
            sub_inp = inp_gray[sy1:sy2, sx1:sx2]
            sub_comp_dil = cv2.dilate(sub_comp, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
            erased_ink = sub_comp_dil & sub_mask & (sub_base < 135) & (sub_inp > 220)
            if np.any(erased_ink):
                grouped = cv2.dilate(erased_ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19)))
                nk, k_lbl, k_stats, k_cents = cv2.connectedComponentsWithStats(grouped, connectivity=8)
                if nk > 2:
                    for kid in range(1, nk):
                        kx, ky, kw, kh, _ = k_stats[kid]
                        raw_ink_px = int(np.sum(erased_ink & (k_lbl == kid)))
                        if raw_ink_px < 75:
                            continue
                        wcx = sx1 + float(k_cents[kid][0])
                        wcy = sy1 + float(k_cents[kid][1])
                        nearest_d = min(math.hypot(wcx - rcx, wcy - rcy) for _, rcx, rcy in r_list)
                        nearest_reg = min(r_list, key=lambda t: math.hypot(wcx - t[1], wcy - t[2]))
                        p_cluster = (wcx - sx1, wcy - sy1)
                        p_reg = (nearest_reg[1] - sx1, nearest_reg[2] - sy1)
                        rc = float(dist[max(0, min(dist.shape[0] - 1, int(round(p_cluster[1])))), max(0, min(dist.shape[1] - 1, int(round(p_cluster[0]))))])
                        rr = float(dist[max(0, min(dist.shape[0] - 1, int(round(p_reg[1])))), max(0, min(dist.shape[1] - 1, int(round(p_reg[0]))))])
                        neck = _min_dist_along_segment(dist, p_cluster, p_reg)
                        min_lobe_r = min(rc, rr)
                        ux1, uy1 = int(sx1) + int(kx), int(sy1) + int(ky)
                        ux2, uy2 = int(sx1) + int(kx + kw), int(sy1) + int(ky + kh)
                        lobe_is_blank = (
                            int(np.sum(text_ink_all[uy1:uy2, ux1:ux2])) < 18
                            if text_ink_all is not None
                            else False
                        )
                        is_stepped_pair = abs(wcy - nearest_reg[2]) >= 58.0 and abs(wcx - nearest_reg[1]) >= 45.0
                        has_true_pinch = min_lobe_r >= 18.0 and neck < 0.80 * min_lobe_r
                        has_cusp_div = raw_ink_px >= 90 and pair_divided_by_bubble_cusps(sub_cusps, p_cluster, p_reg)
                        if (nearest_d >= 75.0 and (has_true_pinch or (lobe_is_blank and is_stepped_pair and raw_ink_px >= 140))) or (
                            nearest_d >= 52.0 and has_cusp_div
                        ):
                            findings.append(
                                {
                                    "severity": "error" if (lobe_is_blank or has_cusp_div) else "warning",
                                    "category": "unfilled_connected_lobe",
                                    "region_index": nearest_reg[0],
                                    "message": (
                                        f"Connected bubble chamber around ({wcx:.0f}, {wcy:.0f}) had {raw_ink_px}px of raw text erased "
                                        f"(cusp_divided={has_cusp_div}, neck={neck:.1f}px vs min_r={min_lobe_r:.1f}px), but all text was merged into Region #{nearest_reg[0]} — split into 2 separate regions!"
                                    ),
                                    "lobe_bbox_xyxy": [ux1, uy1, ux2, uy2],
                                }
                            )

    # Check 3 (`图1` V14_029 #8): Bubble/frame black border cut/eroded by mask overflow!
    # Works on BOTH dark-background panels AND pure-white-background panels, including thin anti-aliased JPEG apex curves!
    if np.any(mask_bin):
        erased_aa = mask_bin & (base_gray < 172) & (inp_gray > 218)
        erased_dark = (
            cv2.morphologyEx(erased_aa.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
        ) & mask_bin
        raw_unmasked_dark = (~mask_bin) & (base_gray < 155) & (inp_gray < 165)
        ns_b, sb_lbl, sb_stats, _ = cv2.connectedComponentsWithStats(raw_unmasked_dark.astype(np.uint8), connectivity=8)
        surviving_border = np.zeros_like(raw_unmasked_dark, dtype=bool)
        for sbid in range(1, ns_b):
            _, _, sbw, sbh, sbarea = sb_stats[sbid]
            if sbarea >= 45 and max(sbw, sbh) >= 24:
                surviving_border[sb_lbl == sbid] = True
        # Keep entire connected components of `erased_dark` that touch within 6px of `surviving_border` (even when a wide wiped top/bottom arc spans >120px!)
        near_surv_seed = cv2.dilate(surviving_border.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))) > 0
        if np.any(erased_dark & near_surv_seed):
            ne, e_lbl, e_stats, _ = cv2.connectedComponentsWithStats(erased_dark.astype(np.uint8), connectivity=8)
            for eid in range(1, ne):
                ex, ey, ew, eh, earea = e_stats[eid]
                if earea < 20 or max(ew, eh) < 10 or earea > max(ew, eh) * 16:
                    continue
                px1, py1 = max(0, ex - 6), max(0, ey - 6)
                px2, py2 = min(w, ex + ew + 6), min(h, ey + eh + 6)
                seg = e_lbl[py1:py2, px1:px2] == eid
                # Must contain genuine crisp dark raw ink (`base_gray < 115`), not pure light-gray screentone
                if int(np.sum(seg & (base_gray[py1:py2, px1:px2] < 115))) < 14:
                    continue
                seg_ring = (cv2.dilate(seg.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0) & (~seg)
                touch_surv = seg_ring & surviving_border[py1:py2, px1:px2]
                if not np.any(touch_surv):
                    continue
                touch_grouped = cv2.dilate(touch_surv.astype(np.uint8), np.ones((5, 5), dtype=np.uint8))
                nt, _, t_stats, t_cents = cv2.connectedComponentsWithStats(touch_grouped, connectivity=8)
                valid_cids = [tid for tid in range(1, nt) if t_stats[tid, cv2.CC_STAT_AREA] >= 3]
                if len(valid_cids) >= 2:
                    max_end_dist = max(
                        math.hypot(t_cents[a][0] - t_cents[b][0], t_cents[a][1] - t_cents[b][1])
                        for i_a, a in enumerate(valid_cids)
                        for b in valid_cids[i_a + 1 :]
                    )
                    if max_end_dist >= 10.0:
                        ecx, ecy = ex + ew * 0.5, ey + eh * 0.5
                        near_r = min(range(len(reg_centers)), key=lambda k: math.hypot(ecx - reg_centers[k][0], ecy - reg_centers[k][1])) if reg_centers else None
                        if near_r is not None and math.hypot(ecx - reg_centers[near_r][0], ecy - reg_centers[near_r][1]) <= 360.0:
                            findings.append(
                                {
                                    "severity": "error",
                                    "category": "bubble_border_eroded",
                                    "region_index": int(near_r),
                                    "message": (
                                        f"Mask overflow near Region #{near_r} wiped out {int(earea)}px of continuous black speech-bubble/panel border "
                                        f"at [{int(ex)}, {int(ey)}, {int(ex + ew)}, {int(ey + eh)}] (`图1`)! "
                                        f"Restore the severed border cleanly via `mtui_edit_mask_and_inpaint(erase_rects=[[{int(max(0, ex - 2))}, {int(max(0, ey - 2))}, {int(min(w, ex + ew + 2))}, {int(min(h, ey + eh + 2))}]])`!"
                                    ),
                                    "suggested_restore_rect_xyxy": [
                                        int(max(0, int(ex) - 2)),
                                        int(max(0, int(ey) - 2)),
                                        int(min(w, int(ex + ew) + 2)),
                                        int(min(h, int(ey + eh) + 2)),
                                    ],
                                }
                            )

    # Check 4: Untranslated boxed/bubble SFX (enclosed clean white box/bubble with 0 regions and crisp black kana/SFX strokes)
    for lbl in range(1, n_cc):
        if lbl in bubble_to_regs:
            continue
        x0, y0, bw, bh, area = cc_stats[lbl]
        if area < 2600 or area > 0.16 * w * h or bw < 42 or bh < 42:
            continue
        edges = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw >= w - 2) + int(y0 + bh >= h - 2)
        if edges >= 2:
            continue
        comp = cc_labels[y0 : y0 + bh, x0 : x0 + bw] == lbl
        # Skip if any region center is inside bounding box of this component
        if any(x0 <= rcx <= x0 + bw and y0 <= rcy <= y0 + bh for rcx, rcy in reg_centers):
            continue
        # Must be bounded by dark outline around most of its outer ring
        outer_ring = (cv2.dilate(comp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0) & (~comp)
        sub_base_full = base_gray[y0 : y0 + bh, x0 : x0 + bw]
        if not np.any(outer_ring) or float(np.mean(sub_base_full[outer_ring] < 130)) < 0.55:
            continue
        interior = cv2.erode(comp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))) > 0
        int_area = int(np.sum(interior))
        if int_area < 900:
            continue
        # Background around strokes inside interior must be overwhelmingly pure white (>=246)
        white_ratio = float(np.mean(sub_base_full[interior] >= 246))
        if white_ratio < 0.80 or white_ratio > 0.992:
            continue
        # Skip if already masked
        if float(np.mean(mask_bin[y0 : y0 + bh, x0 : x0 + bw][interior])) > 0.10:
            continue
        dark_strokes = interior & (sub_base_full < 75)
        dark_px = int(np.sum(dark_strokes))
        if not (130 <= dark_px <= 12000):
            continue
        # Group nearby strokes into characters and verify centered character-like geometry
        ch_grp = cv2.dilate(dark_strokes.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        nc, _, c_stats, _ = cv2.connectedComponentsWithStats(ch_grp, connectivity=8)
        char_boxes = [
            c_stats[cid]
            for cid in range(1, nc)
            if c_stats[cid, cv2.CC_STAT_AREA] >= 90
            and 14 <= c_stats[cid, cv2.CC_STAT_WIDTH] <= bw * 0.75
            and 14 <= c_stats[cid, cv2.CC_STAT_HEIGHT] <= bh * 0.75
        ]
        if 1 <= len(char_boxes) <= 6:
            ux1 = int(x0) + int(min(b[0] for b in char_boxes))
            uy1 = int(y0) + int(min(b[1] for b in char_boxes))
            ux2 = int(x0) + int(max(b[0] + b[2] for b in char_boxes))
            uy2 = int(y0) + int(max(b[1] + b[3] for b in char_boxes))
            # Ensure character group does not touch the eroded interior boundary (which means artwork/hair poking in, not floating text!)
            int_border = (cv2.dilate(interior.astype(np.uint8), np.ones((3, 3), dtype=np.uint8)) > 0) & (~interior)
            if not np.any(cv2.dilate(dark_strokes.astype(np.uint8), np.ones((3, 3), dtype=np.uint8)) & int_border):
                findings.append(
                    {
                        "severity": "warning",
                        "category": "untranslated_boxed_sfx",
                        "region_index": None,
                        "message": (
                            f"Untranslated text/SFX detected inside enclosed white bubble/panel box [{x0}, {y0}, {x0 + bw}, {y0 + bh}] "
                            f"(strokes bbox=[{ux1}, {uy1}, {ux2}, {uy2}], {len(char_boxes)} glyph group(s)) — inspect and translate if dialogue/boxed SFX!"
                        ),
                        "box_xyxy": [int(x0), int(y0), int(x0 + bw), int(y0 + bh)],
                        "strokes_xyxy": [ux1, uy1, ux2, uy2],
                    }
                )

    # Check 7: Pure-white-paper gray smudge vs Frameless speed-line/limb flat-patch scar (`图12 V14_089 #0`)
    in_bubble_set = {r_idx for r_list in bubble_to_regs.values() for r_idx, _, _ in r_list}
    for r_idx, reg in enumerate(regions):
        x1, y1, x2, y2 = region_bbox_xyxy(reg)
        bw, bh = x2 - x1, y2 - y1
        if bw < 8 or bh < 8:
            continue
        sx1, sy1 = max(0, int(x1) - 10), max(0, int(y1) - 10)
        sx2, sy2 = min(w, int(x2) + 10), min(h, int(y2) + 10)
        sub_b = base_gray[sy1:sy2, sx1:sx2]
        sub_i = inp_gray[sy1:sy2, sx1:sx2]
        sub_m = mask_bin[sy1:sy2, sx1:sx2]
        if sub_b.size > 0 and np.any(sub_m):
            rx1_o, ry1_o = max(0, sx1 - 14), max(0, sy1 - 14)
            rx2_o, ry2_o = min(w, sx2 + 14), min(h, sy2 + 14)
            outer_unmasked = (~mask_bin[ry1_o:ry2_o, rx1_o:rx2_o])
            outer_base = base_gray[ry1_o:ry2_o, rx1_o:rx2_o]
            ring_white_ratio = float(np.mean(outer_base[outer_unmasked] >= 242)) if np.any(outer_unmasked) else 0.0

            gx_o = cv2.Sobel(outer_base, cv2.CV_32F, 1, 0, ksize=3)
            gy_o = cv2.Sobel(outer_base, cv2.CV_32F, 0, 1, ksize=3)
            grad_o = np.hypot(gx_o, gy_o)
            outer_grad_p85 = float(np.percentile(grad_o[outer_unmasked], 85)) if np.any(outer_unmasked) else 0.0
            gx_i = cv2.Sobel(sub_i, cv2.CV_32F, 1, 0, ksize=3)
            gy_i = cv2.Sobel(sub_i, cv2.CV_32F, 0, 1, ksize=3)
            grad_i = np.hypot(gx_i, gy_i)
            flat_scar = sub_m & (grad_i < 4.5) & (sub_i >= 80) & (sub_i <= 242)
            scar_px = int(np.sum(flat_scar))

            if ring_white_ratio < 0.85 and outer_grad_p85 >= 24.0 and scar_px >= 2500:
                findings.append(
                    {
                        "severity": "error",
                        "category": "frameless_artwork_scar",
                        "region_index": int(r_idx),
                        "message": (
                            f"Frameless artwork Region #{r_idx} has a {scar_px}px flat gray/non-white inpaint scar "
                            f"over textured linework [{sx1}, {sy1}, {sx2}, {sy2}] — "
                            "NEVER use `solid_fill`/`telea` here! Use `tight_halo_rects` + full-res `lama_large`!"
                        ),
                    }
                )
            elif r_idx not in in_bubble_set and ring_white_ratio < 0.85:
                # Check 7b: Bloated mask outside tight glyph+halo channel unnecessarily destroying surrounding raw artwork
                # (characters, clothing, props, backgrounds, screentones, speed lines, or panel borders)!
                ex_x1, ex_y1 = max(0, int(x1) - 52), max(0, int(y1) - 24)
                ex_x2, ex_y2 = min(w, int(x2) + 52), min(h, int(y2) + 24)
                ex_m = mask_bin[ex_y1:ex_y2, ex_x1:ex_x2]
                if np.any(ex_m):
                    tight_m = extract_tight_halo_glyph_mask(base_rgb, [ex_x1, ex_y1, ex_x2, ex_y2], halo_dilate_px=6)[
                        ex_y1:ex_y2, ex_x1:ex_x2
                    ] > 0
                    over_m = ex_m & (~tight_m)
                    over_px = int(np.sum(over_m))
                    if over_px >= 1200:
                        ex_b = base_gray[ex_y1:ex_y2, ex_x1:ex_x2]
                        ex_i = inp_gray[ex_y1:ex_y2, ex_x1:ex_x2]
                        gb_x = cv2.Sobel(ex_b, cv2.CV_32F, 1, 0, ksize=3)
                        gb_y = cv2.Sobel(ex_b, cv2.CV_32F, 0, 1, ksize=3)
                        grad_b_ex = np.hypot(gb_x, gb_y)
                        gi_x = cv2.Sobel(ex_i, cv2.CV_32F, 1, 0, ksize=3)
                        gi_y = cv2.Sobel(ex_i, cv2.CV_32F, 0, 1, ksize=3)
                        grad_i_ex = np.hypot(gi_x, gi_y)
                        wiped_detail = over_m & (
                            ((grad_b_ex >= 24.0) & (grad_i_ex < 0.45 * grad_b_ex))
                            | ((grad_b_ex >= 34.0) & (np.abs(ex_b.astype(np.int16) - ex_i.astype(np.int16)) >= 22))
                        )
                        wiped_edge_px = int(np.sum(wiped_detail))
                        if wiped_edge_px >= 200:
                            findings.append(
                                {
                                    "severity": "error",
                                    "category": "frameless_overmasked_artwork",
                                    "region_index": int(r_idx),
                                    "suggested_tight_halo_rect_xyxy": [ex_x1, ex_y1, ex_x2, ex_y2],
                                    "message": (
                                        f"Frameless Region #{r_idx} ({visible_text_of_region(reg)!r}) over-masked {over_px}px outside glyph strokes "
                                        f"and damaged {wiped_edge_px}px of surrounding raw artwork (linework, screentones, clothing, props, speed lines, or character details) in "
                                        f"[{ex_x1}, {ex_y1}, {ex_x2}, {ex_y2}]! Use `mtui_edit_mask_and_inpaint(tight_halo_rects=[[{ex_x1}, {ex_y1}, {ex_x2}, {ex_y2}]], inpaint_method='neural')` "
                                        "to shrink the mask strictly to glyph+halo and minimize impact on the raw artwork!"
                                    ),
                                }
                            )
                # Check 7c: Un-erased stroked raw glyphs/furigana left behind around a frameless region on non-white artwork
                fx1, fy1 = max(0, int(x1) - 26), max(0, int(y1) - 26)
                fx2, fy2 = min(w, int(x2) + 26), min(h, int(y2) + 26)
                rem_halo_glyph = (
                    extract_tight_halo_glyph_mask(inpainted_rgb, [fx1, fy1, fx2, fy2], halo_dilate_px=0)[fy1:fy2, fx1:fx2] > 0
                ) & (~mask_bin[fy1:fy2, fx1:fx2])
                rem_glyph_px = int(np.sum(rem_halo_glyph))
                if rem_glyph_px >= 65:
                    ys_r, xs_r = np.nonzero(rem_halo_glyph)
                    ux1_f, uy1_f = fx1 + int(xs_r.min()), fy1 + int(ys_r.min())
                    ux2_f, uy2_f = fx1 + int(xs_r.max()) + 1, fy1 + int(ys_r.max()) + 1
                    findings.append(
                        {
                            "severity": "warning",
                            "category": "unmasked_adjacent_raw_text",
                            "region_index": int(r_idx),
                            "suggested_tight_halo_rect_xyxy": [max(0, ux1_f - 4), max(0, uy1_f - 4), min(w, ux2_f + 4), min(h, uy2_f + 4)],
                            "message": (
                                f"Frameless Region #{r_idx} ({visible_text_of_region(reg)!r}) still has ~{rem_glyph_px}px of un-erased raw stroked glyph/furigana "
                                f"over artwork at [{ux1_f}, {uy1_f}, {ux2_f}, {uy2_f}] — spot-repair via `tight_halo_rects` + `neural` without over-expanding the mask!"
                            ),
                        }
                    )
            elif r_idx in in_bubble_set or ring_white_ratio >= 0.90:
                gray_smudge = sub_m & (sub_b >= 245) & (sub_i >= 120) & (sub_i <= 234)
                if int(np.sum(gray_smudge)) >= 180:
                    findings.append(
                        {
                            "severity": "warning",
                            "category": "inpaint_gray_smudge",
                            "region_index": int(r_idx),
                            "message": (
                                f"Region #{r_idx} inside pure-white area has {int(np.sum(gray_smudge))}px of gray smudge "
                                f"in [{sx1}, {sy1}, {sx2}, {sy2}] — re-inpaint cleanly with `solid_fill`!"
                            ),
                        }
                    )
        if r_idx not in in_bubble_set:
            # Check extended strip along reading axis for un-erased raw characters on white background (e.g. partial bbox on footer/4-koma)
            d_raw = str(reg.get("direction") or "auto").lower()
            ex1 = max(0, int(x1) - (140 if d_raw in ("h", "horizontal", "hr") else 45))
            ex2 = min(w, int(x2) + (140 if d_raw in ("h", "horizontal", "hr") else 45))
            ey1 = max(0, int(y1) - (20 if d_raw in ("h", "horizontal", "hr") else 65))
            ey2 = min(h, int(y2) + (20 if d_raw in ("h", "horizontal", "hr") else 65))
            ext_b = base_gray[ey1:ey2, ex1:ex2]
            ext_i = inp_gray[ey1:ey2, ex1:ex2]
            ext_m = mask_bin[ey1:ey2, ex1:ex2]
            if ext_b.size > 0 and float(np.percentile(ext_b, 75)) >= 246.0:
                # Look for crisp dark strokes surrounded by white paper that were NOT masked
                unmasked_chars = (~ext_m) & (ext_b < 85) & (ext_i < 85)
                # Keep only small character-sized connected components (not long panel border lines!)
                nc, cl, cs, _ = cv2.connectedComponentsWithStats(unmasked_chars.astype(np.uint8), connectivity=8)
                char_px = 0
                c_rects = []
                for cid in range(1, nc):
                    cx0, cy0, cw0, ch0, carea = cs[cid]
                    touches_ext_edge = cx0 == 0 or cy0 == 0 or (cx0 + cw0) >= ext_b.shape[1] or (cy0 + ch0) >= ext_b.shape[0]
                    if not touches_ext_edge and 12 <= carea <= 1600 and cw0 <= 65 and ch0 <= 65:
                        # Check white paper immediately around this glyph
                        gx1, gy1 = max(0, cx0 - 3), max(0, cy0 - 3)
                        gx2, gy2 = min(ext_b.shape[1], cx0 + cw0 + 3), min(ext_b.shape[0], cy0 + ch0 + 3)
                        if float(np.mean(ext_i[gy1:gy2, gx1:gx2] >= 242)) >= 0.45:
                            char_px += int(carea)
                            c_rects.append([ex1 + int(cx0), ey1 + int(cy0), ex1 + int(cx0 + cw0), ey1 + int(cy0 + ch0)])
                if len(c_rects) >= 3 and char_px >= 90:
                    ux1 = min(r[0] for r in c_rects)
                    uy1 = min(r[1] for r in c_rects)
                    ux2 = max(r[2] for r in c_rects)
                    uy2 = max(r[3] for r in c_rects)
                    findings.append(
                        {
                            "severity": "warning",
                            "category": "unmasked_adjacent_raw_text",
                            "region_index": int(r_idx),
                            "message": (
                                f"Out-of-bubble Region #{r_idx} left {len(c_rects)} un-erased raw character component(s) ({char_px}px) "
                                f"on white background at [{ux1}, {uy1}, {ux2}, {uy2}] — expand mask and re-inpaint cleanly!"
                            ),
                            "suggested_mask_rect_xyxy": [ux1 - 3, uy1 - 3, ux2 + 3, uy2 + 3],
                        }
                    )

            # Check 10 (`图6` fallback): Panel-edge-clipped bright shout bubble (`lbl == 0`) with giant overflowing font_size
            sw_val_o = float(reg.get("stroke_width", reg.get("default_stroke_width", 0.07)) or 0.07)
            fs_val_o = int(round(float(reg.get("font_size") or 16)))
            vis_o = visible_text_of_region(reg).strip()
            lns_o = [ln.strip() for ln in vis_o.split("\n") if ln.strip()]
            if sw_val_o < 0.16 and fs_val_o >= 48 and len(lns_o) >= 2 and sub_i.size > 0 and float(np.percentile(sub_i, 80)) >= 236.0:
                line_sp_o = max(0.85, float(reg.get("line_spacing", 1.0) or 1.0))
                let_sp_o = max(0.85, float(reg.get("letter_spacing", 1.0) or 1.0))
                est_w_o = len(lns_o) * fs_val_o * line_sp_o
                est_h_o = max(_count_vertical_cells_in_line(ln) for ln in lns_o) * fs_val_o * let_sp_o
                if est_w_o > max(bw * 1.06, 170.0) or est_h_o > max(bh * 1.12, 220.0):
                    findings.append(
                        {
                            "severity": "error",
                            "category": "text_overflows_bubble",
                            "region_index": int(r_idx),
                            "message": (
                                f"Region #{r_idx} ({vis_o!r}, font_size={fs_val_o}px) in panel-clipped bright bubble has "
                                f"estimated text block [{int(round(est_w_o))}x{int(round(est_h_o))}px] exceeding region bbox "
                                f"[{int(round(bw))}x{int(round(bh))}px] — recalibrate font_size when adding columns!"
                            ),
                        }
                    )

            # Check 11 (`图4`): Top/bottom margin running book/series title header (`ソード・オラトリア [15]`)
            src_o = str(reg.get("text", "") or "").strip()
            comb_o = f"{src_o} {vis_o}".strip()
            if (
                vis_o
                and "\n" not in vis_o
                and bw >= max(55.0, bh * 1.5)
                and (y2 <= 0.118 * h or y1 >= 0.935 * h)
                and not _CHAPTER_OR_ENDNOTE_HEADER_RE.search(comb_o)
                and sub_b.size > 0
                and float(np.percentile(sub_b, 75)) >= 232.0
                and (_RUNNING_TITLE_VOL_RE.search(src_o) or _RUNNING_TITLE_VOL_RE.search(vis_o))
            ):
                findings.append(
                    {
                        "severity": "warning",
                        "category": "running_header_book_title",
                        "region_index": int(r_idx),
                        "message": (
                            f"Region #{r_idx} ({vis_o!r}, raw={src_o!r}) is a page-margin running series/book title header — "
                            "running book titles must NOT be translated; delete region with `restore_artwork=True`!"
                        ),
                    }
                )

    # Check 12 (`图1`): Table of Contents (TOC) alignment, `CONTENTS` duplication & un-erased raw digits/furigana over colored artwork
    for r_idx, reg in enumerate(regions):
        vis_toc = visible_text_of_region(reg).strip()
        if not vis_toc:
            continue
        toc_lines = [ln.strip() for ln in vis_toc.split("\n") if ln.strip()]
        n_toc_hits = sum(1 for ln in toc_lines if _TOC_LINE_RE.search(ln))
        if n_toc_hits >= 2:
            al_toc = str(reg.get("alignment") or "auto").strip().lower()
            has_dup_contents = any(re.match(r"^(?:contents|目\s*[次录錄])$", ln, re.IGNORECASE) for ln in toc_lines)
            if al_toc in ("auto", "center") or has_dup_contents:
                reasons = []
                if al_toc in ("auto", "center"):
                    reasons.append(f"uses alignment={al_toc!r} instead of 'left' (or per-row left-aligned regions)")
                if has_dup_contents:
                    reasons.append("duplicates the untouched English 'CONTENTS' graphic header inside the translated text block")
                findings.append(
                    {
                        "severity": "error",
                        "category": "toc_alignment_or_merge_error",
                        "region_index": int(r_idx),
                        "message": (
                            f"TOC Region #{r_idx} {'; and '.join(reasons)} — keep English 'CONTENTS' untouched, "
                            "place each TOC chapter row at its raw Y coordinate with `alignment='left'`!"
                        ),
                    }
                )
            # Scan partially-masked horizontal TOC rows on `inpainted_rgb` for un-erased page numbers (`003`, `145`) & furigana over colored background!
            tx1, ty1, tx2, ty2 = region_bbox_xyxy(reg)
            bx1 = max(0, int(tx1) - 180)
            bx2 = min(w, int(tx2) + 220)
            by1 = max(0, int(ty1) - 240)
            by2 = min(h, int(ty2) + 320)
            if bx2 > bx1 + 40 and by2 > by1 + 40 and np.any(mask_bin[by1:by2, bx1:bx2]):
                band_inp = inp_gray[by1:by2, bx1:bx2]
                band_mask = mask_bin[by1:by2, bx1:bx2]
                bg_loc = cv2.dilate(band_inp, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19)))
                unmasked_glyphs = (~band_mask) & ((bg_loc.astype(np.int16) - band_inp.astype(np.int16)) >= 48)
                # Dilate masked rows horizontally across the whole band + ±18px vertically so ONLY rows that were partially erased (never untouched `CONTENTS`!) are checked
                row_has_mask = np.sum(band_mask, axis=1) >= 28
                row_mask_1d = (
                    cv2.dilate(row_has_mask.astype(np.uint8).reshape(-1, 1), np.ones((37, 1), dtype=np.uint8)).ravel() > 0
                )
                active_rows_2d = np.repeat(row_mask_1d[:, None], band_inp.shape[1], axis=1)
                cand_toc_rem = unmasked_glyphs & active_rows_2d
                if np.any(cand_toc_rem):
                    ntc, _, tcs, _ = cv2.connectedComponentsWithStats(cand_toc_rem.astype(np.uint8), connectivity=8)
                    rem_rects: list[list[int]] = []
                    rem_px = 0
                    for cid in range(1, ntc):
                        cx0, cy0, cw0, ch0, carea = tcs[cid]
                        if 10 <= carea <= 1800 and 4 <= cw0 <= 58 and 8 <= ch0 <= 58:
                            rem_rects.append([bx1 + int(cx0), by1 + int(cy0), bx1 + int(cx0 + cw0), by1 + int(cy0 + ch0)])
                            rem_px += int(carea)
                    if len(rem_rects) >= 3 and rem_px >= 65:
                        ux1 = min(r[0] for r in rem_rects)
                        uy1 = min(r[1] for r in rem_rects)
                        ux2 = max(r[2] for r in rem_rects)
                        uy2 = max(r[3] for r in rem_rects)
                        findings.append(
                            {
                                "severity": "error",
                                "category": "toc_unmasked_raw_text",
                                "region_index": int(r_idx),
                                "message": (
                                    f"TOC Region #{r_idx} left {len(rem_rects)} un-erased raw glyph/page-number component(s) "
                                    f"({rem_px}px, e.g. trailing '003'/'145' or furigana) on partially masked TOC rows at "
                                    f"[{ux1}, {uy1}, {ux2}, {uy2}] — mask tight glyph strokes across the full TOC row and repair with `lama_large`!"
                                ),
                                "suggested_mask_rect_xyxy": [max(0, ux1 - 4), max(0, uy1 - 4), min(w, ux2 + 4), min(h, uy2 + 4)],
                            }
                        )

    # Check 6: Next-volume preview / ad page out-of-bubble promotional text (exclude colophon pages with 発行所/株式会社)
    page_blob = " ".join(f"{r.get('text', '')} {visible_text_of_region(r)}" for r in regions)
    if re.search(r"(発売予定|予定.*発売|预定.*发售)", page_blob) and not re.search(r"(発行所|发行所|株式会社)", page_blob):
        in_bubble_ridxs = {r_idx for r_list in bubble_to_regs.values() for r_idx, _, _ in r_list}
        for r_idx, reg in enumerate(regions):
            if r_idx not in in_bubble_ridxs:
                findings.append(
                    {
                        "severity": "warning",
                        "category": "preview_ad_out_of_bubble_translation",
                        "region_index": int(r_idx),
                        "message": (
                            f"Region #{r_idx} ({visible_text_of_region(reg)!r}) is promotional out-of-bubble text on a preview/ad page — "
                            "only translate enclosed panel bubbles; delete out-of-bubble ad slogans with restore_artwork=True!"
                        ),
                    }
                )

    return findings


def audit_single_page(
    paths: PagePaths,
    *,
    check_inpaint_cv: bool = True,
    check_font_glyphs: bool = True,
    default_font_family: str = "",
    default_target_lang: str = "CHS",
    default_alignment: str = "",
    yuzumarker_page_cache: Optional[dict[str, Any]] = None,
    glossary_terms: Optional[tuple[GlossaryTerm, ...]] = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    if not paths.json_exists:
        issues.append(
            {
                "severity": "error",
                "category": "missing_json",
                "region_index": None,
                "message": "No *_translations.json found for this page (ensure '图片可编辑 / save_text' was enabled).",
            }
        )
        return {
            "page_name": paths.source_path.name,
            "source_path": str(paths.source_path),
            "region_count": 0,
            "issue_count": len(issues),
            "issues": issues,
        }

    _, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])

    if regions and not paths.inpainted_exists:
        issues.append(
            {
                "severity": "warning",
                "category": "missing_inpainted_sidecar",
                "region_index": None,
                "message": "Inpainted sidecar image is missing; re-rendering with backend_inpaint or edit_mask_and_inpaint will regenerate it.",
            }
        )

    if not paths.rendered_output_exists:
        issues.append(
            {
                "severity": "info",
                "category": "missing_rendered_output",
                "region_index": None,
                "message": f"Rendered output image not yet present at {paths.rendered_output_path}.",
            }
        )

    base_rgb: Optional[np.ndarray] = None
    img_w, img_h = 0, 0
    if paths.display_base_path.is_file():
        base_rgb = open_rgb_image(paths.display_base_path)
        img_h, img_w = base_rgb.shape[:2]

    bboxes: list[tuple[int, list[float]]] = []
    font_missing_map: dict[str, set[str]] = {}
    eff_glossary = load_active_glossary_terms() if glossary_terms is None else glossary_terms
    eff_default_align = (default_alignment or "").strip().lower()
    if not eff_default_align:
        try:
            cfg_al = str((get_runtime_settings().load_mtui_config_dict().get("render") or {}).get("alignment") or "").strip().lower()
            if cfg_al in ("left", "center", "right", "auto"):
                eff_default_align = cfg_al
        except Exception:
            pass

    for idx, reg in enumerate(regions):
        src_txt = str(reg.get("text", "") or "").strip()
        vis_txt = visible_text_of_region(reg).strip()
        target_lang = str(reg.get("target_lang") or default_target_lang).upper()
        prob = float(reg.get("prob", 1.0) or 1.0)
        fs = int(round(float(reg.get("font_size") or 16)))
        bbox = region_bbox_xyxy(reg)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bboxes.append((idx, bbox))

        # 1. Empty or untranslated check
        if src_txt and not vis_txt:
            issues.append(
                {
                    "severity": "error",
                    "category": "empty_translation",
                    "region_index": idx,
                    "message": f"Region #{idx} has source text {src_txt!r} but empty translation.",
                }
            )
        elif vis_txt and target_lang in ("CHS", "CHT", "ENG"):
            if _KANA_RE.search(vis_txt):
                issues.append(
                    {
                        "severity": "warning",
                        "category": "untranslated_kana",
                        "region_index": idx,
                        "message": f"Region #{idx} translation still contains Japanese kana: {vis_txt!r} (source: {src_txt!r}).",
                    }
                )
            if _HANGUL_RE.search(vis_txt):
                issues.append(
                    {
                        "severity": "warning",
                        "category": "untranslated_hangul",
                        "region_index": idx,
                        "message": f"Region #{idx} translation still contains Korean Hangul: {vis_txt!r}.",
                    }
                )

        # 2. Low OCR confidence
        if prob < 0.45 and src_txt:
            issues.append(
                {
                    "severity": "warning",
                    "category": "low_ocr_confidence",
                    "region_index": idx,
                    "message": f"Region #{idx} had low OCR confidence ({prob:.2f}) on {src_txt!r} — verify against original artwork.",
                }
            )

        # 2b. Semantic, Glossary, OCR-Corruption, Number-Drop & Polarity Mistranslation Checks
        if src_txt and vis_txt and target_lang in ("CHS", "CHT"):
            issues.extend(audit_region_translation_accuracy(idx, reg, glossary_terms=eff_glossary))

        # 3. Line-break & punctuation hygiene
        if vis_txt:
            lines = [ln for ln in vis_txt.split("\n")]
            for l_no, ln in enumerate(lines):
                stripped = ln.strip()
                if l_no > 0 and stripped and stripped[0] in _CLOSING_START_PUNCT:
                    issues.append(
                        {
                            "severity": "warning",
                            "category": "linebreak_leading_punct",
                            "region_index": idx,
                            "message": f"Region #{idx} line {l_no + 1} starts with closing punctuation {stripped[0]!r} in {vis_txt!r}.",
                        }
                    )
                if l_no < len(lines) - 1 and stripped and stripped[-1] in _OPENING_END_PUNCT:
                    issues.append(
                        {
                            "severity": "warning",
                            "category": "linebreak_trailing_open_punct",
                            "region_index": idx,
                            "message": f"Region #{idx} line {l_no + 1} ends with opening bracket/quote {stripped[-1]!r} in {vis_txt!r}.",
                        }
                    )
                # Prosodic breath check: enclitic particle (`了/着/过/的/得/们`) stranded at the start of a new column
                if l_no > 0 and stripped and stripped[0] in "了着过的得们" and not _VALID_CLITIC_START_WORDS_RE.search(stripped):
                    issues.append(
                        {
                            "severity": "warning",
                            "category": "linebreak_broken_breath",
                            "region_index": idx,
                            "message": (
                                f"Region #{idx} line {l_no + 1} ({stripped!r}) starts with enclitic particle {stripped[0]!r} — "
                                "breaks natural Chinese reading breath! Keep enclitic particles attached to the preceding word and re-balance columns."
                            ),
                        }
                    )
            if len(lines) >= 2:
                last_clean = re.sub(r"[\W_]+", "", lines[-1])
                prev_clean = re.sub(r"[\W_]+", "", lines[-2])
                if len(last_clean) == 1 and len(prev_clean) >= 5:
                    issues.append(
                        {
                            "severity": "warning",
                            "category": "orphan_single_char_line",
                            "region_index": idx,
                            "message": f"Region #{idx} ends with a 1-char orphan line {lines[-1]!r} after {lines[-2]!r} — re-balance line breaks to fit container!",
                        }
                    )

        # 4. Typography, Alignment, Unified Bubble-Contour Silhouette & Direction vs Aspect Ratio
        dir_raw = str(reg.get("direction") or "auto").lower()
        dir_short = {"horizontal": "h", "vertical": "v", "hr": "h", "vr": "v"}.get(dir_raw, dir_raw)
        is_eff_vert = dir_short == "v" or (
            dir_short == "auto" and not (bw > 0 and bh > 0 and (bw / max(bh, 1.0)) > 1.45 and "\n" not in vis_txt)
        )
        # 4a: Vertical column-top alignment check (`alignment="left"` = top-aligned / 齐顶 in MTUI vertical text!)
        if vis_txt and is_eff_vert and eff_default_align != "center":
            reg_align = str(reg.get("alignment") or "auto").strip().lower()
            if reg_align in ("center", "right"):
                n_cols_align = len([ln for ln in vis_txt.split("\n") if ln.strip()])
                if eff_default_align == "left":
                    al_sev = "error" if n_cols_align >= 2 else "warning"
                else:
                    al_sev = "warning" if n_cols_align >= 2 else ""
                if al_sev:
                    issues.append(
                        {
                            "severity": al_sev,
                            "category": "vertical_alignment_mismatch",
                            "region_index": idx,
                            "current_alignment": reg_align,
                            "expected_alignment": "left",
                            "column_count": n_cols_align,
                            "message": (
                                f"Region #{idx} ({vis_txt!r}) is vertical ({n_cols_align} col) with explicit alignment={reg_align!r}, "
                                f"violating top-aligned manga layout (project default={eff_default_align or 'left'!r})! "
                                "In MTUI vertical text, `alignment='left'` means column top-aligned (齐顶); "
                                "NEVER set `alignment='center'` to center a bubble — set `alignment='left'` and use `center-bubbles` for optical block centering!"
                            ),
                        }
                    )
        # 3b: Unified multi-column silhouette check (`N >= 2`, covering `[3,10,1]`, `[5,5,1]`, `[6,1]`, `[2,9,3]`, `[3,7,8,2,9]`, `[4,9,4]`, `[3,5,7]`, `[8,10]`,
        # while allowing natural flexible trade-offs like `[5,6,3]`, `[6,5,6]`, `[5,4,5]`, `[6,6,4]`, `[3,7,8,5,5]`!)
        if vis_txt and is_eff_vert:
            ne_lines = [ln.strip() for ln in vis_txt.split("\n") if ln.strip()]
            n_cols_v = len(ne_lines)
            if n_cols_v >= 2:
                col_cells = [_count_vertical_cells_in_line(ln) for ln in ne_lines]
                total_cells = sum(col_cells)
                max_c = max(col_cells)
                ideal_c = max(4, int(round(total_cells / n_cols_v)))
                first_has_pause = ne_lines[0].endswith(("！", "!", "？", "?", "︙", "…", "，", "、", "：", ":"))
                last_alnum = re.sub(r"[\W_]+", "", ne_lines[-1])

                # A) 1-char orphan tail or interior 1-char column (`*** / ********** / *` [3,10,1], [5,5,1], [6,1])
                is_orphan_tail = (
                    (col_cells[-1] == 1 and max_c >= 4)
                    or (len(last_alnum) == 1 and col_cells[-1] <= 2 and max_c >= 6)
                )
                is_interior_one = any(col_cells[k] == 1 for k in range(1, n_cols_v - 1)) and max_c >= 3

                # B) Deep interior notch (`[3,7,8,2,9]` or `[6,2,6]`, while allowing mild `[6,5,6]` / `[5,4,5]`)
                is_interior_valley = any(
                    col_cells[k] <= 3 and col_cells[k] <= 0.52 * min(col_cells[k - 1], col_cells[k + 1])
                    for k in range(1, n_cols_v - 1)
                )

                # C) Severe spike vs starved non-final column (`[2,9,3]`, `[3,10,1]`, `[4,9,4]`, `[2,8]`, `[2,6,10,5]`)
                non_final_starved = False
                extreme_spike = False
                for k in range(n_cols_v - 1):
                    if k == 0 and first_has_pause and col_cells[0] >= 2:
                        continue
                    c_k = col_cells[k]
                    if max_c >= 7 and c_k <= 4 and max_c >= 2.15 * c_k and (max_c - c_k) >= 5:
                        non_final_starved = True
                        if c_k <= 3 or (max_c - c_k) >= 6:
                            extreme_spike = True
                    elif c_k == 1 and max_c >= 4:
                        non_final_starved = True
                        extreme_spike = True

                # D) Outer-wall overhang or unpaused bottom-heavy wedge (`[3,5,7]`, `[3,7,8,5,9]`)
                is_inverted_wedge = (
                    not first_has_pause and col_cells[0] <= 3 and col_cells[-1] >= 7 and col_cells[-1] >= 2.0 * col_cells[0]
                )
                is_outer_wall_spike = (
                    n_cols_v >= 4 and col_cells[-1] >= 8 and col_cells[-1] >= float(np.median(col_cells[:-1])) * 1.32
                )
                is_2col_noodle = n_cols_v == 2 and total_cells >= 16 and max_c >= 9 and bw >= 85.0

                if (
                    is_orphan_tail
                    or is_interior_one
                    or is_interior_valley
                    or non_final_starved
                    or is_inverted_wedge
                    or is_outer_wall_spike
                    or is_2col_noodle
                ):
                    is_severe = bool(
                        (col_cells[-1] == 1 and max_c >= 6)
                        or is_interior_one
                        or is_interior_valley
                        or extreme_spike
                    )
                    issues.append(
                        {
                            "severity": "error" if is_severe else "warning",
                            "category": "unbalanced_linebreak_columns",
                            "region_index": idx,
                            "column_lengths": col_cells,
                            "message": (
                                f"Region #{idx} ({vis_txt!r}) has poorly conformed column lengths {col_cells} — "
                                f"weigh natural Chinese breath pauses with the bubble's actual contour (~{ideal_c} chars/col; "
                                "use secondary breath boundaries or slight phrasing tweaks to avoid 1-char orphans like [3,10,1], spikes like [2,9,3], or interior valleys like [3,7,8,2,9])!"
                            ),
                        }
                    )
        if vis_txt and is_eff_vert:
            bad_v_syms = sorted({ch for ch in vis_txt if ch in "—─━═–―…⋯（）()【】《》〈〉〔〕「」『』〜〰～"})
            if bad_v_syms:
                issues.append(
                    {
                        "severity": "error",
                        "category": "unrotated_vertical_symbol",
                        "region_index": idx,
                        "message": (
                            f"Region #{idx} is vertical ('{dir_raw}') but contains horizontal/unrotated symbol(s) "
                            f"{''.join(bad_v_syms)!r} in {vis_txt!r}! Must use vertical forms (││, ︙, ︻︼, ︵︶, ﹁﹂, ﹃﹄, ︽︾, ︿﹀, ︴)."
                        ),
                    }
                )
            dbl_rot_syms = _find_double_rotated_vertical_glyphs(reg.get("translation_rich"))
            if dbl_rot_syms:
                issues.append(
                    {
                        "severity": "error",
                        "category": "double_rotated_vertical_symbol",
                        "region_index": idx,
                        "message": (
                            f"Region #{idx} is vertical and already uses vertical presentation glyph(s) "
                            f"{''.join(dbl_rot_syms)!r} in {vis_txt!r}, yet `translation_rich` ALSO applies `transform.rotation` (±90°), "
                            "rotating them sideways back to horizontal! Re-save via `mtui_update_regions` (or clear rotation)."
                        ),
                    }
                )
        elif vis_txt and dir_short == "h":
            bad_h_syms = sorted({ch for ch in vis_txt if ch in "│︱┃║︲︙︰︵︶︻︼︽︾︿﹀︹︺﹁﹂﹃﹄︴"})
            if bad_h_syms:
                issues.append(
                    {
                        "severity": "error",
                        "category": "vertical_symbol_in_horizontal",
                        "region_index": idx,
                        "message": (
                            f"Region #{idx} is horizontal ('h') but contains vertical presentation symbol(s) "
                            f"{''.join(bad_h_syms)!r} in {vis_txt!r}!"
                        ),
                    }
                )
        if bw > 0 and bh > 0 and len(vis_txt) >= 3:
            aspect = bw / bh
            if dir_short == "h" and aspect < 0.48 and bh > 60:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "direction_aspect_mismatch",
                        "region_index": idx,
                        "message": f"Region #{idx} is tall & narrow ({bw:.0f}x{bh:.0f}px) but set to horizontal ('h'); vertical ('v') likely fits better.",
                    }
                )
            elif dir_short == "v" and aspect > 2.1 and bw > 60 and "\n" not in vis_txt:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "direction_aspect_mismatch",
                        "region_index": idx,
                        "message": f"Region #{idx} is wide & flat ({bw:.0f}x{bh:.0f}px) with 1 column but set to vertical ('v'); horizontal ('h') or multi-column `[BR]` likely fits better.",
                    }
                )

        if vis_txt and fs < 12:
            issues.append(
                {
                    "severity": "warning",
                    "category": "tiny_font_size",
                    "region_index": idx,
                    "message": f"Region #{idx} has very small font_size={fs}px — may be hard to read.",
                }
            )

        # 4b. Spurious detector tilt inside bright speech/burst bubbles
        ang = float(reg.get("angle", 0.0) or 0.0)
        if vis_txt and 1.2 <= abs(ang) <= 15.0 and base_rgb is not None:
            cx, cy = region_center(reg)
            ix0 = max(0, int(round(cx)) - 18)
            ix1 = min(img_w, int(round(cx)) + 19)
            iy0 = max(0, int(round(cy)) - 18)
            iy1 = min(img_h, int(round(cy)) + 19)
            if ix1 > ix0 and iy1 > iy0:
                # High p85 luminance around center indicates a white speech/burst bubble interior
                patch_luma = np.mean(base_rgb[iy0:iy1, ix0:ix1], axis=2)
                if float(np.percentile(patch_luma, 85)) >= 238.0:
                    issues.append(
                        {
                            "severity": "info",
                            "category": "suspect_bubble_tilt",
                            "region_index": idx,
                            "message": (
                                f"Region #{idx} inside bright bubble has non-zero angle={ang:+.2f}° in {vis_txt!r}; "
                                "verify raw kanji horizontal strokes — if upright in raw artwork (common OBB false tilt on unequal column lengths), set angle=0.0."
                            ),
                        }
                    )

        if img_w > 0 and img_h > 0:
            if bbox[0] < -5 or bbox[1] < -5 or bbox[2] > img_w + 5 or bbox[3] > img_h + 5:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "bbox_out_of_bounds",
                        "region_index": idx,
                        "message": f"Region #{idx} bbox {bbox} extends outside page dimensions ({img_w}x{img_h}).",
                    }
                )

        # 5. Missing glyph & bubble-font sanity checks
        if vis_txt:
            eff_font_chk = str(reg.get("font_family") or default_font_family).strip()
            if eff_font_chk == "FZYaSong-R-GBK" and len(vis_txt) <= 45:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "novel_thin_songti_in_bubble",
                        "region_index": idx,
                        "message": (
                            f"Region #{idx} ({vis_txt!r}) uses ultra-thin novel font 'FZYaSong-R-GBK' inside a short bubble — "
                            "inspect 1:1 native raw glyphs: thin serifless/quirky pen strokes must use 'FZZhiYi-M12S' / 'Yozai' / 'Xiaolai', "
                            "and normal Mincho dialogue must use 'FZYaSong-DB-GBK'!"
                        ),
                    }
                )
        if check_font_glyphs and vis_txt:
            eff_font = str(reg.get("font_family") or default_font_family).strip()
            if eff_font:
                missing_chars = check_region_missing_glyphs(reg, default_font_family)
                if missing_chars:
                    font_missing_map.setdefault(eff_font, set()).update(missing_chars)
                    issues.append(
                        {
                            "severity": "error",
                            "category": "missing_font_glyphs",
                            "region_index": idx,
                            "message": (
                                f"Region #{idx}: font {eff_font!r} is missing glyph(s) "
                                f"{''.join(missing_chars)!r} — will render tofu boxes unless font_family or character is changed!"
                            ),
                        }
                    )

    # 6. Overlapping regions & rendered text-block collision check (`图6` V17_031 #2 colliding with #3!)
    est_render_rects: list[tuple[int, list[float]]] = []
    for idx_r, reg_r in enumerate(regions):
        vt_r = visible_text_of_region(reg_r).strip()
        if not vt_r:
            continue
        lns_r = [ln.strip() for ln in vt_r.split("\n") if ln.strip()]
        if not lns_r:
            continue
        fs_r = float(reg_r.get("font_size") or 16)
        lsp_r = max(0.85, float(reg_r.get("line_spacing", 1.0) or 1.0))
        csp_r = max(0.85, float(reg_r.get("letter_spacing", 1.0) or 1.0))
        bx1_r, by1_r, bx2_r, by2_r = region_bbox_xyxy(reg_r)
        dr_r = str(reg_r.get("direction") or "auto").lower()
        is_v_r = dr_r in ("v", "vertical", "vr") or (
            dr_r == "auto" and not ((bx2_r - bx1_r) / max(1.0, by2_r - by1_r) > 1.45 and len(lns_r) == 1)
        )
        if is_v_r:
            ew_r = len(lns_r) * fs_r * lsp_r * 1.16
            eh_r = max(_count_vertical_cells_in_line(ln) for ln in lns_r) * fs_r * csp_r * 1.04
        else:
            ew_r = max(len(ln) for ln in lns_r) * fs_r * csp_r * 1.04
            eh_r = len(lns_r) * fs_r * lsp_r * 1.16
        cx_r, cy_r = region_center(reg_r)
        est_render_rects.append((idx_r, [cx_r - ew_r * 0.50, cy_r - eh_r * 0.50, cx_r + ew_r * 0.50, cy_r + eh_r * 0.50]))

    emitted_overlap_pairs: set[tuple[int, int]] = set()
    for i in range(len(bboxes)):
        idx_a, box_a = bboxes[i]
        for j in range(i + 1, len(bboxes)):
            idx_b, box_b = bboxes[j]
            overlap = _boxes_iou(box_a, box_b)
            if overlap >= 0.45:
                emitted_overlap_pairs.add((idx_a, idx_b))
                issues.append(
                    {
                        "severity": "error",
                        "category": "overlapping_regions",
                        "region_index": idx_a,
                        "message": f"Regions #{idx_a} and #{idx_b} overlap heavily (overlap={overlap:.2f}) — merge or reposition so text never collides!",
                    }
                )
    for i in range(len(est_render_rects)):
        idx_a, rbox_a = est_render_rects[i]
        for j in range(i + 1, len(est_render_rects)):
            idx_b, rbox_b = est_render_rects[j]
            if (idx_a, idx_b) in emitted_overlap_pairs:
                continue
            ix_w = min(rbox_a[2], rbox_b[2]) - max(rbox_a[0], rbox_b[0])
            iy_h = min(rbox_a[3], rbox_b[3]) - max(rbox_a[1], rbox_b[1])
            if ix_w >= 8.0 and iy_h >= 8.0:
                issues.append(
                    {
                        "severity": "error",
                        "category": "rendered_regions_overlap",
                        "region_index": idx_a,
                        "message": (
                            f"Rendered text blocks of Region #{idx_a} ({visible_text_of_region(regions[idx_a])!r}) and "
                            f"Region #{idx_b} ({visible_text_of_region(regions[idx_b])!r}) collide by {int(round(ix_w))}x{int(round(iy_h))}px (`图6`)! "
                            "Re-break columns shorter, reduce font_size, and re-run `center-bubbles` so adjacent chambers NEVER overlap!"
                        ),
                    }
                )

    # 6b. Same-page Songti + Heiti mixed-dialogue continuity check
    songti_idxs = [
        i
        for i, r in enumerate(regions)
        if any(k in str(r.get("font_family") or "") for k in ("FZYaSong", "FZCuSong", "Source Han Serif"))
    ]
    heiti_idxs = [
        i
        for i, r in enumerate(regions)
        if str(r.get("font_family") or default_font_family).strip() == "FZHei-B01"
    ]
    if songti_idxs and heiti_idxs:
        issues.append(
            {
                "severity": "info",
                "category": "same_page_serif_sans_mix",
                "region_index": songti_idxs[0],
                "message": (
                    f"Page mixes Songti regions {songti_idxs} and Heiti ('FZHei-B01') regions {heiti_idxs} — "
                    "verify 1:1 native raw kana on same-speaker consecutive bubbles so one continuous sentence is never split half-Heiti / half-Songti!"
                ),
            }
        )

    # 7. CV Residual Ink, Structural, Overflow, Bold & Artwork Scar checks
    if check_inpaint_cv and base_rgb is not None and paths.inpainted_exists:
        inpainted_rgb = open_rgb_image(paths.inpainted_path)
        rendered_rgb = open_rgb_image(paths.rendered_output_path) if paths.rendered_output_exists else None
        initial_regions = _load_initial_snapshot_regions(paths)
        mask_u8 = decode_mask_base64(image_data.get("mask_raw"), base_rgb.shape[:2])
        cv_findings = _detect_residual_ink_around_regions(base_rgb, inpainted_rgb, mask_u8, regions)
        issues.extend(cv_findings)
        if yuzumarker_page_cache is None:
            w_cache = load_font_detection_cache(paths.work_dir)
            yuzumarker_page_cache = (w_cache.get("pages") or {}).get(paths.source_path.stem)
        struct_findings = _detect_structural_and_border_cv_issues(
            base_rgb,
            inpainted_rgb,
            mask_u8,
            regions,
            rendered_rgb=rendered_rgb,
            initial_regions=initial_regions,
            yuzumarker_page_cache=yuzumarker_page_cache,
        )
        issues.extend(struct_findings)

    return {
        "page_name": paths.source_path.name,
        "source_path": str(paths.source_path),
        "region_count": len(regions),
        "issue_count": len(issues),
        "issues": issues,
    }


def run_qa_audit(
    target_path: str | Path,
    *,
    check_inpaint_cv: bool = True,
    check_font_glyphs: bool = True,
    only_pages_with_issues: bool = False,
    force_yuzumarker: bool = False,
    glossary_file: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Run automated QA audit across a manga page or entire chapter folder."""
    pages = list_manga_pages_in_path(target_path)
    mtui_cfg = get_runtime_settings().load_mtui_config_dict()
    default_font = str((mtui_cfg.get("render") or {}).get("font_family") or "")
    default_lang = str((mtui_cfg.get("translator") or {}).get("target_lang") or "CHS")
    default_align = str((mtui_cfg.get("render") or {}).get("alignment") or "").strip().lower()
    active_glossary = load_active_glossary_terms(glossary_file)

    # Ensure local YuzuMarker 4x Ensemble font cache is populated in production runs
    ym_caches_by_work: dict[Path, dict[str, Any]] = {}
    if check_inpaint_cv and pages and yuzumarker_models_available():
        for p in pages:
            if p.work_dir not in ym_caches_by_work:
                ym_caches_by_work[p.work_dir] = load_font_detection_cache(p.work_dir)
        needs_scan = any(
            p.json_exists
            and (
                int(ym_caches_by_work[p.work_dir].get("version") or 1) < CACHE_VERSION
                or p.source_path.stem not in (ym_caches_by_work[p.work_dir].get("pages") or {})
            )
            for p in pages
        )
        if force_yuzumarker or (needs_scan and "PYTEST_CURRENT_TEST" not in os.environ):
            try:
                from .worker.client import get_worker_client

                get_worker_client().detect_fonts(target_path, force_refresh=force_yuzumarker)
                ym_caches_by_work = {w: load_font_detection_cache(w) for w in ym_caches_by_work}
            except Exception:
                pass

    page_reports: list[dict[str, Any]] = []
    counts_by_category: dict[str, int] = {}
    total_issues = 0

    all_reports: list[tuple[PagePaths, dict[str, Any]]] = []
    for p in pages:
        p_ym_cache = ((ym_caches_by_work.get(p.work_dir) or {}).get("pages") or {}).get(p.source_path.stem)
        rep = audit_single_page(
            p,
            check_inpaint_cv=check_inpaint_cv,
            check_font_glyphs=check_font_glyphs,
            default_font_family=default_font,
            default_target_lang=default_lang,
            default_alignment=default_align,
            yuzumarker_page_cache=p_ym_cache,
            glossary_terms=active_glossary,
        )
        all_reports.append((p, rep))

    # Cross-page repeated top/bottom margin running series-title detector (`图4`, even when volume digits are omitted!)
    if len(all_reports) >= 4:
        margin_groups: dict[str, list[tuple[dict[str, Any], int, str, str]]] = {}
        for p, rep in all_reports:
            if not p.json_exists or not p.display_base_path.is_file():
                continue
            try:
                from PIL import Image as _PILImage

                with _PILImage.open(p.display_base_path) as _im:
                    _, ph = _im.size
            except Exception:
                continue
            _, pdata = load_page_json(p)
            pregs = pdata.get("regions", [])
            already_hdr = {iss.get("region_index") for iss in rep["issues"] if iss.get("category") == "running_header_book_title"}
            for r_idx, reg in enumerate(pregs):
                if r_idx in already_hdr:
                    continue
                x1, y1, x2, y2 = region_bbox_xyxy(reg)
                bw, bh = x2 - x1, y2 - y1
                vis_t = visible_text_of_region(reg).strip()
                src_t = str(reg.get("text", "") or "").strip()
                comb_t = f"{src_t} {vis_t}".strip()
                if (
                    vis_t
                    and "\n" not in vis_t
                    and bw >= max(55.0, bh * 1.5)
                    and (y2 <= 0.118 * ph or y1 >= 0.935 * ph)
                    and not _CHAPTER_OR_ENDNOTE_HEADER_RE.search(comb_t)
                ):
                    norm_key = re.sub(r"[\s・·:：\-—\d①-⑳\(\)（）\[\]［］]+", "", src_t or vis_t).casefold()
                    if len(norm_key) >= 3:
                        margin_groups.setdefault(norm_key, []).append((rep, r_idx, vis_t, src_t))
        for norm_key, hits in margin_groups.items():
            distinct_pages = {id(rep) for rep, _, _, _ in hits}
            if len(distinct_pages) >= 2:
                for rep, r_idx, vis_t, src_t in hits:
                    rep["issues"].append(
                        {
                            "severity": "warning",
                            "category": "running_header_book_title",
                            "region_index": int(r_idx),
                            "message": (
                                f"Region #{r_idx} ({vis_t!r}, raw={src_t!r}) repeats across {len(distinct_pages)} pages in the page margin — "
                                "running book/series title headers must NOT be translated; delete region with `restore_artwork=True`!"
                            ),
                        }
                    )
                    rep["issue_count"] = len(rep["issues"])

    error_count = 0
    warning_count = 0
    info_count = 0
    errors_by_category: dict[str, int] = {}

    for _, rep in all_reports:
        total_issues += rep["issue_count"]
        for iss in rep["issues"]:
            cat = iss["category"]
            sev = str(iss.get("severity") or "warning").lower()
            counts_by_category[cat] = counts_by_category.get(cat, 0) + 1
            if sev == "error":
                error_count += 1
                errors_by_category[cat] = errors_by_category.get(cat, 0) + 1
            elif sev == "info":
                info_count += 1
            else:
                warning_count += 1
        if not only_pages_with_issues or rep["issue_count"] > 0:
            page_reports.append(rep)

    return {
        "audited_pages": len(pages),
        "total_issues": total_issues,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "issues_by_category": counts_by_category,
        "errors_by_category": errors_by_category,
        "pages": page_reports,
    }


def diff_qa_audits(
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    *,
    pages: Optional[set[str]] = None,
    high_conf_ym_threshold: float = 0.85,
) -> dict[str, Any]:
    """Compare two `run_qa_audit` reports to enforce a zero-regression closed-loop gate after Gate B mutations.

    Blocks any newly introduced `error` (especially newly introduced high-confidence `yuzumarker_font_mismatch`
    or `vertical_alignment_mismatch` regressions caused by visual verifier edits).
    """

    def _index_errors(report: dict[str, Any]) -> dict[tuple[str, Any, str], dict[str, Any]]:
        indexed: dict[tuple[str, Any, str], dict[str, Any]] = {}
        for p_rep in report.get("pages") or []:
            pname = str(p_rep.get("page_name") or "")
            if pages is not None and pname not in pages and Path(pname).stem not in pages:
                continue
            for iss in p_rep.get("issues") or []:
                if str(iss.get("severity") or "").lower() != "error":
                    continue
                key = (pname, iss.get("region_index"), str(iss.get("category") or ""))
                if key not in indexed:
                    entry = dict(iss)
                    entry["page_name"] = pname
                    indexed[key] = entry
        return indexed

    before_errs = _index_errors(before_report)
    after_errs = _index_errors(after_report)

    def _key_sort(k: tuple[str, Any, str]) -> tuple[str, int, str]:
        return (k[0], -1 if k[1] is None else int(k[1]), k[2])

    new_errors = [after_errs[k] for k in sorted(after_errs.keys(), key=_key_sort) if k not in before_errs]
    resolved_errors = [before_errs[k] for k in sorted(before_errs.keys(), key=_key_sort) if k not in after_errs]
    new_high_conf_ym = [
        e
        for e in new_errors
        if e.get("category") == "yuzumarker_font_mismatch"
        and float(e.get("pred_conf") or 0.0) >= high_conf_ym_threshold
    ]

    return {
        "before_error_count": len(before_errs),
        "after_error_count": len(after_errs),
        "resolved_error_count": len(resolved_errors),
        "newly_introduced_error_count": len(new_errors),
        "newly_introduced_high_conf_yuzumarker_count": len(new_high_conf_ym),
        "passed": len(new_errors) == 0,
        "newly_introduced_errors": new_errors,
        "newly_introduced_high_conf_yuzumarker_errors": new_high_conf_ym,
    }
