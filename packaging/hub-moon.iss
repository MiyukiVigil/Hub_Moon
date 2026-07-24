; Inno Setup script — wraps the PyInstaller onedir bundle into a Windows installer.
;
; Build it in two steps, ON WINDOWS:
;   pyinstaller packaging\hub-moon.spec           ->  dist\hub-moon\hub-moon.exe
;   iscc /DAppVersion=0.2.0 packaging\hub-moon.iss ->  dist\HubMoon-Setup-0.2.0.exe
;
; (The GitHub Actions workflow does both on a windows-latest runner.)
;
; Defines can be overridden on the iscc command line with /D<name>=<value>.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\hub-moon"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define MyAppName "Hub Moon"
#define MyAppPublisher "MiyukiVigil"
#define MyAppURL "https://github.com/MiyukiVigil/Hub_Moon"
#define MyAppExeName "hub-moon.exe"

[Setup]
; AppId uniquely identifies this app for upgrades/uninstall — keep it stable.
AppId={{9BAE6DAA-DA8D-421C-A240-7898856F9A53}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\Hub Moon
DefaultGroupName=Hub Moon
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=HubMoon-Setup-{#AppVersion}
SetupIconFile=hub-moon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; PySide6 / Qt6 is 64-bit only
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; the whole PyInstaller onedir bundle (exe + Qt runtime + QML tree)
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Hub Moon"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,Hub Moon}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Hub Moon"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,Hub Moon}"; Flags: nowait postinstall skipifsilent
