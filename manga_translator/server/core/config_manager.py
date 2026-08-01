"""
配置管理模块

负责加载、保存和管理服务器配置和管理员配置。
"""

import json
import os
from contextlib import contextmanager
from typing import Optional

from manga_translator import Config
from manga_translator.custom_api_params import migrate_legacy_custom_api_params_config
from manga_translator.runtime_paths import get_config_path
from manga_translator.server_paths import ADMIN_CONFIG_FILE, ensure_server_data_layout
from manga_translator.utils import BASE_PATH

# 配置文件路径
ADMIN_CONFIG_PATH = str(ADMIN_CONFIG_FILE)
# 默认配置文件：打包后位于 app.exe 同级的 config/config.json。
SERVER_CONFIG_PATH = get_config_path('config.json')

# 文件目录路径
FONTS_DIR = os.path.join(BASE_PATH, 'fonts')
PROMPTS_DIR = os.path.join(BASE_PATH, 'dict')
PROMPTS_DIR = os.path.abspath(PROMPTS_DIR)

# 确保目录存在
os.makedirs(FONTS_DIR, exist_ok=True)
os.makedirs(PROMPTS_DIR, exist_ok=True)
ensure_server_data_layout()

# i18n 相关路径
desktop_locales_dir = os.path.join(BASE_PATH, 'desktop_qt_ui', 'locales')
desktop_locales_dir = os.path.abspath(desktop_locales_dir)

# 全局翻译字典缓存
translations_cache = {}

print(f"[INFO] i18n locales directory: {desktop_locales_dir}")
print(f"[INFO] Fonts directory: {FONTS_DIR}")
print(f"[INFO] Prompts directory: {PROMPTS_DIR}")

# 默认管理端配置
DEFAULT_ADMIN_SETTINGS = {
    'visible_sections': ['translator', 'cli', 'detector', 'ocr', 'inpainter', 'render', 'upscale', 'colorizer'],
    'hidden_keys': [
        'upscale.realcugan_model',
        # CLI配置默认隐藏
        'cli.format',
        'cli.save_quality',
        'cli.overwrite',
        'cli.skip_no_text',
        'cli.save_text',
        'cli.load_text',
        'cli.translate_json_only',
        'cli.template',
        # 'cli.attempts',  # 不再隐藏，让用户可以设置重试次数
        'cli.ignore_errors',
        'cli.batch_size',
        'cli.batch_concurrent',
        'cli.use_gpu',
        'cli.verbose',
        'cli.psd_script_only',  # Web UI隐藏PSD脚本模式参数
        'cli.generate_and_export',
        'cli.colorize_only',
        'cli.upscale_only',
        'cli.inpaint_only',
        # Qt UI 专属参数（替换翻译模式相关）
        'cli.replace_translation',
        'render.enable_template_alignment',
        'render.paste_mask_dilation_pixels',
        # 翻译器高级配置
        'translator.enable_post_translation_check',
        'translator.post_check_max_retry_attempts',
        'translator.post_check_repetition_threshold',
        'translator.post_check_target_lang_threshold',
        'translator.translator_chain',
        'translator.selective_translation',
        'translator.skip_lang',
         'use_custom_api_params',  # 仅用于服务器端，Web UI 用户端不显示
         'render.gimp_font',
         'detector.import_yolo_labels',  # Qt UI 专属 - 导入固定YOLO框
      ],
    'readonly_keys': [],
    'default_values': {},
    'allowed_translators': [],
    'allowed_languages': [],
    'allowed_workflows': [],
    'permissions': {
        'can_upload_fonts': True,
        'can_delete_fonts': True,
        'can_upload_prompts': True,
        'can_delete_prompts': True,
        'can_add_folders': True,
    },
    'upload_limits': {
        'max_image_size_mb': 10,
        'max_images_per_batch': 50,
    },
    'user_access': {
        'require_password': False,
        'user_password': '',
    },
    'api_key_policy': {
        'require_user_keys': False,
        'allow_server_keys': True,
        'save_user_keys_to_server': False,
    },
    'show_env_to_users': False,
    'announcement': {
        'enabled': False,
        'message': '',
        'type': 'info',
    },
    'registration': {
        'enabled': False,  # 是否开启用户注册
        'default_group': 'default',  # 新注册用户的默认用户组
        'require_approval': False,  # 是否需要管理员审批（预留）
    },
}

# 所有可用的翻译流程
AVAILABLE_WORKFLOWS = [
    'normal',
    'export_trans',
    'export_raw',
    'import_trans',
    'colorize',
    'upscale',
    'inpaint',
]


