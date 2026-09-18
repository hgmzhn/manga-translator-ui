"""Shared value serialization for workspace commands and search snapshots."""

import hashlib
import json

def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _patch(value) -> dict:
    return value.model_dump(mode="json", exclude_unset=True)

