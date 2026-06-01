# v2.2.8 更新日志

发布日期：2026-05-20

## ✨ 新功能

> 本节三项功能来自 PR [#155](https://github.com/hgmzhn/manga-translator-ui/pull/155)。

### 编辑器：PS 风格对齐/分布工具栏

- 工具栏新增 8 个矢量图标按钮：左/中/右对齐 + 顶/中/底对齐 + 垂直/水平间距分布
- 两种参照模式：选区（包围盒）/ 画布（整张图），按钮一键切换
- 选中 ≥1 框 + 画布参照即可对齐到画布；≥2 框任意参照均可；间距均分要求 ≥3 框
- 全程接入撤销/重做（`MultiRegionUpdateCommand` 单次原子提交，撤销不会因清空 regions 失效）
- 图标走 SVG 资源 + `QPalette.Text`/`Highlight` 注入主题色，跟随主题切换

### 编辑器：批量移动文本框

- 多选若干文本框后，拖拽其中任意一个，其它选中项同步跟随
- 拖拽时实时同步白框和文字位置；释放后所有项的位移一并提交模型

### 编辑器：智能间距吸附

- 拖拽白框时自动检测它与场景中其它任意两个框对的等距关系
- 距吸附目标 5px 内自动吸附到对称位置，并显示双组对称刻度线 + 距离标签
- 同时检测水平和垂直两个方向，间距吸附优先于普通边缘吸附

### 编辑器：「替换前译文」双向同步

- JSON 新增 `translation_raw` 字段保存 YAML 替换规则应用前的译文
- 属性面板新增「显示替换前译文」复选框，勾选切换译文框显示替换前/替换后内容
- 编辑替换前内容实时跑替换规则同步到译文；编辑译文同步覆盖替换前

### API 渲染/上色:OpenRouter 出图开箱即用

- `openai_renderer` / `openai_colorizer` 新增 OpenRouter 后端识别(`openrouter.ai`)
- 自动优先走 `chat/completions`(跳过 `images/edits` / `images/generations` 的无效探测,减少首次请求 2 次 404 往返)
- 自动注入 `modalities: ["image", "text"]`(OpenRouter 出图必须的字段);用户在 `custom_api_params.json` 写的同名字段会覆盖默认值
- 自动设 `max_tokens: 2048`(避免 OpenRouter 默认 32768 触发 402 余额不足;同样可被用户覆盖)
- 模型示例:`google/gemini-3-pro-image-preview` / `google/gemini-2.5-flash-image`,`image_config` 等专有参数走 `custom_api_params.json` 顶层透传即可

### 核心/UI：配置文件丢失自动补全

- CLI/Web/Qt 启动时自动检测缺失的本地配置文件，并通过内置模板自动生成
- 涵盖的文件包括：`text_replacements.yaml`（文本替换规则）、`translation_template.json`（导出原文模板）、`filter_list.json`、`custom_api_params.json` 及各类 AI 提示词 YAML
- 修改 `doc/SETTINGS.md` 和 `.gitignore`，让生成的配置文件不再被 Git 错误追踪，解决版本冲突问题
- `text_replacements.yaml` 竖排规则（vertical）新增”六点变三点省略号”替换支持

### 编辑器：导出回写原翻译输出目录

- 主翻译流程（含并发流水线）保存最终图片时，将其输出目录写入对应 `_translations.json` 的 `last_export_dir` 字段
- 编辑器再次导出该图时优先读取该字段，把导出图回写到原翻译流程的输出位置，而不是全局”上次输出路径”
- `save_to_source_dir` 仍为最高优先级；JSON 未记录时退回原有逻辑（全局 `last_output_path` → 源目录）
- 纯 JSON 导出等不产出图片的流程会保留已存在的 `last_export_dir`，避免被清空

## 🚀 性能优化

### 编辑器：A/D 切图防黑闪（来自 PR [#155](https://github.com/hgmzhn/manga-translator-ui/pull/155)，本仓库重写实现）

- `ResourceManager` 既有 LRU 图像缓存（上限 5 张）现在同时持有 QImage：后台线程预转一次，命中即复用，主线程零阻塞
- `on_image_changed` 用 "detach `_image_item` + 全量 `clear_all_state` + reattach + `setPixmap`" 模式：场景全清的同时复用旧 `QGraphicsPixmapItem`，新图就绪后原地替换
- 整个切换被 `setUpdatesEnabled(False/True)` 包裹，viewport 不会看到中间空白帧
- A/D 切换看过的图瞬时响应；大图（2000×3000）首次加载也不再卡顿主线程 30-80 ms
- `clear_editor_state` 新增 `keep_document` 参数：切图场景下跳过 `unload_image()` 和 `clear_document()`，保留 LRU 缓存

## 🔧 重构

- 编辑器对齐工具栏图标由 QPainter 手画改为 SVG 资源 + `_themed_icon` 主题色注入（约 -150 行）
- `_detect_spacing_snap` 4 方向 × 4 分支重复代码参数化为按轴循环（80 → 50 行），顺带修复第二候选 spec 的刻度线右端字段错位
- 编辑器工具栏新增中文硬编码全部走 `self._t()`，补完 6 个 locale（en/zh_CN/zh_TW/ja/ko/es）的对齐/分布翻译

## 🐛 修复

- 修复横排文本渲染时有概率出现字序错乱（最后一个字跑到第一个位置）。
- 修复编辑器使用画板功能涂抹后导出时，画板涂层在最终导出图中丢失。
- 修复画板涂层在编辑器预览中描边边缘显示为黑色（导出正常）。
- 修复在画板页点击「选择」按钮时被强制切回蒙版页的问题。
- 修复 `apply_white_frame_center` 写盘时只移动 `center` 而 `white_frame_rect_local` / `render_box_rect_local` 不同步平移，导致下次打开 JSON 看到区域位置漂移的问题。
- 修复并行模式修复线程使用未过滤 `text_regions` 的问题：翻译过滤后对比原始/过滤后 region 集合，有差异则触发修复重做（基于过滤后的 regions 重新生成 mask 并修复），避免被过滤掉的框在最终图上留下空白补丁。
- 修复关闭超分/上色后重跑，旧 `editor_base` 底图仍被编辑器加载的问题：JSON 缺少 `upscale_ratio` / `colorizer` 时视为过期，删除残留并回退原图。
- 修复调大字间距时中文省略号 `……` 被拆开拉宽的问题：横排保持省略号内部点距，竖排按替换规则归一为竖省略号并复用既有省略号间距计算。
- 修复自动生成的 `text_replacements.yaml` 含控制字符导致加载失败。
- 修复并发流水线下选择上色器报 `got multiple values for keyword argument 'config'`。
- 修复导入配置后超分倍率显示错乱（null 被显示成 ×4）。
