# Settings Reference

This document fully explains the program's settings and parameters.

To stay aligned with the current desktop UI, this English version uses the actual current UI labels from the project `i18n`. The original Chinese document groups items as `Basic Settings`, `Advanced Settings`, and `Options`, but in the current UI those controls are mainly distributed across:

- `Translation Interface`
- `Settings`
  - `General`
  - `Recognition`
  - `Translation`
  - `Inpainting`
  - `Typesetting`
  - `Mode Specific`
- `API Management`
- `Prompt Management`
- `Font Management`

Where the original Chinese document used older UI wording, this English version keeps the full explanation but updates the visible button names and locations to match the current UI.

---

## Parameter Details

The original Chinese document grouped the interface into three tabs. In the current desktop UI, the same settings are spread across the pages listed above, but the underlying config items and behavior are the same.

---

## Basic Settings

### Translator Settings

- **`Translator` (`translator`)**: choose the translation engine.
  - Current UI location: `Translation Interface` -> `Translator`
  - Online translators include `OpenAI`, `Google Gemini`, `Vertex`, `DeepL`, `Baidu`, and others.
  - High-quality translators include `OpenAI High Quality`, `Gemini High Quality`, and `Vertex High Quality` and are generally recommended.

- **`Target Language` (`target_lang`)**: target output language for translation.
  - Current UI location: `Settings` -> `Translation` -> `Target Language`
  - Common choices include `Simplified Chinese`, `Traditional Chinese`, `English`, `Japanese`, `Korean`, and more.

- **`Keep Source Language` (`keep_lang`)**: after text-box merging and before translation starts, only keep text regions detected as the specified source language.
  - Current UI location: `Settings` -> `Translation` -> `Keep Source Language`
  - Default: `none` / `No Filter`
  - Use case: when translating English-release Japanese manga, keep only English text and try to skip Japanese titles, sound effects, or decorative text that should stay unchanged.
  - Pipeline stage: this only affects the normal `OCR -> text-box merge -> translation` main flow, and runs after region merging but before translation.
  - English rule: pure Latin letters, numbers, spaces, and common English punctuation are prioritized as `ENG`.
  - Japanese rule: text containing hiragana or katakana is prioritized as `JPN`.
  - Shared CJK rule: if text contains only Han characters and no kana, the app does not force a strict Chinese-vs-Japanese split. It treats it as shared CJK text, so `CHS`, `CHT`, and `JPN` all keep it. This avoids accidental filtering of short terms such as `開始` or `決戦`.
  - Usage suggestions:
    - for English comics, `paddleocr_latin` is recommended, together with `Keep Source Language = ENG`
    - if you do not want to filter any text, keep it at `none`

- **`Enable Streaming` (`enable_streaming`)**: control whether supported translators prefer streaming responses.
  - Current UI location: `Settings` -> `Translation` -> `Enable Streaming`
  - Applies to: `OpenAI`, `Google Gemini`, `Vertex`, `OpenAI High Quality`, `Gemini High Quality`, and `Vertex High Quality`
  - Default: enabled (`true`)
  - When enabled: the app prefers the unified streaming transport layer and receives incremental responses in real time. If streaming fails, it falls back to a normal request automatically.
  - When disabled: the app always uses non-streaming requests and no longer attempts streaming.
  - Use case: if a proxy, relay service, local compatibility layer, or API gateway handles streaming poorly, disabling this option can improve compatibility.

- **`Don't Skip Target Lang` (`no_text_lang_skip`)**: do not skip text that is already in the target language.
  - Current UI location: `Settings` -> `Translation` -> `Don't Skip Target Lang`
  - When enabled: even if OCR or language detection says the text is already in the target language, it still goes through the translation flow.
  - Coverage: normal batch mode, high-quality batch mode, and the `batch_concurrent` concurrent pipeline.
  - Same-language results are no longer misclassified as "already skipped" during post-processing just because the source and target text match.

- **`Custom Prompt` (`high_quality_prompt_path`)**: custom prompt file path.
  - Internal config key: `translator.high_quality_prompt_path`
  - Applies to: `OpenAI`, `Google Gemini`, `Vertex`, `OpenAI High Quality`, `Gemini High Quality`, and `Vertex High Quality`
  - Default: `dict/prompt_example.yaml`
  - You can create new `.yaml` or `.json` files under `dict/`
  - Standard YAML or JSON format is enough; YAML is preferred when both exist
  - The app rescans the `dict` directory whenever the prompt selection UI is opened
  - After adding a new prompt file, you can see it immediately without restarting
  - Current UI note: in the current desktop UI, prompt files are mainly managed through `Prompt Management`, then applied with `Apply Selected Prompt`

- **`Auto Extract Glossary` (`extract_glossary`)**: automatically extract new terms from translation results.
  - Current UI location: `Settings` -> `Translation` -> `Auto Extract Glossary`
  - Applies to: `OpenAI`, `Google Gemini`, `Vertex`, `OpenAI High Quality`, `Gemini High Quality`, and `Vertex High Quality`
  - When enabled, AI automatically identifies and extracts names, places, organizations, and other proper nouns.
  - Extracted terms are added to the glossary section of the current custom prompt file.
  - Later translations reuse those terms to keep wording consistent.
  - This is especially useful for long manga series, where character names and terminology should remain stable.

- **`Auto Remove Final Period` (`remove_trailing_period`)**: when the source text has no ending punctuation, automatically remove an extra period added to the translation.
  - Current UI location: `Settings` -> `Translation` -> `Auto Remove Final Period`
  - Applies to: the main translation flow
  - Coverage: normal batch mode, high-quality batch mode, and the `batch_concurrent` concurrent pipeline
  - Ending symbols handled by this option: `。`, `.`, `．`
  - Cases not handled:
    - the source already has sentence-ending punctuation
    - repeated dots or ellipsis such as `...`
    - the editor's single-region translate button

- **`Convert to Traditional Chinese` (`convert_to_traditional`)**: convert simplified Chinese to traditional Chinese after translation.
  - Current UI location: `Settings` -> `Translation` -> `Post Processing` -> `Convert to Traditional Chinese`
  - Uses OpenCC `s2twp` configuration (Taiwan standard), which converts not only characters but also regional vocabulary (e.g. 软件 → 軟體)
  - Applies to: all translation modes (normal batch, high-quality batch, concurrent pipeline)
  - Mutually exclusive with `convert_to_simplified`; if both are enabled, this option takes priority

