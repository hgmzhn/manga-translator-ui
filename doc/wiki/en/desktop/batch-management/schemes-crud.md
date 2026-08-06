---
title: Batch Scheme Management
description: Manage batch-edit schemes: view, create, duplicate, rename, delete, and autosave
pageId: desktop.batch-management.schemes-crud
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Batch Scheme Management

When you want to save a batch-edit setup—which regions to filter and what to do with them—for reuse across sessions, store it as a batch scheme. A scheme consists of a name, match conditions (`match`), and actions (`actions`). This page covers only the scheme list and the create, duplicate, rename, and delete operations, plus autosave and persistence. Configuring match conditions is covered in [Match conditions](./conditions.md), action types and their fixed execution order in [Actions and order](./actions-and-order.md), and hit preview, write-back, and restore from backup in [Preview, apply, and restore](./preview-apply-restore.md).

## Feature boundary {#feature-boundary}

- A scheme = name + `match` (`logic` + `conditions`) + `actions`. Schemes are stored only in `config/batch_edit_schemes.yaml`; they are not written to `config/config.json` and never enter the rendering or translation pipeline.
- The “New / Rename / Duplicate / Delete” buttons on the scheme bar manage the scheme itself only. Condition rows, the logic combo, and the three action cards belong to sibling pages; this page only explains how they are saved as part of a scheme.
- The batch-management scope follows the main file list: the panel footer shows “Scope: {count} translated files from the main file list”. The file list itself is managed on the [File list and input](../translation/file-list-and-input.md) page.
- Schemes contain no keys or private user data; scheme names, condition values, and action fields may contain business text, so sanitize them before sharing reports.

## UI operations {#ui-operations}

### View and switch schemes

1. Open “Batch Management” (`Batch Management`) from the left navigation. The page title is “Batch Management” and the subtitle is “Match regions across the main file list and edit their text, styling, and properties in bulk”.
2. At the top, the scheme bar shows the “Scheme:” (`Scheme:`) combo on the left, listing all schemes, and four buttons on the right: “New” (`New`), “Rename” (`Rename`), “Duplicate” (`Duplicate`), and “Delete” (`Delete`).
3. Selecting a scheme loads its content into the “Match conditions”, “Batch actions”, and preview areas below; editing conditions and actions belongs to the sibling pages.
4. When you switch schemes, if the current scheme has unsaved changes, the app first stops the debounce timer and saves the current scheme, then loads the newly selected scheme.
5. When you return to this page, the panel reloads the scheme list from disk and selects the first item, unless a save is pending; a pending autosave skips the reload so in-memory edits are not clobbered.

### Create a scheme

1. Click “New” (`New`). A text input dialog opens with the title “New scheme” (`New scheme`), the field label “Scheme name” (`Scheme name`), and the buttons “OK” (`OK`) / “Cancel” (`Cancel`).
2. Type a name and press Enter or click “OK”. Leading and trailing whitespace is stripped; an empty name behaves like Cancel and creates nothing.
3. A name that collides with an existing scheme shows the warning “A scheme named '{name}' already exists.” and aborts the creation; choose another name.
4. A new scheme has no match conditions or actions; add them in [Match conditions](./conditions.md) and [Actions and order](./actions-and-order.md) first. Any save writes the file automatically.

### Duplicate a scheme

1. Select the scheme to copy and click “Duplicate” (`Duplicate`).
2. The input dialog defaults to the name “`<original scheme name> 2`”; the field label is still “Scheme name”.
3. On confirmation, the app deep-copies the current scheme's `match` and `actions` into the new scheme, replacing only the name, then switches to the new scheme and saves.
4. Duplicate follows the same empty-name and collision rules; after repeated duplication the default name may already exist, so rename it manually.

### Rename a scheme

1. Select a scheme and click “Rename” (`Rename`). The input dialog defaults to the current name.
2. On confirmation, only the name changes; conditions and actions stay untouched, then the app saves.
3. A colliding name shows the same “A scheme named '{name}' already exists.” warning and aborts.

### Delete a scheme

