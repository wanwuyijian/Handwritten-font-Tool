# -*- coding: utf-8 -*-
"""Rotating file logging for operation and crash logs.

Two log files live next to the exe (``logs`` folder):

* ``hstool.log``        - every user action, render, save and error
* ``crash.log``         - python tracebacks from uncaught exceptions (crash
                          reporter) and from the GUI exception hook

The log files rotate at 2 MB and keep 3 old copies, so a portable folder never
grows without bound.  When the exe folder is read-only (for example running
from a CD or a locked network share) the logger falls back to
``%LOCALAPPDATA%\\HandwrittenSignatureTool\\logs``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

from .paths import app_dir, ensure_dir

LOG_DIR_NAME = "logs"
LOG_FILE = "hstool.log"
CRASH_FILE = "crash.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3

_configured = False
_log_dir: Path | None = None
_lock = threading.Lock()


def candidate_dirs() -> list[Path]:
    dirs = [app_dir() / LOG_DIR_NAME]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "HandwrittenSignatureTool" / LOG_DIR_NAME)
    dirs.append(Path.home() / ".hstool" / LOG_DIR_NAME)
    return dirs


def log_dir() -> Path:
    """Directory used for the log files (created on first use)."""
    global _log_dir
    if _log_dir is not None:
        return _log_dir
    for folder in candidate_dirs():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        _log_dir = folder
        return folder
    _log_dir = Path(os.environ.get("TEMP", ".")) / "hstool_logs"
    ensure_dir(_log_dir)
    return _log_dir


def _rotating(path: Path, level: int, fmt: str) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        str(path), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))
    return handler


def setup(verbose_console: bool = False) -> logging.Logger:
    """Configure the ``hstool`` logger tree. Safe to call more than once."""
    global _configured
    with _lock:
        if _configured:
            return logging.getLogger("hstool")
        folder = log_dir()
        fmt = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
        root = logging.getLogger("hstool")
        root.setLevel(logging.DEBUG)
        root.propagate = False
        try:
            root.addHandler(_rotating(folder / LOG_FILE, logging.DEBUG, fmt))
        except OSError:
            pass
        crash_logger = logging.getLogger("hstool.crash")
        try:
            crash_logger.addHandler(logging.handlers.RotatingFileHandler(
                str(folder / CRASH_FILE), maxBytes=MAX_BYTES,
                backupCount=BACKUPS, encoding="utf-8",
            ))
        except OSError:
            pass
        stream_logger = logging.getLogger("hstool")
        if verbose_console or not getattr(sys, "frozen", False):
            console = logging.StreamHandler(sys.stderr)
            console.setLevel(logging.INFO)
            console.setFormatter(logging.Formatter(fmt))
            stream_logger.addHandler(console)
        _configured = True
        root.info("=" * 70)
        root.info("log directory: %s", folder)
        return root


def get_logger(name: str = "") -> logging.Logger:
    """Return a child of the ``hstool`` logger (configures on first use)."""
    setup()
    return logging.getLogger("hstool" + ("." + name if name else ""))


def crash_path() -> Path:
    return log_dir() / CRASH_FILE


def log_file() -> Path:
    return log_dir() / LOG_FILE


def record_crash(context: str, exc_text: str) -> None:
    """Append a crash report to crash.log (used by the crash reporter)."""
    logger = logging.getLogger("hstool.crash")
    setup()
    logger.error("---- %s ----\n%s", context, exc_text)


def install_excepthook() -> None:
    """Make uncaught exceptions land in crash.log instead of vanishing."""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            import traceback

            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            record_crash("uncaught exception", text)
        finally:
            previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
