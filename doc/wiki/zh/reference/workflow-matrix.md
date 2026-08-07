---
title: 工作流矩阵
description: 九种翻译工作流的输入、跳过阶段与输出汇总矩阵，以及到各工作流页面的反向链接
pageId: reference.workflow-matrix
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 工作流矩阵

本页是翻译页“翻译流程模式：”九种工作流的参考索引：用一张矩阵汇总每种工作流的输入、跳过阶段和输出，并反向链接到对应页面。需要某种工作流的完整操作、参数边界、文件格式和跳过条件时，从矩阵行进入对应的[工作流页面](../workflows/normal.md)；九种模式的选择、开始按钮文案、输出目录控件和互斥写入见[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)，添加文件与列表状态见[文件列表与输入](../desktop/translation/file-list-and-input.md)，模式对参数的强制覆盖见[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)。

本页只负责汇总和反向链接，不替代各工作流页面与设置页面；检测、OCR、翻译、蒙版、修复、排版、超分与上色的参数算法见对应设置页，不在本页重复。

## 选择器与工作流字段 {#selector-and-workflow-fields}

| 索引 | English | 简体中文 | 开始按钮（English / 简体中文） |
| ---: | --- | --- | --- |
| 0 | Normal Translation | 正常翻译流程 | Start Translation / 开始翻译 |
| 1 | Export Translation | 导出翻译 | Export Translation / 导出翻译 |
| 2 | Export Original Text | 导出原文 | Generate Original Text Template / 仅生成原文模板 |
| 3 | Translate JSON Only | 仅翻译（JSON） | Start JSON Translation / 开始仅翻译（JSON） |
| 4 | Import Translation and Render | 导入翻译并渲染 | Import Translation and Render / 导入翻译并渲染 |
| 5 | Colorize Only | 仅上色 | Start Colorizing / 开始上色 |
| 6 | Upscale Only | 仅超分 | Start Upscaling / 开始超分 |
| 7 | Inpaint Only | 仅修复 | Start Inpainting / 开始修复 |
| 8 | Replace Translation | 替换翻译 | Start Replace Translation / 开始替换翻译 |

## 工作流汇总矩阵 {#workflow-summary-matrix}

下表汇总九种工作流的输入、跳过阶段和输出。“条件执行”表示阶段由常规参数决定（例如 `colorizer.colorizer != none`、`upscale.upscale_ratio` 为真或检测到了文本），不是工作流强制开启；“跳过阶段”只列该工作流路径不调用或明确跳过的部分。详细发现规则、文件命名和跳过条件见矩阵行链接的工作流页面。

| 工作流 | 输入与前置 | 跳过阶段 | 输出 | 详细页面 |
| --- | --- | --- | --- | --- |
| 正常翻译 | 主输入图片；无工作流副文件前置 | 无强制跳过；检测无文本行或 OCR 无文本时提前返回 | 主输出图；`save_text=true` 时写工程 JSON，修复完成时还写修复图；启用上色或超分时写编辑器底图 | [正常翻译流程](../workflows/normal.md) |
| 导出翻译 | 主输入图片与可读取的导出模板 | 修复、渲染、主输出图保存 | 工程 JSON 与 `translations/<stem>_translated.<模板格式>` | [导出翻译](../workflows/export-translation.md) |
| 导出原文 | 主输入图片与可读取的导出模板 | 翻译、修复、渲染、主输出图保存 | 工程 JSON 与 `originals/<stem>_original.<模板格式>` | [导出原文](../workflows/export-original.md) |
| 仅翻译（JSON） | 主输入图片必须能找到工程 JSON | 上色、超分、检测、OCR、合并、蒙版、修复、渲染 | 回写工程 JSON；成功后删除同图原文副文件；不写主输出图 | [仅翻译（JSON）](../workflows/translate-json-only.md) |
| 导入翻译并渲染 | 主输入图片必须有工程 JSON；开始前优先原文副文件，否则用译文副文件 | 上色、超分、检测、OCR、翻译、文本行合并（JSON 无蒙版且导入 YOLO 标签时检测除外） | 主输出图与更新后的工程 JSON；重新修复时写修复图 | [导入翻译并渲染](../workflows/import-translation-and-render.md) |
| 仅上色 | 主输入图片；是否上色由 `colorizer.colorizer` 决定 | 超分、检测、OCR、合并、翻译、蒙版、修复、渲染 | 主输出图；上色器有效时写编辑器底图 | [仅上色](../workflows/colorize-only.md) |
| 仅超分 | 主输入图片；实际倍率来自 `upscale.upscale_ratio` | 检测、OCR、合并、翻译、蒙版、修复、渲染 | 主输出图；启用上色或倍率时写编辑器底图 | [仅超分](../workflows/upscale-only.md) |
| 仅修复 | 主输入图片 | OCR、翻译、渲染 | 主输出图；该分支清空 `text_regions`，不以翻译文本渲染 | [仅修复](../workflows/inpaint-only.md) |
| 替换翻译 | 主输入为生肉图；同图工作目录 `translated_images/` 下必须有同名翻译图 | 翻译服务调用 | 主输出图；非直接粘贴且 `save_text=true` 时另写修复图和工程 JSON，直接粘贴时不写二者也不导出 PSD | [替换翻译](../workflows/replace-translation.md) |

