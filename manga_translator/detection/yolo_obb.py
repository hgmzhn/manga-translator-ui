"""
YOLO 辅助检测器
使用 Ultralytics YOLO 运行时进行推理。
"""

import os
from typing import Any, Optional, Tuple

import numpy as np
import torch
from ultralytics import YOLO

from ..utils import Quadrilateral, build_det_rearrange_plan, det_rearrange_patch_array
from .common import OfflineDetector


class YOLOOBBDetector(OfflineDetector):
    """YOLO 辅助检测器 - 基于 Ultralytics YOLO 运行时"""

    supports_detection_rearrange = True

    _MODEL_FILENAME = "ysgyolo_yolo26_2.0.pt"
    _SOURCE_CHECKPOINT_FILENAME = "ysgyolo_yolo26_2.0.pt"
    _MODEL_MAPPING = {
        "model": {
            "url": [
                "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ysgyolo_yolo26_2.0.pt",
            ],
            "hash": "889347d65c8636dd188a8ed4f312b29658543faaa69016b5958ddf0559980e22",
            "file": "ysgyolo_yolo26_2.0.pt",
        },
    }

    _DEFAULT_CLASS_ID_TO_LABEL = {
        0: "balloon",
        1: "qipao",
        2: "fangkuai",
        3: "changfangtiao",
        4: "kuangwai",
        5: "other",
    }

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        super().__init__(*args, **kwargs)

        self.class_id_to_label = dict(self._DEFAULT_CLASS_ID_TO_LABEL)
        self.classes = [label for idx, label in sorted(self.class_id_to_label.items()) if label != "other"]
        self.input_size = 1600
        self.using_cuda = False
        self.device = "cpu"
        self.torch_device = torch.device("cpu")
        self.model: Optional[Any] = None

    def _check_downloaded(self) -> bool:
        return os.path.exists(self._get_file_path(self._MODEL_FILENAME))

    @staticmethod
    def _empty_results() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.empty((0, 4, 2), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )

    @staticmethod
    def _to_numpy(data: Any) -> np.ndarray:
        if isinstance(data, np.ndarray):
            return data
        if hasattr(data, "detach"):
            data = data.detach()
        if hasattr(data, "cpu"):
            data = data.cpu()
        if hasattr(data, "numpy"):
            return data.numpy()
        return np.asarray(data)

    def _resolve_device(self, device: str) -> torch.device:
        requested = (device or "cpu").lower()
        if requested.startswith("cuda"):
            if torch.cuda.is_available():
                return torch.device(device)
            self.logger.warning("YOLO OBB: 请求 CUDA，但当前不可用，回退到 CPU")
        elif requested.startswith("mps"):
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return torch.device("mps")
            self.logger.warning("YOLO OBB: 请求 MPS，但当前不可用，回退到 CPU")
        return torch.device("cpu")

    async def _load(self, device: str):
        model_path = self._get_file_path(self._MODEL_FILENAME)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"YOLO OBB 模型不存在: {model_path}")

        self.torch_device = self._resolve_device(device)
        self.device = str(self.torch_device)
        self.using_cuda = self.torch_device.type == "cuda"

        load_path = os.path.relpath(model_path, os.getcwd())
        if not os.path.exists(load_path):
            load_path = model_path

        model = self.model or YOLO(load_path, task="obb")
        model.to(str(self.torch_device))
        self.model = model

        self.logger.info(f"YOLO OBB: {self.torch_device.type.upper()} 模式加载成功")

    async def _unload(self):
        self.model = None

    def _get_rearrange_target_size(self, detect_size: int) -> int:
        return int(getattr(self, "input_size", detect_size))

    def xyxy2xyxyxyxy(self, boxes: np.ndarray) -> np.ndarray:
        """将轴对齐框从 xyxy 转换为四角点"""
        x1 = boxes[:, 0:1]
        y1 = boxes[:, 1:2]
        x2 = boxes[:, 2:3]
        y2 = boxes[:, 3:4]
        pt1 = np.concatenate([x1, y1], axis=1)
        pt2 = np.concatenate([x2, y1], axis=1)
        pt3 = np.concatenate([x2, y2], axis=1)
        pt4 = np.concatenate([x1, y2], axis=1)
        return np.stack([pt1, pt2, pt3, pt4], axis=1)

    def deduplicate_boxes(
        self,
        boxes: np.ndarray,
        scores: np.ndarray,
        class_ids: np.ndarray,
        distance_threshold: float = 10.0,
        iou_threshold: float = 0.3,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """后处理去重：移除中心点距离很近或高度重叠的框"""
        if len(boxes) == 0:
            return boxes, scores, class_ids

        centers = np.mean(boxes, axis=1)
        keep = []
        sorted_indices = np.argsort(scores)[::-1]

        for i in sorted_indices:
            should_keep = True
            for j in keep:
                dist = np.linalg.norm(centers[i] - centers[j])
                if class_ids[i] == class_ids[j] and dist < distance_threshold:
                    should_keep = False
                    break

                box_i_min = np.min(boxes[i], axis=0)
                box_i_max = np.max(boxes[i], axis=0)
                box_j_min = np.min(boxes[j], axis=0)
                box_j_max = np.max(boxes[j], axis=0)

                inter_min = np.maximum(box_i_min, box_j_min)
                inter_max = np.minimum(box_i_max, box_j_max)
                inter_wh = np.maximum(0, inter_max - inter_min)
                inter_area = inter_wh[0] * inter_wh[1]

                box_i_area = (box_i_max[0] - box_i_min[0]) * (box_i_max[1] - box_i_min[1])
                box_j_area = (box_j_max[0] - box_j_min[0]) * (box_j_max[1] - box_j_min[1])
                union_area = box_i_area + box_j_area - inter_area

                if union_area > 0:
                    iou = inter_area / union_area
                    if iou > iou_threshold:
                        should_keep = False
                        break

            if should_keep:
                keep.append(i)

        return boxes[keep], scores[keep], class_ids[keep]

    def _extract_prediction_arrays(
        self,
        raw_result: Any,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if raw_result is None:
            return self._empty_results()

        obb = getattr(raw_result, "obb", None)
        if obb is not None:
            boxes_corners = self._to_numpy(getattr(obb, "xyxyxyxy", np.empty((0, 4, 2), dtype=np.float32)))
            scores = self._to_numpy(getattr(obb, "conf", np.empty((0,), dtype=np.float32)))
            class_ids = self._to_numpy(getattr(obb, "cls", np.empty((0,), dtype=np.int32)))
        else:
            boxes = getattr(raw_result, "boxes", None)
            if boxes is None:
                return self._empty_results()
            boxes_xyxy = self._to_numpy(getattr(boxes, "xyxy", np.empty((0, 4), dtype=np.float32)))
            if boxes_xyxy.ndim == 1:
                boxes_xyxy = boxes_xyxy.reshape(-1, 4)
            if boxes_xyxy.size == 0:
                return self._empty_results()
            boxes_corners = self.xyxy2xyxyxyxy(boxes_xyxy.astype(np.float32))
            scores = self._to_numpy(getattr(boxes, "conf", np.empty((len(boxes_xyxy),), dtype=np.float32)))
            class_ids = self._to_numpy(getattr(boxes, "cls", np.empty((len(boxes_xyxy),), dtype=np.int32)))

        boxes_corners = np.asarray(boxes_corners, dtype=np.float32)
        if boxes_corners.ndim == 2 and boxes_corners.shape[1] == 8:
            boxes_corners = boxes_corners.reshape(-1, 4, 2)
        elif boxes_corners.ndim != 3 or boxes_corners.shape[1:] != (4, 2):
            return self._empty_results()

        scores = np.asarray(scores, dtype=np.float32).reshape(-1)
        class_ids = np.asarray(class_ids, dtype=np.int32).reshape(-1)
        if len(boxes_corners) == 0 or len(scores) != len(boxes_corners) or len(class_ids) != len(boxes_corners):
            return self._empty_results()

        valid_class_ids = np.array(list(self.class_id_to_label.keys()), dtype=np.int32)
        valid_cls_mask = np.isin(class_ids, valid_class_ids)
        if not np.all(valid_cls_mask):
            drop_count = int(np.size(valid_cls_mask) - np.sum(valid_cls_mask))
            self.logger.info(f"YOLO OBB过滤无效类别: 移除 {drop_count} 个框")
            boxes_corners = boxes_corners[valid_cls_mask]
            scores = scores[valid_cls_mask]
            class_ids = class_ids[valid_cls_mask]

        if len(boxes_corners) == 0:
            return self._empty_results()

        return boxes_corners.astype(np.float32), scores.astype(np.float32), class_ids.astype(np.int32)

    def _predict_patch(
        self,
        image: np.ndarray,
        conf_threshold: float,
        iou_threshold: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self.model is None:
            raise RuntimeError("YOLO OBB 模型未加载")

        results = self.model.predict(
            source=image,
            imgsz=int(self.input_size),
            conf=float(conf_threshold),
            iou=float(max(0.01, min(iou_threshold, 0.95))),
            device=str(self.torch_device),
            verbose=False,
        )
        if isinstance(results, list):
            raw_result = results[0] if results else None
        else:
            raw_result = next(iter(results), None)
        return self._extract_prediction_arrays(raw_result)

    def _rearrange_detect_unified(
        self,
        image: np.ndarray,
        text_threshold: float,
        iou_threshold: float,
        verbose: bool = False,
        result_path_fn=None,
        rearrange_plan: Optional[dict] = None,
        det_rearrange_min_effective_short_side: float = 341.0,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """使用与主检测器相同的切割逻辑进行检测"""
        if image is None or image.size == 0:
            self.logger.error("YOLO OBB: 输入图片无效")
            return self._empty_results()

        h, w = image.shape[:2]
        if h == 0 or w == 0:
            self.logger.error(f"YOLO OBB: 图片尺寸为0: {h}x{w}")
            return self._empty_results()

        if rearrange_plan is None:
            rearrange_plan = build_det_rearrange_plan(
                image,
                tgt_size=self.input_size,
                min_effective_short_side=det_rearrange_min_effective_short_side,
            )
        if rearrange_plan is None:
            self.logger.warning("YOLO OBB统一切割: 当前图像不满足切割条件")
            return self._empty_results()

        transpose = rearrange_plan["transpose"]
        h = rearrange_plan["h"]
        w = rearrange_plan["w"]
        pw_num = rearrange_plan["pw_num"]
        patch_size = rearrange_plan["patch_size"]
        ph_num = rearrange_plan["ph_num"]
        rel_step_list = rearrange_plan["rel_step_list"]
        pad_num = rearrange_plan["pad_num"]

        self.logger.info(
            f"YOLO OBB统一切割: 原图={h}x{w}, patch_size={patch_size}, "
            f"ph_num={ph_num}, pw_num={pw_num}, pad_num={pad_num}, transpose={transpose}"
        )

        patch_array = det_rearrange_patch_array(rearrange_plan)

        all_boxes = []
        all_scores = []
        all_class_ids = []
        all_patch_info = []

        for ii, patch in enumerate(patch_array):
            if np.all(patch == 0):
                self.logger.debug(f"YOLO OBB patch {ii}: 跳过padding patch")
                continue

            if patch.size == 0 or patch.shape[0] == 0 or patch.shape[1] == 0:
                self.logger.warning(f"YOLO OBB patch {ii}: 跳过无效patch, shape={patch.shape}")
                continue

            try:
                boxes, scores, class_ids = self._predict_patch(
                    patch,
                    conf_threshold=text_threshold,
                    iou_threshold=iou_threshold,
                )
            except Exception as e:
                self.logger.error(f"YOLO OBB patch {ii} 推理失败: {e}")
                self.logger.error(f"Patch shape: {patch.shape}")
                continue

            patch_shape = patch.shape[:2]
            if len(boxes) > 0:
                all_boxes.append(boxes)
                all_scores.append(scores)
                all_class_ids.append(class_ids)
                all_patch_info.append((ii, patch_shape))

            if verbose:
                self.logger.debug(f"YOLO OBB patch {ii}: 检测到 {len(boxes)} 个框")
                try:
                    import logging

                    from ..utils import imwrite_unicode

                    logger = logging.getLogger("manga_translator")
                    debug_path = (
                        result_path_fn(f"yolo_rearrange_{ii}.png")
                        if result_path_fn
                        else f"result/yolo_rearrange_{ii}.png"
                    )
                    imwrite_unicode(debug_path, patch[..., ::-1], logger)
                except Exception as e:
                    self.logger.error(f"保存YOLO调试图失败: {e}")

        if len(all_boxes) == 0:
            return self._empty_results()

        mapped_boxes = []
        mapped_scores = []
        mapped_class_ids = []

        for boxes, scores, class_ids, (patch_idx, patch_shape) in zip(
            all_boxes, all_scores, all_class_ids, all_patch_info
        ):
            _pw = patch_shape[1] // pw_num
            if _pw <= 0:
                continue

            for box, score, class_id in zip(boxes, scores, class_ids):
                x_min = float(np.min(box[:, 0]))
                x_max = float(np.max(box[:, 0]))

                jj_start = max(0, int(np.floor(x_min / _pw)))
                jj_end = min(pw_num - 1, int(np.floor(max(x_max - 1e-6, x_min) / _pw)))

                for jj in range(jj_start, jj_end + 1):
                    pidx = patch_idx * pw_num + jj
                    if pidx >= len(rel_step_list):
                        continue

                    stripe_l = jj * _pw
                    stripe_r = (jj + 1) * _pw
                    if x_max <= stripe_l or x_min >= stripe_r:
                        continue

                    rel_t = rel_step_list[pidx]
                    t = int(round(rel_t * h))

                    mapped_box = box.copy()
                    mapped_box[:, 0] = np.clip(mapped_box[:, 0], stripe_l, stripe_r) - stripe_l
                    mapped_box[:, 1] = np.clip(mapped_box[:, 1] + t, 0, h)

                    mapped_w = float(np.max(mapped_box[:, 0]) - np.min(mapped_box[:, 0]))
                    mapped_h = float(np.max(mapped_box[:, 1]) - np.min(mapped_box[:, 1]))
                    if mapped_w < 1.0 or mapped_h < 1.0:
                        continue

                    mapped_boxes.append(mapped_box)
                    mapped_scores.append(score)
                    mapped_class_ids.append(class_id)

        if len(mapped_boxes) == 0:
            return self._empty_results()

        boxes_corners = np.array(mapped_boxes, dtype=np.float32)
        scores = np.array(mapped_scores, dtype=np.float32)
        class_ids = np.array(mapped_class_ids, dtype=np.int32)

        if transpose:
            boxes_corners = boxes_corners[:, :, ::-1].copy()

        boxes_corners, scores, class_ids = self.deduplicate_boxes(
            boxes_corners,
            scores,
            class_ids,
            distance_threshold=20.0,
            iou_threshold=0.5,
        )

        self.logger.info(f"YOLO OBB统一切割检测完成: 合并去重后 {len(boxes_corners)} 个框")
        return boxes_corners, scores, class_ids

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
        """
        执行检测推理（支持长图分割检测）

        Returns:
            textlines: List[Quadrilateral]
            raw_mask: None
            debug_img: None
        """
        if image is None:
            self.logger.error("YOLO OBB: 接收到的图片为None")
            return [], None, None

        if not isinstance(image, np.ndarray):
            self.logger.error(f"YOLO OBB: 接收到的不是numpy数组，类型: {type(image)}")
            return [], None, None

        if image.size == 0:
            self.logger.error("YOLO OBB: 接收到的图片大小为0")
            return [], None, None

        if len(image.shape) < 2:
            self.logger.error(f"YOLO OBB: 图片维度不足: {image.shape}")
            return [], None, None

        if image.shape[0] == 0 or image.shape[1] == 0:
            self.logger.error(f"YOLO OBB: 图片尺寸为0: {image.shape}")
            return [], None, None

        self.logger.debug(
            f"YOLO OBB输入图像: shape={image.shape}, dtype={image.dtype}, min={image.min()}, max={image.max()}"
        )

        img_shape = image.shape[:2]
        rearrange_plan = build_det_rearrange_plan(
            image,
            tgt_size=self.input_size,
            min_effective_short_side=det_rearrange_min_effective_short_side,
        )

        if rearrange_plan is not None:
            self.logger.info("YOLO OBB: 检测到长图，使用统一切割逻辑")
            boxes_corners, scores, class_ids = self._rearrange_detect_unified(
                image,
                text_threshold,
                box_threshold,
                verbose,
                result_path_fn,
                rearrange_plan=rearrange_plan,
                det_rearrange_min_effective_short_side=det_rearrange_min_effective_short_side,
            )
        else:
            try:
                boxes_corners, scores, class_ids = self._predict_patch(
                    image,
                    conf_threshold=text_threshold,
                    iou_threshold=box_threshold,
                )
            except Exception as e:
                self.logger.error(f"YOLO OBB推理失败: {e}")
                self.logger.error(f"输入图像 shape: {image.shape}, dtype: {image.dtype}")
                self.logger.error(f"当前 device: {self.device}")
                raise

            if len(boxes_corners) > 0:
                boxes_corners[:, :, 0] = np.clip(boxes_corners[:, :, 0], 0, img_shape[1])
                boxes_corners[:, :, 1] = np.clip(boxes_corners[:, :, 1], 0, img_shape[0])

        textlines = []
        for corners, score, class_id in zip(boxes_corners, scores, class_ids):
            pts = corners.astype(np.int32)
            label = self.class_id_to_label.get(int(class_id))
            if not label:
                label = self.classes[class_id] if class_id < len(self.classes) else f"class_{class_id}"
            quad = Quadrilateral(pts, label, float(score))
            quad.det_label = label
            quad.yolo_label = label
            quad.is_yolo_box = True
            textlines.append(quad)

        self.logger.info(f"YOLO OBB检测到 {len(textlines)} 个文本框")
        return textlines, None, None
