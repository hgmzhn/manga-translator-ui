"""
Session Repository

This module provides data access for session ownership and access control.
"""

import json
import os
from typing import List, Optional

from manga_translator.server.models.session_models import (
    SessionAccessAttempt,
    SessionOwnership,
)
from manga_translator.server_paths import SERVER_DATA_DIR


class SessionRepository:
    """Repository for managing session ownership data."""
    
    def __init__(self, data_dir: str = None):
        """
        Initialize the session repository.
        
        Args:
            data_dir: Directory for data storage
        """
        if data_dir is None:
            data_dir = str(SERVER_DATA_DIR)
        
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.sessions_file = os.path.join(self.data_dir, 'sessions.json')
        self.access_log_file = os.path.join(self.data_dir, 'session_access_log.json')
        self._ensure_files_exist()
    
    def _ensure_files_exist(self):
        """Ensure required data files exist."""
        if not os.path.exists(self.sessions_file):
            with open(self.sessions_file, 'w', encoding='utf-8') as f:
                json.dump({'sessions': {}}, f, indent=2)
        if not os.path.exists(self.access_log_file):
            with open(self.access_log_file, 'w', encoding='utf-8') as f:
                json.dump({'access_attempts': []}, f, indent=2)
    
    def _read_json(self, file_path: str) -> dict:
        """Read JSON file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _write_json(self, file_path: str, data: dict) -> None:
        """Write JSON file."""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def create_session(self, session: SessionOwnership) -> SessionOwnership:
        """
        Create a new session ownership record.
        
        Args:
            session: Session ownership object
            
        Returns:
            The created session
        """
        data = self._read_json(self.sessions_file)
        data['sessions'][session.session_token] = session.to_dict()
        self._write_json(self.sessions_file, data)
        return session
    
    def get_session(self, session_token: str) -> Optional[SessionOwnership]:
        """
        Get session ownership by token.
        
        Args:
            session_token: Session token to look up
            
        Returns:
            SessionOwnership if found, None otherwise
        """
        data = self._read_json(self.sessions_file)
        session_data = data['sessions'].get(session_token)
        if session_data:
            return SessionOwnership.from_dict(session_data)
        return None
    
    def get_user_sessions(self, user_id: str) -> List[SessionOwnership]:
        """
        Get all sessions owned by a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of sessions owned by the user
        """
        data = self._read_json(self.sessions_file)
        sessions_data = data.get('sessions', {})
        sessions = []
        # 兼容 list 和 dict 两种格式
        if isinstance(sessions_data, list):
            for session_data in sessions_data:
                if session_data.get('user_id') == user_id:
                    sessions.append(SessionOwnership.from_dict(session_data))
        else:
            for session_data in sessions_data.values():
                if session_data.get('user_id') == user_id:
                    sessions.append(SessionOwnership.from_dict(session_data))
        return sessions
    
    def get_all_sessions(self) -> List[SessionOwnership]:
        """
        Get all sessions (admin only).
        
        Returns:
            List of all sessions
        """
        data = self._read_json(self.sessions_file)
        sessions_data = data.get('sessions', {})
        # 兼容 list 和 dict 两种格式
        if isinstance(sessions_data, list):
            return [SessionOwnership.from_dict(s) for s in sessions_data]
        else:
            return [SessionOwnership.from_dict(s) for s in sessions_data.values()]
    
    def update_session_status(self, session_token: str, status: str) -> bool:
        """
        Update session status.
        
        Args:
            session_token: Session token
            status: New status
            
        Returns:
            True if updated, False if session not found
        """
        data = self._read_json(self.sessions_file)
        if session_token in data['sessions']:
            data['sessions'][session_token]['status'] = status
            self._write_json(self.sessions_file, data)
            return True
        return False
    
    def delete_session(self, session_token: str) -> bool:
        """
        Delete a session record.
        
        Args:
            session_token: Session token to delete
            
        Returns:
            True if deleted, False if not found
        """
        data = self._read_json(self.sessions_file)
        if session_token in data['sessions']:
            del data['sessions'][session_token]
            self._write_json(self.sessions_file, data)
            return True
        return False
    
    def log_access_attempt(self, attempt: SessionAccessAttempt) -> None:
        """
        Log a session access attempt.
        
        Args:
            attempt: Access attempt to log
        """
        data = self._read_json(self.access_log_file)
        data['access_attempts'].append(attempt.to_dict())
        
        # Keep only last 10000 attempts to prevent file from growing too large
        if len(data['access_attempts']) > 10000:
            data['access_attempts'] = data['access_attempts'][-10000:]
        
        self._write_json(self.access_log_file, data)
    
    def get_access_attempts(
        self,
        session_token: Optional[str] = None,
        user_id: Optional[str] = None,
        granted: Optional[bool] = None,
        limit: int = 100
    ) -> List[SessionAccessAttempt]:
        """
        Get session access attempts with optional filtering.
        
        Args:
            session_token: Filter by session token
            user_id: Filter by user ID
            granted: Filter by granted status
            limit: Maximum number of attempts to return
            
        Returns:
            List of access attempts
        """
        data = self._read_json(self.access_log_file)
        attempts = []
        
        for attempt_data in reversed(data['access_attempts']):
            if session_token and attempt_data['session_token'] != session_token:
                continue
            if user_id and attempt_data['user_id'] != user_id:
                continue
            if granted is not None and attempt_data['granted'] != granted:
                continue
            
            attempts.append(SessionAccessAttempt.from_dict(attempt_data))
            
            if len(attempts) >= limit:
                break
        
        return attempts
    
    def get_unauthorized_attempts(self, limit: int = 100) -> List[SessionAccessAttempt]:
        """
        Get unauthorized access attempts.
        
        Args:
            limit: Maximum number of attempts to return
            
        Returns:
            List of unauthorized access attempts
        """
        return self.get_access_attempts(granted=False, limit=limit)
