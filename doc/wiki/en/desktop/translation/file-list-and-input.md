---
title: File List and Input
description: Add images, folders, or archives in the desktop translation workspace and manage the pending file tree
pageId: desktop.translation.file-list-and-input
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# File List and Input

## Feature boundary

This page explains how the desktop “Translation Interface” collects, displays, and removes pending inputs: individual image files, folders, dropped paths, and supported archives/documents. The file list only manages input sources, the thumbnail tree, and selection state; output directory, workflow selection, start action, and task progress belong to [Output Directory and Workflow](./output-directory-and-workflow.md) and [Progress, Stop, and Task State](./progress-stop-and-task-state.md).

An archive can appear in the input list without having been extracted. Extraction and image discovery happen during the scan that starts the task.

## UI operations

### Adding files, folders, and drops

The translation page input card has three buttons. The file dialog remembers the last open directory; after a successful selection, the directory containing the first file becomes the next starting directory.

1. Click “Add Files” and choose one or more supported images or archives in the file dialog.
2. Click “Add Folder” and choose one or more directories in the folder selector. Folder scanning recurses into child directories.
3. You can also drag files or folders into the file-list area. The Qt drop handler accepts local file URLs and sends their paths through the same input coordinator used by the buttons.
4. The same path is not added twice. Adding a parent folder supersedes separately added paths below it; adding a path already covered by a listed parent does not create another source.

“Clear List” removes all input sources and the exclusion records for items in the list. While a task is running, adding, removing, and clearing are rejected and a warning is logged.

### File tree, thumbnails, and single-item removal

The list displays scan results as a one- or multi-level folder tree: folder nodes show their file count, while image nodes show a thumbnail, file name, and status dot. An image is “Translated” or “Untranslated” according to whether an associated translation JSON was found during the scan; archives show an archive icon rather than an image thumbnail.

- Selecting an image node emits a selection event that the main window can use to open or synchronize the editor.
- Visible image thumbnails load asynchronously and use a bounded cache. A thumbnail read failure leaves the file node in place instead of turning it into a list-scan failure.
- Clicking the close icon at the right of a node removes only that item. Removing a folder removes its descendants; if an item is still covered by a retained parent folder, the removal is recorded as an excluded file/subfolder so the next scan does not add it again.
- During a rescan, the model is cleared and shows a loading message. When the result arrives, the view attempts to restore expanded directories and the selected path.

### Empty, loading, ready, and error states

| State | UI behavior | User action |
| --- | --- | --- |
| Empty | The list shows “Drag and drop files or folders here / or click the buttons above to add” and a dashed area | Click “Add Files” or “Add Folder”, or drop local paths |
| Loading | The list model is temporarily cleared and shows “正在加载文件列表...” | Wait for the background scan; an older result cannot replace a newer request |
| Ready | The folder tree, counts, thumbnails, and “Translated”/“Untranslated” status are visible | Select files, expand directories, or remove an item |
| Error | The list model is cleared and the specific message is shown in an error color | Correct the path or permissions and add it again; do not copy private paths from the error into a public report |

If the scan started with a task finds no valid images, the application shows “File List Empty” and “Please add image files to translate!”. An archive that cannot be extracted, or an unsupported file, is not a valid image input by itself.

## Option matrix

This feature has no configurable enum parameter. The following table records UI-call keys and their actual locale values; the English page retains all three columns for direct comparison.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Add Files` | Add Files | 添加文件 |
| `Add Folder` | Add Folder | 添加文件夹 |
| `Clear List` | Clear List | 清空列表 |
| `Drag and drop files or folders here\\nor click the buttons above to add` | Drag and drop files or folders here / or click the buttons above to add | 拖拽文件或文件夹到此处 / 或点击上方按钮添加 |
| `Translated` | Translated | 已翻译 |
| `Untranslated` | Untranslated | 未翻译 |
| `File List Empty` | File List Empty | 文件列表为空 |
| `Please add image files to translate!` | Please add image files to translate! | 请先添加要翻译的图片文件！ |
| `正在加载文件列表...` | English key missing; code falls back to 正在加载文件列表... | 正在加载文件列表... |

### Supported input extensions

| Input category | Stored/code values | English | Simplified Chinese |
| --- | --- | --- | --- |
| Images | `.png`, `.jpg`, `.jpeg`, `.jfif`, `.webp`, `.avif`, `.bmp`, `.tiff`, `.tif`, `.heic`, `.heif` | Supported image files | 支持的图片文件 |
| Archives or documents | `.pdf`, `.epub`, `.cbz`, `.cbr`, `.zip` | Supported archives/documents | 支持的压缩包/文档 |

Image extensions come from `manga_translator.image_formats.SUPPORTED_IMAGE_EXTENSIONS`; the archive set is declared separately by the desktop file service and file-catalog snapshot service. Extension matching is case-insensitive. The file dialog’s “All Supported Files” filter contains both sets, but a filter is not a guarantee that the content can be decoded.

## Runtime behavior

### From input sources to the file tree

```mermaid
flowchart TD
    A[Add file/folder/local drop] --> B[Record normalized source path]
    B --> C[Build FileCatalogSnapshot in background]
    C --> D{Extension}
    D -->|Image| E[Create image node and find translation JSON]
    D -->|Archive/document| F[Create archive node]
    C --> G[Recursively scan and naturally sort]
    G --> H[Skip manga_translator_work]
    E --> I[Update file tree and thumbnails]
    F --> I
    I --> J[Rescan and extract archives when task starts]
