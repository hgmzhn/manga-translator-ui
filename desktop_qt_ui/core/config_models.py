from typing import Any, Dict, List, Optional, Union

from manga_translator.custom_api_params import migrate_legacy_custom_api_params_config
from pydantic import BaseModel, Field, model_validator

from theme_registry import VALID_THEME_PREFERENCES as REGISTERED_THEME_PREFERENCES
from theme_registry import VALID_THEMES as REGISTERED_THEMES

VALID_LAYOUT_MODES = {"smart_scaling", "strict", "balloon_fill"}


class TranslatorSettings(BaseModel):
    translator: str = "openai_hq"
    target_lang: str = "CHS"
    keep_lang: str = "none"
    enable_streaming: bool = True
    no_text_lang_skip: bool = False
    # 相对路径，后端会用 BASE_PATH 拼接（打包后=app.exe 同级，开发时=项目根目录）
    high_quality_prompt_path: Optional[str] = "dict/prompt_example.yaml"
    extract_glossary: bool = False
    max_requests_per_minute: int = 0
    remove_trailing_period: bool = False
    convert_to_traditional: bool = False
    convert_to_simplified: bool = False


class OcrSettings(BaseModel):
    ocr: str = "48px"
    use_hybrid_ocr: bool = True
    secondary_ocr: str = "mocr"
    min_text_length: int = 0
    ignore_bubble: float = 0.0
    use_model_bubble_filter: bool = False
    model_bubble_overlap_threshold: float = 0.1
    use_model_bubble_repair_intersection: bool = False
    limit_mask_dilation_to_bubble_mask: bool = False
    prob: float = 0.1
    merge_gamma: float = 0.8
    merge_sigma: float = 2.5
    merge_edge_ratio_threshold: float = 0.0
    merge_special_require_full_wrap: bool = True
    ocr_vl_language_hint: str = "auto"
    ocr_vl_custom_prompt: Optional[str] = None
    ai_ocr_concurrency: int = 1
    ai_ocr_custom_prompt: Optional[str] = None


class DetectorSettings(BaseModel):
    detector: str = "default"
    detection_size: int = 2048
    det_rearrange_min_effective_short_side: int = 341
    text_threshold: float = 0.5
    box_threshold: float = 0.5
    unclip_ratio: float = 2.5
    import_yolo_labels: bool = False
    use_yolo_obb: bool = False
    use_sfx_filter: bool = False
    sfx_filter_include_bubble_text: bool = False
    yolo_obb_conf: float = 0.4
    yolo_obb_overlap_threshold: float = 0.1
    min_box_area_ratio: float = (
        0.0009  # 最小检测框面积占比（相对图片总像素），默认0.09%
    )


class InpainterSettings(BaseModel):
    inpainter: str = "lama_mpe"
    inpainting_size: int = 2048
    inpainting_precision: str = "fp32"
    force_use_torch_inpainting: bool = False
    solid_fill_pure_bubbles: bool = False
    per_block_inpainting: bool = False


