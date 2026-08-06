---
title: Floating Rich Text Editor
description: Edit rich-text styling of a region in a floating window beside the canvas, and understand its automatic save and write-back behavior
pageId: desktop.editor.floating-rich-text
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Floating Rich Text Editor

Use the floating rich-text editor when a line of translation needs inline styling such as bold, color, ruby, or vertical-in-horizontal (TCY). It is an independent window docked next to the currently selected text region on the canvas and provides body editing, per-run style cards, ruby input, and saved style presets. This page covers opening, editing, and saving with the floating window; the parameter details and rendering effects of each style are covered in [Style properties](./style-properties.md), plain text fields, find/replace, and the region list are covered in [Region list and text editing](./region-list-and-text-editing.md), rich-text rule matching and preset files are covered in [Rich text styles and presets](../rich-text-rules/styles-and-presets.md), and shortcuts and focus conflicts are covered in [Shortcuts](./shortcuts.md).

## Feature boundary {#feature-boundary}

- The floating rich-text editor is a top-level tool window created by `EditorView` (`Qt.Tool` plus frameless), not a child overlay of the canvas, so it is never clipped by the canvas viewport and can be dragged onto other panels or monitors.
- It is shown only when exactly one region is selected and the menu toggle “Show Rich Text Editor Popup” (`Show Rich Text Editor Popup`) is enabled; multi-selection, clicking blank canvas, starting to drag a region, or turning the toggle off hides it and clears its binding.
- It edits three fields of the current region: `translation` (final translation), `translation_raw` (pre-replacement translation), and `translation_rich` (a `richtext.v1` document). The document has no separate “Save” button: body and style changes are written back to the model automatically after a 180 ms debounce, and flushed immediately on hide, close, region switch, or body focus loss.
- “Auto Apply Rich Text Rules While Editing” (`Auto Apply Rich Text Rules While Editing`) in the top “Menu” controls whether rich-text rules are applied incrementally while typing (on by default; styles only, characters never change).
- Not covered here: region-level style parameters (font size, color, stroke, spacing, angle, alignment, direction) are in [Style properties](./style-properties.md); plain text editing, find/replace, and list sync in the property panel are in [Region list and text editing](./region-list-and-text-editing.md); canvas tools and selection are in [Canvas tools and selection](./canvas-tools-and-selection.md); shortcut registration and focus priority are in [Shortcuts](./shortcuts.md).

## UI operations {#ui-operations}

### Open, position, and close the floating editor {#open-position-close}

