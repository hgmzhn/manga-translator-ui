"""对比 Photoshop 与项目 text_render 的默认字距（advance 步进）。

前置：先用 Photoshop 跑 test/ps_spacing_ps_script.jsx 生成 test/ps_spacing/ps_*.png。
本脚本：用项目渲染器渲染同字体（SimHei）同字号（100px）同文本，
按墨迹投影切出每个字形，比较相邻字形的质心步进（= advance + 默认字距）。

运行（repo 包根）：python test/ps_font_spacing.py
"""
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

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ps_spacing')
FONT = r'C:\Windows\Fonts\simhei.ttf'
FONT_SIZE = 100

CASES = [
    ('h_cjk', 'h', '国国国国国'),
    ('h_latin', 'h', 'HHHHH'),
    ('v_cjk', 'v', '国国国国国'),
]


def render_project(kind, text):
    if kind == 'h':
        return text_render.put_text_horizontal(
            FONT_SIZE, text, 10, 10, 'center', False,
            (0, 0, 0), None, 'en_US', True, 1.0, None, 1,
            0.07, letter_spacing=1.0,
        )
    return text_render.put_text_vertical(
        FONT_SIZE, text, 10, 'center', (0, 0, 0), None,
        1.0, None, 1, 0.07, letter_spacing=1.0,
    )


def ink_mask(img):
    """PS 导出是白底 RGB，项目输出是 RGBA；统一成半覆盖阈值的墨迹掩码。"""
    if img.ndim == 3 and img.shape[2] == 4:
        return img[..., 3] >= 128
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return gray < 128


def glyph_runs(mask, vertical):
    """沿书写方向做投影，按空隙切成每字形一段，返回 [(start, end, centroid)]。"""
    axis = 1 if vertical else 0
    prof = mask.any(axis=0 if not vertical else 1)
    idx = np.flatnonzero(prof)
    if idx.size == 0:
        return []
    gaps = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[gaps + 1]))
    ends = np.concatenate((idx[gaps], [idx[-1]]))
    runs = []
    coords = np.nonzero(mask)
    pos = coords[0] if vertical else coords[1]
    for s, e in zip(starts, ends):
        sel = (pos >= s) & (pos <= e)
        centroid = float(pos[sel].mean())
        runs.append((int(s), int(e), centroid))
    return runs


def describe(runs):
    steps = [round(runs[i + 1][2] - runs[i][2], 2) for i in range(len(runs) - 1)]
    widths = [r[1] - r[0] + 1 for r in runs]
    return steps, widths


def main():
    text_render.set_font(FONT)
    text_render.set_bold(False)
    os.makedirs(OUT, exist_ok=True)

    failures = []
    for name, kind, text in CASES:
        rgba = render_project(kind, text)
        if rgba is None:
            print(f'{name}: project render returned None')
            failures.append(name)
            continue
        cv2.imwrite(os.path.join(OUT, f'proj_{name}.png'),
                    cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        proj_runs = glyph_runs(ink_mask(rgba), vertical=(kind == 'v'))

        ps_path = os.path.join(OUT, f'ps_{name}.png')
        if not os.path.exists(ps_path):
            print(f'{name}: missing {ps_path} (run the JSX first)')
            failures.append(name)
            continue
        ps_img = cv2.imread(ps_path, cv2.IMREAD_UNCHANGED)
        ps_runs = glyph_runs(ink_mask(ps_img), vertical=(kind == 'v'))

        proj_steps, proj_widths = describe(proj_runs)
        ps_steps, ps_widths = describe(ps_runs)
        print(f'--- {name} ({len(ps_runs)} vs {len(proj_runs)} glyph runs)')
        print(f'  PS   steps={ps_steps} widths={ps_widths}')
        print(f'  proj steps={proj_steps} widths={proj_widths}')
        if len(ps_steps) != len(proj_steps) or not ps_steps:
            print('  VERDICT: run count mismatch, cannot compare')
            failures.append(name)
            continue
        diffs = [round(a - b, 2) for a, b in zip(ps_steps, proj_steps)]
        max_diff = max(abs(d) for d in diffs)
        print(f'  step diff (PS - proj) = {diffs}, max |diff| = {max_diff}')
        if max_diff <= 1.0:
            print('  VERDICT: EQUAL (within 1px)')
        else:
            print('  VERDICT: DIFFERENT')
            failures.append(name)
    print()
    if failures:
        print('cases not equal / not comparable:', ', '.join(failures))
        return 1
    print('all cases: default spacing equal (within 1px)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
