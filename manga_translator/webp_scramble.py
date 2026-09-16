"""Restore the horizontal strip layout used by some manga image hosts."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from PIL import Image


_18COMIC_HOST = "18comic.vip"
_JS_INTEGER_TEMPLATE = (
    r"\b(?:var|let|const)\s+{name}\s*=\s*['\"]?(\d+)['\"]?"
)


@dataclass(frozen=True)
class WebpScrambleContext:
    """The page-level values required by the host's strip permutation."""

    aid: int
    scramble_id: int

    @property
    def enabled(self) -> bool:
        # The site's JavaScript bypasses scrambling for albums older than the
        # rollout threshold. Keep the same boundary here.
        return self.aid >= self.scramble_id


def _extract_js_integer(html: str, name: str) -> int | None:
    match = _JS_INTEGER_TEMPLATE.format(name=re.escape(name))
    found = re.search(match, html, flags=re.IGNORECASE)
    return int(found.group(1)) if found else None


def extract_scramble_context(html: str) -> WebpScrambleContext | None:
    """Read the page variables used by 18comic's client-side decoder."""
    aid = _extract_js_integer(html, "aid")
    scramble_id = _extract_js_integer(html, "scramble_id")
    if aid is None or scramble_id is None:
        return None
    return WebpScrambleContext(aid=aid, scramble_id=scramble_id)


def page_key_from_url(url: str) -> str:
    """Return the zero-padded page key used by the JavaScript decoder."""
    filename = unquote(PurePosixPath(urlparse(url).path).name)
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def is_scrambled_chapter_url(url: str, context: WebpScrambleContext) -> bool:
    """Limit restoration to 18comic chapter page images only."""
    if not context.enabled:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname != _18COMIC_HOST and not hostname.endswith(f".{_18COMIC_HOST}"):
        return False

    parts = [part for part in PurePosixPath(parsed.path).parts if part != "/"]
    return len(parts) >= 3 and parts[-3:-1] == ["photos", str(context.aid)]


def stripe_count(aid: int, page_key: str) -> int:
    """Match the site's ``get_num`` function exactly."""
    digest = hashlib.md5(f"{aid}{page_key}".encode("utf-8")).hexdigest()
    value = ord(digest[-1])
    if 268850 <= aid <= 421925:
        value %= 10
    elif aid >= 421926:
        value %= 8

    return {
        0: 2,
        1: 4,
        2: 6,
        3: 8,
        4: 10,
        5: 12,
        6: 14,
        7: 16,
        8: 18,
        9: 20,
    }.get(value, 10)


def restore_scrambled_webp(
    image_data: bytes,
    *,
    aid: int,
    page_key: str,
) -> bytes:
    """Restore a scrambled WebP and return a lossless WebP encoding.

    The source WebP is already compressed by the host. Re-encoding the
    reconstructed pixels as lossless WebP avoids adding another lossy pass.
    Non-WebP data is returned unchanged so this helper is safe on mixed pages.
    """
    with Image.open(io.BytesIO(image_data)) as source:
        if (source.format or "").upper() != "WEBP" or getattr(source, "n_frames", 1) != 1:
            return image_data

        width, height = source.size
        count = stripe_count(aid, page_key)
        if count <= 1 or height < count:
            return image_data

        source.load()
        restored = Image.new(source.mode, source.size)
        base_height, remainder = divmod(height, count)

        # This is the inverse of the site's drawImage loop. The first source
        # strip contains the remainder and represents the top of the page;
        # the remaining source strips are laid out from bottom to top.
        first_height = base_height + remainder
        restored.paste(
            source.crop((0, height - first_height, width, height)),
            (0, 0),
        )
        for destination_index in range(1, count):
            source_top = height - base_height * (destination_index + 1) - remainder
            restored.paste(
                source.crop((0, source_top, width, source_top + base_height)),
                (0, remainder + destination_index * base_height),
            )

        output = io.BytesIO()
        restored.save(output, format="WEBP", lossless=True, method=6)
        return output.getvalue()