1. Select a scheme and click “Delete” (`Delete`).
2. A confirmation dialog asks “Delete scheme '{name}'?” (`Delete scheme '{name}'?`); the default button is “No”, and only clicking “Yes” proceeds.
3. Deletion stops any pending autosave first; if the list becomes empty, the panel automatically creates a default “New scheme” (`New scheme`).
4. Deleting a scheme only affects `config/batch_edit_schemes.yaml`; it never deletes or modifies per-image JSON, backups, or translation results.

### Autosave and status messages

- Changing conditions, actions, or logic starts a 600 ms debounce timer; nothing is written during the wait. When the timer fires, `save_schemes()` rewrites the whole list and the status bar shows “Saved automatically” (`Saved automatically`).
- If writing fails with an `OSError`, the status bar shows “Save error” (`Save error`) followed by the error message; no dialog is shown.
- When you switch schemes, unsaved changes are written first; after a confirmed delete, unsaved changes are discarded.
- When the app closes, `shutdown()` stops the timer, flushes pending changes, and then shuts down the background service.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Batch Management` | Batch Management | 批量管理 |
| `Match regions across the main file list and edit their text, styling, and properties in bulk` | Match regions across the main file list and edit their text, styling, and properties in bulk | 跨主页文件列表匹配区域，批量修改文字、富文本样式与属性 |
| `Scheme:` | Scheme: | 方案: |
| `New` | New | 新建 |
| `Rename` | Rename | 重命名 |
| `Duplicate` | Duplicate | 复制 |
| `Delete` | Delete | 删除 |
| `New scheme` | New scheme | 新方案 |
| `Scheme name` | Scheme name | 方案名称 |
| `OK` | OK | 确定 |
| `Cancel` | Cancel | 取消 |
| `Delete scheme '{name}'?` | Delete scheme '{name}'? | 删除方案“{name}”？ |
| `A scheme named '{name}' already exists.` | A scheme named '{name}' already exists. | 已存在名为“{name}”的方案。 |
| `Saved automatically` | Saved automatically | 已自动保存 |
| `Save error` | Save error | 保存失败 |
| `Scope: {count} translated files from the main file list` | Scope: {count} translated files from the main file list | 范围：主页文件列表中的 {count} 个已翻译文件 |

## Empty and error states {#empty-and-error-states}

| Trigger | UI behavior | What happens next |
| --- | --- | --- |
| `config/batch_edit_schemes.yaml` does not exist | First access creates the file from the built-in example; the combo shows the example scheme | Use it directly, or rename/delete it to get the default scheme |
| File is corrupted or YAML parsing fails | `load_schemes()` returns an empty list and the combo temporarily shows a “New scheme” | Any save overwrites the file with the whole in-memory list |
| File exists but contains no valid scheme entries | Same as above: temporary “New scheme” | Same as above |
| Entered name is empty or whitespace only | Create/duplicate/rename simply cancels without a prompt | No write happens |
| Name collides with an existing scheme | Warning “A scheme named '{name}' already exists.” | The operation aborts; choose another name |
| Save fails (I/O error) | Status bar shows “Save error: {error}” | The in-memory list is kept and can be saved again |

Error messages may contain local paths; sanitize them before copying into public reports.

## Runtime behavior {#runtime-behavior}

Scheme loading, editing, and saving share one data flow:

```mermaid
flowchart TD
    A["Open Batch Management\n_load_schemes()"] --> B{"config/batch_edit_schemes.yaml exists?"}
    B -->|no| C["ensure_schemes_exists writes the built-in example"]
    B -->|yes| D["load_schemes() normalizes entries"]
    C --> E["Combo is filled with scheme names"]
    D --> E
    E --> F{"User action"}
    F -->|New| G["Enter a name: empty cancels / duplicate warns"]
    F -->|Duplicate| H["Default \"<original> 2\", deep-copies match and actions"]
    F -->|Rename| I["Only the name changes; conditions and actions stay"]
    F -->|Delete| J["Removed after \"Yes\"; empty list rebuilds a default \"New scheme\""]
    F -->|Edit conditions or actions| K["Whole list saved after a 600ms debounce"]
    G --> L["save_schemes() writes YAML back"]
    H --> L
    I --> L
    J --> L
    K --> L
    L --> M["Status bar: \"Saved automatically\""]
