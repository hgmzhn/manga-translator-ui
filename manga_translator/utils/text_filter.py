# 文本过滤工具
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from manga_translator.runtime_paths import get_config_dir

from . import get_logger

logger = get_logger('TextFilter')

# 过滤列表缓存：(包含过滤列表, 精确过滤列表)
_filter_lists: Optional[Tuple[List[str], List[str]]] = None

_FILTER_LIST_FILENAME = 'filter_list.json'
_LEGACY_FILTER_LIST_FILENAME = 'filter_list.txt'

_DEFAULT_FILTER_LIST_DATA = {
    "contains": [],
    "exact": [],
}


def _get_filter_list_path() -> str:
    """
    获取 JSON 过滤列表文件路径

    打包环境：可执行文件同级/config/filter_list.json
    开发环境：项目根目录/config/filter_list.json
    """
    return os.path.join(get_config_dir(), _FILTER_LIST_FILENAME)


def _get_legacy_filter_list_path() -> str:
    """获取旧版 TXT 过滤列表文件路径。"""
    return os.path.join(get_config_dir(), _LEGACY_FILTER_LIST_FILENAME)


def _sanitize_rule_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []

    sanitized: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            sanitized.append(text)
    return sanitized


def _normalize_rule_list(values: Any) -> List[str]:
    return [item.lower() for item in _sanitize_rule_list(values)]


def _write_filter_list_json(path: str, data: Dict[str, Any]) -> None:
    payload = dict(data) if isinstance(data, dict) else {}
    payload["contains"] = _sanitize_rule_list(payload.get("contains", []))
    payload["exact"] = _sanitize_rule_list(payload.get("exact", []))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def _parse_legacy_filter_list(path: str) -> Tuple[List[str], List[str]]:
    contains_list: List[str] = []
    exact_list: List[str] = []
    current_section = None

    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line == '[包含过滤]':
                current_section = 'contains'
                continue
            if line == '[精确过滤]':
                current_section = 'exact'
                continue

            if current_section == 'contains':
                contains_list.append(line)
            elif current_section == 'exact':
                exact_list.append(line)

    return contains_list, exact_list


def _migrate_legacy_filter_list() -> bool:
    json_path = _get_filter_list_path()
    legacy_path = _get_legacy_filter_list_path()

    if os.path.exists(json_path) or not os.path.exists(legacy_path):
        return False

    try:
        contains_list, exact_list = _parse_legacy_filter_list(legacy_path)
        _write_filter_list_json(json_path, {
            "contains": contains_list,
            "exact": exact_list,
        })
        logger.info(f"已将旧版过滤列表迁移为 JSON: {json_path}")
        return True
    except Exception as exc:
        logger.error(f"迁移旧版过滤列表失败: {exc}")
        return False


def ensure_filter_list_exists() -> str:
    """
    确保过滤列表文件存在，如果不存在则创建默认 JSON 文件。

    Returns:
        过滤列表文件路径
    """
    filter_path = _get_filter_list_path()

    if os.path.exists(filter_path):
        return filter_path

    if _migrate_legacy_filter_list():
        return filter_path

    try:
        _write_filter_list_json(filter_path, _DEFAULT_FILTER_LIST_DATA)
        logger.info(f"已创建过滤列表文件: {filter_path}")
    except Exception as exc:
        logger.error(f"创建过滤列表文件失败: {exc}")

    return filter_path


def load_filter_list_config() -> Dict[str, List[str]]:
    """
    加载过滤列表 JSON 配置，保留原始大小写。
    """
    filter_path = ensure_filter_list_exists()

    try:
        with open(filter_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("filter list json root must be an object")
        return {
            "contains": _sanitize_rule_list(data.get("contains", [])),
            "exact": _sanitize_rule_list(data.get("exact", [])),
        }
    except Exception as exc:
        logger.error(f"加载过滤列表配置失败: {exc}")
        return {
            "contains": [],
            "exact": [],
        }


def save_filter_list_config(data: Dict[str, Any]) -> str:
    """
    保存过滤列表 JSON 配置。
    """
    global _filter_lists

    filter_path = _get_filter_list_path()
    _write_filter_list_json(filter_path, data)
    _filter_lists = None
    return filter_path


def load_filter_list(force_reload: bool = False) -> Tuple[List[str], List[str]]:
    """
    加载过滤列表。

    Args:
        force_reload: 是否强制重新加载

    Returns:
        (包含过滤列表, 精确过滤列表)，都是小写
    """
    global _filter_lists

    if _filter_lists is not None and not force_reload:
        return _filter_lists

    try:
        config = load_filter_list_config()
        contains_list = _normalize_rule_list(config.get("contains", []))
        exact_list = _normalize_rule_list(config.get("exact", []))

        if contains_list or exact_list:
            logger.info(f"已加载过滤规则: 包含过滤 {len(contains_list)} 条, 精确过滤 {len(exact_list)} 条")

        _filter_lists = (contains_list, exact_list)
    except Exception as exc:
        logger.error(f"加载过滤列表失败: {exc}")
        _filter_lists = ([], [])

    return _filter_lists


def match_filter(text: str) -> Optional[Tuple[str, str]]:
    """
    检查文本是否匹配过滤列表。

    Args:
        text: 要检查的文本

    Returns:
        (匹配的过滤词, 匹配类型)，如果没有匹配返回 None
        匹配类型: "包含" 或 "精确"
    """
    if not text:
        return None

    contains_list, exact_list = load_filter_list()
    text_lower = text.lower()

    for filter_word in exact_list:
        if text_lower == filter_word:
            return (filter_word, "精确")

    for filter_word in contains_list:
        if filter_word in text_lower:
            return (filter_word, "包含")

    return None


def should_filter(text: str) -> bool:
    """
    检查文本是否应该被过滤。

    Args:
        text: 要检查的文本

    Returns:
        True 如果应该过滤，False 否则
    """
    return match_filter(text) is not None
