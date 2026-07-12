"""
资源管理路由模块

提供用户资源（提示词和字体）的上传、查询和删除API。
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from manga_translator.server.core.middleware import require_auth
from manga_translator.server.core.models import Session
from manga_translator.server.core.permission_integration import (
    IntegratedPermissionService,
)
from manga_translator.server.core.resource_service import ResourceManagementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resources", tags=["resources"])

# 全局服务实例（将在服务器启动时初始化）
_resource_service: ResourceManagementService = None
_permission_service: IntegratedPermissionService = None


def init_resource_routes(
    resource_service: ResourceManagementService,
    permission_service: IntegratedPermissionService
) -> None:
    """
    初始化资源路由使用的服务实例
    
    Args:
        resource_service: 资源管理服务
        permission_service: 权限管理服务
    """
    global _resource_service, _permission_service
    _resource_service = resource_service
    _permission_service = permission_service
    logger.info("Resource routes initialized")


def get_resource_service() -> ResourceManagementService:
    """获取资源管理服务实例"""
    if not _resource_service:
        raise RuntimeError("Resource service not initialized")
    return _resource_service


def get_permission_service() -> IntegratedPermissionService:
    """获取权限管理服务实例"""
    if not _permission_service:
        raise RuntimeError("Permission service not initialized")
    return _permission_service


# ============================================================================
# 提示词管理端点
# ============================================================================

@router.post("/prompts", response_model=dict)
async def upload_prompt(
    file: UploadFile = File(...),
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    上传提示词文件
    
    需求: 1.1, 1.5
    
    Args:
        file: 上传的文件
        session: 用户会话
        resource_service: 资源管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 包含上传的资源信息
    
    Raises:
        HTTPException: 如果权限不足或上传失败
    """
    # 检查上传权限
    if not permission_service.check_upload_prompt_permission(session.username):
        raise HTTPException(
            status_code=403,
            detail="您没有上传提示词的权限"
        )
    
    try:
        # 上传文件
        resource = await resource_service.upload_prompt(session.username, file)
        
        logger.info(f"User {session.username} uploaded prompt: {resource.filename}")
        
        return {
            "success": True,
            "message": "提示词上传成功",
            "resource": resource.to_dict()
        }
    
    except ValueError as e:
        logger.warning(f"Prompt upload failed for user {session.username}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Unexpected error during prompt upload: {e}")
        raise HTTPException(status_code=500, detail="上传提示词时发生错误")


@router.get("/prompts", response_model=dict)
async def get_prompts(
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service)
):
    """
    获取用户的提示词列表
    
    需求: 1.3
    
    Args:
        session: 用户会话
        resource_service: 资源管理服务
    
    Returns:
        dict: 包含提示词列表
    """
    try:
        prompts = resource_service.get_user_prompts(session.username)
        
        return {
            "success": True,
            "prompts": [prompt.to_dict() for prompt in prompts],
            "count": len(prompts)
        }
    
    except Exception as e:
        logger.error(f"Error getting prompts for user {session.username}: {e}")
        raise HTTPException(status_code=500, detail="获取提示词列表时发生错误")


@router.delete("/prompts/{resource_id}", response_model=dict)
async def delete_prompt(
    resource_id: str,
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    删除提示词
    
    需求: 1.4
    
    Args:
        resource_id: 资源ID
        session: 用户会话
        resource_service: 资源管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 删除结果
    
    Raises:
        HTTPException: 如果权限不足或删除失败
    """
    # 检查删除权限
    if not permission_service.check_delete_own_files_permission(session.username):
        raise HTTPException(
            status_code=403,
            detail="您没有删除文件的权限"
        )
    
    try:
        # 删除资源
        success = resource_service.delete_prompt(resource_id, session.username)
        
        if success:
            logger.info(f"User {session.username} deleted prompt: {resource_id}")
            return {
                "success": True,
                "message": "提示词删除成功"
            }
        else:
            raise HTTPException(status_code=500, detail="删除提示词失败")
    
    except ValueError as e:
        logger.warning(f"Prompt deletion failed for user {session.username}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Unexpected error during prompt deletion: {e}")
        raise HTTPException(status_code=500, detail="删除提示词时发生错误")


# ============================================================================
# 字体管理端点
# ============================================================================

def _font_public_dict(resource) -> dict:
    return {
        'id': resource.id,
        'filename': resource.filename,
        'font_family': resource.font_family,
    }

@router.post("/fonts", response_model=dict)
async def upload_font(
    file: UploadFile = File(...),
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    上传字体文件
    
    需求: 2.1, 2.5
    
    Args:
        file: 上传的文件
        session: 用户会话
        resource_service: 资源管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 包含上传的资源信息
    
    Raises:
        HTTPException: 如果权限不足或上传失败
    """
    # 检查上传权限
    has_permission = permission_service.check_upload_font_permission(session.username)
    logger.info(f"[DEBUG] User {session.username} upload font permission: {has_permission}")
    if not has_permission:
        raise HTTPException(
            status_code=403,
            detail="您没有上传字体的权限"
        )
    
    try:
        # 上传文件
        resource = await resource_service.upload_font(session.username, file)
        try:
            from manga_translator.rendering.text_render import load_font_file
            resource.font_family = load_font_file(str(resource_service.base_path / resource.file_path))
            resource_service.fonts_repo.update_resource(
                resource.id, {'font_family': resource.font_family}
            )
        except Exception as exc:
            logger.warning(f"Font uploaded but Qt registration failed for {resource.filename}: {exc}")
        
        logger.info(f"User {session.username} uploaded font: {resource.filename}")
        
        return {
            "success": True,
            "message": "字体上传成功",
            "resource": _font_public_dict(resource)
        }
    
    except ValueError as e:
        logger.warning(f"Font upload failed for user {session.username}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Unexpected error during font upload: {e}")
        raise HTTPException(status_code=500, detail="上传字体时发生错误")


@router.get("/fonts", response_model=dict)
async def get_fonts(
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service)
):
    """
    获取用户的字体列表
    
    需求: 2.3
    
    Args:
        session: 用户会话
        resource_service: 资源管理服务
    
    Returns:
        dict: 包含字体列表
    """
    try:
        fonts = resource_service.get_user_fonts(session.username)
        
        return {
            "success": True,
            "fonts": [_font_public_dict(font) for font in fonts],
            "count": len(fonts)
        }
    
    except Exception as e:
        logger.error(f"Error getting fonts for user {session.username}: {e}")
        raise HTTPException(status_code=500, detail="获取字体列表时发生错误")


