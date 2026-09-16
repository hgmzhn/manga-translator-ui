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
from manga_translator.webp_scramble import (
    WebpScrambleContext,
    extract_scramble_context,
    is_scrambled_chapter_url,
    page_key_from_url,
    restore_scrambled_webp,
)


MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_COUNT = 200
DOWNLOAD_TIMEOUT = 45
_18COMIC_HOSTS = frozenset({"18comic.vip", "www.18comic.vip"})
_18COMIC_CDN_HOSTS = (
    "cdn-msp3.18comic.vip",
    "cdn-msp.18comic.vip",
    "cdn-msp2.18comic.vip",
    "cdn-msp4.18comic.vip",
)
_18COMIC_SCRAMBLE_ID = 220980
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
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


def _next_output_dir(html_path: Path, output_root: str | Path | None = None) -> Path:
    return _next_output_dir_for_stem(
        html_path.name,
        html_path.parent if output_root is None else output_root,
    )


def _next_output_dir_for_stem(stem: str, output_root: str | Path | None = None) -> Path:
    parent = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else Path.cwd()
    )
    parent.mkdir(parents=True, exist_ok=True)
    base = parent / f"{_safe_stem(stem)}-images"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{_safe_stem(stem)}-images-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _validate_page_url(value: str) -> str:
    page_url = str(value or "").strip()
    if not page_url:
        raise HtmlImageDownloadError("网址不能为空")
    if "://" not in page_url:
        page_url = f"https://{page_url}"

    parsed = urlparse(page_url)
    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise HtmlImageDownloadError("网址格式不合法") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
    ):
        raise HtmlImageDownloadError("只支持 HTTP 或 HTTPS 网址，且网址不能包含用户名或密码")
    return page_url


def _fetch_html_from_url(page_url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        page_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise ValueError(f"HTTP {status}")

            content_type = response.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type and media_type not in HTML_CONTENT_TYPES:
                raise ValueError("网址返回的内容不是 HTML")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_HTML_BYTES:
                raise ValueError(f"HTML 响应超过 {MAX_HTML_BYTES // (1024 * 1024)} MB 限制")

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_HTML_BYTES:
                    raise ValueError(f"HTML 响应超过 {MAX_HTML_BYTES // (1024 * 1024)} MB 限制")
                chunks.append(chunk)

            response_url = _validate_page_url(response.geturl() or page_url)
            encoding = response.headers.get_content_charset() or "utf-8"
    except HtmlImageDownloadError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise HtmlImageDownloadError(f"网址加载失败: {exc}") from exc

    return b"".join(chunks).decode(encoding, errors="replace"), response_url


def _url_stem(page_url: str) -> str:
    parsed = urlparse(page_url)
    path_name = PurePosixPath(parsed.path).name
    return path_name or parsed.hostname or "chapter"


def _18comic_photo_aid(page_url: str) -> int | None:
    """Return the album id for an 18comic photo URL, if it is supported."""
    parsed = urlparse(page_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname not in _18COMIC_HOSTS:
        return None
    match = re.fullmatch(r"/photo/(\d+)/?", parsed.path)
    return int(match.group(1)) if match else None


def _persist_download_results(
    urls: list[str],
    results: list[tuple[int, bytes | None, str]],
    *,
    source_value: str,
    source_name: str,
    output_dir: str | Path | None,
    scramble_context: WebpScrambleContext | None,
) -> HtmlImageDownloadResult:
    """Write downloaded bytes in URL order and restore supported WebP pages."""
    download_dir = _next_output_dir_for_stem(source_name, output_dir)
    file_stem = _safe_stem(source_name)
    image_paths: list[str] = []
    failures: list[str] = []

    for index, image_data, extension_or_error in sorted(results, key=lambda result: result[0]):
        if image_data is None:
            failures.append(f"第 {index} 张: {extension_or_error}")
            continue
        source_url = urls[index - 1]
        if scramble_context and is_scrambled_chapter_url(source_url, scramble_context):
            try:
                image_data = restore_scrambled_webp(
                    image_data,
                    aid=scramble_context.aid,
                    page_key=page_key_from_url(source_url),
                )
            except (OSError, ValueError) as exc:
                failures.append(f"第 {index} 张: 图片还原失败: {exc}")
                continue
        image_path = download_dir / f"{file_stem}_{index:03d}{extension_or_error}"
        image_path.write_bytes(image_data)
        image_paths.append(str(image_path))

    if not image_paths:
        try:
            download_dir.rmdir()
        except OSError:
            pass
        detail = "没有成功下载图片"
        if failures:
            detail += "；" + "；".join(failures[:3])
        raise HtmlImageDownloadError(detail)

    return HtmlImageDownloadResult(
        html_path=source_value,
        output_dir=str(download_dir),
        image_paths=tuple(image_paths),
        extracted_count=len(urls),
        failures=tuple(failures),
    )


def _download_url_list(
    urls: list[str],
    *,
    referer: str,
    source_value: str,
    source_name: str,
    output_dir: str | Path | None,
    scramble_context: WebpScrambleContext | None,
) -> HtmlImageDownloadResult:
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(urls))) as executor:
        futures = [
            executor.submit(_download_one, index, url, referer)
            for index, url in enumerate(urls, start=1)
        ]
        results = [future.result() for future in futures]
    return _persist_download_results(
        urls,
        results,
        source_value=source_value,
        source_name=source_name,
        output_dir=output_dir,
        scramble_context=scramble_context,
    )


