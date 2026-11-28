"""Mask editing, artwork pixel restoration, local inpainting, and paste/clone overlays."""

from __future__ import annotations

import base64
import copy
import logging
import math
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .bubble_centering import split_multilobe_cusp_bisector
from .config import get_runtime_settings
from .path_resolver import PagePaths, resolve_page_paths
from .project_store import (
    _atomic_replace_with_retry,
    decode_mask_base64,
    encode_mask_base64,
    ensure_initial_snapshot,
    ensure_region_uids,
    format_region_summary,
    load_page_json,
    open_rgb_image,
    page_edit_lock,
    region_bbox_xyxy,
    region_center,
    resolve_region_target_index,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from .richtext_builder import (
    normalize_hex_color,
    plain_to_storage_br,
    region_is_bold,
    set_region_whole_bold,
    storage_br_to_plain,
)

logger = logging.getLogger("mtui_mcp.inpaint")


def build_region_polygon_mask(
    region: dict[str, Any],
    shape_hw: tuple[int, int],
    expand_px: int = 0,
) -> np.ndarray:
    """Build a `{0, 255}` uint8 world-space mask for `region['lines']` rotated by `region['angle']` around `center`."""
    h, w = int(shape_hw[0]), int(shape_hw[1])
    out = np.zeros((h, w), dtype=np.uint8)
    if h <= 0 or w <= 0 or not isinstance(region, dict):
        return out
    lines = region.get("lines") or []
    ang = float(region.get("angle", 0.0) or 0.0)
    if not math.isfinite(ang):
        ang = 0.0
    raw_c = region.get("center")
    if isinstance(raw_c, (list, tuple)) and len(raw_c) >= 2:
        cx, cy = float(raw_c[0]), float(raw_c[1])
    else:
        cx, cy = region_center(region)
    rad = math.radians(ang)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    polys: list[np.ndarray] = []
    for poly in lines:
        try:
            arr = np.asarray(poly, dtype=np.float64)
        except Exception:
            continue
        if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] < 2:
            continue
        arr = arr[:, :2]
        if not np.all(np.isfinite(arr)):
            continue
        if abs(ang) > 1e-6:
            dx = arr[:, 0] - cx
            dy = arr[:, 1] - cy
            wx = cx + dx * cos_a - dy * sin_a
            wy = cy + dx * sin_a + dy * cos_a
            world_pts = np.column_stack((wx, wy))
        else:
            world_pts = arr
        pts = np.rint(world_pts).astype(np.int32)
        pts[:, 0] = np.clip(pts[:, 0], 0, max(0, w - 1))
        pts[:, 1] = np.clip(pts[:, 1], 0, max(0, h - 1))
        polys.append(pts)

    if polys:
        cv2.fillPoly(out, polys, 255)
    if expand_px > 0 and np.any(out):
        k = int(expand_px) * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        out = cv2.dilate(out, kernel)
    return out


