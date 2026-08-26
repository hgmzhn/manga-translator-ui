
"""
应用业务逻辑层
处理应用的核心业务逻辑，与UI层分离
"""
import asyncio
import base64
import concurrent.futures
import io
import logging
import os
import textwrap
import time
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
from manga_translator.image_formats import (
    OUTPUT_IMAGE_FORMATS,
)
from manga_translator.utils.curl_cffi_transport import (
    GEMINI_CURL_HEADERS,
    OPENAI_CURL_HEADERS,
    InvalidAPIKeyCharactersError,
    validate_api_key_for_http_header,
)
from manga_translator.utils.openai_compat import resolve_openai_compatible_api_key
from manga_translator.utils.system_proxy import (
    gemini_http_options_proxy_args,
    openai_http_client_kwargs,
    system_proxy_request_kwargs,
)
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
from utils.font_list import fonts_directory


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
_OPENAI_BROWSER_HEADERS = OPENAI_CURL_HEADERS
_GEMINI_BROWSER_HEADERS = GEMINI_CURL_HEADERS


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
    error_dialog_requested = pyqtSignal(str)
    warning_dialog_requested = pyqtSignal(str)
    render_setting_changed = pyqtSignal()
    file_sources_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_service = get_config_service()
        self.translation_service = get_translation_service()
        self.file_service = get_file_service()
        self.state_manager = get_state_manager()
        self.i18n = get_i18n_manager()
        self.preset_service = get_preset_service()

        # 扫描与翻译严格串行，避免模型/ONNX 资源并发冲突；执行器常驻，
        # 运行期间不在 GUI 线程 join，应用退出时再等待任务完成清理。
        self._task_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="translation-task",
        )
        self._scan_future: Optional[concurrent.futures.Future] = None
        self._translate_future: Optional[concurrent.futures.Future] = None
        self._cleanup_future: Optional[concurrent.futures.Future] = None
        self._scan_request_id = 0
        self.current_worker = None  # 当前运行的worker
        self._shutdown_started = False
        self._stop_requested = False
        self.current_task_id = 0  # 任务ID，用于区分不同的翻译任务
        self.saved_files_count = 0
        self.completed_output_sources: Dict[str, str] = {}
        self._last_progress_log_at = 0.0
        self._task_failures: List[Dict[str, str]] = []
        self._task_failure_keys: set[str] = set()

        self.source_files: List[str] = [] # Holds both files and folders
        self._source_folders: Dict[str, str] = {}
        self.file_to_folder_map: Dict[str, Optional[str]] = {} # 记录文件来自哪个文件夹
        self.archive_to_temp_map: Dict[str, str] = {} # 记录压缩包解压的临时目录
        self.excluded_subfolders: set = set() # 记录被删除的子文件夹路径
        self.excluded_files: set = set() # 记录从已添加文件夹中排除的单文件/压缩包

        self.app_config = AppConfig()

    @staticmethod
    def _path_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    @classmethod
    def _path_is_within(cls, path: str, folder: str) -> bool:
        path_key = cls._path_key(path)
        folder_key = cls._path_key(folder)
        try:
            return os.path.commonpath([path_key, folder_key]) == folder_key
        except ValueError:
            return False

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
                    if hasattr(widget, "currentData"):
                        data = widget.currentData()
                        env_vars[key] = str(data if data is not None else widget.currentText()).strip()
                    else:
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
        cli = getattr(config, "cli", None)
        if (
            cli is not None
            and getattr(cli, "export_from_local_json", False)
            and (
                getattr(cli, "generate_and_export", False)
                or (
                    getattr(cli, "template", False)
                    and getattr(cli, "save_text", False)
                )
            )
        ):
            return True


        api_candidate_validator = getattr(
            getattr(self, "main_view", None),
            "_validate_api_candidate_availability",
            None,
        )
        if callable(api_candidate_validator):
            return bool(api_candidate_validator())

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

    def open_dict_directory(self):
        import subprocess
        import sys
        # dict 目录在 app.exe 同级（打包后）或项目根目录（开发时）
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

    def open_fonts_directory(self):
        import subprocess
        import sys

        fonts_dir = fonts_directory()
        try:
            os.makedirs(fonts_dir, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(fonts_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", fonts_dir])
            else:
                subprocess.run(["xdg-open", fonts_dir])
        except Exception as e:
            self.logger.error(f"Error opening fonts directory: {e}")

    def get_hq_prompt_options(self) -> List[str]:
        try:
            # dict 目录在 app.exe 同级（打包后）或项目根目录（开发时）
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
        Image.new("RGB", (50, 50), (255, 255, 255)).save(buffer, format="PNG")
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
                impersonate="chrome",
                timeout=30.0,
                stream_timeout=30.0,
            )
        except ImportError:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                timeout=30.0,
                **openai_http_client_kwargs(api_base or "https://api.openai.com/v1"),
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
                impersonate="chrome",
                timeout=30.0,
                stream_timeout=30.0,
            )
        except ImportError:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=resolved_api_key,
                base_url=api_base or "https://api.openai.com/v1",
                timeout=30.0,
                **openai_http_client_kwargs(api_base or "https://api.openai.com/v1"),
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
                impersonate="chrome",
                timeout=60.0,
                stream_timeout=60.0,
            )

            async def fetch_remote_image(url: str):
                response = await client.session.get(
                    url,
                    timeout=60.0,
                    **system_proxy_request_kwargs(url),
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        self._t("api_test_error_remote_image", status=response.status_code)
                    )
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
                **openai_http_client_kwargs(api_base or "https://api.openai.com/v1"),
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
                impersonate="chrome",
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
                http_options_kwargs = gemini_http_options_proxy_args(base_url)
                if base_url != "https://generativelanguage.googleapis.com":
                    http_options_kwargs["base_url"] = base_url
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(**http_options_kwargs),
                ) if http_options_kwargs else genai.Client(api_key=api_key)
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
                impersonate="chrome",
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
                http_options_kwargs = gemini_http_options_proxy_args(base_url)
                if base_url != "https://generativelanguage.googleapis.com":
                    http_options_kwargs["base_url"] = base_url
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(**http_options_kwargs),
                ) if http_options_kwargs else genai.Client(api_key=api_key)
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
                impersonate="chrome",
                timeout=60.0,
                stream_timeout=60.0,
            )
            try:
                response = await client.models.generate_content(**request_kwargs)
                if not self._extract_gemini_image_bytes(response):
                    raise RuntimeError(self._t("api_test_error_gemini_no_image"))
                return True, f"连接成功，图像模型 {model_name} 可用"
            finally:
                await client.close()
        except ImportError:
            from google import genai
            from google.genai import types

            def sync_test():
                http_options_kwargs = gemini_http_options_proxy_args(base_url)
                if base_url != "https://generativelanguage.googleapis.com":
                    http_options_kwargs["base_url"] = base_url
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(**http_options_kwargs),
                ) if http_options_kwargs else genai.Client(api_key=api_key)
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
                    raise RuntimeError(self._t("api_test_error_gemini_no_image"))
                return True, f"连接成功，图像模型 {model_name} 可用"

            return await asyncio.get_running_loop().run_in_executor(None, sync_test)

    async def test_api_connection_async(self, translator_key: str, api_key: str, api_base: str = None, model: str = None) -> tuple[bool, str]:
        """异步测试API连接（如果指定了模型，会测试该模型是否可用）"""
        try:
            normalized_key = self._normalize_api_test_target(translator_key)
            if self._is_openai_compatible_target(normalized_key) or "gemini" in normalized_key:
                validate_api_key_for_http_header(api_key)

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
                    return False, self._t("api_test_error_sakura_base")
                client = AsyncOpenAI(
                    api_key="sk-114514",  # Sakura使用固定密钥
                    base_url=api_base,
                    **openai_http_client_kwargs(api_base),
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
                            return False, self._t(
                                "api_test_error_model_unavailable",
                                model=model,
                                error=str(e),
                            )
                    else:
                        await client.models.list()
                        return True, "连接成功"
                finally:
                    await client.close()
            
            else:
                return False, self._t("api_test_error_unsupported")
                
        except InvalidAPIKeyCharactersError as e:
            return False, self._t(
                "api_test_error_invalid_key_characters",
                start=e.start_position,
                end=e.end_position,
            )
        except Exception as e:
            return False, self._t("api_test_error_connection_failed", error=str(e))
    
    async def get_available_models_async(self, translator_key: str, api_key: str, api_base: str = None) -> tuple[bool, List[str], str]:
        """异步获取可用模型列表"""
        try:
            normalized_key = self._normalize_api_test_target(translator_key)
            if self._is_openai_compatible_target(normalized_key) or "gemini" in normalized_key:
                validate_api_key_for_http_header(api_key)

            if self._is_openai_compatible_target(normalized_key):
                resolved_api_key = resolve_openai_compatible_api_key(api_key, api_base or "https://api.openai.com/v1")
                # 尝试使用 curl_cffi 客户端绕过 TLS 指纹检测
                try:
                    from manga_translator.translators.common import AsyncOpenAICurlCffi
                    client = AsyncOpenAICurlCffi(
                        api_key=resolved_api_key,
                        base_url=api_base or "https://api.openai.com/v1",
                        impersonate="chrome",
                        timeout=60.0
                    )
                except ImportError:
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(
                        api_key=resolved_api_key,
                        base_url=api_base or "https://api.openai.com/v1",
                        timeout=60.0,
                        **openai_http_client_kwargs(api_base or "https://api.openai.com/v1"),
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
                        impersonate="chrome",
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

                    def sync_get_models():
                        base_url = api_base.strip() if is_custom_api else "https://generativelanguage.googleapis.com"
                        http_options_kwargs = gemini_http_options_proxy_args(base_url)
                        if is_custom_api:
                            http_options_kwargs["base_url"] = base_url
                        client = genai.Client(
                            api_key=api_key,
                            http_options=types.HttpOptions(**http_options_kwargs),
                        ) if http_options_kwargs else genai.Client(api_key=api_key)
                        models = list(client.models.list())
                        model_names = [m.name.replace("models/", "") for m in models]
                        return True, model_names, "获取成功"

                    return await loop.run_in_executor(None, sync_get_models)
            
            elif "sakura" in normalized_key:
                # Sakura使用OpenAI兼容API
                from openai import AsyncOpenAI
                if not api_base:
                    return False, [], self._t("api_test_error_sakura_base")
                client = AsyncOpenAI(
                    api_key="sk-114514",
                    base_url=api_base,
                    **openai_http_client_kwargs(api_base),
                )
                try:
                    models_response = await client.models.list()
                    model_ids = [m.id for m in models_response.data]
                    return True, model_ids, "获取成功"
                finally:
                    await client.close()
            
            else:
                return False, [], self._t("api_models_error_unsupported")
                
        except InvalidAPIKeyCharactersError as e:
            return False, [], self._t(
                "api_test_error_invalid_key_characters",
                start=e.start_position,
                end=e.end_position,
            )
        except Exception as e:
            return False, [], self._t("api_models_error_failed", error=str(e))
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
                    "det_rearrange_min_effective_short_side": self._t("label_det_rearrange_min_effective_short_side"),
                    "text_threshold": self._t("label_text_threshold"),
                    "import_yolo_labels": self._t("label_import_yolo_labels"),
                    "use_yolo_obb": self._t("label_use_yolo_obb"),
                    "use_sfx_filter": self._t("label_use_sfx_filter"),
                    "sfx_filter_include_bubble_text": self._t("label_sfx_filter_include_bubble_text"),
                    "yolo_obb_conf": self._t("label_yolo_obb_conf"),
                    "yolo_obb_overlap_threshold": self._t("label_yolo_obb_overlap_threshold"),
                    "box_threshold": self._t("label_box_threshold"),
                    "unclip_ratio": self._t("label_unclip_ratio"),
                    "min_box_area_ratio": self._t("label_min_box_area_ratio"),
                    "inpainter": self._t("label_inpainter"),
                    "inpainting_size": self._t("label_inpainting_size"),
                    "inpainting_precision": self._t("label_inpainting_precision"),
                    "force_use_torch_inpainting": self._t("label_force_use_torch_inpainting"),
                    "solid_fill_pure_bubbles": self._t("label_solid_fill_pure_bubbles"),
                    "per_block_inpainting": self._t("label_per_block_inpainting"),
                    "renderer": self._t("label_renderer"),
                    "font_family": self._t("label_font_family"),
                    "disable_system_fonts": self._t("label_disable_system_fonts"),
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
                    "semantic_linebreak": self._t("label_semantic_linebreak"),
                    "remove_linebreak_punctuation": self._t("label_remove_linebreak_punctuation"),
                    "check_br_and_retry": self._t("label_check_br_and_retry"),
                    "strict_smart_scaling": self._t("label_strict_smart_scaling"),
                    "enable_template_alignment": self._t("label_enable_template_alignment"),
                    "paste_mask_dilation_pixels": self._t("label_paste_mask_dilation_pixels"),
                    "ai_renderer_concurrency": self._t("label_ai_renderer_concurrency"),
                    "direction": self._t("label_direction"),
                    "uppercase": self._t("label_uppercase"),
                    "lowercase": self._t("label_lowercase"),
                    "no_hyphenation": self._t("label_no_hyphenation"),
                    "bubble_layout_english": self._t("label_bubble_layout_english"),
                    "font_color": self._t("label_font_color"),
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
                    "export_from_local_json": self._t("label_export_from_local_json"),
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
            "format": [self._t("format_not_specified"), *OUTPUT_IMAGE_FORMATS],
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
        if self.state_manager.is_translating():
            self._ui_log("任务运行期间不能修改文件列表。", "WARNING")
            return
        original_sources = list(self.source_files)
        original_keys = {self._path_key(path) for path in original_sources}
        source_by_key = {self._path_key(path): path for path in original_sources}
        folder_by_key = dict(self._source_folders)
        exclusions_changed = False
        for path in file_paths:
            norm_path = os.path.normpath(path)
            path_key = self._path_key(norm_path)
            path_is_dir = os.path.isdir(norm_path)
            folder_matches = {
                item
                for item in self.excluded_subfolders
                if self._path_is_within(norm_path, item)
                or (path_is_dir and self._path_is_within(item, norm_path))
            }
            file_matches = {
                item
                for item in self.excluded_files
                if self._path_key(item) == path_key
                or (path_is_dir and self._path_is_within(item, norm_path))
            }
            if folder_matches:
                self.excluded_subfolders.difference_update(folder_matches)
                exclusions_changed = True
            if file_matches:
                self.excluded_files.difference_update(file_matches)
                exclusions_changed = True

            covered_by_parent = any(
                path_key != folder_key
                and self._path_is_within(norm_path, source)
                for folder_key, source in folder_by_key.items()
            )
            if covered_by_parent:
                source_by_key.pop(path_key, None)
                folder_by_key.pop(path_key, None)
                continue

            if path_is_dir:
                redundant_keys = [
                    source_key
                    for source_key, source in source_by_key.items()
                    if source_key != path_key and self._path_is_within(source, norm_path)
                ]
                for source_key in redundant_keys:
                    source_by_key.pop(source_key, None)
                    folder_by_key.pop(source_key, None)
                folder_by_key[path_key] = norm_path
            source_by_key.setdefault(path_key, norm_path)

        sources = list(source_by_key.values())
        source_keys = list(source_by_key)
        sources_changed = source_keys != [self._path_key(path) for path in original_sources]
        if sources_changed or exclusions_changed:
            remaining_keys = set(source_keys)
            for removed_path in original_sources:
                if self._path_key(removed_path) not in remaining_keys:
                    self.file_to_folder_map.pop(removed_path, None)
            self.source_files = sources
            self._source_folders = folder_by_key
            new_paths = [path for path in sources if self._path_key(path) not in original_keys]
            if new_paths:
                self.logger.info(f"Added {len(new_paths)} files/folders to the list.")
                self.files_added.emit(new_paths)
            self.file_sources_changed.emit()

    def get_last_open_dir(self) -> str:
        path = self.config_service.get_config().app.last_open_dir
        return path

    def set_last_open_dir(self, path: str):
        self.update_single_config('app.last_open_dir', path)

    def add_folder(self):
        """Opens a dialog to select folders (supports multiple selection) and adds their paths to the list."""
        last_dir = self.get_last_open_dir()

        # 使用自定义的现代化文件夹选择器
        from ui.secondary_pages.folder_dialog import select_folders

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
        if self.state_manager.is_translating():
            self._ui_log("任务运行期间不能修改文件列表。", "WARNING")
            return
        try:
            norm_file_path = os.path.normpath(file_path)
            target_key = self._path_key(norm_file_path)
            matched_path = next(
                (path for path in self.source_files if self._path_key(path) == target_key),
                None,
            )

            # 直接添加的文件、压缩包或文件夹只移除源，不扫描磁盘。
            if matched_path:
                self.source_files.remove(matched_path)
                self._source_folders.pop(target_key, None)
                self.file_to_folder_map.pop(matched_path, None)
                covered_by_folder = any(
                    target_key != self._path_key(folder)
                    and self._path_is_within(norm_file_path, folder)
                    and os.path.isdir(folder)
                    for folder in self.source_files
                )
                if covered_by_folder:
                    if os.path.isdir(norm_file_path):
                        self.excluded_subfolders.add(norm_file_path)
                    else:
                        self.excluded_files.add(norm_file_path)
                else:
                    for exclusions in (self.excluded_subfolders, self.excluded_files):
                        exclusions.difference_update({
                            item for item in exclusions if self._path_is_within(item, matched_path)
                        })
                self.file_removed.emit(file_path)
                self.file_sources_changed.emit()
                return

            parent_folder = next(
                (
                    folder
                    for folder in self.source_files
                    if target_key != self._path_key(folder)
                    and self._path_is_within(norm_file_path, folder)
                    and os.path.isdir(folder)
                ),
                None,
            )
            if parent_folder:
                if os.path.isdir(norm_file_path):
                    self.excluded_subfolders.add(norm_file_path)
                else:
                    self.excluded_files.add(norm_file_path)
                    self.file_to_folder_map.pop(norm_file_path, None)
                self.file_removed.emit(file_path)
                self.file_sources_changed.emit()
                return

            # 兼容“若干单文件按父目录分组”的目录节点删除；只检查已有源列表。
            grouped_files = [
                path
                for path in self.source_files
                if not os.path.isdir(path) and self._path_is_within(path, norm_file_path)
            ]
            if grouped_files:
                remove_keys = {self._path_key(path) for path in grouped_files}
                self.source_files = [
                    path for path in self.source_files if self._path_key(path) not in remove_keys
                ]
                for path in grouped_files:
                    self.file_to_folder_map.pop(path, None)
                self.file_removed.emit(file_path)
                self.file_sources_changed.emit()
                return

            # 如果到这里还没有处理，说明路径不存在
            self.logger.warning(f"Path not found in list for removal: {file_path}")
        except Exception as e:
            self._ui_log(f"移除路径时发生异常: {e}", "ERROR")

    def clear_file_list(self):
        if self.state_manager.is_translating():
            self._ui_log("任务运行期间不能修改文件列表。", "WARNING")
            return
        if not (self.source_files or self.excluded_subfolders or self.excluded_files):
            return
        # TODO: Add confirmation dialog
        self.source_files.clear()
        self._source_folders.clear()
        self.file_to_folder_map.clear()  # 清空文件夹映射
        self.excluded_subfolders.clear()  # 清空排除列表
        self.excluded_files.clear()
        self.files_cleared.emit()
        self.file_sources_changed.emit()
        self.logger.info("File list cleared by user.")
    # endregion

    # region 核心任务逻辑
    def start_file_scanning(self, task_config: dict):
        """启动后台文件扫描任务"""
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在准备文件...")

        self._scan_request_id += 1
        request_id = self._scan_request_id
        scanner_worker = FileScannerRunnable(
            source_files=list(self.source_files),
            excluded_subfolders=self.excluded_subfolders,
            excluded_files=self.excluded_files,
            file_service=self.file_service,
            output_base_dir=task_config.get("app", {}).get("last_output_path", ""),
            overwrite_extract=bool(task_config.get("cli", {}).get("overwrite", True)),
            finished_callback=lambda *args: self.on_scanning_finished(
                request_id, task_config, *args
            ),
            error_callback=lambda error: self.on_scanning_error(request_id, error),
            progress_callback=self.on_worker_log
        )

        self.current_worker = scanner_worker

        def run_after_config_flush():
            if not self.config_service.flush_pending_writes():
                scanner_worker._emit_error("配置或 API Key 保存失败，任务未启动")
                return
            scanner_worker.run()

        try:
            self._scan_future = self._task_executor.submit(run_after_config_flush)
        except RuntimeError as exc:
            self.current_worker = None
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("任务启动失败")
            self._ui_log(f"文件扫描任务启动失败: {exc}", "ERROR")
            return

        self._ui_log("文件扫描任务已启动")

    def on_scanning_finished(
        self,
        request_id,
        task_config,
        resolved_files,
        file_map,
        archive_map,
        excluded_subfolders,
        excluded_files,
    ):
        """文件扫描完成，启动翻译任务"""
        if request_id != self._scan_request_id or self._shutdown_started:
            return

        self._scan_future = None
        self._ui_log(f"文件扫描完成，共找到 {len(resolved_files)} 个文件")
        self.current_worker = None

        self.file_to_folder_map = file_map
        self.archive_to_temp_map = archive_map
        self.excluded_subfolders = excluded_subfolders
        self.excluded_files = excluded_files
        
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
        self._start_translation_worker(resolved_files, task_config)

    def on_scanning_error(self, request_id, error_msg):
        if request_id != self._scan_request_id or self._shutdown_started:
            return

        self._scan_future = None
        self._ui_log(f"扫描文件时出错: {error_msg}", "ERROR")
        self.current_worker = None
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message("扫描失败")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "扫描失败", f"扫描文件时出错:\n{error_msg}")

    def _start_translation_worker(self, files_to_process, task_config):
        """启动翻译工作线程（内部方法，由扫描完成后调用）"""
        self.saved_files_count = 0
        self.completed_output_sources.clear()
        self._last_progress_log_at = 0.0
        self._reset_task_failures()
        
        # 生成新的任务ID
        self.current_task_id += 1
        task_id = self.current_task_id
        
        translation_worker = TranslationRunnable(
            files=files_to_process,
            config_dict=task_config,
            output_folder=task_config.get("app", {}).get("last_output_path", ""),
            root_dir=self.config_service.root_dir,
            file_to_folder_map=self.file_to_folder_map.copy(),
            finished_callback=lambda results: self.on_task_finished(results, task_id),
            error_callback=lambda error: self.on_task_error(error, task_id),
            progress_callback=lambda current, total, message: self.on_task_progress(
                current, total, message, task_id
            ),
        )
        
        self.current_worker = translation_worker
        
        try:
            self._translate_future = self._task_executor.submit(translation_worker.run)
        except RuntimeError as exc:
            self.current_worker = None
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("任务启动失败")
            self._ui_log(f"翻译任务启动失败: {exc}", "ERROR")
            return

        self._ui_log(f"翻译任务已启动 (任务ID: {task_id})")
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在翻译...")

    def start_backend_task(self):
        """
        Resolves input paths and uses a 'Worker-to-Thread' model to start the translation task.
        """
        # 检查是否有任务在运行
        if self.state_manager.is_translating():
            self._ui_log("一个任务已经在运行中。", "WARNING")
            return
        self._stop_requested = False

        self._scan_future = None if self._scan_future and self._scan_future.done() else self._scan_future
        self._translate_future = (
            None if self._translate_future and self._translate_future.done() else self._translate_future
        )
        self._cleanup_future = (
            None if self._cleanup_future and self._cleanup_future.done() else self._cleanup_future
        )
        if any(
            future is not None and not future.done()
            for future in (self._scan_future, self._translate_future, self._cleanup_future)
        ):
            self._ui_log("上一个任务仍在后台收尾，请稍后再试。", "WARNING")
            return

        # 任务启动前排空 UI 中尚未提交的 .env 写入。
        if hasattr(self, 'main_view') and self.main_view and hasattr(self.main_view, '_flush_all_pending_env_vars'):
            self.main_view._flush_all_pending_env_vars(wait=False)

        # 检查输出目录是否合法 (提前检查)
        config = self.config_service.get_config()
        output_path = config.app.last_output_path
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
            if not self._validate_runtime_api_requirements(config):
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
        self.start_file_scanning(config.model_dump())

    def on_task_finished(self, results, task_id):
        """处理后端已经保存完成的任务结果。"""
        # 检查任务ID是否匹配，防止已停止的任务更新状态
        if task_id != self.current_task_id:
            return

        self.current_worker = None
        saved_files = []
        skipped_results = [result for result in (results or []) if result.get('skipped')]
        skipped_count = len(skipped_results)
        if results:
            self._ui_log(f"翻译任务完成，收到 {len(results)} 个结果。")
            for result in results:
                if result.get('skipped'):
                    reason = result.get('skip_message') or '后端已跳过该文件'
                    self._ui_log(f"⏭️ {os.path.basename(result.get('original_path') or '')}: {reason}")
                    continue
                if not result.get('success'):
                    self._record_task_failure_from_result(result)
                    continue

                output_path = result.get('output_path')
                if output_path:
                    normalized_output = os.path.normpath(output_path)
                    saved_files.append(normalized_output)
                    original_path = result.get('original_path')
                    if original_path:
                        self.completed_output_sources[self._path_key(normalized_output)] = os.path.normpath(
                            original_path
                        )
                    continue

                self._record_task_failure(
                    result.get('original_path'),
                    "后端报告处理成功，但未返回已保存文件路径",
                )

        self.saved_files_count = len(saved_files)

        failed_count = len(self._task_failures)
        all_skipped = skipped_count > 0 and self.saved_files_count == 0 and failed_count == 0
        all_skipped_message = self._t(
            "all_existing_outputs_skipped_dialog",
            count=skipped_count,
        )
        if all_skipped:
            self._ui_log(
                f"任务未处理新文件：{skipped_count} 个文件被后端跳过。",
                "WARNING",
            )
        elif failed_count > 0:
            self._ui_log(f"翻译任务完成。成功处理 {self.saved_files_count} 个文件，失败 {failed_count} 个文件。", "WARNING")
        elif skipped_count > 0:
            self._ui_log(f"翻译任务完成。成功处理 {self.saved_files_count} 个文件，已跳过 {skipped_count} 个文件。")
        else:
            self._ui_log(f"翻译任务完成。总共成功处理 {self.saved_files_count} 个文件。")
        
        try:
            self.state_manager.set_translating(False)
            if all_skipped:
                self.state_manager.set_status_message(
                    self._t(
                        "all_existing_outputs_skipped_status",
                        count=skipped_count,
                    )
                )
                self.warning_dialog_requested.emit(all_skipped_message)
            elif failed_count > 0:
                self.state_manager.set_status_message(f"任务完成，成功处理 {self.saved_files_count} 个文件，失败 {failed_count} 个文件。")
            elif skipped_count > 0:
                self.state_manager.set_status_message(
                    f"任务完成，成功处理 {self.saved_files_count} 个文件，已跳过 {skipped_count} 个文件。"
                )
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
        
        QTimer.singleShot(100, self._cleanup_after_task)

    def resolve_completed_source(self, output_path: str) -> Optional[str]:
        return self.completed_output_sources.get(self._path_key(output_path))

    def _cleanup_archive_paths(self, archive_paths: List[str]):
        from desktop_qt_ui.utils.archive_extractor import cleanup_archive_temp

        for archive_path in archive_paths:
            try:
                cleanup_archive_temp(archive_path)
            except Exception as exc:
                self._ui_log(f"清理压缩包临时文件失败: {exc}", "WARNING")

    def _cleanup_after_task(self):
        """在后台清理临时文件；GUI 线程只交接引用。"""
        try:
            if self._cleanup_future is not None and not self._cleanup_future.done():
                return self._cleanup_future
            self._cleanup_future = None
            archive_paths = list(self.archive_to_temp_map)
            self.archive_to_temp_map.clear()
            if archive_paths and not self._shutdown_started:
                try:
                    self._cleanup_future = self._task_executor.submit(
                        self._cleanup_archive_paths, archive_paths
                    )
                except RuntimeError:
                    pass
            return self._cleanup_future
        except Exception as e:
            if "has been deleted" not in str(e):
                self._ui_log(f"清理任务资源时出错: {e}", "WARNING")
            return None
    
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

    def on_task_progress(self, current, total, message, task_id):
        if task_id != self.current_task_id or self._shutdown_started:
            return
        now = time.monotonic()
        if current <= 0 or (total > 0 and current >= total) or now - self._last_progress_log_at >= 1.0:
            self._last_progress_log_at = now
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
            self._stop_requested = True
            self.state_manager.set_status_message("正在停止...")
            if hasattr(self, 'main_view') and self.main_view:
                self.main_view.set_stopping_state()
            
            # 使扫描和翻译的晚到回调全部失效。
            self._scan_request_id += 1
            self.current_task_id += 1
            worker = self.current_worker
            self.current_worker = None
            try:
                worker.stop()
            except Exception as exc:
                self._ui_log(f"停止任务时出错: {exc}", "WARNING")

            QTimer.singleShot(0, self._cleanup_stopped_task_when_idle)
            return True

        if self._stop_requested:
            self._ui_log("任务仍在停止中。", "WARNING")
            return True
        if any(
            future is not None and not future.done()
            for future in (self._scan_future, self._translate_future, self._cleanup_future)
        ):
            self._ui_log("后台任务尚未结束，不能恢复开始状态。", "WARNING")
            return False

        self._ui_log("请求停止任务，但没有正在运行的任务", "WARNING")
        self.state_manager.set_translating(False)
        return False
    
    @pyqtSlot()
    def _finish_stop_task(self):
        """后台任务真正结束后恢复 UI。"""
        self._stop_requested = False
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message("任务已停止")
        if hasattr(self, 'main_view') and self.main_view:
            self.main_view.reset_progress()
        self.current_worker = None

    def _cleanup_stopped_task_when_idle(self):
        if self._shutdown_started:
            return
        if any(
            future is not None and not future.done()
            for future in (self._scan_future, self._translate_future)
        ):
            QTimer.singleShot(100, self._cleanup_stopped_task_when_idle)
            return
        self._scan_future = None
        self._translate_future = None
        if self._cleanup_future is None:
            self._cleanup_after_task()
        if self._cleanup_future is not None and not self._cleanup_future.done():
            QTimer.singleShot(100, self._cleanup_stopped_task_when_idle)
            return
        self._cleanup_future = None
        self._finish_stop_task()
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
            self._scan_request_id += 1
            self.current_task_id += 1
            if self.current_worker:
                self._ui_log("应用关闭中，停止任务...")
                if hasattr(self.current_worker, 'stop'):
                    try:
                        self.current_worker.stop()
                    except Exception as e:
                        self._ui_log(f"停止worker时出错: {e}", "WARNING")
            self.current_worker = None
            self.state_manager.set_translating(False)
            self._task_executor.shutdown(wait=True, cancel_futures=True)

            # 关闭缩略图加载线程池
            try:
                from ui.widgets.file_list_view import (
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

            # 关闭编辑器文档线程池，并排空专属导出队列。
            # 只在编辑器模块已加载过时清理，避免退出路径反而把整个编辑器栈 import 进来
            try:
                import sys
                for module_name in ("editor.editor_controller", "desktop_qt_ui.editor.editor_controller"):
                    editor_module = sys.modules.get(module_name)
                    if editor_module is None:
                        continue
                    for controller in editor_module.get_active_editor_controllers():
                        try:
                            controller.shutdown()
                        except Exception:
                            pass
            except Exception:
                pass

            # 有序停止后台协程事件循环线程（inpaint/OCR 协程），
            # 避免退出时事件循环线程被强杀
            try:
                from services import get_async_service
                async_service = get_async_service()
                if async_service is not None:
                    async_service.shutdown()
            except Exception as e:
                self._ui_log(f"关闭异步服务时出错: {e}", "WARNING")
            try:
                # 模块级单例（若有代码直接使用过 services.async_service 的全局实例）
                from services.async_service import shutdown_async_service
                shutdown_async_service()
            except Exception:
                pass

            if self.translation_service:
                pass
        except Exception as e:
            self._ui_log(f"应用关闭异常: {e}", "ERROR")
    # endregion

class TranslationWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

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
        根据错误信息构建跟随当前界面语言的友好错误提示。
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

        def _current_log_file_path() -> str:
            for handler in reversed(logging.getLogger().handlers):
                if isinstance(handler, logging.FileHandler):
                    path = str(getattr(handler, "baseFilename", "") or "").strip()
                    if path:
                        return os.path.normpath(os.path.abspath(path))
            return os.path.normpath(os.path.abspath(os.path.join("result", "log_*.txt")))

        i18n = get_i18n_manager()

        def _translate(key: str, **kwargs) -> str:
            return i18n.translate(key, **kwargs) if i18n else key

        friendly_msg = ""
        
        # 如果是"达到最大尝试次数"的错误，提取真正的错误原因
        real_error = error_message
        if "达到最大尝试次数" in error_message and "最后一次错误:" in error_message:
            # 提取真正的错误原因
            try:
                real_error = error_message.split("最后一次错误:")[1].strip()
            except Exception:
                pass

        lower_error = real_error.lower()

        def _is_image_output_unsupported_error(*section_markers: str) -> bool:
            if not any(marker in lower_error for marker in section_markers):
                return False
            return any(
                marker in lower_error
                for marker in (
                    "image_url",
                    "unknown variant",
                    "expected `text`",
                    "expected 'text'",
                    "did not contain an image",
                    "did not contain image data",
                    "compatible image output interface",
                    "only support text chat",
                    "not image generation/editing output",
                )
            )
        
        # 检查是否是AI断句检查失败
        if ("BR markers missing" in real_error or 
            "AI断句检查" in error_message or 
            "BRMarkersValidationException" in error_traceback or
            "_validate_br_markers" in error_traceback):
            friendly_msg = _translate("friendly_error_br_markers")
        
        # 检查是否是翻译数量不匹配错误
        elif "翻译数量不匹配" in real_error or "Translation count mismatch" in real_error:
            friendly_msg = _translate("friendly_error_translation_count")
        
        # 检查是否是翻译质量检查失败
        elif "翻译质量检查失败" in real_error or "Quality check failed" in real_error:
            friendly_msg = _translate("friendly_error_translation_quality")

        # 检查是否是 OpenAI/Gemini 空响应错误（统一处理）
        elif (
            (("NoneType" in real_error or "NoneType" in error_traceback) and
             ("strip" in real_error.lower() or "strip" in error_traceback.lower()))
            or ("returned empty content" in real_error.lower())
            or ("returned empty text" in real_error.lower())
            or ("响应text为空" in real_error)
        ):
            friendly_msg = _translate("friendly_error_empty_ai_response")

        # 检查是否是渲染/上色模型不支持图片输出
        elif _is_image_output_unsupported_error("renderer", "render request", "渲染"):
            friendly_msg = _translate("friendly_error_renderer_unsupported")

        elif _is_image_output_unsupported_error("colorizer", "colorization", "colorize", "上色"):
            friendly_msg = _translate("friendly_error_colorizer_unsupported")

        # 检查是否是模型或 API 端点不支持图片输入
        elif (
            "不支持多模态" in real_error
            or "不支持图片输入" in real_error
            or "no endpoints found that support image input" in lower_error
            or ("support image input" in lower_error and "endpoint" in lower_error)
            or ("multimodal" in lower_error and "renderer" not in lower_error)
            or ("vision" in lower_error and "renderer" not in lower_error)
            or ("image_url" in lower_error and "renderer" not in lower_error)
            or ("expected `text`" in lower_error and "renderer" not in lower_error)
            or ("unknown variant" in lower_error and "renderer" not in lower_error)
        ):
            friendly_msg = _translate("friendly_error_multimodal_unsupported")
        
        # 检查是否是模型不存在/模型名错误
        elif (
            "code=20012" in real_error.lower()
            or "model does not exist" in real_error.lower()
            or ("does not exist" in real_error.lower() and "model" in real_error.lower())
            or "model not found" in real_error.lower()
            or "invalid model" in real_error.lower()
            or "no such model" in real_error.lower()
            or "supported api model names" in lower_error
            or "supported model names" in lower_error
            or ("you passed" in lower_error and "model" in lower_error)
            or ("unsupported" in lower_error and "model" in lower_error)
            or "模型不存在" in real_error
            or "模型名称不存在" in real_error
        ):
            friendly_msg = _translate("friendly_error_model_unsupported")

        # 检查是否是404错误（API地址或模型配置错误）
        elif "API_404_ERROR" in real_error or "404" in real_error or "HTML错误页面" in real_error:
            friendly_msg = _translate("friendly_error_api_404_html")

        # 检查是否是API密钥错误
        elif (
            "api key" in real_error.lower()
            or "authentication" in real_error.lower()
            or "unauthorized" in real_error.lower()
            or "401" in real_error
            or "no available api candidates" in real_error.lower()
            or "exhausting api candidates" in real_error.lower()
            or "api candidates" in real_error.lower()
        ):
            friendly_msg = _translate("friendly_error_api_credentials")
        
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
            friendly_msg = _translate("friendly_error_network")
        
        # 检查是否是速率限制错误
        elif "rate limit" in real_error.lower() or "429" in real_error or "too many requests" in real_error.lower():
            friendly_msg = _translate("friendly_error_http_429")
        
        # 检查是否是403禁止访问错误
        elif "403" in real_error or "forbidden" in real_error.lower():
            friendly_msg = _translate("friendly_error_http_403")

        
        # 检查是否是404未找到错误
        elif "404" in real_error or "not found" in real_error.lower():
            friendly_msg = _translate("friendly_error_http_404")
        
        # 检查是否是500服务器错误
        elif "500" in real_error or "internal server error" in real_error.lower():
            friendly_msg = _translate("friendly_error_http_500")
        
        # 检查是否是502/503/504网关错误
        elif any(code in real_error for code in ["502", "503", "504"]) or "bad gateway" in real_error.lower() or "service unavailable" in real_error.lower() or "gateway timeout" in real_error.lower():
            error_code = "502/503/504"
            if "502" in real_error:
                error_code = "502"
            elif "503" in real_error:
                error_code = "503"
            elif "504" in real_error:
                error_code = "504"
            
            friendly_msg = _translate("friendly_error_http_gateway", code=error_code)
        
        # 检查是否是内容过滤错误
        elif "content filter" in real_error.lower() or "content_filter" in real_error:
            friendly_msg = _translate("friendly_error_content_filter")
        
        # 检查是否是语言不支持错误
        elif "language not supported" in real_error.lower() or "LanguageUnsupportedException" in error_traceback:
            friendly_msg = _translate("friendly_error_language_unsupported")
        
        # 检查是否是请求被拦截错误
        elif "blocked" in real_error.lower() or "request was blocked" in real_error.lower():
            friendly_msg = _translate("friendly_error_request_blocked")
        
        # 通用错误
        else:
            friendly_msg = _translate(
                "friendly_error_generic",
                error=error_message,
                log_path=_current_log_file_path(),
            )
        
        friendly_msg += _translate(
            "friendly_error_raw_details",
            error=_wrap_error_text(error_message),
        )
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
            
            font_family = self.config_dict.get('render', {}).get('font_family')
            if font_family:
                translator_params['font_family'] = font_family
            translator = MangaTranslator(params=translator_params)
            self._log_info("--- 翻译器初始化完成")
            
            # 注册进度钩子，接收后端的批次进度
            progress_signal = self.progress  # 捕获信号引用
            progress_context = {
                "skipped_count": 0,
                "processing_started_at": None,
                "detail": "处理中",
                "failed_count": 0,
            }

            def emit_eta_progress(current: int, total: int, detail: str | None = None):
                total = max(int(total or 0), 0)
                current = max(0, min(int(current or 0), total)) if total > 0 else 0
                elapsed_seconds = 0.0
                if progress_context["processing_started_at"] is not None:
                    elapsed_seconds = max(0.0, time.perf_counter() - progress_context["processing_started_at"])
                completed_count = max(0, current - progress_context["skipped_count"])
                remaining_count = max(0, total - current)
                message = self._build_eta_progress_message(
                    completed_count=completed_count,
                    remaining_count=remaining_count,
                    elapsed_seconds=elapsed_seconds,
                    skipped_count=progress_context["skipped_count"],
                    failed_count=progress_context["failed_count"],
                    detail=detail if detail is not None else progress_context["detail"],
                )
                progress_signal.emit(current, total, message)
            
            async def progress_hook(state: str, finished: bool):
                try:
                    if state.startswith("batch:"):
                        # 后端统一报告绝对进度: batch:start:end:total[:failed][:skipped]
                        parts = state.split(":")
                        if len(parts) >= 4:
                            batch_end = int(parts[2])
                            total = int(parts[3])
                            if len(parts) >= 5:
                                try:
                                    progress_context["failed_count"] = max(0, int(parts[4]))
                                except (TypeError, ValueError):
                                    pass
                            if len(parts) >= 6:
                                try:
                                    progress_context["skipped_count"] = max(0, int(parts[5]))
                                except (TypeError, ValueError):
                                    pass
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
                tip_key = (
                    "Tip: Reads existing local JSON and exports translated text only; no detection, OCR, API translation, or JSON write-back"
                    if cli_config.get("export_from_local_json", False)
                    else "Tip: After exporting, check manga_translator_work/translations/ for imagename_translated.txt files"
                )
                workflow_tip = self._t(tip_key)
            elif cli_config.get('template', False):
                workflow_mode = self._t("Export Original Text")
                tip_key = (
                    "Tip: Reads existing local JSON and exports original text only; no detection, OCR, API translation, or JSON write-back"
                    if cli_config.get("export_from_local_json", False)
                    else "Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode"
                )
                workflow_tip = self._t(tip_key)
            elif cli_config.get('load_text', False):
                workflow_mode = self._t("Import Translation and Render")
                workflow_tip = self._t("Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)")
            elif cli_config.get('translate_json_only', False):
                workflow_mode = self._t("Translate JSON Only")
                workflow_tip = self._t("Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success")
                 
                # TXT导入JSON的预处理已经统一到翻译器入口（manga_translator.py），这里不再需要

            total_images = len(self.files)
            progress_context["detail"] = "处理中"
            progress_context["failed_count"] = 0
            self._log_info(f"--- 开始批量处理 ({'高质量模式' if is_hq else '批量模式'})")
            self._log_info(
                self._t(
                    "📊 Batch processing mode: {total} images in {batches} batches",
                    total=total_images,
                    batches=(total_images + batch_size - 1) // batch_size if batch_size > 0 else total_images,
                )
            )
            self._log_info(self._t("🔧 Translation workflow: {mode}", mode=workflow_mode))
            self._log_info(self._t("📁 Output directory: {dir}", dir=self.output_folder))
            if workflow_tip:
                self._log_info(workflow_tip)
            self._log_info(self._t("🚀 Starting translation..."))
            emit_eta_progress(0, total_images, "处理中")
            if total_images > 0:
                progress_context["processing_started_at"] = time.perf_counter()
                images_with_configs = [(file_path, config) for file_path in self.files]
                contexts = await translator.translate_batch(
                    images_with_configs,
                    save_info=save_info,
                )
            else:
                contexts = []

            success_count = 0
            skipped_count = 0
            failed_count = 0
            failed_items = []
            for ctx in contexts:
                if not self._is_running:
                    raise asyncio.CancelledError("Task stopped by user.")
                if not ctx:
                    fallback_error = 'Batch translation returned no context'
                    results.append({'success': False, 'original_path': 'Unknown', 'error': fallback_error})
                    failed_count += 1
                    failed_items.append({'file_name': 'Unknown', 'summary': fallback_error})
                    continue

                image_name = self._get_context_value(ctx, 'image_name', 'Unknown') or 'Unknown'
                file_name = os.path.basename(image_name)
                if self._get_context_value(ctx, 'skipped'):
                    skip_message = self._get_context_value(ctx, 'skip_message', '后端已跳过该文件')
                    results.append({
                        'success': True,
                        'original_path': image_name,
                        'output_path': self._get_context_value(ctx, 'output_path'),
                        'skipped': True,
                        'skip_reason': self._get_context_value(ctx, 'skip_reason'),
                        'skip_message': skip_message,
                    })
                    skipped_count += 1
                    self._log_info(f"⏭️ {file_name}: {skip_message}")
                    continue

                error_message = self._extract_context_error_message(ctx)
                error_summary = self._normalize_error_summary(error_message)
                if error_message:
                    results.append({'success': False, 'original_path': image_name, 'error': error_message})
                    failed_count += 1
                    failed_items.append({'file_name': file_name, 'summary': error_summary})
                    self._log_warning(f"\n⚠️ 图片 {file_name} 翻译失败：{error_summary}")
                    self._log_error(error_message)
                elif self._get_context_value(ctx, 'success') or self._get_context_value(ctx, 'result'):
                    result = {'success': True, 'original_path': image_name}
                    output_path = self._get_context_value(ctx, 'output_path')
                    if output_path:
                        result['output_path'] = output_path
                    results.append(result)
                    success_count += 1
                else:
                    fallback_error = "翻译结果为空"
                    results.append({'success': False, 'original_path': image_name, 'error': fallback_error})
                    failed_count += 1
                    failed_items.append({'file_name': file_name, 'summary': fallback_error})

            if failed_count > 0:
                self._log_warning(
                    self._build_batch_failure_log_message(
                        failed_items=failed_items,
                        total_failed=failed_count,
                    )
                )
            self._log_info(
                f"批量处理完成：成功 {success_count}，跳过 {skipped_count}，失败 {failed_count}。"
            )
            self._log_info(self._t("💾 Files saved to: {dir}", dir=self.output_folder))

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


class FileScannerRunnable(QRunnable):
    """文件扫描任务（线程池版本）"""

    def __init__(self, source_files, excluded_subfolders, excluded_files, file_service,
                 output_base_dir, overwrite_extract,
                 finished_callback, error_callback, progress_callback):
        super().__init__()
        self.source_files = source_files
        self.excluded_subfolders = excluded_subfolders.copy()
        self.excluded_files = excluded_files.copy()
        self.file_service = file_service
        self.output_base_dir = output_base_dir
        self.overwrite_extract = overwrite_extract
        self.finished_callback = finished_callback
        self.error_callback = error_callback
        self.progress_callback = progress_callback
        self.file_to_folder_map = {}
        self.archive_to_temp_map = {}
        self._is_running = True
        self.setAutoDelete(True)
        
        # ✅ 创建信号对象用于线程安全通信
        self.signals = WorkerSignals()
        if finished_callback:
            self.signals.finished.connect(lambda args: finished_callback(*args), type=Qt.ConnectionType.QueuedConnection)
        if error_callback:
            self.signals.error.connect(error_callback, type=Qt.ConnectionType.QueuedConnection)
        if progress_callback:
            self.signals.progress.connect(progress_callback, type=Qt.ConnectionType.QueuedConnection)

    def stop(self):
        self._is_running = False

    def run(self):
        """在线程池中执行"""
        try:
            if not self._is_running:
                return
            self._emit_progress("正在扫描文件...")
            resolved_files = []
            processed_archives = set()
             
            # 分离文件和文件夹
            folders = []
            individual_files = []
            archive_files = []
            
            for path in self.source_files:
                if not self._is_running:
                    return
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

            output_base_dir = self.output_base_dir
            overwrite_extract = self.overwrite_extract

            def _is_excluded(file_path: str) -> bool:
                if MainAppLogic._path_key(file_path) in excluded_file_keys:
                    return True
                return any(
                    MainAppLogic._path_is_within(file_path, excluded_folder)
                    for excluded_folder in self.excluded_subfolders
                )

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
                if not self._is_running:
                    return
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

                    if not self._is_running:
                        return
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
                if not self._is_running:
                    return
                _extract_archive(archive_path)

            def _belongs_to_source_folder(path: str) -> bool:
                return any(MainAppLogic._path_is_within(path, folder) for folder in folders)

            self.excluded_subfolders = {
                path for path in self.excluded_subfolders if _belongs_to_source_folder(path)
            }
            self.excluded_files = {
                path for path in self.excluded_files if _belongs_to_source_folder(path)
            }
            excluded_file_keys = {
                MainAppLogic._path_key(path) for path in self.excluded_files
            }
            
            # 对文件夹进行自然排序
            folders.sort(key=self.file_service._natural_sort_key)
            
            # 按文件夹分组处理
            for folder in folders:
                if not self._is_running:
                    return
                self._emit_progress(f"正在扫描文件夹: {os.path.basename(folder)}")
                folder_files, folder_archives = self.file_service.get_supported_files_from_folder(
                    folder, recursive=True
                )

                if not self._is_running:
                    return
                folder_files = [f for f in folder_files if not _is_excluded(f)]
                folder_archives = [f for f in folder_archives if not _is_excluded(f)]

                # 处理文件夹内的压缩包文件
                for archive_path in folder_archives:
                    if not self._is_running:
                        return
                    _extract_archive(archive_path, folder)
                 
                resolved_files.extend(folder_files)
                for file_path in folder_files:
                    self.file_to_folder_map[file_path] = folder
            
            # 处理单独添加的文件
            individual_files.sort(key=self.file_service._natural_sort_key)
            for file_path in individual_files:
                if not self._is_running:
                    return
                resolved_files.append(file_path)
                self.file_to_folder_map[file_path] = None

            unique_files = list(dict.fromkeys(resolved_files))
            if self._is_running:
                self._emit_finished(
                    unique_files,
                    self.file_to_folder_map,
                    self.archive_to_temp_map,
                    self.excluded_subfolders,
                    self.excluded_files,
                )
            
        except Exception as e:
            if self._is_running:
                self._emit_error(str(e))
    
    def _emit_finished(self, *args):
        """线程安全地发送完成信号"""
        self.signals.finished.emit(args)
    
    def _emit_error(self, msg):
        """线程安全地发送错误信号"""
        self.signals.error.emit(msg)
    
    def _emit_progress(self, msg):
        """线程安全地发送进度信号"""
        if self._is_running:
            self.signals.progress.emit(msg)


class TranslationRunnable(QRunnable):
    """翻译任务（线程池版本）"""
    
    def __init__(self, files, config_dict, output_folder, root_dir, file_to_folder_map,
                 finished_callback, error_callback, progress_callback):
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
        self._loop = None
        self._worker = None
        self._last_progress_emit_at = 0.0
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
    
    def stop(self):
        """停止任务"""
        self._is_running = False
        if self._worker is not None:
            self._worker._is_running = False
        task = self._current_task
        loop = self._loop
        if loop is not None and task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
    
    def run(self):
        """在线程池中执行"""
        loop = None
        try:
            import asyncio
            import sys
            if not self._is_running:
                return
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
            self._loop = loop
            asyncio.set_event_loop(loop)
            
            # 创建并运行任务（复用TranslationWorker的_do_processing逻辑）
            worker = TranslationWorker(
                self.files, self.config_dict, self.output_folder, 
                self.root_dir, self.file_to_folder_map
            )
            self._worker = worker
            worker._is_running = self._is_running
            if not self._is_running:
                return
            
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
            self._worker = None
            self._current_task = None
            self._loop = None
    
    def _emit_finished(self, results):
        """线程安全地发送完成信号"""
        if self._is_running:
            self.signals.finished.emit((results,))
    
    def _emit_error(self, msg):
        """线程安全地发送错误信号"""
        if self._is_running:
            self.signals.error.emit(msg)
    
    def _emit_progress(self, current, total, message):
        """线程安全地发送进度信号"""
        if not self._is_running:
            return
        now = time.monotonic()
        is_terminal = total > 0 and current >= total
        if not is_terminal and now - self._last_progress_emit_at < 0.05:
            return
        self._last_progress_emit_at = now
        self.signals.translation_progress.emit(current, total, message)
