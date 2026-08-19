from __future__ import annotations

import copy

import numpy as np
from PyQt6.QtCore import QTimer
from editor import text_renderer_backend
from editor.region_geometry_state import RegionGeometryState
from editor.region_render_snapshot import RegionRenderSnapshot
from editor.render_layout_pipeline import (
    calculate_region_dst_points,
    resolve_region_layout_parameters,
)
from editor.text_render_pipeline import (
    build_region_render_params as pipeline_build_region_render_params,
)
from editor.text_render_pipeline import (
    build_text_block_from_region as pipeline_build_text_block_from_region,
)
from editor.text_render_pipeline import (
    clear_region_text,
    render_region_text,
)
from services import get_render_parameter_service

from .graphics_items import RegionTextItem


class GraphicsViewRenderingMixin:
    def _schedule_render_update(self) -> None:
        # 场景里还没有 region item 时（切图/清空后的首次重建）立即执行，避免防抖延迟出现空白帧；
        # 已有内容时用防抖合并连续的整批重建请求。
        immediate = not self._region_items
        if immediate:
            if self.render_debounce_timer.isActive():
                self.render_debounce_timer.stop()
            if not self._immediate_render_update_pending:
                self._immediate_render_update_pending = True
                QTimer.singleShot(0, self._perform_render_update)
            return
        if self._immediate_render_update_pending:
            return
        self.render_debounce_timer.start()

    def on_regions_changed(self, change):
        self.clear_region_style_preview()
        if change.kind == "updated":
            for region_index in change.indices:
                if 0 <= region_index < len(self._region_items):
                    self._perform_single_item_update(
                        region_index,
                        edit_kind=self._consume_pending_geometry_edit(region_index),
                    )
        elif change.kind == "inserted":
            self._handle_regions_inserted(change.indices)
        elif change.kind == "removed":
            self._handle_regions_removed(change.indices)
        else:
            self._clear_pending_geometry_edits()
            self.render_coordinator.clear_render_snapshots()
            self._schedule_render_update()

    def _renumber_region_items_from(self, start_index: int) -> None:
        for region_index in range(max(0, int(start_index)), len(self._region_items)):
            item = self._region_items[region_index]
            if item is not None:
                item.region_index = region_index

    def _handle_regions_inserted(self, indices) -> None:
        regions = self.model.get_regions()
        if self._image_item is None:
            self._schedule_render_update()
            return

        first_changed = len(self._region_items)
        for index in sorted(indices):
            if not (0 <= index < len(regions)):
                continue
            item = RegionTextItem(
                regions[index],
                index,
                geometry_callback=self._on_region_geometry_changed,
            )
            item.set_snap_enabled(getattr(self, "_snap_enabled", False))
            item.set_image_item(self._image_item)
            item.setZValue(100)
            self.scene.addItem(item)
            insert_at = max(0, min(index, len(self._region_items)))
            self._region_items.insert(insert_at, item)
            self.render_coordinator.insert_region(insert_at)
            first_changed = min(first_changed, insert_at)

        self._renumber_region_items_from(first_changed)
        for index in sorted(indices):
            if 0 <= index < len(self._region_items):
                self._perform_single_item_update(index)
        self.on_region_display_mode_changed(
            self.model.get_region_display_mode(), render_missing=False
        )
        self.scene.update()

    def _handle_regions_removed(self, indices) -> None:
        first_changed = len(self._region_items)
        for index in sorted(indices, reverse=True):
            if not (0 <= index < len(self._region_items)):
                continue
            item = self._region_items.pop(index)
            try:
                if item and hasattr(item, "scene") and item.scene():
                    self.scene.removeItem(item)
            except (RuntimeError, AttributeError):
                pass
            self.render_coordinator.remove_region(index)
            first_changed = min(first_changed, index)

        self._renumber_region_items_from(first_changed)
        self._clear_pending_geometry_edits()
        self.scene.update()

    def _log_layout_failure(
        self,
        index: int,
        text_block,
        line_spacing,
        letter_spacing,
        angle,
        error: Exception,
    ) -> None:
        self.logger.warning(
            "Failed to calculate dst_points for region %s: %s "
            "(font_size=%s, horizontal=%s, center=%s, xyxy=%s, "
            "line_spacing=%s, letter_spacing=%s, angle=%s)",
            index,
            error,
            getattr(text_block, "font_size", None),
            getattr(text_block, "horizontal", None),
            getattr(text_block, "center", None),
            getattr(text_block, "xyxy", None),
            line_spacing,
            letter_spacing,
            angle,
        )

    def _values_equal(self, left, right) -> bool:
        try:
            if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
                return np.array_equal(np.asarray(left), np.asarray(right))
            return left == right
        except Exception:
            return False

    def _infer_geometry_edit_kind(self, region_index: int, new_region_data: dict) -> str:
        old_region_data = self.model.get_region_by_index(region_index)
        if not isinstance(old_region_data, dict) or not isinstance(new_region_data, dict):
            return "unknown"

        if not self._values_equal(old_region_data.get("angle"), new_region_data.get("angle")):
            return "rotate"
        if not self._values_equal(old_region_data.get("center"), new_region_data.get("center")):
            return "move"
        if not self._values_equal(old_region_data.get("lines"), new_region_data.get("lines")):
            return "shape"

        white_changed = (
            not self._values_equal(
                old_region_data.get("white_frame_rect_local"),
                new_region_data.get("white_frame_rect_local"),
            )
            or not self._values_equal(
                old_region_data.get("has_custom_white_frame", False),
                new_region_data.get("has_custom_white_frame", False),
            )
        )
        if white_changed:
            return "white_frame"
        return "other"

    def _perform_single_item_update(self, index, edit_kind: str | None = None):
        try:
            if not (0 <= index < len(self._region_items)):
                return

            region_data = self.model.get_region_by_index(index)
            item = self._region_items[index]
            if not region_data or item is None or item.scene() is None:
                return

            if edit_kind is None:
                edit_kind = self._consume_pending_geometry_edit(index)

            # white_frame 编辑时 item.geo 在拖动中已经更新过，跳过 update_from_data。
            # 其他编辑（rotate/move/shape/other）需要从 model 同步到 item。
            if edit_kind != "white_frame":
                region_for_item = region_data.copy()
                if (
                    hasattr(item, "geo")
                    and item.geo is not None
                    and self._region_geometry_matches_item(region_data, item)
                ):
                    try:
                        region_for_item.update(item.geo.to_persisted_state_patch())
                        region_for_item["center"] = list(item.geo.center)
                    except Exception:
                        pass
                item.update_from_data(region_for_item)

            # dst 永远由 calc_box_from_font(字号) 反算 + render_center 定位（snapshot 完成），
            # 不再需要 override —— 白框只负责 UI 显示和提供渲染中心。
            self._recalculate_single_region_render_data(index)

            self._update_single_region_text_visual(index)
            if item.scene() is not None:
                item.update()
        except (RuntimeError, AttributeError) as e:
            self.logger.warning("Item update failed for region %s: %s", index, e)

    def _set_pending_geometry_edit(self, region_index: int, edit_kind: str):
        self._pending_geometry_edit_kinds[int(region_index)] = str(edit_kind)

    def _consume_pending_geometry_edit(self, region_index: int) -> str | None:
        return self._pending_geometry_edit_kinds.pop(int(region_index), None)

    def _clear_pending_geometry_edits(self):
        self._pending_geometry_edit_kinds.clear()

    def _region_geometry_matches_item(self, region_data: dict, item: RegionTextItem) -> bool:
        try:
            if not hasattr(item, "geo") or item.geo is None:
                return False

            if not self._values_equal(region_data.get("center"), list(item.geo.center)):
                return False
            if not self._values_equal(region_data.get("angle"), float(item.geo.angle)):
                return False
            if not self._values_equal(region_data.get("lines"), item.geo.lines):
                return False
            if not self._values_equal(
                region_data.get("has_custom_white_frame", False),
                bool(item.geo.has_custom_white_frame),
            ):
                return False

            model_wf = region_data.get("white_frame_rect_local")
            item_wf = item.geo.custom_white_frame_local
            if model_wf is None and item_wf is None:
                return True
            return self._values_equal(model_wf, item_wf)
        except Exception:
            return False

    def _ensure_render_cache_size(self, index: int):
        self.render_coordinator.ensure_region_capacity(index)

    def _build_render_box_patch(self, region_data: dict, dst_points) -> dict:
        geo_state = RegionGeometryState.from_region_data(region_data)
        geo_state.set_render_box(dst_points)
        return geo_state.to_render_box_patch()

    def _build_derived_region(self, region_data: dict, dst_points) -> dict | None:
        """基于渲染结果计算 region 的派生字段写回；无实际变化时返回 None。

        不修改传入的 region_data；有变化时返回携带新 render_box 的深拷贝。
        """
        patch = self._build_render_box_patch(region_data, dst_points)
        new_render_box = patch.get("render_box_rect_local")
        if self._values_equal(region_data.get("render_box_rect_local"), new_render_box):
            return None
        updated = copy.deepcopy(region_data)
        updated.update(patch)
        return updated

    def _persist_single_render_box(self, index: int, dst_points):
        region_data = self.model.get_region_by_index(index)
        if not isinstance(region_data, dict):
            return
        updated = self._build_derived_region(region_data, dst_points)
        if updated is not None:
            self.model.store_derived_regions({index: updated})

    def _persist_render_boxes(self, regions: list[dict], dst_points_list: list):
        updates: dict[int, dict] = {}
        for index, dst_points in enumerate(dst_points_list):
            if not (0 <= index < len(regions)):
                continue
            region_data = regions[index]
            if not isinstance(region_data, dict):
                continue
            updated = self._build_derived_region(region_data, dst_points)
            if updated is not None:
                updates[index] = updated
        if updates:
            self.model.store_derived_regions(updates)

    def _build_render_snapshot(self, index: int, region_data: dict, item: RegionTextItem | None) -> RegionRenderSnapshot:
        preview_patch = getattr(self, "_font_preview_overrides", {}).get(index)
        if preview_patch:
            region_data = copy.deepcopy(region_data)
            region_data.update(preview_patch)
        geo_state = item.geo if (item is not None and hasattr(item, "geo")) else None
        return RegionRenderSnapshot.from_sources(
            region_index=index,
            region_data=region_data,
            geo_state=geo_state,
        )

    def _render_region_text_visual(self, index: int):
        if not (0 <= index < len(self._region_items)):
            return
        item = self._region_items[index]
        if item is None or not hasattr(item, "scene") or item.scene() is None:
            return
        if not hasattr(item, "text_item") or item.text_item is None:
            return

        if self.model.get_region_display_mode() in ["box_only", "none"]:
            item.text_item.setVisible(False)
            return
        item.text_item.setVisible(True)

        if index >= len(self._text_blocks_cache) or index >= len(self._dst_points_cache):
            return
        text_block = self._text_blocks_cache[index]
        dst_points = self._dst_points_cache[index]
        if text_block is None or dst_points is None:
            clear_region_text(item)
            return

        snapshot = self._render_snapshot_cache[index] if index < len(self._render_snapshot_cache) else None
        if snapshot is None:
            region_data = self.model.get_region_by_index(index)
            if not region_data:
                return
            snapshot = self._build_render_snapshot(index, region_data, item)
            self._ensure_render_cache_size(index)
            self._render_snapshot_cache[index] = snapshot

        region_data_for_render = snapshot.text_block_input()
        unrotated_text_block = pipeline_build_text_block_from_region(
            region_data_for_render,
            font_size_override=getattr(text_block, "font_size", None),
            log_tag=f" for region {index}",
        )
        if unrotated_text_block is None:
            clear_region_text(item)
            return

        render_parameter_service = get_render_parameter_service()
        render_params = pipeline_build_region_render_params(
            render_parameter_service,
            text_renderer_backend,
            index,
            snapshot.style_input(),
            unrotated_text_block,
        )

        render_result = render_region_text(
            text_renderer_backend,
            unrotated_text_block,
            dst_points,
            render_params,
            len(self._text_blocks_cache),
        )

        if render_result:
            if len(render_result) >= 3:
                pixmap, pos, actual_dst_points = render_result[:3]
            else:
                pixmap, pos = render_result
                actual_dst_points = dst_points
            item.set_dst_points(actual_dst_points)
            if index not in getattr(self, "_font_preview_overrides", {}):
                self._persist_single_render_box(index, actual_dst_points)
            item.update_text_pixmap(
                pixmap,
                pos,
                0,
                None,
                render_center=snapshot.render_center,
            )
        else:
            clear_region_text(item)

    def preview_region_style(self, region_indices, patch: dict) -> None:
        """Render a temporary style preview without changing model data or history."""
        if not isinstance(patch, dict) or not patch:
            return
        regions = self.model.get_regions()
        overrides = {}
        for raw_index in region_indices or []:
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(regions):
                overrides[index] = copy.deepcopy(patch)
        if not overrides:
            return
        self._font_preview_overrides = overrides
        for index in overrides:
            self._recalculate_single_region_render_data(index)
            self._update_single_region_text_visual(index)
        self.scene.update()

    def clear_region_style_preview(self) -> None:
        """Restore model-backed rendering after a temporary style preview."""
        if not getattr(self, "_font_preview_overrides", None):
            return
        indices = list(self._font_preview_overrides)
        self._font_preview_overrides = {}
        for index in indices:
            self._recalculate_single_region_render_data(index)
            self._update_single_region_text_visual(index)
        self.scene.update()

    def _perform_render_update(self):
        self._immediate_render_update_pending = False
        self.selection_manager.suppress_forward_sync(True)
        self.setUpdatesEnabled(False)
        try:
            regions = self.model.get_regions()
            current_items = self._region_items
            first_new_item_index = len(current_items)

            while len(current_items) > len(regions):
                item = current_items.pop()
                try:
                    if item and hasattr(item, "scene") and item.scene():
                        self.scene.removeItem(item)
                except (RuntimeError, AttributeError):
                    pass
            self.render_coordinator.trim_regions(len(regions))

            while len(current_items) < len(regions):
                i = len(current_items)
                item = RegionTextItem(
                    regions[i],
                    i,
                    geometry_callback=self._on_region_geometry_changed,
                )
                item.set_snap_enabled(getattr(self, "_snap_enabled", False))
                item.set_image_item(self._image_item)
                item.setZValue(100)
                self.scene.addItem(item)
                current_items.append(item)

            for i, region_data in enumerate(regions):
                if i < len(current_items):
                    item = current_items[i]
                    item.set_image_item(self._image_item)
                    item.region_index = i
                    # 本轮刚创建的 item 已经用同一份 region_data 初始化过，避免重复
                    # prepareGeometryChange/update/scene invalidation。
                    if i >= first_new_item_index:
                        continue
                    item.update_from_data(region_data)
            self.recalculate_render_data()
            self.on_region_display_mode_changed(
                self.model.get_region_display_mode(), render_missing=False
            )
        except Exception as e:
            self.logger.error("Render update failed: %s", e, exc_info=True)
        finally:
            self.selection_manager.suppress_forward_sync(False)
            self.selection_manager.restore_selection_after_rebuild()
            self.setUpdatesEnabled(True)

    def _update_text_visuals(self):
        try:
            if self.model.get_region_display_mode() in ["box_only", "none"]:
                for item in self._region_items:
                    if item and hasattr(item, "text_item") and item.text_item and item.scene():
                        item.text_item.setVisible(False)
                return

            for i in range(min(len(self._region_items), len(self._text_blocks_cache), len(self._dst_points_cache))):
                self._render_region_text_visual(i)

        except (RuntimeError, AttributeError) as e:
            self.logger.warning("Text visuals update failed: %s", e)

    def _recalculate_single_region_render_data(self, index, override_dst_points=None):
        regions = self.model.get_regions()
        if self._image_item is None or not regions or not (0 <= index < len(regions)):
            return

        self._ensure_render_cache_size(index)

        item = self._region_items[index] if 0 <= index < len(self._region_items) else None
        snapshot = self._build_render_snapshot(index, regions[index], item)
        self._render_snapshot_cache[index] = snapshot

        region_dict = snapshot.text_block_input()
        text_block = pipeline_build_text_block_from_region(region_dict, log_tag=f" for region {index}")
        self._text_blocks_cache[index] = text_block
        if text_block is None:
            self._dst_points_cache[index] = None
            return

        render_parameter_service = get_render_parameter_service()
        layout_params = None
        try:
            layout_params = resolve_region_layout_parameters(
                render_parameter_service,
                index,
                snapshot.style_input(),
                text_block,
            )
            self._dst_points_cache[index] = calculate_region_dst_points(
                text_block,
                layout_params,
                override_dst_points=override_dst_points,
            )
        except Exception as e:
            self._log_layout_failure(
                index,
                text_block,
                getattr(layout_params, "line_spacing", None),
                getattr(layout_params, "letter_spacing", None),
                region_dict.get("angle"),
                e,
            )
            self._dst_points_cache[index] = None

    def _update_single_region_text_visual(self, index):
        try:
            self._render_region_text_visual(index)
        except (RuntimeError, AttributeError) as e:
            self.logger.warning("Text visual update failed for region %s: %s", index, e)
        except Exception as e:
            self.logger.error("Text visual update failed for region %s: %s", index, e, exc_info=True)

    def recalculate_render_data(self):
        regions = self.model.get_regions()
        if self._image_item is None or not regions:
            self.render_coordinator.reset()
            return

        snapshots: list[RegionRenderSnapshot] = []
        for i, region_dict in enumerate(regions):
            item = self._region_items[i] if i < len(self._region_items) else None
            snapshots.append(self._build_render_snapshot(i, region_dict, item))
        self._render_snapshot_cache = snapshots

        self._text_blocks_cache = [
            pipeline_build_text_block_from_region(snapshot.text_block_input(), log_tag=f" for region {i}")
            for i, snapshot in enumerate(snapshots)
        ]

        render_parameter_service = get_render_parameter_service()

        dst_points_list = []
        for i, text_block in enumerate(self._text_blocks_cache):
            if text_block is None:
                dst_points_list.append(None)
                continue

            snapshot = snapshots[i] if i < len(snapshots) else None
            layout_params = None
            try:
                region_dict = snapshot.region_data if snapshot is not None else {}
                layout_params = resolve_region_layout_parameters(
                    render_parameter_service,
                    i,
                    region_dict,
                    text_block,
                )
                dst_points_list.append(
                    calculate_region_dst_points(
                        text_block,
                        layout_params,
                    )
                )
            except Exception as e:
                self._log_layout_failure(
                    i,
                    text_block,
                    getattr(layout_params, "line_spacing", None),
                    getattr(layout_params, "letter_spacing", None),
                    regions[i].get("angle") if i < len(regions) else "N/A",
                    e,
                )
                dst_points_list.append(None)

        self._dst_points_cache = dst_points_list
        self._update_text_visuals()
        self.scene.update()

    def _on_region_geometry_changed(self, region_index, new_region_data):
        self._set_pending_geometry_edit(
            region_index,
            self._infer_geometry_edit_kind(region_index, new_region_data),
        )
        self.region_geometry_changed.emit(region_index, new_region_data)
