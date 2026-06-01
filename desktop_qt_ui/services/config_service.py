"""
配置管理服务
负责应用程序的配置加载、保存、验证和环境变量管理
"""
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.config_models import AppSettings
from dotenv import dotenv_values, load_dotenv

from manga_translator.colorization.prompt_loader import ensure_ai_colorizer_prompt_file
from manga_translator.custom_api_params import (
    ensure_custom_api_params_file,
    migrate_legacy_custom_api_params_config,
)
from manga_translator.ocr.prompt_loader import ensure_ai_ocr_prompt_file
from manga_translator.rendering.prompt_loader import ensure_ai_renderer_prompt_file
from manga_translator.utils.openai_compat import is_openai_api_key_optional

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

from PyQt6.QtCore import QObject, pyqtSignal


class ConfigService(QObject):
    """配置管理服务"""

    config_changed = pyqtSignal(dict)
    
    def __init__(self, root_dir: str):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.root_dir = root_dir
        # .env文件应该在exe所在目录（可写位置）
        # 打包后：E:\manga-translator-cpu-v1.9.2\.env
        # 开发时：项目根目录\.env
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            exe_dir = os.path.dirname(sys.executable)
            self.env_path = os.path.join(exe_dir, ".env")
        else:
            self.env_path = os.path.join(self.root_dir, ".env")

        # Use get_default_config_path() for PyInstaller compatibility
        # Temporarily set a placeholder, will be properly set after initialization
        self.default_config_path = None
        self.user_config_path = None

        self.config_path = None # This will hold the path of a loaded file
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
        if hasattr(sys, '_MEIPASS'):
            self.logger.debug(f"打包环境，sys._MEIPASS = {sys._MEIPASS}")

        # 加载配置：优先级 用户配置 > 默认配置 > 代码默认值
        self._load_configs_with_priority()
        
        self._translator_configs = None
        self._env_cache = None
        self._config_cache = None

    @property
    def translator_configs(self):
        """延迟加载翻译器配置"""
        if self._translator_configs is None:
            self._translator_configs = self._init_translator_configs()
        return self._translator_configs
        
    def _init_translator_configs(self) -> Dict[str, TranslatorConfig]:
        """从JSON文件初始化翻译器配置注册表"""
        configs = {}
        
        if hasattr(sys, '_MEIPASS'):
            # Packaged environment
            config_path = os.path.join(sys._MEIPASS, "examples", "config", "translators.json")
        else:
            # Development environment
            config_path = os.path.join(self.root_dir, "examples", "config", "translators.json")

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
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
            for key in translator_config.required_env_vars + translator_config.optional_env_vars:
                if key and key not in seen:
                    seen.add(key)
                    env_keys.append(key)

        for key in PRESET_SPECIAL_ENV_VARS:
            if key not in seen:
                seen.add(key)
                env_keys.append(key)

        return env_keys

    @staticmethod
    def _has_env_value(env_vars: Dict[str, str], key: str) -> bool:
        value = env_vars.get(key, "")
        return bool(str(value or "").strip())

    def get_missing_runtime_api_requirements(
        self,
        config: AppSettings,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取当前配置下缺失的运行时 API Key 要求。"""
        merged_env_vars = {
            key: str(value or "")
            for key, value in self.load_env_vars().items()
        }
        if env_vars:
            for key, value in env_vars.items():
                merged_env_vars[key] = str(value or "")

        checks = [
            ("translator", "translator", getattr(config.translator, "translator", None)),
            ("ocr", "ocr", getattr(config.ocr, "ocr", None)),
            ("colorizer", "colorizer", getattr(config.colorizer, "colorizer", None)),
            ("render", "renderer", getattr(config.render, "renderer", None)),
        ]

        if bool(getattr(config.ocr, "use_hybrid_ocr", False)):
            checks.append(("ocr", "secondary_ocr", getattr(config.ocr, "secondary_ocr", None)))

        missing: List[Dict[str, Any]] = []
        for section, setting, selected_value in checks:
            feature_name = str(selected_value or "").strip()
            if not feature_name:
                continue

            requirement = RUNTIME_API_REQUIREMENTS.get(feature_name)
            if not requirement:
                continue

            accepted_env_vars = list(requirement.get("accepted_env_vars", []))
            if any(self._has_env_value(merged_env_vars, key) for key in accepted_env_vars):
                continue

            accepted_base_env_vars = list(requirement.get("accepted_base_env_vars", []))
            if requirement.get("allow_empty_api_key_for_local_base") and any(
                is_openai_api_key_optional("", merged_env_vars.get(key, ""))
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

            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                loaded_data = migrate_legacy_custom_api_params_config(json.loads(content))

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
                        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
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
                                error_keys.append((current_path, value, str(validate_err)))
                                self.logger.warning(f"配置键 '{current_path}' 值无效: {value}，使用默认值: {old_value}")
                    except Exception as e:
                        error_keys.append((current_path, value, str(e)))
                        self.logger.warning(f"配置键 '{current_path}' 加载失败: {e}，保持默认值")
            
            safe_deep_update(new_config_dict, loaded_data)
            
            # 最终验证并创建配置对象
            try:
                self.current_config = AppSettings.model_validate(new_config_dict)
            except Exception as final_err:
                self.logger.error(f"配置验证失败，使用默认配置: {final_err}")
                self.current_config = AppSettings()
            
            # 报告错误的键
            if error_keys:
                self.logger.warning(f"配置文件中有 {len(error_keys)} 个无效配置项已使用默认值替换:")
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
    
    def save_config_file(self, config_path: Optional[str] = None) -> bool:
        """
        保存JSON配置文件
        - 如果指定路径，只保存到指定路径
        - 否则保存到用户配置路径（打包后在_internal/examples，开发时在examples目录）
        - 同时更新模板配置（开发环境）
        """
        try:
            if config_path:
                # 如果指定了路径，只保存到指定路径
                save_paths = [config_path]
            else:
                # 打包环境和开发环境都保存到用户配置
                # 开发环境额外保存到模板配置
                if hasattr(sys, '_MEIPASS'):
                    save_paths = [self.user_config_path]
                else:
                    save_paths = [self.user_config_path, self.default_config_path]
            
            success_count = 0
            for save_path in save_paths:
                if not save_path:
                    continue
                
                # 获取当前配置
                config_dict = self.current_config.model_dump()
                
                # 强制设置 min_box_area_ratio 为 0（模板配置固定值）
                if 'detector' not in config_dict:
                    config_dict['detector'] = {}
                config_dict['detector']['min_box_area_ratio'] = 0
                
                # 读取现有配置，保留favorite_folders
                existing_favorites = None
                if os.path.exists(save_path):
                    try:
                        with open(save_path, 'r', encoding='utf-8') as f:
                            existing_config = json.load(f)
                            existing_favorites = existing_config.get('app', {}).get('favorite_folders')
                    except Exception:
                        pass
                
                # 只有保存到模板配置时才重置临时状态（仅开发环境）
                is_default_config = save_path == self.default_config_path
                if is_default_config and not hasattr(sys, '_MEIPASS'):
                    # 读取现有模板配置，保留某些字段
                    if os.path.exists(save_path):
                        try:
                            with open(save_path, 'r', encoding='utf-8') as f:
                                json.load(f)
                        except Exception:
                            pass
                    
                    # 重置临时UI状态为默认值
                    if 'app' not in config_dict:
                        config_dict['app'] = {}
                    config_dict['app']['last_open_dir'] = '.'
                    config_dict['app']['last_output_path'] = ''
                    # 模板配置中这些字段保持固定值
                    config_dict['app']['favorite_folders'] = None
                    config_dict['app']['theme'] = 'light'
                    config_dict['app']['ui_language'] = 'auto'  # 模板配置始终为 auto
                    config_dict['app']['current_preset'] = '默认'  # 模板配置始终为默认预设
                    config_dict['app']['saved_colors'] = None  # 模板配置中保存的颜色始终为空
                    config_dict['app']['saved_style_presets'] = None  # 模板配置中不保留用户自定义样式
                    
                    if 'cli' in config_dict:
                        config_dict['cli']['verbose'] = False
                    
                    # 模板配置中的字体路径和提示词路径始终保持为默认示例文件
                    # 这样用户可以看到示例，但不会被个人设置覆盖
                    if 'render' not in config_dict:
                        config_dict['render'] = {}
                    config_dict['render']['font_path'] = 'fonts/Arial-Unicode-Regular.ttf'
                    
                    # AI断句相关设置在模板配置中始终为关闭状态
                    config_dict['render']['disable_auto_wrap'] = False
                    config_dict['render']['center_text_in_bubble'] = False
                    config_dict['render']['optimize_line_breaks'] = False
                    config_dict['render']['check_br_and_retry'] = False
                    config_dict['render']['strict_smart_scaling'] = False
                    
                    if 'translator' not in config_dict:
                        config_dict['translator'] = {}
                    config_dict['translator']['high_quality_prompt_path'] = 'dict/prompt_example.yaml'
                    
                    # 混合OCR在模板配置中始终为关闭状态
                    if 'ocr' not in config_dict:
                        config_dict['ocr'] = {}
                    config_dict['ocr']['use_hybrid_ocr'] = False
                    
                    # PSD导出字体在模板配置中始终为空
                    if 'cli' not in config_dict:
                        config_dict['cli'] = {}
                    config_dict['cli']['psd_font'] = None
                else:
                    # 用户配置保留favorite_folders（但如果当前配置已经有新值，就不覆盖）
                    if existing_favorites is not None:
                        if 'app' not in config_dict:
                            config_dict['app'] = {}
                        # 只有当前配置中没有 favorite_folders 时，才使用旧值
                        if 'favorite_folders' not in config_dict.get('app', {}):
                            config_dict['app']['favorite_folders'] = existing_favorites
                
                try:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json.dump(config_dict, f, indent=2, ensure_ascii=False)
                    
                    success_count += 1
                except Exception as e:
                    self.logger.error(f"保存配置失败 ({os.path.basename(save_path)}): {e}")
            
            if success_count > 0:
                self.config_path = self.user_config_path
                return True
            else:
                self.logger.error("所有配置文件保存失败")
                return False
            
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False

    def reload_config(self):
        """
        强制从 .env 和 JSON 文件完全重新加载配置。
        这能确保外部对文件的任何修改都能在程序中生效。
        """
        self.logger.info("正在强制重新加载配置...")
        
        # 1. 重新加载 .env 文件到 os.environ。翻译引擎会自动从此读取。
        load_dotenv(self.env_path, override=True)
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
        return getattr(self.current_config.app, 'current_preset', '默认')
    
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
                if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                    deep_update(target[key], value)
                else:
                    target[key] = value
        
        deep_update(new_config_dict, updates)

        self.current_config = AppSettings.model_validate(new_config_dict)
        self.logger.debug("配置已更新，正在通知监听者...")
        config_dict = self.current_config.model_dump()
        self.config_changed.emit(config_dict)

    def load_env_vars(self) -> Dict[str, str]:
        """加载环境变量"""
        try:
            if os.path.exists(self.env_path):
                return dotenv_values(self.env_path)
            else:
                return {}
        except Exception as e:
            self.logger.error(f"加载环境变量失败: {e}")
            return {}
    
    def save_env_var(self, key: str, value: str) -> bool:
        """保存单个环境变量 - 统一使用双引号包裹值"""
        try:
            # 去除首尾空格
            value = value.strip()
            
            # 转义双引号和反斜杠，然后用双引号包裹
            escaped_value = value.replace('\\', '\\\\').replace('"', '\\"')
            formatted_line = f'{key}="{escaped_value}"\n'
            
            if not os.path.exists(self.env_path):
                os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
                with open(self.env_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_line)
            else:
                # 手动读取、更新、写入
                lines = []
                key_found = False
                with open(self.env_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # 更新或添加键值对
                with open(self.env_path, 'w', encoding='utf-8') as f:
                    for line in lines:
                        stripped = line.strip()
                        if stripped and not stripped.startswith('#'):
                            if '=' in stripped:
                                existing_key = stripped.split('=', 1)[0].strip()
                                if existing_key == key:
                                    f.write(formatted_line)
                                    key_found = True
                                    continue
                        f.write(line)
                    
                    # 如果键不存在，追加到文件末尾
                    if not key_found:
                        f.write(formatted_line)
            
            # 重新加载环境变量到os.environ，使其立即生效
            load_dotenv(self.env_path, override=True)
            return True

        except Exception as e:
            self.logger.error(f"保存环境变量失败: {e}")
            return False
    
    def save_env_vars(self, env_vars: Dict[str, str]) -> bool:
        """批量保存环境变量"""
        try:
            for key, value in env_vars.items():
                if not self.save_env_var(key, value):
                    return False
            
            # 清除缓存，确保下次读取时获取最新值
            self._env_cache = None
            
            return True
        except Exception as e:
            self.logger.error(f"批量保存环境变量失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def replace_env_file(self, env_vars: Dict[str, str]) -> bool:
        """完全替换.env文件内容"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
            
            # 写入新的.env文件
            with open(self.env_path, 'w', encoding='utf-8') as f:
                for key, value in env_vars.items():
                    # 去除首尾空格
                    value = value.strip()
                    # 转义双引号和反斜杠，然后用双引号包裹
                    escaped_value = value.replace('\\', '\\\\').replace('"', '\\"')
                    f.write(f'{key}="{escaped_value}"\n')
            
            # 重新加载环境变量到os.environ，使其立即生效
            load_dotenv(self.env_path, override=True)
            
            # 清除缓存
            self._env_cache = None
            
            return True
        except Exception as e:
            self.logger.error(f"替换.env文件失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def validate_translator_env_vars(self, translator_name: str) -> Dict[str, bool]:
        """验证翻译器的环境变量是否完整"""
        env_vars = self.load_env_vars()
        required_vars = self.get_required_env_vars(translator_name)
        
        validation_result = {}
        for var in required_vars:
            value = env_vars.get(var, "")
            is_present = bool(value.strip())
            is_valid_format = self.validate_api_key(value, var, translator_name) if is_present else True
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

        打包后配置文件在 _internal/examples/config-example.json
        开发时在 项目根目录/examples/config-example.json
        """
        if hasattr(sys, '_MEIPASS'):
            # 打包环境：sys._MEIPASS 指向 _internal 目录
            return os.path.join(sys._MEIPASS, 'examples', 'config-example.json')
        else:
            # 开发环境
            return os.path.join(self.root_dir, "examples", "config-example.json")
    
    def get_user_config_path(self) -> str:
        """
        获取用户配置文件路径
        
        打包后：用户配置在_internal/examples/config.json（可写）
        开发时：在项目根目录的examples目录
        """
        if hasattr(sys, '_MEIPASS'):
            # 打包环境：用户配置在_internal/examples目录
            return os.path.join(sys._MEIPASS, 'examples', 'config.json')
        else:
            # 开发环境：用户配置在项目根目录的examples目录
            return os.path.join(self.root_dir, "examples", "config.json")
    
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
                    with open(self.default_config_path, 'r', encoding='utf-8') as src:
                        config_data = json.load(src)
                    with open(self.user_config_path, 'w', encoding='utf-8') as dst:
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
            with open(self.default_config_path, 'r', encoding='utf-8') as f:
                default_data = migrate_legacy_custom_api_params_config(json.load(f))
            
            # 读取用户配置
            with open(self.user_config_path, 'r', encoding='utf-8') as f:
                user_data = migrate_legacy_custom_api_params_config(json.load(f))
            
            # 同步配置（递归处理嵌套字典）
            synced_data = self._sync_dict(default_data, user_data)
            
            # 如果有变化，保存回用户配置
            if synced_data != user_data:
                self.logger.info("检测到配置结构变化，正在同步用户配置")
                with open(self.user_config_path, 'w', encoding='utf-8') as f:
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