class RenderSettings(BaseModel):
    renderer: str = "default"
    alignment: str = "auto"
    disable_font_border: bool = False
    disable_auto_wrap: bool = True
    font_size_offset: int = 0
    font_size_minimum: int = 0
    direction: str = "auto"
    uppercase: bool = False
    lowercase: bool = False
    font_family: str = ""
    disable_system_fonts: bool = False
    no_hyphenation: bool = False
    bubble_layout_english: bool = False
    font_color: Optional[str] = None
    line_spacing: Optional[float] = 1.0  # 行间距倍率，默认1.0
    letter_spacing: Optional[float] = 1.0  # 字间距倍率，默认1.0
    font_size: Optional[int] = None
    rtl: bool = True
    layout_mode: str = "smart_scaling"
    balloon_fill_font_layout_safety: bool = False
    max_font_size: int = 0
    font_scale_ratio: float = 1.0
    center_text_in_bubble: bool = False
    optimize_line_breaks: bool = False
    semantic_linebreak: bool = False
    remove_linebreak_punctuation: bool = False
    check_br_and_retry: bool = False
    strict_smart_scaling: bool = False
    stroke_width: float = 0.07
    enable_template_alignment: bool = (
        False  # 启用模板匹配对齐（替换翻译模式）- 直接提取翻译图文字
    )
    paste_mask_dilation_pixels: int = 10  # 粘贴模式蒙版膨胀大小（像素），设为0禁用膨胀
    ai_renderer_concurrency: int = 1

    @model_validator(mode="after")
    def _validate_layout_mode(self):
        if self.layout_mode not in VALID_LAYOUT_MODES:
            raise ValueError(
                f"Invalid render.layout_mode: {self.layout_mode!r}. "
                f"Supported values: {', '.join(sorted(VALID_LAYOUT_MODES))}"
            )
        return self


class UpscaleSettings(BaseModel):
    upscaler: str = "esrgan"
    upscale_ratio: Optional[Union[int, str]] = (
        None  # 可以是数字或字符串(mangajanai: x2, x4, DAT2 x4)
    )
    realcugan_model: Optional[str] = None
    tile_size: Optional[int] = None
    revert_upscaling: bool = False


class ColorizerSettings(BaseModel):
    colorization_size: int = 576
    denoise_sigma: int = 30
    colorizer: str = "none"
    ai_colorizer_history_pages: int = 0


class CliSettings(BaseModel):
    verbose: bool = False  # 默认关闭详细日志
    attempts: int = -1
    ignore_errors: bool = False
    use_gpu: bool = True
    disable_onnx_gpu: bool = False  # 禁用 ONNX Runtime GPU 加速（强制 ONNX 走 CPU）
    context_size: int = 3
    format: str = "不指定"
    overwrite: bool = True
    skip_no_text: bool = False
    save_text: bool = True
    export_from_local_json: bool = False
    load_text: bool = False
    translate_json_only: bool = False
    template: bool = False
    save_quality: int = 100
    batch_size: int = 1
    batch_concurrent: bool = False
    generate_and_export: bool = False
    colorize_only: bool = False
    upscale_only: bool = False  # 仅超分模式
    inpaint_only: bool = False  # 仅输出修复图片模式
    save_to_source_dir: bool = (
        False  # 输出到原图目录的 manga_translator_work/result 子目录
    )
    export_editable_psd: bool = False  # 导出可编辑的PSD文件（需要Photoshop）
    psd_script_only: bool = False  # 仅生成JSX脚本而不执行Photoshop
    replace_translation: bool = (
        False  # 替换翻译模式：将一张图的翻译应用到另一张生肉图上
    )


_LEGACY_THEME_MIGRATIONS = {
    ("dark", "teal"): "ocean",
    ("gray", "green"): "forest",
    ("gray", "orange"): "sunset",
    ("dark", "rose"): "rose",
}

_ACCENT_ONLY_THEME_FALLBACKS = {
    "teal": "ocean",
    "green": "forest",
    "orange": "sunset",
    "rose": "rose",
}

_VALID_THEMES = set(REGISTERED_THEMES)
_VALID_THEME_PREFERENCES = set(REGISTERED_THEME_PREFERENCES)


