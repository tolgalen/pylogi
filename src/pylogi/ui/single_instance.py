# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Run only one pyLogi per user: a second launch asks the running one to show its window and exits."""
from __future__ import annotations

import getpass
from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _name(demo: bool = False) -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"pylogi-{user}" + ("-demo" if demo else "")      # the demo mode never collides with the real app


def notify_running(demo: bool = False, message: bytes = b"show", timeout_ms: int = 500) -> bool:
    """True when another instance answered (message: b\"show\" shows its window, b\"toggle\" toggles it)."""
    sock = QLocalSocket()
    sock.connectToServer(_name(demo))
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(message)
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Listens for later launches and calls on_message('show' | 'toggle') for each of them."""

    def __init__(self, on_message: Callable[[str], None], demo: bool = False):
        super().__init__()
        self.on_message = on_message
        self.server = QLocalServer(self)
        name = _name(demo)
        QLocalServer.removeServer(name)                     # stale socket left by a crash
        self.ok = self.server.listen(name)
        self.server.newConnection.connect(self._connection)

    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.disconnected.connect(sock.deleteLater)
            sock.waitForReadyRead(200)
            self.on_message(bytes(sock.readAll()).decode("ascii", "ignore").strip() or "show")
