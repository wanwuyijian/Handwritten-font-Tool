# -*- coding: utf-8 -*-
"""Text normalization and language detection.

The tool must cope with Chinese (Simplified / Traditional) and English input
that is often copy-pasted from Word documents, PDFs or chat apps.  Those
sources inject zero-width characters, non-breaking spaces and full-width
punctuation, which would otherwise show up as missing glyphs ("tofu") in the
rendered signature.

Public functions
----------------
normalize_text(text)  -> str            cleaned text ready for rendering
analyze(text)         -> TextReport     character statistics
detect_script(...)    -> list[Script]   script runs used to pick a font
"""

from __future__ import annotations

import re

import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List

# Removed outright: zero-width / invisible formatting characters and control
# characters (except the line break itself).
_INVISIBLE = {
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u2060",  # word joiner
    "\ufeff",  # byte order mark
    "\u00ad",  # soft hyphen
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
    "\u2066",  # left-to-right isolate
    "\u2067",  # right-to-left isolate
    "\u2068",  # first strong isolate
    "\u2069",  # pop directional isolate
    "\u0000",
}

# Whitespace variants normalized to a plain space.
_SPACE_CHARS = {
    "\u00a0",  # no-break space
    "\u2007",  # figure space
    "\u2009",  # thin space
    "\u202f",  # narrow no-break space
    "\u3000",  # ideographic space
    "\t",
    "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
    "\u2008", "\u200a",
}


def to_halfwidth(text: str) -> str:
    """Convert full-width ASCII variants (FF01-FF5E) to normal ASCII."""
    out: List[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0xFF5F or code == 0xFF60:
            out.append("(" if code == 0xFF5F else ")")
        else:
            out.append(ch)
    return "".join(out)


def is_invisible(ch: str) -> bool:
    """True for characters that never take ink: zero-width marks, controls, BOMs."""
    if ch in _INVISIBLE:
        return True
    if ch in ("\n", "\r", "\t"):
        return False
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Cs", "Co", "Cn")


def strip_invisible(text: str) -> str:
    """Drop zero-width characters, BOMs and other controls; keep \n / \r."""
    return "".join(ch for ch in text if not is_invisible(ch))


def normalize_text(text: str) -> str:
    """Normalize *text* for rendering.

    * strips invisible characters
    * converts CRLF / CR to LF
    * converts ideographic and other exotic spaces to a normal space
    * collapses runs of spaces and trims trailing whitespace on every line
    * removes blank lines at the start/end of the block
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for raw_line in text.split("\n"):
        chars: List[str] = []
        invisible = False
        for ch in raw_line:
            if ch in _SPACE_CHARS:
                # A space right after an invisible / zero-width character is
                # copy-paste layout noise, not a word gap: drop it.
                if invisible:
                    continue
                chars.append(" ")
                invisible = False
            elif is_invisible(ch):
                invisible = True
            else:
                chars.append(ch)
                invisible = False
        line = "".join(chars)
        # collapse repeated spaces
        collapsed: List[str] = []
        previous_space = False
        for ch in line:
            if ch == " ":
                if previous_space:
                    continue
                previous_space = True
            else:
                previous_space = False
            collapsed.append(ch)
        lines.append("".join(collapsed).strip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# language / script analysis
# --------------------------------------------------------------------------

CJK_RANGES = (
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F),  # CJK Extension B .. Compat. Ideographs Supplement
    (0x30000, 0x323AF),  # CJK Extension G/H
)

# Characters that are part of Chinese text but are not ideographs themselves.
CJK_PUNCT = set("，。、；：？！“”‘’（）《》〈〉【】〔〕—…·～￥　,.;:?!()[]")


class Script:
    CJK = "CJK"
    LATIN = "Latin"
    DIGIT = "Digit"
    SPACE = "Space"
    OTHER = "Other"


def is_cjk(ch: str) -> bool:
    """True for CJK ideographs (also covers the Japanese/Korean han reuse)."""
    code = ord(ch)
    for lo, hi in CJK_RANGES:
        if lo <= code <= hi:
            return True
    return False


def is_kana_or_hangul(ch: str) -> bool:
    code = ord(ch)
    return (0x3040 <= code <= 0x30FF) or (0xAC00 <= code <= 0xD7AF) or (
        0x1100 <= code <= 0x11FF
    )


def is_latin(ch: str) -> bool:
    code = ord(ch)
    return (
        0x0041 <= code <= 0x005A
        or 0x0061 <= code <= 0x007A
        or 0x00C0 <= code <= 0x024F
        or 0x1E00 <= code <= 0x1EFF
    )


def is_digit(ch: str) -> bool:
    return ch.isdigit()


def classify(ch: str) -> str:
    if ch == " ":
        return Script.SPACE
    if is_cjk(ch) or ch in CJK_PUNCT or is_kana_or_hangul(ch):
        return Script.CJK
    if is_latin(ch):
        return Script.LATIN
    if is_digit(ch):
        return Script.DIGIT
    return Script.OTHER


@dataclass
class TextReport:
    """Result of :func:`analyze`."""

    total: int = 0
    cjk: int = 0
    latin: int = 0
    digits: int = 0
    spaces: int = 0
    other: int = 0
    unknown: List[str] = field(default_factory=list)

    @property
    def languages(self) -> List[str]:
        names: List[str] = []
        if self.cjk:
            names.append("中文")
        if self.latin:
            names.append("English")
        if self.digits and not self.latin:
            names.append("数字")
        if not names:
            names.append("符号")
        return names

    def summary(self, limit: int = 6) -> str:
        parts = ["%d 字符" % self.total]
        if self.cjk:
            parts.append("汉字 %d" % self.cjk)
        if self.latin:
            parts.append("英文字母 %d" % self.latin)
        if self.digits:
            parts.append("数字 %d" % self.digits)
        if self.other:
            parts.append("符号 %d" % self.other)
        text = " · ".join(parts)
        if self.unknown:
            names = "".join(repr(c)[1:-1] for c in self.unknown[:limit])
            more = "" if len(self.unknown) <= limit else " 等 %d 个" % len(
                self.unknown
            )
            text += "  ⚠ 字体缺字：%s%s" % (names, more)
        return text


def analyze(text: str) -> TextReport:
    """Count characters by script class."""
    report = TextReport()
    for ch in text:
        if ch == "\n":
            continue
        report.total += 1
        kind = classify(ch)
        if kind == Script.CJK:
            report.cjk += 1
        elif kind == Script.LATIN:
            report.latin += 1
        elif kind == Script.DIGIT:
            report.digits += 1
        elif kind == Script.SPACE:
            report.spaces += 1
        else:
            report.other += 1
    return report


def script_runs(text: str) -> List[str]:
    """Return the script classes present, in first-appearance order."""
    seen: List[str] = []
    for ch in text:
        kind = classify(ch)
        if kind in (Script.SPACE, Script.OTHER):
            continue
        if kind not in seen:
            seen.append(kind)
    return seen


def iter_chars(text: Iterable[str]) -> List[str]:
    return [ch for ch in text]
