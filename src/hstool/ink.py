# -*- coding: utf-8 -*-
"""Ink and pen definitions used by the renderer and the UI.

Every pen is drawn with two layers so the result looks like real stationery
instead of "text with a font":

* the **body** - the glyph rendered at full weight, carrying the stroke;
* the **halo**  - a slightly larger, lighter copy underneath, simulating ink
  bleeding into the paper (fountain pen) or the soft graphite smear of a
  pencil.

The pen type changes the halo strength, graininess and edge softness.  The
colour choice sets the hue.  Sliders then scale stroke weight, ink depth and
blur on top of these base values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

RGB = Tuple[int, int, int]

# Base ink colours: slightly off pure RGB, like real ink on paper.
COLORS: Dict[str, Tuple[str, RGB]] = {
    "black": ("黑色", (24, 24, 28)),
    "red": ("红色", (186, 26, 40)),
    "blue": ("蓝色", (18, 48, 158)),
}

# pen key -> attributes used by render.py
PEN_TYPES: Dict[str, dict] = {
    "fountain": {
        # wet ink: deep colour, soft paper-bleed halo, almost no grain
        "label": "钢笔（墨水笔）",
        "en": "Fountain",
        "core_alpha": 1.00,
        "halo_alpha": 0.26,
        "halo_scale": 1.12,      # extra body width added to the halo copy
        "grain": 0.03,
        "edge": 1.9,
        "core_darken": 0.80,     # multiply factor of base colour
        "halo_wash": 0.88,       # halo mixes base colour with white
    },
    "ballpoint": {
        # paste ink: hard edge, thin uniform line, little bleed
        "label": "圆珠笔",
        "en": "Ballpoint",
        "core_alpha": 0.95,
        "halo_alpha": 0.10,
        "halo_scale": 0.9,
        "grain": 0.06,
        "edge": 0.5,
        "core_darken": 0.96,
        "halo_wash": 0.62,
    },
    "pencil": {
        # graphite: pale, very grainy, soft smudge, clearly softer than ink
        "label": "铅笔",
        "en": "Pencil",
        "core_alpha": 0.74,
        "halo_alpha": 0.20,
        "halo_scale": 1.20,
        "grain": 0.55,
        "edge": 2.4,
        "core_darken": 1.35,
        "halo_wash": 0.50,
    },
}

PEN_ORDER = ("fountain", "ballpoint", "pencil")
COLOR_ORDER = ("black", "red", "blue")

# (label, swatch colour) shown in the UI
COLOR_LABELS = {
    "black": ("黑色", "#18181c"),
    "red": ("红色", "#ba1a28"),
    "blue": ("蓝色", "#12309e"),
}

PEN_LABELS = {
    "fountain": "钢笔",
    "ballpoint": "圆珠笔",
    "pencil": "铅笔",
}


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def shade(rgb: RGB, factor: float) -> RGB:
    """Scale an RGB triple toward black (factor < 1) or white (factor > 1)."""
    out = []
    for component in rgb:
        if factor <= 1.0:
            value = component * factor
        else:
            value = component + (255 - component) * (factor - 1.0)
        out.append(int(clamp(round(value), 0, 255)))
    return (out[0], out[1], out[2])


def blend(a: RGB, b: RGB, t: float) -> RGB:
    """Linear blend; t=0 -> a, t=1 -> b."""
    t = clamp(t, 0.0, 1.0)
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


@dataclass(frozen=True)
class InkSpec:
    """Resolved ink description handed to the renderer."""

    color_key: str
    pen_key: str
    body_rgb: RGB
    halo_rgb: RGB
    body_alpha: int
    halo_alpha: int
    body_weight: int
    halo_weight: int
    blur: float
    grain: float

    @property
    def hex_color(self) -> str:
        return "#%02x%02x%02x" % self.body_rgb


def build_ink(
    color_key: str = "black",
    pen_key: str = "fountain",
    weight: float = 1.0,
    depth: float = 0.9,
    blur: float = 0.12,
    opacity: float = 1.0,
) -> InkSpec:
    """Resolve everything the renderer needs from the user's slider values.

    weight  0.2 .. 3.0  stroke weight multiplier
    depth   0.0 .. 1.0  ink colour depth (0 = pale, 1 = full strength)
    blur    0.0 .. 1.0  softness / fuzziness
    opacity 0.3 .. 1.0  overall ink opacity
    """
    color_key = color_key if color_key in COLORS else "black"
    pen_key = pen_key if pen_key in PEN_TYPES else "fountain"
    weight = clamp(float(weight), 0.2, 3.0)
    depth = clamp(float(depth), 0.0, 1.0)
    blur = clamp(float(blur), 0.0, 1.0)
    opacity = clamp(float(opacity), 0.3, 1.0)

    pen = PEN_TYPES[pen_key]
    base = COLORS[color_key][1]

    # Depth: at 0 the ink is thin and washed out (pale colour, low opacity),
    # at 1 it is opaque and at full pigment strength.  Dilution is a wash
    # toward neutral grey (thin ink on white paper), never a darker colour,
    # so the darkest pixels always grow with depth.
    # never a darker colour, so the darkest pixels always grow with depth.
    body = shade(base, pen["core_darken"] * (0.55 + 0.45 * depth))
    body = blend(body, (246, 246, 246), 0.55 * (1.0 - depth))
    halo = blend(base, (255, 255, 255), 1.0 - pen["halo_wash"] * (0.35 + 0.65 * depth))

    body_alpha = int(round(255 * pen["core_alpha"] * (0.62 + 0.38 * depth) * opacity))
    halo_alpha = int(round(255 * pen["halo_alpha"] * (0.35 + 0.65 * depth) * opacity))

    body_weight = int(round(clamp(1.0 + (weight - 1.0) * 1.6, 1, 6)))
    halo_weight = int(round(clamp(body_weight + pen["halo_scale"] + weight * 0.8, 1, 9)))
    effective_blur = clamp(blur * 1.6 + pen["edge"] * 0.42, 0.0, 3.2)
    grain = clamp(pen["grain"] * (1.0 - 0.6 * depth) * (0.25 + blur * 1.2), 0.0, 0.32)

    return InkSpec(
        color_key=color_key,
        pen_key=pen_key,
        body_rgb=body,
        halo_rgb=halo,
        body_alpha=max(20, body_alpha),
        halo_alpha=max(0, halo_alpha),
        body_weight=body_weight,
        halo_weight=halo_weight,
        blur=effective_blur,
        grain=grain,
    )


def pen_label(pen_key: str) -> str:
    return PEN_TYPES.get(pen_key, PEN_TYPES["fountain"])["label"]


def color_label(color_key: str) -> str:
    return COLORS.get(color_key, COLORS["black"])[0]
