"""渲染 golden 基准：dump / 对比 put_text_* 像素输出与 calc_box_from_font 测量。

用途：text_render 重构的回归安全网。重构前 --dump 存基线，重构各阶段 --check 对比。

运行（repo 包根）：
    PYTHONUTF8=1 python test/render_golden.py --dump    # 生成基线到 test/golden/
    PYTHONUTF8=1 python test/render_golden.py --check   # 与基线逐像素对比
    PYTHONUTF8=1 python test/render_golden.py --check --save-diff  # 差异另存可视化 PNG

确定性条件：默认字体 Arial-Unicode、bold=False、显式 stroke_width、offscreen。
基线目录 test/golden/ 不进 git（test/ 本身被忽略），本机回归用。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np

from manga_translator.config import Config
from manga_translator.rendering import calc_box_from_font
from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import RICH_TEXT_FORMAT

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden')

FONT_SIZE = 48


def _rich(blocks):
    return {'format': RICH_TEXT_FORMAT, 'blocks': blocks}


def _para(*inlines):
    return {'type': 'paragraph', 'inlines': list(inlines)}


def _text(t, style=None):
    return {'type': 'text', 'text': t, 'style': style or {}}


# (name, kind, text, kwargs)
# kind: 'h' = put_text_horizontal, 'v' = put_text_vertical
# kwargs 覆盖默认渲染参数；measure=False 跳过 calc_box_from_font（如 reversed 无测量语义）
CASES = [
    # --- 纯文本横排 ---
    ('h_cjk_single', 'h', '漫画翻译测试', {}),
    ('h_cjk_multi', 'h', '第一行文字[BR]第二行更长的文字[BR]三', {}),
    ('h_ascii', 'h', 'Hello, World! 123', {}),
    ('h_mixed_multi', 'h', 'CJK混排Alpha测试[BR]second LINE 42', {}),
    ('h_ellipsis_tracked', 'h', '这是……省略……号', {'letter_spacing': 1.25}),
    ('h_exclaim_q', 'h', '！？！？', {}),
    ('h_nostroke', 'h', '无描边红字', {'bg': None}),
    ('h_reversed', 'h', 'שלום abc 123', {'reversed_direction': True, 'measure': False}),
    ('h_spacing', 'h', '行距字距测试[BR]第二行', {'line_spacing': 1.5, 'letter_spacing': 1.2}),
    ('h_single_char', 'h', '字', {}),
    # --- 纯文本竖排 ---
    ('v_cjk_single', 'v', '竖排单列文字', {}),
    ('v_cjk_multi', 'v', '第一列文字[BR]第二列更长文字[BR]短', {}),
    ('v_punct', 'v', '「引号」、句号。间隔…点ー长音', {}),
    ('v_wide_glyphs', 'v', '破——折号ＡＢＣ全角2026', {}),
    ('v_nostroke', 'v', '竖排无描边', {'bg': None}),
    ('v_spacing', 'v', '列距测试[BR]第二列', {'line_spacing': 1.5, 'letter_spacing': 1.15}),
    ('v_ascii_rotate', 'v', 'abc!?123', {}),
    # --- 富文本 ---
    ('rich_h_styles', 'h', _rich([
        _para(
            _text('普通'),
            _text('红色', {'color': '#ff0000'}),
            _text('大号', {'fontSize': 64}),
            _text('斜体', {'italic': True}),
        ),
        _para(_text('第二段落')),
    ]), {}),
    ('rich_h_ruby_emph', 'h', _rich([
        _para(
            _text('前'),
            {'type': 'ruby', 'base': [_text('漢字')], 'text': [_text('かんじ')]},
            _text('着重', {'emphasis': True}),
        ),
    ]), {}),
    ('rich_v_tcy_offset', 'v', _rich([
        _para(
            _text('年'),
            {'type': 'tcy', 'content': [_text('2026')]},
            _text('偏移', {'transform': {'offsetX': 6.0, 'offsetY': -4.0}}),
        ),
        _para(_text('第二列')),
    ]), {}),
    ('rich_h_span_stroke', 'h', _rich([
        _para(
            _text('局部描边', {'stroke': {'color': '#0000ff', 'width': 0.12}}),
            _text('无描边段'),
        ),
    ]), {}),
    ('rich_v_ruby', 'v', _rich([
        _para(
            {'type': 'ruby', 'base': [_text('竖排')], 'text': [_text('たてがき')]},
            _text('正文'),
        ),
    ]), {}),
    # --- 三遍绘制（effects→stroke→fill 全局顺序）敏感用例 ---
    ('rich_h_glow_outer', 'h', _rich([
        _para(
            _text('发光', {'glow': {'color': '#00ff00', 'blur': 0.08}}),
            _text('外描', {'outerStroke': {'color': '#ff00ff', 'width': 0.10}}),
            _text('普通'),
        ),
    ]), {}),
    ('rich_v_glow_outer', 'v', _rich([
        _para(
            _text('光', {'glow': {'color': '#00ff00', 'blur': 0.08}}),
            _text('外', {'outerStroke': {'color': '#ff00ff', 'width': 0.10}}),
            _text('普'),
        ),
    ]), {}),
    ('rich_v_bigstroke_overlap', 'v', _rich([
        _para(_text('あいう')),
        _para(_text('か'), _text('き', {'stroke': {'color': '#ff0000', 'width': 0.6}}), _text('く')),
        _para(_text('さしす')),
    ]), {}),
    ('rich_v_italic_stroke_tcy', 'v', _rich([
        _para(
            _text('第'),
            {'type': 'tcy', 'content': [_text('2024', {'italic': True})]},
            _text('话', {'stroke': {'color': '#0000ff', 'width': 0.15}}),
        ),
    ]), {}),
    ('rich_v_emphasis', 'v', _rich([
        _para(_text('着重号', {'emphasis': True}), _text('正文')),
    ]), {}),
]

DEFAULTS = dict(
    fg=(20, 20, 20),
    bg=(250, 250, 250),
    alignment='center',
    line_spacing=1.0,
    letter_spacing=1.0,
    stroke_width=0.07,
    reversed_direction=False,
)


def _render_case(kind, text, params):
    p = dict(DEFAULTS)
    p.update(params)
    if kind == 'h':
        return text_render.put_text_horizontal(
            FONT_SIZE, text, 10, 10, p['alignment'], p['reversed_direction'],
            p['fg'], p['bg'], 'en_US', True, p['line_spacing'], None, 1,
            p['stroke_width'], letter_spacing=p['letter_spacing'],
        )
    return text_render.put_text_vertical(
        FONT_SIZE, text, 10, p['alignment'], p['fg'], p['bg'],
        p['line_spacing'], None, 1, p['stroke_width'],
        letter_spacing=p['letter_spacing'],
    )


def _measure_case(kind, text, params):
    p = dict(DEFAULTS)
    p.update(params)
    config = Config()
    config.render.disable_font_border = p['bg'] is None
    config.render.stroke_width = p['stroke_width']
    w, h, n, (bx, by) = calc_box_from_font(
        FONT_SIZE, text, kind == 'h', p['line_spacing'], config, None,
        center=None, angle=0, letter_spacing=p['letter_spacing'],
    )
    return {'w': int(w), 'h': int(h), 'n': int(n),
            'body_x': round(float(bx), 4), 'body_y': round(float(by), 4)}


def _prepare():
    text_render.set_font('Arial-Unicode-Regular.ttf')
    text_render.set_bold(False)


def do_dump():
    _prepare()
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    measures = {}
    for name, kind, text, params in CASES:
        rgba = _render_case(kind, text, params)
        if rgba is None:
            print(f'  {name}: render returned None (recorded)')
            measures[name] = {'render': None}
        else:
            np.save(os.path.join(GOLDEN_DIR, f'{name}.npy'), rgba)
            entry = {'render': [int(rgba.shape[0]), int(rgba.shape[1])]}
            if params.get('measure', True):
                entry['measure'] = _measure_case(kind, text, params)
            measures[name] = entry
            print(f'  {name}: {rgba.shape[1]}x{rgba.shape[0]}'
                  + (f" measure={entry.get('measure')}" if 'measure' in entry else ''))
    with open(os.path.join(GOLDEN_DIR, 'measures.json'), 'w', encoding='utf-8') as f:
        json.dump(measures, f, ensure_ascii=False, indent=1)
    print(f'\nbaseline written to {GOLDEN_DIR} ({len(CASES)} cases)')


def do_check(save_diff=False):
    _prepare()
    with open(os.path.join(GOLDEN_DIR, 'measures.json'), 'r', encoding='utf-8') as f:
        baseline = json.load(f)
    failures = []
    for name, kind, text, params in CASES:
        base_entry = baseline.get(name)
        if base_entry is None:
            failures.append(f'{name}: no baseline entry')
            continue
        rgba = _render_case(kind, text, params)
        if base_entry['render'] is None:
            if rgba is not None:
                failures.append(f'{name}: baseline None but now renders {rgba.shape}')
            else:
                print(f'  {name}: OK (None)')
            continue
        if rgba is None:
            failures.append(f'{name}: renders None, baseline {base_entry["render"]}')
            continue
        expected = np.load(os.path.join(GOLDEN_DIR, f'{name}.npy'))
        msgs = []
        if rgba.shape != expected.shape:
            msgs.append(f'shape {expected.shape}->{rgba.shape}')
        else:
            diff = np.abs(rgba.astype(np.int16) - expected.astype(np.int16))
            if diff.any():
                msgs.append(
                    f'pixels differ: n={int((diff.max(axis=2) > 0).sum())}, max={int(diff.max())}'
                )
            if save_diff and diff.any():
                import cv2
                vis = np.clip(diff.max(axis=2) * 8, 0, 255).astype(np.uint8)
                cv2.imwrite(os.path.join(GOLDEN_DIR, f'{name}.diff.png'), vis)
        if params.get('measure', True) and 'measure' in base_entry:
            now = _measure_case(kind, text, params)
            if now != base_entry['measure']:
                msgs.append(f'measure {base_entry["measure"]} -> {now}')
        if msgs:
            failures.append(f'{name}: ' + '; '.join(msgs))
            print(f'  {name}: DIFF ({"; ".join(msgs)})')
        else:
            print(f'  {name}: OK')
    print()
    if failures:
        print(f'{len(failures)}/{len(CASES)} cases differ:')
        for line in failures:
            print('  -', line)
        return 1
    print(f'all {len(CASES)} cases identical to baseline')
    return 0


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--dump', action='store_true')
    group.add_argument('--check', action='store_true')
    ap.add_argument('--save-diff', action='store_true')
    args = ap.parse_args()
    if args.dump:
        do_dump()
        return 0
    return do_check(save_diff=args.save_diff)


if __name__ == '__main__':
    raise SystemExit(main())
