---
title: First Translation
description: Desktop steps and workflow boundaries for adding images, choosing an output directory, and running the first translation task
pageId: introduction.first-translation
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# First Translation

> Not installed yet? Start with the [installation guide](../install/windows-portable.md) (Windows portable / source / Linux/macOS / Docker).
> Online translation needs an API Key: see the [API configuration guide](../desktop/api-management/api-key-guide.md) and the [API feature selectors](../desktop/api-management/feature-selectors.md).

This guide covers the first complete translation on desktop in three parts: Installation → API Configuration → Steps.

## What to know first {#feature-boundary}

This guide focuses on the minimal path for the first complete translation on desktop: install, configure the API (required for online translation), add images, set an output directory, keep “Normal Translation”, and start the task. Parameter details of each module (detector, OCR, translator, typesetting, and API credentials) are covered on their own pages ([Settings](../desktop/settings/index.md), [Translator](../desktop/translator/selection-and-languages.md), [API Management](../desktop/api-management/feature-selectors.md)), not here.

For a first run, use a public, non-sensitive sample image. “Normal Translation” is not a configuration-free demo mode: online translation still requires credentials configured in API Management.

## Installation {#installation}

Install the desktop app first, using any of these options:

- **Windows Portable**: extract and run, see [Windows Portable](../install/windows-portable.md).
- **Windows from Source**: run from source, see [Windows from Source](../install/source-windows.md).
- **Linux and macOS**: run on Linux or macOS, see [Linux and macOS Installation](../install/linux-and-macos.md).
- **Docker Deployment**: run in a container, see [Docker Deployment](../install/docker.md).

After installing and launching, open “Translation Interface” in the sidebar and continue with [Steps](#steps) below.

## API Configuration {#api-configuration}

Online translation needs an API Key. If you use online translation, configure it in “API Management” first:

- Apply for and enter the API Key: see the [API configuration guide](../desktop/api-management/api-key-guide.md).
- Choose which translation features and models to enable: see the [API feature selectors](../desktop/api-management/feature-selectors.md).

Then return here and follow [Steps](#steps) to add files and start translation; skip this section if you do not use online translation.

## Steps {#steps}

1. Open “Translation Interface” in the sidebar. This is the page where desktop translations run; its title defaults to “Normal Translation”.
2. Add inputs: click “Add Files” to choose images, or click “Add Folder” to add images from a folder. You can also drop files or folders directly onto the input list. The list shows the added files, and each file can be removed individually.
3. Make sure the input list is not empty; translation cannot start with an empty list. “Clear List” only clears the current input list and does not delete source images or already-generated results on disk.
4. Set the output directory: type a directory into the “Output Directory” field, click “Browse...” to pick one, or drop a folder onto the field. The translated images are written to this directory.
5. Keep “Translation Workflow Mode” at “Normal Translation” and click “Start Translation”.

Before starting, the app checks the output directory, the input list, and the API requirements; if a check fails, the task does not start.

## During and After a Task {#during-and-after}

- After clicking “Start Translation”, the button first shows `Starting...` and then becomes “Stop Translation”. While the task is running, the input list and the add/clear buttons are disabled, and the progress area shows the current count, total count, and status messages.
- Clicking “Stop Translation” changes the button to “Stopping...”. After the stop completes, the task returns to a stopped state. Stopping only cancels the current task and does not delete files already saved.
- Successful, partially failed, and skipped results are recorded in the task state and logs. The translated images are written to the output directory you set; “Open” only opens the output directory, not the editor. To continue editing a result, see [Editor import, export, and writeback](../desktop/editor/import-export-and-writeback.md).
- “Export Translation”, “Export Original Text”, “Translate JSON Only”, and “Import Translation and Render” are other workflows that require understanding project sidecar files. For a first run where you only want translated images, use “Normal Translation” and do not choose those modes.

“Normal Translation” runs the configured stages in order: colorization → upscaling → detection → OCR → text-line merging → translation → inpainting → typesetting and rendering; every stage is optional and skipped when not configured.

## Dependencies and Conflicts {#dependencies-and-conflicts}

- Readable input images and an existing, writable output directory are required; otherwise the task does not start.
- Common image extensions (such as png, jpg, and webp) are supported; archives such as .zip and .cbz must be verified by actually running them.
- Online translation requires credentials, addresses, and models configured in API Management first; this page never displays real secrets.
- `save_text` controls whether text content is included with the results, and with `overwrite` disabled existing same-name results may be skipped; both are adjusted in Settings.

For stored values and defaults, see the [Settings Parameter Index](../reference/settings-index.md).
