# -*- coding: utf-8 -*-
"""Test-suite runner.

Runs every ``test_*.py`` in ``tools/`` (and its ``unittest`` cases) and prints a
summary.  ``--selftest`` produces a JSON report used by the exe build script to
prove the frozen binary can render with every bundled font.

Usage::

    python tools/run_tests.py
    python tools/run_tests.py --selftest [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def run_test_scripts(verbose: bool) -> int:
    import importlib
    import inspect
    import unittest  # noqa: F401  (kept for discovery)

    failures = 0
    total = 0
    for path in sorted((ROOT / "tools").glob("test_*.py")):
        module = importlib.import_module(path.stem)
        for name, func in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(func):
                continue
            total += 1
            try:
                func()
                if verbose:
                    print("PASS", name)
            except Exception:
                failures += 1
                print("FAIL", name)
                traceback.print_exc()
    print("\n%d tests, %d failed" % (total, failures))
    return 0 if failures == 0 else 1


def run_selftest(out_path: Path | None) -> int:
    """Render a sample with every style and every pen, then report."""
    from hstool import __version__
    from hstool.fonts import list_styles
    from hstool.options import RenderOptions
    from hstool.render import render
    from hstool.text import analyze, normalize_text

    report = {"version": __version__, "styles": [], "analysis": [], "ok": True}
    sample_cn = "张三丰"
    sample_en = "Zhang Sanfeng"
    for info in list_styles():
        entry = {
            "key": info.style.key,
            "name": info.style.name,
            "available": info.available,
            "origin": info.style.origin,
            "font_file": str(info.path or ""),
            "renders": {},
        }
        text = sample_cn if info.style.supports_cjk else sample_en
        for pen in ("fountain", "ballpoint", "pencil"):
            try:
                res = render(text, RenderOptions(
                    text=text, font_key=info.style.key, pen_key=pen,
                    color_key="black", width_mm=35, height_mm=14, dpi=300,
                ))
                entry["renders"][pen] = {
                    "ok": True,
                    "size": "%dx%d" % res.image.size,
                    "kb": round(res.size_kb, 1),
                    "missing": res.missing,
                }
                if res.missing:
                    entry["renders"][pen]["ok"] = False
                    report["ok"] = False
            except Exception as exc:
                entry["renders"][pen] = {"ok": False, "error": str(exc)}
                report["ok"] = False
        if not info.available:
            report["ok"] = False
        report["styles"].append(entry)

    for text in ("张三丰", "Zhang Sanfeng", "张三丰 2026年10月20日", "李四 Li Si\n第二行"):
        cleaned = normalize_text(text)
        rep = analyze(cleaned)
        report["analysis"].append({
            "input": text, "normalized": cleaned,
            "summary": rep.summary(), "languages": rep.languages,
        })
    for item in report["analysis"]:
        if not item["languages"]:
            report["ok"] = False

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        print("selftest written to", out_path)
    else:
        print(text)
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true", help="frozen-exe self test")
    parser.add_argument("--out", type=Path, default=None, help="selftest JSON output")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest(args.out)
    return run_test_scripts(args.verbose)


if __name__ == "__main__":
    sys.exit(main())
