# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Interactive keyboard map for per-key RGB editing (ANSI full size)."""
from __future__ import annotations

import colorsys
import re

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

# (label, HID usage, width in key units).  usage None = gap.
_G = (None, None, 0.5)


def _row(items):
    return [(a, b, c) for a, b, c in items]


def _chars(s, usages):
    return [(ch, u, 1) for ch, u in zip(s, usages)]


ROWS = [
    [("Esc", 0x29, 1), _G, *[(f"F{i}", 0x3A + i - 1, 1) for i in range(1, 5)], _G,
     *[(f"F{i}", 0x3A + i - 1, 1) for i in range(5, 9)], _G,
     *[(f"F{i}", 0x3A + i - 1, 1) for i in range(9, 13)], _G,
     ("Prt", 0x46, 1), ("Scr", 0x47, 1), ("Pse", 0x48, 1)],
    [("`", 0x35, 1), *_chars("1234567890", range(0x1E, 0x28)), ("-", 0x2D, 1), ("=", 0x2E, 1),
     ("Back", 0x2A, 2), _G, ("Ins", 0x49, 1), ("Hom", 0x4A, 1), ("PgU", 0x4B, 1), _G,
     ("Num", 0x53, 1), ("/", 0x54, 1), ("*", 0x55, 1), ("-", 0x56, 1)],
    [("Tab", 0x2B, 1.5), *_chars("QWERTYUIOP", (0x14, 0x1A, 0x08, 0x15, 0x17, 0x1C, 0x18, 0x0C, 0x12, 0x13)),
     ("[", 0x2F, 1), ("]", 0x30, 1), ("\\", 0x31, 1.5), _G, ("Del", 0x4C, 1), ("End", 0x4D, 1),
     ("PgD", 0x4E, 1), _G, ("7", 0x5F, 1), ("8", 0x60, 1), ("9", 0x61, 1), ("+", 0x57, 1)],
    [("Caps", 0x39, 1.75), *_chars("ASDFGHJKL", (0x04, 0x16, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F)),
     (";", 0x33, 1), ("'", 0x34, 1), ("Enter", 0x28, 2.25), _G, (None, None, 3), _G,
     ("4", 0x5C, 1), ("5", 0x5D, 1), ("6", 0x5E, 1), (None, None, 1)],
    [("Shift", 0xE1, 2.25), *_chars("ZXCVBNM", (0x1D, 0x1B, 0x06, 0x19, 0x05, 0x11, 0x10)),
     (",", 0x36, 1), (".", 0x37, 1), ("/", 0x38, 1), ("Shift", 0xE5, 2.75), _G, (None, None, 1),
     ("↑", 0x52, 1), (None, None, 1), _G, ("1", 0x59, 1), ("2", 0x5A, 1), ("3", 0x5B, 1),
     ("Ent", 0x58, 1)],
    [("Ctrl", 0xE0, 1.25), ("Win", 0xE3, 1.25), ("Alt", 0xE2, 1.25), ("Space", 0x2C, 6.25),
     ("Alt", 0xE6, 1.25), ("Win", 0xE7, 1.25), ("Menu", 0x65, 1.25), ("Ctrl", 0xE4, 1.25), _G,
     ("←", 0x50, 1), ("↓", 0x51, 1), ("→", 0x4F, 1), _G, ("0", 0x62, 2), (".", 0x63, 1)],
]


def iso_rows():
    """ISO variant: tall Enter, the extra '<>' key next to the left Shift and the '#' key."""
    rows = [list(r) for r in ROWS]
    rows[2] = [("Enter", 0x28, 1.5, 2) if k[1] == 0x31 else k for k in rows[2]]
    row3 = []
    for k in rows[3]:
        if k[1] == 0x39:
            row3.append(("Caps", 0x39, 1.5))
        elif k[1] == 0x28:
            row3 += [("#", 0x32, 1), (None, None, 1.5)]       # '#' + the lower half of the tall Enter
        else:
            row3.append(k)
    rows[3] = row3
    rows[4] = [("Shift", 0xE1, 1.25) if k[1] == 0xE1 and k[2] == 2.25 else k for k in rows[4]]
    rows[4].insert(1, ("<", 0x64, 1))
    return rows


NUMPAD_USAGES = set(range(0x53, 0x64))                 # NumLock ... keypad '.'
_TKL_NAME = re.compile(r"\b(tkl|tenkeyless|ten-keyless)\b", re.I)


def detect_form(present_ids: set[int] | None, name: str = "") -> tuple[str, bool]:
    """(layout 'ansi'|'iso', has_numpad) from the key ids a keyboard reports; names are only a fallback."""
    if present_ids:
        iso = 97 in present_ids                               # the extra ISO key (usage 0x64)
        numpad = any(i in present_ids for i in range(80, 97))
        return ("iso" if iso else "ansi"), numpad
    return "ansi", not _TKL_NAME.search(name)


