"""Regression tests for Fluent combo-box popups hosted in stacked pages."""

import _bootstrap  # noqa: F401

import pytest

from PyQt6.QtWidgets import QApplication, QStackedWidget, QVBoxLayout, QWidget


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_combo_popup_uses_top_level_parent():
    from ui.widgets.wheel_filter import TopLevelComboBox

    host = QWidget()
    stack = QStackedWidget(host)
    page = QWidget()
    page.setLayout(QVBoxLayout())
    stack.addWidget(page)
    combo = TopLevelComboBox(page)
    combo.addItems(["one", "two"])
    page.layout().addWidget(combo)

    try:
        menu = combo._createComboMenu()
        assert menu.parentWidget() is host
        assert menu.parentWidget().isWindow()
        menu.deleteLater()
    finally:
        host.close()
        host.deleteLater()


def test_combo_popup_stops_animation_before_close():
    from PyQt6.QtCore import QAbstractAnimation, QPoint, QPropertyAnimation
    from ui.widgets.wheel_filter import TopLevelComboBox

    combo = TopLevelComboBox()
    combo.addItem("one")
    menu = combo._createComboMenu()
    animation = QPropertyAnimation(menu, b"pos", menu)
    animation.setStartValue(QPoint(0, 0))
    animation.setEndValue(QPoint(1, 1))
    animation.setDuration(5000)
    menu.aniManager = type("Manager", (), {"ani": animation})()
    menu.show()
    animation.start()
    menu.close()
    assert animation.state() == QAbstractAnimation.State.Stopped
    combo.deleteLater()
