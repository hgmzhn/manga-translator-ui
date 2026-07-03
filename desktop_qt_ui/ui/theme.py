"""
Theme runtime helpers.

This module owns:
- current theme tracking
- qfluentwidgets theme application helpers
"""

from __future__ import annotations

import logging

from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QWidget

from theme_registry import AVAILABLE_THEMES, DEFAULT_THEME


logger = logging.getLogger(__name__)

_VALID_THEMES = set(AVAILABLE_THEMES)
_DARK_THEMES = frozenset({"dark", "gray", "forest", "sunset", "rose"})
_THEME_ACCENT_COLORS = {
    "light": "#3F9BF5",
    "dark": "#4A8EE0",
    "gray": "#87A3C4",
    "ocean": "#4A8EE0",
    "forest": "#59B86D",
    "sunset": "#EE974C",
    "rose": "#D86E8C",
}
_CURRENT_THEME = DEFAULT_THEME


def normalize_theme(theme: str | None) -> str:
    return theme if theme in _VALID_THEMES else DEFAULT_THEME


def monospace_font(size: int = 11) -> QFont:
    preferred_families = ("Cascadia Mono", "Consolas", "Courier New")
    try:
        available_families = set(QFontDatabase.families())
    except Exception:
        available_families = set()

    family = next((name for name in preferred_families if name in available_families), "Consolas")
    font = QFont(family, size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def resolve_qfluent_theme(theme: str | None = None):
    from qfluentwidgets import Theme

    return Theme.DARK if normalize_theme(theme or _CURRENT_THEME) in _DARK_THEMES else Theme.LIGHT


def resolve_theme_color(theme: str | None = None) -> str:
    return _THEME_ACCENT_COLORS.get(normalize_theme(theme or _CURRENT_THEME), _THEME_ACCENT_COLORS[DEFAULT_THEME])


def set_current_theme(theme: str) -> None:
    global _CURRENT_THEME
    _CURRENT_THEME = normalize_theme(theme)


def get_current_theme() -> str:
    return _CURRENT_THEME


def is_dark_theme(theme: str | None = None) -> bool:
    return normalize_theme(theme or _CURRENT_THEME) in _DARK_THEMES


def apply_native_title_bar_theme(widget: QWidget, theme: str | None = None, logger=None) -> None:
    """Apply the qfluentwidgets theme mode to a native Windows title bar for a widget."""
    import sys

    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        from PyQt6.QtGui import QColor

        resolved_theme = normalize_theme(theme or _CURRENT_THEME)
        hwnd = int(widget.winId())
        if not hwnd:
            return

        dark_caption = is_dark_theme(resolved_theme)
        caption_hex = "#202020" if dark_caption else "#FFFFFF"
        text_hex = "#FFFFFF" if dark_caption else "#000000"
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020

        def _to_colorref(value: str):
            color = QColor(value)
            return wintypes.DWORD(color.red() | (color.green() << 8) | (color.blue() << 16))

        def _set_dwm_attr(attribute: int, data):
            return dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(attribute),
                ctypes.byref(data),
                ctypes.sizeof(data),
            )

        dark_mode = ctypes.c_int(1 if dark_caption else 0)
        result = _set_dwm_attr(DWMWA_USE_IMMERSIVE_DARK_MODE, dark_mode)
        if result != 0:
            _set_dwm_attr(DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1, dark_mode)

        _set_dwm_attr(DWMWA_CAPTION_COLOR, _to_colorref(caption_hex))
        # 不设置 DWMWA_BORDER_COLOR：让 Windows 使用原生默认边框，配色更自然。
        _set_dwm_attr(DWMWA_TEXT_COLOR, _to_colorref(text_hex))

        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(0),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    except Exception as exc:
        if logger is not None:
            logger.debug(f"应用原生标题栏主题失败: {exc}")


def apply_application_theme(theme: str, app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return

    resolved_theme = normalize_theme(theme)
    qfluent_theme = resolve_qfluent_theme(resolved_theme)

    set_current_theme(resolved_theme)

    from qfluentwidgets import setTheme, setThemeColor

    setTheme(qfluent_theme)
    accent = resolve_theme_color(resolved_theme)
    setThemeColor(accent)
