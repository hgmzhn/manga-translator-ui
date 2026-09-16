import asyncio
from urllib.parse import parse_qs

import _bootstrap  # noqa: F401

from manga_translator.config import Config, Translator
from manga_translator.translators import TRANSLATORS
from manga_translator.translators import aliyun as aliyun_module
from manga_translator.translators.aliyun import (
    AliyunTranslator,
    aliyun_percent_encode,
    build_aliyun_translate_request,
)


def test_aliyun_translator_is_registered_and_maps_languages():
    translator = AliyunTranslator()

    assert Translator.aliyun in TRANSLATORS
    assert translator.supports_languages("JPN", "CHS")
    assert translator.parse_language_codes("JPN", "CHS") == ("ja", "zh")
    assert translator.parse_language_codes("auto", "CHT") == ("auto", "zh-tw")


def test_aliyun_rpc_signature_request_is_deterministic():
    endpoint, body = build_aliyun_translate_request(
        "test-id",
        "test-secret",
        "こんにちは!*",
        "ja",
        "zh",
        api_base="https://example.invalid/",
        signature_nonce="fixed-nonce",
        timestamp="2026-01-01T00:00:00Z",
    )
    fields = parse_qs(body, keep_blank_values=True)

    assert endpoint == "https://example.invalid"
    assert fields["Action"] == ["TranslateGeneral"]
    assert fields["SourceText"] == ["こんにちは!*"]
    assert fields["SignatureVersion"] == ["1.0"]
    assert fields["SignatureNonce"] == ["fixed-nonce"]
    assert fields["Timestamp"] == ["2026-01-01T00:00:00Z"]
    assert fields["Signature"]
    assert aliyun_percent_encode("!*'()") == "%21%2A%27%28%29"


def test_aliyun_translator_sends_one_signed_request_per_text(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def json(self):
            return {"Code": 200, "Data": {"Translated": self.text}}

    class FakeSession:
        def __init__(self):
            self.bodies = []

        async def post(self, endpoint, *, data, **kwargs):
            self.bodies.append((endpoint, data, kwargs))
            return FakeResponse(f"译文{len(self.bodies)}")

        async def close(self):
            return None

    session = FakeSession()
    monkeypatch.setattr(
        aliyun_module,
        "create_curl_cffi_async_session",
        lambda **kwargs: session,
    )

    translator = AliyunTranslator()
    translator.access_key_id = "test-id"
    translator.access_key_secret = "test-secret"
    translator.api_base = "https://example.invalid"
    result = asyncio.run(translator._translate("ja", "zh", ["一", "二"]))

    assert result == ["译文1", "译文2"]
    assert len(session.bodies) == 2
    assert all(item[0] == "https://example.invalid" for item in session.bodies)
    assert all("Signature=" in item[1] for item in session.bodies)
    assert all("Content-Type" in item[2]["headers"] for item in session.bodies)


def test_aliyun_config_credentials_override_environment(monkeypatch):
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "env-id")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "env-secret")
    config = Config(
        translator={
            "translator": "aliyun",
            "aliyun_access_key_id": "config-id",
            "aliyun_access_key_secret": "config-secret",
        }
    )

    translator = AliyunTranslator()
    translator.parse_args(config)

    assert translator.access_key_id == "config-id"
    assert translator.access_key_secret == "config-secret"
