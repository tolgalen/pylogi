# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pylogi.core import buttons as B
from pylogi.hidpp import features as ft
from pylogi.hidpp.constants import F, SW_ID
from pylogi.hidpp.onboard import crc16, parse_profile, seal, O_DPIS, Onboard
from pylogi.hidpp.protocol import Channel, Hidpp20Device, NoReply
from pylogi.hidpp.transport import Transport


class FakeDevice(Transport):
    """A tiny HID++ 2.0 device: ROOT, FEATURE_SET, DEVICE_NAME, DPI, CHANGE_HOST."""
    FEATS = {F.FEATURE_SET: 1, F.DEVICE_NAME: 2, F.ADJUSTABLE_DPI: 3, F.CHANGE_HOST: 4,
             F.HOSTS_INFO: 5, F.FW_VERSION: 6}

    def __init__(self):
        self.q = []
        self.dpi = 1000
        self.host = 0
        self.writes = []
        self.silent = False          # simulate a sleeping device
        self.renames = []

    def write(self, data):
        self.writes.append(data)
        if self.silent:
            return
        dev, idx, fn_sw, params = data[1], data[2], data[3], data[4:]
        fn = fn_sw >> 4
        hdr = bytes([idx, fn_sw])
        rep = lambda payload: self.q.append(bytes([0x11, dev]) + hdr + payload.ljust(16, b"\0"))
        if idx == 0 and fn == 0:
            fid = struct.unpack(">H", params[:2])[0]
            rep(bytes([self.FEATS.get(fid, 0), 0, 0]))
        elif idx == 0 and fn == 1:
            rep(bytes([4, 5, params[2]]))
        elif idx == 1 and fn == 0:
            rep(bytes([len(self.FEATS)]))
        elif idx == 1 and fn == 1:
            fid = {v: k for k, v in self.FEATS.items()}[params[0]]
            rep(struct.pack(">H", fid))
        elif idx == 2 and fn == 0:
            rep(bytes([6]))
        elif idx == 2 and fn == 1:
            rep(b"MXFake"[params[0]:])
        elif idx == 2 and fn == 2:
            rep(bytes([3]))
        elif idx == 3 and fn == 1:
            rep(bytes([0]) + struct.pack(">HHHH", 400, 0xE000 + 100, 8000, 0) + bytes(6))
        elif idx == 3 and fn == 2:
            rep(bytes([0]) + struct.pack(">HH", self.dpi, 800))
        elif idx == 3 and fn == 3:
            self.dpi = struct.unpack(">H", params[1:3])[0]
            rep(bytes([0]) + params[1:3])
        elif idx == 4 and fn == 0:
            rep(bytes([3, self.host]))
        elif idx == 4 and fn == 1:
            self.host = params[0]   # no reply on purpose (device drops the link)
        elif idx == 5 and fn == 1:
            rep(bytes([params[0], 1, 4, 0, 8, 24]))
        elif idx == 5 and fn == 4:
            self.renames.append((params[0], params[1], bytes(params[2:16])))
            rep(bytes([params[0]]))
        elif idx == 6 and fn == 0:
            rep(bytes([1, 0xD2, 0xD4, 0x51, 0x23]))
        elif idx == 6 and fn == 2:
            rep(b"2538ZAK5A678")
        else:
            self.q.append(bytes([0x11, dev, 0xFF, idx, fn_sw, 0x07]) + bytes(14))

    def read(self, timeout):
        return self.q.pop(0) if self.q else None


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeDevice()
        self.d = Hidpp20Device(Channel(self.t), 1)

    def test_ping_and_features(self):
        self.assertEqual(self.d.ping(), (4, 5))
        self.assertEqual(self.d.features()[F.ADJUSTABLE_DPI], 3)
        self.assertEqual(self.d.name(), "MXFake")
        self.assertEqual(self.d.kind(), "mouse")

    def test_short_report_for_small_requests(self):
        self.d.ping()
        self.assertEqual(self.t.writes[0][0], 0x10)
        self.assertEqual(len(self.t.writes[0]), 7)

    def test_dpi_roundtrip(self):
        info = ft.read_dpi(self.d)
        self.assertEqual(info.current, 1000)
        self.assertEqual(info.values[:3], [400, 500, 600])
        self.assertEqual(info.values[-1], 8000)
        ft.write_dpi(self.d, 1600)
        self.assertEqual(self.t.dpi, 1600)

    def test_hosts_without_hosts_info(self):
        self.d._index[F.HOSTS_INFO] = None          # device only offers CHANGE_HOST
        cur, hosts = ft.read_hosts(self.d)
        self.assertEqual((cur, len(hosts)), (0, 3))
        ft.switch_host(self.d, 2)
        self.assertEqual(self.t.host, 2)

    def test_error_and_timeout(self):
        from pylogi.hidpp.protocol import Hidpp20Error
        with self.assertRaises(Hidpp20Error):
            self.d.ch.request(1, (0x04 << 8) | (9 << 4) | SW_ID)
        silent = Channel(type("S", (Transport,), {"write": lambda s, d: None,
                                                  "read": lambda s, t: None})())
        with self.assertRaises(NoReply):
            silent.request(1, 0x0000 | SW_ID, timeout=0.05)


