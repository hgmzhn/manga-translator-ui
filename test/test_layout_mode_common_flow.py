"""排版模式公共流程回归：入口单次 BR 判断 + strict 按框适配 + balloon_fill 降级 strict。

回归点（2026-07 公共流程重整）：
1. strict 无 BR / 有 BR 都用最终文本 + OCR 框重新适配字号，不再直接用候选大字号。
2. font_size_offset 在适配字号之后只应用一次。
3. balloon_fill 无 original_img / 无气泡蒙版 / 区域未被包裹时降级 strict，与 strict 结果一致。
4. 替换翻译模式强制单行区域豁免按框缩字，清除 BR 后按候选字号渲染。

运行（repo 包根）：
    PYTHONUTF8=1 python test/test_layout_mode_common_flow.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np

from manga_translator.config import Config
from manga_translator.rendering import (
    _resolve_region_stroke_width,
    calc_font_from_box,
    resize_regions_to_font_size,
)
from manga_translator.utils import TextBlock

IMG = np.zeros((400, 400, 3), dtype=np.uint8)

LONG_NO_BR_TEXT = '这是一段相当长的测试文本需要自动断句才能放进气泡里'
BR_TEXT = '第一行文字[BR]第二行更长的文字[BR]第三行'


def make_region(translation, box=(100, 100, 220, 160), font_size=48, n_lines=2):
    # n_lines>=2 才会进入自动断句；单条 OCR 线命中求解器的单行不换行规则
    x, y, w, h = box
    line_h = h / n_lines
    lines = [
        [[x, y + i * line_h], [x + w, y + i * line_h],
         [x + w, y + (i + 1) * line_h], [x, y + (i + 1) * line_h]]
        for i in range(n_lines)
    ]
    return TextBlock(
        lines=lines,
        texts=['原文'] * n_lines,
        font_size=font_size,
        translation=translation,
        direction='h',
        target_lang='CHS',
    )


def make_config(layout_mode):
    config = Config()
    config.render.layout_mode = layout_mode
    return config


def run_layout(layout_mode, translation, box=(100, 100, 220, 160), font_size=48,
               config=None, original_img=None, n_lines=2):
    config = config or make_config(layout_mode)
    region = make_region(translation, box=box, font_size=font_size, n_lines=n_lines)
    dst_list = resize_regions_to_font_size(
        IMG, [region], config, original_img=original_img, skip_text_replacements=True,
    )
    return region, dst_list[0], config


def expected_strict_font(region, config, extra_offset=0):
    """独立复算：最终文本 + OCR 框 → 适配字号（strict 上限），floor 8，再加一次 offset。"""
    w, h = region.unrotated_size
    fit = calc_font_from_box(
        width=float(w),
        height=float(h),
        text=region.translation,
        is_horizontal=True,
        line_spacing=1.0,
        config=config,
        target_lang=region.target_lang,
        letter_spacing=1.0,
        stroke_width=_resolve_region_stroke_width(region, config),
    )
    return max(int(fit), 8) + extra_offset


def test_strict_no_br_caps_font_to_box():
    region, dst, config = run_layout('strict', LONG_NO_BR_TEXT)
    assert '[BR]' in region.translation, '无 BR 文本应先统一自动断句'
    expected = expected_strict_font(region, config)
    assert region.font_size == expected, f'font={region.font_size}, expected fit={expected}'
    assert dst is not None


def test_strict_br_follows_same_rule():
    region, dst, config = run_layout('strict', BR_TEXT)
    assert region.translation == BR_TEXT, '显式断句应原样保留'
    expected = expected_strict_font(region, config)
    assert region.font_size == expected, f'font={region.font_size}, expected fit={expected}'
    assert dst is not None


def test_strict_offset_applied_once_after_fit():
    config = make_config('strict')
    config.render.font_size_offset = 5
    region, _, _ = run_layout('strict', BR_TEXT, config=config)
    expected = expected_strict_font(region, config, extra_offset=5)
    assert region.font_size == expected, f'font={region.font_size}, expected fit+5={expected}'


def test_smart_scaling_smoke():
    region, dst, _ = run_layout('smart_scaling', LONG_NO_BR_TEXT)
    assert region.font_size >= 1
    assert dst is not None


def test_balloon_fill_no_original_img_degrades_to_strict():
    strict_region, strict_dst, _ = run_layout('strict', LONG_NO_BR_TEXT)
    balloon_region, balloon_dst, _ = run_layout('balloon_fill', LONG_NO_BR_TEXT, original_img=None)
    assert balloon_region.font_size == strict_region.font_size
    assert balloon_region.translation == strict_region.translation
    assert np.allclose(np.asarray(balloon_dst), np.asarray(strict_dst))


def test_balloon_fill_without_bubble_mask_degrades_to_strict():
    # mangalens 缓存必然未命中 → 全局蒙版为空 → 区域未被包裹 → 降级 strict
    strict_region, strict_dst, _ = run_layout('strict', LONG_NO_BR_TEXT)
    balloon_region, balloon_dst, _ = run_layout(
        'balloon_fill', LONG_NO_BR_TEXT, original_img=np.full((400, 400, 3), 255, dtype=np.uint8),
    )
    assert balloon_region.font_size == strict_region.font_size
    assert balloon_region.translation == strict_region.translation
    assert np.allclose(np.asarray(balloon_dst), np.asarray(strict_dst))


def test_replace_mode_single_line_keeps_candidate_font():
    config = make_config('strict')
    config.cli.replace_translation = True
    # 宽单行 OCR 框 + 强制横排：命中强制单行豁免
    region = make_region('替换模式[BR]强制单行文本', box=(100, 100, 300, 48), font_size=48, n_lines=1)
    fit_before = calc_font_from_box(
        width=300.0, height=48.0, text=region.translation, is_horizontal=True,
        line_spacing=1.0, config=config, target_lang='CHS', letter_spacing=1.0,
        stroke_width=_resolve_region_stroke_width(region, config),
    )
    expected = max(max(48, int(fit_before)), 8)
    dst_list = resize_regions_to_font_size(IMG, [region], config, skip_text_replacements=True)
    assert '[BR]' not in region.translation and '\n' not in region.translation
    assert region.font_size == expected, f'font={region.font_size}, expected candidate={expected}'
    assert dst_list[0] is not None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f'PASS {test.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'FAIL {test.__name__}: {exc}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
