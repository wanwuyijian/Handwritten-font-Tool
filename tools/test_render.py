# -*- coding: utf-8 -*-
"""Render pipeline tests: size metadata, transparency, pens, clipping, budget."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from hstool.ink import build_ink  # noqa: E402
from hstool.options import RenderOptions  # noqa: E402
from hstool.render import render  # noqa: E402


def _png_dpi(data: bytes) -> tuple:
    image = Image.open(io.BytesIO(data))
    dpi = image.info.get("dpi")
    return image, dpi


def test_dpi_metadata():
    for dpi in (150, 300, 600):
        res = render("张三丰", RenderOptions(text="张三丰", dpi=dpi))
        image, stored = _png_dpi(res.image_bytes)
        assert stored is not None, "PNG must carry a pHYs chunk"
        assert abs(stored[0] - dpi) <= 1, (dpi, stored)
        assert image.mode == "RGBA"
        assert res.image_bytes.startswith(b"\x89PNG")


def test_transparent_background():
    res = render("张三丰", RenderOptions(text="张三丰"))
    image = res.image
    assert image.mode == "RGBA"
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    assert all(c[3] == 0 for c in corners), corners


def test_opaque_mode():
    res = render("张三丰", RenderOptions(text="张三丰", color_mode="RGB"))
    assert res.image.mode == "RGB"
    assert res.image.getpixel((0, 0)) == (255, 255, 255)


def test_not_clipped():
    """Ink must not touch the image border at any pen weight."""
    for weight in (0.2, 1.0, 3.0):
        for pen in ("fountain", "ballpoint", "pencil"):
            res = render("张三丰 Zhang", RenderOptions(
                text="张三丰 Zhang", pen_key=pen, weight=weight))
            alpha = res.image.getchannel("A")
            w, h = alpha.size
            border = []
            border += [alpha.getpixel((x, 0)) for x in range(w)]
            border += [alpha.getpixel((x, h - 1)) for x in range(w)]
            border += [alpha.getpixel((0, y)) for y in range(h)]
            border += [alpha.getpixel((w - 1, y)) for y in range(h)]
            # trim threshold is 18, ink leftovers must stay far below it
            assert max(border) <= 6, (pen, weight, max(border))


def test_physical_size_within_request():
    res = render("张三丰", RenderOptions(text="张三丰", width_mm=35, height_mm=14, dpi=600))
    assert res.width_mm <= 35.01 and res.height_mm <= 14.01, (res.width_mm, res.height_mm)
    assert res.width_mm > 30  # the ink fills the requested width


def test_word_paste_size_matches_ui():
    """Word sizes a picture from pHYs; it must match the UI millimetres."""
    from hstool.options import MM_PER_INCH
    from hstool.render import reported_px_per_meter

    for width_mm, height_mm in ((30.0, 10.0), (45.0, 15.0), (55.0, 18.0)):
        res = render("Liu 张三丰", RenderOptions(
            text="Liu 张三丰", width_mm=width_mm, height_mm=height_mm, dpi=600,
            auto_size=False))
        ppm = reported_px_per_meter(res.image_bytes)
        assert ppm is not None, "pHYs chunk missing"
        # size Word computes, in 0.01 mm units
        # mm = half_mm / (px per 0.01 mm) * 0.01 mm
        word_w = res.image.width * 100000.0 / ppm[0]
        word_h = res.image.height * 100000.0 / ppm[1]
        assert abs(word_w / 100.0 - width_mm) <= 0.05, (width_mm, word_w / 100.0)
        assert abs(word_h / 100.0 - height_mm) <= 0.05, (height_mm, word_h / 100.0)
        # and the chunk still reads as the chosen DPI for other software
        assert abs(ppm[0] / 1000.0 * MM_PER_INCH - 600) < 2.0


def test_png_chunks_stay_valid():
    """The rewritten pHYs must not break the chunk stream."""
    import struct

    res = render("张三丰", RenderOptions(text="张三丰"))
    data = res.image_bytes
    pos = 8
    chunks = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunks.append(kind)
        pos += 12 + length
    assert pos == len(data), "trailing bytes after IEND"
    assert chunks[0] == b"IHDR" and chunks[-1] == b"IEND"
    assert chunks.count(b"pHYs") == 1, chunks
    # opening the image again proves the CRCs are sound
    Image.open(io.BytesIO(data)).load()


def test_file_size_budget():
    text = "张三丰 2026年10月20日"
    res = render(text, RenderOptions(text=text, max_file_kb=120))
    assert res.size_kb <= 120.0, res.size_kb


def test_multiline():
    res = render("张三丰\nZhang Sanfeng", RenderOptions(text="a\nb", height_mm=20))
    assert res.image.height > 0 and res.image.width > 0


def test_empty_text():
    res = render("   ", RenderOptions(text=""))
    assert res.image.size == (1, 1)


def test_pen_differs():
    inks = {pen: build_ink(pen_key=pen) for pen in ("fountain", "ballpoint", "pencil")}
    assert inks["fountain"].body_rgb != inks["pencil"].body_rgb
    assert inks["pencil"].grain > inks["ballpoint"].grain
    assert inks["ballpoint"].halo_alpha <= inks["fountain"].halo_alpha


def test_depth_darkens():
    # a deeper setting always means more pigment coverage
    for color in ("black", "red", "blue"):
        pale = build_ink(color_key=color, depth=0.0)
        deep = build_ink(color_key=color, depth=1.0)
        assert deep.body_alpha >= pale.body_alpha
    # light ink visibly darkens; black is already at full darkness
    pale = build_ink(color_key="red", depth=0.0)
    deep = build_ink(color_key="red", depth=1.0)
    assert sum(deep.body_rgb) < sum(pale.body_rgb)


def test_every_style_renders():
    from hstool.fonts import list_styles

    for info in list_styles():
        if not info.available:
            continue
        text = "张三丰" if info.style.supports_cjk else "Zhang"
        res = render(text, RenderOptions(text=text, font_key=info.style.key, dpi=200))
        assert not res.missing, (info.style.key, res.missing)


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL", name, exc)
    print("failed:", failures)
    sys.exit(1 if failures else 0)


def test_slant_leans_the_ink():
    """Slant values move the ink: + leans right, - leans left.

    The drawing mask is widened on the side a slant leans towards, so the ink
    moment is compared with the centre of its own ink box: leaning right pulls
    the moment towards the top (smaller x), leaning left towards the foot.
    """
    from PIL import ImageFont

    from hstool.options import SLANT_MAX
    from hstool.render import _Glyph, _draw_mask, _shear_mask

    def offset(dx):
        font = ImageFont.truetype("simkai.ttf", 64)
        glyph = _Glyph(ch="\u738b", x=40.0, y=40.0, advance=64.0, font=font)
        mask = _shear_mask(_draw_mask([glyph], (220, 160), (0.0, 0.0), 0), dx)
        width, height = mask.size
        total = moment = 0.0
        for x in range(width):
            weight = float(sum(mask.crop((x, 0, x + 1, height)).tobytes()))
            total += weight
            moment += weight * x
        box = mask.getbbox()
        return moment / max(total, 1.0) - (box[0] + box[2]) / 2.0

    upright = offset(0.0)
    assert abs(upright) < 1.5                 # a straight glyph stays centred
    assert offset(SLANT_MAX) < upright - 2.0  # leaning right
    assert offset(-SLANT_MAX) > upright + 2.0  # leaning left
    assert offset(0.2) < offset(0.0) < offset(-0.2)


def test_slant_is_clamped():
    opts = RenderOptions(slant=9.9).clamped()
    assert abs(opts.slant) <= 0.45
