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
    families = ["Preview Sans", "Preview Serif"]

    with patch.object(font_list, "list_font_families", return_value=families):
        combo = font_list.FontComboBox()
        changes = []
        combo.currentFontChanged.connect(lambda font: changes.append(font.family()))

        combo.setCurrentFamily("Preview Serif")
        assert isinstance(combo, ComboBox)
        assert combo.currentFamily() == "Preview Serif"
        assert combo.currentFont().family() == "Preview Serif"
        assert changes == ["Preview Serif"]

        menu = combo._createComboMenu()
        menu.addAction(QAction("Preview Serif", menu))
        assert menu.view.item(0).font().family() == "Preview Serif"

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
