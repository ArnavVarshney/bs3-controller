; BS3 Controller — Windows installer (Inno Setup 6).
;
; Build (from the repo root, after building the exes + staging LHM):
;   pyinstaller packaging\bs3-web.spec
;   powershell -File packaging\fetch-lhm.ps1
;   iscc packaging\bs3-setup.iss            [/DAppVersion=0.2.0 for releases]
;
; Output: dist\bs3-controller-setup-<version>.exe
;
; Design notes:
; - x64 only (bleak WinRT backend); localhost-only server, no firewall rule.
; - LHM ships UNMODIFIED next to our exes (MPL-2.0, see LHM-ATTRIBUTION.txt);
;   run it once as admin so its WMI provider feeds CPU temps to bs3-web.
; - Autostart entries are opt-in Tasks (unchecked by default) under HKLM,
;   removed on uninstall. HKLM (not HKCU) so there is no per-user warning
;   under the admin install this package needs (Program Files + LHM driver).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppName=BS3 Controller
AppVersion={#AppVersion}
AppPublisher=Arnav Varshney
AppPublisherURL=https://github.com/ArnavVarshney/bs3-controller
DefaultDirName={autopf}\BS3 Controller
DefaultGroupName=BS3 Controller
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=bs3-controller-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
UninstallDisplayName=BS3 Controller
WizardStyle=modern

[Files]
Source: "..\dist\bs3-web.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\bs3-webw.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\bs3ctl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\LHM-ATTRIBUTION.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\stage\lhm\*"; DestDir: "{app}\lhm"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BS3 Backend (start server)"; Filename: "{app}\bs3-web.exe"; Parameters: "--transport ble --lhm"; WorkingDir: "{app}"
Name: "{group}\BS3 Dashboard (open browser)"; Filename: "http://127.0.0.1:8765/"
Name: "{group}\LibreHardwareMonitor"; Filename: "{app}\lhm\LibreHardwareMonitor.exe"; Comment: "Run once as admin: feeds CPU temps to the BS3 backend"
Name: "{group}\Uninstall BS3 Controller"; Filename: "{uninstallexe}"

[Tasks]
Name: "backend_autostart"; Description: "Start the BS3 backend silently when I log in (no window; recommended)"; GroupDescription: "Startup:"; Flags: exclusive
Name: "backend_console_autostart"; Description: "Start the BS3 backend with a visible window when I log in"; GroupDescription: "Startup:"; Flags: exclusive unchecked
Name: "no_autostart"; Description: "Do not start anything at login"; GroupDescription: "Startup:"; Flags: exclusive unchecked
Name: "lhm_elevated"; Description: "Run LibreHardwareMonitor elevated at login (required for CPU temps; consent once, silent after)"; Flags: checkedonce

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "BS3Web"; ValueData: """{app}\bs3-webw.exe"" --transport ble --lhm"; Tasks: backend_autostart; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "BS3Web"; ValueData: """{app}\bs3-web.exe"" --transport ble --lhm"; Tasks: backend_console_autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\lhm\LibreHardwareMonitor.exe"; Description: "Run LibreHardwareMonitor now (elevated: enables CPU temps)"; Flags: postinstall skipifsilent runascurrentuser

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/delete /tn ""BS3 Controller\LibreHardwareMonitor"" /f"; RunOnceId: "DelLhmTask"; Flags: runhidden

[Code]
procedure CurStepChanged(CurStepID: TSetupStep);
var
  ResultCode: Integer;
begin
  { Elevated logon task for LHM: silent UAC (consent given to this admin
    installer once), runs elevated at every login. The backend can never
    elevate LHM itself (Error 740), so this task owns the lifecycle. }
  if (CurStepID = ssPostInstall) and WizardIsTaskSelected('lhm_elevated') then
    Exec('schtasks.exe',
      '/create /tn "BS3 Controller\LibreHardwareMonitor" /tr "' +
        ExpandConstant('{app}\lhm\LibreHardwareMonitor.exe') +
        '" /sc onlogon /rl highest /f',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
