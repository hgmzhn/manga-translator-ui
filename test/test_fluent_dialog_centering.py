import _bootstrap  # noqa: F401

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication, QWidget

from ui.secondary_pages.fluent_dialog import FluentSecondaryDialog, _centered_window_position


def test_centered_window_position_uses_owner_center():
    available = QRect(0, 0, 1920, 1080)
    owner = QRect(160, 90, 1600, 900)

    assert _centered_window_position(QSize(1000, 700), owner, available) == QPoint(459, 189)


def test_centered_window_position_stays_inside_work_area():
    available = QRect(0, 0, 1920, 1080)
    owner = QRect(1800, 900, 200, 200)

    assert _centered_window_position(QSize(800, 600), owner, available) == QPoint(1120, 480)
    assert _centered_window_position(QSize(2200, 1200), owner, available) == QPoint(0, 0)


def test_centered_window_position_supports_negative_screen_coordinates():
    available = QRect(-1920, 0, 1920, 1080)
    owner = QRect(-1600, 100, 1200, 800)

    assert _centered_window_position(QSize(800, 600), owner, available) == QPoint(-1400, 199)


def test_fluent_secondary_dialog_centers_when_shown():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    dialog = None
    try:
        owner.resize(600, 400)
        owner.move(50, 60)
        owner.show()
        app.processEvents()

        dialog = FluentSecondaryDialog(owner)
        dialog.resize(240, 160)
        dialog.show()
        app.processEvents()

        expected = _centered_window_position(
            dialog.size(),
            owner.frameGeometry(),
            owner.screen().availableGeometry(),
        )
        assert dialog.pos() == expected
    finally:
        if dialog is not None:
            dialog.close()
        owner.close()
