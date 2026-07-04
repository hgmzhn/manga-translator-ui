from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, List, Optional

from ..config import Config
from ..utils import TextBlock, get_logger

logger = get_logger('render')


@dataclass(frozen=True)
class ReplacementLayoutRecord:
    raw_text: str
    replaced_text: str


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


def _collect_insertions(before: str, after: str) -> List[tuple[int, str]]:
    if before == after:
        return []
    insertions: List[tuple[int, str]] = []
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'insert':
            insertions.append((i1, after[j1:j2]))
    return insertions


def project_insertions_to_raw(record: ReplacementLayoutRecord, replaced_text_after_layout: str) -> str:
    insertions = _collect_insertions(record.replaced_text, replaced_text_after_layout)
    if not insertions:
        return record.raw_text

    mapper = _boundary_map_replaced_to_raw(record.replaced_text, record.raw_text)
    raw_insertions: dict[int, List[str]] = {}
    for replaced_pos, inserted_text in insertions:
        if not inserted_text:
            continue
        replaced_pos = min(max(replaced_pos, 0), len(mapper) - 1)
        raw_pos = mapper[replaced_pos]
        raw_insertions.setdefault(raw_pos, []).append(inserted_text)

    if not raw_insertions:
        return record.raw_text

    result_parts = []
    last = 0
    for raw_pos in sorted(raw_insertions):
        result_parts.append(record.raw_text[last:raw_pos])
        result_parts.extend(raw_insertions[raw_pos])
        last = raw_pos
    result_parts.append(record.raw_text[last:])
    return ''.join(result_parts)


def prepare_text_replacements_for_layout(
    text_regions: List[TextBlock],
    config: Config,
    *,
    resolve_render_horizontal: Callable[[TextBlock], bool],
    apply_vertical_horizontal_markup: Callable[..., str],
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
        if region is None or not getattr(region, 'translation', ''):
            continue
        if getattr(region, '_replacement_layout_record', None) is not None:
            continue

        render_horizontally = resolve_render_horizontal(region)
        raw_text = region.translation
        raw_text = apply_vertical_horizontal_markup(
            raw_text,
            render_horizontally=render_horizontally,
            config=config,
        )
        direction = 0 if render_horizontally else 1

        try:
            replaced_text = apply_replacements(raw_text, direction, replacements)
        except Exception as exc:
            logger.warning(f"[RENDER] Failed to apply text replacements, use raw text: {exc}")
            replaced_text = raw_text

        region.translation_raw = raw_text
        region.translation = replaced_text
        region._replacement_layout_record = ReplacementLayoutRecord(
            raw_text=raw_text,
            replaced_text=replaced_text,
        )


def sync_translation_raw_from_layout(text_regions: List[TextBlock]) -> None:
    for region in text_regions:
        record = getattr(region, '_replacement_layout_record', None)
        if record is None:
            continue
        region.translation_raw = project_insertions_to_raw(record, region.translation)
        try:
            delattr(region, '_replacement_layout_record')
        except Exception:
            pass
