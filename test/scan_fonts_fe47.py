"""扫描 fonts/ 目录所有字体：U+FE47 (﹇) 是否有字形、竖排 base 是否有位图。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from manga_translator.rendering import text_render  # noqa: E402
from manga_translator.rendering.text_render._glyphs import (  # noqa: E402
    _glyph_spec_via_layout,
)

FONT_SIZE = 48


def main():
    fonts = sorted(
        p
        for p in (ROOT / "fonts").iterdir()
        if p.suffix.lower() in (".ttf", ".otf", ".ttc")
    )
    for path in fonts:
        try:
            text_render.set_font(str(path))
        except Exception as e:
            print(f"{path.name}: set_font failed: {e}")
            continue
        try:
            spec = _glyph_spec_via_layout("﹇", FONT_SIZE)
            spec_desc = (
                "None" if spec is None else f"family={spec.raw_font.familyName()}"
            )
        except Exception as e:
            spec_desc = f"error: {e}"
        try:
            base = text_render._vertical_base(FONT_SIZE, "﹇")
            bshape = None if base.bitmap is None else base.bitmap.shape
            base_desc = f"bitmap={bshape} adv={base.advance_y}"
        except Exception as e:
            base_desc = f"error: {e}"
        print(f"{path.name}: spec[{spec_desc}] base[{base_desc}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
