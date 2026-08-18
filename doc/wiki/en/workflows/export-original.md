---
title: Export Original Text
description: Export original text read-only from local project JSON by default, without detection, OCR, or JSON write-back; an option restores the legacy flow
pageId: workflows.export-original
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Export Original Text

Export Original Text uses the legacy image detection/OCR and JSON-write flow by default. Enabling Settings → Mode Specific → Text Export → “Export Text from Local JSON Only” instead reads `regions[].text` from existing local project JSON and writes only `<stem>_original.<template-format>` without opening the image, running detection/OCR, or writing JSON back.

See [Output Directory and Workflow](../desktop/translation/output-directory-and-workflow.md) for the nine-mode overview and [Mode-Specific Workflows and Template Alignment](../desktop/settings/mode-specific.md#cli-export-from-local-json) for this toggle and `cli.template`.

## When to use it {#feature-boundary}

- Selecting Export Original Text (combo index `2`) sets `cli.template=true`; by default it detects/OCRs the main image and writes the original-text sidecar.
- With the toggle on, inputs become the matching project JSON and a readable template. The image path only locates JSON and is not decoded; no main image is written and JSON is unchanged.
- With the toggle off, the legacy flow remains active; it requires `cli.save_text=true` and runs detection, OCR, mask processing, and JSON write-back.

## Run this workflow {#ui-operations}

### Select the Export Original Text workflow {#select-export-original}

1. Open the “Translation” page and click the “Translation Workflow Mode:” combo box in the “Translation Task” card.
2. Select “Export Original Text”. The UI sets only `cli.template=true`, clears the other seven workflow fields, and saves the configuration; the title changes to “Export Original Text” and the subtitle shows the matching tip.
3. Click “Generate Original Text Template” to start the task. Switching modes does not start a task automatically; while a task is running, the button shows states such as “Stop Translation”; see [Progress, Stop, and Task State](../desktop/translation/progress-stop-and-task-state.md).

`imagename` in the tip is the program's example name for the input `<stem>`, not a private user file name; `manga_translator_work/originals/` is a fixed subdirectory under each image's work directory.

## Processing order {#runtime-behavior}

When `cli.export_from_local_json` is enabled, `translate_batch()` reads local JSON before image materialization. Missing JSON fails that image without OCR fallback. The toggle defaults off; when off, the legacy `template + save_text` branch runs.

```mermaid
flowchart LR
    Input["Input image path"] --> Find["Find matching project JSON"]
    Find -->|found| Read["Read regions.text"] --> Export["Render original sidecar through template"]
    Export --> Tpl["originals/&lt;stem&gt;_original.&lt;extension&gt;"]
    Find -->|missing or invalid| Fail["Fail this image; never fall back to OCR"]
    Read -. "not executed" .-> Skip["Image decode / detection / OCR / JSON write-back"]
```

### Input and discovery rules {#input-and-discovery}

- The translation-page file list still supplies image paths; each path determines the work directory and matching project JSON, but the export branch does not open image content.
- Project JSON must exist at the new `manga_translator_work/json/<stem>_translations.json` location or the compatible legacy sibling path.
- The original sidecar is written to `manga_translator_work/originals/<stem>_original.<template-format>`; a readable template is required, with invalid/missing formats falling back to `json`.
- `detector.import_yolo_labels` applies only to the legacy detection flow after local JSON export is disabled.

### Skipped and kept stages {#skipped-and-kept-stages}

- With the toggle on, image loading, colorization, upscaling, detection, OCR, translation, mask processing, inpainting, rendering, main-image saving, and JSON write-back are skipped.
- With the toggle on, `regions[].text` is read from existing JSON and rendered through the template. `<translated>` may still use the JSON's existing `translation` value.
- With the toggle off, the legacy flow runs conditional colorization/upscaling, detection, OCR, mask refinement, and JSON writing; it still does not call a translation service.

### Project JSON details {#mask-and-json-details}

- With the toggle on, project JSON is opened read-only and its bytes remain unchanged, including mask, font size, translations, and editor fields.
- `generate_original_text()` exports non-empty source text through the template and removes `[BR]`; no regions produce the template's empty output.

### Output files {#output-files}

| Output | Path | Notes |
| --- | --- | --- |
| Project JSON | `manga_translator_work/json/<stem>_translations.json` | Required, read-only, and unchanged when the toggle is on |
| Original-text template | `manga_translator_work/originals/<stem>_original.<template-format>` | Written by both paths; extension comes from template `output_format` |
| Main output image | not written | Export mode does not render a main image |
| Editor base image | conditional | Only the legacy path with the toggle off may colorize or upscale |

## Inputs, outputs, and limitations {#dependencies-and-conflicts}

- With the toggle on, an existing project JSON and readable template are required. Missing JSON fails without OCR fallback; `batch_concurrent` remains incompatible.
- With `cli.overwrite=false`, an existing original sidecar is skipped before processing.
- `cli.save_text` remains part of the Export Original Text workflow flag combination; with the toggle on, JSON is never saved or overwritten.
- With the toggle off, the legacy flow and its detection, OCR, colorization/upscaling, and `save_text` write behavior remain active.

## Read next {#related-pages}

- Other workflows: [Normal Translation](./normal.md) · [Export Translation](./export-translation.md) · [Translate JSON Only](./translate-json-only.md) · [Import Translation and Render](./import-translation-and-render.md) · [Colorize Only](./colorize-only.md) · [Upscale Only](./upscale-only.md) · [Inpaint Only](./inpaint-only.md) · [Replace Translation](./replace-translation.md)
- Selecting a workflow, output directory, and mutually exclusive writes: [Output Directory and Workflow](../desktop/translation/output-directory-and-workflow.md)
- Inputs, skipped stages, and outputs of all nine workflows: [Workflow Matrix](../reference/workflow-matrix.md)
- Mutually exclusive workflow fields, parameter overrides, and template alignment: [Mode-Specific Workflows and Template Alignment](../desktop/settings/mode-specific.md)

> See the reference index: [Workflow Matrix](../reference/workflow-matrix.md).
