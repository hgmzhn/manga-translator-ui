import _bootstrap  # noqa: F401

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import InfoBar, InfoBarPosition, InfoBarManager

from ui.widgets.toast_notification import ToastManager, ToastNotification


def _ensure_app():
    return QApplication.instance() or QApplication([])


def test_hidden_parent_toast_close_then_show_parent():
    """在隐藏父控件下创建 duration=0 的通知，关闭通知后显示父控件，

    处理延迟删除事件，再创建新通知；确认没有异常，且管理器不再保留已销毁对象。
    """
    app = _ensure_app()
    parent = QWidget()
    mgr = ToastManager(parent)

    # 1. 隐藏父控件下创建非自动关闭的 Toast
    loading_toast = mgr.show_info("正在加载...", duration=0)
    assert loading_toast._bar is not None
    assert loading_toast.is_active()

    # 2. 在父控件显示前关闭该 Toast
    loading_toast.close()
    assert loading_toast._bar is None
    assert not loading_toast.is_active()

    # 3. 显示父控件，触发 Qt 的 showEvent 与事件循环
    parent.show()
    app.processEvents()

    # 4. 创建新通知，确保计算位置时不会因引用已销毁的 InfoBar 抛出 RuntimeError
    success_toast = mgr.show_success("加载完成")
    app.processEvents()

    bottom_mgr = InfoBarManager.make(InfoBarPosition.BOTTOM)
    bars = bottom_mgr.infoBars.get(parent, [])
    assert all(not sip.isdeleted(b) for b in bars), "管理器中不应残留已析构的 InfoBar"
    assert success_toast.isVisible()

    # 清理
    mgr.close_all()
    parent.close()


def test_toast_manager_close_all_with_hidden_parent():
    """测试 close_all() 能安全关闭隐藏状态下创建的全部通知，

    并在后续父控件展示时正常清空活跃列表。
    """
    app = _ensure_app()
    parent = QWidget()
    mgr = ToastManager(parent)

    # 在隐藏控件上排队多条 Toast
    mgr.show_info("提示信息 1", duration=0)
    mgr.show_error("错误信息 2", duration=3000)
    mgr.show_toast("成功信息 3", duration=3000, success=True)
    assert len(mgr.active_toasts) == 3

    # 在隐藏状态下执行 close_all()
    mgr.close_all()
    assert len(mgr.active_toasts) == 0

    # 父控件显示，处理延迟事件
    parent.show()
    app.processEvents()

    # 随后能够正常弹出新通知
    new_toast = mgr.show_info("新通知", duration=0)
    app.processEvents()
    assert new_toast.isVisible()

    bottom_mgr = InfoBarManager.make(InfoBarPosition.BOTTOM)
    bars = bottom_mgr.infoBars.get(parent, [])
    assert all(not sip.isdeleted(b) for b in bars), "管理器中不应残留已析构的 InfoBar"

    mgr.close_all()
    parent.close()


def test_raw_infobar_hidden_parent_safeguard():
    """直接测试底层 InfoBar 在隐藏父控件关闭、后展示父控件时的容错清洗。"""
    app = _ensure_app()
    parent = QWidget()

    # 隐藏时创建并关闭
    bar1 = InfoBar.info("标题1", "内容1", position=InfoBarPosition.BOTTOM, parent=parent, duration=-1)
    bar1.close()

    # 显示父控件并排空事件
    parent.show()
    app.processEvents()

    # 创建新的 InfoBar，验证安全补丁兜底成功
    bar2 = InfoBar.success("标题2", "内容2", position=InfoBarPosition.BOTTOM, parent=parent, duration=-1)
    app.processEvents()

    bottom_mgr = InfoBarManager.make(InfoBarPosition.BOTTOM)
    bars = bottom_mgr.infoBars.get(parent, [])
    assert all(not sip.isdeleted(b) for b in bars)

    bar2.close()
    parent.close()
