import gc
import os

from typing import ClassVar

import cv2
import numpy as np
import torch

from ..config import InpainterConfig
from .common import OfflineInpainter



class Flux2KleinInpainter(OfflineInpainter):
    """FLUX.2 Klein 4B image inpainter with GGUF transformer weights."""

    _MODEL_MAPPING: ClassVar[dict] = {
        "model_index": {
            "url": [
                "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/model_index.json",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/model_index.json",
            ],
            "file": "flux2-klein/model_index.json",
        },
        "scheduler": {
            "url": [
                "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/scheduler/scheduler_config.json",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/scheduler/scheduler_config.json",
            ],
            "file": "flux2-klein/scheduler/scheduler_config.json",
        },
        "transformer_config": {
            "url": [
                "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/transformer/config.json",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/transformer/config.json",
            ],
            "file": "flux2-klein/transformer/config.json",
        },
        "transformer": {
            "url": [
                "https://huggingface.co/unsloth/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q4_K_M.gguf",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/transformer/flux-2-klein-4b-Q4_K_M.gguf",
            ],
            "hash": "0b25d143c8469b342bc5af3bce92b783bf6b0636d285f7b2f75e38af63af9a15",
            "file": "flux2-klein/transformer/flux-2-klein-4b-Q4_K_M.gguf",
        },
        "vae_config": {
            "url": [
                "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/vae/config.json",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/vae/config.json",
            ],
            "file": "flux2-klein/vae/config.json",
        },
        "vae": {
            "url": [
                "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/vae/diffusion_pytorch_model.safetensors",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/vae/diffusion_pytorch_model.safetensors",
            ],
            "hash": "ca70d2202afe6415bdbcb8793ba8cd99fd159cfe6192381504d6c4d3036e0f04",
            "file": "flux2-klein/vae/diffusion_pytorch_model.safetensors",
        },
        "prompt_embeddings": {
            "url": [
                "https://huggingface.co/dreMaz/flux2-klein-inpaint/resolve/main/flux2_inpaint_prompt.safetensors",
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/flux2-klein/flux2_inpaint_prompt.safetensors",
            ],
            "hash": "7d7b19ec266581cb1faa51ad92f49a302932b0c589feae633f97da2d925cb6a4",
            "file": "flux2-klein/flux2_inpaint_prompt.safetensors",
        },
    }
    def __init__(self):
        for mapping in self._MODEL_MAPPING.values():
            file_path = mapping.get("file")
            if file_path:
                os.makedirs(os.path.dirname(self._get_file_path(file_path)), exist_ok=True)
        super().__init__()
        self.pipeline = None
        self.prompt_embeds = None
        self.device = "cpu"

    async def _load(self, device: str, **kwargs):
        from diffusers import GGUFQuantizationConfig
        from safetensors.torch import load_file
        from .flux_inpaint_pipeline import (
            AutoencoderKLFlux2,
            Flux2KleinInpaintPipeline,
            Flux2Transformer2DModel,
        )

        self.device = device
        dtype = torch.bfloat16
        model_root = self._get_file_path("flux2-klein")
        vae_root = self._get_file_path("flux2-klein/vae")

        transformer = Flux2Transformer2DModel.from_single_file(
            self._get_file_path("flux2-klein/transformer/flux-2-klein-4b-Q4_K_M.gguf"),
            quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
            torch_dtype=dtype,
            config=self._get_file_path("flux2-klein/transformer/config.json"),
        )
        self.prompt_embeds = load_file(self._get_file_path("flux2-klein/flux2_inpaint_prompt.safetensors"))["prompt_embeds"]
        self.prompt_embeds = self.prompt_embeds.to(device=device, dtype=dtype)
        vae = AutoencoderKLFlux2.from_pretrained(vae_root, local_files_only=True).to(device=device, dtype=dtype)
        self.pipeline = Flux2KleinInpaintPipeline.from_pretrained(
            model_root,
            text_encoder=None,
            tokenizer=None,
            vae=vae,
            transformer=transformer,
            local_files_only=True,
        ).to(device=device)
        if device.startswith("cuda") and torch.version.hip is None:
            try:
                self.pipeline.enable_xformers_memory_efficient_attention()
            except Exception as exc:
                self.logger.warning(f"Flux2 Klein xformers unavailable; using default attention: {exc}")

    async def _unload(self):
        self.pipeline = None
        self.prompt_embeds = None
        gc.collect()

    @staticmethod
    def _resize_inputs(image: np.ndarray, mask: np.ndarray, max_resolution: int):
        height, width = image.shape[:2]
        scale = min(1.0, max_resolution / max(height, width))
        target_h = max(16, int(round(height * scale / 16)) * 16)
        target_w = max(16, int(round(width * scale / 16)) * 16)
        resized_image = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_AREA)
        resized_mask = cv2.resize(mask, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        return resized_image, resized_mask

    async def _infer(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        config: InpainterConfig,
        inpainting_size: int = 1024,
        verbose: bool = False,
    ) -> np.ndarray:
        if self.pipeline is None or self.prompt_embeds is None:
            raise RuntimeError("Flux2 Klein inpainter is not loaded")

        original = np.asarray(image, dtype=np.uint8)
        mask_binary = (np.asarray(mask) > 0).astype(np.uint8)
        if not np.any(mask_binary):
            return original.copy()

        max_resolution = max(16, int(inpainting_size or 1024))
        resized_image, resized_mask = self._resize_inputs(original, mask_binary, max_resolution)
        result = self.pipeline(
            image=resized_image,
            mask=resized_mask,
            prompt_embeds=self.prompt_embeds,
            height=resized_image.shape[0],
            width=resized_image.shape[1],
            num_inference_steps=20,
            guidance_scale=1.0,
            return_dict=False,
            output_type="numpy",
        )
        generated = result[0] if isinstance(result, tuple) else result
        generated = np.asarray(generated)
        if generated.ndim == 4:
            generated = generated[0]
        if generated.dtype != np.uint8:
            generated = np.clip(generated * 255.0, 0, 255).astype(np.uint8)
        if generated.shape[:2] != original.shape[:2]:
            generated = cv2.resize(generated, (original.shape[1], original.shape[0]), interpolation=cv2.INTER_LINEAR)
        full_mask = mask_binary[..., None]
        return generated * full_mask + original * (1 - full_mask)
