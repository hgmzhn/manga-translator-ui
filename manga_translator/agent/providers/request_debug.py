"""Readable request snapshots without embedding large image payloads in the UI."""

from __future__ import annotations

import json


def format_request_body(content: bytes) -> str:
    """Format a display-only copy; never modify the outgoing HTTP request."""
    body = json.loads(content)
    _summarize_images(body)
    return json.dumps(body, ensure_ascii=False, indent=2)


def _summarize_images(value) -> None:
    if isinstance(value, list):
        for item in value:
            _summarize_images(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if (
                key in {"image_url", "url"}
                and isinstance(item, str)
                and item.startswith("data:image/")
            ):
                separator = item.find(",")
                if separator >= 0 and item[:separator].endswith(";base64"):
                    media_type = item[5:separator].split(";", 1)[0]
                    encoded_length = len(item) - separator - 1
                    padding = 2 if item.endswith("==") else int(item.endswith("="))
                    byte_count = encoded_length * 3 // 4 - padding
                    value[key] = (
                        f"<{media_type}: base64 omitted; "
                        f"{byte_count:,} bytes / {encoded_length:,} base64 characters>"
                    )
            else:
                _summarize_images(item)
