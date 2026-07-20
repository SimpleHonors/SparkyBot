; SparkyBot Inno Setup 6 installer script
;
; Build order:
;   1. Run build\build_windows.bat first (produces dist\SparkyBot\)
;   2. From repo root: iscc build\sparkybot.iss
;
; AppVersion must track core/version.py — update the literal below on every release.

#define MyAppName "SparkyBot"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "SimpleHonors"
#define MyAppURL "https://github.com/SimpleHonors/SparkyBot"
#define MyAppExeName "SparkyBot.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Classic root-level install. Admin once at install time; the [Dirs]
; ACL below then lets the app write config/logs/updates beside itself
; without elevation (Program Files would forbid that).
DefaultDirName={sd}\{#MyAppName}
; Do not reuse the previous (AppData) install path from the registry —
; existing installs must move to C:\SparkyBot on upgrade.
UsePreviousAppDir=no
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=SparkyBot-v{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=yes
DisableProgramGroupPage=yes

[Dirs]
; Grant normal users write access so the app can keep its config, logs,
; caches, and self-updates next to the exe like it always has.
Name: "{app}"; Permissions: users-modify

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\SparkyBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked
