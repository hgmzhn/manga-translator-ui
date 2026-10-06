"""Download chapter images referenced by a locally saved HTML page."""

from __future__ import annotations

import base64
import binascii
import concurrent.futures
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, unquote, urlparse

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
_MANHUABIKA_HOSTS = frozenset({"manhuabika.com", "www.manhuabika.com"})
_MANHUABIKA_API_DOMAINS = ("picaapi.go2778.com", "picaapi.acbbb.com")
_MANHUABIKA_APP_VERSION = "20251017"
_MANHUABIKA_SIGNING_SALT = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
_MANHUABIKA_SIGNING_KEY = (
    "~d}$Q7$eIni=V)9\\RK/P.RM4;9[7|@/CA}b~OW!3?EV`<>M7pddUBL5n|0/*Cn"
)
_MANHUABIKA_NONCE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
_MANHUABIKA_BROWSER_MANIFEST_SCHEMA = "manhuabika-browser-manifest/v1"
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


def _manhuabika_reader_parts(page_url: str) -> tuple[str, int] | None:
    """Return the comic id and chapter order for a PicaWeb reader URL."""
    parsed = urlparse(page_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname not in _MANHUABIKA_HOSTS:
        return None
    match = re.fullmatch(r"/comic/reader/([^/]+)/([0-9]+)/?", parsed.path)
    if not match:
        return None
    comic_id, order = match.groups()
    return comic_id, int(order)


def _manhuabika_nonce() -> str:
    return "".join(secrets.choice(_MANHUABIKA_NONCE_ALPHABET) for _ in range(32)).lower()


def _manhuabika_signature(
    path_with_query: str,
    timestamp: str,
    nonce: str,
    method: str,
) -> str:
    """Match the HMAC-SHA256 request signature used by the PicaWeb client."""
    message = (
        f"{path_with_query}{timestamp}{nonce}{method}{_MANHUABIKA_SIGNING_SALT}"
    ).lower()
    return hmac.new(
        _MANHUABIKA_SIGNING_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _manhuabika_media_url(media: object) -> str:
    """Build an original image URL from a Pica page's media object."""
    if not isinstance(media, dict):
        return ""
    file_server = str(media.get("fileServer") or "").strip()
    path = str(media.get("path") or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        url = path
    else:
        if not file_server:
            return ""
        if not file_server.startswith(("http://", "https://")):
            file_server = f"https://{file_server}"
        base = file_server.rstrip("/")
        clean_path = path.lstrip("/")
        if base.endswith("/static"):
            url = f"{base}/{clean_path}"
        elif clean_path.startswith("static/"):
            url = f"{base}/{clean_path}"
        else:
            url = f"{base}/static/{clean_path}"

    # The API may return the proxy path while the reader's 原图 setting uses
    # the corresponding /tobs/sub_storage_1/... object.
    if "/g:ce/" in url:
        encoded = url.rsplit("/g:ce/", 1)[1].rsplit(".", 1)[0]
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            decoded = ""
        if decoded.startswith(("http://", "https://")):
            url = decoded

    parsed = urlparse(url)
    if parsed.path.startswith("/static/sub_storage_1/"):
        url = url.replace(
            "/static/sub_storage_1/",
            "/static/tobs/sub_storage_1/",
            1,
        )
    return url


def _manhuabika_browser_manifest_urls(
    manifest: object,
    *,
    max_images: int,
) -> tuple[str, str, list[str]]:
    """Validate a browser-collected manifest without accepting browser state."""
    if not isinstance(manifest, dict):
        raise HtmlImageDownloadError("manhuabika 浏览器清单必须是 JSON 对象")
    if manifest.get("schema") != _MANHUABIKA_BROWSER_MANIFEST_SCHEMA:
        raise HtmlImageDownloadError("不支持的 manhuabika 浏览器清单版本")

    page_url = _validate_page_url(str(manifest.get("page_url") or ""))
    reader_parts = _manhuabika_reader_parts(page_url)
    if reader_parts is None:
        raise HtmlImageDownloadError(
            "manhuabika 浏览器清单的 page_url 不是受支持的阅读器网址"
        )
    comic_id, order = reader_parts

    raw_pages = manifest.get("pages")
    if isinstance(raw_pages, dict):
        page_items = list(raw_pages.items())
    elif isinstance(raw_pages, list):
        page_items = []
        for item in raw_pages:
            if not isinstance(item, dict):
                continue
            page_items.append((item.get("number"), item.get("url")))
    else:
        page_items = []

    page_by_number: dict[int, str] = {}
    for raw_number, raw_url in page_items:
        try:
            number = int(raw_number)
        except (TypeError, ValueError):
            continue
        url = str(raw_url or "").strip()
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if number < 1 or not url or number in page_by_number:
            continue
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.username
            or parsed.password
            or not hostname.endswith(".picacomic.com")
            or "/static/tobs/" not in parsed.path
        ):
            continue
        page_by_number[number] = url

    limit = max(1, min(int(max_images or MAX_IMAGE_COUNT), MAX_IMAGE_COUNT))
    urls = [page_by_number[number] for number in sorted(page_by_number)][:limit]
    if not urls:
        raise HtmlImageDownloadError(
            "manhuabika 浏览器清单没有可用的原图地址；只接受 picacomic.com/static/tobs 图片"
        )
    return page_url, f"manhuabika-{comic_id}-{order}", urls


def download_manhuabika_browser_manifest(
    manifest_path: str | Path,
    max_images: int = MAX_IMAGE_COUNT,
    output_dir: str | Path | None = None,
) -> HtmlImageDownloadResult:
    """Download a browser-collected, credential-free manhuabika manifest."""
    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise HtmlImageDownloadError(f"manhuabika 浏览器清单不存在: {path}")
    if path.stat().st_size > MAX_HTML_BYTES:
        raise HtmlImageDownloadError("manhuabika 浏览器清单超过 5 MB 限制")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HtmlImageDownloadError("manhuabika 浏览器清单不是有效 JSON") from exc

    page_url, source_name, urls = _manhuabika_browser_manifest_urls(
        manifest,
        max_images=max_images,
    )
    return _download_url_list(
        urls,
        referer=page_url,
        source_value=str(path),
        source_name=source_name,
        output_dir=output_dir,
        scramble_context=None,
    )


def _manhuabika_api_request(
    api_domain: str,
    path: str,
    query: dict[str, object],
    nonce: str,
) -> dict[str, object]:
    query_string = urlencode(query)
    path_with_query = f"{path}?{query_string}" if query_string else path
    timestamp = str(int(time.time()))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.picacomic.com.v1+json",
        "app-channel": "1",
        "app-uuid": "webUUIDv2",
        "app-version": _MANHUABIKA_APP_VERSION,
        "app-platform": "android",
        "Content-Type": "application/json; charset=UTF-8",
        "time": timestamp,
        "nonce": nonce,
        "image-quality": "original",
        "signature": _manhuabika_signature(path_with_query, timestamp, nonce, "GET"),
    }
    request = urllib.request.Request(
        f"https://{api_domain}{path_with_query}",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            payload = response.read()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise HtmlImageDownloadError(f"manhuabika 页面接口请求失败: {exc}") from exc

    try:
        response_json = json.loads(payload)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise HtmlImageDownloadError("manhuabika 页面接口返回了无法解析的数据") from exc
    if not isinstance(response_json, dict):
        raise HtmlImageDownloadError("manhuabika 页面接口返回格式异常")
    return response_json


def _fetch_manhuabika_page_urls(
    page_url: str,
    *,
    comic_id: str,
    order: int,
    max_images: int,
) -> list[str]:
    """Resolve PicaWeb's JS-loaded page records into original image URLs."""
    limit = max(1, min(int(max_images or MAX_IMAGE_COUNT), MAX_IMAGE_COUNT))
    path = f"/comics/{comic_id}/order/{order}/pages"
    last_error = ""

    for api_domain in _MANHUABIKA_API_DOMAINS:
        nonce = _manhuabika_nonce()
        urls: list[str] = []
        api_page = 1
        try:
            while len(urls) < limit:
                response = _manhuabika_api_request(
                    api_domain,
                    path,
                    {"page": api_page},
                    nonce,
                )
                if response.get("code") not in (None, 200):
                    raise HtmlImageDownloadError(
                        f"manhuabika 页面接口返回错误码 {response.get('code')}"
                    )
                data = response.get("data")
                pages = data.get("pages") if isinstance(data, dict) else None
                docs = pages.get("docs") if isinstance(pages, dict) else None
                if not isinstance(docs, list) or not docs:
                    last_error = (
                        "页面接口没有返回图片记录（可能需要当前浏览器登录态；"
                        "下载器不会读取或复制浏览器凭据）"
                    )
                    break

                for doc in docs:
                    if not isinstance(doc, dict):
                        continue
                    media = doc.get("media") or doc
                    image_url = _manhuabika_media_url(media)
                    if image_url:
                        urls.append(image_url)
                        if len(urls) >= limit:
                            break

                total_pages = pages.get("pages") if isinstance(pages, dict) else None
                if len(urls) >= limit or (
                    isinstance(total_pages, int) and api_page >= total_pages
                ):
                    break
                api_page += 1

            if urls:
                return urls[:limit]
        except HtmlImageDownloadError as exc:
            last_error = str(exc)

    raise HtmlImageDownloadError(
        "manhuabika 阅读器没有解析出原图地址；请确认页面可正常阅读并选择“原图”模式"
        + (f"（{last_error}）" if last_error else "")
    )


def _download_manhuabika_reader_images(
    page_url: str,
    *,
    comic_id: str,
    order: int,
    max_images: int,
    output_dir: str | Path | None,
) -> HtmlImageDownloadResult:
    urls = _fetch_manhuabika_page_urls(
        page_url,
        comic_id=comic_id,
        order=order,
        max_images=max_images,
    )
    return _download_url_list(
        urls,
        referer=page_url,
        source_value=page_url,
        source_name=f"manhuabika-{comic_id}-{order}",
        output_dir=output_dir,
        scramble_context=None,
    )


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
    """Fetch an HTTP(S) page and download its referenced images.

    PicaWeb reader URLs are handled before the generic HTML path because their
    page records are loaded by JavaScript and are not present in the HTML
    shell. Other hosts retain the ordinary static-HTML behavior.
    """
    normalized_url = _validate_page_url(page_url)
    manhuabika_parts = _manhuabika_reader_parts(normalized_url)
    if manhuabika_parts:
        comic_id, order = manhuabika_parts
        return _download_manhuabika_reader_images(
            normalized_url,
            comic_id=comic_id,
            order=order,
            max_images=max_images,
            output_dir=output_dir,
        )
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
