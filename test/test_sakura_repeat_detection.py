import logging
import pytest
from unittest.mock import AsyncMock
from manga_translator.translators.sakura import SakuraTranslator


def test_detect_and_caculate_repeats_empty_and_short_strings():
    translator = SakuraTranslator.__new__(SakuraTranslator)
    translator.logger = logging.getLogger("test_sakura")

    # None input
    repeated, s, count, pattern, actual_threshold = translator.detect_and_caculate_repeats(None)
    assert repeated is False
    assert s == ""
    assert count == 0
    assert pattern == ""
    assert actual_threshold == SakuraTranslator._REPEAT_DETECT_THRESHOLD

    # Empty string
    repeated, s, count, pattern, actual_threshold = translator.detect_and_caculate_repeats("")
    assert repeated is False
    assert s == ""
    assert count == 0
    assert pattern == ""
    assert actual_threshold == SakuraTranslator._REPEAT_DETECT_THRESHOLD

    # Single character string (len < 2)
    repeated, s, count, pattern, actual_threshold = translator.detect_and_caculate_repeats("好")
    assert repeated is False
    assert s == "好"
    assert count == 0
    assert pattern == ""
    assert actual_threshold == SakuraTranslator._REPEAT_DETECT_THRESHOLD

    # Short string with no repeat
    repeated, s, count, pattern, actual_threshold = translator.detect_and_caculate_repeats("ab")
    assert repeated is False
    assert s == "ab"
    assert count == 1
    assert pattern == "a"
    assert actual_threshold == SakuraTranslator._REPEAT_DETECT_THRESHOLD


def test_detect_and_caculate_repeats_threshold_hit():
    translator = SakuraTranslator.__new__(SakuraTranslator)
    translator.logger = logging.getLogger("test_sakura")

    pattern_src = "喵" * 25
    repeated, s, count, pattern, actual_threshold = translator.detect_and_caculate_repeats(pattern_src, threshold=20, remove_all=True)
    assert repeated is True
    assert count == 25
    assert pattern == "喵"
    assert s == "喵"


def test_detect_repeats_and_get_repeat_count():
    translator = SakuraTranslator.__new__(SakuraTranslator)
    translator.logger = logging.getLogger("test_sakura")

    assert translator._detect_repeats("") is False
    assert translator._detect_repeats("啊") is False
    assert translator._get_repeat_count("") == 0
    assert translator._get_repeat_count("啊") == 0


def test_translate_single_lines_handles_short_responses():
    import asyncio
    translator = SakuraTranslator.__new__(SakuraTranslator)
    translator.logger = logging.getLogger("test_sakura")

    # Mock _handle_translation_request to return short or empty responses
    responses = ["", "嗯", "好的", "哈哈" * 25]
    call_idx = 0

    async def fake_handle_request(query):
        nonlocal call_idx
        res = responses[call_idx]
        call_idx += 1
        return res

    translator._handle_translation_request = AsyncMock(side_effect=fake_handle_request)

    queries = ["「」", "「うん」", "「わかりました」", "「あ」"]
    results = asyncio.run(translator._translate_single_lines(queries))

    # Empty and "嗯" and "好的" should not trigger repeat detection, returns responses
    assert results[0] == ""
    assert results[1] == "嗯"
    assert results[2] == "好的"
    # "哈哈"*25 triggers repeat detection, returns original query
    assert results[3] == "「あ」"