- **`Convert to Simplified Chinese` (`convert_to_simplified`)**: convert traditional Chinese to simplified Chinese after translation.
  - Current UI location: `Settings` -> `Translation` -> `Post Processing` -> `Convert to Simplified Chinese`
  - Uses OpenCC `t2s` configuration
  - Applies to: all translation modes (normal batch, high-quality batch, concurrent pipeline)
  - Mutually exclusive with `convert_to_traditional`; if both are enabled, `convert_to_traditional` takes priority

- **`Use Custom API Params` (`use_custom_api_params`)**: enable custom API parameters.
  - Current UI location: `Settings` -> `General` -> `Use Custom API Params`
  - Applies to: translation, AI OCR, AI rendering, and AI colorization
  - When enabled, the app reads custom parameters from `examples/custom_api_params.json` and passes them to enabled AI APIs
  - Click the `Edit` button to create and open the config file automatically
  - The file uses standard JSON format and takes effect dynamically because it is reloaded when translation runs
  - Typical use cases:
    - control special parameters for local models such as Ollama, for example disabling thinking mode
    - adjust temperature, max tokens, and similar API parameters
    - pass provider-specific options for a specific model
  - Recommended grouped structure:
    ```json
    {
      "translator": {
        "thinking": {"type": "disabled"}
      },
      "ocr": {
        "response_format": {"type": "json_object"}
      },
      "render": {
        "quality": "high"
      },
      "colorizer": {
        "size": "1536x1536"
      }
    }
    ```
  - If a parameter should be sent to every AI backend, place it under `common`:
    ```json
    {
      "common": {
        "timeout": 120
      },
      "translator": {
        "thinking": {"type": "disabled"}
      }
    }
    ```
  - Legacy compatibility: if keys are written directly at the top level, the app treats them as `common`
  - Example for disabling translator thinking mode:
    ```json
    {
      "translator": {
        "thinking": {"type": "disabled"},
        "thinking_budget": 0
      }
    }
    ```

- **`Max Requests Per Minute` (`max_requests_per_minute`)**: maximum number of requests per minute.
  - Current UI location: `Settings` -> `Translation` -> `Max Requests Per Minute`
  - `0` means unlimited

### CLI Options

- **`Verbose Logging` (`verbose`)**: output detailed debug information.
  - Current UI location: `Settings` -> `General` -> `Verbose Logging`
  - Default: disabled (`false`)
  - When enabled:
    - the log panel shows more `DEBUG` level details for detection, OCR, rendering, and other intermediate stages
    - each task creates debug intermediate files under `result/<timestamp>-<image>-<target>-<translator>/`
    - the UI writes a runtime log file to `result/log_<timestamp>.txt`
  - When disabled:
    - the interface mostly shows `INFO` level logs and stays cleaner for daily use
  - Suggestions:
    - keep it off for routine translation
    - turn it on temporarily when debugging missed detection, OCR mistakes, or layout problems
    - clean old logs afterward so the `result/` directory does not keep growing

- **`Use GPU` (`use_gpu`)**: enable GPU acceleration.
  - Current UI location: `Settings` -> `General` -> `Use GPU`

- **`Disable ONNX GPU Acceleration` (`disable_onnx_gpu`)**: disable ONNX Runtime GPU acceleration and force `CPUExecutionProvider`.
  - Current UI location: `Settings` -> `General` -> `Disable ONNX GPU Acceleration`
  - Default: disabled (`false`)
  - Use case: ONNX Runtime can have compatibility, driver, or provider initialization problems in GPU mode
  - Important note: this only affects the ONNX Runtime branch, not the program-wide GPU switch
  - Recommendation: if an ONNX model crashes or fails only in GPU mode, try enabling this first

- **`Retry Attempts` (`attempts`)**: retry count for errors.
  - Current UI location: `Settings` -> `General` -> `Retry Attempts`
  - `-1` means unlimited retries

- **`Ignore Errors` (`ignore_errors`)**: ignore errors and continue processing.
  - Current UI location: `Settings` -> `General` -> `Ignore Errors`

- **`Context Pages` (`context_size`)**: translation context page count for multi-page joint translation.
  - Current UI location: `Settings` -> `Translation` -> `Context Pages`

- **`Output Format` (`format`)**: output image format.
  - Current UI location: `Settings` -> `General` -> `Output Format`
  - Choices: `PNG`, `JPEG`, `WEBP`, `Not Specified` to keep the original format

- **`Overwrite Existing Files` (`overwrite`)**: overwrite existing translated files.
  - Current UI location: `Settings` -> `General` -> `Overwrite Existing Files`

- **`Skip Images Without Text` (`skip_no_text`)**: skip images where no text was detected.
  - Current UI location: `Settings` -> `General` -> `Skip Images Without Text`

- **`Editable Image` (`save_text`)**: save translation results to JSON files for later editing.
  - Current UI location: `Settings` -> `General` -> `Editable Image`
  - Output path: `source_image_dir/manga_translator_work/json/`
  - When enabled, the app also saves repaired images to `source_image_dir/manga_translator_work/inpainted/`
  - In `Import Translation` (`load_text`) mode, a new repaired image is generated only if inpainting actually runs again

- **`Import Translation` (`load_text`)**: load translation results from JSON and render them directly.
  - Current UI entry: `Translation Workflow Mode:` -> `Import Translation and Render`
  - Requires matching JSON data to already exist
  - This mode always starts from the original image and does not run colorization or upscaling
  - If the JSON already contains a saved mask, that mask is reused directly
  - If the JSON lacks a mask and `Import Fixed YOLO Boxes` (`import_yolo_labels`) is enabled, the app reruns detection on the original image to generate a mask
  - If an older repaired image already exists, it is not reused as detection input; it is only reused as a render base when possible

- **`Translate JSON Only` (`translate_json_only`)**: extract original text from existing JSON, run translation, and write the result back into the JSON.
  - Current UI entry: `Translation Workflow Mode:` -> `Translate JSON Only`
  - Requires matching JSON data to already exist
  - Does not run detection, OCR, inpainting, or rendering
  - Good for workflows where original text was exported earlier and only the translation content needs updating
  - After the JSON is written successfully, the matching `_original.txt` file is deleted

- **`Export Original Text` (`template`)**: export original text to a text file for manual translation.
  - Current UI entry: `Translation Workflow Mode:` -> `Export Original Text`

- **`Image Save Quality` (`save_quality`)**: JPEG save quality from `0` to `100`.
  - Current UI location: `Settings` -> `General` -> `Image Save Quality`