## 互斥、并发与参数边界 {#mutual-exclusion-and-concurrency}

- GUI 切换保证八个工作流布尔字段互斥，但不校验手工 JSON、服务请求或其他入口提供的组合。`sync_workflow_mode_from_config()` 读取已有组合时的显示优先级为：替换翻译、仅修复、仅超分、仅上色、导入翻译、仅翻译 JSON、导出原文、导出翻译、正常。`translate_batch()` 的入口先执行 `load_text` 的 TXT 预导入，再按 `replace_translation` → `load_text` → `translate_json_only` → 常规预处理（`colorize_only` 比 `upscale_only`/`inpaint_only` 更早返回）→ 导出原文 → 导出翻译的顺序分派；手工叠加模式没有“同时执行”的契约。
- `batch_concurrent` 仅正常翻译可进入并发管线；其余八种模式在桌面控制层和核心 `translate_batch()` 中都被视为不兼容，前端会把本次局部变量改为非并发，核心分支也只在“无不兼容模式”时构建 `ConcurrentPipeline`。
- `render.enable_template_alignment`（“启用直接粘贴模式”）是替换翻译专用：开启时走直接粘贴，不写 JSON、修复图或 PSD；关闭时以 OCR 得到的配对区域重新渲染。

## 相关页面 {#related-pages}

| 页面 | 与本页的关系 |
| --- | --- |
| [输出目录与工作流](../desktop/translation/output-directory-and-workflow.md) | 九种模式的选择操作、按钮文案、输出目录控件与互斥写入的总览入口 |
| [文件列表与输入](../desktop/translation/file-list-and-input.md) | 所有工作流共用的主输入发现规则（递归、自然排序、跳过 `manga_translator_work`） |
| [进度、停止与任务状态](../desktop/translation/progress-stop-and-task-state.md) | 所有工作流共用的开始、停止、取消与进度状态 |
| [模式专用工作流与模板对齐](../desktop/settings/mode-specific.md) | 替换翻译等模式的强制参数覆盖与模板对齐 |
| [CLI 批量与输出](../desktop/settings/cli-batch-and-output.md) | `batch_size`/`batch_concurrent` 与输出格式，并发只对正常模式开放 |
| [超分与上色](../desktop/settings/upscale-and-colorization.md) | 条件上色/超分的前置参数（上色器、倍率） |

九个工作流页面的反向链接位于矩阵“详细页面”列；各工作流页面之间还互相链接（例如模板/JSON 家族与旁路工作流）。

## 开发指南 {#developer-guide}

### 选项中英对照 {#option-matrix}

