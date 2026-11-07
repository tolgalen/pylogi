# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""'Device' section: details, Easy-Switch channels, keyboard keys and configuration."""
from __future__ import annotations


from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core.config import Config, config_dir
from ..core.device import Device
from ..core.settings import DeviceSettings
from ..i18n import N_, tr
from . import icons, theme
from .home import CATEGORIES, category, connection_text
from .widgets import Switch, attempt, guarded, icon_card, muted

_BUS = {"bluetooth": N_("Bluetooth"), "bolt": N_("Logi Bolt receiver"),
        "unifying": N_("Unifying receiver"), "lightspeed": N_("Lightspeed receiver"),
        "usb": N_("USB cable")}
_FAMILY_BUS = {"Bolt": "bolt", "Unifying": "unifying", "Lightspeed": "lightspeed",
               "Nano": "unifying", "Bluetooth": "bluetooth", "USB": "usb"}


def kv_grid(rows: list[tuple[str, str]], value_style: str = "font-size:15px;") -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(18)
    g.setVerticalSpacing(8)
    for r, (k, v) in enumerate(rows):
        lk = QLabel(k)
        lk.setObjectName("muted")
        lk.setWordWrap(True)
        lk.setFixedWidth(104)
        lv = QLabel(v)
        lv.setStyleSheet(value_style)
        lv.setWordWrap(True)
        lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g.addWidget(lk, r, 0, Qt.AlignTop)
        g.addWidget(lv, r, 1, Qt.AlignTop)
    g.setColumnStretch(1, 1)
    return g


def status_pill(dev: Device) -> QLabel:
    dot = f'<span style="color:{theme.GOOD}">●</span>' if dev.online else "○"
    pill = QLabel(f"{dot} " + (tr("Connected") if dev.online else tr("Offline")))
    pill.setTextFormat(Qt.RichText)
    pill.setObjectName("pill")
    return pill


class BatteryChart(QWidget):
    """Battery level over the last days: axes with values, charging periods marked, and the exact level
    and time under the mouse pointer. The time axis starts at the first sample (at most `days` back)."""
    LEFT, RIGHT, TOP, BOTTOM = 38, 8, 8, 22

    def __init__(self, samples: list[tuple[float, int, bool]], days: float = 30):
        super().__init__()
        self.samples, self.days = samples, days
        self.hover: int | None = None                      # index of the sample under the pointer
        self.setMinimumHeight(190)
        self.setMouseTracking(True)

    # -- geometry ---------------------------------------------------------------
    def _span(self) -> tuple[float, float]:
        import time
        now = time.time()
        first = self.samples[0][0] if self.samples else now
        t0 = max(now - self.days * 86400, first)
        return t0, max(now, t0 + 3600)

    def _plot(self):
        from PySide6.QtCore import QRectF
        return QRectF(self.LEFT, self.TOP, max(10, self.width() - self.LEFT - self.RIGHT),
                      max(10, self.height() - self.TOP - self.BOTTOM))

    def _point(self, t: float, pct: float):
        from PySide6.QtCore import QPointF
        r, (t0, t1) = self._plot(), self._span()
        return QPointF(r.left() + (t - t0) / (t1 - t0) * r.width(), r.top() + r.height() * (1 - pct / 100))

    # -- interaction ------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        if not self.samples:
            return
        x = e.position().x()
        i = min(range(len(self.samples)), key=lambda k: abs(self._point(self.samples[k][0], 0).x() - x))
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, _) -> None:
        self.hover = None
        self.update()

    @staticmethod
    def _stamp(t: float) -> str:
        """Date only (no time of day), in the system's short date format."""
        from PySide6.QtCore import QDateTime, QLocale
        d = QDateTime.fromSecsSinceEpoch(int(t)).date()
        return QLocale().toString(d, QLocale.ShortFormat)

    # -- painting ---------------------------------------------------------------
    def paintEvent(self, _):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r, (t0, t1) = self._plot(), self._span()
        small = p.font()
        small.setPixelSize(11)
        p.setFont(small)
        for pct in (0, 25, 50, 75, 100):                      # horizontal grid with the level on the left
            y = self._point(t0, pct).y()
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 8, self.LEFT - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{pct}%")
        ticks = 4                                              # dates under the plot
        for k in range(ticks + 1):
            t = t0 + (t1 - t0) * k / ticks
            x = r.left() + r.width() * k / ticks
            align = Qt.AlignLeft if k == 0 else Qt.AlignRight if k == ticks else Qt.AlignHCenter
            box = QRectF(x - 40 if align == Qt.AlignHCenter else x - 80 if align == Qt.AlignRight else x,
                         r.bottom() + 4, 80 if align == Qt.AlignHCenter else 80, 16)
            p.setPen(QColor(theme.MUTED))
            p.drawText(box, align | Qt.AlignVCenter, self._stamp(t))
        pts = [self._point(t, pct) for t, pct, _ in self.samples]
        if len(pts) < 2:
            return
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        area = QPainterPath(path)
        area.lineTo(QPointF(pts[-1].x(), r.bottom()))
        area.lineTo(QPointF(pts[0].x(), r.bottom()))
        fill = QColor(theme.ACCENT)
        fill.setAlpha(45)
        p.fillPath(area, fill)
        p.setPen(QPen(QColor(theme.ACCENT), 1.8))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.GOOD))
        for (_t, _pct, charging), q in zip(self.samples, pts):
            if charging:
                p.drawEllipse(q, 2.2, 2.2)
        if self.hover is not None and self.hover < len(pts):  # pointer read-out
            t, pct, charging = self.samples[self.hover]
            q = pts[self.hover]
            p.setPen(QPen(QColor(theme.BORDER_HI), 1, Qt.DashLine))
            p.drawLine(QPointF(q.x(), r.top()), QPointF(q.x(), r.bottom()))
            p.setPen(QPen(QColor(theme.PANEL), 2))
            p.setBrush(QColor(theme.battery_color(pct, charging)))
            p.drawEllipse(q, 4.5, 4.5)
            text = f"{pct}%" + (" ⚡" if charging else "") + "  ·  " + self._stamp(t)
            tw = p.fontMetrics().horizontalAdvance(text) + 16
            bx = min(max(q.x() - tw / 2, r.left()), r.right() - tw)
            by = r.top() + 2 if q.y() > r.top() + 34 else r.bottom() - 24
            box = QRectF(bx, by, tw, 22)
            p.setPen(QPen(QColor(theme.BORDER_HI), 1))
            p.setBrush(QColor(theme.CARD_HI))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor(theme.TEXT))
            p.drawText(box, Qt.AlignCenter, text)