def build_rows(layout: str = "ansi", numpad: bool = True, present_ids: set[int] | None = None):
    """Key rows for a layout, dropping the numpad (TKL) and any key the keyboard says it does not have."""
    from ..hidpp.features import key_id
    rows = iso_rows() if layout == "iso" else [list(r) for r in ROWS]
    out = []
    for row in rows:
        kept = []
        for k in row:
            u = k[1]
            if u is not None and ((not numpad and u in NUMPAD_USAGES) or
                                  (present_ids is not None and key_id(u) not in present_ids)):
                continue
            kept.append(k)
        while kept and kept[-1][1] is None:                   # no dangling gaps where the numpad used to be
            kept.pop()
        out.append(kept)
    return out


class KeyboardWidget(QWidget):
    selectionChanged = Signal()
    colorPicked = Signal(object)          # (r, g, b) copied from a key (right-click, or the "copy colour" mode)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ROWS
        self.colors: dict[int, tuple[int, int, int]] = {}
        self.selected: set[int] = set()
        self.pick_mode = False             # True: the next click copies the key's colour instead of selecting it
        self._rects: list[tuple[QRectF, int, str]] = []
        self._anchor = None
        self.setMinimumHeight(250)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)                     # the keys grow with the width, the height follows
        self.setSizePolicy(policy)
        self.setMouseTracking(True)

    def _units(self) -> tuple[float, int]:
        """(width of the widest row in key units, number of rows)."""
        return max(sum(k[2] for k in row) for row in self.rows) + .2, len(self.rows)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        total, rows = self._units()
        return max(250, int(w / total * (rows + .4)))

    def sizeHint(self):
        return QSize(700, self.heightForWidth(700))

    # -- model
    def set_layout(self, name: str, numpad: bool = True, present_ids: set[int] | None = None) -> None:
        self.rows = build_rows(name, numpad, present_ids)
        self.update()

    def all_usages(self) -> list[int]:
        return [k[1] for row in self.rows for k in row if k[1] is not None]

    def clear_colors(self) -> None:
        self.colors.clear()
        self.update()

    def set_colors(self, colors: dict[int, tuple]) -> None:
        self.colors.update(colors)
        self.update()

    def apply_color(self, rgb, only_selected=True) -> dict[int, tuple]:
        keys = self.selected if only_selected and self.selected else set(self.all_usages())
        out = {u: tuple(rgb) for u in keys}
        self.colors.update(out)
        self.update()
        return out

    def rainbow(self) -> dict[int, tuple]:
        out = {}
        for r, row in enumerate(self.rows):
            x = 0.0
            for k in row:
                u, w = k[1], k[2]
                if u is not None:
                    h = ((x / 22.0) + r * 0.02) % 1.0
                    out[u] = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, 1, 1))
                x += w
        self.colors.update(out)
        self.update()
        return out

    def select_all(self) -> None:
        self.selected = set(self.all_usages())
        self.update()
        self.selectionChanged.emit()

    # -- painting / input
    def _layout(self) -> None:
        total = max(sum(k[2] for k in row) for row in self.rows)
        unit = min(self.width() / (total + .2), self.height() / (len(self.rows) + .4))
        gap = max(2.0, unit * .08)
        self._rects.clear()
        for r, row in enumerate(self.rows):
            x = 0.1 * unit
            y = r * unit + (0.2 * unit if r else 0)
            for k in row:
                label, u, w = k[0], k[1], k[2]
                span = k[3] if len(k) > 3 else 1
                if u is not None:
                    self._rects.append((QRectF(x + gap / 2, y + gap / 2, w * unit - gap,
                                               span * unit - gap), u, label))
                x += w * unit

    def paintEvent(self, _):
        self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        for rect, u, label in self._rects:
            c = self.colors.get(u)
            col = QColor(*c) if c else QColor(theme.CARD_HI)
            p.setBrush(col)
            sel = u in self.selected
            p.setPen(QPen(QColor(theme.ACCENT) if sel else QColor(theme.BORDER), 2 if sel else 1))
            p.drawRoundedRect(rect, 5, 5)
            lum = (0.3 * col.red() + 0.59 * col.green() + 0.11 * col.blue())
            p.setPen(QColor("#111" if lum > 140 else "#ddd"))
            f.setPixelSize(max(8, int(min(rect.height(), rect.width() * .95) * .34)))
            p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, label)

    def _hit(self, pos):
        for rect, u, _ in self._rects:
            if rect.contains(pos):
                return u
        return None

    def mousePressEvent(self, e):
        u = self._hit(e.position())
        if u is not None and (e.button() == Qt.RightButton or (self.pick_mode and e.button() == Qt.LeftButton)):
            self.pick_mode = False
            if u in self.colors:
                self.colorPicked.emit(tuple(self.colors[u]))
            self.update()
            return
        if e.button() != Qt.LeftButton:
            return
        mods = e.modifiers()
        if u is None:
            if not mods & Qt.ControlModifier:
                self.selected.clear()
        elif mods & Qt.ControlModifier:
            self.selected ^= {u}
        else:
            self.selected = {u}
        self._anchor = u
        self.update()
        self.selectionChanged.emit()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            u = self._hit(e.position())
            if u is not None and u not in self.selected:
                self.selected.add(u)
                self.update()
                self.selectionChanged.emit()
