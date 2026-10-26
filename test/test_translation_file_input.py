"""Offline protocol regressions; never contacts a model or reads API credentials."""

import ast
import asyncio
import base64
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import MethodType, ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_file_input_checks"
_MISSING = object()
_TEST_MODULE_NAMES = (PACKAGE, PACKAGE + ".openai_file_input", PACKAGE + ".gemini_file_input")
_HARNESS_METHODS = ("_build_unified_user_prompt", "_build_user_prompt_for_texts", "_build_user_prompt_for_hq", "_create_translation_request")


def _restore_test_modules(previous):
    """Restore only the private module entries owned by this test environment."""
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Harness:
    """Run the real prompt and transport methods without loading GPU dependencies."""

    logger = logging.getLogger("file-input-check")
    _get_retry_hint = lambda self, attempt, reason: f"Retry {attempt}: {reason}"

    def __init__(self, namespace):
        """Bind extracted methods to this instance without changing shared classes."""
        for name in _HARNESS_METHODS:
            setattr(self, name, MethodType(namespace[name], self))


def completed(text):
    """Build a completed Responses payload with deterministic token accounting."""
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}
    ]}], "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}}


class FileInputTests(unittest.TestCase):
    """Cover attachment protocols, compatibility, and failure diagnostics offline."""

    def setUp(self):
        """Create independent translator state and a batch with Unicode source text."""
        previous = {name: sys.modules.get(name, _MISSING) for name in _TEST_MODULE_NAMES}
        self.addCleanup(_restore_test_modules, previous)
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / "manga_translator/translators")]
        sys.modules[PACKAGE] = package

        def load_module(name, relative_path, register=True):
            """Load a source module only while this test's cleanup is registered."""
            spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
            module = importlib.util.module_from_spec(spec)
            if register:
                sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        self.adapter = load_module(PACKAGE + ".openai_file_input", "manga_translator/translators/openai_file_input.py")
        self.gemini_adapter = load_module(PACKAGE + ".gemini_file_input", "manga_translator/translators/gemini_file_input.py")
        retry_module = load_module(PACKAGE + ".retry", "manga_translator/utils/retry.py", register=False)

        # Execute real methods without importing the GPU/OCR package hierarchy.
        self.common_tree = ast.parse((ROOT / "manga_translator/translators/common.py").read_text(encoding="utf-8"))
        common = next(node for node in self.common_tree.body if isinstance(node, ast.ClassDef) and node.name == "CommonTranslator")
        methods = [node for node in common.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _HARNESS_METHODS]
        response_class = next(node for node in self.common_tree.body if isinstance(node, ast.ClassDef) and node.name == "_OpenAIResponse")
        diagnostics = next(node for node in self.common_tree.body if isinstance(node, ast.FunctionDef) and node.name == "_response_diagnostics")
        self.namespace = {
            "__package__": PACKAGE, "List": list, "Dict": dict,
            "system_proxy_request_kwargs": lambda url: {},
            "_extract_http_error_details": lambda response: "endpoint unavailable",
            "summarize_response_text": retry_module.summarize_response_text,
        }
        exec(compile(ast.Module(body=[response_class, diagnostics, *methods], type_ignores=[]), "common.py", "exec"), self.namespace)
        self.harness = Harness(self.namespace)
        self.batch = [
            {"original_texts": ['こんにちは\n世界', '"引用" \\ 😀'], "text_order": [1, 2]},
            {"original_texts": ["第二页"], "text_order": [3]},
        ]

    def test_unicode_originals_only_in_attachment_and_hq_mapping(self):
        """Keep cleaned Unicode sources in the file and preserve HQ region metadata."""
        ctx = SimpleNamespace(config=SimpleNamespace(render=SimpleNamespace(disable_auto_wrap=True)))
        content = self.harness._build_unified_user_prompt(self.batch, ctx, file_input=True, image_index_map={0: 1, 1: 2})
        self.assertNotIn("こんにちは", content[0]["text"])
        self.assertNotIn("第二页", content[0]["text"])
        data = content[1]["file"]["file_data"].split(",", 1)[1]
        items = json.loads(base64.b64decode(data).decode("utf-8"))
        self.assertEqual(items[0], {"id": 1, "text": "こんにちは 世界", "image_index": 1, "original_region_count": 2})
        self.assertEqual(items[1]["text"], '"引用" \\ 😀')
        self.assertEqual(items[2]["image_index"], 2)
        self.assertEqual(items[2]["id"], 3)

    def test_legacy_prompt_and_retry(self):
        """Leave text mode unchanged and keep retry instructions outside the file."""
        legacy = self.harness._build_unified_user_prompt(self.batch)
        self.assertIsInstance(legacy, str)
        self.assertIn("All texts to translate (JSON Array)", legacy)
        parts = self.harness._build_unified_user_prompt(self.batch, file_input=True, retry_attempt=2, retry_reason="count mismatch")
        self.assertTrue(parts[0]["text"].startswith("Retry 2: count mismatch"))

    def test_history_images_and_parameters(self):
        """Retain history and image content while adapting Responses parameters."""
        messages = [
            {"role": "system", "content": "Translate into Chinese"},
            {"role": "user", "content": "historical original"},
            {"role": "assistant", "content": "historical translation"},
            {"role": "user", "content": self.adapter.build_file_content([{ "id": 1, "text": "source"}]) + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "low"}}
            ]},
        ]
        request = self.adapter.build_responses_request({"model": "test-model", "messages": messages, "max_tokens": 100, "reasoning_effort": "low"})
        self.assertNotIn("messages", request)
        self.assertEqual(request["input"][2], messages[2])
        self.assertEqual(request["input"][-1]["content"][-1], {"type": "input_image", "image_url": "data:image/png;base64,abc", "detail": "low"})
        self.assertEqual(request["max_output_tokens"], 100)
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertFalse(request["store"])

    def test_truncated_refusal_empty_responses_rejected(self):
        """Reject incomplete, refused, or empty output before translation parsing."""
        for result in [{"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, completed(""),
                       {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}]:
            with self.assertRaises(RuntimeError):
                self.adapter.normalize_response(result)

    def test_mock_http_and_existing_json_response_shape(self):
        """Route file input to Responses and retain the existing parsed result shape."""
        captured = {}
        text = '{"translations":[{"id":1,"translation":"你好"}]}'
        async def post(url, **kwargs):
            """Capture one request and return a completed mock response."""
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
        """Keep ordinary text requests on the original Chat Completions client."""
        async def create(**kwargs):
            """Return the received Chat arguments without accessing a network."""
            return kwargs
        self.harness.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": []}))
        self.assertEqual(result, {"model": "mock", "messages": []})

    def test_incompatible_endpoint_fails_explicitly(self):
        """Report unsupported Responses endpoints without falling back to text input."""
        async def post(*args, **kwargs):
            """Simulate a service that has no Responses endpoint."""
            return SimpleNamespace(status_code=404)
        self.harness._translation_file_input = True
        self.harness.client = SimpleNamespace(base_url="https://mock.invalid/v1", default_headers={}, api_key="", timeout=10, session=SimpleNamespace(post=post))
        with self.assertRaisesRegex(RuntimeError, "TXT input support; HTTP 404"):
            asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": []}))

    def test_gemini_native_file_and_image_coexist(self):
        """Combine native Gemini text attachments with independent image parts."""
        content = self.harness._build_unified_user_prompt(self.batch, file_input=True, image_index_map={0: 1, 1: 2})
        parts = self.gemini_adapter.to_gemini_parts(content)
        image = {"inlineData": {"mimeType": "image/png", "data": "image-data"}}
        parts.append(image)
        self.assertNotIn("こんにちは", parts[0]["text"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "text/plain")
        items = json.loads(base64.b64decode(parts[1]["inlineData"]["data"]).decode("utf-8"))
        self.assertEqual([item["id"] for item in items], [1, 2, 3])
        self.assertEqual(items[-1]["image_index"], 2)
        self.assertEqual(parts[-1], image)
        self.assertEqual(self.gemini_adapter.to_gemini_parts("legacy prompt"), [{"text": "legacy prompt"}])

    def test_all_prompt_builders_preserve_text_mode_and_use_attachment_setting(self):
        """Compare all four disabled prompt paths with their original text builders."""
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
            self.assertEqual(self.gemini_adapter.to_gemini_parts(content)[1]["inlineData"]["mimeType"], "text/plain")
            legacy_builder = self.harness._build_user_prompt_for_hq if image_mode else self.harness._build_user_prompt_for_texts
            for ctx in [None, SimpleNamespace(config=SimpleNamespace(render=SimpleNamespace(disable_auto_wrap=True)))]:
                for attempt in [0, 2]:
                    with self.subTest(translator=filename, retry=attempt, ai_break=ctx is not None):
                        self.harness._translation_file_input = False
                        actual = ns["_build_user_prompt"](self.harness, source, ctx, retry_attempt=attempt, retry_reason="count mismatch")
                        expected = legacy_builder(source, ctx, "", retry_attempt=attempt, retry_reason="count mismatch")
                        self.assertEqual(actual, expected)

    def test_gemini_transport_preserves_native_attachment_on_wire(self):
        """Capture the actual Gemini transport's JSON body without making API calls."""
        client_cls = next(node for node in self.common_tree.body if isinstance(node, ast.ClassDef) and node.name == "AsyncGeminiCurlCffi")
        models_cls = next(node for node in client_cls.body if isinstance(node, ast.ClassDef) and node.name == "Models")
        method = next(node for node in models_cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_content")
        ns = {"json": json, "system_proxy_request_kwargs": lambda url: {}, "_GeminiResponse": lambda data: data}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "common.py", "exec"), ns)
        captured = {}
        expected = {"candidates": [{"content": {"parts": [{"text": "translation JSON"}]}}]}
        async def post(url, **kwargs):
            """Record the native Gemini payload and return a mock candidate."""
            captured.update(url=url, **kwargs)
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: expected)
        model = SimpleNamespace(parent=SimpleNamespace(base_url="https://mock.invalid", api_key="test-key", default_headers={}, timeout=10, session=SimpleNamespace(post=post)))
        content = self.harness._build_unified_user_prompt(self.batch, file_input=True)
        parts = self.gemini_adapter.to_gemini_parts(content)
        image = {"inlineData": {"mimeType": "image/png", "data": "abc"}}
        parts.append(image)
        result = asyncio.run(ns["generate_content"](model, "mock", [{"role": "user", "parts": parts}]))
        self.assertEqual(captured["json"]["contents"][0]["parts"], parts)
        self.assertEqual(result, expected)
        self.assertTrue(captured["url"].endswith(":generateContent"))

    def test_failed_responses_preserve_service_error_details(self):
        """Keep service codes/messages and retain incomplete/unknown fallbacks."""
        cases = [
            ({"status": "failed", "error": {"code": "quota_exceeded", "message": "Quota exhausted"}}, "quota_exceeded: Quota exhausted"),
            ({"status": "failed", "error": "upstream unavailable"}, "upstream unavailable"),
            ({"status": "failed", "error": {"code": "server_error"}}, "server_error"),
            ({"status": "incomplete", "error": {}, "incomplete_details": {"reason": "max_output_tokens"}}, "max_output_tokens"),
            ({"status": "failed", "error": {}}, "unknown"),
        ]
        for response, reason in cases:
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, reason):
                self.adapter.normalize_response(response)

    def test_non_json_responses_include_bounded_http_diagnostics(self):
        """Include MIME type and bounded response text while chaining parse errors."""
        for mime, body in [("text/html", "<html>unsupported endpoint</html>"), ("text/event-stream", "data: unexpected stream")]:
            parse_error = ValueError("not valid JSON")

            def invalid_json():
                """Simulate the real client's response.json() parse failure."""
                raise parse_error

            async def post(*args, **kwargs):
                """Return HTTP 200 with a non-JSON body from a compatible service."""
                return SimpleNamespace(status_code=200, headers={"content-type": mime}, text=body + "x" * 3000, json=invalid_json)

            self.harness._translation_file_input = True
            self.harness.client = SimpleNamespace(base_url="https://mock.invalid/v1", default_headers={}, api_key="", timeout=10, session=SimpleNamespace(post=post))
            with self.subTest(mime=mime), self.assertRaises(RuntimeError) as caught:
                asyncio.run(self.harness._create_translation_request({"model": "mock", "messages": []}))
            self.assertIn("HTTP 200", str(caught.exception))
            self.assertIn(mime, str(caught.exception))
            self.assertIn(body, str(caught.exception))
            self.assertLess(len(str(caught.exception)), 1500)
            self.assertIs(caught.exception.__cause__, parse_error)

    def test_hq_image_preparation_maps_only_successful_uploads_and_clears_fallback(self):
        """Exercise both real HQ preparation loops and the image-free retry call."""
        class PromptCaptured(Exception):
            """Stop translation after building the prompt, before any API request."""

        def encode_gemini(image):
            """Keep usable images and simulate an unsupported image encoding."""
            if image == "broken":
                raise ValueError("cannot encode image")
            return b"encoded", "image/png"

        def native_part(**kwargs):
            """Supply the Gemini Part serialization interface without loading its SDK."""
            return SimpleNamespace(model_dump=lambda **options: {"inlineData": {"mimeType": kwargs["mime_type"], "data": "encoded"}})

        for filename, class_name in [("openai_hq.py", "OpenAIHighQualityTranslator"), ("gemini_hq.py", "GeminiHighQualityTranslator")]:
            source_tree = ast.parse((ROOT / "manga_translator/translators" / filename).read_text(encoding="utf-8"))
            cls = next(node for node in source_tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
            selected = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"_build_user_prompt", "_translate_batch_high_quality"}]
            ns = {"List": list, "Dict": dict, "Any": object, "encode_image_for_openai": lambda image: "encoded", "encode_image_for_gemini": encode_gemini, "types": SimpleNamespace(Part=SimpleNamespace(from_bytes=native_part))}
            exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), ns)
            method = next(node for node in selected if node.name == "_translate_batch_high_quality")
            prompt_call = next(node for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_build_user_prompt")

            for images in [[None, "broken", "usable"], [None, None, None]]:
                batch = [{"image": image, "original_texts": [f"page {index + 1}"], "text_order": [index + 1]} for index, image in enumerate(images)]
                harness = Harness(self.namespace)
                harness.client = object()
                harness._translation_file_input = True
                harness._resolve_max_total_attempts = lambda: 1
                harness._check_cancelled = lambda: None
                harness._increment_global_attempt = lambda: True
                harness._get_system_prompt = harness._get_system_instruction = lambda *args, **kwargs: "system"
                harness._log_provider_name = lambda: "Gemini"

                def capture_prompt(batch_data, ctx, **kwargs):
                    """Capture the production prompt after its actual images are prepared."""
                    raise PromptCaptured(ns["_build_user_prompt"](harness, batch_data, ctx, **kwargs))

                harness._build_user_prompt = capture_prompt
                with self.subTest(translator=filename, images=images), self.assertRaises(PromptCaptured) as caught:
                    asyncio.run(ns["_translate_batch_high_quality"](harness, ["a", "b", "c"], batch, "JPN", "CHS"))
                content = caught.exception.args[0]
                items = json.loads(base64.b64decode(content[1]["file"]["file_data"].split(",", 1)[1]))
                self.assertEqual([item["id"] for item in items], [1, 2, 3])
                expected = {} if images[2] is None else ({2: 1} if filename == "gemini_hq.py" else {1: 1, 2: 2})
                self.assertEqual({index: item["image_index"] for index, item in enumerate(items) if "image_index" in item}, expected)
                self.assertIn("Entries without image_index have no attached image", content[0]["text"])

                harness._build_user_prompt = MethodType(ns["_build_user_prompt"], harness)
                fallback = eval(compile(ast.Expression(body=prompt_call), filename, "eval"), {}, {"self": harness, "batch_data": batch, "ctx": None, "retry_attempt": 1, "retry_reason": "images disabled", "image_index_map": expected, "send_images": False})
                fallback_items = json.loads(base64.b64decode(fallback[1]["file"]["file_data"].split(",", 1)[1]))
                self.assertTrue(all("image_index" not in item for item in fallback_items))


class CollectionIsolationTests(unittest.TestCase):
    """Guard collection and cleanup when the test suite shares one interpreter."""

    def test_import_does_not_register_private_modules(self):
        """Importing the test module must leave private module registrations intact."""
        previous = {name: sys.modules.get(name, _MISSING) for name in _TEST_MODULE_NAMES}
        spec = importlib.util.spec_from_file_location("_file_input_collection_probe", __file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, original in previous.items():
            self.assertIs(sys.modules.get(name, _MISSING), original)

    def test_cleanup_removes_new_modules_and_restores_existing_modules(self):
        """Cleanup restores prior registrations after normal runs or setup failures."""
        from unittest.mock import patch

        for existing in (False, True):
            for fail_setup in (False, True):
                with self.subTest(existing=existing, fail_setup=fail_setup):
                    original = {name: sys.modules.get(name, _MISSING) for name in _TEST_MODULE_NAMES}
                    try:
                        for name in _TEST_MODULE_NAMES:
                            if existing:
                                sys.modules[name] = ModuleType(name)
                            else:
                                sys.modules.pop(name, None)
                        expected = {name: sys.modules.get(name, _MISSING) for name in _TEST_MODULE_NAMES}
                        case = FileInputTests("test_legacy_prompt_and_retry")
                        try:
                            if fail_setup:
                                with patch.object(importlib.util, "module_from_spec", side_effect=RuntimeError("setup failed")):
                                    with self.assertRaisesRegex(RuntimeError, "setup failed"):
                                        case.setUp()
                            else:
                                case.setUp()
                                case.test_legacy_prompt_and_retry()
                        finally:
                            case.doCleanups()
                        for name, previous in expected.items():
                            self.assertIs(sys.modules.get(name, _MISSING), previous)
                    finally:
                        _restore_test_modules(original)


if __name__ == "__main__":
    unittest.main()
