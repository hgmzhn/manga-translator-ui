---
title: 仅上色
description: 只对输入图片执行上色，跳过检测、OCR、翻译、修复和渲染，输出上色后的主图
pageId: workflows.colorize-only
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 仅上色

当只需要给整批图片上色、暂时不需要检测文字、OCR、翻译、修复和渲染时，使用“仅上色”工作流。它在翻译页“翻译流程模式：”下拉框中位于“导入翻译并渲染”之后，开始按钮显示“开始上色”。该模式只执行上色阶段，然后立即返回并保存主输出图；它不会因为选择了本模式而强制启用上色器，`colorizer.colorizer` 为 `none` 时结果是原图。

“仅上色”与“仅超分”“仅修复”同属只做单个处理阶段的工作流，区别见[仅超分](./upscale-only.md)和[仅修复](./inpaint-only.md)；九种工作流的整体边界见[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)，汇总表见[工作流矩阵](../reference/workflow-matrix.md)。上色器类型、上色大小、降噪强度等参数见[超分与上色](../desktop/settings/upscale-and-colorization.md)，九种模式对参数的强制覆盖见[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)。

## 什么时候用

- 输入：主输入图片（与正常翻译相同的文件发现规则：递归查找受支持扩展名、自然排序、跳过名为 `manga_translator_work` 的目录）。
- 执行阶段：上色（条件执行）。是否实际上色由 `colorizer.colorizer` 决定；为 `none` 时结果为原图。
- 跳过阶段：超分、检测、OCR、文本行合并、翻译、蒙版细化、修复、渲染。源码中仅上色分支位于上色之后、超分之前，因此即使设置了 `upscale_ratio` 也不会执行超分。
- 输出文件：主输出图；上色器有效（`colorizer != none`）时写编辑器底图 `manga_translator_work/editor_base/<原文件名>`；`cli.save_text` 启用时批量路径还会写空 `regions` 的工程 JSON（根据当前实现，可能因版本而异）。
- 工作流字段：下拉框索引 5 写入 `cli.colorize_only=true`；GUI 切换保证八个工作流布尔字段互斥。

## 运行这个流程

### 选择仅上色工作流

1. 打开“翻译”页，在“翻译流程模式：”下拉框中选择“仅上色”。
2. 页面标题变为“仅上色”，副标题显示提示：仅对图片进行上色处理，不进行检测、OCR、翻译和渲染。
3. 开始按钮变为“开始上色”；点击后按该模式启动后端任务。

选择模式只写入配置并更新界面文案，不会自动开始任务。开始前应先添加主输入图片（“添加文件…”“添加文件夹…”或拖放），并在“输出目录:”中填写或拖入输出文件夹。若上色器选择 `openai_colorizer` / `gemini_colorizer`，还需要在 API 管理中配置对应 Key；i18n 说明文案提到缺少 Key 时 UI 不会开始翻译，本工作流启动时是否执行同样的阻断检查可能因版本而异。

## 处理顺序

### 处理阶段与输出

仅上色复用 `_translate_until_translation()` 的上色步骤，然后在上色器执行完、超分开始前提前返回。下面的 Mermaid 展示阶段顺序、结果赋值和输出分支；虚线表示“仅上色不进入”的正常流程后续阶段。

```mermaid
flowchart LR
    Input["主输入图片"] --> CQ{"colorizer.colorizer\n!= none?"}
    CQ -- "是" --> Run["执行上色器"]
    CQ -- "否" --> Skip["跳过上色\n结果 = 原图"]
    Run --> Result["ctx.result = 上色结果"]
    Skip --> Result
    Result --> Save["保存主输出图"]
    Result --> BaseQ{"上色器有效\n(colorizer != none)?"}
    BaseQ -- "是" --> Base["编辑器底图\nmanga_translator_work/editor_base/"]
    BaseQ -- "否" --> NoBase["不写编辑器底图"]
    Result -. "save_text 或 text_output_file" .-> Json["工程 JSON（regions 为空）"]
    Result -. "仅上色不进入" .-> Skipped["正常流程后续阶段：超分 / 检测 / OCR / 翻译 / 蒙版 / 修复 / 渲染"]
```

