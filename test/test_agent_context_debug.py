import _bootstrap  # noqa: F401

import asyncio
import base64
import copy
import json
from pathlib import Path
import sys

import httpx
import pytest
from pydantic_ai.messages import PartStartEvent, TextPart

from manga_translator.agent.providers.openai import OpenAIResponsesBackend
from manga_translator.agent.providers.request_debug import ContextTrace
from manga_translator.utils import system_proxy


def test_debug_snapshots_preserve_full_text_without_mutating_request_or_retaining_image_bytes():
    secret = "debug-key-secret"
    encoded = base64.b64encode(b"image bytes").decode()
    body = {"input": [{"role": "user", "content": [
        {"type": "input_text", "text": "完整输入" * 1000 + secret},
        {"type": "input_image", "image_url": "data:image/png;base64," + encoded},
    ]}], "tools": [{"name": "apply_edits"}]}
    original = copy.deepcopy(body)
    events = []
    ContextTrace(events.append, secret=secret).add("request", {"body": body})
    assert body == original
    shown = events[0]["data"]["body"]
    assert shown["input"][0]["content"][0]["text"] == "完整输入" * 1000 + "[redacted]"
    assert "base64 omitted" in shown["input"][0]["content"][1]["image_url"]
    assert shown["tools"] == body["tools"]
    body["tools"].clear()
    assert shown["tools"] == [{"name": "apply_edits"}]


@pytest.mark.parametrize("failure", ["null_output", "http_error"])
def test_real_sdk_failure_retains_request_partial_response_and_traceback(monkeypatch, failure):
    debug = []
    requests = []
    secret = "debug-key-secret"
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1",
            "name": "apply_edits", "arguments": '{"page":{"id":1}}'}
    response = {"id": "resp_1", "object": "response", "model": "grok-4.6",
                "created_at": 1, "status": "completed", "output": None}
    events = [
        {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 0,
         "delta": item["arguments"]},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    body = "".join("event: " + event["type"] + "\ndata: "
                   + json.dumps({**event, "sequence_number": index}) + "\n\n"
                   for index, event in enumerate(events))

    async def handle(request):
        requests.append(json.loads(request.content))
        if failure == "http_error":
            return httpx.Response(400, json={"error": {"message": "invalid request " + secret,
                                                       "type": "invalid_request_error"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def run():
        from pydantic_ai.exceptions import ModelHTTPError

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(system_proxy, "openai_http_client_kwargs", lambda _: {"http_client": client})
        backend = OpenAIResponsesBackend(api_key=secret, model="grok-4.6", base_url="https://offline.invalid/v1",
                                         on_debug_event=debug.append)
        with pytest.raises(TypeError if failure == "null_output" else ModelHTTPError):
            _ = [event async for event in backend.stream("编辑当前页", images=(), message_history=[])]
        assert client.is_closed
    asyncio.run(run())

    records = list({event["id"]: event for event in debug}.values())
    assert records[0]["kind"] == "turn_start"
    request = next(event["data"]["body"] for event in records if event["kind"] == "request")
    assert request == requests[0]
    assert request["tools"][0]["name"] == "apply_edits"
    assert request["input"]
    error = records[-1]
    assert error["kind"] == "error"
    assert "Traceback (most recent call last)" in error["data"]["traceback"]
    assert secret not in json.dumps(records)
    assert not any(event["kind"] == "turn_complete" for event in records)
    if failure == "null_output":
        partial = next(event["data"] for event in records if event["kind"] == "model_response")
        assert partial["state"] == "interrupted"
        assert partial["parts"][0]["args"] == item["arguments"]
        assert error["data"]["exception_type"] == "TypeError"
        assert "NoneType" in error["data"]["message"]
    else:
        assert error["data"]["response_body"]


def test_closing_stream_keeps_partial_output_and_marks_context_interrupted(monkeypatch):
    debug = []
    closed = []
    backend = OpenAIResponsesBackend(api_key="offline-test", model="offline", on_debug_event=debug.append)

    async def stream(*args, trace, **kwargs):
        try:
            trace.response_event(0, PartStartEvent(index=0, part=TextPart("部分输出")))
            yield "部分输出"
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    monkeypatch.setattr(backend, "_stream", stream)

    async def run():
        response = backend.stream("test", images=(), message_history=[])
        assert await anext(response) == "部分输出"
        await response.aclose()
    asyncio.run(run())
    assert closed == [True]
    records = list({event["id"]: event for event in debug}.values())
    assert records[-1]["kind"] == "cancelled"
    assert records[-2]["data"]["state"] == "interrupted"
    assert records[-2]["data"]["parts"][0]["content"] == "部分输出"


def main():
    return pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
