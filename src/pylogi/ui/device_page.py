# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Per-device page: top bar, section sidebar and the section pages."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.actions import describe
from ..core.config import Config
from ..core.device import Device
from ..core.settings import DeviceSettings
from ..i18n import N_, tr
from . import icons, theme
from .background import Background
from .button_map import ButtonMap, Inspector
from .cards import LightingCard, PointerCard, ProfilesCard, ReceiverCard, is_pointing
from .details import DetailsPage, status_pill
from .camera_page import CameraPage
from .widgets import attempt, guarded, muted


def _scroll(*cards: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(28, 22, 28, 22)
    lay.setSpacing(14)
    for c in cards:
        lay.addWidget(c)
    lay.addStretch(1)
    sc.setWidget(inner)
    return sc


_TYPE_GLYPH = {"key": "keyboard", "media": "circle", "mouse": "mouse", "scroll": "list",
               "text": "keyboard", "command": "gear", "dpi": "gauge", "macro": "list", "gesture": "mouse",
               "none": "circle"}


class ButtonsPage(QWidget):
    """Profile strip + button map + inspector (the buttons editor)."""

    def __init__(self, dev: Device, images, settings=None):
        super().__init__()
        self.dev, self.images, self.settings = dev, images, settings
        self.engine = getattr(settings, "engine", None)
        self.profile = ""
        self.controls = guarded(self, dev.controls, quiet=True) or []
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)

        strip = QHBoxLayout()
        strip.setContentsMargins(16, 10, 16, 8)
        strip.setSpacing(8)
        lbl = QLabel(tr("Profile"))
        lbl.setObjectName("muted")
        strip.addWidget(lbl)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        strip.addLayout(self.chips)
        strip.addStretch(1)
        self.add_btn = QPushButton("+ " + tr("Add application"))
        self.add_btn.clicked.connect(self._add_app)
        strip.addWidget(self.add_btn)
        self.root.addLayout(strip)
        hint = QHBoxLayout()
        hint.setContentsMargins(16, 0, 16, 6)
        h = QLabel(tr("Default assignments are used unless an app profile replaces them."))
        h.setObjectName("muted")
        self.active = QLabel("")
        self.active.setObjectName("muted")
        hint.addWidget(h)
        hint.addStretch(1)
        hint.addWidget(self.active)
        self.root.addLayout(hint)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        self.root.addWidget(sep)

        self.body = QHBoxLayout()
        self.body.setSpacing(0)
        self.root.addLayout(self.body, 1)
        self.map: ButtonMap | None = None
        self._has_layout = False
        self._chip_row()
        self._build()
        self._timer = QTimer(self)                   # the application in focus changes by itself
        self._timer.timeout.connect(self._refresh_active)
        self._timer.start(1000)
        self._refresh_active()

    # -- profiles
    def _profiles(self) -> list[str]:
        return self.settings.profiles(self.dev) if self.settings else [""]

    def _chip_row(self) -> None:
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        group = QButtonGroup(self)
        for name in self._profiles():
            b = QPushButton(tr("Default") if not name else name)
            b.setObjectName("chipbtn")
            b.setCheckable(True)
            b.setChecked(name == self.profile)
            b.clicked.connect(lambda _=False, n=name: self._select_profile(n))
            if name:
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, n=name, w=b: self._chip_menu(n, w, pos))
            group.addButton(b)
            self.chips.addWidget(b)
        self.add_btn.setVisible(self.settings is not None)

    def _select_profile(self, name: str) -> None:
        self.profile = name
        self._chip_row()
        self._build()

    def _add_app(self) -> None:
        current = ""
        if self.engine is not None and self.engine.detector.available:
            current = self.engine.detector.current() or ""
        hint = tr("Application (window class, e.g. firefox)")
        if self.engine is not None and not self.engine.detector.available:
            hint += "\n" + tr("Automatic switching is unavailable:") + " " + self.engine.detector.reason
        name, ok = QInputDialog.getText(self, tr("Add application"), hint, text=current)
        name = name.strip().lower()
        if ok and name:
            self.settings.add_profile(self.dev, name)
            self._select_profile(name)

    def _chip_menu(self, name: str, widget: QWidget, pos) -> None:
        m = QMenu(self)
        m.addAction(tr("Delete profile"), lambda: self._delete_profile(name))
        m.exec(widget.mapToGlobal(pos))

    def _delete_profile(self, name: str) -> None:
        self.settings.remove_profile(self.dev, name)
        if self.profile == name:
            self.profile = ""
        self._chip_row()
        self._build()
        if self.engine is not None:
            attempt(self, self.settings.apply_buttons, self.dev)

    def _refresh_active(self) -> None:
        prof = self.engine.profile_for(self.dev) if self.engine else ""
        self.active.setText(tr("Active: {name}").format(name=prof or tr("Default")))

    # -- what each card shows
    def _summary(self, cid: int) -> tuple[str, str]:
        c = next(x for x in self.controls if x.cid == cid)
        b = self.settings.effective(self.dev, self.profile).get(cid) if self.settings else None
        if not b:
            return tr(c.name), "click"
        t = b["type"]
        if t == "remap":
            target = next((x for x in self.controls if x.cid == b.get("cid")), None)
            return tr(target.name if target else c.name), "click"
        text = {"key": describe(b), "media": tr(b.get("name", "")), "text": '"' + b.get("text", "") + '"',
                "command": b.get("cmd", ""), "none": tr("Disabled"),
                "mouse": tr(Inspector.MOUSE_LABELS.get(b.get("button", ""), "")),
                "scroll": tr(Inspector.SCROLL_LABELS.get(b.get("dir", ""), "")),
                "dpi": tr("Next DPI") if b.get("mode", "next") == "next" else tr("Previous DPI"),
                "macro": describe(b), "gesture": tr("Gesture")}.get(t, "")
        return text, _TYPE_GLYPH.get(t, "click")

    def _build(self) -> None:
        sel = self.map.current if self.map else None
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self.controls:
            self.body.addWidget(muted(tr("No configurable buttons found.")), 1)
            return
        lay = self.images.buttons_layout(self.dev) if self.images else None
        self._has_layout = lay is not None
        photo, slots = lay if lay else (None, None)
        self.map = ButtonMap(self.controls, photo, slots, self._summary)
        self.inspector = Inspector(self.dev, self.controls, self.map.refresh, self.settings,
                                   self.engine, self.profile)
        self.map.selected.connect(self.inspector.show_control)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 8, 10, 8)
        wl.addWidget(self.map)
        self.body.addWidget(wrap, 1)
        line = QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background:{theme.BORDER};")
        self.body.addWidget(line)
        self.inspector.setFixedWidth(310)
        self.body.addWidget(self.inspector)
        if sel is not None and sel in self.map.cards:
            self.map._select(sel)

    def images_ready(self) -> None:
        """A catalog download finished: upgrade the fallback list to the photo map."""
        if self.images and not self._has_layout and self.images.buttons_layout(self.dev):
            self._build()


