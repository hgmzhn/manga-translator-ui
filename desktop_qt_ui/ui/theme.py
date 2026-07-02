"""
Theme runtime helpers.

This module owns:
- current theme tracking
- palette generation
- theme application helpers
- lightweight repolish helpers for local widget stylesheets
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication, QToolTip, QWidget
from theme_registry import AVAILABLE_THEMES, DEFAULT_THEME, THEME_OPTIONS

from ui.theme_tokens import (
    DARK_THEMES,
    _ACCENT_BASES,
    _DARK_THEME_BASES,
    _THEME_PROFILES,
    _THEME_TOKEN_OVERRIDES,
    _THEMES,
)


def normalize_theme(theme: str) -> str:
    return theme if theme in _THEME_PROFILES else DEFAULT_THEME


def monospace_font(size: int = 11) -> QFont:
    font = QFont("Consolas", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def resolve_theme_variant(theme: str) -> tuple[str, str]:
    profile = _THEME_PROFILES[normalize_theme(theme)]
    return profile["base_theme"], profile["accent"]


def get_theme_colors(theme: str) -> dict:
    """Return semantic tokens for a theme layered with per-theme surface and accent tokens."""
    normalized_theme = normalize_theme(theme)
    base_theme, accent = resolve_theme_variant(normalized_theme)
    colors = dict(_THEMES[base_theme])
    colors.update(_THEME_TOKEN_OVERRIDES.get(normalized_theme, {}))
    colors.update(_build_accent_overrides(accent, dark_theme=base_theme in _DARK_THEME_BASES))
    return colors


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Unsupported hex color: {value}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _mix_hex(source: str, target: str, amount: float) -> str:
    source_rgb = _hex_to_rgb(source)
    target_rgb = _hex_to_rgb(target)
    mixed = tuple(
        max(0, min(255, round(src + (dst - src) * amount)))
        for src, dst in zip(source_rgb, target_rgb)
    )
    return _rgb_to_hex(mixed)


def _lighten(color: str, amount: float) -> str:
    return _mix_hex(color, "#FFFFFF", amount)


def _darken(color: str, amount: float) -> str:
    return _mix_hex(color, "#000000", amount)


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


def _build_accent_overrides(accent: str, *, dark_theme: bool) -> dict:
    base = _ACCENT_BASES[accent]

    strong = _lighten(base, 0.16 if dark_theme else 0.04)
    soft = _lighten(base, 0.08 if dark_theme else 0.18)
    deep = _darken(base, 0.22 if dark_theme else 0.16)
    deeper = _darken(base, 0.32 if dark_theme else 0.24)

    return {
        "text_divider_dot": strong,
        "border_input_focus": _rgba(base, 0.92 if dark_theme else 0.80),
        "border_tab_selected": _rgba(strong, 0.76 if dark_theme else 0.70),
        "btn_soft_checked_bg": _rgba(base, 0.30 if dark_theme else 0.18),
        "btn_soft_checked_border": _rgba(strong, 0.76 if dark_theme else 0.78),
        "btn_primary_bg": base,
        "btn_primary_hover": soft,
        "btn_primary_pressed": deep,
        "btn_primary_border": _rgba(strong, 0.74 if dark_theme else 0.62),
        "btn_bg": _rgba(base, 0.94 if dark_theme else 0.92),
        "btn_border": _rgba(strong, 0.60 if dark_theme else 0.48),
        "btn_hover": _rgba(soft, 0.98 if dark_theme else 0.96),
        "btn_pressed": _rgba(deeper, 0.98 if dark_theme else 0.98),
        "btn_checked_bg": _rgba(deep, 0.90 if dark_theme else 0.92),
        "btn_checked_border": _rgba(strong, 0.78 if dark_theme else 0.78),
        "btn_settings_bg": _rgba(base, 0.22 if dark_theme else 0.14),
        "btn_settings_border": _rgba(strong, 0.42 if dark_theme else 0.30),
        "btn_settings_hover": _rgba(soft, 0.34 if dark_theme else 0.24),
        "btn_settings_hover_border": _rgba(strong, 0.62 if dark_theme else 0.48),
        "nav_hover_bg": _rgba(base, 0.28 if dark_theme else 0.10),
        "nav_hover_border": _rgba(strong, 0.38 if dark_theme else 0.24),
        "nav_checked_bg": _rgba(base, 0.42 if dark_theme else 0.16),
        "nav_checked_border": _rgba(strong, 0.82 if dark_theme else 0.58),
        "cta_gradient_start": strong,
        "cta_gradient_end": soft,
        "cta_border": _rgba(_lighten(base, 0.34), 0.90 if dark_theme else 0.82),
        "cta_hover_start": _lighten(base, 0.24 if dark_theme else 0.14),
        "cta_hover_end": _lighten(base, 0.34 if dark_theme else 0.24),
        "accent_soft": _rgba(base, 0.20 if dark_theme else 0.14),
        "tab_selected_start": _rgba(strong, 0.88 if dark_theme else 0.16),
        "tab_selected_end": _rgba(deep, 0.84 if dark_theme else 0.08),
        "tab_hover": _rgba(base, 0.24 if dark_theme else 0.12),
        "list_item_hover": _rgba(base, 0.26 if dark_theme else 0.08),
        "list_item_selected": _rgba(soft, 0.46 if dark_theme else 0.18),
        "dropdown_selection": _rgba(soft, 0.42 if dark_theme else 0.18),
        "splitter_handle_hover": _rgba(strong, 0.46 if dark_theme else 0.46),
        "divider_accent_start": strong,
        "divider_accent_end": deep,
        "divider_line_start": _rgba(strong, 0.34 if dark_theme else 0.36),
        "divider_line_end": _rgba(strong, 0.05),
        "divider_desc": _rgba(strong, 0.40 if dark_theme else 0.40),
        "divider_desc_end": _rgba(strong, 0.05),
        "checkbox_checked_start": strong,
        "checkbox_checked_end": deep,
        "checkbox_checked_border": _rgba(_lighten(base, 0.28), 0.82 if dark_theme else 0.72),
        "checkbox_hover_border": _rgba(strong, 0.66 if dark_theme else 0.52),
        "slider_handle_start": strong,
        "slider_handle_end": deep,
        "slider_handle_border": _rgba(_lighten(base, 0.28), 0.64 if dark_theme else 0.50),
        "slider_handle_hover_start": _lighten(base, 0.26 if dark_theme else 0.18),
        "slider_handle_hover_end": _lighten(base, 0.12 if dark_theme else 0.06),
    }

_VALID_THEMES = set(AVAILABLE_THEMES)
_CURRENT_THEME = "light"


def _to_qcolor(value: str) -> QColor:
    """Parse Qt-safe colors, including CSS-like rgb()/rgba() strings."""
    color = QColor(value)
    if color.isValid():
        return color

    normalized = value.strip().lower()
    if normalized.startswith("rgba(") and normalized.endswith(")"):
        parts = [part.strip() for part in normalized[5:-1].split(",")]
        if len(parts) == 4:
            red = int(float(parts[0]))
            green = int(float(parts[1]))
            blue = int(float(parts[2]))
            alpha_raw = float(parts[3])
            alpha = int(round(alpha_raw * 255)) if alpha_raw <= 1 else int(round(alpha_raw))
            return QColor(red, green, blue, max(0, min(255, alpha)))

    if normalized.startswith("rgb(") and normalized.endswith(")"):
        parts = [part.strip() for part in normalized[4:-1].split(",")]
        if len(parts) == 3:
            red = int(float(parts[0]))
            green = int(float(parts[1]))
            blue = int(float(parts[2]))
            return QColor(red, green, blue)

    return QColor("#000000")


def set_current_theme(theme: str) -> None:
    global _CURRENT_THEME
    normalized_theme = normalize_theme(theme)
    _CURRENT_THEME = normalized_theme if normalized_theme in _VALID_THEMES else "light"

def get_current_theme() -> str:
    return _CURRENT_THEME


def get_current_theme_colors() -> dict:
    return get_theme_colors(_CURRENT_THEME)


def is_dark_theme(theme: str | None = None) -> bool:
    active_theme = normalize_theme(theme or _CURRENT_THEME)
    return active_theme in DARK_THEMES


def build_theme_palette(theme: str) -> QPalette:
    c = get_theme_colors(theme)
    palette = QPalette()

    active_roles = {
        QPalette.ColorRole.Window: c["bg_window_shell"],
        QPalette.ColorRole.WindowText: c["text_primary"],
        QPalette.ColorRole.Base: c["bg_input"],
        QPalette.ColorRole.AlternateBase: c["bg_surface_soft"],
        QPalette.ColorRole.ToolTipBase: c["bg_dropdown"],
        QPalette.ColorRole.ToolTipText: c["text_accent"],
        QPalette.ColorRole.Text: c["text_primary"],
        QPalette.ColorRole.Button: c["bg_surface_raised"],
        QPalette.ColorRole.ButtonText: c["text_accent"],
        QPalette.ColorRole.BrightText: c["text_bright"],
        QPalette.ColorRole.Light: c["bg_gradient_end"],
        QPalette.ColorRole.Midlight: c["border_input_hover"],
        QPalette.ColorRole.Dark: c["bg_gradient_start"],
        QPalette.ColorRole.Mid: c["border_list"],
        QPalette.ColorRole.Shadow: c["bg_gradient_start"],
        QPalette.ColorRole.Highlight: c["cta_gradient_start"],
        QPalette.ColorRole.HighlightedText: c["cta_text"],
        QPalette.ColorRole.Link: c["divider_accent_start"],
        QPalette.ColorRole.LinkVisited: c["divider_accent_end"],
        QPalette.ColorRole.PlaceholderText: c["text_muted"],
    }

    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, value in active_roles.items():
            palette.setColor(group, role, _to_qcolor(value))

    disabled_roles = {
        QPalette.ColorRole.WindowText: c["text_disabled"],
        QPalette.ColorRole.Text: c["text_disabled"],
        QPalette.ColorRole.ButtonText: c["text_disabled"],
        QPalette.ColorRole.PlaceholderText: c["text_disabled"],
        QPalette.ColorRole.Button: c["btn_disabled_bg"],
        QPalette.ColorRole.Base: c["bg_input"],
        QPalette.ColorRole.Highlight: c["btn_disabled_border"],
        QPalette.ColorRole.HighlightedText: c["text_muted"],
    }
    for role, value in disabled_roles.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, _to_qcolor(value))

    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, accent_role, _to_qcolor(c["cta_gradient_start"]))
        palette.setColor(QPalette.ColorGroup.Disabled, accent_role, _to_qcolor(c["btn_disabled_border"]))

    return palette


def repolish_widget(widget: QWidget) -> None:
    try:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()
    except RuntimeError:
        return


def apply_widget_stylesheet(widget: QWidget, stylesheet: str) -> None:
    """Apply a local stylesheet without walking the entire widget tree."""
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    if widget.styleSheet() != stylesheet:
        widget.setStyleSheet(stylesheet)
    repolish_widget(widget)


def apply_native_title_bar_theme(widget: QWidget, theme: str | None = None, logger=None) -> None:
    """Apply the current theme colors to a native Windows title bar for a widget."""
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

        colors = get_theme_colors(resolved_theme)
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
        DWMWA_BORDER_COLOR = 34
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

        is_dark_caption = is_dark_theme(resolved_theme)
        dark_mode = ctypes.c_int(1 if is_dark_caption else 0)
        result = _set_dwm_attr(DWMWA_USE_IMMERSIVE_DARK_MODE, dark_mode)
        if result != 0:
            _set_dwm_attr(DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1, dark_mode)

        caption_color = _to_colorref(colors["bg_window_shell"])
        text_color = _to_colorref(colors["text_bright"] if is_dark_caption else colors["text_accent"])

        _set_dwm_attr(DWMWA_CAPTION_COLOR, caption_color)
        # 不设置 DWMWA_BORDER_COLOR：border_sidebar 是半透明色（如 rgba(0,0,0,0.05)），
        # 而 COLORREF 不支持 alpha，转换时会丢掉透明度变成纯黑/纯白描边。
        # 让 Windows 使用原生默认边框，配色更自然。
        _set_dwm_attr(DWMWA_TEXT_COLOR, text_color)

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

    set_current_theme(resolved_theme)
    from qfluentwidgets import Theme, setTheme, setThemeColor

    setTheme(Theme.DARK if is_dark_theme(resolved_theme) else Theme.LIGHT)
    theme_colors = get_theme_colors(resolved_theme)
    setThemeColor(theme_colors.get("btn_primary_bg") or theme_colors.get("cta_gradient_start"))
    palette = build_theme_palette(resolved_theme)
    app.setPalette(palette)
    app.setStyleSheet("")
    QToolTip.setPalette(palette)
    QToolTip.setFont(app.font())
