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


def _encode_curl_file_path(path: str) -> str | bytes:
    """Encode a libcurl file path for Windows' ANSI ``char*`` API.

    curl_cffi's upstream fix uses ``locale.getpreferredencoding(False)``.
    This application intentionally runs with ``PYTHONUTF8=1``, which makes
    that function return UTF-8 instead of the Windows system ANSI code page.
    libcurl on Windows then cannot open non-ASCII CA paths and reports error
    77.  Passing ACP-encoded bytes preserves the application's UTF-8 mode
    while matching the native file API.
    """
    if os.name != "nt":
        return path

    acp = ctypes.windll.kernel32.GetACP()
    if not acp:
        raise OSError("Windows GetACP() returned no active code page")
    return path.encode(f"cp{acp}", errors="strict")


def create_curl_cffi_async_session(
    *,
    base_url: str,
    impersonate: str = CURL_CFFI_IMPERSONATE,
):
    """Create a curl_cffi session without implicit environment proxy lookup."""
    from curl_cffi.curl import DEFAULT_CACERT
    from curl_cffi.requests import AsyncSession

    verify = _encode_curl_file_path(DEFAULT_CACERT)
    if is_local_openai_compatible_endpoint(base_url):
        return AsyncSession(trust_env=False, verify=verify)
    return AsyncSession(
        trust_env=False,
        verify=verify,
        impersonate=impersonate,
    )
