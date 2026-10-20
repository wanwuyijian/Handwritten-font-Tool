# -*- coding: utf-8 -*-
"""Unified renderer: text -> transparent PNG (or in-memory image + bytes).

The renderer is deliberately dependency-light (Pillow only) so the resulting
exe stays small.  Every style is rendered from a *font file*; the handwriting
look comes from a small post-processing pipeline instead of a heavy AI model:

1. **auto fit**     - the text is measured and scaled to fill the canvas,
2. **ink layers**   - a pale "halo" copy underneath a full-strength body copy
                      reproduces ink bleed (fountain) / smudge (pencil),
3. **wobble**       - every character is rotated and offset a little so the
                      baseline is not perfectly straight (optional jitter),
4. **fuzz**         - a mild Gaussian blur softens the stroke edges,
5. **grain**        - multiplication with a per-pixel noise field makes the
                      ink look like it sits *in* paper fibres rather than on
                      top of it,
6. **trim + pad**   - the transparent margin is trimmed and a small margin is
                      re-added, so Word uses the ink itself as the anchor.

The result is always written with a pHYs chunk so Word inserts the picture at
the requested physical size (millimetres), independent of the screen DPI.
"""

from __future__ import annotations

import io
import math
import random
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .fonts import get_style, list_styles, open_truetype
from .ink import build_ink
from .options import COLOR_MODE_OPAQUE, MM_PER_INCH, RenderOptions
from .text import Script, classify, normalize_text

Image.MAX_IMAGE_PIXELS = 200_000_000

# Latin companion faces for the CJK hands, best first.  "segoesc.ttf" is the
# Segoe Script *UI* face and draws boxes for printable ASCII on some Windows
# builds; Ink Free covers the whole Latin range and is a real handwriting face.
LATIN_FALLBACK_KEYS = ("inkfree", "segoescript")

# alpha below this is invisible grain mist, not ink
_TRIM_THRESHOLD = 18


# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------
@dataclass
class RenderResult:
    image: Image.Image         # the final image (RGBA or RGB)
    image_bytes: bytes         # encoded PNG
    width_mm: float            # physical width stored in the PNG
    height_mm: float
    dpi: int
    font_keys: List[str]       # fonts actually used
    missing: List[str]         # characters no font could render
    stored_bytes: int

    @property
    def size_kb(self) -> float:
        return self.stored_bytes / 1024.0

    def size_label(self) -> str:
        return "%d × %d px · %.1f × %.1f mm · %.0f KB" % (
            self.image.width,
            self.image.height,
            self.width_mm,
            self.height_mm,
            self.size_kb,
        )


# --------------------------------------------------------------------------
# font helpers
# --------------------------------------------------------------------------
_cmap_cache: Dict[str, set] = {}


def _font_cmap(font: ImageFont.FreeTypeFont) -> set:
    """Code points a loaded Pillow font really contains (via fontTools)."""
    key = getattr(font, "_hstool_key", None) or str(getattr(font, "path", ""))
    cached = _cmap_cache.get(key)
    if cached is not None:
        return cached
    data = getattr(font, "font_bytes", None)
    glyphs = set()
    try:
        from fontTools.ttLib import TTFont

        if data:
            tt = TTFont(io.BytesIO(data), fontNumber=0, lazy=True)
        else:
            tt = TTFont(str(font.path), fontNumber=0, lazy=True)
        try:
            glyphs = set((tt.getBestCmap() or {}).keys())
        finally:
            tt.close()
    except Exception:
        glyphs = set()
    _cmap_cache[key] = glyphs
    return glyphs


