# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Small vector icons painted on the fly (no image assets)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)

from . import theme  # noqa: E402

GREEN, ORANGE, SKY = "#22c55e", "#f59e0b", "#38bdf8"


def _canvas(w: int, h: int, scale: float = 2.0):
    pm = QPixmap(int(w * scale), int(h * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _pen(color: str, width: float = 1.5) -> QPen:
    return QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def glyph(name: str, color: str | None = None, size: int = 16) -> QPixmap:
    """Monochrome UI glyphs: grid, list, category, gear, plus, dots, circle, plug, bluetooth."""
    color = color or theme.ICON
    pm, p = _canvas(size, size)
    s = float(size)
    p.setPen(_pen(color, max(1.3, s / 11)))
    p.setBrush(Qt.NoBrush)
    if name == "grid":
        for x in (.14, .56):
            for y in (.14, .56):
                p.drawRoundedRect(QRectF(s * x, s * y, s * .3, s * .3), 1.5, 1.5)
    elif name == "list":
        for y in (.24, .5, .76):
            p.drawLine(QPointF(s * .16, s * y), QPointF(s * .84, s * y))
    elif name == "category":   # stacked groups
        p.drawRoundedRect(QRectF(s * .2, s * .14, s * .6, s * .22), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * .14, s * .42, s * .72, s * .22), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * .08, s * .7, s * .84, s * .2), 1.5, 1.5)
    elif name == "plus":
        p.drawLine(QPointF(s * .5, s * .18), QPointF(s * .5, s * .82))
        p.drawLine(QPointF(s * .18, s * .5), QPointF(s * .82, s * .5))
    elif name == "gear":
        path = QPainterPath()
        import math
        for i in range(16):
            a = i * math.pi / 8
            r = s * (.42 if i % 2 == 0 else .32)
            pt = QPointF(s / 2 + r * math.cos(a), s / 2 + r * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(s / 2, s / 2), s * .13, s * .13)
    elif name == "help":
        from PySide6.QtGui import QFont
        p.drawEllipse(QRectF(s * .1, s * .1, s * .8, s * .8))
        f = QFont()
        f.setPixelSize(int(s * .56))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, s * .04, s, s), Qt.AlignCenter, "?")
    elif name == "chevron":
        p.drawPolyline(QPolygonF([QPointF(s * .36, s * .2), QPointF(s * .66, s * .5), QPointF(s * .36, s * .8)]))
    elif name == "back":
        p.drawPolyline(QPolygonF([QPointF(s * .64, s * .2), QPointF(s * .34, s * .5), QPointF(s * .64, s * .8)]))
    elif name in ("left", "right"):
        d = -1 if name == "left" else 1
        c = s * .5
        p.drawEllipse(QRectF(s * .08, s * .08, s * .84, s * .84))
        p.drawLine(QPointF(c - d * s * .2, c), QPointF(c + d * s * .2, c))
        p.drawPolyline(QPolygonF([QPointF(c + d * s * .02, c - s * .18), QPointF(c + d * s * .2, c),
                                  QPointF(c + d * s * .02, c + s * .18)]))
    elif name == "mouse":
        p.drawRoundedRect(QRectF(s * .28, s * .1, s * .44, s * .8), s * .2, s * .2)
        p.drawLine(QPointF(s * .5, s * .1), QPointF(s * .5, s * .42))
        p.drawLine(QPointF(s * .28, s * .42), QPointF(s * .72, s * .42))
    elif name == "gauge":
        p.drawArc(QRectF(s * .1, s * .14, s * .8, s * .8), 0, 180 * 16)
        p.drawLine(QPointF(s * .5, s * .54), QPointF(s * .72, s * .32))
    elif name == "click":    # pointer with sparks (buttons section)
        p.drawPolygon(QPolygonF([QPointF(s * .34, s * .3), QPointF(s * .84, s * .52), QPointF(s * .6, s * .6),
                                 QPointF(s * .5, s * .84)]))
        p.drawLine(QPointF(s * .2, s * .18), QPointF(s * .3, s * .26))
        p.drawLine(QPointF(s * .12, s * .4), QPointF(s * .24, s * .4))
        p.drawLine(QPointF(s * .34, s * .12), QPointF(s * .34, s * .22))
    elif name == "sliders":
        for y, x in ((.25, .35), (.5, .65), (.75, .4)):
            p.drawLine(QPointF(s * .12, s * y), QPointF(s * .88, s * y))
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(QPointF(s * x, s * y), s * .08, s * .08)
            p.setBrush(Qt.NoBrush)
    elif name == "bulb":
        p.drawEllipse(QRectF(s * .24, s * .1, s * .52, s * .52))
        p.drawLine(QPointF(s * .38, s * .74), QPointF(s * .62, s * .74))
        p.drawLine(QPointF(s * .42, s * .88), QPointF(s * .58, s * .88))
    elif name == "folder":
        path = QPainterPath(QPointF(s * .1, s * .26))
        for x, y in ((.4, .26), (.5, .36), (.9, .36), (.9, .8), (.1, .8)):
            path.lineTo(s * x, s * y)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "info":
        p.drawEllipse(QRectF(s * .1, s * .1, s * .8, s * .8))
        p.drawLine(QPointF(s * .5, s * .45), QPointF(s * .5, s * .7))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(color)))
        p.drawEllipse(QPointF(s * .5, s * .3), s * .05, s * .05)
    elif name == "network":
        p.drawRect(QRectF(s * .38, s * .1, s * .24, s * .2))
        p.drawRect(QRectF(s * .08, s * .68, s * .24, s * .2))
        p.drawRect(QRectF(s * .68, s * .68, s * .24, s * .2))
        p.drawLine(QPointF(s * .5, s * .3), QPointF(s * .5, s * .5))
        p.drawLine(QPointF(s * .2, s * .5), QPointF(s * .8, s * .5))
        p.drawLine(QPointF(s * .2, s * .5), QPointF(s * .2, s * .68))
        p.drawLine(QPointF(s * .8, s * .5), QPointF(s * .8, s * .68))
    elif name == "keyboard":
        p.drawRoundedRect(QRectF(s * .06, s * .24, s * .88, s * .52), 3, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(color)))
        for r in (.36, .5):
            for c in range(5):
                p.drawRect(QRectF(s * (.16 + c * .14), s * r, s * .07, s * .06))
        p.drawRect(QRectF(s * .28, s * .62, s * .44, s * .05))
    elif name == "eye":
        path = QPainterPath(QPointF(s * .08, s * .5))
        path.quadTo(QPointF(s * .5, s * .06), QPointF(s * .92, s * .5))
        path.quadTo(QPointF(s * .5, s * .94), QPointF(s * .08, s * .5))
        p.drawPath(path)
        p.drawEllipse(QPointF(s * .5, s * .5), s * .13, s * .13)
    elif name == "camera":
        p.drawRoundedRect(QRectF(s * .1, s * .26, s * .8, s * .55), 3, 3)
        p.drawEllipse(QPointF(s * .5, s * .54), s * .16, s * .16)
    elif name == "dots":
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(color)))
        for x in (.2, .5, .8):
            p.drawEllipse(QPointF(s * x, s * .5), s * .07, s * .07)
    elif name == "circle":
        p.drawEllipse(QRectF(s * .12, s * .12, s * .76, s * .76))
        p.setBrush(QBrush(QColor(color)))
        p.drawEllipse(QPointF(s / 2, s / 2), s * .09, s * .09)
    elif name == "plug":
        p.drawLine(QPointF(s * .36, s * .12), QPointF(s * .36, s * .3))
        p.drawLine(QPointF(s * .64, s * .12), QPointF(s * .64, s * .3))
        p.drawRoundedRect(QRectF(s * .24, s * .3, s * .52, s * .26), 2, 2)
        p.drawLine(QPointF(s * .5, s * .56), QPointF(s * .5, s * .88))
    elif name == "bluetooth":
        pts = [(.3, .3), (.7, .68), (.5, .86), (.5, .14), (.7, .32), (.3, .7)]
        path = QPainterPath(QPointF(s * pts[0][0], s * pts[0][1]))
        for x, y in pts[1:]:
            path.lineTo(s * x, s * y)
        p.drawPath(path)
    p.end()
    return pm


