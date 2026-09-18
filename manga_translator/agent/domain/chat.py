"""Immutable image attachments accepted by the chat application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatImage:
    """An immutable image attachment supported by OpenAI Responses."""

    data: bytes
    media_type: str = "image/png"

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("image data must be bytes")
        if not self.data:
            raise ValueError("image data must not be empty")
        if self.media_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise ValueError(f"unsupported image media type: {self.media_type!r}")


@dataclass(frozen=True, slots=True)
class ChatCanvas:
    """An actual edit-tool render, separate from visible assistant text."""

    context_id: str
    image: ChatImage
    page: dict
    canvas: dict
