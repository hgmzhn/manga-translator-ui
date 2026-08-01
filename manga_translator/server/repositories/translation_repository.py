"""
Repository for translation history management.
优化：按用户分片存储，提高多用户场景下的性能。
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from manga_translator.server.core.download_ticket_service import resolve_path_within
from manga_translator.server.models import TranslationResult


class TranslationRepository:
    """
    Repository for managing translation history.
    使用按用户分片的存储策略，每个用户一个独立的 JSON 文件。
    同时维护一个索引文件用于快速查找 session_token。
    """
    
    def __init__(self, base_path: str):
        """
        初始化仓库。
        
        Args:
            base_path: 原始的单文件路径，会转换为目录路径
        """
        # 将原来的文件路径转换为目录
        self.base_dir = Path(base_path).parent / 'history'
        self.index_file = self.base_dir / '_index.json'
        self._lock = threading.RLock()
        self._ensure_dirs()
        self._migrate_old_data(base_path)
    
    def _ensure_dirs(self) -> None:
        """确保目录存在"""
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def _migrate_old_data(self, old_file: str) -> None:
        """迁移旧的单文件数据到新的分片结构"""
        old_path = Path(old_file)
        if not old_path.exists():
            return
        
        try:
            with open(old_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            
            sessions = old_data.get('sessions', [])
            if not sessions:
                return
            
            # 按用户分组迁移
            for session in sessions:
                user_id = session.get('user_id', 'unknown')
                self._add_to_user_file(user_id, session)
            
            # 备份并删除旧文件
            backup_path = old_path.with_suffix('.json.migrated')
            old_path.rename(backup_path)
            
        except Exception as e:
            print(f"Migration warning: {e}")
    
    def _get_user_file(self, user_id: str) -> Path:
        """获取用户的历史文件路径"""
        # 使用安全的文件名
        safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in user_id)
        return resolve_path_within(self.base_dir, self.base_dir / f'{safe_name}.json')
    
    def _read_user_data(self, user_id: str) -> Dict[str, Any]:
        """读取用户数据"""
        user_file = self._get_user_file(user_id)
        with self._lock:
            if not user_file.exists():
                return {'sessions': [], 'last_updated': None}
            try:
                with open(user_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {'sessions': [], 'last_updated': None}
    
    def _write_user_data(self, user_id: str, data: Dict[str, Any]) -> None:
        """写入用户数据"""
        user_file = self._get_user_file(user_id)
        data['last_updated'] = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            temp_path = user_file.with_suffix('.tmp')
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(temp_path, user_file)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink()
                raise
    
    def _add_to_user_file(self, user_id: str, session: dict) -> None:
        """添加会话到用户文件"""
        data = self._read_user_data(user_id)
        data['sessions'].append(session)
        self._write_user_data(user_id, data)
        self._update_index(session['session_token'], user_id)
    
    def _read_index(self) -> Dict[str, str]:
        """读取索引文件 (session_token -> user_id)"""
        with self._lock:
            if not self.index_file.exists():
                return {}
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
    
    def _write_index(self, index: Dict[str, str]) -> None:
        """写入索引文件"""
        with self._lock:
            temp_path = self.index_file.with_suffix('.tmp')
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(index, f, ensure_ascii=False)
                os.replace(temp_path, self.index_file)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink()
                raise
    
    def _update_index(self, session_token: str, user_id: str) -> None:
        """更新索引"""
        index = self._read_index()
        index[session_token] = user_id
        self._write_index(index)
    
    def _remove_from_index(self, session_token: str) -> None:
        """从索引中移除"""
        index = self._read_index()
        if session_token in index:
            del index[session_token]
            self._write_index(index)
    
    def add_session(self, result: TranslationResult) -> None:
        """添加翻译会话到历史"""
        self._add_to_user_file(result.user_id, result.to_dict())
    
    def get_user_sessions(self, user_id: str) -> List[dict]:
        """获取指定用户的所有会话"""
        data = self._read_user_data(user_id)
        return data.get('sessions', [])
    
    def get_session_by_token(self, session_token: str) -> Optional[dict]:
        """通过 token 获取会话"""
        # 先查索引
        index = self._read_index()
        user_id = index.get(session_token)
        
        if user_id:
            data = self._read_user_data(user_id)
            for session in data.get('sessions', []):
                if session.get('session_token') == session_token:
                    return session
        
        # 索引未命中，遍历所有用户文件（兼容旧数据）
        for user_file in self.base_dir.glob('*.json'):
            if user_file.name.startswith('_'):
                continue
            try:
                with open(user_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for session in data.get('sessions', []):
                    if session.get('session_token') == session_token:
                        # 更新索引
                        self._update_index(session_token, session.get('user_id', 'unknown'))
                        return session
            except Exception:
                continue
        
        return None
    
    def get_all_sessions(self) -> List[dict]:
        """获取所有会话（管理员用）"""
        all_sessions = []
        for user_file in self.base_dir.glob('*.json'):
            if user_file.name.startswith('_'):
                continue
            try:
                with open(user_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                all_sessions.extend(data.get('sessions', []))
            except Exception:
                continue
        return all_sessions
    
    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        # 遍历所有用户文件查找并删除
        for user_file in self.base_dir.glob('*.json'):
            if user_file.name.startswith('_'):
                continue
            try:
                with open(user_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                original_len = len(data.get('sessions', []))
                sessions = [s for s in data.get('sessions', []) if s.get('id') != session_id]
                
                if len(sessions) < original_len:
                    # 找到并删除了
                    deleted_session = next(
                        (s for s in data.get('sessions', []) if s.get('id') == session_id), 
                        None
                    )
                    data['sessions'] = sessions
                    user_id = user_file.stem
                    self._write_user_data(user_id, data)
                    
                    if deleted_session:
                        self._remove_from_index(deleted_session.get('session_token', ''))
                    return True
            except Exception:
                continue
        return False
    
    def update_session(self, session_id: str, updates: dict) -> bool:
        """更新会话"""
        for user_file in self.base_dir.glob('*.json'):
            if user_file.name.startswith('_'):
                continue
            try:
                with open(user_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for session in data.get('sessions', []):
                    if session.get('id') == session_id:
                        session.update(updates)
                        user_id = user_file.stem
                        self._write_user_data(user_id, data)
                        return True
            except Exception:
                continue
        return False
    
    def search_sessions(self, user_id: Optional[str] = None, 
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> List[dict]:
        """搜索会话"""
        def filter_func(session):
            if start_date and session.get('timestamp', '') < start_date:
                return False
            if end_date and session.get('timestamp', '') > end_date:
                return False
            return True
        
        if user_id:
            # 只查询指定用户
            sessions = self.get_user_sessions(user_id)
            return [s for s in sessions if filter_func(s)]
        else:
            # 查询所有用户
            all_sessions = self.get_all_sessions()
            return [s for s in all_sessions if filter_func(s)]
