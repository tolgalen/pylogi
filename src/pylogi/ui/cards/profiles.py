# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Onboard profiles card."""
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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView
)

from ...core.device import Device, profiles_to_json
from ...core.types import ButtonBinding, Profile
from ...hidpp.onboard import parse_profile
from ...i18n import N_, tr
from ..widgets import Card, ColorButton, Switch, attempt, guarded


from .buttons import ButtonEditor


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
        from ...hidpp.onboard import fixed_color, lighting_records
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
            Path(f).write_text(profiles_to_json(self.profiles), encoding="utf-8")

    def _import(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, tr("Import…"), "", "JSON (*.json)")
        if not f:
            return
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
            for entry, p in zip(data, self.profiles):
                prof = parse_profile(p.index, p.sector, p.enabled,
                                     bytes.fromhex(entry["raw"]))
                self.profiles[p.index] = prof
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self._show(self.combo.currentIndex())


# ---------------------------------------------------------------- Lighting
