# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Mouse buttons card and its editor."""
from __future__ import annotations


from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QWidget
)

from ...core import buttons as B
from ...core.device import Device
from ...i18n import N_, tr
from ..widgets import Card, Switch, guarded, muted




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
