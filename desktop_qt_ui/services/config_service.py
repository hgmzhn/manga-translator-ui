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

from dotenv import dotenv_values, set_key, load_dotenv

from core.config_models import AppSettings


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
        # 打包后：root_dir = _internal，.env在exe所在目录（_internal的上一级）
        # 开发时：root_dir = 项目根目录，.env也在项目根目录
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
    
    def validate_api_key(self, key: str, var_name: str, translator_name: str) -> bool:
        """验证API密钥格式"""
        config = self.get_translator_config(translator_name)
        if not config or var_name not in config.validation_rules:
            return True  # 如果没有验证规则，则认为有效
            
        pattern = config.validation_rules[var_name]
        return bool(re.match(pattern, key))
    
    def load_config_file(self, config_path: str) -> bool:
        """加载JSON配置文件并与默认设置合并"""
        try:
            if not os.path.exists(config_path):
                self.logger.error(f"配置文件不存在: {config_path}")
                return False

            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                # 预处理以处理不规范的JSON，例如尾随逗号
                # content = re.sub(r',(\s*[\]})', r'\1', content) # Temporarily disabled for debugging
                loaded_data = json.loads(content)

            # 深层合并加载的数据和现有配置
            new_config_dict = self.current_config.dict()
            
            def deep_update(target, source):
                for key, value in source.items():
                    if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                        deep_update(target[key], value)
                    else:
                        target[key] = value
            
            deep_update(new_config_dict, loaded_data)
            
            self.current_config = AppSettings.parse_obj(new_config_dict)
            
            self.config_path = config_path
            self.logger.debug(f"加载配置: {os.path.basename(config_path)}")
            config_dict = self.current_config.dict()
            config_dict = self._convert_config_for_ui(config_dict)
            self.config_changed.emit(config_dict)
            return True
            
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
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
                config_dict = self.current_config.dict()
                
                # 读取现有配置，保留favorite_folders
                existing_favorites = None
                if os.path.exists(save_path):
                    try:
                        with open(save_path, 'r', encoding='utf-8') as f:
                            existing_config = json.load(f)
                            existing_favorites = existing_config.get('app', {}).get('favorite_folders')
                    except:
                        pass
                
                # 只有保存到模板配置时才重置临时状态（仅开发环境）
                is_default_config = save_path == self.default_config_path
                if is_default_config and not hasattr(sys, '_MEIPASS'):
                    # 重置临时UI状态为默认值
                    if 'app' not in config_dict:
                        config_dict['app'] = {}
                    config_dict['app']['last_open_dir'] = '.'
                    config_dict['app']['last_output_path'] = ''
                    # 模板配置中这两个字段保持固定值
                    config_dict['app']['favorite_folders'] = None
                    config_dict['app']['theme'] = 'light'
                    
                    if 'cli' in config_dict:
                        config_dict['cli']['verbose'] = False
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
        config_dict = self.current_config.dict()
        config_dict = self._convert_config_for_ui(config_dict)
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
        return self.current_config.copy(deep=True)

    def get_config_reference(self) -> AppSettings:
        """获取对当前配置模型的直接引用，谨慎使用。"""
        return self.current_config
    
    def _convert_config_for_ui(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """将配置转换为UI显示格式"""
        # 转换超分倍数：None -> '不使用', int -> str
        if 'upscale' in config_dict and 'upscale_ratio' in config_dict['upscale']:
            ratio = config_dict['upscale']['upscale_ratio']
            if ratio is None:
                config_dict['upscale']['upscale_ratio'] = '不使用'
            else:
                config_dict['upscale']['upscale_ratio'] = str(ratio)
        return config_dict
    
    def set_config(self, config: AppSettings) -> None:
        """设置配置并通知监听者"""
        self.current_config = config.copy(deep=True)
        self.logger.debug("配置已更新，正在通知监听者...")
        config_dict = self.current_config.dict()
        config_dict = self._convert_config_for_ui(config_dict)
        self.config_changed.emit(config_dict)
    
    def update_config(self, updates: Dict[str, Any]) -> None:
        """更新配置的部分内容"""
        new_config_dict = self.current_config.dict()

        def deep_update(target, source):
            for key, value in source.items():
                if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                    deep_update(target[key], value)
                else:
                    target[key] = value
        
        deep_update(new_config_dict, updates)

        self.current_config = AppSettings.parse_obj(new_config_dict)
        self.logger.debug("配置已更新，正在通知监听者...")
        config_dict = self.current_config.dict()
        config_dict = self._convert_config_for_ui(config_dict)
        self.config_changed.emit(config_dict)

    def load_env_vars(self) -> Dict[str, str]:
        """加载环境变量"""
        try:
            if os.path.exists(self.env_path):
                return dotenv_values(self.env_path)
            return {}
        except Exception as e:
            self.logger.error(f"加载环境变量失败: {e}")
            return {}
    
    def save_env_var(self, key: str, value: str) -> bool:
        """保存单个环境变量 - 使用手动处理避免set_key的自动引号"""
        try:
            # 去除首尾空格
            value = value.strip()
            
            if not os.path.exists(self.env_path):
                os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
                with open(self.env_path, 'w', encoding='utf-8') as f:
                    f.write(f"{key}={value}\n")
            else:
                # 手动读取、更新、写入，避免set_key的自动处理
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
                                    f.write(f"{key}={value}\n")
                                    key_found = True
                                    continue
                        f.write(line)
                    
                    # 如果键不存在，追加到文件末尾
                    if not key_found:
                        f.write(f"{key}={value}\n")
            
            # 重新加载环境变量到os.environ，使其立即生效
            load_dotenv(self.env_path, override=True)
            self.logger.info(f"保存环境变量: {key}={value}")
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
            return True
        except Exception as e:
            self.logger.error(f"批量保存环境变量失败: {e}")
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
                default_data = json.load(f)
            
            # 读取用户配置
            with open(self.user_config_path, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
            
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