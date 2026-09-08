# Attendance Automation Hub

English | [简体中文](README_CN.md)

A standalone Windows application for Android attendance automation. It provides
randomized daily clock-in, immediate clock-in/clock-out, clock-out update confirmation,
holiday and per-day schedules, attendance history, a desktop panel, scrcpy phone control,
and private remote Web access through Tailscale. The default clock-in window is
`09:00–09:30`.

> The automation operates a real attendance account. Run the safe tests first and make
> sure its use complies with your organization's policies. Test mode does not actively
> click attendance buttons, but Feishu's Quick Clock-in feature may still clock you in
> automatically when the app opens.

## 1. Requirements

- Windows 10/11, running under the currently signed-in desktop user.
- A physical Android 8+ device connected by USB.
- [Python 3.10+](https://www.python.org/downloads/windows/)
- [Microsoft OpenJDK 17](https://learn.microsoft.com/java/openjdk/download)
- [Node.js LTS](https://nodejs.org/en/download)
- [Android Studio / Android SDK](https://developer.android.com/studio)
- [Appium and UiAutomator2](https://appium.io/docs/en/latest/ecosystem/drivers/)
- [scrcpy](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md) (phone display and control)
- [Tailscale](https://tailscale.com/docs/install/windows) (remote access only)

Install the main components with WinGet in PowerShell:

```powershell
winget install --exact --id Python.Python.3.10
winget install --exact --id Microsoft.OpenJDK.17
winget install --exact --id OpenJS.NodeJS.LTS
winget install --exact --id Google.AndroidStudio
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Tailscale.Tailscale
```

### Android SDK

Open Android Studio → `More Actions` → `SDK Manager` → `SDK Tools`, then install:

- Android SDK Platform-Tools
- Android SDK Build-Tools
- Android SDK Command-line Tools (latest)

The SDK is normally located at `%LOCALAPPDATA%\Android\Sdk`. Set the user environment
variables in PowerShell:

```powershell
$sdkPath = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$platformTools = Join-Path $sdkPath "platform-tools"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("ANDROID_HOME", $sdkPath, "User")
[Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $sdkPath, "User")
if (($userPath -split ";") -notcontains $platformTools) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$platformTools", "User")
}
```

The JDK installer should also set `JAVA_HOME`. If diagnostics report that it is missing,
set it to the actual JDK 17 directory and add `%JAVA_HOME%\bin` to the user `Path`.
Reopen PowerShell after changing environment variables.

Install Appium 3 and its Android driver:

```powershell
npm.cmd install --global appium
appium.cmd driver install uiautomator2
appium.cmd driver doctor uiautomator2
```

## 2. Prepare the phone

1. Install Feishu, sign in to the account to be operated, and confirm that you can
   manually open `Workplace → Attendance`.
2. In About phone, tap the build/version number repeatedly to enable Developer options
   and USB debugging.
3. Connect the phone by USB and approve this computer's debugging authorization.
4. Run the command below and record the serial number whose status is `device`:

```powershell
adb devices -l
```

## 3. Install the project

```powershell
git clone <repository-url> attendance-automation-hub
cd attendance-automation-hub
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\hub.ps1 diagnose
```

Install the development dependencies only when you need to run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## 4. Configure and test safely

The following command prompts twice for a Web password of at least 12 characters. The
initial setup enables device access only and does not allow the script to actively click
real attendance buttons:

```powershell
.\scripts\setup\configure_remote.ps1 -DeviceUdid "YOUR_ADB_SERIAL" -EnableDevice
.\hub.ps1 diagnose
.\scripts\runtime\run_morning.ps1 -TestMode -Immediate
.\scripts\runtime\run_clock_out.ps1 -TestMode
```

After confirming that detection works correctly, configure it again and explicitly enable
real actions:

```powershell
.\scripts\setup\configure_remote.ps1 `
    -DeviceUdid "YOUR_ADB_SERIAL" `
    -EnableDevice -EnableRealActions -ConfirmProduction CUTOVER
```

Install the daily task and the Web service, which is hosted invisibly by `wscript.exe`
after sign-in:

```powershell
.\scripts\setup\install_daily_task.ps1
.\scripts\setup\install_remote_service.ps1
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

The daily task starts at 09:00 and randomly selects an actual time from the remaining
window through 09:30. Weekends and statutory holidays are skipped by default, while
official adjusted working days run normally. If the computer resumes after 09:00 but
before 09:30, the task still calculates a time within the remaining window; it does not
make up the clock-in after 09:30. The scheduled task requires the user to remain signed
in. Locking the screen is fine; signing out is not.

After a computer restart, the Tailscale service starts with Windows, the Web service
starts when this Windows user signs in, and the daily task remains scheduled. The daily
task does not wake a powered-off or sleeping computer. For the first verification, keep
the computer powered on and signed in before 09:00.

## 5. Commands

```powershell
# Desktop panel; you can also double-click Attendance Hub.vbs
.\hub.ps1 desktop

# Immediate real clock-in / clock-out
.\hub.ps1 clock-in
.\hub.ps1 clock-out

# Phone display and unrestricted control
.\scripts\operations\start_scrcpy.ps1

# Environment diagnostics
.\hub.ps1 diagnose

# Enable / pause the daily task
Enable-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
Disable-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
```

Closing the desktop panel does not affect scheduled tasks. If the remote Web page stops
responding, restart its background service:

```powershell
Stop-ScheduledTask -TaskName "Attendance Automation Hub Web" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

Local address: `http://127.0.0.1:8765`.

## 6. Private remote access with Tailscale (optional)

1. Install Tailscale on the attendance computer and the remote computer/Android phone,
   then sign in to the same tailnet.
2. Reconfigure the allowed Tailscale login email:

```powershell
.\scripts\setup\configure_remote.ps1 `
    -DeviceUdid "YOUR_ADB_SERIAL" `
    -TailscaleUser "your-email@example.com" `
    -EnableDevice -EnableRealActions -ConfirmProduction CUTOVER
```

3. In an administrator PowerShell window, create an HTTPS endpoint accessible only from
   the tailnet:

```powershell
.\scripts\setup\setup_tailscale_serve.ps1
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

The script displays a URL such as `https://<computer>.<tailnet>.ts.net`. Do not enable
Funnel or expose ports `8765`, `4723`, or any ADB port to the public internet. Clash users
should route `*.ts.net` directly.

Disable remote access with:

```powershell
.\scripts\setup\setup_tailscale_serve.ps1 -Disable
.\scripts\setup\uninstall_remote_service.ps1
```

## 7. Data and troubleshooting

- Daily logs: `logs\YYYY-MM\YYYY-MM-DD.log`
- Failure screenshots/XML: `artifacts\`
- Attendance history and remote job database: `data\`
- Local account, password hash, and device configuration: `config\app_config.json`,
  `config\remote_config.json`
- The calendar page can synchronize China's annual statutory holiday schedule and set
  run/skip status or exact clock-in and clock-out times for an individual date.

Common checks:

```powershell
adb devices -l
appium.cmd driver doctor uiautomator2
.\hub.ps1 diagnose
Get-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
Get-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

Real configuration, logs, databases, screenshots, `.venv`, and release packages are
excluded from Git. Build a release package with `.\hub.ps1 build`.