def detect_inward_bubble_cusps(sub_mask_u8: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """Return `(x, y, in_dx, in_dy, depth_px)` of inward concave cusps/spikes on a `{0, 255}` bubble component mask."""
    clean = cv2.morphologyEx(np.where(sub_mask_u8 > 0, 255, 0).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []
    cnt = max(contours, key=cv2.contourArea)
    pts = cnt[:, 0, :]
    n = len(pts)
    if n < 40:
        return []
    sh, sw = clean.shape
    depths = np.zeros(n, dtype=np.float32)
    bisectors = np.zeros((n, 2), dtype=np.float32)

    for k in (10, 18, 28, 38):
        if n <= 2 * k + 6:
            continue
        p_prev = np.roll(pts, k, axis=0).astype(np.float32)
        p_next = np.roll(pts, -k, axis=0).astype(np.float32)
        p_cur = pts.astype(np.float32)
        mid = 0.5 * (p_prev + p_next)
        va = p_prev - p_cur
        vb = p_next - p_cur
        la = np.hypot(va[:, 0], va[:, 1]) + 1e-6
        lb = np.hypot(vb[:, 0], vb[:, 1]) + 1e-6
        v_sum = (va / la[:, None]) + (vb / lb[:, None])
        v_norm = np.hypot(v_sum[:, 0], v_sum[:, 1])
        chord = p_next - p_prev
        clen = np.hypot(chord[:, 0], chord[:, 1]) + 1e-6
        perp_d = np.abs(chord[:, 0] * (p_prev[:, 1] - p_cur[:, 1]) - (p_prev[:, 0] - p_cur[:, 0]) * chord[:, 1]) / clen
        for i in range(n):
            if perp_d[i] < 3.0 or v_norm[i] < 0.22:
                continue
            out_dir = v_sum[i] / v_norm[i]
            in_dir = -out_dir
            ox, oy = int(round(p_cur[i, 0] + 4.0 * out_dir[0])), int(round(p_cur[i, 1] + 4.0 * out_dir[1]))
            ix, iy = int(round(p_cur[i, 0] + 4.0 * in_dir[0])), int(round(p_cur[i, 1] + 4.0 * in_dir[1]))
            mx, my = int(round(mid[i, 0])), int(round(mid[i, 1]))
            out_black = (not (0 <= ox < sw and 0 <= oy < sh)) or (clean[oy, ox] == 0)
            mid_black = (not (0 <= mx < sw and 0 <= my < sh)) or (clean[my, mx] == 0)
            in_white = (0 <= ix < sw and 0 <= iy < sh) and (clean[iy, ix] > 0)
            if out_black and mid_black and in_white and perp_d[i] > depths[i]:
                depths[i] = perp_d[i]
                bisectors[i] = in_dir

    cusps: list[tuple[float, float, float, float, float]] = []
    win = 14
    for i in range(n):
        if depths[i] < 3.5:
            continue
        idxs = [(i + d) % n for d in range(-win, win + 1)]
        if depths[i] >= np.max(depths[idxs]) and (i == min(j for j in idxs if depths[j] == depths[i])):
            cusps.append((float(pts[i, 0]), float(pts[i, 1]), float(bisectors[i, 0]), float(bisectors[i, 1]), float(depths[i])))
    return cusps


def pair_divided_by_bubble_cusps(
    cusps: list[tuple[float, float, float, float, float]],
    pa: tuple[float, float],
    pb: tuple[float, float],
) -> bool:
    """Return True if `pa` and `pb` lie on opposite sides of inward chamber-dividing contour cusps (`OO` / double-shout)."""
    ax, ay = float(pa[0]), float(pa[1])
    bx, by = float(pb[0]), float(pb[1])
    dx, dy = abs(ax - bx), abs(ay - by)
    if dx >= 45.0:
        x_lo, x_hi = min(ax, bx) + 8.0, max(ax, bx) - 8.0
        top_c = [c for c in cusps if x_lo <= c[0] <= x_hi and c[3] >= 0.35 and c[4] >= 11.0]
        bot_c = [c for c in cusps if x_lo <= c[0] <= x_hi and c[3] <= -0.35 and c[4] >= 11.0]
        if (top_c and bot_c) or any(c[4] >= 20.0 for c in top_c + bot_c):
            return True
    if dy >= 45.0:
        y_lo, y_hi = min(ay, by) + 8.0, max(ay, by) - 8.0
        left_c = [c for c in cusps if y_lo <= c[1] <= y_hi and c[2] >= 0.35 and c[4] >= 11.0]
        right_c = [c for c in cusps if y_lo <= c[1] <= y_hi and c[2] <= -0.35 and c[4] >= 11.0]
        if (left_c and right_c) or any(c[4] >= 20.0 for c in left_c + right_c):
            return True
    return False


def extract_tight_halo_glyph_mask(
    base_rgb: np.ndarray,
    rect_xyxy: list[float],
    halo_dilate_px: int = 3,
) -> np.ndarray:
    """Extract ONLY stroked manga glyphs (`dark core + white halo` or `white core + dark halo`) inside `rect_xyxy`,
    leaving 100% of adjacent facial features (eyes, pupils, hair, screentones, speedlines, borders) untouched!
    """
    h, w = base_rgb.shape[:2]
    full_mask = np.zeros((h, w), dtype=np.uint8)
    x1 = max(0, min(w - 1, int(math.floor(rect_xyxy[0]))))
    y1 = max(0, min(h - 1, int(math.floor(rect_xyxy[1]))))
    x2 = max(x1 + 1, min(w, int(math.ceil(rect_xyxy[2]))))
    y2 = max(y1 + 1, min(h, int(math.ceil(rect_xyxy[3]))))

    pad = 10
    px1, py1 = max(0, x1 - pad), max(0, y1 - pad)
    px2, py2 = min(w, x2 + pad), min(h, y2 + pad)
    gray = cv2.cvtColor(base_rgb[py1:py2, px1:px2], cv2.COLOR_RGB2GRAY)
    ph, pw = gray.shape
    in_x1, in_y1 = x1 - px1, y1 - py1
    in_x2, in_y2 = x2 - px1, y2 - py1
    k_ring = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    for core_bin, halo_bin, grow_halo_cond in (
        ((gray <= 85).astype(np.uint8), (gray >= 232).astype(np.uint8), gray >= 215),
        ((gray >= 238).astype(np.uint8), (gray <= 65).astype(np.uint8), gray <= 85),
    ):
        n_c, c_lbl, c_stats, _ = cv2.connectedComponentsWithStats(core_bin, connectivity=8)
        valid_core = np.zeros_like(core_bin)
        for cid in range(1, n_c):
            cx0, cy0, cw0, ch0, area = c_stats[cid]
            if area < 8:
                continue
            comp = c_lbl == cid
            inside_frac = float(comp[in_y1:in_y2, in_x1:in_x2].sum()) / max(1.0, float(area))
            if inside_frac < 0.70 or (cw0 > (in_x2 - in_x1) * 0.96 and ch0 > (in_y2 - in_y1) * 0.85):
                continue
            rx0, ry0 = max(0, cx0 - 6), max(0, cy0 - 6)
            rx1_c, ry1_c = min(pw, cx0 + cw0 + 6), min(ph, cy0 + ch0 + 6)
            sub_c = comp[ry0:ry1_c, rx0:rx1_c].astype(np.uint8)
            ring = (cv2.dilate(sub_c, k_ring) > 0) & (core_bin[ry0:ry1_c, rx0:rx1_c] == 0)
            if np.any(ring) and float(np.mean(halo_bin[ry0:ry1_c, rx0:rx1_c][ring] > 0)) >= 0.62:
                # Guard against wide-open shocked anime pupils (`small dark dot floating inside wide white sclera enclosed by dark eyelid`)!
                if area < 550 and 0.65 <= (cw0 / max(1.0, float(ch0))) <= 1.55:
                    ex0, ey0 = max(0, cx0 - 36), max(0, cy0 - 24)
                    ex1_s, ey1_s = min(pw, cx0 + cw0 + 36), min(ph, cy0 + ch0 + 24)
                    sclera_sub = halo_bin[ey0:ey1_s, ex0:ex1_s]
                    _, sc_lbl, sc_stats, _ = cv2.connectedComponentsWithStats(sclera_sub, connectivity=8)
                    touch_sc = np.unique(sc_lbl[(cv2.dilate(comp[ey0:ey1_s, ex0:ex1_s].astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & (sc_lbl > 0)])
                    if touch_sc.size > 0:
                        sid = int(touch_sc[0])
                        sc_w = int(sc_stats[sid, cv2.CC_STAT_WIDTH])
                        sc_comp = (sc_lbl == sid).astype(np.uint8)
                        sc_outer = (cv2.dilate(sc_comp, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0) & (sc_comp == 0)
                        # Exclude core_bin interior (`the pupil itself`) from outer eyelid check
                        sc_outer_eyelid = sc_outer & (~comp[ey0:ey1_s, ex0:ex1_s])
                        if sc_w >= cw0 * 2.2 and np.any(sc_outer_eyelid) and float(np.mean(core_bin[ey0:ey1_s, ex0:ex1_s][sc_outer_eyelid] > 0)) >= 0.50:
                            continue
                valid_core[comp] = 1

        if np.any(valid_core):
            near_core = cv2.dilate(valid_core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (23, 23))) > 0
            halo_attached = near_core & grow_halo_cond
            core_dil1 = cv2.dilate(valid_core, np.ones((3, 3), np.uint8)) > 0
            _, h_lbl = cv2.connectedComponents(halo_attached.astype(np.uint8), connectivity=8)
            touched_h = np.unique(h_lbl[core_dil1 & (h_lbl > 0)])
            glyph_and_halo = (valid_core > 0) | np.isin(h_lbl, touched_h)
            out_sub = glyph_and_halo.astype(np.uint8) * 255
            if halo_dilate_px > 0:
                kd = int(halo_dilate_px) * 2 + 1
                out_sub = cv2.dilate(out_sub, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kd, kd)))
            rx1_f = max(0, x1 - halo_dilate_px)
            ry1_f = max(0, y1 - halo_dilate_px)
            rx2_f = min(w, x2 + halo_dilate_px)
            ry2_f = min(h, y2 + halo_dilate_px)
            full_mask[ry1_f:ry2_f, rx1_f:rx2_f] = out_sub[ry1_f - py1 : ry2_f - py1, rx1_f - px1 : rx2_f - px1]
            return full_mask

    return full_mask


def auto_detect_ink_mask_in_rect(
    base_rgb: np.ndarray,
    rect_xyxy: list[float],
    dilate_px: int = 5,
) -> np.ndarray:
    """Automatically segment high-contrast text ink inside a speech-bubble rectangle (or tight halo glyphs on artwork) without wiping borders/faces."""
    h, w = base_rgb.shape[:2]
    full_mask = np.zeros((h, w), dtype=np.uint8)
    x1 = max(0, min(w - 1, int(math.floor(rect_xyxy[0]))))
    y1 = max(0, min(h - 1, int(math.floor(rect_xyxy[1]))))
    x2 = max(x1 + 1, min(w, int(math.ceil(rect_xyxy[2]))))
    y2 = max(y1 + 1, min(h, int(math.ceil(rect_xyxy[3]))))

    crop = base_rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return full_mask

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    # On non-uniform textured artwork (`p25 < 190` and `p85 < 242` or stroked text over art), prefer tight halo extraction first!
    if float(np.percentile(gray, 25)) < 190.0 and float(np.percentile(gray, 75)) < 242.0:
        tight = extract_tight_halo_glyph_mask(base_rgb, rect_xyxy, halo_dilate_px=max(2, min(5, int(dilate_px))))
        if np.any(tight):
            return tight
    bg_median = float(np.median(gray))
    if bg_median >= 128.0:
        # Dark text on light bubble
        diff = np.clip(bg_median - gray.astype(np.float32), 0, 255).astype(np.uint8)
    else:
        # Light text on dark background
        diff = np.clip(gray.astype(np.float32) - bg_median, 0, 255).astype(np.uint8)

    _, local_bin = cv2.threshold(diff, max(35, int(np.percentile(diff, 75))), 255, cv2.THRESH_BINARY)
    # Exclude components that touch the outer crop boundary if they look like a long bubble outline
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(local_bin, connectivity=8)
    ch, cw = local_bin.shape
    cleaned = np.zeros_like(local_bin)
    for lbl in range(1, num_labels):
        lx, ly, lw, lh, area = stats[lbl]
        touches_edge = lx == 0 or ly == 0 or (lx + lw) >= cw or (ly + lh) >= ch
        spans_most_box = lw > cw * 0.85 or lh > ch * 0.85
        if touches_edge and spans_most_box:
            continue
        if area >= 3:
            cleaned[labels == lbl] = 255

    if not np.any(cleaned):
        return full_mask

    if dilate_px > 0:
        k = int(dilate_px) * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        cleaned = cv2.dilate(cleaned, kernel)

    full_mask[y1:y2, x1:x2] = cleaned
    return full_mask


def extract_bubble_border_shield(base_rgb: np.ndarray) -> np.ndarray:
    """Return `{0, 255}` mask of continuous hollow speech-bubble / panel-frame black outlines so
    rectangular/dilated text-mask edits inside a bubble NEVER accidentally wipe out bubble borders (`图1`).
    """
    h, w = base_rgb.shape[:2]
    gray = cv2.cvtColor(base_rgb, cv2.COLOR_RGB2GRAY)
    dark = (gray < 145).astype(np.uint8)
    n_c, c_lbl, c_stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    core_border = np.zeros((h, w), dtype=np.uint8)
    for cid in range(1, n_c):
        _, _, cw0, ch0, area = c_stats[cid]
        # True bubble outlines & panel borders span >=65px on BOTH axes and are hollow frames (`bbox_area >= 4.2 * ink_area`),
        # whereas solid kanji, emphasis dots, furigana, and 1D `――` / `││` dashes (`min(cw0, ch0) < 65`) are never matched!
        if cw0 >= 65 and ch0 >= 65 and area >= 180 and (float(cw0) * float(ch0)) >= 4.2 * float(area):
            core_border[c_lbl == cid] = 255
    if not np.any(core_border):
        return core_border
    # Include 1px anti-aliased gray fringe (`gray < 215`) attached to the outline so border anti-aliasing stays crisp
    fringe = (cv2.dilate(core_border, np.ones((3, 3), dtype=np.uint8)) > 0) & (gray < 215)
    return np.where(fringe, 255, 0).astype(np.uint8)


def apply_mask_operations(
    initial_mask: Optional[np.ndarray],
    base_rgb: np.ndarray,
    regions: list[dict[str, Any]],
    *,
    add_rects: Optional[list[list[float]]] = None,
    add_polygons: Optional[list[list[list[float]]]] = None,
    add_brush_strokes: Optional[list[dict[str, Any]]] = None,
    dilate_regions: Optional[list[dict[str, Any]]] = None,
    auto_detect_ink_rects: Optional[list[list[float]]] = None,
    tight_halo_rects: Optional[list[list[float]]] = None,
    erase_rects: Optional[list[list[float]]] = None,
    erase_polygons: Optional[list[list[list[float]]]] = None,
    erase_brush_strokes: Optional[list[dict[str, Any]]] = None,
    clear_region_indices: Optional[list[int]] = None,
    protect_bubble_borders: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply mask additions and erasures, returning `(new_mask, added_delta, removed_delta)`."""
    h, w = base_rgb.shape[:2]
    prev_mask = (
        initial_mask.copy()
        if (initial_mask is not None and initial_mask.shape == (h, w))
        else np.zeros((h, w), dtype=np.uint8)
    )
    prev_mask = np.where(prev_mask > 0, 255, 0).astype(np.uint8)
    cur = prev_mask.copy()
    explicit_add = np.zeros((h, w), dtype=np.uint8)
    border_shield = (
        extract_bubble_border_shield(base_rgb)
        if (protect_bubble_borders and (add_rects or dilate_regions or auto_detect_ink_rects))
        else None
    )

    # 1. Additions
    for rect in add_rects or []:
        if len(rect) >= 4:
            x1 = max(0, min(w - 1, int(round(rect[0]))))
            y1 = max(0, min(h - 1, int(round(rect[1]))))
            x2 = max(0, min(w, int(round(rect[2]))))
            y2 = max(0, min(h, int(round(rect[3]))))
            if x2 > x1 and y2 > y1:
                rect_m = np.zeros((h, w), dtype=np.uint8)
                rect_m[y1:y2, x1:x2] = 255
                if border_shield is not None:
                    rect_m[border_shield > 0] = 0
                cur = cv2.bitwise_or(cur, rect_m)
                explicit_add = cv2.bitwise_or(explicit_add, rect_m)

    for poly in add_polygons or []:
        pts = np.rint(np.asarray(poly, dtype=np.float32)).astype(np.int32)
        if pts.ndim == 2 and pts.shape[0] >= 3:
            cv2.fillPoly(cur, [pts[:, :2]], 255)
            cv2.fillPoly(explicit_add, [pts[:, :2]], 255)

    for stroke in add_brush_strokes or []:
        pts = stroke.get("points") or []
        radius = max(1, int(round(float(stroke.get("radius", 12)))))
        if len(pts) == 1:
            px, py = int(round(pts[0][0])), int(round(pts[0][1]))
            cv2.circle(cur, (px, py), radius, 255, -1)
            cv2.circle(explicit_add, (px, py), radius, 255, -1)
        elif len(pts) >= 2:
            for a, b in zip(pts[:-1], pts[1:]):
                p1 = (int(round(a[0])), int(round(a[1])))
                p2 = (int(round(b[0])), int(round(b[1])))
                cv2.line(cur, p1, p2, 255, thickness=radius * 2)
                cv2.line(explicit_add, p1, p2, 255, thickness=radius * 2)

    for d_spec in dilate_regions or []:
        r_idx = int(d_spec["region_index"])
        px = max(1, int(round(float(d_spec.get("pixels", 8)))))
        if 0 <= r_idx < len(regions):
            rm = build_region_polygon_mask(regions[r_idx], (h, w), expand_px=px)
            # Also dilate existing mask components overlapping this region
            existing_in_reg = cv2.bitwise_and(cur, rm)
            if np.any(existing_in_reg):
                k = px * 2 + 1
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
                dil_part = cv2.dilate(existing_in_reg, kernel)
                if border_shield is not None:
                    dil_part[border_shield > 0] = 0
                cur = cv2.bitwise_or(cur, dil_part)
                explicit_add = cv2.bitwise_or(explicit_add, dil_part)
            else:
                if border_shield is not None:
                    rm[border_shield > 0] = 0
                cur = cv2.bitwise_or(cur, rm)
                explicit_add = cv2.bitwise_or(explicit_add, rm)

    for ink_rect in auto_detect_ink_rects or []:
        if len(ink_rect) >= 4:
            ink_m = auto_detect_ink_mask_in_rect(base_rgb, ink_rect, dilate_px=5)
            if border_shield is not None:
                ink_m[border_shield > 0] = 0
            cur = cv2.bitwise_or(cur, ink_m)
            explicit_add = cv2.bitwise_or(explicit_add, ink_m)

    # 1b. Surgical Frameless Tight-Halo Replacement (`图5` eye-safe repair: clears bloated mask in rect -> masks ONLY glyph+halo!)
    for th_rect in tight_halo_rects or []:
        if len(th_rect) >= 4:
            tx1 = max(0, min(w - 1, int(round(th_rect[0]))))
            ty1 = max(0, min(h - 1, int(round(th_rect[1]))))
            tx2 = max(0, min(w, int(round(th_rect[2]))))
            ty2 = max(0, min(h, int(round(th_rect[3]))))
            if tx2 > tx1 and ty2 > ty1:
                # First un-mask everything inside `th_rect` so accidentally wiped eyes/hair/borders revert to Raw!
                cur[ty1:ty2, tx1:tx2] = 0
                th_mask = extract_tight_halo_glyph_mask(base_rgb, [tx1, ty1, tx2, ty2], halo_dilate_px=3)
                cur = cv2.bitwise_or(cur, th_mask)
                explicit_add = cv2.bitwise_or(explicit_add, th_mask)

    # 2. Erasures (un-masking to restore original artwork!)
    for r_idx in clear_region_indices or []:
        if 0 <= int(r_idx) < len(regions):
            rm = build_region_polygon_mask(regions[int(r_idx)], (h, w), expand_px=10)
            cur = cv2.bitwise_and(cur, cv2.bitwise_not(rm))

    for rect in erase_rects or []:
        if len(rect) >= 4:
            x1 = max(0, min(w - 1, int(round(rect[0]))))
            y1 = max(0, min(h - 1, int(round(rect[1]))))
            x2 = max(0, min(w, int(round(rect[2]))))
            y2 = max(0, min(h, int(round(rect[3]))))
            if x2 > x1 and y2 > y1:
                cur[y1:y2, x1:x2] = 0

    for poly in erase_polygons or []:
        pts = np.rint(np.asarray(poly, dtype=np.float32)).astype(np.int32)
        if pts.ndim == 2 and pts.shape[0] >= 3:
            cv2.fillPoly(cur, [pts[:, :2]], 0)

    for stroke in erase_brush_strokes or []:
        pts = stroke.get("points") or []
        radius = max(1, int(round(float(stroke.get("radius", 12)))))
        if len(pts) == 1:
            px, py = int(round(pts[0][0])), int(round(pts[0][1]))
            cv2.circle(cur, (px, py), radius, 0, -1)
        elif len(pts) >= 2:
            for a, b in zip(pts[:-1], pts[1:]):
                p1 = (int(round(a[0])), int(round(a[1])))
                p2 = (int(round(b[0])), int(round(b[1])))
                cv2.line(cur, p1, p2, 0, thickness=radius * 2)

    cur = np.where(cur > 0, 255, 0).astype(np.uint8)
    added = np.where((cur > 0) & ((prev_mask == 0) | (explicit_add > 0)), 255, 0).astype(np.uint8)
    removed = np.where((prev_mask > 0) & (cur == 0), 255, 0).astype(np.uint8)
    return cur, added, removed


def _merge_overlapping_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Merge overlapping or touching `[x1, y1, x2, y2]` ROI boxes so blur windows never double-blend."""
    if not boxes:
        return []
    result = [list(b) for b in boxes]
    merged_any = True
    while merged_any:
        merged_any = False
        new_result: list[list[int]] = []
        while result:
            curr = result.pop(0)
            merged = False
            for i, other in enumerate(result):
                if not (curr[2] < other[0] or other[2] < curr[0] or curr[3] < other[1] or other[3] < curr[1]):
                    result[i] = [
                        min(curr[0], other[0]),
                        min(curr[1], other[1]),
                        max(curr[2], other[2]),
                        max(curr[3], other[3]),
                    ]
                    merged = True
                    merged_any = True
                    break
            if not merged:
                new_result.append(curr)
        result = new_result
    return [(b[0], b[1], b[2], b[3]) for b in result]


def _apply_frosted_glass_blur(
    image_rgb: np.ndarray,
    binary_mask_u8: np.ndarray,
    blur_radius: int = 0,
) -> np.ndarray:
    """Frosted-glass Gaussian blur matching `manga_translator/inpainting/blur.py` (`BlurInpainter.apply_blur`)."""
    out = image_rgb.copy()
    if not np.any(binary_mask_u8 > 0):
        return out

    h, w = image_rgb.shape[:2]
    clamped_radius = max(0, min(200, int(blur_radius or 0)))
    if clamped_radius > 0:
        ksize = max(3, min(200, clamped_radius))
    else:
        ksize = max(21, int(min(h, w) * 0.025))
    if ksize % 2 == 0:
        ksize += 1

    dilate_k = max(3, int(min(h, w) * 0.003)) | 1
    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_k, dilate_k))
    mask_dilated = cv2.dilate(binary_mask_u8, kernel_dilate, iterations=1)
    feather_k = max(7, (ksize // 3) | 1)
    feather_r = max(3, feather_k // 2)

    contours, _ = cv2.findContours(mask_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return out

    pad = max(10, ksize + feather_r + 2)
    raw_boxes: list[tuple[int, int, int, int]] = []
    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        raw_boxes.append(
            (
                max(0, bx - pad),
                max(0, by - pad),
                min(w, bx + bw + pad),
                min(h, by + bh + pad),
            )
        )

    for x1, y1, x2, y2 in _merge_overlapping_boxes(raw_boxes):
        roi_mask = mask_dilated[y1:y2, x1:x2]
        if not np.any(roi_mask > 0):
            continue
        roi_img = image_rgb[y1:y2, x1:x2]
        roi_telea = cv2.inpaint(roi_img, roi_mask, 3, cv2.INPAINT_TELEA)
        roi_blurred = cv2.GaussianBlur(roi_telea, (ksize, ksize), 0)
        dist_outside = cv2.distanceTransform(255 - roi_mask, cv2.DIST_L2, 5)
        roi_alpha = (
            0.5 * (1.0 + np.cos(np.pi * np.clip(dist_outside / float(feather_r), 0.0, 1.0)))
        ).astype(np.float32)
        if roi_img.ndim == 3:
            roi_alpha = roi_alpha[:, :, None]
        out[y1:y2, x1:x2] = np.clip(
            roi_img.astype(np.float32) * (1.0 - roi_alpha) + roi_blurred.astype(np.float32) * roi_alpha,
            0,
            255,
        ).astype(np.uint8)

    return out


def local_inpaint_crop(
    image_rgb: np.ndarray,
    mask_u8: np.ndarray,
    method: str = "telea",  # "solid_fill" | "telea" | "ns" | "blur"
    *,
    blur_radius: int = 0,
) -> np.ndarray:
    """Fast local OpenCV / solid-fill / frosted-blur repair on an RGB image/crop and `{0, 255}` mask."""
    out = image_rgb.copy()
    binary = np.where(mask_u8 > 0, 255, 0).astype(np.uint8)
    if not np.any(binary):
        return out

    if method == "blur":
        return _apply_frosted_glass_blur(image_rgb, binary, blur_radius=blur_radius)

    if method == "solid_fill":
        # Per-component ring sampling: snap to pure white paper background when ring is bright so nearby hair/lines never turn white background into a gray rectangle!
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        n_comp, comp_lbl = cv2.connectedComponents(binary, connectivity=8)
        for cid in range(1, n_comp):
            cmask = comp_lbl == cid
            dilated = cv2.dilate(cmask.astype(np.uint8), k, iterations=1) > 0
            ring = dilated & (~binary.astype(bool))
            if np.any(ring):
                ring_px = image_rgb[ring]
                ring_luma = np.mean(ring_px, axis=1)
                if float(np.percentile(ring_luma, 75)) >= 242.0:
                    bright_ring = ring_px[ring_luma >= 240.0]
                    fill_rgb = np.median(bright_ring, axis=0).astype(np.uint8) if len(bright_ring) > 0 else np.array([255, 255, 255], dtype=np.uint8)
                else:
                    fill_rgb = np.median(ring_px, axis=0).astype(np.uint8)
            else:
                fill_rgb = np.array([255, 255, 255], dtype=np.uint8)
            out[cmask] = fill_rgb
        return out

    bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    repaired_bgr = cv2.inpaint(bgr, binary, inpaintRadius=5, flags=flag)
    return cv2.cvtColor(repaired_bgr, cv2.COLOR_BGR2RGB)


def apply_incremental_local_repair(
    base_rgb: np.ndarray,
    previous_inpainted_rgb: Optional[np.ndarray],
    new_mask: np.ndarray,
    added_delta: np.ndarray,
    removed_delta: np.ndarray,
    method: str = "telea",
    padding: int = 48,
    *,
    blur_radius: int = 0,
) -> np.ndarray:
    """Incremental repair matching `EditorControllerInpaintService.async_inpaint`."""
    h, w = base_rgb.shape[:2]
    if method == "blur":
        # Upstream bypasses small-crop incremental mode for `blur` so adaptive kernel size and large blur radii
        # (`pad = ksize + feather_r + 2` up to 270px) never clip at a 48px crop seam.
        result = local_inpaint_crop(base_rgb, new_mask, method="blur", blur_radius=blur_radius)
        if np.any(removed_delta):
            erased_unmasked = (removed_delta > 0) & (new_mask == 0)
            result[erased_unmasked] = base_rgb[erased_unmasked]
        return result

    if previous_inpainted_rgb is None or previous_inpainted_rgb.shape[:2] != (h, w):
        return local_inpaint_crop(base_rgb, new_mask, method=method, blur_radius=blur_radius)

    result = previous_inpainted_rgb.copy()
    if np.any(removed_delta):
        unmasked = new_mask == 0
        result[unmasked] = base_rgb[unmasked]

    if np.any(added_delta):
        ys, xs = np.where(added_delta > 0)
        y1 = max(0, int(np.min(ys)) - padding)
        y2 = min(h, int(np.max(ys)) + padding + 1)
        x1 = max(0, int(np.min(xs)) - padding)
        x2 = min(w, int(np.max(xs)) + padding + 1)
        repaired_patch = local_inpaint_crop(
            result[y1:y2, x1:x2],
            added_delta[y1:y2, x1:x2],
            method=method,
        )
        result[y1:y2, x1:x2] = repaired_patch

    if np.any(removed_delta):
        unmasked = new_mask == 0
        result[unmasked] = base_rgb[unmasked]

    return result


# ---------------------------------------------------------------------------
# Region Structural Operations (Add / Delete / Merge)
# ---------------------------------------------------------------------------


def _commit_inpainted_and_json_atomic(
    paths: PagePaths,
    image_key: str,
    image_data: dict[str, Any],
    new_inpainted_rgb: Optional[np.ndarray],
) -> Path:
    """Capture `initial_before_mcp` BEFORE touching `inpainted_path`, and roll back `inpainted_path` if JSON save fails."""
    if get_runtime_settings().auto_backup_on_first_edit:
        ensure_initial_snapshot(paths)

    if new_inpainted_rgb is None:
        return save_page_json_atomic(paths, image_key, image_data, auto_initial_snapshot=False)

    had_prev_inpainted = paths.inpainted_exists and paths.inpainted_path.is_file()
    backup_tmp_path: Optional[Path] = None
    wrote_inpainted = False
    preserve_backup_on_error = False
    try:
        if had_prev_inpainted:
            paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_bak = tempfile.mkstemp(
                prefix=f".rollback_{paths.inpainted_path.name}.",
                suffix=paths.inpainted_path.suffix or ".png",
                dir=str(paths.inpainted_path.parent),
            )
            os.close(fd)
            backup_tmp_path = Path(tmp_bak)
            shutil.copy2(paths.inpainted_path, backup_tmp_path)

        save_rgb_image_atomic(new_inpainted_rgb, paths.inpainted_path)
        wrote_inpainted = True
        return save_page_json_atomic(paths, image_key, image_data, auto_initial_snapshot=False)
    except BaseException as orig_exc:
        if wrote_inpainted:
            try:
                if had_prev_inpainted and backup_tmp_path is not None and backup_tmp_path.is_file():
                    _atomic_replace_with_retry(backup_tmp_path, paths.inpainted_path)
                    backup_tmp_path = None
                elif not had_prev_inpainted and paths.inpainted_path.exists():
                    paths.inpainted_path.unlink()
            except OSError as rb_exc:
                if had_prev_inpainted and backup_tmp_path is not None and backup_tmp_path.exists():
                    preserve_backup_on_error = True
                    recovery_msg = (
                        f"Failed to roll back {paths.inpainted_path} ({rb_exc}); "
                        f"pre-edit inpainted backup preserved at {backup_tmp_path}"
                    )
                    logger.error("%s", recovery_msg)
                    if isinstance(orig_exc, OSError):
                        raise OSError(orig_exc.errno, f"{orig_exc} [{recovery_msg}]") from orig_exc
                    if isinstance(orig_exc, Exception):
                        raise RuntimeError(f"{orig_exc} [{recovery_msg}]") from orig_exc
                else:
                    logger.warning("Failed to roll back %s after JSON save error: %s", paths.inpainted_path, rb_exc)
        raise
    finally:
        if not preserve_backup_on_error and backup_tmp_path is not None and backup_tmp_path.exists():
            try:
                backup_tmp_path.unlink()
            except OSError:
                pass


def add_region_to_page(
    page_path: str | Path,
    *,
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
    direction: str = "auto",
    alignment: str = "auto",
    angle: float = 0.0,
    auto_mask_ink: bool = True,
    local_inpaint_method: Optional[str] = "solid_fill",
) -> tuple[PagePaths, dict[str, Any]]:
    """Add a new text region to a page, optionally auto-masking and repairing the underlying ink."""
    if polygon is not None and len(polygon) >= 4:
        pts = [[float(p[0]), float(p[1])] for p in polygon[:4]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rect = [min(xs), min(ys), max(xs), max(ys)]
    elif bbox_xyxy is not None and len(bbox_xyxy) >= 4:
        x1, y1, x2, y2 = (float(v) for v in bbox_xyxy[:4])
        pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        rect = [x1, y1, x2, y2]
    else:
        raise ValueError("Either bbox_xyxy=[x1, y1, x2, y2] or a 4-point polygon is required")

    with page_edit_lock(page_path):
        paths = resolve_page_paths(page_path)
        image_key, image_data = load_page_json(paths)
        regions = image_data.setdefault("regions", [])

        w_box = max(1.0, rect[2] - rect[0])
        h_box = max(1.0, rect[3] - rect[1])
        eff_font_size = (
            max(12, min(72, int(round(min(w_box, h_box) * 0.45))))
            if font_size is None
            else int(font_size)
        )

        d_norm = {"h": "horizontal", "v": "vertical"}.get(direction.lower(), direction.lower())
        fg_hex = normalize_hex_color(font_color) or "#000000"
        bg_hex = normalize_hex_color(stroke_color) or "#ffffff"
        bg_rgb = [int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)]
        storage_trans = plain_to_storage_br(translation)

        mtui_cfg: dict[str, Any] = {}
        try:
            mtui_cfg = get_runtime_settings().load_mtui_config_dict()
        except Exception:
            pass
        target_lang = (mtui_cfg.get("translator") or {}).get("target_lang", "CHS")
        default_font = font_family or (mtui_cfg.get("render") or {}).get("font_family", "")
        eff_align = (alignment or "").strip().lower()
        if not eff_align or eff_align == "auto":
            cfg_align = str((mtui_cfg.get("render") or {}).get("alignment") or "").strip().lower()
            eff_align = cfg_align if cfg_align in ("left", "center", "right", "auto") else "auto"

        new_reg: dict[str, Any] = {
            "lines": [pts],
            "texts": [source_text],
            "text": source_text,
            "translation": storage_trans,
            "translation_raw": storage_trans,
            "font_size": int(eff_font_size),
            "font_family": default_font,
            "font_color": fg_hex,
            "bg_colors": bg_rgb,
            "stroke_width": float(stroke_width),
            "default_stroke_width": float(stroke_width),
            "direction": d_norm,
            "alignment": eff_align,
            "line_spacing": 1.0,
            "letter_spacing": 1.0,
            "angle": float(angle),
            "center": [(rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0],
            "target_lang": target_lang,
            "language": "unknown",
            "prob": 1.0,
        }
        if bold:
            set_region_whole_bold(new_reg, True)
        regions.append(new_reg)
        ensure_region_uids(regions)
        new_idx = len(regions) - 1
        new_uid = str(new_reg.get("region_uid") or "")

        repaired_rgb: Optional[np.ndarray] = None
        if auto_mask_ink and paths.display_base_path.is_file():
            base_rgb = open_rgb_image(paths.display_base_path)
            prev_mask = decode_mask_base64(image_data.get("mask_raw"), base_rgb.shape[:2])
            new_mask, added, removed = apply_mask_operations(
                prev_mask,
                base_rgb,
                regions,
                auto_detect_ink_rects=[rect],
            )
            image_data["mask_raw"] = encode_mask_base64(new_mask)
            image_data["mask_is_refined"] = True

            if local_inpaint_method in ("solid_fill", "telea", "ns", "blur"):
                prev_inpaint = open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else None
                repaired_rgb = apply_incremental_local_repair(
                    base_rgb,
                    prev_inpaint,
                    new_mask,
                    added,
                    removed,
                    method=local_inpaint_method,
                )

        saved_json = _commit_inpainted_and_json_atomic(paths, image_key, image_data, repaired_rgb)
        refreshed = resolve_page_paths(saved_json)
        _, saved_data = load_page_json(refreshed)
        saved_regions = saved_data.get("regions", [])
        try:
            s_idx = resolve_region_target_index(saved_regions, region_uid=new_uid) if new_uid else new_idx
            summary = format_region_summary(s_idx, saved_regions[s_idx])
        except (KeyError, IndexError):
            summary = format_region_summary(new_idx, new_reg)
        return refreshed, summary


def delete_regions_from_page(
    page_path: str | Path,
    region_indices: list[int],
    *,
    restore_artwork: bool = True,
    expand_clear_px: int = 12,
) -> tuple[PagePaths, dict[str, Any]]:
    """Delete region(s) by index and optionally clear their mask & restore original artwork pixels in `inpainted`."""
    with page_edit_lock(page_path):
        paths = resolve_page_paths(page_path)
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])

        to_remove = sorted({int(i) for i in region_indices if 0 <= int(i) < len(regions)}, reverse=True)
        if not to_remove:
            raise ValueError("No valid region_indices to delete")

        to_remove_set = set(to_remove)
        removed_regions = [copy.deepcopy(regions[i]) for i in to_remove]
        surviving_regions = [regions[i] for i in range(len(regions)) if i not in to_remove_set]

        repaired_inpainted: Optional[np.ndarray] = None
        actually_restored = False
        mask_cleared = False

        if restore_artwork and paths.display_base_path.is_file():
            base_rgb = open_rgb_image(paths.display_base_path)
            h, w = base_rgb.shape[:2]
            prev_mask = decode_mask_base64(image_data.get("mask_raw"), (h, w))
            if prev_mask is not None:
                prot_expand = max(2, max(0, int(expand_clear_px)) // 2)
                protected_mask = np.zeros((h, w), dtype=np.uint8)
                for s_reg in surviving_regions:
                    if isinstance(s_reg, dict):
                        s_rm = build_region_polygon_mask(s_reg, (h, w), expand_px=prot_expand)
                        protected_mask = cv2.bitwise_or(protected_mask, s_rm)

                num_lbl, labels = cv2.connectedComponents((prev_mask > 0).astype(np.uint8), connectivity=8)
                protected_labels = (
                    set(np.unique(labels[(protected_mask > 0) & (labels > 0)]).tolist())
                    if num_lbl > 1
                    else set()
                )

                clear_union = np.zeros((h, w), dtype=np.uint8)
                for reg in removed_regions:
                    rm = build_region_polygon_mask(reg, (h, w), expand_px=max(0, int(expand_clear_px)))
                    rm_safe = cv2.bitwise_and(rm, cv2.bitwise_not(protected_mask))
                    clear_union = cv2.bitwise_or(clear_union, rm_safe)
                    # Expand to whole connected mask components ONLY when not shared with surviving regions
                    if num_lbl > 1:
                        touched = [
                            int(lbl)
                            for lbl in np.unique(labels[rm > 0])
                            if int(lbl) != 0 and int(lbl) not in protected_labels
                        ]
                        if touched:
                            comp_mask = np.where(np.isin(labels, touched), 255, 0).astype(np.uint8)
                            comp_safe = cv2.bitwise_and(comp_mask, cv2.bitwise_not(protected_mask))
                            clear_union = cv2.bitwise_or(clear_union, comp_safe)

                new_mask = cv2.bitwise_and(prev_mask, cv2.bitwise_not(clear_union))
                restored_pixels = (prev_mask > 0) & (new_mask == 0)
                mask_cleared = bool(np.any(restored_pixels))
                image_data["mask_raw"] = encode_mask_base64(new_mask)
                image_data["mask_is_refined"] = True

                if paths.inpainted_exists and paths.inpainted_path.is_file() and mask_cleared:
                    inpainted_rgb = open_rgb_image(paths.inpainted_path)
                    if inpainted_rgb.shape[:2] == (h, w):
                        inpainted_rgb[restored_pixels] = base_rgb[restored_pixels]
                        repaired_inpainted = inpainted_rgb
                        actually_restored = True

        for idx in to_remove:
            regions.pop(idx)

        saved_json = _commit_inpainted_and_json_atomic(paths, image_key, image_data, repaired_inpainted)
        refreshed = resolve_page_paths(saved_json)
        return refreshed, {
            "deleted_indices": sorted(to_remove),
            "remaining_region_count": len(regions),
            "artwork_restored": actually_restored,
            "mask_cleared": mask_cleared,
        }


def merge_regions_on_page(
    page_path: str | Path,
    region_indices: list[int],
    *,
    merged_translation: Optional[str] = None,
    force_merge: bool = False,
) -> tuple[PagePaths, dict[str, Any]]:
    """Merge 2+ split regions into one combined region (refusing to merge across connected multi-lobe cusps unless `force_merge=True`)."""
    with page_edit_lock(page_path):
        paths = resolve_page_paths(page_path)
        image_key, image_data = load_page_json(paths)
        regions = image_data.get("regions", [])

        ordered = sorted({int(i) for i in region_indices if 0 <= int(i) < len(regions)})
        if len(ordered) < 2:
            raise ValueError("merge_regions requires at least 2 valid region_indices")

        # Safety Guard (`图2 & 图3`): Refuse to merge separate chambers of a connected multi-lobe bubble divided by inward border cusps!
        if not force_merge and paths.inpainted_exists:
            inp_gray = cv2.cvtColor(open_rgb_image(paths.inpainted_path), cv2.COLOR_RGB2GRAY)
            ih, iw = inp_gray.shape[:2]
            bright = cv2.erode((inp_gray >= 236).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
            _, cc_lbls, cc_stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=4)
            for idx_i_pos in range(len(ordered)):
                for idx_j_pos in range(idx_i_pos + 1, len(ordered)):
                    ia, ib = ordered[idx_i_pos], ordered[idx_j_pos]
                    ca, cb = region_center(regions[ia]), region_center(regions[ib])
                    box_a, box_b = region_bbox_xyxy(regions[ia]), region_bbox_xyxy(regions[ib])
                    wa, ha = max(1.0, box_a[2] - box_a[0]), max(1.0, box_a[3] - box_a[1])
                    wb, hb = max(1.0, box_b[2] - box_b[0]), max(1.0, box_b[3] - box_b[1])
                    v_ov = max(0.0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1])) / min(ha, hb)
                    h_ov = max(0.0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])) / min(wa, wb)
                    ixa = max(0, min(iw - 1, int(round(ca[0]))))
                    iya = max(0, min(ih - 1, int(round(ca[1]))))
                    ixb = max(0, min(iw - 1, int(round(cb[0]))))
                    iyb = max(0, min(ih - 1, int(round(cb[1]))))
                    lba, lbb = int(cc_lbls[iya, ixa]), int(cc_lbls[iyb, ixb])
                    if lba > 0 and lba == lbb:
                        x0, y0, bw0, bh0, area0 = cc_stats[lba]
                        edges = (
                            int(x0 <= 2)
                            + int(y0 <= 2)
                            + int(x0 + bw0 >= iw - 2)
                            + int(y0 + bh0 >= ih - 2)
                        )
                        if area0 <= 0.48 * iw * ih and edges < 3:
                            pad_c = 6
                            comp_u8 = (cc_lbls[y0 : y0 + bh0, x0 : x0 + bw0] == lba).astype(np.uint8)
                            sub_u8 = np.pad(comp_u8 * 255, pad_c, constant_values=0)
                            loc_a = (ca[0] - x0 + pad_c, ca[1] - y0 + pad_c)
                            loc_b = (cb[0] - x0 + pad_c, cb[1] - y0 + pad_c)
                            cusps = detect_inward_bubble_cusps(sub_u8)
                            divided = pair_divided_by_bubble_cusps(cusps, loc_a, loc_b)
                            if (
                                not divided
                                and cusps
                                and v_ov < 0.55
                                and h_ov < 0.40
                                and abs(ca[1] - cb[1]) >= 48.0
                            ):
                                split_m = split_multilobe_cusp_bisector((sub_u8 > 0).astype(np.uint8), [loc_a, loc_b])
                                _, sp_lbl = cv2.connectedComponents(split_m, connectivity=4)
                                sh_s, sw_s = sp_lbl.shape
                                sa = int(
                                    sp_lbl[
                                        max(0, min(sh_s - 1, int(round(loc_a[1])))),
                                        max(0, min(sw_s - 1, int(round(loc_a[0])))),
                                    ]
                                )
                                sb = int(
                                    sp_lbl[
                                        max(0, min(sh_s - 1, int(round(loc_b[1])))),
                                        max(0, min(sw_s - 1, int(round(loc_b[0])))),
                                    ]
                                )
                                divided = sa > 0 and sb > 0 and sa != sb
                            if divided:
                                raise ValueError(
                                    f"Refusing to merge Regions #{ia} and #{ib}: OpenCV contour analysis detected inward chamber-dividing "
                                    "border cusps/spikes between them (2-chamber connected bubble)! Keep one region per chamber!"
                                )

        primary = copy.deepcopy(regions[ordered[0]])
        was_bold = region_is_bold(primary)
        world_polys: list[list[list[float]]] = []
        combined_texts: list[str] = []
        combined_trans: list[str] = []
        angles: list[float] = []

        for idx in ordered:
            r = regions[idx]
            ang = float(r.get("angle", 0.0) or 0.0)
            angles.append(ang)
            rcx, rcy = region_center(r)
            rad = math.radians(ang)
            ca, sa = math.cos(rad), math.sin(rad)
            for poly in r.get("lines") or []:
                w_poly: list[list[float]] = []
                for pt in poly:
                    dx, dy = float(pt[0]) - rcx, float(pt[1]) - rcy
                    # Re-project stored unrotated lines back to world coordinates (+angle)
                    wx = rcx + dx * ca - dy * sa
                    wy = rcy + dx * sa + dy * ca
                    w_poly.append([wx, wy])
                if w_poly:
                    world_polys.append(w_poly)
            if r.get("texts"):
                combined_texts.extend(str(t) for t in r["texts"])
            elif r.get("text"):
                combined_texts.append(str(r["text"]))
            vis = storage_br_to_plain(str(r.get("translation", "") or "")).strip()
            if vis:
                combined_trans.append(vis)

        # Preserve intentional common tilt when all merged lines slant in the same direction (>= 2 deg)
        if angles and (all(a >= 2.0 for a in angles) or all(a <= -2.0 for a in angles)):
            merged_angle = round(sum(angles) / len(angles), 2)
        else:
            merged_angle = 0.0

        all_pts = np.array([pt for poly in world_polys for pt in poly], dtype=np.float32)
        if len(all_pts) >= 3:
            (mcx, mcy), _, _ = cv2.minAreaRect(all_pts)
            new_cx, new_cy = float(mcx), float(mcy)
        elif len(all_pts) > 0:
            new_cx, new_cy = float(np.mean(all_pts[:, 0])), float(np.mean(all_pts[:, 1]))
        else:
            bbox0 = region_bbox_xyxy(primary)
            new_cx, new_cy = (bbox0[0] + bbox0[2]) / 2.0, (bbox0[1] + bbox0[3]) / 2.0

        # Store `lines` unrotated (-merged_angle) around `new_center`, matching TextBlock.to_dict() convention
        urad = -math.radians(merged_angle)
        uca, usa = math.cos(urad), math.sin(urad)
        combined_lines = [
            [
                [
                    new_cx + (pt[0] - new_cx) * uca - (pt[1] - new_cy) * usa,
                    new_cy + (pt[0] - new_cx) * usa + (pt[1] - new_cy) * uca,
                ]
                for pt in poly
            ]
            for poly in world_polys
        ]

        primary["lines"] = combined_lines
        primary["angle"] = merged_angle
        primary["center"] = [new_cx, new_cy]
        primary["texts"] = combined_texts
        primary["text"] = "".join(combined_texts)
        final_trans = merged_translation if merged_translation is not None else "\n".join(combined_trans)
        primary["translation"] = plain_to_storage_br(final_trans)
        primary["translation_raw"] = primary["translation"]
        primary.pop("translation_rich", None)
        if was_bold:
            set_region_whole_bold(primary, True)
        else:
            primary.pop("bold", None)
        primary.pop("white_frame_rect_local", None)
        primary.pop("white_frame_quad_local", None)
        primary.pop("render_box_rect_local", None)
        primary.pop("render_box_quad_local", None)
        primary["has_custom_white_frame"] = False

        regions[ordered[0]] = primary
        for idx in reversed(ordered[1:]):
            regions.pop(idx)
        ensure_region_uids(regions)
        merged_uid = str(primary.get("region_uid") or "")

        saved_json = save_page_json_atomic(paths, image_key, image_data)
        refreshed = resolve_page_paths(saved_json)
        _, saved_data = load_page_json(refreshed)
        saved_regions = saved_data.get("regions", [])
        try:
            s_idx = resolve_region_target_index(saved_regions, region_uid=merged_uid) if merged_uid else ordered[0]
            summary = format_region_summary(s_idx, saved_regions[s_idx])
        except (KeyError, IndexError):
            summary = format_region_summary(ordered[0], primary)
        return refreshed, summary


# ---------------------------------------------------------------------------
# Paste Overlays & Clone-Patch Helper
# ---------------------------------------------------------------------------


def manage_page_paste_overlays(
    page_path: str | Path,
    action: str,  # "list" | "add_clone_patch" | "add_image_file" | "remove" | "clear"
    *,
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
) -> tuple[PagePaths, dict[str, Any]]:
    """Manage `paste_overlays` (compatible with `desktop_qt_ui/editor/paste_overlay_state.py`), including clone-stamp patches!"""
    if action == "list":
        paths = resolve_page_paths(page_path)
        _, image_data = load_page_json(paths)
        overlays = list(image_data.get("paste_overlays") or [])
        return paths, {
            "count": len(overlays),
            "overlays": [
                {k: v for k, v in item.items() if k != "image"}
                for item in overlays
                if isinstance(item, dict)
            ],
        }

    with page_edit_lock(page_path):
        paths = resolve_page_paths(page_path)
        image_key, image_data = load_page_json(paths)
        overlays = list(image_data.get("paste_overlays") or [])

        if action == "clear":
            image_data.pop("paste_overlays", None)
            saved = save_page_json_atomic(paths, image_key, image_data)
            return resolve_page_paths(saved), {"cleared": len(overlays), "count": 0}

        if action == "remove":
            if not overlay_id:
                raise ValueError("overlay_id is required for action='remove'")
            before = len(overlays)
            overlays = [item for item in overlays if str(item.get("id")) != str(overlay_id)]
            image_data["paste_overlays"] = overlays
            saved = save_page_json_atomic(paths, image_key, image_data)
            return resolve_page_paths(saved), {"removed": before - len(overlays), "count": len(overlays)}

        if action == "add_clone_patch":
            if not source_rect_xyxy or len(source_rect_xyxy) < 4 or not target_center_xy or len(target_center_xy) < 2:
                raise ValueError("source_rect_xyxy=[sx1, sy1, sx2, sy2] and target_center_xy=[tx, ty] are required")
            # Prefer cloning from clean inpainted or base image
            src_img_path = paths.inpainted_path if paths.inpainted_exists else paths.display_base_path
            src_rgb = open_rgb_image(src_img_path)
            h, w = src_rgb.shape[:2]
            sx1 = max(0, min(w - 1, int(round(source_rect_xyxy[0]))))
            sy1 = max(0, min(h - 1, int(round(source_rect_xyxy[1]))))
            sx2 = max(sx1 + 1, min(w, int(round(source_rect_xyxy[2]))))
            sy2 = max(sy1 + 1, min(h, int(round(source_rect_xyxy[3]))))
            patch_rgb = src_rgb[sy1:sy2, sx1:sx2]
            ph, pw = patch_rgb.shape[:2]
            alpha = np.full((ph, pw), 255, dtype=np.uint8)
            if feather_px > 0 and min(ph, pw) > feather_px * 3:
                dist = np.minimum(
                    np.minimum(np.arange(ph)[:, None], np.arange(ph - 1, -1, -1)[:, None]),
                    np.minimum(np.arange(pw)[None, :], np.arange(pw - 1, -1, -1)[None, :]),
                )
                alpha = np.clip((dist + 1) / float(feather_px + 1) * 255.0, 0, 255).astype(np.uint8)
            rgba = np.dstack([patch_rgb, alpha])
            target_w = float(width if width is not None else pw)
            target_h = float(height if height is not None else ph)
        elif action == "add_image_file":
            if not image_file_path or not target_center_xy or len(target_center_xy) < 2:
                raise ValueError("image_file_path and target_center_xy=[tx, ty] are required")
            from PIL import Image

            with Image.open(image_file_path) as im:
                rgba = np.array(im.convert("RGBA"), dtype=np.uint8, copy=True)
            ph, pw = rgba.shape[:2]
            target_w = float(width if width is not None else pw)
            target_h = float(height if height is not None else ph)
        else:
            raise ValueError(f"Unsupported paste_overlay action: {action!r}")

        bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
        ok, encoded = cv2.imencode(".png", bgra)
        if not ok:
            raise RuntimeError("Failed to encode paste overlay PNG")
        b64_png = base64.b64encode(encoded).decode("ascii")

        new_item = {
            "id": uuid.uuid4().hex,
            "name": name,
            "z": int(z),
            "visible": True,
            "opacity": max(0.0, min(1.0, float(opacity))),
            "center_x": float(target_center_xy[0]),
            "center_y": float(target_center_xy[1]),
            "width": target_w,
            "height": target_h,
            "rotation": float(rotation),
            "flip_h": bool(flip_h),
            "flip_v": bool(flip_v),
            "image": b64_png,
        }
        overlays.append(new_item)
        image_data["paste_overlays"] = overlays
        saved = save_page_json_atomic(paths, image_key, image_data)
        return resolve_page_paths(saved), {
            "added_overlay": {k: v for k, v in new_item.items() if k != "image"},
            "count": len(overlays),
        }
