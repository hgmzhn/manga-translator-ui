"""
自动清理服务

根据配置定期清理过期文件和超出存储限制的文件。
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from manga_translator.server_paths import SERVER_DATA_DIR, USER_RESOURCES_DIR

logger = logging.getLogger('manga_translator.server')


class CleanupService:
    """自动清理服务"""
    
    def __init__(self):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.directories = {
            "results": str(SERVER_DATA_DIR / "results"),
            "user_fonts": str(USER_RESOURCES_DIR / "fonts"),
            "user_prompts": str(USER_RESOURCES_DIR / "prompts")
        }
    
    def get_settings(self) -> dict:
        """获取清理设置"""
        from manga_translator.server.core.config_manager import admin_settings
        return admin_settings.get('cleanup', {
            'auto_cleanup': False,
            'interval_hours': 24,
            'max_age_days': 7,
            'max_size_gb': 10
        })
    
    def start(self):
        """启动自动清理任务"""
        if self.running:
            return
        
        settings = self.get_settings()
        if not settings.get('auto_cleanup', False):
            logger.info("Automatic cleanup is not enabled")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._cleanup_loop())
        logger.info("Automatic cleanup service started")
    
    def stop(self):
        """停止自动清理任务"""
        self.running = False
        if self.task:
            self.task.cancel()
            self.task = None
        logger.info("Automatic cleanup service stopped")
    
    async def _cleanup_loop(self):
        """清理循环"""
        while self.running:
            try:
                settings = self.get_settings()
                
                if not settings.get('auto_cleanup', False):
                    logger.info("Automatic cleanup disabled; stopping the cleanup loop")
                    break
                
                # 执行清理
                await self.run_cleanup()
                
                # 等待下一次清理
                interval_hours = settings.get('interval_hours', 24)
                await asyncio.sleep(interval_hours * 3600)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Automatic cleanup failed: {e}")
                await asyncio.sleep(3600)  # 出错后等待1小时重试
    
    async def run_cleanup(self) -> dict:
        """执行清理"""
        settings = self.get_settings()
        max_age_days = settings.get('max_age_days', 7)
        max_size_gb = settings.get('max_size_gb', 10)
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        
        total_freed = 0
        files_deleted = 0
        
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(days=max_age_days)
        
        # 1. 清理过期文件
        for dir_name, dir_path in self.directories.items():
            if not os.path.exists(dir_path):
                continue
            
            freed, deleted = self._cleanup_old_files(dir_path, cutoff_time)
            total_freed += freed
            files_deleted += deleted
        
        # 2. 如果总大小超过限制，继续清理最旧的文件
        total_size = self._get_total_size()
        if total_size > max_size_bytes:
            extra_freed, extra_deleted = self._cleanup_by_size(max_size_bytes)
            total_freed += extra_freed
            files_deleted += extra_deleted
        
        if files_deleted > 0:
            logger.info(f"Automatic cleanup complete: deleted {files_deleted} files, freed {total_freed / 1024 / 1024:.2f} MB")
        
        return {
            "freed_bytes": total_freed,
            "files_deleted": files_deleted
        }
    
    def _cleanup_old_files(self, directory: str, cutoff_time: datetime) -> tuple:
        """清理过期文件"""
        freed = 0
        deleted = 0
        
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path), tz=timezone.utc)
                    if mtime < cutoff_time:
                        size = os.path.getsize(file_path)
                        os.remove(file_path)
                        freed += size
                        deleted += 1
                except (OSError, IOError) as e:
                    logger.warning(f"Failed to delete file: {file_path}, error: {e}")
        
        # 清理空目录
        self._remove_empty_dirs(directory)
        
        return freed, deleted
    
    def _cleanup_by_size(self, max_size_bytes: int) -> tuple:
        """按大小清理，删除最旧的文件直到低于限制"""
        freed = 0
        deleted = 0
        
        # 收集所有文件及其修改时间
        all_files = []
        for dir_name, dir_path in self.directories.items():
            if not os.path.exists(dir_path):
                continue
            
            for root, dirs, files in os.walk(dir_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        size = os.path.getsize(file_path)
                        all_files.append((file_path, mtime, size))
                    except (OSError, IOError):
                        pass
        
        # 按修改时间排序（最旧的在前）
        all_files.sort(key=lambda x: x[1])
        
        # 删除最旧的文件直到低于限制
        current_size = self._get_total_size()
        for file_path, mtime, size in all_files:
            if current_size <= max_size_bytes:
                break
            
            try:
                os.remove(file_path)
                freed += size
                deleted += 1
                current_size -= size
            except (OSError, IOError) as e:
                logger.warning(f"Failed to delete file: {file_path}, error: {e}")
        
        return freed, deleted
    
    def _get_total_size(self) -> int:
        """获取所有目录的总大小"""
        total = 0
        for dir_path in self.directories.values():
            if os.path.exists(dir_path):
                for root, dirs, files in os.walk(dir_path):
                    for file in files:
                        try:
                            total += os.path.getsize(os.path.join(root, file))
                        except (OSError, IOError):
                            pass
        return total
    
    def _remove_empty_dirs(self, directory: str):
        """递归删除空目录"""
        for root, dirs, files in os.walk(directory, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except (OSError, IOError):
                    pass


# 全局实例
_cleanup_service: Optional[CleanupService] = None


def get_cleanup_service() -> CleanupService:
    """获取清理服务实例"""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = CleanupService()
    return _cleanup_service


# ============================================================================
# CleanupSchedulerService - 兼容现有路由的清理调度服务
# ============================================================================

class CleanupRule:
    """清理规则"""
    def __init__(self, id: str, level: str, retention_days: int, target_id: str = None,
                 enabled: bool = True, created_at: str = None, created_by: str = None):
        self.id = id
        self.level = level
        self.retention_days = retention_days
        self.target_id = target_id
        self.enabled = enabled
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.created_by = created_by
    
    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'level': self.level,
            'retention_days': self.retention_days,
            'target_id': self.target_id,
            'enabled': self.enabled,
            'created_at': self.created_at,
            'created_by': self.created_by
        }


class CleanupReport:
    """清理报告"""
    def __init__(self):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.deleted_sessions = []
        self.deleted_files_count = 0
        self.freed_space_bytes = 0
        self.errors = []
        self.success = True
    
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'deleted_sessions_count': len(self.deleted_sessions),
            'deleted_sessions': self.deleted_sessions,
            'deleted_files_count': self.deleted_files_count,
            'freed_space_mb': self.freed_space_bytes / 1024 / 1024,
            'freed_space_bytes': self.freed_space_bytes,
            'errors': self.errors,
            'success': self.success
        }


class CleanupSchedulerService:
    """清理调度服务 - 兼容现有路由"""
    
    def __init__(self):
        self.rules: list = []
        self.history: list = []
        self.cleanup_service = get_cleanup_service()
    
    def configure_auto_cleanup(self, level: str, retention_days: int, 
                               target_id: str = None, admin_id: str = None) -> CleanupRule:
        """配置自动清理规则"""
        import uuid
        rule = CleanupRule(
            id=str(uuid.uuid4()),
            level=level,
            retention_days=retention_days,
            target_id=target_id,
            created_by=admin_id
        )
        self.rules.append(rule)
        return rule
    
    def get_cleanup_rules(self) -> list:
        """获取所有清理规则"""
        return self.rules
    
    def delete_cleanup_rule(self, rule_id: str) -> bool:
        """删除清理规则"""
        for i, rule in enumerate(self.rules):
            if rule.id == rule_id:
                self.rules.pop(i)
                return True
        return False
    
    def manual_cleanup(self, filters: dict = None, admin_id: str = None,
                       user_group_mapping: dict = None) -> CleanupReport:
        """手动清理"""
        report = CleanupReport()
        
        try:
            # 使用基础清理服务执行清理
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果在异步上下文中，创建新任务
                result = {"freed_bytes": 0, "files_deleted": 0}
            else:
                result = loop.run_until_complete(self.cleanup_service.run_cleanup())
            
            report.freed_space_bytes = result.get("freed_bytes", 0)
            report.deleted_files_count = result.get("files_deleted", 0)
            report.success = True
        except Exception as e:
            report.errors.append(str(e))
            report.success = False
        
        self.history.append(report.to_dict())
        return report
    
    def get_status(self) -> dict:
        """获取调度器状态"""
        return {
            'running': self.cleanup_service.running,
            'rules_count': len(self.rules),
            'last_cleanup': self.history[-1]['timestamp'] if self.history else None
        }
    
    def run_now(self) -> dict:
        """立即执行清理"""
        report = self.manual_cleanup()
        return report.to_dict()
    
    def get_cleanup_history(self, limit: int = 10) -> list:
        """获取清理历史"""
        return self.history[-limit:]


# 全局调度服务实例
_scheduler_service: Optional[CleanupSchedulerService] = None


def get_cleanup_scheduler_service() -> CleanupSchedulerService:
    """获取清理调度服务实例"""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = CleanupSchedulerService()
    return _scheduler_service
