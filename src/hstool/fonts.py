# -*- coding: utf-8 -*-
"""Font catalogue, resolution and safe loading.

Font sources, in priority order:

1. **Bundled OFL fonts** (``assets/fonts``): LXGW WenKai (霞鹜文楷) and
   Yozai (悠哉字体). Both are SIL Open Font License 1.1, so they may be
   redistributed inside the portable exe.
2. **Windows system fonts** under ``C:\\Windows\\Fonts``: KaiTi, STXingkai,
   STXinwei, FZShuTi, LiSu, FangSong, Ink Free, Segoe Script. They exist on
   virtually every Windows 10 / 11 machine and are referenced by file name,
   never bundled (they are proprietary).
3. **User fonts**: ``assets\\fonts\\custom`` next to the exe, any ``*.ttf`` /
   ``*.otf`` / ``*.ttc`` dropped there becomes an extra selectable style.

Every font is opened through :func:`open_truetype`, which knows the
PyInstaller one-file trick: Pillow cannot open a font straight from
``sys._MEIPASS`` because FreeType uses plain ``fopen`` and the bundled
directory is extracted lazily. Reading the bytes into memory and letting
Pillow load them from a ``BytesIO`` copy fixes this for every font path.
"""

from __future__ import annotations

import io
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from PIL import ImageFont

from .paths import asset_path, app_dir
from .text import Script, classify, is_cjk

WINDIR = os.environ.get("WINDIR", r"C:\Windows")
SYSTEM_FONTS = Path(WINDIR) / "Fonts"

# Marker used to tell CJK-capable fonts from Latin-only fonts.
_CJK_PROBE = "\u6c38"  # 永


@dataclass(frozen=True)
class FontStyle:
    """One selectable handwriting style."""

    key: str
    name: str            # display name (Chinese)
    name_en: str         # ASCII name used for file names
    files: Sequence[str]  # candidate file names (bundled asset name or system file)
    origin: str          # "bundled" | "system" | "custom"
    description: str
    supports_cjk: bool = True
    latin_fallback: str = ""  # style key used for latin when this font is CJK only


# Order matters: it is the order of the combo box (10 built-in styles).
CATALOG: List[FontStyle] = [
    FontStyle(
        key="kaiti",
        name="标准楷体",
        name_en="KaiTi",
        files=("simkai.ttf",),
        origin="system",
        description="规整端正的楷书，正式合同、保证书最常用",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="xingkai",
        name="行楷（连笔流畅）",
        name_en="XingKai",
        files=("STXINGKA.TTF",),
        origin="system",
        description="行书连笔，书写流畅，签字气息浓厚",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="wenkai",
        name="霞鹜文楷（自然手写）",
        name_en="LXGWWenKai",
        files=("LXGWWenKai-Regular.ttf", "LXGWWenKai-Light.ttf"),
        origin="bundled",
        description="开源手写楷体，笔画自然，中文资料签名首选",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="yozai",
        name="悠哉手写（随性）",
        name_en="Yozai",
        files=("Yozai-Regular.ttf",),
        origin="bundled",
        description="开源简体手写体，字迹轻松随性，适合日常文书",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="xinwei",
        name="新魏碑（稳重有力）",
        name_en="STXinwei",
        files=("STXINWEI.TTF",),
        origin="system",
        description="魏碑风骨，笔画厚重，显得郑重可靠",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="shuti",
        name="舒体（飞动大气）",
        name_en="FZShuTi",
        files=("FZSTK.TTF",),
        origin="system",
        description="行草感强、笔画飞动，适合个性化署名",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="lisu",
        name="隶书（古朴舒展）",
        name_en="LiSu",
        files=("SIMLI.TTF",),
        origin="system",
        description="隶书扁方舒展，古朴大气，适合证明类文书",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="fangsong",
        name="仿宋（公文常用）",
        name_en="FangSong",
        files=("simfang.ttf",),
        origin="system",
        description="仿宋端正，公文资料中常见的签字风格",
        latin_fallback="wenkai",
    ),
    FontStyle(
        key="inkfree",
        name="Ink Free（英文手写）",
        name_en="InkFree",
        files=("Inkfree.ttf",),
        origin="system",
        description="英文手写体，签英文姓名/英文资料",
        supports_cjk=False,
    ),
    FontStyle(
        key="segoescript",
        name="Segoe Script（英文连笔）",
        name_en="SegoeScript",
        files=("segoesc.ttf",),
        origin="system",
        description="英文连笔花体，签英文名很自然",
        supports_cjk=False,
    ),
]

