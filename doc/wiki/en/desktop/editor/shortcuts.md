---
title: Editor Shortcuts
description: Review every registered keyboard and wheel shortcut in the editor, and understand key priority when focus conflicts
pageId: desktop.editor.shortcuts
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Editor Shortcuts

Most high-frequency editor actions can be triggered with the keyboard or the mouse wheel: undo/redo, copy/paste/delete, switching canvas tools, switching images, and wheel combos for the brush size and the selected regions' font size. This page lists every shortcut and wheel combo actually registered by `EditorShortcutManager`, and explains who receives a key when focus is in a text widget, on the canvas, or in the floating rich-text window.

The full operations of the toolbar and menus, canvas tools, region list, property panels, and floating rich text live in [Toolbar and Menus](./toolbar-and-menus.md), [Canvas Tools and Selection](./canvas-tools-and-selection.md), [Region List and Text Editing](./region-list-and-text-editing.md), [Text Properties](./text-properties.md), [Style Properties](./style-properties.md), [Floating Rich Text](./floating-rich-text.md), and [Import/Export and Writeback](./import-export-and-writeback.md). This page only answers “which key does what”; it does not repeat those pages' control details.

## Feature boundary {#feature-boundary}

- All editor keyboard shortcuts are registered by `desktop_qt_ui/ui/editor/shortcut_manager.py#EditorShortcutManager`; the toolbar never registers `QAction` shortcuts and only appends hint text to the “Export Image”, “Undo”, and “Redo” menu items.
- `Undo`, `Redo`, `Copy`, `Paste`, `Select All`, and `Delete` are registered with `QKeySequence.StandardKey`; on Windows the primary bindings are `Ctrl+Z`, `Ctrl+Y`, `Ctrl+C`, `Ctrl+V`, `Ctrl+A`, and `Del`. The displayed mapping follows the Qt platform.
- `Ctrl+Q` (export), `Q`/`W`/`E` (tool switching), and `A`/`D` (image switching) are fixed literals and do not change across platforms.
- Wheel combos are handled by an event filter installed on the canvas viewport: `Shift+wheel` changes the shared brush size, any `Ctrl`-containing wheel combo changes the selected regions' font size, and a plain wheel is passed to `GraphicsView` to zoom the canvas.
- `Escape` or canvas focus loss cancels an in-progress box selection, drawing, text box, clone stamp, region drag, or middle-button pan without committing anything.
- Shortcut dispatch priority, from highest to lowest: focus in another top-level window (for example the floating rich-text window) → focus in a main-window text widget → focus on the canvas. The full conflict rules are in [Runtime behavior](#runtime-behavior).

## UI operations {#ui-operations}

### View shortcut hints in the Menu {#toolbar-hints}

Open the editor toolbar's “Menu”: “Export Image”, “Undo”, and “Redo” have `(Ctrl+Q)`, `(Ctrl+Z)`, and `(Ctrl+Y)` hint text appended by code. These hints are text only; the real shortcuts are registered by `EditorShortcutManager`.

### Keyboard shortcut reference {#shortcut-reference}

The following table lists every keyboard shortcut actually registered by `_setup_editor_shortcuts()`. Behavior differs when focus is in a text widget (`QTextEdit` or `QLineEdit`) versus on the canvas; with text focus, `Q`/`W`/`E`/`A`/`D` are forwarded to the widget as ordinary characters instead of switching tools or images.

| Shortcut | With focus in a text widget | Canvas focus |
| --- | --- | --- |
| `Ctrl+Z` | Undo text editing | Undoes the editor action |
| `Ctrl+Y` | Redo text editing | Redoes the editor action |
| `Ctrl+C` | Copy text | Copies the last selected region |
| `Ctrl+V` | Paste text | With one selected region, pastes its style; with none, pastes a region at the mouse or default position |
| `Ctrl+A` | Select all text | Selects all regions |
| `Del` | Does not delete regions | Deletes the selected regions |
| `Ctrl+Q` | Still exports (flushes pending floating rich-text changes first) | Still exports |
| `Q` | Types the character `q` | Switches to the select tool |
| `W` | Types the character `w` | Switches to the brush tool |
| `E` | Types the character `e` | Switches to the eraser tool |
| `A` | Types the character `a` | Selects the previous image in the file list |
| `D` | Types the character `d` | Selects the next image in the file list |

### Wheel combos {#wheel-combos}

| Combo | Effect | Limits |
| --- | --- | --- |
| `Shift + wheel` | Changes the shared brush size by ±1 per notch | Clamped to `5`–`200`; the event is swallowed and does not zoom the canvas |
| Any `Ctrl`-containing wheel combo | Changes the font size of all selected regions by ±5% (minimum 1) | Swallowed even with no selection; never falls through to canvas zoom |
| Plain wheel | Zooms the canvas anchored at the mouse position | Scale clamped to `0.05`–`50.0`, handled by `GraphicsView.wheelEvent()` |

## Runtime behavior {#runtime-behavior}

### Registration and focus dispatch {#registration-and-dispatch}

`EditorShortcutManager` registers every editor shortcut with focus awareness (`context_aware=True`). On trigger it reads `QApplication.focusWidget()`: if there is no focused widget, or the focused widget's top-level window is not the `EditorView` window (for example when focus is in the `Qt.Tool` floating rich-text window), every editor shortcut returns immediately, so the main window's stale focus cannot delete canvas regions by accident; otherwise it checks whether the focus is a `QTextEdit` / `QLineEdit` to decide between text-widget handling and editor semantics.

```mermaid
flowchart TD
    A["QShortcut triggered<br/>EditorShortcutManager"] --> B{"focusWidget non-null and top-level window<br/>equals EditorView's window?"}
    B -->|no| X["Return immediately<br/>no focused widget or other top-level window"]
    B -->|yes| C{"Focus is QTextEdit / QLineEdit?"}
    C -->|yes| D["Undo/Redo/Copy/Paste/SelectAll<br/>call text-widget methods; Q/W/E/A/D forwarded as characters"]
    C -->|no| E["Editor semantics"]
    E --> E1["Undo/Redo → controller<br/>Copy → copy last selected region<br/>Paste → paste style or region<br/>SelectAll → select all regions<br/>Delete → delete selected regions"]
    E --> E2["Q/W/E → select / brush / eraser<br/>A/D → previous / next image in file list"]
```

### Key forwarding with text focus {#text-widget-forwarding}

When focus is in a text widget, the `Q`/`W`/`E`/`A`/`D` handlers temporarily disable the corresponding shortcut, synthesize a `KeyPress` plus `KeyRelease` event (for example `q`) sent to the widget, and then re-enable the shortcut to avoid recursion. `Ctrl+Q` export does not depend on focus: it calls `EditorView.export_image()` directly, which first runs `flush_pending_changes()` to flush debounced rich-text body and ruby edits from the floating editor.

### Wheel event filtering {#wheel-event-filter}

`EditorShortcutManager` installs an event filter on `graphics_view.viewport()`, so wheel events reach the filter first:

```mermaid
flowchart LR
    W["Canvas viewport wheel event"] --> F{"EditorShortcutManager.eventFilter"}
    F -->|Shift| B["Change shared brush size<br/>±1 per notch, clamp 5–200, swallow"]
    F -->|contains Ctrl| C["Change selected-region font size<br/>±5% (min 1), swallowed even without selection"]
    F -->|other| Z["Not intercepted<br/>GraphicsView.wheelEvent<br/>zoom canvas by 1.15"]
```

The `Shift` branch matches “modifiers equal Shift”; the `Ctrl` branch matches “contains Ctrl”, so `Ctrl+Shift+wheel` also resizes fonts. Property-panel sliders, spin boxes, and dropdowns do not swallow the wheel without keyboard focus and let the parent scroll area take over; `CustomSlider` changes its value only while focused.

### Escape and canvas focus loss {#escape-and-focus-out}

When the canvas has an in-progress interaction (box selection, drawing, text box, clone stamp, region drag, or middle-button pan), pressing `Escape` calls `_cancel_active_interaction(commit=False)` to discard it without committing; `focusOutEvent` (modal dialogs, window deactivation, or focus transfer) uses the same discard path so a lost `mouseRelease` cannot leave a stray box-selection rectangle or drawing preview on the scene.

## Dependencies and conflicts {#dependencies-and-conflicts}

- `Delete` deletes regions only when focus is not in a text widget; a `Delete` inside a text widget never triggers region deletion.
- The floating rich-text window is shown with `WA_ShowWithoutActivating`, and selection changes never call `focus_text()` to steal focus; the canvas keeps focus, so `Delete`/`A`/`D`/`Q`/`W`/`E` keep their canvas semantics after the popup appears. Clicking the editor's text box enters text editing normally.
- A canvas mouse press first calls `force_save_property_panel_edits()` and then `setFocus()` on the canvas, so property-panel text being edited is not lost when switching to the canvas.
- Rows being edited in the region list use delta sync to preserve drafts: a focused translation box is not overwritten by model updates, avoiding lost focus, caret, or IME composition; property-panel text being edited is likewise not overwritten by regular refresh, with asynchronous forced field updates as the exception.
- The toolbar only shows shortcut hint text and never registers `QAction` shortcuts, avoiding double triggers with `EditorShortcutManager`.
- Shortcut behavior depends on selection state: with no selection, `Delete` and `Copy` do nothing and `Paste` inserts a region at the mouse position; with multiple regions, `Copy` copies only the last selected one.
- Outside the editor (other main-window pages or other system windows), these shortcuts are outside `EditorShortcutManager`'s registration scope and are not guaranteed to work.

For further developer-facing mappings and source evidence, see the [Source evidence index](../../reference/source-evidence-index.md) and the [Options and I18n matrix](../../reference/options-i18n-matrix.md).
