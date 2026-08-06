---
title: Import Translation and Render
description: Import translations from a project JSON or original/translated text sidecars and render them onto images, skipping detection, OCR, and translation
pageId: workflows.import-translation-and-render
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Import Translation and Render

Use the Import Translation and Render workflow when translations already exist — for example, after manually translating the `imagename_original.txt` produced by Export Original Text, obtaining a translated sidecar from Export Translation, or saving a project JSON from the editor — and you only need to render those translations onto the images. The workflow loads text regions, the mask, and layout flags from the project JSON, optionally updating the JSON from a text sidecar first, then runs mask refinement, inpainting, and rendering, writes the result back to the project JSON, and saves the main output image. The normal path does not run colorization, upscaling, detection, OCR, text-line merging, or translation.

Import Translation and Render forms the template/JSON family together with [Export Original Text](./export-original.md), [Export Translation](./export-translation.md), and [Translate JSON Only](./translate-json-only.md). The overall boundaries of the nine workflows are in [Output Directory and Workflow](../desktop/translation/output-directory-and-workflow.md), with a summary table in [Workflow Matrix](../reference/workflow-matrix.md).

## Feature boundary

- Inputs: the main input images (the same file-discovery rules as normal translation), and a project JSON that must be found for each image; optional inputs are the original/translated text sidecars (TXT import) and an existing inpainted image.
- Stages executed: JSON/in-memory payload load → (reuse the refined mask when present, otherwise) mask refinement → inpainting → rendering → main-image saving and JSON write-back. When the JSON has no mask and `detector.import_yolo_labels` is enabled, detection runs additionally to generate a mask.
- Stages skipped: colorization, upscaling, detection, OCR, text-line merging, and translation (detection for a missing mask is the only exception).
- Output files: the main output image and the updated project JSON; an inpainted image is written when inpainting is rerun and `save_text=true`; a PSD is exported when `export_editable_psd` is enabled.
- Workflow field: combo index 4 writes `cli.load_text=true` at runtime; GUI switching keeps the eight workflow booleans mutually exclusive.

## UI operations

### Select the Import Translation and Render workflow

1. Prepare a project JSON for each image (`manga_translator_work/json/<stem>_translations.json`, with the legacy image-side location also accepted). For manual translation, first run Export Original Text and translate `imagename_original.txt` in `manga_translator_work/originals/`.
2. Open the translation page and choose “Import Translation and Render” (`Import Translation and Render`) in the “Translation Workflow Mode:” (`Translation Workflow Mode:`) combo box.
3. The page title becomes “Import Translation and Render” and the subtitle shows the hint: TXT files will be read from `manga_translator_work/originals/` or `translations/` and rendered (prioritizing `_original.txt`).
4. The start button becomes “Import Translation and Render” (`Import Translation and Render`); clicking it starts the backend task in this mode.