class AppSection(BaseModel):
    last_open_dir: str = "."
    last_output_path: str = ""
    favorite_folders: Optional[List[str]] = None
    folder_dialog_sort: str = "name_ascending"
    theme: str = "light"  # 主题选项由 theme_registry.py 统一定义
    theme_user_preference: str = "light"
    ui_language: str = "auto"  # UI语言：auto(自动检测), zh_CN, en_US, ja_JP, ko_KR 等
    auto_check_updates: bool = True  # 启动时是否自动检查新版本
    use_system_proxy: bool = False  # 网络请求是否使用操作系统代理配置
    current_preset: str = "默认"  # 当前使用的预设名称
    editor_snap_enabled: bool = False  # 编辑器文本框移动/旋转时是否启用吸附
    editor_center_scale_enabled: bool = False  # 拖动文本框边/角时是否围绕中心对称缩放
    editor_rich_text_popup_enabled: bool = True  # 是否显示编辑器富文本浮动弹窗
    editor_rich_text_popup_pinned: bool = False  # 是否固定富文本浮窗位置并阻止自动隐藏
    editor_auto_save_on_switch: bool = True  # 切图时自动保存工程数据
    editor_auto_export_on_switch: bool = True  # 切图时自动导出渲染图片
    editor_suppress_unsaved_warning: bool = False  # 切图时不再提醒未保存编辑
    editor_auto_rich_text_rules: bool = (
        True  # 编辑译文时自动应用富文本规则（命中已带手工富文本则整段跳过）
    )
    editor_delete_and_recover: bool = False  # 删除文本框时同时移除其蒙版并恢复原图
    unload_models_after_translation: bool = (
        False  # 翻译完成后卸载模型（释放内存更彻底，但下次使用需要重新加载）
    )
    saved_colors: Optional[List[str]] = None  # 保存的常用颜色列表
    saved_style_presets: Optional[Dict[str, Dict[str, Any]]] = (
        None  # 编辑器保存的样式组合
    )
    saved_rich_text_presets: Optional[Dict[str, Dict[str, Any]]] = (
        None  # 富文本片段样式预设
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_theme_variants(cls, data: Any):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        theme_accent = normalized.get("theme_accent")
        theme_value = normalized.get("theme")
        theme_user_preference = normalized.get("theme_user_preference")

        if theme_value == "system":
            mapped_user_pref = _LEGACY_THEME_MIGRATIONS.get(
                (theme_user_preference, theme_accent)
            )
            if mapped_user_pref:
                normalized["theme_user_preference"] = mapped_user_pref
            elif (
                theme_user_preference not in _VALID_THEME_PREFERENCES
                and theme_accent in _ACCENT_ONLY_THEME_FALLBACKS
            ):
                normalized["theme_user_preference"] = _ACCENT_ONLY_THEME_FALLBACKS[
                    theme_accent
                ]
        else:
            mapped_theme = _LEGACY_THEME_MIGRATIONS.get((theme_value, theme_accent))
            if mapped_theme:
                normalized["theme"] = mapped_theme
            elif (
                theme_value not in _VALID_THEMES
                and theme_accent in _ACCENT_ONLY_THEME_FALLBACKS
            ):
                normalized["theme"] = _ACCENT_ONLY_THEME_FALLBACKS[theme_accent]

            if normalized.get("theme_user_preference") not in _VALID_THEME_PREFERENCES:
                normalized["theme_user_preference"] = normalized.get("theme", "light")

        if normalized.get("theme") not in _VALID_THEMES:
            normalized["theme"] = "light"
        if normalized.get("theme_user_preference") not in _VALID_THEME_PREFERENCES:
            normalized["theme_user_preference"] = "light"
        return normalized


class AppSettings(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    filter_text_enabled: bool = True  # 是否启用过滤列表
    kernel_size: int = 3
    mask_dilation_offset: int = 70
    use_custom_api_params: bool = False  # 是否使用自定义API参数配置文件（通用）
    translator: TranslatorSettings = Field(default_factory=TranslatorSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    detector: DetectorSettings = Field(default_factory=DetectorSettings)
    inpainter: InpainterSettings = Field(default_factory=InpainterSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    upscale: UpscaleSettings = Field(default_factory=UpscaleSettings)
    colorizer: ColorizerSettings = Field(default_factory=ColorizerSettings)
    cli: CliSettings = Field(default_factory=CliSettings)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_custom_api_params(cls, data: Any):
        return migrate_legacy_custom_api_params_config(data)
