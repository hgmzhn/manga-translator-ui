import _bootstrap  # noqa: F401, I001
import asyncio
import io
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PIL import Image
from PyQt6.QtNetwork import QNetworkProxy

from manga_translator.colorization.model_api_colorizer import BaseAPIColorizer
from manga_translator.rendering.model_api_renderer import BaseAPIRenderer
from manga_translator.translators import common
from manga_translator.utils import system_proxy
from manga_translator.utils import curl_cffi_transport


PROXY_URL = "http://proxy.example:8080"


class _Response:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}
    text = ""

    def __init__(self, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def json(self):
        return self._payload


class _RecordingSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response


def _proxy_kwargs(_url):
    return {"proxy": PROXY_URL}


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_qt_proxy_conversion_preserves_type_credentials_and_bypass():
    proxy_types = QNetworkProxy.ProxyType
    http_proxy = QNetworkProxy(
        proxy_types.HttpProxy,
        "proxy.example",
        8080,
        "user name",
        "p@ss",
    )
    socks_proxy = QNetworkProxy(proxy_types.Socks5Proxy, "2001:db8::1", 1080)
    no_proxy = QNetworkProxy(proxy_types.NoProxy)

    assert system_proxy._proxy_url_from_qt_proxies([http_proxy], proxy_types) == (
        "http://user%20name:p%40ss@proxy.example:8080"
    )
    assert system_proxy._proxy_url_from_qt_proxies([socks_proxy], proxy_types) == (
        "socks5://[2001:db8::1]:1080"
    )
    assert (
        system_proxy._proxy_url_from_qt_proxies([no_proxy, http_proxy], proxy_types)
        is None
    )


def test_openai_shared_transport_resolves_proxy_for_each_request(monkeypatch):
    session = _RecordingSession(
        _Response({"choices": [{"message": {"content": "ok"}}]})
    )
    parent = SimpleNamespace(
        api_key="key",
        base_url="https://api.example/v1",
        default_headers={},
        timeout=30,
        stream_timeout=30,
        session=session,
    )
    monkeypatch.setattr(common, "system_proxy_request_kwargs", _proxy_kwargs)

    response = asyncio.run(
        common.AsyncOpenAICurlCffi.ChatCompletions(parent).create(
            model="model",
            messages=[{"role": "user", "content": "test"}],
        )
    )

    assert response.choices[0].message.content == "ok"
    assert session.calls[0][0:2] == (
        "POST",
        "https://api.example/v1/chat/completions",
    )
    assert session.calls[0][2]["proxy"] == PROXY_URL


def test_gemini_shared_transport_resolves_proxy_for_each_request(monkeypatch):
    session = _RecordingSession(
        _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ]
            }
        )
    )
    parent = SimpleNamespace(
        api_key="key",
        base_url="https://generativelanguage.googleapis.com",
        default_headers={},
        timeout=30,
        stream_timeout=30,
        session=session,
    )
    monkeypatch.setattr(common, "system_proxy_request_kwargs", _proxy_kwargs)

    response = asyncio.run(
        common.AsyncGeminiCurlCffi.Models(parent).generate_content(
            model="model",
            contents="test",
        )
    )

    assert response.text == "ok"
    assert session.calls[0][0:2] == (
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/model:generateContent",
    )
    assert session.calls[0][2]["proxy"] == PROXY_URL


@pytest.mark.parametrize(
    ("owner_type", "fetch_method"),
    [
        (BaseAPIRenderer, BaseAPIRenderer._fetch_image_from_url),
        (BaseAPIColorizer, BaseAPIColorizer._fetch_image_from_url),
    ],
)
def test_generated_image_download_resolves_proxy_for_target_url(
    monkeypatch,
    owner_type,
    fetch_method,
):
    session = _RecordingSession(_Response(content=_png_bytes()))
    owner = object.__new__(owner_type)
    owner.client = SimpleNamespace(session=session)
    module_name = fetch_method.__module__
    module = __import__(module_name, fromlist=["system_proxy_request_kwargs"])
    monkeypatch.setattr(module, "system_proxy_request_kwargs", _proxy_kwargs)

    image = asyncio.run(fetch_method(owner, "https://images.example/result.png"))

    assert image.size == (2, 2)
    assert session.calls[0][0:2] == (
        "GET",
        "https://images.example/result.png",
    )
    assert session.calls[0][2]["proxy"] == PROXY_URL


def test_curl_transport_uses_current_chrome_without_environment_proxy(monkeypatch):
    created = []

    class FakeAsyncSession:
        def __init__(self, **kwargs):
            created.append(kwargs)

    import curl_cffi.requests

    monkeypatch.setattr(curl_cffi.requests, "AsyncSession", FakeAsyncSession)

    curl_cffi_transport.create_curl_cffi_async_session(
        base_url="https://api.example/v1"
    )

    assert created == [{"trust_env": False, "impersonate": "chrome"}]


def test_curl_transport_disables_impersonation_for_local_endpoints(monkeypatch):
    created = []

    class FakeAsyncSession:
        def __init__(self, **kwargs):
            created.append(kwargs)

    import curl_cffi.requests

    monkeypatch.setattr(curl_cffi.requests, "AsyncSession", FakeAsyncSession)

    curl_cffi_transport.create_curl_cffi_async_session(
        base_url="http://127.0.0.1:11434/v1"
    )

    assert created == [{"trust_env": False}]


def test_curl_transport_headers_do_not_override_impersonated_browser_identity():
    forbidden_headers = {
        "user-agent",
        "connection",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
    }

    assert forbidden_headers.isdisjoint(
        header.lower() for header in curl_cffi_transport.OPENAI_CURL_HEADERS
    )
    assert forbidden_headers.isdisjoint(
        header.lower() for header in curl_cffi_transport.GEMINI_CURL_HEADERS
    )


@pytest.mark.parametrize(
    "client_type",
    [common.AsyncOpenAICurlCffi, common.AsyncGeminiCurlCffi],
)
def test_api_clients_reject_unencodable_key_before_creating_transport(
    monkeypatch,
    client_type,
):
    def fail_if_called(**_kwargs):
        pytest.fail("transport must not be created for an unencodable API key")

    monkeypatch.setattr(common, "create_curl_cffi_async_session", fail_if_called)

    with pytest.raises(curl_cffi_transport.InvalidAPIKeyCharactersError) as error:
        client_type(
            api_key="abcdefghijklm中文测试",
            base_url="https://api.example/v1",
        )

    assert error.value.start_position == 14
    assert error.value.end_position == 17
    assert "latin-1" not in str(error.value)


def test_api_key_header_validation_preserves_supported_key():
    api_key = "sk-valid_ASCII-key"

    assert curl_cffi_transport.validate_api_key_for_http_header(api_key) == api_key
