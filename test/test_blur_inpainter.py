import _bootstrap  # noqa: F401, I001

import asyncio
import typing

import numpy as np
import pytest
from pydantic import ValidationError

from desktop_qt_ui.core.config_models import InpainterSettings
from editor.controller_inpaint_service import EditorControllerInpaintService
from editor.inpaint_state import (
    InpaintArtifact,
    InpaintConfigSnapshot,
    InpaintKey,
    InpaintRequest,
    MaskDelta,
)
import manga_translator.inpainting as inpainting_pkg
import manga_translator.manga_translator as mt_mod
from manga_translator.config import Config, Inpainter, InpainterConfig
from manga_translator.inpainting.blur import BlurInpainter
from manga_translator.utils import Context
from services.erase_config_service import InpainterConfig as EraseInpainterConfig
from services.erase_config_service import InpainterType
from services.lightweight_inpainter import LightweightInpainter, PreviewConfig


def _make_striped_rgb(h: int = 160, w: int = 160) -> np.ndarray:
    img = np.full((h, w, 3), 240, dtype=np.uint8)
    img[:, ::4] = 20
    img[::4, :] = 40
    return img


def test_blur_inpainter_rgb_and_grayscale_roi_isolation():
    rgb = _make_striped_rgb(160, 160)
    empty_mask = np.zeros((160, 160), dtype=np.uint8)
    empty_out = BlurInpainter.apply_blur(rgb, empty_mask, blur_radius=15)
    assert empty_out is not rgb
    assert np.array_equal(empty_out, rgb)

    mask = np.zeros((160, 160), dtype=np.uint8)
    mask[70:90, 70:90] = 255

    # 偶数 radius、超大 radius（钳位到 200->201）与 0（自适应）均应正常工作
    out_even = BlurInpainter.apply_blur(rgb, mask, blur_radius=16)
    assert out_even.shape == rgb.shape and out_even.dtype == np.uint8
    assert not np.array_equal(out_even[72:88, 72:88], rgb[72:88, 72:88])
    assert np.array_equal(out_even[:15, :15], rgb[:15, :15])
    assert np.array_equal(out_even[-15:, -15:], rgb[-15:, -15:])

    out_clamped = BlurInpainter.apply_blur(rgb, mask, blur_radius=500)
    assert out_clamped.shape == rgb.shape and out_clamped.dtype == np.uint8

    out_auto = BlurInpainter.apply_blur(rgb, mask, blur_radius=0)
    assert out_auto.shape == rgb.shape and out_auto.dtype == np.uint8
    assert np.array_equal(out_auto[:15, :15], rgb[:15, :15])

    # 2D 灰度图兼容且保持局部 ROI 隔离
    gray = rgb[:, :, 0].copy()
    out_gray = BlurInpainter.apply_blur(gray, mask, blur_radius=15)
    assert out_gray.shape == gray.shape and out_gray.dtype == np.uint8
    assert not np.array_equal(out_gray[72:88, 72:88], gray[72:88, 72:88])
    assert np.array_equal(out_gray[:15, :15], gray[:15, :15])


def test_blur_radius_config_validation():
    for cls in (InpainterConfig, InpainterSettings):
        assert cls(blur_radius=0).blur_radius == 0
        assert cls(blur_radius=200).blur_radius == 200

        with pytest.raises(ValidationError):
            cls(blur_radius=-1)
        with pytest.raises(ValidationError):
            cls(blur_radius=201)

        obj = cls()
        with pytest.raises(ValidationError):
            obj.blur_radius = -1
        with pytest.raises(ValidationError):
            obj.blur_radius = 201


def test_dispatch_blur_skips_long_image_split(monkeypatch):
    async def fail_split(*_args, **_kwargs):
        raise AssertionError("blur inpainter must skip long-image split")

    monkeypatch.setattr(inpainting_pkg, "_dispatch_with_split", fail_split)

    img = _make_striped_rgb(400, 100)  # aspect ratio 4.0 > INPAINT_SPLIT_RATIO (3.0)
    mask = np.zeros((400, 100), dtype=np.uint8)
    mask[180:220, 30:70] = 255
    cfg = InpainterConfig(inpainter=Inpainter.blur, blur_radius=15)

    out = asyncio.run(
        inpainting_pkg.dispatch(Inpainter.blur, img, mask, cfg, device="cpu")
    )
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    assert np.array_equal(out[:20, :20], img[:20, :20])


