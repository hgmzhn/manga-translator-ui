"""
错误处理和验证服务
提供统一的错误处理、输入验证功能
"""
import os
import re
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

class ErrorLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool = True
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    def add_error(self, message: str):
        self.errors.append(message)
        self.is_valid = False
    
    def add_warning(self, message: str):
        self.warnings.append(message)

class InputValidator:
    """输入验证器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # API密钥验证模式
        self.api_patterns = {
            'openai': r'^sk-[a-zA-Z0-9]{48}$',
            'deepl': r'^[a-fA-F0-9-:fx]+$',
        }
    
    def validate_file_path(self, file_path: str) -> ValidationResult:
        """验证文件路径"""
        result = ValidationResult()
        
        if not file_path:
            result.add_error("文件路径不能为空")
            return result
        
        if not os.path.exists(file_path):
            result.add_error("文件不存在")
        elif not os.path.isfile(file_path):
            result.add_error("路径不是文件")
        
        return result
    
    def validate_image_file(self, file_path: str) -> ValidationResult:
        """验证图片文件"""
        result = self.validate_file_path(file_path)
        
        if result.is_valid:
            valid_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}
            _, ext = os.path.splitext(file_path)
            
            if ext.lower() not in valid_extensions:
                result.add_error(f"不支持的图片格式: {ext}")
        
        return result
    
    def validate_api_key(self, api_key: str, provider: str) -> ValidationResult:
        """验证API密钥"""
        result = ValidationResult()
        
        if not api_key:
            result.add_error("API密钥不能为空")
            return result
        
        pattern = self.api_patterns.get(provider.lower())
        if pattern and not re.match(pattern, api_key):
            result.add_error(f"{provider} API密钥格式不正确")
        
        return result

# 全局验证器
_validator = None

def get_validator() -> InputValidator:
    global _validator
    if _validator is None:
        _validator = InputValidator()
    return _validator