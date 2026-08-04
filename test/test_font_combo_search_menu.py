"""字体下拉框搜索菜单的窗口行为回归（offscreen）。

锁定 2026-08-04 的修复：搜索菜单不能是 ``Qt.Popup``。Popup 拿不到 Windows
键盘焦点，Qt 会把 ``focusObject`` 留在主窗口原来的控件上，进而解除持有键盘
焦点那个窗口的输入法关联——表现为搜索框只能敲 ASCII，中文打不进去。换成工具
窗口后菜单自己拿焦点，但 Popup 白送的"点别处即关闭"要自己接管，一并锁住。

运行：
    uv run python test/test_font_combo_search_menu.py
或：
    uv run python -m pytest test/test_font_combo_search_menu.py
"""

import _bootstrap  # noqa: F401

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from utils.font_list import FontComboBox  # noqa: E402

_APP: QApplication | None = None


def _app() -> QApplication:
    # 必须留一个模块级引用：QApplication 被回收后再建控件会直接崩进程
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _open_menu():
    """建一个字体下拉框并展开它的搜索菜单，返回 ``(combo, menu)``。"""
    app = _app()
    combo = FontComboBox()
    if combo.count() == 0:
        # offscreen 平台可能一个系统字体都枚举不到，补几条假条目撑起菜单
        for family in ("Alpha Sans", "Beta Serif", "Gamma Mono"):
            combo.addItem(family, userData=family)
    combo.show()
    combo._showComboMenu()
    app.processEvents()
    menu = combo.dropMenu
    assert menu is not None, "菜单没有展开"
    return combo, menu


def _close(combo):
    combo._closeComboMenu()
    combo.close()
    combo.deleteLater()


def test_search_menu_is_a_tool_window_not_popup():
    combo, menu = _open_menu()
    try:
        window_type = menu.windowFlags() & Qt.WindowType.WindowType_Mask
        assert window_type != Qt.WindowType.Popup, "Popup 会让搜索框拿不到输入法"
        assert window_type == Qt.WindowType.Tool
        assert menu.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        _close(combo)


def test_focus_object_is_the_search_edit():
    """输入法跟着全局 focusObject 走——它必须落在搜索框上。

    注意 offscreen 平台下这条对 Popup 也成立（离屏后端照样把菜单窗口设成
    focusWindow），真正区分 Popup 的护栏是上面的窗口类型断言。这里只保证
    焦点确实交到了搜索框，没被菜单或列表截胡。
    """
    app = _app()
    combo, menu = _open_menu()
    try:
        app.processEvents()  # exec() 里的 singleShot(0) 才会把焦点交给搜索框
        assert menu.search_edit.hasFocus()
        assert app.focusWidget() is menu.search_edit
        assert QGuiApplication.focusObject() is menu.search_edit
    finally:
        _close(combo)


def test_menu_closes_when_window_deactivates():
    """顶替 Popup 的自动收起：切到别的窗口就关。"""
    combo, menu = _open_menu()
    try:
        QApplication.sendEvent(menu, QEvent(QEvent.Type.WindowActivate))
        QApplication.sendEvent(menu, QEvent(QEvent.Type.WindowDeactivate))
        assert not menu.isVisible()
    finally:
        _close(combo)


def test_deactivate_before_activation_keeps_menu_open():
    """菜单还没拿到焦点时的失活事件不能把自己关掉。"""
    combo, menu = _open_menu()
    try:
        menu._was_activated = False
        QApplication.sendEvent(menu, QEvent(QEvent.Type.WindowDeactivate))
        assert menu.isVisible()
    finally:
        _close(combo)


def test_click_inside_menu_padding_keeps_menu_open():
    """基类靠 Popup 的鼠标捕获在这里收外部点击；工具窗口收到的都是内部点击。"""
    combo, menu = _open_menu()
    try:
        pos = QPointF(2.0, 2.0)  # 搜索框四周的留白，不属于 view
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pos,
            menu.mapToGlobal(pos.toPoint()).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(menu, event)
        assert menu.isVisible()
    finally:
        _close(combo)


def test_escape_closes_menu():
    app = _app()
    combo, menu = _open_menu()
    try:
        app.processEvents()
        QTest.keyClick(menu.search_edit, Qt.Key.Key_Escape)
        assert not menu.isVisible()
    finally:
        _close(combo)


def test_search_filters_rows():
    """回归：换窗口类型不能影响搜索过滤本身。"""
    app = _app()
    combo, menu = _open_menu()
    try:
        app.processEvents()
        first = menu._search_terms[0]
        menu.search_edit.setText(first)
        app.processEvents()
        assert not menu.view.item(0).isHidden()

        menu.search_edit.setText("zzz-no-such-font-zzz")
        app.processEvents()
        assert all(menu.view.item(row).isHidden() for row in range(menu.view.count()))

        menu.search_edit.setText("")
        app.processEvents()
        assert not any(menu.view.item(row).isHidden() for row in range(menu.view.count()))
    finally:
        _close(combo)


def main() -> int:
    failures = []
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 汇总所有失败再退出
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for line in failures:
        print("FAIL", line)
    total = sum(1 for name, func in globals().items() if name.startswith("test_") and callable(func))
    print(f"{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
