from __future__ import annotations

import sys
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from desktop_qt_ui.services import html_image_download_service as service
from extract_html_image_urls import extract_image_urls_from_html
from manga_translator.webp_scramble import (
    WebpScrambleContext,
    is_scrambled_chapter_url,
    restore_scrambled_webp,
    stripe_count,
)
from PIL import Image
from io import BytesIO


class _FakeHtmlResponse:
    status = 200

    def __init__(self, body: bytes, final_url: str):
        self._body = body
        self._final_url = final_url
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _size=-1):
        body, self._body = self._body, b""
        return body

    def geturl(self):
        return self._final_url


def test_url_import_resolves_relative_images_and_uses_output_dir(monkeypatch, tmp_path):
    html = b'<html><body><img src="/images/page.png"></body></html>'
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return _FakeHtmlResponse(html, "https://reader.example/chapters/1")

    downloaded_urls = []

    def fake_download_one(index, url, referer):
        downloaded_urls.append((index, url, referer))
        return index, b"fake image", ".png"

    monkeypatch.setattr(service.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(service, "_download_one", fake_download_one)

    result = service.download_html_images_from_url(
        "reader.example/chapters/1",
        output_dir=tmp_path,
    )

    assert requests == [("https://reader.example/chapters/1", service.DOWNLOAD_TIMEOUT)]
    assert downloaded_urls == [
        (1, "https://reader.example/images/page.png", "https://reader.example/chapters/1")
    ]
    assert result.html_path == "https://reader.example/chapters/1"
    assert result.extracted_count == 1
    assert len(result.image_paths) == 1
    assert Path(result.image_paths[0]).read_bytes() == b"fake image"
    assert Path(result.output_dir).parent == tmp_path.resolve()


def test_local_html_import_without_output_dir_keeps_sibling_output(monkeypatch, tmp_path):
    html_path = tmp_path / "chapter.html"
    html_path.write_text('<img src="page.png">', encoding="utf-8")

    monkeypatch.setattr(
        service,
        "_download_one",
        lambda index, _url, _referer: (index, b"local image", ".png"),
    )

    result = service.download_html_images(html_path)

    assert Path(result.output_dir).parent == tmp_path.resolve()
    assert Path(result.image_paths[0]).read_bytes() == b"local image"


def test_relative_canonical_url_is_resolved_against_page_url():
    html = (
        '<link rel="canonical" href="../chapter-2">'
        '<img src="images/page.png">'
    )

    assert extract_image_urls_from_html(
        html,
        base_url="https://reader.example/chapters/1/",
    ) == ["https://reader.example/chapters/images/page.png"]


def test_manhuabika_reader_url_is_scoped_to_the_supported_host():
    assert service._manhuabika_reader_parts(
        "https://manhuabika.com/comic/reader/6aa96114e1dbce1e11ce5ff4/1"
    ) == ("6aa96114e1dbce1e11ce5ff4", 1)
    assert service._manhuabika_reader_parts(
        "https://other.example/comic/reader/6aa96114e1dbce1e11ce5ff4/1"
    ) is None
    assert service._manhuabika_reader_parts(
        "https://manhuabika.com/comic/6aa96114e1dbce1e11ce5ff4"
    ) is None


def test_manhuabika_media_path_prefers_the_original_tobs_object():
    assert service._manhuabika_media_url(
        {
            "fileServer": "https://storage-b.picacomic.com",
            "path": "sub_storage_1/53/c5/page.jpg",
        }
    ) == "https://storage-b.picacomic.com/static/tobs/sub_storage_1/53/c5/page.jpg"


def test_manhuabika_browser_manifest_is_scoped_and_sorted(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manhuabika-browser-manifest.json"
    manifest_path.write_text(
        """{
          "schema": "manhuabika-browser-manifest/v1",
          "page_url": "https://manhuabika.com/comic/reader/comic-1/1",
          "pages": {
            "002": "https://storage-b.picacomic.com/static/tobs/sub_storage_1/b/page.jpg",
            "001": "https://storage-b.picacomic.com/static/tobs/sub_storage_1/a/page.jpg",
            "003": "https://storage-b.picacomic.com/static/tobeimg/proxy.jpg",
            "004": "https://other.example/static/tobs/page.jpg"
          }
        }""",
        encoding="utf-8",
    )
    captured = {}

    def fake_download(urls, **kwargs):
        captured["urls"] = urls
        captured["kwargs"] = kwargs
        return service.HtmlImageDownloadResult(
            html_path=kwargs["source_value"],
            output_dir=str(tmp_path),
            image_paths=(),
            extracted_count=len(urls),
            failures=(),
        )

    monkeypatch.setattr(service, "_download_url_list", fake_download)

    result = service.download_manhuabika_browser_manifest(manifest_path)

    assert result.extracted_count == 2
    assert captured["urls"] == [
        "https://storage-b.picacomic.com/static/tobs/sub_storage_1/a/page.jpg",
        "https://storage-b.picacomic.com/static/tobs/sub_storage_1/b/page.jpg",
    ]
    assert captured["kwargs"]["referer"].startswith("https://manhuabika.com/")
    assert captured["kwargs"]["scramble_context"] is None


def test_manhuabika_browser_manifest_rejects_wrong_reader_url(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """{
          "schema": "manhuabika-browser-manifest/v1",
          "page_url": "https://other.example/comic/reader/comic-1/1",
          "pages": {"001": "https://storage-b.picacomic.com/static/tobs/page.jpg"}
        }""",
        encoding="utf-8",
    )

    with pytest.raises(service.HtmlImageDownloadError, match="受支持的阅读器网址"):
        service.download_manhuabika_browser_manifest(manifest_path)


def test_manhuabika_page_api_paginates_and_returns_original_urls(monkeypatch):
    calls = []

    def fake_api_request(domain, path, query, nonce):
        calls.append((domain, path, query, nonce))
        return {
            "code": 200,
            "data": {
                "pages": {
                    "docs": [
                        {
                            "media": {
                                "fileServer": "https://storage-b.picacomic.com",
                                "path": (
                                    "tobs/sub_storage_1/53/c5/"
                                    "53c53695-e30b-4ba5-9c8c-7c4914188c35.jpg"
                                ),
                            }
                        }
                    ],
                    "pages": 1,
                }
            },
        }

    monkeypatch.setattr(service, "_manhuabika_api_request", fake_api_request)

    urls = service._fetch_manhuabika_page_urls(
        "https://manhuabika.com/comic/reader/6aa96114e1dbce1e11ce5ff4/1",
        comic_id="6aa96114e1dbce1e11ce5ff4",
        order=1,
        max_images=10,
    )

    assert urls == [
        "https://storage-b.picacomic.com/static/tobs/sub_storage_1/53/c5/"
        "53c53695-e30b-4ba5-9c8c-7c4914188c35.jpg"
    ]
    assert calls[0][0] == "picaapi.go2778.com"
    assert calls[0][1] == "/comics/6aa96114e1dbce1e11ce5ff4/order/1/pages"
    assert calls[0][2] == {"page": 1}
    assert len(calls[0][3]) == 32


def test_manhuabika_reader_route_does_not_fetch_static_html(monkeypatch, tmp_path):
    image_urls = ["https://storage-b.picacomic.com/static/tobs/page.jpg"]
    captured = {}

    def fake_resolve(page_url, *, comic_id, order, max_images):
        captured["resolve"] = (page_url, comic_id, order, max_images)
        return image_urls

    def fake_download(urls, **kwargs):
        captured["download"] = (urls, kwargs)
        return service.HtmlImageDownloadResult(
            html_path=kwargs["source_value"],
            output_dir=str(tmp_path),
            image_paths=(str(tmp_path / "page.jpg"),),
            extracted_count=len(urls),
            failures=(),
        )

    monkeypatch.setattr(service, "_fetch_manhuabika_page_urls", fake_resolve)
    monkeypatch.setattr(service, "_download_url_list", fake_download)
    monkeypatch.setattr(
        service,
        "_fetch_html_from_url",
        lambda _url: pytest.fail("manhuabika should not use static HTML fetching"),
    )

    result = service.download_html_images_from_url(
        "https://manhuabika.com/comic/reader/6aa96114e1dbce1e11ce5ff4/1",
        output_dir=tmp_path,
    )

    assert result.extracted_count == 1
    assert captured["resolve"][1:] == ("6aa96114e1dbce1e11ce5ff4", 1, 200)
    assert captured["download"][0] == image_urls


def test_18comic_page_array_selects_chapter_images_in_order():
    html = """
    <script>var page_arr = [\"00002.webp\", \"00001.webp\"];</script>
    <img src="https://cdn.example/media/photos/123/00001.webp">
    <img src="https://cdn.example/ad/banner.webp">
    <img src="https://cdn.example/media/photos/123/00002.webp">
    """

    assert extract_image_urls_from_html(html) == [
        "https://cdn.example/media/photos/123/00002.webp",
        "https://cdn.example/media/photos/123/00001.webp",
    ]


def test_18comic_strip_restore_matches_the_original_pixels():
    aid = 1468668
    page_key = "00001"
    width, height = 7, 29
    original = Image.new("RGB", (width, height))
    pixels = original.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ((y * 7) % 256, (x * 31) % 256, (y * 11 + x) % 256)

    count = stripe_count(aid, page_key)
    scrambled = Image.new("RGB", original.size)
    base_height, remainder = divmod(height, count)
    first_height = base_height + remainder
    scrambled.paste(original.crop((0, 0, width, first_height)), (0, height - first_height))
    for destination_index in range(1, count):
        source_top = remainder + destination_index * base_height
        scrambled.paste(
            original.crop((0, source_top, width, source_top + base_height)),
            (0, height - base_height * (destination_index + 1) - remainder),
        )

    encoded = BytesIO()
    scrambled.save(encoded, format="WEBP", lossless=True)
    restored = restore_scrambled_webp(
        encoded.getvalue(),
        aid=aid,
        page_key=page_key,
    )
    with Image.open(BytesIO(restored)) as decoded:
        assert decoded.size == (width, height)
        assert list(decoded.convert("RGB").getdata()) == list(original.getdata())


def test_strip_restore_is_scoped_to_18comic_hosts_and_photo_paths():
    context = WebpScrambleContext(aid=1468668, scramble_id=220980)

    assert is_scrambled_chapter_url(
        "https://cdn-msp.18comic.vip/media/photos/1468668/00001.webp",
        context,
    )
    assert not is_scrambled_chapter_url(
        "https://other.example/media/photos/1468668/00001.webp",
        context,
    )
    assert not is_scrambled_chapter_url(
        "https://cdn-msp.18comic.vip/assets/1468668/00001.webp",
        context,
    )


@pytest.mark.parametrize(
    "page_url",
    ["file:///tmp/chapter.html", "ftp://reader.example/chapter", "https://user:pass@example.com"],
)
def test_url_import_rejects_non_http_or_credential_urls(page_url):
    with pytest.raises(service.HtmlImageDownloadError):
        service.download_html_images_from_url(page_url)


def test_18comic_url_falls_back_to_cdn_when_page_html_is_forbidden(
    monkeypatch, tmp_path
):
    image_buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(image_buffer, format="WEBP")
    image_data = image_buffer.getvalue()

    def forbidden(_url):
        raise service.HtmlImageDownloadError("网址加载失败: HTTP Error 403: Forbidden")

    def fake_download_one(index, url, _referer):
        if url.endswith("00001.webp") or url.endswith("00002.webp"):
            return index, image_data, ".webp"
        return index, None, "HTTP 404"

    monkeypatch.setattr(service, "_fetch_html_from_url", forbidden)
    monkeypatch.setattr(service, "_download_one", fake_download_one)

    result = service.download_html_images_from_url(
        "https://18comic.vip/photo/1468668",
        max_images=10,
        output_dir=tmp_path,
    )

    assert result.extracted_count == 2
    assert len(result.image_paths) == 2
    assert result.failures == ()


def test_forbidden_non_18comic_url_does_not_use_the_cdn_fallback(monkeypatch):
    error = service.HtmlImageDownloadError("网址加载失败: HTTP Error 403: Forbidden")

    def forbidden(_url):
        raise error

    monkeypatch.setattr(service, "_fetch_html_from_url", forbidden)

    with pytest.raises(service.HtmlImageDownloadError) as caught:
        service.download_html_images_from_url("https://reader.example/photo/1468668")

    assert caught.value is error
