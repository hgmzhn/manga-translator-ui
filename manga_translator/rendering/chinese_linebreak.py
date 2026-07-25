"""中文语义断句与排版。

用 HanLP 粗分词 + 成分句法把译文组织成语义单元树,排版时按预算逐层拆分,
使换行尽量落在语义边界上。模型缺失或推理失败时返回 None,由调用方回退
普通换行。
"""

import asyncio
import math
import os
import re
import threading
import unicodedata
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

import cv2
import numpy as np

warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*", category=FutureWarning)

from .text_render import get_char_offset_y, get_string_width
from ..utils.generic import BASE_PATH
from ..utils.log import get_logger


logger = get_logger("render")


# ---------------------------------------------------------------------------
# 模型资源
# ---------------------------------------------------------------------------

COARSE_MODEL_NAME = "coarse_electra_small_20220616_012050"
CONSTITUENCY_MODEL_NAME = "ctb9_con_electra_small_20220215_230116"
MODEL_SUB_DIR = os.path.join("rendering", "hanlp")
MODEL_DIR = os.path.join(BASE_PATH, "models", MODEL_SUB_DIR)
COARSE_MODEL_DIR = os.path.join(MODEL_DIR, COARSE_MODEL_NAME)
CONSTITUENCY_MODEL_DIR = os.path.join(MODEL_DIR, CONSTITUENCY_MODEL_NAME)

DEFAULT_MODEL_REPO_BASE_URL = "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master"
MODEL_ARCHIVE_HASHES = {
    COARSE_MODEL_NAME: "017db1fa7ecf6ba84ee6772a922260e872ca2a1da5142ec646cbc2621b8ee44e",
    CONSTITUENCY_MODEL_NAME: "e5787b1ab741362988723b5bd02ebf3b10c597f82318d304ebac61f1f6e801d5",
}


def get_chinese_linebreak_model_mapping() -> dict[str, dict[str, Any]]:
    base_url = os.environ.get("MT_HANLP_LINEBREAK_MODEL_BASE_URL", DEFAULT_MODEL_REPO_BASE_URL).rstrip("/")
    mapping: dict[str, dict[str, Any]] = {}
    for model_name in (COARSE_MODEL_NAME, CONSTITUENCY_MODEL_NAME):
        item: dict[str, Any] = {
            "url": f"{base_url}/{model_name}.zip",
            "archive": {model_name: "."},
        }
        archive_hash = MODEL_ARCHIVE_HASHES.get(model_name)
        if archive_hash:
            item["hash"] = archive_hash
        mapping[f"hanlp_{model_name}"] = item
    return mapping


async def download_chinese_linebreak_models(force: bool = False) -> bool:
    from ..utils.inference import ModelWrapper

    class ChineseLineBreakModelBundle(ModelWrapper):
        _KEY = "ChineseLineBreak"
        _MODEL_SUB_DIR = MODEL_SUB_DIR
        _MODEL_MAPPING = get_chinese_linebreak_model_mapping()

        async def _load(self, device: str, *args, **kwargs):
            return None

        async def _unload(self):
            return None

        async def _infer(self, *args, **kwargs):
            return None

    bundle = ChineseLineBreakModelBundle()
    await bundle.download(force=force)
    return bundle.is_downloaded()


async def download_chinese_linebreak_models_if_enabled(config: Any, force: bool = False) -> bool:
    render_config = getattr(config, "render", None) if config is not None else None
    if not force and not bool(getattr(render_config, "semantic_linebreak", False)):
        return False
    _log_enabled_notice_once()
    return await _ensure_chinese_linebreak_models_downloaded(force=force)


def _log_enabled_notice_once() -> None:
    global _enabled_notice_logged
    if _enabled_notice_logged:
        return
    logger.info("[中文语义断句] 检测到语义断句打开，将会进行语义断句")
    _enabled_notice_logged = True


async def _ensure_chinese_linebreak_models_downloaded(force: bool = False) -> bool:
    global _download_checked

    if _download_checked and not force:
        return chinese_linebreak_models_available()

    async with _get_download_lock():
        if _download_checked and not force:
            return chinese_linebreak_models_available()

        try:
            await download_chinese_linebreak_models(force=force)
            if not chinese_linebreak_models_available():
                logger.warning("中文语义断句 HanLP 模型未准备完整，渲染时会回退普通换行")
        except Exception as exc:
            logger.warning(f"HanLP Chinese linebreak model download failed; falling back to normal line breaking: {exc}")
        finally:
            _download_checked = True

        return chinese_linebreak_models_available()


def _get_download_lock() -> asyncio.Lock:
    global _download_lock
    if _download_lock is None:
        _download_lock = asyncio.Lock()
    return _download_lock


