"""Multimodal visual inspection image generator for Vision Agents (Antigravity, Codex, Claude).

Generates crisp, pixel-ruler-annotated comparison sheets so Vision Agents can:
1. See numbered text boxes `[0]`, `[1]`, `[2]` alongside original & translated pages,
2. Inspect `Original | Inpainted + Mask Contour | Rendered` side-by-side,
3. Read EXACT world `(X, Y)` pixel coordinates off zoomed 3-panel Region Loupes!
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .path_resolver import PagePaths, resolve_page_paths
from .project_store import (
    decode_mask_base64,
    load_page_json,
    open_rgb_image,
    region_center,
    region_perspective_quad_world,
    region_render_world_bbox_xyxy,
    save_rgb_image_atomic,
)

_RULER_TOP_MARGIN = 50
_RULER_LEFT_MARGIN = 58
_RULER_RIGHT_MARGIN = 14
_RULER_BOT_MARGIN = 16
_RULER_FRAME_PAD_W = _RULER_LEFT_MARGIN + _RULER_RIGHT_MARGIN
_RULER_FRAME_PAD_H = _RULER_TOP_MARGIN + _RULER_BOT_MARGIN


def _draw_badge(img_rgb: np.ndarray, text: str, x: int, y: int, bg_rgb: tuple[int, int, int] = (220, 38, 38)) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.58
    thickness = 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    pad = 4
    h, w = img_rgb.shape[:2]
    bx1 = max(0, min(w - tw - pad * 2, x))
    by1 = max(0, min(h - th - pad * 2, y - th - pad * 2))
    bx2 = min(w, bx1 + tw + pad * 2)
    by2 = min(h, by1 + th + pad * 2)
    cv2.rectangle(img_rgb, (bx1, by1), (bx2, by2), bg_rgb, -1)
    cv2.rectangle(img_rgb, (bx1, by1), (bx2, by2), (255, 255, 255), 1)
    cv2.putText(
        img_rgb,
        text,
        (bx1 + pad, by2 - pad - 1),
        font,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def _annotate_regions_on_rgb(
    canvas_rgb: np.ndarray,
    regions: list[dict[str, Any]],
    *,
    draw_polygons: bool = True,
    draw_centers: bool = False,
    highlight_indices: Optional[set[int]] = None,
    coord_scale: tuple[float, float] = (1.0, 1.0),
) -> np.ndarray:
    out = canvas_rgb.copy()
    sx, sy = float(coord_scale[0]), float(coord_scale[1])
    scale_arr = np.array([[sx, sy]], dtype=np.float32)

    for idx, reg in enumerate(regions):
        if highlight_indices is not None and idx not in highlight_indices:
            color = (130, 140, 155)
            thick = 1
        else:
            palette = [
                (239, 68, 68),
                (37, 99, 235),
                (16, 185, 129),
                (217, 119, 6),
                (147, 51, 234),
                (236, 72, 153),
            ]
            color = palette[idx % len(palette)]
            thick = 2

        if draw_polygons:
            for poly in reg.get("lines") or []:
                raw_pts = np.asarray(poly, dtype=np.float32)
                if raw_pts.ndim == 2 and raw_pts.shape[0] >= 3:
                    pts = np.rint(raw_pts[:, :2] * scale_arr).astype(np.int32)
                    cv2.polylines(out, [pts], isClosed=True, color=color, thickness=thick, lineType=cv2.LINE_AA)
            if draw_centers:
                quad_w = region_perspective_quad_world(reg)
                if quad_w is not None:
                    q_pts = np.rint(np.asarray(quad_w, dtype=np.float32)[:, :2] * scale_arr).astype(np.int32)
                    cv2.polylines(out, [q_pts], isClosed=True, color=(6, 182, 212), thickness=thick, lineType=cv2.LINE_AA)

        if draw_centers:
            rcx, rcy = region_center(reg)
            cx, cy = int(round(rcx * sx)), int(round(rcy * sy))
            cv2.drawMarker(out, (cx, cy), color, markerType=cv2.MARKER_CROSS, markerSize=14, thickness=2)

        wx1, wy1, _, _ = region_render_world_bbox_xyxy(reg)
        x1, y1 = int(round(wx1 * sx)), int(round(wy1 * sy))
        _draw_badge(out, f"#{idx}", x1, y1, bg_rgb=color)
    return out


def _overlay_mask_on_rgb(inpainted_rgb: np.ndarray, mask_u8: Optional[np.ndarray]) -> np.ndarray:
    out = inpainted_rgb.copy()
    if mask_u8 is None or not np.any(mask_u8 > 0):
        return out
    if mask_u8.shape != out.shape[:2]:
        mask_u8 = cv2.resize(mask_u8, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_NEAREST)
    binary = (mask_u8 > 0).astype(np.uint8) * 255
    tint = out.copy()
    tint[binary > 0] = (255, 70, 70)
    cv2.addWeighted(tint, 0.28, out, 0.72, 0, dst=out)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(out, contours, -1, (220, 20, 20), 2, lineType=cv2.LINE_AA)
    return out


def _choose_ruler_ticks(world_0: int, world_span: int, view_px: int, *, min_px_gap: float = 26.0) -> list[int]:
    """Select clean world-coordinate ticks on ``[world_0, world_0 + world_span]``, guaranteeing >=1 tick."""
    span = max(1, int(world_span))
    px_per_world = max(1, int(view_px)) / float(span)

    if span <= 6:
        base_step = 1
    elif span <= 15:
        base_step = 2
    elif span <= 35:
        base_step = 5
    elif span <= 80:
        base_step = 10
    elif span <= 160:
        base_step = 20
    elif span <= 400:
        base_step = 50
    elif span <= 1000:
        base_step = 100
    else:
        base_step = 200

    nice_steps = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)
    step = base_step
    for cand in nice_steps:
        if cand >= base_step and cand * px_per_world >= min_px_gap:
            step = cand
            break
    else:
        step = max(base_step, nice_steps[-1])

    first_w = ((world_0 + step - 1) // step) * step
    ticks = list(range(first_w, world_0 + span + 1, step))
    if not ticks:
        # Short crop did not cross a multiple of `step`: fall back to fine steps or exact crop origin
        for finer in reversed(nice_steps):
            if finer >= step:
                continue
            fw = ((world_0 + finer - 1) // finer) * finer
            cand_ticks = list(range(fw, world_0 + span + 1, finer))
            if cand_ticks:
                if len(cand_ticks) > 1 and finer * px_per_world < min_px_gap:
                    ticks = [cand_ticks[0]]
                else:
                    ticks = cand_ticks
                break
        if not ticks:
            ticks = [world_0]
    return ticks


def _wrap_panel_with_world_rulers(
    crop_rgb: np.ndarray,
    world_x0: int,
    world_y0: int,
    title: str,
    *,
    world_w: Optional[int] = None,
    world_h: Optional[int] = None,
    upscale: int = 1,
    show_grid: bool = True,
) -> np.ndarray:
    """Frame an image crop (at native, upscaled, or pre-downscaled size) with exact world-pixel ``(X, Y)`` rulers."""
    ch_in, cw_in = crop_rgb.shape[:2]
    w_span_x = max(1, int(world_w) if world_w is not None else cw_in)
    w_span_y = max(1, int(world_h) if world_h is not None else ch_in)

    if upscale > 1 and (cw_in, ch_in) == (w_span_x, w_span_y):
        view = cv2.resize(
            crop_rgb,
            (cw_in * upscale, ch_in * upscale),
            interpolation=cv2.INTER_NEAREST if upscale >= 3 else cv2.INTER_CUBIC,
        )
    else:
        view = crop_rgb.copy()
    vh, vw = view.shape[:2]

    eff_sx = vw / float(w_span_x)
    eff_sy = vh / float(w_span_y)

    x_ticks = _choose_ruler_ticks(world_x0, w_span_x, vw, min_px_gap=28.0)
    y_ticks = _choose_ruler_ticks(world_y0, w_span_y, vh, min_px_gap=18.0)

    top_margin = _RULER_TOP_MARGIN
    left_margin = _RULER_LEFT_MARGIN
    right_margin = _RULER_RIGHT_MARGIN
    bot_margin = _RULER_BOT_MARGIN

    framed = np.full((vh + top_margin + bot_margin, vw + left_margin + right_margin, 3), 28, dtype=np.uint8)

    # Draw subtle coordinate grid on `view` if requested
    if show_grid:
        for wx in x_ticks:
            vx = int(round((wx - world_x0) * eff_sx))
            if 0 <= vx < vw:
                view[:, vx] = (view[:, vx].astype(np.uint16) * 3 + np.array([90, 160, 255], dtype=np.uint16)) // 4
        for wy in y_ticks:
            vy = int(round((wy - world_y0) * eff_sy))
            if 0 <= vy < vh:
                view[vy, :] = (view[vy, :].astype(np.uint16) * 3 + np.array([90, 160, 255], dtype=np.uint16)) // 4

    framed[top_margin : top_margin + vh, left_margin : left_margin + vw] = view
    cv2.rectangle(
        framed,
        (left_margin - 1, top_margin - 1),
        (left_margin + vw, top_margin + vh),
        (180, 190, 205),
        1,
    )

    # Title bar
    cv2.putText(
        framed,
        title,
        (left_margin, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (240, 245, 255),
        1,
        cv2.LINE_AA,
    )

    # X ticks
    for wx in x_ticks:
        px = left_margin + min(vw, max(0, int(round((wx - world_x0) * eff_sx))))
        cv2.line(framed, (px, top_margin - 6), (px, top_margin - 1), (200, 210, 225), 1)
        cv2.putText(
            framed,
            str(wx),
            (max(2, px - 14), top_margin - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (195, 210, 230),
            1,
            cv2.LINE_AA,
        )

    # Y ticks
    for wy in y_ticks:
        py = top_margin + min(vh, max(0, int(round((wy - world_y0) * eff_sy))))
        cv2.line(framed, (left_margin - 6, py), (left_margin - 1, py), (200, 210, 225), 1)
        cv2.putText(
            framed,
            str(wy),
            (6, py + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (195, 210, 230),
            1,
            cv2.LINE_AA,
        )

    return framed


def _downscale_max_edge(img_rgb: np.ndarray, max_edge: int) -> np.ndarray:
    if max_edge <= 0:
        return img_rgb
    h, w = img_rgb.shape[:2]
    longest = max(h, w)
    if longest <= max_edge:
        return img_rgb
    scale = max_edge / float(longest)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(img_rgb, (nw, nh), interpolation=cv2.INTER_AREA)


def _compute_target_panel_view_wh(w: int, h: int, *, n_cols: int, max_edge: int) -> tuple[int, int]:
    """Compute pre-scaled panel view ``(vw, vh)`` so the framed ``n_cols`` composite stays within ``max_edge``."""
    if max_edge <= 0:
        return w, h
    unscaled_w = n_cols * (w + _RULER_FRAME_PAD_W)
    unscaled_h = h + _RULER_FRAME_PAD_H
    if max(unscaled_w, unscaled_h) <= max_edge:
        return w, h
    avail_vw = max(1, (max_edge - n_cols * _RULER_FRAME_PAD_W) // max(1, n_cols))
    avail_vh = max(1, max_edge - _RULER_FRAME_PAD_H)
    scale = min(1.0, avail_vw / float(max(1, w)), avail_vh / float(max(1, h)))
    return max(1, int(math.floor(w * scale))), max(1, int(math.floor(h * scale)))


def _resize_if_needed(img_rgb: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    h, w = img_rgb.shape[:2]
    if (w, h) == (target_w, target_h):
        return img_rgb
    interp = cv2.INTER_AREA if (target_w < w or target_h < h) else cv2.INTER_LANCZOS4
    return cv2.resize(img_rgb, (target_w, target_h), interpolation=interp)


def _rendered_panel_source_info(paths: PagePaths) -> tuple[str, str]:
    """Return ``(rendered_panel_source, fallback_short_label)``."""
    if paths.rendered_output_exists:
        return "rendered", ""
    if paths.inpainted_exists:
        return "inpainted_fallback", "fallback: inpainted"
    return "original_fallback", "fallback: original"


def generate_page_inspection_image(
    page_path: str | Path,
    *,
    mode: str = "side_by_side",  # "side_by_side" | "inpaint_check" | "annotated_rendered" | "clean_rendered"
    show_grid: bool = False,
    max_edge: int = 2200,
) -> tuple[Path, dict[str, Any]]:
    """Generate a full-page visual inspection PNG for multimodal Agent review."""
    paths = resolve_page_paths(page_path)
    if not paths.display_base_path.is_file():
        raise FileNotFoundError(f"Base image not found: {paths.display_base_path}")

    base_rgb = open_rgb_image(paths.display_base_path)
    h, w = base_rgb.shape[:2]
    _, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])

    rendered_source, fb_label = _rendered_panel_source_info(paths)

    if mode == "clean_rendered":
        vw, vh = (w, h) if max_edge <= 0 else (
            (w, h)
            if max(w, h) <= max_edge
            else (
                max(1, int(round(w * (max_edge / float(max(w, h)))))),
                max(1, int(round(h * (max_edge / float(max(w, h)))))),
            )
        )
        raw_ren = (
            open_rgb_image(paths.rendered_output_path)
            if paths.rendered_output_exists
            else (open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb)
        )
        composite = _resize_if_needed(raw_ren, vw, vh)
    elif mode == "annotated_rendered":
        vw, vh = _compute_target_panel_view_wh(w, h, n_cols=1, max_edge=max_edge)
        raw_ren = (
            open_rgb_image(paths.rendered_output_path)
            if paths.rendered_output_exists
            else (open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb)
        )
        ren_view = _resize_if_needed(raw_ren, vw, vh)
        annotated = _annotate_regions_on_rgb(
            ren_view,
            regions,
            draw_polygons=True,
            draw_centers=True,
            coord_scale=(vw / float(w), vh / float(h)),
        )
        fb_suffix = f" [{fb_label}]" if fb_label else ""
        composite = _wrap_panel_with_world_rulers(
            annotated,
            0,
            0,
            f"{paths.source_path.name} — Annotated Rendered ({w}x{h}){fb_suffix}",
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
    elif mode == "inpaint_check":
        vw, vh = _compute_target_panel_view_wh(w, h, n_cols=3, max_edge=max_edge)
        sx, sy = vw / float(w), vh / float(h)
        base_view = _resize_if_needed(base_rgb, vw, vh)
        del base_rgb

        raw_inp = open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else None
        inp_view = _resize_if_needed(raw_inp, vw, vh) if raw_inp is not None else base_view.copy()

        if paths.rendered_output_exists:
            ren_view = _resize_if_needed(open_rgb_image(paths.rendered_output_path), vw, vh)
        else:
            ren_view = inp_view.copy()
        del raw_inp

        mask_u8 = decode_mask_base64(image_data.get("mask_raw"), (h, w))
        if mask_u8 is not None and mask_u8.shape != (vh, vw):
            mask_u8 = cv2.resize(mask_u8, (vw, vh), interpolation=cv2.INTER_NEAREST)

        p1 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(base_view, regions, draw_polygons=True, coord_scale=(sx, sy)),
            0,
            0,
            "1. Original + Boxes",
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
        p2 = _wrap_panel_with_world_rulers(
            _overlay_mask_on_rgb(inp_view, mask_u8),
            0,
            0,
            "2. Inpainted + Mask (Red)",
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
        p3_title = "3. Final Rendered" if paths.rendered_output_exists else f"3. Rendered ({fb_label})"
        p3 = _wrap_panel_with_world_rulers(
            ren_view,
            0,
            0,
            p3_title,
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
        composite = np.concatenate([p1, p2, p3], axis=1)
    else:
        # Default: side_by_side (Original + Boxes | Final Rendered + Badges)
        vw, vh = _compute_target_panel_view_wh(w, h, n_cols=2, max_edge=max_edge)
        sx, sy = vw / float(w), vh / float(h)
        base_view = _resize_if_needed(base_rgb, vw, vh)
        raw_ren = (
            open_rgb_image(paths.rendered_output_path)
            if paths.rendered_output_exists
            else (open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb)
        )
        ren_view = _resize_if_needed(raw_ren, vw, vh)
        del base_rgb, raw_ren

        p1 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(base_view, regions, draw_polygons=True, coord_scale=(sx, sy)),
            0,
            0,
            f"Original ({paths.source_path.name})",
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
        p2_title = "Rendered (exists)" if paths.rendered_output_exists else f"Rendered (preview {fb_label})"
        p2 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(ren_view, regions, draw_polygons=False, draw_centers=False, coord_scale=(sx, sy)),
            0,
            0,
            p2_title,
            world_w=w,
            world_h=h,
            show_grid=show_grid,
        )
        composite = np.concatenate([p1, p2], axis=1)

    composite = _downscale_max_edge(composite, max_edge)
    out_file = paths.inspect_dir / f"{paths.source_path.stem}_page_{mode}.png"
    save_rgb_image_atomic(composite, out_file)

    return out_file, {
        "inspection_image_path": str(out_file),
        "mode": mode,
        "page_dimensions_wh": [w, h],
        "region_count": len(regions),
        "rendered_output_exists": paths.rendered_output_exists,
        "rendered_panel_source": rendered_source,
    }


def generate_region_loupe_image(
    page_path: str | Path,
    region_indices: Optional[list[int]] = None,
    *,
    custom_crop_xyxy: Optional[list[float]] = None,
    padding_px: int = 55,
    show_grid: bool = True,
    max_edge: int = 2400,
) -> tuple[Path, dict[str, Any]]:
    """Generate high-resolution zoomed 3-panel strip(s) (`Original | Inpainted+Mask | Rendered`) with exact world `(X, Y)` coordinate rulers."""
    paths = resolve_page_paths(page_path)
    if not paths.display_base_path.is_file():
        raise FileNotFoundError(f"Base image not found: {paths.display_base_path}")

    base_rgb = open_rgb_image(paths.display_base_path)
    h, w = base_rgb.shape[:2]
    _, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])

    if custom_crop_xyxy is not None and len(custom_crop_xyxy) >= 4:
        targets: list[tuple[str, Optional[int], list[float]]] = [
            ("Custom Crop", None, [float(v) for v in custom_crop_xyxy[:4]])
        ]
    else:
        idxs = region_indices if region_indices is not None else list(range(min(len(regions), 6)))
        targets = []
        for i in idxs:
            if 0 <= int(i) < len(regions):
                targets.append((f"Region #{int(i)}", int(i), region_render_world_bbox_xyxy(regions[int(i)])))

    if not targets:
        raise ValueError("No valid region_indices or custom_crop_xyxy to inspect")

    valid_specs: list[tuple[str, Optional[int], list[float], int, int, int, int, int, int, int]] = []
    for label, r_idx, bbox in targets:
        cx1 = max(0, int(math.floor(bbox[0])) - int(padding_px))
        cy1 = max(0, int(math.floor(bbox[1])) - int(padding_px))
        cx2 = min(w, int(math.ceil(bbox[2])) + int(padding_px))
        cy2 = min(h, int(math.ceil(bbox[3])) + int(padding_px))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        cw, ch = cx2 - cx1, cy2 - cy1
        upscale = 2 if max(cw, ch) < 260 else 1
        valid_specs.append((label, r_idx, bbox, cx1, cy1, cx2, cy2, cw, ch, upscale))

    if not valid_specs:
        raise ValueError(
            f"Selected region/crop rectangle(s) do not intersect page bounds [0, 0, {w}, {h}]"
        )

    mask_u8 = decode_mask_base64(image_data.get("mask_raw"), (h, w))
    inpaint_rgb = open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb.copy()
    if inpaint_rgb.shape[:2] != (h, w):
        inpaint_rgb = cv2.resize(inpaint_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)

    rendered_source, fb_label = _rendered_panel_source_info(paths)
    rendered_rgb = open_rgb_image(paths.rendered_output_path) if paths.rendered_output_exists else inpaint_rgb.copy()
    if rendered_rgb.shape[:2] != (h, w):
        rendered_rgb = cv2.resize(rendered_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)

    # Pre-compute target sheet scale BEFORE allocating multi-row framed strips (`F55`)
    n_rows = len(valid_specs)
    max_nom_vw = max(cw * up for *_, cw, ch, up in valid_specs)
    sum_nom_vh = sum(ch * up for *_, cw, ch, up in valid_specs)
    sheet_scale = 1.0
    if max_edge > 0:
        unscaled_sheet_w = 3 * (max_nom_vw + _RULER_FRAME_PAD_W)
        unscaled_sheet_h = sum_nom_vh + n_rows * _RULER_FRAME_PAD_H
        if max(unscaled_sheet_w, unscaled_sheet_h) > max_edge:
            avail_row_vw = max(1, (max_edge - 3 * _RULER_FRAME_PAD_W) // 3)
            avail_sum_vh = max(n_rows, max_edge - n_rows * _RULER_FRAME_PAD_H)
            sheet_scale = min(1.0, avail_row_vw / float(max(1, max_nom_vw)), avail_sum_vh / float(max(1, sum_nom_vh)))

    strips: list[np.ndarray] = []
    crop_records: list[dict[str, Any]] = []
    ren_suffix = "" if paths.rendered_output_exists else f" ({fb_label})"

    for label, r_idx, bbox, cx1, cy1, cx2, cy2, cw, ch, upscale in valid_specs:
        target_vw = max(1, int(math.floor(cw * upscale * sheet_scale)))
        target_vh = max(1, int(math.floor(ch * upscale * sheet_scale)))
        sx_c = target_vw / float(cw)
        sy_c = target_vh / float(ch)
        scale_xy = np.array([[sx_c, sy_c]], dtype=np.float32)

        def _prep_crop(src_arr: np.ndarray) -> np.ndarray:
            sub = src_arr[cy1:cy2, cx1:cx2]
            if (cw, ch) == (target_vw, target_vh):
                return sub.copy()
            if target_vw < cw or target_vh < ch:
                interp = cv2.INTER_AREA
            elif upscale >= 3:
                interp = cv2.INTER_NEAREST
            else:
                interp = cv2.INTER_CUBIC
            return cv2.resize(sub, (target_vw, target_vh), interpolation=interp)

        orig_crop = _prep_crop(base_rgb)
        if r_idx is not None:
            for poly in regions[r_idx].get("lines") or []:
                pts = np.asarray(poly, dtype=np.float32)
                if pts.ndim == 2 and pts.shape[0] >= 3:
                    local_pts = np.rint((pts[:, :2] - np.array([[cx1, cy1]], dtype=np.float32)) * scale_xy).astype(np.int32)
                    cv2.polylines(orig_crop, [local_pts], True, (37, 99, 235), 2, cv2.LINE_AA)

        sub_mask = mask_u8[cy1:cy2, cx1:cx2] if mask_u8 is not None else None
        inp_crop = _overlay_mask_on_rgb(_prep_crop(inpaint_rgb), sub_mask)
        ren_crop = _prep_crop(rendered_rgb)
        if r_idx is not None:
            quad_w = region_perspective_quad_world(regions[r_idx])
            if quad_w is not None:
                q_pts = np.rint(
                    (np.asarray(quad_w, dtype=np.float32)[:, :2] - np.array([[cx1, cy1]], dtype=np.float32)) * scale_xy
                ).astype(np.int32)
                cv2.polylines(ren_crop, [q_pts], True, (6, 182, 212), 2, cv2.LINE_AA)
            rcx, rcy = region_center(regions[r_idx])
            cv2.drawMarker(
                ren_crop,
                (int(round((rcx - cx1) * sx_c)), int(round((rcy - cy1) * sy_c))),
                (16, 185, 129),
                markerType=cv2.MARKER_CROSS,
                markerSize=12,
                thickness=1,
            )

        p_orig = _wrap_panel_with_world_rulers(
            orig_crop, cx1, cy1, f"{label} — Original", world_w=cw, world_h=ch, show_grid=show_grid
        )
        p_inp = _wrap_panel_with_world_rulers(
            inp_crop, cx1, cy1, f"{label} — Inpainted+Mask", world_w=cw, world_h=ch, show_grid=show_grid
        )
        p_ren = _wrap_panel_with_world_rulers(
            ren_crop, cx1, cy1, f"{label} — Rendered{ren_suffix}", world_w=cw, world_h=ch, show_grid=show_grid
        )

        row_strip = np.concatenate([p_orig, p_inp, p_ren], axis=1)
        strips.append(row_strip)
        crop_records.append(
            {
                "label": label,
                "region_index": r_idx,
                "world_crop_xyxy": [cx1, cy1, cx2, cy2],
                "region_bbox_xyxy": bbox,
            }
        )

    max_w = max(s.shape[1] for s in strips)
    padded_strips: list[np.ndarray] = []
    for s in strips:
        if s.shape[1] < max_w:
            pad_right = np.full((s.shape[0], max_w - s.shape[1], 3), 20, dtype=np.uint8)
            s = np.concatenate([s, pad_right], axis=1)
        padded_strips.append(s)

    final_sheet = np.concatenate(padded_strips, axis=0)
    final_sheet = _downscale_max_edge(final_sheet, max_edge)

    suffix_tag = "custom" if custom_crop_xyxy else "_".join(str(r["region_index"]) for r in crop_records[:5])
    out_file = paths.inspect_dir / f"{paths.source_path.stem}_loupe_{suffix_tag}.png"
    save_rgb_image_atomic(final_sheet, out_file)

    return out_file, {
        "loupe_image_path": str(out_file),
        "inspected_crops": crop_records,
        "rendered_output_exists": paths.rendered_output_exists,
        "rendered_panel_source": rendered_source,
        "coordinate_note": "Ruler numbers on the top (X) and left (Y) axes are exact world pixel coordinates on the source page.",
    }