```

- Loading: `load_schemes()` first calls `ensure_schemes_exists()` to lazily create the file, then parses with `yaml.safe_load`; entries pass through `normalize_scheme()`, which drops empty names, drops invalid actions, and stably sorts `actions` as `set_fields -> replace_text -> rich_text`.
- Collecting: `_collect_scheme()` gathers data from the logic combo, condition rows, and the three action cards; `enabled` is always written as `True`; the result is normalized again.
- Writing: `save_schemes()` serializes the whole list with `yaml.safe_dump(allow_unicode=True, sort_keys=False, width=120)` and writes UTF-8 with LF line endings; every save is a full overwrite and does not depend on prior file content.
- Debounce: `_AUTOSAVE_DELAY_MS = 600`. `_mark_dirty()` starts a single-shot timer and clears the previous preview when conditions or actions change; `_save_current_scheme()` emits the `data_changed` signal after a successful save (static check found no connected consumer in the current source; it is reserved for external integration).
- The `enabled` field is preserved on read, but the current UI always writes `True` and the batch engine does not filter schemes by it; do not rely on it as an enable/disable switch.

## Dependencies and conflicts {#dependencies-and-conflicts}

- The scheme file serves the desktop batch-management page only; `batch_edit_schemes.py` deliberately does not register in `manga_translator/runtime_files.py` bootstrapping, so schemes never enter the rendering or translation pipeline.
- Schemes are not written to `config/config.json` or any configuration model; they are not Settings-page parameters.
- Switching, duplicating, renaming, or deleting schemes never modifies per-image JSON, `.bak` backups, or editor memory; editor conflicts and write-back timing are covered in [Preview, apply, and restore](./preview-apply-restore.md).
- Once conditions or actions change, the previous hit preview is invalidated immediately (the table is cleared and the apply button disabled) because the on-disk results are no longer trustworthy; see [Preview, apply, and restore](./preview-apply-restore.md) for preview and execution details.
- Scheme names appear in the combo and may contain business text; check scheme names, condition values, and action content before sharing screenshots or logs.

## Related files and formats {#files-and-formats}

| File/format | Actual role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `config/batch_edit_schemes.yaml` | Scheme persistence file: a top-level `schemes` list, each item with `name`, `enabled`, `comment`, `match`, `actions` | Read/written with `yaml.safe_load`/`safe_dump`; keep the YAML parseable when editing by hand, as invalid entries are dropped on the next save |
| `config/` | Runtime external configuration directory, resolved by `get_config_path()` | In development it is the repository `config/`; in frozen builds it is `config/` next to the executable; never commit real private paths |
| `*_translations.json`, `<json-file>.bak` | Per-image translations and backups, managed by preview/write-back/restore | This page's CRUD never touches them; see [Preview, apply, and restore](./preview-apply-restore.md) |
| `config/config.json` | Settings-page parameter persistence | Schemes are not written to this file and are not managed by any config model |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Page container | `desktop_qt_ui/ui/main_page/pages/batch_edit_page.py`, `desktop_qt_ui/ui/main_page/layout.py` | Page title/subtitle, panel embedding, and refresh/snapshot sync on page activation |
| Scheme panel | `desktop_qt_ui/ui/secondary_pages/batch_edit_panel.py` | Scheme bar, create/rename/duplicate/delete, name validation, debounced autosave, status bar, `data_changed` |
| Persistence | `desktop_qt_ui/services/batch_edit_schemes.py` | YAML structure, lazy creation, normalization, `save_schemes()` write-back |
| Runtime paths | `manga_translator/runtime_paths.py` | `get_config_path()` decides where `batch_edit_schemes.yaml` actually lives |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Key mapping and actual bilingual display values in the table above |
| File-list scope | `desktop_qt_ui/ui/main_window.py`, `desktop_qt_ui/ui/main_page/pages/translation_page.py` | Main file-catalog snapshot push and refresh |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract |
| UI layout and calls | Complete | Statically checked the batch-management page, scheme panel, and name input dialog |
| `en_US` / `zh_CN` actual locales | Complete | The table records key, actual English, and actual Simplified Chinese values |
| Scheme CRUD and persistence chain | Complete | Statically checked loading, normalization, debounced save, delete-and-rebuild, and shutdown flush |
| Sanitized runtime verification | Deferred | No real `.env`, user `config.json`, API key/token, username, user image, or private prompt was read |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |
