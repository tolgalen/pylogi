# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Pointer / DPI card."""
from __future__ import annotations


from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton
)

from ...core.actions import THUMB_MODES
from ...core.device import Device
from ...i18n import N_, tr
from ..widgets import Card, Switch, attempt, guarded
from ..widgets import debounced as _debounced


from .common import _slider, is_pointing


class PointerCard(Card):
    def __init__(self, dev: Device, settings=None, engine=None):
        super().__init__(N_("Pointer") if is_pointing(dev) else N_("Performance"))
        self.dev = dev
        self.settings = settings
        self.engine = engine
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
        if dev.has("thumbwheel"):
            res = guarded(self, dev.thumb, quiet=True)
            if res is not None:
                r += 2
                mode, inverted = settings.thumb(dev) if settings else ("native", res[1])
                usable = engine is None or engine.injector.available
                self.thumb_mode = QComboBox()
                for key, label in THUMB_MODES:
                    if key == "native" or usable:
                        self.thumb_mode.addItem(tr(label), key)
                self.thumb_mode.setCurrentIndex(max(0, self.thumb_mode.findData(mode)))
                self.thumb_inv = Switch(tr("Invert thumb wheel direction"))
                self.thumb_inv.setChecked(inverted)
                self.thumb_mode.activated.connect(lambda _i: self._set_thumb())
                self.thumb_inv.toggled.connect(lambda _v: self._set_thumb())
                grid.addWidget(QLabel(tr("Thumb wheel")), r, 0)
                grid.addWidget(self.thumb_mode, r, 1, 1, 2)
                grid.addWidget(self.thumb_inv, r + 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)

    def _set_thumb(self) -> None:
        mode, inv = self.thumb_mode.currentData(), self.thumb_inv.isChecked()
        if self.settings is None:
            attempt(self, self.dev.set_thumb, mode != "native", inv)
            return
        self.settings.save_thumb(self.dev, mode, inv)
        engine = self.settings.engine
        attempt(self, engine.configure_thumb if engine is not None else self.dev.set_thumb,
                *((self.dev,) if engine is not None else (False, inv)))

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
