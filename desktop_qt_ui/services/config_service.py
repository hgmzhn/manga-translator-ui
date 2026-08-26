"""
配置管理服务
负责应用程序的配置加载、保存、验证和环境变量管理
"""

import json
import logging
import os
import re
import sys
import tempfile
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv.parser import parse_stream
from manga_translator.api_key_rotation import (
    env_has_any_indexed_value,
    get_rotation_env_keys,
    get_rotation_slot_count,
)
from manga_translator.colorization.prompt_loader import ensure_ai_colorizer_prompt_file
from manga_translator.custom_api_params import (
    ensure_custom_api_params_file,
    migrate_legacy_custom_api_params_config,
)
from manga_translator.ocr.prompt_loader import ensure_ai_ocr_prompt_file
from manga_translator.rendering.prompt_loader import ensure_ai_renderer_prompt_file
from manga_translator.runtime_paths import get_config_path
from manga_translator.utils.dotenv_utils import (
    APP_DOTENV_PATH_ENV,
    format_env_line,
    load_app_dotenv,
    read_dotenv_file,
    remove_invalid_dotenv_lines,
    validate_env_key,
)
from manga_translator.utils.openai_compat import resolve_openai_compatible_api_key

from core.config_models import AppSettings

PRESET_SPECIAL_ENV_VARS = [
    "OCR_OPENAI_API_KEY",
    "OCR_OPENAI_MODEL",
    "OCR_OPENAI_API_BASE",
    "OCR_GEMINI_API_KEY",
    "OCR_GEMINI_MODEL",
    "OCR_GEMINI_API_BASE",
    "COLOR_OPENAI_API_KEY",
    "COLOR_OPENAI_MODEL",
    "COLOR_OPENAI_API_BASE",
    "COLOR_GEMINI_API_KEY",
    "COLOR_GEMINI_MODEL",
    "COLOR_GEMINI_API_BASE",
    "RENDER_OPENAI_API_KEY",
    "RENDER_OPENAI_MODEL",
    "RENDER_OPENAI_API_BASE",
    "RENDER_GEMINI_API_KEY",
    "RENDER_GEMINI_MODEL",
    "RENDER_GEMINI_API_BASE",
]

API_ROTATION_ENV_GROUPS = [
    ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL"),
    ("GEMINI_API_KEY", "GEMINI_API_BASE", "GEMINI_MODEL"),
    ("OCR_OPENAI_API_KEY", "OCR_OPENAI_API_BASE", "OCR_OPENAI_MODEL"),
    ("OCR_GEMINI_API_KEY", "OCR_GEMINI_API_BASE", "OCR_GEMINI_MODEL"),
    ("COLOR_OPENAI_API_KEY", "COLOR_OPENAI_API_BASE", "COLOR_OPENAI_MODEL"),
    ("COLOR_GEMINI_API_KEY", "COLOR_GEMINI_API_BASE", "COLOR_GEMINI_MODEL"),
    ("RENDER_OPENAI_API_KEY", "RENDER_OPENAI_API_BASE", "RENDER_OPENAI_MODEL"),
    ("RENDER_GEMINI_API_KEY", "RENDER_GEMINI_API_BASE", "RENDER_GEMINI_MODEL"),
]

RUNTIME_API_REQUIREMENTS = {
    "openai": {
        "display_name": "OpenAI",
        "accepted_env_vars": ["OPENAI_API_KEY"],
        "accepted_base_env_vars": ["OPENAI_API_BASE"],
        "allow_empty_api_key_for_local_base": True,
    },
    "openai_hq": {
        "display_name": "OpenAI HQ",
        "accepted_env_vars": ["OPENAI_API_KEY"],
        "accepted_base_env_vars": ["OPENAI_API_BASE"],
        "allow_empty_api_key_for_local_base": True,
    },
    "gemini": {
        "display_name": "Gemini",
        "accepted_env_vars": ["GEMINI_API_KEY"],
    },
    "gemini_hq": {
        "display_name": "Gemini HQ",
        "accepted_env_vars": ["GEMINI_API_KEY"],
    },
    "openai_ocr": {
        "display_name": "OpenAI OCR",
        "accepted_env_vars": ["OCR_OPENAI_API_KEY", "OPENAI_API_KEY"],
        "accepted_base_env_vars": ["OCR_OPENAI_API_BASE", "OPENAI_API_BASE"],
        "allow_empty_api_key_for_local_base": True,
    },
    "gemini_ocr": {
        "display_name": "Gemini OCR",
        "accepted_env_vars": ["OCR_GEMINI_API_KEY", "GEMINI_API_KEY"],
    },
    "openai_colorizer": {
        "display_name": "OpenAI Colorizer",
        "accepted_env_vars": ["COLOR_OPENAI_API_KEY", "OPENAI_API_KEY"],
        "accepted_base_env_vars": ["COLOR_OPENAI_API_BASE", "OPENAI_API_BASE"],
        "allow_empty_api_key_for_local_base": True,
    },
    "gemini_colorizer": {
        "display_name": "Gemini Colorizer",
        "accepted_env_vars": ["COLOR_GEMINI_API_KEY", "GEMINI_API_KEY"],
    },
    "openai_renderer": {
        "display_name": "OpenAI Renderer",
        "accepted_env_vars": ["RENDER_OPENAI_API_KEY", "OPENAI_API_KEY"],
        "accepted_base_env_vars": ["RENDER_OPENAI_API_BASE", "OPENAI_API_BASE"],
        "allow_empty_api_key_for_local_base": True,
    },
    "gemini_renderer": {
        "display_name": "Gemini Renderer",
        "accepted_env_vars": ["RENDER_GEMINI_API_KEY", "GEMINI_API_KEY"],
    },
}


