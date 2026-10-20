# -*- coding: utf-8 -*-
"""PyInstaller bootstrap for the portable single-file exe.

This file is intentionally *outside* the package so PyInstaller can use it as
the ``--onefile`` entry script without a relative-import problem.
"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> None:
    # When frozen, ``src`` is embedded, so make sure the package is importable
    # even if the loader changed sys.path ordering.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:  # pragma: no cover - frozen only
        sys.path.insert(0, here)


_bootstrap()

from hstool.__main__ import exe_main  # noqa: E402  (after bootstrap)

if __name__ == "__main__":
    exe_main()