- **`Batch Size` (`batch_size`)**: batch processing size.
  - Current UI location: `Settings` -> `General` -> `Batch Size`
  - Default: `1`
  - For high-quality translators (`OpenAI High Quality` / `Gemini High Quality` / `Vertex High Quality`), this controls how many images are sent at once
  - Larger values make translation faster but consume more tokens
  - Recommended range: `1-10`

- **`Concurrent Batch Processing` (`batch_concurrent`)**: enable batch concurrent processing.
  - Current UI location: `Settings` -> `General` -> `Concurrent Batch Processing`

- **`Export Editable PSD` (`export_editable_psd`)**: export layered PSD files.
  - Current UI location: `Settings` -> `General` -> `Export Editable PSD`
  - Photoshop is required
  - The export contains:
    - original image layer
    - inpainted image layer
    - editable text layers
  - The original layer prefers the colorized or upscaled base image from `manga_translator_work/editor_base/`
  - The inpainted layer prefers the current-session inpainted image, then falls back to `manga_translator_work/inpainted/`
  - Export path: `source_image_dir/manga_translator_work/psd/`

- **`PSD Default Font` (`psd_font`)**: font used for text layers in Photoshop.
  - Current UI location: `Settings` -> `General` -> `PSD Default Font`
  - Supports either the display name or the PostScript name
  - If left empty, Photoshop uses its own default font

- **`Generate PSD Script Only` (`psd_script_only`)**: only generate the `.jsx` script and do not launch Photoshop automatically.
  - Current UI location: `Settings` -> `General` -> `Generate PSD Script Only`
  - Does not directly generate a PSD file
  - Script path: `source_image_dir/manga_translator_work/psd/`
  - Useful if you want to review or run the export script manually

- **`Unload Models After Translation` (`unload_models_after_translation`)**: unload all models after translation to release memory.
  - Current UI location: `Settings` -> `General` -> `Unload Models After Translation`
  - Default: disabled
  - Benefit: frees VRAM and memory more thoroughly, which is helpful on low-VRAM machines
  - Drawback: the next translation task must load models again

- **`Export Translation` (`generate_and_export`)**: generate translation results and export them.
  - Current UI entry: `Translation Workflow Mode:` -> `Export Translation`

- **`Colorize Only` (`colorize_only`)**: perform colorization only and do not translate.
  - Current UI entry: `Translation Workflow Mode:` -> `Colorize Only`

- **`Upscale Only` (`upscale_only`)**: perform super-resolution only and do not translate.
  - Current UI entry: `Translation Workflow Mode:` -> `Upscale Only`

- **`Inpaint Only` (`inpaint_only`)**: detect text and output only the repaired clean image.
  - Current UI entry: `Translation Workflow Mode:` -> `Inpaint Only`
  - This mode does not render translated text

- **`Save to Source Directory` (`save_to_source_dir`)**: output translation results to the original image directory.
  - Current UI location: `Settings` -> `General` -> `Save to Source Directory`
  - When enabled, output goes to `source_image_dir/manga_translator_work/result/`
  - This makes translated files easier to manage and find
  - Especially useful in batch workflows that should preserve the source folder structure

- **`Replace Translation Mode` (`replace_translation`)**: extract translation data from already translated images and apply it to raw images.
  - Current UI entry: `Translation Workflow Mode:` -> `Replace Translation`
  - The app automatically matches text regions between raw images and translated images
  - Supports many-to-one matching, where multiple raw boxes can map to one translated box
  - Automatically saves optimized masks so the Qt editor can skip mask optimization later
  - Supports editable PSD export
  - Does not support parallel processing and automatically uses sequential processing
  - How to use it:
    1. prepare raw pages and their matching translated pages
    2. put the translated pages in `source_image_dir/manga_translator_work/translated_images/`
    3. choose `Replace Translation` from `Translation Workflow Mode:`
    4. add the raw images
    5. the app extracts OCR results from the translated pages and applies them automatically

---

## Advanced Settings

### Detector Settings

- **`Text Detector` (`detector`)**: text detection algorithm.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Text Detector`
  - `default`: default detector (`DBNet + ResNet34`)
  - `ctd`: comic text detector
  - `craft`: CRAFT detector

- **`Detection Size` (`detection_size`)**: image scale size used during detection.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Detection Size`
  - Default is usually `2048`
  - Larger values are more accurate but slower

- **`Text Threshold` (`text_threshold`)**: text detection confidence threshold.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Text Threshold`
  - Range: `0-1`
  - Larger values are stricter

- **`Box Generation Threshold` (`box_threshold`)**: confidence threshold used to generate text boxes.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Box Generation Threshold`
  - Lower values detect more boxes

- **`Unclip Ratio` (`unclip_ratio`)**: control how far detected text boxes are expanded.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Unclip Ratio`

- **`Min Box Area Ratio` (`min_box_area_ratio`)**: minimum detected box area relative to the total image pixels.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Min Box Area Ratio`
  - Default: `0.0009` = `0.09%`
  - Filters out boxes that are too small
  - Larger values filter more aggressively and remove small text boxes
  - Smaller values keep more small text boxes
  - Suggested range: `0.0005-0.002` (`0.05%-0.2%`)

