# Workflows Guide

This document explains the current Qt UI workflow modes and related features.

The names in this guide follow the current `Translation Workflow Mode:` list in the Qt UI.

---

## 📋 Workflow Modes

The `Translation Workflow Mode:` dropdown currently contains **9** modes:

1. `Normal Translation`
2. `Export Translation`
3. `Export Original Text`
4. `Translate JSON Only`
5. `Import Translation and Render`
6. `Colorize Only`
7. `Upscale Only`
8. `Inpaint Only`
9. `Replace Translation`

---

## 1. Normal Translation

**Purpose**: standard end-to-end image translation.

**Steps**:

1. Add files or folders
2. Choose `Translator` and `Target Language`
3. Click `Start Translation`
4. The translated result is saved to the output directory

**Pipeline**:

- Detection
- OCR
- Translation
- Inpainting
- Rendering

Qt UI tip:

- `Tip: Standard translation pipeline with detection, OCR, translation and rendering`

---

## 2. Export Translation

**Purpose**: translate first, then export text results for later review or editing.

**Recommended steps**:

1. Choose `Export Translation`
2. Enable `Editable Image` so JSON data is generated too
3. Start the task

The app will:

- Complete the normal translation flow
- Generate JSON data
- Generate translated TXT output

Typical files:

- `manga_translator_work/json/<image>_translations.json`
- `manga_translator_work/translations/<image>_translated.txt`

Qt UI tip:

- `Tip: After exporting, check manga_translator_work/translations/ for imagename_translated.txt files`

---

## 3. Export Original Text

**Purpose**: detect and OCR only, then export original text for manual translation.

**Steps**:

1. Choose `Export Original Text`
2. Enable `Editable Image`
3. Click `Generate Original Text Template`

The app will:

- Detect text regions
- Run OCR
- Generate JSON data
- Generate the original-text TXT file
- Stop before translation

Typical files:

- `manga_translator_work/json/<image>_translations.json`
- `manga_translator_work/originals/<image>_original.txt`

**Manual translation flow**:

1. Open `<image>_original.txt`
2. Replace the source text with your translated text
3. Then use `Import Translation and Render`

TXT import priority when rendering later:

- Highest: `_original.txt`
- Then: `_translated.txt`
- Otherwise: JSON translation fields

Qt UI tip:

- `Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode`

---

## 4. Translate JSON Only

**Purpose**: translate existing JSON text content only, without detection, OCR, inpainting, or rendering.

**When to use it**:

- You already have `_translations.json`
- You want to update the translation text only
- You do not want to rerun OCR or image processing

**Behavior**:

- Reads original text from the JSON file
- Sends only the translation stage
- Writes the translated text back into JSON
- Deletes `<image>_original.txt` after a successful write-back

Qt UI tip:

- `Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success`

---

## 5. Import Translation and Render

**Purpose**: import translation content from TXT or JSON and render the page again without re-translating.

**Steps**:

1. Choose `Import Translation and Render`
2. Add previously processed images that already have matching JSON data
3. Click `Import Translation and Render`

The app will:

- Check whether `_original.txt` or `_translated.txt` exists
- Import TXT content into JSON when needed
- Load translation text from JSON
- Render the final image
- Skip the translation step itself

TXT priority:

- `_original.txt` from `manga_translator_work/originals/`
- `_translated.txt` from `manga_translator_work/translations/`
- JSON translation fields if no TXT exists

Useful when:

- You edited TXT files manually
- You edited JSON translation content
- You changed rendering settings such as font, color, or layout

Qt UI tip:

- `Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)`

---

## 6. Colorize Only

**Purpose**: colorize black-and-white images only.

**Steps**:

1. Choose `Colorize Only`
2. Open `Settings` -> `Mode Specific`
3. Configure:
   - `Colorization Model`
   - `Colorization Size`
   - `Denoise Strength`
4. Click `Start Colorizing`

The app will skip:

- Detection
- OCR
- Translation
- Rendering

It only performs colorization and saves the output image.

Qt UI tip:

- `Tip: Only colorize images, no detection, OCR, translation or rendering`

---

## 7. Upscale Only

**Purpose**: super-resolve images only.

**Steps**:

1. Choose `Upscale Only`
2. Open `Settings` -> `Mode Specific`
3. Configure:
   - `Upscaling Model`
   - `Upscale Ratio`
   - `Real-CUGAN Model`
   - `Tile Size (0=No Split)` if VRAM is limited
4. Click `Start Upscaling`

The app will skip:

- Detection
- OCR
- Translation
- Rendering

Typical recommendations:

- High quality: `realcugan`
- Best quality but heavier: `mangajanai`
- Low VRAM: reduce `Tile Size (0=No Split)` to values like `400`

Qt UI tip:

- `Tip: Only upscale images, no detection, OCR, translation or rendering`

---

## 8. Inpaint Only

**Purpose**: detect text and erase it, then output a clean image without rendering translated text.

**Steps**:

