"""Qt-independent conversational use cases."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from typing import TYPE_CHECKING, Protocol

from ..domain.chat import ChatImage

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    """Native conversation messages from a completed, released backend run."""

    messages: list[ModelMessage]


class ChatBackend(Protocol):
    """Stream text followed by one native result after releasing resources."""

    def stream(
        self,
        text: str,
        *,
        images: tuple[ChatImage, ...],
        message_history: list[ModelMessage],
    ) -> AsyncIterator[str | ChatTurnResult]:
        """Propagate errors and cancellation without yielding a final result."""


@dataclass(slots=True)
class _SessionState:
    history: list[ModelMessage] = field(default_factory=list)
    generation: int = 0
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChatService:
    """Manage isolated sessions using an injected backend.

    Call all methods on the same event-loop thread. A UI bridge should schedule
    operations there rather than mutate this service from the GUI thread.
    Turns in a session are serialized; different sessions can run concurrently.
    Only complete, successful native runs enter history. Failures and
    cancellations propagate to the caller and leave prior history unchanged.
    """

    def __init__(self, backend: ChatBackend) -> None:
        self._backend = backend
        self._sessions: dict[str, _SessionState] = {}

    def _session(self, session_id: str) -> _SessionState:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        state = self._sessions.get(session_id)
        if state is None:
            state = _SessionState()
            self._sessions[session_id] = state
        return state

    def clear(self, session_id: str = "default") -> None:
        """Clear history and invalidate in-flight or queued turns.

        This does not stop the backend request. Cancel the task executing
        ``stream`` to interrupt network I/O as well. An invalidated stream raises
        ``asyncio.CancelledError`` rather than yielding an obsolete reply.
        """
        state = self._session(session_id)
        state.history = []
        state.generation += 1

    async def stream(
        self,
        text: str,
        *,
        images: tuple[ChatImage, ...] = (),
        session_id: str = "default",
    ) -> AsyncIterator[str]:
        """Yield deltas and commit only after normal exhaustion.

        Consumers stopping early must close the iterator, for example with
        ``contextlib.aclosing``, to release the backend and session turn lock.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        images = tuple(images)
        if any(not isinstance(image, ChatImage) for image in images):
            raise TypeError("message images must contain ChatImage values")
        if not text.strip() and not images:
            raise ValueError("a chat turn must contain text or images")

        state = self._session(session_id)
        generation = state.generation
        async with state.turn_lock:
            if state.generation != generation:
                raise asyncio.CancelledError("session was cleared")
            result: ChatTurnResult | None = None
            has_text = False
            response = self._backend.stream(
                text, images=images, message_history=state.history
            )
            try:
                async for event in response:
                    task = asyncio.current_task()
                    if state.generation != generation or (task and task.cancelling()):
                        raise asyncio.CancelledError("chat turn was cancelled")
                    if result is not None:
                        raise RuntimeError("backend returned an event after its final result")
                    if isinstance(event, ChatTurnResult):
                        result = event
                    elif isinstance(event, str):
                        if event:
                            has_text = has_text or bool(event.strip())
                            yield event
                    else:
                        raise TypeError("backend returned an unsupported stream event")
            finally:
                close = getattr(response, "aclose", None)
                if close is not None:
                    await close()
            # A backend may accidentally suppress CancelledError. Never commit
            # its late result after the caller has cancelled this turn.
            task = asyncio.current_task()
            if state.generation != generation or (task and task.cancelling()):
                raise asyncio.CancelledError("chat turn was cancelled")
            if result is None:
                raise RuntimeError("chat response ended without a final result")
            if not has_text:
                raise ValueError("backend returned no assistant text")
            state.history = result.messages
