"""
环境变量服务（EnvService）

管理 .env 文件的加载、解析、更新和热重载。
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from manga_translator.utils.dotenv_utils import (
    normalize_env_value,
    read_dotenv_file,
    validate_env_key,
    write_dotenv_file,
)

logger = logging.getLogger(__name__)


class EnvService:
    """环境变量服务"""
    
    def __init__(self, env_file: str = ".env"):
        """
        初始化环境变量服务
        
        Args:
            env_file: .env 文件路径（相对于工作区根目录）
        """
        self.env_file = env_file
        self.env_vars: Dict[str, str] = {}
        self._load_env_file()
    
    def load_env_file(self, path: Optional[str] = None) -> Dict[str, str]:
        """
        加载 .env 文件
        
        Args:
            path: .env 文件路径（如果为 None，使用初始化时的路径）
        
        Returns:
            Dict[str, str]: 加载的环境变量字典
        """
        if path:
            self.env_file = path
        
        return self._load_env_file()
    
    def save_env_file(self, path: Optional[str] = None, env_vars: Optional[Dict[str, str]] = None) -> bool:
        """
        保存 .env 文件
        
        Args:
            path: .env 文件路径（如果为 None，使用当前路径）
            env_vars: 要保存的环境变量（如果为 None，使用当前环境变量）
        
        Returns:
            bool: 保存是否成功
        """
        if path:
            self.env_file = path
        
        if env_vars is None:
            env_vars = self.env_vars
        
        try:
            write_dotenv_file(self.env_file, env_vars)
            
            logger.info(f"Saved {len(env_vars)} environment variable(s) to {self.env_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save .env file: {e}")
            return False
    
    def reload_env(self) -> bool:
        """
        重新加载环境变量
        
        Returns:
            bool: 重新加载是否成功
        """
        try:
            self._load_env_file()
            logger.info("Environment variables reloaded")
            return True
        except Exception as e:
            logger.error(f"Failed to reload environment variables: {e}")
            return False
    
    def get_env_vars(self, show_values: bool = False) -> Dict[str, str]:
        """
        获取环境变量
        
        Args:
            show_values: 是否显示实际值（False 时隐藏敏感信息）
        
        Returns:
            Dict[str, str]: 环境变量字典
        """
        if show_values:
            return self.env_vars.copy()
        else:
            # 隐藏敏感值
            return {
                key: self._mask_value(value)
                for key, value in self.env_vars.items()
            }
    
    def get_env_var(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        获取单个环境变量
        
        Args:
            key: 环境变量名
            default: 默认值
        
        Returns:
            Optional[str]: 环境变量值
        """
        return self.env_vars.get(key, default)
    
    def update_env_var(self, key: str, value: str) -> bool:
        """
        更新单个环境变量
        
        Args:
            key: 环境变量名
            value: 环境变量值
        
        Returns:
            bool: 更新是否成功
        """
        try:
            key = validate_env_key(key)
            value = normalize_env_value(value)

            # 更新内存中的值
            self.env_vars[key] = value
            
            # 同时更新系统环境变量
            os.environ[key] = value
            
            # 保存到文件
            success = self.save_env_file()
            
            if success:
                logger.info(f"Updated environment variable: {key}")
            
            return success
        except Exception as e:
            logger.error(f"Failed to update environment variable {key}: {e}")
            return False
    
    def delete_env_var(self, key: str) -> bool:
        """
        删除环境变量
        
        Args:
            key: 环境变量名
        
        Returns:
            bool: 删除是否成功
        """
        try:
            if key in self.env_vars:
                del self.env_vars[key]
                
                # 同时从系统环境变量中删除
                if key in os.environ:
                    del os.environ[key]
                
                # 保存到文件
                success = self.save_env_file()
                
                if success:
                    logger.info(f"Deleted environment variable: {key}")
                
                return success
            else:
                logger.warning(f"Environment variable {key} does not exist")
                return False
        except Exception as e:
            logger.error(f"Failed to delete environment variable {key}: {e}")
            return False
    
    def _load_env_file(self) -> Dict[str, str]:
        """
        从 .env 文件加载环境变量
        
        Returns:
            Dict[str, str]: 加载的环境变量字典
        """
        self.env_vars = {}
        
        try:
            env_path = Path(self.env_file)
            
            # 检查文件是否存在
            if not env_path.exists():
                logger.warning(f".env file not found at {self.env_file}")
                return self.env_vars
            
            self.env_vars = read_dotenv_file(env_path)
            for key, value in self.env_vars.items():
                os.environ[key] = value
            
            logger.info(f"Loaded {len(self.env_vars)} environment variable(s) from {self.env_file}")
            return self.env_vars
        
        except Exception as e:
            logger.error(f"Failed to load .env file: {e}")
            return self.env_vars
    
    def _mask_value(self, value: str) -> str:
        """
        隐藏敏感值
        
        Args:
            value: 原始值
        
        Returns:
            str: 隐藏后的值
        """
        if len(value) <= 4:
            return '*' * len(value)
        else:
            # 显示前2个和后2个字符
            return value[:2] + '*' * (len(value) - 4) + value[-2:]
