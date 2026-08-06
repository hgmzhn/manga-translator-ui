---
title: Editor Toolbar and Menus
description: Use the editor toolbar's three dropdown menus and persistent controls; understand menu expansion, export, zoom, and toggle persistence
pageId: desktop.editor.toolbar-and-menus
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Editor Toolbar and Menus

When you enter the editor, a fixed horizontal toolbar sits at the top. It groups high-frequency actions into three single-level dropdown menus (“Menu”, “Display Mode”, “Arrange”) and keeps two persistent controls (“Fit to Window” and original-image opacity). This page explains how the three menus expand, where each menu item leads, and how the five editor toggles are stored and persisted.

The complete options and canvas effects of “Display Mode” and “Arrange” live in [Display, Compare, and Arrange](./display-compare-and-arrange.md); canvas tools, property panels, the floating rich-text editor, shortcuts, and import/export are covered by [Canvas Tools and Selection](./canvas-tools-and-selection.md), [Text Properties](./text-properties.md), [Style Properties](./style-properties.md), [Floating Rich Text](./floating-rich-text.md), [Shortcuts](./shortcuts.md), and [Import/Export and Writeback](./import-export-and-writeback.md).

## Feature boundary

- The toolbar itself never switches pages: the back-to-home entry lives in the main-window sidebar, not in the editor toolbar.
- The “Menu” dropdown contains export, undo/redo, zoom in/out, and five checkable toggles; the five toggles are persisted in the `app` config section.
- “Display Mode” is an exclusive radio selection that decides whether the canvas shows the original, text, boxes, nothing, or a two-panel comparison; “Arrange” provides a reference radio, alignment, and spacing distribution. Their complete options belong to [Display, Compare, and Arrange](./display-compare-and-arrange.md).
- Zoom in/out is view scaling only: `Zoom In (+)` / `Zoom Out (-)` scale by 1.15 per step and clamp the canvas scale to `0.05`–`50.0`; “Fit to Window” only fits the view. Neither modifies any region data.
- The “Original Image Opacity” slider (0–100) controls only the transparency of the original-image overlay on the canvas; it is not an export parameter.
- The real shortcut registration is not in the toolbar: `Ctrl+Q` (export), `Ctrl+Z` (undo), and `Ctrl+Y` (redo) are registered globally by `EditorShortcutManager`; the toolbar only shows hint text. See [Shortcuts](./shortcuts.md).

## UI operations

### Three dropdown menus

1. Open “Menu” (`Menu`): it shows “Export Image” (`Export Image`, with a `(Ctrl+Q)` hint appended by code), undo/redo (`Undo` / `Redo`, hinted `Ctrl+Z` / `Ctrl+Y`), zoom in/out (`Zoom In (+)` / `Zoom Out (-)`), and five checkable editor toggles.
2. Open “Display Mode” (`Display Mode`): a radio group that switches between five canvas display states; see [Display, Compare, and Arrange](./display-compare-and-arrange.md) for the effects.
3. Open “Arrange” (`Arrange`): first pick a reference (selection/canvas), then apply alignment or distribution; the menu stays open after a click so you can continue. See [Display, Compare, and Arrange](./display-compare-and-arrange.md) for the full options.

### Persistent toolbar controls

- Click “Fit to Window” (`Fit to Window`): the current image is scaled to fill the canvas viewport while keeping its aspect ratio.
- Drag the “Original Image Opacity:” (`Original Image Opacity:`) slider: `0` is fully transparent (showing the inpainted/cleaned background), `100` is fully opaque (showing the original); it starts at `0`.

