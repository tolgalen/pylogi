# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""ONBOARD_PROFILES (0x8100): read/modify/write the profiles stored in the device.

Profiles are edited *in place*: only the fields we understand are patched in
the raw sector, everything else (macros, lighting, reserved bytes) is kept
untouched and the CRC is recomputed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ..core.types import ButtonBinding, Profile
from .constants import F
from .protocol import Hidpp20Device

# offsets inside a profile sector
O_RATE, O_DPI_DEF, O_DPI_SHIFT, O_DPIS = 0, 1, 2, 3     # 5 x uint16 LE
O_COLOR = 13                                            # r, g, b
O_BUTTONS, N_BUTTONS = 32, 16                           # 4 bytes each
O_NAME, NAME_LEN = 160, 48                              # UTF-16LE
O_LIGHT, N_LIGHT, LIGHT_LEN = 208, 4, 11                # lighting records after the name
LIGHT_FIXED = 0x01                                      # record mode: fixed colour (mode, r, g, b, 0x02, ...)


def lighting_records(raw: bytes) -> list[bytes]:
    """The four 11-byte lighting records of a profile sector."""
    return [bytes(raw[O_LIGHT + i * LIGHT_LEN:O_LIGHT + (i + 1) * LIGHT_LEN]) for i in range(N_LIGHT)]


def fixed_color(rec: bytes) -> tuple[int, int, int] | None:
    """Colour of a fixed-colour record, None for any other mode."""
    return tuple(rec[1:4]) if len(rec) >= 4 and rec[0] == LIGHT_FIXED else None


def with_fixed_color(raw: bytes, rgb: tuple[int, int, int], slot: int | None = None) -> bytes:
    """Return the sector with fixed-colour records (all four, or just ``slot``); CRC recomputed."""
    out = bytearray(raw)
    for i in ([slot] if slot is not None else range(N_LIGHT)):
        o = O_LIGHT + i * LIGHT_LEN
        out[o:o + LIGHT_LEN] = bytes([LIGHT_FIXED, *rgb, 0x02]) + bytes(LIGHT_LEN - 5)
    return seal(bytes(out))


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE as used by the onboard memory."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def seal(sector: bytes) -> bytes:
    """Return sector with a fresh CRC in its last two bytes."""
    body = bytes(sector[:-2])
    return body + struct.pack(">H", crc16(body))


def parse_profile(index: int, sector: int, enabled: bool, raw: bytes,
                  button_count: int = N_BUTTONS) -> Profile:
    rate = raw[O_RATE]
    dpis = list(struct.unpack("<5H", raw[O_DPIS:O_DPIS + 10]))
    btns = [ButtonBinding(bytes(raw[O_BUTTONS + 4 * i:O_BUTTONS + 4 * i + 4]))
            for i in range(min(N_BUTTONS, button_count))]
    name = raw[O_NAME:O_NAME + NAME_LEN].decode("utf-16-le", "ignore")
    name = name.split("\0")[0] or f"Profile {index + 1}"
    return Profile(index, sector, enabled, name, rate, dpis,
                   raw[O_DPI_DEF], raw[O_DPI_SHIFT], tuple(raw[O_COLOR:O_COLOR + 3]),
                   btns, bytes(raw))


@dataclass
class Description:
    memory_model: int
    profile_format: int
    macro_format: int
    profile_count: int
    profile_count_oob: int
    button_count: int
    sector_count: int
    sector_size: int


class Onboard:
    def __init__(self, dev: Hidpp20Device):
        self.dev = dev
        r = dev.call(F.ONBOARD_PROFILES, 0)
        mm, pf, mf, pc, pco, bc, sc, ss = struct.unpack(">BBBBBBBH", r[:9])
        self.desc = Description(mm, pf, mf, pc, pco, bc, sc, ss)

    # -- mode
    def host_mode(self) -> bool:
        return self.dev.call(F.ONBOARD_PROFILES, 2)[0] == 2

    def set_host_mode(self, host: bool) -> None:
        self.dev.call(F.ONBOARD_PROFILES, 1, bytes([2 if host else 1]))

    # -- which profile the device is using (stored in the device, survives power-off)
    def current_sector(self) -> int:
        return struct.unpack(">H", self.dev.call(F.ONBOARD_PROFILES, 4)[:2])[0]

    def set_current_sector(self, sector: int) -> None:
        self.dev.call(F.ONBOARD_PROFILES, 3, struct.pack(">H", sector))

    # -- memory
    def read_sector(self, sector: int) -> bytes:
        size = self.desc.sector_size
        buf = bytearray(size)
        pos = 0
        while pos < size:
            off = min(pos, size - 16)
            r = self.dev.call(F.ONBOARD_PROFILES, 5,
                              struct.pack(">HH", sector, off), long=True)
            buf[off:off + 16] = r[:16]
            pos += 16
        return bytes(buf)

    def write_sector(self, sector: int, data: bytes) -> None:
        size = self.desc.sector_size
        if len(data) != size:
            raise ValueError("wrong sector size")
        self.dev.call(F.ONBOARD_PROFILES, 6,
                      struct.pack(">HHH", sector, 0, size), long=True)
        for i in range(0, size, 16):
            self.dev.call(F.ONBOARD_PROFILES, 7, data[i:i + 16].ljust(16, b"\0"),
                          long=True)
        self.dev.call(F.ONBOARD_PROFILES, 8, b"", long=True)

    def directory(self) -> list[tuple[int, bool]]:
        """Entries (sector, enabled) of the profile directory (sector 0)."""
        raw = self.read_sector(0)
        out = []
        for i in range(0, len(raw) - 3, 4):
            sec = struct.unpack(">H", raw[i:i + 2])[0]
            if sec == 0xFFFF:
                break
            out.append((sec, bool(raw[i + 2])))
        return out

    # -- profiles
    def read_profiles(self) -> list[Profile]:
        profiles = []
        for i, (sector, enabled) in enumerate(self.directory()):
            raw = self.read_sector(sector)
            profiles.append(self.parse(i, sector, enabled, raw))
        return profiles

    def parse(self, index: int, sector: int, enabled: bool, raw: bytes) -> Profile:
        return parse_profile(index, sector, enabled, raw, self.desc.button_count or N_BUTTONS)

    def encode(self, p: Profile) -> bytes:
        raw = bytearray(p.raw)
        if p.report_rate_ms:
            raw[O_RATE] = p.report_rate_ms
        raw[O_DPI_DEF], raw[O_DPI_SHIFT] = p.dpi_default, p.dpi_shift
        raw[O_DPIS:O_DPIS + 10] = struct.pack("<5H", *p.dpis)
        raw[O_COLOR:O_COLOR + 3] = bytes(p.color)
        for i, b in enumerate(p.buttons[:N_BUTTONS]):
            raw[O_BUTTONS + 4 * i:O_BUTTONS + 4 * i + 4] = b.raw
        nm = p.name.encode("utf-16-le")[:NAME_LEN - 2].ljust(NAME_LEN, b"\0")
        raw[O_NAME:O_NAME + NAME_LEN] = nm
        return seal(bytes(raw))

    def set_profile_color(self, p: Profile, rgb: tuple[int, int, int], slot: int | None = None) -> bytes:
        """Store a fixed lighting colour in the profile's sector; returns the written sector."""
        data = with_fixed_color(self.read_sector(p.sector), rgb, slot)
        self.write_sector(p.sector, data)
        return data

    def write_profile(self, p: Profile) -> None:
        self.write_sector(p.sector, self.encode(p))
