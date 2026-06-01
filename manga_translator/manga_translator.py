
import asyncio
import json
import logging
import os
import sys
import time
import traceback
import unicodedata
from typing import Any, List, Optional

import cv2
import langcodes
import matplotlib
import numpy as np
import py3langid as langid
import regex as re
import torch
from PIL import Image

from .config import Colorizer, Config, Inpainter, Renderer, Translator
from .server_paths import normalize_server_resource_path
from .utils import (
    BASE_PATH,
    Context,
    ModelWrapper,
    TextBlock,
    detect_bubbles_with_mangalens,
    dump_image,
    imwrite_unicode,
    is_valuable_text,
    load_image,
    open_pil_image,
    save_pil_image,
    sort_regions,
    visualize_textblocks,
)
from .utils.onnx_runtime import set_onnx_gpu_disabled
from .utils.text_filter import ensure_filter_list_exists, match_filter

matplotlib.use('Agg')  # 使用非GUI后端
from matplotlib import cm

from .colorization import dispatch as dispatch_colorization
from .colorization import prepare as prepare_colorization
from .colorization import unload as unload_colorization
from .detection import dispatch as dispatch_detection
from .detection.imported_yolo import (
    build_mask_from_textlines,
    load_imported_yolo_textlines,
)
from .detection import prepare as prepare_detection
from .detection import unload as unload_detection
from .inpainting import dispatch as dispatch_inpainting
from .inpainting import prepare as prepare_inpainting
from .inpainting import unload as unload_inpainting
from .mask_refinement import dispatch as dispatch_mask_refinement
from .ocr import dispatch as dispatch_ocr
from .ocr import prepare as prepare_ocr
from .ocr import unload as unload_ocr
from .rendering import dispatch as dispatch_rendering
from .textline_merge import dispatch as dispatch_textline_merge
from .translators import (
    dispatch as dispatch_translation,
)
from .translators import (
    prepare as prepare_translation,
)
from .translators import (
    unload as unload_translation,
)
from .translators.common import ISO_639_1_TO_KEEP_LANGUAGES, ISO_639_1_TO_VALID_LANGUAGES, KEEP_LANGUAGES
from .upscaling import dispatch as dispatch_upscaling
from .upscaling import prepare as prepare_upscaling
from .upscaling import unload as unload_upscaling
from .utils.path_manager import (
    find_inpainted_path,
    find_json_path,
    get_inpainted_path,
    get_json_path,
    get_original_txt_path,
    get_work_image_path,
)
from .utils.translation_text import remove_trailing_period_if_needed

# Will be overwritten by __main__.py if module is being run directly (with python -m)
logger = logging.getLogger('manga_translator')

ARCHIVE_EXTRACT_IMAGE_DIRNAME = 'original_images'
ARCHIVE_EXTRACT_META_FILENAME = '.extract_meta.json'
_KEEP_LANG_NONE_VALUES = {'', 'NONE', 'OFF', 'DISABLED'}
_DETECTED_KEEP_LANG_CODES = set(KEEP_LANGUAGES.keys())
_KEEP_LANG_CJK_SHARED = 'CJK_SHARED'
_KEEP_LANG_SHARED_CJK_TARGETS = frozenset({'CHS', 'CHT', 'JPN'})
_ENGLISH_KEEP_FILTER_PUNCTUATION = frozenset(
    ".,!?;:'\"-()[]{}<>/&@#%+*=~_|`$^\\"
    "…“”‘’–—"
)


class FileTranslationFailure(Exception):
    """Abort the current file while allowing the overall batch to continue."""

    def __init__(self, stage: str, error: Exception):
        self.stage = stage
        self.original_error = error
        message = str(error).strip() or repr(error)
        super().__init__(message)


def _resolve_archive_output_dir_from_extracted_image(image_path: str, output_folder: str) -> Optional[str]:
    """
    如果 image_path 指向输出目录中的压缩包解压图片，返回对应压缩包输出目录。
    例如: <output>/A/B/1/original_images/page.png -> <output>/A/B/1
    """
    if not image_path or not output_folder:
        return None

    image_parent = os.path.normpath(os.path.dirname(image_path))
    if os.path.basename(image_parent) != ARCHIVE_EXTRACT_IMAGE_DIRNAME:
        return None

    meta_path = os.path.join(image_parent, ARCHIVE_EXTRACT_META_FILENAME)
    if not os.path.isfile(meta_path):
        return None

    archive_output_dir = os.path.normpath(os.path.dirname(image_parent))
    output_root_abs = os.path.normcase(os.path.abspath(output_folder))
    archive_output_abs = os.path.normcase(os.path.abspath(archive_output_dir))

    try:
        common = os.path.commonpath([output_root_abs, archive_output_abs])
    except ValueError:
        return None

    if common != output_root_abs:
        return None

    return archive_output_dir


def _is_likely_english_text_for_keep_filter(text: str) -> bool:
    normalized = unicodedata.normalize('NFKC', str(text or '').strip())
    if not normalized:
        return False

    has_latin_letter = False
    for char in normalized:
        if char.isspace() or char.isdigit():
            continue
        if char in _ENGLISH_KEEP_FILTER_PUNCTUATION:
            continue

        category = unicodedata.category(char)
        if category.startswith('L') and 'LATIN' in unicodedata.name(char, ''):
            has_latin_letter = True
            continue

        return False

    return has_latin_letter


def _normalize_detected_keep_language(lang_code: Optional[str]) -> str:
    value = str(lang_code or '').strip()
    if not value:
        return 'UNKNOWN'

    upper_value = value.upper()
    if upper_value in _DETECTED_KEEP_LANG_CODES:
        return upper_value

    mapped = ISO_639_1_TO_KEEP_LANGUAGES.get(value.lower())
    if mapped:
        return mapped.upper()

    return 'UNKNOWN'


def _normalize_keep_filter_script_candidate(text: str) -> str:
    normalized = unicodedata.normalize('NFKC', str(text or '').strip())
    if not normalized:
        return ''

    normalized = re.sub(r'^[\p{P}\p{S}\s]+|[\p{P}\p{S}\s]+$', '', normalized)
    return normalized


def _detect_script_based_keep_language(text: str) -> Optional[str]:
    candidate = _normalize_keep_filter_script_candidate(text)
    if not candidate:
        return None

    meaningful_chars = []
    for char in candidate:
        category = unicodedata.category(char)
        if char.isspace() or char.isdigit() or category.startswith(('P', 'S')):
            continue
        meaningful_chars.append(char)

    if not meaningful_chars:
        return None

    meaningful_text = ''.join(meaningful_chars)
    if re.search(r'[\p{Hiragana}\p{Katakana}]', meaningful_text):
        return 'JPN'
    if re.search(r'\p{Hangul}', meaningful_text):
        return 'KOR'
    if re.fullmatch(r'\p{Han}+', meaningful_text):
        return _KEEP_LANG_CJK_SHARED

    return None


def _detect_region_keep_language(text: str) -> str:
    text = str(text or '').strip()
    if not text:
        return 'UNKNOWN'

    if _is_likely_english_text_for_keep_filter(text):
        return 'ENG'

    detected_by_script = _detect_script_based_keep_language(text)
    if detected_by_script:
        return detected_by_script

    try:
        detected_lang, _ = langid.classify(text)
    except Exception:
        return 'UNKNOWN'

    return _normalize_detected_keep_language(detected_lang)


def _keep_language_matches(detected_lang: str, keep_lang: str) -> bool:
    if detected_lang == keep_lang:
        return True
    if keep_lang in {'CHS', 'CHT'} and detected_lang in {'CHS', 'CHT'}:
        return True
    if detected_lang == _KEEP_LANG_CJK_SHARED and keep_lang in _KEEP_LANG_SHARED_CJK_TARGETS:
        return True
    return False


def set_main_logger(l):
    global logger
    logger = l

class TranslationInterrupt(Exception):
    """
    Can be raised from within a progress hook to prematurely terminate
    the translation.
    """
    pass

def load_dictionary(file_path):
    dictionary = []
    if file_path:
        path_to_check = file_path if os.path.isabs(file_path) else os.path.join(BASE_PATH, file_path)
        if os.path.exists(path_to_check):
            with open(path_to_check, 'r', encoding='utf-8') as file:
                for line_number, line in enumerate(file, start=1):
                    # Ignore empty lines and lines starting with '#' or '//'
                    if not line.strip() or line.strip().startswith('#') or line.strip().startswith('//'):
                        continue
                    # Remove comment parts
                    line = line.split('#')[0].strip()
                    line = line.split('//')[0].strip()
                    parts = line.split()
                    if len(parts) == 1:
                        # If there is only the left part, the right part defaults to an empty string, meaning delete the left part
                        pattern = re.compile(parts[0])
                        dictionary.append((pattern, '', line_number))
                    elif len(parts) == 2:
                        # If both left and right parts are present, perform the replacement
                        pattern = re.compile(parts[0])
                        dictionary.append((pattern, parts[1], line_number))
                    else:
                        logger.error(f'Invalid dictionary entry at line {line_number}: {line.strip()}')
    return dictionary

def apply_dictionary(text, dictionary):
    for pattern, value, line_number in dictionary:
        text = pattern.sub(value, text)
    return text

def parse_upscale_ratio(upscale_ratio) -> float:
    """
    解析超分倍率，支持多种格式：
    - 数字: 2, 3, 4
    - 字符串数字: "2", "3", "4"
    - mangajanai格式: "x2", "x4", "DAT2 x4"
    - realcugan格式: "2x-conservative", "3x-denoise1x" 等
    
    返回浮点数倍率，如果无法解析则返回 0
    """
    if not upscale_ratio:
        return 0
    
    try:
        if isinstance(upscale_ratio, (int, float)):
            return float(upscale_ratio)
        
        if isinstance(upscale_ratio, str):
            # 移除空格并转小写
            upscale_ratio = upscale_ratio.strip().lower()
            # 优先匹配开头的数字（如 "2x-conservative" 中的 2）
            match = re.match(r'^(\d+)x', upscale_ratio)
            if not match:
                # 如果没匹配到，尝试匹配任意位置的数字（如 "x2" 或 "DAT2 x4"）
                match = re.search(r'(\d+)', upscale_ratio)
            
            if match:
                return float(match.group(1))
        
        return 0
    except (ValueError, TypeError):
        logger.warning(f"无法解析超分倍率: {upscale_ratio}, 将忽略")
        return 0