## Option matrix

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Menu` | Menu | 菜单 |
| `Display Mode` | Display Mode | 显示模式 |
| `Arrange` | Arrange | 排列 |
| `Fit to Window` | Fit to Window | 适应窗口 |
| `Original Image Opacity:` | Original Image Opacity: | 原图不透明度: |
| `Export Image` | Export Image | 导出图片 |
| `Undo` | Undo | 撤销 |
| `Redo` | Redo | 重做 |
| `Zoom In (+)` | Zoom In (+) | 放大 (+) |
| `Zoom Out (-)` | Zoom Out (-) | 缩小 (-) |
| `Enable Editor Snapping` | Enable Editor Snapping | 启用编辑器吸附 |
| `Scale Text Boxes from Center` | Scale Text Boxes from Center | 中心点缩放 |
| `Show Rich Text Editor Popup` | Show Rich Text Editor Popup | 显示富文本编辑弹窗 |
| `Auto Apply Rich Text Rules While Editing` | Auto Apply Rich Text Rules While Editing | 编辑时自动应用富文本规则 |
| `Auto Export on Image Switch` | Auto Export on Image Switch | 切图时自动导出 |

The `(Ctrl+Q)`, `(Ctrl+Z)`, and `(Ctrl+Y)` suffixes on “Export Image”, “Undo”, and “Redo” are hint text appended by code after the i18n value; they are not part of any locale value, and the shortcut manager performs the actual trigger. The full option values of “Display Mode” and “Arrange” (such as `Show Text and Boxes`, `Align Left`, `Distribute Vertical Spacing`) are listed item by item in [Display, Compare, and Arrange](./display-compare-and-arrange.md).

## Runtime behavior

### Menu expansion and language switching

All three dropdown buttons open single-level menus; there are no nested submenus, and icon, check/radio indicator, and text columns are laid out independently:

```mermaid
flowchart LR
    subgraph TB["Editor toolbar EditorToolbar"]
        M["Menu"] --> MI["Export / Undo-Redo / Zoom + 5 toggles"]
        D["Display Mode"] --> DI["5 exclusive display states"]
        A["Arrange"] --> AI["Reference radio + align/distribute (stays open)"]
        F["Fit to Window"]
        O["Original image opacity 0–100"]
    end
    MI --> C["controller / export_service / graphics_view"]
    D --> CV["controller.set_display_mode"]
    A --> AV["controller align and distribute"]
    F --> FV["graphics_view.fit_to_window"]
    O --> OV["controller.set_original_image_alpha"]
```

- “Menu” uses a `CheckableMenu` with a leading indicator column: checking one of the five toggles shows the indicator, and the icon and text columns stay independent.
- The five “Display Mode” states and the “Arrange” reference options are exclusive `QActionGroup` radios.
- “Arrange” is a stay-open menu: it remains expanded after choosing a reference or applying an alignment/distribution so you can keep operating; clicking outside or pressing `Esc` closes it.
- On language switch, `EditorView.refresh_ui_texts()` calls `EditorToolbar.refresh_ui_texts()`, which rebuilds all three menus and restores the display mode, reference, toggles, and enabled states from internal fields so no state is lost.
- When the window is too narrow, toolbar content moves into a horizontal scroll area instead of wrapping or collapsing.

### Export and auto-export on image switch

- Clicking “Export Image” (or pressing `Ctrl+Q`) runs `EditorView.export_image()` → `controller.export_image()`: it first calls `commit_pending_edits()` to flush unsaved edits, then hands the work to the background export queue (`EditorControllerExportService`). Progress is shown as a Toast; failures have their own error messages.
- While a document is loading, `toolbar.set_export_enabled(False)` disables export; it is re-enabled after the loaded data is applied.
- “Auto Export on Image Switch” (`Auto Export on Image Switch`) decides how unsaved edits are handled when switching images:
  - On: unsaved edits trigger an automatic export (`automatic=True`); if export is rejected, the image switch is aborted.
  - Off: an “Unsaved edits” three-button dialog appears (“导出图片”/“不保存”/“取消”). These three labels are currently hard-coded Chinese in source with no i18n key; they are a known gap, and no English label is invented here.
- The auto-export consumer reads the configuration directly when switching images; it does not depend on view-memory state.

### Undo and redo

- Undo/redo is implemented with `QUndoStack` (`history_service`); after each command-state change, the controller updates the toolbar's undo/redo enabled state.
- The shortcut text on the toolbar is a hint only; the focus-aware registration lives in `EditorShortcutManager`.

### Zoom and fit to window

- “Zoom In (+)” / “Zoom Out (-)” call `_apply_zoom(1.15)` / `_apply_zoom(1 / 1.15)` per step and clamp the target scale to `0.05`–`50.0`.
- The canvas wheel zooms the same way (up zooms in, down zooms out, factor 1.15), anchored at the mouse position.
- “Fit to Window” calls `fitInView(..., KeepAspectRatio)` to place the current image fully inside the viewport.

### Original image opacity

- The slider value `0`–`100` maps to `0.0`–`1.0`: `0` is fully transparent (showing the inpainted/cleaned background) and `100` is fully opaque (showing the original).
- When an image loads and the user has not manually adjusted the opacity, the default depends on whether an inpainted image exists: `0` with one, `1` without. Once the user drags the slider, the session stops overriding it (`_user_adjusted_alpha`).
- Slider changes are mirrored back through the model's `original_image_alpha_changed` signal so external updates also refresh the slider position.

### Persistence of the five toggles

| Stored value | English actual value | Simplified Chinese actual value | Default | Write path |
| --- | --- | --- | --- | --- |
| `app.editor_snap_enabled` | Enable Editor Snapping | 启用编辑器吸附 | `false` | Menu → `snap_enabled_changed` → `config_service.update_config` + `save_config_file` |
| `app.editor_center_scale_enabled` | Scale Text Boxes from Center | 中心点缩放 | `false` | Same, `center_scale_enabled_changed` |
| `app.editor_rich_text_popup_enabled` | Show Rich Text Editor Popup | 显示富文本编辑弹窗 | `true` | Same, `rich_text_popup_enabled_changed` |
| `app.editor_auto_rich_text_rules` | Auto Apply Rich Text Rules While Editing | 编辑时自动应用富文本规则 | `true` | Same, `auto_rich_text_rules_changed` |
| `app.editor_auto_export_on_switch` | Auto Export on Image Switch | 切图时自动导出 | `true` | Same, `auto_export_on_switch_changed` |

The Qt model `desktop_qt_ui/core/config_models.py#AppSection`, the release example `config/config-example.json`, and the `EditorToolbar` constructor defaults all agree. Configuration changes are mirrored back to the toolbar buttons through the `config_changed` signal, so external changes or config reloads stay in sync.

