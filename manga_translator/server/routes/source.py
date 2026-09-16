"""Source import routes for the web UI."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import re
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import aiohttp
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from extract_html_image_urls import (
    extract_canonical_url_from_html,
    extract_image_urls_from_html,
)
from manga_translator.webp_scramble import (
    extract_scramble_context,
    is_scrambled_chapter_url,
    page_key_from_url,
    restore_scrambled_webp,
)
from manga_translator.server.core.middleware import require_auth
from manga_translator.server.core.models import Session


router = APIRouter(prefix="/source", tags=["source"])

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_COUNT = 200
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=10, sock_read=30)
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


def _safe_stem(filename: str | None) -> str:
    stem = PurePosixPath(filename or "chapter").stem
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
    is_base64 = any(part.lower() == "base64" for part in metadata[1:])
    if is_base64:
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid base64 image data") from exc
    else:
        data = unquote(payload).encode("utf-8")

    if not media_type.startswith("image/"):
        raise ValueError("data URL is not an image")
    return data, CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


async def _download_image(
    session: aiohttp.ClientSession,
    url: str,
    referer: str,
) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("only http(s) image URLs are supported")

    request_headers = {"Referer": referer} if referer else None
    async with session.get(url, allow_redirects=True, headers=request_headers) as response:
        if response.status >= 400:
            raise ValueError(f"HTTP {response.status}")

        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/") and _url_extension(url) == ".jpg":
            raise ValueError("response is not an image")

        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_IMAGE_BYTES:
            raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
            chunks.append(chunk)

        data = b"".join(chunks)
        if not data:
            raise ValueError("empty response")
        return data, _content_extension(content_type, url)


async def _download_one(
    session: aiohttp.ClientSession,
    index: int,
    url: str,
    referer: str,
) -> tuple[int, bytes, str] | tuple[int, None, str]:
    try:
        if url.startswith("data:"):
            image_data, extension = _decode_data_url(url)
        else:
            image_data, extension = await _download_image(session, url, referer)
        return index, image_data, extension
    except (aiohttp.ClientError, ValueError, OSError) as exc:
        return index, None, str(exc)


@router.post("/html/download", response_class=StreamingResponse)
async def download_html_images(
    html: UploadFile = File(...),
    include_all_images: bool = Form(False),
    max_images: int = Form(0),
    _session: Session = Depends(require_auth),
):
    """Extract chapter images from uploaded HTML and return them as a ZIP."""
    html_bytes = await html.read(MAX_HTML_BYTES + 1)
    if len(html_bytes) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="HTML 文件超过 5 MB 限制")

    html_text = html_bytes.decode("utf-8", errors="replace")
    urls = extract_image_urls_from_html(html_text, include_all_images=include_all_images)
    requested_limit = max_images if max_images > 0 else MAX_IMAGE_COUNT
    urls = urls[: min(requested_limit, MAX_IMAGE_COUNT)]
    if not urls:
        raise HTTPException(status_code=422, detail="HTML 中没有找到可下载的图片 URL")

    # 优先使用 HTML 声明的原章节地址，兼容需要页面 Referer 的图片 CDN。
    canonical_url = extract_canonical_url_from_html(html_text)
    first_http_url = next((url for url in urls if url.startswith(("http://", "https://"))), "")
    referer = canonical_url if canonical_url.startswith(("http://", "https://")) else first_http_url
    archive_stem = _safe_stem(html.filename)
    scramble_context = extract_scramble_context(html_text)

    connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT, connector=connector, headers=headers) as session:
        results = await asyncio.gather(
            *(_download_one(session, index, url, referer) for index, url in enumerate(urls, start=1))
        )

    downloaded: list[tuple[str, bytes]] = []
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
        downloaded.append((f"{archive_stem}_{index:03d}{extension_or_error}", image_data))

    if not downloaded:
        detail = "没有成功下载图片"
        if failures:
            detail += "；" + "；".join(failures[:3])
        raise HTTPException(status_code=502, detail=detail)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for filename, image_data in downloaded:
            zip_file.writestr(filename, image_data)
    archive.seek(0)

    response = StreamingResponse(archive, media_type="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="{archive_stem}-images.zip"'
    response.headers["X-Extracted-Count"] = str(len(urls))
    response.headers["X-Downloaded-Count"] = str(len(downloaded))
    response.headers["X-Failed-Count"] = str(len(failures))
    return response
