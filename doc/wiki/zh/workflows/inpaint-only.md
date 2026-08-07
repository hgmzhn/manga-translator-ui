---
title: 仅修复
description: 只检测文字区域并清除原文，跳过 OCR、翻译与渲染，输出无字干净图
pageId: workflows.inpaint-only
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 仅修复

当只需要把画面里的文字区域抹掉、保留干净的背景图，不需要识别文字内容、翻译或排版渲染时，使用“仅修复”工作流。它仍然执行条件上色、条件超分、检测、文本行合并、蒙版细化和修复，但跳过 OCR、翻译与渲染，并在分支结束前清空文本区域，因此输出的是不含译文的无字图。

“仅修复”与“仅上色”“仅超分”都属于只处理图片的旁路模式，区别见[仅上色](./colorize-only.md)和[仅超分](./upscale-only.md)；与完整流水线的差异见[正常翻译流程](./normal.md)。九种工作流的整体边界见[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)，汇总表见[工作流矩阵](../reference/workflow-matrix.md)。蒙版与修复参数本身见[蒙版与图像修复](../desktop/settings/mask-and-inpainting.md)。

## 功能边界

- 输入：主输入图片（与正常翻译相同的文件发现规则），不需要工程 JSON、TXT 或配对图等任何工作流副文件前置。
- 执行阶段：条件上色（`colorizer.colorizer != none` 时）→ 条件超分（`upscale.upscale_ratio` 有值时）→ 检测 → 跳过 OCR、用字面量 `TEXT` 填充检测行 → 文本行合并 → 蒙版细化 → 修复。
- 跳过阶段：OCR、翻译、渲染和文本排版；分支结束前把 `text_regions` 清空，因此结果不会以译文渲染。
- 输出文件：主输出图（修复后的无字图；无文字行、无合并区域或选择 AI 渲染器时是未修复的工作图）；条件上色或超分启用时还会写编辑器底图；`save_text` 开启时写空 `regions` 的工程 JSON（静态源码结论，运行待验证）。
- 工作流字段：下拉框第 7 项，运行时写入 `cli.inpaint_only=true`；GUI 切换保证八个工作流布尔字段互斥。

## UI 操作

### 选择仅修复工作流

1. 打开翻译页，在“翻译流程模式：”下拉框中选择“仅修复”。
2. 页面标题变为“仅修复”，副标题显示提示“提示：仅检测文字并执行图像修复，输出无字干净图，不进行翻译和渲染”。
3. 开始按钮变为“开始修复”；点击后按该模式启动后端任务。

选择模式只写入配置并更新界面文案，不会自动开始任务。开始前先添加主输入图片（“添加文件”“添加文件夹”或拖放），本模式不要求任何副文件。

“输出目录:”决定主输出图的位置，命名规则与正常翻译相同：正常输出目录下保留输入文件夹名与相对层级，`save_to_source_dir=true` 时改为原图同级 `manga_translator_work/result/`，`cli.format` 为空或 `none` 时保留原扩展名。

## 运行机理

### 处理阶段与输出

仅修复复用正常翻译的预处理前半段，然后在 `_translate_until_translation` 的“仅修复”分支中收尾。下面的 Mermaid 展示源码确认的阶段顺序、跳过分支和输出；它与正常翻译共享条件上色、条件超分和检测，但 OCR 与翻译被跳过。

```mermaid
flowchart LR
    Input["主输入图片"] --> Pre["条件：上色 -> 超分"]
    Pre --> Detect["检测"]
    Detect --> HasLine{"检测到文字行且原始蒙版有效?"}
    HasLine -->|否| Pass["以未修复工作图作为结果"]
    HasLine -->|是| Fill["跳过 OCR：检测行填充字面量 TEXT"]
    Fill --> Merge["文本行合并"]
    Merge --> HasRegion{"存在合并后的文本区域?"}
    HasRegion -->|否| Pass
    HasRegion -->|是| Refine["蒙版细化"]
    Refine --> AiRenderer{"选择 AI 渲染器?"}
    AiRenderer -->|是| WorkBase["跳过修复，工作图作为修复底图"]
    AiRenderer -->|否| HasMask{"蒙版为空?"}
    HasMask -->|是| WorkBase
    HasMask -->|否| Inpaint["修复"]
    WorkBase --> Result["结果图"]
    Inpaint --> Result
    Result --> Clear["清空 text_regions，不渲染译文"]
    Result --> Save["保存主输出图"]
    Save -. "跳过" .-> SkipStages["OCR / 翻译 / 渲染"]
    Clear -. "跳过" .-> SkipStages
```

