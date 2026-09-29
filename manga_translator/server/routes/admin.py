"""
Admin routes module.

This module contains all /admin/* endpoints for the manga translator server.
Updated to use the new session-based authentication system.
"""

import io
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from manga_translator.server.core.config_manager import (
    admin_settings,
    save_admin_settings,
)
from manga_translator.server.core.logging_manager import (
    add_log,
    global_log_queue,
    task_logs,
    task_logs_lock,
)
from manga_translator.server.core.middleware import require_admin
from manga_translator.server.core.models import Session
from manga_translator.server.core.task_manager import (
    active_tasks,
    active_tasks_lock,
)
from manga_translator.server_paths import SERVER_DATA_DIR, USER_RESOURCES_DIR

logger = logging.getLogger('manga_translator.server')

router = APIRouter(prefix="/admin", tags=["admin"])


# ============================================================================
# Admin Settings Management Endpoints
# ============================================================================

@router.get("/settings")
async def get_admin_settings(session: Session = Depends(require_admin)):
    """Get admin settings."""
    return admin_settings


@router.post("/settings")
@router.put("/settings")
async def update_admin_settings(
    settings: dict,
    session: Session = Depends(require_admin)
):
    """Update admin settings."""
    # Support partial updates (allow new keys)
    for key, value in settings.items():
        if key in admin_settings and isinstance(admin_settings[key], dict) and isinstance(value, dict):
            admin_settings[key].update(value)
        else:
            admin_settings[key] = value
    
    # Save to file
    if save_admin_settings(admin_settings):
        logger.info(f"Admin settings updated by user '{session.username}'")
        return {"success": True, "message": "Settings saved to file"}
    else:
        return {"success": False, "message": "Failed to save settings to file"}


# ============================================================================
# Announcement Management Endpoints
# ============================================================================

@router.put("/announcement")
async def update_announcement(
    announcement: dict,
    session: Session = Depends(require_admin)
):
    """Update announcement."""
    admin_settings['announcement'] = announcement
    save_admin_settings(admin_settings)
    logger.info(f"Announcement updated by user '{session.username}': enabled={announcement.get('enabled')}, type={announcement.get('type')}")
    return {"success": True}


# ============================================================================
# Task Management Endpoints
# ============================================================================

@router.get("/tasks")
async def get_active_tasks_endpoint(session: Session = Depends(require_admin)):
    """Get all active tasks with user information."""
    # Use the task_manager function to get tasks
    from manga_translator.server.core.task_manager import get_active_tasks
    return get_active_tasks()


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    force: bool = False,
    session: Session = Depends(require_admin)
):
    """Cancel specified translation task."""
    with active_tasks_lock:
        if task_id in active_tasks:
            active_tasks[task_id]["cancel_requested"] = True
            
            if force:
                # Force cancel: directly call asyncio.Task.cancel()
                task = active_tasks[task_id].get("task")
                if task and not task.done():
                    task.cancel()
                    add_log(f"Administrator {session.username} forcibly cancelled task: {task_id[:8]}", "WARNING")
                    return {"success": True, "message": "任务已强制终止"}
                else:
                    add_log(f"Administrator {session.username} requested forced task cancellation, but the task has already completed: {task_id[:8]}", "INFO")
                    return {"success": True, "message": "任务已完成，无需取消"}
            else:
                # Cooperative cancel: set flag, wait for task to respond at checkpoint
                add_log(f"Administrator {session.username} requested task cancellation: {task_id[:8]}", "WARNING")
                return {"success": True, "message": "取消请求已发送（协作式取消）"}
        else:
            raise HTTPException(404, detail="任务不存在或已完成")


# ============================================================================
# Log Management Endpoints
# ============================================================================

