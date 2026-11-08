"""
文本替换引擎 - 从 YAML 配置加载替换规则并应用到译文字段

支持三个分组：
  - common: 通用替换，始终执行
  - horizontal: 横排时执行（direction == 0）
  - vertical: 竖排时执行（direction == 1）

每条规则支持字面替换和正则替换（regex: true）
"""
import logging
import os
import re
from typing import Dict, Optional, Tuple

import yaml

from manga_translator.runtime_paths import get_config_path

logger = logging.getLogger(__name__)

# 默认配置文件路径
_DEFAULT_REPLACEMENTS_PATH = get_config_path('text_replacements.yaml')

# 缓存：(文件路径, mtime) -> 解析后的规则
_replacements_cache: Dict[str, Tuple[float, dict]] = {}


_DEFAULT_REPLACEMENTS_YAML = r"""# 文本替换规则配置
# 在渲染前应用到译文字段，按顺序执行
# 支持三种分组：horizontal（横排时）、vertical（竖排时）、common（通用，始终执行）
#
# 每条规则格式：
#   - pattern: "匹配模式"        # 必填，支持正则表达式
#     replace: "替换内容"        # 必填，支持正则反向引用 \1 \2 等
#     regex: true/false          # 可选，默认 false（字面替换），true 时 pattern 作为正则处理
#     enabled: true/false        # 可选，默认 true，设为 false 可临时禁用该规则
#     comment: "说明"            # 可选，备注
#
# 字面替换（regex: false 或省略）：直接逐字符匹配替换
# 正则替换（regex: true）：pattern 按 Python re 语法解析

# ═══════════════════════════════════════════════════════════════
# 通用替换（无论横排竖排都执行，最先应用）
# ═══════════════════════════════════════════════════════════════
common:
  - pattern: "・"
    replace: "·"
    comment: "统一中点符号"

  - pattern: '\.{3}'
    replace: '…'
    regex: true
    comment: "三个英文句号转换为一个省略号"

  # - pattern: '第(\d+)话'
  #   replace: '第\1話'
  #   regex: true
  #   enabled: false
  #   comment: "示例：话→話"

# ═══════════════════════════════════════════════════════════════
# 横排替换（direction == 0 时执行，在 common 之后）
# ═══════════════════════════════════════════════════════════════
horizontal:
  - pattern: "︰"
    replace: "‥"
  - pattern: "│"
    replace: "─"
  - pattern: "┃"
    replace: "━"
  - pattern: "║"
    replace: "═"
  - pattern: "︱"
    replace: "—"
  - pattern: "︲"
    replace: "–"
  - pattern: "︴"
    replace: "_"
  - pattern: "︵"
    replace: "（"
  - pattern: "︶"
    replace: "）"
  - pattern: "︷"
    replace: "{"
  - pattern: "︸"
    replace: "}"
  - pattern: "︹"
    replace: "〔"
  - pattern: "︺"
    replace: "〕"
  - pattern: "︻"
    replace: "【"
  - pattern: "︼"
    replace: "】"
  - pattern: "︽"
    replace: "《"
  - pattern: "︾"
    replace: "》"
  - pattern: "︿"
    replace: "〈"
  - pattern: "﹀"
    replace: "〉"
  - pattern: "﹁"
    replace: "「"
  - pattern: "﹂"
    replace: "」"
  - pattern: "﹃"
    replace: "『"
  - pattern: "﹄"
    replace: "』"
  - pattern: "﹅"
    replace: "﹑"
  - pattern: "﹇"
    replace: "["
  - pattern: "﹈"
    replace: "]"
  - pattern: "⋮"
    replace: "…"
  - pattern: "︙"
    replace: "⋯"
  - pattern: "≀"
    replace: "~"
  - pattern: "︕"
    replace: "!"
  - pattern: "︖"
    replace: "?"
  - pattern: "︒"
    replace: "。"
  - pattern: "︔"
    replace: "；"
  - pattern: "︓"
    replace: "："
  - pattern: "︐"
    replace: "，"

# ═══════════════════════════════════════════════════════════════
# 竖排替换（direction == 1 时执行，在 common 之后）
# ═══════════════════════════════════════════════════════════════
vertical:
  - pattern: '\s{2,}'
    replace: ' '
    regex: true
    comment: "竖排多余空白压缩为单个"

  - pattern: "‥"
    replace: "︰"
  - pattern: "─"
    replace: "│"
  - pattern: "━"
    replace: "┃"
  - pattern: "═"
    replace: "║"
  - pattern: "—"
    replace: "│"
  - pattern: "–"
    replace: "︲"
  - pattern: "―"
    replace: "|"
  - pattern: "⟨"
    replace: "︿"
  - pattern: "⟩"
    replace: "﹀"
  - pattern: "⟪"
    replace: "︿"
  - pattern: "⟫"
    replace: "﹀"
  - pattern: "﹑"
    replace: "﹅"
  - pattern: "⦅"
    replace: "︵"
  - pattern: "⦆"
    replace: "︶"
  - pattern: "❨"
    replace: "︵"
  - pattern: "❩"
    replace: "︶"
  - pattern: "❪"
    replace: "︷"
  - pattern: "❫"
    replace: "︸"
  - pattern: "❬"
    replace: "︿"
  - pattern: "❭"
    replace: "﹀"
  - pattern: "❮"
    replace: "︿"
  - pattern: "❯"
    replace: "﹀"
  - pattern: "（"
    replace: "︵"
  - pattern: "）"
    replace: "︶"
  - pattern: "("
    replace: "︵"
  - pattern: ")"
    replace: "︶"
  - pattern: "｛"
    replace: "︷"
  - pattern: "｝"
    replace: "︸"
  - pattern: "{"
    replace: "︷"
  - pattern: "}"
    replace: "︸"
  - pattern: "〔"
    replace: "︹"
  - pattern: "〕"
    replace: "︺"
  - pattern: "【"
    replace: "︻"
  - pattern: "】"
    replace: "︼"
  - pattern: "《"
    replace: "︽"
  - pattern: "》"
    replace: "︾"
  - pattern: "〈"
    replace: "︿"
  - pattern: "〉"
    replace: "﹀"
  - pattern: "﹇"
    replace: "［"
    comment: "方块括号竖排形字形覆盖差，归一回全角走旋转"
  - pattern: "﹈"
    replace: "］"
  - pattern: "["
    replace: "［"
  - pattern: "]"
    replace: "］"
  - pattern: "……"
    replace: "︙"
    comment: "六点变三点省略号"
  - pattern: "…"
    replace: "︙"
  - pattern: "⋯"
    replace: "︙"
  - pattern: "⋰"
    replace: "⋮"
  - pattern: "⋱"
    replace: "⋮"
  - pattern: "〜"
    replace: "︴"
  - pattern: "〰"
    replace: "︴"
  - pattern: "؟"
    replace: "︖"
  - pattern: "¿"
    replace: "︖"
  - pattern: "¡"
    replace: "︕"
  - pattern: "。"
    replace: "︒"
  - pattern: "；"
    replace: "︔"
  - pattern: "："
    replace: "︓"
  - pattern: "，"
    replace: "︐"
  - pattern: "‚"
    replace: "︐"
  - pattern: "„"
    replace: "︐"
  - pattern: "−"
    replace: "︲"
  - pattern: "・"
    replace: "·"
"""

