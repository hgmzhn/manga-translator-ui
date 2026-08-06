---
title: Settings Parameter Index
description: Index every visible desktop settings parameter and jump to the matching settings page or parameter anchor
pageId: reference.settings-index
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Settings Parameter Index

Use this page when you need to find which settings tab a parameter belongs to, what its stored key and UI text are, and where its detailed explanation lives. It aggregates every visible parameter of the seven settings tabs into one index and links each row to the matching settings page or parameter anchor. Parameter semantics, the three default tiers, consumers, and runtime behavior are covered on the corresponding settings pages; this page only summarizes and links back, without restating standalone file documentation.

For the settings shell, description panel, import/export, and configuration lifecycle, see [Settings and Configuration Lifecycle](../desktop/settings/index.md). For the full value/i18n mapping of every enum option, see [Options and i18n Matrix](./options-i18n-matrix.md). For the nine-workflow stage matrix, see [Workflow Matrix](./workflow-matrix.md).

## How to use this page {#how-to-use}

Follow this order:

1. Confirm the target tab and page in “Settings tabs and pages”.
2. Find the parameter row under the matching tab in “Parameter index”; the stored key and the actual English and Simplified Chinese text are on the same row.
3. Use the “Jump to” link to open the parameter section on the settings page; rows without a dedicated explicit anchor link to the page itself.
4. Defaults, dependencies, consumers, and runtime behavior always come from the settings page you land on; this page does not expand them.

This page covers only the visible parameter rows of the settings page. `settings_tab_layout.json` has 110 entries, of which 109 render as visible parameters; the remaining one (`render.font_color`) is not rendered because its release default is `null` and it has no control branch. The following are out of scope: API-management credentials, addresses, models, slots, and rotation strategies; editor property-panel parameters; prompt lists and batch-management schemes; and the full processing steps of the nine workflows.

## Settings tabs and pages {#settings-tabs}

The settings page groups parameters by the left-side tabs; tab titles come from the actual locale values. `Advanced` is only a divider title inside the OCR, Detection, and Inpainting tabs, not a separate tab.

| Layout title / UI call key | English actual value | Simplified Chinese actual value | Visible parameter count | Page |
| --- | --- | --- | ---: | --- |
| `General` | General | 通用 | 19 | [General and Application Settings](../desktop/settings/general-and-app.md), [CLI, Batch, and Output](../desktop/settings/cli-batch-and-output.md) |
| `OCR` | OCR | 文字识别 | 17 | [OCR, Filtering, and Text-Line Merging](../desktop/settings/ocr-filter-and-merge.md) |
| `Detection` | Detection | 检测 | 13 | [Detection](../desktop/settings/detection.md) |
| `Translation` | Translation | 翻译 | 11 | [Translation settings](../desktop/settings/translation.md) |
| `Inpainting` | Inpainting | 修复 | 10 | [Mask And Inpainting](../desktop/settings/mask-and-inpainting.md) |
| `Typesetting` | Typesetting | 排版 | 28 | [Typesetting and Rendering](../desktop/settings/typesetting-and-rendering.md) |
| `Mode Specific` | Mode Specific | 模式相关 | 11 | [Mode-Specific Workflows and Template Alignment](../desktop/settings/mode-specific.md), [Upscale and Colorization](../desktop/settings/upscale-and-colorization.md) |

The seven tabs total 109 visible parameter rows.

## UI call keys and actual text {#ui-i18n}

