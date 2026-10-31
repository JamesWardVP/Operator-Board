; Inno Setup script for the Windows installer: OperatorBoard-Setup-<version>.exe
; Built by packaging/build.py --installer (and the GitHub Actions release build):
;   ISCC.exe /DAppVersion=0.2.2 /DSourceDir=<dist\OperatorBoard> /DOutputDir=<dist> operator_board.iss
;
; Installs for the current user by default (no administrator prompt); administrators can
; choose "all users" instead. Settings in %USERPROFILE%\.operator_board are kept on uninstall.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=path to dist\OperatorBoard
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades and the uninstaller.
AppId={{A97D2F1D-752D-4D19-BC23-99BE59BC0C49}
AppName=Operator Board
AppVersion={#AppVersion}
AppVerName=Operator Board {#AppVersion}
AppPublisher=JamesWardVP
AppPublisherURL=https://github.com/JamesWardVP/Operator-Board
AppSupportURL=https://github.com/JamesWardVP/Operator-Board/issues
AppUpdatesURL=https://github.com/JamesWardVP/Operator-Board/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Operator Board
DefaultGroupName=Operator Board
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=operator_board.ico
UninstallDisplayIcon={app}\OperatorBoard.exe
UninstallDisplayName=Operator Board
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=OperatorBoard-Setup-{#AppVersion}
; Close a running Operator Board before replacing its files.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Remove the previous version's runtime first so no stale libraries are left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Operator Board"; Filename: "{app}\OperatorBoard.exe"
Name: "{autodesktop}\Operator Board"; Filename: "{app}\OperatorBoard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OperatorBoard.exe"; Description: "{cm:LaunchProgram,Operator Board}"; Flags: nowait postinstall skipifsilent
