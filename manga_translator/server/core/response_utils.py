"""
响应工具模块

负责图像转换、JSON转换和临时环境变量管理。
"""

import io
import json

from fastapi import HTTPException

from manga_translator import Config
from manga_translator.server.core.api_key_policy import get_effective_api_key_policy
from manga_translator.server.core.group_management_service import (
    get_group_management_service,
)
from manga_translator.server.core.middleware import get_services
from manga_translator.server.runtime_api import (
    apply_runtime_api_overrides,
    clear_runtime_api_overrides,
)
from manga_translator.server.to_json import to_translation


def transform_to_image(ctx):
    """
    将翻译上下文转换为图像字节
    
    Args:
        ctx: 翻译上下文对象
    
    Returns:
        图像字节数据
    """
    # 检查 ctx.result 是否存在
    if ctx.result is None:
        raise HTTPException(500, detail="Translation failed: no result image generated")
    
    # 检查是否使用占位符（在web模式下final.png保存后会设置此标记）
    if hasattr(ctx, 'use_placeholder') and ctx.use_placeholder:
        # ctx.result已经是1x1占位符图片，快速传输
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        return img_byte_arr.getvalue()

    # 返回完整的翻译结果
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()


def transform_to_json(ctx):
    """
    将翻译上下文转换为JSON字节
    
    Args:
        ctx: 翻译上下文对象
    
    Returns:
        JSON字节数据
    """
    return to_translation(ctx).model_dump_json().encode("utf-8")


def transform_to_bytes(ctx):
    """
    将翻译上下文转换为自定义字节格式
    
    Args:
        ctx: 翻译上下文对象
    
    Returns:
        自定义字节数据
    """
    return to_translation(ctx).to_bytes()


async def apply_user_env_vars(user_env_vars_str: str, config: Config, admin_settings: dict, username: str = None):
    """
    解析用户提供的环境变量，并检查策略
    如果用户没有提供 API Keys，尝试从用户选择的预设中获取
    
    **重要**: 此函数会将用户 API Key 设置到 config.translator 中，
    翻译器会在 parse_args 时读取这些值，实现用户级 API Key 隔离。
    
    Args:
        user_env_vars_str: JSON 字符串，包含用户的 API Keys
        config: 配置对象
        admin_settings: 管理员设置
        username: 用户名（用于获取预设配置）
    
    Returns:
        dict: 用户提供的环境变量字典，如果没有则返回 None
    
    Raises:
        HTTPException: 如果策略不允许
    """
    import logging
    logger = logging.getLogger('manga_translator.server')
    
    logger.info(f"[EnvVars] apply_user_env_vars called for user '{username}'")
    
    policy = get_effective_api_key_policy(username, admin_settings)
    require_user_keys = policy.get('require_user_keys', False)
    allow_server_keys = policy.get('allow_server_keys', True)
    
    # 先尝试解析用户直接提供的 API Keys
    user_env_vars = None
    if user_env_vars_str and user_env_vars_str.strip() not in ('{}', ''):
        try:
            user_env_vars = json.loads(user_env_vars_str)
            user_env_vars = {k: v for k, v in user_env_vars.items() if v and k.isupper()}
            if user_env_vars:
                logger.info(f"[EnvVars] User '{username}' provided direct env vars: {list(user_env_vars.keys())}")
        except json.JSONDecodeError:
            pass
    
    # 用户没有直接提供完整配置时，尝试与预设合并
    logger.info(f"[EnvVars] Resolving preset env vars for user '{username}'")
    preset_state = await get_user_preset_env_state(username) if username else None
    preset_env_vars = (preset_state or {}).get('env_vars')

    merged_env_vars = {}
    if preset_env_vars:
        logger.info(
            f"[EnvVars] Using preset env vars for user '{username}'"
            f" from {preset_state.get('source')}: {list(preset_env_vars.keys())}"
        )
        merged_env_vars.update(preset_env_vars)
    if user_env_vars:
        logger.info(
            f"[EnvVars] Applying user env vars over preset/server for user '{username}':"
            f" {list(user_env_vars.keys())}"
        )
        merged_env_vars.update(user_env_vars)
    if merged_env_vars:
        config._allow_server_api_keys = allow_server_keys
        _apply_env_vars_to_config(
            merged_env_vars,
            config,
            logger,
            allow_server_api_keys=allow_server_keys,
        )
        apply_runtime_api_overrides(config, merged_env_vars)
        return merged_env_vars
    
    # 没有用户 API Keys 也没有预设
    logger.info(f"[EnvVars] No preset env vars for user '{username}', using server defaults")
    if require_user_keys:
        # 强制要求用户提供 API Keys
        raise HTTPException(403, detail="User API keys are required")
    
    if not allow_server_keys:
        # 不允许使用服务器的 API Keys，但用户也没提供
        raise HTTPException(403, detail="Server API keys are not allowed, please provide your own")
    
    # 允许使用服务器的 API Keys（已经在环境变量中）
    # 清除 config 中的用户级 API Key，确保使用服务器默认值
    config._allow_server_api_keys = True
    config.translator.user_api_key = None
    config.translator.user_api_base = None
    config.translator.user_api_model = None
    config.translator.aliyun_access_key_id = None
    config.translator.aliyun_access_key_secret = None
    config.translator.aliyun_api_base = None
    clear_runtime_api_overrides(config)
    return None


