import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from manga_translator import manga_translator as translator_module  # noqa: E402
from manga_translator.inpainting import ballon_fill  # noqa: E402
from manga_translator.utils import Context  # noqa: E402


class _Region:
    xyxy = (2, 2, 8, 4)


def _translator_for_inpainting():
    translator = object.__new__(translator_module.MangaTranslator)
    translator.device = "cpu"
    translator.verbose = False
    translator._check_cancelled = lambda: None
    translator._get_cuda_memory_snapshot = lambda: None
    translator._log_cuda_memory_snapshot = lambda *args, **kwargs: None
    return translator


def _per_block_config(*, solid_fill=False):
    return SimpleNamespace(
        inpainter=SimpleNamespace(
            inpainter="none",
            inpainting_precision="fp32",
            inpainting_size=2048,
            solid_fill_pure_bubbles=solid_fill,
            per_block_inpainting=True,
        )
    )


def test_per_block_inpainting_uses_refined_mask_and_square_crop(monkeypatch):
    image = np.zeros((8, 10, 3), dtype=np.uint8)
    refined_mask = np.zeros((8, 10), dtype=np.uint8)
    refined_mask[2, 2:5] = 255
    refined_mask[3, 5:8] = 255
    raw_mask = np.full((8, 10), 255, dtype=np.uint8)
    ctx = Context(
        img_rgb=image,
        mask=refined_mask,
        mask_raw=raw_mask,
        text_regions=[_Region()],
    )

    monkeypatch.setattr(
        ballon_fill,
        "enlarge_window",
        lambda xyxy, im_w, im_h, ratio: list(xyxy),
    )

    captured = {}

    async def fake_dispatch(inpainter, crop, mask, config, inpainting_size, device, verbose):
        captured["crop"] = crop.copy()
        captured["mask"] = mask.copy()
        return crop.copy()

    monkeypatch.setattr(translator_module, "dispatch_inpainting", fake_dispatch)

    result = asyncio.run(
        _translator_for_inpainting()._run_inpainting(_per_block_config(), ctx)
    )

    refined_crop = refined_mask[2:4, 2:8]
    expected_mask = cv2.copyMakeBorder(
        refined_crop,
        0,
        4,
        0,
        0,
        cv2.BORDER_REFLECT,
    )
    raw_crop = raw_mask[2:4, 2:8]
    raw_padded = cv2.copyMakeBorder(raw_crop, 0, 4, 0, 0, cv2.BORDER_REFLECT)

    assert result.shape == image.shape
    assert captured["crop"].shape[:2] == (6, 6)
    np.testing.assert_array_equal(captured["mask"], expected_mask)
    assert not np.array_equal(captured["mask"], raw_padded)


def test_raw_mask_is_reserved_for_solid_fill(monkeypatch):
    image = np.zeros((8, 10, 3), dtype=np.uint8)
    refined_mask = np.zeros((8, 10), dtype=np.uint8)
    refined_mask[2:4, 2:8] = 255
    raw_mask = np.zeros((8, 10), dtype=np.uint8)
    raw_mask[2:4, 4:6] = 255
    ctx = Context(
        img_rgb=image,
        mask=refined_mask,
        mask_raw=raw_mask,
        text_regions=[_Region()],
    )

    monkeypatch.setattr(
        ballon_fill,
        "enlarge_window",
        lambda xyxy, im_w, im_h, ratio: list(xyxy),
    )

    captured = {}

    def fake_solid_fill(img, mask, text_regions, mask_tight):
        captured["solid_fill_mask"] = mask_tight.copy()
        return img.copy(), mask.copy(), 0

    async def fake_dispatch(inpainter, crop, mask, config, inpainting_size, device, verbose):
        captured["inpaint_mask"] = mask.copy()
        return crop.copy()

    monkeypatch.setattr(translator_module, "solid_fill_pure_bubbles", fake_solid_fill)
    monkeypatch.setattr(translator_module, "dispatch_inpainting", fake_dispatch)

    asyncio.run(
        _translator_for_inpainting()._run_inpainting(
            _per_block_config(solid_fill=True),
            ctx,
        )
    )

    expected_raw = cv2.dilate(
        raw_mask,
        np.ones((5, 5), np.uint8),
        iterations=1,
    )
    expected_refined = cv2.copyMakeBorder(
        refined_mask[2:4, 2:8],
        0,
        4,
        0,
        0,
        cv2.BORDER_REFLECT,
    )

    np.testing.assert_array_equal(captured["solid_fill_mask"], expected_raw)
    np.testing.assert_array_equal(captured["inpaint_mask"], expected_refined)