- **`Import Fixed YOLO Boxes` (`import_yolo_labels`)**: import YOLO annotation boxes from a fixed directory.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Import Fixed YOLO Boxes`
  - Fixed path: `source_image_dir/manga_translator_work/yolo_labels/<same_image_name>.txt`
  - Class labels are ignored and all boxes in the file are imported
  - In the normal translation flow: the detector-generated mask is preserved, but later OCR and text-box merging use the imported boxes
  - In `Export Original Text` / `Export Translation`: the imported boxes are used directly and the mask is not saved to JSON
  - In `Import Translation and Render`: the JSON text boxes are not overwritten; imported YOLO boxes are only used when the JSON is missing a mask and one must be generated

- **`Enable YOLO Detection` (`use_yolo_obb`)**: use YOLO oriented bounding boxes as assisted detection.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `Enable YOLO Detection`
  - Helps improve detection accuracy

- **`YOLO Confidence Threshold` (`yolo_obb_conf`)**: confidence threshold for YOLO-assisted detection.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `YOLO Confidence Threshold`
  - Larger values are stricter

- **`YOLO IoU` (`yolo_obb_iou`, legacy parameter)**: older Chinese documentation described this as the IoU threshold used to judge YOLO box overlap.
  - Current UI note: this parameter has been removed from the current app and is no longer shown in `Settings`
  - Removal note: `detector.yolo_obb_iou` was cleaned up because the current `yolo26obb` flow uses end-to-end post-processing and no longer uses this option
  - If you are comparing older configs or screenshots, this is why you can no longer find a matching control in the current desktop UI

- **`YOLO Overlap Removal Threshold` (`yolo_obb_overlap_threshold`)**: overlap threshold used to remove overlapping YOLO boxes.
  - Current UI location: `Settings` -> `Recognition` -> `Detection` -> `YOLO Overlap Removal Threshold`

### Inpainter Settings

- **`Inpainting Model` (`inpainter`)**: image inpainting algorithm.
  - Current UI location: `Settings` -> `Inpainting` -> `Inpainting Model`
  - `lama_large`: large LaMa model, recommended and highest quality
  - `lama_mpe`: LaMa MPE model, faster
  - `default`: AOT inpainter

- **`Inpainting Size` (`inpainting_size`)**: image size used during inpainting.
  - Current UI location: `Settings` -> `Inpainting` -> `Advanced` -> `Inpainting Size`
  - Larger values usually improve quality but increase runtime

- **`Inpainting Precision` (`inpainting_precision`)**: precision setting.
  - Current UI location: `Settings` -> `Inpainting` -> `Advanced` -> `Inpainting Precision`
  - `fp32`: single precision, most accurate and slowest
  - `fp16`: half precision, balanced
  - `bf16`: BFloat16, recommended when supported

- **`Force Use PyTorch Inpainting` (`force_use_torch_inpainting`)**: force PyTorch for inpainting.
  - Current UI location: `Settings` -> `Inpainting` -> `Advanced` -> `Force Use PyTorch Inpainting`
  - By default, CPU mode prefers the ONNX engine because it is usually faster
  - When enabled, the app always uses the PyTorch engine for inpainting
  - Use case: ONNX has problems or higher precision is needed
  - This option does not matter in GPU mode because GPU already uses PyTorch

### Renderer Settings

- **`Renderer` (`renderer`)**: rendering engine.
  - Current UI location: `Settings` -> `Typesetting` -> `Renderer`
  - `default`: default renderer
  - `manga2eng_pillow`: Manga2Eng Pillow renderer
  - `openai_renderer`: render a whole page through an OpenAI image API using a cleaned numbered page plus a combined prompt
  - `gemini_renderer`: render a whole page through a Gemini image API using a cleaned numbered page plus a combined prompt

- **`AI Renderer Prompt`**: fixed prompt file for `OpenAI Renderer` / `Gemini Renderer`.
  - Current UI location: `Settings` -> `Typesetting` -> `AI Renderer Prompt`
  - Click `Edit` in the Qt UI to modify it
  - Fixed path: `dict/ai_renderer_prompt.yaml`
  - Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
  - Format: YAML with top-level key `ai_renderer_prompt`
  - The real request automatically combines:
    - the cleaned page with numbered boxes
    - the translated text matching each number
  - Sound effects and onomatopoeia are also sent to the AI renderer if they have translated text

- **`AI Renderer Concurrency` (`ai_renderer_concurrency`)**: maximum concurrent full-page render requests for `OpenAI Renderer` / `Gemini Renderer`.
  - Current UI location: `Settings` -> `Typesetting` -> `AI Renderer Concurrency`
  - In batch mode this limits how many full-page render requests are sent at the same time
  - Only shown in the Qt desktop UI, not on the web admin config page

- **`Layout Mode` (`layout_mode`)**: text layout mode.
  - Current UI location: `Settings` -> `Typesetting` -> `Layout Mode`
  - `smart_scaling`: `Smart Scaling`
  - `strict`: `Strict Boundary`
  - `balloon_fill`: `Smart Bubble`, recommended

- **`Alignment` (`alignment`)**: text alignment mode.
  - Current UI location: `Settings` -> `Typesetting` -> `Alignment`
  - `auto`: `Auto`
  - `left`: `Left`
  - `center`: `Center`
  - `right`: `Right`

- **`Text Direction` (`direction`)**: text direction.
  - Current UI location: `Settings` -> `Typesetting` -> `Text Direction`
  - `auto`: auto-detect
  - `horizontal`: horizontal layout
  - `vertical`: vertical layout

- **`Font Path` (`font_path`)**: font file path for a custom font.
  - Internal config key: `render.font_path`
  - The app can load new `.ttf`, `.otf`, or `.ttc` files from the `fonts` directory
  - The font list is rescanned whenever the font selection UI is opened
  - New fonts appear without restarting
  - Current UI note: in the current desktop UI, default font selection is mainly handled in `Font Management`, then applied with `Apply Selected Font`

- **`Disable Font Border` (`disable_font_border`)**: disable text border / stroke.
  - Current UI location: `Settings` -> `Typesetting` -> `Disable Font Border`

- **`Stroke Width Ratio` (`stroke_width`)**: text stroke width relative to font size.
  - Current UI location: `Settings` -> `Typesetting` -> `Stroke Width Ratio`
  - Default: `0.07` (`7%`)
  - Range: `0.0-1.0`
  - Set to `0` to fully disable the stroke effect, same as enabling `Disable Font Border`
  - Suggested range: `0.05-0.15` (`5%-15%`)
  - Larger values make a thicker border, smaller values make a thinner one

- **`AI Line Breaking` (`disable_auto_wrap`)**: disable normal auto-wrap when AI line breaking is being used.
  - Current UI location: `Settings` -> `Typesetting` -> `AI Line Breaking`
  - Enabling this turns off the standard auto-wrap behavior
  - Commonly used together with the AI line-break related settings below

- **`Font Size Offset` (`font_size_offset`)**: font size offset.
  - Current UI location: `Settings` -> `Typesetting` -> `Font Size Offset`
  - Positive values enlarge text, negative values shrink it

- **`Minimum Font Size` (`font_size_minimum`)**: minimum font size limit.
  - Current UI location: `Settings` -> `Typesetting` -> `Minimum Font Size`

- **`Maximum Font Size` (`max_font_size`)**: maximum font size limit.
  - Current UI location: `Settings` -> `Typesetting` -> `Maximum Font Size`

- **`Uppercase` (`uppercase`)**: convert output to uppercase.
  - Current UI location: `Settings` -> `Typesetting` -> `Uppercase`

- **`Lowercase` (`lowercase`)**: convert output to lowercase.
  - Current UI location: `Settings` -> `Typesetting` -> `Lowercase`

- **`Disable Hyphenation` (`no_hyphenation`)**: disable hyphenation-based line breaking.
  - Current UI location: `Settings` -> `Typesetting` -> `Disable Hyphenation`

- **`Bubble Layout (Force Horizontal)` (`bubble_layout_english`)**: enable bubble-shape layout for all languages and force horizontal rendering.
  - Current UI location: `Settings` -> `Typesetting` -> `Bubble Layout (Force Horizontal)`
  - Default: disabled (`false`)
  - English already uses this layout by default; this switch mainly affects Korean, Chinese, and other languages
  - When enabled: the app uses the balloon mask extracted from the original image to determine line breaks for all languages, and forces horizontal text direction regardless of the original text orientation
  - Use case: when translating manga into Korean or other languages and you want the text to follow the speech bubble shape with horizontal layout, similar to how English typesetting works

- **`Font Color` (`font_color`)**: font color in hexadecimal, for example `#FFFFFF`.
  - Current UI location: `Settings` -> `Typesetting` -> `Font Color`

- **`Line Spacing` (`line_spacing`)**: line spacing multiplier.
  - Current UI location: `Settings` -> `Typesetting` -> `Line Spacing`
  - Default: `1.0`
  - Range: `0.1-5.0`

- **`Letter Spacing` (`letter_spacing`)**: letter spacing multiplier.
  - Current UI location: `Settings` -> `Typesetting` -> `Letter Spacing`
  - Default: `1.0`
  - Range: `0.1-5.0`
  - `1.0` matches the old default rendering behavior
  - This affects layout, text-box size calculation, and final rendering together
  - Can be set globally and also overridden per region in the editor

- **`Font Size` (`font_size`)**: fixed font size override.
  - Current UI location: `Settings` -> `Typesetting` -> `Font Size`

- **`Auto Rotate Symbols` (`auto_rotate_symbols`, legacy wording)**: the original Chinese document also described this setting as automatic symbol rotation.
  - Legacy meaning: automatically rotate punctuation such as `！？` inside vertical text so it displays correctly
  - Current UI note: in the current desktop UI, the same option is labeled `Horizontal in Vertical`

- **`Horizontal in Vertical` (`auto_rotate_symbols`)**: current UI label for the same vertical-text symbol handling option.
  - Current UI location: `Settings` -> `Typesetting` -> `Horizontal in Vertical`
  - Automatically keeps short horizontal symbols and fragments readable inside vertical text
  - This covers punctuation such as `！？` and similar symbols

- **`Right to Left` (`rtl`)**: enable right-to-left layout.
  - Current UI location: `Settings` -> `Typesetting` -> `Right to Left`

- **`Font Scale Ratio` (`font_scale_ratio`)**: global font scale ratio.
  - Current UI location: `Settings` -> `Typesetting` -> `Font Scale Ratio`

- **`Center in Bubble` (`center_text_in_bubble`)**: center text blocks inside the speech bubble.
  - Current UI location: `Settings` -> `Typesetting` -> `Center in Bubble`

- **`AI Line Break Auto Enlarge` (`optimize_line_breaks`)**: enable AI line-break optimization that automatically adjusts font size to reduce broken lines.
  - Current UI location: `Settings` -> `Typesetting` -> `AI Line Break Auto Enlarge`
  - Requires `OpenAI` or `Google Gemini` translators
  - AI automatically optimizes line breaks to improve readability

- **`AI Line Break Check` (`check_br_and_retry`)**: check the AI line-break result and retry if needed.
  - Current UI location: `Settings` -> `Typesetting` -> `AI Line Break Check`
  - The app automatically retries if the line-break result does not meet expectations

- **`Don't Expand Box on Auto Enlarge` (`strict_smart_scaling`)**: strict smart-scaling mode for AI line breaks.
  - Current UI location: `Settings` -> `Typesetting` -> `Don't Expand Box on Auto Enlarge`
  - Keeps the original text-box size unchanged and only shrinks the font to fit

- **`Enable Direct Paste Mode` (`enable_template_alignment`)**: enable direct paste mode for `Replace Translation`.
  - Current UI location: `Settings` -> `Mode Specific` -> `Replace Translation` -> `Enable Direct Paste Mode`
  - Default: disabled
  - Function: match by coordinates and crop the region directly from the translated image, then paste it into the raw image
  - Use case: keep the original font, style, symbols, and sound effects from the translated page
  - Note: only works in the `Replace Translation` workflow

- **`Paste Mode Connect Distance Ratio` (`paste_connect_distance_ratio`)**: distance ratio for connecting nearby mask regions.
  - Relative to the long side of the image
  - Default: `0.03` (`3%`)
  - Current UI note: this older parameter is not currently exposed in the desktop `Settings` page

- **`Paste Mode Mask Dilation Pixels` (`paste_mask_dilation_pixels`)**: pixels added to enlarge the mask before paste mode runs.
  - Current UI location: `Settings` -> `Mode Specific` -> `Replace Translation` -> `Paste Mode Mask Dilation Pixels`
  - Default: `10`
  - Set to `0` to disable dilation

### Super-Resolution Settings

- **`Upscaling Model` (`upscaler`)**: super-resolution model.
  - Current UI location: `Settings` -> `Mode Specific` -> `Upscaling` -> `Upscaling Model`
  - `waifu2x`: Waifu2x super-resolution model
  - `realcugan`: Real-CUGAN super-resolution model, recommended
  - `mangajanai`: MangaJaNai, best quality but most demanding
    - automatically detects color vs black-and-white pages and chooses the appropriate model
    - color pages use IllustrationJaNai
    - black-and-white pages use MangaJaNai
  - Other upscalers can also exist depending on the build

- **`Upscale Ratio` (`upscale_ratio`)**: super-resolution scale factor.
  - Current UI location: `Settings` -> `Mode Specific` -> `Upscaling` -> `Upscale Ratio`
  - General choices: `Not Use`, `2`, `3`, `4`
  - Current UI note: when `Upscaling Model` is `Real-CUGAN`, this dropdown doubles as the Real-CUGAN model selector

