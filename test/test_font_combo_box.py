import _bootstrap  # noqa: F401

import types
from unittest.mock import patch

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import ComboBox

from utils import font_list


_APPLICATION: QApplication | None = None


def _app() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    return _APPLICATION


def test_font_list_skips_non_scalable_families():
    app = _app()
    database = types.SimpleNamespace(
        families=lambda: ["Outline Font", "Fixedsys"],
        isScalable=lambda family: family == "Outline Font",
    )

    with (
        patch.object(font_list, "list_font_files"),
        patch.object(font_list, "QFontDatabase", database),
    ):
        assert font_list.list_font_families() == ["Outline Font"]
    app.processEvents()


def test_font_list_merges_legacy_and_typographic_names_for_same_face():
    families = ["toolboxQiangDiao-W", "toolboxQiangDiao-W-Regular"]
    records = (
        (1, "en", "toolboxQiangDiao-W-Regular"),
        (16, "en", "toolboxQiangDiao-W"),
        (1, "zh", "工具箱腔调体-简繁-Regular"),
        (16, "zh", "工具箱腔调体-简繁"),
    )

    with (
        patch.object(font_list, "list_font_families", return_value=families),
        patch.object(font_list, "_font_family_name_records", return_value=records),
        patch.object(font_list, "_font_face_signature", return_value=b"same-face"),
    ):
        entries = font_list.list_font_family_entries("zh_CN")

    assert len(entries) == 1
    display, family, aliases = entries[0]
    assert display == "工具箱腔调体-简繁"
    assert family == "toolboxQiangDiao-W"
    assert set(aliases) == {
        "toolboxQiangDiao-W",
        "toolboxQiangDiao-W-Regular",
        "工具箱腔调体-简繁",
        "工具箱腔调体-简繁-Regular",
    }


def test_font_list_keeps_typographic_names_for_different_faces_separate():
    families = ["Example-W", "Example-W-Regular"]
    records = (
        (1, "en", "Example-W-Regular"),
        (16, "en", "Example-W"),
    )

    with (
        patch.object(font_list, "list_font_families", return_value=families),
        patch.object(font_list, "_font_family_name_records", return_value=records),
        patch.object(font_list, "_font_face_signature", side_effect=lambda family: family.encode()),
    ):
        entries = font_list.list_font_family_entries("en_US")

    assert [(display, family) for display, family, _aliases in entries] == [
        ("Example-W", "Example-W"),
        ("Example-W-Regular", "Example-W-Regular"),
    ]


def test_localized_font_family_restores_original_brackets_for_display():
    family = "toolboxQiangDiao-W"
    records = (
        (16, "en", family),
        (16, "zh", "工具箱腔调体-简繁"),
    )
    original_names = {
        font_list._search_key(family): "[toolbox]QiangDiao-W",
        font_list._search_key("工具箱腔调体-简繁"): "[工具箱]腔调体-简繁",
    }

    with (
        patch.object(font_list, "_font_family_name_records", return_value=records),
        patch.dict(font_list._ORIGINAL_FONT_DISPLAY_NAMES, original_names, clear=True),
    ):
        display, aliases = font_list.localized_font_family(family, "zh_CN")

    assert display == "[工具箱]腔调体-简繁"
    assert "工具箱腔调体-简繁" in aliases
    assert "[工具箱]腔调体-简繁" in aliases


def test_font_combo_box_keeps_fluent_style_and_font_preview():
    app = _app()
    families = ["Preview Sans", "预览Sans", "Preview Serif"]
    locale = ["en_US"]

    def name_records(family):
        if family not in families:
            return ()
        suffix = "Sans" if family.endswith("Sans") else "Serif"
        return (
            (1, "en", f"Preview {suffix}"),
            (1, "zh", f"预览{suffix}"),
            (1, "ja", f"プレビュー{suffix}"),
        )

    with (
        patch.object(font_list, "list_font_families", return_value=families),
        patch.object(font_list, "_font_family_name_records", side_effect=name_records),
    ):
        combo = font_list.FontComboBox(locale_getter=lambda: locale[0])
        changes = []
        combo.currentFontChanged.connect(lambda font: changes.append(font.family()))

        combo.setCurrentFamily("Preview Serif")
        assert isinstance(combo, ComboBox)
        assert combo.currentText() == "Preview Serif"
        assert combo.currentFamily() == "Preview Serif"
        assert combo.currentFont().family() == "Preview Serif"
        assert changes == ["Preview Serif"]

        locale[0] = "zh_CN"
        combo.refresh_ui_texts()
        assert combo.count() == 2
        assert combo.currentText() == "预览Serif"
        assert combo.currentFamily() == "Preview Serif"

        menu = combo._createComboMenu()
        for item in combo.items:
            menu.addAction(QAction(item.text, menu))
        assert menu.view.item(1).font().family() == "Preview Serif"

        menu.search_edit.setText("preview serif")
        assert menu.view.item(0).isHidden()
        assert not menu.view.item(1).isHidden()

        menu.search_edit.setText("预览sans")
        assert not menu.view.item(0).isHidden()
        assert menu.view.item(1).isHidden()

        combo.refresh()
        assert combo.currentFamily() == "Preview Serif"

        menu.close()
        combo.close()
        app.processEvents()


def main() -> int:
    test_font_list_skips_non_scalable_families()
    test_font_list_merges_legacy_and_typographic_names_for_same_face()
    test_font_list_keeps_typographic_names_for_different_faces_separate()
    test_localized_font_family_restores_original_brackets_for_display()
    test_font_combo_box_keeps_fluent_style_and_font_preview()
    print("font combo box check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
