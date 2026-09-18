import _bootstrap  # noqa: F401

import asyncio
import copy
import io
import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import (
    ImageUrl, ModelRequest, ModelResponse, TextPart, ThinkingPart,
    ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from manga_translator.agent.context.images import EDIT_IMAGE_METADATA, prune_images_after_edit
from manga_translator.agent.domain.tool_models import AccessGrant, Edit, ToolContext, ToolError
from manga_translator.agent.tools import create_toolset
from manga_translator.agent.tools.builtin.page import apply_edits, read_page, revert_edits
from manga_translator.agent.workspace import Workspace


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (12, 12), color).save(output, format="PNG")
    return output.getvalue()


class Renderer:
    def __init__(self):
        self.snapshots = []
        self.error = None

    async def observe(self, snapshot, **kwargs):
        self.snapshots.append(copy.deepcopy(snapshot))
        if self.error:
            raise self.error
        return {
            "image": png((int(snapshot["regions"][0]["font_size"]), 0, 0)),
            "mime_type": "image/png", "page_id": snapshot["page_id"],
            "rendered_revision": snapshot["revision"], "view": "rendered",
            "width": 12, "height": 12,
        }

    async def close(self):
        pass


@pytest.fixture
def ctx():
    workspace = Workspace()
    workspace.register_page({
        "page_id": "p", "work_id": "w", "chapter_id": "c", "order": 0,
        "folder": ".", "name": "page.png", "width": 120, "height": 120,
        "regions": [{
            "region_id": "r", "texts": ["原文"], "translation": "译文",
            "translation_raw": "最初译文", "font_size": 20,
            "lines": [[[0, 0], [100, 0], [100, 100], [0, 100]]], "angle": 0,
            "source_lang": "JPN", "target_lang": "CHS", "prob": 0.9,
            "shadow_offset": [1, 2], "is_locked": False,
            "render_box_rect_local": [1, 2, 3, 4],
            "white_frame_rect_local": [1, 2, 3, 4], "has_custom_white_frame": True,
            "dst_points": [[[1, 2], [3, 2], [3, 4], [1, 4]]],
            "_host_path": "private", "font_path": "private-font",
        }],
    })
    return SimpleNamespace(deps=ToolContext(
        workspace, AccessGrant({"p"}, {"p"}, {"p"}, set()), "task", renderer=Renderer()
    ))


def edit(size=30):
    return TypeAdapter(Edit).validate_python({
        "op": "set_region_style", "region_id": "r", "style": {"font_size": size}
    })


def images_in(messages):
    images = []
    def visit(value):
        if isinstance(value, ImageUrl) or isinstance(value, BinaryContent) and value.is_image:
            images.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
    for message in messages:
        for part in message.parts:
            if isinstance(part, (UserPromptPart, ToolReturnPart)):
                visit(part.content)
    return images


def test_edit_returns_full_current_properties_then_image_and_allows_next_edit(ctx):
    async def run():
        await read_page(ctx, {"id": 1})
        result = await apply_edits(ctx, {"id": 1}, [edit()], "first")
        data = result.return_value
        assert data["status"] == "accepted" and data["render_status"] == "rendered"
        region = data["page"]["regions"][0]
        assert region["font_size"] == 30
        assert region["translation_raw"] == "最初译文"
        assert region["source_lang"] == "JPN" and region["prob"] == 0.9
        assert region["shadow_offset"] == [1, 2] and region["is_locked"] is False
        for key in ("render_box_rect_local", "white_frame_rect_local", "has_custom_white_frame",
                    "dst_points", "version", "_host_path", "font_path"):
            assert key not in region
        assert isinstance(result.content[-1], BinaryContent)
        assert all(isinstance(item, str) for item in result.content[:-1])
        assert "保留文字、思考、工具调用和工具结果" in result.content[0]
        assert ctx.deps.observed_revisions["p"] == 2
        assert ctx.deps.renderer.snapshots[-1]["regions"][0]["font_size"] == 30
        again = await apply_edits(ctx, {"id": 1}, [edit(40)], "second")
        assert again.return_value["page"]["regions"][0]["font_size"] == 40
        assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 3
    asyncio.run(run())


