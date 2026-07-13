#define MyAppName "Marketing Control"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppExeName "Marketing Control.exe"

[Setup]
AppId={{A8438454-96E4-4AC2-8A50-21394780A703}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Marketing Control
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=installer
OutputBaseFilename=marketing-control-setup-{#MyAppVersion}-x64

[Files]
Source: "..\dist\Marketing Control\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
