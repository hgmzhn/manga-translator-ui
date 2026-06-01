"""
文本替换引擎 - 从 YAML 配置加载替换规则并应用到译文字段

支持三个分组：
  - common: 通用替换，始终执行
  - horizontal: 横排时执行（direction == 0）
  - vertical: 竖排时执行（direction == 1）

每条规则支持字面替换和正则替换（regex: true）。
连续的单字符字面规则会合并为一次 str.translate 调用以提升性能。
"""
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import yaml

from ..utils import BASE_PATH

logger = logging.getLogger(__name__)

# 默认配置文件路径
_DEFAULT_REPLACEMENTS_PATH = os.path.join(BASE_PATH, 'examples', 'text_replacements.yaml')

# 保护标记正则（模块级，编译一次复用，避免每次调用 apply_replacements 时重复编译）
_PROTECTED_RE = re.compile(
    r'<H>.*?</H>'        # <H>...</H> 块
    r'|\[BR\]'           # [BR]
    r'|【BR】'           # 【BR】
    r'|<br\s*/?>'        # <br> / <br/>
    , re.IGNORECASE | re.DOTALL
)

# 缓存：文件路径 -> (mtime, 解析后的规则)
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

  - pattern: '\s{2,}'
    replace: ' '
    regex: true
    comment: "多余空格压缩为单个"

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
  - pattern: "‥"
    replace: "︰"
  - pattern: "─"
    replace: "│"
  - pattern: "━"
    replace: "┃"
  - pattern: "═"
    replace: "║"
  - pattern: "—"
    replace: "︱"
  - pattern: "―"
    replace: "|"
  - pattern: "–"
    replace: "︲"
  - pattern: "_"
    replace: "︴"
  - pattern: "("
    replace: "︵"
  - pattern: ")"
    replace: "︶"
  - pattern: "（"
    replace: "︵"
  - pattern: "）"
    replace: "︶"
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
  - pattern: "⟨"
    replace: "︿"
  - pattern: "⟩"
    replace: "﹀"
  - pattern: "⟪"
    replace: "︿"
  - pattern: "⟫"
    replace: "﹀"
  - pattern: "「"
    replace: "﹁"
  - pattern: "」"
    replace: "﹂"
  - pattern: "『"
    replace: "﹃"
  - pattern: "』"
    replace: "﹄"
  - pattern: "\""
    replace: "﹂"
  - pattern: "'"
    replace: "﹂"
  - pattern: "\u201C"
    replace: "﹁"
    comment: "左双引号"
  - pattern: "\u201D"
    replace: "﹂"
    comment: "右双引号"
  - pattern: "﹑"
    replace: "﹅"
  - pattern: "["
    replace: "﹇"
  - pattern: "]"
    replace: "﹈"
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
    replace: "﹇"
  - pattern: "❭"
    replace: "﹈"
  - pattern: "❮"
    replace: "︿"
  - pattern: "❯"
    replace: "﹀"
  - pattern: "﹆"
    replace: "﹆"
    comment: "保持不变"
  - pattern: "﹉"
    replace: "﹉"
    comment: "保持不变"
  - pattern: "﹊"
    replace: "﹊"
    comment: "保持不变"
  - pattern: "﹋"
    replace: "﹋"
    comment: "保持不变"
  - pattern: "﹌"
    replace: "﹌"
    comment: "保持不变"
  - pattern: "﹍"
    replace: "﹍"
    comment: "保持不变"
  - pattern: "﹎"
    replace: "﹎"
    comment: "保持不变"
  - pattern: "﹏"
    replace: "﹏"
    comment: "保持不变"
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
  - pattern: "″"
    replace: "﹂"
  - pattern: "‴"
    replace: "﹂"
  - pattern: "‶"
    replace: "﹁"
  - pattern: "ⷷ"
    replace: "﹁"
  - pattern: "〜"
    replace: "︴"
  - pattern: "～"
    replace: "︴"
  - pattern: "~"
    replace: "≀"
  - pattern: "〰"
    replace: "︴"
  - pattern: "!"
    replace: "︕"
  - pattern: "?"
    replace: "︖"
  - pattern: "؟"
    replace: "︖"
  - pattern: "¿"
    replace: "︖"
  - pattern: "¡"
    replace: "︕"
  - pattern: "."
    replace: "︒"
    enabled: false
  - pattern: "。"
    replace: "︒"
  - pattern: ";"
    replace: "︔"
  - pattern: "；"
    replace: "︔"
  - pattern: ":"
    replace: "︓"
  - pattern: "："
    replace: "︓"
  - pattern: ","
    replace: "︐"
  - pattern: "，"
    replace: "︐"
  - pattern: "‚"
    replace: "︐"
  - pattern: "„"
    replace: "︐"
  - pattern: "-"
    replace: "︲"
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
    """确保文本替换规则配置文件存在，如果不存在则使用内置模板创建。"""
    if os.path.exists(_DEFAULT_REPLACEMENTS_PATH):
        return _DEFAULT_REPLACEMENTS_PATH

    try:
        reset_text_replacements_to_default(_DEFAULT_REPLACEMENTS_PATH)
        logger.info(f"已创建文本替换规则文件: {_DEFAULT_REPLACEMENTS_PATH}")
    except Exception as e:
        logger.error(f"创建文本替换规则文件失败: {e}")

    return _DEFAULT_REPLACEMENTS_PATH



