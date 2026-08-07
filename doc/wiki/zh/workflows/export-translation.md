---
title: 导出译文
description: 把检测、OCR 和翻译后的译文按模板导出为文本文件，不生成渲染后的图片
pageId: workflows.export-translation
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 导出译文

当只需要批量取得“图片文字翻译成目标语言后的文本”，暂时不需要修复、排版和渲染成图时，使用“导出翻译”工作流。它仍会执行条件上色、条件超分、检测、OCR 和翻译，但跳过修复、渲染和主输出图保存，把每张图的译文按模板写入工作目录的 `translations/` 副文件，同时保存工程 JSON。

“导出翻译”与“导出原文”“仅翻译（JSON）”“导入翻译并渲染”构成模板/JSON 家族，区别见[导出原文](./export-original.md)、[仅翻译（JSON）](./translate-json-only.md)和[导入翻译并渲染](./import-translation-and-render.md)；九种工作流的整体边界见[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)，汇总表见[工作流矩阵](../reference/workflow-matrix.md)。

## 功能边界

- 输入：主输入图片（与正常翻译相同的文件发现规则），以及可读取的导出模板 `config/translation_template.json`。
- 执行阶段：条件上色 → 条件超分 → 检测 → OCR → 文本行合并 → 翻译；有文本区域且只有原始蒙版时会细化蒙版。
- 跳过阶段：修复、渲染、主输出图保存。翻译结果只落到工程 JSON 和译文副文件，不生成最终成图。
- 输出文件：`manga_translator_work/json/<stem>_translations.json` 和 `manga_translator_work/translations/<stem>_translated.<模板扩展名>`。
- 工作流字段：下拉框第 1 项，运行时写入 `cli.generate_and_export=true`；GUI 切换保证八个工作流布尔字段互斥。

## UI 操作

### 选择导出译文工作流

1. 打开翻译页，在“翻译流程模式：”下拉框中选择“导出翻译”。
2. 页面标题变为“导出翻译”，副标题显示提示：导出翻译后可在 `manga_translator_work/translations/` 目录查看译文副文件。
3. 开始按钮变为“导出翻译”；点击后按该模式启动后端任务。

选择模式只写入配置并更新界面文案，不会自动开始任务。开始前应先添加主输入图片（“添加文件…”“添加文件夹…”或拖放），并保证导出模板存在或可解析。界面提示固定写作 `imagename_translated.txt`，但实际扩展名由模板的 `output_format` 决定（默认 `json`），提示文案不随模板扩展名变化。

“输出目录:”只决定主输出图的位置；导出翻译不写主图，因此本模式下它不影响 JSON 与译文副文件位置，这两类文件始终按输入图片的工作目录规则写入。

## 运行机理

### 处理阶段与输出

导出翻译复用正常翻译的前半段流水线，然后在模板导出处理器中收尾。下面的 Mermaid 展示源码确认的阶段顺序、蒙版分支和输出文件；它与导出原文共享 `_handle_template_export`，但以 `ensure_json_with_empty_regions=false` 调用，因此无文本区域时不产出文件（导出原文则写出空模板）。

```mermaid
flowchart LR
    Input["主输入图片"] --> Pre["条件：上色 -> 超分"]
    Pre --> Detect["检测"] --> OCR["OCR"] --> Merge["文本行合并"] --> Translate["翻译"]
    Translate --> Handler["模板导出处理"]
    Handler --> Yolo{"导入 YOLO 标签?"}
    Yolo -->|是| YoloMask["跳过蒙版细化，JSON 不保存蒙版"]
    Yolo -->|否| RefineCheck{"有区域且只有原始蒙版?"}
    RefineCheck -->|是| Refine["蒙版细化（只细化，不修复）"]
    RefineCheck -->|否| NoRefine["跳过蒙版细化"]
    YoloMask --> HasText{"存在非空文本区域?"}
    Refine --> HasText
    NoRefine --> HasText
    HasText -->|是| SaveJson["保存工程 JSON"]
    HasText -->|否| NoOut["不产出文件"]
    SaveJson --> JsonFile["manga_translator_work/json/&lt;stem&gt;_translations.json"]
    SaveJson --> Gen["generate_translated_text 按模板写译文"]
    Gen --> TxtFile["manga_translator_work/translations/&lt;stem&gt;_translated.&lt;模板扩展名&gt;"]
    Translate -. "不执行" .-> Skip["修复 / 渲染 / 主图保存"]
```

