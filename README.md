# Attendance Automation Hub

Windows 上的独立 Android 考勤自动化工具，包含每日随机上班打卡、立即上/下班、
下班更新确认、节假日与单日计划、历史记录、桌面面板、scrcpy 手机控制和 Tailscale
私网 Web 控制。默认上班窗口为 `09:00～09:30`。

> 自动化会操作真实考勤账号。请先运行安全测试，并确认符合所在组织的制度。
> 测试模式不会主动点击，但飞书“极速打卡”仍可能在应用打开时自行打卡。

## 1. 环境要求

- Windows 10/11，使用当前登录的桌面用户运行。
- Android 8 或更高版本的真机，USB 连接电脑。
- [Python 3.10+](https://www.python.org/downloads/windows/)
- [Microsoft OpenJDK 17](https://learn.microsoft.com/java/openjdk/download)
- [Node.js LTS](https://nodejs.org/en/download)
- [Android Studio / Android SDK](https://developer.android.com/studio)
- [Appium 与 UiAutomator2](https://appium.io/docs/en/latest/ecosystem/drivers/)
- [scrcpy](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)（手机画面控制）
- [Tailscale](https://tailscale.com/docs/install/windows)（仅远程访问需要）

可在 PowerShell 中用 WinGet 安装主要组件：

```powershell
winget install --exact --id Python.Python.3.10
winget install --exact --id Microsoft.OpenJDK.17
winget install --exact --id OpenJS.NodeJS.LTS
winget install --exact --id Google.AndroidStudio
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Tailscale.Tailscale
```

### Android SDK

打开 Android Studio → `More Actions` → `SDK Manager` → `SDK Tools`，安装：

- Android SDK Platform-Tools
- Android SDK Build-Tools
- Android SDK Command-line Tools (latest)

通常 SDK 位于 `%LOCALAPPDATA%\Android\Sdk`。设置用户环境变量：

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

安装 JDK 时应同时设置 `JAVA_HOME`；若诊断提示缺失，将它设为实际 JDK 17 目录，
并把 `%JAVA_HOME%\bin` 加入用户 `Path`。完成环境变量设置后重新打开 PowerShell。

安装 Appium 3 和 Android 驱动：

```powershell
npm.cmd install --global appium
appium.cmd driver install uiautomator2
appium.cmd driver doctor uiautomator2
```

## 2. 准备手机

1. 安装飞书并登录需要操作的账号，确认可以手动进入“工作台 → 假勤”。
2. 在手机“关于手机”中连续点击版本号，开启开发者选项与 USB 调试。
3. USB 连接电脑，在手机上允许这台电脑的调试授权。
4. 运行下列命令，记录状态为 `device` 的序列号：

```powershell
adb devices -l
```

## 3. 安装项目

```powershell
git clone <repository-url> attendance-automation-hub
cd attendance-automation-hub
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\hub.ps1 diagnose
```

开发者需要运行测试时再安装开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## 4. 配置与安全测试

下面的配置命令会要求输入两次不少于 12 位的 Web 密码。首次只开启设备访问，
不会允许脚本主动点击真实打卡：

```powershell
.\scripts\setup\configure_remote.ps1 -DeviceUdid "你的ADB序列号" -EnableDevice
.\hub.ps1 diagnose
.\scripts\runtime\run_morning.ps1 -TestMode -Immediate
.\scripts\runtime\run_clock_out.ps1 -TestMode
```

确认识别结果正确后，重新配置并显式开启真实操作：

```powershell
.\scripts\setup\configure_remote.ps1 `
    -DeviceUdid "你的ADB序列号" `
    -EnableDevice -EnableRealActions -ConfirmProduction CUTOVER
```

安装每日任务和登录后自启的 Web 服务：

```powershell
.\scripts\setup\install_daily_task.ps1
.\scripts\setup\install_remote_service.ps1
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

每日任务在 09:00 启动，并从当时到 09:30 的剩余窗口中随机选择实际时间；周末、
法定节假日默认跳过，调休工作日执行。电脑错过 09:00 后在 09:30 前恢复时仍会计算
剩余窗口；09:30 后不会补打。计划任务要求用户保持登录，锁屏不影响，退出登录会影响。

## 5. 使用命令

```powershell
# 桌面面板；也可以双击 Attendance Hub.vbs
.\hub.ps1 desktop

# 立即真实上班 / 下班
.\hub.ps1 clock-in
.\hub.ps1 clock-out

# 手机画面与自由操作
.\scripts\operations\start_scrcpy.ps1

# 环境诊断
.\hub.ps1 diagnose

# 启用 / 暂停每日任务
Enable-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
Disable-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
```

关闭桌面面板不影响计划任务。远程网页中断时重启后台服务：

```powershell
Stop-ScheduledTask -TaskName "Attendance Automation Hub Web" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

本机访问地址：`http://127.0.0.1:8765`。

## 6. Tailscale 私网远程访问（可选）

1. 在打卡电脑和远程电脑/安卓手机安装 Tailscale，并登录同一 tailnet。
2. 重新配置允许访问的 Tailscale 登录邮箱：

```powershell
.\scripts\setup\configure_remote.ps1 `
    -DeviceUdid "你的ADB序列号" `
    -TailscaleUser "your-email@example.com" `
    -EnableDevice -EnableRealActions -ConfirmProduction CUTOVER
```

3. 在管理员 PowerShell 中配置仅 tailnet 可访问的 HTTPS 入口：

```powershell
.\scripts\setup\setup_tailscale_serve.ps1
Start-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

脚本会显示 `https://<computer>.<tailnet>.ts.net`。不要启用 Funnel，也不要把
`8765`、`4723` 或 ADB 端口映射到公网。Clash 用户应让 `*.ts.net` 走直连规则。

停用远程入口：

```powershell
.\scripts\setup\setup_tailscale_serve.ps1 -Disable
.\scripts\setup\uninstall_remote_service.ps1
```

## 7. 数据与故障排查

- 每日日志：`logs\YYYY-MM\YYYY-MM-DD.log`
- 失败截图/XML：`artifacts\`
- 历史与远程任务数据库：`data\`
- 本机账号、密码哈希和设备配置：`config\app_config.json`、`config\remote_config.json`
- 日历页面可同步国务院年度放假安排，并为单日设置执行/跳过或精确上下班时间。

常见检查：

```powershell
adb devices -l
appium.cmd driver doctor uiautomator2
.\hub.ps1 diagnose
Get-ScheduledTask -TaskName "Attendance Hub Morning Clock-In"
Get-ScheduledTask -TaskName "Attendance Automation Hub Web"
```

真实配置、日志、数据库、截图、`.venv` 和发布包均被 Git 排除。发布包可用
`.\hub.ps1 build` 生成。
