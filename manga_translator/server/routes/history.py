"""
历史记录管理路由模块

提供翻译历史的查询、搜索和下载API。
"""

import logging
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from manga_translator.server.core.download_ticket_service import (
    DownloadTicketService,
    resolve_path_within,
)
from manga_translator.server.core.history_service import HistoryManagementService
from manga_translator.server.core.middleware import require_admin, require_auth
from manga_translator.server.core.models import Session
from manga_translator.server.core.permission_integration import (
    IntegratedPermissionService,
)
from manga_translator.server.core.search_service import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


# ============================================================================
# Request Models
# ============================================================================

class BatchDownloadRequest(BaseModel):
    """批量下载请求模型"""
    session_tokens: List[str]
    filename: Optional[str] = None

# 全局服务实例（将在服务器启动时初始化）
_history_service: HistoryManagementService = None
_search_service: SearchService = None
_permission_service: IntegratedPermissionService = None
_download_ticket_service = DownloadTicketService()


def init_history_routes(
    history_service: HistoryManagementService,
    permission_service: IntegratedPermissionService,
    search_service: Optional[SearchService] = None,
    **kwargs  # 兼容旧的调用方式
) -> None:
    """
    初始化历史记录路由使用的服务实例
    
    Args:
        history_service: 历史管理服务
        permission_service: 权限管理服务
        search_service: 搜索服务（可选）
    """
    global _history_service, _search_service, _permission_service
    _history_service = history_service
    _search_service = search_service or SearchService()
    _permission_service = permission_service
    logger.info("History routes initialized")


def get_history_service() -> HistoryManagementService:
    """获取历史管理服务实例"""
    if not _history_service:
        raise RuntimeError("History service not initialized")
    return _history_service


def get_permission_service() -> IntegratedPermissionService:
    """获取权限管理服务实例"""
    if not _permission_service:
        raise RuntimeError("Permission service not initialized")
    return _permission_service


def get_search_service() -> SearchService:
    """获取搜索服务实例"""
    if not _search_service:
        raise RuntimeError("Search service not initialized")
    return _search_service


def _sanitize_history_filename(filename: str) -> str:
    if not filename or '/' in filename or '\\' in filename or filename in {'.', '..'}:
        raise HTTPException(status_code=400, detail="无效的文件名")
    return filename


