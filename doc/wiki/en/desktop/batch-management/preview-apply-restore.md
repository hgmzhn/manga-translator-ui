---
title: Batch Preview, Apply, and Restore
description: Preview the regions a scheme matches, write it back to per-image JSON after selection, and restore from .bak backups
pageId: desktop.batch-management.preview-apply-restore
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Batch Preview, Apply, and Restore

The Batch Management page works on the translated files in the main file list. You first run “Preview matches” (`Preview matches`), review every region that would be changed in the table, tick the rows to write back, and finally use “Apply to selected” (`Apply to selected`). Before writing, the app copies each JSON to a `.bak` by default, so most mistakes can be undone with “Restore from backup” (`Restore from backup`).

This page covers preview, selection, write-back, backup, and restore only. Scheme create/rename/duplicate/delete and autosave are in [Batch scheme management](./schemes-crud.md), condition fields and the `all`/`any` logic are in [Match conditions](./conditions.md), and the three action types with their fixed order are in [Actions and order](./actions-and-order.md). The editor's own save and write-back is documented in [Editor import, export, and write-back](../editor/import-export-and-writeback.md).

## Feature boundary {#feature-boundary}

- Batch write-back targets per-image JSON (`<stem>_translations.json`), not final images; Batch Management never enters the rendering pipeline.
- Preview is mandatory: there is no “apply without previewing” button.
- The scope comes from the `json_by_file` mapping (image path → JSON path) in the main file list snapshot. The panel does not rescan the disk itself; the main window pushes a new snapshot whenever the file list changes.
- “Apply to selected” always re-reads the files from disk and re-runs the scheme; it never reuses cached preview results.
- Restore can only roll a file back to its sibling `.bak`; the `.bak` is consumed in the process and no longer exists afterwards.
- The “Match conditions” card and the “Batch actions” card live on the same panel, but their details are covered by [Match conditions](./conditions.md) and [Actions and order](./actions-and-order.md) respectively.

## UI operations {#ui-operations}

### Check the scope and preview matches {#preview-matches}

Open “Batch Management” (`Batch Management`). The status bar at the bottom of the page shows “Scope: {count} translated files from the main file list” on the right.

After configuring conditions and actions:

1. Click “Preview matches” (`Preview matches`).
2. If the main file list has no translated files yet, you see “The main file list has no translated files yet. Add files on the translation page first.”.
3. If no batch action is enabled, you see “Enable at least one batch action first.”.
4. The scan runs in a progress dialog labeled “Scanning...” (`Scanning...`); it can be cancelled.
5. Matches are written into the preview table, one row per region, with six columns: check, image, region, before, after, changes.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Batch Management` | Batch Management | 批量管理 |
| `Preview matches` | Preview matches | 预览命中 |
| `Select All` | Select All | 全部选中 |
| `Select None` | Select None | 全不选 |
| `Image` | Image | 图片 |
| `Region` | Region | 区域 |
| `Before` | Before | 原文 |
| `After` | After | 改后 |
| `Changes` | Changes | 变更 |
| `Scope: {count} translated files from the main file list` | Scope: {count} translated files from the main file list | 范围：主页文件列表中的 {count} 个已翻译文件 |
| `{regions} regions in {files} files` | {regions} regions in {files} files | {files} 个文件中的 {regions} 个区域 |
| `Scanned {regions} regions in {files} files` | Scanned {regions} regions in {files} files | 已扫描 {files} 个文件中的 {regions} 个区域 |
| `{count} malformed regions skipped` | {count} malformed regions skipped | 跳过 {count} 个结构异常的区域 |
| `{count} files could not be read` | {count} files could not be read | {count} 个文件读取失败 |
| `Back up each file before writing` | Back up each file before writing | 写入前备份每个文件 |
| `Writes a .bak next to each modified JSON` | Writes a .bak next to each modified JSON | 在每个被改动的 JSON 旁写一个 .bak |
| `Apply to selected` | Apply to selected | 对选中项执行 |
| `Restore from backup` | Restore from backup | 从备份恢复 |
| `Roll every file in scope back to its .bak, then delete the .bak` | Roll every file in scope back to its .bak, then delete the .bak | 把范围内每个文件还原成它的 .bak，还原后 .bak 就消耗掉 |

- The “Image” column shows the file name; hovering shows the full JSON path. “Region” shows the region index. “Before”/“After” show the visible translation text (line breaks are displayed as `\n`). “Changes” lists the fields that would be modified (for example `line_spacing, translation`).
- “Select All” (`Select All`) and “Select None” (`Select None`) toggle the check state of every row at once.
- With no matches, “Apply to selected” (`Apply to selected`) stays disabled; with matches, the summary shows “{regions} regions in {files} files”.
- Regions with an invalid structure are skipped and the status line appends “{count} malformed regions skipped”; unreadable files open an error dialog titled “{count} files could not be read”.
- As soon as a condition or action changes, the previous preview is invalidated (the table is cleared and Apply is disabled again); you must preview again.

### Apply changes in bulk {#apply}

1. Tick the rows you want to write back in the preview table.
2. “Back up each file before writing” (`Back up each file before writing`) is checked by default; when you uncheck it, the confirmation dialog appends “Backups are disabled. This cannot be undone.”.
3. Click “Apply to selected” (`Apply to selected`); the confirmation asks “Apply this scheme to {regions} regions in {files} files?”.
4. If the editor currently has an image from the target scope open, the confirmation appends a notice that the in-memory copy will overwrite these changes when switching images and that the editor will be reloaded after applying.
5. The progress dialog is titled “Apply to selected” and shows “Writing...” (`Writing...`); it can be cancelled. After cancellation the status line shows “Cancelled” (`Cancelled`) and nothing is written.
6. On completion the status line shows “Updated {regions} regions in {files} files”; files that could not be written are listed in an error dialog titled “{count} files could not be written”.
7. After a successful write-back the preview table is cleared — the on-disk content has changed, so the old preview is no longer trustworthy.

### Restore from backup {#restore}

1. Click “Restore from backup” (`Restore from backup`); its tooltip reads “Roll every file in scope back to its .bak, then delete the .bak”.
2. If no file in scope has a `.bak`, you see “No backup found for the files in scope.”.
3. When backups exist, the confirmation asks “Roll {files} files back to their backup? The backup is consumed.”; if the editor has a target image open, the reload notice is appended as well.
4. The progress dialog shows “Restoring...” (`Restoring...`); it can be cancelled.
5. On completion the status line shows “Restored {files} files”; files that could not be written are listed in the “{count} files could not be written” error dialog.

Restore also uses the “translated files from the main file list” scope: it only handles the sibling `.bak` files of those JSON files, never scans the whole disk, and never creates new backups.

## Options and statuses {#options-and-statuses}

#### “Back up each file before writing” {#backup-option}

- Control: checkbox, checked by default.
- Location: header of the preview card on the Batch Management page; the “Apply to selected” confirmation appends a warning based on it.
- Stored value: not persisted; it only affects whether the current apply run (`request_apply(..., backup=...)`) copies the original file to `<json>.bak` before writing.
- Options: checked / unchecked.
- Default: checked at UI initialization (`setChecked(True)`).
- Effective stage: the apply (write-back) stage, before writing to disk.
- Mechanism: when checked, `write_json_document` first copies the original JSON to `<json>.bak` with `shutil.copy2`, then writes atomically through a temp file in the same directory plus `os.replace`; restore rolls the backup back with `os.replace(.bak, json)` and consumes it. When unchecked there is no `.bak`, so “Restore from backup” has no data for those files.
- Dependencies/conflicts: the restore button depends on whether a `.bak` exists; unchecking means giving up undo capability.
- Related files and debug artifacts: `<json>.bak`.
- Diagram: see the on/off comparison below.

```mermaid
flowchart LR
    subgraph On["“Back up each file before writing” checked (default)"]
        A1["Write back JSON"] --> A2["Copy JSON.bak first"]
        A2 --> A3["Restore from backup works"]
    end
    subgraph Off["Unchecked"]
        B1["Write back JSON"] --> B2["No .bak created"]
        B2 --> B3["Cannot undo; restore has no data"]
    end
