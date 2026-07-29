"""实测 Photoshop 仿斜体（faux italic）的倾斜角与剪切原点。

前置：先用 Photoshop 跑 test/ps_italic_probe.jsx 生成 test/ps_spacing/ps_it_*.png
（基线在 y=400，字号 100px，SimHei「国」）。

方法：同一字形的正常版与仿斜体版逐行求墨迹质心，行位移 dx(y) 做最小二乘
线性拟合：dx = a*y + b。倾斜角 = atan(-a)（图像 y 向下，顶端右倾为正），
dx=0 的行 y0 = -b/a 即剪切原点（对比已知基线 y=400 判断是否绕基线剪切）。
另用三连字测仿斜体是否改变 advance。

运行（repo 包根）：python test/ps_italic_angle.py
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
BASELINE_Y = 400


def ink_mask(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return gray < 128


def row_centroids(mask, min_count=3):
    out = {}
    for y in range(mask.shape[0]):
        xs = np.flatnonzero(mask[y])
        if xs.size >= min_count:
            out[y] = float(xs.mean())
    return out


def fit_shear(normal, italic):
    ys = sorted(set(normal) & set(italic))
    if len(ys) < 20:
        raise RuntimeError(f'too few overlapping rows: {len(ys)}')
    ys_arr = np.array(ys, dtype=np.float64)
    dx = np.array([italic[y] - normal[y] for y in ys], dtype=np.float64)
    a, b = np.polyfit(ys_arr, dx, 1)
    resid = dx - (a * ys_arr + b)
    # 二次拟合：MAD 剔除 AA 边缘行的离群残差后重拟合
    med = np.median(resid)
    mad = np.median(np.abs(resid - med))
    keep = np.abs(resid - med) <= max(3.0 * 1.4826 * mad, 0.3)
    a, b = np.polyfit(ys_arr[keep], dx[keep], 1)
    resid = dx[keep] - (a * ys_arr[keep] + b)
    angle_deg = math.degrees(math.atan(-a))
    y0 = -b / a if a != 0 else float('nan')
    return angle_deg, a, y0, float(np.abs(resid).max()), int(keep.sum())


def h_steps(mask):
    prof = mask.any(axis=0)
    idx = np.flatnonzero(prof)
    gaps = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[gaps + 1]))
    ends = np.concatenate((idx[gaps], [idx[-1]]))
    ys, xs = np.nonzero(mask)
    cents = []
    for s, e in zip(starts, ends):
        sel = (xs >= s) & (xs <= e)
        cents.append(float(xs[sel].mean()))
    return [round(cents[i + 1] - cents[i], 2) for i in range(len(cents) - 1)]


def main():
    for label, normal_png, italic_png in [
        ('GUO@100px', 'ps_it_normal.png', 'ps_it_italic.png'),
        ('H@200px', 'ps_it2_normal.png', 'ps_it2_italic.png'),
    ]:
        normal = row_centroids(ink_mask(os.path.join(OUT, normal_png)))
        italic = row_centroids(ink_mask(os.path.join(OUT, italic_png)))
        angle, slope, y0, max_resid, n_rows = fit_shear(normal, italic)
        print(f'--- {label}: rows used {n_rows}, max fit residual {max_resid:.2f} px')
        print(f'  shear slope dx/dy = {slope:.5f}  ->  angle = {angle:.2f} deg (top leans right)')
        print(f'  tan(angle) = {math.tan(math.radians(angle)):.4f}')
        print(f'  dx=0 at y = {y0:.1f} (baseline was set at y={BASELINE_Y})')

    steps = h_steps(ink_mask(os.path.join(OUT, 'ps_it_row.png')))
    print(f'faux-italic 3-char row steps = {steps} (normal advance would be 100.0)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