### 模板导出细节

- 模板路径解析顺序：用户指定路径 > `MANGA_TEMPLATE_PATH` 环境变量 > 默认 `config/translation_template.json`。
- 模板首个 `output_format:` 行决定副文件扩展名；合法值为安全的 1–32 字符扩展名，缺失或非法时回退 `json`。
- `generate_translated_text()` 从工程 JSON 的 `regions` 收集条目：只包含非空原文区域，原文和译文都移除 `[BR]` 标记；`<original>` 填原文、`<translated>` 填译文，译文为空时导出空串（不回退原文，这与导出原文不同）。
- 模板必须至少包含一行 `<original>` 占位符，否则解析抛错，导出日志记为 “Failed to export clean text”。
- 内置默认模板的 `output_format` 为 `json`，占位符结构为 `<original>` / `<translated>` 行。

### 导出翻译写入的 JSON 字段

- `skip_font_scaling=true`：导出翻译保留固定字号，便于后续按已生成结果回放；导出原文和仅翻译（JSON）则写 `false`。
- 蒙版：`save_mask` 开启时保存 base64 蒙版与 `mask_is_refined`；导入 YOLO 标签的导出模式跳过蒙版保存。
- 启用上色或超分时，JSON 记录 `colorizer`、`upscale_ratio`、`upscaler`。
- 本模式不渲染，因此不写 `skip_text_replacements`；已有 JSON 中的画笔/印章图层会被保留。

### 与并发和互斥的关系

- `batch_concurrent` 不兼容：桌面控制层和 `translate_batch()` 都把它视为不兼容模式，强制按非并发处理；界面仍保存并发配置也不会变成并发管线。
- 手工叠加多个工作流字段不是受支持组合。运行时 `translate_batch()` 的分派顺序里，导出原文分支先于导出翻译分支；GUI 切换时八个字段互斥。
- 与正常翻译一样，预处理阶段仍会按 `colorizer.colorizer` 和 `upscale.upscale_ratio` 执行条件上色与超分；这些值不是本工作流的强制开关。

## 依赖与冲突

- 模板依赖：模板缺失时导出步骤记录警告，工程 JSON 仍会保存；模板不可解析时译文副文件不产出。
- 无文本区域：本模式以 `ensure_json_with_empty_regions=false` 调用共享导出流程，文本区域为空时不写 JSON 和译文副文件；这与导出原文写出空模板的行为不同（静态源码结论，空区域下的运行提示待验证）。
- `cli.overwrite=false`：GUI 开始前跳过译文副文件已存在的图片（检查 `get_translated_txt_path`，即按模板扩展名生成的目标文件）。
- `cli.save_text`：本模式进入导出分支不依赖 `save_text`，JSON 和译文副文件在导出分支内无条件写出；这与导出原文要求 `template=true` 且 `save_text=true` 不同。
- 上色、超分、检测、OCR、翻译仍按所选参数产生模型、显存、网络和 API 成本；本页不重复其参数说明。
- 主输出目录、`save_to_source_dir`、`cli.format` 只影响主输出图；本模式不写主图，因此这些设置对本工作流输出无直接影响。

## 相关页面 {#related-pages}

- 其它工作流：[正常翻译流程](./normal.md) · [导出原文](./export-original.md) · [仅翻译（JSON）](./translate-json-only.md) · [导入翻译并渲染](./import-translation-and-render.md) · [仅上色](./colorize-only.md) · [仅超分](./upscale-only.md) · [仅修复](./inpaint-only.md) · [替换翻译](./replace-translation.md)
- 九种工作流的选择、输出目录与互斥写入：[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)
- 九种工作流的输入、跳过阶段与输出汇总：[工作流矩阵](../reference/workflow-matrix.md)
- 工作流字段互斥、参数覆盖与模板对齐：[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)

> 详见参考索引：[工作流矩阵](../reference/workflow-matrix.md)。