Selecting a mode only writes configuration and updates the UI texts; it does not start a task. Before starting, add the main input images (“Add Files...”, “Add Folder...”, or drag-and-drop) and make sure every image has a parsable project JSON; images without a JSON enter the error fallback branch (static source conclusion; the runtime prompt still needs verification).

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Translation Workflow Mode:` | Translation Workflow Mode: | 翻译流程模式： |
| `Import Translation and Render` | Import Translation and Render | 导入翻译并渲染 |
| `Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)` | Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt) | 提示：将从 manga_translator_work/originals/ 或 translations/ 目录读取 TXT 文件并渲染（优先使用 _original.txt） |
| `Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode` | Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode | 提示：导出原文后，可在 manga_translator_work/originals/ 目录手动翻译 图片名_original.txt 文件，然后使用「导入翻译并渲染」模式 |
| `label_load_text` | Import Translation | 导入翻译 |
| `label_save_text` | Editable Image | 图片可编辑 |
| `label_overwrite` | Overwrite Existing Files | 覆盖已存在文件 |
| `label_import_yolo_labels` | Import Fixed YOLO Boxes | 导入固定YOLO框 |
| `label_batch_concurrent` | Concurrent Batch Processing | 并发批量处理 |

The UI hints always read `_original.txt` / “TXT files”, but the actual sidecar extension comes from the template's `output_format` (default `json`); the hint text does not change with the template extension.

## Option matrix

The combo box has no separate `userData`; the index is the mode value. Runtime code maps index 4 to `cli.load_text=true`. The stored values of the related settings are listed below, with the three UI evidence columns and their actual effect on this workflow.

| Stored value | English | Simplified Chinese | Effect in this workflow |
| --- | --- | --- | --- |
| `load_text=true` | Import Translation and Render | 导入翻译并渲染 | Enters the import branch; skips colorization, upscaling, detection, OCR, merging, and translation |
| `overwrite=false` | Overwrite Existing Files | 覆盖已存在文件 | Skips images whose main output image already exists before starting |
| `save_text=true` | Editable Image | 图片可编辑 | Controls whether a rerun inpainted image is saved; the JSON write-back does not depend on it |
| `import_yolo_labels=true` | Import Fixed YOLO Boxes | 导入固定YOLO框 | Runs detection to generate a mask when the JSON has no mask |
| `batch_concurrent=true` | Concurrent Batch Processing | 并发批量处理 | This mode is forced to run non-concurrently |

## Runtime behavior

### Input discovery and TXT import

`translate_batch()` runs `_preprocess_load_text_mode()` before per-image processing. For each image it looks up the project JSON with `find_json_path()` (the new location `manga_translator_work/json/<stem>_translations.json` takes priority, falling back to the image-side `<stem>_translations.json`), then looks up the original and translated sidecars with `find_txt_files()`. The original sidecar (`originals/<stem>_original.<template-extension>`) takes priority; the translated sidecar (`translations/<stem>_translated.<template-extension>`) is used only when the original does not exist. Images without a JSON or without a TXT skip the import (the former errors in the per-image stage).

When a JSON and a TXT are found, `safe_update_large_json_from_text()` parses the TXT using the placeholder structure of `config/translation_template.json`, updates each region's `translation` field by exact original-text match followed by whitespace-normalized fuzzy match, and writes back atomically through a temporary file. A successful import forces `skip_font_scaling=false` so this render re-runs smart font scaling instead of reusing old font sizes. When the template file is missing, `_get_default_template_path()` auto-creates the built-in default template; when the TXT cannot be parsed (for example the template has no `<original>` placeholder), the import is skipped and only a debug log is written.

### Processing stages and outputs

```mermaid
flowchart LR
    Input["Main input image"] --> Pre["Step 0: TXT → JSON import<br/>original sidecar first, else translated sidecar"]
    Pre --> Find{"Find project JSON"}
    Find -->|"No JSON"| Error["Error fallback: output a copy of the original"]
    Find -->|"Found"| Load["Load regions / mask / layout flags"]
    Load --> Mask{"Mask in JSON?"}
    Mask -->|"Refined mask"| Use["Use ctx.mask directly"]
    Mask -->|"Raw mask"| Refine["Mask refinement"]
    Mask -->|"No mask"| Yolo{"import_yolo_labels?"}
    Yolo -->|"Yes"| Detect["Detection generates mask"]
    Yolo -->|"No"| Poly["Fill mask from region polygons"]
    Detect -. "failed or no mask" .-> Poly
    Use --> Inpaint["Inpainting"]
    Refine --> Inpaint
    Poly --> Inpaint
    Inpaint --> Render["Rendering"]
    Render --> Out["Main output image"]
    Render --> Back["Write back project JSON"]
    Input -. "normal path skips" .-> Skip["Colorize / Upscale / Detection / OCR / Merge / Translation"]