def invalidate_replacements_cache(file_path: Optional[str] = None) -> None:
    """清理替换规则缓存，让后续渲染重新读取文件。"""
    if file_path is None:
        file_path = _DEFAULT_REPLACEMENTS_PATH
    _replacements_cache.pop(file_path, None)


def reset_text_replacements_to_default(file_path: Optional[str] = None) -> str:
    """将文本替换规则配置写回内置默认模板。"""
    if file_path is None:
        file_path = _DEFAULT_REPLACEMENTS_PATH

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(_DEFAULT_REPLACEMENTS_YAML)
    invalidate_replacements_cache(file_path)
    return file_path


def ensure_text_replacements_exists() -> str:
    """确保文本替换规则存在；历史默认文件升级由启动初始化统一处理。"""
    if os.path.exists(_DEFAULT_REPLACEMENTS_PATH):
        return _DEFAULT_REPLACEMENTS_PATH
    
    try:
        reset_text_replacements_to_default(_DEFAULT_REPLACEMENTS_PATH)
        logger.info(f"Created text replacement rules file: {_DEFAULT_REPLACEMENTS_PATH}")
    except Exception as e:
        logger.error(f"Failed to create text replacement rules file: {e}")
        
    return _DEFAULT_REPLACEMENTS_PATH



def _compile_rule(rule: dict) -> Optional[Tuple[re.Pattern, str]]:
    """编译单条替换规则为 (compiled_pattern, replace_string)"""
    pattern_str = rule.get('pattern')
    replace_str = rule.get('replace', '')
    is_regex = rule.get('regex', False)
    enabled = rule.get('enabled', True)

    if not pattern_str or not enabled:
        return None

    try:
        if is_regex:
            compiled = re.compile(pattern_str)
        else:
            # 字面替换：转义所有正则特殊字符
            compiled = re.compile(re.escape(pattern_str))
        return (compiled, replace_str)
    except re.error as e:
        comment = rule.get('comment', '')
        logger.warning(f"Failed to compile replacement rule: pattern='{pattern_str}' comment='{comment}' error={e}")
        return None


