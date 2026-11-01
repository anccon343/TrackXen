; Inno Setup script for the TrackXen installer (Inno Setup 6: https://jrsoftware.org/isinfo.php).
; Build: packaging\build.ps1  (or: ISCC packaging\trackxen.iss after the PyInstaller build in dist\TrackXen)
;
; The person installing chooses "only for me" (no administrator needed: %LOCALAPPDATA%\Programs) or
; "for all users" (Program Files; also adds a Windows Firewall rule so tablets and phones on the
; private network can open the UI on port 8090; for "only for me" Windows asks on the first start).
; Start-menu (and optional desktop) shortcuts, an optional start-with-Windows entry.
; NVIDIA's libraries are not in the setup: TrackXen downloads the ones the PC needs on its first
; start (%LOCALAPPDATA%\TrackXen\gpu-libs). Settings, logs, registered faces and the TensorRT
; engines built for the GPU live in %APPDATA%\TrackXen; both are kept on uninstall.

#define AppName "TrackXen"
#define AppVersion "0.1.0"

[Setup]
AppId={{7C1B4D2E-5A0F-4C8B-9E3D-2F6A8B1C4D70}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\installer-output
OutputBaseFilename=TrackXen-Setup-{#AppVersion}
SetupIconFile=trackxen.ico
UninstallDisplayIcon={app}\TrackXen.exe
LicenseFile=..\LICENSE-NOTICE.txt
WizardStyle=modern
Compression=lzma2/normal
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start TrackXen when I sign in to Windows"; GroupDescription: "Start:"; Flags: unchecked

[Files]
Source: "..\dist\TrackXen\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TrackXen.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\TrackXen.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\TrackXen.exe"" --no-browser"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""TrackXen"" dir=in action=allow program=""{app}\TrackXen.exe"" enable=yes profile=private,domain"; \
  Flags: runhidden; StatusMsg: "Allowing tablets on the network to reach TrackXen..."; Check: IsAdminInstallMode
Filename: "{app}\TrackXen.exe"; Description: "Start TrackXen now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM TrackXen.exe"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""TrackXen"""; Flags: runhidden; \
  RunOnceId: "DelFirewall"; Check: IsAdminInstallMode

[Messages]
FinishedLabel=Setup has finished installing [name] on your computer.%n%nOn its first start TrackXen downloads NVIDIA's graphics libraries for this PC (about 1.5 GB, once), so it needs an internet connection then. Until the download is done it runs on the processor.

[Code]
const
  DisplayClass = 'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}';

function DriverDesc(Subkey: String): String;
begin
  if not RegQueryStringValue(HKLM, DisplayClass + '\' + Subkey, 'DriverDesc', Result) then
    Result := '';
end;

function HasNvidiaGpu(): Boolean;
var
  Names: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if RegGetSubkeyNames(HKLM, DisplayClass, Names) then
    for I := 0 to GetArrayLength(Names) - 1 do
      if Pos('NVIDIA', Uppercase(DriverDesc(Names[I]))) > 0 then
        Result := True;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not HasNvidiaGpu() and not WizardSilent() then
    Result := MsgBox('No NVIDIA graphics card was found. TrackXen still runs, but on the CPU it can only follow ' +
                     'people slowly (an NVIDIA RTX card is recommended). Install anyway?', mbConfirmation, MB_YESNO) = IDYES;
end;
