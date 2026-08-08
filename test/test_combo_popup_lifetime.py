import _bootstrap  # noqa: F401

from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QPropertyAnimation

from ui.widgets.wheel_filter import _stop_popup_animation


def test_stop_popup_animation_tolerates_deleted_qt_animation():
    animation = QPropertyAnimation()
    menu = SimpleNamespace(aniManager=SimpleNamespace(ani=animation))
    sip.delete(animation)

    assert sip.isdeleted(animation)
    _stop_popup_animation(menu)
