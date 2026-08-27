---
title: 界面选项对照表
description: 汇总桌面端所有固定选项的存储值、English 与简体中文，并链接到对应功能页面
pageId: reference.options-i18n-matrix
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 界面选项对照表

当需要查找某个下拉框或枚举选项实际存进配置的值，以及界面显示的英文和简体中文时，本页提供汇总矩阵。它只负责把固定选项的“存储值 → English → 简体中文”集中列出并反链到对应功能页；每个选项的默认值、影响阶段、依赖和最终消费者以对应功能页为准。

本页是参考索引，不替代任何功能页面：设置参数见[设置索引](./settings-index.md)与各设置页，工作流映射见[工作流矩阵](./workflow-matrix.md)，翻译器与 API 槽轮换的边界见[翻译器选择](../desktop/translator/selection-and-languages.md)和[API 通道与轮询策略](../desktop/api-management/slots-and-rotation.md)。

## 收录内容 {#feature-boundary}

- 只收录桌面 Qt UI 中由源码固定生成的下拉框、枚举和模式选择器；字体、模型名、预设和方案名等运行时列表见[运行时列表](#runtime-lists)。
- 只汇总选项的存储值与双语显示值，不重复每个功能页的 UI 操作、默认值矩阵和运行机理。
- 数值输入、开关、文件编辑动作等非枚举控件不进入本矩阵；它们仍在对应功能页说明。
- 这里不展示任何真实密钥、令牌、用户名、私有路径或用户配置内容。

## 矩阵口径 {#matrix-conventions}

- 存储值：写回配置、环境变量或编辑器 JSON 的值；若控件没有 `userData`，记录实际使用的索引或文字。
- English / 简体中文：来自 `desktop_qt_ui/locales/en_US.json` 与 `zh_CN.json` 的实际显示值；`—` 表示没有 locale key，控件直接显示源码字面量，两种语言相同。
- “同上”表示与上一行相同的控件；各小节的链接指向该选项的权威页面。
- 模型、语言等选项在语言切换后按映射重新填充，因此“实际显示值”是语言切换后的最终值。

## 设置页：通用与应用 {#app-general-options}

主题、界面语言和应用级开关位于设置页“General”分组，完整说明见[通用与应用设置](../desktop/settings/general-and-app.md)。

### 主题与界面语言 {#theme-and-language}

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `light` | Light | 浅色 | `app.theme`，设置 General |
| `dark` | Dark | 深色 | 同上 |
| `gray` | Gray | 灰色 | 同上 |
| `ocean` | Ocean | 海洋 | 同上 |
| `forest` | Forest | 森林 | 同上 |
| `sunset` | Sunset | 落日 | 同上 |
| `rose` | Rose | 玫瑰 | 同上 |
| `system` | Follow System | 跟随系统 | 同上 |
| `auto` | Auto-detected language | 自动检测语言 | `app.ui_language` 启动时按系统 locale 选择；不是语言下拉的固定项 |
| `zh_CN` | 简体中文 | 简体中文 | `app.ui_language`，语言下拉（`LocaleInfo.name` 原生名称） |
| `zh_TW` | 繁體中文 | 繁體中文 | 同上 |
| `en_US` | English | English | 同上 |
| `ja_JP` | 日本語 | 日本語 | 同上 |
| `ko_KR` | 한국어 | 한국어 | 同上 |
| `es_ES` | Español | Español | 同上 |

### 输出格式与批量参数 {#format-and-batch}

`cli.format` 是输出格式下拉；批量大小、并发和重试次数是整数输入，不进入选项矩阵。完整说明见[CLI、批量与输出](../desktop/settings/cli-batch-and-output.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `Not Specified` | Not Specified | 不指定 | `cli.format`，设置 General；发行配置可能存本地化文本 `不指定` |
| `png` | png | png | 同上 |
| `jpg` | jpg | jpg | 同上 |
| `jpeg` | jpeg | jpeg | 同上 |
| `jfif` | jfif | jfif | 同上 |
| `webp` | webp | webp | 同上 |
| `avif` | avif | avif | 同上 |
| `bmp` | bmp | bmp | 同上 |
| `tiff` | tiff | tiff | 同上 |
| `tif` | tif | tif | 同上 |
| `heic` | heic | heic | 同上 |
| `heif` | heif | heif | 同上 |

## 设置页：检测、OCR 与过滤 {#detection-ocr-filter}

### 检测器 {#detector-options}

`detector.detector` 是文本检测器下拉；完整说明见[检测设置](../desktop/settings/detection.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `default` | default | default | `detector.detector`，设置 Detection |
| `dbconvnext` | dbconvnext | dbconvnext | 同上 |
| `ctd` | ctd | ctd | 同上 |
| `craft` | craft | craft | 同上 |
| `none` | none | none | 同上 |

### OCR 引擎与 VLM OCR 语言提示 {#ocr-options}

`ocr.ocr`、`ocr.secondary_ocr` 与 `ocr.ocr_vl_language_hint` 是下拉；编辑器属性面板复用 OCR 映射。完整说明见[OCR 过滤与合并](../desktop/settings/ocr-filter-and-merge.md)与[区域列表与文本编辑](../desktop/editor/region-list-and-text-editing.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `32px` | 32px | 32px | `ocr.ocr` / `ocr.secondary_ocr`，设置 OCR |
| `48px` | 48px | 48px | 同上 |
| `48px_ctc` | 48px_ctc | 48px_ctc | 同上 |
| `mocr` | mocr | mocr | 同上 |
| `paddleocr` | paddleocr | paddleocr | 同上 |
| `paddleocr_korean` | paddleocr_korean | paddleocr_korean | 同上 |
| `paddleocr_latin` | paddleocr_latin | paddleocr_latin | 同上 |
| `paddleocr_thai` | paddleocr_thai | paddleocr_thai | 同上 |
| `paddleocr_vl` | paddleocr_vl | paddleocr_vl | 同上 |
| `hayai_ocr_v2` | hayai_ocr_v2 | hayai_ocr_v2 | 同上 |
| `openai_ocr` | openai_ocr | openai_ocr | 同上 |
| `gemini_ocr` | gemini_ocr | gemini_ocr | 同上 |
| `auto` | Auto | 自动 | `ocr.ocr_vl_language_hint`，设置 OCR |
| `multilingual` | Multilingual | 多语言 | 同上 |
| `Arabic` | Arabic | 阿拉伯语 | 同上 |
| `Simplified Chinese` | Simplified Chinese | 简体中文 | 同上 |
| `Traditional Chinese` | Traditional Chinese | 繁体中文 | 同上 |
| `English` | English | 英语 | 同上 |
| `Japanese` | Japanese | 日语 | 同上 |
| `Korean` | Korean | 韩语 | 同上 |
| `Spanish` | Spanish | 西班牙语 | 同上 |
| `French` | French | 法语 | 同上 |
| `German` | German | 德语 | 同上 |
| `Russian` | Russian | 俄语 | 同上 |
| `Portuguese` | Portuguese | 葡萄牙语 | 同上 |
| `Italian` | Italian | 意大利语 | 同上 |
| `Thai` | Thai | 泰语 | 同上 |
| `Vietnamese` | Vietnamese | 越南语 | 同上 |
| `Indonesian` | Indonesian | 印尼语 | 同上 |
| `Turkish` | Turkish | 土耳其语 | 同上 |
| `Polish` | Polish | 波兰语 | 同上 |
| `Ukrainian` | Ukrainian | 乌克兰语 | 同上 |

## 翻译器与语言 {#translator-and-languages}

翻译器、目标语言和保留语言的下拉在设置“翻译”分组、API 管理翻译功能页和编辑器属性面板中复用；完整说明见[翻译器选择](../desktop/translator/selection-and-languages.md)、[翻译设置](../desktop/settings/translation.md)与[API 功能选择器](../desktop/api-management/feature-selectors.md)。

### 翻译器 {#translator-options}

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `openai` | OpenAI | OpenAI | `translator.translator`，设置 / API 管理 / 编辑器 |
| `openai_hq` | OpenAI High Quality | OpenAI高质量翻译 | 同上 |
| `gemini` | Google Gemini | Google Gemini | 同上 |
| `gemini_hq` | Gemini High Quality | Gemini高质量翻译 | 同上 |
| `sakura` | Sakura | Sakura | 同上 |
| `none` | None | 无 | 同上 |
| `original` | Original | 原文 | 同上 |

### 目标语言与保留语言 {#languages-options}

`translator.target_lang` 是目标语言下拉；`translator.keep_lang` 除全部目标语言外还额外包含 `none` 和六个仅在保留语言中出现的语言。完整说明见[翻译器选择](../desktop/translator/selection-and-languages.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `CHS` | Simplified Chinese | 简体中文 | `translator.target_lang`，设置 / 编辑器 |
| `CHT` | Traditional Chinese | 繁体中文 | 同上 |
| `CSY` | Czech | 捷克语 | 同上 |
| `NLD` | Dutch | 荷兰语 | 同上 |
| `ENG` | English | 英语 | 同上 |
| `FRA` | French | 法语 | 同上 |
| `DEU` | German | 德语 | 同上 |
| `HUN` | Hungarian | 匈牙利语 | 同上 |
| `ITA` | Italian | 意大利语 | 同上 |
| `JPN` | Japanese | 日语 | 同上 |
| `KOR` | Korean | 韩语 | 同上 |
| `POL` | Polish | 波兰语 | 同上 |
| `PTB` | Portuguese (Brazil) | 葡萄牙语（巴西） | 同上 |
| `ROM` | Romanian | 罗马尼亚语 | 同上 |
| `RUS` | Russian | 俄语 | 同上 |
| `ESP` | Spanish | 西班牙语 | 同上 |
| `TRK` | Turkish | 土耳其语 | 同上 |
| `UKR` | Ukrainian | 乌克兰语 | 同上 |
| `VIN` | Vietnamese | 越南语 | 同上 |
| `ARA` | Arabic | 阿拉伯语 | 同上 |
| `SRP` | Serbian | 塞尔维亚语 | 同上 |
| `HRV` | Croatian | 克罗地亚语 | 同上 |
| `THA` | Thai | 泰语 | 同上 |
| `IND` | Indonesian | 印度尼西亚语 | 同上 |
| `FIL` | Filipino (Tagalog) | 菲律宾语（他加禄语） | 同上 |

`translator.keep_lang` 在上述 `target_lang` 全部值之外额外提供（数据源是 `KEEP_LANGUAGES`，而不是 UI 显示文本的反向推导）：

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `none` | No Filter | 不过滤 | `translator.keep_lang`，设置 Translation |
| `SWE` | Swedish | 瑞典语 | 同上 |
| `DAN` | Danish | 丹麦语 | 同上 |
| `NOR` | Norwegian | 挪威语 | 同上 |
| `FIN` | Finnish | 芬兰语 | 同上 |
| `MSA` | Malay | 马来语 | 同上 |
| `CAT` | Catalan | 加泰罗尼亚语 | 同上 |

## 蒙版、修复与排版 {#inpainting-and-typesetting}

### 修复模型与精度 {#inpainter-options}

`inpainter.inpainter` 与 `inpainter.inpainting_precision` 是下拉；完整说明见[蒙版与修复设置](../desktop/settings/mask-and-inpainting.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `default` | default | default | `inpainter.inpainter`，设置 Inpainting |
| `lama_large` | lama_large | lama_large | 同上 |
| `lama_mpe` | lama_mpe | lama_mpe | 同上 |
| `sd` | sd | sd | 同上 |
| `none` | none | none | 同上 |
| `original` | original | original | 同上 |
| `fp32` | fp32 | fp32 | `inpainter.inpainting_precision`，设置 Inpainting |
| `fp16` | fp16 | fp16 | 同上 |
| `bf16` | bf16 | bf16 | 同上 |

### 渲染器、对齐、方向与布局 {#render-options}

`render.renderer`、`render.alignment`、`render.direction` 与 `render.layout_mode` 是下拉；编辑器属性面板复用对齐和方向映射。完整说明见[排版与渲染设置](../desktop/settings/typesetting-and-rendering.md)与[文本属性](../desktop/editor/text-properties.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `default` | Default | Default | `render.renderer`，设置 Typesetting / API 管理渲染 |
| `openai_renderer` | OpenAI Renderer | OpenAI Renderer | 同上 |
| `gemini_renderer` | Gemini Renderer | Gemini Renderer | 同上 |
| `none` | None | 无 | 同上 |
| `auto` | Auto | 自动 | `render.alignment`，设置 Typesetting / 编辑器 |
| `left` | Left | 左对齐 | 同上 |
| `center` | Center | 居中 | 同上 |
| `right` | Right | 右对齐 | 同上 |
| `auto` | Auto | 自动 | `render.direction`，设置 Typesetting |
| `h` | Horizontal | 横排 | 编辑器方向属性 |
| `v` | Vertical | 竖排 | 编辑器方向属性 |
| `horizontal` | Horizontal | 横排 | `render.direction` 核心 enum（UI 反向映射写 `h`） |
| `vertical` | Vertical | 竖排 | `render.direction` 核心 enum（UI 反向映射写 `v`） |
| `smart_scaling` | Smart Scaling | 智能缩放 | `render.layout_mode`，设置 Typesetting |
| `strict` | Strict Boundary | 严格边界 | 同上 |
| `balloon_fill` | Smart Bubble | 智能气泡 | 同上 |

## 超分与上色 {#upscale-and-colorization}

### 超分模型与倍率 {#upscaler-options}

`upscale.upscaler` 与 `upscale.upscale_ratio` 是下拉；倍率随上色器开关联动，Real-CUGAN 的档位写入 `upscale.realcugan_model`。完整说明见[超分与上色设置](../desktop/settings/upscale-and-colorization.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `waifu2x` | Waifu2x | Waifu2x | `upscale.upscaler`，设置 Mode Specific |
| `esrgan` | ESRGAN | ESRGAN | 同上 |
| `4xultrasharp` | 4x UltraSharp | 4x UltraSharp | 同上 |
| `realcugan` | Real-CUGAN | Real-CUGAN | 同上 |
| `mangajanai` | MangaJaNai | MangaJaNai | 同上 |
| `null` | Not Use | 不使用 | `upscale.upscale_ratio`（前置 `upscaler` 非 `realcugan` / `mangajanai`） |
| `2` | 2 | 2 | 同上 |
| `3` | 3 | 3 | 同上 |
| `4` | 4 | 4 | 同上 |

Real-CUGAN 的倍率下拉（前置 `upscaler=realcugan`）与 MangaJaNai 倍率：

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `null` | Not Use | 不使用 | `upscale.upscale_ratio`，Real-CUGAN / MangaJaNai “不使用” |
| `2x-conservative` | 2x-Conservative | 2倍-保守 | `realcugan` 档位 |
| `2x-conservative-pro` | 2x-Conservative-Pro | 2倍-保守-Pro | 同上 |
| `2x-no-denoise` | 2x-No Denoise | 2倍-无降噪 | 同上 |
| `2x-denoise1x` | 2x-Denoise1x | 2倍-降噪1x | 同上 |
| `2x-denoise2x` | 2x-Denoise2x | 2倍-降噪2x | 同上 |
| `2x-denoise3x` | 2x-Denoise3x | 2倍-降噪3x | 同上 |
| `2x-denoise3x-pro` | 2x-Denoise3x-Pro | 2倍-降噪3x-Pro | 同上 |
| `3x-conservative` | 3x-Conservative | 3倍-保守 | 同上 |
| `3x-conservative-pro` | 3x-Conservative-Pro | 3倍-保守-Pro | 同上 |
| `3x-no-denoise` | 3x-No Denoise | 3倍-无降噪 | 同上 |
| `3x-no-denoise-pro` | 3x-No Denoise-Pro | 3倍-无降噪-Pro | 同上 |
| `3x-denoise3x` | 3x-Denoise3x | 3倍-降噪3x | 同上 |
| `3x-denoise3x-pro` | 3x-Denoise3x-Pro | 3倍-降噪3x-Pro | 同上 |
| `4x-conservative` | 4x-Conservative | 4倍-保守 | 同上 |
| `4x-no-denoise` | 4x-No Denoise | 4倍-无降噪 | 同上 |
| `4x-denoise3x` | 4x-Denoise3x | 4倍-降噪3x | 同上 |
| `x2` | x2 | x2 | `mangajanai` 倍率 |
| `x4` | x4 | x4 | 同上 |
| `DAT2 x4` | DAT2 x4 | DAT2 x4 | 同上 |

### 上色模型 {#colorizer-options}

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `none` | None | 无 | `colorizer.colorizer`，设置 Mode Specific / API 管理上色 |
| `mc2` | Manga Colorization v2 | Manga Colorization v2 | 同上 |
| `openai_colorizer` | OpenAI Colorizer | OpenAI Colorizer | 同上 |
| `gemini_colorizer` | Gemini Colorizer | Gemini Colorizer | 同上 |

## 工作流、API 管理与自定义参数 {#workflow-and-api}

### 翻译工作流 {#workflow-options}

翻译工作区的工作流下拉没有 `userData`，以索引作为实际模式值；切换时只设置对应的一个互斥 CLI 标志。九个工作流的输入输出和跳过阶段见[工作流矩阵](./workflow-matrix.md)、[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)与 `workflows/` 各页。

| 存储值 | English | 简体中文 | 对应 CLI 标志 |
| --- | --- | --- | --- |
| `0` | Normal Translation | 正常翻译流程 | — |
| `1` | Export Translation | 导出翻译 | `cli.generate_and_export` |
| `2` | Export Original Text | 导出原文 | `cli.template` |
| `3` | Translate JSON Only | 仅翻译（JSON） | `cli.translate_json_only` |
| `4` | Import Translation and Render | 导入翻译并渲染 | `cli.load_text` |
| `5` | Colorize Only | 仅上色 | `cli.colorize_only` |
| `6` | Upscale Only | 仅超分 | `cli.upscale_only` |
| `7` | Inpaint Only | 仅修复 | `cli.inpaint_only` |
| `8` | Replace Translation | 替换翻译 | `cli.replace_translation` |

### API 轮换策略与自定义参数类型 {#api-options}

API 通道轮换策略下拉，以及自定义 API 参数的类型和布尔值下拉；完整说明见[API 通道与轮询策略](../desktop/api-management/slots-and-rotation.md)与[自定义请求参数](../desktop/api-management/custom-request-parameters.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `failover` | Ordered failover | 按顺序故障切换 | API 通道轮换策略 |
| `round_robin` | Round robin | 轮询 | 同上 |
| `string` | String | 字符串 | 自定义 API 参数类型 |
| `number` | Number | 数值 | 同上 |
| `boolean` | Boolean | 布尔值 | 同上 |
| `null` | Null | 空值 | 同上 |
| `json` | JSON | JSON | 同上 |
| `true` | true | true | 自定义 API 参数布尔值 |
| `false` | false | false | 同上 |

## 编辑器、批量管理与提示词术语 {#editor-batch-terms}

### 富文本推进与批量管理 {#rich-text-and-batch}

富文本“强制推进”（Force Advance）下拉，以及批量管理的方向/对齐枚举、布尔值、逻辑、富文本模式和规则组。完整说明见[富文本样式与预设](../desktop/rich-text-rules/styles-and-presets.md)、[批量条件匹配](../desktop/batch-management/conditions.md)与[批量动作与执行顺序](../desktop/batch-management/actions-and-order.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `half` | Half Advance | 半格推进 | 富文本 Force Advance，编辑器 / 富文本规则 |
| `full` | Full Advance | 全角推进 | 同上 |
| `h` | h | h | 批量条件 / 批量设属性 Direction |
| `v` | v | v | 同上 |
| `hr` | hr | hr | 同上 |
| `vr` | vr | vr | 同上 |
| `auto` | auto | auto | 同上 |
| `left` | left | left | 批量条件 / 批量设属性 Alignment |
| `center` | center | center | 同上 |
| `right` | right | right | 同上 |
| `auto` | auto | auto | 同上 |
| `true` | Yes | 是 | 批量条件布尔值 |
| `false` | No | 否 | 同上 |
| `all` | Match all | 全部满足 | 批量逻辑 |
| `any` | Match any | 任一满足 | 同上 |
| `overwrite` | Overwrite | 覆盖 | 批量富文本模式 |
| `fill` | Fill in | 添加 | 同上 |
| `replace` | Replace | 替换 | 同上 |
| `common` | Common (Always) | 通用（始终执行） | 富文本规则组 |
| `horizontal` | Horizontal | 横排 | 同上 |
| `vertical` | Vertical | 竖排 | 同上 |

### 批量字段与运算符 {#batch-fields-operators}

批量条件与“设置 region 属性”共用字段选择器，后者只显示可写字段；运算符会随字段类型重新填充。完整说明见[批量条件匹配](../desktop/batch-management/conditions.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `translation` | Translation | 翻译 | 批量字段 |
| `text` | Source Text | 原文 | 同上 |
| `translation_raw` | Translation (pre-replacement) | 译文（替换前） | 同上 |
| `font_family` | Font Family | 字体 | 同上 |
| `target_lang` | Target Language | 目标语言 | 同上 |
| `source_lang` | Source Language | 源语言 | 同上 |
| `direction` | Direction | 排版方向 | 同上 |
| `alignment` | Alignment | 对齐 | 同上 |
| `font_size` | Font Size | 绝对字号 | 同上 |
| `angle` | Angle | 角度 | 同上 |
| `line_spacing` | Line Spacing | 行距 | 同上 |
| `letter_spacing` | Letter Spacing | 字距 | 同上 |
| `stroke_width` | Stroke Width | 描边宽度 | 同上 |
| `prob` | OCR Confidence | OCR 置信度 | 同上 |
| `fg_colors` | Text Color | 文字颜色 | 同上 |
| `bg_colors` | Stroke Color | 描边颜色 | 同上 |
| `has_rich_text` | Has Rich Text | 含富文本 | 同上 |
| `line_count` | Line Count | 行数 | 同上 |
| `region_index` | Region Index | 区域序号 | 同上 |
| `contains` | contains | 包含 | 文本字段运算符 |
| `not_contains` | does not contain | 不包含 | 同上 |
| `eq` | equals | 等于 | 同上 |
| `ne` | not equal to | 不等于 | 同上 |
| `regex` | matches regex | 正则匹配 | 同上 |
| `not_regex` | does not match regex | 正则不匹配 | 同上 |
| `empty` | is empty | 为空 | 同上 |
| `not_empty` | is not empty | 不为空 | 同上 |
| `gt` | greater than | 大于 | 数值字段运算符 |
| `gte` | at least | 大于等于 | 同上 |
| `lt` | less than | 小于 | 同上 |
| `lte` | at most | 小于等于 | 同上 |
| `between` | between | 介于 | 同上 |
| `color_eq` | equals color | 颜色等于 | 颜色字段运算符 |
| `color_near` | close to color | 颜色接近 | 同上 |
| `is_true` | is yes | 是 | 布尔字段运算符 |
| `is_false` | is no | 否 | 同上 |

### 提示词术语分类 {#prompt-terms}

术语编辑对话框默认提供以下六种类别；已存在或后续新建的非标准类别会一并出现，因此不把这六项误称为封闭枚举。完整说明见[结构化编辑器与格式](../desktop/prompts/structured-editor-and-format.md)。

| 存储值 | English | 简体中文 | 控件 / 使用页面 |
| --- | --- | --- | --- |
| `Person` | Person | 人物 | 提示词术语分类 |
| `Location` | Location | 地点 | 同上 |
| `Org` | Organization | 组织 | 同上 |
| `Item` | Item | 物品 | 同上 |
| `Skill` | Skill | 技能 | 同上 |
| `Creature` | Creature | 生物 | 同上 |

## 运行时列表 {#runtime-lists}

以下控件的数据来自本机或用户配置，不能生成稳定的“所有选项”表；它们在对应功能页按运行态说明。

| 控件 | 数据源 | 固定清单不可用的原因 |
| --- | --- | --- |
| 设置和编辑器的字体 | `utils/font_list.py` / 系统与项目字体目录 | 取决于本机已安装字体与目录内容 |
| API 管理模型名 | OpenAI/Gemini “Get Models” API 调用 | 取决于凭据、API 地址和远端服务返回值 |
| API 管理与设置页的预设 | `PresetService`、`custom_api_params.json` | 用户可新增、重命名或删除 |
| 提示词选择 | `dict/` 下的提示词文件 | 目录内容可变，且当前实现按文件名显示 |
| 编辑器样式 / 富文本样式 | `app.saved_style_presets`、`app.saved_rich_text_presets` | 用户配置的键名就是显示值和存储值 |
| 批量管理方案 | `config/batch_edit_schemes.yaml` | 用户定义的方案名和数量可变 |

## 使用说明 {#dependencies-and-conflicts}

- 这里仅汇总固定选项；同名设置的不同形态（例如 `render.direction` 的核心 enum `horizontal`/`vertical` 与 UI 反向映射 `h`/`v`）以对应功能页为准。
- 模型名、语言等选项在语言切换后重新填充，矩阵记录的 English/简体中文是切换后的实际显示值。
- 工作流下拉以索引为模式值并同步互斥 CLI 标志，不能把索引当作文本存储值使用。
- API 轮换策略只影响已选定提供商内部的请求端点，不改变翻译器实现；边界见[API 通道与轮询策略](../desktop/api-management/slots-and-rotation.md)。
- 不展示真实密钥、令牌、用户名、私有路径或用户配置内容；共享日志或调试目录前先脱敏。

## 关联文件与格式 {#files-and-formats}

| 文件/目录 | 本页实际作用 | 手改/兼容注意 |
| --- | --- | --- |
| `desktop_qt_ui/locales/en_US.json` / `zh_CN.json` | English 与简体中文实际显示值的来源 | 缺失 key 按 i18n 回退处理；不要凭中文自行翻译 |
| `desktop_qt_ui/app_logic.py` | `get_options_for_key()` / `get_display_mapping()` | 选项与显示映射的单一入口 |
| `desktop_qt_ui/ui/main_page/dynamic_settings.py`、`layout.py` | 普通设置控件、主题/语言下拉与倍率联动 | 特殊控件单独处理，不走通用回退 |
| `manga_translator/config.py`、`manga_translator/image_formats.py` | 核心枚举与输出格式 | enum 字面量直接显示时两种语言相同 |
| `config/config-example.json` | 发行默认值 | 只作模板核对，不复制用户路径或私密内容 |

## 数据来源 {#source-evidence}

| 数据 | 文件 | 用途 |
| --- | --- | --- |
| 选项与显示映射 | `desktop_qt_ui/app_logic.py` | `get_options_for_key()`、`get_display_mapping()` |
| 设置控件 | `desktop_qt_ui/ui/main_page/dynamic_settings.py`、`layout.py` | 普通设置控件、主题/语言下拉、倍率联动 |
| API 下拉与轮换 | `desktop_qt_ui/ui/main_page/env_management.py`、`manga_translator/api_key_rotation.py` | 功能选择器、`ROTATION_STRATEGIES` |
| 核心枚举 | `manga_translator/config.py`、`manga_translator/image_formats.py` | 模型/排版 enum 与输出格式 |
| 语言与 locale | `desktop_qt_ui/services/translation_service.py`、`desktop_qt_ui/services/i18n_service.py`、`desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 目标/保留语言与实际双语值 |
| 工作流 | `desktop_qt_ui/ui/main_page/pages/translation_page.py`、`desktop_qt_ui/ui/main_page/runtime.py` | 九个索引值与 CLI 标志映射 |
| 编辑器与批量 | `desktop_qt_ui/ui/widgets/property_panel.py`、`desktop_qt_ui/ui/widgets/rich_text_editor_components.py`、`desktop_qt_ui/services/batch_edit_engine.py` | 编辑器复用项、富文本推进和批量条件 |
| 调查与生成数据 | `doc/wiki/research/phase0-options-i18n-matrix.md`、`doc/wiki/data/i18n.generated.json`、`doc/wiki/data/settings.generated.json` | 选项清单、界面文案与页面映射 |
