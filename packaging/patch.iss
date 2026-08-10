#define AppName "Vector Radio Patch"
#define AppVersion "1.0.2"

[Setup]
AppId={{A71517CA-8A77-4CB4-9D40-AD99D801945B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName=Vector Radio Patch {#AppVersion}
AppPublisher=Vector Radio
DefaultDirName={localappdata}\Programs\Vector Radio
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Vector_Radio_Patch_{#AppVersion}
SetupIconFile=assets\vector-radio.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Global\VectorRadioSingleInstance
Uninstallable=no
MinVersion=10.0.17763

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Qwen_python_20260804_4sskbslqs.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\backend\*"; DestDir: "{app}\backend"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\ui\*"; DestDir: "{app}\ui"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\scripts\migrate_local_library.py"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\docs\PATCH_1.0.2.md"; DestDir: "{app}"; DestName: "PATCH_1.0.2.md"; Flags: ignoreversion

[Run]
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\tools\migrate_local_library.py"" ""{userdesktop}\localRadio"" ""{app}"""; WorkingDir: "{app}"; StatusMsg: "Перенесення наявної локальної бібліотеки..."; Flags: runhidden waituntilterminated; Check: LegacyLibraryExists
Filename: "{app}\VectorRadio.exe"; Description: "Запустити оновлене Vector Radio"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function InstalledRoot: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\Vector Radio');
end;

function LegacyLibraryExists: Boolean;
var
  LegacyRoot: String;
begin
  LegacyRoot := ExpandConstant('{userdesktop}\localRadio');
  Result :=
    FileExists(LegacyRoot + '\data\radio.db') and
    (CompareText(LegacyRoot, InstalledRoot) <> 0);
end;

function InitializeSetup: Boolean;
begin
  Result :=
    FileExists(InstalledRoot + '\VectorRadio.exe') and
    FileExists(InstalledRoot + '\runtime\python.exe');
  if not Result then
    MsgBox(
      'Vector Radio не знайдено. Спочатку встановіть повну версію.',
      mbError,
      MB_OK
    );
end;
