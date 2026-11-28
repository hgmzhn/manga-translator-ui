"""Closed-loop optical bubble centering (Cusp-Bisector Multi-Lobe Split + Gestalt Ellipse + Orthogonal Clearance)."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image

from .path_resolver import PagePaths, list_manga_pages_in_path, resolve_page_paths
from .project_store import (
    _inpainted_path_identity,
    _parse_loaded_page_json,
    apply_region_updates_to_page,
    compute_page_revision,
    ensure_region_uids,
    load_page_json,
    region_has_perspective_quad,
)
from .richtext_builder import region_max_richtext_blur


def detect_all_concave_cusps(sub_mask: np.ndarray, pts_arr: np.ndarray) -> list[tuple[int, np.ndarray, float]]:
    """Detect ALL inward concavity cusps along an external contour (even when multiple cusps share one convex-hull pocket)."""
    n = len(pts_arr)
    sh, sw = sub_mask.shape
    depths = np.zeros(n, dtype=np.float32)
    bisectors = np.zeros((n, 2), dtype=np.float32)

    for k in (10, 18, 28):
        if n <= 2 * k + 5:
            continue
        p_prev = np.roll(pts_arr, k, axis=0).astype(np.float32)
        p_next = np.roll(pts_arr, -k, axis=0).astype(np.float32)
        p_cur = pts_arr.astype(np.float32)

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
            if perp_d[i] < 2.2 or v_norm[i] < 0.25:
                continue
            out_dir = v_sum[i] / v_norm[i]
            in_dir = -out_dir
            ox, oy = int(round(p_cur[i, 0] + 4.0 * out_dir[0])), int(round(p_cur[i, 1] + 4.0 * out_dir[1]))
            ix, iy = int(round(p_cur[i, 0] + 4.0 * in_dir[0])), int(round(p_cur[i, 1] + 4.0 * in_dir[1]))
            mx, my = int(round(mid[i, 0])), int(round(mid[i, 1]))
            out_black = (not (0 <= ox < sw and 0 <= oy < sh)) or (sub_mask[oy, ox] == 0)
            mid_black = (not (0 <= mx < sw and 0 <= my < sh)) or (sub_mask[my, mx] == 0)
            in_white = (0 <= ix < sw and 0 <= iy < sh) and (sub_mask[iy, ix] > 0)
            if out_black and mid_black and in_white and perp_d[i] > depths[i]:
                depths[i] = perp_d[i]
                bisectors[i] = in_dir

    cusps = []
    win = 12
    for i in range(n):
        if depths[i] < 2.5:
            continue
        idxs = [(i + d) % n for d in range(-win, win + 1)]
        if depths[i] >= np.max(depths[idxs]) and (i == min(j for j in idxs if depths[j] == depths[i])):
            cusps.append((i, bisectors[i], float(depths[i])))
    return cusps


def split_multilobe_cusp_bisector(comp_mask: np.ndarray, reg_pts: list[tuple[float, float]]) -> np.ndarray:
    """Split connected multi-lobe speech bubbles along true waist necks guided by cusp inward angle bisectors."""
    ys_all, xs_all = np.nonzero(comp_mask > 0)
    if xs_all.size < 80:
        return comp_mask
    pad_b = 4
    x0, x1 = max(0, int(xs_all.min())), min(comp_mask.shape[1], int(xs_all.max()) + 1)
    y0, y1 = max(0, int(ys_all.min())), min(comp_mask.shape[0], int(ys_all.max()) + 1)

    sub_raw = np.pad(comp_mask[y0:y1, x0:x1], pad_b, mode="constant", constant_values=0)
    local_pts = [(cx - x0 + pad_b, cy - y0 + pad_b) for cx, cy in reg_pts]
    sh, sw = sub_raw.shape

    kern_tail = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    sub = cv2.morphologyEx(sub_raw, cv2.MORPH_OPEN, kern_tail)
    if any(sub[max(0, min(sh - 1, int(round(cy)))), max(0, min(sw - 1, int(round(cx))))] == 0 for cx, cy in local_pts):
        sub = sub_raw.copy()

    contours, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return comp_mask
    cnt = max(contours, key=cv2.contourArea)
    n_cnt = len(cnt)
    if n_cnt < 40:
        return comp_mask

    pts_arr = cnt[:, 0, :]
    cusps = detect_all_concave_cusps(sub, pts_arr)
    if not cusps:
        return comp_mask

    cusp_idxs = np.array([c[0] for c in cusps], dtype=np.int32)
    seeds_arr = np.array(local_pts, dtype=np.float32)
    target_idxs = np.unique(np.concatenate([np.arange(0, n_cnt, 3), cusp_idxs]))

    cands = []
    for f_idx, in_bis, _ in cusps:
        p1 = pts_arr[f_idx].astype(np.float32)
        for j_idx in target_idxs:
            j_idx = int(j_idx)
            if min(abs(f_idx - j_idx), n_cnt - abs(f_idx - j_idx)) < 35:
                continue
            p2 = pts_arr[j_idx].astype(np.float32)
            dv = p2 - p1
            clen = float(np.hypot(dv[0], dv[1]))
            if clen < 14.0 or clen > 295.0:
                continue
            d_unit = dv / clen
            dot1 = float(d_unit[0] * in_bis[0] + d_unit[1] * in_bis[1])
            if dot1 < 0.52:
                continue

            rel = seeds_arr - p1[None, :]
            cross = dv[0] * rel[:, 1] - dv[1] * rel[:, 0]
            if not (np.any(cross > 0) and np.any(cross < 0)):
                continue
            t_proj = np.clip((rel[:, 0] * dv[0] + rel[:, 1] * dv[1]) / (clen * clen), 0.0, 1.0)
            proj = p1[None, :] + t_proj[:, None] * dv[None, :]
            if float(np.hypot(seeds_arr[:, 0] - proj[:, 0], seeds_arr[:, 1] - proj[:, 1]).min()) < 18.0:
                continue

            n_samp = int(clen) + 1
            xs = np.linspace(p1[0], p2[0], n_samp).astype(int)
            ys = np.linspace(p1[1], p2[1], n_samp).astype(int)
            if n_samp > 8 and float(np.mean(sub[ys[3:-3], xs[3:-3]] > 0)) < 0.92:
                continue

            best_opp_bonus = 1.0
            for c2_idx, bis2, _ in cusps:
                if c2_idx != f_idx and min(abs(j_idx - c2_idx), n_cnt - abs(j_idx - c2_idx)) <= 10:
                    if float((-d_unit[0]) * bis2[0] + (-d_unit[1]) * bis2[1]) >= 0.40:
                        best_opp_bonus = min(best_opp_bonus, 0.58)

            score = (clen / (dot1 ** 1.6)) * best_opp_bonus
            cands.append((score, (int(round(p1[0])), int(round(p1[1]))), (int(round(p2[0])), int(round(p2[1])))))

    cands.sort(key=lambda x: x[0])
    work = sub_raw.copy()

    def seed_labels(mask_img: np.ndarray) -> list[int]:
        _, l_img = cv2.connectedComponents(mask_img, connectivity=4)
        return [
            int(l_img[max(0, min(sh - 1, int(round(cy)))), max(0, min(sw - 1, int(round(cx))))])
            for cx, cy in local_pts
        ]

    for _, p1, p2 in cands:
        cur_s = seed_labels(work)
        prev_k = len(set(cur_s) - {0})
        if prev_k >= len(local_pts):
            break
        dv = np.array([p2[0] - p1[0], p2[1] - p1[1]], dtype=np.float32)
        L = float(np.hypot(dv[0], dv[1])) + 1e-6
        q1 = (int(round(p1[0] - 2.5 * dv[0] / L)), int(round(p1[1] - 2.5 * dv[1] / L)))
        q2 = (int(round(p2[0] + 2.5 * dv[0] / L)), int(round(p2[1] + 2.5 * dv[1] / L)))

        thin = work.copy()
        cv2.line(thin, q1, q2, 0, thickness=4)
        new_s = seed_labels(thin)
        new_k = len(set(new_s) - {0})
        if 0 not in new_s and new_k > prev_k:
            buf = work.copy()
            cv2.line(buf, q1, q2, 0, thickness=8)
            buf_s = seed_labels(buf)
            work = buf if (0 not in buf_s and len(set(buf_s) - {0}) == new_k) else thin

    out = comp_mask.copy()
    out[y0:y1, x0:x1] = work[pad_b : sh - pad_b, pad_b : sw - pad_b]
    return out


def strip_pointer_tails(lobe_mask: np.ndarray) -> np.ndarray:
    """Amputate narrow outward speech pointer tails while keeping flat walls, curved arcs, and inward spikes."""
    ys_all, xs_all = np.nonzero(lobe_mask > 0)
    if xs_all.size < 100:
        return lobe_mask
    pad_b = 3
    x0, x1 = max(0, int(xs_all.min())), min(lobe_mask.shape[1], int(xs_all.max()) + 1)
    y0, y1 = max(0, int(ys_all.min())), min(lobe_mask.shape[0], int(ys_all.max()) + 1)
    sub = np.pad(lobe_mask[y0:y1, x0:x1], pad_b, mode="constant", constant_values=0)
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    max_d = float(dist.max())
    if max_d < 12.0:
        return lobe_mask
    r = max(4, min(14, int(round(0.22 * max_d))))
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    opened = cv2.morphologyEx(sub, cv2.MORPH_OPEN, kern)
    if int(opened.sum()) < 150:
        return lobe_mask
    num_b, b_l, b_s, _ = cv2.connectedComponentsWithStats(opened, connectivity=4)
    if num_b > 1:
        best = 1 + int(np.argmax(b_s[1:, cv2.CC_STAT_AREA]))
        opened = (b_l == best).astype(np.uint8)
    out = np.zeros_like(lobe_mask)
    sh, sw = sub.shape
    out[y0:y1, x0:x1] = opened[pad_b : sh - pad_b, pad_b : sw - pad_b]
    return out


def _run_span_1d(arr_1d: np.ndarray, ref_idx: int) -> tuple[int, int] | None:
    padded = np.concatenate(([0], arr_1d, [0]))
    diffs = np.diff(padded)
    starts = np.nonzero(diffs == 1)[0]
    ends = np.nonzero(diffs == -1)[0] - 1
    if starts.size == 0:
        return None
    for s, e in zip(starts, ends):
        if s <= ref_idx <= e and (e - s) >= 6:
            return int(s), int(e)
    best = int(np.argmax(ends - starts))
    if (ends[best] - starts[best]) >= 6:
        return int(starts[best]), int(ends[best])
    return None


def _safe_clamp(val: float, min_neg_room: float, min_pos_room: float, floor_px: float) -> float:
    lo = -(min_neg_room - floor_px)
    hi = +(min_pos_room - floor_px)
    if lo > hi:
        return 0.5 * (min_pos_room - min_neg_room)
    return float(min(max(val, lo), hi))


def fill_small_interior_holes(mask: np.ndarray, max_hole_area: int = 500) -> np.ndarray:
    """Fill tiny interior dark specks/pinholes inside a white bubble BEFORE morphological opening."""
    inv = (mask == 0).astype(np.uint8)
    num_l, lbls, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if num_l <= 2:
        return mask
    out = mask.copy()
    h, w = mask.shape
    for cid in range(1, num_l):
        x0, y0, bw, bh, area = stats[cid]
        if area <= max_hole_area and x0 > 0 and y0 > 0 and (x0 + bw) < w and (y0 + bh) < h:
            out[lbls == cid] = 1
    return out


def gestalt_complete_lobe(lobe_mask: np.ndarray, real_comp_mask: np.ndarray) -> np.ndarray:
    """Reconstruct each multi-lobe bubble's complete underlying oval via `cv2.fitEllipse` on its unclipped outer curved arc."""
    clean_lobe = fill_small_interior_holes(lobe_mask)
    clean_real = fill_small_interior_holes(real_comp_mask)
    bm = strip_pointer_tails(clean_lobe)
    if np.array_equal(lobe_mask, real_comp_mask):
        return bm

    contours, _ = cv2.findContours(bm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return bm
    cnt = max(contours, key=cv2.contourArea)[:, 0, :]
    dist_to_real_wall = cv2.distanceTransform(clean_real, cv2.DIST_L2, 3)

    ys_r, xs_r = np.nonzero(clean_real > 0)
    rx0, rx1, ry0, ry1 = int(xs_r.min()), int(xs_r.max()), int(ys_r.min()), int(ys_r.max())

    arc_pts = []
    for x, y in cnt:
        if dist_to_real_wall[y, x] > 4.5:
            continue
        if (
            (abs(x - rx0) <= 3 and np.sum(clean_real[:, rx0 : rx0 + 4]) > 45)
            or (abs(x - rx1) <= 3 and np.sum(clean_real[:, max(0, rx1 - 3) : rx1 + 1]) > 45)
            or (abs(y - ry0) <= 3 and np.sum(clean_real[ry0 : ry0 + 4, :]) > 45)
            or (abs(y - ry1) <= 3 and np.sum(clean_real[max(0, ry1 - 3) : ry1 + 1]) > 45)
        ):
            continue
        arc_pts.append([float(x), float(y)])

    if len(arc_pts) < 25:
        return bm
    try:
        (ecx, ecy), (ma, MA), angle = cv2.fitEllipse(np.array(arc_pts, dtype=np.float32))
    except cv2.error:
        return bm
    ys_b, xs_b = np.nonzero(bm > 0)
    bw, bh = float(xs_b.max() - xs_b.min()), float(ys_b.max() - ys_b.min())
    if ma <= 10 or MA <= 10 or MA > 2.2 * max(bw, bh) or ma > 1.8 * max(bw, bh):
        return bm
    ell_mask = np.zeros_like(bm)
    cv2.ellipse(ell_mask, ((ecx, ecy), (ma, MA), angle), 1, thickness=-1)
    dist_from_bm = cv2.distanceTransform((bm == 0).astype(np.uint8), cv2.DIST_L2, 5)
    completed = ((bm > 0) | ((ell_mask > 0) & (dist_from_bm <= 55.0))).astype(np.uint8) & clean_real
    return strip_pointer_tails(completed)


def compute_orthogonal_equal_margin_center(
    raw_lobe_mask: np.ndarray,
    real_comp_mask: np.ndarray,
    hard_safe_mask: np.ndarray,
    ink_mask: np.ndarray,
    cur_cx: float,
    cur_cy: float,
    fs: float = 26.0,
    has_trailing_light_punct: bool = False,
) -> tuple[float, float] | None:
    """Gestalt Oval Completion + Full-Ink P2 Rectangular-Corner 5-Iteration 2D Fixed-Point Centering."""
    _ = (fs, has_trailing_light_punct)
    gm = gestalt_complete_lobe(raw_lobe_mask, real_comp_mask)
    hs_clean = fill_small_interior_holes(hard_safe_mask, max_hole_area=120)
    iys0, ixs0 = np.nonzero(ink_mask > 0)
    if ixs0.size < 15:
        return None

    h, w = gm.shape
    iy_min0, iy_max0 = int(iys0.min()), int(iys0.max())
    ix_min0, ix_max0 = int(ixs0.min()), int(ixs0.max())

    dx, dy = 0.0, 0.0
    for _ in range(5):
        sx_min = max(0, min(w - 1, int(round(ix_min0 + dx))))
        sx_max = max(0, min(w - 1, int(round(ix_max0 + dx))))
        sy_min = max(0, min(h - 1, int(round(iy_min0 + dy))))
        sy_max = max(0, min(h - 1, int(round(iy_max0 + dy))))

        ref_x = int(round(0.5 * (sx_min + sx_max)))
        pls, prs = [], []
        for y_row in range(sy_min, sy_max + 1):
            sp = _run_span_1d(gm[y_row, :], ref_x)
            if sp is not None:
                pls.append(float(sx_min - sp[0]))
                prs.append(float(sp[1] - sx_max))
        if len(pls) >= 4:
            eff_L = 0.88 * float(np.percentile(pls, 2)) + 0.12 * float(np.median(pls))
            eff_R = 0.88 * float(np.percentile(prs, 2)) + 0.12 * float(np.median(prs))
            dx += 0.5 * (eff_R - eff_L)

        h_pl, h_pr = [], []
        for y_row in range(sy_min, sy_max + 1):
            sp = _run_span_1d(hs_clean[y_row, :], ref_x)
            if sp is not None:
                h_pl.append(float((ix_min0 + dx) - sp[0]))
                h_pr.append(float(sp[1] - (ix_max0 + dx)))
        if h_pl and h_pr:
            dx += _safe_clamp(0.0, float(np.percentile(h_pl, 2)), float(np.percentile(h_pr, 2)), 14.0)

        sx_min = max(0, min(w - 1, int(round(ix_min0 + dx))))
        sx_max = max(0, min(w - 1, int(round(ix_max0 + dx))))

        ref_y = int(round(0.5 * (sy_min + sy_max)))
        pts_t, pts_b = [], []
        for x_col in range(sx_min, sx_max + 1):
            sp = _run_span_1d(gm[:, x_col], ref_y)
            if sp is not None:
                pts_t.append(float(sy_min - sp[0]))
                pts_b.append(float(sp[1] - sy_max))
        if len(pts_t) >= 4:
            eff_T = 0.55 * float(np.percentile(pts_t, 10)) + 0.45 * float(np.median(pts_t))
            eff_B = 0.55 * float(np.percentile(pts_b, 10)) + 0.45 * float(np.median(pts_b))
            dy += 0.5 * (eff_B - eff_T)

        h_pt, h_pb = [], []
        for x_col in range(sx_min, sx_max + 1):
            sp = _run_span_1d(hs_clean[:, x_col], ref_y)
            if sp is not None:
                h_pt.append(float((iy_min0 + dy) - sp[0]))
                h_pb.append(float(sp[1] - (iy_max0 + dy)))
        if h_pt and h_pb:
            dy += _safe_clamp(0.0, float(np.percentile(h_pt, 2)), float(np.percentile(h_pb, 2)), 16.0)

    return round(cur_cx + dx, 1), round(cur_cy + dy, 1)


def _estimate_empty_lobe_center(raw_lobe_mask: np.ndarray, reg: dict[str, Any]) -> tuple[float, float] | None:
    """Pre-render fallback when rendered result ink is unavailable: uses synthetic text block dimensions."""
    body_mask = strip_pointer_tails(raw_lobe_mask)
    ys_all, xs_all = np.nonzero(body_mask > 0)
    if xs_all.size < 150:
        return None
    h, w = body_mask.shape
    cx, cy = float(np.median(xs_all)), float(np.median(ys_all))

    tr = str(reg.get("translation", "")).replace("[BR]", "\n")
    cols = [c for c in tr.split("\n") if c.strip()] or ["汉"]
    fs = float(reg.get("font_size") or 26)
    est_w = max(fs, len(cols) * fs * 1.05)
    est_h = max(fs * 1.5, max(len(c) for c in cols) * fs * 1.0)

    for _ in range(2):
        x1 = max(0, int(round(cx - 0.5 * est_w)))
        x2 = min(w - 1, int(round(cx + 0.5 * est_w)))
        pts_t, pts_b = [], []
        for x in range(x1, x2 + 1):
            sp = _run_span_1d(body_mask[:, x], int(round(cy)))
            if sp:
                pts_t.append((cy - 0.5 * est_h) - sp[0])
                pts_b.append(sp[1] - (cy + 0.5 * est_h))
        if len(pts_t) >= 3:
            et = 0.65 * float(np.percentile(pts_t, 15)) + 0.35 * float(np.median(pts_t))
            eb = 0.65 * float(np.percentile(pts_b, 15)) + 0.35 * float(np.median(pts_b))
            cy += 0.5 * (eb - et)

        y1 = max(0, int(round(cy - 0.5 * est_h)))
        y2 = min(h - 1, int(round(cy + 0.5 * est_h)))
        pls, prs = [], []
        for y in range(y1, y2 + 1):
            sp = _run_span_1d(body_mask[y, :], int(round(cx)))
            if sp:
                pls.append((cx - 0.5 * est_w) - sp[0])
                prs.append(sp[1] - (cx + 0.5 * est_w))
        if len(pls) >= 3:
            el = 0.70 * float(np.percentile(pls, 12)) + 0.30 * float(np.median(pls))
            er = 0.70 * float(np.percentile(prs, 12)) + 0.30 * float(np.median(prs))
            cx += 0.5 * (er - el)

    return round(cx, 1), round(cy, 1)


def partition_page_white_bubbles(inp_gray: np.ndarray, regs: list[dict[str, Any]]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Return `{region_index: (lobe_mask, real_unsplit_bubble_mask)}` for all white-bubble vertical regions on a page."""
    h, w = inp_gray.shape
    sealed = cv2.dilate((inp_gray < 225).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    _, cc_l, cc_s, _ = cv2.connectedComponentsWithStats((sealed == 0).astype(np.uint8), connectivity=4)

    cc_map: dict[int, list[int]] = {}
    rc_map: dict[int, tuple[float, float]] = {}
    for i, r in enumerate(regs):
        if str(r.get("direction", "")).lower().startswith("h") or not r.get("center"):
            continue
        if abs(float(r.get("angle", 0.0) or 0.0)) >= 3.0:
            continue
        if region_has_perspective_quad(r):
            continue
        raw_cx, raw_cy = float(r["center"][0]), float(r["center"][1])
        cx = max(0.0, min(float(max(0, w - 1)), raw_cx))
        cy = max(0.0, min(float(max(0, h - 1)), raw_cy))
        ix, iy = max(0, min(w - 1, int(round(cx)))), max(0, min(h - 1, int(round(cy))))
        if inp_gray[iy, ix] < 235:
            continue
        lbl = int(cc_l[iy, ix])
        if lbl == 0:
            continue
        x0, y0, bw, bh, area = cc_s[lbl]
        edges = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw >= w - 2) + int(y0 + bh >= h - 2)
        if area <= 0.22 * w * h and edges < 3:
            rc_map[i] = (cx, cy)
            cc_map.setdefault(lbl, []).append(i)

    lobe_by_ri: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lbl, ridxs in cc_map.items():
        cm = (cc_l == lbl).astype(np.uint8)
        if len(ridxs) == 1:
            lobe_by_ri[ridxs[0]] = (cm, cm)
        else:
            sm = split_multilobe_cusp_bisector(cm, [rc_map[ri] for ri in ridxs])
            _, sl = cv2.connectedComponents(sm, connectivity=4)
            groups: dict[int, list[int]] = {}
            for ri in ridxs:
                cx, cy = rc_map[ri]
                sx = max(0, min(w - 1, int(round(cx))))
                sy = max(0, min(h - 1, int(round(cy))))
                k = int(sl[sy, sx])
                if k > 0:
                    groups.setdefault(k, []).append(ri)
            for k, sub in groups.items():
                if len(sub) == 1:
                    lobe_by_ri[sub[0]] = ((sl == k).astype(np.uint8), cm)
    return lobe_by_ri


def build_region_hard_safe_mask(
    real_comp_mask: np.ndarray,
    excl_inks: dict[int, np.ndarray],
    ri: int,
    gap_px: int = 14,
) -> np.ndarray:
    other = np.zeros_like(real_comp_mask)
    for k, m in excl_inks.items():
        if k != ri:
            other |= m
    if not np.any(other):
        return real_comp_mask
    ys, xs = np.nonzero(other > 0)
    pad = gap_px + 2
    x0, x1 = max(0, int(xs.min()) - pad), min(real_comp_mask.shape[1], int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(real_comp_mask.shape[0], int(ys.max()) + pad + 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap_px + 1, 2 * gap_px + 1))
    dil = cv2.dilate(other[y0:y1, x0:x1], kern)
    safe = real_comp_mask.copy()
    safe[y0:y1, x0:x1] = safe[y0:y1, x0:x1] & (dil == 0).astype(np.uint8)
    return safe


def assign_exclusive_page_ink_masks(ink_full: np.ndarray, regs: list[dict[str, Any]]) -> dict[int, np.ndarray]:
    """Partition `ink_full` mutually exclusively among regions using each region's layout box around `center`."""
    ys, xs = np.nonzero(ink_full > 0)
    if xs.size == 0 or not regs:
        return {}
    boxes = []
    valid_ris = []
    for i, r in enumerate(regs):
        if not r.get("center"):
            continue
        cx, cy = float(r["center"][0]), float(r["center"][1])
        tr = str(r.get("translation", "")).replace("[BR]", "\n")
        cols = [c for c in tr.split("\n") if c] or ["汉"]
        fs = float(r.get("font_size") or 26)
        ls = float(r.get("line_spacing") or 1.02)
        if str(r.get("direction", "")).lower().startswith("h"):
            hw = 0.5 * (max(len(c) for c in cols) * fs * 1.05) + 0.65 * fs
            hh = 0.5 * (len(cols) * fs * ls) + 0.55 * fs
        else:
            hw = 0.5 * (len(cols) * fs * ls) + 0.55 * fs
            hh = 0.5 * (max(len(c) for c in cols) * fs * 1.04) + 0.65 * fs
        boxes.append((cx, cy, max(16.0, hw), max(16.0, hh)))
        valid_ris.append(i)
    if not boxes:
        return {}
    b_arr = np.array(boxes, dtype=np.float32)
    px, py = xs.astype(np.float32)[:, None], ys.astype(np.float32)[:, None]
    norm_cheb = np.maximum(np.abs(px - b_arr[None, :, 0]) / b_arr[None, :, 2], np.abs(py - b_arr[None, :, 1]) / b_arr[None, :, 3])
    best_k = np.argmin(norm_cheb, axis=1)
    best_val = norm_cheb[np.arange(xs.size), best_k]
    out: dict[int, np.ndarray] = {}
    for k_idx, ri in enumerate(valid_ris):
        sel = (best_k == k_idx) & (best_val <= 1.18)
        if np.any(sel):
            m = np.zeros_like(ink_full)
            m[ys[sel], xs[sel]] = 1
            out[ri] = m
    return out


def _extract_page_exclusive_inks(
    inp_g: np.ndarray,
    res_g: Optional[np.ndarray],
    regs: list[dict[str, Any]],
) -> dict[int, np.ndarray]:
    """Extract per-region rendered ink masks, using a lower luma threshold (``>= 18.0``) for regions with ``richtext.v1`` ``blur > 0.2``."""
    if res_g is None or res_g.shape != inp_g.shape or not regs:
        return {}
    diff_g = np.clip(inp_g.astype(np.float32) - res_g.astype(np.float32), 0.0, 255.0)
    ink_std = (diff_g >= 35.0).astype(np.uint8)
    excl_inks = assign_exclusive_page_ink_masks(ink_std, regs)
    blurred_indices = [ri for ri, r in enumerate(regs) if isinstance(r, dict) and region_max_richtext_blur(r) > 0.2]
    if blurred_indices:
        ink_soft = (diff_g >= 18.0).astype(np.uint8)
        soft_excl = assign_exclusive_page_ink_masks(ink_soft, regs)
        for ri in blurred_indices:
            if ri in soft_excl:
                excl_inks[ri] = soft_excl[ri]
    return excl_inks


def _is_rendered_output_fresh(paths: PagePaths) -> bool:
    """Return True iff ``rendered_output_path`` exists and is at least as fresh as ``json_path`` and ``inpainted_path``."""
    if not (paths.rendered_output_exists and paths.rendered_output_path and paths.rendered_output_path.is_file()):
        return False
    try:
        ren_mtime = paths.rendered_output_path.stat().st_mtime_ns
        if paths.json_exists and paths.json_path.is_file():
            if ren_mtime < paths.json_path.stat().st_mtime_ns:
                return False
        if paths.inpainted_exists and paths.inpainted_path.is_file():
            if ren_mtime < paths.inpainted_path.stat().st_mtime_ns:
                return False
        return True
    except OSError:
        return False


def compute_chapter_optical_centers(target_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Compute optical centers without mutating JSON or re-rendering."""
    pages = [p for p in list_manga_pages_in_path(target_path) if p.json_exists and p.inpainted_exists]
    report: dict[str, list[dict[str, Any]]] = {}
    for p in pages:
        with Image.open(p.inpainted_path) as im:
            inp_g = np.array(im.convert("L"))
        res_g: Optional[np.ndarray] = None
        if _is_rendered_output_fresh(p):
            with Image.open(p.rendered_output_path) as rim:
                res_g = np.array(rim.convert("L"))
        _, img_data = load_page_json(p)
        regs = img_data.get("regions") or []
        excl_inks = _extract_page_exclusive_inks(inp_g, res_g, regs)
        lobes = partition_page_white_bubbles(inp_g, regs)
        page_items: list[dict[str, Any]] = []
        for ri, (lobe_m, real_m) in sorted(lobes.items()):
            r = regs[ri]
            cx, cy = float(r["center"][0]), float(r["center"][1])
            tr = str(r.get("translation", ""))
            fs = float(r.get("font_size") or 26)
            light_tail = tr.endswith(("︙", "。", "，", "…", "︒", "︐"))
            res_pt = None
            if ri in excl_inks:
                hard_safe = build_region_hard_safe_mask(real_m, excl_inks, ri, gap_px=15)
                res_pt = compute_orthogonal_equal_margin_center(lobe_m, real_m, hard_safe, excl_inks[ri], cx, cy, fs, light_tail)
            if res_pt is None:
                res_pt = _estimate_empty_lobe_center(lobe_m, r)
            if res_pt is not None:
                page_items.append({"index": ri, "region_uid": r.get("region_uid"), "old_center": [cx, cy], "optical_center": list(res_pt)})
        if page_items:
            report[p.source_path.name] = page_items
    return report


def _rendered_artifact_fingerprint(paths: PagePaths) -> Optional[tuple[int, int, str]]:
    if not (paths.rendered_output_exists and paths.rendered_output_path and paths.rendered_output_path.is_file()):
        return None
    try:
        st = paths.rendered_output_path.stat()
        digest = hashlib.sha1(paths.rendered_output_path.read_bytes()).hexdigest()
        return (st.st_mtime_ns, st.st_size, digest)
    except OSError:
        return None


def _exact_float_token(value: Any) -> Any:
    try:
        fv = float(value)
    except (TypeError, ValueError, OverflowError):
        return ("invalid", repr(value))
    if math.isnan(fv):
        return ("nan",)
    return fv


def _regions_partition_signature(regs: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Capture every exact region geometry/layout input consumed by ``partition_page_white_bubbles``."""
    sig: list[tuple[Any, ...]] = []
    for r in regs:
        if not isinstance(r, dict):
            continue
        c = r.get("center")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            cx_tok = _exact_float_token(c[0])
            cy_tok = _exact_float_token(c[1])
            px_tok = (
                (int(round(cx_tok)), int(round(cy_tok)))
                if isinstance(cx_tok, float)
                and isinstance(cy_tok, float)
                and math.isfinite(cx_tok)
                and math.isfinite(cy_tok)
                else None
            )
            c_tup: Any = (cx_tok, cy_tok, px_tok)
        else:
            c_tup = None
        dir_raw = str(r.get("direction") or "")
        dir_is_horiz = dir_raw.lower().startswith("h")
        dir_norm = str(r.get("direction") or "auto").strip().lower()
        ang_tok = _exact_float_token(r.get("angle", 0.0) or 0.0)
        ang_ge_3 = bool(isinstance(ang_tok, float) and abs(ang_tok) >= 3.0)
        has_quad = region_has_perspective_quad(r)
        wf_rect = r.get("white_frame_rect_local")
        wf_tup = (
            tuple(_exact_float_token(v) for v in wf_rect[:4])
            if isinstance(wf_rect, (list, tuple)) and len(wf_rect) >= 4
            else None
        )
        sig.append(
            (
                str(r.get("region_uid") or "").strip(),
                dir_is_horiz,
                dir_norm,
                c_tup,
                ang_tok,
                ang_ge_3,
                has_quad,
                wf_tup,
            )
        )
    return sig


def _non_export_content_digest(raw_parsed: Any, inp_stat: tuple[Any, ...]) -> str:
    """Hash page JSON excluding export-path metadata (`last_export_dir`, `last_output_path`) plus `inpainted` identity."""
    if isinstance(raw_parsed, dict):
        stripped: Any = {
            k: (
                {ek: ev for ek, ev in v.items() if ek not in ("last_export_dir", "last_output_path")}
                if isinstance(v, dict)
                else v
            )
            for k, v in raw_parsed.items()
        }
    else:
        stripped = raw_parsed
    h = hashlib.sha256()
    h.update(json.dumps(stripped, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    if len(inp_stat) == 3:
        h.update(f"|inp:{inp_stat[0]}:{inp_stat[1]}:{inp_stat[2]}".encode("utf-8"))
    elif len(inp_stat) == 2:
        h.update(f"|inp:{inp_stat[0]}:{inp_stat[1]}".encode("utf-8"))
    else:
        raise TypeError(f"Unsupported inp_stat: {inp_stat!r}")
    return h.hexdigest()


def _read_consistent_page_state(
    source_path: Path,
) -> Optional[tuple[PagePaths, str, dict[str, Any], str, tuple[str, int, int]]]:
    """Read a mutually consistent `(paths, page_rev, image_data, non_export_sig, inp_stat)` snapshot."""
    canonical_key = os.path.abspath(str(source_path))
    for _attempt in range(5):
        p_cur = resolve_page_paths(source_path)
        if not (
            p_cur.json_exists
            and p_cur.json_path.is_file()
            and p_cur.inpainted_exists
            and p_cur.inpainted_path.is_file()
        ):
            return None
        try:
            st_inp_1 = p_cur.inpainted_path.stat()
            inp_stat_1 = (
                _inpainted_path_identity(p_cur.inpainted_path),
                int(st_inp_1.st_size),
                int(st_inp_1.st_mtime_ns),
            )
            raw_bytes_1 = p_cur.json_path.read_bytes()
        except OSError:
            continue
        p_after = resolve_page_paths(source_path)
        if not (
            p_after.json_exists
            and p_after.json_path.is_file()
            and p_after.inpainted_exists
            and p_after.inpainted_path.is_file()
        ):
            return None
        try:
            st_inp_2 = p_after.inpainted_path.stat()
            inp_stat_2 = (
                _inpainted_path_identity(p_after.inpainted_path),
                int(st_inp_2.st_size),
                int(st_inp_2.st_mtime_ns),
            )
            raw_bytes_2 = p_after.json_path.read_bytes()
        except OSError:
            continue
        if (
            os.path.normcase(os.path.abspath(str(p_cur.json_path)))
            != os.path.normcase(os.path.abspath(str(p_after.json_path)))
            or inp_stat_1 != inp_stat_2
            or raw_bytes_1 != raw_bytes_2
        ):
            continue
        raw_parsed = json.loads(raw_bytes_1.decode("utf-8"))
        _, img_data = _parse_loaded_page_json(raw_parsed, canonical_key)
        rev = compute_page_revision(p_after, json_bytes=raw_bytes_1, inpainted_stat=inp_stat_1)
        content_sig = _non_export_content_digest(raw_parsed, inp_stat_1)
        return p_after, rev, img_data, content_sig, inp_stat_1
    return None


def _normalize_render_result(render_res: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(render_res, dict):
        raw_st = render_res.get("status")
        norm_st = str(raw_st) if raw_st is not None else "ok"
        res_dict = dict(render_res)
        res_dict.setdefault("status", norm_st)
        return norm_st, res_dict
    return "ok", {"status": "ok", "result": render_res}


def _partition_lobes_by_uid(
    inp_g: np.ndarray,
    regs: list[dict[str, Any]],
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    ensure_region_uids(regs)
    by_ri = partition_page_white_bubbles(inp_g, regs)
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for ri, masks in by_ri.items():
        if 0 <= ri < len(regs) and isinstance(regs[ri], dict):
            uid = str(regs[ri].get("region_uid") or "").strip()
            if uid:
                out[uid] = masks
    return out


def run_closed_loop_bubble_centering(
    target_path: str | Path,
    *,
    passes: int = 2,
    render_page_fn: Optional[Callable[[PagePaths], Any]] = None,
    render_results_out: Optional[dict[str, Any]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run closed-loop optical centering + re-rendering page-by-page (freeing per-page masks immediately)."""
    pages = [p for p in list_manga_pages_in_path(target_path) if p.json_exists and p.inpainted_exists]
    eff_passes = 1 if render_page_fn is None else max(1, int(passes))
    report: dict[str, list[dict[str, Any]]] = {}

    for p in pages:
        page_name = p.source_path.name
        init_state = _read_consistent_page_state(p.source_path)
        if init_state is None:
            continue
        p_init, _init_rev, init_data, _init_content_sig, inp_stat = init_state
        init_regs = init_data.get("regions") or []
        if not init_regs:
            continue

        with Image.open(p_init.inpainted_path) as im:
            inp_g = np.array(im.convert("L"))
        lobes_by_uid = _partition_lobes_by_uid(inp_g, init_regs)
        if not lobes_by_uid:
            continue
        cached_part_sig = _regions_partition_signature(init_regs)

        last_render_status = "not_requested" if render_page_fn is None else "unchanged"
        verified_fresh_fp: Optional[tuple[int, int, str]] = None
        verified_for_rev: Optional[str] = None
        tainted_fp: Optional[tuple[int, int, str]] = None

        for pass_idx in range(eff_passes):
            cur_state = _read_consistent_page_state(p.source_path)
            if cur_state is None:
                break
            p_fresh, page_rev, img_data, pre_content_sig_0, cur_inp_stat = cur_state
            regs = img_data.get("regions") or []
            if not regs:
                break
            ensure_region_uids(regs)
            cur_part_sig = _regions_partition_signature(regs)
            if cur_inp_stat != inp_stat:
                inp_stat = cur_inp_stat
                with Image.open(p_fresh.inpainted_path) as im:
                    inp_g = np.array(im.convert("L"))
                lobes_by_uid = _partition_lobes_by_uid(inp_g, regs)
                cached_part_sig = cur_part_sig
            elif cur_part_sig != cached_part_sig:
                lobes_by_uid = _partition_lobes_by_uid(inp_g, regs)
                cached_part_sig = cur_part_sig

            if not lobes_by_uid:
                break

            cur_fp = _rendered_artifact_fingerprint(p_fresh)
            is_tainted = tainted_fp is not None and cur_fp == tainted_fp
            rendered_is_fresh = (not is_tainted) and (
                (
                    verified_fresh_fp is not None
                    and cur_fp == verified_fresh_fp
                    and page_rev == verified_for_rev
                )
                or _is_rendered_output_fresh(p_fresh)
            )

            if not rendered_is_fresh and pass_idx == 0 and render_page_fn is not None:
                prev_fp_0 = cur_fp
                pre_res = render_page_fn(p_fresh)
                last_render_status, pre_res_dict = _normalize_render_result(pre_res)

                post_state_0 = _read_consistent_page_state(p.source_path)
                if post_state_0 is None:
                    if render_results_out is not None:
                        render_results_out[page_name] = pre_res_dict
                    break
                p_fresh, rev_before_fp_0, img_data, post_content_sig_0, post_inp_stat_0 = post_state_0
                post_fp_0 = _rendered_artifact_fingerprint(p_fresh)
                rev_after_fp_0 = compute_page_revision(resolve_page_paths(p.source_path))

                fp_updated_0 = post_fp_0 is not None and (
                    prev_fp_0 is None or post_fp_0[0] != prev_fp_0[0] or post_fp_0[2] != prev_fp_0[2]
                )
                if last_render_status in ("ok", "success") and not fp_updated_0:
                    last_render_status = "partial"
                    pre_res_dict["status"] = "partial"
                    pre_res_dict["output_updated"] = False
                    pre_res_dict.setdefault(
                        "error",
                        "Rendered output missing or not updated during pre-render",
                    )
                if render_results_out is not None:
                    render_results_out[page_name] = pre_res_dict

                # Re-bind consistent regions, base image, partition cache, and revision after pre-render (R3-F02 / R3-F20)
                if rev_after_fp_0 != rev_before_fp_0:
                    re_state_0 = _read_consistent_page_state(p.source_path)
                    if re_state_0 is None:
                        break
                    p_fresh, page_rev, img_data, _, cur_inp_stat = re_state_0
                else:
                    page_rev = rev_after_fp_0
                    cur_inp_stat = post_inp_stat_0

                regs = img_data.get("regions") or []
                if not regs:
                    break
                ensure_region_uids(regs)
                cur_part_sig = _regions_partition_signature(regs)
                if cur_inp_stat != inp_stat:
                    inp_stat = cur_inp_stat
                    with Image.open(p_fresh.inpainted_path) as im:
                        inp_g = np.array(im.convert("L"))
                    lobes_by_uid = _partition_lobes_by_uid(inp_g, regs)
                    cached_part_sig = cur_part_sig
                elif cur_part_sig != cached_part_sig:
                    lobes_by_uid = _partition_lobes_by_uid(inp_g, regs)
                    cached_part_sig = cur_part_sig

                if not lobes_by_uid:
                    break

                # Only mark output verified when non-export page content was unchanged across pre-render (R3-F50)
                if (
                    fp_updated_0
                    and last_render_status in ("ok", "partial", "success")
                    and post_content_sig_0 == pre_content_sig_0
                    and rev_before_fp_0 == rev_after_fp_0
                ):
                    verified_fresh_fp = post_fp_0
                    verified_for_rev = rev_after_fp_0
                    tainted_fp = None
                    rendered_is_fresh = True
                else:
                    verified_fresh_fp = None
                    verified_for_rev = None
                    if post_fp_0 is not None:
                        tainted_fp = post_fp_0
                    rendered_is_fresh = False

            res_g: Optional[np.ndarray] = None
            if (
                rendered_is_fresh
                and p_fresh.rendered_output_exists
                and p_fresh.rendered_output_path
                and p_fresh.rendered_output_path.is_file()
            ):
                with Image.open(p_fresh.rendered_output_path) as rim:
                    res_g = np.array(rim.convert("L"))

            excl_inks = _extract_page_exclusive_inks(inp_g, res_g, regs)
            upds: list[dict[str, Any]] = []
            page_items: list[dict[str, Any]] = []
            for ri, r in enumerate(regs):
                if not isinstance(r, dict) or not r.get("center"):
                    continue
                uid = str(r.get("region_uid") or "").strip()
                masks = lobes_by_uid.get(uid)
                if masks is None:
                    continue
                lobe_m, real_m = masks
                cx, cy = float(r["center"][0]), float(r["center"][1])
                tr = str(r.get("translation", ""))
                fs = float(r.get("font_size") or 26)
                light_tail = tr.endswith(("︙", "。", "，", "…", "︒", "︐"))
                res_pt = None
                if ri in excl_inks:
                    hard_safe = build_region_hard_safe_mask(real_m, excl_inks, ri, gap_px=15)
                    res_pt = compute_orthogonal_equal_margin_center(
                        lobe_m, real_m, hard_safe, excl_inks[ri], cx, cy, fs, light_tail
                    )
                if res_pt is None:
                    res_pt = _estimate_empty_lobe_center(lobe_m, r)
                if res_pt is not None:
                    nx, ny = res_pt
                    page_items.append(
                        {
                            "index": ri,
                            "region_uid": uid,
                            "old_center": [cx, cy],
                            "optical_center": [nx, ny],
                            "render_status": last_render_status,
                        }
                    )
                    dx = nx - cx
                    dy = ny - cy
                    if abs(dx) >= 0.9 or abs(dy) >= 0.9:
                        target_key = {"region_uid": uid} if uid else {"region_index": ri}
                        upds.append({**target_key, "move_dx": dx, "move_dy": dy})

            if page_items:
                report[page_name] = page_items
            if not upds:
                break

            apply_region_updates_to_page(p_fresh.source_path, updates=upds, expected_revision=page_rev)
            if render_page_fn is None:
                break

            pre_render_state = _read_consistent_page_state(p.source_path)
            if pre_render_state is None:
                break
            pre_render_paths, _pre_render_rev, _, pre_render_content_sig, _ = pre_render_state
            prev_fp = _rendered_artifact_fingerprint(pre_render_paths)

            render_res = render_page_fn(pre_render_paths)
            last_render_status, last_render_dict = _normalize_render_result(render_res)

            post_render_state = _read_consistent_page_state(p.source_path)
            post_render_paths = (
                post_render_state[0] if post_render_state is not None else resolve_page_paths(p.source_path)
            )
            rev_before_fp = post_render_state[1] if post_render_state is not None else None
            post_render_content_sig = post_render_state[3] if post_render_state is not None else None
            post_fp = _rendered_artifact_fingerprint(post_render_paths)
            rev_after_fp = compute_page_revision(resolve_page_paths(p.source_path))

            # Always verify rendered output updated on EVERY post-write render, including the final pass (R3-F19)
            output_updated = post_fp is not None and (
                prev_fp is None or post_fp[0] != prev_fp[0] or post_fp[2] != prev_fp[2]
            )
            if not output_updated:
                if last_render_status in ("ok", "success"):
                    last_render_status = "partial"
                last_render_dict["status"] = last_render_status
                last_render_dict["output_updated"] = False
                last_render_dict.setdefault(
                    "error",
                    "Rendered output missing or not updated after bubble centering",
                )

            if render_results_out is not None:
                render_results_out[page_name] = last_render_dict
            for item in report.get(page_name, []):
                item["render_status"] = last_render_status

            if not output_updated or last_render_status not in ("ok", "success"):
                verified_fresh_fp = None
                verified_for_rev = None
                if post_fp is not None:
                    tainted_fp = post_fp
                break

            # Only record verified_for_rev when non-export page state matches pre-render & revision stayed stable across fp (R3-F50)
            if (
                post_render_state is not None
                and post_render_content_sig == pre_render_content_sig
                and rev_before_fp is not None
                and rev_before_fp == rev_after_fp
            ):
                verified_fresh_fp = post_fp
                verified_for_rev = rev_after_fp
                tainted_fp = None
            else:
                verified_fresh_fp = None
                verified_for_rev = None
                tainted_fp = post_fp

            if pass_idx + 1 >= eff_passes:
                break

    return report
