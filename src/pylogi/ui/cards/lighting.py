# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Lighting card."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton
)

from ...core.config import Config
from ...core.device import Device
from ...core.settings import DeviceSettings
from ...i18n import N_, tr
from ..keyboard_widget import KeyboardWidget, detect_form
from ..widgets import Card, ColorButton, FlowLayout, Switch, attempt, guarded, muted
from ..widgets import debounced as _debounced


from .common import _slider, _zone_label


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
        from ...core import lighting_fx as fx
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
        from ...core import lighting_fx as fx
        run = fx.HUB.get(self.dev)
        if run is None:
            return
        run.speed, run.color = self.fx_speed.value(), tuple(self.color.color())
        self.settings.save(self.dev, "fx", {"effect": run.effect, "color": list(run.color), "speed": run.speed})

    def _fx_changed(self, update_only: bool = False) -> None:
        from ...core import lighting_fx as fx
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
        from ...core import lighting_fx as fx
        if fx.HUB.get(self.dev) is not None:
            self._fx_stop()
        else:
            self._fx_play()

    def _fx_play(self) -> None:
        from ...core import lighting_fx as fx
        eid = self.fx_box.currentData()
        if not eid:
            return
        color, speed = self.color.color(), self.fx_speed.value()
        run = fx.HUB.start(self.dev, eid, fx.positions_from_rows(self.kb.rows), color, speed)
        if run.error and not run.running:
            QMessageBox.warning(self, tr("Lighting"), tr("The effect could not start:") + "\n" + run.error)
            return
        self.settings.save(self.dev, "fx", {"effect": eid, "color": list(color), "speed": speed})
        self._fx_changed(update_only=True)

    def _fx_stop(self) -> None:
        """Stop the effect and put the saved colours back (or the keyboard's own lighting)."""
        from ...core import lighting_fx as fx
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
        from ...core import lighting_fx as fx
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
