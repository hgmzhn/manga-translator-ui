import argparse
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, PrivateAttr, model_validator

from manga_translator.custom_api_params import migrate_legacy_custom_api_params_config

VALID_LAYOUT_MODES = {"smart_scaling", "strict", "balloon_fill"}


# TODO: Refactor
class TranslatorChain:
    def __init__(self, string: str):
        """
        Parses string in form 'trans1:lang1;trans2:lang2' into chains,
        which will be executed one after another when passed to the dispatch function.
        """
        from manga_translator.translators import TRANSLATORS, VALID_LANGUAGES
        if not string:
            raise Exception('Invalid translator chain')
        self.chain = []
        self.target_lang = None
        for g in string.split(';'):
            trans, lang = g.split(':')
            translator = Translator[trans]
            if translator not in TRANSLATORS:
                raise ValueError('Invalid choice: %s (choose from %s)' % (trans, ', '.join(map(repr, TRANSLATORS))))
            if lang not in VALID_LANGUAGES:
                raise ValueError('Invalid choice: %s (choose from %s)' % (lang, ', '.join(map(repr, VALID_LANGUAGES))))
            self.chain.append((translator, lang))
        self.translators, self.langs = list(zip(*self.chain))

    def has_offline(self) -> bool:
        """
        Returns True if the chain contains offline translators.
        """
        return False

    def __eq__(self, __o: object) -> bool:
        if type(__o) is str:
            return __o == self.translators[0]
        return super.__eq__(self, __o)


def translator_chain(string):
    try:
        return TranslatorChain(string)
    except ValueError as e:
        raise argparse.ArgumentTypeError(e)
    except Exception:
        raise argparse.ArgumentTypeError(f'Invalid translator_chain value: "{string}". Example usage: --translator "openai:gemini" -l "JPN:ENG"')


def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

class Renderer(str, Enum):
    default = "default"  # Qt 离屏渲染器
    openai_renderer = "openai_renderer"
    gemini_renderer = "gemini_renderer"
    none = "none"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str) and value.lower() in {"manga2eng", "manga2eng_pillow"}:
            return cls.default
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Alignment(str, Enum):
    auto = "auto"
    left = "left"
    center = "center"
    right = "right"

class Direction(str, Enum):
    auto = "auto"
    h = "horizontal"
    v = "vertical"

class InpaintPrecision(str, Enum):
    fp32 = "fp32"
    fp16 = "fp16"
    bf16 = "bf16"

    def __str__(self):
        return self.name

class Detector(str, Enum):
    default = "default"
    dbconvnext = "dbconvnext"
    ctd = "ctd"
    craft = "craft"
    # paddle = "paddle"  # 已移除（需要 rusty_manga_image_translator）
    none = "none"

class Inpainter(str, Enum):
    default = "default"
    lama_large = "lama_large"
    lama_mpe = "lama_mpe"
    sd = "sd"
    none = "none"
    original = "original"

class Colorizer(str, Enum):
    none = "none"
    mc2 = "mc2"
    openai_colorizer = "openai_colorizer"
    gemini_colorizer = "gemini_colorizer"

class Ocr(str, Enum):
    ocr32px = "32px"
    ocr48px = "48px"
    ocr48px_ctc = "48px_ctc"
    mocr = "mocr"
    paddleocr = "paddleocr"
    paddleocr_korean = "paddleocr_korean"
    paddleocr_latin = "paddleocr_latin"
    paddleocr_thai = "paddleocr_thai"
    paddleocr_vl = "paddleocr_vl"  # PaddleOCR-VL for Manga (VLM-based OCR)
    openai_ocr = "openai_ocr"
    gemini_ocr = "gemini_ocr"

class Translator(str, Enum):
    openai = "openai"
    openai_hq = "openai_hq"
    gemini = "gemini"
    gemini_hq = "gemini_hq"
    sakura = "sakura"
    none = "none"
    original = "original"

    def __str__(self):
        return self.name

    # Map 'chatgpt' and any translator starting with 'gpt'* to 'openai'
    @classmethod
    def _missing_(cls, value):
        if value.startswith('gpt') or value == 'chatgpt':
            return cls.openai
        raise ValueError(f"{value} is not a valid {cls.__name__}")


