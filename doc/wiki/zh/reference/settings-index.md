---
title: 设置参数索引
description: 汇总桌面设置页的全部可见参数，并跳转到对应设置页与参数锚点
pageId: reference.settings-index
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 设置参数索引

当你想确认某个参数属于设置页的哪个页签、它的存储键和界面文案是什么、以及详细说明在哪个页面时，使用本页。本页把设置页七个页签下的全部可见参数汇总成索引表，并为每一项提供跳转到对应设置页或参数锚点的链接；参数的具体语义、三类默认值、消费者和运行机理在对应设置页展开，本页只做汇总与反向链接，不重复撰写脱离功能的文件百科。

设置页外壳、说明面板、导入导出和配置生命周期见[设置页与配置生命周期](../desktop/settings/index.md)；全部枚举选项的 value/i18n 对照见[选项与 i18n 矩阵](./options-i18n-matrix.md)；九种工作流的阶段矩阵见[工作流矩阵](./workflow-matrix.md)。

## 本页用法 {#how-to-use}

按以下顺序使用本页：

1. 在“设置页与页签”确认目标参数所属页签及对应页面。
2. 在“参数索引”按页签找到参数行；存储键、English 与简体中文实际文案在同一行并列。
3. 点击“跳转”列进入对应设置页的参数小节；没有独立显式锚点的参数行跳转到页面本身。
4. 默认值、依赖、消费者和运行机理一律以跳转后的设置页正文为准，本页不展开。

本页只覆盖设置页中可见的参数行。`settings_tab_layout.json` 有 110 个条目，其中 109 个渲染为可见参数，1 个（`render.font_color`）因发行默认值为 `null` 且没有对应控件分支而不渲染。以下内容不属于本页：API 管理页的凭据、地址、模型、候选槽与轮询策略，编辑器属性面板参数，提示词列表与批量管理方案，以及九种工作流的完整处理步骤。

## 设置页与页签 {#settings-tabs}

设置页通过左侧页签分组显示参数；页签标题来自 locale 的实际显示值。`Advanced` 只是 OCR、Detection、Inpainting 页签内的分隔线标题，不是独立页签。

| 布局标题 / UI 调用 key | English 实际值 | 简体中文实际值 | 可见参数数 | 对应页面 |
| --- | --- | --- | ---: | --- |
| `General` | General | 通用 | 19 | [通用与应用设置](../desktop/settings/general-and-app.md)、[CLI、批量与输出](../desktop/settings/cli-batch-and-output.md) |
| `OCR` | OCR | 文字识别 | 17 | [OCR、过滤与文本行合并](../desktop/settings/ocr-filter-and-merge.md) |
| `Detection` | Detection | 检测 | 13 | [检测](../desktop/settings/detection.md) |
| `Translation` | Translation | 翻译 | 11 | [翻译设置](../desktop/settings/translation.md) |
| `Inpainting` | Inpainting | 修复 | 10 | [蒙版与图像修复](../desktop/settings/mask-and-inpainting.md) |
| `Typesetting` | Typesetting | 排版 | 28 | [排版与渲染](../desktop/settings/typesetting-and-rendering.md) |
| `Mode Specific` | Mode Specific | 模式相关 | 11 | [模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)、[超分与上色](../desktop/settings/upscale-and-colorization.md) |

七个页签合计 109 个可见参数行。

## UI 调用 key 与实际文案 {#ui-i18n}

本页出现的页签、标题和按钮均核对自 `desktop_qt_ui/locales/en_US.json` 与 `zh_CN.json`：

| UI 调用 key | English 实际值 | 简体中文实际值 |
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

## 参数索引 {#parameter-index}

下表按设置页页签分组，逐项记录存储键、English 与简体中文实际文案，并提供跳转链接。数据来自 `doc/wiki/data/settings.generated.json`（由设置布局、UI 绑定和两个 locale 生成）与 `doc/wiki/data/i18n.generated.json`；每个跳转锚点都已与对应设置页中的显式锚点核对。

### General（通用） {#tab-general}

General 页签的 19 个参数分散在两个页面：应用级参数见[通用与应用设置](../desktop/settings/general-and-app.md)，CLI/输出参数见[CLI、批量与输出](../desktop/settings/cli-batch-and-output.md)。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
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

### OCR（文字识别） {#tab-ocr}

