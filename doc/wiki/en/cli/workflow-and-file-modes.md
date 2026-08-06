---
title: Workflows and File Modes
description: Configure the nine CLI workflow fields and understand how the main output image and manga_translator_work sidecar files are read and written
pageId: cli.workflow-and-file-modes
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Workflows and File Modes

The CLI `local` mode has no “workflow” command-line switch; the nine workflows are expressed as boolean fields in the `cli` section of the configuration file, the same fields written by the desktop “Translation Workflow Mode:” dropdown. This page explains how those fields reach `MangaTranslator`, which pipeline stages and output files each field changes, and how the main output image and the `manga_translator_work` sidecar files (project JSON, original/translation template exports, inpainted images, editor base images, and replace-translation pair images) are read and written for each image.

This page does not repeat `local` input collection or output-directory resolution (see [Local input and output](./local-input-output.md)), does not explain `--config` or explicit parameter overrides (see [Configuration overrides](./configuration-overrides.md)), and does not expand the full UI walkthrough of each workflow (see [Output directory and workflow](../desktop/translation/output-directory-and-workflow.md) and the `workflows/` pages; the summary table is [Workflow matrix](../reference/workflow-matrix.md)). The structure of the four top-level subcommands is in [Command structure](./command-structure.md).

## Feature boundary {#feature-boundary}

- The official `local` subcommand has no workflow switch among its options; workflow fields come from the `cli` section of the configuration file (Qt `CliSettings` or the release config example), and `MangaTranslator` reads them from the merged parameter dictionary.
- The nine workflow fields are `cli.load_text`, `cli.translate_json_only`, `cli.template`, `cli.generate_and_export`, `cli.colorize_only`, `cli.upscale_only`, `cli.inpaint_only`, and `cli.replace_translation`, plus `cli.save_text`, which is used together with `cli.template`.
- The main output image is written to the directory resolved from `-o/--output` (the CLI `save_info` does not carry `save_to_source_dir`); project JSON, original/translation template files, inpainted images, editor base images, and replace-translation pair images are always written to `manga_translator_work/` next to the source image directory, regardless of `-o`.
- Subprocess mode (`--subprocess`) consumes the same `cli` workflow fields; memory management and resume are covered in [Subprocess, memory, and recovery](./subprocess-memory-and-recovery.md).
- The exact branches, skipped stages, and file outputs of each field are defined in the `workflows/` pages; this page covers only the CLI-facing dispatch order and file read/write boundary.

## Workflow parameters {#workflow-parameters}

### The nine workflows and stored values {#workflow-mapping}

The index of the desktop “Translation Workflow Mode:” dropdown maps one-to-one to a `cli` field; `local` dispatches on the same fields after loading configuration. When the dropdown is switched, the UI first clears all eight mutually exclusive fields to `false` and then sets only the field for the selected mode (“Export Original Text” additionally requires `cli.save_text=true`), so at most one row below is active in a single configuration.

| Index | Stored value `cli.*` | English actual value | Simplified Chinese actual value | Start button (English / Simplified Chinese) | Main output / bypass |
| ---: | --- | --- | --- | --- | --- |
| 0 | All eight fields `false` | Normal Translation | 正常翻译流程 | Start Translation / 开始翻译 | Main output image; project JSON and inpainted image also written when `save_text` is enabled |
| 1 | `generate_and_export=true` | Export Translation | 导出翻译 | Export Translation / 导出翻译 | Project JSON + `translations/<stem>_translated.<template-ext>`, rendering skipped |
| 2 | `template=true` (with `save_text=true`) | Export Original Text | 导出原文 | Generate Original Text Template / 仅生成原文模板 | Project JSON + `originals/<stem>_original.<template-ext>`, translation and rendering skipped |
| 3 | `translate_json_only=true` | Translate JSON Only | 仅翻译（JSON） | Start JSON Translation / 开始仅翻译（JSON） | Writes back project JSON and deletes the original sidecar; no main output image |
| 4 | `load_text=true` | Import Translation and Render | 导入翻译并渲染 | Import Translation and Render / 导入翻译并渲染 | Loads regions from JSON, renders the main output image, and writes JSON back |
| 5 | `colorize_only=true` | Colorize Only | 仅上色 | Start Colorizing / 开始上色 | Main output image (colorized), detection/OCR/translation/rendering skipped |
| 6 | `upscale_only=true` | Upscale Only | 仅超分 | Start Upscaling / 开始超分 | Main output image (upscaled), detection/OCR/translation/rendering skipped |
| 7 | `inpaint_only=true` | Inpaint Only | 仅修复 | Start Inpainting / 开始修复 | Main output image (inpainted), translation/rendering skipped |
| 8 | `replace_translation=true` | Replace Translation | 替换翻译 | Start Replace Translation / 开始替换翻译 | Main output image (pasted translation); pair image from `translated_images/` |