class Upscaler(str, Enum):
    waifu2x = "waifu2x"
    esrgan = "esrgan"
    upscler4xultrasharp = "4xultrasharp"
    realcugan = "realcugan"
    mangajanai = "mangajanai"

class RenderConfig(BaseModel):
    renderer: Renderer = Renderer.default
    """Rendering engine selection."""
    force_strict_layout: bool = False
    """Force renderer to strictly adhere to the bounding box, like in --load-text mode."""
    alignment: Alignment = Alignment.auto
    """Align rendered text"""
    disable_font_border: bool = False
    """Disable font border"""
    disable_auto_wrap: bool = False
    font_size_offset: int = 0
    """Offset font size by a given amount, positive number increase font size and vice versa"""
    font_size_minimum: int = -1
    """Minimum output font size. Default is image_sides_sum/200"""
    max_font_size: int = 0
    """Maximum output font size. 0 means no limit"""
    font_scale_ratio: float = 1.0
    """Font size scale ratio. Applied before max_font_size limit"""
    center_text_in_bubble: bool = False
    """Center the text block vertically in the bubble"""
    optimize_line_breaks: bool = False
    """Automatically optimize line breaks by testing all combinations to find the best font size"""
    semantic_linebreak: bool = False
    """Use local HanLP semantic line breaking for Chinese translations without explicit [BR] markers."""
    remove_linebreak_punctuation: bool = False
    """Remove comma/period punctuation immediately before or after line break markers."""
    check_br_and_retry: bool = False
    """Check if translation contains [BR] markers when AI line breaking is enabled (regions≥2). Retry if missing."""
    strict_smart_scaling: bool = False
    """In smart_scaling mode, prevent text box expansion by skipping combinations without line breaks"""
    direction: Direction = Direction.auto
    """Force text to be rendered horizontally/vertically/none"""
    uppercase: bool = False
    """Change text to uppercase"""
    lowercase: bool = False
    """Change text to lowercase"""
    no_hyphenation: bool = False
    """If renderer should be splitting up words using a hyphen character (-)"""
    bubble_layout_english: bool = False
    """Enable bubble-based English typesetting (balloon mask line breaking) and force horizontal rendering."""
    font_family: Optional[str] = None
    """Qt font family used for rendering."""
    font_color: Optional[str] = None
    """Overwrite the text fg/bg color detected by the OCR model. Use hex string without the "#" such as FFFFFF for a white foreground or FFFFFF:000000 to also have a black background around the text."""
    line_spacing: Optional[float] = None
    """Line spacing multiplier. Default is 1.0. Actual spacing = font_size * base_spacing * multiplier (base: 0.01 for horizontal, 0.2 for vertical)."""
    letter_spacing: Optional[float] = None
    """Letter spacing multiplier. Default is 1.0. Actual glyph advance = font advance * multiplier."""
    font_size: Optional[int] = None
    """Use fixed font size for rendering"""
    rtl: bool = True
    """Right-to-left reading order for panel and text_region sorting,"""  
    layout_mode: str = 'smart_scaling'
    """The layout mode to use for rendering. Options: 'smart_scaling', 'strict', 'balloon_fill'"""
    stroke_width: float = 0.07
    """Stroke/border width ratio relative to font size. Default is 0.07 (7%). Set to 0 to disable stroke."""
    enable_template_alignment: bool = False
    """Enable template matching alignment for replace translation mode. Directly extracts text from translated image and pastes to raw image."""
    paste_mask_dilation_pixels: int = 10
    """Mask dilation size in pixels for paste mode. Default is 10. Set to 0 to disable dilation. Actual dilation = pixels // 3 iterations with 3x3 kernel."""
    ai_renderer_concurrency: int = 1
    """Maximum concurrent API requests for OpenAI/Gemini/Vertex renderers."""
    _font_color_fg = None
    _font_color_bg = None

    @model_validator(mode="after")
    def _validate_layout_mode(self):
        if self.layout_mode not in VALID_LAYOUT_MODES:
            raise ValueError(
                f"Invalid render.layout_mode: {self.layout_mode!r}. "
                f"Supported values: {', '.join(sorted(VALID_LAYOUT_MODES))}"
            )
        return self

    @property
    def font_color_fg(self):
        if self.font_color and not self._font_color_fg:
            colors = self.font_color.split(':')
            try:
                self._font_color_fg = hex2rgb(colors[0]) if colors[0] else None
                self._font_color_bg = hex2rgb(colors[1]) if len(colors) > 1 and colors[1] else None
            except Exception:
                raise Exception(
                    f'Invalid --font-color value: {self.font_color}. Use a hex value such as FF0000')
        return self._font_color_fg

    @property
    def font_color_bg(self):
        if self.font_color and not self._font_color_bg:
            colors = self.font_color.split(':')
            try:              
                self._font_color_fg = hex2rgb(colors[0]) if colors[0] else None
                self._font_color_bg = hex2rgb(colors[1]) if len(colors) > 1 and colors[1] else None
            except Exception:
                raise Exception(
                    f'Invalid --font-color value: {self.font_color}. Use a hex value such as FF0000')
        return self._font_color_bg

