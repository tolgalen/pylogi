#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
#
# Per-user install for Linux: builds the private virtual environment, puts a `pylogi` command on the
# PATH (a symlink to the launcher) and adds the application-menu entry.  No root needed.
set -euo pipefail
cd "$(dirname "$0")/.."
./pylogi.sh --setup
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/pylogi.sh" "$HOME/.local/bin/pylogi"
./pylogi.sh --install-launcher
echo
echo "Installed: run 'pylogi' (make sure ~/.local/bin is in your PATH) or use the application menu."
echo "Optional (needs root): device permissions for /dev/hidraw* and /dev/uinput"
echo "  sudo cp packaging/42-pylogi.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger"
echo "Optional: start with the session:  pylogi --autostart on"
echo "GNOME/Wayland per-application profiles:  pylogi --install-gnome-extension   (then log out and in)"