def load_admin_settings() -> dict:
    """从文件加载管理员配置"""
    if os.path.exists(ADMIN_CONFIG_PATH):
        try:
            with open(ADMIN_CONFIG_PATH, 'r', encoding='utf-8') as f:
                loaded_settings = json.load(f)
                print(f"[INFO] Loaded admin settings from: {ADMIN_CONFIG_PATH}")
                # 合并默认配置和加载的配置
                import copy
                settings = copy.deepcopy(DEFAULT_ADMIN_SETTINGS)
                for key, value in loaded_settings.items():
                    if key in settings and isinstance(settings[key], list) and isinstance(value, list):
                        # 列表类型：以默认值为基础，追加文件中有但默认没有的项
                        default_set = set(settings[key])
                        merged = list(settings[key])
                        for item in value:
                            if item not in default_set:
                                merged.append(item)
                        settings[key] = merged
                    elif key in settings and isinstance(settings[key], dict) and isinstance(value, dict):
                        # 字典类型：深度合并
                        merged_dict = copy.deepcopy(settings[key])
                        merged_dict.update(value)
                        settings[key] = merged_dict
                    else:
                        settings[key] = value
                
                # 如果配置文件中没有密码，尝试从环境变量读取
                if not settings.get('admin_password'):
                    env_password = os.environ.get('MANGA_TRANSLATOR_ADMIN_PASSWORD')
                    if env_password and len(env_password) >= 6:
                        settings['admin_password'] = env_password
                        # 保存到配置文件
                        save_admin_settings(settings)
                        print("[INFO] Admin password set from environment variable MANGA_TRANSLATOR_ADMIN_PASSWORD")
                    elif env_password:
                        print("[WARNING] MANGA_TRANSLATOR_ADMIN_PASSWORD is too short (minimum 6 characters)")
                
                return settings
        except Exception as e:
            print(f"[ERROR] Failed to load admin settings: {e}")
            return DEFAULT_ADMIN_SETTINGS.copy()
    else:
        print(f"[INFO] Admin config file not found, using defaults: {ADMIN_CONFIG_PATH}")
        settings = DEFAULT_ADMIN_SETTINGS.copy()
        
        # 首次启动时，尝试从环境变量读取密码
        env_password = os.environ.get('MANGA_TRANSLATOR_ADMIN_PASSWORD')
        if env_password and len(env_password) >= 6:
            settings['admin_password'] = env_password
            # 保存到配置文件
            save_admin_settings(settings)
            print("[INFO] Admin password set from environment variable MANGA_TRANSLATOR_ADMIN_PASSWORD")
        elif env_password:
            print("[WARNING] MANGA_TRANSLATOR_ADMIN_PASSWORD is too short (minimum 6 characters)")
        
        return settings


