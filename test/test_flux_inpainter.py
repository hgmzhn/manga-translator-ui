import _bootstrap  # noqa: F401

import asyncio

import numpy as np

from manga_translator.config import Inpainter, InpainterConfig
from manga_translator.inpainting import INPAINTERS
from manga_translator.inpainting.inpainting_flux import Flux2KleinInpainter


def test_flux_inpainter_is_registered():
    assert Inpainter.flux2_klein.value == "flux2-klein"
    assert INPAINTERS[Inpainter.flux2_klein] is Flux2KleinInpainter


def test_flux_resize_keeps_model_alignment():
    image = np.zeros((33, 65, 3), dtype=np.uint8)
    mask = np.zeros((33, 65), dtype=np.uint8)
    resized_image, resized_mask = Flux2KleinInpainter._resize_inputs(image, mask, 64)

    assert resized_image.shape[:2] == (32, 64)
    assert resized_mask.shape == (32, 64)


def test_flux_inpaint_preserves_unmasked_pixels():
    inpainter = Flux2KleinInpainter()
    inpainter.prompt_embeds = object()
    original = np.full((32, 48, 3), 17, dtype=np.uint8)
    mask = np.zeros((32, 48), dtype=np.uint8)
    mask[8:24, 12:36] = 255

    def fake_pipeline(**kwargs):
        generated = np.ones((kwargs["height"], kwargs["width"], 3), dtype=np.float32)
        return (np.asarray([generated]),)

    inpainter.pipeline = fake_pipeline
    result = asyncio.run(inpainter._infer(original, mask, InpainterConfig(), inpainting_size=64))

    assert np.all(result[mask == 0] == 17)
    assert np.all(result[mask > 0] == 255)
