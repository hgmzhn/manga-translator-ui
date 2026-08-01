"""
账号管理服务（AccountService）

管理用户账号的创建、查询、更新和删除。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import bcrypt

from manga_translator.server.core.models import UserAccount, UserPermissions
from manga_translator.server.core.persistence import atomic_write_json, load_json

logger = logging.getLogger(__name__)


class AccountService:
    """账号管理服务"""
    
    def __init__(self, accounts_file: str = "manga_translator/server/data/accounts.json"):
        """
        初始化账号管理服务
        
        Args:
            accounts_file: 账号存储文件路径
        """
        self.accounts_file = accounts_file
        self.accounts: Dict[str, UserAccount] = {}
        self._load_accounts()
    
    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        group: str = "default",
        permissions: Optional[UserPermissions] = None
    ) -> UserAccount:
        """
        创建新用户
        
        Args:
            username: 用户名
            password: 密码（明文）
            role: 角色（'admin' 或 'user'）
            group: 用户组名称（默认为 'default'）
            permissions: 用户权限（如果为 None，使用默认权限）
        
        Returns:
            UserAccount: 创建的用户账号
        
        Raises:
            ValueError: 如果用户名已存在、密码强度不足或角色无效
        """
        # 验证用户名唯一性
        if username in self.accounts:
            raise ValueError(f"用户名 '{username}' 已存在")
        
        # 验证密码强度（至少6个字符）
        if len(password) < 6:
            raise ValueError("密码长度必须至少为6个字符")
        
        # 验证角色
        if role not in ['admin', 'user']:
            raise ValueError(f"无效的角色: {role}")
        
        # 使用默认权限（如果未提供）
        if permissions is None:
            if role == 'admin':
                permissions = UserPermissions(
                    allowed_translators=["*"],
                    allowed_ocr=["*"],
                    allowed_colorizers=["*"],
                    allowed_renderers=["*"],
                    allowed_workflows=["*"],
                    allowed_parameters=["*"],
                    max_concurrent_tasks=10,
                    daily_quota=-1,
                    can_upload_files=True,
                    can_delete_files=True
                )
            else:
                # 普通用户默认继承用户组配置
                # allowed_translators/allowed_parameters 为空表示继承用户组
                # 用户级别的设置可以覆盖用户组（白名单解锁或黑名单禁用）
                permissions = UserPermissions(
                    allowed_translators=[],  # 空=继承用户组
                    denied_translators=[],
                    allowed_ocr=[],          # 空=继承用户组
                    denied_ocr=[],
                    allowed_colorizers=[],   # 空=继承用户组
                    denied_colorizers=[],
                    allowed_renderers=[],    # 空=继承用户组
                    denied_renderers=[],
                    allowed_workflows=[],    # 空=继承用户组
                    denied_workflows=[],
                    allowed_parameters=[],   # 空=继承用户组
                    denied_parameters=[],
                    max_concurrent_tasks=2,
                    daily_quota=100,
                    can_upload_files=True,
                    can_delete_files=False
                )
        
        # 哈希密码
        password_hash = self._hash_password(password)
        
        # 创建用户账号
        account = UserAccount(
            username=username,
            password_hash=password_hash,
            role=role,
            group=group,
            permissions=permissions,
            created_at=datetime.now(timezone.utc),
            last_login=None,
            is_active=True,
            must_change_password=False
        )
        
        # 保存到内存
        self.accounts[username] = account
        
        # 持久化
        self._save_accounts()
        
        logger.info(f"Created user: {username} (role: {role})")
        return account
    
    def get_user(self, username: str) -> Optional[UserAccount]:
        """
        获取用户信息
        
        Args:
            username: 用户名
        
        Returns:
            Optional[UserAccount]: 用户账号，如果不存在返回 None
        """
        return self.accounts.get(username)
    
    def list_users(self) -> List[UserAccount]:
        """
        列出所有用户
        
        Returns:
            List[UserAccount]: 所有用户账号列表
        """
        return list(self.accounts.values())
    
    def update_user(self, username: str, updates: Dict[str, Any]) -> bool:
        """
        更新用户信息
        
        Args:
            username: 用户名
            updates: 要更新的字段字典
        
        Returns:
            bool: 更新是否成功
        
        Raises:
            ValueError: 如果用户不存在或更新字段无效
        """
        account = self.accounts.get(username)
        if not account:
            raise ValueError(f"用户 '{username}' 不存在")
        
        # 允许更新的字段
        allowed_fields = {
            'role', 'group', 'permissions', 'is_active', 'must_change_password',
            'parameter_config', 'default_preset_id'
        }
        
        # 验证更新字段
        for field in updates.keys():
            if field not in allowed_fields:
                raise ValueError(f"不允许更新字段: {field}")
        
        # 应用更新
        if 'role' in updates:
            if updates['role'] not in ['admin', 'user']:
                raise ValueError(f"无效的角色: {updates['role']}")
            account.role = updates['role']
        
        if 'permissions' in updates:
            if isinstance(updates['permissions'], dict):
                account.permissions = UserPermissions.from_dict(updates['permissions'])
            elif isinstance(updates['permissions'], UserPermissions):
                account.permissions = updates['permissions']
            else:
                raise ValueError("permissions 必须是字典或 UserPermissions 对象")
        
        if 'is_active' in updates:
            account.is_active = bool(updates['is_active'])
        
        if 'must_change_password' in updates:
            account.must_change_password = bool(updates['must_change_password'])

        if 'group' in updates:
            account.group = str(updates['group'])

        if 'parameter_config' in updates:
            account.parameter_config = updates['parameter_config']

        if 'default_preset_id' in updates:
            account.default_preset_id = updates['default_preset_id']
        
        # 持久化
        self._save_accounts()
        
        logger.info(f"Updated user: {username}")
        return True
    
    def delete_user(self, username: str) -> bool:
        """
        删除用户
        
        Args:
            username: 用户名
        
        Returns:
            bool: 删除是否成功
        
        Raises:
            ValueError: 如果用户不存在
        """
        if username not in self.accounts:
            raise ValueError(f"用户 '{username}' 不存在")
        
        # 从内存中删除
        del self.accounts[username]
        
        # 持久化
        self._save_accounts()
        
        logger.info(f"Deleted user: {username}")
        return True
    
    def verify_password(self, username: str, password: str) -> bool:
        """
        验证密码
        
        Args:
            username: 用户名
            password: 密码（明文）
        
        Returns:
            bool: 密码是否正确
        """
        account = self.accounts.get(username)
        if not account:
            return False
        
        return self._verify_password(password, account.password_hash)
    
    def change_password(self, username: str, new_password: str) -> bool:
        """
        修改密码
        
        Args:
            username: 用户名
            new_password: 新密码（明文）
        
        Returns:
            bool: 修改是否成功
        
        Raises:
            ValueError: 如果用户不存在或密码强度不足
        """
        account = self.accounts.get(username)
        if not account:
            raise ValueError(f"用户 '{username}' 不存在")
        
        # 验证密码强度
        if len(new_password) < 6:
            raise ValueError("密码长度必须至少为6个字符")
        
        # 哈希新密码
        account.password_hash = self._hash_password(new_password)
        account.must_change_password = False
        
        # 持久化
        self._save_accounts()
        
        logger.info(f"Changed password for user: {username}")
        return True
    
    def create_default_admin(
        self,
        username: str = "admin",
        password: str = "admin123"
    ) -> Optional[UserAccount]:
        """
        创建默认管理员账号
        
        Args:
            username: 管理员用户名
            password: 管理员密码
        
        Returns:
            Optional[UserAccount]: 创建的管理员账号，如果已存在返回 None
        """
        # 如果已存在用户，不创建
        if self.accounts:
            logger.info("Users already exist, skipping default admin creation")
            return None
        
        # 创建默认管理员
        try:
            admin = self.create_user(
                username=username,
                password=password,
                role='admin',
                permissions=UserPermissions(
                    allowed_translators=["*"],
                    allowed_ocr=["*"],
                    allowed_colorizers=["*"],
                    allowed_renderers=["*"],
                    allowed_workflows=["*"],
                    allowed_parameters=["*"],
                    max_concurrent_tasks=10,
                    daily_quota=-1,
                    can_upload_files=True,
                    can_delete_files=True
                )
            )
            admin.must_change_password = True
            self._save_accounts()
            
            logger.warning(
                f"Created default admin account - "
                f"Username: {username} "
                f"(PLEASE CHANGE THIS PASSWORD IMMEDIATELY!)"
            )
            return admin
        except Exception as e:
            logger.error(f"Failed to create default admin: {e}")
            return None
    
    def update_last_login(self, username: str) -> bool:
        """
        更新最后登录时间
        
        Args:
            username: 用户名
        
        Returns:
            bool: 更新是否成功
        """
        account = self.accounts.get(username)
        if not account:
            return False
        
        account.last_login = datetime.now(timezone.utc)
        self._save_accounts()
        return True
    
    def _hash_password(self, password: str) -> str:
        """
        哈希密码
        
        Args:
            password: 明文密码
        
        Returns:
            str: 哈希后的密码
        """
        salt = bcrypt.gensalt()
        # bcrypt has a 72 byte limit, truncate if necessary
        password_bytes = password.encode('utf-8')[:72]
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')
    
    def _verify_password(self, password: str, password_hash: str) -> bool:
        """
        验证密码
        
        Args:
            password: 明文密码
            password_hash: 哈希密码
        
        Returns:
            bool: 密码是否匹配
        """
        try:
            # bcrypt has a 72 byte limit, truncate if necessary
            password_bytes = password.encode('utf-8')[:72]
            return bcrypt.checkpw(
                password_bytes,
                password_hash.encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False
    
    def _load_accounts(self) -> None:
        """从持久化存储加载账号"""
        try:
            data = load_json(self.accounts_file, default={'version': '1.0', 'accounts': []})
            
            accounts_data = data.get('accounts', [])
            self.accounts = {}
            
            for account_data in accounts_data:
                try:
                    account = UserAccount.from_dict(account_data)
                    self.accounts[account.username] = account
                except Exception as e:
                    logger.error(f"Failed to load account: {e}")
            
            logger.info(f"Loaded {len(self.accounts)} account(s)")
        except Exception as e:
            logger.error(f"Failed to load accounts: {e}")
            self.accounts = {}
    
    def _save_accounts(self) -> None:
        """保存账号到持久化存储"""
        try:
            data = {
                'version': '1.0',
                'accounts': [account.to_dict() for account in self.accounts.values()]
            }
            
            success = atomic_write_json(self.accounts_file, data, create_backup=True)
            if success:
                logger.debug(f"Saved {len(self.accounts)} account(s)")
            else:
                logger.error("Failed to save accounts")
        except Exception as e:
            logger.error(f"Failed to save accounts: {e}")
