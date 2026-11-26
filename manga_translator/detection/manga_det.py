"""PP-OCRv6 Manga ONNX text detector."""

import os

import cv2
import numpy as np

from ..utils import Quadrilateral, det_rearrange_forward
from ..utils.onnx_runtime import (
    create_inference_session,
    create_session_options,
    import_onnxruntime,
)
from .common import OfflineDetector
from .default_utils.dbnet_utils import SegDetectorRepresenter


class MangaDetDetector(OfflineDetector):
    """PP-OCRv6 Manga detector with on-demand model downloading."""

    _MODEL_FILENAME = "manga_det_v0.2.onnx"
    supports_detection_rearrange = True
    # ModelWrapper downloads missing files and verifies their SHA-256.
    _MODEL_MAPPING = {
        "model": {
            "url": "https://huggingface.co/Kellenok/PP-OCRv6_manga/resolve/main/det/manga_det_v0.2.onnx",
            "hash": "d132078c46e292b226fb5a2ca52a7612ad319262dfdf493e7d8e3be435295978",
            "file": _MODEL_FILENAME,
        },
    }
    _INPUT_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    _INPUT_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    async def _load(self, device: str):
        model_path = self._get_file_path(self._MODEL_FILENAME)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Manga detector model not found: {model_path}")

        ort = import_onnxruntime(
            "onnxruntime is required for manga_det_v0.2.onnx. "
            "Install onnxruntime-gpu (or onnxruntime) first."
        )
        self.session, self.runtime_device = create_inference_session(
            ort,
            model_path,
            device=device,
            sess_options=create_session_options(ort, log_severity_level=3),
            logger=self.logger,
        )
        self.input_name = self.session.get_inputs()[0].name
        self.device = device
        self.logger.info(f"Manga Det loaded with ONNX Runtime on {self.runtime_device.upper()}")

    async def _unload(self):
        self.session = None

    @staticmethod
    def _get_input_shape(height: int, width: int, detect_size: int) -> tuple[int, int]:
        target_size = max(int(detect_size), 32)
        if max(height, width) / min(height, width) > 2:
            ratio = min(1.0, (0.75 * target_size * target_size / (height * width)) ** 0.5)
        else:
            ratio = target_size / max(height, width)
        resized_height = max(int(round(height * ratio / 32) * 32), 32)
        resized_width = max(int(round(width * ratio / 32) * 32), 32)
        return resized_height, resized_width

    def _forward_batch(self, batch: np.ndarray, device: str = "cpu"):
        # Project images are RGB; the published detector expects BGR.
        model_input = np.asarray(batch)[..., ::-1].astype(np.float32) / 255.0
        model_input = (model_input - self._INPUT_MEAN) / self._INPUT_STD
        model_input = np.ascontiguousarray(model_input.transpose((0, 3, 1, 2)))
        prediction = self.session.run(None, {self.input_name: model_input})[0]
        return prediction, prediction

    async def _infer(
        self,
        image: np.ndarray,
        detect_size: int,
        text_threshold: float,
        box_threshold: float,
        unclip_ratio: float,
        verbose: bool = False,
        result_path_fn=None,
        det_rearrange_min_effective_short_side: float = 341.0,
    ):
        height, width = image.shape[:2]
        prediction, _ = det_rearrange_forward(
            image,
            self._forward_batch,
            detect_size,
            4,
            device=self.device,
            verbose=verbose,
            result_path_fn=result_path_fn,
            min_effective_short_side=det_rearrange_min_effective_short_side,
        )
        if prediction is None:
            resized_height, resized_width = self._get_input_shape(height, width, detect_size)
            model_image = cv2.resize(image, (resized_width, resized_height))
            prediction, _ = self._forward_batch([model_image])

        detector = SegDetectorRepresenter(text_threshold, box_threshold, unclip_ratio=unclip_ratio)
        boxes, scores = detector({'shape': [(height, width)]}, prediction)
        boxes, scores = boxes[0], scores[0]

        textlines = []
        for points, score in zip(boxes, scores):
            if not np.any(points):
                continue
            quad = Quadrilateral(points.astype(np.int32), '', float(score))
            if quad.area > 16:
                textlines.append(quad)

        raw_mask = cv2.resize(prediction[0, 0], (width, height), interpolation=cv2.INTER_LINEAR)
        raw_mask = np.clip(raw_mask * 255, 0, 255).astype(np.uint8)
        self.logger.info(f"Manga Det detected {len(textlines)} text lines")
        return textlines, raw_mask, None
