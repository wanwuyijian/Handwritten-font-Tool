# -*- coding: utf-8 -*-
"""Handwritten Signature Tool (手写签名字体生成工具).

A portable Windows desktop tool that renders typed text as a handwritten
signature PNG (transparent background) ready to be inserted into Word
documents such as contracts, guarantee letters and other paperwork.
"""

APP_NAME = "手写签名字体生成工具"
APP_NAME_EN = "Handwritten Signature Tool"
APP_ID = "hstool"

# Single source of truth for the version. build.ps1 reads this file.
__version__ = "1.0.4"
VERSION = tuple(int(part) for part in __version__.split("."))

GITHUB_REPO = "https://github.com/wanwuyijian/Handwritten-font-Tool"
