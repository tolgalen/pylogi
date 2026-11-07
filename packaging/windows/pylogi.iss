; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (C) 2026 pyLogi contributors
; Inno Setup script: installs the PyInstaller folder (dist\pylogi) with Start Menu / desktop shortcuts.
;   iscc /DAppVersion=0.2.0 packaging\windows\pylogi.iss        (scripts\build_release.py does this)
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppName=pyLogi
AppVersion={#AppVersion}
AppPublisher=pyLogi contributors
DefaultDirName={autopf}\pyLogi
DefaultGroupName=pyLogi
UninstallDisplayIcon={app}\pylogi.exe
OutputDir=..\..\dist
OutputBaseFilename=pyLogi-{#AppVersion}-setup
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\src\pylogi\data\pylogi.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\..\dist\pylogi\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\pyLogi"; Filename: "{app}\pylogi.exe"
Name: "{autodesktop}\pyLogi"; Filename: "{app}\pylogi.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start pyLogi with Windows (minimized in the tray)"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "pyLogi"; \
    ValueData: """{app}\pylogi.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\pylogi.exe"; Description: "Start pyLogi"; Flags: nowait postinstall skipifsilent