The tabs, titles, and buttons on this page are verified against `desktop_qt_ui/locales/en_US.json` and `zh_CN.json`:

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Settings` | Settings | 设置 |
| `Settings Page Title` | Settings | 参数设置 |
| `Settings Page Subtitle` | Adjust translation pipeline parameters. Changes are saved automatically. | 调整翻译流程的各项参数。修改后将自动保存。 |
| `General` | General | 通用 |
| `OCR` | OCR | 文字识别 |
| `Detection` | Detection | 检测 |
| `Translation` | Translation | 翻译 |
| `Inpainting` | Inpainting | 修复 |
| `Typesetting` | Typesetting | 排版 |
| `Mode Specific` | Mode Specific | 模式相关 |
| `Advanced` | Advanced | 高级 |
| `Export Config` | Export Config | 导出配置 |
| `Import Config` | Import Config | 导入配置 |

## Parameter index {#parameter-index}

The tables below group parameters by settings tab and record the stored key, the actual English and Simplified Chinese text, and a jump link. The data comes from `doc/wiki/data/settings.generated.json` (generated from the settings layout, UI binding, and the two locales) and `doc/wiki/data/i18n.generated.json`; every jump anchor was checked against the explicit anchors in the corresponding settings page.

### General {#tab-general}

The 19 parameters of the General tab span two pages: application-level parameters are in [General and Application Settings](../desktop/settings/general-and-app.md), and CLI/output parameters are in [CLI, Batch, and Output](../desktop/settings/cli-batch-and-output.md).

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `app.ui_language` | Language: | 语言： | [#app-ui-language](../desktop/settings/general-and-app.md#app-ui-language) |
| `app.theme` | Theme: | 主题： | [#app-theme](../desktop/settings/general-and-app.md#app-theme) |
| `cli.verbose` | Verbose Logging | 详细日志 | [#cli-verbose](../desktop/settings/cli-batch-and-output.md#cli-verbose) |
| `cli.ignore_errors` | Ignore Errors | 忽略错误 | [#cli-ignore-errors](../desktop/settings/cli-batch-and-output.md#cli-ignore-errors) |
| `cli.use_gpu` | Use GPU | 使用 GPU | [#cli-use-gpu](../desktop/settings/cli-batch-and-output.md#cli-use-gpu) |
| `cli.disable_onnx_gpu` | Disable ONNX GPU Acceleration | 禁用 ONNX GPU 加速 | [#cli-disable-onnx-gpu](../desktop/settings/cli-batch-and-output.md#cli-disable-onnx-gpu) |
| `cli.format` | Output Format | 输出格式 | [#cli-format](../desktop/settings/cli-batch-and-output.md#cli-format) |
| `cli.overwrite` | Overwrite Existing Files | 覆盖已存在文件 | [#cli-overwrite](../desktop/settings/cli-batch-and-output.md#cli-overwrite) |
| `cli.skip_no_text` | Skip Images Without Text | 跳过无文本图像 | [#cli-skip-no-text](../desktop/settings/cli-batch-and-output.md#cli-skip-no-text) |
| `cli.save_text` | Editable Image | 图片可编辑 | [#cli-save-text](../desktop/settings/cli-batch-and-output.md#cli-save-text) |
| `cli.save_quality` | Image Save Quality | 图像保存质量 | [#cli-save-quality](../desktop/settings/cli-batch-and-output.md#cli-save-quality) |
| `cli.attempts` | Retry Attempts | 重试次数 | [#cli-attempts](../desktop/settings/cli-batch-and-output.md#cli-attempts) |
| `cli.batch_size` | Batch Size | 批量大小 | [#cli-batch-size](../desktop/settings/cli-batch-and-output.md#cli-batch-size) |
| `cli.batch_concurrent` | Concurrent Batch Processing | 并发批量处理 | [#cli-batch-concurrent](../desktop/settings/cli-batch-and-output.md#cli-batch-concurrent) |
| `use_custom_api_params` | Use Custom API Params | 使用自定义API参数 | [#custom-api-params](../desktop/settings/general-and-app.md#custom-api-params) |
| `cli.save_to_source_dir` | Save to Source Directory | 输出到原图目录 | [#cli-save-to-source-dir](../desktop/settings/cli-batch-and-output.md#cli-save-to-source-dir) |
| `cli.export_editable_psd` | Export Editable PSD | 导出可编辑PSD | [#cli-export-editable-psd](../desktop/settings/cli-batch-and-output.md#cli-export-editable-psd) |
| `cli.psd_script_only` | Generate PSD Script Only | 仅生成PSD脚本 | [#cli-psd-script-only](../desktop/settings/cli-batch-and-output.md#cli-psd-script-only) |
| `app.unload_models_after_translation` | Unload Models After Translation | 翻译完成后卸载模型 | [#unload-models](../desktop/settings/general-and-app.md#unload-models) |

### OCR {#tab-ocr}

The 17 parameters of this tab are explained in [OCR, Filtering, and Text-Line Merging](../desktop/settings/ocr-filter-and-merge.md).

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `ocr.ocr` | OCR Model | OCR模型 | [#ocr-ocr](../desktop/settings/ocr-filter-and-merge.md#ocr-ocr) |
| `ocr.use_hybrid_ocr` | Enable Hybrid OCR | 启用混合OCR | [#hybrid-ocr](../desktop/settings/ocr-filter-and-merge.md#hybrid-ocr) |
| `ocr.secondary_ocr` | Secondary OCR | 备用OCR | [#hybrid-ocr](../desktop/settings/ocr-filter-and-merge.md#hybrid-ocr) |
| `ocr.ai_ocr_prompt_path` | AI OCR Prompt | AI OCR 提示词 | [#ocr-vl-and-ai](../desktop/settings/ocr-filter-and-merge.md#ocr-vl-and-ai) |
| `ocr.ai_ocr_concurrency` | AI OCR Concurrency | AI OCR 并发数 | [#ocr-vl-and-ai](../desktop/settings/ocr-filter-and-merge.md#ocr-vl-and-ai) |
| `ocr.ocr_vl_language_hint` | PaddleOCR-VL Language Hint | PaddleOCR-VL 语言提示 | [#ocr-vl-and-ai](../desktop/settings/ocr-filter-and-merge.md#ocr-vl-and-ai) |
| `ocr.ocr_vl_custom_prompt` | PaddleOCR-VL Custom Prompt (Override) | PaddleOCR-VL 自定义提示词（优先） | [#ocr-vl-and-ai](../desktop/settings/ocr-filter-and-merge.md#ocr-vl-and-ai) |
| `ocr.use_model_bubble_filter` | Enable Model Bubble Filter | 启用模型气泡过滤 | [#model-bubble-filter](../desktop/settings/ocr-filter-and-merge.md#model-bubble-filter) |
| `ocr.min_text_length` | Minimum Text Length | 最小文本长度 | [#ocr-min-text-length](../desktop/settings/ocr-filter-and-merge.md#ocr-min-text-length) |
| `ocr.ignore_bubble` | Ignore Non-Bubble Text | 忽略非气泡文本 | [#ocr-ignore-bubble](../desktop/settings/ocr-filter-and-merge.md#ocr-ignore-bubble) |
| `ocr.merge_special_require_full_wrap` | Require Full Wrap In Special Pre-Merge | 模型辅助合并 | [#special-pre-merge](../desktop/settings/ocr-filter-and-merge.md#special-pre-merge) |
| `ocr.model_bubble_overlap_threshold` | Model Bubble Overlap Threshold | 模型气泡重叠阈值 | [#model-bubble-filter](../desktop/settings/ocr-filter-and-merge.md#model-bubble-filter) |
| `filter_text_enabled` | Enable Filter List | 启用过滤列表 | [#filter-text-enabled](../desktop/settings/ocr-filter-and-merge.md#filter-text-enabled) |
| `ocr.prob` | Text Region Min Probability | 文本区域最低概率 (prob) | [#ocr-prob](../desktop/settings/ocr-filter-and-merge.md#ocr-prob) |
| `ocr.merge_gamma` | Merge Distance Tolerance | 合并-距离容忍度 | [#merge-tolerances](../desktop/settings/ocr-filter-and-merge.md#merge-tolerances) |
| `ocr.merge_sigma` | Merge Outlier Tolerance | 合并-离群容忍度 | [#merge-tolerances](../desktop/settings/ocr-filter-and-merge.md#merge-tolerances) |
| `ocr.merge_edge_ratio_threshold` | Merge Edge Ratio Threshold | 合并-边缘距离比例阈值 | [#merge-edge-ratio](../desktop/settings/ocr-filter-and-merge.md#merge-edge-ratio) |

### Detection {#tab-detection}

The 13 parameters of this tab are explained in [Detection](../desktop/settings/detection.md).

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `detector.detector` | Text Detector | 文本检测器 | [#detector-detector-parameter](../desktop/settings/detection.md#detector-detector-parameter) |
| `detector.import_yolo_labels` | Import Fixed YOLO Boxes | 导入固定YOLO框 | [#detector-import-yolo-labels](../desktop/settings/detection.md#detector-import-yolo-labels) |
| `detector.use_yolo_obb` | Enable YOLO Detection | 启用YOLO辅助检测 | [#detector-use-yolo-obb](../desktop/settings/detection.md#detector-use-yolo-obb) |
| `detector.use_sfx_filter` | SFX Filter | 拟声词过滤 | [#detector-use-sfx-filter](../desktop/settings/detection.md#detector-use-sfx-filter) |
| `detector.sfx_filter_include_bubble_text` | Include Bubble Text in SFX Filter | 气泡文本参与拟声词过滤 | [#detector-sfx-filter-include-bubble-text](../desktop/settings/detection.md#detector-sfx-filter-include-bubble-text) |
| `detector.min_box_area_ratio` | Min Box Area Ratio | 最小检测框面积占比 | [#detector-min-box-area-ratio](../desktop/settings/detection.md#detector-min-box-area-ratio) |
| `detector.detection_size` | Detection Size | 检测大小 | [#detector-detection-size](../desktop/settings/detection.md#detector-detection-size) |
| `detector.det_rearrange_min_effective_short_side` | Long Image Rearrange Min Short Side | 长图重排最低有效短边 | [#detector-rearrange-short-side](../desktop/settings/detection.md#detector-rearrange-short-side) |
| `detector.text_threshold` | Text Threshold | 文本阈值 | [#detector-text-threshold](../desktop/settings/detection.md#detector-text-threshold) |
| `detector.box_threshold` | Box Generation Threshold | 边界框生成阈值 | [#detector-box-threshold](../desktop/settings/detection.md#detector-box-threshold) |
| `detector.unclip_ratio` | Unclip Ratio | Unclip比例 | [#detector-unclip-ratio](../desktop/settings/detection.md#detector-unclip-ratio) |
| `detector.yolo_obb_conf` | YOLO Confidence Threshold | YOLO置信度阈值 | [#detector-yolo-obb-conf](../desktop/settings/detection.md#detector-yolo-obb-conf) |
| `detector.yolo_obb_overlap_threshold` | YOLO Overlap Removal Threshold | YOLO辅助检测重叠率删除阈值 | [#detector-yolo-obb-overlap-threshold](../desktop/settings/detection.md#detector-yolo-obb-overlap-threshold) |

### Translation {#tab-translation}

The 11 parameters of this tab are explained in [Translation settings](../desktop/settings/translation.md).

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `translator.translator` | Translator | 翻译器 | [#translator-translator](../desktop/settings/translation.md#translator-translator) |
| `translator.target_lang` | Target Language | 目标语言 | [#translator-target-lang](../desktop/settings/translation.md#translator-target-lang) |
| `translator.keep_lang` | Keep Source Language | 保留源语言 | [#translator-keep-lang](../desktop/settings/translation.md#translator-keep-lang) |
| `translator.enable_streaming` | Enable Streaming | 启用流式传输 | [#translator-enable-streaming](../desktop/settings/translation.md#translator-enable-streaming) |
| `translator.no_text_lang_skip` | Don't Skip Target Lang | 不跳过目标语言文本 | [#translator-no-text-lang-skip](../desktop/settings/translation.md#translator-no-text-lang-skip) |
| `translator.extract_glossary` | Auto Extract Glossary | 自动提取新术语 | [#translator-extract-glossary](../desktop/settings/translation.md#translator-extract-glossary) |
| `translator.max_requests_per_minute` | Max Requests Per Minute | 每分钟最大请求数 | [#translator-max-requests-per-minute](../desktop/settings/translation.md#translator-max-requests-per-minute) |
| `translator.remove_trailing_period` | Auto Remove Final Period/Comma | 自动移除末尾句号逗号 | [#translator-remove-trailing-period](../desktop/settings/translation.md#translator-remove-trailing-period) |
| `cli.context_size` | Context Pages | 上下文页数 | [#cli-context-size](../desktop/settings/translation.md#cli-context-size) |
| `translator.convert_to_traditional` | Convert to Traditional Chinese | 简体转繁体 | [#translator-chinese-conversion](../desktop/settings/translation.md#translator-chinese-conversion) |
| `translator.convert_to_simplified` | Convert to Simplified Chinese | 繁体转简体 | [#translator-chinese-conversion](../desktop/settings/translation.md#translator-chinese-conversion) |

### Inpainting {#tab-inpainting}

The 10 parameters of this tab are explained in [Mask And Inpainting](../desktop/settings/mask-and-inpainting.md). `inpainter.inpainter`, `inpainter.solid_fill_pure_bubbles`, and `inpainter.per_block_inpainting` have no dedicated explicit anchor on that page and link to the page itself.

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `inpainter.inpainter` | Inpainting Model | 修复模型 | [Page](../desktop/settings/mask-and-inpainting.md) |
| `mask_dilation_offset` | Mask Dilation Offset | 遮罩扩张偏移 | [#dilation-and-kernel](../desktop/settings/mask-and-inpainting.md#dilation-and-kernel) |
| `ocr.limit_mask_dilation_to_bubble_mask` | Keep Dilation Inside Bubble Mask | 膨胀不超过气泡蒙版 | [#bubble-range](../desktop/settings/mask-and-inpainting.md#bubble-range) |
| `ocr.use_model_bubble_repair_intersection` | Expand Bubble Repair Range | 扩大气泡修复范围 | [#bubble-range](../desktop/settings/mask-and-inpainting.md#bubble-range) |
| `inpainter.solid_fill_pure_bubbles` | Solid Fill Pure Bubbles | 纯色气泡直接填色 | [Page](../desktop/settings/mask-and-inpainting.md) |
| `inpainter.per_block_inpainting` | Per-Block Inpainting | 逐块修复 | [Page](../desktop/settings/mask-and-inpainting.md) |
| `inpainter.inpainting_size` | Inpainting Size | 修复大小 | [#size-and-precision](../desktop/settings/mask-and-inpainting.md#size-and-precision) |
| `inpainter.inpainting_precision` | Inpainting Precision | 修复精度 | [#size-and-precision](../desktop/settings/mask-and-inpainting.md#size-and-precision) |
| `kernel_size` | Kernel Size | 卷积核大小 | [#dilation-and-kernel](../desktop/settings/mask-and-inpainting.md#dilation-and-kernel) |
| `inpainter.force_use_torch_inpainting` | Force Use PyTorch Inpainting | 强制使用PyTorch修复 | [#force-torch](../desktop/settings/mask-and-inpainting.md#force-torch) |

### Typesetting {#tab-typesetting}

The 28 parameters of this tab are explained in [Typesetting and Rendering](../desktop/settings/typesetting-and-rendering.md). The parameter sections of that page have no dedicated explicit anchors, so all rows link to the page itself.

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `render.renderer` | Renderer | 渲染器 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_family` | Font | 字体 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.ai_renderer_prompt_path` | AI Renderer Prompt | AI 渲染提示词 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.ai_renderer_concurrency` | AI Renderer Concurrency | AI 渲染并发数 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.alignment` | Alignment | 对齐方式 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.semantic_linebreak` | Chinese Semantic Line Break | 中文语义断句 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.remove_linebreak_punctuation` | Trim Around Line Breaks | 去除换行符周围逗号句号 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.disable_auto_wrap` | AI Line Breaking | AI断句 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.optimize_line_breaks` | AI Line Break Auto Enlarge | AI断句自动扩大文字 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.strict_smart_scaling` | Don't Expand Box on Auto Enlarge | AI断句自动扩大文字下不扩大文本框 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.check_br_and_retry` | AI Line Break Check | AI断句检查 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.stroke_width` | Stroke Width Ratio | 描边宽度比例 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.disable_font_border` | Disable Font Border | 禁用字体边框 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.center_text_in_bubble` | Center in Bubble | 气泡内居中 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size_offset` | Font Size Offset | 字体大小偏移量 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size_minimum` | Minimum Font Size | 最小字体大小 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.direction` | Text Direction | 文本方向 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.uppercase` | Uppercase | 大写 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.lowercase` | Lowercase | 小写 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.no_hyphenation` | Disable Hyphenation | 禁用连字符 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.bubble_layout_english` | Bubble Layout (Force Horizontal) | 根据气泡排版(强制横排) | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.line_spacing` | Line Spacing | 行间距 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.letter_spacing` | Letter Spacing | 字间距 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size` | Font Size | 字体大小 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.rtl` | Right to Left | 从右到左 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.layout_mode` | Layout Mode | 排版模式 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.max_font_size` | Maximum Font Size | 最大字体大小 | [Page](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_scale_ratio` | Font Scale Ratio | 字体缩放比例 | [Page](../desktop/settings/typesetting-and-rendering.md) |

