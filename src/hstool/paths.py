# -*- coding: utf-8 -*-
"""Filesystem helpers: frozen detection, app/resource dirs, atomic writes."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory of the running executable (the .exe folder when frozen)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def resource_dir() -> Path:
    """Directory holding bundled read-only resources (assets/...)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent.parent


def asset_path(*parts: str) -> Path:
    """Resolve a bundled asset (frozen bundle first, then the source tree)."""
    candidate = resource_dir().joinpath("assets", *parts)
    if candidate.exists():
        return candidate
    return app_dir().joinpath("assets", *parts)


def ensure_dir(path: Path) -> Path:
    """Create *path* (and parents) when missing."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file + os.replace)."""
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def shorten(text: str, limit: int = 40) -> str:
    """Shorten *text* for log lines."""
    text = (text or "").replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
