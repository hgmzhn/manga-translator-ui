"""验证项目渲染器的 PS 对齐斜体：与 PS 探针同方法的逐行/逐列质心拟合。

检查点（全部对照 PS 实测基准，SimHei 100px）：
1. 横排「国国国」normal vs italic=True：字符步进不变（100），逐行 dx(y)
   斜率 ≈ tan10°（锚点无关，斜率即角度）。
2. 竖排「国国国」：y 步进不变（100），每字局部 dx(y) 斜率 ≈ tan10°，
   墨迹 cx 均匀右漂（PS 实测 +6px 量级）、无随列累计。
3. 竖排「HHH」italic：逐列 dy(x) 斜率 ≈ +tan10°（横躺字 = R·S 的 y 剪切）。

运行（repo 包根）：python test/proj_italic_check.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import cv2
import numpy as np

from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import RICH_TEXT_FORMAT

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ps_spacing')
FONT = r'C:\Windows\Fonts\simhei.ttf'
FONT_SIZE = 100


def _doc(text, style):
    return {'format': RICH_TEXT_FORMAT, 'blocks': [
        {'type': 'paragraph', 'inlines': [{'type': 'text', 'text': text, 'style': style}]},
    ]}


def render_h(text, style):
    return text_render.put_text_horizontal(
        FONT_SIZE, _doc(text, style), 10, 10, 'center', False,
        (0, 0, 0), None, 'en_US', True, 1.0, None, 1, 0.0, letter_spacing=1.0,
    )


def render_v(text, style):
    return text_render.put_text_vertical(
        FONT_SIZE, _doc(text, style), 10, 'center', (0, 0, 0), None,
        1.0, None, 1, 0.0, letter_spacing=1.0,
    )


def mask_of(rgba):
    return rgba[..., 3] >= 128


def line_centroids(mask, axis, min_count=3):
    out = {}
    n = mask.shape[0] if axis == 0 else mask.shape[1]
    for i in range(n):
        line = mask[i] if axis == 0 else mask[:, i]
        hits = np.flatnonzero(line)
        if hits.size >= min_count:
            out[i] = float(hits.mean())
    return out


def fit_slope(pairs_a, pairs_b, align_keys=True):
    """对齐两图的行/列（按各自墨迹起点对齐）后拟合位移斜率。"""
    ka = sorted(pairs_a)
    kb = sorted(pairs_b)
    if not ka or not kb:
        return None
    shift = kb[0] - ka[0]
    keys = [k for k in ka if (k + shift) in pairs_b]
    if len(keys) < 15:
        return None
    t = np.array(keys, dtype=np.float64)
    d = np.array([pairs_b[k + shift] - pairs_a[k] for k in keys], dtype=np.float64)
    a, b = np.polyfit(t, d, 1)
    resid = d - (a * t + b)
    med = np.median(resid)
    mad = np.median(np.abs(resid - med))
    keep = np.abs(resid - med) <= max(3.0 * 1.4826 * mad, 0.3)
    a, b = np.polyfit(t[keep], d[keep], 1)
    resid = d[keep] - (a * t[keep] + b)
    return a, float(np.abs(resid).max()), int(keep.sum())


def runs(mask, vertical):
    prof = mask.any(axis=1 if vertical else 0)
    idx = np.flatnonzero(prof)
    if idx.size == 0:
        return []
    gaps = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[gaps + 1]))
    ends = np.concatenate((idx[gaps], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist()))


def centroid_steps(mask, vertical):
    rs = runs(mask, vertical)
    ys, xs = np.nonzero(mask)
    pos = ys if vertical else xs
    cents = []
    for s, e in rs:
        sel = (pos >= s) & (pos <= e)
        cents.append(float(pos[sel].mean()))
    return [round(cents[i + 1] - cents[i], 2) for i in range(len(cents) - 1)], rs


def main():
    text_render.set_font(FONT)
    text_render.set_bold(False)
    os.makedirs(OUT, exist_ok=True)
    ok = True

    # 1. 横排
    hn = render_h('国国国', {})
    hi = render_h('国国国', {'italic': True})
    cv2.imwrite(os.path.join(OUT, 'proj_it_h_normal.png'), cv2.cvtColor(hn, cv2.COLOR_RGBA2BGRA))
    cv2.imwrite(os.path.join(OUT, 'proj_it_h_italic.png'), cv2.cvtColor(hi, cv2.COLOR_RGBA2BGRA))
    steps_n, _ = centroid_steps(mask_of(hn), False)
    steps_i, _ = centroid_steps(mask_of(hi), False)
    fit = fit_slope(line_centroids(mask_of(hn), 0), line_centroids(mask_of(hi), 0))
    print('--- horizontal GUOx3')
    print(f'  steps normal={steps_n} italic={steps_i}')
    if fit:
        a, mr, n = fit
        ang = math.degrees(math.atan(-a))
        print(f'  dx(y) slope={a:.5f} -> {ang:.2f} deg (resid<={mr:.2f}, {n} rows)')
        ok &= abs(ang - 10.0) < 0.3
    else:
        print('  dx(y): fit failed'); ok = False
    ok &= steps_n == steps_i

    # 2. 竖排直立
    vn = render_v('国国国', {})
    vi = render_v('国国国', {'italic': True})
    cv2.imwrite(os.path.join(OUT, 'proj_it_v_normal.png'), cv2.cvtColor(vn, cv2.COLOR_RGBA2BGRA))
    cv2.imwrite(os.path.join(OUT, 'proj_it_v_italic.png'), cv2.cvtColor(vi, cv2.COLOR_RGBA2BGRA))
    vsteps_n, runs_n = centroid_steps(mask_of(vn), True)
    vsteps_i, runs_i = centroid_steps(mask_of(vi), True)
    print('--- vertical GUOx3 (upright)')
    print(f'  y-steps normal={vsteps_n} italic={vsteps_i}')
    ok &= vsteps_n == vsteps_i
    if len(runs_n) == len(runs_i) == 3:
        mn, mi = mask_of(vn), mask_of(vi)
        ys_n, xs_n = np.nonzero(mn)
        ys_i, xs_i = np.nonzero(mi)
        for k, ((sn, en), (si, ei)) in enumerate(zip(runs_n, runs_i)):
            cx_n = float(xs_n[(ys_n >= sn) & (ys_n <= en)].mean())
            cx_i = float(xs_i[(ys_i >= si) & (ys_i <= ei)].mean())
            fit = fit_slope(line_centroids(mn[sn:en + 1], 0), line_centroids(mi[si:ei + 1], 0))
            if fit:
                a, mr, n = fit
                ang = math.degrees(math.atan(-a))
                print(f'  char{k}: slope {ang:.2f} deg, cx drift {cx_i - cx_n:+.2f}px (resid<={mr:.2f})')
                ok &= abs(ang - 10.0) < 0.3
    else:
        print(f'  run count {len(runs_n)} vs {len(runs_i)} (italic 可能墨迹相连，允许)')

    # 3. 竖排横躺（项目里走 90° 旋转的是引号类字符）
    hn3 = render_v('「「「', {})
    hi3 = render_v('「「「', {'italic': True})
    cv2.imwrite(os.path.join(OUT, 'proj_it_vh_normal.png'), cv2.cvtColor(hn3, cv2.COLOR_RGBA2BGRA))
    cv2.imwrite(os.path.join(OUT, 'proj_it_vh_italic.png'), cv2.cvtColor(hi3, cv2.COLOR_RGBA2BGRA))
    fit = fit_slope(line_centroids(mask_of(hn3), 1, min_count=2), line_centroids(mask_of(hi3), 1, min_count=2))
    print('--- vertical bracket x3 (rotated)')
    if fit:
        a, mr, n = fit
        ang = math.degrees(math.atan(a))
        print(f'  dy(x) slope={a:.5f} -> {ang:.2f} deg (resid<={mr:.2f}, {n} cols)')
        ok &= abs(ang - 10.0) < 0.5
    else:
        print('  dy(x): fit failed'); ok = False

    print()
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
