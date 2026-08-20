from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QGridLayout, QTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    Flyout,
    FlyoutAnimationType,
    FlyoutViewBase,
    PushButton,
)
from qfluentwidgets import FluentIcon as FIF

from .hover_hint import set_hover_hint


@dataclass(frozen=True, slots=True)
class _SymbolSpec:
    display: str
    insertion: str
    name_key: str


_SYMBOLS = (
    _SymbolSpec("！", "！", "Symbol: Full-width exclamation mark"),
    _SymbolSpec("？", "？", "Symbol: Full-width question mark"),
    _SymbolSpec("…", "…", "Symbol: Ellipsis"),
    _SymbolSpec("——", "——", "Symbol: Long dash"),
    _SymbolSpec("「", "「", "Symbol: Left corner bracket"),
    _SymbolSpec("」", "」", "Symbol: Right corner bracket"),
    _SymbolSpec("『", "『", "Symbol: Left white corner bracket"),
    _SymbolSpec("』", "』", "Symbol: Right white corner bracket"),
    _SymbolSpec("〝", "〝", "Symbol: Vertical opening quotation mark"),
    _SymbolSpec("〟", "〟", "Symbol: Vertical closing quotation mark"),
    _SymbolSpec("!", "!", "Symbol: Half-width exclamation mark"),
    _SymbolSpec("?", "?", "Symbol: Half-width question mark"),
    _SymbolSpec("~", "~", "Symbol: Half-width tilde"),
    _SymbolSpec("～", "～", "Symbol: Full-width tilde"),
    _SymbolSpec("〰", "〰", "Symbol: Wavy dash"),
    _SymbolSpec("〜", "〜", "Symbol: Wave dash"),
    _SymbolSpec("※", "※", "Symbol: Reference mark"),
    _SymbolSpec("♥", "♥", "Symbol: Black heart"),
    _SymbolSpec("♡", "♡", "Symbol: White heart"),
    _SymbolSpec("●", "●", "Symbol: Black circle"),
    _SymbolSpec("○", "○", "Symbol: White circle"),
    _SymbolSpec("♩", "♩", "Symbol: Quarter note"),
    _SymbolSpec("♪", "♪", "Symbol: Eighth note"),
    _SymbolSpec("♫", "♫", "Symbol: Beamed eighth notes"),
    _SymbolSpec("♬", "♬", "Symbol: Beamed sixteenth notes"),
    _SymbolSpec("█", "█", "Symbol: Black square"),
    _SymbolSpec("　", "　", "Symbol: Full-width space"),
)


class _SymbolButton(PushButton):
    def __init__(self, spec: _SymbolSpec, parent: QWidget):
        super().__init__(parent=parent)
        self.setText(spec.display)
        self.setFixedSize(48, 30)


class _QuickSymbolFlyoutView(FlyoutViewBase):
    symbol_requested = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        translate: Callable[[str], str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(8)
        root.addWidget(CaptionLabel(title, self))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(5)
        for index, spec in enumerate(_SYMBOLS):
            button = _SymbolButton(spec, self)
            name = translate(spec.name_key)
            button.setAccessibleName(name)
            set_hover_hint(button, name)
            button.clicked.connect(
                lambda _checked=False, value=spec.insertion: self.symbol_requested.emit(
                    value
                )
            )
            grid.addWidget(button, index // 8, index % 8)
        root.addLayout(grid)

    def addWidget(self, widget, stretch=0, align=Qt.AlignmentFlag.AlignLeft):
        raise NotImplementedError


class QuickSymbolButton(PushButton):
    """Insert the symbol presets from the reference MTU editor."""

    def __init__(
        self,
        editor: QTextEdit,
        translate: Callable[[str], str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent=parent)
        self._editor = editor
        self._translate = translate
        self._flyout: Flyout | None = None
        self._insert_cursor: QTextCursor | None = None
        self.setIcon(FIF.EMOJI_TAB_SYMBOLS)
        self.setFixedHeight(30)
        self.clicked.connect(self._show_symbols)
        self.refresh_ui_texts()

    def refresh_ui_texts(self) -> None:
        self.setText(self._translate("Quick Symbols"))
        set_hover_hint(self, self.text())

    def _show_symbols(self) -> None:
        if self._flyout is not None:
            self._flyout.close()
            return

        self._insert_cursor = QTextCursor(self._editor.textCursor())
        view = _QuickSymbolFlyoutView(self.text(), self._translate, self.window())
        flyout = Flyout(view, self.window())
        flyout.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        flyout.view.setGraphicsEffect(None)
        view.symbol_requested.connect(self._insert_symbol)
        flyout.closed.connect(self._clear_flyout)
        self._flyout = flyout

        anchor = self.mapToGlobal(QPoint(0, self.height() + 8))
        flyout.exec(anchor, FlyoutAnimationType.DROP_DOWN)

    def _insert_symbol(self, symbol: str) -> None:
        if self._insert_cursor is None:
            return
        self._editor.setTextCursor(self._insert_cursor)
        self._editor.insertPlainText(symbol)
        self._editor.setFocus()
        if self._flyout is not None:
            self._flyout.close()

    def _clear_flyout(self) -> None:
        self._flyout = None
        self._insert_cursor = None