```

The Mermaid above shows the stages and branches confirmed in the source. Limitation notes: detection runs additionally only when the JSON has no mask and `import_yolo_labels=true`; an AI renderer (`renderer` set to OpenAI/Gemini) skips real inpainting and uses the work image as the render base; an existing inpainted image is reused only when the JSON carries a mask, otherwise inpainting reruns.

### Mask, inpainting, and rendering

- When loading, a JSON mask with `mask_is_refined=true` is used directly as `ctx.mask`; otherwise it becomes `ctx.mask_raw` and goes through mask refinement.
- With no mask in the JSON: if `import_yolo_labels` is enabled, detection is tried first to generate a mask (falling back on failure); otherwise a mask is filled from the region `lines` polygons.
- Inpainting order: AI renderer skip → editor in-memory payload provides an inpainted image → existing on-disk inpainted image (requires a mask in the JSON) → rerun inpainting. A rerun writes `manga_translator_work/inpainted/<stem>_inpainted.<original-extension>` when `save_text=true`.
- Rendering honors `skip_font_scaling` from the JSON (treated as `true` when absent; a TXT import writes `false`; a JSON written by Export Translation replays fixed font sizes) and `skip_text_replacements` for whether text-replacement rules are applied again.
- When loading, regions with an empty `translation` fall back to the original text, and a missing `target_lang` falls back to the configured target language.

### JSON write-back and editor export

After rendering, `_save_text_to_file()` writes the latest regions (including post-render fields such as `translation` and `font_size`) back to the project JSON: it preserves existing `paint_overlay`/`stamp_overlay` layers, records `last_export_dir`, saves the mask with `mask_is_refined`, and writes `skip_text_replacements=true` when the image was rendered. If any region failed to parse (`region_parse_failures > 0`), the write-back is skipped to protect the project file from losing regions.

The editor “export” channel does not go through the on-disk JSON: `export_service.py` injects an in-memory payload via `set_preloaded_load_text_payload()`, and the backend treats such a payload as an authorized final draft and only renders — skipping text replacements and JSON write-back, and using the editor-provided inpainted image directly. The editor UI and project saving are documented on the editor pages; this page only notes that the channel shares the same `load_text` branch as file-based import.

### Concurrency and mutual exclusion

- `batch_concurrent` is incompatible: both the desktop controller and `translate_batch()` treat this mode as incompatible and force non-concurrent processing; saving a concurrent configuration in the UI does not produce a concurrent pipeline.
- Manually stacking multiple workflow fields is not a supported combination. GUI switching keeps the eight booleans mutually exclusive; when syncing the combo from configuration, import translation has lower priority than replace translation, inpaint only, upscale only, and colorize only.
- This mode makes no translation service calls and does not run conditional colorization/upscaling from `colorizer.colorizer`/`upscale.upscale_ratio`; the main output is based on the original image.

## Dependencies and conflicts

- The project JSON is a hard prerequisite: when `find_json_path()` finds no JSON, the per-image stage reports “translation file not found or invalid” and the error fallback outputs a copy of the original image (static source conclusion; the user-visible prompt needs runtime verification).
- The TXT import depends on the template: a missing template auto-creates the default; an unparsable template or a TXT with no valid entries skips the import (debug log only) and translations are not updated.
- `cli.overwrite=false`: the GUI skips images whose main output image already exists before starting (it checks the result of `_calculate_output_path()`).
- `cli.save_text`: the JSON write-back in this mode does not depend on it, but a rerun inpainted image is saved only when `save_text=true`.
- `detector.import_yolo_labels`: it triggers detection-based mask generation only when the JSON has no mask; with a mask present the parameter has no effect on this workflow.
- `render.enable_template_alignment` is a Replace Translation-only setting and is unrelated to this workflow.
- Inpainting and rendering still consume model, VRAM, and API costs according to the selected models; this page does not repeat those parameter descriptions.
- The main output directory, `save_to_source_dir`, and `cli.format` determine the main image location and extension; the JSON, inpainted image, and sidecars always follow the per-image work-directory rules.

## Related files and formats

| File/format | Actual role on this page | Notes |
| --- | --- | --- |
| `manga_translator_work/json/<stem>_translations.json` | Primary project JSON input and write-back target | New location takes priority; falls back to the legacy image-side location; accepts legacy list and new `regions` dict structures |
| `manga_translator_work/originals/<stem>_original.<template-extension>` | TXT import source (priority) | Extension comes from the template's `output_format`, default `json`; the UI hint always reads `_original.txt` |
| `manga_translator_work/translations/<stem>_translated.<template-extension>` | TXT import source (fallback) | Used only when the original sidecar does not exist |
| `config/translation_template.json` | Determines the TXT import placeholder structure and sidecar extension | The first `output_format:` line is the extension; missing/invalid falls back to `json` |
| `manga_translator_work/inpainted/<stem>_inpainted.<original-extension>` | Reused when present; written when inpainting reruns | Reuse requires a mask in the JSON; writing requires `save_text=true` |
| Legacy image-side `<stem>_translations.txt` | Deprecated old TXT format | The loader returns `None`; no longer supported |
| Main output image | Final rendered image | Determined by `_calculate_output_path()` using the output directory, relative hierarchy, `save_to_source_dir`, and `cli.format` |

No real user configuration, keys, tokens, usernames, private absolute paths, user images, or task artifacts are shown on this page. `mask_raw` in the JSON is base64 PNG and is not sanitization; debug directories must not be uploaded as-is.

## Source evidence

| Layer | File | What was checked |
| --- | --- | --- |
| Workflow selection and writes | `desktop_qt_ui/ui/main_page/runtime.py:183-215` | Index 4 → `load_text=true`, eight-field mutual exclusion, and config saving |
| Title, hint, and start button | `desktop_qt_ui/ui/main_page/runtime.py:22-47,219-238` | “Import Translation and Render” title, hint call keys, and button text |
| i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Actual bilingual values for `Import Translation and Render`, the two hints, and `label_*` |
| Controller | `desktop_qt_ui/app_logic.py:3228,3241-3272` | Workflow hint, pre-start main-image check, and special-mode concurrency disabling |
| TXT import | `desktop_qt_ui/services/workflow_service.py:811` | `safe_update_large_json_from_text`: template parsing, matching, `skip_font_scaling=false`, atomic write-back |
| Preprocessing | `manga_translator/manga_translator.py:1145` | `_preprocess_load_text_mode`: import only when JSON exists, original first, auto-created template |
| Loading | `manga_translator/manga_translator.py:1325` | `_load_text_and_regions_from_file`: JSON structure, mask and flag parsing, failure counter |
| Dispatch and processing | `manga_translator/manga_translator.py:3429,3605-3990` | Step-0 preprocessing, load_text branch, mask/inpaint/render/write-back |
| Editor export | `desktop_qt_ui/services/export_service.py:855` | `set_preloaded_load_text_payload` in-memory payload channel |
| Paths | `manga_translator/utils/path_manager.py:151,178,204,392,442` | JSON/original/translated/inpainted paths and lookup fallbacks |
| Template | `manga_translator/utils/translation_template.py` | `output_format` parsing, default value, and safety validation |

## Verification

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract; the three contract files were not modified |
| Source and research material | Complete | Cross-checked `workflow-matrix-source-evidence.md` and the UI, i18n, controller, workflow-service, and core sources |
| Three-column i18n evidence | Complete | The workflow option, the two hints, the button, and the related settings record the call key, English, and Simplified Chinese actual values |
| Route/page mirror | Pending | Run route mirror and source-evidence checks after completing the pages |
| TXT import and write-back | Pending | Original-over-translated priority, template parse-failure feedback, and `skip_font_scaling` writes need sanitized runtime verification |
| Missing-mask/YOLO fallback and inpainted reuse | Pending | Detection fallback with `import_yolo_labels` and no mask, and the existing-inpainted reuse condition need runtime verification |
| Production build | Pending | Run `npm run docs:build --prefix doc/wiki` if needed |

- [ ] [In progress] Runtime confirmation remains: the error prompt and file retention when JSON is missing, user-visible feedback for failed TXT imports, and the actual output of inpainted reuse and YOLO mask generation.