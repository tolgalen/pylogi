# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.core import lighting_fx as fx  # noqa: E402
from pylogi.ui.keyboard_widget import build_rows  # noqa: E402

app = QApplication.instance() or QApplication([])
KEYS = fx.positions_from_rows(build_rows("ansi", False, None))
A, L, SPACE = 0x04, 0x0F, 0x2C                       # usages of 'A', 'L' (far apart) and the space bar


def lit(frame_):
    return {u for u, c in frame_.items() if any(c)}


class FrameTests(unittest.TestCase):
    def test_every_effect_colours_every_key_with_valid_values(self):
        st = fx.State(1)
        for effect in fx.EFFECTS:
            for t in (0.0, 0.4, 2.5, 9.0):
                f = fx.frame(effect, t, KEYS, (10, 200, 255), 5, st, [A] if fx.EFFECTS[effect][1] else None)
                self.assertEqual(set(f), set(KEYS), effect)
                for c in f.values():
                    self.assertEqual(len(c), 3)
                    self.assertTrue(all(0 <= v <= 255 for v in c), (effect, c))

    def test_unknown_effect(self):
        with self.assertRaises(ValueError):
            fx.frame("nope", 0, KEYS)

    def test_wave_moves_and_uses_the_chosen_colour(self):
        a, b = fx.frame("wave", 0.0, KEYS, (0, 200, 255)), fx.frame("wave", 1.1, KEYS, (0, 200, 255))
        self.assertNotEqual(a, b)
        self.assertTrue(all(c[0] == 0 and c[2] >= c[1] for c in a.values()))        # only shades of the colour

    def test_rainbow_has_many_hues_and_cycle_is_uniform(self):
        self.assertGreater(len(set(fx.frame("rainbow", 0.0, KEYS).values())), 20)
        c = fx.frame("cycle", 3.0, KEYS)
        self.assertEqual(len(set(c.values())), 1)
        self.assertNotEqual(c, fx.frame("cycle", 3.5, KEYS))

    def test_breathing_goes_dim_and_bright(self):
        levels = [max(next(iter(fx.frame("breathing", t / 10, KEYS, (255, 255, 255), 5).values())))
                  for t in range(0, 40)]
        self.assertGreater(max(levels), 200)
        self.assertLess(min(levels), 60)

    def test_speed_changes_the_pace(self):
        self.assertGreater(fx.speed_factor(10), fx.speed_factor(1) * 5)
        self.assertEqual(fx.frame("wave", 1.0, KEYS, speed=1) == fx.frame("wave", 1.0, KEYS, speed=10), False)

    def test_radar_lights_only_part_of_the_keyboard(self):
        n = len(lit(fx.frame("radar", 0.3, KEYS, (255, 0, 0))))
        self.assertTrue(0 < n < len(KEYS))

    def test_starlight_twinkles_and_fades(self):
        st = fx.State(7)
        for i in range(30):
            f = fx.frame("starlight", i * 0.1, KEYS, (255, 255, 255), 5, st)
        self.assertTrue(3 <= len(lit(f)) < len(KEYS) // 2)
        late = fx.frame("starlight", 60.0, KEYS, (255, 255, 255), 5, st)      # nothing new for a long time
        self.assertLessEqual(len(lit(late)), len(lit(f)) + 40)

    def test_reactive_lights_only_the_pressed_key_then_fades(self):
        st = fx.State()
        self.assertEqual(lit(fx.frame("reactive", 0.0, KEYS, (255, 0, 0), 5, st, [A])), {A})
        mid = fx.frame("reactive", 0.4, KEYS, (255, 0, 0), 5, st)
        self.assertEqual(lit(mid), {A})
        self.assertLess(mid[A][0], 255)
        self.assertEqual(lit(fx.frame("reactive", 5.0, KEYS, (255, 0, 0), 5, st)), set())

    def test_ripple_spreads_from_the_pressed_key(self):
        st = fx.State()
        first = fx.frame("ripple", 0.0, KEYS, (255, 0, 0), 5, st, [A])
        self.assertIn(A, lit(first))
        self.assertNotIn(L, lit(first))                                        # the far key is still dark
        later = lit(fx.frame("ripple", 0.9, KEYS, (255, 0, 0), 5, st))
        self.assertNotIn(A, later)                                             # the ring has moved on
        self.assertTrue(later)
        self.assertEqual(lit(fx.frame("ripple", 9.0, KEYS, (255, 0, 0), 5, st)), set())


class KeyPressTests(unittest.TestCase):
    def test_keycode_table(self):
        self.assertEqual(fx.usage_for_keycode(30), A)                          # KEY_A
        self.assertEqual(fx.usage_for_keycode(57), SPACE)
        self.assertEqual(fx.usage_for_keycode(1), 0x29)                        # Esc
        self.assertIsNone(fx.usage_for_keycode(999))
        self.assertEqual(len(set(fx.usage_for_keycode(c) for c in range(1, 128)) - {None}),
                         len([c for c in range(1, 128) if fx.usage_for_keycode(c)]))   # no two codes share a usage

    def test_event_nodes_are_found_by_model_name(self):
        root = Path(tempfile.mkdtemp())
        for n, label in (("event3", "Logitech G915 TKL Keyboard"), ("event4", "AT Translated Keyboard"),
                         ("event5", "Logitech MX Anywhere 3S"), ("mouse0", "x")):
            (root / n / "device").mkdir(parents=True)
            (root / n / "device" / "name").write_text(label + "\n")
        nodes = fx._event_nodes("G915 TKL LIGHTSPEED Wireless RGB Mechanical Gaming Keyboard", str(root))
        self.assertEqual([Path(n).name for n in nodes], ["event3"])
        self.assertEqual(fx._event_nodes("Nothing 123", str(root)), [])
        self.assertEqual(fx._event_nodes("G915", str(root / "missing")), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only: mkfifo")

    def test_reading_key_presses_from_a_node(self):
        fifo = Path(tempfile.mkdtemp()) / "event0"
        os.mkfifo(fifo)
        src = fx.KeyPressSource("G915")
        src.nodes = [str(fifo)]
        self.assertTrue(src.start())
        fd = os.open(fifo, os.O_WRONLY)
        ev = struct.Struct("llHHi")
        os.write(fd, ev.pack(0, 0, 1, 30, 1) + ev.pack(0, 0, 1, 30, 0) +      # A down, A up (ignored)
                 ev.pack(0, 0, 1, 57, 1) + ev.pack(0, 0, 4, 4, 30))            # space down, a non-key event
        end, got = time.time() + 3, []
        while len(got) < 2 and time.time() < end:
            got += src.pop()
            time.sleep(0.02)
        os.close(fd)
        src.stop()
        self.assertEqual(got, [A, SPACE])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only: O_NONBLOCK")

    def test_missing_permission_is_reported(self):
        src = fx.KeyPressSource("G915")
        src.nodes = ["/nonexistent/event9"]
        self.assertFalse(src.start())
        self.assertIn("input", src.reason)
        src.nodes = []
        self.assertFalse(src.start())


class FakeKeyboard:
    def __init__(self, fail_after=None):
        self.uid, self.name, self.writes, self.fail_after = "kb1", "G915 TKL", [], fail_after
        self.reset = 0

    def set_key_colors(self, colors):
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise RuntimeError("device went away")
        self.writes.append(dict(colors))

    def reset_lighting(self):
        self.reset += 1


class RunnerTests(unittest.TestCase):
    def test_only_changed_keys_are_sent(self):
        r = fx.Runner(FakeKeyboard(), "cycle", KEYS)
        st = fx.State()
        first = r.step(0.0, st)
        self.assertEqual(set(first), set(KEYS))
        r.sent.update(first)
        self.assertEqual(r.step(0.0, st), {})                                  # identical frame: nothing to write
        r.sent.update(r.step(2.0, st))
        self.assertEqual(r.step(2.0, st), {})

    def test_plays_and_stops(self):
        kb = FakeKeyboard()
        r = fx.Runner(kb, "rainbow", KEYS, fps=60)
        self.assertTrue(r.start())
        time.sleep(0.4)
        self.assertTrue(r.running)
        r.stop()
        self.assertFalse(r.running)
        self.assertGreater(len(kb.writes), 3)
        n = len(kb.writes)
        time.sleep(0.2)
        self.assertEqual(len(kb.writes), n)                                    # really stopped

    def test_a_failing_device_stops_the_effect_and_reports(self):
        kb = FakeKeyboard(fail_after=2)
        gone = []
        r = fx.Runner(kb, "rainbow", KEYS, fps=100, on_stop=gone.append)
        r.start()
        end = time.time() + 3
        while r.running and time.time() < end:
            time.sleep(0.02)
        self.assertFalse(r.running)
        self.assertEqual(r.error, "device went away")
        self.assertEqual(gone, [r])

    def test_key_press_effects_refuse_without_input_access(self):
        r = fx.Runner(FakeKeyboard(), "ripple", KEYS)
        orig = fx.KeyPressSource.start
        fx.KeyPressSource.start = lambda self: setattr(self, "reason", "cannot read /dev/input") or False
        try:
            self.assertFalse(r.start())
        finally:
            fx.KeyPressSource.start = orig
        self.assertIn("/dev/input", r.error)
        self.assertFalse(r.running)


class HubTests(unittest.TestCase):
    def test_one_effect_per_keyboard_and_reset_on_exit(self):
        hub, kb = fx.Hub(), FakeKeyboard()
        a = hub.start(kb, "cycle", KEYS, (1, 2, 3), 5)
        time.sleep(0.1)
        b = hub.start(kb, "breathing", KEYS, (1, 2, 3), 5)                     # replaces the first one
        self.assertIsNot(a, b)
        self.assertFalse(a.running)
        self.assertIs(hub.get(kb), b)
        hub.stop_all()
        self.assertFalse(b.running)
        self.assertIsNone(hub.get(kb))
        self.assertEqual(kb.reset, 1)                                          # keys handed back to the keyboard

    def test_a_refused_effect_is_not_kept(self):
        hub, kb = fx.Hub(), FakeKeyboard()
        orig = fx.KeyPressSource.start
        fx.KeyPressSource.start = lambda self: setattr(self, "reason", "no access") or False
        try:
            r = hub.start(kb, "reactive", KEYS, (1, 2, 3), 5)
        finally:
            fx.KeyPressSource.start = orig
        self.assertEqual(r.error, "no access")
        self.assertIsNone(hub.get(kb))


class SavedEffectTests(unittest.TestCase):
    def test_a_saved_effect_restarts_when_the_keyboard_connects(self):
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.core.settings import DeviceSettings
        dev = next(d for d in DemoManager().devices if d.online and d.has("keylight"))
        settings = DeviceSettings(Config(Path(tempfile.mkdtemp()) / "c.json"))
        settings.save(dev, "fx", {"effect": "breathing", "color": [255, 0, 0], "speed": 7})
        try:
            settings.apply(dev)
            run = fx.HUB.get(dev)
            self.assertIsNotNone(run)
            self.assertEqual((run.effect, run.color, run.speed), ("breathing", (255, 0, 0), 7))
            self.assertGreater(len(run.keys), 60)
        finally:
            fx.HUB.stop(dev)

    def test_an_unknown_saved_effect_is_ignored(self):
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.core.settings import DeviceSettings
        dev = next(d for d in DemoManager().devices if d.online and d.has("keylight"))
        settings = DeviceSettings(Config(Path(tempfile.mkdtemp()) / "c.json"))
        settings.save(dev, "fx", {"effect": "from-the-future"})
        settings.apply(dev)
        self.assertIsNone(fx.HUB.get(dev))


class CardTests(unittest.TestCase):
    def test_controls_play_change_and_stop(self):
        from unittest import mock
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui import theme
        from pylogi.ui.cards import LightingCard
        theme.apply(app, "dark", "default")
        dev = next(d for d in DemoManager().devices if d.online and d.has("keylight"))
        card = LightingCard(dev, Config(Path(tempfile.mkdtemp()) / "c.json"))
        self.assertFalse(card.fx_button.isEnabled())                           # "Static colours": nothing to play
        card.fx_box.setCurrentIndex(card.fx_box.findData("wave"))
        card._fx_changed()
        self.assertTrue(card.fx_button.isEnabled())
        try:
            card._fx_toggle()
            run = fx.HUB.get(dev)
            self.assertTrue(run.running)
            card.fx_speed.setValue(9)
            self.assertEqual(run.speed, 9)                                     # live
            self.assertEqual(card.settings._rec(dev)["fx"]["speed"], 9)        # and remembered
            card._push({4: (1, 2, 3)})                                         # a static colour replaces the effect
            self.assertIsNone(fx.HUB.get(dev))
            self.assertIsNone(card.settings._rec(dev).get("fx"))
            card.fx_box.setCurrentIndex(card.fx_box.findData("ripple"))
            card._fx_changed()
            with mock.patch("pylogi.ui.cards.QMessageBox.warning") as warn, \
                    mock.patch.object(fx.KeyPressSource, "start", lambda s: setattr(s, "reason", "x") or False):
                card._fx_play()
            self.assertTrue(warn.called)
            self.assertIsNone(fx.HUB.get(dev))
        finally:
            fx.HUB.stop(dev)
            card.deleteLater()                                                  # no widget outlives the application
            app.processEvents()
            QApplication.sendPostedEvents(None, 0)


class CopyColourTests(unittest.TestCase):
    def setUp(self):
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui import theme
        from pylogi.ui.cards import LightingCard
        theme.apply(app, "dark", "default")
        dev = next(d for d in DemoManager().devices if d.online and d.has("keylight"))
        self.card = LightingCard(dev, Config(Path(tempfile.mkdtemp()) / "c.json"))
        self.card.resize(900, 500)
        self.card.show()
        self.card.kb._layout()

    def tearDown(self):
        self.card.deleteLater()
        app.processEvents()
        QApplication.sendPostedEvents(None, 0)

    def click(self, usage, button):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        rect = next(r for r, u, _ in self.card.kb._rects if u == usage)
        pos = QPointF(rect.center())
        self.card.kb.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, button, button, Qt.NoModifier))

    def test_right_click_copies_the_colour_without_selecting(self):
        from PySide6.QtCore import Qt
        kb = self.card.kb
        kb.colors[A] = (12, 34, 56)
        self.click(A, Qt.RightButton)
        self.assertEqual(tuple(self.card.color.color()), (12, 34, 56))
        self.assertEqual(kb.selected, set())

    def test_copy_mode_then_apply_to_another_key(self):
        from PySide6.QtCore import Qt
        kb = self.card.kb
        kb.colors[A] = (200, 10, 20)
        self.card.copy_btn.setChecked(True)
        self.assertTrue(kb.pick_mode)
        self.click(A, Qt.LeftButton)
        self.assertEqual(tuple(self.card.color.color()), (200, 10, 20))
        self.assertFalse(kb.pick_mode)
        self.assertFalse(self.card.copy_btn.isChecked())
        self.click(L, Qt.LeftButton)                                           # back to normal: selects
        self.assertEqual(kb.selected, {L})
        self.card._apply(True)
        self.assertEqual(kb.colors[L], (200, 10, 20))

    def test_a_key_without_colour_copies_nothing(self):
        from PySide6.QtCore import Qt
        before = tuple(self.card.color.color())
        self.click(A, Qt.RightButton)
        self.assertEqual(tuple(self.card.color.color()), before)


if __name__ == "__main__":
    unittest.main()
