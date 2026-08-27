---
title: UI Options Reference
description: Summary of stored values, English, and Simplified Chinese for every fixed desktop option, with links to the owning pages
pageId: reference.options-i18n-matrix
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# UI Options Reference

Use this page when you need the stored value of a combo box or enum option together with the English and Simplified Chinese text shown in the UI. It only aggregates the fixed “stored value → English → Simplified Chinese” mapping and backlinks to the owning feature pages; defaults, affected stages, dependencies, and final consumers remain on those pages.

This is a reference index and does not replace any feature page: parameters are covered by the [Settings index](./settings-index.md) and each settings page, workflows by the [Workflow matrix](./workflow-matrix.md), and the translator-versus-API-slot boundary by [Translator selection](../desktop/translator/selection-and-languages.md) and [API slots and rotation](../desktop/api-management/slots-and-rotation.md).

## What's included {#feature-boundary}

- Covers only combo boxes, enums, and mode selectors that the desktop Qt UI generates from fixed source options; runtime lists such as fonts, model names, presets, and scheme names are documented in [Runtime lists](#runtime-lists).
- Aggregates stored values and bilingual display values only; it does not repeat the UI operations, default matrix, or runtime behavior of each feature page.
- Non-enum controls such as numeric inputs, toggles, and file-edit actions are not part of this matrix; they remain on the corresponding feature pages.
- This page never exposes real keys, tokens, usernames, private paths, or user configuration content.

## Matrix conventions {#matrix-conventions}

- Stored value: the value written back to configuration, environment variables, or editor JSON; when a control has no `userData`, the actual index or text is recorded.
- English / Simplified Chinese: the actual display values from `desktop_qt_ui/locales/en_US.json` and `zh_CN.json`; `—` means there is no locale key and the control shows the source literal in both languages.
- “Same as above” refers to the control of the previous row; the links in each subsection point to the authoritative page for that option.
- Model and language options are re-populated through their mappings after a language switch, so the recorded display values are the final values after switching.

## Settings: General and app {#app-general-options}

Theme, UI language, and app-level toggles live in the “General” group of the settings page; see [General and app settings](../desktop/settings/general-and-app.md) for details.

### Theme and UI language {#theme-and-language}

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `light` | Light | 浅色 | `app.theme`, Settings General |
| `dark` | Dark | 深色 | Same as above |
| `gray` | Gray | 灰色 | Same as above |
| `ocean` | Ocean | 海洋 | Same as above |
| `forest` | Forest | 森林 | Same as above |
| `sunset` | Sunset | 落日 | Same as above |
| `rose` | Rose | 玫瑰 | Same as above |
| `system` | Follow System | 跟随系统 | Same as above |
| `auto` | Auto-detected language | 自动检测语言 | `app.ui_language` picked from the system locale at startup; not a fixed item in the language combo |
| `zh_CN` | 简体中文 | 简体中文 | `app.ui_language`, language combo (`LocaleInfo.name` native names) |
| `zh_TW` | 繁體中文 | 繁體中文 | Same as above |
| `en_US` | English | English | Same as above |
| `ja_JP` | 日本語 | 日本語 | Same as above |
| `ko_KR` | 한국어 | 한국어 | Same as above |
| `es_ES` | Español | Español | Same as above |

### Output format and batch parameters {#format-and-batch}

`cli.format` is the output-format combo; batch size, concurrency, and retry attempts are integer inputs and are not part of the option matrix. See [CLI, batch, and output](../desktop/settings/cli-batch-and-output.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `Not Specified` | Not Specified | 不指定 | `cli.format`, Settings General; the release config may store the localized text `不指定` |
| `png` | png | png | Same as above |
| `jpg` | jpg | jpg | Same as above |
| `jpeg` | jpeg | jpeg | Same as above |
| `jfif` | jfif | jfif | Same as above |
| `webp` | webp | webp | Same as above |
| `avif` | avif | avif | Same as above |
| `bmp` | bmp | bmp | Same as above |
| `tiff` | tiff | tiff | Same as above |
| `tif` | tif | tif | Same as above |
| `heic` | heic | heic | Same as above |
| `heif` | heif | heif | Same as above |

## Settings: Detection, OCR, and filtering {#detection-ocr-filter}

### Detector {#detector-options}

`detector.detector` is the text-detector combo; see [Detection settings](../desktop/settings/detection.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `default` | default | default | `detector.detector`, Settings Detection |
| `dbconvnext` | dbconvnext | dbconvnext | Same as above |
| `ctd` | ctd | ctd | Same as above |
| `craft` | craft | craft | Same as above |
| `none` | none | none | Same as above |

### OCR engines and VLM OCR language hints {#ocr-options}

`ocr.ocr`, `ocr.secondary_ocr`, and `ocr.ocr_vl_language_hint` are Settings-page combos. The editor property panel reuses the OCR option list, but its current choice is stored separately as `app.editor_ocr`, defaulting to `mocr`, and does not overwrite homepage `ocr.ocr`. See [OCR filter and merge](../desktop/settings/ocr-filter-and-merge.md) and [Region list and text editing](../desktop/editor/region-list-and-text-editing.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `32px` | 32px | 32px | `ocr.ocr` / `ocr.secondary_ocr`, Settings OCR |
| `48px` | 48px | 48px | Same as above |
| `48px_ctc` | 48px_ctc | 48px_ctc | Same as above |
| `mocr` | mocr | mocr | Same as above |
| `paddleocr` | paddleocr | paddleocr | Same as above |
| `paddleocr_korean` | paddleocr_korean | paddleocr_korean | Same as above |
| `paddleocr_latin` | paddleocr_latin | paddleocr_latin | Same as above |
| `paddleocr_thai` | paddleocr_thai | paddleocr_thai | Same as above |
| `paddleocr_vl` | paddleocr_vl | paddleocr_vl | Same as above |
| `hayai_ocr_v2` | hayai_ocr_v2 | hayai_ocr_v2 | Same as above |
| `openai_ocr` | openai_ocr | openai_ocr | Same as above |
| `gemini_ocr` | gemini_ocr | gemini_ocr | Same as above |
| `auto` | Auto | 自动 | `ocr.ocr_vl_language_hint`, Settings OCR |
| `multilingual` | Multilingual | 多语言 | Same as above |
| `Arabic` | Arabic | 阿拉伯语 | Same as above |
| `Simplified Chinese` | Simplified Chinese | 简体中文 | Same as above |
| `Traditional Chinese` | Traditional Chinese | 繁体中文 | Same as above |
| `English` | English | 英语 | Same as above |
| `Japanese` | Japanese | 日语 | Same as above |
| `Korean` | Korean | 韩语 | Same as above |
| `Spanish` | Spanish | 西班牙语 | Same as above |
| `French` | French | 法语 | Same as above |
| `German` | German | 德语 | Same as above |
| `Russian` | Russian | 俄语 | Same as above |
| `Portuguese` | Portuguese | 葡萄牙语 | Same as above |
| `Italian` | Italian | 意大利语 | Same as above |
| `Thai` | Thai | 泰语 | Same as above |
| `Vietnamese` | Vietnamese | 越南语 | Same as above |
| `Indonesian` | Indonesian | 印尼语 | Same as above |
| `Turkish` | Turkish | 土耳其语 | Same as above |
| `Polish` | Polish | 波兰语 | Same as above |
| `Ukrainian` | Ukrainian | 乌克兰语 | Same as above |

## Translators and languages {#translator-and-languages}

The Settings page, API-management translation tab, and editor property panel reuse the translator option list. Settings and API management use `translator.translator`; the editor uses the separate `app.editor_translator`, defaulting to `openai`. Target and keep languages remain shared with the homepage translation settings. See [Translator selection](../desktop/translator/selection-and-languages.md), [Translation settings](../desktop/settings/translation.md), and [API feature selectors](../desktop/api-management/feature-selectors.md) for details.

### Translator {#translator-options}

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `openai` | OpenAI | OpenAI | `translator.translator`, Settings / API management / Editor |
| `openai_hq` | OpenAI High Quality | OpenAI高质量翻译 | Same as above |
| `gemini` | Google Gemini | Google Gemini | Same as above |
| `gemini_hq` | Gemini High Quality | Gemini高质量翻译 | Same as above |
| `sakura` | Sakura | Sakura | Same as above |
| `none` | None | 无 | Same as above |
| `original` | Original | 原文 | Same as above |

### Target and keep languages {#languages-options}

`translator.target_lang` is the target-language combo; `translator.keep_lang` additionally offers `none` and six languages that exist only in the keep list. See [Translator selection](../desktop/translator/selection-and-languages.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `CHS` | Simplified Chinese | 简体中文 | `translator.target_lang`, Settings / Editor |
| `CHT` | Traditional Chinese | 繁体中文 | Same as above |
| `CSY` | Czech | 捷克语 | Same as above |
| `NLD` | Dutch | 荷兰语 | Same as above |
| `ENG` | English | 英语 | Same as above |
| `FRA` | French | 法语 | Same as above |
| `DEU` | German | 德语 | Same as above |
| `HUN` | Hungarian | 匈牙利语 | Same as above |
| `ITA` | Italian | 意大利语 | Same as above |
| `JPN` | Japanese | 日语 | Same as above |
| `KOR` | Korean | 韩语 | Same as above |
| `POL` | Polish | 波兰语 | Same as above |
| `PTB` | Portuguese (Brazil) | 葡萄牙语（巴西） | Same as above |
| `ROM` | Romanian | 罗马尼亚语 | Same as above |
| `RUS` | Russian | 俄语 | Same as above |
| `ESP` | Spanish | 西班牙语 | Same as above |
| `TRK` | Turkish | 土耳其语 | Same as above |
| `UKR` | Ukrainian | 乌克兰语 | Same as above |
| `VIN` | Vietnamese | 越南语 | Same as above |
| `ARA` | Arabic | 阿拉伯语 | Same as above |
| `SRP` | Serbian | 塞尔维亚语 | Same as above |
| `HRV` | Croatian | 克罗地亚语 | Same as above |
| `THA` | Thai | 泰语 | Same as above |
| `IND` | Indonesian | 印度尼西亚语 | Same as above |
| `FIL` | Filipino (Tagalog) | 菲律宾语（他加禄语） | Same as above |

`translator.keep_lang` additionally offers the following values beyond the full `target_lang` set (sourced from `KEEP_LANGUAGES`, not derived backwards from display text):

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `none` | No Filter | 不过滤 | `translator.keep_lang`, Settings Translation |
| `SWE` | Swedish | 瑞典语 | Same as above |
| `DAN` | Danish | 丹麦语 | Same as above |
| `NOR` | Norwegian | 挪威语 | Same as above |
| `FIN` | Finnish | 芬兰语 | Same as above |
| `MSA` | Malay | 马来语 | Same as above |
| `CAT` | Catalan | 加泰罗尼亚语 | Same as above |

## Mask, inpainting, and typesetting {#inpainting-and-typesetting}

### Inpainting model and precision {#inpainter-options}

`inpainter.inpainter` and `inpainter.inpainting_precision` are combos; see [Mask and inpainting settings](../desktop/settings/mask-and-inpainting.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `default` | default | default | `inpainter.inpainter`, Settings Inpainting |
| `lama_large` | lama_large | lama_large | Same as above |
| `lama_mpe` | lama_mpe | lama_mpe | Same as above |
| `sd` | sd | sd | Same as above |
| `none` | none | none | Same as above |
| `original` | original | original | Same as above |
| `fp32` | fp32 | fp32 | `inpainter.inpainting_precision`, Settings Inpainting |
| `fp16` | fp16 | fp16 | Same as above |
| `bf16` | bf16 | bf16 | Same as above |

### Renderer, alignment, direction, and layout {#render-options}

`render.renderer`, `render.alignment`, `render.direction`, and `render.layout_mode` are combos; the editor property panel reuses the alignment and direction mappings. See [Typesetting and rendering settings](../desktop/settings/typesetting-and-rendering.md) and [Text properties](../desktop/editor/text-properties.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `default` | Default | Default | `render.renderer`, Settings Typesetting / API-management rendering |
| `openai_renderer` | OpenAI Renderer | OpenAI Renderer | Same as above |
| `gemini_renderer` | Gemini Renderer | Gemini Renderer | Same as above |
| `none` | None | 无 | Same as above |
| `auto` | Auto | 自动 | `render.alignment`, Settings Typesetting / Editor |
| `left` | Left | 左对齐 | Same as above |
| `center` | Center | 居中 | Same as above |
| `right` | Right | 右对齐 | Same as above |
| `auto` | Auto | 自动 | `render.direction`, Settings Typesetting |
| `h` | Horizontal | 横排 | Editor direction property |
| `v` | Vertical | 竖排 | Editor direction property |
| `horizontal` | Horizontal | 横排 | `render.direction` core enum (UI reverse-maps to `h`) |
| `vertical` | Vertical | 竖排 | `render.direction` core enum (UI reverse-maps to `v`) |
| `smart_scaling` | Smart Scaling | 智能缩放 | `render.layout_mode`, Settings Typesetting |
| `strict` | Strict Boundary | 严格边界 | Same as above |
| `balloon_fill` | Smart Bubble | 智能气泡 | Same as above |

## Upscaling and colorization {#upscale-and-colorization}

### Upscaler and ratio {#upscaler-options}

`upscale.upscaler` and `upscale.upscale_ratio` are combos; the ratio list depends on the upscaler, and the Real-CUGAN model is written to `upscale.realcugan_model`. See [Upscale and colorization settings](../desktop/settings/upscale-and-colorization.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `waifu2x` | Waifu2x | Waifu2x | `upscale.upscaler`, Settings Mode Specific |
| `esrgan` | ESRGAN | ESRGAN | Same as above |
| `4xultrasharp` | 4x UltraSharp | 4x UltraSharp | Same as above |
| `realcugan` | Real-CUGAN | Real-CUGAN | Same as above |
| `mangajanai` | MangaJaNai | MangaJaNai | Same as above |
| `null` | Not Use | 不使用 | `upscale.upscale_ratio` (upscaler other than `realcugan` / `mangajanai`) |
| `2` | 2 | 2 | Same as above |
| `3` | 3 | 3 | Same as above |
| `4` | 4 | 4 | Same as above |

The Real-CUGAN ratio combo (with `upscaler=realcugan`) and the MangaJaNai ratios:

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `null` | Not Use | 不使用 | `upscale.upscale_ratio`, “Not Use” for Real-CUGAN / MangaJaNai |
| `2x-conservative` | 2x-Conservative | 2倍-保守 | `realcugan` model |
| `2x-conservative-pro` | 2x-Conservative-Pro | 2倍-保守-Pro | Same as above |
| `2x-no-denoise` | 2x-No Denoise | 2倍-无降噪 | Same as above |
| `2x-denoise1x` | 2x-Denoise1x | 2倍-降噪1x | Same as above |
| `2x-denoise2x` | 2x-Denoise2x | 2倍-降噪2x | Same as above |
| `2x-denoise3x` | 2x-Denoise3x | 2倍-降噪3x | Same as above |
| `2x-denoise3x-pro` | 2x-Denoise3x-Pro | 2倍-降噪3x-Pro | Same as above |
| `3x-conservative` | 3x-Conservative | 3倍-保守 | Same as above |
| `3x-conservative-pro` | 3x-Conservative-Pro | 3倍-保守-Pro | Same as above |
| `3x-no-denoise` | 3x-No Denoise | 3倍-无降噪 | Same as above |
| `3x-no-denoise-pro` | 3x-No Denoise-Pro | 3倍-无降噪-Pro | Same as above |
| `3x-denoise3x` | 3x-Denoise3x | 3倍-降噪3x | Same as above |
| `3x-denoise3x-pro` | 3x-Denoise3x-Pro | 3倍-降噪3x-Pro | Same as above |
| `4x-conservative` | 4x-Conservative | 4倍-保守 | Same as above |
| `4x-no-denoise` | 4x-No Denoise | 4倍-无降噪 | Same as above |
| `4x-denoise3x` | 4x-Denoise3x | 4倍-降噪3x | Same as above |
| `x2` | x2 | x2 | `mangajanai` ratio |
| `x4` | x4 | x4 | Same as above |
| `DAT2 x4` | DAT2 x4 | DAT2 x4 | Same as above |

### Colorizer {#colorizer-options}

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `none` | None | 无 | `colorizer.colorizer`, Settings Mode Specific / API-management colorization |
| `mc2` | Manga Colorization v2 | Manga Colorization v2 | Same as above |
| `openai_colorizer` | OpenAI Colorizer | OpenAI Colorizer | Same as above |
| `gemini_colorizer` | Gemini Colorizer | Gemini Colorizer | Same as above |

## Workflows, API management, and custom parameters {#workflow-and-api}

### Translation workflows {#workflow-options}

The workflow combo in the translation workspace has no `userData`, so the index is the actual mode value; switching sets exactly one mutually exclusive CLI flag. Inputs, outputs, and skipped stages of the nine workflows are covered by the [Workflow matrix](./workflow-matrix.md), [Output directory and workflow](../desktop/translation/output-directory-and-workflow.md), and the `workflows/` pages.

| 存储值 | English | 简体中文 | CLI flag |
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

### API rotation strategies and custom parameter types {#api-options}

The API rotation-strategy combo and the custom-API-parameter type/boolean combos; see [API slots and rotation](../desktop/api-management/slots-and-rotation.md) and [Custom request parameters](../desktop/api-management/custom-request-parameters.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `failover` | Ordered failover | 按顺序故障切换 | API rotation strategy |
| `round_robin` | Round robin | 轮询 | Same as above |
| `string` | String | 字符串 | Custom API parameter type |
| `number` | Number | 数值 | Same as above |
| `boolean` | Boolean | 布尔值 | Same as above |
| `null` | Null | 空值 | Same as above |
| `json` | JSON | JSON | Same as above |
| `true` | true | true | Custom API parameter boolean |
| `false` | false | false | Same as above |

## Editor, batch management, and prompt terms {#editor-batch-terms}

### Rich-text advance and batch management {#rich-text-and-batch}

The rich-text “Force Advance” combo, plus the batch-management direction/alignment enums, booleans, logic, rich-text mode, and rule groups. See [Rich-text styles and presets](../desktop/rich-text-rules/styles-and-presets.md), [Batch conditions](../desktop/batch-management/conditions.md), and [Batch actions and order](../desktop/batch-management/actions-and-order.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `half` | Half Advance | 半格推进 | Rich-text Force Advance, Editor / rich-text rules |
| `full` | Full Advance | 全角推进 | Same as above |
| `h` | h | h | Batch condition / set-field Direction |
| `v` | v | v | Same as above |
| `hr` | hr | hr | Same as above |
| `vr` | vr | vr | Same as above |
| `auto` | auto | auto | Same as above |
| `left` | left | left | Batch condition / set-field Alignment |
| `center` | center | center | Same as above |
| `right` | right | right | Same as above |
| `auto` | auto | auto | Same as above |
| `true` | Yes | 是 | Batch condition boolean |
| `false` | No | 否 | Same as above |
| `all` | Match all | 全部满足 | Batch logic |
| `any` | Match any | 任一满足 | Same as above |
| `overwrite` | Overwrite | 覆盖 | Batch rich-text mode |
| `fill` | Fill in | 添加 | Same as above |
| `replace` | Replace | 替换 | Same as above |
| `common` | Common (Always) | 通用（始终执行） | Rich-text rule group |
| `horizontal` | Horizontal | 横排 | Same as above |
| `vertical` | Vertical | 竖排 | Same as above |

### Batch fields and operators {#batch-fields-operators}

Batch conditions and the “set region properties” action share the field selector; the latter shows writable fields only. Operators are re-populated by field type. See [Batch conditions](../desktop/batch-management/conditions.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `translation` | Translation | 翻译 | Batch field |
| `text` | Source Text | 原文 | Same as above |
| `translation_raw` | Translation (pre-replacement) | 译文（替换前） | Same as above |
| `font_family` | Font Family | 字体 | Same as above |
| `target_lang` | Target Language | 目标语言 | Same as above |
| `source_lang` | Source Language | 源语言 | Same as above |
| `direction` | Direction | 排版方向 | Same as above |
| `alignment` | Alignment | 对齐 | Same as above |
| `font_size` | Font Size | 绝对字号 | Same as above |
| `angle` | Angle | 角度 | Same as above |
| `line_spacing` | Line Spacing | 行距 | Same as above |
| `letter_spacing` | Letter Spacing | 字距 | Same as above |
| `stroke_width` | Stroke Width | 描边宽度 | Same as above |
| `prob` | OCR Confidence | OCR 置信度 | Same as above |
| `fg_colors` | Text Color | 文字颜色 | Same as above |
| `bg_colors` | Stroke Color | 描边颜色 | Same as above |
| `has_rich_text` | Has Rich Text | 含富文本 | Same as above |
| `line_count` | Line Count | 行数 | Same as above |
| `region_index` | Region Index | 区域序号 | Same as above |
| `contains` | contains | 包含 | Text-field operator |
| `not_contains` | does not contain | 不包含 | Same as above |
| `eq` | equals | 等于 | Same as above |
| `ne` | not equal to | 不等于 | Same as above |
| `regex` | matches regex | 正则匹配 | Same as above |
| `not_regex` | does not match regex | 正则不匹配 | Same as above |
| `empty` | is empty | 为空 | Same as above |
| `not_empty` | is not empty | 不为空 | Same as above |
| `gt` | greater than | 大于 | Number-field operator |
| `gte` | at least | 大于等于 | Same as above |
| `lt` | less than | 小于 | Same as above |
| `lte` | at most | 小于等于 | Same as above |
| `between` | between | 介于 | Same as above |
| `color_eq` | equals color | 颜色等于 | Color-field operator |
| `color_near` | close to color | 颜色接近 | Same as above |
| `is_true` | is yes | 是 | Boolean-field operator |
| `is_false` | is no | 否 | Same as above |

### Prompt term categories {#prompt-terms}

The term-editing dialog ships with the following six default categories; existing or newly created non-standard categories also appear, so these six must not be treated as a closed enum. See [Structured editor and format](../desktop/prompts/structured-editor-and-format.md) for details.

| 存储值 | English | 简体中文 | Control / owning page |
| --- | --- | --- | --- |
| `Person` | Person | 人物 | Prompt term category |
| `Location` | Location | 地点 | Same as above |
| `Org` | Organization | 组织 | Same as above |
| `Item` | Item | 物品 | Same as above |
| `Skill` | Skill | 技能 | Same as above |
| `Creature` | Creature | 生物 | Same as above |

## Runtime lists {#runtime-lists}

The following controls read machine-local or user configuration, so no stable “all options” table can be generated; they are documented as runtime behavior on the corresponding feature pages.

| Control | Data source | Why a fixed list is unavailable |
| --- | --- | --- |
| Fonts in settings and the editor | `utils/font_list.py` / system and project font directories | Depends on installed fonts and directory contents |
| Model names in API management | OpenAI/Gemini “Get Models” API calls | Depends on credentials, API base, and remote responses |
| Presets in API management and settings | `PresetService`, `custom_api_params.json` | Users can add, rename, or delete them |
| Prompt selection | Prompt files under `dict/` | Directory contents vary and the implementation shows file names |
| Editor style / rich-text presets | `app.saved_style_presets`, `app.saved_rich_text_presets` | User-defined key names are both display and stored values |
| Batch schemes | `config/batch_edit_schemes.yaml` | User-defined scheme names and counts vary |

## How to use this reference {#dependencies-and-conflicts}

- This page aggregates fixed options only; different forms of the same setting (for example the `render.direction` core enum `horizontal`/`vertical` versus the UI reverse-mapped `h`/`v`) are authoritative on the corresponding feature pages.
- Model and language options are re-populated after a language switch; the English/Simplified Chinese recorded here are the final displayed values.
- The workflow combo uses the index as its mode value and synchronizes mutually exclusive CLI flags; do not treat the index as a textual stored value.
- API rotation strategies only affect request endpoints inside the selected provider; they do not change the translator implementation. See [API slots and rotation](../desktop/api-management/slots-and-rotation.md).
- Never expose real keys, tokens, usernames, private paths, or user configuration; sanitize logs and debug directories before sharing.

## Related files and formats {#files-and-formats}

| File/directory | Role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `desktop_qt_ui/locales/en_US.json` / `zh_CN.json` | Source of the actual English and Simplified Chinese display values | Missing keys fall back through i18n; never translate from Chinese on your own |
| `desktop_qt_ui/app_logic.py` | `get_options_for_key()` / `get_display_mapping()` | Single entry point for options and display mappings |
| `desktop_qt_ui/ui/main_page/dynamic_settings.py`, `layout.py` | Ordinary settings controls, theme/language combos, and ratio linkage | Special controls are handled separately, not via the generic fallback |
| `manga_translator/config.py`, `manga_translator/image_formats.py` | Core enums and output formats | Enum literals shown directly are identical in both languages |
| `config/config-example.json` | Release defaults | Template only; never copy user paths or private content |

## Data sources {#source-evidence}

| Data | File | Use |
| --- | --- | --- |
| Options and display mapping | `desktop_qt_ui/app_logic.py` | `get_options_for_key()`, `get_display_mapping()` |
| Settings controls | `desktop_qt_ui/ui/main_page/dynamic_settings.py`, `layout.py` | Ordinary settings controls, theme/language combos, ratio linkage |
| API combos and rotation | `desktop_qt_ui/ui/main_page/env_management.py`, `manga_translator/api_key_rotation.py` | Feature selectors, `ROTATION_STRATEGIES` |
| Core enums | `manga_translator/config.py`, `manga_translator/image_formats.py` | Model/typesetting enums and output formats |
| Languages and locale | `desktop_qt_ui/services/translation_service.py`, `desktop_qt_ui/services/i18n_service.py`, `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Target/keep languages and actual bilingual values |
| Workflows | `desktop_qt_ui/ui/main_page/pages/translation_page.py`, `desktop_qt_ui/ui/main_page/runtime.py` | Nine index values and CLI flag mapping |
| Editor and batch | `desktop_qt_ui/ui/widgets/property_panel.py`, `desktop_qt_ui/ui/widgets/rich_text_editor_components.py`, `desktop_qt_ui/services/batch_edit_engine.py` | Editor-reused items, rich-text advance, batch conditions |
| Research and generated data | `doc/wiki/research/phase0-options-i18n-matrix.md`, `doc/wiki/data/i18n.generated.json`, `doc/wiki/data/settings.generated.json` | option inventory, UI text, and page mapping |