def _compile_rule(rule: dict):
    """编译单条规则。
    返回 ('translate', char, repl_str) 或 ('regex', compiled_pattern, repl_str) 或 None。
    单字符字面替换走 translate 路径；其余（多字符字面、正则）走 regex 路径。
    """
    pattern_str = rule.get('pattern')
    replace_str = rule.get('replace', '')
    is_regex = rule.get('regex', False)
    enabled = rule.get('enabled', True)

    if not pattern_str or not enabled:
        return None

    try:
        if not is_regex and len(pattern_str) == 1:
            return ('translate', pattern_str, replace_str)
        if is_regex:
            return ('regex', re.compile(pattern_str), replace_str)
        return ('regex', re.compile(re.escape(pattern_str)), replace_str)
    except re.error as e:
        comment = rule.get('comment', '')
        logger.warning(f"替换规则编译失败: pattern='{pattern_str}' comment='{comment}' error={e}")
        return None


def _build_group(rules) -> dict:
    """
    将一个分组的规则列表编译为执行 pipeline。
    保持原始顺序：相邻的单字符字面规则合并为一次 translate，遇到正则就 flush。

    返回 {
        'pipeline': [('translate', table) | ('regex', pattern, repl), ...],
        'translate_dict': {char: repl_str, ...}  # 扁平字典，供 build_h2v_dict / build_v2h_dict 使用
    }
    """
    pipeline: list = []
    translate_dict: Dict[str, str] = {}
    current_batch: Dict[str, str] = {}

    def flush():
        if current_batch:
            pipeline.append(('translate', str.maketrans(current_batch)))
            current_batch.clear()

    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            compiled = _compile_rule(rule)
            if not compiled:
                continue
            if compiled[0] == 'translate':
                current_batch[compiled[1]] = compiled[2]
                translate_dict[compiled[1]] = compiled[2]
            else:
                flush()
                pipeline.append(compiled)
        flush()

    return {'pipeline': pipeline, 'translate_dict': translate_dict}


def _empty_group() -> dict:
    return {'pipeline': [], 'translate_dict': {}}


def _load_and_parse(file_path: str) -> dict:
    """
    加载并解析 YAML 替换配置文件。
    返回 {'common': {...}, 'horizontal': {...}, 'vertical': {...}}，每个值由 _build_group 给出。
    """
    result = {'common': _empty_group(), 'horizontal': _empty_group(), 'vertical': _empty_group()}

    if not file_path or not os.path.exists(file_path):
        return result

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载替换配置失败: {file_path} error={e}")
        return result

    if not isinstance(data, dict):
        logger.error(f"替换配置格式错误，应为字典: {file_path}")
        return result

    for group_name in ('common', 'horizontal', 'vertical'):
        result[group_name] = _build_group(data.get(group_name, []))

    return result


def load_replacements(file_path: Optional[str] = None) -> dict:
    """加载替换规则（带文件修改时间缓存）。"""
    if file_path is None:
        file_path = _DEFAULT_REPLACEMENTS_PATH

    if not os.path.exists(file_path):
        return {'common': _empty_group(), 'horizontal': _empty_group(), 'vertical': _empty_group()}

    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        return {'common': _empty_group(), 'horizontal': _empty_group(), 'vertical': _empty_group()}

    cached = _replacements_cache.get(file_path)
    if cached and cached[0] == mtime:
        return cached[1]

    parsed = _load_and_parse(file_path)
    _replacements_cache[file_path] = (mtime, parsed)
    return parsed


def _apply_pipeline(text: str, pipeline: list) -> str:
    for step in pipeline:
        if step[0] == 'translate':
            text = text.translate(step[1])
        else:  # 'regex'
            text = step[1].sub(step[2], text)
    return text


def apply_replacements(text: str, direction: int, replacements: Optional[dict] = None,
                       file_path: Optional[str] = None) -> str:
    """
    对译文应用替换规则。
    自动跳过 [BR]、<br>、<H>...</H>、【BR】 等标记，避免标记内容被误替换。

    参数:
        text: 原始译文
        direction: 0=横排, 1=竖排
        replacements: 预加载的规则字典（可选，避免重复加载）
        file_path: YAML 配置文件路径（当 replacements 为 None 时使用）

    返回:
        替换后的文本
    """
    if not text:
        return text

    if replacements is None:
        replacements = load_replacements(file_path)

    protected_tokens: List[str] = []

    def _protect(match):
        protected_tokens.append(match.group(0))
        return f'\x00PROT{len(protected_tokens) - 1}\x00'

    text = _PROTECTED_RE.sub(_protect, text)

    # 1. 先应用 common 规则
    common = replacements.get('common') or _empty_group()
    text = _apply_pipeline(text, common.get('pipeline', []))

    # 2. 根据方向应用对应分组
    group_key = 'vertical' if direction == 1 else 'horizontal'
    directional = replacements.get(group_key) or _empty_group()
    text = _apply_pipeline(text, directional.get('pipeline', []))

    # 恢复保护的标记
    for i, token in enumerate(protected_tokens):
        text = text.replace(f'\x00PROT{i}\x00', token)

    return text


def build_h2v_dict(file_path: Optional[str] = None) -> dict:
    """
    从 YAML vertical 分组构建 CJK_H2V 兼容字典（单字符→单字符或保留原字符）。
    """
    replacements = load_replacements(file_path)
    translate_dict = (replacements.get('vertical') or _empty_group()).get('translate_dict', {})
    h2v = {}
    for char, repl in translate_dict.items():
        if len(repl) <= 1:
            h2v[char] = repl if repl else char
    return h2v


def build_v2h_dict(file_path: Optional[str] = None) -> dict:
    """
    从 YAML horizontal 分组构建 CJK_V2H 兼容字典（单字符→单字符）。
    """
    replacements = load_replacements(file_path)
    translate_dict = (replacements.get('horizontal') or _empty_group()).get('translate_dict', {})
    v2h = {}
    for char, repl in translate_dict.items():
        if len(repl) == 1:
            v2h[char] = repl
    return v2h
