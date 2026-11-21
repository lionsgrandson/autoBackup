#define MyAppName "AutoBackup"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "CodeCrafter"

[Setup]
AppId={{C21C8963-4FC2-457A-9CF7-4F923D99EBA0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\CodeCrafter\AutoBackup
DefaultGroupName=AutoBackup
OutputDir=..\output
OutputBaseFilename=AutoBackup-Setup-Windows-x64
UninstallDisplayIcon={app}\AutoBackup.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Files]
Source: "..\dist\AutoBackup\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\AutoBackup"; Filename: "{app}\AutoBackup.exe"
Name: "{autodesktop}\AutoBackup"; Filename: "{app}\AutoBackup.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\AutoBackup.exe"; Description: "Launch AutoBackup"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN ""CodeCrafter AutoBackup - 30 day check"""; Flags: runhidden
