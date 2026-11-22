; Inno-Setup-Skript für den Windows-Installer von CycleOverlay.
; Wird von build.ps1 aufgerufen:  ISCC.exe /DAppVersion=1.4.0 cycleoverlay.iss
; Erwartet das fertige PyInstaller-Ergebnis in ..\..\dist\CycleOverlay\

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B0F3E52-7C1A-4E7B-9A3D-2C8E5F1B7D40}
AppName=CycleOverlay
AppVersion={#AppVersion}
AppVerName=CycleOverlay {#AppVersion}
AppPublisher=Nikola
DefaultDirName={autopf}\CycleOverlay
DefaultGroupName=CycleOverlay
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\CycleOverlay.exe
UninstallDisplayName=CycleOverlay {#AppVersion}
OutputDir=Output
OutputBaseFilename=CycleOverlay-Setup-{#AppVersion}
SetupIconFile=cycleoverlay.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installation für alle Benutzer (Admin) oder nur für mich (ohne Admin) wählbar
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
; ältere Version beim Update sauber ersetzen
CloseApplications=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "CycleOverlay-Projekte (.cyov) mit CycleOverlay öffnen"; GroupDescription: "Dateizuordnung:"

[InstallDelete]
; Reste einer älteren Version im Programmordner entfernen (Benutzerdaten liegen woanders)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\CycleOverlay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CycleOverlay"; Filename: "{app}\CycleOverlay.exe"
Name: "{group}\{cm:UninstallProgram,CycleOverlay}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CycleOverlay"; Filename: "{app}\CycleOverlay.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.cyov"; ValueType: string; ValueName: ""; ValueData: "CycleOverlay.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CycleOverlay.Project"; ValueType: string; ValueName: ""; ValueData: "CycleOverlay-Projekt"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CycleOverlay.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\CycleOverlay.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CycleOverlay.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\CycleOverlay.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\CycleOverlay.exe"; Description: "{cm:LaunchProgram,CycleOverlay}"; Flags: nowait postinstall skipifsilent
; Update aus der App (/SILENT): danach automatisch wieder starten
Filename: "{app}\CycleOverlay.exe"; Flags: nowait runasoriginaluser; Check: WizardSilent
