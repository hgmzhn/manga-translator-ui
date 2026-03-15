"""
Config routes module.

This module contains configuration and metadata endpoints for the manga translator server.
"""

import os
import json
from typing import Optional
from fastapi import APIRouter, Header

from manga_translator.server.core.config_manager import (
    load_default_config_dict, admin_settings, AVAILABLE_WORKFLOWS, FONTS_DIR
)
from manga_translator.utils import BASE_PATH
from manga_translator.server.core.middleware import get_services

router = APIRouter(tags=["config"])

SERVER_HIDDEN_OCR_OPTIONS = {"openai_ocr", "gemini_ocr"}
SERVER_HIDDEN_RENDERER_OPTIONS = {"openai_renderer", "gemini_renderer"}
SERVER_HIDDEN_COLORIZER_OPTIONS = {"openai_colorizer", "gemini_colorizer"}
SERVER_HIDDEN_CONFIG_KEYS = {
    "use_custom_api_params",
    "cli.full_image_workers",
    "ocr.ai_ocr_concurrency",
    "ocr.local_ocr_concurrency",
    "inpainter.local_inpainting_concurrency",
    "render.ai_renderer_concurrency",
    "upscale.local_upscaling_concurrency",
    "colorizer.ai_colorizer_history_pages",
}


def _get_server_ocr_options():
    from manga_translator.ocr import Ocr

    return [member.value for member in Ocr if member.value not in SERVER_HIDDEN_OCR_OPTIONS]


def _get_server_renderer_options():
    from manga_translator.config import Renderer

    return [member.value for member in Renderer if member.value not in SERVER_HIDDEN_RENDERER_OPTIONS]


def _get_server_colorizer_options():
    from manga_translator.colorization import Colorizer

    return [member.value for member in Colorizer if member.value not in SERVER_HIDDEN_COLORIZER_OPTIONS]


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
        'can_view_logs': False
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
            from manga_translator.server.core.group_management_service import get_group_management_service
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
                            if key_config.get('visible') == False or key_config.get('disabled') == True:
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
                                if key_config.get('visible') == False or key_config.get('disabled') == True:
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


