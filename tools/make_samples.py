# -*- coding: utf-8 -*-
"""Render the README illustration sheet (docs/styles.png).

Every built-in style is rendered once with the default settings, composited on a
plain white sheet and labelled.  Run it from the repository root::

    .venv\\Scripts\\python tools\\make_samples.py

Thumbnails (assets/samples/<key>.png) are written as well and shipped inside the
exe, where the GUI shows them behind the font picker.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from hstool.fonts import CATALOG  # noqa: E402
from hstool.options import RenderOptions  # noqa: E402
from hstool.render import render  # noqa: E402

CN_TEXT = "张三丰"
EN_TEXT = "Zhang Sanfeng"
LABEL_FONT = r"C:\Windows\Fonts\msyh.ttc"

CELL_W, CELL_H = 400, 96
PAD = 16
COLS = 2


def label_font(size: int):
    try:
        return ImageFont.truetype(LABEL_FONT, size)
    except OSError:
        return ImageFont.load_default()


def render_one(key: str, text: str) -> Image.Image:
    opts = RenderOptions(text=text, font_key=key, width_mm=30.0, height_mm=9.0,
                         dpi=200, pen_key="fountain", color_key="black",
                         seed=7).clamped()
    return render(text, opts).image


def main() -> int:
    rows = (len(CATALOG) + COLS - 1) // COLS
    width = COLS * CELL_W + PAD
    height = rows * CELL_H + PAD
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    small = label_font(15)

    samples = ROOT / "assets" / "samples"
    samples.mkdir(parents=True, exist_ok=True)

    for index, style in enumerate(CATALOG):
        text = EN_TEXT if style.key in ("inkfree", "segoescript") else CN_TEXT
        try:
            image = render_one(style.key, text)
        except Exception as exc:  # pragma: no cover - diagnostic path
            print("FAIL %-12s %s" % (style.key, exc))
            continue
        image.save(samples / ("%s.png" % style.key))
        col, row = index % COLS, index // COLS
        x0 = PAD // 2 + col * CELL_W
        y0 = PAD // 2 + row * CELL_H
        title = "%d. %s" % (index + 1, style.name)
        draw.text((x0 + 8, y0 + 6), title, fill=(60, 60, 60), font=small)
        box_h = CELL_H - 34
        scale = min((CELL_W - 24) / image.width, box_h / max(1, image.height))
        thumb = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))), Image.LANCZOS)
        if thumb.mode == "RGBA":
            sheet.paste(thumb, (x0 + 12, y0 + 28), thumb)
        else:
            sheet.paste(thumb, (x0 + 12, y0 + 28))
        draw.line([(x0, y0 + CELL_H - 2), (x0 + CELL_W - 4, y0 + CELL_H - 2)],
                  fill=(228, 228, 228))
        print("ok   %-12s %s" % (style.key, image.size))

    out = ROOT / "docs" / "styles.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    print("wrote", out, sheet.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
