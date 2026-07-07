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

from .text_render import calc_horizontal_block_height, get_char_offset_y, get_string_width
from ..utils.generic import BASE_PATH
from ..utils.log import get_logger


logger = get_logger("render")


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

PHRASE_LABELS = {"NP", "DP", "DNP", "QP", "LCP", "CP"}
MAX_PHRASE_TOKENS = 8

PHRASE_PUNCT = set(
    "，。！？；：、,.!?;:．｡､﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖"
    "…‥⋯︰⋮︙︴～〜〰—－–−︱︲─│━┃═║~≀|·・﹅‚„"
    "()（）[]［］{}｛｝【】〔〕〖〗〘〙〚〛"
    "「」『』｢｣《》〈〉"
    "⁅⁆⟦⟧⟨⟩⟪⟫⦃⦄⦅⦆⦇⦈⦉⦊⦋⦌⦍⦎⦏⦐⦑⦒⧼⧽"
    "︵︶︷︸︹︺︻︼︽︾︿﹀"
    "﹁﹂﹃﹄﹙﹚﹛﹜﹝﹞﹇﹈"
)

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
NO_START_CHARS = set(
    "，、。．｡､,.!?！？；;：:﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖"
    "…‥⋯︰⋮︙︴—－–−︱︲～〜〰~≀|·・﹅"
    "”’〞〟＂＇»›"
    "》，」』】）﹂﹄︶︸︺︼︾﹀﹚﹜﹞﹈)]｝｣》〉"
    "⁆⟧⟩⟫⦄⦆⦈⦊⦌⦎⦐⦒⧽"
)
NO_END_CHARS = set("《「『【（﹁﹃︵︷︹︻︽︿﹙﹛﹝﹇([{｛｢〈⁅⟦⟨⟪⦃⦅⦇⦉⦋⦍⦏⦑⧼")
SUFFIX_TOKENS = {"了", "着", "过", "的", "地", "得", "们", "吧", "呢", "吗", "啊", "哦", "呀", "啦"}
STRONG_STANDALONE_MARKS = set("!?！？︕︖⁈⁉‼…‥⋯︰⋮︙♪♫♬♡♥❤★☆")
PREFERRED_BREAK_CHARS = set("，、。．｡､,.!?！？；;：:﹐﹑﹒﹔﹕﹖﹗︐︑︒︓︔︕︖…‥⋯︰⋮︙︴—－–−︱︲～〜〰~≀|")