def receiver_logo(family: str, size: int = 16) -> QPixmap:
    """Stylised marks for the receiver families (not the official logos)."""
    pm, p = _canvas(size, size)
    s = float(size)
    p.setPen(Qt.NoPen)
    if family == "Bolt":
        p.setBrush(QBrush(QColor(GREEN)))
        p.drawPolygon(QPolygonF([QPointF(s * x, s * y) for x, y in
                                 ((.58, .04), (.14, .56), (.46, .56), (.38, .96), (.86, .4), (.54, .4))]))
    elif family == "Unifying":
        p.setBrush(QBrush(QColor(ORANGE)))
        import math
        pts = []
        for i in range(10):
            r = s * (.46 if i % 2 == 0 else .2)
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(QPointF(s / 2 + r * math.cos(a), s / 2 + r * math.sin(a)))
        p.drawPolygon(QPolygonF(pts))
    else:  # Lightspeed / Nano / other
        p.setBrush(QBrush(QColor(SKY)))
        p.drawPolygon(QPolygonF([QPointF(s * x, s * y) for x, y in
                                 ((.62, .04), (.2, .58), (.48, .58), (.36, .96), (.82, .38), (.54, .38))]))
    p.end()
    return pm


def battery(pct: int | None, charging: bool, color: str, w: int = 24, h: int = 12) -> QPixmap:
    pm, p = _canvas(w + 3, h)
    p.setPen(_pen(theme.ICON_DIM, 1.1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(.6, .6, w - 1.2, h - 1.2), 2.5, 2.5)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(theme.ICON_DIM)))
    p.drawRoundedRect(QRectF(w + .2, h * .3, 1.8, h * .4), .8, .8)
    if pct is not None:
        fw = max(1.5, (w - 4.4) * pct / 100)
        p.setBrush(QBrush(QColor(color)))
        p.drawRoundedRect(QRectF(2.2, 2.2, fw, h - 4.4), 1.3, 1.3)
    p.end()
    return pm


def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    return QIcon(glyph(name, color, size))
