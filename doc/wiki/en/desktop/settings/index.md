---
title: Settings and Configuration Lifecycle
description: Explain the seven desktop settings tabs, parameter editing actions, configuration precedence, and runtime boundaries
pageId: desktop.settings.index
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Settings and Configuration Lifecycle

The Settings page adjusts the desktop translation pipeline and passes the edited values to the runtime configuration model. It owns tabs, parameter controls, import/export, and automatic saving; algorithm details belong to [General and app](./general-and-app.md), [CLI, batch, and output](./cli-batch-and-output.md), [Detection](./detection.md), [OCR, filtering, and merging](./ocr-filter-and-merge.md), [Translation](./translation.md), [Mask and inpainting](./mask-and-inpainting.md), [Typesetting and rendering](./typesetting-and-rendering.md), [Upscaling and colorization](./upscale-and-colorization.md), and [Mode-specific parameters](./mode-specific.md). It does not own API credential-slot rotation, prompt lists, editor project data, or the detailed execution of the nine workflows.

## Feature boundary {#feature-boundary}

- The page reads seven UI groups from `settings_tab_layout.json`: General, OCR, Detection, Translation, Inpainting, Typesetting, and Mode Specific. `Advanced` and the other dividers are headings inside a tab, not separate tabs.
- The current layout contains 110 entries, of which 109 are visible parameters; one entry is not rendered by the current dynamic settings code. Internal state, workflow-controlled flags, and deprecated fields are not duplicated as ordinary rows.
- Configuration has three distinct sources: the Qt `AppSettings` model, the core `Config` processing model, and the release template `config/config-example.json`. They must not be collapsed into one default.
- This page explains how the Settings page changes and saves configuration. Detection, OCR, translation, inpainting, typesetting, upscaling, and colorization consumers remain on their respective pages.

## UI operations {#ui-operations}

Open Settings in the desktop application. The header shows “Settings” and “Adjust translation pipeline parameters. Changes are saved automatically.”, with “Export Config” and “Import Config” on the right. The left side contains segmented tabs, the center contains scrollable parameter rows, and the right side contains “Parameter Description”. Selecting a row or its control displays the configuration key and description.

### Tabs and parameter ownership

| Layout `title` / UI call key | English actual value | Simplified Chinese actual value | Main contents |
| --- | --- | --- | --- |
| `General` | General | 通用 | Language, theme, logging/errors, GPU/ONNX, format, overwrite, retries, batches, output, and model unloading |
| `OCR` | OCR | 识别 | Primary/secondary OCR, hybrid OCR, AI OCR, filtering, bubble constraints, and merge thresholds |
| `Detection` | Detection | 检测 | Detector, YOLO, SFX, detection size, and detection thresholds |
| `Translation` | Translation | 翻译 | Translator, target/kept language, streaming, glossary, RPM, context, and post-processing conversion |
| `Inpainting` | Inpainting | 修复 | Inpainter, mask dilation, bubble intersection, solid bubbles, per-block processing, size, and precision |
| `Typesetting` | Typesetting | 排版 | Renderer, font, line breaking, direction, color, spacing, layout, and AI renderer concurrency |
| `Mode Specific` | Mode Specific | 模式相关 | Replace-translation alignment, upscale ratio/tile, and colorizer model/size/denoising |
| `Advanced` | Advanced | 高级 | An advanced divider inside OCR, Detection, or Inpainting; not a separate tab |

Steps:

1. Select a tab. The dynamic layout rebuilds rows in the order defined by `settings_tab_layout.json`.
2. Change a toggle, input, or combo box. Display values are mapped back to stored values through `AppLogic.get_display_mapping()`; fonts and prompts are runtime lists, not fixed enums.
3. Clearing an optional numeric input writes `null`, returning the consumer to its default/automatic semantics. Invalid numeric input also falls back to `null` and remains subject to model validation.
4. Select a row to inspect its right-hand description. The fixed AI OCR, AI renderer, and AI colorizer prompt rows are file-edit actions/resource paths; “Edit” opens the relevant editor instead of placing prompt text in an ordinary setting field.
5. Use “Edit” for the custom API parameter file, and the edit action beside the filter toggle for the filter-list editor. The font row provides “Open Directory”.
6. Use “Export Config” to choose an external JSON file. Use “Import Config” to load JSON; the service performs a per-key deep merge and Pydantic validation. Import can rebuild the whole page and refresh the description, API, and prompt controls.

