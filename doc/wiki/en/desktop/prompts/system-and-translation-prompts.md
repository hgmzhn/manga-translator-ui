---
title: System and Translation Prompts
description: Explains system and translation prompt files, config keys, loading and composition order, placeholder replacement, and how prompts enter the OpenAI/Gemini system instruction
pageId: desktop.prompts.system-and-translation-prompts
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# System and Translation Prompts

Translation requests are driven by system prompts and translation prompts together: the system prompt files control “how to translate” and “what to output,” while translation prompts supply custom rules, glossary extraction, and AI line-breaking requirements. Use this page when you need to know when a prompt file is read, in what order it is concatenated, where the `target_lang` placeholder is replaced, and how the result enters an OpenAI/Gemini request.

This page does not cover prompt-file listing, applying, or CRUD (see [Prompt list, apply, and preview](./list-apply-and-preview.md)), nor the structured editor (see [Structured prompt editor and format](./structured-editor-and-format.md)); AI OCR, AI colorizer, and AI renderer prompts are covered in [AI OCR prompt](./ai-ocr-prompt.md), [AI colorizer prompt](./ai-colorizer-prompt.md), and [AI renderer prompt](./ai-renderer-prompt.md). How history pages become messages is covered in [Context and Prompts](../translator/context-and-prompts.md).

## Feature boundary {#feature-boundary}

- Fixed system prompt files: `dict/system_prompt_hq.yaml` (base system prompt) and `dict/system_prompt_hq_format.yaml` (output format). They are loaded from `dict/` at runtime by filename stem, are not part of the user prompt list, and have no dedicated desktop editor.
- Translation prompts: the custom HQ prompt pointed to by `translator.high_quality_prompt_path` (user `.yaml`/`.yml`/`.json` files under `dict/`), `dict/glossary_extraction_prompt.yaml` (glossary-extraction rules), and `dict/system_prompt_line_break.yaml` (AI line-breaking prompt).
- Related config keys: `translator.high_quality_prompt_path`, `translator.extract_glossary`, `render.disable_auto_wrap`; full parameter documentation is in [Translation settings](../settings/translation.md) and [Typesetting and rendering](../settings/typesetting-and-rendering.md).
- This page only explains how prompts are loaded, combined, and injected into the OpenAI/Gemini system instruction; it does not cover translator selection, API credentials, or candidate-slot rotation (see [Translator selection](../translator/selection-and-languages.md) and [API management](../api-management/slots-and-rotation.md)).
- No real API key, private prompt text, or local absolute path is written on this page. Prompt content is user data; remove it from logs, request exports, and debug directories before sharing.

## UI operations {#ui-operations}

### Select and apply a translation prompt in Prompt Management {#apply-translation-prompt}

Open “Prompt Management” (`Prompt Management`). The “Prompt List” (`Prompt List`) shows only user prompt files under `dict/` and excludes system prompt stems (`system_prompt_hq`, `system_prompt_hq_format`, `system_prompt_line_break`, `glossary_extraction_prompt`, `ai_ocr_prompt`, `ai_colorizer_prompt`, `ai_renderer_prompt`). Select a file and click “Apply Selected Prompt” (`Apply Selected Prompt`); the app writes `dict/<filename>` to `translator.high_quality_prompt_path` and persists it, and the status label shows “Current prompt: {filename}”.

Full list, preview, and editing operations are in [Prompt list, apply, and preview](./list-apply-and-preview.md); structured editing and save validation are in [Structured prompt editor and format](./structured-editor-and-format.md).

### Enable the related toggles in Settings {#settings-toggles}

