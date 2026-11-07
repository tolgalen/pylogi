# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Constants for the Logitech HID++ protocol (USB ids, features, registers)."""

VENDOR_LOGITECH = 0x046D

# Receiver USB product ids -> (family, max paired devices)
RECEIVERS: dict[int, tuple[str, int]] = {
    0xC548: ("Bolt", 6),
    0xC52B: ("Unifying", 6),
    0xC532: ("Unifying", 6),
    **{pid: ("Lightspeed", 1) for pid in (0xC539, 0xC53A, 0xC53D, 0xC53F,
                                          0xC541, 0xC545, 0xC547, 0xC54D)},
    **{pid: ("Nano", 2) for pid in (0xC518, 0xC51A, 0xC51B, 0xC521, 0xC525,
                                    0xC526, 0xC52E, 0xC52F, 0xC531, 0xC534,
                                    0xC535, 0xC537)},
}

# Report ids / sizes
SHORT_ID, LONG_ID, DJ_ID = 0x10, 0x11, 0x20
SHORT_SIZE, LONG_SIZE = 7, 20
RECEIVER_NUMBER = 0xFF
SW_ID = 0x0C  # software id used in the low nibble of HID++ 2.0 requests

# HID++ 1.0 registers
REG_NOTIFICATIONS = 0x00
REG_CONNECTION = 0x02
REG_PAIRING = 0xB2
REG_RECEIVER_INFO = 0xB5
REG_BOLT_PAIRING = 0x2C1
REG_BOLT_DISCOVERY = 0xC0

# HID++ 2.0 feature ids
class F:
    ROOT = 0x0000
    FEATURE_SET = 0x0001
    FW_VERSION = 0x0003
    DEVICE_NAME = 0x0005
    BATTERY_STATUS = 0x1000
    BATTERY_VOLTAGE = 0x1001
    UNIFIED_BATTERY = 0x1004
    REPROG_CONTROLS_V4 = 0x1B04
    CHANGE_HOST = 0x1814
    HOSTS_INFO = 0x1815
    BACKLIGHT2 = 0x1982
    SMART_SHIFT = 0x2110
    SMART_SHIFT_ENH = 0x2111
    HIRES_WHEEL = 0x2121
    THUMB_WHEEL = 0x2150
    ADJUSTABLE_DPI = 0x2201
    REPORT_RATE = 0x8060
    EXT_REPORT_RATE = 0x8061
    COLOR_LED_EFFECTS = 0x8070
    RGB_EFFECTS = 0x8071
    PER_KEY_LIGHTING = 0x8080
    PER_KEY_LIGHTING_V2 = 0x8081
    ONBOARD_PROFILES = 0x8100
    FN_INVERSION = 0x40A0
    NEW_FN_INVERSION = 0x40A2


DEVICE_KINDS = {0: "keyboard", 1: "remote", 2: "numpad", 3: "mouse",
                4: "touchpad", 5: "trackball", 6: "presenter", 7: "receiver"}

ERRORS_20 = {1: "unknown", 2: "invalid argument", 3: "out of range",
             4: "hardware error", 5: "logitech error", 6: "invalid feature index",
             7: "invalid function", 8: "busy", 9: "unsupported"}
ERRORS_10 = {1: "invalid sub id", 2: "invalid address", 3: "invalid value",
             4: "connect fail", 5: "too many devices", 6: "already exists",
             7: "busy", 8: "unknown device", 9: "resource error",
             10: "request unavailable", 11: "unsupported parameter",
             12: "wrong pin"}
