# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Windows hidapi enumeration and transport, exercised with a fake ``hid`` module."""
import sys
import types
import unittest
from unittest import mock

from pylogi.hidpp import transport


def entry(path, pid, page, usage, bus=1):
    return {"path": path.encode(), "vendor_id": 0x046D, "product_id": pid, "usage_page": page,
            "usage": usage, "bus_type": bus, "product_string": "X"}


RX = r"\\?\hid#vid_046d&pid_c52b&mi_02&col0%d#7&abc&0&000%d#{x}"


class FakeDev:
    def __init__(self, log):
        self.log, self.queue, self.name = log, [], ""

    def open_path(self, p): self.name = p.decode()
    def set_nonblocking(self, v): pass
    def write(self, d): self.log.append((self.name, bytes(d))); return len(d)
    def read(self, n, t=0): return self.queue.pop(0) if self.queue else []
    def close(self): pass


class HidapiTests(unittest.TestCase):
    def fake(self, entries):
        m = types.ModuleType("hid")
        m.enumerate = lambda v, p: entries
        m.log, m.devs = [], {}

        def device():
            d = FakeDev(m.log)
            orig = d.open_path
            d.open_path = lambda p: (orig(p), m.devs.__setitem__(p.decode(), d))
            return d
        m.device = device
        return mock.patch.dict(sys.modules, {"hid": m}), m

    def test_receiver_collections_are_grouped(self):
        es = [entry(RX % (i, i - 1), 0xC52B, 0xFF00, u) for i, u in ((1, 1), (2, 2), (3, 4))]
        es.append(entry(r"\\?\hid#vid_046d&pid_c52b&mi_00#8&1&0&0000#{x}", 0xC52B, 1, 6))
        ctx, _ = self.fake(es)
        with ctx:
            infos = transport._enum_hidapi()
        self.assertEqual(len(infos), 1)
        self.assertTrue(infos[0].has_short)
        self.assertEqual(infos[0].bus, "usb")
        self.assertIn("col02", infos[0].path)
        self.assertIn("col01", infos[0].short_path)

    def test_bluetooth_le_usage_0202(self):
        ctx, _ = self.fake([entry(r"\\?\hid#{0000fff0}_dev_vid&02046d_pid&b023#9&1&0&0000#{x}",
                                  0xB023, 0xFF43, 0x0202, bus=0)])
        with ctx:
            infos = transport._enum_hidapi()
        self.assertEqual([(i.bus, i.has_short, i.short_path) for i in infos], [("bluetooth", False, "")])

    def test_routing_by_report_id(self):
        es = [entry(RX % (1, 0), 0xC52B, 0xFF00, 1), entry(RX % (2, 1), 0xC52B, 0xFF00, 2)]
        ctx, m = self.fake(es)
        with ctx:
            info = transport._enum_hidapi()[0]
            t = transport.HidapiTransport(info.path, info.short_path)
            t.write(b"\x10\x01\x00\x00\x00\x00\x00")
            t.write(b"\x11" + b"\0" * 19)
            self.assertIn("col01", m.log[0][0])
            self.assertIn("col02", m.log[1][0])
            m.devs[info.path].queue.append([0x11, 1])
            self.assertEqual(t.read(0.1), bytes([0x11, 1]))
            self.assertIsNone(t.read(0))


class WindowsWebcamTests(unittest.TestCase):
    def test_logitech_camera_from_pnp_entry(self):
        from pylogi.core import webcam
        cam = webcam.parse_pnp_camera([r"USB\VID_046D&PID_085E&MI_00", r"USB\VID_046D&PID_085E"],
                                      "BRIO", r"USB\VID_046D&PID_085E&MI_00\6&1&0&0000")
        self.assertEqual((cam.name, cam.vid_pid, cam.kind), ("BRIO", "046d:085e", "webcam"))
        self.assertEqual(cam.cam_controls(), [])
        self.assertEqual(cam.video_modes(), [])

    def test_other_vendors_are_ignored(self):
        from pylogi.core import webcam
        self.assertIsNone(webcam.parse_pnp_camera([r"USB\VID_04F2&PID_B6BF"], "Integrated", "x"))

    def test_dispatch_by_platform(self):
        from pylogi.core import webcam
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(webcam, "_find_windows", return_value=["w"]):
            self.assertEqual(webcam.find_logitech_webcams(), ["w"])


if __name__ == "__main__":
    unittest.main()