```

`FileListDataService` builds an immutable snapshot in background workers and uses a generation number to discard stale results. Directories use natural sorting, so `file2` comes before `file10`; duplicate sources are removed using a Windows-friendly normalized path key. Scanning skips directories named `manga_translator_work`, preventing a previous task’s project files from becoming new input.

An image node retains its source image path and any discovered JSON path. The scanner checks `<image-dir>/manga_translator_work/json/<stem>_translations.json` first, then the legacy image-directory location. Therefore the status dot means only that an associated JSON was found at scan time; it does not mean that the current task translated successfully.

### Archive handling when a task starts

The list phase only identifies and displays archives. When a task starts, `FileScannerRunnable` calls the archive extractor, collects images inside the archive, and records a mapping from archive to its temporary extraction directory. When extraction is directed to the output directory, it also checks same-name extraction-directory conflicts and skips or clears them according to the overwrite setting. An archive with no images, an extraction failure, or a stopped task is reported through progress/error callbacks.

The call chain is confirmed statically. The actual relative layout for different archive contents, duplicate names, and output directories still requires a sanitized runtime sample; the page therefore does not promise that sidecar TXT/JSON files automatically pair with paths inside an archive.

### Removal and snapshot updates

Removing a source node does not delete the original image, archive, or translation JSON from disk. When a file or folder is removed, the main logic updates its source list and exclusion sets, then the main window requests a new snapshot; the list view also immediately removes the node from its in-memory model and clears related thumbnail-cache entries. Clearing the list likewise changes only in-memory sources and exclusions and does not clean the user work directory.

## Dependencies and conflicts

- An input path must exist and be readable, and an image extension must belong to the supported set. The legacy `FileService.validate_image_file()` also checks the image MIME type and read permission.
- Recursive folder scans skip `manga_translator_work`; do not treat the project directory as a fresh original-image directory.
- The file list is locked while a task runs; adding, removing, and clearing are rejected. See the next page for stop and task states.
- Large directories use background scanning and asynchronous thumbnail reads. Snapshot and thumbnail loading have cancellation/generation guards so an old scan cannot replace a new list, but disk access and thumbnails still consume CPU, memory, and I/O.
- Archive recognition is not extraction success. The contents, permissions, same-name conflicts, and temporary-directory cleanup for PDF, EPUB, CBZ, CBR, and ZIP should be confirmed with an actual run.

## Related files and formats

This page lists only files and fields actually read or maintained by the input list. It does not expand the complete translation-JSON region format; JSON fields and write-back rules belong on workflow/editor pages.

| File or directory | Role on this page | Caution |
| --- | --- | --- |
| Input images | Tree nodes and later primary inputs | Supported extensions are listed above; image content, OCR text, and coordinates are user data |
| `.pdf` / `.epub` / `.cbz` / `.cbr` / `.zip` | List nodes and extraction inputs when a task starts | Do not show user archive contents in documentation; sidecar pairing after extraction is not runtime-verified |
| `manga_translator_work/` | Explicitly excluded during scans | It is not treated as input by list scanning; do not manually publish its artifacts |
| `manga_translator_work/json/<stem>_translations.json` | Associated-file probe used for the “Translated” status | New location has priority and the legacy same-directory file is supported; it may contain text, masks, and overlays |
| `translation_map.json` | Mapping from translated images to originals during snapshot scanning | Record only its structure and purpose, never user paths or contents |

Names and paths displayed in the list come from the local filesystem. Documentation, screenshots, logs, and debug artifacts must not contain real API keys, tokens, usernames, private absolute paths, user images, OCR/translated text, or private prompts.

## Source evidence

| Layer | File | Verified content |
| --- | --- | --- |
| Page UI | `desktop_qt_ui/ui/main_page/pages/translation_page.py:17-124` | Input card, three buttons, file tree, drop signal, and removal signal bindings |
| Main-window wiring | `desktop_qt_ui/ui/main_window.py:391-412, 427-482` | Snapshot loading/ready/error signals and main-list/MainAppLogic connections |
| List view | `desktop_qt_ui/ui/widgets/file_list_view.py:423-869` | Empty/loading/ready/error states, tree model, thumbnails, status dots, single-item removal, and local-URL drops |
| Snapshot scanner | `desktop_qt_ui/services/file_list_data_service.py:18-414` | Supported extensions, recursive tree, natural sort, deduplication, work-directory exclusion, JSON probe, and generation |
| Input service | `desktop_qt_ui/services/file_service.py:25-322` | Image/archive validation, recursive folder discovery, natural sorting, and drop-path processing |
| Input coordinator | `desktop_qt_ui/app_logic.py:1513-1712` | Add/override/deduplicate, folder selection, removal, clearing, and task-running lock |
| Task scanner | `desktop_qt_ui/app_logic.py:1715-1792, 3600-3740` | Scan state, empty-list warning, archive extraction, conflict handling, and result handoff |
| i18n | `desktop_qt_ui/locales/en_US.json:159-161, 481-487, 1245-1246`; `desktop_qt_ui/locales/zh_CN.json:159-161, 479-485, 1244` | Actual English and Simplified Chinese values for this page’s UI keys |
| Format definition | `manga_translator/image_formats.py:6-31` | Canonical image extensions and file-dialog filter |

## Verification

| Check | Status | Notes |
| --- | --- | --- |
| Page boundary and source evidence | Complete | Checked the blueprint S02 scope and the UI, scanner, and task code above |
| Three-column i18n evidence | Complete | Checked `en_US.json` and `zh_CN.json`; the hard-coded Chinese loading fallback is recorded truthfully |
| Bilingual structure mirror | Complete | Chinese and English retain the same sections, subsections, and Mermaid structure |
| Runtime folder/archive verification | Pending runtime | Research explicitly lists this as unresolved; conditional behavior is not presented as a runtime guarantee |
| Screenshots | Deferred to visual task | No screenshots are fabricated, and no user images/configuration are read |
| Sensitive-information review | Complete | No keys, tokens, usernames, private paths, user content, or private prompts were included |
