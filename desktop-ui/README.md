# Manga Image Translator UI

This is the user interface for the Manga Image Translator. It provides a graphical interface for translating manga images.

## Features

- **File Management**: Load and manage multiple image files.
- **Image Viewing**: View images with zoom and pan controls.
se
- **Region Selection**: Select and edit text regions on the image.
- **Text Editing**: Edit the original and translated text for each region.
- **Style Editing**: Customize the font size, color, alignment, and direction of the translated text.
- **OCR and Translation**: Perform OCR and translation on the selected regions.
- **Mask Editing**: View and edit the text masks.
- **Inpainting**: Generate inpainted images to remove the original text.
- **Undo/Redo**: Undo and redo actions in the editor.
- **Shortcuts**: Use keyboard shortcuts to perform common actions.

## Shortcuts

### Global Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open File |
| `Ctrl+S` | Save Config |
| `Ctrl+Q` | Exit App |
| `Ctrl+T` | Start Translation |
| `Escape` | Stop Translation |
| `Ctrl+E` | Switch to Editor |
| `Ctrl+M` | Switch to Main View |
| `Ctrl+A` | Select All Files |
| `Delete` | Delete Selected Files |
| `F1` | Show Help |
| `F5` | Refresh |

### Editor Shortcuts

These shortcuts are active when the editor is in focus.

| Shortcut | Action |
|---|---|
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |

### Canvas Shortcuts

These shortcuts are active when the canvas is in focus.

| Shortcut | Action |
|---|---|
| `Ctrl+A` | Select All Regions |
| `Ctrl+C` | Copy Selected Regions |
| `Ctrl+V` | Paste Region |
| `Delete` | Delete Selected Regions |

### OCR/Translation Shortcuts

| Shortcut | Action |
|---|---|
| `F7` | OCR Recognize |
| `F8` | Translate |
| `F9` | OCR and Translate |