The English/Simplified Chinese actual values above come from the actual entries in `desktop_qt_ui/locales/en_US.json` and `zh_CN.json` (the call key is the English copy), not from guessed translations. The three-column evidence follows:

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Translation Workflow Mode:` | Translation Workflow Mode: | 翻译流程模式： |
| `Normal Translation` | Normal Translation | 正常翻译流程 |
| `Export Translation` | Export Translation | 导出翻译 |
| `Export Original Text` | Export Original Text | 导出原文 |
| `Translate JSON Only` | Translate JSON Only | 仅翻译（JSON） |
| `Import Translation and Render` | Import Translation and Render | 导入翻译并渲染 |
| `Colorize Only` | Colorize Only | 仅上色 |
| `Upscale Only` | Upscale Only | 仅超分 |
| `Inpaint Only` | Inpaint Only | 仅修复 |
| `Replace Translation` | Replace Translation | 替换翻译 |
| `Start Translation` | Start Translation | 开始翻译 |
| `Generate Original Text Template` | Generate Original Text Template | 仅生成原文模板 |
| `Start JSON Translation` | Start JSON Translation | 开始仅翻译（JSON） |
| `Start Colorizing` | Start Colorizing | 开始上色 |
| `Start Upscaling` | Start Upscaling | 开始超分 |
| `Start Inpainting` | Start Inpainting | 开始修复 |
| `Start Replace Translation` | Start Replace Translation | 开始替换翻译 |
| `label_save_text` | Editable Image | 图片可编辑 |
| `label_format` | Output Format | 输出格式 |
| `label_overwrite` | Overwrite Existing Files | 覆盖已存在文件 |
| `label_batch_concurrent` | Concurrent Batch Processing | 并发批量处理 |
| `label_save_to_source_dir` | Save to Source Directory | 输出到原图目录 |
| `format_not_specified` | Not Specified | 不指定 |

The `local` console’s own workflow notices (for example “⚠️ 并发流水线已禁用：当前模式 [...]”) are hard-coded and do not go through locales, so they are not listed above.

### Mutual exclusion and priority {#exclusive-and-priority}

- Normal desktop switching guarantees mutual exclusion: `on_workflow_mode_changed()` first clears `load_text`, `translate_json_only`, `template`, `generate_and_export`, `colorize_only`, `upscale_only`, `inpaint_only`, and `replace_translation` to `false`, then sets only one field; reading back from configuration selects the dropdown index with the priority `replace_translation → inpaint_only → upscale_only → colorize_only → load_text → translate_json_only → template → generate_and_export → normal`.
- Manually editing the config file to set several fields to `true` is not a supported combination; core `translate_batch()` has a fixed dispatch order: `replace_translation` returns the earliest, and inside the batch loop `load_text` and `translate_json_only` are prioritized before the “template export / generate-and-export / normal chain”.
- “Export Original Text” enters `is_template_save_mode` only when both `template=true` and `save_text=true`; setting `template` without `save_text` does not export the original-text template.

### Relationship with the concurrent pipeline {#concurrency-relationship}

`batch_concurrent` (desktop “Concurrent Batch Processing”) applies only to “Normal Translation”. Both `local` and core `translate_batch()` treat `load_text`, `translate_json_only`, `template and save_text`, `generate_and_export`, `colorize_only`, `upscale_only`, `inpaint_only`, and `replace_translation` as incompatible modes: `local` resets `cli.batch_concurrent` to `false` and prints “并发流水线已禁用”, while core `translate_batch()` does not create a `ConcurrentPipeline` when it finds an incompatible field, falling back to per-image/serial processing. In other words, workflow fields are not switches that make concurrency run; they are bypasses that force concurrency off.

```mermaid
flowchart TD
    Start["local reads the cli section of config.json"] --> MT["MangaTranslator(params)"]
    MT --> R{"replace_translation?"}
    R -->|yes| REPLACE["Replace translation: extract copy from translated_images/ and paste"]
    R -->|no| L{"load_text?"}
    L -->|yes| LOAD["TXT→JSON pre-import, then load regions from JSON → mask/inpaint/render → write JSON back"]
    L -->|no| J{"translate_json_only?"}
    J -->|yes| JSONONLY["JSON only: read regions → translate → write JSON back → delete original sidecar"]
    J -->|no| T{"template and save_text?"}
    T -->|yes| TEMPLATE["Export original: skip translation and rendering, export originals/<stem>_original.<ext>"]
    T -->|no| G{"generate_and_export?"}
    G -->|yes| GEN["Export translation: render skipped after translation, export translations/<stem>_translated.<ext>"]
    G -->|no| PART{"colorize_only / upscale_only / inpaint_only?"}
    PART -->|yes| SHORT["Colorize/upscale/inpaint only: short-circuit result inside preprocessing"]
    PART -->|no| NORMAL["Normal: colorize→upscale→detect→OCR→translate→mask→inpaint→render→save main output"]
    NORMAL --> CONC{"batch_concurrent and no incompatible field?"}
    CONC -->|yes| PIPE["ConcurrentPipeline"]
    CONC -->|no| SERIAL["Per-image / serial batches"]
