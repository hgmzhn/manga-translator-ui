from typing import Optional

import cv2
import numpy as np

from ..config import Inpainter, InpainterConfig
from .common import CommonInpainter, OfflineInpainter
from .inpainting_aot import AotInpainter
from .inpainting_flux import Flux2KleinInpainter
from .inpainting_lama_mpe import LamaLargeInpainter, LamaMPEInpainter
from .none import NoneInpainter
from .original import OriginalInpainter

_SD_IMPORT_ERROR = None
try:
    from .inpainting_sd import StableDiffusionInpainter
except Exception as e:
    _SD_IMPORT_ERROR = e

    class StableDiffusionInpainter(OfflineInpainter):
        async def _load(self, device: str):
            raise RuntimeError(
                "Stable Diffusion inpainter is unavailable because optional dependencies are missing. "
                f"Original import error: {_SD_IMPORT_ERROR!r}"
            )

        async def _infer(self, image: np.ndarray, mask: np.ndarray, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
            raise RuntimeError(
                "Stable Diffusion inpainter is unavailable because optional dependencies are missing. "
                f"Original import error: {_SD_IMPORT_ERROR!r}"
            )

INPAINTERS = {
    Inpainter.default: AotInpainter,
    Inpainter.lama_large: LamaLargeInpainter,
    Inpainter.lama_mpe: LamaMPEInpainter,
    Inpainter.flux2_klein: Flux2KleinInpainter,
    Inpainter.sd: StableDiffusionInpainter,
    Inpainter.none: NoneInpainter,
    Inpainter.original: OriginalInpainter,
}
inpainter_cache = {}
INPAINT_SPLIT_RATIO = 3.0

def get_inpainter(key: Inpainter, *args, **kwargs) -> CommonInpainter:
    if key not in INPAINTERS:
        raise ValueError(f'Could not find inpainter for: "{key}". Choose from the following: %s' % ','.join(INPAINTERS))
    if not inpainter_cache.get(key):
        inpainter = INPAINTERS[key]
        inpainter_cache[key] = inpainter(*args, **kwargs)
    return inpainter_cache[key]

async def prepare(inpainter_key: Inpainter, device: str = 'cpu', force_torch: bool = False):
    inpainter = get_inpainter(inpainter_key)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.download()
        await inpainter.load(device, force_torch=force_torch)


def _normalize_binary_mask(mask: np.ndarray) -> np.ndarray:
    if mask is None:
        return None
    mask_np = np.asarray(mask)
    if mask_np.ndim == 3:
        mask_np = mask_np[:, :, 0]
    return np.where(mask_np > 0, 255, 0).astype(np.uint8)


def _inpaint_handle_alpha_channel(original_alpha: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    Keep RGBA alpha stable around inpainted areas based on surrounding alpha.
    """
    alpha_2d = original_alpha[:, :, 0] if original_alpha.ndim == 3 else original_alpha
    result_alpha = alpha_2d.copy()
    mask_bin = (_normalize_binary_mask(mask) > 0).astype(np.uint8)

    if not np.any(mask_bin > 0):
        return result_alpha

    mask_dilated = cv2.dilate(mask_bin, np.ones((15, 15), np.uint8), iterations=1)
    surrounding_mask = mask_dilated - mask_bin

    if np.any(surrounding_mask > 0):
        surrounding_alpha = result_alpha[surrounding_mask > 0]
        if surrounding_alpha.size > 0:
            median_surrounding_alpha = np.median(surrounding_alpha)
            if median_surrounding_alpha < 128:
                result_alpha[mask_bin > 0] = np.uint8(np.clip(np.rint(median_surrounding_alpha), 0, 255))

    return result_alpha


def _build_inpaint_split_ranges(long_side: int, short_side: int) -> tuple[list[tuple[int, int]], int, int]:
    """
    Build overlapped 1D split ranges for extreme-aspect-ratio inpainting.
    The computed tile size uses ceil division so the last tile always reaches the image edge.
    """
    long_side = max(int(long_side), 0)
    short_side = max(int(short_side), 1)
    if long_side <= 0:
        return [], 0, 0

    num_splits = max(int(np.ceil(long_side / (short_side * INPAINT_SPLIT_RATIO))), 1)
    overlap = max(int(short_side * 0.1), 0)
    tile_size = max(1, (long_side + overlap * (num_splits - 1) + num_splits - 1) // num_splits)

    ranges = []
    step = max(tile_size - overlap, 1)
    for ii in range(num_splits):
        start = min(ii * step, max(long_side - tile_size, 0))
        end = min(long_side, start + tile_size)
        ranges.append((start, end))

    if ranges:
        ranges[-1] = (max(long_side - tile_size, 0), long_side)

    return ranges, overlap, tile_size

async def dispatch(inpainter_key: Inpainter, image: np.ndarray, mask: np.ndarray, config: Optional[InpainterConfig], inpainting_size: int = 1024, device: str = 'cpu', verbose: bool = False) -> np.ndarray:
    inpainter = get_inpainter(inpainter_key)
    config = config or InpainterConfig()
    if isinstance(inpainter, OfflineInpainter):
        force_torch = getattr(config, 'force_use_torch_inpainting', False)
        await inpainter.load(device, force_torch=force_torch)

    mask_binary = _normalize_binary_mask(mask)

    original_alpha = None
    image_rgb = image
    if image.ndim == 3 and image.shape[2] == 4:
        image_rgb = image[:, :, :3]
        original_alpha = image[:, :, 3]

    h, w = image_rgb.shape[:2]
    aspect_ratio = max(w / h, h / w)
    if aspect_ratio > INPAINT_SPLIT_RATIO:
        inpainted_rgb = await _dispatch_with_split(
            inpainter,
            image_rgb,
            mask_binary,
            config,
            inpainting_size,
            verbose,
        )
    else:
        inpainted_rgb = await inpainter.inpaint(image_rgb, mask_binary, config, inpainting_size, verbose)

    if original_alpha is not None:
        alpha = _inpaint_handle_alpha_channel(original_alpha, mask_binary)
        return np.concatenate([inpainted_rgb, alpha[:, :, None]], axis=2)

    return inpainted_rgb

async def unload(inpainter_key: Inpainter):
    inpainter = inpainter_cache.pop(inpainter_key, None)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.unload()

async def _dispatch_with_split(
    inpainter: CommonInpainter,
    image: np.ndarray,
    mask: np.ndarray,
    config: InpainterConfig,
    inpainting_size: int,
    verbose: bool,
) -> np.ndarray:
    """
    对极端长宽比的图片进行切割修复。
    """
    h, w = image.shape[:2]
    is_vertical = h > w
    long_side = h if is_vertical else w
    short_side = w if is_vertical else h

    split_ranges, overlap, tile_size = _build_inpaint_split_ranges(long_side, short_side)
    num_splits = len(split_ranges)

    if verbose:
        print(f"[Inpainting Split] image={w}x{h}, aspect_ratio={max(w / h, h / w):.2f}")
        print(f"[Inpainting Split] splitting into {num_splits} tiles along {'height' if is_vertical else 'width'}")
        print(f"[Inpainting Split] tile_size={tile_size}, overlap={overlap}")

    tiles = []
    for ii, (start, end) in enumerate(split_ranges):

        if is_vertical:
            tile_img = image[start:end, :, :].copy()
            tile_mask = mask[start:end, :].copy()
        else:
            tile_img = image[:, start:end, :].copy()
            tile_mask = mask[:, start:end].copy()

        if verbose:
            axis_label = "rows" if is_vertical else "cols"
            print(f"[Inpainting Split] processing tile {ii + 1}/{num_splits}: {axis_label} {start}-{end}")

        tile_inpainted = await inpainter.inpaint(tile_img, tile_mask, config, inpainting_size, verbose)
        tiles.append({
            'image': tile_inpainted,
            'start': start,
            'end': end,
        })

    result = image.copy()
    blend_size = overlap // 2 if overlap > 0 else 0

    for ii, tile_data in enumerate(tiles):
        tile_img = tile_data['image']
        start = tile_data['start']
        end = tile_data['end']

        if num_splits == 1:
            if is_vertical:
                result[start:end, :, :] = tile_img
            else:
                result[:, start:end, :] = tile_img
        elif ii == 0:
            if is_vertical:
                result[start:end - blend_size, :, :] = tile_img[:-blend_size, :, :]
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = 1 - (jj / blend_size)
                        idx = end - blend_size + jj
                        tile_idx = tile_img.shape[0] - blend_size + jj
                        result[idx, :, :] = alpha * tile_img[tile_idx, :, :] + (1 - alpha) * result[idx, :, :]
            else:
                result[:, start:end - blend_size, :] = tile_img[:, :-blend_size, :]
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = 1 - (jj / blend_size)
                        idx = end - blend_size + jj
                        tile_idx = tile_img.shape[1] - blend_size + jj
                        result[:, idx, :] = alpha * tile_img[:, tile_idx, :] + (1 - alpha) * result[:, idx, :]
        elif ii == len(tiles) - 1:
            if is_vertical:
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = jj / blend_size
                        result[start + jj, :, :] = (1 - alpha) * result[start + jj, :, :] + alpha * tile_img[jj, :, :]
                result[start + blend_size:end, :, :] = tile_img[blend_size:, :, :]
            else:
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = jj / blend_size
                        result[:, start + jj, :] = (1 - alpha) * result[:, start + jj, :] + alpha * tile_img[:, jj, :]
                result[:, start + blend_size:end, :] = tile_img[:, blend_size:, :]
        else:
            if is_vertical:
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = jj / blend_size
                        result[start + jj, :, :] = (1 - alpha) * result[start + jj, :, :] + alpha * tile_img[jj, :, :]

                result[start + blend_size:end - blend_size, :, :] = tile_img[blend_size:-blend_size, :, :]

                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = 1 - (jj / blend_size)
                        idx = end - blend_size + jj
                        tile_idx = tile_img.shape[0] - blend_size + jj
                        result[idx, :, :] = alpha * tile_img[tile_idx, :, :] + (1 - alpha) * result[idx, :, :]
            else:
                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = jj / blend_size
                        result[:, start + jj, :] = (1 - alpha) * result[:, start + jj, :] + alpha * tile_img[:, jj, :]

                result[:, start + blend_size:end - blend_size, :] = tile_img[:, blend_size:-blend_size, :]

                if blend_size > 0:
                    for jj in range(blend_size):
                        alpha = 1 - (jj / blend_size)
                        idx = end - blend_size + jj
                        tile_idx = tile_img.shape[1] - blend_size + jj
                        result[:, idx, :] = alpha * tile_img[:, tile_idx, :] + (1 - alpha) * result[:, idx, :]

    if verbose:
        print("[Inpainting Split] tiles merged successfully")

    return result
