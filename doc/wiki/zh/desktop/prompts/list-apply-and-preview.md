---
title: 提示词列表、应用与预览
description: 管理 dict/ 下的用户提示词文件，把选中文件应用到翻译器并预览结构化或 Raw 内容
pageId: desktop.prompts.list-apply-and-preview
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 提示词列表、应用与预览

当自定义 HQ 提示词由文件维护时，本页用于查看 `dict/` 下的用户提示词文件、把选中文件写入翻译器配置、预览文件内容并进入编辑。本页不解释“自定义提示词”参数本身的含义（见[上下文与提示词](../translator/context-and-prompts.md)），也不管理系统提示词与 AI OCR/上色/渲染提示词的固定文件（分别见[系统与翻译提示词](./system-and-translation-prompts.md)、[AI OCR 提示词](./ai-ocr-prompt.md)、[AI 上色提示词](./ai-colorizer-prompt.md)和[AI 渲染提示词](./ai-renderer-prompt.md)）。

## 功能边界 {#feature-boundary}

- 列表只显示 `dict/` 下 `.yaml`、`.yml`、`.json` 的用户提示词文件，并排除系统提示词文件名（`system_prompt_hq`、`system_prompt_hq_format`、`system_prompt_line_break`、`glossary_extraction_prompt`、`ai_ocr_prompt`、`ai_colorizer_prompt`、`ai_renderer_prompt`）。
- “应用所选提示词”把 `dict/<文件名>` 写入 `translator.high_quality_prompt_path` 并持久化到 `config/config.json`；它不切换翻译器类型、API 凭据或候选槽。
- 预览分为“结构化”和“Raw”两种展示；“编辑”入口打开二级弹窗，结构化文件包含“模板编辑 / 源码编辑”两个页签。
- 本页不写入真实密钥或私密提示词正文；错误信息中的本机路径不得复制进公开报告。

## UI 操作 {#ui-operations}

### 查看提示词列表

1. 从左侧导航打开“提示词管理”（`Prompt Management`）。页面标题为“提示词管理”，副标题为“管理和应用翻译提示词文件”。
2. “提示词列表”（`Prompt List`）卡片显示可用文件。当前已应用的提示词带 `* ` 前缀、加粗并使用主题色，悬停提示“当前提示词：{filename}”。
3. 状态标签显示“找到 {count} 个提示词文件。”。切回该页或点击“刷新”（`Refresh`）会重新扫描 `dict/`；点击“打开目录”（`Open Directory`）会用系统文件管理器打开 `dict/` 目录。

### 应用所选提示词

1. 在列表中选中一个提示词文件。
2. 点击“应用所选提示词”（`Apply Selected Prompt`），或直接双击列表项。
3. 程序把 `dict/<文件名>` 写入 `translator.high_quality_prompt_path` 并保存配置；列表刷新后该项成为当前提示词，状态标签显示“当前提示词：{filename}”。
4. 在“设置”→“翻译”分组的“自定义提示词”（`Custom Prompt`）中可以看到同一个路径。

### 预览结构化与 Raw 内容

- 未选中文件时，右侧“提示词预览”（`Prompt Preview`）显示“选择一个提示词文件以预览”，编辑按钮不可用。
- 选中文件后，标题区显示文件名；若文件能解析为字典且包含结构化字段（`system_prompt`、`project_data`、`style_guide`、`translation_rules`、`glossary`，或上色提示词字段），显示结构化分区：系统提示词、项目/项目数据与术语表、风格指南、翻译规则、术语词典（按人物/地点/组织/物品/技能/生物分类）；上色文件另显示提示词正文、上色规则和参考图片。
- 无法解析或非结构化内容显示“无法识别格式 – 显示原始内容”，以只读文本框展示原文。
- 预览内容只读；修改文件请使用“编辑”按钮。

### 进入编辑

1. 在预览面板右上角点击“编辑”（`Edit`）。
2. 结构化文件打开“编辑提示词”（`Edit Prompt`）弹窗，包含“模板编辑 / 源码编辑”两个页签；非结构化文件只有“源码编辑”。
3. 模板编辑按字段分区编辑，可通过“添加字段”（`Add Section`）增删字段、上移/下移分区；源码编辑直接修改原始文本。
4. 保存时校验格式（YAML/JSON）并写回 UTF-8 文件；成功后状态显示“保存成功”，预览自动刷新。AI 上色提示词文件（`ai_colorizer_prompt.yaml`）打开专用的上色提示词编辑器。

