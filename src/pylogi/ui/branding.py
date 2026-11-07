# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Application icon and start-up splash screen (both painted from the vector logo)."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication, QSplashScreen

from ..i18n import N_, tr

DATA = Path(__file__).resolve().parent.parent / "data"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ACCENT = "#3b82f6"


def render_logo(size: int) -> QImage:
    """The logo as an image of size x size pixels (transparent corners)."""
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    r = QSvgRenderer(str(DATA / "pylogi.svg"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def app_icon() -> QIcon:
    """Vector icon; if the SVG plugin is missing, the pre-rendered PNG set is used instead."""
    icon = QIcon(str(DATA / "pylogi.svg"))
    if not icon.isNull() and not icon.pixmap(32, 32).isNull():
        return icon
    icon = QIcon()
    for s in ICON_SIZES:
        f = DATA / "icons" / f"pylogi-{s}.png"
        if f.exists():
            icon.addFile(str(f), size=QSizeFrom(s))
    return icon


def QSizeFrom(s: int):
    from PySide6.QtCore import QSize
    return QSize(s, s)


class SplashScreen(QSplashScreen):
    """Borderless card with the logo, a status line and an indeterminate progress bar."""
    W, H = 560, 320
    SUBTITLE = N_("Manage your Logitech mice, keyboards and webcams")

    def __init__(self, version: str = "", message: str = ""):
        scale = max(1.0, QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0)
        super().__init__(self.background(version, scale),
                         Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.message = message
        self.phase = 0.0
        self.shown_at = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    @classmethod
    def background(cls, version: str = "", scale: float = 2.0) -> QPixmap:
        """Everything except the status line and the progress bar."""
        W, H = cls.W, cls.H
        pm = QPixmap(int(W * scale), int(H * scale))
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 24, 24)
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor("#1b2238"))
        bg.setColorAt(1, QColor("#090c17"))
        p.fillPath(card, bg)
        glow = QRadialGradient(QPointF(110, 112), 260)
        glow.setColorAt(0, QColor(59, 130, 246, 90))
        glow.setColorAt(1, QColor(59, 130, 246, 0))
        p.fillPath(card, glow)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(card)
        p.drawImage(QRectF(44, 62, 116, 116), render_logo(int(116 * scale)))

        title = QFont(QApplication.font())
        title.setPixelSize(50)
        title.setWeight(QFont.Bold)
        p.setFont(title)
        fm = QFontMetricsF(title)
        x, y = 188.0, 116.0
        p.setPen(QColor("#f4f6fb"))
        p.drawText(QPointF(x, y), "py")
        p.setPen(QColor(ACCENT))
        p.drawText(QPointF(x + fm.horizontalAdvance("py"), y), "Logi")

        sub = QFont(QApplication.font())
        sub.setPixelSize(14)
        p.setFont(sub)
        p.setPen(QColor("#9aa4bd"))
        p.drawText(QRectF(188, 138, W - 188 - 40, 54), Qt.TextWordWrap | Qt.AlignTop, tr(cls.SUBTITLE))
        if version:
            small = QFont(QApplication.font())
            small.setPixelSize(12)
            p.setFont(small)
            p.setPen(QColor("#667089"))
            p.drawText(QRectF(W - 140, H - 52, 100, 18), Qt.AlignRight | Qt.AlignVCenter, f"v{version}")
        p.end()
        return pm

    # -- dynamic part ---------------------------------------------------------------
    def set_message(self, text: str) -> None:
        self.message = text
        self.repaint()

    def _tick(self) -> None:
        self.phase = (self.phase + 0.018) % 1.0
        self.repaint()

    def drawContents(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(QApplication.font())
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QColor("#c4cbe0"))
        p.drawText(QRectF(44, self.H - 56, self.W - 200, 20), Qt.AlignLeft | Qt.AlignVCenter, self.message)
        track = QRectF(44, self.H - 28, self.W - 88, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(track, 2, 2)
        seg = track.width() * 0.28                    # a bright segment sliding to the right and wrapping
        start = track.left() + (track.width() + seg) * self.phase - seg
        a, b = max(track.left(), start), min(track.right(), start + seg)
        if b > a:
            grad = QLinearGradient(a, 0, b, 0)
            grad.setColorAt(0, QColor(59, 130, 246, 0))
            grad.setColorAt(0.5, QColor(ACCENT))
            grad.setColorAt(1, QColor(124, 180, 255))
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(a, track.top(), b - a, track.height()), 2, 2)

    # -- closing ----------------------------------------------------------------------
    def finish_when_ready(self, window, show_window, min_ms: int = 900, max_ms: int = 8000):
        """Return (ready, timeout) callbacks: the first one that fires shows the window and closes the splash."""
        done = []

        def finish() -> None:
            if done:
                return
            done.append(True)
            self._timer.stop()
            show_window()
            self.finish(window)

        def ready() -> None:                          # the first device scan completed
            left = max(0, min_ms - int((time.monotonic() - self.shown_at) * 1000))
            QTimer.singleShot(left, finish)
        QTimer.singleShot(max_ms, finish)
        return ready, finish