Changing `app.ui_language` or the application language reloads tab labels, field labels, descriptions, and displayed combo values without changing stored values. There is no separate Apply button: normal edits update memory immediately and are then coalesced to disk by the configuration service.

## Option matrix {#option-matrix}

| UI call key / stored value | English | 简体中文 |
| --- | --- | --- |
| `Settings Page Title` | Settings | 参数设置 |
| `Settings Page Subtitle` | Adjust translation pipeline parameters. Changes are saved automatically. | 调整翻译流程的各项参数。修改后将自动保存。 |
| `Export Config` | Export Config | 导出配置 |
| `Import Config` | Import Config | 导入配置 |
| `Settings Desc Header` | Parameter Description | 参数说明 |
| `Settings Desc Placeholder` | Click any setting on the left to view details | 点击左侧任意设置项查看详细说明 |
| `General` | General | 通用 |
| `OCR` | OCR | 文字识别 |
| `Detection` | Detection | 检测 |
| `Translation` | Translation | 翻译 |
| `Inpainting` | Inpainting | 修复 |
| `Typesetting` | Typesetting | 排版 |
| `Mode Specific` | Mode Specific | 模式相关 |
| `Advanced` | Advanced | 高级 |
| `Theme:` | Theme: | 主题： |
| `Language:` | Language: | 语言： |
| `Edit` | Edit | 编辑 |
| `Open Directory` | Open Directory | 打开目录 |
| `Preset:` | Preset: | 预设： |
| `app.theme=light` | Light | Light |
| `app.theme=dark` | Dark | Dark |
| `app.theme=system` | Follow System | Follow System |
| `cli.format=Not Specified` | Not Specified | 不指定 |
| `upscale_ratio_not_use` | Not Use | 不使用 |
| `alignment_auto` | Auto | 自动 |
| `direction_vertical` | Vertical | 竖排 |
| `layout_mode_smart_scaling` | Smart Scaling | 智能缩放 |

Fixed language names for `app.ui_language` come from `LocaleInfo.name`; some theme names are literals from `theme_registry.py`, so this page does not invent missing i18n keys. The complete value/UI matrix for parameters is in [Options and i18n matrix](../../reference/options-i18n-matrix.md).

## Runtime behavior {#runtime-behavior}

```mermaid
flowchart LR
    A["UI control or imported config"] --> B["AppSettings / ConfigService"]
    B --> C["In-memory config"]
    B --> D["Atomic config.json write"]
    C --> E["Core Config"]
    E --> F["Workflow and stage consumers"]
    G["Explicit CLI arguments"] --> E
    H["Release config defaults"] --> B
    I["Code fallbacks"] --> E
```

`ConfigService` creates `AppSettings()`, loads the release/default JSON, and then overlays the user `config.json`; precedence is user config > `config-example.json` > Qt model defaults. Core `Config()` still defines core fields and defaults. Explicit CLI arguments can override values as they enter core configuration; Web runtime overrides are a separate entry point.

A controller updates `AppSettings`; `update_config()` and imported per-key merges validate through Pydantic. API values update memory and `os.environ` immediately. Normal JSON and `.env` writes use a 250 ms debounce, a single writer, a temporary file, and atomic `os.replace`. Explicit exports wait for the write; shutdown flushes pending snapshots.

- Selecting a translator, OCR, colorizer, or renderer implementation refreshes the corresponding API groups. This chooses a provider; it is not candidate-slot rotation.
- Selecting `upscale.upscaler` repopulates the ratio control: ordinary models store integer 2/3/4, Real-CUGAN also stores `realcugan_model`, and MangaJaNai stores `x2`, `x4`, or `DAT2 x4`; “Not Use” stores `null`.
- `cli.batch_size` is the stage batch size; `cli.batch_concurrent` is image-level pipeline concurrency. They are different controls, and special workflows may override CLI flags.
- Fixed prompt editors write their respective YAML/compatible files. AI OCR, AI renderer, and AI colorizer have separate prompt consumers.

## Dependencies and conflicts {#dependencies-and-conflicts}