def test_revert_automatically_returns_restored_attributes_and_image(ctx):
    async def run():
        await read_page(ctx, {"id": 1})
        result = await apply_edits(ctx, {"id": 1}, [edit()], "edit")
        restored = await revert_edits(ctx, result.return_value["transaction_id"], "undo")
        assert restored.return_value["page"]["regions"][0]["font_size"] == 20
        assert isinstance(restored.content[-1], BinaryContent)
    asyncio.run(run())


def test_render_failure_reports_committed_edit_without_reapplying_on_retry(ctx):
    async def run():
        await read_page(ctx, {"id": 1})
        ctx.deps.renderer.error = ToolError("missing_asset", "No base image")
        result = await apply_edits(ctx, {"id": 1}, [edit()], "edit")
        assert result.return_value["status"] == "accepted"
        assert result.return_value["render_status"] == "failed"
        assert result.return_value["render_error"]["code"] == "missing_asset"
        assert result.metadata[EDIT_IMAGE_METADATA] == []
        assert all(isinstance(item, str) for item in result.content)
        assert "p" not in ctx.deps.observed_revisions
        ctx.deps.renderer.error = None
        retried = await apply_edits(ctx, {"id": 1}, [edit()], "edit")
        assert retried.return_value["transaction_id"] == result.return_value["transaction_id"]
        assert retried.return_value["render_status"] == "rendered"
        assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 2
    asyncio.run(run())


def test_unexpected_render_error_logs_traceback_and_keeps_committed_status(ctx, caplog):
    async def run():
        await read_page(ctx, {"id": 1})
        ctx.deps.renderer.error = TypeError("'NoneType' object is not iterable")
        result = await apply_edits(ctx, {"id": 1}, [edit()], "render-error")
        assert result.return_value["status"] == "accepted"
        assert result.return_value["render_status"] == "failed"
        assert result.return_value["render_error"]["code"] == "render_failed"
        assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 2
    asyncio.run(run())
    assert "Post-edit render failed: task=task page=p revision=2" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "TypeError: 'NoneType' object is not iterable" in caplog.text


@pytest.mark.parametrize("failure", ["read", "locked", "permission", "conflict"])
def test_rejected_edits_do_not_render_or_mark_history_for_deletion(ctx, failure):
    async def run():
        if failure != "read":
            await read_page(ctx, {"id": 1})
        if failure == "locked":
            ctx.deps.workspace._pages["p"]["regions"][0]["is_locked"] = True
        elif failure == "permission":
            ctx.deps.grant.layout_pages.clear()
        elif failure == "conflict":
            ctx.deps.workspace.apply_edits(ctx.deps, "p", {"r": 1}, 1, [edit(25)], "external")
        result = await apply_edits(ctx, {"id": 1}, [edit()], "edit")
        assert result["status"] == "error"
        assert not ctx.deps.renderer.snapshots
    asyncio.run(run())


def test_history_removes_images_only_and_preserves_native_thinking_and_tool_links():
    old = BinaryContent(data=png("red"), media_type="image/png")
    current = BinaryContent(data=png("blue"), media_type="image/png", identifier="latest")
    document = BinaryContent(data=b"document", media_type="application/pdf")
    thinking = ThinkingPart(content="existing summary", signature="opaque-signature",
                            provider_name="openai", provider_details={"raw_content": ["existing"]})
    response = ModelResponse(parts=[thinking, TextPart("editing"), ToolCallPart("apply_edits", {}, "call")])
    history = [
        ModelRequest(parts=[UserPromptPart(["initial", old, document])]), response,
        ModelRequest(parts=[ToolReturnPart("observe_canvas", {"image": ImageUrl("https://example.com/old.png")}, "observe")]),
        ModelRequest(parts=[ToolReturnPart("apply_edits", {"status": "accepted"}, "call",
                           metadata={EDIT_IMAGE_METADATA: ["latest"]}), UserPromptPart(["properties", current])]),
        ModelRequest(parts=[UserPromptPart(["new user image", old])]),
    ]
    saved_initial = history[0]
    prune_images_after_edit(history)
    assert images_in(history) == [current, old]
    assert history[1] is response and history[1].parts[0] is thinking
    assert history[0].parts[0].content[0] == "initial"
    assert history[0].parts[0].content[-1] is document
    assert saved_initial.parts[0].content[1] is old
    assert history[3].parts[0].tool_call_id == "call"


def test_initial_history_is_untouched_without_an_edit():
    request = ModelRequest(parts=[UserPromptPart(["initial", BinaryContent(data=png("red"), media_type="image/png")])])
    history = [request]
    prune_images_after_edit(history)
    assert history[0] is request


def test_native_agent_receives_latest_image_and_thinking_after_two_edits_in_one_batch(ctx):
    async def run():
        step = 0
        thought = ThinkingPart(content="retained thought", signature="opaque-signature")
        async def model(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                assert len(images_in(messages)) == 1
                return ModelResponse(parts=[ToolCallPart("read_page", {"page": {"id": 1}}, "read")])
            if step == 2:
                return ModelResponse(parts=[thought, *[
                    ToolCallPart("apply_edits", {"page": {"id": 1}, "edits": [edit(size).model_dump(exclude_unset=True)],
                                                 "command_id": f"edit-{size}"}, f"call-{size}")
                    for size in (30, 40)
                ]])
            assert step == 3
            assert len(images_in(messages)) == 1
            assert images_in(messages)[0].data == png((40, 0, 0))
            assert any(part is thought for message in messages for part in message.parts)
            results = [part for message in messages for part in message.parts
                       if isinstance(part, ToolReturnPart) and part.tool_name == "apply_edits"]
            assert isinstance(results[0].content["page"]["regions"], str)
            assert results[1].content["page"]["regions"][0]["font_size"] == 40
            read_result = next(part for message in messages for part in message.parts
                               if isinstance(part, ToolReturnPart) and part.tool_name == "read_page")
            assert isinstance(read_result.content["regions"], str)
            assert "\"font_size\": 30" not in str(messages)
            return ModelResponse(parts=[TextPart("完成")])
        agent = Agent(FunctionModel(model), deps_type=ToolContext, toolsets=[create_toolset()])
        result = await agent.run(["初始上下文不变", BinaryContent(data=png("red"), media_type="image/png")], deps=ctx.deps)
        assert result.output == "完成"
        assert len(images_in(result.all_messages())) == 1
    asyncio.run(run())


def test_markdown_rich_text_examples_match_edit_and_renderer_protocols():
    from manga_translator.agent.domain.tool_models import RichDocument
    from manga_translator.agent.prompts import load_prompt
    from manga_translator.rendering.rich_text import RichTextDocument

    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", load_prompt("rich_text"), re.S)]
    assert len(examples) == 2
    operation = TypeAdapter(Edit).validate_python(examples[0]["edits"][0])
    document = operation.document.model_dump(mode="json", exclude_none=True)
    assert RichTextDocument.from_dict(document).plain_text() == "这是重要的事情。"
    ruby = RichDocument.model_validate(examples[1]).model_dump(mode="json", exclude_none=True)
    assert RichTextDocument.from_dict(ruby).plain_text() == "漢字12"


def test_main_agent_exposes_compact_schema_and_applies_markdown_rich_text(ctx):
    from pydantic_ai.profiles.openai import openai_model_profile
    from pydantic_ai.tools import Tool
    from manga_translator.agent.agents.chat import create_agent
    from manga_translator.agent.prompts import load_prompt

    original_schema = json.dumps(Tool(apply_edits).tool_def.parameters_json_schema)
    args = json.loads(re.search(r"```json\n(.*?)\n```", load_prompt("rich_text"), re.S)[1])
    args["edits"][0]["region_id"] = "r"

    async def run():
        await read_page(ctx, {"id": 1})
        requests = 0
        async def model(messages, info):
            nonlocal requests
            requests += 1
            assert [tool.name for tool in info.function_tools] == ["apply_edits"]
            tool = info.function_tools[0]
            compact_schema = json.dumps(tool.parameters_json_schema)
            assert tool.strict is False
            assert len(compact_schema) < len(original_schema) * 0.8
            assert '"default"' not in compact_schema and '"title"' not in compact_schema
            assert '"type": "null"' not in compact_schema
            assert set(tool.parameters_json_schema["required"]) == {"page", "command_id", "edits"}
            definitions = tool.parameters_json_schema["$defs"]
            for operation, payload in (("SetRegionStyle", "style"), ("ReplaceRichText", "document"),
                                       ("SetGeometry", "geometry"), ("SetTranslation", "text")):
                assert {"op", "region_id", payload} <= set(definitions[operation]["required"])
            assert definitions["RegionStylePatch"]["properties"]["font_size"] == {
                "type": "number", "exclusiveMinimum": 0,
            }
            assert definitions["StrokePatch"]["properties"]["width"]["minimum"] == 0
            assert definitions["TextStylePatch"]["properties"]["stroke"]["$ref"] == "#/$defs/StrokePatch"
            assert definitions["RichDocument"]["properties"]["blocks"]["items"]["$ref"] == "#/$defs/Paragraph"
            assert definitions["GeometryPatch"]["properties"]["center"]["minItems"] == 2
            assert "type" in definitions["TextRun"]["required"]
            assert "font_size" in info.instructions and "occurrence_id" in info.instructions
            if requests == 1:
                return ModelResponse(parts=[ToolCallPart("apply_edits", json.dumps(args), "rich")])
            result = next(part for part in messages[-1].parts if isinstance(part, ToolReturnPart))
            assert result.content["status"] == "accepted"
            assert result.content["render_status"] == "rendered"
            region = result.content["page"]["regions"][0]
            assert region["translation"] == "这是重要的事情。"
            inlines = region["translation_rich"]["blocks"][0]["inlines"]
            assert inlines[1]["style"]["bold"] is True
            assert inlines[1]["style"]["color"] == "#ff3300"
            return ModelResponse(parts=[TextPart("完成")])

        agent = create_agent(FunctionModel(model, profile=openai_model_profile("gpt-5")))
        result = await agent.run("修改局部样式", deps=ctx.deps)
        assert result.output == "完成" and requests == 2
        assert len(ctx.deps.renderer.snapshots) == 1
    asyncio.run(run())


@pytest.mark.parametrize("path,value", [
    pytest.param(("page", "id"), 0, id="invalid-page-id"),
    pytest.param(("command_id",), "", id="empty-command-id"),
    pytest.param(("edits", 0, "style", "font_size"), 0, id="invalid-region-size"),
    pytest.param(("edits", 0, "style", "font_color"), "red", id="invalid-color"),
    pytest.param(("edits", 0, "style", "unknown"), True, id="unknown-style-field"),
    pytest.param(("edits", 1, "document", "blocks", 0, "inlines", 0, "type"), "html", id="invalid-rich-node"),
    pytest.param(("edits", 1, "document", "blocks", 0, "inlines", 0, "style", "stroke", "width"), -1,
                 id="invalid-nested-stroke"),
    pytest.param(("edits", 1, "text"), "多余字段", id="wrong-operation-field"),
    pytest.param(("edits", 2, "geometry", "center"), [1, 2, 3], id="invalid-geometry"),
])
def test_compact_main_tool_still_validates_entire_batch_before_mutating(ctx, path, value):
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from manga_translator.agent.agents.chat import create_agent

    args = {
        "page": {"id": 1}, "command_id": "invalid",
        "edits": [
            edit().model_dump(exclude_unset=True),
            {"op": "replace_rich_text", "region_id": "r", "document": {
                "format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
                    {"type": "text", "text": "译文", "style": {"stroke": {"width": 0.1}}},
                ]}],
            }},
            {"op": "set_geometry", "region_id": "r", "geometry": {"center": [50, 50]}},
        ],
    }
    target = args
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    async def run():
        await read_page(ctx, {"id": 1})
        before = ctx.deps.workspace.page(ctx.deps, "p")
        requests = 0
        async def model(messages, info):
            nonlocal requests
            requests += 1
            return ModelResponse(parts=[ToolCallPart("apply_edits", json.dumps(args), f"invalid-{requests}")])
        agent = create_agent(FunctionModel(model))
        with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries count of 2"):
            await agent.run("修改样式", deps=ctx.deps)
        assert requests == 3
        assert ctx.deps.workspace.page(ctx.deps, "p") == before
        assert not ctx.deps.command_payloads
        assert not ctx.deps.transaction_results
        assert not ctx.deps.renderer.snapshots
    asyncio.run(run())


def test_loading_makes_all_regions_editable_without_scope_selection(ctx, monkeypatch, tmp_path):
    from manga_translator.agent.application import preview

    original = ctx.deps.workspace.page(ctx.deps, "p")
    def load(path, **kwargs):
        return {**copy.deepcopy(original), **kwargs, "project_status": "available",
                "original_asset": path, "base_asset": path}
    monkeypatch.setattr(preview, "load_project_page", load)

    async def run():
        session = preview.RenderPreviewSession()
        session.renderer = Renderer()
        try:
            loaded = await session.load(str(tmp_path / "page.png"))
            context = loaded["context"]
            result = await apply_edits(SimpleNamespace(deps=context), {"id": 1}, [edit(32)], "edit")
            assert result.return_value["render_status"] == "rendered"
            assert context.grant.region_ids == {}  # Whole loaded page, no selected-region prerequisite.
            second = await session.load(str(tmp_path / "next.png"))
            assert context.cancelled.is_set()
            assert second["context"].task_id != context.task_id
            assert not second["context"].cancelled.is_set()
        finally:
            await session.close()
        assert second["context"].cancelled.is_set()
    asyncio.run(run())


def test_main_backend_autonomously_edits_twice_streams_canvases_and_continues_next_turn(ctx, monkeypatch, caplog):
    from pydantic_ai.models import openai as model_module
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall
    from manga_translator.agent.application.service import ChatService
    from manga_translator.agent.domain.chat import ChatCanvas, ChatImage
    from manga_translator.agent.providers.openai import OpenAIResponsesBackend
    from manga_translator.agent.tools.builtin.shared import _remember
    from manga_translator.utils import system_proxy

    caplog.set_level(logging.INFO, logger="manga_translator.agent.providers.openai")
    _remember(ctx, ctx.deps.workspace.page(ctx.deps, "p"))
    requests = []
    async def stream_model(messages, info):
        requests.append(copy.deepcopy(messages))
        step = len(requests)
        assert [tool.name for tool in info.function_tools] == ["apply_edits"]
        assert '"id": 1' in info.function_tools[0].description
        assert "richtext.v1" in info.instructions and "fontSize" in info.instructions
        if step in (1, 2, 4):
            if step == 2:
                assert len(images_in(messages)) == 1
                assert images_in(messages)[0].data == png((30, 0, 0))
            size = {1: 30, 2: 35, 4: 40}[step]
            yield {0: DeltaThinkingPart(content=f"检查第{step}次", signature=f"signature-{step}")}
            yield {1: DeltaToolCall(name="apply_edits", tool_call_id=f"call-{step}", json_args=json.dumps({
                "page": {"id": 1}, "command_id": f"edit-{step}",
                "edits": [edit(size).model_dump(exclude_unset=True)],
            }))}
        else:
            assert step in (3, 5)
            assert len(images_in(messages)) == 1
            yield {0: DeltaThinkingPart(content=f"完成检查{step}", signature=f"signature-{step}")}
            yield "调整完成"

    class CompletedFunctionModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                response.finish_reason = "stop"
                yield response

    model = CompletedFunctionModel(stream_function=stream_model)
    monkeypatch.setattr(model_module, "OpenAIResponsesModel", lambda *args, **kwargs: model)
    monkeypatch.setattr(system_proxy, "openai_http_client_kwargs", lambda _: {})
    thinking = []
    debug = []
    backend = OpenAIResponsesBackend(api_key="offline-test", model="offline", on_thinking=thinking.append,
                                     on_debug_event=debug.append)
    service = ChatService(backend)

    async def run():
        first = [event async for event in service.stream(
            "调整排版", images=(ChatImage(png("red")),), tool_context=ctx.deps, session_id="page"
        )]
        canvases = [event for event in first if isinstance(event, ChatCanvas)]
        assert [item.image.data for item in canvases] == [png((30, 0, 0)), png((35, 0, 0))]
        assert all(item.context_id == ctx.deps.task_id for item in canvases)
        assert first[-1] == "调整完成"
        assert "检查第1次" in thinking[-1] and "检查第2次" in thinking[-1] and "完成检查3" in thinking[-1]
        second = [event async for event in service.stream("再增大一点", tool_context=ctx.deps, session_id="page")]
        assert any(isinstance(event, ChatCanvas) and event.image.data == png((40, 0, 0)) for event in second)
        assert len(requests) == 5
        assert any(isinstance(part, TextPart) and part.content == "调整完成"
                   for message in requests[3] for part in message.parts)
        assert ctx.deps.workspace.page(ctx.deps, "p")["regions"][0]["font_size"] == 40
    asyncio.run(run())
    assert caplog.text.count("Tool call: task=task tool=apply_edits") == 3
    assert caplog.text.count("Tool result: task=task tool=apply_edits") == 3
    assert "status=accepted render_status=rendered error=-" in caplog.text
    assert caplog.text.count("Agent turn completed: task=task") == 2
    assert "offline-test" not in caplog.text
    records = list({event["id"]: event for event in debug}.values())
    responses = [event["data"] for event in records if event["kind"] == "model_response"]
    assert len(responses) == 5 and all(response["state"] == "complete" for response in responses)
    assert responses[0]["parts"][0]["content"] == "检查第1次"
    assert responses[-1]["parts"][-1]["content"] == "调整完成"
    results = [event["data"] for event in records if event["kind"] == "tool_result"]
    # Model history cleanup must not erase an earlier diagnostic snapshot.
    assert [result["result"]["page"]["regions"][0]["font_size"] for result in results] == [30, 35, 40]
    starts = [event["data"] for event in records if event["kind"] == "turn_start"]
    assert len(starts) == 2 and starts[1]["message_history"]
    assert len([event for event in records if event["kind"] == "turn_complete"]) == 2


