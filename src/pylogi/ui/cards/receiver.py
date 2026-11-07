# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Receiver card and Bolt pairing dialog."""
from __future__ import annotations


from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout
)

from ...i18n import N_, tr
from ..widgets import Card, guarded, muted




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
            self.body.addWidget(muted(tr("This receiver cannot pair new devices from pyLogi.")))

    def _pair(self) -> None:
        if self.dev.rx.family == "Bolt":
            BoltPairDialog(self, self.dev).exec()
            return
        if guarded(self, self.dev.open_pairing, 30) is None:
            pass
        QMessageBox.information(self, tr("Receiver"),
                                tr("Pairing is open for 30 seconds: switch the device on / press its connect button."))

    def _unpair(self) -> None:
        n, ok = QInputDialog.getInt(self, tr("Unpair"), tr("Paired devices"), 1, 1, 6)
        if ok:
            guarded(self, self.dev.unpair, n)


_BOLT_ERRORS = {"the device did not answer in time": N_("The device did not answer in time"),
                "pairing failed": N_("The receiver could not pair the device")}


class BoltPairDialog(QDialog):
    """Pair a device with a Bolt receiver: discovery, then the passkey that is entered on the device."""
    TIMEOUT = 30

    def __init__(self, parent, dev):
        super().__init__(parent)
        self.dev = dev
        self.setWindowTitle(tr("Pair new device"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.PlainText)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton(tr("Cancel"))
        self.button.clicked.connect(self.reject)
        row.addWidget(self.button)
        lay.addLayout(row)
        self.done = False
        self.elapsed = 0.0
        self.session = dev.bolt_begin(self.TIMEOUT)          # returns at once: the work happens on a thread
        self.text.setText(self._searching())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(300)

    @staticmethod
    def _searching() -> str:
        return (tr("Put the device in pairing mode: hold its Easy-Switch button for three seconds, until the LED blinks quickly.")
                + "\n\n" + tr("Searching for devices…"))

    def _tick(self) -> None:
        s = self.session
        self.elapsed += 0.3
        if s.new_slot is not None:
            return self._finish(tr("Device paired."), ok=True)
        if s.error:
            return self._finish(tr("Pairing failed") + f": {tr(_BOLT_ERRORS.get(s.error, s.error))}")
        if s.finished:
            return self._finish(tr("No device found. Check that it is in pairing mode and try again."))
        if s.passkey:
            if s.type_passkey:
                msg = tr("Type {code} on the device and then press Enter.").format(code=s.passkey)
            else:
                side = {"left": tr("left"), "right": tr("right")}
                steps = ", ".join(side[x] for x in s.passkey_steps())
                msg = tr("Click, in this order: {steps}. Then press the left and right buttons together.").format(steps=steps)
            self.text.setText(f"{s.name}\n\n{msg}")
        elif s.requested:
            self.text.setText(tr("Found {name}. Pairing…").format(name=s.name))
        elif self.elapsed > self.TIMEOUT + 8:
            self._finish(tr("No device found. Check that it is in pairing mode and try again."))

    def _finish(self, msg: str, ok: bool = False) -> None:
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.dev.bolt_end()
        self.text.setText(msg)
        self.button.setText(tr("Close"))
        self.button.clicked.disconnect()
        self.button.clicked.connect(self.accept if ok else self.reject)

    def done_pairing(self) -> None:
        if not self.done:
            self.done = True
            self.timer.stop()
            self.dev.bolt_end()

    def reject(self) -> None:
        self.done_pairing()
        super().reject()

    def accept(self) -> None:
        self.done_pairing()
        super().accept()