class UpscaleConfig(BaseModel):
    upscaler: Upscaler = Upscaler.esrgan
    """Upscaler to use. --upscale-ratio has to be set for it to take effect"""
    revert_upscaling: bool = False
    """Downscales the previously upscaled image after translation back to original size (Use with --upscale-ratio)."""
    upscale_ratio: Optional[Union[int, str]] = None
    """Image upscale ratio applied before detection. Can be int (2,3,4) or string for mangajanai (x2, x4, DAT2 x4)."""
    realcugan_model: Optional[str] = None
    """Real-CUGAN model to use when upscaler is set to realcugan"""
    tile_size: Optional[int] = None
    """Tile size for Real-CUGAN upscaling (default: 400, 0 = process full image without tiling)"""

class TranslatorConfig(BaseModel):
    translator: Translator = Translator.openai_hq
    """Language translator to use"""
    target_lang: str = 'ENG' #todo: validate VALID_LANGUAGES #todo: convert to enum
    """Destination language"""
    keep_lang: str = 'none'
    """After text merging, keep only regions detected as this source language for later processing. Filtered regions remain unchanged. Use 'none' to disable."""
    enable_streaming: bool = True
    """Enable unified streaming transport for supported AI translators."""
    no_text_lang_skip: bool = False
    """Dont skip text that is seemingly already in the target language."""
    skip_lang: Optional[str] = None
    """Skip translation if source image is one of the provide languages, use comma to separate multiple languages. Example: JPN,ENG"""
    high_quality_prompt_path: Optional[str] = None
    """Path to a JSON file containing custom prompts for high-quality translation."""
    extract_glossary: bool = False
    """Automatically extract new terms to glossary (requires high_quality_prompt_path)"""
    remove_trailing_period: bool = False
    """Remove a sentence-final period from the translation when the source text has no terminal punctuation."""
    translator_chain: Optional[str] = None
    """Output of one translator goes in another. Example: --translator-chain "openai:JPN;gemini:ENG"."""    
    selective_translation: Optional[str] = None
    """Select a translator based on detected language in image. Note the first translation service acts as default if the language isn\'t defined. Example: --translator-chain "openai:JPN;gemini:ENG".'"""
    
    # 用户级 API Key（用于 Web 服务器多用户场景）
    # 这些字段优先于环境变量，允许每个用户使用自己的 API Key
    user_api_key: Optional[str] = None
    """User-provided API key (overrides environment variable)"""
    user_api_base: Optional[str] = None
    """User-provided API base URL (overrides environment variable)"""
    user_api_model: Optional[str] = None
    """User-provided model name (overrides environment variable)"""
    
    # API请求频率限制配置
    max_requests_per_minute: int = 0
    """Maximum API requests per minute. 0 means no limit."""
    
    # 简繁体转换（翻译后处理）
    convert_to_traditional: bool = False
    """Convert simplified Chinese to traditional Chinese after translation (using OpenCC s2twp)"""
    convert_to_simplified: bool = False
    """Convert traditional Chinese to simplified Chinese after translation (using OpenCC t2s)"""

    # 译后检查配置项
    enable_post_translation_check: bool = False
    """Enable post-translation validation check"""
    post_check_max_retry_attempts: int = 3
    """Maximum retry attempts for failed translation validation"""
    post_check_repetition_threshold: int = 20
    """Minimum number of consecutive repetitions to trigger hallucination detection"""
    post_check_target_lang_threshold: float = 0.5  
    """Minimum ratio of target language in translation text for ratio check"""
    
    # 使用 PrivateAttr 确保每个实例有独立的缓存
    _translator_gen: Any = PrivateAttr(default=None)

    @property
    def translator_gen(self):
        if self._translator_gen is None:
            if self.selective_translation is not None:
                #todo: refactor TranslatorChain
                trans =  translator_chain(self.selective_translation)
                trans.target_lang = self.target_lang
                self._translator_gen = trans
            elif self.translator_chain is not None:
                trans = translator_chain(self.translator_chain)
                trans.target_lang = trans.langs[0]
                self._translator_gen = trans
            else:
                self._translator_gen = TranslatorChain(f'{str(self.translator)}:{self.target_lang}')
        return self._translator_gen


