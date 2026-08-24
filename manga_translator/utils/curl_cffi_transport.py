from __future__ import annotations

import ctypes
import os

from .openai_compat import is_local_openai_compatible_endpoint

CURL_CFFI_IMPERSONATE = "chrome"
OPENAI_CURL_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
}
GEMINI_CURL_HEADERS = {
    **OPENAI_CURL_HEADERS,
    "Origin": "https://aistudio.google.com",
    "Referer": "https://aistudio.google.com/",
}


class InvalidAPIKeyCharactersError(ValueError):
    """Raised before an API key with unsupported characters reaches HTTP headers."""

    def __init__(self, start: int, end: int):
        self.start_position = start + 1
        self.end_position = end
        super().__init__(
            "API key contains characters unsupported by HTTP headers "
            f"at positions {self.start_position}-{self.end_position}. "
            "Re-paste the key and remove Chinese, full-width, or invisible characters."
        )


def validate_api_key_for_http_header(api_key: str | None) -> str:
    """Reject API-key characters that curl_cffi cannot encode in a header."""
    value = str(api_key or "")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise InvalidAPIKeyCharactersError(exc.start, exc.end) from None
    return value


class _CurlACPPath(str):
    """A path string whose C-boundary encoding is fixed to Windows ACP."""

    def __new__(cls, path: str, encoding: str):
        value = super().__new__(cls, path)
        value._curl_encoding = encoding
        return value

    def encode(self, encoding="utf-8", errors="strict"):
        # curl_cffi calls str.encode() before passing char* options to libcurl.
        # Ignore Python UTF-8 mode for this one native file-path boundary.
        return super().encode(self._curl_encoding, errors)


def _encode_curl_file_path(path: str) -> str:
    """Encode a libcurl file path for Windows' ANSI ``char*`` API."""
    if os.name != "nt":
        return path

    acp = ctypes.windll.kernel32.GetACP()
    if not acp:
        raise OSError("Windows GetACP() returned no active code page")
    return _CurlACPPath(path, f"cp{acp}")


def create_curl_cffi_async_session(
    *,
    base_url: str,
    impersonate: str = CURL_CFFI_IMPERSONATE,
):
    """Create a curl_cffi session without implicit environment proxy lookup."""
    from curl_cffi.curl import DEFAULT_CACERT
    from curl_cffi.requests import AsyncSession

    # Keep this as a path-like string so AsyncSession can create its async
    # connection pool lazily, while controlling the later C-boundary encoding.
    verify = _encode_curl_file_path(DEFAULT_CACERT)
    session_kwargs = {
        "trust_env": False,
        "verify": verify,
    }
    if not is_local_openai_compatible_endpoint(base_url):
        session_kwargs["impersonate"] = impersonate
    return AsyncSession(**session_kwargs)
