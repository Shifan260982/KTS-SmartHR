; SmartHR installer. Built by packaging\build.ps1 from build\stage.
; No questions: per-user install (no admin), progress bar, then SmartHR starts and opens in the browser.
#define Stage "..\build\stage"
#define Launch "{app}\python\python.exe"

[Setup]
AppId={{6F0C2B7E-6A1E-4E8B-9C47-5B2D8E1F3A90}
AppName=SmartHR
AppVersion=0.2.0
DefaultDirName={localappdata}\Programs\SmartHR
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
WizardStyle=modern
OutputDir=..\build
OutputBaseFilename=SmartHR-Setup
; Model weights barely compress, so fast compression keeps the build and install quick.
Compression=lzma2/fast
SolidCompression=no
UninstallDisplayName=SmartHR

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\SmartHR"; Filename: "{#Launch}"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"
Name: "{autodesktop}\SmartHR"; Filename: "{#Launch}"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"

[Run]
; Started through Explorer, so SmartHR is not a child of Setup: whatever opened the installer (an email app, a browser)
; sees it finish as soon as the files are in place, instead of waiting for SmartHR to close.
Filename: "{win}\explorer.exe"; Parameters: """{autodesktop}\SmartHR.lnk"""; Flags: nowait

[UninstallRun]
; Stop SmartHR and its bundled Ollama so their files can be removed.
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process python,ollama -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden waituntilterminated; RunOnceId: "StopSmartHR"

[UninstallDelete]
; Uploaded originals and caches written at run time.
Type: filesandordirs; Name: "{app}"
