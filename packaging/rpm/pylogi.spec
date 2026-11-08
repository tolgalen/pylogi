# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
#
# Fedora package (noarch): pyLogi runs on the distribution's own PySide6.
# Build:  python scripts/build_rpm.py        (RPM and SRPM end up in ./dist)
%{!?pylogi_version: %global pylogi_version 0.3.3}

Name:           pylogi
Version:        %{pylogi_version}
Release:        1%{?dist}
Summary:        Qt manager for Logitech mice, keyboards and webcams (HID++)
License:        GPL-3.0-or-later
URL:            https://github.com/tolgalen/pylogi
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools
BuildRequires:  desktop-file-utils
BuildRequires:  systemd-rpm-macros
# PySide6 is packaged by Fedora as python3-pyside6 (Essentials + Addons)
Requires:       python3-pyside6
Requires:       hicolor-icon-theme
Recommends:     xorg-x11-server-utils

%description
pyLogi is a cross-platform desktop application to manage Logitech mice, keyboards
and webcams over USB, Bluetooth and the Unifying, Bolt and LIGHTSPEED receivers,
without any Logitech software: buttons and software actions, pointer and DPI,
onboard profiles, per-application profiles, lighting, webcam controls, battery,
Easy-Switch and receiver pairing.

Logitech, Logi, Unifying, Bolt and LIGHTSPEED are trademarks of Logitech Europe S.A.
pyLogi is an independent project and is not affiliated with Logitech.

%prep
%autosetup -n %{name}-%{version}
# Fedora ships PySide6 as one package (python3dist(pyside6)), not as -Essentials / -Addons
sed -i 's/"PySide6-Essentials>=6.5", "PySide6-Addons>=6.5"/"PySide6>=6.5"/' pyproject.toml

%generate_buildrequires
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files pylogi

# application menu entry and icons
install -Dpm 0644 src/pylogi/data/pylogi.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/pylogi.svg
for s in 16 24 32 48 64 128 256 512; do
  install -Dpm 0644 src/pylogi/data/icons/pylogi-$s.png \
      %{buildroot}%{_datadir}/icons/hicolor/${s}x${s}/apps/pylogi.png
done
cat > pylogi.desktop <<EOF
[Desktop Entry]
Type=Application
Name=pyLogi
Comment=Manage Logitech mice, keyboards and webcams
Exec=pylogi
Icon=pylogi
Terminal=false
Categories=Settings;HardwareSettings;
StartupWMClass=pylogi
EOF
desktop-file-install --dir=%{buildroot}%{_datadir}/applications pylogi.desktop

# optional background service (enable with: pylogi --service on)
install -Dpm 0644 packaging/systemd/pylogi.service %{buildroot}%{_userunitdir}/pylogi.service

# access to the HID++ interfaces and /dev/uinput for the logged-in user
install -Dpm 0644 packaging/42-pylogi.rules %{buildroot}%{_udevrulesdir}/42-pylogi.rules

%check
%pyproject_check_import -e 'pylogi.ui*' -e 'pylogi.app' -e 'pylogi.__main__'
desktop-file-validate %{buildroot}%{_datadir}/applications/pylogi.desktop

%post
udevadm control --reload >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=hidraw --subsystem-match=misc >/dev/null 2>&1 || :

%files -f %{pyproject_files}
%license LICENSE
%doc README.md THIRD_PARTY.md
%{_bindir}/pylogi
%{_datadir}/applications/pylogi.desktop
%{_datadir}/icons/hicolor/*/apps/pylogi.*
%{_udevrulesdir}/42-pylogi.rules
%{_userunitdir}/pylogi.service

%changelog
* Sun Nov 08 2026 pyLogi contributors <candion@gmail.com> - 0.3.3-1
- The camera preview grows with the width of the window and shrinks to fit a short one

* Sun Nov 08 2026 pyLogi contributors <candion@gmail.com> - 0.3.2-1
- Webcams: saved settings are written again on plug-in, video format, editable presets, presets by application,
  auto-adjust and lock, viewing aids, tray menu and command line
- Debian / Ubuntu package

* Sun Nov 08 2026 pyLogi contributors <candion@gmail.com> - 0.3.1-1
- Background service (pylogi --service on), Wayland window detection on Sway, Hyprland, Niri and KDE
- Command line reads and toggles, Unifying pairing dialog, unpair by name, firmware shown
- The Offline notice is red

* Sat Nov 07 2026 pyLogi contributors <candion@gmail.com> - 0.3.0-1
- Macros, gestures, thumb wheel; input injection for Windows (unverified on hardware)
- UTF-8 for every text file, listener-thread shutdown fix, safer image downloads

* Sat Nov 07 2026 pyLogi contributors <candion@gmail.com> - 0.2.0-1
- Fedora package: noarch, on the distribution's PySide6; udev rules, menu entry and icons included
