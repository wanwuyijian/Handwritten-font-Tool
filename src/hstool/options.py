# -*- coding: utf-8 -*-
"""Render options and DPI/size conversion helpers.

The exported PNG is *sized in real-world millimetres* because it is going to
be placed into a Word document.  Word decides how large the picture appears
based on its pixel count and the DPI stored in the PNG (pHYs chunk), so this
module is the single place that converts between:

    millimetres  <->  inches  <->  pixels @ DPI

Defaults are tuned for a contract signature line: about 45 mm wide, which
looks right next to a printed "签字：" caption, at 600 DPI (roughly 1060 px,
so the PNG stays around 100 KB while looking crisp when printed).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Tuple

DPI_CHOICES = (150, 200, 300, 400, 600, 800, 1200)
SLANT_MAX = 0.45        # largest italic shear the UI allows (≈24°)

DPI_DEFAULT = 600

MM_PER_INCH = 25.4

# Target sizes expressed in mm (width x height)
SIZE_PRESETS = (
    ("a5", "小号 30 × 10 mm（表格签名格）", (30.0, 10.0)),
    ("contract", "标准 45 × 15 mm（合同/保证书）", (45.0, 15.0)),
    ("a4", "大号 55 × 18 mm（资料/公文）", (55.0, 18.0)),
)

SIZE_DEFAULT = "contract"

COLOR_MODE_TRANSPARENT = "RGBA"
COLOR_MODE_OPAQUE = "RGB"
COLOR_MODE_CHOICES = (
    (COLOR_MODE_TRANSPARENT, "透明背景（推荐，插入 Word）"),
    (COLOR_MODE_OPAQUE, "白色背景"),
)

MAX_PIXELS = 4000


class SizePresetError(ValueError):
    """Raised when a requested canvas cannot be produced."""


def mm_to_px(mm: float, dpi: int) -> int:
    """Convert millimetres to whole pixels at *dpi*."""
    return max(1, int(round(float(mm) / MM_PER_INCH * float(dpi))))


def px_to_mm(px: float, dpi: int) -> float:
    return float(px) / float(dpi) * MM_PER_INCH


def mm_to_emu(mm: float) -> int:
    """Millimetres to English Metric Units, the unit Word stores in a picture."""
    return int(round(float(mm) * 36000.0))


@dataclass(frozen=True)
class RenderOptions:
    """Everything that influences the rendered bitmap."""

    text: str = ""
    font_key: str = "wenkai"
    width_mm: float = 45.0
    height_mm: float = 15.0
    dpi: int = DPI_DEFAULT
    pen_key: str = "fountain"
    color_key: str = "black"
    # UI sliders -----------------------------------------------------------
    weight: float = 1.0      # 0.2 .. 3.0 stroke weight multiplier
    depth: float = 0.9       # 0.0 .. 1.0 ink colour depth
    blur: float = 0.04       # 0.0 .. 1.0 blur amount
    opacity: float = 1.0     # 0.3 .. 1.0 overall ink opacity
    # layout ---------------------------------------------------------------
    padding_pct: float = 0.04      # inner margin as a fraction of the canvas
    line_gap: float = 0.16         # extra gap between lines (em fraction)
    jitter: float = 0.35           # 0..1 random per-glyph rotation / offset wobble
    slant: float = 0.0             # -0.45 .. 0.45 italic lean (positive = to the right)
    seed: int = 0                  # 0 = random each render
    color_mode: str = COLOR_MODE_TRANSPARENT
    max_file_kb: int = 0           # >0: re-encode to stay under this size
    auto_size: bool = True         # trim the canvas to the ink before padding

    # derived --------------------------------------------------------------
    @property
    def width_px(self) -> int:
        return mm_to_px(self.width_mm, self.dpi)

    @property
    def height_px(self) -> int:
        return mm_to_px(self.height_mm, self.dpi)

    @property
    def pixel_size(self) -> Tuple[int, int]:
        return (self.width_px, self.height_px)

    def with_text(self, text: str) -> "RenderOptions":
        return replace(self, text=text)

    def clamped(self) -> "RenderOptions":
        """Clamp numeric fields into their valid ranges."""
        def clamp(value: float, lo: float, hi: float) -> float:
            return lo if value < lo else hi if value > hi else value

        width = clamp(float(self.width_mm), 5.0, 250.0)
        height = clamp(float(self.height_mm), 4.0, 120.0)
        dpi = int(clamp(float(self.dpi), 96, 1200))
        # keep the canvas sane for very large requests
        if width / MM_PER_INCH * dpi > MAX_PIXELS:
            dpi = int(MAX_PIXELS / (width / MM_PER_INCH))
            dpi = max(96, dpi)
        if height / MM_PER_INCH * dpi > MAX_PIXELS:
            dpi = int(MAX_PIXELS / (height / MM_PER_INCH))
            dpi = max(96, dpi)
        return replace(
            self,
            width_mm=round(width, 2),
            height_mm=round(height, 2),
            dpi=dpi,
            weight=round(clamp(float(self.weight), 0.2, 3.0), 2),
            depth=round(clamp(float(self.depth), 0.0, 1.0), 2),
            blur=round(clamp(float(self.blur), 0.0, 1.0), 2),
            opacity=round(clamp(float(self.opacity), 0.3, 1.0), 2),
            padding_pct=clamp(float(self.padding_pct), 0.0, 0.2),
            line_gap=clamp(float(self.line_gap), 0.0, 1.0),
            jitter=clamp(float(self.jitter), 0.0, 1.0),
            slant=round(clamp(float(self.slant), -SLANT_MAX, SLANT_MAX), 3),
            max_file_kb=int(clamp(float(self.max_file_kb), 0, 5000)),
        )

    def size_label(self) -> str:
        return "%.1f × %.1f mm @ %d DPI（%d × %d px）" % (
            self.width_mm,
            self.height_mm,
            self.dpi,
            self.width_px,
            self.height_px,
        )


def preset_size(preset_key: str) -> Tuple[float, float]:
    for key, _label, size in SIZE_PRESETS:
        if key == preset_key:
            return size
    for key, _label, size in SIZE_PRESETS:
        if key == SIZE_DEFAULT:
            return size
    return (45.0, 15.0)