1. Open the editor, load an image, and select one text region on the canvas with the selection tool. The floating window appears automatically above that text box (below it when there is not enough room above), horizontally centered on the text box, and clamped to the available geometry of the current screen.
2. Move the mouse to the window border (about 12 px) and hold the left button to drag the whole window elsewhere. Once dragged manually, the automatic docking position is remembered, and later canvas scrolling, zooming, or style-area height changes no longer move it.
3. Showing the window never steals canvas focus (`WA_ShowWithoutActivating`): `focus_text()` is not called when a region is selected, so canvas shortcuts such as `Delete` and `A`/`D` image switching keep their canvas semantics until you click the body.
4. The window hides on deselect or multi-selection, blank-canvas click, starting to drag the current region, or turning off “Show Rich Text Editor Popup”. Pending changes are flushed before hiding (see [Edit-to-save write-back flow](#edit-save-flow)).
5. After a region drag ends, the window re-docks around the text box’s new position and reappears; switching away from the editor page and back also restores a visible window when needed.
6. Turning off “Show Rich Text Editor Popup” flushes, unbinds, and hides the window; turning it back on rebinds immediately from the current model selection.

### Edit the translation body {#edit-translation-body}

1. The body is a plain-text edit box (14 pt, 120 px high) showing the current region’s translation text. On load, `[BR]`, `<br>`, `【BR】`, and real newlines are normalized to line breaks; on save, line breaks are merged back into `[BR]` for `translation`.
2. Type, delete, or paste to modify the body. Each change is committed automatically after a 180 ms debounce, and immediately on hide, close, region switch, or body focus loss.
3. Qt undo/redo is enabled on the body; consecutive rich-text write-backs are merged into one undoable step by the controller using `merge_key`.
4. With “Auto Apply Rich Text Rules While Editing” on (default), each edit applies the rich-text rules incrementally to the document: rules only add styles and never change characters, and matched ranges that already carry manual rich-text traces are skipped entirely.

### Apply styles with the toolbar and style cards {#toolbar-and-style-cards}

1. The toolbar is a grid of 8-column toggle buttons whose button text is the style storage key (`B`, `I`, `U`…); hover hints and accessible names show the translated style name.
2. With no text selected, a style button applies to the whole text; with a selection, it applies to the selection only. Clicking the same button again removes that style (`transform` sub-keys are cleared individually so sibling values survive).
3. The style-card area shows one card per contiguous run of identically styled text in the selected range. The card header shows that run’s text and selects it as the edit target when clicked; the header has “Save Style” (`Save Style`) and “Clear all styles from this text” (`Clear all styles from this text`).
4. Each card lists property rows (key label + style name + control + remove button) for the styles the run actually carries; numeric, color, and combo controls edit in place and commit immediately. When only style values change, cards refresh in place instead of being rebuilt, so a control being typed into or clicked is not interrupted.
5. Ruby: click toolbar `R` (or the ruby row inside a card) for the selected text, type the ruby text in the “Ruby text” (`Ruby text`) input, and commit with “Apply” (`Apply`) or Enter; switching selection, losing body focus, or hiding the window also commits an unapplied ruby draft first.
6. TCY: click `T` to wrap the selected text as a `tcy` node; click it again to unwrap.

### Manage rich-text style presets {#manage-style-presets}

1. The right “Rich Text Presets” (`Rich Text Presets`) sidebar lists saved style presets; with none it shows “No saved styles” (`No saved styles`). The sidebar can be collapsed/expanded, switching width between 248 px expanded and 38 px collapsed.
2. Click a preset name to apply it: all styles are cleared from the selection first, then the preset’s style/ruby/tcy are applied.
3. The card header’s “Save Style” (`Save Style`) opens a name prompt for the current run’s style (“Enter style preset name:”, default name “Rich Text Preset N”); the name cannot be empty, and a duplicate asks to confirm “Style preset '{name}' already exists. Overwrite?”.
4. Each sidebar row has rename and delete buttons; deleting asks for confirmation. A config write failure shows the “Failed to save style preset” error box and rolls back the in-memory presets.

## Option matrix {#option-matrix}

The following three-column table records the UI copy used by the floating rich-text editor; the `UI call key` is the raw key passed to `I18nManager.translate()`, and the English and Simplified Chinese values come directly from `desktop_qt_ui/locales/en_US.json` and `zh_CN.json`.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Show Rich Text Editor Popup` | Show Rich Text Editor Popup | 显示富文本编辑弹窗 |
| `Auto Apply Rich Text Rules While Editing` | Auto Apply Rich Text Rules While Editing | 编辑时自动应用富文本规则 |
| `Rich Text Presets` | Rich Text Presets | 富文本预设 |
| `No saved styles` | No saved styles | 暂无已保存样式 |
| `Choose a saved style to apply` | Choose a saved style to apply | 选择一个已保存样式并应用到当前选区 |
| `Rename preset` | Rename preset | 重命名预设 |
| `Delete preset` | Delete preset | 删除预设 |
| `Expand preset sidebar` | Expand preset sidebar | 展开预设侧边栏 |
| `Collapse preset sidebar` | Collapse preset sidebar | 收起预设侧边栏 |
| `Ruby text` | Ruby text | 注音文本 |
| `Apply` | Apply | 应用 |
| `Save Style` | Save Style | 保存样式 |
| `Clear all styles from this text` | Clear all styles from this text | 清除此段文字的全部样式 |
| `Remove this style` | Remove this style | 删除此样式 |
| `Half Advance` | Half Advance | 半格推进 |
| `Full Advance` | Full Advance | 全角推进 |
| `Enter style preset name:` | Enter style preset name: | 输入样式名称： |
| `Rich Text Preset` | Rich Text Preset | 富文本预设 |
| `Save` | Save | 保存 |
| `Rename` | Rename | 重命名 |
| `Cancel` | Cancel | 取消 |
| `Style preset name cannot be empty` | Style preset name cannot be empty | 样式名称不能为空 |
| `Style preset '{name}' already exists. Overwrite?` | Style preset '{name}' already exists. Overwrite? | 样式“{name}”已存在，是否覆盖？ |
| `Rename style preset` | Rename style preset | 重命名样式预设 |
| `Enter a new style preset name:` | Enter a new style preset name: | 输入新的样式名称： |
| `Delete style preset '{name}'?` | Delete style preset '{name}'? | 确定删除样式“{name}”？ |
| `Failed to save style preset` | Failed to save style preset | 保存样式失败 |
| `Error` | Error | 错误 |
| `Warning` | Warning | 警告 |
| `Confirm` | Confirm | 确认 |
| `Select rich text color` | Select rich text color | 选择富文本颜色 |
| `Select stroke color` | Select stroke color | 选择描边颜色 |
| `Select glow color` | Select glow color | 选择发光颜色 |
| `Select outer stroke color` | Select outer stroke color | 选择外描边颜色 |

The toolbar buttons and the style-card property rows share the same 22 style keys; the toolbar button text is the storage key, and the hover hint and property-row name come from the corresponding i18n keys. The table below lists the storage key and the toolbar hint (hover) in three columns:

| Storage key (button text) | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `B` | Bold | 加粗 |
| `I` | Italic Angle | 斜体角度 |
| `U` | Underline | 下划线 |
| `C` | Text Color | 文字颜色 |
| `S` | Font Size | 绝对字号 |
| `%` | Scale | 字号倍率 |
| `F` | Font Family | 字体 |
| `O` | Stroke | 描边 |
| `G` | Glow | 发光 |
| `OS` | Outer Stroke | 外描边 |
| `D` | Emphasis | 着重号 |
| `FA` | Force Advance | 强制推进 |
| `T` | Vertical-in-Horizontal (TCY) | 竖排内横排（纵中横） |
| `R` | Ruby Text | 注音文本 |
| `Rot` | Rotation | 局部旋转 |
| `K` | Kerning | 字后间距 |
| `PK` | Pre Kerning | 字前间距 |
| `LK` | Line Kerning | 与前一行间距 |
| `NK` | Next Kerning | 与后一行间距 |
| `XY` | X / Y Offset | X / Y 偏移 |
| `M` | Mirror Horizontal | 水平镜像 |
| `MV` | Mirror Vertical | 垂直镜像 |

Style-card property rows use the other name-key set (`Bold`, `Italic`, `Underline`, `Text Color`, `Font Size`, `Scale`, `Font`, `Stroke`, `Glow`, `Outer Stroke`, `Emphasis`, `Force Advance`, `TCY`, `Ruby`, `Rotation`, `Kerning`, `Pre Kerning`, `Line Kerning`, `Next Kerning`, `X / Y Offset`, `Mirror Horizontal`, `Mirror Vertical`), where `I`/`F`/`T`/`R` differ from the toolbar hints: `I`=Italic/斜体, `F`=Font/字体, `T`=TCY/纵中横, `R`=Ruby/注音.

## Runtime behavior {#runtime-behavior}

### Edit-to-save write-back flow {#edit-save-flow}

```mermaid
flowchart LR
    A["Canvas single selection"] --> B["RichTextFloatingEditor.set_region"]
    B --> C["Body input / toolbar / style cards / ruby"]
    C --> D["State machine apply_qt_contents_change / replace_document"]
    D -->|"auto rules on"| E["apply_rich_text_rules adds styles only"]
    D -->|"auto rules off"| F["mark_document_emitted after 180ms debounce"]
    E --> F
    F -->|"rich_text_changed(region_index, document, plain_text)"| G["EditorController.update_translation_rich"]
    G --> H["UpdateRegionCommand merge key region:index:translation_rich"]
    H --> I["EditorModel updates translation / translation_raw / translation_rich"]
    I -->|"regions_changed"| J["View _on_regions_changed_for_rich_editor"]
    J -->|"skips editor's own write-back"| K["refresh_region_if_changed refreshes document"]
```

The diagram is the real source-confirmed data flow: body or style changes enter the editor state machine, optionally apply auto rich-text rules, then commit after a 180 ms debounce; the controller writes the whole document to the model and merges consecutive edits by `merge_key`; the model notifies the view, which skips the editor’s own write-back and only refreshes the document from model data. No real user configuration or task artifact is read at any step.

Flush trigger summary:

| Trigger | Behavior |
| --- | --- |
| 180 ms debounce expires after a body change | Commit the pending document (`mark_document_emitted`) |
| Hide/close the window (`hideEvent`/`closeEvent`) | Commit the ruby draft and body debounce first, then unbind |
| Body loses focus (`focus_lost`) | `flush_pending_changes` commits immediately |
| Switch region (new single selection) | Flush the previous region first, then bind the new region data |
| Turn off “Show Rich Text Editor Popup” | `clear_region`: flush first, then unbind and hide |

### Stored fields and document format {#storage-fields-and-format}

| Field | Stored value | Write-back rule |
| --- | --- | --- |
| `translation` | Plain text; line breaks stored as `[BR]` | Synced to the document body when the body changes |
| `translation_raw` | Pre-replacement translation | Synced when the body changes (the pre-replacement text cannot be reliably reconstructed); kept unchanged for style-only edits |
| `translation_rich` | `richtext.v1` document dict | Full document saved on every write-back |

`translation_rich` uses the `{"format": "richtext.v1", "blocks": [...]}` document structure: `blocks` is a list of paragraphs (`type: paragraph`), each containing `text` (`text`+`style`), `ruby` (`base`+`text`), and `tcy` (`content`) inline nodes; `style` holds flat style fields and the `transform` sub-object holds rotation/offsetX/offsetY/mirrorX/mirrorY. Parsing and serialization live only in `manga_translator/rendering/rich_text.py`. On load the editor parses `translation_rich` strictly and falls back to a paragraph document built from the `translation` plain text when the value is invalid or absent, so the editor never crashes.

### Focus and shortcut priority {#focus-and-shortcuts}

- The floating editor is shown with `WA_ShowWithoutActivating`: selecting a region does not steal canvas focus, so canvas shortcuts (`Delete`, `A`/`D` image switching, `Q`/`W`/`E` tool switching) keep their canvas semantics until the body is clicked.
- Once focus enters the floating editor (another top-level `Qt.Tool` window), `EditorShortcutManager` detects that the window of `QApplication.focusWidget()` is no longer the editor main window and returns early for all context-aware editor shortcuts, preventing a stale main-window focus from deleting canvas regions.
- While the body holds focus, Qt text controls handle text undo/redo and copy/paste; style changes are committed through the document and merged by the controller command.

## Dependencies and conflicts {#dependencies-and-conflicts}

- The floating editor only works with a single selection; it is hidden for multi-selection, no selection, or when the popup toggle is off. Selection changes from the region list and property panel drive the floating window through the same model selection.
- “Auto Apply Rich Text Rules While Editing” affects only the incremental style application inside the editor; the rule files, matching, and preview belong to the rich-text-rules pages. Rules add styles only, never change characters, and skip ranges that already carry manual rich-text traces.
- Rich-text write-back shares the same fields as the property panel’s `translation`/`translation_raw` editing: body changes overwrite both `translation` and `translation_raw`, style-only edits keep `translation_raw`; model changes refresh the editor document through `regions_changed` (skipping its own write-back) so a stale document never overwrites the model.
- Consecutive rich-text edits merge into one undo step by `merge_key` (`region:{index}:translation_rich`); the body’s own Qt undo/redo acts on text only.
- Style presets are stored in the application config `app.saved_rich_text_presets`, not in region data; a failed save rolls back and shows “Failed to save style preset”.
- Positioning is screen-aware: automatic docking chooses above/below within the current screen’s available geometry; manual dragging disables auto-movement, and dragging the text box on the canvas re-docks around the new position.

## Related files and formats {#related-files}

| File/format | Actual role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `<image-dir>/manga_translator_work/json/<stem>_translations.json` | Region data persistence: `translation`, `translation_raw`, `translation_rich` | Editor changes are saved through export/write-back; no real user path or image is shown |
| `config/config.json` | Stores the “Show Rich Text Editor Popup” and “Auto Apply Rich Text Rules While Editing” toggles and `app.saved_rich_text_presets` | Never read or display a real user file; do not commit private absolute paths |
| `config/config-example.json` | Release defaults: `editor_rich_text_popup_enabled: true`, `editor_auto_rich_text_rules: true`, `saved_rich_text_presets: null` | Sanitized examples only |
| Rich-text rules config file | Rule definitions for auto rich-text rules | Consumed when “Auto Apply Rich Text Rules While Editing” is on; covered by the rich-text-rules pages |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Floating window | `desktop_qt_ui/ui/widgets/rich_text_floating_editor.py` | Top-level `Qt.Tool` window, body/toolbar/style cards/preset sidebar, 180 ms debounce, `rich_text_changed` signal, drag border, hide/close flush |
| Composable widgets | `desktop_qt_ui/ui/widgets/rich_text_editor_components.py` | Style-key definitions, toolbar buttons, `StyledRunList`/`StyleRunCard`, ruby input bar, preset sidebar |
| Editor state machine | `desktop_qt_ui/editor/rich_text_editor_state.py` | Region binding, selection, document changes, auto-rule injection, ruby draft, `mark_document_emitted` |
| Structured editing | `desktop_qt_ui/editor/rich_text_editing.py` | Thin delegation for `richtext.v1` parse/serialize, style patches, ruby/tcy wrapping, index conversion |
| Document protocol | `manga_translator/rendering/rich_text.py`, `rich_text_rules.py` | Sole `richtext.v1` implementation, `apply_rich_text_rules` incremental semantics |
| View wiring | `desktop_qt_ui/ui/editor/view.py` | Selection binding, docking position, drag hide/restore, model-change refresh, toggle persistence |
| Controller | `desktop_qt_ui/editor/editor_controller.py`, `editor/commands.py` | `update_translation_rich` field write-back, `merge_key` merging |
| Config models | `desktop_qt_ui/core/config_models.py`, `services/config_service.py` | Defaults, persistence, and rollback of the two toggles and presets |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Key and actual bilingual display values |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract; this page’s TODO stays `[未开工]` and is checked by the coordinator |
| UI layout and calls | Complete | Statically checked the floating window, toolbar, style cards, preset sidebar, and view wiring |
| `en_US` / `zh_CN` actual locales | Complete | The tables record key, actual English, and actual Simplified Chinese values |
| Edit-and-save runtime chain | Complete | Statically checked debounced commit, `update_translation_rich` write-back, `merge_key` merging, and model refresh |
| Sanitized runtime verification | Deferred | No GUI launch or screenshots; no real user image, `.env`, key, or private task artifact was read |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |