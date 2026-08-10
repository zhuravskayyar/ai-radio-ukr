#define AppName "Vector Radio"
#define AppVersion "1.0.5"
#define AppPublisher "Vector Radio"
#define PythonArchive "python-3.11.9-embed-amd64.zip"
#define WebViewInstaller "MicrosoftEdgeWebview2Setup.exe"

[Setup]
AppId={{CB07F177-A5C4-41A4-9EC0-4EA77D15A404}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Vector Radio
DefaultGroupName=Vector Radio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Vector_Radio_Setup_{#AppVersion}
SetupIconFile=assets\vector-radio.ico
UninstallDisplayIcon={app}\VectorRadio.exe
Compression=lzma2/ultra64
SolidCompression=yes
ArchiveExtraction=full
ExtraDiskSpaceRequired=3000000000
WizardStyle=modern
WizardSizePercent=110
CloseApplications=yes
RestartApplications=no
AppMutex=Global\VectorRadioSingleInstance
MinVersion=10.0.17763

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Створити ярлик на робочому столі"; GroupDescription: "Додаткові ярлики:"; Flags: unchecked

[Files]
Source: "assets\VectorRadio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\vector-radio.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "requirements-runtime.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements-tts.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "install_tts.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "tts-sources\styletts2-inference.zip"; DestDir: "{tmp}\tts-sources"; Flags: ignoreversion deleteafterinstall
Source: "tts-sources\ukrainian-word-stress.zip"; DestDir: "{tmp}\tts-sources"; Flags: ignoreversion deleteafterinstall
Source: "tts-sources\ipa-uk.zip"; DestDir: "{tmp}\tts-sources"; Flags: ignoreversion deleteafterinstall
Source: "tts-sources\ukrainian-accentor.zip"; DestDir: "{tmp}\tts-sources"; Flags: ignoreversion deleteafterinstall
Source: "https://www.python.org/ftp/python/3.11.9/{#PythonArchive}"; DestDir: "{app}\runtime"; DestName: "{#PythonArchive}"; ExternalSize: 11249023; Hash: "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"; Flags: external download extractarchive ignoreversion recursesubdirs createallsubdirs; Check: NeedPrivatePython
Source: "python311._pth"; DestDir: "{app}\runtime"; Flags: ignoreversion
Source: "get-pip.py"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall; Check: NeedPip
Source: "..\main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Qwen_python_20260804_4sskbslqs.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\backend\*"; DestDir: "{app}\backend"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\ui\*"; DestDir: "{app}\ui"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\data\music-stories.json"; DestDir: "{app}\data"; Flags: ignoreversion
Source: "..\data\playlist.json"; DestDir: "{app}\data"; Flags: ignoreversion
Source: "..\data\tts_pronunciations.json"; DestDir: "{app}\data"; Flags: ignoreversion
Source: "..\api.example.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\INSTALL_WINDOWS.md"; DestDir: "{app}"; DestName: "API_AND_INSTALL.md"; Flags: ignoreversion
Source: "https://go.microsoft.com/fwlink/p/?LinkId=2124703"; DestDir: "{tmp}"; DestName: "{#WebViewInstaller}"; ExternalSize: 2000000; Flags: external download ignoreversion deleteafterinstall; Check: NeedWebView2

[Dirs]
Name: "{app}\cache"
Name: "{app}\downloads"
Name: "{app}\music"

[Icons]
Name: "{autoprograms}\Vector Radio"; Filename: "{app}\VectorRadio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\vector-radio.ico"
Name: "{autoprograms}\Vector Radio — інструкція API"; Filename: "{app}\API_AND_INSTALL.md"; WorkingDir: "{app}"
Name: "{autodesktop}\Vector Radio"; Filename: "{app}\VectorRadio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\vector-radio.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\VectorRadio.exe"; Description: "Запустити Vector Radio"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function NeedPrivatePython: Boolean;
begin
  Result :=
    not FileExists(ExpandConstant('{app}\runtime\python.exe')) or
    not FileExists(ExpandConstant('{app}\runtime\pythonw.exe'));
end;

function NeedPip: Boolean;
begin
  Result := not FileExists(ExpandConstant('{app}\runtime\Lib\site-packages\pip\__init__.py'));
end;

function WebViewVersion(RootKey: Integer; Subkey: String): String;
begin
  Result := '';
  RegQueryStringValue(RootKey, Subkey, 'pv', Result);
end;

function IsUsefulVersion(Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function NeedWebView2: Boolean;
var
  ClientId: String;
begin
  ClientId := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := not (
    IsUsefulVersion(WebViewVersion(HKCU, ClientId)) or
    IsUsefulVersion(WebViewVersion(HKLM32, ClientId)) or
    IsUsefulVersion(WebViewVersion(HKLM64, ClientId))
  );
end;

procedure RunChecked(
  FileName: String; Parameters: String; StatusText: String; AllowRestartCode: Boolean
);
var
  ResultCode: Integer;
  Succeeded: Boolean;
  Attempt: Integer;
begin
  for Attempt := 1 to 3 do
  begin
    WizardForm.StatusLabel.Caption :=
      StatusText + ' (спроба ' + IntToStr(Attempt) + '/3)';
    Succeeded := Exec(
      FileName,
      Parameters,
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
    if Succeeded and (
      (ResultCode = 0) or
      (AllowRestartCode and ((ResultCode = 1641) or (ResultCode = 3010)))
    ) then
      Exit;
    if Attempt < 3 then
      Sleep(2000);
  end;
  if not Succeeded then
    RaiseException('Не вдалося запустити компонент: ' + FileName);
  RaiseException(
    'Компонент завершився з помилкою ' + IntToStr(ResultCode) + ': ' + FileName
  );
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if NeedWebView2 then
      RunChecked(
        ExpandConstant('{tmp}\{#WebViewInstaller}'),
        '/silent /install',
        'Встановлення Microsoft Edge WebView2 Runtime...',
        True
      );
    if NeedPip then
      RunChecked(
        ExpandConstant('{app}\runtime\python.exe'),
        '"' + ExpandConstant('{tmp}\get-pip.py') +
          '" --disable-pip-version-check --no-warn-script-location',
        'Підготовка приватного Python 3.11...',
        False
      );
    RunChecked(
      ExpandConstant('{app}\runtime\python.exe'),
      '-m pip install --disable-pip-version-check --no-warn-script-location --upgrade -r "' +
        ExpandConstant('{app}\requirements-runtime.txt') + '"',
      'Встановлення компонентів Vector Radio...',
      False
    );
    RunChecked(
      ExpandConstant('{app}\runtime\python.exe'),
      '-m pip install --disable-pip-version-check --no-warn-script-location ' +
        '--index-url https://download.pytorch.org/whl/cpu ' +
        'torch==2.11.0+cpu torchaudio==2.11.0+cpu',
      'Встановлення локального рушія голосу Adam Vector...',
      False
    );
    RunChecked(
      ExpandConstant('{app}\runtime\python.exe'),
      '-m pip install --disable-pip-version-check --no-warn-script-location --upgrade -r "' +
        ExpandConstant('{app}\requirements-tts.txt') + '"',
      'Встановлення українських мовних компонентів...',
      False
    );
    RunChecked(
      ExpandConstant('{app}\runtime\python.exe'),
      '-m pip install --disable-pip-version-check --no-warn-script-location --no-deps ' +
        '"' + ExpandConstant('{tmp}\tts-sources\ukrainian-accentor.zip') + '" ' +
        '"' + ExpandConstant('{tmp}\tts-sources\ipa-uk.zip') + '" ' +
        '"' + ExpandConstant('{tmp}\tts-sources\ukrainian-word-stress.zip') + '" ' +
        '"' + ExpandConstant('{tmp}\tts-sources\styletts2-inference.zip') + '"',
      'Підключення українського StyleTTS2...',
      False
    );
    RunChecked(
      ExpandConstant('{app}\runtime\python.exe'),
      '"' + ExpandConstant('{app}\install_tts.py') + '"',
      'Завантаження та перевірка голосу Adam Vector...',
      False
    );
  end;
end;