1. Choose `Inpaint Only`
2. Open `Settings` -> `Inpainting`
3. Configure:
   - `Inpainting Model`
   - `Inpainting Size`
   - `Inpainting Precision`
4. Click `Start Inpainting`

The app will:

- Detect text
- Create the text mask
- Inpaint the original image
- Skip translation
- Skip rendering

Typical output:

- Cleaned image saved to the output directory

Qt UI tip:

- `Tip: Detect text regions and inpaint to output clean images, no translation or rendering`

---

## 9. Replace Translation

**Purpose**: extract translation data from already translated images and apply it to matching raw images.

This is useful when:

- You already have translated pages
- You want to reprocess the raw pages
- You want to reuse translation results without paying for translation again

### Required file layout

```text
raw-image-folder/
├── page1.jpg
├── page2.jpg
└── manga_translator_work/
    └── translated_images/
        ├── page1.jpg
        └── page2.jpg
```

The translated image filenames should match the raw image filenames.

### Steps

1. Prepare matching raw and translated files
2. Choose `Replace Translation`
3. Add the raw images
4. Click `Start Replace Translation`

The app will:

- Extract OCR results from translated images
- Extract OCR results from raw images
- Match regions by overlap and position
- Inpaint the raw images
- Render translated content onto the raw images

### Matching notes

- Region overlap is used for matching
- Multi-to-one matching is supported
- Warnings are shown in logs when a region cannot be matched

### Related setting

Under `Settings` -> `Mode Specific` -> `Replace Translation`:

- `Enable Direct Paste Mode`

This corresponds to the setting description:

- `Direct paste mode: crop regions from translated image and paste to raw image by coordinate matching, preserving original font style. Only works in Replace Translation mode.`

Qt UI tip:

- `Tip: Place translated images in manga_translator_work/translated_images with matching filenames. The app extracts translated text, matches regions on raw images, inpaints originals, and renders translated text.`

---

## 📝 Editable Image

The `Editable Image` option controls whether the app saves translation JSON data for later editing.

When enabled, the app can generate:

- Detected region data
- OCR original text
- Translation text
- Region positions and rendering-related metadata

Why it matters:

- Lets you open the result in `Editor View`
- Enables `Import Translation and Render`
- Supports workflows that reuse translation JSON

---

## 🤖 AI Line Breaking

Supported translators:

- `OpenAI`
- `Google Gemini`
- `Vertex`
- `OpenAI High Quality`
- `Gemini High Quality`
- `Vertex High Quality`

Relevant setting:

- `AI Line Breaking`

What it does:

- Helps the model produce better line breaks
- Improves readability inside speech bubbles
- Works inside the normal translation request rather than adding a separate request

Useful companion settings in `Typesetting`:

- `AI Line Break Auto Enlarge`
- `AI Line Break Check`
- `Don't Expand Box on Auto Enlarge`

---

## 📚 Auto Extract Glossary

Supported translators:

- `OpenAI`
- `Google Gemini`
- `Vertex`
- `OpenAI High Quality`
- `Gemini High Quality`
- `Vertex High Quality`

Relevant setting:

- `Auto Extract Glossary`

What it does:

1. The translator identifies names and terms during translation
2. New terms are extracted after translation
3. They are written into the current prompt file glossary section
4. Later pages reuse the same term mapping for consistency

Useful for:

- Long series
- Character names
- Place names
- Organization names
- Team workflows that share prompt files

Recommendation:

- Create one prompt file per project and keep glossary extraction enabled for that project

---

## 📂 Custom Export Template

The export-original workflow can be customized with:

- `config/translation_template.json`

This template controls how groups of text items are exported.

How it works:

- The template defines a repeated structure
- `<original>` and `<translated>` are placeholders
- The app repeats the text-box structure, not the entire outer JSON layout

Typical use case:

- Preparing content for external translation tools
- Keeping a structured manual-translation format

---

## 💾 Work File Paths

The app creates `manga_translator_work` next to the source images.

Common outputs:

- **JSON data**
  - `manga_translator_work/json/<image>_translations.json`

- **Editor base image**
  - `manga_translator_work/editor_base/<image>.<ext>`

- **Original text TXT**
  - `manga_translator_work/originals/<image>_original.txt`

- **Translated text TXT**
  - `manga_translator_work/translations/<image>_translated.txt`

- **Inpainted image**
  - `manga_translator_work/inpainted/<image>_inpainted.<ext>`

- **Rendered result**
  - `manga_translator_work/result/<image>.png`

- **PSD output**
  - `manga_translator_work/psd/<image>.psd`

- **PSD script**
  - `manga_translator_work/psd/<image>_photoshop_script.jsx`

- **Translated images for Replace Translation**
  - `manga_translator_work/translated_images/<image>.jpg`

Why these folders matter:

- `json/` stores editable translation state
- `originals/` and `translations/` support manual translation workflows
- `inpainted/` stores cleaned images for editor reuse
- `psd/` stores PSD export artifacts
- `translated_images/` is the source folder for `Replace Translation`

---

Back to [README_EN](../../README_EN.md)
