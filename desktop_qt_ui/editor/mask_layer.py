from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PyQt6.QtGui import QPixmap

from .graphics_items import TransparentPixmapItem
from .image_utils import build_mask_display_frame

if TYPE_CHECKING:
    from .graphics_view import GraphicsView


class MaskLayer:
    """管理 raw/refined mask 覆盖层，隐藏时只标脏，显示时再生成 pixmap。"""

    MASK_TYPES = {"raw", "refined"}
    Z_VALUES = {"raw": 10, "refined": 11}

    def __init__(self, view: "GraphicsView"):
        self.view = view
        self.items: dict[str, TransparentPixmapItem | None] = {"raw": None, "refined": None}
        self.dirty = {"raw": False, "refined": False}

    def clear(self) -> None:
        for mask_type, item in list(self.items.items()):
            if item and item.scene():
                self.view.scene.removeItem(item)
            self.items[mask_type] = None
        self.dirty = {"raw": False, "refined": False}

    def on_mask_data_changed(self, mask_type: str, mask_array: Any) -> None:
        if mask_type not in self.MASK_TYPES:
            return

        item = self.items[mask_type]
        if mask_array is None or getattr(mask_array, "size", 0) == 0:
            self.dirty[mask_type] = False
            self._hide_item(item, clear_pixmap=True)
            return

        self.dirty[mask_type] = True
        current_display_type = self.view.model.get_display_mask_type()
        if current_display_type != mask_type:
            self._hide_item(item)
            return

        display_frame = build_mask_display_frame(mask_array, max_pixels=self.view.MASK_PREVIEW_MAX_PIXELS)
        if display_frame is None:
            return

        item = self._set_mask_pixmap(mask_type, QPixmap.fromImage(display_frame.qimage))

        self.view.viewport().update()

        if item:
            item.setVisible(True)
            self.dirty[mask_type] = False

    def on_display_mask_type_changed(self, mask_type: str) -> None:
        self._build_visible_mask_if_needed(mask_type)
        for item_type, item in self.items.items():
            if item:
                item.setVisible(mask_type == item_type)
        self.view.viewport().update()

    def _build_visible_mask_if_needed(self, mask_type: str) -> None:
        if mask_type == "raw":
            self._build_if_needed("raw", self.view.model.get_raw_mask())
        elif mask_type == "refined":
            self._build_if_needed("refined", self.view.model.get_refined_mask())

    def _build_if_needed(self, mask_type: str, mask_array: Any) -> None:
        if mask_array is None:
            return
        if self.items[mask_type] is None or self.dirty.get(mask_type, False):
            self.on_mask_data_changed(mask_type, mask_array)

    def _set_mask_pixmap(self, mask_type: str, pixmap: QPixmap):
        item = self._ensure_item(mask_type)
        item.setPixmap(pixmap)
        self.view._scale_mask_item(item)
        item.setVisible(self.view.model.get_display_mask_type() == mask_type)
        return item

    def _ensure_item(self, mask_type: str):
        item = self.items[mask_type]
        if item is not None and item.scene() is not None:
            return item

        item = TransparentPixmapItem()
        item.setZValue(self.Z_VALUES[mask_type])
        self.view.scene.addItem(item)
        self.items[mask_type] = item
        return item

    @staticmethod
    def _hide_item(item, *, clear_pixmap: bool = False) -> None:
        if item is None:
            return
        item.setVisible(False)
        if clear_pixmap:
            item.setPixmap(QPixmap())