def test_run_inpainting_skips_per_block_for_blur(monkeypatch):
    async def fail_per_block(*_args, **_kwargs):
        raise AssertionError("blur inpainter must skip per_block_inpainting")

    dispatched = []

    async def fake_dispatch_inpainting(key, img, mask, *_args, **_kwargs):
        dispatched.append((key, img.shape, mask.shape))
        return img.copy()

    monkeypatch.setattr(mt_mod, "inpaint_regions_per_block", fail_per_block)
    monkeypatch.setattr(mt_mod, "dispatch_inpainting", fake_dispatch_inpainting)

    translator = mt_mod.MangaTranslator.__new__(mt_mod.MangaTranslator)
    translator._check_cancelled = lambda: None
    translator._get_cuda_memory_snapshot = lambda: None
    translator._log_cuda_memory_snapshot = lambda *_a, **_kw: None
    translator._model_usage_timestamps = {}
    translator.device = "cpu"
    translator.verbose = False

    cfg = Config()
    cfg.inpainter.inpainter = Inpainter.blur
    cfg.inpainter.per_block_inpainting = True
    cfg.inpainter.solid_fill_pure_bubbles = False

    ctx = Context()
    ctx.img_rgb = np.zeros((64, 64, 3), dtype=np.uint8)
    ctx.mask = np.zeros((64, 64), dtype=np.uint8)
    ctx.mask[16:32, 16:32] = 255

    result = asyncio.run(translator._run_inpainting(cfg, ctx))
    assert result.shape == (64, 64, 3)
    assert dispatched == [(Inpainter.blur, (64, 64, 3), (64, 64))]


def test_editor_controller_inpaint_service_blur_helpers(monkeypatch):
    hints = typing.get_type_hints(EditorControllerInpaintService._is_blur_inpainter)
    assert hints["return"] is bool

    assert EditorControllerInpaintService._is_blur_inpainter("blur") is True
    assert EditorControllerInpaintService._is_blur_inpainter(Inpainter.blur) is True
    assert EditorControllerInpaintService._is_blur_inpainter("Inpainter.blur") is True
    assert EditorControllerInpaintService._is_blur_inpainter("lama_large") is False
    assert EditorControllerInpaintService._is_blur_inpainter(None) is False

    img = np.zeros((32, 32, 3), dtype=np.uint8)
    prev_mask = np.zeros((32, 32), dtype=np.uint8)
    prev_mask[2:6, 2:6] = 255
    curr_mask = prev_mask.copy()
    curr_mask[20:26, 20:26] = 255
    added = np.zeros((32, 32), dtype=np.uint8)
    added[20:26, 20:26] = 255

    prev_key = InpaintKey(document_id=1, mask_revision=1)
    curr_key = InpaintKey(document_id=1, mask_revision=2)
    prev_artifact = InpaintArtifact(key=prev_key, mask=prev_mask, image=img.copy())
    request = InpaintRequest(
        key=curr_key,
        image=img,
        mask=curr_mask,
        delta=MaskDelta(added=added, removed=np.zeros_like(added), mask_revision=2),
        previous_artifact=prev_artifact,
        config=InpaintConfigSnapshot(
            inpainter="blur",
            inpainting_precision="fp32",
            force_use_torch_inpainting=False,
            inpainting_size=1024,
            device="cpu",
            blur_radius=15,
        ),
    )

    full_calls = []

    async def fake_full_inpaint(req):
        full_calls.append(req.key)
        return InpaintArtifact(req.key, req.mask, req.image.copy())

    async def fail_incremental(*_args, **_kwargs):
        raise AssertionError("blur must use _full_inpaint instead of bbox incremental")

    monkeypatch.setattr(EditorControllerInpaintService, "_full_inpaint", staticmethod(fake_full_inpaint))
    monkeypatch.setattr(EditorControllerInpaintService, "_dispatch_inpaint", staticmethod(fail_incremental))

    artifact = asyncio.run(EditorControllerInpaintService.async_inpaint(request))
    assert artifact is not None and artifact.key == curr_key
    assert full_calls == [curr_key]


def test_lightweight_inpainter_blur_cache_key_and_scale(monkeypatch):
    li = LightweightInpainter()
    try:
        current_cfg = EraseInpainterConfig(inpainter=InpainterType.BLUR, blur_radius=20)
        li.config_service = type("_FakeCfgService", (), {"get_current_config": lambda _self: current_cfg})()

        img = np.zeros((32, 32, 3), dtype=np.uint8)
        mask = np.zeros((32, 32), dtype=np.uint8)
        preview_cfg = PreviewConfig()

        key_r20 = li._generate_cache_key(img, mask, InpainterType.BLUR, preview_cfg)
        current_cfg.blur_radius = 40
        key_r40 = li._generate_cache_key(img, mask, InpainterType.BLUR, preview_cfg)
        assert key_r20 != key_r40

        seen_radii = []

        def fake_apply_blur(image, _mask, blur_radius=0):
            seen_radii.append(blur_radius)
            return image

        monkeypatch.setattr(BlurInpainter, "apply_blur", staticmethod(fake_apply_blur))

        current_cfg.blur_radius = 40
        li._inpaint_blur(img, mask, scale=0.25)
        current_cfg.blur_radius = 0
        li._inpaint_blur(img, mask, scale=0.25)

        assert seen_radii == [10, 0]
    finally:
        li.shutdown()
