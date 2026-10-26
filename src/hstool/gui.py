# -*- coding: utf-8 -*-
"""tkinter desktop window for the handwriting signature generator.

Layout (left -> right):

    +-------------------+---------------------------+
    | control panel     | live preview on a paper   |
    | (scrollable)      | styled canvas             |
    +-------------------+---------------------------+

Every control writes straight into :class:`~hstool.settings.Settings`; a
short debounce timer turns the accumulated changes into one background render
so dragging a slider never blocks the UI.

All user visible strings are Chinese, the UI is DPI aware on Windows and the
window is a normal resizable window with minimise / maximise / close.
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from . import APP_NAME, GITHUB_REPO, __version__
from .fonts import list_styles
from .ink import COLOR_LABELS, COLOR_ORDER, PEN_LABELS, PEN_ORDER
from .logging_setup import crash_path, get_logger, log_file, record_crash
from .options import (
    COLOR_MODE_CHOICES,
    DPI_CHOICES,
    SizePresetError,
    RenderOptions,
    SLANT_MAX,
    SIZE_PRESETS,
    preset_size,
)
from .paths import app_dir, ensure_dir
from .render import RenderResult
from .settings import Settings, load as load_settings, save as save_settings
from .text import analyze
from .worker import RenderWorker

log = get_logger("gui")

PREVIEW_MAX_MM = 62.0        # preview never wider than this even for big sizes
CONTROLS_W = 651             # min width of the whole option panel (px)
COLUMN_W_A = 300             # left option column min width (px)
COLUMN_W_B = 336             # right option column min width (px)
SLIDER_WIDTH = 175           # slider length; the panel widens to fit
DEFAULT_GEOMETRY = "1180x760"  # roomy default; shrunk to fit small screens
RENDER_DEBOUNCE_MS = 260


def _enable_dpi_awareness() -> None:
    """Ask Windows for crisp text on high DPI displays (best effort).

    Must run *before* the first window is created: Windows refuses to change
    the DPI awareness of a process that already owns a window.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        shcore = getattr(ctypes.windll, "shcore", None)
        if shcore is not None:
            if shcore.SetProcessDpiAwareness(2) == 0:  # per-monitor v2
                return
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class HandwritingApp(tk.Tk):
    """The main application window."""

    def __init__(self, settings: Settings | None = None) -> None:
        _enable_dpi_awareness()
        super().__init__()
        self.settings = settings or load_settings()
        self.worker = RenderWorker()
        self._render_job: str | None = None
        self._pending_token = 0
        self._current_result: RenderResult | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._styles = []
        self._style_keys: list[str] = []
        self._closing = False
        self._last_render_at = 0.0
        self._tk_ready = True

        self.title("%s v%s" % (APP_NAME, __version__))
        self.minsize(900, 560)
        self._tk_ready_guard()
        self._build_style()
        self._build_menu()
        self._build_layout()
        self._apply_settings()
        self._bind_events()

        self.worker.start()
        self.after(120, self._poll_results)
        self.after(80, lambda: self._schedule_render(immediate=True))
        log.info("gui started, version %s, python %s", __version__, sys.version.split()[0])

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        base = ("Microsoft YaHei UI", 9)
        style.configure(".", font=base)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Hint.TLabel", foreground="#666666", font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Good.TLabel", foreground="#1a7f37", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Warn.TLabel", foreground="#b35900", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="保存为 PNG…", accelerator="Ctrl+S", command=self.on_save)
        file_menu.add_command(label="复制到剪贴板", accelerator="Ctrl+C", command=self.on_copy)
        file_menu.add_separator()
        file_menu.add_command(label="打开日志文件夹", command=self.on_open_logs)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="放大预览", command=lambda: self._zoom_preview(1.25))
        view_menu.add_command(label="缩小预览", command=lambda: self._zoom_preview(0.8))
        view_menu.add_command(label="新建签名窗口", command=self.on_new_window)
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Word 插入方法", command=self.on_help_word)
        help_menu.add_command(label="添加自定义字体", command=self.on_help_fonts)
        help_menu.add_command(label="关于", command=self.on_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=(10, 8, 10, 6))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="手写签名字体生成工具", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="  输入姓名 → 调出满意的手写签名 → 保存透明 PNG → 插入 Word",
            style="Hint.TLabel",
        ).pack(side="left", pady=(6, 0))
        ttk.Label(header, text="v" + __version__, style="Hint.TLabel").pack(side="right", pady=(6, 0))

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left_holder = ttk.Frame(panes)
        right_holder = ttk.Frame(panes)
        panes.add(left_holder, weight=0)
        panes.add(right_holder, weight=1)

        self._build_controls(left_holder)
        self._build_preview(right_holder)
        self._build_status(outer)

    def _build_controls(self, parent: ttk.Frame) -> None:
        """Two fixed-width columns of option boxes inside a scrollable area."""
        canvas = tk.Canvas(parent, width=CONTROLS_W + 20, highlightthickness=0,
                           borderwidth=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas, padding=(2, 2, 8, 2))
        window = canvas.create_window((8, 8), window=body, anchor="nw")

        def _gutter() -> int:
            return 46 if body.winfo_reqwidth() > canvas.winfo_width() else 0

        def _sync(_event=None) -> None:
            # grew: inset by the scrollbar width; shrank: give the space back
            width = max(1, canvas.winfo_width() - _gutter())
            canvas.itemconfigure(window, width=width)
            body.configure(padding=(0, 0, _gutter(), 0))
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _refresh(_event=None) -> None:
            # a child changed size -> the scroll region must follow it
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", _sync)
        _sync()
        canvas.after_idle(_refresh)
        canvas.after(120, _refresh)

        def _wheel(event):
            delta = -1 if getattr(event, "delta", 0) > 0 else 1
            canvas.yview_scroll(delta * 3, "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self._scroll_canvas = canvas

        # Two fixed-width pack columns: each column keeps its own natural
        # width, so one tall box never stretches the boxes next to it.
        self._cols = []
        for width in (COLUMN_W_A, COLUMN_W_B):
            col = ttk.Frame(body)
            col.columnconfigure(0, minsize=width)
            col.pack(side="left", fill="y", anchor="n", padx=(0, 10))
            self._cols.append(col)
        left, right = self._cols

        self._build_text_box(left)
        self._build_font_box(left)
        self._build_pen_box(left)
        self._build_size_box(right)
        self._build_tune_box(right)
        self._build_action_box(right)


    def _build_text_box(self, parent: ttk.Frame, column: int = 0, row: int = 0) -> None:
        box = ttk.LabelFrame(parent, text=" 1. 输入签名文字 ", padding=8)
        self._box_text = box
        box.pack(fill="x", anchor="n", pady=(0, 8))

        self.txt = tk.Text(box, height=3, width=1, wrap="word", font=("Microsoft YaHei UI", 11),
                           undo=True, relief="solid", borderwidth=1, padx=6, pady=4)
        self.txt.pack(fill="x")
        self.txt.insert("1.0", "张三丰")

        hint = ttk.Frame(box)
        hint.pack(fill="x", pady=(4, 0))
        ttk.Label(
            hint,
            text="中英自动识别 · Ctrl+Enter 换行",
            style="Hint.TLabel",
        ).pack(side="left")
        self.lang_label = ttk.Label(hint, text="", style="Status.TLabel")
        self.lang_label.pack(side="right")

        self.txt.bind("<KeyRelease>", lambda e: self._on_text_changed())
        self.txt.bind("<<Paste>>", lambda e: self.after(30, self._on_text_changed))
        self.txt.bind("<Tab>", self._insert_newline)
        self.txt.bind("<Control-Return>", self._insert_newline)

    def _insert_newline(self, _event=None):
        self.txt.insert("insert", "\n")
        self._on_text_changed()
        return "break"

    def _build_font_box(self, parent: ttk.Frame, column: int = 0, row: int = 0) -> None:
        box = ttk.LabelFrame(parent, text=" 2. 手写字体（10 种） ", padding=8)
        self._box_font = box
        box.pack(fill="x", anchor="n", pady=(0, 8))

        self._styles = list_styles()
        labels = []
        for info in self._styles:
            name = info.style.name
            if not info.available:
                name = name + "（本机未安装）"
            elif info.style.origin == "custom":
                name = name
            labels.append(name)
            self._style_keys.append(info.style.key)

        self.font_var = tk.StringVar()
        self.font_combo = ttk.Combobox(
            box, textvariable=self.font_var, values=labels, state="readonly",
            font=("Microsoft YaHei UI", 10),
        )
        self.font_combo.pack(fill="x")
        self.font_combo.bind("<<ComboboxSelected>>", lambda e: self._on_change("font"))

        self.font_desc = ttk.Label(box, text="", style="Hint.TLabel", wraplength=COLUMN_W_A - 40)
        self.font_desc.pack(fill="x", pady=(4, 4))

        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="刷新字体列表", command=self.refresh_fonts).pack(side="left")
        ttk.Button(row, text="打开字体文件夹", command=self.on_open_font_dir).pack(side="left", padx=6)
        ttk.Label(box, text="把 .ttf 字体放进该文件夹，即可作为新字体使用（例如自备毛泽东体）",
                  style="Hint.TLabel", wraplength=COLUMN_W_A - 40).pack(fill="x", pady=(4, 0))

    def _build_pen_box(self, parent: ttk.Frame, column: int = 0, row: int = 0) -> None:
        box = ttk.LabelFrame(parent, text=" 3. 笔型与墨色 ", padding=8)
        self._box_pen = box
        box.pack(fill="x", anchor="n", pady=(0, 8))

        pen_row = ttk.Frame(box)
        pen_row.pack(fill="x")
        ttk.Label(pen_row, text="笔型：").pack(side="left")
        self.pen_var = tk.StringVar()
        for key in PEN_ORDER:
            ttk.Radiobutton(
                pen_row, text=PEN_LABELS.get(key, key), value=key, padding=(0, 2),
                variable=self.pen_var, command=lambda: self._on_change("pen"),
            ).pack(side="left", padx=(0, 8))

        color_row = ttk.Frame(box)
        color_row.pack(fill="x", pady=(6, 0))
        ttk.Label(color_row, text="墨色：").pack(side="left")
        self.color_var = tk.StringVar()
        for key in COLOR_ORDER:
            swatch = tk.Frame(color_row, width=14, height=14, relief="solid", borderwidth=1,
                              background=COLOR_LABELS.get(key, ("", "#000000"))[1])
            swatch.pack(side="left", padx=(2, 2), pady=2)
            ttk.Radiobutton(
                color_row, text=COLOR_LABELS.get(key, (key, ""))[0], value=key, padding=(0, 2),
                variable=self.color_var, command=lambda: self._on_change("color"),
            ).pack(side="left", padx=(0, 14))

    def _slider(self, parent, label, var, lo, hi, command, hint=""):
        """One labelled slider row: name + live value, then the scale."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(3, 0))
        top = ttk.Frame(row)
        top.pack(fill="x")
        ttk.Label(top, text=label).pack(side="left")
        value_label = ttk.Label(top, text=str(var.get()), style="Hint.TLabel", width=4,
                                anchor="e")
        value_label.pack(side="right")
        if hint:
            ttk.Label(top, text=hint, style="Hint.TLabel").pack(side="right", padx=(0, 6))

        scale = ttk.Scale(
            row, from_=lo, to=hi, orient="horizontal", variable=var,
            length=SLIDER_WIDTH,
            command=lambda v, lbl=value_label: (lbl.config(text="%d" % round(float(v))),
                                                command()),
        )
        scale.pack(fill="x")
        return scale

    def _build_tune_box(self, parent: ttk.Frame, row: int = 0, column: int = 0) -> None:
        box = ttk.LabelFrame(parent, text=" 4. 字迹微调 ", padding=8)
        self._box_tune = box
        box.pack(fill="x", anchor="n", pady=(0, 8))

        self.weight_var = tk.IntVar(value=50)
        self.depth_var = tk.IntVar(value=85)
        self.blur_var = tk.IntVar(value=15)
        self.jitter_var = tk.IntVar(value=35)
        self.slant_var = tk.IntVar(value=50)
        self.opacity_var = tk.IntVar(value=100)
        self.letter_spacing_var = tk.IntVar(value=30)
        self.script_gap_var = tk.IntVar(value=25)

        # two columns x four rows keeps the panel compact on 768 px screens
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for idx in (0, 1):
            grid.columnconfigure(idx, weight=1, uniform="tune")
        left = ttk.Frame(grid)
        left.grid(row=0, column=0, sticky="new", padx=(0, 10))
        right = ttk.Frame(grid)
        right.grid(row=0, column=1, sticky="new")

        self._slider(left, "粗细度", self.weight_var, 0, 100,
                     lambda: self._on_change("weight"))
        self._slider(right, "颜色深度", self.depth_var, 0, 100,
                     lambda: self._on_change("depth"))
        self._slider(left, "字间间距", self.letter_spacing_var, 0, 100,
                     lambda: self._on_change("letter_spacing"))
        self._slider(right, "中英间距", self.script_gap_var, 0, 100,
                     lambda: self._on_change("script_gap"))
        self._slider(left, "模糊度", self.blur_var, 0, 100,
                     lambda: self._on_change("blur"))
        self._slider(right, "笔画抖动", self.jitter_var, 0, 100,
                     lambda: self._on_change("jitter"))
        self._slider(left, "字体倾斜度", self.slant_var, 0, 100,
                     lambda: self._on_change("slant"))
        self._slider(right, "整体透明度", self.opacity_var, 30, 100,
                     lambda: self._on_change("opacity"))

    def _build_size_box(self, parent: ttk.Frame, column: int = 0, row: int = 0) -> None:
        box = ttk.LabelFrame(parent, text=" 5. 尺寸与清晰度 ", padding=8)
        self._box_size = box
        box.pack(fill="x", anchor="n", pady=(0, 8))

        self.preset_var = tk.StringVar(master=self)
        self.preset_var.set(SIZE_PRESETS[1][1])
        self.preset_combo = ttk.Combobox(
            box, state="readonly", width=18, values=[label for _k, label, _s in SIZE_PRESETS],
        )
        self.preset_combo.set(self.preset_var.get())
        self.preset_combo.pack(fill="x")
        self.preset_combo.bind("<<ComboboxSelected>>", lambda e: self._on_preset())

        mm_row = ttk.Frame(box)
        mm_row.pack(fill="x", pady=(6, 0))
        ttk.Label(mm_row, text="宽").pack(side="left")
        self.width_var = tk.StringVar(master=self, value="%.1f" % SIZE_PRESETS[1][2][0])
        self.width_entry = ttk.Entry(mm_row, width=5)
        self.width_entry.insert(0, self.width_var.get())
        self.width_entry.pack(side="left", padx=(3, 1))
        ttk.Label(mm_row, text="mm").pack(side="left")
        ttk.Label(mm_row, text="高").pack(side="left", padx=(8, 0))
        self.height_var = tk.StringVar(master=self, value="%.1f" % SIZE_PRESETS[1][2][1])
        self.height_entry = ttk.Entry(mm_row, width=5)
        self.height_entry.insert(0, self.height_var.get())
        self.height_entry.pack(side="left", padx=(3, 1))
        ttk.Label(mm_row, text="mm").pack(side="left")
        for entry in (self.width_entry, self.height_entry):
            entry.bind("<Return>", lambda e: self._on_size_entry())
            entry.bind("<FocusOut>", lambda e: self._on_size_entry())

        dpi_row = ttk.Frame(box)
        dpi_row.pack(fill="x", pady=(6, 0))
        ttk.Label(dpi_row, text="清晰度").pack(side="left")
        self.dpi_var = tk.StringVar(master=self, value="600")
        self.dpi_combo = ttk.Combobox(
            dpi_row, state="readonly", width=7, values=[str(d) for d in DPI_CHOICES],
        )
        self.dpi_combo.set(self.dpi_var.get())
        self.dpi_combo.pack(side="left", padx=(4, 6))
        self.dpi_combo.bind("<<ComboboxSelected>>", lambda e: self._on_dpi())
        ttk.Label(dpi_row, text="DPI", style="Hint.TLabel").pack(side="left")

        self.transparent_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="透明背景（插入 Word 推荐）", variable=self.transparent_var,
                        command=lambda: self._on_change("transparent")).pack(fill="x", pady=(6, 0))
        self.autosize_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="自动裁边（图片只保留字迹）", variable=self.autosize_var,
                        command=lambda: self._on_change("autosize")).pack(fill="x")

        self.size_hint = ttk.Label(box, text="", style="Hint.TLabel", wraplength=COLUMN_W_B - 40)
        self.size_hint.pack(fill="x", pady=(6, 0))

    def _build_action_box(self, parent: ttk.Frame, column: int = 0, row: int = 0) -> None:
        box = ttk.Frame(parent)
        self._box_action = box
        box.pack(fill="x", anchor="n", pady=(0, 10))
        self.save_button = ttk.Button(box, text="保存 PNG 图片…", style="Accent.TButton",
                                      command=self.on_save)
        self.save_button.pack(fill="x")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="换个写法", command=self.on_regenerate).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="复制到剪贴板", command=self.on_copy).pack(
            side="left", fill="x", expand=True, padx=(4, 0))

    def _build_preview(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 预览（模拟纸张效果） ", padding=8)
        box.pack(fill="both", expand=True)

        self.preview = tk.Canvas(box, background="#f4f4f2", highlightthickness=0, width=560, height=380)
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", lambda e: self._draw_preview())

        info = ttk.Frame(box)
        info.pack(fill="x", pady=(6, 0))
        self.info_label = ttk.Label(info, text="准备就绪", style="Status.TLabel")
        self.info_label.pack(side="left")
        self.size_label = ttk.Label(info, text="", style="Hint.TLabel")
        self.size_label.pack(side="right")

    def _build_status(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(6, 0))
        self.status_label = ttk.Label(bar, text="就绪", style="Status.TLabel")
        self.status_label.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.progress.pack(side="right")

    # ------------------------------------------------------------------
    # settings <-> widgets
    # ------------------------------------------------------------------
    def _apply_settings(self) -> None:
        s = self.settings
        self.pen_var.set(s.pen_key if s.pen_key in PEN_ORDER else "fountain")
        self.color_var.set(s.color_key if s.color_key in COLOR_ORDER else "black")
        self.weight_var.set(int(s.weight))
        self.depth_var.set(int(s.depth))
        self.blur_var.set(int(s.blur))
        self.opacity_var.set(int(s.opacity))
        self.jitter_var.set(int(s.jitter))
        self.slant_var.set(int(getattr(s, "slant", 50)))
        self.letter_spacing_var.set(int(getattr(s, "letter_spacing", 30)))
        self.script_gap_var.set(int(getattr(s, "script_gap", 25)))
        self.dpi_var.set(str(s.dpi if s.dpi in DPI_CHOICES else 600))
        self.dpi_combo.set(self.dpi_var.get())
        self.transparent_var.set(bool(s.transparent))
        self.autosize_var.set(bool(s.auto_size))
        self.width_var.set("%.1f" % float(s.width_mm))
        self.height_var.set("%.1f" % float(s.height_mm))
        self._show_size_widgets()

        preset_label = SIZE_PRESETS[1][1]
        for key, label, (w, h) in SIZE_PRESETS:
            if abs(w - float(s.width_mm)) < 0.05 and abs(h - float(s.height_mm)) < 0.05:
                preset_label = label
                break
        self.preset_var.set(preset_label)
        self.preset_combo.set(preset_label)
        self._apply_font_key(s.font_key)

        if s.window_geometry:
            try:
                self.geometry(s.window_geometry)
            except tk.TclError:
                self.geometry(DEFAULT_GEOMETRY)
        else:
            self.geometry(DEFAULT_GEOMETRY)
        self._update_size_hint()
        self._on_text_changed(render=False)
        self.after(200, self._bring_to_front)
        self.after(400, self._fit_window_to_screen)

    def _fit_window_to_screen(self) -> None:
        """Clamp the window into the desktop work area of small screens."""
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            try:  # leave the taskbar alone when the shell reports it
                from ctypes import wintypes as _wt
                rect = _wt.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(
                        0x0030, 0, ctypes.byref(rect), 0) and rect.bottom > rect.top:
                    sh = rect.bottom
                    sw = max(sw, rect.right)
            except Exception:
                pass
            w, h = self.winfo_width(), self.winfo_height()
            max_w, max_h = int(sw * 0.96), int(sh * 0.92)
            w, h = min(w, max_w), min(h, max_h)
            if w != self.winfo_width() or h != self.winfo_height():
                x = max(0, (sw - w) // 2)
                y = max(0, (sh - h) // 3)
                self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except tk.TclError:
            pass

    def _bring_to_front(self) -> None:
        """Make sure the window is really on screen when it gets a startup."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            if self.state() == "iconic":
                self.state("normal")
        except tk.TclError:
            pass

    def _apply_font_key(self, key: str) -> None:
        if key in self._style_keys:
            index = self._style_keys.index(key)
        else:
            available = [i for i, info in enumerate(self._styles) if info.available]
            index = available[0] if available else 0
        self.font_combo.current(index)
        info = self._styles[index]
        desc = info.style.description
        if not info.available:
            desc = "本机未安装该字体，请先安装，或把字体文件放进字体文件夹。"
        elif not info.style.supports_cjk:
            desc += "（仅支持英文/数字，中文会自动换用楷体）"
        self.font_desc.config(text=desc)

    def _current_font_key(self) -> str:
        index = self.font_combo.current()
        if 0 <= index < len(self._style_keys):
            return self._style_keys[index]
        return self.settings.font_key

    def _on_preset(self) -> None:
        index = self.preset_combo.current()
        if index < 0 and self.preset_var.get() in [lab for _k, lab, _s in SIZE_PRESETS]:
            index = [lab for _k, lab, _s in SIZE_PRESETS].index(self.preset_var.get())
        if 0 <= index < len(SIZE_PRESETS):
            _key, _label, (w, h) = SIZE_PRESETS[index]
            self.width_var.set("%.1f" % w)
            self.height_var.set("%.1f" % h)
            self._show_size_widgets()
            self.settings.size_preset = _key
            self._on_size_entry()

    def _on_size_entry(self) -> None:
        try:
            w = float(self.width_entry.get().replace("，", ".").replace(",", "."))
            h = float(self.height_entry.get().replace("，", ".").replace(",", "."))
        except ValueError:
            return
        self.width_var.set("%.1f" % w)
        self.height_var.set("%.1f" % h)
        w = max(5.0, min(250.0, w))
        h = max(4.0, min(120.0, h))
        self.settings.width_mm = w
        self.settings.height_mm = h
        self._update_size_hint()
        self._schedule_render()

    def _on_dpi(self) -> None:
        try:
            value = int(self.dpi_combo.get())
        except (tk.TclError, ValueError):
            return
        self.dpi_var.set(str(value))
        self._on_change("dpi")

    def _update_size_hint(self) -> None:
        try:
            opts = self.render_options()
        except SizePresetError:
            return
        self.size_hint.config(
            text="输出：%d × %d 像素（%.1f × %.1f mm），文件将在保存后显示实际大小"
            % (opts.width_px, opts.height_px, opts.width_mm, opts.height_mm)
        )

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _bind_events(self) -> None:
        self.bind_all("<Control-s>", lambda e: self.on_save())
        self.bind_all("<Control-S>", lambda e: self.on_save())
        self.bind_all("<Control-c>", self._maybe_copy)
        self.bind_all("<Control-C>", self._maybe_copy)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.report_callback_exception = self._tk_exception

    def _maybe_copy(self, _event=None):
        widget = self.focus_get()
        if isinstance(widget, tk.Text):
            return None  # let the text widget copy normally
        if isinstance(widget, (ttk.Entry, tk.Entry)):
            return None
        self.on_copy()
        return "break"

    def _tk_ready_guard(self) -> None:
        """Tk 9 builds that skip init scripts leave ::tk:: vars undefined.

        A readonly ttk.Combobox may then pop its list window the first time a
        value is set and destroy the Tcl variables it was bound to.  Variables
        are therefore only created once the interpreter is fully initialised.
        """
        try:
            ok = self.tk.call("info", "exists", "::tk::ScreenChanged") == "1"
        except tk.TclError:
            ok = False
        if not ok:
            try:
                self.tk.call("package", "require", "Tk")
                ok = self.tk.call("info", "exists", "::tk::ScreenChanged") == "1"
            except tk.TclError:
                ok = False
        self._tk_ready = bool(ok)
        if not self._tk_ready:
            log.warning("Tk initialisation scripts missing; using widget text fallbacks")

    def _sync_size_widgets(self) -> None:
        """Re-show width / height / preset after an external size change."""
        try:
            self.width_entry.configure(textvariable=self.width_var)
            self.height_entry.configure(textvariable=self.height_var)
            self.preset_combo.configure(textvariable=self.preset_var)
        except tk.TclError:
            return
        self.after_idle(self._show_size_widgets)

    def _show_size_widgets(self) -> None:
        # widgets kept their text when the variable was re-bound: push it again
        try:
            self.width_entry.delete(0, "end")
            self.width_entry.insert(0, self.width_var.get())
            self.height_entry.delete(0, "end")
            self.height_entry.insert(0, self.height_var.get())
            if self.preset_combo.get() != self.preset_var.get():
                self.preset_combo.set(self.preset_var.get())
        except (tk.TclError, ValueError):
            pass

    def _on_text_changed(self, render: bool = True) -> None:
        text = self.txt.get("1.0", "end").strip("\n")
        report = analyze(text)
        self.lang_label.config(text=report.summary() if text.strip() else "等待输入")
        if render:
            self._schedule_render()

    def _on_change(self, what: str) -> None:
        if what == "font":
            self._apply_font_key(self._current_font_key())
        log.debug("setting changed: %s", what)
        self._schedule_render()

    def _zoom_preview(self, factor: float) -> None:
        self._preview_scale = max(0.2, min(4.0, getattr(self, "_preview_scale", 1.0) * factor))
        self._draw_preview()

    def render_options(self) -> RenderOptions:
        s = self.settings
        deep = getattr(s, "slider_depth", None)
        return RenderOptions(
            text=self.txt.get("1.0", "end"),
            font_key=self._current_font_key(),
            width_mm=float(self.width_entry.get() or self.width_var.get() or 45),
            height_mm=float(self.height_entry.get() or self.height_var.get() or 15),
            dpi=int(self.dpi_var.get() or 600),
            pen_key=self.pen_var.get() or "fountain",
            color_key=self.color_var.get() or "black",
            weight=(self.weight_var.get() / 100.0) * 3.0,
            depth=self.depth_var.get() / 100.0,
            blur=self.blur_var.get() / 100.0,
            opacity=0.3 + (self.opacity_var.get() / 100.0) * 0.7,
            jitter=self.jitter_var.get() / 100.0,
            slant=(self.slant_var.get() - 50) / 50.0 * SLANT_MAX,
            letter_spacing=-0.30 + (self.letter_spacing_var.get() / 100.0) * 1.10,
            script_gap=(self.script_gap_var.get() / 100.0) * 0.80,
            color_mode="RGBA" if self.transparent_var.get() else "RGB",
            auto_size=bool(self.autosize_var.get()),
            max_file_kb=int(getattr(s, "max_file_kb", 0) or 0),
        ).clamped()

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _schedule_render(self, immediate: bool = False) -> None:
        if self._closing:
            return
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except tk.TclError:
                pass
        delay = 10 if immediate else RENDER_DEBOUNCE_MS
        self._render_job = self.after(delay, self._start_render)

    def _start_render(self, seed: int | None = None) -> None:
        self._render_job = None
        if self._closing:
            return
        try:
            opts = self.render_options()
        except (ValueError, SizePresetError) as exc:
            self._set_status("尺寸或参数无效：%s" % exc, warn=True)
            return
        if seed is not None:
            from dataclasses import replace

            opts = replace(opts, seed=seed)
        text = self.txt.get("1.0", "end")
        if not text.strip():
            self._current_result = None
            self._clear_preview()
            self._set_status("请先输入签名文字", warn=True)
            return
        self._pending_token = self.worker.submit(text, opts)
        self.progress.start(12)
        self._set_status("正在生成…")

    def on_regenerate(self) -> None:
        self._start_render(seed=(int(time.time() * 1000) % 2_000_000_000) or 1)
        log.info("regenerate requested")

    def _poll_results(self) -> None:
        if self._closing:
            return
        try:
            while True:
                outcome = self.worker.results.get_nowait()
                if outcome.token != self._pending_token:
                    continue  # superseded by a newer render
                self.progress.stop()
                self._last_render_at = outcome.elapsed
                if outcome.error or outcome.result is None:
                    self._set_status("生成失败：%s（详见日志）" % outcome.error, warn=True)
                    continue
                self._current_result = outcome.result
                try:
                    self._draw_preview()
                except Exception:
                    log.warning("preview draw failed, retrying once", exc_info=True)
                    self._preview_photo = None
                    self._draw_preview()
                self._report_result(outcome.result, outcome.elapsed)
        except Exception:
            pass
        self.after(60, self._poll_results)

    def _report_result(self, result: RenderResult, elapsed: float) -> None:
        self.size_label.config(
            text="%d × %d px · %.1f × %.1f mm · %.0f KB · %.2f s"
            % (result.image.width, result.image.height, result.width_mm,
               result.height_mm, result.size_kb, elapsed)
        )
        if result.missing:
            chars = "、".join(result.missing[:6])
            self._set_status("有 %d 个字符缺少字形：%s" % (len(result.missing), chars), warn=True)
        else:
            self._set_status("生成完成")

    # ------------------------------------------------------------------
    # preview canvas
    # ------------------------------------------------------------------
    def _clear_preview(self) -> None:
        self.preview.delete("all")
        self._preview_photo = None

    def _draw_preview(self) -> None:
        canvas = self.preview
        canvas.delete("all")
        width = max(120, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        if self._current_result is None:
            canvas.create_text(width / 2, height / 2, text="输入文字后在此预览",
                               fill="#999999", font=("Microsoft YaHei UI", 11))
            return

        result = self._current_result
        zoom = getattr(self, "_preview_scale", 1.0)
        # one screen pixel per physical millimetre, scaled to the canvas: the
        # sheet is a real A4/信纸 width, so the signature shows in true proportion
        a4_mm = 210.0
        try:
            preview_dpi = float(self.winfo_fpixels("1i"))
        except Exception:
            preview_dpi = 96.0
        mm_px = min((width - 64) / a4_mm, (height - 96) / (a4_mm * 1.414)) * zoom
        mm_px = max(1.0, min(mm_px, preview_dpi / 3.0, 8.0))
        draw_w = max(1, int(round(result.width_mm * mm_px)))
        draw_h = max(1, int(round(result.height_mm * mm_px)))

        # the sheet is a usable patch of paper around the signature, never
        # taller than the canvas, drawn in real millimetres
        paper_w = min(width - 24, max(draw_w + 150, int(150 * mm_px)))
        paper_h = min(height - 24, max(draw_h + int(28 * mm_px), int(52 * mm_px)))
        px, py = (width - paper_w) // 2, (height - paper_h) // 2
        canvas.create_rectangle(px + 3, py + 4, px + paper_w + 3, py + paper_h + 4,
                                fill="#d8d8d4", outline="")
        canvas.create_rectangle(px, py, px + paper_w, py + paper_h,
                                fill="#ffffff", outline="#cccccc")

        if self.transparent_var.get():
            # checkerboard hint under transparent pixels
            step = 8
            for gx in range(px + 1, px + paper_w - 1, step):
                for gy in range(py + 1, py + paper_h - 1, step):
                    if ((gx - px) // step + (gy - py) // step) % 2 == 0:
                        canvas.create_rectangle(gx, gy, min(gx + step, px + paper_w - 1),
                                                min(gy + step, py + paper_h - 1),
                                                fill="#fbfbfb", outline="")

        # the signature line, so the user sees it sit exactly like in Word
        line_y = py + (paper_h + draw_h) // 2 + int(draw_h * 0.12) + 4
        line_y = min(line_y, py + paper_h - int(10 * mm_px))
        canvas.create_text(px + 16, line_y - 3, text="签字：", anchor="sw",
                           fill="#555555",
                           font=("Microsoft YaHei UI", max(7, int(3.4 * mm_px))))
        canvas.create_line(px + 16, line_y, px + paper_w - 16, line_y, fill="#dcdcdc")

        resized = result.image.resize((draw_w, draw_h), Image.LANCZOS)
        if resized.mode != "RGBA":
            resized = resized.convert("RGBA")
        # re-created on every draw so the old photo can never be referenced
        self._preview_photo = ImageTk.PhotoImage(resized, master=canvas)
        # a real signature starts right of the caption and sits on the line
        sx = px + max(int(16 * mm_px), (paper_w - draw_w) // 2)
        sx = min(sx, px + paper_w - draw_w - 8)
        sy = line_y - draw_h + int(draw_h * 0.10)
        canvas.create_image(sx, sy, image=self._preview_photo, anchor="nw")

        # a small ruler so the user can judge the real size
        canvas.create_text(px + paper_w // 2, py + paper_h - 12,
                           text="签名尺寸约 %.0f × %.0f mm" % (result.width_mm, result.height_mm),
                           fill="#888888",
                           font=("Microsoft YaHei UI", max(7, int(2.6 * mm_px))))

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _default_name(self) -> str:
        text = self.txt.get("1.0", "end").strip()
        first = (text.splitlines()[0] if text else "signature").strip() or "signature"
        safe = "".join(ch for ch in first if ch.isalnum() or ch in "-_ ")[:24].strip()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return "%s-签名-%s.png" % (safe or "signature", stamp)

    def on_save(self) -> None:
        if self._current_result is None:
            messagebox.showinfo("尚未生成", "请先输入签名文字并等待预览完成。", parent=self)
            return
        initial_dir = self.settings.last_save_dir or str(Path.home() / "Pictures")
        try:
            ensure_dir(Path(initial_dir))
        except OSError:
            initial_dir = str(app_dir())
        path = filedialog.asksaveasfilename(
            parent=self, title="保存签名图片",
            initialdir=initial_dir, initialfile=self._default_name(),
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")],
        )
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(self._current_result.image_bytes)
        except OSError as exc:
            log.error("save failed: %s", exc)
            self._show_error("保存失败", "%s\n\n目标文件可能正被其他程序占用。" % exc)
            return
        self.settings.last_save_dir = str(Path(path).parent)
        self._save_settings()
        kb = len(self._current_result.image_bytes) / 1024.0
        self._set_status("已保存：%s（%.0f KB）" % (Path(path).name, kb))
        log.info("saved %s (%.1f KB)", path, kb)

    def on_copy(self) -> None:
        if self._current_result is None:
            self._set_status("请先生成签名", warn=True)
            return
        try:
            self._copy_to_clipboard(self._current_result)
            self._set_status("已复制到剪贴板，可直接在 Word 中粘贴")
            log.info("copied image to clipboard")
        except Exception as exc:  # noqa: BLE001
            log.error("clipboard failed: %s\n%s", exc, traceback.format_exc())
            self._set_status("复制失败：%s" % exc, warn=True)

    def _copy_to_clipboard(self, result: RenderResult) -> None:
        """Put the image on the Windows clipboard (with DPI information)."""
        self.update_idletasks()
        tmp = Path(os.environ.get("TEMP", ".")) / ("hstool-clip-%d.png" % os.getpid())
        try:
            tmp.write_bytes(result.image_bytes)
            if os.name != "nt":
                raise RuntimeError("剪贴板仅支持 Windows")
            import ctypes

            ctypes.windll.user32.OpenClipboard(None)
            try:
                ctypes.windll.user32.EmptyClipboard()
                with open(str(tmp), "rb") as fh:
                    data = fh.read()
                # CF_DIB would lose alpha; CF_PNG (format id) keeps transparency
                fmt = ctypes.windll.user32.RegisterClipboardFormatW("PNG")
                # growable global memory block
                size = len(data)
                GMEM_MOVEABLE = 0x0002
                kernel32 = ctypes.windll.kernel32
                kernel32.GlobalAlloc.restype = ctypes.c_void_p
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if not handle:
                    raise RuntimeError("无法分配剪贴板内存")
                kernel32.GlobalLock.restype = ctypes.c_void_p
                kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
                ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
                ctypes.memmove(ctypes.c_void_p(ptr), data, size)
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
                ctypes.windll.user32.SetClipboardData.restype = ctypes.c_void_p
                ctypes.windll.user32.SetClipboardData(fmt, ctypes.c_void_p(handle))
            finally:
                ctypes.windll.user32.CloseClipboard()
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def on_open_logs(self) -> None:
        folder = log_file().parent
        try:
            os.startfile(str(folder))  # noqa: S606 - opens explorer
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("日志位置", str(folder), parent=self)
            log.warning("open log dir failed: %s", exc)

    def on_open_font_dir(self) -> None:
        from .fonts import custom_font_dirs

        folder = custom_font_dirs()[0]
        ensure_dir(folder)
        try:
            os.startfile(str(folder))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("字体文件夹", str(folder), parent=self)
            log.warning("open font dir failed: %s", exc)

    def refresh_fonts(self) -> None:
        from . import fonts

        fonts.clear_cache()
        key = self._current_font_key()
        self._styles = list_styles()
        self._style_keys = [info.style.key for info in self._styles]
        self.font_combo.config(values=[
            info.style.name if info.available else info.style.name + "（本机未安装）"
            for info in self._styles
        ])
        self._apply_font_key(key)
        self._set_status("字体列表已刷新（共 %d 种）" % len(self._styles))
        self._schedule_render()

    def on_new_window(self) -> None:
        try:
            HandwritingApp().mainloop()
        except Exception as exc:  # noqa: BLE001
            log.error("new window failed: %s", exc)

    def on_help_word(self) -> None:
        messagebox.showinfo(
            "在 Word 中插入签名",
            "1) 本工具「保存 PNG 图片」，得到透明背景的签名图片；\n"
            "2) Word 中把光标放到「签字：」后面；\n"
            "3) 插入 → 图片 → 此设备，选择刚才的 PNG；\n"
            "4) 图片默认按 PNG 内的 DPI 显示为实际毫米尺寸，无需手工缩放；\n"
            "5) 版式「浮于文字上方」可以随意拖动；\n"
            "6) 打印前请确认图片大小与实际签名大小接近（可用「大号/标准/小号」预设）。",
            parent=self,
        )

    def on_help_fonts(self) -> None:
        from .fonts import custom_font_dirs

        folder = custom_font_dirs()[0]
        messagebox.showinfo(
            "使用自定义手写字体",
            "如有自备手写字体（例如毛泽东体、名家手迹字体等）：\n\n"
            "1) 把 .ttf / .otf 文件复制到：\n%s\n\n"
            "2) 点击「刷新字体列表」，新字体会出现在字体下拉框中；\n"
            "3) 自备字体仅本人使用，请遵守字体授权。\n\n"
            "提示：字体文件夹位于程序旁边，随程序一起复制即可带到其他电脑。" % folder,
            parent=self,
        )

    def on_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "%s v%s\n\n"
            "手写签名字体生成工具：输入姓名，生成透明背景 PNG 签名，直接插入 Word 合同、"
            "资料、保证书的签字处。\n\n"
            "日志目录：%s\n\n"
            "内置开源字体：霞鹜文楷（LXGW WenKai）、悠哉字体（Yozai），"
            "均为 SIL Open Font License 1.1；其余字体使用系统已安装字体。\n\n"
            "项目地址：%s"
            % (APP_NAME, __version__, log_file().parent, GITHUB_REPO),
            parent=self,
        )

    # ------------------------------------------------------------------
    # status / errors / shutdown
    # ------------------------------------------------------------------
    def _set_status(self, message: str, warn: bool = False) -> None:
        try:
            self.status_label.config(text=message, style="Warn.TLabel" if warn else "Status.TLabel")
        except tk.TclError:
            pass

    def _show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self)

    def _tk_exception(self, exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("tkinter callback failed:\n%s", text)
        try:
            record_crash("tkinter callback", text)
            self._set_status("出现错误：%s（已记录到日志）" % exc_value, warn=True)
        except Exception:
            pass

    def _save_settings(self) -> None:
        s = self.settings
        try:
            s.width_mm = float(self.width_entry.get())
            s.height_mm = float(self.height_entry.get())
        except ValueError:
            pass
        s.font_key = self._current_font_key()
        s.pen_key = self.pen_var.get() or s.pen_key
        s.color_key = self.color_var.get() or s.color_key
        s.weight = int(self.weight_var.get())
        s.depth = int(self.depth_var.get())
        s.blur = int(self.blur_var.get())
        s.opacity = int(self.opacity_var.get())
        s.jitter = int(self.jitter_var.get())
        s.slant = int(self.slant_var.get())
        s.letter_spacing = int(self.letter_spacing_var.get())
        s.script_gap = int(self.script_gap_var.get())
        s.transparent = bool(self.transparent_var.get())
        s.auto_size = bool(self.autosize_var.get())
        try:
            s.dpi = int(self.dpi_var.get())
        except ValueError:
            pass
        if self.state() == "normal":
            s.window_geometry = self.geometry()
        save_settings(s)

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._save_settings()
        finally:
            log.info("gui closing")
            try:
                self.worker.stop()
            except Exception:
                pass
            self.destroy()


def _hide_console() -> None:
    """Hide the console window the GUI build carries (no black box on startup).

    The exe is built with console=True so that ``--cli`` / ``--selftest`` keep
    printing; the GUI never needs it, and an empty console would linger as a
    black window next to the app.
    """
    try:
        import ctypes

        u = ctypes.windll.user32
        if getattr(u, "GetConsoleWindow", None) is None:
            return
        hwnd = int(u.GetConsoleWindow() or 0)
        if hwnd:
            u.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:  # pragma: no cover - cosmetically only
        pass


def run() -> int:
    """Start the GUI; returns a process exit code."""
    _hide_console()
    from .logging_setup import install_excepthook

    install_excepthook()
    log.info("=" * 70)
    log.info("starting %s v%s", APP_NAME, __version__)
    try:
        app = HandwritingApp()
    except Exception:
        text = traceback.format_exc()
        log.error("startup failed:\n%s", text)
        record_crash("startup", text)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动失败", "程序启动失败：\n\n%s" % text[-1500:])
            root.destroy()
        except Exception:
            pass
        return 1
    try:
        app.update_idletasks()
        app.deiconify()
        app.lift()
        _hide_console()
        app.mainloop()
    except Exception:
        text = traceback.format_exc()
        log.error("main loop crashed:\n%s", text)
        record_crash("mainloop", text)
        return 2
    return 0
