"""
Config routes module.

This module contains configuration and metadata endpoints for the manga translator server.
"""

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header

from manga_translator.server.core.api_key_policy import get_effective_api_key_policy
from manga_translator.server.core.config_manager import (
    AVAILABLE_WORKFLOWS,
    FONTS_DIR,
    admin_settings,
    load_default_config_dict,
)
from manga_translator.server.core.middleware import get_services, require_auth
from manga_translator.server.core.models import Session
from manga_translator.server.core.response_utils import get_user_preset_env_state
from manga_translator.server_paths import USER_RESOURCES_RELATIVE_DIR
from manga_translator.runtime_paths import get_application_dir, get_config_path
from manga_translator.utils import BASE_PATH

router = APIRouter(tags=["config"])

SERVER_HIDDEN_OCR_OPTIONS = set()
SERVER_HIDDEN_RENDERER_OPTIONS = set()
SERVER_HIDDEN_COLORIZER_OPTIONS = set()
SERVER_HIDDEN_TRANSLATOR_OPTIONS = set()
SERVER_HIDDEN_CONFIG_KEYS = {
    # 服务器端内部参数
    "use_custom_api_params",
    # Qt UI 专属参数，Web 端全体禁用
    "cli.replace_translation",
    "render.enable_template_alignment",
    "render.paste_mask_dilation_pixels",
    # 服务器端控制，用户端不应暴露
    "cli.batch_size",
    "cli.batch_concurrent",
    "cli.use_gpu",
    # upscale
    "upscale.realcugan_model",
    # CLI 配置隐藏
    "cli.format",
    "cli.save_quality",
    "cli.overwrite",
    "cli.skip_no_text",
    "cli.save_text",
    "cli.export_from_local_json",
    "cli.load_text",
    "cli.translate_json_only",
    "cli.template",
    "cli.ignore_errors",
    "cli.verbose",
    "cli.psd_script_only",
    "cli.generate_and_export",
    "cli.colorize_only",
    "cli.upscale_only",
    "cli.inpaint_only",
    # 翻译器高级配置
    "translator.enable_post_translation_check",
    "translator.post_check_max_retry_attempts",
    "translator.post_check_repetition_threshold",
    "translator.post_check_target_lang_threshold",
    "translator.translator_chain",
    "translator.selective_translation",
    "translator.skip_lang",
    # 渲染
    "render.gimp_font",
    # PSD 相关（Qt UI / Photoshop 专属）
    "cli.export_editable_psd",
    # Qt UI 专属 - 输出到原图目录
    "cli.save_to_source_dir",
    # Qt UI 专属 - 导入固定YOLO框
    "detector.import_yolo_labels",
}

WEB_API_ENV_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_API_BASE",
    "GEMINI_MODEL",
    "SAKURA_API_BASE",
    "SAKURA_DICT_PATH",
    "OCR_OPENAI_API_KEY",
    "OCR_OPENAI_API_BASE",
    "OCR_OPENAI_MODEL",
    "OCR_GEMINI_API_KEY",
    "OCR_GEMINI_API_BASE",
    "OCR_GEMINI_MODEL",
    "COLOR_OPENAI_API_KEY",
    "COLOR_OPENAI_API_BASE",
    "COLOR_OPENAI_MODEL",
    "COLOR_GEMINI_API_KEY",
    "COLOR_GEMINI_API_BASE",
    "COLOR_GEMINI_MODEL",
    "RENDER_OPENAI_API_KEY",
    "RENDER_OPENAI_API_BASE",
    "RENDER_OPENAI_MODEL",
    "RENDER_GEMINI_API_KEY",
    "RENDER_GEMINI_API_BASE",
    "RENDER_GEMINI_MODEL",
}


def _load_server_web_env_vars() -> dict:
    from manga_translator.utils.dotenv_utils import read_dotenv_file

    env_path = os.path.join(get_application_dir(), '.env')
    env_vars = read_dotenv_file(env_path)
    return {
        key: value for key, value in env_vars.items()
        if key in WEB_API_ENV_KEYS and value
    }


def _resolve_username_from_token(x_session_token: Optional[str]) -> Optional[str]:
    if not x_session_token:
        return None

    try:
        _, session_service, _ = get_services()
        session = session_service.verify_token(x_session_token)
        return session.username if session else None
    except Exception:
        return None


