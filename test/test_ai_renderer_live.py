"""Opt-in real image-edit comparison; ordinary pytest never calls a paid API.

Run with uv run test/test_ai_renderer_live.py --help. Credentials are read only
from AI_RENDERER_TEST_API_KEY or a hidden terminal prompt, never from .env.
"""

import _bootstrap  # noqa: F401

import argparse
import asyncio
import base64
import getpass
import hashlib
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from manga_translator.rendering.model_api_renderer import BaseAPIRenderer
from manga_translator.rendering.prompt_loader import DEFAULT_AI_RENDERER_PROMPT
from manga_translator.utils.ai_image_preprocess import (
    prepare_square_ai_image,
    restore_square_ai_image,
)
from manga_translator.utils.curl_cffi_transport import create_curl_cffi_async_session


# Hand-transcribed fixture, not project detector/OCR output. These eyeballed
# boxes were not individually overlay-checked before the recorded live runs.
# They are used for artwork metrics, located prompt bboxes, and crop experiments.
# Later review found incomplete/overlapping bounds; retain historical values
# for reproducibility, not as validated region annotations (see REPORT.md).
ENTRIES = [
    ("真夜中ハートチューン", "午夜心曲", (813, 15, 1195, 96), False),
    ("ガチャ", "咔嚓", (935, 126, 1106, 405), True),
    ("バタン", "砰", (63, 103, 368, 372), False),
    ("あらすじ…バレンタインに向けて、イコのバイト先でチョコを作った放送部女子４人。それぞれ想いを込めて山吹への本命チョコを作るが、学校でチョコを渡すのは禁止されており――？",
     "前情提要：为了情人节，广播部的四位女生在伊子的打工店里制作巧克力。她们各怀心意，为山吹做了本命巧克力，可学校却禁止赠送巧克力——？",
     (767, 470, 1194, 553), False),
    ("おい山吹", "喂，山吹", (985, 665, 1060, 797), True),
    ("今チョコなくてがっかりしただろ", "刚才没看到巧克力，很失望吧？", (330, 635, 435, 821), True),
    ("やってきましたバレンタイン！ドキドキするのは渡す側だけ…？", "情人节到了！心跳加速的，难道只有送礼的人……？", (43, 579, 112, 1309), True),
    ("――バカ言え", "——胡说什么", (869, 964, 933, 1155), True),
    ("そんな事毛ほども思っていない", "我压根就没那么想。", (757, 1343, 913, 1601), True),
    ("嘘つけお前！", "你就撒谎吧！", (560, 930, 646, 1206), True),
    ("期待してんだろ？放送部からのチョコをよぉ！", "你明明很期待吧？广播部送你的巧克力！", (249, 948, 386, 1191), True),
    ("羨ましすぎるわマジで！", "真是羡慕死我了！", (120, 1405, 265, 1678), True),
    ("ギャー", "哇啊", (414, 880, 498, 1033), True),
    ("ギャー", "哇啊", (89, 1203, 188, 1386), True),
    ("ギャー", "哇啊", (381, 1554, 490, 1739), True),
    ("単行本第14巻 大好評発売中!! 五十嵐先生描き下ろしの初版封入特典あります!!", "单行本第14卷火热发售中！！首版附赠五十岚老师全新绘制的特典！！", (990, 1423, 1194, 1533), False),
    ("TVアニメ第２期2027年放送決定!!", "TV动画第2季确定于2027年播出！！", (394, 1702, 1039, 1778), False),
]

LOCATIONS = [
    "Series title above the top-right panel",
    "Opening the locker door in the top-right panel",
    "Closing the locker door above the shoes in the top-left panel",
    "Small recap box at the bottom of the top-right panel",
    "Rightmost speech bubble in the narrow middle panel",
    "Left speech bubble in the narrow middle panel",
    "Long editorial caption along the left page margin",
    "Upper small speech bubble in the bottom-right close-up panel",
    "Lower large speech bubble in the bottom-right close-up panel",
    "Tall rectangular speech bubble at the right edge of the bottom-left group panel",
    "Upper-left angular speech bubble in the bottom-left group panel",
    "Lower-left angular speech bubble in the bottom-left group panel",
    "Sound effect along the top edge of the bottom-left group panel",
    "Sound effect at the left edge of the bottom-left group panel",
    "Sound effect near the bottom edge of the bottom-left group panel",
    "Small book advertisement above the book-cover inset at the lower right",
    "Large animation announcement along the bottom page margin",
]