- **`Real-CUGAN Model` (`realcugan_model`)**: Real-CUGAN model selection, only effective when `Upscaling Model = Real-CUGAN`.
  - Current UI note: in the current desktop UI, this is folded into the dynamic `Upscale Ratio` dropdown
  - `2x` series: `2x-Conservative`, `2x-Conservative-Pro`, `2x-No Denoise`, `2x-Denoise1x`, `2x-Denoise2x`, `2x-Denoise3x`, `2x-Denoise3x-Pro`
  - `3x` series: `3x-Conservative`, `3x-Conservative-Pro`, `3x-No Denoise`, `3x-No Denoise-Pro`, `3x-Denoise3x`, `3x-Denoise3x-Pro`
  - `4x` series: `4x-Conservative`, `4x-No Denoise`, `4x-Denoise3x`
  - Pro models give better quality but can be slower
  - Larger denoise levels are more suitable for noisy images

- **`Tile Size` (`tile_size`)**: tile processing size, where `0` means no split.
  - Current UI location: `Settings` -> `Mode Specific` -> `Upscaling` -> `Tile Size (0=No Split)`
  - Default: `0`
  - Suggested range: `200-800`
  - Use: split large images into tiles to reduce VRAM usage
  - Smaller tiles use less memory but are slower

- **`Revert Upscaling` (`revert_upscaling`)**: restore the original resolution after translation so the image does not stay enlarged.
  - Current UI location: `Settings` -> `Mode Specific` -> `Upscaling` -> `Revert Upscaling`

### Colorizer Settings

- **`Colorization Model` (`colorizer`)**: colorizer type.
  - Current UI location: `Settings` -> `Mode Specific` -> `Colorization` -> `Colorization Model`
  - `none`: no colorization
  - `openai_colorizer`: use an OpenAI-compatible / SiliconFlow / DashScope-native image API for whole-page colorization, switching request format automatically based on `API Base URL`
  - `gemini_colorizer`: use a Gemini image API for whole-page colorization
  - Other colorizers may also exist depending on the build

- **`Colorization Size` (`colorization_size`)**: size used for colorization.
  - Current UI location: `Settings` -> `Mode Specific` -> `Colorization` -> `Colorization Size`
  - Larger values improve quality but are slower

- **`Denoise Strength` (`denoise_sigma`)**: denoise strength after colorization.
  - Current UI location: `Settings` -> `Mode Specific` -> `Colorization` -> `Denoise Strength`

- **`AI Colorizer Prompt`**: fixed prompt file for `OpenAI Colorizer` / `Gemini Colorizer`.
  - Current UI location: `Settings` -> `Mode Specific` -> `Colorization` -> `AI Colorizer Prompt`
  - Click `Edit` in the Qt UI to modify it
  - Fixed path: `dict/ai_colorizer_prompt.yaml`
  - Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
  - Format: YAML with top-level key `ai_colorizer_prompt`

- **`AI Colorizer Concurrency` (`ai_colorizer_concurrency`, legacy parameter)**: older Chinese documentation described this as the maximum number of concurrent `OpenAI Colorizer` / `Gemini Colorizer` whole-page requests.
  - Legacy behavior: in batch mode, it limited how many colorization requests were sent at the same time
  - Legacy UI note: the older desktop UI exposed it only in the Qt desktop app, not in the web admin config page
  - Current UI note: the current desktop UI no longer exposes this older parameter and instead shows `AI Colorizer History Pages`

- **`AI Colorizer History Pages` (`ai_colorizer_history_pages`)**: attach previously colorized pages as image-only references.
  - Current UI location: `Settings` -> `Mode Specific` -> `Colorization` -> `AI Colorizer History Pages`
  - Set `0` to disable it
  - The multi-image prompt labels the current page as `Image 1`, and later pages as reference or previously colorized images
  - Current UI note: older Chinese docs described `ai_colorizer_concurrency`, but the current desktop UI now exposes history-page context instead

---

## Options

### OCR Settings

- **`OCR Model` (`ocr`)**: OCR recognition model.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `OCR Model`
  - `32px`: legacy lightweight model, good as a compatibility fallback
  - `48px`: default model, recommended balance of speed and accuracy
  - `48px_ctc`: CTC variant of `48px`, good for comparison but not always more accurate
  - `mocr`: Manga OCR model, commonly used for Japanese manga
  - `paddleocr`: general multilingual model
  - `paddleocr_korean`: recommended for Korean
  - `paddleocr_latin`: recommended for Latin-script text and especially English
  - `paddleocr_thai`: recommended for Thai
  - `paddleocr_vl`: PaddleOCR-VL-1.5, best quality but most resource-intensive; best used together with a language hint or custom prompt
  - `openai_ocr`: per-box OCR through an OpenAI-compatible multimodal API; text color is still extracted locally by the `48px` model
  - `gemini_ocr`: per-box OCR through a Gemini multimodal API; text color is still extracted locally by the `48px` model
  - AI OCR note: quality is often best, but the gap versus local OCR is usually not huge; because it runs once per text box, it consumes many requests and is not recommended when the provider bills per request
  - Recommendations:
    - Japanese manga: `48px` or `mocr`
    - Korean comics: `paddleocr_korean`
    - English: `paddleocr_latin`
    - Thai: `paddleocr_thai`

- **`AI OCR Prompt`**: fixed prompt file for `OpenAI OCR` / `Gemini OCR`.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `AI OCR Prompt`
  - Click `Edit` in the Qt UI to modify it
  - Fixed path: `dict/ai_ocr_prompt.yaml`
  - Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
  - YAML file with top-level key `ai_ocr_prompt`
  - The app no longer switches between multiple OCR prompt files through a dropdown and there is no "save as"
  - If the older `dict/ai_ocr_prompt.json` still exists locally, the app migrates it into YAML on first use

- **AI OCR environment variables**: API OCR reads dedicated OCR interface settings first.
  - OpenAI OCR: `OCR_OPENAI_API_KEY`, `OCR_OPENAI_MODEL`, `OCR_OPENAI_API_BASE`
  - Gemini OCR: `OCR_GEMINI_API_KEY`, `OCR_GEMINI_MODEL`, `OCR_GEMINI_API_BASE`
  - If OCR-specific variables are empty, the app falls back to the regular `OPENAI_*` or `GEMINI_*` variables used for translation
  - Google Cloud or Vertex-related API keys can also be entered in `OCR_GEMINI_*` or the regular `GEMINI_*` fields
  - Keep `OCR_GEMINI_API_BASE` / `GEMINI_API_BASE` empty for the default official host, or keep `https://generativelanguage.googleapis.com`

