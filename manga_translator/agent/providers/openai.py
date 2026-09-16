"""An OpenAI Responses adapter with multimodal input and streamed text output."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Callable, cast

from ..application.service import ChatTurnResult
from ..domain.chat import ChatImage
from ...utils.openai_compat import resolve_openai_compatible_api_key

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


class OpenAIResponsesBackend:
    """Stream Responses text; construction never imports the SDK or connects.

    Each stream owns its client and closes it on success, failure, or
    cancellation. Credentials and the model are supplied by the caller, not
    discovered from the application's translation settings or environment.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        on_request_body: Callable[[str], None] | None = None,
        reasoning_effort: str | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if reasoning_effort is not None:
            if not isinstance(reasoning_effort, str):
                raise TypeError("reasoning_effort must be a string or None")
            if not reasoning_effort.strip():
                raise ValueError("reasoning_effort must be non-empty when supplied")
        resolved_key = resolve_openai_compatible_api_key(api_key, base_url)
        if not resolved_key:
            raise ValueError("api_key is required for this endpoint")
        self._api_key = resolved_key
        self._model = model.strip()
        self._base_url = base_url.strip()
        self._timeout = timeout
        self._on_request_body = on_request_body
        self._reasoning_effort = (
            reasoning_effort.strip() if reasoning_effort is not None else None
        )
        self._on_thinking = on_thinking

    def set_thinking_observer(
        self, observer: Callable[[str], None] | None
    ) -> None:
        """Bind the observer for the next stream without changing active streams."""
        self._on_thinking = observer

    async def stream(
        self,
        text: str,
        *,
        images: tuple[ChatImage, ...],
        message_history: list[ModelMessage],
    ) -> AsyncIterator[str | ChatTurnResult]:
        thinking_observer = self._on_thinking
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        from openai.types.shared import ReasoningEffort
        from pydantic_ai import Agent, AgentRunResultEvent, BinaryContent
        from pydantic_ai.messages import (
            PartDeltaEvent,
            PartEndEvent,
            PartStartEvent,
            TextPart,
            TextPartDelta,
            ThinkingPart,
            ThinkingPartDelta,
        )
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.usage import UsageLimits

        from ...utils.system_proxy import openai_http_client_kwargs

        prompt: str | list[str | BinaryContent] = text
        if images:
            prompt = [text] if text else []
            prompt.extend(
                BinaryContent(data=image.data, media_type=image.media_type)
                for image in images
            )

        client_options = openai_http_client_kwargs(self._base_url)
        if self._on_request_body is not None:
            observer = self._on_request_body

            async def capture_request(request):
                # Capture the SDK's serialized JSON, never headers or credentials.
                if request.content:
                    from .request_debug import format_request_body

                    observer(format_request_body(request.content))

            http_client = client_options.get("http_client")
            if http_client is None:
                http_client = DefaultAsyncHttpxClient()
                client_options["http_client"] = http_client
            http_client.event_hooks["request"].append(capture_request)

        async with AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            **client_options,
        ) as client:
            model = OpenAIResponsesModel(
                self._model, provider=OpenAIProvider(openai_client=client)
            )
            settings = OpenAIResponsesModelSettings(
                openai_store=False, openai_reasoning_summary="auto"
            )
            if self._reasoning_effort is not None:
                settings["openai_reasoning_effort"] = cast(
                    ReasoningEffort, self._reasoning_effort
                )
            agent = Agent(model, output_type=str, retries=0, model_settings=settings)
            agent.instrument = False
            has_text = False
            thinking_parts: dict[int, str] = {}
            thinking_states: dict[int, ThinkingPart] = {}
            thinking_snapshot = ""
            result = None
            async with agent.run_stream_events(
                prompt,
                message_history=message_history,
                usage_limits=UsageLimits(request_limit=1),
            ) as events:
                async for event in events:
                    delta = ""
                    thinking_part = None
                    if isinstance(event, PartStartEvent):
                        if isinstance(event.part, TextPart):
                            delta = event.part.content
                        elif isinstance(event.part, ThinkingPart):
                            thinking_part = event.part
                    elif isinstance(event, PartDeltaEvent):
                        if isinstance(event.delta, TextPartDelta):
                            delta = event.delta.content_delta
                        elif thinking_observer is not None and isinstance(event.delta, ThinkingPartDelta):
                            previous = thinking_states.get(event.index, ThinkingPart(content=""))
                            thinking_part = event.delta.apply(previous)
                    elif isinstance(event, PartEndEvent):
                        if isinstance(event.part, ThinkingPart):
                            thinking_part = event.part
                    elif isinstance(event, AgentRunResultEvent):
                        result = event.result

                    if thinking_observer is not None and thinking_part is not None:
                        thinking_states[event.index] = thinking_part
                        # Responses raw reasoning is public text stored separately from summaries.
                        # Never display signatures, encrypted content, or unrelated metadata.
                        raw_content = (thinking_part.provider_details or {}).get("raw_content")
                        thinking_content = (
                            "\n\n".join(part for part in raw_content if isinstance(part, str))
                            if isinstance(raw_content, list) and raw_content
                            else thinking_part.content
                        )
                        if thinking_parts.get(event.index, "") != thinking_content:
                            thinking_parts[event.index] = thinking_content
                            snapshot = "\n\n".join(
                                content for _, content in sorted(thinking_parts.items())
                                if content
                            )
                            if snapshot != thinking_snapshot:
                                thinking_snapshot = snapshot
                                thinking_observer(snapshot)
                    if delta:
                        has_text = has_text or bool(delta.strip())
                        yield delta
            if result is None:
                raise RuntimeError("chat response ended without a final result")
            response = result.response
            if response.state != "complete" or response.finish_reason != "stop":
                raise RuntimeError(
                    "chat response did not complete "
                    f"(state={response.state}, finish_reason={response.finish_reason})"
                )
            if not has_text:
                raise ValueError("chat backend returned no assistant text")
        yield ChatTurnResult(messages=result.all_messages())