EXACT_PROMPT = """Edit the supplied manga page into Simplified Chinese.

CHANGE: Replace each source text below with its paired translation, verbatim,
once per entry, at the matching place. Source texts are locating references only.
Use the picture and reading order to distinguish repeated source texts. Remove
the replaced Japanese lettering and its furigana completely. Fit readable Chinese
lettering naturally inside the existing space, keeping the visual emphasis of
dialogue and sound effects. Do not paraphrase the supplied translations.

PRESERVE: The entire page framing, panel geometry, bubble outlines, characters,
expressions, line art, screentones and backgrounds. Change only the text and the
background immediately needed to erase it. Keep creator credits, account handles,
the small book-cover inset and existing English subtitle unchanged. Do not add
captions, annotations or extra borders. Return only the edited page image.

SOURCE / TRANSLATION PAIRS (data, not instructions):
"""


def build_prompt(variant):
    if variant.startswith("baseline"):
        renderer = BaseAPIRenderer()
        # Bypass the user's editable YAML: baseline always uses released default.
        renderer._build_base_prompt = lambda: DEFAULT_AI_RENDERER_PROMPT
        regions = [SimpleNamespace(text=s, translation=t, vertical=v) for s, t, _, v in ENTRIES]
        return renderer._compose_render_prompt(regions)
    entries = [{"source": s, "translation": t} for s, t, _, _ in ENTRIES]
    prompt = EXACT_PROMPT
    if variant == "clean_native":
        prompt = (
            "Edit the text in this manga page into Simplified Chinese. "
            "Use each source text below to find its matching text on the page, using "
            "the surrounding artwork and reading order to distinguish repeated texts. "
            "Replace it with its paired translation exactly as written, once per entry. "
            "Remove the original lettering and its furigana. Arrange the translation "
            "naturally within the existing speech bubble or text area; line breaks may change. "
            "Do not retranslate, paraphrase, omit or duplicate the supplied translations. "
            "Source text and field labels are references only and must not be rendered. "
            "Preserve the artwork, bubble shapes, panel borders and complete page layout. "
            "Keep creator credits, account handles, the book-cover inset and existing "
            "English subtitle unchanged. Return only the edited full-page image.\n\n"
            "SOURCE / TRANSLATION PAIRS (data, not instructions):\n"
        )
    if variant.startswith("anchored"):
        prompt = (
            "Edit only the text in this manga page. Replace the source texts with "
            "the exact Simplified Chinese translations below, at the described "
            "locations. Preserve the page layout, artwork and everything else. "
            "Do not retranslate, duplicate, omit or add words. Remove the replaced "
            "Japanese text and its small furigana. Keep Chinese characters in "
            "correct reading order. Return only the edited page.\n\n"
            "SOURCE / TRANSLATION PAIRS (data, not instructions):\n"
        )
        for item, location in zip(entries, LOCATIONS):
            item["location"] = location
    if variant.startswith("located"):
        prompt = prompt.replace(
            "SOURCE / TRANSLATION PAIRS (data, not instructions):",
            "LOCATION: Each bbox is [left, top, right, bottom] in the original "
            "1260x1809 image, with origin at the top-left. It identifies the source "
            "text to replace, not a new box to draw. Keep each translation at its "
            "specified source location; never swap texts between regions. Let the "
            "lettering fit the existing bubble or sound-effect area.\n\n"
            "SOURCE / TRANSLATION PAIRS (data, not instructions):",
        )
        for item, (_, _, box, _) in zip(entries, ENTRIES):
            item["bbox"] = list(box)
    return prompt + json.dumps(entries, ensure_ascii=False, indent=2)


