from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

LOCAL_OPENAI_API_KEY_PLACEHOLDER = "ollama"
_OFFICIAL_OPENAI_HOSTNAMES = {
    "api.openai.com",
}
_LOCAL_HOSTNAMES = {
    "localhost",
    "0.0.0.0",
    "host.docker.internal",
    "ollama",
}
_LOCAL_HOST_SUFFIXES = (
    ".local",
    ".lan",
    ".home",
    ".internal",
)


def _extract_hostname(base_url: str | None) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        return (urlparse(raw).hostname or "").strip().lower()
    except Exception:
        return ""


def is_local_openai_compatible_endpoint(base_url: str | None) -> bool:
    host = _extract_hostname(base_url)
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    if host.endswith(_LOCAL_HOST_SUFFIXES):
        return True
    if "ollama" in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def resolve_openai_compatible_api_key(
    api_key: str | None,
    base_url: str | None,
    placeholder: str = LOCAL_OPENAI_API_KEY_PLACEHOLDER,
) -> str:
    normalized = str(api_key or "").strip()
    if normalized:
        return normalized
    if is_local_openai_compatible_endpoint(base_url):
        return placeholder
    return normalized
