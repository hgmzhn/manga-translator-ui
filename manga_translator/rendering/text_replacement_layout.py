from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, List, Optional

from ..config import Config
from ..utils import TextBlock, get_logger
from .rich_text import is_rich_text_document

logger = get_logger('render')


@dataclass(frozen=True)
class ReplacementLayoutRecord:
    raw_text: str
    replaced_text: str


def _strip_linebreak_edge_punctuation_if_enabled(text: Any, config: Config) -> Any:
    if not isinstance(text, str):
        return text
    if not (config and hasattr(config, 'render') and getattr(config.render, 'remove_linebreak_punctuation', False)):
        return text
    from .auto_linebreak import strip_linebreak_edge_punctuation

    return strip_linebreak_edge_punctuation(text)


def _boundary_map_replaced_to_raw(replaced_text: str, raw_text: str) -> List[int]:
    mapper: List[Optional[int]] = [None] * (len(replaced_text) + 1)
    matcher = SequenceMatcher(None, replaced_text, raw_text, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        replaced_len = i2 - i1
        raw_len = j2 - j1
        if tag == 'equal':
            for offset in range(replaced_len + 1):
                mapper[i1 + offset] = j1 + offset
        elif replaced_len > 0:
            for offset in range(replaced_len + 1):
                ratio = offset / replaced_len
                mapper[i1 + offset] = int(round(j1 + ratio * raw_len))
        else:
            mapper[i1] = j2

    last = 0
    for idx, value in enumerate(mapper):
        if value is None:
            mapper[idx] = last
        else:
            last = value
    mapper[-1] = len(raw_text)
    return [min(max(pos, 0), len(raw_text)) for pos in mapper]


def _collect_layout_edits(before: str, after: str) -> List[tuple[int, int, str]]:
    if before == after:
        return []
    edits: List[tuple[int, int, str]] = []
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            edits.append((i1, i2, after[j1:j2]))
    return edits


def project_layout_changes_to_raw(record: ReplacementLayoutRecord, replaced_text_after_layout: str) -> str:
    edits = _collect_layout_edits(record.replaced_text, replaced_text_after_layout)
    if not edits:
        return record.raw_text

    mapper = _boundary_map_replaced_to_raw(record.replaced_text, record.raw_text)
    raw_edits: List[tuple[int, int, str]] = []
    for replaced_start, replaced_end, replacement in edits:
        replaced_start = min(max(replaced_start, 0), len(mapper) - 1)
        replaced_end = min(max(replaced_end, replaced_start), len(mapper) - 1)
        raw_edits.append((mapper[replaced_start], mapper[replaced_end], replacement))

    result = record.raw_text
    for raw_start, raw_end, replacement in reversed(raw_edits):
        result = result[:raw_start] + replacement + result[raw_end:]
    return result


def prepare_text_replacements_for_layout(
    text_regions: List[TextBlock],
    config: Config,
    *,
    resolve_render_horizontal: Callable[[TextBlock], bool],
    skip_text_replacements: bool = False,
) -> None:
    if skip_text_replacements:
        return

    try:
        from .text_replacements import apply_replacements, load_replacements
        replacements = load_replacements()
    except Exception as exc:
        logger.warning(f"[RENDER] Failed to load text replacement rules, skip replacement: {exc}")
        return

    for region in text_regions:
        if region is None:
            continue
        render_value = region.get_translation_for_rendering() if hasattr(region, 'get_translation_for_rendering') else region.translation
        if not render_value:
            continue
        if is_rich_text_document(render_value):
            continue
        if getattr(region, '_replacement_layout_record', None) is not None:
            continue

        render_horizontally = resolve_render_horizontal(region)
        # TextBlock.translation property 恒返 str
        raw_text = region.translation
        raw_text = _strip_linebreak_edge_punctuation_if_enabled(raw_text, config)
        direction = 0 if render_horizontally else 1

        try:
            replaced_text = apply_replacements(raw_text, direction, replacements)
        except Exception as exc:
            logger.warning(f"[RENDER] Failed to apply text replacements, use raw text: {exc}")
            replaced_text = raw_text
        replaced_text = _strip_linebreak_edge_punctuation_if_enabled(replaced_text, config)

        region.translation_raw = raw_text
        region.translation = replaced_text
        region._replacement_layout_record = ReplacementLayoutRecord(
            raw_text=raw_text,
            replaced_text=replaced_text,
        )


def sync_translation_raw_from_layout(
    text_regions: List[TextBlock],
    config: Config = None,
    *,
    skip_text_replacements: bool = False,
) -> None:
    for region in text_regions:
        record = getattr(region, '_replacement_layout_record', None)
        if record is not None:
            # TextBlock.translation property 恒返 str
            raw_after_layout = project_layout_changes_to_raw(record, region.translation)
            region.translation = _strip_linebreak_edge_punctuation_if_enabled(region.translation, config)
            region.translation_raw = _strip_linebreak_edge_punctuation_if_enabled(
                raw_after_layout,
                config,
            )
            try:
                delattr(region, '_replacement_layout_record')
            except Exception:
                pass

        if not skip_text_replacements:
            # 富文本规则读取的是替换及断句完成后的 translation。规则引擎会把
            # [BR]/【BR】/<br>/换行转换为 paragraph 边界，标记本身不会进入样式 run。
            from .rich_text_rules import apply_rich_text_rules_to_region

            apply_rich_text_rules_to_region(region)

        # 即使终稿通过 skip_text_replacements 跳过自动富文本规则，传统 BR
        # 仍要转换成 paragraph，保证纯文本多行内容沿用统一结构化渲染路径。
        if hasattr(region, 'ensure_translation_rich_from_legacy_breaks'):
            region.ensure_translation_rich_from_legacy_breaks()