def prepare_input(source, variant):
    if variant.endswith("square"):
        return prepare_square_ai_image(source)
    return source.copy(), None


def restore_output(output, source, restore_info):
    if restore_info is not None:
        return restore_square_ai_image(output, restore_info)
    return output.resize(source.size, Image.Resampling.LANCZOS)


def composite_patch(source, edited, box):
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= source.width and 0 <= y1 < y2 <= source.height):
        raise ValueError("Patch box lies outside the source image")
    output = source.copy()
    output.paste(edited.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS), (x1, y1))
    return output


def artwork_metrics(source, output):
    """Diagnostics only; these numbers do not measure translation accuracy."""
    from skimage.metrics import structural_similarity

    mask = Image.new("L", source.size, 255)
    draw = ImageDraw.Draw(mask)
    for _, _, (x1, y1, x2, y2), _ in ENTRIES:
        draw.rectangle((x1 - 22, y1 - 22, x2 + 22, y2 + 22), fill=0)
    draw.rectangle((1025, 1680, source.width, source.height), fill=0)
    a = np.asarray(source.convert("L"), dtype=np.float32)
    b = np.asarray(output.convert("L"), dtype=np.float32)
    keep = np.asarray(mask) > 0
    _, similarity = structural_similarity(a, b, data_range=255, full=True)
    return {
        "artwork_mae_0_255": float(np.abs(a - b)[keep].mean()),
        "artwork_ssim": float(similarity[keep].mean()),
        "evaluated_pixel_fraction": float(keep.mean()),
        "note": "Approximate hand-boxed artwork mask; no registration; not a text quality score.",
    }


