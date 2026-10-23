; Inno Setup script for VIGIL One (per-user install: no administrator rights needed).
; Built by packaging\build_vigil_one.py, which passes /DAppVersion=... and /DHubUrl=...
; The installer ships vigil-one.json next to the app: candidate mode, school's Hub address.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\VIGIL One"
#endif

[Setup]
AppId={{B6E2D3B1-7C1A-4E4F-9A62-5D1E0F3C9A11}
AppName=VIGIL One
AppVersion={#AppVersion}
AppPublisher=VIGIL AI
DefaultDirName={localappdata}\Programs\VIGIL One
DefaultGroupName=VIGIL One
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VIGIL-One-Setup-{#AppVersion}
SetupIconFile=vigil_one.ico
UninstallDisplayIcon={app}\VIGIL One.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "vigil-one.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\VIGIL One"; Filename: "{app}\VIGIL One.exe"
Name: "{autodesktop}\VIGIL One"; Filename: "{app}\VIGIL One.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Run]
Filename: "{app}\VIGIL One.exe"; Description: "Start VIGIL One"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; the exam data lives in %LOCALAPPDATA%\VIGIL One\data and is kept on purpose (outbox may hold unsent items)
Type: filesandordirs; Name: "{app}"
