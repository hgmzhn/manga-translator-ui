import _bootstrap  # noqa: F401

import copy
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError
from pydantic_ai.tools import Tool

from manga_translator.agent.agents.chat import _compact_edit_schema
from manga_translator.agent.tools.builtin.page import apply_edits
from manga_translator.agent.tools.validation import build_validation_feedback


def feedback(arguments):
    tool = Tool(apply_edits)
    schema = _compact_edit_schema(tool.tool_def.parameters_json_schema)
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    with pytest.raises(ValidationError) as caught:
        tool.function_schema.validator.validate_json(raw)
    return build_validation_feedback(raw, caught.value, schema, tool.function_schema.validator)


def arguments(page=None):
    return {"page": page if page is not None else {"id": 1}, "command_id": "test-edit",
            "edits": [{"op": "set_translation", "region_id": "r", "text": '{"keep": "as text"}'}]}


@pytest.mark.parametrize("page", [{"id": 1}, {"folder": ".", "name": "page.png"}])
def test_retry_example_preserves_values_and_explains_both_page_branches(page):
    original = arguments(json.dumps(page))
    before = copy.deepcopy(original)
    report = feedback(original)
    assert original == before
    assert report["executed"] is False
    assert len(report["errors"]) == 2
    by_branch = {error["loc"][-1]: error for error in report["errors"]}
    assert by_branch["PageById"]["expected"]["required"] == ["id"]
    assert by_branch["PageByName"]["expected"]["required"] == ["folder", "name"]
    assert all(error["actual_type"] == "string" and error["path"] == ["page"] for error in report["errors"])
    assert all("再次 JSON 编码" in error["reason"] for error in report["errors"])
    expected = {**before, "page": page}
    assert report["retry_example"] == expected
    Tool(apply_edits).function_schema.validator.validate_python(report["retry_example"])


def test_all_errors_remain_visible_after_decoding_reveals_invalid_page_fields():
    payload = arguments(json.dumps({"id": 0, "unknown": True}))
    payload["edits"] = [{"op": "set_region_style", "region_id": "r",
                         "style": {"font_size": 0, "font_color": "red", "unknown": True}}]
    report = feedback(payload)
    original_paths = {tuple(error["path"]) for error in report["errors"]}
    assert {("page",), ("edits", 0, "style", "font_size"),
            ("edits", 0, "style", "font_color"), ("edits", 0, "style", "unknown")} <= original_paths
    remaining = report["errors_after_decoding"]
    assert any(error["path"] == ["page", "id"] and error["ctx"]["gt"] == 0 for error in remaining)
    assert any(error["path"] == ["page", "unknown"] and error["type"] == "extra_forbidden" for error in remaining)
    assert any(error["path"] == ["page", "folder"] and error["actual_type"] == "missing" for error in remaining)
    size_error = next(error for error in remaining if error["path"] == ["edits", 0, "style", "font_size"])
    assert size_error["expected"]["exclusiveMinimum"] == 0
    assert "retry_example" not in report


@pytest.mark.parametrize("width", [0.1, -1])
def test_nested_encoded_rich_text_is_fully_validated_before_offering_retry_example(width):
    document = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [
        {"type": "text", "text": '{"keep": "as text"}', "style": {"stroke": json.dumps({"width": width})}},
    ]}]}
    payload = arguments()
    payload["edits"] = [{"op": "replace_rich_text", "region_id": "r", "document": json.dumps(document)}]
    report = feedback(payload)
    assert report["errors"][0]["path"] == ["edits", 0, "document"]
    if width < 0:
        assert "retry_example" not in report
        errors = report["errors_after_decoding"]
        assert len(errors) == 1
        assert errors[0]["path"] == ["edits", 0, "document", "blocks", 0, "inlines", 0, "style", "stroke", "width"]
        assert errors[0]["expected"]["minimum"] == 0
    else:
        sample = report["retry_example"]
        node = sample["edits"][0]["document"]["blocks"][0]["inlines"][0]
        assert node["style"]["stroke"] == {"width": width}
        assert node["text"] == '{"keep": "as text"}'
        Tool(apply_edits).function_schema.validator.validate_python(sample)


def test_malformed_json_keeps_parser_details_without_claiming_a_valid_example():
    report = feedback('{"page":')
    assert report["errors"][0]["type"] == "json_invalid"
    assert report["errors"][0]["ctx"]["error"]
    assert report["errors"][0]["json_parse_error"]
    assert "retry_example" not in report


def test_strict_integer_and_missing_edit_payload_report_actual_type_and_required_field():
    payload = arguments({"id": "1"})
    del payload["edits"][0]["text"]
    report = feedback(payload)
    error = next(error for error in report["errors"] if error["path"] == ["page", "id"])
    assert error["actual_type"] == "string" and error["expected"]["type"] == "integer"
    error = next(error for error in report["errors"] if error["path"] == ["edits", 0, "text"])
    assert error["actual_type"] == "missing" and error["type"] == "missing"
    assert "retry_example" not in report


def main():
    return pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