上图表达的是分支：`colorize_only=true` 时，`_translate_until_translation()` 在上色后把 `ctx.result` 设为上色结果、把 `ctx.text_regions` 设为空列表、报告进度状态 `colorize-only-complete`（核心内部完成状态，桌面 locale 中没有对应的专门文案），必要时写编辑器底图，然后返回；批量路径不会再调用 `_complete_translation_pipeline()`，也不会执行超分、检测、OCR、翻译、蒙版、修复或渲染。`colorizer.colorizer=none` 时上色步骤被跳过，`ctx.img_colorized = ctx.input`，结果就是原图。

### 与并发和互斥的关系

- `batch_concurrent` 不兼容：桌面控制层和核心 `translate_batch()` 都把“仅上色”列入不兼容模式清单，强制按非并发处理；界面仍保存并发配置也不会变成并发管线。
- 手工叠加多个工作流字段不是受支持组合。GUI 切换时八个字段互斥；在核心分派中仅上色分支在仅超分和仅修复分支之前返回，因此与 `upscale_only`、`inpaint_only` 同时开启时只有仅上色行为生效，文档不把这种叠加描述为受支持组合。
- 与正常翻译一样，上色是否执行由 `colorizer.colorizer` 决定；仅上色不会强制选择上色器。正常、仅超分、仅修复和替换翻译也会在 `colorizer.colorizer != none` 时先上色，这是上色阶段本身的行为，不是本工作流独有。

## 输入、输出与限制

- `colorizer.colorizer=none`：不执行上色，输出就是原图，也不写编辑器底图；这是“仅上色”不强制上色器的直接后果。
- AI 上色器：`openai_colorizer` / `gemini_colorizer` 需要对应的 API Key（`.env`）和可用网络；i18n 说明提到缺少 Key 时 UI 不会开始翻译，本工作流启动时的实际阻断提示可能因版本而异。
- `upscale_ratio`：仅上色分支在超分之前返回，因此本模式下超分设置被忽略。
- `cli.overwrite=false`：GUI 开始前按主输出图（`_calculate_output_path` 的结果）过滤已存在文件；全部被跳过时会在翻译开始前结束。
- `cli.save_text`：默认 `true`；批量保存路径会写包含空 `regions` 的工程 JSON（根据当前实现，空 regions JSON 的实际内容与编辑器行为可能因版本而异）。
- 无文本区域意味着不产生检测、OCR、翻译、蒙版、修复、渲染相关的中间文件：不写修复图、原文/译文 TXT 或模板导出文件。
- PSD 导出（`export_editable_psd`）属于通用保存逻辑 `_save_and_cleanup_context()`，本模式无文本区域时的实际 PSD 内容未在所有环境中确认。
- 上色模型、显存、网络和 API 成本由 `colorizer.colorizer` 及上色参数决定，这里不重复其参数说明。

## 继续阅读 {#related-pages}

- 其它工作流：[正常翻译流程](./normal.md) · [导出原文](./export-original.md) · [导出翻译](./export-translation.md) · [仅翻译（JSON）](./translate-json-only.md) · [导入翻译并渲染](./import-translation-and-render.md) · [仅超分](./upscale-only.md) · [仅修复](./inpaint-only.md) · [替换翻译](./replace-translation.md)
- 九种工作流的选择、输出目录与互斥写入：[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)
- 九种工作流的输入、跳过阶段与输出汇总：[工作流矩阵](../reference/workflow-matrix.md)
- 工作流字段互斥、参数覆盖与模板对齐：[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)

> 详见参考索引：[工作流矩阵](../reference/workflow-matrix.md)。
