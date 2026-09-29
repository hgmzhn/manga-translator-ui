"""
配额管理服务 (QuotaManagementService)

管理用户的上传限制、对话框数量限制和每日翻译配额。
支持配额检查、计数和重置功能。
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional

from manga_translator.server.core.group_service import GroupService
from manga_translator.server.models.quota_models import QuotaLimit, QuotaStats
from manga_translator.server.repositories.permission_repository import (
    PermissionRepository,
)
from manga_translator.server.repositories.quota_repository import QuotaRepository

logger = logging.getLogger(__name__)


class QuotaManagementService:
    """配额管理服务"""
    
    # 默认配额限制
    DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    DEFAULT_MAX_FILES_PER_UPLOAD = 10
    DEFAULT_MAX_SESSIONS = 5
    DEFAULT_DAILY_QUOTA = -1  # -1 表示无限
    
    def __init__(
        self,
        quota_repo: QuotaRepository,
        permission_repo: PermissionRepository,
        group_service: GroupService,
        data_path: str = "manga_translator/server/data"
    ):
        """
        初始化配额管理服务
        
        Args:
            quota_repo: 配额数据仓库
            permission_repo: 权限数据仓库
            group_service: 用户组服务
            data_path: 数据存储路径
        """
        self.quota_repo = quota_repo
        self.permission_repo = permission_repo
        self.group_service = group_service
        self.data_path = Path(data_path)
        
        # 活跃会话跟踪 (内存中)
        self._active_sessions: Dict[str, List[str]] = {}  # user_id -> [session_tokens]
        
        logger.info("QuotaManagementService initialized")
    
    def _get_user_quota_limit(self, user_id: str) -> QuotaLimit:
        """
        获取用户的配额限制（考虑继承）
        
        优先级：用户级 > 用户组级 > 全局默认
        
        Args:
            user_id: 用户ID
            
        Returns:
            QuotaLimit: 用户配额限制
        """
        # 1. 尝试获取用户级配额
        user_quota_data = self.quota_repo.get_user_quota(user_id)
        if user_quota_data:
            return QuotaLimit.from_dict(user_quota_data)
        
        # 2. 尝试从用户组获取配额
        user_info = self.permission_repo.get_user_permissions(user_id)
        if user_info and 'group' in user_info:
            group_name = user_info['group']
            group_config = self.group_service.get_group_config(group_name)
            
            if group_config and 'quota_limits' in group_config:
                limits = group_config['quota_limits']
                quota = QuotaLimit(
                    user_id=user_id,
                    max_file_size=limits.get('max_file_size', self.DEFAULT_MAX_FILE_SIZE),
                    max_files_per_upload=limits.get('max_files_per_upload', self.DEFAULT_MAX_FILES_PER_UPLOAD),
                    max_sessions=limits.get('max_sessions', self.DEFAULT_MAX_SESSIONS),
                    daily_quota=limits.get('daily_quota', self.DEFAULT_DAILY_QUOTA),
                    current_usage=0,
                    last_reset=datetime.now(UTC).isoformat()
                )
                # 保存到用户级别以便后续快速访问
                self.quota_repo.set_user_quota(user_id, quota)
                return quota
        
        # 3. 使用全局默认值
        quota = QuotaLimit(
            user_id=user_id,
            max_file_size=self.DEFAULT_MAX_FILE_SIZE,
            max_files_per_upload=self.DEFAULT_MAX_FILES_PER_UPLOAD,
            max_sessions=self.DEFAULT_MAX_SESSIONS,
            daily_quota=self.DEFAULT_DAILY_QUOTA,
            current_usage=0,
            last_reset=datetime.now(UTC).isoformat()
        )
        # 保存到用户级别
        self.quota_repo.set_user_quota(user_id, quota)
        return quota
    
    def check_upload_limit(self, user_id: str, file_size: int, file_count: int) -> tuple[bool, Optional[str]]:
        """
        检查上传限制
        
        Args:
            user_id: 用户ID
            file_size: 单个文件大小（字节）
            file_count: 文件数量
            
        Returns:
            tuple[bool, Optional[str]]: (是否允许, 错误消息)
        """
        try:
            quota = self._get_user_quota_limit(user_id)
            
            # 检查文件大小限制
            if file_size > quota.max_file_size:
                max_mb = quota.max_file_size / (1024 * 1024)
                current_mb = file_size / (1024 * 1024)
                return False, f"文件大小 {current_mb:.2f}MB 超过限制 {max_mb:.2f}MB"
            
            # 检查文件数量限制
            if file_count > quota.max_files_per_upload:
                return False, f"文件数量 {file_count} 超过限制 {quota.max_files_per_upload}"
            
            logger.info(f"Upload limit check passed for user {user_id}: {file_count} files, {file_size} bytes")
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking upload limit for user {user_id}: {e}")
            return False, f"检查上传限制时出错: {str(e)}"
    
    def check_session_limit(self, user_id: str) -> tuple[bool, Optional[str]]:
        """
        检查对话框数量限制
        
        Args:
            user_id: 用户ID
            
        Returns:
            tuple[bool, Optional[str]]: (是否允许, 错误消息)
        """
        try:
            quota = self._get_user_quota_limit(user_id)
            
            # 获取当前活跃会话数
            active_count = len(self._active_sessions.get(user_id, []))
            
            # 检查是否超过限制
            if active_count >= quota.max_sessions:
                return False, f"活跃对话框数量 {active_count} 已达到限制 {quota.max_sessions}"
            
            logger.info(f"Session limit check passed for user {user_id}: {active_count}/{quota.max_sessions}")
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking session limit for user {user_id}: {e}")
            return False, f"检查对话框限制时出错: {str(e)}"
    
    def check_daily_quota(self, user_id: str, image_count: int = 1) -> tuple[bool, Optional[str]]:
        """
        检查每日翻译配额
        
        Args:
            user_id: 用户ID
            image_count: 要翻译的图片数量
            
        Returns:
            tuple[bool, Optional[str]]: (是否允许, 错误消息)
        """
        try:
            quota = self._get_user_quota_limit(user_id)
            
            # -1 表示无限配额
            if quota.daily_quota == -1:
                logger.info(f"Daily quota check passed for user {user_id}: unlimited quota")
                return True, None
            
            # 检查是否需要重置配额
            self._check_and_reset_daily_quota(user_id, quota)
            
            # 重新获取配额（可能已重置）
            quota = self._get_user_quota_limit(user_id)
            
            # 检查剩余配额
            remaining = quota.daily_quota - quota.current_usage
            if remaining < image_count:
                logger.warning(f"Daily quota exceeded for user {user_id}: remaining {remaining}, requested {image_count}")
                return False, f"每日配额不足: 剩余 {remaining}, 需要 {image_count}"
            
            logger.info(f"Daily quota check passed for user {user_id}: {quota.current_usage}/{quota.daily_quota}")
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking daily quota for user {user_id}: {e}")
            return False, f"检查每日配额时出错: {str(e)}"
    
    def _check_and_reset_daily_quota(self, user_id: str, quota: QuotaLimit) -> None:
        """
        检查并重置每日配额（如果需要）
        
        Args:
            user_id: 用户ID
            quota: 当前配额
        """
        if not quota.last_reset:
            # 如果从未重置过，立即重置
            self.reset_daily_quota(user_id)
            return
        
        try:
            last_reset = datetime.fromisoformat(quota.last_reset)
            now = datetime.now(UTC)
            
            # 如果上次重置是在不同的日期，则重置
            if last_reset.date() < now.date():
                logger.info(f"Resetting daily quota for user {user_id} (last reset: {last_reset.date()})")
                self.reset_daily_quota(user_id)
        except Exception as e:
            logger.error(f"Error parsing last_reset date for user {user_id}: {e}")
            # 如果解析失败，重置配额
            self.reset_daily_quota(user_id)
    
    def increment_quota_usage(self, user_id: str, image_count: int) -> bool:
        """
        增加配额使用量（仅在翻译成功后调用）
        
        Args:
            user_id: 用户ID
            image_count: 成功翻译的图片数量
            
        Returns:
            bool: 是否成功
        """
        try:
            success = self.quota_repo.increment_usage(user_id, image_count)
            if success:
                logger.info(f"Incremented quota usage for user {user_id} by {image_count}")
            else:
                logger.warning(f"Failed to increment quota usage for user {user_id}")
            return success
        except Exception as e:
            logger.error(f"Error incrementing quota usage for user {user_id}: {e}")
            return False
    
    def reset_daily_quota(self, user_id: Optional[str] = None) -> bool:
        """
        重置每日配额
        
        Args:
            user_id: 用户ID，如果为None则重置所有用户
            
        Returns:
            bool: 是否成功
        """
        try:
            if user_id:
                # 重置单个用户
                success = self.quota_repo.reset_daily_usage(user_id)
                if success:
                    logger.info(f"Reset daily quota for user {user_id}")
                return success
            else:
                # 重置所有用户
                all_quotas = self.quota_repo.get_all_quotas()
                for uid in all_quotas.keys():
                    self.quota_repo.reset_daily_usage(uid)
                logger.info(f"Reset daily quota for all users ({len(all_quotas)} users)")
                return True
        except Exception as e:
            logger.error(f"Error resetting daily quota: {e}")
            return False
    
    def get_quota_stats(self, user_id: str) -> Optional[QuotaStats]:
        """
        获取用户配额统计
        
        Args:
            user_id: 用户ID
            
        Returns:
            QuotaStats: 配额统计信息
        """
        try:
            quota = self._get_user_quota_limit(user_id)
            
            # 计算剩余配额
            remaining = -1 if quota.daily_quota == -1 else (quota.daily_quota - quota.current_usage)
            
            # 获取活跃会话数
            active_sessions = len(self._active_sessions.get(user_id, []))
            
            stats = QuotaStats(
                user_id=user_id,
                daily_limit=quota.daily_quota,
                used_today=quota.current_usage,
                remaining=remaining,
                active_sessions=active_sessions,
                total_uploaded=quota.current_usage  # 简化实现，实际可能需要单独跟踪
            )
            
            logger.debug(f"Retrieved quota stats for user {user_id}")
            return stats
            
        except Exception as e:
            logger.error(f"Error getting quota stats for user {user_id}: {e}")
            return None
    
    def get_all_quota_stats(self) -> Dict[str, QuotaStats]:
        """
        获取所有用户的配额统计（管理员功能）
        
        Returns:
            Dict[str, QuotaStats]: 用户ID到配额统计的映射
        """
        try:
            all_quotas = self.quota_repo.get_all_quotas()
            stats_dict = {}
            
            for user_id in all_quotas.keys():
                stats = self.get_quota_stats(user_id)
                if stats:
                    stats_dict[user_id] = stats
            
            logger.info(f"Retrieved quota stats for {len(stats_dict)} users")
            return stats_dict
            
        except Exception as e:
            logger.error(f"Error getting all quota stats: {e}")
            return {}
    
    def register_session(self, user_id: str, session_token: str) -> bool:
        """
        注册活跃会话
        
        Args:
            user_id: 用户ID
            session_token: 会话令牌
            
        Returns:
            bool: 是否成功
        """
        try:
            if user_id not in self._active_sessions:
                self._active_sessions[user_id] = []
            
            if session_token not in self._active_sessions[user_id]:
                self._active_sessions[user_id].append(session_token)
                logger.info(f"Registered session {session_token} for user {user_id}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error registering session for user {user_id}: {e}")
            return False
    
    def unregister_session(self, user_id: str, session_token: str) -> bool:
        """
        注销活跃会话
        
        Args:
            user_id: 用户ID
            session_token: 会话令牌
            
        Returns:
            bool: 是否成功
        """
        try:
            if user_id in self._active_sessions:
                if session_token in self._active_sessions[user_id]:
                    self._active_sessions[user_id].remove(session_token)
                    logger.info(f"Unregistered session {session_token} for user {user_id}")
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Error unregistering session for user {user_id}: {e}")
            return False
    
    def get_active_sessions(self, user_id: str) -> List[str]:
        """
        获取用户的活跃会话列表
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[str]: 会话令牌列表
        """
        return self._active_sessions.get(user_id, []).copy()
    
    def set_user_quota_limits(
        self,
        user_id: str,
        max_file_size: Optional[int] = None,
        max_files_per_upload: Optional[int] = None,
        max_sessions: Optional[int] = None,
        daily_quota: Optional[int] = None
    ) -> bool:
        """
        设置用户的配额限制（管理员功能）
        
        Args:
            user_id: 用户ID
            max_file_size: 最大文件大小
            max_files_per_upload: 单次上传最大文件数
            max_sessions: 最大会话数
            daily_quota: 每日配额
            
        Returns:
            bool: 是否成功
        """
        try:
            # 获取现有配额或创建新配额
            quota = self._get_user_quota_limit(user_id)
            
            # 更新指定的限制
            if max_file_size is not None:
                quota.max_file_size = max_file_size
            if max_files_per_upload is not None:
                quota.max_files_per_upload = max_files_per_upload
            if max_sessions is not None:
                quota.max_sessions = max_sessions
            if daily_quota is not None:
                quota.daily_quota = daily_quota
            
            # 保存更新后的配额
            self.quota_repo.set_user_quota(user_id, quota)
            logger.info(f"Updated quota limits for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting quota limits for user {user_id}: {e}")
            return False