@pytest.mark.parametrize("invalid_attempts", [1, 2])
def test_main_backend_logs_field_errors_and_allows_correction_before_commit(ctx, monkeypatch, caplog, invalid_attempts):
    from pydantic_ai.messages import RetryPromptPart
    from pydantic_ai.models import openai as model_module
    from pydantic_ai.models.function import DeltaToolCall
    from manga_translator.agent.application.service import ChatService
    from manga_translator.agent.domain.chat import ChatCanvas
    from manga_translator.agent.providers.openai import OpenAIResponsesBackend
    from manga_translator.agent.tools.builtin.shared import _remember
    from manga_translator.utils import system_proxy

    caplog.set_level(logging.INFO, logger="manga_translator.agent.providers.openai")
    _remember(ctx, ctx.deps.workspace.page(ctx.deps, "p"))
    requests = 0

    async def stream_model(messages, info):
        nonlocal requests
        requests += 1
        if requests == invalid_attempts + 2:
            yield "调整完成"
            return
        if requests > 1:
            feedback = next(part for part in messages[-1].parts if isinstance(part, RetryPromptPart))
            errors = json.loads(feedback.content)["errors"]
            assert errors[0]["loc"][-1] == "font_size"
            assert errors[0]["type"] == "greater_than"
            assert errors[0]["actual_type"] == "integer"
            assert errors[0]["expected"]["exclusiveMinimum"] == 0
            assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 1
            assert not ctx.deps.renderer.snapshots
        yield {0: DeltaToolCall(name="apply_edits", tool_call_id=f"call-{requests}", json_args=json.dumps({
            "page": {"id": 1}, "command_id": "correct-style",
            "edits": [{"op": "set_region_style", "region_id": "r",
                       "style": {"font_size": 0 if requests <= invalid_attempts else 30}}],
        }))}

    class CompletedFunctionModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                response.finish_reason = "stop"
                yield response

    model = CompletedFunctionModel(stream_function=stream_model)
    monkeypatch.setattr(model_module, "OpenAIResponsesModel", lambda *args, **kwargs: model)
    monkeypatch.setattr(system_proxy, "openai_http_client_kwargs", lambda _: {})
    debug = []
    service = ChatService(OpenAIResponsesBackend(api_key="offline-test", model="offline", on_debug_event=debug.append))

    async def run():
        events = [event async for event in service.stream("调整字号", tool_context=ctx.deps)]
        assert events[-1] == "调整完成"
        assert sum(isinstance(event, ChatCanvas) for event in events) == 1
        assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 2
        assert len(ctx.deps.renderer.snapshots) == 1
    asyncio.run(run())
    assert requests == invalid_attempts + 2
    assert caplog.text.count("Tool arguments rejected:") == invalid_attempts
    assert "font_size: Input should be greater than 0" in caplog.text
    errors = [event["data"] for event in debug if event["kind"] == "validation_error"]
    assert len(errors) == invalid_attempts
    assert errors[0]["result"]["errors"][0]["loc"][-1] == "font_size"
    assert errors[0]["result"]["errors"][0]["input"] == 0


