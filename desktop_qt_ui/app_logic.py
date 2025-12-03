
"""
应用业务逻辑层
处理应用的核心业务逻辑，与UI层分离
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from manga_translator.config import (
    Alignment,
    Colorizer,
    Detector,
    Direction,
    Inpainter,
    InpaintPrecision,
    Ocr,
    Renderer,
    Translator,
    Upscaler,
)
from manga_translator.save import OUTPUT_FORMATS
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog, QListView, QTreeView

from services import (
    get_config_service,
    get_file_service,
    get_i18n_manager,
    get_logger,
    get_state_manager,
    get_translation_service,
)
from services.state_manager import AppStateKey


@dataclass
class AppConfig:
    """应用配置信息"""
    window_size: tuple = (1200, 800)
    theme: str = "dark"
    language: str = "zh_CN"
    auto_save: bool = True
    max_recent_files: int = 10

class MainAppLogic(QObject):
    """主页面业务逻辑控制器"""
    files_added = pyqtSignal(list)
    files_cleared = pyqtSignal()
    file_removed = pyqtSignal(str)
    config_loaded = pyqtSignal(dict)
    output_path_updated = pyqtSignal(str)
    task_completed = pyqtSignal(list)
    task_file_completed = pyqtSignal(dict)
    log_message = pyqtSignal(str)
    render_setting_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_service = get_config_service()
        self.translation_service = get_translation_service()
        self.file_service = get_file_service()
        self.state_manager = get_state_manager()
        self.i18n = get_i18n_manager()

        self.thread = None
        self.worker = None
        self.saved_files_count = 0
        self.saved_files_list = []  # 收集所有保存的文件路径

        self.source_files: List[str] = [] # Holds both files and folders
        self.file_to_folder_map: Dict[str, Optional[str]] = {} # 记录文件来自哪个文件夹
        self.excluded_subfolders: set = set() # 记录被删除的子文件夹路径
        self.folder_tree_cache: Dict[str, dict] = {} # 缓存文件夹的完整树结构 {top_folder: tree_structure}

        self.app_config = AppConfig()
        self.logger.info("主页面应用业务逻辑初始化完成")
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key


    @pyqtSlot(dict)
    def on_file_completed(self, result):
        """处理单个文件处理完成的信号并保存"""
        if not result.get('success') or not result.get('image_data'):
            self.logger.error(f"Skipping save for failed item: {result.get('original_path')}")
            # 更新任务统计（失败）
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.update_task_stats(success=False)
            return

        try:
            config = self.config_service.get_config()
            output_format = config.cli.format
            save_quality = config.cli.save_quality
            output_folder = config.app.last_output_path

            if not output_folder:
                self.logger.error(self._t("log_output_dir_not_set"))
                self.state_manager.set_status_message(self._t("error_output_dir_not_set"))
                return

            original_path = result['original_path']
            base_filename = os.path.basename(original_path)

            # 检查文件是否来自文件夹
            source_folder = self.file_to_folder_map.get(original_path)

            if source_folder:
                # 文件来自文件夹，保持相对路径结构
                parent_dir = os.path.normpath(os.path.dirname(original_path))
                relative_path = os.path.relpath(parent_dir, source_folder)
                
                # Normalize path and avoid adding '.' as a directory component
                if relative_path == '.':
                    final_output_folder = os.path.join(output_folder, os.path.basename(source_folder))
                else:
                    final_output_folder = os.path.join(output_folder, os.path.basename(source_folder), relative_path)
                final_output_folder = os.path.normpath(final_output_folder)
            else:
                # 文件是单独添加的，直接保存到输出目录
                final_output_folder = output_folder

            # 确定文件扩展名
            if output_format and output_format != self._t("format_not_specified"):
                file_extension = f".{output_format}"
                output_filename = os.path.splitext(base_filename)[0] + file_extension
            else:
                # 保持原扩展名
                output_filename = base_filename

            final_output_path = os.path.join(final_output_folder, output_filename)

            os.makedirs(final_output_folder, exist_ok=True)

            save_kwargs = {}
            image_to_save = result['image_data']

            # Convert RGBA to RGB for JPEG format
            if final_output_path.lower().endswith(('.jpg', '.jpeg')):
                if image_to_save.mode == 'RGBA':
                    image_to_save = image_to_save.convert('RGB')
                save_kwargs['quality'] = save_quality
            elif final_output_path.lower().endswith('.webp'):
                save_kwargs['quality'] = save_quality

            image_to_save.save(final_output_path, **save_kwargs)

            # 更新translation_map.json
            self._update_translation_map(original_path, final_output_path)

            self.saved_files_count += 1
            self.saved_files_list.append(final_output_path)  # 收集保存的文件路径
            self.logger.info(self._t("log_file_saved_successfully", path=final_output_path))
            self.task_file_completed.emit({'path': final_output_path})
            
            # 更新任务统计（成功）
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.update_task_stats(success=True)

        except Exception as e:
            self.logger.error(self._t("log_file_save_error", path=result['original_path'], error=e))
            # 更新任务统计（失败）
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.update_task_stats(success=False)

    def _update_translation_map(self, source_path: str, translated_path: str):
        """在输出目录创建或更新 translation_map.json"""
        try:
            import json
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
                        self.logger.warning(f"Could not decode {map_path}, creating a new one.")

            # 使用翻译后的路径作为键，确保唯一性
            translation_map[translated_path_norm] = source_path_norm

            with open(map_path, 'w', encoding='utf-8') as f:
                json.dump(translation_map, f, ensure_ascii=False, indent=4)

            self.logger.info(f"Updated translation_map.json: {translated_path_norm} -> {source_path_norm}")
        except Exception as e:
            self.logger.error(f"Failed to update translation_map.json: {e}")

    @pyqtSlot(str)
    def on_worker_log(self, message):
        self.log_message.emit(message)

    @pyqtSlot()
    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(None, self._t("Select Output Directory"))
        if folder:
            self.update_single_config('app.last_output_path', folder)
            self.output_path_updated.emit(folder)

    @pyqtSlot()
    def open_output_folder(self):
        import subprocess
        import sys
        output_dir = self.config_service.get_config().app.last_output_path
        if not output_dir or not os.path.isdir(output_dir):
            self.logger.warning(f"Output path is not a valid directory: {output_dir}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(os.path.realpath(output_dir))
            elif sys.platform == "darwin":
                subprocess.run(["open", output_dir])
            else:
                subprocess.run(["xdg-open", output_dir])
        except Exception as e:
            self.logger.error(f"Failed to open output folder: {e}")

    def open_font_directory(self):
        import subprocess
        import sys
        # fonts目录在_internal里（打包后）或项目根目录（开发时）
        fonts_dir = os.path.join(self.config_service.root_dir, 'fonts')
        try:
            if not os.path.exists(fonts_dir):
                os.makedirs(fonts_dir)
            if sys.platform == "win32":
                os.startfile(fonts_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", fonts_dir])
            else:
                subprocess.run(["xdg-open", fonts_dir])
        except Exception as e:
            self.logger.error(f"Error opening font directory: {e}")

    def open_dict_directory(self):
        import subprocess
        import sys
        # dict目录在_internal里（打包后）或项目根目录（开发时）
        dict_dir = os.path.join(self.config_service.root_dir, 'dict')
        try:
            if not os.path.exists(dict_dir):
                os.makedirs(dict_dir)
            if sys.platform == "win32":
                os.startfile(dict_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", dict_dir])
            else:
                subprocess.run(["xdg-open", dict_dir])
        except Exception as e:
            self.logger.error(f"Error opening dict directory: {e}")

    def get_hq_prompt_options(self) -> List[str]:
        try:
            # dict目录在_internal里（打包后）或项目根目录（开发时）
            dict_dir = os.path.join(self.config_service.root_dir, 'dict')
            if not os.path.isdir(dict_dir):
                return []
            prompt_files = sorted([
                f for f in os.listdir(dict_dir)
                if f.lower().endswith('.json') and f not in [
                    'system_prompt_hq.json',
                    'system_prompt_line_break.json'
                ]
            ])
            return prompt_files
        except Exception as e:
            self.logger.error(f"Error scanning prompt directory: {e}")
            return []

    @pyqtSlot(str, str)
    def save_env_var(self, key: str, value: str):
        self.config_service.save_env_var(key, value)
        self.logger.info(f"Saved {key} to .env file.")

    # region 配置管理
    def load_config_file(self, config_path: str) -> bool:
        try:
            success = self.config_service.load_config_file(config_path)
            if success:
                config = self.config_service.get_config()
                self.state_manager.set_current_config(config)
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, config_path)
                self.logger.info(self._t("log_config_loaded_successfully", path=config_path))
                self.config_loaded.emit(config.dict())
                if config.app.last_output_path:
                    self.output_path_updated.emit(config.app.last_output_path)
                return True
            else:
                self.logger.error(self._t("log_config_load_failed", path=config_path))
                return False
        except Exception as e:
            self.logger.error(self._t("log_config_load_exception", error=e))
            return False
    
    def save_config_file(self, config_path: str = None) -> bool:
        try:
            success = self.config_service.save_config_file(config_path)
            if success:
                self.logger.info(self._t("log_config_saved_successfully"))
                return True
            return False
        except Exception as e:
            self.logger.error(self._t("log_config_save_exception", error=e))
            return False
    
    def update_config(self, config_updates: Dict[str, Any]) -> bool:
        try:
            self.config_service.update_config(config_updates)
            updated_config = self.config_service.get_config()
            self.state_manager.set_current_config(updated_config)
            self.logger.info(self._t("log_config_updated_successfully"))
            return True
        except Exception as e:
            self.logger.error(self._t("log_config_update_exception", error=e))
            return False

    def update_single_config(self, full_key: str, value: Any):
        self.logger.debug(f"update_single_config: '{full_key}' = '{value}'")
        try:
            config_obj = self.config_service.get_config()
            keys = full_key.split('.')
            parent_obj = config_obj
            for key in keys[:-1]:
                parent_obj = getattr(parent_obj, key)
            setattr(parent_obj, keys[-1], value)
            
            self.config_service.set_config(config_obj)
            self.config_service.save_config_file()
            self.logger.debug(self._t("log_config_saved", config_key=full_key, value=value))

            # 当翻译器设置被更改时，直接更新翻译服务的内部状态
            if full_key == 'translator.translator':
                self.logger.debug(self._t("log_translator_switched", value=value))
                self.translation_service.set_translator(value)

            # 当渲染设置被更改时，通知编辑器刷新
            if full_key.startswith('render.'):
                self.logger.debug(self._t("log_render_setting_changed", config_key=full_key))
                self.render_setting_changed.emit()

        except Exception as e:
            self.logger.error(f"Error saving single config change for {full_key}: {e}")
    # endregion

    # region UI数据提供
    def get_display_mapping(self, key: str) -> Optional[Dict[str, str]]:
        # 每次都动态生成翻译映射，确保语言切换时能正确更新
        display_name_maps = {
            "alignment": {
                "auto": self._t("alignment_auto"),
                "left": self._t("alignment_left"),
                "center": self._t("alignment_center"),
                "right": self._t("alignment_right")
            },
            "direction": {
                "auto": self._t("direction_auto"),
                "h": self._t("direction_horizontal"),
                "v": self._t("direction_vertical")
            },
            "upscaler": {
                "waifu2x": "Waifu2x",
                "esrgan": "ESRGAN",
                "4xultrasharp": "4x UltraSharp",
                "realcugan": "Real-CUGAN"
            },
            "layout_mode": {
                'default': self._t("layout_mode_default"),
                'smart_scaling': self._t("layout_mode_smart_scaling"),
                'strict': self._t("layout_mode_strict"),
                'fixed_font': self._t("layout_mode_fixed_font"),
                'disable_all': self._t("layout_mode_disable_all"),
                'balloon_fill': self._t("layout_mode_balloon_fill")
            },
                "realcugan_model": {
                    "2x-conservative": self._t("realcugan_2x_conservative"),
                    "2x-conservative-pro": self._t("realcugan_2x_conservative_pro"),
                    "2x-no-denoise": self._t("realcugan_2x_no_denoise"),
                    "2x-denoise1x": self._t("realcugan_2x_denoise1x"),
                    "2x-denoise2x": self._t("realcugan_2x_denoise2x"),
                    "2x-denoise3x": self._t("realcugan_2x_denoise3x"),
                    "2x-denoise3x-pro": self._t("realcugan_2x_denoise3x_pro"),
                    "3x-conservative": self._t("realcugan_3x_conservative"),
                    "3x-conservative-pro": self._t("realcugan_3x_conservative_pro"),
                    "3x-no-denoise": self._t("realcugan_3x_no_denoise"),
                    "3x-no-denoise-pro": self._t("realcugan_3x_no_denoise_pro"),
                    "3x-denoise3x": self._t("realcugan_3x_denoise3x"),
                    "3x-denoise3x-pro": self._t("realcugan_3x_denoise3x_pro"),
                    "4x-conservative": self._t("realcugan_4x_conservative"),
                    "4x-no-denoise": self._t("realcugan_4x_no_denoise"),
                    "4x-denoise3x": self._t("realcugan_4x_denoise3x"),
                },
                "translator": {
                    "youdao": self._t("translator_youdao"),
                    "baidu": self._t("translator_baidu"),
                    "deepl": "DeepL",
                    "papago": "Papago",
                    "caiyun": self._t("translator_caiyun"),
                    "openai": "OpenAI",
                    "none": self._t("translator_none"),
                    "original": self._t("translator_original"),
                    "sakura": "Sakura",
                    "groq": "Groq",
                    "gemini": "Google Gemini",
                    "openai_hq": self._t("translator_openai_hq"),
                    "gemini_hq": self._t("translator_gemini_hq"),
                    "offline": self._t("translator_offline"),
                    "nllb": "NLLB",
                    "nllb_big": "NLLB (Big)",
                    "sugoi": "Sugoi",
                    "jparacrawl": "JParaCrawl",
                    "jparacrawl_big": "JParaCrawl (Big)",
                    "m2m100": "M2M100",
                    "m2m100_big": "M2M100 (Big)",
                    "mbart50": "mBART50",
                    "qwen2": "Qwen2",
                    "qwen2_big": "Qwen2 (Big)",
                },
                "target_lang": self.translation_service.get_target_languages(),
                "labels": {
                    "filter_text": self._t("label_filter_text"),
                    "kernel_size": self._t("label_kernel_size"),
                    "mask_dilation_offset": self._t("label_mask_dilation_offset"),
                    "translator": self._t("label_translator"),
                    "target_lang": self._t("label_target_lang"),
                    "no_text_lang_skip": self._t("label_no_text_lang_skip"),
                    "gpt_config": self._t("label_gpt_config"),
                    "high_quality_prompt_path": self._t("label_high_quality_prompt_path"),
                    "use_mocr_merge": self._t("label_use_mocr_merge"),
                    "ocr": self._t("label_ocr"),
                    "use_hybrid_ocr": self._t("label_use_hybrid_ocr"),
                    "secondary_ocr": self._t("label_secondary_ocr"),
                    "min_text_length": self._t("label_min_text_length"),
                    "ignore_bubble": self._t("label_ignore_bubble"),
                    "prob": self._t("label_prob"),
                    "merge_gamma": self._t("label_merge_gamma"),
                    "merge_sigma": self._t("label_merge_sigma"),
                    "merge_edge_ratio_threshold": self._t("label_merge_edge_ratio_threshold"),
                    "detector": self._t("label_detector"),
                    "detection_size": self._t("label_detection_size"),
                    "text_threshold": self._t("label_text_threshold"),
                    "det_rotate": self._t("label_det_rotate"),
                    "det_auto_rotate": self._t("label_det_auto_rotate"),
                    "det_invert": self._t("label_det_invert"),
                    "det_gamma_correct": self._t("label_det_gamma_correct"),
                    "use_yolo_obb": self._t("label_use_yolo_obb"),
                    "yolo_obb_conf": self._t("label_yolo_obb_conf"),
                    "yolo_obb_iou": self._t("label_yolo_obb_iou"),
                    "yolo_obb_overlap_threshold": self._t("label_yolo_obb_overlap_threshold"),
                    "box_threshold": self._t("label_box_threshold"),
                    "unclip_ratio": self._t("label_unclip_ratio"),
                    "min_box_area_ratio": self._t("label_min_box_area_ratio"),
                    "inpainter": self._t("label_inpainter"),
                    "inpainting_size": self._t("label_inpainting_size"),
                    "inpainting_precision": self._t("label_inpainting_precision"),
                    "inpainting_split_ratio": self._t("label_inpainting_split_ratio"),
                    "renderer": self._t("label_renderer"),
                    "alignment": self._t("label_alignment"),
                    "disable_font_border": self._t("label_disable_font_border"),
                    "disable_auto_wrap": self._t("label_disable_auto_wrap"),
                    "font_size_offset": self._t("label_font_size_offset"),
                    "font_size_minimum": self._t("label_font_size_minimum"),
                    "max_font_size": self._t("label_max_font_size"),
                    "font_scale_ratio": self._t("label_font_scale_ratio"),
                    "stroke_width": self._t("label_stroke_width"),
                    "center_text_in_bubble": self._t("label_center_text_in_bubble"),
                    "optimize_line_breaks": self._t("label_optimize_line_breaks"),
                    "check_br_and_retry": self._t("label_check_br_and_retry"),
                    "strict_smart_scaling": self._t("label_strict_smart_scaling"),
                    "direction": self._t("label_direction"),
                    "uppercase": self._t("label_uppercase"),
                    "lowercase": self._t("label_lowercase"),
                    "font_path": self._t("label_font_path"),
                    "no_hyphenation": self._t("label_no_hyphenation"),
                    "font_color": self._t("label_font_color"),
                    "auto_rotate_symbols": self._t("label_auto_rotate_symbols"),
                    "rtl": self._t("label_rtl"),
                    "layout_mode": self._t("label_layout_mode"),
                    "upscaler": self._t("label_upscaler"),
                    "upscale_ratio": self._t("label_upscale_ratio"),
                    "realcugan_model": self._t("label_realcugan_model"),
                    "tile_size": self._t("label_tile_size"),
                    "revert_upscaling": self._t("label_revert_upscaling"),
                    "colorization_size": self._t("label_colorization_size"),
                    "denoise_sigma": self._t("label_denoise_sigma"),
                    "colorizer": self._t("label_colorizer"),
                    "verbose": self._t("label_verbose"),
                    "attempts": self._t("label_attempts"),
                    "max_requests_per_minute": self._t("label_max_requests_per_minute"),
                    "ignore_errors": self._t("label_ignore_errors"),
                    "use_gpu": self._t("label_use_gpu"),
                    "use_gpu_limited": self._t("label_use_gpu_limited"),
                    "context_size": self._t("label_context_size"),
                    "format": self._t("label_format"),
                    "overwrite": self._t("label_overwrite"),
                    "skip_no_text": self._t("label_skip_no_text"),
                    "save_text": self._t("label_save_text"),
                    "load_text": self._t("label_load_text"),
                    "template": self._t("label_template"),
                    "save_quality": self._t("label_save_quality"),
                    "batch_size": self._t("label_batch_size"),
                    "batch_concurrent": self._t("label_batch_concurrent"),
                    "generate_and_export": self._t("label_generate_and_export"),
                    "last_output_path": self._t("label_last_output_path"),
                    "line_spacing": self._t("label_line_spacing"),
                    "font_size": self._t("label_font_size"),
                    "YOUDAO_APP_KEY": self._t("label_YOUDAO_APP_KEY"),
                    "YOUDAO_SECRET_KEY": self._t("label_YOUDAO_SECRET_KEY"),
                    "BAIDU_APP_ID": self._t("label_BAIDU_APP_ID"),
                    "BAIDU_SECRET_KEY": self._t("label_BAIDU_SECRET_KEY"),
                    "DEEPL_AUTH_KEY": self._t("label_DEEPL_AUTH_KEY"),
                    "CAIYUN_TOKEN": self._t("label_CAIYUN_TOKEN"),
                    "OPENAI_API_KEY": self._t("label_OPENAI_API_KEY"),
                    "OPENAI_MODEL": self._t("label_OPENAI_MODEL"),
                    "OPENAI_API_BASE": self._t("label_OPENAI_API_BASE"),
                    # "OPENAI_HTTP_PROXY": self._t("label_OPENAI_HTTP_PROXY"),  # 已废弃，不在UI中显示
                    "OPENAI_GLOSSARY_PATH": self._t("label_OPENAI_GLOSSARY_PATH"),
                    "DEEPSEEK_API_KEY": self._t("label_DEEPSEEK_API_KEY"),
                    "DEEPSEEK_API_BASE": self._t("label_DEEPSEEK_API_BASE"),
                    "DEEPSEEK_MODEL": self._t("label_DEEPSEEK_MODEL"),
                    "GROQ_API_KEY": self._t("label_GROQ_API_KEY"),
                    "GROQ_MODEL": self._t("label_GROQ_MODEL"),
                    "GEMINI_API_KEY": self._t("label_GEMINI_API_KEY"),
                    "GEMINI_MODEL": self._t("label_GEMINI_MODEL"),
                    "GEMINI_API_BASE": self._t("label_GEMINI_API_BASE"),
                    "SAKURA_API_BASE": self._t("label_SAKURA_API_BASE"),
                    "SAKURA_DICT_PATH": self._t("label_SAKURA_DICT_PATH"),
                    "SAKURA_VERSION": self._t("label_SAKURA_VERSION"),
                    "CUSTOM_OPENAI_API_BASE": self._t("label_CUSTOM_OPENAI_API_BASE"),
                    "CUSTOM_OPENAI_MODEL": self._t("label_CUSTOM_OPENAI_MODEL"),
                    "CUSTOM_OPENAI_API_KEY": self._t("label_CUSTOM_OPENAI_API_KEY"),
                    "CUSTOM_OPENAI_MODEL_CONF": self._t("label_CUSTOM_OPENAI_MODEL_CONF")
                }
            }
        return display_name_maps.get(key)

    def get_options_for_key(self, key: str) -> Optional[List[str]]:
        options_map = {
            "format": [self._t("format_not_specified")] + list(OUTPUT_FORMATS.keys()),
            "renderer": [member.value for member in Renderer],
            "alignment": [member.value for member in Alignment],
            "direction": [member.value for member in Direction],
            "upscaler": [member.value for member in Upscaler],
            "upscale_ratio": [self._t("upscale_ratio_not_use"), "2", "3", "4"],
            "realcugan_model": [
                "2x-conservative",
                "2x-conservative-pro",
                "2x-no-denoise",
                "2x-denoise1x",
                "2x-denoise2x",
                "2x-denoise3x",
                "2x-denoise3x-pro",
                "3x-conservative",
                "3x-conservative-pro",
                "3x-no-denoise",
                "3x-no-denoise-pro",
                "3x-denoise3x",
                "3x-denoise3x-pro",
                "4x-conservative",
                "4x-no-denoise",
                "4x-denoise3x",
            ],
            "translator": [member.value for member in Translator],
            "detector": [member.value for member in Detector],
            "colorizer": [member.value for member in Colorizer],
            "inpainter": [member.value for member in Inpainter],
            "inpainting_precision": [member.value for member in InpaintPrecision],
            "ocr": [member.value for member in Ocr],
            "secondary_ocr": [member.value for member in Ocr]
        }
        return options_map.get(key)
    @pyqtSlot()
    def export_config(self):
        """导出配置（排除敏感信息）"""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import json
        
        try:
            # 选择保存位置
            file_path, _ = QFileDialog.getSaveFileName(
                None,
                self._t("Export Config"),
                "manga_translator_config.json",
                "JSON Files (*.json)"
            )
            
            if not file_path:
                return
            
            # 获取当前配置
            config = self.config_service.get_config()
            config_dict = config.dict()
            
            # 排除敏感信息和临时状态
            # 1. 排除 app 配置（包含路径等临时信息）
            if 'app' in config_dict:
                del config_dict['app']
            
            # 2. 排除 CLI 中的临时状态
            if 'cli' in config_dict:
                # 保留 CLI 配置，但排除某些临时字段
                cli_exclude = ['verbose']  # 可以根据需要添加更多
                for key in cli_exclude:
                    if key in config_dict['cli']:
                        del config_dict['cli'][key]
            
            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            
            self.logger.info(self._t("log_config_exported", path=file_path))
            QMessageBox.information(
                None,
                self._t("Export Success"),
                self._t("Config exported successfully to:\n{path}\n\nNote: Sensitive information like API keys are not included.", path=file_path)
            )
            
        except Exception as e:
            self.logger.error(self._t("log_config_export_failed", error=e))
            QMessageBox.critical(
                None,
                self._t("Export Failed"),
                self._t("Error occurred while exporting config:\n{error}", error=str(e))
            )
    
    @pyqtSlot()
    def import_config(self):
        """导入配置（保留现有的敏感信息）"""
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        import json
        
        try:
            # 选择要导入的文件
            file_path, _ = QFileDialog.getOpenFileName(
                None,
                self._t("Import Config"),
                "",
                "JSON Files (*.json)"
            )
            
            if not file_path:
                return
            
            # 读取导入的配置
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_config = json.load(f)
            
            # 获取当前配置
            current_config = self.config_service.get_config()
            current_dict = current_config.dict()
            
            # 保留当前的 app 配置（路径等临时信息）
            preserved_app = current_dict.get('app', {})
            
            # 深度合并配置
            def deep_update(target, source):
                for key, value in source.items():
                    if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                        deep_update(target[key], value)
                    else:
                        target[key] = value
            
            # 合并导入的配置到当前配置
            deep_update(current_dict, imported_config)
            
            # 恢复 app 配置
            current_dict['app'] = preserved_app
            
            # 更新配置
            from core.config_models import AppSettings
            new_config = AppSettings.parse_obj(current_dict)
            self.config_service.set_config(new_config)
            self.config_service.save_config_file()
            
            # 通知UI更新 - 使用转换后的配置字典
            config_dict_for_ui = self.config_service._convert_config_for_ui(new_config.dict())
            self.config_loaded.emit(config_dict_for_ui)
            
            self.logger.info(self._t("log_config_imported", path=file_path))
            QMessageBox.information(
                None,
                self._t("Import Success"),
                self._t("Config imported successfully!\n\nSource: {path}\n\nNote: Your API keys and sensitive information have been preserved.", path=file_path)
            )
            
        except Exception as e:
            self.logger.error(self._t("log_config_import_failed", error=e))
            QMessageBox.critical(
                None,
                self._t("Import Failed"),
                self._t("Error occurred while importing config:\n{error}\n\nPlease ensure the file format is correct.", error=str(e))
            )
    # endregion

    # region 文件管理
    def add_files(self, file_paths: List[str]):
        """
        Adds files/folders to the list for processing.
        """
        new_paths = []
        for path in file_paths:
            norm_path = os.path.normpath(path)
            if norm_path not in self.source_files:
                new_paths.append(norm_path)

        if new_paths:
            self.source_files.extend(new_paths)
            self.logger.info(f"Added {len(new_paths)} files/folders to the list.")
            self.files_added.emit(new_paths)

    def get_last_open_dir(self) -> str:
        path = self.config_service.get_config().app.last_open_dir
        self.logger.info(f"Retrieved last open directory: {path}")
        return path

    def set_last_open_dir(self, path: str):
        self.logger.info(f"Saving last open directory: {path}")
        self.update_single_config('app.last_open_dir', path)

    def add_folder(self):
        """Opens a dialog to select folders (supports multiple selection) and adds their paths to the list."""
        last_dir = self.get_last_open_dir()

        # 使用自定义的现代化文件夹选择器
        from widgets.folder_dialog import select_folders

        folders = select_folders(
            parent=None,
            start_dir=last_dir,
            multi_select=True,
            config_service=self.config_service
        )

        if folders:
            self.set_last_open_dir(folders[0])  # 保存第一个文件夹的路径
            self.add_files(folders)
    
    def add_folders(self):
        """Alias for add_folder for backward compatibility."""
        self.add_folder()

    def remove_file(self, file_path: str):
        try:
            norm_file_path = os.path.normpath(file_path)
            
            # 情况1：直接在 source_files 中（文件夹或单独添加的文件）
            if norm_file_path in self.source_files:
                self.source_files.remove(norm_file_path)
                # 如果是文件，清理 file_to_folder_map
                if norm_file_path in self.file_to_folder_map:
                    del self.file_to_folder_map[norm_file_path]
                
                # 如果是文件夹，清理排除列表中该文件夹下的所有子文件夹
                if os.path.isdir(norm_file_path):
                    excluded_to_remove = set()
                    for excluded_folder in self.excluded_subfolders:
                        try:
                            # 检查 excluded_folder 是否在被删除的文件夹内
                            common = os.path.commonpath([norm_file_path, excluded_folder])
                            if common == norm_file_path:
                                excluded_to_remove.add(excluded_folder)
                        except ValueError:
                            continue
                    self.excluded_subfolders -= excluded_to_remove
                
                self.file_removed.emit(file_path)
                return
            
            # 情况2：文件夹路径（可能是顶层文件夹或子文件夹）
            if os.path.isdir(norm_file_path):
                # 检查是否是某个顶层文件夹的子文件夹
                parent_folder = None
                for folder in self.source_files:
                    if os.path.isdir(folder):
                        try:
                            # 检查 norm_file_path 是否是 folder 的子文件夹
                            common = os.path.commonpath([folder, norm_file_path])
                            if common == os.path.normpath(folder) and norm_file_path != os.path.normpath(folder):
                                parent_folder = folder
                                break
                        except ValueError:
                            continue
                
                if parent_folder:
                    # 这是子文件夹，添加到排除列表
                    self.excluded_subfolders.add(norm_file_path)
                    # 发射删除信号让 FileListView 处理
                    # FileListView 会自动更新树形结构和文件数量
                    self.file_removed.emit(file_path)
                    return
                
                # 不是子文件夹，可能是通过单独添加文件自动分组的文件夹
                # 删除该文件夹下的所有文件
                files_to_remove = []
                for source_file in self.source_files:
                    if os.path.isfile(source_file):
                        try:
                            # 检查文件是否在这个文件夹内
                            common = os.path.commonpath([norm_file_path, source_file])
                            if common == norm_file_path:
                                files_to_remove.append(source_file)
                        except ValueError:
                            # 不同驱动器，跳过
                            continue
                
                # 移除所有找到的文件
                for f in files_to_remove:
                    self.source_files.remove(f)
                    # 同时清理 file_to_folder_map
                    if f in self.file_to_folder_map:
                        del self.file_to_folder_map[f]
                
                if files_to_remove:
                    self.file_removed.emit(file_path)
                    return
            
            # 情况3：文件夹内的单个文件（只处理文件，不处理文件夹）
            if os.path.isfile(norm_file_path):
                # 检查这个文件是否来自某个文件夹
                parent_folder = None
                for folder in self.source_files:
                    if os.path.isdir(folder):
                        # 检查文件是否在这个文件夹内
                        try:
                            common = os.path.commonpath([folder, norm_file_path])
                            # 确保文件在文件夹内，而不是文件夹本身
                            if common == os.path.normpath(folder) and norm_file_path != os.path.normpath(folder):
                                parent_folder = folder
                                break
                        except ValueError:
                            # 不同驱动器，跳过
                            continue
                
                if parent_folder:
                    # 这是文件夹内的文件，需要将其添加到排除列表
                    # 由于当前架构不支持排除单个文件，我们需要：
                    # 1. 移除整个文件夹
                    # 2. 添加文件夹内的其他文件
                    
                    # 获取文件夹内的所有图片文件
                    folder_files = self.file_service.get_image_files_from_folder(parent_folder, recursive=True)
                    
                    # 移除要删除的文件
                    remaining_files = [f for f in folder_files if os.path.normpath(f) != norm_file_path]
                    
                    # 从 source_files 中移除文件夹
                    self.source_files.remove(parent_folder)
                    
                    # 如果还有剩余文件，将它们作为单独的文件添加回去
                    if remaining_files:
                        self.source_files.extend(remaining_files)
                        # 更新 file_to_folder_map：这些文件现在仍然属于原文件夹
                        # 保持文件夹映射关系，以便输出路径计算正确
                        for f in remaining_files:
                            self.file_to_folder_map[f] = parent_folder
                    
                    self.file_removed.emit(file_path)
                    return
            
            # 如果到这里还没有处理，说明路径不存在
            self.logger.warning(f"Path not found in list for removal: {file_path}")
        except Exception as e:
            self.logger.error(f"移除路径时发生异常: {e}")

    def clear_file_list(self):
        if not self.source_files:
            return
        # TODO: Add confirmation dialog
        self.source_files.clear()
        self.file_to_folder_map.clear()  # 清空文件夹映射
        self.excluded_subfolders.clear()  # 清空排除列表
        self.files_cleared.emit()
        self.logger.info("File list cleared by user.")
    # endregion

    # region 核心任务逻辑
    def get_folder_tree_structure(self) -> dict:
        """
        获取完整的文件夹树结构
        返回: {
            'files': [所有文件列表],
            'tree': {
                'folder_path': {
                    'files': [该文件夹直接包含的文件],
                    'subfolders': [子文件夹路径列表]
                }
            }
        }
        """
        tree = {}
        all_files = []
        
        # 处理每个顶层文件夹
        for source_path in self.source_files:
            if os.path.isdir(source_path):
                norm_folder = os.path.normpath(source_path)
                # 递归构建该文件夹的树结构
                folder_files = self._build_folder_tree(norm_folder, tree)
                all_files.extend(folder_files)
            elif os.path.isfile(source_path):
                # 单独添加的文件
                all_files.append(source_path)
        
        return {
            'files': all_files,
            'tree': tree
        }
    
    def _build_folder_tree(self, folder_path: str, tree: dict) -> List[str]:
        """
        递归构建文件夹树结构
        返回该文件夹及其子文件夹中的所有文件列表
        """
        # 检查是否被排除
        if folder_path in self.excluded_subfolders:
            return []
        
        norm_folder = os.path.normpath(folder_path)
        
        # 初始化该文件夹的树节点
        if norm_folder not in tree:
            tree[norm_folder] = {
                'files': [],
                'subfolders': []
            }
        
        all_files = []
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
        
        try:
            items = os.listdir(folder_path)
            subdirs = []
            files = []
            
            for item in items:
                if item == 'manga_translator_work':
                    continue
                
                item_path = os.path.join(folder_path, item)
                norm_item_path = os.path.normpath(item_path)
                
                if os.path.isdir(item_path):
                    # 检查是否被排除
                    if norm_item_path not in self.excluded_subfolders:
                        subdirs.append(norm_item_path)
                        tree[norm_folder]['subfolders'].append(norm_item_path)
                elif os.path.splitext(item)[1].lower() in image_extensions:
                    files.append(norm_item_path)
            
            # 排序
            subdirs.sort(key=self.file_service._natural_sort_key)
            files.sort(key=self.file_service._natural_sort_key)
            
            # 添加该文件夹直接包含的文件
            tree[norm_folder]['files'] = files
            all_files.extend(files)
            
            # 递归处理子文件夹
            for subdir in subdirs:
                subdir_files = self._build_folder_tree(subdir, tree)
                all_files.extend(subdir_files)
        
        except Exception as e:
            self.logger.error(f"Error building tree for folder {folder_path}: {e}")
        
        return all_files
    
    def _resolve_input_files(self) -> List[str]:
        """
        Expands folders in self.source_files into a list of image files.
        同时记录文件和文件夹的映射关系。
        按文件夹分组排序：先对文件夹进行排序，然后对每个文件夹内的图片排序。
        """
        resolved_files = []
        # 保存旧的映射，用于处理删除文件后的情况
        old_map = self.file_to_folder_map.copy()
        self.file_to_folder_map.clear()

        # 分离文件和文件夹
        folders = []
        individual_files = []
        
        for path in self.source_files:
            if os.path.isdir(path):
                folders.append(path)
            elif os.path.isfile(path):
                if self.file_service.validate_image_file(path):
                    individual_files.append(path)
        
        # 清理排除列表：移除不再属于任何 source_files 文件夹的排除项
        if self.excluded_subfolders:
            excluded_to_remove = set()
            for excluded_folder in self.excluded_subfolders:
                # 检查这个排除的文件夹是否还在某个 source_files 的文件夹内
                is_valid = False
                for folder in folders:
                    try:
                        common = os.path.commonpath([folder, excluded_folder])
                        if common == os.path.normpath(folder):
                            is_valid = True
                            break
                    except ValueError:
                        continue
                if not is_valid:
                    excluded_to_remove.add(excluded_folder)
            self.excluded_subfolders -= excluded_to_remove
        
        # 对文件夹进行自然排序
        folders.sort(key=self.file_service._natural_sort_key)
        
        # 按文件夹分组处理
        for folder in folders:
            # 获取文件夹中的所有图片（递归查找所有子文件夹，已经使用自然排序）
            folder_files = self.file_service.get_image_files_from_folder(folder, recursive=True)
            
            # 过滤掉被排除的子文件夹中的文件
            if self.excluded_subfolders:
                filtered_files = []
                for file_path in folder_files:
                    # 检查文件是否在被排除的子文件夹中
                    is_excluded = False
                    for excluded_folder in self.excluded_subfolders:
                        try:
                            common = os.path.commonpath([excluded_folder, file_path])
                            if common == excluded_folder:
                                is_excluded = True
                                break
                        except ValueError:
                            continue
                    if not is_excluded:
                        filtered_files.append(file_path)
                folder_files = filtered_files
            
            resolved_files.extend(folder_files)
            # 记录这些文件来自这个文件夹
            for file_path in folder_files:
                self.file_to_folder_map[file_path] = folder
        
        # 处理单独添加的文件（使用自然排序）
        individual_files.sort(key=self.file_service._natural_sort_key)
        for file_path in individual_files:
            resolved_files.append(file_path)
            # 检查是否有旧的文件夹映射（删除文件后剩余的文件）
            if file_path in old_map and old_map[file_path] is not None:
                # 保留原来的文件夹映射
                self.file_to_folder_map[file_path] = old_map[file_path]
            else:
                # 真正单独添加的文件，不属于任何文件夹
                self.file_to_folder_map[file_path] = None

        return list(dict.fromkeys(resolved_files)) # Return unique files

    def start_backend_task(self):
        """
        Resolves input paths and uses a 'Worker-to-Thread' model to start the translation task.
        """
        # 通过调用配置服务的 reload_config 方法，强制全面重新加载所有配置
        try:
            self.logger.info("即将开始后台任务，强制重新加载所有配置...")
            self.config_service.reload_config()
            self.logger.info("配置已刷新，继续执行任务。")
        except Exception as e:
            self.logger.error(f"重新加载配置时发生严重错误: {e}")
            # 根据需要，这里可以决定是否要中止任务
            # from PyQt6.QtWidgets import QMessageBox
            # QMessageBox.critical(None, "配置错误", f"无法加载最新配置: {e}")
            # return

        # 检查是否有任务在运行（基于状态而不是线程）
        if self.state_manager.is_translating():
            self.logger.warning("一个任务已经在运行中。")
            return
        
        # 如果有旧线程还在运行，等待它结束（不使用 terminate）
        if self.thread is not None and self.thread.isRunning():
            self.logger.warning("检测到旧线程还在运行，正在请求停止...")
            self.state_manager.set_status_message("正在停止旧任务...")
            
            # 通知 worker 停止
            if self.worker:
                try:
                    self.worker.stop()
                except Exception as e:
                    self.logger.warning(f"停止worker时出错: {e}")
            
            # 请求线程退出
            self.thread.quit()
            
            # 等待最多5秒（给渲染任务足够的时间完成）
            wait_time = 5000  # 5秒
            if not self.thread.wait(wait_time):
                self.logger.error(f"旧线程在{wait_time}ms内未停止，强制终止")
                # 最后手段：强制终止（可能导致资源泄漏，但比线程冲突好）
                self.thread.terminate()
                self.thread.wait()  # 等待终止完成
                self.logger.warning("旧线程已被强制终止")
            else:
                self.logger.info("旧线程已正常停止")
            
            # 清理引用
            self.thread = None
            self.worker = None
            
            # 重置状态
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("就绪")

        # 检查文件列表是否为空
        files_to_process = self._resolve_input_files()
        if not files_to_process:
            self.logger.warning("没有找到有效的图片文件，任务中止")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                self._t("File List Empty"),
                self._t("Please add image files to translate!\n\nYou can add files by:\n• Click 'Add Files' button\n• Click 'Add Folder' button\n• Drag and drop files to the list")
            )
            return

        # 检查输出目录是否合法
        output_path = self.config_service.get_config().app.last_output_path
        if not output_path or not os.path.isdir(output_path):
            self.logger.warning(f"输出目录不合法: {output_path}")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                self._t("Invalid Output Directory"),
                self._t("Please set a valid output directory!\n\nYou can set it by:\n• Click 'Browse...' button to select directory\n• Enter path directly in the output directory field")
            )
            return

        self.saved_files_count = 0
        self.saved_files_list = []  # 重置保存文件列表
        self.thread = QThread()
        self.worker = TranslationWorker(
            files=files_to_process,
            config_dict=self.config_service.get_config().dict(),
            output_folder=self.config_service.get_config().app.last_output_path,
            root_dir=self.config_service.root_dir,
            file_to_folder_map=self.file_to_folder_map.copy()  # 传递文件到文件夹的映射
        )
        
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.process)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.error.connect(self.on_task_error)
        self.worker.progress.connect(self.on_task_progress)
        self.worker.log_received.connect(self.on_worker_log)
        self.worker.file_processed.connect(self.on_file_completed)

        self.thread.start()
        self.logger.info("翻译工作线程已启动。")
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在翻译...")
        
        # 开始任务统计跟踪
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.start_task_tracking(len(files_to_process))

    def on_task_finished(self, results):
        """处理任务完成信号，并根据需要保存批量任务的结果"""
        saved_files = []
        # The `results` list will only contain items from a batch job now.
        # Sequential jobs handle saving in `on_file_completed`.
        if results:
            self.logger.info(f"批量翻译任务完成，收到 {len(results)} 个结果。正在保存...")
            try:
                config = self.config_service.get_config()
                output_format = config.cli.format
                save_quality = config.cli.save_quality
                output_folder = config.app.last_output_path

                if not output_folder:
                    self.logger.error("输出目录未设置，无法保存文件。")
                    self.state_manager.set_status_message("错误：输出目录未设置！")
                else:
                    for result in results:
                        if result.get('success'):
                            # In batch mode, image_data is None because the backend already saved the file.
                            # We just need to acknowledge it.
                            if result.get('image_data') is None:
                                # 构造翻译后的图片路径
                                original_path = result.get('original_path')
                                source_folder = self.file_to_folder_map.get(original_path)

                                if source_folder:
                                    # 文件来自文件夹
                                    folder_name = os.path.basename(source_folder)
                                    final_output_folder = os.path.join(output_folder, folder_name)
                                    translated_file = os.path.join(final_output_folder, os.path.basename(original_path))
                                else:
                                    # 单独添加的文件
                                    translated_file = os.path.join(output_folder, os.path.basename(original_path))

                                # 规范化路径，避免混合斜杠
                                translated_file = os.path.normpath(translated_file)
                                saved_files.append(translated_file)
                                self.logger.info(f"确认由后端批量保存的文件: {original_path}")
                            else:
                                # This handles cases where a result with image_data is present in a batch
                                try:
                                    base_filename = os.path.splitext(os.path.basename(result['original_path']))[0]
                                    file_extension = f".{output_format}" if output_format and output_format != "不指定" else ".png"
                                    output_filename = f"{base_filename}_translated{file_extension}"
                                    final_output_path = os.path.join(output_folder, output_filename)
                                    os.makedirs(output_folder, exist_ok=True)
                                    
                                    save_kwargs = {}
                                    image_to_save = result['image_data']

                                    # Convert RGBA to RGB for JPEG format
                                    if file_extension in ['.jpg', '.jpeg']:
                                        if image_to_save.mode == 'RGBA':
                                            image_to_save = image_to_save.convert('RGB')
                                        save_kwargs['quality'] = save_quality
                                    elif file_extension == '.webp':
                                        save_kwargs['quality'] = save_quality

                                    image_to_save.save(final_output_path, **save_kwargs)
                                    saved_files.append(final_output_path)
                                    self.logger.info(f"成功保存文件: {final_output_path}")
                                except Exception as e:
                                    self.logger.error(f"保存文件 {result['original_path']} 时出错: {e}")
                
                # In batch mode, the saved_files_count is the length of this list
                self.saved_files_count = len(saved_files)

            except Exception as e:
                self.logger.error(f"处理批量任务结果时发生严重错误: {e}")

        # This part runs for both sequential and batch modes
        self.logger.info(f"翻译任务完成。总共成功处理 {self.saved_files_count} 个文件。")
        
        # 对于顺序处理模式，使用累积的 saved_files_list
        if not saved_files and self.saved_files_list:
            saved_files = self.saved_files_list.copy()
        
        try:
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message(f"任务完成，成功处理 {self.saved_files_count} 个文件。")
            
            # 停止任务统计跟踪并重置进度条
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.stop_task_tracking()
                self.main_view.reset_progress()
            
            # 播放系统提示音
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.beep()
                self.logger.info("播放系统提示音")
            except Exception as sound_error:
                self.logger.warning(f"播放提示音失败: {sound_error}")
            
            self.task_completed.emit(saved_files)
        except Exception as e:
            self.logger.error(f"完成任务状态更新或信号发射时发生致命错误: {e}", exc_info=True)
        finally:
            # 清理线程引用（线程应该已经通过deleteLater自动清理）
            # 只在线程仍在运行时进行额外处理
            if self.thread and self.thread.isRunning():
                self.logger.warning("任务完成但线程仍在运行，请求退出...")
                self.thread.quit()
                # 不阻塞UI，让deleteLater处理清理
                # 如果线程在2秒内没有停止，记录警告但不强制终止
                if not self.thread.wait(2000):
                    self.logger.warning("线程未在2秒内停止，将由Qt事件循环自动清理")
            
            # 清理引用，让Qt的deleteLater机制处理实际的对象销毁
            self.thread = None
            self.worker = None

    def on_task_error(self, error_message):
        self.logger.error(f"翻译任务发生错误: {error_message}")
        
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message(f"任务失败: {error_message}")
        
        # 停止任务统计跟踪并重置进度条
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.stop_task_tracking()
            self.main_view.reset_progress()
        
        # 清理线程
        if self.thread and self.thread.isRunning():
            self.logger.warning("错误发生但线程仍在运行，请求退出...")
            self.thread.quit()
            if not self.thread.wait(2000):
                self.logger.warning("线程未在2秒内停止，将由Qt事件循环自动清理")
        
        # 清理引用
        self.thread = None
        self.worker = None

    def on_task_progress(self, current, total, message):
        self.logger.info(f"[进度] {current}/{total}: {message}")
        percentage = (current / total) * 100 if total > 0 else 0
        self.state_manager.set_translation_progress(percentage)
        self.state_manager.set_status_message(f"[{current}/{total}] {message}")
        
        # 更新主视图的进度条
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.update_progress(current, total, message)

    def stop_task(self) -> bool:
        """停止翻译任务（优雅停止，不使用 terminate）"""
        if self.thread and self.thread.isRunning():
            self.logger.info("正在请求停止翻译线程...")

            # 立即更新UI状态：设置为非翻译状态
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("正在停止...")

            # 1. 通知 worker 停止（设置标志）
            if self.worker:
                try:
                    self.worker.stop()
                except:
                    pass

            # 2. 请求线程退出事件循环
            self.thread.quit()
            
            # 3. 连接 finished 信号以清理资源
            def on_thread_finished():
                self.logger.info("翻译线程已正常停止")
                self.state_manager.set_status_message("任务已停止")
                # 停止任务统计跟踪并重置进度条
                if hasattr(self, 'main_view') and self.main_view:
                    self.main_view.stop_task_tracking()
                    self.main_view.reset_progress()
                self.thread = None
                self.worker = None
            
            try:
                self.thread.finished.disconnect()
            except:
                pass
            self.thread.finished.connect(on_thread_finished)

            return True
        
        self.logger.warning("请求停止任务，但没有正在运行的线程。")
        self.state_manager.set_translating(False)
        return False
        return False
    # endregion

    # region 应用生命周期
    def initialize(self) -> bool:
        try:
            # The config is already loaded at startup. We just need to ensure the UI
            # reflects the loaded state without triggering a full, blocking rebuild.
            
            # Get the already loaded config
            config = self.config_service.get_config()

            # Manually emit the signal to populate UI options
            self.config_loaded.emit(config.dict())

            # Manually emit the signal to update the output path display in the UI
            if config.app.last_output_path:
                self.output_path_updated.emit(config.app.last_output_path)
            
            # Ensure the config path is stored in the state manager
            default_config_path = self.config_service.get_default_config_path()
            if os.path.exists(default_config_path):
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, default_config_path)

            self.state_manager.set_app_ready(True)
            self.state_manager.set_status_message("就绪")
            self.logger.info("应用初始化完成")
            return True
        except Exception as e:
            self.logger.error(f"应用初始化异常: {e}")
            return False
    
    def shutdown(self):
        """应用关闭时的清理"""
        try:
            if self.state_manager.is_translating() or (self.thread and self.thread.isRunning()):
                self.logger.info("应用关闭中，停止翻译任务...")
                
                # 通知 worker 停止
                if self.worker:
                    try:
                        self.worker.stop()
                    except Exception as e:
                        self.logger.warning(f"停止worker时出错: {e}")
                
                # 请求线程退出并等待（最多3秒）
                if self.thread and self.thread.isRunning():
                    self.thread.quit()
                    if not self.thread.wait(3000):
                        self.logger.warning("线程3秒内未停止，强制终止")
                        self.thread.terminate()
                        self.thread.wait()
                    else:
                        self.logger.info("翻译线程已正常停止")
                
                self.thread = None
                self.worker = None
                self.state_manager.set_translating(False)
            
            if self.translation_service:
                pass
        except Exception as e:
            self.logger.error(f"应用关闭异常: {e}")
    # endregion

class QtLogHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal

    def emit(self, record):
        msg = self.format(record)
        self.signal.emit(msg)

class TranslationWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    log_received = pyqtSignal(str)
    file_processed = pyqtSignal(dict)

    def __init__(self, files, config_dict, output_folder, root_dir, file_to_folder_map=None):
        super().__init__()
        self.files = files
        self.config_dict = config_dict
        self.output_folder = output_folder
        self.root_dir = root_dir
        self.file_to_folder_map = file_to_folder_map or {}  # 文件到文件夹的映射
        self._is_running = True
        self._current_task = None  # 保存当前运行的异步任务
        self.i18n = get_i18n_manager()
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def stop(self):
        self.log_received.emit("--- Stop request received.")
        self._is_running = False
        # 取消当前运行的异步任务
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        # 添加GPU显存清理（自动清理模式）
        self.log_received.emit("--- [CLEANUP] Cleaning up GPU memory...")
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                self.log_received.emit("--- [CLEANUP] GPU memory cleared")
            else:
                self.log_received.emit("--- [CLEANUP] GPU not available, skipped GPU cleanup")
        except Exception as e:
            self.log_received.emit(f"--- [CLEANUP] Warning: Failed to cleanup GPU: {e}")

    def _build_friendly_error_message(self, error_message: str, error_traceback: str) -> str:
        """
        根据错误信息构建友好的中文错误提示
        """
        friendly_msg = "\n" + "="*80 + "\n"
        friendly_msg += "❌ 翻译任务失败\n"
        friendly_msg += "="*80 + "\n\n"
        
        # 检查是否是AI断句检查失败
        if ("BR markers missing" in error_message or 
            "AI断句检查" in error_message or 
            "BRMarkersValidationException" in error_traceback or
            "_validate_br_markers" in error_traceback):
            friendly_msg += "🔍 错误原因：AI断句检查失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI翻译时未能正确添加断句标记 [BR]，导致多次重试后仍然失败。\n\n"
            friendly_msg += "💡 解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 关闭「AI断句检查」选项（推荐）\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句检查\n"
            friendly_msg += "      - 说明：允许AI在少数情况下不添加断句标记\n\n"
            friendly_msg += "   2. 增加「重试次数」\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高（-1 表示无限重试）\n\n"
            friendly_msg += "   3. 更换翻译模型\n"
            friendly_msg += "      - 某些模型对断句标记的理解更好\n"
            friendly_msg += "      - 建议：尝试 gpt-4o 或 gemini-2.0-flash-exp\n\n"
            friendly_msg += "   4. 关闭「AI断句」功能\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句\n"
            friendly_msg += "      - 说明：使用传统的自动换行（可能导致排版不够精确）\n\n"
        
        # 检查是否是模型不支持多模态
        elif "不支持多模态" in error_message or "multimodal" in error_message.lower() or "vision" in error_message.lower():
            friendly_msg += "🔍 错误原因：模型不支持多模态输入\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   当前使用的是「高质量翻译器」（openai_hq 或 gemini_hq），\n"
            friendly_msg += "   这些翻译器需要发送图片给AI进行分析，但当前模型不支持图片输入。\n\n"
            friendly_msg += "💡 解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 更换为支持多模态的模型（推荐）\n"
            friendly_msg += "      - OpenAI: gpt-4o, gpt-4-turbo, gpt-4-vision-preview\n"
            friendly_msg += "      - Gemini: gemini-2.0-flash-exp, gemini-1.5-pro, gemini-1.5-flash\n"
            friendly_msg += "      - 注意：DeepSeek模型不支持多模态\n\n"
            friendly_msg += "   2. 切换到普通翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 将 openai_hq 改为 openai\n"
            friendly_msg += "      - 将 gemini_hq 改为 gemini\n"
            friendly_msg += "      - 说明：普通翻译器不需要发送图片，只翻译文本\n\n"
        
        # 检查是否是404错误（API地址或模型配置错误）
        elif "API_404_ERROR" in error_message or "404" in error_message or "HTML错误页面" in error_message:
            friendly_msg += "🔍 错误原因：API返回404错误\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API返回了HTML格式的404错误页面，而不是正常的JSON响应。\n"
            friendly_msg += "   这通常意味着API地址错误或模型名称不存在。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 检查API地址配置（最常见）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_API_BASE\n"
            friendly_msg += "      - 正确格式：https://api.openai.com/v1\n"
            friendly_msg += "      - 注意：地址末尾必须是 /v1，不要多加或少加路径\n\n"
            friendly_msg += "   2. 检查模型名称是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_MODEL\n"
            friendly_msg += "      - OpenAI支持的模型：gpt-4o, gpt-4-turbo, gpt-4, gpt-3.5-turbo\n"
            friendly_msg += "      - 注意：模型名称区分大小写，必须完全匹配\n\n"
            friendly_msg += "   3. 如果使用自定义API（如中转API）\n"
            friendly_msg += "      - 确认中转服务的API地址格式\n"
            friendly_msg += "      - 确认中转服务支持你使用的模型\n"
            friendly_msg += "      - 联系中转服务提供商确认配置\n\n"
        
        # 检查是否是API密钥错误
        elif "api key" in error_message.lower() or "authentication" in error_message.lower() or "unauthorized" in error_message.lower() or "401" in error_message:
            friendly_msg += "🔍 错误原因：API密钥验证失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API密钥无效、过期或未正确配置。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查API密钥是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量配置区域\n"
            friendly_msg += "      - 确认密钥没有多余的空格或换行\n\n"
            friendly_msg += "   2. 验证API密钥是否有效\n"
            friendly_msg += "      - OpenAI: https://platform.openai.com/api-keys\n"
            friendly_msg += "      - Gemini: https://aistudio.google.com/app/apikey\n\n"
            friendly_msg += "   3. 检查API额度是否用完\n"
            friendly_msg += "      - 登录对应平台查看余额和使用情况\n\n"
        
        # 检查是否是网络连接错误
        elif "connection" in error_message.lower() or "timeout" in error_message.lower() or "network" in error_message.lower():
            friendly_msg += "🔍 错误原因：网络连接失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   无法连接到API服务器，可能是网络问题或需要代理。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查网络连接\n"
            friendly_msg += "      - 确认电脑可以正常访问互联网\n\n"
            friendly_msg += "   2. 检查API地址是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - 默认值：https://api.openai.com/v1\n\n"
        
        # 检查是否是速率限制错误
        elif "rate limit" in error_message.lower() or "429" in error_message or "too many requests" in error_message.lower():
            friendly_msg += "🔍 错误原因：API请求速率限制 (HTTP 429)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   请求过于频繁，超过了API的速率限制。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 设置每分钟最大请求数（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 每分钟最大请求数\n"
            friendly_msg += "      - 建议：设置为 3-10（取决于API套餐）\n\n"
            friendly_msg += "   2. 稍后重试\n"
            friendly_msg += "      - 等待几分钟后再次尝试翻译\n\n"
            friendly_msg += "   3. 升级API套餐\n"
            friendly_msg += "      - 联系API提供商升级到更高的速率限制\n\n"
        
        # 检查是否是403禁止访问错误
        elif "403" in error_message or "forbidden" in error_message.lower():
            friendly_msg += "🔍 错误原因：访问被拒绝 (HTTP 403)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   服务器拒绝访问，可能是权限不足或地区限制。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查API密钥权限\n"
            friendly_msg += "      - 确认API密钥有访问该服务的权限\n\n"
            friendly_msg += "   2. 检查账户状态\n"
            friendly_msg += "      - 确认账户未被封禁或限制\n\n"

        
        # 检查是否是404未找到错误
        elif "404" in error_message or "not found" in error_message.lower():
            friendly_msg += "🔍 错误原因：资源未找到 (HTTP 404)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   请求的API端点不存在或模型名称错误。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 检查API地址是否正确（推荐）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - OpenAI默认：https://api.openai.com/v1\n"
            friendly_msg += "      - Gemini默认：https://generativelanguage.googleapis.com\n\n"
            friendly_msg += "   2. 检查模型名称\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → MODEL\n"
            friendly_msg += "      - 确认模型名称拼写正确（如 gpt-4o 不是 gpt4o）\n\n"
            friendly_msg += "   3. 验证模型可用性\n"
            friendly_msg += "      - 某些模型可能已下线或更名\n"
            friendly_msg += "      - 访问官方文档查看可用模型列表\n\n"
        
        # 检查是否是500服务器错误
        elif "500" in error_message or "internal server error" in error_message.lower():
            friendly_msg += "🔍 错误原因：服务器内部错误 (HTTP 500)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API服务器遇到内部错误，这通常是临时问题。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 增加重试次数（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n"
            friendly_msg += "      - 服务器错误通常是临时的，重试可能成功\n\n"
            friendly_msg += "   2. 稍后重试\n"
            friendly_msg += "      - 等待几分钟，让服务器恢复正常\n\n"
            friendly_msg += "   3. 检查API服务状态\n"
            friendly_msg += "      - OpenAI: https://status.openai.com/\n"
            friendly_msg += "      - 查看是否有大规模服务中断\n\n"
        
        # 检查是否是502/503/504网关错误
        elif any(code in error_message for code in ["502", "503", "504"]) or "bad gateway" in error_message.lower() or "service unavailable" in error_message.lower() or "gateway timeout" in error_message.lower():
            error_code = "502/503/504"
            if "502" in error_message:
                error_code = "502"
            elif "503" in error_message:
                error_code = "503"
            elif "504" in error_message:
                error_code = "504"
            
            friendly_msg += f"🔍 错误原因：网关/服务不可用 (HTTP {error_code})\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   - 502: 网关接收到无效响应\n"
            friendly_msg += "   - 503: 服务暂时不可用（通常是维护或过载）\n"
            friendly_msg += "   - 504: 网关超时\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 等待后重试（推荐）\n"
            friendly_msg += "      - 这些错误通常是临时的\n"
            friendly_msg += "      - 等待5-10分钟后重新翻译\n\n"
            friendly_msg += "   2. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n\n"
            friendly_msg += "   3. 检查API服务状态\n"
            friendly_msg += "      - 访问API提供商的状态页面\n"
            friendly_msg += "      - OpenAI: https://status.openai.com/\n\n"
            friendly_msg += "   4. 更换API地址\n"
            friendly_msg += "      - 如果使用第三方API中转，尝试更换地址\n\n"
        
        # 检查是否是内容过滤错误
        elif "content filter" in error_message.lower() or "content_filter" in error_message:
            friendly_msg += "🔍 错误原因：内容被安全策略拦截\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI检测到内容可能违反使用政策。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查图片内容\n"
            friendly_msg += "      - 某些敏感内容可能被API拒绝处理\n\n"
            friendly_msg += "   2. 更换翻译器\n"
            friendly_msg += "      - 尝试使用其他翻译器（如 Gemini、DeepL）\n\n"
            friendly_msg += "   3. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 有时重试可以解决临时的过滤问题\n\n"
        
        # 检查是否是语言不支持错误
        elif "language not supported" in error_message.lower() or "LanguageUnsupportedException" in error_traceback:
            friendly_msg += "🔍 错误原因：翻译器不支持当前语言\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 更换翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 建议：使用支持更多语言的翻译器（如 OpenAI、Gemini）\n\n"
            friendly_msg += "   2. 检查目标语言设置\n"
            friendly_msg += "      - 位置：翻译设置 → 目标语言\n"
            friendly_msg += "      - 确认选择的语言被当前翻译器支持\n\n"
        
        # 通用错误
        else:
            friendly_msg += "🔍 错误原因：\n"
            friendly_msg += f"   {error_message}\n\n"
            friendly_msg += "💡 通用解决方案：\n"
            friendly_msg += "   1. 检查配置是否正确\n"
            friendly_msg += "      - 翻译器、API密钥、模型名称等\n\n"
            friendly_msg += "   2. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n\n"
            friendly_msg += "   3. 查看详细日志\n"
            friendly_msg += "      - 在日志框中查找更多错误信息\n\n"
        
        friendly_msg += "="*80 + "\n"
        friendly_msg += "📋 原始错误信息：\n"
        friendly_msg += "-"*80 + "\n"
        friendly_msg += f"{error_message}\n"
        if error_traceback and "Traceback" in error_traceback:
            friendly_msg += "\n" + "-"*80 + "\n"
            friendly_msg += "详细错误：\n"
            friendly_msg += "-"*80 + "\n"
            
            # 只保留API详细错误信息（不保留代码路径）
            lines = error_traceback.split('\n')
            api_error_lines = []
            
            for line in lines:
                # 只保留API错误信息行（包含详细的错误内容）
                if line.strip() and any(keyword in line for keyword in ['BadRequest', 'Error code:', "'error':", "'message':", "{'error':"]):
                    # 如果这是详细的错误信息行，保留它
                    if 'Error code:' in line or "'error':" in line or "{'error':" in line:
                        api_error_lines.append(line.strip())
            
            if api_error_lines:
                friendly_msg += '\n'.join(api_error_lines) + "\n"
                
        friendly_msg += "="*80 + "\n"
        
        return friendly_msg

    async def _do_processing(self):
        log_handler = QtLogHandler(self.log_received)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_handler.setFormatter(formatter)
        manga_logger = logging.getLogger('manga_translator')
        manga_logger.addHandler(log_handler)
        manga_logger.setLevel(logging.INFO)

        results = []
        try:
            from manga_translator.config import (
                ColorizerConfig,
                Config,
                DetectorConfig,
                InpainterConfig,
                OcrConfig,
                RenderConfig,
                Translator,
                TranslatorConfig,
                UpscaleConfig,
            )
            from manga_translator.manga_translator import MangaTranslator
            from PIL import Image

            self.log_received.emit("--- [9] THREAD: Initializing translator...")
            translator_params = self.config_dict.get('cli', {})
            translator_params.update(self.config_dict)
            
            # 根据 verbose 设置设置日志级别
            verbose = translator_params.get('verbose', False)
            if hasattr(self, 'log_service') and self.log_service:
                self.log_service.set_console_log_level(verbose)
            
            font_filename = self.config_dict.get('render', {}).get('font_path')
            if font_filename:
                font_full_path = os.path.join(self.root_dir, 'fonts', font_filename)
                if os.path.exists(font_full_path):
                    translator_params['font_path'] = font_full_path
                    # 同时更新 config_dict 中的 font_path
                    self.config_dict['render']['font_path'] = font_full_path

            translator = MangaTranslator(params=translator_params)
            self.log_received.emit("--- [10] THREAD: Translator initialized.")

            explicit_keys = {'render', 'upscale', 'translator', 'detector', 'colorizer', 'inpainter', 'ocr'}
            remaining_config = {
                k: v for k, v in self.config_dict.items() 
                if k in Config.__fields__ and k not in explicit_keys
            }

            render_config_data = self.config_dict.get('render', {}).copy()

            # 转换 direction 值：'h' -> 'horizontal', 'v' -> 'vertical'
            if 'direction' in render_config_data:
                direction_value = render_config_data['direction']
                if direction_value == 'h':
                    render_config_data['direction'] = 'horizontal'
                elif direction_value == 'v':
                    render_config_data['direction'] = 'vertical'

            translator_config_data = self.config_dict.get('translator', {}).copy()
            hq_prompt_path = translator_config_data.get('high_quality_prompt_path')
            if hq_prompt_path and not os.path.isabs(hq_prompt_path):
                full_prompt_path = os.path.join(self.root_dir, hq_prompt_path)
                if os.path.exists(full_prompt_path):
                    translator_config_data['high_quality_prompt_path'] = full_prompt_path
                else:
                    self.log_received.emit(f"--- WARNING: High quality prompt file not found at {full_prompt_path}")
            
            # 将 CLI 配置中的 attempts 复制到 translator 配置中
            cli_attempts = self.config_dict.get('cli', {}).get('attempts', -1)
            translator_config_data['attempts'] = cli_attempts
            self.log_received.emit(f"--- Setting translator attempts to: {cli_attempts} (from UI config)")

            # 转换超分倍数：'不使用' -> None, '2'/'4' -> int
            upscale_config_data = self.config_dict.get('upscale', {}).copy()
            if 'upscale_ratio' in upscale_config_data:
                ratio_value = upscale_config_data['upscale_ratio']
                if ratio_value == '不使用' or ratio_value is None:
                    upscale_config_data['upscale_ratio'] = None
                else:
                    try:
                        upscale_config_data['upscale_ratio'] = int(ratio_value)
                    except (ValueError, TypeError):
                        upscale_config_data['upscale_ratio'] = None

            config = Config(
                render=RenderConfig(**render_config_data),
                upscale=UpscaleConfig(**upscale_config_data),
                translator=TranslatorConfig(**translator_config_data),
                detector=DetectorConfig(**self.config_dict.get('detector', {})),
                colorizer=ColorizerConfig(**self.config_dict.get('colorizer', {})),
                inpainter=InpainterConfig(**self.config_dict.get('inpainter', {})),
                ocr=OcrConfig(**self.config_dict.get('ocr', {})),
                **remaining_config
            )
            self.log_received.emit("--- [11] THREAD: Config object created correctly.")

            translator_type = config.translator.translator
            is_hq = translator_type in [Translator.openai_hq, Translator.gemini_hq]
            batch_size = self.config_dict.get('cli', {}).get('batch_size', 1)

            # 准备save_info（所有模式都需要）
            output_format = self.config_dict.get('cli', {}).get('format')
            if not output_format or output_format == "不指定":
                output_format = None # Set to None to preserve original extension

            # 收集输入文件夹列表（从file_to_folder_map中获取）
            input_folders = set()
            for file_path in self.files:
                folder = self.file_to_folder_map.get(file_path)
                if folder:
                    input_folders.add(os.path.normpath(folder))

            save_info = {
                'output_folder': self.output_folder,
                'format': output_format,
                'overwrite': self.config_dict.get('cli', {}).get('overwrite', True),
                'input_folders': input_folders
            }

            # 确定翻译流程模式
            workflow_mode = self._t("Normal Translation")
            workflow_tip = ""
            cli_config = self.config_dict.get('cli', {})
            if cli_config.get('upscale_only', False):
                workflow_mode = self._t("Upscale Only")
                workflow_tip = self._t("💡 Tip: Only upscale images, no detection, OCR, translation or rendering")
            elif cli_config.get('colorize_only', False):
                workflow_mode = self._t("Colorize Only")
                workflow_tip = self._t("💡 Tip: Only colorize images, no detection, OCR, translation or rendering")
            elif cli_config.get('generate_and_export', False):
                workflow_mode = self._t("Export Translation")
                workflow_tip = self._t("💡 Tip: After exporting, check manga_translator_work/translations/ for imagename_translated.txt files")
            elif cli_config.get('template', False):
                workflow_mode = self._t("Export Original Text")
                workflow_tip = self._t("💡 Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode")
            elif cli_config.get('load_text', False):
                workflow_mode = self._t("Import Translation and Render")
                workflow_tip = self._t("💡 Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)")
                
                # 在load_text模式下，先自动导入txt文件的翻译到JSON
                self.log_received.emit(self._t("📥 Importing translations from TXT files to JSON..."))
                from desktop_qt_ui.services.workflow_service import smart_update_translations_from_images, ensure_default_template_exists
                template_path = ensure_default_template_exists()
                if template_path:
                    import_result = smart_update_translations_from_images(self.files, template_path)
                    self.log_received.emit(self._t("Import result: {result}", result=import_result))
                else:
                    self.log_received.emit(self._t("⚠️ Warning: Cannot find template file, skipping auto-import"))

            if is_hq or (len(self.files) > 1 and batch_size > 1):
                self.log_received.emit(f"--- [12] THREAD: Starting batch processing ({'HQ mode' if is_hq else 'Batch mode'})...")

                # 输出批量处理信息
                total_images = len(self.files)
                # 前端分批加载的批次大小（用于内存管理）
                frontend_batch_size = 10  # 每次最多加载10张图片到内存
                total_frontend_batches = (total_images + frontend_batch_size - 1) // frontend_batch_size
                
                # 计算后端总批次数（用于显示统一的进度）
                backend_total_batches = (total_images + batch_size - 1) // batch_size if batch_size > 0 else total_images
                
                self.log_received.emit(self._t("📊 Batch processing mode: {total} images in {batches} batches", total=total_images, batches=backend_total_batches))
                self.log_received.emit(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
                self.log_received.emit(self._t("📁 Output directory: {dir}", dir=self.output_folder))
                if workflow_tip:
                    self.log_received.emit(workflow_tip)

                # 按批次加载和处理图片（节省内存）
                self.log_received.emit(self._t("🚀 Starting translation..."))
                
                all_contexts = []
                processed_images_count = 0  # 已处理的图片总数
                
                for frontend_batch_num in range(total_frontend_batches):
                    if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                    
                    batch_start = frontend_batch_num * frontend_batch_size
                    batch_end = min(batch_start + frontend_batch_size, total_images)
                    current_batch_files = self.files[batch_start:batch_end]
                    
                    # 加载当前批次的图片（静默加载，不显示前端批次信息）
                    images_with_configs = []
                    for file_path in current_batch_files:
                        if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                        self.progress.emit(batch_start + len(images_with_configs), total_images, f"Loading: {os.path.basename(file_path)}")
                        try:
                            # 使用二进制模式读取以避免Windows路径编码问题
                            with open(file_path, 'rb') as f:
                                image = Image.open(f)
                                image.load()  # 立即加载图片数据，避免文件句柄关闭后无法访问
                            image.name = file_path
                            images_with_configs.append((image, config))
                        except Exception as e:
                            self.log_received.emit(f"⚠️ 无法加载图片 {os.path.basename(file_path)}: {e}")
                            self.logger.error(f"Error loading image {file_path}: {e}")
                            # 创建错误上下文
                            from manga_translator.utils import Context
                            error_ctx = Context()
                            error_ctx.image_name = file_path
                            error_ctx.translation_error = str(e)
                            all_contexts.append(error_ctx)
                    
                    if images_with_configs:
                        # 传递全局偏移量给后端，让后端显示正确的全局图片编号
                        batch_contexts = await translator.translate_batch(
                            images_with_configs, 
                            save_info=save_info,
                            global_offset=processed_images_count,  # 传递已处理的图片数
                            global_total=total_images  # 传递总图片数
                        )
                        all_contexts.extend(batch_contexts)
                        processed_images_count += len(images_with_configs)
                        
                        # 批次处理完成后，立即清理图片对象
                        for image, _ in images_with_configs:
                            if hasattr(image, 'close'):
                                try:
                                    image.close()
                                except:
                                    pass
                        images_with_configs.clear()
                        
                        # 强制垃圾回收
                        import gc
                        gc.collect()
                
                contexts = all_contexts

                # The backend now handles saving for batch jobs. We just need to collect the paths/status.
                success_count = 0
                failed_count = 0
                for ctx in contexts:
                    if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                    if ctx:
                        # 检查是否有翻译错误
                        if hasattr(ctx, 'translation_error') and ctx.translation_error:
                            results.append({'success': False, 'original_path': ctx.image_name, 'error': ctx.translation_error})
                            failed_count += 1
                            # 输出详细的错误信息（包含原始错误）
                            self.log_received.emit(f"\n⚠️ 图片 {os.path.basename(ctx.image_name)} 翻译失败：")
                            self.log_received.emit(ctx.translation_error)
                        elif hasattr(ctx, 'success') and ctx.success:
                            # 优先检查success标志（因为result可能被清理了）
                            results.append({'success': True, 'original_path': ctx.image_name, 'image_data': None})
                            success_count += 1
                        elif ctx.result:
                            results.append({'success': True, 'original_path': ctx.image_name, 'image_data': None})
                            success_count += 1
                        else:
                            results.append({'success': False, 'original_path': ctx.image_name, 'error': '翻译结果为空'})
                            failed_count += 1
                    else:
                        results.append({'succes000000000000000000000000000000000000000000s': False, 'original_path': 'Unknown', 'error': 'Batch translation returned no context'})
                        failed_count += 1

                if failed_count > 0:
                    self.log_received.emit(self._t("\n⚠️ Batch translation completed: {success}/{total} succeeded, {failed}/{total} failed", success=success_count, total=total_images, failed=failed_count))
                else:
                    self.log_received.emit(self._t("✅ Batch translation completed: {success}/{total} succeeded", success=success_count, total=total_images))
                self.log_received.emit(self._t("💾 Files saved to: {dir}", dir=self.output_folder))

            else:
                self.log_received.emit("--- [12] THREAD: Starting sequential processing...")
                total_files = len(self.files)

                # 输出顺序处理信息
                self.log_received.emit(self._t("📊 Sequential processing mode: {total} images", total=total_files))
                self.log_received.emit(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
                self.log_received.emit(self._t("📁 Output directory: {dir}", dir=self.output_folder))
                if workflow_tip:
                    self.log_received.emit(workflow_tip)

                success_count = 0
                for i, file_path in enumerate(self.files):
                    if not self._is_running:
                        raise asyncio.CancelledError("Task stopped by user.")

                    current_num = i + 1
                    self.progress.emit(i, total_files, f"Processing: {os.path.basename(file_path)}")
                    self.log_received.emit(f"🔄 [{current_num}/{total_files}] 正在处理：{os.path.basename(file_path)}")

                    try:
                        # 使用二进制模式读取以避免Windows路径编码问题
                        with open(file_path, 'rb') as f:
                            image = Image.open(f)
                            image.load()  # 立即加载图片数据，避免文件句柄关闭后无法访问
                        image.name = file_path

                        ctx = await translator.translate(image, config, image_name=image.name)

                        if ctx and ctx.result:
                            self.file_processed.emit({'success': True, 'original_path': file_path, 'image_data': ctx.result})
                            success_count += 1
                            self.log_received.emit(f"✅ [{current_num}/{total_files}] 完成：{os.path.basename(file_path)}")
                        else:
                            self.file_processed.emit({'success': False, 'original_path': file_path, 'error': 'Translation returned no result or image'})
                            self.log_received.emit(f"❌ [{current_num}/{total_files}] 失败：{os.path.basename(file_path)}")

                    except Exception as e:
                        self.log_received.emit(f"❌ [{current_num}/{total_files}] 错误：{os.path.basename(file_path)} - {e}")
                        self.file_processed.emit({'success': False, 'original_path': file_path, 'error': str(e)})
                        # 抛出异常，终止整个翻译流程
                        raise

                self.log_received.emit(f"✅ 顺序翻译完成：成功 {success_count}/{total_files} 张")
                self.log_received.emit(f"💾 文件已保存到：{self.output_folder}")
            
            self.finished.emit(results)
            
            # ✅ 翻译完成后打印内存快照（调试用）
            try:
                import tracemalloc
                snapshot = tracemalloc.take_snapshot()
                top_stats = snapshot.statistics('lineno')
                self.log_received.emit("\n" + "="*80)
                self.log_received.emit("📊 内存占用 TOP 100:")
                self.log_received.emit("="*80)
                for i, stat in enumerate(top_stats[:100], 1):
                    self.log_received.emit(f"{i}. {stat}")
                self.log_received.emit("="*80 + "\n")
            except Exception as e:
                self.log_received.emit(f"Failed to print memory snapshot: {e}")

        except asyncio.CancelledError as e:
            self.log_received.emit(f"Task cancelled: {e}")
            self.error.emit(str(e))
        except Exception as e:
            import traceback
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            # 构建友好的中文错误提示
            friendly_error = self._build_friendly_error_message(error_message, error_traceback)
            
            self.log_received.emit(friendly_error)
            self.error.emit(friendly_error)
        finally:
            manga_logger.removeHandler(log_handler)

            # 翻译结束后清空翻译器缓存，确保下次翻译使用最新的 .env 配置
            try:
                from manga_translator.translators import translator_cache
                translator_cache.clear()
                self.log_received.emit(f"--- [CLEANUP] Cleared translator cache")
            except Exception as e:
                self.log_received.emit(f"--- [CLEANUP] Warning: Failed to clear cache: {e}")

    @pyqtSlot()
    def process(self):
        loop = None
        try:
            import asyncio
            import sys
            self.log_received.emit("--- [1] THREAD: process() method entered, starting asyncio task.")

            # 在Windows上的工作线程中，需要手动初始化Windows Socket
            if sys.platform == 'win32':
                # 使用ctypes直接调用WSAStartup
                import ctypes
                
                try:
                    # WSADATA结构体大小
                    WSADATA_SIZE = 400
                    wsa_data = ctypes.create_string_buffer(WSADATA_SIZE)
                    # 调用WSAStartup，版本2.2
                    ws2_32 = ctypes.WinDLL('ws2_32')
                    result = ws2_32.WSAStartup(0x0202, wsa_data)
                    if result != 0:
                        self.log_received.emit(f"--- [ERROR] WSAStartup failed with code {result}")
                except Exception as e:
                    self.log_received.emit(f"--- [ERROR] Failed to initialize WSA: {e}")
                
                # 使用ProactorEventLoop（Windows默认）
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            # 创建事件循环并保存任务引用
            try:
                loop = asyncio.new_event_loop()
            except Exception as e:
                self.log_received.emit(f"--- [ERROR] Failed to create event loop: {e}")
                import traceback
                self.log_received.emit(f"--- [ERROR] Traceback: {traceback.format_exc()}")
                raise
            
            asyncio.set_event_loop(loop)
            
            self._current_task = loop.create_task(self._do_processing())
            loop.run_until_complete(self._current_task)
            self.log_received.emit("--- [END] THREAD: asyncio task finished.")

        except asyncio.CancelledError:
            self.log_received.emit("--- [CANCELLED] THREAD: asyncio task was cancelled.")
        except Exception as e:
            import traceback
            self.error.emit(f"An error occurred in the asyncio runner: {str(e)}\n{traceback.format_exc()}")
        finally:
            if loop:
                try:
                    # Cancel all remaining tasks
                    tasks = asyncio.all_tasks(loop=loop)
                    for task in tasks:
                        task.cancel()
                    
                    # Gather all tasks to let them finish cancelling
                    if tasks:
                        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

                    # Shutdown async generators
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception as e:
                    self.log_received.emit(f"--- ERROR during asyncio cleanup: {e}")
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
                    self.log_received.emit("--- [CLEANUP] THREAD: asyncio loop closed.")
