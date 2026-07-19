import os
import logging
import re
from typing import Optional, Tuple

from manga_translator.runtime_paths import get_config_path

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE_PATH = get_config_path('translation_template.json')
DEFAULT_TRANSLATION_OUTPUT_FORMAT = 'json'
_SAFE_OUTPUT_FORMAT_RE = re.compile(
    r'^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$',
    re.IGNORECASE,
)

_DEFAULT_TEMPLATE_JSON = '''"output_format": "json",
{
    "<original>": "<translated>",
    "<original>": "<translated>",
    "<original>": "<translated>"
}
'''

_OUTPUT_FORMAT_LINE_RE = re.compile(
    r'''(?im)^[ \t]*["']?output_format["']?[ \t]*:[ \t]*'''
    r'''["']?(?P<format>[a-z0-9_.-]+)["']?[ \t]*,?[ \t]*(?:\r?\n|$)'''
)


def normalize_translation_output_format(value: object) -> str:
    """规范化模板输出扩展名，允许任意安全的文件格式名称。"""
    output_format = str(value or '').strip().lower().lstrip('.')
    if _SAFE_OUTPUT_FORMAT_RE.fullmatch(output_format):
        return output_format
    if output_format:
        logger.warning(
            "Invalid translation template output_format '%s'; falling back to '%s'",
            output_format,
            DEFAULT_TRANSLATION_OUTPUT_FORMAT,
        )
    return DEFAULT_TRANSLATION_OUTPUT_FORMAT


def parse_translation_template_config(template_string: str) -> Tuple[str, str]:
    """读取模板级配置，并返回不含配置行的实际文本模板。"""
    match = _OUTPUT_FORMAT_LINE_RE.search(template_string or '')
    if not match:
        return DEFAULT_TRANSLATION_OUTPUT_FORMAT, template_string

    output_format = normalize_translation_output_format(match.group('format'))
    template_content = template_string[:match.start()] + template_string[match.end():]
    return output_format, template_content


def get_translation_output_format(template_path: Optional[str] = None) -> str:
    """读取模板配置的导出扩展名；文件缺失或无参数时默认 JSON。"""
    final_path = template_path or _DEFAULT_TEMPLATE_PATH
    try:
        with open(final_path, 'r', encoding='utf-8') as f:
            template_string = f.read()
        output_format, _ = parse_translation_template_config(template_string)
        return output_format
    except FileNotFoundError:
        return DEFAULT_TRANSLATION_OUTPUT_FORMAT
    except Exception as e:
        logger.warning(f"读取翻译模板输出格式失败，使用默认 JSON: {e}")
        return DEFAULT_TRANSLATION_OUTPUT_FORMAT


def _write_default_template(file_path: Optional[str] = None) -> str:
    """写入当前内置默认翻译模板。"""
    final_path = file_path or _DEFAULT_TEMPLATE_PATH
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(_DEFAULT_TEMPLATE_JSON)
    return final_path


def ensure_translation_template_exists() -> str:
    """确保翻译模板存在；历史默认模板升级由启动初始化统一处理。"""
    if os.path.exists(_DEFAULT_TEMPLATE_PATH):
        return _DEFAULT_TEMPLATE_PATH

    try:
        _write_default_template(_DEFAULT_TEMPLATE_PATH)
        logger.info(f"已创建翻译模板文件: {_DEFAULT_TEMPLATE_PATH}")
    except Exception as e:
        logger.error(f"创建翻译模板文件失败: {e}")
        
    return _DEFAULT_TEMPLATE_PATH
