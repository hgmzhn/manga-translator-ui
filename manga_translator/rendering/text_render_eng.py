from typing import Any, List

import cv2
import numpy as np

from ..utils import TextBlock, get_logger
from .ballon_extractor import extract_ballon_region
from .text_render import (
    calc_horizontal_block_height,
    calc_horizontal_line_spacing_px,
    get_char_offset_x,
    get_string_width,
)

logger = get_logger('text_render_eng')

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PUNSET_RIGHT_ENG = {'.', '?', '!', ':', ';', ')', '}', "\""}


def _write_region_br_from_lines(region: TextBlock, lines: List[str]) -> None:
    normalized_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not normalized_lines:
        return
    region.translation = '[BR]'.join(normalized_lines)


def _apply_english_case_preferences(text: str, config: Any = None) -> str:
    render_cfg = getattr(config, 'render', None) if config is not None else None
    uppercase = bool(getattr(render_cfg, 'uppercase', False)) if render_cfg is not None else False
    lowercase = bool(getattr(render_cfg, 'lowercase', False)) if render_cfg is not None else False

    if uppercase:
        return text.upper()
    if lowercase:
        return text.lower()
    return text


def _english_hyphenate_enabled(config: Any = None) -> bool:
    return not (config and hasattr(config, 'render') and getattr(config.render, 'no_hyphenation', False))


def _resolve_english_layout_language(target_lang: str) -> str:
    normalized = str(target_lang or '').strip().upper()
    if normalized in {'ENG', 'EN', 'EN_US', 'EN-EN', 'ENGLISH'}:
        return 'en_US'
    return target_lang or 'en_US'


def _hyphenate_overflowing_single_word_lines(
    lines: List[str],
    font_size: int,
    max_width: int,
    target_lang: str,
    letter_spacing: float = 1.0,
    *,
    enabled: bool,
) -> List[str]:
    if not enabled or max_width <= 0:
        return lines
    normalized_lines: List[str] = []
    from .auto_linebreak import _layout_horizontal_eng

    for raw_line in lines:
        line = str(raw_line).strip()
        if not line:
            continue
        if ' ' in line or get_string_width(font_size, line, letter_spacing=letter_spacing) <= max_width:
            normalized_lines.append(line)
            continue

        split_lines, _ = _layout_horizontal_eng(
            font_size,
            line,
            max_width,
            language=_resolve_english_layout_language(target_lang),
            hyphenate=True,
            letter_spacing=letter_spacing,
        )
        split_lines = [str(part).strip() for part in split_lines if str(part).strip()]
        if len(split_lines) <= 1:
            normalized_lines.append(line)
            continue

        for idx, part in enumerate(split_lines):
            if idx < len(split_lines) - 1 and not part.endswith('-'):
                normalized_lines.append(f'{part}-')
            else:
                normalized_lines.append(part)

    return normalized_lines


class Textline:
    def __init__(self, text: str = '', pos_x: int = 0, pos_y: int = 0, length: float = 0, spacing: int = 0) -> None:
        self.text = text
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.length = int(length)
        self.num_words = 0
        if text:
            self.num_words += 1
        self.spacing = 0
        self.add_spacing(spacing)

    def append_right(self, word: str, w_len: int, delimiter: str = ''):
        self.text = self.text + delimiter + word
        if word:
            self.num_words += 1
        self.length += w_len

    def append_left(self, word: str, w_len: int, delimiter: str = ''):
        self.text = word + delimiter + self.text
        if word:
            self.num_words += 1
        self.length += w_len

    def add_spacing(self, spacing: int):
        self.spacing = spacing
        self.pos_x -= spacing
        self.length += 2 * spacing

    def strip_spacing(self):
        self.length -= self.spacing * 2
        self.pos_x += self.spacing
        self.spacing = 0

def seg_eng(text: str, uppercase: bool = True) -> List[str]:
    """
    Extracts every word from text parameter
    """
    # TODO: replace with regexes

    text = text.strip()
    if uppercase:
        text = text.upper()
    text = text.replace('  ', ' ').replace(' .', '.').replace('\n', ' ')
    processed_text = ''

    # dumb way to ensure spaces between words
    text_len = len(text)
    for ii, c in enumerate(text):
        if c in PUNSET_RIGHT_ENG and ii < text_len - 1:
            next_c = text[ii + 1]
            if next_c.isalpha() or next_c.isnumeric():
                processed_text += c + ' '
            else:
                processed_text += c
        else:
            processed_text += c

    word_list = processed_text.split(' ')
    word_num = len(word_list)
    if word_num <= 1:
        return word_list

    words = []
    skip_next = False
    for ii, word in enumerate(word_list):
        if skip_next:
            skip_next = False
            continue
        if len(word) < 3:
            append_left, append_right = False, False
            len_word, len_next, len_prev = len(word), -1, -1
            if ii < word_num - 1:
                len_next = len(word_list[ii + 1])
            if ii > 0:
                len_prev = len(words[-1])
            cond_next = (len_word == 2 and len_next <= 4) or len_word == 1
            cond_prev = (len_word == 2 and len_prev <= 4) or len_word == 1
            if len_next > 0 and len_prev > 0:
                if len_next < len_prev:
                    append_right = cond_next
                else:
                    append_left = cond_prev
            elif len_next > 0:
                append_right = cond_next
            elif len_prev:
                append_left = cond_prev

            if append_left:
                words[-1] = words[-1] + ' ' + word
            elif append_right:
                words.append(word + ' ' + word_list[ii + 1])
                skip_next = True
            else:
                words.append(word)
            continue
        words.append(word)
    return words


def apply_manga2eng_line_breaks(
    region: TextBlock,
    original_img: np.ndarray = None,
    seed_font_size: int = None,
    delimiter: str = ' ',
    config: Any = None,
    letter_spacing: float = 1.0,
) -> bool:
    original_translation = str(getattr(region, 'translation', '') or '')
    text = _apply_english_case_preferences(original_translation, config)
    if text != original_translation:
        region.translation = text
    if not text.strip():
        return text != original_translation

    words = seg_eng(text, uppercase=False)
    font_size = max(int(seed_font_size or getattr(region, 'font_size', 0) or 1), 1)
    render_cfg = getattr(config, 'render', None) if config is not None else None
    line_spacing = getattr(region, 'line_spacing', None)
    if not isinstance(line_spacing, (int, float)) or line_spacing <= 0:
        line_spacing = getattr(render_cfg, 'line_spacing', None) if render_cfg is not None else None
    if not isinstance(line_spacing, (int, float)) or line_spacing <= 0:
        line_spacing = 1.0
    line_height = (
        calc_horizontal_block_height(font_size, text, letter_spacing)
        + calc_horizontal_line_spacing_px(font_size, line_spacing)
    )
    delimiter_len = get_char_offset_x(font_size, delimiter)
    word_lengths = []
    for word in words:
        word_length = 0
        for cdpt in word:
            word_length += get_char_offset_x(font_size, cdpt)
        word_lengths.append(word_length)

    base_lines: List[str] = []
    if not word_lengths or max(word_lengths) <= 0:
        region.translation = text
        return region.translation != original_translation

    if len(words) > 1:
        balloon_mask = None
        enlarge_ratio = getattr(region, 'enlarge_ratio', None)
        if not isinstance(enlarge_ratio, (int, float)) or not np.isfinite(enlarge_ratio) or enlarge_ratio <= 0:
            try:
                box_w = float(max(region.xywh[2], 1))
                box_h = float(max(region.xywh[3], 1))
                enlarge_ratio = min(max(box_w / box_h, box_h / box_w) * 1.5, 3)
            except Exception:
                enlarge_ratio = 1.0

        if original_img is not None:
            try:
                balloon_mask, _ = extract_ballon_region(original_img, region.xywh, enlarge_ratio=enlarge_ratio)
            except Exception as exc:
                logger.debug(f"Manga2Eng line break mask extraction failed: {exc}")
                balloon_mask = None

        if not isinstance(balloon_mask, np.ndarray) or balloon_mask.size == 0 or np.count_nonzero(balloon_mask) == 0:
            try:
                box_w = max(int(round(float(region.xywh[2]))), 1)
                max_width = max(int(box_w * 1.2), max(word_lengths))
            except Exception:
                max_width = max(word_lengths)

            current_words = []
            current_width = 0
            for word, word_length in zip(words, word_lengths):
                next_width = word_length if not current_words else current_width + delimiter_len + word_length
                if current_words and next_width > max_width:
                    base_lines.append(delimiter.join(current_words))
                    current_words = [word]
                    current_width = word_length
                else:
                    current_words.append(word)
                    current_width = next_width
            if current_words:
                base_lines.append(delimiter.join(current_words))
        else:
            balloon_mask = np.asarray(balloon_mask, dtype=np.uint8)

            try:
                textlines = layout_lines_aligncenter(
                    balloon_mask,
                    words,
                    word_lengths,
                    delimiter_len,
                    line_height,
                    delimiter=delimiter,
                )
                base_lines = [line.text for line in textlines if getattr(line, 'text', '').strip()]
            except Exception as exc:
                logger.debug(f"Manga2Eng line break layout failed: {exc}")
                base_lines = []

    final_lines = _hyphenate_overflowing_single_word_lines(
        base_lines if base_lines else [text],
        font_size=font_size,
        max_width=max(int(round(float(region.xywh[2]))), 1),
        target_lang=getattr(region, 'target_lang', ''),
        letter_spacing=letter_spacing,
        enabled=_english_hyphenate_enabled(config),
    )

    lines = [line for line in final_lines if str(line).strip()]
    if len(lines) > 1:
        _write_region_br_from_lines(region, lines)
    else:
        region.translation = lines[0] if lines else text
    return region.translation != original_translation

def layout_lines_aligncenter(
    mask: np.ndarray, 
    words: List[str], 
    word_lengths: List[int], 
    delimiter_len: int, 
    line_height: int,
    spacing: int = 0,
    delimiter: str = ' ',
    max_central_width: float = np.inf,
    word_break: bool = False)->List[Textline]:

    m = cv2.moments(mask)
    mask = 255 - mask
    centroid_y = int(m['m01'] / m['m00'])
    centroid_x = int(m['m10'] / m['m00'])

    # layout the central line, the center word is approximately aligned with the centroid of the mask
    num_words = len(words)
    len_left, len_right = [], []
    wlst_left, wlst_right = [], []
    sum_left, sum_right = 0, 0
    if num_words > 1:
        wl_array = np.array(word_lengths, dtype=np.float64)
        wl_cumsums = np.cumsum(wl_array)
        wl_cumsums = wl_cumsums - wl_cumsums[-1] / 2 - wl_array / 2
        central_index = np.argmin(np.abs(wl_cumsums))

        if central_index > 0:
            wlst_left = words[:central_index]
            len_left = word_lengths[:central_index]
            sum_left = np.sum(len_left)
        if central_index < num_words - 1:
            wlst_right = words[central_index + 1:]
            len_right = word_lengths[central_index + 1:]
            sum_right = np.sum(len_right)
    else:
        central_index = 0

    pos_y = centroid_y - line_height // 2
    pos_x = centroid_x - word_lengths[central_index] // 2

    bh, bw = mask.shape[:2]
    central_line = Textline(words[central_index], pos_x, pos_y, word_lengths[central_index], spacing)
    line_bottom = pos_y + line_height
    while sum_left > 0 or sum_right > 0:
        left_valid, right_valid = False, False

        if sum_left > 0:
            new_len_l = central_line.length + len_left[-1] + delimiter_len
            new_x_l = centroid_x - new_len_l // 2
            new_r_l = new_x_l + new_len_l
            if (new_x_l > 0 and new_r_l < bw):
                if mask[pos_y: line_bottom, new_x_l].sum()==0 and mask[pos_y: line_bottom, new_r_l].sum() == 0:
                    left_valid = True
        if sum_right > 0:
            new_len_r = central_line.length + len_right[0] + delimiter_len
            new_x_r = centroid_x - new_len_r // 2
            new_r_r = new_x_r + new_len_r
            if (new_x_r > 0 and new_r_r < bw):
                if mask[pos_y: line_bottom, new_x_r].sum()==0 and mask[pos_y: line_bottom, new_r_r].sum() == 0:
                    right_valid = True

        insert_left = False
        if left_valid and right_valid:
            if sum_left > sum_right:
                insert_left = True
        elif left_valid:
            insert_left = True
        elif not right_valid:
            break

        if insert_left:
            central_line.append_left(wlst_left.pop(-1), len_left[-1] + delimiter_len, delimiter)
            sum_left -= len_left.pop(-1)
            central_line.pos_x = new_x_l
        else:
            central_line.append_right(wlst_right.pop(0), len_right[0] + delimiter_len, delimiter)
            sum_right -= len_right.pop(0)
            central_line.pos_x = new_x_r
        if central_line.length > max_central_width:
            break

    central_line.strip_spacing()
    lines = [central_line]

    # layout bottom half
    if sum_right > 0:
        w, wl = wlst_right.pop(0), len_right.pop(0)
        pos_x = centroid_x - wl // 2
        pos_y = centroid_y + line_height // 2
        line_bottom = pos_y + line_height
        line = Textline(w, pos_x, pos_y, wl, spacing)
        lines.append(line)
        sum_right -= wl
        while sum_right > 0:
            w, wl = wlst_right.pop(0), len_right.pop(0)
            sum_right -= wl
            new_len = line.length + wl + delimiter_len
            new_x = centroid_x - new_len // 2
            right_x = new_x + new_len
            if new_x <= 0 or right_x >= bw:
                line_valid = False
            elif mask[pos_y: line_bottom, new_x].sum() > 0 or\
                mask[pos_y: line_bottom, right_x].sum() > 0:
                line_valid = False
            else:
                line_valid = True
            if line_valid:
                line.append_right(w, wl+delimiter_len, delimiter)
                line.pos_x = new_x
                if new_len > max_central_width:
                    line_valid = False
                    if sum_right > 0:
                        w, wl = wlst_right.pop(0), len_right.pop(0)
                        sum_right -= wl
                    else:
                        line.strip_spacing()
                        break

            if not line_valid:
                pos_x = centroid_x - wl // 2
                pos_y = line_bottom
                line_bottom += line_height
                line.strip_spacing()
                line = Textline(w, pos_x, pos_y, wl, spacing)
                lines.append(line)

    # layout top half
    if sum_left > 0:
        w, wl = wlst_left.pop(-1), len_left.pop(-1)
        pos_x = centroid_x - wl // 2
        pos_y = centroid_y - line_height // 2 - line_height
        line_bottom = pos_y + line_height
        line = Textline(w, pos_x, pos_y, wl, spacing)
        lines.insert(0, line)
        sum_left -= wl
        while sum_left > 0:
            w, wl = wlst_left.pop(-1), len_left.pop(-1)
            sum_left -= wl
            new_len = line.length + wl + delimiter_len
            new_x = centroid_x - new_len // 2
            right_x = new_x + new_len
            if new_x <= 0 or right_x >= bw:
                line_valid = False
            elif mask[pos_y: line_bottom, new_x].sum() > 0 or\
                mask[pos_y: line_bottom, right_x].sum() > 0:
                line_valid = False
            else:
                line_valid = True
            if line_valid:
                line.append_left(w, wl+delimiter_len, delimiter)
                line.pos_x = new_x
                if new_len > max_central_width:
                    line_valid = False
                    if sum_left > 0:
                        w, wl = wlst_left.pop(-1), len_left.pop(-1)
                        sum_left -= wl
                    else:
                        line.strip_spacing()
                        break

            if not line_valid:
                pos_x = centroid_x - wl // 2
                pos_y -= line_height
                line_bottom = pos_y + line_height
                line.strip_spacing()
                line = Textline(w, pos_x, pos_y, wl, spacing)
                lines.insert(0, line)

    # rbgmsk = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    # cv2.circle(rbgmsk, (centroid_x, centroid_y), 10, (255, 0, 0))
    # for line in lines:
    #     cv2.rectangle(rbgmsk, (line.pos_x, line.pos_y), (line.pos_x + line.length, line.pos_y + line_height), (0, 255, 0))
    # cv2.imshow('mask', rbgmsk)
    # cv2.waitKey(0)

    return lines