def _download_18comic_photo_images(
    page_url: str,
    *,
    aid: int,
    max_images: int,
    output_dir: str | Path | None,
) -> HtmlImageDownloadResult:
    """Download 18comic pages directly when Cloudflare blocks the HTML fetch."""
    limit = max(1, min(int(max_images or MAX_IMAGE_COUNT), MAX_IMAGE_COUNT))
    referer = page_url
    selected_host = ""
    urls: list[str] = []
    results: list[tuple[int, bytes | None, str]] = []

    for host in _18COMIC_CDN_HOSTS:
        first_url = f"https://{host}/media/photos/{aid}/00001.webp"
        first_result = _download_one(1, first_url, referer)
        if first_result[1] is not None:
            selected_host = host
            urls.append(first_url)
            results.append(first_result)
            break

    if not selected_host:
        raise HtmlImageDownloadError(
            "18comic 页面被拒绝，且未找到可访问的章节图片 CDN"
        )

    next_index = 2
    while next_index <= limit:
        batch_indices = list(range(next_index, min(next_index + 8, limit + 1)))
        batch_urls = [
            f"https://{selected_host}/media/photos/{aid}/{index:05d}.webp"
            for index in batch_indices
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch_urls)) as executor:
            batch_results = list(
                executor.map(
                    lambda item: _download_one(item[0], item[1], referer),
                    zip(batch_indices, batch_urls),
                )
            )

        stopped = False
        for result, url in zip(batch_results, batch_urls):
            if result[1] is None:
                stopped = True
                break
            urls.append(url)
            results.append(result)
        if stopped:
            break
        next_index += len(batch_urls)

    return _persist_download_results(
        urls,
        results,
        source_value=page_url,
        source_name=_url_stem(page_url),
        output_dir=output_dir,
        scramble_context=WebpScrambleContext(
            aid=aid,
            scramble_id=_18COMIC_SCRAMBLE_ID,
        ),
    )


def _download_extracted_images(
    html: str,
    *,
    source_value: str,
    source_name: str,
    base_url: str = "",
    max_images: int = MAX_IMAGE_COUNT,
    output_dir: str | Path | None = None,
) -> HtmlImageDownloadResult:
    urls = extract_image_urls_from_html(html, base_url=base_url)
    limit = max(1, min(int(max_images or MAX_IMAGE_COUNT), MAX_IMAGE_COUNT))
    urls = urls[:limit]
    if not urls:
        raise HtmlImageDownloadError("HTML 中没有找到可下载的图片 URL")

    canonical_url = extract_canonical_url_from_html(html)
    first_http_url = next(
        (url for url in urls if url.startswith(("http://", "https://"))), ""
    )
    if canonical_url.startswith(("http://", "https://")):
        referer = canonical_url
    else:
        referer = base_url or first_http_url
    scramble_context = extract_scramble_context(html)
    return _download_url_list(
        urls,
        referer=referer,
        source_value=source_value,
        source_name=source_name,
        output_dir=output_dir,
        scramble_context=scramble_context,
    )


def download_html_images(
    html_path: str | Path,
    max_images: int = MAX_IMAGE_COUNT,
    output_dir: str | Path | None = None,
) -> HtmlImageDownloadResult:
    """Download extracted images into a new directory under ``output_dir``.

    When ``output_dir`` is omitted, the HTML file's directory is used for
    backwards compatibility. Existing directories are never overwritten. A
    numbered directory is used when the same HTML file is imported more than
    once.
    """
    path = Path(html_path).expanduser().resolve()
    if not path.is_file():
        raise HtmlImageDownloadError(f"HTML 文件不存在: {path}")
    if path.stat().st_size > MAX_HTML_BYTES:
        raise HtmlImageDownloadError("HTML 文件超过 5 MB 限制")

    return _download_extracted_images(
        path.read_text(encoding="utf-8", errors="replace"),
        source_value=str(path),
        source_name=path.name,
        max_images=max_images,
        output_dir=path.parent if output_dir is None else output_dir,
    )


def download_html_images_from_url(
    page_url: str,
    max_images: int = MAX_IMAGE_COUNT,
    output_dir: str | Path | None = None,
) -> HtmlImageDownloadResult:
    """Fetch an HTTP(S) HTML page and download its referenced images."""
    normalized_url = _validate_page_url(page_url)
    try:
        html, final_url = _fetch_html_from_url(normalized_url)
    except HtmlImageDownloadError:
        comic_aid = _18comic_photo_aid(normalized_url)
        if comic_aid is None:
            raise
        return _download_18comic_photo_images(
            normalized_url,
            aid=comic_aid,
            max_images=max_images,
            output_dir=output_dir,
        )
    return _download_extracted_images(
        html,
        source_value=normalized_url,
        source_name=_url_stem(final_url),
        base_url=final_url,
        max_images=max_images,
        output_dir=output_dir,
    )
