"""测试入口的公共前置。

与 desktop_qt_ui/main.py:50 同理：必须在任何 PyQt6 导入之前加载 PyTorch，否则
PyQt6 的 Qt DLL 路径会干扰 c10.dll 初始化，在 Windows 上抛 WinError 1114。
参考: https://github.com/pytorch/pytorch/issues/166628
"""

try:
    import torch  # noqa: F401
except ImportError:
    pass
