"""Actionable validation feedback without executing or silently fixing tool calls."""

from copy import deepcopy
import json

from pydantic import ValidationError
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelRetry

from ..prompts import load_prompt


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    return "number"


def _locate(schema, location, root, path=()):
    """Resolve Pydantic union labels to the actual JSON path and field schema."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if location and location[0] == name:
            location = location[1:]
        return _locate(root["$defs"][name], location, root, path)
    if not location:
        return path, schema
    key, *remaining = location
    if key in schema.get("properties", {}):
        return _locate(schema["properties"][key], remaining, root, (*path, key))
    if isinstance(key, int) and schema.get("type") == "array":
        prefix = schema.get("prefixItems", [])
        child = prefix[key] if 0 <= key < len(prefix) else schema.get("items", {})
        if isinstance(child, dict):
            return _locate(child, remaining, root, (*path, key))
    branches = []
    for branch in schema.get("anyOf", schema.get("oneOf", [])):
        resolved = root["$defs"][branch["$ref"].rsplit("/", 1)[-1]] if "$ref" in branch else branch
        tags = [prop["const"] for prop in resolved.get("properties", {}).values() if "const" in prop]
        tags.append({"boolean": "bool", "number": "float", "integer": "int"}.get(resolved.get("type")))
        if "$ref" in branch:
            tags.append(branch["$ref"].rsplit("/", 1)[-1])
        branches.append((branch, tags))
    has_label = any(key in tags for _, tags in branches)
    for branch, tags in branches:
        if has_label and key not in tags:
            continue
        found = _locate(branch, remaining if key in tags else location, root, path)
        if found is not None:
            return found
    if schema.get("type") == "object" and not remaining:
        return (*path, key), {"additionalProperties": schema.get("additionalProperties", True),
                              "allowed_fields": list(schema.get("properties", {}))}
    return None


def _diagnose(error, schema):
    result = []
    for detail in error.errors(include_url=False):
        found = _locate(schema, detail["loc"], schema)
        path, expected = found if found is not None else (detail["loc"], {})
        value = detail.get("input")
        actual = "missing" if detail["type"] == "missing" else _json_type(value)
        reason = detail["msg"]
        entry = {**detail, "path": list(path), "actual_type": actual, "expected": expected}
        if detail["type"] == "missing":
            reason += "；该必填字段未提供。"
        elif detail["type"] == "extra_forbidden":
            reason += "；此位置不接受该字段，请按 allowed_fields 修改。"
        elif actual == "string" and expected.get("type") in {"object", "array"}:
            reason += f"；此字段要求 {expected['type']}，实际传入 string。"
            try:
                decoded = json.loads(value)
            except ValueError as parse_error:
                entry["json_parse_error"] = str(parse_error)
            else:
                if _json_type(decoded) == expected["type"]:
                    reason += " 字符串内是被再次 JSON 编码的数据；应直接传入该结构，不要加外层引号或转义。"
                    entry["decoded_value"] = decoded
        entry["reason"] = reason
        result.append(entry)
    return result


def build_validation_feedback(args, error, schema, validator):
    """Keep every error and validate a copy before offering a complete retry example."""
    report = {"status": "validation_error", "executed": False, "errors": _diagnose(error, schema)}
    try:
        candidate = json.loads(args) if isinstance(args, str) else deepcopy(args)
    except ValueError:
        return report
    diagnostics = report["errors"]
    # Only unwrap fields that validation specifically requires to be objects/arrays.
    # In particular, translation text containing JSON must remain text.
    while True:
        changed = False
        for detail in diagnostics:
            if "decoded_value" not in detail:
                continue
            path = detail["path"]
            if not path:
                candidate = deepcopy(detail["decoded_value"])
                changed = True
                continue
            parent = candidate
            try:
                for key in path[:-1]:
                    parent = parent[key]
                if isinstance(parent[path[-1]], str):
                    parent[path[-1]] = deepcopy(detail["decoded_value"])
                    changed = True
            except (KeyError, IndexError, TypeError):
                continue
        if not changed:
            break
        try:
            validator.validate_python(candidate)
        except ValidationError as remaining:
            diagnostics = _diagnose(remaining, schema)
            report["errors_after_decoding"] = diagnostics
        else:
            report.pop("errors_after_decoding", None)
            report["retry_example"] = candidate
            report["retry_example_validation"] = "已通过完整参数校验；尚未执行，也未检查工作区权限或冲突。"
            break
    return report


class EditValidationFeedback(AbstractCapability):
    def __init__(self, tools):
        self.validators = {tool.name: tool.function_schema.validator for tool in tools}

    async def on_tool_validate_error(self, ctx, *, call, tool_def, args, error):
        if call.tool_name not in self.validators or not isinstance(error, ValidationError):
            raise error
        report = build_validation_feedback(args, error, tool_def.parameters_json_schema,
                                           self.validators[call.tool_name])
        report["tool"] = call.tool_name
        report["action"] = load_prompt("validation")
        raise ModelRetry(json.dumps(report, ensure_ascii=False, default=str)) from error