1. Open “Settings” (`Settings`) → “Translation” (`Translation`) and enable “Auto Extract Glossary” (`Auto Extract Glossary`). This writes `translator.extract_glossary`; only when a parseable custom prompt also exists does the request append the glossary-extraction rules and the `new_terms` output format.
2. Open “Settings” → “Typesetting” (`Typesetting`) and enable “AI Line Breaking” (`AI Line Breaking`). This writes `render.disable_auto_wrap`; when enabled, the translation request loads `dict/system_prompt_line_break.yaml` and attaches `original_region_count` to each region in the user input JSON.
3. The on-screen name of `translator.high_quality_prompt_path` is “Custom Prompt” (`Custom Prompt`). Its dynamic-settings control is implemented in `dynamic_settings.py` (it rescans `dict/` and excludes system prompts when the dropdown opens); the actual entry point for setting this key is “Apply Selected Prompt” in Prompt Management.

| UI call key | English actual value | Simplified Chinese actual value |
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

## Config keys {#config-keys}

| Config key | Stored value / control | Defaults (core / Qt / release example) | Loading trigger and consumer |
| --- | --- | --- | --- |
| `translator.high_quality_prompt_path` | Prompt-file path string, e.g. `dict/<filename>` | `None` / `dict/prompt_example.yaml` / `dict/prompt_example.yaml` | Loaded into `ctx.custom_prompt_json` during translation-batch preparation; consumed only by OpenAI/Gemini and their HQ variants |
| `translator.extract_glossary` | Toggle `true` / `false` | `false` / `false` / `false` | Only when the custom prompt JSON is valid: appends glossary-extraction rules and the extended `new_terms` output format, and writes the response `new_terms` back to the prompt file |
| `render.disable_auto_wrap` | Toggle `true` / `false` | `false` / `true` / `false` | When enabled, loads `system_prompt_line_break.yaml` and attaches `original_region_count` to the user JSON; also affects renderer auto-wrapping |

Do not collapse the three defaults into a single value; Qt's `disable_auto_wrap` default differs from core/release, and the actual Settings value is authoritative.

## Runtime behavior {#runtime-behavior}

### When files are loaded {#loading-timing}

Before a translation batch starts, `_load_and_prepare_prompts()` prepares the prompts once:

- If `translator.high_quality_prompt_path` is non-empty, the path is normalized (`normalize_server_resource_path`), relative paths are joined with `BASE_PATH`, and `load_custom_prompt()` parses the file. If the exact path does not exist, it retries with the extension replaced in the order `.yaml` → `.yml` → `.json`; parse failures only log a warning and do not abort translation.
- If `render.disable_auto_wrap` is true, `load_line_break_prompt()` loads `system_prompt_line_break` from `dict/` into `ctx.line_break_prompt_json`; a missing file is also only logged.

The base system prompt, output format, and glossary-extraction prompt are not preloaded: on every request construction (including retries), `_build_system_prompt()` reads `system_prompt_hq` and `system_prompt_hq_format` from `dict/` by stem, and glossary mode additionally reads `glossary_extraction_prompt`. The loader prefers `.yaml`, then `.yml`, then `.json`.

### Composition order {#composition-order}

`_build_system_prompt()` concatenates the prompts into a single text block in a fixed order: retry hint (retries only) → AI line-break prompt (optional) → custom HQ prompt (optional) → base system prompt → output format. The custom prompt is recursively flattened into a text block by `_flatten_prompt_data()`; when glossary extraction is enabled and the custom prompt is valid, the glossary-extraction rules and the extended `new_terms` output format are appended after the base prompt, with sections separated by `\n\n---\n\n`.

```mermaid
flowchart LR
    R["Retry hint (retries only)"] --> L["AI line-break prompt (optional)"]
    L --> C["Custom HQ prompt (optional)"]
    C --> S["system_prompt_hq or code fallback"]
    S --> G{"extract_glossary and\ncustom prompt valid?"}
    G -->|no| O["Standard translations output format"]
    G -->|yes| T["Glossary rules + new_terms output format"]
    O --> API["OpenAI system message\nor Gemini system_instruction"]
    T --> API
    API --> U["Current-page JSON user prompt"]
```

