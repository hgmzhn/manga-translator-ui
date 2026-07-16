# 横排行高决策：真实墨迹布局（2026-07-16 已决策）

**状态：已完成。** 横排不再选择 `font_size` 或 `ascent+descent` 作为统一行高，
改为按当前内容的实际 shaped glyph 墨迹和富文本效果包络布局。

## 决策

- Qt `QTextLayout` 基线只负责 shaping 和最终绘制坐标。
- 字号适配、渲染框、正文中心和相邻行推进不消费字体设计行框。
- 每个 span 从实际 glyph run 构造 `QPainterPath`，按 path 的像素边界生成主文字框。
- 局部/全局描边、斜体、旋转、镜像和 transform offset 直接进入主文字框。
- ruby 放在对应主墨迹上方，emphasis 放在主墨迹下方，两者进入整行 paint 包络。
- 相邻行基线距离由 `上一行 paint_bottom - 下一行 paint_top + 可见行间隙` 决定；
  因此纯 CJK 自动紧排，而下伸字母、重音、ruby 和着重号会按实际需要拉开。
- `line_spacing` 现在缩放 `0.1em` 的可见墨迹间隙；默认 1.0 即 `0.1em`。
- 显式空行采用一个 `font_size` 高的结构槽位；它不是字体指标 fallback。

## 为什么不使用固定行高

Arial Unicode 48px 的 `ascent+descent` 约为 64px，但常见 CJK 墨迹只有约 44px。
检测模型框住的是可见墨迹，使用 64px 字体设计框做高度适配会无条件缩小字号。
另一方面，固定 `font_size` 无法处理 `gypqj` 下伸、重音、局部大字号和富文本装饰。
真实墨迹碰撞布局同时解决了这两个问题。

## 实测结果

条件：打包 Arial Unicode、默认 7% 描边、`line_spacing=1.0`。

| 场景 | 旧 `font_size` | `ascent+descent` | 真实墨迹计划 |
|---|---:|---:|---:|
| 800×100，`高度受限字号对比` | 88 | 68 | **95** |
| 500×60，`HEIGHT LIMITED` | 54 | 41 | **62** |
| 500×220，三行 CJK | 70 | 53 | **64** |
| 500×220，`gypqj / ÁÉÎÔŨ / gypqj` | 70 | 53 | **63** |

最后一项会因真实上下伸展碰撞而比纯 CJK 更保守，但仍明显大于统一字体行框口径。

## 实现位置

- glyph path 与固定像素墨迹框：`text_render/_layout.py::_horizontal_glyph_path`、
  `_line_ink_geometry`
- span/ruby/emphasis 富文本计划：`_build_rich_horizontal_layout`
- 相邻行防碰撞与最终包络：`_rich_horizontal_layout_geometry`
- 计划消费与绘制：`text_render/_render.py::_render_rich_text_horizontal`
- 字号适配：`calc_font_from_box -> calc_box_from_font -> measure_rich_text_metrics`
- 自动断行候选尺寸：`auto_linebreak._measure_required_size` 直接消费同一测量入口
- 英文气泡 mask 行高：`text_render_eng.apply_manga2eng_line_breaks` 使用当前全文
  实际墨迹高度与配置行间隙，不再使用 `0.8 × font_size`

全局描边已直接进入横排计划，横排不再使用 `calc_box_from_font` 外围 effect padding。
竖排布局未改变。

## 验证

- `python test/render_golden.py --check`
- `QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_rich_text_rendering.py`
- `QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_rich_text_editing.py`
- `QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_textblock_rich_safety.py`
- `QT_QPA_PLATFORM=offscreen PYTHONPATH=. python test/test_font_family_sanitization.py`
