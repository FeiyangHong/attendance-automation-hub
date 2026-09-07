# 飞书打卡工具迁移（简明版）

适用于 Windows 10/11、Android 手机和另一个飞书账号。解压目录可自由选择；脚本会自动使用当前目录，无需修改路径。

## 1. 安装组件

在 PowerShell 依次执行；每组安装完成后重新打开 PowerShell。

### Python 3.11

```powershell
winget install --exact --id Python.Python.3.11
python --version
```

### ADB、Android SDK 与 scrcpy

```powershell
winget install --exact --id Google.AndroidStudio
winget install --exact --id Genymobile.scrcpy
```

打开 Android Studio 的 **SDK Manager → SDK Tools**，安装 **Android SDK Platform-Tools**；再安装一个与手机兼容的 **Android SDK Platform**。设置用户环境变量：

```text
ANDROID_HOME=C:\Users\你的用户名\AppData\Local\Android\Sdk
Path 增加：%ANDROID_HOME%\platform-tools
```

验证：

```powershell
adb version
adb devices -l
scrcpy --version
```

### JDK 17

```powershell
winget install --exact --id Microsoft.OpenJDK.17
java -version
```

如 `JAVA_HOME` 为空，将它设为 JDK 17 安装目录，并把 `%JAVA_HOME%\bin` 加入 `Path`。

### Node.js、Appium 与 UiAutomator2

```powershell
winget install --exact --id OpenJS.NodeJS.LTS
npm.cmd install --global appium
appium.cmd driver install uiautomator2
appium.cmd driver doctor uiautomator2
```

官方参考：[Platform-Tools](https://developer.android.com/tools/releases/platform-tools)、[scrcpy](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)、[Microsoft OpenJDK](https://learn.microsoft.com/java/openjdk/install)、[Node.js](https://nodejs.org/en/download)、[Appium](https://appium.io/docs/en/latest/quickstart/install/)、[UiAutomator2](https://appium.io/docs/en/latest/quickstart/uiauto2-driver/)。

## 2. 创建 Python 环境

在解压后的项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

不要从旧电脑复制 `.venv`。

## 3. 连接并适配新手机

1. 新手机登录目标飞书账号，确认可进入“工作台 → 假勤”。
2. 开启开发者选项和 USB 调试；连接电脑后永久允许调试授权。
3. 运行 `adb devices -l`，状态必须是 `device`，记下设备序列号。
4. 将以下 4 个文件中的 `UDID = "..."` 改为新序列号：

   ```text
   04_feishu_flow.py
   feishu_control_panel.pyw
   01_connect.py
   02_probe_home.py
   ```

5. 国内版飞书包名通常是 `com.ss.android.lark`；如果实际包名不同，同时修改这些 Python 文件中的 `FEISHU_PACKAGE`。
6. 运行 `scrcpy`，确认电脑可显示并操作手机。

## 4. 诊断与首次测试

一键检查全部组件、手机授权、UiAutomator2、Python 环境和项目文件：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\diagnose_environment.ps1
```

诊断无失败后启动 Appium：

```powershell
appium.cmd
```

另开 PowerShell 做识别测试：

```powershell
.\.venv\Scripts\python.exe .\04_feishu_flow.py --dry-run
```

`--dry-run` 不会点击打卡按钮；但开启“极速打卡”时，仅打开飞书也可能产生真实打卡。首次测试前请暂时关闭极速打卡，或避开触发时间和地点。

## 5. 启用

双击 `launch_control_panel.vbs`。确认状态正常后，在界面点击“安装 / 更新任务”；日常执行时程序会自动启动 Appium。迁移替换旧电脑时，请停用旧电脑任务，避免重复操作。

压缩包默认不含旧账号的日志、截图、临时计划和历史数据库。若迁移同一账号并想保留历史，可单独复制 `data\attendance_history.db`；迁移其他账号时不要复制。

## 压缩包内容

```text
核心：04_feishu_flow.py、feishu_control_panel.pyw、attendance_history.py
      daily_plans.py、holiday_sync.py
任务：run_random.ps1、run_clock_out.ps1、schedule_clock_out.ps1
      sync_daily_plan.ps1、install_daily_task.ps1、launch_control_panel.vbs
配置：config\calendar_overrides.json、config\daily_plans.json
      config\official_holidays.json
诊断：diagnose_environment.ps1、01_connect.py、02_probe_home.py
安装：requirements.txt、README_MIGRATION_QUICK.md
```
