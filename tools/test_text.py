# -*- coding: utf-8 -*-
"""Tests for :mod:`hstool.text` - text normalization and language detection.

Run offline with:
    .venv\\Scripts\\python.exe tools\\run_tests.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hstool import text as T  # noqa: E402


def test_basic_normalize():
    assert T.normalize_text("  hello  ") == "hello"
    assert T.normalize_text("a  b") == "a b"
    assert T.normalize_text("line1\r\nline2\rline3") == "line1\nline2\nline3"
    assert T.normalize_text("\n\n张三\n\n\n") == "张三"


def test_invisible_and_spaces():
    weird = "\u5f20\u200b\u4e09\ufeff\u00a0\u56db\u3000\u4e94\t\u516d"
    assert T.normalize_text(weird) == "\u5f20\u4e09\u56db \u4e94 \u516d"


def test_fullwidth():
    assert T.to_halfwidth("ＡＢＣ１２３！") == "ABC123!"


def test_classify():
    assert T.classify("张") == T.Script.CJK
    assert T.classify("A") == T.Script.LATIN
    assert T.classify("z") == T.Script.LATIN
    assert T.classify("7") == T.Script.DIGIT
    assert T.classify(" ") == T.Script.SPACE
    assert T.classify("，") == T.Script.CJK
    assert T.classify("é") == T.Script.LATIN


def test_analyze_mixed():
    report = T.analyze("张三丰 Wang 2026")
    assert report.total == 13
    assert report.cjk == 3
    assert report.latin == 4
    assert report.digits == 4
    assert "中文" in report.languages
    assert "English" in report.languages


def test_analyze_english_only():
    report = T.analyze("John Smith")
    assert report.languages == ["English"]
    assert report.cjk == 0


def test_analyze_empty():
    report = T.analyze("")
    assert report.total == 0
    assert report.languages == ["符号"]


def test_script_runs():
    assert T.script_runs("张三Li") == [T.Script.CJK, T.Script.LATIN]
    assert T.script_runs("") == []


def test_surrogate_cleanup():
    # lone surrogate should disappear, never raise
    cleaned = T.normalize_text("a\ud800b")
    assert "\ud800" not in cleaned and cleaned.startswith("a") and cleaned.endswith("b")


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print("PASS", name, flush=True)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, exc, flush=True)
    print("failed:", failures, flush=True)
    raise SystemExit(1 if failures else 0)
