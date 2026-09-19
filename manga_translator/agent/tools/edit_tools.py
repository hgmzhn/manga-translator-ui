"""Shared edit schema preparation for chat and delegated page agents."""

import json
from copy import deepcopy
from dataclasses import replace

from pydantic_ai.tools import Tool

from .builtin.edits import create_regions, delete_regions, edit_regions, edit_rich_text


def compact_edit_schema(schema):
    """Omit redundant metadata and nullable patch branches from the model schema.

    Execution continues to use the original typed validator.
    """
    result = {key: deepcopy(value) for key, value in schema.items()
              if key not in {"title", "default", "discriminator"}}
    for key in ("properties", "$defs"):
        if key in result:
            result[key] = {name: compact_edit_schema(value) for name, value in result[key].items()}
    for key in ("items", "additionalProperties"):
        if isinstance(result.get(key), dict):
            result[key] = compact_edit_schema(result[key])
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if key in result:
            result[key] = [compact_edit_schema(value) for value in result[key]]
    if schema.get("default", False) is None and "anyOf" in result:
        choices = [value for value in result["anyOf"] if value != {"type": "null"}]
        if len(choices) == 1:
            result.pop("anyOf")
            result = {**choices[0], **result}
        else:
            result["anyOf"] = choices
    for name in ("op", "type"):
        if "const" in result.get("properties", {}).get(name, {}):
            required = result.setdefault("required", [])
            if name not in required:
                required.append(name)
    return result


async def prepare_edit(ctx, definition):
    pages = ctx.deps.grant.layout_pages | ctx.deps.grant.translation_pages | ctx.deps.grant.geometry_pages
    identities = [ctx.deps.workspace.public_identity(pid) for pid in sorted(pages)]
    location = ("可编辑页面（page_id 取 id）：" + json.dumps(identities, ensure_ascii=False)
                if identities else "当前尚未加载页面，请先在渲染界面加载图片。")
    return replace(
        definition, description=(definition.description or "") + "\n" + location,
        parameters_json_schema=compact_edit_schema(definition.parameters_json_schema), strict=False,
    )


def create_edit_tools():
    return [
        Tool(edit_regions, sequential=True, prepare=prepare_edit, max_retries=2),
        Tool(create_regions, sequential=True, prepare=prepare_edit, max_retries=2),
        Tool(delete_regions, sequential=True, prepare=prepare_edit, max_retries=2),
        Tool(edit_rich_text, sequential=True, prepare=prepare_edit, max_retries=2, defer_loading=True),
    ]
