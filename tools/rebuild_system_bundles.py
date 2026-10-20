# -*- coding: utf-8 -*-
"""Rebuild system-font bundles with a smaller, signature-relevant charset.

System fonts are NOT committed to git (proprietary); they are packed into the
local exe only when build.ps1 runs with -BundleSystemFonts.  Subsetting them to
the ~8 k characters a Chinese signature realistically needs keeps the portable
exe at a sane size.
"""
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import build_bundles as BB  # noqa: E402

LIMIT = 8000
ALWAYS = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341"
AUTO_REPORT = Path(r"H:\tmp_fonts\sysfont_report.json")


def limited_charset() -> str:
    extra = ALWAYS
    if AUTO_REPORT.exists():
        extra += json.loads(AUTO_REPORT.read_text(encoding="utf-8")).get("chars", "")
    base = BB.read_charset()
    seen, out = set(), []
    for ch in extra + base:
        if ch in seen:
            continue
        seen.add(ch)
        out.append(ch)
        if len(out) >= LIMIT:
            break
    return "".join(out)


def main() -> int:
    charset = limited_charset()
    print("charset for system fonts:", len(charset))
    manifest = json.loads(BB.MANIFEST.read_text(encoding="utf-8"))
    total = 0
    for key, src, fmt, note in BB.SYSTEM_SOURCES:
        if not src.exists():
            print("missing source:", src)
            continue
        data, used, wanted = BB.build(src, charset, fmt)
        out = BB.OUT_DIR / (key + ".bin")
        out.write_bytes(data)
        total += len(data)
        manifest["fonts"][key] = {
            "file": out.name,
            "format": "zlib",
            "bytes": len(data),
            "chars": used,
            "source": src.name,
            "note": note + " (not redistributed)",
            "charset": "signature-%d" % len(charset),
        }
        print("%-12s %8.2f MB  %d chars" % (key, len(data) / 1e6, used))
    BB.MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("system bundles total: %.2f MB" % (total / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
