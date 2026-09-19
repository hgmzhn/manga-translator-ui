"""In-memory execution snapshots, independent of the UI and model history."""

from __future__ import annotations

import json
import logging
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4


def _snapshot(value, secret):
    from pydantic import BaseModel
    from pydantic_ai import BinaryContent

    if isinstance(value, BinaryContent):
        return {"kind": "binary", "media_type": value.media_type,
                "bytes": len(value.data), "identifier": value.identifier}
    if isinstance(value, bytes):
        return {"kind": "binary", "bytes": len(value)}
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, dict):
        return {str(key): _snapshot(item, secret) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot(item, secret) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _snapshot(getattr(value, field.name), secret) for field in fields(value)}
    if isinstance(value, BaseModel):
        return _snapshot(value.model_dump(mode="python"), secret)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _snapshot(str(value), secret)


class ContextTrace:
    """Send detached records; streaming parts update one record per response."""

    def __init__(self, observer, *, secret=""):
        self.enabled = observer is not None
        self._observer = observer
        self._secret = secret
        self.turn_id = uuid4().hex
        self._sequence = 0
        self._response_index = -1
        self._parts = {}
        self._last_emit = 0.0
        self._response_complete = True

    def add(self, kind, data, *, record_id=None):
        if not self.enabled:
            return
        self._sequence += 1
        payload = self.snapshot(data)
        record = {
            "id": record_id or f"{self.turn_id}:{self._sequence}",
            "turn_id": self.turn_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "data": payload,
        }
        try:
            self._observer(record)
        except Exception:
            logging.getLogger(__name__).exception("Context debug observer failed")

    def snapshot(self, data):
        """Detach display data and redact secrets even when full tracing is disabled."""
        payload = _snapshot(data, self._secret)
        _summarize_images(payload)
        return payload

    def response_event(self, response_index, event):
        if not self.enabled:
            return
        from pydantic_ai.messages import PartDeltaEvent

        if response_index != self._response_index:
            self.finish_response()
            self._response_index = response_index
            self._parts = {}
            self._response_complete = False
        if isinstance(event, PartDeltaEvent):
            previous = self._parts.get(event.index)
            if previous is not None:
                self._parts[event.index] = event.delta.apply(previous)
            if monotonic() - self._last_emit < 0.1:
                return
        else:
            self._parts[event.index] = event.part
        self._emit_response("streaming")

    def _emit_response(self, state):
        if self._parts:
            self.add("model_response", {
                "response_index": self._response_index + 1, "state": state,
                "parts": [part for _, part in sorted(self._parts.items())],
            }, record_id=f"{self.turn_id}:response:{self._response_index}")
            self._last_emit = monotonic()

    def finish_response(self, state="complete"):
        if not self._response_complete:
            self._emit_response(state)
            self._response_complete = True


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
