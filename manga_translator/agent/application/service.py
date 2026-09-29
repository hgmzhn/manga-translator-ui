"""Qt-independent conversational use cases."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

from typing import TYPE_CHECKING, Protocol

from ..domain.chat import ChatActivity, ChatCanvas, ChatImage

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from ..domain.tool_models import ToolContext


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
        tool_context: ToolContext | None = None,
    ) -> AsyncIterator[str | ChatActivity | ChatCanvas | ChatTurnResult]:
        """Propagate errors and cancellation without yielding a final result."""


@dataclass(slots=True)
class _SessionState:
    history: list[ModelMessage] = field(default_factory=list)
    generation: int = 0
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task | None = None
    clearing: bool = False
    clear_task: asyncio.Task | None = None


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
        self._closed = False

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

    async def clear_runtime(self, tool_context: ToolContext | None = None) -> None:
        """Release delegated page work while keeping the backend reusable."""
        clear = getattr(self._backend, "clear_runtime", None)
        if clear is not None:
            await clear(tool_context)

    async def clear_session(
        self, session_id: str = "default", *, tool_context: ToolContext | None = None,
    ) -> None:
        """Stop the old conversation before resetting its command namespace.

        Keep task identity, workspace drafts, renderer and grants stable so the
        host's preview binding remains valid. A cancelled/retired workspace is
        never reactivated; live contexts need no parent cancellation signal.
        """
        state = self._session(session_id)
        active = state.active_task
        if active is asyncio.current_task():
            raise RuntimeError("Close the active stream before clearing it from its consuming task")
        if state.clear_task is None or state.clear_task.done():
            state.clearing = True
            self.clear(session_id)
            state.clear_task = asyncio.create_task(
                self._clear_session(session_id, state, active, tool_context),
                name="manga-agent-session-clear",
            )
        # Folder replacement or shutdown can cancel the UI waiter. Cleanup
        # itself must finish; aclose joins it before releasing the backend.
        await asyncio.shield(state.clear_task)

    async def _clear_session(self, session_id, state, active, tool_context):
        try:
            if active is not None and not active.done():
                if not active.cancelling():
                    active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            async with state.turn_lock:
                await self.clear_runtime(tool_context)
                if tool_context is not None:
                    # An explicitly injected/shared runtime remains host-owned.
                    # Cancel only this parent's work before changing its epoch.
                    if tool_context.runtime is not None:
                        await tool_context.runtime.cancel_tasks(tool_context)
                    tool_context.command_generation = uuid4().hex
                    for field_name in (
                        "command_payloads", "resolved_edit_commands", "read_snapshots",
                        "read_policies", "observed_revisions", "transaction_results",
                    ):
                        getattr(tool_context, field_name).clear()
                if self._sessions.get(session_id) is state:
                    self._sessions.pop(session_id)
        finally:
            state.clearing = False

    async def aclose(self) -> None:
        """Invalidate sessions and release backend-owned delegated tasks and clients."""
        self._closed = True
        active_tasks = set()
        for state in self._sessions.values():
            state.history = []
            state.generation += 1
            active = state.active_task
            if active is not None and active is not asyncio.current_task() and not active.done():
                if not active.cancelling():
                    active.cancel()
                active_tasks.add(active)
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        clear_tasks = [
            state.clear_task for state in self._sessions.values()
            if state.clear_task is not None and state.clear_task is not asyncio.current_task()
        ]
        if clear_tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in clear_tasks))
        close = getattr(self._backend, "aclose", None)
        if close is not None:
            await close()
        self._sessions.clear()

    async def stream(
        self,
        text: str,
        *,
        images: tuple[ChatImage, ...] = (),
        session_id: str = "default",
        tool_context: ToolContext | None = None,
    ) -> AsyncIterator[str | ChatActivity | ChatCanvas]:
        """Yield deltas and commit only after normal exhaustion.

        Consumers stopping early must close the iterator, for example with
        ``contextlib.aclosing``, to release the backend and session turn lock.
        """
        if self._closed:
            raise RuntimeError("Chat service is closed")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        images = tuple(images)
        if any(not isinstance(image, ChatImage) for image in images):
            raise TypeError("message images must contain ChatImage values")
        # Keep originals in host history; ModelImageBudget compresses wire copies.
        if not text.strip() and not images:
            raise ValueError("a chat turn must contain text or images")

        state = self._session(session_id)
        if state.clearing:
            raise RuntimeError("Chat session is being cleared")
        generation = state.generation
        async with state.turn_lock:
            if self._closed or state.clearing or state.generation != generation:
                raise asyncio.CancelledError("session was cleared")
            result: ChatTurnResult | None = None
            has_text = False
            options = {"tool_context": tool_context} if tool_context is not None else {}
            response = self._backend.stream(text, images=images, message_history=state.history, **options)
            state.active_task = asyncio.current_task()
            try:
                async for event in response:
                    task = asyncio.current_task()
                    if state.generation != generation or (task and task.cancelling()):
                        raise asyncio.CancelledError("chat turn was cancelled")
                    if result is not None:
                        raise RuntimeError("backend returned an event after its final result")
                    if isinstance(event, ChatTurnResult):
                        result = event
                    elif isinstance(event, (ChatActivity, ChatCanvas)):
                        yield event
                    elif isinstance(event, str):
                        if event:
                            has_text = has_text or bool(event.strip())
                            yield event
                    else:
                        raise TypeError("backend returned an unsupported stream event")
            finally:
                try:
                    close = getattr(response, "aclose", None)
                    if close is not None:
                        await close()
                finally:
                    state.active_task = None
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
