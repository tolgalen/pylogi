# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Detect other programs that also manage Logitech devices over HID++.

Two managers diverting buttons or writing settings fight each other (last writer
wins): e.g. OpenLogi's agent re-diverts a button right after pyLogi restores it.
"""
from __future__ import annotations

import sys
from pathlib import Path

# process names (kernel "comm", at most 15 characters) of known Logitech device managers
MANAGERS = {
    "openlogi-agent": "OpenLogi", "openlogi": "OpenLogi", "solaar": "Solaar", "logid": "logiops",
    "ratbagd": "libratbag", "piper": "Piper", "logioptionsplus": "Logi Options+",
    "logioptionsplus_agent": "Logi Options+", "lghub_agent": "G HUB", "lghub": "G HUB",
}


def running_managers(proc: Path = Path("/proc")) -> list[str]:
    """Display names of the manager programs currently running (Linux; empty elsewhere)."""
    if not sys.platform.startswith("linux") or not proc.is_dir():
        return []
    found: list[str] = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip().lower()
        except OSError:
            continue
        name = MANAGERS.get(comm) or (MANAGERS.get(comm.replace(".py", "")) if comm.endswith(".py") else None)
        if name and name not in found:
            found.append(name)
    return sorted(found)