### Mode Specific {#tab-mode-specific}

The 11 parameters of this tab span two pages: direct-paste and template-alignment parameters are in [Mode-Specific Workflows and Template Alignment](../desktop/settings/mode-specific.md), and upscale/colorization parameters are in [Upscale and Colorization](../desktop/settings/upscale-and-colorization.md).

| Stored value | English actual value | Simplified Chinese actual value | Jump to |
| --- | --- | --- | --- |
| `render.enable_template_alignment` | Enable Direct Paste Mode | 启用直接粘贴模式 | [#render-enable-template-alignment](../desktop/settings/mode-specific.md#render-enable-template-alignment) |
| `render.paste_mask_dilation_pixels` | Paste Mode Mask Dilation Pixels | 粘贴模式蒙版膨胀大小 | [#render-paste-mask-dilation-pixels](../desktop/settings/mode-specific.md#render-paste-mask-dilation-pixels) |
| `upscale.upscaler` | Upscaling Model | 超分模型 | [#upscale-upscaler](../desktop/settings/upscale-and-colorization.md#upscale-upscaler) |
| `upscale.upscale_ratio` | Upscale Ratio | 超分倍数 | [#upscale-upscale-ratio](../desktop/settings/upscale-and-colorization.md#upscale-upscale-ratio) |
| `upscale.tile_size` | Tile Size (0=No Split) | 分块大小(0=不分割) | [#upscale-tile-size](../desktop/settings/upscale-and-colorization.md#upscale-tile-size) |
| `upscale.revert_upscaling` | Revert Upscaling | 还原超分 | [#upscale-revert-upscaling](../desktop/settings/upscale-and-colorization.md#upscale-revert-upscaling) |
| `colorizer.colorizer` | Colorization Model | 上色模型 | [#colorizer-colorizer](../desktop/settings/upscale-and-colorization.md#colorizer-colorizer) |
| `colorizer.ai_colorizer_prompt_path` | AI Colorizer Prompt | AI 上色提示词 | [#colorizer-ai-colorizer-prompt-path](../desktop/settings/upscale-and-colorization.md#colorizer-ai-colorizer-prompt-path) |
| `colorizer.ai_colorizer_history_pages` | AI Colorizer History Pages | AI 上色历史页数 | [#colorizer-ai-colorizer-history-pages](../desktop/settings/upscale-and-colorization.md#colorizer-ai-colorizer-history-pages) |
| `colorizer.colorization_size` | Colorization Size | 上色大小 | [#colorizer-colorization-size](../desktop/settings/upscale-and-colorization.md#colorizer-colorization-size) |
| `colorizer.denoise_sigma` | Denoise Strength | 降噪强度 | [#colorizer-denoise-sigma](../desktop/settings/upscale-and-colorization.md#colorizer-denoise-sigma) |


## Coverage boundary and backlinks {#coverage-and-backlinks}

- Backlinks: this page is the aggregated jump entry for the seven settings topic pages and [Settings and Configuration Lifecycle](../desktop/settings/index.md); the detailed explanation remains on each topic page.
- Sibling reference pages: the full value/i18n mapping of enum options is in [Options and i18n Matrix](./options-i18n-matrix.md), the nine-workflow stage matrix in [Workflow Matrix](./workflow-matrix.md), the per-page source-evidence summary in [Source Evidence Index](./source-evidence-index.md), and debug artifacts in [Debug Artifact Index](./debug-artifact-index.md).
- Values written back by other controls instead of being settings rows: `upscale.realcugan_model` is written by the upscaler combo (see [Upscale and Colorization](../desktop/settings/upscale-and-colorization.md)); the eight workflow flags (`cli.generate_and_export`, `cli.template`, `cli.translate_json_only`, `cli.load_text`, `cli.colorize_only`, `cli.upscale_only`, `cli.inpaint_only`, `cli.replace_translation`) are set by the workflow dropdown in the translation workspace (see [Mode-Specific Workflows and Template Alignment](../desktop/settings/mode-specific.md)). Editor preferences (`editor_*`) belong to `AppSection` but are not rendered as General rows.
- Runtime lists (fonts, model names, API presets, prompt files, batch schemes) come from the local machine or user configuration and are not part of this index's fixed catalog.

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Settings layout | `desktop_qt_ui/ui/main_page/settings_tab_layout.json` | Seven tabs, 110 entries, 109 visible parameters, `Advanced` dividers |
| Page shell | `desktop_qt_ui/ui/main_page/pages/settings_page.py` | Tab title keys, import/export, description panel |
| Dynamic controls | `desktop_qt_ui/ui/main_page/dynamic_settings.py` | Control types, file-edit actions, skipped fields |
| UI/i18n | `desktop_qt_ui/app_logic.py`, `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Label mapping and actual bilingual display values |
| Config models | `desktop_qt_ui/core/config_models.py`, `manga_translator/config.py` | Qt/core parameter definitions and defaults |
| Generated data | `doc/wiki/data/settings.generated.json`, `doc/wiki/data/i18n.generated.json` | 109 parameter records, 1353 i18n entries |
| Research | `doc/wiki/research/phase0-options-i18n-matrix.md`, `phase0-page-coverage-matrix.md`, `default-sources.md` | Option matrix, coverage matrix, default differences |
| Settings topic pages | `doc/wiki/en/desktop/settings/*.md` | Parameter anchors and jump targets checked row by row |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read sections 1.3 and 5.16 in full and followed the page contract |
| Parameter catalog and anchors | Complete | All 109 records mapped and every `{#anchor}` verified to exist in the target page |
| `en_US` / `zh_CN` actual locales | Complete | Tab and label text matches both locales row by row |
| Generated data files | Complete | `settings.generated.json` and `i18n.generated.json` used as table sources |
| Route mirror and source-evidence scripts | Complete | `node scripts/verify-route-mirror.mjs .` and `node scripts/verify-source-evidence.mjs .` pass |
| Sensitive-information review | Complete | No real `.env`, user config, API key, private path, user image, or private prompt read or written |
| VitePress build | Deferred | Coordinator runs `npm run docs:build --prefix doc/wiki` before merge |
