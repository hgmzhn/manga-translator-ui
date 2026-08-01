"""
Configuration Management Service

Manages .env configuration files, presets, and user configurations.
Provides backup, encryption, and validation capabilities.
"""

import base64
import hashlib
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet

from manga_translator.server.core.env_service import EnvService
from manga_translator.server.models.config_models import ConfigPreset, UserConfig
from manga_translator.server_paths import SERVER_DATA_DIR
from manga_translator.server.repositories.config_repository import (
    ConfigRepository,
    UserConfigRepository,
)

logger = logging.getLogger(__name__)


class ConfigManagementService:
    """Service for managing .env configurations, presets, and user configs."""
    
    _DATA_DIR = SERVER_DATA_DIR
    _DEFAULT_PRESETS_FILE = str(_DATA_DIR / "env_presets.json")
    _DEFAULT_USER_CONFIGS_FILE = str(_DATA_DIR / "user_configs.json")
    
    def __init__(
        self,
        env_file: str = ".env",
        presets_file: str = None,
        user_configs_file: str = None,
        encryption_key: Optional[str] = None
    ):
        # 使用默认绝对路径
        if presets_file is None:
            presets_file = self._DEFAULT_PRESETS_FILE
        if user_configs_file is None:
            user_configs_file = self._DEFAULT_USER_CONFIGS_FILE
        """
        Initialize the configuration management service.
        
        Args:
            env_file: Path to the .env file
            presets_file: Path to the presets JSON file
            user_configs_file: Path to the user configs JSON file
            encryption_key: Encryption key for sensitive data (auto-generated if None)
        """
        self.env_file = env_file
        self.env_service = EnvService(env_file)
        self.preset_repo = ConfigRepository(presets_file)
        self.user_config_repo = UserConfigRepository(user_configs_file)
        
        # Initialize encryption
        self._init_encryption(encryption_key)
        
        logger.info("ConfigManagementService initialized")
    
    def _init_encryption(self, encryption_key: Optional[str] = None):
        """Initialize encryption for sensitive data."""
        if encryption_key:
            # Use provided key - hash it to ensure it's 32 bytes
            key = hashlib.sha256(encryption_key.encode()).digest()
        else:
            # Generate key from machine-specific data
            machine_id = self._get_machine_id()
            key = hashlib.sha256(machine_id.encode()).digest()
        
        # Ensure key is base64-encoded Fernet key (Fernet requires base64-encoded 32-byte key)
        key_b64 = base64.urlsafe_b64encode(key)
        self.cipher = Fernet(key_b64)
    
    def _get_machine_id(self) -> str:
        """Get a machine-specific identifier for encryption."""
        # Use a combination of hostname and env file path
        import socket
        hostname = socket.gethostname()
        env_path = os.path.abspath(self.env_file)
        return f"{hostname}:{env_path}"
    
    def _encrypt_value(self, value: str) -> str:
        """Encrypt a sensitive value."""
        if not value:
            return ""
        encrypted = self.cipher.encrypt(value.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def _decrypt_value(self, encrypted_value: str) -> str:
        """Decrypt a sensitive value."""
        if not encrypted_value:
            return ""
        try:
            encrypted = base64.urlsafe_b64decode(encrypted_value.encode())
            decrypted = self.cipher.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt value: {e}")
            return ""
    
    # ========================================================================
    # Server .env Configuration Management
    # ========================================================================
    
    def get_server_config(self, mask_sensitive: bool = True) -> Dict[str, str]:
        """
        Get server .env configuration.
        
        Args:
            mask_sensitive: Whether to mask sensitive values (API keys)
        
        Returns:
            Dictionary of environment variables
        """
        return self.env_service.get_env_vars(show_values=not mask_sensitive)
    
    def update_server_config(
        self,
        config: Dict[str, str],
        admin_id: str,
        create_backup: bool = True
    ) -> bool:
        """
        Update server .env configuration.
        
        Args:
            config: New configuration dictionary
            admin_id: ID of the admin making the change
            create_backup: Whether to create a backup before updating
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create backup if requested
            if create_backup:
                backup_path = self._create_env_backup()
                logger.info(f"Created .env backup at {backup_path}")
            
            # Update environment variables
            for key, value in config.items():
                if value is not None:  # Allow empty strings but not None
                    self.env_service.update_env_var(key, value)
            
            # Log the change
            logger.info(f"Server config updated by admin {admin_id}")
            
            return True
        except Exception as e:
            logger.error(f"Failed to update server config: {e}")
            return False
    
    def _create_env_backup(self) -> str:
        """
        Create a backup of the .env file.
        
        Returns:
            Path to the backup file
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = SERVER_DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        backup_path = backup_dir / f".env.backup.{timestamp}"
        shutil.copy2(self.env_file, backup_path)
        
        # Keep only the last 10 backups
        self._cleanup_old_backups(backup_dir, max_backups=10)
        
        return str(backup_path)
    
    def _cleanup_old_backups(self, backup_dir: Path, max_backups: int = 10):
        """Clean up old backup files, keeping only the most recent ones."""
        backups = sorted(backup_dir.glob(".env.backup.*"), key=os.path.getmtime, reverse=True)
        for old_backup in backups[max_backups:]:
            try:
                old_backup.unlink()
                logger.info(f"Deleted old backup: {old_backup}")
            except Exception as e:
                logger.error(f"Failed to delete old backup {old_backup}: {e}")
    
    def restore_from_backup(self, backup_path: str, admin_id: str) -> bool:
        """
        Restore .env from a backup file.
        
        Args:
            backup_path: Path to the backup file
            admin_id: ID of the admin performing the restore
        
        Returns:
            True if successful, False otherwise
        """
        try:
            backup_dir = os.path.realpath(SERVER_DATA_DIR / "backups")
            resolved_backup_path = os.path.realpath(backup_path)
            if not resolved_backup_path.startswith(backup_dir + os.sep):
                logger.error(f"Invalid backup path: {backup_path}")
                return False
            if (
                os.path.dirname(resolved_backup_path) != backup_dir
                or not os.path.basename(resolved_backup_path).startswith(".env.backup.")
                or not os.path.isfile(resolved_backup_path)
            ):
                logger.error(f"Backup file not found: {backup_path}")
                return False
            
            # Create a backup of current state before restoring
            current_backup = self._create_env_backup()
            logger.info(f"Created backup of current state: {current_backup}")
            
            # Restore from backup
            shutil.copy2(resolved_backup_path, self.env_file)
            
            # Reload environment variables
            self.env_service.reload_env()
            
            logger.info(f"Restored .env from backup {resolved_backup_path} by admin {admin_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False
    
    def list_backups(self) -> List[Dict[str, str]]:
        """
        List available .env backups.
        
        Returns:
            List of backup information dictionaries
        """
        backup_dir = SERVER_DATA_DIR / "backups"
        if not backup_dir.exists():
            return []
        
        backups = []
        for backup_file in sorted(backup_dir.glob(".env.backup.*"), key=os.path.getmtime, reverse=True):
            stat = backup_file.stat()
            backups.append({
                "path": str(backup_file),
                "filename": backup_file.name,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "size": stat.st_size
            })
        
        return backups
    
    # ========================================================================
    # Configuration Preset Management
    # ========================================================================
    
    def create_preset(
        self,
        name: str,
        description: str,
        config: Dict[str, str],
        created_by: str,
        visible_to_groups: Optional[List[str]] = None
    ) -> Optional[ConfigPreset]:
        """
        Create a new configuration preset.
        
        Args:
            name: Preset name
            description: Preset description
            config: Configuration dictionary (will be encrypted)
            created_by: ID of the admin creating the preset
            visible_to_groups: List of group IDs that can see this preset
        
        Returns:
            Created ConfigPreset or None if failed
        """
        try:
            # Check if preset with same name exists
            existing = self.preset_repo.get_preset_by_name(name)
            if existing:
                logger.error(f"Preset with name '{name}' already exists")
                return None
            
            # Encrypt sensitive configuration values
            encrypted_config = self._encrypt_config(config)
            
            # Create preset
            preset = ConfigPreset.create(
                name=name,
                description=description,
                config=encrypted_config,
                created_by=created_by,
                visible_to_groups=visible_to_groups
            )
            
            # Save to repository
            self.preset_repo.add_preset(preset)
            
            logger.info(f"Created preset '{name}' by {created_by}")
            return preset
        except Exception as e:
            logger.error(f"Failed to create preset: {e}")
            return None
    
    def get_preset(self, preset_id: str, decrypt: bool = False) -> Optional[Dict]:
        """
        Get a configuration preset by ID.
        
        Args:
            preset_id: Preset ID
            decrypt: Whether to decrypt the configuration
        
        Returns:
            Preset dictionary or None if not found
        """
        preset_dict = self.preset_repo.get_preset_by_id(preset_id)
        if not preset_dict:
            return None
        
        if decrypt:
            preset_dict['config'] = self._decrypt_config(preset_dict['config'])
        
        return preset_dict
    
    def get_all_presets(self, include_config: bool = False) -> List[Dict]:
        """
        Get all configuration presets.
        
        Args:
            include_config: Whether to include configuration details
        
        Returns:
            List of preset dictionaries
        """
        presets = self.preset_repo.get_all_presets()
        
        if not include_config:
            # Remove config details for security
            return [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "description": p["description"],
                    "visible_to_groups": p.get("visible_to_groups", []),
                    "created_at": p.get("created_at"),
                    "created_by": p.get("created_by"),
                    "updated_at": p.get("updated_at")
                }
                for p in presets
            ]
        
        return presets
    
    def get_presets_for_group(self, group_id: str) -> List[Dict]:
        """
        Get presets visible to a specific group.
        
        Args:
            group_id: Group ID
        
        Returns:
            List of preset dictionaries (without config details)
        """
        presets = self.preset_repo.get_presets_for_group(group_id)
        
        # Remove config details for security
        return [
            {
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at")
            }
            for p in presets
        ]
    
    def update_preset(
        self,
        preset_id: str,
        updates: Dict,
        admin_id: str
    ) -> bool:
        """
        Update a configuration preset.
        
        Args:
            preset_id: Preset ID
            updates: Dictionary of fields to update
            admin_id: ID of the admin making the update
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Encrypt config if it's being updated
            if 'config' in updates:
                updates['config'] = self._encrypt_config(updates['config'])
            
            # Add updated timestamp
            updates['updated_at'] = datetime.now(timezone.utc).isoformat()
            
            success = self.preset_repo.update_preset(preset_id, updates)
            
            if success:
                logger.info(f"Updated preset {preset_id} by admin {admin_id}")
            
            return success
        except Exception as e:
            logger.error(f"Failed to update preset: {e}")
            return False
    
    def delete_preset(self, preset_id: str, admin_id: str) -> bool:
        """
        Delete a configuration preset.
        
        Args:
            preset_id: Preset ID
            admin_id: ID of the admin deleting the preset
        
        Returns:
            True if successful, False otherwise
        """
        try:
            success = self.preset_repo.delete_preset(preset_id)
            
            if success:
                logger.info(f"Deleted preset {preset_id} by admin {admin_id}")
            
            return success
        except Exception as e:
            logger.error(f"Failed to delete preset: {e}")
            return False
    
    def _encrypt_config(self, config: Dict[str, str]) -> Dict[str, str]:
        """Encrypt sensitive values in configuration."""
        encrypted = {}
        sensitive_keys = ['API_KEY', 'SECRET', 'PASSWORD', 'TOKEN']
        
        for key, value in config.items():
            # Check if key contains sensitive keywords
            if any(sensitive in key.upper() for sensitive in sensitive_keys):
                encrypted[key] = self._encrypt_value(value)
            else:
                encrypted[key] = value
        
        return encrypted
    
    def _decrypt_config(self, config: Dict[str, str]) -> Dict[str, str]:
        """Decrypt sensitive values in configuration."""
        decrypted = {}
        sensitive_keys = ['API_KEY', 'SECRET', 'PASSWORD', 'TOKEN']
        
        for key, value in config.items():
            # Check if key contains sensitive keywords
            if any(sensitive in key.upper() for sensitive in sensitive_keys):
                decrypted[key] = self._decrypt_value(value)
            else:
                decrypted[key] = value
        
        return decrypted
    
    # ========================================================================
    # User Configuration Management
    # ========================================================================
    
    def get_user_config(self, user_id: str, decrypt: bool = False) -> Optional[Dict]:
        """
        Get user configuration.
        
        Args:
            user_id: User ID
            decrypt: Whether to decrypt API keys
        
        Returns:
            User configuration dictionary or None if not found
        """
        config_dict = self.user_config_repo.get_user_config(user_id)
        
        if config_dict and decrypt:
            # Decrypt API keys
            if 'api_keys' in config_dict:
                config_dict['api_keys'] = self._decrypt_config(config_dict['api_keys'])
        
        return config_dict
    
    def save_user_config(
        self,
        user_id: str,
        api_keys: Optional[Dict[str, str]] = None,
        selected_preset_id: Optional[str] = None,
        custom_settings: Optional[Dict] = None,
        config_mode: Optional[str] = None
    ) -> bool:
        """
        Save user configuration.
        
        Args:
            user_id: User ID
            api_keys: User's API keys (will be encrypted)
            selected_preset_id: ID of selected preset
            custom_settings: Custom settings
            config_mode: Configuration mode ('server' or 'custom')
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get existing config or create new
            existing = self.user_config_repo.get_user_config(user_id)
            
            if existing:
                # Update existing config
                updates = {}
                if api_keys is not None:
                    updates['api_keys'] = self._encrypt_config(api_keys)
                if selected_preset_id is not None:
                    updates['selected_preset_id'] = selected_preset_id
                if custom_settings is not None:
                    updates['custom_settings'] = custom_settings
                if config_mode is not None:
                    updates['config_mode'] = config_mode
                
                updates['updated_at'] = datetime.now(timezone.utc).isoformat()
                
                success = self.user_config_repo.update_user_config(user_id, updates)
            else:
                # Create new config
                encrypted_keys = self._encrypt_config(api_keys) if api_keys else {}
                
                config = UserConfig.create(
                    user_id=user_id,
                    api_keys=encrypted_keys,
                    selected_preset_id=selected_preset_id,
                    custom_settings=custom_settings or {},
                    config_mode=config_mode or 'server'
                )
                
                self.user_config_repo.set_user_config(user_id, config)
                success = True
            
            if success:
                logger.info(f"Saved config for user {user_id}")
            
            return success
        except Exception as e:
            logger.error(f"Failed to save user config: {e}")
            return False
    
    def delete_user_config(self, user_id: str) -> bool:
        """
        Delete user configuration.
        
        Args:
            user_id: User ID
        
        Returns:
            True if successful, False otherwise
        """
        try:
            success = self.user_config_repo.delete_user_config(user_id)
            
            if success:
                logger.info(f"Deleted config for user {user_id}")
            
            return success
        except Exception as e:
            logger.error(f"Failed to delete user config: {e}")
            return False
    
    def apply_preset_to_user(self, user_id: str, preset_id: str) -> bool:
        """
        Apply a preset to a user's configuration.
        
        Args:
            user_id: User ID
            preset_id: Preset ID to apply
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get preset
            preset = self.get_preset(preset_id, decrypt=True)
            if not preset:
                logger.error(f"Preset {preset_id} not found")
                return False
            
            # Update user config with preset
            return self.save_user_config(
                user_id=user_id,
                selected_preset_id=preset_id,
                config_mode='server'
            )
        except Exception as e:
            logger.error(f"Failed to apply preset to user: {e}")
            return False

    def clear_user_selected_preset(self, user_id: str) -> bool:
        """Clear the user's explicitly selected preset and fall back to group/server defaults."""
        try:
            existing = self.user_config_repo.get_user_config(user_id)
            if not existing:
                return True

            return self.user_config_repo.update_user_config(
                user_id,
                {
                    "selected_preset_id": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:
            logger.error(f"Failed to clear selected preset for user: {e}")
            return False