class OnboardTests(unittest.TestCase):
    def test_crc_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)

    def test_seal_and_patch_roundtrip(self):
        raw = bytearray(255)
        raw[0] = 2
        raw[O_DPIS:O_DPIS + 10] = struct.pack("<5H", 400, 800, 1600, 3200, 6400)
        raw = seal(bytes(raw))
        p = parse_profile(0, 1, True, raw)
        self.assertEqual((p.report_rate_ms, p.dpis[2]), (2, 1600))
        p.dpis[0] = 450
        p.color = (1, 2, 3)
        p.report_rate_ms = 4
        p.buttons[0].raw = B.key_binding(0x01, 0x06)
        ob = Onboard.__new__(Onboard)
        out = ob.encode(p)
        self.assertEqual(len(out), 255)
        self.assertEqual(struct.unpack(">H", out[-2:])[0], crc16(out[:-2]))
        q = parse_profile(0, 1, True, out)
        self.assertEqual((q.dpis[0], q.color, q.report_rate_ms), (450, (1, 2, 3), 4))
        self.assertEqual(q.buttons[0].describe(), "Ctrl+C")
        # untouched bytes are preserved
        self.assertEqual(out[100:150], raw[100:150])


class MiscTests(unittest.TestCase):
    def test_key_ids(self):
        self.assertEqual(ft.key_id(0x04), 1)       # A
        self.assertEqual(ft.key_id(0x3A), 55)      # F1
        self.assertEqual(ft.key_id(0xE0), 104)     # left ctrl

    def test_binding_descriptions(self):
        self.assertEqual(B.describe_binding(B.mouse_binding(2)), "Right click")
        self.assertEqual(B.describe_binding(B.consumer_binding(0xCD)), "Play/Pause")
        self.assertEqual(B.describe_binding(B.DISABLED), "Disabled")

    def test_demo_ui_builds(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from pathlib import Path
        import tempfile
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.main_window import MainWindow
        m = DemoManager()
        w = MainWindow(m, Config(Path(tempfile.mkdtemp()) / "c.json"))
        for d in m.devices:
            w._open(d)
        w._home()


if __name__ == "__main__":
    unittest.main()


class ImageMatchTests(unittest.TestCase):
    CATALOG = {
        "g913_tkl": {"displayName": "G915 TKL", "extendedDisplayName": "G915 TKL", "type": "KEYBOARD",
                     "modelIds": ["408e", "c343"]},
        "g915_tkl": {"displayName": "G915 TKL", "extendedDisplayName": "G915 TKL Wireless RGB Mechanical Gaming Keyboard",
                     "type": "KEYBOARD", "modelIds": ["c343", "408e"]},
        "g915": {"displayName": "G915", "extendedDisplayName": "G915 Wireless", "type": "KEYBOARD",
                 "modelIds": ["407c"]},
        "mx_anywhere_3s": {"displayName": "MX Anywhere 3S", "extendedDisplayName": "MX Anywhere 3S",
                           "type": "MOUSE", "modelIds": ["2b037"]},
        "m336": {"displayName": "M336/M337/M535", "extendedDisplayName": "Bluetooth Mouse M336/M337/M535",
                 "type": "MOUSE", "modelIds": ["1b014"]},
        "c920": {"displayName": "C920", "extendedDisplayName": "C920 Webcam", "type": "CAMERA",
                 "modelIds": ["08e5"]},
        "c920c": {"displayName": "C920c", "extendedDisplayName": "C920c", "type": "CAMERA", "modelIds": ["0000"]},
    }

    def test_name_beats_colliding_wpid(self):
        from pylogi.core.images import match_device
        self.assertEqual(match_device(self.CATALOG, "MX Anywhere 3S", "mouse", ["b014"]), "mx_anywhere_3s")

    def test_duplicate_catalog_entries_use_key(self):
        from pylogi.core.images import match_device
        self.assertEqual(match_device(self.CATALOG, "G915 TKL", "keyboard", ["408e"]), "g915_tkl")

    def test_webcam_and_unique_id_fallback(self):
        from pylogi.core.images import match_device
        self.assertEqual(match_device(self.CATALOG, "HD Pro Webcam C920", "webcam", ["08e5"]), "c920")
        self.assertEqual(match_device(self.CATALOG, "Some unknown name", "mouse", ["b014"]), "m336")
        self.assertIsNone(match_device(self.CATALOG, "Some unknown name", "keyboard", ["ffff"]))


class ButtonEditorTests(unittest.TestCase):
    META = {"images": [{"key": "device_buttons_image", "assignments": [
        {"slotId": "x_c82", "marker": {"x": 71, "y": 16}, "label": {"x": 1, "y": 0}},
        {"slotId": "x_c86", "marker": {"x": 22, "y": 41}, "label": {"x": -1, "y": 0}},
        {"slotId": "x_c83", "marker": {"x": 28, "y": 60}, "label": {"x": 0, "y": 1}},
        {"slotId": "x_mouse_settings", "marker": {"x": 80, "y": 45}, "label": {"x": 1, "y": 0}}]}]}

    def test_parse_slots(self):
        from pylogi.core.images import parse_button_slots
        slots = parse_button_slots(self.META)
        self.assertEqual([(s["cid"], s["side"]) for s in slots], [(82, 1), (86, -1), (83, -1)])

    def test_cards_never_overlap_and_stay_inside(self):
        from PySide6.QtCore import QRectF
        from pylogi.core.images import parse_button_slots
        from pylogi.ui.button_map import CARD_H, place_cards
        slots = parse_button_slots(self.META) + [{"cid": 9, "x": 25, "y": 62, "side": -1},
                                                 {"cid": 10, "x": 26, "y": 64, "side": -1}]
        area = QRectF(0, 0, 700, 500)
        rects = place_cards(slots, QRectF(150, 0, 400, 500), area)
        left = sorted((r for i, r in rects.items() if slots[i]["side"] < 0), key=lambda r: r.y())
        for a, b in zip(left, left[1:]):
            self.assertLessEqual(a.bottom(), b.top())
        for r in rects.values():
            self.assertGreaterEqual(r.top(), area.top())
            self.assertLessEqual(r.bottom(), area.bottom() + 1e-6)
            self.assertEqual(r.height(), CARD_H)

    def test_page_sections_and_remap_flow(self):
        from PySide6.QtWidgets import QApplication, QComboBox
        app = QApplication.instance() or QApplication([])
        import tempfile
        from pathlib import Path
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.device_page import ButtonsPage, DevicePage
        m = DemoManager()
        page = DevicePage(Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        for dev in m.devices:                       # every section of every device builds
            page.show_device(dev)
            for key in list(page.navs):
                page.ensure(key)
        page.show_device(m.devices[0])              # mouse -> buttons page first
        bp = page.ensure("buttons")
        self.assertIsInstance(bp, ButtonsPage)
        bp.map._select(0x56)                        # Forward
        bp.inspector.type_box.setCurrentIndex(bp.inspector.type_box.findData("remap"))
        bp.inspector._type_changed()
        target = bp.inspector._widgets["cid"]
        target.setCurrentIndex(target.findData(0x53))   # -> Back
        bp.inspector._commit()
        self.assertEqual(m.devices[0].controls()[4].remap, 0x53)
        self.assertEqual(bp.map.cards[0x56].action.toolTip(), "Back")
        bp.map._select(0x50)                        # left click cannot be reassigned
        self.assertIsNone(bp.inspector.findChild(QComboBox))


class CameraLogicTests(unittest.TestCase):
    def _controls(self):
        from pylogi.core.webcam import CamControl
        mk = lambda i, n, t, lo, hi, st, d, v, menu=None: CamControl(i, n, t, lo, hi, st, d, v, 0, menu or {})
        return [mk(1, "Brightness", 1, 0, 255, 1, 128, 140), mk(2, "Contrast", 1, 0, 255, 1, 128, 127),
                mk(3, "White Balance, Automatic", 2, 0, 1, 1, 1, 1),
                mk(4, "White Balance Temperature", 1, 2000, 6500, 1, 4000, 4896),
                mk(5, "Auto Exposure", 3, 0, 3, 1, 3, 3, {1: "Manual Mode", 3: "Aperture Priority Mode"}),
                mk(6, "Exposure Time, Absolute", 1, 3, 2047, 1, 250, 666),
                mk(7, "Pan, Absolute", 1, -36000, 36000, 3600, 0, 0),
                mk(8, "Backlight Compensation", 1, 0, 1, 1, 0, 0),
                mk(9, "Power Line Frequency", 3, 0, 2, 1, 2, 2, {0: "Disabled", 1: "50 Hz", 2: "60 Hz"})]

    def test_roles_and_auto_modes(self):
        from pylogi.ui import camera_page as cp
        cs = self._controls()
        roles = cp.find_roles(cs)
        self.assertEqual(roles["exposure"].id, 6)
        self.assertEqual(roles["wb_auto"].id, 3)
        self.assertEqual(roles["flicker"].id, 9)
        self.assertTrue(cp.auto_is_on(roles["exposure_auto"], 3))
        self.assertFalse(cp.auto_is_on(roles["exposure_auto"], 1))
        self.assertEqual((cp.auto_value(roles["exposure_auto"], True), cp.auto_value(roles["exposure_auto"], False)), (3, 1))
        self.assertEqual((cp.auto_value(roles["wb_auto"], True), cp.auto_value(roles["wb_auto"], False)), (1, 0))

    def test_presets_write_autos_first_and_stay_in_range(self):
        from pylogi.ui import camera_page as cp
        cs = self._controls()
        by_id = {c.id: c for c in cs}
        for name in ("Default", "Streaming", "Video call"):
            preset = cp.builtin_preset(name, cs)
            self.assertTrue(preset, name)
            names = list(preset)
            autos = [n for n in names if n in ("Auto Exposure", "White Balance, Automatic")]
            self.assertEqual(names[:len(autos)], autos, name)
            for c in cs:
                if c.name in preset:
                    self.assertTrue(c.minimum <= preset[c.name] <= c.maximum, (name, c.name))
                    self.assertEqual((preset[c.name] - c.minimum) % c.step, 0, (name, c.name))
        self.assertEqual(cp.builtin_preset("Video call", cs)["Backlight Compensation"], 1)

    def test_camera_page_with_demo_camera(self):
        import tempfile
        from pathlib import Path
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoWebcam
        from pylogi.ui.camera_page import CameraPage
        cam = DemoWebcam()
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        page = CameraPage(cam, cfg)
        self.assertIn("zoom", page.rows)
        page.apply_preset("Video call")
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Video call")
        before = {c.name: c.value for c in cam.cam_controls()}
        page.apply_preset("Default")
        after = {c.name: c.value for c in cam.cam_controls()}
        self.assertEqual(after["Brightness"], 128)
        self.assertNotEqual(before["Brightness"], after["Brightness"])


class DeviceDetailsTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeDevice()
        self.d = Hidpp20Device(Channel(self.t), 1)

    def test_identity(self):
        self.assertEqual(ft.read_identity(self.d), ("d2d45123", "2538ZAK5A678"))

    def test_rename_host_chunks_and_limit(self):
        ft.rename_host(self.d, 2, "Portátil de Carlos")
        data = b"".join(c for _, _, c in self.t.renames)
        self.assertEqual([(h, off) for h, off, _ in self.t.renames], [(2, 0), (2, 14)])
        self.assertEqual(data.split(b"\0")[0].decode(), "Portátil de Carlos")
        self.t.renames.clear()
        ft.rename_host(self.d, 0, "x" * 40)            # device accepts 24 bytes
        data = b"".join(c for _, _, c in self.t.renames)
        self.assertEqual(data.split(b"\0")[0], b"x" * 24)

    def test_on_online_fires_on_every_reconnect(self):
        from pylogi.core.device import HidppDevice
        hd = HidppDevice(self.d, "u", "Bolt")
        calls = []
        hd.on_online = lambda dev: calls.append(dev.online)
        hd.refresh()
        hd.refresh()                       # still online: no new call
        self.t.silent = True
        hd.refresh()
        self.assertTrue(hd.online)         # one unanswered ping is tolerated ...
        self.assertTrue(hd.recheck)        # ... and the manager is told to look again soon
        hd.refresh()
        self.assertFalse(hd.online)        # the second one in a row makes it offline
        self.t.silent = False
        hd.refresh()
        self.assertEqual(calls, [True, True])

    def test_a_single_missed_ping_does_not_flash_offline(self):
        from pylogi.core.device import HidppDevice
        hd = HidppDevice(self.d, "u", "Bolt")
        hd.refresh()
        states = []
        for silent in (True, False, True, False, True, False):         # a ping lost now and then
            self.t.silent = silent
            hd.refresh()
            states.append(hd.online)
        self.assertTrue(all(states))                                    # never shown as offline
        self.assertEqual(hd._misses, 0)                                 # an answer resets the count
        self.assertEqual(hd.device_key(), "serial:2538zak5a678")


class SettingsTests(unittest.TestCase):
    def _env(self):
        import tempfile
        from pathlib import Path
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.core.settings import DeviceSettings
        return DeviceSettings(Config(Path(tempfile.mkdtemp()) / "c.json")), DemoManager().devices[0]

    def test_saved_remaps_are_reapplied(self):
        st, dev = self._env()
        dev.remap_control(0x56, 0x53)
        st.save_remap(dev, 0x56, 0x53)
        st.save(dev, "dpi", 1600)
        dev._remaps.clear()                # device lost its volatile settings
        st.apply(dev)
        self.assertEqual(dev.controls()[4].remap, 0x53)
        self.assertEqual(dev.dpi().current, 1600)

    def test_unmanaged_restores_and_stops(self):
        st, dev = self._env()
        dev.remap_control(0x56, 0x53)
        st.save_remap(dev, 0x56, 0x53)
        st.set_managed(dev, False)
        self.assertFalse(st.managed(dev))
        self.assertEqual(dev.controls()[4].remap, 0)           # default function restored
        st.save_remap(dev, 0x53, 0x56)                         # ignored while unmanaged
        self.assertEqual(st.remaps(dev), {})
        st.apply(dev)
        self.assertEqual(dev.controls()[3].remap, 0)
        st.set_managed(dev, True)
        self.assertTrue(st.managed(dev))

    def test_details_page_builds_for_every_demo_device(self):
        import tempfile
        from pathlib import Path
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.details import DetailsPage
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        for dev in DemoManager().devices:
            DetailsPage(dev, cfg)


class CameraWritePlanTests(CameraLogicTests):
    """Regression: 'Default' failed with 'Permission denied' on inactive manual controls."""

    def test_default_skips_controls_made_inactive_by_auto_modes(self):
        from pylogi.core.camera import plan_writes, builtin_preset
        cs = self._controls()
        plan = plan_writes(builtin_preset("Default", cs), cs)
        names = [c.name for c, _ in plan]
        self.assertNotIn("Exposure Time, Absolute", names)       # exposure auto is on
        self.assertNotIn("White Balance Temperature", names)     # white balance auto is on
        for auto in ("White Balance, Automatic", "Auto Exposure"):
            self.assertLess(names.index(auto), names.index("Brightness"))
        self.assertTrue({"Brightness", "Contrast", "Pan, Absolute"} <= set(names))

    def test_manual_values_are_written_after_auto_is_switched_off(self):
        from pylogi.core.camera import plan_writes
        cs = self._controls()
        values = {"Exposure Time, Absolute": 400, "Auto Exposure": 1, "Brightness": 100}
        plan = [(c.name, v) for c, v in plan_writes(values, cs)]
        self.assertEqual(plan[0], ("Auto Exposure", 1))
        self.assertIn(("Exposure Time, Absolute", 400), plan)

    def test_apply_preset_does_not_raise_permission_errors(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication([])
        from pylogi.core.camera import find_roles
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoWebcam
        from pylogi.ui.camera_page import CameraPage

        class StrictCam(DemoWebcam):           # behaves like the UVC driver
            def set_cam_control(self, cid, value):
                roles = find_roles(self._c)
                ids = {roles[m].id: roles[a] for m, a in (("wb", "wb_auto"), ("focus", "focus_auto"))
                       if m in roles and a in roles}
                auto = ids.get(cid)
                if auto is not None and auto.value == 1:
                    raise PermissionError(13, "Permission denied")
                super().set_cam_control(cid, value)

        cam = StrictCam()
        page = CameraPage(cam, Config(Path(tempfile.mkdtemp()) / "c.json"))
        with mock.patch.object(QMessageBox, "warning") as warn:
            for name in ("Default", "Streaming", "Video call", "Default"):
                page.apply_preset(name)
        warn.assert_not_called()
