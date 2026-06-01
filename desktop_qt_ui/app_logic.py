
"""
应用业务逻辑层
处理应用的核心业务逻辑，与UI层分离
"""
import asyncio
import base64
import io
import logging
import os
import textwrap
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PIL import Image
from PyQt6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtWidgets import QFileDialog
from services import (
    get_config_service,
    get_file_service,
    get_i18n_manager,
    get_logger,
    get_preset_service,
    get_state_manager,
    get_translation_service,
)
from services.state_manager import AppStateKey
from utils.asyncio_cleanup import shutdown_event_loop

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
from manga_translator.utils.openai_compat import resolve_openai_compatible_api_key
from manga_translator.utils import open_pil_image, save_pil_image


@dataclass
class AppConfig:
    """应用配置信息"""
    window_size: tuple = (1200, 800)
    theme: str = "dark"
    language: str = "zh_CN"
    auto_save: bool = True
    max_recent_files: int = 10


ARCHIVE_EXTRACT_IMAGE_DIRNAME = 'original_images'
ARCHIVE_EXTRACT_META_FILENAME = '.extract_meta.json'
_OPENAI_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}
_GEMINI_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
    "Origin": "https://aistudio.google.com",
    "Referer": "https://aistudio.google.com/",
}


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


class MainAppLogic(QObject):
    """主页面业务逻辑控制器"""
    files_added = pyqtSignal(list)
    files_cleared = pyqtSignal()
    file_removed = pyqtSignal(str)
    config_loaded = pyqtSignal(dict)
    output_path_updated = pyqtSignal(str)
    task_completed = pyqtSignal(list)
    task_file_completed = pyqtSignal(dict)
    error_dialog_requested = pyqtSignal(str)
    render_setting_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_service = get_config_service()
        self.translation_service = get_translation_service()
        self.file_service = get_file_service()
        self.state_manager = get_state_manager()
        self.i18n = get_i18n_manager()
        self.preset_service = get_preset_service()

        # ✅ 使用普通线程替代线程池
        self.current_thread = None  # 当前运行的线程
        self.current_worker = None  # 当前运行的worker
        self._shutdown_started = False
        self.current_task_id = 0  # 任务ID，用于区分不同的翻译任务
        self.saved_files_count = 0
        self.saved_files_list = []  # 收集所有保存的文件路径
        self._task_failures: List[Dict[str, str]] = []
        self._task_failure_keys: set[str] = set()

        self.source_files: List[str] = [] # Holds both files and folders
        self.file_to_folder_map: Dict[str, Optional[str]] = {} # 记录文件来自哪个文件夹
        self.archive_to_temp_map: Dict[str, str] = {} # 记录压缩包解压的临时目录
        self.excluded_subfolders: set = set() # 记录被删除的子文件夹路径
        self.folder_tree_cache: Dict[str, dict] = {} # 缓存文件夹的完整树结构 {top_folder: tree_structure}

        self.app_config = AppConfig()
        self._ui_log("主页面应用业务逻辑初始化完成")
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key
    
    def _ui_log(self, message: str, level: str = "INFO"):
        """
        输出到日志文件
        使用 root logger 确保写入 main.py 配置的日志文件
        """
        try:
            root_logger = logging.getLogger()
            if level == "ERROR":
                root_logger.error(message)
            elif level == "DEBUG":
                root_logger.debug(message)
            elif level == "WARNING":
                root_logger.warning(message)
            else:
                root_logger.info(message)
        except Exception:
            print(f"{level} - {message}")

    def _collect_runtime_env_values(self) -> Dict[str, str]:
        env_vars = self.config_service.load_env_vars()
        if hasattr(self, "main_view") and self.main_view and getattr(self.main_view, "env_widgets", None):
            for key, pair in self.main_view.env_widgets.items():
                if not pair or len(pair) < 2:
                    continue
                widget = pair[1]
                try:
                    env_vars[key] = widget.text().strip()
                except Exception:
                    continue
        return env_vars

    def _format_missing_api_requirement_label(self, item: Dict[str, Any]) -> str:
        section = item.get("section")
        setting = item.get("setting")
        if section == "translator":
            section_label = self._t("label_translator")
        elif section == "ocr" and setting == "secondary_ocr":
            section_label = self._t("label_secondary_ocr")
        elif section == "ocr":
            section_label = self._t("label_ocr")
        elif section == "colorizer":
            section_label = self._t("label_colorizer")
        elif section == "render":
            section_label = self._t("label_renderer")
        else:
            section_label = str(section or self._t("Settings"))

        display_name = str(item.get("display_name") or item.get("selected_value") or "").strip()
        if display_name:
            return f"{section_label}: {display_name}"
        return section_label

    def _validate_runtime_api_requirements(self, config) -> bool:
        from PyQt6.QtWidgets import QMessageBox

        env_vars = self._collect_runtime_env_values()
        missing = self.config_service.get_missing_runtime_api_requirements(config, env_vars)
        if not missing:
            return True

        details = "\n".join(
            f"- {self._format_missing_api_requirement_label(item)} -> {' / '.join(item.get('accepted_env_vars', []))}"
            for item in missing
        )
        log_summary = "; ".join(
            f"{self._format_missing_api_requirement_label(item)} -> {' / '.join(item.get('accepted_env_vars', []))}"
            for item in missing
        )
        self._ui_log(f"API 配置缺失，已阻止开始翻译: {log_summary}", "WARNING")
        QMessageBox.warning(
            None,
            self._t("API Keys Required"),
            self._t(
                "The selected features are missing required API Keys (.env):\n{details}\n\nPlease fill one of the listed API key fields in API Keys (.env) and try again.",
                details=details,
            ),
        )
        return False

    def _reset_task_failures(self):
        self._task_failures = []
        self._task_failure_keys = set()

    def _normalize_task_error_summary(self, error_message: str, limit: int = 160) -> str:
        raw = str(error_message or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        summary = lines[0] if lines else "未记录详细错误"
        return textwrap.shorten(summary, width=limit, placeholder="...")

    def _record_task_failure(self, original_path: str, error_message: str):
        normalized_path = os.path.normpath(str(original_path or "Unknown"))
        raw_error = str(error_message or "").strip() or "未记录详细错误"
        failure_key = f"{normalized_path}\n{raw_error}"
        if failure_key in self._task_failure_keys:
            return

        self._task_failure_keys.add(failure_key)
        self._task_failures.append(
            {
                "original_path": normalized_path,
                "file_name": os.path.basename(normalized_path) or normalized_path,
                "error": raw_error,
                "summary": self._normalize_task_error_summary(raw_error),
            }
        )

    def _record_task_failure_from_result(self, result: Dict[str, Any]):
        if not result or result.get("success"):
            return
        self._record_task_failure(result.get("original_path"), result.get("error"))

    def _build_task_failure_dialog_message(self) -> str:
        failed_count = len(self._task_failures)
        if failed_count == 0:
            return ""

        first_failure = self._task_failures[0]
        return TranslationWorker._build_friendly_error_message(first_failure["error"], "")


    @pyqtSlot(dict)
    def on_file_completed(self, result):
        """处理单个文件处理完成的信号并保存"""
        if not result.get('success'):
            self._record_task_failure_from_result(result)
            self.logger.error(f"Skipping save for failed item: {result.get('original_path')}")
            return

        try:
            # 检查是否是批量模式（后端已保存，有 output_path 但没有 image_data）
            if result.get('output_path') and not result.get('image_data'):
                # 批量模式：文件已由后端保存
                final_output_path = result['output_path']
                self.saved_files_count += 1
                self.saved_files_list.append(final_output_path)
                self.logger.info(self._t("log_file_saved_successfully", path=final_output_path))
                self.task_file_completed.emit({'path': final_output_path})
                return
            
            # 顺序模式：需要前端保存
            if not result.get('image_data'):
                self.logger.error(f"No image_data for: {result.get('original_path')}")
                return
            config = self.config_service.get_config()
            output_format = config.cli.format
            save_quality = config.cli.save_quality
            output_folder = config.app.last_output_path
            save_to_source_dir = config.cli.save_to_source_dir

            original_path = result['original_path']
            base_filename = os.path.basename(original_path)

            # 检查是否启用了"输出到原图目录"模式
            if save_to_source_dir:
                # 输出到原图所在目录的 manga_translator_work/result 子目录
                source_dir = os.path.dirname(original_path)
                final_output_folder = os.path.join(source_dir, 'manga_translator_work', 'result')
            else:
                # 原有逻辑：使用配置的输出目录
                if not output_folder:
                    self.logger.error(self._t("log_output_dir_not_set"))
                    self.state_manager.set_status_message(self._t("error_output_dir_not_set"))
                    return

                # 检查文件是否来自文件夹或压缩包
                source_folder = self.file_to_folder_map.get(original_path)

                if source_folder:
                    # 检查是否来自压缩包
                    if self.file_service.is_archive_file(source_folder):
                        # 文件来自压缩包：
                        # 优先复用解压目录的上级输出目录，避免文件夹扫描时被平铺到输出根目录
                        archive_output_dir = _resolve_archive_output_dir_from_extracted_image(
                            original_path, output_folder
                        )
                        if archive_output_dir:
                            final_output_folder = archive_output_dir
                        else:
                            archive_name = os.path.splitext(os.path.basename(source_folder))[0]
                            final_output_folder = os.path.join(output_folder, archive_name)
                    else:
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

            image_to_save = result['image_data']
            self._save_image_with_source_metadata(
                image_to_save,
                final_output_path,
                original_path,
                save_quality,
            )

            # 更新translation_map.json
            self._update_translation_map(original_path, final_output_path)

            self.saved_files_count += 1
            self.saved_files_list.append(final_output_path)  # 收集保存的文件路径
            self.logger.info(self._t("log_file_saved_successfully", path=final_output_path))
            self.task_file_completed.emit({'path': final_output_path})

        except Exception as e:
            self.logger.error(self._t("log_file_save_error", path=result['original_path'], error=e))

    def _save_image_with_source_metadata(
        self,
        image: Image.Image,
        output_path: str,
        source_path: Optional[str],
        save_quality: int,
    ):
        source_image = None
        try:
            if source_path and os.path.exists(source_path):
                try:
                    source_image = open_pil_image(source_path, eager=True)
                except Exception as exc:
                    self.logger.warning(f"读取原图元数据失败，将继续保存但不继承ICC: {source_path}, error={exc}")
            save_pil_image(
                image,
                output_path,
                source_image=source_image,
                quality=save_quality,
            )
        finally:
            if source_image is not None:
                try:
                    source_image.close()
                except Exception:
                    pass

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

    def _calculate_output_path(self, image_path: str, save_info: dict) -> str:
        """
        计算输出文件的完整路径（用于预检查文件是否存在）
        
        Args:
            image_path: 输入图片的路径
            save_info: 包含输出配置的字典
                - output_folder: 输出文件夹
                - format: 输出格式（可选）
                - save_to_source_dir: 是否输出到原图目录
                
        Returns:
            str: 计算后的输出文件完整路径
        """
        output_folder = save_info.get('output_folder')
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
            
            # 检查文件是否来自文件夹
            source_folder = self.file_to_folder_map.get(image_path)
            if source_folder:
                # 检查是否来自压缩包
                if self.file_service.is_archive_file(source_folder):
                    archive_output_dir = _resolve_archive_output_dir_from_extracted_image(
                        image_path, output_folder
                    )
                    if archive_output_dir:
                        final_output_dir = archive_output_dir
                    else:
                        archive_name = os.path.splitext(os.path.basename(source_folder))[0]
                        final_output_dir = os.path.join(output_folder, archive_name)
                else:
                    # 文件来自文件夹，保持相对路径结构
                    relative_path = os.path.relpath(parent_dir, source_folder)
                    # Normalize path and avoid adding '.' as a directory component
                    if relative_path == '.':
                        final_output_dir = os.path.join(output_folder, os.path.basename(source_folder))
                    else:
                        final_output_dir = os.path.join(output_folder, os.path.basename(source_folder), relative_path)
                final_output_dir = os.path.normpath(final_output_dir)
        
        # 处理输出文件名和格式
        base_filename, _ = os.path.splitext(os.path.basename(file_path))
        if output_format and output_format.strip() and output_format.lower() not in ['none', '不指定']:
            output_filename = f"{base_filename}.{output_format}"
        else:
            output_filename = os.path.basename(file_path)
        
        final_output_path = os.path.join(final_output_dir, output_filename)
        return final_output_path

    @pyqtSlot(str)
    def on_worker_log(self, message):
        message = str(message).rstrip()
        if not message:
            return
        self.logger.info(message)

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
            # 系统提示词文件的 stem（不含扩展名），排除这些文件
            system_prompt_stems = {
                'system_prompt_hq',
                'system_prompt_hq_format',
                'system_prompt_line_break',
                'glossary_extraction_prompt',
                'ai_ocr_prompt',
                'ai_colorizer_prompt',
                'ai_renderer_prompt',
            }
            prompt_extensions = ('.yaml', '.yml', '.json')
            prompt_files = sorted([
                f for f in os.listdir(dict_dir)
                if f.lower().endswith(prompt_extensions)
                and os.path.splitext(f)[0] not in system_prompt_stems
            ])
            return prompt_files
        except Exception as e:
            self.logger.error(f"Error scanning prompt directory: {e}")
            return []

    @pyqtSlot(str, str)
    def save_env_var(self, key: str, value: str):
        self.config_service.save_env_var(key, value)
        # 不再输出日志，避免刷屏

    # region 预设管理
    def get_presets_list(self) -> List[str]:
        """获取所有预设名称列表"""
        return self.preset_service.get_presets_list()
    
    @pyqtSlot(str)
    def save_preset(self, preset_name: str, copy_current: bool = False) -> bool:
        """保存预设
        
        Args:
            preset_name: 预设名称
            copy_current: 是否复制当前配置。False=创建空白预设，True=复制当前配置
        """
        try:
            preset_env_keys = self.config_service.get_all_preset_env_vars()
            if copy_current:
                # 复制当前配置模式：保存全部 API 相关的环境变量
                current_env_vars = self.config_service.load_env_vars()
                all_env_vars = {key: current_env_vars.get(key, "") for key in preset_env_keys}
                
                # 保存所有环境变量，包括空值，以准确反映当前配置状态
                success = self.preset_service.save_preset(preset_name, all_env_vars)
                if success:
                    # 不输出日志，避免刷屏
                    pass
            else:
                # 创建空白预设模式：为全部 API 环境变量创建空白结构
                empty_env_vars = {key: "" for key in preset_env_keys}
                
                success = self.preset_service.save_preset(preset_name, empty_env_vars)
                if success:
                    self._ui_log(f"预设已创建: {preset_name} (空白预设)")
            
            if not success:
                self._ui_log(f"保存预设失败: {preset_name}", "ERROR")
            return success
        except Exception as e:
            self.logger.error(f"保存预设失败: {e}")
            self._ui_log(f"保存预设失败: {e}", "ERROR")
            return False
    
    @pyqtSlot(str)
    def load_preset(self, preset_name: str) -> bool:
        """加载预设并完全替换.env文件"""
        try:
            # 加载预设文件
            preset_env_vars = self.preset_service.load_preset(preset_name)
            if preset_env_vars is None:
                self._ui_log(f"加载预设失败: {preset_name}", "ERROR")
                return False
            
            # 完全替换.env文件，只保留预设中的字段
            success = self.config_service.replace_env_file(preset_env_vars)
            if not success:
                self._ui_log(f"应用预设失败: {preset_name}", "ERROR")
            return success
        except Exception as e:
            self.logger.error(f"加载预设失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            self._ui_log(f"加载预设失败: {e}", "ERROR")
            return False
    
    @pyqtSlot(str)
    def delete_preset(self, preset_name: str) -> bool:
        """删除预设"""
        try:
            success = self.preset_service.delete_preset(preset_name)
            if success:
                self._ui_log(f"预设已删除: {preset_name}")
            else:
                self._ui_log(f"删除预设失败: {preset_name}", "ERROR")
            return success
        except Exception as e:
            self.logger.error(f"删除预设失败: {e}")
            self._ui_log(f"删除预设失败: {e}", "ERROR")
            return False
    # endregion
    
    # region API测试
    @staticmethod
    def _normalize_api_test_target(translator_key: str) -> str:
        return (translator_key or "").strip().lower()

    @staticmethod
    def _is_openai_compatible_target(normalized_key: str) -> bool:
        return any(
            token in normalized_key
            for token in ("openai", "custom_openai", "deepseek", "groq")
        )

    @staticmethod
    def _build_api_test_image_bytes() -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), (255, 255, 255)).save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _extract_gemini_image_bytes(response) -> bytes | None:
        raw = getattr(response, "raw", None) or {}

        def _get_field(obj, *names):
            if obj is None:
                return None
            for name in names:
                if isinstance(obj, dict):
                    if name in obj:
                        return obj[name]
                elif hasattr(obj, name):
                    return getattr(obj, name)
            return None

        candidates = raw.get("candidates") or _get_field(response, "candidates") or []
        for candidate in candidates:
            content = _get_field(candidate, "content") or {}
            parts = _get_field(content, "parts") or []
            for part in parts:
                inline_data = _get_field(part, "inlineData", "inline_data")
                if inline_data is None and hasattr(part, "inline_data"):
                    inline_data = getattr(part, "inline_data")
                data = _get_field(inline_data, "data") if inline_data is not None else None
                if data:
                    return base64.b64decode(data)
        return None

    @staticmethod
    def _get_default_model_for_test(normalized_key: str) -> str | None:
        defaults = {
            "openai_ocr": "gpt-4o",
            "gemini_ocr": "gemini-1.5-flash",
            "openai_colorizer": "gpt-image-1",
            "gemini_colorizer": "gemini-2.0-flash-preview-image-generation",
            "openai_renderer": "gpt-image-1",
            "gemini_renderer": "gemini-2.0-flash-preview-image-generation",
        }
        return defaults.get(normalized_key)

    async def _test_openai_text_api(self, api_key: str, api_base: str | None, model: str | None) -> tuple[bool, str]:
        resolved_api_key = resolve_openai_compatible_api_key(api_key, api_base or "https://api.openai.com/v1")
        try:
            from manga_translator.translators.common import AsyncOpenAICurlCffi
            client = AsyncOpenAICurlCffi(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                default_headers=_OPENAI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=30.0,
                stream_timeout=30.0,
            )
        except ImportError:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                timeout=30.0,
            )

        try:
            if model and model.strip():
                await client.chat.completions.create(
                    model=model.strip(),
                    messages=[{"role": "user", "content": "test"}],
                )
                return True, f"连接成功，模型 {model.strip()} 可用"
            await client.models.list()
            return True, "连接成功"
        finally:
            await client.close()

    async def _test_openai_ocr_api(self, api_key: str, api_base: str | None, model: str | None) -> tuple[bool, str]:
        model_name = (model or "").strip() or self._get_default_model_for_test("openai_ocr")
        image_b64 = base64.b64encode(self._build_api_test_image_bytes()).decode("ascii")
        resolved_api_key = resolve_openai_compatible_api_key(api_key, api_base or "https://api.openai.com/v1")

        try:
            from manga_translator.translators.common import AsyncOpenAICurlCffi
            client = AsyncOpenAICurlCffi(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                default_headers=_OPENAI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=30.0,
                stream_timeout=30.0,
            )
        except ImportError:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                timeout=30.0,
            )

        try:
            await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Read the image and reply with OK."},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                        ],
                    }
                ],
            )
            return True, f"连接成功，OCR 模型 {model_name} 可用"
        finally:
            await client.close()

    async def _test_openai_image_api(self, api_key: str, api_base: str | None, model: str | None, target_label: str) -> tuple[bool, str]:
        model_name = (model or "").strip() or self._get_default_model_for_test(target_label)
        resolved_api_key = resolve_openai_compatible_api_key(api_key, api_base or "https://api.openai.com/v1")

        try:
            from manga_translator.translators.common import AsyncOpenAICurlCffi
            from manga_translator.utils.openai_image_interface import (
                request_openai_image_with_fallback,
            )

            client = AsyncOpenAICurlCffi(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                default_headers=_OPENAI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=60.0,
                stream_timeout=60.0,
            )

            async def fetch_remote_image(url: str):
                response = await client.session.get(url, timeout=60.0)
                if response.status_code != 200:
                    raise RuntimeError(f"Failed to download generated image: HTTP {response.status_code}")
                return Image.open(io.BytesIO(response.content)).convert("RGB")

            try:
                await request_openai_image_with_fallback(
                    session=client.session,
                    base_url=(api_base or "https://api.openai.com/v1").rstrip("/"),
                    api_key=resolved_api_key,
                    default_headers=_OPENAI_BROWSER_HEADERS,
                    model_name=model_name,
                    prompt_text="Return a simple test image.",
                    image_bytes=self._build_api_test_image_bytes(),
                    filename="test.png",
                    timeout=60.0,
                    fetch_remote_image=fetch_remote_image,
                    provider_name="OpenAI API Test",
                    logger=self.logger,
                )
                return True, f"连接成功，图像模型 {model_name} 可用"
            finally:
                await client.close()
        except ImportError:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                timeout=60.0,
            )
            try:
                await client.images.generate(
                    model=model_name,
                    prompt="Generate a simple test image.",
                    size="1024x1024",
                )
                return True, f"连接成功，图像模型 {model_name} 可用"
            finally:
                await client.close()

    async def _test_gemini_text_api(self, api_key: str, api_base: str | None, model: str | None) -> tuple[bool, str]:
        base_url = api_base.strip() if api_base and api_base.strip() else "https://generativelanguage.googleapis.com"

        try:
            from manga_translator.translators.common import AsyncGeminiCurlCffi
            client = AsyncGeminiCurlCffi(
                api_key=api_key,
                base_url=base_url,
                default_headers=_GEMINI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=30.0,
                stream_timeout=30.0,
            )
            try:
                if model and model.strip():
                    await client.models.generate_content(model=model.strip(), contents="test")
                    return True, f"连接成功，模型 {model.strip()} 可用"
                await client.models.list()
                return True, "连接成功"
            finally:
                await client.close()
        except ImportError:
            from google import genai
            from google.genai import types

            def sync_test():
                if base_url != "https://generativelanguage.googleapis.com":
                    client = genai.Client(
                        api_key=api_key,
                        http_options=types.HttpOptions(base_url=base_url),
                    )
                else:
                    client = genai.Client(api_key=api_key)

                if model and model.strip():
                    client.models.generate_content(model=model.strip(), contents="test")
                    return True, f"连接成功，模型 {model.strip()} 可用"
                list(client.models.list())
                return True, "连接成功"

            return await asyncio.get_running_loop().run_in_executor(None, sync_test)

    async def _test_gemini_ocr_api(self, api_key: str, api_base: str | None, model: str | None) -> tuple[bool, str]:
        model_name = (model or "").strip() or self._get_default_model_for_test("gemini_ocr")
        base_url = api_base.strip() if api_base and api_base.strip() else "https://generativelanguage.googleapis.com"
        image_b64 = base64.b64encode(self._build_api_test_image_bytes()).decode("ascii")
        contents = [
            {
                "role": "user",
                "parts": [
                    {"text": "Read the image and reply with OK."},
                    {"inlineData": {"mimeType": "image/png", "data": image_b64}},
                ],
            }
        ]

        try:
            from manga_translator.translators.common import AsyncGeminiCurlCffi
            client = AsyncGeminiCurlCffi(
                api_key=api_key,
                base_url=base_url,
                default_headers=_GEMINI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=30.0,
                stream_timeout=30.0,
            )
            try:
                await client.models.generate_content(model=model_name, contents=contents)
                return True, f"连接成功，OCR 模型 {model_name} 可用"
            finally:
                await client.close()
        except ImportError:
            from google import genai
            from google.genai import types

            def sync_test():
                if base_url != "https://generativelanguage.googleapis.com":
                    client = genai.Client(
                        api_key=api_key,
                        http_options=types.HttpOptions(base_url=base_url),
                    )
                else:
                    client = genai.Client(api_key=api_key)
                client.models.generate_content(model=model_name, contents=contents)
                return True, f"连接成功，OCR 模型 {model_name} 可用"

            return await asyncio.get_running_loop().run_in_executor(None, sync_test)

    async def _test_gemini_image_api(self, api_key: str, api_base: str | None, model: str | None, target_label: str) -> tuple[bool, str]:
        model_name = (model or "").strip() or self._get_default_model_for_test(target_label)
        base_url = api_base.strip() if api_base and api_base.strip() else "https://generativelanguage.googleapis.com"
        image_b64 = base64.b64encode(self._build_api_test_image_bytes()).decode("ascii")
        request_kwargs = {
            "model": model_name,
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Return a simple test image."},
                        {"inlineData": {"mimeType": "image/png", "data": image_b64}},
                    ],
                }
            ],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
            ],
        }

        try:
            from manga_translator.translators.common import AsyncGeminiCurlCffi
            client = AsyncGeminiCurlCffi(
                api_key=api_key,
                base_url=base_url,
                default_headers=_GEMINI_BROWSER_HEADERS,
                impersonate="chrome110",
                timeout=60.0,
                stream_timeout=60.0,
            )
            try:
                response = await client.models.generate_content(**request_kwargs)
                if not self._extract_gemini_image_bytes(response):
                    raise RuntimeError("Gemini image response did not contain an image.")
                return True, f"连接成功，图像模型 {model_name} 可用"
            finally:
                await client.close()
        except ImportError:
            from google import genai
            from google.genai import types

            def sync_test():
                if base_url != "https://generativelanguage.googleapis.com":
                    client = genai.Client(
                        api_key=api_key,
                        http_options=types.HttpOptions(base_url=base_url),
                    )
                else:
                    client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=request_kwargs["contents"],
                    config=types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                        safety_settings=[
                            types.SafetySetting(category=item["category"], threshold=item["threshold"])
                            for item in request_kwargs["safetySettings"]
                        ],
                    ),
                )
                if not self._extract_gemini_image_bytes(response):
                    raise RuntimeError("Gemini image response did not contain an image.")
                return True, f"连接成功，图像模型 {model_name} 可用"

            return await asyncio.get_running_loop().run_in_executor(None, sync_test)

    async def test_api_connection_async(self, translator_key: str, api_key: str, api_base: str = None, model: str = None) -> tuple[bool, str]:
        """异步测试API连接（如果指定了模型，会测试该模型是否可用）"""
        try:
            normalized_key = self._normalize_api_test_target(translator_key)

            if normalized_key == "openai_ocr":
                return await self._test_openai_ocr_api(api_key, api_base, model)
            if normalized_key in {"openai_colorizer", "openai_renderer"}:
                return await self._test_openai_image_api(api_key, api_base, model, normalized_key)
            if normalized_key == "gemini_ocr":
                return await self._test_gemini_ocr_api(api_key, api_base, model)
            if normalized_key in {"gemini_colorizer", "gemini_renderer"}:
                return await self._test_gemini_image_api(api_key, api_base, model, normalized_key)
            if self._is_openai_compatible_target(normalized_key):
                return await self._test_openai_text_api(api_key, api_base, model)
            if "gemini" in normalized_key:
                return await self._test_gemini_text_api(api_key, api_base, model)
            if "sakura" in normalized_key:
                # Sakura使用OpenAI兼容API
                from openai import AsyncOpenAI
                if not api_base:
                    return False, "请先配置SAKURA_API_BASE"
                client = AsyncOpenAI(
                    api_key="sk-114514",  # Sakura使用固定密钥
                    base_url=api_base
                )
                
                try:
                    # 如果指定了模型，测试该模型
                    if model and model.strip():
                        try:
                            # 不传递 max_tokens 以兼容所有模型
                            await client.chat.completions.create(
                                model=model,
                                messages=[{"role": "user", "content": "test"}]
                            )
                            return True, f"连接成功，模型 {model} 可用"
                        except Exception as e:
                            return False, f"连接成功但模型 {model} 不可用: {str(e)}"
                    else:
                        await client.models.list()
                        return True, "连接成功"
                finally:
                    await client.close()
            
            else:
                return False, "该翻译器不支持API测试"
                
        except Exception as e:
            return False, f"连接失败: {str(e)}"
    
    async def get_available_models_async(self, translator_key: str, api_key: str, api_base: str = None) -> tuple[bool, List[str], str]:
        """异步获取可用模型列表"""
        try:
            normalized_key = self._normalize_api_test_target(translator_key)

            if self._is_openai_compatible_target(normalized_key):
                resolved_api_key = resolve_openai_compatible_api_key(api_key, api_base or "https://api.openai.com/v1")
                # 尝试使用 curl_cffi 客户端绕过 TLS 指纹检测
                try:
                    from manga_translator.translators.common import AsyncOpenAICurlCffi
                    client = AsyncOpenAICurlCffi(
                        api_key=resolved_api_key,
                        base_url=api_base or "https://api.openai.com/v1",
                        impersonate="chrome110",
                        timeout=60.0
                    )
                except ImportError:
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(
                        api_key=resolved_api_key,
                        base_url=api_base or "https://api.openai.com/v1",
                        timeout=60.0,
                    )
                
                try:
                    models_response = await client.models.list()
                    
                    # 获取所有模型ID，不过滤
                    model_ids = [m.id for m in models_response.data]
                    model_ids.sort(reverse=True)  # 新模型在前
                    
                    return True, model_ids, "获取成功"
                finally:
                    await client.close()
            
            elif "gemini" in normalized_key:
                # Gemini API - 使用 curl_cffi 绕过 TLS 指纹检测，使用 Google Gemini 认证格式
                try:
                    from manga_translator.translators.common import AsyncGeminiCurlCffi

                    # 确定 base_url
                    base_url = api_base.strip() if api_base and api_base.strip() else "https://generativelanguage.googleapis.com"

                    client = AsyncGeminiCurlCffi(
                        api_key=api_key,
                        base_url=base_url,
                        impersonate="chrome110",
                        timeout=60.0
                    )
                    try:
                        models_response = await client.models.list()
                        model_ids = [m.id for m in models_response]
                        return True, model_ids, "获取成功"
                    finally:
                        await client.close()
                except ImportError:
                    # 如果 curl_cffi 不可用，回退到标准客户端
                    import asyncio

                    from google import genai
                    from google.genai import types
                    loop = asyncio.get_event_loop()

                    # 检查是否是自定义API
                    is_custom_api = (
                        api_base
                        and api_base.strip()
                        and api_base.strip() not in ["https://generativelanguage.googleapis.com", "https://generativelanguage.googleapis.com/"]
                    )

                    if is_custom_api:
                        # 自定义 API 使用 http_options
                        def sync_get_models():
                            client = genai.Client(
                                api_key=api_key,
                                http_options=types.HttpOptions(base_url=api_base.strip())
                            )
                            models = list(client.models.list())
                            model_names = [m.name.replace("models/", "") for m in models]
                            return True, model_names, "获取成功"
                    else:
                        def sync_get_models():
                            client = genai.Client(api_key=api_key)
                            models = list(client.models.list())
                            model_names = [m.name.replace("models/", "") for m in models]
                            return True, model_names, "获取成功"

                    return await loop.run_in_executor(None, sync_get_models)
            
            elif "sakura" in normalized_key:
                # Sakura使用OpenAI兼容API
                from openai import AsyncOpenAI
                if not api_base:
                    return False, [], "请先配置SAKURA_API_BASE"
                client = AsyncOpenAI(
                    api_key="sk-114514",
                    base_url=api_base
                )
                try:
                    models_response = await client.models.list()
                    model_ids = [m.id for m in models_response.data]
                    return True, model_ids, "获取成功"
                finally:
                    await client.close()
            
            else:
                return False, [], "该翻译器不支持获取模型列表"
                
        except Exception as e:
            return False, [], f"获取失败: {str(e)}"
    # endregion

    # region 配置管理
    def load_config_file(self, config_path: str) -> bool:
        try:
            success = self.config_service.load_config_file(config_path)
            if success:
                config = self.config_service.get_config()
                self.state_manager.set_current_config(config)
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, config_path)
                self.logger.info(self._t("log_config_loaded_successfully", path=config_path))
                self.config_loaded.emit(config.model_dump())
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
            
            # 当目标语言被更改时，更新翻译服务的目标语言
            if full_key == 'translator.target_lang':
                self.logger.debug(f"Target language switched to: {value}")
                self.translation_service.set_target_language(value)

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
                "realcugan": "Real-CUGAN",
                "mangajanai": "MangaJaNai"
            },
            "renderer": {
                "default": "Default",
                "openai_renderer": "OpenAI Renderer",
                "gemini_renderer": "Gemini Renderer",
                "none": self._t("translator_none"),
            },
            "colorizer": {
                "none": self._t("translator_none"),
                "mc2": "Manga Colorization v2",
                "openai_colorizer": "OpenAI Colorizer",
                "gemini_colorizer": "Gemini Colorizer",
            },
            "layout_mode": {
                'smart_scaling': self._t("layout_mode_smart_scaling"),
                'strict': self._t("layout_mode_strict"),
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
                    "openai": "OpenAI",
                    "openai_hq": self._t("translator_openai_hq"),
                    "gemini": "Google Gemini",
                    "gemini_hq": self._t("translator_gemini_hq"),
                    "sakura": "Sakura",
                    "none": self._t("translator_none"),
                    "original": self._t("translator_original"),
                },
                "target_lang": self.translation_service.get_target_languages(),
                "keep_lang": {
                    "none": self._t("lang_filter_disabled"),
                    **self.translation_service.get_keep_languages(),
                },
                "ocr_vl_language_hint": {
                    "auto": self._t("ocr_lang_auto"),
                    "multilingual": self._t("ocr_lang_multilingual"),
                    "Arabic": self._t("ocr_lang_arabic"),
                    "Simplified Chinese": self._t("ocr_lang_simplified_chinese"),
                    "Traditional Chinese": self._t("ocr_lang_traditional_chinese"),
                    "English": self._t("ocr_lang_english"),
                    "Japanese": self._t("ocr_lang_japanese"),
                    "Korean": self._t("ocr_lang_korean"),
                    "Spanish": self._t("ocr_lang_spanish"),
                    "French": self._t("ocr_lang_french"),
                    "German": self._t("ocr_lang_german"),
                    "Russian": self._t("ocr_lang_russian"),
                    "Portuguese": self._t("ocr_lang_portuguese"),
                    "Italian": self._t("ocr_lang_italian"),
                    "Thai": self._t("ocr_lang_thai"),
                    "Vietnamese": self._t("ocr_lang_vietnamese"),
                    "Indonesian": self._t("ocr_lang_indonesian"),
                    "Turkish": self._t("ocr_lang_turkish"),
                    "Polish": self._t("ocr_lang_polish"),
                    "Ukrainian": self._t("ocr_lang_ukrainian"),
                },
                "labels": {
                    "filter_text_enabled": self._t("label_filter_text_enabled"),
                    "kernel_size": self._t("label_kernel_size"),
                    "mask_dilation_offset": self._t("label_mask_dilation_offset"),
                    "translator": self._t("label_translator"),
                    "target_lang": self._t("label_target_lang"),
                    "keep_lang": self._t("label_keep_lang"),
                    "enable_streaming": self._t("label_enable_streaming"),
                    "no_text_lang_skip": self._t("label_no_text_lang_skip"),
                    "high_quality_prompt_path": self._t("label_high_quality_prompt_path"),
                    "extract_glossary": self._t("label_extract_glossary"),
                    "remove_trailing_period": self._t("label_remove_trailing_period"),
                    "convert_to_traditional": self._t("label_convert_to_traditional"),
                    "convert_to_simplified": self._t("label_convert_to_simplified"),
                    "use_custom_api_params": self._t("label_use_custom_api_params"),
                    "ocr": self._t("label_ocr"),
                    "use_hybrid_ocr": self._t("label_use_hybrid_ocr"),
                    "secondary_ocr": self._t("label_secondary_ocr"),
                    "min_text_length": self._t("label_min_text_length"),
                    "ignore_bubble": self._t("label_ignore_bubble"),
                    "use_model_bubble_filter": self._t("label_use_model_bubble_filter"),
                    "model_bubble_overlap_threshold": self._t("label_model_bubble_overlap_threshold"),
                    "use_model_bubble_repair_intersection": self._t("label_use_model_bubble_repair_intersection"),
                    "limit_mask_dilation_to_bubble_mask": self._t("label_limit_mask_dilation_to_bubble_mask"),
                    "prob": self._t("label_prob"),
                    "merge_gamma": self._t("label_merge_gamma"),
                    "merge_sigma": self._t("label_merge_sigma"),
                    "merge_edge_ratio_threshold": self._t("label_merge_edge_ratio_threshold"),
                    "merge_special_require_full_wrap": self._t("label_merge_special_require_full_wrap"),
                    "ai_ocr_concurrency": self._t("label_ai_ocr_concurrency"),
                    "ai_ocr_custom_prompt": self._t("label_ai_ocr_custom_prompt"),
                    "ocr_vl_language_hint": self._t("label_ocr_vl_language_hint"),
                    "ocr_vl_custom_prompt": self._t("label_ocr_vl_custom_prompt"),
                    "detector": self._t("label_detector"),
                    "detection_size": self._t("label_detection_size"),
                    "text_threshold": self._t("label_text_threshold"),
                    "import_yolo_labels": self._t("label_import_yolo_labels"),
                    "use_yolo_obb": self._t("label_use_yolo_obb"),
                    "yolo_obb_conf": self._t("label_yolo_obb_conf"),
                    "yolo_obb_overlap_threshold": self._t("label_yolo_obb_overlap_threshold"),
                    "box_threshold": self._t("label_box_threshold"),
                    "unclip_ratio": self._t("label_unclip_ratio"),
                    "min_box_area_ratio": self._t("label_min_box_area_ratio"),
                    "inpainter": self._t("label_inpainter"),
                    "inpainting_size": self._t("label_inpainting_size"),
                    "inpainting_precision": self._t("label_inpainting_precision"),
                    "force_use_torch_inpainting": self._t("label_force_use_torch_inpainting"),
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
                    "enable_template_alignment": self._t("label_enable_template_alignment"),
                    "paste_mask_dilation_pixels": self._t("label_paste_mask_dilation_pixels"),
                    "ai_renderer_concurrency": self._t("label_ai_renderer_concurrency"),
                    "direction": self._t("label_direction"),
                    "uppercase": self._t("label_uppercase"),
                    "lowercase": self._t("label_lowercase"),
                    "font_path": self._t("label_font_path"),
                    "no_hyphenation": self._t("label_no_hyphenation"),
                    "bubble_layout_english": self._t("label_bubble_layout_english"),
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
                    "ai_colorizer_history_pages": self._t("label_ai_colorizer_history_pages"),
                    "verbose": self._t("label_verbose"),
                    "attempts": self._t("label_attempts"),
                    "max_requests_per_minute": self._t("label_max_requests_per_minute"),
                    "ignore_errors": self._t("label_ignore_errors"),
                    "use_gpu": self._t("label_use_gpu"),
                    "disable_onnx_gpu": self._t("label_disable_onnx_gpu"),
                    "context_size": self._t("label_context_size"),
                    "format": self._t("label_format"),
                    "overwrite": self._t("label_overwrite"),
                    "skip_no_text": self._t("label_skip_no_text"),
                    "save_text": self._t("label_save_text"),
                    "load_text": self._t("label_load_text"),
                    "translate_json_only": self._t("label_translate_json_only"),
                    "template": self._t("label_template"),
                    "save_quality": self._t("label_save_quality"),
                    "batch_size": self._t("label_batch_size"),
                    "batch_concurrent": self._t("label_batch_concurrent"),
                    "generate_and_export": self._t("label_generate_and_export"),
                    "export_editable_psd": self._t("label_export_editable_psd"),
                    "last_output_path": self._t("label_last_output_path"),
                    "save_to_source_dir": self._t("label_save_to_source_dir"),
                    "psd_font": self._t("label_psd_font"),
                    "psd_script_only": self._t("label_psd_script_only"),
                    "line_spacing": self._t("label_line_spacing"),
                    "letter_spacing": self._t("label_letter_spacing"),
                    "font_size": self._t("label_font_size"),
                    "OPENAI_API_KEY": self._t("label_OPENAI_API_KEY"),
                    "OPENAI_MODEL": self._t("label_OPENAI_MODEL"),
                    "OPENAI_API_BASE": self._t("label_OPENAI_API_BASE"),
                    "OPENAI_GLOSSARY_PATH": self._t("label_OPENAI_GLOSSARY_PATH"),
                    "GEMINI_API_KEY": self._t("label_GEMINI_API_KEY"),
                    "GEMINI_MODEL": self._t("label_GEMINI_MODEL"),
                    "GEMINI_API_BASE": self._t("label_GEMINI_API_BASE"),
                    "OCR_OPENAI_API_KEY": self._t("label_OCR_OPENAI_API_KEY"),
                    "OCR_OPENAI_MODEL": self._t("label_OCR_OPENAI_MODEL"),
                    "OCR_OPENAI_API_BASE": self._t("label_OCR_OPENAI_API_BASE"),
                    "OCR_GEMINI_API_KEY": self._t("label_OCR_GEMINI_API_KEY"),
                    "OCR_GEMINI_MODEL": self._t("label_OCR_GEMINI_MODEL"),
                    "OCR_GEMINI_API_BASE": self._t("label_OCR_GEMINI_API_BASE"),
                    "COLOR_OPENAI_API_KEY": self._t("label_COLOR_OPENAI_API_KEY"),
                    "COLOR_OPENAI_MODEL": self._t("label_COLOR_OPENAI_MODEL"),
                    "COLOR_OPENAI_API_BASE": self._t("label_COLOR_OPENAI_API_BASE"),
                    "COLOR_GEMINI_API_KEY": self._t("label_COLOR_GEMINI_API_KEY"),
                    "COLOR_GEMINI_MODEL": self._t("label_COLOR_GEMINI_MODEL"),
                    "COLOR_GEMINI_API_BASE": self._t("label_COLOR_GEMINI_API_BASE"),
                    "RENDER_OPENAI_API_KEY": self._t("label_RENDER_OPENAI_API_KEY"),
                    "RENDER_OPENAI_MODEL": self._t("label_RENDER_OPENAI_MODEL"),
                    "RENDER_OPENAI_API_BASE": self._t("label_RENDER_OPENAI_API_BASE"),
                    "RENDER_GEMINI_API_KEY": self._t("label_RENDER_GEMINI_API_KEY"),
                    "RENDER_GEMINI_MODEL": self._t("label_RENDER_GEMINI_MODEL"),
                    "RENDER_GEMINI_API_BASE": self._t("label_RENDER_GEMINI_API_BASE"),
                    "SAKURA_API_BASE": self._t("label_SAKURA_API_BASE"),
                    "SAKURA_DICT_PATH": self._t("label_SAKURA_DICT_PATH"),
                    "CUSTOM_OPENAI_API_BASE": self._t("label_CUSTOM_OPENAI_API_BASE"),
                    "CUSTOM_OPENAI_MODEL": self._t("label_CUSTOM_OPENAI_MODEL"),
                    "CUSTOM_OPENAI_API_KEY": self._t("label_CUSTOM_OPENAI_API_KEY"),
                    "CUSTOM_OPENAI_MODEL_CONF": self._t("label_CUSTOM_OPENAI_MODEL_CONF")
                }
            }
        return display_name_maps.get(key)

    def get_options_for_key(self, key: str) -> Optional[List[str]]:
        options_map = {
            "format": [self._t("format_not_specified")] + [fmt for fmt in OUTPUT_FORMATS.keys() if fmt not in ['xcf', 'psd', 'pdf']],
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
            "keep_lang": ["none"] + list(self.translation_service.get_keep_languages().keys()),
            "detector": [member.value for member in Detector],
            "colorizer": [member.value for member in Colorizer],
            "inpainter": [member.value for member in Inpainter],
            "inpainting_precision": [member.value for member in InpaintPrecision],
            "ocr": [member.value for member in Ocr],
            "secondary_ocr": [member.value for member in Ocr],
            "ocr_vl_language_hint": [
                "auto",
                "multilingual",
                "Arabic",
                "Simplified Chinese",
                "Traditional Chinese",
                "English",
                "Japanese",
                "Korean",
                "Spanish",
                "French",
                "German",
                "Russian",
                "Portuguese",
                "Italian",
                "Thai",
                "Vietnamese",
                "Indonesian",
                "Turkish",
                "Polish",
                "Ukrainian",
            ],
        }
        return options_map.get(key)
    @pyqtSlot()
    def export_config(self):
        """导出配置（排除敏感信息）"""
        import json

        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        
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
            config_dict = config.model_dump()
            
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
        import json

        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        
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
            current_dict = current_config.model_dump()
            
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
            new_config = AppSettings.model_validate(current_dict)
            self.config_service.set_config(new_config)
            self.config_service.save_config_file()
            
            # 通知UI更新 - 与首次加载/重新加载保持一致，直接发原始 dump,
            # 避免 None → '不使用' 这类有损转换让下游 UI 的 None 判定失效
            self.config_loaded.emit(new_config.model_dump())
            
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
            
            # 尝试在 source_files 中找到匹配的路径（不区分大小写，处理路径分隔符）
            matched_path = None
            for source_path in self.source_files:
                if os.path.normpath(source_path).lower() == norm_file_path.lower():
                    matched_path = source_path
                    break
            
            # 情况1：直接在 source_files 中（文件夹或单独添加的文件）
            if matched_path:
                self.source_files.remove(matched_path)
                # 如果是文件，清理 file_to_folder_map
                if matched_path in self.file_to_folder_map:
                    del self.file_to_folder_map[matched_path]
                
                # 如果是文件夹，清理排除列表中该文件夹下的所有子文件夹
                if os.path.isdir(matched_path):
                    excluded_to_remove = set()
                    for excluded_folder in self.excluded_subfolders:
                        try:
                            # 检查 excluded_folder 是否在被删除的文件夹内
                            common = os.path.commonpath([matched_path, excluded_folder])
                            if common == os.path.normpath(matched_path):
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
            self._ui_log(f"移除路径时发生异常: {e}", "ERROR")

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
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.avif'}
        
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
    
    def start_file_scanning(self):
        """启动后台文件扫描任务"""
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在准备文件...")
        
        # ✅ 使用线程池运行扫描任务
        scanner_worker = FileScannerRunnable(
            source_files=self.source_files,
            excluded_subfolders=self.excluded_subfolders,
            file_service=self.file_service,
            finished_callback=self.on_scanning_finished,
            error_callback=self.on_scanning_error,
            progress_callback=self.on_worker_log
        )
        
        self.current_worker = scanner_worker
        
        # 使用普通线程启动
        thread = threading.Thread(target=scanner_worker.run, daemon=True)
        self.current_thread = thread
        thread.start()
        
        self._ui_log("文件扫描任务已启动")

    def on_scanning_finished(self, resolved_files, file_map, archive_map, excluded):
        """文件扫描完成，启动翻译任务"""
        self._ui_log(f"文件扫描完成，共找到 {len(resolved_files)} 个文件")
        
        # ✅ 清理worker引用
        self.current_worker = None
        
        # 更新状态
        # 此时我们需要合并旧的文件映射（如果有必要），但在这种重扫模式下，
        # worker返回的已经是全量数据的最新状态（除了单独添加的文件可能丢失原有映射关系）
        # FileScannerWorker 已处理了大部分映射，这里我们需要处理"单独文件保留旧映射"的逻辑
        # 但由于Worker中无法访问旧map，我们在Worker中对单独文件设为None。
        # 如果需要保留旧映射（例如单独添加的文件其实属于某个被移除的文件夹），
        # 这里的逻辑可能比较复杂。鉴于UI逻辑重构，我们暂时接受Worker的全新结果。
        
        self.file_to_folder_map = file_map
        self.archive_to_temp_map = archive_map
        self.excluded_subfolders = excluded
        
        # 检查文件列表是否为空
        if not resolved_files:
            self._ui_log("没有找到有效的图片文件，任务中止", "WARNING")
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("就绪")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                self._t("File List Empty"),
                self._t("Please add image files to translate!")
            )
            return

        # 启动真正的翻译任务
        self._start_translation_worker(resolved_files)

    def on_scanning_error(self, error_msg):
        self._ui_log(f"扫描文件时出错: {error_msg}", "ERROR")
        self.current_worker = None
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message("扫描失败")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "扫描失败", f"扫描文件时出错:\n{error_msg}")

    def _start_translation_worker(self, files_to_process):
        """启动翻译工作线程（内部方法，由扫描完成后调用）"""
        self.saved_files_count = 0
        self.saved_files_list = []
        self._reset_task_failures()
        
        # 生成新的任务ID
        self.current_task_id += 1
        task_id = self.current_task_id
        
        # ✅ 使用线程池运行翻译任务
        translation_worker = TranslationRunnable(
            files=files_to_process,
            config_dict=self.config_service.get_config().model_dump(),
            output_folder=self.config_service.get_config().app.last_output_path,
            root_dir=self.config_service.root_dir,
            file_to_folder_map=self.file_to_folder_map.copy(),
            finished_callback=lambda results: self.on_task_finished(results, task_id),
            error_callback=lambda error: self.on_task_error(error, task_id),
            progress_callback=self.on_task_progress,
            file_processed_callback=self.on_file_completed
        )
        
        self.current_worker = translation_worker
        
        # 使用普通线程启动
        thread = threading.Thread(target=translation_worker.run, daemon=True)
        self.current_thread = thread
        thread.start()
        
        self._ui_log(f"翻译任务已启动 (任务ID: {task_id})")
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在翻译...")

    def _resolve_input_files(self) -> List[str]:
        """
        DEPRECATED: Use FileScannerWorker instead.
        Kept for compatibility if needed, but logic moved to worker.
        """
        # ... logic ...
        return []

    def start_backend_task(self):
        """
        Resolves input paths and uses a 'Worker-to-Thread' model to start the translation task.
        """
        # 通过调用配置服务的 reload_config 方法，强制全面重新加载所有配置
        try:
            self._ui_log("即将开始后台任务，强制重新加载所有配置...")
            self.config_service.reload_config()
            self._ui_log("配置已刷新，继续执行任务。")
        except Exception as e:
            self._ui_log(f"重新加载配置时发生严重错误: {e}", "ERROR")

        # 检查是否有任务在运行
        if self.state_manager.is_translating():
            self._ui_log("一个任务已经在运行中。", "WARNING")
            return
        
        # ✅ 等待旧线程完全结束（防止ONNX Runtime冲突）
        if self.current_thread is not None and self.current_thread.is_alive():
            self._ui_log("等待上一个任务完全结束...")
            self.current_thread.join(timeout=3.0)  # 最多等3秒
            if self.current_thread.is_alive():
                self._ui_log("上一个任务未能在3秒内结束，强制继续", "WARNING")
            self.current_thread = None
            self.current_worker = None

        # 检查输出目录是否合法 (提前检查)
        output_path = self.config_service.get_config().app.last_output_path
        if not output_path or not os.path.isdir(output_path):
            self._ui_log(f"输出目录不合法: {output_path}", "WARNING")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                self._t("Invalid Output Directory"),
                self._t("Please set a valid output directory!")
            )
            return
            
        # 检查源文件列表是否为空 (初步检查，具体以扫描结果为准)
        if not self.source_files:
            self._ui_log("文件列表为空", "WARNING")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                self._t("File List Empty"),
                self._t("Please add image files to translate!")
            )
            return

        # 按当前所选功能精确校验 API Keys
        try:
            if not self._validate_runtime_api_requirements(self.config_service.get_config()):
                return
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox

            self._ui_log(f"API Keys 校验失败，已阻止开始翻译: {e}", "ERROR")
            QMessageBox.warning(
                None,
                self._t("API Keys Required"),
                self._t("Unable to validate API Keys (.env). Please check the log and try again."),
            )
            return

        # 启动后台文件扫描
        self.start_file_scanning()

    def on_task_finished(self, results, task_id):
        """处理任务完成信号，并根据需要保存批量任务的结果"""
        # 检查任务ID是否匹配，防止已停止的任务更新状态
        if task_id != self.current_task_id:
            return
        
        saved_files = []
        # The `results` list will only contain items from a batch job now.
        # Sequential jobs handle saving in `on_file_completed`.
        if results:
            self._ui_log(f"批量翻译任务完成，收到 {len(results)} 个结果。正在保存...")
            try:
                config = self.config_service.get_config()
                output_format = config.cli.format
                save_quality = config.cli.save_quality
                output_folder = config.app.last_output_path

                if not output_folder:
                    self._ui_log("输出目录未设置，无法保存文件。", "ERROR")
                    self.state_manager.set_status_message("错误：输出目录未设置！")
                else:
                    for result in results:
                        if result.get('success'):
                            # 检查是否有 output_path（批量模式下后端已保存）
                            if result.get('output_path'):
                                # 批量模式：直接使用后端保存的路径
                                translated_file = result.get('output_path')
                                saved_files.append(translated_file)
                            elif result.get('image_data') is None:
                                # 兼容旧代码：构造翻译后的图片路径
                                original_path = result.get('original_path')
                                effective_format = output_format
                                if not effective_format or effective_format == "不指定":
                                    effective_format = None
                                save_info = {
                                    'output_folder': output_folder,
                                    'format': effective_format,
                                    'save_to_source_dir': config.cli.save_to_source_dir
                                }
                                translated_file = self._calculate_output_path(original_path, save_info)

                                # 规范化路径，避免混合斜杠
                                translated_file = os.path.normpath(translated_file)
                                saved_files.append(translated_file)
                            else:
                                # This handles cases where a result with image_data is present in a batch
                                try:
                                    base_filename = os.path.splitext(os.path.basename(result['original_path']))[0]
                                    file_extension = f".{output_format}" if output_format and output_format != "不指定" else ".png"
                                    output_filename = f"{base_filename}_translated{file_extension}"
                                    final_output_path = os.path.join(output_folder, output_filename)
                                    os.makedirs(output_folder, exist_ok=True)
                                    
                                    image_to_save = result['image_data']
                                    self._save_image_with_source_metadata(
                                        image_to_save,
                                        final_output_path,
                                        result.get('original_path'),
                                        save_quality,
                                    )
                                    saved_files.append(final_output_path)
                                    self._ui_log(f"成功保存文件: {final_output_path}")
                                except Exception as e:
                                    self._ui_log(f"保存文件 {result['original_path']} 时出错: {e}", "ERROR")
                        else:
                            self._record_task_failure_from_result(result)
                 
                # In batch mode, the saved_files_count is the length of this list
                self.saved_files_count = len(saved_files)

            except Exception as e:
                self._ui_log(f"处理批量任务结果时发生严重错误: {e}", "ERROR")

        failed_count = len(self._task_failures)
        if failed_count > 0:
            self._ui_log(f"翻译任务完成。成功处理 {self.saved_files_count} 个文件，失败 {failed_count} 个文件。", "WARNING")
        else:
            self._ui_log(f"翻译任务完成。总共成功处理 {self.saved_files_count} 个文件。")
        
        # 对于顺序处理模式，使用累积的 saved_files_list
        if not saved_files and self.saved_files_list:
            saved_files = self.saved_files_list.copy()
        
        try:
            self.state_manager.set_translating(False)
            if failed_count > 0:
                self.state_manager.set_status_message(f"任务完成，成功处理 {self.saved_files_count} 个文件，失败 {failed_count} 个文件。")
            else:
                self.state_manager.set_status_message(f"任务完成，成功处理 {self.saved_files_count} 个文件。")
            
            # 重置主视图的进度条
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.reset_progress()
            
            # 播放系统提示音
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.beep()
            except Exception:
                pass
            
            # 使用列表副本发送信号，避免引用问题
            self.task_completed.emit(list(saved_files))
            if failed_count > 0:
                self.error_dialog_requested.emit(self._build_task_failure_dialog_message())
        except Exception as e:
            self._ui_log(f"完成任务状态更新或信号发射时发生致命错误: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        
        # 注意：将清理逻辑移出 finally 块，使用 QTimer 延迟执行
        # 这样可以确保信号有足够时间被主线程处理
        QTimer.singleShot(100, self._cleanup_after_task)
    
    def _cleanup_after_task(self):
        """延迟清理任务相关资源"""
        try:
            # 清理线程引用（线程应该已经通过deleteLater自动清理）
            # ✅ 线程池自动管理，无需手动清理线程
            
            # 清理压缩包解压的临时文件
            if hasattr(self, 'archive_to_temp_map') and self.archive_to_temp_map:
                try:
                    from desktop_qt_ui.utils.archive_extractor import (
                        cleanup_archive_temp,
                    )
                    for archive_path in list(self.archive_to_temp_map.keys()):
                        cleanup_archive_temp(archive_path)
                    self.archive_to_temp_map.clear()
                    self._ui_log("已清理压缩包临时文件")
                except Exception as cleanup_error:
                    self._ui_log(f"清理临时文件时出错: {cleanup_error}", "WARNING")

            # 翻译任务完成后释放 CUDA 缓存
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    self._ui_log("翻译完成后已调用 torch.cuda.empty_cache()", "DEBUG")
            except Exception as memory_cleanup_error:
                self._ui_log(f"调用 torch.cuda.empty_cache() 失败: {memory_cleanup_error}", "WARNING")
        except Exception as e:
            # 忽略 C++ 对象已删除的错误
            if "has been deleted" not in str(e):
                self._ui_log(f"清理任务资源时出错: {e}", "WARNING")
        finally:
            # ✅ 清理worker引用
            self.current_worker = None
    
    def on_task_error(self, error_message, task_id):
        # 检查任务ID是否匹配，防止已停止的任务更新状态
        if task_id != self.current_task_id:
            return
        
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message("任务失败")
        
        # 重置主视图的进度条
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.reset_progress()
        
        # 弹出错误提示框
        self.error_dialog_requested.emit(error_message)
        
        # 清理worker引用
        self.current_worker = None

    def on_task_progress(self, current, total, message):
        self._ui_log(f"[进度] {current}/{total}: {message}")
        percentage = (current / total) * 100 if total > 0 else 0
        self.state_manager.set_translation_progress(percentage)
        self.state_manager.set_status_message(f"[{current}/{total}] {message}")
        
        # 更新主视图的进度条
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.update_progress(current, total, message)

    def stop_task(self) -> bool:
        """停止翻译任务"""
        if self.current_worker and hasattr(self.current_worker, 'stop'):
            self._ui_log("正在请求停止任务...")
            self.state_manager.set_status_message("正在停止...")
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.set_stopping_state()
            
            # 增加任务ID，使旧任务的回调失效
            self.current_task_id += 1
            
            # 通知worker停止
            self.current_worker.stop()
            
            # ✅ 在后台线程中等待任务真正结束
            def wait_for_thread_finish():
                if self.current_thread and self.current_thread.is_alive():
                    self._ui_log("等待翻译进程结束...")
                    self.current_thread.join(timeout=10.0)  # 增加到10秒
                    if self.current_thread.is_alive():
                        self._ui_log("翻译进程未能在10秒内结束，继续等待...", "WARNING")
                        # 继续等待，直到线程真正结束
                        self.current_thread.join(timeout=30.0)  # 再等30秒
                        if self.current_thread.is_alive():
                            self._ui_log("翻译进程未能在40秒内结束，强制标记为已停止", "ERROR")
                        else:
                            self._ui_log("翻译进程已结束")
                    else:
                        self._ui_log("翻译进程已结束")
                
                # 在主线程中更新UI
                from PyQt6.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(
                    self,
                    "_finish_stop_task",
                    Qt.ConnectionType.QueuedConnection
                )
            
            # 在后台线程中等待
            wait_thread = threading.Thread(target=wait_for_thread_finish, daemon=True)
            wait_thread.start()
            
            return True
        
        self._ui_log("请求停止任务，但没有正在运行的任务", "WARNING")
        self.state_manager.set_translating(False)
        return False
    
    @pyqtSlot()
    def _finish_stop_task(self):
        """在主线程中完成停止任务的清理工作"""
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message("任务已停止")
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.reset_progress()
        self._cleanup_after_task()
        self.current_thread = None
        self.current_worker = None
    # endregion

    # region 应用生命周期
    def initialize(self) -> bool:
        try:
            # The config is already loaded at startup. We just need to ensure the UI
            # reflects the loaded state without triggering a full, blocking rebuild.
            
            # Get the already loaded config
            config = self.config_service.get_config()

            # Manually emit the signal to populate UI options
            self.config_loaded.emit(config.model_dump())

            # Manually emit the signal to update the output path display in the UI
            if config.app.last_output_path:
                self.output_path_updated.emit(config.app.last_output_path)
            
            # Ensure the config path is stored in the state manager
            default_config_path = self.config_service.get_default_config_path()
            if os.path.exists(default_config_path):
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, default_config_path)

            self.state_manager.set_app_ready(True)
            self.state_manager.set_status_message("就绪")
            self._ui_log("应用初始化完成")
            return True
        except Exception as e:
            self._ui_log(f"应用初始化异常: {e}", "ERROR")
            return False
    
    def shutdown(self):
        """应用关闭时的清理"""
        if self._shutdown_started:
            return

        self._shutdown_started = True

        try:
            if self.state_manager.is_translating() and self.current_worker:
                self._ui_log("应用关闭中，停止任务...")
                
                # 通知worker停止
                if hasattr(self.current_worker, 'stop'):
                    try:
                        self.current_worker.stop()
                    except Exception as e:
                        self._ui_log(f"停止worker时出错: {e}", "WARNING")
                
                # ✅ 等待线程完成（最多5秒）
                if self.current_thread and self.current_thread.is_alive():
                    self.current_thread.join(timeout=5.0)
                    if self.current_thread.is_alive():
                        self._ui_log("线程5秒内未完成任务", "WARNING")
                    else:
                        self._ui_log("所有任务已正常停止")
                
                self.current_thread = None
                self.current_worker = None
                self.state_manager.set_translating(False)
            
            # 关闭缩略图加载线程池
            try:
                from desktop_qt_ui.widgets.file_list_view import (
                    shutdown_thumbnail_executor,
                )
                shutdown_thumbnail_executor()
            except Exception:
                pass
            
            # 关闭轻量级修复器线程池
            try:
                from desktop_qt_ui.services.lightweight_inpainter import (
                    get_lightweight_inpainter,
                )
                inpainter = get_lightweight_inpainter()
                if inpainter:
                    inpainter.shutdown()
            except Exception:
                pass
            except Exception:
                pass
            
            if self.translation_service:
                pass
        except Exception as e:
            self._ui_log(f"应用关闭异常: {e}", "ERROR")
    # endregion

class FileScannerWorker(QObject):
    """
    Worker for scanning files and folders in a background thread.
    Replaces the synchronous _resolve_input_files method.
    """
    finished = pyqtSignal(list, dict, dict, set) # resolved_files, file_to_folder_map, archive_to_temp_map, excluded_subfolders
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, source_files, excluded_subfolders, file_service):
        super().__init__()
        self.source_files = source_files
        self.excluded_subfolders = excluded_subfolders.copy()
        self.file_service = file_service
        self.file_to_folder_map = {}
        self.archive_to_temp_map = {}

    def process(self):
        try:
            self.progress.emit("正在扫描文件...")
            resolved_files = []
            processed_archives = set()
             
            # 分离文件和文件夹
            folders = []
            individual_files = []
            archive_files = []
            
            for path in self.source_files:
                if os.path.isdir(path):
                    folders.append(path)
                elif os.path.isfile(path):
                    if self.file_service.is_archive_file(path):
                        archive_files.append(path)
                    elif self.file_service.validate_image_file(path):
                        individual_files.append(path)

            from desktop_qt_ui.utils.archive_extractor import (
                check_output_extract_conflict,
                clear_output_extract_root,
                extract_images_from_archive,
                get_output_extract_dir,
                write_output_extract_marker,
            )

            output_base_dir = ''
            overwrite_extract = True
            try:
                cfg = self.file_service.config_service.get_config()
                output_base_dir = cfg.app.last_output_path
                overwrite_extract = bool(getattr(cfg.cli, 'overwrite', True))
            except Exception:
                output_base_dir = ''
                overwrite_extract = True

            def _is_excluded(file_path: str) -> bool:
                if not self.excluded_subfolders:
                    return False
                for excluded_folder in self.excluded_subfolders:
                    try:
                        common = os.path.commonpath([excluded_folder, file_path])
                        if common == excluded_folder:
                            return True
                    except ValueError:
                        continue
                return False

            def _get_archive_output_base_dir(archive_path: str, scan_root: str = None) -> str:
                if not (output_base_dir and os.path.isdir(output_base_dir)):
                    return ''
                if not scan_root:
                    return output_base_dir

                archive_parent = os.path.normpath(os.path.dirname(archive_path))
                scan_root_norm = os.path.normpath(scan_root)
                try:
                    relative_parent = os.path.relpath(archive_parent, scan_root_norm)
                except ValueError:
                    return output_base_dir

                nested_base = os.path.join(output_base_dir, os.path.basename(scan_root_norm))
                if relative_parent != '.':
                    nested_base = os.path.join(nested_base, relative_parent)
                return os.path.normpath(nested_base)

            def _extract_archive(archive_path: str, scan_root: str = None) -> None:
                norm_archive = os.path.normcase(os.path.abspath(archive_path))
                if norm_archive in processed_archives:
                    return
                processed_archives.add(norm_archive)

                try:
                    self.progress.emit(f"正在解压: {os.path.basename(archive_path)}")
                    archive_output_base_dir = _get_archive_output_base_dir(archive_path, scan_root)
                    if archive_output_base_dir:
                        if check_output_extract_conflict(archive_output_base_dir, archive_path):
                            if not overwrite_extract:
                                self.progress.emit(
                                    f"跳过解压(同名冲突且未开启覆盖): {os.path.basename(archive_path)}"
                                )
                                return
                            clear_output_extract_root(archive_output_base_dir, archive_path)
                        extract_dir = get_output_extract_dir(archive_output_base_dir, archive_path)
                        images, extracted_dir = extract_images_from_archive(archive_path, extract_dir)
                        if images:
                            write_output_extract_marker(archive_output_base_dir, archive_path)
                    else:
                        images, extracted_dir = extract_images_from_archive(archive_path)

                    if images:
                        self.archive_to_temp_map[archive_path] = extracted_dir
                        for img_path in images:
                            resolved_files.append(img_path)
                            self.file_to_folder_map[img_path] = archive_path
                        self.progress.emit(f"从 {os.path.basename(archive_path)} 提取了 {len(images)} 张图片")
                    else:
                        self.progress.emit(f"警告: {os.path.basename(archive_path)} 中没有找到图片")
                except Exception as e:
                    self.progress.emit(f"解压 {os.path.basename(archive_path)} 失败: {e}")

            # 处理顶层压缩包文件
            for archive_path in archive_files:
                _extract_archive(archive_path)
            
            # 清理排除列表
            if self.excluded_subfolders:
                excluded_to_remove = set()
                for excluded_folder in self.excluded_subfolders:
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
                self.progress.emit(f"正在扫描文件夹: {os.path.basename(folder)}")
                # 获取文件夹中的所有图片
                folder_files = self.file_service.get_image_files_from_folder(folder, recursive=True)
                folder_archives = self.file_service.get_archive_files_from_folder(folder, recursive=True)
                 
                # 过滤掉被排除的子文件夹中的文件
                if self.excluded_subfolders:
                    folder_files = [f for f in folder_files if not _is_excluded(f)]
                    folder_archives = [f for f in folder_archives if not _is_excluded(f)]

                # 处理文件夹内的压缩包文件
                for archive_path in folder_archives:
                    _extract_archive(archive_path, folder)
                 
                resolved_files.extend(folder_files)
                # 记录这些文件来自这个文件夹
                for file_path in folder_files:
                    self.file_to_folder_map[file_path] = folder
            
            # 处理单独添加的文件
            individual_files.sort(key=self.file_service._natural_sort_key)
            for file_path in individual_files:
                resolved_files.append(file_path)
                # 单独添加的文件，映射为None（除非在MainAppLogic中有旧映射，但这里我们无法访问旧映射，
                # 不过MainAppLogic可以在接收结果时合并）
                self.file_to_folder_map[file_path] = None

            unique_files = list(dict.fromkeys(resolved_files))
            self.finished.emit(unique_files, self.file_to_folder_map, self.archive_to_temp_map, self.excluded_subfolders)
            
        except Exception as e:
            self.error.emit(str(e))


class TranslationWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
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
        self.logger = get_logger(__name__)
        self.file_service = get_file_service()
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _log(self, level: int, message: str):
        message = str(message).rstrip()
        if not message:
            return
        self.logger.log(level, message)

    def _log_info(self, message: str):
        self._log(logging.INFO, message)

    def _log_warning(self, message: str):
        self._log(logging.WARNING, message)

    def _log_error(self, message: str):
        self._log(logging.ERROR, message)

    def _get_context_value(self, ctx, key: str, default=None):
        if ctx is None:
            return default
        if isinstance(ctx, dict):
            return ctx.get(key, default)
        return getattr(ctx, key, default)

    def _normalize_error_summary(self, message: str, limit: int = 240) -> str:
        raw = str(message or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        summary = lines[0] if lines else ""
        if not summary:
            return "未记录详细错误"
        return textwrap.shorten(summary, width=limit, placeholder="...")

    def _extract_context_error_message(self, ctx) -> str:
        candidates = (
            "translation_error",
            "error",
            "critical_error_msg",
            "exception",
            "message",
        )
        for key in candidates:
            value = self._get_context_value(ctx, key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _build_batch_failure_log_message(self, failed_items: list[dict], total_failed: int) -> str:
        lines = [
            f"\n⚠️ 批量翻译完成：失败 {total_failed} 张"
        ]
        for item in failed_items[:5]:
            lines.append(f"- {item['file_name']}: {item['summary']}")
        remaining = total_failed - min(len(failed_items), 5)
        if remaining > 0:
            lines.append(f"- 另有 {remaining} 张失败，详细原因见上方单图日志")
        return "\n".join(lines)

    @staticmethod
    def _format_eta_duration(seconds: float) -> str:
        total_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分"
        if minutes > 0:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    def _build_eta_progress_message(
        self,
        completed_count: int,
        remaining_count: int,
        elapsed_seconds: float,
        skipped_count: int = 0,
        failed_count: int = 0,
        detail: str = "",
    ) -> str:
        parts = [detail] if detail else []
        if completed_count <= 0:
            if skipped_count > 0:
                parts.append(f"已跳过 {skipped_count} 张")
            if failed_count > 0:
                parts.append(f"已失败 {failed_count} 张")
            if remaining_count <= 0:
                parts.append("无需处理")
                return " | ".join(parts)
            parts.append("等待首张完成后估算剩余时间")
            return " | ".join(parts)

        average_seconds = elapsed_seconds / max(completed_count, 1)
        parts.append(f"均速 {average_seconds:.1f} 秒/张")
        parts.append(f"预计剩余 {self._format_eta_duration(average_seconds * max(remaining_count, 0))}")
        if skipped_count > 0:
            parts.append(f"已跳过 {skipped_count} 张")
        if failed_count > 0:
            parts.append(f"已失败 {failed_count} 张")
        return " | ".join(parts)
    
    def _calculate_output_path(self, image_path: str, save_info: dict) -> str:
        """
        计算输出文件的完整路径（用于预检查文件是否存在）
        
        Args:
            image_path: 输入图片的路径
            save_info: 包含输出配置的字典
                
        Returns:
            str: 计算后的输出文件完整路径
        """
        output_folder = save_info.get('output_folder')
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
            
            # 检查文件是否来自文件夹
            source_folder = self.file_to_folder_map.get(image_path)
            if source_folder:
                # 检查是否来自压缩包
                if self.file_service.is_archive_file(source_folder):
                    archive_output_dir = _resolve_archive_output_dir_from_extracted_image(
                        image_path, output_folder
                    )
                    if archive_output_dir:
                        final_output_dir = archive_output_dir
                    else:
                        archive_name = os.path.splitext(os.path.basename(source_folder))[0]
                        final_output_dir = os.path.join(output_folder, archive_name)
                else:
                    # 文件来自文件夹，保持相对路径结构
                    relative_path = os.path.relpath(parent_dir, source_folder)
                    # Normalize path and avoid adding '.' as a directory component
                    if relative_path == '.':
                        final_output_dir = os.path.join(output_folder, os.path.basename(source_folder))
                    else:
                        final_output_dir = os.path.join(output_folder, os.path.basename(source_folder), relative_path)
                final_output_dir = os.path.normpath(final_output_dir)
        
        # 处理输出文件名和格式
        base_filename, _ = os.path.splitext(os.path.basename(file_path))
        if output_format and output_format.strip() and output_format.lower() not in ['none', '不指定']:
            output_filename = f"{base_filename}.{output_format}"
        else:
            output_filename = os.path.basename(file_path)
        
        final_output_path = os.path.join(final_output_dir, output_filename)
        return final_output_path

    def stop(self):
        self._log_info("--- Stop request received.")
        self._is_running = False
        # 取消当前运行的异步任务
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        # 使用统一的内存清理模块
        try:
            from desktop_qt_ui.utils.memory_cleanup import full_memory_cleanup
            # 使用配置中的卸载模型开关
            unload_models = self.config_dict.get('app', {}).get('unload_models_after_translation', False)
            full_memory_cleanup(log_callback=self._log_info, unload_models=unload_models)
        except Exception as e:
            self._log_warning(f"--- [CLEANUP] Warning: Failed to cleanup: {e}")

    @staticmethod
    def _build_friendly_error_message(error_message: str, error_traceback: str) -> str:
        """
        根据错误信息构建友好的中文错误提示
        """
        def _wrap_error_text(text: str, width: int = 88) -> str:
            wrapped_lines = []
            for line in (text or "").splitlines():
                if not line:
                    wrapped_lines.append("")
                    continue
                wrapped_lines.extend(
                    textwrap.wrap(
                        line,
                        width=width,
                        break_long_words=True,
                        break_on_hyphens=False,
                    )
                    or [""]
                )
            return "\n".join(wrapped_lines)

        friendly_msg = ""
        
        # 如果是"达到最大尝试次数"的错误，提取真正的错误原因
        real_error = error_message
        if "达到最大尝试次数" in error_message and "最后一次错误:" in error_message:
            # 提取真正的错误原因
            try:
                real_error = error_message.split("最后一次错误:")[1].strip()
            except Exception:
                pass
        
        # 检查是否是AI断句检查失败
        if ("BR markers missing" in real_error or 
            "AI断句检查" in error_message or 
            "BRMarkersValidationException" in error_traceback or
            "_validate_br_markers" in error_traceback):
            friendly_msg += "🔍 错误原因：AI断句检查失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI翻译时未能正确添加断句标记 [BR]，导致多次重试后仍然失败。\n\n"
            friendly_msg += "解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 关闭「AI断句检查」选项（推荐）\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句检查\n"
            friendly_msg += "      - 说明：允许AI在少数情况下不添加断句标记\n\n"
            friendly_msg += "   2. 增加「重试次数」\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高（-1 表示无限重试）\n\n"
            friendly_msg += "   3. 更换翻译模型\n"
            friendly_msg += "      - 某些模型对断句标记的理解更好\n"
            friendly_msg += "      - 建议：尝试 gpt-5.2、gemini-3-pro 或 grok-4.2\n\n"
            friendly_msg += "   4. 关闭「AI断句」功能\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句\n"
            friendly_msg += "      - 说明：使用传统的自动换行（可能导致排版不够精确）\n\n"
            friendly_msg += "   5. 减小批量大小\n"
            friendly_msg += "      - 位置：高级设置 → 批量大小\n"
            friendly_msg += "      - 建议：将批量大小减小（如从 3 减到 1 或 2）\n"
            friendly_msg += "      - 说明：批量处理的文本越少，AI越容易正确添加断句标记\n\n"
        
        # 检查是否是翻译数量不匹配错误
        elif "翻译数量不匹配" in real_error or "Translation count mismatch" in real_error:
            friendly_msg += "🔍 错误原因：翻译数量不匹配\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI返回的翻译条数与原文条数不一致。\n"
            friendly_msg += "   这通常是因为AI将多条文本合并翻译，或漏掉了某些文本。\n\n"
            friendly_msg += "解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 增加「重试次数」（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高（-1 表示无限重试）\n"
            friendly_msg += "      - 说明：多次重试通常能让AI返回正确数量的翻译\n\n"
            friendly_msg += "   2. 更换翻译模型\n"
            friendly_msg += "      - 某些模型对指令的遵循能力更强\n"
            friendly_msg += "      - 建议：尝试 gpt-5.2、gemini-3-pro 或 grok-4.2\n\n"
            friendly_msg += "   3. 减小批量大小\n"
            friendly_msg += "      - 位置：高级设置 → 批量大小\n"
            friendly_msg += "      - 建议：将批量大小减小（如从 3 减到 1 或 2）\n"
            friendly_msg += "      - 说明：批量处理的文本越少，AI越不容易出错\n\n"
        
        # 检查是否是翻译质量检查失败
        elif "翻译质量检查失败" in real_error or "Quality check failed" in real_error:
            friendly_msg += "🔍 错误原因：翻译质量检查失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI返回的翻译存在质量问题，如空翻译、合并翻译或可疑符号。\n\n"
            friendly_msg += "解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 增加「重试次数」（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高（-1 表示无限重试）\n\n"
            friendly_msg += "   2. 更换翻译模型\n"
            friendly_msg += "      - 某些模型翻译质量更稳定\n"
            friendly_msg += "      - 建议：尝试 gpt-5.2、gemini-3-pro 或 grok-4.2\n\n"
            friendly_msg += "   3. 减小批量大小\n"
            friendly_msg += "      - 位置：高级设置 → 批量大小\n"
            friendly_msg += "      - 建议：将批量大小减小（如从 3 减到 1 或 2）\n"
            friendly_msg += "      - 说明：批量处理的文本越少，AI翻译质量越稳定\n\n"

        # 检查是否是 OpenAI/Gemini 空响应错误（统一处理）
        elif (
            (("NoneType" in real_error or "NoneType" in error_traceback) and
             ("strip" in real_error.lower() or "strip" in error_traceback.lower()))
            or ("returned empty content" in real_error.lower())
            or ("returned empty text" in real_error.lower())
            or ("响应text为空" in real_error)
        ):
            friendly_msg += "🔍 错误原因：AI接口返回空文本（空回）\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   当前请求没有返回可解析的文本内容（OpenAI/Gemini 都可能出现）。\n"
            friendly_msg += "   可能是触发了内容审核，或者服务器繁忙导致临时空回。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. ⭐ 更换模型（推荐）\n"
            friendly_msg += "      - OpenAI：gpt-5.2、gpt-5.2-mini\n"
            friendly_msg += "      - Gemini：gemini-3-pro、gemini-3-flash\n\n"
            friendly_msg += "   2. 更换站点（API地址）\n"
            friendly_msg += "      - Gemini 官方地址： https://generativelanguage.googleapis.com\n"
            friendly_msg += "      - OpenAI 官方地址： https://api.openai.com/v1\n"
            friendly_msg += "      - 若使用第三方中转，尝试更换服务商或改用官方 API\n\n"
            friendly_msg += "   3. 更换翻译图片的内容后再试\n"
            friendly_msg += "      - 避免敏感画面或高风险词汇，降低审核拦截概率\n\n"
            friendly_msg += "   4. 稍后重试（服务器繁忙时常见）\n\n"

        # 检查是否是模型不支持多模态
        elif ("不支持多模态" in real_error or
              "multimodal" in real_error.lower() or
              "vision" in real_error.lower() or
              "image_url" in real_error.lower() or
              "expected `text`" in real_error.lower() or
              "unknown variant" in real_error.lower()):
            friendly_msg += "🔍 错误原因：模型不支持多模态输入\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   当前使用的是「高质量翻译器」（OpenAI高质量翻译 或 Gemini高质量翻译），\n"
            friendly_msg += "   这些翻译器需要发送图片给AI进行分析，但当前模型不支持图片输入。\n\n"
            friendly_msg += "解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 切换到普通翻译器（推荐）\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 将「OpenAI高质量翻译」改为「OpenAI」\n"
            friendly_msg += "      - 将「Gemini高质量翻译」改为「Google Gemini」\n"
            friendly_msg += "      - 说明：普通翻译器不需要发送图片，只翻译文本\n\n"
            friendly_msg += "   2. 更换为支持多模态的模型\n"
            friendly_msg += "      - OpenAI: gpt-5.2、gpt-5.2-mini\n"
            friendly_msg += "      - Gemini: gemini-3-pro、gemini-3-flash\n"
            friendly_msg += "      - Grok: grok-4.2\n"
            friendly_msg += "      - 注意：DeepSeek模型不支持多模态\n\n"
        
        # 检查是否是模型不存在/模型名错误
        elif (
            "code=20012" in real_error.lower()
            or "model does not exist" in real_error.lower()
            or ("does not exist" in real_error.lower() and "model" in real_error.lower())
            or "model not found" in real_error.lower()
            or "invalid model" in real_error.lower()
            or "no such model" in real_error.lower()
            or "模型不存在" in real_error
            or "模型名称不存在" in real_error
        ):
            friendly_msg += "🔍 错误原因：模型不存在，或当前 API 站点不支持该模型\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API 已经连通，但服务端找不到你填写的模型名称。\n"
            friendly_msg += "   这通常是模型名拼写不对、大小写不一致、模型已下线，或当前中转/渠道并不提供这个模型。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. ⭐ 检查模型名称是否与服务商提供的名称完全一致（最常见）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → MODEL\n"
            friendly_msg += "      - 注意：模型名称通常区分大小写，不能省略前缀或版本号\n\n"
            friendly_msg += "   2. 使用「测试连接」或模型列表功能确认当前站点实际支持哪些模型\n"
            friendly_msg += "      - API管理 → 测试连接 / 获取模型列表\n"
            friendly_msg += "      - 先确认该站点真的提供你要用的模型\n\n"
            friendly_msg += "   3. 如果你用的是第三方 OpenAI 兼容站点（如中转、渠道、硅基流动等）\n"
            friendly_msg += "      - 不要假设它支持 OpenAI 官方的全部模型名\n"
            friendly_msg += "      - 需要改成该服务商自己的实际模型 ID\n\n"
            friendly_msg += "   4. 检查 API 地址和翻译器类型是否匹配\n"
            friendly_msg += "      - OpenAI 兼容接口应使用「OpenAI」或「OpenAI高质量」翻译器\n"
            friendly_msg += "      - 若站点和翻译器类型不匹配，也可能导致模型判断异常\n\n"
            friendly_msg += "   5. 若该模型最近改名、下线或迁移渠道\n"
            friendly_msg += "      - 访问对应服务商的模型广场或官方文档\n"
            friendly_msg += "      - 换成当前仍可用的模型名后再试\n\n"

        # 检查是否是404错误（API地址或模型配置错误）
        elif "API_404_ERROR" in real_error or "404" in real_error or "HTML错误页面" in real_error:
            friendly_msg += "🔍 错误原因：API返回404错误\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API返回了HTML格式的404错误页面，而不是正常的JSON响应。\n"
            friendly_msg += "   这通常意味着API地址错误或模型名称不存在。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. ⭐ 检查API地址配置（最常见）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_API_BASE\n"
            friendly_msg += "      - 正确格式：https://api.openai.com/v1\n"
            friendly_msg += "      - 注意：地址末尾必须是 /v1，不要多加或少加路径\n\n"
            friendly_msg += "   2. 检查模型名称是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_MODEL\n"
            friendly_msg += "      - 确认模型名称与API服务提供的模型完全匹配\n"
            friendly_msg += "      - 注意：模型名称区分大小写\n"
            friendly_msg += "      - 提示：可以使用「测试连接」功能查看可用模型列表\n\n"
            friendly_msg += "   3. 如果使用自定义API（如中转API、第三方服务）\n"
            friendly_msg += "      - 确认中转服务的API地址格式\n"
            friendly_msg += "      - 确认中转服务支持你使用的模型\n"
            friendly_msg += "      - 联系中转服务提供商确认配置\n\n"
        
        # 检查是否是API密钥错误
        elif "api key" in real_error.lower() or "authentication" in real_error.lower() or "unauthorized" in real_error.lower() or "401" in real_error:
            friendly_msg += "🔍 错误原因：API密钥验证失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API密钥无效、过期或未正确配置。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. 检查API密钥是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量配置区域\n"
            friendly_msg += "      - 确认密钥没有多余的空格或换行\n\n"
            friendly_msg += "   2. 验证API密钥是否有效\n"
            friendly_msg += "      - OpenAI: https://platform.openai.com/api-keys\n"
            friendly_msg += "      - Gemini: https://aistudio.google.com/app/apikey\n\n"
            friendly_msg += "   3. 检查API额度是否用完\n"
            friendly_msg += "      - 登录对应平台查看余额和使用情况\n\n"
        
        # 检查是否是网络连接错误
        elif (
            "connection" in real_error.lower()
            or "connect" in real_error.lower()
            or "failed to connect" in real_error.lower()
            or "could not connect to server" in real_error.lower()
            or "connection timed out" in real_error.lower()
            or "timed out after" in real_error.lower()
            or "连接" in real_error
            or "timeout" in real_error.lower()
            or "超时" in real_error
            or "network" in real_error.lower()
            or "网络" in real_error
            or "curl: (7)" in real_error.lower()
            or "curl: (28)" in real_error.lower()
            or "host" in real_error.lower()
            or "hostname" in real_error.lower()
            or "dns" in real_error.lower()
            or "getaddrinfo" in real_error.lower()
            or "failed to resolve" in real_error.lower()
            or "temporary failure in name resolution" in real_error.lower()
            or "name or service not known" in real_error.lower()
            or "no address associated with hostname" in real_error.lower()
            or "nodename nor servname provided" in real_error.lower()
            or "主机" in real_error
            or "解析" in real_error
        ):
            friendly_msg += "🔍 错误原因：网络连接或 Host 解析失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   无法连接到API服务器，可能是网络异常、超时，或 Host / DNS 解析失败。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. 检查网络连接\n"
            friendly_msg += "      - 确认电脑可以正常访问互联网\n\n"
            friendly_msg += "   2. 尝试开启 TUN（虚拟网卡模式）\n"
            friendly_msg += "      - 某些代理环境下，开启 TUN 后域名解析会更稳定\n\n"
            friendly_msg += "   3. 检查API地址是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - 默认值：https://api.openai.com/v1\n\n"
        
        # 检查是否是速率限制错误
        elif "rate limit" in real_error.lower() or "429" in real_error or "too many requests" in real_error.lower():
            friendly_msg += "🔍 错误原因：API请求被拒绝 (HTTP 429)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   HTTP 429 错误有多种可能原因：\n"
            friendly_msg += "   • API密钥错误或无效\n"
            friendly_msg += "   • 账户余额不足或欠费\n"
            friendly_msg += "   • 请求速率超过限制（RPM/TPM）\n"
            friendly_msg += "   • 当前账户级别不支持该模型\n\n"
            friendly_msg += "解决方案（按顺序检查）：\n"
            friendly_msg += "   1. ⭐ 检查API密钥是否正确（最常见）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量配置区域\n"
            friendly_msg += "      - 确认密钥没有多余的空格或换行\n"
            friendly_msg += "      - 使用「测试连接」功能验证密钥是否有效\n\n"
            friendly_msg += "   2. 检查账户余额和状态\n"
            friendly_msg += "      - OpenAI: https://platform.openai.com/usage\n"
            friendly_msg += "      - Gemini: https://aistudio.google.com/app/apikey\n"
            friendly_msg += "      - 确认账户余额充足且未欠费\n"
            friendly_msg += "      - 确认账户状态正常（未被限制）\n\n"
            friendly_msg += "   3. 检查模型是否支持\n"
            friendly_msg += "      - 某些模型需要特定的账户级别或付费套餐\n"
            friendly_msg += "      - 例如：GPT-4 需要付费账户，免费账户只能用 GPT-3.5\n"
            friendly_msg += "      - 尝试更换为账户支持的模型\n\n"
            friendly_msg += "   4. 降低请求速率\n"
            friendly_msg += "      - 位置：通用设置 → 每分钟最大请求数\n"
            friendly_msg += "      - 建议：设置为 3-10（取决于API套餐）\n"
            friendly_msg += "      - 免费账户建议设置为 3\n\n"
            friendly_msg += "   5. 稍后重试\n"
            friendly_msg += "      - 等待几分钟后再次尝试翻译\n\n"
            friendly_msg += "   6. 升级API套餐\n"
            friendly_msg += "      - 联系API提供商升级到更高级别的套餐\n\n"
        
        # 检查是否是403禁止访问错误
        elif "403" in real_error or "forbidden" in real_error.lower():
            friendly_msg += "🔍 错误原因：访问被拒绝 (HTTP 403)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   服务器拒绝访问，可能是权限不足或地区限制。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. 检查API密钥权限\n"
            friendly_msg += "      - 确认API密钥有访问该服务的权限\n\n"
            friendly_msg += "   2. 检查账户状态\n"
            friendly_msg += "      - 确认账户未被封禁或限制\n\n"

        
        # 检查是否是404未找到错误
        elif "404" in real_error or "not found" in real_error.lower():
            friendly_msg += "🔍 错误原因：资源未找到 (HTTP 404)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   请求的API端点不存在或模型名称错误。\n"
            friendly_msg += "   也可能是翻译器类型与API地址不匹配。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. ⭐ 检查翻译器类型是否匹配API地址（最常见）\n"
            friendly_msg += "      - 如果API地址是 xxxx/v1 格式（OpenAI兼容接口）\n"
            friendly_msg += "        → 应选择「OpenAI」或「OpenAI高质量」翻译器\n"
            friendly_msg += "      - 如果使用 Gemini 官方 API (generativelanguage.googleapis.com)\n"
            friendly_msg += "        → 应选择「Gemini」或「Gemini高质量」翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n\n"
            friendly_msg += "   2. 检查API地址是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - OpenAI默认：https://api.openai.com/v1\n"
            friendly_msg += "      - Gemini默认：https://generativelanguage.googleapis.com\n"
            friendly_msg += "      - 注意：地址末尾的 /v1 不要多加或少加\n\n"
            friendly_msg += "   3. 检查模型名称\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → MODEL\n"
            friendly_msg += "      - 确认模型名称拼写正确（如 gpt-5.2 不是 gpt52）\n"
            friendly_msg += "      - 使用「测试连接」功能查看可用模型列表\n\n"
            friendly_msg += "   4. 验证模型可用性\n"
            friendly_msg += "      - 某些模型可能已下线或更名\n"
            friendly_msg += "      - 访问官方文档查看可用模型列表\n\n"
        
        # 检查是否是500服务器错误
        elif "500" in real_error or "internal server error" in real_error.lower():
            friendly_msg += "🔍 错误原因：服务器内部错误 (HTTP 500)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API服务器遇到内部错误，这通常是临时问题。\n\n"
            friendly_msg += "解决方案：\n"
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
        elif any(code in real_error for code in ["502", "503", "504"]) or "bad gateway" in real_error.lower() or "service unavailable" in real_error.lower() or "gateway timeout" in real_error.lower():
            error_code = "502/503/504"
            if "502" in real_error:
                error_code = "502"
            elif "503" in real_error:
                error_code = "503"
            elif "504" in real_error:
                error_code = "504"
            
            friendly_msg += f"🔍 错误原因：网关/服务不可用 (HTTP {error_code})\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   - 502: 网关接收到无效响应\n"
            friendly_msg += "   - 503: 服务暂时不可用（通常是维护或过载）\n"
            friendly_msg += "   - 504: 网关超时\n\n"
            friendly_msg += "解决方案：\n"
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
        elif "content filter" in real_error.lower() or "content_filter" in real_error:
            friendly_msg += "🔍 错误原因：内容被安全策略拦截\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI检测到内容可能违反使用政策。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. 检查图片内容\n"
            friendly_msg += "      - 某些敏感内容可能被API拒绝处理\n\n"
            friendly_msg += "   2. 更换翻译器\n"
            friendly_msg += "      - 尝试使用其他翻译器（如 Gemini、DeepL）\n\n"
            friendly_msg += "   3. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 有时重试可以解决临时的过滤问题\n\n"
        
        # 检查是否是语言不支持错误
        elif "language not supported" in real_error.lower() or "LanguageUnsupportedException" in error_traceback:
            friendly_msg += "🔍 错误原因：翻译器不支持当前语言\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. 更换翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 建议：使用支持更多语言的翻译器（如 OpenAI、Gemini）\n\n"
            friendly_msg += "   2. 检查目标语言设置\n"
            friendly_msg += "      - 位置：翻译设置 → 目标语言\n"
            friendly_msg += "      - 确认选择的语言被当前翻译器支持\n\n"
        
        # 检查是否是请求被拦截错误
        elif "blocked" in real_error.lower() or "request was blocked" in real_error.lower():
            friendly_msg += "🔍 错误原因：请求被API服务商拦截\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API服务商（可能是第三方中转）拦截了你的请求。\n"
            friendly_msg += "   这通常是中转服务的反滥用机制或内容审核导致的。\n\n"
            friendly_msg += "解决方案：\n"
            friendly_msg += "   1. ⭐ 更换API服务商（推荐）\n"
            friendly_msg += "      - 如果使用第三方中转API，尝试更换其他服务商\n"
            friendly_msg += "      - 或者使用官方API（如 api.openai.com）\n\n"
            friendly_msg += "   2. 切换到普通翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 将 openai_hq 改为 openai（不发送图片）\n"
            friendly_msg += "      - 某些中转服务不支持多模态（图片+文本）请求\n\n"
            friendly_msg += "   3. 检查API密钥状态\n"
            friendly_msg += "      - 确认API密钥未被封禁或限制\n"
            friendly_msg += "      - 联系API服务商确认账户状态\n\n"
        
        # 通用错误
        else:
            friendly_msg += "🔍 错误原因：\n"
            friendly_msg += f"   {error_message}\n\n"
            friendly_msg += "通用解决方案：\n"
            friendly_msg += "   1. 检查配置是否正确\n"
            friendly_msg += "      - 翻译器、API密钥、模型名称等\n\n"
            friendly_msg += "   2. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n\n"
            friendly_msg += "   3. 查看详细日志\n"
            friendly_msg += "      - 在日志框中查找更多错误信息\n\n"
        
        friendly_msg += "📋 原始错误信息：\n"
        friendly_msg += f"{_wrap_error_text(error_message)}\n"
        if error_traceback and "Traceback" in error_traceback:
            # 只保留API详细错误信息（不保留代码路径）
            lines = error_traceback.split('\n')
            api_error_lines = []
            
            for line in lines:
                # 只保留API错误信息行（包含详细的错误内容）
                if line.strip() and any(keyword in line for keyword in ['BadRequest', 'Error code:', "'error':", "'message':", "{'error':"]):
                    api_error_lines.append(line.strip())
            
            if api_error_lines:
                friendly_msg += "\n"
                friendly_msg += _wrap_error_text('\n'.join(api_error_lines)) + "\n"

        for marker in ("🔍 ", "📝 ", "📋 "):
            friendly_msg = friendly_msg.replace(marker, "")

        return friendly_msg

    async def _do_processing(self):
        manga_logger = logging.getLogger('manga_translator')
        
        # 根据 verbose 配置设置日志级别
        verbose = self.config_dict.get('cli', {}).get('verbose', False)
        log_level = logging.DEBUG if verbose else logging.INFO
        manga_logger.setLevel(log_level)
        
        # 根日志器设为 DEBUG 以允许所有日志通过
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # 文件处理器始终为 DEBUG，其他处理器根据 verbose 设置
        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.setLevel(logging.DEBUG)  # 文件日志始终 DEBUG
            else:
                handler.setLevel(log_level)  # 控制台根据 verbose 设置

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

            self._log_info("--- 正在初始化翻译器...")
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
            self._log_info("--- 翻译器初始化完成")
            
            # 注册进度钩子，接收后端的批次进度
            progress_signal = self.progress  # 捕获信号引用
            progress_context = {
                "offset": 0,
                "overall_total": 0,
                "processing_started_at": None,
                "use_backend_hook": True,
                "batch_concurrent": False,
                "detail": "处理中",
                "failed_count": 0,
            }

            def emit_eta_progress(current: int, total: int, detail: str | None = None):
                total = max(int(total or 0), 0)
                current = max(0, min(int(current or 0), total)) if total > 0 else 0
                elapsed_seconds = 0.0
                if progress_context["processing_started_at"] is not None:
                    elapsed_seconds = max(0.0, time.perf_counter() - progress_context["processing_started_at"])
                completed_count = max(0, current - progress_context["offset"])
                remaining_count = max(0, total - current)
                message = self._build_eta_progress_message(
                    completed_count=completed_count,
                    remaining_count=remaining_count,
                    elapsed_seconds=elapsed_seconds,
                    skipped_count=progress_context["offset"],
                    failed_count=progress_context["failed_count"],
                    detail=detail if detail is not None else progress_context["detail"],
                )
                progress_signal.emit(current, total, message)
            
            async def progress_hook(state: str, finished: bool):
                try:
                    if not progress_context["use_backend_hook"]:
                        return
                    if state.startswith("batch:"):
                        # 解析批次进度: "batch:start:end:total[:failed]"
                        parts = state.split(":")
                        if len(parts) >= 4:
                            batch_end = int(parts[2])
                            total = int(parts[3])
                            failed_count = progress_context["failed_count"]
                            if len(parts) >= 5:
                                try:
                                    failed_count = max(0, int(parts[4]))
                                except (TypeError, ValueError):
                                    failed_count = progress_context["failed_count"]
                            progress_context["failed_count"] = failed_count
                            if progress_context["batch_concurrent"]:
                                batch_end += progress_context["offset"]
                                total = progress_context["overall_total"] or (total + progress_context["offset"])
                            else:
                                total = progress_context["overall_total"] or total
                            emit_eta_progress(batch_end, total)
                except Exception:
                    pass  # 忽略进度更新错误，不影响翻译流程
            
            translator.add_progress_hook(progress_hook)

            explicit_keys = {'render', 'upscale', 'translator', 'detector', 'colorizer', 'inpainter', 'ocr'}
            remaining_config = {
                k: v for k, v in self.config_dict.items() 
                if k in Config.model_fields and k not in explicit_keys
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
                    self._log_warning(f"--- WARNING: High quality prompt file not found at {full_prompt_path}")
            
            # 转换超分倍数：'不使用' -> None, '2'/'4' -> int
            upscale_config_data = self.config_dict.get('upscale', {}).copy()
            if 'upscale_ratio' in upscale_config_data:
                ratio_value = upscale_config_data['upscale_ratio']
                if ratio_value == '不使用' or ratio_value is None:
                    upscale_config_data['upscale_ratio'] = None
                elif isinstance(ratio_value, str) and ratio_value in ('x2', 'x4', 'DAT2 x4'):
                    # mangajanai 的字符串选项，直接保留
                    upscale_config_data['upscale_ratio'] = ratio_value
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
            self._log_info("--- 配置对象创建完成")

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
                'input_folders': input_folders,
                'save_to_source_dir': self.config_dict.get('cli', {}).get('save_to_source_dir', False)
            }

            # Filter out existing files if overwrite is False
            original_files = self.files
            skipped_files = []
            files_to_process = []
            
            # 获取 cli_config（用于检查特殊模式）
            cli_config = self.config_dict.get('cli', {})
            
            if not save_info['overwrite']:
                self._log_info("--- 🔍 检查已存在的文件（覆盖检测已禁用）...")
                self.logger.info("检查已存在的文件（覆盖检测已禁用）")
                
                for file_path in self.files:
                    try:
                        should_skip = False
                        
                        # 检查导出原文/翻译的TXT文件（如果启用）
                        if cli_config.get('translate_json_only', False):
                            from manga_translator.utils.path_manager import (
                                get_original_txt_path,
                            )
                            txt_path = get_original_txt_path(file_path, create_dir=False)
                            if not os.path.exists(txt_path):
                                should_skip = True
                        elif cli_config.get('template', False) and cli_config.get('save_text', False):
                            # 导出原文模式 - 检查TXT文件
                            from manga_translator.utils.path_manager import (
                                get_original_txt_path,
                            )
                            txt_path = get_original_txt_path(file_path, create_dir=False)
                            if os.path.exists(txt_path):
                                should_skip = True
                        elif cli_config.get('generate_and_export', False):
                            # 导出翻译模式 - 检查TXT文件
                            from manga_translator.utils.path_manager import (
                                get_translated_txt_path,
                            )
                            txt_path = get_translated_txt_path(file_path, create_dir=False)
                            if os.path.exists(txt_path):
                                should_skip = True
                        else:
                            # 普通翻译模式 - 检查图片文件
                            output_path = self._calculate_output_path(file_path, save_info)
                            if os.path.exists(output_path):
                                should_skip = True
                        
                        if should_skip:
                            skipped_files.append(file_path)
                            results.append({'success': True, 'original_path': file_path, 'image_data': None, 'skipped': True})
                        else:
                            files_to_process.append(file_path)
                    except Exception as e:
                        # If check fails, assume it needs processing
                        self.logger.error(f"检查文件时出错 {file_path}: {e}")
                        files_to_process.append(file_path)
                
                if skipped_files:
                    skip_msg = self._t("⏭️ Skipped {count} existing files.", count=len(skipped_files))
                    self._log_info(skip_msg)
                    self._log_info("--- ℹ️ 跳过的文件将不会被处理，如需重新翻译请启用「覆盖已存在文件」选项")
                    self.logger.info(f"已跳过 {len(skipped_files)} 个已存在的文件（覆盖检测已禁用）")
                    # Update files list to only include those needing processing
                    self.files = files_to_process
                else:
                    self._log_info("--- ✅ 未发现已存在的文件，将处理所有文件")
                    self.logger.info("未发现已存在的文件，将处理所有文件")
            
            # Update total count for progress bar logic
            total_original_count = len(original_files)
            skipped_count = len(skipped_files)
            
            # 确定翻译流程模式
            workflow_mode = self._t("Normal Translation")
            workflow_tip = ""
            cli_config = self.config_dict.get('cli', {})
            if cli_config.get('upscale_only', False):
                workflow_mode = self._t("Upscale Only")
                workflow_tip = self._t("Tip: Only upscale images, no detection, OCR, translation or rendering")
            elif cli_config.get('colorize_only', False):
                workflow_mode = self._t("Colorize Only")
                workflow_tip = self._t("Tip: Only colorize images, no detection, OCR, translation or rendering")
            elif cli_config.get('generate_and_export', False):
                workflow_mode = self._t("Export Translation")
                workflow_tip = self._t("Tip: After exporting, check manga_translator_work/translations/ for imagename_translated.txt files")
            elif cli_config.get('template', False):
                workflow_mode = self._t("Export Original Text")
                workflow_tip = self._t("Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode")
            elif cli_config.get('load_text', False):
                workflow_mode = self._t("Import Translation and Render")
                workflow_tip = self._t("Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)")
            elif cli_config.get('translate_json_only', False):
                workflow_mode = self._t("Translate JSON Only")
                workflow_tip = self._t("Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success")
                 
                # TXT导入JSON的预处理已经统一到翻译器入口（manga_translator.py），这里不再需要

            # 检查是否启用并发模式
            batch_concurrent = self.config_dict.get('cli', {}).get('batch_concurrent', False)
            
            # 检查是否有不兼容并行的特殊模式
            load_text = self.config_dict.get('cli', {}).get('load_text', False)
            translate_json_only = self.config_dict.get('cli', {}).get('translate_json_only', False)
            template = self.config_dict.get('cli', {}).get('template', False)
            save_text = self.config_dict.get('cli', {}).get('save_text', False)
            generate_and_export = self.config_dict.get('cli', {}).get('generate_and_export', False)
            colorize_only = self.config_dict.get('cli', {}).get('colorize_only', False)
            upscale_only = self.config_dict.get('cli', {}).get('upscale_only', False)
            inpaint_only = self.config_dict.get('cli', {}).get('inpaint_only', False)
            replace_translation = self.config_dict.get('cli', {}).get('replace_translation', False)
            
            is_template_save_mode = template and save_text
            has_incompatible_mode = (
                load_text or 
                translate_json_only or
                is_template_save_mode or 
                generate_and_export or 
                colorize_only or 
                upscale_only or 
                inpaint_only or
                replace_translation
            )
            
            # 如果有不兼容模式，强制禁用并行
            if batch_concurrent and has_incompatible_mode:
                incompatible_modes = []
                if load_text:
                    incompatible_modes.append("导入翻译")
                if translate_json_only:
                    incompatible_modes.append("仅翻译(JSON)")
                if is_template_save_mode:
                    incompatible_modes.append("导出原文")
                if generate_and_export:
                    incompatible_modes.append("导出翻译")
                if colorize_only:
                    incompatible_modes.append("仅上色")
                if upscale_only:
                    incompatible_modes.append("仅超分")
                if inpaint_only:
                    incompatible_modes.append("仅修复")
                if replace_translation:
                    incompatible_modes.append("替换翻译")
                
                self._log_warning(f"⚠️  并发流水线已禁用：当前模式 [{', '.join(incompatible_modes)}] 不支持并发处理")
                batch_concurrent = False

            progress_context["offset"] = skipped_count
            progress_context["overall_total"] = total_original_count
            progress_context["batch_concurrent"] = batch_concurrent
            progress_context["failed_count"] = 0
            if is_hq or (len(self.files) > 0 and batch_size > 1):
                self._log_info(f"--- 开始批量处理 ({'高质量模式' if is_hq else '批量模式'})")

                # 输出批量处理信息
                # total_images is the number of files to process
                total_images = len(self.files)
                
                # 如果启用并发模式，不分批加载（并发流水线内部会按需加载）
                if batch_concurrent:
                    progress_context["detail"] = "并发处理中"
                    self._log_info(self._t("📊 Concurrent pipeline mode: {total} images (Total: {orig})", total=total_images, orig=total_original_count))
                    self._log_info(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
                    self._log_info(self._t("📁 Output directory: {dir}", dir=self.output_folder))
                    if workflow_tip:
                        self._log_info(workflow_tip)
                    self._log_info(self._t("🚀 Starting translation..."))
                    
                    # 初始化进度条 (start from skipped_count)
                    emit_eta_progress(skipped_count, total_original_count, "并发处理中")
                    if total_images > 0:
                        progress_context["processing_started_at"] = time.perf_counter()
                    
                    if total_images > 0:
                        # 并发模式：直接传递所有文件路径，不预加载图片
                        images_with_configs = [(file_path, config) for file_path in self.files]
                        
                        # 调用翻译（并发流水线会自动处理）
                        all_contexts = await translator.translate_batch(
                            images_with_configs,
                            save_info=save_info,
                            global_offset=skipped_count,
                            global_total=total_original_count
                        )
                    else:
                        all_contexts = []
                else:
                    progress_context["detail"] = "批量处理中"
                    # 非并发模式：和并发模式一样直接把路径交给后端，由后端按 batch_size 控制加载
                    # 计算后端总批次数（用于显示统一的进度）
                    # Note: This is an estimation for logging purposes
                    backend_total_batches = (total_images + batch_size - 1) // batch_size if batch_size > 0 else total_images
                    
                    # 显示批量处理信息
                    if skipped_count > 0:
                        self._log_info(self._t("📊 Batch processing mode: {total} images in {batches} batches", total=total_images, batches=backend_total_batches))
                        self._log_info(f"--- ℹ️ 另有 {skipped_count} 个文件已跳过（原始总数：{total_original_count}）")
                    else:
                        self._log_info(self._t("📊 Batch processing mode: {total} images in {batches} batches", total=total_images, batches=backend_total_batches))
                    
                    self._log_info(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
                    self._log_info(self._t("📁 Output directory: {dir}", dir=self.output_folder))
                    if workflow_tip:
                        self._log_info(workflow_tip)

                    # 交给后端按 batch_size 懒加载并处理
                    self._log_info(self._t("🚀 Starting translation..."))
                    
                    # 初始化进度条
                    emit_eta_progress(skipped_count, total_original_count, "批量处理中")
                    if total_images > 0:
                        progress_context["processing_started_at"] = time.perf_counter()
                    
                    if total_images > 0:
                        images_with_configs = [(file_path, config) for file_path in self.files]
                        all_contexts = await translator.translate_batch(
                            images_with_configs,
                            save_info=save_info,
                            global_offset=skipped_count,
                            global_total=total_original_count
                        )
                    else:
                        all_contexts = []
                
                # 并发模式和非并发模式都会到这里
                contexts = all_contexts

                # The backend now handles saving for batch jobs. We just need to collect the paths/status.
                success_count = 0
                failed_count = 0
                failed_items = []
                for ctx in contexts:
                    if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                    if ctx:
                        image_name = self._get_context_value(ctx, 'image_name', 'Unknown') or 'Unknown'
                        file_name = os.path.basename(image_name)
                        # 检查是否有翻译错误
                        error_message = self._extract_context_error_message(ctx)
                        error_summary = self._normalize_error_summary(error_message)
                        if error_message:
                            results.append({'success': False, 'original_path': image_name, 'error': error_message})
                            failed_count += 1
                            failed_items.append({'file_name': file_name, 'summary': error_summary})
                            self._log_warning(f"\n⚠️ 图片 {file_name} 翻译失败：{error_summary}")
                            self._log_error(error_message)
                        elif self._get_context_value(ctx, 'success'):
                            # 优先检查success标志（因为result可能被清理了）
                            # 计算后端保存的文件路径
                            output_path = self._calculate_output_path(image_name, save_info)
                            results.append({'success': True, 'original_path': image_name, 'image_data': None, 'output_path': output_path})
                            success_count += 1
                        elif self._get_context_value(ctx, 'result'):
                            output_path = self._calculate_output_path(image_name, save_info)
                            results.append({'success': True, 'original_path': image_name, 'image_data': None, 'output_path': output_path})
                            success_count += 1
                        else:
                            fallback_error = "翻译结果为空"
                            results.append({'success': False, 'original_path': image_name, 'error': fallback_error})
                            failed_count += 1
                            failed_items.append({'file_name': file_name, 'summary': fallback_error})
                            self._log_warning(f"\n⚠️ 图片 {file_name} 翻译失败：{fallback_error}")
                    else:
                        fallback_error = 'Batch translation returned no context'
                        results.append({'success': False, 'original_path': 'Unknown', 'error': fallback_error})
                        failed_count += 1
                        failed_items.append({'file_name': 'Unknown', 'summary': fallback_error})
                        self._log_warning(f"\n⚠️ 图片 Unknown 翻译失败：{fallback_error}")

                if failed_count > 0:
                    self._log_warning(
                        self._build_batch_failure_log_message(
                            failed_items=failed_items,
                            total_failed=failed_count,
                        )
                    )
                    self._log_warning(
                        self._t(
                            "\n⚠️ Batch translation completed: {success}/{total} succeeded, {failed}/{total} failed",
                            success=success_count,
                            total=total_images,
                            failed=failed_count,
                        )
                    )
                else:
                    self._log_info(self._t("✅ Batch translation completed: {success}/{total} succeeded", success=success_count, total=total_images))
                self._log_info(self._t("💾 Files saved to: {dir}", dir=self.output_folder))

            else:
                progress_context["detail"] = "顺序处理中"
                progress_context["use_backend_hook"] = False
                self._log_info("--- 开始顺序处理...")
                total_files = len(self.files)

                # 输出顺序处理信息
                self._log_info(self._t("📊 Sequential processing mode: {total} images (Total: {orig})", total=total_files, orig=total_original_count))
                self._log_info(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
                self._log_info(self._t("📁 Output directory: {dir}", dir=self.output_folder))
                if workflow_tip:
                    self._log_info(workflow_tip)

                # 初始化进度条
                emit_eta_progress(skipped_count, total_original_count, "顺序处理中")
                if total_files > 0:
                    progress_context["processing_started_at"] = time.perf_counter()
                
                success_count = 0
                for i, file_path in enumerate(self.files):
                    if not self._is_running:
                        raise asyncio.CancelledError("Task stopped by user.")

                    current_num = skipped_count + i + 1
                    self._log_info(f"🔄 [{current_num}/{total_original_count}] 正在处理：{os.path.basename(file_path)}")

                    try:
                        # 使用二进制模式读取以避免Windows路径编码问题
                        with open(file_path, 'rb') as f:
                            image = open_pil_image(f, eager=True)
                        image.name = file_path

                        ctx = await translator.translate(image, config, image_name=image.name, save_info=save_info)
                        
                        # 检查翻译是否成功（批量模式下 ctx.result 可能为 None，但文件已由后端保存）
                        if ctx and ctx.success:
                            # 计算后端保存的文件路径
                            output_path = self._calculate_output_path(file_path, save_info)
                            self.file_processed.emit({
                                'success': True, 
                                'original_path': file_path, 
                                'image_data': ctx.result,  # 可能为 None（批量模式）
                                'output_path': output_path  # 后端保存的路径
                            })
                            success_count += 1
                            self._log_info(f"✅ [{current_num}/{total_files}] 完成：{os.path.basename(file_path)}")
                            emit_eta_progress(current_num, total_original_count, f"刚完成: {os.path.basename(file_path)}")
                        else:
                            error_msg = getattr(ctx, 'translation_error', 'Translation returned no result') if ctx else 'Translation failed'
                            progress_context["failed_count"] += 1
                            self.file_processed.emit({'success': False, 'original_path': file_path, 'error': error_msg})
                            self._log_warning(f"❌ [{current_num}/{total_files}] 失败：{os.path.basename(file_path)}")
                            emit_eta_progress(current_num, total_original_count, f"处理失败: {os.path.basename(file_path)}")

                    except Exception as e:
                        self._log_error(f"❌ [{current_num}/{total_files}] 错误：{os.path.basename(file_path)} - {e}")
                        progress_context["failed_count"] += 1
                        self.file_processed.emit({'success': False, 'original_path': file_path, 'error': str(e)})
                        emit_eta_progress(current_num, total_original_count, f"处理失败: {os.path.basename(file_path)}")
                        # 抛出异常，终止整个翻译流程
                        raise

                self._log_info(f"✅ 顺序翻译完成：成功 {success_count}/{total_files} 张")
                self._log_info(f"💾 文件已保存到：{self.output_folder}")
            
            self.finished.emit(results)

        except asyncio.CancelledError as e:
            self._log_warning(f"Task cancelled: {e}")
            self.logger.warning(f"Task cancelled: {e}")
            self.error.emit(str(e))
        except Exception as e:
            import traceback
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            # 记录到logger，确保命令行能看到
            self.logger.error(f"Translation error: {error_message}")
            self.logger.error(error_traceback)
            
            # 构建友好的中文错误提示
            friendly_error = self._build_friendly_error_message(error_message, error_traceback)
            
            self.error.emit(friendly_error)
        finally:
            # 翻译结束后进行完整的内存清理（特别是CPU模式）
            try:
                # 显式清理大对象引用，帮助GC回收
                if 'translator' in locals():
                    # 确保卸载所有模型
                    if hasattr(translator, '_detector_cleanup_task') and translator._detector_cleanup_task:
                        translator._detector_cleanup_task.cancel()
                        try:
                            await translator._detector_cleanup_task
                        except asyncio.CancelledError:
                            pass
                    del translator
                if 'results' in locals():
                    del results
                if 'all_contexts' in locals():
                    del all_contexts
                if 'images_with_configs' in locals():
                    del images_with_configs
                
                from desktop_qt_ui.utils.memory_cleanup import full_memory_cleanup
                # 使用配置中的卸载模型开关
                unload_models = self.config_dict.get('app', {}).get('unload_models_after_translation', False)
                full_memory_cleanup(log_callback=self._log_info, unload_models=unload_models)
            except Exception as e:
                self._log_warning(f"--- [CLEANUP] Warning: 内存清理时出错: {e}")

    @pyqtSlot()
    def process(self):
        loop = None
        try:
            import asyncio
            import sys
            self._log_info("--- 开始处理任务...")

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
                        self._log_error(f"--- [ERROR] WSAStartup failed with code {result}")
                except Exception as e:
                    self._log_error(f"--- [ERROR] Failed to initialize WSA: {e}")
                
                # 使用ProactorEventLoop（Windows默认）
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            # 创建事件循环并保存任务引用
            try:
                loop = asyncio.new_event_loop()
            except Exception as e:
                self._log_error(f"--- [ERROR] Failed to create event loop: {e}")
                import traceback
                self._log_error(f"--- [ERROR] Traceback: {traceback.format_exc()}")
                raise
            
            asyncio.set_event_loop(loop)
            
            self._current_task = loop.create_task(self._do_processing())
            loop.run_until_complete(self._current_task)
            # 任务处理完成，不输出日志

        except asyncio.CancelledError:
            pass
        except Exception as e:
            import traceback
            error_msg = f"An error occurred in the asyncio runner: {str(e)}\n{traceback.format_exc()}"
            # 同时记录到logger，确保命令行能看到
            self.logger.error(error_msg)
            self.error.emit(error_msg)
        finally:
            if loop:
                shutdown_event_loop(loop, logger=self.logger, label="worker loop")
                # 清理完成，不输出日志



# ============================================================================
# 线程池版本的Worker类（使用QRunnable替代QThread，避免线程管理问题）
# ============================================================================

class WorkerSignals(QObject):
    """信号包装器，因为QRunnable不能直接发送信号"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    translation_progress = pyqtSignal(int, int, str)
    file_processed = pyqtSignal(dict)


class FileScannerRunnable(QRunnable):
    """文件扫描任务（线程池版本）"""
    
    def __init__(self, source_files, excluded_subfolders, file_service, 
                 finished_callback, error_callback, progress_callback):
        super().__init__()
        self.source_files = source_files
        self.excluded_subfolders = excluded_subfolders.copy()
        self.file_service = file_service
        self.finished_callback = finished_callback
        self.error_callback = error_callback
        self.progress_callback = progress_callback
        self.file_to_folder_map = {}
        self.archive_to_temp_map = {}
        self.setAutoDelete(True)
        
        # ✅ 创建信号对象用于线程安全通信
        self.signals = WorkerSignals()
        if finished_callback:
            self.signals.finished.connect(lambda args: finished_callback(*args), type=Qt.ConnectionType.QueuedConnection)
        if error_callback:
            self.signals.error.connect(error_callback, type=Qt.ConnectionType.QueuedConnection)
        if progress_callback:
            self.signals.progress.connect(progress_callback, type=Qt.ConnectionType.QueuedConnection)
    
    def run(self):
        """在线程池中执行"""
        try:
            self._emit_progress("正在扫描文件...")
            resolved_files = []
            processed_archives = set()
             
            # 分离文件和文件夹
            folders = []
            individual_files = []
            archive_files = []
            
            for path in self.source_files:
                if os.path.isdir(path):
                    folders.append(path)
                elif os.path.isfile(path):
                    if self.file_service.is_archive_file(path):
                        archive_files.append(path)
                    elif self.file_service.validate_image_file(path):
                        individual_files.append(path)

            from desktop_qt_ui.utils.archive_extractor import (
                check_output_extract_conflict,
                clear_output_extract_root,
                extract_images_from_archive,
                get_output_extract_dir,
                write_output_extract_marker,
            )

            output_base_dir = ''
            overwrite_extract = True
            try:
                cfg = self.file_service.config_service.get_config()
                output_base_dir = cfg.app.last_output_path
                overwrite_extract = bool(getattr(cfg.cli, 'overwrite', True))
            except Exception:
                output_base_dir = ''
                overwrite_extract = True

            def _is_excluded(file_path: str) -> bool:
                if not self.excluded_subfolders:
                    return False
                for excluded_folder in self.excluded_subfolders:
                    try:
                        common = os.path.commonpath([excluded_folder, file_path])
                        if common == excluded_folder:
                            return True
                    except ValueError:
                        continue
                return False

            def _get_archive_output_base_dir(archive_path: str, scan_root: str = None) -> str:
                if not (output_base_dir and os.path.isdir(output_base_dir)):
                    return ''
                if not scan_root:
                    return output_base_dir

                archive_parent = os.path.normpath(os.path.dirname(archive_path))
                scan_root_norm = os.path.normpath(scan_root)
                try:
                    relative_parent = os.path.relpath(archive_parent, scan_root_norm)
                except ValueError:
                    return output_base_dir

                nested_base = os.path.join(output_base_dir, os.path.basename(scan_root_norm))
                if relative_parent != '.':
                    nested_base = os.path.join(nested_base, relative_parent)
                return os.path.normpath(nested_base)

            def _extract_archive(archive_path: str, scan_root: str = None) -> None:
                norm_archive = os.path.normcase(os.path.abspath(archive_path))
                if norm_archive in processed_archives:
                    return
                processed_archives.add(norm_archive)

                try:
                    self._emit_progress(f"正在解压: {os.path.basename(archive_path)}")
                    archive_output_base_dir = _get_archive_output_base_dir(archive_path, scan_root)
                    if archive_output_base_dir:
                        if check_output_extract_conflict(archive_output_base_dir, archive_path):
                            if not overwrite_extract:
                                self._emit_progress(
                                    f"跳过解压(同名冲突且未开启覆盖): {os.path.basename(archive_path)}"
                                )
                                return
                            clear_output_extract_root(archive_output_base_dir, archive_path)
                        extract_dir = get_output_extract_dir(archive_output_base_dir, archive_path)
                        images, extracted_dir = extract_images_from_archive(archive_path, extract_dir)
                        if images:
                            write_output_extract_marker(archive_output_base_dir, archive_path)
                    else:
                        images, extracted_dir = extract_images_from_archive(archive_path)

                    if images:
                        self.archive_to_temp_map[archive_path] = extracted_dir
                        for img_path in images:
                            resolved_files.append(img_path)
                            self.file_to_folder_map[img_path] = archive_path
                        self._emit_progress(f"从 {os.path.basename(archive_path)} 提取了 {len(images)} 张图片")
                    else:
                        self._emit_progress(f"警告: {os.path.basename(archive_path)} 中没有找到图片")
                except Exception as e:
                    self._emit_progress(f"解压 {os.path.basename(archive_path)} 失败: {e}")

            # 处理顶层压缩包文件
            for archive_path in archive_files:
                _extract_archive(archive_path)
            
            # 清理排除列表
            if self.excluded_subfolders:
                excluded_to_remove = set()
                for excluded_folder in self.excluded_subfolders:
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
                self._emit_progress(f"正在扫描文件夹: {os.path.basename(folder)}")
                folder_files = self.file_service.get_image_files_from_folder(folder, recursive=True)
                folder_archives = self.file_service.get_archive_files_from_folder(folder, recursive=True)
                 
                # 过滤掉被排除的子文件夹中的文件
                if self.excluded_subfolders:
                    folder_files = [f for f in folder_files if not _is_excluded(f)]
                    folder_archives = [f for f in folder_archives if not _is_excluded(f)]

                # 处理文件夹内的压缩包文件
                for archive_path in folder_archives:
                    _extract_archive(archive_path, folder)
                 
                resolved_files.extend(folder_files)
                for file_path in folder_files:
                    self.file_to_folder_map[file_path] = folder
            
            # 处理单独添加的文件
            individual_files.sort(key=self.file_service._natural_sort_key)
            for file_path in individual_files:
                resolved_files.append(file_path)
                self.file_to_folder_map[file_path] = None

            unique_files = list(dict.fromkeys(resolved_files))
            self._emit_finished(unique_files, self.file_to_folder_map, self.archive_to_temp_map, self.excluded_subfolders)
            
        except Exception as e:
            self._emit_error(str(e))
    
    def _emit_finished(self, *args):
        """线程安全地发送完成信号"""
        self.signals.finished.emit(args)
    
    def _emit_error(self, msg):
        """线程安全地发送错误信号"""
        self.signals.error.emit(msg)
    
    def _emit_progress(self, msg):
        """线程安全地发送进度信号"""
        self.signals.progress.emit(msg)


class TranslationRunnable(QRunnable):
    """翻译任务（线程池版本）"""
    
    def __init__(self, files, config_dict, output_folder, root_dir, file_to_folder_map,
                 finished_callback, error_callback, progress_callback, file_processed_callback):
        super().__init__()
        self.files = files
        self.config_dict = config_dict
        self.output_folder = output_folder
        self.root_dir = root_dir
        self.file_to_folder_map = file_to_folder_map or {}
        self.finished_callback = finished_callback
        self.error_callback = error_callback
        
        self.progress_callback = progress_callback # Keep reference just in case
        self._is_running = True
        self._current_task = None
        self.logger = get_logger(__name__)
        self.file_service = get_file_service()
        self.setAutoDelete(True)
        
        # ✅ 创建信号对象用于线程安全通信
        self.signals = WorkerSignals()
        if finished_callback:
            self.signals.finished.connect(lambda args: finished_callback(*args), type=Qt.ConnectionType.QueuedConnection)
        if error_callback:
            self.signals.error.connect(error_callback, type=Qt.ConnectionType.QueuedConnection)
            
        if progress_callback:
            self.signals.translation_progress.connect(progress_callback, type=Qt.ConnectionType.QueuedConnection)
        if file_processed_callback:
            self.signals.file_processed.connect(file_processed_callback, type=Qt.ConnectionType.QueuedConnection)
    
    def stop(self):
        """停止任务"""
        self.logger.info("--- 收到停止请求")
        self._is_running = False
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        try:
            from desktop_qt_ui.utils.memory_cleanup import full_memory_cleanup
            # 使用配置中的卸载模型开关（这里没有config_dict，默认使用False）
            full_memory_cleanup(log_callback=lambda msg: self.logger.info(str(msg).rstrip()), unload_models=False)
        except Exception as e:
            self.logger.warning(f"--- [CLEANUP] 清理失败: {e}")
    
    def run(self):
        """在线程池中执行"""
        loop = None
        try:
            import asyncio
            import sys
            self.logger.info("--- 开始处理任务...")

            # Windows平台初始化
            if sys.platform == 'win32':
                import ctypes
                try:
                    WSADATA_SIZE = 400
                    wsa_data = ctypes.create_string_buffer(WSADATA_SIZE)
                    ws2_32 = ctypes.WinDLL('ws2_32')
                    result = ws2_32.WSAStartup(0x0202, wsa_data)
                    if result != 0:
                        self.logger.error(f"--- [ERROR] WSAStartup failed with code {result}")
                except Exception as e:
                    self.logger.error(f"--- [ERROR] Failed to initialize WSA: {e}")
                
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            # 创建事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 创建并运行任务（复用TranslationWorker的_do_processing逻辑）
            worker = TranslationWorker(
                self.files, self.config_dict, self.output_folder, 
                self.root_dir, self.file_to_folder_map
            )
            worker._is_running = self._is_running
            
            # 用于接收 worker 的 finished 信号
            results = []
            worker_had_error = False

            def on_worker_finished(worker_results):
                results.extend(worker_results)

            def on_worker_error(msg):
                nonlocal worker_had_error
                worker_had_error = True
                self._emit_error(msg)
            
            # 连接信号到回调
            worker.progress.connect(lambda c, t, m: self._emit_progress(c, t, m))
            worker.file_processed.connect(lambda d: self._emit_file_processed(d))
            worker.error.connect(on_worker_error)
            worker.finished.connect(on_worker_finished)
            
            self._current_task = loop.create_task(worker._do_processing())
            loop.run_until_complete(self._current_task)
            
            # 任务完成，发送结果
            if not worker_had_error:
                self._emit_finished(results)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            import traceback
            error_msg = f"翻译任务错误: {str(e)}\n{traceback.format_exc()}"
            self.logger.error(error_msg)
            self._emit_error(error_msg)
        finally:
            if loop:
                shutdown_event_loop(loop, logger=self.logger, label="threadpool worker loop")
    
    def _emit_finished(self, results):
        """线程安全地发送完成信号"""
        self.signals.finished.emit((results,))
    
    def _emit_error(self, msg):
        """线程安全地发送错误信号"""
        self.signals.error.emit(msg)
    
    def _emit_progress(self, current, total, message):
        """线程安全地发送进度信号"""
        self.signals.translation_progress.emit(current, total, message)
    
    def _emit_file_processed(self, data):
        """线程安全地发送文件处理完成信号"""
        self.signals.file_processed.emit(data)

