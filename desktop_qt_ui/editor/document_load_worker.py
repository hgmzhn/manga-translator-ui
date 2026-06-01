from __future__ import annotations

import concurrent.futures
import os
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from manga_translator.utils.path_manager import (
    find_inpainted_path,
    find_json_path,
    find_paint_overlay_path,
)

from .session import DocumentLoadFailure, DocumentSnapshot

if TYPE_CHECKING:
    from .controller_document_service import EditorControllerDocumentService


class DocumentLoadWorker:
    """后台加载一张编辑器文档，并把可并行的辅助 IO 收拢到这里。"""

    AUX_WORKERS = 4

    def __init__(self, service: "EditorControllerDocumentService", image_path: str):
        self.service = service
        self.controller = service.controller
        self.image_path = image_path

    @property
    def logger(self):
        return self.service.logger

    def load(self) -> DocumentSnapshot | DocumentLoadFailure:
        try:
            return self._load_snapshot()
        except Exception as e:
            self.logger.error(f"Error loading image data: {e}", exc_info=True)
            return DocumentLoadFailure(str(e))

    def _load_snapshot(self) -> DocumentSnapshot:
        source_path, display_image_path = self.service.resolve_editor_image_paths(self.image_path)

        image_resource = self.service.resource_manager.load_image(display_image_path)
        image = image_resource.image
        image_size = image.size

        aux_paths = {
            "json": find_json_path(source_path),
            "inpainted": find_inpainted_path(source_path),
            "paint_overlay": find_paint_overlay_path(source_path),
        }

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.AUX_WORKERS) as executor:
            futures = self._submit_aux_loads(
                executor,
                source_path,
                display_image_path,
                image,
                image_size,
                aux_paths,
            )

            # 后台预转 QImage 到 ImageResource(走 LRU);命中缓存时跳过
            self._ensure_qimage(image_resource, image)

            compare_image = futures["compare"].result()

            regions, raw_mask = futures["json"].result()

            inpainted_path, inpainted_image = futures["inpainted"].result()

            paint_overlay_path, paint_overlay_image = futures["paint_overlay"].result()

        return DocumentSnapshot(
            source_path=source_path,
            image=image,
            compare_image=compare_image,
            regions=regions,
            raw_mask=raw_mask,
            inpainted_path=inpainted_path,
            inpainted_image=inpainted_image,
            paint_overlay_path=paint_overlay_path,
            paint_overlay_image=paint_overlay_image,
        )

    def _submit_aux_loads(
        self,
        executor: concurrent.futures.ThreadPoolExecutor,
        source_path: str,
        display_image_path: str,
        image,
        image_size,
        aux_paths: dict[str, str | None],
    ) -> dict[str, concurrent.futures.Future]:
        return {
            "compare": executor.submit(self._load_compare_image, source_path, display_image_path, image, image_size),
            "json": executor.submit(self._load_regions_and_mask, source_path, aux_paths["json"]),
            "inpainted": executor.submit(self._load_inpainted_image, aux_paths["inpainted"], image_size),
            "paint_overlay": executor.submit(self._load_paint_overlay_image, aux_paths["paint_overlay"], image_size),
        }

    def _ensure_qimage(self, image_resource, image) -> None:
        if image_resource.qimage is not None:
            return
        image_path = getattr(image_resource, "path", None)
        if image_path:
            try:
                from PyQt6.QtGui import QImageReader

                reader = QImageReader(image_path)
                reader.setAutoTransform(True)
                qimage = reader.read()
                if not qimage.isNull():
                    image_resource.qimage = qimage
                    return
            except Exception as reader_err:
                self.logger.debug("QImageReader fallback for %s: %s", image_path, reader_err)
        try:
            from .image_utils import image_like_to_qimage

            image_resource.qimage = image_like_to_qimage(image)
        except Exception as conv_err:
            self.logger.warning(f"Failed to pre-convert QImage: {conv_err}")

    def _load_compare_image(self, source_path: str, display_image_path: str, image, image_size):
        if os.path.normpath(source_path) == os.path.normpath(display_image_path):
            return image
        try:
            return self.controller._load_detached_image_array(source_path, image_size)
        except Exception as compare_error:
            self.logger.warning(f"Error loading compare image: {compare_error}")
            return image

    def _load_regions_and_mask(self, source_path: str, json_path: str | None):
        if not json_path:
            return [], None
        regions, raw_mask, _ = self.service.file_service.load_translation_json(source_path)
        return regions, raw_mask

    def _load_inpainted_image(self, inpainted_path: str | None, image_size):
        if not inpainted_path:
            return None, None
        try:
            return inpainted_path, self.controller._load_detached_image_array(inpainted_path, image_size)
        except Exception as e:
            self.logger.error(f"Error loading inpainted image: {e}")
            return None, None

    def _load_paint_overlay_image(self, paint_overlay_path: str | None, image_size):
        if not paint_overlay_path:
            return None, None
        overlay_image = self._load_paint_overlay_array(paint_overlay_path, image_size)
        if overlay_image is None:
            return None, None
        return paint_overlay_path, overlay_image

    def _load_paint_overlay_array(self, overlay_path: str, target_size):
        """加载 paint overlay 图层并对齐到底图尺寸，返回 RGBA uint8 numpy 数组。"""
        try:
            with Image.open(overlay_path) as overlay_image:
                overlay_image.load()
                if overlay_image.mode != "RGBA":
                    converted = overlay_image.convert("RGBA")
                    overlay_image.close()
                    overlay_image = converted
                if target_size is not None and overlay_image.size != target_size:
                    resized = overlay_image.resize(target_size, Image.Resampling.NEAREST)
                    overlay_image.close()
                    overlay_image = resized
                array = np.array(overlay_image, dtype=np.uint8, copy=True)
            if array.ndim != 3 or array.shape[2] != 4:
                return None
            return array
        except Exception as e:
            self.logger.error(f"Failed to load paint overlay: {overlay_path} ({e})")
            return None
