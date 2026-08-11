# Usage Guide

This document explains the basic Qt UI workflow, translator selection, common settings, and editor operations.

The wording in this English guide follows the current Qt UI i18n strings from `desktop_qt_ui/locales/en_US.json`.

---

## 📋 Table of Contents

- [First Use](#first-use)
- [Basic Operations](#basic-operations)
- [Choosing a Translator](#choosing-a-translator)
- [Common Settings](#common-settings)
- [Visual Editing](#visual-editing)
- [FAQ](#faq)
- [Next Steps](#next-steps)

---

## First Use

### 1. Download model files manually if needed

If automatic model download fails on the first run, you can download the model folder manually.

**Model download mirrors**:

- **Quark Drive**
  - Link: `https://pan.quark.cn/s/e4e8d1635bf1`
  - Code: `e77d39EiKf`

- **China Mobile Cloud**
  - Link: `https://yun.139.com/shareweb/#/w/i/2qidZUhfLSS6i`
  - Code: `ahbl`

- **China Unicom Cloud**
  - Link: `https://pan.wo.cn/s/1r1h4V35426`
  - Code: `FaQS`

- **Baidu Netdisk**
  - Link: `https://pan.baidu.com/s/17YIs2nvgUAapcTI1i0ncFA?pwd=3w3u`
  - Code: `3w3u`

**How to use the downloaded files**:

1. Download the `models` folder
2. Put `models` in the program root directory, next to `app.exe` or `步骤2-启动Qt界面.bat`
3. Launch the app again

> 💡 If automatic download works correctly, you do not need to do this manually.

### 2. Start the program

Choose the matching entry for your installation:

**Script install**

- Double-click `步骤2-启动Qt界面.bat`

**Packaged release**

- Double-click `app.exe`

**Source install**

- Run:

  ```bash
  py -3.12 -m desktop_qt_ui.main
  ```

On the first run, the app will:

- Load AI models, which can take 3 to 5 minutes
- Initialize translation backends
- Open the main window

### 3. CPU build users must disable GPU

If you are using the CPU package or do not have a compatible NVIDIA GPU:

1. Open `Settings`
2. Open `General`
3. Turn off `Use GPU`

> ⚠️ Turning on `Use GPU` on a CPU-only setup can crash the app.

### 4. Set the output directory

1. Stay on `Translation Interface`
2. Find `Output Directory:`
3. Click `Browse...`
4. Choose where translated results should be saved

---

## Basic Operations

### Add images

You can add input pages in three ways:

1. **Use `Add Files`**
   - Click `Add Files`
   - Select one or more images

2. **Use `Add Folder`**
   - Click `Add Folder`
   - Select a folder that contains images
   - The app scans supported files automatically

3. **Drag and drop**
   - Drag images or folders directly into the file list
   - Multiple items are supported

**Supported formats**: `.png`, `.jpg`, `.jpeg`, `.jfif`, `.webp`, `.avif`, `.bmp`, `.tiff`, `.tif`, `.heic`, `.heif`

### Choose a translator

1. Open `Settings`
2. Open `Translation`
3. Find `Translator`
4. Recommended first choices:
   - `OpenAI High Quality`
   - `Gemini High Quality`
   - `OpenAI` or `Google Gemini` if you want a lighter text-only workflow

> 💡 Important:
> - High-quality translators require multimodal models
> - DeepSeek and similar text-only models do not work as `OpenAI High Quality` / `Gemini High Quality`
> - Online translators require API setup in `API Management`
> - Google Cloud or Vertex-related API keys can also be entered directly in the Gemini fields; keep the default official Base URL

### Choose the target language

1. Open `Settings`
2. Open `Translation`
3. Find `Target Language`
4. Common choices:
   - `Simplified Chinese`
   - `Traditional Chinese`
   - `English`

### Start translation

Before starting, make sure:

- ✓ `Use GPU` is configured correctly
- ✓ `Output Directory:` is set
- ✓ Images have been added
- ✓ `Translator` and `Target Language` are selected

Then:

1. Return to `Translation Interface`
2. If needed, choose a value in `Translation Workflow Mode:`
3. Click `Start Translation`

During processing:

- The progress bar shows current progress
- The log panel prints runtime messages
- You can click `Stop Translation` to interrupt the task

After completion:

- Results are saved automatically to the output directory
- You can open them in `Editor View` for manual cleanup

---

## Choosing a Translator

### Online translators

These require API keys.

| Translator | Type | Cost | Quality | Notes |
|--------|------|------|------|------|
| `OpenAI` | GPT-family text models | Medium | High | Easy to configure |
| `Google Gemini` | Gemini-family text models | Low to medium | High | Easy to configure |
| `Sakura` | Japanese-oriented | Medium | High | Good for Japanese-focused workflows |

### High-quality translators

These use image context and usually give the best results.

| Translator | Typical model type | Strength |
|--------|------|------|
| `OpenAI High Quality` | GPT-4o-class multimodal | Best context understanding |
| `Gemini High Quality` | Gemini multimodal | Strong image-aware translation |

**Why use high-quality translators**

- 📸 They can use the image itself as context
- 🎯 They handle scene-dependent text more accurately
- 📝 They can process batches with better story-level consistency
- 🔧 They work well with custom prompts and glossary extraction

### Configure online translators

If the translator requires keys:

1. Open `API Management`
2. Fill the required environment values such as:
   - `OpenAI API Key`
   - `OpenAI Model`
   - `OpenAI API Base`
   - `Gemini API Key`
   - `Gemini Model`
   - `Vertex API Key`
   - `Vertex Model`
3. Return to `Translation Interface`

Detailed API setup:

**📚 API Configuration Guide** → [./API_CONFIG.md](./API_CONFIG.md)

Recommended models:

- Best quality: multimodal OpenAI or Gemini models
- Budget text-only path: DeepSeek-compatible text models

---

## Common Settings

### Translation workflow modes

The current `Translation Workflow Mode:` list in the Qt UI includes:

1. `Normal Translation`
2. `Export Translation`
3. `Export Original Text`
4. `Translate JSON Only`
5. `Import Translation and Render`
6. `Colorize Only`
7. `Upscale Only`
8. `Inpaint Only`
9. `Replace Translation`

The main button text changes with the mode:

- `Start Translation`
- `Export Translation`
- `Generate Original Text Template`
- `Start JSON Translation`
- `Import Translation and Render`
- `Start Colorizing`
- `Start Upscaling`
- `Start Inpainting`
- `Start Replace Translation`

**How they are used**

- `Normal Translation`: the standard end-to-end translation flow
- `Export Translation`: translate, then export text for later review
- `Export Original Text`: OCR only, for manual translation workflows
- `Translate JSON Only`: translate existing JSON text content without detection, OCR, or rendering
- `Import Translation and Render`: reuse existing translation data and render again
- `Colorize Only`: colorization only
- `Upscale Only`: super-resolution only
- `Inpaint Only`: erase text and repair the image without rendering translation
- `Replace Translation`: apply translation extracted from translated images to matching raw pages

**Detailed workflow reference** → [./WORKFLOWS.md](./WORKFLOWS.md)

### OCR model selection

Open:

1. `Settings`
2. `OCR`
3. `OCR Model`

Common choices:

- `48px`: default recommendation
- `48px_ctc`: comparison variant of 48px
- `mocr`: Manga OCR, especially useful for Japanese manga
- `paddleocr`: multilingual OCR
- `paddleocr_korean`: often good for Korean comics
- `paddleocr_latin`: recommended for English and Latin-script text
- `paddleocr_thai`: recommended for Thai
- `paddleocr_vl`: best accuracy but highest resource cost
- `openai_ocr`: OCR through an OpenAI-compatible multimodal API
- `gemini_ocr`: OCR through Gemini multimodal API

Recommended Japanese combo if `Enable Hybrid OCR` is on:

- `48px + mocr`

If you use `openai_ocr` or `gemini_ocr`:

- Fill OCR API keys in `API Management`
- The fixed prompt entry is `AI OCR Prompt`
- The actual prompt file is `dict/ai_ocr_prompt.yaml`
- AI OCR is requested per text box, so request-based billing can add up quickly

### Keep Source Language

Open:

1. `Settings`
2. `Translation`
3. Find `Keep Source Language`

This controls which OCR results continue through later processing after region merging.

Typical use case:

- For English-release Japanese manga, set it to `ENG` so only English text is inpainted, translated, and rendered
- Non-matching regions stay unchanged and are not inpainted, translated, or rendered

Behavior notes:

- Latin-only text is prioritized as `ENG`
- Text containing kana is prioritized as `JPN`
- Han-only text shared across Chinese and Japanese is treated as shared CJK text, so `CHS`, `CHT`, and `JPN` can all keep it

To disable the filter:

- Set it to `No Filter`

### AI colorizer and AI renderer

Relevant locations:

- `Settings` -> `Mode Specific` for colorization
- `Settings` -> `Typesetting` for rendering

Available API-based options:

- `OpenAI Colorizer`
- `Gemini Colorizer`
- `OpenAI Renderer`
- `Gemini Renderer`

Notes:

- `AI Colorizer Prompt` uses `dict/ai_colorizer_prompt.yaml`
- `AI Renderer Prompt` uses `dict/ai_renderer_prompt.yaml`
- `AI Renderer Concurrency` controls concurrent page render requests
- AI renderer sends numbered boxes and the corresponding translated text together

### Font management

Install the desired font in the operating system, or copy a `.ttf`, `.otf`, or
`.ttc` file into the project `fonts` directory. Then select it from `Settings`
-> `Typesetting` -> `Font`. Use `Open Directory` beside the selector to access
the project directory; reopen the dropdown to refresh the combined font list.

### Prompt management

Open `Prompt Management` if you want to manage translation prompts.

Available actions include:

- `New`
- `Copy`
- `Rename`
- `Delete`
- `Refresh`
- `Open Directory`
- `Edit Prompt`
- `Apply Selected Prompt`

### Filter list

The app can skip watermark-like text, ads, or other unwanted OCR regions through a filter list.

Relevant UI entries:

- `Enable Filter List`
- `Edit Filter List`

Storage:

- `config/filter_list.json`

Two filter modes:

- `contains`: fuzzy match
- `exact`: exact match

If OCR text matches the filter:

- The region is skipped completely
- It will not be translated, erased, or rendered

---

## Visual Editing

If you want to adjust the result after translation:

### Open the editor

1. Finish a translation task
2. Open the result in `Editor View`
3. Use the left-side file list to switch images

### Common editor operations

**Region tools**

- `No Selection`
- `Brush`
- `Eraser`
- Right-click blank canvas space and choose `Add Text Box`, then drag with the left mouse button to draw the new text box.
- With the selection tool active, drag from blank canvas space with the left mouse button to box-select multiple text boxes.

Shortcuts:

- `1` / `2` / `3`: switch the Mask / Paint / Clone Stamp tab
- `Q`: choose the first tool (No Selection) on the current tab
- `W`: choose the second tool (Brush or Clone Stamp) on the current tab
- `E`: choose the third tool (Eraser) on the current tab

**Text actions**

- `Recognize`
- `Translate`
- `Placeholder`
- `Newline↵`

**Property area**

The right panel is `Property Editor`, where you can edit:

- `Original Text:`
- `Translated Text:`
- `Font`
- `Font Size`
- `Font Color`
- `Stroke Color`
- `Stroke Width`
- `Line Spacing`
- `Letter Spacing`
- `Alignment`
- `Direction`

**Preview and export**

- `Generate Preview`
- `Fit to Window`
- `Compare with Original (Two Panels)`
- `Export Image`
- `Save JSON`

**Image navigation**

- `A`: previous image
- `D`: next image

**History and region actions**

- `Ctrl+Z`: undo
- `Ctrl+Y`: redo
- `Ctrl+A`: select all text boxes on the current image (selects text when a text input is focused)
- `Ctrl+C`: copy selected region
- `Ctrl+V`: paste region or style
- `Delete`: delete selected region
- `Ctrl+Q`: export current image

**Mouse wheel shortcuts**

- `Ctrl + wheel`: scale selected text boxes and their font sizes proportionally
- `Shift + wheel`: change mask brush size
- plain wheel: zoom the view

> 💡 Single-key shortcuts such as `A`, `D`, `1`, `2`, `3`, `Q`, `W`, and `E` are context-aware. If focus is inside a text input, those keys type normally instead of triggering tab, tool, or image switches.

**Editor-related details** → [./FEATURES.md](./FEATURES.md)

---

## FAQ

### Q1: Translation is too slow

Possible reasons:

1. **You are using the CPU path**
   - CPU is much slower than a compatible GPU

2. **Images are very large**
   - Open `Settings` -> `Detection`
   - Reduce `Detection Size`
   - Open `Settings` -> `Inpainting`
   - Reduce `Inpainting Size`

### Q2: Translation quality is not good

Try this:

1. Switch to `OpenAI High Quality`, `Gemini High Quality`, or `Vertex High Quality`
2. Use `Export Original Text` for a manual translation workflow
3. Edit the result in `Editor View`
4. Use prompt files from `Prompt Management`

### Q3: Some text is not detected

Try this:

1. Turn on `Verbose Logging`
2. Open `Settings` -> `Detection`
3. Tune:
   - `Text Threshold`
   - `Box Generation Threshold`
   - `Unclip Ratio`
   - `Detection Size`
4. If necessary, fix the result manually in `Editor View`

Detailed debug reference:

**Debugging Guide** → [../DEBUGGING.md](../DEBUGGING.md)

### Q4: GPU mode crashes

Try this:

1. Check whether the GPU and driver support CUDA 13.0 by default, or CUDA 12.6 for the alternate package
   - RTX 50 series cards must use the NVIDIA GPU build
2. Update the NVIDIA driver
3. Enable `Disable ONNX GPU Acceleration` in `Settings` -> `General`
4. Fall back to the CPU build if the machine is not compatible

### Q5: How do I translate an entire folder

1. Click `Add Folder`
2. Select the folder
3. Confirm your translator and output path
4. Click `Start Translation`

If you want to tune throughput:

- Open `Settings` -> `General`
- Adjust `Batch Size`
- Optionally enable `Concurrent Batch Processing`

---

## Next Steps

To learn more:

- [Features](./FEATURES.md)
- [Workflows](./WORKFLOWS.md)
- [Settings Reference](./SETTINGS.md)
- [Debugging Guide](../DEBUGGING.md)

---

Back to [README_EN](../../README_EN.md)
