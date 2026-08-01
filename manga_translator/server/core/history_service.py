"""
History Management Service for translation results.

This service handles:
- Saving translation results with session tokens
- Retrieving user translation history
- Managing translation result metadata
- Session token generation (UUID v4)
"""

import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from manga_translator.server.core.download_ticket_service import resolve_path_within
from manga_translator.server.models import TranslationResult
from manga_translator.server.repositories.translation_repository import (
    TranslationRepository,
)

logger = logging.getLogger(__name__)


class HistoryManagementService:
    """
    Service for managing translation history and results.
    
    Validates: Requirements 3.1, 3.5, 9.1-9.5, 35.9
    """
    
    def __init__(self, result_directory: str, translation_repo: Optional[TranslationRepository] = None):
        """
        Initialize the history management service.
        
        Args:
            result_directory: Base directory for storing translation results
            translation_repo: Optional translation repository instance
        """
        self.result_directory = Path(os.path.normcase(os.path.realpath(result_directory)))
        self.translation_repo = translation_repo or TranslationRepository(
            os.path.join('manga_translator', 'server', 'data', 'translation_history.json')
        )
        
        # Ensure result directory exists
        self.result_directory.mkdir(parents=True, exist_ok=True)
    
    def generate_session_token(self) -> str:
        """
        Generate a unique session token using UUID v4.
        
        Returns:
            A unique session token string
            
        Validates: Requirement 35.9 - UUID v4 for unpredictable tokens
        """
        return str(uuid.uuid4())

    def _resolve_session_directory(self, result_path: str | Path) -> Optional[Path]:
        try:
            session_dir = resolve_path_within(self.result_directory, result_path)
        except ValueError:
            logger.warning("Rejected history path outside result directory: %s", result_path)
            return None
        return None if session_dir == self.result_directory else session_dir
    
    def save_translation_result(
        self,
        user_id: str,
        session_token: str,
        files: List[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> TranslationResult:
        """
        Save translation result to storage.
        
        Args:
            user_id: ID of the user who performed the translation
            session_token: Unique session token for this translation
            files: List of file paths to save
            metadata: Optional metadata dictionary
        
        Returns:
            TranslationResult object
            
        Validates: Requirements 3.1, 9.1-9.5
        """
        if not session_token or '/' in session_token or '\\' in session_token or '\x00' in session_token:
            raise ValueError("Invalid history session token")

        # Create session directory
        session_dir = resolve_path_within(
            self.result_directory,
            self.result_directory / session_token,
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Calculate total size and copy files
        total_size = 0
        saved_files = []
        
        for file_path in files:
            source_path = resolve_path_within(tempfile.gettempdir(), file_path)
            if source_path.is_file():
                file_size = source_path.stat().st_size
                total_size += file_size

                # Copy file to session directory
                dest_path = resolve_path_within(session_dir, session_dir / source_path.name)
                shutil.copy2(source_path, dest_path)
                saved_files.append(str(dest_path))
        
        # Create metadata file
        result_metadata = metadata or {}
        result_metadata.update({
            'user_id': user_id,
            'session_token': session_token,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'file_count': len(saved_files),
            'files': [os.path.basename(f) for f in saved_files]
        })
        
        metadata_path = session_dir / 'metadata.json'
        import json
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(result_metadata, f, indent=2, ensure_ascii=False)
        
        # Create TranslationResult object
        result = TranslationResult.create(
            user_id=user_id,
            session_token=session_token,
            file_count=len(saved_files),
            total_size=total_size,
            result_path=str(session_dir),
            metadata=result_metadata,
            status='completed'
        )
        
        # Save to repository
        self.translation_repo.add_session(result)
        
        return result
    
    def get_user_history(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[TranslationResult]:
        """
        Get translation history for a specific user.
        
        Args:
            user_id: ID of the user
            filters: Optional filters (start_date, end_date, status)
        
        Returns:
            List of TranslationResult objects
            
        Validates: Requirement 3.2
        """
        filters = filters or {}
        
        # Get user sessions from repository
        sessions = self.translation_repo.search_sessions(
            user_id=user_id,
            start_date=filters.get('start_date'),
            end_date=filters.get('end_date')
        )
        
        # Apply status filter if provided
        if 'status' in filters:
            sessions = [s for s in sessions if s.get('status') == filters['status']]
        
        # Convert to TranslationResult objects
        results = [TranslationResult.from_dict(s) for s in sessions]
        
        # Sort by timestamp (newest first)
        results.sort(key=lambda x: x.timestamp, reverse=True)
        
        return results
    
    def get_all_history(
        self,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[TranslationResult]:
        """
        Get all translation history (admin only).
        
        Args:
            filters: Optional filters (user_id, start_date, end_date, status)
        
        Returns:
            List of TranslationResult objects
            
        Validates: Requirements 5.1-5.5
        """
        filters = filters or {}
        
        # Get all sessions from repository
        sessions = self.translation_repo.search_sessions(
            user_id=filters.get('user_id'),
            start_date=filters.get('start_date'),
            end_date=filters.get('end_date')
        )
        
        # Apply status filter if provided
        if 'status' in filters:
            sessions = [s for s in sessions if s.get('status') == filters['status']]
        
        # Convert to TranslationResult objects
        results = [TranslationResult.from_dict(s) for s in sessions]
        
        # Sort by timestamp (newest first)
        results.sort(key=lambda x: x.timestamp, reverse=True)
        
        return results
    
    def get_session_by_token(
        self,
        session_token: str,
        user_id: Optional[str] = None
    ) -> Optional[TranslationResult]:
        """
        Get a specific translation session by token.
        
        Args:
            session_token: Session token to look up
            user_id: Optional user ID for ownership verification
        
        Returns:
            TranslationResult object if found, None otherwise
            
        Validates: Requirements 3.2, 35.1-35.10
        """
        session_data = self.translation_repo.get_session_by_token(session_token)
        
        if not session_data:
            return None
        
        # Verify ownership if user_id is provided
        if user_id and session_data.get('user_id') != user_id:
            return None
        
        return TranslationResult.from_dict(session_data)
    
    def delete_session(
        self,
        session_token: str,
        user_id: Optional[str] = None
    ) -> bool:
        """
        Delete a translation session.
        
        Args:
            session_token: Session token to delete
            user_id: Optional user ID for ownership verification
        
        Returns:
            True if deleted successfully, False otherwise
        """
        # Get session to verify ownership
        session = self.get_session_by_token(session_token, user_id)
        
        if not session:
            return False
        
        # Delete files from filesystem
        session_dir = self._resolve_session_directory(session.result_path)
        if session_dir is not None and session_dir.exists():
            shutil.rmtree(session_dir)
        
        # Delete from repository
        return self.translation_repo.delete_session(session.id)
    
    def get_session_files(
        self,
        session_token: str,
        user_id: Optional[str] = None
    ) -> List[str]:
        """
        Get list of files for a session.
        
        Args:
            session_token: Session token
            user_id: Optional user ID for ownership verification
        
        Returns:
            List of file paths
        """
        import logging
        logger = logging.getLogger(__name__)
        
        session = self.get_session_by_token(session_token, user_id)
        
        if not session:
            logger.warning(f"get_session_files: session not found for token={session_token[:8]}, user_id={user_id}")
            return []
        
        session_dir = self._resolve_session_directory(session.result_path)
        if session_dir is None:
            return []
        logger.debug(f"get_session_files: session_dir={session_dir}, exists={session_dir.exists()}")
        
        if not session_dir.exists():
            logger.warning(f"get_session_files: session_dir does not exist: {session_dir}")
            return []
        
        # Get all image files (excluding metadata.json)
        files = []
        for file_path in session_dir.iterdir():
            if file_path.is_file() and file_path.name != 'metadata.json':
                files.append(str(file_path))
        
        return sorted(files)
    
    def update_session_status(
        self,
        session_token: str,
        status: str,
        user_id: Optional[str] = None
    ) -> bool:
        """
        Update the status of a translation session.
        
        Args:
            session_token: Session token
            status: New status (processing, completed, failed)
            user_id: Optional user ID for ownership verification
        
        Returns:
            True if updated successfully, False otherwise
        """
        session = self.get_session_by_token(session_token, user_id)
        
        if not session:
            return False
        
        return self.translation_repo.update_session(
            session.id,
            {'status': status}
        )
    
    def create_download_archive(
        self,
        session_token: str,
        user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a ZIP archive for a session's files.
        
        Args:
            session_token: Session token
            user_id: Optional user ID for ownership verification
        
        Returns:
            Path to the created ZIP file, or None if failed
            
        Validates: Requirement 3.4
        """
        # Get session files
        files = self.get_session_files(session_token, user_id)
        
        if not files:
            return None
        
        # Create temporary ZIP file
        temp_dir = tempfile.gettempdir()
        zip_path = resolve_path_within(
            temp_dir,
            os.path.join(temp_dir, f"history_{uuid.uuid4().hex}.zip"),
        )
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in files:
                    source_path = Path(file_path)
                    if source_path.is_file():
                        # Add file to ZIP with just the filename (no path)
                        zipf.write(source_path, source_path.name)

            return str(zip_path)
        
        except Exception as e:
            logger.error(f"Failed to create ZIP archive: {e}")
            return None
    
    def create_batch_download_archive(
        self,
        session_tokens: List[str],
        user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a ZIP archive for multiple sessions' files.
        
        Args:
            session_tokens: List of session tokens
            user_id: Optional user ID for ownership verification
        
        Returns:
            Path to the created ZIP file, or None if failed
            
        Validates: Requirements 4.2, 4.3
        """
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"Creating batch download for {len(session_tokens)} sessions, user_id={user_id}")
        
        # Create temporary ZIP file
        temp_dir = tempfile.gettempdir()
        zip_filename = (
            f"batch_download_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}.zip"
        )
        zip_path = resolve_path_within(temp_dir, os.path.join(temp_dir, zip_filename))
        
        try:
            total_files = 0
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for session_number, session_token in enumerate(session_tokens, 1):
                    logger.info(f"Processing session: {session_token}")

                    # Get session files
                    files = self.get_session_files(session_token, user_id)
                    logger.info(f"  Found {len(files)} files for session {session_token[:8]}")
                    
                    if not files:
                        logger.warning(f"  No files found for session {session_token[:8]}")
                        continue
                    
                    # Add files to ZIP with session token as folder
                    for file_path in files:
                        source_path = Path(file_path)
                        if source_path.is_file():
                            # Add file to ZIP with session token folder
                            arcname = os.path.join(f"session_{session_number}", source_path.name)
                            zipf.write(source_path, arcname)
                            total_files += 1
                            logger.info(f"  Added: {arcname}")
            
            logger.info(f"Batch ZIP created with {total_files} files: {zip_path}")
            return str(zip_path)
        
        except Exception as e:
            logger.error(f"Failed to create batch ZIP archive: {e}")
            return None
    
    def cleanup_temp_file(self, file_path: str) -> None:
        """
        Clean up a temporary file.
        
        Args:
            file_path: Path to the file to delete
        """
        import logging
        import time
        _logger = logging.getLogger(__name__)
        
        # 延迟删除，等待文件传输完成
        time.sleep(1)
        
        try:
            temp_path = resolve_path_within(tempfile.gettempdir(), file_path)
            if temp_path.is_file():
                temp_path.unlink()
                _logger.debug(f"Cleaned up temp file: {temp_path}")
        except Exception as e:
            _logger.warning(f"Failed to cleanup temp file {file_path}: {e}")