def _days_left(days: float) -> str:
    if days < 1:
        return tr("less than a day left")
    n = int(round(days))
    return tr("about 1 day left") if n == 1 else tr("about {n} days left").format(n=min(n, 365))


def details_card(dev: Device, history=None) -> QFrame:
    card, lay = icon_card("info", N_("Device details"))
    top = QHBoxLayout()
    names = QVBoxLayout()
    names.setSpacing(2)
    n = QLabel(dev.name)
    n.setStyleSheet("font-size:15px;")
    n.setWordWrap(True)
    cat = dict((c[0], c[1]) for c in CATEGORIES)[category(dev)]
    c = QLabel(tr(cat))
    c.setObjectName("muted")
    names.addWidget(n)
    names.addWidget(c)
    top.addLayout(names, 1)
    top.addWidget(status_pill(dev), 0, Qt.AlignTop)
    lay.addLayout(top)
    b = dev.battery
    if dev.online and b is not None and b.percent is not None:
        row = QHBoxLayout()
        row.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icons.battery(b.percent, b.charging, theme.battery_color(b.percent, b.charging), 40, 18))
        pct = QLabel(f"{b.percent}%" + (" ⚡" if b.charging else ""))
        pct.setStyleSheet("font-size:20px;")
        left = history.estimate_days(dev.device_key(), b.percent) if history is not None and not b.charging else None
        lab = QLabel(tr("Battery") + (" · " + _days_left(left) if left is not None else ""))
        lab.setObjectName("muted")
        row.addWidget(ic)
        row.addWidget(pct)
        row.addWidget(lab, 1, Qt.AlignBottom)
        lay.addLayout(row)
    if history is not None and dev.online:
        samples = history.series(dev.device_key(), 30)
        if len(samples) >= 2:
            lab = QLabel(tr("Battery history") + " · " + tr("30 days"))
            lab.setObjectName("muted")
            lay.addWidget(lab)
            lay.addWidget(BatteryChart(samples))
    rows = [(tr("Connection"), connection_text(dev)), (tr("Device key"), dev.device_key())]
    if dev.info.serial:
        rows.append((tr("Serial number"), dev.info.serial.upper()))
    lay.addLayout(kv_grid(rows))
    return card


class EasySwitchSection(QFrame):
    """One row per channel: name, 'Channel n · …' line, rename / switch buttons."""

    def __init__(self, dev: Device):
        super().__init__()
        self.setObjectName("card")
        self.dev = dev
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 14, 18, 14)
        self.lay.setSpacing(12)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.glyph("network", theme.MUTED, 16))
        t = QLabel("Easy-Switch")
        t.setObjectName("h2")
        head.addWidget(ic)
        head.addWidget(t)
        head.addStretch(1)
        self.lay.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(12)
        self.lay.addLayout(self.rows)
        self.load()

    def _clear(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
            elif it.layout():
                lay = it.layout()
                while lay.count():
                    w = lay.takeAt(0)
                    if w.widget():
                        w.widget().hide()
                        w.widget().deleteLater()

    def load(self) -> None:
        self._clear()
        res = guarded(self, self.dev.hosts, quiet=True)
        if not res:
            self.rows.addWidget(muted(tr("Not available")))
            return
        _cur, hosts = res
        for h in hosts:
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(1)
            name = QLabel(h.name if h.paired and h.name else (tr("Empty") if not h.paired else f"{tr('Channel')} {h.index + 1}"))
            name.setStyleSheet("font-size:15px;" + ("" if h.paired else f"color:{theme.MUTED};"))
            parts = [f"{tr('Channel')} {h.index + 1}"]
            if h.current:
                parts.append(tr("This computer"))
            bus = _FAMILY_BUS.get(self.dev.connection) if h.current else h.bus
            if bus in _BUS:
                parts.append(tr(_BUS[bus]))
            sub = QLabel(" · ".join(parts))
            sub.setObjectName("muted")
            sub.setStyleSheet("font-size:12px;")
            col.addWidget(name)
            col.addWidget(sub)
            row.addLayout(col, 1)
            if h.paired and not h.current:
                sw = QPushButton(tr("Switch host"))
                sw.clicked.connect(lambda _=False, i=h.index: self._switch(i))
                row.addWidget(sw)
            if h.paired:
                rn = QPushButton(tr("Rename…"))
                rn.clicked.connect(lambda _=False, h=h: self._rename(h))
                row.addWidget(rn)
            self.rows.addLayout(row)

    def _rename(self, h) -> None:
        text, ok = QInputDialog.getText(self, tr("Rename…"), tr("Name"), text=h.name)
        text = text.strip()
        if ok and text and text != h.name and attempt(self, self.dev.rename_host, h.index, text):
            self.load()

    def _switch(self, i: int) -> None:
        if QMessageBox.question(self, tr("Easy-Switch"),
                                tr("The device will disconnect from this computer.")) == QMessageBox.Yes:
            attempt(self, self.dev.switch_host, i)


def keys_card(dev: Device) -> QFrame:
    card, lay = icon_card("keyboard", N_("Keys"))
    row = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(1)
    t = QLabel(tr("Fn lock"))
    t.setStyleSheet("font-size:15px;")
    available = dev.online and dev.has("fnlock")
    d = QLabel(tr("Use the F-keys as standard function keys without holding Fn.") if available
               else tr("This keyboard does not expose an Fn lock."))
    d.setObjectName("muted")
    d.setStyleSheet("font-size:12px;")
    d.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(d)
    row.addLayout(col, 1)
    sw = Switch()
    sw.setEnabled(available)
    if available:
        state = guarded(card, dev.fn_lock, quiet=True)
        sw.setChecked(bool(state))
        sw.toggled.connect(lambda on: attempt(card, dev.set_fn_lock, on))
    row.addWidget(sw)
    if not available:
        na = QLabel(tr("Not available"))
        na.setObjectName("muted")
        row.addWidget(na)
    lay.addLayout(row)
    return card


class ConfigCard(QFrame):
    settings_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self, dev: Device, cfg: Config):
        super().__init__()
        self.setObjectName("card")
        self.dev, self.cfg = dev, cfg
        self.settings = DeviceSettings(cfg)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.glyph("folder", theme.MUTED, 16))
        t = QLabel(tr("Configuration"))
        t.setObjectName("h2")
        head.addWidget(ic)
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)

        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(1)
        mt = QLabel(tr("Manage this device"))
        mt.setStyleSheet("font-size:15px;")
        md = QLabel(tr("Turning this off restores the default function of the controls "
                       "and pyLogi stops applying settings."))
        md.setObjectName("muted")
        md.setStyleSheet("font-size:12px;")
        md.setWordWrap(True)
        col.addWidget(mt)
        col.addWidget(md)
        row.addLayout(col, 1)
        self.manage = Switch()
        self.manage.setChecked(self.settings.managed(dev))
        self.manage.toggled.connect(self._managed)
        row.addWidget(self.manage)
        lay.addLayout(row)

        self.facts = QVBoxLayout()
        lay.addLayout(self.facts)
        self._facts()

        btns = QHBoxLayout()
        s = QPushButton(" " + tr("Settings"))
        s.setIcon(icons.icon("gear", theme.ICON, 16))
        s.clicked.connect(self.settings_requested)
        f = QPushButton(" " + tr("Configuration folder"))
        f.setIcon(icons.icon("folder", theme.ICON, 16))
        f.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir()))))
        btns.addWidget(s, 1)
        btns.addWidget(f, 1)
        lay.addLayout(btns)
        rm = QPushButton(" " + tr("Remove device…"))
        rm.setIcon(icons.icon("plug", theme.BAD, 16))
        rm.clicked.connect(lambda: self.remove_requested.emit(self.dev))
        lay.addWidget(rm)

    def _facts(self) -> None:
        while self.facts.count():
            it = self.facts.takeAt(0)
            if it.layout():
                lay = it.layout()
                while lay.count():
                    w = lay.takeAt(0)
                    if w.widget():
                        w.widget().hide()
                        w.widget().deleteLater()
        d, st = self.dev, self.settings
        profile = tr("Default profile")
        if d.has("webcam"):          # the active camera preset plays the profile's role
            name = self.cfg.get(f"campreset:{d.uid}", "Default")
            profile = tr(name) if name in ("Default", "Streaming", "Video call") else name
        rows = [(tr("Active profile"), profile)]
        if d.has("buttons") and d.kind in ("mouse", "trackball", "touchpad", "presenter"):
            rows.append((tr("Button remaps"), str(len(st.remaps(d)))))
        if d.has("dpi"):
            v = st.dpi(d)
            rows.append((tr("Saved DPI"), f"{v}" if v else "—"))
        if d.has("keylight"):
            rows.append((tr("Key colors"), str(len(st._rec(d).get("key_colors") or {}))))
        if d.has("webcam"):
            rows.append((tr("Camera presets"), str(len(self.cfg.get(f"cam:{d.uid}", {})))))
        self.facts.addLayout(kv_grid(rows, "font-size:16px;"))

    def _managed(self, on: bool) -> None:
        if not on and QMessageBox.question(
                self, tr("Manage this device"),
                tr("Restore the default function of the controls and stop applying settings?")
        ) != QMessageBox.Yes:
            self.manage.blockSignals(True)
            self.manage.setChecked(True)
            self.manage.blockSignals(False)
            return
        guarded(self, self.settings.set_managed, self.dev, on)
        self._facts()


class DetailsPage(QScrollArea):
    settings_requested = Signal()
    remove_requested = Signal(object)

    def __init__(self, dev: Device, cfg: Config, extra_cards: list[QWidget] | None = None, history=None):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.outer = QHBoxLayout(inner)
        self.outer.setContentsMargins(40, 24, 40, 24)
        self.outer.setSpacing(18)
        cards: list[QWidget] = [*(extra_cards or []), details_card(dev, history)]
        if dev.has("easyswitch"):
            cards.append(EasySwitchSection(dev))
        if dev.kind in ("keyboard", "numpad"):
            cards.append(keys_card(dev))
        conf = ConfigCard(dev, cfg)
        conf.settings_requested.connect(self.settings_requested)
        conf.remove_requested.connect(self.remove_requested)
        cards.append(conf)
        self.cards = cards
        self._columns: list[QWidget] = []
        self._ncols = 0
        self._layout_cards(1)
        self.setWidget(inner)

    COLUMN_WIDTH = 640          # widest a column grows
    TWO_COLUMNS_FROM = 1000     # viewport width from which the cards are split in two columns

    def _layout_cards(self, ncols: int) -> None:
        """Place the cards in `ncols` columns, each card going to the currently shortest one."""
        for c in self.cards:                                   # keep the cards alive when the columns go
            c.setParent(None)
        for col in self._columns:
            self.outer.removeWidget(col)
            col.setParent(None)
            col.deleteLater()
        while self.outer.count():                              # trailing stretch
            self.outer.takeAt(0)
        self._columns, heights = [], [0] * ncols
        layouts = []
        for _ in range(ncols):
            col, lay = QWidget(), QVBoxLayout()
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            col.setLayout(lay)
            col.setMaximumWidth(self.COLUMN_WIDTH)
            self._columns.append(col)
            layouts.append(lay)
        for c in self.cards:
            k = heights.index(min(heights))
            layouts[k].addWidget(c)
            c.show()
            heights[k] += c.sizeHint().height() + 14
        for col, lay in zip(self._columns, layouts):
            lay.addStretch(1)
            self.outer.addWidget(col, 1, Qt.AlignTop) if ncols > 1 else self.outer.addWidget(col, 1)
        self.outer.addStretch(1 if ncols == 1 else 0)
        self._ncols = ncols

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        n = 2 if self.viewport().width() >= self.TWO_COLUMNS_FROM and len(self.cards) > 1 else 1
        if n != self._ncols:
            self._layout_cards(n)
