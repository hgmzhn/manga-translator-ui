# text_render 渲染层重构记录（2026-07-13）

目标：以减少代码量、提高可维护性为目的重构 `manga_translator/rendering/text_render.py`
及连带文件。分四个阶段推进，每阶段用 golden 像素基线 + 三个测试套件验收。

## 状态一览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 测试基线 + golden 像素基准脚本 | 完成 |
| Phase 1 | 死代码与空壳清理（-659 行） | **已提交 d69b21e** |
| Phase 2 | 纯文本归一到富文本单路径（净 -约 300 行） | **已提交；横排行高口径决策待定，见 doc/DECISION_h_line_height_2026-07-13.md** |
| Phase 3 | text_render.py 拆分为子模块（facade 保 API） | 未开始 |
| Phase 4 | 连带文件简化（__init__.py / text_render_hq.py） | 未开始 |

回归验收手段（Phase 0 建立）：
- `test/render_golden.py`：22 个语料用例（纯文本横/竖排、省略号、宽字形、reversed、
  富文本 ruby/tcy/斜体/偏移/局部描边）dump `put_text_*` 的 RGBA 输出与
  `calc_box_from_font` 测量四元组。
  用法：`python test/render_golden.py --dump`（建基线）/ `--check`（逐像素对比）/
  `--check --save-diff`（差异可视化 PNG）。
- 测试套件：`test/test_rich_text_rendering.py`（36，其中
  `test_vertical_bold_does_not_change_body_column_width` 为重构前已存在的字体相关
  flake）、`test_rich_text_editing.py`（23）、`test_textblock_rich_safety.py`（13）。
  跑法：`QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_xxx.py`
  （pytest 会被上级旧仓库的 conftest 污染 sys.path，用直跑方式）。

## Phase 1（已提交 d69b21e）

纯删除，golden 逐像素零差异：

- 零引用死函数：`_stroke_path`、`_font_supports_character`、`_get_fallback_glyph`、
  `_style_bold_extra`、`_clear_shape_caches`、`resolve_horizontal_line_spacing_multiplier`、
  `put_char_horizontal`
- 恒等空壳（渲染层字符替换早已外移到 text_replacements.yaml）：
  `compact_special_symbols`、`normalize_vertical_ellipsis_text`、
  `_normalize_horizontal_measure_text`，8 处调用点内联为 `(text or '')`
- `text_render_eng.py` 死渲染链：`render_lines` + `render_textblock_list_eng`
  （全仓无调用；活跃的 `apply_manga2eng_line_breaks` 断行链保留）
- 整文件删除：`text_render_pillow_eng.py`（285 行，零引用）

## Phase 2（工作区未提交）

### 改动内容

核心思想：**纯文本就是"单 span、默认样式"的富文本特例**。渲染与测量入口把纯字符
串归一为单样式 richtext 文档（`_coerce_render_document`，内部走
`rich_text.legacy_line_breaks_to_document`，BR/换行 → 段落），此后只剩富文本一套编排。

- `put_text_horizontal` / `put_text_vertical`：签名不变，入口归一后直接走
  `_render_rich_text_*`；两套纯文本编排（约 170 行）删除
- `measure_rich_text_metrics`：入口同样接受纯字符串
- `rendering/__init__.py`：`calc_text_block_dimensions` / `calc_text_block_metrics`
  的手写近似公式（`font_size×行数 + spacing`、`sum(line_widths)+spacing`）退役，
  统一走 `measure_rich_text_metrics`——测量与渲染共用同一套包络几何
- 删除随之孤儿化的函数：`_build_vertical_layout`、`_crop_and_color`、
  `_paste_surface`、`_paste_glyph_pair`、`_vertical_border_bitmap`、
  `_glyph_stroke_alpha` 及 `FontState.strokes` 缓存链
- 断行决策原语不动：`get_string_width/height`、`calc_horizontal/vertical(_metrics)`、
  `auto_linebreak` 全链、`_vertical_base`（断行按 per-char advance，与列厚无关）
- HQ 超采样透明兼容：`text_render_hq` 只是放大字号转发 `put_text_*`，
  `plain_equivalent_text` 降级链继续工作，闸门未动

### 顺带修复的 bug：富文本横排 reversed 摆放

`_line_surface` 在 `reversed_direction=True` 时以"行右端"为原点相对化墨迹
（`origin_x = -logical_width`），`left_rel` 因此带 +行宽偏移。旧纯文本编排的
`pen_x` 公式有补偿项；富文本编排 `draw_x = cursor_x + left_rel` 从来没有补偿——
墨迹整体右移一个行宽被裁出画布。生产原本不可达（rendering 层只对
`direction=='hl'` 传 True，而富文本恒 False），归一后纯文本 'hl' 会踩到。
已在 `_render_rich_text_horizontal` 摆放处补偿（reversed 时 `draw_x -= 行宽`）。

### 验证结果

- 富文本 5 个 golden 用例逐像素零差异（归一没碰富文本路径）
- 竖排：测量值完全不变；渲染面仅 +2~7px（固定列厚 + 包络 extras 与旧自适应列宽
  对 CJK 几乎无差）；标点旋转/贴边/省略号全部一致（对比图人工确认）
- 横排：文字内容与摆位一致；行高口径变化见下节
- 测试套件与基线持平（35/36+23+13）
- 对比图存于 `test/golden/compare/`（左旧右新）

### ⚠ 决策点：横排行高口径（暂缓提交的原因）

归一修正了一个旧的隐藏缺陷，副作用可见：

| | 旧（两套口径并存） | 新（归一） |
|---|---|---|
| 横排测量行高 | `font_size`（48px/行） | `ascent+descent`（Arial-Unicode ≈65px/行） |
| 横排渲染行进 | `QTextLine.height()`（65px/行） | 同测量（65px/行） |
| 后果 | 测量比渲染矮 35%，多行必向下溢出测量框（靠 render() 补边吸收） | 测量==渲染，白框贴合 |

量化影响（48px 字号实测）：
- **宽度受限场景（漫画气泡最常见）：字号自适应完全不变**
  （4 个典型场景 33/58/63/49 归一前后一致）
- **高度受限场景：字号自适应约 -23%**（800×100 框：88→68；500×60 英文：54→41）
- 另一个旧的不一致被消除：同一段文字"无样式 vs 加任意样式"的字号自适应原本相差
  20%+（富文本路径一直是新口径），归一后一致

补偿/反悔手段：
1. 全局补偿：`render.font_scale_ratio` 配置按需放大
2. 回退整个 Phase 2：`git checkout -- manga_translator/rendering/__init__.py
   manga_translator/rendering/text_render.py`（改动全部在工作区，HEAD 是 Phase 1）
3. 如果只想改行高模型（保留归一）：`_build_rich_horizontal_layout` 中
  `line_height = ruby_extra + ascent + descent + dot_extra` 一处（text_render.py），
  和 `_rich_horizontal_layout_geometry` 共用同一数字，改一处测/渲同步变。
  注意这会同时改变富文本既有行为（测试固化，需同步更新断言）。

## Phase 3 / Phase 4（未开始）

- Phase 3：`text_render.py`（当前约 2350 行）拆分为 fonts / glyphs / compose /
  layout / measure 子模块，facade 保持 `text_render.xxx` 全部公共 API 与
  `auto_linebreak` 依赖的 `_vertical_base`、`_measure_horizontal_text_width`。
  外部消费方清单与硬边界见 `~/.claude/plans/scalable-fluttering-cerf.md`。
- Phase 4：`rendering/__init__.py` 的 `generate_line_break_combinations` 三分支合并；
  `text_render_hq.py` 三处重复调用收口。
- 附带备忘：`scripts/verify_vertical_stroke_alignment.py` 引用了不存在的
  `_get_vertical_border_bitmap`（早于本次重构就已脱节），需要时按新 API 重写。