@dataclass
class TranslatorConfig:
    """翻译器配置信息"""

    name: str
    display_name: str
    required_env_vars: List[str]
    optional_env_vars: List[str] = field(default_factory=list)
    validation_rules: Dict[str, str] = field(default_factory=dict)


from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot


class ConfigService(QObject):
    """配置管理服务"""

    config_changed = pyqtSignal(dict)
    write_failed = pyqtSignal(str)
    SAVE_DEBOUNCE_MS = 250

    def __init__(self, root_dir: str):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.root_dir = root_dir
        # .env文件应该在exe所在目录（可写位置）
        # 打包后：E:\manga-translator-cpu-v1.9.2\.env
        # 开发时：项目根目录\.env
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            self.env_path = os.path.join(exe_dir, ".env")
        else:
            self.env_path = os.path.join(self.root_dir, ".env")
        os.environ[APP_DOTENV_PATH_ENV] = self.env_path
        self._deferred_write_error: Optional[str] = None
        self._remove_invalid_env_lines()
        try:
            self._env_values = read_dotenv_file(self.env_path)
            load_app_dotenv(self.env_path, override=True)
        except Exception as exc:
            self.logger.error(f"加载 .env 失败: {exc}")
            self._env_values = {}

        # Use get_default_config_path() for PyInstaller compatibility
        # Temporarily set a placeholder, will be properly set after initialization
        self.default_config_path = None
        self.user_config_path = None

        self.config_path = None  # This will hold the path of a loaded file
        self.current_config: AppSettings = AppSettings()

        # Set the correct default config path
        self.default_config_path = self.get_default_config_path()
        self.user_config_path = self.get_user_config_path()
        try:
            ensure_custom_api_params_file(logger=self.logger)
            ensure_ai_ocr_prompt_file()
            ensure_ai_renderer_prompt_file()
            ensure_ai_colorizer_prompt_file()
        except Exception as exc:
            self.logger.error(f"创建本地配置模板文件失败: {exc}")
        self.logger.debug(f"默认配置: {os.path.basename(self.default_config_path)}")
        self.logger.debug(f"用户配置: {os.path.basename(self.user_config_path)}")
        self.logger.debug(f"默认配置存在: {os.path.exists(self.default_config_path)}")
        self.logger.debug(f"用户配置存在: {os.path.exists(self.user_config_path)}")
        if getattr(sys, "frozen", False):
            self.logger.debug(
                f"打包环境，外部配置目录 = {os.path.dirname(self.user_config_path)}"
            )

        # 加载配置：优先级 用户配置 > 默认配置 > 代码默认值
        self._load_configs_with_priority()

        self._translator_configs = None
        self._env_cache = None
        self._config_cache = None
        self._initialize_write_pipeline()

    def _remove_invalid_env_lines(self) -> None:
        try:
            removed = remove_invalid_dotenv_lines(self.env_path)
        except Exception as exc:
            self._deferred_write_error = f"{self.env_path}: {exc}"
            self.logger.error(f"清理 .env 解析错误行失败: {exc}")
            return
        if removed:
            self.logger.warning(f"已删除 .env 中 {removed} 行无法解析的配置")

    def take_deferred_write_error(self) -> Optional[str]:
        error = self._deferred_write_error
        self._deferred_write_error = None
        return error

    def _initialize_write_pipeline(self) -> None:
        self._write_lock = threading.RLock()
        self._pending_config_writes: Dict[str, Dict[str, Any]] = {}
        self._pending_env_updates: Dict[str, Optional[str]] = {}
        self._pending_env_replacement: Optional[Dict[str, str]] = None
        self._write_futures: set[Future] = set()
        self._write_errors: list[Exception] = []
        self._env_write_failed = False
        self._writer_closed = False
        self._writer_shutdown_started = False
        self._write_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="config-writer",
        )
        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._write_timer.timeout.connect(self._submit_pending_writes)

    @property
    def translator_configs(self):
        """延迟加载翻译器配置"""
        if self._translator_configs is None:
            self._translator_configs = self._init_translator_configs()
        return self._translator_configs

    def _init_translator_configs(self) -> Dict[str, TranslatorConfig]:
        """从JSON文件初始化翻译器配置注册表"""
        configs = {}

        config_path = get_config_path("config", "translators.json")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, config_data in data.items():
                configs[name] = TranslatorConfig(**config_data)
        except FileNotFoundError:
            self.logger.error(f"Translator config file not found at: {config_path}")
        except Exception as e:
            self.logger.error(f"Failed to load translator configs: {e}")
        return configs

    def get_translator_configs(self) -> Dict[str, TranslatorConfig]:
        """获取所有翻译器配置"""
        return self.translator_configs

    def get_translator_config(self, translator_name: str) -> Optional[TranslatorConfig]:
        """获取特定翻译器配置"""
        return self.translator_configs.get(translator_name)

    def get_required_env_vars(self, translator_name: str) -> List[str]:
        """获取翻译器必需的环境变量"""
        config = self.get_translator_config(translator_name)
        return config.required_env_vars if config else []

    def get_all_env_vars(self, translator_name: str) -> List[str]:
        """获取翻译器所有相关环境变量"""
        config = self.get_translator_config(translator_name)
        if not config:
            return []
        return config.required_env_vars + config.optional_env_vars

    def get_all_preset_env_vars(self) -> List[str]:
        """获取预设应包含的全部 API 环境变量。"""
        env_keys: List[str] = []
        seen = set()

        for translator_config in self.translator_configs.values():
            for key in (
                translator_config.required_env_vars
                + translator_config.optional_env_vars
            ):
                if key and key not in seen:
                    seen.add(key)
                    env_keys.append(key)

        for key in PRESET_SPECIAL_ENV_VARS:
            if key not in seen:
                seen.add(key)
                env_keys.append(key)

        current_env_vars = self.load_env_vars()
        for api_key_env, api_base_env, model_env in API_ROTATION_ENV_GROUPS:
            slots = get_rotation_slot_count(
                current_env_vars,
                (api_key_env, api_base_env, model_env),
            )
            for key in get_rotation_env_keys(
                api_key_env, api_base_env, model_env, slots=slots
            ):
                if key not in seen:
                    seen.add(key)
                    env_keys.append(key)

        return env_keys

    @staticmethod
    def _has_env_value(env_vars: Dict[str, str], key: str) -> bool:
        return env_has_any_indexed_value(env_vars, key)

    def get_missing_runtime_api_requirements(
        self,
        config: AppSettings,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取当前配置下缺失的运行时 API Key 要求。"""
        merged_env_vars = {
            key: str(value or "") for key, value in self.load_env_vars().items()
        }
        if env_vars:
            for key, value in env_vars.items():
                merged_env_vars[key] = str(value or "")

        checks = [
            (
                "translator",
                "translator",
                getattr(config.translator, "translator", None),
            ),
            ("ocr", "ocr", getattr(config.ocr, "ocr", None)),
            ("colorizer", "colorizer", getattr(config.colorizer, "colorizer", None)),
            ("render", "renderer", getattr(config.render, "renderer", None)),
        ]

        if bool(getattr(config.ocr, "use_hybrid_ocr", False)):
            checks.append(
                ("ocr", "secondary_ocr", getattr(config.ocr, "secondary_ocr", None))
            )

        missing: List[Dict[str, Any]] = []
        for section, setting, selected_value in checks:
            feature_name = str(selected_value or "").strip()
            if not feature_name:
                continue

            requirement = RUNTIME_API_REQUIREMENTS.get(feature_name)
            if not requirement:
                continue

            accepted_env_vars = list(requirement.get("accepted_env_vars", []))
            if any(
                self._has_env_value(merged_env_vars, key) for key in accepted_env_vars
            ):
                continue

            accepted_base_env_vars = list(requirement.get("accepted_base_env_vars", []))
            if requirement.get("allow_empty_api_key_for_local_base") and any(
                resolve_openai_compatible_api_key("", merged_env_vars.get(key, ""))
                for key in accepted_base_env_vars
            ):
                continue

            missing.append(
                {
                    "section": section,
                    "setting": setting,
                    "selected_value": feature_name,
                    "display_name": requirement.get("display_name", feature_name),
                    "accepted_env_vars": accepted_env_vars,
                }
            )

        return missing

    def validate_api_key(self, key: str, var_name: str, translator_name: str) -> bool:
        """验证API密钥格式"""
        config = self.get_translator_config(translator_name)
        if not config or var_name not in config.validation_rules:
            return True  # 如果没有验证规则，则认为有效

        pattern = config.validation_rules[var_name]
        return bool(re.match(pattern, key))

    def load_config_file(self, config_path: str) -> bool:
        """加载JSON配置文件并与默认设置合并，逐个键验证，错误的键使用默认值"""
        try:
            if not os.path.exists(config_path):
                self.logger.error(f"配置文件不存在: {config_path}")
                return False

            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                loaded_data = migrate_legacy_custom_api_params_config(
                    json.loads(content)
                )

            # 获取默认配置作为基础
            default_config = AppSettings()
            new_config_dict = default_config.model_dump()

            # 逐个键安全合并，验证每个值
            error_keys = []

            def safe_deep_update(target, source, path=""):
                """安全的深层合并，逐个键验证"""
                for key, value in source.items():
                    current_path = f"{path}.{key}" if path else key
                    try:
                        if (
                            isinstance(value, dict)
                            and key in target
                            and isinstance(target[key], dict)
                        ):
                            # 递归处理嵌套字典
                            safe_deep_update(target[key], value, current_path)
                        else:
                            # 尝试设置值，验证是否有效
                            old_value = target.get(key)
                            target[key] = value

                            # 尝试用新值创建配置对象来验证
                            try:
                                AppSettings.model_validate(new_config_dict)
                            except Exception as validate_err:
                                # 验证失败，恢复默认值
                                target[key] = old_value
                                error_keys.append(
                                    (current_path, value, str(validate_err))
                                )
                                self.logger.warning(
                                    f"配置键 '{current_path}' 值无效: {value}，使用默认值: {old_value}"
                                )
                    except Exception as e:
                        error_keys.append((current_path, value, str(e)))
                        self.logger.warning(
                            f"配置键 '{current_path}' 加载失败: {e}，保持默认值"
                        )

            safe_deep_update(new_config_dict, loaded_data)

            # 最终验证并创建配置对象
            try:
                self.current_config = AppSettings.model_validate(new_config_dict)
            except Exception as final_err:
                self.logger.error(f"配置验证失败，使用默认配置: {final_err}")
                self.current_config = AppSettings()

            # 报告错误的键
            if error_keys:
                self.logger.warning(
                    f"配置文件中有 {len(error_keys)} 个无效配置项已使用默认值替换:"
                )
                for key_path, bad_value, err in error_keys[:5]:  # 只显示前5个
                    self.logger.warning(f"  - {key_path}: {bad_value}")
                if len(error_keys) > 5:
                    self.logger.warning(f"  ... 还有 {len(error_keys) - 5} 个")

            self.config_path = config_path
            self.logger.debug(f"加载配置: {os.path.basename(config_path)}")
            config_dict = self.current_config.model_dump()
            self.config_changed.emit(config_dict)
            return True

        except json.JSONDecodeError as e:
            self.logger.error(f"配置文件JSON格式错误: {e}，使用默认配置")
            self.current_config = AppSettings()
            return False
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}，使用默认配置")
            self.current_config = AppSettings()
            return False

    def _build_config_payload(self, save_path: str) -> Dict[str, Any]:
        config_dict = self.current_config.model_dump()
        is_default_config = save_path == self.default_config_path
        if is_default_config:
            config_dict.setdefault("detector", {})["min_box_area_ratio"] = 0

        if is_default_config and not getattr(sys, "frozen", False):
            app = config_dict.setdefault("app", {})
            app.update(
                {
                    "last_open_dir": ".",
                    "last_output_path": "",
                    "favorite_folders": None,
                    "folder_dialog_sort": "name_ascending",
                    "theme": "light",
                    "ui_language": "auto",
                    "current_preset": "默认",
                    "editor_snap_enabled": False,
                    "editor_center_scale_enabled": False,
                    "editor_rich_text_popup_enabled": True,
                    "editor_rich_text_popup_pinned": False,
                    "editor_auto_save_on_switch": True,
                    "editor_auto_export_on_switch": True,
                    "editor_suppress_unsaved_warning": False,
                    "editor_auto_rich_text_rules": True,
                    "editor_delete_and_recover": False,
                    "saved_colors": None,
                    "saved_style_presets": None,
                    "saved_rich_text_presets": None,
                }
            )
            config_dict.setdefault("cli", {})["verbose"] = False
            render = config_dict.setdefault("render", {})
            render.update(
                {
                    "font_family": "Microsoft YaHei UI",
                    "disable_auto_wrap": False,
                    "center_text_in_bubble": False,
                    "optimize_line_breaks": False,
                    "semantic_linebreak": False,
                    "remove_linebreak_punctuation": False,
                    "check_br_and_retry": False,
                    "strict_smart_scaling": False,
                    "balloon_fill_mask_layout": False,
                }
            )
            config_dict.setdefault("translator", {})["high_quality_prompt_path"] = (
                "dict/prompt_example.yaml"
            )
            config_dict.setdefault("ocr", {})["use_hybrid_ocr"] = False
        return config_dict

    @staticmethod
    def _atomic_write_text(path: str, content: str) -> None:
        target = os.path.abspath(path)
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=directory,
                prefix=f".{os.path.basename(target)}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, target)
            temp_path = None
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @classmethod
    def _merge_dotenv_updates(
        cls,
        path: str,
        updates: Dict[str, Optional[str]],
    ) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as source:
                for mapping in parse_stream(source):
                    key = mapping.key
                    if mapping.error:
                        continue
                    if key in updates:
                        if key not in seen and updates[key] is not None:
                            lines.append(format_env_line(key, updates[key]))
                        seen.add(key)
                    else:
                        lines.append(mapping.original.string)

        for key, value in updates.items():
            if key in seen or value is None:
                continue
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines.append("\n")
            lines.append(format_env_line(key, value))
        return "".join(lines)

    @classmethod
    def _write_snapshots(
        cls,
        config_writes: Dict[str, Dict[str, Any]],
        env_write: Optional[tuple[bool, Dict[str, Optional[str]]]],
        env_path: str,
    ) -> None:
        errors: list[str] = []
        for save_path, payload in config_writes.items():
            try:
                content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                cls._atomic_write_text(save_path, content)
            except Exception as exc:
                errors.append(f"{save_path}: {exc}")
        if env_write is not None:
            try:
                replace, payload = env_write
                if replace:
                    content = "".join(
                        format_env_line(key, value) for key, value in payload.items()
                    )
                else:
                    content = cls._merge_dotenv_updates(env_path, payload)
                cls._atomic_write_text(env_path, content)
            except Exception as exc:
                errors.append(f"{env_path}: {exc}")
        if errors:
            raise OSError("; ".join(errors))

    def _take_pending_writes(self):
        with self._write_lock:
            config_writes = self._pending_config_writes
            self._pending_config_writes = {}
            if self._pending_env_replacement is not None:
                env_write = (True, self._pending_env_replacement)
            elif self._pending_env_updates:
                env_write = (False, self._pending_env_updates)
            else:
                env_write = None
            self._pending_env_replacement = None
            self._pending_env_updates = {}
        return config_writes, env_write

    @pyqtSlot()
    def _submit_pending_writes(self) -> Optional[Future]:
        if self._writer_closed:
            return None
        config_writes, env_write = self._take_pending_writes()
        if not config_writes and env_write is None:
            return None

        future = self._write_executor.submit(
            self._write_snapshots,
            config_writes,
            env_write,
            self.env_path,
        )
        with self._write_lock:
            self._write_futures.add(future)

        had_env_write = env_write is not None

        def on_done(done_future: Future) -> None:
            error = None
            try:
                done_future.result()
            except Exception as exc:
                error = exc
            with self._write_lock:
                self._write_futures.discard(done_future)
                if error is not None:
                    self._write_errors.append(error)
                    if had_env_write:
                        self._env_write_failed = True
                elif had_env_write:
                    self._env_write_failed = False
            if error is not None:
                message = f"后台保存配置失败: {error}"
                self.logger.error(
                    message,
                    exc_info=(type(error), error, error.__traceback__),
                )
                self.write_failed.emit(str(error))

        future.add_done_callback(on_done)
        return future

    def _schedule_write(self) -> bool:
        if self._writer_shutdown_started or self._writer_closed:
            self.logger.warning("配置写入器已关闭，忽略保存请求")
            return False
        self._write_timer.start(self.SAVE_DEBOUNCE_MS)
        return True

    def request_save(self) -> bool:
        """Queue the current default config snapshots for a coalesced write."""
        return self.save_config_file()

    def save_config_file(self, config_path: Optional[str] = None) -> bool:
        """Queue a coalesced save; explicit export paths retain synchronous status."""
        try:
            if config_path:
                save_paths = [config_path]
            elif getattr(sys, "frozen", False):
                save_paths = [self.user_config_path]
            else:
                save_paths = [self.user_config_path, self.default_config_path]

            payloads = {
                os.path.abspath(path): self._build_config_payload(path)
                for path in save_paths
                if path
            }
            if not payloads:
                return False
            with self._write_lock:
                self._pending_config_writes.update(payloads)
            self.config_path = self.user_config_path
            if not self._schedule_write():
                return False
            return self.flush_pending_writes() if config_path else True
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False

    def reload_config(self):
        """
        强制从 .env 和 JSON 文件完全重新加载配置。
        这能确保外部对文件的任何修改都能在程序中生效。
        """
        self.logger.info("正在强制重新加载配置...")
        self.flush_pending_writes()
        self._remove_invalid_env_lines()

        # 1. 重新加载 .env 文件到 os.environ。翻译引擎会自动从此读取。
        load_app_dotenv(self.env_path, override=True)
        with self._write_lock:
            self._env_values = read_dotenv_file(self.env_path)
        self.logger.info(f".env 文件已从 {self.env_path} 重新加载，环境变量已更新。")

        # 2. 重新创建 AppSettings 对象 (用于UI设置)
        self.current_config = AppSettings()

        # 3. 按优先级重新加载配置文件
        self._load_configs_with_priority()

        # 4. 通知所有监听者配置已更改
        config_dict = self.current_config.model_dump()
        self.config_changed.emit(config_dict)
        self.logger.info("配置重载完成。")

    def reload_from_disk(self):
        """
        强制从当前设置的 config_path 重新加载配置, 并通知所有监听者。
        """
        self.flush_pending_writes()
        if self.config_path and os.path.exists(self.config_path):
            self.logger.debug(f"从磁盘重载配置: {os.path.basename(self.config_path)}")
            self.load_config_file(self.config_path)
        else:
            self.logger.warning("无法重载配置：config_path 未设置或文件不存在。")

    def get_config(self) -> AppSettings:
        """获取当前配置模型的深拷贝副本"""
        return self.current_config.model_copy(deep=True)

    def get_config_reference(self) -> AppSettings:
        """获取对当前配置模型的直接引用，谨慎使用。"""
        return self.current_config

    def get_current_preset(self) -> str:
        """获取当前预设名称"""
        return getattr(self.current_config.app, "current_preset", "默认")

    def set_current_preset(self, preset_name: str) -> bool:
        """设置当前预设名称并保存到配置文件"""
        try:
            self.current_config.app.current_preset = preset_name
            self.save_config_file()
            # 不输出日志，避免刷屏
            return True
        except Exception as e:
            self.logger.error(f"保存当前预设失败: {e}")
            return False

    def _convert_config_for_ui(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """已废弃: 历史上把 upscale_ratio 的 None 改成 '不使用' 字符串,
        但下游 UI 全部按 `value is None` 判分支, 转换后反而错乱(mangajanai 落到 else=>'x4')。
        现保留空实现仅作兼容, 实际不再做任何转换。"""
        return config_dict

    def set_config(self, config: AppSettings) -> None:
        """设置配置并通知监听者"""
        self.current_config = config.model_copy(deep=True)
        self.logger.debug("配置已更新，正在通知监听者...")
        config_dict = self.current_config.model_dump()
        self.config_changed.emit(config_dict)

    def update_config(self, updates: Dict[str, Any]) -> None:
        """更新配置的部分内容"""
        new_config_dict = self.current_config.model_dump()

        def deep_update(target, source):
            for key, value in source.items():
                if (
                    isinstance(value, dict)
                    and key in target
                    and isinstance(target[key], dict)
                ):
                    deep_update(target[key], value)
                else:
                    target[key] = value

        deep_update(new_config_dict, updates)

        self.current_config = AppSettings.model_validate(new_config_dict)
        self.logger.debug("配置已更新，正在通知监听者...")
        config_dict = self.current_config.model_dump()
        self.config_changed.emit(config_dict)

    def load_env_vars(self) -> Dict[str, str]:
        """Return the current in-memory environment snapshot."""
        with self._write_lock:
            return dict(self._env_values)

    def save_env_var(self, key: str, value: str) -> bool:
        """Update memory/os.environ immediately and coalesce the disk write."""
        try:
            return self.save_env_vars({key: value})
        except Exception as e:
            self.logger.error(f"保存环境变量失败: {e}")
            return False

    def save_env_vars(self, env_vars: Dict[str, str]) -> bool:
        """Apply a batch in memory and persist it with one atomic rewrite."""
        try:
            normalized = {
                validate_env_key(str(key)): (
                    "" if value is None else str(value).strip()
                )
                for key, value in env_vars.items()
            }
            with self._write_lock:
                self._env_values.update(normalized)
                if self._env_write_failed:
                    self._pending_env_replacement = dict(self._env_values)
                    self._pending_env_updates.clear()
                elif self._pending_env_replacement is not None:
                    self._pending_env_replacement.update(normalized)
                else:
                    self._pending_env_updates.update(normalized)
            os.environ.update(normalized)
            self._env_cache = None
            return self._schedule_write()
        except Exception as e:
            self.logger.error(f"批量保存环境变量失败: {e}")
            return False

    def delete_env_vars(self, keys: list[str] | tuple[str, ...] | set[str]) -> bool:
        """删除多个环境变量，并立即同步到运行环境。"""
        try:
            normalized_keys = [validate_env_key(str(key)) for key in keys]
            with self._write_lock:
                for key in normalized_keys:
                    self._env_values.pop(key, None)
                    if self._env_write_failed:
                        self._pending_env_replacement = dict(self._env_values)
                        self._pending_env_updates.clear()
                    elif self._pending_env_replacement is not None:
                        self._pending_env_replacement.pop(key, None)
                    else:
                        self._pending_env_updates[key] = None
            for key in normalized_keys:
                os.environ.pop(key, None)
            self._env_cache = None
            return self._schedule_write()
        except Exception as e:
            self.logger.error(f"删除环境变量失败: {e}")
            return False

    def replace_env_file(self, env_vars: Dict[str, str]) -> bool:
        """完全替换.env文件内容"""
        try:
            normalized_env_vars = {
                validate_env_key(str(key)): (
                    "" if value is None else str(value).strip()
                )
                for key, value in env_vars.items()
            }
            with self._write_lock:
                old_keys = set(self._env_values)
                self._env_values = dict(normalized_env_vars)
                self._pending_env_replacement = dict(normalized_env_vars)
                self._pending_env_updates.clear()
            for key in old_keys - normalized_env_vars.keys():
                os.environ.pop(key, None)
            os.environ.update(normalized_env_vars)
            self._env_cache = None
            return self._schedule_write()
        except Exception as e:
            self.logger.error(f"替换.env文件失败: {e}")
            return False

    def flush_pending_writes(self) -> bool:
        """Submit pending snapshots and wait until all accepted writes finish."""
        if QThread.currentThread() is self.thread():
            self._write_timer.stop()
        success = True
        while True:
            self._submit_pending_writes()
            with self._write_lock:
                futures = list(self._write_futures)
                has_pending = (
                    bool(self._pending_config_writes)
                    or bool(self._pending_env_updates)
                    or self._pending_env_replacement is not None
                )
            if not futures and not has_pending:
                with self._write_lock:
                    if self._write_errors:
                        success = False
                        self._write_errors.clear()
                return success
            for future in futures:
                try:
                    future.result()
                except Exception:
                    success = False

    def shutdown(self) -> bool:
        """Flush coalesced writes and stop the writer thread. Idempotent."""
        if self._writer_closed:
            return True
        self._writer_shutdown_started = True
        success = self.flush_pending_writes()
        self._write_executor.shutdown(wait=True, cancel_futures=False)
        self._writer_closed = True
        return success

    def validate_translator_env_vars(self, translator_name: str) -> Dict[str, bool]:
        """验证翻译器的环境变量是否完整"""
        env_vars = self.load_env_vars()
        required_vars = self.get_required_env_vars(translator_name)

        validation_result = {}
        for var in required_vars:
            value = env_vars.get(var, "")
            is_present = bool(value.strip())
            is_valid_format = (
                self.validate_api_key(value, var, translator_name)
                if is_present
                else True
            )
            validation_result[var] = is_present and is_valid_format

        return validation_result

    def get_missing_env_vars(self, translator_name: str) -> List[str]:
        """获取缺失的环境变量"""
        validation_result = self.validate_translator_env_vars(translator_name)
        return [var for var, is_valid in validation_result.items() if not is_valid]

    def is_translator_configured(self, translator_name: str) -> bool:
        """检查翻译器是否已完整配置"""
        missing_vars = self.get_missing_env_vars(translator_name)
        return len(missing_vars) == 0

    def get_default_config_path(self) -> str:
        """
        获取默认配置文件路径

        打包后配置文件在 app.exe 同级/config/config-example.json
        开发时在 项目根目录/config/config-example.json
        """
        return get_config_path("config-example.json")

    def get_user_config_path(self) -> str:
        """
        获取用户配置文件路径

        打包后：用户配置在 app.exe 同级/config/config.json（可写）
        开发时：在项目根目录的 config 目录
        """
        return get_config_path("config.json")

    def _load_configs_with_priority(self):
        """
        按优先级加载配置文件
        优先级：用户配置 > 默认配置 > 代码默认值
        """
        # 1. 先加载默认配置（如果存在）
        if os.path.exists(self.default_config_path):
            self.logger.info(f"加载默认配置: {self.default_config_path}")
            self.load_config_file(self.default_config_path)
        else:
            self.logger.warning(f"默认配置不存在: {self.default_config_path}")

        # 2. 再加载用户配置（如果存在），覆盖默认配置
        if os.path.exists(self.user_config_path):
            self.logger.info(f"加载用户配置: {self.user_config_path}")
            self.load_config_file(self.user_config_path)
            self.config_path = self.user_config_path
        else:
            self.logger.info(f"用户配置不存在: {self.user_config_path}")
            # 如果用户配置不存在，从默认配置创建一份
            if os.path.exists(self.default_config_path):
                self.logger.info("从默认配置创建用户配置")
                try:
                    # 复制默认配置到用户配置位置
                    os.makedirs(os.path.dirname(self.user_config_path), exist_ok=True)
                    with open(self.default_config_path, "r", encoding="utf-8") as src:
                        config_data = json.load(src)
                    with open(self.user_config_path, "w", encoding="utf-8") as dst:
                        json.dump(config_data, dst, indent=2, ensure_ascii=False)
                    self.logger.info(f"用户配置已创建: {self.user_config_path}")
                    self.config_path = self.user_config_path
                except Exception as e:
                    self.logger.error(f"创建用户配置失败: {e}")
                    self.config_path = self.default_config_path
            else:
                self.config_path = self.user_config_path

        # 3. 同步用户配置（添加新字段、删除旧字段）
        self._sync_user_config()

    def _sync_user_config(self):
        """
        同步用户配置文件
        - 如果默认配置新增字段 → 添加到用户配置
        - 如果默认配置删除字段 → 从用户配置删除
        - 保持用户修改的值不变
        """
        if not os.path.exists(self.default_config_path):
            self.logger.warning("默认配置不存在，跳过同步")
            return

        if not os.path.exists(self.user_config_path):
            self.logger.info("用户配置不存在，跳过同步")
            return

        try:
            # 读取默认配置（作为模板）
            with open(self.default_config_path, "r", encoding="utf-8") as f:
                default_data = migrate_legacy_custom_api_params_config(json.load(f))

            # 读取用户配置
            with open(self.user_config_path, "r", encoding="utf-8") as f:
                user_data = migrate_legacy_custom_api_params_config(json.load(f))

            # 同步配置（递归处理嵌套字典）
            synced_data = self._sync_dict(default_data, user_data)

            # 如果有变化，保存回用户配置
            if synced_data != user_data:
                self.logger.info("检测到配置结构变化，正在同步用户配置")
                with open(self.user_config_path, "w", encoding="utf-8") as f:
                    json.dump(synced_data, f, indent=2, ensure_ascii=False)
                self.logger.info("用户配置同步完成")

        except Exception as e:
            self.logger.error(f"同步用户配置失败: {e}")

    def _sync_dict(self, template: dict, user: dict) -> dict:
        """
        递归同步字典
        - 保留模板中存在的键
        - 删除模板中不存在的键
        - 保持用户设置的值
        """
        result = {}

        for key in template.keys():
            if key in user:
                # 用户配置有这个键
                if isinstance(template[key], dict) and isinstance(user[key], dict):
                    # 递归处理嵌套字典
                    result[key] = self._sync_dict(template[key], user[key])
                else:
                    # 使用用户的值
                    result[key] = user[key]
            else:
                # 用户配置没有这个键，使用模板的值
                result[key] = template[key]

        return result

    def load_default_config(self) -> bool:
        """加载默认配置"""
        default_path = self.get_default_config_path()
        return self.load_config_file(default_path)