If the base system prompt is missing or empty, the in-code fallback (`_HQ_FALLBACK_PROMPT`) is used; a missing output-format prompt logs a warning but the request still goes out with weaker format constraints.

### Placeholder replacement {#placeholders}

Placeholders in prompt files are literal triple-brace markers (for example <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code>), not Python string-format syntax. At each request construction, the runtime replaces the marker with the full target-language name: `VALID_LANGUAGES` maps language codes to full names (such as `CHS` → `Chinese (Simplified)`, `JPN` → `Japanese`), and unknown codes are kept as-is. Replacement happens only in the in-memory request text; the `dict/` files are never rewritten by it.

| Placeholder | Files | Replaced when | Replaced with |
| --- | --- | --- | --- |
| <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code> | `system_prompt_hq`, `system_prompt_hq_format`, custom prompts, `glossary_extraction_prompt` | Every request construction | Full target-language name |
| <code>&#123;&#123;&#123;optional_new_terms_rule&#125;&#125;&#125;</code> | `system_prompt_hq_format` | Only when `extract_glossary=True` | Rule text requiring a `new_terms` key; empty in normal mode |
| <code>&#123;&#123;&#123;optional_new_terms_example_suffix&#125;&#125;&#125;</code> | `system_prompt_hq_format` | Only when `extract_glossary=True` | The `new_terms` section in the output JSON example; empty in normal mode |
| <code>&#123;&#123;&#123;optional_new_terms_final_instruction&#125;&#125;&#125;</code> | `system_prompt_hq_format` | Only when `extract_glossary=True` | The closing instruction to return `"new_terms": []` when none found; empty in normal mode |

### Path into the OpenAI/Gemini system instruction {#system-instruction-path}

The combined system prompt is injected as a single unit; it is never split into multiple system messages per file:

```mermaid
flowchart TD
    B["Single text produced by _build_system_prompt"] --> O{"Translator implementation"}
    O -->|OpenAI| O1["messages[0] = role=system\ncontent = system prompt"]
    O1 --> O2["History user/assistant messages"]
    O2 --> O3["Current-page role=user (images in HQ)"]
    O -->|Gemini| G1["generation_config.system_instruction\n= system prompt"]
    G1 --> G2["contents = history user/model parts"]
    G2 --> G3["Current-page user parts"]
```

- OpenAI (`openai.py`, `openai_hq.py`): the system prompt is placed in `messages[0]` (`role=system`), followed by history `user`/`assistant` messages from `_build_openai_context_messages()`, then the current-page `user` request; the HQ user message includes image content.
- Gemini (`gemini.py`, `gemini_hq.py`): the system prompt is assigned to `generation_config.system_instruction`, and `contents` first carries history `user`/`model` parts from `_build_gemini_context_messages()`, then the current-page `user` parts.
- When glossary mode is enabled and the response contains `new_terms`, both OpenAI and Gemini call `merge_glossary_to_file()` to merge the new terms back into the `glossary` field of the custom prompt file (YAML or JSON depending on the extension).
- Streaming and non-streaming transports use the same system instruction and message construction; with `disable_auto_wrap` enabled, each region in the current-page user JSON carries `original_region_count` so final rendering can validate the `[BR]` marker count.

## Dependencies and conflicts {#dependencies-and-conflicts}

- Enabling “Auto Extract Glossary” alone has no effect: the code requires `bool(custom_prompt_json) and extract_glossary` to both be true, i.e. a parseable custom prompt must exist and the toggle must be on.
- A missing base system prompt falls back to built-in code text; a missing format or glossary prompt only weakens constraints without crashing; an unparseable custom prompt is skipped and the base prompt is still used.
- Glossary write-back modifies the custom prompt file under `dict/`; to keep the runtime from editing files, turn off “Auto Extract Glossary” or use a read-only copy.
- System prompt files are excluded from the user prompt list and have no desktop editor; manual edits must keep the YAML/JSON root as an object.
- `render.disable_auto_wrap` affects both typesetting line wrapping and translation requests (line-break prompt + `original_region_count`); it is not a pure rendering toggle, and Qt's default `true` differs from the core/release default `false`.
- Prompt text may contain business content and goes verbatim into requests and logs; remove prompt bodies, history text, paths, and credentials before sharing.