本页签 17 个参数的说明见[OCR、过滤与文本行合并](../desktop/settings/ocr-filter-and-merge.md)。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
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

### Detection（检测） {#tab-detection}

本页签 13 个参数的说明见[检测](../desktop/settings/detection.md)。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
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

### Translation（翻译） {#tab-translation}

本页签 11 个参数的说明见[翻译设置](../desktop/settings/translation.md)。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
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

### Inpainting（修复） {#tab-inpainting}

本页签 10 个参数的说明见[蒙版与图像修复](../desktop/settings/mask-and-inpainting.md)。其中 `inpainter.inpainter`、`inpainter.solid_fill_pure_bubbles`、`inpainter.per_block_inpainting` 在该页没有独立显式锚点，跳转到页面本身。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
| --- | --- | --- | --- |
| `inpainter.inpainter` | Inpainting Model | 修复模型 | [页面](../desktop/settings/mask-and-inpainting.md) |
| `mask_dilation_offset` | Mask Dilation Offset | 遮罩扩张偏移 | [#dilation-and-kernel](../desktop/settings/mask-and-inpainting.md#dilation-and-kernel) |
| `ocr.limit_mask_dilation_to_bubble_mask` | Keep Dilation Inside Bubble Mask | 膨胀不超过气泡蒙版 | [#bubble-range](../desktop/settings/mask-and-inpainting.md#bubble-range) |
| `ocr.use_model_bubble_repair_intersection` | Expand Bubble Repair Range | 扩大气泡修复范围 | [#bubble-range](../desktop/settings/mask-and-inpainting.md#bubble-range) |
| `inpainter.solid_fill_pure_bubbles` | Solid Fill Pure Bubbles | 纯色气泡直接填色 | [页面](../desktop/settings/mask-and-inpainting.md) |
| `inpainter.per_block_inpainting` | Per-Block Inpainting | 逐块修复 | [页面](../desktop/settings/mask-and-inpainting.md) |
| `inpainter.inpainting_size` | Inpainting Size | 修复大小 | [#size-and-precision](../desktop/settings/mask-and-inpainting.md#size-and-precision) |
| `inpainter.inpainting_precision` | Inpainting Precision | 修复精度 | [#size-and-precision](../desktop/settings/mask-and-inpainting.md#size-and-precision) |
| `kernel_size` | Kernel Size | 卷积核大小 | [#dilation-and-kernel](../desktop/settings/mask-and-inpainting.md#dilation-and-kernel) |
| `inpainter.force_use_torch_inpainting` | Force Use PyTorch Inpainting | 强制使用PyTorch修复 | [#force-torch](../desktop/settings/mask-and-inpainting.md#force-torch) |

### Typesetting（排版） {#tab-typesetting}

本页签 28 个参数的说明见[排版与渲染](../desktop/settings/typesetting-and-rendering.md)。该页参数小节没有独立显式锚点，全部跳转到页面本身。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
| --- | --- | --- | --- |
| `render.renderer` | Renderer | 渲染器 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_family` | Font | 字体 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.ai_renderer_prompt_path` | AI Renderer Prompt | AI 渲染提示词 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.ai_renderer_concurrency` | AI Renderer Concurrency | AI 渲染并发数 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.alignment` | Alignment | 对齐方式 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.semantic_linebreak` | Chinese Semantic Line Break | 中文语义断句 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.remove_linebreak_punctuation` | Trim Around Line Breaks | 去除换行符周围逗号句号 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.disable_auto_wrap` | AI Line Breaking | AI断句 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.optimize_line_breaks` | AI Line Break Auto Enlarge | AI断句自动扩大文字 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.strict_smart_scaling` | Don't Expand Box on Auto Enlarge | AI断句自动扩大文字下不扩大文本框 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.check_br_and_retry` | AI Line Break Check | AI断句检查 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.stroke_width` | Stroke Width Ratio | 描边宽度比例 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.disable_font_border` | Disable Font Border | 禁用字体边框 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.center_text_in_bubble` | Center in Bubble | 气泡内居中 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size_offset` | Font Size Offset | 字体大小偏移量 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size_minimum` | Minimum Font Size | 最小字体大小 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.direction` | Text Direction | 文本方向 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.uppercase` | Uppercase | 大写 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.lowercase` | Lowercase | 小写 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.no_hyphenation` | Disable Hyphenation | 禁用连字符 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.bubble_layout_english` | Bubble Layout (Force Horizontal) | 根据气泡排版(强制横排) | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.line_spacing` | Line Spacing | 行间距 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.letter_spacing` | Letter Spacing | 字间距 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_size` | Font Size | 字体大小 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.rtl` | Right to Left | 从右到左 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.layout_mode` | Layout Mode | 排版模式 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.max_font_size` | Maximum Font Size | 最大字体大小 | [页面](../desktop/settings/typesetting-and-rendering.md) |
| `render.font_scale_ratio` | Font Scale Ratio | 字体缩放比例 | [页面](../desktop/settings/typesetting-and-rendering.md) |

### Mode Specific（模式相关） {#tab-mode-specific}

本页签 11 个参数分散在两个页面：直接粘贴与模板对齐参数见[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)，超分与上色参数见[超分与上色](../desktop/settings/upscale-and-colorization.md)。

| 存储值 | English 实际值 | 简体中文实际值 | 跳转 |
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


## 覆盖边界与反向链接 {#coverage-and-backlinks}

- 反向链接：本页是设置页七个专题页与[设置页与配置生命周期](../desktop/settings/index.md)的汇总跳转入口；参数详细说明仍以专题页为准。
- 兄弟参考页：完整枚举选项的 value/i18n 对照见[选项与 i18n 矩阵](./options-i18n-matrix.md)，九种工作流阶段矩阵见[工作流矩阵](./workflow-matrix.md)，各页源码依据汇总见[源码依据索引](./source-evidence-index.md)，调试产物见[调试产物索引](./debug-artifact-index.md)。
- 由其他控件写回、不作为设置参数行显示的值：`upscale.realcugan_model` 由超分模型下拉写回（见[超分与上色](../desktop/settings/upscale-and-colorization.md)）；八个工作流标志（`cli.generate_and_export`、`cli.template`、`cli.translate_json_only`、`cli.load_text`、`cli.colorize_only`、`cli.upscale_only`、`cli.inpaint_only`、`cli.replace_translation`）由翻译工作区的工作流下拉设置（见[模式专用工作流与模板对齐](../desktop/settings/mode-specific.md)）。编辑器偏好（`editor_*`）属于 `AppSection`，但不在 General 页签显示为动态行。
- 运行时列表（字体、模型名、API 预设、提示词文件、批量方案）来自本机或用户配置，不进入本索引的固定清单。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 设置布局 | `desktop_qt_ui/ui/main_page/settings_tab_layout.json` | 七个页签、110 个条目、109 个可见参数、`Advanced` 分隔线 |
| 页面外壳 | `desktop_qt_ui/ui/main_page/pages/settings_page.py` | 页签标题 key、导入/导出、说明面板 |
| 动态控件 | `desktop_qt_ui/ui/main_page/dynamic_settings.py` | 控件类型、文件编辑动作、跳过的字段 |
| UI/i18n | `desktop_qt_ui/app_logic.py`、`desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | label 映射与实际中英文显示值 |
| 配置模型 | `desktop_qt_ui/core/config_models.py`、`manga_translator/config.py` | Qt/核心参数定义与默认 |
| 生成数据 | `doc/wiki/data/settings.generated.json`、`doc/wiki/data/i18n.generated.json` | 109 条参数记录、1353 个 i18n 条目 |
| 调查资料 | `doc/wiki/research/phase0-options-i18n-matrix.md`、`phase0-page-coverage-matrix.md`、`default-sources.md` | 选项矩阵、覆盖矩阵和默认值差异 |
| 设置专题页 | `doc/wiki/zh/desktop/settings/*.md` | 参数锚点与跳转目标逐项核对 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取 1.3 节、5.16 小节并按页面合同编写 |
| 参数清单与锚点 | 完成 | 109 条记录逐项映射，`{#anchor}` 全部存在于目标页 |
| `en_US` / `zh_CN` 实际 locale | 完成 | 页签与 label 文案与两个 locale 实际值逐项一致 |
| 生成数据文件 | 完成 | `settings.generated.json`、`i18n.generated.json` 作为表数据来源 |
| 路由镜像与源码依据脚本 | 完成 | `node scripts/verify-route-mirror.mjs .`、`node scripts/verify-source-evidence.mjs .` 通过 |
| 敏感信息审查 | 完成 | 未读取或写入真实 `.env`、用户配置、API Key、私有路径、用户图片或私有提示词 |
| VitePress 构建 | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` |