- **AI colorization environment variables**: API colorization reads dedicated colorization settings first.
  - OpenAI Colorizer: `COLOR_OPENAI_API_KEY`, `COLOR_OPENAI_MODEL`, `COLOR_OPENAI_API_BASE`
  - Gemini Colorizer: `COLOR_GEMINI_API_KEY`, `COLOR_GEMINI_MODEL`, `COLOR_GEMINI_API_BASE`
  - If colorization-specific variables are empty, the app falls back to the regular `OPENAI_*` or `GEMINI_*` variables
  - Google Cloud or Vertex-related API keys can also be entered in `COLOR_GEMINI_*` or the regular `GEMINI_*` fields
  - Keep `COLOR_GEMINI_API_BASE` / `GEMINI_API_BASE` empty for the default official host, or keep `https://generativelanguage.googleapis.com`
  - If `COLOR_OPENAI_API_BASE` matches different backends, the app automatically switches request formats:
    - SiliconFlow: `https://api.siliconflow.cn/v1`
    - DashScope native: `https://dashscope.aliyuncs.com/api/v1` or `https://dashscope-intl.aliyuncs.com/api/v1`
    - Volcano Engine or other OpenAI-compatible endpoints
  - If `Use Custom API Params` is enabled, the `colorizer` group is automatically mapped into the corresponding backend request body

- **AI rendering environment variables**: API rendering reads dedicated render settings first.
  - OpenAI Renderer: `RENDER_OPENAI_API_KEY`, `RENDER_OPENAI_MODEL`, `RENDER_OPENAI_API_BASE`
  - Gemini Renderer: `RENDER_GEMINI_API_KEY`, `RENDER_GEMINI_MODEL`, `RENDER_GEMINI_API_BASE`
  - If render-specific variables are empty, the app falls back to the regular `OPENAI_*` or `GEMINI_*` variables
  - Google Cloud or Vertex-related API keys can also be entered in `RENDER_GEMINI_*` or the regular `GEMINI_*` fields
  - Keep `RENDER_GEMINI_API_BASE` / `GEMINI_API_BASE` empty for the default official host, or keep `https://generativelanguage.googleapis.com`
  - If `RENDER_OPENAI_API_BASE` matches different backends, the app also switches request formats automatically, using rules similar to `OpenAI Colorizer`
  - If `Use Custom API Params` is enabled, the `render` group is automatically mapped into the corresponding backend request body

- **`Enable Hybrid OCR` (`use_hybrid_ocr`)**: use two OCR models together to improve accuracy.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `Enable Hybrid OCR`
  - Recommended Japanese manga combination: `48px + mocr`

- **`Secondary OCR` (`secondary_ocr`)**: the second OCR model used when hybrid OCR is enabled.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `Secondary OCR`
  - For Japanese manga, if the primary OCR is `48px`, `mocr` is a good secondary OCR

- **`Minimum Text Length` (`min_text_length`)**: minimum text length; shorter text is filtered out.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `Minimum Text Length`

- **`Ignore Non-Bubble Text` (`ignore_bubble`)**: intelligently skip text outside speech bubbles.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `Ignore Non-Bubble Text`
  - Function: automatically identify and skip text outside dialogue bubbles, such as titles, sound effects, or background text
  - Supported OCR models: all current models, including `48px`, `48px_ctc`, `32px`, `mocr`, and `paddleocr`
  - Range: `0-1`, where `0` disables the feature
  - Threshold behavior:
    - `0`: disabled, keep all text
    - `0.01-0.3`: loose filtering
    - `0.3-0.7`: medium filtering
    - `0.7-1.0`: strict filtering and may accidentally filter valid bubbles
  - Working principle:
    - measure the black-vs-white ratio around the edge of a text box
    - normal white bubble: edge is almost all white -> keep
    - normal black bubble: edge is almost all black -> keep
    - non-bubble area: edge mixes black and white -> skip
    - colored text: if detected as colored, skip
  - Use cases:
    - pages with many sound effects or titles that should not be translated
    - workflows that only want dialogue bubbles
    - reducing unnecessary translation work
  - Log output example: `[FILTERED] Region X ignored - Non-bubble area detected`

- **`Keep Dilation Inside Bubble Mask` (`limit_mask_dilation_to_bubble_mask`)**: keep mask dilation from growing outside the bubble area.
  - Current UI location: `Settings` -> `Inpainting` -> `Keep Dilation Inside Bubble Mask`
  - Default: `false`
  - When enabled: the post-processing stage constrains the final repair mask using the model bubble area, preventing repair from spilling outside the bubble
  - Use case: protect bubble borders and avoid unwanted repair outside the dialogue box

- **`Text Region Min Probability` (`prob`)**: OCR recognition probability threshold.
  - Current UI location: `Settings` -> `Recognition` -> `Advanced` -> `Text Region Min Probability`

- **`Merge Distance Tolerance` (`merge_gamma`)**: distance tolerance for merging text regions.
  - Current UI location: `Settings` -> `Recognition` -> `Advanced` -> `Merge Distance Tolerance`

- **`Merge Outlier Tolerance` (`merge_sigma`)**: outlier tolerance for merging text regions.
  - Current UI location: `Settings` -> `Recognition` -> `Advanced` -> `Merge Outlier Tolerance`

- **`Merge Edge Ratio Threshold` (`merge_edge_ratio_threshold`)**: edge-ratio threshold for region merging.
  - Current UI location: `Settings` -> `Recognition` -> `Advanced` -> `Merge Edge Ratio Threshold`

- **`Require Full Wrap In Special Pre-Merge` (`merge_special_require_full_wrap`)**: control whether model-label-assisted pre-merge is enabled.
  - Current UI location: `Settings` -> `Recognition` -> `OCR` -> `Require Full Wrap In Special Pre-Merge`
  - Enabled by default:
    - run model-assisted pre-merge first
    - the `changfangtiao` group stays separate
    - `balloon` / `qipao` / `other` groups are handled separately
    - unlabeled boxes must be fully enclosed by a target labeled box to join pre-merge
    - pre-merged boxes no longer participate in later raw merging
  - Disabled:
    - skip model-assisted pre-merge
    - all boxes go directly into the original merging algorithm

### Global Parameters

- **`Kernel Size` (`kernel_size`)**: convolution kernel size used for text erasure.
  - Current UI location: `Settings` -> `Inpainting` -> `Advanced` -> `Kernel Size`
  - Default: `3`

- **`Mask Dilation Offset` (`mask_dilation_offset`)**: mask dilation offset.
  - Current UI location: `Settings` -> `Inpainting` -> `Mask Dilation Offset`
  - Default: `70`
  - Controls how far the text-erasure mask expands