@pytest.mark.parametrize("invalid_attempts", [1, 2, 3])
def test_real_http_retry_feedback_for_json_string_page(ctx, monkeypatch, invalid_attempts):
    import httpx
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from manga_translator.agent.application.service import ChatService
    from manga_translator.agent.domain.chat import ChatCanvas
    from manga_translator.agent.providers.openai import OpenAIResponsesBackend
    from manga_translator.agent.tools.builtin.shared import _remember
    from manga_translator.utils import system_proxy

    _remember(ctx, ctx.deps.workspace.page(ctx.deps, "p"))
    requests, feedbacks, debug = [], [], []
    wrong_page = json.dumps({"id": 1})

    def response_stream(step, arguments=None):
        response = {"id": f"resp_{step}", "object": "response", "model": "glm-5.3-flash",
                    "created_at": 1, "status": "completed"}
        if arguments is not None:
            item = {"type": "function_call", "id": f"fc_{step}", "call_id": f"call_{step}",
                    "name": "apply_edits", "arguments": json.dumps(arguments), "status": "completed"}
            added = {**item, "arguments": "", "status": "in_progress"}
            delta = {"type": "response.function_call_arguments.delta", "item_id": item["id"],
                     "output_index": 0, "delta": item["arguments"]}
        else:
            item = {"type": "message", "id": f"msg_{step}", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "翻译完成", "annotations": []}]}
            added = {**item, "content": [], "status": "in_progress"}
            delta = {"type": "response.output_text.delta", "item_id": item["id"],
                     "output_index": 0, "content_index": 0, "delta": "翻译完成"}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": added},
            delta,
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {**response, "output": [item]}},
        ]
        body = "".join("event: " + event["type"] + "\ndata: "
                       + json.dumps({**event, "sequence_number": index}) + "\n\n"
                       for index, event in enumerate(events))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        step = len(requests)
        assert step <= invalid_attempts + 2
        if 1 < step <= invalid_attempts + 1:
            feedback = next(item["output"] for item in body["input"]
                            if item.get("type") == "function_call_output"
                            and item["call_id"] == f"call_{step - 1}")
            report, _ = json.JSONDecoder().raw_decode(feedback)
            details = report["errors"]
            assert {tuple(error["loc"]) for error in details} == {
                ("page", "PageById"), ("page", "PageByName"),
            }
            assert all(error["type"] == "model_type" and error["input"] == wrong_page for error in details)
            assert all(error["path"] == ["page"] and error["actual_type"] == "string" for error in details)
            assert all(error["expected"]["type"] == "object" for error in details)
            assert "再次 JSON 编码" in feedback and report["executed"] is False
            assert report["retry_example"]["page"] == {"id": 1}
            assert report["retry_example"]["command_id"] == "translate-chs-001"
            assert "Input should be an object" in feedback
            assert "Fix the errors and try again." in feedback
            assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 1
            assert not ctx.deps.transaction_results and not ctx.deps.renderer.snapshots
            feedbacks.append(feedback)
        if step <= invalid_attempts + 1:
            arguments = {
                "page": wrong_page,
                "command_id": "translate-chs-001",
                "edits": [{"op": "set_translation", "region_id": "r", "text": "午夜心动变奏曲"}],
            }
            if step > invalid_attempts:
                arguments = report["retry_example"]
            return response_stream(step, arguments)
        result = next(item["output"] for item in body["input"]
                      if item.get("type") == "function_call_output" and item["call_id"] == f"call_{step - 1}")
        assert json.loads(result)["status"] == "accepted"
        assert any(part.get("type") == "input_image" for item in body["input"]
                   if isinstance(item.get("content"), list) for part in item["content"])
        return response_stream(step)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(system_proxy, "openai_http_client_kwargs", lambda _: {"http_client": client})
        service = ChatService(OpenAIResponsesBackend(
            api_key="offline-test", model="glm-5.3-flash", base_url="https://offline.invalid/v1",
            on_debug_event=debug.append,
        ))
        if invalid_attempts == 3:
            with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries count of 2"):
                _ = [event async for event in service.stream("翻译成中文排版", tool_context=ctx.deps)]
            assert len(requests) == 3 and len(feedbacks) == 2
            assert ctx.deps.workspace.page(ctx.deps, "p")["revision"] == 1
            assert not ctx.deps.transaction_results and not ctx.deps.renderer.snapshots
            assert debug[-1]["kind"] == "error"
        else:
            events = [event async for event in service.stream("翻译成中文排版", tool_context=ctx.deps)]
            assert len(requests) == invalid_attempts + 2 and len(feedbacks) == invalid_attempts
            assert "".join(event for event in events if isinstance(event, str)) == "翻译完成"
            assert sum(isinstance(event, ChatCanvas) for event in events) == 1
            page = ctx.deps.workspace.page(ctx.deps, "p")
            assert page["revision"] == 2 and page["regions"][0]["translation"] == "午夜心动变奏曲"
            assert len(ctx.deps.transaction_results) == len(ctx.deps.renderer.snapshots) == 1
            assert debug[-1]["kind"] == "turn_complete"
        assert client.is_closed
    asyncio.run(run())
    print(f"invalid_calls={invalid_attempts}, requests={len(requests)}, "
          f"automatic_feedbacks={len(feedbacks)}, commits={len(ctx.deps.transaction_results)}")
    if invalid_attempts == 1:
        print("Actual feedback sent in the next HTTP request:\n" + feedbacks[0])


def main():
    return pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