def _get_server_ocr_options():
    from manga_translator.ocr import Ocr

    return [member.value for member in Ocr if member.value not in SERVER_HIDDEN_OCR_OPTIONS]


def _get_server_renderer_options():
    from manga_translator.config import Renderer

    return [member.value for member in Renderer if member.value not in SERVER_HIDDEN_RENDERER_OPTIONS]


def _get_server_colorizer_options():
    from manga_translator.colorization import Colorizer

    return [member.value for member in Colorizer if member.value not in SERVER_HIDDEN_COLORIZER_OPTIONS]


def _get_server_keep_lang_options():
    from manga_translator.translators.common import KEEP_LANGUAGES

    return ['none'] + list(KEEP_LANGUAGES.keys())


def _get_server_translator_options():
    from manga_translator.config import Translator

    return [member.value for member in Translator if member.value not in SERVER_HIDDEN_TRANSLATOR_OPTIONS]


def _filter_server_hidden_config(config_dict: dict) -> dict:
    filtered = {}
    for section, content in config_dict.items():
        if isinstance(content, dict):
            visible_content = {
                key: value
                for key, value in content.items()
                if f"{section}.{key}" not in SERVER_HIDDEN_CONFIG_KEYS
            }
            if visible_content:
                filtered[section] = visible_content
        elif section not in SERVER_HIDDEN_CONFIG_KEYS:
            filtered[section] = content
    return filtered


def _filter_options_by_permissions(options: dict, username: str, permission_service) -> dict:
    filtered = dict(options)
    feature_option_map = {
        'translator': 'translator',
        'ocr': 'ocr',
        'secondary_ocr': 'ocr',
        'colorizer': 'colorizer',
        'renderer': 'renderer',
    }

    for key, feature_type in feature_option_map.items():
        values = filtered.get(key)
        if isinstance(values, list):
            filtered[key] = permission_service.filter_allowed_options(username, feature_type, values)

    return filtered


# ============================================================================
# Configuration Endpoints
# ============================================================================

@router.get("/config/defaults")
async def get_config_defaults():
    """Get server default configuration (template for permission editor)"""
    config = load_default_config_dict()
    config = _filter_server_hidden_config(config)
    
    # 过滤掉Qt UI专属配置（app部分）
    config = {k: v for k, v in config.items() if k not in WEB_EXCLUDED_SECTIONS}
    
    # 添加配额默认值
    config['quota'] = {
        'daily_image_limit': 100,
        'daily_char_limit': 100000,
        'max_concurrent_tasks': 3,
        'max_batch_size': 20,
        'max_image_size_mb': 10,
        'max_images_per_batch': 50
    }
    
    # 添加功能权限默认值
    config['permissions'] = {
        'can_upload_fonts': True,
        'can_delete_fonts': True,
        'can_upload_prompts': True,
        'can_delete_prompts': True,
        'can_use_batch': True,
        'can_use_api': True,
        'can_export_text': True,
        'can_view_history': True,
        'can_view_logs': False,
        'show_env_editor': False,
        'allow_server_keys': True,
        'require_user_keys': False,
        'save_user_keys_to_server': False,
    }
    
    return config


# Web端不需要的配置部分（Qt UI专属）
WEB_EXCLUDED_SECTIONS = {'app'}


