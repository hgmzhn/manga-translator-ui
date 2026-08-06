---
title: 系统提示词与翻译提示词
description: 说明系统提示词与翻译提示词的配置文件、配置键、加载与组合顺序、占位符替换以及进入 OpenAI/Gemini 系统指令的路径
pageId: desktop.prompts.system-and-translation-prompts
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 系统提示词与翻译提示词

翻译请求由系统提示词与翻译提示词共同驱动：系统提示词文件控制“如何翻译”与“输出什么格式”，翻译提示词提供自定义规则、术语提取和 AI 断句要求。当你需要知道某个提示词文件何时被读取、以什么顺序拼接、`target_lang` 占位符在哪里替换，以及最终怎样进入 OpenAI/Gemini 请求时，使用本页。

本页不覆盖提示词文件的列表、应用与 CRUD（见[提示词列表、应用与预览](./list-apply-and-preview.md)），也不覆盖结构化编辑器（见[提示词结构化编辑器](./structured-editor-and-format.md)）；AI OCR、AI 上色和 AI 渲染提示词分别见[AI OCR 提示词](./ai-ocr-prompt.md)、[AI 上色提示词](./ai-colorizer-prompt.md)和[AI 渲染提示词](./ai-renderer-prompt.md)。上下文历史页如何变成消息见[上下文与提示词](../translator/context-and-prompts.md)。

## 功能边界 {#feature-boundary}

- 系统提示词固定文件：`dict/system_prompt_hq.yaml`（基础系统提示词）与 `dict/system_prompt_hq_format.yaml`（输出格式）。它们由运行时按文件名 stem 从 `dict/` 加载，不属于用户提示词列表，桌面端没有专用编辑器。
- 翻译提示词：`translator.high_quality_prompt_path` 指向的自定义 HQ 提示词（`dict/` 下的 `.yaml`/`.yml`/`.json` 用户文件）、`dict/glossary_extraction_prompt.yaml`（术语提取规则）与 `dict/system_prompt_line_break.yaml`（AI 断句提示词）。
- 相关配置键：`translator.high_quality_prompt_path`、`translator.extract_glossary`、`render.disable_auto_wrap`；完整参数文档见[翻译设置](../settings/translation.md)与[排版与渲染](../settings/typesetting-and-rendering.md)。
- 本页只说明提示词如何加载、组合并进入 OpenAI/Gemini 系统指令；不涉及翻译器选择、API 凭据和候选槽轮换（见[翻译器选择](../translator/selection-and-languages.md)与[API 管理页](../api-management/slots-and-rotation.md)）。
- 不在页面中写入真实 API Key、私有提示词正文或本机绝对路径；提示词内容属于用户数据，共享日志、请求导出或调试目录前必须删除。

## UI 操作 {#ui-operations}

### 在提示词管理页选择并应用翻译提示词 {#apply-translation-prompt}

打开“提示词管理”（`Prompt Management`），“提示词列表”（`Prompt List`）只显示 `dict/` 下的用户提示词文件，并排除系统提示词 stem（`system_prompt_hq`、`system_prompt_hq_format`、`system_prompt_line_break`、`glossary_extraction_prompt`、`ai_ocr_prompt`、`ai_colorizer_prompt`、`ai_renderer_prompt`）。选中文件后点击“应用所选提示词”（`Apply Selected Prompt`），程序把 `dict/<文件名>` 写入 `translator.high_quality_prompt_path` 并持久化，状态标签显示“当前提示词：{filename}”。

列表、预览和编辑的完整操作见[提示词列表、应用与预览](./list-apply-and-preview.md)；结构化编辑与保存校验见[提示词结构化编辑器](./structured-editor-and-format.md)。

### 在设置页打开相关开关 {#settings-toggles}

