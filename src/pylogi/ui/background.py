# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Run a blocking call (device I/O) off the UI thread and get the result back on it."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Signal


class Background(QObject):
    """`run(token, fn)` executes fn in a daemon thread; `finished(token, error)` fires in the UI thread."""
    finished = Signal(object, object)

    def run(self, token: object, fn: Callable[[], None]) -> None:
        threading.Thread(target=self._work, args=(token, fn), daemon=True).start()

    def _work(self, token: object, fn: Callable[[], None]) -> None:
        err = None
        try:
            fn()
        except Exception as e:      # reported to the receiver, never lost in the thread
            err = e
        try:
            self.finished.emit(token, err)
        except RuntimeError:        # the page was destroyed (e.g. language change) while we worked
            pass
