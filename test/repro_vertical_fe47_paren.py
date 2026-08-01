"""复现两问题：竖排末尾 ﹇ 不渲染；竖排 () 不居中。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import numpy as np  # noqa: E402

from manga_translator.rendering import text_render  # noqa: E402
from manga_translator.rendering.text_replacements import apply_replacements  # noqa: E402
from manga_translator.rendering.rich_text_rules import apply_rich_text_rules  # noqa: E402

FONT = str(ROOT / "fonts" / "思源黑体Medium.ttf")
FONT_SIZE = 48
OUT = ROOT / "test" / "_out_fe47"
OUT.mkdir(exist_ok=True)


def render_vertical(label: str, raw: str):
    replaced = apply_replacements(raw, 1)
    doc = apply_rich_text_rules(replaced, "vertical")
    payload = doc if doc is not None else replaced
    kind = "rich" if doc is not None else "plain"
    print(f"[{label}] raw={raw!r} replaced={replaced!r} rules={kind}")
    if doc is not None:
        print(f"  doc={doc.to_dict()}")
    img = text_render.put_text_vertical(
        FONT_SIZE, payload, 720, "left", (0, 0, 0), None, 1.0
    )
    if img is None:
        print("  render=None")
        return None
    alpha = img[:, :, 3]
    print(f"  canvas={img.shape[1]}x{img.shape[0]}")
    ys, xs = np.nonzero(alpha)
    if ys.size:
        print(f"  ink y=[{ys.min()},{ys.max()}] x=[{xs.min()},{xs.max()}]")
    else:
        print("  ink=EMPTY")
    try:
        import cv2
        bgr = np.full((img.shape[0], img.shape[1], 3), 255, np.uint8)
        mask = alpha.astype(np.float32) / 255.0
        for c in range(3):
            bgr[:, :, c] = (bgr[:, :, c] * (1 - mask)).astype(np.uint8)
        cv2.imwrite(str(OUT / f"{label}.png"), bgr)
    except Exception as e:
        print(f"  save failed: {e}")
    return img


def ink_rows(img, label):
    """按行聚类墨迹段，输出每段的 y 范围与 x 中心，用于看居中。"""
    alpha = img[:, :, 3]
    rows_has = (alpha > 0).any(axis=1)
    segs = []
    start = None
    for y, has in enumerate(rows_has):
        if has and start is None:
            start = y
        elif not has and start is not None:
            segs.append((start, y - 1))
            start = None
    if start is not None:
        segs.append((start, len(rows_has) - 1))
    width = img.shape[1]
    print(f"  [{label}] canvas_w={width} center={width / 2:.1f}")
    for y0, y1 in segs:
        band = alpha[y0 : y1 + 1]
        ys, xs = np.nonzero(band)
        cx = (xs.min() + xs.max()) / 2.0
        print(
            f"    seg y=[{y0},{y1}] x=[{xs.min()},{xs.max()}] "
            f"ink_cx={cx:.1f} offset={cx - width / 2:.1f}"
        )
    return segs


def main():
    text_render.set_font(FONT)

    # 问题1：末尾 ﹇
    base = text_render._vertical_base(FONT_SIZE, "﹇")
    print(
        f"_vertical_base('﹇'): bitmap={'None' if base.bitmap is None else base.bitmap.shape},"
        f" advance_y={base.advance_y} y={base.y} ink_w={base.ink_w}"
    )
    img = render_vertical("ABBBB_fe47", "ABBBB﹇")
    if img is not None:
        ink_rows(img, "ABBBB﹇")
    img2 = render_vertical("fe47_mid", "A﹇B")
    if img2 is not None:
        ink_rows(img2, "A﹇B")

    # 问题2：括号居中
    img3 = render_vertical("ascii_paren", "(あ)")
    if img3 is not None:
        ink_rows(img3, "(あ)")
    img4 = render_vertical("full_paren", "（あ）")
    if img4 is not None:
        ink_rows(img4, "（あ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
