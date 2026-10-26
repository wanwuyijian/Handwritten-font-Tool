# -*- coding: utf-8 -*-
"""Console / script entry point with a small CLI.

Usage
-----
    HandwrittenSignatureTool.exe                     # start the GUI
    HandwrittenSignatureTool.exe --cli "张三" -o a.png
    HandwrittenSignatureTool.exe --cli "Li Si" --font xingkai --pen pencil \
        --color blue --size 35x14 --dpi 600 -o sign.png
    HandwrittenSignatureTool.exe --selftest          # write a self check log
    HandwrittenSignatureTool.exe --version

The GUI is still the main product; the CLI exists for batch use, for the
automated self test after packaging and for troubleshooting on a machine where
the GUI cannot start.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import APP_NAME, APP_NAME_EN, __version__
from .logging_setup import crash_path, get_logger, install_excepthook, log_file, setup
from .options import DPI_DEFAULT, RenderOptions, preset_size
from .render import render

log = get_logger("main")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="HandwrittenSignatureTool",
        description="%s v%s - %s" % (APP_NAME, __version__, APP_NAME_EN),
    )
    parser.add_argument("text", nargs="?", default=None, help="signature text")
    parser.add_argument("-o", "--out", help="output PNG path")
    parser.add_argument("--cli", action="store_true", help="run once without the GUI")
    parser.add_argument("--font", default="kaiti", help="font key, e.g. kaiti/xingkai/wenkai")
    parser.add_argument("--pen", default="fountain", choices=("fountain", "ballpoint", "pencil"))
    parser.add_argument("--color", default="black", choices=("black", "red", "blue"))
    parser.add_argument("--size", default="35x14", help="width x height in mm")
    parser.add_argument("--dpi", type=int, default=DPI_DEFAULT)
    parser.add_argument("--weight", type=float, default=1.0, help="0.0 .. 3.0 (0 = hairline)")
    parser.add_argument("--depth", type=float, default=0.9, help="0.0 .. 1.0")
    parser.add_argument("--blur", type=float, default=0.12, help="0.0 .. 1.0")
    parser.add_argument("--letter-spacing", type=float, default=0.0,
                        help="-0.30 .. 0.80 em gap after every glyph")
    parser.add_argument("--script-gap", type=float, default=0.0,
                        help="0.0 .. 0.80 em gap where CJK meets Latin/digits")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--list-fonts", action="store_true", help="print the font catalogue")
    parser.add_argument("--selftest", action="store_true", help="render every style and report")
    parser.add_argument("--version", action="version", version="%s v%s" % (APP_NAME, __version__))
    return parser


def _parse_size(value: str) -> tuple[float, float]:
    text = value.lower().replace("×", "x").replace("*", "x").replace("mm", "")
    if "x" in text:
        left, right = text.split("x", 1)
        try:
            return float(left), float(right)
        except ValueError:
            pass
    default = preset_size("contract")
    log.warning("invalid --size %r, using %sx%s", value, default[0], default[1])
    return default


def _cli(args) -> int:
    if not args.text:
        print("error: no text given (usage: --cli \"张三\" -o sign.png)", file=sys.stderr)
        return 2
    width, height = _parse_size(args.size)
    opts = RenderOptions(
        text=args.text,
        font_key=args.font,
        pen_key=args.pen,
        color_key=args.color,
        width_mm=width,
        height_mm=height,
        dpi=args.dpi,
        weight=args.weight,
        depth=args.depth,
        blur=args.blur,
        letter_spacing=args.letter_spacing,
        script_gap=args.script_gap,
        seed=args.seed,
    ).clamped()
    result = render(args.text, opts)
    out = Path(args.out) if args.out else Path.cwd() / "signature.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.image_bytes)
    print(result.size_label())
    print("saved: %s" % out)
    if result.missing:
        print("missing glyphs: %s" % "".join(result.missing))
    log.info("cli render %s -> %s (%s)", args.text, out, result.size_label())
    return 0


def _selftest() -> int:
    """Render every available style; returns 0 when all of them worked."""
    from .fonts import list_styles

    failures = []
    for info in list_styles():
        sample = "\u5f20\u4e09\u4e30" if info.style.supports_cjk else "ZhangSanfeng"
        try:
            result = render(sample, RenderOptions(text=sample, font_key=info.style.key, dpi=200))
            ok = result.image.width > 1 and not result.missing
            print("%-14s %-6s %s" % (info.style.key, "OK" if ok else "FAIL", result.size_label()))
            if not ok:
                failures.append(info.style.key)
        except Exception as exc:  # noqa: BLE001
            print("%-14s ERROR %s" % (info.style.key, exc))
            failures.append(info.style.key)
    if failures:
        print("failed styles: %s" % ", ".join(failures))
        return 1
    print("all styles rendered")
    return 0


def _list_fonts() -> int:
    from .fonts import list_styles

    for index, info in enumerate(list_styles(), 1):
        print("%2d. %-14s %-16s %s%s" % (
            index, info.style.key, info.style.name,
            "available" if info.available else "NOT FOUND",
            "" if info.style.supports_cjk else " (latin only)",
        ))
        if info.path:
            print("    %s" % info.path)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    setup()
    install_excepthook()
    log.info("%s v%s started, argv=%s", APP_NAME_EN, __version__, argv)

    if args.list_fonts:
        return _list_fonts()
    if args.selftest:
        return _selftest()
    if args.cli or (args.text and args.out):
        return _cli(args)

    from .gui import run as run_gui

    return run_gui()


def script_main() -> int:
    """Entry point used by the exe: turn crashes into a log entry, never a dialog storm."""
    try:
        return main()
    except SystemExit as exc:  # argparse --version / --help
        return int(exc.code or 0)
    except Exception:  # noqa: BLE001
        text = traceback.format_exc()
        try:
            from .logging_setup import record_crash

            record_crash("main", text)
        except Exception:
            pass
        sys.stderr.write(text)
        return 1


def exe_main() -> None:
    """PyInstaller entry point (no console): log everything, then exit."""
    code = script_main()
    if code:
        log.error("exiting with code %d (logs: %s, crashes: %s)", code, log_file(), crash_path())
    raise SystemExit(code)


if __name__ == "__main__":  # pragma: no cover - allows "python -m hstool"
    raise SystemExit(script_main())
