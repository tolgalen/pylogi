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

from ...core import debuglog as _debuglog
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
        else:
            UnifyingPairDialog(self, self.dev).exec()
        self._refresh()

    def _refresh(self) -> None:
        self.lst.setText("\n".join(self.dev.slot_names) or "—")

    def _unpair(self) -> None:
        names = list(self.dev.slot_names)
        if not names:
            QMessageBox.information(self, tr("Unpair"), tr("No devices are paired with this receiver."))
            return
        choice, ok = QInputDialog.getItem(self, tr("Unpair"), tr("Device to unpair"), names, 0, False)
        if not ok:
            return
        if QMessageBox.question(self, tr("Unpair"), tr("Unpair {name}? It will have to be paired again to be used.")
                                .format(name=choice.split(":", 1)[-1].strip())) != QMessageBox.Yes:
            return
        guarded(self, self.dev.unpair, int(choice.split(":", 1)[0]))
        guarded(self, self.dev.refresh, True)
        self._refresh()


class UnifyingPairDialog(QDialog):
    """Pair a device with a Unifying / Lightspeed / Nano receiver: open the lock, then watch the paired list."""
    TIMEOUT = 30

    def __init__(self, parent, dev):
        super().__init__(parent)
        self.dev = dev
        self.setWindowTitle(tr("Pair new device"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        self.text = QLabel(tr("Switch the device off and on again (or press its connect button). "
                              "It is searched for during the next {seconds} seconds.").format(seconds=self.TIMEOUT)
                           + "\n\n" + tr("Searching for devices…"))
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.PlainText)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton(tr("Cancel"))
        self.button.clicked.connect(self.reject)
        row.addWidget(self.button)
        lay.addLayout(row)
        self.before = set(dev.slot_names)
        self.elapsed = 0.0
        self.done = False
        guarded(self, dev.open_pairing, self.TIMEOUT)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(2000)

    def _tick(self) -> None:
        self.elapsed += 2.0
        guarded(self, self.dev.refresh, True, quiet=True)
        new = [n for n in self.dev.slot_names if n not in self.before]
        if new:
            return self._finish(tr("Device paired.") + "\n" + new[0], ok=True)
        if self.elapsed >= self.TIMEOUT + 2:
            self._finish(tr("No device found. Check that it is in pairing mode and try again."))

    def _finish(self, msg: str, ok: bool = False) -> None:
        if self.done:
            return
        self.done = True
        self.timer.stop()
        if not ok:
            try:
                self.dev.rx.close_pairing()
            except Exception as e:
                _debuglog.log("error", f"closing the pairing lock: {e!r}")
        self.text.setText(msg)
        self.button.setText(tr("Close"))
        self.button.clicked.disconnect()
        self.button.clicked.connect(self.accept)

    def reject(self) -> None:
        if not self.done:
            self._finish("")
        super().reject()


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