# slow device reads each section needs; fetched off the UI thread before the page is built
SECTION_READS = {
    "buttons": ["controls"],
    "pointer": ["dpi", "smartshift", "report_rate", "wheel", "thumb"],
    "profiles": ["profiles", "profile_host_mode", "active_profile"],
    "lighting": ["zones", "backlight", "key_presence"],
    "webcam": ["cam_controls"],
    "device": ["hosts", "fn_lock"],
}


class DevicePage(QWidget):
    back = Signal()
    settings_requested = Signal()
    remove_requested = Signal(object)
    picture_requested = Signal(object, bool)

    SECTIONS = {  # key -> (label, glyph)
        "buttons": (N_("Buttons"), "click"),
        "pointer": (N_("Pointer"), "gauge"),
        "profiles": (N_("Profiles"), "sliders"),
        "lighting": (N_("Lighting"), "bulb"),
        "webcam": (N_("Camera"), "camera"),
        "device": (N_("Device"), "gear"),
    }

    def __init__(self, cfg: Config, images=None, settings: DeviceSettings | None = None):
        super().__init__()
        self.cfg, self.images = cfg, images
        self.settings = settings or DeviceSettings(cfg)
        self.history = None                 # BatteryHistory, set by the main window
        self.dev: Device | None = None
        self.pages: dict[str, QWidget] = {}
        self.navs: dict[str, QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.top = QFrame()
        self.top.setObjectName("topbar")
        self.top_lay = QHBoxLayout(self.top)
        self.top_lay.setContentsMargins(16, 10, 20, 10)
        root.addWidget(self.top)
        body = QHBoxLayout()
        body.setSpacing(0)
        root.addLayout(body, 1)
        self.side = QFrame()
        self.side.setObjectName("sidebar")
        self.side.setFixedWidth(176)
        self.side_lay = QVBoxLayout(self.side)
        self.side_lay.setContentsMargins(10, 12, 10, 12)
        self.side_lay.setSpacing(4)
        body.addWidget(self.side)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.group = QButtonGroup(self)
        self.bg = Background(self)
        self.bg.finished.connect(self._loaded)
        self._gen = 0                       # bumped per device so late results are ignored
        self._pending: dict[str, QWidget] = {}

    # -- construction
    def _clear(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                w = it.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                self._clear(it.layout())

    def _sections(self, dev: Device) -> list[str]:
        out = []
        if dev.has("buttons") and dev.kind in ("mouse", "trackball", "touchpad", "presenter"):
            out.append("buttons")
        if dev.has("dpi") or dev.has("smartshift") or dev.has("report_rate") or dev.has("wheel") or dev.has("thumbwheel"):
            out.append("pointer")
        if dev.has("profiles"):
            out.append("profiles")
        if dev.has("keylight") or dev.has("zones") or dev.has("backlight"):
            out.append("lighting")
        if dev.has("webcam"):
            out.append("webcam")
        out.append("device")
        return out

    def show_device(self, dev: Device) -> None:
        self.dev = dev
        self._gen += 1
        self._pending.clear()
        self.pages.clear()
        self.navs.clear()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        for b in self.group.buttons():
            self.group.removeButton(b)
        self._clear(self.top_lay)
        self._clear(self.side_lay)

        back = QPushButton(" " + tr("Devices"))
        back.setObjectName("flat")
        back.setIcon(icons.icon("back", theme.ICON, 16))
        back.clicked.connect(self.back)
        self.top_lay.addWidget(back)
        name = QLabel(dev.name)
        name.setObjectName("name")
        name.setStyleSheet("font-size:17px;")
        self.top_lay.addWidget(name)
        self.top_lay.addStretch(1)
        b = dev.battery
        if dev.online and b is not None and b.percent is not None:
            ic = QLabel()
            ic.setPixmap(icons.battery(b.percent, b.charging, theme.battery_color(b.percent, b.charging)))
            self.top_lay.addWidget(ic)
            self.top_lay.addWidget(QLabel(f"{b.percent}%" + (" ⚡" if b.charging else "")))
        self.top_lay.addWidget(status_pill(dev))

        keys = self._sections(dev)
        for key in keys:
            label, glyph = self.SECTIONS[key]
            if key == "pointer" and not is_pointing(dev):
                label = N_("Performance")                 # e.g. a keyboard: only the report rate
            nav = QPushButton(" " + tr(label))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            nav.setIcon(icons.icon(glyph, theme.ICON_DIM, 16))
            nav.clicked.connect(lambda _=False, k=key: self._go(k))
            self.group.addButton(nav)
            self.side_lay.addWidget(nav)
            self.navs[key] = nav
        self.side_lay.addStretch(1)
        self._go(keys[0])

    def _build(self, key: str) -> QWidget:
        dev = self.dev
        if key == "buttons":
            return ButtonsPage(dev, self.images, self.settings)
        if key == "pointer":
            return _scroll(PointerCard(dev, self.settings, getattr(self.settings, "engine", None)))
        if key == "profiles":
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(28, 22, 28, 22)
            lay.addWidget(ProfilesCard(dev), 1)
            return page
        if key == "lighting":
            return _scroll(LightingCard(dev, self.cfg))
        if key == "webcam":
            return CameraPage(dev, self.cfg)
        extra = [ReceiverCard(dev)] if "receiver" in dev.caps else []
        page = DetailsPage(dev, self.cfg, extra, self.history)
        page.settings_requested.connect(self.settings_requested)
        page.remove_requested.connect(self.remove_requested)
        page.picture_requested.connect(self.picture_requested)
        return page

    def _go(self, key: str) -> None:
        """Show a section; its device reads run in the background behind a 'Loading…' page."""
        self.navs[key].setChecked(True)
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            return
        ph = QLabel(tr("Loading…"))
        ph.setObjectName("muted")
        ph.setAlignment(Qt.AlignCenter)
        self.pages[key] = ph
        self._pending[key] = ph
        self.stack.addWidget(ph)
        self.stack.setCurrentWidget(ph)
        dev, reads = self.dev, SECTION_READS.get(key, [])
        self.bg.run((self._gen, key), lambda: dev.prefetch(reads))

    def _loaded(self, token, _err) -> None:
        gen, key = token
        ph = self._pending.pop(key, None) if gen == self._gen else None
        if ph is not None:
            self._swap(key, ph)

    def _swap(self, key: str, placeholder: QWidget) -> None:
        page = self._build(key)
        was_current = self.stack.currentWidget() is placeholder
        idx = self.stack.indexOf(placeholder)
        self.stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stack.insertWidget(idx, page)
        self.pages[key] = page
        if was_current:
            self.stack.setCurrentWidget(page)

    def ensure(self, key: str) -> QWidget:
        """Build a section right now (synchronously); used by tests and scripts."""
        ph = self._pending.pop(key, None)
        if ph is None and key not in self.pages:
            self.pages[key] = ph = QLabel()
            self.stack.addWidget(ph)
        if ph is not None:
            self.dev.prefetch(SECTION_READS.get(key, []))
            self._swap(key, ph)
        self.navs[key].setChecked(True)
        return self.pages[key]

    def images_ready(self) -> None:
        page = self.pages.get("buttons")
        if isinstance(page, ButtonsPage):
            page.images_ready()
