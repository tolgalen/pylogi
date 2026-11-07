# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""HID++ 1.0 / 2.0 request-reply engine."""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from typing import Optional

from .constants import (ERRORS_10, ERRORS_20, F, LONG_ID, LONG_SIZE,
                        RECEIVER_NUMBER, SHORT_ID, SHORT_SIZE, SW_ID)
from . import trace
from .transport import Transport


class HidppError(Exception):
    pass


class NoReply(HidppError):
    pass


class Hidpp10Error(HidppError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"HID++ 1.0 error {code}: {ERRORS_10.get(code, '?')}")


class Hidpp20Error(HidppError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"HID++ 2.0 error {code}: {ERRORS_20.get(code, '?')}")


class FeatureMissing(HidppError):
    pass


class Channel:
    """A HID++ capable interface; serialises requests and matches replies."""

    def __init__(self, transport: Transport, has_short: bool = True):
        self.t = transport
        self.has_short = has_short
        self.lock = threading.RLock()
        self.notifications: deque[bytes] = deque(maxlen=128)
        self._event_flag = False
        self._listeners: list = []
        self._listen_thread: Optional[threading.Thread] = None
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self.t.close()

    def _frame(self, devnum: int, header: int, params: bytes, long: bool) -> bytes:
        data = struct.pack("!H", header) + params
        if not long and self.has_short and len(data) <= SHORT_SIZE - 2:
            return bytes([SHORT_ID, devnum]) + data.ljust(SHORT_SIZE - 2, b"\0")
        if len(data) > LONG_SIZE - 2:
            raise ValueError("HID++ payload too long")
        return bytes([LONG_ID, devnum]) + data.ljust(LONG_SIZE - 2, b"\0")

    def poll(self) -> bool:
        """Collect pending unsolicited reports; True if one is a device (dis)connection.

        Cheap (no blocking) and skipped when a request is in flight, so it can be
        called from the scan loop to react to devices waking up or going to sleep.
        """
        if not self.lock.acquire(blocking=False):
            return False
        hit = False
        try:
            while True:
                m = self.t.read(0)
                if m is None:
                    return hit
                self.notifications.append(m)
                trace.log("RX", m, "notification")
                if len(m) > 2 and m[0] in (SHORT_ID, LONG_ID) and m[2] in (0x40, 0x41):
                    hit = True
        except OSError:
            self._event_flag = True
            return True      # the interface vanished (unplugged): let the scan notice and drop it
        finally:
            self._event_flag |= hit
            self.lock.release()

    def connection_event(self) -> bool:
        """True once per (dis)connection report seen by anybody reading this channel."""
        hit = self.poll()
        flag, self._event_flag = self._event_flag or hit, False
        return flag

    # -- real-time listening (diverted buttons arrive as unsolicited reports) -----------
    def add_listener(self, fn) -> None:
        """fn(channel, report) is called from a background thread for every unsolicited report."""
        if fn not in self._listeners:
            self._listeners.append(fn)
        if self._listen_thread is None or not self._listen_thread.is_alive():
            self._listen_thread = threading.Thread(target=self._listen, daemon=True, name="hidpp-listen")
            self._listen_thread.start()

    def remove_listener(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _listen(self) -> None:
        while self._listeners and not self._closed:
            try:
                if not self.t.wait(0.1):
                    continue
                if not self.lock.acquire(blocking=False):
                    time.sleep(0.003)          # a request is in flight and will queue what it reads
                else:
                    self.lock.release()
                    self.poll()
                while self.notifications:
                    m = self.notifications.popleft()
                    for fn in list(self._listeners):
                        try:
                            fn(self, m)
                        except Exception:
                            pass
            except OSError:
                return

    def drain(self) -> None:
        while self.t.read(0) is not None:
            pass

    def request(self, devnum: int, header: int, params: bytes = b"",
                long: bool = False, timeout: float = 1.5) -> bytes:
        """Send a request, return the reply payload (after the 2 header bytes)."""
        hdr = struct.pack("!H", header)
        with self.lock:
            # stale notifications are queued, not dropped
            while True:
                m = self.t.read(0)
                if m is None:
                    break
                self.notifications.append(m)
            frame = self._frame(devnum, header, bytes(params), long)
            trace.log("TX", frame)
            self.t.write(frame)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise NoReply(f"no reply for {header:#06x}")
                m = self.t.read(left)
                if m is None or len(m) < 6 or m[0] not in (SHORT_ID, LONG_ID):
                    continue
                trace.log("RX", m)
                direct = devnum in (0x00, RECEIVER_NUMBER)
                if m[1] != devnum and not direct:
                    self.notifications.append(m)
                    continue
                if m[2] == 0x8F and m[3:5] == hdr:
                    raise Hidpp10Error(m[5])
                if m[2] == 0xFF and m[3:5] == hdr:
                    raise Hidpp20Error(m[5])
                if m[2:4] == hdr:
                    return bytes(m[4:])
                self.notifications.append(m)

    # HID++ 1.0 register helpers -------------------------------------------
    def read_register(self, devnum: int, reg: int, params: bytes = b"",
                      long: bool = False) -> bytes:
        base = 0x8300 if long else 0x8100
        return self.request(devnum, base | (reg & 0xFF), params)

    def write_register(self, devnum: int, reg: int, params: bytes) -> bytes:
        base = 0x8200 if len(params) > 3 else 0x8000
        return self.request(devnum, base | (reg & 0xFF), params)


class Hidpp20Device:
    """A HID++ 2.0 endpoint (device index on a channel)."""

    def __init__(self, channel: Channel, devnum: int):
        self.ch = channel
        self.devnum = devnum
        self._index: dict[int, Optional[int]] = {}
        self._features: Optional[dict[int, int]] = None
        self.protocol: Optional[tuple[int, int]] = None

    # -- low level
    def ping(self, timeout: float = 0.8) -> Optional[tuple[int, int]]:
        mark = 0xA5
        try:
            r = self.ch.request(self.devnum, 0x0010 | SW_ID,
                                bytes([0, 0, mark]), timeout=timeout)
        except Hidpp10Error:
            self.protocol = (1, 0)
            return None
        except HidppError:
            return None
        if len(r) >= 3 and r[2] == mark:
            self.protocol = (r[0], r[1])
            return self.protocol
        return None

    def feature_index(self, fid: int) -> Optional[int]:
        if fid == F.ROOT:
            return 0
        if fid not in self._index:
            try:
                r = self.ch.request(self.devnum, 0x0000 | SW_ID,
                                    struct.pack("!H", fid))
                self._index[fid] = r[0] or None
            except HidppError:
                self._index[fid] = None
        return self._index[fid]

    def has(self, fid: int) -> bool:
        return self.feature_index(fid) is not None

    def call(self, fid: int, fn: int, params: bytes = b"",
             long: bool = False, timeout: float = 1.5) -> bytes:
        idx = self.feature_index(fid)
        if idx is None:
            raise FeatureMissing(f"feature {fid:#06x} not supported")
        for attempt in range(6):
            try:
                return self.ch.request(self.devnum, (idx << 8) | (fn << 4) | SW_ID,
                                       bytes(params), long, timeout)
            except Hidpp20Error as e:
                if e.code != 8 or attempt == 5:        # 8 = BUSY: transient, try again shortly
                    raise
                time.sleep(0.03 * (attempt + 1))

    def features(self) -> dict[int, int]:
        """Full feature table {feature_id: index} (cached)."""
        if self._features is None:
            table: dict[int, int] = {}
            count = self.call(F.FEATURE_SET, 0)[0]
            for i in range(1, count + 1):
                r = self.call(F.FEATURE_SET, 1, bytes([i]))
                table[(r[0] << 8) | r[1]] = i
            self._features = table
            self._index.update(table)
        return self._features

    # -- identity
    def name(self) -> str:
        n = self.call(F.DEVICE_NAME, 0)[0]
        out = b""
        while len(out) < n:
            out += self.call(F.DEVICE_NAME, 1, bytes([len(out)]))
        return out[:n].decode("utf-8", "replace").rstrip("\0")

    def kind(self) -> str:
        from .constants import DEVICE_KINDS
        return DEVICE_KINDS.get(self.call(F.DEVICE_NAME, 2)[0], "unknown")
