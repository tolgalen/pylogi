# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Home page: device overview as grid, list or grouped by category."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..core.config import Config
from ..core.device import Device
from ..core.conflicts import running_managers
from ..core.removal import is_hidden
from ..i18n import N_, tr
from . import icons, theme

CATEGORIES = [  # key, badge label, section label
    ("mouse", N_("Mouse"), N_("Mice")),
    ("keyboard", N_("Keyboard"), N_("Keyboards")),
    ("camera", N_("Camera"), N_("Webcams")),
    ("other", N_("Other"), N_("Devices")),
]
_KIND_TO_CAT = {"mouse": "mouse", "trackball": "mouse", "touchpad": "mouse",
                "keyboard": "keyboard", "numpad": "keyboard", "webcam": "camera"}
_FAMILY = {"Bolt": N_("Logi Bolt receiver"), "Unifying": N_("Unifying receiver"),
           "Lightspeed": N_("Lightspeed receiver"), "Nano": N_("Nano receiver")}


def category(dev: Device) -> str:
    return _KIND_TO_CAT.get(dev.kind, "other")


def connection_text(dev: Device) -> str:
    """Second line of a list row: receiver + slot, Bluetooth or USB."""
    if not dev.online:
        return tr("Not available")
    if dev.slot and dev.connection in _FAMILY:
        return f"{tr(_FAMILY[dev.connection])} · {tr('Slot')} {dev.slot}"
    if dev.connection == "Bluetooth":
        return tr("Bluetooth")
    return tr("USB cable")


def _sorted(devs: list[Device]) -> list[Device]:
    order = [c[0] for c in CATEGORIES]
    return sorted(devs, key=lambda d: (not d.online, order.index(category(d)), d.name.lower()))


class ElidedLabel(QLabel):
    """Label that shortens its text with an ellipsis instead of widening its card."""

    def __init__(self, text: str):
        super().__init__(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(30)

    def paintEvent(self, e):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width()))
        super().paintEvent(e)


class PhotoLabel(QWidget):
    """Shows the product photo scaled to fit, or a drawn icon as fallback."""

    def __init__(self, dev: Device, images, parent=None):
        super().__init__(parent)
        self.dev, self.images = dev, images
        self._cache: tuple[QSize, object] | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if not self.dev.online:
            p.setOpacity(0.4)
        photo = self.images.photo(self.dev) if self.images else None
        if photo is not None:
            if self._cache is None or self._cache[0] != self.size():
                self._cache = (self.size(), photo.scaled(self.size(), Qt.KeepAspectRatio,
                                                         Qt.SmoothTransformation))
            pm = self._cache[1]
        else:
            side = max(24, min(self.width(), self.height(), 120))
            pm = theme.device_icon(self.dev.kind, side, theme.MUTED)
        dpr = pm.devicePixelRatio()
        w, h = pm.width() / dpr, pm.height() / dpr
        p.drawPixmap(int((self.width() - w) / 2), int((self.height() - h) / 2), pm)


def _badge(dev: Device) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    ic = QLabel()
    text = ""
    if not dev.online:
        ic.setPixmap(icons.glyph("circle", theme.MUTED, 16))
    elif dev.slot and dev.connection in _FAMILY:
        ic.setPixmap(icons.receiver_logo(dev.connection, 16))
        text = f"{tr('Slot')} {dev.slot}"
    elif dev.connection == "Bluetooth":
        ic.setPixmap(icons.glyph("bluetooth", "#60a5fa", 16))
    else:
        ic.setPixmap(icons.glyph("plug", theme.MUTED, 16))
    lay.addWidget(ic)
    if text:
        t = QLabel(text)
        t.setObjectName("muted")
        lay.addWidget(t)
    return w


def _battery_label(dev: Device, with_text: bool = False) -> QWidget | None:
    b = dev.battery
    if not dev.online or b is None or b.percent is None:
        return None
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    ic = QLabel()
    ic.setPixmap(icons.battery(b.percent, b.charging, theme.battery_color(b.percent, b.charging)))
    ic.setToolTip(f"{tr('Battery')} {b.percent}%" + (f" · {tr('Charging')}" if b.charging else ""))
    lay.addWidget(ic)
    if with_text:
        lay.addWidget(QLabel(f"{b.percent}%" + (" ⚡" if b.charging else "")))
    return w


class _Base(QFrame):
    clicked = Signal(object)
    rescan = Signal()
    remove = Signal(object)
    picture = Signal(object, bool)      # (device, reset?)

    def __init__(self, dev: Device):
        super().__init__()
        self.dev = dev
        self.setObjectName("devcard")
        self.setProperty("offline", not dev.online)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)               # reachable with Tab, opened with Enter / Space
        self.setAccessibleName(f"{dev.name}, {tr('Connected') if dev.online else tr('Offline')}")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.dev)
        else:
            super().keyPressEvent(e)

    def _menu_button(self) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon("dots", theme.MUTED, 18))
        b.setCursor(Qt.ArrowCursor)
        b.setAccessibleName(tr("More options"))
        b.setToolTip(tr("More options"))
        b.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(b)
        m.addAction(tr("Open"), lambda: self.clicked.emit(self.dev))
        m.addAction(tr("Rescan"), self.rescan.emit)
        m.addSeparator()
        m.addAction(tr("Choose picture…"), lambda: self.picture.emit(self.dev, False))
        m.addAction(tr("Use the original picture"), lambda: self.picture.emit(self.dev, True))
        m.addSeparator()
        m.addAction(tr("Remove…"), lambda: self.remove.emit(self.dev))
        b.setMenu(m)
        return b

    def _name_row(self, elide: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        n = ElidedLabel(self.dev.name) if elide else QLabel(self.dev.name)
        n.setObjectName("name")
        n.setToolTip(self.dev.name)
        row.addWidget(n, 1 if elide else 0)
        label = dict((c[0], c[1]) for c in CATEGORIES)[category(self.dev)]
        pill = QLabel(tr(label))
        pill.setObjectName("pill")
        row.addWidget(pill)
        if not elide:
            row.addStretch(1)
        return row

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.dev)


class DeviceCard(_Base):
    """Grid cell: badges on top, large photo, name + category pill."""

    def __init__(self, dev: Device, images):
        super().__init__(dev)
        self.setMinimumHeight(268)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 14, 12)
        top = QHBoxLayout()
        top.addWidget(_badge(dev))
        top.addStretch(1)
        bat = _battery_label(dev, True)
        if bat:
            top.addWidget(bat)
        lay.addLayout(top)
        lay.addWidget(PhotoLabel(dev, images), 1)
        row = self._name_row(elide=True)
        row.addWidget(self._menu_button())
        lay.addLayout(row)


class DeviceRow(_Base):
    """List row: thumbnail, name/category, status line, battery."""

    def __init__(self, dev: Device, images):
        super().__init__(dev)
        self.setFixedHeight(100)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(18)
        ph = PhotoLabel(dev, images)
        ph.setFixedSize(130, 82)
        lay.addWidget(ph)
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addStretch(1)
        col.addLayout(self._name_row())
        st = QHBoxLayout()
        st.setSpacing(6)
        dot = QLabel("●" if dev.online else "○")
        dot.setStyleSheet(f"color:{theme.GOOD if dev.online else theme.MUTED};font-size:9px;")
        st.addWidget(dot)
        txt = QLabel(tr("Connected") if dev.online else tr("Offline"))
        txt.setObjectName("muted")
        st.addWidget(txt)
        sep = QLabel("·")
        sep.setObjectName("muted")
        st.addWidget(sep)
        info = QLabel(connection_text(dev))
        info.setObjectName("muted")
        st.addWidget(info)
        st.addStretch(1)
        col.addLayout(st)
        col.addStretch(1)
        lay.addLayout(col, 1)
        bat = _battery_label(dev, True)
        if bat:
            lay.addWidget(bat)
        lay.addSpacing(30)
        lay.addWidget(self._menu_button(), 0, Qt.AlignBottom)


class ResponsiveGrid(QWidget):
    """Grid whose column count follows the available width."""

    def __init__(self, widgets: list[QWidget], min_width: int = 330, gap: int = 14):
        super().__init__()
        self.items, self.min_width, self.gap = widgets, min_width, gap
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(gap)
        self.cols = 0
        self._layout(max(1, self.width() // min_width) if self.width() > 1 else 3)

    def _layout(self, cols: int) -> None:
        if cols == self.cols:
            return
        self.cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols)
        for c in range(max(cols, 8)):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout(max(1, (self.width() + self.gap) // (self.min_width + self.gap)))


class HomePage(QWidget):
    open_device = Signal(object)
    settings_requested = Signal()
    add_requested = Signal()
    rescan_requested = Signal()
    remove_requested = Signal(object)
    picture_requested = Signal(object, bool)
    help_requested = Signal()
    about_requested = Signal(str)

    VIEWS = [("grid", N_("Grid")), ("list", N_("List")), ("category", N_("By category"))]

    def __init__(self, images, cfg: Config):
        super().__init__()
        self.images, self.cfg = images, cfg
        self.view = cfg.get("view", "grid")
        if self.view not in dict(self.VIEWS):
            self.view = "grid"
        self.devices: list[Device] = []
        self.errors: list[str] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(28, 18, 28, 14)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        t = QLabel(tr("Devices"))
        t.setObjectName("title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        titles.addWidget(t)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in self.VIEWS:
            b = QToolButton()
            b.setObjectName("view")
            b.setCheckable(True)
            b.setIcon(icons.icon(key, theme.ICON, 16))
            b.setToolTip(tr(label))
            b.setAccessibleName(tr(label))
            b.setChecked(key == self.view)
            b.clicked.connect(lambda _=False, k=key: self.set_view(k))
            group.addButton(b)
            head.addWidget(b)
        head.addSpacing(8)
        helpb = QToolButton()
        helpb.setObjectName("view")
        helpb.setIcon(icons.icon("help", theme.ICON, 16))
        helpb.setToolTip(tr("Help"))
        helpb.setAccessibleName(tr("Help"))
        helpb.setPopupMode(QToolButton.InstantPopup)
        hm = QMenu(helpb)
        hm.addAction(tr("Help") + "\tF1", self.help_requested.emit)
        hm.addSeparator()
        hm.addAction(tr("About pyLogi"), lambda: self.about_requested.emit("about"))
        hm.addAction(tr("License"), lambda: self.about_requested.emit("license"))
        helpb.setMenu(hm)
        head.addWidget(helpb)
        settings = QPushButton(" " + tr("Settings"))
        settings.setIcon(icons.icon("gear", theme.ICON, 16))
        settings.clicked.connect(self.settings_requested)
        head.addWidget(settings)
        add = QPushButton(" " + tr("Add"))
        add.setObjectName("primary")
        add.setIcon(icons.icon("plus", "#ffffff", 16))
        add.clicked.connect(self.add_requested)
        head.addWidget(add)
        root.addLayout(head)
        self.banner = QFrame()                       # "another program is managing your devices"
        self.banner.setObjectName("warning")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(28, 8, 28, 8)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        bl.addWidget(self.banner_text, 1)
        dismiss = QPushButton(tr("Dismiss"))
        dismiss.clicked.connect(self._dismiss_banner)
        bl.addWidget(dismiss)
        self.banner.hide()
        root.addWidget(self.banner)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER};")
        root.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.holder = QWidget()
        self.body = QVBoxLayout(self.holder)
        self.body.setContentsMargins(28, 18, 28, 20)
        self.body.setSpacing(12)
        self.scroll.setWidget(self.holder)
        root.addWidget(self.scroll, 1)

    def _update_banner(self) -> None:
        ignored = set(self.cfg.get("ignored_managers", []))
        names = [n for n in running_managers() if n not in ignored]
        self.banner.setVisible(bool(names))
        if names:
            self.banner_text.setText("⚠ " + tr("Another program is also controlling your Logitech devices ({names}). "
                                                "Button actions and settings can conflict; close it for reliable results."
                                                ).format(names=", ".join(names)))

    def _dismiss_banner(self) -> None:
        self.cfg.set("ignored_managers", sorted(set(self.cfg.get("ignored_managers", [])) | set(running_managers())))
        self._update_banner()

    # -- model -> widgets
    def set_view(self, view: str) -> None:
        self.view = view
        self.cfg.set("view", view)
        self.populate(self.devices, self.errors)

    def populate(self, devices: list[Device], errors: list[str]) -> None:
        self.devices, self.errors = devices, errors
        self._update_banner()
        shown = _sorted([d for d in devices if d.kind != "receiver" and not is_hidden(self.cfg, d)])
        n = len(shown)
        self.subtitle.setText(tr("1 device") if n == 1 else tr("{n} devices").format(n=n))
        self._clear()
        if not shown:
            self._empty(errors)
            return
        if self.view == "list":
            for d in shown:
                self.body.addWidget(self._wire(DeviceRow(d, self.images)))
        elif self.view == "category":
            for key, _badge_label, section in CATEGORIES:
                group = [d for d in shown if category(d) == key]
                if group:
                    h = QLabel(tr(section))
                    h.setObjectName("h2")
                    self.body.addWidget(h)
                    self.body.addWidget(ResponsiveGrid([self._wire(DeviceCard(d, self.images)) for d in group]))
        else:
            self.body.addWidget(ResponsiveGrid([self._wire(DeviceCard(d, self.images)) for d in shown]))
        self.body.addStretch(1)

    def _wire(self, w: _Base) -> _Base:
        w.clicked.connect(self.open_device)
        w.rescan.connect(self.rescan_requested)
        w.remove.connect(self.remove_requested)
        w.picture.connect(self.picture_requested)
        return w

    def _clear(self) -> None:
        while self.body.count():
            it = self.body.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _empty(self, errors: list[str]) -> None:
        self.body.addSpacing(70)
        t = QLabel(tr("No Logitech devices found"))
        t.setObjectName("h1")
        t.setAlignment(Qt.AlignCenter)
        self.body.addWidget(t)
        m = QLabel(tr("Connect a receiver, a wired device or pair one over Bluetooth."))
        m.setObjectName("muted")
        m.setAlignment(Qt.AlignCenter)
        self.body.addWidget(m)
        if any("Permission" in e or "Errno 13" in e for e in errors):
            w = QLabel(tr("Some HID devices could not be opened. Install the udev rules from packaging/."))
            w.setObjectName("muted")
            w.setAlignment(Qt.AlignCenter)
            w.setWordWrap(True)
            self.body.addWidget(w)
        self.body.addStretch(1)