```

Unchecking only affects the next click on “Apply to selected”: it does not delete leftover `.bak` files, and it does not stop “Restore from backup” from using backups that already exist.

#### Statuses and messages {#status-and-messages}

| Scenario | UI call key (English actual value) | Simplified Chinese actual value |
| --- | --- | --- |
| Preview finished | Scanned {regions} regions in {files} files | 已扫描 {files} 个文件中的 {regions} 个区域 |
| Preview skipped malformed regions | {count} malformed regions skipped | 跳过 {count} 个结构异常的区域 |
| Preview read failure | {count} files could not be read | {count} 个文件读取失败 |
| Apply finished | Updated {regions} regions in {files} files | 已更新 {files} 个文件中的 {regions} 个区域 |
| Apply/restore write failure | {count} files could not be written | {count} 个文件写入失败 |
| Restore finished | Restored {files} files | 已恢复 {files} 个文件 |
| Cancelled | Cancelled | 已取消 |
| Background error | Error | 错误 |

## Runtime behavior {#runtime-behavior}

### Preview scanning {#scan-mechanism}

Clicking “Preview matches” hands the current scheme (conditions + actions) and the JSON paths in scope to a background thread. For each JSON the thread: reads and parses it (keeping the detected indentation) → walks the `regions` under every top-level image entry → skips regions with an invalid structure → evaluates the conditions per region → applies the scheme to a copy of the region. When the trial result differs from the original, one match row is produced.

```mermaid
flowchart TD
    Start["Click Preview matches"] --> CheckFiles{"Any translated JSON in scope?"}
    CheckFiles -->|No| Warn1["Prompt: add files on the translation page first"]
    CheckFiles -->|Yes| CheckActions{"At least one batch action enabled?"}
    CheckActions -->|No| Warn2["Prompt: enable at least one batch action first"]
    CheckActions -->|Yes| Scan["Background scan: read JSON → walk regions → evaluate conditions → trial-run actions"]
    Scan --> Result{"Any match?"}
    Result -->|No| Disable["Apply to selected stays disabled"]
    Result -->|Yes| Table["Preview table + summary {regions} regions in {files} files"]
```

The “Before”/“After” columns show the visible translation text: rich text wins when present, otherwise it falls back to `translation`; line breaks are displayed as `\n`. Preview only computes; it never writes to disk.

### Applying changes {#apply-mechanism}

“Apply to selected” carries the rows the user ticked (JSON path + image key + region index), not the whole match list. During execution the engine re-reads the files and re-runs sanity checks, condition evaluation, and the scheme for every target region, then:

1. Only the region entries that actually changed are replaced; the other top-level keys (`mask_raw`, `mask_is_refined`, overlays, dimensions, etc.) and unmatched regions are preserved as-is.
2. When backup is checked, `<json>.bak` is created first with `shutil.copy2`.
3. A temp file (`.batch_edit_*.tmp`) is written in the same directory, `fsync`ed, and `os.replace` swaps it in atomically.
4. The original indentation is preserved (the backend writes 4, the editor writes 2) and `ensure_ascii=False`, keeping diffs minimal.
5. Only files with actual changes are written; files without changes are neither modified nor backed up.

```mermaid
flowchart TD
    Apply["Click Apply to selected"] --> Confirm{"Confirmation accepted?"}
    Confirm -->|No| NoWrite["Nothing is written"]
    Confirm -->|Yes| ReRead["Re-read from disk (no preview cache)"]
    ReRead --> Loop["Per file: recompute conditions → trial-run actions → replace changed regions"]
    Loop --> Backup{"Back up each file before writing checked?"}
    Backup -->|Yes| Bak["Create JSON.bak"]
    Backup -->|No| NoBak["No backup created"]
    Bak --> Write["Temp file + os.replace atomic write-back"]
    NoBak --> Write
    Write --> Editor{"Editor has a target image open?"}
    Editor -->|Yes| Reload["Append notice to confirmation; reload editor after apply"]
    Editor -->|No| Done["Status: Updated {regions} regions in {files} files"]
    Reload --> Done
```

Write-back only modifies text/style entries in the JSON; it does not re-render images automatically. Re-export from the translation or editor workflow when you need new images.

### Restoring {#restore-mechanism}

“Restore from backup” runs `os.replace(backup, json_path)` for every JSON in scope that has a `.bak`: this changes a directory entry instead of moving data, so it is faster than byte-by-byte copying and is itself atomic. After a successful restore the `.bak` is consumed (it no longer exists), which is why the button tooltip says “then delete the .bak”. Files without a `.bak` are skipped; if none exist you see “No backup found for the files in scope.”.

```mermaid
flowchart TD
    Restore["Click Restore from backup"] --> Has{"Any .bak in scope?"}
    Has -->|No| Warn["No backup found for the files in scope."]
    Has -->|Yes| Confirm2{"Confirm: roll {files} files back to their backup?"}
    Confirm2 -->|No| NoOp["Nothing is written"]
    Confirm2 -->|Yes| Replace["Per file os.replace(.bak, JSON)<br/>atomic replace, .bak consumed"]
    Replace --> Editor2{"Editor has a target image open?"}
    Editor2 -->|Yes| Reload2["Reload editor automatically"]
    Editor2 -->|No| Done2["Status: Restored {files} files"]
    Reload2 --> Done2
