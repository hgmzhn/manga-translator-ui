"""纯文本编辑操作的采集与最小化收窄(后端逻辑,UI 层只转发事件)。

Qt 文本框的 ``contentsChange`` 给出 ``(位置, 删除数, 插入数)``,但有两个怪癖:
- 改动涉及文档末尾时,删除/插入计数会把末尾段落分隔符也算进去(多 1);
- IME 预编辑与提交会把整篇文档报成一次"全量替换"。

本模块对照改动前的镜像文本,把每次报告收窄成最小真实操作
``[pos, removed_len, inserted_text]``;预编辑期间文本未变的"假替换"收窄后
为空,直接丢弃。这是对已记录操作的精确收窄,不是模糊 diff。

坐标口径与编辑框一致，直接使用真实 ``\n`` 换行；每个换行仍占一个字符。
"""

from __future__ import annotations

from typing import List, Optional


def minimal_edit_op(
    mirror: str,
    current: str,
    position: int,
    chars_removed: int,
    chars_added: int,
) -> Optional[list]:
    """把一次 contentsChange 收窄成最小操作;无实际改动返回 ``None``。

    ``mirror`` 是改动前的文本,``current`` 是改动后的文本;裁掉报告区间
    首尾未变的部分,还原出真实的插入/删除/替换区间。计数虚报(末尾段落
    分隔符)由切片越界自然钳制。
    """
    pos = max(0, int(position))
    removed_text = mirror[pos : pos + max(0, int(chars_removed))]
    added_text = current[pos : pos + max(0, int(chars_added))]
    while removed_text and added_text and removed_text[0] == added_text[0]:
        removed_text = removed_text[1:]
        added_text = added_text[1:]
        pos += 1
    while removed_text and added_text and removed_text[-1] == added_text[-1]:
        removed_text = removed_text[:-1]
        added_text = added_text[:-1]
    if not removed_text and not added_text:
        return None
    return [pos, len(removed_text), added_text]


class EditOpRecorder:
    """跟踪一个文本框的编辑操作序列,产出富文本同步用的 edit_info。

    UI 层的职责只剩三件事:
    - 文本每次变化时调 :meth:`record_change`(用户编辑)或
      :meth:`invalidate`(程序化写入,操作作废);
    - 程序化刷新完成后调 :meth:`reset` 重建基线;
    - 发射修改信号时调 :meth:`take_edit_info` 取走操作并推进基线。
    """

    def __init__(self) -> None:
        self._ops: List[list] = []
        # 框内原样文本镜像，用于收窄下一次报告
        self._doc_text = ""
        # 上次 take/reset 时的规范形文本,作为下一份 edit_info 的 pre_text
        self._baseline = ""

    def reset(self, current_text: str) -> None:
        """以当前文本为准重建基线(程序化刷新后调用)。"""
        self._doc_text = current_text
        self._ops = []
        self._baseline = current_text

    def invalidate(self, current_text: str) -> None:
        """程序化写入:镜像跟进,累积操作作废;基线由随后的 reset 统一重建。"""
        self._doc_text = current_text
        self._ops = []

    def record_change(
        self,
        current_text: str,
        position: int,
        chars_removed: int,
        chars_added: int,
    ) -> None:
        """记录一次用户编辑(contentsChange 报告 + 改动后全文)。"""
        mirror = self._doc_text
        self._doc_text = current_text
        op = minimal_edit_op(mirror, current_text, position, chars_removed, chars_added)
        if op is not None:
            self._ops.append(op)

    def take_edit_info(self, current_text: str) -> dict:
        """取走累积操作,返回 {ops, pre_text, post_text}(\\n 口径)并推进基线。"""
        post_text = current_text
        edit_info = {
            "ops": self._ops,
            "pre_text": self._baseline,
            "post_text": post_text,
        }
        self._ops = []
        self._baseline = post_text
        return edit_info
