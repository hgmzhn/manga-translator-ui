---
title: Paste Overlays
description: Drop PNG/JPG assets onto the canvas as independent overlays with move, resize, rotate, copy/paste, persistence and export support
pageId: desktop.editor.paste-overlays
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Paste Overlays

Besides writing translations into text regions, typesetting often needs to place **cut-out image assets** on the page: hand-written characters, SFX, stamps, stickers, or background patches borrowed from another page. Text regions are a poor fit for that, so previously people composited such assets outside the editor and pasted the finished page back. The editor's *paste overlay* object covers this use case: drop a PNG/JPG straight onto the canvas and edit its position, size, rotation, opacity and stacking independently, with the result persisted per page and baked into exports.

## What it does

- Dropping **PNG/JPG/WebP** creates a page-level overlay centered at the drop point, selected immediately; oversized assets (edge > 2048 px) are scaled down automatically.
- Once selected, editing follows the same habits as text regions: **move / corner uniform resize / top rotation / hover cursors**; `Ctrl+Wheel` scales uniformly, `Ctrl+C` / `Ctrl+V` copy & paste, `Delete` removes, `Ctrl+Z` undoes.
- Each change (move/resize/rotate/add/remove) is an undoable command.
- Overlays are persisted with the page (see Storage below) and restored when reopening; **export** composites them into the final image at the same position/size/orientation as shown on the canvas, below rendered text.
- Pages containing *only* overlays (no text regions) still export correctly, preserving transparency for transparent PNG assets.

## Editing in the editor

### Adding an overlay

1. Open the editor and load a manga page.
2. Drag a PNG/JPG from Explorer onto the canvas and release — it lands where you drop it.
3. Alternatively copy an existing overlay by selecting it and pressing `Ctrl+C`, then press `Ctrl+V` to paste a copy at the mouse position.

Overlays and text regions do not interfere: clicking an overlay selects the overlay, clicking a text region selects the region; clicking empty canvas or a text region dismisses the overlay handles.

### Editing the selected overlay

The selected overlay shows the same decorations as text regions:

- **Dashed border** marks the selection;
- **Corner squares**: drag to **scale uniformly** around the overlay center;
- **Top ring**: drag to **rotate** around the center;
- Hovering the corner/rotation handles switches the cursor to diagonal-resize / move, so the draggable areas are discoverable;
- Dragging the body moves the overlay;
- `Ctrl+Wheel` = uniform scaling (same entry point as the text-region font-size wheel: with no region selected it applies to the overlay);
- `Ctrl+C` / `Ctrl+V` = copy / paste (overlay clipboard is independent from the region clipboard);
- `Delete` removes the selected overlay.

For shortcuts and focus rules see [Shortcuts](./shortcuts.md); canvas tools, selection, zoom and pan see [Canvas tools and selection](./canvas-tools-and-selection.md).

## Saving and export behavior

### Storage format

Overlays are stored alongside page `regions` in the page's `*_translations.json`:

```jsonc
"paste_overlays": [{
  "id": "…",
  "name": "…",
  "z": 0, "visible": true, "opacity": 1.0,
  "center_x": 0.0, "center_y": 0.0,   // source-image pixel coordinates
  "width": 0.0, "height": 0.0,
  "rotation": 0.0, "flip_h": false, "flip_v": false,
  "image": "<base64 PNG, RGBA>"
}]
```

- Images are embedded as base64 PNG (RGBA), like `mask_raw` / `paint_overlay` / `stamp_overlay`, so a page stays self-contained and portable;
- Old page files without `paste_overlays` load fine (treated as empty);
- Geometry is expressed in **source-image pixels**; the canvas preview and backend export share the same coordinate space.

### Export and backend

- Editor "export" composites overlays in ascending `z` order over the base image with premultiplied-alpha source-over (no darkened or bleeding edges), below rendered text;
- Pages with overlays but no text regions/mask export correctly, including RGBA output for transparent PNG assets;
- CLI/Web loading from disk JSON reads `paste_overlays` and materializes them so results match the desktop export.

## Limitations and roadmap

- Overlays are edited as **single objects** for now (no multi-select/align/distribute); stacking is controlled by the `z` field;
- Overlays and text regions use separate object types and selection state; unifying them fully (shared property panel, multi-select batch operations, common geometry-handle pipeline) would be a model-layer change and is open for discussion;
- Oversized or malformed project files are defensively skipped (including a pre-decode pixel-size check) to keep exports from exhausting memory.

## Related pages

- [Canvas tools and selection](./canvas-tools-and-selection.md)
- [Shortcuts](./shortcuts.md)
- [Import, export and write-back](./import-export-and-writeback.md)
- [Mask painting and the clone stamp](./mask-paint-and-clone-stamp.md)