def _parse_skip_font_scaling_flag(value, default: bool = True) -> bool:
    """Normalize skip_font_scaling values loaded from JSON."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    if value is None:
        return default
    return bool(value)

class MangaTranslator:
    verbose: bool
    ignore_errors: bool
    _gpu_limited_memory: bool
    device: Optional[str]
    kernel_size: Optional[int]
    models_ttl: int
    _progress_hooks: list[Any]
    result_sub_folder: str
    batch_size: int

    def __init__(self, params: dict = {}):
        self.pre_dict = params.get('pre_dict', None)
        self.post_dict = params.get('post_dict', None)
        self.skip_text_replacements = params.get('skip_text_replacements', False)
        self.font_path = None
        self.kernel_size = None
        self.device = None
        self.text_output_file = params.get('save_text_file', None)
        self._gpu_limited_memory = False
        self.ignore_errors = False
        self.verbose = False
        self.models_ttl = 0
        self.batch_size = 1  # 默认不批量处理
        self.disable_onnx_gpu = False

        self._progress_hooks = []
        self._add_logger_hook()
        
        # 取消检查回调（用于Web服务器等场景）
        self._cancel_check_callback = None

        params = params or {}
        
        self._batch_contexts = []  # 存储批量处理的上下文
        self._batch_configs = []   # 存储批量处理的配置
        # batch_concurrent 四并发模式（默认关闭，可通过配置开启）
        self.batch_concurrent = params.get('batch_concurrent', False)
        
        # 添加模型加载状态标志
        self._models_loaded = False
        
        self.parse_init_params(params)
        self.result_sub_folder = ''

        # The flag below controls whether to allow TF32 on matmul. This flag defaults to False
        # in PyTorch 1.12 and later.
        torch.backends.cuda.matmul.allow_tf32 = True

        # The flag below controls whether to allow TF32 on cuDNN. This flag defaults to True.
        torch.backends.cudnn.allow_tf32 = True

        self._model_usage_timestamps = {}
        self._detector_cleanup_task = None
        self.context_size = params.get('context_size', 0)
        self.all_page_translations = []
        self._original_page_texts = []  # 存储原文页面数据，用于并发模式下的上下文
        self._colorizer_history_images = []  # 存储最近已上色页面，用于 AI 上色历史参考

        # 调试图片管理相关属性
        self._current_image_context = None  # 存储当前处理图片的上下文信息
        self._saved_image_contexts = {}     # 存储批量处理中每个图片的上下文信息
        
        # 日志文件现在由UI层管理，这里不再创建
        self._log_file_path = None
        
        # 过滤列表开关（默认启用）
        self.filter_text_enabled = params.get('filter_text_enabled', True)
        
        # 确保过滤列表文件存在
        try:
            ensure_filter_list_exists()
            from .rendering.text_replacements import ensure_text_replacements_exists
            ensure_text_replacements_exists()
            from .utils.translation_template import ensure_translation_template_exists
            ensure_translation_template_exists()
        except Exception:
            pass

    def parse_init_params(self, params: dict):
        self.verbose = params.get('verbose', False)
        # font_path 优先从配置文件读取，如果没有则使用命令行参数
        self.font_path = params.get('font_path', None)
        self.models_ttl = params.get('models_ttl', 0)
        self.batch_size = params.get('batch_size', 3)  # 批量大小（翻译批次）
        disable_onnx_gpu = params.get('disable_onnx_gpu', False)
        if isinstance(disable_onnx_gpu, str):
            disable_onnx_gpu = disable_onnx_gpu.strip().lower() in ('1', 'true', 'yes', 'on')
        self.disable_onnx_gpu = bool(disable_onnx_gpu)
        set_onnx_gpu_disabled(self.disable_onnx_gpu)
        if self.disable_onnx_gpu:
            logger.info("ONNX GPU acceleration disabled; ONNX Runtime will use CPUExecutionProvider.")
        
        # batch_concurrent 四并发流水线处理（可选功能）
        # 开启后：检测、OCR、修复、翻译四并发，提升处理速度
            
        self.ignore_errors = params.get('ignore_errors', False)
        # check mps for apple silicon or cuda for nvidia
        device = 'mps' if torch.backends.mps.is_available() else 'cuda'
        self.device = device if params.get('use_gpu', False) else 'cpu'
        if self.using_gpu and ( not torch.cuda.is_available() and not torch.backends.mps.is_available()):
            # GPU不可用时，自动回退到CPU而不是抛出异常
            logger.warning(
                'CUDA or Metal compatible device could not be found in torch whilst --use-gpu was set. '
                'Automatically falling back to CPU mode.'
            )
            self.device = 'cpu'
        if params.get('model_dir'):
            ModelWrapper._MODEL_DIR = params.get('model_dir')
        #todo: fix why is kernel size loaded in the constructor
        self.kernel_size=int(params.get('kernel_size', 3))
        # Set input files
        self.input_files = params.get('input', [])
        # Set save_text
        self.save_text = params.get('save_text', False)
        # Set load_text
        self.load_text = params.get('load_text', False)
        self.translate_json_only = params.get('translate_json_only', False)
        self.save_mask = not params.get('no_save_mask', False)
        self.template = params.get('template', False)
        self.attempts = params.get('attempts', -1)
        self._attempts_override_provided = 'attempts' in params
        self.save_quality = params.get('save_quality', 100)
        self.skip_no_text = params.get('skip_no_text', False)
        self.generate_and_export = params.get('generate_and_export', False)
        self.colorize_only = params.get('colorize_only', False)
        self.upscale_only = params.get('upscale_only', False)
        self.inpaint_only = params.get('inpaint_only', False)
        
        # 替换翻译模式（从已翻译图片复制翻译数据到生肉图片）
        self.replace_translation = params.get('replace_translation', False)
        
        
        # batch_concurrent 已在初始化时设置并验证
        

        
    def _set_image_context(self, config: Config, image=None):
        """设置当前处理图片的上下文信息，用于生成调试图片子文件夹"""
        from .utils.generic import get_image_md5

        # 使用毫秒级时间戳确保唯一性
        timestamp = str(int(time.time() * 1000))
        detection_size = str(getattr(config.detector, 'detection_size', 1024))
        target_lang = getattr(config.translator, 'target_lang', 'unknown')
        translator = getattr(config.translator, 'translator', 'unknown')

        # 计算图片MD5哈希值
        if image is not None:
            file_md5 = get_image_md5(image)
        else:
            file_md5 = "unknown"

        # 生成子文件夹名：{timestamp}-{file_md5}-{detection_size}-{target_lang}-{translator}
        subfolder_name = f"{timestamp}-{file_md5}-{detection_size}-{target_lang}-{translator}"

        self._current_image_context = {
            'subfolder': subfolder_name,
            'file_md5': file_md5,
            'config': config
        }
        
    def _get_image_subfolder(self) -> str:
        """获取当前图片的调试子文件夹名"""
        if self._current_image_context:
            return self._current_image_context['subfolder']
        return ''
    
    def _save_current_image_context(self, image_md5: str):
        """保存当前图片上下文，用于批量处理中保持一致性"""
        if self._current_image_context:
            self._saved_image_contexts[image_md5] = self._current_image_context.copy()

    def _restore_image_context(self, image_md5: str):
        """恢复保存的图片上下文"""
        if image_md5 in self._saved_image_contexts:
            self._current_image_context = self._saved_image_contexts[image_md5].copy()
            return True
        return False

    @property
    def using_gpu(self):
        return self.device.startswith('cuda') or self.device == 'mps'

    async def translate(self, image: Image.Image, config: Config, image_name: str = None, skip_context_save: bool = False, save_info: dict = None) -> Context:
        """
        Translates a single image by calling translate_batch with batch_size=1.
        
        This is a compatibility wrapper. All translation logic is now unified in translate_batch().

        :param image: Input image.
        :param config: Translation config.
        :param image_name: Image file name for saving results.
        :param save_info: Save configuration (output_folder, format, etc.)
        :return: Translation context.
        """
        # Attach image_name to image object for batch processing
        if image_name and not hasattr(image, 'name'):
            image.name = image_name
        
        # Call unified batch translation with single image
        results = await self.translate_batch(
            images_with_configs=[(image, config)],
            batch_size=1,
            save_info=save_info
        )
        
        # Return the single result
        return results[0] if results else Context()

    def _apply_runtime_cli_overrides(self, config: Config) -> Config:
        if (
            config is not None
            and self._attempts_override_provided
            and hasattr(config, 'cli')
            and hasattr(config.cli, 'attempts')
        ):
            config.cli.attempts = self.attempts
        return config

    def _calculate_output_path(self, image_path: str, save_info: dict) -> str:
        """
        计算输出文件的完整路径
        
        Args:
            image_path: 输入图片的路径
            save_info: 包含输出配置的字典，包括：
                - output_folder: 输出文件夹
                - input_folders: 输入文件夹集合
                - format: 输出格式（可选）
                - save_to_source_dir: 是否输出到原图目录的 manga_translator_work/result 子目录
                
        Returns:
            str: 计算后的输出文件完整路径
        """
        output_folder = save_info.get('output_folder')
        input_folders = save_info.get('input_folders', set())
        output_format = save_info.get('format')
        save_to_source_dir = save_info.get('save_to_source_dir', False)
        
        file_path = image_path
        parent_dir = os.path.normpath(os.path.dirname(file_path))
        
        # 检查是否启用了"输出到原图目录"模式
        if save_to_source_dir:
            # 输出到原图所在目录的 manga_translator_work/result 子目录
            final_output_dir = os.path.join(parent_dir, 'manga_translator_work', 'result')
        else:
            # 原有逻辑：使用配置的输出目录
            final_output_dir = output_folder

            # 优先处理压缩包解压目录：<...>/original_images/<image>
            archive_output_dir = _resolve_archive_output_dir_from_extracted_image(file_path, output_folder)
            if archive_output_dir:
                final_output_dir = archive_output_dir
            else:
                # 计算相对路径以保持文件夹结构
                for folder in input_folders:
                    if parent_dir.startswith(folder):
                        relative_path = os.path.relpath(parent_dir, folder)
                        # Normalize path and avoid adding '.' as a directory component
                        if relative_path == '.':
                            final_output_dir = os.path.join(output_folder, os.path.basename(folder))
                        else:
                            final_output_dir = os.path.join(output_folder, os.path.basename(folder), relative_path)
                        # Normalize to use consistent separators
                        final_output_dir = os.path.normpath(final_output_dir)
                        break
        
        os.makedirs(final_output_dir, exist_ok=True)
        
        # 处理输出文件名和格式
        base_filename, _ = os.path.splitext(os.path.basename(file_path))
        if output_format and output_format.strip() and output_format.lower() != 'none':
            output_filename = f"{base_filename}.{output_format}"
        else:
            output_filename = os.path.basename(file_path)
        
        final_output_path = os.path.join(final_output_dir, output_filename)
        return final_output_path

    def _save_translated_image(
        self,
        image: Image.Image,
        output_path: str,
        image_path: str,
        overwrite: bool = True,
        mode_label: str = "BATCH",
        source_image: Optional[Image.Image] = None,
    ) -> bool:
        """
        保存翻译后的图片到指定路径
        
        Args:
            image: 要保存的PIL图片对象
            output_path: 输出文件路径
            image_path: 原始图片路径（用于更新翻译映射表）
            overwrite: 是否覆盖已存在的文件
            mode_label: 模式标签（用于日志）
            
        Returns:
            bool: 是否成功保存
        """
        if not overwrite and os.path.exists(output_path):
            logger.info(f"  -> ⚠️ [{mode_label}] Skipping existing file: {os.path.basename(output_path)}")
            return False
        
        try:
            save_pil_image(
                image,
                output_path,
                source_image=source_image,
                quality=self.save_quality,
            )
            logger.info(f"  -> ✅ [{mode_label}] Saved successfully: {os.path.basename(output_path)}")
            
            # 更新翻译映射表
            self._update_translation_map(image_path, output_path)
            return True
        except Exception as e:
            logger.error(f"Error saving image to {output_path}: {e}")
            return False
    
    def _save_and_cleanup_context(self, ctx: Context, save_info: dict, config: Config = None, mode_label: str = "BATCH") -> bool:
        """
        统一的保存和清理方法：保存翻译结果、导出PSD并清理内存
        
        Args:
            ctx: Context对象
            save_info: 保存信息字典
            config: Config对象（用于PSD导出）
            mode_label: 模式标签（用于日志）
            
        Returns:
            bool: 是否成功保存
        """
        if not save_info or not ctx.result:
            return False
        
        try:
            overwrite = save_info.get('overwrite', True)
            final_output_path = self._calculate_output_path(ctx.image_name, save_info)
            # 记录本次输出目录，供 _save_text_to_file 写入 JSON，供编辑器后续导出时回写到同一位置
            try:
                ctx.final_output_dir = os.path.dirname(final_output_path)
            except Exception:
                pass
            success = self._save_translated_image(
                ctx.result,
                final_output_path,
                ctx.image_name,
                overwrite,
                mode_label,
                source_image=ctx.input,
            )
            
            # 标记成功
            if success or not overwrite:  # 跳过已存在的文件也算成功
                ctx.success = True
            elif overwrite:
                self._mark_context_failure(
                    ctx,
                    RuntimeError(f"保存输出文件失败: {os.path.basename(final_output_path)}"),
                    stage='saving',
                )
            
            # 导出可编辑PSD（如果启用）
            if config and hasattr(config, 'cli') and hasattr(config.cli, 'export_editable_psd') and config.cli.export_editable_psd:
                try:
                    from .utils.photoshop_export import (
                        get_psd_output_path,
                        photoshop_export,
                    )
                    psd_path = get_psd_output_path(ctx.image_name)
                    cli_cfg = getattr(config, 'cli', None)
                    default_font = getattr(cli_cfg, 'psd_font', None)
                    line_spacing = getattr(config.render, 'line_spacing', None) if hasattr(config, 'render') else None
                    script_only = getattr(cli_cfg, 'psd_script_only', False)
                    photoshop_export(psd_path, ctx, default_font, ctx.image_name, self.verbose, self._result_path, line_spacing, script_only)
                    logger.info(f"  -> ✅ [PSD] Exported editable PSD: {os.path.basename(psd_path)}")
                except Exception as psd_err:
                    logger.error(f"Error exporting PSD for {os.path.basename(ctx.image_name)}: {psd_err}")
            
            # ✅ 保存后立即清理result以释放内存
            ctx.result = None
            
            return success
        except Exception as e:
            logger.error(f"Error in _save_and_cleanup_context: {e}")
            self._mark_context_failure(ctx, e, stage='saving')
            return False

    def _save_text_to_file(self, image_path: str, ctx: Context, config: Config = None) -> bool:
        """保存/回写文本区域到JSON（含translation、font_size等渲染后字段），使用新的目录结构"""
        text_output_file = self.text_output_file
        if not text_output_file:
            # 使用新的路径管理器生成JSON路径
            text_output_file = get_json_path(image_path, create_dir=True)

        data = {}
        image_key = os.path.abspath(image_path)

        # Prepare data for JSON serialization
        regions_data = [region.to_dict() for region in ctx.text_regions]

        def normalize_font_path_for_save(font_path: str) -> str:
            """Normalize font path to portable relative form when possible."""
            if not font_path:
                return ''

            if os.path.isabs(font_path):
                norm_path = os.path.normpath(font_path)
                base_path = os.path.normpath(BASE_PATH)
                fonts_dir = os.path.normpath(os.path.join(base_path, 'fonts'))
                try:
                    if os.path.commonpath([norm_path, fonts_dir]) == fonts_dir:
                        return os.path.relpath(norm_path, base_path).replace('\\', '/')
                    if os.path.commonpath([norm_path, base_path]) == base_path:
                        return os.path.relpath(norm_path, base_path).replace('\\', '/')
                except ValueError:
                    return norm_path
                return norm_path

            normalized = font_path.replace('\\', '/')
            if normalized.lower().startswith('fonts/'):
                return normalized
            if '/' in normalized:
                return normalized
            return f"fonts/{normalized}"

        # 补全每个区域的 font_path：若区域没有特定字体，填入当前全局字体
        # 这样后端渲染时完全依靠区域字体，不再依赖运行时全局字体状态
        global_font = ''
        if config and hasattr(config, 'render') and getattr(config.render, 'font_path', None):
            global_font = config.render.font_path
        if not global_font:
            global_font = self.font_path or ''
        global_font = normalize_font_path_for_save(global_font)

        # 统一 region.font_path 保存格式（优先相对路径）
        for region in regions_data:
            region_font_path = region.get('font_path')
            if region_font_path:
                region['font_path'] = normalize_font_path_for_save(region_font_path)

        if global_font:
            for region in regions_data:
                if not region.get('font_path'):
                    region['font_path'] = global_font

        # 强制使用Config中的排版方向和对齐方式覆盖（如果存在）
        # 这是为了确保即使 textline_merge 检测过程使用了 auto，
        # 最终保存时也会反映用户的强制设置（例如全书强制横排）
        if config and hasattr(config, 'render'):
            try:
                # 覆盖方向
                if hasattr(config.render, 'direction'):
                    dir_val = config.render.direction
                    if hasattr(dir_val, 'value'): dir_val = dir_val.value

                    forced_direction = None
                    if dir_val == 'vertical': forced_direction = 'v'
                    elif dir_val == 'horizontal': forced_direction = 'h'

                    if forced_direction:
                        for region in regions_data:
                            region['direction'] = forced_direction

                # 覆盖对齐方式
                if hasattr(config.render, 'alignment'):
                    align_val = config.render.alignment
                    if hasattr(align_val, 'value'): align_val = align_val.value

                    if align_val in ('left', 'center', 'right'):
                        for region in regions_data:
                            region['alignment'] = align_val

            except Exception as e:
                logger.warning(f"Failed to override region settings from config: {e}")


        # 对竖排区域的 translation 应用 auto_add_horizontal_tags
        # 确保竖排内横排标记 <H> 写入 JSON
        if config and hasattr(config, 'render') and getattr(config.render, 'auto_rotate_symbols', False):
            from .rendering.text_render import auto_add_horizontal_tags
            for region in regions_data:
                direction = region.get('direction', '')
                is_vertical = direction in ('v', 'vertical')
                if 'horizontal' in region:
                    is_vertical = not region['horizontal']
                if is_vertical and region.get('translation'):
                    region['translation'] = auto_add_horizontal_tags(region['translation'])

        # 获取图片尺寸（优先使用保存的尺寸，兼容并发模式）
        if hasattr(ctx, 'original_size') and ctx.original_size:
            original_width, original_height = ctx.original_size
        elif ctx.input and hasattr(ctx.input, 'size'):
            original_width, original_height = ctx.input.size
        else:
            # 如果都没有，使用默认值或从图片文件读取
            logger.warning("无法获取图片尺寸，使用默认值")
            original_width, original_height = 0, 0
        
        data_to_save = {
            'regions': regions_data,
            'original_width': original_width,
            'original_height': original_height
        }

        preserved_skip_font_scaling = getattr(ctx, 'skip_font_scaling', None)
        if preserved_skip_font_scaling is None and os.path.exists(text_output_file):
            try:
                with open(text_output_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                if existing_data and len(existing_data.values()) > 0:
                    existing_image_data = next(iter(existing_data.values()))
                    if isinstance(existing_image_data, dict) and 'skip_font_scaling' in existing_image_data:
                        preserved_skip_font_scaling = _parse_skip_font_scaling_flag(
                            existing_image_data.get('skip_font_scaling'),
                            default=True,
                        )
            except Exception as e:
                logger.warning(f"Failed to preserve skip_font_scaling from existing JSON {text_output_file}: {e}")

        # 导出原文 / 仅翻译(JSON)：后续导入渲染应重新执行智能排版，不继承旧字号。
        if (self.template and self.save_text) or self.translate_json_only:
            data_to_save['skip_font_scaling'] = False
        # 导出翻译模式保留固定字号，便于按已生成结果回放。
        elif self.generate_and_export:
            data_to_save['skip_font_scaling'] = True
        elif preserved_skip_font_scaling is not None:
            data_to_save['skip_font_scaling'] = bool(preserved_skip_font_scaling)
        
        # 添加超分和上色配置信息
        if config:
            if config.upscale and config.upscale.upscale_ratio:
                data_to_save['upscale_ratio'] = config.upscale.upscale_ratio
                if config.upscale.upscaler:
                    data_to_save['upscaler'] = config.upscale.upscaler
                logger.info(f"在JSON中记录超分信息: ratio={config.upscale.upscale_ratio}, upscaler={config.upscale.upscaler}")
            
            if config.colorizer and config.colorizer.colorizer and config.colorizer.colorizer != 'none':
                data_to_save['colorizer'] = config.colorizer.colorizer
                logger.info(f"在JSON中记录上色信息: colorizer={config.colorizer.colorizer}")

        # 导入 YOLO 框的导出类模式不保存蒙版，后续由 load_text 缺失 mask 时再补生成
        skip_mask_export = (
            getattr(ctx, 'used_imported_yolo_labels', False) and
            ((self.template and self.save_text) or self.generate_and_export)
        )

        # 保存优化后的蒙版（ctx.mask），而不是原始蒙版（ctx.mask_raw）
        # 这样加载后可以直接使用，无需再次进行蒙版优化
        mask_to_save = None
        mask_is_refined = False
        if ctx.mask is not None:
            mask_to_save = ctx.mask
            mask_is_refined = True
        elif ctx.mask_raw is not None:
            mask_to_save = ctx.mask_raw

        if skip_mask_export:
            logger.info("Import YOLO labels enabled in export mode: skipping mask save in JSON")
        elif self.save_mask and mask_to_save is not None:
            try:
                import base64

                import cv2
                _, buffer = cv2.imencode('.png', mask_to_save)
                mask_base64 = base64.b64encode(buffer).decode('utf-8')
                data_to_save['mask_raw'] = mask_base64
                data_to_save['mask_is_refined'] = mask_is_refined
            except Exception as e:
                logger.error(f"Failed to encode mask to base64: {e}")

        # 记录本次主翻译流程的输出目录，编辑器再次导出时回写到原目录
        final_output_dir = getattr(ctx, 'final_output_dir', None)
        if final_output_dir:
            data_to_save['last_export_dir'] = final_output_dir
        elif os.path.exists(text_output_file):
            # 本轮没有写图（例如仅导出 JSON 模式），保留已有的 last_export_dir
            try:
                with open(text_output_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                if existing_data and len(existing_data.values()) > 0:
                    existing_image_data = next(iter(existing_data.values()))
                    if isinstance(existing_image_data, dict):
                        preserved_dir = existing_image_data.get('last_export_dir')
                        if preserved_dir:
                            data_to_save['last_export_dir'] = preserved_dir
            except Exception as e:
                logger.debug(f"Failed to preserve last_export_dir from existing JSON {text_output_file}: {e}")

        data[image_key] = data_to_save

        try:
            # Use a custom encoder to handle numpy types
            class NumpyEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, np.integer):
                        return int(obj)
                    if isinstance(obj, np.floating):
                        return float(obj)
                    if isinstance(obj, np.ndarray):
                        return obj.tolist()
                    return super(NumpyEncoder, self).default(obj)

            json_string = json.dumps(data, ensure_ascii=False, indent=4, cls=NumpyEncoder)

            with open(text_output_file, 'wb') as f:
                f.write(json_string.encode('utf-8'))
            logger.info(f"JSON saved to: {text_output_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to write translation file to {text_output_file}: {e}")
            return False

    def _prepare_loaded_regions(self, loaded_regions: List[TextBlock], use_text_as_translation: bool = False) -> None:
        for region in loaded_regions:
            if not hasattr(region, 'font_size') or not region.font_size:
                try:
                    if region.lines.ndim == 3 and region.lines.shape[1] >= 4 and region.lines.shape[2] >= 2:
                        box_height = np.max(region.lines[:, :, 1]) - np.min(region.lines[:, :, 1])
                        region.font_size = min(int(box_height * 0.8), 128)
                    else:
                        logger.warning(f"Invalid lines shape {region.lines.shape}, using default font_size=24")
                        region.font_size = 24
                except Exception as e:
                    logger.warning(f"Error calculating font_size from lines: {e}, using default font_size=24")
                    region.font_size = 24

            if use_text_as_translation and not region.translation:
                region.translation = region.text
                logger.debug(f"Region translation is empty, using original text: {region.text[:50]}...")

    def _apply_pre_dictionary_to_regions(self, ctx: Context) -> None:
        pre_dict = load_dictionary(self.pre_dict)
        pre_replacements = []
        for region in ctx.text_regions:
            if region.text is None:
                continue
            original = region.text
            region.text = apply_dictionary(region.text, pre_dict)
            if original != region.text:
                pre_replacements.append(f"{original} => {region.text}")

        if pre_replacements:
            logger.info("Pre-translation replacements:")
            for replacement in pre_replacements:
                logger.info(replacement)
        else:
            logger.info("No pre-translation replacements made.")

    def _delete_original_txt_after_json_translation(self, image_path: str) -> None:
        original_txt_path = get_original_txt_path(image_path, create_dir=False)
        if not os.path.exists(original_txt_path):
            return

        try:
            os.remove(original_txt_path)
            logger.info(f"Deleted original text file after JSON translation: {original_txt_path}")
        except Exception as e:
            logger.warning(f"Failed to delete original text file {original_txt_path}: {e}")

    async def _handle_generate_and_export(
        self,
        ctx: Context,
        config: Config,
        ensure_json_with_empty_regions: bool = False
    ) -> None:
        """
        Shared generate_and_export workflow:
        1) refine mask if available
        2) save JSON
        3) export translated TXT via template
        """
        image_name = getattr(ctx, 'image_name', None)
        if not image_name:
            return

        has_regions = hasattr(ctx, 'text_regions') and ctx.text_regions is not None
        has_non_empty_regions = has_regions and bool(ctx.text_regions)

        # 导入 YOLO 框的导出模式不保存蒙版
        if getattr(ctx, 'used_imported_yolo_labels', False):
            logger.info("Import YOLO labels enabled in generate_and_export mode: skipping mask refinement and mask save")
            ctx.mask = None
            ctx.mask_raw = None
        # 导出翻译模式：强制执行蒙版优化（跳过修复）
        elif has_non_empty_regions and ctx.mask is None and ctx.mask_raw is not None:
            await self._report_progress('mask-generation')
            try:
                ctx.mask = await self._run_mask_refinement(config, ctx)
            except Exception:
                logger.error(f"Error during mask-generation in generate_and_export mode:\n{traceback.format_exc()}")
                ctx.mask = ctx.mask_raw  # 回退到原始蒙版
        elif not has_non_empty_regions and ctx.mask_raw is not None:
            logger.info(
                f"Generate-and-export mode: no text regions for {os.path.basename(image_name)}, "
                "skipping mask refinement and exporting empty JSON/TXT only"
            )

        should_export = has_regions and (has_non_empty_regions or ensure_json_with_empty_regions)
        if not should_export:
            return

        self._save_text_to_file(image_name, ctx, config)

        try:
            json_path = find_json_path(image_name)
            if json_path and os.path.exists(json_path):
                from desktop_qt_ui.services.workflow_service import (
                    generate_translated_text,
                    get_template_path_from_config,
                )
                template_path = get_template_path_from_config()
                if template_path and os.path.exists(template_path):
                    translated_result = generate_translated_text(json_path, template_path)
                    logger.info(f"Translated text export for {os.path.basename(image_name)}: {translated_result}")
                else:
                    logger.warning(f"Template file not found for {os.path.basename(image_name)}: {template_path}")
            else:
                logger.warning(f"JSON file not found for {os.path.basename(image_name)}")
        except Exception as e:
            logger.error(f"Failed to export clean text for {os.path.basename(image_name)}: {e}")

    async def _handle_template_and_save_text(
        self,
        ctx: Context,
        config: Config,
        ensure_json_with_empty_regions: bool = True
    ) -> None:
        """
        Shared template+save_text workflow:
        1) refine mask if available
        2) save JSON
        3) export original TXT via template
        """
        image_name = getattr(ctx, 'image_name', None)
        if not image_name:
            return

        has_regions = hasattr(ctx, 'text_regions') and ctx.text_regions is not None
        has_non_empty_regions = has_regions and bool(ctx.text_regions)

        # 导入 YOLO 框的导出模式不保存蒙版
        if getattr(ctx, 'used_imported_yolo_labels', False):
            logger.info("Import YOLO labels enabled in template mode: skipping mask refinement and mask save")
            ctx.mask = None
            ctx.mask_raw = None
        # 导出原文模式：强制执行蒙版优化（跳过修复）
        elif has_non_empty_regions and ctx.mask is None and ctx.mask_raw is not None:
            await self._report_progress('mask-generation')
            try:
                ctx.mask = await self._run_mask_refinement(config, ctx)
            except Exception:
                logger.error(f"Error during mask-generation in template mode:\n{traceback.format_exc()}")
                ctx.mask = ctx.mask_raw  # 回退到原始蒙版
        elif not has_non_empty_regions and ctx.mask_raw is not None:
            logger.info(
                f"Template mode: no text regions for {os.path.basename(image_name)}, "
                "skipping mask refinement and exporting empty JSON/TXT only"
            )

        should_export = has_regions and (has_non_empty_regions or ensure_json_with_empty_regions)
        if not should_export:
            return

        self._save_text_to_file(image_name, ctx, config)

        try:
            json_path = find_json_path(image_name)
            if json_path and os.path.exists(json_path):
                from desktop_qt_ui.services.workflow_service import (
                    generate_original_text,
                    get_template_path_from_config,
                )
                template_path = get_template_path_from_config()
                if template_path and os.path.exists(template_path):
                    original_result = generate_original_text(json_path, template_path)
                    logger.info(f"Original text export for {os.path.basename(image_name)}: {original_result}")
                else:
                    logger.warning(f"Template file not found for {os.path.basename(image_name)}: {template_path}")
            else:
                logger.warning(f"JSON file not found for {os.path.basename(image_name)}")
        except Exception as e:
            logger.error(f"Failed to export original text for {os.path.basename(image_name)}: {e}")

    def _save_inpainted_image(self, image_path: str, inpainted_img: np.ndarray):
        """保存修复后的图片到 inpainted 目录。"""
        inpainted_path = get_inpainted_path(image_path, create_dir=True)
        self._save_image_to_path(inpainted_path, inpainted_img, "Inpainted image", source_image_path=image_path)

    def _save_work_image(self, image_path: str, image_data, label: str = "Work image") -> Optional[str]:
        """保存编辑器专用的上色/超分底图到 editor_base 目录。"""
        work_image_path = get_work_image_path(image_path, create_dir=True)
        return self._save_image_to_path(work_image_path, image_data, label, source_image_path=image_path)

    def _save_editor_base_if_needed(self, ctx, config, image_data=None) -> Optional[str]:
        """在执行了上色或超分时，保存编辑器使用的底图。"""
        input_image = getattr(ctx, 'input', None)
        image_path = getattr(input_image, 'name', None)
        if not image_path:
            return None

        has_colorized = config.colorizer.colorizer != Colorizer.none
        has_upscaled = bool(config.upscale.upscale_ratio)
        if not has_colorized and not has_upscaled:
            return None

        if image_data is None:
            image_data = getattr(ctx, 'upscaled', None) or getattr(ctx, 'img_colorized', None)
        if image_data is None:
            return None

        return self._save_work_image(image_path, image_data, "Processed base image")

    def _save_image_to_path(
        self,
        target_path: str,
        image_data,
        label: str,
        source_image_path: Optional[str] = None,
    ) -> Optional[str]:
        """将图像保存到指定路径。"""
        try:
            if image_data is None:
                return None

            source_image = None
            if isinstance(image_data, Image.Image):
                image_to_save = image_data.copy()
            elif isinstance(image_data, np.ndarray):
                image_to_save = Image.fromarray(image_data)
            else:
                raise TypeError(f"Unsupported work image type: {type(image_data)}")

            try:
                if source_image_path and os.path.exists(source_image_path):
                    try:
                        source_image = open_pil_image(source_image_path, eager=True)
                    except Exception as exc:
                        logger.warning(
                            f"Failed to read source image metadata for {label.lower()}, saving without ICC: "
                            f"{source_image_path}, error={exc}"
                        )

                save_pil_image(
                    image_to_save,
                    target_path,
                    source_image=source_image,
                    quality=self.save_quality,
                )
                if self.verbose:
                    logger.debug(f"{label} saved to: {target_path}")
                return target_path
            finally:
                if source_image is not None:
                    source_image.close()
                image_to_save.close()
        except Exception as e:
            logger.error(f"Failed to save {label.lower()}: {e}")
            return None

    def _preprocess_load_text_mode(self, images_with_configs: List[tuple]):
        """
        load_text模式预处理：自动从TXT文件导入翻译到JSON
        这个方法在翻译开始前统一执行，确保CLI和UI都能使用
        """
        try:
            from manga_translator.utils.path_manager import (
                find_json_path,
                find_txt_files,
            )
            
            # 获取默认模板路径
            template_path = self._get_default_template_path()
            if not template_path or not os.path.exists(template_path):
                logger.warning("Template file not found, skipping TXT to JSON import")
                return
            
            # 收集需要处理的图片路径
            image_paths = []
            for image, config in images_with_configs:
                image_path = None
                if hasattr(image, 'name') and image.name:
                    image_path = image.name
                elif isinstance(image, (str, os.PathLike)):
                    image_path = os.fspath(image)

                if image_path:
                    image_paths.append(image_path)
                else:
                    continue
            
            if not image_paths:
                return
            
            # 批量处理TXT导入
            success_count = 0
            skip_count = 0
            
            for image_path in image_paths:
                try:
                    # 查找JSON和TXT文件
                    json_path = find_json_path(image_path)
                    original_txt_path, translated_txt_path = find_txt_files(image_path)
                    
                    # 如果没有JSON文件，跳过（稍后会报错）
                    if not json_path:
                        skip_count += 1
                        continue
                    
                    # 优先使用原文TXT，其次使用翻译TXT
                    txt_path = original_txt_path if original_txt_path else translated_txt_path
                    
                    if not txt_path:
                        skip_count += 1
                        continue
                    
                    # 执行TXT到JSON的导入
                    from desktop_qt_ui.services.workflow_service import (
                        safe_update_large_json_from_text,
                    )
                    result = safe_update_large_json_from_text(txt_path, json_path, template_path)
                    
                    if not result.startswith("错误"):
                        success_count += 1
                        logger.debug(f"Imported TXT to JSON: {os.path.basename(image_path)}")
                    
                except Exception as e:
                    logger.debug(f"Failed to import TXT for {os.path.basename(image_path)}: {e}")
                    continue
            
            if success_count > 0:
                logger.info(f"TXT to JSON import completed: {success_count} successful, {skip_count} skipped")
            elif skip_count > 0:
                logger.debug(f"No TXT files found for import ({skip_count} images)")
                
        except ImportError as e:
            logger.warning(f"Cannot import workflow_service, skipping TXT to JSON import: {e}")
        except Exception as e:
            logger.warning(f"Error during TXT to JSON import: {e}")

    
    def _get_default_template_path(self) -> Optional[str]:
        """获取默认模板文件路径"""
        try:
            # 尝试多个可能的路径
            possible_paths = [
                os.path.join(os.path.dirname(__file__), '..', 'examples', 'translation_template.json'),
                os.path.join(os.getcwd(), 'examples', 'translation_template.json'),
            ]
            
            # 如果是打包环境
            if getattr(sys, 'frozen', False):
                if hasattr(sys, '_MEIPASS'):
                    possible_paths.insert(0, os.path.join(sys._MEIPASS, 'examples', 'translation_template.json'))
                else:
                    exe_dir = os.path.dirname(sys.executable)
                    possible_paths.insert(0, os.path.join(exe_dir, 'examples', 'translation_template.json'))
            
            for path in possible_paths:
                abs_path = os.path.abspath(path)
                if os.path.exists(abs_path):
                    return abs_path
            
            # 如果都不存在，尝试创建默认模板
            default_path = os.path.abspath(possible_paths[0])
            os.makedirs(os.path.dirname(default_path), exist_ok=True)
            
            default_content = '''翻译模板文件

原文: <original>
译文: <translated>

'''
            with open(default_path, 'w', encoding='utf-8') as f:
                f.write(default_content)
            
            logger.info(f"Created default template at: {default_path}")
            return default_path
            
        except Exception as e:
            logger.warning(f"Failed to get/create default template: {e}")
            return None
    
    def _load_text_and_regions_from_file(self, image_path: str, config: Config):
        """加载翻译数据，支持新的目录结构和向后兼容"""
        if not image_path:
            return None, None, False, True

        # 使用path_manager查找JSON文件（新位置优先）
        text_file_path = find_json_path(image_path)

        if not text_file_path:
            # 检查旧的TXT格式
            base_path, _ = os.path.splitext(image_path)
            text_file_path_txt = base_path + '_translations.txt'
            if os.path.exists(text_file_path_txt):
                # If the old format is found, load from it
                regions = self._load_text_and_regions_from_txt_file(image_path)
                # Since old format doesn't have mask, we return None for mask and refined status
                return regions, None, False, True
            else:
                logger.info(f"Translation file not found for: {image_path}")
                return None, None, False, True

        try:
            # Force UTF-8 encoding to handle potential file encoding issues
            with open(text_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read or parse translation file {text_file_path}: {e}")
            return None, None, False, True

        # Don't check the image key. Assume the user knows what they are doing
        # and that the first entry in the JSON is the one they want to load.
        if not data or len(data.values()) == 0:
            logger.warning(f"JSON file {text_file_path} is empty or invalid.")
            return None, None, False, True

        # Get the first value from the dictionary, regardless of the key.
        image_data = next(iter(data.values()))
        mask_is_refined = False
        skip_font_scaling = True

        # Handle both old and new JSON formats
        if isinstance(image_data, list):
            # Old format: value is a list of regions
            regions_data = image_data
            mask_raw_data = None
        elif isinstance(image_data, dict):
            # New format: value is a dict with 'regions' and 'mask_raw'
            regions_data = image_data.get('regions', [])
            mask_raw_data = image_data.get('mask_raw', None)
            mask_is_refined = image_data.get('mask_is_refined', False)
            skip_font_scaling = _parse_skip_font_scaling_flag(
                image_data.get('skip_font_scaling', True),
                default=True,
            )
        else:
            logger.warning(f"Invalid data format in JSON file {text_file_path}.")
            return None, None, False, True

        regions = []
        for region_data in regions_data:
            try:
                # Convert literal '\\n' to newline characters for the rendering engine
                if 'text' in region_data and isinstance(region_data['text'], str):
                    region_data['text'] = region_data['text'].replace('\\n', '\n')
                if 'translation' in region_data and isinstance(region_data['translation'], str):
                    region_data['translation'] = region_data['translation'].replace('\\n', '\n')

                # If target_lang is missing or empty, set it from the config.
                if not region_data.get('target_lang'):
                    if config and config.translator and config.translator.target_lang:
                        region_data['target_lang'] = config.translator.target_lang
                        logger.debug(f"Region target_lang missing in JSON, falling back to config's target_lang: {config.translator.target_lang}")

                # Convert hex font_color from editor to an 'fg_color' tuple for TextBlock
                if 'font_color' in region_data and isinstance(region_data['font_color'], str):
                    hex_color = region_data.pop('font_color') # Use pop to remove the old key
                    if hex_color.startswith('#') and len(hex_color) == 7:
                        try:
                            r = int(hex_color[1:3], 16)
                            g = int(hex_color[3:5], 16)
                            b = int(hex_color[5:7], 16)
                            region_data['fg_color'] = (r, g, b)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Could not parse font_color '{hex_color}': {e}")
                
                # Map 'fg_colors' (list) to 'fg_color' (tuple) if present
                if 'fg_colors' in region_data:
                    fg_val = region_data.pop('fg_colors')
                    if isinstance(fg_val, list):
                        region_data['fg_color'] = tuple(fg_val)
                
                # Map 'bg_colors' or 'text_stroke_color' to 'bg_color'
                if 'bg_colors' in region_data:
                    bg_val = region_data.pop('bg_colors')
                    if isinstance(bg_val, list):
                        region_data['bg_color'] = tuple(bg_val)
                elif 'text_stroke_color' in region_data: # Handle UI specific name
                    bg_val = region_data.pop('text_stroke_color')
                    if isinstance(bg_val, list): # List RGB
                         region_data['bg_color'] = tuple(bg_val)
                    elif isinstance(bg_val, str) and bg_val.startswith('#'): # Hex string
                        try:
                            r = int(bg_val[1:3], 16)
                            g = int(bg_val[3:5], 16)
                            b = int(bg_val[5:7], 16)
                            region_data['bg_color'] = (r, g, b)
                        except (ValueError, TypeError):
                             pass
                

                # 描边宽度 - stroke_width 优先级高于 default_stroke_width
                # 用户在编辑器中设置的 stroke_width 应该覆盖原始的 default_stroke_width
                if 'stroke_width' in region_data:
                    region_data['default_stroke_width'] = region_data.pop('stroke_width')
                
                # 确保 line_spacing 和 default_stroke_width 被正确传递
                # 这些参数已经在 region_data 中，会被 TextBlock 构造函数接收

                # Recreate the TextBlock object by unpacking the dictionary
                # This restores all saved attributes
                if 'lines' in region_data and isinstance(region_data['lines'], list):
                    # Fix: Use np.float64 to match TextBlock expectation, not np.int32
                    lines_arr = np.array(region_data['lines'], dtype=np.float64)
                    # 验证并修正形状，确保是 (N, 4, 2)
                    if lines_arr.ndim == 2 and lines_arr.shape == (4, 2):
                        lines_arr = lines_arr.reshape(1, 4, 2)
                    elif lines_arr.ndim != 3 or lines_arr.shape[1] != 4 or lines_arr.shape[2] != 2:
                        logger.warning(f"[加载JSON] 无效的lines形状: {lines_arr.shape}, 跳过此区域")
                        continue
                    region_data['lines'] = lines_arr
                
                # 导入翻译模式：颜色已由用户确认，不需要自动调整描边颜色
                region_data['adjust_bg_color'] = False
                region = TextBlock(**region_data)
                regions.append(region)
            except Exception as e:
                logger.error(f"Failed to parse a region in {text_file_path}: {e}")
                continue
        
        mask_raw = None
        if isinstance(mask_raw_data, str):
            try:
                import base64

                import cv2
                img_bytes = base64.b64decode(mask_raw_data)
                img_array = np.frombuffer(img_bytes, dtype=np.uint8)
                mask_raw = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
            except Exception as e:
                logger.error(f"Failed to decode base64 mask: {e}")
        elif isinstance(mask_raw_data, list):
            mask_raw = np.array(mask_raw_data, dtype=np.uint8)
        
        logger.info(f"Loaded {len(regions)} regions from {text_file_path}")
        if mask_raw is not None:
            logger.info(f"Loaded mask_raw from {text_file_path}")

        return regions, mask_raw, mask_is_refined, skip_font_scaling

    def _load_text_and_regions_from_txt_file(self, image_path: str) -> Optional[List[TextBlock]]:
        """
        旧的TXT格式加载方法（已废弃）
        现在只支持JSON格式，此方法保留用于向后兼容但不再实现
        """
        logger.warning("TXT format is deprecated and no longer supported. Please use JSON format instead.")
        return None

    # If `revert_upscaling` is True, revert to input size
    # Else leave `ctx` as-is
    async def _revert_upscale(self, config: Config, ctx: Context):
        if config.upscale.revert_upscaling:
            await self._report_progress('downscaling')
            ctx.result = ctx.result.resize(ctx.input.size)

        # 在verbose模式下保存final.png到调试文件夹
        if ctx.result and self.verbose:
            try:
                final_img = np.array(ctx.result)
                if len(final_img.shape) == 3:  # 彩色图片，转换BGR顺序
                    final_img = cv2.cvtColor(final_img, cv2.COLOR_RGB2BGR)
                final_path = self._result_path('final.png')
                imwrite_unicode(final_path, final_img, logger)
            except Exception as e:
                logger.error(f"Error saving final.png debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        return ctx

    def _get_ai_colorizer_history_pages(self, config: Config) -> int:
        colorizer_config = getattr(config, 'colorizer', None)
        try:
            return max(int(getattr(colorizer_config, 'ai_colorizer_history_pages', 0) or 0), 0)
        except (TypeError, ValueError):
            return 0

    def _should_use_ai_colorizer_history(self, config: Config) -> bool:
        colorizer_config = getattr(config, 'colorizer', None)
        colorizer_type = getattr(colorizer_config, 'colorizer', None)
        return (
            self._get_ai_colorizer_history_pages(config) > 0
            and colorizer_type in {
                Colorizer.openai_colorizer,
                Colorizer.gemini_colorizer,
            }
        )

    def _get_colorizer_history_images(self, config: Config) -> list[Image.Image]:
        if not self._should_use_ai_colorizer_history(config):
            return []

        history_pages = self._get_ai_colorizer_history_pages(config)
        if history_pages <= 0 or not self._colorizer_history_images:
            return []
        return list(self._colorizer_history_images[-history_pages:])

    def _append_colorizer_history_image(self, config: Config, image) -> None:
        if not self._should_use_ai_colorizer_history(config) or image is None:
            return

        if not isinstance(image, Image.Image):
            image = Image.fromarray(np.asarray(image).astype(np.uint8))

        history_image = image.convert("RGB").copy()
        self._colorizer_history_images.append(history_image)

        history_pages = self._get_ai_colorizer_history_pages(config)
        if history_pages <= 0 or len(self._colorizer_history_images) <= history_pages:
            return

        stale_images = self._colorizer_history_images[:-history_pages]
        self._colorizer_history_images = self._colorizer_history_images[-history_pages:]
        for stale_image in stale_images:
            if hasattr(stale_image, 'close'):
                try:
                    stale_image.close()
                except Exception:
                    pass

    def _clear_colorizer_history(self) -> None:
        for history_image in self._colorizer_history_images:
            if hasattr(history_image, 'close'):
                try:
                    history_image.close()
                except Exception:
                    pass
        self._colorizer_history_images = []

    async def _run_colorizer(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("colorizer", config.colorizer.colorizer)] = current_time

        result = await dispatch_colorization(
            config.colorizer.colorizer,
            colorization_size=config.colorizer.colorization_size,
            denoise_sigma=config.colorizer.denoise_sigma,
            device=self.device,
            image=ctx.input,
            config=config,
            colorizer_history_images=self._get_colorizer_history_images(config),
        )
        self._append_colorizer_history_image(config, result)
        return result

    async def _run_upscaling(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("upscaling", config.upscale.upscaler)] = current_time
        
        # Prepare kwargs for Real-CUGAN (NCNN version) and MangaJaNai
        upscaler_kwargs = {}
        if config.upscale.upscaler == 'realcugan':
            realcugan_model = getattr(config.upscale, 'realcugan_model', None)
            if realcugan_model:
                upscaler_kwargs['model_name'] = realcugan_model
            # tile_size: None=use upscaler default, 0=no tiling, >0=manual tile size
            tile_size = getattr(config.upscale, 'tile_size', None)
            if tile_size is not None:
                upscaler_kwargs['tile_size'] = tile_size
        elif config.upscale.upscaler == 'mangajanai':
            # mangajanai 的 upscale_ratio 可以是字符串 (x2, x4, DAT2 x4) 或数字
            ratio = config.upscale.upscale_ratio
            if isinstance(ratio, str):
                upscaler_kwargs['model_name'] = ratio
                # 从字符串解析实际倍率
                if 'x2' in ratio.lower():
                    actual_ratio = 2
                else:
                    actual_ratio = 4
            elif ratio == 2:
                upscaler_kwargs['model_name'] = 'x2'
                actual_ratio = 2
            else:
                upscaler_kwargs['model_name'] = 'x4'
                actual_ratio = 4

            tile_size = getattr(config.upscale, 'tile_size', None)
            if tile_size is not None:
                upscaler_kwargs['tile_size'] = tile_size
        
        # 获取实际的数字倍率
        if config.upscale.upscaler == 'mangajanai':
            upscale_ratio_num = actual_ratio
        else:
            upscale_ratio_num = config.upscale.upscale_ratio
        
        result = (await dispatch_upscaling(
            config.upscale.upscaler, 
            [ctx.img_colorized], 
            upscale_ratio_num, 
            self.device,
            **upscaler_kwargs
        ))[0]
        
        # 如果 models_ttl > 0，则由清理任务自动卸载；否则立即卸载以释放显存
        if self.models_ttl > 0:
            logger.info(f"Upscaling model {config.upscale.upscaler} will be unloaded after {self.models_ttl}s of inactivity")
        else:
            # models_ttl == 0 表示永久保留，但 upscaling 模型占用显存较大，仍然立即卸载
            logger.info(f"Unloading upscaling model {config.upscale.upscaler} immediately to free VRAM")
            await self._unload_model('upscaling', config.upscale.upscaler, **upscaler_kwargs)
            del self._model_usage_timestamps[("upscaling", config.upscale.upscaler)]
        
        return result

    async def _run_detection(self, config: Config, ctx: Context):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()
        
        current_time = time.time()
        self._model_usage_timestamps[("detection", config.detector.detector)] = current_time
        import_yolo_labels = bool(getattr(config.detector, 'import_yolo_labels', False))
        ctx.used_imported_yolo_labels = False
        imported_textlines = []
        imported_mask_raw = None
        if import_yolo_labels:
            image_name = getattr(ctx, 'image_name', None)
            input_name = getattr(getattr(ctx, 'input', None), 'name', None)
            image_name_has_dir = bool(image_name and (os.path.isabs(image_name) or os.path.dirname(image_name)))
            input_name_has_dir = bool(input_name and (os.path.isabs(input_name) or os.path.dirname(input_name)))
            if input_name_has_dir and not image_name_has_dir:
                label_lookup_path = input_name
            else:
                label_lookup_path = image_name or input_name
            imported_textlines = load_imported_yolo_textlines(
                getattr(ctx, 'img_rgb', None),
                label_lookup_path,
                logger=logger,
            )
            if imported_textlines:
                imported_mask_raw = build_mask_from_textlines(ctx.img_rgb.shape, imported_textlines)

        should_use_imported_labels_only = (
            import_yolo_labels and
            bool(imported_textlines) and
            self.template and
            self.save_text
        )

        if should_use_imported_labels_only:
            logger.info(
                "Import YOLO labels enabled in template mode: skip detector boxes and use imported labels directly"
            )
            ctx.used_imported_yolo_labels = True
            result = (imported_textlines, imported_mask_raw, None)
        else:
            use_yolo_obb = config.detector.use_yolo_obb
            if self.load_text:
                use_yolo_obb = False
            result = await dispatch_detection(
                config.detector.detector,
                ctx.img_rgb,
                config.detector.detection_size,
                config.detector.text_threshold,
                config.detector.box_threshold,
                config.detector.unclip_ratio,
                self.device,
                self.verbose,
                use_yolo_obb,
                config.detector.yolo_obb_conf,
                config.detector.yolo_obb_overlap_threshold,
                config.detector.min_box_area_ratio,
                self._result_path,
            )
        
            # 处理bbox调试图（如果检测器返回了）
            if self.verbose and result and len(result) == 3 and result[2] is not None:
                third_elem = result[2]
                # 检查是否是tuple（包含三张图）
                if isinstance(third_elem, tuple) and len(third_elem) == 3:
                    try:
                        logger.info(f'[DEBUG] Processing 3-element tuple: {[type(x) for x in third_elem]}')
                        bbox_img, binary_mask_img, raw_mask_mask = third_elem
                        # 保存边框调试图
                        bbox_debug_path = self._result_path('bboxes_with_scores.png')
                        imwrite_unicode(bbox_debug_path, bbox_img, logger)
                        logger.info(f'Saved bbox debug image to {bbox_debug_path}')
                        # 保存二值化mask
                        binary_mask_path = self._result_path('mask_binary.png')
                        imwrite_unicode(binary_mask_path, binary_mask_img, logger)
                        logger.info(f'Saved binary mask to {binary_mask_path}')
                        # 暂存raw_mask_mask到ctx以便后续生成对比图
                        ctx.raw_mask_mask = raw_mask_mask
                        logger.info(f'[DEBUG] Stored raw_mask_mask for later comparison (shape: {raw_mask_mask.shape})')
                        result = (result[0], result[1], None)
                    except Exception as e:
                        logger.error(f'Failed to save bbox debug images: {e}')
                # 兼容2张图的情况
                elif isinstance(third_elem, tuple) and len(third_elem) == 2:
                    try:
                        bbox_img, binary_mask_img = third_elem
                        bbox_debug_path = self._result_path('bboxes_with_scores.png')
                        imwrite_unicode(bbox_debug_path, bbox_img, logger)
                        logger.info(f'Saved bbox debug image to {bbox_debug_path}')
                        binary_mask_path = self._result_path('mask_binary.png')
                        imwrite_unicode(binary_mask_path, binary_mask_img, logger)
                        logger.info(f'Saved binary mask to {binary_mask_path}')
                        result = (result[0], result[1], None)
                    except Exception as e:
                        logger.error(f'Failed to save bbox debug images: {e}')
                # 兼容单张图的情况（包括混合检测调试图）
                elif isinstance(third_elem, np.ndarray) and len(third_elem.shape) == 3:
                    try:
                        # 如果启用了YOLO辅助检测，优先保存为混合检测调试图
                        if config.detector.use_yolo_obb:
                            # 保存混合检测调试图
                            hybrid_debug_path = self._result_path('hybrid_detection_boxes.png')
                            imwrite_unicode(hybrid_debug_path, cv2.cvtColor(third_elem, cv2.COLOR_RGB2BGR), logger)
                            logger.info(f'✅ 已保存混合检测调试图: {hybrid_debug_path}')
                        else:
                            # 保存普通bbox调试图
                            bbox_debug_path = self._result_path('bboxes_with_scores.png')
                            imwrite_unicode(bbox_debug_path, third_elem, logger)
                            logger.info(f'Saved bbox debug image to {bbox_debug_path}')
                        result = (result[0], result[1], None)
                    except Exception as e:
                        logger.error(f'Failed to save bbox debug image: {e}')

            if import_yolo_labels and imported_textlines and not self.load_text:
                detector_box_count = len(result[0]) if result and result[0] else 0
                raw_mask = result[1] if result and len(result) > 1 else None
                if raw_mask is None:
                    raw_mask = imported_mask_raw
                ctx.used_imported_yolo_labels = True
                result = (imported_textlines, raw_mask, result[2] if result and len(result) > 2 else None)
                logger.info(
                    f"Import YOLO labels enabled: replace detector boxes with imported boxes "
                    f"(detector_boxes={detector_box_count}, imported_boxes={len(imported_textlines)})"
                )
        
        # --- BEGIN NON-MAXIMUM SUPPRESSION (NMS) FOR DE-DUPLICATION ---
        if result and result[0]:
            try:
                from shapely.geometry import Polygon

                def calculate_iou(box_1, box_2):
                    poly_1 = Polygon(box_1.pts)
                    poly_2 = Polygon(box_2.pts)
                    if not poly_1.is_valid or not poly_2.is_valid:
                        return 0.0
                    intersection_area = poly_1.intersection(poly_2).area
                    union_area = poly_1.union(poly_2).area
                    if union_area == 0:
                        return 0.0
                    return intersection_area / union_area

                textlines = result[0][:] # Work on a copy
                textlines.sort(key=lambda x: x.prob, reverse=True)
                
                kept_textlines = []
                while textlines:
                    current_box = textlines.pop(0)
                    kept_textlines.append(current_box)
                    remaining_textlines = []
                    for box in textlines:
                        iou = calculate_iou(current_box, box)
                        if iou < 0.9: # IoU threshold, 0.9 means very high overlap
                            remaining_textlines.append(box)
                    textlines = remaining_textlines

                if len(result[0]) != len(kept_textlines):
                    logger.info(f"Removed {len(result[0]) - len(kept_textlines)} duplicate lines via NMS.")
                    result = (kept_textlines, result[1], result[2])

            except Exception as e:
                logger.error(f"An error occurred during Non-Maximum Suppression: {e}")
                pass
        # --- END NON-MAXIMUM SUPPRESSION (NMS) ---

        # 拆分检测框：
        # - 前向流程（OCR/翻译）不包含 other
        # - 保留 other 供 textline_merge 的模型辅助合并阶段使用
        if result and result[0]:
            all_textlines = result[0]
            forward_textlines = []
            other_textlines = []
            for txtln in all_textlines:
                det_label = getattr(txtln, 'det_label', None) or getattr(txtln, 'yolo_label', None)
                if isinstance(det_label, str) and det_label.strip().lower() == 'other':
                    other_textlines.append(txtln)
                else:
                    forward_textlines.append(txtln)
            ctx.model_assisted_other_textlines = other_textlines
            ctx.all_detected_textlines = all_textlines
            if other_textlines:
                logger.info(
                    f"Detection split: total={len(all_textlines)}, "
                    f"forward={len(forward_textlines)}, other_for_model_assisted_merge={len(other_textlines)}"
                )
            result = (forward_textlines, result[1], result[2])

        self._prime_bubble_detection_cache(config, getattr(ctx, 'img_rgb', None))
        return result

    def _should_prime_bubble_cache(self, config: Config) -> bool:
        render_cfg = getattr(config, 'render', None)
        ocr_cfg = getattr(config, 'ocr', None)
        return any(
            (
                getattr(render_cfg, 'layout_mode', None) == 'balloon_fill',
                bool(getattr(render_cfg, 'center_text_in_bubble', False)),
                bool(getattr(ocr_cfg, 'use_model_bubble_filter', False)),
                bool(getattr(ocr_cfg, 'use_model_bubble_repair_intersection', False)),
                bool(getattr(ocr_cfg, 'limit_mask_dilation_to_bubble_mask', False)),
            )
        )

    def _prime_bubble_detection_cache(self, config: Config, image: Optional[np.ndarray]) -> None:
        if image is None or getattr(image, 'size', 0) == 0:
            return
        if not self._should_prime_bubble_cache(config):
            return
        try:
            result = detect_bubbles_with_mangalens(image, return_annotated=False, verbose=False)
            detected = len(result.detections) if result is not None else 0
            logger.info(f"Bubble cache primed during detection stage: detections={detected}")
        except Exception as exc:
            logger.warning(f"Bubble cache priming failed during detection stage: {exc}")

    def _save_labeled_textline_debug_image(self, img_rgb: np.ndarray, textlines: List, filename: str = 'bboxes_unfiltered_labeled.png'):
        """
        在原图上绘制带标签的检测框调试图（仅供 verbose 模式调用）。
        """
        if img_rgb is None or textlines is None:
            return
        if len(textlines) == 0:
            return

        # BGR 颜色映射（OpenCV）
        label_colors = {
            'balloon': (255, 255, 0),       # 青
            'qipao': (0, 255, 0),           # 绿
            'other': (0, 255, 255),         # 黄
            'changfangtiao': (255, 0, 255), # 品红
            'fangkuai': (255, 128, 0),      # 橙
            'kuangwai': (128, 0, 255),      # 紫
            'hengxie': (255, 128, 0),       # 橙
            'shuqing': (128, 0, 255),       # 紫
            'unlabeled': (200, 200, 200),   # 灰
        }

        try:
            canvas_bgr = cv2.cvtColor(np.copy(img_rgb), cv2.COLOR_RGB2BGR)
            label_stats = {}
            for idx, txtln in enumerate(textlines):
                pts = getattr(txtln, 'pts', None)
                if pts is None:
                    continue
                pts = np.asarray(pts, dtype=np.int32)
                if pts.size == 0:
                    continue

                label = getattr(txtln, 'det_label', None) or getattr(txtln, 'yolo_label', None) or 'unlabeled'
                label = str(label).strip().lower() if label is not None else 'unlabeled'
                if not label:
                    label = 'unlabeled'
                color = label_colors.get(label, (80, 80, 255))  # 未知标签：红

                cv2.polylines(canvas_bgr, [pts], True, color=color, thickness=2)

                x = int(np.min(pts[:, 0]))
                y = int(np.min(pts[:, 1])) - 6
                if y < 12:
                    y = int(np.max(pts[:, 1])) + 14
                caption = f'{idx}:{label}'
                cv2.putText(canvas_bgr, caption, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2, cv2.LINE_AA)
                cv2.putText(canvas_bgr, caption, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

                label_stats[label] = label_stats.get(label, 0) + 1

            out_path = self._result_path(filename)
            imwrite_unicode(out_path, canvas_bgr, logger)
            logger.info(f'Saved labeled textline debug image to {out_path}')
            logger.info(f'Textline label statistics: {label_stats}')
        except Exception as e:
            logger.error(f'Failed to save labeled textline debug image: {e}')

    async def _unload_model(self, tool: str, model: str, **kwargs):
        logger.info(f"Unloading {tool} model: {model}")
        match tool:
            case 'colorizer':
                await unload_colorization(model)
            case 'detection':
                await unload_detection(model)
            case 'inpainting':
                await unload_inpainting(model)
            case 'ocr':
                await unload_ocr(model)
            case 'upscaling':
                await unload_upscaling(model, **kwargs)
            case 'translation':
                await unload_translation(model)
            case 'textline_merge':
                # textline_merge 不需要卸载（无模型）
                logger.debug("textline_merge does not require unloading")
            case 'rendering':
                # rendering 不需要卸载（无模型）
                logger.debug("rendering does not require unloading")
            case _:
                logger.warning(f"Unknown tool type for unloading: {tool}")
        
        # 清理 Python 内存（对 CPU 和 GPU 都有效）
        import gc
        gc.collect()
        
        # 卸载后主动回收 CUDA 缓存并同步 GPU 任务
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        
        logger.info(f"模型 {tool}/{model} 已卸载，内存已清理")

    def _cleanup_gpu_memory(self, aggressive: bool = False):
        """清理 GPU 显存的辅助方法。

        Args:
            aggressive: 是否执行激进清理（empty_cache/ipc_collect）。
                        批次边界与模型卸载后建议 True。
        """
        import gc
        gc.collect()

        # 仅在 CUDA 设备下执行显存清理；MPS 目前无等价 empty_cache。
        device_str = str(getattr(self, 'device', ''))
        if device_str.startswith('cuda'):
            try:
                import torch
                if torch.cuda.is_available():
                    if aggressive:
                        torch.cuda.empty_cache()
                        if hasattr(torch.cuda, 'ipc_collect'):
                            torch.cuda.ipc_collect()
                    torch.cuda.synchronize()
            except Exception:
                pass

    def _get_cuda_memory_snapshot(self) -> Optional[dict]:
        """获取当前 CUDA 显存快照。非 CUDA 设备返回 None。"""
        device_str = str(getattr(self, 'device', ''))
        if not device_str.startswith('cuda'):
            return None
        try:
            if not torch.cuda.is_available():
                return None
            device = torch.device(device_str)
            device_index = device.index if device.index is not None else torch.cuda.current_device()
            free_bytes, total_bytes = torch.cuda.mem_get_info(device_index)
            return {
                'device': device_index,
                'allocated_mb': torch.cuda.memory_allocated(device_index) / (1024 ** 2),
                'reserved_mb': torch.cuda.memory_reserved(device_index) / (1024 ** 2),
                'peak_allocated_mb': torch.cuda.max_memory_allocated(device_index) / (1024 ** 2),
                'peak_reserved_mb': torch.cuda.max_memory_reserved(device_index) / (1024 ** 2),
                'free_mb': free_bytes / (1024 ** 2),
                'total_mb': total_bytes / (1024 ** 2),
            }
        except Exception:
            return None

    def _log_cuda_memory_snapshot(self, stage: str, include_peak: bool = True):
        """打印 CUDA 显存占用快照，便于定位阶段性显存增长。"""
        if not self.verbose:
            return
        snapshot = self._get_cuda_memory_snapshot()
        if snapshot is None:
            return
        peak_suffix = ""
        if include_peak:
            peak_suffix = (
                f", peak_allocated={snapshot['peak_allocated_mb']:.1f}MB"
                f", peak_reserved={snapshot['peak_reserved_mb']:.1f}MB"
            )
        logger.debug(
            f"[显存] {stage}: cuda:{snapshot['device']}, "
            f"allocated={snapshot['allocated_mb']:.1f}MB, "
            f"reserved={snapshot['reserved_mb']:.1f}MB"
            f"{peak_suffix}, "
            f"free={snapshot['free_mb']:.1f}MB, "
            f"total={snapshot['total_mb']:.1f}MB"
        )
    
    def _cleanup_context_memory(self, ctx, keep_result=True):
        """
        清理单个上下文的中间数据（用于特殊模式）
        
        Args:
            ctx: Context对象
            keep_result: bool - 是否保留 ctx.result
        """
        # 清理输入图像
        if hasattr(ctx, 'input') and ctx.input is not None:
            if keep_result and hasattr(ctx, 'result') and ctx.input is ctx.result:
                pass
            else:
                if hasattr(ctx.input, 'close'):
                    try:
                        ctx.input.close()
                    except Exception:
                        pass
            del ctx.input
            ctx.input = None
        
        # 清理中间处理图像
        if hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
            del ctx.img_rgb
            ctx.img_rgb = None
        
        if hasattr(ctx, 'img_colorized') and ctx.img_colorized is not None:
            del ctx.img_colorized
            ctx.img_colorized = None
        
        if hasattr(ctx, 'upscaled') and ctx.upscaled is not None:
            del ctx.upscaled
            ctx.upscaled = None
        
        if hasattr(ctx, 'img_inpainted') and ctx.img_inpainted is not None:
            del ctx.img_inpainted
            ctx.img_inpainted = None
        
        if hasattr(ctx, 'img_rendered') and ctx.img_rendered is not None:
            del ctx.img_rendered
            ctx.img_rendered = None
        
        if hasattr(ctx, 'img_alpha') and ctx.img_alpha is not None:
            del ctx.img_alpha
            ctx.img_alpha = None
        
        if hasattr(ctx, 'mask') and ctx.mask is not None:
            del ctx.mask
            ctx.mask = None
        
        if hasattr(ctx, 'mask_raw') and ctx.mask_raw is not None:
            del ctx.mask_raw
            ctx.mask_raw = None

        if hasattr(ctx, 'textlines') and ctx.textlines is not None:
            ctx.textlines = None

        # 如果不保留结果，也清理 result
        if not keep_result and hasattr(ctx, 'result') and ctx.result is not None:
            del ctx.result
            ctx.result = None
        
        # 强制垃圾回收和GPU显存清理
        self._cleanup_gpu_memory()
        logger.debug('[MEMORY] Context cleanup completed')

    
    def _cleanup_batch_memory(self, current_batch_images=None, preprocessed_contexts=None, translated_contexts=None, keep_results=True):
        """
        统一的批次内存清理方法
        
        Args:
            current_batch_images: List[(image, config)] - 当前批次的原始图片
            preprocessed_contexts: List[(ctx, config)] - 预处理后的上下文
            translated_contexts: List[(ctx, config)] - 翻译后的上下文
            keep_results: bool - 是否保留 ctx.result（用于返回结果）
        """
#         import gc
        
        # 1. 清理原始图片
        if current_batch_images:
            for image, _ in current_batch_images:
                is_result = False
                if keep_results and translated_contexts:
                    for t_ctx, _ in translated_contexts:
                        if hasattr(t_ctx, 'result') and t_ctx.result is image:
                            is_result = True
                            break
                if not is_result:
                    if hasattr(image, 'close'):
                        try:
                            image.close()
                        except Exception:
                            pass
            current_batch_images.clear()
        
        # 2. 清理预处理上下文中的输入图像
        if preprocessed_contexts:
            for ctx, _ in preprocessed_contexts:
                if hasattr(ctx, 'input') and ctx.input is not None:
                    is_result = False
                    if keep_results and translated_contexts:
                        for t_ctx, _ in translated_contexts:
                            if hasattr(t_ctx, 'result') and t_ctx.result is ctx.input:
                                is_result = True
                                break
                    if not is_result:
                        # 先关闭再删除
                        if hasattr(ctx.input, 'close'):
                            try:
                                ctx.input.close()
                            except Exception:
                                pass
                    del ctx.input
                    ctx.input = None
            preprocessed_contexts.clear()
        
        # 3. 清理翻译上下文中的中间图像
        if translated_contexts:
            for ctx, _ in translated_contexts:
                # 清理中间处理图像（使用 del 显式删除）
                if hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
                    del ctx.img_rgb
                    ctx.img_rgb = None
                if hasattr(ctx, 'img_colorized') and ctx.img_colorized is not None:
                    del ctx.img_colorized
                    ctx.img_colorized = None
                if hasattr(ctx, 'upscaled') and ctx.upscaled is not None:
                    del ctx.upscaled
                    ctx.upscaled = None
                if hasattr(ctx, 'img_inpainted') and ctx.img_inpainted is not None:
                    del ctx.img_inpainted
                    ctx.img_inpainted = None
                if hasattr(ctx, 'img_rendered') and ctx.img_rendered is not None:
                    del ctx.img_rendered
                    ctx.img_rendered = None
                if hasattr(ctx, 'img_alpha') and ctx.img_alpha is not None:
                    del ctx.img_alpha
                    ctx.img_alpha = None
                if hasattr(ctx, 'mask') and ctx.mask is not None:
                    del ctx.mask
                    ctx.mask = None
                if hasattr(ctx, 'mask_raw') and ctx.mask_raw is not None:
                    del ctx.mask_raw
                    ctx.mask_raw = None
                if hasattr(ctx, 'textlines') and ctx.textlines is not None:
                    ctx.textlines = None

                # 如果不保留结果，也清理 result
                if not keep_results and hasattr(ctx, 'result') and ctx.result is not None:
                    del ctx.result
                    ctx.result = None
            
            translated_contexts.clear()
        
        # 4. 强制垃圾回收和GPU显存清理
        # 批次边界执行激进显存回收，避免长批处理中显存持续增长。
        self._cleanup_gpu_memory(aggressive=True)
        
        # 5. Windows 特定：强制释放物理内存
        try:
            import ctypes
            ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1, -1)
            logger.debug('[MEMORY] Windows working set trimmed')
        except Exception:
            pass  # 非 Windows 系统时忽略
        
        logger.debug('[MEMORY] Batch cleanup completed')

    def _build_image_load_error_context(self, image_name: str, error: Exception, config: Config = None) -> Context:
        ctx = Context()
        ctx.image_name = image_name
        ctx.text_regions = []
        ctx.success = False
        ctx.translation_error = str(error)
        ctx.error = ctx.translation_error
        if config is not None:
            ctx.config = config
        return ctx

    def _format_pipeline_error_message(self, stage: str, error: Exception) -> str:
        stage_labels = {
            "preprocessing": "预处理",
            "ocr": "OCR",
            "colorizing": "上色",
            "upscaling": "超分",
            "detection": "检测",
            "textline_merge": "文本合并",
            "translation": "翻译",
            "mask-generation": "蒙版生成",
            "inpainting": "修复",
            "rendering": "渲染",
            "saving": "保存",
        }
        raw_message = str(error).strip() or repr(error)
        stage_label = stage_labels.get(stage, stage or "处理")
        return f"{stage_label}失败: {raw_message}"

    def _resolve_pipeline_error(self, stage: str, error: Exception) -> tuple[str, Exception]:
        if isinstance(error, FileTranslationFailure):
            stage = error.stage or stage
            error = error.original_error
        return stage, error

    def _build_stage_error_context(
        self,
        image,
        error: Exception,
        config: Config = None,
        stage: str = "",
    ) -> Context:
        stage, error = self._resolve_pipeline_error(stage, error)
        image_name = getattr(image, "name", None) if image is not None else None
        ctx = Context()
        if image is not None:
            ctx.input = image
        if image_name:
            ctx.image_name = image_name
        ctx.text_regions = []
        ctx.success = False
        ctx.translation_error = self._format_pipeline_error_message(stage, error)
        ctx.error = ctx.translation_error
        if config is not None:
            ctx.config = config
        return ctx

    def _mark_context_failure(self, ctx: Context, error: Exception, stage: str = "") -> Context:
        stage, error = self._resolve_pipeline_error(stage, error)
        if ctx is None:
            ctx = Context()
        if ctx.text_regions is None:
            ctx.text_regions = []
        if not getattr(ctx, "image_name", None):
            input_image = getattr(ctx, "input", None)
            input_name = getattr(input_image, "name", None) if input_image is not None else None
            if input_name:
                ctx.image_name = input_name
        ctx.success = False
        ctx.translation_error = self._format_pipeline_error_message(stage, error)
        ctx.error = ctx.translation_error
        return ctx

    def _materialize_batch_inputs(self, batch_items: List[tuple]) -> tuple[list[tuple], list[Context]]:
        """
        Lazily load path-based items so batching stays under backend control.
        """
        loaded_items: list[tuple] = []
        load_errors: list[Context] = []

        for image_or_path, config in batch_items:
            if isinstance(image_or_path, str):
                image_path = image_or_path
                try:
                    with open(image_path, 'rb') as f:
                        image = open_pil_image(f, eager=True)
                    image.name = image_path
                    loaded_items.append((image, config))
                except Exception as exc:
                    logger.error(f"加载图片失败 {image_path}: {exc}")
                    load_errors.append(self._build_image_load_error_context(image_path, exc, config))
                continue

            loaded_items.append((image_or_path, config))

        return loaded_items, load_errors

    # Background models cleanup job.
    async def _detector_cleanup_job(self):
        logger.info(f"Model cleanup job started with models_ttl={self.models_ttl} seconds")
        while True:
            if self.models_ttl == 0:
                await asyncio.sleep(1)
                continue
            now = time.time()
            for (tool, model), last_used in list(self._model_usage_timestamps.items()):
                time_since_last_use = now - last_used
                if time_since_last_use > self.models_ttl:
                    logger.info(f"Model {tool}/{model} has been idle for {time_since_last_use:.1f}s (TTL: {self.models_ttl}s), unloading...")
                    await self._unload_model(tool, model)
                    del self._model_usage_timestamps[(tool, model)]
            await asyncio.sleep(1)

    async def _run_ocr(self, config: Config, ctx: Context):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()
        
        current_time = time.time()
        self._model_usage_timestamps[("ocr", config.ocr.ocr)] = current_time
        
        # 为OCR创建子文件夹（只在verbose模式下）
        if self.verbose:
            image_subfolder = self._get_image_subfolder()
            if image_subfolder:
                if self.result_sub_folder:
                    ocr_result_dir = os.path.join(BASE_PATH, 'result', self.result_sub_folder, image_subfolder, 'ocrs')
                else:
                    ocr_result_dir = os.path.join(BASE_PATH, 'result', image_subfolder, 'ocrs')
                os.makedirs(ocr_result_dir, exist_ok=True)
            else:
                ocr_result_dir = os.path.join(BASE_PATH, 'result', self.result_sub_folder, 'ocrs')
                os.makedirs(ocr_result_dir, exist_ok=True)
        else:
            # 非verbose模式下使用临时目录或不创建OCR结果目录
            ocr_result_dir = None
        
        # 临时设置环境变量供OCR模块使用
        old_ocr_dir = os.environ.get('MANGA_OCR_RESULT_DIR', None)
        if ocr_result_dir:
            os.environ['MANGA_OCR_RESULT_DIR'] = ocr_result_dir
        
        try:
            # --- Primary OCR run ---
            primary_ocr_engine = config.ocr.ocr
            ocr_name = primary_ocr_engine.value if hasattr(primary_ocr_engine, 'value') else primary_ocr_engine
            logger.info(f"Running primary OCR with: {ocr_name}")
            textlines = await dispatch_ocr(
                primary_ocr_engine,
                ctx.img_rgb,
                ctx.textlines,
                config.ocr,
                self.device,
                self.verbose,
                runtime_config=config,
            )

            # --- BEGIN: HYBRID OCR LOGIC ---
            if config.ocr.use_hybrid_ocr:
                # Identify textlines that failed recognition or have low confidence
                # 判断失败条件：文本为空 或 置信度低于阈值
                prob_threshold = config.ocr.prob if config.ocr.prob is not None else 0.1
                failed_indices = [
                    i for i, tl in enumerate(textlines) 
                    if not tl.text.strip() or tl.prob < prob_threshold
                ]
                
                if failed_indices:
                    # Use textlines[i] instead of ctx.textlines[i] because OCR may have changed the order
                    failed_textlines = [textlines[i] for i in failed_indices]
                    logger.info(f"{len(failed_textlines)} textlines failed or have low confidence (< {prob_threshold}) with primary OCR. Trying secondary OCR...")
                    
                    secondary_ocr_engine = config.ocr.secondary_ocr
                    # We can reuse the same config object, just switching the engine
                    secondary_config = config.ocr
                    
                    secondary_ocr_name = secondary_ocr_engine.value if hasattr(secondary_ocr_engine, 'value') else secondary_ocr_engine
                    logger.info(f"Running secondary OCR with: {secondary_ocr_name}")
                    secondary_results = await dispatch_ocr(
                        secondary_ocr_engine,
                        ctx.img_rgb,
                        failed_textlines,
                        secondary_config,
                        self.device,
                        self.verbose,
                        runtime_config=config,
                    )
                    
                    # Merge the results back into the original list
                    for i, result_tl in zip(failed_indices, secondary_results):
                        textlines[i] = result_tl # Replace the failed textline with the new result
                    
                    logger.info("Secondary OCR processing finished.")
                    
                    # ✅ 混合OCR完成后清理,防止两次OCR调用累积
#                     import gc
                    if 'secondary_results' in locals():
                        del secondary_results
                    if 'failed_textlines' in locals():
                        del failed_textlines
                    if 'failed_indices' in locals():
                        del failed_indices
                    self._cleanup_gpu_memory()
            # --- END: HYBRID OCR LOGIC ---

        finally:
            # 恢复环境变量
            if old_ocr_dir is not None:
                os.environ['MANGA_OCR_RESULT_DIR'] = old_ocr_dir
            elif 'MANGA_OCR_RESULT_DIR' in os.environ:
                del os.environ['MANGA_OCR_RESULT_DIR']

        new_textlines = []
        filtered_count = 0
        for textline in textlines:
            text = str(getattr(textline, 'text', '') or '')
            if not text.strip():
                continue

            if self.filter_text_enabled:
                match_result = match_filter(text)
                if match_result:
                    matched_word, match_type = match_result
                    filtered_count += 1
                    logger.info(f'OCR过滤文本行 ({match_type}匹配): "{text}" -> 匹配: "{matched_word}"')
                    continue

            if config.render.font_color_fg:
                textline.fg_r, textline.fg_g, textline.fg_b = config.render.font_color_fg
            if config.render.font_color_bg:
                textline.bg_r, textline.bg_g, textline.bg_b = config.render.font_color_bg
            new_textlines.append(textline)

        if filtered_count > 0:
            logger.info(f'OCR过滤列表: 过滤了 {filtered_count} 个文本行')
        return new_textlines

    async def _run_textline_merge(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("textline_merge", "textline_merge")] = current_time
        # Filter out languages to skip  
        if config.translator.skip_lang is not None:  
            skip_langs = [lang.strip().upper() for lang in config.translator.skip_lang.split(',')]
            filtered_textlines = []  
            for txtln in ctx.textlines:  
                try:  
                    detected_lang, confidence = langid.classify(txtln.text)
                    source_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
                    if source_language != 'UNKNOWN':
                        source_language = source_language.upper()
                except Exception:  
                    source_language = 'UNKNOWN'  
    
                # Print detected source_language and whether it's in skip_langs  
                # logger.info(f'Detected source language: {source_language}, in skip_langs: {source_language in skip_langs}, text: "{txtln.text}"')  
    
                if source_language in skip_langs:  
                    logger.info(f'Filtered out: {txtln.text}')  
                    logger.info(f'Reason: Detected language {source_language} is in skip_langs')  
                    continue  # Skip this region  
                filtered_textlines.append(txtln)  
            ctx.textlines = filtered_textlines  
    
        merge_input_textlines = list(ctx.textlines)
        enable_model_assisted_merge = bool(getattr(config.ocr, 'merge_special_require_full_wrap', True))
        model_assisted_other_textlines = []
        if enable_model_assisted_merge:
            model_assisted_other_textlines = getattr(ctx, 'model_assisted_other_textlines', None) or []
            if model_assisted_other_textlines:
                logger.info(
                    f"Model-assisted merge uses auxiliary 'other' boxes only: "
                    f"ocr_textlines={len(merge_input_textlines)}, "
                    f"other_aux={len(model_assisted_other_textlines)}"
                )

        text_regions = await dispatch_textline_merge(
            merge_input_textlines,
            ctx.img_rgb.shape[1],
            ctx.img_rgb.shape[0],
            config,
            verbose=self.verbose,
            model_assisted_other_textlines=(
                model_assisted_other_textlines if enable_model_assisted_merge else None
            )
        )
        for region in text_regions:
            if not hasattr(region, "text_raw"):
                region.text_raw = region.text      # Save initial OCR results for downstream processing.

        # 应用合并后的面积过滤（基于合并后的大框）
        # 只过滤单个检测框的小区域，保留包含多个检测框的合并区域
        if config.detector.min_box_area_ratio > 0:
            img_h, img_w = ctx.img_rgb.shape[:2]
            img_total_pixels = img_h * img_w
            
            # 模拟检测器的切割逻辑，判断是否需要切割
            h, w = img_h, img_w
            if h < w:
                h, w = w, h
            
            asp_ratio = h / w
            tgt_size = config.detector.detection_size
            down_scale_ratio = h / tgt_size
            require_rearrange = down_scale_ratio > 2.5 and asp_ratio > 3
            
            # 如果需要切割，计算切割块的大小
            if require_rearrange:
                pw_num = max(int(np.floor(2 * tgt_size / w)), 2)
                patch_size = pw_num * w
                
                # 限制切割后块的最大长宽比（不超过 3:1）
                # 注意：ph = pw_num * w，所以 ph/w = pw_num
                max_patch_aspect_ratio = 3.0
                if pw_num > max_patch_aspect_ratio:
                    # pw_num 太大，说明切割块太高，需要减小
                    # 但是不能直接减小 pw_num，因为这是检测器的逻辑
                    # 我们只是用来计算面积过滤的参考值
                    # 所以这里使用限制后的 pw_num 来计算 tile_pixels
                    adjusted_pw_num = max_patch_aspect_ratio
                    adjusted_ph = adjusted_pw_num * w
                    tile_pixels = adjusted_ph * w
                    logger.info(f'检测到极端长宽比图片 (长宽比={asp_ratio:.2f}), 限制面积过滤参考块长宽比: 原始切割块={patch_size}x{w} (长宽比={pw_num:.2f}), 过滤参考块={adjusted_ph:.0f}x{w} (长宽比={adjusted_pw_num:.2f}), 面积={tile_pixels:.0f}像素')
                else:
                    tile_pixels = patch_size * w
                    logger.info(f'检测到极端长宽比图片 (长宽比={asp_ratio:.2f}), 使用切割块面积 ({patch_size}x{w}={tile_pixels}像素) 进行过滤')
            else:
                tile_pixels = img_total_pixels  # 不切割，使用整图
            
            before_filter_count = len(text_regions)
            filtered_out_regions = []
            filtered_in_regions = []
            
            for region in text_regions:
                # 计算合并的检测框数量
                num_textlines = len(region.lines)
                
                # 如果包含多个检测框，说明是真正的文本区域，保留
                if num_textlines > 1:
                    filtered_in_regions.append(region)
                    continue
                
                # 只对单个检测框的区域进行面积过滤
                region_area = region.real_area
                # 使用切割块面积（如果有切割）或整图面积
                area_ratio = region_area / tile_pixels
                
                if region_area <= 16 or area_ratio <= config.detector.min_box_area_ratio:
                    filtered_out_regions.append((region, area_ratio, num_textlines, require_rearrange))
                else:
                    filtered_in_regions.append(region)
            
            text_regions = filtered_in_regions
            after_filter_count = len(text_regions)
            
            if filtered_out_regions:
                reference_desc = f'切割块({patch_size}x{w})' if require_rearrange else f'整图({img_w}x{img_h})'
                filter_ratio = len(filtered_out_regions) / before_filter_count * 100 if before_filter_count > 0 else 0
                # Info级别：只显示摘要
                logger.info(f'合并后面积过滤: 参考={reference_desc}, 最小面积比例={config.detector.min_box_area_ratio:.4f} ({config.detector.min_box_area_ratio*100:.2f}%), '
                           f'过滤前={before_filter_count}, 过滤后={after_filter_count}, 移除={len(filtered_out_regions)} ({filter_ratio:.1f}%, 仅单框区域)')
                # Verbose模式：显示详细信息
                if self.verbose:
                    for idx, (region, ratio, num_lines, was_rearranged) in enumerate(filtered_out_regions):
                        # 获取框的宽高
                        x1, y1, x2, y2 = region.xyxy
                        width = x2 - x1
                        height = y2 - y1
                        logger.debug(f'  移除单框区域[{idx+1}]: 大小={width:.0f}x{height:.0f}, 面积={region.real_area:.1f}像素, 占比={ratio*100:.3f}%, 文本="{region.text[:20]}"')

        keep_lang = str(getattr(config.translator, 'keep_lang', 'none') or 'none').strip().upper()
        keep_lang_enabled = keep_lang not in _KEEP_LANG_NONE_VALUES
        keep_lang_filtered_count = 0

        new_text_regions = []
        for region in text_regions:
            # 跳过text为None的区域
            if region.text is None:
                logger.warning('跳过text为None的区域')
                continue
                
            # Remove leading spaces after pre-translation dictionary replacement                
            original_text = region.text  
            stripped_text = original_text.strip()  
            
            # Record removed leading characters  
            removed_start_chars = original_text[:len(original_text) - len(stripped_text)]  
            if removed_start_chars:  
                logger.info(f'Removed leading characters: "{removed_start_chars}" from "{original_text}"')  
            
            # Modified filtering condition: handle incomplete parentheses  
            bracket_pairs = {  
                '(': ')', '（': '）', '[': ']', '【': '】', '{': '}', '〔': '〕', '〈': '〉', '「': '」',  
                '"': '"', '＂': '＂', "'": "'", "“": "”", '《': '》', '『': '』', '〝': '〞', '﹁': '﹂', '﹃': '﹄',  
                '⸂': '⸃', '⸄': '⸅', '⸉': '⸊', '⸌': '⸍', '⸜': '⸝', '⸠': '⸡', '‹': '›', '«': '»', '＜': '＞', '<': '>'  
            }   
            left_symbols = set(bracket_pairs.keys())  
            right_symbols = set(bracket_pairs.values())  
            
            has_brackets = any(s in stripped_text for s in left_symbols) or any(s in stripped_text for s in right_symbols)  
            
            if has_brackets:  
                result_chars = []  
                stack = []  
                to_skip = []    
                
                # 第一次遍历：标记匹配的括号  
                # First traversal: mark matching brackets
                for i, char in enumerate(stripped_text):  
                    if char in left_symbols:  
                        stack.append((i, char))  
                    elif char in right_symbols:  
                        if stack:  
                            # 有对应的左括号，出栈  
                            # There is a corresponding left bracket, pop the stack
                            stack.pop()  
                        else:  
                            # 没有对应的左括号，标记为删除  
                            # No corresponding left parenthesis, marked for deletion
                            to_skip.append(i)  
                
                # 标记未匹配的左括号为删除
                # Mark unmatched left brackets as delete  
                for pos, _ in stack:  
                    to_skip.append(pos)  
                
                has_removed_symbols = len(to_skip) > 0  
                
                # 第二次遍历：处理匹配但不对应的括号
                # Second pass: Process matching but mismatched brackets
                stack = []  
                for i, char in enumerate(stripped_text):  
                    if i in to_skip:  
                        # 跳过孤立的括号
                        # Skip isolated parentheses
                        continue  
                        
                    if char in left_symbols:  
                        stack.append(char)  
                        result_chars.append(char)  
                    elif char in right_symbols:  
                        if stack:  
                            left_bracket = stack.pop()  
                            expected_right = bracket_pairs.get(left_bracket)  
                            
                            if char != expected_right:  
                                # 替换不匹配的右括号为对应左括号的正确右括号
                                # Replace mismatched right brackets with the correct right brackets corresponding to the left brackets
                                result_chars.append(expected_right)  
                                logger.info(f'Fixed mismatched bracket: replaced "{char}" with "{expected_right}"')  
                            else:  
                                result_chars.append(char)  
                    else:  
                        result_chars.append(char)  
                
                new_stripped_text = ''.join(result_chars)  
                
                if has_removed_symbols:  
                    logger.info(f'Removed unpaired bracket from "{stripped_text}"')  
                
                if new_stripped_text != stripped_text and not has_removed_symbols:  
                    logger.info(f'Fixed brackets: "{stripped_text}" → "{new_stripped_text}"')  
                
                stripped_text = new_stripped_text  
              
            region.text = stripped_text.strip()

            if keep_lang_enabled and region.text:
                detected_keep_lang = _detect_region_keep_language(region.text)
                if not _keep_language_matches(detected_keep_lang, keep_lang):
                    keep_lang_filtered_count += 1
                    logger.info(f'Filtered out: {region.text}')
                    logger.info(
                        f'Reason: Detected source language {detected_keep_lang} '
                        f'does not match keep_lang={keep_lang}.'
                    )
                    continue

            # 过滤空文本、过短文本、无价值文本
            if not region.text \
                    or len(region.text) < config.ocr.min_text_length \
                    or not is_valuable_text(region.text) \
                    or (not config.translator.no_text_lang_skip and langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0):
                if region.text and region.text.strip():
                    logger.info(f'Filtered out: {region.text}')
                    if len(region.text) < config.ocr.min_text_length:
                        logger.info('Reason: Text length is less than the minimum required length.')
                    elif not is_valuable_text(region.text):
                        logger.info('Reason: Text is not considered valuable.')
                    elif langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0:
                        logger.info('Reason: Text language matches the target language and no_text_lang_skip is False.')
                elif not region.text:
                    logger.info('Filtered out: Empty text region')
            else:
                if config.render.font_color_fg or config.render.font_color_bg:
                    if config.render.font_color_bg:
                        region.adjust_bg_color = False
                new_text_regions.append(region)
        if keep_lang_enabled and keep_lang_filtered_count > 0:
            logger.info(
                f'合并后保留语言过滤: keep_lang={keep_lang}, '
                f'移除了 {keep_lang_filtered_count} 个文本区域'
            )
        text_regions = new_text_regions
        text_regions = sort_regions(
            text_regions,
            right_to_left=config.render.rtl,
            img=ctx.img_rgb,
            force_simple_sort=config.force_simple_sort
        )   
        
        
        
        return text_regions

    def _prune_context_history(self):
        """
        Prune translation history to prevent memory leaks in large batch tasks.
        Keeps only the most recent pages needed for context.
        """
        # Minimum history to keep (context_size + buffer)
        # If context_size is 0, keep a small buffer (e.g., 5) just in case
        keep_size = max(self.context_size, 1) + 5
        
        if len(self.all_page_translations) > keep_size:
            # Remove oldest entries
            trim_count = len(self.all_page_translations) - keep_size
            self.all_page_translations = self.all_page_translations[trim_count:]
            if len(self._original_page_texts) >= trim_count:
                self._original_page_texts = self._original_page_texts[trim_count:]
            # Also clean up saved image contexts if they are too old (simple heuristic)
            if len(self._saved_image_contexts) > keep_size * 2:
                # Keep only the last N keys
                keys = list(self._saved_image_contexts.keys())
                keys_to_remove = keys[:-keep_size*2]
                for k in keys_to_remove:
                    del self._saved_image_contexts[k]

    @staticmethod
    def _get_context_region_count(region: Any) -> int:
        lines = getattr(region, 'lines', None)
        if lines is None:
            return 1

        try:
            return max(int(len(lines)), 1)
        except TypeError:
            return 1

    def _build_page_context_entries(self, ctx: Context) -> List[dict]:
        entries = []
        if not getattr(ctx, 'text_regions', None):
            return entries

        for region in ctx.text_regions:
            original_text = getattr(region, 'text', None)
            translated_text = getattr(region, 'translation', None)
            if original_text is None or not translated_text:
                continue

            entries.append({
                "text": original_text,
                "translation": translated_text,
                "original_region_count": self._get_context_region_count(region),
            })

        return entries

    def _normalize_context_page_entries(self, page: Any) -> List[dict]:
        """
        历史上下文页兼容两种结构：
        1. 旧结构：{original_text: translation}
        2. 新结构：[{"text": ..., "translation": ..., "original_region_count": ...}, ...]
        """
        normalized_entries: List[dict] = []

        if isinstance(page, dict):
            for original_text, translated_text in page.items():
                normalized_entries.append({
                    "text": original_text,
                    "translation": translated_text,
                })
            return normalized_entries

        if not isinstance(page, list):
            return normalized_entries

        for entry in page:
            if not isinstance(entry, dict):
                continue

            original_text = entry.get("text")
            translated_text = entry.get("translation")
            if original_text is None or translated_text is None:
                continue

            normalized_entry = {
                "text": original_text,
                "translation": translated_text,
            }

            region_count = entry.get("original_region_count")
            if isinstance(region_count, (int, float)):
                normalized_entry["original_region_count"] = max(int(region_count), 1)

            normalized_entries.append(normalized_entry)

        return normalized_entries

    def _page_has_context_entries(self, page: Any) -> bool:
        for entry in self._normalize_context_page_entries(page):
            original_text = str(entry.get("text") or "").strip()
            translated_text = str(entry.get("translation") or "").strip()
            if original_text and translated_text:
                return True
        return False

    def _build_prev_context(self, use_original_text=False, current_page_index=None, batch_index=None, batch_original_texts=None):
        """
        跳过句子数为0的页面，取最近 context_size 个非空页面，构造成历史多轮对话：
        - user: 过去发送给 AI 的文本请求（不附带图片）
        - assistant: 过去 AI 返回的单行 JSON 结果

        最终返回 JSON 数组字符串；如果没有任何非空页面，返回空串。

        Args:
            use_original_text: 是否使用原文而不是译文作为上下文（当前未使用）
            current_page_index: 当前页面索引，用于确定上下文范围
            batch_index: 当前页面在批次中的索引（当前未使用）
            batch_original_texts: 当前批次的原文数据（当前未使用）
        """
        if self.context_size <= 0:
            return ""

        # 使用指定页面索引之前的页面作为上下文
        if current_page_index is not None:
            available_pages = self.all_page_translations[:current_page_index] if self.all_page_translations else []
        else:
            # 使用所有已完成的页面
            available_pages = self.all_page_translations or []

        if not available_pages:
            return ""

        # 筛选出有句子的页面
        non_empty_pages = [
            page for page in available_pages
            if self._page_has_context_entries(page)
        ]
        # 实际要用的页数
        pages_used = min(self.context_size, len(non_empty_pages))
        if pages_used == 0:
            return ""
        tail = non_empty_pages[-pages_used:]

        # 构造成历史 user / assistant 多轮消息
        history_turns = []
        for page in tail:
            page_entries = []
            for entry in self._normalize_context_page_entries(page):
                original_text = entry.get("text")
                translated_text = entry.get("translation")
                original_clean = (original_text or "").replace('\n', ' ').replace('\ufffd', '').strip()
                translated_clean = (translated_text or "").strip()
                if original_clean and translated_clean:
                    page_entry = {
                        "text": original_clean,
                        "translation": translated_clean,
                    }
                    region_count = entry.get("original_region_count")
                    if isinstance(region_count, int) and region_count > 0:
                        page_entry["original_region_count"] = region_count
                    page_entries.append(page_entry)

            if not page_entries:
                continue

            input_data = [
                {
                    "id": index + 1,
                    "text": entry["text"],
                    **({"original_region_count": entry["original_region_count"]} if "original_region_count" in entry else {}),
                }
                for index, entry in enumerate(page_entries)
            ]
            output_data = {
                "translations": [
                    {"id": index + 1, "translation": entry["translation"]}
                    for index, entry in enumerate(page_entries)
                ]
            }
            user_prompt = (
                "Please translate the following manga text regions:\n\n"
                "All texts to translate (JSON Array):\n"
                + json.dumps(input_data, ensure_ascii=False, separators=(',', ':'))
                + "\n\nCRITICAL: Provide translations in the exact same order as the input array. "
                + "Follow the OUTPUT FORMAT specified in the System Prompt."
            )
            assistant_response = json.dumps(output_data, ensure_ascii=False, separators=(',', ':'))
            history_turns.append({
                "user": user_prompt,
                "assistant": assistant_response,
            })

        if not history_turns:
            return ""

        return json.dumps(history_turns, ensure_ascii=False, separators=(',', ':'))

    async def _dispatch_with_context(self, config: Config, texts: list[str], ctx: Context):
        # Attach config to context for translators that need it
        ctx.config = config

        # 计算实际要使用的上下文页数和跳过的空页数
        # Calculate the actual number of context pages to use and empty pages to skip
        done_pages = self.all_page_translations
        if self.context_size > 0 and done_pages:
            pages_expected = min(self.context_size, len(done_pages))
            non_empty_pages = [
                page for page in done_pages
                if self._page_has_context_entries(page)
            ]
            pages_used = min(self.context_size, len(non_empty_pages))
            skipped = pages_expected - pages_used
        else:
            pages_used = skipped = 0

        if self.context_size > 0:
            logger.info(f"Context-aware translation enabled with {self.context_size} pages of history")

        # 构建上下文字符串
        # Build the context string
        prev_ctx = self._build_prev_context()

        # 如果是 OpenAI 翻译器，则专门处理上下文注入
        # Special handling for OpenAI translator: inject context
        if config.translator.translator == Translator.openai:
            from .translators.openai import OpenAITranslator
            translator = OpenAITranslator()

            translator.parse_args(config)
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count('"translation"')
                logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
            if skipped > 0:
                logger.warning(f"Skipped {skipped} pages with no sentences")
                


            # OpenAI 需要传递 ctx 参数（用于AI断句）
            return await translator._translate(ctx.from_lang, config.translator.target_lang, texts, ctx)
        else:
            return await dispatch_translation(
                config.translator.translator_gen,
                texts,
                config,
                False,  # use_mtpe removed
                ctx,
                'cpu' if self._gpu_limited_memory else self.device
            )
        
    async def _load_and_prepare_prompts(self, config: Config, ctx: Context):
        """Loads custom HQ and line break prompts into the context object."""
        from .translators.prompt_loader import (
            load_custom_prompt,
            load_line_break_prompt,
        )
        
        # Load custom high-quality prompt from file if specified (supports .yaml/.json)
        ctx.custom_prompt_json = None
        if config.translator.high_quality_prompt_path:
            try:
                prompt_path = normalize_server_resource_path(config.translator.high_quality_prompt_path)
                if not os.path.isabs(prompt_path):
                    prompt_path = os.path.join(BASE_PATH, prompt_path)
                
                ctx.custom_prompt_json = load_custom_prompt(prompt_path)
                if ctx.custom_prompt_json:
                    logger.info(f"Successfully loaded custom HQ prompt from: {prompt_path}")
                    # Log the parsed content for user verification
                    from .translators.common import _flatten_prompt_data
                    _flatten_prompt_data(ctx.custom_prompt_json)
                    # logger.info(f"--- Parsed Custom Prompt Content ---\n{parsed_content}\n------------------------------------")
                else:
                    logger.warning(f"Custom HQ prompt file not found or invalid: {prompt_path}")
            except Exception as e:
                logger.error(f"Error loading custom HQ prompt: {e}")

        # Load AI line break prompt if enabled (supports .yaml/.json)
        ctx.line_break_prompt_json = None
        if config.render.disable_auto_wrap: # This is the "AI断句" switch
            try:
                dict_dir = os.path.join(BASE_PATH, 'dict')
                ctx.line_break_prompt_json = load_line_break_prompt(dict_dir)
                if ctx.line_break_prompt_json:
                    logger.info("AI line breaking is enabled. Loaded line break prompt.")
                else:
                    logger.warning("AI line breaking is enabled, but line break prompt file not found.")
            except Exception as e:
                logger.error(f"Failed to load line break prompt: {e}")
        return ctx

    async def _run_mask_refinement(self, config: Config, ctx: Context):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()
        
        return await dispatch_mask_refinement(
            ctx.text_regions,
            ctx.img_rgb,
            ctx.mask_raw,
            method='fit_text',
            dilation_offset=config.mask_dilation_offset,
            verbose=self.verbose,
            kernel_size=self.kernel_size,
            use_model_bubble_repair_intersection=bool(getattr(config.ocr, 'use_model_bubble_repair_intersection', False)),
            limit_mask_dilation_to_bubble_mask=bool(getattr(config.ocr, 'limit_mask_dilation_to_bubble_mask', False)),
            debug_path_fn=self._result_path if self.verbose else None,
        )

    async def _run_inpainting(self, config: Config, ctx: Context):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()

        img_shape = tuple(ctx.img_rgb.shape[:2]) if getattr(ctx, 'img_rgb', None) is not None else None
        mask_shape = tuple(ctx.mask.shape[:2]) if getattr(ctx, 'mask', None) is not None else None
        logger.info(
            f"[修复] inpainter={config.inpainter.inpainter}, "
            f"precision={getattr(config.inpainter, 'inpainting_precision', 'n/a')}, "
            f"inpainting_size={config.inpainter.inpainting_size}, "
            f"image_shape={img_shape}, mask_shape={mask_shape}"
        )
        self._log_cuda_memory_snapshot("inpainting/before_cleanup")

        # 修复前先执行一次激进显存清理，降低并发/长批次下的显存碎片与OOM概率。
        self._cleanup_gpu_memory(aggressive=True)
        self._log_cuda_memory_snapshot("inpainting/after_cleanup")
        snapshot = self._get_cuda_memory_snapshot()
        if snapshot is not None:
            try:
                torch.cuda.reset_peak_memory_stats(snapshot['device'])
            except Exception:
                pass
            self._log_cuda_memory_snapshot("inpainting/before_dispatch", include_peak=False)
        
        current_time = time.time()
        self._model_usage_timestamps[("inpainting", config.inpainter.inpainter)] = current_time
        try:
            result = await dispatch_inpainting(
                config.inpainter.inpainter,
                ctx.img_rgb,
                ctx.mask,
                config.inpainter,
                config.inpainter.inpainting_size,
                self.device,
                self.verbose,
            )
            return result
        finally:
            self._log_cuda_memory_snapshot("inpainting/after_dispatch")

    def _should_skip_inpainting_for_ai_renderer(self, config: Config) -> bool:
        return config.render.renderer in (
            Renderer.openai_renderer,
            Renderer.gemini_renderer,
        )

    async def _run_text_rendering(self, config: Config, ctx: Context, skip_font_scaling: bool = False):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()

        current_time = time.time()
        self._model_usage_timestamps[("rendering", config.render.renderer)] = current_time

        # 全局字体只作为“补全区域字体”的来源，后端渲染实际只读 region.font_path
        fallback_font_path = config.render.font_path or self.font_path or ''
        if ctx.text_regions:
            for region in ctx.text_regions:
                if not getattr(region, 'font_path', ''):
                    region.font_path = fallback_font_path

        render_base_img = ctx.img_rgb if self._should_skip_inpainting_for_ai_renderer(config) else ctx.img_inpainted

        if config.render.renderer == Renderer.none:
            output = render_base_img
        else:
            # Request debug image for balloon_fill mode when verbose
            need_debug_img = self.verbose and config.render.layout_mode == 'balloon_fill'
            result = await dispatch_rendering(render_base_img, ctx.text_regions, config, ctx.img_rgb, return_debug_img=need_debug_img, skip_font_scaling=skip_font_scaling)
            
            # Handle debug image if returned
            if need_debug_img and isinstance(result, tuple):
                output, debug_img = result
                # Save balloon_fill debug image
                if debug_img is not None:
                    try:
                        debug_path = self._result_path('balloon_fill_boxes.png')
                        # debug_img 在 rendering 阶段已统一为 BGR，这里直接保存
                        imwrite_unicode(debug_path, debug_img, logger)
                        logger.info(f"📸 Balloon fill debug image saved: {debug_path}")
                    except Exception as e:
                        logger.error(f"Failed to save balloon_fill debug image: {e}")
            else:
                output = result
        
        # ✅ 渲染完成后立即清理不再需要的图像数据
        if hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
            del ctx.img_rgb
            ctx.img_rgb = None
        if hasattr(ctx, 'img_inpainted') and ctx.img_inpainted is not None:
            del ctx.img_inpainted
            ctx.img_inpainted = None
        
        # 强制垃圾回收，释放内存
        import gc
        gc.collect()
        
        return output

    def _create_confidence_heatmap(self, mask: np.ndarray, vmin: float = 0.0, vmax: float = 1.0, equalize: bool = True) -> np.ndarray:
        """
        将灰度mask转换为带颜色条的置信度热力图
        
        Args:
            mask: 灰度mask数组 (0-255)
            vmin: 颜色映射的最小值 (0-1)，低于此值显示为最低颜色
            vmax: 颜色映射的最大值 (0-1)，高于此值显示为最高颜色
            equalize: 是否应用直方图均衡化增强对比度
        
        Returns:
            带颜色条的BGR图像
        """
        # 如果是多通道图像，转换为单通道
        if len(mask.shape) == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        
        # 直方图均衡化增强对比度
        if equalize:
            mask = cv2.equalizeHist(mask)
        
        # 归一化mask到0-1范围
        if mask.max() > 0:
            mask_normalized = mask.astype(np.float32) / 255.0
        else:
            mask_normalized = mask.astype(np.float32)
        
        # 调整映射范围：将[vmin, vmax]映射到[0, 1]
        if vmin != 0.0 or vmax != 1.0:
            mask_normalized = np.clip((mask_normalized - vmin) / (vmax - vmin), 0, 1)
        
        # 应用颜色映射（使用jet colormap）
        colormap = cm.get_cmap('jet')
        colored_mask = colormap(mask_normalized)
        
        # 转换为BGR格式 (matplotlib返回RGBA)
        colored_mask_bgr = (colored_mask[:, :, :3] * 255).astype(np.uint8)
        colored_mask_bgr = cv2.cvtColor(colored_mask_bgr, cv2.COLOR_RGB2BGR)
        
        # 创建带颜色条的图像
        h, w = mask.shape
        # 创建颜色条 (宽度为图像宽度的10%，最小50像素)
        colorbar_width = max(50, int(w * 0.1))
        colorbar_height = h
        
        # 生成颜色条
        colorbar = np.linspace(1, 0, colorbar_height).reshape(-1, 1)
        colorbar = np.tile(colorbar, (1, colorbar_width))
        colored_colorbar = colormap(colorbar)
        colored_colorbar_bgr = (colored_colorbar[:, :, :3] * 255).astype(np.uint8)
        colored_colorbar_bgr = cv2.cvtColor(colored_colorbar_bgr, cv2.COLOR_RGB2BGR)
        
        # 创建带文字标注的颜色条
        # 添加白色边框和文字背景
        colorbar_with_labels = np.ones((colorbar_height, colorbar_width + 100, 3), dtype=np.uint8) * 255
        colorbar_with_labels[:, :colorbar_width] = colored_colorbar_bgr
        
        # 添加刻度和文字
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        font_thickness = 1
        num_ticks = 11  # 显示11个刻度
        
        for i in range(num_ticks):
            # 映射到实际值范围 [vmax, vmin]
            normalized_value = 1.0 - i / (num_ticks - 1)  # 从1.0到0.0
            actual_value = vmin + normalized_value * (vmax - vmin)  # 映射到[vmin, vmax]
            y_pos = int(i * (colorbar_height - 1) / (num_ticks - 1))
            
            # 绘制刻度线
            cv2.line(colorbar_with_labels, 
                    (colorbar_width, y_pos), 
                    (colorbar_width + 10, y_pos), 
                    (0, 0, 0), 1)
            
            # 绘制文字
            text = f'{actual_value:.2f}'
            text_size = cv2.getTextSize(text, font, font_scale, font_thickness)[0]
            text_y = y_pos + text_size[1] // 2
            cv2.putText(colorbar_with_labels, text, 
                       (colorbar_width + 15, text_y), 
                       font, font_scale, (0, 0, 0), font_thickness)
        
        # 添加标题
        title = 'Confidence'
        title_size = cv2.getTextSize(title, font, font_scale, font_thickness)[0]
        title_x = colorbar_width + (100 - title_size[0]) // 2
        cv2.putText(colorbar_with_labels, title, 
                   (title_x, 20), 
                   font, font_scale, (0, 0, 0), font_thickness)
        
        # 拼接原图和颜色条
        result_image = np.hstack([colored_mask_bgr, colorbar_with_labels])
        
        return result_image

    def _result_path(self, path: str) -> str:
        """
        Returns path to result folder where intermediate images are saved when using verbose flag
        or web mode input/result images are cached.
        """
        # 只有在verbose模式下才使用图片级子文件夹
        if self.verbose:
            image_subfolder = self._get_image_subfolder()
            if image_subfolder:
                if self.result_sub_folder:
                    result_path = os.path.join(BASE_PATH, 'result', self.result_sub_folder, image_subfolder, path)
                else:
                    result_path = os.path.join(BASE_PATH, 'result', image_subfolder, path)
                # 确保目录存在
                os.makedirs(os.path.dirname(result_path), exist_ok=True)
                return result_path
        
        # 在server/web模式下（result_sub_folder为空）且为非verbose模式时
        # 需要创建一个子文件夹来保存final.png
        if not self.result_sub_folder:
            # When no subfolder is specified (like in desktop-ui mode),
            # the 'path' parameter is expected to be an absolute path to the output file.
            # Therefore, we don't join it with BASE_PATH.
            base_dir = os.path.join(BASE_PATH, 'result')
            result_path = os.path.join(base_dir, path)
        else:
            result_path = os.path.join(BASE_PATH, 'result', self.result_sub_folder, path)
        
        # 确保目录存在
        dir_to_create = os.path.dirname(result_path)
        if dir_to_create:
            os.makedirs(dir_to_create, exist_ok=True)
        return result_path

    def add_progress_hook(self, ph):
        self._progress_hooks.append(ph)
    
    def set_cancel_check_callback(self, callback):
        """设置取消检查回调函数"""
        self._cancel_check_callback = callback
    
    def _check_cancelled(self):
        """检查任务是否被取消"""
        if self._cancel_check_callback and self._cancel_check_callback():
            logger.warning('[阶段] 任务被取消')
            raise asyncio.CancelledError("Task cancelled")

    async def _report_progress(self, state: str, finished: bool = False):
        for ph in self._progress_hooks:
            await ph(state, finished)

    def _add_logger_hook(self):
        # TODO: Pass ctx to logger hook
        LOG_MESSAGES = {
            'upscaling': 'Running upscaling',
            'detection': 'Running text detection',
            'ocr': 'Running ocr',
            'mask-generation': 'Running mask refinement',
            'inpainting': 'Running inpainting',
            'translating': 'Running text translation',
            'rendering': 'Running rendering',
            'colorizing': 'Running colorization',
            'downscaling': 'Running downscaling',
        }
        LOG_MESSAGES_SKIP = {
            'skip-no-regions': 'No text regions! - Skipping',
            'skip-no-text': 'No text regions with text! - Skipping',
            'error-translating': 'Text translator returned empty queries',
            'cancelled': 'Image translation cancelled',
        }
        LOG_MESSAGES_ERROR = {
            # 'error-lang':           'Target language not supported by chosen translator',
        }

        async def ph(state, finished):
            if state in LOG_MESSAGES:
                logger.info(LOG_MESSAGES[state])
            elif state in LOG_MESSAGES_SKIP:
                logger.warning(LOG_MESSAGES_SKIP[state])
            elif state in LOG_MESSAGES_ERROR:
                logger.error(LOG_MESSAGES_ERROR[state])

        self.add_progress_hook(ph)

    async def translate_batch(self, images_with_configs: List[tuple], batch_size: int = None, image_names: List[str] = None, save_info: dict = None, global_offset: int = 0, global_total: int = None) -> List[Context]:
        """
        批量翻译多张图片，在翻译阶段进行批量处理以提高效率
        
        Args:
            images_with_configs: List of (image, config) tuples
            batch_size: 批量大小，如果为None则使用实例的batch_size
            image_names: 已弃用的参数，保留用于兼容性
            save_info: 保存配置，包含output_folder、input_folders、format等
            global_offset: 全局偏移量，用于显示正确的图片编号（前端分批加载时使用）
            global_total: 全局总图片数，用于显示正确的总批次数（前端分批加载时使用）
            
        Returns:
            List of Context objects with translation results
        """
        # 每次翻译任务开始时重新加载过滤列表（仅在启用时）
        if self.filter_text_enabled:
            from .utils.text_filter import load_filter_list
            load_filter_list(force_reload=True)

        images_with_configs = [
            (image, self._apply_runtime_cli_overrides(config))
            for image, config in images_with_configs
        ]
        
        batch_size = batch_size or self.batch_size
        
        # 如果提供了全局总数，使用它来计算总批次数；否则使用当前批次的图片数
        display_total = global_total if global_total is not None else len(images_with_configs)
        
        # === 步骤0: load_text模式预处理 - 自动从TXT导入到JSON ===
        if self.load_text and images_with_configs:
            logger.info("Load text mode detected: Auto-importing translations from TXT to JSON...")
            self._preprocess_load_text_mode(images_with_configs)
        
        # === 步骤1: 替换翻译模式优先检查 ===
        # 替换翻译模式应该优先于高质量翻译模式，因为它是一个独立的工作流程
        if self.replace_translation and images_with_configs:
            logger.info("Replace translation mode detected: Will extract translations from translated images")
            return await self._translate_batch_replace_translation(images_with_configs, save_info, global_offset, global_total)
        
        # === 步骤2: 检查是否需要使用高质量翻译模式 ===
        is_hq_translator = False
        if images_with_configs:
            first_config = images_with_configs[0][1]
            if first_config and hasattr(first_config.translator, 'translator'):
                from manga_translator.config import Translator
                translator_type = first_config.translator.translator
                is_hq_translator = translator_type in [Translator.openai_hq, Translator.gemini_hq]
                is_import_export_mode = self.load_text or self.template or self.translate_json_only

                # 如果是高质量翻译且未启用并发模式，使用专用的高质量翻译流程
                if is_hq_translator and not is_import_export_mode and not self.batch_concurrent:
                    logger.info(f"检测到高质量翻译器 {translator_type}，自动启用高质量翻译模式")
                    return await self._translate_batch_high_quality(images_with_configs, save_info, global_offset, global_total)
                
                if is_hq_translator and is_import_export_mode:
                    logger.warning("检测到导入/导出翻译模式，高质量翻译流程将被跳过，将使用标准流程进行渲染。")
        
        # === 步骤3: 检查是否需要使用顺序处理模式 ===
        # 注意：不要在这里调用 translate()，因为 translate() 会调用 translate_batch()，造成无限循环
        # 相反，我们直接使用批量处理逻辑，但 batch_size 设置为 1
        is_template_save_mode = self.template and self.save_text
        if is_template_save_mode:
            logger.info("Template+SaveText mode detected. Forcing sequential processing to save files one by one.")
            batch_size = 1  # 强制使用 batch_size=1
        elif batch_size <= 1 and not self.batch_concurrent:
            logger.debug('Batch size <= 1, using sequential processing')
            batch_size = 1
        
        # === 步骤3: 检查是否使用并发流水线模式 ===
        # 并发流水线支持：普通翻译、高质量翻译、单文件翻译
        # 不支持的特殊模式：
        # - load_text: 从JSON加载翻译
        # - template + save_text: 导出原文
        # - generate_and_export: 导出翻译
        # - colorize_only: 仅上色
        # - upscale_only: 仅超分
        # - inpaint_only: 仅修复
        
        # 检查是否有不兼容的特殊模式
        has_incompatible_mode = (
            self.load_text or 
            self.translate_json_only or
            is_template_save_mode or 
            self.generate_and_export or 
            self.colorize_only or 
            self.upscale_only or 
            self.inpaint_only or
            self.replace_translation  # 替换翻译模式也不支持并发
        )
        
        # 如果启用了并发但有不兼容模式，给出提示
        if self.batch_concurrent and has_incompatible_mode:
            incompatible_modes = []
            if self.load_text:
                incompatible_modes.append("加载翻译")
            if self.translate_json_only:
                incompatible_modes.append("仅翻译(JSON)")
            if is_template_save_mode:
                incompatible_modes.append("导出原文")
            if self.generate_and_export:
                incompatible_modes.append("导出翻译")
            if self.colorize_only:
                incompatible_modes.append("仅上色")
            if self.upscale_only:
                incompatible_modes.append("仅超分")
            if self.inpaint_only:
                incompatible_modes.append("仅修复")
            if self.replace_translation:
                incompatible_modes.append("替换翻译")
            
            logger.info(f'⚠️  并发流水线已禁用：当前模式 [{", ".join(incompatible_modes)}] 不支持并发处理')
        
        if self.batch_concurrent and not has_incompatible_mode:
            mode_desc = "高质量翻译" if is_hq_translator else "标准翻译"
            logger.info(f'🚀 启用并发流水线模式 ({mode_desc}): {len(images_with_configs)} 张图片, 翻译批量大小: {batch_size}')
            from .utils.concurrent_pipeline import ConcurrentPipeline
            
            # 保存save_info供并发流水线使用
            self._current_save_info = save_info
            
            pipeline = ConcurrentPipeline(self, batch_size)
            
            # 提取文件路径和配置
            file_paths = []
            configs = []
            for item in images_with_configs:
                # item 可能是 (image, config) 或 image
                if isinstance(item, tuple):
                    image, config = item
                    # 如果 image 是 PIL.Image 对象且有 name 属性（文件路径）
                    if hasattr(image, 'name'):
                        file_paths.append(image.name)
                    else:
                        # 如果是字符串，直接作为路径
                        file_paths.append(str(image))
                    configs.append(config)
                else:
                    # 单个图片对象
                    if hasattr(item, 'name'):
                        file_paths.append(item.name)
                    else:
                        file_paths.append(str(item))
                    # 使用默认配置
                    configs.append(images_with_configs[0][1] if isinstance(images_with_configs[0], tuple) else None)
            
            # 使用并发流水线处理（分批加载图片）
            contexts = await pipeline.process_batch(file_paths, configs)

            # 清理翻译历史，防止内存泄漏
            self._prune_context_history()

            return contexts
        
        # === 步骤4: 批量处理模式（顺序处理） ===
        logger.info(f'Starting batch translation: {len(images_with_configs)} images, batch size: {batch_size}')
        logger.info('[阶段] 批量翻译任务启动')
        
        # Start the background cleanup job once if not already started.
        if self._detector_cleanup_task is None:
            self._detector_cleanup_task = asyncio.create_task(self._detector_cleanup_job())
        
        results = []
        total_images = len(images_with_configs)

        async def report_completed_image_progress():
            """顺序批处理按单张图片完成推进整体进度，避免整批处理期间长时间停滞。"""
            if display_total <= 0:
                return
            completed = min(global_offset + len(results), display_total)
            failed_count = sum(1 for ctx in results if getattr(ctx, 'translation_error', None))
            await self._report_progress(f"batch:{completed}:{completed}:{display_total}:{failed_count}")

        # 分批处理所有图片
        for batch_start in range(0, total_images, batch_size):
            current_batch_images = []
            preprocessed_contexts = []
            translated_contexts = []
            
            try:
                await asyncio.sleep(0)  # 检查是否被取消
                self._check_cancelled()  # 检查取消标志

                batch_end = min(batch_start + batch_size, total_images)
                current_batch_items = images_with_configs[batch_start:batch_end]

                # 计算全局图片编号（考虑前端分批加载的偏移量）
                global_batch_start = global_offset + batch_start + 1
                global_batch_end = global_offset + batch_end
                global_batch_num = (global_offset + batch_start) // batch_size + 1
                global_total_batches = (display_total + batch_size - 1) // batch_size
                progress_state = f"batch:{global_batch_start}:{global_batch_end}:{display_total}"
                
                logger.info(f"Processing rolling batch {global_batch_num}/{global_total_batches} (images {global_batch_start}-{global_batch_end})")
                logger.info(f'[阶段] 开始处理批次 {global_batch_num}/{global_total_batches}')

                current_batch_images, load_error_contexts = self._materialize_batch_inputs(current_batch_items)
                if load_error_contexts:
                    results.extend(load_error_contexts)
                    await report_completed_image_progress()
                if not current_batch_images:
                    await self._report_progress(progress_state)
                    continue

                # --- 阶段1: 预处理（检测、OCR、文本行合并） ---
                
                # 特殊情况：load_text模式（从JSON加载翻译）
                if self.load_text:
                    logger.info("Load text mode: Loading translations from JSON and skipping text detection/OCR/translation")
                    for i, (image, config) in enumerate(current_batch_images):
                        await asyncio.sleep(0)
                        self._check_cancelled()  # 检查取消标志
                        try:
                            self._set_image_context(config, image)
                            image_name = image.name if hasattr(image, 'name') else None
                            
                            # 直接处理 load_text 模式，不调用 translate() 避免无限循环
                            ctx = Context()
                            ctx.input = image
                            ctx.image_name = image_name
                            ctx.verbose = self.verbose
                            ctx.save_quality = self.save_quality
                            ctx.config = config
                            
                            # 加载翻译数据
                            loaded_regions, loaded_mask, mask_is_refined, skip_font_scaling = self._load_text_and_regions_from_file(image_name, config)
                            if loaded_regions is None:
                                json_path = os.path.splitext(image_name)[0] + '_translations.json' if image_name else 'unknown'
                                raise FileNotFoundError(f"Translation file not found or invalid: {json_path}")
                            
                            # 如果regions是空列表，记录日志但继续处理（渲染原图）
                            if not loaded_regions:
                                logger.info(f"No text regions found in JSON for {os.path.basename(image_name)}, will render original image")
                            
                            self._prepare_loaded_regions(loaded_regions, use_text_as_translation=True)
                            
                            ctx.text_regions = loaded_regions
                            ctx.skip_font_scaling = skip_font_scaling
                            
                            existing_inpainted_path = find_inpainted_path(image_name) if image_name else None

                            # load_text 始终基于原图处理，不走上色/超分，也不把已有修复图塞进 img_colorized/upscaled
                            ctx.img_colorized = ctx.input
                            ctx.upscaled = ctx.input
                            
                            ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
                            
                            # 验证加载的图片
                            if ctx.img_rgb is None or ctx.img_rgb.size == 0:
                                logger.error("[批量] 加载图片失败: img_rgb为空或无效")
                                continue
                            
                            if len(ctx.img_rgb.shape) < 2 or ctx.img_rgb.shape[0] == 0 or ctx.img_rgb.shape[1] == 0:
                                logger.error(f"[批量] 加载的图片尺寸无效: {ctx.img_rgb.shape}")
                                continue

                            import_yolo_labels = bool(getattr(config.detector, 'import_yolo_labels', False))
                            if loaded_mask is not None or not import_yolo_labels:
                                # load_text 跳过文本检测阶段；如果当前配置依赖气泡缓存
                                # （例如 balloon_fill / 气泡内居中 / 模型气泡过滤），这里补做一次预热。
                                # 导入 YOLO 框且需要重新跑检测生成 mask 的场景，后续 _run_detection 会自行预热，
                                # 这里避免重复调用。
                                self._prime_bubble_detection_cache(config, ctx.img_rgb)

                            # 处理 mask
                            if loaded_mask is not None:
                                if mask_is_refined:
                                    ctx.mask = loaded_mask
                                else:
                                    ctx.mask_raw = loaded_mask
                            else:
                                if import_yolo_labels:
                                    try:
                                        detection_img_rgb = None
                                        input_image = getattr(ctx, 'input', None)
                                        if input_image is not None:
                                            try:
                                                detection_img_rgb, _ = load_image(input_image)
                                            except Exception as original_load_err:
                                                logger.warning(
                                                    f"Load text mode: failed to load original image for mask detection, "
                                                    f"falling back to current image ({original_load_err})"
                                                )

                                        mask_ctx = Context()
                                        mask_ctx.img_rgb = detection_img_rgb if detection_img_rgb is not None else ctx.img_rgb
                                        mask_ctx.image_name = image_name
                                        _, generated_mask_raw, generated_mask = await self._run_detection(config, mask_ctx)
                                        if generated_mask_raw is not None:
                                            ctx.mask_raw = generated_mask_raw
                                        if generated_mask is not None:
                                            ctx.mask = generated_mask
                                        if ctx.mask_raw is not None or ctx.mask is not None:
                                            logger.info("Load text mode: generated mask from detection because JSON has no mask")
                                    except Exception as e:
                                        logger.warning(f"Load text mode: detection-based mask generation failed, fallback to region mask ({e})")

                                if ctx.mask_raw is None and ctx.mask is None:
                                    mask = np.zeros_like(ctx.img_rgb[:, :, 0])
                                    polygons = [p.reshape((-1, 1, 2)) for r in ctx.text_regions for p in r.lines]
                                    cv2.fillPoly(mask, polygons, 255)
                                    ctx.mask_raw = mask
                            
                            # load_text 模式下：无论是否超分，都强制对齐 mask 到当前图像尺寸，
                            # 避免 ONNX inpainting 因 image/mask 维度不一致而报错。
                            target_h, target_w = ctx.img_rgb.shape[:2]
                            for mask_attr in ('mask_raw', 'mask'):
                                mask_val = getattr(ctx, mask_attr, None)
                                if mask_val is None:
                                    continue

                                mask_arr = np.asarray(mask_val)
                                if mask_arr.ndim == 3:
                                    mask_arr = mask_arr[:, :, 0]
                                elif mask_arr.ndim != 2:
                                    squeezed = np.squeeze(mask_arr)
                                    if squeezed.ndim == 2:
                                        mask_arr = squeezed
                                    else:
                                        logger.warning(
                                            f"[load_text] {mask_attr} shape invalid ({mask_arr.shape}), fallback to zero mask {target_h}x{target_w}"
                                        )
                                        mask_arr = np.zeros((target_h, target_w), dtype=np.uint8)

                                if mask_arr.shape[0] != target_h or mask_arr.shape[1] != target_w:
                                    logger.warning(
                                        f"[load_text] Resizing {mask_attr} from {mask_arr.shape[:2]} to {(target_h, target_w)}"
                                    )
                                    mask_arr = cv2.resize(mask_arr, (target_w, target_h), interpolation=cv2.INTER_NEAREST)

                                if mask_arr.dtype != np.uint8:
                                    mask_arr = mask_arr.astype(np.uint8, copy=False)

                                setattr(ctx, mask_attr, mask_arr)
                            
                            # load_text 支持“仅修复”模式：即使没有文字区域，只要 JSON 里有可用蒙版，也执行修复。
                            if not ctx.text_regions:
                                mask_for_inpainting = ctx.mask if ctx.mask is not None else ctx.mask_raw
                                has_mask_for_inpainting = False
                                if mask_for_inpainting is not None:
                                    try:
                                        has_mask_for_inpainting = np.count_nonzero(mask_for_inpainting) > 0
                                    except Exception as mask_count_err:
                                        logger.warning(
                                            f"Load text mode: failed to inspect imported mask for {os.path.basename(image_name)} "
                                            f"({mask_count_err}), falling back to original image"
                                        )

                                if has_mask_for_inpainting:
                                    logger.info(
                                        f"No text regions found in JSON for {os.path.basename(image_name)}, "
                                        "using imported mask for inpaint-only output"
                                    )
                                    mask_injected_from_raw = ctx.mask is None
                                    if ctx.mask is None:
                                        ctx.mask = np.asarray(mask_for_inpainting, dtype=np.uint8)

                                    generated_inpainted_in_load_text = False
                                    if existing_inpainted_path and loaded_mask is not None:
                                        try:
                                            existing_inpainted_image = open_pil_image(existing_inpainted_path, eager=False)
                                            existing_inpainted_rgb, _ = load_image(existing_inpainted_image)
                                            ctx.img_inpainted = existing_inpainted_rgb
                                            logger.info("Load text mode: Using existing inpainted image for mask-only import.")
                                        except Exception as existing_inpaint_err:
                                            logger.warning(
                                                f"Load text mode: failed to load existing inpainted image for mask-only import, "
                                                f"rerunning inpainting ({existing_inpaint_err})"
                                            )
                                            await self._report_progress('inpainting')
                                            ctx.img_inpainted = await self._run_inpainting(config, ctx)
                                            generated_inpainted_in_load_text = True
                                    else:
                                        await self._report_progress('inpainting')
                                        ctx.img_inpainted = await self._run_inpainting(config, ctx)
                                        generated_inpainted_in_load_text = True

                                    if (
                                        generated_inpainted_in_load_text
                                        and image_name
                                        and ctx.img_inpainted is not None
                                        and self.save_text
                                    ):
                                        self._save_inpainted_image(image_name, ctx.img_inpainted)

                                    if mask_injected_from_raw:
                                        ctx.mask = None

                                    await self._report_progress('finished', True)
                                    ctx.result = dump_image(ctx.input, ctx.img_inpainted, ctx.img_alpha)
                                    ctx = await self._revert_upscale(config, ctx)
                                else:
                                    logger.info(
                                        f"No text regions or usable mask found in JSON for {os.path.basename(image_name)}, "
                                        "returning original image"
                                    )
                                    await self._report_progress('finished', True)
                                    ctx.result = ctx.upscaled  # 返回上采样后的原图
                                    ctx = await self._revert_upscale(config, ctx)
                            else:
                                # Mask refinement
                                if ctx.mask is None:
                                    await self._report_progress('mask-generation')
                                    ctx.mask = await self._run_mask_refinement(config, ctx)
                                
                                # Inpainting
                                generated_inpainted_in_load_text = False
                                if self._should_skip_inpainting_for_ai_renderer(config):
                                    logger.info("AI renderer selected: skipping inpainting and using original work image as render base.")
                                    ctx.img_inpainted = ctx.img_rgb
                                elif existing_inpainted_path and loaded_mask is not None:
                                    try:
                                        existing_inpainted_image = open_pil_image(existing_inpainted_path, eager=False)
                                        existing_inpainted_rgb, _ = load_image(existing_inpainted_image)
                                        ctx.img_inpainted = existing_inpainted_rgb
                                        logger.info("Load text mode: Using existing inpainted image, skipping inpainting.")
                                    except Exception as existing_inpaint_err:
                                        logger.warning(
                                            f"Load text mode: failed to load existing inpainted image, rerunning inpainting ({existing_inpaint_err})"
                                        )
                                        await self._report_progress('inpainting')
                                        ctx.img_inpainted = await self._run_inpainting(config, ctx)
                                        generated_inpainted_in_load_text = True
                                else:
                                    await self._report_progress('inpainting')
                                    ctx.img_inpainted = await self._run_inpainting(config, ctx)
                                    generated_inpainted_in_load_text = True

                                if (
                                    generated_inpainted_in_load_text
                                    and image_name
                                    and ctx.img_inpainted is not None
                                    and self.save_text
                                ):
                                    self._save_inpainted_image(image_name, ctx.img_inpainted)
                                
                                # Rendering - load_text按JSON中的skip_font_scaling控制：True=跳过字体缩放，False=执行字体缩放
                                await self._report_progress('rendering')
                                ctx.img_rendered = await self._run_text_rendering(config, ctx, skip_font_scaling=skip_font_scaling)
                                
                                await self._report_progress('finished', True)
                                ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
                                ctx = await self._revert_upscale(config, ctx)

                            # load_text模式：渲染后回写JSON（同步最新regions，包含translation/font_size等字段）
                            if hasattr(ctx, 'text_regions') and ctx.text_regions is not None and hasattr(ctx, 'image_name') and ctx.image_name:
                                try:
                                    self._save_text_to_file(ctx.image_name, ctx, config)
                                except Exception as save_json_err:
                                    logger.error(f"Error updating JSON in load_text mode for {os.path.basename(ctx.image_name)}: {save_json_err}")
                            
                            preprocessed_contexts.append((ctx, config))
                            
                            # ✅ 每处理完一张图片后立即清理内存（保留result）
                            self._cleanup_context_memory(ctx, keep_result=True)
                            
                        except Exception as e:
                            logger.error(f"Error loading text for image {i+1} in batch: {e}")
                            ctx = Context()
                            ctx.input = image
                            ctx.text_regions = []
                            if hasattr(image, 'name'):
                                ctx.image_name = image.name
                            ctx.translation_error = str(e)
                            fallback_result = None
                            try:
                                fallback_result = image.copy()
                            except Exception as copy_error:
                                logger.warning(f"Failed to copy fallback image for load_text error: {copy_error}")
                                image_name = getattr(image, 'name', None)
                                if image_name:
                                    try:
                                        reopened_image = open_pil_image(image_name, eager=True)
                                        fallback_result = reopened_image.copy()
                                        reopened_image.close()
                                    except Exception as reopen_error:
                                        logger.warning(f"Failed to reopen fallback image for load_text error: {reopen_error}")
                            ctx.result = fallback_result
                            preprocessed_contexts.append((ctx, config))
                    
                    # load_text模式下已经完成了所有处理（包括渲染），直接保存并返回
                    for ctx, config in preprocessed_contexts:
                        if save_info and ctx.result:
                            try:
                                # 使用统一的保存和清理方法（包含PSD导出）
                                self._save_and_cleanup_context(ctx, save_info, config, "LOAD_TEXT")
                            except Exception as save_err:
                                logger.error(f"Error saving load_text result for {os.path.basename(ctx.image_name)}: {save_err}")
                        
                        results.append(ctx)
                        await report_completed_image_progress()
                    
                    # ✅ load_text模式：批次完成后清理批次数据（图片已在循环内清理）
                    # 注意：不能关闭仍被 ctx.result 引用的 image 对象
                    if current_batch_images:
                        # 收集所有仍被 result 引用的 image 对象
                        result_ids = {id(ctx.result) for ctx, _ in preprocessed_contexts if ctx.result is not None}
                        for i, (image, _) in enumerate(current_batch_images):
                            if id(image) in result_ids:
                                continue  # 该 image 仍被 ctx.result 引用，不能关闭
                            if hasattr(image, 'close'):
                                try:
                                    image.close()
                                except Exception:
                                    pass
                    
                    # 清空列表，防止 finally 中的 _cleanup_batch_memory 再次关闭这些 image
                    current_batch_images.clear()
                    preprocessed_contexts.clear()
                    
                    # load_text模式处理完成，继续下一批
                    continue

                if self.translate_json_only:
                    logger.info("Translate JSON only mode: Loading original text from JSON and skipping detection/OCR/rendering")
                    for i, (image, config) in enumerate(current_batch_images):
                        await asyncio.sleep(0)
                        self._check_cancelled()
                        try:
                            self._set_image_context(config, image)
                            image_name = image.name if hasattr(image, 'name') else None

                            ctx = Context()
                            ctx.input = image
                            ctx.image_name = image_name
                            ctx.verbose = self.verbose
                            ctx.save_quality = self.save_quality
                            ctx.config = config
                            ctx.from_lang = 'auto'

                            loaded_regions, loaded_mask, mask_is_refined, skip_font_scaling = self._load_text_and_regions_from_file(image_name, config)
                            if loaded_regions is None:
                                json_path = find_json_path(image_name) if image_name else None
                                if not json_path and image_name:
                                    json_path = get_json_path(image_name, create_dir=False)
                                raise FileNotFoundError(f"JSON translation data not found or invalid: {json_path}")

                            self._prepare_loaded_regions(loaded_regions, use_text_as_translation=False)
                            ctx.text_regions = loaded_regions
                            ctx.skip_font_scaling = skip_font_scaling

                            if loaded_mask is not None:
                                if mask_is_refined:
                                    ctx.mask = loaded_mask
                                else:
                                    ctx.mask_raw = loaded_mask

                            self._apply_pre_dictionary_to_regions(ctx)
                            preprocessed_contexts.append((ctx, config))
                        except Exception as e:
                            logger.error(f"Error loading JSON translation data for image {i+1} in batch: {e}")
                            ctx = Context()
                            ctx.input = image
                            ctx.text_regions = []
                            if hasattr(image, 'name'):
                                ctx.image_name = image.name
                            ctx.translation_error = str(e)
                            preprocessed_contexts.append((ctx, config))

                    logger.info('[阶段] JSON 原文加载完成，开始翻译阶段')
                    try:
                        translated_contexts = await self._batch_translate_contexts(preprocessed_contexts, batch_size)
                    except Exception as e:
                        logger.error(f"Error during JSON-only batch translation stage: {e}")
                        raise

                    for ctx, config in translated_contexts:
                        if getattr(ctx, 'translation_error', None):
                            results.append(ctx)
                            await report_completed_image_progress()
                            continue

                        try:
                            save_success = self._save_text_to_file(ctx.image_name, ctx, config)
                            if not save_success:
                                raise IOError(f"Failed to save JSON for {os.path.basename(ctx.image_name)}")
                            self._delete_original_txt_after_json_translation(ctx.image_name)
                            ctx.success = True
                        except Exception as save_err:
                            logger.error(f"Error saving translated JSON for {os.path.basename(ctx.image_name)}: {save_err}")
                            ctx = self._mark_context_failure(ctx, save_err, stage='saving')

                        results.append(ctx)
                        await report_completed_image_progress()
                        self._cleanup_context_memory(ctx, keep_result=True)

                    if current_batch_images:
                        for image, _ in current_batch_images:
                            if hasattr(image, 'close'):
                                try:
                                    image.close()
                                except Exception:
                                    pass

                    continue

                # 标准模式：执行检测、OCR等预处理
                logger.info('[阶段] 开始预处理阶段（检测、OCR）')
                for i, (image, config) in enumerate(current_batch_images):
                    # 检查是否被取消
                    await asyncio.sleep(0)
                    self._check_cancelled()  # 检查取消标志
                    try:
                        self._set_image_context(config, image)
                        # ✅ 保存context以便渲染阶段复用，避免生成两个文件夹
                        from .utils.generic import get_image_md5
                        image_md5 = get_image_md5(image)
                        self._save_current_image_context(image_md5)
                        ctx = await self._translate_until_translation(image, config)
                        if hasattr(image, 'name'):
                            ctx.image_name = image.name
                        preprocessed_contexts.append((ctx, config))
                    except Exception as e:
                        logger.error(f"Error pre-processing image {i+1} in batch: {e}", exc_info=True)
                        ctx = self._build_stage_error_context(image, e, config, stage='preprocessing')
                        preprocessed_contexts.append((ctx, config))

                # --- 阶段2: 翻译 ---
                logger.info('[阶段] 预处理完成，开始翻译阶段')
                if self.colorize_only or self.upscale_only or self.inpaint_only:
                    # 特殊情况：仅上色/仅超分/仅修复模式，跳过翻译
                    mode_name = "Colorize Only" if self.colorize_only else ("Upscale Only" if self.upscale_only else "Inpaint Only")
                    logger.info(f"{mode_name} mode: Skipping translation and rendering stages.")
                    translated_contexts = preprocessed_contexts
                elif is_template_save_mode:
                    # 特殊情况：导出原文模式，跳过翻译
                    logger.info("Template+SaveText mode: Skipping translation, will export original text only.")
                    translated_contexts = preprocessed_contexts
                else:
                    # 标准翻译流程
                    try:
                        translated_contexts = await self._batch_translate_contexts(preprocessed_contexts, batch_size)
                    except Exception as e:
                        logger.error(f"Error during batch translation stage: {e}")
                        raise

                # --- 阶段3: 渲染和保存 ---
                # 特殊情况：导出原文模式（跳过渲染，只保存JSON和导出原文）
                if is_template_save_mode:
                    logger.info("Template+SaveText mode: Skipping rendering, exporting original text only.")
                    for ctx, config in translated_contexts:
                        if getattr(ctx, 'translation_error', None):
                            results.append(ctx)
                            await report_completed_image_progress()
                            continue
                        await self._handle_template_and_save_text(ctx, config)
                        # ✅ 标记成功（导出原文完成）
                        ctx.success = True
                        results.append(ctx)
                        await report_completed_image_progress()
                        
                        # ✅ 每处理完一张图片后立即清理内存（保留result）
                        self._cleanup_context_memory(ctx, keep_result=True)
                    
                    # ✅ 批次完成后清理批次数据（图片已在循环内清理）
                    if current_batch_images:
                        for i, (image, _) in enumerate(current_batch_images):
                            if hasattr(image, 'close'):
                                try:
                                    image.close()
                                except Exception:
                                    pass
                    
                    continue  # 跳过渲染，继续下一批次
                
                # 特殊情况：生成并导出模式（跳过渲染）
                if self.generate_and_export:
                    logger.info("'Generate and Export' mode enabled. Skipping rendering.")
                    for ctx, config in translated_contexts:
                        if getattr(ctx, 'translation_error', None):
                            results.append(ctx)
                            await report_completed_image_progress()
                            continue
                        await self._handle_generate_and_export(ctx, config)
                        # ✅ 标记成功（导出翻译完成）
                        ctx.success = True
                        results.append(ctx)
                        await report_completed_image_progress()
                        
                        # ✅ 每处理完一张图片后立即清理内存（保留result）
                        self._cleanup_context_memory(ctx, keep_result=True)
                    
                    # ✅ 批次完成后清理批次数据（图片已在循环内清理）
                    if current_batch_images:
                        for i, (image, _) in enumerate(current_batch_images):
                            if hasattr(image, 'close'):
                                try:
                                    image.close()
                                except Exception:
                                    pass
                    
                    continue  # 跳过渲染，继续下一批次

                # 标准流程：渲染并保存
                logger.info('[阶段] 翻译完成，开始渲染阶段')
                for idx, (ctx, config) in enumerate(translated_contexts):
                    await asyncio.sleep(0)  # 检查是否被取消
                    self._check_cancelled()  # 检查取消标志
                    if getattr(ctx, 'translation_error', None):
                        results.append(ctx)
                        await report_completed_image_progress()
                        continue
                    try:
                        if hasattr(ctx, 'input'):
                            from .utils.generic import get_image_md5
                            image_md5 = get_image_md5(ctx.input)
                            if not self._restore_image_context(image_md5):
                                self._set_image_context(config, ctx.input)
                        
                        # Colorize/Upscale/Inpaint Only Mode: Skip rendering pipeline
                        if not self.colorize_only and not self.upscale_only and not self.inpaint_only:
                            ctx = await self._complete_translation_pipeline(ctx, config)
                        if save_info and ctx.result:
                            try:
                                # 使用统一的保存和清理方法（包含PSD导出）
                                self._save_and_cleanup_context(ctx, save_info, config, "BATCH")
                            except Exception as save_err:
                                logger.error(f"Error saving standard batch result for {os.path.basename(ctx.image_name)}: {save_err}")

                        # 只在save_text或text_output_file启用时保存JSON（包括空的text_regions）
                        if (self.save_text or self.text_output_file) and hasattr(ctx, 'text_regions') and ctx.text_regions is not None and hasattr(ctx, 'image_name') and ctx.image_name:
                            # 使用循环变量中的config，而不是从ctx中获取
                            self._save_text_to_file(ctx.image_name, ctx, config)

                        results.append(ctx)
                        await report_completed_image_progress()

                        # ✅ 渲染完一张立即清理这张图片的中间数据（不等整个批次完成）
                        self._cleanup_context_memory(ctx, keep_result=True)

                        # 每渲染3张图片就强制垃圾回收一次
                        if (idx + 1) % 3 == 0:
                            import gc
                            gc.collect()

                    except Exception as e:
                        logger.error(f"Error rendering image in batch: {e}", exc_info=True)
                        ctx = self._mark_context_failure(ctx, e, stage='rendering')
                        results.append(ctx)
                        await report_completed_image_progress()
            
            finally:
                # ✅ 批次完成后（无论成功还是失败）立即清理内存
                logger.debug(f'[阶段] 批次 {batch_start//batch_size + 1} 处理完成，开始清理内存')
                self._cleanup_batch_memory(
                    current_batch_images=current_batch_images,
                    preprocessed_contexts=preprocessed_contexts,
                    translated_contexts=translated_contexts,
                    keep_results=True
                )
                logger.debug(f'[MEMORY] Batch {batch_start//batch_size + 1} cleanup completed')

        logger.info(f"Batch translation completed: processed {len(results)} images")
        return results

    async def _translate_until_translation(self, image: Image.Image, config: Config) -> Context:
        """
        执行翻译之前的所有步骤（彩色化、上采样、检测、OCR、文本行合并）
        """
        
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()
        
        ctx = Context()
        ctx.input = image
        ctx.result = None
        
        # 保存原始输入图片用于调试
        if self.verbose:
            try:
                input_img = np.array(image)
                if len(input_img.shape) == 3:  # 彩色图片，转换BGR顺序
                    input_img = cv2.cvtColor(input_img, cv2.COLOR_RGB2BGR)
                result_path = self._result_path('input.png')
                imwrite_unicode(result_path, input_img, logger)
            except Exception as e:
                logger.error(f"Error saving input.png debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # preload and download models (not strictly necessary, remove to lazy load)
        if self.models_ttl == 0 and not self._models_loaded:
            logger.info('Loading models')
            
            # ✅ 检查停止标志
            await asyncio.sleep(0)
            self._check_cancelled()
            
            if config.upscale.upscale_ratio:
                # 传递超分配置参数
                upscaler_kwargs = {}
                if config.upscale.upscaler == 'realcugan':
                    if config.upscale.realcugan_model:
                        upscaler_kwargs['model_name'] = config.upscale.realcugan_model
                    if config.upscale.tile_size is not None:
                        upscaler_kwargs['tile_size'] = config.upscale.tile_size
                elif config.upscale.upscaler == 'mangajanai':
                    # mangajanai 的 upscale_ratio 可以是字符串 (x2, x4, DAT2 x4) 或数字
                    ratio = config.upscale.upscale_ratio
                    if isinstance(ratio, str):
                        upscaler_kwargs['model_name'] = ratio
                    elif ratio == 2:
                        upscaler_kwargs['model_name'] = 'x2'
                    else:
                        upscaler_kwargs['model_name'] = 'x4'
                    if config.upscale.tile_size is not None:
                        upscaler_kwargs['tile_size'] = config.upscale.tile_size
                await prepare_upscaling(config.upscale.upscaler, **upscaler_kwargs)
            
            await prepare_detection(config.detector.detector)
            
            await prepare_ocr(config.ocr.ocr, self.device)
            
            await prepare_inpainting(config.inpainter.inpainter, self.device)
            
            await prepare_translation(config.translator.translator_gen)
            
            if config.colorizer.colorizer != Colorizer.none:
                await prepare_colorization(config.colorizer.colorizer)
            
            self._models_loaded = True  # 标记模型已加载

        # Start the background cleanup job once if not already started.
        if self._detector_cleanup_task is None:
            self._detector_cleanup_task = asyncio.create_task(self._detector_cleanup_job())

        # ✅ 检查停止标志
        await asyncio.sleep(0)
        self._check_cancelled()

        # -- Colorization
        if config.colorizer.colorizer != Colorizer.none:
            await self._report_progress('colorizing')
            try:
                ctx.img_colorized = await self._run_colorizer(config, ctx)
            except Exception as e:
                logger.error(f"Error during colorizing:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                raise FileTranslationFailure("colorizing", e) from e
        else:
            ctx.img_colorized = ctx.input

        # --- Colorize Only Mode Check (for batch processing) ---
        if self.colorize_only:
            logger.info("Colorize Only mode (batch): Running colorization only, skipping detection, OCR, translation and rendering.")
            ctx.result = ctx.img_colorized
            ctx.text_regions = []  # Empty text regions
            self._save_editor_base_if_needed(ctx, config, ctx.img_colorized)
            await self._report_progress('colorize-only-complete', True)
            # 不在这里清理，让调用方在保存JSON后统一清理
            return ctx

        # -- Upscaling
        if config.upscale.upscale_ratio:
            # ✅ 检查停止标志
            await asyncio.sleep(0)
            self._check_cancelled()
            
            await self._report_progress('upscaling')
            try:
                ctx.upscaled = await self._run_upscaling(config, ctx)
            except Exception as e:
                logger.error(f"Error during upscaling:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                raise FileTranslationFailure("upscaling", e) from e
        else:
            ctx.upscaled = ctx.img_colorized

        if (
            hasattr(ctx.input, 'name') and ctx.input.name and
            (config.colorizer.colorizer != Colorizer.none or config.upscale.upscale_ratio)
        ):
            self._save_editor_base_if_needed(ctx, config)

        # --- Upscale Only Mode Check (for batch processing) ---
        if self.upscale_only:
            logger.info("Upscale Only mode (batch): Running upscaling only, skipping detection, OCR, translation and rendering.")
            ctx.result = ctx.upscaled
            ctx.text_regions = []  # Empty text regions
            await self._report_progress('upscale-only-complete', True)
            # 不在这里清理，让调用方在保存JSON后统一清理
            return ctx

        # --- Inpaint Only Mode Check (for batch processing) ---
        if self.inpaint_only:
            logger.info("=== Inpaint Only Mode ===")
            logger.info("Pipeline: Detection → Fill Text → Textline Merge → Mask Refinement → Inpainting")
            
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
            
            # 验证加载的图片
            if ctx.img_rgb is None or ctx.img_rgb.size == 0:
                logger.error("[批量流程] 加载图片失败: img_rgb为空或无效")
                raise Exception("加载图片失败: img_rgb为空或无效")
            
            if len(ctx.img_rgb.shape) < 2 or ctx.img_rgb.shape[0] == 0 or ctx.img_rgb.shape[1] == 0:
                logger.error(f"[批量流程] 加载的图片尺寸无效: {ctx.img_rgb.shape}")
                raise Exception(f"加载的图片尺寸无效: {ctx.img_rgb.shape}")
            
            # Step 1: 检测 - 获取textlines（检测框）和mask_raw（原始蒙版）
            await self._report_progress('detection')
            try:
                ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
                logger.info(f"✓ Step 1 - Detection: Found {len(ctx.textlines) if ctx.textlines else 0} textlines")
                logger.info(f"  - mask_raw: {ctx.mask_raw.shape if ctx.mask_raw is not None else 'None'}")
                if ctx.mask_raw is not None:
                    logger.info(f"  - mask_raw non-zero pixels: {np.count_nonzero(ctx.mask_raw)}")
            except Exception as e:
                logger.error(f"Error during detection:\n{traceback.format_exc()}")
                if not self.ignore_errors:
                    raise
                raise FileTranslationFailure("detection", e) from e
            
            if not ctx.textlines or ctx.mask_raw is None:
                logger.warning("No textlines or mask_raw detected, skipping inpainting.")
                ctx.img_inpainted = ctx.img_rgb
                ctx.result = ctx.img_inpainted
                ctx.text_regions = []
                await self._report_progress('inpaint-only-complete', True)
                # 不在这里清理，让调用方在保存JSON后统一清理
                return ctx
            
            # Step 2: 填充文本 - 跳过OCR，为每个textline填充占位文本
            for textline in ctx.textlines:
                textline.text = "TEXT"
            logger.info(f"✓ Step 2 - Fill Text: Filled {len(ctx.textlines)} textlines with placeholder 'TEXT'")
            
            # Step 3: Textline Merge - 将textlines合并成text_regions（大框）
            try:
                ctx.text_regions = await dispatch_textline_merge(
                    ctx.textlines,
                    ctx.img_rgb.shape[1],
                    ctx.img_rgb.shape[0],
                    config,
                    verbose=self.verbose,
                    model_assisted_other_textlines=(
                        getattr(ctx, 'model_assisted_other_textlines', None)
                        if bool(getattr(config.ocr, 'merge_special_require_full_wrap', True))
                        else None
                    )
                )
                logger.info(f"✓ Step 3 - Textline Merge: Merged {len(ctx.textlines)} textlines into {len(ctx.text_regions)} text_regions")
            except Exception:
                logger.error(f"Error during textline merge:\n{traceback.format_exc()}")
                # 降级：为每个textline创建一个简单的TextBlock
                logger.warning("Falling back to simple text_regions (1 textline = 1 region)")
                ctx.text_regions = []
                fallback_line_spacing = 1.0
                fallback_letter_spacing = 1.0
                if hasattr(config, 'render'):
                    line_spacing_val = getattr(config.render, 'line_spacing', None)
                    if line_spacing_val is not None:
                        fallback_line_spacing = float(line_spacing_val)
                    letter_spacing_val = getattr(config.render, 'letter_spacing', None)
                    if letter_spacing_val is not None:
                        fallback_letter_spacing = float(letter_spacing_val)

                for textline in ctx.textlines:
                    region = TextBlock(
                        lines=[textline.pts],
                        texts=["TEXT"],
                        font_size=int(textline.font_size) if hasattr(textline, 'font_size') else 20,
                        angle=0,
                        prob=textline.prob if hasattr(textline, 'prob') else 1.0,
                        fg_color=(0, 0, 0),
                        bg_color=(255, 255, 255),
                        line_spacing=fallback_line_spacing,
                        letter_spacing=fallback_letter_spacing
                    )
                    ctx.text_regions.append(region)
                logger.info(f"Created {len(ctx.text_regions)} simple text_regions")
            
            if not ctx.text_regions:
                logger.warning("No text_regions created, skipping mask refinement and inpainting.")
                ctx.img_inpainted = ctx.img_rgb
                ctx.result = ctx.img_inpainted
                await self._report_progress('inpaint-only-complete', True)
                # 不在这里清理，让调用方在保存JSON后统一清理
                return ctx
            
            # Step 4: Mask Refinement - 使用text_regions和mask_raw优化蒙版
            skip_mask_refinement_for_imported_export = (
                getattr(ctx, 'used_imported_yolo_labels', False) and
                ((self.template and self.save_text) or self.generate_and_export)
            )
            if skip_mask_refinement_for_imported_export:
                logger.info("Import YOLO labels enabled in export mode: skipping mask refinement stage")
                ctx.mask = None
            else:
                await self._report_progress('mask-generation')
                try:
                    ctx.mask = await self._run_mask_refinement(config, ctx)
                    mask_pixels = np.count_nonzero(ctx.mask) if ctx.mask is not None else 0
                    logger.info(f"✓ Step 4 - Mask Refinement: Generated mask with {mask_pixels} non-zero pixels")
                except Exception:
                    logger.error(f"Error during mask refinement:\n{traceback.format_exc()}")
                    # 降级到简单膨胀
                    logger.warning("Falling back to simple mask dilation")
                    kernel = np.ones((config.kernel_size, config.kernel_size), np.uint8)
                    ctx.mask = cv2.dilate(ctx.mask_raw, kernel, iterations=config.mask_dilation_offset // config.kernel_size)
                    mask_pixels = np.count_nonzero(ctx.mask) if ctx.mask is not None else 0
                    logger.info(f"Simple dilated mask has {mask_pixels} non-zero pixels")
            
            # Step 5: Inpainting - 使用优化后的mask进行修复
            if self._should_skip_inpainting_for_ai_renderer(config):
                logger.info("AI renderer selected: skipping inpainting and using original work image as render base.")
                ctx.img_inpainted = ctx.img_rgb
            elif ctx.mask is None or np.count_nonzero(ctx.mask) == 0:
                logger.warning("Mask is empty! Skipping inpainting.")
                ctx.img_inpainted = ctx.img_rgb
            else:
                await self._report_progress('inpainting')
                try:
                    ctx.img_inpainted = await self._run_inpainting(config, ctx)
                    logger.info("✓ Step 5 - Inpainting: Completed successfully")
                except Exception as e:
                    logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
                    if not self.ignore_errors:
                        raise
                    raise FileTranslationFailure("inpainting", e) from e
            
            # 设置结果 - 转换为PIL Image（保存函数需要PIL格式）
            from PIL import Image
            if isinstance(ctx.img_inpainted, np.ndarray):
                ctx.result = Image.fromarray(ctx.img_inpainted)
            else:
                ctx.result = ctx.img_inpainted
            
            ctx.text_regions = []

            # 设置标志，告诉_complete_translation_pipeline跳过处理
            ctx.inpaint_only_complete = True

            logger.info("=== Inpaint Only Mode Complete ===")
            await self._report_progress('inpaint-only-complete', True)
            # 不在这里清理，让调用方在保存JSON后统一清理
            return ctx

        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
        
        # 验证加载的图片
        if ctx.img_rgb is None or ctx.img_rgb.size == 0:
            logger.error("加载图片失败: img_rgb为空或无效")
            if not self.ignore_errors:
                raise Exception("加载图片失败: img_rgb为空或无效")
            raise FileTranslationFailure("preprocessing", RuntimeError("加载图片失败: img_rgb为空或无效"))
        
        if len(ctx.img_rgb.shape) < 2 or ctx.img_rgb.shape[0] == 0 or ctx.img_rgb.shape[1] == 0:
            logger.error(f"加载的图片尺寸无效: {ctx.img_rgb.shape}")
            if not self.ignore_errors:
                raise Exception(f"加载的图片尺寸无效: {ctx.img_rgb.shape}")
            raise FileTranslationFailure("preprocessing", RuntimeError(f"加载的图片尺寸无效: {ctx.img_rgb.shape}"))

        # -- Detection
        await self._report_progress('detection')
        try:
            ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
        except Exception as e:
            logger.error(f"Error during detection:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            raise FileTranslationFailure("detection", e) from e

        if self.verbose and ctx.mask_raw is not None:
            # 生成带置信度颜色映射和颜色条的热力图
            logger.info(f"Generating confidence heatmap for mask_raw (shape: {ctx.mask_raw.shape}, dtype: {ctx.mask_raw.dtype})")
            heatmap = self._create_confidence_heatmap(ctx.mask_raw, equalize=False)
            logger.info(f"Heatmap generated (shape: {heatmap.shape}), saving to mask_raw.png")
            imwrite_unicode(self._result_path('mask_raw.png'), heatmap, logger)

        if not ctx.textlines:
            await self._report_progress('skip-no-regions', True)
            ctx.result = ctx.upscaled
            ctx.text_regions = []  # 设置为空列表，以便保存空的JSON
            return await self._revert_upscale(config, ctx)

        if self.verbose:
            img_bbox_raw = np.copy(ctx.img_rgb)
            for txtln in ctx.textlines:
                det_label = getattr(txtln, 'det_label', None) or getattr(txtln, 'yolo_label', None)
                if isinstance(det_label, str) and det_label.strip().lower() == 'other':
                    continue
                cv2.polylines(img_bbox_raw, [txtln.pts], True, color=(255, 0, 0), thickness=2)
            imwrite_unicode(self._result_path('bboxes_unfiltered.png'), cv2.cvtColor(img_bbox_raw, cv2.COLOR_RGB2BGR), logger)
            # 仅在开启模型辅助合并时输出标签调试图，避免开关关闭时仍生成该文件。
            # 调试图优先使用检测原始全集（含 other），用于排查标签分流逻辑。
            if bool(getattr(config.ocr, 'merge_special_require_full_wrap', True)):
                labeled_debug_textlines = getattr(ctx, 'all_detected_textlines', None) or ctx.textlines
                self._save_labeled_textline_debug_image(
                    ctx.img_rgb,
                    labeled_debug_textlines,
                    'bboxes_unfiltered_labeled.png'
                )

        # -- OCR
        await self._report_progress('ocr')
        try:
            ctx.textlines = await self._run_ocr(config, ctx)
        except Exception as e:
            logger.error(f"Error during ocr:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            raise FileTranslationFailure("ocr", e) from e

        if not ctx.textlines:
            await self._report_progress('skip-no-text', True)
            ctx.result = ctx.upscaled
            ctx.text_regions = []  # 设置为空列表，以便保存空的JSON
            return await self._revert_upscale(config, ctx)

        # -- Textline merge
        await self._report_progress('textline_merge')
        try:
            ctx.text_regions = await self._run_textline_merge(config, ctx)
        except Exception as e:
            logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            raise FileTranslationFailure("textline_merge", e) from e

        if self.verbose and ctx.text_regions:
            show_panels = not config.force_simple_sort  # 当不使用简单排序时显示panel
            bboxes = visualize_textblocks(cv2.cvtColor(ctx.img_rgb, cv2.COLOR_BGR2RGB), ctx.text_regions, 
                                        show_panels=show_panels, img_rgb=ctx.img_rgb, right_to_left=config.render.rtl)
            imwrite_unicode(self._result_path('bboxes.png'), bboxes, logger)

        # Apply pre-dictionary after textline merge
        self._apply_pre_dictionary_to_regions(ctx)

        # 保存当前图片上下文到ctx中，用于并发翻译时的路径管理
        if self._current_image_context:
            ctx.image_context = self._current_image_context.copy()

        return ctx

    async def _batch_translate_contexts(self, contexts_with_configs: List[tuple], batch_size: int) -> List[tuple]:
        """
        批量处理翻译步骤，防止内存溢出
        """
        results = []
        total_contexts = len(contexts_with_configs)
        
        # 按批次处理，防止内存溢出
        for i in range(0, total_contexts, batch_size):
            await asyncio.sleep(0)  # 检查是否被取消
            self._check_cancelled()  # 检查取消标志
            batch = contexts_with_configs[i:i + batch_size]
            logger.info(f'Processing translation batch {i//batch_size + 1}/{(total_contexts + batch_size - 1)//batch_size}')
            
            # 收集当前批次的所有文本
            all_texts = []
            batch_text_mapping = []  # 记录每个文本属于哪个context和region
            
            for ctx_idx, (ctx, config) in enumerate(batch):
                if not ctx.text_regions:
                    continue
                    
                region_start_idx = len(all_texts)
                for region_idx, region in enumerate(ctx.text_regions):
                    # 跳过 None 值，避免后续处理时出错
                    if region.text is not None:
                        all_texts.append(region.text)
                        batch_text_mapping.append((ctx_idx, region_idx))
                
            if not all_texts:
                # 当前批次没有需要翻译的文本
                results.extend(batch)
                continue
                
            # 批量翻译
            try:
                await self._report_progress('translating')
                # 使用第一个配置进行翻译（假设批次内配置相同）
                sample_config = batch[0][1] if batch else None
                if sample_config:
                    # ✅ 合并当前批次所有图片的text_regions（用于AI断句）
                    # 创建临时ctx，避免影响原始ctx
                    merged_ctx = Context()
                    merged_ctx.config = sample_config  # 复制配置
                    
                    all_regions = []
                    for ctx, _ in batch:
                        if ctx.text_regions:
                            all_regions.extend(ctx.text_regions)
                    merged_ctx.text_regions = all_regions
                    
                    # 复制第一个ctx的其他必要属性
                    first_ctx = batch[0][0]
                    if hasattr(first_ctx, 'from_lang'):
                        merged_ctx.from_lang = first_ctx.from_lang
                    
                    # ✅ 加载AI断句prompt和自定义HQ prompt
                    merged_ctx = await self._load_and_prepare_prompts(sample_config, merged_ctx)
                    
                    logger.debug(f"[Batch] Merged {len(all_regions)} text regions from {len(batch)} images for AI line breaking")
                    
                    # 支持批量翻译 - 传递合并后的上下文（仅用于AI断句）
                    batch_contexts = [ctx for ctx, config in batch]
                    
                    # 计算当前批次在所有页面中的索引（用于上下文）
                    # i 是当前批次的起始索引（相对于本次translate_batch调用的所有图片）
                    # self.all_page_translations 包含之前所有已翻译的页面
                    page_index = len(self.all_page_translations) + i
                    
                    # 准备batch_original_texts（用于并发模式的上下文）
                    batch_original_texts = []
                    for ctx, _ in batch:
                        if ctx.text_regions:
                            image_data = {
                                'original_texts': [region.text for region in ctx.text_regions if region.text is not None]
                            }
                            batch_original_texts.append(image_data)
                    
                    # ✅ 为HQ翻译器准备high_quality_batch_data（包含图片和text_regions）
                    # 这是HQ翻译器进入高质量批量模式的必要条件，也是AI断句检查能正常工作的前提
                    if sample_config.translator.translator in [Translator.openai_hq, Translator.gemini_hq]:
                        hq_batch_data = []
                        global_text_index = 1  # 全局文本编号从1开始（与提示词中的编号一致）
                        for ctx, _ in batch:
                            if ctx.text_regions:
                                num_regions = len(ctx.text_regions)
                                # 为当前图片生成全局连续的文本编号
                                text_order = list(range(global_text_index, global_text_index + num_regions))
                                global_text_index += num_regions
                                
                                upscaled_size = None
                                # 使用超分后的图片尺寸（如果有超分），否则使用上色后的图片尺寸
                                # 注意：需要处理 PIL Image 和 numpy array 两种情况
                                from PIL import Image as PILImage
                                if hasattr(ctx, 'upscaled') and ctx.upscaled is not None:
                                    if isinstance(ctx.upscaled, PILImage.Image):
                                        w, h = ctx.upscaled.size
                                        upscaled_size = (h, w)  # 转换为 (height, width)
                                    else:
                                        upscaled_size = ctx.upscaled.shape[:2]  # numpy: (height, width)
                                elif hasattr(ctx, 'img_colorized') and ctx.img_colorized is not None:
                                    if isinstance(ctx.img_colorized, PILImage.Image):
                                        w, h = ctx.img_colorized.size
                                        upscaled_size = (h, w)
                                    else:
                                        upscaled_size = ctx.img_colorized.shape[:2]
                                elif hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
                                    if isinstance(ctx.img_rgb, PILImage.Image):
                                        w, h = ctx.img_rgb.size
                                        upscaled_size = (h, w)
                                    else:
                                        upscaled_size = ctx.img_rgb.shape[:2]
                                
                                img_data = {
                                    'image': ctx.input if hasattr(ctx, 'input') else None,
                                    'text_regions': ctx.text_regions,
                                    'original_texts': [region.text for region in ctx.text_regions if region.text is not None],
                                    'text_order': text_order,
                                    'upscaled_size': upscaled_size
                                }
                                hq_batch_data.append(img_data)
                        
                        if hq_batch_data:
                            merged_ctx.high_quality_batch_data = hq_batch_data
                            logger.debug(f"[Batch] Prepared high_quality_batch_data for {len(hq_batch_data)} images")
                    
                    translated_texts = await self._batch_translate_texts(
                        all_texts, 
                        sample_config, 
                        merged_ctx, 
                        batch_contexts,
                        page_index=page_index,
                        batch_index=0,  # 批量处理时第一张图的批次索引为0
                        batch_original_texts=batch_original_texts
                    )
                else:
                    translated_texts = all_texts  # 无法翻译时保持原文
                    
                # 将翻译结果分配回各个context
                text_idx = 0
                for ctx_idx, (ctx, config) in enumerate(batch):
                    if not ctx.text_regions:  # 检查text_regions是否为None或空
                        continue
                    for region_idx, region in enumerate(ctx.text_regions):
                        if text_idx < len(translated_texts):
                            region.translation = translated_texts[text_idx]
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                            text_idx += 1
                        
                # 应用后处理逻辑（括号修正、过滤等）
                for ctx, config in batch:
                    if ctx.text_regions:
                        ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                
                # ✅ 立即保存当前批次的翻译结果到all_page_translations，供下一个批次使用上下文
                for ctx, config in batch:
                    if ctx.text_regions:
                        page_entries = self._build_page_context_entries(ctx)
                        self.all_page_translations.append(page_entries)
                        logger.debug(f"[Batch Context] Saved {len(page_entries)} translations for next batch context")
                        
                # Prune history to prevent memory leak
                self._prune_context_history()
                        
                # 批次级别的目标语言检查
                if batch and batch[0][1].translator.enable_post_translation_check:
                    # 收集批次内所有页面的filtered regions
                    all_batch_regions = []
                    for ctx, config in batch:
                        if ctx.text_regions:
                            all_batch_regions.extend(ctx.text_regions)
                    
                    # 进行批次级别的目标语言检查
                    batch_lang_check_result = True
                    if all_batch_regions and len(all_batch_regions) > 10:
                        sample_config = batch[0][1]
                        logger.info(f"Starting batch-level target language check with {len(all_batch_regions)} regions...")
                        batch_lang_check_result = await self._check_target_language_ratio(
                            all_batch_regions,
                            sample_config.translator.target_lang,
                            min_ratio=0.5
                        )
                        
                        if not batch_lang_check_result:
                            logger.warning("Batch-level target language ratio check failed")
                            
                            # 批次重新翻译逻辑
                            max_batch_retry = sample_config.translator.post_check_max_retry_attempts
                            batch_retry_count = 0
                            
                            while batch_retry_count < max_batch_retry and not batch_lang_check_result:
                                batch_retry_count += 1
                                logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry}")
                                
                                # 重新翻译批次内所有区域
                                all_original_texts = []
                                region_mapping = []  # 记录每个text属于哪个ctx
                                
                                for ctx_idx, (ctx, config) in enumerate(batch):
                                    if ctx.text_regions:
                                        for region in ctx.text_regions:
                                            if hasattr(region, 'text') and region.text:
                                                all_original_texts.append(region.text)
                                                region_mapping.append((ctx_idx, region))
                                
                                if all_original_texts:
                                    try:
                                        # 重新批量翻译
                                        logger.info(f"Retrying translation for {len(all_original_texts)} regions...")
                                        new_translations = await self._batch_translate_texts(all_original_texts, sample_config, batch[0][0])
                                        
                                        # 更新翻译结果到各个region
                                        for i, (ctx_idx, region) in enumerate(region_mapping):
                                            if i < len(new_translations) and new_translations[i]:
                                                old_translation = region.translation
                                                region.translation = new_translations[i]
                                                logger.debug(f"Region {i+1} translation updated: '{old_translation}' -> '{new_translations[i]}'")
                                        
                                        # 重新收集所有regions并检查目标语言比例
                                        all_batch_regions = []
                                        for ctx, config in batch:
                                            if ctx.text_regions:
                                                all_batch_regions.extend(ctx.text_regions)
                                        
                                        logger.info(f"Re-checking batch-level target language ratio after batch retry {batch_retry_count}...")
                                        batch_lang_check_result = await self._check_target_language_ratio(
                                            all_batch_regions,
                                            sample_config.translator.target_lang,
                                            min_ratio=0.5
                                        )
                                        
                                        if batch_lang_check_result:
                                            logger.info("Batch-level target language check passed")
                                            break
                                        else:
                                            logger.warning("Batch-level target language check still failed")
                                            
                                    except Exception as e:
                                        logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                                        break
                                else:
                                    logger.warning("No text found for batch retry")
                                    break
                            
                            if not batch_lang_check_result:
                                logger.error(f"Batch-level target language check failed after all {max_batch_retry} batch retries")
                    else:
                        logger.info(f"Skipping batch-level target language check: only {len(all_batch_regions)} regions (threshold: 10)")
                    
                    # 统一的成功信息
                    if batch_lang_check_result:
                        logger.info("All translation regions passed post-translation check.")
                    else:
                        logger.warning("Some translation regions failed post-translation check.")
                        
                # 过滤逻辑（简化版本，保留主要过滤条件）
                for ctx, config in batch:
                    if ctx.text_regions:
                        new_text_regions = []
                        for region in ctx.text_regions:
                            should_filter = False
                            filter_reason = ""

                            if not region.translation.strip():
                                should_filter = True
                                filter_reason = "Translation contain blank areas"
                            elif config.translator.translator != Translator.none:
                                if region.translation.isnumeric():
                                    should_filter = True
                                    filter_reason = "Numeric translation"
                                elif not config.translator.translator == Translator.original:
                                    if self._should_filter_identical_translation(config, region):
                                        should_filter = True
                                        filter_reason = "Translation identical to original"

                            if should_filter:
                                if region.translation.strip():
                                    logger.info(f'Filtered out: {region.translation}')
                                    logger.info(f'Reason: {filter_reason}')
                            else:
                                new_text_regions.append(region)
                        ctx.text_regions = new_text_regions
                        
                results.extend(batch)
                
            except Exception as e:
                # 安全地获取异常信息
                try:
                    error_msg = str(e)
                except Exception:
                    error_msg = f"无法获取异常信息 (异常类型: {type(e).__name__})"
                
                logger.error(f"Error in batch translation: {error_msg}")
                logger.error(traceback.format_exc())
                if not self.ignore_errors:
                    raise
                # 错误时标记当前批次文件失败，不再回退原文
                for ctx, config in batch:
                    self._mark_context_failure(ctx, FileTranslationFailure("translation", e), stage='translation')
                    ctx.text_regions = []
                results.extend(batch)
            
            # ✅ 翻译批次完成后清理内存
            # 清理merged_ctx和batch中的临时数据
                if 'merged_ctx' in locals() and merged_ctx:
                    merged_ctx.text_regions = None
                    merged_ctx = None
                batch = None
                self._cleanup_gpu_memory(aggressive=True)

        return results

    async def _concurrent_translate_contexts(self, contexts_with_configs: List[tuple]) -> List[tuple]:
        """
        并发处理翻译步骤，为每个图片单独发送翻译请求，避免合并大批次
        """

        # 在并发模式下，先保存所有页面的原文用于上下文
        batch_original_texts = []  # 存储当前批次的原文
        if self.context_size > 0:
            for i, (ctx, config) in enumerate(contexts_with_configs):
                if ctx.text_regions:
                    # 保存当前页面的原文
                    page_texts = {}
                    for j, region in enumerate(ctx.text_regions):
                        page_texts[j] = region.text
                    batch_original_texts.append(page_texts)

                    # 确保 _original_page_texts 有足够的长度
                    while len(self._original_page_texts) <= len(self.all_page_translations) + i:
                        self._original_page_texts.append({})

                    self._original_page_texts[len(self.all_page_translations) + i] = page_texts
                else:
                    batch_original_texts.append({})

        async def translate_single_context(ctx_config_pair_with_index):
            """翻译单个context的异步函数"""
            ctx, config, page_index, batch_index = ctx_config_pair_with_index
            try:
                if not ctx.text_regions:
                    return ctx, config

                # 收集该context的所有文本
                texts = [region.text for region in ctx.text_regions if region.text is not None]

                if not texts:
                    return ctx, config

                logger.debug(f'Translating {len(texts)} regions for single image in concurrent mode (page {page_index}, batch {batch_index})')

                # 单独翻译这一张图片的文本，传递页面索引和批次索引用于正确的上下文
                translated_texts = await self._batch_translate_texts(
                    texts, config, ctx,
                    page_index=page_index,
                    batch_index=batch_index,
                    batch_original_texts=batch_original_texts
                )

                # 将翻译结果分配回各个region
                for i, region in enumerate(ctx.text_regions):
                    if i < len(translated_texts):
                        region.translation = translated_texts[i]
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                
                # 应用后处理逻辑（括号修正、过滤等）
                if ctx.text_regions:
                    ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                
                # 单页目标语言检查（如果启用）
                if config.translator.enable_post_translation_check and ctx.text_regions:
                    page_lang_check_result = await self._check_target_language_ratio(
                        ctx.text_regions,
                        config.translator.target_lang,
                        min_ratio=0.3  # 对单页使用更宽松的阈值
                    )
                    
                    if not page_lang_check_result:
                        logger.warning("Page-level target language check failed for single image")
                        
                        # 单页重试逻辑
                        max_retry = config.translator.post_check_max_retry_attempts
                        retry_count = 0
                        
                        while retry_count < max_retry and not page_lang_check_result:
                            retry_count += 1
                            logger.info(f"Retrying single image translation {retry_count}/{max_retry}")
                            
                            # 重新翻译
                            original_texts = [region.text for region in ctx.text_regions if hasattr(region, 'text') and region.text]
                            if original_texts:
                                try:
                                    new_translations = await self._batch_translate_texts(original_texts, config, ctx)
                                    
                                    # 更新翻译结果
                                    text_idx = 0
                                    for region in ctx.text_regions:
                                        if hasattr(region, 'text') and region.text and text_idx < len(new_translations):
                                            old_translation = region.translation
                                            region.translation = new_translations[text_idx]
                                            logger.debug(f"Region translation updated: '{old_translation}' -> '{new_translations[text_idx]}'")
                                            text_idx += 1
                                    
                                    # 重新检查
                                    page_lang_check_result = await self._check_target_language_ratio(
                                        ctx.text_regions,
                                        config.translator.target_lang,
                                        min_ratio=0.3
                                    )
                                    
                                    if page_lang_check_result:
                                        logger.info("Single image target language check passed")
                                        break
                                        
                                except Exception as e:
                                    logger.error(f"Error during single image retry {retry_count}: {e}")
                                    break
                            else:
                                break
                        
                        if not page_lang_check_result:
                            logger.warning(f"Single image target language check failed after all {max_retry} retries")
                
                # 过滤逻辑
                if ctx.text_regions:
                    new_text_regions = []
                    for region in ctx.text_regions:
                        should_filter = False
                        filter_reason = ""

                        if not region.translation.strip():
                            should_filter = True
                            filter_reason = "Translation contain blank areas"
                        elif config.translator.translator != Translator.none:
                            if region.translation.isnumeric():
                                should_filter = True
                                filter_reason = "Numeric translation"
                            elif not config.translator.translator == Translator.original:
                                if self._should_filter_identical_translation(config, region):
                                    should_filter = True
                                    filter_reason = "Translation identical to original"

                        if should_filter:
                            if region.translation.strip():
                                logger.info(f'Filtered out: {region.translation}')
                                logger.info(f'Reason: {filter_reason}')
                        else:
                            new_text_regions.append(region)
                    ctx.text_regions = new_text_regions
                
                return ctx, config
                
            except Exception as e:
                logger.error(f"Error in concurrent translation for single image: {e}")
                if not self.ignore_errors:
                    raise
                self._mark_context_failure(ctx, FileTranslationFailure("translation", e), stage='translation')
                ctx.text_regions = []
                return ctx, config
        
        # 创建并发任务，为每个任务添加页面索引和批次索引
        tasks = []
        for i, ctx_config_pair in enumerate(contexts_with_configs):
            # 计算当前页面在整个翻译序列中的索引
            page_index = len(self.all_page_translations) + i
            batch_index = i  # 在当前批次中的索引
            ctx_config_pair_with_index = (*ctx_config_pair, page_index, batch_index)
            task = asyncio.create_task(translate_single_context(ctx_config_pair_with_index))
            tasks.append(task)
        
        logger.info(f'Starting concurrent translation of {len(tasks)} images...')
        
        # 等待所有任务完成
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error in concurrent translation gather: {e}")
            raise
        
        # 处理结果，检查是否有异常
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Image {i+1} concurrent translation failed: {result}")
                if not self.ignore_errors:
                    raise result
                ctx, config = contexts_with_configs[i]
                self._mark_context_failure(ctx, FileTranslationFailure("translation", result), stage='translation')
                ctx.text_regions = []
                final_results.append((ctx, config))
            else:
                final_results.append(result)
        
        logger.info(f'Concurrent translation completed: {len(final_results)} images processed')
        return final_results

    async def _batch_translate_texts(self, texts: List[str], config: Config, ctx: Context, batch_contexts: List[Context] = None, page_index: int = None, batch_index: int = None, batch_original_texts: List[dict] = None) -> List[str]:
        """
        批量翻译文本列表，使用现有的翻译器接口

        Args:
            texts: 要翻译的文本列表
            config: 配置对象
            ctx: 上下文对象
            batch_contexts: 批处理上下文列表
            page_index: 当前页面索引，用于并发模式下的上下文计算
            batch_index: 当前页面在批次中的索引
            batch_original_texts: 当前批次的原文数据
        """
        if config.translator.translator == Translator.none:
            return ["" for _ in texts]



        # 如果是OpenAI翻译器、Gemini翻译器或高质量翻译器，需要处理上下文
        if config.translator.translator in [Translator.openai, Translator.gemini, Translator.openai_hq, Translator.gemini_hq]:
            if config.translator.translator == Translator.openai:
                from .translators.openai import OpenAITranslator
                translator = OpenAITranslator()
            elif config.translator.translator == Translator.gemini:
                from .translators.gemini import GeminiTranslator
                translator = GeminiTranslator()
            elif config.translator.translator == Translator.openai_hq:
                from .translators.openai_hq import OpenAIHighQualityTranslator
                translator = OpenAIHighQualityTranslator()
            elif config.translator.translator == Translator.gemini_hq:
                from .translators.gemini_hq import GeminiHighQualityTranslator
                translator = GeminiHighQualityTranslator()

            translator.parse_args(config)
            # 注意：-1 表示无限重试，也是有效值
            # 重试次数统一从 cli.attempts 解析
            
            # 传递取消检查回调给翻译器
            if self._cancel_check_callback:
                translator.set_cancel_check_callback(self._cancel_check_callback)

            # 为所有翻译器构建和设置文本上下文（包括HQ翻译器）
            done_pages = self.all_page_translations
            if self.context_size > 0 and done_pages:
                pages_expected = min(self.context_size, len(done_pages))
                non_empty_pages = [
                    page for page in done_pages
                    if self._page_has_context_entries(page)
                ]
                pages_used = min(self.context_size, len(non_empty_pages))
                skipped = pages_expected - pages_used
            else:
                pages_used = skipped = 0

            if self.context_size > 0:
                logger.info(f"Context-aware translation enabled with {self.context_size} pages of history")

            # 构建上下文（简化版，不使用批次内上下文）
            prev_ctx = self._build_prev_context(
                use_original_text=False,  # 始终使用翻译结果作为上下文
                current_page_index=page_index,
                batch_index=None,  # 不使用批次内上下文
                batch_original_texts=None
            )
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count('"translation"')
                logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
            if skipped > 0:
                logger.warning(f"Skipped {skipped} pages with no sentences")


            # 将config附加到ctx，供翻译器使用（例如AI断句功能）
            ctx.config = config
            
            # openai_hq、gemini_hq 等需要传递ctx参数
            if config.translator.translator in [Translator.openai_hq, Translator.gemini_hq]:
                # 所有需要上下文的翻译器都在这里传递ctx
                return await translator._translate(
                    ctx.from_lang,
                    config.translator.target_lang,
                    texts,
                    ctx
                )
            else:
                # 普通OpenAI和Gemini需要ctx参数（用于AI断句）
                return await translator._translate(
                    ctx.from_lang,
                    config.translator.target_lang,
                    texts,
                    ctx
                )

        else:
            # 使用通用翻译调度器
            return await dispatch_translation(
                config.translator.translator_gen,
                texts,
                config,
                False,  # use_mtpe removed
                ctx,
                'cpu' if self._gpu_limited_memory else self.device
            )
    
    def _translate_error_message(self, error_msg: str) -> str:
        """将英文错误消息转换为中文提示"""
        error_lower = error_msg.lower()
        
        # OpenAI API 错误
        if '404' in error_msg or 'not found' in error_lower:
            return "❌ 翻译失败：API端点未找到(404错误)\n💡 解决方法：\n1. 检查API地址是否正确配置\n2. 如使用第三方API，确认模型名称是否正确\n3. 确认API密钥是否有效"
        
        if '401' in error_msg or 'unauthorized' in error_lower or 'authentication' in error_lower:
            return "❌ 翻译失败：API认证失败(401错误)\n💡 解决方法：\n1. 检查API密钥是否正确\n2. 确认API密钥是否已激活\n3. 检查账户是否有足够余额"
        
        if '429' in error_msg or 'rate limit' in error_lower:
            return "❌ 翻译失败：API请求频率超限(429错误)\n💡 解决方法：\n1. 等待一段时间后重试\n2. 升级API套餐以提高请求限制\n3. 减小批处理大小"
        
        if '500' in error_msg or '502' in error_msg or '503' in error_msg or 'server error' in error_lower:
            return "❌ 翻译失败：API服务器错误(5xx错误)\n💡 解决方法：\n1. 稍后重试\n2. 检查API服务状态页面\n3. 尝试使用其他翻译服务"
        
        if 'timeout' in error_lower or 'timed out' in error_lower:
            return "❌ 翻译失败：请求超时\n💡 解决方法：\n1. 检查网络连接\n2. 增加超时时间设置\n3. 减小批处理大小或图片数量"
        
        if 'connection' in error_lower:
            return "❌ 翻译失败：网络连接错误\n💡 解决方法：\n1. 检查网络连接\n2. 检查防火墙设置\n3. 如使用代理，确认代理配置正确"
        
        if 'quota' in error_lower or 'balance' in error_lower or 'insufficient' in error_lower:
            return "❌ 翻译失败：API配额不足或余额不足\n💡 解决方法：\n1. 充值API账户\n2. 检查账户配额使用情况\n3. 升级API套餐"
        
        if 'budget' in error_lower or 'exceededbudget' in error_lower:
            return "❌ 翻译失败：API预算已用完\n💡 解决方法：\n1. 在API提供商后台增加预算限制\n2. 充值账户余额\n3. 等待下个计费周期\n4. 暂时使用其他翻译服务"
        
        # 通用错误
        return f"❌ 翻译失败：{error_msg}\n💡 建议：\n1. 检查API配置是否正确\n2. 查看完整日志以获取详细错误信息\n3. 尝试更换翻译服务"

    def _should_filter_identical_translation(self, config: Config, region) -> bool:
        """Keep identical text when no_text_lang_skip is enabled."""
        if getattr(config.translator, 'no_text_lang_skip', False):
            return False
        return region.text.lower().strip() == region.translation.lower().strip()
            
    async def _apply_post_translation_processing(self, ctx: Context, config: Config) -> List:
        """
        应用翻译后处理逻辑（括号修正、过滤等）
        """
        # 检查text_regions是否为None或空
        if not ctx.text_regions:
            return []
            
        check_items = [
            # 圆括号处理
            ["(", "（", "「", "【"],
            ["（", "(", "「", "【"],
            [")", "）", "」", "】"],
            ["）", ")", "」", "】"],
            
            # 方括号处理
            ["[", "［", "【", "「"],
            ["［", "[", "【", "「"],
            ["]", "］", "】", "」"],
            ["］", "]", "】", "」"],
            
            # 引号处理
            ["「", "“", "‘", "『", "【"],
            ["」", "”", "’", "』", "】"],
            ["『", "“", "‘", "「", "【"],
            ["』", "”", "’", "」", "】"],
            
            # 新增【】处理
            ["【", "(", "（", "「", "『", "["],
            ["】", ")", "）", "」", "』", "]"],
        ]

        # 统一渲染：不在翻译后阶段强制替换引号/括号，交由渲染层处理。

        for region in ctx.text_regions:
            if region.text and region.translation:
                # 引号处理逻辑
                if '『' in region.text and '』' in region.text:
                    quote_type = '『』'
                elif '「' in region.text and '」' in region.text:
                    quote_type = '「」'
                elif '【' in region.text and '】' in region.text: 
                    quote_type = '【】'
                else:
                    quote_type = None
                
                if quote_type:
                    src_quote_count = region.text.count(quote_type[0])
                    dst_dquote_count = region.translation.count('"')
                    dst_fwquote_count = region.translation.count('＂')
                    
                    if (src_quote_count > 0 and
                        (src_quote_count == dst_dquote_count or src_quote_count == dst_fwquote_count) and
                        not region.translation.isascii()):
                        
                        if quote_type == '「」':
                            region.translation = re.sub(r'"([^"]*)"', r'「\1」', region.translation)
                        elif quote_type == '『』':
                            region.translation = re.sub(r'"([^"]*)"', r'『\1』', region.translation)
                        elif quote_type == '【】':  
                            region.translation = re.sub(r'"([^"]*)"', r'【\1】', region.translation)

                # 括号修正逻辑
                for v in check_items:
                    num_src_std = region.text.count(v[0])
                    num_src_var = sum(region.text.count(t) for t in v[1:])
                    num_dst_std = region.translation.count(v[0])
                    num_dst_var = sum(region.translation.count(t) for t in v[1:])
                    
                    if (num_src_std > 0 and
                        num_src_std != num_src_var and
                        num_src_std == num_dst_std + num_dst_var):
                        for t in v[1:]:
                            region.translation = region.translation.replace(t, v[0])

                # 统一渲染：这里不做强制替换。

        # 注意：翻译结果的保存移动到了translate方法的最后，确保保存的是最终结果

        # 简繁体转换（OpenCC）
        if config.translator.convert_to_traditional or config.translator.convert_to_simplified:
            try:
                import opencc
                if config.translator.convert_to_traditional:
                    _opencc_converter = opencc.OpenCC('s2twp')
                    logger.info("Applying OpenCC: Simplified → Traditional (s2twp)")
                else:
                    _opencc_converter = opencc.OpenCC('t2s')
                    logger.info("Applying OpenCC: Traditional → Simplified (t2s)")
                for region in ctx.text_regions:
                    if region.translation:
                        original = region.translation
                        region.translation = _opencc_converter.convert(region.translation)
                        if original != region.translation:
                            logger.debug(f"OpenCC: {original} => {region.translation}")
            except ImportError:
                logger.warning("opencc-python-reimplemented not installed, skipping Chinese conversion")

        # 应用后字典
        post_dict = load_dictionary(self.post_dict)
        post_replacements = []  
        for region in ctx.text_regions:  
            original = region.translation  
            region.translation = apply_dictionary(region.translation, post_dict)
            if original != region.translation:  
                post_replacements.append(f"{original} => {region.translation}")  

        if post_replacements:
            logger.info("Post-translation replacements:")
            for replacement in post_replacements:
                logger.info(replacement)
        else:
            logger.info("No post-translation replacements made.")

        # 注:文本替换规则(text_replacements.yaml) 已挪到 rendering 函数 dispatch() 内执行,
        # 在 [BR]/<H> 标记加完之后、画字之前 — 这样 raw 含完整标记,渲染图也用替换后字符。

        # 单个region幻觉检测
        failed_regions = []
        if config.translator.enable_post_translation_check:
            logger.info("Starting post-translation check...")
            
            # 单个region级别的幻觉检测
            for region in ctx.text_regions:
                if region.translation and region.translation.strip():
                    # 只检查重复内容幻觉
                    if await self._check_repetition_hallucination(
                        region.translation, 
                        config.translator.post_check_repetition_threshold,
                        silent=False
                    ):
                        failed_regions.append(region)
            
            # 对失败的区域进行重试
            if failed_regions:
                logger.warning(f"Found {len(failed_regions)} regions that failed repetition check, starting retry...")
                for region in failed_regions:
                    try:
                        logger.info(f"Retrying translation for region with text: '{region.text}'")
                        new_translation = await self._retry_translation_with_validation(region, config, ctx)
                        if new_translation:
                            old_translation = region.translation
                            region.translation = new_translation
                            logger.info(f"Region retry successful: '{old_translation}' -> '{new_translation}'")
                        else:
                            logger.warning(f"Region retry failed, keeping original translation: '{region.translation}'")
                            break
                    except Exception as e:
                        logger.error(f"Error during region retry: {e}")
                        break

        for region in ctx.text_regions:
            if region.translation:
                region.translation = remove_trailing_period_if_needed(
                    region.text,
                    region.translation,
                    bool(getattr(config.translator, 'remove_trailing_period', False)),
                )
        
        return ctx.text_regions

    async def _complete_translation_pipeline(self, ctx: Context, config: Config) -> Context:
        """
        完成翻译后的处理步骤（掩码细化、修复、渲染）
        """
        await self._report_progress('after-translating')

        # Inpaint Only Mode: Skip pipeline, ctx.result already set
        if hasattr(ctx, 'inpaint_only_complete') and ctx.inpaint_only_complete:
            logger.info("Skipping _complete_translation_pipeline (inpaint only mode already complete)")
            return ctx

        # Colorize Only Mode: Skip validation, ctx.result should already be set
        if self.colorize_only:
            return ctx

        if not ctx.text_regions:
            await self._report_progress('error-translating', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)
        elif ctx.text_regions == 'cancel':
            await self._report_progress('cancelled', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Mask refinement
        if ctx.mask is None:
            await self._report_progress('mask-generation')
            try:
                ctx.mask = await self._run_mask_refinement(config, ctx)
            except Exception as e:
                logger.error(f"Error during mask-generation:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise 
                raise FileTranslationFailure("mask-generation", e) from e

        if self.verbose and ctx.mask is not None:
            try:
                inpaint_input_img = await dispatch_inpainting(Inpainter.none, ctx.img_rgb, ctx.mask, config.inpainter,config.inpainter.inpainting_size,
                                                              self.device, self.verbose)
                
                # 保存inpaint_input.png
                inpaint_input_path = self._result_path('inpaint_input.png')
                imwrite_unicode(inpaint_input_path, cv2.cvtColor(inpaint_input_img, cv2.COLOR_RGB2BGR), logger)
                
                # 保存mask_final.png
                mask_final_path = self._result_path('mask_final.png')
                imwrite_unicode(mask_final_path, ctx.mask, logger)
            except Exception as e:
                logger.error(f"Error saving debug images (inpaint_input.png, mask_final.png): {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # -- Inpainting
        if self._should_skip_inpainting_for_ai_renderer(config):
            logger.info("AI renderer selected: skipping inpainting and using original work image as render base.")
            ctx.img_inpainted = ctx.img_rgb
        else:
            await self._report_progress('inpainting')
            try:
                ctx.img_inpainted = await self._run_inpainting(config, ctx)
                
                # ✅ Inpainting完成后强制GC和GPU清理
                self._cleanup_gpu_memory()

            except Exception as e:
                logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
                if not self.ignore_errors:
                    raise
                raise FileTranslationFailure("inpainting", e) from e

        if self.verbose:
            try:
                inpainted_path = self._result_path('inpainted.png')
                imwrite_unicode(inpainted_path, cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)
            except Exception as e:
                logger.error(f"Error saving inpainted.png debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # 保存inpainted图片到新目录结构（用于可编辑图片功能）
        # 仅在开启“图片可编辑”时保存
        if self.save_text and hasattr(ctx, 'image_name') and ctx.image_name and ctx.img_inpainted is not None:
            self._save_inpainted_image(ctx.image_name, ctx.img_inpainted)

        # -- Rendering
        await self._report_progress('rendering')

        # 在rendering状态后立即发送文件夹信息，用于前端精确检查final.png
        if hasattr(self, '_progress_hooks') and self._current_image_context:
            folder_name = self._current_image_context['subfolder']
            # 发送特殊格式的消息，前端可以解析
            await self._report_progress(f'rendering_folder:{folder_name}')

        try:
            ctx.img_rendered = await self._run_text_rendering(config, ctx)
        except Exception as e:
            logger.error(f"Error during rendering:\n{traceback.format_exc()}")
            if not self.ignore_errors:
                raise
            raise FileTranslationFailure("rendering", e) from e

        await self._report_progress('finished', True)
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
        
        # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
        if self.verbose:
            ctx.debug_folder = self._get_image_subfolder()

        return await self._revert_upscale(config, ctx)
    
    async def _check_repetition_hallucination(self, text: str, threshold: int = 5, silent: bool = False) -> bool:
        """
        检查文本是否包含重复内容（模型幻觉）
        Check if the text contains repetitive content (model hallucination)
        """
        if not text or len(text.strip()) < threshold:
            return False
            
        # 检查字符级重复
        consecutive_count = 1
        prev_char = None
        
        for char in text:
            if char == prev_char:
                consecutive_count += 1
                if consecutive_count >= threshold:
                    if not silent:
                        logger.warning(f'Detected character repetition hallucination: "{text}" - repeated character: "{char}", consecutive count: {consecutive_count}')
                    return True
            else:
                consecutive_count = 1
            prev_char = char
        
        # 检查词语级重复（按字符分割中文，按空格分割其他语言）
        segments = re.findall(r'[\u4e00-\u9fff]|\S+', text)
        
        if len(segments) >= threshold:
            consecutive_segments = 1
            prev_segment = None
            
            for segment in segments:
                if segment == prev_segment:
                    consecutive_segments += 1
                    if consecutive_segments >= threshold:
                        if not silent:
                            logger.warning(f'Detected word repetition hallucination: "{text}" - repeated segment: "{segment}", consecutive count: {consecutive_segments}')
                        return True
                else:
                    consecutive_segments = 1
                prev_segment = segment
        
        # 检查短语级重复
        words = text.split()
        if len(words) >= threshold * 2:
            for i in range(len(words) - threshold + 1):
                phrase = ' '.join(words[i:i + threshold//2])
                remaining_text = ' '.join(words[i + threshold//2:])
                if phrase in remaining_text:
                    phrase_count = text.count(phrase)
                    if phrase_count >= 3:  # 降低短语重复检测阈值
                        if not silent:
                            logger.warning(f'Detected phrase repetition hallucination: "{text}" - repeated phrase: "{phrase}", occurrence count: {phrase_count}')
                        return True
                        
        return False

    async def _check_target_language_ratio(self, text_regions: List, target_lang: str, min_ratio: float = 0.5) -> bool:
        """
        检查翻译结果中目标语言的占比是否达到要求
        使用py3langid进行语言检测
        Check if the target language ratio meets the requirement by detecting the merged translation text
        
        Args:
            text_regions: 文本区域列表
            target_lang: 目标语言代码
            min_ratio: 最小目标语言占比（此参数在新逻辑中不使用，保留为兼容性）
            
        Returns:
            bool: True表示通过检查，False表示未通过
        """
        if not text_regions or len(text_regions) <= 10:
            # 如果区域数量不超过10个，跳过此检查
            return True
            
        # 合并所有翻译文本
        all_translations = []
        for region in text_regions:
            translation = getattr(region, 'translation', '')
            if translation and translation.strip():
                all_translations.append(translation.strip())
        
        if not all_translations:
            logger.debug('No valid translation texts for language ratio check')
            return True
            
        # 将所有翻译合并为一个文本进行检测
        merged_text = ''.join(all_translations)
        
        # logger.info(f'Target language check - Merged text preview (first 200 chars): "{merged_text[:200]}"')
        # logger.info(f'Target language check - Total merged text length: {len(merged_text)} characters')
        # logger.info(f'Target language check - Number of regions: {len(all_translations)}')
        
        # 使用py3langid进行语言检测
        try:
            detected_lang, confidence = langid.classify(merged_text)
            detected_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
            if detected_language != 'UNKNOWN':
                detected_language = detected_language.upper()
            
            # logger.info(f'Target language check - py3langid result: "{detected_lang}" -> "{detected_language}" (confidence: {confidence:.3f})')
        except Exception as e:
            logger.debug(f'py3langid failed for merged text: {e}')
            detected_language = 'UNKNOWN'
        
        # 检查检测出的语言是否为目标语言
        is_target_lang = (detected_language == target_lang.upper())
        
        # logger.info(f'Target language check: Detected language "{detected_language}" using py3langid (confidence: {confidence:.3f})')
        # logger.info(f'Target language check: Target is "{target_lang.upper()}"')
        # logger.info(f'Target language check result: {"PASSED" if is_target_lang else "FAILED"}')
        
        return is_target_lang

    async def _validate_translation(self, original_text: str, translation: str, target_lang: str, config, ctx: Context = None, silent: bool = False, page_lang_check_result: bool = None) -> bool:
        """
        验证翻译质量（包含目标语言比例检查和幻觉检测）
        Validate translation quality (includes target language ratio check and hallucination detection)
        
        Args:
            page_lang_check_result: 页面级目标语言检查结果，如果为None则进行检查，如果已有结果则直接使用
        """
        if not config.translator.enable_post_translation_check:
            return True
            
        if not translation or not translation.strip():
            return True
        
        # 1. 目标语言比例检查（页面级别）
        if page_lang_check_result is None and ctx and ctx.text_regions and len(ctx.text_regions) > 10:
            # 进行页面级目标语言检查
            page_lang_check_result = await self._check_target_language_ratio(
                ctx.text_regions,
                target_lang,
                min_ratio=0.5
            )
            
        # 如果页面级检查失败，直接返回失败
        if page_lang_check_result is False:
            if not silent:
                logger.debug("Target language ratio check failed for this region")
            return False
        
        # 2. 检查重复内容幻觉（region级别）
        if await self._check_repetition_hallucination(
            translation, 
            config.translator.post_check_repetition_threshold,
            silent
        ):
            return False
                
        return True

    async def _retry_translation_with_validation(self, region, config: Config, ctx: Context) -> str:
        """
        带验证的重试翻译
        Retry translation with validation
        """
        original_translation = region.translation
        max_attempts = config.translator.post_check_max_retry_attempts
        
        for attempt in range(max_attempts):
            # 验证当前翻译 - 在重试过程中只检查单个region（幻觉检测），不进行页面级检查
            is_valid = await self._validate_translation(
                region.text, 
                region.translation, 
                config.translator.target_lang,
                config,
                ctx=None,  # 不传ctx避免页面级检查
                silent=True,  # 重试过程中禁用日志输出
                page_lang_check_result=True  # 传入True跳过页面级检查，只做region级检查
            )
            
            if is_valid:
                if attempt > 0:
                    logger.info(f'Post-translation check passed (Attempt {attempt + 1}/{max_attempts}): "{region.translation}"')
                return region.translation
            
            # 如果不是最后一次尝试，进行重新翻译
            if attempt < max_attempts - 1:
                logger.warning(f'Post-translation check failed (Attempt {attempt + 1}/{max_attempts}), re-translating: "{region.text}"')
                
                try:
                    # 单独重新翻译这个文本区域
                    if config.translator.translator != Translator.none:
                        from .translators import dispatch
                        retranslated = await dispatch(
                            config.translator.translator_gen,
                            [region.text],
                            config.translator,
                            False,  # use_mtpe removed
                            ctx,
                            'cpu' if self._gpu_limited_memory else self.device
                        )
                        if retranslated:
                            region.translation = retranslated[0]
                            
                            # 应用格式化处理
                            if config.render.uppercase:
                                region.translation = region.translation.upper()
                            elif config.render.lowercase:
                                region.translation = region.translation.lower()
                                
                            logger.info(f'Re-translation finished: "{region.text}" -> "{region.translation}"')
                        else:
                            logger.warning(f'Re-translation failed, keeping original translation: "{original_translation}"')
                            region.translation = original_translation
                            break
                    else:
                        logger.warning('Translator is none, cannot re-translate.')
                        break
                        
                except Exception as e:
                    logger.error(f'Error during re-translation: {e}')
                    region.translation = original_translation
                    break
            else:
                logger.warning(f'Post-translation check failed, maximum retry attempts ({max_attempts}) reached, keeping original translation: "{original_translation}"')
                region.translation = original_translation
        
        return region.translation

    def _update_translation_map(self, source_path: str, translated_path: str):
        """在输出目录创建或更新 translation_map.json"""
        try:
            output_dir = os.path.dirname(translated_path)
            map_path = os.path.join(output_dir, 'translation_map.json')
            
            # 规范化路径以确保一致性
            source_path_norm = os.path.normpath(source_path)
            translated_path_norm = os.path.normpath(translated_path)

            translation_map = {}
            if os.path.exists(map_path):
                with open(map_path, 'r', encoding='utf-8') as f:
                    try:
                        translation_map = json.load(f)
                    except json.JSONDecodeError:
                        logger.warning(f"Could not decode {map_path}, creating a new one.")
            
            # 使用翻译后的路径作为键，确保唯一性
            translation_map[translated_path_norm] = source_path_norm
            
            with open(map_path, 'w', encoding='utf-8') as f:
                json.dump(translation_map, f, ensure_ascii=False, indent=4)

        except Exception as e:
            logger.error(f"Failed to update translation map: {e}")

    async def _translate_batch_replace_translation(self, images_with_configs: List[tuple], save_info: dict = None, global_offset: int = 0, global_total: int = None) -> List[Context]:
        """
        替换翻译模式：从翻译图提取OCR结果并应用到生肉图
        
        流程：
        1. 对生肉图执行检测+OCR，过滤低置信度区域
        2. 查找对应的翻译图，执行检测+OCR
        3. 区域匹配（考虑尺寸缩放）
        4. 使用匹配的区域执行修复和渲染
        
        Args:
            images_with_configs: List of (image, config) tuples
            save_info: 保存配置
            global_offset: 全局偏移量
            global_total: 全局总图片数
        """
        from .utils.replace_translation import translate_batch_replace_translation
        return await translate_batch_replace_translation(self, images_with_configs, save_info, global_offset, global_total)

    async def _translate_batch_high_quality(self, images_with_configs: List[tuple], save_info: dict = None, global_offset: int = 0, global_total: int = None) -> List[Context]:
        """
        高质量翻译模式：按批次滚动处理，每批独立完成预处理、翻译、渲染全流程。
        如果提供了save_info，则在每批处理后直接保存。
        
        Args:
            images_with_configs: List of (image, config) tuples
            save_info: 保存配置
            global_offset: 全局偏移量，用于显示正确的图片编号
            global_total: 全局总图片数，用于显示正确的总批次数
        """
        batch_size = self.batch_size if self.batch_size > 1 else 3  # 统一使用batch_size参数
        logger.info(f"Starting high quality translation in rolling batch mode with batch size: {batch_size}")
        results = []
        
        # 如果提供了全局总数，使用它来计算总批次数；否则使用当前批次的图片数
        display_total = global_total if global_total is not None else len(images_with_configs)
        
        total_images = len(images_with_configs)

        async def report_completed_image_progress():
            if display_total <= 0:
                return
            completed = min(global_offset + len(results), display_total)
            failed_count = sum(1 for ctx in results if getattr(ctx, 'translation_error', None))
            await self._report_progress(f"batch:{completed}:{completed}:{display_total}:{failed_count}")

        for batch_start in range(0, total_images, batch_size):
            # 检查是否被取消
            await asyncio.sleep(0)
            self._check_cancelled()  # 检查取消标志

            batch_end = min(batch_start + batch_size, total_images)
            current_batch_items = images_with_configs[batch_start:batch_end]

            # 计算全局图片编号（考虑前端分批加载的偏移量）
            global_batch_start = global_offset + batch_start + 1
            global_batch_end = global_offset + batch_end
            global_batch_num = (global_offset + batch_start) // batch_size + 1
            global_total_batches = (display_total + batch_size - 1) // batch_size
            progress_state = f"batch:{global_batch_start}:{global_batch_end}:{display_total}"
            
            logger.info(f"Processing rolling batch {global_batch_num}/{global_total_batches} (images {global_batch_start}-{global_batch_end})")

            current_batch_images, load_error_contexts = self._materialize_batch_inputs(current_batch_items)
            if load_error_contexts:
                results.extend(load_error_contexts)
                await report_completed_image_progress()
            if not current_batch_images:
                await self._report_progress(progress_state)
                continue

            # 阶段一：预处理当前批次
            preprocessed_contexts = []
            for i, (image, config) in enumerate(current_batch_images):
                # 检查是否被取消
                await asyncio.sleep(0)
                self._check_cancelled()  # 检查取消标志
                try:
                    self._set_image_context(config, image)
                    # ✅ 保存context以便渲染阶段复用，避免生成两个文件夹
                    from .utils.generic import get_image_md5
                    image_md5 = get_image_md5(image)
                    self._save_current_image_context(image_md5)
                    ctx = await self._translate_until_translation(image, config)
                    if hasattr(image, 'name'):
                        ctx.image_name = image.name
                    preprocessed_contexts.append((ctx, config))
                except Exception as e:
                    logger.error(f"Error pre-processing image {i+1} in batch: {e}", exc_info=True)
                    ctx = self._build_stage_error_context(image, e, config, stage='preprocessing')
                    preprocessed_contexts.append((ctx, config))

            # 阶段二：翻译当前批次
            batch_data = []
            global_text_index = 1  # 全局文本编号从1开始（与提示词中的编号一致）
            for ctx, config in preprocessed_contexts:
                num_regions = len(ctx.text_regions) if ctx.text_regions else 0
                # 为当前图片生成全局连续的文本编号
                text_order = list(range(global_text_index, global_text_index + num_regions))
                global_text_index += num_regions
                
                # 获取超分后的尺寸（用于坐标转换）
                upscaled_size = None
                if hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
                    upscaled_size = ctx.img_rgb.shape[:2]  # (height, width)
                
                image_data = {
                    'image': ctx.input,
                    'text_regions': ctx.text_regions if ctx.text_regions else [],
                    'original_texts': [region.text for region in ctx.text_regions if region.text is not None] if ctx.text_regions else [],
                    'text_order': text_order,
                    'upscaled_size': upscaled_size
                }
                batch_data.append(image_data)

            if any(data['original_texts'] for data in batch_data):
                try:
                    sample_config = preprocessed_contexts[0][1] if preprocessed_contexts else None
                    if sample_config:
                        # ✅ 创建新的Context用于enhanced_ctx，避免污染第一张图片的context
                        enhanced_ctx = Context()
                        # 复制第一张图片的必要属性
                        if preprocessed_contexts:
                            first_ctx = preprocessed_contexts[0][0]
                            if hasattr(first_ctx, 'input'):
                                enhanced_ctx.input = first_ctx.input
                            if hasattr(first_ctx, 'img_rgb'):
                                enhanced_ctx.img_rgb = first_ctx.img_rgb
                        
                        enhanced_ctx.high_quality_batch_data = batch_data

                        # ✅ 合并所有页面的text_regions到enhanced_ctx（用于AI断句）
                        all_regions = []
                        for ctx, _ in preprocessed_contexts:
                            if ctx.text_regions:
                                all_regions.extend(ctx.text_regions)
                        enhanced_ctx.text_regions = all_regions
                        logger.debug(f"[HQ Batch] Merged {len(all_regions)} text regions from {len(preprocessed_contexts)} pages")

                        # Centralized prompt loading logic
                        enhanced_ctx = await self._load_and_prepare_prompts(sample_config, enhanced_ctx)
                        
                        all_texts = [text for data in batch_data for text in data['original_texts']]
                        text_mapping = [(img_idx, region_idx) for img_idx, data in enumerate(batch_data) for region_idx, _ in enumerate(data['original_texts'])]
                        
                        logger.info(f"Sending batch data with {len(preprocessed_contexts)} images, {len(all_texts)} text regions to high quality translator")
                        
                        # 计算当前批次在所有页面中的索引（用于上下文）
                        # batch_start 是当前批次的起始索引（相对于本次translate_batch调用的所有图片）
                        # self.all_page_translations 包含之前所有已翻译的页面
                        page_index = len(self.all_page_translations) + batch_start
                        
                        # 高质量翻译模式：批量翻译
                        translated_texts = await self._batch_translate_texts(
                            all_texts, 
                            sample_config, 
                            enhanced_ctx,
                            page_index=page_index,
                            batch_index=None,  # 不使用批次内上下文
                            batch_original_texts=None
                        )
                        
                        for text_idx, (img_idx, region_idx) in enumerate(text_mapping):
                            if text_idx < len(translated_texts):
                                ctx, config = preprocessed_contexts[img_idx]
                                if ctx.text_regions and region_idx < len(ctx.text_regions):
                                    region = ctx.text_regions[region_idx]
                                    region.translation = translated_texts[text_idx]
                                    region.target_lang = config.translator.target_lang
                                    region._alignment = config.render.alignment
                                    region._direction = config.render.direction
                        
                        for ctx, config in preprocessed_contexts:
                            if ctx.text_regions:
                                ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                        
                        # ✅ 立即保存当前批次的翻译结果到all_page_translations，供下一个批次使用上下文
                        for ctx, config in preprocessed_contexts:
                            if ctx.text_regions:
                                # 保存译文和原始区域数量，供历史上下文与 AI 断句复用
                                page_entries = self._build_page_context_entries(ctx)
                                self.all_page_translations.append(page_entries)
                                logger.debug(f"[HQ Batch Context] Saved {len(page_entries)} translations for next batch context")
                                
                                # 保存原文（用于并发模式的上下文）
                                page_original_texts = {i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                                                      for i, r in enumerate(ctx.text_regions)}
                                self._original_page_texts.append(page_original_texts)
                                logger.debug(f"[HQ Batch Context] Saved {len(page_original_texts)} original texts for next batch context")
                                
                                # Prune history to prevent memory leak
                                self._prune_context_history()
                                
                except Exception as e:
                    logger.error(f"Error in high quality batch translation: {e}")
                    if not self.ignore_errors:
                        raise
                    for ctx, config in preprocessed_contexts:
                        self._mark_context_failure(ctx, FileTranslationFailure("translation", e), stage='translation')
                        ctx.text_regions = []
            # --- NEW: Handle Generate and Export for High-Quality Mode ---
            if self.generate_and_export:
                logger.info("'Generate and Export' mode enabled. Skipping rendering.")
                for ctx, config in preprocessed_contexts:
                    if getattr(ctx, 'translation_error', None):
                        results.append(ctx)
                        await report_completed_image_progress()
                        continue
                    await self._handle_generate_and_export(ctx, config)

                    # ✅ 标记成功（导出翻译完成）
                    ctx.success = True
                    results.append(ctx)
                    await report_completed_image_progress()
                
                # ✅ 批次完成后立即清理内存
                self._cleanup_batch_memory(
                    current_batch_images=current_batch_images,
                    preprocessed_contexts=preprocessed_contexts,
                    keep_results=True
                )
                await self._report_progress(progress_state)
                
                continue # BUG FIX: Continue to the next batch instead of returning

            # 阶段三：渲染并保存当前批次
            for ctx, config in preprocessed_contexts:
                # 检查是否被取消
                await asyncio.sleep(0)
                self._check_cancelled()  # 检查取消标志
                if getattr(ctx, 'translation_error', None):
                    results.append(ctx)
                    await report_completed_image_progress()
                    continue
                try:
                    if hasattr(ctx, 'input'):
                        from .utils.generic import get_image_md5
                        image_md5 = get_image_md5(ctx.input)
                        if not self._restore_image_context(image_md5):
                            self._set_image_context(config, ctx.input)
                    
                    # Colorize/Upscale/Inpaint Only Mode: Skip rendering pipeline
                    if not self.colorize_only and not self.upscale_only and not self.inpaint_only:
                        ctx = await self._complete_translation_pipeline(ctx, config)
                    
                    # --- BEGIN SAVE LOGIC ---
                    if save_info and ctx.result:
                        try:
                            self._save_and_cleanup_context(ctx, save_info, config, "HQ")
                        except Exception as save_err:
                            logger.error(f"Error saving high-quality result for {os.path.basename(ctx.image_name)}: {save_err}")
                            import traceback
                            logger.error(traceback.format_exc())
                    # --- END SAVE LOGIC ---

                    # 只在save_text或text_output_file启用时保存JSON（包括空的text_regions）
                    if (self.save_text or self.text_output_file) and hasattr(ctx, 'text_regions') and ctx.text_regions is not None and hasattr(ctx, 'image_name') and ctx.image_name:
                        # 使用循环变量中的config，而不是从ctx中获取
                        self._save_text_to_file(ctx.image_name, ctx, config)

                    # ✅ 标记成功
                    ctx.success = True

                    # ✅ 清理中间处理图像（保留text_regions等元数据）
                    self._cleanup_context_memory(ctx, keep_result=True)

                    results.append(ctx)
                    await report_completed_image_progress()
                except Exception as e:
                    logger.error(f"Error rendering image: {e}")
                    if not self.ignore_errors:
                        raise RuntimeError(f"Rendering failed for {os.path.basename(ctx.image_name) if hasattr(ctx, 'image_name') else 'Unknown'}: {e}") from e
                    ctx = self._mark_context_failure(ctx, e, stage='rendering')
                    results.append(ctx)
                    await report_completed_image_progress()
            
            # ✅ 批次完成后立即清理内存（但保留翻译历史供下一批次使用）
#             import gc
            # 1. 清理batch_data中的图像引用
            for data in batch_data:
                if 'image' in data:
                    data['image'] = None
            batch_data.clear()
            
            # 2. 使用统一清理方法
            self._cleanup_batch_memory(
                preprocessed_contexts=preprocessed_contexts,
                keep_results=True
            )
            
            logger.debug(f'[MEMORY] Batch {batch_start//batch_size + 1} cleanup completed (kept translation history for context)')
            await self._report_progress(progress_state)

        logger.info(f"High quality translation completed: processed {len(results)} images")
        return results