_load_lock = threading.Lock()
_tokenizer: Any = None
_parser: Any = None
_load_failed = False
_missing_models_logged = False
_load_started_logged = False
_load_success_logged = False
_unit_cache: dict[str, Tuple["SemanticUnit", ...]] = {}
_MAX_UNIT_CACHE_SIZE = 2048
_H_BLOCK_RE = re.compile(r"(<H>.*?</H>)", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"\s*(\[BR\]|<br>|【BR】)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class SemanticUnit:
    text: str
    children: Tuple["SemanticUnit", ...] = ()
    protected: bool = False


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


@dataclass(frozen=True)
class _HBlock:
    start: int
    end: int
    tagged_text: str
    content: str


@dataclass(frozen=True)
class _RangedUnit:
    start: int
    end: int
    unit: SemanticUnit


def append_chinese_linebreak_debug_record(config: Any, record: dict[str, Any]) -> None:
    records = getattr(config, "_chinese_linebreak_debug_records", None) if config is not None else None
    if not isinstance(records, list):
        return
    records.append(_json_safe_value(record))


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


def chinese_linebreak_models_available() -> bool:
    for model_dir in (COARSE_MODEL_DIR, CONSTITUENCY_MODEL_DIR):
        if not os.path.isdir(model_dir):
            return False
        for filename in ("config.json", "model.pt", "vocabs.json"):
            if not os.path.isfile(os.path.join(model_dir, filename)):
                return False
    return True


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

    visible_text, h_blocks = _hide_h_tags(text or "")
    models = _get_models()
    if models is not None and visible_text:
        tokenizer, parser = models
        try:
            tokens = _normalize_tokens(tokenizer(visible_text))
            if tokens and "".join(tokens) == visible_text:
                try:
                    snapshot["constituency_tree"] = str(parser(tokens))
                except Exception as exc:
                    snapshot["constituency_tree_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            snapshot["constituency_tokenize_error"] = f"{type(exc).__name__}: {exc}"

    units = _protected_semantic_units(text or "")
    if units is not None:
        snapshot["semantic_units"] = [_semantic_unit_to_debug(unit) for unit in units]
    elif h_blocks:
        snapshot["h_blocks"] = [
            {"start": block.start, "end": block.end, "tagged_text": block.tagged_text, "content": block.content}
            for block in h_blocks
        ]

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
        "protected": bool(unit.protected),
        "children": [_semantic_unit_to_debug(child) for child in unit.children],
    }


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
        if not paragraph:
            lines.append("")
            metrics.append(0)
            continue

        units = _protected_semantic_units(paragraph)
        if not units:
            return None

        para_lines = _layout_units(units, max(1, int(max_budget)), measure)
        if not para_lines:
            return None
        para_lines = _avoid_single_char_lines(para_lines, max(1, int(max_budget)), measure)
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


def choose_chinese_bubble_linebreak(
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
) -> Optional[BubbleLinebreakEvaluation]:
    choice = choose_chinese_bubble_linebreak_with_trace(
        source_text=source_text,
        current_text=current_text,
        font_size=font_size,
        target_segments=target_segments,
        total_budget=total_budget,
        line_budget=line_budget,
        horizontal=horizontal,
        letter_spacing=letter_spacing,
        evaluate=evaluate,
    )
    return choice.selected if choice is not None and choice.selected is not None else None


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


def candidate_semantic_break_penalty(source_text: str, text_with_br: str) -> int:
    plain_text = _BR_RE.sub("", text_with_br or "")
    if plain_text != (source_text or ""):
        return 1000000

    boundary_costs = _semantic_boundary_costs(source_text)
    penalty = 0
    cursor = 0
    lines = _split_br_text(text_with_br)
    for line in lines[:-1]:
        cursor += len(line)
        if cursor <= 0 or cursor >= len(plain_text):
            continue
        penalty += boundary_costs.get(cursor, 8)

    return penalty


def _semantic_boundary_costs(text: str) -> dict[int, int]:
    units = _protected_semantic_units(text)
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


def _longest_line_len(text_with_br: str) -> int:
    lengths = [_visible_line_length(line) for line in _split_br_text(text_with_br)]
    return max(lengths, default=0)


def _visible_line_length(line: str) -> int:
    return sum(1 for char in _visible_line_text(line) if not char.isspace())


def _split_br_text(text: str) -> list[str]:
    return [line for line in _BR_RE.split(text or "") if not _BR_RE.fullmatch(line or "")]


def _make_measure(font_size: int, horizontal: bool, letter_spacing: float) -> Callable[[str], int]:
    if horizontal:
        def measure_horizontal(value: str) -> int:
            total = 0
            for part in _H_BLOCK_RE.split(value):
                if not part:
                    continue
                if _is_h_block(part):
                    total += int(get_string_width(font_size, _h_block_content(part), letter_spacing=letter_spacing))
                else:
                    total += int(get_string_width(font_size, part, letter_spacing=letter_spacing))
            return total

        return measure_horizontal

    def measure_vertical(value: str) -> int:
        total = 0
        for part in _H_BLOCK_RE.split(value):
            if not part:
                continue
            if _is_h_block(part):
                total += int(calc_horizontal_block_height(font_size, _h_block_content(part), letter_spacing=letter_spacing))
            else:
                total += int(sum(max(0, get_char_offset_y(font_size, char, letter_spacing=letter_spacing)) for char in part))
        return total

    return measure_vertical


def _get_models() -> Optional[tuple[Any, Any]]:
    global _tokenizer, _parser, _load_failed, _missing_models_logged, _load_started_logged, _load_success_logged
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
            if not _load_started_logged:
                logger.info(f"[中文语义断句] 加载 HanLP 模型: {MODEL_DIR}")
                _load_started_logged = True
            import warnings

            warnings.filterwarnings("ignore", message=".*pynvml package is deprecated.*", category=FutureWarning)
            import hanlp

            _tokenizer = hanlp.load(COARSE_MODEL_DIR)
            _parser = hanlp.load(CONSTITUENCY_MODEL_DIR)
            if not _load_success_logged:
                logger.info("[中文语义断句] HanLP 模型加载完成")
                _load_success_logged = True
        except Exception as exc:
            _tokenizer = None
            _parser = None
            _load_failed = True
            logger.warning(f"[中文语义断句] HanLP 模型加载失败，回退普通换行: {exc}")
            return None
    return _tokenizer, _parser


def _semantic_units(text: str) -> Optional[Tuple[SemanticUnit, ...]]:
    cached = _unit_cache.get(text)
    if cached is not None:
        return cached

    models = _get_models()
    if models is None:
        return None

    tokenizer, parser = models
    try:
        tokens = _normalize_tokens(tokenizer(text))
    except Exception:
        return None
    if not tokens or "".join(tokens) != text:
        return None

    try:
        tree = parser(tokens)
        units = tuple(_units_from_tree(tree))
    except Exception:
        units = tuple(SemanticUnit(token) for token in tokens)

    units = tuple(_wrap_brackets(_attach_suffix_tokens(list(units))))
    if "".join(unit.text for unit in units) != text:
        return None

    if len(_unit_cache) >= _MAX_UNIT_CACHE_SIZE:
        _unit_cache.clear()
    _unit_cache[text] = units
    return units


def _protected_semantic_units(text: str) -> Optional[Tuple[SemanticUnit, ...]]:
    visible_text, h_blocks = _hide_h_tags(text)
    if not h_blocks:
        return _semantic_units(text)
    if not visible_text:
        return (SemanticUnit(text, protected=True),) if text else ()

    units = _semantic_units(visible_text)
    if units is None:
        return None

    restored = _restore_h_blocks(units, h_blocks)
    if "".join(unit.text for unit in restored) != text:
        return None
    return restored


def _hide_h_tags(text: str) -> tuple[str, Tuple[_HBlock, ...]]:
    visible_parts: list[str] = []
    h_blocks: list[_HBlock] = []
    source_pos = 0
    visible_pos = 0

    for match in _H_BLOCK_RE.finditer(text):
        prefix = text[source_pos:match.start()]
        visible_parts.append(prefix)
        visible_pos += len(prefix)

        tagged_text = match.group(0)
        content = _h_block_content(tagged_text)
        visible_parts.append(content)
        h_blocks.append(_HBlock(visible_pos, visible_pos + len(content), tagged_text, content))
        visible_pos += len(content)
        source_pos = match.end()

    suffix = text[source_pos:]
    visible_parts.append(suffix)
    return "".join(visible_parts), tuple(h_blocks)


def _restore_h_blocks(units: Tuple[SemanticUnit, ...], h_blocks: Tuple[_HBlock, ...]) -> Tuple[SemanticUnit, ...]:
    ranged_units = _restore_h_blocks_ranged(units, h_blocks, 0)
    return tuple(ranged.unit for ranged in ranged_units)


def _restore_h_blocks_ranged(
    units: Tuple[SemanticUnit, ...],
    h_blocks: Tuple[_HBlock, ...],
    base_start: int,
) -> Tuple[_RangedUnit, ...]:
    ranged_units: list[_RangedUnit] = []
    cursor = base_start

    for unit in units:
        start = cursor
        end = start + len(unit.text)
        cursor = end

        if unit.children:
            children = _restore_h_blocks_ranged(unit.children, h_blocks, start)
            text = "".join(child.unit.text for child in children)
            ranged_units.append(_RangedUnit(start, end, SemanticUnit(text, tuple(child.unit for child in children), unit.protected)))
        else:
            ranged_units.extend(_split_leaf_at_h_boundaries(unit, h_blocks, start, end))

    return _merge_h_blocks_at_level(tuple(ranged_units), h_blocks)


def _split_leaf_at_h_boundaries(
    unit: SemanticUnit,
    h_blocks: Tuple[_HBlock, ...],
    start: int,
    end: int,
) -> list[_RangedUnit]:
    boundaries = {start, end}
    for block in h_blocks:
        if start < block.start < end:
            boundaries.add(block.start)
        if start < block.end < end:
            boundaries.add(block.end)

    points = sorted(boundaries)
    if len(points) <= 2:
        return [_RangedUnit(start, end, unit)]

    output: list[_RangedUnit] = []
    for left, right in zip(points, points[1:]):
        if left == right:
            continue
        offset_left = left - start
        offset_right = right - start
        output.append(_RangedUnit(left, right, SemanticUnit(unit.text[offset_left:offset_right], protected=unit.protected)))
    return output


def _merge_h_blocks_at_level(
    ranged_units: Tuple[_RangedUnit, ...],
    h_blocks: Tuple[_HBlock, ...],
) -> Tuple[_RangedUnit, ...]:
    if not ranged_units:
        return ()

    output: list[_RangedUnit] = []
    index = 0
    while index < len(ranged_units):
        ranged = ranged_units[index]
        block = _covering_h_block(ranged.start, ranged.end, h_blocks)
        if block is None:
            output.append(ranged)
            index += 1
            continue

        end_index = index
        while end_index < len(ranged_units):
            candidate = ranged_units[end_index]
            if not (block.start <= candidate.start and candidate.end <= block.end):
                break
            end_index += 1

        output.append(_RangedUnit(block.start, block.end, SemanticUnit(block.tagged_text, protected=True)))
        index = end_index

    return tuple(output)


def _covering_h_block(start: int, end: int, h_blocks: Tuple[_HBlock, ...]) -> Optional[_HBlock]:
    if start == end:
        return None
    for block in h_blocks:
        if block.start < block.end and block.start <= start and end <= block.end:
            return block
    return None


def _is_h_block(text: str) -> bool:
    return text.lower().startswith("<h>") and text.lower().endswith("</h>")


def _h_block_content(text: str) -> str:
    return text[3:-4] if len(text) >= 7 else ""


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


def _attach_suffix_tokens(units: list[SemanticUnit]) -> list[SemanticUnit]:
    output: list[SemanticUnit] = []
    for unit in units:
        if output and unit.text in SUFFIX_TOKENS:
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
        return SemanticUnit(text, protected=unit.protected)

    children = [
        _affix_unit(
            child,
            prefix=prefix if idx == 0 else "",
            suffix=suffix if idx == len(unit.children) - 1 else "",
        )
        for idx, child in enumerate(unit.children)
    ]
    return SemanticUnit(text, tuple(children), unit.protected)


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
        nonlocal current
        if not unit.text:
            return

        unit_width = measure(unit.text)
        children = _split_unit(unit) if depth < 32 else ()

        if current:
            candidate = current + unit.text
            if measure(candidate) > max_budget and not _must_attach_to_previous(unit.text) and not _must_attach_next(current):
                if children:
                    for child in children:
                        place(child, depth + 1)
                    return
                break_index = _preferred_punctuation_break_index(candidate, max_budget, measure)
                if break_index is not None and break_index >= len(current):
                    lines.append(candidate[:break_index])
                    current = candidate[break_index:]
                    return
                flush()

        if unit_width > max_budget and children:
            if current:
                flush()
            for child in children:
                place(child, depth + 1)
            return

        add_text(unit.text)

    for unit in units:
        place(unit)

    flush()
    return [line for line in lines if line or len(lines) == 1]


def _preferred_punctuation_break_index(
    text: str,
    max_budget: int,
    measure: Callable[[str], int],
) -> Optional[int]:
    best: Optional[int] = None
    for index in _preferred_punctuation_break_indices(text):
        if index <= 0 or index >= len(text):
            continue
        prefix = text[:index]
        suffix = text[index:]
        if not prefix or not suffix:
            continue
        if suffix[0] in NO_START_CHARS:
            continue
        if measure(prefix) <= max_budget:
            best = index
    return best


def _preferred_punctuation_break_indices(text: str) -> list[int]:
    indices: list[int] = []
    cursor = 0
    for match in _H_BLOCK_RE.finditer(text):
        indices.extend(_preferred_punctuation_break_indices_plain(text[cursor:match.start()], cursor))
        cursor = match.end()
    indices.extend(_preferred_punctuation_break_indices_plain(text[cursor:], cursor))
    return indices


def _preferred_punctuation_break_indices_plain(text: str, offset: int) -> list[int]:
    indices: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char not in PREFERRED_BREAK_CHARS:
            index += 1
            continue

        break_at = index + 1
        while break_at < len(text) and text[break_at] in NO_START_CHARS:
            break_at += 1
        indices.append(offset + break_at)
        index = break_at
    return indices


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
    visible = _visible_line_text(line).strip()
    if not visible:
        return False
    content_count = sum(1 for char in visible if _is_content_char(char))
    if content_count == 0:
        return True
    if content_count == 1:
        has_strong_mark = any(char in STRONG_STANDALONE_MARKS for char in visible)
        return line_index > 0 if has_strong_mark else True
    return False


def _visible_line_text(line: str) -> str:
    parts: list[str] = []
    for part in _H_BLOCK_RE.split(line):
        if not part:
            continue
        parts.append(_h_block_content(part) if _is_h_block(part) else part)
    return "".join(parts)


def _is_content_char(char: str) -> bool:
    if char.isspace():
        return False
    category = unicodedata.category(char)
    return category[:1] in {"L", "N"}


def _split_unit(unit: SemanticUnit) -> Tuple[SemanticUnit, ...]:
    if unit.protected:
        return ()
    if unit.children and "".join(child.text for child in unit.children) == unit.text:
        return unit.children
    return ()


def _char_units(text: str) -> list[SemanticUnit]:
    if len(text) <= 1:
        return [SemanticUnit(text)] if text else []

    if text[0] in OPEN_TO_CLOSE and OPEN_TO_CLOSE[text[0]] == text[-1] and len(text) > 2:
        inner = text[1:-1]
        if len(inner) == 1:
            return [SemanticUnit(text)]
        units = [SemanticUnit(text[0] + inner[0])]
        units.extend(SemanticUnit(char) for char in inner[1:-1])
        units.append(SemanticUnit(inner[-1] + text[-1]))
        return units

    if text[0] in OPEN_TO_CLOSE and len(text) > 1:
        return [SemanticUnit(text[:2])] + [SemanticUnit(char) for char in text[2:]]
    if text[-1] in CLOSE_TO_OPEN and len(text) > 1:
        return [SemanticUnit(char) for char in text[:-2]] + [SemanticUnit(text[-2:])]
    return [SemanticUnit(char) for char in text]


def _must_attach_to_previous(text: str) -> bool:
    return bool(text) and (text[0] in NO_START_CHARS or text[0] in SUFFIX_TOKENS)


def _must_attach_next(text: str) -> bool:
    return bool(text) and text[-1] in NO_END_CHARS
