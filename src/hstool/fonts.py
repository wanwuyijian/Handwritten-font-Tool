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

# Extra folders scanned next to the exe / repository root.  Users keep their
# downloaded fonts in any of these (``ZiTi`` is the folder the maintainer of
# this project uses), and a missing folder is simply skipped.
_EXTRA_DIR_NAMES = ("fonts", "字体", "ZiTi", "ziti", "字体文件")

# Fonts are only looked for one level deep: a whole disk scan would be slow
# and would pick up hundreds of unusable faces.
_SCAN_DEPTH = 1


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def custom_font_dirs() -> List[Path]:
    """Directories that may add extra user fonts (checked in this order)."""
    dirs = [asset_path("fonts", name) for name in _CUSTOM_DIR_NAMES]
    for name in _EXTRA_DIR_NAMES:
        dirs.append(asset_path("fonts", name))
        dirs.append(app_dir() / name)
    unique: List[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def primary_font_dir() -> Path:
    """The folder the UI opens and the one users are told to drop fonts into.

    The portable package ships a writable ``fonts\\`` folder next to the exe,
    so that wins; a frozen build's ``_MEIPASS`` copy is read-only and must
    never be offered.
    """
    app_fonts = app_dir() / "fonts"
    if app_fonts.is_dir():
        return app_fonts
    for folder in custom_font_dirs():
        if _looks_virtual(folder):
            continue
        if folder.is_dir() and _iter_font_files(folder, _SCAN_DEPTH):
            return folder
    return app_fonts


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


_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


def _iter_font_files(folder: Path, depth: int) -> List[Path]:
    """Font files in *folder*, walking at most *depth* levels down."""
    files: List[Path] = []
    stack = [(folder, 0)]
    while stack:
        current, level = stack.pop()
        if not current.is_dir():
            continue
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if level < depth:
                    stack.append((entry, level + 1))
                continue
            if entry.suffix.lower() in _FONT_SUFFIXES:
                files.append(entry)
    return files


def discover_custom() -> List[FontStyle]:
    """Find user supplied fonts in the custom font folders.

    Every folder returned by :func:`custom_font_dirs` is scanned one level
    deep, so both a flat drop-in folder and the common
    ``字体/厂家/xxx.ttf`` layout work.  Fonts are de-duplicated by file name
    and by the file itself, so the same face dropped into two folders only
    shows up once.
    """
    found: List[FontStyle] = []
    seen_keys = set()
    seen_files = set()
    for folder in custom_font_dirs():
        for entry in _iter_font_files(folder, _SCAN_DEPTH):
            try:
                real = entry.resolve()
            except OSError:
                real = entry
            if real in seen_files:
                continue
            key = "custom:" + entry.stem
            if key in seen_keys:
                continue
            seen_keys.add(key)
            seen_files.add(real)
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
    found.sort(key=lambda style: style.name.lower())
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
    cached = _cjk_cache_get(path)
    if cached is not None:
        _CJK_SUPPORT[key] = cached
        return cached
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
    _cjk_cache_put(path, result)
    return result


# --------------------------------------------------------------------------
# CJK-support cache
#
# Probing ~20 CJK fonts costs well over a second (fontTools has to parse the
# cmap table of every file), which used to be paid on every start and on every
# "refresh fonts" click.  The verdict only depends on the file itself, so it is
# cached in a small JSON file next to the exe, keyed by path + size + mtime.
# Any failure (read-only folder, corrupt cache) is silent: the probe simply
# runs again.
# --------------------------------------------------------------------------

_CJK_CACHE_NAME = "font-cache.json"


def _cjk_cache_path() -> Path:
    return app_dir() / _CJK_CACHE_NAME


def _cjk_cache_key(path: Path) -> Optional[str]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return "%s|%d|%d" % (str(path).lower(), stat.st_size, int(stat.st_mtime))


_cjk_disk_cache: Optional[Dict[str, bool]] = None


def _cjk_cache_load() -> Dict[str, bool]:
    global _cjk_disk_cache
    if _cjk_disk_cache is not None:
        return _cjk_disk_cache
    data: Dict[str, bool] = {}
    try:
        import json

        with open(_cjk_cache_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(key, str) and isinstance(value, bool):
                    data[key] = value
    except Exception:
        data = {}
    _cjk_disk_cache = data
    return data


def _cjk_cache_get(path: Path) -> Optional[bool]:
    key = _cjk_cache_key(path)
    if key is None:
        return None
    return _cjk_cache_load().get(key)


def _cjk_cache_put(path: Path, value: bool) -> None:
    key = _cjk_cache_key(path)
    if key is None:
        return
    cache = _cjk_cache_load()
    if cache.get(key) == value:
        return
    cache[key] = value
    try:
        import json

        tmp = Path(str(_cjk_cache_path()) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, _cjk_cache_path())
    except Exception:
        pass


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
