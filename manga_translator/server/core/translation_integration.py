"""
翻译流程集成模块

将权限检查、配额管理、历史记录和日志记录集成到翻译流程中。

需求: 1.2, 3.1, 27.2, 31.2
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class TranslationIntegrationService:
    """
    翻译流程集成服务
    
    负责在翻译开始、进行和完成时协调各个服务：
    - 权限检查
    - 配额检查和更新
    - 历史记录保存
    - 日志记录
    
    需求: 1.2, 3.1, 27.2, 31.2
    """
    
    def __init__(
        self,
        permission_service=None,
        quota_service=None,
        history_service=None,
        log_service=None
    ):
        """
        初始化翻译集成服务
        
        Args:
            permission_service: 权限服务
            quota_service: 配额服务
            history_service: 历史服务
            log_service: 日志服务
        """
        self.permission_service = permission_service
        self.quota_service = quota_service
        self.history_service = history_service
        self.log_service = log_service
        
        logger.info("TranslationIntegrationService initialized")
    
    def check_translation_permission(
        self,
        username: str,
        translator: str
    ) -> Tuple[bool, Optional[str]]:
        """
        检查翻译权限
        
        Args:
            username: 用户名
            translator: 翻译器名称
        
        Returns:
            Tuple[bool, Optional[str]]: (是否允许, 错误消息)
        
        需求: 1.2
        """
        if not self.permission_service:
            logger.warning("Permission service not available, allowing by default")
            return True, None
        
        try:
            has_permission = self.permission_service.check_translator_permission(
                username, translator
            )
            
            if not has_permission:
                permissions = self.permission_service.get_user_permissions(username)
                allowed = permissions.allowed_translators if permissions else []
                error_msg = f"您没有权限使用翻译器 '{translator}'。允许的翻译器: {allowed}"
                logger.warning(f"Permission denied for user {username}: translator '{translator}' is not allowed. Allowed translators: {allowed}")
                return False, error_msg
            
            logger.debug(f"Permission check passed for user {username}, translator {translator}")
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking permission: {e}")
            return False, f"权限检查失败: {str(e)}"
    
    def check_quota_before_translation(
        self,
        username: str,
        image_count: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """
        翻译前检查配额
        
        Args:
            username: 用户名
            image_count: 要翻译的图片数量
        
        Returns:
            Tuple[bool, Optional[str]]: (是否允许, 错误消息)
        
        需求: 27.2
        """
        if not self.quota_service:
            logger.warning("Quota service not available, allowing by default")
            return True, None
        
        try:
            # 检查每日配额
            allowed, error_msg = self.quota_service.check_daily_quota(username, image_count)
            
            if not allowed:
                return False, error_msg
            
            logger.debug(f"Quota check passed for user {username}, count {image_count}")
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking quota: {e}")
            return False, f"配额检查失败: {str(e)}"
    
    def on_translation_start(
        self,
        session_token: str,
        username: str,
        translator: str,
        config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        翻译开始时的处理
        
        Args:
            session_token: 会话令牌
            username: 用户名
            translator: 翻译器名称
            config: 翻译配置
        
        Returns:
            bool: 是否成功
        
        需求: 31.2
        """
        try:
            # 记录翻译开始日志
            if self.log_service:
                self.log_service.log_translation_event(
                    session_token=session_token,
                    user_id=username,
                    event_type='translation_start',
                    message=f'Starting translation with translator: {translator}',
                    level='info',
                    details={
                        'translator': translator,
                        'config': self._sanitize_config(config) if config else None,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                )
            
            logger.info(f"Translation started: user={username}, session={session_token}, translator={translator}")
            return True
            
        except Exception as e:
            logger.error(f"Error in on_translation_start: {e}")
            return False
    
    def on_translation_progress(
        self,
        session_token: str,
        username: str,
        progress: float,
        message: str = ""
    ) -> bool:
        """
        翻译进行中的处理
        
        Args:
            session_token: 会话令牌
            username: 用户名
            progress: 进度 (0-100)
            message: 进度消息
        
        Returns:
            bool: 是否成功
        
        需求: 31.2
        """
        try:
            # 记录翻译进度日志
            if self.log_service:
                self.log_service.log_translation_event(
                    session_token=session_token,
                    user_id=username,
                    event_type='translation_progress',
                    message=message or f'Translation progress: {progress:.1f}%',
                    level='info',
                    details={
                        'progress': progress,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                )
            
            logger.debug(f"Translation progress: user={username}, session={session_token}, progress={progress}%")
            return True
            
        except Exception as e:
            logger.error(f"Error in on_translation_progress: {e}")
            return False
    
    def on_translation_complete(
        self,
        session_token: str,
        username: str,
        result_files: list,
        image_count: int = 1,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        翻译完成时的处理
        
        Args:
            session_token: 会话令牌
            username: 用户名
            result_files: 结果文件列表
            image_count: 成功翻译的图片数量
            metadata: 元数据
        
        Returns:
            bool: 是否成功
        
        需求: 3.1, 27.2, 31.2
        """
        try:
            # 1. 更新配额（仅在成功时）
            if self.quota_service and image_count > 0:
                self.quota_service.increment_quota_usage(username, image_count)
                logger.info(f"Quota updated for user {username}: +{image_count}")
            
            # 2. 保存翻译结果到历史记录
            if self.history_service and result_files:
                try:
                    self.history_service.save_translation_result(
                        user_id=username,
                        session_token=session_token,
                        files=result_files,
                        metadata=metadata
                    )
                    logger.info(f"Translation result saved: session={session_token}, files={len(result_files)}")
                except Exception as e:
                    logger.error(f"Failed to save translation result: {e}")
            
            # 3. 记录翻译完成日志
            if self.log_service:
                self.log_service.log_translation_event(
                    session_token=session_token,
                    user_id=username,
                    event_type='translation_complete',
                    message=f'Translation complete: {image_count} images',
                    level='info',
                    details={
                        'image_count': image_count,
                        'file_count': len(result_files) if result_files else 0,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                )
            
            logger.info(f"Translation completed: user={username}, session={session_token}, images={image_count}")
            return True
            
        except Exception as e:
            logger.error(f"Error in on_translation_complete: {e}")
            return False
    
    def on_translation_error(
        self,
        session_token: str,
        username: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        翻译错误时的处理
        
        Args:
            session_token: 会话令牌
            username: 用户名
            error_message: 错误消息
            error_details: 错误详情
        
        Returns:
            bool: 是否成功
        
        需求: 31.2
        """
        try:
            # 记录翻译错误日志
            if self.log_service:
                self.log_service.log_translation_event(
                    session_token=session_token,
                    user_id=username,
                    event_type='translation_error',
                    message=error_message,
                    level='error',
                    details={
                        'error': error_message,
                        'details': error_details,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                )
            
            logger.error(f"Translation error: user={username}, session={session_token}, error={error_message}")
            return True
            
        except Exception as e:
            logger.error(f"Error in on_translation_error: {e}")
            return False
    
    def _sanitize_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        清理配置，移除敏感信息
        
        Args:
            config: 原始配置
        
        Returns:
            Dict[str, Any]: 清理后的配置
        """
        if not config:
            return {}
        
        # 复制配置
        sanitized = dict(config)
        
        # 移除敏感字段
        sensitive_keys = ['api_key', 'api_secret', 'password', 'token', 'key']
        
        def remove_sensitive(d):
            if isinstance(d, dict):
                return {
                    k: '***' if any(s in k.lower() for s in sensitive_keys) else remove_sensitive(v)
                    for k, v in d.items()
                }
            elif isinstance(d, list):
                return [remove_sensitive(item) for item in d]
            return d
        
        return remove_sensitive(sanitized)


# 全局实例
_integration_service: Optional[TranslationIntegrationService] = None


def init_translation_integration(
    permission_service=None,
    quota_service=None,
    history_service=None,
    log_service=None
) -> TranslationIntegrationService:
    """
    初始化翻译集成服务
    
    Args:
        permission_service: 权限服务
        quota_service: 配额服务
        history_service: 历史服务
        log_service: 日志服务
    
    Returns:
        TranslationIntegrationService: 集成服务实例
    """
    global _integration_service
    _integration_service = TranslationIntegrationService(
        permission_service=permission_service,
        quota_service=quota_service,
        history_service=history_service,
        log_service=log_service
    )
    logger.info("Translation integration service initialized")
    return _integration_service


def get_translation_integration() -> Optional[TranslationIntegrationService]:
    """
    获取翻译集成服务实例
    
    Returns:
        Optional[TranslationIntegrationService]: 集成服务实例
    """
    return _integration_service