_CUSTOM_DIR_NAMES = ("custom", "用户字体")


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def custom_font_dirs() -> List[Path]:
    """Directories that may add extra user fonts."""
    dirs = [asset_path("fonts", name) for name in _CUSTOM_DIR_NAMES]
    dirs.append(app_dir() / "fonts")
    unique: List[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def _resolve_system(name: str) -> Optional[Path]:
    candidate = SYSTEM_FONTS / name
    if candidate.exists():
        return candidate
    # case-insensitive fallback (FAT / some OEM images)
    try:
        lowered = name.lower()
        for entry in SYSTEM_FONTS.iterdir():
            if entry.name.lower() == lowered and entry.is_file():
                return entry
    except OSError:
        pass
    return None


BUNDLE_DIR_NAMES = ("bundle", "字体包")


def bundle_candidates(key: str) -> List[str]:
    """Pre-subset bundle file names for *key* (see tools/build_bundles.py)."""
    return [
        "%s.woff" % key,
        "%s.woff2" % key,
        "%s.bin" % key,
        "%s.ttf" % key,
    ]


def _bundle_path(key: str) -> Optional[Path]:
    for folder in BUNDLE_DIR_NAMES:
        for name in bundle_candidates(key):
            candidate = asset_path(folder, name)
            if candidate.exists():
                return candidate
    return None


def resolve(style: FontStyle) -> Optional[Path]:
    """Return the font file for *style*, or None when unavailable.

    The pre-subset bundle wins, so the result is identical on every
    machine; the installed source font is only a fallback.
    """
    bundled = _bundle_path(style.key)
    if bundled is not None:
        return bundled
    for name in style.files:
        path = asset_path("fonts", name)
        if path.exists():
            return path
    if style.origin == "system":
        for name in style.files:
            path = _resolve_system(name)
            if path is not None:
                return path
    return None


def discover_custom() -> List[FontStyle]:
    """Find user supplied fonts in the custom font folders."""
    found: List[FontStyle] = []
    seen = set()
    for folder in custom_font_dirs():
        if not folder.is_dir():
            continue
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            key = "custom:" + entry.stem
            if key in seen:
                continue
            seen.add(key)
            found.append(
                FontStyle(
                    key=key,
                    name="%s（自定义）" % entry.stem,
                    name_en=_safe_stem(entry.stem),
                    files=(str(entry),),
                    origin="custom",
                    description="用户自备字体：%s" % entry.name,
                    supports_cjk=font_has_cjk(entry),
                )
            )
    return found


def _safe_stem(stem: str) -> str:
    keep = []
    for ch in stem:
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            keep.append(ch)
    return "".join(keep) or "Custom"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

_font_cache: Dict[str, "_LoadedFont"] = {}
_CJK_SUPPORT: Dict[str, bool] = {}


class _LoadedFont:
    """A font file loaded into memory (or opened in place) for Pillow."""

    __slots__ = ("path", "data", "reusable")

    def __init__(self, path: Path, data: Optional[bytes], reusable: bool):
        self.path = path
        self.data = data
        self.reusable = reusable


def _unpack_bundle(raw: bytes, suffix: str) -> bytes:
    """Turn a pre-subset bundle (see tools/build_bundles.py) into TTF bytes."""
    suffix = suffix.lower()
    if suffix == ".bin":
        return zlib.decompress(raw)
    return raw  # .woff / .woff2 / .ttf / .otf are accepted by FreeType as-is


def _load_font_file(path: Path) -> _LoadedFont:
    """Load a font file. Bundled / _MEIPASS paths are always read into memory."""
    key = str(path)
    cached = _font_cache.get(key)
    if cached is not None and (cached.data is not None or path.exists()):
        return cached
    use_memory = True
    if path.exists() and not _looks_virtual(path) and path.suffix.lower() in (
        ".bin", ".woff", ".woff2"
    ):
        use_memory = True  # bundles must be unpacked, never memory-mapped
    elif path.exists() and not _looks_virtual(path):
        use_memory = False
    data: Optional[bytes] = None
    if use_memory:
        with open(path, "rb") as fh:
            data = fh.read()
        if path.suffix.lower() in (".bin", ".woff", ".woff2"):
            data = _unpack_bundle(data, path.suffix)
    loaded = _LoadedFont(path, data, use_memory)
    _font_cache[key] = loaded
    return loaded


def _looks_virtual(path: Path) -> bool:
    """True when the path lives inside the PyInstaller extraction folder."""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return False
    try:
        return str(path).lower().startswith(str(Path(meipass)).lower())
    except Exception:
        return False


def open_truetype(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Open *path* at pixel *size*.

    Always loads from a bytes buffer when possible so the file handle is not
    kept open - Windows locks font files that are memory-mapped by FreeType
    and that would block users from replacing a font while the app runs.
    """
    size = max(4, int(size))
    loaded = _load_font_file(path)
    if loaded.data is not None:
        return ImageFont.truetype(io.BytesIO(loaded.data), size)
    font = ImageFont.truetype(str(path), size)
    return font


def font_has_cjk(path: Path) -> bool:
    """Cheap check whether a font contains a common CJK ideograph."""
    key = str(path)
    if key in _CJK_SUPPORT:
        return _CJK_SUPPORT[key]
    result = False
    try:
        font = open_truetype(path, 24)
        try:
            mask, _ = font.getmask2(_CJK_PROBE, "L")
            result = mask.size[0] > 0
        finally:
            # Pillow's FreeTypeFont has no close(); TTFont does.
            closer = getattr(font, "close", None)
            if closer is not None:
                closer()
        # "notdef" placeholder glyphs still report a width; verify with
        # fontTools for a definitive answer.
        result = result and _cmap_has(path, 0x6C38)
    except Exception:
        result = False
    _CJK_SUPPORT[key] = result
    return result


def _cmap_has(path: Path, codepoint: int) -> bool:
    try:
        from fontTools.ttLib import TTFont

        loaded = _load_font_file(path)
        if loaded.data is not None:
            font = TTFont(io.BytesIO(loaded.data), fontNumber=0, lazy=True)
        else:
            font = TTFont(str(path), fontNumber=0, lazy=True)
        try:
            cmap = font.getBestCmap() or {}
            return codepoint in cmap
        finally:
            font.close()
    except Exception:
        return True


def font_cmap(path: Path) -> Dict[int, str]:
    """Return the best unicode -> glyph name mapping of *path*."""
    from fontTools.ttLib import TTFont

    loaded = _load_font_file(path)
    if loaded.data is not None:
        font = TTFont(io.BytesIO(loaded.data), fontNumber=0, lazy=True)
    else:
        font = TTFont(str(path), fontNumber=0, lazy=True)
    try:
        return font.getBestCmap() or {}
    finally:
        font.close()


def clear_cache() -> None:
    _font_cache.clear()
    _CJK_SUPPORT.clear()


# --------------------------------------------------------------------------
# catalogue helpers used by the UI
# --------------------------------------------------------------------------

@dataclass
class StyleInfo:
    style: FontStyle
    path: Optional[Path]

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.exists()

    @property
    def supports_cjk(self) -> bool:
        return self.style.supports_cjk


def list_styles(include_custom: bool = True) -> List[StyleInfo]:
    infos = [StyleInfo(style, resolve(style)) for style in CATALOG]
    if include_custom:
        for style in discover_custom():
            path = Path(style.files[0])
            infos.append(StyleInfo(style, path if path.exists() else None))
    return infos


def get_style(key: str) -> FontStyle:
    for style in CATALOG:
        if style.key == key:
            return style
    for style in discover_custom():
        if style.key == key:
            return style
    return CATALOG[0]


def style_for_script(key: str, script: str) -> str:
    """Pick the font key to use for one script run."""
    style = get_style(key)
    if script == Script.CJK:
        if style.supports_cjk:
            return key
        return "kaiti"
    # latin / digits / symbols
    if style.supports_cjk:
        return style.latin_fallback or "inkfree"
    return key


def split_runs(text: str) -> List[tuple]:
    """Split *text* into ``(script, chunk)`` runs (whitespace joins to Latin)."""
    runs: List[tuple] = []
    current_kind: Optional[str] = None
    buffer: List[str] = []
    for ch in text:
        if ch == "\n":
            kind = "newline"
        else:
            kind = classify(ch)
            if kind == Script.SPACE:
                kind = Script.LATIN
            elif kind == Script.DIGIT:
                kind = Script.LATIN
        if ch == "\n":
            if buffer:
                runs.append((current_kind, "".join(buffer)))
                buffer = []
            runs.append(("newline", "\n"))
            current_kind = None
            continue
        if kind != current_kind and current_kind is not None:
            runs.append((current_kind, "".join(buffer)))
            buffer = []
        current_kind = kind
        buffer.append(ch)
    if buffer:
        runs.append((current_kind, "".join(buffer)))
    return runs