def _font_has(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    """True when *ch* has a real glyph in *font*."""
    glyphs = _font_cmap(font)
    if not glyphs:
        return True  # unknown -> assume yes, never block the user's text
    return ord(ch) in glyphs


def _resolve_font_files(opts: RenderOptions) -> Dict[str, object]:
    """Map style keys -> font file paths that are available on this machine."""
    files: Dict[str, object] = {}
    for info in list_styles():
        if info.available:
            files[info.style.key] = info.path
    return files


def _pick_font_key(opts: RenderOptions, script: str, files: Dict[str, object]) -> str:
    style = get_style(opts.font_key)
    if script == Script.CJK:
        key = opts.font_key
        if not style.supports_cjk:
            key = "wenkai" if "wenkai" in files else "kaiti"
        return key if key in files else next(iter(files))
    # latin / digits
    if not style.supports_cjk:
        return opts.font_key if opts.font_key in files else LATIN_FALLBACK_KEYS[0]
    latin = style.latin_fallback or LATIN_FALLBACK_KEYS[0]
    if latin in files and files[latin] is not None:
        return latin
    for key in LATIN_FALLBACK_KEYS:
        if key in files and files[key] is not None:
            return key
    return opts.font_key if opts.font_key in files else next(iter(files))


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
@dataclass
class _Glyph:
    ch: str
    x: float
    y: float
    advance: float
    font: ImageFont.FreeTypeFont


def _measure_line(font: ImageFont.FreeTypeFont, text: str) -> float:
    if not text:
        return 0.0
    return float(font.getlength(text))


def _build_layout(
    opts: RenderOptions,
    size_px: int,
    files: Dict[str, object],
) -> Tuple[List[_Glyph], Dict[str, ImageFont.FreeTypeFont], List[str], float, float]:
    """Place every character; returns glyph list, fonts, missing, width, height."""
    lines = opts.text.split("\n")
    style = get_style(opts.font_key)
    fonts: Dict[str, ImageFont.FreeTypeFont] = {}

    def font_for(script: str):
        key = _pick_font_key(opts, script, files)
        if key not in fonts:
            font = open_truetype(files[key], size_px)
            try:
                font._hstool_key = "%s|%d" % (files[key], size_px)  # type: ignore[attr-defined]
            except Exception:
                pass
            fonts[key] = font
        return fonts[key]

    cjk_font = font_for(Script.CJK)
    latin_font = font_for(Script.LATIN)
    line_height = max(cjk_font.getmetrics()[0] + cjk_font.getmetrics()[1],
                      latin_font.getmetrics()[0] + latin_font.getmetrics()[1])
    gap = line_height * opts.line_gap
    missing: List[str] = []
    ascent_cjk = cjk_font.getmetrics()[0]
    ascent_latin = latin_font.getmetrics()[0]
    ascent = max(ascent_cjk, ascent_latin)

    glyphs: List[_Glyph] = []
    max_width = 0.0
    for line_index, line in enumerate(lines):
        cursor = 0.0
        base_y = line_index * (line_height + gap)
        for ch in line:
            kind = classify(ch) if ch != " " else Script.SPACE
            if kind == Script.CJK:
                font = cjk_font
                top = ascent - ascent_cjk
            else:
                font = latin_font
                top = ascent - ascent_latin
            if ch != " " and not _font_has(font, ch):
                missing.append(ch)
            advance = font.getlength(ch)
            glyphs.append(_Glyph(ch, cursor, base_y + top, advance, font))
            cursor += advance
        max_width = max(max_width, cursor)
    total_height = (len(lines) - 1) * (line_height + gap) + line_height
    return glyphs, fonts, missing, max_width, total_height


def _fit_size(
    opts: RenderOptions,
    files: Dict[str, object],
    box_w: int,
    box_h: int,
) -> Tuple[int, List[_Glyph], Dict[str, ImageFont.FreeTypeFont], List[str]]:
    """Binary search the font size that fills the canvas best."""
    lines = opts.text.split("\n")

    def measure(size: int):
        glyphs, fonts, missing, width, height = _build_layout(opts, size, files)
        return width, height, glyphs, fonts, missing

    lo, hi = 4, max(16, box_h * 3)
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        width, height, glyphs, fonts, missing = measure(mid)
        if width <= box_w and height <= box_h:
            best = (mid, glyphs, fonts, missing)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        best = (4, *measure(4)[2:])
    return best


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _shear_mask(mask: Image.Image, slant: float) -> Image.Image:
    """Lean the ink by *slant* (0 = upright, +0.45 leans right, about 24 deg).

    The shear pivots on the ink's own bottom edge, so the baseline of a
    signature stays on the signature line while the top of the strokes swings
    sideways.  A lean empties one side of the mask, so the result is always
    pasted back into a mask of the original size: fitting and centring then
    work exactly as they do for an upright signature.
    """
    if abs(slant) < 0.005:
        return mask
    bbox = mask.getbbox()
    if bbox is None:
        return mask
    # PIL only samples inside the requested output box, so the box is widened
    # by the room the lean needs and the slack is trimmed again below
    span = bbox[3] - bbox[1]
    extra = int(math.ceil(abs(slant) * span)) + 2
    width, height = mask.size
    pivot = float(bbox[3])
    # PIL maps output -> input and y grows downwards here: sampling further
    # right near the top makes the glyph lean right (positive slant)
    coeffs = (1.0, -slant, slant * pivot, 0.0, 1.0, 0.0)
    lean = mask.transform((width + extra, height), Image.AFFINE, coeffs,
                          resample=Image.BICUBIC)
    # a right lean frees space on the left, so the ink moves there and stays
    # centred in its cell and on the signature line
    out = Image.new("L", (width, height), 0)
    out.paste(lean, (extra if slant < 0 else 0, 0))
    return out


def _draw_mask(
    glyphs: List[_Glyph],
    size: Tuple[int, int],
    origin: Tuple[float, float],
    stroke_width: int,
    scale: float = 1.0,
    rng: Optional[random.Random] = None,
    jitter: float = 0.0,
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    ox, oy = origin
    for glyph in glyphs:
        if glyph.ch == " ":
            continue
        x = ox + glyph.x
        y = oy + glyph.y
        if glyph.advance <= 0:
            continue
        if jitter > 0.0 and rng is not None:
            # rotate around the glyph centre and nudge it slightly
            pad = max(8, stroke_width * 4)
            cell_w = int(glyph.advance) + 2 * pad + 8
            cell_h = int(glyph.font.size * 2) + 2 * pad
            cell = Image.new("L", (cell_w, cell_h), 0)
            ImageDraw.Draw(cell).text(
                (pad, pad), glyph.ch, font=glyph.font, fill=255,
                stroke_width=stroke_width, stroke_fill=255,
            )
            angle = rng.uniform(-5.0, 5.0) * jitter
            if abs(angle) > 0.2:
                cell = cell.rotate(
                    angle, resample=Image.BICUBIC,
                    center=(pad, pad + glyph.font.size * 0.5),
                )
            # sub-pixel offsets are lost when pasting, so stay within a pixel
            dx = rng.uniform(-0.6, 0.6) * jitter * glyph.font.size * 0.05
            dy = rng.uniform(-0.8, 0.8) * jitter * glyph.font.size * 0.05
            mask.paste(cell, (int(round(x - pad + dx)), int(round(y - pad + dy))), cell)
        else:
            draw.text((x, y), glyph.ch, font=glyph.font, fill=255,
                      stroke_width=stroke_width, stroke_fill=255)
    if scale != 1.0:
        new_size = (max(1, int(size[0] * scale)), max(1, int(size[1] * scale)))
        mask = mask.resize(new_size, Image.BICUBIC).crop((0, 0, size[0], size[1]))
    return mask


def _grain(image: Image.Image, strength: float, seed: int) -> Image.Image:
    """Speckle the ink: some pixels stay pale, some lose alpha entirely.

    A single noise field drives *both* the colour and the alpha of every ink
    pixel, so the texture looks like pigment sitting in the paper fibres
    instead of a smooth half-transparent wash.
    """
    if strength <= 0.002:
        return image
    width, height = image.size
    alpha = image.getchannel("A")
    if alpha.getextrema()[1] == 0:
        return image
    rng = random.Random(seed)
    low_w = max(2, width // 2)
    low_h = max(2, height // 2)
    noise = Image.effect_noise((low_w, low_h), 80).resize((width, height), Image.BICUBIC)
    noise = noise.convert("L")
    # colour: pale/dark pits in the pigment (never fully white -> keeps tint)
    rgb_factor = noise.point(lambda v: int(255 - (v / 255.0) * strength * 200))
    # alpha: a small share of pixels loses its ink (graphite on paper grain)
    drop = noise.point(lambda v: int(255 if v > 255 - strength * 150 else 0))
    rgb = ImageChops.multiply(image.convert("RGB"), Image.merge("RGB", [rgb_factor] * 3))
    alpha = ImageChops.subtract(alpha, drop.point(lambda v: v // 6))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _colourise(mask: Image.Image, rgb: Tuple[int, int, int], alpha: int) -> Image.Image:
    solid = Image.new("RGBA", mask.size, rgb + (0,))
    solid.putalpha(mask.point(lambda v: (v * alpha) // 255))
    return solid


def _trim(
    image: Image.Image,
    pad_px: float,
    dpi: int,
    keep_min_mm: Tuple[float, float],
) -> Image.Image:
    alpha = image.getchannel("A")
    # ignore the faintest grain fringe so the canvas is not padded for it
    bbox = alpha.point(lambda v: 255 if v > _TRIM_THRESHOLD else 0).getbbox()
    if bbox is None:
        return image
    left = max(0, int(bbox[0] - pad_px))
    top = max(0, int(bbox[1] - pad_px))
    right = min(image.width, int(bbox[2] + pad_px))
    bottom = min(image.height, int(bbox[3] + pad_px))
    trimmed = image.crop((left, top, right, bottom))

    min_w = int(round(keep_min_mm[0] / MM_PER_INCH * dpi))
    min_h = int(round(keep_min_mm[1] / MM_PER_INCH * dpi))
    width = max(trimmed.width, min_w)
    height = max(trimmed.height, min_h)
    if width != trimmed.width or height != trimmed.height:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(
            trimmed,
            ((width - trimmed.width) // 2, (height - trimmed.height) // 2),
        )
        trimmed = canvas
    return trimmed


def _read_exact_px_per_meter(data: bytes) -> Optional[Tuple[int, int]]:
    """Return the pHYs resolution stored in a PNG, if it has one."""
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"pHYs" and len(body) >= 9:
            if body[8] == 1:                      # unit: metre
                return struct.unpack(">II", body[:8])
            return None
        pos += 12 + length
    return None


def png_as_word_picture(data: bytes, width_mm: float, height_mm: float,
                        dpi: int) -> bytes:
    """Store the physical size in the PNG so Word pastes it at that size.

    Word takes a picture's size from the DPI it derives from the PNG pHYs
    chunk, which only holds whole pixels per metre.  The chunk is therefore
    written with the ratio that lands on the measured width / height, so the
    pasted picture is the size shown in the UI instead of one pixel off.
    """
    if width_mm <= 0 or height_mm <= 0:
        return data
    header = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(header):
        return data
    ihdr_len = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR":
        return data
    width_px, height_px = struct.unpack(">II", data[16:24])
    if width_px <= 0 or height_px <= 0:
        return data
    # whole px/m, rounded to nearest: Word then lands within half a 0.01 mm
    # step of the requested size whatever rounding it uses.  Measured physical
    # sizes carry a sub-pixel remainder from ResizeOptions, so the ratio is
    # tidied to the DPI the user picked instead of the raw pixel/mm quotient.
    x_ppm = y_ppm = float(dpi) * 1000.0 / MM_PER_INCH
    if abs(width_mm * dpi / MM_PER_INCH - width_px) > 1.0:
        x_ppm = width_px * 1000.0 / width_mm
    if abs(height_mm * dpi / MM_PER_INCH - height_px) > 1.0:
        y_ppm = height_px * 1000.0 / height_mm
    x_ppm = int(round(x_ppm))
    y_ppm = int(round(y_ppm))
    body = struct.pack(">IIB", x_ppm, y_ppm, 1)   # unit 1 = metre
    chunk = struct.pack(">I", len(body)) + b"pHYs" + body
    chunk += struct.pack(">I", zlib.crc32(b"pHYs" + body) & 0xFFFFFFFF)
    # drop any existing pHYs (Pillow writes one when dpi is passed) and put the
    # exact chunk straight after IHDR, where readers look for it
    head_end = 8 + 8 + ihdr_len + 4
    out = [header, data[8:head_end], chunk]
    pos = head_end
    while pos + 8 <= len(data):
        clen = struct.unpack(">I", data[pos:pos + 4])[0]
        ckind = data[pos + 4:pos + 8]
        if ckind != b"pHYs":
            out.append(data[pos:pos + 12 + clen])
        pos += 12 + clen
    return b"".join(out)


def reported_px_per_meter(data: bytes) -> Optional[Tuple[int, int]]:
    """Debug/test helper: the pHYs values stored in *data*."""
    return _read_exact_px_per_meter(data)


def _save_png(image: Image.Image, dpi: int,
              size_mm: Optional[Tuple[float, float]] = None) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(dpi, dpi), optimize=True, compress_level=9)
    data = buffer.getvalue()
    if size_mm is not None:
        data = png_as_word_picture(data, size_mm[0], size_mm[1], dpi)
    return data


def _png_budget(image: Image.Image, dpi: int, max_kb: int,
                size_mm: Optional[Tuple[float, float]] = None) -> bytes:
    data = _save_png(image, dpi, size_mm)
    if max_kb <= 0:
        return data
    limit = max_kb * 1024
    if len(data) <= limit:
        return data
    # Shrink until the file fits; never go below 64 px on the short edge.
    current = image
    for _ in range(60):
        if len(data) <= limit:
            return data
        if min(current.size) <= 96:
            break
        current = current.resize(
            (max(1, int(current.width * 0.82)), max(1, int(current.height * 0.82))),
            Image.LANCZOS,
        )
        data = _save_png(current, dpi, size_mm)
    if len(data) > limit:
        # last resort: 8-bit indexed PNG with a hand-built palette
        current = current.convert("PA", palette=Image.ADAPTIVE, colors=64)
        data = _save_png(current, dpi, size_mm)
    return data


def render(text: str, opts: RenderOptions) -> RenderResult:
    """Render *text* with *opts* and return a :class:`RenderResult`."""
    opts = opts.clamped()
    cleaned = normalize_text(text)
    if not cleaned.strip():
        return RenderResult(
            image=Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
            image_bytes=_save_png(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), opts.dpi),
            width_mm=1.0,
            height_mm=1.0,
            dpi=opts.dpi,
            font_keys=[],
            missing=[],
            stored_bytes=0,
        )
    opts = RenderOptions(**{**opts.__dict__, "text": cleaned})

    files = _resolve_font_files(opts)
    width_px, height_px = opts.pixel_size
    pad_x = int(width_px * opts.padding_pct)
    pad_y = int(height_px * opts.padding_pct)
    # a real signature swallows most of the free height next to the caption
    # (the glyph box is taller than the ink), so only keep a hair of padding
    pad_y = min(pad_y, max(2, int(height_px * 0.005)))
    box_w = max(8, width_px - 2 * pad_x)
    box_h = max(8, height_px - 2 * pad_y)

    size_px, glyphs, fonts, missing = _fit_size(opts, files, box_w, box_h)
    # re-measure to get the exact ink box
    glyphs, fonts, missing, ink_w, ink_h = _build_layout(opts, size_px, files)

    ink = build_ink(
        color_key=opts.color_key,
        pen_key=opts.pen_key,
        weight=opts.weight,
        depth=opts.depth,
        blur=opts.blur,
        opacity=opts.opacity,
    )
    # margin big enough for stroke width, halo and blur, so nothing is clipped
    margin = int(size_px * 0.08) + ink.halo_weight + int(math.ceil(ink.blur * 3)) + 4
    origin = (float(margin), float(margin))
    ink_w = int(ink_w + 2 * margin)
    ink_h = int(ink_h + 2 * margin)

    seed = opts.seed if opts.seed else random.randrange(1, 2 ** 31)
    rng = random.Random(seed)

    body_mask = _draw_mask(glyphs, (ink_w, ink_h), origin, ink.body_weight,
                           rng=rng, jitter=opts.jitter)
    if opts.slant:
        body_mask = _shear_mask(body_mask, opts.slant)
    canvas = Image.new("RGBA", (ink_w, ink_h), (0, 0, 0, 0))
    if ink.halo_alpha > 0:
        rng_h = random.Random(seed)
        halo_mask = _draw_mask(glyphs, (ink_w, ink_h), origin, ink.halo_weight,
                               scale=1.006, rng=rng_h, jitter=opts.jitter)
        if opts.slant:
            halo_mask = _shear_mask(halo_mask, opts.slant)
        halo = _colourise(halo_mask, ink.halo_rgb, ink.halo_alpha)
        if ink.blur > 0.05:
            halo = halo.filter(ImageFilter.GaussianBlur(ink.blur * 1.4))
        canvas.alpha_composite(halo)
    body = _colourise(body_mask, ink.body_rgb, ink.body_alpha)
    if ink.blur > 0.05:
        body = body.filter(ImageFilter.GaussianBlur(ink.blur))
    canvas.alpha_composite(body)

    canvas = _grain(canvas, ink.grain, seed)

    # Trim and re-pad, then scale into the requested physical canvas.
    pad_px = max(2.0, height_px * 0.012)
    if opts.auto_size:
        keep_min = (1.0, 1.0)   # trim to the ink itself, Word-friendly
    else:
        keep_min = (opts.width_mm * 0.92, opts.height_mm * 0.92)
    trimmed = _trim(canvas, pad_px, opts.dpi, keep_min)

    scale = max(min(width_px / trimmed.width, height_px / trimmed.height), 0.1)
    if scale != 1.0:
        trimmed = trimmed.resize(
            (max(1, int(round(trimmed.width * scale))),
             max(1, int(round(trimmed.height * scale)))),
            Image.LANCZOS,
        )
    if trimmed.width > width_px or trimmed.height > height_px:
        box = ((trimmed.width - width_px) // 2, (trimmed.height - height_px) // 2)
        trimmed = trimmed.crop((box[0], box[1], box[0] + width_px, box[1] + height_px))

    final_w = trimmed.width if opts.auto_size else width_px
    final_h = trimmed.height if opts.auto_size else height_px
    if not opts.auto_size and (trimmed.width > final_w or trimmed.height > final_h):
        left = (trimmed.width - final_w) // 2
        top = (trimmed.height - final_h) // 2
        trimmed = trimmed.crop((left, top, left + final_w, top + final_h))
    final = Image.new("RGBA", (final_w, final_h), (0, 0, 0, 0))
    final.alpha_composite(trimmed, ((final_w - trimmed.width) // 2,
                                    (final_h - trimmed.height) // 2))

    if opts.color_mode == COLOR_MODE_OPAQUE:
        background = Image.new("RGBA", final.size, (255, 255, 255, 255))
        background.alpha_composite(final)
        final = background

    # the physical size measured from the final pixels, so Word inserts the
    # picture exactly at the width / height chosen in the UI
    size_mm = (final.width / opts.dpi * MM_PER_INCH, final.height / opts.dpi * MM_PER_INCH)
    data = _png_budget(final, opts.dpi, opts.max_file_kb, size_mm)
    # decode back so the returned image matches what was written
    written = Image.open(io.BytesIO(data))
    written.load()
    if opts.color_mode == COLOR_MODE_OPAQUE:
        written = written.convert("RGB")
    result = RenderResult(
        image=written,
        image_bytes=data,
        width_mm=round(final.width / opts.dpi * MM_PER_INCH, 2),
        height_mm=round(final.height / opts.dpi * MM_PER_INCH, 2),
        dpi=opts.dpi,
        font_keys=list(fonts.keys()),
        missing=sorted(set(missing)),
        stored_bytes=len(data),
    )
    return result


def render_to_file(text: str, opts: RenderOptions, path) -> RenderResult:
    result = render(text, opts)
    with open(path, "wb") as fh:
        fh.write(result.image_bytes)
    return result