1. 打开“设置”（`Settings`）→“翻译”（`Translation`）分组，打开“自动提取新术语”（`Auto Extract Glossary`）。该开关写入 `translator.extract_glossary`；只有同时存在可解析的自定义提示词时，翻译请求才会追加术语提取规则与 `new_terms` 输出格式。
2. 打开“设置”→“排版”（`Typesetting`）分组，打开“AI 断句”（`AI Line Breaking`）。该开关写入 `render.disable_auto_wrap`；开启后翻译请求会加载 `dict/system_prompt_line_break.yaml`，并在用户输入 JSON 的每个区域上附加 `original_region_count`。
3. `translator.high_quality_prompt_path` 的界面显示名是“自定义提示词”（`Custom Prompt`）。它的动态设置控件在 `dynamic_settings.py` 中实现（打开下拉时重新扫描 `dict/` 并排除系统提示词）；实际设置该键的主要入口是提示词管理页的“应用所选提示词”。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Settings` | Settings | 设置 |
| `Translation` | Translation | 翻译 |
| `Typesetting` | Typesetting | 排版 |
| `label_high_quality_prompt_path` | Custom Prompt | 自定义提示词 |
| `label_extract_glossary` | Auto Extract Glossary | 自动提取新术语 |
| `label_disable_auto_wrap` | AI Line Breaking | AI 断句 |
| `Prompt Management` | Prompt Management | 提示词管理 |
| `Prompt List` | Prompt List | 提示词列表 |
| `Apply Selected Prompt` | Apply Selected Prompt | 应用所选提示词 |
| `System Prompt` | System Prompt | 系统提示词 |
| `Prompt Text` | Prompt Text | 提示词正文 |
| `Current prompt: {filename}` | Current prompt: {filename} | 当前提示词：{filename} |

## 配置键 {#config-keys}

| 配置键 | 存储值/控件 | 默认值（核心 / Qt / 发行示例） | 加载触发与消费 |
| --- | --- | --- | --- |
| `translator.high_quality_prompt_path` | 提示词文件路径字符串，如 `dict/<文件名>` | `None` / `dict/prompt_example.yaml` / `dict/prompt_example.yaml` | 翻译批次准备时加载自定义 HQ 提示词到 `ctx.custom_prompt_json`；只有 OpenAI/Gemini 及其 HQ 变体消费 |
| `translator.extract_glossary` | 开关 `true` / `false` | `false` / `false` / `false` | 仅当自定义提示词 JSON 有效时，把术语提取规则与扩展 `new_terms` 输出格式拼入系统提示词，并把响应的 `new_terms` 写回提示词文件 |
| `render.disable_auto_wrap` | 开关 `true` / `false` | `false` / `true` / `false` | 开启时加载 `system_prompt_line_break.yaml` 并在用户 JSON 附加 `original_region_count`；同时影响排版自动换行 |

三类默认值不应合并写成单一默认；Qt 的 `disable_auto_wrap` 默认与核心/发行不同，以设置页实际值为准。

## 运行机理 {#runtime-behavior}

### 文件加载时机 {#loading-timing}

翻译批次开始前，`_load_and_prepare_prompts()` 做一次提示词准备：

- 若 `translator.high_quality_prompt_path` 非空，先把路径规范化（`normalize_server_resource_path`），相对路径再与 `BASE_PATH` 拼接，然后调用 `load_custom_prompt()` 解析。精确路径不存在时会按 `.yaml` → `.yml` → `.json` 顺序替换扩展名重试；解析失败只记录警告，不中断翻译。
- 若 `render.disable_auto_wrap` 为真，调用 `load_line_break_prompt()` 从 `dict/` 加载 `system_prompt_line_break`，结果存入 `ctx.line_break_prompt_json`；文件缺失同样只记警告。

基础系统提示词、输出格式与术语提取提示词不在准备阶段预载：每次构造请求（包括重试）时，`_build_system_prompt()` 按 stem 从 `dict/` 现读 `system_prompt_hq`、`system_prompt_hq_format`，术语模式再读 `glossary_extraction_prompt`。加载器优先 `.yaml`，其次 `.yml`，最后 `.json`。

### 组合顺序 {#composition-order}

`_build_system_prompt()` 把提示词拼成一段单一文本，顺序固定为：重试提示（仅重试时）→ AI 断句提示（可选）→ 自定义 HQ 提示词（可选）→ 基础系统提示词 → 输出格式。自定义提示词由 `_flatten_prompt_data()` 递归展平为文本块；开启术语提取且自定义提示词有效时，基础提示词之后依次追加术语提取规则和带 `new_terms` 的扩展输出格式，各段之间用 `\n\n---\n\n` 分隔。

```mermaid
flowchart LR
    R["重试提示（仅重试时）"] --> L["AI 断句提示（可选）"]
    L --> C["自定义 HQ 提示词（可选）"]
    C --> S["system_prompt_hq 或代码 fallback"]
    S --> G{"extract_glossary 且\n自定义提示词有效?"}
    G -->|否| O["标准 translations 输出格式"]
    G -->|是| T["术语提取规则 + new_terms 输出格式"]
    O --> API["OpenAI system 消息\n或 Gemini system_instruction"]
    T --> API
    API --> U["当前页 JSON user 提示词"]
