"""批量管理方案的持久化 —— ``config/batch_edit_schemes.yaml``。

与 ``text_replacements.yaml`` / ``rich_text_rules.yaml`` 同目录同格式，但方案
只服务桌面 UI 的批量管理页、不进渲染管线，因此读写留在 ``desktop_qt_ui``
一侧，也不加入 ``manga_translator/runtime_files.py`` 的启动引导（那会造成
manga_translator → desktop_qt_ui 的反向依赖）。文件在首次访问时惰性创建。

方案结构::

    schemes:
      - name: "方案名"
        enabled: true
        comment: ""
        match:
          logic: all            # all | any
          conditions:
            - {field: direction, op: eq, value: v}
        actions:
          - {type: set_fields, fields: {line_spacing: 1.1}}
          - {type: replace_text, pattern: '\\.{3}', regex: true, replace: '…'}
          - {type: rich_text, mode: overwrite, pattern: '[（）]', regex: true,
             style: {transform: {rotation: -90}}, ruby: "", tcy: false}

``replace_text`` 与 ``rich_text`` 都可以出现多条，按写下的先后依次执行
（``ACTION_ORDER`` 的排序是稳定的，只分组不打乱组内顺序）。
"""

from __future__ import annotations

import copy
import os
from typing import Any, Optional

import yaml

from manga_translator.runtime_paths import get_config_path

ACTION_SET_FIELDS = "set_fields"
ACTION_REPLACE_TEXT = "replace_text"
ACTION_RICH_TEXT = "rich_text"

# 动作固定按此顺序执行：改文字会清掉命中区间的富文本，先加样式再改文字等于白加。
ACTION_ORDER = (ACTION_SET_FIELDS, ACTION_REPLACE_TEXT, ACTION_RICH_TEXT)

LOGIC_ALL = "all"
LOGIC_ANY = "any"

# rich_text 动作的三种模式：
#   overwrite 覆盖 —— 你编的那几项赢，命中区间上的其他项保留
#   fill      添加 —— 命中区间已有的项赢，只补它没有的
#   replace   替换 —— 先清掉命中区间原有的全部样式，再应用新样式
RICH_MODE_OVERWRITE = "overwrite"
RICH_MODE_FILL = "fill"
RICH_MODE_REPLACE = "replace"
RICH_MODES = (RICH_MODE_OVERWRITE, RICH_MODE_FILL, RICH_MODE_REPLACE)

_DEFAULT_SCHEMES_YAML = """# 批量管理方案
# 每个方案 = 匹配条件（筛 region）+ 批量动作（改命中的内容）
#
# match.logic: all（全部满足）| any（任一满足）
# match.conditions[].field 见批量管理页的字段下拉；op 随字段类型变化
#
# actions 固定按 set_fields -> replace_text -> rich_text 顺序执行，
# 因为改文字会清掉命中区间的富文本，先加样式再改文字等于白加。
# replace_text / rich_text 都可以写多条，同类型之间按写下的先后执行。
#
# rich_text.mode: overwrite（你写的项赢）| fill（只补缺少项）| replace（整套替换）
# rich_text.pattern 留空 = 整条 region 的全部文字
# rich_text.match_style 可选；有值时按 match_style_logic: all | any 筛选命中文字的现有样式
#
# 条件负责筛 region，动作各自带 pattern 负责在译文里定位子串 —— 两者分开，
# 不存在“哪条条件的命中区间才是目标”的歧义。

schemes:
  - name: "示例：竖排小字压行距"
    enabled: false
    comment: "启用前请按自己的需要改条件和动作"
    match:
      logic: all
      conditions:
        - field: direction
          op: eq
          value: v
        - field: font_size
          op: lte
          value: 18
    actions:
      - type: set_fields
        fields:
          line_spacing: 1.0
"""


def get_schemes_path() -> str:
    return get_config_path("batch_edit_schemes.yaml")


def ensure_schemes_exists(file_path: Optional[str] = None) -> str:
    file_path = os.path.abspath(file_path or get_schemes_path())
    if not os.path.exists(file_path):
        reset_schemes_to_default(file_path)
    return file_path


def reset_schemes_to_default(file_path: Optional[str] = None) -> str:
    file_path = os.path.abspath(file_path or get_schemes_path())
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_DEFAULT_SCHEMES_YAML)
    return file_path


