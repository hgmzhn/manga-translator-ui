import _bootstrap  # noqa: F401

import asyncio
import json
import random

import pytest

from manga_translator.translators.common import CommonTranslator, StreamTextAccumulator


def _feed_all(pieces):
    acc = StreamTextAccumulator()
    deltas = [acc.feed(piece) for piece in pieces]
    return acc.text, deltas


@pytest.mark.parametrize(
    "pieces",
    [
        ["于", "21", "9", "9年"],
        ["男人稳稳", "当", "当地坐在"],
        ["定价1000日元", "日元"],
        list("啊" * 40),
        ["这是合法的重复内容，不应被当成重发。"] * 3,
        ["开头", "这是一段至少十六个字符长度的尾部内容。"] * 2,
        ["abcdefgh", "abcdefgh后文"],
        ["abcdefghijklmnop", "abcdefghijklmnop后文"],
        ["abcdefghijklmnop后文", "abcdefghijklmnop"],
        ["甲乙丙", "丙丁戊"],
        ["", "正文", "", "正文", ""],
        [],
    ],
)
def test_delta_chunks_are_preserved_exactly(pieces):
    text, deltas = _feed_all(pieces)
    assert text == "".join(pieces)
    assert deltas == pieces


def test_seeded_random_chunk_boundaries_preserve_json():
    payload = {
        "translations": [
            {"id": 1, "translation": "于2199年，男人稳稳当当地坐着。"},
            {"id": 2, "translation": "哈哈哈哈，100100日元！" * 20},
            {"id": 3, "translation": "「甲」[BR]︐乙︒" * 20},
        ]
    }
    source = json.dumps(payload, ensure_ascii=False)
    for seed in range(1000):
        rng = random.Random(seed)
        pieces = []
        offset = 0
        while offset < len(source):
            size = rng.randint(1, 80)
            pieces.extend(["", source[offset:offset + size]])
            offset += size
        text, deltas = _feed_all(pieces)
        assert text == source, f"seed={seed}"
        assert "".join(deltas) == source, f"seed={seed}"
        assert json.loads(text) == payload


class _FakeTranslator:
    def __init__(self):
        self.logger = None

    def _check_cancelled(self):
        return None

    async def _await_with_cancel_polling(self, awaitable, poll_interval=0.2, on_cancel=None):
        return await awaitable


class _AsyncChunks:
    def __init__(self, pieces):
        self._pieces = iter(pieces)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._pieces)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("sync_iter_in_thread", [False, True])
@pytest.mark.parametrize(
    "pieces",
    [
        ["于", "21", "9", "9年", "稳稳", "当", "当地"],
        ["这是合法的重复内容，不应被当成重发。"] * 3,
        ["abcdefgh", "abcdefgh后文", "", "abcdefgh后文"],
        ["", ""],
        [],
    ],
)
def test_transport_preserves_deltas_callbacks_and_finish_reason(pieces, sync_iter_in_thread):
    seen = []
    chunks = [{"text": piece, "finish": None} for piece in pieces]
    chunks.extend([
        {"text": "", "finish": "stop"},
        {"text": "", "finish": None},
    ])
    stream = iter(chunks) if sync_iter_in_thread else _AsyncChunks(chunks)
    coro = CommonTranslator._run_unified_stream_transport(
        _FakeTranslator(),
        create_stream=lambda: stream,
        extract_text=lambda chunk: chunk["text"],
        extract_finish_reason=lambda chunk: chunk["finish"],
        on_chunk=lambda delta, full: seen.append((delta, full)),
        sync_iter_in_thread=sync_iter_in_thread,
    )
    text, finish = asyncio.run(coro)
    assert text == "".join(pieces)
    assert finish == "stop"
    full = ""
    expected_seen = []
    for piece in pieces:
        full += piece
        if piece:
            expected_seen.append((piece, full))
    assert seen == expected_seen
    if not sync_iter_in_thread:
        assert stream.closed


def test_async_transport_closes_stream_after_extraction_error():
    stream = _AsyncChunks(["正文"])

    def fail_extract(chunk):
        raise ValueError("invalid chunk")

    with pytest.raises(ValueError, match="invalid chunk"):
        asyncio.run(CommonTranslator._run_unified_stream_transport(
            _FakeTranslator(), create_stream=lambda: stream, extract_text=fail_extract,
        ))
    assert stream.closed


def test_async_transport_accepts_awaitable_stream_factory():
    stream = _AsyncChunks(["正文", "正文"])

    async def create_stream():
        return stream

    text, finish = asyncio.run(CommonTranslator._run_unified_stream_transport(
        _FakeTranslator(), create_stream=create_stream, extract_text=lambda chunk: chunk,
    ))
    assert text == "正文正文"
    assert finish is None
    assert stream.closed


@pytest.mark.parametrize("pieces, phase", [([], "first chunk"), (["正文"], "idle")])
def test_async_transport_timeout_closes_stream(pieces, phase):
    class TimeoutChunks(_AsyncChunks):
        async def __anext__(self):
            try:
                return await super().__anext__()
            except StopAsyncIteration:
                raise asyncio.TimeoutError

    stream = TimeoutChunks(pieces)
    with pytest.raises(TimeoutError, match=f"Streaming {phase} timeout"):
        asyncio.run(CommonTranslator._run_unified_stream_transport(
            _FakeTranslator(), create_stream=lambda: stream, extract_text=lambda chunk: chunk,
        ))
    assert stream.closed


def test_async_transport_cancellation_calls_cleanup_and_closes_stream():
    class CancelledChunks(_AsyncChunks):
        async def __anext__(self):
            raise asyncio.CancelledError

    stream = CancelledChunks([])
    cleanups = []

    async def cleanup():
        cleanups.append(True)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(CommonTranslator._run_unified_stream_transport(
            _FakeTranslator(), create_stream=lambda: stream, extract_text=lambda chunk: chunk,
            on_cancel=cleanup,
        ))
    assert cleanups == [True]
    assert stream.closed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