```

基础系统提示词缺失或为空时使用代码内 fallback（`_HQ_FALLBACK_PROMPT`）；输出格式提示词缺失时记录日志但请求仍会发出，只是缺少严格格式约束。

### 占位符替换 {#placeholders}

提示词文件中的占位符是三层花括号的字面量标记（例如 <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code>），不是 Python 字符串格式化语法。每次请求构造时，运行时把标记替换为目标语言全称：`VALID_LANGUAGES` 把语言代码映射为全称（如 `CHS` → `Chinese (Simplified)`、`JPN` → `Japanese`），未收录的代码原样保留。替换只发生在内存中的请求文本，不会改写 `dict/` 文件。

| 占位符 | 所在文件 | 替换时机 | 替换为 |
| --- | --- | --- | --- |
| <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code> | `system_prompt_hq`、`system_prompt_hq_format`、自定义提示词、`glossary_extraction_prompt` | 每次请求构造 | 目标语言全称 |
| <code>&#123;&#123;&#123;optional_new_terms_rule&#125;&#125;&#125;</code> | `system_prompt_hq_format` | 仅 `extract_glossary=True` | 要求输出 `new_terms` 键的规则文本；普通模式替换为空 |
| <code>&#123;&#123;&#123;optional_new_terms_example_suffix&#125;&#125;&#125;</code> | `system_prompt_hq_format` | 仅 `extract_glossary=True` | 输出 JSON 示例中的 `new_terms` 段；普通模式替换为空 |
| <code>&#123;&#123;&#123;optional_new_terms_final_instruction&#125;&#125;&#125;</code> | `system_prompt_hq_format` | 仅 `extract_glossary=True` | 未找到新术语时返回 `"new_terms": []` 的结尾指令；普通模式替换为空 |

### 进入 OpenAI/Gemini 系统指令 {#system-instruction-path}

组合后的系统提示词作为一个整体注入，不会按文件拆成多条 system 消息：

```mermaid
flowchart TD
    B["_build_system_prompt 组合后的单一文本"] --> O{"翻译实现"}
    O -->|OpenAI| O1["messages[0] = role=system\ncontent = 系统提示词"]
    O1 --> O2["历史上下文 user/assistant 消息"]
    O2 --> O3["当前页 role=user（HQ 含图片）"]
    O -->|Gemini| G1["generation_config.system_instruction\n= 系统提示词"]
    G1 --> G2["contents = 历史 user/model parts"]
    G2 --> G3["当前页 user parts"]
