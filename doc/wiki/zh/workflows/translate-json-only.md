---
title: 仅翻译（JSON）
description: 仅翻译 JSON 工作流：从已有工程 JSON 读取原文、翻译并回写，跳过检测、OCR、蒙版、修复与渲染
pageId: workflows.translate-json-only
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 仅翻译（JSON）

当每张图已经存在工程 JSON（例如“导出原文”生成的 `manga_translator_work/json/<stem>_translations.json`，或编辑器中保存过的工程），并且你只想让程序重新翻译 JSON 里的原文、把译文写回 JSON，而不想重新检测、OCR、修复或渲染成图时，使用“仅翻译（JSON）”工作流。它从 JSON 载入文本区域，执行预词典替换和翻译，回写工程 JSON，成功后删除同图的原文副文件；不执行上色、超分、检测、OCR、文本行合并、蒙版、修复和渲染，也不写主输出图。

“仅翻译（JSON）”与“导出译文”“导出原文”“导入翻译并渲染”构成模板/JSON 家族，区别见[导出译文](./export-translation.md)、[导出原文](./export-original.md)和[导入翻译并渲染](./import-translation-and-render.md)；九种工作流的整体边界见[输出目录与工作流](../desktop/translation/output-directory-and-workflow.md)，汇总表见[工作流矩阵](../reference/workflow-matrix.md)。`cli.translate_json_only` 的参数定义见[模式专属工作流与模板对齐](../desktop/settings/mode-specific.md#cli-translate-json-only)。

## 功能边界 {#feature-boundary}

- 本页只覆盖九种工作流中的“仅翻译（JSON）/ Translate JSON Only”（下拉框索引 `3`）。选择该模式时，界面先清空八个互斥工作流字段，再只把 `cli.translate_json_only` 设为 `true` 并保存配置。
- 输入：主输入图片（与正常翻译相同的文件发现规则），且每张图必须能找到可解析的工程 JSON；JSON 接受旧格式（值是一个区域列表）或新格式（值是含 `regions` 的 dict）两种结构。
- 执行阶段：从 JSON 载入区域与蒙版 → 预词典替换 → 翻译 → JSON 回写；成功后删除同图 `<stem>_original.<模板扩展名>` 原文副文件。
- 跳过阶段：条件上色、条件超分、检测、OCR、文本行合并、蒙版细化、修复、渲染和主输出图保存。
- 输出文件：回写 `manga_translator_work/json/<stem>_translations.json`（新位置优先；读取自旧位置时回写仍落到新位置）。
- 工作流字段：下拉框索引 `3` → `cli.translate_json_only=true`；GUI 切换保证八个工作流布尔字段互斥。

## UI 操作 {#ui-operations}

### 选择仅翻译（JSON）工作流 {#select-translate-json-only}

1. 打开“翻译”页（`Translation`），在“翻译任务”（`Translation Task`）卡片中点击“翻译流程模式：”（`Translation Workflow Mode:`）下拉框。
2. 选择“仅翻译（JSON）”（`Translate JSON Only`）。切换时界面只把 `cli.translate_json_only` 设为 `true`，其余七个工作流字段清为 `false` 并保存配置；标题变为“仅翻译（JSON）”，副标题显示对应提示。
3. 点击“开始仅翻译（JSON）”（`Start JSON Translation`）开始按钮启动任务。切换模式不会自动开始任务；任务进行中按钮会变为“停止翻译”（`Stop Translation`）等状态，见[进度、停止与任务状态](../desktop/translation/progress-stop-and-task-state.md)。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Translation Workflow Mode:` | Translation Workflow Mode: | 翻译流程模式： |
| `Translate JSON Only` | Translate JSON Only | 仅翻译（JSON） |
| `Start JSON Translation` | Start JSON Translation | 开始仅翻译（JSON） |
| `Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success` | Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success | 提示：需要预先存在 JSON 数据。程序会从 JSON 读取原文并执行翻译，完成后回写 JSON，并删除图片名_original.txt。 |
| `label_translate_json_only` | Translate JSON Only | 仅翻译（JSON） |
| `label_overwrite` | Overwrite Existing Files | 覆盖已存在文件 |
| `label_batch_concurrent` | Concurrent Batch Processing | 并发批量处理 |

副标题里的 `imagename` / `图片名` 是程序对输入 `<stem>` 的示例称呼，不是用户私有文件名；`manga_translator_work/json/` 是每图工作目录下的固定子目录名。提示中的 `imagename_original.txt` 只是固定示例文案，实际扩展名由模板 `output_format` 决定（默认 `json`），删除原文副文件时按该扩展名定位。

“输出目录:”只决定主输出图的位置；本模式不写主图，因此它不影响 JSON 的读取与回写位置，这两者始终按输入图片的工作目录规则定位。

## 选项中英对照 {#option-matrix}

下拉框没有独立 `userData`，索引就是模式值；运行时代码把索引 `3` 映射到 `cli.translate_json_only=true`。相关设置的存储值如下表，三列 UI 证据与作用并列。

| 存储值 | English | 简体中文 | 本工作流中的实际作用 |
| --- | --- | --- | --- |
| `translate_json_only=true` | Translate JSON Only | 仅翻译（JSON） | 进入 JSON-only 分支，跳过全部图像阶段，只做“JSON 载入 → 翻译 → JSON 回写” |
| `overwrite=false` | Overwrite Existing Files | 覆盖已存在文件 | 开始前跳过“原文副文件不存在”的图片（方向与通常覆盖检查相反，运行提示待验证） |
| `save_text` | Editable Image | 图片可编辑 | 本模式回写 JSON 不依赖该值，JSON-only 分支无条件保存 JSON |
| `batch_concurrent=true` | Concurrent Batch Processing | 并发批量处理 | 本模式强制按非并发处理，界面保存并发配置也不会变成并发管线 |

## 运行机理 {#runtime-behavior}

### 输入与发现规则 {#input-and-discovery}

- 主输入图片：与正常翻译相同的文件发现规则（“添加文件…”/“添加文件夹…”或拖放；递归查找、自然排序、跳过名为 `manga_translator_work` 的目录）。
- JSON 查找：`find_json_path()` 先查新位置 `manga_translator_work/json/<stem>_translations.json`，再回退旧位置（图片同级的 `<stem>_translations.json`）。两种位置都找不到且存在旧格式 `<stem>_translations.txt` 时，回退到 TXT 读取（该格式没有蒙版）。
- 找不到 JSON 时，该图抛出 `FileNotFoundError` 并标记失败，不进入翻译和回写。
- JSON 结构兼容两种格式：旧格式把图片键对应的值存为区域列表；新格式存为 dict，包含 `regions`、`mask_raw`（base64/列表）、`mask_is_refined`、`skip_font_scaling`、`skip_text_replacements` 等字段。代码不校验图片键名，直接取第一个值；区域缺 `target_lang` 时用配置 `translator.target_lang` 回填。

### 跳过与保留的阶段 {#skipped-and-kept-stages}

下面的 Mermaid 展示源码确认的阶段顺序、解析失败保险丝和输出文件；它是九种工作流矩阵中的“JSON 读取 → 翻译 → JSON 回写”分支，与导出原文/导出译文共享 JSON 读写设施，但跳过它们的图像阶段。

```mermaid
flowchart LR
    Img["主输入图片"] --> Find["find_json_path 按图片定位工程 JSON"]
    Find --> NewJson["新位置：manga_translator_work/json/&lt;stem&gt;_translations.json"]
    Find --> OldJson["回退：图片同级 &lt;stem&gt;_translations.json 或 .txt"]
    NewJson --> Load["载入 regions（旧列表 / 新 dict）与蒙版"]
    OldJson --> Load
    Load --> PreDict["预词典替换原文"]
    PreDict --> Translate["翻译（目标语言由配置决定）"]
    Translate --> Fuse{"存在解析失败区域?"}
    Fuse -->|是| Protect["禁止回写 JSON，保留原文件"]
    Fuse -->|否| Writeback["回写工程 JSON"]
    Writeback --> Del["成功后删除同图 _original.&lt;模板扩展名&gt;"]
    Load -. "跳过" .-> Skipped["上色 / 超分 / 检测 / OCR / 文本行合并 / 蒙版 / 修复 / 渲染 / 主图保存"]
```

### JSON 回写细节 {#json-writeback-details}

- 回写调用 `_save_text_to_file()`：写入 `regions`（每区域含坐标、原文、译文、字号等渲染字段）、`original_width`、`original_height`，并强制 `skip_font_scaling=false`（与导出原文一致），使后续“导入翻译并渲染”重新执行智能排版，不继承旧字号。
- `skip_text_replacements` 保持缺省 `false`：本模式不渲染，JSON 里存的是替换规则生效前的原始译文，导入渲染时才应用文本替换规则。
- 蒙版：从 JSON 载入的 `mask_raw`（base64/列表解码）写回；`mask_is_refined` 状态保留。已有 JSON 中的 `paint_overlay`、`stamp_overlay`（编辑器画笔/印章图层）和 `last_export_dir` 会被保留，避免回写覆盖丢失。
- 保险丝：区域解析失败（`load_text_parse_failures > 0`）时禁止回写，改为显式失败并保留原文件，避免把丢失的区域永久覆盖进工程 JSON。
- 成功后调用 `_delete_original_txt_after_json_translation()` 删除同图原文副文件；删除失败只记录警告，不使任务失败。

### 输出文件 {#output-files}

| 输出 | 路径 | 说明 |
| --- | --- | --- |
| 工程 JSON（回写） | `manga_translator_work/json/<stem>_translations.json` | 新位置优先；即使读取自旧位置，回写也落到新位置 |
| 原文副文件（删除） | `manga_translator_work/originals/<stem>_original.<模板扩展名>` | 成功翻译后删除；扩展名由模板 `output_format` 决定，默认 `json` |
| 主输出图 | 不写 | 渲染被跳过，本模式不生成主图 |

## 依赖与冲突 {#dependencies-and-conflicts}

- 依赖每张图存在可解析的工程 JSON；找不到或解析失败时该图失败并保留原文件。
- `batch_concurrent` 不兼容：桌面控制层和 `translate_batch()` 都把它视为不兼容模式，强制按非并发处理；界面仍保存并发配置也不会变成并发管线。
- 手工叠加多个工作流字段不是受支持组合。运行时 `translate_batch()` 的分派顺序里，`load_text` 的 TXT 预导入先执行、`replace_translation` 优先分派，之后才处理 `translate_json_only`；GUI 切换时八个字段互斥。
- 不受 `cli.save_text` 控制：JSON-only 分支在翻译后无条件回写 JSON；这与导出原文要求 `template=true` 且 `save_text=true` 不同。
- 高质量翻译器（`openai_hq`/`gemini_hq`）在本模式下被当作导入/导出模式，跳过专用高质量流程，走标准翻译流程并记录日志警告。
- 本模式仍按所选翻译器产生 API/模型成本；上色、超分、检测、OCR、修复和渲染不产生成本（阶段被跳过）。
- 主输出目录、`save_to_source_dir`、`cli.format` 只影响主输出图；本模式不写主图，因此这些设置对本工作流输出无直接影响。

## 关联文件与格式 {#related-files-and-formats}

| 文件/格式 | 本页实际作用 | 说明 |
| --- | --- | --- |
| `manga_translator_work/json/<stem>_translations.json` | 输入与回写目标 | 新位置优先；旧图片同级 JSON 仅作为输入回退，回写仍到新位置 |
| `<stem>_translations.txt` | 旧格式输入回退 | 兼容旧 TXT，无蒙版 |
| `manga_translator_work/originals/<stem>_original.<format>` | 成功后的删除对象 | 扩展名由 `config/translation_template.json` 的 `output_format` 决定，默认 `json` |
| `config/translation_template.json` | 决定原文副文件扩展名 | 仅在按模板扩展名定位原文副文件时读取 |
| 主输出图 | 不产出 | 本模式跳过主图保存 |

不在本页展示真实用户配置、密钥、令牌、用户名、私有绝对路径、用户图片或任务产物。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 工作流选择与写入 | `desktop_qt_ui/ui/main_page/runtime.py:183-215` | 索引 `3` → `translate_json_only=true`、八字段互斥和配置保存 |
| 标题、提示与开始按钮 | `desktop_qt_ui/ui/main_page/runtime.py:22-47,219-238` | “Translate JSON Only”标题、提示调用 key 和按钮文案 |
| i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 下拉、提示、按钮和 `label_*` 实际双语值 |
| 控制层 | `desktop_qt_ui/app_logic.py:3142-3148,3231-3233,3242-3284` | 原文副文件不存在时跳过、工作流提示、特殊模式并发禁用 |
| Qt 配置 | `desktop_qt_ui/core/config_models.py:123-147` | `translate_json_only` 等字段与默认值 |
| 核心分派 | `manga_translator/manga_translator.py:3448,3481,3495,3988-4079` | JSON-only 分支、并发禁用、载入→翻译→回写→删原文 |
| JSON 读取 | `manga_translator/manga_translator.py:1325-1524` | `_load_text_and_regions_from_file`、新旧结构、解析失败计数 |
| JSON 回写 | `manga_translator/manga_translator.py:713-911` | `_save_text_to_file`、`skip_font_scaling=false`、蒙版与覆盖层保留 |
| 原文副文件删除 | `manga_translator/manga_translator.py:949-958` | `_delete_original_txt_after_json_translation` |
| 路径 | `manga_translator/utils/path_manager.py:151-227,367-389` | `get_json_path`、`find_json_path`、`get_original_txt_path` |
| 参数定义 | `manga_translator/config.py:422-425` | `translate_json_only` 字段语义 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取并按页面合同编写；不修改三份合同文件 |
| 源码与研究资料 | 完成 | 已核对 `workflow-matrix-source-evidence.md`、`phase0-related-files-formats-debug-safety.md` 与 UI、i18n、控制层和核心源码 |
| i18n 三列证据 | 完成 | 工作流选项、提示、按钮和相关设置均记录调用 key、English、简体中文实际值 |
| 路由/页面镜像 | 完成（本页） | 本页 zh/en 镜像与 source evidence 脚本均通过；全仓库 route mirror 因其他代理进行中的页面暂未通过 |
| 运行态待确认 | 待运行 | “原文副文件不存在则跳过”的 GUI 行为、JSON 解析失败提示、旧 TXT 回退和压缩包输入需脱敏运行验证 |
| 生产构建 | 待运行 | 必要时运行 `npm run docs:build --prefix doc/wiki` |

- [ ] [进行中] 运行态待确认：`overwrite=false` 时“原文副文件不存在则跳过”的实际提示、JSON 解析失败的可见反馈、旧 TXT 回退与压缩包输入下的副文件配对。