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

from .path_resolver import resolve_page_paths
from .project_store import (
    decode_mask_base64,
    load_page_json,
    open_rgb_image,
    region_center,
    region_perspective_quad_world,
    region_render_world_bbox_xyxy,
    save_rgb_image_atomic,
)


def _draw_badge(img_rgb: np.ndarray, text: str, x: int, y: int, bg_rgb: tuple[int, int, int] = (220, 38, 38)) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.58
    thickness = 2
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
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
) -> np.ndarray:
    out = canvas_rgb.copy()
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
                pts = np.rint(np.asarray(poly, dtype=np.float32)).astype(np.int32)
                if pts.ndim == 2 and pts.shape[0] >= 3:
                    cv2.polylines(out, [pts[:, :2]], isClosed=True, color=color, thickness=thick, lineType=cv2.LINE_AA)
            if draw_centers:
                quad_w = region_perspective_quad_world(reg)
                if quad_w is not None:
                    q_pts = np.rint(np.asarray(quad_w, dtype=np.float32)).astype(np.int32)
                    cv2.polylines(out, [q_pts[:, :2]], isClosed=True, color=(6, 182, 212), thickness=thick, lineType=cv2.LINE_AA)

        if draw_centers:
            cx, cy = (int(round(v)) for v in region_center(reg))
            cv2.drawMarker(out, (cx, cy), color, markerType=cv2.MARKER_CROSS, markerSize=14, thickness=2)

        x1, y1, _, _ = (int(round(v)) for v in region_render_world_bbox_xyxy(reg))
        _draw_badge(out, f"#{idx}", x1, y1, bg_rgb=color)
    return out


def _overlay_mask_on_rgb(inpainted_rgb: np.ndarray, mask_u8: Optional[np.ndarray]) -> np.ndarray:
    out = inpainted_rgb.copy()
    if mask_u8 is None or mask_u8.shape != out.shape[:2] or not np.any(mask_u8 > 0):
        return out
    binary = (mask_u8 > 0).astype(np.uint8) * 255
    tint = out.copy()
    tint[binary > 0] = (255, 70, 70)
    cv2.addWeighted(tint, 0.28, out, 0.72, 0, dst=out)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(out, contours, -1, (220, 20, 20), 2, lineType=cv2.LINE_AA)
    return out