def _apply_env_vars_to_config(
    env_vars: dict,
    config: Config,
    logger,
    *,
    allow_server_api_keys: bool,
):
    """
    将环境变量映射到 config.translator 的用户级字段
    
    支持的环境变量:
    - OPENAI_API_KEY, GEMINI_API_KEY -> user_api_key
    - OPENAI_API_BASE, GEMINI_API_BASE -> user_api_base
    - OPENAI_MODEL, GEMINI_MODEL -> user_api_model
    - ALIYUN_ACCESS_KEY_ID -> aliyun_access_key_id
    - ALIYUN_ACCESS_KEY_SECRET -> aliyun_access_key_secret
    - ALIYUN_API_BASE -> aliyun_api_base
    
    注意：预设可能使用 OPENAI_* 变量来配置第三方 API（如 Gemini 通过 OpenAI 兼容接口）
    所以我们统一将这些变量映射到 user_api_* 字段，翻译器会根据自己的类型使用这些值
    
    Args:
        env_vars: 环境变量字典
        config: 配置对象
        logger: 日志记录器
    """
    logger.info(f"[EnvVars->Config] Processing env vars: {list(env_vars.keys())}")

    translator_value = getattr(config.translator, 'translator', '')
    translator_name = str(getattr(translator_value, 'value', translator_value) or '').strip().lower()
    provider_priority = {
        "openai": {
            "api_key": ["OPENAI_API_KEY"],
            "api_base": ["OPENAI_API_BASE"],
            "model": ["OPENAI_MODEL"],
        },
        "openai_hq": {
            "api_key": ["OPENAI_API_KEY"],
            "api_base": ["OPENAI_API_BASE"],
            "model": ["OPENAI_MODEL"],
        },
        "gemini": {
            "api_key": ["GEMINI_API_KEY"],
            "api_base": ["GEMINI_API_BASE"],
            "model": ["GEMINI_MODEL"],
        },
        "gemini_hq": {
            "api_key": ["GEMINI_API_KEY"],
            "api_base": ["GEMINI_API_BASE"],
            "model": ["GEMINI_MODEL"],
        },
        "aliyun": {
            "access_key_id": ["ALIYUN_ACCESS_KEY_ID"],
            "access_key_secret": ["ALIYUN_ACCESS_KEY_SECRET"],
            "api_base": ["ALIYUN_API_BASE"],
        },
    }
    priority = provider_priority.get(translator_name)

    config.translator.user_api_key = None
    config.translator.user_api_base = None
    config.translator.user_api_model = None
    config.translator.aliyun_access_key_id = None
    config.translator.aliyun_access_key_secret = None
    config.translator.aliyun_api_base = None

    if not priority:
        logger.info(
            f"[EnvVars->Config] Translator '{translator_name}' does not require"
            " provider API merge; keep existing translator config."
        )
        return

    def _pick_first(field_name: str, target_attr: str, mask_value: bool = False):
        for var in priority.get(field_name, []):
            value = env_vars.get(var)
            if not value:
                continue
            setattr(config.translator, target_attr, value)
            logger.info(f"[EnvVars->Config] Set {target_attr} from {var}")
            return

    if translator_name == "aliyun":
        _pick_first("access_key_id", "aliyun_access_key_id")
        _pick_first("access_key_secret", "aliyun_access_key_secret")
        _pick_first("api_base", "aliyun_api_base")
        if not (
            config.translator.aliyun_access_key_id
            and config.translator.aliyun_access_key_secret
        ):
            if allow_server_api_keys:
                logger.info(
                    "[EnvVars->Config] Aliyun credentials are incomplete; "
                    "falling back to server default credentials."
                )
                return
            raise HTTPException(
                403,
                detail=(
                    "Selected translator 'aliyun' requires both "
                    "ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET."
                ),
            )
        logger.info(
            "[EnvVars->Config] Final Aliyun config: access_key_id=SET, "
            f"access_key_secret={'SET' if config.translator.aliyun_access_key_secret else 'NOT SET'}, "
            f"api_base={config.translator.aliyun_api_base}"
        )
        return

    _pick_first("api_key", "user_api_key", mask_value=True)
    _pick_first("api_base", "user_api_base")
    _pick_first("model", "user_api_model")

    if not config.translator.user_api_key:
        if allow_server_api_keys:
            logger.info(
                f"[EnvVars->Config] No matching runtime API key found for translator"
                f" '{translator_name}', falling back to server default key."
            )
            return
        expected_key_names = ", ".join(priority.get("api_key", []))
        raise HTTPException(
            403,
            detail=(
                f"Selected translator '{translator_name}' requires one of: {expected_key_names}. "
                "The API key you filled does not match the current translator."
            ),
        )
    
    # 最终确认
    logger.info(f"[EnvVars->Config] Final config.translator: user_api_key={'SET' if config.translator.user_api_key else 'NOT SET'}, user_api_base={config.translator.user_api_base}, user_api_model={config.translator.user_api_model}")