class DetectorConfig(BaseModel):
    """"""
    detector: Detector =Detector.default
    """"Text detector used for creating a text mask from an image, DO NOT use craft for manga, it\'s not designed for it"""
    detection_size: int = 2048
    """Size of image used for detection"""
    det_rearrange_min_effective_short_side: int = 341
    """Minimum effective short-side resolution preserved by long-image detection rearrange"""
    text_threshold: float = 0.5
    """Threshold for text detection"""
    import_yolo_labels: bool = False
    """Import YOLO labels from manga_translator_work/yolo_labels and use them in detection workflows"""
    use_yolo_obb: bool = False
    """Enable YOLO OBB auxiliary detector for hybrid detection"""
    yolo_obb_conf: float = 0.4
    """Confidence threshold for YOLO OBB detector"""
    yolo_obb_overlap_threshold: float = 0.1
    """Overlap ratio threshold for removing YOLO boxes (0.0-1.0). YOLO boxes with overlap >= threshold will be removed if they don't meet replacement criteria. Set to 1.0 to keep all overlapping boxes."""
    box_threshold: float = 0.7
    """Threshold for bbox generation"""
    unclip_ratio: float = 2.3
    """How much to extend text skeleton to form bounding box"""
    min_box_area_ratio: float = 0.0009
    """Minimum detection box area ratio relative to total image pixels (default 0.0009 = 0.09%)"""

class InpainterConfig(BaseModel):
    inpainter: Inpainter = Inpainter.lama_large
    """Inpainting model to use"""
    inpainting_size: int = 2048
    """Size of image used for inpainting (too large will result in OOM)"""
    inpainting_precision: InpaintPrecision = InpaintPrecision.bf16
    """Inpainting precision for lama, use bf16 while you can."""
    force_use_torch_inpainting: bool = False
    """Force use PyTorch for inpainting instead of ONNX (useful if ONNX has memory issues)"""

class ColorizerConfig(BaseModel):
    colorization_size: int = 576
    """Size of image used for colorization. Set to -1 to use full image size"""
    denoise_sigma: int = 30
    """Used by colorizer and affects color strength, range from 0 to 255 (default 30). -1 turns it off."""
    colorizer: Colorizer = Colorizer.none
    """Colorization model to use."""
    ai_colorizer_history_pages: int = 0
    """How many previously colorized pages to attach as image-only context for AI colorizers."""

class CliConfig(BaseModel):
    """CLI-specific configuration options"""
    attempts: int = -1
    """Number of retry attempts for translation. -1 means unlimited retries"""
    verbose: bool = False
    """Enable verbose logging"""
    use_gpu: bool = True
    """Use GPU for processing"""
    disable_onnx_gpu: bool = False
    """Disable ONNX Runtime GPU acceleration and force CPUExecutionProvider."""
    context_size: int = 3
    """Context size for translation"""
    batch_size: int = 1
    """Batch size for processing"""
    batch_concurrent: bool = False
    """Enable concurrent pipeline (Detection, OCR, Inpainting, Translation in parallel)"""
    format: Optional[str] = None
    """Output format"""
    save_quality: int = 100
    """Save quality for output images"""
    overwrite: bool = False
    """Overwrite existing files"""
    skip_no_text: bool = False
    """Skip images with no text"""
    save_text: bool = False
    """Save extracted text"""
    ignore_errors: bool = False
    """Ignore errors and continue processing"""
    export_editable_psd: bool = False
    """Export editable PSD file with layers (requires Photoshop)"""
    psd_font: Optional[str] = None
    """Font name for PSD export (PostScript name, e.g. ArialMT, SimHei). If not set, uses default font."""
    save_to_source_dir: bool = False
    """Save translation results to manga_translator_work/result/ subdirectory in the source image directory."""
    psd_script_only: bool = False
    """Only generate JSX script without executing Photoshop"""
    replace_translation: bool = False
    """Replace translation mode: apply translation from one image to another raw image"""
    translate_json_only: bool = False
    """Translate existing JSON only: read original text from JSON, translate, and write back JSON"""