@router.delete("/fonts/{resource_id}", response_model=dict)
async def delete_font(
    resource_id: str,
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    删除字体
    
    需求: 2.4
    
    Args:
        resource_id: 资源ID
        session: 用户会话
        resource_service: 资源管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 删除结果
    
    Raises:
        HTTPException: 如果权限不足或删除失败
    """
    # 检查删除权限
    if not permission_service.check_delete_own_files_permission(session.username):
        raise HTTPException(
            status_code=403,
            detail="您没有删除文件的权限"
        )
    
    try:
        # 删除资源
        success = resource_service.delete_font(resource_id, session.username)
        
        if success:
            logger.info(f"User {session.username} deleted font: {resource_id}")
            return {
                "success": True,
                "message": "字体删除成功"
            }
        else:
            raise HTTPException(status_code=500, detail="删除字体失败")
    
    except ValueError as e:
        logger.warning(f"Font deletion failed for user {session.username}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Unexpected error during font deletion: {e}")
        raise HTTPException(status_code=500, detail="删除字体时发生错误")


@router.delete("/fonts/by-name/{filename}", response_model=dict)
async def delete_font_by_name(
    filename: str,
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    按文件名删除字体
    
    Args:
        filename: 文件名
        session: 用户会话
    
    Returns:
        dict: 删除结果
    """
    if not permission_service.check_delete_own_files_permission(session.username):
        raise HTTPException(status_code=403, detail="您没有删除文件的权限")
    
    try:
        # 获取用户的字体列表，找到匹配的资源
        fonts = resource_service.get_user_fonts(session.username)
        target_font = None
        for font in fonts:
            if font.filename == filename:
                target_font = font
                break
        
        if not target_font:
            raise HTTPException(status_code=404, detail="字体不存在")
        
        success = resource_service.delete_font(target_font.id, session.username)
        if success:
            return {"success": True, "message": "字体删除成功"}
        else:
            raise HTTPException(status_code=500, detail="删除字体失败")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting font by name: {e}")
        raise HTTPException(status_code=500, detail="删除字体时发生错误")


@router.delete("/prompts/by-name/{filename}", response_model=dict)
async def delete_prompt_by_name(
    filename: str,
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    按文件名删除提示词
    
    Args:
        filename: 文件名
        session: 用户会话
    
    Returns:
        dict: 删除结果
    """
    if not permission_service.check_delete_own_files_permission(session.username):
        raise HTTPException(status_code=403, detail="您没有删除文件的权限")
    
    try:
        # 获取用户的提示词列表，找到匹配的资源
        prompts = resource_service.get_user_prompts(session.username)
        target_prompt = None
        for prompt in prompts:
            if prompt.filename == filename:
                target_prompt = prompt
                break
        
        if not target_prompt:
            raise HTTPException(status_code=404, detail="提示词不存在")
        
        success = resource_service.delete_prompt(target_prompt.id, session.username)
        if success:
            return {"success": True, "message": "提示词删除成功"}
        else:
            raise HTTPException(status_code=500, detail="删除提示词失败")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting prompt by name: {e}")
        raise HTTPException(status_code=500, detail="删除提示词时发生错误")


# ============================================================================
# 资源统计端点
# ============================================================================

@router.get("/stats", response_model=dict)
async def get_resource_stats(
    session: Session = Depends(require_auth),
    resource_service: ResourceManagementService = Depends(get_resource_service)
):
    """
    获取用户的资源统计信息
    
    Args:
        session: 用户会话
        resource_service: 资源管理服务
    
    Returns:
        dict: 资源统计信息
    """
    try:
        stats = resource_service.get_resource_stats(session.username)
        
        return {
            "success": True,
            "stats": stats
        }
    
    except Exception as e:
        logger.error(f"Error getting resource stats for user {session.username}: {e}")
        raise HTTPException(status_code=500, detail="获取资源统计时发生错误")
