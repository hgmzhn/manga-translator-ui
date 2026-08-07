---
title: General and Application Settings
description: Configure desktop language, theme, general processing switches, editor preferences, and application state
pageId: desktop.settings.general-and-app
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# General and Application Settings

This guide covers the settings page’s “General” group and the application state it carries. It documents language, theme, the custom API-parameter file switch, the filter list, global mask parameters, model unloading, and editor preferences; specialized detection, OCR, translation, inpainting, typesetting, upscaling, and colorization parameters belong to their respective pages.

## Change it in the desktop app {#ui-operations}

Open Settings and select “General”. Dynamic rows are generated from storage keys in the layout file; clicking a row shows its description in the right-hand description panel. Changing a toggle, number, or combo box updates the configuration immediately, after which the configuration service coalesces the disk write. Leaving a numeric field empty writes `null`, so the relevant consumer applies its default semantics.

The “Use Custom API Params” row includes an “Edit” button that opens `config/custom_api_params.json`; this is a file-edit action, not JSON embedded in `AppSettings`. The filter-list row includes an “Edit Filter List” button for the filter-word file. The font-directory button is in Typesetting, not this page.

### Theme, language, and presets

- “Theme” options are generated from `THEME_OPTIONS` in `theme_registry.py`; selection emits a theme-change signal and immediately refreshes Qt styling.
- “Language” options come from `I18nManager.get_available_locales()`, not from guessing keys in `en_US.json` / `zh_CN.json`. Selection refreshes desktop text, Qt built-in widget translations, and navigation, then saves `app.ui_language`.
- The API preset toolbar displays the current API preset. Switching a preset refreshes API forms and credential slots; it does not change the translator or detector implementation. The current preset name is stored in `app.current_preset` and is application state rather than a normal dynamic settings row.

## Parameters

> For the mapping of UI names, storage keys, and default values of the parameters on this page, see the [Settings Parameter Index](../../reference/settings-index.md).

### Language {#app-ui-language}

The “Language” combo box is on Settings → General and decides the desktop interface language; selecting it refreshes the interface text immediately.

- `auto`: detects the system language automatically (falls back to Simplified Chinese when not registered).
- `zh_CN`: Simplified Chinese.
- `zh_TW`: Traditional Chinese.
- `en_US`: English.
- `ja_JP`: Japanese.
- `ko_KR`: Korean.
- `es_ES`: Spanish.

It changes the interface language only, not the translation target language. Default: `auto`.

### Theme {#app-theme}

The “Theme” combo box is on Settings → General and selects the interface color scheme; selecting it refreshes the styling immediately.

- `light`: light theme.
- `dark`: dark theme.
- `gray`: gray theme.
- `ocean`: ocean theme.
- `forest`: forest theme.
- `sunset`: sunset theme.
- `rose`: rose theme.
- `system`: follows the OS appearance and selects a registered theme.

Default: `light`.

### Use Custom API Params {#custom-api-params}

The “Use Custom API Params” toggle is on Settings → General. When enabled, `config/custom_api_params.json` is read; presets are matched by the current request’s model name and `common` is merged with the corresponding API module section to attach extra request parameters for translation, AI OCR, AI rendering, and AI colorization. The adjacent `Edit` button opens that JSON file. It does not store keys, bases, or models and does not perform API candidate rotation.

Default: `true`.

See [Custom Request Parameters](../api-management/custom-request-parameters.md) for details.

### Unload Models After Translation {#unload-models}

The “Unload Models After Translation” toggle is on Settings → General. When enabled, the desktop unloads models after each image/task completes, releasing memory and VRAM; the next task reloads models on demand. It helps under low VRAM but adds loading time to the next task.

```mermaid
flowchart LR
    T["Task complete"] --> Q{"Unload models?"}
    Q -->|no| K["Keep cache for reuse by next task"]
    Q -->|yes| U["Unload models and release memory/VRAM"]
    U --> L["Next task reloads models"]
```

Default: `false`.

### Enable Filter List {#filter-text-enabled}

The “Enable Filter List” toggle is on Settings → General and has an “Edit Filter List” button. When enabled, an OCR result that matches a filter word (exact/contains rules, case-insensitive) skips that text region; the filter-word file is maintained by the filter-list editor. Default: `false`.

### Kernel Size {#kernel-size}

“Kernel Size” is an integer input that controls the convolution kernel used for mask cleanup before inpainting; an excessive value can damage line art. Default: `3`.

### Mask Dilation Offset {#mask-dilation-offset}

“Mask Dilation Offset” is an integer input that controls how many pixels the text mask expands to cover residual source pixels; `0` means no extra expansion, and bubble constraints are further limited by dedicated OCR/Inpainting options. Default: `50`.

## Runtime behavior and configuration lifecycle {#runtime}

The settings page builds dynamic controls from `ConfigService.get_config().model_dump()`. Each control change is sent through `MainAppLogic.update_single_config()` to the Pydantic `AppSettings`; translator and target-language changes additionally refresh the translation service, while `render.*` emits an editor-refresh signal. Language and theme use dedicated signals: language reloads locale/Qt translators, and theme reapplies styling.

At startup, the priority is code `AppSettings` defaults < the release template such as `config/config-example.json` < user `config/config.json`. The user configuration is synchronized with added/removed template keys. Ordinary settings are persisted to `config/config.json`; the service coalesces writes with a 250 ms debounce, while explicit save/switch operations flush pending writes. Explicit CLI arguments override `cli.*` only at the CLI entry point; an omitted argument is not an override.

General’s GPU, ONNX, batch, output, and retry settings ultimately enter core `Config.cli`; the CLI/batch page owns their complete workflow and concurrency explanation, while this page records their General controls and boundaries.

## Interactions and caveats {#dependencies}

- `cli.use_gpu` requires matching CUDA/hardware dependencies; `cli.disable_onnx_gpu` can disable only the ONNX GPU backend, so the two switches are not mutually exclusive.
- `cli.batch_concurrent` is constrained by special inputs/workflows and resource conditions; it does not guarantee simultaneous execution of every model or API request.
- `cli.export_editable_psd` requires Photoshop; with `cli.psd_script_only`, only the script is produced and a PSD must not be claimed.
- `use_custom_api_params` requires parseable JSON and a matching model configuration; it is separate from `.env` credentials, API bases, and API slot rotation.
- Excessive `mask_dilation_offset` or `kernel_size` can consume line art and balloon borders; bubble-mask limits require the OCR/Inpainting settings.
- Unloading models reduces resident VRAM but costs the next task’s load time; it does not guarantee that third-party processes immediately return all memory.
