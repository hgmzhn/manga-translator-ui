#!/usr/bin/env python3
"""Extract image URLs from a locally saved HTML file.

The script reads HTML as text only. It does not execute JavaScript and it does
not make network requests. It understands ordinary image attributes and the
Base64-encoded ``slides_p_path`` array used by some manga readers.
"""

from __future__ import annotations

import argparse
import base64
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}
IMAGE_ATTRIBUTES = ("src", "data-src", "data-original", "data-lazy-src")


class ImageAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_values: list[str] = []
        self.canonical_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        if tag.lower() == "img":
            for name in IMAGE_ATTRIBUTES:
                value = attributes.get(name)
                if value:
                    self.image_values.append(value.strip())

        if tag.lower() == "link" and attributes.get("rel", "").lower() == "canonical":
            self.canonical_url = (attributes.get("href") or "").strip()


def _looks_like_image_url(value: str) -> bool:
    path = urlparse(value).path.lower().split("?", 1)[0]
    return any(path.endswith(extension) for extension in IMAGE_EXTENSIONS)


def _decode_base64_url(value: str) -> str | None:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded.strip() if _looks_like_image_url(decoded.strip()) else None


def _slides_page_urls(html: str) -> list[str]:
    match = re.search(r"slides_p_path\s*=\s*\[(.*?)\]\s*;", html, flags=re.DOTALL)
    if not match:
        return []

    encoded_values = re.findall(r"['\"]([A-Za-z0-9+/=_-]+)['\"]", match.group(1))
    decoded_urls: list[str] = []
    for value in encoded_values:
        decoded = _decode_base64_url(value)
        if decoded:
            decoded_urls.append(decoded)
    return decoded_urls


def _normalise_urls(values: list[str], base_url: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value.startswith("data:"):
            normalised = value
        else:
            normalised = urljoin(base_url, value)
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


def extract_image_urls(html_path: Path, include_all_images: bool = False) -> list[str]:
    return extract_image_urls_from_html(
        html_path.read_text(encoding="utf-8", errors="replace"),
        include_all_images=include_all_images,
    )


def extract_image_urls_from_html(html: str, include_all_images: bool = False) -> list[str]:
    """Extract image URLs from HTML content already loaded in memory."""
    parser = ImageAttributeParser()
    parser.feed(html)

    chapter_urls = _slides_page_urls(html)
    values = chapter_urls or parser.image_values
    if include_all_images:
        values = chapter_urls + parser.image_values

    base_url = parser.canonical_url
    return _normalise_urls(values, base_url)


def extract_canonical_url_from_html(html: str) -> str:
    """Return the canonical page URL, if the HTML declares one."""
    parser = ImageAttributeParser()
    parser.feed(html)
    return parser.canonical_url


def main() -> int:
    argument_parser = argparse.ArgumentParser(
        description="Extract image URLs from a saved HTML file without network access."
    )
    argument_parser.add_argument("html", type=Path, help="path to the saved HTML file")
    argument_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output text file; defaults to <html-name>-image-urls.txt",
    )
    argument_parser.add_argument(
        "--all-images",
        action="store_true",
        help="also include ordinary <img> and lazy-loading image attributes",
    )
    args = argument_parser.parse_args()

    if not args.html.is_file():
        argument_parser.error(f"HTML file does not exist: {args.html}")

    output_path = args.output or args.html.with_name(f"{args.html.stem}-image-urls.txt")
    urls = extract_image_urls(args.html, include_all_images=args.all_images)
    output_path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    print(f"extracted {len(urls)} image URL(s)")
    print(f"saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
