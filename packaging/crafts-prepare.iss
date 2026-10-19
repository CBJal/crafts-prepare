; Per-user Windows x64 installer. No admin, service, firewall, or autostart.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\CraftsPrepare"
#endif
#ifndef OutputDir
  #define OutputDir "output"
#endif

[Setup]
AppId={{A7B1E4C2-6D58-4F0A-9C33-8E2F5A1B7D44}}
AppName=Crafts Prepare
AppVersion={#AppVersion}
AppPublisher=Crafts Prepare
DefaultDirName={localappdata}\Programs\Crafts Prepare
DefaultGroupName=Crafts Prepare
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=CraftsPrepare-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\CraftsPrepare.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=PREREQUISITES.txt
DisableWelcomePage=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Crafts Prepare"; Filename: "{app}\CraftsPrepare.exe"
Name: "{autodesktop}\Crafts Prepare"; Filename: "{app}\CraftsPrepare.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CraftsPrepare.exe"; Description: "Launch Crafts Prepare"; Flags: nowait postinstall skipifsilent
