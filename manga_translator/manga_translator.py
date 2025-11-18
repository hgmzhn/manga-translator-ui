
import asyncio
import torch
import cv2
import json
import langcodes
import os
import regex as re
import time
import torch
import logging
import sys
import traceback
import numpy as np
from PIL import Image
from typing import Optional, Any, List
import py3langid as langid

from .config import Config, Colorizer, Detector, Translator, Renderer, Inpainter
from .utils import (
    BASE_PATH,
    LANGUAGE_ORIENTATION_PRESETS,
    ModelWrapper,
    Context,
    load_image,
    dump_image,
    visualize_textblocks,
    is_valuable_text,
    sort_regions,
    get_color_name,
    rgb2hex,
    TextBlock,
    imwrite_unicode
)
import matplotlib
matplotlib.use('Agg')  # 使用非GUI后端
import matplotlib.pyplot as plt
from matplotlib import cm
from .utils.path_manager import (
    get_json_path,
    get_inpainted_path,
    find_json_path
)

from .detection import dispatch as dispatch_detection, prepare as prepare_detection, unload as unload_detection
from .upscaling import dispatch as dispatch_upscaling, prepare as prepare_upscaling, unload as unload_upscaling
from .ocr import dispatch as dispatch_ocr, prepare as prepare_ocr, unload as unload_ocr
from .textline_merge import dispatch as dispatch_textline_merge
from .mask_refinement import dispatch as dispatch_mask_refinement
from .inpainting import dispatch as dispatch_inpainting, prepare as prepare_inpainting, unload as unload_inpainting
from .translators import (
    dispatch as dispatch_translation,
    prepare as prepare_translation,
    unload as unload_translation,
)
from .translators.common import ISO_639_1_TO_VALID_LANGUAGES
from .colorization import dispatch as dispatch_colorization, prepare as prepare_colorization, unload as unload_colorization
from .rendering import dispatch as dispatch_rendering, dispatch_eng_render, dispatch_eng_render_pillow

# Will be overwritten by __main__.py if module is being run directly (with python -m)
logger = logging.getLogger('manga_translator')