```

- OpenAI（`openai.py`、`openai_hq.py`）：系统提示词放入 `messages[0]`（`role=system`），随后插入 `_build_openai_context_messages()` 生成的历史 `user`/`assistant` 消息，最后追加当前页 `user` 请求；HQ 模式的用户消息包含图片内容。
- Gemini（`gemini.py`、`gemini_hq.py`）：系统提示词赋给 `generation_config.system_instruction`，`contents` 先插入 `_build_gemini_context_messages()` 生成的历史 `user`/`model` parts，最后追加当前页 `user` parts。
- 术语模式开启且响应包含 `new_terms` 时，OpenAI/Gemini 都会调用 `merge_glossary_to_file()`，把新术语合并写回自定义提示词文件的 `glossary` 字段（按扩展名写 YAML 或 JSON）。
- 流式与非流式传输使用同一套系统指令和消息构建；`disable_auto_wrap` 开启时，当前页用户 JSON 中每个区域带有 `original_region_count`，供最终渲染检查 `[BR]` 标记数量。

## 依赖与冲突 {#dependencies-and-conflicts}

- “自动提取新术语”单独开启无效：代码要求 `bool(custom_prompt_json) and extract_glossary` 同时为真，即必须存在可解析的自定义提示词且开关开启。
- 基础系统提示词缺失回退到代码内置文本；格式或术语提示词缺失只减弱约束、不崩溃；自定义提示词解析失败时跳过该文件继续使用基础提示词。
- 术语写回会修改 `dict/` 下的自定义提示词文件；如果不想让运行时改动文件，关闭“自动提取新术语”或改用只读副本。
- 系统提示词文件被用户提示词列表排除且没有桌面编辑器；手动修改需保持 YAML/JSON 根结构为对象。
- `render.disable_auto_wrap` 同时影响排版换行和翻译请求（断句提示词 + `original_region_count`），不是纯渲染开关；Qt 默认 `true` 与核心/发行默认 `false` 不一致。
- 提示词正文可能包含业务文本并原样进入请求与日志；共享前必须删除提示词正文、历史文本、路径和凭据。

## 关联文件与格式 {#files-and-formats}

| 文件/格式 | 本页实际作用 | 手改与兼容注意 |
| --- | --- | --- |
| `dict/system_prompt_hq.yaml` | 基础 HQ 系统提示词（key `system_prompt`） | 缺失/为空时代码 fallback；保持 YAML 可解析 |
| `dict/system_prompt_hq_format.yaml` | 输出格式提示词（key `system_prompt_hq_format`，含四个占位符） | 缺失时输出约束减弱；占位符按上表替换 |
| `dict/glossary_extraction_prompt.yaml` | 术语提取规则（key `glossary_extraction_prompt`） | 仅术语模式参与，替换 <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code> |
| `dict/system_prompt_line_break.yaml` | AI 断句提示词（key `line_break_prompt`） | 由 `render.disable_auto_wrap` 触发 |
| `dict/prompt_example.yaml` | 默认自定义 HQ 提示词示例 | 通过 `translator.high_quality_prompt_path` 加载；只记录结构，不展示私密正文 |
| `.yaml` / `.yml` / `.json` | 加载器支持的提示词格式 | 根必须是对象；同 stem 优先 `.yaml` |
| `config/config-example.json`、`config/config.json` | 发行默认与用户设置持久化 | 不读取或展示真实用户文件 |

## Mermaid 数据流限制 {#diagram-limits}

上图中的组合与注入路径是源码确认的真实数据转换；它们不代表每次运行都会加载全部文件或发起网络请求。`disable_auto_wrap=false`、提示词路径未配置、文件缺失或解析失败、非 HQ 翻译器、术语开关关闭，都会走对应旁路。文档没有伪造运行截图或私有任务产物。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 配置定义 | `manga_translator/config.py`、`desktop_qt_ui/core/config_models.py`、`config/config-example.json` | 三个配置键及核心/Qt/发行三类默认值 |
| 提示词加载 | `manga_translator/translators/prompt_loader.py` | stem 解析顺序、YAML/JSON 解析、占位符替换、系统文件排除 |
| 组合与请求构建 | `manga_translator/translators/common.py` | `_flatten_prompt_data`、`_build_system_prompt`、fallback、`merge_glossary_to_file` |
| 批次准备 | `manga_translator/manga_translator.py` | `_load_and_prepare_prompts` 的加载时机与路径解析 |
| UI/i18n | `desktop_qt_ui/ui/main_page/pages/prompt_page.py`、`desktop_qt_ui/app_logic.py`、`desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 应用所选提示词、显示名映射、实际中英文案 |
| 最终消费者 | `manga_translator/translators/openai.py`、`openai_hq.py`、`gemini.py`、`gemini_hq.py` | system 消息 / `system_instruction`、历史与用户消息、`new_terms` 写回 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已读取 1.3 节与 5.7 小节并按页面合同编写 |
| 配置键与三类默认值 | 完成 | 静态核对 `config.py`、`config_models.py`、`config-example.json` |
| 加载、组合与占位符 | 完成 | 静态核对 `prompt_loader.py`、`common.py`、`manga_translator.py` |
| OpenAI/Gemini 系统指令路径 | 完成 | 静态核对 `openai.py`、`openai_hq.py`、`gemini.py`、`gemini_hq.py` |
| `en_US` / `zh_CN` 实际 locale | 完成 | 页面表格逐项记录 key、English、简体中文实际值 |
| 镜像与源码检查 | 完成 | `node scripts/verify-route-mirror.mjs .`、`node scripts/verify-source-evidence.mjs .` 通过 |
| 脱敏运行验证 | 待后续 | 未读取真实 `.env`、用户 `config.json`、API key/token、用户名、用户图片或私有提示词 |
| VitePress 构建 | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` |