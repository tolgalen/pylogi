# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Shared widgets: Card, colour swatch, error helper."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QLayout, QCheckBox, QColorDialog, QFrame, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from ..core.types import Unsupported
from ..i18n import tr


class Card(QFrame):
    """Rounded panel with a title; add content via `self.body`."""

    def __init__(self, title: str, parent: QWidget | None = None, expand: bool = False):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel(tr(title))
        self.title.setObjectName("h2")
        head.addWidget(self.title)
        head.addStretch()
        self.head = head
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        if expand:                 # the content (e.g. a table) uses all the height the page gives the card
            lay.addLayout(self.body, 1)
        else:
            lay.addLayout(self.body)
            lay.addStretch(1)      # keep content at the top when the card is stretched


class ColorButton(QPushButton):
    """Button showing a colour; clicking opens the colour dialog."""
    colorChanged = Signal(tuple)

    def __init__(self, rgb=(255, 255, 255), parent=None):
        super().__init__(parent)
        self._rgb = tuple(rgb)
        self.setFixedSize(54, 28)
        self.clicked.connect(self._pick)
        self._paint()

    def color(self):
        return self._rgb

    def setColor(self, rgb) -> None:
        self._rgb = tuple(rgb)
        self._paint()

    def _paint(self) -> None:
        pm = QPixmap(40, 16)
        pm.fill(QColor(*self._rgb))
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(40, 16))

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(*self._rgb), self, tr("Color"))
        if c.isValid():
            self.setColor((c.red(), c.green(), c.blue()))
            self.colorChanged.emit(self._rgb)


def guarded(parent: QWidget, fn: Callable, *args, quiet: bool = False):
    """Run a device call; show errors instead of crashing the UI."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        return fn(*args)
    except Unsupported:
        if not quiet:
            QMessageBox.information(parent, tr("Error"), tr("Not supported by this device"))
    except PermissionError:
        QMessageBox.warning(parent, tr("Permission denied"),
                            tr("Some HID devices could not be opened. "
                               "Install the udev rules from packaging/."))
    except Exception as e:  # device / protocol errors
        if not quiet:
            QMessageBox.warning(parent, tr("Error"), str(e))
    finally:
        QApplication.restoreOverrideCursor()
    return None


def debounced(slider, fn, ms=250):
    """Call fn(value) only after the user stops moving the slider."""
    t = QTimer(slider)
    t.setSingleShot(True)
    val = {}
    t.timeout.connect(lambda: fn(val["v"]))
    slider.valueChanged.connect(lambda v: (val.__setitem__("v", v), t.start(ms)))


def attempt(parent: QWidget, fn: Callable, *args) -> bool:
    """Run a device call; True on success, False (after telling the user) on failure."""
    failed = []

    def run(*a):
        try:
            return fn(*a)
        except Exception:
            failed.append(True)
            raise

    guarded(parent, run, *args)
    return not failed


def muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("muted")
    lb.setWordWrap(True)
    return lb


class Switch(QCheckBox):
    """Toggle switch with a knob (QSS cannot draw one); optional label on the right."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QCheckBox::indicator { width: 0; height: 0; }")

    def sizeHint(self) -> QSize:
        w = 40 + (10 + self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, 24)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _):
        from . import theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on, ok = self.isChecked(), self.isEnabled()
        track = QColor(theme.ACCENT if on else theme.SWITCH_OFF)
        if not ok:
            track.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 3, 36, 18, 9, 9)
        p.setBrush(QColor(theme.KNOB_ON if on else theme.KNOB_OFF))
        p.drawEllipse(3 + (18 if on else 0), 6, 12, 12)
        if self.text():
            p.setPen(QColor(theme.TEXT if ok else theme.MUTED))
            p.drawText(self.rect().adjusted(46, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())


def icon_card(icon_name: str, title: str):
    """Rounded card with an icon + title header; returns (frame, body layout)."""
    from PySide6.QtWidgets import QLabel
    from . import icons, theme
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    head = QHBoxLayout()
    ic = QLabel()
    ic.setPixmap(icons.glyph(icon_name, theme.MUTED, 16))
    t = QLabel(tr(title))
    t.setObjectName("h2")
    head.addWidget(ic)
    head.addWidget(t)
    head.addStretch(1)
    lay.addLayout(head)
    return f, lay



class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps them onto new lines, so none is ever cut off."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._place(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, dry: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > area.right() + 1 and line_h > 0:
                x, y, line_h = area.x(), y + line_h + self._space, 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._space
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
