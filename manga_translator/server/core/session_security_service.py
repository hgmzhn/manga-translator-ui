"""
Session Security Service

This module provides session ownership and access control functionality.

Security Features:
- UUID v4 token generation (128-bit random, cryptographically secure)
- Anti-enumeration protection via rate limiting
- Access attempt logging for audit trails
- Ownership-based access control
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from manga_translator.server.core.permission_service_v2 import EnhancedPermissionService

# Flask imports removed - using FastAPI now
from manga_translator.server.models.session_models import (
    SessionAccessAttempt,
    SessionOwnership,
)
from manga_translator.server.repositories.permission_repository import (
    PermissionRepository,
)
from manga_translator.server.repositories.session_repository import SessionRepository
from manga_translator.server_paths import SERVER_DATA_DIR


class SessionSecurityService:
    """
    Service for managing session security and ownership.
    
    Security Features:
    - UUID v4 tokens: 128-bit random identifiers (2^122 possible values)
    - Rate limiting: Prevents brute-force enumeration attacks
    - Access logging: Tracks all access attempts for audit
    - Ownership validation: Ensures users can only access their own sessions
    """
    
    # Rate limiting configuration
    MAX_FAILED_ATTEMPTS = 10  # Maximum failed attempts per user
    RATE_LIMIT_WINDOW = timedelta(minutes=5)  # Time window for rate limiting
    
    def __init__(self, data_dir: str = None):
        """
        Initialize the session security service.
        
        Args:
            data_dir: Directory for data storage
        """
        self.repository = SessionRepository(data_dir)
        
        # Initialize permission repository with file path
        if data_dir is None:
            data_dir = str(SERVER_DATA_DIR)
        
        permissions_file = str(Path(data_dir) / 'permissions.json')
        permission_repo = PermissionRepository(permissions_file)
        self.permission_service = EnhancedPermissionService(permission_repo)
        
        # Track failed attempts for rate limiting
        self._failed_attempts: Dict[str, List[datetime]] = {}
    
    def create_session(self, user_id: str, metadata: Optional[Dict[str, Any]] = None) -> SessionOwnership:
        """
        Create a new session and bind it to a user.
        
        Args:
            user_id: ID of the user creating the session
            metadata: Optional metadata for the session
            
        Returns:
            Created session ownership object
            
        Validates: Requirements 35.1
        """
        session_token = SessionOwnership.generate_session_token()
        from datetime import timezone
        session = SessionOwnership(
            session_token=session_token,
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
            status="active",
            metadata=metadata or {}
        )
        
        return self.repository.create_session(session)
    
    def _is_admin(self, user_id: str) -> bool:
        """
        Check if a user is an admin by looking up their role.
        
        Args:
            user_id: User ID (username) to check
            
        Returns:
            True if user is admin, False otherwise
        """
        import json
        import os
        
        # Try to read accounts file to get user's role
        # 首先尝试项目根目录的 accounts.json
        possible_paths = [
            'accounts.json',  # 项目根目录
        ]
        
        if hasattr(self.repository, 'data_dir'):
            possible_paths.append(os.path.join(self.repository.data_dir, 'accounts.json'))
        
        for accounts_file in possible_paths:
            if os.path.exists(accounts_file):
                try:
                    with open(accounts_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        accounts_list = data.get('accounts', [])
                        
                        # 查找用户
                        for account in accounts_list:
                            if account.get('username') == user_id:
                                # 检查 role 是否为 admin
                                if account.get('role') == 'admin':
                                    return True
                                # 检查 group 是否为 admin
                                if account.get('group') == 'admin':
                                    return True
                                return False
                except Exception:
                    pass
        
        # Fallback to permission service
        return self.permission_service.is_admin(user_id)
    
    def _check_rate_limit(self, user_id: str) -> tuple[bool, Optional[str]]:
        """
        Check if user has exceeded rate limit for failed access attempts.
        
        This prevents enumeration attacks where an attacker tries to guess
        valid session tokens by making many requests.
        
        Args:
            user_id: User ID to check
            
        Returns:
            Tuple of (is_allowed, reason_if_denied)
            
        Validates: Requirements 35.9 (anti-enumeration)
        """
        from datetime import timezone
        now = datetime.now(timezone.utc)
        
        # Clean up old attempts outside the window
        if user_id in self._failed_attempts:
            cutoff = now - self.RATE_LIMIT_WINDOW
            self._failed_attempts[user_id] = [
                attempt for attempt in self._failed_attempts[user_id]
                if attempt > cutoff
            ]
        
        # Check if user has exceeded limit
        failed_count = len(self._failed_attempts.get(user_id, []))
        if failed_count >= self.MAX_FAILED_ATTEMPTS:
            return False, "Rate limit exceeded. Too many failed attempts. Try again later."
        
        return True, None
    
    def _record_failed_attempt(self, user_id: str) -> None:
        """
        Record a failed access attempt for rate limiting.
        
        Args:
            user_id: User ID that failed
        """
        if user_id not in self._failed_attempts:
            self._failed_attempts[user_id] = []
        from datetime import timezone
        self._failed_attempts[user_id].append(datetime.now(timezone.utc))
    
    def _clear_failed_attempts(self, user_id: str) -> None:
        """
        Clear failed attempts for a user (on successful access).
        
        Args:
            user_id: User ID to clear
        """
        if user_id in self._failed_attempts:
            self._failed_attempts[user_id] = []
    
    def validate_session_token_format(self, token: str) -> bool:
        """
        Validate that a token is a properly formatted UUID v4.
        
        This prevents processing of obviously invalid tokens and
        provides early rejection of enumeration attempts.
        
        Args:
            token: Token to validate
            
        Returns:
            True if valid UUID v4 format, False otherwise
            
        Validates: Requirements 35.9
        """
        try:
            uuid_obj = uuid.UUID(token, version=4)
            # Verify it's actually version 4
            return uuid_obj.version == 4 and str(uuid_obj) == token
        except (ValueError, AttributeError):
            return False
    
    def check_session_ownership(
        self,
        session_token: str,
        user_id: str,
        action: str = "view"
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a user has permission to access a session.
        
        Security features:
        - Rate limiting to prevent enumeration attacks
        - Token format validation
        - Ownership-based access control
        
        Args:
            session_token: Session token to check
            user_id: User ID attempting access
            action: Action being attempted (view, edit, delete, export)
            
        Returns:
            Tuple of (is_allowed, reason_if_denied)
            
        Validates: Requirements 35.2, 35.3, 35.4, 35.5, 35.6, 35.9
        """
        # Check rate limit first (anti-enumeration)
        rate_ok, rate_reason = self._check_rate_limit(user_id)
        if not rate_ok:
            return False, rate_reason
        
        # Validate token format (reject obviously invalid tokens)
        if not self.validate_session_token_format(session_token):
            self._record_failed_attempt(user_id)
            return False, "Invalid session token format"
        
        # Get session ownership
        session = self.repository.get_session(session_token)
        
        if not session:
            self._record_failed_attempt(user_id)
            return False, "Session not found"
        
        # Check if user is admin
        is_admin = self._is_admin(user_id)
        
        # Admins can access all sessions
        if is_admin:
            self._clear_failed_attempts(user_id)
            return True, None
        
        # Regular users can only access their own sessions
        if session.user_id == user_id:
            self._clear_failed_attempts(user_id)
            return True, None
        
        # Access denied - record failed attempt
        self._record_failed_attempt(user_id)
        return False, "Access denied: You do not own this session"
    
    def log_access_attempt(
        self,
        session_token: str,
        user_id: str,
        action: str,
        granted: bool,
        reason: Optional[str] = None
    ) -> None:
        """
        Log a session access attempt for audit purposes.
        
        Args:
            session_token: Session being accessed
            user_id: User attempting access
            action: Action being attempted
            granted: Whether access was granted
            reason: Reason for denial if not granted
            
        Validates: Requirements 35.8
        """
        from datetime import timezone
        attempt = SessionAccessAttempt(
            session_token=session_token,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            action=action,
            granted=granted,
            reason=reason
        )
        self.repository.log_access_attempt(attempt)
    
    def get_user_sessions(self, user_id: str) -> List[SessionOwnership]:
        """
        Get all sessions owned by a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of sessions owned by the user
            
        Validates: Requirements 35.2
        """
        return self.repository.get_user_sessions(user_id)
    
    def get_all_sessions(self, requesting_user_id: str) -> Optional[List[SessionOwnership]]:
        """
        Get all sessions (admin only).
        
        Args:
            requesting_user_id: User requesting all sessions
            
        Returns:
            List of all sessions if user is admin, None otherwise
            
        Validates: Requirements 35.6
        """
        if not self._is_admin(requesting_user_id):
            return None
        
        return self.repository.get_all_sessions()
    
    def update_session_status(self, session_token: str, status: str) -> bool:
        """
        Update session status.
        
        Args:
            session_token: Session token
            status: New status (active, completed, failed)
            
        Returns:
            True if updated, False if session not found
        """
        return self.repository.update_session_status(session_token, status)
    
    def delete_session(
        self,
        session_token: str,
        user_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Delete a session (with ownership check).
        
        Args:
            session_token: Session to delete
            user_id: User attempting deletion
            
        Returns:
            Tuple of (success, error_message)
            
        Validates: Requirements 35.7
        """
        # Check ownership
        allowed, reason = self.check_session_ownership(session_token, user_id, "delete")
        
        if not allowed:
            self.log_access_attempt(session_token, user_id, "delete", False, reason)
            return False, reason
        
        # Delete session
        success = self.repository.delete_session(session_token)
        self.log_access_attempt(session_token, user_id, "delete", success)
        
        return success, None if success else "Session not found"
    
    def get_unauthorized_attempts(self, requesting_user_id: str, limit: int = 100) -> Optional[List[SessionAccessAttempt]]:
        """
        Get unauthorized access attempts (admin only).
        
        Args:
            requesting_user_id: User requesting the log
            limit: Maximum number of attempts to return
            
        Returns:
            List of unauthorized attempts if user is admin, None otherwise
            
        Validates: Requirements 35.8
        """
        if not self._is_admin(requesting_user_id):
            return None
        
        return self.repository.get_unauthorized_attempts(limit)
    
    # Note: require_session_ownership decorator removed - using FastAPI dependencies instead
    # Session ownership checks are now done directly in route handlers
