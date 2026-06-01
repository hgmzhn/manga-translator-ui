import os
import logging
from . import BASE_PATH

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE_PATH = os.path.join(BASE_PATH, 'examples', 'translation_template.json')

_DEFAULT_TEMPLATE_JSON = """{
    "<original>": "<translated>",
    "<original>": "<translated>",
    "<original>": "<translated>"
}
"""

def ensure_translation_template_exists() -> str:
    """确保翻译模板文件存在，如果不存在则使用内置模板创建。"""
    if os.path.exists(_DEFAULT_TEMPLATE_PATH):
        return _DEFAULT_TEMPLATE_PATH
    
    os.makedirs(os.path.dirname(_DEFAULT_TEMPLATE_PATH), exist_ok=True)
    try:
        with open(_DEFAULT_TEMPLATE_PATH, 'w', encoding='utf-8') as f:
            f.write(_DEFAULT_TEMPLATE_JSON)
        logger.info(f"已创建翻译模板文件: {_DEFAULT_TEMPLATE_PATH}")
    except Exception as e:
        logger.error(f"创建翻译模板文件失败: {e}")
        
    return _DEFAULT_TEMPLATE_PATH