def _resolve_history_file_path(
    result_directory: str | Path,
    result_path: str,
    filename: str,
) -> Path:
    safe_filename = _sanitize_history_filename(filename)
    try:
        session_dir = resolve_path_within(result_directory, result_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="会话文件目录不存在")

    if session_dir == Path(result_directory):
        raise HTTPException(status_code=404, detail="会话文件目录不存在")
    if not session_dir.exists() or not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="会话文件目录不存在")

    try:
        file_path = resolve_path_within(session_dir, session_dir / safe_filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文件名")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return file_path


def _sanitize_download_filename(filename: Optional[str], default_name: str) -> str:
    sanitized = Path(filename or default_name).name.strip().replace('\r', '').replace('\n', '')
    if not sanitized or sanitized in {'.', '..'}:
        sanitized = Path(default_name).name.strip().replace('\r', '').replace('\n', '')
    if not sanitized or sanitized in {'.', '..'}:
        sanitized = 'download.zip'
    if not sanitized.endswith('.zip'):
        sanitized += '.zip'
    return sanitized


def _get_history_user_id(session: Session) -> Optional[str]:
    return None if session.role == 'admin' else session.username


def _build_ticket_response(ticket, url: str) -> dict:
    expires_in = max(1, int((ticket.expires_at - datetime.now(timezone.utc)).total_seconds()))
    return {
        "url": url,
        "filename": ticket.filename,
        "expires_in": expires_in,
        "expires_at": ticket.expires_at.isoformat(),
    }


def _issue_download_ticket(
    path: str | Path,
    allowed_root: str | Path,
    filename: str,
    media_type: str,
    delete_on_cleanup: bool = False,
) -> dict:
    ticket = _download_ticket_service.issue_ticket(
        path=path,
        allowed_root=allowed_root,
        filename=filename,
        media_type=media_type,
        delete_on_cleanup=delete_on_cleanup,
    )
    return _build_ticket_response(ticket, f"/api/history/downloads/t/{ticket.token}")


@router.api_route("/downloads/t/{ticket}", methods=["GET", "HEAD"])
async def download_by_ticket(ticket: str):
    """使用短时下载票据提供文件下载。"""
    download_ticket = _download_ticket_service.get_ticket(ticket)
    if download_ticket is None:
        raise HTTPException(status_code=404, detail="下载链接无效或已过期")

    return FileResponse(
        path=download_ticket.path,
        filename=download_ticket.filename,
        media_type=download_ticket.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


# ============================================================================
# 用户历史查询端点
# ============================================================================

@router.get("", response_model=dict)
async def get_user_history(
    start_date: Optional[str] = Query(None, description="开始日期 (ISO格式)"),
    end_date: Optional[str] = Query(None, description="结束日期 (ISO格式)"),
    status: Optional[str] = Query(None, description="状态筛选"),
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    获取用户的翻译历史（支持筛选）
    
    需求: 3.2, 12.2
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        status: 状态筛选
        session: 用户会话
        history_service: 历史管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 包含历史记录列表
    
    Raises:
        HTTPException: 如果权限不足或查询失败
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有查看历史记录的权限"
        )
    
    try:
        # 构建筛选条件
        filters = {}
        if start_date:
            filters['start_date'] = start_date
        if end_date:
            filters['end_date'] = end_date
        if status:
            filters['status'] = status
        
        # 获取用户历史
        results = history_service.get_user_history(session.username, filters)
        
        return {
            "success": True,
            "history": [result.to_dict() for result in results],
            "count": len(results)
        }
    
    except Exception as e:
        logger.error(f"Error getting history for user {session.username}: {e}")
        raise HTTPException(status_code=500, detail="获取历史记录时发生错误")


@router.get("/{session_token}", response_model=dict)
async def get_session_details(
    session_token: str,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    获取单个会话的详细信息
    
    Args:
        session_token: 会话令牌
        session: 用户会话
        history_service: 历史管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 会话详细信息
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有查看历史记录的权限"
        )
    
    try:
        # 确定用户ID（管理员可以查看所有）
        user_id = _get_history_user_id(session)
        
        # 直接通过 history_service 获取会话，它会自动检查所有权
        result = history_service.get_session_by_token(session_token, user_id)
        
        if not result:
            raise HTTPException(
                status_code=404,
                detail="会话不存在"
            )
        
        # 获取会话文件列表
        files = [
            Path(file_path).name
            for file_path in history_service.get_session_files(session_token, user_id)
        ]
        
        result_dict = result.to_dict()
        result_dict['files'] = files
        
        return {
            "success": True,
            "session": result_dict
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session details: {e}")
        raise HTTPException(status_code=500, detail="获取会话详情时发生错误")


@router.post("/{session_token}/download-ticket")
async def create_session_download_ticket(
    session_token: str,
    filename: Optional[str] = Query(None, description="自定义下载文件名"),
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """为单个会话的 ZIP 下载创建短时票据。"""
    view_permission = permission_service.get_view_history_permission(session.username)
    if view_permission == 'none':
        raise HTTPException(status_code=403, detail="您没有下载历史记录的权限")

    user_id = _get_history_user_id(session)
    zip_path = history_service.create_download_archive(session_token, user_id)
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="会话不存在")

    download_filename = _sanitize_download_filename(
        filename,
        f"history_{session_token[:8]}.zip",
    )
    return _issue_download_ticket(
        path=zip_path,
        allowed_root=tempfile.gettempdir(),
        filename=download_filename,
        media_type="application/zip",
        delete_on_cleanup=True,
    )


# ============================================================================
# 管理员历史查询端点
# ============================================================================

@router.get("/admin/all", response_model=dict)
async def get_all_history(
    user_id: Optional[str] = Query(None, description="用户ID筛选"),
    start_date: Optional[str] = Query(None, description="开始日期 (ISO格式)"),
    end_date: Optional[str] = Query(None, description="结束日期 (ISO格式)"),
    status: Optional[str] = Query(None, description="状态筛选"),
    limit: int = Query(20, description="每页数量"),
    offset: int = Query(0, description="偏移量"),
    session: Session = Depends(require_admin),
    history_service: HistoryManagementService = Depends(get_history_service)
):
    """
    管理员查看所有用户的翻译历史
    
    需求: 5.1-5.5, 12.4
    
    Args:
        user_id: 用户ID筛选
        start_date: 开始日期
        end_date: 结束日期
        status: 状态筛选
        limit: 每页数量
        offset: 偏移量
        session: 管理员会话
        history_service: 历史管理服务
    
    Returns:
        dict: 包含所有历史记录列表
    """
    try:
        # 构建筛选条件
        filters = {}
        if user_id:
            filters['user_id'] = user_id
        if start_date:
            filters['start_date'] = start_date
        if end_date:
            filters['end_date'] = end_date
        if status:
            filters['status'] = status
        
        # 获取所有历史
        all_results = history_service.get_all_history(filters)
        total = len(all_results)
        
        # 应用分页
        paginated_results = all_results[offset:offset + limit]
        
        # 转换为前端期望的格式
        records = []
        for result in paginated_results:
            result_dict = result.to_dict()
            # 映射字段名以匹配前端期望
            records.append({
                'id': result_dict.get('session_token', result_dict.get('id', '')),
                'username': result_dict.get('user_id', ''),
                'filename': result_dict.get('metadata', {}).get('files', [''])[0] if result_dict.get('metadata', {}).get('files') else '-',
                'translator': result_dict.get('metadata', {}).get('translator', '-'),
                'status': result_dict.get('status', 'completed'),
                'created_at': result_dict.get('timestamp', ''),
                'file_count': result_dict.get('file_count', 0),
                'total_size': result_dict.get('total_size', 0),
            })
        
        return {
            "success": True,
            "records": records,
            "total": total,
            # 保留旧格式以兼容
            "history": [result.to_dict() for result in paginated_results],
            "count": len(paginated_results)
        }
    
    except Exception as e:
        logger.error(f"Error getting all history: {e}")
        raise HTTPException(status_code=500, detail="获取历史记录时发生错误")


# ============================================================================
# 搜索端点
# ============================================================================

@router.get("/search", response_model=dict)
async def search_history(
    q: str = Query(..., description="搜索查询"),
    start_date: Optional[str] = Query(None, description="开始日期 (ISO格式)"),
    end_date: Optional[str] = Query(None, description="结束日期 (ISO格式)"),
    status: Optional[str] = Query(None, description="状态筛选"),
    session: Session = Depends(require_auth),
    search_service: SearchService = Depends(get_search_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    搜索翻译历史
    
    需求: 13.1-13.5
    
    Args:
        q: 搜索查询
        start_date: 开始日期
        end_date: 结束日期
        status: 状态筛选
        session: 用户会话
        search_service: 搜索服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 搜索结果
    
    Raises:
        HTTPException: 如果权限不足或搜索失败
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有查看历史记录的权限"
        )
    
    try:
        # 构建筛选条件
        filters = {}
        if start_date:
            filters['start_date'] = start_date
        if end_date:
            filters['end_date'] = end_date
        if status:
            filters['status'] = status
        
        # 确定搜索范围
        user_id = _get_history_user_id(session)
        
        # 执行搜索
        results = search_service.search(q, filters, user_id)
        
        # 获取搜索统计
        stats = search_service.get_search_stats(q, filters, user_id)
        
        return {
            "success": True,
            "query": q,
            "results": [result.to_dict() for result in results],
            "count": len(results),
            "stats": stats
        }
    
    except Exception as e:
        logger.error(f"Error searching history: {e}")
        raise HTTPException(status_code=500, detail="搜索历史记录时发生错误")


# ============================================================================
# 下载端点
# ============================================================================

@router.get("/{session_token}/download")
async def download_session(
    session_token: str,
    background_tasks: BackgroundTasks,
    filename: Optional[str] = Query(None, description="自定义下载文件名"),
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    下载单个会话的翻译结果
    
    Args:
        session_token: 会话令牌
        filename: 自定义文件名（可选）
        session: 用户会话
        history_service: 历史管理服务
        permission_service: 权限管理服务
    
    Returns:
        FileResponse: ZIP文件
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有下载历史记录的权限"
        )
    
    try:
        # 确定用户ID（管理员可以下载所有）
        is_admin = session.role == 'admin'
        user_id = None if is_admin else session.username
        
        # 创建ZIP文件（history_service 会自动检查所有权）
        zip_path = history_service.create_download_archive(session_token, user_id)
        
        if not zip_path or not os.path.exists(zip_path):
            raise HTTPException(
                status_code=404,
                detail="会话不存在"
            )
        
        # 添加后台任务清理临时文件
        background_tasks.add_task(history_service.cleanup_temp_file, zip_path)
        
        # 使用自定义文件名或默认文件名
        download_filename = _sanitize_download_filename(
            filename,
            f"history_{session_token[:8]}.zip",
        )
        
        # 返回文件
        return FileResponse(
            path=zip_path,
            filename=download_filename,
            media_type="application/zip"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading session: {e}")
        raise HTTPException(status_code=500, detail="下载会话时发生错误")


@router.post("/batch-download-ticket")
async def create_batch_download_ticket(
    request: BatchDownloadRequest,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """为批量历史 ZIP 下载创建短时票据。"""
    view_permission = permission_service.get_view_history_permission(session.username)
    if view_permission == 'none':
        raise HTTPException(status_code=403, detail="您没有下载历史记录的权限")

    if len(request.session_tokens) > 50:
        raise HTTPException(status_code=400, detail="批量下载最多支持50个会话")

    user_id = _get_history_user_id(session)
    zip_path = history_service.create_batch_download_archive(request.session_tokens, user_id)
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="无法创建下载文件或您没有访问权限")

    download_filename = _sanitize_download_filename(request.filename, os.path.basename(zip_path))
    return _issue_download_ticket(
        path=zip_path,
        allowed_root=tempfile.gettempdir(),
        filename=download_filename,
        media_type="application/zip",
        delete_on_cleanup=True,
    )


@router.post("/batch-download")
async def batch_download_sessions(
    request: BatchDownloadRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    批量下载多个会话的翻译结果
    
    Args:
        request: 批量下载请求
        session: 用户会话
        history_service: 历史管理服务
        permission_service: 权限管理服务
    
    Returns:
        FileResponse: ZIP文件
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有下载历史记录的权限"
        )
    
    # 限制批量下载数量
    if len(request.session_tokens) > 50:
        raise HTTPException(
            status_code=400,
            detail="批量下载最多支持50个会话"
        )
    
    try:
        # 确定用户ID（管理员可以下载所有）
        user_id = _get_history_user_id(session)
        
        # 创建批量ZIP文件
        zip_path = history_service.create_batch_download_archive(
            request.session_tokens,
            user_id
        )
        
        if not zip_path or not os.path.exists(zip_path):
            raise HTTPException(
                status_code=404,
                detail="无法创建下载文件或您没有访问权限"
            )
        
        # 添加后台任务清理临时文件
        background_tasks.add_task(history_service.cleanup_temp_file, zip_path)
        
        # 返回文件
        return FileResponse(
            path=zip_path,
            filename=os.path.basename(zip_path),
            media_type="application/zip"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error batch downloading sessions: {e}")
        raise HTTPException(status_code=500, detail="批量下载会话时发生错误")


# ============================================================================
# 历史删除端点
# ============================================================================

@router.get("/{session_token}/file/{filename}")
async def get_history_file(
    session_token: str,
    filename: str,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    获取历史记录中的单个文件
    
    Args:
        session_token: 会话令牌
        filename: 文件名
        session: 用户会话
    
    Returns:
        FileResponse: 文件内容
    """
    # 检查查看权限
    view_permission = permission_service.get_view_history_permission(session.username)
    
    if view_permission == 'none':
        raise HTTPException(
            status_code=403,
            detail="您没有查看历史记录的权限"
        )
    
    try:
        # 确定用户ID（管理员可以查看所有）
        is_admin = session.role == 'admin'
        user_id = None if is_admin else session.username
        
        # 直接通过 history_service 获取会话，它会自动检查所有权
        result = history_service.get_session_by_token(session_token, user_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="会话不存在或您没有访问权限")
        
        # 构建文件路径
        file_path = _resolve_history_file_path(
            history_service.result_directory,
            result.result_path,
            filename,
        )
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        
        # 返回文件
        return FileResponse(
            path=file_path,
            filename=file_path.name,
            media_type=media_type
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history file: {e}")
        raise HTTPException(status_code=500, detail="获取文件时发生错误")


@router.post("/{session_token}/file/{filename}/download-ticket")
async def create_history_file_download_ticket(
    session_token: str,
    filename: str,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """为历史单文件下载创建短时票据。"""
    view_permission = permission_service.get_view_history_permission(session.username)
    if view_permission == 'none':
        raise HTTPException(status_code=403, detail="您没有查看历史记录的权限")

    user_id = _get_history_user_id(session)
    result = history_service.get_session_by_token(session_token, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="会话不存在或您没有访问权限")

    file_path = _resolve_history_file_path(
        history_service.result_directory,
        result.result_path,
        filename,
    )
    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return _issue_download_ticket(
        path=file_path,
        allowed_root=history_service.result_directory,
        filename=file_path.name,
        media_type=media_type,
        delete_on_cleanup=False,
    )


@router.delete("/{session_token}", response_model=dict)
async def delete_session(
    session_token: str,
    session: Session = Depends(require_auth),
    history_service: HistoryManagementService = Depends(get_history_service),
    permission_service: IntegratedPermissionService = Depends(get_permission_service)
):
    """
    删除翻译会话
    
    Args:
        session_token: 会话令牌
        session: 用户会话
        history_service: 历史管理服务
        permission_service: 权限管理服务
    
    Returns:
        dict: 删除结果
    """
    # 检查删除权限
    is_admin = session.role == 'admin'
    can_delete_own = permission_service.check_delete_own_files_permission(session.username)
    
    if not is_admin and not can_delete_own:
        raise HTTPException(
            status_code=403,
            detail="您没有删除历史记录的权限"
        )
    
    try:
        # 删除会话（history_service 会自动检查所有权）
        user_id = None if is_admin else session.username
        success = history_service.delete_session(session_token, user_id)
        
        if success:
            logger.info(f"User {session.username} deleted session: {session_token}")
            return {
                "success": True,
                "message": "会话删除成功"
            }
        else:
            raise HTTPException(
                status_code=404,
                detail="会话不存在"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail="删除会话时发生错误")

