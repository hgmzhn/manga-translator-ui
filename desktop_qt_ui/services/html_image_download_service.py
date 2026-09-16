"""Download chapter images referenced by a locally saved HTML page."""

from __future__ import annotations

import base64
import binascii
import concurrent.futures
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from extract_html_image_urls import (
    extract_canonical_url_from_html,
    extract_image_urls_from_html,
)


MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_COUNT = 200
DOWNLOAD_TIMEOUT = 45
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
CONTENT_TYPE_EXTENSIONS = {
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}
ALLOWED_EXTENSIONS = set(CONTENT_TYPE_EXTENSIONS.values()) | {".jpeg", ".tif"}


@dataclass(frozen=True)
class HtmlImageDownloadResult:
    html_path: str
    output_dir: str
    image_paths: tuple[str, ...]
    extracted_count: int
    failures: tuple[str, ...]


class HtmlImageDownloadError(RuntimeError):
    """Raised when no image can be downloaded from the selected HTML file."""


def _safe_stem(value: str) -> str:
    stem = Path(value).stem
    stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._")
    return (stem or "chapter")[:60]


def _url_extension(url: str) -> str:
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    return suffix if suffix in ALLOWED_EXTENSIONS else ".jpg"


def _content_extension(content_type: str, url: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(media_type, _url_extension(url))


def _decode_data_url(url: str) -> tuple[bytes, str]:
    header, separator, payload = url.partition(",")
    if not separator or not header.lower().startswith("data:"):
        raise ValueError("invalid data URL")

    metadata = header[5:].split(";", 1)
    media_type = metadata[0].lower() or "image/jpeg"
    if any(part.lower() == "base64" for part in metadata[1:]):
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid base64 image data") from exc
    else:
        data = unquote(payload).encode("utf-8")

    if not media_type.startswith("image/"):
        raise ValueError("data URL is not an image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    return data, CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


def _download_one(index: int, url: str, referer: str) -> tuple[int, bytes | None, str]:
    try:
        if url.startswith("data:"):
            image_data, extension = _decode_data_url(url)
            return index, image_data, extension

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("only http(s) image URLs are supported")

        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise ValueError(f"HTTP {status}")

            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/") and _url_extension(url) == ".jpg":
                raise ValueError("response is not an image")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
                chunks.append(chunk)

        image_data = b"".join(chunks)
        if not image_data:
            raise ValueError("empty response")
        return index, image_data, _content_extension(content_type, url)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return index, None, str(exc)


def _next_output_dir(html_path: Path) -> Path:
    base = html_path.with_name(f"{_safe_stem(html_path.name)}-images")
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = html_path.with_name(f"{_safe_stem(html_path.name)}-images-{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def download_html_images(
    html_path: str | Path,
    max_images: int = MAX_IMAGE_COUNT,
) -> HtmlImageDownloadResult:
    """Download extracted images into a new sibling directory.

    Existing directories are never overwritten. A numbered directory is used
    when the same HTML file is imported more than once.
    """
    path = Path(html_path).expanduser().resolve()
    if not path.is_file():
        raise HtmlImageDownloadError(f"HTML 文件不存在: {path}")
    if path.stat().st_size > MAX_HTML_BYTES:
        raise HtmlImageDownloadError("HTML 文件超过 5 MB 限制")

    html = path.read_text(encoding="utf-8", errors="replace")
    urls = extract_image_urls_from_html(html)
    limit = max(1, min(int(max_images or MAX_IMAGE_COUNT), MAX_IMAGE_COUNT))
    urls = urls[:limit]
    if not urls:
        raise HtmlImageDownloadError("HTML 中没有找到可下载的图片 URL")

    canonical_url = extract_canonical_url_from_html(html)
    first_http_url = next((url for url in urls if url.startswith(("http://", "https://"))), "")
    referer = canonical_url if canonical_url.startswith(("http://", "https://")) else first_http_url
    output_dir = _next_output_dir(path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(urls))) as executor:
        futures = [
            executor.submit(_download_one, index, url, referer)
            for index, url in enumerate(urls, start=1)
        ]
        results = [future.result() for future in futures]

    image_paths: list[str] = []
    failures: list[str] = []
    for index, image_data, extension_or_error in sorted(results, key=lambda result: result[0]):
        if image_data is None:
            failures.append(f"第 {index} 张: {extension_or_error}")
            continue
        image_path = output_dir / f"{_safe_stem(path.name)}_{index:03d}{extension_or_error}"
        image_path.write_bytes(image_data)
        image_paths.append(str(image_path))

    if not image_paths:
        try:
            output_dir.rmdir()
        except OSError:
            pass
        detail = "没有成功下载图片"
        if failures:
            detail += "；" + "；".join(failures[:3])
        raise HtmlImageDownloadError(detail)

    return HtmlImageDownloadResult(
        html_path=str(path),
        output_dir=str(output_dir),
        image_paths=tuple(image_paths),
        extracted_count=len(urls),
        failures=tuple(failures),
    )