@router.get("/logs")
async def get_logs_endpoint(
    session: Session = Depends(require_admin),
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    level: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """Get logs with filtering support."""
    from datetime import timezone

    # Validate and cap limit
    limit = min(max(1, limit), 1000)
    offset = max(0, offset)
    
    try:
        with task_logs_lock:
            if task_id:
                # Get logs for specific task
                logs = list(task_logs.get(task_id, []))
            else:
                # Get global logs
                logs = list(global_log_queue)
        
        # Filter by session_id if specified
        if session_id:
            logs = [log for log in logs if log.get('session_id') == session_id]
        
        # Filter by level if specified (skip if 'all')
        if level and level.lower() != 'all':
            level_upper = level.upper()
            logs = [log for log in logs if log.get('level', '').upper() == level_upper]
        
        # Filter by time range if specified
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                logs = [log for log in logs 
                       if datetime.fromisoformat(log.get('timestamp', '').replace('Z', '+00:00')) >= start_dt]
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid start_time format: {start_time}, error: {e}")
        
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                logs = [log for log in logs 
                       if datetime.fromisoformat(log.get('timestamp', '').replace('Z', '+00:00')) <= end_dt]
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid end_time format: {end_time}, error: {e}")
        
        # Get total before pagination
        total = len(logs)
        
        # Apply pagination (get most recent logs)
        # Reverse to get newest first, then slice, then reverse back
        logs = list(reversed(logs))
        paginated_logs = logs[offset:offset + limit]
        
        # Ensure all log messages are properly escaped and formatted
        for log in paginated_logs:
            if 'message' in log and isinstance(log['message'], str):
                # Ensure message is a string and handle any encoding issues
                log['message'] = log['message']
            if 'timestamp' in log:
                # Ensure timestamp is in ISO format
                try:
                    if isinstance(log['timestamp'], str):
                        # Validate it's a proper ISO format
                        datetime.fromisoformat(log['timestamp'].replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    # If invalid, use current time
                    log['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        return {
            "logs": paginated_logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total
        }
    
    except Exception as e:
        logger.error(f"Error fetching logs: {e}", exc_info=True)
        raise HTTPException(500, detail=f"Failed to fetch logs: {str(e)}")


@router.get("/logs/export")
async def export_logs(
    session: Session = Depends(require_admin),
    task_id: Optional[str] = None
):
    """Export logs as text file."""
    with task_logs_lock:
        if task_id:
            logs = list(task_logs.get(task_id, []))
            filename = f"logs_{task_id[:8]}.txt"
        else:
            logs = list(global_log_queue)
            from datetime import timezone
            filename = f"logs_all_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
    
    # Generate log text
    log_text = "\n".join([
        f"[{log['timestamp']}] [{log['level']}] {log['message']}"
        for log in logs
    ])
    
    return StreamingResponse(
        io.BytesIO(log_text.encode('utf-8')),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Storage and Cleanup Management Endpoints
# ============================================================================

def get_directory_stats(directory: str) -> dict:
    """获取目录的文件统计信息"""
    total_size = 0
    file_count = 0
    
    if os.path.exists(directory):
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    total_size += os.path.getsize(file_path)
                    file_count += 1
                except (OSError, IOError):
                    pass
    
    return {"size": total_size, "count": file_count}


@router.get("/storage/info")
async def get_storage_info(session: Session = Depends(require_admin)):
    """获取存储使用情况。"""
    results_dir = str(SERVER_DATA_DIR / "results")
    user_fonts_dir = str(USER_RESOURCES_DIR / "fonts")
    user_prompts_dir = str(USER_RESOURCES_DIR / "prompts")
    
    # 获取统计信息
    results_stats = get_directory_stats(results_dir)
    user_fonts_stats = get_directory_stats(user_fonts_dir)
    user_prompts_stats = get_directory_stats(user_prompts_dir)
    
    # 用户上传资源合计
    uploads_size = user_fonts_stats["size"] + user_prompts_stats["size"]
    uploads_count = user_fonts_stats["count"] + user_prompts_stats["count"]
    
    total_size = results_stats["size"] + uploads_size
    
    return {
        "uploads_size": uploads_size,
        "uploads_count": uploads_count,
        "results_size": results_stats["size"],
        "results_count": results_stats["count"],
        "cache_size": 0,  # 暂无独立缓存目录
        "cache_count": 0,
        "total_size": total_size,
        # 详细信息
        "user_fonts_size": user_fonts_stats["size"],
        "user_fonts_count": user_fonts_stats["count"],
        "user_prompts_size": user_prompts_stats["size"],
        "user_prompts_count": user_prompts_stats["count"]
    }


@router.post("/cleanup/{target}")
async def cleanup_storage(
    target: str,
    session: Session = Depends(require_admin)
):
    """清理指定目录。"""
    import shutil

    targets = {
        "uploads": [
            str(USER_RESOURCES_DIR / "fonts"),
            str(USER_RESOURCES_DIR / "prompts")
        ],
        "results": [str(SERVER_DATA_DIR / "results")],
        "cache": []  # 暂无独立缓存目录
    }
    
    if target not in targets and target != "all":
        raise HTTPException(400, detail=f"无效的清理目标: {target}")
    
    freed_bytes = 0
    cleaned_dirs = []
    
    if target == "all":
        dirs_to_clean = []
        for dirs in targets.values():
            dirs_to_clean.extend(dirs)
    else:
        dirs_to_clean = targets[target]
    
    for dir_path in dirs_to_clean:
        if os.path.exists(dir_path):
            # 计算清理前的大小
            stats = get_directory_stats(dir_path)
            freed_bytes += stats["size"]
            
            # 清理目录内容（保留目录本身和 index.json）
            for item in os.listdir(dir_path):
                # 保留 index.json 文件
                if item == "index.json":
                    continue
                item_path = os.path.join(dir_path, item)
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    logger.warning(f"Cleanup failed: {item_path}, error: {e}")
            
            cleaned_dirs.append(dir_path)
    
    logger.info(f"Administrator {session.username} cleaned the {target} directory, freeing {freed_bytes} bytes")
    
    return {
        "success": True,
        "freed_bytes": freed_bytes,
        "cleaned_dirs": cleaned_dirs
    }
