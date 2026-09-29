"""Own configured child-model clients across manager turns, outside the GUI."""

from __future__ import annotations

import asyncio
import json

from ..runtime.page_tasks import PageTaskRuntime, current_page_trace


class OpenAIPageTaskRuntime(PageTaskRuntime):
    """Close executions before releasing the client shared by child agents."""

    def __init__(self, *, client, on_closed=None, **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._close_task: asyncio.Task[None] | None = None
        self._on_closed = on_closed

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(
                self._close_resources(), name="manga-page-runtime-close"
            )
        # A cancelled manager turn must not interrupt resource cleanup started
        # by clear, workspace reload, or application shutdown.
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        try:
            await super().close()
        finally:
            try:
                await self._client.close()
            finally:
                callback, self._on_closed = self._on_closed, None
                if callback is not None:
                    callback(self)


async def create_page_runtime(
    *, api_key: str, model: str, base_url: str, timeout: float,
    model_settings: dict, on_debug_event=None, on_closed=None,
) -> OpenAIPageTaskRuntime:
    """Create a separate child transport; construction sends no model request."""
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient

    from ...utils.system_proxy import openai_http_client_kwargs
    from ..providers.responses import create_responses_model

    options = openai_http_client_kwargs(base_url)
    if on_debug_event is not None:
        http_client = options.get("http_client")
        if http_client is None:
            http_client = DefaultAsyncHttpxClient()
            options["http_client"] = http_client

        async def capture_child_request(request):
            trace = current_page_trace.get()
            if trace is not None and trace.enabled and request.content:
                trace.add("request", {"body": json.loads(request.content)})

        http_client.event_hooks["request"].append(capture_child_request)

    client = None
    try:
        client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1, **options,
        )
        child_model = create_responses_model(model, client=client)
        return OpenAIPageTaskRuntime(
            client=client, model=child_model, model_settings=model_settings,
            on_debug_event=on_debug_event, debug_secret=api_key, on_closed=on_closed,
        )
    except BaseException:
        if client is not None:
            await client.close()
        elif options.get("http_client") is not None:
            await options["http_client"].aclose()
        raise
