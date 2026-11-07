# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Feature cards shown on the device page."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QWidget
)

from ..core import buttons as B
from ..core.config import Config
from ..core.device import Device, profiles_to_json
from ..core.types import ButtonBinding, Profile
from ..hidpp.onboard import parse_profile
from ..core.settings import DeviceSettings
from ..i18n import N_, tr
from .keyboard_widget import KeyboardWidget, detect_form
from .widgets import Card, ColorButton, FlowLayout, Switch, attempt, guarded, muted
from .widgets import debounced as _debounced


def _slider(lo, hi, val, step=1) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    return s


def _zone_label(name: str) -> str:
    if name.startswith("Zone "):
        return tr("Zone") + name[4:]
    return tr(name)


# ----------------------------------------------------------------- Pointer
def is_pointing(dev: Device) -> bool:
    """True when the section is about a pointer (DPI, SmartShift, wheel); a keyboard only has the report rate."""
    return dev.has("dpi") or dev.has("smartshift") or dev.has("wheel")


class PointerCard(Card):
    def __init__(self, dev: Device, settings=None):
        super().__init__(N_("Pointer") if is_pointing(dev) else N_("Performance"))
        self.dev = dev
        self.settings = settings
        grid = QGridLayout()
        self.body.addLayout(grid)
        r = 0
        if dev.has("dpi"):
            info = guarded(self, dev.dpi, quiet=True)
            if info:
                self.dpi_label = QLabel(f"{info.current} DPI")
                self.dpi_label.setObjectName("h2")
                vals = info.values
                self.vals = vals
                idx = min(range(len(vals)), key=lambda i: abs(vals[i] - info.current))
                self.dpi_slider = _slider(0, len(vals) - 1, idx)
                self.dpi_slider.valueChanged.connect(
                    lambda i: self.dpi_label.setText(f"{vals[i]} DPI"))
                _debounced(self.dpi_slider, lambda i: self._set_dpi(vals[i]))
                grid.addWidget(QLabel(tr("DPI")), r, 0)
                grid.addWidget(self.dpi_slider, r, 1)
                grid.addWidget(self.dpi_label, r, 2)
                r += 1
                presets = QHBoxLayout()
                for v in (400, 800, 1600, 3200):
                    b = QPushButton(str(v))
                    b.setObjectName("flat")
                    b.clicked.connect(lambda _=False, v=v: self._preset(v))
                    presets.addWidget(b)
                presets.addStretch()
                grid.addLayout(presets, r, 1, 1, 2)
                r += 1
        if dev.has("report_rate"):
            res = guarded(self, dev.report_rate, quiet=True)
            if res:
                cur, opts = res
                cb = QComboBox()
                cb.addItems(opts)
                cb.setCurrentText(cur)
                cb.currentTextChanged.connect(lambda t: guarded(self, dev.set_report_rate, t))
                grid.addWidget(QLabel(tr("Report rate")), r, 0)
                grid.addWidget(cb, r, 1, 1, 2)
                r += 1
        if dev.has("smartshift"):
            res = guarded(self, dev.smartshift, quiet=True)
            if res:
                ratchet, thr = res
                chk = Switch(tr("Ratchet"))
                chk.setChecked(ratchet)
                sl = _slider(1, 255, min(max(thr, 1), 255))
                sl.setEnabled(ratchet)
                chk.toggled.connect(sl.setEnabled)
                send = lambda *_: self._set_smartshift(chk.isChecked(), sl.value())
                chk.toggled.connect(send)
                _debounced(sl, send)
                grid.addWidget(QLabel(tr("SmartShift")), r, 0)
                grid.addWidget(chk, r, 1)
                r += 1
                grid.addWidget(QLabel(tr("Torque / threshold")), r, 0)
                grid.addWidget(sl, r, 1, 1, 2)
        if dev.has("wheel"):
            res = guarded(self, dev.wheel, quiet=True)
            if res is not None:
                r += 1
                hires, inv = Switch(tr("Smooth (high-resolution) scrolling")), Switch(tr("Invert scrolling direction"))
                hires.setChecked(res[0])
                inv.setChecked(res[1])
                send = lambda *_: self._set_wheel(hires.isChecked(), inv.isChecked())
                hires.toggled.connect(send)
                inv.toggled.connect(send)
                grid.addWidget(QLabel(tr("Wheel")), r, 0)
                grid.addWidget(hires, r, 1, 1, 2)
                grid.addWidget(inv, r + 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)

    def _set_wheel(self, hires: bool, invert: bool) -> None:
        if attempt(self, self.dev.set_wheel, hires, invert) and self.settings:
            self.settings.save(self.dev, "wheel", [hires, invert])

    def _set_dpi(self, v: int) -> None:
        if attempt(self, self.dev.set_dpi, v) and self.settings:
            self.settings.save(self.dev, "dpi", v)

    def _set_smartshift(self, ratchet: bool, threshold: int) -> None:
        if attempt(self, self.dev.set_smartshift, ratchet, threshold) and self.settings:
            self.settings.save(self.dev, "smartshift", [ratchet, threshold])

    def _preset(self, v: int) -> None:
        i = min(range(len(self.vals)), key=lambda k: abs(self.vals[k] - v))
        self.dpi_slider.setValue(i)


