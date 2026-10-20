# -*- coding: utf-8 -*-
"""Collect a character corpus and build the bundled character set.

Sources, merged then de-duplicated:

1. ``corpus/common.txt``    - manually curated most frequent Han characters.
2. ``corpus/system.txt``    - every unique character found in Windows system
   binaries (``System32\\*``), which are full of Chinese UI strings, error
   messages, legal phrases and place names - a surprisingly good corpus.
3. GB2312 level 1 + 2 (6 763 Han characters, the de-facto standard set that
   "common Chinese" is measured against).
4. Non ideograph text characters used by contracts: ASCII, CJK punctuation,
   full-width forms, currency signs, units.

Result -> ``assets/charset/cn.txt`` (UTF-8, unique characters, deterministic
order so the file diffs cleanly).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "assets" / "corpus"
OUT = ROOT / "assets" / "charset" / "cn.txt"

SYSTEM_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def gb2312_chars() -> set:
    chars = set()
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return chars


def gb2312_text() -> set:
    chars = set()
    for hi in range(0xA1, 0xFF):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    chars.update(chr(cp) for cp in range(0x20, 0x7F))
    for start, end in (
        (0x2000, 0x2070),
        (0x3000, 0x3040),
        (0xFF00, 0xFFF0),
        (0x2190, 0x21A0),
    ):
        chars.update(chr(cp) for cp in range(start, end))
    chars.update("\u00a5\u20ac\u00b0\u00b1\u00d7\u00f7\u00a9\u00ae\u2122\u00a4\u00a7\u00a8")
    return chars


def scan_system(limit: int = 0) -> set:
    found = set()
    if not SYSTEM_DIR.is_dir():
        return found
    files = sorted(SYSTEM_DIR.glob("*.exe")) + sorted(SYSTEM_DIR.glob("*.dll"))
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            text = data.decode("utf-16-le", errors="ignore")
        except Exception:
            text = ""
        found.update(HAN.findall(text))
        if limit and len(found) > limit:
            break
    return found


def read_corpus() -> set:
    found = set()
    if not CORPUS.is_dir():
        return found
    for path in CORPUS.glob("*.txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found.update(HAN.findall(text))
    return found


def main() -> None:
    sys_corpus = read_corpus()
    scanned = scan_system()
    gb_han = gb2312_chars()
    print("manual corpus : %5d" % len(sys_corpus), flush=True)
    print("system scan   : %5d" % len(scanned), flush=True)
    print("gb2312        : %5d" % len(gb_han), flush=True)

    han = set(gb_han)
    han.update(c for c in sys_corpus if HAN.fullmatch(c))
    han.update(c for c in scanned if HAN.fullmatch(c))
    text = set(gb2312_text()) | {c for c in sys_corpus | scanned if not HAN.fullmatch(c)}
    # keep only printable, non-control characters
    text = {c for c in text if c.isprintable() or c == " "}

    ordered = "".join(sorted(text)) + "".join(sorted(han))
    print("total         : %5d characters" % len(ordered), flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(ordered, encoding="utf-8", newline="")
    print("wrote", OUT, OUT.stat().st_size, "bytes", flush=True)


if __name__ == "__main__":
    main()