## Related files and formats {#files-and-formats}

| File/format | Role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `dict/system_prompt_hq.yaml` | Base HQ system prompt (key `system_prompt`) | Code fallback when missing/empty; keep YAML parseable |
| `dict/system_prompt_hq_format.yaml` | Output-format prompt (key `system_prompt_hq_format`, four placeholders) | Missing weakens output constraints; placeholders replaced as in the table above |
| `dict/glossary_extraction_prompt.yaml` | Glossary-extraction rules (key `glossary_extraction_prompt`) | Participates only in glossary mode; replaces <code>&#123;&#123;&#123;target_lang&#125;&#125;&#125;</code> |
| `dict/system_prompt_line_break.yaml` | AI line-break prompt (key `line_break_prompt`) | Triggered by `render.disable_auto_wrap` |
| `dict/prompt_example.yaml` | Default custom HQ prompt example | Loaded through `translator.high_quality_prompt_path`; structure only, never private bodies |
| `.yaml` / `.yml` / `.json` | Prompt formats supported by the loader | Root must be an object; same stem prefers `.yaml` |
| `config/config-example.json`, `config/config.json` | Release defaults and user-settings persistence | Never read or display a real user file |

## Mermaid data-flow limits {#diagram-limits}

The composition and injection paths in the diagrams are source-confirmed data transformations; they do not claim that every run loads every file or makes a network request. `disable_auto_wrap=false`, an unset prompt path, missing or unparseable files, non-HQ translators, and a disabled glossary toggle all take their documented bypasses. No runtime screenshot or private task artifact has been fabricated.

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Config definitions | `manga_translator/config.py`, `desktop_qt_ui/core/config_models.py`, `config/config-example.json` | The three config keys and the core/Qt/release default sets |
| Prompt loading | `manga_translator/translators/prompt_loader.py` | Stem resolution order, YAML/JSON parsing, placeholder replacement, system-file exclusion |
| Composition and request building | `manga_translator/translators/common.py` | `_flatten_prompt_data`, `_build_system_prompt`, fallback, `merge_glossary_to_file` |
| Batch preparation | `manga_translator/manga_translator.py` | `_load_and_prepare_prompts` timing and path resolution |
| UI/i18n | `desktop_qt_ui/ui/main_page/pages/prompt_page.py`, `desktop_qt_ui/app_logic.py`, `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Apply-selected-prompt, display-name mapping, actual bilingual copy |
| Final consumers | `manga_translator/translators/openai.py`, `openai_hq.py`, `gemini.py`, `gemini_hq.py` | System message / `system_instruction`, history and user messages, `new_terms` write-back |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read sections 1.3 and 5.7 and followed the page contract |
| Config keys and three default sets | Complete | Statically checked `config.py`, `config_models.py`, `config-example.json` |
| Loading, composition, and placeholders | Complete | Statically checked `prompt_loader.py`, `common.py`, `manga_translator.py` |
| OpenAI/Gemini system-instruction path | Complete | Statically checked `openai.py`, `openai_hq.py`, `gemini.py`, `gemini_hq.py` |
| `en_US` / `zh_CN` actual locales | Complete | The table records key, actual English, and actual Simplified Chinese values |
| Route mirror and source evidence | Complete | `node scripts/verify-route-mirror.mjs .`, `node scripts/verify-source-evidence.mjs .` passed |
| Sanitized runtime verification | Deferred | No real `.env`, user `config.json`, API key/token, username, user image, or private prompt was read |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` before merge |