def _wrap_panel_with_world_rulers(
    crop_rgb: np.ndarray,
    world_x0: int,
    world_y0: int,
    title: str,
    *,
    upscale: int = 1,
    show_grid: bool = True,
) -> np.ndarray:
    """Frame an image crop with exact world-pixel `(X, Y)` coordinate rulers and optional subtle grid."""
    ch, cw = crop_rgb.shape[:2]
    if upscale > 1:
        view = cv2.resize(crop_rgb, (cw * upscale, ch * upscale), interpolation=cv2.INTER_NEAREST if upscale >= 3 else cv2.INTER_CUBIC)
    else:
        view = crop_rgb.copy()
    vh, vw = view.shape[:2]

    # Choose clean world tick spacing based on world width/height
    span = max(cw, ch)
    if span <= 160:
        step = 20
    elif span <= 400:
        step = 50
    elif span <= 1000:
        step = 100
    else:
        step = 200

    top_margin = 50
    left_margin = 58
    right_margin = 14
    bot_margin = 16

    framed = np.full((vh + top_margin + bot_margin, vw + left_margin + right_margin, 3), 28, dtype=np.uint8)

    # Draw subtle coordinate grid on `view` if requested
    if show_grid:
        first_wx = ((world_x0 + step - 1) // step) * step
        for wx in range(first_wx, world_x0 + cw, step):
            vx = int(round((wx - world_x0) * upscale))
            if 0 <= vx < vw:
                view[:, vx] = (view[:, vx].astype(np.uint16) * 3 + np.array([90, 160, 255], dtype=np.uint16)) // 4
        first_wy = ((world_y0 + step - 1) // step) * step
        for wy in range(first_wy, world_y0 + ch, step):
            vy = int(round((wy - world_y0) * upscale))
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
    first_wx = ((world_x0 + step - 1) // step) * step
    for wx in range(first_wx, world_x0 + cw + 1, step):
        px = left_margin + int(round((wx - world_x0) * upscale))
        if left_margin <= px <= left_margin + vw:
            cv2.line(framed, (px, top_margin - 6), (px, top_margin - 1), (200, 210, 225), 1)
            cv2.putText(
                framed,
                str(wx),
                (px - 14, top_margin - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (195, 210, 230),
                1,
                cv2.LINE_AA,
            )

    # Y ticks
    first_wy = ((world_y0 + step - 1) // step) * step
    for wy in range(first_wy, world_y0 + ch + 1, step):
        py = top_margin + int(round((wy - world_y0) * upscale))
        if top_margin <= py <= top_margin + vh:
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

    rendered_rgb = (
        open_rgb_image(paths.rendered_output_path)
        if paths.rendered_output_exists
        else (open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb.copy())
    )
    if rendered_rgb.shape[:2] != (h, w):
        rendered_rgb = cv2.resize(rendered_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)

    if mode == "clean_rendered":
        composite = rendered_rgb
    elif mode == "annotated_rendered":
        annotated = _annotate_regions_on_rgb(rendered_rgb, regions, draw_polygons=True, draw_centers=True)
        composite = _wrap_panel_with_world_rulers(
            annotated, 0, 0, f"{paths.source_path.name} — Annotated Rendered ({w}x{h})", show_grid=show_grid
        )
    elif mode == "inpaint_check":
        inpaint_rgb = open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb.copy()
        if inpaint_rgb.shape[:2] != (h, w):
            inpaint_rgb = cv2.resize(inpaint_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)
        mask_u8 = decode_mask_base64(image_data.get("mask_raw"), (h, w))
        p1 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(base_rgb, regions, draw_polygons=True),
            0,
            0,
            "1. Original + Boxes",
            show_grid=show_grid,
        )
        p2 = _wrap_panel_with_world_rulers(
            _overlay_mask_on_rgb(inpaint_rgb, mask_u8),
            0,
            0,
            "2. Inpainted + Mask (Red)",
            show_grid=show_grid,
        )
        p3 = _wrap_panel_with_world_rulers(
            rendered_rgb,
            0,
            0,
            "3. Final Rendered",
            show_grid=show_grid,
        )
        composite = np.concatenate([p1, p2, p3], axis=1)
    else:
        # Default: side_by_side (Original + Boxes | Final Rendered + Badges)
        p1 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(base_rgb, regions, draw_polygons=True),
            0,
            0,
            f"Original ({paths.source_path.name})",
            show_grid=show_grid,
        )
        p2 = _wrap_panel_with_world_rulers(
            _annotate_regions_on_rgb(rendered_rgb, regions, draw_polygons=False, draw_centers=False),
            0,
            0,
            f"Rendered ({'exists' if paths.rendered_output_exists else 'preview fallback'})",
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
    }


def generate_region_loupe_image(
    page_path: str | Path,
    region_indices: Optional[list[int]] = None,
    *,
    custom_crop_xyxy: Optional[list[float]] = None,
    padding_px: int = 55,
    show_grid: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Generate high-resolution zoomed 3-panel strip(s) (`Original | Inpainted+Mask | Rendered`) with exact world `(X, Y)` coordinate rulers."""
    paths = resolve_page_paths(page_path)
    if not paths.display_base_path.is_file():
        raise FileNotFoundError(f"Base image not found: {paths.display_base_path}")

    base_rgb = open_rgb_image(paths.display_base_path)
    h, w = base_rgb.shape[:2]
    _, image_data = load_page_json(paths)
    regions = image_data.get("regions", [])
    mask_u8 = decode_mask_base64(image_data.get("mask_raw"), (h, w))

    inpaint_rgb = open_rgb_image(paths.inpainted_path) if paths.inpainted_exists else base_rgb.copy()
    if inpaint_rgb.shape[:2] != (h, w):
        inpaint_rgb = cv2.resize(inpaint_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)

    rendered_rgb = open_rgb_image(paths.rendered_output_path) if paths.rendered_output_exists else inpaint_rgb.copy()
    if rendered_rgb.shape[:2] != (h, w):
        rendered_rgb = cv2.resize(rendered_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)

    inpaint_with_mask = _overlay_mask_on_rgb(inpaint_rgb, mask_u8)

    strips: list[np.ndarray] = []
    crop_records: list[dict[str, Any]] = []

    if custom_crop_xyxy is not None and len(custom_crop_xyxy) >= 4:
        targets = [("Custom Crop", None, [float(v) for v in custom_crop_xyxy[:4]])]
    else:
        idxs = region_indices if region_indices is not None else list(range(min(len(regions), 6)))
        targets = []
        for i in idxs:
            if 0 <= int(i) < len(regions):
                targets.append((f"Region #{int(i)}", int(i), region_render_world_bbox_xyxy(regions[int(i)])))

    if not targets:
        raise ValueError("No valid region_indices or custom_crop_xyxy to inspect")

    for label, r_idx, bbox in targets:
        cx1 = max(0, int(math.floor(bbox[0])) - int(padding_px))
        cy1 = max(0, int(math.floor(bbox[1])) - int(padding_px))
        cx2 = min(w, int(math.ceil(bbox[2])) + int(padding_px))
        cy2 = min(h, int(math.ceil(bbox[3])) + int(padding_px))
        if cx2 <= cx1 or cy2 <= cy1:
            continue

        cw, ch = cx2 - cx1, cy2 - cy1
        upscale = 2 if max(cw, ch) < 260 else 1

        orig_crop = base_rgb[cy1:cy2, cx1:cx2].copy()
        if r_idx is not None:
            for poly in regions[r_idx].get("lines") or []:
                pts = np.rint(np.asarray(poly, dtype=np.float32)).astype(np.int32)
                if pts.ndim == 2 and pts.shape[0] >= 3:
                    local_pts = pts[:, :2] - np.array([[cx1, cy1]], dtype=np.int32)
                    cv2.polylines(orig_crop, [local_pts], True, (37, 99, 235), 2, cv2.LINE_AA)

        inp_crop = inpaint_with_mask[cy1:cy2, cx1:cx2].copy()
        ren_crop = rendered_rgb[cy1:cy2, cx1:cx2].copy()
        if r_idx is not None:
            quad_w = region_perspective_quad_world(regions[r_idx])
            if quad_w is not None:
                q_pts = np.rint(np.asarray(quad_w, dtype=np.float32)).astype(np.int32) - np.array([[cx1, cy1]], dtype=np.int32)
                cv2.polylines(ren_crop, [q_pts], True, (6, 182, 212), 2, cv2.LINE_AA)
            rcx, rcy = region_center(regions[r_idx])
            cv2.drawMarker(
                ren_crop,
                (int(round(rcx)) - cx1, int(round(rcy)) - cy1),
                (16, 185, 129),
                markerType=cv2.MARKER_CROSS,
                markerSize=12,
                thickness=1,
            )

        p_orig = _wrap_panel_with_world_rulers(orig_crop, cx1, cy1, f"{label} — Original", upscale=upscale, show_grid=show_grid)
        p_inp = _wrap_panel_with_world_rulers(inp_crop, cx1, cy1, f"{label} — Inpainted+Mask", upscale=upscale, show_grid=show_grid)
        p_ren = _wrap_panel_with_world_rulers(ren_crop, cx1, cy1, f"{label} — Rendered", upscale=upscale, show_grid=show_grid)

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
    final_sheet = _downscale_max_edge(final_sheet, 2400)

    suffix_tag = "custom" if custom_crop_xyxy else "_".join(str(r["region_index"]) for r in crop_records[:5])
    out_file = paths.inspect_dir / f"{paths.source_path.stem}_loupe_{suffix_tag}.png"
    save_rgb_image_atomic(final_sheet, out_file)

    return out_file, {
        "loupe_image_path": str(out_file),
        "inspected_crops": crop_records,
        "coordinate_note": "Ruler numbers on the top (X) and left (Y) axes are exact world pixel coordinates on the source page.",
    }
