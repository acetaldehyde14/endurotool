; iRacing Enduro Monitor — Inno Setup Script
; Download Inno Setup: https://jrsoftware.org/isinfo.php
; Compile: open this file in Inno Setup Compiler → Build

[Setup]
AppName=iRacing Enduro Monitor
AppVersion=1.0.0
AppPublisher=Your Team Name
AppPublisherURL=https://your-server.com
DefaultDirName={autopf}\iRacingEnduro
DefaultGroupName=iRacing Enduro Monitor
OutputBaseFilename=iRacingEnduro-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\iRacingEnduro.exe
PrivilegesRequired=lowest    ; no admin needed

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Assumes you've run PyInstaller first → dist\iRacingEnduro.exe exists
Source: "dist\iRacingEnduro.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\iRacing Enduro Monitor"; Filename: "{app}\iRacingEnduro.exe"
Name: "{group}\Uninstall iRacing Enduro"; Filename: "{uninstallexe}"
Name: "{commondesktop}\iRacing Enduro Monitor"; Filename: "{app}\iRacingEnduro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Registry]
; Auto-start on Windows login (current user only — no admin required)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "iRacingEnduro"; \
  ValueData: """{app}\iRacingEnduro.exe"""; \
  Flags: uninsdeletevalue

[Run]
Filename: "{app}\iRacingEnduro.exe"; \
  Description: "Launch iRacing Enduro Monitor now"; \
  Flags: nowait postinstall skipifsilent
