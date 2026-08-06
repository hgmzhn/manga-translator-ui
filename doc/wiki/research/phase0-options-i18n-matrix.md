# 桌面端枚举与下拉选项清单

> 调查日期：2026-08-06<br>
> 范围：桌面 Qt UI 中具有固定源码选项的下拉框、枚举和模式选择器；英语和简体中文均以 `en_US.json` / `zh_CN.json` 的实际返回值为准。

## 口径

- `value` 是写回配置、环境变量或编辑器数据的值；若控件没有 `userData`，记录实际使用的索引或文字。
- `—` 表示没有 locale key：控件直接显示源码字面量，因此 English 与简体中文相同。
- 模型、语言、主题等来源在语言切换后重新填充；表中只记录当前源码可以静态确定的集合。
- 字体、保存的样式、配置预设、提示词文件和自定义 API 参数预设来自本机或用户配置，不能生成稳定的“所有选项”表，见[运行时列表](#runtime-lists)。

## 固定设置项

`dynamic_settings.py` 对普通设置的选项来源是 `AppLogic.get_options_for_key()` 和 `get_display_mapping()`；API 管理的四个功能选择器复用相同来源。`property_panel.py` 复用 OCR、翻译器、目标语言、对齐和方向的同一映射。

| 控件 / 使用页面 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| `app.theme`，设置 | `light` | Light | Light | —，`theme_registry.py` 字面量 |
| 同上 | `dark` | Dark | Dark | — |
| 同上 | `gray` | Gray | Gray | — |
| 同上 | `ocean` | Ocean | Ocean | — |
| 同上 | `forest` | Forest | Forest | — |
| 同上 | `sunset` | Sunset | Sunset | — |
| 同上 | `rose` | Rose | Rose | — |
| 同上 | `system` | Follow System | Follow System | — |
| `app.ui_language`，设置 | `zh_CN` | 简体中文 | 简体中文 | —，`LocaleInfo.name` |
| 同上 | `zh_TW` | 繁體中文 | 繁體中文 | — |
| 同上 | `en_US` | English | English | — |
| 同上 | `ja_JP` | 日本語 | 日本語 | — |
| 同上 | `ko_KR` | 한국어 | 한국어 | — |
| 同上 | `es_ES` | Español | Español | — |
| `cli.format`，设置 | `Not Specified`（发行配置仍存 `不指定`） | Not Specified | 不指定 | `format_not_specified` |
| 同上 | `png` | png | png | —，`OUTPUT_IMAGE_FORMATS` |
| 同上 | `jpg` | jpg | jpg | — |
| 同上 | `jpeg` | jpeg | jpeg | — |
| 同上 | `jfif` | jfif | jfif | — |
| 同上 | `webp` | webp | webp | — |
| 同上 | `avif` | avif | avif | — |
| 同上 | `bmp` | bmp | bmp | — |
| 同上 | `tiff` | tiff | tiff | — |
| 同上 | `tif` | tif | tif | — |
| 同上 | `heic` | heic | heic | — |
| 同上 | `heif` | heif | heif | — |
| `ocr.ocr`、`ocr.secondary_ocr`，设置；编辑器 OCR | `32px` | 32px | 32px | —，`Ocr` enum |
| 同上 | `48px` | 48px | 48px | — |
| 同上 | `48px_ctc` | 48px_ctc | 48px_ctc | — |
| 同上 | `mocr` | mocr | mocr | — |
| 同上 | `paddleocr` | paddleocr | paddleocr | — |
| 同上 | `paddleocr_korean` | paddleocr_korean | paddleocr_korean | — |
| 同上 | `paddleocr_latin` | paddleocr_latin | paddleocr_latin | — |
| 同上 | `paddleocr_thai` | paddleocr_thai | paddleocr_thai | — |
| 同上 | `paddleocr_vl` | paddleocr_vl | paddleocr_vl | — |
| 同上 | `openai_ocr` | openai_ocr | openai_ocr | — |
| 同上 | `gemini_ocr` | gemini_ocr | gemini_ocr | — |
| `detector.detector`，设置 | `default` | default | default | —，`Detector` enum |
| 同上 | `dbconvnext` | dbconvnext | dbconvnext | — |
| 同上 | `ctd` | ctd | ctd | — |
| 同上 | `craft` | craft | craft | — |
| 同上 | `none` | none | none | — |
| `inpainter.inpainter`，设置 | `default` | default | default | —，`Inpainter` enum |
| 同上 | `lama_large` | lama_large | lama_large | — |
| 同上 | `lama_mpe` | lama_mpe | lama_mpe | — |
| 同上 | `sd` | sd | sd | — |
| 同上 | `none` | none | none | — |
| 同上 | `original` | original | original | — |
| `inpainter.inpainting_precision`，设置 | `fp32` | fp32 | fp32 | —，`InpaintPrecision` enum |
| 同上 | `fp16` | fp16 | fp16 | — |
| 同上 | `bf16` | bf16 | bf16 | — |
| `render.renderer`，设置；API 管理渲染功能 | `default` | Default | Default | — |
| 同上 | `openai_renderer` | OpenAI Renderer | OpenAI Renderer | — |
| 同上 | `gemini_renderer` | Gemini Renderer | Gemini Renderer | — |
| 同上 | `none` | None | 无 | `translator_none` |
| `render.alignment`，设置；编辑器属性 | `auto` | Auto | 自动 | `alignment_auto` |
| 同上 | `left` | Left | 左对齐 | `alignment_left` |
| 同上 | `center` | Center | 居中 | `alignment_center` |
| 同上 | `right` | Right | 右对齐 | `alignment_right` |
| `render.direction`，设置 | `auto` | Auto | 自动 | `direction_auto` |
| 编辑器方向属性 | `h` | Horizontal | 横排 | `direction_horizontal` |
| 编辑器方向属性 | `v` | Vertical | 竖排 | `direction_vertical` |
| `render.direction` 的核心 enum 值 | `horizontal` | Horizontal | 横排 | `direction_horizontal`（UI 反向映射写 `h`） |
| 同上 | `vertical` | Vertical | 竖排 | `direction_vertical`（UI 反向映射写 `v`） |
| `render.layout_mode`，设置 | `smart_scaling` | Smart Scaling | 智能缩放 | `layout_mode_smart_scaling` |
| 同上 | `strict` | Strict Boundary | 严格边界 | `layout_mode_strict` |
| 同上 | `balloon_fill` | Smart Bubble | 智能气泡 | `layout_mode_balloon_fill` |
| `upscale.upscaler`，设置 | `waifu2x` | Waifu2x | Waifu2x | — |
| 同上 | `esrgan` | ESRGAN | ESRGAN | — |
| 同上 | `4xultrasharp` | 4x UltraSharp | 4x UltraSharp | — |
| 同上 | `realcugan` | Real-CUGAN | Real-CUGAN | — |
| 同上 | `mangajanai` | MangaJaNai | MangaJaNai | — |
| `colorizer.colorizer`，设置；API 管理上色功能 | `none` | None | 无 | `translator_none` |
| 同上 | `mc2` | Manga Colorization v2 | Manga Colorization v2 | — |
| 同上 | `openai_colorizer` | OpenAI Colorizer | OpenAI Colorizer | — |
| 同上 | `gemini_colorizer` | Gemini Colorizer | Gemini Colorizer | — |

## 翻译器与语言

| 控件 / 使用页面 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| `translator.translator`，设置；API 管理翻译功能；编辑器 | `openai` | OpenAI | OpenAI | — |
| 同上 | `openai_hq` | OpenAI High Quality | OpenAI高质量翻译 | `translator_openai_hq` |
| 同上 | `gemini` | Google Gemini | Google Gemini | — |
| 同上 | `gemini_hq` | Gemini High Quality | Gemini高质量翻译 | `translator_gemini_hq` |
| 同上 | `sakura` | Sakura | Sakura | — |
| 同上 | `none` | None | 无 | `translator_none` |
| 同上 | `original` | Original | 原文 | `translator_original` |
| `translator.target_lang`，设置；编辑器 | `CHS` | Simplified Chinese | 简体中文 | `lang_CHS` |
| 同上 | `CHT` | Traditional Chinese | 繁体中文 | `lang_CHT` |
| 同上 | `CSY` | Czech | 捷克语 | `lang_CSY` |
| 同上 | `NLD` | Dutch | 荷兰语 | `lang_NLD` |
| 同上 | `ENG` | English | 英语 | `lang_ENG` |
| 同上 | `FRA` | French | 法语 | `lang_FRA` |
| 同上 | `DEU` | German | 德语 | `lang_DEU` |
| 同上 | `HUN` | Hungarian | 匈牙利语 | `lang_HUN` |
| 同上 | `ITA` | Italian | 意大利语 | `lang_ITA` |
| 同上 | `JPN` | Japanese | 日语 | `lang_JPN` |
| 同上 | `KOR` | Korean | 韩语 | `lang_KOR` |
| 同上 | `POL` | Polish | 波兰语 | `lang_POL` |
| 同上 | `PTB` | Portuguese (Brazil) | 葡萄牙语（巴西） | `lang_PTB` |
| 同上 | `ROM` | Romanian | 罗马尼亚语 | `lang_ROM` |
| 同上 | `RUS` | Russian | 俄语 | `lang_RUS` |
| 同上 | `ESP` | Spanish | 西班牙语 | `lang_ESP` |
| 同上 | `TRK` | Turkish | 土耳其语 | `lang_TRK` |
| 同上 | `UKR` | Ukrainian | 乌克兰语 | `lang_UKR` |
| 同上 | `VIN` | Vietnamese | 越南语 | `lang_VIN` |
| 同上 | `ARA` | Arabic | 阿拉伯语 | `lang_ARA` |
| 同上 | `SRP` | Serbian | 塞尔维亚语 | `lang_SRP` |
| 同上 | `HRV` | Croatian | 克罗地亚语 | `lang_HRV` |
| 同上 | `THA` | Thai | 泰语 | `lang_THA` |
| 同上 | `IND` | Indonesian | 印度尼西亚语 | `lang_IND` |
| 同上 | `FIL` | Filipino (Tagalog) | 菲律宾语（他加禄语） | `lang_FIL` |
| `translator.keep_lang`，设置 | `none` | No Filter | 不过滤 | `lang_filter_disabled` |
| 同上（在 `target_lang` 基础上额外提供） | `SWE` | Swedish | 瑞典语 | `lang_SWE` |
| 同上 | `DAN` | Danish | 丹麦语 | `lang_DAN` |
| 同上 | `NOR` | Norwegian | 挪威语 | `lang_NOR` |
| 同上 | `FIN` | Finnish | 芬兰语 | `lang_FIN` |
| 同上 | `MSA` | Malay | 马来语 | `lang_MSA` |
| 同上 | `CAT` | Catalan | 加泰罗尼亚语 | `lang_CAT` |

`keep_lang` 同时包含上表全部 `target_lang` 值；其数据源是 `KEEP_LANGUAGES`，而不是 UI 显示文本的反向推导。

## PaddleOCR-VL 语言提示

| 控件 / 使用页面 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| `ocr.ocr_vl_language_hint`，设置 | `auto` | Auto | 自动 | `ocr_lang_auto` |
| 同上 | `multilingual` | Multilingual | 多语言 | `ocr_lang_multilingual` |
| 同上 | `Arabic` | Arabic | 阿拉伯语 | `ocr_lang_arabic` |
| 同上 | `Simplified Chinese` | Simplified Chinese | 简体中文 | `ocr_lang_simplified_chinese` |
| 同上 | `Traditional Chinese` | Traditional Chinese | 繁体中文 | `ocr_lang_traditional_chinese` |
| 同上 | `English` | English | 英语 | `ocr_lang_english` |
| 同上 | `Japanese` | Japanese | 日语 | `ocr_lang_japanese` |
| 同上 | `Korean` | Korean | 韩语 | `ocr_lang_korean` |
| 同上 | `Spanish` | Spanish | 西班牙语 | `ocr_lang_spanish` |
| 同上 | `French` | French | 法语 | `ocr_lang_french` |
| 同上 | `German` | German | 德语 | `ocr_lang_german` |
| 同上 | `Russian` | Russian | 俄语 | `ocr_lang_russian` |
| 同上 | `Portuguese` | Portuguese | 葡萄牙语 | `ocr_lang_portuguese` |
| 同上 | `Italian` | Italian | 意大利语 | `ocr_lang_italian` |
| 同上 | `Thai` | Thai | 泰语 | `ocr_lang_thai` |
| 同上 | `Vietnamese` | Vietnamese | 越南语 | `ocr_lang_vietnamese` |
| 同上 | `Indonesian` | Indonesian | 印尼语 | `ocr_lang_indonesian` |
| 同上 | `Turkish` | Turkish | 土耳其语 | `ocr_lang_turkish` |
| 同上 | `Polish` | Polish | 波兰语 | `ocr_lang_polish` |
| 同上 | `Ukrainian` | Ukrainian | 乌克兰语 | `ocr_lang_ukrainian` |

## 依赖上色器的放大倍率

该控件是 `upscale.upscale_ratio`；选择“not use”会存为 `null`，其余值随 `upscale.upscaler` 改变。Real-CUGAN 的模型名写入 `upscale.realcugan_model`，而不是 `upscale_ratio`。

| 前置 `upscaler` | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| 非 `realcugan` / `mangajanai` | `null` | Not Use | 不使用 | `upscale_ratio_not_use` |
| 非 `realcugan` / `mangajanai` | `2` | 2 | 2 | — |
| 同上 | `3` | 3 | 3 | — |
| 同上 | `4` | 4 | 4 | — |
| `realcugan` | `null` | Not Use | 不使用 | `upscale_ratio_not_use` |
| `realcugan` | `2x-conservative` | 2x-Conservative | 2倍-保守 | `realcugan_2x_conservative` |
| 同上 | `2x-conservative-pro` | 2x-Conservative-Pro | 2倍-保守-Pro | `realcugan_2x_conservative_pro` |
| 同上 | `2x-no-denoise` | 2x-No Denoise | 2倍-无降噪 | `realcugan_2x_no_denoise` |
| 同上 | `2x-denoise1x` | 2x-Denoise1x | 2倍-降噪1x | `realcugan_2x_denoise1x` |
| 同上 | `2x-denoise2x` | 2x-Denoise2x | 2倍-降噪2x | `realcugan_2x_denoise2x` |
| 同上 | `2x-denoise3x` | 2x-Denoise3x | 2倍-降噪3x | `realcugan_2x_denoise3x` |
| 同上 | `2x-denoise3x-pro` | 2x-Denoise3x-Pro | 2倍-降噪3x-Pro | `realcugan_2x_denoise3x_pro` |
| 同上 | `3x-conservative` | 3x-Conservative | 3倍-保守 | `realcugan_3x_conservative` |
| 同上 | `3x-conservative-pro` | 3x-Conservative-Pro | 3倍-保守-Pro | `realcugan_3x_conservative_pro` |
| 同上 | `3x-no-denoise` | 3x-No Denoise | 3倍-无降噪 | `realcugan_3x_no_denoise` |
| 同上 | `3x-no-denoise-pro` | 3x-No Denoise-Pro | 3倍-无降噪-Pro | `realcugan_3x_no_denoise_pro` |
| 同上 | `3x-denoise3x` | 3x-Denoise3x | 3倍-降噪3x | `realcugan_3x_denoise3x` |
| 同上 | `3x-denoise3x-pro` | 3x-Denoise3x-Pro | 3倍-降噪3x-Pro | `realcugan_3x_denoise3x_pro` |
| 同上 | `4x-conservative` | 4x-Conservative | 4倍-保守 | `realcugan_4x_conservative` |
| 同上 | `4x-no-denoise` | 4x-No Denoise | 4倍-无降噪 | `realcugan_4x_no_denoise` |
| 同上 | `4x-denoise3x` | 4x-Denoise3x | 4倍-降噪3x | `realcugan_4x_denoise3x` |
| `mangajanai` | `null` | Not Use | 不使用 | `upscale_ratio_not_use` |
| `mangajanai` | `x2` | x2 | x2 | — |
| 同上 | `x4` | x4 | x4 | — |
| 同上 | `DAT2 x4` | DAT2 x4 | DAT2 x4 | — |

## 工作流与 API 管理

工作流下拉没有 `userData`，以索引作为实际模式值；切换时清除八个互斥 CLI 标志，并只设置该索引对应的一个标志。API 功能选择器的取值已经在上面的翻译器、OCR、上色和渲染表中列出。

| 控件 / 使用页面 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| 翻译工作区工作流 | `0` | Normal Translation | 正常翻译流程 | `Normal Translation` |
| 同上 | `1` / `cli.generate_and_export` | Export Translation | 导出翻译 | `Export Translation` |
| 同上 | `2` / `cli.template` | Export Original Text | 导出原文 | `Export Original Text` |
| 同上 | `3` / `cli.translate_json_only` | Translate JSON Only | 仅翻译（JSON） | `Translate JSON Only` |
| 同上 | `4` / `cli.load_text` | Import Translation and Render | 导入翻译并渲染 | `Import Translation and Render` |
| 同上 | `5` / `cli.colorize_only` | Colorize Only | 仅上色 | `Colorize Only` |
| 同上 | `6` / `cli.upscale_only` | Upscale Only | 仅超分 | `Upscale Only` |
| 同上 | `7` / `cli.inpaint_only` | Inpaint Only | 仅修复 | `Inpaint Only` |
| 同上 | `8` / `cli.replace_translation` | Replace Translation | 替换翻译 | `Replace Translation` |
| API 通道轮换策略 | `failover` | Ordered failover | 按顺序故障切换 | `api_rotation_strategy_failover` |
| 同上 | `round_robin` | Round robin | 轮询 | `api_rotation_strategy_round_robin` |
| 自定义 API 参数的类型 | `string` | String | 字符串 | `String` |
| 同上 | `number` | Number | 数字 | `Number` |
| 同上 | `boolean` | Boolean | 布尔值 | `Boolean` |
| 同上 | `null` | Null | 空值 | `Null` |
| 同上 | `json` | JSON | JSON | — |
| 自定义 API 参数的布尔值 | `true` | true | true | — |
| 同上 | `false` | false | false | — |

## 编辑器与批量管理

编辑器属性面板的 OCR、翻译器、目标语言、对齐和方向均已合并到前表。以下是其余具有固定项目的选择器；字体、样式预设与方案名称属于运行时列表。

| 控件 / 使用页面 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- |
| 富文本“Force Advance”，编辑器 / 富文本规则 | `half` | Half Advance | 半格推进 | `Half Advance` |
| 同上 | `full` | Full Advance | 全角推进 | `Full Advance` |
| 批量条件 / 批量设属性的“Direction” | `h` | h | h | —，`FIELDS` 字面量 |
| 同上 | `v` | v | v | — |
| 同上 | `hr` | hr | hr | — |
| 同上 | `vr` | vr | vr | — |
| 同上 | `auto` | auto | auto | — |
| 批量条件 / 批量设属性的“Alignment” | `left` | left | left | —，`FIELDS` 字面量 |
| 同上 | `center` | center | center | — |
| 同上 | `right` | right | right | — |
| 同上 | `auto` | auto | auto | — |
| 批量条件布尔值 | `true` | Yes | 是 | `Yes` |
| 同上 | `false` | No | 否 | `No` |
| 批量逻辑 | `all` | Match all | 全部满足 | `Match all` |
| 同上 | `any` | Match any | 任一满足 | `Match any` |
| 批量富文本模式 | `overwrite` | Overwrite | 覆盖 | `Overwrite` |
| 同上 | `fill` | Fill in | 添加 | `Fill in` |
| 同上 | `replace` | Replace | 替换 | `Replace` |
| 富文本规则组 | `common` | Common (Always) | 通用（始终执行） | `Common (Always)` |
| 同上 | `horizontal` | Horizontal | 横排 | `Horizontal` |
| 同上 | `vertical` | Vertical | 竖排 | `Vertical` |

### 批量字段与运算符

批量条件与“设置 region 属性”共用字段选择器；后者只显示可写字段。运算符会随字段类型重新填充，所以表中“适用字段”列是完整静态的可达范围。

| 控件 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| 批量字段 | `translation` | Translation | 翻译 | `Translation` |
| 同上 | `text` | Source Text | 原文 | `Source Text` |
| 同上 | `translation_raw` | Translation (pre-replacement) | 译文（替换前） | `Translation (pre-replacement)` |
| 同上 | `font_family` | Font Family | 字体 | `Font Family` |
| 同上 | `target_lang` | Target Language | 目标语言 | `Target Language` |
| 同上 | `source_lang` | Source Language | 源语言 | `Source Language` |
| 同上 | `direction` | Direction | 排版方向 | `Direction` |
| 同上 | `alignment` | Alignment | 对齐 | `Alignment` |
| 同上 | `font_size` | Font Size | 绝对字号 | `Font Size` |
| 同上 | `angle` | Angle | 角度 | `Angle` |
| 同上 | `line_spacing` | Line Spacing | 行距 | `Line Spacing` |
| 同上 | `letter_spacing` | Letter Spacing | 字距 | `Letter Spacing` |
| 同上 | `stroke_width` | Stroke Width | 描边宽度 | `Stroke Width` |
| 同上 | `prob` | OCR Confidence | OCR 置信度 | `OCR Confidence` |
| 同上 | `fg_colors` | Text Color | 文字颜色 | `Text Color` |
| 同上 | `bg_colors` | Stroke Color | 描边颜色 | `Stroke Color` |
| 同上 | `has_rich_text` | Has Rich Text | 含富文本 | `Has Rich Text` |
| 同上 | `line_count` | Line Count | 行数 | `Line Count` |
| 同上 | `region_index` | Region Index | 区域序号 | `Region Index` |
| 文本字段运算符 | `contains` | contains | 包含 | `contains` |
| 同上 | `not_contains` | does not contain | 不包含 | `does not contain` |
| 同上 | `eq` | equals | 等于 | `equals` |
| 同上 | `ne` | not equal to | 不等于 | `not equal to` |
| 同上 | `regex` | matches regex | 正则匹配 | `matches regex` |
| 同上 | `not_regex` | does not match regex | 正则不匹配 | `does not match regex` |
| 同上 | `empty` | is empty | 为空 | `is empty` |
| 同上 | `not_empty` | is not empty | 不为空 | `is not empty` |
| 数值字段运算符 | `eq` | equals | 等于 | `equals` |
| 同上 | `ne` | not equal to | 不等于 | `not equal to` |
| 同上 | `gt` | greater than | 大于 | `greater than` |
| 同上 | `gte` | at least | 大于等于 | `at least` |
| 同上 | `lt` | less than | 小于 | `less than` |
| 同上 | `lte` | at most | 小于等于 | `at most` |
| 同上 | `between` | between | 介于 | `between` |
| 颜色字段运算符 | `color_eq` | equals color | 颜色等于 | `equals color` |
| 同上 | `color_near` | close to color | 颜色接近 | `close to color` |
| 布尔字段运算符 | `is_true` | is yes | 是 | `is yes` |
| 同上 | `is_false` | is no | 否 | `is no` |

## 提示词术语分类

术语编辑对话框的下拉默认具有以下六种类别；已存在或后续新建的非标准类别会一并出现，因此不把这六项误称为封闭枚举。

| 控件 | value | English | 简体中文 | i18n key |
| --- | --- | --- | --- | --- |
| 提示词术语分类 | `Person` | Person | 人物 | `Person` |
| 同上 | `Location` | Location | 地点 | `Location` |
| 同上 | `Org` | Organization | 组织 | `Org` |
| 同上 | `Item` | Item | 物品 | `Item` |
| 同上 | `Skill` | Skill | 技能 | `Skill` |
| 同上 | `Creature` | Creature | 生物 | `Creature` |

## 运行时列表

| 控件 | 数据源 | 固定清单不可用的原因 |
| --- | --- | --- |
| 设置和编辑器的字体 | `utils/font_list.py` / 系统与项目字体目录 | 取决于本机已安装字体与目录内容。 |
| API 管理模型名 | OpenAI/Gemini “Get Models” API 调用 | 取决于凭据、API 地址和远端服务返回值。 |
| API 管理与设置页的预设 | `PresetService`、`custom_api_params.json` | 用户可新增、重命名或删除。 |
| 提示词选择 | `dict/` 下的提示词文件 | 目录内容可变，且当前实现按文件名显示。 |
| 编辑器样式 / 富文本样式 | `app.saved_style_presets`、`app.saved_rich_text_presets` | 用户配置的键名就是显示值和存储值。 |
| 批量管理方案 | `config/batch_edit_schemes.yaml` | 用户定义的方案名和数量可变。 |

## 源码依据

| 层级 | 文件 | 核对内容 |
| --- | --- | --- |
| 选项与显示映射 | `desktop_qt_ui/app_logic.py` | `get_options_for_key()`、`get_display_mapping()`。 |
| 设置控件 | `desktop_qt_ui/ui/main_page/dynamic_settings.py` | 普通设置控件、条件倍率和显示值反向写回。 |
| API 下拉与轮换 | `desktop_qt_ui/ui/main_page/env_management.py`、`manga_translator/api_key_rotation.py` | 功能选择器、`ROTATION_STRATEGIES`。 |
| 核心枚举 | `manga_translator/config.py`、`manga_translator/image_formats.py` | 模型/排版 enum 和输出格式。 |
| 语言与 locale | `desktop_qt_ui/services/translation_service.py`、`desktop_qt_ui/services/i18n_service.py`、`desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json` | 目标/保留语言和实际双语值。 |
| 工作流 | `desktop_qt_ui/ui/main_page/pages/translation_page.py`、`desktop_qt_ui/ui/main_page/runtime.py` | 九个索引值与 CLI 标志映射。 |
| 编辑器与批量 | `desktop_qt_ui/ui/widgets/property_panel.py`、`desktop_qt_ui/ui/widgets/rich_text_editor_components.py`、`desktop_qt_ui/services/batch_edit_engine.py` | 编辑器复用项、富文本推进和批量条件。 |