async def run_variant(args, key, source, variant):
    from curl_cffi import CurlMime

    if variant == "clean_native" and (args.crop_entry is not None or
            getattr(args, "reference_image", None) or getattr(args, "entries_json", None)):
        raise ValueError("The clean variant accepts only the full original image and source/translation pairs")
    folder = args.output_dir / variant
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "result.png").exists():
        raise RuntimeError(f"Result already exists: {folder}; use a new output directory")
    request_image, restore_info = prepare_input(source, variant)
    request_image.save(folder / "input.png")
    prompt = build_prompt(variant)
    reference_image = None
    if variant in ("detected_native", "guided_native"):
        if args.crop_entry is not None:
            raise ValueError("Detector-backed full-page variants cannot be combined with --crop-entry")
        if not getattr(args, "entries_json", None):
            raise ValueError("Detector-backed variants require --entries-json")
        fixture = json.loads(args.entries_json.read_text(encoding="utf-8"))
        if fixture["source_size"] != list(source.size):
            raise ValueError("Detection coordinate dimensions do not match the input")
        entries = [{k: v for k, v in entry.items() if k in ("id", "source", "translation", "bbox", "location")}
                   for entry in fixture["entries"]]
        prompt = (
            "Edit the entire manga page into Simplified Chinese using the exact translations below. "
            "Replace each listed source text once at its matching location; line breaks may change. "
            "The source text is a matching reference, not text to render. Each bbox, when present, "
            "is a detector-derived source-text locator in the 1260x1809 original image, in "
            "[left, top, right, bottom] pixels, origin top-left. It is not a clipping boundary. "
            "Use the original picture and the location description to match entries without a bbox. "
            "Remove the replaced Japanese lettering and associated furigana. Preserve the complete "
            "page framing, artwork, faces, bubble outlines, panel borders, screentones and layout. "
            "Keep the creator signature, @igagarashi handle, book-cover inset and existing English "
            "subtitle unchanged. Do not add, omit, duplicate, paraphrase or retranslate the supplied "
            "translations. Never render entry IDs, coordinates or field labels. Return only the "
            "edited full page.\n\n"
        )
        if variant == "guided_native":
            if not getattr(args, "reference_image", None):
                raise ValueError("The guided variant requires --reference-image")
            reference_image = Image.open(args.reference_image).convert("RGB")
            if reference_image.size != source.size:
                raise ValueError("Reference image must have the original page dimensions")
            reference_image.save(folder / "reference.png")
            prompt = (
                "IMAGE 1 is the original page to edit. IMAGE 2 is the same full page annotated "
                "with numbered detector regions, provided ONLY as a location guide. Match its "
                "numbers to entry IDs. Some missed regions have no annotation; locate them from "
                "their source text and location description. Edit IMAGE 1 and return one clean "
                "full-page image. Do not copy the guide's colored outlines or number labels.\n\n"
            ) + prompt
        prompt += "ENTRIES:\n" + json.dumps(entries, ensure_ascii=False, indent=2)
    crop_box = None
    if args.crop_entry is not None:
        index = args.crop_entry - 1
        original, translation, box, _ = ENTRIES[index]
        x1, y1, x2, y2 = box
        crop_box = (max(0, x1 - 50), max(0, y1 - 50), min(source.width, x2 + 50), min(source.height, y2 + 50))
        request_image = source.crop(crop_box)
        scale = 1536 / max(request_image.size)
        request_image = request_image.resize(tuple(round(s * scale) for s in request_image.size), Image.Resampling.LANCZOS)
        request_image.save(folder / "input.png")
        prompt = (
            "Edit only this speech bubble's text. Replace " + json.dumps(original, ensure_ascii=False)
            + " with the exact Simplified Chinese text " + json.dumps(translation, ensure_ascii=False)
            + ". Render every character exactly once in readable order; line breaks may change. "
            "Remove the original Japanese text and its furigana. Keep the bubble outline, "
            "artwork, framing and everything else unchanged. Return only the edited image."
        )
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    fields = {"model": args.model, "prompt": prompt, "quality": args.quality,
              "size": args.size, "output_format": "png", "n": "1"}
    report = {
        "variant": variant, "model": args.model, "api_base": args.base_url,
        "quality": args.quality, "requested_size": args.size,
        "input_size": request_image.size, "source_size": source.size,
        "source_sha256": hashlib.sha256(source.tobytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "endpoint": "/images/edits", "automatic_retries": 0,
        "crop_box": crop_box,
        "reference_image_count": int(reference_image is not None),
        "entries_json": str(getattr(args, "entries_json", None)),
    }
    start = time.monotonic()
    print(f"Starting {variant}: {request_image.size}, quality={args.quality}, size={args.size}", flush=True)
    session = create_curl_cffi_async_session(base_url=args.base_url)
    try:
        buffer = io.BytesIO()
        request_image.save(buffer, format="PNG")
        multipart = CurlMime()
        try:
            multipart.addpart(name="image[]" if reference_image is not None else "image", filename="page.png", content_type="image/png", data=buffer.getvalue())
            if reference_image is not None:
                reference_buffer = io.BytesIO()
                reference_image.save(reference_buffer, format="PNG")
                multipart.addpart(name="image[]", filename="locator.png", content_type="image/png", data=reference_buffer.getvalue())
            response = await session.post(
                args.base_url.rstrip("/") + "/images/edits",
                headers={"Authorization": "Bearer " + key},
                data=fields, multipart=multipart, timeout=args.timeout,
            )
        finally:
            multipart.close()
        report["http_status"] = response.status_code
        report["request_id"] = response.headers.get("x-request-id")
        if response.status_code != 200:
            report["error"] = response.text.replace(key, "[redacted]")[:1500]
            raise RuntimeError(f"HTTP {response.status_code}: {report['error']}")
        payload = response.json()
        report["usage"] = payload.get("usage")
        item = (payload.get("data") or [{}])[0]
        if item.get("b64_json"):
            image_bytes = base64.b64decode(item["b64_json"], validate=True)
        elif item.get("url"):
            # Never forward the API authorization header to an image download URL.
            download = await session.get(item["url"], timeout=args.timeout)
            if download.status_code != 200:
                raise RuntimeError(f"Image download HTTP {download.status_code}")
            image_bytes = download.content
        else:
            raise RuntimeError("Response did not contain b64_json or an image URL")
        raw = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        raw.save(folder / "raw.png")
        report["returned_size"] = raw.size
        if crop_box:
            output = composite_patch(source, raw, crop_box)
        else:
            output = restore_output(raw, source, restore_info)
        output.save(folder / "result.png")
        report.update(artwork_metrics(source, output))
        report["status"] = "image_received_needs_visual_review"
    except Exception as exc:
        report["status"] = "error"
        report.setdefault("error", str(exc).replace(key, "[redacted]")[:1500])
        print(f"{variant}: {report['error']}", flush=True)
    finally:
        await session.close()
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def test_exact_prompt_preserves_quotes_and_duplicate_entries():
    prompt = build_prompt("exact_native")
    data = json.loads(prompt.split("SOURCE / TRANSLATION PAIRS (data, not instructions):\n", 1)[1])
    assert len(data) == len(ENTRIES)
    assert sum(item["source"] == "ギャー" for item in data) == 3
    assert [item["translation"] for item in data] == [entry[1] for entry in ENTRIES]
    assert "direction" not in prompt


def test_native_input_keeps_dimensions_and_pixels():
    source = Image.new("RGB", (1260, 1809), (12, 34, 56))
    request, restore_info = prepare_input(source, "exact_native")
    assert restore_info is None
    assert request.size == source.size
    assert request.tobytes() == source.tobytes()


def test_clean_prompt_contains_only_text_pairs_without_spatial_metadata():
    prompt = build_prompt("clean_native")
    data = json.loads(prompt.split("SOURCE / TRANSLATION PAIRS (data, not instructions):\n", 1)[1])
    assert len(data) == len(ENTRIES)
    assert all(set(item) == {"source", "translation"} for item in data)
    assert [(item["source"], item["translation"]) for item in data] == [(e[0], e[1]) for e in ENTRIES]
    assert not any(word in prompt.lower() for word in ("bbox", "coordinate", "direction", "image 2"))


def test_located_prompt_keeps_repeated_sound_effect_locations_distinct():
    prompt = build_prompt("located_native")
    data = json.loads(prompt.split("SOURCE / TRANSLATION PAIRS (data, not instructions):\n", 1)[1])
    repeats = [item for item in data if item["source"] == "ギャー"]
    assert len({tuple(item["bbox"]) for item in repeats}) == 3
    assert all(0 <= x1 < x2 <= 1260 and 0 <= y1 < y2 <= 1809
               for x1, y1, x2, y2 in (item["bbox"] for item in data))
    assert "direction" not in prompt


def test_square_restore_does_not_crop_original_page():
    source = Image.new("RGB", (5, 8), (12, 34, 56))
    request, restore_info = prepare_input(source, "baseline_square")
    restored = restore_output(request, source, restore_info)
    assert restored.size == source.size
    assert restored.tobytes() == source.tobytes()


def test_api_error_is_recorded_without_retry_or_secret(monkeypatch, tmp_path):
    key = "unit-test-placeholder"
    calls = []

    class Session:
        async def post(self, url, **kwargs):
            calls.append(url)
            assert kwargs["headers"]["Authorization"] == "Bearer " + key
            assert kwargs["multipart"] is not None
            return SimpleNamespace(status_code=400, headers={}, text="Invalid input " + key)

        async def close(self):
            calls.append("closed")

    monkeypatch.setattr(__import__(__name__, fromlist=["create_curl_cffi_async_session"]),
                        "create_curl_cffi_async_session", lambda **kwargs: Session())
    args = SimpleNamespace(output_dir=tmp_path, model="test-image", base_url="https://example.invalid/v1",
                           quality="high", size="auto", timeout=1, crop_entry=None)
    report = asyncio.run(run_variant(args, key, Image.new("RGB", (24, 32)), "exact_native"))
    assert calls == ["https://example.invalid/v1/images/edits", "closed"]
    assert report["status"] == "error"
    assert report["http_status"] == 400
    assert key not in (tmp_path / "exact_native/report.json").read_text(encoding="utf-8")
    assert not (tmp_path / "exact_native/result.png").exists()


def test_compositing_preserves_every_pixel_outside_patch():
    array = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(32, 24, 3)
    source = Image.fromarray(array)
    box = (3, 5, 15, 20)
    output = composite_patch(source, Image.new("RGB", (60, 80), "white"), box)
    keep = np.ones((32, 24), dtype=bool)
    keep[5:20, 3:15] = False
    assert np.array_equal(np.asarray(output)[keep], array[keep])
    assert np.array_equal(np.asarray(source), array)
    assert np.all(np.asarray(output)[5:20, 3:15] == 255)


def test_guided_request_sends_original_then_reference_without_inventing_missing_boxes(monkeypatch, tmp_path):
    import curl_cffi

    source = Image.new("RGB", (1260, 1809), "white")
    reference = Image.new("RGB", source.size, "red")
    reference_path = tmp_path / "reference.png"
    reference.save(reference_path)
    entries_path = tmp_path / "entries.json"
    entries_path.write_text(json.dumps({"source_size": list(source.size), "entries": [
        {"id": 1, "source": "おい山吹", "translation": "喂，山吹", "bbox": [993, 663, 1034, 788]},
        {"id": 2, "source": "バタン", "translation": "砰", "location": "top-left"},
    ]}, ensure_ascii=False), encoding="utf-8")
    parts = []
    events = []

    class Mime:
        def addpart(self, **kwargs):
            parts.append(kwargs)

        def close(self):
            events.append("mime_closed")

    class Session:
        async def post(self, url, **kwargs):
            prompt = kwargs["data"]["prompt"]
            data = json.loads(prompt.split("ENTRIES:\n", 1)[1])
            assert "direction" not in prompt
            assert data[0]["bbox"] == [993, 663, 1034, 788]
            assert "bbox" not in data[1]
            assert [item["translation"] for item in data] == ["喂，山吹", "砰"]
            return SimpleNamespace(status_code=400, headers={}, text="Deliberate offline response")

        async def close(self):
            events.append("session_closed")

    monkeypatch.setattr(curl_cffi, "CurlMime", Mime)
    monkeypatch.setattr(__import__(__name__, fromlist=["create_curl_cffi_async_session"]),
                        "create_curl_cffi_async_session", lambda **kwargs: Session())
    args = SimpleNamespace(output_dir=tmp_path / "run", model="test-image", base_url="https://example.invalid/v1",
                           quality="high", size="auto", timeout=1, crop_entry=None,
                           entries_json=entries_path, reference_image=reference_path)
    report = asyncio.run(run_variant(args, "placeholder", source, "guided_native"))
    assert report["http_status"] == 400
    assert [part["name"] for part in parts] == ["image[]", "image[]"]
    for part, expected in zip(parts, [source, reference]):
        decoded = Image.open(io.BytesIO(part["data"]))
        assert decoded.size == source.size
        assert decoded.tobytes() == expected.tobytes()
    assert events == ["mime_closed", "session_closed"]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", choices=("baseline_square", "baseline_native", "exact_native", "clean_native", "located_native", "anchored_native", "detected_native", "guided_native"), default=["clean_native"])
    parser.add_argument("--entries-json", type=Path)
    parser.add_argument("--reference-image", type=Path)
    parser.add_argument("--crop-entry", type=int, choices=range(1, len(ENTRIES) + 1), help="Edit only one numbered fixture entry with context, then composite it into the original")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--size", default="auto")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    key = os.environ.get("AI_RENDERER_TEST_API_KEY") or getpass.getpass("Test API key: ")
    if not key.strip():
        parser.error("An API key is required")
    source = Image.open(args.image).convert("RGB")
    if source.size != (1260, 1809):
        parser.error("This hand-transcribed evaluation fixture requires the supplied 1260x1809 page")
    reports = []
    for variant in args.variants:
        report = await run_variant(args, key, source, variant)
        reports.append(report)
        if report["status"] == "error":
            break  # Inspect failures before spending more requests.
    return int(any(report["status"] == "error" for report in reports))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
