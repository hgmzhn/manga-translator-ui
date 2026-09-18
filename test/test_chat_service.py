import _bootstrap  # noqa: F401

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field

import pytest

from manga_translator.agent.application.service import ChatService
from manga_translator.agent.domain.chat import ChatMessage


async def collect(stream: AsyncIterator[str]) -> str:
    async with aclosing(stream):
        return "".join([delta async for delta in stream])


@dataclass
class FakeBackend:
    replies: list[str]
    requests: list[tuple[ChatMessage, ...]] = field(default_factory=list)
    error: Exception | None = None
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.requests.append(tuple(messages))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        yield self.replies.pop(0)
        if self.error is not None:
            raise self.error


def test_multiple_turns_pass_history_to_backend() -> None:
    async def scenario() -> None:
        backend = FakeBackend(["first answer", "second answer"])
        service = ChatService(backend)

        await collect(service.stream("first"))
        await collect(service.stream("second"))

        assert backend.requests == [
            (ChatMessage("user", "first"),),
            (
                ChatMessage("user", "first"),
                ChatMessage("assistant", "first answer"),
                ChatMessage("user", "second"),
            ),
        ]
        assert service.history()[-1] == ChatMessage("assistant", "second answer")

    asyncio.run(scenario())


def test_clear_starts_a_fresh_conversation() -> None:
    async def scenario() -> None:
        backend = FakeBackend(["old answer", "new answer"])
        service = ChatService(backend)

        await collect(service.stream("old question"))
        service.clear()
        await collect(service.stream("new question"))

        assert backend.requests[-1] == (ChatMessage("user", "new question"),)
        assert service.history() == (
            ChatMessage("user", "new question"),
            ChatMessage("assistant", "new answer"),
        )

    asyncio.run(scenario())


def test_backend_error_does_not_add_user_or_assistant_messages() -> None:
    async def scenario() -> None:
        backend = FakeBackend(["saved answer", "partial answer"])
        service = ChatService(backend)
        await collect(service.stream("saved question"))
        previous = service.history()
        backend.error = RuntimeError("offline failure")

        async with aclosing(service.stream("will fail")) as stream:
            assert await anext(stream) == "partial answer"
            assert service.history() == previous
            with pytest.raises(RuntimeError, match="offline failure"):
                await anext(stream)

        assert service.history() == previous

    asyncio.run(scenario())


def test_cancelled_request_does_not_pollute_history() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        backend = FakeBackend(["never committed"], started=started, release=release)
        service = ChatService(backend)

        task = asyncio.create_task(collect(service.stream("cancel me")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert service.history() == ()
        release.set()

    asyncio.run(scenario())


def test_services_and_sessions_keep_history_isolated() -> None:
    async def scenario() -> None:
        first = ChatService(FakeBackend(["one", "two"]))
        second = ChatService(FakeBackend(["other"]))

        await collect(first.stream("first", session_id="a"))
        await collect(first.stream("separate", session_id="b"))
        await collect(second.stream("second", session_id="a"))

        assert [message.content for message in first.history("a")] == ["first", "one"]
        assert [message.content for message in first.history("b")] == [
            "separate",
            "two",
        ]
        assert [message.content for message in second.history("a")] == ["second", "other"]

    asyncio.run(scenario())


def test_clear_during_request_does_not_restore_stale_response() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        backend = FakeBackend(["stale"], started=started, release=release)
        service = ChatService(backend)

        task = asyncio.create_task(collect(service.stream("old")))
        await started.wait()
        service.clear()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.history() == ()

    asyncio.run(scenario())



@pytest.mark.parametrize("yield_late_delta", [False, True])
def test_cancelled_backend_that_returns_late_cannot_commit(yield_late_delta) -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        class LateBackend:
            async def stream(self, messages):
                yield "initial delta"
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if yield_late_delta:
                        yield "late answer"

        service = ChatService(LateBackend())
        task = asyncio.create_task(collect(service.stream("cancel me")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.history() == ()

    asyncio.run(scenario())


def test_clear_invalidates_queued_turns_before_they_reach_backend() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        backend = FakeBackend(["stale", "new answer"], started=started, release=release)
        service = ChatService(backend)
        first = asyncio.create_task(collect(service.stream("old")))
        await started.wait()
        queued = asyncio.create_task(collect(service.stream("queued")))
        await asyncio.sleep(0)
        service.clear()
        release.set()
        for task in (first, queued):
            with pytest.raises(asyncio.CancelledError):
                await task
        await collect(service.stream("new"))
        assert backend.requests == [
            (ChatMessage("user", "old"),),
            (ChatMessage("user", "new"),),
        ]
        assert service.history() == (
            ChatMessage("user", "new"),
            ChatMessage("assistant", "new answer"),
        )

    asyncio.run(scenario())


def test_deltas_arrive_before_exhaustion_and_history_commits_atomically() -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        class GatedBackend:
            async def stream(self, messages):
                yield "first "
                await release.wait()
                yield "second"

        service = ChatService(GatedBackend())
        async with aclosing(service.stream("question")) as stream:
            assert await anext(stream) == "first "
            assert not release.is_set()
            assert service.history() == ()
            release.set()
            assert await anext(stream) == "second"
            assert service.history() == ()
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        assert service.history() == (
            ChatMessage("user", "question"),
            ChatMessage("assistant", "first second"),
        )

    asyncio.run(scenario())


def test_early_close_releases_backend_and_turn_without_committing() -> None:
    async def scenario() -> None:
        closed = asyncio.Event()

        class ClosingBackend:
            async def stream(self, messages):
                try:
                    yield "first "
                    yield "second"
                finally:
                    closed.set()

        service = ChatService(ClosingBackend())
        stream = service.stream("abandoned")
        assert await anext(stream) == "first "
        await stream.aclose()
        assert closed.is_set()
        assert service.history() == ()
        assert await collect(service.stream("next")) == "first second"
        assert service.history() == (
            ChatMessage("user", "next"),
            ChatMessage("assistant", "first second"),
        )

    asyncio.run(scenario())
