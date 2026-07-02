from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import ToolTipFilter, ToolTipPosition


class _HoverHintController(QObject):
    def __init__(self, widget: QWidget, text: str, delay_ms: int = 450):
        super().__init__(widget)
        self._widget = widget
        self._filter = ToolTipFilter(
            widget,
            showDelay=delay_ms,
            position=ToolTipPosition.BOTTOM,
        )
        widget.setToolTip(str(text or ""))
        widget.installEventFilter(self._filter)
        widget.destroyed.connect(self._cleanup)

    def _cleanup(self, *_args):
        self._hide_hint()
        self._widget = None

    def _hide_hint(self):
        if self._filter is not None:
            self._filter.hideToolTip()

    def set_text(self, text: str, delay_ms: int | None = None):
        if delay_ms is not None and delay_ms > 0:
            self._filter.setToolTipDelay(delay_ms)

        if self._widget is not None:
            self._widget.setToolTip(str(text or ""))

        self._hide_hint()


def set_hover_hint(widget: QWidget, text: str, delay_ms: int = 450):
    controller = getattr(widget, "_hover_hint_controller", None)
    if isinstance(controller, _HoverHintController):
        controller.set_text(text, delay_ms=delay_ms)
        return controller

    controller = _HoverHintController(widget, text, delay_ms=delay_ms)
    setattr(widget, "_hover_hint_controller", controller)
    return controller


def install_hover_hint(widget: QWidget, text: str, delay_ms: int = 450):
    return set_hover_hint(widget, text, delay_ms=delay_ms)
