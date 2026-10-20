# -*- coding: utf-8 -*-
"""Build the portable font bundles.

For every built-in style we take a source font, keep only the glyphs needed
for the chosen character set and store a compressed copy under
``assets/bundle/``:

* OFL fonts (WenKai, Yozai, ...) -> ``<key>.woff``  (WOFF = zlib wrapped TTF)
* Windows system fonts          -> ``<key>.bin``    (raw-deflated TTF)

At run time ``hstool.fonts`` decompresses a bundle **in memory** and hands the
bytes to Pillow.  That is what makes the portable exe behave the same on every
Windows 10/11 machine, including ones without the Chinese language pack.

Two groups of bundles exist:

* **core** (default): the OFL fonts.  Small, redistributable, shipped in git
  and inside the release exe.
* **system** (``--with-system``): the Windows CJK system fonts.  They are
  large (about 2-7 MB each, 25 MB in total) and are proprietary, so they are
  *not* committed to the repository.  Build them locally on a Windows machine
  that has the fonts installed; the resulting ``assets/bundle/*.bin`` files are
  only used by the local exe build and are ignored by git (``.gitignore``).
  Without them the tool falls back to reading the installed font from
  ``C:\\Windows\\Fonts`` at run time, which works on every Chinese Windows.

Usage::

    python tools/build_bundles.py                         # core bundles only
    python tools/build_bundles.py --with-system           # + Windows CJK fonts
    python tools/build_bundles.py --with-system --verify  # + render a sample
    python tools/build_bundles.py --list                  # show what would be built
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zlib
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT_DIR = ASSETS / "bundle"
CHARSET_FILE = ASSETS / "charset" / "cn.txt"
MANIFEST = ASSETS / "bundle" / "manifest.json"

WINDIR = Path(r"C:\Windows\Fonts")

# (bundle key, source path, format, note)
CORE_SOURCES = (
    ("wenkai", ASSETS / "fonts" / "LXGWWenKai-Regular.ttf", "woff", "OFL 1.1 - LXGW WenKai"),
    ("yozai", ASSETS / "fonts" / "Yozai-Regular.ttf", "woff", "OFL 1.1 - Yozai"),
)

SYSTEM_SOURCES = (
    ("kaiti", WINDIR / "simkai.ttf", "zlib", "Windows system font"),
    ("xingkai", WINDIR / "STXINGKA.TTF", "zlib", "Windows system font"),
    ("xinwei", WINDIR / "STXINWEI.TTF", "zlib", "Windows system font"),
    ("shuti", WINDIR / "FZSTK.TTF", "zlib", "Windows system font"),
    ("lisu", WINDIR / "SIMLI.TTF", "zlib", "Windows system font"),
    ("fangsong", WINDIR / "simfang.ttf", "zlib", "Windows system font"),
    ("inkfree", WINDIR / "Inkfree.ttf", "zlib", "Windows system font"),
    ("segoescript", WINDIR / "segoesc.ttf", "zlib", "Windows system font"),
)


def read_charset() -> str:
    text = CHARSET_FILE.read_text(encoding="utf-8")
    extra = "\r\t\u00a5\uffe5\u00a0"
    return text + extra


def build(src: Path, charset: str, fmt: str) -> tuple[bytes, int, int]:
    font = TTFont(str(src))
    cmap = font.getBestCmap()
    wanted = set(ord(ch) for ch in charset)
    available = [cp for cp in wanted if cp in cmap]
    options = subset.Options()
    options.drop_tables = []
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.hinting = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=available)
    subsetter.subset(font)
    font.flavor = fmt if fmt in ("woff", "woff2") else None
    buffer = io.BytesIO()
    font.save(buffer)
    data = buffer.getvalue()
    if fmt == "zlib":
        data = zlib.compress(data, 9)
    return data, len(available), len(wanted)


def build_all(sources) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    charset = read_charset()
    # keep the entries of bundles that are not rebuilt this run
    manifest = {"charset_size": len(charset), "fonts": {}}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["charset_size"] = len(charset)
            manifest.setdefault("fonts", {})
        except Exception:
            pass
    for key, src, fmt, note in sources:
        if not src.exists():
            print("SKIP %-10s missing %s" % (key, src), flush=True)
            continue
        t0 = time.time()
        data, got, wanted = build(src, charset, fmt)
        suffix = "woff" if fmt == "woff" else "bin"
        out = OUT_DIR / ("%s.%s" % (key, suffix))
        out.write_bytes(data)
        manifest["fonts"][key] = {
            "file": out.name,
            "format": "woff" if fmt == "woff" else "zlib",
            "bytes": len(data),
            "chars": got,
            "source": src.name,
            "note": note,
        }
        print(
            "%-10s %6d / %6d chars  %7d KB  %5.1fs -> %s"
            % (key, got, wanted, len(data) // 1024, time.time() - t0, out.name),
            flush=True,
        )
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(item["bytes"] for item in manifest["fonts"].values())
    print("total bundle size: %.1f MB" % (total / 1048576), flush=True)
    return manifest


def verify() -> None:
    from PIL import Image, ImageDraw, ImageFont

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sample = "\u5f20\u4e09\u4e30 \u7b7e\u540d ABC 2026"
    for key, item in manifest["fonts"].items():
        path = OUT_DIR / item["file"]
        raw = path.read_bytes()
        data = zlib.decompress(raw) if item["format"] == "zlib" else raw
        font = ImageFont.truetype(io.BytesIO(data), 64)
        image = Image.new("L", (800, 120), 0)
        ImageDraw.Draw(image).text((8, 8), sample, font=font, fill=255)
        box = image.getbbox()
        print("%-10s verify %s bbox=%s" % (key, "OK " if box else "FAIL", box), flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="render a sample with every bundle")
    parser.add_argument("--list", action="store_true", help="list sources and exit")
    parser.add_argument(
        "--with-system", action="store_true",
        help="also build the (large, proprietary) Windows CJK font bundles",
    )
    args = parser.parse_args(argv)
    sources = CORE_SOURCES + SYSTEM_SOURCES if args.with_system else CORE_SOURCES
    if args.list:
        for key, src, fmt, note in sources:
            state = "ok" if src.exists() else "MISSING"
            print("%-10s %-8s %-6s %s  (%s)" % (key, fmt, state, src.name, note))
        return 0
    build_all(sources)
    if args.verify:
        verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