### 新建、复制、重命名与删除

- “新建”（`New`）创建 YAML 提示词模板，输入文件名（不含扩展名）后写入 `dict/`。
- “复制”（`Copy`）复制选中文件，默认新名为 `原文件名_copy`。
- “重命名”（`Rename`）重命名选中文件；若重命名的是当前提示词，`translator.high_quality_prompt_path` 会同步更新。
- “删除”（`Delete`）弹出“确认删除”并询问“确定要删除此提示词文件吗？”；删除当前提示词时会清空路径。
- 新建/复制/重命名都会校验文件名（非法字符、重名、无效扩展名），成功后状态标签显示“已创建/已复制/已重命名为/已删除：{filename}”。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Prompt Management` | Prompt Management | 提示词管理 |
| `Manage and apply prompt files for translation` | Manage and apply prompt files for translation | 管理和应用翻译提示词文件 |
| `Prompt List` | Prompt List | 提示词列表 |
| `Prompt Preview` | Prompt Preview | 提示词预览 |
| `Apply Selected Prompt` | Apply Selected Prompt | 应用所选提示词 |
| `New` | New | 新建 |
| `Copy` | Copy | 复制 |
| `Rename` | Rename | 重命名 |
| `Delete` | Delete | 删除 |
| `Refresh` | Refresh | 刷新 |
| `Open Directory` | Open Directory | 打开目录 |
| `Edit` | Edit | 编辑 |
| `Save` | Save | 保存 |
| `Cancel` | Cancel | 取消 |
| `OK` | OK | 确定 |
| `Warning` | Warning | 警告 |
| `Error` | Error | 错误 |
| `Found {count} prompt files.` | Found {count} prompt files. | 找到 {count} 个提示词文件。 |
| `Current prompt: {filename}` | Current prompt: {filename} | 当前提示词：{filename} |
| `Created: {filename}` | Created: {filename} | 已创建：{filename} |
| `Copied: {filename}` | Copied: {filename} | 已复制：{filename} |
| `Renamed to: {filename}` | Renamed to: {filename} | 已重命名为：{filename} |
| `Deleted: {filename}` | Deleted: {filename} | 已删除：{filename} |
| `Select a prompt file to preview` | Select a prompt file to preview | 选择一个提示词文件以预览 |
| `File not found` | File not found | 文件不存在 |
| `Unrecognized format – showing raw content` | Unrecognized format – showing raw content | 无法识别格式 – 显示原始内容 |
| `Error reading file: {error}` | Error reading file: {error} | 读取文件出错：{error} |
| `New Prompt` | New Prompt | 新建提示词 |
| `Copy Prompt` | Copy Prompt | 复制提示词 |
| `Rename Prompt` | Rename Prompt | 重命名提示词 |
| `Edit Prompt` | Edit Prompt | 编辑提示词 |
| `Enter prompt file name (without extension):` | Enter prompt file name (without extension): | 输入提示词文件名（不含扩展名）： |
| `Enter new prompt file name (without extension):` | Enter new prompt file name (without extension): | 输入新的提示词文件名（不含扩展名）： |
| `Template Edit` | Template Edit | 模板编辑 |
| `Raw Edit` | Raw Edit | 源码编辑 |
| `System Prompt` | System Prompt | 系统提示词 |
| `Project` | Project | 项目 |
| `Project Data` | Project Data | 项目数据 |
| `Project Title` | Project Title | 项目标题 |
| `Terminology` | Terminology | 术语表 |
| `Style Guide` | Style Guide | 风格指南 |
| `Translation Rules` | Translation Rules | 翻译规则 |
| `Glossary` | Glossary | 术语词典 |
| `Original` | Original | 原文 |
| `Translation` | Translation | 翻译 |
| `Prompt Text` | Prompt Text | 提示词正文 |
| `Colorization Rules` | Colorization Rules | 上色规则 |
| `Reference Images` | Reference Images | 参考图片 |
| `Add Section` | Add Section | 添加字段 |
| `Confirm Delete` | Confirm Delete | 确认删除 |
| `Are you sure you want to delete this prompt file?` | Are you sure you want to delete this prompt file? | 确定要删除此提示词文件吗？ |
| `Invalid file name.` | Invalid file name. | 文件名无效。 |
| `File already exists` | File already exists | 文件已存在 |
| `Please select a prompt file first.` | Please select a prompt file first. | 请先选择一个提示词文件。 |
| `Selected prompt file does not exist.` | Selected prompt file does not exist. | 所选提示词文件不存在。 |
| `Loaded successfully` | Loaded successfully | 加载成功 |
| `Saved successfully` | Saved successfully | 保存成功 |
| `Save failed` | Save failed | 保存失败 |
| `Format Error` | Format Error | 格式错误 |
| `Serialize Error` | Serialize Error | 序列化错误 |
| `label_high_quality_prompt_path` | Custom Prompt | 自定义提示词 |

## 空状态与错误状态 {#empty-and-error-states}

| 触发条件 | 列表/状态标签 | 预览面板 | 可用操作 |
| --- | --- | --- | --- |
| 尚未选中文件 | 状态标签保持“找到 {count} 个提示词文件。” | “选择一个提示词文件以预览”；编辑禁用 | 新建 / 刷新 / 打开目录 |
| `dict/` 没有可用用户文件 | “找到 0 个提示词文件。”；列表为空 | 同上（清空状态） | 新建 / 打开目录 |
| 选中项文件已被外部删除 | 列表项可能仍短暂存在 | “文件不存在”；编辑禁用 | 刷新 / 删除 |
| 文件存在但解析失败或根类型不是字典 | 状态标签不变 | “无法识别格式 – 显示原始内容” | 编辑（源码编辑保存时校验） |
| 读取文件发生 I/O 错误 | 状态标签不变 | “读取文件出错：{error}” | 编辑 |
| 编辑器保存时格式或序列化错误 | 编辑器状态区显示“格式错误 / 序列化错误 / 保存失败” | — | 修正后重新保存 |

错误信息可能包含本机路径或解析细节；复制到公开报告前必须脱敏。

## 运行机理 {#runtime-behavior}

列表刷新、选中预览、应用和编辑共享同一条数据流：

```mermaid
flowchart LR
    A["打开提示词管理页"] --> B["refresh_prompt_manager 扫描 dict/"]
    B --> C{"存在 .yaml/.yml/.json 用户文件?"}
    C -->|否| D["列表为空；状态：找到 0 个提示词文件"]
    C -->|是| E["列表项 = 文件名；当前提示词带 * 前缀加粗"]
    E --> F["选中列表项"]
    F --> G{"文件存在?"}
    G -->|否| H["预览：文件不存在；编辑禁用"]
    G -->|是| I["按扩展名解析 YAML 或 JSON"]
    I --> J{"解析为字典且含结构化字段?"}
    J -->|是| K["结构化预览：系统提示词/项目/术语/风格/规则/术语词典"]
    J -->|否| L["Raw 预览：显示原始内容"]
    K --> M["点击编辑 → 编辑器弹窗"]
    L --> M
    M --> N["模板编辑或源码编辑"]
    N --> O["校验并写回文件"]
    O --> P["预览刷新"]
    F --> Q["应用所选提示词"]
    Q --> R["写入 translator.high_quality_prompt_path"]
    R --> S["保存到 config.json；列表刷新并标记当前"]
    S --> T["翻译开始时加载并拼入系统提示词"]
