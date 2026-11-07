# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Appearance: palettes (dark / light), named themes and the application style sheet.

Like OpenLogi the accent colour (#3B82F6) is the same in every appearance.  The
appearance can be ``system`` (follow the OS), ``light`` or ``dark``.  Named
themes are JSON files in ``pylogi/themes`` and ``<config dir>/themes``::

    {"_meta": {"id": "midnight", "name": "Midnight"},
     "dark":  {"BG": "#0b1020", ...},     # any subset of the tokens below
     "light": {"BG": "#eef2fb", ...}}

Colour tokens are module attributes (``theme.BG``, ``theme.TEXT`` ...) that
change when :func:`apply` runs, so code must read them at call time and the UI
is rebuilt after a change.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QPainter, QPainterPath,
                           QPalette, QPen, QPixmap)

from ..i18n import N_

THEME_NAMES = [N_("Default"), N_("Midnight"), N_("Graphite"), N_("Forest"), N_("Sunset"), N_("Violet"), N_("Rose")]   # names of the shipped themes (for translation)
PACKAGE_THEMES = Path(__file__).resolve().parent.parent / "themes"

DARK = {
    "BG": "#17171a", "PANEL": "#1d1d21", "CARD": "#222226", "CARD_HI": "#2a2a2f",
    "BORDER": "#323238", "BORDER_HI": "#52525b", "TEXT": "#f4f4f5", "MUTED": "#a1a1aa",
    "ICON": "#e4e4e7", "ICON_DIM": "#d4d4d8", "LINE": "#71717a", "DISABLED": "#5b6470",
    "NAV": "#1e2a44", "GROOVE": "#1c2540", "GROOVE_OFF": "#2b3550", "HANDLE": "#090b12",
    "HANDLE_RING": "#131a2e", "SWITCH_OFF": "#3f3f46", "KNOB_ON": "#0b0b0f", "KNOB_OFF": "#18181b",
    "ACCENT": "#3b82f6", "ACCENT_HI": "#60a5fa", "ACCENT_DIM": "#1e3a8a", "ON_ACCENT": "#ffffff",
    "GOOD": "#22c55e", "WARN": "#f59e0b", "BAD": "#ef4444", "BAD_HI": "#f87171",
}
LIGHT = {
    "BG": "#f4f4f5", "PANEL": "#fafafa", "CARD": "#ffffff", "CARD_HI": "#f1f1f3",
    "BORDER": "#e0e0e5", "BORDER_HI": "#a1a1aa", "TEXT": "#18181b", "MUTED": "#6b6b76",
    "ICON": "#3f3f46", "ICON_DIM": "#52525b", "LINE": "#a1a1aa", "DISABLED": "#a1a1aa",
    "NAV": "#dbeafe", "GROOVE": "#d8e2f7", "GROOVE_OFF": "#e4e4e7", "HANDLE": "#ffffff",
    "HANDLE_RING": "#a9c3f5", "SWITCH_OFF": "#c4c4cc", "KNOB_ON": "#ffffff", "KNOB_OFF": "#ffffff",
    "ACCENT": "#3b82f6", "ACCENT_HI": "#2563eb", "ACCENT_DIM": "#bfdbfe", "ON_ACCENT": "#ffffff",
    "GOOD": "#16a34a", "WARN": "#d97706", "BAD": "#dc2626", "BAD_HI": "#b91c1c",
}
TOKENS = list(DARK)

globals().update(DARK)          # defaults until apply() is called
MODE = "dark"
THEME = "default"
QSS = ""


# ----------------------------------------------------------------- themes
def _user_dir() -> Path:
    from ..core.config import config_dir
    return config_dir() / "themes"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def available_themes() -> dict[str, str]:
    """{id: display name} from the package and the user's theme folder."""
    found: dict[str, str] = {"default": "Default"}
    dirs = [PACKAGE_THEMES]
    try:
        dirs.append(_user_dir())
    except OSError:
        pass
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            meta = _read(f).get("_meta", {})
            found[meta.get("id") or f.stem] = meta.get("name") or f.stem
    return found


def palette(theme_id: str, mode: str) -> dict[str, str]:
    """Base palette of the mode, overridden by the named theme (invalid colours ignored)."""
    pal = dict(DARK if mode == "dark" else LIGHT)
    dirs = [PACKAGE_THEMES]
    try:
        dirs.append(_user_dir())
    except OSError:
        pass
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            data = _read(f)
            if (data.get("_meta", {}).get("id") or f.stem) != theme_id:
                continue
            for k, v in data.get(mode, {}).items():
                if k in pal and isinstance(v, str) and QColor.isValidColorName(v):
                    pal[k] = v
    return pal


def resolve_mode(appearance: str) -> str:
    if appearance in ("dark", "light"):
        return appearance
    scheme = QGuiApplication.styleHints().colorScheme()
    return "light" if scheme == Qt.ColorScheme.Light else "dark"      # unknown -> the dark look


def apply(app, appearance: str = "system", theme_id: str = "default") -> str:
    """Activate a palette and restyle the application; returns the resolved mode."""
    global MODE, THEME, QSS
    mode = resolve_mode(appearance)
    if theme_id not in available_themes():
        theme_id = "default"
    globals().update(palette(theme_id, mode))
    MODE, THEME = mode, theme_id
    QSS = build_qss()
    app.setStyle("Fusion")
    app.setPalette(_qpalette())
    app.setStyleSheet(QSS)
    return mode


def _qpalette() -> QPalette:
    p = QPalette()
    g = globals()
    for role, token in ((QPalette.Window, "BG"), (QPalette.WindowText, "TEXT"), (QPalette.Base, "PANEL"),
                        (QPalette.AlternateBase, "CARD"), (QPalette.Text, "TEXT"), (QPalette.Button, "CARD_HI"),
                        (QPalette.ButtonText, "TEXT"), (QPalette.ToolTipBase, "PANEL"),
                        (QPalette.ToolTipText, "TEXT"), (QPalette.Highlight, "ACCENT"),
                        (QPalette.HighlightedText, "ON_ACCENT"), (QPalette.PlaceholderText, "MUTED"),
                        (QPalette.Link, "ACCENT_HI")):
        p.setColor(role, QColor(g[token]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(g["DISABLED"]))
    return p


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours."""
    def lum(c: str) -> float:
        q = QColor(c)
        ch = [q.redF(), q.greenF(), q.blueF()]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------- style sheet
def build_qss() -> str:
    return f"""
* {{ font-family: "Noto Sans", "Segoe UI", "Helvetica Neue", sans-serif; font-size: 13px; font-style: normal; }}
QMainWindow, QWidget#root, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 22px; font-weight: 600; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#card:hover {{ border-color: {BORDER_HI}; }}
QFrame#devcard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#devcard:hover, QFrame#devcard:focus {{ border-color: {ACCENT}; }}
QFrame#devcard[offline="true"] {{ border-color: {BAD}; }}
QFrame#devcard[offline="true"]:hover {{ border-color: {BAD_HI}; }}
QLabel#pill {{ border: 1px solid {BORDER}; border-radius: 10px; padding: 1px 9px; color: {MUTED};
    background: {PANEL}; font-size: 12px; }}
QLabel#name {{ font-size: 15px; font-weight: 500; }}
QLabel#title {{ font-size: 22px; font-weight: 500; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 3px; }}
QToolButton:hover {{ background: {CARD_HI}; }}
QToolButton#view {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QToolButton#view:checked {{ background: {CARD_HI}; border-color: {BORDER_HI}; }}
QToolButton::menu-indicator {{ image: none; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {CARD_HI}; }}
QDialog {{ background: {BG}; }}
QFrame#topbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QFrame#sidebar {{ background: {BG}; border-right: 1px solid {BORDER}; }}
QPushButton#nav {{ text-align: left; padding: 9px 12px; background: transparent; border: none;
    border-radius: 8px; color: {MUTED}; }}
QPushButton#nav:hover {{ color: {TEXT}; background: {PANEL}; }}
QPushButton#nav:checked {{ background: {NAV}; color: {TEXT}; }}
QLabel#chip {{ background: {NAV}; border-radius: 6px; padding: 5px 12px; color: {TEXT}; }}
QFrame#btncard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#btncard:hover {{ border-color: {BORDER_HI}; }}
QFrame#btncard[selected="true"] {{ border-color: {ACCENT_HI}; }}
QFrame#btncard:disabled {{ background: {PANEL}; }}
QPushButton {{ background: {CARD_HI}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_DIM}; }}
QPushButton:disabled {{ color: {DISABLED}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#host {{ border-radius: 12px; padding: 12px 10px; text-align: center; }}
QPushButton#host:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT_HI}; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton#flat:hover {{ color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 6px; background: {GROOVE}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::sub-page:horizontal:disabled {{ background: {GROOVE_OFF}; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -6px 0; border-radius: 8px;
    background: {HANDLE}; border: 2px solid {HANDLE_RING}; }}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT_HI}; }}
QSlider::handle:horizontal:disabled {{ background: {HANDLE}; border-color: {GROOVE}; }}
QPushButton#chipbtn {{ background: transparent; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 2px 11px; font-size: 12px; color: {MUTED}; }}
QPushButton#chipbtn:hover {{ border-color: {BORDER_HI}; color: {TEXT}; }}
QPushButton#chipbtn:checked {{ border-color: {ACCENT_HI}; color: {TEXT}; background: {NAV}; }}
QLabel#value {{ color: {ACCENT_HI}; font-size: 14px; }}
QLabel#value:disabled {{ color: {MUTED}; }}
QFrame#warning {{ background: rgba(245, 158, 11, 38); border-bottom: 1px solid {WARN}; }}
QFrame#warning QLabel {{ color: {TEXT}; }}
QLabel#section {{ color: {MUTED}; font-size: 12px; }}
QRadioButton {{ spacing: 10px; padding: 3px 0; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 9px; border: 2px solid {BORDER_HI};
    background: transparent; }}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ border-color: {ACCENT};
    background: qradialgradient(cx:.5, cy:.5, radius:.5, fx:.5, fy:.5,
        stop:0 {ACCENT}, stop:.5 {ACCENT}, stop:.55 transparent); }}
QComboBox, QLineEdit, QSpinBox {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 5px 10px; min-height: 18px; }}
QComboBox:hover, QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM}; }}
QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px; background: {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; }}
QTableWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: transparent; }}
QTableWidget::item {{ padding: 4px; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; padding: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


def battery_color(pct: int | None, charging: bool = False) -> str:
    if charging:
        return ACCENT
    if pct is None:
        return MUTED
    return GOOD if pct > 40 else WARN if pct > 15 else BAD


def device_icon(kind: str, size: int = 72, color: str | None = None) -> QPixmap:
    """Simple vector pictograms drawn on the fly (no image assets needed)."""
    color = color or ACCENT
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), size / 24, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(color).darker(500)))
    s = size
    if kind in ("mouse", "trackball", "touchpad"):
        path = QPainterPath()
        path.addRoundedRect(QRectF(s * .28, s * .1, s * .44, s * .8), s * .22, s * .22)
        p.drawPath(path)
        p.drawLine(QPointF(s * .28, s * .42), QPointF(s * .72, s * .42))
        p.drawLine(QPointF(s * .5, s * .1), QPointF(s * .5, s * .42))
        p.setBrush(QBrush(QColor(color)))
        p.drawRoundedRect(QRectF(s * .46, s * .2, s * .08, s * .12), 2, 2)
    elif kind in ("keyboard", "numpad", "remote", "presenter"):
        p.drawRoundedRect(QRectF(s * .06, s * .26, s * .88, s * .48), s * .07, s * .07)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(Qt.NoPen)
        for r in range(3):
            for c in range(7 if r < 2 else 5):
                x = s * (.14 + c * .108 + (.1 if r == 2 else 0))
                p.drawRoundedRect(QRectF(x, s * (.34 + r * .12), s * .07, s * .07), 1.5, 1.5)
    elif kind == "webcam":
        p.drawEllipse(QRectF(s * .2, s * .08, s * .6, s * .6))
        p.setBrush(QBrush(QColor(color)))
        p.drawEllipse(QRectF(s * .38, s * .26, s * .24, s * .24))
        p.setBrush(QBrush(QColor(color).darker(500)))
        p.drawRoundedRect(QRectF(s * .32, s * .74, s * .36, s * .14), 4, 4)
        p.drawLine(QPointF(s * .5, s * .68), QPointF(s * .5, s * .74))
    else:  # receiver / unknown
        p.drawRoundedRect(QRectF(s * .3, s * .12, s * .4, s * .56), 5, 5)
        p.setBrush(QBrush(QColor(color)))
        p.drawRect(QRectF(s * .38, s * .68, s * .24, s * .16))
        p.drawEllipse(QRectF(s * .44, s * .28, s * .12, s * .12))
    p.end()
    return pm
