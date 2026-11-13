; Teslim Paketi — Windows kurulum sihirbazı (Inno Setup)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E3B2C1A-7F44-4C2E-9D1B-5A0C7E9F2B31}
AppName=Teslim Paketi
AppVersion={#AppVersion}
AppPublisher=Teslim Paketi
DefaultDirName={localappdata}\Programs\Teslim Paketi
DefaultGroupName=Teslim Paketi
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=TeslimPaketi-Kurulum-{#AppVersion}
SetupIconFile=..\..\assets\TeslimPaketi.ico
UninstallDisplayIcon={app}\TeslimPaketi.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\TeslimPaketi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Teslim Paketi"; Filename: "{app}\TeslimPaketi.exe"
Name: "{autodesktop}\Teslim Paketi"; Filename: "{app}\TeslimPaketi.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TeslimPaketi.exe"; Description: "{cm:LaunchProgram,Teslim Paketi}"; Flags: nowait postinstall skipifsilent
