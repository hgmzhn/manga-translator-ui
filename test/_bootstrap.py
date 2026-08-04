"""测试公共前置：sys.path、offscreen、torch/PyQt6 加载顺序。

任何要用到 Qt 或本仓代码的测试脚本，**第一句** import 就写：

    import _bootstrap  # noqa: F401

它把三件每个测试都得自己记一遍的事收在一处：

1. ``sys.path`` —— 仓库根 + ``desktop_qt_ui``（不然 ``No module named 'editor'``）；
2. ``QT_QPA_PLATFORM=offscreen`` —— 必须早于任何 PyQt6 导入；
3. **torch 必须在 PyQt6 之前加载**。这条是 Windows 上的硬约束：PyQt6 的
   Qt DLL 搜索路径会顶掉 ``c10.dll`` 的依赖解析，反过来导入会得到
   ``OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败``。
   桌面端正式入口 ``desktop_qt_ui/main.py`` 里做的就是这件事（见那里引用的
   https://github.com/pytorch/pytorch/issues/166628），测试沿用同一套。

没装 torch 的环境照常跑 —— 预载失败就跳过，不影响纯 Qt 测试。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "desktop_qt_ui"):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 必须在任何 PyQt6 导入之前；理由见模块 docstring。
try:
    import torch  # noqa: F401
except ImportError:
    pass

__all__ = ["ROOT"]