@router.get("/config")
async def get_config(
    mode: str = 'user',
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """
    Get default configuration structure
    
    Args:
        mode: 'user' (legacy admin filtering), 'authenticated' (user permission filtering), or 'admin' (full config)
        x_session_token: Session token for authenticated mode
    
    Returns:
        Filtered configuration based on mode and user permissions
    """
    config_dict = load_default_config_dict()
    config_dict = _filter_server_hidden_config(config_dict)
    
    # 过滤掉Qt UI专属配置（app部分）
    config_dict = {k: v for k, v in config_dict.items() if k not in WEB_EXCLUDED_SECTIONS}
    
    # If authenticated mode, filter based on user permissions and group config
    if mode == 'authenticated':
        if not x_session_token:
            return {"error": {"code": "NO_TOKEN", "message": "Session token required for authenticated mode"}}
        
        account_service, session_service, permission_service = get_services()
        
        # Verify token
        session = session_service.verify_token(x_session_token)
        if not session:
            return {"error": {"code": "INVALID_TOKEN", "message": "Invalid or expired session token"}}
        
        # Get user account to get group info
        account = account_service.get_user(session.username)
        if not account:
            return {}
        
        # Get user permissions
        permissions = account.permissions
        
        # Get group config for parameter visibility
        group_hidden_params = set()
        group_default_values = {}
        try:
            from manga_translator.server.core.group_management_service import (
                get_group_management_service,
            )
            group_service = get_group_management_service()
            group = group_service.get_group(account.group)
            
            if group and group.get('parameter_config'):
                param_config = group['parameter_config']
                
                # 检查是否有嵌套的 parameter_config（禁用配置）
                nested_param_config = param_config.get('parameter_config', {})
                if nested_param_config:
                        # 处理嵌套的禁用配置 {"translator.translator": {"disabled": true}}
                        for full_key, key_config in nested_param_config.items():
                            if isinstance(key_config, dict):
                                if key_config.get('visible') is False or key_config.get('disabled') is True:
                                    group_hidden_params.add(full_key)
                                if 'default_value' in key_config:
                                    group_default_values[full_key] = key_config['default_value']
                
                # 遍历用户组的参数配置，找出默认值
                for section, section_config in param_config.items():
                    if section == 'parameter_config':
                        continue  # 跳过嵌套的禁用配置
                    if isinstance(section_config, dict):
                        for key, key_config in section_config.items():
                            full_key = f"{section}.{key}"
                            # 旧格式: {visible: false, disabled: true} 或新格式: 直接是值
                            if isinstance(key_config, dict):
                                if key_config.get('visible') is False or key_config.get('disabled') is True:
                                    group_hidden_params.add(full_key)
                                if 'default_value' in key_config:
                                    group_default_values[full_key] = key_config['default_value']
                            else:
                                # 新格式：直接是默认值
                                group_default_values[full_key] = key_config
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get group config: {e}")
        
        # 用户级别的白名单可以解锁用户组禁用的参数
        # 空数组表示继承用户组，不是禁止所有
        user_allowed_params = set(permissions.allowed_parameters) if permissions.allowed_parameters else set()
        user_denied_params = set(permissions.denied_parameters) if hasattr(permissions, 'denied_parameters') and permissions.denied_parameters else set()
        
        # 如果用户没有设置任何参数权限（空数组），则默认允许所有（继承用户组）
        user_has_param_restrictions = len(user_allowed_params) > 0 and "*" not in user_allowed_params
        
        filtered_config = {}
        
        # Filter configuration sections
        for section, content in config_dict.items():
            if isinstance(content, dict):
                filtered_content = {}
                for key, value in content.items():
                    full_key = f"{section}.{key}"
                    
                    # 1. 检查是否被用户黑名单禁用（最高优先级）
                    if full_key in user_denied_params:
                        continue
                    
                    # 2. 检查是否被用户组禁用
                    if full_key in group_hidden_params:
                        # 检查用户白名单是否解锁（用户白名单可以覆盖用户组禁用）
                        if full_key not in user_allowed_params and "*" not in user_allowed_params:
                            continue
                    
                    # 3. 如果用户有明确的参数限制（非空且非*），检查是否在允许列表中
                    # 注意：空数组表示继承用户组，不是禁止所有
                    if user_has_param_restrictions:
                        if full_key not in user_allowed_params:
                            continue
                    
                    # 使用用户组默认值（如果有）
                    if full_key in group_default_values:
                        value = group_default_values[full_key]
                    
                    filtered_content[key] = value
                
                if filtered_content:
                    filtered_config[section] = filtered_content
            else:
                # Top-level parameters
                if section not in user_denied_params and section not in group_hidden_params:
                    filtered_config[section] = content
        
        # Add user permissions info to response
        # 获取有效的每日配额（优先从用户组获取）
        effective_daily_quota = permission_service.get_effective_daily_quota(session.username)
        
        # 获取允许的工作流列表
        allowed_workflows = list(AVAILABLE_WORKFLOWS)  # 默认所有
        try:
            group_allowed_wf = set(group.get('allowed_workflows', [])) if group else set()
            group_denied_wf = set(group.get('denied_workflows', [])) if group else set()
            
            # 如果用户组有白名单限制
            if group_allowed_wf and "*" not in group_allowed_wf:
                allowed_workflows = [wf for wf in AVAILABLE_WORKFLOWS if wf in group_allowed_wf]
            
            # 移除用户组黑名单
            allowed_workflows = [wf for wf in allowed_workflows if wf not in group_denied_wf]
        except Exception:
            pass
        
        filtered_config['user_permissions'] = {
            'username': session.username,
            'role': session.role,
            'group': account.group,
            'allowed_translators': permissions.allowed_translators,
            'allowed_ocr': getattr(permissions, 'allowed_ocr', []),
            'allowed_colorizers': getattr(permissions, 'allowed_colorizers', []),
            'allowed_renderers': getattr(permissions, 'allowed_renderers', []),
            'allowed_parameters': permissions.allowed_parameters,
            'allowed_workflows': allowed_workflows,
            'max_concurrent_tasks': permissions.max_concurrent_tasks,
            'daily_quota': effective_daily_quota,
            'can_upload_files': permissions.can_upload_files,
            'can_delete_files': permissions.can_delete_files
        }
        
        return filtered_config
    
    # If user mode, filter based on admin settings (legacy behavior)
    if mode == 'user':
        filtered_config = {}
        visible_sections = admin_settings.get('visible_sections', [])
        hidden_keys = admin_settings.get('hidden_keys', [])
        default_values = admin_settings.get('default_values', {})
        
        for section, content in config_dict.items():
            if isinstance(content, dict):
                # This is a config section (like translator, detector, cli, etc.)
                # Skip sections not in visible list
                if visible_sections and section not in visible_sections:
                    continue
                
                filtered_content = {}
                for key, value in content.items():
                    full_key = f"{section}.{key}"
                    if full_key not in hidden_keys:
                        # Use admin-set default values (if any)
                        filtered_content[key] = default_values.get(full_key, value)
                if filtered_content:
                    filtered_config[section] = filtered_content
            else:
                # This is top-level parameter (like filter_text, kernel_size, mask_dilation_offset, etc.)
                # Top-level parameters not restricted by visible_sections, only check if in hidden list
                if section not in hidden_keys:
                    filtered_config[section] = content
        
        return filtered_config
    
    return config_dict


@router.get("/config/options")
async def get_config_options(
    session_token: str = Header(alias="X-Session-Token", default=None)
):
    """Get options for parameters that should be dropdowns
    
    If session token is provided, also includes user's uploaded fonts.
    """
    from manga_translator.config import Alignment, Direction, InpaintPrecision
    from manga_translator.detection import Detector
    from manga_translator.image_formats import (
        OUTPUT_IMAGE_FORMATS,
        SUPPORTED_IMAGE_EXTENSIONS,
    )
    from manga_translator.inpainting import Inpainter
    from manga_translator.translators import VALID_LANGUAGES
    from manga_translator.upscaling import Upscaler
    
    # Get server font list (shared fonts)
    fonts = []
    if os.path.exists(FONTS_DIR):
        fonts = sorted([f for f in os.listdir(FONTS_DIR) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
    
    from manga_translator.rendering.text_render import load_font_file
    server_font_families = []
    for filename in fonts:
        try:
            server_font_families.append(load_font_file(os.path.join(FONTS_DIR, filename)))
        except Exception:
            pass
    
    # Get user's uploaded fonts if session is provided
    user_font_families = []
    user_prompt_paths = []
    if session_token:
        try:
            from manga_translator.server.routes.resources import get_resource_service
            
            _, session_service, _ = get_services()
            session = session_service.verify_token(session_token)
            if session:
                resource_service = get_resource_service()
                user_font_resources = resource_service.get_user_fonts(session.username)
                for font_resource in user_font_resources:
                    try:
                        user_font_families.append(
                            load_font_file(str(resource_service.base_path / font_resource.file_path))
                        )
                    except Exception:
                        if font_resource.font_family:
                            user_font_families.append(font_resource.font_family)
                # 用户提示词使用相对路径: manga_translator/server/data/user_resources/prompts/{username}/{filename}
                user_prompt_resources = resource_service.get_user_prompts(session.username)
                user_prompt_paths = [
                    f'{USER_RESOURCES_RELATIVE_DIR}/prompts/{session.username}/{p.filename}'
                    for p in user_prompt_resources
                ]
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get user resources: {e}")
    
    all_font_families = sorted(set(server_font_families + user_font_families), key=str.casefold)
    
    # Get server prompt list
    prompts = []
    dict_dir = os.path.join(BASE_PATH, 'dict')
    if os.path.exists(dict_dir):
        prompts = sorted([f for f in os.listdir(dict_dir) 
                         if f.lower().endswith(('.json', '.yaml', '.yml')) and os.path.splitext(f)[0] not in ('system_prompt_hq', 'system_prompt_hq_format', 'system_prompt_line_break', 'glossary_extraction_prompt')])
    
    # 服务器提示词使用相对路径: dict/{filename}
    server_prompt_paths = [f'dict/{p}' for p in prompts]
    all_prompt_paths = server_prompt_paths + user_prompt_paths
    
    options = {
        'renderer': _get_server_renderer_options(),
        'alignment': [member.value for member in Alignment],
        'direction': [member.value for member in Direction],
        'upscaler': [member.value for member in Upscaler],
        'detector': [member.value for member in Detector],
        'colorizer': _get_server_colorizer_options(),
        'inpainter': [member.value for member in Inpainter],
        'inpainting_precision': [member.value for member in InpaintPrecision],
        'ocr': _get_server_ocr_options(),
        'secondary_ocr': _get_server_ocr_options(),
        'translator': _get_server_translator_options(),
        'target_lang': list(VALID_LANGUAGES),
        'keep_lang': _get_server_keep_lang_options(),
        'upscale_ratio': ['不使用', '2', '3', '4'],
        'realcugan_model': [
            '2x-conservative', '2x-conservative-pro', '2x-no-denoise',
            '2x-denoise1x', '2x-denoise2x', '2x-denoise3x', '2x-denoise3x-pro',
            '3x-conservative', '3x-conservative-pro', '3x-no-denoise', '3x-no-denoise-pro',
            '3x-denoise3x', '3x-denoise3x-pro',
            '4x-conservative', '4x-no-denoise', '4x-denoise3x'
        ],
        'font_family': all_font_families,
        'high_quality_prompt_path': all_prompt_paths,
        'layout_mode': ['smart_scaling', 'strict', 'balloon_fill'],
        'ocr_vl_language_hint': [
            'auto',
            'multilingual',
            'Arabic',
            'Simplified Chinese',
            'Traditional Chinese',
            'English',
            'Japanese',
            'Korean',
            'Spanish',
            'French',
            'German',
            'Russian',
            'Portuguese',
            'Italian',
            'Thai',
            'Vietnamese',
            'Indonesian',
            'Turkish',
            'Polish',
            'Ukrainian'
        ],
        'format': ['不指定', *OUTPUT_IMAGE_FORMATS],
        'image_extensions': list(SUPPORTED_IMAGE_EXTENSIONS),
    }

    if session_token:
        try:
            account_service, session_service, permission_service = get_services()
            session = session_service.verify_token(session_token)
            if session and account_service.get_user(session.username):
                options = _filter_options_by_permissions(options, session.username, permission_service)
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to filter config options by permissions: {e}")

    return options


# ============================================================================
# Metadata Endpoints
# ============================================================================

@router.get("/fonts")
async def get_fonts():
    """List available fonts"""
    fonts = []
    if os.path.exists(FONTS_DIR):
        for f in os.listdir(FONTS_DIR):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                fonts.append(f)
    return sorted(fonts)


@router.get("/translators")
async def get_translators(
    mode: str = 'user',
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """
    Get all available translators
    
    Args:
        mode: 'user' (legacy admin filtering), 'authenticated' (user permission filtering), or 'admin' (all translators)
        x_session_token: Session token for authenticated mode
    
    Returns:
        List of translators based on mode and user permissions
    """
    from manga_translator.translators import TRANSLATORS
    all_translators = [str(t) for t in TRANSLATORS if str(t) not in SERVER_HIDDEN_TRANSLATOR_OPTIONS]
    
    # If authenticated mode, filter based on user permissions and group config
    if mode == 'authenticated':
        if not x_session_token:
            return {"error": {"code": "NO_TOKEN", "message": "Session token required for authenticated mode"}}
        
        account_service, session_service, permission_service = get_services()
        
        # Verify token
        session = session_service.verify_token(x_session_token)
        if not session:
            return {"error": {"code": "INVALID_TOKEN", "message": "Invalid or expired session token"}}
        
        # Get user account
        account = account_service.get_user(session.username)
        if not account:
            return []
        
        return permission_service.filter_allowed_options(
            session.username,
            'translator',
            sorted(all_translators),
        )
    
    # If user mode and admin set allowed translator list (legacy behavior)
    if mode == 'user' and admin_settings.get('allowed_translators'):
        allowed = admin_settings['allowed_translators']
        return [t for t in all_translators if t in allowed]
    
    return all_translators


@router.get("/languages")
async def get_languages(
    mode: str = 'user',
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """
    Get all valid languages
    
    Args:
        mode: 'user' (legacy admin filtering), 'authenticated' (user permission filtering), or 'admin' (all languages)
        x_session_token: Session token for authenticated mode
    
    Returns:
        List of languages based on mode and user permissions
    
    Note: Currently languages are not restricted by user permissions in the permission model.
          This endpoint returns all languages for authenticated users, but can be extended
          to support language-level permissions in the future.
    """
    from manga_translator.translators import VALID_LANGUAGES
    all_languages = list(VALID_LANGUAGES)
    
    # If authenticated mode, return all languages (no language-level permissions yet)
    if mode == 'authenticated':
        if not x_session_token:
            return {"error": {"code": "NO_TOKEN", "message": "Session token required for authenticated mode"}}
        
        _, session_service, _ = get_services()
        
        # Verify token
        session = session_service.verify_token(x_session_token)
        if not session:
            return {"error": {"code": "INVALID_TOKEN", "message": "Invalid or expired session token"}}
        
        # Future: Add language permission filtering here if needed
        # For now, all authenticated users can see all languages
        return all_languages
    
    # If user mode and admin set allowed language list (legacy behavior)
    if mode == 'user' and admin_settings.get('allowed_languages'):
        allowed = admin_settings['allowed_languages']
        return [lang for lang in all_languages if lang in allowed]
    
    return all_languages


@router.get("/workflows")
async def get_workflows(
    mode: str = 'user',
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """
    Get all available workflows
    
    Args:
        mode: 'user' (legacy admin filtering), 'authenticated' (user permission filtering), or 'admin' (all workflows)
        x_session_token: Session token for authenticated mode
    
    Returns:
        List of workflows based on mode and user permissions
    """
    # If authenticated mode, filter based on user permissions and group config
    if mode == 'authenticated':
        if not x_session_token:
            return {"error": {"code": "NO_TOKEN", "message": "Session token required for authenticated mode"}}
        
        account_service, session_service, _ = get_services()
        
        # Verify token
        session = session_service.verify_token(x_session_token)
        if not session:
            return {"error": {"code": "INVALID_TOKEN", "message": "Invalid or expired session token"}}
        
        # Get user account
        account = account_service.get_user(session.username)
        if not account:
            return []
        
        # Get group config for workflow permissions
        group_allowed = set()
        group_denied = set()
        try:
            from manga_translator.server.core.group_management_service import (
                get_group_management_service,
            )
            group_service = get_group_management_service()
            group = group_service.get_group(account.group)
            
            if group:
                group_allowed = set(group.get('allowed_workflows', []))
                group_denied = set(group.get('denied_workflows', []))
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get group config: {e}")
        
        # Get user permissions (if workflow permissions exist)
        permissions = account.permissions
        user_allowed = set()
        user_denied = set()
        if hasattr(permissions, 'allowed_workflows') and permissions.allowed_workflows:
            user_allowed = set(permissions.allowed_workflows)
        if hasattr(permissions, 'denied_workflows') and permissions.denied_workflows:
            user_denied = set(permissions.denied_workflows)
        
        # 权限逻辑: 用户黑名单 + 用户组黑名单 - 用户白名单
        # 1. 如果用户组允许所有（*）或未设置，则从所有工作流开始
        if "*" in group_allowed or not group_allowed:
            result = set(AVAILABLE_WORKFLOWS)
        else:
            result = group_allowed.intersection(set(AVAILABLE_WORKFLOWS))
        
        # 2. 移除用户组黑名单
        result -= group_denied
        
        # 3. 移除用户黑名单
        result -= user_denied
        
        # 4. 用户白名单可以解锁
        if user_allowed and "*" not in user_allowed:
            for wf in user_allowed:
                if wf in AVAILABLE_WORKFLOWS:
                    result.add(wf)
        
        # 保持原始顺序
        return [wf for wf in AVAILABLE_WORKFLOWS if wf in result]
    
    # If user mode and admin set allowed workflow list (legacy behavior)
    if mode == 'user' and admin_settings.get('allowed_workflows'):
        allowed = admin_settings['allowed_workflows']
        return [wf for wf in AVAILABLE_WORKFLOWS if wf in allowed]
    
    return AVAILABLE_WORKFLOWS


@router.get("/translator-config/{translator}")
async def get_translator_config(translator: str):
    """Get translator configuration (required API keys) - public info only"""
    config_path = get_config_path('config', 'translators.json')
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                configs = json.load(f)
                config = configs.get(translator, {})
                # Only return public info, not validation rules or sensitive info
                return {
                    'name': config.get('name'),
                    'display_name': config.get('display_name'),
                    'required_env_vars': config.get('required_env_vars', []),
                    'optional_env_vars': config.get('optional_env_vars', [])
                }
        except Exception:
            return {}
    return {}


# ============================================================================
# User Settings Endpoints
# ============================================================================

@router.get("/user/settings")
async def get_user_settings(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """Get user-side visibility settings (includes group quota settings)"""
    username = _resolve_username_from_token(x_session_token)

    # 默认值从 admin_settings 获取
    permissions = admin_settings.get('permissions', {})
    api_key_policy = get_effective_api_key_policy(username, admin_settings)
    upload_limits = admin_settings.get('upload_limits', {})
    
    # 默认设置
    result = {
        'show_env_editor': api_key_policy.get('show_env_editor', False),
        'can_upload_fonts': permissions.get('can_upload_fonts', True),
        'can_upload_prompts': permissions.get('can_upload_prompts', True),
        'allow_server_keys': api_key_policy.get('allow_server_keys', True),
        'max_image_size_mb': upload_limits.get('max_image_size_mb', 0),
        'max_images_per_batch': upload_limits.get('max_images_per_batch', 0)
    }
    
    # 如果有用户登录，从用户组配置获取配额
    if username:
        try:
            account_service, session_service, _ = get_services()
            session = session_service.verify_token(x_session_token)
            if session:
                account = account_service.get_user(session.username)
                if account:
                    # 获取用户组配置
                    from manga_translator.server.core.group_management_service import (
                        get_group_management_service,
                    )
                    group_service = get_group_management_service()
                    group = group_service.get_group(account.group)
                    
                    if group:
                        param_config = group.get('parameter_config', {})
                        quota = param_config.get('quota', {})
                        group_permissions = param_config.get('permissions', {})
                        
                        # 使用用户组的配额设置（如果有）
                        if 'max_image_size_mb' in quota:
                            result['max_image_size_mb'] = quota['max_image_size_mb']
                        if 'max_images_per_batch' in quota:
                            result['max_images_per_batch'] = quota['max_images_per_batch']
                        if 'can_upload_fonts' in group_permissions:
                            result['can_upload_fonts'] = group_permissions['can_upload_fonts']
                        if 'can_upload_prompts' in group_permissions:
                            result['can_upload_prompts'] = group_permissions['can_upload_prompts']
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get group settings: {e}")
    
    return result


@router.get("/user/access")
async def get_user_access():
    """Check if user access requires password"""
    user_access = admin_settings.get('user_access', {})
    return {
        "require_password": user_access.get('require_password', False)
    }


# ============================================================================
# API Key Policy Endpoints
# ============================================================================

@router.get("/api-key-policy")
async def get_api_key_policy(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """Get API key policy for users"""
    username = _resolve_username_from_token(x_session_token)
    policy = get_effective_api_key_policy(username, admin_settings)
    policy['merge_order'] = ['user_input', 'selected_preset', 'server_default']
    policy['fallback_rule'] = 'feature_specific_then_provider_default'
    return policy


@router.get("/env")
async def get_user_env_vars(session: Session = Depends(require_auth)):
    """Do not expose server-side API key values to regular users."""
    policy = get_effective_api_key_policy(session.username, admin_settings)
    if not policy.get('show_env_editor', False):
        return {}

    return {}


@router.get("/env/effective")
async def get_effective_user_env_vars(session: Session = Depends(require_auth)):
    """Get API key source metadata for the current user editor without secret values."""
    policy = get_effective_api_key_policy(session.username, admin_settings)
    if not policy.get('show_env_editor', False):
        return {
            'policy': policy,
            'selected_preset_id': None,
            'selected_preset_name': None,
            'selected_preset_source': None,
            'effective_keys': [],
            'server_env_vars': {},
            'preset_env_vars': {},
            'merged_env_vars': {},
            'sources': {},
        }

    server_env_vars = (
        _load_server_web_env_vars()
        if policy.get('allow_server_keys', True)
        else {}
    )
    preset_state = await get_user_preset_env_state(session.username)
    preset_env_vars = (preset_state or {}).get('env_vars', {})

    merged_env_vars = dict(server_env_vars)
    merged_env_vars.update(preset_env_vars)

    sources = {key: 'server' for key in server_env_vars.keys()}
    sources.update({key: 'preset' for key in preset_env_vars.keys()})

    return {
        'policy': policy,
        'selected_preset_id': (preset_state or {}).get('preset_id'),
        'selected_preset_name': (preset_state or {}).get('preset_name'),
        'selected_preset_source': (preset_state or {}).get('source'),
        'effective_keys': list(sources.keys()),
        'server_env_vars': {},
        'preset_env_vars': {},
        'merged_env_vars': {},
        'sources': sources,
    }


@router.post("/env")
async def save_user_env_vars(env_vars: dict, session: Session = Depends(require_auth)):
    """Save user's environment variables"""
    from fastapi import HTTPException

    from manga_translator.server.core.env_service import EnvService
    from manga_translator.utils.dotenv_utils import APP_DOTENV_PATH_ENV, load_app_dotenv
    
    policy = get_effective_api_key_policy(session.username, admin_settings)
    if not policy.get('show_env_editor', False):
        raise HTTPException(403, detail="Not allowed to edit environment variables")

    save_to_server = policy.get('save_user_keys_to_server', False)

    filtered_env_vars = {
        key: '' if value is None else str(value)
        for key, value in env_vars.items()
        if key in WEB_API_ENV_KEYS
    }
    
    if not save_to_server:
        # Don't save to server, just return success (actually temporary use)
        return {"success": True, "saved_to_server": False}
    
    # Save to server .env file using EnvService for consistent formatting
    env_path = os.path.join(get_application_dir(), '.env')
    os.environ[APP_DOTENV_PATH_ENV] = env_path
    try:
        env_service = EnvService(env_path)
        
        for key, value in filtered_env_vars.items():
            env_service.update_env_var(key, value)
        
        # 重新加载 .env 文件确保所有变量都是最新的
        load_app_dotenv(env_path, override=True)
        
        return {"success": True, "saved_to_server": True}
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to save env vars: {str(e)}")


# ============================================================================
# i18n Endpoints
# ============================================================================

@router.get("/i18n/languages")
async def get_i18n_languages():
    """Get available languages"""
    from manga_translator.server.core.config_manager import get_available_locales
    return get_available_locales()


@router.get("/i18n/{locale}")
async def get_translation(locale: str):
    """Get translations for a specific locale"""
    from manga_translator.server.core.config_manager import load_translation
    return load_translation(locale)


# ============================================================================
# Announcement Endpoint
# ============================================================================

@router.get("/announcement")
async def get_announcement():
    """Get announcement (user side)"""
    announcement = admin_settings.get('announcement', {})
    if announcement.get('enabled', False):
        return {
            "enabled": True,
            "message": announcement.get('message', ''),
            "type": announcement.get('type', 'info')
        }
    return {"enabled": False}
