import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

utils_package = types.ModuleType("utils")
utils_package.__path__ = [str(ROOT / "desktop_qt_ui" / "utils")]
sys.modules["utils"] = utils_package

manga_package = types.ModuleType("manga_translator")
manga_package.__path__ = [str(ROOT / "manga_translator")]
rendering_package = types.ModuleType("manga_translator.rendering")
rendering_package.__path__ = [str(ROOT / "manga_translator" / "rendering")]
text_render = types.ModuleType("manga_translator.rendering.text_render")
text_render.qt_family_is_ambiguous = lambda _family: False
text_render.register_font_file = lambda _path: []
sys.modules["manga_translator"] = manga_package
sys.modules["manga_translator.rendering"] = rendering_package
sys.modules["manga_translator.rendering.text_render"] = text_render

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import ComboBox

from utils import font_list


def test_font_combo_box_keeps_fluent_style_and_font_preview():
    app = QApplication.instance() or QApplication([])
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
    test_font_combo_box_keeps_fluent_style_and_font_preview()
    print("font combo box check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