```

- 列表来源：`controller.get_hq_prompt_options()` 扫描 `config_service.root_dir/dict`，只收集 `.yaml/.yml/.json`，按文件名排序，并排除系统提示词文件名。`refresh_prompt_manager` 用“文件元组 + 当前文件名”签名判断是否需要重建列表。
- 应用动作：`apply_selected_prompt` 发出 `setting_changed("translator.high_quality_prompt_path", "dict/<文件名>")`；`app_logic.update_single_config` 更新配置模型并调用 `save_config_file()` 持久化。该键不会热更新翻译服务，只在翻译开始时读取。
- 预览判定：`PromptPreviewPanel.load_file` 先检查文件是否存在，再用 `yaml.safe_load` / `json.load` 解析；`_is_structured` 要求根是字典且含至少一个结构化字段。解析失败或非结构化内容一律走 Raw 预览。
- 编辑保存：`PromptEditorDialog` 在模板页签收集字段并序列化（YAML 用 `allow_unicode` 输出，JSON 用 `indent=2`），在源码页签校验 JSON/YAML 语法，最后以 UTF-8 写回；关闭后预览刷新。
- 最终消费者：翻译开始时 `_load_and_prepare_prompts` 把相对路径 `dict/<文件名>` 解析为绝对路径，用 `load_custom_prompt` 加载（文件缺失时会尝试替换扩展名），存入 `ctx.custom_prompt_json`；`_build_system_prompt` 用 `_flatten_prompt_data` 把它展平后放在基础系统提示词之前。开启 `extract_glossary` 时，翻译器把提取到的新术语通过 `merge_glossary_to_file` 写回提示词文件的 `glossary` 字段。

## 依赖与冲突 {#dependencies-and-conflicts}

- `translator.high_quality_prompt_path` 由 OpenAI / Gemini 翻译器（含 HQ 变体）在翻译阶段消费，`_load_and_prepare_prompts` 会在配置了该路径时加载自定义提示词；Sakura 等不使用该字段的翻译器不会读取它，切换到这类翻译器时路径仍保留但不会被消费。
- 应用动作只写配置键，不切换翻译器或 API 候选槽；相关边界见[翻译器选择](../translator/selection-and-languages.md)与 API 管理页面。
- 列表排除系统提示词与 AI OCR/上色/渲染提示词文件；这些固定提示词在“设置”→“OCR / 排版 / 模式专用”中编辑，不在本页 CRUD。
- 删除当前提示词会清空路径；重命名当前提示词会同步路径。应用前文件会被重新校验。
- 提示词正文属于用户内容；共享日志、请求导出或调试目录前必须删除提示词正文、本机路径与凭据。

## 关联文件与格式 {#files-and-formats}

| 文件/格式 | 本页实际作用 | 手改与兼容注意 |
| --- | --- | --- |
| `dict/` | 用户提示词文件目录；列表扫描 `.yaml/.yml/.json` | 系统提示词文件被排除；新建/复制/重命名接受 `.txt` 扩展名，但列表扫描不含 `.txt` |
| `config/config.json` | 持久化 `translator.high_quality_prompt_path` | 不读取或展示真实用户文件 |
| `config/config-example.json` | 发行默认 HQ 路径示例 | 只使用脱敏示例 |
| `.yaml` / `.yml` / `.json` | 提示词文件格式 | 根必须是字典；字段由提示词加载器支持 |
| `dict/prompt_example.yaml` | 默认自定义 HQ 提示词示例 | 保持可解析的 YAML 根结构 |
| `dict/ai_colorizer_prompt.yaml` | AI 上色提示词固定文件，打开专用编辑器 | 含 `reference_images` 时逐项检查，不展示私人参考图 |
| `dict/system_prompt_hq*.yaml`、`dict/ai_*_prompt.yaml` | 系统/AI 固定提示词，被列表排除 | 不属于本页管理范围 |

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| UI 布局 | `desktop_qt_ui/ui/main_page/pages/prompt_page.py` | 列表卡片、按钮行、状态标签、预览面板与信号连接 |
| 页面控制器 | `desktop_qt_ui/ui/main_page/layout.py` | 刷新、应用、选中预览、编辑、新建/复制/重命名/删除 |
| 应用与持久化 | `desktop_qt_ui/app_logic.py` | `get_hq_prompt_options`、`open_dict_directory`、`update_single_config`、HQ 路径解析 |
| 预览与编辑 | `desktop_qt_ui/ui/secondary_pages/prompt_preview.py`、`ai_colorizer_prompt_editor.py` | 结构化/Raw 判定、空状态、模板/源码页签与保存 |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | key 映射和实际中英文显示值 |
| 加载与消费者 | `manga_translator/translators/prompt_loader.py`、`manga_translator/manga_translator.py`、`manga_translator/translators/common.py`、`manga_translator/translators/openai.py`、`openai_hq.py`、`gemini.py`、`gemini_hq.py` | 加载、路径回退、系统提示词组合与术语写回 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取并按页面合同编写 |
| UI 布局与调用 | 完成 | 静态核对提示词页、预览面板、编辑器和控制器 |
| `en_US` / `zh_CN` 实际 locale | 完成 | 页面表格逐项记录 key、English、简体中文实际值 |
| 列表/应用/预览运行链 | 完成 | 静态核对扫描、应用持久化、预览判定与最终消费者 |
| 脱敏运行验证 | 待后续 | 本页未读取真实 `.env`、用户 `config.json`、API key/token、用户名、用户图片或私有提示词 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |