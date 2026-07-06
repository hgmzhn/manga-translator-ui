import unicodedata

_TRAILING_REMOVABLE_PUNCT_CHARS = ".。．｡︒﹒,，︐﹐‚„"
_TRAILING_CLOSERS = "\"'”’)]}）］】〉》」』"


def _split_text_tail(text: str) -> tuple[str, str]:
    value = str(text or "")
    end = len(value)

    while end > 0 and value[end - 1].isspace():
        end -= 1

    whitespace_suffix = value[end:]
    core = value[:end]
    close_start = len(core)

    while close_start > 0 and core[close_start - 1] in _TRAILING_CLOSERS:
        close_start -= 1

    return core[:close_start], core[close_start:] + whitespace_suffix


def has_terminal_punctuation(text: str) -> bool:
    body, _suffix = _split_text_tail(text)
    if not body:
        return False

    last_char = body[-1]
    if last_char in _TRAILING_REMOVABLE_PUNCT_CHARS:
        return True

    return unicodedata.category(last_char).startswith("P")


def remove_trailing_period_if_needed(source_text: str, translation_text: str, enabled: bool) -> str:
    translation = str(translation_text or "")
    if not enabled or not translation or has_terminal_punctuation(source_text):
        return translation

    body, suffix = _split_text_tail(translation)
    if not body or body[-1] not in _TRAILING_REMOVABLE_PUNCT_CHARS:
        return translation

    if len(body) >= 2 and body[-2] in _TRAILING_REMOVABLE_PUNCT_CHARS:
        return translation

    return body[:-1] + suffix