- OpenAI/Gemini translation, AI OCR, AI colorization, or AI rendering requires the relevant environment variables and a reachable API base. Hybrid OCR with an AI secondary OCR also needs that secondary OCR credential. Real values do not belong in this page.
- GPU, ONNX GPU, Torch inpainting precision, and model choices depend on hardware, installed dependency groups, and VRAM. `disable_onnx_gpu` is not the same as `use_gpu=false`.
- Hybrid OCR, AI concurrency, RPM, retries, and batch concurrency increase recognition/network pressure and possibly API cost.
- `upscale_ratio` depends on `upscaler`; template alignment and paste-mask dilation are meaningful only in replace-translation mode.
- Imported unknown keys do not become controls. Invalid values fall back to defaults and are logged. Do not hand-edit the same JSON or `.env` while the application has pending writes.

## Related files and formats {#related-files-and-formats}

| File/format | Settings-page use and boundary | Note |
| --- | --- | --- |
| `config/config.json` | User settings as UTF-8 JSON, preferred over the default template | Invalid values fall back by field; do not copy private paths |
| `config/config-example.json` | Release/development default template | Not identical to Qt/Core defaults |
| `.env` | API Key, Base, Model, and other dotenv entries | Never publish values, screenshots, or credentials |
| `config/custom_api_params.json` | Extra API request parameters when `use_custom_api_params` is enabled | Does not carry credentials or rotation |
| `dict/ai_ocr_prompt.yaml`, `dict/ai_renderer_prompt.yaml`, `dict/ai_colorizer_prompt.yaml` | The three fixed prompt-editor actions | Consumed by separate AI modules |
| `config/filter_list.json` / `filter_list.txt` | Filter-list settings | Rules can skip OCR regions |
| `config/translation_template.json` | Workflow text-extension template | Parsed as a text template, not strict JSON configuration |
| `manga_translator_work/` | Translation JSON, TXT, masks/overlays, and editor data | May contain user content and absolute paths |

## Screenshot and diagram boundary {#visual-boundary}

The Mermaid diagram on this page describes configuration lifecycle and precedence; it is not a substitute for a runtime screenshot. No headed UI screenshot was generated during this source investigation. Future screenshots should cover all seven tabs, the description panel, combo options, file-edit actions, import/export, and preset refresh, with usernames, private absolute paths, keys, tokens, user images, and private prompts cropped or replaced. Debug JSON, `mask_raw`, PSD, and JSX must also be treated as user content.

## Source evidence {#source-evidence}

| Layer | File | Checked content |
| --- | --- | --- |
| Layout | `desktop_qt_ui/ui/main_page/settings_tab_layout.json` | Seven tabs, ordering, dividers; Phase 0 records the 110/109 baseline |
| Page shell | `desktop_qt_ui/ui/main_page/pages/settings_page.py` | Title, import/export, tabs, and description panel |
| Dynamic controls | `desktop_qt_ui/ui/main_page/dynamic_settings.py` | Control types, skipped fields, dynamic upscale choices, and prompt editors |
| i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Actual bilingual UI text |
| Configuration model | `desktop_qt_ui/core/config_models.py` | `AppSettings`, Qt defaults, and validation |
| Persistence | `desktop_qt_ui/services/config_service.py` | Precedence, per-key validation, debounce, atomic writes, and flush |
| Stage consumers | `manga_translator/config.py` and detection, OCR, translation, inpainting, rendering, upscaling, and colorization modules | Core defaults, CLI overrides, and final consumers |
| Research | `doc/wiki/research/default-sources.md`, `phase0-options-i18n-matrix.md`, `phase0-related-files-formats-debug-safety.md` | Default differences, options, formats, and sensitive-data boundaries |

## Verification {#verification}

| Check | Status | Note |
| --- | --- | --- |
| Page, layout, controls, and persistence source | Complete | Static source review completed |
| i18n key → English → Simplified Chinese | Complete | UI wording comes from both locale files; literals remain explicit |
| Defaults and precedence | Static complete | Core 120, Qt 131, Release 131; user config was not read |
| Headed UI, import/export, and write verification | Pending runtime | No screenshot fabricated |
| Mermaid, route mirror, and source-field checks | Pending site-wide acceptance | Anchors and evidence fields are present |
| VitePress build | Pending execution | `npm ci --prefix doc/wiki`; `npm run docs:build --prefix doc/wiki` |
| Sensitive-data review | Complete | No key, token, username, private path, user image, or private prompt included |
