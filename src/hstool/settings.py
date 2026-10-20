# -*- coding: utf-8 -*-
"""Persistent user settings (``settings.json`` next to the exe).

Only the options that are worth remembering are stored; the text itself is
deliberately *not* persisted so a shared computer does not leak signatures.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict

from .options import DPI_DEFAULT, SIZE_DEFAULT, SLANT_MAX, preset_size
from .paths import app_dir, ensure_dir

SETTINGS_FILE = "settings.json"
SCHEMA_VERSION = 1


@dataclass
class Settings:
    font_key: str = "kaiti"
    pen_key: str = "fountain"
    color_key: str = "black"
    weight: int = 50          # slider 0..100 -> 0.2..3.0
    depth: int = 85           # slider 0..100 -> ink colour depth
    blur: int = 8             # slider 0..100 -> blur amount
    opacity: int = 100        # slider 0..100 -> overall ink opacity
    size_preset: str = SIZE_DEFAULT
    width_mm: float = preset_size(SIZE_DEFAULT)[0]
    height_mm: float = preset_size(SIZE_DEFAULT)[1]
    dpi: int = DPI_DEFAULT
    transparent: bool = True
    auto_size: bool = True
    jitter: int = 35          # 0..100 handwriting wobble
    slant: int = 50           # 0..100 -> 0 = 24 deg left, 50 = upright, 100 = 24 deg right
    max_file_kb: int = 0      # 0 = no limit
    last_save_dir: str = ""
    window_geometry: str = ""

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in (data or {}).items() if k in known}
        settings = cls()
        for key, value in values.items():
            current = getattr(settings, key)
            try:
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, int) and not isinstance(current, bool):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                else:
                    value = str(value) if value is not None else ""
            except (TypeError, ValueError):
                continue
            setattr(settings, key, value)
        return settings

    # convenience conversions ------------------------------------------
    def slider_weight(self) -> float:
        return 0.2 + (self.weight / 100.0) * 2.8

    def slider_depth(self) -> float:
        return self.depth / 100.0

    def slider_blur(self) -> float:
        return self.blur / 100.0

    def slider_opacity(self) -> float:
        return 0.3 + (self.opacity / 100.0) * 0.7

    def slider_jitter(self) -> float:
        return self.jitter / 100.0

    def slider_slant(self) -> float:
        """Slider 0..100 -> -SLANT_MAX..+SLANT_MAX (50 = upright)."""
        return (self.slant - 50) / 50.0 * SLANT_MAX


def settings_path() -> Path:
    return app_dir() / SETTINGS_FILE


def load(path: Path | None = None) -> Settings:
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return Settings.from_dict(data)


def save(settings: Settings, path: Path | None = None) -> bool:
    target = path or settings_path()
    ensure_dir(target.parent)
    try:
        target.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