```

### Cancellation and background execution {#cancel-and-threading}

Preview, apply, and restore each occupy a background channel (`scan` / `apply` / `restore`) run by a `ThreadPoolExecutor` (at most 2 worker threads) so the UI thread is never blocked. Each channel uses a generation counter: only the latest request's result is accepted, older results are discarded. On cancellation:

1. The channel's cancel event is set and its generation is incremented.
2. The engine checks the cancel event at the next file/region boundary and raises a cancellation exception.
3. Result signals from the cancelled run no longer reach the UI; the panel sets the status line to “Cancelled”.

The progress dialog is a closable modal dialog: `setRange(0, total)` decides between a determinate and an indeterminate bar, and the close button is equivalent to cancel.

### Editor conflicts and reload {#editor-conflict}

The editor keeps regions in memory and does not listen for file changes; when you switch images its auto-export overwrites the on-disk JSON from the stale in-memory copy. So when the apply/restore target includes the image currently open in the editor, the panel appends the notice “The editor currently has '{name}' open.” to the confirmation, and after success it calls the editor reload entry point to load that image again, so the in-memory copy cannot wipe out the changes just written.

## Dependencies and conflicts {#dependencies-and-conflicts}

- The scope comes entirely from the main file list snapshot: JSON files outside the file list are never previewed, applied, or restored; the panel does not scan the disk itself.
- Preview again before applying: changing a condition or action invalidates the old preview.
- If you uncheck “Back up each file before writing” when applying, those files have no `.bak`, so a later “Restore from backup” cannot roll them back.
- Restore only works on the `.bak` files in scope: it cannot restore files that were never backed up, and it cannot roll back to an earlier version (there is only one `.bak` and it is consumed).
- Write-back only replaces region entries in the JSON; masks, overlays, and dimensions are preserved, but if the editor is not reloaded after the write, its auto-export on image switch can still overwrite the whole JSON.
- Batch write-back is unrelated to the rendering pipeline: changes do not re-render images automatically.

## Related files and formats {#related-files-and-formats}

| File/format | Actual role on this page | Note |
| --- | --- | --- |
| `<image-dir>/manga_translator_work/json/<stem>_translations.json` | The target file for batch preview, write-back, and restore | The top-level key is the image absolute path; the value holds `regions` plus `mask_raw`/overlays, etc. Reading prefers the new location and falls back to the legacy `*_translations.json` next to the image |
| `<json>.bak` | Per-file backup created before apply; consumed on restore | Lives next to the matching JSON; disappears after restore; this documentation never shows real paths |
| `config/batch_edit_schemes.yaml` | Stores the current scheme (conditions + actions) | Read/written by `desktop_qt_ui/services/batch_edit_schemes.py`; never enters the rendering pipeline |
| `result/` debug artifacts | Unrelated to batch write-back | Batch edits neither create nor update debug images |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| UI | `desktop_qt_ui/ui/main_page/pages/batch_edit_page.py`, `desktop_qt_ui/ui/secondary_pages/batch_edit_panel.py` | Page title/subtitle, six preview columns, backup checkbox, apply/restore confirmations and progress, status line and error dialogs |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json`, `doc/wiki/data/i18n.generated.json` | English and Simplified Chinese actual values for the preview/apply/restore keys |
| Background scheduling | `desktop_qt_ui/services/batch_edit_service.py` | scan/apply/restore channels, generation, cancel events, progress signals |
| Engine | `desktop_qt_ui/services/batch_edit_engine.py` | Scanning, re-read-then-apply, backup and atomic write in `write_json_document`, `os.replace` restore in `restore_files` |
| Scope wiring | `desktop_qt_ui/ui/main_window.py` | Pushing file-list snapshots into `set_catalog_snapshot`, editor context via `set_editor_context` |
| JSON paths | `manga_translator/utils/path_manager.py` | New/legacy `*_translations.json` resolution and compatibility |
| Scheme persistence | `desktop_qt_ui/services/batch_edit_schemes.py` | `config/batch_edit_schemes.yaml` structure and read/write |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read the relevant items in 1.3, 5.9, and 6.3 and followed the page contract |
| UI layout and calls | Complete | Statically checked the batch panel preview table, backup checkbox, apply/restore flows, and editor-conflict notice |
| `en_US` / `zh_CN` actual locales | Complete | The tables record key, actual English, and actual Simplified Chinese values |
| `.bak` and write-back/restore chain | Complete | Statically checked backup, atomic write, and `os.replace` restore in `batch_edit_engine.py` |
| Sanitized runtime verification | Deferred | GUI not launched; real progress, cancel boundaries, `.bak` behavior, and editor-conflict reload need a headed sanitized run |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |



