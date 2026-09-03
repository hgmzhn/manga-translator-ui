#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出服务
负责将编辑器中的内容导出为后端渲染的图片
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np
from manga_translator.image_formats import resolve_pil_image_format
from manga_translator.rendering.rich_text import is_redundant_plain_document
from manga_translator.utils import open_pil_image, save_pil_image
from manga_translator.utils.path_manager import (
    find_json_path,
    get_inpainted_path,
    get_json_path,
)
from PIL import Image

from editor.document_state import ExportBase
from editor.image_utils import image_like_to_pil, image_like_to_rgb_array
from utils.asyncio_cleanup import shutdown_event_loop
from utils.json_encoder import CustomJSONEncoder

if TYPE_CHECKING:
    from editor.controller_export_service import ExportJob


@dataclass(frozen=True, slots=True)
class BackendRenderResult:
    image: Image.Image
    generated_inpainted_image: Optional[np.ndarray]


@dataclass(frozen=True, slots=True)
class BackendExportResult:
    generated_inpainted_image: Optional[np.ndarray] = None
    error: Optional[str] = None


class ExportService:
    """导出服务类"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _prepare_paired_inpainted_payload(self, image: Any, base_size) -> np.ndarray:
        """Validate the paired snapshot already owned by the export job."""
        inpainted_rgb = image_like_to_rgb_array(image, copy=False)
        if inpainted_rgb is None:
            raise ValueError("paired export render image is empty")
        actual_size = (inpainted_rgb.shape[1], inpainted_rgb.shape[0])
        if actual_size != tuple(base_size):
            raise ValueError(
                f"paired export image size {actual_size} does not match source {tuple(base_size)}"
            )
        return inpainted_rgb

    def save_inpainted_image(
        self,
        source_image_path: str,
        inpainted_image: Any,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a generated inpaint image atomically."""
        if not source_image_path or inpainted_image is None:
            raise ValueError("generated inpaint image and source path are required")

        save_image = None
        source_image = None
        temporary = None
        try:
            if isinstance(inpainted_image, Image.Image):
                save_image = inpainted_image
            else:
                save_image = image_like_to_pil(inpainted_image)
                if save_image is None:
                    raise ValueError(
                        "generated inpaint image conversion returned no image"
                    )

            inpainted_path = get_inpainted_path(source_image_path, create_dir=True)
            save_quality = (config or {}).get("cli", {}).get("save_quality", 95)
            try:
                source_image = open_pil_image(
                    source_image_path, eager=False, apply_exif=False
                )
            except Exception as metadata_error:
                self.logger.warning(
                    f"读取原图元数据失败，将继续保存但不继承ICC: {source_image_path}, error={metadata_error}"
                )
            fd, temporary = tempfile.mkstemp(
                prefix=f".{os.path.basename(inpainted_path)}.",
                suffix=".tmp",
                dir=os.path.dirname(inpainted_path),
            )
            os.close(fd)
            save_pil_image(
                save_image,
                temporary,
                source_image=source_image,
                quality=save_quality,
                format=resolve_pil_image_format(inpainted_path),
            )
            os.replace(temporary, inpainted_path)
            temporary = None
            self.logger.info(f"已回写导出后的修复图: {inpainted_path}")
            return inpainted_path
        finally:
            if temporary:
                try:
                    os.remove(temporary)
                except FileNotFoundError:
                    pass
            if source_image is not None:
                try:
                    source_image.close()
                except Exception:
                    pass
            if save_image is not None and not isinstance(inpainted_image, Image.Image):
                try:
                    save_image.close()
                except Exception:
                    pass

    def delete_inpainted_image(self, source_image_path: str) -> bool:
        inpainted_path = get_inpainted_path(source_image_path, create_dir=False)
        try:
            os.remove(inpainted_path)
        except FileNotFoundError:
            return False
        self.logger.info(f"已删除过期修复图片: {inpainted_path}")
        return True

    def get_saved_export_directory(self, source_path: str) -> Optional[str]:
        json_path = find_json_path(source_path)
        if not json_path:
            return None
        metadata = self._read_existing_image_data(
            json_path,
            os.path.abspath(source_path),
        )
        saved = metadata.get("last_export_dir")
        if isinstance(saved, str) and saved.strip():
            return os.path.normpath(saved.strip())
        return None

    def build_output_path(self, config: Dict[str, Any], source_path: str) -> str:
        """Resolve the editor output path from persisted and current configuration."""
        cli = config.get("cli", {})
        app = config.get("app", {})
        output_dir = self.get_saved_export_directory(source_path)
        if not output_dir and cli.get("save_to_source_dir", False):
            output_dir = os.path.join(
                os.path.dirname(source_path),
                "manga_translator_work",
                "result",
            )
        if not output_dir:
            configured = app.get("last_output_path")
            output_dir = (
                configured
                if configured and os.path.exists(configured)
                else os.path.dirname(source_path)
            )
        os.makedirs(output_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(source_path))[0]
        output_format = str(cli.get("format") or "").strip().lower()
        if not output_format or output_format == "不指定":
            output_format = (
                os.path.splitext(source_path)[1].lower().lstrip(".") or "png"
            )
        return os.path.join(output_dir, f"{base_name}.{output_format}")

    def execute_export_job(self, job: "ExportJob") -> BackendExportResult:
        """Render, persist, and report one worker-owned export job."""
        rendered_image = None
        export_started = time.perf_counter()

        try:
            self.logger.info(f"开始导出图片到: {job.output_path}")
            output_dir = os.path.dirname(job.output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            source_image = job.export_base.source_image
            # 贴片整页预合成放到导出 worker 里做（GUI 线程不再分配整页缓冲）
            paste_overlay = None
            if job.paste_overlays:
                try:
                    from editor.paste_overlay_state import compose_paste_overlays

                    canvas_size = getattr(source_image, "size", None)
                    if not (
                        isinstance(canvas_size, (tuple, list))
                        and len(canvas_size) >= 2
                    ):
                        shape = getattr(source_image, "shape", None)
                        canvas_size = (
                            (shape[1], shape[0])
                            if shape is not None and len(shape) >= 2
                            else None
                        )
                    if canvas_size:
                        paste_overlay = compose_paste_overlays(
                            job.paste_overlays,
                            (int(canvas_size[0]), int(canvas_size[1])),
                        )
                except Exception as compose_error:
                    # 贴片预合成失败应终止导出（外层会转成失败的 BackendExportResult），
                    # 不能静默跳过贴片层却返回“成功”
                    self.logger.error(f"贴片预合成失败，导出终止: {compose_error}")
                    raise
            payload = self._build_load_text_payload(
                job.regions,
                job.export_base,
                job.config,
                base_size=source_image.size,
                paint_overlay=job.paint_overlay,
                stamp_overlay=job.stamp_overlay,
                paste_overlay=paste_overlay,
            )
            translator_params = self._prepare_translator_params(job.config)
            render_started = time.perf_counter()
            backend_result = self._execute_backend_render(
                source_image,
                job.source_path,
                payload,
                translator_params,
                job.config,
                output_path=job.output_path,
                source_image_path=job.source_path,
            )
            render_elapsed = time.perf_counter() - render_started
            rendered_image = backend_result.image
            generated = backend_result.generated_inpainted_image
            if job.export_base.kind == "backend_inpaint":
                if generated is None:
                    raise RuntimeError("后端修复未生成最终修复图")
                self.save_inpainted_image(job.source_path, generated, job.config)
            elif generated is not None:
                raise RuntimeError(
                    f"{job.export_base.kind} export unexpectedly generated an inpaint image"
                )

            save_started = time.perf_counter()
            self._save_rendered_image(
                rendered_image,
                job.output_path,
                job.config,
                source_image=source_image,
            )
            self.logger.info(
                f"图片已成功导出到: {job.output_path} "
                f"(总耗时 {time.perf_counter() - export_started:.2f}s, "
                f"后端渲染 {render_elapsed:.2f}s, "
                f"保存 {time.perf_counter() - save_started:.2f}s)"
            )
            return BackendExportResult(generated)
        except Exception as error:
            message = f"后端渲染导出失败: {error}"
            self.logger.error(message, exc_info=True)
            return BackendExportResult(error=message)
        finally:
            if rendered_image is not None:
                try:
                    rendered_image.close()
                except Exception:
                    pass

    def save_editor_project(
        self,
        image_path: str,
        regions_data: List[Dict[str, Any]],
        mask: Optional[np.ndarray],
        config: Optional[Dict[str, Any]],
        *,
        paint_overlay: Optional[np.ndarray] = None,
        stamp_overlay: Optional[np.ndarray] = None,
        paste_overlays: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Atomically persist the editor snapshot without render-only geometry changes."""
        json_path = find_json_path(image_path) or get_json_path(
            image_path,
            create_dir=True,
        )
        image_key = os.path.abspath(image_path)
        self._save_regions_data_internal(
            regions_data,
            json_path,
            image_key,
            mask,
            config,
            # 编辑器 translation 已是替换后终稿；白框坐标仍相对源区域中心。
            skip_text_replacements=True,
            preserve_existing_preprocess_flags=True,
            paint_overlay=paint_overlay,
            stamp_overlay=stamp_overlay,
            paste_overlays=paste_overlays,
        )
        return json_path

    def _save_regions_data(
        self,
        regions_data: List[Dict[str, Any]],
        json_path: str,
        mask: Optional[np.ndarray] = None,
        config: Optional[Dict[str, Any]] = None,
        paste_overlays: Optional[List[Dict[str, Any]]] = None,
    ):
        """保存区域数据到JSON文件，确保格式与TextBlock兼容（用于导出）"""
        # 使用文件名作为键（向后兼容）
        image_key = os.path.splitext(
            os.path.basename(json_path.replace("_translations.json", ""))
        )[0]
        self._save_regions_data_internal(
            regions_data,
            json_path,
            image_key,
            mask,
            config,
            skip_text_replacements=True,
            paste_overlays=paste_overlays,
        )

    def _read_existing_image_data(
        self, json_path: str, image_key: str
    ) -> Dict[str, Any]:
        """读取当前图片已有的 JSON 元数据，用于编辑器导出时保留底图来源标志。"""
        if not json_path or not os.path.exists(json_path):
            return {}

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception as e:
            self.logger.debug(f"读取已有JSON失败，无法继承预处理标志: {json_path}: {e}")
            return {}

        if not isinstance(existing_data, dict):
            return {}

        image_data = existing_data.get(image_key)
        if image_data is None:
            normalized_image_key = os.path.normcase(os.path.abspath(image_key))
            for existing_key, existing_value in existing_data.items():
                if not isinstance(existing_key, str):
                    continue
                try:
                    normalized_existing_key = os.path.normcase(
                        os.path.abspath(existing_key)
                    )
                except Exception:
                    continue
                if normalized_existing_key == normalized_image_key:
                    image_data = existing_value
                    break

        if image_data is None and existing_data:
            image_data = next(iter(existing_data.values()))

        return image_data if isinstance(image_data, dict) else {}

    def _preserve_existing_preprocess_flags(
        self,
        target_data: Dict[str, Any],
        existing_image_data: Dict[str, Any],
    ) -> None:
        """保留 editor_base 是否有效所需的上色/超分标志。"""
        if not existing_image_data:
            return

        if not target_data.get("upscale_ratio"):
            existing_upscale_ratio = existing_image_data.get("upscale_ratio")
            if existing_upscale_ratio:
                target_data["upscale_ratio"] = existing_upscale_ratio
                existing_upscaler = existing_image_data.get("upscaler")
                if existing_upscaler:
                    target_data["upscaler"] = existing_upscaler
                self.logger.info(
                    f"保留已有超分信息: ratio={existing_upscale_ratio}, upscaler={existing_upscaler}"
                )

        if not target_data.get("colorizer"):
            existing_colorizer = existing_image_data.get("colorizer")
            if existing_colorizer and str(existing_colorizer).lower() != "none":
                target_data["colorizer"] = existing_colorizer
                self.logger.info(f"保留已有上色信息: colorizer={existing_colorizer}")

        if not target_data.get("last_export_dir"):
            existing_export_dir = existing_image_data.get("last_export_dir")
            if isinstance(existing_export_dir, str) and existing_export_dir:
                target_data["last_export_dir"] = existing_export_dir
                self.logger.info(f"保留已有导出目录: {existing_export_dir}")

    def _normalize_regions_for_backend(
        self,
        regions_data: List[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """把编辑器 region 规整为 load_text 兼容的字典列表（lines 形状、颜色、方向等）。"""
        default_region_font_family = ""
        if config:
            render_config = config.get("render", {})
            default_region_font_family = render_config.get("font_family") or ""

        # 准备保存数据，确保数据格式正确
        save_data = []
        for idx, region in enumerate(regions_data):
            region_copy = region.copy()

            rich = region_copy.get("translation_rich")
            if rich is not None:
                try:
                    if is_redundant_plain_document(
                        rich, region_copy.get("translation", "")
                    ):
                        region_copy.pop("translation_rich", None)
                except (TypeError, ValueError):
                    # 后端加载边界负责把非法富文本降级；这里不扩大既有保存行为。
                    pass

            # 确保必要字段存在
            if "translation" not in region_copy:
                region_copy["translation"] = region_copy.get("text", "")

            # 确保lines字段存在且格式正确
            if "lines" not in region_copy:
                self.logger.warning(f"Region missing 'lines' field: {region_copy}")
                continue

            # 验证和转换lines数据格式
            lines_data = region_copy["lines"]
            if isinstance(lines_data, list):
                # 确保每个多边形都有足够的点
                valid_polygons = []
                for poly in lines_data:
                    if isinstance(poly, list) and len(poly) >= 4:
                        # 确保每个点都是[x, y]格式
                        valid_points = []
                        for point in poly:
                            if isinstance(point, (list, tuple)) and len(point) >= 2:
                                valid_points.append([float(point[0]), float(point[1])])
                            else:
                                self.logger.warning(
                                    f"Invalid point format in polygon: {point}"
                                )
                                break
                        else:
                            if len(valid_points) >= 4:
                                # 确保是矩形格式（4个点）
                                if len(valid_points) == 4:
                                    valid_polygons.append(valid_points)
                                else:
                                    # 如果超过4个点，取前4个点
                                    self.logger.warning(
                                        f"Polygon has {len(valid_points)} points, using first 4"
                                    )
                                    valid_polygons.append(valid_points[:4])
                    else:
                        self.logger.warning(f"Invalid polygon format: {poly}")

                if valid_polygons:
                    # 恢复到正确的 (N, 4, 2) 形状
                    region_copy["lines"] = np.array(valid_polygons, dtype=np.float64)
                else:
                    self.logger.warning(
                        f"No valid polygons found in region: {region_copy}"
                    )
                    continue
            elif isinstance(lines_data, np.ndarray):
                # 如果已经是numpy数组，验证并修正形状
                lines_arr = lines_data
                if lines_arr.ndim == 2 and lines_arr.shape == (4, 2):
                    # 单个多边形，需要添加一个维度变成 (1, 4, 2)
                    lines_arr = lines_arr.reshape(1, 4, 2)
                    self.logger.debug("Reshaped lines from (4, 2) to (1, 4, 2)")
                elif (
                    lines_arr.ndim != 3
                    or lines_arr.shape[1] != 4
                    or lines_arr.shape[2] != 2
                ):
                    self.logger.warning(
                        f"Invalid lines array shape: {lines_arr.shape}, expected (N, 4, 2)"
                    )
                    continue
                region_copy["lines"] = lines_arr.astype(np.float64)
            else:
                self.logger.warning(
                    f"Lines data is not a list or numpy array: {type(lines_data)}"
                )
                continue

            # --- Foreground Color ---
            # 优先使用 font_color (hex格式),如果没有才使用 fg_colors/fg_color (tuple格式)
            if "font_color" not in region_copy or region_copy["font_color"] is None:
                fg_tuple = region_copy.pop("fg_colors", None)
                if fg_tuple is None:
                    fg_tuple = region_copy.pop(
                        "fg_color", None
                    )  # Fallback for singular

                if isinstance(fg_tuple, (list, tuple)) and len(fg_tuple) == 3:
                    try:
                        r, g, b = fg_tuple
                        region_copy["font_color"] = (
                            f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                        )
                    except (ValueError, TypeError) as e:
                        self.logger.warning(
                            f"Could not convert fg_color tuple to hex for saving: {e}"
                        )
            else:
                # font_color 已存在,移除 fg_colors/fg_color 避免冲突
                region_copy.pop("fg_colors", None)
                region_copy.pop("fg_color", None)

            # --- Background/Stroke Color ---
            bg_tuple = region_copy.pop("bg_colors", None)
            if bg_tuple is None:
                bg_tuple = region_copy.pop("bg_color", None)  # Fallback

            # Ensure bg_color (singular) is present in the final dict if it exists
            if bg_tuple:
                region_copy["bg_color"] = bg_tuple

            # 确保其他必要字段存在
            if "texts" not in region_copy:
                region_copy["texts"] = [region_copy.get("text", "")]

            # 确保其他必要字段存在
            if "language" not in region_copy:
                region_copy["language"] = "unknown"
            if "font_size" not in region_copy:
                region_copy["font_size"] = 12
            if "angle" not in region_copy:
                region_copy["angle"] = 0
            if "target_lang" not in region_copy:
                region_copy["target_lang"] = "CHS"  # 默认目标语言

            if not region_copy.get("font_family") and default_region_font_family:
                region_copy["font_family"] = default_region_font_family
            region_copy.pop("font_path", None)

            # 转换 direction 值：'v' -> 'vertical', 'h' -> 'horizontal'
            if "direction" in region_copy:
                direction_value = region_copy["direction"]
                if direction_value == "v":
                    region_copy["direction"] = "vertical"
                elif direction_value == "h":
                    region_copy["direction"] = "horizontal"

            save_data.append(region_copy)

        return save_data

    def _build_load_text_payload(
        self,
        regions_data: List[Dict[str, Any]],
        export_base: ExportBase,
        config: Optional[Dict[str, Any]],
        base_size,
        paint_overlay: Optional[np.ndarray] = None,
        stamp_overlay: Optional[np.ndarray] = None,
        paste_overlay: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Build an in-memory payload whose base state is explicit and valid."""
        save_data = self._normalize_regions_for_backend(regions_data, config)
        payload = json.loads(
            json.dumps(
                {"regions": save_data}, ensure_ascii=False, cls=CustomJSONEncoder
            )
        )
        payload["editor_export_base_kind"] = export_base.kind
        if export_base.mask is not None:
            payload["mask_raw"] = np.asarray(export_base.mask)
            payload["mask_is_refined"] = True
        if export_base.kind == "paired":
            payload["inpainted_rgb"] = self._prepare_paired_inpainted_payload(
                export_base.render_image,
                base_size,
            )
        if paint_overlay is not None:
            payload["paint_overlay"] = np.asarray(paint_overlay)
        if stamp_overlay is not None:
            payload["stamp_overlay"] = np.asarray(stamp_overlay)
        if paste_overlay is not None:
            payload["paste_overlay"] = np.asarray(paste_overlay)
        self.logger.info(
            f"已构建内存导出载荷: 区域数={len(payload['regions'])}, 底图状态={export_base.kind}"
        )
        return payload

    def _save_regions_data_internal(
        self,
        regions_data: List[Dict[str, Any]],
        json_path: str,
        image_key: str,
        mask: Optional[np.ndarray] = None,
        config: Optional[Dict[str, Any]] = None,
        skip_text_replacements: bool = False,
        preserve_existing_preprocess_flags: bool = False,
        last_export_dir: Optional[str] = None,
        paint_overlay: Optional[np.ndarray] = None,
        stamp_overlay: Optional[np.ndarray] = None,
        paste_overlays: Optional[List[Dict[str, Any]]] = None,
    ):
        """保存区域数据到JSON文件的内部实现"""
        save_data = self._normalize_regions_for_backend(regions_data, config)

        # load_text模式期望的格式：字典，键为图片路径，值为包含regions的字典
        # image_key 由调用方传入（可以是完整路径或文件名）
        formatted_data = {image_key: {"regions": save_data}}

        # 添加超分和上色配置信息
        if config:
            upscale_config = config.get("upscale", {})
            upscale_ratio = upscale_config.get("upscale_ratio", 0)
            if upscale_ratio:
                formatted_data[image_key]["upscale_ratio"] = upscale_ratio
                upscaler = upscale_config.get("upscaler", "")
                if upscaler:
                    formatted_data[image_key]["upscaler"] = upscaler
                self.logger.info(
                    f"在JSON中记录超分信息: ratio={upscale_ratio}, upscaler={upscaler}"
                )

            colorizer_config = config.get("colorizer", {})
            colorizer = colorizer_config.get("colorizer", "")
            if colorizer and colorizer != "none":
                formatted_data[image_key]["colorizer"] = colorizer
                self.logger.info(f"在JSON中记录上色信息: colorizer={colorizer}")

        if preserve_existing_preprocess_flags:
            existing_image_data = self._read_existing_image_data(json_path, image_key)
            self._preserve_existing_preprocess_flags(
                formatted_data[image_key], existing_image_data
            )

        if last_export_dir:
            formatted_data[image_key]["last_export_dir"] = os.path.normpath(
                last_export_dir
            )

        # 如果有蒙版数据，则添加到JSON中
        if mask is not None:
            self.logger.info("在导出JSON中加入预计算的蒙版（已编辑的refined mask）。")
            # 使用base64编码保存蒙版，避免JSON文件过大
            import base64

            import cv2

            _, encoded_mask = cv2.imencode(".png", mask)
            mask_base64 = base64.b64encode(encoded_mask).decode("utf-8")
            formatted_data[image_key]["mask_raw"] = mask_base64
            formatted_data[image_key]["mask_is_refined"] = (
                True  # 标记为已精炼的蒙版，跳过后端的蒙版优化
            )
            self.logger.info(
                "蒙版已保存（base64编码），标记为已精炼，后端将跳过蒙版优化"
            )
        if skip_text_replacements:
            formatted_data[image_key]["skip_text_replacements"] = True

        # 画笔层/印章层以 base64 PNG 存入 JSON（RGBA），由后端渲染前合成
        for overlay_key, overlay in (
            ("paint_overlay", paint_overlay),
            ("stamp_overlay", stamp_overlay),
        ):
            if overlay is None:
                continue
            overlay_arr = np.asarray(overlay)
            if (
                overlay_arr.ndim != 3
                or overlay_arr.shape[2] != 4
                or not np.any(overlay_arr[..., 3])
            ):
                continue
            import base64

            import cv2

            bgra = cv2.cvtColor(
                overlay_arr.astype(np.uint8, copy=False), cv2.COLOR_RGBA2BGRA
            )
            ok, encoded = cv2.imencode(".png", bgra)
            if not ok:
                self.logger.warning(f"编码 {overlay_key} 失败，跳过写入")
                continue
            formatted_data[image_key][overlay_key] = base64.b64encode(encoded).decode(
                "utf-8"
            )
            self.logger.info(f"{overlay_key} 已保存（base64 PNG）")

        # 贴片（图块叠加）列表：纯 JSON 字典，图片内容为 base64 PNG（RGBA）
        if paste_overlays:
            try:
                from editor.paste_overlay_state import serialize_paste_overlays

                formatted_data[image_key]["paste_overlays"] = serialize_paste_overlays(
                    paste_overlays
                )
                self.logger.info(f"已写入贴片: {len(paste_overlays)} 个")
            except Exception as serialize_error:
                self.logger.error(f"序列化贴片失败，跳过写入: {serialize_error}")

        # 添加调试信息
        self.logger.info(f"保存区域数据到: {json_path}")
        self.logger.info(f"区域数量: {len(save_data)}")

        output_dir = os.path.dirname(os.path.abspath(json_path))
        os.makedirs(output_dir, exist_ok=True)
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(json_path)}.",
                suffix=".tmp",
                dir=output_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    formatted_data,
                    f,
                    indent=2,
                    ensure_ascii=False,
                    cls=CustomJSONEncoder,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, json_path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _save_rendered_image(
        self,
        image: Image.Image,
        output_path: str,
        config: Dict[str, Any],
        source_image: Optional[Image.Image] = None,
    ):
        """
        保存渲染后的图像到文件

        Args:
            image: 要保存的图像
            output_path: 输出路径
            config: 配置字典
        """
        temp_output_path = output_path + ".tmp"

        try:
            save_quality = config.get("cli", {}).get("save_quality", 95)
            image_format = resolve_pil_image_format(output_path)
            save_pil_image(
                image,
                temp_output_path,
                source_image=source_image,
                quality=save_quality,
                format=image_format,
            )

            # 确保文件已写入
            if not os.path.exists(temp_output_path):
                raise Exception(f"临时文件未成功创建: {temp_output_path}")

            # 原子性替换
            os.replace(temp_output_path, output_path)
            self.logger.info(f"图片已保存: {output_path}")

        except Exception as e:
            self.logger.error(f"保存图片失败: {e}")
            # 清理临时文件
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            raise

    def _prepare_translator_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """准备翻译器参数"""
        translator_params = {}

        render_config = config.get("render", {})
        font_family_value = render_config.get("font_family")
        if font_family_value:
            translator_params["font_family"] = font_family_value
            self.logger.info(f"透传字体 family: {font_family_value}")
        self.logger.info("字体按 family 透传；字体文件路径不写入任务参数")

        output_format = str(config.get("cli", {}).get("format") or "").strip().lower()
        if output_format and output_format != "不指定":
            translator_params["format"] = output_format
            self.logger.info(f"设置输出格式: {output_format}")

        # 提取并传递GPU配置
        cli_config = config.get("cli", {})
        if "use_gpu" in cli_config:
            translator_params["use_gpu"] = cli_config["use_gpu"]
            self.logger.info(f"设置GPU配置: use_gpu={cli_config['use_gpu']}")

        # 设置其他参数
        translator_params.update(config)
        translator_params["load_text"] = True  # 关键：启用加载文本模式
        translator_params["save_text"] = False  # 不保存文本

        # 添加调试日志
        self.logger.info(f"Config keys: {list(config.keys())}")
        if "upscale" in config:
            self.logger.info(f"Upscale config: {config['upscale']}")
        else:
            self.logger.warning("No upscale config found in config")
        if "colorizer" in config:
            self.logger.info(f"Colorizer config: {config['colorizer']}")
        else:
            self.logger.warning("No colorizer config found in config")

        # 关键：设置翻译器为none，跳过翻译步骤，直接渲染
        translator_params["translator"] = "none"
        self.logger.info(
            "设置翻译器为none，启用load_text模式，跳过翻译步骤，直接进行渲染"
        )

        return translator_params

    @staticmethod
    def _take_context_result(ctx) -> Optional[Image.Image]:
        """接管 ctx.result 的所有权（translate 内已保证结果独立，无需再整页拷贝）。"""
        result = getattr(ctx, "result", None)
        if result is None:
            return None
        ctx.result = None
        return result

    def _execute_backend_render(
        self,
        image: Image.Image,
        image_name: str,
        payload: Dict[str, Any],
        translator_params: Dict[str, Any],
        config: Dict[str, Any],
        progress_callback: Optional[callable] = None,
        output_path: str = None,
        source_image_path: str = None,
    ) -> BackendRenderResult:
        """Execute strict editor rendering and return any newly generated artifact."""
        try:
            from manga_translator.config import Config, RenderConfig
            from manga_translator.manga_translator import MangaTranslator

            if progress_callback:
                progress_callback("创建翻译器实例...")

            # 创建翻译器实例并注册内存载荷
            translator = MangaTranslator(params=translator_params)
            if payload is not None:
                translator.set_preloaded_load_text_payload(image_name, payload)

            image.name = image_name  # load_text 以该名字匹配内存载荷/查找辅助文件

            # 创建配置对象
            render_config = config.get("render", {}).copy()  # 使用copy避免修改原配置

            # 转换 direction 值：'v' -> 'vertical', 'h' -> 'horizontal'
            if "direction" in render_config:
                direction_value = render_config["direction"]
                if direction_value == "v":
                    render_config["direction"] = "vertical"
                elif direction_value == "h":
                    render_config["direction"] = "horizontal"

            render_config["font_color"] = None  # Explicitly disable global font color
            render_cfg = RenderConfig(**render_config)

            # 创建翻译器配置，设置为none以跳过翻译
            from manga_translator.config import (
                ColorizerConfig,
                InpainterConfig,
                TranslatorConfig,
                UpscaleConfig,
            )

            translator_cfg = TranslatorConfig(translator="none")

            # 从config中提取upscale、colorizer、inpainter和cli配置
            upscale_config = config.get("upscale", {})
            colorizer_config = config.get("colorizer", {})
            inpainter_config = config.get("inpainter", {})
            cli_config = config.get("cli", {})
            upscale_cfg = (
                UpscaleConfig(**upscale_config) if upscale_config else UpscaleConfig()
            )
            colorizer_cfg = (
                ColorizerConfig(**colorizer_config)
                if colorizer_config
                else ColorizerConfig()
            )
            inpainter_cfg = (
                InpainterConfig(**inpainter_config)
                if inpainter_config
                else InpainterConfig()
            )

            # 创建CliConfig对象（包含PSD导出配置）
            from manga_translator.config import CliConfig

            cli_cfg = CliConfig(**cli_config) if cli_config else CliConfig()

            self.logger.info(
                f"Creating Config with upscale_ratio={upscale_cfg.upscale_ratio}, colorizer={colorizer_cfg.colorizer}, inpainting_size={inpainter_cfg.inpainting_size}"
            )
            self.logger.info(
                f"PSD导出配置: export_editable_psd={cli_cfg.export_editable_psd}, font_family={render_cfg.font_family}, psd_script_only={cli_cfg.psd_script_only}"
            )

            cfg = Config(
                render=render_cfg,
                translator=translator_cfg,
                upscale=upscale_cfg,
                colorizer=colorizer_cfg,
                inpainter=inpainter_cfg,
                cli=cli_cfg,
            )

            if progress_callback:
                progress_callback("执行后端渲染...")

            # 执行翻译（实际是渲染）
            import sys

            # 在Windows上的工作线程中，需要手动初始化Windows Socket
            if sys.platform == "win32":
                # 使用ctypes直接调用WSAStartup
                import ctypes

                try:
                    WSADATA_SIZE = 400
                    wsa_data = ctypes.create_string_buffer(WSADATA_SIZE)
                    ws2_32 = ctypes.WinDLL("ws2_32")
                    ws2_32.WSAStartup(0x0202, wsa_data)
                except Exception:
                    pass

                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                ctx = loop.run_until_complete(
                    translator.translate(image, cfg, image_name=image_name)
                )
                translation_error = getattr(ctx, "translation_error", None) or getattr(
                    ctx, "error", None
                )
                if translation_error:
                    raise RuntimeError(
                        f"translator.translate returned translation_error: {translation_error}"
                    )
                generated_inpainted = getattr(
                    ctx,
                    "editor_export_generated_inpainted",
                    None,
                )
                if generated_inpainted is not None:
                    generated_inpainted = np.asarray(
                        generated_inpainted,
                        dtype=np.uint8,
                    )
                    ctx.editor_export_generated_inpainted = None
                export_kind = payload["editor_export_base_kind"]
                result_image = self._take_context_result(ctx)
                if result_image is None:
                    raise RuntimeError("translator returned no rendered image")
                if cfg.cli.export_editable_psd:
                    if export_kind == "backend_inpaint":
                        ctx.img_inpainted = generated_inpainted
                    elif export_kind == "paired":
                        ctx.img_inpainted = payload["inpainted_rgb"]
                    else:
                        ctx.img_inpainted = image_like_to_rgb_array(image, copy=True)
                # 导出可编辑PSD（如果启用）
                if cfg.cli.export_editable_psd:
                    try:
                        from manga_translator.utils.photoshop_export import (
                            get_psd_output_path,
                            photoshop_export,
                            resolve_photoshop_font,
                        )

                        psd_base_path = source_image_path or output_path
                        if not psd_base_path:
                            raise ValueError(
                                "PSD export requires a source or output path"
                            )
                        psd_path = get_psd_output_path(psd_base_path)

                        default_font = resolve_photoshop_font(cfg)
                        line_spacing = (
                            cfg.render.line_spacing
                            if hasattr(cfg.render, "line_spacing")
                            else None
                        )
                        script_only = cfg.cli.psd_script_only

                        image_path_for_psd = psd_base_path

                        self.logger.info(f"开始导出PSD: {psd_path}")
                        self.logger.info(
                            f"使用图片路径查找inpainted: {image_path_for_psd}"
                        )
                        photoshop_export(
                            psd_path,
                            ctx,
                            default_font,
                            image_path_for_psd,
                            False,
                            None,
                            line_spacing,
                            script_only,
                        )
                        self.logger.info(
                            f"✅ [PSD] 已导出可编辑PSD: {os.path.basename(psd_path)}"
                        )

                        if progress_callback:
                            progress_callback(
                                f"已导出PSD: {os.path.basename(psd_path)}"
                            )
                    except Exception as psd_err:
                        self.logger.error(f"导出PSD失败: {psd_err}")
                        import traceback

                        self.logger.error(traceback.format_exc())

                if generated_inpainted is not None:
                    generated_inpainted.setflags(write=False)
                return BackendRenderResult(result_image, generated_inpainted)

            except Exception as translate_error:
                self.logger.error(f"translator.translate执行失败: {translate_error}")
                self.logger.error(f"错误类型: {type(translate_error).__name__}")
                import traceback

                self.logger.error(f"完整堆栈:\n{traceback.format_exc()}")
                raise
            finally:
                shutdown_event_loop(
                    loop, logger=self.logger, label="backend export loop"
                )

        except Exception as e:
            self.logger.error(f"执行后端渲染时出错: {type(e).__name__}: {e}")
            import traceback

            self.logger.error(f"完整堆栈:\n{traceback.format_exc()}")
            raise

    def export_regions_json(
        self,
        regions_data: List[Dict[str, Any]],
        output_path: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """导出区域数据为JSON文件"""
        try:
            self._save_regions_data(regions_data, output_path, None, config)
            self.logger.info(f"区域数据已导出到: {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"导出区域数据失败: {e}")
            return False
