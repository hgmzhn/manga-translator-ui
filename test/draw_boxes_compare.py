# -*- coding: utf-8 -*-
"""Draw merged-pipeline boxes vs per-stripe solo boxes on the original image."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import torch

from repro_seam_dilution import (
    IMG_PATH, DETECT_SIZE, MIN_EFF, OUT_DIR,
    imread_unicode, load_model, forward_one, crop_pads, extract_boxes, iou,
)
from manga_translator.utils.generic import (
    build_det_rearrange_plan,
    det_rearrange_patch_array,
    det_rearrange_patch_spans,
    det_unrearrange_patch_maps,
    square_pad_resize,
)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    img = imread_unicode(IMG_PATH)
    plan = build_det_rearrange_plan(img, tgt_size=DETECT_SIZE, min_effective_short_side=MIN_EFF)
    h, w = plan["h"], plan["w"]
    pw_num, ph_num, patch_size = plan["pw_num"], plan["ph_num"], plan["patch_size"]
    spans = det_rearrange_patch_spans(plan)

    model = load_model(device)
    patch_arr = det_rearrange_patch_array(plan)
    db_lst = []
    for patch in patch_arr:
        p, _r, pad_h, pad_w = square_pad_resize(patch, tgt_size=DETECT_SIZE)
        db_lst.append(crop_pads(forward_one(model, p, device), pad_h, pad_w, DETECT_SIZE))

    merged = det_unrearrange_patch_maps(db_lst, plan, data_format="chw")
    mH, mW = merged.shape[1], merged.shape[2]

    merged_boxes = extract_boxes(merged[0], h, w)
    print(f"merged boxes: {len(merged_boxes)}")

    packed_pw = db_lst[0].shape[-1] // pw_num
    solo_boxes_all = []
    for pidx in range(ph_num):
        ii, jj = divmod(pidx, pw_num)
        sub = db_lst[ii][0][:, jj * packed_pw:(jj + 1) * packed_pw]
        solo = np.zeros((mH, mW), dtype=np.float32)
        t = int(round(plan["rel_step_list"][pidx] * mH))
        bh, bw = min(sub.shape[0], mH - t), min(sub.shape[1], mW)
        solo[t:t + bh, :bw] = sub[:bh, :bw]
        boxes = extract_boxes(solo, h, w)
        print(f"solo stripe {pidx} (y=[{spans[pidx][0]},{spans[pidx][1]}]): {len(boxes)} boxes")
        solo_boxes_all.extend(boxes)

    missing = [
        (sb, ss) for sb, ss in solo_boxes_all
        if max((iou(sb, mb) for mb, _ in merged_boxes), default=0.0) < 0.3
    ]
    print(f"solo-only (missing from merged): {len(missing)}")

    vis = img.copy()
    # seam lines: yellow dashed-ish (thin solid)
    for i in range(ph_num - 1):
        y = spans[i][1]
        cv2.line(vis, (0, y), (w, y), (255, 220, 0), 2)
    for sb, _s in solo_boxes_all:          # blue thin: per-stripe solo
        cv2.polylines(vis, [sb.astype(np.int32)], True, (40, 90, 255), 2)
    for mb, _s in merged_boxes:            # green: pipeline merged result
        cv2.polylines(vis, [mb.astype(np.int32)], True, (0, 200, 0), 4)
    for sb, _s in missing:                 # red thick: lost by merge
        cv2.polylines(vis, [sb.astype(np.int32)], True, (255, 0, 0), 6)

    full_path = OUT_DIR / "compare_boxes_full.png"
    cv2.imwrite(str(full_path), vis[..., ::-1])
    print(f"saved {full_path}")

    for i in range(ph_num - 1):
        y = spans[i][1]
        t_next = spans[i + 1][0]
        c1, c2 = max(0, t_next - 100), min(h, y + 250)
        crop = vis[c1:c2]
        p = OUT_DIR / f"compare_seam_{i}_{i+1}.png"
        cv2.imwrite(str(p), crop[..., ::-1])
    print("saved seam crops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
