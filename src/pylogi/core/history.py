# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Battery history: one sample every few minutes per device, kept for a month."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import config_dir

SAMPLE_EVERY = 600.0          # seconds between samples of one device
KEEP = 30 * 24 * 3600


class BatteryHistory:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "battery.json"
        self.data: dict[str, list[list[float]]] = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def record(self, key: str, percent: int, charging: bool, now: float | None = None) -> bool:
        """Add a sample if the last one is old enough (or the level changed by 5+ points)."""
        now = time.time() if now is None else now
        series = self.data.setdefault(key, [])
        if series and now - series[-1][0] < SAMPLE_EVERY and abs(series[-1][1] - percent) < 5:
            return False
        series.append([now, percent, int(charging)])
        while series and now - series[0][0] > KEEP:
            series.pop(0)
        self._save()
        return True

    def series(self, key: str, days: float = 30, now: float | None = None) -> list[tuple[float, int, bool]]:
        now = time.time() if now is None else now
        return [(t, int(p), bool(c)) for t, p, c in self.data.get(key, []) if now - t <= days * 86400]

    def estimate_days(self, key: str, percent: int | None, now: float | None = None) -> float | None:
        """Days left at the recent discharge rate (last two weeks, charging periods ignored), or None
        when there is not enough data (under 6 hours of discharge or a drop under 3 points)."""
        if percent is None:
            return None
        pts = self.series(key, 14, now)
        drop = span = 0.0
        for (t1, p1, c1), (t2, p2, c2) in zip(pts, pts[1:]):
            if c1 or c2 or p2 > p1 or t2 - t1 > 2 * 86400:       # charging, a recharge, or a gap in the data
                continue
            drop += p1 - p2
            span += t2 - t1
        if span < 6 * 3600 or drop < 3:
            return None
        return percent / (drop / (span / 86400))

    def forget(self, key: str) -> None:
        if self.data.pop(key, None) is not None:
            self._save()

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass
