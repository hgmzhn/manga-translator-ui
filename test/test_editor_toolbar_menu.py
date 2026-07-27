"""编辑器顶栏菜单化重构的冒烟回归。

覆盖点：
- 常驻控件只剩 菜单/显示模式/排列/导出/适应窗口/不透明度滑条，信号照常发射
- 菜单 Action 触发对应信号；撤销/重做启停随 update_undo_redo_state
- 显示模式单选：切换发信号、重复选择不重复发、语言刷新重建后选中保持
- 排列：参照单选影响对齐选项启停；选项触发 align/distribute 信号；点击任意选项不关闭菜单
- refresh_ui_texts 重建菜单后状态保持
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def _flush_qt_events():
    yield
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _make_toolbar():
    from ui.widgets.editor_toolbar import EditorToolbar

    return EditorToolbar()


def _collect(signal):
    received = []
    signal.connect(lambda *args: received.append(args))
    return received


def test_persistent_widgets_and_signals():
    toolbar = _make_toolbar()
    try:
        # 常驻控件存在，旧的直排控件已删除；导出已收进「菜单」下拉
        assert toolbar.menu_button is not None
        assert toolbar.fit_window_button is not None
        assert toolbar.original_image_alpha_slider is not None
        assert not hasattr(toolbar, "export_button")
        assert not hasattr(toolbar, "zoom_label")
        assert not hasattr(toolbar, "display_mode_combo")
        assert not hasattr(toolbar, "align_ref_button")

        exports = _collect(toolbar.export_requested)
        fits = _collect(toolbar.fit_window_requested)
        alphas = _collect(toolbar.original_image_alpha_changed)

        toolbar.export_action.trigger()
        toolbar.fit_window_button.click()
        toolbar.original_image_alpha_slider.setValue(37)
        assert len(exports) == 1
        assert len(fits) == 1
        assert alphas and alphas[-1] == (37,)

        toolbar.set_export_enabled(False)
        assert not toolbar.export_action.isEnabled()
        toolbar.export_action.trigger()  # 禁用后不再发信号
        assert len(exports) == 1
        toolbar.set_export_enabled(True)
        assert toolbar.export_action.isEnabled()

        # set_original_image_alpha_slider 不回环发信号
        alphas.clear()
        toolbar.set_original_image_alpha_slider(0.5)
        assert toolbar.original_image_alpha_slider.value() == 50
        assert not alphas
    finally:
        toolbar.close()
        toolbar.deleteLater()


def test_menu_actions_emit_signals():
    toolbar = _make_toolbar()
    try:
        # 返回主页已彻底移除（主窗口侧边栏负责页面切换）
        assert not hasattr(toolbar, "back_action")
        assert not hasattr(toolbar, "back_requested")

        undos = _collect(toolbar.undo_requested)
        redos = _collect(toolbar.redo_requested)
        zoom_ins = _collect(toolbar.zoom_in_requested)
        zoom_outs = _collect(toolbar.zoom_out_requested)
        center_scales = _collect(toolbar.center_scale_enabled_changed)

        toolbar.zoom_in_action.trigger()
        toolbar.zoom_out_action.trigger()
        assert len(zoom_ins) == len(zoom_outs) == 1

        toolbar.center_scale_action.trigger()
        assert center_scales == [(True,)]
        assert toolbar.is_center_scale_enabled()

        # 撤销/重做初始禁用；启停跟随 update_undo_redo_state
        assert not toolbar.undo_action.isEnabled()
        assert not toolbar.redo_action.isEnabled()
        toolbar.update_undo_redo_state(True, False)
        assert toolbar.undo_action.isEnabled()
        assert not toolbar.redo_action.isEnabled()
        toolbar.undo_action.trigger()
        toolbar.redo_action.trigger()  # 禁用 action 的 trigger 不发 triggered
        assert len(undos) == 1
        assert len(redos) == 0
    finally:
        toolbar.close()
        toolbar.deleteLater()


def test_display_mode_radio():
    toolbar = _make_toolbar()
    try:
        modes = _collect(toolbar.display_mode_changed)
        assert toolbar.display_mode_actions["full"].isChecked()

        toolbar.display_mode_actions["text_only"].trigger()
        assert modes == [("text_only",)]
        assert toolbar.display_mode_actions["text_only"].isChecked()
        assert not toolbar.display_mode_actions["full"].isChecked()

        # 重复选择当前项不重复发信号
        toolbar.display_mode_actions["text_only"].trigger()
        assert modes == [("text_only",)]
    finally:
        toolbar.close()
        toolbar.deleteLater()


def test_align_reference_and_actions():
    toolbar = _make_toolbar()
    try:
        aligns = _collect(toolbar.align_requested)
        dists = _collect(toolbar.distribute_requested)

        assert toolbar.get_align_reference() == "selection"
        # 选区模式：1 个选中不够，2 个可对齐，3 个可分布
        toolbar.update_align_distribute_buttons(1)
        assert not toolbar.align_actions["left"].isEnabled()
        toolbar.update_align_distribute_buttons(2)
        assert toolbar.align_actions["left"].isEnabled()
        assert not toolbar._dist_v_action.isEnabled()
        toolbar.update_align_distribute_buttons(3)
        assert toolbar._dist_v_action.isEnabled()

        # 画布模式：1 个选中即可对齐
        toolbar.align_ref_actions["canvas"].trigger()
        assert toolbar.get_align_reference() == "canvas"
        toolbar.update_align_distribute_buttons(1)
        assert toolbar.align_actions["left"].isEnabled()

        toolbar.align_actions["left"].trigger()
        assert aligns == [("left",)]
        toolbar.update_align_distribute_buttons(3)
        toolbar._dist_v_action.trigger()
        toolbar._dist_h_action.trigger()
        assert dists == [("spacing_v",), ("spacing_h",)]
    finally:
        toolbar.close()
        toolbar.deleteLater()


def test_arrange_menu_stays_open_on_click():
    """点击排列菜单里的任意选项（参照/对齐/分布）都不关闭菜单。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    toolbar = _make_toolbar()
    try:
        aligns = _collect(toolbar.align_requested)
        toolbar.update_align_distribute_buttons(2)
        menu = toolbar.arrange_menu
        # exec() blocks until the menu closes, so code below it cannot close it
        # in the offscreen test process. Show non-modally and flush Qt events.
        menu.show()
        QApplication.processEvents()
        assert menu.isVisible()

        view = menu.view

        def _item_of(action):
            for i in range(view.count()):
                if view.item(i).data(Qt.ItemDataRole.UserRole) is action:
                    return view.item(i)
            raise AssertionError("menu item not found")

        # 对齐选项：触发信号且菜单保持打开
        view.itemClicked.emit(_item_of(toolbar.align_actions["left"]))
        assert aligns == [("left",)]
        assert menu.isVisible()

        # 参照单选：切换生效且菜单保持打开
        view.itemClicked.emit(_item_of(toolbar.align_ref_actions["canvas"]))
        assert toolbar.get_align_reference() == "canvas"
        assert menu.isVisible()

        # 禁用选项点击无效果
        toolbar.update_align_distribute_buttons(0)
        view.itemClicked.emit(_item_of(toolbar.align_actions["left"]))
        assert aligns == [("left",)]

        menu.close()
    finally:
        toolbar.close()
        toolbar.deleteLater()


def test_refresh_ui_texts_rebuilds_menu_preserving_state():
    toolbar = _make_toolbar()
    try:
        toolbar.display_mode_actions["box_only"].trigger()
        toolbar.align_ref_actions["canvas"].trigger()
        toolbar.update_undo_redo_state(True, True)
        toolbar.update_align_distribute_buttons(3)
        toolbar.set_export_enabled(False)
        toolbar.set_center_scale_enabled(True)
        old_menus = (toolbar.main_menu, toolbar.display_menu, toolbar.arrange_menu)
        icon_count = len(toolbar._themed_icon_buttons)

        modes = _collect(toolbar.display_mode_changed)
        toolbar.refresh_ui_texts()

        assert toolbar.main_menu is not old_menus[0]
        assert toolbar.display_menu is not old_menus[1]
        assert toolbar.arrange_menu is not old_menus[2]
        # 重建过程不发显示模式信号，且选中/参照/启停全部保持
        assert not modes
        assert toolbar.display_mode_actions["box_only"].isChecked()
        assert toolbar.get_align_reference() == "canvas"
        assert toolbar.align_ref_actions["canvas"].isChecked()
        assert toolbar.undo_action.isEnabled()
        assert toolbar.redo_action.isEnabled()
        assert not toolbar.export_action.isEnabled()  # 导出禁用状态跨重建保持
        assert toolbar.center_scale_action.isChecked()
        assert toolbar.align_actions["left"].isEnabled()
        assert toolbar._dist_v_action.isEnabled()
        # 排列菜单改用 Action 后不再登记主题图标按钮，重建也不累积
        assert len(toolbar._themed_icon_buttons) == icon_count == 0

        toolbar.refresh_theme()  # 重建后刷主题不应触碰已销毁控件
    finally:
        toolbar.close()
        toolbar.deleteLater()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_persistent_widgets_and_signals,
        test_menu_actions_emit_signals,
        test_display_mode_radio,
        test_align_reference_and_actions,
        test_arrange_menu_stays_open_on_click,
        test_refresh_ui_texts_rebuilds_menu_preserving_state,
    ]
    for func in tests:
        func()
        app.processEvents()
        print(f"PASS {func.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
