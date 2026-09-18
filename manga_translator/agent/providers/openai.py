"""An OpenAI Responses adapter with multimodal input and streamed text output."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from contextlib import aclosing
from typing import TYPE_CHECKING, AsyncIterator, Callable, cast

from ..application.service import ChatTurnResult
from ..domain.chat import ChatCanvas, ChatImage
from ...utils.openai_compat import resolve_openai_compatible_api_key
from .request_debug import ContextTrace

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from ..domain.tool_models import ToolContext


logger = logging.getLogger(__name__)


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
        on_debug_event: Callable[[dict], None] | None = None,
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
        self._on_debug_event = on_debug_event
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
        tool_context: ToolContext | None = None,
    ) -> AsyncIterator[str | ChatCanvas | ChatTurnResult]:
        trace = ContextTrace(self._on_debug_event, secret=self._api_key)
        trace.add("turn_start", {
            "model": self._model, "task_id": tool_context.task_id if tool_context is not None else "chat",
            "text": text, "images": images, "message_history": message_history,
        })
        try:
            async with aclosing(self._stream(
                text, images=images, message_history=message_history, tool_context=tool_context, trace=trace,
            )) as stream:
                async for event in stream:
                    yield event
        except (asyncio.CancelledError, GeneratorExit):
            trace.finish_response("interrupted")
            trace.add("cancelled", {"message": "The turn was cancelled or its stream was closed."})
            raise
        except Exception as error:
            trace.finish_response("interrupted")
            trace.add("error", {
                "exception_type": type(error).__name__, "message": str(error),
                "traceback": "".join(traceback.format_exception(error)),
                "response_body": getattr(error, "body", None),
            })
            raise

    async def _stream(
        self, text: str, *, images: tuple[ChatImage, ...], message_history: list[ModelMessage],
        tool_context: ToolContext | None, trace: ContextTrace,
    ) -> AsyncIterator[str | ChatCanvas | ChatTurnResult]:
        thinking_observer = self._on_thinking
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        from openai.types.shared import ReasoningEffort
        from pydantic_ai import AgentRunResultEvent, BinaryContent
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            PartDeltaEvent,
            PartEndEvent,
            PartStartEvent,
            RetryPromptPart,
            TextPart,
            TextPartDelta,
            ThinkingPart,
            ThinkingPartDelta,
            ToolReturnPart,
        )
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.usage import UsageLimits

        from ...utils.system_proxy import openai_http_client_kwargs
        from ..agents.chat import canvas_from_tool_result, create_agent, empty_context

        context = tool_context if tool_context is not None else empty_context()
        logger.info("Agent turn started: model=%s task=%s history=%d images=%d",
                    self._model, context.task_id, len(message_history), len(images))

        prompt: str | list[str | BinaryContent] = text
        if images:
            prompt = [text] if text else []
            prompt.extend(
                BinaryContent(data=image.data, media_type=image.media_type)
                for image in images
            )

        client_options = openai_http_client_kwargs(self._base_url)
        if self._on_request_body is not None or trace.enabled:
            observer = self._on_request_body
            request_index = 0

            async def capture_request(request):
                nonlocal request_index
                request_index += 1
                logger.info("Model request sent: model=%s task=%s", self._model, context.task_id)
                # Capture the SDK's serialized JSON, never headers or credentials.
                if request.content:
                    from .request_debug import format_request_body

                    if trace.enabled:
                        trace.add("request", {"request_index": request_index, "body": json.loads(request.content)})
                    if observer is not None:
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
            agent = create_agent(model, model_settings=settings)
            agent.instrument = False
            has_text = False
            thinking_parts: dict[tuple[int, int], str] = {}
            thinking_states: dict[tuple[int, int], ThinkingPart] = {}
            response_index = -1
            thinking_snapshot = ""
            result = None
            async with agent.run_stream_events(
                prompt,
                message_history=message_history,
                deps=context,
                usage_limits=UsageLimits(request_limit=30, tool_calls_limit=100),
            ) as events:
                async for event in events:
                    delta = ""
                    thinking_part = None
                    if isinstance(event, PartStartEvent):
                        if event.index == 0:
                            response_index += 1
                            logger.info("Model response started: task=%s step=%d",
                                        context.task_id, response_index + 1)
                        if isinstance(event.part, TextPart):
                            delta = event.part.content
                        elif isinstance(event.part, ThinkingPart):
                            thinking_part = event.part
                    elif isinstance(event, PartDeltaEvent):
                        if isinstance(event.delta, TextPartDelta):
                            delta = event.delta.content_delta
                        elif thinking_observer is not None and isinstance(event.delta, ThinkingPartDelta):
                            previous = thinking_states.get((response_index, event.index), ThinkingPart(content=""))
                            thinking_part = event.delta.apply(previous)
                    elif isinstance(event, PartEndEvent):
                        if isinstance(event.part, ThinkingPart):
                            thinking_part = event.part
                    elif isinstance(event, AgentRunResultEvent):
                        result = event.result
                        trace.finish_response()
                    elif isinstance(event, FunctionToolCallEvent):
                        trace.finish_response()
                        trace.add("tool_call", {
                            "tool_name": event.part.tool_name, "tool_call_id": event.part.tool_call_id,
                            "arguments": event.part.args, "args_valid": event.args_valid,
                        })
                        logger.info("Tool call: task=%s tool=%s call_id=%s args_valid=%s",
                                    context.task_id, event.part.tool_name,
                                    event.part.tool_call_id, event.args_valid)
                    elif isinstance(event, FunctionToolResultEvent):
                        part = event.part
                        feedback = part.content
                        if isinstance(part, RetryPromptPart) and isinstance(feedback, str):
                            try:
                                report = json.loads(feedback)
                            except ValueError:
                                pass
                            else:
                                if isinstance(report, dict) and report.get("status") == "validation_error":
                                    feedback = report
                        trace.add("validation_error" if isinstance(part, RetryPromptPart) else "tool_result", {
                            "tool_name": part.tool_name, "tool_call_id": part.tool_call_id,
                            "result": feedback, "content": event.content,
                            "metadata": getattr(part, "metadata", None),
                            **({"model_feedback": part.model_response()} if isinstance(part, RetryPromptPart) else {}),
                        })
                        if isinstance(part, RetryPromptPart):
                            errors = feedback.get("errors", []) if isinstance(feedback, dict) else feedback
                            details = errors if isinstance(errors, str) else "; ".join(
                                ".".join(map(str, item.get("path", item["loc"]))) + ": " + item.get("reason", item["msg"])
                                for item in errors
                            )
                            logger.warning("Tool arguments rejected: tool=%s call_id=%s details=%s",
                                           part.tool_name, part.tool_call_id, details)
                        data = part.content if isinstance(part.content, dict) else {}
                        error = data.get("error") or data.get("render_error") or {}
                        logger.info(
                            "Tool result: task=%s tool=%s call_id=%s status=%s render_status=%s error=%s",
                            context.task_id, part.tool_name, part.tool_call_id,
                            data.get("status", "returned" if isinstance(part, ToolReturnPart) else "retry"),
                            data.get("render_status", "-"),
                            error.get("code", "-") if isinstance(error, dict) else "-",
                        )
                        canvas = canvas_from_tool_result(event, context)
                        if canvas is not None:
                            yield canvas

                    if isinstance(event, (PartStartEvent, PartDeltaEvent, PartEndEvent)):
                        trace.response_event(response_index, event)
                    if thinking_observer is not None and thinking_part is not None:
                        key = (response_index, event.index)
                        thinking_states[key] = thinking_part
                        # Responses raw reasoning is public text stored separately from summaries.
                        # Never display signatures, encrypted content, or unrelated metadata.
                        raw_content = (thinking_part.provider_details or {}).get("raw_content")
                        thinking_content = (
                            "\n\n".join(part for part in raw_content if isinstance(part, str))
                            if isinstance(raw_content, list) and raw_content
                            else thinking_part.content
                        )
                        if thinking_parts.get(key, "") != thinking_content:
                            thinking_parts[key] = thinking_content
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
        logger.info("Agent turn completed: task=%s model_responses=%d", context.task_id, response_index + 1)
        trace.add("turn_complete", {"messages": result.all_messages(), "usage": result.usage})
        yield ChatTurnResult(messages=result.all_messages())
