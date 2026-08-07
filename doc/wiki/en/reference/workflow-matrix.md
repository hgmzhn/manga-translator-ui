---
title: Workflow Matrix
description: Summary matrix of inputs, skipped stages, and outputs of the nine translation workflows, with reverse links to their pages
pageId: reference.workflow-matrix
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Workflow Matrix

This page is the reference index for the nine workflows of the "Translation Workflow Mode:" selector on the translation page: it summarizes the inputs, skipped stages, and outputs of each workflow in one matrix and reverse-links to the corresponding pages. For the full operations, parameter boundaries, file formats, and skip conditions of a workflow, enter its [workflow page](../workflows/normal.md) from a matrix row; the selector, start-button texts, output-directory controls, and mutually exclusive writes are covered by [Output Directory and Workflow](../desktop/translation/output-directory-and-workflow.md), adding files and list states by [File List and Input](../desktop/translation/file-list-and-input.md), and mode-forced parameter overrides by [Mode-Specific Workflow and Template Alignment](../desktop/settings/mode-specific.md).

This page summarizes and reverse-links only; it does not replace the workflow pages or the settings pages. The parameter algorithms of detection, OCR, translation, mask, inpainting, rendering, upscaling, and colorization live in the corresponding settings pages.

## Selector and workflow fields {#selector-and-workflow-fields}

| Index | English | Simplified Chinese | Start button (English / Simplified Chinese) |
| ---: | --- | --- | --- |
| 0 | Normal Translation | 正常翻译流程 | Start Translation / 开始翻译 |
| 1 | Export Translation | 导出翻译 | Export Translation / 导出翻译 |
| 2 | Export Original Text | 导出原文 | Generate Original Text Template / 仅生成原文模板 |
| 3 | Translate JSON Only | 仅翻译（JSON） | Start JSON Translation / 开始仅翻译（JSON） |
| 4 | Import Translation and Render | 导入翻译并渲染 | Import Translation and Render / 导入翻译并渲染 |
| 5 | Colorize Only | 仅上色 | Start Colorizing / 开始上色 |
| 6 | Upscale Only | 仅超分 | Start Upscaling / 开始超分 |
| 7 | Inpaint Only | 仅修复 | Start Inpainting / 开始修复 |
| 8 | Replace Translation | 替换翻译 | Start Replace Translation / 开始替换翻译 |

## Workflow summary matrix {#workflow-summary-matrix}

The table below summarizes the inputs, skipped stages, and outputs of the nine workflows. "Conditional" means the stage is decided by ordinary parameters (for example `colorizer.colorizer != none`, a truthy `upscale.upscale_ratio`, or detected text), not forced by the workflow; the "Skipped stages" column lists only the parts that the workflow path does not call or explicitly skips. Detailed discovery rules, file naming, and skip conditions are in the workflow page linked from each row.

| Workflow | Input and prerequisites | Skipped stages | Outputs | Detailed page |
| --- | --- | --- | --- | --- |
| Normal Translation | Main input images; no workflow side-file prerequisite | No forced skip; returns early when detection finds no textlines or OCR finds no text | Main output image; project JSON and inpainted image when `save_text=true`; editor base image when colorization or upscaling is enabled | [Normal Translation](../workflows/normal.md) |
| Export Translation | Main input images and a readable export template | Inpainting, rendering, main-image saving | Project JSON and `translations/<stem>_translated.<template-format>` | [Export Translation](../workflows/export-translation.md) |
| Export Original Text | Main input images and a readable export template | Translation, inpainting, rendering, main-image saving | Project JSON and `originals/<stem>_original.<template-format>` | [Export Original Text](../workflows/export-original.md) |
| Translate JSON Only | Main input images must resolve to a project JSON | Colorization, upscaling, detection, OCR, merge, mask, inpainting, rendering | Writes back the project JSON; deletes the original side-file on success; no main output image | [Translate JSON Only](../workflows/translate-json-only.md) |
| Import Translation and Render | Main input images must have a project JSON; original side-file preferred over translated side-file | Colorization, upscaling, detection, OCR, translation, textline merge (detection is the exception when the JSON lacks a mask and YOLO labels are imported) | Main output image and updated project JSON; inpainted image when re-inpainting runs | [Import Translation and Render](../workflows/import-translation-and-render.md) |
| Colorize Only | Main input images; whether colorization runs depends on `colorizer.colorizer` | Upscaling, detection, OCR, merge, translation, mask, inpainting, rendering | Main output image; editor base image when a colorizer is active | [Colorize Only](../workflows/colorize-only.md) |
| Upscale Only | Main input images; the actual ratio comes from `upscale.upscale_ratio` | Detection, OCR, merge, translation, mask, inpainting, rendering | Main output image; editor base image when colorization or a ratio is enabled | [Upscale Only](../workflows/upscale-only.md) |
| Inpaint Only | Main input images | OCR, translation, rendering | Main output image; the branch clears `text_regions` and does not render translated text | [Inpaint Only](../workflows/inpaint-only.md) |
| Replace Translation | Main input is the raw image; a same-named translated image must exist in `translated_images/` in the per-image work directory | Translation service call | Main output image; additionally the inpainted image and project JSON when not direct-paste and `save_text=true`; neither is written in direct-paste mode, and no PSD is exported | [Replace Translation](../workflows/replace-translation.md) |