# ----------------------------------------------------------------- Buttons
class ButtonsCard(Card):
    def __init__(self, dev: Device):
        super().__init__(N_("Buttons"))
        self.dev = dev
        ctrls = guarded(self, dev.controls, quiet=True) or []
        self.ctrls = ctrls
        t = QTableWidget(len(ctrls), 2)
        t.setHorizontalHeaderLabels([tr("Button"), tr("Remap to")])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setSelectionMode(QTableWidget.NoSelection)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setMinimumHeight(min(40 + 38 * len(ctrls), 340))
        for i, c in enumerate(ctrls):
            t.setItem(i, 0, QTableWidgetItem(tr(c.name)))
            if c.reprogrammable:
                cb = QComboBox()
                cb.addItem(tr("Default"), 0)
                for o in ctrls:
                    if o.cid != c.cid and o.reprogrammable:
                        cb.addItem(tr(o.name), o.cid)
                cb.setCurrentIndex(max(0, cb.findData(c.remap)))
                cb.activated.connect(lambda _i, cb=cb, c=c:
                                     guarded(self, dev.remap_control, c.cid, cb.currentData()))
                t.setCellWidget(i, 1, cb)
            else:
                t.setItem(i, 1, QTableWidgetItem("—"))
        self.body.addWidget(t)
        self.body.addWidget(muted(tr("Remaps last until the device is switched off.")))


# ---------------------------------------------------------------- Profiles
class ButtonEditor(QWidget):
    """Edits one 4-byte button binding."""

    def __init__(self, raw: bytes):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        self.kind.addItems([tr(x) for x in (N_("Mouse"), N_("Key"), N_("Media"), N_("Special"), N_("Disabled"), N_("Raw"))])
        self.value = QComboBox()
        self.mods = [Switch(n) for n, _ in B.MODIFIERS]
        lay.addWidget(self.kind)
        lay.addWidget(self.value, 1)
        for m in self.mods:
            lay.addWidget(m)
        self._raw = raw
        self.kind.currentIndexChanged.connect(self._fill)
        self._load(raw)

    def _load(self, raw: bytes) -> None:
        t = raw[0] if raw else 0xFF
        k = {0x80: {1: 0, 2: 1, 3: 2}.get(raw[1], 5), 0x90: 3, 0xFF: 4}.get(t, 5)
        if t == 0x80 and raw[1] not in (1, 2, 3):
            k = 5
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(k)
        self.kind.blockSignals(False)
        self._fill()
        if k == 0:
            self.value.setCurrentIndex(max(0, self.value.findData((raw[2] << 8) | raw[3])))
        elif k == 1:
            self.value.setCurrentIndex(max(0, self.value.findData(raw[3])))
            for (_, bit), m in zip(B.MODIFIERS, self.mods):
                m.setChecked(bool(raw[2] & bit))
        elif k == 2:
            self.value.setCurrentIndex(max(0, self.value.findData((raw[2] << 8) | raw[3])))
        elif k == 3:
            self.value.setCurrentIndex(max(0, self.value.findData(raw[1])))

    def _fill(self) -> None:
        k = self.kind.currentIndex()
        self.value.clear()
        src = {0: B.MOUSE_BUTTONS, 1: B.KEYS, 2: B.CONSUMER, 3: B.SPECIAL}.get(k, {})
        for code, name in src.items():
            self.value.addItem(tr(name), code)
        self.value.setEnabled(k < 4)
        for m in self.mods:
            m.setVisible(k == 1)

    def raw(self) -> bytes:
        k = self.kind.currentIndex()
        v = self.value.currentData()
        if k == 0 and v is not None:
            return B.mouse_binding(v)
        if k == 1 and v is not None:
            mods = sum(bit for (_, bit), m in zip(B.MODIFIERS, self.mods) if m.isChecked())
            return B.key_binding(mods, v)
        if k == 2 and v is not None:
            return B.consumer_binding(v)
        if k == 3 and v is not None:
            return B.special_binding(v)
        if k == 4:
            return B.DISABLED
        return self._raw


class ProfilesCard(Card):
    def __init__(self, dev: Device):
        super().__init__(N_("Onboard profiles"), expand=True)
        self.dev = dev
        self.profiles: list[Profile] = []
        self.editors: list[ButtonEditor] = []
        self.active_sector: int | None = None      # sector of the profile the device is using
        self.onboard = False                       # False = host mode: the device ignores its stored profiles
        top = QHBoxLayout()
        self.mode = Switch(tr("Use onboard profiles"))
        top.addWidget(self.mode)
        top.addStretch()
        self.combo = QComboBox()
        top.addWidget(QLabel(tr("Profile")))
        top.addWidget(self.combo)
        self.use_btn = QPushButton(tr("Use this profile"))
        self.use_btn.setToolTip(tr("Make the device use the profile selected here (it is stored in the device)"))
        self.use_btn.clicked.connect(self._activate)
        top.addWidget(self.use_btn)
        self.body.addLayout(top)

        form = QGridLayout()
        self.name = QLineEdit()
        self.color = ColorButton()
        self.rate = QComboBox()
        self.rate.addItems(["1", "2", "4", "8"])
        self.dpis = [QSpinBox() for _ in range(5)]
        for s in self.dpis:
            s.setRange(100, 32000)
            s.setSingleStep(50)
        self.def_lvl, self.shift_lvl = QSpinBox(), QSpinBox()
        for s in (self.def_lvl, self.shift_lvl):
            s.setRange(0, 4)
        form.addWidget(QLabel(tr("Name")), 0, 0)
        form.addWidget(self.name, 0, 1, 1, 3)
        form.addWidget(QLabel(tr("Color")), 0, 4)
        form.addWidget(self.color, 0, 5)
        form.addWidget(QLabel(tr("Report rate") + " (ms)"), 1, 0)
        form.addWidget(self.rate, 1, 1)
        lab_def, lab_shift, lab_dpi = QLabel(tr("Default level")), QLabel(tr("Shift level")), QLabel(tr("DPI levels"))
        form.addWidget(lab_def, 1, 2)
        form.addWidget(self.def_lvl, 1, 3)
        form.addWidget(lab_shift, 1, 4)
        form.addWidget(self.shift_lvl, 1, 5)
        form.addWidget(lab_dpi, 2, 0)
        for i, s in enumerate(self.dpis):
            form.addWidget(s, 2, 1 + i)
        # keyboards and other devices without a sensor have no DPI levels: do not show them
        self.dpi_widgets = [lab_def, self.def_lvl, lab_shift, self.shift_lvl, lab_dpi, *self.dpis]
        if not dev.has("dpi"):
            for w in self.dpi_widgets:
                w.hide()
        self.body.addLayout(form)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Button"), tr("Action")])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(200)
        self.body.addWidget(self.table, 1)              # takes the free height instead of scrolling in a small box

        row = QHBoxLayout()
        for text, fn, primary in ((tr("Save to device"), self._save, True),
                                  (tr("Reload"), self.load, False),
                                  (tr("Export…"), self._export, False),
                                  (tr("Import…"), self._import, False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        self.body.addLayout(row)
        self.combo.currentIndexChanged.connect(self._show)
        self.mode.toggled.connect(self._mode)
        self.load()

    def load(self, keep: int = 0) -> None:
        profs = guarded(self, self.dev.profiles)
        if profs is None:
            return
        self.profiles = profs
        self.active_sector = guarded(self, self.dev.active_profile, quiet=True)
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in profs:
            mark = "● " if p.sector == self.active_sector else ""      # ● = the profile the device is using
            col = self._profile_color(p) if self.dev.has("keylight") else None
            if self.dev.has("keylight"):          # keyboards: show the fixed lighting colour stored in the profile
                pm = QPixmap(14, 14)
                pm.fill(QColor(*col) if col else Qt.transparent)
                self.combo.addItem(QIcon(pm), f"{mark}{p.index + 1}: {p.name}" + ("" if col else f"  ({tr('effect')})"))
            else:
                self.combo.addItem(f"{mark}{p.index + 1}: {p.name}")
        self.combo.setCurrentIndex(min(keep, max(len(profs) - 1, 0)))
        self.combo.blockSignals(False)
        host = guarded(self, self.dev.profile_host_mode, quiet=True)
        self.onboard = host is False
        self.mode.blockSignals(True)
        self.mode.setChecked(self.onboard)
        self.mode.blockSignals(False)
        if profs:
            self._show(min(keep, len(profs) - 1))

    @staticmethod
    def _profile_color(p):
        from ..hidpp.onboard import fixed_color, lighting_records
        return next((c for c in map(fixed_color, lighting_records(p.raw)) if c), None)

    def _refresh_use_button(self) -> None:
        i = self.combo.currentIndex()
        sel = self.profiles[i].sector if 0 <= i < len(self.profiles) else None
        can = bool(self.onboard) and self.active_sector is not None and sel is not None and sel != self.active_sector
        self.use_btn.setEnabled(can)
        self.use_btn.setVisible(self.active_sector is not None)

    def _activate(self) -> None:
        i = self.combo.currentIndex()
        if 0 <= i < len(self.profiles) and attempt(self, self.dev.set_active_profile, self.profiles[i].sector):
            self.load(keep=i)

    def _show(self, i: int) -> None:
        if not 0 <= i < len(self.profiles):
            return
        p = self.profiles[i]
        self.name.setText(p.name)
        self.color.setColor(p.color)
        self.rate.setCurrentText(str(p.report_rate_ms))
        for s, v in zip(self.dpis, p.dpis):
            s.setValue(v)
        self.def_lvl.setValue(p.dpi_default)
        self.shift_lvl.setValue(p.dpi_shift)
        self._refresh_use_button()
        self.table.setRowCount(len(p.buttons))
        self.editors = []
        for r, b in enumerate(p.buttons):
            if self.dev.kind in ("keyboard", "numpad"):
                label = f"{tr('Key')} {r + 1}"
            else:
                label = f"G{r + 1}" if r > 4 else f"{tr('Button')} {r + 1}"
            self.table.setItem(r, 0, QTableWidgetItem(label))
            ed = ButtonEditor(b.raw)
            self.editors.append(ed)
            self.table.setCellWidget(r, 1, ed)
            self.table.setRowHeight(r, 38)

    def _collect(self) -> Profile | None:
        i = self.combo.currentIndex()
        if i < 0:
            return None
        p = self.profiles[i]
        p.name = self.name.text() or p.name
        p.color = self.color.color()
        p.report_rate_ms = int(self.rate.currentText())
        p.dpis = [s.value() for s in self.dpis]
        p.dpi_default, p.dpi_shift = self.def_lvl.value(), self.shift_lvl.value()
        p.buttons = [ButtonBinding(e.raw()) for e in self.editors]
        return p

    def _save(self) -> None:
        p = self._collect()
        if p is None:
            return
        if QMessageBox.question(self, tr("Onboard profiles"),
                                tr("Write profile to the device?") + "\n" +
                                tr("A backup of the current profiles is saved first.")
                                ) != QMessageBox.Yes:
            return
        if attempt(self, self.dev.write_profile, p):
            QMessageBox.information(self, tr("Saved"), f"{tr('Backup saved to')} {self.dev.last_backup}")
        self.load()

    def _mode(self, onboard: bool) -> None:
        guarded(self, self.dev.set_profile_host_mode, not onboard)
        self.onboard = onboard
        self._refresh_use_button()

    def _export(self) -> None:
        self._collect()
        f, _ = QFileDialog.getSaveFileName(self, tr("Export…"), "profiles.json", "JSON (*.json)")
        if f and self.profiles:
            Path(f).write_text(profiles_to_json(self.profiles))

    def _import(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, tr("Import…"), "", "JSON (*.json)")
        if not f:
            return
        try:
            data = json.loads(Path(f).read_text())
            for entry, p in zip(data, self.profiles):
                prof = parse_profile(p.index, p.sector, p.enabled,
                                     bytes.fromhex(entry["raw"]))
                self.profiles[p.index] = prof
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self._show(self.combo.currentIndex())


# ---------------------------------------------------------------- Lighting
class LightingCard(Card):
    def __init__(self, dev: Device, cfg: Config):
        super().__init__(N_("Lighting"))
        self.dev, self.cfg = dev, cfg
        if dev.has("keylight"):
            self.kb = KeyboardWidget()
            self.settings = DeviceSettings(cfg)
            # what the keyboard says it has (verified: a TKL reports no numpad keys, an ISO one reports the '<>' key)
            self.presence = guarded(self, dev.key_presence, quiet=True)
            self.auto_layout, self.auto_numpad = detect_form(self.presence, dev.name)
            rec = self.settings._rec(dev)
            self.layout_box, self.size_box = QComboBox(), QComboBox()
            self.layout_box.addItem(f"{tr('Automatic')} ({self.auto_layout.upper()})", "auto")
            self.layout_box.addItem("ANSI", "ansi")
            self.layout_box.addItem("ISO", "iso")
            self.size_box.addItem(f"{tr('Automatic')}: {tr('Full size') if self.auto_numpad else tr('TKL (no numpad)')}", "auto")
            self.size_box.addItem(tr("Full size"), "full")
            self.size_box.addItem(tr("TKL (no numpad)"), "tkl")
            self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(rec.get("key_layout", "auto"))))
            self.size_box.setCurrentIndex(max(0, self.size_box.findData(rec.get("key_size", "auto"))))
            self._relayout()
            self.layout_box.activated.connect(self._layout_chosen)
            self.size_box.activated.connect(self._layout_chosen)
            top = QHBoxLayout()
            top.addWidget(QLabel(tr("Key layout")))
            top.addWidget(self.layout_box)
            top.addWidget(QLabel(tr("Key size")))
            top.addWidget(self.size_box)
            top.addStretch()
            self.body.addLayout(top)
            self.body.addWidget(self.kb)
            row = FlowLayout()
            self.color = ColorButton((0, 200, 255))
            row.addWidget(self.color)
            for text, fn in ((tr("Apply to selected"), lambda: self._apply(True)),
                             (tr("Apply to all"), lambda: self._apply(False)),
                             (tr("Rainbow"), self._rainbow),
                             (tr("Select all"), self.kb.select_all),
                             *(((tr("Load colour from profile…"), self._load_from_profile),
                                (tr("Save colour to profile…"), self._save_to_profile))
                               if dev.has("profiles") else ()),
                             *(((tr("Reset colours"), self._reset),) if dev.has("profiles") else ()),
                             (tr("Save preset…"), self._save_preset),
                             (tr("Load preset…"), self._load_preset)):
                b = QPushButton(text)
                b.clicked.connect(fn)
                row.addWidget(b)
            self.copy_btn = QPushButton(tr("Copy colour from a key"))
            self.copy_btn.setCheckable(True)
            self.copy_btn.setToolTip(tr("Click this, then a key: its colour goes to the picker. "
                                        "Right-clicking a key does the same."))
            self.copy_btn.toggled.connect(self._copy_mode)
            row.addWidget(self.copy_btn)
            self.kb.colorPicked.connect(self._color_copied)
            self.body.addLayout(row)
            self._build_effects()
            stored = self.settings._rec(dev).get("key_colors") or {}
            self.kb.colors.update({int(k): tuple(v) for k, v in stored.items()})
        if dev.has("zones"):
            zs = guarded(self, dev.zones, quiet=True) or []
            for z in zs:
                row = QHBoxLayout()
                row.addWidget(QLabel(f"{tr('Zones')}: {_zone_label(z.name)}"))
                cb = ColorButton((255, 255, 255))
                cb.colorChanged.connect(lambda rgb, z=z: guarded(self, dev.set_zone_color, z.index, rgb))
                row.addWidget(cb)
                row.addStretch()
                self.body.addLayout(row)
        if dev.has("backlight"):
            res = guarded(self, dev.backlight, quiet=True)
            if res:
                en, lvl, mx = res
                row = QHBoxLayout()
                chk = Switch(tr("Backlight"))
                chk.setChecked(en)
                sl = _slider(0, mx, lvl)
                send = lambda *_: guarded(self, dev.set_backlight, chk.isChecked(), sl.value())
                chk.toggled.connect(send)
                _debounced(sl, send)
                row.addWidget(chk)
                row.addWidget(QLabel(tr("Level")))
                row.addWidget(sl, 1)
                self.body.addLayout(row)

    def _copy_mode(self, on: bool) -> None:
        self.kb.pick_mode = on
        self.kb.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def _color_copied(self, rgb) -> None:
        """A key's colour became the picker colour: ready to 'Apply to selected' on other keys."""
        self.color.setColor(tuple(rgb))
        self.copy_btn.setChecked(False)

    # -- animated effects (software, played while pyLogi runs)
    def _build_effects(self) -> None:
        from ..core import lighting_fx as fx
        self.fx_box = QComboBox()
        self.fx_box.addItem(tr("Static colours"), "")
        for eid, (label, _keys, _color) in fx.EFFECTS.items():
            self.fx_box.addItem(tr(label), eid)
        self.fx_speed = _slider(1, 10, 5)
        self.fx_button = QPushButton(tr("Play effect"))
        self.fx_info = muted("")
        rec = self.settings._rec(self.dev).get("fx") or {}
        if rec.get("effect"):
            self.fx_box.setCurrentIndex(max(0, self.fx_box.findData(rec["effect"])))
            self.fx_speed.setValue(int(rec.get("speed", 5)))
            if rec.get("color"):
                self.color.setColor(tuple(rec["color"]))
        line = QHBoxLayout()
        line.addWidget(QLabel(tr("Effect")))
        line.addWidget(self.fx_box)
        line.addWidget(QLabel(tr("Speed")))
        line.addWidget(self.fx_speed, 1)
        line.addWidget(self.fx_button)
        self.body.addLayout(line)
        self.body.addWidget(self.fx_info)
        self.fx_box.activated.connect(lambda _i: self._fx_changed())
        self.fx_button.clicked.connect(self._fx_toggle)
        self.fx_speed.valueChanged.connect(lambda _v: self._fx_live())
        self.color.colorChanged.connect(lambda _c: self._fx_live())
        self._fx_changed(update_only=True)

    def _fx_live(self) -> None:
        """Speed and colour changes apply to the effect that is playing, and are remembered."""
        from ..core import lighting_fx as fx
        run = fx.HUB.get(self.dev)
        if run is None:
            return
        run.speed, run.color = self.fx_speed.value(), tuple(self.color.color())
        self.settings.save(self.dev, "fx", {"effect": run.effect, "color": list(run.color), "speed": run.speed})

    def _fx_changed(self, update_only: bool = False) -> None:
        from ..core import lighting_fx as fx
        eid = self.fx_box.currentData()
        needs_keys = bool(eid) and fx.EFFECTS[eid][1]
        self.fx_button.setEnabled(bool(eid))
        playing = fx.HUB.get(self.dev) is not None
        self.fx_button.setText(tr("Stop effect") if playing else tr("Play effect"))
        self.fx_info.setText(tr("Needs read access to /dev/input (the 'input' group): only the key code is used "
                                "to light the key.") if needs_keys else
                             tr("Effects are drawn by pyLogi, so it has to keep running (it can sit in the tray).")
                             if eid else "")
        if not update_only and not eid and playing:
            self._fx_stop()

    def _fx_toggle(self) -> None:
        from ..core import lighting_fx as fx
        if fx.HUB.get(self.dev) is not None:
            self._fx_stop()
        else:
            self._fx_play()

    def _fx_play(self) -> None:
        from ..core import lighting_fx as fx
        eid = self.fx_box.currentData()
        if not eid:
            return
        color, speed = self.color.color(), self.fx_speed.value()
        rec = self.settings._rec(self.dev)
        run = fx.HUB.start(self.dev, eid, fx.positions_from_rows(self.kb.rows), color, speed)
        if run.error and not run.running:
            QMessageBox.warning(self, tr("Lighting"), tr("The effect could not start:") + "\n" + run.error)
            return
        self.settings.save(self.dev, "fx", {"effect": eid, "color": list(color), "speed": speed})
        self._fx_changed(update_only=True)

    def _fx_stop(self) -> None:
        """Stop the effect and put the saved colours back (or the keyboard's own lighting)."""
        from ..core import lighting_fx as fx
        fx.HUB.stop(self.dev)
        self.settings.save(self.dev, "fx", None)
        stored = self.settings._rec(self.dev).get("key_colors") or {}
        if stored:
            attempt(self, self.dev.set_key_colors, {int(u): tuple(c) for u, c in stored.items()})
        elif self.dev.has("profiles"):
            guarded(self, self.dev.reset_lighting, quiet=True)
        self._fx_changed(update_only=True)

    def _relayout(self) -> None:
        """Layout and size: the detected ones unless the user chose otherwise (then every key is drawn)."""
        layout, size = self.layout_box.currentData(), self.size_box.currentData()
        manual = layout != "auto" or size != "auto"
        self.kb.set_layout(self.auto_layout if layout == "auto" else layout,
                           self.auto_numpad if size == "auto" else size == "full",
                           None if manual else self.presence)

    def _layout_chosen(self, _i: int) -> None:
        self._relayout()
        self.settings.save(self.dev, "key_layout", self.layout_box.currentData())
        self.settings.save(self.dev, "key_size", self.size_box.currentData())

    def _push(self, colors) -> None:
        from ..core import lighting_fx as fx
        if fx.HUB.get(self.dev) is not None:                 # a static colour replaces a running effect
            fx.HUB.stop(self.dev)
            self.settings.save(self.dev, "fx", None)
            self._fx_changed(update_only=True)
        if attempt(self, self.dev.set_key_colors, colors):
            # remembered per device and written again every time the keyboard reconnects
            self.settings.save(self.dev, "key_colors", {str(k): list(v) for k, v in self.kb.colors.items()})

    def _apply(self, only_selected: bool) -> None:
        self._push(self.kb.apply_color(self.color.color(), only_selected))

    def _pick_profile(self, title: str, show_colors: bool = False):
        profs = [p for p in (guarded(self, self.dev.profiles, quiet=True) or []) if p.enabled]
        if not profs:
            return None
        names = [f"{p.index + 1}. {p.name}" for p in profs]
        if show_colors:        # tell which profiles hold a fixed colour (the others use a lighting effect)
            for i, p in enumerate(profs):
                c = guarded(self, self.dev.profile_color, p.sector, quiet=True)
                names[i] += f"  ({'#%02X%02X%02X' % tuple(c)})" if c else f"  ({tr('effect')})"
        choice, ok = QInputDialog.getItem(self, title, tr("Profile:"), names, 0, False)
        return profs[names.index(choice)] if ok else None

    def _load_from_profile(self) -> None:
        """Read the fixed lighting colour of an onboard profile and apply it to every key."""
        prof = self._pick_profile(tr("Load colour from profile…"), show_colors=True)
        if prof is None:
            return
        rgb = guarded(self, self.dev.profile_color, prof.sector)
        if rgb is None:
            QMessageBox.information(self, tr("Load colour from profile…"),
                                    tr("This profile uses a lighting effect, not a fixed colour."))
            return
        self.color.setColor(tuple(rgb))
        self._push(self.kb.apply_color(tuple(rgb), False))

    def _save_to_profile(self) -> None:
        """Store the picker colour as the fixed lighting colour of an onboard profile.

        The keyboard keeps one fixed colour per profile in its memory; individual
        per-key colours are not stored there (they stay software-controlled)."""
        prof = self._pick_profile(tr("Save colour to profile…"))
        if prof is None:
            return
        if QMessageBox.question(self, tr("Save colour to profile…"),
                                tr("Write the colour to the profile '{name}' in the keyboard's memory?").format(name=prof.name)
                                + "\n" + tr("A backup of the current profiles is saved first.")) != QMessageBox.Yes:
            return
        if attempt(self, self.dev.set_profile_color, prof.sector, self.color.color()):
            QMessageBox.information(self, tr("Saved"), f"{tr('Backup saved to')} {self.dev.last_backup}")

    def _reset(self) -> None:
        """Forget the colours pyLogi re-sends and let the keyboard show its own profile lighting again."""
        if attempt(self, self.dev.reset_lighting):
            self.settings.save(self.dev, "key_colors", {})
            self.kb.clear_colors()

    def _rainbow(self) -> None:
        self._push(self.kb.rainbow())

    def _save_preset(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, tr("Save preset…"), "keys.json", "JSON (*.json)")
        if f:
            Path(f).write_text(json.dumps({str(k): list(v) for k, v in self.kb.colors.items()}))

    def _load_preset(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, tr("Load preset…"), "", "JSON (*.json)")
        if not f:
            return
        try:
            cols = {int(k): tuple(v) for k, v in json.loads(Path(f).read_text()).items()}
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.kb.set_colors(cols)
        self._push(cols)


# ---------------------------------------------------------------- Receiver
class ReceiverCard(Card):
    def __init__(self, dev):
        super().__init__(N_("Receiver"))
        self.dev = dev
        self.body.addWidget(QLabel(f"<b>{dev.name}</b>"))
        self.body.addWidget(QLabel(f"{tr('Serial')}: {dev.info.serial or '—'}"))
        self.body.addWidget(QLabel(tr("Paired devices")))
        self.lst = QLabel("\n".join(dev.slot_names) or "—")
        self.body.addWidget(self.lst)
        row = QHBoxLayout()
        if dev.rx.can_pair:
            b = QPushButton(tr("Pair new device"))
            b.setObjectName("primary")
            b.clicked.connect(self._pair)
            row.addWidget(b)
            u = QPushButton(tr("Unpair"))
            u.clicked.connect(self._unpair)
            row.addWidget(u)
        row.addStretch()
        self.body.addLayout(row)
        if not dev.rx.can_pair:
            self.body.addWidget(muted(tr("Pair Bolt devices from the OS Bluetooth settings or Solaar.")))

    def _pair(self) -> None:
        if guarded(self, self.dev.open_pairing, 30) is None:
            pass
        QMessageBox.information(self, tr("Receiver"),
                                tr("Pairing is open for 30 seconds: switch the device on / press its connect button."))

    def _unpair(self) -> None:
        n, ok = QInputDialog.getInt(self, tr("Unpair"), tr("Paired devices"), 1, 1, 6)
        if ok:
            guarded(self, self.dev.unpair, n)