async def get_user_preset_env_vars(username: str) -> dict:
    preset_state = await get_user_preset_env_state(username)
    return preset_state.get('env_vars') if preset_state else None


async def get_user_preset_env_state(username: str) -> dict:
    """
    获取用户当前生效预设中的 API Keys 和来源信息。
    
    Args:
        username: 用户名
    
    Returns:
        dict: {
            "preset_id": str,
            "preset_name": str,
            "source": "user_selected" | "group_default",
            "env_vars": {...},
        }
    """
    import logging
    logger = logging.getLogger('manga_translator.server')
    
    try:
        from manga_translator.server.core.config_management_service import (
            ConfigManagementService,
        )
        
        config_service = ConfigManagementService()
        
        # 获取用户配置
        user_config = config_service.get_user_config(username)
        logger.info(f"[Preset] User '{username}' config: {user_config}")

        preset_id = user_config.get('selected_preset_id') if user_config else None
        preset_source = 'user_selected'
        logger.info(f"[Preset] User '{username}' selected preset_id: {preset_id}")

        if not preset_id:
            account_service, _, _ = get_services()
            account = account_service.get_user(username)
            if account:
                group = get_group_management_service().get_group(account.group)
                preset_id = (group or {}).get('default_preset_id')
                if preset_id:
                    preset_source = 'group_default'
                    logger.info(
                        f"[Preset] User '{username}' has no selected preset,"
                        f" falling back to group default preset_id: {preset_id}"
                    )

        if not preset_id:
            logger.info(f"[Preset] No preset selected for user '{username}'")
            return None
        
        # 获取预设配置（解密）
        preset = config_service.get_preset(preset_id, decrypt=True)
        logger.info(f"[Preset] Preset '{preset_id}' loaded: {preset is not None}")
        if not preset:
            logger.warning(f"[Preset] Preset '{preset_id}' not found")
            return None
        
        # 从预设配置中提取 API Keys
        preset_config = preset.get('config', {})
        logger.info(f"[Preset] Preset config keys: {list(preset_config.keys())}")
        if not preset_config:
            logger.info(f"[Preset] Preset '{preset_id}' has no config")
            return None
        
        # 提取环境变量（大写的键）
        env_vars = {k: v for k, v in preset_config.items() if v and k.isupper()}
        
        # 仅记录解密到的键名，不记录任何值或前缀
        for key in env_vars:
            if 'API_KEY' in key:
                value = env_vars[key]
                if value:
                    logger.info(f"[Preset] {key} decrypted successfully")
                else:
                    logger.warning(f"[Preset] {key} is empty after decryption!")
        
        logger.info(f"[Preset] Extracted env vars for user '{username}': {list(env_vars.keys())}")
        if not env_vars:
            return None

        return {
            'preset_id': preset_id,
            'preset_name': preset.get('name'),
            'source': preset_source,
            'env_vars': env_vars,
        }
        
    except Exception as e:
        import logging
        logging.getLogger('manga_translator.server').warning(f"Failed to get user preset env vars: {e}")
        import traceback
        traceback.print_exc()
        return None
