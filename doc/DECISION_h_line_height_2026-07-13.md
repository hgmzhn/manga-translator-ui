# 决策待定：横排行高口径（2026-07-13）

**状态：未决策。** 本文档记录 Phase 2 归一引入的横排行高口径变化，供改天决策。
在决策前，新口径（测量==渲染）已随归一 commit 生效；三个方向任选其一，操作见文末。

## 背景

text_render 重构 Phase 2 把纯文本渲染归一到富文本单路径（见
`doc/REFACTOR_text_render_2026-07-13.md`）。归一暴露并修正了一个旧的自相矛盾：

| | 归一前 | 归一后 |
|---|---|---|
| 横排**测量**行高 | `font_size`（48px 字号 = 48px/行） | `ascent+descent`（≈65px/行，Arial-Unicode） |
| 横排**渲染**行进 | `QTextLine.height()`（≈65px/行） | 同测量（65px/行） |
| 矛盾 | 测量框比实际渲染矮约 35%，多行必向下溢出，靠 render() 补边掩盖 | 无，白框贴合 |

另一个被消除的旧不一致：同一段文字"无样式 vs 加任意样式"走两条路径，
字号自适应原本相差 20%+（富文本路径一直是 ascent+descent 口径）。

## 量化影响（48px 字号实测）

- **宽度受限场景（漫画气泡最常见）：字号自适应完全不变**
  （4 个典型场景 33/58/63/49 归一前后一致）
- **高度受限场景：字号自适应约 -23%**
  （800×100 框：88→68；500×60 英文：54→41）
- 竖排：不受影响（测量值不变，渲染面仅 +2~7px 包络差）

## 三个方向

1. **接受新口径**（当前生效）：测量==渲染，白框贴合；高度受限字号变小可用
   `render.font_scale_ratio` 配置全局补偿。什么都不用做。
2. **改用紧凑行高**：测量与渲染都改 `font_size`/行。字号自适应恢复旧值，但行距
   变紧、大字形墨迹可能贴近；富文本既有行为同步变化，需更新测试断言。
   改法：`text_render.py` 的 `_build_rich_horizontal_layout` 中
   `line_height = ruby_extra + ascent + descent + dot_extra` 一处改为
   `ruby_extra + font_size + dot_extra`（与 `_rich_horizontal_layout_geometry`
   共用同一数字，测/渲自动同步）。
3. **回退整个 Phase 2**：`git revert` 归一 commit
   （`git log --oneline | grep 归一` 找 hash）。回到两套编排并存 + 旧矛盾口径。

## 验证手段

任何方向改完后跑：
- `python test/render_golden.py --check`（golden 基线在 `test/golden/`）
- `QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_rich_text_rendering.py`
- 高度受限自适应抽查：`calc_font_from_box(width=800, height=100, text=..., is_horizontal=True)`
