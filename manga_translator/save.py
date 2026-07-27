import os

from PIL import Image

from .image_formats import OUTPUT_IMAGE_FORMATS
from .utils import Context
from .utils.generic import save_pil_image


class FormatNotSupportedException(Exception):
    def __init__(self, fmt: str):
        super().__init__(f"Format {fmt} is not supported.")


def save_result(result: Image.Image, dest: str, ctx: Context):
    extension = os.path.splitext(dest)[1].removeprefix(".").lower()
    if extension not in OUTPUT_IMAGE_FORMATS:
        raise FormatNotSupportedException(extension)
    save_pil_image(result, dest, quality=ctx.save_quality)