@router.get("/config/structure")
async def get_config_structure(token: str = Header(alias="X-Admin-Token", default=None)):
    """Get full configuration structure with metadata (admin only)"""
    from manga_translator.config import Renderer, Alignment, Direction, InpaintPrecision
    from manga_translator.upscaling import Upscaler
    from manga_translator.translators import Translator
    from manga_translator.detection import Detector
    from manga_translator.colorization import Colorizer
    from manga_translator.inpainting import Inpainter
    from manga_translator.ocr import Ocr
    
    config_dict = load_default_config_dict()
    config_dict = _filter_server_hidden_config(config_dict)
    
    # Get font list
    fonts = []
    if os.path.exists(FONTS_DIR):
        fonts = sorted([f for f in os.listdir(FONTS_DIR) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
    
    # Get prompt list
    prompts = []
    prompts_dir = os.path.join(BASE_PATH, 'dict')
    if os.path.exists(prompts_dir):
        prompts = sorted([f for f in os.listdir(prompts_dir) 
                         if f.lower().endswith(('.json', '.yaml', '.yml')) and os.path.splitext(f)[0] not in ('system_prompt_hq', 'system_prompt_hq_format', 'system_prompt_line_break', 'glossary_extraction_prompt')])
    
    # Define parameter options (enum types)
    param_options = {
        'renderer': _get_server_renderer_options(),
        'alignment': [member.value for member in Alignment],
        'direction': [member.value for member in Direction],
        'upscaler': [member.value for member in Upscaler],
        'translator': [member.value for member in Translator],
        'detector': [member.value for member in Detector],
        'colorizer': _get_server_colorizer_options(),
        'inpainter': [member.value for member in Inpainter],
        'inpainting_precision': [member.value for member in InpaintPrecision],
        'ocr': _get_server_ocr_options(),
        'secondary_ocr': _get_server_ocr_options(),
        'upscale_ratio': ['不使用', '2', '3', '4'],
        'realcugan_model': [
            '2x-conservative', '2x-conservative-pro', '2x-no-denoise',
            '2x-denoise1x', '2x-denoise2x', '2x-denoise3x', '2x-denoise3x-pro',
            '3x-conservative', '3x-conservative-pro', '3x-no-denoise', '3x-no-denoise-pro',
            '3x-denoise3x', '3x-denoise3x-pro',
            '4x-conservative', '4x-no-denoise', '4x-denoise3x'
        ],
        'font_path': [f'fonts/{f}' for f in fonts],
        'high_quality_prompt_path': [f'dict/{p}' for p in prompts],
        'layout_mode': ['default', 'smart_scaling', 'strict', 'fixed_font', 'disable_all', 'balloon_fill']
    }
    
    # Build config structure, including metadata for each parameter
    structure = {}
    for section, content in config_dict.items():
        if isinstance(content, dict):
            structure[section] = {}
            for key, value in content.items():
                full_key = f"{section}.{key}"
                structure[section][key] = {
                    'value': value,
                    'type': type(value).__name__,
                    'full_key': full_key,
                    'hidden': full_key in admin_settings.get('hidden_keys', []),
                    'readonly': full_key in admin_settings.get('readonly_keys', []),
                    'default_override': admin_settings.get('default_values', {}).get(full_key),
                    'options': param_options.get(key)  # Add options list
                }
        else:
            structure[section] = {
                'value': content,
                'type': type(content).__name__
            }
    
    return structure


@router.get("/config/options")
async def get_config_options(
    session_token: str = Header(alias="X-Session-Token", default=None)
):
    """Get options for parameters that should be dropdowns
    
    If session token is provided, also includes user's uploaded fonts.
    """
    from manga_translator.config import Renderer, Alignment, Direction, InpaintPrecision
    from manga_translator.upscaling import Upscaler
    from manga_translator.translators import Translator, VALID_LANGUAGES
    from manga_translator.detection import Detector
    from manga_translator.colorization import Colorizer
    from manga_translator.inpainting import Inpainter
    from manga_translator.ocr import Ocr
    
    # Get server font list (shared fonts)
    fonts = []
    if os.path.exists(FONTS_DIR):
        fonts = sorted([f for f in os.listdir(FONTS_DIR) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
    
    # 服务器字体使用相对路径: fonts/{filename}
    server_font_paths = [f'fonts/{f}' for f in fonts]
    
    # Get user's uploaded fonts if session is provided
    user_font_paths = []
    user_prompt_paths = []
    if session_token:
        try:
            from manga_translator.server.routes.resources import get_resource_service
            
            _, session_service, _ = get_services()
            session = session_service.verify_token(session_token)
            if session:
                resource_service = get_resource_service()
                # 用户字体使用相对路径: manga_translator/server/user_resources/fonts/{username}/{filename}
                user_font_resources = resource_service.get_user_fonts(session.username)
                user_font_paths = [
                    f'manga_translator/server/user_resources/fonts/{session.username}/{f.filename}' 
                    for f in user_font_resources
                ]
                # 用户提示词使用相对路径: manga_translator/server/user_resources/prompts/{username}/{filename}
                user_prompt_resources = resource_service.get_user_prompts(session.username)
                user_prompt_paths = [
                    f'manga_translator/server/user_resources/prompts/{session.username}/{p.filename}' 
                    for p in user_prompt_resources
                ]
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get user resources: {e}")
    
    # Combine server fonts and user fonts
    all_font_paths = server_font_paths + user_font_paths
    
    # Get server prompt list
    prompts = []
    dict_dir = os.path.join(BASE_PATH, 'dict')
    if os.path.exists(dict_dir):
        prompts = sorted([f for f in os.listdir(dict_dir) 
                         if f.lower().endswith(('.json', '.yaml', '.yml')) and os.path.splitext(f)[0] not in ('system_prompt_hq', 'system_prompt_hq_format', 'system_prompt_line_break', 'glossary_extraction_prompt')])
    
    # 服务器提示词使用相对路径: dict/{filename}
    server_prompt_paths = [f'dict/{p}' for p in prompts]
    all_prompt_paths = server_prompt_paths + user_prompt_paths
    
    return {
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
        'translator': [member.value for member in Translator],
        'target_lang': list(VALID_LANGUAGES),
        'upscale_ratio': ['不使用', '2', '3', '4'],
        'realcugan_model': [
            '2x-conservative', '2x-conservative-pro', '2x-no-denoise',
            '2x-denoise1x', '2x-denoise2x', '2x-denoise3x', '2x-denoise3x-pro',
            '3x-conservative', '3x-conservative-pro', '3x-no-denoise', '3x-no-denoise-pro',
            '3x-denoise3x', '3x-denoise3x-pro',
            '4x-conservative', '4x-no-denoise', '4x-denoise3x'
        ],
        'font_path': all_font_paths,
        'high_quality_prompt_path': all_prompt_paths,
        'layout_mode': ['default', 'smart_scaling', 'strict', 'fixed_font', 'disable_all', 'balloon_fill'],
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
        'format': ['png', 'webp', 'jpg', 'avif']  # 移除了 xcf, psd, pdf（使用 export_editable_psd 配置项代替）
    }


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
    all_translators = [str(t) for t in TRANSLATORS]
    
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
        
        # Get group config for translator permissions
        group_allowed = set()
        group_denied = set()
        try:
            from manga_translator.server.core.group_management_service import get_group_management_service
            group_service = get_group_management_service()
            group = group_service.get_group(account.group)
            
            if group:
                group_allowed = set(group.get('allowed_translators', []))
                group_denied = set(group.get('denied_translators', []))
        except Exception as e:
            import logging
            logging.getLogger('manga_translator.server').warning(f"Failed to get group config: {e}")
        
        # Get user permissions
        permissions = account.permissions
        user_allowed = set(permissions.allowed_translators) if permissions.allowed_translators else set()
        user_denied = set(permissions.denied_translators) if hasattr(permissions, 'denied_translators') and permissions.denied_translators else set()
        
        # 权限逻辑: 用户黑名单 + 用户组黑名单 - 用户白名单
        # 1. 如果用户组允许所有（*），则从所有翻译器开始
        # 2. 否则从用户组允许的翻译器开始
        if "*" in group_allowed:
            result = set(all_translators)
        else:
            result = group_allowed.intersection(set(all_translators)) if group_allowed else set(all_translators)
        
        # 3. 移除用户组黑名单
        result -= group_denied
        
        # 4. 移除用户黑名单
        result -= user_denied
        
        # 5. 用户白名单可以解锁（如果用户有明确的白名单且不是*）
        if user_allowed and "*" not in user_allowed:
            # 用户白名单可以添加回被用户组禁用的翻译器
            for t in user_allowed:
                if t in all_translators:
                    result.add(t)
        
        return sorted(list(result))
    
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
            from manga_translator.server.core.group_management_service import get_group_management_service
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
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                               'examples', 'config', 'translators.json')
    
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
        except Exception as _e:
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
    # 默认值从 admin_settings 获取
    permissions = admin_settings.get('permissions', {})
    api_key_policy = admin_settings.get('api_key_policy', {})
    upload_limits = admin_settings.get('upload_limits', {})
    
    # 默认设置
    result = {
        'show_env_editor': admin_settings.get('show_env_to_users', False),
        'can_upload_fonts': permissions.get('can_upload_fonts', True),
        'can_upload_prompts': permissions.get('can_upload_prompts', True),
        'allow_server_keys': api_key_policy.get('allow_server_keys', True),
        'max_image_size_mb': upload_limits.get('max_image_size_mb', 0),
        'max_images_per_batch': upload_limits.get('max_images_per_batch', 0)
    }
    
    # 如果有用户登录，从用户组配置获取配额
    if x_session_token:
        try:
            account_service, session_service, _ = get_services()
            session = session_service.verify_token(x_session_token)
            if session:
                account = account_service.get_user(session.username)
                if account:
                    # 获取用户组配置
                    from manga_translator.server.core.group_management_service import get_group_management_service
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
async def get_api_key_policy():
    """Get API key policy for users"""
    return admin_settings.get('api_key_policy', {
        'require_user_keys': False,
        'allow_server_keys': True,
        'save_user_keys_to_server': False,
    })


@router.get("/env")
async def get_user_env_vars():
    """Get environment variables for users (based on policy)"""
    from dotenv import dotenv_values
    
    # Check if users are allowed to view .env editor
    # Note: This only controls whether users can "see" API Keys, not whether server keys are used during translation
    show_env_to_users = admin_settings.get('show_env_to_users', False)
    if not show_env_to_users:
        # Don't show API Keys editor to users, return empty
        return {}
    
    # If showing editor, return server's API Keys values (let users see and edit)
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')
    if os.path.exists(env_path):
        env_vars = dotenv_values(env_path)
        return {key: value for key, value in env_vars.items() if value}
    return {}


@router.post("/env")
async def save_user_env_vars(env_vars: dict):
    """Save user's environment variables"""
    from dotenv import load_dotenv
    from fastapi import HTTPException
    from manga_translator.server.core.env_service import EnvService
    
    # Check if users are allowed to edit .env
    show_env_to_users = admin_settings.get('show_env_to_users', False)
    if not show_env_to_users:
        # Don't allow users to edit .env, return error
        raise HTTPException(403, detail="Not allowed to edit environment variables")
    
    policy = admin_settings.get('api_key_policy', {})
    save_to_server = policy.get('save_user_keys_to_server', False)
    
    if not save_to_server:
        # Don't save to server, just return success (actually temporary use)
        return {"success": True, "saved_to_server": False}
    
    # Save to server .env file using EnvService for consistent formatting
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')
    try:
        env_service = EnvService(env_path)
        
        for key, value in env_vars.items():
            if value:  # Only save non-empty values
                env_service.update_env_var(key, value)
        
        # 重新加载 .env 文件确保所有变量都是最新的
        load_dotenv(env_path, override=True)
        
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
