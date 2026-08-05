"""批量管理面板控件回归测试（offscreen）。

运行：
    uv run python test/test_batch_edit_panel.py
或：
    uv run python -m pytest test/test_batch_edit_panel.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

# 先导入引擎（会拉起 torch）再导入 PyQt6：Windows 上反过来会撞 c10.dll 初始化失败
from services import batch_edit_engine as engine  # noqa: E402
from services import batch_edit_schemes as scheme_store  # noqa: E402

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from qfluentwidgets import ScrollArea, SingleDirectionScrollArea  # noqa: E402

from ui.secondary_pages.batch_edit_panel import BatchEditPanel  # noqa: E402


_APP: QApplication | None = None


def _app() -> QApplication:
    # 必须留一个模块级引用：QApplication 被回收后再建控件会直接崩进程
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class _FakeSnapshot:
    def __init__(self, json_by_file):
        self.json_by_file = json_by_file


def _panel(directory: str) -> BatchEditPanel:
    """把方案文件指到临时目录，别动用户真实的 config/batch_edit_schemes.yaml。"""
    path = os.path.join(directory, "batch_edit_schemes.yaml")
    # 预写一份空方案表：否则 ensure_schemes_exists 会铺内置默认模板（自带示例条件），
    # 断言里就得把那几条一起算进去。
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("schemes: []\n")
    scheme_store.get_schemes_path = lambda: path
    _app()
    return BatchEditPanel(t_func=lambda value, **kwargs: value)


def _match(json_path: str, image_key: str, index: int) -> engine.MatchItem:
    return engine.MatchItem(
        json_path=json_path,
        image_key=image_key,
        region_index=index,
        image_name=os.path.basename(image_key),
        before_text="before",
        after_text="after",
        summary="font_size",
    )


def test_panel_builds_and_roundtrips_a_scheme():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            panel._add_condition_row({"field": "font_size", "op": "between", "value": [10, 20]})
            panel._add_condition_row({"field": "direction", "op": "eq", "value": "v"})
            panel.replace_card.load_actions([
                {"type": "replace_text", "pattern": "a", "regex": False, "replace": "b"}])
            panel.set_fields_card.load_actions([{"type": "set_fields", "fields": {"font_size": 30}}])
            scheme = panel._collect_scheme()

            assert [item["field"] for item in scheme["match"]["conditions"]] == ["font_size", "direction"]
            assert [action["type"] for action in scheme["actions"]] == ["set_fields", "replace_text"]

            # 存盘再读回，UI 状态应当还原
            panel._save_current_scheme()
            reloaded = scheme_store.load_schemes(scheme_store.get_schemes_path())
            assert reloaded[panel._current_index]["match"]["conditions"] == scheme["match"]["conditions"]
        finally:
            panel.shutdown()


def test_batch_page_uses_regular_fluent_scroll_area():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            scrolls = panel.findChildren(ScrollArea)
            assert len(scrolls) == 1
            assert panel.findChildren(SingleDirectionScrollArea) == []
            assert (
                scrolls[0].horizontalScrollBarPolicy()
                == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
        finally:
            panel.shutdown()


def test_condition_rows_can_be_removed():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            first = panel._add_condition_row({"field": "has_rich_text", "op": "is_true", "value": None})
            panel._add_condition_row({"field": "direction", "op": "eq", "value": "v"})
            assert len(panel._condition_rows) == 2
            panel._remove_condition_row(first)
            assert len(panel._condition_rows) == 1
            assert panel._collect_scheme()["match"]["conditions"][0]["field"] == "direction"
        finally:
            panel.shutdown()


def test_scope_follows_catalog_snapshot():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            assert panel._json_paths() == []
            panel.set_catalog_snapshot(_FakeSnapshot({
                "C:/img/a.png": "C:/img/work/a_translations.json",
                "C:/img/b.png": "C:/img/work/b_translations.json",
            }))
            assert len(panel._json_paths()) == 2
            # 同一个 json 被两张图指向时只算一次
            panel.set_catalog_snapshot(_FakeSnapshot({
                "C:/img/a.png": "C:/img/work/a_translations.json",
                "C:/img/a.webp": "C:/img/work/a_translations.json",
            }))
            assert len(panel._json_paths()) == 1
        finally:
            panel.shutdown()


def test_preview_table_selection():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            matches = [_match("j1.json", "a.png", 0), _match("j1.json", "a.png", 1),
                       _match("j2.json", "b.png", 0)]
            panel._matches = matches
            for item in matches:
                panel._append_match_row(item)

            assert panel.table.rowCount() == 3
            assert len(panel._selected_matches()) == 3

            panel._set_all_checked(False)
            assert panel._selected_matches() == []

            panel.table.item(1, panel.COL_CHECK).setCheckState(Qt.CheckState.Checked)
            selected = panel._selected_matches()
            assert len(selected) == 1 and selected[0].region_index == 1
        finally:
            panel.shutdown()


def test_editor_conflict_is_detected_only_for_files_in_range():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            open_image = os.path.join(directory, "a.png")
            open_json = os.path.join(directory, "a_translations.json")
            other_json = os.path.join(directory, "b_translations.json")
            panel.set_catalog_snapshot(_FakeSnapshot({open_image: open_json}))
            panel.set_editor_context(lambda: open_image, lambda path: None)

            in_range = [_match(os.path.abspath(open_json), open_image, 0)]
            assert panel._conflicting_editor_image(
                {item.json_path for item in in_range}) == open_image

            out_of_range = [_match(os.path.abspath(other_json), "b.png", 0)]
            assert panel._conflicting_editor_image(
                {item.json_path for item in out_of_range}) is None

            # 编辑器没开文档时不该误报
            panel.set_editor_context(lambda: None, lambda path: None)
            assert panel._conflicting_editor_image({item.json_path for item in in_range}) is None
        finally:
            panel.shutdown()


def test_restore_lists_only_files_that_have_a_backup():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            with_backup = os.path.join(directory, "a_translations.json")
            without_backup = os.path.join(directory, "b_translations.json")
            for path in (with_backup, without_backup):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("{}")
            with open(with_backup + ".bak", "w", encoding="utf-8") as handle:
                handle.write("{}")

            panel.set_catalog_snapshot(_FakeSnapshot({
                os.path.join(directory, "a.png"): with_backup,
                os.path.join(directory, "b.png"): without_backup,
            }))
            assert panel._restorable_paths() == [os.path.abspath(with_backup)]
        finally:
            panel.shutdown()


def test_changing_a_condition_invalidates_the_preview():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            panel._matches = [_match("j1.json", "a.png", 0)]
            panel._append_match_row(panel._matches[0])
            panel.apply_button.setEnabled(True)
            panel._add_condition_row({"field": "has_rich_text", "op": "is_true", "value": None})
            assert panel._matches == []
            assert panel.table.rowCount() == 0
            assert panel.apply_button.isEnabled() is False
        finally:
            panel.shutdown()


def test_refresh_ui_texts_does_not_crash_with_rows_present():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            panel._add_condition_row({"field": "fg_colors", "op": "color_near",
                                      "value": {"color": "#FF0000", "tolerance": 25}})
            panel.set_fields_card.load_actions([{"type": "set_fields", "fields": {"font_family": "Arial"}}])
            panel.rich_text_card.load_actions([
                {"type": "rich_text", "mode": "overwrite", "pattern": "x", "style": {"bold": True}},
                {"type": "rich_text", "mode": "replace", "pattern": "",
                 "match_style": {"color": "#FF0000"}, "style": {"bold": True}},
            ])
            panel.refresh_ui_texts()
            panel.apply_theme()
        finally:
            panel.shutdown()


def test_replace_card_keeps_every_entry_in_order():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            panel.replace_card.load_actions([
                {"type": "replace_text", "pattern": "a", "replace": "1"},
                {"type": "replace_text", "pattern": "b", "replace": "2"},
            ])
            panel.replace_card._add_entry({"type": "replace_text", "pattern": "c", "replace": "3"})
            actions = panel.replace_card.to_actions()
            assert [item["pattern"] for item in actions] == ["a", "b", "c"]

            # 删中间一条，其余顺序不变
            panel.replace_card._remove_entry(panel.replace_card._entries[1])
            assert [item["pattern"] for item in panel.replace_card.to_actions()] == ["a", "c"]

            # 方案里也得原样落三条同类型动作（ACTION_ORDER 只分组、不打乱组内顺序）
            panel.replace_card._add_entry({"type": "replace_text", "pattern": "d", "replace": "4"})
            scheme = panel._collect_scheme()
            assert [item["pattern"] for item in scheme["actions"]] == ["a", "c", "d"]
        finally:
            panel.shutdown()


def test_rich_text_entry_modes_round_trip():
    with tempfile.TemporaryDirectory() as directory:
        panel = _panel(directory)
        try:
            loaded = [
                {"type": "rich_text", "mode": "overwrite", "pattern": "喂", "style": {"bold": True}},
                {"type": "rich_text", "mode": "fill", "pattern": "", "style": {"color": "#FF0000"}},
                {"type": "rich_text", "mode": "replace", "pattern": "！",
                 "match_style": {"bold": True, "color": "#00FF00"},
                 "match_style_logic": "any", "style": {"underline": True}},
            ]
            panel.rich_text_card.load_actions(loaded)
            actions = panel.rich_text_card.to_actions()
            assert [item["mode"] for item in actions] == ["overwrite", "fill", "replace"]
            # pattern 留空 = 整条 region，不该被当成"没填完"丢掉
            assert actions[1]["pattern"] == "" and actions[1]["style"] == {"color": "#FF0000"}
            assert actions[2]["match_style"] == {"bold": True, "color": "#00FF00"}
            assert actions[2]["match_style_logic"] == "any"
            assert actions[2]["style"] == {"underline": True}
            assert panel.rich_text_card._entries[2].match_logic_combo.currentData() == "any"
        finally:
            panel.shutdown()


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