- 分支对每个检测行填入字面量 `TEXT` 占位文本并跳过 OCR；文本行合并把检测行合并成 `text_regions`。
- 无文字行、无原始蒙版或无合并区域时，直接以未修复的工作图作为结果返回，不进入蒙版细化和修复。
- 蒙版细化失败时降级为简单膨胀，膨胀参数来自 `kernel_size` 与 `mask_dilation_offset`。
- 选择 AI 渲染器（OpenAI/Gemini）或蒙版为空时跳过真正的修复，工作图原样作为“修复底图”。
- 分支结束前清空 `text_regions` 并设置 `inpaint_only_complete` 标志；批量保存阶段跳过 `_complete_translation_pipeline`，不执行蒙版细化后的常规渲染。

### 蒙版与修复细节

- 蒙版细化与修复参数来自设置“修复”页签（修复模型、修复大小、修复精度、逐块修复、纯色气泡直接填色、遮罩扩张偏移等），详见[蒙版与图像修复](../desktop/settings/mask-and-inpainting.md)。
- 修复输入是条件上色/超分之后的工作图（`load_image(ctx.upscaled)`），不是原始输入图。
- 修复结果写入 `ctx.img_inpainted`，再转成 PIL 图像作为 `ctx.result` 保存为主输出图；本模式不额外写 `inpainted/` 修复图副文件。

### 与并发和互斥的关系

- `batch_concurrent` 不兼容：桌面控制层和 `translate_batch()` 都把它视为不兼容模式，强制按非并发处理；界面仍保存并发配置也不会变成并发管线。
- 手工叠加多个工作流字段不是受支持组合；GUI 切换时八个字段互斥，后端 `translate_batch()` 只在无任何不兼容模式时才构建并发流水线。
- 与正常翻译一样，预处理阶段仍会按 `colorizer.colorizer` 与 `upscale.upscale_ratio` 执行条件上色与超分；这些值不是本工作流的强制开关。

## 依赖与冲突

- 输入依赖：主输入必须是文件服务支持的图片；本模式不要求工程 JSON、TXT 或配对图等副文件。
- `cli.overwrite=false`：GUI 开始前检查主输出图是否已存在（与其他“只写主图”模式共用普通翻译的检查分支）。
- `cli.save_text`：默认 `true`。批量保存阶段在 `save_text` 开启时仍会写入空 `regions` 的工程 JSON（含蒙版与上色/超分信息），这是静态源码结论，实际 GUI 文件行为待运行验证。
- AI 渲染器：选中 OpenAI/Gemini 渲染器时，本模式不执行真正的修复，输出未修复的工作图；这与“仅修复”名称存在差异，属于源码确认的行为。
- 检测、蒙版细化和修复按所选参数产生模型与显存成本；OCR 和翻译被跳过，不产生对应成本。
- 主输出目录、`save_to_source_dir`、`cli.format` 只影响主输出图；本模式不写原文/译文 TXT，因此导出模板文件不影响本工作流。

## 相关页面 {#related-pages}

- 其它工作流：[正常翻译流程](./normal.md) · [导出原文](./export-original.md) · [导出翻译](./export-translation.md) · [仅翻译（JSON）](./translate-json-only.md) · [导入翻译并渲染](./import-translation-and-render.md) · [仅上色](./colorize-only.md) · [仅超分](./upscale-only.md) · [替换翻译](./replace-translation.md)
- 九种工作流的选择、输出目录与互斥写入：[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)
- 九种工作流的输入、跳过阶段与输出汇总：[工作流矩阵](../reference/workflow-matrix.md)
- 工作流字段互斥、参数覆盖与模板对齐：[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)

> 详见参考索引：[工作流矩阵](../reference/workflow-matrix.md)。