## Dependencies and conflicts

- The toolbar only shows shortcut text and never registers `QAction` shortcuts, avoiding double triggers with the focus-aware registrations in `EditorShortcutManager`.
- When focus is in a text widget, editing shortcuts such as undo/redo are left to the text control; `Q`/`W`/`E`/`A`/`D` are forwarded as text instead of switching tools/images; `Ctrl+Q` export is unaffected. See [Shortcuts](./shortcuts.md).
- The availability of “Arrange” items depends on the selection count: with the canvas reference one selected region is enough for alignment, with the selection reference two are required, and spacing distribution needs three.
- “Auto Export on Image Switch” depends on the export queue and the image-loading flow: a rejected auto-export aborts the switch; when the user picks “export”, the switch waits for the export to finish.
- Export is an asynchronous queue task that shares a state machine with editor cancellation/cleanup; on shutdown the export queue is drained before the app exits.
- Turning off “Show Rich Text Editor Popup” immediately hides any visible floating editor; the canvas keeps focus, so delete/shortcut semantics are unchanged.

## Related files and formats

| File/format | Actual role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `config/config-example.json` | Release defaults for the five editor toggles | Use sanitized examples only; importing user configuration overrides memory settings |
| `config/config.json` | Runtime user-settings persistence | Never read or display a real user file; do not commit private absolute paths |
| `desktop_qt_ui/locales/en_US.json` / `zh_CN.json` | Translations for “Menu”, “Display Mode”, “Arrange”, and all menu items/persistent controls | Mark missing keys honestly as missing/fallback; do not invent translations |
| `desktop_qt_ui/ui/widgets/editor_toolbar.py` | All toolbar widgets and menu construction | Shortcut hint text for export/undo/redo is appended by code, not part of any locale value |
| `desktop_qt_ui/ui/editor/view.py` | Toolbar creation, signal wiring, config sync, and language refresh | The five toggles are persisted through `config_service` |

## Mermaid diagram limits

The diagram describes the source-confirmed toolbar structure and signal destinations; it does not claim that every operation triggers an export or a network request. Export failures, empty selections, and missing images each take their documented branches. No runtime screenshot or private task artifact has been fabricated.

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| UI | `desktop_qt_ui/ui/widgets/editor_toolbar.py` | Three dropdown menus, persistent controls, five toggles, check/radio/stay-open behavior, and language rebuild |
| View wiring | `desktop_qt_ui/ui/editor/view.py` | Toolbar creation and fixed height, signal wiring, `_on_config_changed` sync, `refresh_ui_texts` |
| Controller/services | `desktop_qt_ui/editor/editor_controller.py`, `controller_export_service.py`, `controller_document_service.py` | Export queue, auto-export/unsaved dialog, undo-redo state, opacity mapping |
| Canvas view | `desktop_qt_ui/ui/editor/graphics_view_input.py`, `graphics_view.py` | Zoom factor and clamps, wheel zoom, fit to window |
| Config models | `desktop_qt_ui/core/config_models.py`, `config/config-example.json` | Qt model and release defaults for the five toggles |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Key mapping and actual bilingual display values |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract (section 1.3, item 5.10) |
| UI layout and calls | Complete | Statically checked `editor_toolbar.py`, `view.py`, and the export/image-switch services |
| `en_US` / `zh_CN` actual locales | Complete | The tables record key, actual English, and actual Simplified Chinese values item by item |
| Runtime chain (export, zoom, opacity, persistence) | Complete | Statically checked export service, graphics view, and config service |
| Sanitized runtime verification | Deferred | No real user config, API key/token, username, user image, or private task artifact was read |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |
