# -*- coding: utf-8 -*-
"""Reproduce long-image rearrange seam dilution for the default detector.

Runs the exact rearrange -> per-patch forward -> merge(average) -> box extraction
pipeline, then compares against per-stripe solo extraction to find boxes that
the merge step loses. Prints ASCII only (GBK console safe).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import torch

IMG_PATH = r"D:\xiazai\图片助手(ImageAssistant)_批量图片下载器\4980C0B8E19EB347770340E0AD736FF0.jpg"
DETECT_SIZE = 2048
TEXT_THR = 0.5
BOX_THR = 0.7
UNCLIP = 2.3
MIN_EFF = 341.0
OUT_DIR = ROOT / "test" / "out_seam"

from manga_translator.utils.generic import (  # noqa: E402
    build_det_rearrange_plan,
    det_rearrange_patch_array,
    det_rearrange_patch_spans,
    det_unrearrange_patch_maps,
    square_pad_resize,
)
from manga_translator.detection.default_utils import dbnet_utils  # noqa: E402
from manga_translator.detection.default_utils.DBNet_resnet34 import (  # noqa: E402
    TextDetection,
)


def imread_unicode(path):
    buf = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    assert img is not None, "failed to read image"
    return img[..., ::-1].copy()  # BGR -> RGB


def load_model(device):
    model = TextDetection()
    ckpt = ROOT / "models" / "detection" / "detect-20241225.ckpt"
    sd = torch.load(str(ckpt), map_location="cpu")
    model.load_state_dict(sd["model"] if "model" in sd else sd)
    model.eval()
    return model.to(device)


def forward_one(model, patch, device):
    batch = patch[None].astype(np.float32) / 127.5 - 1.0
    batch = torch.from_numpy(batch).permute(0, 3, 1, 2).to(device)
    with torch.no_grad():
        db, mask = model(batch)
        db = db.sigmoid().cpu().numpy()
    return db[0]  # (C, h, w)


def crop_pads(d, pad_h, pad_w, tgt):
    if pad_h > 0:
        p = int(d.shape[-2] / tgt * pad_h)
        if p > 0:
            d = d[..., :-p, :]
    if pad_w > 0:
        p = int(d.shape[-1] / tgt * pad_w)
        if p > 0:
            d = d[..., :, :-p]
    return d


def extract_boxes(db_map_2d, dest_h, dest_w, box_thr=BOX_THR):
    det = dbnet_utils.SegDetectorRepresenter(TEXT_THR, box_thr, unclip_ratio=UNCLIP)
    pred = db_map_2d[None, None]
    boxes, scores = det({"shape": [(dest_h, dest_w)]}, pred)
    boxes, scores = boxes[0], scores[0]
    out = []
    for b, s in zip(boxes, scores):
        b = np.asarray(b)
        if b.reshape(-1).sum() <= 0:
            continue
        out.append((b.astype(np.float64), float(s)))
    return out


def aabb(box):
    xs, ys = box[:, 0], box[:, 1]
    return xs.min(), ys.min(), xs.max(), ys.max()


def iou(b1, b2):
    x1a, y1a, x2a, y2a = aabb(b1)
    x1b, y1b, x2b, y2b = aabb(b2)
    ix = max(0.0, min(x2a, x2b) - max(x1a, x1b))
    iy = max(0.0, min(y2a, y2b) - max(y1a, y1b))
    inter = ix * iy
    a1 = (x2a - x1a) * (y2a - y1a)
    a2 = (x2b - x1b) * (y2b - y1b)
    return inter / max(a1 + a2 - inter, 1e-6)


def box_mean_score(map2d, box_pts):
    h, w = map2d.shape
    pts = box_pts.copy()
    xmin = int(np.clip(np.floor(pts[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(pts[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(pts[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(pts[:, 1].max()), 0, h - 1))
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    pts[:, 0] -= xmin
    pts[:, 1] -= ymin
    cv2.fillPoly(mask, pts.reshape(1, -1, 2).astype(np.int32), 1)
    return cv2.mean(map2d[ymin:ymax + 1, xmin:xmax + 1], mask)[0]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    img = imread_unicode(IMG_PATH)
    H, W = img.shape[:2]
    print(f"image: {W}x{H}")

    plan = build_det_rearrange_plan(img, tgt_size=DETECT_SIZE, min_effective_short_side=MIN_EFF)
    if plan is None:
        print("plan is None -> rearrange not triggered, nothing to reproduce")
        return 1
    h, w = plan["h"], plan["w"]
    pw_num, ph_num, patch_size = plan["pw_num"], plan["ph_num"], plan["patch_size"]
    spans = det_rearrange_patch_spans(plan)
    print(f"plan: transpose={plan['transpose']} h={h} w={w} pw_num={pw_num} "
          f"ph_num={ph_num} patch_size={patch_size} p_num={plan['p_num']} pad_num={plan['pad_num']}")
    for i, (t, b) in enumerate(spans):
        ov = ""
        if i + 1 < len(spans):
            ov = f" overlap_with_next={b - spans[i + 1][0]}px"
        print(f"  stripe {i}: y=[{t}, {b}]{ov}")

    model = load_model(device)
    patch_arr = det_rearrange_patch_array(plan)

    db_lst = []
    for i, patch in enumerate(patch_arr):
        p, _r, pad_h, pad_w = square_pad_resize(patch, tgt_size=DETECT_SIZE)
        d = forward_one(model, p, device)
        d = crop_pads(d, pad_h, pad_w, DETECT_SIZE)
        db_lst.append(d)
        print(f"patch {i}: input={p.shape[1]}x{p.shape[0]} db={d.shape[2]}x{d.shape[1]}")

    merged = det_unrearrange_patch_maps(db_lst, plan, data_format="chw")
    mH, mW = merged.shape[1], merged.shape[2]
    scale = mH / h
    print(f"merged map: {mW}x{mH} scale={scale:.4f}")

    # Pipeline-equivalent extraction on the merged map (dest = original size).
    merged_boxes = extract_boxes(merged[0], h, w)
    print(f"pipeline(merged) boxes: {len(merged_boxes)}")

    # Solo view per stripe: place each stripe's own db into original coords, weight 1.
    packed_pw = db_lst[0].shape[-1] // pw_num
    stripe_scale_y = db_lst[0].shape[-2] / patch_size
    solo_maps = {}
    for pidx in range(ph_num):
        ii, jj = divmod(pidx, pw_num)
        d = db_lst[ii][0]  # prob channel
        sub = d[:, jj * packed_pw:(jj + 1) * packed_pw]
        solo = np.zeros((mH, mW), dtype=np.float32)
        t = int(round(plan["rel_step_list"][pidx] * mH))
        bh = min(sub.shape[0], mH - t)
        bw = min(sub.shape[1], mW)
        solo[t:t + bh, :bw] = sub[:bh, :bw]
        solo_maps[pidx] = solo

    # Extract boxes from the LAST stripe's solo map (this is what "detect the
    # patch standalone" effectively sees for that stripe).
    last = ph_num - 1
    solo_boxes = extract_boxes(solo_maps[last], h, w)
    t_last, b_last = spans[last]
    print(f"solo(stripe {last}) boxes: {len(solo_boxes)} (stripe y=[{t_last},{b_last}])")

    # Find solo boxes with no counterpart in merged extraction.
    missing = []
    for sb, ss in solo_boxes:
        best = max((iou(sb, mb) for mb, _ in merged_boxes), default=0.0)
        if best < 0.3:
            missing.append((sb, ss, best))
    print(f"boxes found solo but MISSING from merged extraction: {len(missing)}")

    prev_b = spans[last - 1][1] if last >= 1 else t_last  # bottom of previous stripe
    for k, (sb, ss, best) in enumerate(missing):
        x1, y1, x2, y2 = aabb(sb)
        pts_m = sb.copy() * scale
        m_score = box_mean_score(merged[0], pts_m)
        s_score = box_mean_score(solo_maps[last], pts_m.copy())
        prev_score = box_mean_score(solo_maps[last - 1], pts_m.copy()) if last >= 1 else 0.0
        in_overlap = y1 < prev_b
        print(f"  MISSING[{k}] y=[{int(y1)},{int(y2)}] x=[{int(x1)},{int(x2)}] "
              f"solo_score={ss:.3f} merged_region_mean={m_score:.3f} "
              f"lastview_mean={s_score:.3f} prevview_mean={prev_score:.3f} "
              f"in_overlap_band={in_overlap} (prev stripe bottom={prev_b})")

    # Also check every stripe pair overlap band for dilution stats.
    for i in range(ph_num - 1):
        t2, b1 = spans[i + 1][0], spans[i][1]
        T, B = int(round(t2 * scale)), int(round(b1 * scale))
        a = solo_maps[i][T:B]
        b = solo_maps[i + 1][T:B]
        m = merged[0][T:B]
        strong = b > TEXT_THR  # text per the later stripe's view
        if strong.sum() == 0:
            continue
        print(f"  seam {i}/{i+1}: overlap_y=[{t2},{b1}] strong_px={int(strong.sum())} "
              f"later_view_mean={float(b[strong].mean()):.3f} "
              f"earlier_view_mean={float(a[strong].mean()):.3f} "
              f"merged_mean={float(m[strong].mean()):.3f}")

    # Save visual evidence around each missing box.
    vis_full = img.copy() if not plan["transpose"] else np.transpose(img, (1, 0, 2)).copy()
    for mb, _ in merged_boxes:
        cv2.polylines(vis_full, [mb.astype(np.int32)], True, (0, 200, 0), 3)
    for sb, _s, _b in missing:
        cv2.polylines(vis_full, [sb.astype(np.int32)], True, (255, 0, 0), 3)
    for k, (sb, _s, _b) in enumerate(missing):
        x1, y1, x2, y2 = [int(v) for v in aabb(sb)]
        pad = 200
        cy1, cy2 = max(0, y1 - pad), min(h, y2 + pad)
        crop = vis_full[cy1:cy2, :]
        cv2.imwrite(str(OUT_DIR / f"missing_{k}_ctx.png"), crop[..., ::-1])
        # heatmap strip: prev view / last view / merged
        T, B = int(round(cy1 * scale)), int(round(cy2 * scale))
        hm = np.concatenate([
            solo_maps[last - 1][T:B] if last >= 1 else np.zeros((B - T, mW), np.float32),
            solo_maps[last][T:B],
            merged[0][T:B],
        ], axis=1)
        cv2.imwrite(str(OUT_DIR / f"missing_{k}_maps.png"),
                    np.clip(hm * 255, 0, 255).astype(np.uint8))
    print(f"saved visuals to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
