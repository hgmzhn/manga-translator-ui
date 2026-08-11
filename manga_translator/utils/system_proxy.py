from __future__ import annotations

from urllib.parse import quote

_enabled = False


def set_system_proxy_enabled(enabled: bool) -> None:
    """Enable Qt's OS proxy lookup for application network requests."""
    global _enabled
    _enabled = bool(enabled)

    try:
        from PyQt6.QtNetwork import QNetworkProxyFactory

        QNetworkProxyFactory.setUseSystemConfiguration(_enabled)
    except (ImportError, RuntimeError):
        # Non-Qt entry points keep their existing direct/network-library behavior.
        pass


def is_system_proxy_enabled() -> bool:
    return _enabled


def resolve_system_proxy_url(url: str) -> str | None:
    """Return the operating-system proxy selected by Qt for *url*."""
    if not _enabled or not str(url or "").strip():
        return None

    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtNetwork import (
            QNetworkProxy,
            QNetworkProxyFactory,
            QNetworkProxyQuery,
        )

        proxies = QNetworkProxyFactory.systemProxyForQuery(
            QNetworkProxyQuery(QUrl(str(url)))
        )
    except (ImportError, RuntimeError):
        return None

    return _proxy_url_from_qt_proxies(proxies, QNetworkProxy.ProxyType)


def _proxy_url_from_qt_proxies(proxies, proxy_types) -> str | None:
    """Convert Qt's ordered proxy result into one transport proxy URL."""
    for proxy in proxies:
        proxy_type = proxy.type()
        if proxy_type == proxy_types.NoProxy:
            return None
        if proxy_type == proxy_types.DefaultProxy:
            continue

        host = proxy.hostName().strip()
        port = int(proxy.port())
        if not host or port <= 0:
            continue

        if proxy_type == proxy_types.Socks5Proxy:
            scheme = "socks5"
        elif proxy_type in {
            proxy_types.HttpProxy,
            proxy_types.HttpCachingProxy,
            proxy_types.FtpCachingProxy,
        }:
            scheme = "http"
        else:
            continue

        if ":" in host and not host.startswith("["):
            host = f"[{host}]"

        username = proxy.user().strip()
        password = proxy.password()
        credentials = ""
        if username:
            credentials = quote(username, safe="")
            if password:
                credentials += f":{quote(password, safe='')}"
            credentials += "@"

        return f"{scheme}://{credentials}{host}:{port}"

    return None


def system_proxy_request_kwargs(url: str) -> dict[str, str]:
    """Build curl_cffi/httpx-style per-request proxy keyword arguments."""
    proxy_url = resolve_system_proxy_url(url)
    return {"proxy": proxy_url} if proxy_url else {}


def openai_http_client_kwargs(url: str) -> dict[str, object]:
    """Build an OpenAI SDK http_client argument when the OS selects a proxy."""
    proxy_url = resolve_system_proxy_url(url)
    if not proxy_url:
        return {}

    import httpx

    return {"http_client": httpx.AsyncClient(proxy=proxy_url)}


def gemini_http_options_proxy_args(url: str, *, asynchronous: bool = False) -> dict[str, dict[str, str]]:
    """Build google-genai HttpOptions transport arguments for an OS proxy."""
    proxy_url = resolve_system_proxy_url(url)
    if not proxy_url:
        return {}

    field = "async_client_args" if asynchronous else "client_args"
    return {field: {"proxy": proxy_url}}
