"""
配置管理服务
负责应用程序的配置加载、保存、验证和环境变量管理
"""
import os
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from dotenv import dotenv_values, set_key
import re

@dataclass
class TranslatorConfig:
    """翻译器配置信息"""
    name: str
    display_name: str
    required_env_vars: List[str]
    optional_env_vars: List[str] = field(default_factory=list)
    validation_rules: Dict[str, str] = field(default_factory=dict)

class ConfigService:
    """配置管理服务"""
    
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.env_path = os.path.join(root_dir, "..", ".env")
        self.config_path = None
        self.current_config = {}
        self.logger = logging.getLogger(__name__)
        self.callbacks = []
        
        # 延迟初始化翻译器配置（仅在需要时加载）
        self._translator_configs = None
        self._env_cache = None
        self._config_cache = None
    
    def register_callback(self, callback):
        """注册配置变更回调"""
        self.callbacks.append(callback)

    @property
    def translator_configs(self):
        """延迟加载翻译器配置"""
        if self._translator_configs is None:
            self._translator_configs = self._init_translator_configs()
        return self._translator_configs
        
    def _init_translator_configs(self) -> Dict[str, TranslatorConfig]:
        """初始化翻译器配置注册表"""
        configs = {
            "youdao": TranslatorConfig(
                name="youdao",
                display_name="有道翻译",
                required_env_vars=["YOUDAO_APP_KEY", "YOUDAO_SECRET_KEY"],
                validation_rules={
                    "YOUDAO_APP_KEY": r"^[a-zA-Z0-9]{8,}$",
                    "YOUDAO_SECRET_KEY": r"^[a-zA-Z0-9]{8,}$"
                }
            ),
            "baidu": TranslatorConfig(
                name="baidu",
                display_name="百度翻译",
                required_env_vars=["BAIDU_APP_ID", "BAIDU_SECRET_KEY"],
                validation_rules={
                    "BAIDU_APP_ID": r"^[0-9]{20}$",
                    "BAIDU_SECRET_KEY": r"^[a-zA-Z0-9]{32}$"
                }
            ),
            "openai": TranslatorConfig(
                name="openai",
                display_name="OpenAI",
                required_env_vars=["OPENAI_API_KEY"],
                optional_env_vars=["OPENAI_MODEL", "OPENAI_API_BASE", "OPENAI_HTTP_PROXY"],
                validation_rules={
                    "OPENAI_API_KEY": r"^sk-[a-zA-Z0-9]{48}$"
                }
            ),
            "deepl": TranslatorConfig(
                name="deepl",
                display_name="DeepL",
                required_env_vars=["DEEPL_AUTH_KEY"],
                validation_rules={
                    "DEEPL_AUTH_KEY": r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}:fx$"
                }
            ),
            "gemini": TranslatorConfig(
                name="gemini",
                display_name="Google Gemini",
                required_env_vars=["GEMINI_API_KEY"],
                optional_env_vars=["GEMINI_MODEL", "GEMINI_API_BASE"]
            ),
            "deepseek": TranslatorConfig(
                name="deepseek",
                display_name="DeepSeek",
                required_env_vars=["DEEPSEEK_API_KEY"],
                optional_env_vars=["DEEPSEEK_MODEL", "DEEPSEEK_API_BASE"]
            ),
            "groq": TranslatorConfig(
                name="groq",
                display_name="Groq",
                required_env_vars=["GROQ_API_KEY"],
                optional_env_vars=["GROQ_MODEL"]
            ),
            "sakura": TranslatorConfig(
                name="sakura",
                display_name="Sakura",
                required_env_vars=["SAKURA_API_BASE"],
                optional_env_vars=["SAKURA_DICT_PATH", "SAKURA_VERSION"]
            ),
            "custom_openai": TranslatorConfig(
                name="custom_openai",
                display_name="自定义 OpenAI",
                required_env_vars=["CUSTOM_OPENAI_API_KEY", "CUSTOM_OPENAI_API_BASE"],
                optional_env_vars=["CUSTOM_OPENAI_MODEL", "CUSTOM_OPENAI_MODEL_CONF"]
            )
        }
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
        """加载JSON配置文件"""
        try:
            if not os.path.exists(config_path):
                self.logger.error(f"配置文件不存在: {config_path}")
                return False
                
            with open(config_path, 'r', encoding='utf-8') as f:
                self.current_config = json.load(f)
                
            self.config_path = config_path
            self.logger.info(f"成功加载配置文件: {config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
            return False
    
    def save_config_file(self, config_path: Optional[str] = None) -> bool:
        """保存JSON配置文件"""
        try:
            save_path = config_path or self.config_path
            if not save_path:
                self.logger.error("没有指定保存路径")
                return False
                
            # 确保目录存在
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(self.current_config, f, indent=2, ensure_ascii=False)
                
            self.config_path = save_path
            self.logger.info(f"成功保存配置文件: {save_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False
    
    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return self.current_config.copy()
    
    def set_config(self, config: Dict[str, Any]) -> None:
        """设置配置并通知监听者"""
        self.current_config = config.copy()
        self.logger.info("配置已更新，正在通知监听者...")
        for callback in self.callbacks:
            try:
                callback()
            except Exception as e:
                self.logger.error(f"执行配置更新回调时出错: {e}")
    
    def update_config(self, updates: Dict[str, Any]) -> None:
        """更新配置的部分内容"""
        self.current_config.update(updates)
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """获取配置中的特定值"""
        return self.current_config.get(key, default)
    
    def set_config_value(self, key: str, value: Any) -> None:
        """设置配置中的特定值"""
        self.current_config[key] = value
    
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
        """保存单个环境变量"""
        try:
            # 确保.env文件存在
            if not os.path.exists(self.env_path):
                os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
                with open(self.env_path, 'w') as f:
                    pass
                    
            set_key(self.env_path, key, value)
            self.logger.info(f"保存环境变量: {key}")
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
        """获取默认配置文件路径"""
        return os.path.join(self.root_dir, "..", "examples", "config-example.json")
    
    def load_default_config(self) -> bool:
        """加载默认配置"""
        default_path = self.get_default_config_path()
        return self.load_config_file(default_path)