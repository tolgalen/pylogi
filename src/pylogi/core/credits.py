# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Credits and third-party licences: the single source for the About dialog, README and THIRD_PARTY.md."""
from __future__ import annotations

from dataclasses import dataclass

LICENSE_ID = "GPL-3.0-or-later"
LICENSE_NAME = "GNU General Public License, version 3 or (at your option) any later version"
COPYRIGHT = "Copyright (C) 2026 pyLogi contributors"

WARRANTY = ("pyLogi is free software: you can redistribute it and/or modify it under the terms of the GNU General "
            "Public License as published by the Free Software Foundation, either version 3 of the License, or (at "
            "your option) any later version.\n\n"
            "pyLogi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the "
            "implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public "
            "License for more details.")

TRADEMARKS = ("Logitech, Logi, Unifying, Bolt, LIGHTSPEED, G HUB and Logi Options+ are trademarks of Logitech Europe "
              "S.A. and/or its affiliates. pyLogi is an independent project: it is not affiliated with, endorsed by "
              "or supported by Logitech.")


@dataclass(frozen=True)
class Component:
    name: str
    license: str
    role: str
    url: str = ""


COMPONENTS: list[Component] = [
    Component("Python", "PSF-2.0", "Language runtime and standard library (unittest, argparse, ctypes, ...).",
              "https://www.python.org/"),
    Component("Qt 6 / PySide6 (Qt for Python)", "LGPL-3.0-only (Qt is also available under GPL and commercial terms)",
              "GUI toolkit, used unmodified through the PySide6 wheels.", "https://doc.qt.io/qtforpython-6/"),
    Component("hidapi (optional)", "BSD-3-Clause / GPL-3.0 / HIDAPI licence (your choice)",
              "HID transport on Windows only; Linux talks to /dev/hidraw directly.",
              "https://github.com/libusb/hidapi"),
    Component("Solaar", "GPL-2.0-or-later",
              "Reference for the HID++ protocol: register and feature layouts were cross-checked against its public "
              "source. No Solaar code is included.", "https://github.com/pwr-Solaar/Solaar"),
    Component("OpenLogi", "MIT OR Apache-2.0 (code); design assets are all rights reserved",
              "Inspiration for the user interface, and the public asset mirror (assets.openlogi.org) that serves the "
              "product photos and button-hotspot metadata. No OpenLogi code or design asset is included.",
              "https://github.com/AprilNEA/OpenLogi"),
    Component("Logitech product photos and device metadata", "Property of Logitech; not covered by the GPL",
              "Downloaded at run time only with your consent and cached on your computer. They are never "
              "redistributed with pyLogi."),
]
