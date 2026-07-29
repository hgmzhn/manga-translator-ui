"""实测 Photoshop 竖排仿斜体的变换语义（直立 CJK 与横躺拉丁两组）。

前置：先跑 test/ps_vitalic_probe.jsx 与 test/ps_vitalic2_probe.jsx 生成
test/ps_spacing/ps_vit_*.png / ps_vith_*.png（竖排锚点 [350,150]，100px SimHei）。

分析（每组）：
1) 单字 normal vs italic：逐行 dx(y) 拟合（水平剪切分量与轴）、
   逐列 dy(x) 拟合（垂直剪切分量与轴）。
2) 三连列：按 y 投影切字，每字质心（漂移是否累计）、y 步进、每字局部 dx(y)。

运行（repo 包根）：python test/ps_vitalic_angle.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import cv2
import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ps_spacing')


def ink_mask(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return gray < 128


def line_centroids(mask, axis, min_count=3):
    """axis=0: 逐行 x 质心 {y: cx}；axis=1: 逐列 y 质心 {x: cy}。"""
    out = {}
    n = mask.shape[0] if axis == 0 else mask.shape[1]
    for i in range(n):
        line = mask[i] if axis == 0 else mask[:, i]
        hits = np.flatnonzero(line)
        if hits.size >= min_count:
            out[i] = float(hits.mean())
    return out


def robust_fit(pairs_a, pairs_b, min_keys=15):
    keys = sorted(set(pairs_a) & set(pairs_b))
    if len(keys) < min_keys:
        return None
    t = np.array(keys, dtype=np.float64)
    d = np.array([pairs_b[k] - pairs_a[k] for k in keys], dtype=np.float64)
    a, b = np.polyfit(t, d, 1)
    resid = d - (a * t + b)
    med = np.median(resid)
    mad = np.median(np.abs(resid - med))
    keep = np.abs(resid - med) <= max(3.0 * 1.4826 * mad, 0.3)
    a, b = np.polyfit(t[keep], d[keep], 1)
    resid = d[keep] - (a * t[keep] + b)
    zero_at = -b / a if a != 0 else float('nan')
    return a, b, zero_at, float(np.abs(resid).max()), int(keep.sum()), float(np.mean(d))


def v_runs(mask):
    prof = mask.any(axis=1)
    idx = np.flatnonzero(prof)
    gaps = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[gaps + 1]))
    ends = np.concatenate((idx[gaps], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist()))


def analyze(prefix, label):
    n1 = ink_mask(os.path.join(OUT, f'ps_{prefix}_normal1.png'))
    i1 = ink_mask(os.path.join(OUT, f'ps_{prefix}_italic1.png'))

    fit_x = robust_fit(line_centroids(n1, 0), line_centroids(i1, 0))
    fit_y = robust_fit(line_centroids(n1, 1), line_centroids(i1, 1))
    print(f'--- {label}: single glyph')
    if fit_x:
        a, b, zero_at, mr, n, mean_d = fit_x
        ang = math.degrees(math.atan(-a))
        print(f'  dx(y): slope={a:.5f} -> x-shear angle {ang:.2f} deg, dx=0 at y={zero_at:.1f}, '
              f'mean dx={mean_d:.2f}, resid<={mr:.2f} ({n} rows)')
    else:
        print('  dx(y): not enough rows')
    if fit_y:
        a, b, zero_at, mr, n, mean_d = fit_y
        ang = math.degrees(math.atan(a))
        print(f'  dy(x): slope={a:.5f} -> y-shear angle {ang:.2f} deg, dy=0 at x={zero_at:.1f}, '
              f'mean dy={mean_d:.2f}, resid<={mr:.2f} ({n} cols)')
    else:
        print('  dy(x): not enough cols')
    ys_n, xs_n = np.nonzero(n1)
    ys_i, xs_i = np.nonzero(i1)
    print(f'  ink bbox normal: x[{xs_n.min()},{xs_n.max()}] y[{ys_n.min()},{ys_n.max()}]')
    print(f'  ink bbox italic: x[{xs_i.min()},{xs_i.max()}] y[{ys_i.min()},{ys_i.max()}]')

    n3 = ink_mask(os.path.join(OUT, f'ps_{prefix}_normal3.png'))
    i3 = ink_mask(os.path.join(OUT, f'ps_{prefix}_italic3.png'))
    runs_n, runs_i = v_runs(n3), v_runs(i3)
    print(f'--- {label}: 3-char column ({len(runs_n)} vs {len(runs_i)} runs)')
    for tag, mask, runs in (('normal', n3, runs_n), ('italic', i3, runs_i)):
        ys, xs = np.nonzero(mask)
        info = []
        for s, e in runs:
            sel = (ys >= s) & (ys <= e)
            info.append((round(float(ys[sel].mean()), 1), round(float(xs[sel].mean()), 1)))
        steps = [round(info[k + 1][0] - info[k][0], 1) for k in range(len(info) - 1)]
        print(f'  {tag}: per-char (cy, cx)={info} y-steps={steps}')
    if len(runs_n) == len(runs_i):
        for k, ((sn, en), (si, ei)) in enumerate(zip(runs_n, runs_i)):
            fit = robust_fit(line_centroids(n3[sn:en + 1], 0), line_centroids(i3[si:ei + 1], 0))
            if fit:
                a, b, zero_at, mr, n, mean_d = fit
                ang = math.degrees(math.atan(-a))
                print(f'  char{k}: local dx(y) slope={a:.5f} ({ang:.2f} deg), '
                      f'dx=0 at local y={zero_at:.1f} (char span {en - sn + 1}px), mean dx={mean_d:.2f}')


def main():
    analyze('vit', 'upright CJK (GUO)')
    analyze('vith', 'rotated latin (H)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