# ─── 归一化 ───


def _clean_conditions(raw: Any) -> list[dict]:
    conditions: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "") or "").strip()
        op = str(item.get("op", "") or "").strip()
        if not field or not op:
            continue
        conditions.append({"field": field, "op": op, "value": copy.deepcopy(item.get("value"))})
    return conditions


def _clean_action(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    action_type = str(raw.get("type", "") or "").strip()
    if action_type == ACTION_SET_FIELDS:
        fields = raw.get("fields")
        if not isinstance(fields, dict) or not fields:
            return None
        return {"type": action_type, "fields": copy.deepcopy(fields)}

    pattern = str(raw.get("pattern", "") or "")
    regex = bool(raw.get("regex", False))
    if action_type == ACTION_REPLACE_TEXT:
        if not pattern:
            return None
        return {
            "type": action_type,
            "pattern": pattern,
            "regex": regex,
            "replace": str(raw.get("replace", "") or ""),
        }
    if action_type == ACTION_RICH_TEXT:
        mode = str(raw.get("mode", "") or "").strip().lower()
        if mode not in RICH_MODES:
            # 旧 clear 动作没有目标样式，不能安全迁移为替换；直接丢弃。
            if mode == "clear":
                return None
            mode = RICH_MODE_OVERWRITE
        # pattern 留空 = 整条 region 的全部文字（region 由匹配条件筛，不是这里）
        style = raw.get("style")
        ruby = raw.get("ruby", "")
        match_style = raw.get("match_style")
        match_style_logic = str(raw.get("match_style_logic", LOGIC_ALL) or LOGIC_ALL).strip().lower()
        if match_style_logic not in (LOGIC_ALL, LOGIC_ANY):
            match_style_logic = LOGIC_ALL
        action = {
            "type": action_type,
            "mode": mode,
            "pattern": pattern,
            "regex": regex,
            "style": copy.deepcopy(style) if isinstance(style, dict) else {},
            "ruby": ruby if isinstance(ruby, str) else "",
            "tcy": bool(raw.get("tcy", False)),
        }
        if isinstance(match_style, dict) and match_style:
            action["match_style"] = copy.deepcopy(match_style)
            action["match_style_logic"] = match_style_logic
        # 三者全空的富文本动作什么也做不了，直接丢弃（与规则页 _compile_rule 同口径）
        if not action["style"] and not action["ruby"] and not action["tcy"]:
            return None
        return action
    return None


def normalize_scheme(raw: Any) -> Optional[dict]:
    """校验并归一化一个方案；结构不可用时返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "") or "").strip()
    if not name:
        return None

    match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
    logic = str(match.get("logic", LOGIC_ALL) or LOGIC_ALL).strip().lower()
    if logic not in (LOGIC_ALL, LOGIC_ANY):
        logic = LOGIC_ALL

    actions = [action for action in (_clean_action(item) for item in raw.get("actions") or []) if action]
    actions.sort(key=lambda action: ACTION_ORDER.index(action["type"]))

    return {
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "comment": str(raw.get("comment", "") or ""),
        "match": {"logic": logic, "conditions": _clean_conditions(match.get("conditions"))},
        "actions": actions,
    }


def new_scheme(name: str) -> dict:
    return {
        "name": str(name or "").strip() or "未命名",
        "enabled": True,
        "comment": "",
        "match": {"logic": LOGIC_ALL, "conditions": []},
        "actions": [],
    }


# ─── 读写 ───


def load_schemes(file_path: Optional[str] = None) -> list[dict]:
    """读取全部方案；文件缺失/损坏时返回空列表而不是抛异常。"""
    file_path = ensure_schemes_exists(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    raw_schemes = data.get("schemes")
    if not isinstance(raw_schemes, list):
        return []
    return [scheme for scheme in (normalize_scheme(item) for item in raw_schemes) if scheme]


def dump_schemes(schemes: list[dict]) -> str:
    payload = {"schemes": [scheme for scheme in (normalize_scheme(item) for item in schemes) if scheme]}
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)


def save_schemes(schemes: list[dict], file_path: Optional[str] = None) -> str:
    file_path = os.path.abspath(file_path or get_schemes_path())
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump_schemes(schemes))
    return file_path
