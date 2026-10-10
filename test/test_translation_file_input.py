"""Offline protocol regressions; never contacts a model or reads API credentials."""

import ast
import asyncio
import base64
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_file_input_checks"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "manga_translator/translators")]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    PACKAGE + ".openai_file_input", ROOT / "manga_translator/translators/openai_file_input.py"
)
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
gemini_spec = importlib.util.spec_from_file_location(
    PACKAGE + ".gemini_file_input", ROOT / "manga_translator/translators/gemini_file_input.py"
)
gemini_adapter = importlib.util.module_from_spec(gemini_spec)
sys.modules[gemini_spec.name] = gemini_adapter
gemini_spec.loader.exec_module(gemini_adapter)

# Execute the real prompt and transport methods in isolation from GPU/OCR imports.
tree = ast.parse((ROOT / "manga_translator/translators/common.py").read_text(encoding="utf-8"))
common = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CommonTranslator")
names = {"_build_unified_user_prompt", "_build_user_prompt_for_texts", "_build_user_prompt_for_hq", "_create_translation_request"}
methods = [node for node in common.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
response_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_OpenAIResponse")
namespace = {
    "__package__": PACKAGE, "List": list, "Dict": dict,
    "system_proxy_request_kwargs": lambda url: {},
    "_extract_http_error_details": lambda response: "endpoint unavailable",
}
exec(compile(ast.Module(body=[response_class, *methods], type_ignores=[]), "common.py", "exec"), namespace)


class Harness:
    _build_unified_user_prompt = namespace["_build_unified_user_prompt"]
    _build_user_prompt_for_texts = namespace["_build_user_prompt_for_texts"]
    _build_user_prompt_for_hq = namespace["_build_user_prompt_for_hq"]
    _create_translation_request = namespace["_create_translation_request"]
    logger = logging.getLogger("file-input-check")
    _get_retry_hint = lambda self, attempt, reason: f"Retry {attempt}: {reason}"


def completed(text):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}
    ]}], "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}}


class FileInputTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.batch = [
            {"original_texts": ['こんにちは\n世界', '"引用" \\ 😀'], "text_order": [1, 2]},
            {"original_texts": ["第二页"], "text_order": [3]},
        ]

    def test_unicode_originals_only_in_attachment_and_hq_mapping(self):
        ctx = SimpleNamespace(config=SimpleNamespace(render=SimpleNamespace(disable_auto_wrap=True)))
        content = self.harness._build_unified_user_prompt(self.batch, ctx, file_input=True)
        self.assertNotIn("こんにちは", content[0]["text"])
        self.assertNotIn("第二页", content[0]["text"])
        data = content[1]["file"]["file_data"].split(",", 1)[1]
        items = json.loads(base64.b64decode(data).decode("utf-8"))
        self.assertEqual(items[0], {"id": 1, "text": "こんにちは 世界", "image_index": 1, "original_region_count": 2})
        self.assertEqual(items[1]["text"], '"引用" \\ 😀')
        self.assertEqual(items[2]["image_index"], 2)
        self.assertEqual(items[2]["id"], 3)

    def test_legacy_prompt_and_retry(self):
        legacy = self.harness._build_unified_user_prompt(self.batch)
        self.assertIsInstance(legacy, str)
        self.assertIn("All texts to translate (JSON Array)", legacy)
        parts = self.harness._build_unified_user_prompt(self.batch, file_input=True, retry_attempt=2, retry_reason="count mismatch")
        self.assertTrue(parts[0]["text"].startswith("Retry 2: count mismatch"))

    def test_history_images_and_parameters(self):
        messages = [
            {"role": "system", "content": "Translate into Chinese"},
            {"role": "user", "content": "historical original"},
            {"role": "assistant", "content": "historical translation"},
            {"role": "user", "content": adapter.build_file_content([{ "id": 1, "text": "source"}]) + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "low"}}
            ]},
        ]
        request = adapter.build_responses_request({"model": "test-model", "messages": messages, "max_tokens": 100, "reasoning_effort": "low"})
        self.assertNotIn("messages", request)
        self.assertEqual(request["input"][2], messages[2])
        self.assertEqual(request["input"][-1]["content"][-1], {"type": "input_image", "image_url": "data:image/png;base64,abc", "detail": "low"})
        self.assertEqual(request["max_output_tokens"], 100)
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertFalse(request["store"])

    def test_truncated_refusal_empty_responses_rejected(self):
        for result in [{"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, completed(""),
                       {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]:
            with self.assertRaises(RuntimeError):
                adapter.normalize_response(result)

    def test_mock_http_and_existing_json_response_shape(self):
        captured = {}
        text = '{"translations":[{"id":1,"translation":"你好"}]}'
        async def post(url, **kwargs):
            captured.update(url=url, **kwargs)
            return SimpleNamespace(status_code=200, json=lambda: completed(text))
        self.harness.client = SimpleNamespace(base_url="https://mock.invalid/v1", default_headers={}, api_key="", timeout=10, session=SimpleNamespace(post=post))
        self.harness._translation_file_input = True
        content = self.harness._build_unified_user_prompt(self.batch, file_input=True)
        response = asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": [{"role": "user", "content": content}]}))
        self.assertEqual(captured["url"], "https://mock.invalid/v1/responses")
        self.assertEqual(captured["json"]["input"][0]["content"][1]["type"], "input_file")
        self.assertEqual(response.choices[0].message.content, text)
        self.assertEqual(response.usage.total_tokens, 8)

    def test_text_mode_uses_existing_chat_endpoint(self):
        async def create(**kwargs):
            return kwargs
        self.harness.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": []}))
        self.assertEqual(result, {"model": "mock", "messages": []})

    def test_incompatible_endpoint_fails_explicitly(self):
        async def post(*args, **kwargs):
            return SimpleNamespace(status_code=404)
        self.harness._translation_file_input = True
        self.harness.client = SimpleNamespace(base_url="https://mock.invalid/v1", default_headers={}, api_key="", timeout=10, session=SimpleNamespace(post=post))
        with self.assertRaisesRegex(RuntimeError, "TXT input support; HTTP 404"):
            asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": []}))

    def test_gemini_native_file_and_image_coexist(self):
        content = self.harness._build_unified_user_prompt(self.batch, file_input=True)
        parts = gemini_adapter.to_gemini_parts(content)
        image = {"inlineData": {"mimeType": "image/png", "data": "image-data"}}
        parts.append(image)
        self.assertNotIn("こんにちは", parts[0]["text"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "text/plain")
        items = json.loads(base64.b64decode(parts[1]["inlineData"]["data"]).decode("utf-8"))
        self.assertEqual([item["id"] for item in items], [1, 2, 3])
        self.assertEqual(items[-1]["image_index"], 2)
        self.assertEqual(parts[-1], image)
        self.assertEqual(gemini_adapter.to_gemini_parts("legacy prompt"), [{"text": "legacy prompt"}])

    def test_all_prompt_builders_preserve_text_mode_and_use_attachment_setting(self):
        for filename, class_name, source, image_mode in [
            ("openai.py", "OpenAITranslator", ["原文😀"], False),
            ("openai_hq.py", "OpenAIHighQualityTranslator", self.batch, True),
            ("gemini.py", "GeminiTranslator", ["原文😀"], False),
            ("gemini_hq.py", "GeminiHighQualityTranslator", self.batch, True),
        ]:
            tree = ast.parse((ROOT / "manga_translator/translators" / filename).read_text(encoding="utf-8"))
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
            method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_user_prompt")
            ns = {"List": list, "Dict": dict, "Any": object}
            exec(compile(ast.Module(body=[method], type_ignores=[]), filename, "exec"), ns)
            self.harness._translation_file_input = True
            content = ns["_build_user_prompt"](self.harness, source, None)
            self.assertEqual(gemini_adapter.to_gemini_parts(content)[1]["inlineData"]["mimeType"], "text/plain")
            legacy_builder = self.harness._build_user_prompt_for_hq if image_mode else self.harness._build_user_prompt_for_texts
            for ctx in [None, SimpleNamespace(config=SimpleNamespace(render=SimpleNamespace(disable_auto_wrap=True)))]:
                for attempt in [0, 2]:
                    with self.subTest(translator=filename, retry=attempt, ai_break=ctx is not None):
                        self.harness._translation_file_input = False
                        actual = ns["_build_user_prompt"](self.harness, source, ctx, retry_attempt=attempt, retry_reason="count mismatch")
                        expected = legacy_builder(source, ctx, "", retry_attempt=attempt, retry_reason="count mismatch")
                        self.assertEqual(actual, expected)

    def test_gemini_transport_preserves_native_attachment_on_wire(self):
        client_cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AsyncGeminiCurlCffi")
        models_cls = next(node for node in client_cls.body if isinstance(node, ast.ClassDef) and node.name == "Models")
        method = next(node for node in models_cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_content")
        ns = {"json": json, "system_proxy_request_kwargs": lambda url: {}, "_GeminiResponse": lambda data: data}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "common.py", "exec"), ns)
        captured = {}
        expected = {"candidates": [{"content": {"parts": [{"text": "translation JSON"}]}}]}
        async def post(url, **kwargs):
            captured.update(url=url, **kwargs)
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: expected)
        model = SimpleNamespace(parent=SimpleNamespace(base_url="https://mock.invalid", api_key="test-key", default_headers={}, timeout=10, session=SimpleNamespace(post=post)))
        content = self.harness._build_unified_user_prompt(self.batch, file_input=True)
        parts = gemini_adapter.to_gemini_parts(content)
        image = {"inlineData": {"mimeType": "image/png", "data": "abc"}}
        parts.append(image)
        result = asyncio.run(ns["generate_content"](model, "mock", [{"role": "user", "parts": parts}]))
        self.assertEqual(captured["json"]["contents"][0]["parts"], parts)
        self.assertEqual(result, expected)
        self.assertTrue(captured["url"].endswith(":generateContent"))


if __name__ == "__main__":
    unittest.main()
