# -*- coding: utf-8 -*-
"""Render worker thread.

tkinter is not thread-safe, so all Pillow work happens in a background thread
and the result is handed back through a queue that the Tk main loop polls.
Apart from keeping the window responsive while a large canvas is rendered,
this gives every render a place to catch and *log* failures.
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Optional

from .logging_setup import get_logger
from .options import RenderOptions
from .render import RenderResult, render

log = get_logger("worker")


@dataclass
class Job:
    token: int
    text: str
    options: RenderOptions


@dataclass
class Outcome:
    token: int
    result: Optional[RenderResult]
    error: str = ""
    elapsed: float = 0.0


class RenderWorker:
    """Runs renders one after another on a daemon thread."""

    def __init__(self) -> None:
        self._requests: "queue.Queue[Optional[Job]]" = queue.Queue()
        self.results: "queue.Queue[Outcome]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._token = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="render-worker", daemon=True
                )
                self._thread.start()

    def stop(self) -> None:
        self._requests.put(None)

    def submit(self, text: str, options: RenderOptions) -> int:
        self._token += 1
        self.start()
        self._requests.put(Job(self._token, text, options))
        return self._token

    def _loop(self) -> None:
        while True:
            job = self._requests.get()
            if job is None:
                return
            import time

            start = time.time()
            try:
                result = render(job.text, job.options)
                elapsed = time.time() - start
                log.debug(
                    "render #%d ok: %s in %.2fs font=%s",
                    job.token, result.size_label(), elapsed, ",".join(result.font_keys),
                )
                self.results.put(Outcome(job.token, result, "", elapsed))
            except Exception as exc:  # noqa: BLE001 - logged for the crash log
                text = traceback.format_exc()
                log.error("render #%d failed: %s\n%s", job.token, exc, text)
                self.results.put(Outcome(job.token, None, str(exc), time.time() - start))
