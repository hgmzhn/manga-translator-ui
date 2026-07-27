from PIL import Image

from manga_translator.translators.common import draw_text_boxes_on_image
from manga_translator.translators.gemini_hq import encode_image_for_gemini


class _Region:
    xyxy = (2, 2, 12, 12)


def test_prepare_annotated_gemini_image_accepts_pil_image():
    image = Image.new("RGB", (20, 20), "white")

    annotated = draw_text_boxes_on_image(image, [_Region()], [1])
    image_bytes, mime_type = encode_image_for_gemini(annotated)

    assert isinstance(annotated, Image.Image)
    assert isinstance(image_bytes, bytes)
    assert image_bytes.startswith(b"\xff\xd8")
    assert mime_type == "image/jpeg"