def chinese_linebreak_models_available() -> bool:
    for model_dir in (COARSE_MODEL_DIR, CONSTITUENCY_MODEL_DIR):
        if not os.path.isdir(model_dir):
            return False
        for filename in ("config.json", "model.pt", "vocabs.json"):
            if not os.path.isfile(os.path.join(model_dir, filename)):
                return False
    return True


def _get_models() -> Optional[tuple[Any, Any]]:
    global _tokenizer, _parser, _load_failed, _missing_models_logged
    if _load_failed:
        return None
    if _tokenizer is not None and _parser is not None:
        return _tokenizer, _parser
    if not chinese_linebreak_models_available():
        if not _missing_models_logged:
            logger.warning(f"[中文语义断句] HanLP 模型未找到，回退普通换行: {MODEL_DIR}")
            _missing_models_logged = True
        return None

    with _load_lock:
        if _tokenizer is not None and _parser is not None:
            return _tokenizer, _parser
        if _load_failed:
            return None
        try:
            warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*", category=FutureWarning)
            import hanlp

            _tokenizer = hanlp.load(COARSE_MODEL_DIR)
            _parser = hanlp.load(CONSTITUENCY_MODEL_DIR)
        except Exception as exc:
            _tokenizer = None
            _parser = None
            _load_failed = True
            logger.warning(f"[中文语义断句] HanLP 模型加载失败，回退普通换行: {exc}")
            return None
    return _tokenizer, _parser


# ---------------------------------------------------------------------------
# 字符表
# ---------------------------------------------------------------------------

OPEN_TO_CLOSE = {
    "(": ")",
    "（": "）",
    "[": "]",
    "［": "］",
    "{": "}",
    "｛": "｝",
    "【": "】",
    "〔": "〕",
    "〖": "〗",
    "〘": "〙",
    "〚": "〛",
    "「": "」",
    "『": "』",
    "｢": "｣",
    "《": "》",
    "〈": "〉",
    "⁅": "⁆",
    "⟦": "⟧",
    "⟨": "⟩",
    "⟪": "⟫",
    "⦃": "⦄",
    "⦅": "⦆",
    "⦇": "⦈",
    "⦉": "⦊",
    "⦋": "⦌",
    "⦍": "⦎",
    "⦏": "⦐",
    "⦑": "⦒",
    "⧼": "⧽",
    "︵": "︶",
    "︷": "︸",
    "︹": "︺",
    "︻": "︼",
    "︽": "︾",
    "︿": "﹀",
    "﹁": "﹂",
    "﹃": "﹄",
    "﹙": "﹚",
    "﹛": "﹜",
    "﹝": "﹞",
    "﹇": "﹈",
}
CLOSE_TO_OPEN = {close: open_ for open_, close in OPEN_TO_CLOSE.items()}

_WHITESPACE_CHARS = set(" \t　")

# 结构性断句字符:句读标点加空白。空白与逗号同级,作为独立单元参与断句;
# 换行若落在空白处,该空白会在排版收尾时被删除(见 layout_chinese_cjk)。
STRUCTURAL_BREAK_CHARS = set(
    "，、。．｡､,.!?！？；;：:﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖"
    "…‥⋯︰⋮︙︴—－–−︱︲～〜〰~≀|"
) | _WHITESPACE_CHARS

# 以下三个表由基础表派生,避免手抄多份字符清单造成遗漏
# (旧版手抄的 NO_END_CHARS 漏了 ［〔〖〘〚,NO_START_CHARS 漏了 ］〕〗〙〛)。
PHRASE_PUNCT = (
    (STRUCTURAL_BREAK_CHARS - _WHITESPACE_CHARS)
    | set(OPEN_TO_CLOSE)
    | set(CLOSE_TO_OPEN)
    | set("─│━┃═║·・﹅‚„")
)
NO_START_CHARS = (
    (STRUCTURAL_BREAK_CHARS - _WHITESPACE_CHARS)
    | set(CLOSE_TO_OPEN)
    | set("·・﹅”’〞〟＂＇»›")
)
NO_END_CHARS = set(OPEN_TO_CLOSE)

PHRASE_LABELS = {"NP", "DP", "DNP", "QP", "LCP", "CP"}
MAX_PHRASE_TOKENS = 8
SUFFIX_TOKENS = {"了", "着", "过", "的", "地", "得", "们", "吧", "呢", "吗", "啊", "哦", "呀", "啦"}
STRONG_STANDALONE_MARKS = set("!?！？︕︖⁈⁉‼…‥⋯︰⋮︙♪♫♬♡♥❤★☆")


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------