## Mutual exclusion, concurrency, and parameter boundaries {#mutual-exclusion-and-concurrency}

- GUI switching guarantees that the eight workflow boolean fields are mutually exclusive, but it does not validate combinations provided by hand-written JSON, service requests, or other entry points. When `sync_workflow_mode_from_config()` reads an existing combination, the display priority is: Replace Translation, Inpaint Only, Upscale Only, Colorize Only, Import Translation, Translate JSON Only, Export Original Text, Export Translation, Normal. The `translate_batch()` entry performs the `load_text` TXT pre-import first, then dispatches in the order `replace_translation` → `load_text` → `translate_json_only` → ordinary pre-processing (`colorize_only` returns before `upscale_only`/`inpaint_only`) → Export Original Text → Export Translation; hand-combined modes have no "run simultaneously" contract.
- `batch_concurrent` can enter the concurrent pipeline only from Normal Translation; the other eight modes are treated as incompatible in both the desktop controller and the core `translate_batch()`. The frontend switches the local variable for this run to non-concurrent, and the core branch builds a `ConcurrentPipeline` only when no incompatible mode is present.
- `render.enable_template_alignment` ("Enable Direct Paste Mode") is specific to Replace Translation: when enabled it uses direct paste and writes no JSON, inpainted image, or PSD; when disabled it re-renders with the paired regions from OCR.

## Read next {#related-pages}

| Page | Relationship to this page |
| --- | --- |
| [Output Directory and Workflow](../desktop/translation/output-directory-and-workflow.md) | overview entry for selecting the nine modes, button texts, output-directory controls, and exclusive writes |
| [File List and Input](../desktop/translation/file-list-and-input.md) | the shared main-input discovery rules of all workflows (recursive, natural sort, skip `manga_translator_work`) |
| [Progress, Stop, and Task State](../desktop/translation/progress-stop-and-task-state.md) | the shared start, stop, cancel, and progress states of all workflows |
| [Mode-Specific Workflow and Template Alignment](../desktop/settings/mode-specific.md) | forced parameter overrides and template alignment of modes such as Replace Translation |
| [CLI Batch and Output](../desktop/settings/cli-batch-and-output.md) | `batch_size`/`batch_concurrent` and output format; concurrency is open to Normal mode only |
| [Upscale and Colorization](../desktop/settings/upscale-and-colorization.md) | prerequisite parameters of conditional colorization/upscaling (colorizer, ratio) |

The reverse links to the nine workflow pages live in the "Detailed page" column of the matrix; the workflow pages also link among themselves (for example the template/JSON family and the pass-through workflows).

## Developer Guide {#developer-guide}

### Option matrix {#option-matrix}