def _load_and_parse(file_path: str) -> dict:
    """
    加载并解析 YAML 替换配置文件。
    返回 {'common': [...], 'horizontal': [...], 'vertical': [...]}
    每个列表元素为 (compiled_pattern, replace_string)
    """
    result = {'common': [], 'horizontal': [], 'vertical': []}

    if not file_path or not os.path.exists(file_path):
        return result

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load replacement configuration: {file_path} error={e}")
        return result

    if not isinstance(data, dict):
        logger.error(f"Invalid replacement configuration format; expected a dictionary: {file_path}")
        return result

    for group_name in ('common', 'horizontal', 'vertical'):
        rules = data.get(group_name, [])
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            compiled = _compile_rule(rule)
            if compiled:
                result[group_name].append(compiled)

    return result


def load_replacements(file_path: Optional[str] = None) -> dict:
    """
    加载替换规则（带文件修改时间缓存）。

    参数:
        file_path: YAML 配置文件路径，None 时使用默认路径

    返回:
        {'common': [...], 'horizontal': [...], 'vertical': [...]}
    """
    if file_path is None:
        file_path = _DEFAULT_REPLACEMENTS_PATH

    if not os.path.exists(file_path):
        return {'common': [], 'horizontal': [], 'vertical': []}

    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        return {'common': [], 'horizontal': [], 'vertical': []}

    cached = _replacements_cache.get(file_path)
    if cached and cached[0] == mtime:
        return cached[1]

    parsed = _load_and_parse(file_path)
    _replacements_cache[file_path] = (mtime, parsed)
    return parsed


def _apply_replacement_rules(text: str, direction: int, replacements: dict, substitute) -> str:
    """共享保护标记、分组顺序与恢复步骤；substitute 提供字符串或带样式的正则替换。"""
    if not text:
        return text

    # 保护标记：提取 [BR]、<br>、【BR】 等，用占位符替代
    _PROTECTED_RE = re.compile(
        r'\[BR\]'            # [BR]
        r'|【BR】'           # 【BR】
        r'|<br\s*/?>'        # <br> / <br/>
        , re.IGNORECASE | re.DOTALL
    )
    protected_tokens = []

    def _protect(match):
        """暂存原始换行标记，返回供后续规则处理的占位符。"""
        protected_tokens.append(match.group(0))
        return f'\x00PROT{len(protected_tokens) - 1}\x00'

    text = substitute(_PROTECTED_RE, _protect, text)

    # 给用户规则稳定的身份；保护和恢复标记不创建新的字符身份。
    group_key = 'vertical' if direction == 1 else 'horizontal'
    for group in ('common', group_key):
        for index, (pattern, repl) in enumerate(replacements.get(group, [])):
            rule_id = (group, index, pattern.pattern, pattern.flags, repr(repl))
            text = substitute(pattern, repl, text, rule_id)

    # 恢复保护的标记
    for i, token in enumerate(protected_tokens):
        text = substitute(re.compile(re.escape(f'\x00PROT{i}\x00')), lambda match: token, text)

    return text


def apply_replacements(text: str, direction: int, replacements: Optional[dict] = None,
                       file_path: Optional[str] = None) -> str:
    """按 common、方向分组顺序替换译文，并保护现有的 BR 换行标记。

    direction 为 0（横排）或 1（竖排）；未提供 replacements 时从 file_path
    或默认配置加载规则。空文本原样返回，正则替换错误交由调用方处理。
    """
    if not text:
        return text
    if replacements is None:
        replacements = load_replacements(file_path)
    return _apply_replacement_rules(
        text, direction, replacements, lambda pattern, repl, current, rule_id=None: pattern.sub(repl, current)
    )


def build_h2v_dict(file_path: Optional[str] = None) -> dict:
    """
    从 YAML vertical 分组构建 CJK_H2V 兼容字典。
    仅包含非正则的单字符→单字符映射，供 CJK_Compatibility_Forms_translate 使用。
    """
    replacements = load_replacements(file_path)
    h2v = {}
    for pattern, repl in replacements.get('vertical', []):
        # 只取字面替换（pattern 是 re.escape 后的单字符）
        raw = pattern.pattern
        # re.escape 单字符的结果：要么是字符本身，要么是 \x 形式
        unescaped = None
        if len(raw) == 1:
            unescaped = raw
        elif len(raw) == 2 and raw[0] == '\\':
            unescaped = raw[1]
        
        if unescaped and len(repl) <= 1:
            h2v[unescaped] = repl if repl else unescaped

    return h2v


def build_v2h_dict(file_path: Optional[str] = None) -> dict:
    """
    从 YAML horizontal 分组构建 CJK_V2H 兼容字典。
    仅包含非正则的单字符→单字符映射。
    """
    replacements = load_replacements(file_path)
    v2h = {}
    for pattern, repl in replacements.get('horizontal', []):
        raw = pattern.pattern
        unescaped = None
        if len(raw) == 1:
            unescaped = raw
        elif len(raw) == 2 and raw[0] == '\\':
            unescaped = raw[1]

        if unescaped and len(repl) == 1:
            v2h[unescaped] = repl

    return v2h