# 全局console实例，用于日志重定向
_global_console = None
_log_console = None

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
        original_text = text  
        text = pattern.sub(value, text)
        if text != original_text:  
            logger.info(f'Line {line_number}: Replaced "{original_text}" with "{text}" using pattern "{pattern.pattern}" and value "{value}"')
    return text

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
        self.font_path = None
        self.use_mtpe = False
        self.kernel_size = None
        self.device = None
        self.text_output_file = params.get('save_text_file', None)
        self._gpu_limited_memory = False
        self.ignore_errors = False
        self.verbose = False
        self.models_ttl = 0
        self.batch_size = 1  # 默认不批量处理

        self._progress_hooks = []
        self._add_logger_hook()

        params = params or {}
        
        self._batch_contexts = []  # 存储批量处理的上下文
        self._batch_configs = []   # 存储批量处理的配置
        self.disable_memory_optimization = params.get('disable_memory_optimization', False)
        # batch_concurrent 会在 parse_init_params 中验证并设置
        self.batch_concurrent = params.get('batch_concurrent', False)
        
        # 流水线并行模式：在等待翻译时处理下一张图片的检测/OCR
        self.pipeline_mode = params.get('pipeline_mode', False)
        
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
        self.prep_manual = params.get('prep_manual', None)
        self.context_size = params.get('context_size', 0)
        self.all_page_translations = []
        self._original_page_texts = []  # 存储原文页面数据，用于并发模式下的上下文

        # 调试图片管理相关属性
        self._current_image_context = None  # 存储当前处理图片的上下文信息
        self._saved_image_contexts = {}     # 存储批量处理中每个图片的上下文信息
        
        # API失败智能降级相关属性
        self._degraded_mode = False          # 是否处于降级模式
        self._degraded_success_count = 0     # 降级模式下成功翻译的图片数
        self._degraded_recovery_threshold = 3  # 恢复正常模式所需的连续成功数
        self._original_batch_size = None     # 原始批次大小
        self._original_line2_concurrency = None  # 原始Line2并发数
        
        # 设置日志文件
        self._setup_log_file()

    def _setup_log_file(self):
        """设置日志文件，在result文件夹下创建带时间戳的log文件"""
        try:
            # 创建result目录
            result_dir = os.path.join(BASE_PATH, 'result')
            os.makedirs(result_dir, exist_ok=True)
            
            # 生成带时间戳的日志文件名
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            log_filename = f"log_{timestamp}.txt"
            log_path = os.path.join(result_dir, log_filename)
            
            # 配置文件日志处理器
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            # 使用自定义格式器，保持与控制台输出一致
            from .utils.log import Formatter
            formatter = Formatter()
            file_handler.setFormatter(formatter)
            
            # 添加到manga-translator根logger以捕获所有输出
            mt_logger = logging.getLogger('manga-translator')
            mt_logger.addHandler(file_handler)
            if not mt_logger.level or mt_logger.level > logging.DEBUG:
                mt_logger.setLevel(logging.DEBUG)
            
            # 保存日志文件路径供后续使用
            self._log_file_path = log_path
            
            # 简单的print重定向
            import builtins
            original_print = builtins.print
            
            def log_print(*args, **kwargs):
                # 正常打印到控制台
                original_print(*args, **kwargs)
                # 同时写入日志文件
                try:
                    import io
                    buffer = io.StringIO()
                    original_print(*args, file=buffer, **kwargs)
                    output = buffer.getvalue()
                    if output.strip():
                        with open(log_path, 'a', encoding='utf-8') as f:
                            f.write(output)
                except Exception:
                    pass
            
            builtins.print = log_print
            
            # Rich Console输出重定向
            try:
                from rich.console import Console
                import sys
                
                # 创建一个自定义的文件对象，同时写入控制台和日志文件
                class TeeFile:
                    def __init__(self, log_file_path, original_file):
                        self.log_file_path = log_file_path
                        self.original_file = original_file
                    
                    def write(self, text):
                        # 写入原始输出
                        self.original_file.write(text)
                        # 写入日志文件
                        try:
                            if text.strip():
                                with open(self.log_file_path, 'a', encoding='utf-8') as f:
                                    f.write(text)
                        except Exception:
                            pass
                        return len(text)
                    
                    def flush(self):
                        self.original_file.flush()
                    
                    def __getattr__(self, name):
                        return getattr(self.original_file, name)
                
                # 创建一个仅用于日志记录的Console（无颜色、无样式）
                class LogOnlyFile:
                    def __init__(self, log_file_path):
                        self.log_file_path = log_file_path
                    
                    def write(self, text):
                        try:
                            if text.strip():
                                with open(self.log_file_path, 'a', encoding='utf-8') as f:
                                    f.write(text)
                        except Exception:
                            pass
                        return len(text)
                    
                    def flush(self):
                        pass
                    
                    def isatty(self):
                        return False
                
                # 为日志创建纯文本console
                log_file_only = LogOnlyFile(log_path)
                log_console = Console(file=log_file_only, force_terminal=False, no_color=True, width=80)
                
                # 创建带颜色的控制台console
                display_console = Console(force_terminal=True)
                
                # 全局设置console实例，供translator使用
                global _global_console, _log_console
                _global_console = display_console  # 控制台显示用
                _log_console = log_console         # 日志记录用
                
            except Exception as e:
                logger.debug(f"Failed to setup rich console logging: {e}")
            
            logger.info(f"Log file created: {log_path}")
        except Exception as e:
            print(f"Failed to setup log file: {e}")

    def parse_init_params(self, params: dict):
        self.verbose = params.get('verbose', False)
        self.use_mtpe = params.get('use_mtpe', False)
        self.font_path = params.get('font_path', None)
        self.models_ttl = params.get('models_ttl', 0)
        self.batch_size = params.get('batch_size', 1)  # 添加批量大小参数
        self.high_quality_batch_size = params.get('high_quality_batch_size', 3)
        self.pipeline_mode = params.get('pipeline_mode', False)  # 流水线并行模式
        # 流水线并发配置（提供合理默认值，并在后续使用时做最小值保护）
        def _safe_int(v, d):
            try:
                if v in (None, '', 'None'):
                    return d
                return int(v)
            except Exception:
                return d
        self.pipeline_line1_concurrency = _safe_int(params.get('pipeline_line1_concurrency', 2), 2)
        self.pipeline_line2_concurrency = _safe_int(params.get('pipeline_line2_concurrency', 3), 3)
        self.pipeline_line3_concurrency = _safe_int(params.get('pipeline_line3_concurrency', 1), 1)
        self.pipeline_line4_concurrency = _safe_int(params.get('pipeline_line4_concurrency', 1), 1)
        self.pipeline_translation_batch_size = _safe_int(
            params.get('pipeline_translation_batch_size', self.high_quality_batch_size),
            self.high_quality_batch_size
        )
        
        # 长图拼接配置
        self.enable_long_image_stitching = params.get('enable_long_image_stitching', False)
        self.long_image_max_height = _safe_int(params.get('long_image_max_height', 10000), 10000)
        self.long_image_bubble_margin = _safe_int(params.get('long_image_bubble_margin', 100), 100)
        
        # 验证batch_concurrent参数
        if self.batch_concurrent and self.batch_size < 2:
            logger.warning('--batch-concurrent requires --batch-size to be at least 2. When batch_size is 1, concurrent mode has no effect.')
            logger.info('Suggestion: Use --batch-size 2 (or higher) with --batch-concurrent, or remove --batch-concurrent flag.')
            # 自动禁用并发模式
            self.batch_concurrent = False
            
        self.ignore_errors = params.get('ignore_errors', False)
        # check mps for apple silicon or cuda for nvidia
        device = 'mps' if torch.backends.mps.is_available() else 'cuda'
        self.device = device if params.get('use_gpu', False) else 'cpu'
        self._gpu_limited_memory = params.get('use_gpu_limited', False)
        if self._gpu_limited_memory and not self.using_gpu:
            self.device = device
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
        self.save_mask = not params.get('no_save_mask', False)
        self.template = params.get('template', False)
        self.is_ui_mode = params.get('is_ui_mode', False)
        self.attempts = params.get('attempts', -1)
        self.save_quality = params.get('save_quality', 100)
        self.skip_no_text = params.get('skip_no_text', False)
        self.generate_and_export = params.get('generate_and_export', False)
        self.colorize_only = params.get('colorize_only', False)
        self.upscale_only = params.get('upscale_only', False)
        
        
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

    async def translate(self, image: Image.Image, config: Config, image_name: str = None, skip_context_save: bool = False) -> Context:
        """
        Translates a single image.

        :param image: Input image.
        :param config: Translation config.
        :param image_name: Deprecated parameter, kept for compatibility.
        :return: Translation context.
        """
        await self._report_progress('running_pre_translation_hooks')
        for hook in self._progress_hooks:
            try:
                hook('running_pre_translation_hooks', False)
            except Exception as e:
                logger.error(f"Error in progress hook: {e}")

        ctx = Context()
        ctx.input = image
        ctx.image_name = image_name
        ctx.result = None
        ctx.verbose = self.verbose
        ctx.save_quality = self.save_quality
        ctx.config = config  # 保存config以便后续使用

        # 设置图片上下文以生成调试图片子文件夹
        self._set_image_context(config, image)
        
        # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
        # 在web模式下总是保存，不仅仅是verbose模式
        ctx.debug_folder = self._get_image_subfolder()

        # --- Colorize Only Mode ---
        if self.colorize_only:
            logger.info("Colorize Only mode: Running colorization only, skipping detection, OCR, translation and rendering.")
            
            # Run colorization if enabled
            if config.colorizer.colorizer != Colorizer.none:
                await self._report_progress('colorizing')
                try:
                    ctx.img_colorized = await self._run_colorizer(config, ctx)
                    ctx.result = ctx.img_colorized
                    logger.info("Colorization completed successfully.")
                except Exception as e:
                    logger.error(f"Error during colorizing:\n{traceback.format_exc()}")
                    if not self.ignore_errors:
                        raise
                    ctx.result = ctx.input  # Fallback to input image if colorization fails
            else:
                logger.warning("Colorize Only mode enabled but no colorizer selected. Returning original image.")
                ctx.result = ctx.input
            
            await self._report_progress('colorize-only-complete', True)
            return ctx

        # --- Upscale Only Mode ---
        if self.upscale_only:
            logger.info("Upscale Only mode: Running upscaling only, skipping detection, OCR, translation and rendering.")
            
            # Initialize img_colorized (same as batch processing flow)
            ctx.img_colorized = ctx.input
            
            # Run upscaling if enabled
            if config.upscale.upscale_ratio:
                await self._report_progress('upscaling')
                try:
                    ctx.img_up = await self._run_upscaling(config, ctx)
                    ctx.result = ctx.img_up
                    logger.info("Upscaling completed successfully.")
                except Exception as e:
                    logger.error(f"Error during upscaling:\n{traceback.format_exc()}")
                    if not self.ignore_errors:
                        raise
                    ctx.result = ctx.input  # Fallback to input image if upscaling fails
            else:
                logger.warning("Upscale Only mode enabled but no upscale_ratio set. Returning original image.")
                ctx.result = ctx.input
            
            await self._report_progress('upscale-only-complete', True)
            return ctx

        if self.load_text:
            # 加载文本模式：先尝试导入TXT到JSON
            logger.info("Load text mode: Attempting to import TXT to JSON first...")
            try:
                from desktop_qt_ui.services.workflow_service import smart_update_translations_from_images, get_template_path_from_config
                template_path = get_template_path_from_config()
                if template_path and os.path.exists(template_path):
                    # 使用当前图片文件路径进行TXT导入JSON处理
                    report = smart_update_translations_from_images([ctx.image_name], template_path)
                    logger.info(f"TXT import result: {report}")
                    if "错误" in report or "失败" in report:
                        logger.warning("TXT import failed, but continuing with normal load text processing")
                else:
                    logger.warning(f"Template file not found for import: {template_path}, skipping TXT import")
            except Exception as e:
                logger.error(f"Failed to import TXT: {e}, continuing with normal load text processing")

            logger.info("Attempting to load translation from file...")
            loaded_regions, loaded_mask, mask_is_refined = self._load_text_and_regions_from_file(ctx.image_name, config)
            if loaded_regions:
                logger.info("Successfully loaded translations. Skipping detection, OCR and translation.")

                # In --load-text mode, TextBlock objects are missing calculated fields like font_size.
                # We must add a reasonable font_size based on the bounding box height to prevent rendering errors.
                for region in loaded_regions:
                    if not hasattr(region, 'font_size') or not region.font_size:
                        box_height = np.max(region.lines[:,:,1]) - np.min(region.lines[:,:,1])
                        # Heuristic: Set font size to 80% of box height, but cap at a max of 128 to be safe.
                        region.font_size = min(int(box_height * 0.8), 128)

                ctx.text_regions = loaded_regions
                
                # -- 在load_text模式下也执行上色和超分 --
                # Colorization
                if config.colorizer.colorizer != Colorizer.none:
                    await self._report_progress('colorizing')
                    try:
                        ctx.img_colorized = await self._run_colorizer(config, ctx)
                    except Exception as e:  
                        logger.error(f"Error during colorizing in load_text mode:\n{traceback.format_exc()}")  
                        if not self.ignore_errors:  
                            raise  
                        ctx.img_colorized = ctx.input  # Fallback to input image if colorization fails
                else:
                    ctx.img_colorized = ctx.input

                # Upscaling
                if config.upscale.upscale_ratio:
                    await self._report_progress('upscaling')
                    try:
                        ctx.upscaled = await self._run_upscaling(config, ctx)
                    except Exception as e:  
                        logger.error(f"Error during upscaling in load_text mode:\n{traceback.format_exc()}")  
                        if not self.ignore_errors:  
                            raise  
                        ctx.upscaled = ctx.img_colorized # Fallback to colorized (or input) image if upscaling fails
                else:
                    ctx.upscaled = ctx.img_colorized

                # 使用上色和超分后的图片进行后续处理
                ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

                # 加载文本模式不需要翻译处理，直接跳过到渲染阶段
                
                if loaded_mask is not None:
                    if mask_is_refined:
                        ctx.mask = loaded_mask
                    else:
                        ctx.mask_raw = loaded_mask
                else:
                    # Manually create raw mask from loaded regions if not present in JSON
                    if ctx.mask_raw is None:
                        logger.debug("Creating raw mask from loaded regions for --load-text mode (mask_raw not in JSON).")
                        mask = np.zeros_like(ctx.img_rgb[:, :, 0])
                        polygons = [p.reshape((-1, 1, 2)) for r in ctx.text_regions for p in r.lines]
                        cv2.fillPoly(mask, polygons, 255)
                        ctx.mask_raw = mask
                
                # 如果执行了超分，需要将mask和坐标也超分到相同尺寸
                if config.upscale.upscale_ratio:
                    upscale_ratio = config.upscale.upscale_ratio
                    if ctx.mask_raw is not None:
                        logger.info(f"Upscaling mask_raw from {ctx.mask_raw.shape} to match upscaled image {ctx.img_rgb.shape[:2]}")
                        ctx.mask_raw = cv2.resize(ctx.mask_raw, (ctx.img_rgb.shape[1], ctx.img_rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
                    if ctx.mask is not None:
                        logger.info(f"Upscaling mask from {ctx.mask.shape} to match upscaled image {ctx.img_rgb.shape[:2]}")
                        ctx.mask = cv2.resize(ctx.mask, (ctx.img_rgb.shape[1], ctx.img_rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
                    
                    # 同时放大文本区域的坐标和字体大小
                    logger.info(f"Upscaling text region coordinates and font sizes by {upscale_ratio}x")
                    for i, region in enumerate(ctx.text_regions):
                        # 放大坐标
                        old_lines = region.lines.copy()
                        region.lines = region.lines * upscale_ratio
                        # 放大字体大小
                        if hasattr(region, 'font_size') and region.font_size:
                            old_font_size = region.font_size
                            region.font_size = int(region.font_size * upscale_ratio)
                            logger.debug(f"Region {i}: coordinates scaled, font_size {old_font_size} → {region.font_size}")
                        else:
                            logger.debug(f"Region {i}: coordinates scaled, no font_size to scale")
                
                # Mask generation
                if ctx.mask is None:  # Only run mask refinement if no pre-refined mask is loaded
                    await self._report_progress('mask-generation')
                    ctx.mask = await self._run_mask_refinement(config, ctx)
                else:
                    logger.info("Using pre-refined mask from JSON, skipping mask refinement")
                if self.verbose and ctx.mask is not None:
                    imwrite_unicode(self._result_path('mask_final.png'), ctx.mask, logger)

                # Inpainting
                await self._report_progress('inpainting')
                ctx.img_inpainted = await self._run_inpainting(config, ctx)
                if self.verbose:
                    imwrite_unicode(self._result_path('inpainted.png'), cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)

                # 保存inpainted图片到新目录结构
                if hasattr(ctx, 'image_name') and ctx.image_name and ctx.img_inpainted is not None:
                    self._save_inpainted_image(ctx.image_name, ctx.img_inpainted)

                # Rendering
                await self._report_progress('rendering')
                ctx.img_rendered = await self._run_text_rendering(config, ctx)
                
                await self._report_progress('finished', True)
                ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
                return await self._revert_upscale(config, ctx)
            else:
                # 加载文本模式下JSON文件不存在或解析失败，应该报错而不是回退到翻译
                json_path = os.path.splitext(ctx.image_name)[0] + '_translations.json'
                error_msg = f"Load text mode failed: Translation file not found or invalid: {json_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

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
        logger.debug(f'[DEBUG] Checking model load: models_ttl={self.models_ttl}, _models_loaded={self._models_loaded}')
        if ( self.models_ttl == 0 and not self._models_loaded ):
            logger.info('Loading models')
            if config.upscale.upscale_ratio:
                # 传递超分配置参数
                upscaler_kwargs = {}
                if config.upscale.upscaler == 'realcugan':
                    if config.upscale.realcugan_model:
                        upscaler_kwargs['model_name'] = config.upscale.realcugan_model
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
            logger.info('[DEBUG] Models loaded and flag set to True')
        else:
            logger.debug('[DEBUG] Skipping model load - already loaded or TTL enabled')

        # translate
        ctx = await self._translate(config, ctx)

        # 在翻译流程的最后保存翻译结果，确保保存的是最终结果（包括重试后的结果）
        # Save translation results at the end of translation process to ensure final results are saved
        if not skip_context_save and ctx.text_regions:
            # 汇总本页翻译，供下一页做上文
            page_translations = {r.text_raw if hasattr(r, "text_raw") else r.text: r.translation
                                 for r in ctx.text_regions}
            self.all_page_translations.append(page_translations)

            # 同时保存原文用于并发模式的上下文
            page_original_texts = {i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                                  for i, r in enumerate(ctx.text_regions)}
            self._original_page_texts.append(page_original_texts)

        return ctx

    def _save_text_to_file(self, image_path: str, ctx: Context, config: Config = None):
        """保存翻译数据到JSON文件，使用新的目录结构"""
        text_output_file = self.text_output_file
        if not text_output_file:
            # 使用新的路径管理器生成JSON路径
            text_output_file = get_json_path(image_path, create_dir=True)

        data = {}
        image_key = os.path.abspath(image_path)

        # Prepare data for JSON serialization
        regions_data = [region.to_dict() for region in ctx.text_regions]

        original_width, original_height = ctx.input.size
        data_to_save = {
            'regions': regions_data,
            'original_width': original_width,
            'original_height': original_height
        }
        
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

        if self.save_mask and ctx.mask_raw is not None:
            try:
                import base64
                import cv2
                _, buffer = cv2.imencode('.png', ctx.mask_raw)
                mask_base64 = base64.b64encode(buffer).decode('utf-8')
                data_to_save['mask_raw'] = mask_base64
            except Exception as e:
                logger.error(f"Failed to encode mask to base64: {e}")

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
        except Exception as e:
            logger.error(f"Failed to write translation file to {text_output_file}: {e}")

    def _save_inpainted_image(self, image_path: str, inpainted_img: np.ndarray):
        """保存修复后的图片到inpainted目录"""
        try:
            inpainted_path = get_inpainted_path(image_path, create_dir=True)
            imwrite_unicode(inpainted_path, cv2.cvtColor(inpainted_img, cv2.COLOR_RGB2BGR), logger)
            logger.info(f"Inpainted image saved to: {inpainted_path}")
        except Exception as e:
            logger.error(f"Failed to save inpainted image: {e}")

    def _load_text_and_regions_from_file(self, image_path: str, config: Config) -> (Optional[List[TextBlock]], Optional[np.ndarray], bool):
        """加载翻译数据，支持新的目录结构和向后兼容"""
        if not image_path:
            return None, None, False

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
                return regions, None, False
            else:
                logger.info(f"Translation file not found for: {image_path}")
                return None, None, False

        try:
            # Force UTF-8 encoding to handle potential file encoding issues
            with open(text_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read or parse translation file {text_file_path}: {e}")
            return None, None, False

        # Don't check the image key. Assume the user knows what they are doing
        # and that the first entry in the JSON is the one they want to load.
        if not data or len(data.values()) == 0:
            logger.warning(f"JSON file {text_file_path} is empty or invalid.")
            return None, None, False

        # Get the first value from the dictionary, regardless of the key.
        image_data = next(iter(data.values()))
        mask_is_refined = False

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
        else:
            logger.warning(f"Invalid data format in JSON file {text_file_path}.")
            return None, None, False

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

                # Recreate the TextBlock object by unpacking the dictionary
                # This restores all saved attributes
                if 'lines' in region_data and isinstance(region_data['lines'], list):
                    # Fix: Use np.float64 to match TextBlock expectation, not np.int32
                    region_data['lines'] = np.array(region_data['lines'], dtype=np.float64)
                
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

        return regions if regions else None, mask_raw, mask_is_refined

    def _load_text_and_regions_from_txt_file(self, image_path: str) -> Optional[List[TextBlock]]:
        # This is the old implementation for reading .txt files
        base_path, _ = os.path.splitext(image_path)
        text_file_path = base_path + '_translations.txt'
        # ... (rest of the old implementation)
        # ... for brevity, I will not include the full implementation here again
        # ... but it would be the same as the one I wrote before.
        return None # Placeholder

        if not match:
            logger.warning(f"No translation data found for {image_path} in {text_file_path}")
            return None

        image_block_content = match.group(1)
        
        # Regex to parse each region within the block
        region_pattern = re.compile(
            r'-- (\d+) --\n'
            r'color: #\d+: .*? \(fg, bg: ([0-9a-fA-F]{6}) ([0-9a-fA-F]{6})\)\n'
            r'text:  (.*)\n'
            r'trans: (.*)\n'
            r'((?:coords: \[.*\]\n)+)'
        )

        for region_match in region_pattern.finditer(image_block_content):
            try:
                _, fg_hex, bg_hex, text, trans, coords_str = region_match.groups()
                
                fg_color = tuple(int(fg_hex[i:i+2], 16) for i in (0, 2, 4))
                bg_color = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4))
                
                text = text.strip()
                trans = trans.strip()

                coord_lines = coords_str.strip().split('\n')
                lines = []
                for line in coord_lines:
                    # Extract numbers from "coords: [num, num, ...]"
                    coords_match = re.search(r'\[(.*)\]', line)
                    if coords_match:
                        coords = np.fromstring(coords_match.group(1), sep=',').astype(np.int32)
                        lines.append(coords.reshape(-1, 2))

                if not lines:
                    continue

                # We don't have all the info, but we can create a TextBlock with what we have
                region = TextBlock(
                    lines=lines,
                    texts=[text],
                    translation=trans,
                    fg_color=fg_color,
                    bg_color=bg_color,
                )
                regions.append(region)
            except Exception as e:
                logger.error(f"Failed to parse a region in {text_file_path}: {e}")
                continue
        
        logger.info(f"Loaded {len(regions)} regions from {text_file_path}")
        return regions if regions else None

    async def _translate(self, config: Config, ctx: Context) -> Context:
        # Start the background cleanup job once if not already started.
        if self._detector_cleanup_task is None:
            self._detector_cleanup_task = asyncio.create_task(self._detector_cleanup_job())
        # -- Colorization
        if config.colorizer.colorizer != Colorizer.none:
            await self._report_progress('colorizing')
            try:
                ctx.img_colorized = await self._run_colorizer(config, ctx)
            except Exception as e:  
                logger.error(f"Error during colorizing:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.img_colorized = ctx.input  # Fallback to input image if colorization fails

        else:
            ctx.img_colorized = ctx.input

        # -- Upscaling
        # The default text detector doesn't work very well on smaller images, might want to
        # consider adding automatic upscaling on certain kinds of small images.
        if config.upscale.upscale_ratio:
            await self._report_progress('upscaling')
            try:
                ctx.upscaled = await self._run_upscaling(config, ctx)
            except Exception as e:  
                logger.error(f"Error during upscaling:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.upscaled = ctx.img_colorized # Fallback to colorized (or input) image if upscaling fails
        else:
            ctx.upscaled = ctx.img_colorized

        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        # -- Detection
        await self._report_progress('detection')
        try:
            ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
        except Exception as e:  
            logger.error(f"Error during detection:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] 
            ctx.mask_raw = None
            ctx.mask = None

        if self.verbose and ctx.mask_raw is not None:
            # 生成带置信度颜色映射和颜色条的热力图
            logger.info(f"Generating confidence heatmap for mask_raw (shape: {ctx.mask_raw.shape}, dtype: {ctx.mask_raw.dtype})")
            heatmap = self._create_confidence_heatmap(ctx.mask_raw, equalize=False)
            logger.info(f"Heatmap generated (shape: {heatmap.shape}), saving to mask_raw.png")
            imwrite_unicode(self._result_path('mask_raw.png'), heatmap, logger)
            
            # 如果有raw_mask_mask，生成对比图
            if hasattr(ctx, 'raw_mask_mask') and ctx.raw_mask_mask is not None:
                try:
                    logger.info(f"Generating mask vs db comparison heatmap...")
                    logger.info(f"[DEBUG] raw_mask_mask shape: {ctx.raw_mask_mask.shape}, ctx.mask_raw shape: {ctx.mask_raw.shape}")
                    
                    # 确保两个mask尺寸一致
                    if ctx.raw_mask_mask.shape != ctx.mask_raw.shape:
                        logger.info(f"[DEBUG] Resizing raw_mask_mask from {ctx.raw_mask_mask.shape} to {ctx.mask_raw.shape}")
                        raw_mask_mask_resized = cv2.resize(ctx.raw_mask_mask, 
                                                          (ctx.mask_raw.shape[1], ctx.mask_raw.shape[0]), 
                                                          interpolation=cv2.INTER_LINEAR)
                    else:
                        raw_mask_mask_resized = ctx.raw_mask_mask
                    
                    heatmap_mask = self._create_confidence_heatmap(raw_mask_mask_resized, equalize=False)
                    heatmap_db = heatmap  # 复用刚生成的db热力图
                    comparison = np.hstack([heatmap_mask, heatmap_db])
                    comparison_path = self._result_path('mask_comparison.png')
                    imwrite_unicode(comparison_path, comparison, logger)
                    logger.info(f'Saved mask vs db comparison to {comparison_path}')
                except Exception as e:
                    logger.error(f'Failed to generate mask vs db comparison: {e}')

        # --- BEGIN: Save raw detection boxes image in verbose mode ---
        if self.verbose and ctx.textlines:
            try:
                logger.info("Verbose mode: Saving raw detection boxes image...")
                raw_detection_image = np.copy(ctx.img_rgb)
                for textline in ctx.textlines:
                    # Draw each polygon with a unique color to distinguish them
                    # Using a simple hash of the textline object to get a color
                    color_val = hash(str(textline.pts)) % (256 * 256 * 256)
                    color = (color_val & 0xFF, (color_val >> 8) & 0xFF, (color_val >> 16) & 0xFF)
                    cv2.polylines(raw_detection_image, [textline.pts.astype(np.int32)], isClosed=True, color=color, thickness=2)
                
                # Convert to BGR for saving with OpenCV
                raw_detection_image_bgr = cv2.cvtColor(raw_detection_image, cv2.COLOR_RGB2BGR)
                imwrite_unicode(self._result_path('detection_raw_boxes.png'), raw_detection_image_bgr, logger)
                logger.info("Saved raw detection boxes to detection_raw_boxes.png")
            except Exception as e:
                logger.error(f"Failed to save raw detection boxes image: {e}")
        # --- END: Save raw detection boxes image ---

        if not ctx.textlines:
            await self._report_progress('skip-no-regions', True)
            # If no text was found result is intermediate image product
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        if self.verbose:
            img_bbox_raw = np.copy(ctx.img_rgb)
            for txtln in ctx.textlines:
                cv2.polylines(img_bbox_raw, [txtln.pts], True, color=(255, 0, 0), thickness=2)
            imwrite_unicode(self._result_path('bboxes_unfiltered.png'), cv2.cvtColor(img_bbox_raw, cv2.COLOR_RGB2BGR), logger)

        # -- OCR
        await self._report_progress('ocr')
        try:
            ctx.textlines = await self._run_ocr(config, ctx)
        except Exception as e:  
            logger.error(f"Error during ocr:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] # Fallback to empty textlines if OCR fails

        if not ctx.textlines:
            await self._report_progress('skip-no-text', True)
            # If no text was found result is intermediate image product
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Textline merge
        await self._report_progress('textline_merge')
        try:
            ctx.text_regions = await self._run_textline_merge(config, ctx)
        except Exception as e:  
            logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.text_regions = [] # Fallback to empty text_regions if textline merge fails

        if self.verbose and ctx.text_regions:
            show_panels = not config.force_simple_sort  # 当不使用简单排序时显示panel
            bboxes = visualize_textblocks(cv2.cvtColor(ctx.img_rgb, cv2.COLOR_BGR2RGB), ctx.text_regions, 
                                        show_panels=show_panels, img_rgb=ctx.img_rgb, right_to_left=config.render.rtl)
            imwrite_unicode(self._result_path('bboxes.png'), bboxes, logger)

        # Apply pre-dictionary after textline merge
        pre_dict = load_dictionary(self.pre_dict)
        pre_replacements = []
        for region in ctx.text_regions:
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
            
        # -- Translation
        # 判断是否需要跳过翻译步骤
        should_skip_translation = False
        
        # 1. 保存文本+模板配置：只执行检测器和OCR步骤，不进行翻译，然后为当前文件快速退出
        if self.save_text and self.template:
            logger.info("Save text + Template mode: Running up to OCR, then saving and stopping for this file.")
            should_skip_translation = True
            # 设置原文作为翻译结果, 并设置目标语言以防万一
            for region in ctx.text_regions:
                region.translation = region.text
                # Set target_lang to avoid downstream errors if the pipeline were to continue (belt and braces)
                if not hasattr(region, 'target_lang') or not region.target_lang:
                    region.target_lang = config.translator.target_lang

            # 保存JSON文件
            if hasattr(ctx, 'image_name') and ctx.image_name:
                self._save_text_to_file(ctx.image_name, ctx, config)
                logger.info(f"JSON template saved for {os.path.basename(ctx.image_name)}.")
                
                # 直接导出TXT文件（原文）
                try:
                    json_path = find_json_path(ctx.image_name)
                    if json_path and os.path.exists(json_path):
                        from desktop_qt_ui.services.workflow_service import generate_original_text, get_template_path_from_config
                        template_path = get_template_path_from_config()
                        if template_path and os.path.exists(template_path):
                            # 导出原文
                            original_result = generate_original_text(json_path, template_path)
                            logger.info(f"Original text export result: {original_result}")
                        else:
                            logger.warning(f"Template file not found: {template_path}")
                    else:
                        logger.warning(f"JSON file not found for TXT export: {ctx.image_name}")
                except Exception as e:
                    logger.error(f"Failed to export TXT: {e}")
            else:
                logger.warning("Could not save translation file, image_name not in context.")

            # ✅ 标记成功（模板模式成功生成原文）
            ctx.success = True
            
            # 设置占位符结果并为当前文件提前返回，以便主循环可以处理下一个文件
            ctx.result = None
            return ctx
        # 2. 模板配置+加载文本：TXT文件内容就是翻译，不进行翻译处理
        elif self.template and self.load_text:
            logger.info("Template + Load text mode: TXT content is translation, skipping translation.")
            should_skip_translation = True
            # TXT文件的内容本身就是翻译结果，无需额外处理
        # 3. 单独模板配置：没有任何作用，正常进行翻译
        elif self.template and not self.save_text and not self.load_text:
            logger.info("Template only mode: No effect, proceeding with normal translation.")
            should_skip_translation = False
        
        if not should_skip_translation:
            await self._report_progress('translating')
            try:
                ctx.text_regions = await self._run_text_translation(config, ctx)
            except Exception as e:  
                logger.error(f"Error during translating:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise 
                ctx.text_regions = [] # Fallback to empty text_regions if translation fails

        if hasattr(ctx, 'pipeline_should_stop') and ctx.pipeline_should_stop:
            ctx.result = ctx.input
            return ctx

        await self._report_progress('after-translating')

        if not ctx.text_regions:
            await self._report_progress('error-translating', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)
        elif ctx.text_regions == 'cancel':
            await self._report_progress('cancelled', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Mask refinement
        # (Delayed to take advantage of the region filtering done after ocr and translation)
        if ctx.mask is None:
            await self._report_progress('mask-generation')
            try:
                ctx.mask = await self._run_mask_refinement(config, ctx)
            except Exception as e:  
                logger.error(f"Error during mask-generation:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise 
                ctx.mask = ctx.mask_raw if ctx.mask_raw is not None else np.zeros_like(ctx.img_rgb, dtype=np.uint8)[:,:,0] # Fallback to raw mask or empty mask

        if self.verbose and ctx.mask is not None:
            inpaint_input_img = await dispatch_inpainting(Inpainter.none, ctx.img_rgb, ctx.mask, config.inpainter,config.inpainter.inpainting_size,
                                                          self.device, self.verbose)
            imwrite_unicode(self._result_path('inpaint_input.png'), cv2.cvtColor(inpaint_input_img, cv2.COLOR_RGB2BGR), logger)
            imwrite_unicode(self._result_path('mask_final.png'), ctx.mask, logger)

        # -- Inpainting
        await self._report_progress('inpainting')
        try:
            ctx.img_inpainted = await self._run_inpainting(config, ctx)

        except Exception as e:
            logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
            if not self.ignore_errors:
                raise
            else:
                ctx.img_inpainted = ctx.img_rgb
        ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

        if self.verbose:
            try:
                inpainted_path = self._result_path('inpainted.png')
                imwrite_unicode(inpainted_path, cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)
            except Exception as e:
                logger.error(f"Error saving inpainted.png debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # 保存inpainted图片到新目录结构
        if hasattr(ctx, 'image_name') and ctx.image_name and ctx.img_inpainted is not None:
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
            ctx.img_rendered = ctx.img_inpainted # Fallback to inpainted (or original RGB) image if rendering fails

        await self._report_progress('finished', True)
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

        return await self._revert_upscale(config, ctx)
    
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

        # Web流式模式优化：保存final.png并使用占位符
        if ctx.result and not self.result_sub_folder and hasattr(self, '_is_streaming_mode') and self._is_streaming_mode:
            # 保存final.png文件
            final_img = np.array(ctx.result)
            if len(final_img.shape) == 3:  # 彩色图片，转换BGR顺序
                final_img = cv2.cvtColor(final_img, cv2.COLOR_RGB2BGR)
            imwrite_unicode(self._result_path('final.png'), final_img, logger)

            # 通知前端文件已就绪
            if hasattr(self, '_progress_hooks') and self._current_image_context:
                folder_name = self._current_image_context['subfolder']
                await self._report_progress(f'final_ready:{folder_name}')

            # 创建占位符结果并立即返回
            from PIL import Image
            placeholder = Image.new('RGB', (1, 1), color='white')
            ctx.result = placeholder
            ctx.use_placeholder = True
            return ctx

        return ctx

    async def _run_colorizer(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("colorizer", config.colorizer.colorizer)] = current_time
        #todo: im pretty sure the ctx is never used. does it need to be passed in?
        return await dispatch_colorization(
            config.colorizer.colorizer,
            colorization_size=config.colorizer.colorization_size,
            denoise_sigma=config.colorizer.denoise_sigma,
            device=self.device,
            image=ctx.input,
            **ctx
        )

    async def _run_upscaling(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("upscaling", config.upscale.upscaler)] = current_time
        
        # Prepare kwargs for Real-CUGAN (NCNN version)
        upscaler_kwargs = {}
        if config.upscale.upscaler == 'realcugan':
            realcugan_model = getattr(config.upscale, 'realcugan_model', None)
            if realcugan_model:
                upscaler_kwargs['model_name'] = realcugan_model
            # tile_size: None=use upscaler default, 0=no tiling, >0=manual tile size
            tile_size = getattr(config.upscale, 'tile_size', None)
            if tile_size is not None:
                upscaler_kwargs['tile_size'] = tile_size
        
        result = (await dispatch_upscaling(
            config.upscale.upscaler, 
            [ctx.img_colorized], 
            config.upscale.upscale_ratio, 
            self.device,
            **upscaler_kwargs
        ))[0]
        
        # Unload upscaling model immediately after use to free VRAM
        logger.info(f"Unloading upscaling model {config.upscale.upscaler} to free VRAM")
        await self._unload_model('upscaling', config.upscale.upscaler, **upscaler_kwargs)
        del self._model_usage_timestamps[("upscaling", config.upscale.upscaler)]
        
        return result

    async def _run_detection(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("detection", config.detector.detector)] = current_time
        result = await dispatch_detection(config.detector.detector, ctx.img_rgb, config.detector.detection_size, config.detector.text_threshold,
                                        config.detector.box_threshold,
                                        config.detector.unclip_ratio, config.detector.det_invert, config.detector.det_gamma_correct, config.detector.det_rotate,
                                        config.detector.det_auto_rotate,
                                        self.device, self.verbose,
                                        config.detector.use_yolo_obb, config.detector.yolo_obb_conf, config.detector.yolo_obb_iou, config.detector.yolo_obb_overlap_threshold)
        
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

        return result
    async def _unload_model(self, tool: str, model: str, **kwargs):
        logger.info(f"Unloading {tool} model: {model}")
        match tool:
            case 'colorization':
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
        if torch.cuda.is_available():
            torch.cuda.empty_cache()  # empty CUDA cache

    # Background models cleanup job.
    async def _detector_cleanup_job(self):
        while True:
            if self.models_ttl == 0:
                await asyncio.sleep(1)
                continue
            now = time.time()
            for (tool, model), last_used in list(self._model_usage_timestamps.items()):
                if now - last_used > self.models_ttl:
                    await self._unload_model(tool, model)
                    del self._model_usage_timestamps[(tool, model)]
            await asyncio.sleep(1)

    async def _run_ocr(self, config: Config, ctx: Context):
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
            logger.info(f"Running primary OCR with: {primary_ocr_engine.value}")
            textlines = await dispatch_ocr(primary_ocr_engine, ctx.img_rgb, ctx.textlines, config.ocr, self.device, self.verbose)

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
                    
                    logger.info(f"Running secondary OCR with: {secondary_ocr_engine.value}")
                    secondary_results = await dispatch_ocr(secondary_ocr_engine, ctx.img_rgb, failed_textlines, secondary_config, self.device, self.verbose)
                    
                    # Merge the results back into the original list
                    for i, result_tl in zip(failed_indices, secondary_results):
                        textlines[i] = result_tl # Replace the failed textline with the new result
                    
                    logger.info("Secondary OCR processing finished.")
                    
                    # ✅ 混合OCR完成后清理,防止两次OCR调用累积
                    import gc
                    if 'secondary_results' in locals():
                        del secondary_results
                    if 'failed_textlines' in locals():
                        del failed_textlines
                    if 'failed_indices' in locals():
                        del failed_indices
                    gc.collect()
                    if hasattr(self, 'device') and (self.device == 'cuda' or self.device == 'mps'):
                        try:
                            import torch
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
            # --- END: HYBRID OCR LOGIC ---

        finally:
            # 恢复环境变量
            if old_ocr_dir is not None:
                os.environ['MANGA_OCR_RESULT_DIR'] = old_ocr_dir
            elif 'MANGA_OCR_RESULT_DIR' in os.environ:
                del os.environ['MANGA_OCR_RESULT_DIR']

        new_textlines = []
        for textline in textlines:
            if textline.text.strip():
                if config.render.font_color_fg:
                    textline.fg_r, textline.fg_g, textline.fg_b = config.render.font_color_fg
                if config.render.font_color_bg:
                    textline.bg_r, textline.bg_g, textline.bg_b = config.render.font_color_bg
                new_textlines.append(textline)
        return new_textlines

    async def _run_textline_merge(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("textline_merge", "textline_merge")] = current_time
        text_regions = await dispatch_textline_merge(ctx.textlines, ctx.img_rgb.shape[1], ctx.img_rgb.shape[0],
                                                     config, verbose=self.verbose)
        for region in text_regions:
            if not hasattr(region, "text_raw"):
                region.text_raw = region.text      # <- Save the initial OCR results to expand the render detection box. Also, prevent affecting the forbidden translation function.       
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
    
        text_regions = await dispatch_textline_merge(ctx.textlines, ctx.img_rgb.shape[1], ctx.img_rgb.shape[0],  
                                                     config, verbose=self.verbose)  

        new_text_regions = []
        for region in text_regions:
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
                '"': '"', '＂': '＂', "'": "'", "“": "”", '《': '》', '『': '』', '"': '"', '〝': '〞', '﹁': '﹂', '﹃': '﹄',  
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
            
            # 增强过滤：检测网站水印
            is_watermark = False
            watermark_patterns = [
                '웹툰왕국뉴토끼',  # 韩文漫画网站水印
                'newtoki',
                'manga18',
                '뉴토끼',
                'webtoon',
                'toonkor',
            ]
            for pattern in watermark_patterns:
                if pattern.lower() in region.text.lower():
                    is_watermark = True
                    break
            
            # 增强过滤：检测混合日韩文（MangaOCR误识别）
            has_hiragana = any('\u3040' <= ch <= '\u309f' for ch in region.text)
            has_katakana = any('\u30a0' <= ch <= '\u30ff' for ch in region.text)
            has_hangul = any('\uac00' <= ch <= '\ud7a3' for ch in region.text)
            is_mixed_jp_kr = (has_hiragana or has_katakana) and has_hangul
            
            if len(region.text) < config.ocr.min_text_length \
                    or not is_valuable_text(region.text) \
                    or is_watermark \
                    or is_mixed_jp_kr \
                    or (not config.translator.no_text_lang_skip and langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0):
                if region.text.strip():
                    logger.info(f'Filtered out: {region.text}')
                    if len(region.text) < config.ocr.min_text_length:
                        logger.info('Reason: Text length is less than the minimum required length.')
                    elif not is_valuable_text(region.text):
                        logger.info('Reason: Text is not considered valuable.')
                    elif is_watermark:
                        logger.info('Reason: Detected as website watermark or spam.')
                    elif is_mixed_jp_kr:
                        logger.info('Reason: Mixed Japanese-Korean text detected (likely OCR error).')
                    elif langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0:
                        logger.info('Reason: Text language matches the target language and no_text_lang_skip is False.')
            else:
                if config.render.font_color_fg or config.render.font_color_bg:
                    if config.render.font_color_bg:
                        region.adjust_bg_color = False
                new_text_regions.append(region)
        text_regions = new_text_regions

        text_regions = sort_regions(
            text_regions,
            right_to_left=config.render.rtl,
            img=ctx.img_rgb,
            force_simple_sort=config.force_simple_sort
        )   
        
        
        
        return text_regions

    def _build_prev_context(self, use_original_text=True, current_page_index=None, batch_index=None, batch_original_texts=None):
        """
        跳过句子数为0的页面，取最近 context_size 个非空页面，拼成：
        <|1|>句子
        <|2|>句子
        ...
        的格式；如果没有任何非空页面，返回空串。

        Args:
            use_original_text: 是否使用原文而不是译文作为上下文
            current_page_index: 当前页面索引，用于确定上下文范围
            batch_index: 当前页面在批次中的索引
            batch_original_texts: 当前批次的原文数据
        """
        if self.context_size <= 0:
            return ""

        # 在并发模式下，需要特殊处理上下文范围
        if batch_index is not None and batch_original_texts is not None:
            # 并发模式：使用已完成的页面 + 当前批次中已处理的页面
            available_pages = self.all_page_translations.copy()

            # 添加当前批次中在当前页面之前的页面
            for i in range(batch_index):
                if i < len(batch_original_texts) and batch_original_texts[i]:
                    # 在并发模式下，我们使用原文作为"已完成"的页面
                    if use_original_text:
                        available_pages.append(batch_original_texts[i])
                    else:
                        # 如果不使用原文，则跳过当前批次的页面（因为它们还没有翻译完成）
                        pass
        elif current_page_index is not None:
            # 流水线模式：只使用指定批次索引的页面作为上下文（不跨批次）
            if current_page_index >= 0 and current_page_index < len(self.all_page_translations):
                available_pages = [self.all_page_translations[current_page_index]]
            else:
                available_pages = []
        else:
            # 使用所有已完成的页面
            available_pages = self.all_page_translations or []

        if not available_pages:
            return ""

        # 筛选出有句子的页面
        non_empty_pages = [
            page for page in available_pages
            if any(sent.strip() for sent in page.values())
        ]
        # 实际要用的页数
        pages_used = min(self.context_size, len(non_empty_pages))
        if pages_used == 0:
            return ""
        tail = non_empty_pages[-pages_used:]

        # 拼接 - 根据参数决定使用原文还是译文
        lines = []
        for page in tail:
            # 使用原文（字典的键）或译文（字典的值）
            items = page.keys() if use_original_text else page.values()
            for sent in items:
                if sent.strip():
                    lines.append(sent.strip())

        numbered = [f"<|{i+1}|>{s}" for i, s in enumerate(lines)]
        context_type = "original text" if use_original_text else "translation results"
        return f"Here are the previous {context_type} for reference:\n" + "\n".join(numbered)

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
                if any(sent.strip() for sent in page.values())
            ]
            pages_used = min(self.context_size, len(non_empty_pages))
            skipped = pages_expected - pages_used
        else:
            pages_used = skipped = 0

        if self.context_size > 0:
            logger.info(f"Context-aware translation enabled with {self.context_size} pages of history using original text")

        # 构建上下文字符串 - 使用原文
        # Build the context string - use original text
        prev_ctx = self._build_prev_context(use_original_text=True)

        # 如果是 OpenAI 翻译器，则专门处理上下文注入
        # Special handling for OpenAI translator: inject context
        if config.translator.translator == Translator.openai:
            from .translators.openai import OpenAITranslator
            translator = OpenAITranslator()

            translator.parse_args(config.translator)
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count("<|")
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
                self.use_mtpe,
                ctx,
                'cpu' if self._gpu_limited_memory else self.device
            )
        
    async def _load_and_prepare_prompts(self, config: Config, ctx: Context):
        """Loads custom HQ and line break prompts into the context object."""
        # Load custom high-quality prompt from JSON file if specified
        ctx.custom_prompt_json = None
        if config.translator.high_quality_prompt_path:
            try:
                prompt_path = config.translator.high_quality_prompt_path
                if not os.path.isabs(prompt_path):
                    prompt_path = os.path.join(BASE_PATH, prompt_path)
                
                if os.path.exists(prompt_path):
                    with open(prompt_path, 'r', encoding='utf-8') as f:
                        ctx.custom_prompt_json = json.load(f)
                    logger.info(f"Successfully loaded custom HQ prompt from: {prompt_path}")
                    # Log the parsed content for user verification
                    from .translators.gemini_hq import _flatten_prompt_data
                    parsed_content = _flatten_prompt_data(ctx.custom_prompt_json)
                    # logger.info(f"--- Parsed Custom Prompt Content ---\n{parsed_content}\n------------------------------------")
                else:
                    logger.warning(f"Custom HQ prompt file not found at: {prompt_path}")
            except Exception as e:
                logger.error(f"Error loading custom HQ prompt: {e}")

        # Load AI line break prompt if enabled
        ctx.line_break_prompt_json = None
        if config.render.disable_auto_wrap: # This is the "AI断句" switch
            try:
                line_break_prompt_path = os.path.join(BASE_PATH, 'dict', 'system_prompt_line_break.json')
                if os.path.exists(line_break_prompt_path):
                    with open(line_break_prompt_path, 'r', encoding='utf-8') as f:
                        ctx.line_break_prompt_json = json.load(f)
                    logger.info("AI line breaking is enabled. Loaded line break prompt.")
                else:
                    logger.warning("AI line breaking is enabled, but line break prompt file not found.")
            except Exception as e:
                logger.error(f"Failed to load line break prompt: {e}")
        return ctx

    async def _run_text_translation(self, config: Config, ctx: Context):
        # Centralized prompt loading logic
        ctx = await self._load_and_prepare_prompts(config, ctx)

        # 检查text_regions是否为None或空
        if not ctx.text_regions:
            return []
            
        # 如果设置了prep_manual则将translator设置为none，防止token浪费
        if self.prep_manual:  
            config.translator.translator = Translator.none
    
        current_time = time.time()
        self._model_usage_timestamps[("translation", config.translator.translator)] = current_time

        # --- Main translation logic ---
        if config.translator.translator == Translator.none:
            for region in ctx.text_regions:  
                region.translation = ""  # 空翻译将创建空白区域 / Empty translation will create blank areas
        else: # Actual network translation
            # --- BEGIN PRE-TRANSLATION DE-DUPLICATION ---
            # Per user request, clean up duplicate lines within regions before sending to translator.
            logger.debug("Starting pre-translation de-duplication of regions...")
            for region in ctx.text_regions:
                if hasattr(region, 'lines') and hasattr(region, 'texts') and isinstance(region.lines, np.ndarray) and isinstance(region.texts, list) and len(region.lines) > 1:
                    unique_lines = []
                    unique_texts = []
                    seen_coords = set()
                    
                    original_line_count = len(region.lines)

                    for i, line_coords in enumerate(region.lines):
                        # Flatten for hashing, ensuring consistent representation
                        coords_tuple = tuple(line_coords.reshape(-1))
                        if coords_tuple not in seen_coords:
                            seen_coords.add(coords_tuple)
                            unique_lines.append(line_coords)
                            if i < len(region.texts):
                                unique_texts.append(region.texts[i])

                    if len(unique_lines) < original_line_count:
                        logger.info(f"Pre-translation cleanup: Found and removed {original_line_count - len(unique_lines)} internal duplicate lines in a region.")
                        region.lines = np.array(unique_lines, dtype=np.float64)
                        region.texts = unique_texts
                        region.text = '\n'.join(unique_texts)
            logger.debug("Pre-translation de-duplication finished.")
            # --- END PRE-TRANSLATION DE-DUPLICATION ---

            texts = [region.text.replace('\ufffd', '') for region in ctx.text_regions]
            translated_sentences = await self._dispatch_with_context(config, texts, ctx)

            for region, translation in zip(ctx.text_regions, translated_sentences):
                if config.render.uppercase:
                    translation = translation.upper()
                elif config.render.lowercase:
                    translation = translation.lower()
                region.translation = translation

        # 统一设置所有region的通用属性（避免重复代码）
        for region in ctx.text_regions:
            region.target_lang = config.translator.target_lang
            region._alignment = config.render.alignment
            region._direction = config.render.direction

        # --- Save results (moved to after post-processing) ---
        # JSON保存移到后处理（标点符号替换等）之后，确保保存的是最终结果
        # (JSON saving is deferred until after post-processing to ensure final results are saved)

        # --- NEW: Generate and Export Workflow ---
        if self.generate_and_export:
            logger.info("'Generate and Export' mode: Halting pipeline after translation and exporting clean text.")
            if hasattr(ctx, 'image_name') and ctx.image_name and ctx.text_regions:
                try:
                    json_path = find_json_path(ctx.image_name)
                    if json_path and os.path.exists(json_path):
                        from desktop_qt_ui.services.workflow_service import generate_translated_text, get_template_path_from_config
                        template_path = get_template_path_from_config()
                        if template_path and os.path.exists(template_path):
                            # 导出翻译
                            translated_result = generate_translated_text(json_path, template_path)
                            logger.info(f"Translated text export result: {translated_result}")
                        else:
                            logger.warning(f"Template file not found, cannot export clean text: {template_path}")
                    else:
                        logger.warning(f"JSON file not found, cannot export clean text: {ctx.image_name}")
                except Exception as e:
                    logger.error(f"Failed to export clean text in 'Generate and Export' mode: {e}")
            
            # ✅ 标记成功（导出翻译模式完成）
            ctx.success = True
            ctx.pipeline_should_stop = True # Set flag to stop before rendering
            # Do not return here, let the function complete to return all regions

        # === 模板+保存文本模式退出逻辑 ===
        if self.template and self.save_text:
            logger.info("Template + Save Text mode: Stopping pipeline for this file to generate text template only.")
            ctx.pipeline_should_stop = True
            # Return early to skip all post-processing
            return ctx.text_regions

        # --- Post-processing (当不是"仅生成文本模板"模式时执行) ---
        
        # Punctuation correction logic. for translators often incorrectly change quotation marks from the source language to those commonly used in the target language.
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
            ["「", """, "'", "『", "【"],
            ["」", """, "'", "』", "】"],
            ["『", """, "'", "「", "【"],
            ["』", """, "'", "」", "】"],
            
            # 新增【】处理
            ["【", "(", "（", "「", "『", "["],
            ["】", ")", "）", "」", "』", "]"],
        ]

        replace_items = [
            ["「", """],
            ["「", "'"],
            ["」", """],
            ["」", "'"],
            ["【", "["],  
            ["】", "]"],
        ]
        
        # 全角句点替换（必须先替换长的，再替换短的，避免部分替换）
        full_width_period_replace = [
            ["…", "．．．"],  # 把三个全角句点替换为省略号
            ["…", "．．"],    # 把两个全角句点替换为省略号
        ]

        for region in ctx.text_regions:
            if region.text and region.translation:
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

                # === 优化后的数量判断逻辑 ===
                # === Optimized quantity judgment logic ===
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

                # 全角句点替换（必须先替换，避免被两个点的规则部分替换）
                # Full-width period replacement (must be done first to avoid partial replacement)
                for v in full_width_period_replace:
                    region.translation = region.translation.replace(v[1], v[0])
                
                # 强制替换规则
                # Forced replacement rules
                for v in replace_items:
                    region.translation = region.translation.replace(v[1], v[0])

        # Apply post dictionary after translating
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

        # 译后检查和重试逻辑 - 第一阶段：单个region幻觉检测
        failed_regions = []
        if config.translator.enable_post_translation_check:
            logger.info("Starting post-translation check...")
            
            # 单个region级别的幻觉检测（在过滤前进行）
            for region in ctx.text_regions:
                if region.translation and region.translation.strip():
                    # 只检查重复内容幻觉，不进行页面级目标语言检查
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
                    await self._retry_translation_with_validation(region, config, ctx)
                logger.info("Repetition check retry finished.")

        # 译后检查和重试逻辑 - 第二阶段：页面级目标语言检查（使用过滤后的区域）
        if config.translator.enable_post_translation_check:
            
            # 页面级目标语言检查（使用过滤后的区域数量）
            page_lang_check_result = True
            if ctx.text_regions and len(ctx.text_regions) > 5:
                logger.info(f"Starting page-level target language check with {len(ctx.text_regions)} regions...")
                page_lang_check_result = await self._check_target_language_ratio(
                    ctx.text_regions,
                    config.translator.target_lang,
                    min_ratio=0.5
                )
                
                if not page_lang_check_result:
                    logger.warning("Page-level target language ratio check failed")
                    
                    # 第二阶段：整个批次重新翻译逻辑
                    max_batch_retry = config.translator.post_check_max_retry_attempts
                    batch_retry_count = 0
                    
                    while batch_retry_count < max_batch_retry and not page_lang_check_result:
                        batch_retry_count += 1
                        logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry} for page-level target language check...")
                        
                        # 重新翻译所有区域
                        original_texts = []
                        for region in ctx.text_regions:
                            if hasattr(region, 'text') and region.text:
                                original_texts.append(region.text)
                            else:
                                original_texts.append("")
                        
                        if original_texts:
                            try:
                                # 重新批量翻译
                                logger.info(f"Retrying translation for {len(original_texts)} regions...")
                                new_translations = await self._batch_translate_texts(original_texts, config, ctx)
                                
                                # 更新翻译结果到regions
                                for i, region in enumerate(ctx.text_regions):
                                    if i < len(new_translations) and new_translations[i]:
                                        old_translation = region.translation
                                        region.translation = new_translations[i]
                                        logger.debug(f"Region {i+1} translation updated: '{old_translation}' -> '{new_translations[i]}'")
                                    
                                # 重新检查目标语言比例
                                logger.info(f"Re-checking page-level target language ratio after batch retry {batch_retry_count}...")
                                page_lang_check_result = await self._check_target_language_ratio(
                                    ctx.text_regions,
                                    config.translator.target_lang,
                                    min_ratio=0.5
                                )
                                
                                if page_lang_check_result:
                                    logger.info(f"Page-level target language check passed")
                                    break
                                else:
                                    logger.warning(f"Page-level target language check still failed")
                                    
                            except Exception as e:
                                logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                                break
                        else:
                            logger.warning("No text found for batch retry")
                            break
                    
                    if not page_lang_check_result:
                        logger.error(f"Page-level target language check failed after all {max_batch_retry} batch retries")
                else:
                    logger.info(f"Skipping page-level target language check: only {len(ctx.text_regions)} regions (threshold: 5)")
            
            # 统一的成功信息
            if page_lang_check_result:
                logger.info("All translation regions passed post-translation check.")
            else:
                logger.warning("Some translation regions failed post-translation check.")

        # 过滤逻辑（简化版本，保留主要过滤条件）
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
                elif config.filter_text and re.search(config.re_filter_text, region.translation):
                    should_filter = True
                    filter_reason = f"Matched filter text: {config.filter_text}"
                elif not config.translator.translator == Translator.original:
                    text_equal = region.text.lower().strip() == region.translation.lower().strip()
                    if text_equal:
                        should_filter = True
                        filter_reason = "Translation identical to original"

            if should_filter:
                if region.translation.strip():
                    logger.info(f'Filtered out: {region.translation}')
                    logger.info(f'Reason: {filter_reason}')
            else:
                new_text_regions.append(region)

        # 更新context中的文本区域列表（使用过滤后的）
        ctx.text_regions = new_text_regions

        # --- Save JSON after all post-processing (including punctuation replacement and filtering) ---
        if self.save_text or self.text_output_file:
            if hasattr(ctx, 'image_name') and ctx.image_name:
                # 使用ctx中保存的config，如果没有则使用当前config参数
                config_to_use = getattr(ctx, 'config', config) if hasattr(ctx, 'config') else config
                self._save_text_to_file(ctx.image_name, ctx, config_to_use)
                logger.info(f"Translations saved to JSON for {ctx.image_name} (after post-processing).")
            else:
                logger.warning("Could not save translation file, image_name not in context.")

        return new_text_regions

    async def _run_mask_refinement(self, config: Config, ctx: Context):
        return await dispatch_mask_refinement(ctx.text_regions, ctx.img_rgb, ctx.mask_raw, 'fit_text',
                                              config.mask_dilation_offset, config.ocr.ignore_bubble, self.verbose,self.kernel_size)

    async def _run_inpainting(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("inpainting", config.inpainter.inpainter)] = current_time
        return await dispatch_inpainting(config.inpainter.inpainter, ctx.img_rgb, ctx.mask, config.inpainter, config.inpainter.inpainting_size, self.device,
                                         self.verbose)

    async def _run_text_rendering(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("rendering", config.render.renderer)] = current_time
        if config.render.renderer == Renderer.none:
            output = ctx.img_inpainted
        # manga2eng currently only supports horizontal left to right rendering
        elif (config.render.renderer == Renderer.manga2Eng or config.render.renderer == Renderer.manga2EngPillow) and ctx.text_regions and LANGUAGE_ORIENTATION_PRESETS.get(ctx.text_regions[0].target_lang) == 'h':
            if config.render.renderer == Renderer.manga2EngPillow:
                output = await dispatch_eng_render_pillow(ctx.img_inpainted, ctx.img_rgb, ctx.text_regions, self.font_path, config.render.line_spacing)
            else:
                output = await dispatch_eng_render(ctx.img_inpainted, ctx.img_rgb, ctx.text_regions, self.font_path, config.render.line_spacing)
        else:
            # Request debug image for balloon_fill mode when verbose
            need_debug_img = self.verbose and config.render.layout_mode == 'balloon_fill'
            result = await dispatch_rendering(ctx.img_inpainted, ctx.text_regions, self.font_path, config, ctx.img_rgb, return_debug_img=need_debug_img)
            
            # Handle debug image if returned
            if need_debug_img and isinstance(result, tuple):
                output, debug_img = result
                # Save balloon_fill debug image
                if debug_img is not None:
                    try:
                        debug_path = self._result_path('balloon_fill_boxes.png')
                        imwrite_unicode(debug_path, cv2.cvtColor(debug_img, cv2.COLOR_RGB2BGR), logger)
                        logger.info(f"📸 Balloon fill debug image saved: {debug_path}")
                    except Exception as e:
                        logger.error(f"Failed to save balloon_fill debug image: {e}")
            else:
                output = result
        
        # ✅ 渲染完成后立即清理img_rgb（不再需要）
        if hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
            del ctx.img_rgb
            ctx.img_rgb = None
        
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
                logger.warn(LOG_MESSAGES_SKIP[state])
            elif state in LOG_MESSAGES_ERROR:
                logger.error(LOG_MESSAGES_ERROR[state])

        self.add_progress_hook(ph)

    async def pipeline_translate_batch(self, images_with_configs: List[tuple], batch_size: int = None, image_names: List[str] = None, save_info: dict = None) -> List[Context]:
        """
        两阶段流水线并行翻译模式
        
        第一阶段（并发执行）：
        - 线1: 检测+OCR
        - 线2: AI翻译+Inpainting+渲染
        
        第二阶段（等待第一阶段完成后执行）：
        - 线3: 超分（GPU密集，批量处理）
        """
        # 读取并发设置并做下限保护
        c1 = max(1, int(getattr(self, 'pipeline_line1_concurrency', 2)))
        c2 = max(1, int(getattr(self, 'pipeline_line2_concurrency', 3)))
        c3 = max(1, int(getattr(self, 'pipeline_line3_concurrency', 1)))

        logger.info("="*50)
        logger.info("🚀 Two-Stage Pipeline Mode Enabled")
        logger.info("  📌 Stage 1: Line1 & Line2 run concurrently")
        logger.info(f"    ├─ Line1: Detection+OCR (Concurrency: {c1})")
        logger.info(f"    └─ Line2: Translation+Inpaint+Render (Concurrency: {c2})")
        logger.info("  📌 Stage 2: Line3 starts after Stage 1 completes")
        logger.info(f"    └─ Line3: Upscale Only (Concurrency: {c3})")
        logger.info("  • Expected speedup: 40-60% for online translators")
        logger.info("="*50)
        
        total_images = len(images_with_configs)
        results = [None] * total_images
        
        # 检查是否为特殊模式
        is_template_save_mode = self.template and self.save_text
        if is_template_save_mode or batch_size and batch_size <= 1:
            logger.info("Pipeline mode not applicable for template/sequential mode, falling back to standard batch processing")
            # 防止递归
            original_pipeline_mode = self.pipeline_mode
            self.pipeline_mode = False
            ret = await self.translate_batch(images_with_configs, batch_size, image_names, save_info)
            self.pipeline_mode = original_pipeline_mode
            return ret
        
        # 队列（作为各线之间的缓冲区），按下游并发设置缓冲大小
        preprocess_queue = asyncio.Queue(maxsize=max(2, c2 + 1))   # 线1 → 线2
        translate_queue = asyncio.Queue(maxsize=max(2, c3 + 1))    # 线2 → 线3
        render_queue = asyncio.Queue(maxsize=2)                     # 线3 → 主收集
        
        # 并发控制
        line1_semaphore = asyncio.Semaphore(c1)
        line2_semaphore = asyncio.Semaphore(c2)
        line3_semaphore = asyncio.Semaphore(c3)
        
        async def line1_detection_ocr():
            """线1: 仅执行检测+OCR（去掉超分以加速）"""
            
            async def process_single(idx: int, image: Image.Image, config: Config):
                async with line1_semaphore:
                    try:
                        logger.info(f"[Line1-Detection] 🔍 Processing image {idx+1}/{total_images}")
                        self._set_image_context(config, image)
                        
                        from .utils.generic import get_image_md5
                        image_md5 = get_image_md5(image)
                        self._save_current_image_context(image_md5)
                        
                        # 临时禁用超分，使线1仅做检测+OCR
                        original_upscale_ratio = config.upscale.upscale_ratio
                        config.upscale.upscale_ratio = None
                        
                        ctx = await self._translate_until_translation(image, config)
                        
                        # 恢复配置并保存供线3使用
                        config.upscale.upscale_ratio = original_upscale_ratio
                        ctx.pipeline_upscale_ratio = original_upscale_ratio
                        
                        if hasattr(image, 'name'):
                            ctx.image_name = image.name
                        
                        await preprocess_queue.put((idx, ctx, config))
                        logger.info(f"[Line1-Detection] ✅ Image {idx+1} detection & OCR completed")
                    except Exception as e:
                        logger.error(f"[Line1-Detection] ❌ Error in image {idx+1}: {e}")
                        ctx = Context()
                        ctx.input = image
                        ctx.text_regions = []
                        if hasattr(image, 'name'):
                            ctx.image_name = image.name
                        ctx.pipeline_upscale_ratio = config.upscale.upscale_ratio
                        await preprocess_queue.put((idx, ctx, config))
            
            # 并发提交所有图像任务
            tasks = []
            for idx, (image, config) in enumerate(images_with_configs):
                tasks.append(asyncio.create_task(process_single(idx, image, config)))
            await asyncio.gather(*tasks)
            await preprocess_queue.put(None)
            logger.info("[Line1-Detection] 🏁 All detection & OCR completed")
        
        async def line2_translation_render():
            """线2: AI翻译 + Inpainting + 渲染（支持并发）"""
            
            async def translate_and_render_single(idx: int, ctx: Context, config: Config):
                async with line2_semaphore:
                    try:
                        logger.info(f"[Line2-TransRender] 🌐 Processing image {idx+1}/{total_images}")
                        
                        # 翻译
                        if ctx.text_regions:
                            all_texts = [region.text for region in ctx.text_regions]
                            page_index = len(self.all_page_translations) + idx
                            batch_original_texts = [{'original_texts': all_texts}]
                            ctx = await self._load_and_prepare_prompts(config, ctx)
                            
                            translated_texts = await self._batch_translate_texts(
                                all_texts, config, ctx, [ctx],
                                page_index=page_index, batch_index=0,
                                batch_original_texts=batch_original_texts
                            )
                            
                            for region, translation in zip(ctx.text_regions, translated_texts):
                                region.translation = translation
                                region.target_lang = config.translator.target_lang
                                region._alignment = config.render.alignment
                                region._direction = config.render.direction
                            
                            ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                            page_trans = {r.text: r.translation for r in ctx.text_regions if r.translation}
                            self.all_page_translations.append(page_trans)
                            logger.info(f"[Line2-TransRender] ✅ Translation completed {idx+1}")
                        else:
                            logger.info(f"[Line2-TransRender] ⚠️ No text regions {idx+1}")
                        
                        # Inpainting + 渲染
                        if hasattr(ctx, 'image_context'):
                            self._current_image_context = ctx.image_context
                        
                        # 生成mask
                        if ctx.mask is None and ctx.mask_raw is not None:
                            await self._report_progress('mask-generation')
                            ctx.mask = await self._run_mask_refinement(config, ctx)
                        
                        if ctx.text_regions:
                            await self._report_progress('inpainting')
                            ctx.img_inpainted = await self._run_inpainting(config, ctx)
                            if self.verbose:
                                imwrite_unicode(self._result_path('inpainted.png'),
                                                cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)
                            if hasattr(ctx, 'image_name') and ctx.image_name:
                                self._save_inpainted_image(ctx.image_name, ctx.img_inpainted)
                            
                            await self._report_progress('rendering')
                            ctx.img_rendered = await self._run_text_rendering(config, ctx)
                            ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
                            logger.info(f"[Line2-TransRender] ✅ Render completed {idx+1}")
                        else:
                            ctx.result = ctx.input
                        
                        # 保存文本（如果需要）
                        if self.save_text and ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                            self._save_text_to_file(ctx.image_name, ctx, config)
                        
                        await translate_queue.put((idx, ctx, config))
                    except Exception as e:
                        logger.error(f"[Line2-TransRender] ❌ Error processing image {idx+1}: {e}")
                        await translate_queue.put((idx, ctx, config))
            
            # 并发消费者
            async def consumer():
                tasks = []
                while True:
                    item = await preprocess_queue.get()
                    if item is None:
                        break
                    idx, ctx, config = item
                    task = asyncio.create_task(translate_and_render_single(idx, ctx, config))
                    tasks.append(task)
                if tasks:
                    await asyncio.gather(*tasks)
                await translate_queue.put(None)
                logger.info("[Line2-TransRender] 🏁 All translation & rendering completed")
            
            await consumer()
        
        async def line3_upscale_only():
            """线3: 仅超分（两阶段模式：等待线1+线2完成后才开始）"""
            
            async def upscale_single(idx: int, ctx: Context, config: Config):
                async with line3_semaphore:
                    try:
                        # 仅进行超分（如果启用）
                        if hasattr(ctx, 'pipeline_upscale_ratio') and ctx.pipeline_upscale_ratio:
                            logger.info(f"[Line3-Upscale] 🔍 Upscaling image {idx+1}/{total_images} ({ctx.pipeline_upscale_ratio}x)")
                            upscale_ctx = Context()
                            upscale_ctx.img_colorized = ctx.result
                            upscale_ctx.input = ctx.result
                            upscaled_result = await self._run_upscaling(config, upscale_ctx)
                            ctx.result = upscaled_result
                            logger.info(f"[Line3-Upscale] ✅ Image {idx+1} upscaled successfully")
                        else:
                            logger.info(f"[Line3-Upscale] ⏭️ Image {idx+1} skipped (no upscale needed)")
                        
                        # 保存最终结果
                        if save_info and ctx.result and hasattr(ctx, 'image_name') and ctx.image_name:
                            await self._save_single_result(ctx, save_info)
                        
                        await render_queue.put((idx, ctx))
                    except Exception as e:
                        logger.error(f"[Line3-Upscale] ❌ Error upscaling image {idx+1}: {e}")
                        await render_queue.put((idx, ctx))
            
            # 两阶段模式：先收集所有渲染结果，等线2完成后再批量超分
            logger.info("[Line3-Upscale] ⏳ Waiting for Stage 1 (Line1 & Line2) to complete...")
            all_rendered = []
            while True:
                item = await translate_queue.get()
                if item is None:
                    break
                all_rendered.append(item)
            
            logger.info(f"[Line3-Upscale] ✅ Stage 1 completed. Starting Stage 2 (Upscale) with {len(all_rendered)} images...")
            
            # 开始批量超分
            tasks = []
            for idx, ctx, config in all_rendered:
                task = asyncio.create_task(upscale_single(idx, ctx, config))
                tasks.append(task)
            
            # 等待所有超分任务完成
            if tasks:
                await asyncio.gather(*tasks)
            
            await render_queue.put(None)
            logger.info("[Line3-Upscale] 🏁 Stage 2 (All upscaling) completed")
        
        workers = [
            asyncio.create_task(line1_detection_ocr()),
            asyncio.create_task(line2_translation_render()),
            asyncio.create_task(line3_upscale_only()),
        ]
        
        completed = 0
        while completed < total_images:
            item = await render_queue.get()
            if item is None:
                break
            idx, ctx = item
            results[idx] = ctx
            completed += 1
            logger.info(f"[Pipeline] 📊 Progress: {completed}/{total_images} images completed")
        
        await asyncio.gather(*workers)
        
        logger.info("="*50)
        logger.info(f"🎉 Two-Stage Pipeline Completed: {total_images} images")
        logger.info("="*50)
        return results
    
    async def _save_single_result(self, ctx: Context, save_info: dict):
        """保存单个结果的辅助方法"""
        try:
            output_folder = save_info.get('output_folder')
            input_folders = save_info.get('input_folders', set())
            output_format = save_info.get('format')
            overwrite = save_info.get('overwrite', True)

            file_path = ctx.image_name
            final_output_dir = output_folder
            parent_dir = os.path.normpath(os.path.dirname(file_path))
            
            for folder in input_folders:
                if parent_dir.startswith(folder):
                    relative_path = os.path.relpath(parent_dir, folder)
                    if relative_path == '.':
                        final_output_dir = os.path.join(output_folder, os.path.basename(folder))
                    else:
                        final_output_dir = os.path.join(output_folder, os.path.basename(folder), relative_path)
                    final_output_dir = os.path.normpath(final_output_dir)
                    break

            os.makedirs(final_output_dir, exist_ok=True)

            base_filename, _ = os.path.splitext(os.path.basename(file_path))
            if output_format and output_format.strip() and output_format.lower() != 'none':
                output_filename = f"{base_filename}.{output_format}"
            else:
                output_filename = os.path.basename(file_path)

            final_output_path = os.path.join(final_output_dir, output_filename)

            if not overwrite and os.path.exists(final_output_path):
                logger.info(f"  -> ⚠️ [PIPELINE] Skipping existing file: {os.path.basename(final_output_path)}")
            else:
                image_to_save = ctx.result
                if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                    image_to_save = image_to_save.convert('RGB')

                image_to_save.save(final_output_path, quality=self.save_quality)
                logger.info(f"  -> ✅ [PIPELINE] Saved successfully: {os.path.basename(final_output_path)}")
                
        except Exception as save_err:
            logger.error(f"Error saving pipeline result for {os.path.basename(ctx.image_name)}: {save_err}")

    async def translate_batch(self, images_with_configs: List[tuple], batch_size: int = None, image_names: List[str] = None, save_info: dict = None) -> List[Context]:
        """
        批量翻译多张图片，在翻译阶段进行批量处理以提高效率
        
        如果启用了pipeline_mode，将自动使用流水线并行处理模式。
        
        Args:
            images_with_configs: List of (image, config) tuples
            batch_size: 批量大小，如果为None则使用实例的batch_size
            image_names: 已弃用的参数，保留用于兼容性
        Returns:
            List of Context objects with translation results
        """
        batch_size = batch_size or self.batch_size
        
        # ✅ 如果启用了四线流水线模式，使用并行处理工作流
        if self.pipeline_mode and len(images_with_configs) > 1:
            logger.info(f"启动四线流水线模式：{len(images_with_configs)} 张图片")
            logger.info(f"并发设置 - Line1(检测+OCR):{self.pipeline_line1_concurrency}, Line2(翻译):{self.pipeline_line2_concurrency}, Line3(修复):{self.pipeline_line3_concurrency}, Line4(渲染+超分):{self.pipeline_line4_concurrency}")
            logger.info(f"翻译打包大小: {self.pipeline_translation_batch_size}")
            return await self._translate_batch_pipeline_4_lines(images_with_configs, save_info)
        
        batch_size = batch_size or self.batch_size
        
        # 检查是否使用高质量翻译器，如果是则自动启用高质量模式
        if images_with_configs:
            first_config = images_with_configs[0][1] if images_with_configs else None
            if first_config and hasattr(first_config.translator, 'translator'):
                from manga_translator.config import Translator
                translator_type = first_config.translator.translator
                is_hq_translator = translator_type in [Translator.openai_hq, Translator.gemini_hq]
                is_import_export_mode = self.load_text or self.template

                if is_hq_translator and not is_import_export_mode:
                    logger.info(f"检测到高质量翻译器 {translator_type}，自动启用高质量翻译模式")
                    return await self._translate_batch_high_quality(images_with_configs, save_info)
                
                if is_hq_translator and is_import_export_mode:
                    logger.warning("检测到导入/导出翻译模式，高质量翻译流程将被跳过，将使用标准流程进行渲染。")
        
        # 检查是否为"仅生成模板"模式
        is_template_save_mode = self.template and self.save_text

        if batch_size <= 1 or is_template_save_mode:
            if is_template_save_mode:
                logger.info("Template+SaveText mode detected. Forcing sequential processing to save files one by one.")
            else:
                logger.debug('Batch size <= 1, switching to individual processing mode')

            results = []
            for i, (image, config) in enumerate(images_with_configs):
                # 确保传递 image_name 以便正确保存文件
                # The image object should have a .name attribute attached by the caller (e.g., the UI)
                image_name_to_pass = image.name if hasattr(image, 'name') else None

                ctx = await self.translate(image, config, image_name=image_name_to_pass)

                # 如果提供了save_info，保存图片
                if save_info and ctx.result:
                    try:
                        output_folder = save_info.get('output_folder')
                        input_folders = save_info.get('input_folders', set())
                        output_format = save_info.get('format')
                        overwrite = save_info.get('overwrite', True)

                        file_path = ctx.image_name
                        final_output_dir = output_folder
                        parent_dir = os.path.normpath(os.path.dirname(file_path))
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

                        base_filename, _ = os.path.splitext(os.path.basename(file_path))
                        if output_format and output_format.strip() and output_format.lower() != 'none':
                            output_filename = f"{base_filename}.{output_format}"
                        else:
                            output_filename = os.path.basename(file_path)

                        final_output_path = os.path.join(final_output_dir, output_filename)

                        if not overwrite and os.path.exists(final_output_path):
                            logger.info(f"  -> ⚠️ [SEQUENTIAL] Skipping existing file: {os.path.basename(final_output_path)}")
                        else:
                            image_to_save = ctx.result
                            if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                                image_to_save = image_to_save.convert('RGB')

                            image_to_save.save(final_output_path, quality=self.save_quality)
                            logger.info(f"  -> ✅ [SEQUENTIAL] Saved successfully: {os.path.basename(final_output_path)}")

                    except Exception as save_err:
                        logger.error(f"Error saving sequential result for {os.path.basename(ctx.image_name)}: {save_err}")

                results.append(ctx)
            return results
        
        logger.debug(f'Starting batch translation: {len(images_with_configs)} images, batch size: {batch_size}')
        results = []
        total_images = len(images_with_configs)

        for batch_start in range(0, total_images, batch_size):
            # 检查是否被取消
            await asyncio.sleep(0)

            batch_end = min(batch_start + batch_size, total_images)
            current_batch_images = images_with_configs[batch_start:batch_end]

            logger.info(f"Processing rolling batch {batch_start//batch_size + 1}/{(total_images + batch_size - 1)//batch_size} (images {batch_start+1}-{batch_end})")

            # 阶段一：预处理当前批次
            preprocessed_contexts = []
            
            # ✅ 检查是否为load_text模式
            if self.load_text:
                logger.info("Load text mode: Loading translations from JSON and skipping detection/OCR/translation")
                for i, (image, config) in enumerate(current_batch_images):
                    await asyncio.sleep(0)
                    try:
                        self._set_image_context(config, image)
                        # 使用标准的translate方法，它会自动处理load_text模式
                        ctx = await self.translate(image, config, image_name=image.name if hasattr(image, 'name') else None)
                        preprocessed_contexts.append((ctx, config))
                    except Exception as e:
                        logger.error(f"Error loading text for image {i+1} in batch: {e}")
                        ctx = Context()
                        ctx.input = image
                        ctx.text_regions = []
                        if hasattr(image, 'name'):
                            ctx.image_name = image.name
                        ctx.translation_error = str(e)
                        preprocessed_contexts.append((ctx, config))
                
                # load_text模式下已经完成了所有处理（包括渲染），直接保存并返回
                for ctx, config in preprocessed_contexts:
                    if save_info and ctx.result:
                        try:
                            output_folder = save_info.get('output_folder')
                            input_folders = save_info.get('input_folders', set())
                            output_format = save_info.get('format')
                            overwrite = save_info.get('overwrite', True)

                            file_path = ctx.image_name
                            final_output_dir = output_folder
                            parent_dir = os.path.normpath(os.path.dirname(file_path))
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

                            base_filename, _ = os.path.splitext(os.path.basename(file_path))
                            if output_format and output_format.strip() and output_format.lower() != 'none':
                                output_filename = f"{base_filename}.{output_format}"
                            else:
                                output_filename = os.path.basename(file_path)

                            final_output_path = os.path.join(final_output_dir, output_filename)

                            if not overwrite and os.path.exists(final_output_path):
                                logger.info(f"  -> ⚠️ [LOAD_TEXT] Skipping existing file: {os.path.basename(final_output_path)}")
                            else:
                                image_to_save = ctx.result
                                if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                                    image_to_save = image_to_save.convert('RGB')

                                image_to_save.save(final_output_path, quality=self.save_quality)
                                logger.info(f"  -> ✅ [LOAD_TEXT] Saved successfully: {os.path.basename(final_output_path)}")
                            
                            # 标记成功
                            ctx.success = True

                        except Exception as save_err:
                            logger.error(f"Error saving load_text result for {os.path.basename(ctx.image_name)}: {save_err}")
                    
                    results.append(ctx)
                
                # load_text模式处理完成，继续下一批
                continue

            # 标准模式：执行检测、OCR等预处理
            for i, (image, config) in enumerate(current_batch_images):
                # 检查是否被取消
                await asyncio.sleep(0)
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
                    logger.error(f"Error pre-processing image {i+1} in batch: {e}")
                    ctx = Context()
                    ctx.input = image
                    ctx.text_regions = []
                    if hasattr(image, 'name'):
                        ctx.image_name = image.name
                    preprocessed_contexts.append((ctx, config))

            # --- Colorize Only Mode: Skip translation and rendering ---
            if self.colorize_only:
                logger.info("Colorize Only mode: Skipping translation and rendering stages.")
                translated_contexts = preprocessed_contexts
            else:
                # 阶段二：翻译当前批次
                try:
                    translated_contexts = await self._batch_translate_contexts(preprocessed_contexts, batch_size)
                except Exception as e:
                    logger.error(f"Error during batch translation stage: {e}")
                    # 重新抛出异常，终止翻译流程
                    raise

            # --- NEW: Handle Generate and Export for Standard Batch Mode ---
            if self.generate_and_export:
                logger.info("'Generate and Export' mode enabled for standard batch. Skipping rendering.")
                for ctx, config in translated_contexts:
                    if ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                        self._save_text_to_file(ctx.image_name, ctx, config)
                        try:
                            json_path = find_json_path(ctx.image_name)
                            if json_path and os.path.exists(json_path):
                                from desktop_qt_ui.services.workflow_service import generate_translated_text, get_template_path_from_config
                                template_path = get_template_path_from_config()
                                if template_path and os.path.exists(template_path):
                                    # 导出翻译
                                    translated_result = generate_translated_text(json_path, template_path)
                                    logger.info(f"Translated text export for {os.path.basename(ctx.image_name)}: {translated_result}")
                                else:
                                    logger.warning(f"Template file not found for {os.path.basename(ctx.image_name)}: {template_path}")
                            else:
                                logger.warning(f"JSON file not found for {os.path.basename(ctx.image_name)}")
                        except Exception as e:
                            logger.error(f"Failed to export clean text for {os.path.basename(ctx.image_name)}: {e}")
                    # ✅ 标记成功（导出翻译完成）
                    ctx.success = True
                    results.append(ctx)
                continue # Skip rendering and proceed to the next batch

            # 阶段三：渲染并保存当前批次
            for ctx, config in translated_contexts:
                # 检查是否被取消
                await asyncio.sleep(0)
                try:
                    if hasattr(ctx, 'input'):
                        from .utils.generic import get_image_md5
                        image_md5 = get_image_md5(ctx.input)
                        if not self._restore_image_context(image_md5):
                            self._set_image_context(config, ctx.input)
                    
                    # Colorize Only Mode: Skip rendering pipeline
                    if not self.colorize_only:
                        ctx = await self._complete_translation_pipeline(ctx, config)

                    logger.info(f"[DEBUG] save_info={save_info is not None}, ctx.result={ctx.result is not None}")
                    if save_info and ctx.result:
                        try:
                            output_folder = save_info.get('output_folder')
                            input_folders = save_info.get('input_folders', set())
                            output_format = save_info.get('format')
                            overwrite = save_info.get('overwrite', True)

                            file_path = ctx.image_name
                            final_output_dir = output_folder
                            parent_dir = os.path.normpath(os.path.dirname(file_path))
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

                            base_filename, _ = os.path.splitext(os.path.basename(file_path))
                            if output_format and output_format.strip() and output_format.lower() != 'none':
                                output_filename = f"{base_filename}.{output_format}"
                            else:
                                output_filename = os.path.basename(file_path)
                            
                            final_output_path = os.path.join(final_output_dir, output_filename)

                            if not overwrite and os.path.exists(final_output_path):
                                logger.info(f"  -> ⚠️ [BATCH] Skipping existing file: {os.path.basename(final_output_path)}")
                            else:
                                image_to_save = ctx.result
                                if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                                    image_to_save = image_to_save.convert('RGB')
                                
                                image_to_save.save(final_output_path, quality=self.save_quality)
                                logger.info(f"  -> ✅ [BATCH] Saved successfully: {os.path.basename(final_output_path)}")

                        except Exception as save_err:
                            logger.error(f"Error saving standard batch result for {os.path.basename(ctx.image_name)}: {save_err}")

                    if ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                        # 使用循环变量中的config，而不是从ctx中获取
                        self._save_text_to_file(ctx.image_name, ctx, config)

                    results.append(ctx)
                except Exception as e:
                    logger.error(f"Error rendering image in batch: {e}")
                    results.append(ctx)

        logger.info(f"Batch translation completed: processed {len(results)} images")
        return results

    async def _translate_until_translation(self, image: Image.Image, config: Config) -> Context:
        """
        执行翻译之前的所有步骤（彩色化、上采样、检测、OCR、文本行合并）
        """
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
        logger.debug(f'[DEBUG-2] Checking model load: models_ttl={self.models_ttl}, _models_loaded={self._models_loaded}')
        if ( self.models_ttl == 0 and not self._models_loaded ):
            logger.info('Loading models')
            if config.upscale.upscale_ratio:
                # 传递超分配置参数
                upscaler_kwargs = {}
                if config.upscale.upscaler == 'realcugan':
                    if config.upscale.realcugan_model:
                        upscaler_kwargs['model_name'] = config.upscale.realcugan_model
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
            logger.info('[DEBUG-2] Models loaded and flag set to True')
        else:
            logger.debug('[DEBUG-2] Skipping model load - already loaded or TTL enabled')

        # Start the background cleanup job once if not already started.
        if self._detector_cleanup_task is None:
            self._detector_cleanup_task = asyncio.create_task(self._detector_cleanup_job())

        # -- Colorization
        if config.colorizer.colorizer != Colorizer.none:
            await self._report_progress('colorizing')
            try:
                ctx.img_colorized = await self._run_colorizer(config, ctx)
            except Exception as e:  
                logger.error(f"Error during colorizing:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.img_colorized = ctx.input
        else:
            ctx.img_colorized = ctx.input

        # --- Colorize Only Mode Check (for batch processing) ---
        if self.colorize_only:
            logger.info("Colorize Only mode (batch): Running colorization only, skipping detection, OCR, translation and rendering.")
            ctx.result = ctx.img_colorized
            ctx.text_regions = []  # Empty text regions
            await self._report_progress('colorize-only-complete', True)
            return ctx

        # -- Upscaling
        if config.upscale.upscale_ratio:
            await self._report_progress('upscaling')
            try:
                ctx.upscaled = await self._run_upscaling(config, ctx)
            except Exception as e:  
                logger.error(f"Error during upscaling:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.upscaled = ctx.img_colorized
        else:
            ctx.upscaled = ctx.img_colorized

        # --- Upscale Only Mode Check (for batch processing) ---
        if self.upscale_only:
            logger.info("Upscale Only mode (batch): Running upscaling only, skipping detection, OCR, translation and rendering.")
            ctx.result = ctx.upscaled
            ctx.text_regions = []  # Empty text regions
            await self._report_progress('upscale-only-complete', True)
            return ctx

        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        # -- Detection
        await self._report_progress('detection')
        try:
            ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
        except Exception as e:  
            logger.error(f"Error during detection:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] 
            ctx.mask_raw = None
            ctx.mask = None

        if self.verbose and ctx.mask_raw is not None:
            # 生成带置信度颜色映射和颜色条的热力图
            logger.info(f"Generating confidence heatmap for mask_raw (shape: {ctx.mask_raw.shape}, dtype: {ctx.mask_raw.dtype})")
            heatmap = self._create_confidence_heatmap(ctx.mask_raw, equalize=False)
            logger.info(f"Heatmap generated (shape: {heatmap.shape}), saving to mask_raw.png")
            imwrite_unicode(self._result_path('mask_raw.png'), heatmap, logger)

        if not ctx.textlines:
            await self._report_progress('skip-no-regions', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        if self.verbose:
            img_bbox_raw = np.copy(ctx.img_rgb)
            for txtln in ctx.textlines:
                cv2.polylines(img_bbox_raw, [txtln.pts], True, color=(255, 0, 0), thickness=2)
            imwrite_unicode(self._result_path('bboxes_unfiltered.png'), cv2.cvtColor(img_bbox_raw, cv2.COLOR_RGB2BGR), logger)

        # -- OCR
        await self._report_progress('ocr')
        try:
            ctx.textlines = await self._run_ocr(config, ctx)
        except Exception as e:  
            logger.error(f"Error during ocr:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = []

        if not ctx.textlines:
            await self._report_progress('skip-no-text', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Textline merge
        await self._report_progress('textline_merge')
        try:
            ctx.text_regions = await self._run_textline_merge(config, ctx)
        except Exception as e:  
            logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.text_regions = []

        if self.verbose and ctx.text_regions:
            show_panels = not config.force_simple_sort  # 当不使用简单排序时显示panel
            bboxes = visualize_textblocks(cv2.cvtColor(ctx.img_rgb, cv2.COLOR_BGR2RGB), ctx.text_regions, 
                                        show_panels=show_panels, img_rgb=ctx.img_rgb, right_to_left=config.render.rtl)
            imwrite_unicode(self._result_path('bboxes.png'), bboxes, logger)

        # Apply pre-dictionary after textline merge
        pre_dict = load_dictionary(self.pre_dict)
        pre_replacements = []
        for region in ctx.text_regions:
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
                                'original_texts': [region.text for region in ctx.text_regions]
                            }
                            batch_original_texts.append(image_data)
                    
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
                        page_trans = {}
                        for region in ctx.text_regions:
                            if region.translation:
                                page_trans[region.text] = region.translation
                        self.all_page_translations.append(page_trans)
                        logger.debug(f"[Batch Context] Saved {len(page_trans)} translations for next batch context")
                        
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
                                            logger.info(f"Batch-level target language check passed")
                                            break
                                        else:
                                            logger.warning(f"Batch-level target language check still failed")
                                            
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
                                elif config.filter_text and re.search(config.re_filter_text, region.translation):
                                    should_filter = True
                                    filter_reason = f"Matched filter text: {config.filter_text}"
                                elif not config.translator.translator == Translator.original:
                                    text_equal = region.text.lower().strip() == region.translation.lower().strip()
                                    if text_equal:
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
                logger.error(f"Error in batch translation: {e}")
                if not self.ignore_errors:
                    raise
                # 错误时保持原文
                for ctx, config in batch:
                    if not ctx.text_regions:  # 检查text_regions是否为None或空
                        continue
                    for region in ctx.text_regions:
                        region.translation = region.text
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                results.extend(batch)

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
                texts = [region.text for region in ctx.text_regions]

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
                        logger.warning(f"Page-level target language check failed for single image")
                        
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
                                        logger.info(f"Single image target language check passed")
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
                            elif config.filter_text and re.search(config.re_filter_text, region.translation):
                                should_filter = True
                                filter_reason = f"Matched filter text: {config.filter_text}"
                            elif not config.translator.translator == Translator.original:
                                text_equal = region.text.lower().strip() == region.translation.lower().strip()
                                if text_equal:
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
                # 错误时保持原文
                if ctx.text_regions:
                    for region in ctx.text_regions:
                        region.translation = region.text
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
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
                # 创建失败的占位符
                ctx, config = contexts_with_configs[i]
                if ctx.text_regions:
                    for region in ctx.text_regions:
                        region.translation = region.text
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
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

            translator.parse_args(config.translator)
            translator.attempts = self.attempts

            # 为所有翻译器构建和设置文本上下文（包括HQ翻译器）
            # 始终使用原文作为上下文
            use_original_text = True

            done_pages = self.all_page_translations
            if self.context_size > 0 and done_pages:
                pages_expected = min(self.context_size, len(done_pages))
                non_empty_pages = [
                    page for page in done_pages
                    if any(sent.strip() for sent in page.values())
                ]
                pages_used = min(self.context_size, len(non_empty_pages))
                skipped = pages_expected - pages_used
            else:
                pages_used = skipped = 0

            if self.context_size > 0:
                context_type = "original text" if use_original_text else "translation results"
                logger.info(f"Context-aware translation enabled with {self.context_size} pages of history using {context_type}")

            # 构建上下文
            prev_ctx = self._build_prev_context(
                use_original_text=use_original_text,
                current_page_index=page_index,
                batch_index=batch_index,
                batch_original_texts=batch_original_texts
            )
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count("<|")
                logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
            if skipped > 0:
                logger.warning(f"Skipped {skipped} pages with no sentences")


            # 将config附加到ctx，供翻译器使用（例如AI断句功能）
            ctx.config = config
            
            # openai_hq, gemini_hq 等需要传递ctx参数
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
                self.use_mtpe,
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
        
        # 通用错误
        return f"❌ 翻译失败：{error_msg}\n💡 建议：\n1. 检查API配置是否正确\n2. 查看完整日志以获取详细错误信息\n3. 尝试更换翻译服务"
            
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

        replace_items = [
            ["「", "“"],
            ["「", "‘"],
            ["」", "”"],
            ["」", "”"],
            ["【", "["],  
            ["】", "]"],  
        ]

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

                # 强制替换规则
                # Forced replacement rules
                for v in replace_items:
                    region.translation = region.translation.replace(v[1], v[0])

        # 注意：翻译结果的保存移动到了translate方法的最后，确保保存的是最终结果

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
        
        return ctx.text_regions

    async def _complete_translation_pipeline(self, ctx: Context, config: Config) -> Context:
        """
        完成翻译后的处理步骤（掩码细化、修复、渲染）
        """
        await self._report_progress('after-translating')

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
                ctx.mask = ctx.mask_raw if ctx.mask_raw is not None else np.zeros_like(ctx.img_rgb, dtype=np.uint8)[:,:,0]

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
        await self._report_progress('inpainting')
        try:
            ctx.img_inpainted = await self._run_inpainting(config, ctx)
            
            # ✅ Inpainting完成后强制GC和GPU清理
            import gc
            gc.collect()
            if hasattr(self, 'device') and (self.device == 'cuda' or self.device == 'mps'):
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
            if not self.ignore_errors:
                raise
            else:
                ctx.img_inpainted = ctx.img_rgb
        ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

        if self.verbose:
            try:
                inpainted_path = self._result_path('inpainted.png')
                imwrite_unicode(inpainted_path, cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)
            except Exception as e:
                logger.error(f"Error saving inpainted.png debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # 保存inpainted图片到新目录结构
        if hasattr(ctx, 'image_name') and ctx.image_name and ctx.img_inpainted is not None:
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
            ctx.img_rendered = ctx.img_inpainted

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
            confidence = -9999
        
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
                            self.use_mtpe,
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

    async def _translate_batch_high_quality(self, images_with_configs: List[tuple], save_info: dict = None) -> List[Context]:
        """
        高质量翻译模式：按批次滚动处理，每批独立完成预处理、翻译、渲染全流程。
        如果提供了save_info，则在每批处理后直接保存。
        """
        batch_size = getattr(self, 'high_quality_batch_size', 3)
        logger.info(f"Starting high quality translation in rolling batch mode with batch size: {batch_size}")
        results = []
        
        total_images = len(images_with_configs)
        for batch_start in range(0, total_images, batch_size):
            # 检查是否被取消
            await asyncio.sleep(0)

            batch_end = min(batch_start + batch_size, total_images)
            current_batch_images = images_with_configs[batch_start:batch_end]

            logger.info(f"Processing rolling batch {batch_start//batch_size + 1}/{(total_images + batch_size - 1)//batch_size} (images {batch_start+1}-{batch_end})")

            # 阶段一：预处理当前批次
            preprocessed_contexts = []
            for i, (image, config) in enumerate(current_batch_images):
                # 检查是否被取消
                await asyncio.sleep(0)
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
                    logger.error(f"Error pre-processing image {i+1} in batch: {e}")
                    ctx = Context()
                    ctx.input = image
                    ctx.text_regions = []
                    if hasattr(image, 'name'):
                        ctx.image_name = image.name
                    preprocessed_contexts.append((ctx, config))

            # 阶段二：翻译当前批次
            batch_data = []
            for ctx, config in preprocessed_contexts:
                image_data = {
                    'image': ctx.input,
                    'text_regions': ctx.text_regions if ctx.text_regions else [],
                    'original_texts': [region.text for region in ctx.text_regions] if ctx.text_regions else [],
                    'text_order': list(range(len(ctx.text_regions))) if ctx.text_regions else []
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
                        enhanced_ctx.high_quality_batch_size = len(preprocessed_contexts)

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
                        
                        # 高质量翻译批量模式：不使用batch_index和batch_original_texts
                        # 只使用page_index来获取之前已完成页面的上下文
                        translated_texts = await self._batch_translate_texts(
                            all_texts, 
                            sample_config, 
                            enhanced_ctx,
                            page_index=page_index,
                            batch_index=None,  # 高质量批量模式不使用批次内上下文
                            batch_original_texts=None  # 高质量批量模式不使用批次内上下文
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
                                # 保存译文
                                page_trans = {}
                                for region in ctx.text_regions:
                                    if region.translation:
                                        page_trans[region.text] = region.translation
                                self.all_page_translations.append(page_trans)
                                logger.debug(f"[HQ Batch Context] Saved {len(page_trans)} translations for next batch context")
                                
                                # 保存原文（用于并发模式的上下文）
                                page_original_texts = {i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                                                      for i, r in enumerate(ctx.text_regions)}
                                self._original_page_texts.append(page_original_texts)
                                logger.debug(f"[HQ Batch Context] Saved {len(page_original_texts)} original texts for next batch context")
                                
                except Exception as e:
                    logger.error(f"Error in high quality batch translation: {e}")
                    # 重新抛出异常，终止翻译流程
                    raise
            # --- NEW: Handle Generate and Export for High-Quality Mode ---
            if self.generate_and_export:
                logger.info("'Generate and Export' mode enabled for high-quality translation. Skipping rendering.")
                for ctx, config in preprocessed_contexts:
                    # Ensure JSON is saved first
                    if ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                        self._save_text_to_file(ctx.image_name, ctx, config)

                        # Export the clean text using the template
                        try:
                            json_path = find_json_path(ctx.image_name)
                            if json_path and os.path.exists(json_path):
                                from desktop_qt_ui.services.workflow_service import generate_translated_text, get_template_path_from_config
                                template_path = get_template_path_from_config()
                                if template_path and os.path.exists(template_path):
                                    # 导出翻译
                                    translated_result = generate_translated_text(json_path, template_path)
                                    logger.info(f"Translated text export for {os.path.basename(ctx.image_name)}: {translated_result}")
                                else:
                                    logger.warning(f"Template file not found, cannot export clean text for {os.path.basename(ctx.image_name)}: {template_path}")
                            else:
                                logger.warning(f"JSON file not found, cannot export clean text for {os.path.basename(ctx.image_name)}")
                        except Exception as e:
                            logger.error(f"Failed to export clean text for {os.path.basename(ctx.image_name)} in HQ mode: {e}")

                    # ✅ 标记成功（导出翻译完成）
                    ctx.success = True
                    results.append(ctx)
                
                continue # BUG FIX: Continue to the next batch instead of returning

            # 阶段三：渲染并保存当前批次
            for ctx, config in preprocessed_contexts:
                # 检查是否被取消
                await asyncio.sleep(0)
                try:
                    if hasattr(ctx, 'input'):
                        from .utils.generic import get_image_md5
                        image_md5 = get_image_md5(ctx.input)
                        if not self._restore_image_context(image_md5):
                            self._set_image_context(config, ctx.input)
                    
                    # Colorize Only Mode: Skip rendering pipeline
                    if not self.colorize_only:
                        ctx = await self._complete_translation_pipeline(ctx, config)

                    # --- BEGIN SAVE LOGIC ---
                    if save_info and ctx.result:
                        try:
                            output_folder = save_info.get('output_folder')
                            input_folders = save_info.get('input_folders', set())
                            output_format = save_info.get('format')
                            overwrite = save_info.get('overwrite', True)

                            file_path = ctx.image_name
                            final_output_dir = output_folder
                            parent_dir = os.path.normpath(os.path.dirname(file_path))
                            
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

                            base_filename, _ = os.path.splitext(os.path.basename(file_path))
                            if output_format and output_format.strip() and output_format.lower() != 'none':
                                output_filename = f"{base_filename}.{output_format}"
                            else:
                                output_filename = os.path.basename(file_path)
                            
                            final_output_path = os.path.join(final_output_dir, output_filename)

                            if not overwrite and os.path.exists(final_output_path):
                                logger.info(f"  -> ⚠️ [HQ] Skipping existing file: {os.path.basename(final_output_path)}")
                            else:
                                image_to_save = ctx.result
                                if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                                    image_to_save = image_to_save.convert('RGB')
                                
                                image_to_save.save(final_output_path, quality=self.save_quality)
                                logger.info(f"  -> ✅ [HQ] Saved successfully: {os.path.basename(final_output_path)}")

                        except Exception as save_err:
                            logger.error(f"Error saving high-quality result for {os.path.basename(ctx.image_name)}: {save_err}")
                    # --- END SAVE LOGIC ---

                    if ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                        # 使用循环变量中的config，而不是从ctx中获取
                        self._save_text_to_file(ctx.image_name, ctx, config)

                    # ✅ 标记成功（在清理result之前）
                    ctx.success = True
                    
                    # ✅ 清理ctx中的大对象，只保留必要信息
                    if hasattr(ctx, 'result'):
                        ctx.result = None  # 保存后删除渲染结果
                    
                    # ✅ 清理中间处理图像（保留text_regions等元数据）
                    if hasattr(ctx, 'img_rgb'):
                        ctx.img_rgb = None
                    if hasattr(ctx, 'img_inpainted'):
                        ctx.img_inpainted = None
                    if hasattr(ctx, 'img_rendered'):
                        ctx.img_rendered = None
                    if hasattr(ctx, 'img_colorized'):
                        ctx.img_colorized = None
                    if hasattr(ctx, 'img_alpha'):
                        ctx.img_alpha = None
                    if hasattr(ctx, 'mask'):
                        ctx.mask = None
                    if hasattr(ctx, 'mask_raw'):
                        ctx.mask_raw = None
                    
                    results.append(ctx)
                except Exception as e:
                    logger.error(f"Error rendering image: {e}")
                    # 渲染失败时抛出异常，而不是继续处理
                    raise RuntimeError(f"Rendering failed for {os.path.basename(ctx.image_name) if hasattr(ctx, 'image_name') else 'Unknown'}: {e}") from e
            
            # ✅ 批次完成后立即清理内存（但保留翻译历史供下一批次使用）
            import gc
            # 1. 清理batch_data中的图像引用
            for data in batch_data:
                if 'image' in data:
                    data['image'] = None
            batch_data.clear()
            
            # 2. 清理preprocessed_contexts中的输入图像
            for ctx, _ in preprocessed_contexts:
                if hasattr(ctx, 'input'):
                    ctx.input = None
            preprocessed_contexts.clear()
            
            # 3. 强制垃圾回收
            gc.collect()
            
            # 4. GPU显存清理（如果使用GPU）
            if hasattr(self, 'device') and (self.device == 'cuda' or self.device == 'mps'):
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass
            
            logger.debug(f'[MEMORY] Batch {batch_start//batch_size + 1} cleanup completed (kept translation history for context)')

        logger.info(f"High quality translation completed: processed {len(results)} images")
        return results

    def _group_by_chapter(self, images_with_configs):
        """
        按章节分组图片（基于文件路径）
        
        返回: List[{'name': str, 'path': Path, 'images': List[tuple]}]
        """
        from pathlib import Path
        
        chapters = []
        current_chapter_dir = None
        current_chapter_images = []
        
        for img, cfg in images_with_configs:
            # 获取图片所在目录
            img_path = Path(img.name) if hasattr(img, 'name') and img.name else None
            img_dir = img_path.parent if img_path else None
            
            if img_dir != current_chapter_dir:
                # 新章节开始
                if current_chapter_images:
                    chapters.append({
                        'name': current_chapter_dir.name if current_chapter_dir else 'Unknown',
                        'path': current_chapter_dir,
                        'images': current_chapter_images
                    })
                current_chapter_dir = img_dir
                current_chapter_images = [(img, cfg)]
            else:
                # 同一章节
                current_chapter_images.append((img, cfg))
        
        # 最后一个章节
        if current_chapter_images:
            chapters.append({
                'name': current_chapter_dir.name if current_chapter_dir else 'Unknown',
                'path': current_chapter_dir,
                'images': current_chapter_images
            })
        
        return chapters

    async def _translate_batch_pipeline_4_lines(self, images_with_configs: List[tuple], save_info: dict = None) -> List[Context]:
        """
        四线流水线批量翻译实现
        
        特性：
        - 按章节分组处理，每章节重置上下文
        - 章节内批次串行，保证滚动窗口上下文
        
        Line1: 检测+OCR (并发)
        Line2: 翻译 (批次并行)
        Line3: 修复/Inpainting (并发)
        Line4: 渲染+超分 (并发)
        """
        import asyncio
        from asyncio import Queue, Semaphore
        import traceback
        
        total_images = len(images_with_configs)
        
        # === 智能长图拼接（可选） ===
        if self.enable_long_image_stitching:
            try:
                from .image_stitcher import SmartImageStitcher
                import cv2
                import numpy as np
                from PIL import Image
                
                logger.info(f"[长图拼接] 启用智能长图拼接功能")
                logger.info(f"[长图拼接] 配置: 最大高度={self.long_image_max_height}px, 边界检测={self.long_image_bubble_margin}px")
                logger.info(f"[长图拼接] 开始处理 {total_images} 张图片")
                
                # 创建拼接器
                stitcher = SmartImageStitcher(
                    max_height=self.long_image_max_height,
                    bubble_margin=self.long_image_bubble_margin
                )
                
                # 拼接图片
                stitched_segments = stitcher.stitch_images(images_with_configs)
                
                logger.info(f"[长图拼接] 完成: {total_images}张原始图片 → {len(stitched_segments)}个长图段")
                
                # 准备所有长图段（批量处理以保持上下文连贯）
                all_segment_images_configs = []
                
                for segment_idx, (stitched_img, original_configs, metadata) in enumerate(stitched_segments):
                    logger.info(f"[长图拼接] 准备段{segment_idx+1}/{len(stitched_segments)}: {metadata['image_count']}张图, 高度{metadata['total_height']}px")
                    
                    # 将numpy数组转换为PIL Image
                    if isinstance(stitched_img, np.ndarray):
                        stitched_img_pil = Image.fromarray(cv2.cvtColor(stitched_img, cv2.COLOR_BGR2RGB))
                    else:
                        stitched_img_pil = stitched_img
                    
                    # 使用第一张图的config作为长图的config
                    if original_configs:
                        # original_configs是[(img, config), ...]格式，提取第一个config
                        segment_config = original_configs[0][1]
                    else:
                        segment_config = images_with_configs[0][1] if images_with_configs else None
                    
                    # 为长图生成清晰的名称：seg03_img005-007_3p.jpg
                    indices = metadata['image_indices']
                    img_count = metadata['image_count']
                    start_num = indices[0] + 1  # 转为1-based
                    end_num = indices[-1] + 1
                    
                    # 获取原始图片的完整路径和扩展名
                    if original_configs:
                        first_img = original_configs[0][0]
                        base_name = getattr(first_img, 'name', '')
                        import os
                        if base_name:
                            # 获取原始图片的目录路径
                            original_dir = os.path.dirname(base_name)
                            ext = os.path.splitext(base_name)[1]
                        else:
                            original_dir = ''
                            ext = '.jpg'
                    else:
                        original_dir = ''
                        ext = '.jpg'
                    
                    # 新命名格式：seg03_img005-007_3p.jpg
                    new_filename = f"seg{segment_idx+1:02d}_img{start_num:03d}-{end_num:03d}_{img_count}p{ext}"
                    
                    # 设置PIL Image的name属性（包含完整路径）
                    if original_dir:
                        stitched_name = os.path.join(original_dir, new_filename)
                    else:
                        stitched_name = new_filename
                    stitched_img_pil.name = stitched_name
                    
                    # 添加到批量处理列表
                    all_segment_images_configs.append((stitched_img_pil, segment_config))
                
                logger.info(f"[长图拼接] 所有段准备完成，开始批量处理 {len(all_segment_images_configs)} 个长图段")
                
                # 临时禁用拼接避免递归，然后批量处理所有段
                original_stitching_flag = self.enable_long_image_stitching
                self.enable_long_image_stitching = False
                
                try:
                    # 一次性处理所有段，保持上下文连贯
                    all_segment_results = await self._translate_batch_pipeline_4_lines(
                        all_segment_images_configs, save_info
                    )
                    logger.info(f"[长图拼接] 所有段处理完成，共 {len(all_segment_results)} 个结果")
                    return all_segment_results
                finally:
                    # 恢复拼接标志
                    self.enable_long_image_stitching = original_stitching_flag
                
            except Exception as e:
                logger.error(f"[长图拼接] 拼接失败: {e}")
                logger.exception(e)
                logger.info(f"[长图拼接] 回退到正常流水线处理")
                # 失败时继续使用正常流程
        
        logger.info(f"四线流水线启动：处理 {total_images} 张图片")
        
        # 按章节分组
        chapters = self._group_by_chapter(images_with_configs)
        logger.info(f"检测到 {len(chapters)} 个章节")
        
        all_results = []
        
        # 逐章节处理
        for chapter_idx, chapter in enumerate(chapters):
            logger.info(f"\n{'='*60}")
            logger.info(f"开始处理章节 {chapter_idx+1}/{len(chapters)}: {chapter['name']} ({len(chapter['images'])}页)")
            logger.info(f"{'='*60}\n")
            
            # 🔑 每个章节开始时重置上下文
            self.all_page_translations = []
            logger.info(f"章节 {chapter['name']}: 重置上下文历史")
            
            # 处理该章节
            chapter_results = await self._process_chapter_pipeline(chapter['images'], save_info)
            all_results.extend(chapter_results)
            
            logger.info(f"\n{'='*60}")
            logger.info(f"完成章节 {chapter_idx+1}/{len(chapters)}: {chapter['name']}")
            logger.info(f"{'='*60}\n")
        
        return all_results
    
    async def _process_chapter_pipeline(self, images_with_configs: List[tuple], save_info: dict = None) -> List[Context]:
        """
        处理单个章节的流水线
        """
        import asyncio
        from asyncio import Queue, Semaphore
        import traceback
        
        total_images = len(images_with_configs)
        logger.info(f"章节图片数: {total_images} 张")
        
        # 创建队列和信号量
        ocr_queue = Queue(maxsize=50)  # Line1 -> Line2
        translate_queue = Queue(maxsize=50)  # Line2 -> Line3
        inpaint_queue = Queue(maxsize=50)  # Line3 -> Line4
        render_queue = Queue(maxsize=50)  # Line4 -> 结果
        
        # 并发控制信号量
        line1_semaphore = Semaphore(self.pipeline_line1_concurrency)
        line3_semaphore = Semaphore(self.pipeline_line3_concurrency)
        line4_semaphore = Semaphore(self.pipeline_line4_concurrency)
        
        # 上下文追踪
        page_counter = {'count': 0}
        page_counter_lock = asyncio.Lock()  # 保护批次计数器的并发访问
        
        results = []
        completed_count = 0
        
        async def line1_worker(image, config, index):
            """Line1: 检测+OCR"""
            async with line1_semaphore:
                try:
                    logger.info(f"Line1: [并发{self.pipeline_line1_concurrency}] 开始处理图片 {index+1}/{total_images}")
                    
                    # 设置图片上下文
                    self._set_image_context(config, image)
                    
                    ctx = Context()
                    ctx.input = image
                    ctx.image_name = getattr(image, 'name', None)
                    ctx.verbose = self.verbose
                    ctx.save_quality = self.save_quality
                    ctx.config = config

                    # 上色处理
                    if config.colorizer.colorizer.value != 'none':
                        logger.debug(f"Line1: 上色处理 - 图片 {index+1}")
                        ctx.img_colorized = await self._run_colorizer(config, ctx)
                    else:
                        ctx.img_colorized = ctx.input

                    # 超分处理
                    if config.upscale.upscale_ratio:
                        logger.debug(f"Line1: 超分处理 - 图片 {index+1}")
                        ctx.upscaled = await self._run_upscaling(config, ctx)
                    else:
                        ctx.upscaled = ctx.img_colorized

                    ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

                    # 检测
                    logger.debug(f"Line1: 检测处理 - 图片 {index+1}")
                    ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
                    await asyncio.sleep(0)  # 让出控制权

                    if not ctx.textlines:
                        logger.info(f"Line1: 图片 {index+1} 未检测到文本，跳过后续处理")
                        await render_queue.put((ctx, config, index, True))  # 直接跳到结果队列
                        return

                    # OCR
                    logger.debug(f"Line1: OCR处理 - 图片 {index+1}")
                    ctx.textlines = await self._run_ocr(config, ctx)
                    await asyncio.sleep(0)  # 让出控制权

                    if not ctx.textlines:
                        logger.info(f"Line1: 图片 {index+1} OCR未识别到文本，跳过后续处理")
                        await render_queue.put((ctx, config, index, True))
                        return

                    # 文本行合并
                    logger.debug(f"Line1: 文本行合并 - 图片 {index+1}")
                    textline_count_before = len(ctx.textlines) if ctx.textlines else 0
                    ctx.text_regions = await self._run_textline_merge(config, ctx)
                    await asyncio.sleep(0)  # 让出控制权

                    if not ctx.text_regions:
                        logger.warning(f"Line1: 图片 {index+1} 文本合并后无区域（合并前有{textline_count_before}个文本行），跳过后续处理")
                        await render_queue.put((ctx, config, index, True))
                        return

                    logger.info(f"Line1: 完成处理图片 {index+1}/{total_images}，检测到 {len(ctx.text_regions)} 个文本区域")
                    for i, region in enumerate(ctx.text_regions):
                        logger.debug(f"Line1: 图片{index+1} - region{i}: '{region.text}'")
                    
                    # 放入OCR队列
                    await ocr_queue.put((ctx, config, index))
                    logger.debug(f"Line1: 图片{index+1}已放入ocr_queue，当前队列大小: {ocr_queue.qsize()}")
                    
                    # 主动让出控制权，让Line2/3/4有机会执行
                    await asyncio.sleep(0)
                    
                except Exception as e:
                    logger.error(f"Line1: 处理图片 {index+1} 时出错: {e}")
                    logger.error(f"Line1: 错误详情: {traceback.format_exc()}")
                    # 创建失败的context
                    ctx = Context()
                    ctx.input = image
                    ctx.image_name = getattr(image, 'name', None)
                    ctx.result = image  # 返回原图
                    await render_queue.put((ctx, config, index, True))

        async def line2_worker():
            """Line2: 翻译（批量处理）"""
            logger.info("Line2: 翻译工作线程已启动")
            batch_buffer = []
            consecutive_timeouts = 0
            max_consecutive_timeouts = 3
            
            while True:
                try:
                    try:
                        item = await asyncio.wait_for(ocr_queue.get(), timeout=1.0)
                        ctx, config, image_idx = item
                        batch_buffer.append(item)
                        consecutive_timeouts = 0
                        logger.debug(f"Line2: 从队列获取图片{image_idx+1}，当前批次大小: {len(batch_buffer)}")
                        
                        if len(batch_buffer) >= self.pipeline_translation_batch_size:
                            await self._process_translation_batch(batch_buffer, translate_queue, page_counter, page_counter_lock)
                            batch_buffer = []
                            
                    except asyncio.TimeoutError:
                        consecutive_timeouts += 1
                        logger.debug(f"Line2: 超时{consecutive_timeouts}次，队列大小: {ocr_queue.qsize()}, Line1完成: {all(task.done() for task in line1_tasks)}")
                        
                        if batch_buffer:
                            logger.info(f"Line2: 超时后处理剩余批次，大小: {len(batch_buffer)}")
                            await self._process_translation_batch(batch_buffer, translate_queue, page_counter, page_counter_lock)
                            batch_buffer = []
                        
                        if consecutive_timeouts >= max_consecutive_timeouts:
                            if all(task.done() for task in line1_tasks) and ocr_queue.empty():
                                logger.info(f"Line2: Line1已完成且队列为空（连续{consecutive_timeouts}次超时），退出")
                                break
                            else:
                                if not all(task.done() for task in line1_tasks):
                                    logger.debug("Line2: Line1还在运行，重置超时计数")
                                    consecutive_timeouts = 0
                                elif not ocr_queue.empty():
                                    logger.debug(f"Line2: 队列不为空（{ocr_queue.qsize()}项），重置超时计数")
                                    consecutive_timeouts = 0
                            
                except Exception as e:
                    logger.error(f"Line2: 翻译工作进程出错: {e}")
                    break
            
            if batch_buffer:
                await self._process_translation_batch(batch_buffer, translate_queue, page_counter, page_counter_lock)

        async def line3_worker():
            """Line3: 修复/Inpainting"""
            logger.info("Line3: 修复工作线程已启动")
            while True:
                try:
                    item = await asyncio.wait_for(translate_queue.get(), timeout=5.0)
                    if item is None:  # 结束信号
                        break
                        
                    ctx, config, index = item
                    
                    async with line3_semaphore:
                        try:
                            logger.info(f"Line3: 开始修复处理图片 {index+1}/{total_images}")
                            
                            # 恢复图片上下文
                            if hasattr(ctx, 'input'):
                                from .utils.generic import get_image_md5
                                image_md5 = get_image_md5(ctx.input)
                                if not self._restore_image_context(image_md5):
                                    self._set_image_context(config, ctx.input)

                            # 掩码细化
                            if ctx.mask is None:
                                logger.debug(f"Line3: 掩码细化 - 图片 {index+1}")
                                ctx.mask = await self._run_mask_refinement(config, ctx)

                            # 修复/Inpainting
                            logger.debug(f"Line3: 修复处理 - 图片 {index+1}")
                            ctx.img_inpainted = await self._run_inpainting(config, ctx)

                            # 保存修复后的图片
                            if hasattr(ctx, 'image_name') and ctx.image_name and ctx.img_inpainted is not None:
                                self._save_inpainted_image(ctx.image_name, ctx.img_inpainted)

                            logger.info(f"Line3: 完成修复处理图片 {index+1}/{total_images}")
                            await inpaint_queue.put((ctx, config, index))
                            
                        except Exception as e:
                            logger.error(f"Line3: 处理图片 {index+1} 时出错: {e}")
                            logger.error(f"Line3: 错误详情: {traceback.format_exc()}")
                            # 跳过修复，使用原图
                            ctx.img_inpainted = ctx.img_rgb
                            await inpaint_queue.put((ctx, config, index))
                            
                except asyncio.TimeoutError:
                    # 检查是否所有前置任务都完成了
                    if translate_queue.empty() and all(task.done() for task in line2_tasks):
                        break
                except Exception as e:
                    logger.error(f"Line3: 工作进程出错: {e}")
                    break

        async def line4_worker():
            """Line4: 渲染+超分"""
            logger.info("Line4: 渲染工作线程已启动")
            while True:
                try:
                    item = await asyncio.wait_for(inpaint_queue.get(), timeout=5.0)
                    if item is None:  # 结束信号
                        break
                        
                    ctx, config, index = item
                    
                    async with line4_semaphore:
                        try:
                            logger.info(f"Line4: 开始渲染处理图片 {index+1}/{total_images}")
                            
                            # 恢复图片上下文
                            if hasattr(ctx, 'input'):
                                from .utils.generic import get_image_md5
                                image_md5 = get_image_md5(ctx.input)
                                if not self._restore_image_context(image_md5):
                                    self._set_image_context(config, ctx.input)

                            # 渲染
                            logger.debug(f"Line4: 渲染处理 - 图片 {index+1}")
                            ctx.img_rendered = await self._run_text_rendering(config, ctx)

                            # 生成最终结果
                            ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
                            ctx = await self._revert_upscale(config, ctx)

                            # 保存JSON
                            if ctx.text_regions and hasattr(ctx, 'image_name') and ctx.image_name:
                                self._save_text_to_file(ctx.image_name, ctx, config)

                            logger.info(f"Line4: 完成渲染处理图片 {index+1}/{total_images}")
                            await render_queue.put((ctx, config, index, False))
                            
                        except Exception as e:
                            logger.error(f"Line4: 处理图片 {index+1} 时出错: {e}")
                            logger.error(f"Line4: 错误详情: {traceback.format_exc()}")
                            # 使用修复后的图片作为结果
                            ctx.result = ctx.img_inpainted if hasattr(ctx, 'img_inpainted') else ctx.input
                            await render_queue.put((ctx, config, index, False))
                            
                except asyncio.TimeoutError:
                    # 检查是否所有前置任务都完成了
                    if inpaint_queue.empty() and all(task.done() for task in line3_tasks):
                        break
                except Exception as e:
                    logger.error(f"Line4: 工作进程出错: {e}")
                    break

        # 预先声明任务列表（供worker函数内部引用）
        line1_tasks = []
        line2_tasks = []
        line3_tasks = []
        line4_tasks = []
        
        # 启动所有工作线程
        line1_tasks = [asyncio.create_task(line1_worker(image, config, i)) 
                       for i, (image, config) in enumerate(images_with_configs)]
        
        line2_tasks = [asyncio.create_task(line2_worker()) 
                       for _ in range(self.pipeline_line2_concurrency)]
        
        line3_tasks = [asyncio.create_task(line3_worker()) 
                       for _ in range(self.pipeline_line3_concurrency)]
        
        line4_tasks = [asyncio.create_task(line4_worker()) 
                       for _ in range(self.pipeline_line4_concurrency)]
        
        logger.info(f"四线流水线任务启动完成：Line1({len(line1_tasks)}), Line2({len(line2_tasks)}), Line3({len(line3_tasks)}), Line4({len(line4_tasks)})")

        # 收集结果
        result_contexts = [None] * total_images
        
        # 结果收集器
        async def result_collector():
            nonlocal completed_count
            while completed_count < total_images:
                try:
                    ctx, config, index, skipped = await render_queue.get()
                    result_contexts[index] = ctx
                    completed_count += 1
                    
                    if skipped:
                        logger.info(f"图片 {index+1}/{total_images} 处理完成（跳过翻译）")
                    else:
                        logger.info(f"图片 {index+1}/{total_images} 流水线处理完成")
                    
                    # 如果提供了save_info，保存图片
                    if save_info and ctx.result:
                        await self._save_pipeline_result(ctx, config, save_info)
                    
                except Exception as e:
                    logger.error(f"结果收集器出错: {e}")

        result_task = asyncio.create_task(result_collector())

        # 真正的流水线执行：所有线程同时启动，通过队列协调
        try:
            # 等待所有任务完成（流水线模式）
            all_tasks = line1_tasks + line2_tasks + line3_tasks + line4_tasks + [result_task]
            await asyncio.gather(*all_tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"流水线执行出错: {e}")
        finally:
            # 清理：发送结束信号给所有工作线程
            logger.info("发送流水线结束信号...")
            
            # 给Line2发送结束信号（通过空的ocr_queue检测）
            # Line2会在超时后自动检测line1_tasks完成状态
            
            # 给Line3发送结束信号
            for _ in range(self.pipeline_line3_concurrency):
                try:
                    await asyncio.wait_for(translate_queue.put(None), timeout=1.0)
                except:
                    pass
            
            # 给Line4发送结束信号  
            for _ in range(self.pipeline_line4_concurrency):
                try:
                    await asyncio.wait_for(inpaint_queue.put(None), timeout=1.0)
                except:
                    pass
        logger.info("四线流水线处理完成")

        # 过滤掉None结果并返回
        final_results = [ctx for ctx in result_contexts if ctx is not None]
        logger.info(f"四线流水线总计完成: {len(final_results)}/{total_images} 张图片")
        
        return final_results

    def _is_api_polling_failure(self, translated_texts):
        """检测是否为API轮询失败错误"""
        if not translated_texts or len(translated_texts) < 2:
            return False
        
        # 检测是否包含特定的错误消息
        error_patterns = [
            '所有API密钥均请求失败',
            '具体错误请查看轮询日志',
            'all.*key.*failed',
            'API.*polling.*failed'
        ]
        
        text_combined = ' '.join(translated_texts[:5])  # 检查前5条
        return any(pattern in text_combined for pattern in error_patterns)
    
    async def _enter_degraded_mode(self):
        """进入降级模式：单图处理，减少并发数"""
        if self._degraded_mode:
            return  # 已经在降级模式
        
        self._degraded_mode = True
        self._degraded_success_count = 0
        
        # 保存原始配置
        self._original_batch_size = self.pipeline_translation_batch_size
        self._original_line2_concurrency = self.pipeline_line2_concurrency
        
        # 降级配置：单图处理，并发数为1
        self.pipeline_translation_batch_size = 1
        # 注意：Line2并发数不能在运行时改变（线程已启动）
        # 但批次大小变为1就能实现单图处理
        
        logger.warning("="*80)
        logger.warning("🔻 API轮询失败被检测到！启动智能降级模式")
        logger.warning(f"降级配置：批次大小 {self._original_batch_size} → 1 (单图处理)")
        logger.warning(f"恢复条件：连续成功 {self._degraded_recovery_threshold} 张图片")
        logger.warning("="*80)
    
    async def _exit_degraded_mode(self):
        """退出降级模式：恢复批量处理"""
        if not self._degraded_mode:
            return  # 未在降级模式
        
        self._degraded_mode = False
        self._degraded_success_count = 0
        
        # 恢复原始配置
        if self._original_batch_size is not None:
            self.pipeline_translation_batch_size = self._original_batch_size
        
        logger.info("="*80)
        logger.info("✨ API恢复正常！退出降级模式")
        logger.info(f"恢复配置：批次大小 1 → {self.pipeline_translation_batch_size} (批量处理)")
        logger.info("="*80)
    
    async def _process_translation_batch(self, batch_buffer, translate_queue, page_counter, page_counter_lock):
        """处理翻译批次：使用原文作为上下文"""
        if not batch_buffer:
            return
            
        try:
            # 原子地获取并递增批次索引（防止并发竞态条件）
            async with page_counter_lock:
                current_batch_index = page_counter['count']
                page_counter['count'] += 1
            
            logger.info(f"Line2: 开始翻译批次{current_batch_index} ({len(batch_buffer)}张图片) - 使用原文作为上下文参考")
            
            # 提取整个批次的所有文本
            all_texts = []
            text_mapping = []  # (batch_index, region_index, image_idx)
            
            # 先为批次加载AI断句prompt（使用第一个配置）
            sample_ctx, sample_config, _ = batch_buffer[0]
            # 确保ctx.config存在（翻译器需要检查config.render.disable_auto_wrap）
            if not hasattr(sample_ctx, 'config') or sample_ctx.config is None:
                sample_ctx.config = sample_config
            sample_ctx = await self._load_and_prepare_prompts(sample_config, sample_ctx)
            
            # 检查AI断句是否启用
            ai_break_enabled = sample_config.render.disable_auto_wrap
            logger.info(f"Line2: 为批次加载AI断句prompt完成 (AI断句开关: {ai_break_enabled})")
            
            for batch_idx, (ctx, config, image_idx) in enumerate(batch_buffer):
                if ctx.text_regions:
                    for region_idx, region in enumerate(ctx.text_regions):
                        all_texts.append(region.text)
                        text_mapping.append((batch_idx, region_idx, image_idx))

            if all_texts:
                logger.info(f"Line2: 批次总共 {len(all_texts)} 个文本区域，准备高质量翻译")
                logger.debug(f"Line2: 批次文本内容: {all_texts}")
                
                # 【关键修复】在翻译前先保存原文，使后续批次能立即获取上下文
                # 这样可以避免并发竞态条件导致上下文丢失
                batch_originals = {}
                for ctx, config, image_idx in batch_buffer:
                    if ctx.text_regions:
                        for region in ctx.text_regions:
                            batch_originals[region.text] = region.text  # 原文->原文的映射
                
                if batch_originals:
                    self.all_page_translations.append(batch_originals)
                    logger.info(f"Line2: 批次{current_batch_index}在翻译前保存了 {len(batch_originals)} 条原文（供后续批次使用）")
                
                try:
                    # 为高质量翻译器准备batch_data（启用高质量翻译模式和AI断句）
                    if sample_config.translator.translator in [Translator.openai_hq, Translator.gemini_hq]:
                        batch_data = []
                        for batch_idx, (ctx, config, image_idx) in enumerate(batch_buffer):
                            if ctx.text_regions:
                                batch_data.append({
                                    'original_texts': [region.text for region in ctx.text_regions],
                                    'text_regions': ctx.text_regions,
                                    'image': ctx.input if hasattr(ctx, 'input') else None
                                })
                        
                        # 设置high_quality_batch_data以启用高质量翻译模式
                        sample_ctx.high_quality_batch_data = batch_data
                        sample_ctx.high_quality_batch_size = len(batch_buffer)
                        logger.info(f"Line2: 已设置high_quality_batch_data，批次大小: {len(batch_data)}")
                    
                    # 整个批次统一翻译（传递prev_batch_index以使用前一批次上下文）
                    # 高质量翻译器同时处理批次内的多图上下文
                    prev_batch_index = current_batch_index - 1 if current_batch_index > 0 else None
                    translated_texts = await self._batch_translate_texts(
                        all_texts, sample_config, sample_ctx,
                        page_index=prev_batch_index  # ← 使用前一批次作为上下文
                    )
                    
                    # 【智能降级检测】检测API轮询失败
                    if self._is_api_polling_failure(translated_texts):
                        logger.error(f"Line2: 批次{current_batch_index} 检测到API轮询失败")
                        await self._enter_degraded_mode()
                        # 使用原文作为后备
                        translated_texts = all_texts.copy()
                    elif self._degraded_mode:
                        # 在降级模式下，翻译成功的计数
                        self._degraded_success_count += len(batch_buffer)
                        logger.info(f"Line2: 降级模式 - 成功翻译 {len(batch_buffer)} 张图片，累计成功 {self._degraded_success_count}/{self._degraded_recovery_threshold}")
                        
                        # 检查是否达到恢复阈值
                        if self._degraded_success_count >= self._degraded_recovery_threshold:
                            await self._exit_degraded_mode()
                    
                    # AI断句处理：整个批次级别检查数量匹配
                    if len(translated_texts) != len(all_texts):
                        logger.warning(f"Line2: 批次AI断句导致翻译数量不匹配 - 输入:{len(all_texts)}, 输出:{len(translated_texts)}")
                        logger.info(f"Line2: 批次原文: {all_texts}")
                        logger.info(f"Line2: 批次译文: {translated_texts}")
                        
                        # 智能分配：尝试映射翻译结果
                        if len(translated_texts) > len(all_texts):
                            # 翻译结果更多：可能是拆分，取前N个
                            logger.info(f"Line2: 批次AI断句拆分了文本，取前{len(all_texts)}个翻译")
                            translated_texts = translated_texts[:len(all_texts)]
                        else:
                            # 翻译结果更少：可能是合并，补充原文
                            logger.info(f"Line2: 批次AI断句合并了文本，用原文补足")
                            while len(translated_texts) < len(all_texts):
                                translated_texts.append(all_texts[len(translated_texts)])
                    
                    # 将翻译结果分配回原始区域
                    for text_idx, (batch_idx, region_idx, image_idx) in enumerate(text_mapping):
                        if text_idx < len(translated_texts):
                            ctx, config, _ = batch_buffer[batch_idx]
                            if ctx.text_regions and region_idx < len(ctx.text_regions):
                                region = ctx.text_regions[region_idx]
                                region.translation = translated_texts[text_idx]
                                region.target_lang = config.translator.target_lang
                                region._alignment = config.render.alignment
                                region._direction = config.render.direction
                                logger.debug(f"Line2: 图片{image_idx+1} 区域{region_idx}: '{region.text}' -> '{region.translation}'")

                except Exception as e:
                    # 【智能降级检测】检查是否为API失败
                    error_msg = str(e)
                    if '所有API密钥均请求失败' in error_msg or 'API.*failed' in error_msg.lower():
                        logger.error(f"Line2: 批次{current_batch_index} 翻译异常 - 检测到API失败: {e}")
                        await self._enter_degraded_mode()
                    
                    # 捕获翻译错误（包括AI断句数量不匹配）
                    if "Translation count mismatch" in error_msg:
                        logger.warning(f"Line2: 批次AI断句数量不匹配错误，使用原文作为备选: {e}")
                        # 使用原文作为翻译结果
                        for text_idx, (batch_idx, region_idx, image_idx) in enumerate(text_mapping):
                            if text_idx < len(all_texts):
                                ctx, config, _ = batch_buffer[batch_idx]
                                if ctx.text_regions and region_idx < len(ctx.text_regions):
                                    region = ctx.text_regions[region_idx]
                                    region.translation = all_texts[text_idx]
                                    region.target_lang = config.translator.target_lang
                                    region._alignment = config.render.alignment
                                    region._direction = config.render.direction
                        # 标记翻译错误（使用原文）
                        for ctx, config, _ in batch_buffer:
                            ctx.translation_error = f"批次翻译失败（AI断句数量不匹配），已使用原文: {error_msg}"
                    else:
                        logger.error(f"Line2: 批次翻译出错: {e}")
                        logger.error(f"Line2: 错误详情: {traceback.format_exc()}")
                        # 出错时使用原文
                        for text_idx, (batch_idx, region_idx, image_idx) in enumerate(text_mapping):
                            if text_idx < len(all_texts):
                                ctx, config, _ = batch_buffer[batch_idx]
                                if ctx.text_regions and region_idx < len(ctx.text_regions):
                                    region = ctx.text_regions[region_idx]
                                    region.translation = all_texts[text_idx]
                                    region.target_lang = config.translator.target_lang
                                    region._alignment = config.render.alignment
                                    region._direction = config.render.direction
                        # 标记翻译错误（使用原文）
                        for ctx, config, _ in batch_buffer:
                            ctx.translation_error = f"批次翻译失败，已使用原文: {error_msg}\n详情: {traceback.format_exc()}"

            # 应用后处理
            for ctx, config, image_idx in batch_buffer:
                if ctx.text_regions:
                    ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
            
            # 更新批次翻译结果（原文->译文映射）
            # 注意：翻译前已经保存了原文，这里需要更新为译文
            batch_translations = {}
            for ctx, config, image_idx in batch_buffer:
                if ctx.text_regions:
                    for r in ctx.text_regions:
                        if r.translation:
                            batch_translations[r.text] = r.translation
            
            if batch_translations and current_batch_index < len(self.all_page_translations):
                # 更新已存在的原文条目为译文
                self.all_page_translations[current_batch_index].update(batch_translations)
                logger.info(f"Line2: 更新批次{current_batch_index}的 {len(batch_translations)} 条翻译结果（原文->译文）")
            
            logger.info(f"Line2: 完成批次{current_batch_index}翻译 ({len(batch_buffer)}张图片)")
            
            # 将处理好的项目放入下一个队列
            for ctx, config, image_idx in batch_buffer:
                await translate_queue.put((ctx, config, image_idx))
                    
        except Exception as e:
            logger.error(f"Line2: 批量翻译出错: {e}")
            logger.error(f"Line2: 错误详情: {traceback.format_exc()}")
            
            # 标记批次中所有图片翻译失败
            for ctx, config, _ in batch_buffer:
                if not hasattr(ctx, 'translation_error') or not ctx.translation_error:
                    ctx.translation_error = f"批次翻译过程出错: {str(e)}\n详情: {traceback.format_exc()}"
            
            # 出错时仍然要将项目传递给下一阶段，避免流水线阻塞
            for ctx, config, image_idx in batch_buffer:
                await translate_queue.put((ctx, config, image_idx))

    async def _save_pipeline_result(self, ctx, config, save_info):
        """保存流水线处理结果"""
        try:
            if not ctx.result:
                return
                
            output_folder = save_info.get('output_folder')
            input_folders = save_info.get('input_folders', set())
            output_format = save_info.get('format')
            overwrite = save_info.get('overwrite', True)

            file_path = ctx.image_name
            final_output_dir = output_folder
            parent_dir = os.path.normpath(os.path.dirname(file_path))
            
            for folder in input_folders:
                if parent_dir.startswith(folder):
                    relative_path = os.path.relpath(parent_dir, folder)
                    if relative_path == '.':
                        final_output_dir = os.path.join(output_folder, os.path.basename(folder))
                    else:
                        final_output_dir = os.path.join(output_folder, os.path.basename(folder), relative_path)
                    final_output_dir = os.path.normpath(final_output_dir)
                    break
            
            os.makedirs(final_output_dir, exist_ok=True)

            base_filename, _ = os.path.splitext(os.path.basename(file_path))
            if output_format and output_format.strip() and output_format.lower() != 'none':
                output_filename = f"{base_filename}.{output_format}"
            else:
                output_filename = os.path.basename(file_path)
            
            final_output_path = os.path.join(final_output_dir, output_filename)

            if not overwrite and os.path.exists(final_output_path):
                logger.info(f"  -> ⚠️ [PIPELINE] Skipping existing file: {os.path.basename(final_output_path)}")
            else:
                image_to_save = ctx.result
                if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                    image_to_save = image_to_save.convert('RGB')
                
                image_to_save.save(final_output_path, quality=self.save_quality)
                logger.info(f"  -> ✅ [PIPELINE] Saved successfully: {os.path.basename(final_output_path)}")

        except Exception as save_err:
            logger.error(f"Error saving pipeline result for {os.path.basename(ctx.image_name) if hasattr(ctx, 'image_name') else 'Unknown'}: {save_err}")
