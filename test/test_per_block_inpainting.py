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
from manga_translator.utils import Context, build_bubble_mask_from_mangalens_result  # noqa: E402


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
        ocr=SimpleNamespace(model_bubble_overlap_threshold=0.1),
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
        text_regions=[],
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


def test_per_block_inpainting_isolates_refined_mask_components(monkeypatch):
    image = np.zeros((8, 14, 3), dtype=np.uint8)
    remaining_mask = np.zeros((8, 14), dtype=np.uint8)
    remaining_mask[2:4, 2:4] = 255
    remaining_mask[2:4, 9:11] = 255
    windows = []
    captured_masks = []

    def fake_enlarge_window(xyxy, im_w, im_h, ratio):
        windows.append((list(xyxy), ratio))
        return [0, 0, im_w, im_h]

    async def fake_inpaint(crop, mask):
        captured_masks.append(mask.copy())
        return crop.copy()

    monkeypatch.setattr(ballon_fill, "enlarge_window", fake_enlarge_window)

    result, count = asyncio.run(
        ballon_fill.inpaint_regions_per_block(image, remaining_mask, fake_inpaint)
    )

    assert count == 2
    assert windows == [([2, 2, 4, 4], 2.0), ([9, 2, 11, 4], 2.0)]
    assert [np.count_nonzero(mask[:8, :14]) for mask in captured_masks] == [4, 4]
    assert captured_masks[0][2:4, 2:4].all()
    assert not captured_masks[0][2:4, 9:11].any()
    assert captured_masks[1][2:4, 9:11].all()
    assert not captured_masks[1][2:4, 2:4].any()
    assert not remaining_mask.any()
    np.testing.assert_array_equal(result, image)


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
    model_bubble_mask = np.zeros((8, 10), dtype=np.uint8)
    model_bubble_mask[1:6, 1:9] = 255

    def fake_solid_fill(img, mask, text_regions, mask_tight, bubble_mask, overlap_threshold):
        captured["solid_fill_mask"] = mask_tight.copy()
        captured["bubble_mask"] = bubble_mask.copy()
        captured["overlap_threshold"] = overlap_threshold
        return img.copy(), mask.copy(), 0

    async def fake_dispatch(inpainter, crop, mask, config, inpainting_size, device, verbose):
        captured["inpaint_mask"] = mask.copy()
        return crop.copy()

    monkeypatch.setattr(translator_module, "solid_fill_pure_bubbles", fake_solid_fill)
    monkeypatch.setattr(translator_module, "dispatch_inpainting", fake_dispatch)
    monkeypatch.setattr(
        translator_module,
        "detect_bubbles_with_mangalens",
        lambda *args, **kwargs: object(),
    )
    def fake_build_bubble_mask(result, shape, erode_ratio=0.0):
        captured["bubble_erode_ratio"] = erode_ratio
        return model_bubble_mask.copy()

    monkeypatch.setattr(
        translator_module,
        "build_bubble_mask_from_mangalens_result",
        fake_build_bubble_mask,
    )

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
    np.testing.assert_array_equal(captured["bubble_mask"], model_bubble_mask)
    assert captured["bubble_erode_ratio"] == 0.02
    assert captured["overlap_threshold"] == 0.1
    np.testing.assert_array_equal(captured["inpaint_mask"], expected_refined)


def test_solid_fill_handles_connected_bubbles_and_excludes_all_raw_text():
    image = np.full((24, 64, 3), 80, dtype=np.uint8)
    image[2:22, 2:42] = 200
    image[2:22, 2] = (0, 0, 255)
    image[9:13, 9:13] = 0
    image[9:13, 29:33] = 0

    raw_mask = np.zeros((24, 64), dtype=np.uint8)
    raw_mask[9:13, 9:13] = 255
    raw_mask[9:13, 29:33] = 255
    refined_mask = raw_mask.copy()
    bubble_mask = np.zeros((24, 64), dtype=np.uint8)
    bubble_mask[2:22, 2:22] = 255
    bubble_mask[6:22, 18:42] = 255
    bubble_mask[2:22, 46:62] = 255
    bubble_mask = build_bubble_mask_from_mangalens_result(
        SimpleNamespace(
            raw_result=SimpleNamespace(
                masks=SimpleNamespace(data=(bubble_mask > 0)[None, ...]),
            ),
            detections=[],
        ),
        bubble_mask.shape,
        erode_ratio=0.02,
    )
    regions = [
        SimpleNamespace(xyxy=(9, 9, 13, 13)),
        SimpleNamespace(xyxy=(29, 9, 33, 13)),
    ]

    result, remaining_mask, count = ballon_fill.solid_fill_pure_bubbles(
        image,
        refined_mask,
        regions,
        raw_mask,
        bubble_mask,
        overlap_threshold=0.1,
    )

    assert count == 2
    np.testing.assert_array_equal(result[10, 10], (200, 200, 200))
    np.testing.assert_array_equal(result[10, 30], (200, 200, 200))
    np.testing.assert_array_equal(result[10, 2], (0, 0, 255))
    np.testing.assert_array_equal(result[10, 50], (80, 80, 80))
    assert not remaining_mask.any()


def test_bubble_mask_builder_supports_component_and_image_ratios():
    masks = np.zeros((1, 200, 200), dtype=np.uint8)
    masks[0, 50:100, 50:100] = 1
    result = SimpleNamespace(
        raw_result=SimpleNamespace(masks=SimpleNamespace(data=masks)),
        detections=[],
    )

    component = build_bubble_mask_from_mangalens_result(
        result, masks.shape[1:], erode_ratio=0.02)
    image = build_bubble_mask_from_mangalens_result(
        result, masks.shape[1:], erode_ratio=0.02, erode_per_component=False)

    assert component[50, 75] == 0 and component[51, 75] == 255
    assert image[53, 75] == 0 and image[54, 75] == 255
