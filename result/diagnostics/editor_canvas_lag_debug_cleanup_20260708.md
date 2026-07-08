# Editor Canvas Lag Debug Cleanup Notes

## Purpose

This note records the temporary canvas lag diagnostics added for reproduction.

The diagnostics are observation-only:

- no cache behavior changes
- no cleanup behavior changes
- no repaint/refresh strategy changes
- no interaction throttling changes
- no normal logger output added

Runtime records are written to:

```text
result/diagnostics/editor_canvas_lag_debug/editor_canvas_lag_debug_YYYYMMDD_HHMMSS_PID.json
```

## Added File

Delete this file to remove the diagnostic writer:

```text
desktop_qt_ui/utils/canvas_lag_debug.py
```

## Instrumented Files

Remove imports and calls to `utils.canvas_lag_debug` from these files:

```text
desktop_qt_ui/app_logic.py
desktop_qt_ui/editor/controller_document_service.py
desktop_qt_ui/editor/document_load_worker.py
desktop_qt_ui/editor/editor_controller.py
desktop_qt_ui/ui/editor/graphics_items.py
desktop_qt_ui/ui/editor/graphics_view.py
desktop_qt_ui/ui/editor/graphics_view_input.py
desktop_qt_ui/ui/editor/graphics_view_rendering.py
```

Search target:

```text
canvas_lag_debug
record_canvas_debug
record_canvas_duration
mark_canvas_interaction
last_canvas_interaction
```

## Output File

The generated debug file can be deleted at any time:

```text
result/diagnostics/editor_canvas_lag_debug/
```

The file is generated only after the app hits an instrumented path. One app process writes one timestamped file unless `EDITOR_CANVAS_LAG_DEBUG_PATH` is set.

## Runtime Switch

Diagnostics are enabled by default. To disable without removing code:

```text
EDITOR_CANVAS_LAG_DEBUG=0
```

Optional output path override:

```text
EDITOR_CANVAS_LAG_DEBUG_PATH=C:\path\to\editor_canvas_lag_debug.json
```

## Recorded Areas

The JSON records cover:

- translation worker cleanup boundary and CUDA/process memory snapshot
- UI delayed cleanup boundary and CUDA/process memory snapshot
- editor document load worker phases
- main-thread apply snapshot phases
- font color update path
- original image alpha path
- canvas wheel/toolbar zoom input
- region text visual render
- full render rebuild
- `QGraphicsView.paintEvent`
- selected slow `RegionTextItem.paint`
- white-frame drag/edit/commit scene invalidation

## Cleanup Verification

After removing the instrumentation, this should return no matches:

```powershell
rg -n "canvas_lag_debug|record_canvas_debug|record_canvas_duration|mark_canvas_interaction|last_canvas_interaction" desktop_qt_ui
```

Then run:

```powershell
python -m py_compile desktop_qt_ui\app_logic.py desktop_qt_ui\editor\document_load_worker.py desktop_qt_ui\editor\controller_document_service.py desktop_qt_ui\editor\editor_controller.py desktop_qt_ui\ui\editor\graphics_view.py desktop_qt_ui\ui\editor\graphics_view_input.py desktop_qt_ui\ui\editor\graphics_view_rendering.py desktop_qt_ui\ui\editor\graphics_items.py
```
