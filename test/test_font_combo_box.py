import _bootstrap  # noqa: F401

import types
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QAction, QFont, QWheelEvent
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import ComboBox

from manga_translator.rendering.text_render import _fonts as text_fonts
from utils import font_list


_APPLICATION: QApplication | None = None


def _app() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    return _APPLICATION


def _font_with_style(family: str, style: str, _size: int) -> QFont:
    font = QFont(family)
    font.setStyleName(style)
    return font


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


def test_font_list_can_exclude_system_families():
    app = _app()
    database = types.SimpleNamespace(
        families=lambda: ["Project Font", "System Font"],
        isScalable=lambda _family: True,
    )

    with (
        patch.object(font_list, "list_font_files"),
        patch.object(font_list, "QFontDatabase", database),
        patch.dict(font_list._REGISTERED_FONT_FAMILIES, {"project.ttf": ["Project Font"]}, clear=True),
    ):
        assert font_list.list_font_families(include_system=False) == ["Project Font"]
        assert font_list.list_font_families(include_system=True) == ["Project Font", "System Font"]
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
        patch.object(font_list, "_resolved_font_identity", return_value=(400, 0, 100, "regular")),
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
        patch.object(font_list, "_resolved_font_identity", side_effect=lambda family: (family,)),
    ):
        entries = font_list.list_font_family_entries("en_US")

    assert [(display, family) for display, family, _aliases in entries] == [
        ("Example-W", "Example-W"),
        ("Example-W-Regular", "Example-W-Regular"),
    ]


def test_font_list_expands_each_family_style_into_a_selectable_entry():
    with (
        patch.object(
            font_list,
            "list_font_family_entries",
            return_value=[("Preview Sans", "Preview Sans", ("Preview Sans", "Preview Sans CN"))],
        ),
        patch.object(font_list, "QFontDatabase", types.SimpleNamespace(
            styles=lambda family: ["Regular", "Bold", "Light"],
        )),
    ):
        entries = font_list.list_font_style_entries("en_US")

    assert [(display, value) for display, value, _aliases in entries] == [
        ("Preview Sans - Bold", "Preview Sans::Bold"),
        ("Preview Sans - Light", "Preview Sans::Light"),
        ("Preview Sans - Regular", "Preview Sans"),
    ]
    regular_aliases = entries[2][2]
    assert "Preview Sans CN" in regular_aliases
    assert "Preview Sans CN::Bold" in entries[0][2]


def test_font_list_merges_legacy_style_family_by_resolved_face():
    families = [
        ("Preview Sans", "Preview Sans", ("Preview Sans",)),
        ("Preview Sans Light", "Preview Sans Light", ("Preview Sans Light",)),
    ]

    def styles(family):
        return ["Regular", "Light"] if family == "Preview Sans" else ["Regular"]

    def signature(family, style):
        return {
            ("Preview Sans", "Regular"): b"regular",
            ("Preview Sans", "Light"): b"light",
            ("Preview Sans Light", "Regular"): b"light",
        }[(family, style)]

    with (
        patch.object(font_list, "list_font_family_entries", return_value=families),
        patch.object(font_list, "QFontDatabase", types.SimpleNamespace(styles=styles)),
        patch.object(font_list, "_resolved_font_identity", side_effect=signature),
    ):
        entries = font_list.list_font_style_entries("en_US")

    assert [(display, value) for display, value, _aliases in entries] == [
        ("Preview Sans - Light", "Preview Sans::Light"),
        ("Preview Sans - Regular", "Preview Sans"),
    ]
    assert "Preview Sans Light" in entries[0][2]


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


def test_font_combo_box_preserves_selected_style():
    app = _app()

    with (
        patch.object(
            font_list,
            "list_font_style_entries",
            return_value=[
                ("Preview Sans - Regular", "Preview Sans", ("Preview Sans",)),
                (
                    "Preview Sans - Bold",
                    "Preview Sans::Bold",
                    ("Preview Sans::Bold", "Preview Sans Bold"),
                ),
            ],
        ),
        patch.object(font_list.QFontDatabase, "font", side_effect=_font_with_style),
    ):
        combo = font_list.FontComboBox()
        combo.setCurrentFamily("Preview Sans::Bold")

        assert combo.currentFamily() == "Preview Sans::Bold"
        assert combo.currentFont().family() == "Preview Sans"
        assert combo.currentFont().styleName() == "Bold"

        combo.setCurrentFamily("Preview Sans Bold")
        assert combo.currentFamily() == "Preview Sans::Bold"

        combo.close()
    app.processEvents()


def test_renderer_applies_style_encoded_in_font_selection():
    app = _app()
    previous_state = getattr(text_fonts._thread_state, "value", None)
    try:
        text_fonts._thread_state.value = text_fonts.FontState()
        with (
            patch.object(text_fonts, "_ensure_qt_runtime"),
            patch.object(text_fonts, "_register_project_fonts"),
            patch.object(text_fonts, "_match_family", return_value="Preview Sans"),
            patch.object(text_fonts.QFontDatabase, "font", side_effect=_font_with_style),
        ):
            text_fonts.set_font("Preview Sans::Light")
            font = text_fonts._layout_font(24, 1.0)

        assert font.family() == "Preview Sans"
        assert font.styleName() == "Light"
    finally:
        if previous_state is None:
            delattr(text_fonts._thread_state, "value")
        else:
            text_fonts._thread_state.value = previous_state
    app.processEvents()


def test_font_combo_box_wheel_never_changes_and_menu_hover_previews():
    app = _app()

    def populate(combo, current=None, locale_code="en_US"):
        combo.clear()
        for family in ("Alpha Sans", "Beta Serif", "Gamma Mono"):
            combo.addItem(family, userData=family)
        if current:
            combo.setCurrentFamily(current)

    with patch.object(font_list, "populate_font_combo", side_effect=populate):
        combo = font_list.FontComboBox()
        combo.setCurrentIndex(1)

        event = QWheelEvent(
            QPointF(1, 1),
            QPointF(1, 1),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        combo.wheelEvent(event)
        assert combo.currentFamily() == "Beta Serif"

        previews = []
        combo.fontPreviewChanged.connect(previews.append)
        menu = combo._createComboMenu()
        for item in combo.items:
            menu.addAction(QAction(item.text, menu))
        menu._on_item_entered(menu.view.item(2))
        assert previews == ["Gamma Mono"]
        menu.leaveEvent(QEvent(QEvent.Type.Leave))
        assert previews[-1] == ""
        menu.close()
        combo.close()
    app.processEvents()


def main() -> int:
    test_font_list_skips_non_scalable_families()
    test_font_list_can_exclude_system_families()
    test_font_list_merges_legacy_and_typographic_names_for_same_face()
    test_font_list_keeps_typographic_names_for_different_faces_separate()
    test_font_list_expands_each_family_style_into_a_selectable_entry()
    test_font_list_merges_legacy_style_family_by_resolved_face()
    test_localized_font_family_restores_original_brackets_for_display()
    test_font_combo_box_keeps_fluent_style_and_font_preview()
    test_font_combo_box_preserves_selected_style()
    test_renderer_applies_style_encoded_in_font_selection()
    test_font_combo_box_wheel_never_changes_and_menu_hover_previews()
    print("font combo box check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