class OcrConfig(BaseModel):
    ocr: Ocr = Ocr.ocr48px
    """Optical character recognition (OCR) model to use"""
    use_hybrid_ocr: bool = False
    """Enable hybrid OCR mode, using a secondary OCR engine if the primary one fails."""
    secondary_ocr: Ocr = Ocr.ocr48px
    """Secondary OCR to use in hybrid mode."""
    min_text_length: int = 0
    """Minimum text length of a text region"""
    ignore_bubble: float = 0.0
    """Threshold for ignoring non-bubble text areas (0-1). 0=disabled, 0.01-0.3=loose, 0.3-0.7=medium, 0.7-1.0=strict. Higher values filter more aggressively."""
    use_model_bubble_filter: bool = False
    """Enable model-based bubble filtering (MangaLens). Regions not overlapping detected bubble boxes will be filtered."""
    model_bubble_overlap_threshold: float = 0.1
    """Minimum overlap ratio (0-1) between text bbox and model-detected bubble boxes. Lower values are more permissive."""
    use_model_bubble_repair_intersection: bool = False
    """After mask refinement, keep only model bubble-mask connected components that intersect the refined mask."""
    limit_mask_dilation_to_bubble_mask: bool = False
    """Clip refined-mask connected components by model bubble mask: intersecting components keep only intersection; non-intersecting components are preserved."""
    prob: float | None = None
    """Minimum probability of a text region to be considered valid. If None, uses the model default."""
    merge_gamma: float = 0.8
    """Textline merge distance tolerance, higher is more tolerant."""
    merge_sigma: float = 2.5
    """Textline merge deviation tolerance, higher is more tolerant."""
    merge_edge_ratio_threshold: float = 0.0
    """If a box has two neighbors with edge distance ratio > this value, disconnect the larger distance edge. 0 means disabled."""
    merge_special_require_full_wrap: bool = True
    """Require unlabeled boxes to be fully wrapped by target-labeled boxes in special pre-merge groups."""
    ocr_vl_language_hint: str = 'auto'
    """PaddleOCR-VL language hint. Options: auto, multilingual, ja, ko, zh, en, fr, de, es, ru, ar."""
    ocr_vl_custom_prompt: Optional[str] = None
    """Custom PaddleOCR-VL prompt. If set, it overrides built-in prompt mode/language hint."""
    ai_ocr_concurrency: int = 1
    """Maximum concurrent API requests for OpenAI OCR and Gemini OCR."""
    ai_ocr_custom_prompt: Optional[str] = None
    """Custom prompt for API OCR backends such as OpenAI OCR and Gemini OCR."""

class Config(BaseModel):
    # General
    render: RenderConfig = RenderConfig()
    """render configs"""
    upscale: UpscaleConfig = UpscaleConfig()
    """upscaler configs"""
    translator: TranslatorConfig = TranslatorConfig()
    """tanslator configs"""
    detector: DetectorConfig = DetectorConfig()
    """detector configs"""
    colorizer: ColorizerConfig = ColorizerConfig()
    """colorizer configs"""
    inpainter: InpainterConfig = InpainterConfig()
    """inpainter configs"""
    ocr: OcrConfig = OcrConfig()
    """Ocr configs"""
    cli: CliConfig = CliConfig()
    """CLI configs"""
    # ?
    force_simple_sort: bool = False
    """Don't use panel detection for sorting, use a simpler fallback logic instead"""
    kernel_size: int = 3
    """Set the convolution kernel size of the text erasure area to completely clean up text residues"""
    mask_dilation_offset: int = 20
    """By how much to extend the text mask to remove left-over text pixels of the original image."""
    use_custom_api_params: bool = False
    """Use custom API parameters from examples/custom_api_params.json for supported AI backends."""
    _runtime_api_overrides: dict[str, dict[str, dict[str, str]]] = PrivateAttr(default_factory=dict)
    _allow_server_api_keys: bool = PrivateAttr(default=True)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_custom_api_params(cls, data):
        return migrate_legacy_custom_api_params_config(data)
