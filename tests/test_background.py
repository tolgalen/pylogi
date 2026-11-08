# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Compositor detectors, background service, command-line reads/toggles and receiver pairing dialogs."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pylogi import cli  # noqa: E402
from pylogi.core import actions as A  # noqa: E402
from pylogi.core import desktop  # noqa: E402


class CompositorDetectors(unittest.TestCase):
    def test_sway_finds_the_focused_leaf_of_the_tree(self):
        tree = {"focused": False, "nodes": [
            {"focused": False, "app_id": "foot", "nodes": []},
            {"focused": False, "nodes": [{"focused": True, "app_id": "Firefox", "nodes": []}]}],
            "floating_nodes": []}
        self.assertEqual(A.SwayDetector().parse(json.dumps(tree)), "Firefox")

    def test_sway_uses_the_x11_class_for_xwayland_windows(self):
        tree = {"nodes": [{"focused": True, "app_id": None, "window_properties": {"class": "Steam"}}]}
        self.assertEqual(A.SwayDetector().parse(json.dumps(tree)), "Steam")

    def test_hyprland_niri_and_kde(self):
        self.assertEqual(A.HyprlandDetector().parse('{"class": "kitty", "title": "x"}'), "kitty")
        self.assertEqual(A.NiriDetector().parse('{"app_id": "org.gnome.Nautilus"}'), "org.gnome.Nautilus")
        self.assertEqual(A.KdeDetector().parse("org.kde.dolphin\n"), "org.kde.dolphin")

    def test_current_is_lower_case_and_survives_garbage(self):
        d = A.HyprlandDetector()
        with mock.patch.object(A, "_run", return_value='{"class": "Kitty"}'):
            self.assertEqual(d.current(), "kitty")
        with mock.patch.object(A, "_run", return_value="not json"):
            self.assertIsNone(d.current())
        with mock.patch.object(A, "_run", return_value=None):
            self.assertIsNone(d.current())

    def test_a_detector_is_only_available_in_its_own_session(self):
        d = A.SwayDetector()
        with mock.patch.dict(os.environ, {"SWAYSOCK": "", "XDG_CURRENT_DESKTOP": "GNOME"}), \
                mock.patch.object(A.shutil, "which", return_value="/usr/bin/swaymsg"):
            self.assertFalse(d.available)
        d = A.SwayDetector()
        with mock.patch.dict(os.environ, {"SWAYSOCK": "/run/sway.sock"}), \
                mock.patch.object(A.shutil, "which", return_value="/usr/bin/swaymsg"), \
                mock.patch.object(A, "_run", return_value='{"nodes": []}'):
            self.assertTrue(d.available)

    def test_auto_detector_knows_every_backend(self):
        names = {type(b).__name__ for b in A.AutoDetector().backends}
        self.assertTrue({"GnomeDetector", "SwayDetector", "HyprlandDetector", "NiriDetector", "KdeDetector",
                         "X11Detector"} <= names)


class ServiceTests(unittest.TestCase):
    def test_unit_runs_minimised_and_restarts(self):
        unit = desktop.service_unit("/usr/bin/pylogi")
        self.assertIn("ExecStart=/usr/bin/pylogi --minimized --no-splash", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)

    def test_shipped_unit_matches_the_generated_one(self):
        shipped = (Path(__file__).resolve().parent.parent / "packaging" / "systemd" / "pylogi.service").read_text()
        self.assertEqual(shipped, desktop.service_unit("/usr/bin/pylogi"))

    def test_enable_and_disable(self):
        calls = []

        def fake(*args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}), \
                mock.patch.object(desktop, "service_available", return_value=True), \
                mock.patch.object(desktop, "_systemctl", side_effect=fake):
            desktop.set_autostart(True)
            self.assertEqual(desktop.set_service(True), "enabled")
            self.assertTrue(desktop.service_path().exists())
            self.assertFalse(desktop.autostart_enabled())              # the service replaces the autostart entry
            self.assertIn(("enable", "--now", "pylogi.service"), calls)
            self.assertEqual(desktop.set_service(False), "disabled")
            self.assertFalse(desktop.service_path().exists())
            self.assertIn(("disable", "--now", "pylogi.service"), calls)

    def test_a_failing_systemctl_is_reported(self):
        bad = SimpleNamespace(returncode=1, stdout="", stderr="Failed to connect to bus")
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}), \
                mock.patch.object(desktop, "service_available", return_value=True), \
                mock.patch.object(desktop, "_systemctl", return_value=bad):
            with self.assertRaises(OSError):
                desktop.set_service(True)

    def test_cli_option(self):
        from pylogi.app import build_parser
        self.assertEqual(build_parser().parse_args(["--service", "on"]).service, "on")


