from __future__ import annotations

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


def create_curl_cffi_async_session(
    *,
    base_url: str,
    impersonate: str = CURL_CFFI_IMPERSONATE,
):
    """Create a curl_cffi session without implicit environment proxy lookup."""
    from curl_cffi.requests import AsyncSession

    if is_local_openai_compatible_endpoint(base_url):
        return AsyncSession(trust_env=False)
    return AsyncSession(trust_env=False, impersonate=impersonate)
