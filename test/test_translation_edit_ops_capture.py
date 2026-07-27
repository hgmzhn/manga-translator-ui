"""text_edit_ops 回归:contentsChange 收窄成最小操作(IME 全量替换等)与记录器行为。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.utils.text_edit_ops import (  # noqa: E402
    EditOpRecorder,
    minimal_edit_op,
)
from manga_translator.rendering.rich_text_sync import (  # noqa: E402
    document_after_edit_ops,
    sync_region_rich_translation,
)


def test_ime_preedit_noop_is_dropped():
    # IME 预编辑:文档没变,但 contentsChange 报全量替换(计数含末尾分隔符)
    assert minimal_edit_op("ABCDEF", "ABCDEF", 0, 7, 7) is None


def test_ime_commit_narrows_to_insertion():
    # IME 提交:全量替换收窄成中间插入
    assert minimal_edit_op("ABCDEF", "ABC你好DEF", 0, 7, 9) == [3, 0, "你好"]


def test_plain_typing_unchanged():
    assert minimal_edit_op("ABC", "ABxC", 2, 0, 1) == [2, 0, "x"]


def test_deletion():
    assert minimal_edit_op("ABC", "AC", 1, 1, 0) == [1, 1, ""]


def test_select_all_replace():
    assert minimal_edit_op("ABCDEF", "XYZ", 0, 7, 4) == [0, 6, "XYZ"]


def test_replace_with_common_prefix_suffix():
    assert minimal_edit_op("你好吗", "你行吗", 0, 4, 4) == [1, 1, "行"]


def test_linebreak_char_converted():
    assert minimal_edit_op("A", "A↵", 1, 0, 1) == [1, 0, "\n"]


def test_recorder_lifecycle():
    recorder = EditOpRecorder()
    recorder.reset("你好")
    recorder.record_change("你x好", 1, 0, 1)
    info = recorder.take_edit_info("你x好")
    assert info == {"ops": [[1, 0, "x"]], "pre_text": "你好", "post_text": "你x好"}
    # 取走后基线前移,缓冲清空
    info2 = recorder.take_edit_info("你x好")
    assert info2 == {"ops": [], "pre_text": "你x好", "post_text": "你x好"}


def test_recorder_invalidate_discards_ops():
    recorder = EditOpRecorder()
    recorder.reset("你好")
    recorder.record_change("你x好", 1, 0, 1)
    recorder.invalidate("换了内容")
    recorder.reset("换了内容")
    info = recorder.take_edit_info("换了内容")
    assert info["ops"] == []
    assert info["pre_text"] == "换了内容"


def test_sync_region_entry_no_ops_returns_none():
    doc = {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": [
            {"type": "text", "text": "你好", "style": {"bold": True}},
        ]}],
    }
    assert sync_region_rich_translation(doc, None, raw_mode=False, new_translation="新文本") is None
    assert sync_region_rich_translation(
        doc, {"ops": [], "pre_text": "你好", "post_text": "你好"},
        raw_mode=False, new_translation="你好",
    ) is None


def test_sync_region_entry_applies_ops():
    doc = {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": [
            {"type": "text", "text": "你好", "style": {"bold": True}},
        ]}],
    }
    result = sync_region_rich_translation(
        doc,
        {"ops": [[1, 0, "呀"]], "pre_text": "你好", "post_text": "你呀好"},
        raw_mode=False,
        new_translation="你呀好",
    )
    assert result is not None
    assert result["blocks"][0]["inlines"] == [
        {"type": "text", "text": "你呀好", "style": {"bold": True}},
    ]


def test_ime_end_to_end_keeps_styles():
    """真实 QTextEdit + QInputMethodEvent:记录器采集回放后样式保留。"""
    from PyQt6.QtWidgets import QApplication, QTextEdit
    from PyQt6.QtGui import QInputMethodEvent

    app = QApplication.instance() or QApplication([])
    edit = QTextEdit()
    try:
        edit.setPlainText("ABCDEF")
        recorder = EditOpRecorder()
        recorder.reset(edit.toPlainText())

        edit.document().contentsChange.connect(
            lambda pos, removed, added: recorder.record_change(
                edit.toPlainText(), pos, removed, added
            )
        )

        cursor = edit.textCursor()
        cursor.setPosition(3)
        edit.setTextCursor(cursor)

        for preedit in ["n", "ni", "nihao"]:
            app.sendEvent(edit, QInputMethodEvent(preedit, []))
        commit = QInputMethodEvent("", [])
        commit.setCommitString("你好", 0, 0)
        app.sendEvent(edit, commit)

        assert edit.toPlainText() == "ABC你好DEF"
        info = recorder.take_edit_info(edit.toPlainText())
        assert info["ops"] == [[3, 0, "你好"]]

        doc = {
            "format": "richtext.v1",
            "blocks": [{"type": "paragraph", "inlines": [
                {"type": "text", "text": "ABCDEF", "style": {"color": "#ff0000"}},
            ]}],
        }
        result = document_after_edit_ops(doc, info["ops"], info["pre_text"], info["post_text"])
        assert result is not None
        assert result.plain_text() == "ABC你好DEF"
        # 同样式段中间插入 → 继承红色,单一 run
        inlines = result.to_dict()["blocks"][0]["inlines"]
        assert [(i["text"], i["style"]) for i in inlines] == [
            ("ABC你好DEF", {"color": "#ff0000"}),
        ]
    finally:
        edit.close()


def main():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