class FakeMouse:
    name, kind, connection, online, uid = "MX Test", "mouse", "bluetooth", True, "x:1"
    caps = {"dpi", "wheel", "thumbwheel", "fnlock"}

    def __init__(self):
        self.wheel_state = (False, False)
        self.thumb_inv = False
        self.fn = False
        self.battery = SimpleNamespace(percent=80, charging=False)
        self.info = SimpleNamespace(firmware="RQR 24.01.B0012")
        self.rate = "1 ms"

    def device_key(self): return "x:1"
    def dpi(self): return SimpleNamespace(current=1600, default=1000)
    def wheel(self): return self.wheel_state
    def set_wheel(self, hires, inv): self.wheel_state = (hires, inv)
    def thumb(self): return (False, self.thumb_inv)
    def set_thumb(self, diverted=None, invert=None): self.thumb_inv = self.thumb_inv if invert is None else invert
    def fn_lock(self): return self.fn
    def set_fn_lock(self, on): self.fn = on
    def report_rate(self): return (self.rate, ["1 ms", "2 ms"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.m = FakeMouse()
        self.settings = mock.Mock()

    def test_get_only_the_named_settings(self):
        out = cli.get(self.m, ["dpi", "battery", "firmware", "wheel-invert", "report-rate"])
        self.assertEqual(out, {"dpi": 1600, "battery": 80, "firmware": "RQR 24.01.B0012", "wheel-invert": False,
                               "report-rate": "1 ms"})

    def test_get_unknown_name_lists_the_valid_ones(self):
        with self.assertRaises(SystemExit) as e:
            cli.get(self.m, ["nonsense"])
        self.assertIn("dpi", str(e.exception))

    def test_toggle_flips_booleans(self):
        cli.set_values(self.m, ["wheel-invert=toggle", "fn-lock=~"], self.settings)
        self.assertEqual(self.m.wheel_state, (False, True))
        self.assertTrue(self.m.fn)
        cli.set_values(self.m, ["wheel-invert=toggle", "fn-lock=toggle"], self.settings)
        self.assertEqual(self.m.wheel_state, (False, False))
        self.assertFalse(self.m.fn)

    def test_thumb_invert_is_written_and_saved(self):
        cli.set_values(self.m, ["thumb-invert=on"], self.settings)
        self.assertTrue(self.m.thumb_inv)
        self.settings.save_thumb.assert_called_once_with(self.m, invert=True)

    def test_get_option_takes_names(self):
        from pylogi.app import build_parser
        a = build_parser().parse_args(["--get", "MX Test", "dpi", "battery"])
        self.assertEqual(a.get, ["MX Test", "dpi", "battery"])


class ReceiverDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def fake_receiver(self):
        slots = ["1: MX Master (mouse)"]
        rx = mock.Mock(family="Unifying", can_pair=True)

        def refresh(force=False):
            if self.paired:
                slots.append("2: K400 (keyboard)")
        dev = SimpleNamespace(rx=rx, slot_names=slots, name="Unifying receiver", info=SimpleNamespace(serial="ABCD"),
                              open_pairing=mock.Mock(), refresh=refresh, unpair=mock.Mock())
        return dev

    def test_unifying_dialog_reports_the_new_device(self):
        from pylogi.ui.cards.receiver import UnifyingPairDialog
        self.paired = False
        dev = self.fake_receiver()
        dlg = UnifyingPairDialog(None, dev)
        dev.open_pairing.assert_called_once_with(30)
        dlg._tick()
        self.assertFalse(dlg.done)
        self.paired = True
        dlg._tick()
        self.assertTrue(dlg.done)
        self.assertIn("K400", dlg.text.text())
        dev.rx.close_pairing.assert_not_called()

    def test_unifying_dialog_gives_up_and_closes_the_lock(self):
        from pylogi.ui.cards.receiver import UnifyingPairDialog
        self.paired = False
        dev = self.fake_receiver()
        dlg = UnifyingPairDialog(None, dev)
        dlg.elapsed = 100
        dlg._tick()
        self.assertTrue(dlg.done)
        dev.rx.close_pairing.assert_called_once()

    def test_unpair_asks_which_device_and_confirms(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        from pylogi.ui.cards.receiver import ReceiverCard
        self.paired = True
        dev = self.fake_receiver()
        dev.slot_names.append("2: K400 (keyboard)")
        card = ReceiverCard(dev)
        with mock.patch.object(QInputDialog, "getItem", return_value=("2: K400 (keyboard)", True)), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            card._unpair()
        dev.unpair.assert_called_once_with(2)
        with mock.patch.object(QInputDialog, "getItem", return_value=("2: K400 (keyboard)", True)), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            dev.unpair.reset_mock()
            card._unpair()
        dev.unpair.assert_not_called()


if __name__ == "__main__":
    unittest.main()
