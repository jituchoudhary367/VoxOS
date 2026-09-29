[Setup]
AppName=VoxOS
AppVersion=0.1.0
AppPublisher=VoxOS
AppPublisherURL=https://github.com/jituchoudhary367/VoxOS
DefaultDirName={autopf}\VoxOS
DefaultGroupName=VoxOS
OutputDir=dist
OutputBaseFilename=VoxOS-Setup-v2
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
SetupIconFile=voxos\ui\assets\vox.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start VoxOS automatically with Windows"; GroupDescription: "System Integration";

[Files]
Source: "dist\VoxOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VoxOS"; Filename: "{app}\VoxOS.exe"
Name: "{autodesktop}\VoxOS"; Filename: "{app}\VoxOS.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoxOS"; ValueData: """{app}\VoxOS.exe"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\VoxOS.exe"; Description: "{cm:LaunchProgram,VoxOS}"; Flags: nowait postinstall skipifsilent
