# 迁移简明指南

## 1. 安装组件

Windows 10/11 管理员 PowerShell：

```powershell
winget install --exact --id Python.Python.3.10
winget install --exact --id Microsoft.OpenJDK.17
winget install --exact --id OpenJS.NodeJS.LTS
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Tailscale.Tailscale
npm.cmd install --global appium
appium.cmd driver install uiautomator2
appium.cmd driver doctor uiautomator2
```

`scrcpy` 自带 ADB；若 `adb` 不在 Path，另装 Android SDK Platform-Tools，并将其目录加入 Path。验证：

```powershell
python --version
java -version
node --version
adb version
scrcpy --version
appium.cmd --version
```

## 2. 安装项目

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\diagnose_environment.ps1
```

手机开启“开发者选项 → USB 调试”，连接后运行 `adb devices -l`，记录状态为 `device` 的序列号。新手机先登录目标飞书账号并确认能进入“工作台 → 假勤”。

## 3. 安全迁移与测试

```powershell
.\migrate_from_legacy.ps1 -LegacyPath E:\PhoneRemote\feishu_dryrun -IncludeLogs
.\configure_remote.ps1 -DeviceUdid 序列号 -TailscaleUser 你的邮箱 -EnableDevice
.\run_remote_service.ps1
```

打开 `http://127.0.0.1:8765` 检查页面。首次识别测试前建议临时关闭飞书极速打卡；即使脚本不点击，打开飞书仍可能触发极速打卡。

## 4. 私网访问

在电脑和远程安卓/电脑安装 Tailscale，登录同一 tailnet。在打卡电脑的管理员 PowerShell 运行：

```powershell
.\setup_tailscale_serve.ps1
.\install_remote_service.ps1
```

通过脚本显示的 `https://电脑名.网络名.ts.net` 访问。不要使用 Funnel，也不要把 8765、4723 或 ADB 端口映射到公网。

## 5. 最终切换

确认新仓库全部状态正常后才运行：

```powershell
.\configure_remote.ps1 -DeviceUdid 序列号 -TailscaleUser 你的邮箱 -EnableDevice -EnableRealActions -ConfirmProduction CUTOVER
.\cutover_to_hub.ps1 -ConfirmCutover CUTOVER
```

脚本会先安装并验证新任务，随后只停用旧每日任务，不删除旧仓库。需要恢复时：

```powershell
.\rollback_to_legacy.ps1 -ConfirmRollback ROLLBACK
```

发布压缩包应包含仓库源码、`config\*.example.json`、诊断/安装/迁移/回滚脚本和文档；不包含 `.venv`、真实配置、日志、截图、数据库或账号资料。