_load_lock = threading.Lock()
_tokenizer: Any = None
_parser: Any = None
_load_failed = False
_missing_models_logged = False
_download_checked = False
_enabled_notice_logged = False
_download_lock: Optional[asyncio.Lock] = None
_unit_cache: dict[str, Tuple["SemanticUnit", ...]] = {}
_MAX_UNIT_CACHE_SIZE = 2048
_inference_fallback_log_cache: set[tuple[str, str]] = set()
_MAX_INFERENCE_FALLBACK_LOG_CACHE_SIZE = 256
_BR_RE = re.compile(r"\s*(\[BR\]|<br>|【BR】)\s*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticUnit:
    text: str
    children: Tuple["SemanticUnit", ...] = ()


@dataclass(frozen=True)
class BubbleLinebreakEvaluation:
    text_with_br: str
    required_width: float
    required_height: float
    n_segments: int
    dst_points: Any
    overflow_pixels: int

    @property
    def fits(self) -> bool:
        return self.overflow_pixels == 0


@dataclass(frozen=True)
class BubbleLinebreakChoice:
    selected: Optional[BubbleLinebreakEvaluation]
    candidates: Tuple[tuple[tuple[int, int, int, float, float], BubbleLinebreakEvaluation], ...]
    evaluations: Tuple[dict[str, Any], ...] = ()


# ---------------------------------------------------------------------------
# 语义单元构建
# ---------------------------------------------------------------------------


def _semantic_units(text: str) -> Optional[Tuple[SemanticUnit, ...]]:
    cached = _unit_cache.get(text)
    if cached is not None:
        return cached

    models = _get_models()
    if models is None:
        return None
    tokenizer, parser = models

    tokens = _tokenize_for_parse(tokenizer, text)
    if tokens is None:
        return None

    try:
        units = tuple(_units_from_tree(parser(tokens)))
    except Exception as exc:
        _log_inference_fallback(text, "成分句法推理失败，使用粗分词结果继续断句", exc)
        units = tuple(SemanticUnit(token) for token in tokens)

    spaced = _inject_space_units(units, text)
    if spaced is None:
        _log_inference_fallback(text, "空白回填结果与原文不一致，回退普通换行")
        return None

    units = tuple(_wrap_brackets(_attach_suffix_tokens(list(spaced))))
    units = _structure_punctuation_boundaries(units)
    if "".join(unit.text for unit in units) != text:
        _log_inference_fallback(text, "语义树重组结果与原文不一致，回退普通换行")
        return None

    if len(_unit_cache) >= _MAX_UNIT_CACHE_SIZE:
        _unit_cache.clear()
    _unit_cache[text] = units
    return units


def _tokenize_for_parse(tokenizer: Any, text: str) -> Optional[list[str]]:
    # HanLP 粗分词会丢弃空白,因此按去空白后的原文校验;空白稍后由
    # _inject_space_units 按原文位置回填,这里保证 token 序列不含空白。
    try:
        raw_tokens = _normalize_tokens(tokenizer(text))
    except Exception as exc:
        _log_inference_fallback(text, "粗分词推理失败，回退普通换行", exc)
        return None
    tokens = [token for token in ("".join(raw.split()) for raw in raw_tokens) if token]
    if not tokens or "".join(tokens) != "".join(text.split()):
        _log_inference_fallback(text, "粗分词结果与原文不一致，回退普通换行")
        return None
    return tokens


def _normalize_tokens(tokens: Any) -> list[str]:
    if isinstance(tokens, str):
        return [tokens]
    if isinstance(tokens, (list, tuple)) and tokens and all(isinstance(sentence, (list, tuple)) for sentence in tokens):
        return [str(token) for sentence in tokens for token in sentence]
    if isinstance(tokens, (list, tuple)):
        return [str(token) for token in tokens]
    return [str(tokens)]


def _units_from_tree(node: Any) -> list[SemanticUnit]:
    if isinstance(node, str):
        return [SemanticUnit(node)]

    children = _node_children(node)
    if not children:
        leaves = _node_leaves(node)
        return [SemanticUnit(str(leaves[0]))] if len(leaves) == 1 else [SemanticUnit(str(node))]

    child_units: list[SemanticUnit] = []
    for child in children:
        child_units.extend(_units_from_tree(child))

    label = _node_label(node)
    text = "".join(unit.text for unit in child_units)
    token_count = len(_node_leaves(node))
    if (
        label in PHRASE_LABELS
        and 2 <= token_count <= MAX_PHRASE_TOKENS
        and not _contains_phrase_punct(text)
        and len(child_units) > 1
    ):
        return [SemanticUnit(text, tuple(child_units))]
    return child_units


def _node_label(node: Any) -> str:
    label = getattr(node, "label", None)
    if callable(label):
        try:
            return str(label())
        except Exception:
            return ""
    return ""


def _node_leaves(node: Any) -> list[str]:
    leaves = getattr(node, "leaves", None)
    if callable(leaves):
        try:
            return [str(token) for token in leaves()]
        except Exception:
            return []
    if isinstance(node, str):
        return [node]
    return []


def _node_children(node: Any) -> list[Any]:
    try:
        return list(node)
    except Exception:
        return []


def _contains_phrase_punct(text: str) -> bool:
    return any(char in PHRASE_PUNCT for char in text)


def _inject_space_units(units: Tuple[SemanticUnit, ...], text: str) -> Optional[Tuple[SemanticUnit, ...]]:
    """把分词时丢弃的空白按原文位置回填为独立单元。

    空白通常落在 token 边界上,作为兄弟单元插入:嵌套单元内部的空白留在该
    单元内,单元之间的空白落在最近公共祖先层。粗分词也可能把带空格的专名
    (如 "HELLO WORLD")合成一个 token,此时在叶子内部还原空白并拆出子节点。
    与原文对不齐时返回 None,由调用方回退。
    """
    rebuilt, cursor = _inject_space_walk(units, text, 0)
    if rebuilt is None:
        return None
    tail = _whitespace_run(text, cursor)
    if tail:
        rebuilt.append(SemanticUnit(tail))
        cursor += len(tail)
    return tuple(rebuilt) if cursor == len(text) else None


def _inject_space_walk(
    units: Tuple[SemanticUnit, ...], text: str, cursor: int
) -> tuple[Optional[list[SemanticUnit]], int]:
    out: list[SemanticUnit] = []
    for unit in units:
        gap = _whitespace_run(text, cursor)
        if gap:
            out.append(SemanticUnit(gap))
            cursor += len(gap)
        if unit.children:
            children, cursor = _inject_space_walk(unit.children, text, cursor)
            if children is None:
                return None, cursor
            out.append(SemanticUnit("".join(child.text for child in children), tuple(children)))
        else:
            span, cursor = _match_leaf_span(unit.text, text, cursor)
            if span is None:
                return None, cursor
            out.append(unit if span == unit.text else _leaf_unit_with_spaces(span))
    return out, cursor


def _match_leaf_span(leaf_text: str, text: str, cursor: int) -> tuple[Optional[str], int]:
    """把去除过空白的叶子 token 对齐回原文,允许原文在字符间夹带空白。

    返回 (含原文空白的片段, 新游标);对不齐返回 (None, 原游标)。
    尾随空白不消费,留给兄弟单元层处理。
    """
    start = cursor
    for char in leaf_text:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != char:
            return None, start
        cursor += 1
    return text[start:cursor], cursor


def _leaf_unit_with_spaces(span: str) -> SemanticUnit:
    parts = [part for part in re.split(r"(\s+)", span) if part]
    if len(parts) <= 1:
        return SemanticUnit(span)
    return SemanticUnit(span, tuple(SemanticUnit(part) for part in parts))


def _whitespace_run(text: str, start: int) -> str:
    end = start
    while end < len(text) and text[end].isspace():
        end += 1
    return text[start:end]


def _attach_suffix_tokens(units: list[SemanticUnit]) -> list[SemanticUnit]:
    output: list[SemanticUnit] = []
    for unit in units:
        if output and unit.text in SUFFIX_TOKENS and not output[-1].text[-1:].isspace():
            previous = output.pop()
            output.append(SemanticUnit(previous.text + unit.text, (previous, unit)))
        else:
            output.append(unit)
    return output


def _wrap_brackets(units: list[SemanticUnit]) -> list[SemanticUnit]:
    output: list[SemanticUnit] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if len(unit.text) == 1 and unit.text in OPEN_TO_CLOSE:
            close = OPEN_TO_CLOSE[unit.text]
            depth = 1
            close_index = -1
            for probe in range(index + 1, len(units)):
                probe_text = units[probe].text
                if len(probe_text) == 1 and probe_text == unit.text:
                    depth += 1
                elif len(probe_text) == 1 and probe_text == close:
                    depth -= 1
                    if depth == 0:
                        close_index = probe
                        break
            if close_index > index + 1:
                inner = _wrap_brackets(units[index + 1:close_index])
                output.append(_make_bracket_unit(unit.text, inner, close))
                index = close_index + 1
                continue
        output.append(unit)
        index += 1
    return output


def _make_bracket_unit(open_bracket: str, inner: list[SemanticUnit], close_bracket: str) -> SemanticUnit:
    text = open_bracket + "".join(unit.text for unit in inner) + close_bracket
    if not inner:
        return SemanticUnit(text)

    children = [
        _affix_unit(
            unit,
            prefix=open_bracket if idx == 0 else "",
            suffix=close_bracket if idx == len(inner) - 1 else "",
        )
        for idx, unit in enumerate(inner)
    ]
    return SemanticUnit(text, tuple(children))


def _affix_unit(unit: SemanticUnit, *, prefix: str = "", suffix: str = "") -> SemanticUnit:
    text = prefix + unit.text + suffix
    if not unit.children or "".join(child.text for child in unit.children) != unit.text:
        return SemanticUnit(text)

    children = [
        _affix_unit(
            child,
            prefix=prefix if idx == 0 else "",
            suffix=suffix if idx == len(unit.children) - 1 else "",
        )
        for idx, child in enumerate(unit.children)
    ]
    return SemanticUnit(text, tuple(children))


def _structure_punctuation_boundaries(units: Tuple[SemanticUnit, ...]) -> Tuple[SemanticUnit, ...]:
    units = tuple(_structure_unit_children(unit) for unit in units)
    for index, unit in enumerate(units):
        if not _is_structural_break_unit(unit):
            continue

        left_items = units[:index]
        right_items = units[index + 1:]
        if not left_items or not right_items:
            continue

        left_tree = _group_semantic_units(left_items + (unit,))
        right_tree = _group_semantic_units(_structure_punctuation_boundaries(right_items))
        return (left_tree, right_tree)

    return units


def _structure_unit_children(unit: SemanticUnit) -> SemanticUnit:
    if not unit.children:
        return unit

    children = _structure_punctuation_boundaries(unit.children)
    if children == unit.children:
        return unit

    text = "".join(child.text for child in children)
    if text != unit.text:
        return unit
    return SemanticUnit(unit.text, children)


def _group_semantic_units(units: Tuple[SemanticUnit, ...]) -> SemanticUnit:
    if len(units) == 1:
        return units[0]
    return SemanticUnit("".join(unit.text for unit in units), units)


def _is_structural_break_unit(unit: SemanticUnit) -> bool:
    if unit.children:
        return False
    visible = unit.text.strip()
    if not visible:
        # 纯空白单元:与逗号同级的断句点(换行落在这里时空白会被删除)。
        return bool(unit.text)
    return all(char in STRUCTURAL_BREAK_CHARS for char in visible)


def _log_inference_fallback(text: str, reason: str, exc: Optional[Exception] = None) -> None:
    key = (reason, text)
    if key in _inference_fallback_log_cache:
        return
    if len(_inference_fallback_log_cache) >= _MAX_INFERENCE_FALLBACK_LOG_CACHE_SIZE:
        _inference_fallback_log_cache.clear()
    _inference_fallback_log_cache.add(key)

    message = f"[中文语义断句] {reason}: {_compact_log_text(text)}"
    if exc is not None:
        message += f" ({type(exc).__name__}: {exc})"
    logger.warning(message)


def _compact_log_text(text: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "..."


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------


def layout_chinese_cjk(
    font_size: int,
    text: str,
    max_budget: int,
    *,
    horizontal: bool,
    letter_spacing: float = 1.0,
) -> Optional[Tuple[list[str], list[int]]]:
    if not text or max_budget <= 0:
        return None

    text = _BR_RE.sub("\n", text)
    measure = _make_measure(font_size, horizontal, letter_spacing)
    lines: list[str] = []
    metrics: list[int] = []

    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            metrics.append(0)
            continue

        units = _semantic_units(paragraph)
        if not units:
            return None

        para_lines = _layout_units(units, max(1, int(max_budget)), measure)
        if not para_lines:
            return None
        para_lines = _avoid_single_char_lines(para_lines, max(1, int(max_budget)), measure)
        # 行首/行尾空白只在换行恰好落在空白处时出现,按规则删除;行内空白保留。
        # 必须在单字行合并之后再剥,否则合并回同一行时会丢失词间空格。
        para_lines = [line for line in (line.strip() for line in para_lines) if line] or [""]
        lines.extend(para_lines)
        metrics.extend(int(measure(line)) for line in para_lines)

    return lines, metrics


def layout_chinese_cjk_candidates(
    font_size: int,
    text: str,
    budgets: list[int],
    *,
    horizontal: bool,
    letter_spacing: float = 1.0,
) -> list[Tuple[int, list[str], list[int]]]:
    candidates: list[Tuple[int, list[str], list[int]]] = []
    seen: set[str] = set()
    for budget in budgets:
        layout = layout_chinese_cjk(
            font_size,
            text,
            max(1, int(budget)),
            horizontal=horizontal,
            letter_spacing=letter_spacing,
        )
        if not layout:
            continue
        lines, metrics = layout
        key = "\n".join(lines)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((budget, lines, metrics))
    return candidates


def _make_measure(font_size: int, horizontal: bool, letter_spacing: float) -> Callable[[str], int]:
    if horizontal:
        def measure_horizontal(value: str) -> int:
            return int(get_string_width(font_size, value, letter_spacing=letter_spacing))

        return measure_horizontal

    def measure_vertical(value: str) -> int:
        return int(sum(max(0, get_char_offset_y(font_size, char, letter_spacing=letter_spacing)) for char in value))

    return measure_vertical


def _layout_units(units: Tuple[SemanticUnit, ...], max_budget: int, measure: Callable[[str], int]) -> list[str]:
    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    def add_text(value: str) -> None:
        nonlocal current
        if not value:
            return
        if current:
            current += value
        elif lines and _must_attach_to_previous(value):
            lines[-1] += value
        else:
            current = value

    def place(unit: SemanticUnit, depth: int = 0) -> None:
        if not unit.text:
            return

        if (
            current
            and measure(current + unit.text) > max_budget
            and not _must_attach_to_previous(unit.text)
            and not _must_attach_next(current)
        ):
            flush()

        children = _split_unit(unit) if depth < 32 else ()
        if children and measure(unit.text) > max_budget:
            flush()
            for child in children:
                place(child, depth + 1)
            return

        add_text(unit.text)

    for unit in units:
        place(unit)

    flush()
    return [line for line in lines if line or len(lines) == 1]


def _split_unit(unit: SemanticUnit) -> Tuple[SemanticUnit, ...]:
    if unit.children and "".join(child.text for child in unit.children) == unit.text:
        return unit.children
    return ()


def _must_attach_to_previous(text: str) -> bool:
    return bool(text) and (text[0] in NO_START_CHARS or text[0] in SUFFIX_TOKENS)


def _must_attach_next(text: str) -> bool:
    return bool(text) and text[-1] in NO_END_CHARS


def _avoid_single_char_lines(
    lines: list[str],
    max_budget: int,
    measure: Callable[[str], int],
) -> list[str]:
    if len(lines) <= 1:
        return lines

    output = [line for line in lines if line or len(lines) == 1]
    index = 0
    while index < len(output):
        line = output[index]
        if not _is_weak_single_char_line(line, index):
            index += 1
            continue

        if len(output) == 1:
            break

        prev_score: Optional[tuple[int, int]] = None
        next_score: Optional[tuple[int, int]] = None
        if index > 0:
            merged_prev = output[index - 1] + line
            prev_score = (max(0, int(measure(merged_prev)) - max_budget), 0)
        if index + 1 < len(output):
            merged_next = line + output[index + 1]
            next_score = (max(0, int(measure(merged_next)) - max_budget), 1)

        if prev_score is not None and (next_score is None or prev_score <= next_score):
            output[index - 1] += output[index]
            del output[index]
            index = max(0, index - 1)
        elif next_score is not None:
            output[index] += output[index + 1]
            del output[index + 1]
            index = max(0, index - 1)
        else:
            index += 1

    return output


def _is_weak_single_char_line(line: str, line_index: int = 0) -> bool:
    visible = line.strip()
    if not visible:
        return False
    content_count = sum(1 for char in visible if _is_content_char(char))
    if content_count == 0:
        return True
    if content_count == 1:
        has_strong_mark = any(char in STRONG_STANDALONE_MARKS for char in visible)
        return line_index > 0 if has_strong_mark else True
    return False


def _is_content_char(char: str) -> bool:
    if char.isspace():
        return False
    category = unicodedata.category(char)
    return category[:1] in {"L", "N"}


# ---------------------------------------------------------------------------
# 候选生成与评分
# ---------------------------------------------------------------------------


def choose_chinese_bubble_linebreak_with_trace(
    *,
    source_text: str,
    current_text: str,
    font_size: int,
    target_segments: int,
    total_budget: float,
    line_budget: float,
    horizontal: bool,
    letter_spacing: float,
    evaluate: Callable[[str], Optional[BubbleLinebreakEvaluation]],
) -> Optional[BubbleLinebreakChoice]:
    budgets = _bubble_candidate_budgets(total_budget, line_budget, target_segments)
    candidate_items: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    def add_candidate(candidate_text: str, source: dict[str, Any]) -> None:
        if not candidate_text or candidate_text in seen:
            return
        seen.add(candidate_text)
        candidate_items.append((candidate_text, source))

    add_candidate(current_text, {"source": "current"})
    for budget, lines, metrics in layout_chinese_cjk_candidates(
        font_size,
        source_text,
        budgets,
        horizontal=horizontal,
        letter_spacing=letter_spacing,
    ):
        add_candidate(
            "[BR]".join(lines),
            {"source": "budget", "budget": budget, "lines": lines, "metrics": metrics},
        )

    scored_candidates: list[tuple[tuple[int, int, int, float, float], BubbleLinebreakEvaluation]] = []
    evaluations: list[dict[str, Any]] = []
    target = max(1, int(target_segments))

    for index, (candidate_text, source) in enumerate(candidate_items, start=1):
        semantic_penalty = candidate_semantic_break_penalty(source_text, candidate_text)
        uniformity = _line_text_uniformity(candidate_text)
        evaluated = evaluate(candidate_text)
        if evaluated is None:
            evaluations.append(
                {
                    "index": index,
                    **source,
                    "text_with_br": candidate_text,
                    "semantic_penalty": semantic_penalty,
                    "uniformity": uniformity,
                    "accepted": False,
                    "filter_reason": "evaluate_failed",
                }
            )
            continue

        base_record = {
            "index": index,
            **source,
            "text_with_br": candidate_text,
            "segments": int(evaluated.n_segments),
            "required": {"width": float(evaluated.required_width), "height": float(evaluated.required_height)},
            "fits": bool(evaluated.fits),
            "overflow_pixels": int(evaluated.overflow_pixels),
            "semantic_penalty": semantic_penalty,
            "uniformity": uniformity,
        }
        if int(evaluated.n_segments) != target:
            evaluations.append(
                {
                    **base_record,
                    "accepted": False,
                    "filter_reason": "target_segments",
                    "expected_segments": target,
                }
            )
            continue

        score = (
            0 if evaluated.fits else 1,
            semantic_penalty,
            0 if evaluated.fits else int(evaluated.overflow_pixels),
            uniformity,
            -float(_longest_line_len(candidate_text)),
        )
        evaluations.append({**base_record, "accepted": True, "score": list(score)})
        scored_candidates.append((score, evaluated))

    if not scored_candidates:
        return BubbleLinebreakChoice(None, tuple(), tuple(evaluations))

    scored_candidates.sort(key=lambda item: item[0])
    selected_text = scored_candidates[0][1].text_with_br
    evaluations = [
        {**item, "selected": item.get("text_with_br") == selected_text}
        for item in evaluations
    ]
    return BubbleLinebreakChoice(scored_candidates[0][1], tuple(scored_candidates), tuple(evaluations))


def _bubble_candidate_budgets(total_budget: float, line_budget: float, target_segments: int) -> list[int]:
    target = max(1, int(target_segments))
    base_budget = float(total_budget) / target if total_budget and math.isfinite(total_budget) else 1.0
    raw_budgets = {
        float(line_budget),
        float(total_budget),
        float(base_budget),
    }
    for factor in (0.7, 0.85, 1.0, 1.15, 1.35, 1.6, 2.0):
        raw_budgets.add(float(base_budget * factor))
    for factor in (0.8, 1.0, 1.2, 1.5):
        raw_budgets.add(float(line_budget * factor))
    return sorted({
        max(1, int(round(value)))
        for value in raw_budgets
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    })


def candidate_semantic_break_penalty(source_text: str, text_with_br: str) -> int:
    """候选断行相对原文的语义边界代价。

    候选的每一行必须与原文逐字符对齐,唯一允许的差异是换行处被删掉的
    原文空白(含换行符);对不齐即视为内容被篡改,返回 1000000。断在空白处
    时,取空白两端边界代价中较小的一个。
    """
    source = source_text or ""
    lines = _split_br_text(text_with_br)
    boundary_costs = _semantic_boundary_costs(source)
    penalty = 0
    cursor = 0

    for index, line in enumerate(lines):
        if source[cursor:cursor + len(line)] != line:
            return 1000000
        cursor += len(line)
        gap_start = cursor
        cursor += len(_whitespace_run(source, cursor))
        if index < len(lines) - 1:
            positions = [pos for pos in {gap_start, cursor} if 0 < pos < len(source)]
            if positions:
                penalty += min(boundary_costs.get(pos, 8) for pos in positions)

    return penalty if cursor == len(source) else 1000000


def _semantic_boundary_costs(text: str) -> dict[int, int]:
    units = _semantic_units(text)
    if units is None:
        return {}

    costs: dict[int, int] = {}

    def set_cost(position: int, cost: int) -> None:
        if position <= 0 or position >= len(text):
            return
        previous = costs.get(position)
        if previous is None or cost < previous:
            costs[position] = cost

    def visit(items: Tuple[SemanticUnit, ...], start: int, depth: int) -> int:
        cursor = start
        for unit in items:
            unit_start = cursor
            unit_end = unit_start + len(unit.text)
            set_cost(unit_end, depth)
            if unit.children:
                visit(unit.children, unit_start, depth + 1)
            cursor = unit_end
        return cursor

    visit(units, 0, 0)
    return costs


def _line_text_uniformity(text_with_br: str) -> float:
    lines = _split_br_text(text_with_br)
    if len(lines) <= 1:
        return 0.0
    lengths = [_visible_line_length(line) for line in lines]
    if not lengths or sum(lengths) <= 0:
        return float("inf")
    mean = sum(lengths) / len(lengths)
    variance = sum((length - mean) ** 2 for length in lengths) / len(lengths)
    return (variance ** 0.5) / mean if mean > 0 else float("inf")


def _longest_line_len(text_with_br: str) -> int:
    lengths = [_visible_line_length(line) for line in _split_br_text(text_with_br)]
    return max(lengths, default=0)


def _visible_line_length(line: str) -> int:
    return sum(1 for char in line if not char.isspace())


def _split_br_text(text: str) -> list[str]:
    return [line for line in _BR_RE.split(text or "") if not _BR_RE.fullmatch(line or "")]


def bubble_mask_overflow_pixels(dst_points: Any, bubble_mask: Any) -> int:
    if dst_points is None or bubble_mask is None:
        return 1 << 60

    dst_array = np.asarray(dst_points)
    mask_array = np.asarray(bubble_mask)
    if dst_array.size == 0 or mask_array.size == 0:
        return 1 << 60

    polygon = dst_array[0] if dst_array.ndim == 3 else dst_array
    polygon = np.asarray(polygon).reshape(-1, 2)
    if polygon.shape[0] < 3:
        return 1 << 60

    candidate_mask = np.zeros(mask_array.shape[:2], dtype=np.uint8)
    cv2.fillPoly(candidate_mask, [np.rint(polygon).astype(np.int32)], 255)
    candidate_pixels = candidate_mask > 0
    if int(np.count_nonzero(candidate_pixels)) <= 0:
        return 1 << 60
    return int(np.count_nonzero(candidate_pixels & (mask_array <= 0)))


# ---------------------------------------------------------------------------
# 调试快照与调试记录(仅供调试面板/调试 JSON 输出使用)
# ---------------------------------------------------------------------------


def append_chinese_linebreak_debug_record(config: Any, record: dict[str, Any]) -> None:
    records = getattr(config, "_chinese_linebreak_debug_records", None) if config is not None else None
    if not isinstance(records, list):
        return
    records.append(_json_safe_value(record))


def build_chinese_linebreak_debug_snapshot(
    text: str,
    *,
    font_size: int,
    target_segments: int,
    total_budget: float,
    line_budget: float,
    horizontal: bool,
    letter_spacing: float = 1.0,
) -> dict[str, Any]:
    budgets = _bubble_candidate_budgets(total_budget, line_budget, target_segments)
    snapshot: dict[str, Any] = {
        "models_available": chinese_linebreak_models_available(),
        "model_dirs": {
            "tokenizer": COARSE_MODEL_DIR,
            "constituency": CONSTITUENCY_MODEL_DIR,
        },
        "font_size": font_size,
        "target_segments": target_segments,
        "total_budget": float(total_budget),
        "line_budget": float(line_budget),
        "direction": "h" if horizontal else "v",
        "letter_spacing": float(letter_spacing),
        "candidate_budgets": budgets,
        "constituency_tree": None,
        "semantic_units": None,
        "candidate_layouts": [],
    }

    models = _get_models()
    if models is not None and text:
        tokenizer, parser = models
        tokens = _tokenize_for_parse(tokenizer, text)
        if tokens:
            try:
                snapshot["constituency_tree"] = str(parser(tokens))
            except Exception as exc:
                snapshot["constituency_tree_error"] = f"{type(exc).__name__}: {exc}"

    units = _semantic_units(text or "")
    if units is not None:
        snapshot["semantic_units"] = [_semantic_unit_to_debug(unit) for unit in units]

    seen: set[str] = set()
    for budget in budgets:
        layout = layout_chinese_cjk(
            font_size,
            text,
            max(1, int(budget)),
            horizontal=horizontal,
            letter_spacing=letter_spacing,
        )
        if not layout:
            snapshot["candidate_layouts"].append({"budget": budget, "available": False})
            continue
        lines, metrics = layout
        candidate_text = "[BR]".join(lines)
        duplicate = candidate_text in seen
        seen.add(candidate_text)
        snapshot["candidate_layouts"].append(
            {
                "budget": budget,
                "available": True,
                "duplicate": duplicate,
                "lines": lines,
                "metrics": metrics,
                "text_with_br": candidate_text,
                "segments": len(lines),
                "semantic_penalty": candidate_semantic_break_penalty(text, candidate_text),
            }
        )

    return _json_safe_value(snapshot)


def _semantic_unit_to_debug(unit: SemanticUnit) -> dict[str, Any]:
    return {
        "text": unit.text,
        "children": [_semantic_unit_to_debug(child) for child in unit.children],
    }


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe_value(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)