### Filter List

The program supports skipping specific text regions through a filter list, for example watermarks or ad text.

- **File Path**: `examples/filter_list.json`
- Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
- **Format**: JSON object with `contains` and `exact` arrays
- **Working Principle**: if the OCR original text matches the filter, that region is skipped completely and is not translated, erased, or rendered
- **Auto Creation**: the app creates the file automatically on startup if it does not exist; older `filter_list.txt` files are migrated automatically

Example:

```json
{
  "contains": [
    "pixiv",
    "twitter",
    "@username",
    "ad",
    "promo"
  ],
  "exact": []
}
```

---

## Path Configuration Notes

### Relative path base

- **Packaged Build**: relative to the `_internal` directory
- **Development Build**: relative to the project root

### Common paths

**Custom prompt paths** (`dict` directory):

- **System prompts** built into the program. They support `.yaml`, `.yml`, and `.json`, with YAML preferred:
  - `dict/system_prompt_hq.yaml` - system prompt for high-quality translation
  - `dict/system_prompt_line_break.yaml` - system prompt for AI line breaking
  - `dict/glossary_extraction_prompt.yaml` - system prompt for glossary extraction
  - `dict/system_prompt_hq_format.yaml` - system prompt for high-quality translation output format
  - `dict/ai_ocr_prompt.yaml` - fixed OCR prompt for `OpenAI OCR` / `Gemini OCR` / `Vertex OCR`
  - `dict/ai_colorizer_prompt.yaml` - fixed colorization prompt for `OpenAI Colorizer` / `Gemini Colorizer` / `Vertex Colorizer`
  - `dict/ai_renderer_prompt.yaml` - fixed rendering prompt for `OpenAI Renderer` / `Gemini Renderer` / `Vertex Renderer`
- **User custom prompts** selected in the UI:
  - `dict/prompt_example.yaml` - prompt example
  - you can add your own `.yaml` or `.json` prompt files here
- Applies to: `OpenAI`, `Google Gemini`, `Vertex`, `OpenAI High Quality`, `Gemini High Quality`, and `Vertex High Quality`
- Prompt files can customize translation style, glossary, and context instructions

**How to add a custom prompt**

> Note: this prompt system is used by **OpenAI**, **Google Gemini**, **Vertex**, **OpenAI High Quality**, **Gemini High Quality**, and **Vertex High Quality**.

> Important: if you installed with the script version, do **not** edit `prompt_example.yaml` directly, because updates may overwrite it. Create a new file instead.

Current UI method:

1. Open `Prompt Management`
2. Click `Open Directory` to open the `dict` directory
3. Create a new `.yaml` or `.json` file there, for example `my_prompt.yaml`
4. Open `prompt_example.yaml` and copy its contents into the new file
5. Edit the new file and fill in character names, glossary entries, and other project notes
6. Return to `Prompt Management`, select the new file, and click `Apply Selected Prompt`

Prompt example (YAML, JSON is also supported):

```yaml
# Custom system prompt. If left empty, only the built-in base prompt is used.
# The {{{target_lang}}} placeholder is replaced with the target language name.
system_prompt: |
  You are a professional manga translator who is fluent in multiple languages.
  Your task is to translate manga text into natural and fluent target-language output.

  Rules:
  1. Preserve tone, style, and emotion from the original text.
  2. Strictly follow the glossary below when available.
  3. If no special translation is required, use the most natural common wording.

# Glossary to keep names and terms consistent
glossary:
  Person:
    - original: "ましろ"
      translation: "Mashiro"
  Location: []
  Org: []
  Item: []
  Skill: []
  Creature: []
```

Field explanation:

- `system_prompt`: system prompt that defines translation style and rules
- `glossary`: glossary containing different categories of proper nouns
  - `Person`: character names
  - `Location`: place names
  - `Org`: organization names
  - `Item`: item names
  - `Skill`: skill names
  - `Creature`: creature names
- Each glossary entry contains:
  - `original`
  - `translation`

Lazy shortcut:

- if you do not want to write the glossary by hand, you can ask another AI to generate it from:
  - the original and translated title of the work
  - the original and translated names of main characters
  - the structure of `prompt_example.yaml` as the template

**Export original text template path**:

- Default: `examples/translation_template.json`
- Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
- Used to customize the exported original-text format
- Defines a text-box structure that the program repeats automatically

**Filter list path**:

- Default: `examples/filter_list.json`
- Legacy compatibility: `examples/filter_list.txt` is migrated automatically to JSON
- Used to skip watermarks, ads, and other text that should not be translated

**Text replacement rules path**:

- Default: `examples/text_replacements.yaml`
- Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
- Used for custom text replacements applied after translation and before rendering
- Supports three groups: `common` (all directions), `horizontal` (horizontal text only), `vertical` (vertical text only)
- **Execution order**: The `common` group runs first, followed by either `horizontal` or `vertical` depending on the text direction.
- **Cascading mechanism**: Rules execute from top to bottom. Text generated by an earlier replacement can be replaced again by subsequent matching rules.
- Each rule format:
  ```yaml
  - pattern: "match content"
    replace: "replacement content"
    regex: true          # optional, default false
    enabled: true        # optional, default true
    comment: "note"      # optional
  ```
- Replacement results are written into JSON; the editor export skips them automatically
- Can be edited visually in the Qt UI under `Data Management` -> `Replacement Rules`

**Custom API params path**:

- Default: `examples/custom_api_params.json`
- Note: In CLI mode, if this file cannot be found, please start the application once first, and it will be generated automatically from a built-in template.
- Used for extra request parameters for translation, AI OCR, AI rendering, and AI colorization
- Recommended groups: `translator`, `ocr`, `render`, `colorizer`
- Optional shared group: `common`
- Legacy compatibility: top-level JSON keys are treated as `common`

**Font path**:

- Default: the `fonts` directory
- You can also point to a specific font file such as `fonts/my_font.ttf`
- Supports `.ttf`, `.otf`, and `.ttc`

**How to add a custom font**

Current UI method:

1. Open `Font Management`
2. Either:
   - click `Import`, or
   - click `Open Directory` and copy the font file into `fonts/`
3. Font filenames are best kept in English if possible
4. Select the new font in `Font Management`
5. Click `Apply Selected Font`

**Output folder**:

- Default: the same directory as the input file
- You can change it on `Translation Interface` through `Output Directory:`

---

Back to [README_EN](../../README_EN.md)
