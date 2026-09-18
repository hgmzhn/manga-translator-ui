"""Deterministic natural ordering for registered, relative page identities."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_DIGITS = re.compile(r"([0-9]+)")


def _natural(value: str) -> tuple:
    return tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part.casefold())
        for part in _DIGITS.split(value)
    )


def page_order(folder: str, name: str) -> tuple:
    """Sort folder components and filename stems naturally, extension breaking ties."""
    filename = PurePosixPath(name)
    directories = () if folder == "." else tuple(_natural(part) for part in folder.split("/"))
    return directories, _natural(filename.stem), filename.suffix.casefold(), folder, name
