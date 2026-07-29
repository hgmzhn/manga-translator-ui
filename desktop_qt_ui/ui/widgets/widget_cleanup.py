"""Small Qt widget cleanup helpers shared by dynamic layouts."""

from PyQt6.QtWidgets import QWidget


def delete_widget(widget: QWidget | None) -> None:
    """Hide a widget immediately and let Qt destroy it at the event-loop boundary."""
    if widget is None:
        return
    try:
        widget.hide()
    except RuntimeError:
        return

    for candidate in (widget, *widget.findChildren(QWidget)):
        hide_popup = getattr(candidate, "hidePopup", None)
        if not callable(hide_popup):
            continue
        try:
            hide_popup()
        except RuntimeError:
            pass

    cleanup_event_filters = getattr(widget, "_cleanup_event_filters", None)
    if callable(cleanup_event_filters):
        cleanup_event_filters()
    widget.deleteLater()


def clear_layout(layout, *, restore_stretch: bool = False) -> None:
    """Recursively clear widgets and child layouts without reparenting widgets."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            delete_widget(widget)
            continue
        child_layout = item.layout()
        if child_layout is not None:
            clear_layout(child_layout)
            child_layout.deleteLater()
    if restore_stretch:
        layout.addStretch(1)