The "Translation Workflow Mode:" (`Translation Workflow Mode:`) combo box is built by index, and the index is also the mapping used by `on_workflow_mode_changed()` to write configuration. When the mode changes, the GUI first clears all eight mutually exclusive `cli` workflow fields to `false`, then sets and saves only the field of the selected mode; a single GUI selection is therefore exclusive. "Export Original Text" additionally depends on `cli.save_text`, see [Mutual exclusion, concurrency, and parameter boundaries](#mutual-exclusion-and-concurrency).

| Index | English | Simplified Chinese | Stored value | Start button (English / Simplified Chinese) |
| ---: | --- | --- | --- | --- |
| 0 | Normal Translation | 正常翻译流程 | all eight workflow fields `false` | Start Translation / 开始翻译 |
| 1 | Export Translation | 导出翻译 | `generate_and_export=true` | Export Translation / 导出翻译 |
| 2 | Export Original Text | 导出原文 | `template=true` | Generate Original Text Template / 仅生成原文模板 |
| 3 | Translate JSON Only | 仅翻译（JSON） | `translate_json_only=true` | Start JSON Translation / 开始仅翻译（JSON） |
| 4 | Import Translation and Render | 导入翻译并渲染 | `load_text=true` | Import Translation and Render / 导入翻译并渲染 |
| 5 | Colorize Only | 仅上色 | `colorize_only=true` | Start Colorizing / 开始上色 |
| 6 | Upscale Only | 仅超分 | `upscale_only=true` | Start Upscaling / 开始超分 |
| 7 | Inpaint Only | 仅修复 | `inpaint_only=true` | Start Inpainting / 开始修复 |
| 8 | Replace Translation | 替换翻译 | `replace_translation=true` | Start Replace Translation / 开始替换翻译 |

For all nine modes the call key equals the actual English value (both are the strings passed to `_t()` at runtime, not `label_*` setting keys); the Simplified Chinese column is verified against `desktop_qt_ui/locales/zh_CN.json`. The combo box has no separate `userData`; the index is the mode value.

The boundaries of cross-mode parameters on workflow branches are:

| Parameter | Effective/ignored boundary |
| --- | --- |
| `cli.save_text` | GUI/release default is `true`; required for the Export Original Text branch, also controls project JSON, inpainted images, and editor-project writes in ordinary workflows; Translate JSON Only writes back JSON unconditionally |
| `colorizer.colorizer` | not a forced value of Colorize Only; Normal, Upscale Only, Inpaint Only, and Replace Translation also colorize first when it is not `none` |
| `upscale.upscale_ratio` | not a forced value of Upscale Only; when empty, Upscale Only passes through (or keeps the preceding colorization result); Normal, Inpaint Only, and Replace Translation also upscale first when it is truthy |
| `detector.import_yolo_labels` | Export Original Text/Export Translation skip mask refinement and mask saving; Import Translation triggers detection to create a mask when the JSON has none |
| `render.paste_mask_dilation_pixels` | consumed only by the direct-paste branch of Replace Translation to dilate the paste mask |
| `cli.overwrite` | before start, the GUI checks existing side-files or the main output image per workflow: the corresponding TXT for the two exports, the original side-file for Translate JSON Only, and the main output image for the other modes |

The "skip when the original side-file does not exist" condition of Translate JSON Only runs in the opposite direction of a normal overwrite check and may vary by release; the real GUI dialogs, files retained after cancellation, and error prompts of the nine modes also follow the unverified list in the research material.

### Related files and formats

#### Per-image work directory and file naming {#per-image-work-directory}

Apart from the main output image, all workflow side-files are located in the per-image work directory based on the original path and extension-less `<stem>` of the input image; JSON lookup uses the new location first and falls back to the old image-side location. The extension of template-exported/imported side-files comes from the first `output_format:` line of `config/translation_template.json`; a legal value is a safe 1–32 character extension, and a missing or invalid value falls back to `json`.

| Resource | New location / file name | Compatibility or priority |
| --- | --- | --- |
| Translation project JSON | `manga_translator_work/json/<stem>_translations.json` | falls back to `<image-dir>/<stem>_translations.json` |
| Original export | `manga_translator_work/originals/<stem>_original.<template-format>` | format is `json` when the template is missing or unreadable |
| Translation export | `manga_translator_work/translations/<stem>_translated.<template-format>` | same as above |
| Inpainted image | `manga_translator_work/inpainted/<stem>_inpainted.<original-ext>` | no other lookup location |
| Colorization/upscaling editor base | `manga_translator_work/editor_base/<original-filename>` | compatible with the same-named base at the old work-directory root |
| Replace-translation pair image | `manga_translator_work/translated_images/<stem><ext>` | same extension first, then every `SUPPORTED_IMAGE_EXTENSIONS` entry |

The main output image is decided by `MangaTranslator._calculate_output_path()`: the normal output directory keeps the input folder name and relative hierarchy; with `save_to_source_dir=true` it becomes `manga_translator_work/result/` next to the original; with `cli.format` empty or `none` the original extension is kept, otherwise the given extension is used.

### Code locations {#source-evidence}
| Layer | File | What this page verified |
| --- | --- | --- |
| UI layout | `desktop_qt_ui/ui/main_page/pages/translation_page.py:27` | translation page, workflow combo box, start button, and event wiring |
| Workflow state and writes | `desktop_qt_ui/ui/main_page/runtime.py:21,151-238` | nine indexes, prompts, exclusive-field writes, config-sync priority, and button texts |
| i18n | `desktop_qt_ui/locales/en_US.json:488`; `desktop_qt_ui/locales/zh_CN.json:486` | actual values of workflows, start buttons, prompts, and settings |
| Input and discovery | `desktop_qt_ui/services/file_service.py:31` | main-input extension validation, recursive discovery, natural sort, and work-directory exclusion |
| Controller | `desktop_qt_ui/app_logic.py:3094` | main-output path passing, pre-overwrite checks, and special-mode concurrency disabling |
| Qt config | `desktop_qt_ui/core/config_models.py:123` | workflow fields and the `save_text` default |
| Core dispatch | `manga_translator/manga_translator.py:504,3399,4236,5206` | main output, JSON writes, template exports, TXT imports, and the nine runtime dispatches |
| Paths/templates | `manga_translator/utils/path_manager.py:12`; `manga_translator/utils/translation_template.py:10` | per-image work directory, side-file discovery, and `output_format` fallback |
| Replace translation | `manga_translator/utils/replace_translation.py:128,726` | dual-image processing, pairing, direct paste, and output boundaries |
| Research material | `doc/wiki/research/workflow-matrix-source-evidence.md` | inputs, stages, outputs, and the unverified list of the nine workflows |