“翻译流程模式：”下拉框按索引建立，索引同时是 `on_workflow_mode_changed()` 写入配置的映射依据。切换模式时，GUI 先把八个互斥的 `cli` 工作流字段全部清为 `false`，再只设置所选模式对应的字段并保存配置，因此 GUI 的一次选择是互斥的；“导出原文”另外依赖 `cli.save_text`，见[互斥、并发与参数边界](#mutual-exclusion-and-concurrency)。

| 索引 | English | 简体中文 | 存储值 | 开始按钮（English / 简体中文） |
| ---: | --- | --- | --- | --- |
| 0 | Normal Translation | 正常翻译流程 | 八个工作流字段均为 `false` | Start Translation / 开始翻译 |
| 1 | Export Translation | 导出翻译 | `generate_and_export=true` | Export Translation / 导出翻译 |
| 2 | Export Original Text | 导出原文 | `template=true` | Generate Original Text Template / 仅生成原文模板 |
| 3 | Translate JSON Only | 仅翻译（JSON） | `translate_json_only=true` | Start JSON Translation / 开始仅翻译（JSON） |
| 4 | Import Translation and Render | 导入翻译并渲染 | `load_text=true` | Import Translation and Render / 导入翻译并渲染 |
| 5 | Colorize Only | 仅上色 | `colorize_only=true` | Start Colorizing / 开始上色 |
| 6 | Upscale Only | 仅超分 | `upscale_only=true` | Start Upscaling / 开始超分 |
| 7 | Inpaint Only | 仅修复 | `inpaint_only=true` | Start Inpainting / 开始修复 |
| 8 | Replace Translation | 替换翻译 | `replace_translation=true` | Start Replace Translation / 开始替换翻译 |

九个模式的调用 key 与 English 实际值相同（都是运行时代码传给 `_t()` 的字符串，不是 `label_*` 设置项 key）；简体中文列核对自 `desktop_qt_ui/locales/zh_CN.json`。下拉框没有独立 `userData`，索引就是模式值。

跨模式参数对工作流分支的边界如下表：

| 参数 | 生效/被忽略的边界 |
| --- | --- |
| `cli.save_text` | GUI/发行默认值为 `true`；是“导出原文”进入导出分支的必要条件，还控制普通工作流的 JSON、修复图和编辑器工程写入；仅翻译 JSON 自己无条件回写 JSON |
| `colorizer.colorizer` | 不是“仅上色”的强制值；正常、仅超分、仅修复和替换翻译也会在其不为 `none` 时先上色 |
| `upscale.upscale_ratio` | 不是“仅超分”的强制值；为空时仅超分原样通过（或保留前置上色结果）；正常、仅修复和替换翻译也会在其为真时先超分 |
| `detector.import_yolo_labels` | 导出原文/导出翻译跳过蒙版细化和蒙版保存；导入翻译且 JSON 无蒙版时触发检测补蒙版 |
| `render.paste_mask_dilation_pixels` | 只消费在替换翻译的直接粘贴分支，膨胀粘贴蒙版 |
| `cli.overwrite` | GUI 开始前按工作流检查既有副文件或主输出图：导出原文/翻译检查对应 TXT，仅翻译 JSON 检查原文副文件，其他模式检查主输出图 |

仅翻译 JSON 的“原文副文件不存在则跳过”条件方向与通常覆盖检查不同，需运行验证；九种模式的真实 GUI 弹窗、取消后文件保留和错误提示也以研究资料的未验证清单为准。

### 关联文件与格式

#### 每图工作目录与文件命名 {#per-image-work-directory}

除主输出图外，各工作流的副文件都以输入图片的原始路径和不含扩展名的 `<stem>` 为基准定位到每图工作目录；JSON 查找先用新位置，再回退旧的图片同级位置。模板导出/导入的副文件扩展名取 `config/translation_template.json` 首个 `output_format:` 行，合法值为安全的 1–32 字符扩展名；缺失或非法时回退 `json`。

| 资源 | 新位置 / 文件名 | 兼容或优先级 |
| --- | --- | --- |
| 译文工程 JSON | `manga_translator_work/json/<stem>_translations.json` | 回退 `<图片目录>/<stem>_translations.json` |
| 原文导出 | `manga_translator_work/originals/<stem>_original.<模板格式>` | 模板未指定或不可读时格式为 `json` |
| 译文导出 | `manga_translator_work/translations/<stem>_translated.<模板格式>` | 同上 |
| 修复图 | `manga_translator_work/inpainted/<stem>_inpainted.<原扩展名>` | 无其他查找位置 |
| 上色/超分编辑器底图 | `manga_translator_work/editor_base/<原文件名>` | 兼容旧工作目录根部的同名底图 |
| 替换翻译配对图 | `manga_translator_work/translated_images/<stem><扩展名>` | 先同扩展名，后遍历 `SUPPORTED_IMAGE_EXTENSIONS` |

主输出图由 `MangaTranslator._calculate_output_path()` 决定：正常输出目录保留输入文件夹名与相对层级；`save_to_source_dir=true` 时改为原图同级 `manga_translator_work/result/`；`cli.format` 为空或 `none` 时保留原扩展名，否则使用给定扩展名。

### 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| UI 布局 | `desktop_qt_ui/ui/main_page/pages/translation_page.py:27` | 翻译页、工作流下拉、开始按钮及事件连接 |
| 工作流状态与写入 | `desktop_qt_ui/ui/main_page/runtime.py:21,151-238` | 九个索引、提示、互斥字段写入、配置同步优先级和按钮文本 |
| i18n | `desktop_qt_ui/locales/en_US.json:488`; `desktop_qt_ui/locales/zh_CN.json:486` | 工作流、开始按钮、提示与设置实际值 |
| 输入与发现 | `desktop_qt_ui/services/file_service.py:31` | 主输入扩展名验证、递归发现、自然排序和工作目录排除 |
| 控制层 | `desktop_qt_ui/app_logic.py:3094` | 主输出路径传递、覆盖前检查和特殊模式并发禁用 |
| Qt 配置 | `desktop_qt_ui/core/config_models.py:123` | 工作流字段与 `save_text` 默认值 |
| 核心分派 | `manga_translator/manga_translator.py:504,3399,4236,5206` | 主输出、JSON 写入、模板导出、TXT 导入和九种运行时分派 |
| 路径/模板 | `manga_translator/utils/path_manager.py:12`; `manga_translator/utils/translation_template.py:10` | 每图工作目录、副文件发现和 `output_format` 回退 |
| 替换翻译 | `manga_translator/utils/replace_translation.py:128,726` | 双图处理、配对、直接粘贴和输出边界 |
| 研究资料 | `doc/wiki/research/workflow-matrix-source-evidence.md` | 九个工作流的输入、阶段、输出与未验证清单 |