```

Diagram note: this is the source-confirmed dispatch order of `translate_batch()`; the `load_text` pre-import only converts TXT to JSON and the batch-inner branch still applies afterwards. When several fields are `true` at once, this order applies instead of the GUI mutual-exclusion rule.

## File modes {#file-modes}

### Main output image {#main-output-image}

- The output path is computed by `MangaTranslator._calculate_output_path()`: inside the directory resolved from `-o`, the relative hierarchy of input folders is preserved; when `cli.format` is empty, `none`, or “不指定” (`Not Specified`), the original file name (including its extension) is kept, otherwise `<stem>.<format>` is used.
- The CLI `save_info` contains only `output_folder`, `format`, `overwrite`, and `input_folders`; it does not contain `save_to_source_dir`, so the CLI never jumps to `manga_translator_work/result/` next to the source image. This differs from the desktop.
- When `--overwrite` is off, images whose main output already exists, or whose workflow sidecar already exists, are skipped; `local` performs an overwrite pre-check at startup.

### Per-image work directory {#per-image-work-directory}

Project JSON, template exports, inpainted images, editor base images, and replace-translation pair images are rooted at `manga_translator_work/` next to the source image directory and named after `<stem>` (the input file name without extension):

| Resource | Relative path / file name | Read/write rule |
| --- | --- | --- |
| Project JSON | `manga_translator_work/json/<stem>_translations.json` | Lookup tries the new location first, then falls back to the legacy `<image-dir>/<stem>_translations.json` |
| Original export | `manga_translator_work/originals/<stem>_original.<template-ext>` | Falls back to `json` when the template format is missing or unreadable |
| Translation export | `manga_translator_work/translations/<stem>_translated.<template-ext>` | Same as above |
| Inpainted image | `manga_translator_work/inpainted/<stem>_inpainted.<original-ext>` | Written when `save_text` is enabled and inpainting finished |
| Editor base image | `manga_translator_work/editor_base/<original-file-name>` | Written when colorization or upscaling ran |
| Replace-translation pair image | `manga_translator_work/translated_images/<stem><ext>` | Same extension first, then iterate supported extensions |
| Paint overlay | `manga_translator_work/paint_overlay/<stem>_overlay.png` | Written when the editor saves a color paint overlay |
| YOLO labels | `manga_translator_work/yolo_labels/<stem>.txt` | Written when YOLO label import/export is enabled |

These directory names are reserved by `manga_translator/utils/path_manager.py`; folder scanning skips the whole `manga_translator_work` directory, so do not treat the work directory as ordinary input.

### Project JSON {#translation-json}

- The project JSON records each image’s regions, source/translation text, masks, and post-rendering fields; it is written to `manga_translator_work/json/` when `save_text` (desktop “Editable Image”) is enabled, on template export, or on JSON-only write-back.
- `_save_text_to_file()` writes `skip_font_scaling` and `skip_text_replacements` depending on the mode: original-text export / JSON-only write `false` (re-run smart layout when importing for rendering), translation export writes `true` (replay the generated result), and a rendered image writes `skip_text_replacements=true` to prevent a second replacement pass.
- JSON-only and import-and-render modes require a parseable JSON; the parse-failure fuse skips the write-back so that the project file is not overwritten and regions are not permanently lost. Field structure is detailed in the `workflows/` pages and the editor import/export page.

### Original and translation template exports {#template-exports}

- The default template file is `config/translation_template.json` (overridable by the `MANGA_TEMPLATE_PATH` environment variable or a UI file picker).
- Template text uses the `<original>` and `<translated>` placeholders; `translation_template.py` parses the first `output_format:` line to obtain the export extension (a safe 1–32 character extension), falling back to `json` when missing or invalid.
- “Export Original Text” calls `generate_original_text` and “Export Translation” calls `generate_translated_text`; the `load_text` pre-import of “Import Translation and Render” uses `safe_update_large_json_from_text` to write TXT content back into JSON using the same template.

### Replace-translation pair image {#replace-translation-pairs}

`replace_translation` needs a translated image as the “translation source”: `find_translated_image()` always looks in `manga_translator_work/translated_images/`, matching the same extension first and then iterating supported extensions; the translation JSON is also located inside that directory or in the legacy position. Once found, OCR obtains the paired regions and `render.enable_template_alignment` selects the “direct paste” or “re-render” branch; see [Replace translation](../workflows/replace-translation.md).

## Runtime behavior {#runtime-behavior}

### Workflow dispatch order {#workflow-dispatch}

The dispatch order is described in [Mutual exclusion and priority](#exclusive-and-priority) and the diagram note above. Key points:

- `local` copies the `cli` section of `config_service.get_config().model_dump()` into `translator_params`, and `MangaTranslator` reads the nine fields with calls such as `params.get('load_text', False)`; the config-file field names are therefore the stored values.
- In batch processing, `translate_batch()` first performs the `load_text` TXT→JSON pre-import; `replace_translation` then returns the earliest; inside the batch loop the branches run in the order `load_text → translate_json_only → normal preprocessing → template+save_text → generate_and_export → normal render and save`.
- `template+save_text` forces `batch_size=1` (per-image disk writes); other workflows batch by `cli.batch_size`.
- Colorize/upscale/inpaint only short-circuit inside normal preprocessing (`colorize_only` returns the colorized result, `upscale_only` returns the upscaled result, `inpaint_only` returns the inpainted result after mask refinement); all of them skip translation and rendering.

### The three default sets {#default-values}

There is no “always on” default for the workflow fields; the three default sets only affect base fields such as `save_text`, `format`, `overwrite`, `batch_size`, and `batch_concurrent`:

| Field | Core `Config.cli` | Qt `CliSettings` | Release `config-example.json` |
| --- | --- | --- | --- |
| `save_text` | `false` | `true` | `true` |
| `format` | `None` | `不指定` | `不指定` |
| `overwrite` | `false` | `true` | `true` |
| `batch_size` | `1` | `1` | `3` |
| `batch_concurrent` | `false` | `false` | `false` |
| `attempts` | `-1` | `-1` | `3` |
| `save_to_source_dir` | `false` | `false` | `false` |
| Six GUI workflow booleans | no such field (consumers fall back to `False`) | all `false` | all `false` |

`load_text`, `template`, `generate_and_export`, `colorize_only`, `upscale_only`, and `inpaint_only` are not formal fields of the core `CliConfig`; `MangaTranslator` reads them from the parameter dictionary and falls back to `False` when missing. `replace_translation` and `translate_json_only` exist in the core `CliConfig` and default to `false`.

## Dependencies and conflicts {#dependencies-and-conflicts}

- Workflow fields conflict with `batch_concurrent`: all eight special branches (including `template and save_text`) force the concurrent pipeline off.
- `cli.format` affects only the main output image extension; it does not affect the project JSON (always `.json`) or the template-export extension (decided by the template `output_format`).
- The `--subprocess` branch explicitly writes only `use_gpu`/`disable_onnx_gpu` into `cli_config`; the other workflow fields still come from the config file, and `--format`/`--batch-size`/`--attempts` do not enter that branch’s override writes (source difference; see [Configuration overrides](./configuration-overrides.md)).
- Stacking several workflow fields manually runs in the core dispatch order, a combination the desktop does not guarantee; on JSON parse failure, JSON-only/import-and-render skip the write-back to protect the project file.
- Sidecar files are written next to the source image directory even when `-o` points elsewhere; before deleting, migrating, or sharing `manga_translator_work/`, check whether it contains user images and text.

## Related files and formats {#related-files-and-formats}

| File/format | Actual role on this page | Note |
| --- | --- | --- |
| `config/config.json` | Source of the workflow fields in the `cli` section | Never display a real user config or a private absolute path |
| `config/config-example.json` | Release default reference | Differences from core/Qt defaults are in [The three default sets](#default-values) |
| `config/translation_template.json` | Original/translation export template and `output_format` | Record only the placeholder structure and sanitized samples, never real text |
| `manga_translator_work/json/*_translations.json` | Project JSON read/write | Remove user text and paths before sharing |
| `manga_translator_work/originals/`, `translations/` | Template-export sidecars | File names must match the input `<stem>` |
| `manga_translator_work/inpainted/`, `editor_base/`, `paint_overlay/`, `translated_images/` | Inpainted images, base images, overlays, replace pairs | Written conditionally by the corresponding workflow |
| `manga_translator_work/yolo_labels/` | YOLO labels | Participates when import/export is enabled |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Parser | `manga_translator/args.py` | `local` has no workflow switch; `--format`/`--overwrite` help text |
| CLI execution | `manga_translator/mode/local.py` | `cli_config` read, `batch_concurrent` incompatibility disable, overwrite pre-check, `save_info` fields |
| Config models | `manga_translator/config.py`, `desktop_qt_ui/core/config_models.py` | Core `CliConfig` vs Qt `CliSettings` field differences and defaults |
| Workflow dispatch | `manga_translator/manga_translator.py` | Nine-field read, `translate_batch()` branches, template export, JSON write-back, inpainted/base image saves |
| Paths/files | `manga_translator/utils/path_manager.py`, `manga_translator/utils/translation_template.py`, `desktop_qt_ui/services/workflow_service.py` | `manga_translator_work` subdirectories, naming rules, placeholders, and `output_format` |
| Desktop mapping | `desktop_qt_ui/ui/main_page/runtime.py`, `desktop_qt_ui/ui/main_page/pages/translation_page.py` | Dropdown index, mutual-exclusion writes, start-button copy |
| i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Actual bilingual values for workflow and button keys |
| Research | `doc/wiki/research/cli-command-inventory.md`, `doc/wiki/research/workflow-matrix-source-evidence.md` | Official subcommand contract and the nine-workflow matrix |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract |
| `local --help` | Complete | Actually ran `uv run --no-sync python -m manga_translator local --help`; options match this page |
| i18n three columns | Complete | Verified actual values in `en_US.json` / `zh_CN.json` item by item |
| Workflow dispatch and file paths | Complete | Statically checked `manga_translator.py`, `path_manager.py`, `config.py`, and `mode/local.py` |
| Sanitized runtime verification | Deferred | No real translation run; no user image, config, key, or private path was read |
| Static checks | Complete | `verify-route-mirror.mjs` and `verify-source-evidence.mjs` PASS for this page (pre-existing issues in other repo pages are reported separately) |
