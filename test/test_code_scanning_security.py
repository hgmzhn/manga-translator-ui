import asyncio
import base64
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from PIL import Image

from manga_translator import Config
from manga_translator.api_key_rotation import make_endpoint_status_key
from manga_translator.mode.share import _decode_attributes
from manga_translator.server.core.download_ticket_service import resolve_path_within
from manga_translator.server.request_extraction import to_pil_image
from manga_translator.server.sent_data_internal import _encode_attributes
from manga_translator.translators.common import parse_hq_response


def _data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_remote_image_urls_are_rejected_without_fetching():
    try:
        asyncio.run(to_pil_image("http://127.0.0.1/private.png"))
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("remote image URL was accepted")


def test_data_uri_and_shared_json_image_round_trip():
    source = Image.new("RGB", (2, 3), "red")
    decoded = asyncio.run(to_pil_image(_data_uri(source)))
    assert decoded.size == source.size

    attributes = _decode_attributes("translate", _encode_attributes(source, Config()))
    assert attributes["image"].size == source.size
    assert isinstance(attributes["config"], Config)

    batch = _decode_attributes(
        "translate_batch",
        _encode_attributes({"images": [source], "config": Config(), "batch_size": 1}, None),
    )
    assert len(batch["images_with_configs"]) == 1
    assert batch["batch_size"] == 1

    decoded.close()
    attributes["image"].close()
    batch["images_with_configs"][0][0].close()
    source.close()


def test_api_key_status_fingerprint_is_keyed_and_stable():
    first = make_endpoint_status_key("ocr", "openai", 1, "https://example.com", "model", "secret-a")
    second = make_endpoint_status_key("ocr", "openai", 1, "https://example.com", "model", "secret-a")
    other = make_endpoint_status_key("ocr", "openai", 1, "https://example.com", "model", "secret-b")
    assert first == second
    assert first != other
    assert "secret-a" not in first


def test_resolve_path_within_rejects_traversal():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "root"
        root.mkdir()
        assert resolve_path_within(root, root / "file.txt").parent == resolve_path_within(root.parent, root)
        try:
            resolve_path_within(root, root.parent / "escape.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal was accepted")


def test_hq_regex_fallback_handles_escaped_quotes():
    translations, terms = parse_hq_response('prefix "translation": "hello\\\"world" suffix')
    assert translations == ['hello"world']
    assert terms == []


def main() -> int:
    test_remote_image_urls_are_rejected_without_fetching()
    test_data_uri_and_shared_json_image_round_trip()
    test_api_key_status_fingerprint_is_keyed_and_stable()
    test_resolve_path_within_rejects_traversal()
    test_hq_regex_fallback_handles_escaped_quotes()
    print("5 security regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