def save_admin_settings(settings: dict) -> bool:
    """保存管理员配置到文件"""
    try:
        os.makedirs(os.path.dirname(ADMIN_CONFIG_PATH), exist_ok=True)
        with open(ADMIN_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        print(f"[INFO] Saved admin settings to: {ADMIN_CONFIG_PATH}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save admin settings: {e}")
        return False


def load_default_config_dict() -> dict:
    """加载默认配置文件，返回字典格式（包含Qt UI的完整配置）"""
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config_dict = migrate_legacy_custom_api_params_config(json.load(f))
            return config_dict
        except Exception as e:
            print(f"[WARNING] Failed to load default config from {SERVER_CONFIG_PATH}: {e}")
            return {}
    else:
        print(f"[WARNING] Default config file not found: {SERVER_CONFIG_PATH}")
        return {}


def load_default_config() -> Config:
    """加载默认配置文件，返回Config对象"""
    config_dict = load_default_config_dict()
    if config_dict:
        try:
            config = Config.model_validate(config_dict)
            return config
        except Exception as e:
            print(f"[WARNING] Failed to parse config: {e}")
            return Config()
    return Config()


def parse_config(config_str: str) -> Config:
    """解析配置，如果为空则使用默认配置"""
    if not config_str or config_str.strip() in ('{}', ''):
        print("[INFO] No config provided, using default config from config/config.json")
        return load_default_config()
    else:
        config = Config.parse_raw(config_str)
        # Config 现在有 cli 属性，cli.attempts 会自动存储
        return config


def get_available_workflows(mode: str = 'user', admin_settings: Optional[dict] = None) -> list:
    """
    获取可用的工作流列表
    
    Args:
        mode: 'user' 或 'admin'
        admin_settings: 管理员设置字典（可选）
    
    Returns:
        可用的工作流列表
    """
    # 如果是用户模式且管理员设置了允许的流程列表
    if mode == 'user' and admin_settings and admin_settings.get('allowed_workflows'):
        allowed = admin_settings['allowed_workflows']
        return [wf for wf in AVAILABLE_WORKFLOWS if wf in allowed]
    
    return AVAILABLE_WORKFLOWS


@contextmanager
def temp_env_vars(env_vars: dict):
    """
    临时设置环境变量的上下文管理器
    
    注意：此函数不再使用全局锁，因为：
    1. 并发控制由 translation_semaphore 处理
    2. 全局锁会导致所有翻译任务串行化，严重影响性能
    3. 如果需要用户级别的 API Key 隔离，应该在翻译器层面处理
    
    Args:
        env_vars: 要临时设置的环境变量字典
    """
    import logging
    logger = logging.getLogger('manga_translator.server')
    
    if not env_vars:
        # 没有用户环境变量，直接使用服务器默认值
        yield
        return
    
    logger.debug(f"[TempEnv] Setting temporary env vars: {list(env_vars.keys())}")
    
    # 保存原始值
    original_values = {}
    for key in env_vars:
        original_values[key] = os.environ.get(key)
    
    try:
        # 设置新值
        for key, value in env_vars.items():
            if value:  # 只设置非空值
                os.environ[key] = str(value)
                logger.debug(f"[TempEnv] Set {key}=***")
        
        # 清除翻译器缓存，强制重新创建（这样才能读取新的环境变量）
        try:
            from manga_translator.translators import translator_cache
            translator_cache.clear()
            logger.debug("[TempEnv] Cleared translator cache")
        except Exception as e:
            logger.warning(f"[TempEnv] Failed to clear translator cache: {e}")
        
        yield
    finally:
        # 恢复原始值
        for key, original_value in original_values.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        
        logger.debug("[TempEnv] Restored original env vars")
        
        # 再次清除缓存，确保下次使用服务器的
        try:
            from manga_translator.translators import translator_cache
            translator_cache.clear()
        except Exception:
            pass


def init_server_config_file():
    """初始化服务器配置文件（如果不存在，从模板复制）"""
    if not os.path.exists(SERVER_CONFIG_PATH):
        EXAMPLE_CONFIG_PATH = get_config_path('config-example.json')
        if os.path.exists(EXAMPLE_CONFIG_PATH):
            import shutil
            shutil.copy(EXAMPLE_CONFIG_PATH, SERVER_CONFIG_PATH)
            print(f"[INFO] Created server config from template: {SERVER_CONFIG_PATH}")
        else:
            print("[WARNING] Template config not found, will use default Config()")


# ============================================================================
# i18n Functions
# ============================================================================

def load_translation(locale: str) -> dict:
    """加载指定语言的翻译文件"""
    if locale in translations_cache:
        return translations_cache[locale]
    
    locales_dir = os.path.realpath(desktop_locales_dir)
    locale_file = os.path.realpath(os.path.join(locales_dir, f"{locale}.json"))
    if not locale_file.startswith(locales_dir + os.sep):
        print(f"[WARNING] Invalid locale: {locale}")
        return {}
    if os.path.dirname(locale_file) != locales_dir:
        print(f"[WARNING] Invalid locale: {locale}")
        return {}
    if os.path.isfile(locale_file):
        try:
            with open(locale_file, 'r', encoding='utf-8') as f:
                translations = json.load(f)
                translations_cache[locale] = translations
                print(f"[INFO] Loaded {len(translations)} translations for {locale}")
                return translations
        except Exception as e:
            print(f"[ERROR] Failed to load translation file {locale_file}: {e}")
            return {}
    else:
        print(f"[WARNING] Translation file not found: {locale_file}")
        return {}


def get_available_locales() -> dict:
    """获取可用的语言列表"""
    locales = {}
    if os.path.exists(desktop_locales_dir):
        for filename in os.listdir(desktop_locales_dir):
            if filename.endswith('.json'):
                locale_code = filename[:-5]  # 移除 .json
                locales[locale_code] = locale_code
    return locales


# ============================================================================
# 配置热加载
# ============================================================================

# 记录配置文件的修改时间，用于检测变化
_admin_config_mtime = 0


def reload_admin_settings_if_changed() -> bool:
    """
    检查配置文件是否变化，如果变化则重新加载。
    
    Returns:
        bool: 配置是否被重新加载
    """
    global admin_settings, _admin_config_mtime
    
    try:
        if not os.path.exists(ADMIN_CONFIG_PATH):
            return False
        
        current_mtime = os.path.getmtime(ADMIN_CONFIG_PATH)
        
        if current_mtime > _admin_config_mtime:
            old_concurrent = admin_settings.get('max_concurrent_tasks', 3)
            
            # 重新加载配置
            admin_settings = load_admin_settings()
            _admin_config_mtime = current_mtime
            
            new_concurrent = admin_settings.get('max_concurrent_tasks', 3)
            
            # 如果并发数变化，更新 semaphore
            if old_concurrent != new_concurrent:
                from .task_manager import update_server_config
                update_server_config({'max_concurrent_tasks': new_concurrent})
                print(f"[INFO] 配置热加载: max_concurrent_tasks {old_concurrent} -> {new_concurrent}")
            
            return True
        
        return False
    except Exception as e:
        print(f"[WARNING] 配置热加载失败: {e}")
        return False


def get_admin_settings() -> dict:
    """
    获取管理员配置（会自动检查热加载）
    """
    reload_admin_settings_if_changed()
    return admin_settings


# ============================================================================
# Module Initialization
# ============================================================================

# 加载管理端配置（模块级别）
admin_settings = load_admin_settings()

# 初始化配置文件修改时间
if os.path.exists(ADMIN_CONFIG_PATH):
    _admin_config_mtime = os.path.getmtime(ADMIN_CONFIG_PATH)

print(f"[INFO] Available locales: {list(get_available_locales().keys())}")
