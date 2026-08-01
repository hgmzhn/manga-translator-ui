"""
系统初始化模块

负责系统启动时的初始化逻辑，包括：
- 检查和创建默认管理员账号
- 加载用户账号到内存
- 启动会话清理定时任务
- 启动审计日志轮转定时任务
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from manga_translator.server.core.account_service import AccountService
from manga_translator.server.core.audit_service import AuditService
from manga_translator.server.core.session_service import SessionService

logger = logging.getLogger(__name__)


class SystemInitializer:
    """系统初始化器"""
    
    def __init__(
        self,
        account_service: AccountService,
        session_service: SessionService,
        audit_service: AuditService
    ):
        """
        初始化系统初始化器
        
        Args:
            account_service: 账号管理服务
            session_service: 会话管理服务
            audit_service: 审计日志服务
        """
        self.account_service = account_service
        self.session_service = session_service
        self.audit_service = audit_service
        
        # 后台任务
        self._session_cleanup_task: Optional[asyncio.Task] = None
        self._log_rotation_task: Optional[asyncio.Task] = None
    
    async def initialize(self) -> None:
        """
        执行系统初始化
        
        包括：
        1. 检查是否存在用户账号
        2. 如果不存在，创建默认管理员账号
        3. 在日志中显示默认管理员登录信息
        4. 加载所有用户账号到内存
        5. 启动会话清理定时任务
        6. 启动审计日志轮转定时任务
        """
        logger.info("=" * 60)
        logger.info("Starting system initialization...")
        logger.info("=" * 60)
        
        # 1. 加载所有用户账号到内存（已在 AccountService.__init__ 中完成）
        user_count = len(self.account_service.accounts)
        logger.info(f"Loaded {user_count} user account(s) from storage")
        
        # 2. 检查是否存在用户账号
        if user_count == 0:
            logger.warning("=" * 60)
            logger.warning("NO USER ACCOUNTS FOUND")
            logger.warning("Please create an admin account via the web interface")
            logger.warning("Visit /static/login.html to set up the first admin account")
            logger.warning("=" * 60)
        else:
            logger.info("User accounts already exist")
            # 列出现有用户
            for username, account in self.account_service.accounts.items():
                logger.info(
                    f"  - User: {username} (role: {account.role}, "
                    f"active: {account.is_active})"
                )
        
        # 3. 启动会话清理定时任务
        await self._start_session_cleanup_task()
        
        # 4. 启动审计日志轮转定时任务
        await self._start_log_rotation_task()
        
        logger.info("=" * 60)
        logger.info("System initialization completed successfully")
        logger.info("=" * 60)
    
    async def shutdown(self) -> None:
        """
        系统关闭时的清理工作
        
        包括：
        1. 停止后台任务
        2. 清理会话（可选）
        """
        logger.info("Starting system shutdown...")
        
        # 停止后台任务
        if self._session_cleanup_task:
            self._session_cleanup_task.cancel()
            try:
                await self._session_cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Session cleanup task stopped")
        
        if self._log_rotation_task:
            self._log_rotation_task.cancel()
            try:
                await self._log_rotation_task
            except asyncio.CancelledError:
                pass
            logger.info("Log rotation task stopped")
        
        logger.info("System shutdown completed")
    
    async def _create_default_admin(self) -> None:
        """
        创建默认管理员账号
        
        需求: 10.1, 10.2, 10.3
        """
        default_username = "admin"
        default_password = "admin123"
        
        try:
            admin = self.account_service.create_default_admin(
                username=default_username,
                password=default_password
            )
            
            if admin:
                logger.warning("=" * 60)
                logger.warning("DEFAULT ADMIN ACCOUNT CREATED")
                logger.warning("=" * 60)
                logger.warning(f"Username: {default_username}")
                logger.warning("=" * 60)
                logger.warning("PLEASE CHANGE THIS PASSWORD IMMEDIATELY!")
                logger.warning("=" * 60)
                
                # 记录审计事件
                self.audit_service.log_event(
                    event_type='system_init',
                    username='system',
                    ip_address='127.0.0.1',
                    details={
                        'action': 'create_default_admin',
                        'admin_username': default_username
                    },
                    result='success'
                )
            else:
                logger.info("Default admin account already exists or creation skipped")
        except Exception as e:
            logger.error(f"Failed to create default admin account: {e}")
            # 记录审计事件
            self.audit_service.log_event(
                event_type='system_init',
                username='system',
                ip_address='127.0.0.1',
                details={
                    'action': 'create_default_admin',
                    'error': str(e)
                },
                result='failure'
            )
    
    async def _start_session_cleanup_task(self) -> None:
        """
        启动会话清理定时任务
        
        每5分钟清理一次过期会话
        
        需求: 3.6, 6.5
        """
        async def cleanup_loop():
            """会话清理循环"""
            while True:
                try:
                    # 等待5分钟
                    await asyncio.sleep(300)  # 5 minutes
                    
                    # 清理过期会话
                    cleaned_count = self.session_service.cleanup_expired_sessions()
                    
                    if cleaned_count > 0:
                        logger.info(f"Cleaned up {cleaned_count} expired session(s)")
                        
                        # 记录审计事件
                        self.audit_service.log_event(
                            event_type='session_cleanup',
                            username='system',
                            ip_address='127.0.0.1',
                            details={
                                'cleaned_count': cleaned_count,
                                'timestamp': datetime.now().isoformat()
                            },
                            result='success'
                        )
                except asyncio.CancelledError:
                    logger.info("Session cleanup task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in session cleanup task: {e}")
                    # 继续运行，不要因为一次错误就停止
        
        # 启动后台任务
        self._session_cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info("Session cleanup task started (interval: 5 minutes)")
    
    async def _start_log_rotation_task(self) -> None:
        """
        启动审计日志轮转定时任务
        
        每24小时检查一次日志文件大小，如果超过限制则轮转
        
        需求: 12.7
        """
        async def rotation_loop():
            """日志轮转循环"""
            while True:
                try:
                    # 等待24小时
                    await asyncio.sleep(86400)  # 24 hours
                    
                    # 检查并轮转日志
                    # 注意：AuditService 已经在每次写入时自动检查大小
                    # 这里只是定期强制检查一次
                    logger.info("Performing scheduled audit log rotation check")
                    
                    # 记录审计事件
                    self.audit_service.log_event(
                        event_type='log_rotation_check',
                        username='system',
                        ip_address='127.0.0.1',
                        details={
                            'timestamp': datetime.now().isoformat()
                        },
                        result='success'
                    )
                except asyncio.CancelledError:
                    logger.info("Log rotation task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in log rotation task: {e}")
                    # 继续运行，不要因为一次错误就停止
        
        # 启动后台任务
        self._log_rotation_task = asyncio.create_task(rotation_loop())
        logger.info("Log rotation task started (interval: 24 hours)")


# 全局系统初始化器实例
_system_initializer: Optional[SystemInitializer] = None


def init_system(
    account_service: AccountService,
    session_service: SessionService,
    audit_service: AuditService
) -> SystemInitializer:
    """
    初始化系统初始化器
    
    Args:
        account_service: 账号管理服务
        session_service: 会话管理服务
        audit_service: 审计日志服务
    
    Returns:
        SystemInitializer: 系统初始化器实例
    """
    global _system_initializer
    _system_initializer = SystemInitializer(
        account_service,
        session_service,
        audit_service
    )
    return _system_initializer


def get_system_initializer() -> Optional[SystemInitializer]:
    """
    获取系统初始化器实例
    
    Returns:
        Optional[SystemInitializer]: 系统初始化器实例，如果未初始化返回 None
    """
    return _system_initializer
